from __future__ import annotations

import base64
import hashlib
import math
import textwrap
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.creative_production.schemas import (
    BrandVisualProfile,
    ContentBrief,
    CreativePlatform,
    CreativeQualityLevel,
    CreativeScore,
    ImageCandidate,
    ObjectiveImageQA,
    ProviderAvailability,
    VisualBrief,
)


@dataclass(frozen=True)
class ImageProviderCapability:
    provider: str
    status: ProviderAvailability
    model: str
    external: bool
    operations: tuple[str, ...]
    message: str = ""


class ImageGenerationProvider(ABC):
    key: str
    model: str
    external: bool

    @abstractmethod
    def health_check(self) -> ImageProviderCapability:
        """Return local configuration capability."""

    @abstractmethod
    def generate(
        self,
        brief: VisualBrief,
        output_dir: Path,
        *,
        variation: int,
    ) -> ImageCandidate:
        """Generate a visual without typography/layout text."""

    def edit(
        self,
        source: ImageCandidate,
        brief: VisualBrief,
        output_dir: Path,
        instructions: list[str],
    ) -> ImageCandidate:
        return self.generate(brief, output_dir, variation=source.revision_number + 100)

    def inpaint(self, *_: Any, **__: Any) -> ImageCandidate:
        raise NotImplementedError("This provider does not support inpaint.")

    def outpaint(self, *_: Any, **__: Any) -> ImageCandidate:
        raise NotImplementedError("This provider does not support outpaint.")

    def variation(
        self,
        source: ImageCandidate,
        brief: VisualBrief,
        output_dir: Path,
    ) -> ImageCandidate:
        return self.edit(source, brief, output_dir, ["Create a visual variation"])

    def reference_generate(
        self,
        brief: VisualBrief,
        references: list[str],
        output_dir: Path,
    ) -> ImageCandidate:
        raise NotImplementedError("This provider does not support reference generation.")

    @staticmethod
    def estimate_cost(quantity: int) -> float | None:
        return None


def _safe_output(output_dir: Path, filename: str) -> Path:
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / Path(filename).name).resolve()
    if candidate.parent != root:
        raise ValueError("Asset output pathが許可ディレクトリ外です。")
    return candidate


class LocalPatternImageProvider(ImageGenerationProvider):
    """Creates a clearly marked offline visual placeholder without embedded text."""

    key = "local_pattern"
    model = "deterministic-pattern-v1"
    external = False

    def health_check(self) -> ImageProviderCapability:
        return ImageProviderCapability(
            provider=self.key,
            status=ProviderAvailability.AVAILABLE,
            model=self.model,
            external=False,
            operations=("generate", "edit", "variation"),
            message="API不要のレイアウト検証用背景。AI生成画像ではありません。",
        )

    def generate(
        self,
        brief: VisualBrief,
        output_dir: Path,
        *,
        variation: int,
    ) -> ImageCandidate:
        seed_text = f"{brief.visual_brief_id}:{variation}:{brief.concept}"
        seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
        colors = brief.color_direction or ["#17324D", "#F3B61F", "#F7F9FB"]
        background = colors[variation % len(colors)]
        accent = colors[(variation + 1) % len(colors)]
        image = Image.new("RGB", (brief.width, brief.height), background)
        draw = ImageDraw.Draw(image, "RGBA")
        for index in range(7):
            radius = max(30, int(min(brief.width, brief.height) * (0.08 + index * 0.035)))
            x = int((seed * (index + 3) * 17) % max(1, brief.width))
            y = int((seed * (index + 5) * 31) % max(1, brief.height))
            alpha = 28 + index * 8
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=(*ImageColor.hex_to_rgb(accent), min(alpha, 110)),
            )
        asset_id = f"AST-{hashlib.sha256(seed_text.encode()).hexdigest()[:24]}"
        path = _safe_output(output_dir, f"{asset_id}-background.png")
        image.save(path, "PNG", optimize=True)
        return ImageCandidate(
            asset_id=asset_id,
            visual_brief_id=brief.visual_brief_id,
            content_brief_id=brief.content_brief_id,
            provider=self.key,
            model=self.model,
            file_path=str(path),
            mime_type="image/png",
            width=brief.width,
            height=brief.height,
            prompt=brief.concept,
            negative_prompt=", ".join(brief.negative_constraints),
            seed=seed,
            parameters={"variation": variation, "visual_only": True},
            is_placeholder=True,
        )


class ImageColor:
    @staticmethod
    def hex_to_rgb(value: str) -> tuple[int, int, int]:
        clean = value.removeprefix("#")
        if len(clean) != 6:
            return 23, 50, 77
        return tuple(int(clean[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


class OpenAIImageProvider(ImageGenerationProvider):
    key = "openai_image"
    external = True

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 180.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self.model = model.strip()
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def health_check(self) -> ImageProviderCapability:
        configured = bool(self._api_key and self.model)
        return ImageProviderCapability(
            provider=self.key,
            status=(
                ProviderAvailability.AVAILABLE
                if configured
                else ProviderAvailability.NOT_CONFIGURED
            ),
            model=self.model or "—",
            external=True,
            operations=("generate", "edit"),
            message="API key/model設定済み" if configured else "API keyまたはImage modelが未設定",
        )

    def generate(
        self,
        brief: VisualBrief,
        output_dir: Path,
        *,
        variation: int,
    ) -> ImageCandidate:
        if not self._api_key or not self.model:
            raise RuntimeError("OpenAI Image Providerが未設定です。")
        started = time.perf_counter()
        size = self._supported_size(brief.width, brief.height)
        response = self._client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self.model,
                "prompt": (
                    f"{brief.concept}. Mood: {brief.mood}. Lighting: {brief.lighting}. "
                    f"Composition: {brief.composition}. Subject: {brief.subject}. "
                    "Do not render text, logos, watermarks, or UI."
                ),
                "size": size,
                "quality": "high",
                "n": 1,
            },
        )
        if response.status_code in {401, 403}:
            raise RuntimeError("OpenAI Image APIの認証または利用権限を確認してください。")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        if not data or not data[0].get("b64_json"):
            raise RuntimeError("OpenAI Image API応答に画像がありません。")
        binary = base64.b64decode(data[0]["b64_json"], validate=True)
        asset_id = f"AST-{hashlib.sha256(binary).hexdigest()[:24]}"
        path = _safe_output(output_dir, f"{asset_id}-background.png")
        path.write_bytes(binary)
        with Image.open(path) as generated:
            width, height = generated.size
        return ImageCandidate(
            asset_id=asset_id,
            visual_brief_id=brief.visual_brief_id,
            content_brief_id=brief.content_brief_id,
            provider=self.key,
            model=self.model,
            file_path=str(path),
            mime_type="image/png",
            width=width,
            height=height,
            prompt=brief.concept,
            negative_prompt=", ".join(brief.negative_constraints),
            parameters={
                "variation": variation,
                "size": size,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
            is_placeholder=False,
        )

    @staticmethod
    def _supported_size(width: int, height: int) -> str:
        if math.isclose(width / height, 1.0, rel_tol=0.15):
            return "1024x1024"
        return "1536x1024" if width > height else "1024x1536"


class UnavailableImageProvider(ImageGenerationProvider):
    external = True

    def __init__(self, key: str, model: str, message: str) -> None:
        self.key = key
        self.model = model
        self._message = message

    def health_check(self) -> ImageProviderCapability:
        return ImageProviderCapability(
            provider=self.key,
            status=ProviderAvailability.NOT_CONFIGURED,
            model=self.model or "—",
            external=True,
            operations=("generate", "edit", "inpaint", "outpaint", "variation"),
            message=self._message,
        )

    def generate(
        self,
        brief: VisualBrief,
        output_dir: Path,
        *,
        variation: int,
    ) -> ImageCandidate:
        raise RuntimeError(self._message)


class ImageProviderRegistry:
    def __init__(self, providers: Iterable[ImageGenerationProvider]) -> None:
        self._providers = {provider.key: provider for provider in providers}

    @property
    def available(self) -> list[ImageGenerationProvider]:
        return [
            provider
            for provider in self._providers.values()
            if provider.health_check().status is ProviderAvailability.AVAILABLE
        ]

    def get(self, key: str) -> ImageGenerationProvider:
        return self._providers[key]

    def capabilities(self) -> dict[str, ImageProviderCapability]:
        return {key: provider.health_check() for key, provider in self._providers.items()}


PLATFORM_IMAGE_SIZES = {
    CreativePlatform.X: (1600, 900),
    CreativePlatform.INSTAGRAM_FEED: (1080, 1350),
    CreativePlatform.INSTAGRAM_CAROUSEL: (1080, 1350),
    CreativePlatform.INSTAGRAM_REEL: (1080, 1920),
    CreativePlatform.INSTAGRAM_STORY: (1080, 1920),
    CreativePlatform.TIKTOK: (1080, 1920),
    CreativePlatform.YOUTUBE_LONG: (1280, 720),
    CreativePlatform.YOUTUBE_SHORTS: (1080, 1920),
    CreativePlatform.PINTEREST: (1000, 1500),
    CreativePlatform.IMAGE: (1280, 720),
}


class ArtDirector:
    def create_visual_brief(
        self,
        brief: ContentBrief,
        platform: CreativePlatform,
        brand: BrandVisualProfile,
    ) -> VisualBrief:
        width, height = PLATFORM_IMAGE_SIZES.get(platform, (1280, 720))
        return VisualBrief(
            content_brief_id=brief.content_brief_id,
            platform=platform,
            concept=f"{brief.market}: {brief.primary_message}",
            mood="credible, focused, quietly optimistic",
            lighting="soft directional light with readable contrast",
            composition="single focal area with negative space reserved for deterministic text",
            color_direction=brand.colors,
            texture="subtle editorial texture",
            environment="abstract context without third-party brand identifiers",
            subject=brief.target_audience,
            styling=brand.image_style,
            camera="platform-appropriate editorial framing",
            reference_attributes=[brand.photography_style, brand.illustration_style],
            negative_constraints=[*brand.forbidden_visuals, "text", "logo", "watermark"],
            headline=brief.target_problem[:80],
            cta=brief.cta[:60],
            width=width,
            height=height,
        )


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/YuGothB.ttc" if bold else "C:/Windows/Fonts/YuGothR.ttc"),
        Path("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc"),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


class TypographyLayoutEngine:
    def composite(
        self,
        source: ImageCandidate,
        brief: VisualBrief,
        output_dir: Path,
        brand: BrandVisualProfile,
    ) -> ImageCandidate:
        with Image.open(source.file_path).convert("RGB") as background:
            image = background.resize((brief.width, brief.height))
        draw = ImageDraw.Draw(image, "RGBA")
        margin = int(min(brief.width, brief.height) * 0.08)
        box_width = int(brief.width * 0.72)
        box_height = int(brief.height * 0.58)
        draw.rounded_rectangle(
            (margin, margin, margin + box_width, margin + box_height),
            radius=max(16, margin // 3),
            fill=(8, 18, 31, 205),
        )
        headline_size = max(28, int(min(brief.width, brief.height) * 0.068))
        body_size = max(18, int(headline_size * 0.42))
        headline_font = _font(headline_size, bold=True)
        body_font = _font(body_size)
        max_chars = max(8, int(box_width / max(1, headline_size) * 1.65))
        headline_lines = textwrap.wrap(brief.headline, width=max_chars)[:4]
        y = margin + int(margin * 0.75)
        for line in headline_lines:
            draw.text(
                (margin + margin // 2, y), line, font=headline_font, fill=(255, 255, 255, 255)
            )
            y += int(headline_size * 1.35)
        draw.text(
            (margin + margin // 2, margin + box_height - body_size * 2),
            brief.cta,
            font=body_font,
            fill=(*ImageColor.hex_to_rgb(brand.colors[1 % len(brand.colors)]), 255),
        )
        draw.text(
            (margin, brief.height - margin - body_size),
            "DRAFT PLACEHOLDER" if source.is_placeholder else "HUMAN APPROVAL REQUIRED",
            font=body_font,
            fill=(255, 255, 255, 220),
        )
        composite_id = (
            f"AST-{hashlib.sha256(f'{source.asset_id}:composite'.encode()).hexdigest()[:24]}"
        )
        path = _safe_output(output_dir, f"{composite_id}-composite.png")
        image.save(path, "PNG", optimize=True)
        return ImageCandidate(
            asset_id=composite_id,
            visual_brief_id=brief.visual_brief_id,
            content_brief_id=brief.content_brief_id,
            provider="deterministic_layout",
            model="pillow-layout-v1",
            file_path=str(path),
            mime_type="image/png",
            width=brief.width,
            height=brief.height,
            prompt="Deterministic typography and safe-area composition",
            negative_prompt="text overflow, unsafe margin",
            parameters={
                "font_family": brand.typography,
                "headline_size": headline_size,
                "line_height": 1.35,
                "safe_margin": margin,
                "source_provider": source.provider,
            },
            parent_asset_id=source.asset_id,
            revision_number=source.revision_number,
            is_placeholder=source.is_placeholder,
        )


class ImageObjectiveQA:
    def check(self, candidate: ImageCandidate, brief: VisualBrief) -> ObjectiveImageQA:
        issues: list[str] = []
        path = Path(candidate.file_path)
        valid = False
        width = height = 0
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
            valid = True
        except (OSError, UnidentifiedImageError):
            issues.append("画像ファイルを検証できません。")
        resolution_ok = width == brief.width and height == brief.height
        if not resolution_ok:
            issues.append("Required dimensionsと一致しません。")
        aspect_ok = valid and math.isclose(
            width / max(1, height), brief.width / brief.height, rel_tol=0.01
        )
        if not aspect_ok:
            issues.append("Aspect ratioが一致しません。")
        file_size_ok = path.is_file() and 0 < path.stat().st_size <= 20 * 1024 * 1024
        if not file_size_ok:
            issues.append("File sizeが0または20MB超です。")
        safe_area = (
            bool(candidate.parameters.get("safe_margin"))
            or candidate.provider != "deterministic_layout"
        )
        text_overflow = candidate.provider == "deterministic_layout" and len(brief.headline) > 160
        if text_overflow:
            issues.append("HeadlineがTypography safe boxを超える可能性があります。")
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        passed = (
            valid
            and resolution_ok
            and aspect_ok
            and file_size_ok
            and safe_area
            and not text_overflow
        )
        return ObjectiveImageQA(
            asset_id=candidate.asset_id,
            valid_file=valid,
            resolution_ok=resolution_ok,
            aspect_ratio_ok=aspect_ok,
            file_size_ok=file_size_ok,
            safe_area_ok=safe_area,
            text_overflow=text_overflow,
            duplicate_hash=digest,
            issues=issues,
            passed=passed,
        )


class VisualJury:
    def score(
        self,
        candidate: ImageCandidate,
        qa: ObjectiveImageQA,
        brief: VisualBrief,
    ) -> list[CreativeScore]:
        objective = {
            "resolution": 100.0 if qa.resolution_ok else 0.0,
            "aspect_ratio": 100.0 if qa.aspect_ratio_ok else 0.0,
            "file_integrity": 100.0 if qa.valid_file else 0.0,
            "safe_area": 100.0 if qa.safe_area_ok and not qa.text_overflow else 30.0,
        }
        platform = {
            "composition": 84.0,
            "message_clarity": 88.0 if candidate.provider == "deterministic_layout" else 70.0,
            "platform_fit": 90.0 if qa.aspect_ratio_ok else 35.0,
            "visual_hierarchy": 87.0 if candidate.provider == "deterministic_layout" else 65.0,
        }
        brand = {
            "brand_fit": 86.0,
            "originality": 70.0 if candidate.is_placeholder else 88.0,
            "professional_quality": 62.0 if candidate.is_placeholder else 88.0,
            "copyright_safety": 95.0,
        }
        rubrics = [
            ("Objective Image QA", objective),
            ("Platform Visual Judge", platform),
            ("Brand Visual Judge", brand),
        ]
        return [
            CreativeScore(
                candidate_id=candidate.asset_id,
                judge_name=name,
                rubric=rubric,
                overall=round(sum(rubric.values()) / len(rubric), 2),
                blocking_issues=qa.issues if name == "Objective Image QA" and not qa.passed else [],
            )
            for name, rubric in rubrics
        ]


@dataclass(frozen=True)
class ImageProductionResult:
    visual_brief: VisualBrief
    candidates: list[ImageCandidate]
    final: ImageCandidate
    qa: list[ObjectiveImageQA]
    scores: list[CreativeScore]
    degraded: bool
    degradation_reasons: tuple[str, ...]


class ImageArena:
    def __init__(self, registry: ImageProviderRegistry) -> None:
        self._registry = registry

    def run(
        self,
        content_brief: ContentBrief,
        platform: CreativePlatform,
        brand: BrandVisualProfile,
        output_dir: Path,
        *,
        candidate_count: int,
    ) -> ImageProductionResult:
        visual_brief = ArtDirector().create_visual_brief(content_brief, platform, brand)
        providers = self._registry.available
        if not providers:
            raise RuntimeError("利用可能なImage Providerがありません。")
        backgrounds: list[ImageCandidate] = []
        errors: list[str] = []
        for index in range(candidate_count):
            provider = providers[index % len(providers)]
            try:
                backgrounds.append(provider.generate(visual_brief, output_dir, variation=index))
            except Exception as exc:
                errors.append(f"{provider.key}: {type(exc).__name__}")
        if not backgrounds:
            fallback = self._registry.get("local_pattern")
            backgrounds.append(fallback.generate(visual_brief, output_dir, variation=0))
            errors.append("外部Image Provider失敗のためlocal_patternへfallbackしました。")
        composites = [
            TypographyLayoutEngine().composite(background, visual_brief, output_dir, brand)
            for background in backgrounds
        ]
        qa_results = [ImageObjectiveQA().check(candidate, visual_brief) for candidate in composites]
        scores = [
            score
            for candidate, qa in zip(composites, qa_results, strict=True)
            for score in VisualJury().score(candidate, qa, visual_brief)
        ]
        by_asset: dict[str, list[float]] = {}
        for score in scores:
            by_asset.setdefault(score.candidate_id, []).append(score.overall)
        final = max(
            composites,
            key=lambda candidate: (
                sum(by_asset[candidate.asset_id]) / len(by_asset[candidate.asset_id])
            ),
        )
        external_provider_count = len(
            {background.provider for background in backgrounds if not background.is_placeholder}
        )
        needs_multiple = content_brief.quality_level in {
            CreativeQualityLevel.PREMIUM,
            CreativeQualityLevel.FLAGSHIP,
        }
        degraded = bool(errors) or (needs_multiple and external_provider_count < 2)
        if needs_multiple and external_provider_count < 2:
            errors.append("Premium/Flagshipの複数Image Provider要件を満たしていません。")
        if final.is_placeholder:
            errors.append("最終画像はDRAFT PLACEHOLDERでありHuman Approval前の確認用です。")
            degraded = True
        return ImageProductionResult(
            visual_brief=visual_brief,
            candidates=[*backgrounds, *composites],
            final=final,
            qa=qa_results,
            scores=scores,
            degraded=degraded,
            degradation_reasons=tuple(dict.fromkeys(errors)),
        )

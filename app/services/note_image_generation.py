from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

OPENAI_IMAGE_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
GPT_IMAGE_MODEL = "gpt-image-2"
API_IMAGE_SIZE = "1280x672"
NOTE_IMAGE_SIZE = (1280, 670)

NOTE_EYECATCH_PROMPT = """Note記事のアイキャッチ画像を作成してください。
テーマは「{article_theme}」です。

目的は、記事を読んでもらうための自然で落ち着いたアイキャッチを作ることです。
広告感は抑え、読み物としての信頼感とやわらかさを重視してください。

デザイン要件：
- 清潔感がある
- 落ち着いた雰囲気
- 情報を詰め込みすぎない
- 余白をしっかり取る
- 写真風または上品なビジュアル表現
- 過度に派手にしない
- 文字を入れる場合は短く、最小限にする

構図：
- 主題が中央またはやや片寄せ
- 文字を載せる余白を確保
- 見やすくシンプルにする

入れるモチーフ：
{motifs}

避けること：
- チラシのような強すぎる訴求
- 文字の詰め込み
- ごちゃごちゃした構図
- 安っぽい広告デザイン

出力は、Noteのアイキャッチとして自然に見える1枚にしてください。

制作上の指定：
- 横長の1枚として構成し、重要な主題や文字は中央寄りの安全な範囲に収める
- ロゴ、透かし、URL、価格、購入を促す文言は入れない
"""


class NoteImageGenerationError(RuntimeError):
    """Safe, user-facing failure raised while generating a note image."""


@dataclass(frozen=True)
class GeneratedNoteImage:
    image_bytes: bytes
    prompt: str
    model: str = GPT_IMAGE_MODEL
    width: int = NOTE_IMAGE_SIZE[0]
    height: int = NOTE_IMAGE_SIZE[1]


def build_note_image_prompt(article_theme: str, motifs: str = "") -> str:
    theme = article_theme.strip()
    if not theme:
        raise ValueError("記事テーマを入力してください。")
    motif_text = motifs.strip() or (
        "特になし。テーマから自然に連想できる控えめな要素を選んでください。"
    )
    return NOTE_EYECATCH_PROMPT.format(article_theme=theme, motifs=motif_text)


class OpenAINoteImageGenerator:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 150.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise NoteImageGenerationError(
                "OpenAI APIキーが未設定です。設定画面の手順に沿って登録してください。"
            )
        self._api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def generate(self, article_theme: str, motifs: str = "") -> GeneratedNoteImage:
        return self.generate_variations(article_theme, motifs, count=1)[0]

    def generate_variations(
        self,
        article_theme: str,
        motifs: str = "",
        *,
        count: int = 3,
    ) -> list[GeneratedNoteImage]:
        if count not in {1, 3}:
            raise ValueError("画像の案数は1枚または3枚を選んでください。")
        prompt = build_note_image_prompt(article_theme, motifs)
        try:
            response = self._client.post(
                OPENAI_IMAGE_GENERATIONS_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GPT_IMAGE_MODEL,
                    "prompt": prompt,
                    "size": API_IMAGE_SIZE,
                    "quality": "medium",
                    "n": count,
                },
            )
        except httpx.TimeoutException as exc:
            raise NoteImageGenerationError(
                f"GPT Image 2が{self.timeout_seconds:g}秒以内に応答しませんでした。"
                "少し待ってから、もう一度生成してください。"
            ) from exc
        except httpx.RequestError as exc:
            raise NoteImageGenerationError(
                "OpenAI APIへ接続できませんでした。通信環境を確認して再実行してください。"
            ) from exc

        if response.status_code != 200:
            self._raise_api_error(response)

        try:
            payload = response.json()
            image_items = payload["data"]
            if not isinstance(image_items, list) or len(image_items) != count:
                raise ValueError("unexpected image count")
            generated_images: list[GeneratedNoteImage] = []
            for item in image_items:
                encoded_image = item["b64_json"]
                if not isinstance(encoded_image, str) or not encoded_image:
                    raise ValueError("empty image")
                raw_image = base64.b64decode(encoded_image, validate=True)
                generated_images.append(
                    GeneratedNoteImage(
                        image_bytes=_prepare_note_image(raw_image),
                        prompt=prompt,
                    )
                )
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
            raise NoteImageGenerationError(
                "GPT Image 2の応答から画像を読み取れませんでした。もう一度生成してください。"
            ) from exc

        return generated_images

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        error_code = ""
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict):
                error_code = str(error.get("code", ""))
        except (ValueError, AttributeError):
            pass

        if error_code == "moderation_blocked":
            raise NoteImageGenerationError(
                "安全基準により画像を生成できませんでした。記事テーマやモチーフの表現を変えてください。"
            )
        messages = {
            400: (
                "画像生成の入力を受け付けられませんでした。"
                "記事テーマやモチーフを見直してください。"
            ),
            401: "OpenAI APIキーが無効です。設定画面の登録内容を確認してください。",
            403: (
                "このOpenAI APIキーではGPT Image 2を利用できません。"
                "API組織の認証と権限を確認してください。"
            ),
            404: "GPT Image 2を利用できません。OpenAI側のモデル利用設定を確認してください。",
            429: (
                "OpenAI APIの利用上限に達しました。"
                "残高・利用上限を確認し、少し待って再実行してください。"
            ),
        }
        if response.status_code in messages:
            raise NoteImageGenerationError(messages[response.status_code])
        if response.status_code >= 500:
            raise NoteImageGenerationError(
                "OpenAI APIで一時的な問題が発生しています。少し待って再実行してください。"
            )
        raise NoteImageGenerationError(
            f"OpenAI APIでエラーが発生しました（HTTP {response.status_code}）。"
        )


def _prepare_note_image(raw_image: bytes) -> bytes:
    try:
        with Image.open(BytesIO(raw_image)) as source:
            source.load()
            converted = source.convert("RGB")
            prepared = ImageOps.fit(
                converted,
                NOTE_IMAGE_SIZE,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = BytesIO()
            prepared.save(output, format="PNG", optimize=True)
    except (OSError, UnidentifiedImageError) as exc:
        raise NoteImageGenerationError(
            "生成画像をnote用のサイズへ整えられませんでした。もう一度生成してください。"
        ) from exc
    return output.getvalue()

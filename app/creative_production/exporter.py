from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.creative_production.schemas import ContentPackage


def package_as_json(package: ContentPackage) -> str:
    return package.model_dump_json(indent=2)


def package_as_markdown(package: ContentPackage) -> str:
    lines = [
        f"# {package.campaign.name}",
        "",
        f"- Package: {package.package_id}",
        f"- Status: {package.status.value}",
        f"- Quality: {package.content_brief.quality_level.value}",
        f"- Research Run: {package.content_brief.research_run_id}",
        f"- Degraded Quality Mode: {package.degraded_quality_mode}",
        "",
    ]
    if package.degradation_reasons:
        lines.extend(
            [
                "## Degradation reasons",
                "",
                *[f"- {reason}" for reason in package.degradation_reasons],
                "",
            ]
        )
    for platform, candidate in package.final_content.items():
        lines.extend(
            [
                f"## {platform.value}",
                "",
                candidate.content,
                "",
                f"Evidence: {', '.join(candidate.evidence_ids) or 'none'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Assets",
            "",
            *[
                f"- {asset.asset_id}: {asset.kind.value} / {asset.provider} / "
                f"placeholder={asset.is_placeholder}"
                for asset in package.assets
            ],
            "",
            "## Evidence",
            "",
            *[f"- {evidence_id}" for evidence_id in package.evidence_ids],
            "",
            "> Human approval is required before publishing.",
        ]
    )
    return "\n".join(lines)


def package_as_zip(package: ContentPackage) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.json", package_as_json(package))
        archive.writestr("content.md", package_as_markdown(package))
        for asset in package.assets:
            path = Path(asset.file_path)
            if not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
                continue
            archive.writestr(f"assets/{path.name}", path.read_bytes())
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "package_id": package.package_id,
                    "assets": [asset.model_dump(mode="json") for asset in package.assets],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return buffer.getvalue()

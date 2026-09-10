from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class MediaProbeResult:
    valid: bool
    kind: str
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    probe: str = "filesystem"


def probe_media(path: Path, mime_type: str) -> MediaProbeResult:
    if not path.is_file():
        return MediaProbeResult(False, "missing", errors=("Fileがありません。",))
    size = path.stat().st_size
    if size <= 0:
        return MediaProbeResult(False, "empty", errors=("Fileが空です。",))
    if mime_type.startswith("image/"):
        return _probe_image(path, mime_type, size)
    if mime_type.startswith("video/"):
        return _probe_video(path, size)
    return MediaProbeResult(
        True,
        "other",
        metadata={"file_size_bytes": size},
        warnings=("画像・動画以外のため技術Probe対象外です。",),
    )


def _probe_image(path: Path, mime_type: str, size: int) -> MediaProbeResult:
    expected_formats = {
        "image/jpeg": {"JPEG"},
        "image/png": {"PNG"},
        "image/webp": {"WEBP"},
        "image/gif": {"GIF"},
    }
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            detected_format = str(image.format or "").upper()
            mode = image.mode
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return MediaProbeResult(
            False,
            "image",
            metadata={"file_size_bytes": size},
            errors=(f"画像をDecodeできません: {type(exc).__name__}",),
            probe="pillow",
        )
    errors: list[str] = []
    allowed = expected_formats.get(mime_type)
    if allowed and detected_format not in allowed:
        errors.append(
            f"宣言MIME {mime_type}と検出Format {detected_format or 'unknown'}が不一致です。"
        )
    if width <= 0 or height <= 0:
        errors.append("画像解像度が不正です。")
    return MediaProbeResult(
        not errors,
        "image",
        metadata={
            "file_size_bytes": size,
            "width": width,
            "height": height,
            "aspect_ratio": round(width / height, 6) if height else None,
            "format": detected_format,
            "mode": mode,
        },
        errors=tuple(errors),
        probe="pillow",
    )


def _probe_video(path: Path, size: int) -> MediaProbeResult:
    executable = shutil.which("ffprobe")
    if not executable:
        return MediaProbeResult(
            True,
            "video",
            metadata={"file_size_bytes": size},
            warnings=("ffprobe未導入のためCodec・尺・解像度を実測できません。",),
            probe="filesystem",
        )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable and argument list
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration,size:stream=codec_name,codec_type,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return MediaProbeResult(
            False,
            "video",
            metadata={"file_size_bytes": size},
            errors=(f"ffprobe実行失敗: {type(exc).__name__}",),
            probe="ffprobe",
        )
    if completed.returncode != 0:
        return MediaProbeResult(
            False,
            "video",
            metadata={"file_size_bytes": size},
            errors=("VideoをDecodeできません。",),
            probe="ffprobe",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return MediaProbeResult(
            False,
            "video",
            metadata={"file_size_bytes": size},
            errors=("ffprobe結果がJSONではありません。",),
            probe="ffprobe",
        )
    streams = payload.get("streams", [])
    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video_stream, dict):
        return MediaProbeResult(
            False,
            "video",
            metadata={"file_size_bytes": size},
            errors=("Video streamがありません。",),
            probe="ffprobe",
        )
    format_data = payload.get("format", {})
    duration_value = video_stream.get("duration") or (
        format_data.get("duration") if isinstance(format_data, dict) else None
    )
    try:
        duration = float(duration_value) if duration_value is not None else None
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
    except (TypeError, ValueError):
        duration, width, height = None, 0, 0
    errors = []
    if width <= 0 or height <= 0:
        errors.append("Video解像度を確認できません。")
    if duration is None or duration <= 0:
        errors.append("Video durationを確認できません。")
    codec = str(video_stream.get("codec_name", ""))
    if not codec:
        errors.append("Video codecを確認できません。")
    return MediaProbeResult(
        not errors,
        "video",
        metadata={
            "file_size_bytes": size,
            "width": width,
            "height": height,
            "aspect_ratio": round(width / height, 6) if height else None,
            "duration_seconds": duration,
            "codec": codec,
        },
        errors=tuple(errors),
        probe="ffprobe",
    )

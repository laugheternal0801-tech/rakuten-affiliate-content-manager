from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.publishing import media_probe


def test_image_probe_reads_real_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "image.jpg"
    Image.new("RGB", (1200, 628), color=(20, 30, 40)).save(path, "JPEG")

    result = media_probe.probe_media(path, "image/jpeg")

    assert result.valid is True
    assert result.probe == "pillow"
    assert result.metadata["width"] == 1200
    assert result.metadata["height"] == 628


def test_corrupt_image_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not-an-image")

    result = media_probe.probe_media(path, "image/jpeg")

    assert result.valid is False
    assert "Decode" in result.errors[0]


def test_video_without_ffprobe_requires_later_manual_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"test-video")
    monkeypatch.setattr(media_probe.shutil, "which", lambda _: None)

    result = media_probe.probe_media(path, "video/mp4")

    assert result.valid is True
    assert result.probe == "filesystem"
    assert "ffprobe未導入" in result.warnings[0]

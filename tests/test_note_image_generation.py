from __future__ import annotations

import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.services.note_image_generation import (
    API_IMAGE_SIZE,
    GPT_IMAGE_MODEL,
    NOTE_IMAGE_SIZE,
    OPENAI_IMAGE_GENERATIONS_URL,
    NoteImageGenerationError,
    OpenAINoteImageGenerator,
    build_note_image_prompt,
)


def make_api_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1280, 672), color=(232, 225, 211)).save(output, format="PNG")
    return output.getvalue()


def test_note_prompt_inserts_theme_and_optional_motifs() -> None:
    prompt = build_note_image_prompt(
        "コーヒーメーカー5機種の比較",
        "白いカップ、木製テーブル、朝の自然光",
    )

    assert "テーマは「コーヒーメーカー5機種の比較」です。" in prompt
    assert "白いカップ、木製テーブル、朝の自然光" in prompt
    assert "広告感は抑え" in prompt
    assert "文字の詰め込み" in prompt
    assert "ロゴ、透かし、URL、価格" in prompt


def test_note_prompt_supplies_a_safe_default_when_motifs_are_blank() -> None:
    prompt = build_note_image_prompt("手軽に楽しむドリップコーヒー", "  ")

    assert "テーマから自然に連想できる控えめな要素" in prompt


def test_gpt_image_2_request_and_note_sized_png_output() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(make_api_image()).decode("ascii")}]},
        )

    api_key = "-".join(["test", "openai", "key"])
    generator = OpenAINoteImageGenerator(
        api_key,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    generated = generator.generate("落ち着いたコーヒー時間", "白いマグカップ")

    assert captured["url"] == OPENAI_IMAGE_GENERATIONS_URL
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == f"Bearer {api_key}"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload == {
        "model": GPT_IMAGE_MODEL,
        "prompt": generated.prompt,
        "size": API_IMAGE_SIZE,
        "quality": "medium",
        "n": 1,
    }
    with Image.open(BytesIO(generated.image_bytes)) as image:
        assert image.format == "PNG"
        assert image.size == NOTE_IMAGE_SIZE


def test_gpt_image_2_can_generate_three_candidates_in_one_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        encoded = base64.b64encode(make_api_image()).decode("ascii")
        return httpx.Response(200, json={"data": [{"b64_json": encoded}] * 3})

    generator = OpenAINoteImageGenerator(
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    generated = generator.generate_variations("コーヒー比較", count=3)

    assert len(generated) == 3
    assert isinstance(captured["payload"], dict)
    assert captured["payload"]["n"] == 3


def test_image_candidate_count_is_limited_to_one_or_three() -> None:
    generator = OpenAINoteImageGenerator("test-key")

    with pytest.raises(ValueError, match="1枚または3枚"):
        generator.generate_variations("記事テーマ", count=2)


@pytest.mark.parametrize(
    "status_code,response_body,expected_message",
    [
        (401, {"error": {"code": "invalid_api_key"}}, "APIキーが無効"),
        (429, {"error": {"code": "rate_limit_exceeded"}}, "利用上限"),
        (400, {"error": {"code": "moderation_blocked"}}, "安全基準"),
    ],
)
def test_image_api_errors_are_safe(
    status_code: int,
    response_body: dict[str, object],
    expected_message: str,
) -> None:
    api_key = "-".join(["private", "openai", "value"])
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=response_body)
        )
    )
    generator = OpenAINoteImageGenerator(api_key, client=client)

    with pytest.raises(NoteImageGenerationError) as exc_info:
        generator.generate("記事テーマ")

    assert expected_message in str(exc_info.value)
    assert api_key not in str(exc_info.value)


def test_image_api_timeout_has_clear_guidance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    generator = OpenAINoteImageGenerator(
        "-".join(["test", "key"]),
        timeout_seconds=150,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(NoteImageGenerationError, match="150秒以内"):
        generator.generate("記事テーマ")

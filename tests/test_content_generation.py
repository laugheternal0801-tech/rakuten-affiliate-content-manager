from __future__ import annotations

import json

import httpx
import pytest

from app.models import Product
from app.services.content_generation import (
    ANTHROPIC_MESSAGES_URL,
    ContentGenerationError,
    GenerationContext,
    LLMContentGenerator,
    TemplateContentGenerator,
    analyze_copy,
    get_content_generator,
)


def make_product() -> Product:
    return Product(
        item_code="shop:test-item",
        item_name="テスト用コーヒードリッパー",
        catchcopy="毎日のコーヒー時間に使いやすいシンプルな形",
        item_price=2_980,
        affiliate_url="https://item.rakuten.co.jp/shop/test-item/",
        item_url="https://item.rakuten.co.jp/shop/test-item/",
        affiliate_rate=4.0,
        review_count=128,
        review_average=4.5,
        postage_flag=0,
        availability=1,
        point_rate=2.0,
        is_sample=False,
    )


def make_context() -> GenerationContext:
    return GenerationContext(
        products=[make_product()],
        theme="自宅で楽しむコーヒー",
        link_mode="direct",
        disclosure="この記事にはアフィリエイト広告が含まれています。",
        pr_required=True,
        target_audience="忙しい朝でもコーヒーを楽しみたい人",
        tone="親しみやすい",
        appeal_points=("価格", "レビュー評価", "送料"),
        custom_message="朝の準備に取り入れやすいか確認してみてください。",
        target_length=500,
        hashtag_count=3,
    )


def test_generates_three_distinct_variations() -> None:
    outputs = TemplateContentGenerator().generate_variations("note", make_context(), 3)

    assert len(outputs) == 3
    assert len({output.title for output in outputs}) == 3
    assert len({output.body for output in outputs}) == 3
    assert all("忙しい朝でもコーヒーを楽しみたい人" in output.body for output in outputs)
    assert all(output.body.startswith("【PR】") for output in outputs)


def test_x_copy_uses_requested_appeals_and_hashtags() -> None:
    output = TemplateContentGenerator().generate("X", make_context())
    analysis = analyze_copy(output.body, 500)

    assert "2,980円" in output.body
    assert "平均評価は4.5" in output.body
    assert analysis.hashtag_count == 3
    assert analysis.japanese_status == "日本語中心"


def test_unverified_experience_is_not_presented_as_personal_use() -> None:
    output = TemplateContentGenerator().generate("楽天ROOM", make_context())

    assert "使用感は未確認" in output.body
    assert "使ってみた" not in output.body
    assert "愛用している" not in output.body


def test_pinterest_includes_visual_production_notes() -> None:
    output = TemplateContentGenerator().generate("Pinterest", make_context())

    assert output.metadata["画像に載せる文字"]
    assert output.metadata["構図案"]
    assert output.metadata["撮影メモ"]


def test_copy_analysis_counts_japanese_and_target_difference() -> None:
    analysis = analyze_copy("【PR】\n日本語の投稿です。\n#商品比較", 40)

    assert analysis.character_count == 20
    assert analysis.difference == -20
    assert analysis.hashtag_count == 1
    assert analysis.japanese_status == "日本語中心"


def test_claude_generator_sends_safe_structured_request_and_parses_response() -> None:
    captured: dict[str, object] = {}
    response_content = json.dumps(
        {
            "title": "朝のコーヒー選び",
            "body": "商品情報を確認しながら選べます。",
            "creative_angle": "時短重視",
            "key_points": ["価格", "レビュー"],
            "review_notes": ["価格を公開前に確認"],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": response_content}],
                "stop_reason": "end_turn",
            },
        )

    api_key = "-".join(["test", "api", "key"])
    client = httpx.Client(transport=httpx.MockTransport(handler))
    generator = LLMContentGenerator(
        TemplateContentGenerator(),
        provider="anthropic",
        api_key=api_key,
        model="claude-sonnet-5",
        client=client,
    )

    output = generator.generate("note", make_context())

    assert captured["url"] == ANTHROPIC_MESSAGES_URL
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-api-key"] == api_key
    assert headers["anthropic-version"] == "2023-06-01"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "claude-sonnet-5"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in payload
    assert "未信頼の参照データ" in payload["system"]
    assert output.title == "朝のコーヒー選び"
    assert output.body.startswith("【PR】")
    assert make_product().affiliate_url in output.body
    assert output.metadata["モデル"] == "claude-sonnet-5"


def test_claude_generator_returns_safe_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

    api_key = "-".join(["private", "test", "value"])
    generator = get_content_generator(
        "llm",
        provider="anthropic",
        api_key=api_key,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ContentGenerationError) as exc_info:
        generator.generate("X", make_context())

    assert "APIキーが無効" in str(exc_info.value)
    assert api_key not in str(exc_info.value)


def test_claude_generator_rejects_malformed_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "not-json"}],
                "stop_reason": "end_turn",
            },
        )

    generator = get_content_generator(
        "llm",
        provider="anthropic",
        api_key="-".join(["test", "key"]),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ContentGenerationError, match="応答を投稿案として読み取れません"):
        generator.generate("Instagram", make_context())


def test_llm_mode_requires_api_key() -> None:
    with pytest.raises(ContentGenerationError, match="APIキーが未設定"):
        get_content_generator("llm", provider="anthropic", api_key="")

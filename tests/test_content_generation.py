from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import httpx
import pytest

from app.models import Experience, Product
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


def make_comparison_products(count: int = 5) -> list[Product]:
    products = []
    for index in range(count):
        product = make_product()
        product.item_code = f"shop:test-item-{index + 1}"
        product.item_name = f"比較商品{index + 1}"
        product.affiliate_url = f"https://item.rakuten.co.jp/shop/test-item-{index + 1}/"
        product.item_url = product.affiliate_url
        products.append(product)
    return products


def test_generates_three_distinct_variations() -> None:
    outputs = TemplateContentGenerator().generate_variations("note", make_context(), 3)

    assert len(outputs) == 3
    assert len({output.title for output in outputs}) == 3
    assert len({output.body for output in outputs}) == 3
    assert all("忙しい朝でもコーヒーを楽しみたい人" in output.body for output in outputs)
    assert all(output.body.startswith("【PR】") for output in outputs)


def test_template_note_uses_a_mobile_friendly_vertical_comparison() -> None:
    context = replace(make_context(), products=make_comparison_products(5))

    output = TemplateContentGenerator().generate("note", context)

    assert "| 商品 |" not in output.body
    assert "|---|" not in output.body
    assert output.body.count("- **価格**：") == 5
    assert output.body.count("- **レビュー**：★") == 5
    assert output.body.count("- **ポイント**：") == 5


def test_x_copy_uses_requested_appeals_and_hashtags() -> None:
    output = TemplateContentGenerator().generate("X", make_context())
    analysis = analyze_copy(output.body, 500)

    assert "2,980円" in output.body
    assert "平均評価は4.5" in output.body
    assert analysis.hashtag_count == 3
    assert analysis.japanese_status == "日本語中心"


def test_unverified_experience_is_not_presented_as_personal_use() -> None:
    output = TemplateContentGenerator().generate("楽天ROOM", make_context())

    assert "使用感は確認していない" in output.body
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
            "seo_title": "朝のコーヒー選び｜比較ポイント",
            "summary": "忙しい朝のコーヒー選びで確認したい価格やレビューの見方をまとめます。",
            "hashtags": ["コーヒー", "商品比較", "楽天市場"],
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

    assert generator.timeout_seconds == 120.0
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
    assert output.metadata["SEOタイトル"] == "朝のコーヒー選び｜比較ポイント"
    assert output.metadata["推奨ハッシュタグ"] == ["#コーヒー", "#商品比較", "#楽天市場"]


def test_comparison_article_ignores_legacy_experience_records() -> None:
    captured: dict[str, object] = {}
    products = make_comparison_products()
    products[0].experience = Experience(
        has_used=True,
        usage_period="3か月",
        usage_scene="平日の朝",
        positive_points="準備が短く済んだ",
        negative_points="大きめのカップは置きづらかった",
        verified_at=date(2026, 8, 9),
    )
    products[1].experience = Experience(
        has_used=True,
        usage_scene="この未確認体験は送信しない",
        positive_points="未確認の感想",
        compared_products="手入れが簡単というレビュー傾向",
        memo="毎日使うなら洗いやすさを重視したい",
        verified_at=None,
    )
    response_content = json.dumps(
        {
            "title": "コーヒーメーカーおすすめ比較",
            "body": "# 比較記事\n\n5商品を比較します。",
            "creative_angle": "使用場面別比較",
            "key_points": ["手入れ", "価格", "サイズ"],
            "review_notes": ["最新価格を確認"],
            "seo_title": "コーヒーメーカーおすすめ比較5選",
            "summary": "忙しい朝に使いやすいコーヒーメーカー5商品を、手入れや価格で比較します。",
            "hashtags": ["コーヒーメーカー", "おすすめ", "比較"],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": response_content}],
                "stop_reason": "end_turn",
            },
        )

    generator = LLMContentGenerator(
        TemplateContentGenerator(),
        provider="anthropic",
        api_key="test-key",
        model="claude-sonnet-5",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    context = replace(
        make_context(),
        products=products,
        theme="コーヒーメーカー",
        target_audience="忙しい朝に手早く準備できる機種を選べず困っている人",
        target_length=3_000,
        hashtag_count=0,
        article_format="comparison_review",
        article_genre="家庭用コーヒーメーカー",
        main_keyword="コーヒーメーカー おすすめ 比較",
    )

    output = generator.generate("note", context)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    prompt = payload["messages"][0]["content"]
    assert "商品比較記事を5年執筆しているレビュアー" in prompt
    assert "この記事が向いていない人" in prompt
    assert "使い方別のおすすめ" in prompt
    assert "コーヒーメーカー おすすめ 比較" in prompt
    assert "準備が短く済んだ" not in prompt
    assert "平日の朝" not in prompt
    assert "この未確認体験は送信しない" not in prompt
    assert "未確認の感想" not in prompt
    assert "手入れが簡単というレビュー傾向" not in prompt
    assert "毎日使うなら洗いやすさを重視したい" not in prompt
    assert '"verified": false' in prompt
    assert '"review_observations": ""' in prompt
    assert "実際に使用した感想には置き換えない" in prompt
    assert "Markdownのパイプ表" in prompt
    assert "スマートフォンでも読みやすい縦型の比較表" in prompt
    assert "『価格』『内容量・数量』『1個・1杯あたりの目安』" in prompt
    assert "ひと目で選ぶなら" in prompt
    assert "推測で埋めない" in prompt
    assert "本文全体で2,600〜3,200字" in prompt
    assert "1商品210字以内" in prompt
    assert "詳しさより3,200字の上限を優先" in prompt
    assert "key_pointsとreview_notesはそれぞれ3項目以内" in prompt
    assert output.metadata["記事形式"] == "5〜7商品比較レビュー"
    assert all(product.affiliate_url in output.body for product in products)


def test_comparison_prompt_reduces_each_review_budget_for_seven_products() -> None:
    context = replace(
        make_context(),
        products=make_comparison_products(7),
        article_format="comparison_review",
        article_genre="ドリップコーヒー",
        main_keyword="ドリップコーヒー 比較",
    )

    prompt = LLMContentGenerator._user_prompt(context, {"products": []})

    assert "比較する商品：参照データのproductsにある7点" in prompt
    assert "1商品150字以内" in prompt


@pytest.mark.parametrize("count", [4, 8])
def test_comparison_article_requires_five_to_seven_products(count: int) -> None:
    generator = LLMContentGenerator(
        TemplateContentGenerator(),
        provider="anthropic",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )
    context = replace(
        make_context(),
        products=make_comparison_products(count),
        article_format="comparison_review",
        article_genre="家庭用コーヒーメーカー",
        main_keyword="コーヒーメーカー 比較",
    )

    with pytest.raises(ValueError, match="5〜7点"):
        generator.generate("note", context)


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

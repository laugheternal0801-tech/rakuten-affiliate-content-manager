from __future__ import annotations

from app.models import Product
from app.services.content_generation import (
    GenerationContext,
    TemplateContentGenerator,
    analyze_copy,
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

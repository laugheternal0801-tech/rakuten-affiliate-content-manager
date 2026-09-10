from __future__ import annotations

import json

import httpx
import pytest

from app.services.article_revision import (
    ClaudeArticleRevisionService,
    find_article_section,
    replace_article_section,
)
from app.services.content_generation import ContentGenerationError

ARTICLE = """# コーヒー比較

忙しい朝は、どれを選べばよいか迷います。

## 選ぶときに見るべき基準を3つ

1. 手入れ
2. 価格
3. サイズ

## 比較表

### 商品A
- 価格：2,000円

## 商品ごとのレビュー

### 商品A
良い点と気になる点です。
https://example.com/a

## 使い方別のおすすめ

朝なら商品Aです。

## まとめ

用途に合わせて選びます。
"""


def test_find_and_replace_one_markdown_section() -> None:
    section = find_article_section(ARTICLE, "comparison")

    assert section.text.startswith("## 比較表")
    assert "## 商品ごとのレビュー" not in section.text

    replaced = replace_article_section(ARTICLE, section, "## 比較表\n\n新しい比較内容")

    assert "新しい比較内容" in replaced
    assert "## 商品ごとのレビュー" in replaced
    assert "1. 手入れ" in replaced


def test_introduction_can_be_found_without_a_dedicated_heading() -> None:
    section = find_article_section(ARTICLE, "introduction")

    assert "忙しい朝" in section.text
    assert "選ぶときに見るべき基準" not in section.text


def test_revision_service_changes_only_selected_section_and_keeps_urls() -> None:
    response_content = json.dumps(
        {
            "replacement": (
                "## 商品ごとのレビュー\n\n### 商品A\n"
                "朝の使用場面が伝わるレビューです。\nhttps://example.com/a"
            ),
            "change_summary": "使用場面を具体的にしました。",
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert "selected_textだけ" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": response_content}],
                "stop_reason": "end_turn",
            },
        )

    service = ClaudeArticleRevisionService(
        "test-key",
        model="claude-sonnet-5",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    revision = service.revise(
        title="コーヒー比較",
        body=ARTICLE,
        target="reviews",
        instruction="朝の場面を具体的に",
    )

    assert revision.title == "コーヒー比較"
    assert "朝の使用場面" in revision.body
    assert "## 比較表" in revision.body
    assert "https://example.com/a" in revision.body


def test_revision_is_rejected_when_an_existing_link_disappears() -> None:
    response_content = json.dumps(
        {"replacement": "## 商品ごとのレビュー\n\nリンクなし", "change_summary": "短縮"},
        ensure_ascii=False,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": response_content}],
                    "stop_reason": "end_turn",
                },
            )
        )
    )
    service = ClaudeArticleRevisionService("test-key", model="claude-sonnet-5", client=client)

    with pytest.raises(ContentGenerationError, match="商品リンクが欠けた"):
        service.revise(title="コーヒー比較", body=ARTICLE, target="reviews")

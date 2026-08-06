from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.models import Experience, Product
from app.schemas import GeneratedContent

CHANNELS = ["note", "X", "Pinterest", "Instagram", "楽天ROOM"]


@dataclass(frozen=True)
class GenerationContext:
    products: Sequence[Product]
    theme: str
    link_mode: str
    disclosure: str
    pr_required: bool


class ContentGenerator(ABC):
    @abstractmethod
    def generate(self, channel: str, context: GenerationContext) -> GeneratedContent:
        raise NotImplementedError


def _link(product: Product, mode: str) -> str:
    if mode == "direct" and product.affiliate_url:
        return product.affiliate_url
    return "［楽天市場で見る］"


def _experience(product: Product) -> Experience | None:
    return product.experience


def _fact_summary(product: Product) -> str:
    postage = "送料無料" if product.postage_flag == 0 else "送料条件は商品ページで要確認"
    return (
        f"価格 {product.item_price:,}円、レビュー {product.review_count:,}件、"
        f"平均 {product.review_average:.1f}、{postage}、ポイント {product.point_rate:g}倍"
    )


def _experience_section(product: Product) -> str:
    exp = _experience(product)
    if not exp or exp.has_used is not True:
        return (
            "商品情報を確認した範囲では比較候補です。使用感は未確認のため、"
            "体験情報を入力してください。"
        )
    facts = [
        f"使用期間: {exp.usage_period or '要確認'}",
        f"使用場面: {exp.usage_scene or '要確認'}",
        f"よかった点: {exp.positive_points or '要確認'}",
        f"気になった点: {exp.negative_points or '要確認'}",
    ]
    return "\n".join(facts)


def _prefix(context: GenerationContext) -> str:
    if context.pr_required:
        return "PR\n\n"
    return f"{context.disclosure}\n\n" if context.disclosure else ""


class TemplateContentGenerator(ContentGenerator):
    def generate(self, channel: str, context: GenerationContext) -> GeneratedContent:
        if not context.products:
            raise ValueError("対象商品を1件以上選択してください。")
        generators = {
            "note": self._note,
            "X": self._x,
            "Pinterest": self._pinterest,
            "Instagram": self._instagram,
            "楽天ROOM": self._room,
        }
        try:
            return generators[channel](context)
        except KeyError as exc:
            raise ValueError("未対応の媒体です。") from exc

    def _note(self, context: GenerationContext) -> GeneratedContent:
        products = context.products
        title_candidates = [
            f"{context.theme}の選び方｜比較前に確認したい3つの基準",
            f"{context.theme}を比較：価格・レビュー・送料で候補を整理",
            f"失敗を減らす{context.theme}選び｜楽天市場の候補を事実ベースで確認",
        ]
        rows = ["| 商品 | 価格 | レビュー | 送料 |", "|---|---:|---:|---|"]
        for product in products:
            postage = "無料" if product.postage_flag == 0 else "要確認"
            rows.append(
                f"| {product.item_name} | {product.item_price:,}円 | "
                f"{product.review_average:.1f}（{product.review_count:,}件） | {postage} |"
            )
        sections: list[str] = []
        for product in products:
            exp = _experience(product)
            sections.append(
                "\n".join(
                    [
                        f"## {product.item_name}",
                        product.catchcopy or "商品キャッチコピーは要確認です。",
                        "",
                        f"- 確認できた情報: {_fact_summary(product)}",
                        f"- メリット候補: {(exp.positive_points if exp else '') or '要確認'}",
                        f"- デメリット・注意点: {(exp.negative_points if exp else '') or '要確認'}",
                        "- 向いている人: "
                        + ((exp.suitable_for if exp else "") or "体験情報を入力してください"),
                        "- 向いていない人: "
                        + ((exp.unsuitable_for if exp else "") or "体験情報を入力してください"),
                        "",
                        _experience_section(product),
                        "",
                        f"{_link(product, context.link_mode)}",
                    ]
                )
            )
        body = _prefix(context) + "\n".join(
            [
                "# タイトル候補",
                *[f"- {title}" for title in title_candidates],
                "",
                "## 想定読者",
                f"{context.theme}を楽天市場で比較し、根拠を確認して選びたい人。",
                "",
                "## 読者の悩み",
                "候補が多く、価格だけでなくレビュー数・送料・利用場面も整理したい。",
                "",
                "## 結論",
                "自動評価点は候補整理の補助です。仕様・価格・在庫を再確認して最終判断します。",
                "",
                "## 選び方の基準3つ",
                "1. 用途と予算が合うか",
                "2. レビュー件数と平均評価を分けて見る",
                "3. 送料・在庫・セール期限を確認する",
                "",
                "## 商品比較表",
                *rows,
                "",
                *sections,
                "",
                "## 用途別おすすめ",
                "用途別の結論は、保存した仕様と体験情報を確認して追記してください（要確認）。",
                "",
                "## まとめ",
                "価格や在庫は変動します。リンク先の楽天市場で最新情報を確認してください。",
                "",
                "最終確認は投稿者本人が行ってください。",
            ]
        )
        return GeneratedContent(
            channel="note",
            title=title_candidates[0],
            body=body,
            metadata={"titles": title_candidates},
        )

    def _x(self, context: GenerationContext) -> GeneratedContent:
        product = context.products[0]
        link = _link(product, context.link_mode)
        patterns = {
            "悩み型": (
                "候補が多くて迷うときは、価格だけでなく送料・レビュー件数・平均評価を分けて確認。"
            ),
            "比較型": (
                f"{product.item_name}は{_fact_summary(product)}。比較候補の1つとして整理しました。"
            ),
            "結論型": "結論：自動評価点は候補を絞る補助。最後は用途と最新の商品情報で判断。",
            "記事誘導型": f"{context.theme}の選び方を、価格・レビュー・送料の3軸で整理しました。",
            "セール・価格更新型": (
                f"確認時点の価格は{product.item_price:,}円。"
                "価格・在庫・終了日時はリンク先で再確認を。"
            ),
        }
        posts: list[dict[str, Any]] = []
        for pattern, base in patterns.items():
            for number, suffix in enumerate(
                [
                    "迷ったときの確認メモです。",
                    "失敗回避のチェックリストに。",
                    "用途に合うかを先に確認。",
                ],
                start=1,
            ):
                text = f"{base}\n{suffix}\n{link}\n{context.disclosure}".strip()
                posts.append({"種類": pattern, "案": number, "本文": text, "文字数": len(text)})
        body = _prefix(context) + "\n\n".join(
            f"### {post['種類']} 案{post['案']}（{post['文字数']}文字）\n{post['本文']}"
            for post in posts
        )
        return GeneratedContent(
            channel="X", title=f"{context.theme} X投稿案", body=body, metadata={"posts": posts}
        )

    def _pinterest(self, context: GenerationContext) -> GeneratedContent:
        product = context.products[0]
        intents = ["初心者向け", "コスパ重視", "失敗回避", "比較", "用途別"]
        pins = []
        for intent in intents:
            pins.append(
                {
                    "検索意図": intent,
                    "ピンタイトル": f"{intent}｜{context.theme}の選び方チェック",
                    "説明文": (
                        f"{_fact_summary(product)}を確認し、{intent}の観点で候補を整理。"
                        "最新情報は楽天市場で要確認。"
                    ),
                    "画像に載せる文字": f"{context.theme}\n{intent}の3チェック",
                    "構図案": "オリジナル写真を中央、確認ポイントを左右に3つ配置",
                    "保存先ボード": f"{context.theme}選び",
                    "note誘導文": "比較基準の詳細はnoteで確認",
                    "楽天誘導文": f"最新の価格・在庫を{_link(product, context.link_mode)}",
                    "撮影案": "自然光でオリジナル写真を撮影。商品ロゴや公式画像は加工しない。",
                }
            )
        body = _prefix(context) + "\n\n".join(
            "\n".join(
                [
                    f"## {pin['検索意図']}",
                    *[f"- {key}: {value}" for key, value in pin.items() if key != "検索意図"],
                ]
            )
            for pin in pins
        )
        return GeneratedContent(
            channel="Pinterest",
            title=f"{context.theme} Pinterest案",
            body=body,
            metadata={"pins": pins},
        )

    def _instagram(self, context: GenerationContext) -> GeneratedContent:
        product = context.products[0]
        exp = _experience(product)
        fact_guard = (
            exp.positive_points
            if exp and exp.has_used is True and exp.positive_points
            else "使用感は未確認。商品情報を確認した範囲で整理"
        )
        slides = [
            ("1", f"{context.theme}、何で選ぶ？"),
            ("2", "まず用途を決める"),
            ("3", f"価格: {product.item_price:,}円（確認日時を記載）"),
            ("4", f"レビュー: {product.review_average:.1f} / {product.review_count:,}件"),
            ("5", "送料・在庫をチェック"),
            ("6", fact_guard),
            ("7", "比較表で候補を整理"),
            ("8", "最新情報は楽天市場で確認"),
        ]
        slide_text = "\n".join(
            f"### {no}枚目\n{heading}\n本文: {heading}を事実ベースで確認。"
            for no, heading in slides
        )
        body = _prefix(context) + "\n".join(
            [
                "# カルーセル8枚構成",
                slide_text,
                "",
                "## キャプション",
                f"{context.theme}を選ぶ前に、価格・送料・レビューを整理しました。{fact_guard}。",
                f"{_link(product, context.link_mode)}",
                "",
                "## リール台本",
                "冒頭: 選び方で迷っていませんか？\n"
                "展開: 3つの確認軸を順に表示\n"
                "締め: 保存して商品ページで再確認",
                "",
                "## ストーリーズ3枚構成",
                "1. 悩みの提示\n2. 比較の3軸\n3. プロフィールまたは楽天ROOMへの導線",
                "",
                "## プロフィール誘導文",
                "比較メモの詳細はプロフィールのリンクへ。",
                "",
                "## 楽天ROOM誘導文",
                "保存した候補は楽天ROOMのコレクションで確認できます。",
                "",
                "## ハッシュタグ候補",
                f"#{context.theme.replace(' ', '')} #楽天購入候補 #商品比較 #失敗回避",
                "",
                "## オリジナル写真の撮影チェックリスト",
                "- 自分で撮影した写真か\n"
                "- ロゴや公式商品画像を加工していないか\n"
                "- 実際の色味を誤認させていないか",
            ]
        )
        return GeneratedContent(
            channel="Instagram", title=f"{context.theme} Instagram案", body=body
        )

    def _room(self, context: GenerationContext) -> GeneratedContent:
        product = context.products[0]
        exp = _experience(product)
        used = exp is not None and exp.has_used is True
        positive_points = exp.positive_points if used and exp is not None else ""
        short = f"{_fact_summary(product)}。" + (
            positive_points or "購入候補として仕様を確認しました。"
        )
        body = _prefix(context) + "\n".join(
            [
                "# 短い紹介文",
                short,
                "",
                "# 詳細な紹介文",
                product.catchcopy or "商品情報はリンク先で要確認です。",
                _experience_section(product),
                f"{_link(product, context.link_mode)}",
                "",
                "# 向いている人",
                (exp.suitable_for if exp else "体験情報を入力してください") or "要確認",
                "",
                "# 注意点",
                (exp.negative_points if exp else "価格・送料・在庫は楽天市場で再確認") or "要確認",
                "",
                "# ハッシュタグ",
                f"#{context.theme.replace(' ', '')} #楽天ROOM #購入候補",
                "",
                "# コレクション名候補",
                f"{context.theme}の比較候補",
                "",
                "# コレクション説明文",
                "取得した商品情報と入力済み体験情報をもとに、比較候補を整理しています。",
            ]
        )
        return GeneratedContent(channel="楽天ROOM", title=f"{product.item_name} 紹介案", body=body)


class LLMContentGenerator(ContentGenerator):
    """Extension point for a future provider; safely falls back until configured."""

    def __init__(self, fallback: ContentGenerator, provider: str = "", api_key: str = "") -> None:
        self.fallback = fallback
        self.provider = provider
        self.api_key = api_key

    def generate(self, channel: str, context: GenerationContext) -> GeneratedContent:
        # No paid provider is coupled to the initial implementation.
        return self.fallback.generate(channel, context)


def get_content_generator(mode: str, provider: str = "", api_key: str = "") -> ContentGenerator:
    template = TemplateContentGenerator()
    if mode == "llm" and provider and api_key:
        return LLMContentGenerator(template, provider=provider, api_key=api_key)
    return template

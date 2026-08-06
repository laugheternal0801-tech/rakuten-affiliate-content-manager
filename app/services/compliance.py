from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from app.models import Product


@dataclass(frozen=True)
class ComplianceIssue:
    level: str
    code: str
    message: str
    suggestion: str


@dataclass(frozen=True)
class ComplianceReport:
    status: str
    issues: list[ComplianceIssue]
    checked_at: str
    disclaimer: str = (
        "このチェックは法的適合性を保証しません。最終確認は投稿者本人が行ってください。"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "issues": [asdict(issue) for issue in self.issues],
            "checked_at": self.checked_at,
            "disclaimer": self.disclaimer,
        }


DANGEROUS_PHRASES = {
    "絶対": "断定を避け、条件や個人差を明記してください。",
    "必ず": "『場合があります』『確認してください』などに置き換えてください。",
    "確実": "根拠の範囲を限定した表現にしてください。",
    "完璧": "具体的な長所と制約を併記してください。",
    "日本一": "客観的な根拠がなければ削除してください。",
    "業界一": "客観的な根拠がなければ削除してください。",
    "最安": "確認日時と比較範囲を示すか、削除してください。",
    "No.1": "調査主体・期間・範囲を示せない場合は削除してください。",
    "治る": "医療効果を断定せず、必要に応じて専門家へ確認してください。",
    "痩せる": "効果を断定せず、個人差があることを明記してください。",
    "効果がある": "根拠の範囲と個人差を明記してください。",
    "誰でも": "対象条件を具体化してください。",
    "100％": "根拠が検証できない場合は削除してください。",
    "口コミでは": "他人の口コミの転載・一般化を避け、取得元を確認してください。",
    "レビューによると": "レビューの転載・要約ではなくAPIの集計値として扱ってください。",
    "愛用している": "本人の確認済み体験情報がある場合だけ使用してください。",
    "使ってみた": "本人の確認済み体験情報がある場合だけ使用してください。",
}

UNUSED_PROHIBITED = [
    "使ってみた",
    "愛用している",
    "実感した",
    "飲んでみた",
    "購入した",
    "使いやすかった",
    "おすすめできると感じた",
]


def is_rakuten_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "rakuten.co.jp" or host.endswith(".rakuten.co.jp"))


def _unknown_numeric_claims(text: str, products: Iterable[Product]) -> list[str]:
    allowed: set[str] = set()
    for product in products:
        allowed.update(
            {
                str(product.item_price),
                f"{product.item_price:,}",
                str(product.review_count),
                f"{product.review_count:,}",
                f"{product.review_average:g}",
                f"{product.point_rate:g}",
            }
        )
    candidates = set(re.findall(r"(?<![\w])\d[\d,]*(?:\.\d+)?(?=円|件|％|%|倍)", text))
    return sorted(value for value in candidates if value not in allowed)


def check_content(
    text: str,
    products: list[Product],
    *,
    affiliate_disclosure_required: bool,
    info_verified_at: date | None,
    comparison_basis_saved: bool,
) -> ComplianceReport:
    issues: list[ComplianceIssue] = []
    for phrase, suggestion in DANGEROUS_PHRASES.items():
        if phrase.lower() in text.lower():
            issues.append(
                ComplianceIssue(
                    "warning",
                    "dangerous_phrase",
                    f"注意表現「{phrase}」を検出しました。",
                    suggestion,
                )
            )

    now = datetime.now().astimezone()
    for product in products:
        exp = product.experience
        if not exp or exp.has_used is not True:
            for phrase in UNUSED_PROHIBITED:
                if phrase in text:
                    issues.append(
                        ComplianceIssue(
                            "blocking",
                            "fabricated_experience",
                            f"未使用商品の体験を示す表現「{phrase}」があります。",
                            "『商品情報を確認した範囲では』『購入候補として』『仕様上は』へ修正してください。",
                        )
                    )
        if product.is_sample:
            issues.append(
                ComplianceIssue(
                    "blocking",
                    "sample_product",
                    f"「{product.item_name}」は架空のサンプルデータです。",
                    "実在商品を楽天公式APIから取得し、情報を確認して差し替えてください。",
                )
            )
        if product.availability != 1:
            issues.append(
                ComplianceIssue(
                    "blocking",
                    "unavailable",
                    "在庫切れの商品が含まれています。",
                    "商品を差し替えるか在庫復活後に再確認してください。",
                )
            )
        if product.sale_end and product.sale_end < now:
            issues.append(
                ComplianceIssue(
                    "blocking",
                    "expired_sale",
                    "セール終了日時を過ぎています。",
                    "セール表現を削除し、価格を再確認してください。",
                )
            )
        if not product.affiliate_url:
            issues.append(
                ComplianceIssue(
                    "warning",
                    "missing_affiliate_url",
                    "アフィリエイトURLがありません。",
                    "affiliateIdを設定して商品を再取得してください。",
                )
            )
        elif not is_rakuten_url(product.affiliate_url):
            issues.append(
                ComplianceIssue(
                    "blocking",
                    "invalid_affiliate_url",
                    "リンク先が楽天市場の公式HTTPS URLではありません。",
                    "保存済みURLを確認してください。",
                )
            )

    if affiliate_disclosure_required and not any(
        marker in text for marker in ["アフィリエイト広告", "PR", "広告"]
    ):
        issues.append(
            ComplianceIssue(
                "blocking",
                "missing_disclosure",
                "広告・PR表記が見つかりません。",
                "本文冒頭付近に広告表記を追加してください。",
            )
        )
    if "円" in text and info_verified_at is None:
        issues.append(
            ComplianceIssue(
                "warning",
                "missing_price_date",
                "価格情報の確認日時がありません。",
                "情報確認日を保存し、本文にも確認日を記載してください。",
            )
        )
    if len(products) > 1 and not comparison_basis_saved:
        issues.append(
            ComplianceIssue(
                "warning",
                "missing_comparison_basis",
                "比較根拠が体験情報に保存されていません。",
                "比較した商品・基準を保存してください。",
            )
        )
    unknown_numbers = _unknown_numeric_claims(text, products)
    if unknown_numbers:
        issues.append(
            ComplianceIssue(
                "warning",
                "unverified_numbers",
                f"取得元を確認できない数値候補があります: {', '.join(unknown_numbers)}",
                "楽天APIまたは本人確認済み情報に根拠があるか確認してください。",
            )
        )

    if any(issue.level == "blocking" for issue in issues):
        status = "投稿不可"
    elif issues:
        status = "要確認"
    else:
        status = "OK"
    return ComplianceReport(status=status, issues=issues, checked_at=now.isoformat())

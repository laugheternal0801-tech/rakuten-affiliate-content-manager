from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Experience, Product
from app.services.compliance import check_content


def make_product(**overrides: object) -> Product:
    values: dict[str, object] = {
        "item_code": "shop:1",
        "item_name": "コーヒー",
        "item_price": 1200,
        "affiliate_url": "https://hb.afl.rakuten.co.jp/example",
        "item_url": "https://item.rakuten.co.jp/shop/1/",
        "availability": 1,
        "postage_flag": 0,
        "review_count": 100,
        "review_average": 4.5,
        "point_rate": 1,
        "is_sample": False,
    }
    values.update(overrides)
    return Product(**values)


def run_check(text: str, product: Product):
    return check_content(
        text,
        [product],
        affiliate_disclosure_required=True,
        info_verified_at=datetime.now().date(),
        comparison_basis_saved=True,
    )


def test_unused_product_false_experience_is_blocked() -> None:
    product = make_product()
    product.experience = Experience(product_id=1, has_used=False)
    report = run_check("PR\n使ってみたらよかったです。", product)
    assert report.status == "投稿不可"
    assert any(issue.code == "fabricated_experience" for issue in report.issues)


def test_dangerous_expression_is_warned() -> None:
    product = make_product()
    report = run_check("PR\n絶対に満足できます。", product)
    assert report.status == "要確認"
    assert any(issue.code == "dangerous_phrase" for issue in report.issues)


def test_missing_pr_disclosure_is_blocked() -> None:
    product = make_product()
    report = run_check("商品情報を確認した範囲では候補です。", product)
    assert any(issue.code == "missing_disclosure" for issue in report.issues)


def test_expired_sale_is_blocked() -> None:
    product = make_product(sale_end=datetime.now().astimezone() - timedelta(hours=1))
    report = run_check("PR\n商品情報を確認した範囲では候補です。", product)
    assert any(issue.code == "expired_sale" for issue in report.issues)


def test_out_of_stock_is_blocked() -> None:
    product = make_product(availability=0)
    report = run_check("PR\n商品情報を確認した範囲では候補です。", product)
    assert any(issue.code == "unavailable" for issue in report.issues)

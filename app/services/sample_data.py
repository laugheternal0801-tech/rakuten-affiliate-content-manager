from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product
from app.repositories import DEFAULT_SCORE_WEIGHTS, DEFAULT_WEEKLY_PLAN, save_product, set_setting
from app.services.scoring import calculate_score

SAMPLE_PRODUCTS: list[dict[str, object]] = [
    {
        "item_code": "sample-coffee:001",
        "item_name": "サンプル 深煎りコーヒー豆 200g",
        "catchcopy": "架空商品：香ばしさを想定した比較検証用データ",
        "item_price": 1480,
        "item_caption": "これは操作確認専用の架空商品です。実在の商品ではありません。",
        "affiliate_url": "",
        "item_url": "https://www.rakuten.co.jp/",
        "affiliate_rate": 4.0,
        "review_count": 180,
        "review_average": 4.3,
        "postage_flag": 0,
        "availability": 1,
        "point_rate": 2.0,
        "shop_name": "サンプル珈琲店（架空）",
        "shop_code": "sample-coffee",
        "genre_id": "100356",
        "is_sample": True,
    },
    {
        "item_code": "sample-coffee:002",
        "item_name": "サンプル マイルドブレンド ドリップバッグ 20袋",
        "catchcopy": "架空商品：手軽さを想定した比較検証用データ",
        "item_price": 2380,
        "item_caption": "これは操作確認専用の架空商品です。実在の商品ではありません。",
        "affiliate_url": "",
        "item_url": "https://www.rakuten.co.jp/",
        "affiliate_rate": 6.0,
        "review_count": 920,
        "review_average": 4.6,
        "postage_flag": 0,
        "availability": 1,
        "point_rate": 5.0,
        "shop_name": "テストロースター（架空）",
        "shop_code": "test-roaster",
        "genre_id": "100356",
        "is_sample": True,
    },
    {
        "item_code": "sample-coffee:003",
        "item_name": "サンプル デカフェコーヒー 粉 150g",
        "catchcopy": "架空商品：カフェインを控えたい場面の比較検証用データ",
        "item_price": 1780,
        "item_caption": "これは操作確認専用の架空商品です。実在の商品ではありません。",
        "affiliate_url": "",
        "item_url": "https://www.rakuten.co.jp/",
        "affiliate_rate": 3.0,
        "review_count": 75,
        "review_average": 4.1,
        "postage_flag": 1,
        "availability": 1,
        "point_rate": 1.0,
        "sale_start": datetime.now().astimezone() - timedelta(days=2),
        "sale_end": datetime.now().astimezone() + timedelta(days=5),
        "shop_name": "ローカル検証ショップ（架空）",
        "shop_code": "local-test",
        "genre_id": "100356",
        "is_sample": True,
    },
]


def seed_sample_data(session: Session) -> bool:
    if (session.scalar(select(func.count(Product.id))) or 0) > 0:
        return False
    for values in SAMPLE_PRODUCTS:
        score = calculate_score(
            values,
            keyword="コーヒー",
            target_min_price=1000,
            target_max_price=3000,
            weights=DEFAULT_SCORE_WEIGHTS,
        )
        values_with_score = {
            **values,
            "score": score.total,
            "score_details": score.details,
        }
        save_product(session, values_with_score)
    set_setting(session, "score_weights", DEFAULT_SCORE_WEIGHTS)
    set_setting(session, "pr_policy", "always")
    set_setting(session, "affiliate_disclosure", "この記事にはアフィリエイト広告が含まれています。")
    set_setting(session, "weekly_plan", DEFAULT_WEEKLY_PLAN)
    return True

from app.services.scoring import calculate_score, normalize_review_count


def test_review_count_normalization_is_logarithmic_and_bounded() -> None:
    assert normalize_review_count(0) == 0
    assert 0 < normalize_review_count(100) < normalize_review_count(1_000) < 1
    assert normalize_review_count(1_000_000) == 1


def test_score_calculation_returns_100_point_breakdown() -> None:
    product = {
        "item_name": "深煎り コーヒー",
        "catchcopy": "コーヒー豆",
        "item_price": 2_000,
        "affiliate_rate": 10,
        "review_count": 10_000,
        "review_average": 5,
        "postage_flag": 0,
    }
    weights = {
        "affiliate_rate": 25,
        "review_count": 20,
        "review_average": 15,
        "price_fit": 15,
        "free_shipping": 10,
        "keyword_match": 15,
    }
    score = calculate_score(
        product,
        keyword="コーヒー",
        target_min_price=1_000,
        target_max_price=3_000,
        weights=weights,
    )
    assert score.total == 100
    assert score.details["review_count"]["label"] == "レビュー件数（対数正規化）"

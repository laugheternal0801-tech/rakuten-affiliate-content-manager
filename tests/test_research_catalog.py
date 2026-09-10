from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Content, Product
from app.repositories import create_content
from app.research_catalog.briefs import brief_handoff_payload, build_article_brief
from app.research_catalog.models import (
    ResearchOffer,
    ResearchPlanRevision,
    ResearchProduct,
    ResearchSchemaVersion,
)
from app.research_catalog.quality import assess_product_quality, plan_ready_blockers
from app.research_catalog.repositories import (
    archive_research_product,
    ensure_schema_version,
    get_research_plan,
    link_brief_to_content,
    list_content_research_links,
    save_article_brief,
    save_evidence,
    save_offer,
    save_published_article_reference,
    save_research_plan,
    save_research_product,
)
from app.research_catalog.schemas import (
    EvidenceInput,
    OfferInput,
    PriceTaxStatus,
    ProductCandidateInput,
    ResearchPlanInput,
    SourceType,
    VerificationStatus,
)
from app.research_catalog.time import JST


def _database(path: Path):
    return create_engine(f"sqlite:///{path}")


def _candidate(
    code: str,
    *,
    name: str,
    model: str,
    variant: str,
    legacy_product_id: int | None = None,
) -> ProductCandidateInput:
    return ProductCandidateInput(
        product_code=code,
        product_name=name,
        manufacturer="国内メーカー",
        model_number=model,
        variant=variant,
        genre="検証中ジャンル",
        audience="日本国内で商品選びに迷う人",
        pain_point="仕様と条件を根拠付きで比較できない",
        use_case="自宅で毎日使う",
        status="researching",
        legacy_product_id=legacy_product_id,
    )


def _legacy_product(code: str, name: str) -> Product:
    return Product(
        item_code=code,
        item_name=name,
        item_price=1000,
        affiliate_rate=4.0,
        affiliate_url=f"https://affiliate.example/{code}",
        item_url=f"https://shop.example/{code}",
        is_sample=False,
    )


def test_research_quality_flags_stale_terms_and_blocks_ready_state(tmp_path: Path) -> None:
    engine = _database(tmp_path / "quality.db")
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
    with Session(engine) as session:
        product = save_research_product(
            session,
            _candidate("QUALITY-1", name="品質確認商品", model="Q-1", variant="標準"),
        )
        save_offer(
            session,
            product.id,
            OfferInput(
                seller_name="販売店",
                affiliate_url="https://affiliate.example/item?tracking=kept",
                price=Decimal("1200"),
                shipping_fee=Decimal("0"),
                price_tax_status=PriceTaxStatus.TAX_INCLUDED,
                commission_rate=4.0,
                commission_base_notes="税込商品価格を対象",
                terms_checked_at=now - timedelta(days=31),
                is_primary=True,
            ),
        )
        save_evidence(
            session,
            product.id,
            EvidenceInput(
                summary="メーカー仕様を確認",
                source_url="https://maker.example/spec",
                source_type=SourceType.MANUFACTURER,
                verification_status=VerificationStatus.CONFIRMED_FACT,
                checked_at=now,
            ),
        )
        session.flush()
        session.expire(product, ["offers", "evidence"])

        report = assess_product_quality(product, freshness_days=30, now=now)
        blockers = plan_ready_blockers(
            [product],
            ["価格"],
            {product.id: {"価格": ""}},
            {"根拠のある比較ができるか": {"memo": "", "basis": ""}},
            freshness_days=30,
            now=now,
        )

    assert report.label == "要確認"
    assert {issue.code for issue in report.blocking_issues} == {"terms_stale"}
    assert any("2点以上" in blocker for blocker in blockers)
    assert any("価格・報酬条件" in blocker for blocker in blockers)
    assert any("比較値" in blocker for blocker in blockers)
    assert any("判断根拠" in blocker for blocker in blockers)


def test_product_research_persists_unknown_zero_multiple_sources_and_variants(
    tmp_path: Path,
) -> None:
    engine = _database(tmp_path / "catalog.db")
    Base.metadata.create_all(engine)
    Base.metadata.create_all(engine)

    exact_affiliate_url = (
        "https://affiliate.example/item?scid=af_pc_etc&iasid=07rpp_10095___abc&x=1"
    )
    with Session(engine) as session:
        ensure_schema_version(session)
        product_a = save_research_product(
            session,
            _candidate("SKU-A-S", name="整理用品", model="MODEL-A", variant="Sサイズ"),
        )
        product_b = save_research_product(
            session,
            _candidate("SKU-A-L", name="整理用品", model="MODEL-A", variant="Lサイズ"),
        )
        save_offer(
            session,
            product_a.id,
            OfferInput(
                seller_name="販売店A",
                product_url="https://shop.example/item?b=2&a=1",
                affiliate_url=exact_affiliate_url,
                price=None,
                shipping_fee=Decimal("0"),
                commission_rate=None,
                price_tax_status=PriceTaxStatus.UNKNOWN,
                commission_base_notes="不明",
                is_primary=True,
            ),
        )
        save_offer(
            session,
            product_a.id,
            OfferInput(
                seller_name="販売店B",
                price=Decimal("0"),
                shipping_fee=None,
                commission_rate=0.0,
                currency="jpy",
            ),
        )
        save_evidence(
            session,
            product_a.id,
            EvidenceInput(
                summary="メーカー仕様欄で寸法を確認",
                source_url="https://maker.example/spec?model=A&size=S",
                source_type=SourceType.MANUFACTURER,
                verification_status=VerificationStatus.CONFIRMED_FACT,
                checked_at=datetime(2026, 9, 9, 12, 0, tzinfo=JST),
            ),
        )
        save_evidence(
            session,
            product_a.id,
            EvidenceInput(
                summary="机上で設置面積を実測",
                source_url="",
                source_type=SourceType.OWN_MEASUREMENT,
                verification_status=VerificationStatus.CONFIRMED_FACT,
                checked_at=datetime(2026, 9, 9, 12, 30, tzinfo=JST),
                actually_used=True,
                use_conditions="幅90cmの机で7日間",
                observations="手前に約20cmの作業余白が残った",
            ),
        )
        session.commit()
        product_a_id = product_a.id
        product_b_id = product_b.id

    with Session(engine) as session:
        product_a = session.get(ResearchProduct, product_a_id)
        product_b = session.get(ResearchProduct, product_b_id)
        assert product_a is not None and product_b is not None
        assert (product_a.product_code, product_a.variant) == ("SKU-A-S", "Sサイズ")
        assert (product_b.product_code, product_b.variant) == ("SKU-A-L", "Lサイズ")
        offers = list(
            session.scalars(
                select(ResearchOffer)
                .where(ResearchOffer.product_id == product_a_id)
                .order_by(ResearchOffer.id)
            )
        )
        assert len(offers) == 2
        assert offers[0].price is None
        assert offers[0].shipping_fee == Decimal("0.00")
        assert offers[0].commission_rate is None
        assert offers[0].affiliate_url == exact_affiliate_url
        assert offers[1].price == Decimal("0.00")
        assert offers[1].shipping_fee is None
        assert offers[1].commission_rate == 0.0
        assert offers[1].currency == "JPY"
        assert len(product_a.evidence) == 2
        assert product_a.evidence[1].source_url == ""
        assert product_a.evidence[1].actually_used is True
        assert session.scalar(select(func.count(ResearchSchemaVersion.version))) == 1
        save_research_product(
            session,
            _candidate(
                "SKU-A-S",
                name="整理用品（編集済み）",
                model="MODEL-A",
                variant="Sサイズ",
            ),
            product_id=product_a_id,
        )
        session.commit()

    with Session(engine) as session:
        edited = session.get(ResearchProduct, product_a_id)
        assert edited is not None
        assert edited.product_name == "整理用品（編集済み）"
        archive_research_product(session, product_a_id)
        session.commit()

    with Session(engine) as session:
        archived = session.get(ResearchProduct, product_a_id)
        assert archived is not None
        assert archived.status == "archived"
        assert len(archived.offers) == 2
        assert len(archived.evidence) == 2


def test_plan_snapshots_brief_handoff_and_history_survive_schema_reapply(
    tmp_path: Path,
) -> None:
    engine = _database(tmp_path / "handoff.db")
    Base.metadata.create_all(engine)
    exact_affiliate_url = (
        "https://affiliate.example/rakuten?scid=af_pc_etc&iasid=07rpp_10095___keep"
    )

    with Session(engine) as session:
        legacy_a = _legacy_product("legacy-a", "既存商品A")
        legacy_b = _legacy_product("legacy-b", "既存商品B")
        old_content = Content(
            channel="note",
            theme="既存履歴",
            title="変更してはいけない履歴",
            draft_body="既存本文",
            status="review",
        )
        session.add_all([legacy_a, legacy_b, old_content])
        session.flush()
        old_content_id = old_content.id

        research_a = save_research_product(
            session,
            _candidate(
                "PLAN-A-S",
                name="候補A",
                model="A-100",
                variant="S",
                legacy_product_id=legacy_a.id,
            ),
        )
        research_b = save_research_product(
            session,
            _candidate(
                "PLAN-B-L",
                name="候補B",
                model="B-200",
                variant="L",
                legacy_product_id=legacy_b.id,
            ),
        )
        for index, product in enumerate((research_a, research_b), 1):
            save_offer(
                session,
                product.id,
                OfferInput(
                    seller_name=f"楽天店舗{index}",
                    product_url=f"https://item.rakuten.co.jp/shop/item-{index}",
                    affiliate_url=(
                        exact_affiliate_url
                        if index == 1
                        else "https://affiliate.example/rakuten?z=2&y=1"
                    ),
                    price=Decimal(str(1000 * index)),
                    shipping_fee=None,
                    commission_rate=None,
                    commission_base_notes="不明",
                    is_primary=True,
                ),
            )
        secondary_affiliate_url = "https://affiliate.example/second?keep=2&order=1"
        save_offer(
            session,
            research_a.id,
            OfferInput(
                seller_name="楽天店舗Aの別販売先",
                product_url="https://item.rakuten.co.jp/other/item-a",
                affiliate_url=secondary_affiliate_url,
                price=None,
                shipping_fee=Decimal("0"),
                commission_rate=0.0,
                commission_base_notes="商品本体のみ",
            ),
        )
        save_evidence(
            session,
            research_a.id,
            EvidenceInput(
                summary="公式仕様で幅20cmと確認",
                source_url="https://maker.example/a/spec",
                source_type=SourceType.MANUFACTURER,
                verification_status=VerificationStatus.CONFIRMED_FACT,
                checked_at=datetime(2026, 9, 9, 10, 0, tzinfo=JST),
            ),
        )
        save_evidence(
            session,
            research_b.id,
            EvidenceInput(
                summary="レビューから静かそうだと推測",
                source_url="https://review.example/b",
                source_type=SourceType.THIRD_PARTY,
                verification_status=VerificationStatus.HYPOTHESIS,
                checked_at=datetime(2026, 9, 9, 10, 0, tzinfo=JST),
            ),
        )
        plan = save_research_plan(
            session,
            ResearchPlanInput(
                title="国内向け2商品の根拠比較",
                audience="狭い住居で使う商品を選びたい人",
                pain_point="寸法と費用を同じ基準で比較できない",
                article_purpose="確認済み情報を分けて選択を助ける",
                comparison_axes=["設置面積", "手入れ"],
                evaluation={
                    "読者の悩みとの一致": {
                        "memo": "狭い場所の比較に合う",
                        "basis": "登録した実寸情報",
                    }
                },
                adoption_reason="型番ごとの寸法差を説明できる",
                weaknesses="送料と報酬率が未確認",
                additional_checks="公開直前に販売条件を再確認",
                status="ready",
            ),
            {
                research_a.id: {"設置面積": "幅20cm", "手入れ": "判定材料不足"},
                research_b.id: {"設置面積": "幅25cm", "手入れ": "水洗い"},
            },
        )
        session.commit()
        plan_id = plan.id
        research_a_id = research_a.id

    with Session(engine) as session:
        offer = session.scalar(
            select(ResearchOffer).where(ResearchOffer.product_id == research_a_id)
        )
        assert offer is not None
        offer.price = Decimal("9999")
        session.commit()

    Base.metadata.create_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_schema_version(session)
        assert session.get(Content, old_content_id).draft_body == "既存本文"  # type: ignore[union-attr]
        plan = get_research_plan(session, plan_id)
        assert plan is not None
        assert plan.version == 1
        assert len(plan.revisions) == 1
        revision = session.scalar(
            select(ResearchPlanRevision).where(ResearchPlanRevision.plan_id == plan_id)
        )
        assert revision is not None
        first_snapshot = plan.product_links[0].product_snapshot_json
        assert Decimal(first_snapshot["offers"][0]["price"]) == Decimal("1000")
        assert first_snapshot["offers"][0]["affiliate_url"] == exact_affiliate_url

        brief_json, markdown = build_article_brief(plan)
        assert exact_affiliate_url in markdown
        assert secondary_affiliate_url in markdown
        assert "公式仕様で幅20cmと確認" in markdown
        assert "レビューから静かそうだと推測" in markdown
        assert "判定材料不足" in markdown
        assert "記事生成済みを意味しない" in markdown
        assert brief_json["automatic_posting"] is False
        brief = save_article_brief(
            session,
            plan_id=plan.id,
            title=plan.title,
            markdown=markdown,
            brief_json=brief_json,
            disclosure_policy=brief_json["disclosure_policy"],
        )
        with pytest.raises(ValueError, match="一致確認"):
            brief_handoff_payload(brief, channel="Pinterest")

        save_published_article_reference(
            session,
            brief.id,
            url="https://note.com/example/n/n123?from=affiliate",
            title="実際に公開した比較記事",
            summary="候補Aと候補Bの違いを、確認済み情報で比較した記事。",
            match_confirmed=True,
        )
        pinterest_payload = brief_handoff_payload(brief, channel="Pinterest")
        assert pinterest_payload["published_article_url"].endswith("?from=affiliate")
        assert pinterest_payload["theme"] == "実際に公開した比較記事"

        content = create_content(
            session,
            channel="Pinterest",
            theme=pinterest_payload["theme"],
            title="Pinterest下書き",
            body="自動投稿しない下書き",
            product_ids=pinterest_payload["product_ids"],
            affiliate_url=exact_affiliate_url,
            pr_required=True,
            compliance_status="要確認",
            compliance_report={},
            info_verified_at=None,
        )
        link_brief_to_content(
            session,
            brief_id=brief.id,
            content_id=content.id,
            channel="Pinterest",
        )
        session.commit()
        links = list_content_research_links(session, content.id)
        assert len(links) == 1
        assert links[0].brief.brief_code == brief.brief_code
        assert links[0].brief.plan.plan_code == plan.plan_code


def test_external_evidence_requires_a_source_but_measurement_does_not() -> None:
    checked_at = datetime(2026, 9, 9, 12, 0, tzinfo=JST)
    with pytest.raises(ValueError, match="出典URL"):
        EvidenceInput(
            summary="販売店の説明を見た",
            source_type=SourceType.RETAILER,
            verification_status=VerificationStatus.UNVERIFIED,
            checked_at=checked_at,
        )

    measured = EvidenceInput(
        summary="重量を自分で測った",
        source_type=SourceType.OWN_MEASUREMENT,
        verification_status=VerificationStatus.CONFIRMED_FACT,
        checked_at=checked_at,
        actually_used=True,
        use_conditions="家庭用の秤を使用",
        observations="本体のみで500gだった",
    )
    assert measured.source_url == ""

"""SKU 匹配封頂結論置信度。價保回傳排除條款，不回傳天數。"""

from prism.commerce.money import unit
from prism.commerce.protection import judge_protection
from prism.commerce.sku_match import SkuTraits, align, mark_match

HIGH = unit("0.85")
MEDIUM = unit("0.65")


def test_code_conflict_cannot_be_rescued_by_title():
    anchor = SkuTraits(code="DEMO-SKU-01", title="示範標準化商品", brand="北境")
    observed = SkuTraits(code="OTHER-SKU", title="示範標準化商品", brand="北境")
    mark = align(anchor, observed, high=HIGH, medium=MEDIUM)
    assert mark.confidence == unit("0")
    assert mark.band == "low"
    assert mark.cap == unit("0")
    assert mark.limits_conclusion is True
    assert "標準碼衝突" in mark.reasons[0]


def test_brand_and_model_reach_high_but_title_alone_does_not():
    anchor = SkuTraits(title="示範標準化商品", brand="北境", model="NB-1")
    matched = align(
        anchor,
        SkuTraits(title="別的標題", brand="北境", model="NB-1"),
        high=HIGH,
        medium=MEDIUM,
    )
    title_only = align(anchor, SkuTraits(title="示範標準化商品"), high=HIGH, medium=MEDIUM)
    partial = align(
        anchor,
        SkuTraits(title="示範標準化商品", brand="北境"),
        high=HIGH,
        medium=MEDIUM,
    )
    assert matched.band == "high"
    assert matched.confidence == unit("0.85")
    assert title_only.band == "low"
    assert partial.band == "medium"
    assert partial.confidence == unit("0.70")


def test_fallback_lowers_the_conclusion_cap():
    mark = mark_match(unit("1"), high=HIGH, medium=MEDIUM, acquisition_mode="fallback")
    assert mark.confidence == unit("1")
    assert mark.cap == unit("0.90")


def test_protection_reports_exclusions_without_a_day_count():
    subsidy = judge_protection(page_list_price_dropped=True, cause="national_subsidy")
    unparsed = judge_protection(page_list_price_dropped=True, cause="unparsed")
    page = judge_protection(page_list_price_dropped=True, cause="page_list_price")
    assert subsidy.applies is False
    assert subsidy.matched_exclusions == ("national_subsidy",)
    assert unparsed.applies is False
    assert unparsed.unparsed is True
    assert page.applies is True
    assert page.matched_exclusions == ()
    for verdict in (subsidy, unparsed, page):
        assert "days" not in verdict.to_dict()
        assert "天" not in verdict.detail
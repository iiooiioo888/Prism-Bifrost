"""同一身份才對照實付。規格不同只列單位價格。"""

from decimal import Decimal

from prism.commerce.compare import Quote, compare_quotes
from prism.commerce.identity import parse_traits
from prism.commerce.money import unit


def _quote(**kwargs: object) -> Quote:
    title = str(kwargs.get("title") or "品牌：虎牌 型號：MMP-T 500ml 黑色 保溫杯")
    base = dict(
        platform="tmall",
        label="天貓",
        source_url="https://detail.tmall.com/item.htm?id=1",
        title=title,
        sku_code="id:same",
        list_price=unit("100"),
        price_basis="guest",
        match_band="high",
        match_confidence=unit("0.96"),
        traits=parse_traits(title),
    )
    base.update(kwargs)
    return Quote(**base)  # type: ignore[arg-type]


def test_same_identity_reports_gap_and_leaves_shipping_out_when_unpublished():
    payload = compare_quotes(
        [
            _quote(platform="tmall", label="天貓", list_price=unit("128"), source_url="https://detail.tmall.com/item.htm?id=1"),
            _quote(
                platform="taobao",
                label="淘寶",
                list_price=unit("80"),
                shipping_price=unit("6"),
                source_url="https://item.taobao.com/item.htm?id=2",
            ),
        ]
    )
    group = payload["same_identity"][0]
    assert group["comparable_count"] == 2
    assert group["low_platform"] == "淘寶"
    assert group["low_payable"] == "86.00"
    assert group["spread"] == "42.00"
    by_platform = {row["platform"]: row for row in group["rows"]}
    assert by_platform["tmall"]["gap"] == "42.00"
    assert by_platform["tmall"]["shipping_known"] is False
    assert by_platform["taobao"]["shipping"] == "6.00"
    assert "不含運費" in group["statement"]
    assert payload["related_specs"] == []


def test_outlier_and_out_of_stock_stay_reviewable_without_a_lowest_price_claim():
    payload = compare_quotes(
        [
            _quote(platform="tmall", label="天貓", list_price=unit("100"), source_url="https://detail.tmall.com/item.htm?id=1"),
            _quote(platform="taobao", label="淘寶", list_price=unit("100"), source_url="https://item.taobao.com/item.htm?id=2"),
            _quote(platform="jd", label="京東", list_price=unit("40"), source_url="https://item.jd.com/3.html"),
            _quote(
                platform="pinduoduo",
                label="拼多多",
                list_price=unit("20"),
                availability="outofstock",
                source_url="https://mobile.yangkeduo.com/goods.html?goods_id=4",
            ),
        ]
    )
    group = payload["same_identity"][0]
    assert group["comparable_count"] == 3
    assert group["low_payable"] == "40.00"
    assert group["review"]
    assert "複核" in group["review"]
    held = next(row for row in group["rows"] if row["platform"] == "pinduoduo")
    assert held["comparable"] is False
    assert "缺貨" in held["reason"]
    rendered = group["statement"] + (group["review"] or "")
    for banned in ("最便宜", "最優", "全網最低", "實時", "全自動"):
        assert banned not in rendered


def test_different_capacity_is_split_and_shown_as_unit_price():
    large = "品牌：虎牌 型號：MMP-T 500ml 黑色 保溫杯"
    small = "品牌：虎牌 型號：MMP-T 350ml 黑色 保溫杯"
    payload = compare_quotes(
        [
            _quote(title=large, sku_code="id:500", list_price=unit("100"), traits=parse_traits(large)),
            _quote(
                title=small,
                sku_code="id:350",
                platform="jd",
                label="京東",
                list_price=unit("70"),
                match_band="low",
                match_confidence=unit("0"),
                source_url="https://item.jd.com/9.html",
                traits=parse_traits(small),
            ),
        ]
    )
    assert [group["comparable_count"] for group in payload["same_identity"]] == [1, 0]
    board = payload["related_specs"][0]
    assert board["brand"] == "虎牌"
    assert "不併入" in board["note"]
    by_capacity = {row["capacity"]: row for row in board["rows"]}
    assert by_capacity["500ml"]["unit_price"] == "0.2000"
    assert by_capacity["500ml"]["unit"] == "ml"
    assert by_capacity["350ml"]["sku_code"] == "id:350"
    assert by_capacity["350ml"]["payable"] == "70.00"


def test_stable_baseline_is_cited_beside_the_list_price():
    payload = compare_quotes(
        [_quote(list_price=unit("80"))],
        baselines={"id:same": (14, Decimal("100.00"))},
        baseline_required=14,
    )
    row = payload["same_identity"][0]["rows"][0]
    assert row["baseline"] == "標價低於 14 日基線中位數 100.00 共 20.00"
    short = compare_quotes(
        [_quote(list_price=unit("80"))],
        baselines={"id:same": (3, Decimal("100.00"))},
        baseline_required=14,
    )
    assert short["same_identity"][0]["rows"][0]["baseline"] is None

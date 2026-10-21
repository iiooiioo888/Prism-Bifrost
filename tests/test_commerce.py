"""雙11規則、棄權分級、模型金額禁令與劣化速率。"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from prism.commerce.baseline import daily_baseline
from prism.commerce.decide import OfferInput, decide_now
from prism.commerce.money import unit
from prism.commerce.rules import Adjustment, RuleOrderError, price_protection, solve
from prism.commerce.service import CommerceService
from prism.db.models import CanonicalEvent, Sku, SkuOffer
from prism.db.session import session_scope
from prism.demo.commerce import SKU_CODE, seed_commerce
from prism.demo.seed import seed
from prism.llm.gateway import ModelGateway
from prism.raven.base import Probe, RawItem
from prism.raven.degrade import failure_acceleration, should_preempt_fallback
from prism.raven.router import Backend, ProbeRouter
from prism.scenes.registry import ComplianceError, SceneConfigError, load_scenes, refuse_hostile_collection


WEIGHTS = {
    "payable": unit("0.40"),
    "price_protection": unit("0.25"),
    "inventory_reliability": unit("0.20"),
    "after_sales": unit("0.15"),
}


def _offer(**kwargs: object) -> OfferInput:
    base = dict(
        offer_id="x",
        platform="店",
        source_url="https://example.test/item",
        list_price=unit("100"),
        price_basis="guest",
        match_confidence=unit("0.90"),
        page_list_price_dropped=False,
        protection_cause="",
        inventory_reliability=unit("0.5"),
        after_sales=unit("0.5"),
        adjustments=(),
        acquisition_mode="live",
    )
    base.update(kwargs)
    return OfferInput(**base)  # type: ignore[arg-type]


def _sample_offers() -> list[OfferInput]:
    offer_a = _offer(
        offer_id="a",
        platform="甲店",
        source_url="https://shop-a.example/items/demo-sku-01",
        list_price=unit("200"),
        match_confidence=unit("0.96"),
        inventory_reliability=unit("0.2"),
        after_sales=unit("0.3"),
        adjustments=(
            Adjustment("item", "percent", unit("0.05"), "單品折扣"),
            Adjustment("store", "amount", unit("10"), "店鋪券"),
        ),
    )
    offer_b = _offer(
        offer_id="b",
        platform="乙店",
        source_url="https://shop-b.example/items/demo-sku-01",
        list_price=unit("250"),
        match_confidence=unit("0.92"),
        page_list_price_dropped=True,
        protection_cause="page_list_price",
        inventory_reliability=unit("0.95"),
        after_sales=unit("0.9"),
        adjustments=(
            Adjustment("item", "amount", unit("20"), "單品券"),
            Adjustment("platform", "amount", unit("10"), "平台券"),
            Adjustment("national_subsidy", "percent", unit("0.10"), "國補"),
        ),
    )
    offer_c = _offer(
        offer_id="c",
        platform="丙店",
        source_url="https://shop-c.example/items/demo-sku-01",
        list_price=None,
        price_basis="logged_in",
        match_confidence=unit("0.60"),
        protection_cause="logged_in_price",
    )
    return [offer_a, offer_b, offer_c]


def test_illegal_coupon_order_is_rejected_without_reordering():
    adjustments = [
        Adjustment("item", "amount", unit("20"), "單品券"),
        Adjustment("national_subsidy", "percent", unit("0.10"), "國補"),
    ]
    with pytest.raises(RuleOrderError):
        solve(unit("250"), adjustments, declared_order=["national_subsidy", "item"])


def test_national_subsidy_uses_residual_payable():
    result = solve(
        unit("250"),
        [
            Adjustment("item", "amount", unit("20"), "單品券"),
            Adjustment("platform", "amount", unit("10"), "平台券"),
            Adjustment("national_subsidy", "percent", unit("0.10"), "國補"),
        ],
        declared_order=["item", "store", "platform", "red_packet", "national_subsidy"],
    )
    assert result.payable == Decimal("198.00")
    national = next(step for step in result.steps if step.layer == "national_subsidy")
    assert national.base_before == Decimal("220.00")
    assert national.deduction == Decimal("22.00")


def test_mutex_keeps_higher_priority_even_when_smaller():
    adjustments = [
        Adjustment("store", "amount", unit("5"), "小券", exclusive_group="store-coupon", priority=2),
        Adjustment("store", "amount", unit("30"), "大券", exclusive_group="store-coupon", priority=1),
    ]
    result = solve(unit("100"), adjustments)
    assert result.payable == Decimal("95.00")
    assert len(result.steps) == 1
    assert result.steps[0].label == "小券"


def test_mutex_tie_breaks_on_larger_benefit():
    adjustments = [
        Adjustment("store", "amount", unit("5"), "小券", exclusive_group="store-coupon", priority=1),
        Adjustment("store", "amount", unit("30"), "大券", exclusive_group="store-coupon", priority=1),
    ]
    result = solve(unit("100"), adjustments)
    assert result.payable == Decimal("70.00")
    assert result.steps[0].label == "大券"


def test_price_protection_is_page_list_price_only():
    assert price_protection(page_list_price_dropped=True, cause="page_list_price") is True
    assert price_protection(page_list_price_dropped=False, cause="page_list_price") is False
    assert price_protection(page_list_price_dropped=True, cause="national_subsidy") is False
    assert price_protection(page_list_price_dropped=True, cause="billion_subsidy") is False


def test_ranking_prefers_protected_offer_over_lower_payable():
    payload = decide_now(
        offers=_sample_offers(),
        node="onsale",
        scene_id="double11",
        sku_code=SKU_CODE,
        sku_title="示範標準化商品",
        weights=WEIGHTS,
        baseline_days=3,
        baseline_required=14,
        match_high=unit("0.85"),
        match_medium=unit("0.65"),
    )
    assert payload["ranking"][0]["platform"] == "乙店"
    assert payload["ranking"][0]["payable"] == "198.00"
    assert payload["ranking"][0]["score"] == "0.575"
    assert payload["sku_match"]["limits_conclusion"] is True
    assert payload["sku_match"]["conclusion_confidence_cap"] == "0.920"
    assert payload["ranking"][0]["match_cap"] == "0.920"
    assert payload["ranking"][0]["price_protection_verdict"]["applies"] is True
    assert payload["ranking"][0]["price_protection_verdict"]["matched_exclusions"] == []
    assert "days" not in payload["ranking"][0]["price_protection_verdict"]
    assert payload["abstain"]["executable"][0]["confidence_cap"] == "0.920"
    assert payload["ranking"][1]["platform"] == "甲店"
    assert payload["ranking"][1]["payable"] == "180.00"
    assert payload["ranking"][1]["score"] == "0.485"
    assert payload["ranking"][0]["steps"][-1]["base_before"] == "220.00"
    assert "丙店" not in {item["platform"] for item in payload["ranking"]}
    joined = " ".join(item["statement"] for item in payload["abstain"]["executable"])
    assert "198.00" in joined
    assert "最便宜" not in joined
    assert "最優" not in joined
    assert any("未滿 14 日" in line for line in payload["abstain"]["confirmations"])
    assert any("登入態" in line for line in payload["abstain"]["risks"])
    assert "模型生成貨幣金額" in payload["abstain"]["prohibited"]
    assert payload["fast_path"]["cadence"] == "秒級到分鐘級"


def test_scene_config_rejects_reordered_subsidy_and_hostile_collection():
    document = {
        "scenes": {
            "bad": {
                "title": "錯誤順序",
                "fusion": "sku_offer",
                "retention_days": 1,
                "dimensions": {
                    "subjects": ["SKU"],
                    "signal_domains": ["public_price_page"],
                    "triggers": ["onsale"],
                    "delivery": ["reviewable_conclusion"],
                    "time_sensitivity": "fast_and_slow",
                },
                "nodes": ["onsale"],
                "price_rule_order": ["national_subsidy", "item", "store", "platform", "red_packet"],
                "ranking_weights": {
                    "payable": "0.40",
                    "price_protection": "0.25",
                    "inventory_reliability": "0.20",
                    "after_sales": "0.15",
                },
                "baseline_days_required": 14,
                "sku_match": {"high": "0.85", "medium": "0.65"},
                "compliance": {
                    "login_reuse": False,
                    "fingerprint_simulation": False,
                    "risk_control_evasion": False,
                    "captcha_bypass": False,
                    "login_wall_bypass": False,
                    "public_visible_is_not_permission": True,
                },
            }
        }
    }
    with pytest.raises(SceneConfigError):
        load_scenes(document)
    with pytest.raises(ComplianceError):
        refuse_hostile_collection(login_reuse=True)


def test_loaded_scenes_keep_pipelines_shared(runtime):
    scenes = runtime.settings.scenes
    assert scenes["harbor_watch"].fusion == "canonical_event"
    assert scenes["double11"].fusion == "sku_offer"
    assert scenes["double11"].price_rule_order[-1] == "national_subsidy"
    assert scenes["double11"].compliance["captcha_bypass"] is False
    assert scenes["double11"].compliance["public_visible_is_not_permission"] is True


def test_offers_do_not_create_canonical_events(runtime):
    now = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)
    with session_scope(runtime.factory) as session:
        before = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
    seed_commerce(runtime, now)
    with session_scope(runtime.factory) as session:
        after = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
    assert before == after == 0
    service = CommerceService(runtime)
    with pytest.raises(ComplianceError):
        service.record_offer(
            sku_code=SKU_CODE,
            sku_title="示範標準化商品",
            scene_id="double11",
            platform="丁店",
            source_url="https://shop-d.example/items/demo-sku-01",
            upstream_timestamp=now,
            collected_at=now,
            acquisition_mode="live",
            list_price=Decimal("90"),
            price_basis="logged_in",
            match_confidence=Decimal("0.99"),
            page_list_price_dropped=False,
            protection_cause="",
            inventory_reliability=Decimal("0.4"),
            after_sales=Decimal("0.4"),
            adjustments=[],
        )
    payload = service.decide(scene_id="double11", sku_code=SKU_CODE, node="onsale", path="slow")
    assert payload["slow_path"]["baseline_ready"] is False
    assert payload["confidence_reduced"] is True


def test_model_gateway_rejects_money_without_echo():
    calls = {"n": 0}

    def complete(_: dict) -> str:
        calls["n"] += 1
        return json.dumps({"explanation": "實付只要 12 元", "drivers": ["m1"], "speculative": False})

    gateway = ModelGateway()
    prompt = {"task": "explain"}
    first = gateway.complete_json("explain", prompt, complete, known_mention_ids={"m1"})
    assert first.status == "rejected"
    assert first.payload["prohibited"] is True
    rendered = json.dumps(first.payload, ensure_ascii=False)
    assert "12" not in rendered
    assert "元" not in rendered
    second = gateway.complete_json("explain", prompt, complete, known_mention_ids={"m1"})
    assert second.status == "rejected"
    assert second.cached is True
    assert calls["n"] == 1

    def clean(_: dict) -> str:
        return json.dumps({"explanation": "提及增加但未量化", "drivers": ["m1"], "speculative": False})

    kept = gateway.complete_json("explain", {"task": "clean"}, clean, known_mention_ids={"m1"})
    assert kept.status == "ok"
    assert "提及增加" in kept.payload["explanation"]


def test_degradation_preempts_only_when_failure_accelerates():
    assert failure_acceleration([False, False, False, False]) == 0
    assert should_preempt_fallback([False, False, False, False]) is False
    assert should_preempt_fallback([True, True, False, False]) is True
    assert failure_acceleration([True]) is None

    called = {"primary": False}

    class Primary(Probe):
        platform = "web"
        probe_id = "primary"

        async def fetch(self, query_or_feed: str, since: datetime, limit: int = 100) -> list[RawItem]:
            called["primary"] = True
            raise RuntimeError("不該呼叫首選")

        async def health(self):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    class Secondary(Probe):
        platform = "web"
        probe_id = "secondary"

        async def fetch(self, query_or_feed: str, since: datetime, limit: int = 100) -> list[RawItem]:
            now = datetime(2026, 10, 21, tzinfo=timezone.utc)
            return [
                RawItem(
                    source_url="https://example.test/a",
                    upstream_timestamp=now,
                    collected_at=now,
                    platform="web",
                    title="標題",
                    body="正文",
                    author=None,
                    language="zh",
                    acquisition_mode="live",
                    raw_ref="r",
                    probe_id="secondary",
                    content_hash="h",
                )
            ]

        async def health(self):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    router = ProbeRouter(
        [Backend(Primary(), 0, "primary"), Backend(Secondary(), 1, "secondary")]
    )
    result = asyncio.run(
        router.fetch("q", datetime(2026, 10, 21, tzinfo=timezone.utc), preempt_fallback=True)
    )
    assert called["primary"] is False
    assert result.acquisition_mode == "fallback"
    assert result.attempts[0].error == "劣化速率上升，提前啟用備選"
    assert result.backend_id == "secondary"


def test_scene_api_exposes_reviewable_decision(runtime, client):
    now = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)
    seed(runtime, now)
    headers = {"Authorization": "Bearer dev-analyst"}
    scenes = client.get("/api/v1/scenes", headers=headers)
    assert scenes.status_code == 200
    ids = {item["scene_id"] for item in scenes.json()["items"]}
    assert ids == {"harbor_watch", "double11"}
    decisions = client.get("/api/v1/scenes/double11/decisions", headers=headers)
    assert decisions.status_code == 200
    payload = decisions.json()["items"][0]["payload"]
    assert payload["ranking"][0]["platform"] == "乙店"
    assert payload["ranking"][0]["payable"] == "198.00"
    assert payload["baseline_median"] == "200.00"
    assert payload["baseline_median_provisional"] is True
    assert any("中位數" in line for line in payload["abstain"]["confirmations"])
    probes = client.get("/api/v1/health/probes", headers=headers).json()["items"]
    rss = next(item for item in probes if item["probe_id"] == "rss")
    assert rss["status"] == "healthy"
    assert rss["degradation_rate"] == pytest.approx(0.25)
    assert rss["preempt_fallback"] is True
    jina = next(item for item in probes if item["probe_id"] == "jina_reader")
    assert "degradation_rate" in jina
    assert "preempt_fallback" in jina
    reddit = next(item for item in probes if item["probe_id"] == "reddit")
    assert reddit["degradation_rate"] is None
    assert reddit["preempt_fallback"] is False
    slow = client.post(
        "/api/v1/scenes/double11/decide",
        json={"sku_code": SKU_CODE, "node": "presale", "path": "slow"},
        headers=headers,
    )
    assert slow.status_code == 200
    assert slow.json()["path"] == "slow"
    viewer = client.post(
        "/api/v1/scenes/double11/decide",
        json={"sku_code": SKU_CODE, "node": "onsale", "path": "fast"},
        headers={"Authorization": "Bearer dev-viewer"},
    )
    assert viewer.status_code == 403
    with session_scope(runtime.factory) as session:
        events = int(session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0)
    assert events > 0


def test_unknown_subsidy_is_left_unapplied():
    result = solve(
        unit("250"),
        [
            Adjustment("item", "amount", unit("20"), "單品券"),
            Adjustment("national_subsidy", "percent", unit("0.10"), "國補", eligibility="unknown"),
        ],
    )
    assert result.payable == Decimal("230.00")
    assert [item.label for item in result.withheld] == ["國補"]


def test_unparsed_price_protection_is_not_scored():
    payload = decide_now(
        offers=[
            _offer(
                offer_id="p",
                platform="甲店",
                list_price=unit("100"),
                page_list_price_dropped=True,
                protection_cause="unparsed",
                match_confidence=unit("0.96"),
                inventory_reliability=unit("0.2"),
                after_sales=unit("0.3"),
            )
        ],
        node="onsale",
        scene_id="double11",
        sku_code=SKU_CODE,
        sku_title="示範標準化商品",
        weights=WEIGHTS,
        baseline_days=3,
        baseline_required=14,
        match_high=unit("0.85"),
        match_medium=unit("0.65"),
    )
    assert payload["ranking"][0]["price_protection"] is False
    assert payload["ranking"][0]["score"] == "0.485"
    assert any("未能完整解析" in line for line in payload["uncertainties"])


def test_daily_price_median_groups_each_day_first():
    days, median = daily_baseline(
        [
            (date(2026, 10, 19), unit("10")),
            (date(2026, 10, 19), unit("30")),
            (date(2026, 10, 20), unit("40")),
        ]
    )
    assert days == 2
    assert median == Decimal("30.00")


def test_ingest_ignores_optional_price_fields(runtime):
    now = datetime(2026, 10, 21, 9, tzinfo=timezone.utc)
    runtime.ingest_items(
        [
            RawItem(
                source_url="https://port-authority.example/notices/price-field",
                upstream_timestamp=now,
                collected_at=now,
                platform="rss",
                title="一則公告",
                body="北境港務局發布與價格欄位無關的公開公告。",
                author="北境港務局",
                language="zh",
                acquisition_mode="live",
                raw_ref="raw-price-field",
                probe_id="rss",
                content_hash="hash-price-field",
                sku_code="SHOULD-NOT-CREATE",
                platform_listing_id="L1",
                coupon_stack=("national_subsidy",),
                price_valid_until="2026-11-11",
                price_protection_covers="unknown",
                observed_list_price="10.00",
            )
        ],
        now=now,
    )
    with session_scope(runtime.factory) as session:
        assert int(session.scalar(select(func.count()).select_from(Sku)) or 0) == 0
        assert int(session.scalar(select(func.count()).select_from(SkuOffer)) or 0) == 0

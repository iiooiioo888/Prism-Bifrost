"""場景五維度與合規登記。電商採集預設拒絕登入態、指紋與風控對抗。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

CANONICAL_PRICE_ORDER = ("item", "store", "platform", "red_packet", "national_subsidy")
FORBIDDEN_COLLECTION = (
    "login_reuse",
    "fingerprint_simulation",
    "risk_control_evasion",
    "captcha_bypass",
    "login_wall_bypass",
)
REQUIRED_WEIGHTS = ("payable", "price_protection", "inventory_reliability", "after_sales")


class SceneConfigError(ValueError):
    """場景配置違反基線。"""


class ComplianceError(ValueError):
    """採集方式未被允許。"""


@dataclass(frozen=True)
class Scene:
    scene_id: str
    title: str
    fusion: str
    subjects: tuple[str, ...]
    signal_domains: tuple[str, ...]
    triggers: tuple[str, ...]
    delivery: tuple[str, ...]
    time_sensitivity: str
    compliance: Mapping[str, bool]
    retention_days: int
    nodes: tuple[str, ...] = ()
    price_rule_order: tuple[str, ...] = ()
    ranking_weights: Mapping[str, str] | None = None
    baseline_days_required: int = 0
    sku_match_high: str = "0.85"
    sku_match_medium: str = "0.65"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "title": self.title,
            "fusion": self.fusion,
            "dimensions": {
                "subjects": list(self.subjects),
                "signal_domains": list(self.signal_domains),
                "triggers": list(self.triggers),
                "delivery": list(self.delivery),
                "time_sensitivity": self.time_sensitivity,
            },
            "compliance": dict(self.compliance),
            "retention_days": self.retention_days,
            "nodes": list(self.nodes),
            "price_rule_order": list(self.price_rule_order),
            "ranking_weights": None if self.ranking_weights is None else dict(self.ranking_weights),
            "baseline_days_required": self.baseline_days_required,
            "sku_match": {"high": self.sku_match_high, "medium": self.sku_match_medium},
        }


def refuse_hostile_collection(**flags: bool) -> None:
    """登入態復用、指紋模擬、風控對抗、驗證碼與登入牆繞過一律拒絕。"""
    hit = [name for name in FORBIDDEN_COLLECTION if flags.get(name)]
    if hit:
        raise ComplianceError("此採集方式必須逐場景登記且預設拒絕：" + "、".join(hit))


def load_scenes(document: Mapping[str, Any]) -> dict[str, Scene]:
    raw_scenes = document.get("scenes", {})
    if not isinstance(raw_scenes, Mapping):
        raise SceneConfigError("scenes 必須是映射")
    loaded: dict[str, Scene] = {}
    for scene_id, spec in raw_scenes.items():
        if not isinstance(spec, Mapping):
            raise SceneConfigError(f"場景 {scene_id} 必須是映射")
        loaded[str(scene_id)] = _scene(str(scene_id), spec)
    return loaded


def _scene(scene_id: str, spec: Mapping[str, Any]) -> Scene:
    fusion = str(spec.get("fusion") or "")
    if fusion not in {"canonical_event", "sku_offer"}:
        raise SceneConfigError(f"{scene_id} 的融合方式必須是 canonical_event 或 sku_offer")
    dimensions = spec.get("dimensions") or {}
    if not isinstance(dimensions, Mapping):
        raise SceneConfigError(f"{scene_id} 缺少五維度")
    subjects = _tuple_of(dimensions.get("subjects"), f"{scene_id} 關注對象")
    domains = _tuple_of(dimensions.get("signal_domains"), f"{scene_id} 訊號域")
    triggers = _tuple_of(dimensions.get("triggers"), f"{scene_id} 觸發條件")
    delivery = _tuple_of(dimensions.get("delivery"), f"{scene_id} 交付終點")
    sensitivity = str(dimensions.get("time_sensitivity") or "")
    if not subjects or not domains or not triggers or not delivery or not sensitivity:
        raise SceneConfigError(f"{scene_id} 的五維度都必須寫明")
    compliance = _compliance(scene_id, spec.get("compliance"))
    order = tuple(str(item) for item in spec.get("price_rule_order") or [])
    weights = spec.get("ranking_weights")
    nodes = tuple(str(item) for item in spec.get("nodes") or [])
    sku_match = spec.get("sku_match") or {}
    high = "0.85"
    medium = "0.65"
    weight_map: dict[str, str] | None = None
    baseline = int(spec.get("baseline_days_required") or 0)
    if fusion == "sku_offer":
        if order != CANONICAL_PRICE_ORDER:
            raise SceneConfigError(
                f"{scene_id} 的優惠順序必須是單品級、店鋪級、平台級、紅包、國補，國補固定在最後"
            )
        if not isinstance(weights, Mapping):
            raise SceneConfigError(f"{scene_id} 缺少排序權重")
        weight_map = {key: str(weights[key]) for key in REQUIRED_WEIGHTS}
        total = sum((Decimal(weight_map[key]) for key in REQUIRED_WEIGHTS), Decimal("0"))
        if total != Decimal("1.00") and total != Decimal("1"):
            raise SceneConfigError(f"{scene_id} 的排序權重之和必須為 1")
        if not isinstance(sku_match, Mapping):
            raise SceneConfigError(f"{scene_id} 缺少 SKU 匹配置信度分檔")
        high = str(sku_match.get("high") or "")
        medium = str(sku_match.get("medium") or "")
        if Decimal(high) <= Decimal(medium):
            raise SceneConfigError(f"{scene_id} 的高檔門檻必須高於中檔")
        if baseline < 1:
            raise SceneConfigError(f"{scene_id} 必須聲明歷史價格基線天數")
        if not nodes:
            raise SceneConfigError(f"{scene_id} 必須聲明驗收節點")
    return Scene(
        scene_id=scene_id,
        title=str(spec.get("title") or scene_id),
        fusion=fusion,
        subjects=subjects,
        signal_domains=domains,
        triggers=triggers,
        delivery=delivery,
        time_sensitivity=sensitivity,
        compliance=compliance,
        retention_days=int(spec.get("retention_days") or 0),
        nodes=nodes,
        price_rule_order=order,
        ranking_weights=weight_map,
        baseline_days_required=baseline,
        sku_match_high=high,
        sku_match_medium=medium,
    )


def _compliance(scene_id: str, raw: Any) -> dict[str, bool]:
    if not isinstance(raw, Mapping):
        raise SceneConfigError(f"{scene_id} 缺少合規登記")
    flags = {name: raw.get(name) for name in (*FORBIDDEN_COLLECTION, "public_visible_is_not_permission")}
    for name in FORBIDDEN_COLLECTION:
        if flags[name] is not False:
            raise SceneConfigError(f"{scene_id} 的 {name} 必須明確設為 false")
    if flags["public_visible_is_not_permission"] is not True:
        raise SceneConfigError(f"{scene_id} 必須聲明公開可見不等於允許採集")
    refuse_hostile_collection(**{name: bool(flags[name]) for name in FORBIDDEN_COLLECTION})
    return {name: bool(flags[name]) for name in flags}


def _tuple_of(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SceneConfigError(f"{label}必須是非空列表")
    return tuple(str(item) for item in value)

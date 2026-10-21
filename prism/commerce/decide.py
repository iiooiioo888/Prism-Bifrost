"""快路徑做規則與排序，慢路徑維護基線。模型不參與金額。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from prism.commerce.money import money, money_text, score_text, unit
from prism.commerce.rules import Adjustment, price_protection, match_band, solve

PROHIBITED_STATEMENTS = (
    "最便宜",
    "最優",
    "全網最低",
    "實時",
    "全自動",
    "模型生成貨幣金額",
)


@dataclass(frozen=True)
class OfferInput:
    offer_id: str
    platform: str
    source_url: str
    list_price: Decimal | None
    price_basis: str
    match_confidence: Decimal
    page_list_price_dropped: bool
    protection_cause: str
    inventory_reliability: Decimal
    after_sales: Decimal
    adjustments: tuple[Adjustment, ...]
    acquisition_mode: str = "live"
    valid_until: str | None = None


def decide_now(**kwargs: object) -> dict:
    return decide_offers(path="fast", **kwargs)  # type: ignore[arg-type]


def refresh_baseline(**kwargs: object) -> dict:
    return decide_offers(path="slow", **kwargs)  # type: ignore[arg-type]


def decide_offers(
    offers: list[OfferInput],
    *,
    path: str,
    node: str,
    scene_id: str,
    sku_code: str,
    sku_title: str,
    weights: dict[str, Decimal],
    baseline_days: int,
    baseline_required: int,
    match_high: Decimal,
    match_medium: Decimal,
) -> dict:
    if path not in {"fast", "slow"}:
        raise ValueError("路徑只能是 fast 或 slow")
    solved: list[dict] = []
    withheld: list[dict] = []
    for offer in offers:
        band = match_band(offer.match_confidence, high=match_high, medium=match_medium)
        comparable = offer.price_basis == "guest" and band != "low" and offer.list_price is not None
        if not comparable:
            withheld.append(_withheld(offer, band))
            continue
        assert offer.list_price is not None
        result = solve(offer.list_price, list(offer.adjustments))
        protected = price_protection(
            page_list_price_dropped=offer.page_list_price_dropped,
            cause=offer.protection_cause,
        )
        factor = unit("0.90") if offer.acquisition_mode == "fallback" else unit("1")
        solved.append(
            {
                "offer": offer,
                "band": band,
                "payable": result.payable,
                "steps": result.steps,
                "protected": protected,
                "cap": offer.match_confidence * factor,
            }
        )
    _assign_scores(solved, weights)
    solved.sort(key=lambda item: (-item["score"], item["payable"], item["offer"].platform))
    ranking = [_public_rank(item) for item in solved]
    abstain = _abstain(solved, withheld, baseline_days, baseline_required)
    _guard_statements(abstain["executable"])
    payload: dict = {
        "scene_id": scene_id,
        "sku_code": sku_code,
        "sku_title": sku_title,
        "node": node,
        "path": path,
        "label": "規則引擎產出，待人工確認",
        "baseline_days": baseline_days,
        "baseline_required": baseline_required,
        "confidence_reduced": baseline_days < baseline_required,
        "ranking": ranking,
        "withheld": withheld,
        "abstain": abstain,
        "price_rule_order": ["item", "store", "platform", "red_packet", "national_subsidy"],
    }
    if path == "fast":
        payload["fast_path"] = {"cadence": "秒級到分鐘級", "uses": ["規則引擎", "決策合成器"]}
    else:
        payload["slow_path"] = {
            "cadence": "小時級到天級",
            "maintains": ["歷史價格基線", "來源健康度"],
            "baseline_days": baseline_days,
            "baseline_ready": baseline_days >= baseline_required,
        }
    return payload


def _assign_scores(solved: list[dict], weights: dict[str, Decimal]) -> None:
    if not solved:
        return
    payables = [item["payable"] for item in solved]
    low = min(payables)
    high = max(payables)
    span = high - low
    for item in solved:
        payable_score = unit("1") if span == 0 else (high - item["payable"]) / span
        offer: OfferInput = item["offer"]
        raw = (
            payable_score * weights["payable"]
            + (unit("1") if item["protected"] else unit("0")) * weights["price_protection"]
            + offer.inventory_reliability * weights["inventory_reliability"]
            + offer.after_sales * weights["after_sales"]
        )
        item["score"] = min(raw, item["cap"])


def _public_rank(item: dict) -> dict:
    offer: OfferInput = item["offer"]
    reasons = [
        f"實付 {money_text(item['payable'])} 由規則引擎按固定層級算出",
        "頁面標價下降，價保成立" if item["protected"] else "價保不成立",
        f"庫存可靠性 {score_text(offer.inventory_reliability)}",
        f"售後可靠度 {score_text(offer.after_sales)}",
    ]
    if offer.valid_until:
        reasons.append(f"價格有效期至 {offer.valid_until}")
    reasons.append(f"價格基準為{ '遊客態' if offer.price_basis == 'guest' else offer.price_basis }")
    return {
        "offer_id": offer.offer_id,
        "platform": offer.platform,
        "source_url": offer.source_url,
        "list_price": money_text(offer.list_price or money("0")),
        "payable": money_text(item["payable"]),
        "score": score_text(item["score"]),
        "match_confidence": score_text(offer.match_confidence),
        "match_band": item["band"],
        "price_basis": offer.price_basis,
        "comparable": True,
        "price_protection": item["protected"],
        "acquisition_mode": offer.acquisition_mode,
        "reasons": reasons,
        "steps": [
            {
                "layer": step.layer,
                "label": step.label,
                "base_before": money_text(step.base_before),
                "deduction": money_text(step.deduction),
                "payable_after": money_text(step.payable_after),
            }
            for step in item["steps"]
        ],
    }


def _withheld(offer: OfferInput, band: str) -> dict:
    if offer.price_basis == "logged_in":
        reason = "登入態價格不可比，且系統不採集登入態價格"
    elif offer.list_price is None:
        reason = "缺少可核對的遊客態標價"
    else:
        reason = "SKU 匹配置信度為低檔，不進入排序"
    return {
        "offer_id": offer.offer_id,
        "platform": offer.platform,
        "source_url": offer.source_url,
        "price_basis": offer.price_basis,
        "match_confidence": score_text(offer.match_confidence),
        "match_band": band,
        "reason": reason,
    }


def _abstain(solved: list[dict], withheld: list[dict], baseline_days: int, baseline_required: int) -> dict:
    executable: list[dict] = []
    confirmations: list[str] = []
    risks: list[str] = []
    for item in solved:
        offer: OfferInput = item["offer"]
        statement = (
            f"{offer.platform} 實付 {money_text(item['payable'])}，"
            f"排序分數 {score_text(item['score'])}。"
            "價格來自規則引擎，名次同時計入價保、庫存可靠性與售後可靠度。"
        )
        entry = {
            "statement": statement,
            "offer_id": offer.offer_id,
            "payable": money_text(item["payable"]),
            "source_url": offer.source_url,
        }
        if item["band"] == "high":
            executable.append(entry)
        else:
            confirmations.append(
                f"{offer.platform} 的 SKU 匹配置信度為中檔，實付 {money_text(item['payable'])} 需人工確認後才可執行"
            )
    if solved:
        leader = solved[0]
        leader_offer: OfferInput = leader["offer"]
        others = "、".join(
            f"{item['offer'].platform} 實付 {money_text(item['payable'])}" for item in solved[1:]
        )
        ranking_statement = f"目前排序第一是 {leader_offer.platform}，實付 {money_text(leader['payable'])}。"
        if others:
            ranking_statement += f"其餘可比較方案：{others}。"
        ranking_statement += "名次由價保、庫存可靠性與售後可靠度共同決定。"
        if leader["band"] == "high":
            executable.insert(
                0,
                {
                    "statement": ranking_statement,
                    "offer_id": leader_offer.offer_id,
                    "payable": money_text(leader["payable"]),
                    "source_url": leader_offer.source_url,
                },
            )
        else:
            confirmations.insert(0, ranking_statement)
    if baseline_days < baseline_required:
        confirmations.append(
            f"歷史價格基線 {baseline_days} 日，未滿 {baseline_required} 日，結論置信度降低"
        )
    for item in withheld:
        risks.append(f"{item['platform']}：{item['reason']}")
    return {
        "executable": executable,
        "confirmations": confirmations,
        "risks": risks,
        "prohibited": list(PROHIBITED_STATEMENTS),
    }


def _guard_statements(executable: list[dict]) -> None:
    for entry in executable:
        for banned in PROHIBITED_STATEMENTS:
            if banned in entry["statement"]:
                raise RuntimeError("可執行結論含被禁止的聲明")

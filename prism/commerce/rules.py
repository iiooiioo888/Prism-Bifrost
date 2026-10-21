"""優惠約束求解。層級順序固定，國補永遠使用前面抵扣後的實付價。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from prism.commerce.money import money
from prism.commerce.protection import judge_protection
from prism.scenes.registry import CANONICAL_PRICE_ORDER


class RuleOrderError(ValueError):
    """聲明的優惠順序不符合硬約束。"""


@dataclass(frozen=True)
class Adjustment:
    layer: str
    kind: str
    value: Decimal
    label: str
    exclusive_group: str | None = None
    priority: int = 0
    eligibility: str = "confirmed"


@dataclass(frozen=True)
class AppliedStep:
    layer: str
    label: str
    base_before: Decimal
    deduction: Decimal
    payable_after: Decimal


@dataclass(frozen=True)
class SolveResult:
    payable: Decimal
    steps: tuple[AppliedStep, ...]
    withheld: tuple[Adjustment, ...] = ()


def price_protection(*, page_list_price_dropped: bool, cause: str) -> bool:
    """價保布爾。排除條款的明細在 protection.judge_protection。"""
    return judge_protection(page_list_price_dropped=page_list_price_dropped, cause=cause).applies


def solve(
    list_price: Decimal,
    adjustments: list[Adjustment],
    declared_order: list[str] | None = None,
) -> SolveResult:
    _check_eligibility(adjustments)
    if declared_order is not None:
        assert_declared_order(declared_order, adjustments)
    withheld = tuple(item for item in adjustments if item.eligibility == "unknown")
    applicable = [item for item in adjustments if item.eligibility == "confirmed"]
    payable = money(list_price)
    steps: list[AppliedStep] = []
    for layer in CANONICAL_PRICE_ORDER:
        chosen = _resolve_layer([item for item in applicable if item.layer == layer], payable)
        for adjustment in chosen:
            base = payable
            deduction = min(_benefit(base, adjustment), base)
            deduction = money(deduction)
            payable = money(base - deduction)
            steps.append(
                AppliedStep(
                    layer=layer,
                    label=adjustment.label,
                    base_before=base,
                    deduction=deduction,
                    payable_after=payable,
                )
            )
    return SolveResult(payable=payable, steps=tuple(steps), withheld=withheld)


def _check_eligibility(adjustments: list[Adjustment]) -> None:
    for adjustment in adjustments:
        if adjustment.eligibility not in {"confirmed", "unknown"}:
            raise RuleOrderError("優惠資格只能是已確認或無法確認")


def assert_declared_order(declared: list[str], adjustments: list[Adjustment]) -> None:
    """非法順序直接拒絕，不靜默重排。"""
    if not declared:
        raise RuleOrderError("聲明的優惠順序不可為空")
    if len(set(declared)) != len(declared):
        raise RuleOrderError("聲明的優惠順序含重複層級")
    cursor = 0
    canonical = list(CANONICAL_PRICE_ORDER)
    for layer in declared:
        found = next((index for index in range(cursor, len(canonical)) if canonical[index] == layer), None)
        if found is None:
            raise RuleOrderError("優惠順序偏離單品級、店鋪級、平台級、紅包、國補，系統拒絕套用")
        cursor = found + 1
    used = {item.layer for item in adjustments}
    missing = sorted(used - set(declared))
    if missing:
        raise RuleOrderError("聲明的優惠順序未涵蓋已出現的層級：" + "、".join(missing))


def _resolve_layer(adjustments: list[Adjustment], payable: Decimal) -> list[Adjustment]:
    if not adjustments:
        return []
    grouped: dict[str, list[Adjustment]] = {}
    solo: list[Adjustment] = []
    for adjustment in adjustments:
        _validate(adjustment)
        if adjustment.exclusive_group:
            grouped.setdefault(adjustment.exclusive_group, []).append(adjustment)
        else:
            solo.append(adjustment)
    chosen = [_pick(group, payable) for group in grouped.values()]
    chosen.extend(solo)
    chosen.sort(key=lambda item: (-item.priority, item.label))
    return chosen


def _pick(group: list[Adjustment], payable: Decimal) -> Adjustment:
    return max(group, key=lambda item: (item.priority, _benefit(payable, item), item.label))


def _benefit(payable: Decimal, adjustment: Adjustment) -> Decimal:
    if adjustment.kind == "percent":
        return money(payable * adjustment.value)
    return money(adjustment.value)


def _validate(adjustment: Adjustment) -> None:
    if adjustment.layer not in CANONICAL_PRICE_ORDER:
        raise RuleOrderError(f"未知優惠層級：{adjustment.layer}")
    if adjustment.kind not in {"percent", "amount"}:
        raise RuleOrderError(f"未知優惠型別：{adjustment.kind}")
    if adjustment.value < 0:
        raise RuleOrderError("優惠數值不可為負")
    if adjustment.kind == "percent" and adjustment.value > 1:
        raise RuleOrderError("百分比優惠必須以小數表示")

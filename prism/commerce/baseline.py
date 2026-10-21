"""歷史標價的中位數。只做確定性統計，不寫入事件指標表。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from prism.commerce.money import money


def median_money(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(money(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return money((ordered[midpoint - 1] + ordered[midpoint]) / 2)


def daily_baseline(observations: list[tuple[date, Decimal]]) -> tuple[int, Decimal | None]:
    """同一天先取中位數，再對各天的中位數取中位數。"""
    by_day: dict[date, list[Decimal]] = {}
    for observed_on, price in observations:
        by_day.setdefault(observed_on, []).append(price)
    daily: list[Decimal] = []
    for prices in by_day.values():
        day_median = median_money(prices)
        if day_median is not None:
            daily.append(day_median)
    return len(daily), median_money(daily)


def as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value

"""劣化速率。失敗率加速上升時提前走備選，不必等到通道完全失效。"""

from __future__ import annotations

PREEMPT_THRESHOLD = 0.25
MIN_SAMPLES = 4
PREEMPT_REASON = "劣化速率上升，提前啟用備選"


def failure_acceleration(success_flags: list[bool]) -> float | None:
    """後段失敗率減去前段失敗率。樣本少於四筆時不判斷。"""
    if len(success_flags) < MIN_SAMPLES:
        return None
    midpoint = len(success_flags) // 2
    return _failure_rate(success_flags[midpoint:]) - _failure_rate(success_flags[:midpoint])


def should_preempt_fallback(success_flags: list[bool], threshold: float = PREEMPT_THRESHOLD) -> bool:
    acceleration = failure_acceleration(success_flags)
    return acceleration is not None and acceleration >= threshold


def _failure_rate(flags: list[bool]) -> float:
    if not flags:
        return 0.0
    return sum(1 for flag in flags if not flag) / len(flags)

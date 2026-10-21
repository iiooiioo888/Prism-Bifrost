"""突發檢測。穩健統計，模型不參與。"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class BurstResult:
    current_count: float
    baseline_median: float | None
    mad: float | None
    score: float
    is_burst: bool
    sample_count: int


def burst_score(
    current_count: float,
    baseline_samples: list[float],
    z_threshold: float = 3.0,
    eps: float = 1e-6,
) -> BurstResult:
    """burst_score = (c_now - b) / (1.4826 * MAD + eps)。"""
    if current_count < 0:
        raise ValueError("提及數不可為負")
    if not baseline_samples:
        return BurstResult(
            current_count=current_count,
            baseline_median=None,
            mad=None,
            score=0.0,
            is_burst=False,
            sample_count=0,
        )
    baseline = statistics.median(baseline_samples)
    deviations = [abs(sample - baseline) for sample in baseline_samples]
    mad = statistics.median(deviations)
    score = (current_count - baseline) / (1.4826 * mad + eps)
    return BurstResult(
        current_count=current_count,
        baseline_median=baseline,
        mad=mad,
        score=score,
        is_burst=score > z_threshold,
        sample_count=len(baseline_samples),
    )


def normalize_burst(score: float, divisor: float = 3.0) -> float:
    if divisor <= 0:
        raise ValueError("突發歸一化除數必須為正")
    if score <= 0:
        return 0.0
    return min(score / divisor, 1.0)

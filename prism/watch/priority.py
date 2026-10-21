"""優先級評分。權重來自配置，預設值對齊說明書。"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WEIGHTS = (0.20, 0.25, 0.20, 0.20, 0.10, 0.05)


@dataclass(frozen=True)
class PriorityParts:
    source_diversity_norm: float
    calibrated_confidence_avg: float
    burst_score_norm: float
    entity_relevance: float
    cross_domain_support: float
    unsupported_ratio: float


@dataclass(frozen=True)
class PriorityResult:
    priority: float
    parts: PriorityParts
    weights: tuple[float, float, float, float, float, float]
    high_priority: bool


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_priority(
    parts: PriorityParts,
    weights: tuple[float, ...] = DEFAULT_WEIGHTS,
    alert_threshold: float = 0.75,
) -> PriorityResult:
    if len(weights) != 6:
        raise ValueError("優先級權重必須是六個數")
    if any(weight < 0 for weight in weights):
        raise ValueError("優先級權重不可為負")
    clamped = PriorityParts(
        source_diversity_norm=_clamp01(parts.source_diversity_norm),
        calibrated_confidence_avg=_clamp01(parts.calibrated_confidence_avg),
        burst_score_norm=_clamp01(parts.burst_score_norm),
        entity_relevance=_clamp01(parts.entity_relevance),
        cross_domain_support=_clamp01(parts.cross_domain_support),
        unsupported_ratio=_clamp01(parts.unsupported_ratio),
    )
    w1, w2, w3, w4, w5, w6 = weights
    priority = (
        w1 * clamped.source_diversity_norm
        + w2 * clamped.calibrated_confidence_avg
        + w3 * clamped.burst_score_norm
        + w4 * clamped.entity_relevance
        + w5 * clamped.cross_domain_support
        - w6 * clamped.unsupported_ratio
    )
    return PriorityResult(
        priority=priority,
        parts=clamped,
        weights=(w1, w2, w3, w4, w5, w6),
        high_priority=priority >= alert_threshold,
    )

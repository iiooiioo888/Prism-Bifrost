"""三檔歸併評分。研判分數本身不寫事實層。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MergeBand = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class MergeScore:
    rule_score: float
    semantic_score: float
    entity_overlap_score: float
    rule_hit: bool
    semantic_hit: bool
    score: float
    band: MergeBand

    @property
    def auto_merge(self) -> bool:
        return self.band == "high"


def blend(rule_score: float, semantic_score: float, entity_overlap_score: float) -> float:
    """說明書公式：0.4 規則 + 0.4 語義 + 0.2 實體重疊。"""
    return 0.4 * rule_score + 0.4 * semantic_score + 0.2 * entity_overlap_score


def classify_band(
    score: float,
    rule_hit: bool,
    semantic_hit: bool,
    auto_threshold: float = 0.85,
    review_threshold: float = 0.65,
) -> MergeBand:
    """高檔必須同時達到分數與雙通道命中。

    分數已達自動閾值但規則或語義缺一，依保守歸併落入人工佇列，不自動合併。
    """
    if score >= auto_threshold and rule_hit and semantic_hit:
        return "high"
    if score >= review_threshold:
        return "medium"
    return "low"


def score_merge(
    rule_score: float,
    semantic_score: float,
    entity_overlap_score: float,
    rule_hit: bool,
    semantic_hit: bool,
    auto_threshold: float = 0.85,
    review_threshold: float = 0.65,
) -> MergeScore:
    for name, value in (
        ("rule_score", rule_score),
        ("semantic_score", semantic_score),
        ("entity_overlap_score", entity_overlap_score),
    ):
        if value < 0 or value > 1:
            raise ValueError(f"{name} 必須位於 0 到 1 之間")
    blended = blend(rule_score, semantic_score, entity_overlap_score)
    return MergeScore(
        rule_score=rule_score,
        semantic_score=semantic_score,
        entity_overlap_score=entity_overlap_score,
        rule_hit=rule_hit,
        semantic_hit=semantic_hit,
        score=blended,
        band=classify_band(
            blended,
            rule_hit,
            semantic_hit,
            auto_threshold=auto_threshold,
            review_threshold=review_threshold,
        ),
    )

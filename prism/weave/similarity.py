"""規則特徵與正文語義分數。語義通道在第一階段用可複算的字元二元組餘弦，不呼叫模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from prism.bridge.normalize import text_similarity


@dataclass(frozen=True)
class MentionView:
    title: str
    body: str
    author: str | None
    normalized_url: str
    upstream_timestamp: datetime | None
    entity_ids: frozenset[str]


def rule_features(
    left: MentionView,
    right: MentionView,
    title_threshold: float = 0.80,
    author_window_hours: float = 6,
) -> tuple[float, bool, float]:
    parts: list[float] = []
    url_hit = bool(left.normalized_url) and left.normalized_url == right.normalized_url
    if url_hit:
        parts.append(1.0)
    title_score = text_similarity(left.title, right.title)
    if title_score >= title_threshold:
        parts.append(title_score)
    author_hit = False
    if left.author and right.author and left.author.casefold() == right.author.casefold():
        if left.upstream_timestamp and right.upstream_timestamp:
            delta = abs(left.upstream_timestamp - right.upstream_timestamp)
            if delta <= timedelta(hours=author_window_hours):
                author_hit = True
                parts.append(0.90)
    rule_hit = url_hit or title_score >= title_threshold or author_hit
    rule_score = min(1.0, max(parts) if parts else 0.0)
    return rule_score, rule_hit, title_score


def semantic_features(left: MentionView, right: MentionView, hit_threshold: float) -> tuple[float, bool]:
    """語義分數取正文餘弦。正文為空時才退回標題，避免與規則通道重複計分。"""
    if left.body.strip() and right.body.strip():
        score = text_similarity(left.body, right.body)
    else:
        score = text_similarity(left.title, right.title)
    score = min(1.0, max(0.0, score))
    return score, score >= hit_threshold


def entity_overlap(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)

"""確定性統計。此模組是事件指標表的唯一寫入者。"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta


def shannon_entropy(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        if count <= 0:
            continue
        probability = count / total
        entropy -= probability * math.log2(probability)
    return entropy


def source_diversity(domains: list[str]) -> float:
    if not domains:
        return 0.0
    return len(set(domains)) / len(domains)


def diffusion_rate(platforms_before: set[str], platforms_now: set[str], hours: float = 6.0) -> float:
    if hours <= 0:
        raise ValueError("擴散時間窗口必須為正")
    new_platforms = platforms_now - platforms_before
    return len(new_platforms) / hours


@dataclass(frozen=True)
class MentionStat:
    mention_id: str
    event_id: str
    upstream_timestamp: datetime | None
    collected_at: datetime
    platform: str
    domain: str
    source_type: str
    acquisition_mode: str
    calibrated_confidence: float
    abstain: bool
    duplicate_of: str | None
    sentiment: str | None = None


@dataclass(frozen=True)
class MetricWindow:
    window_start: datetime
    window_end: datetime
    mention_count: int
    source_diversity: float
    platform_entropy: float
    diffusion_rate: float
    sentiment_dist: dict[str, float]
    burst_score: float | None


def mentions_in_window(mentions: list[MentionStat], start: datetime, end: datetime) -> list[MentionStat]:
    selected: list[MentionStat] = []
    for mention in mentions:
        stamp = mention.upstream_timestamp
        if stamp is None:
            continue
        if start <= stamp < end:
            selected.append(mention)
    return selected


def sentiment_distribution(mentions: list[MentionStat]) -> dict[str, float]:
    labeled = [mention.sentiment for mention in mentions if mention.sentiment]
    if not labeled:
        return {}
    counts = Counter(labeled)
    total = sum(counts.values())
    return {key: value / total for key, value in sorted(counts.items())}


def compute_window(
    mentions: list[MentionStat],
    window_start: datetime,
    window_end: datetime,
    previous_platforms: set[str],
    burst_score: float | None,
) -> MetricWindow:
    current = mentions_in_window(mentions, window_start, window_end)
    hours = max((window_end - window_start).total_seconds() / 3600, 0.000001)
    platforms_now = {mention.platform for mention in current}
    return MetricWindow(
        window_start=window_start,
        window_end=window_end,
        mention_count=len(current),
        source_diversity=source_diversity([mention.domain for mention in current]),
        platform_entropy=shannon_entropy(dict(Counter(mention.platform for mention in current))),
        diffusion_rate=diffusion_rate(previous_platforms, platforms_now, hours=hours),
        sentiment_dist=sentiment_distribution(current),
        burst_score=burst_score,
    )


def rolling_windows(now: datetime) -> list[tuple[str, datetime, datetime]]:
    return [
        ("1h", now - timedelta(hours=1), now),
        ("24h", now - timedelta(hours=24), now),
        ("7d", now - timedelta(days=7), now),
    ]


# 情感詞表是確定性佔位，不是大型語言模型。未命中則不產出分布。
_POSITIVE = ("恢復", "完成", "開放", "穩定", "確認")
_NEGATIVE = ("管制", "封閉", "延誤", "事故", "中斷", "故障")


def persist_ready(session, row) -> None:  # type: ignore[no-untyped-def]
    """事件指標的唯一寫入入口。呼叫端應是研判管道中的確定性統計，而不是模型。"""
    existing = session.get(
        type(row),
        (row.event_id, row.window_start, row.window_end),
    )
    if existing is None:
        session.add(row)
        return
    existing.mention_count = row.mention_count
    existing.source_diversity = row.source_diversity
    existing.platform_entropy = row.platform_entropy
    existing.diffusion_rate = row.diffusion_rate
    existing.sentiment_dist = row.sentiment_dist
    existing.burst_score = row.burst_score
    existing.computed_at = row.computed_at


def lexicon_sentiment(body: str) -> str | None:
    positive = sum(term in body for term in _POSITIVE)
    negative = sum(term in body for term in _NEGATIVE)
    if positive == 0 and negative == 0:
        return None
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"

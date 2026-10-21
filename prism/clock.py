"""時間工具。儲存一律使用帶時區的 UTC。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def in_zone(value: datetime, timezone_name: str) -> datetime:
    return ensure_utc(value).astimezone(ZoneInfo(timezone_name))


def trailing_hour(now: datetime, days_back: int = 0) -> tuple[datetime, datetime]:
    """回傳對齊的一小時窗口：[now - 1h - d 天, now - d 天]。"""
    end = ensure_utc(now) - timedelta(days=days_back)
    return end - timedelta(hours=1), end

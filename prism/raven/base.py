"""探針抽象。平台差異止於這一層。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class RawItem:
    source_url: str
    upstream_timestamp: datetime | None
    collected_at: datetime
    platform: str
    title: str | None
    body: str
    author: str | None
    language: str
    acquisition_mode: Literal["live", "fallback"]
    raw_ref: str
    probe_id: str
    content_hash: str
    source_type: str = "web"


@dataclass
class ProbeHealth:
    probe_id: str
    platform: str
    last_success_at: datetime | None
    consecutive_failures: int
    fallback_ratio_24h: float
    mean_lag_seconds: float | None
    status: Literal["healthy", "degraded", "down"]
    detail: str = ""


class Probe(ABC):
    platform: str
    probe_id: str

    @abstractmethod
    async def fetch(
        self,
        query_or_feed: str,
        since: datetime,
        limit: int = 100,
    ) -> list[RawItem]:
        """採集公開來源。不得改寫原始網址。"""

    @abstractmethod
    async def health(self) -> ProbeHealth:
        """回報通道健康度。"""

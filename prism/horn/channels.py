"""加拉爾號角。測試預設為乾跑，不向外部網址送出資料。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from prism.audit.log import append_audit


@dataclass(frozen=True)
class HornResult:
    channel: str
    ok: bool
    detail: str


def test_channels(session: Session, channels: list[str], actor: str = "system:horn") -> list[HornResult]:
    catalog = {
        "workbench": "工作台告警中心可接收高優先級告警",
        "im": "即時通訊通道已登記，本次為乾跑",
        "email": "郵件通道已登記，本次為乾跑",
        "webhook": "網路鉤子已登記，本次不送出外部請求",
    }
    results: list[HornResult] = []
    for channel in channels:
        if channel not in catalog:
            results.append(HornResult(channel, False, "未知通道"))
            continue
        results.append(HornResult(channel, True, catalog[channel]))
    append_audit(
        session,
        actor=actor,
        action="horn.test",
        target_type="horn",
        target_id="channels",
        payload={"results": [item.__dict__ for item in results], "live_delivery": False},
        created_at=datetime.now(timezone.utc),
    )
    return results

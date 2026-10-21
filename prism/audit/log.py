"""僅追加審計。此模組沒有更新或刪除函式。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from prism.db.models import AuditLog


def append_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    created_at: datetime | None = None,
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(row)
    return row

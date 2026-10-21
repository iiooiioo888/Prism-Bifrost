"""權杖鑑權與敏感操作的二次確認。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class Principal:
    actor: str
    role: str


_RANK = {"viewer": 1, "analyst": 2, "admin": 3}


def principal_from_request(
    request: Request,
    authorization: str | None = Header(default=None),
    x_confirm: str | None = Header(default=None),
) -> Principal:
    settings = request.app.state.runtime.settings
    if not settings.app.get("auth_required", True):
        return Principal(actor="系統", role="admin")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="需要持有權杖")
    token = authorization.split(" ", 1)[1].strip()
    record = settings.token_map().get(token)
    if record is None:
        raise HTTPException(status_code=401, detail="權杖無效")
    return Principal(actor=record["actor"], role=record["role"])


def require_role(principal: Principal, role: str) -> None:
    if _RANK.get(principal.role, 0) < _RANK[role]:
        raise HTTPException(status_code=403, detail="角色不足以執行此操作")


def require_confirm(x_confirm: str | None) -> None:
    if (x_confirm or "").strip().lower() not in {"yes", "true", "1"}:
        raise HTTPException(status_code=428, detail="敏感操作需要標頭 X-Confirm: yes")

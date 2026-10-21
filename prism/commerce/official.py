"""平台官方介面。沒有營運者設定的來源時不呼叫，也不改走搜尋頁。"""

from __future__ import annotations

from typing import Any

from prism.runtime import PrismRuntime


def official_report(runtime: PrismRuntime) -> dict[str, Any]:
    enabled = bool(runtime.settings.official.get("enabled"))
    provider = getattr(runtime, "official_quotes", None)
    if not enabled:
        return {
            "configured": False,
            "detail": "官方介面未設定，不呼叫平台介面。搜尋頁被拒絕時，價格只來自使用者貼上的公開商品頁。",
        }
    if provider is None:
        return {
            "configured": False,
            "detail": "官方介面已開啟，但沒有憑證或介面位址，沒有呼叫。",
        }
    return {"configured": True, "detail": "已讀取官方介面回傳的公開標價。"}


def official_quotes(runtime: PrismRuntime, query: str) -> list[dict[str, Any]]:
    report = official_report(runtime)
    if not report["configured"]:
        return []
    provider = getattr(runtime, "official_quotes", None)
    rows = provider(query) if provider is not None else []
    return [row for row in rows if isinstance(row, dict)]

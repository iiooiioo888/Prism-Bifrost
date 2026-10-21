"""模型輸出不得生成貨幣金額。拒絕時不回顯數字。"""

from __future__ import annotations

import json
import re
from typing import Any

MONEY_KEYS = frozenset({"price", "amount", "payable", "list_price", "pay_price", "currency_amount"})
_CURRENCY = re.compile(r"[¥￥]\s*\d|(?:\d+(?:\.\d+)?)\s*[元圓]|\bRMB\b|\bCNY\b", re.IGNORECASE)
REJECTION = {"status": "rejected", "prohibited": True, "error": "模型輸出含貨幣金額，已拒絕且不回顯"}


def contains_prohibited_money(raw: str) -> bool:
    if _CURRENCY.search(raw):
        return True
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return _walk(parsed)


def _walk(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in MONEY_KEYS or _CURRENCY.search(str(key)):
                return True
            if _walk(item):
                return True
        return False
    if isinstance(value, list):
        return any(_walk(item) for item in value)
    if isinstance(value, str):
        return _CURRENCY.search(value) is not None
    return False

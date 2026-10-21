"""從商品名稱取出品牌、型號、容量與顏色。缺欄位不會被猜成高檔匹配。"""

from __future__ import annotations

import hashlib
import re

from prism.commerce.sku_match import SkuTraits

_BRAND = re.compile(r"品牌[:：]\s*(\S+)")
_MODEL = re.compile(r"型號[:：]\s*(\S+)")
_CAPACITY = re.compile(r"(\d+(?:\.\d+)?)(毫升|ml|升|l|千克|kg|克|g)", re.IGNORECASE)
_COLORS = ("黑色", "白色", "紅色", "藍色", "綠色", "粉色", "金色", "銀色", "灰色", "紫色", "棕色", "橙色", "黃色", "青色")
_UNIT = {"毫升": "ml", "ml": "ml", "升": "l", "l": "l", "千克": "kg", "kg": "kg", "克": "g", "g": "g"}


def parse_traits(text: str) -> SkuTraits:
    brand = _capture(_BRAND, text)
    model = _capture(_MODEL, text)
    capacity = _capacity(text)
    color = _color(text)
    title = _BRAND.sub(" ", text)
    title = _MODEL.sub(" ", title)
    title = _CAPACITY.sub(" ", title)
    if color:
        title = title.replace(color, " ")
    specs: list[tuple[str, str]] = []
    if capacity:
        specs.append(("capacity", capacity))
    if color:
        specs.append(("color", color))
    return SkuTraits(title=" ".join(title.split()), brand=brand, model=model, specs=tuple(specs))


def observe_traits(text: str, anchor: SkuTraits) -> SkuTraits:
    """頁面沒有另標品牌或型號時，只在原文出現監測目標的字樣才算一致。"""
    parsed = parse_traits(text)
    sample = _token(text)
    brand = parsed.brand
    model = parsed.model
    if brand is None and anchor.brand and _token(anchor.brand) in sample:
        brand = anchor.brand
    if model is None and anchor.model and _token(anchor.model) in sample:
        model = anchor.model
    return SkuTraits(
        title=parsed.title or " ".join(text.split()),
        brand=brand,
        model=model,
        specs=parsed.specs,
    )


def identity_code(traits: SkuTraits) -> str | None:
    """有品牌、型號、容量或顏色才共用一個 SKU。只有通稱時不合併。"""
    specs = dict(traits.specs)
    parts = (_token(traits.brand), _token(traits.model), _token(specs.get("capacity")), _token(specs.get("color")))
    if not any(parts):
        return None
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"id:{digest}"


def _capture(pattern: re.Pattern[str], text: str) -> str | None:
    found = pattern.search(text)
    if not found:
        return None
    token = found.group(1).strip()
    return token or None


def _capacity(text: str) -> str | None:
    found = _CAPACITY.search(text)
    if not found:
        return None
    unit_name = _UNIT[found.group(2).lower()]
    return f"{found.group(1)}{unit_name}"


def _color(text: str) -> str | None:
    return next((name for name in _COLORS if name in text), None)


def _token(value: str | None) -> str:
    return " ".join((value or "").casefold().split())

"""同一身份的實付對照。規格不同只給單位價格，不併成同一個排序。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from prism.commerce.identity import SkuTraits
from prism.commerce.money import money, money_text, score_text
from prism.commerce.rules import Adjustment, solve

PROHIBITED_STATEMENTS = ("最便宜", "最優", "全網最低", "實時", "全自動", "模型生成貨幣金額")
_CAPACITY = re.compile(r"(\d+(?:\.\d+)?)(ml|l|g|kg)")
_BASE = {"ml": ("ml", Decimal("1")), "l": ("ml", Decimal("1000")), "g": ("g", Decimal("1")), "kg": ("g", Decimal("1000"))}
_UNIT_QUANT = Decimal("0.0001")
_OUTLIER = Decimal("0.5")


@dataclass(frozen=True)
class Quote:
    platform: str
    label: str
    source_url: str
    title: str
    sku_code: str
    list_price: Decimal
    price_basis: str
    match_band: str
    match_confidence: Decimal
    availability: str = "unknown"
    shipping_price: Decimal | None = None
    seller: str | None = None
    traits: SkuTraits = SkuTraits()
    adjustments: tuple[Adjustment, ...] = ()


def landed_payable(
    list_price: Decimal,
    adjustments: tuple[Adjustment, ...] | list[Adjustment],
    shipping_price: Decimal | None,
):
    """規則引擎先算實付，公開運費只在頁面寫明時加在後面。"""
    result = solve(list_price, list(adjustments))
    payable = result.payable if shipping_price is None else money(result.payable + shipping_price)
    return payable, result


def compare_quotes(
    quotes: list[Quote],
    *,
    baselines: dict[str, tuple[int, Decimal | None]] | None = None,
    baseline_required: int = 14,
) -> dict:
    history = baselines or {}
    groups = _groups(quotes, history, baseline_required)
    related = _related(quotes)
    note = (
        "只對照同一件商品的遊客態實付。低檔匹配與缺貨留在標價表，不進入對照。"
        "運費只有頁面公開寫明才計入。容量或顏色不同的商品分開列單位價格，不併成同一排序。"
    )
    payload = {"note": note, "same_identity": groups, "related_specs": related}
    _guard(payload)
    return payload


def _groups(quotes: list[Quote], baselines: dict[str, tuple[int, Decimal | None]], baseline_required: int) -> list[dict]:
    buckets: dict[str, list[Quote]] = {}
    for quote in quotes:
        buckets.setdefault(quote.sku_code, []).append(quote)
    groups: list[dict] = []
    for sku_code in sorted(buckets):
        rows = [_row(quote, baselines.get(sku_code), baseline_required) for quote in buckets[sku_code]]
        comparable = [row for row in rows if row["comparable"]]
        if len(comparable) >= 3:
            amounts = [row["_payable"] for row in comparable]
            for index, row in enumerate(comparable):
                others = amounts[:index] + amounts[index + 1 :]
                if row["_payable"] < _median(others) * _OUTLIER:
                    row["review"] = True
                    row["reason"] = f"{row['reason']}實付低於其餘可對照標價中位數的一半，先複核是不是同一件。"
        comparable.sort(key=lambda row: (row["_payable"], row["platform"]))
        held = [row for row in rows if not row["comparable"]]
        held.sort(key=lambda row: row["platform"])
        ordered = comparable + held
        low = comparable[0] if comparable else None
        low_amount = None if low is None else low["_payable"]
        spread = None
        if len(comparable) >= 2 and low_amount is not None:
            spread = money_text(comparable[-1]["_payable"] - low_amount)
        for row in ordered:
            if row["comparable"] and low_amount is not None:
                row["gap"] = money_text(row["_payable"] - low_amount)
            row.pop("_payable", None)
        review = next((row["reason"] for row in ordered if row["review"]), None)
        groups.append(
            {
                "sku_code": sku_code,
                "title": _identity_title(buckets[sku_code]),
                "comparable_count": len(comparable),
                "spread": spread,
                "low_payable": None if low is None else low["payable"],
                "low_platform": None if low is None else low["label"],
                "statement": _statement(buckets[sku_code], comparable, spread, low),
                "review": review,
                "rows": ordered,
            }
        )
    groups.sort(key=lambda item: (-item["comparable_count"], item["sku_code"]))
    return groups


def _row(quote: Quote, baseline: tuple[int, Decimal | None] | None, baseline_required: int) -> dict:
    payable, _result = landed_payable(quote.list_price, quote.adjustments, quote.shipping_price)
    comparable, reason = _comparable(quote)
    unit_amount, unit_name = _unit_price(payable, dict(quote.traits.specs).get("capacity"))
    return {
        "platform": quote.platform,
        "label": quote.label,
        "source_url": quote.source_url,
        "title": quote.title,
        "list_price": money_text(quote.list_price),
        "payable": money_text(payable) if comparable else None,
        "shipping": None if quote.shipping_price is None else money_text(quote.shipping_price),
        "shipping_known": quote.shipping_price is not None,
        "availability": quote.availability,
        "seller": quote.seller,
        "match_band": quote.match_band,
        "match_confidence": score_text(quote.match_confidence),
        "gap": None,
        "unit_price": None if unit_amount is None else f"{unit_amount:.4f}",
        "unit": unit_name,
        "baseline": _baseline_text(quote.list_price, baseline, baseline_required),
        "comparable": comparable,
        "review": False,
        "reason": reason,
        "_payable": payable,
    }


def _comparable(quote: Quote) -> tuple[bool, str]:
    if quote.price_basis != "guest":
        return False, "登入態價格不可比。"
    if quote.match_band == "low":
        return False, "SKU 匹配置信度為低檔，不進入實付對照。"
    if quote.availability == "outofstock":
        return False, "頁面標成缺貨，不進入實付對照。"
    if quote.shipping_price is None:
        return True, "運費未公開，實付不含運費。"
    return True, f"公開運費 {money_text(quote.shipping_price)} 已計入實付。"


def _statement(quotes: list[Quote], comparable: list[dict], spread: str | None, low: dict | None) -> str:
    if not comparable or low is None:
        return "這一身份沒有可對照的遊客態標價。"
    if len(comparable) == 1:
        text = f"{low['label']} 實付 {low['payable']}。這一身份目前只有一筆可對照標價。"
    else:
        text = (
            f"同一件商品有 {len(comparable)} 筆可對照實付。"
            f"實付較低的是 {low['label']} {low['payable']}，與其餘可對照方案最多相差 {spread}。"
        )
    if any(quote.shipping_price is None for quote in quotes if quote.match_band != "low"):
        text += "運費未公開的方案，實付不含運費。"
    if any(row["review"] for row in comparable):
        text += "其中有實付低於其餘中位數一半，先複核是不是同一件。"
    if any(quote.match_band == "medium" for quote in quotes):
        text += "中檔匹配可以對照，仍需人工確認是不是同一件。"
    return text


def _related(quotes: list[Quote]) -> list[dict]:
    families: dict[tuple[str, str], list[Quote]] = {}
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    for quote in quotes:
        if quote.price_basis != "guest" or not quote.traits.brand or not quote.traits.model:
            continue
        key = (quote.traits.brand.casefold(), quote.traits.model.casefold())
        families.setdefault(key, []).append(quote)
        labels[key] = (quote.traits.brand, quote.traits.model)
    boards: list[dict] = []
    for key in sorted(families):
        specs = {_spec_key(quote) for quote in families[key]}
        if len(specs) < 2:
            continue
        rows = []
        for quote in families[key]:
            payable, _result = landed_payable(quote.list_price, quote.adjustments, quote.shipping_price)
            specs_map = dict(quote.traits.specs)
            unit_amount, unit_name = _unit_price(payable, specs_map.get("capacity"))
            rows.append(
                {
                    "sku_code": quote.sku_code,
                    "platform": quote.platform,
                    "label": quote.label,
                    "source_url": quote.source_url,
                    "capacity": specs_map.get("capacity"),
                    "color": specs_map.get("color"),
                    "list_price": money_text(quote.list_price),
                    "payable": money_text(payable),
                    "unit_price": None if unit_amount is None else f"{unit_amount:.4f}",
                    "unit": unit_name,
                    "match_band": quote.match_band,
                }
            )
        rows.sort(key=lambda row: (row["capacity"] or "", row["color"] or "", row["payable"], row["platform"]))
        brand, model = labels[key]
        boards.append(
            {
                "brand": brand,
                "model": model,
                "note": "容量或顏色不同，已分開監測，不併入同一實付排序。單位價格只供人工對照。",
                "rows": rows,
            }
        )
    return boards


def _identity_title(quotes: list[Quote]) -> str:
    traits = quotes[0].traits
    specs = dict(traits.specs)
    parts = [traits.brand, traits.model, specs.get("capacity"), specs.get("color")]
    text = " ".join(part for part in parts if part)
    return text or quotes[0].title


def _spec_key(quote: Quote) -> tuple[str, str]:
    specs = dict(quote.traits.specs)
    return (specs.get("capacity") or "", specs.get("color") or "")


def _unit_price(amount: Decimal, capacity: str | None) -> tuple[Decimal | None, str | None]:
    found = _CAPACITY.fullmatch(capacity or "")
    if not found:
        return None, None
    unit_name, factor = _BASE[found.group(2)]
    quantity = Decimal(found.group(1)) * factor
    if quantity <= 0:
        return None, None
    return (amount / quantity).quantize(_UNIT_QUANT, rounding=ROUND_HALF_UP), unit_name


def _baseline_text(list_price: Decimal, baseline: tuple[int, Decimal | None] | None, required: int) -> str | None:
    if baseline is None:
        return None
    days, median = baseline
    if median is None or days < required:
        return None
    gap = money(list_price - median)
    shown = money_text(median)
    if gap > 0:
        return f"標價高於 {days} 日基線中位數 {shown} 共 {money_text(gap)}"
    if gap < 0:
        return f"標價低於 {days} 日基線中位數 {shown} 共 {money_text(-gap)}"
    return f"標價等於 {days} 日基線中位數 {shown}"


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return money((ordered[mid - 1] + ordered[mid]) / Decimal(2))


def _guard(payload: dict) -> None:
    texts = [payload["note"]]
    for group in payload["same_identity"]:
        texts.append(group["statement"])
        if group["review"]:
            texts.append(group["review"])
        texts.extend(row["reason"] for row in group["rows"])
    for board in payload["related_specs"]:
        texts.append(board["note"])
    for text in texts:
        for banned in PROHIBITED_STATEMENTS:
            if banned in text:
                raise RuntimeError("價格對照含被禁止的聲明")

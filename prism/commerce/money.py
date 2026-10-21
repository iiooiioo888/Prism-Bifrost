"""貨幣金額只以十進位運算，禁止經由浮點數。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
MILLE = Decimal("0.001")


def money(value: Decimal | str | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(value: Decimal) -> str:
    return f"{money(value):.2f}"


def unit(value: Decimal | str | int) -> Decimal:
    return Decimal(str(value))


def score_text(value: Decimal) -> str:
    return f"{value.quantize(MILLE, rounding=ROUND_HALF_UP):.3f}"

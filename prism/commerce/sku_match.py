"""SKU 匹配置信度。跨平台對照只產出分檔，結論置信度不得高過這個上限。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from prism.commerce.money import unit


@dataclass(frozen=True)
class SkuTraits:
    """對照用的公開識別欄位。缺欄位會降低置信度，不會被補成高檔。"""

    code: str | None = None
    title: str = ""
    brand: str | None = None
    model: str | None = None
    specs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MatchMark:
    confidence: Decimal
    band: str
    cap: Decimal
    acquisition_mode: str
    reasons: tuple[str, ...]
    limits_conclusion: bool = True


def band_of(confidence: Decimal, *, high: Decimal, medium: Decimal) -> str:
    if confidence >= high:
        return "high"
    if confidence >= medium:
        return "medium"
    return "low"


def mark_match(
    confidence: Decimal,
    *,
    high: Decimal,
    medium: Decimal,
    acquisition_mode: str = "live",
    reasons: tuple[str, ...] = (),
) -> MatchMark:
    """已給定的匹配置信度只分檔並封頂，不在這裡被改寫得更高。"""
    bounded = min(max(unit(confidence), unit("0")), unit("1"))
    factor = unit("0.90") if acquisition_mode == "fallback" else unit("1")
    return MatchMark(
        confidence=bounded,
        band=band_of(bounded, high=high, medium=medium),
        cap=bounded * factor,
        acquisition_mode=acquisition_mode,
        reasons=reasons,
    )


def align(
    anchor: SkuTraits,
    observed: SkuTraits,
    *,
    high: Decimal,
    medium: Decimal,
    acquisition_mode: str = "live",
) -> MatchMark:
    """標準碼衝突或品牌、型號、規格衝突時直接落到低檔。標題單獨一致不能進入可執行結論。"""
    anchor_code = _token(anchor.code)
    observed_code = _token(observed.code)
    if anchor_code and observed_code:
        if anchor_code == observed_code:
            return mark_match(
                unit("1"),
                high=high,
                medium=medium,
                acquisition_mode=acquisition_mode,
                reasons=("標準碼一致",),
            )
        return mark_match(
            unit("0"),
            high=high,
            medium=medium,
            acquisition_mode=acquisition_mode,
            reasons=("標準碼衝突，不因標題相似而提高",),
        )
    conflict = _identity_conflict(anchor, observed)
    if conflict:
        return mark_match(
            unit("0"),
            high=high,
            medium=medium,
            acquisition_mode=acquisition_mode,
            reasons=(conflict,),
        )
    score = unit("0")
    reasons: list[str] = []
    if _same(anchor.brand, observed.brand):
        score += unit("0.45")
        reasons.append("品牌一致")
    if _same(anchor.model, observed.model):
        score += unit("0.40")
        reasons.append("型號一致")
    if _same(anchor.title, observed.title):
        score += unit("0.25")
        reasons.append("標題一致" if reasons else "僅標題一致，不足以單獨確認是同一 SKU")
    spec_delta, spec_reason = _spec_agreement(anchor.specs, observed.specs)
    if spec_reason:
        score += spec_delta
        reasons.append(spec_reason)
    if not reasons:
        reasons.append("沒有可用的 SKU 對照欄位")
    return mark_match(
        min(score, unit("1")),
        high=high,
        medium=medium,
        acquisition_mode=acquisition_mode,
        reasons=tuple(reasons),
    )


def _identity_conflict(anchor: SkuTraits, observed: SkuTraits) -> str | None:
    if _both(anchor.brand, observed.brand) and not _same(anchor.brand, observed.brand):
        return "品牌衝突"
    if _both(anchor.model, observed.model) and not _same(anchor.model, observed.model):
        return "型號衝突"
    if _spec_conflict(anchor.specs, observed.specs):
        return "規格衝突"
    return None


def _spec_agreement(
    left: tuple[tuple[str, str], ...],
    right: tuple[tuple[str, str], ...],
) -> tuple[Decimal, str]:
    left_map = _spec_map(left)
    right_map = _spec_map(right)
    shared = set(left_map) & set(right_map)
    if not shared:
        return unit("0"), ""
    matched = sum(1 for key in shared if left_map[key] == right_map[key])
    if matched == 0:
        return unit("0"), ""
    return unit("0.15") * (Decimal(matched) / Decimal(len(shared))), "規格一致"


def _spec_conflict(left: tuple[tuple[str, str], ...], right: tuple[tuple[str, str], ...]) -> bool:
    left_map = _spec_map(left)
    right_map = _spec_map(right)
    return any(left_map[key] != right_map[key] for key in set(left_map) & set(right_map))


def _spec_map(specs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return {_token(key): _token(value) for key, value in specs if _token(key) and _token(value)}


def _same(left: str | None, right: str | None) -> bool:
    return bool(_both(left, right) and _token(left) == _token(right))


def _both(left: str | None, right: str | None) -> bool:
    return bool(_token(left) and _token(right))


def _token(value: str | None) -> str:
    return " ".join((value or "").casefold().split())

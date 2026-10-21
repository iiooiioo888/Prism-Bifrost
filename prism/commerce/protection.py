"""價保是布爾，加上排除條款是否命中。不輸出天數。"""

from __future__ import annotations

from dataclasses import dataclass


CLAUSES = (
    ("national_subsidy", "國補"),
    ("platform_coupon", "平台券"),
    ("red_packet", "紅包"),
    ("billion_subsidy", "百億補貼"),
)


@dataclass(frozen=True)
class ProtectionVerdict:
    applies: bool
    trigger: str
    matched_exclusions: tuple[str, ...]
    unparsed: bool
    detail: str

    def to_dict(self) -> dict:
        return {
            "applies": self.applies,
            "trigger": self.trigger,
            "matched_exclusions": list(self.matched_exclusions),
            "unparsed": self.unparsed,
            "detail": self.detail,
        }


def judge_protection(*, page_list_price_dropped: bool, cause: str) -> ProtectionVerdict:
    """只有頁面標價下降才成立。國補、平台券、紅包、百億補貼命中排除條款。未解析則不成立。"""
    labels = dict(CLAUSES)
    matched = tuple(code for code in labels if cause == code)
    unparsed = cause == "unparsed"
    applies = bool(page_list_price_dropped) and cause == "page_list_price" and not matched and not unparsed
    if unparsed:
        detail = "價保排除條款未能完整解析，價保不成立"
    elif matched:
        detail = f"{labels[matched[0]]}屬於排除條款，不構成價保"
    elif applies:
        detail = "僅頁面標價下降觸發價保"
    elif cause == "page_list_price":
        detail = "頁面標價未下降，價保不成立"
    else:
        detail = "沒有頁面標價下降這個觸發，價保不成立"
    return ProtectionVerdict(
        applies=applies,
        trigger="page_list_price" if cause == "page_list_price" else "none",
        matched_exclusions=matched,
        unparsed=unparsed,
        detail=detail,
    )

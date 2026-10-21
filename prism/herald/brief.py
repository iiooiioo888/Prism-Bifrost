"""簡報合成。合成器只使用鑑別器留下的證據。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from prism.config import Settings
from prism.db.models import BriefRow, CanonicalEvent, EventAssessment, EventMention
from prism.llm.validate import discriminate


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def render_brief(
    *,
    bottom_line: str,
    event_ids: list[str],
    what_changed: str,
    by_category: str,
    uncertainties: str,
    watchlist: str,
    footer: str,
) -> str:
    cited = "、".join(event_ids) if event_ids else "無"
    return (
        f"## 核心判斷\n{bottom_line}（引用：{cited}）\n\n"
        f"## 本期變化\n{what_changed}\n\n"
        f"## 訊號分類\n{by_category}\n\n"
        f"## 主要不確定性\n{uncertainties}\n\n"
        f"## 後續關注\n{watchlist}\n\n"
        f"> {footer}\n"
    )


class BriefService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def generate(self, period: str = "day", now: datetime | None = None, model_claims: list[dict] | None = None) -> BriefRow:
        if period not in {"day", "week"}:
            raise ValueError("週期只接受 day 或 week")
        moment = _aware(now or datetime.now(timezone.utc))
        window = timedelta(days=1 if period == "day" else 7)
        events = self.session.scalars(select(CanonicalEvent).where(CanonicalEvent.status == "active")).all()
        known_events = {event.event_id for event in events}
        mentions = self.session.scalars(select(EventMention)).all()
        known_mentions = {mention.mention_id for mention in mentions}
        claims: list[dict] = []
        watch_items: list[str] = []
        uncertainty_items: list[str] = []
        for event in events:
            assessment = self.session.get(EventAssessment, event.event_id)
            if assessment is None:
                continue
            if _aware(event.last_updated) < moment - window and _aware(event.first_seen) < moment - window:
                continue
            if assessment.abstain and not assessment.promoted:
                uncertainty_items.append(f"{event.title} 校準後置信不足，已棄權（{event.event_id}）")
                continue
            event_mentions = [mention for mention in mentions if mention.event_id == event.event_id]
            claims.append(
                {
                    "text": event.title,
                    "event_ids": [event.event_id],
                    "mention_ids": [mention.mention_id for mention in event_mentions],
                    "signal_urls": [mention.source_url for mention in event_mentions],
                    "priority": float(assessment.priority),
                    "burst": float(assessment.burst_score),
                }
            )
            if float(assessment.priority) >= 0.5:
                watch_items.append(f"{event.title}（{event.event_id}）")
        if model_claims:
            claims.extend(model_claims)
        judged = discriminate(claims, known_mentions, known_events)
        accepted = sorted(judged.accepted, key=lambda item: item.get("priority") or 0, reverse=True)
        if accepted:
            top = accepted[0]
            bottom = f"{top['text']} 是本期優先級最高的已引用訊號"
            event_ids = list(top.get("event_ids") or [])
            changed_lines = [
                f"- {item['text']}（引用：{'、'.join(item.get('event_ids') or [])}）" for item in accepted
            ]
            categories = "\n".join(changed_lines)
        else:
            bottom = "證據不足，本期對實質判斷棄權"
            event_ids = []
            categories = "- 本期沒有通過鑑別的實質陳述"
            changed_lines = ["- 沒有可引用的變化"]
        if judged.rejected:
            uncertainty_items.extend(f"無支撐陳述已排除：{item.get('text', '')}" for item in judged.rejected)
        if not uncertainty_items:
            uncertainty_items.append("本期通過鑑別的陳述尚未發現未引用內容")
        footer = self.settings.herald.get("human_baseline_label", "待人工確認")
        model_assisted = 0
        if model_claims:
            footer = self.settings.herald.get("model_assisted_label", "模型輔助，待人工確認")
            model_assisted = 1
        body = render_brief(
            bottom_line=bottom,
            event_ids=event_ids,
            what_changed="\n".join(changed_lines),
            by_category=categories,
            uncertainties="\n".join(f"- {item}" for item in uncertainty_items),
            watchlist="\n".join(f"- {item}" for item in watch_items) if watch_items else "- 無",
            footer=footer,
        )
        row = BriefRow(
            period=period,
            body=body,
            evidence={"accepted": accepted, "rejected": judged.rejected},
            model_assisted=model_assisted,
            created_at=moment,
        )
        self.session.add(row)
        self.session.flush()
        return row

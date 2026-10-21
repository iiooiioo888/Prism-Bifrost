"""種入一條可複核的虛構港務訊號，涵蓋自動歸併、人工佇列、棄權與突發。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from prism.db.models import CanonicalEvent, Entity, EntityAlias, ProbeRun
from prism.db.session import session_scope
from prism.demo.commerce import seed_commerce
from prism.raven.base import RawItem
from prism.runtime import PrismRuntime


OFFICIAL_TITLE = "北境港務局發布臨時航道管制"
OFFICIAL_BODY = (
    "北境港務局依公開公告對北境航道實施臨時管制，引航船舶改走外側備援航道，"
    "航標更換期間港務電台持續廣播。管制範圍含主航道東段，恢復時間以港務電台為準。"
)


def _item(
    *,
    url: str,
    title: str,
    body: str,
    when: datetime,
    platform: str,
    source_type: str,
    mode: str,
    author: str,
    probe_id: str,
) -> RawItem:
    from prism.bridge.normalize import content_hash

    return RawItem(
        source_url=url,
        upstream_timestamp=when,
        collected_at=when + timedelta(minutes=5),
        platform=platform,
        title=title,
        body=body,
        author=author,
        language="zh",
        acquisition_mode=mode,  # type: ignore[arg-type]
        raw_ref="",
        probe_id=probe_id,
        content_hash=content_hash(body),
        source_type=source_type,
    )


def ensure_entities(runtime: PrismRuntime, now: datetime) -> None:
    with runtime.factory() as session:
        existing = session.scalar(select(func.count()).select_from(Entity))
        if existing:
            session.rollback()
            return
        bureau = Entity(
            name="北境港務局",
            description="負責北境港公開航道公告與引航調度的機構",
            watched=1,
            created_at=now,
        )
        channel = Entity(
            name="北境航道",
            description="北境港主航道與其外側備援航道",
            watched=1,
            created_at=now,
        )
        session.add_all([bureau, channel])
        session.flush()
        session.add_all(
            [
                EntityAlias(entity_id=bureau.entity_id, alias="NBPA"),
                EntityAlias(entity_id=bureau.entity_id, alias="北境港務"),
                EntityAlias(entity_id=channel.entity_id, alias="北境主航道"),
            ]
        )
        session.commit()


def build_story(now: datetime) -> list[RawItem]:
    items: list[RawItem] = []
    for day in range(7, 0, -1):
        when = now - timedelta(days=day, minutes=30)
        items.append(
            _item(
                url=f"https://port-authority.example/notices/NB-1842/d{day}",
                title=OFFICIAL_TITLE,
                body=(
                    f"{OFFICIAL_BODY}第 {day} 日港務電台複誦公告編號 NB-1842，"
                    f"當日引航窗口為第 {day} 個白晝時段，外側備援航道保持開放。"
                ),
                when=when,
                platform="rss",
                source_type="web",
                mode="live",
                author="北境港務局",
                probe_id="rss",
            )
        )
    spike = [
        ("https://marine-wire.example/reports/nb-1842-live", "web", "rss", "海事線報", "海事線報轉述"),
        ("https://gazette.example/bulletins/nb-1842", "web", "rss", "公報室", "公報室轉載"),
        ("https://feeds.example/items/nb-1842", "news", "rss", "航運簡報", "航運簡報摘記"),
        ("https://port-authority.example/notices/NB-1842/update", "web", "rss", "北境港務局", "港務局更新"),
        ("https://marine-wire.example/journals/nb-1842-note", "academic", "arxiv", "航道研究室", "研究室紀要"),
    ]
    for index, (url, source_type, platform, author, marker) in enumerate(spike):
        items.append(
            _item(
                url=url,
                title=OFFICIAL_TITLE,
                body=(
                    f"{OFFICIAL_BODY}{marker}第 {index} 號補充：公告編號 NB-1842 仍在生效，"
                    "外側備援航道持續承接改道船舶，航標更換尚未完成。"
                ),
                when=now - timedelta(minutes=50 - index * 5),
                platform=platform,
                source_type=source_type,
                mode="live",
                author=author,
                probe_id="rss",
            )
        )
    items.append(
        _item(
            url="https://harbor-echo.example/notes/open-day",
            title=OFFICIAL_TITLE,
            body="北境港務局與北境航道出現在開放日節目單，當天安排樂團、攤位、導覽與餐廳，沒有引航廣播，也沒有航標作業。",
            when=now - timedelta(minutes=12),
            platform="rss",
            source_type="web",
            mode="live",
            author="港灣回聲",
            probe_id="rss",
        )
    )
    items.append(
        _item(
            url="https://forum.example/posts/hearsay-9",
            title="好像聽說港口今天有點怪",
            body="有人在討論區留言說今天風大，船期可能亂掉，沒有附上任何公告編號或機構名稱。",
            when=now - timedelta(minutes=15),
            platform="reddit",
            source_type="social",
            mode="fallback",
            author="匿名討論",
            probe_id="reddit",
        )
    )
    return items


def ensure_degradation_sample(runtime: PrismRuntime, now: datetime) -> None:
    """在最終成功之前放入加速失敗樣本，讓健康度仍為健康，同時露出劣化速率。"""
    with session_scope(runtime.factory) as session:
        marked = session.scalar(select(func.count()).select_from(ProbeRun).where(ProbeRun.error == "劣化速率樣本"))
        if marked:
            return
        base = now - timedelta(minutes=8)
        pattern = [False, False, True, True, False, False, False]
        for index, success in enumerate(pattern):
            session.add(
                ProbeRun(
                    probe_id="rss",
                    platform="rss",
                    backend_id="feedparser",
                    acquisition_mode="live" if success else None,
                    success=1 if success else 0,
                    item_count=0,
                    error=None if success else "劣化速率樣本",
                    created_at=base + timedelta(minutes=index),
                )
            )


def seed(runtime: PrismRuntime | None = None, now: datetime | None = None) -> dict[str, int]:
    runtime = runtime or PrismRuntime()
    moment = now or datetime.now(timezone.utc)
    commerce = seed_commerce(runtime, moment)
    ensure_degradation_sample(runtime, moment)
    with runtime.factory() as session:
        count = session.scalar(select(func.count()).select_from(CanonicalEvent)) or 0
        session.rollback()
    if count:
        return {"events": int(count), "seeded": 0, **commerce}
    ensure_entities(runtime, moment)
    outcome = runtime.ingest_items(build_story(moment), actor="system:seed", now=moment)
    runtime.record_probe(
        probe_id="rss",
        platform="rss",
        backend_id="feedparser",
        acquisition_mode="live",
        success=True,
        item_count=len(outcome.report.stored),
        error=None,
        now=moment,
    )
    runtime.record_probe(
        probe_id="jina_reader",
        platform="jina_reader",
        backend_id="jina-public",
        acquisition_mode="fallback",
        success=True,
        item_count=0,
        error=None,
        now=moment,
    )
    return {
        "events": len({item.event_id for item in outcome.assignments}),
        "stored": len(outcome.report.stored),
        "dropped_hash": outcome.report.dropped_hash,
        "near_duplicates": outcome.report.near_duplicates,
        "seeded": 1,
        **commerce,
    }

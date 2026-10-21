from datetime import datetime, timezone

from sqlalchemy import select

from prism.db.models import EventMention
from prism.db.session import session_scope
from prism.demo.seed import seed


def test_console_contract(runtime, client):
    seed(runtime, datetime(2026, 10, 21, 8, tzinfo=timezone.utc))
    headers = {"Authorization": "Bearer dev-analyst"}
    events = client.get("/api/v1/events?status=active", headers=headers)
    assert events.status_code == 200
    items = events.json()["items"]
    assert items
    abstained = client.get("/api/v1/events?abstain=true", headers=headers).json()["items"]
    assert abstained
    assert all(item["abstain"] for item in abstained)
    detail = client.get(f"/api/v1/events/{items[0]['event_id']}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert "mentions" in body and "metrics" in body and "entities" in body
    mentions = client.get(f"/api/v1/events/{items[0]['event_id']}/mentions", headers=headers)
    assert mentions.status_code == 200
    metrics = client.get(f"/api/v1/events/{items[0]['event_id']}/metrics", headers=headers)
    assert metrics.json()["items"]
    alerts = client.get("/api/v1/alerts", headers=headers).json()["items"]
    assert alerts
    assert alerts[0]["trigger_rules"]
    assert alerts[0]["signal_urls"]
    probes = client.get("/api/v1/health/probes", headers=headers).json()["items"]
    by_id = {item["probe_id"]: item for item in probes}
    assert by_id["reddit"]["enabled"] is False
    assert by_id["reddit"]["status"] == "down"
    assert by_id["jina_reader"]["status"] == "degraded"
    assert by_id["rss"]["status"] == "healthy"
    dashboard = client.get("/api/v1/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["mention_series"]
    brief = client.post("/api/v1/briefs", json={"period": "day"}, headers=headers)
    assert brief.status_code == 200
    fetched = client.get(f"/api/v1/briefs/{brief.json()['brief_id']}", headers=headers)
    assert "核心判斷" in fetched.json()["body"]
    assert "待人工確認" in fetched.json()["body"]
    queue = client.get("/api/v1/review/queue", headers=headers).json()["suggestions"]
    assert queue
    mention_id = body["mentions"][0]["mention_id"]
    event_before = body["mentions"][0]["event_id"]
    labeled = client.post(
        f"/api/v1/mentions/{mention_id}/label",
        json={"label": "uncertain", "note": "時間線仍需對一下公告原文"},
        headers=headers,
    )
    assert labeled.status_code == 200
    with session_scope(runtime.factory) as session:
        mention = session.get(EventMention, mention_id)
        assert mention.event_id == event_before
    anonymous = client.get("/api/v1/events")
    assert anonymous.status_code == 401


def test_split_and_erasure(runtime, client):
    seed(runtime, datetime(2026, 10, 21, 8, tzinfo=timezone.utc))
    headers = {"Authorization": "Bearer dev-analyst", "X-Confirm": "yes"}
    events = client.get("/api/v1/events?status=active", headers={"Authorization": "Bearer dev-analyst"}).json()["items"]
    target = next(item for item in events if item["mention_count"] >= 2 and not item["abstain"])
    detail = client.get(f"/api/v1/events/{target['event_id']}", headers={"Authorization": "Bearer dev-analyst"}).json()
    mention_id = detail["mentions"][0]["mention_id"]
    split = client.post(
        f"/api/v1/events/{target['event_id']}/split",
        json={"mention_ids": [mention_id], "reason": "這條是後續更新", "title": "北境航道後續更新"},
        headers=headers,
    )
    assert split.status_code == 200
    created = client.get(f"/api/v1/events/{split.json()['event_id']}", headers={"Authorization": "Bearer dev-analyst"})
    assert created.status_code == 200
    assert created.json()["mentions"][0]["link_method"] == "human"
    admin = {"Authorization": "Bearer dev-admin", "X-Confirm": "yes"}
    author = created.json()["mentions"][0]["author"]
    removed = client.post("/api/v1/compliance/erasure", json={"author": author, "reason": "保留期屆滿"}, headers=admin)
    assert removed.status_code == 200
    assert removed.json()["removed"]
    with session_scope(runtime.factory) as session:
        left = session.scalars(select(EventMention).where(EventMention.author == author)).all()
        assert left == []

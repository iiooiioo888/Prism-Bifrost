from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from prism.bridge.normalize import content_hash, normalize_url, simhash, simhash_similarity
from prism.db.models import AuditLog, CanonicalEvent, EventMention, MergeSuggestion
from prism.db.session import session_scope
from prism.demo.seed import seed
from prism.llm import gateway as gateway_module
from prism.llm.validate import discriminate, parse_json_with_retry, validate_merge_suggestion
from prism.raven.base import Probe, RawItem
from prism.raven.compliance import ComplianceError, SitePolicy
from prism.raven.health import classify_health
from prism.raven.router import Backend, ProbeRouter, ProbeUnavailable
from prism.weave import metrics as metrics_module


def test_url_normalization_strips_tracking_and_fragment():
    left = normalize_url("HTTPS://News.Example.com/a/?utm_source=x&b=2&fbclid=z#top")
    right = normalize_url("https://news.example.com/a?b=2")
    assert left == right


def test_compliance_blocks_login_and_unknown_domain():
    policy = SitePolicy.from_config(
        {
            "defaults": {"allow_fetch": False, "respect_robots": True},
            "blocked_path_fragments": ["/login", "/captcha"],
            "sites": [{"domain": "feeds.example", "allow_fetch": True, "respect_robots": True}],
        }
    )
    with pytest.raises(ComplianceError):
        policy.assert_allowed("https://feeds.example/login")
    with pytest.raises(ComplianceError):
        policy.assert_allowed("https://unknown.example/post")
    policy.assert_allowed("https://feeds.example/items/1")


def test_router_marks_fallback_and_records_total_failure():
    class OkProbe(Probe):
        platform = "rss"

        def __init__(self, probe_id: str, fail: bool = False):
            self.probe_id = probe_id
            self.fail = fail

        async def fetch(self, query_or_feed: str, since: datetime, limit: int = 100):
            if self.fail:
                raise RuntimeError("後端不可用")
            return [
                RawItem(
                    source_url="https://feeds.example/a",
                    upstream_timestamp=since,
                    collected_at=since,
                    platform="rss",
                    title="t",
                    body="b",
                    author=None,
                    language="zh",
                    acquisition_mode="live",
                    raw_ref="raw/x",
                    probe_id=self.probe_id,
                    content_hash=content_hash("b"),
                )
            ]

        async def health(self):
            raise NotImplementedError

    now = datetime.now(timezone.utc)

    async def fallback_case():
        router = ProbeRouter(
            [
                Backend(OkProbe("primary", fail=True), 1, "primary"),
                Backend(OkProbe("backup"), 2, "backup"),
            ]
        )
        return await router.fetch("feed", now)

    import asyncio

    result = asyncio.run(fallback_case())
    assert result.acquisition_mode == "fallback"
    assert result.items[0].acquisition_mode == "fallback"
    assert result.attempts[0].success is False

    async def down_case():
        router = ProbeRouter([Backend(OkProbe("primary", fail=True), 1, "primary")])
        await router.fetch("feed", now)

    with pytest.raises(ProbeUnavailable):
        asyncio.run(down_case())


def test_health_degradation_is_explicit():
    now = datetime.now(timezone.utc)
    degraded = classify_health(
        probe_id="rss",
        platform="rss",
        last_success_at=now,
        consecutive_failures=0,
        fallback_ratio_24h=0.4,
        mean_lag_seconds=None,
        last_mode="fallback",
        all_backends_failed=False,
        now=now,
    )
    assert degraded.status == "degraded"
    assert degraded.detail
    down = classify_health(
        probe_id="rss",
        platform="rss",
        last_success_at=None,
        consecutive_failures=3,
        fallback_ratio_24h=0,
        mean_lag_seconds=None,
        last_mode=None,
        all_backends_failed=True,
        now=now,
    )
    assert down.status == "down"


def test_near_duplicate_similarity_threshold():
    left = simhash("北境港務局對北境航道實施臨時管制，公告編號 NB-1842。")
    right = simhash("北境港務局對北境航道實施臨時管制，公告編號 NB-1842。")
    assert simhash_similarity(left, right) == pytest.approx(1)
    other = simhash("這是一篇關於城市音樂會、餐廳營業與旅遊導覽的完全不同文章，沒有航運內容。")
    assert simhash_similarity(left, other) < 0.95


def test_seed_story_keeps_fact_and_judgment_apart(runtime):
    now = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
    result = seed(runtime, now)
    assert result["seeded"] == 1
    assert result["stored"] > 0
    with session_scope(runtime.factory) as session:
        events = session.scalars(select(CanonicalEvent)).all()
        suggestions = session.scalars(select(MergeSuggestion).where(MergeSuggestion.status == "pending")).all()
        mentions = session.scalars(select(EventMention)).all()
        assert suggestions, "中置信必須進入人工佇列，而不是自動合併"
        for suggestion in suggestions:
            left_ids = {item.mention_id for item in mentions if item.event_id == suggestion.left_event_id}
            right_ids = {item.mention_id for item in mentions if item.event_id == suggestion.right_event_id}
            assert suggestion.left_mention_id in left_ids
            assert suggestion.right_mention_id in right_ids
            assert left_ids.isdisjoint(right_ids)
        official = [event for event in events if "管制" in event.title or event.title == "北境港務局發布臨時航道管制"]
        assert official
        from prism.db.models import AlertRow, EventAssessment

        assessments = session.scalars(select(EventAssessment)).all()
        assert any(item.abstain for item in assessments)
        alerts = session.scalars(select(AlertRow)).all()
        assert alerts, "高優先級事件應產生帶規則與網址的告警"
        for alert in alerts:
            assert alert.trigger_rules
            assert alert.signal_urls
            assert alert.abstain == 0
        audits = session.scalars(select(AuditLog)).all()
        assert any(row.action == "weave.suggest_merge" for row in audits)
        assert any(row.action == "weave.auto_merge" or row.action == "weave.near_duplicate_attach" for row in audits)


def test_human_merge_writes_fact_layer_and_audit_is_append_only(runtime, client):
    now = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
    seed(runtime, now)
    with session_scope(runtime.factory) as session:
        suggestion = session.scalars(select(MergeSuggestion).where(MergeSuggestion.status == "pending")).first()
        assert suggestion is not None
        suggestion_id = suggestion.suggestion_id
        target = suggestion.left_event_id
        source = suggestion.right_event_id
    denied = client.post(
        f"/api/v1/review/suggestions/{suggestion_id}/accept",
        json={"reason": "測試"},
        headers={"Authorization": "Bearer dev-viewer"},
    )
    assert denied.status_code == 403
    unconfirmed = client.post(
        f"/api/v1/review/suggestions/{suggestion_id}/accept",
        json={"reason": "測試"},
        headers={"Authorization": "Bearer dev-analyst"},
    )
    assert unconfirmed.status_code == 428
    accepted = client.post(
        f"/api/v1/review/suggestions/{suggestion_id}/accept",
        json={"reason": "人工確認兩篇談的是同一份公告"},
        headers={"Authorization": "Bearer dev-analyst", "X-Confirm": "yes"},
    )
    assert accepted.status_code == 200
    with session_scope(runtime.factory) as session:
        source_event = session.get(CanonicalEvent, source)
        assert source_event.status == "merged"
        moved = session.scalars(select(EventMention).where(EventMention.event_id == target)).all()
        assert any(mention.link_method == "human" for mention in moved)
        with pytest.raises(IntegrityError):
            session.execute(text("UPDATE audit_log SET actor = '竄改'"))
            session.commit()


def test_model_layer_cannot_name_metric_writer():
    source = metrics_module.persist_ready.__doc__ or ""
    assert "唯一寫入" in source
    for module in (gateway_module,):
        text_source = open(module.__file__, encoding="utf-8").read()
        assert "EventMetrics" not in text_source
        assert "persist_ready" not in text_source


def test_citation_gate_and_retry():
    calls = {"n": 0}

    def retry_still_bad():
        calls["n"] += 1
        return "still-not-json"

    with pytest.raises(Exception):
        parse_json_with_retry("not-json", retry_still_bad, max_retries=1)
    assert calls["n"] == 1
    recovered = parse_json_with_retry("not-json", lambda: '{"same_event": true}', max_retries=1)
    assert recovered["same_event"] is True
    draft = validate_merge_suggestion(
        {"same_event": True, "confidence": "high", "supporting_mention_ids": ["missing"]},
        {"m1"},
    )
    assert draft.unsupported is True
    judged = discriminate(
        [
            {"text": "有引用", "event_ids": ["e1"], "mention_ids": ["m1"]},
            {"text": "空引用", "event_ids": [], "mention_ids": []},
            {"text": "矛盾", "event_ids": ["e1"], "contradicted": True},
        ],
        {"m1"},
        {"e1"},
    )
    assert len(judged.accepted) == 1
    assert len(judged.rejected) == 2

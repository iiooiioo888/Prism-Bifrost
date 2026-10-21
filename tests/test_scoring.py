from datetime import datetime, timedelta, timezone

import pytest

from prism.watch.alerts import AlertContractError, build_alert, fired_rules
from prism.watch.burst import burst_score, normalize_burst
from prism.watch.calibrate import calibrate
from prism.watch.priority import DEFAULT_WEIGHTS, PriorityParts, score_priority
from prism.weave.scoring import score_merge
from prism.weave.similarity import MentionView, entity_overlap, rule_features, semantic_features

BANDS = {
    "web": (0.52, 0.68),
    "social": (0.40, 0.57),
    "academic": (0.65, 0.92),
    "news": (0.52, 0.68),
    "video": (0.37, 0.60),
    "code": (0.37, 0.60),
    "corpus": (0.65, 0.92),
    "registry": (0.65, 0.92),
    "market": (0.40, 0.57),
}
MULTIPLIERS = {
    "web": 0.72,
    "social": 0.72,
    "academic": 0.88,
    "news": 0.68,
    "video": 0.65,
    "code": 0.65,
    "corpus": 0.90,
    "registry": 0.90,
    "market": 0.60,
}


def test_merge_formula_and_conservative_band():
    high = score_merge(1, 1, 0.3, True, True)
    assert high.score == pytest.approx(0.4 + 0.4 + 0.06)
    assert high.band == "high"
    # 規則與語義都滿分，但沒有實體重疊時只有 0.80，不得自動歸併。
    almost = score_merge(1, 1, 0, True, True)
    assert almost.score == pytest.approx(0.80)
    assert almost.band == "medium"
    # 分數已達 0.85，但語義未命中，仍然進人工佇列。
    single_channel = score_merge(1, 0.9, 0.8, True, False)
    assert single_channel.score >= 0.85
    assert single_channel.band == "medium"
    low = score_merge(0.2, 0.2, 0.2, False, False)
    assert low.band == "low"


def test_rule_and_semantic_channels_are_distinct():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    left = MentionView("同一標題", "甲文章完全不同的正文內容，討論港口開放日與餐廳", "甲", "https://a.example/1", now, frozenset())
    right = MentionView("同一標題", "乙文章寫的是航標更換與引航改道，句子幾乎沒有重疊", "乙", "https://b.example/2", now, frozenset())
    rule_score, rule_hit, _title = rule_features(left, right)
    semantic_score, semantic_hit = semantic_features(left, right, 0.60)
    assert rule_hit is True
    assert rule_score == pytest.approx(1)
    assert semantic_hit is False
    assert semantic_score < 0.60


def test_entity_overlap_empty_is_zero():
    assert entity_overlap(frozenset(), frozenset({"a"})) == 0
    assert entity_overlap(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)


def test_calibration_matches_spec_formula():
    social_fallback = calibrate("social", "fallback", BANDS, MULTIPLIERS)
    expected = ((0.40 + 0.57) / 2) * 0.72 * 0.90
    assert social_fallback.calibrated_confidence == pytest.approx(expected)
    assert social_fallback.abstain is True
    web = calibrate("web", "live", BANDS, MULTIPLIERS)
    assert web.calibrated_confidence == pytest.approx(((0.52 + 0.68) / 2) * 0.72)
    assert web.acquisition_multiplier == 1
    assert web.abstain is False
    academic = calibrate("academic", "live", BANDS, MULTIPLIERS)
    assert academic.source_multiplier == 0.88


def test_burst_uses_mad_constant_and_empty_baseline_abstains_from_claim():
    result = burst_score(8, [1, 1, 1, 1, 1, 1, 1], eps=1e-6)
    assert result.baseline_median == 1
    assert result.mad == 0
    assert result.score == pytest.approx(7 / 1e-6)
    assert result.is_burst is True
    quiet = burst_score(4, [1, 2, 3, 4, 5, 6, 7])
    assert quiet.score < 3
    assert burst_score(10, []).score == 0
    assert normalize_burst(3) == 1
    assert normalize_burst(1.5) == pytest.approx(0.5)
    assert normalize_burst(-2) == 0


def test_priority_weights_and_threshold():
    assert DEFAULT_WEIGHTS == (0.20, 0.25, 0.20, 0.20, 0.10, 0.05)
    result = score_priority(
        PriorityParts(
            source_diversity_norm=1,
            calibrated_confidence_avg=1,
            burst_score_norm=1,
            entity_relevance=1,
            cross_domain_support=1,
            unsupported_ratio=0,
        )
    )
    assert result.priority == pytest.approx(0.95)
    assert result.high_priority is True
    penalized = score_priority(
        PriorityParts(0, 0, 0, 0, 0, 1),
    )
    assert penalized.priority == pytest.approx(-0.05)
    assert penalized.high_priority is False


def test_alert_contract_rejects_black_box():
    now = datetime.now(timezone.utc)
    with pytest.raises(AlertContractError):
        build_alert(
            alert_id="a",
            event_id="e",
            priority=0.9,
            trigger_rules=[],
            calibrated_confidence=0.5,
            abstain=False,
            signal_urls=["https://example.com"],
            summary="x",
            created_at=now,
        )
    with pytest.raises(AlertContractError):
        build_alert(
            alert_id="a",
            event_id="e",
            priority=0.9,
            trigger_rules=["priority>=0.75"],
            calibrated_confidence=0.5,
            abstain=False,
            signal_urls=[],
            summary="x",
            created_at=now,
        )
    with pytest.raises(AlertContractError):
        build_alert(
            alert_id="a",
            event_id="e",
            priority=0.9,
            trigger_rules=["priority>=0.75"],
            calibrated_confidence=0.2,
            abstain=True,
            signal_urls=["https://example.com"],
            summary="x",
            created_at=now,
        )
    rules = fired_rules(
        priority=0.82,
        alert_threshold=0.75,
        burst_score=4,
        burst_threshold=3,
        entity_relevance=0.91,
        source_diversity=0.2,
        calibrated_confidence=0.4,
        cross_domain_support=0.2,
    )
    assert "burst_score>3.0" in rules
    assert "entity_relevance>0.8" in rules


def test_author_window_rule():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    left = MentionView("完全不同的甲標題", "aaa", "同一作者", "https://a.example/1", now, frozenset())
    right = MentionView("完全不同的乙標題", "bbb", "同一作者", "https://b.example/2", now + timedelta(hours=2), frozenset())
    score, hit, _title = rule_features(left, right)
    assert hit is True
    assert score == pytest.approx(0.9)

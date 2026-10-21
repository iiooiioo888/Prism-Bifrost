"""告警物件。缺少觸發規則或原始網址時拒絕生成。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class AlertContractError(ValueError):
    """違反告警硬約束。"""


@dataclass(frozen=True)
class AlertDraft:
    alert_id: str
    event_id: str
    priority: float
    trigger_rules: tuple[str, ...]
    calibrated_confidence: float
    abstain: bool
    signal_urls: tuple[str, ...]
    summary: str
    created_at: datetime

    def as_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "event_id": self.event_id,
            "priority": self.priority,
            "trigger_rules": list(self.trigger_rules),
            "calibrated_confidence": self.calibrated_confidence,
            "abstain": self.abstain,
            "signal_urls": list(self.signal_urls),
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
        }


def build_alert(
    *,
    alert_id: str,
    event_id: str,
    priority: float,
    trigger_rules: list[str],
    calibrated_confidence: float,
    abstain: bool,
    signal_urls: list[str],
    summary: str,
    created_at: datetime,
) -> AlertDraft:
    rules = tuple(rule for rule in trigger_rules if rule.strip())
    urls = tuple(dict.fromkeys(url for url in signal_urls if url.strip()))
    if abstain:
        raise AlertContractError("棄權提及不得進入告警")
    if not rules:
        raise AlertContractError("告警必須攜帶觸發規則")
    if not urls:
        raise AlertContractError("告警必須攜帶原始訊號網址")
    return AlertDraft(
        alert_id=alert_id,
        event_id=event_id,
        priority=priority,
        trigger_rules=rules,
        calibrated_confidence=calibrated_confidence,
        abstain=False,
        signal_urls=urls,
        summary=summary,
        created_at=created_at,
    )


def fired_rules(
    *,
    priority: float,
    alert_threshold: float,
    burst_score: float,
    burst_threshold: float,
    entity_relevance: float,
    source_diversity: float,
    calibrated_confidence: float,
    cross_domain_support: float,
) -> list[str]:
    rules: list[str] = []
    if priority >= alert_threshold:
        rules.append(f"priority>={alert_threshold:.2f}")
    if burst_score > burst_threshold:
        rules.append(f"burst_score>{burst_threshold:.1f}")
    if entity_relevance > 0.8:
        rules.append("entity_relevance>0.8")
    if source_diversity >= 0.5:
        rules.append("source_diversity>=0.5")
    if calibrated_confidence >= 0.6:
        rules.append("calibrated_confidence>=0.6")
    if cross_domain_support >= 0.5:
        rules.append("cross_domain_support>=0.5")
    return rules

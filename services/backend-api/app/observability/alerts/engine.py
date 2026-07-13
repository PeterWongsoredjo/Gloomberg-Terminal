"""
processes system events and slo breaches to trigger deduped and routed alert actions
restricts repeat alerts using cooldown thresholds to avoid flood logs
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability import operator_log
from app.observability.alerts.config import AlertRule, get_rules
from app.observability.slo.engine import Breach


@dataclass(frozen=True)
class Alert:
    alert_id: str
    severity: str
    dedup_key: str
    payload: dict[str, Any]
    route: tuple[str, ...]
    raised_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class _Safe(dict[str, Any]):

    def __missing__(self, key: str) -> str:
        return ""


class AlertEngine:

    def __init__(self, rules: list[AlertRule] | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        resolved = rules if rules is not None else get_rules()
        self._by_slo = {rule.trigger: rule for rule in resolved if rule.is_slo}
        self._by_event = {rule.trigger: rule for rule in resolved if not rule.is_slo}
        self._clock = clock
        self._last_seen: dict[str, float] = {}

    def from_breaches(self, breaches: list[Breach], context: dict[str, Any]) -> list[Alert]:
        alerts: list[Alert] = []
        for breach in breaches:
            rule = self._by_slo.get(breach.slo_id)
            if rule is None:
                continue
            merged = {
                **context,
                "dataset": breach.dataset,
                "run_id": breach.run_id,
                "provider": breach.provider,
                breach.measure: breach.value,
            }
            alert = self._raise(rule, merged)
            if alert is not None:
                alerts.append(alert)
        return alerts

    def raise_event(self, event_name: str, context: dict[str, Any]) -> Alert | None:
        rule = self._by_event.get(event_name)
        if rule is None:
            return None
        return self._raise(rule, context)

    def _raise(self, rule: AlertRule, context: dict[str, Any]) -> Alert | None:
        dedup = rule.dedup_key.format_map(_Safe(context))
        now = self._clock()
        last = self._last_seen.get(dedup)
        if last is not None and now - last < rule.cooldown_minutes * 60:
            return None
        self._last_seen[dedup] = now
        payload = {key: context.get(key) for key in rule.payload_includes}
        alert = Alert(rule.alert_id, rule.severity, dedup, payload, rule.route)
        if "operator_log" in rule.route:
            operator_log.log_alert({"alert_id": alert.alert_id, "severity": alert.severity, "payload": payload})
        return alert


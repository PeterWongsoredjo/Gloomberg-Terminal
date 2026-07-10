"""
evaluates service level objectives metrics using calendar awareness
tracks if datasets are fresh and identifies breaches of latency and token thresholds
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.observability.calendar import SessionCalendar, get_calendar
from app.observability.clock import ensure_utc
from app.observability.slo.config import SloRule, get_rules


@dataclass
class SloSample:
    trade_date: date
    now_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    freshness: dict[str, datetime] = field(default_factory=dict)  # dataset -> data_as_of (UTC)
    coverage_ratio: float | None = None
    gold_promotion_ok: bool | None = None
    run_latency_ms: int | None = None
    consumed_tokens: int | None = None
    run_id: str | None = None
    provider_quota: dict[str, float] = field(default_factory=dict)  # provider -> quota_pct


@dataclass(frozen=True)
class Breach:
    slo_id: str
    severity: str
    measure: str
    value: float | bool | None
    threshold: float | bool | None
    dataset: str | None = None
    run_id: str | None = None
    provider: str | None = None


class SloEngine:

    def __init__(self, rules: list[SloRule] | None = None, calendar: SessionCalendar | None = None) -> None:
        self._rules = rules if rules is not None else get_rules()
        self._calendar = calendar or get_calendar()

    def freshness_met(self, dataset: str, data_as_of: datetime | None, trade_date: date, now_utc: datetime | None = None) -> bool:
        now = ensure_utc(now_utc) or datetime.now(UTC)
        rule = self._freshness_rule(dataset)
        if rule is None or not self._freshness_due(trade_date, now, rule):
            return True  # not applicable: non-trading day or before the EOD deadline
        landed = ensure_utc(data_as_of)
        return landed is not None and landed >= self._calendar.close_datetime_utc(trade_date)

    def evaluate(self, sample: SloSample) -> list[Breach]:
        sample.now_utc = ensure_utc(sample.now_utc) or datetime.now(UTC)
        sample.freshness = {k: v for k, v in ((k, ensure_utc(v)) for k, v in sample.freshness.items()) if v is not None}
        breaches: list[Breach] = []
        for rule in self._rules:
            breaches.extend(self._evaluate_rule(rule, sample))
        return breaches

    def _evaluate_rule(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        handler = {
            "data_as_of_age": self._check_freshness,
            "coverage_ratio": self._check_coverage,
            "gold_promotion_ok": self._check_promotion,
            "run_latency_ms": self._check_latency,
            "consumed_tokens": self._check_tokens,
            "quota_pct": self._check_quota,
        }.get(rule.measure)
        return handler(rule, sample) if handler else []

    def _freshness_rule(self, dataset: str) -> SloRule | None:
        for rule in self._rules:
            if rule.measure == "data_as_of_age" and rule.applies_when.get("dataset") == dataset:
                return rule
        return None

    def _freshness_due(self, trade_date: date, now: datetime, rule: SloRule) -> bool:
        if rule.calendar_aware and not self._calendar.is_trading_day(trade_date):
            return False
        window = timedelta(minutes=rule.target["max_age_after_close_minutes"])
        return now >= self._calendar.close_datetime_utc(trade_date) + window

    def _check_freshness(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        dataset = rule.applies_when.get("dataset", "")
        if not self._freshness_due(sample.trade_date, sample.now_utc, rule):
            return []
        data_as_of = sample.freshness.get(dataset)
        close = self._calendar.close_datetime_utc(sample.trade_date)
        if data_as_of is not None and data_as_of >= close:
            return []
        age = None if data_as_of is None else (sample.now_utc - data_as_of).total_seconds() / 60
        return [Breach(rule.slo_id, rule.severity, rule.measure, age, rule.target["max_age_after_close_minutes"], dataset=dataset)]

    def _check_coverage(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        if rule.calendar_aware and not self._calendar.is_trading_day(sample.trade_date):
            return []
        floor = float(rule.target["min"])
        if sample.coverage_ratio is None or sample.coverage_ratio >= floor:
            return []
        return [Breach(rule.slo_id, rule.severity, rule.measure, sample.coverage_ratio, floor)]

    def _check_promotion(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        if rule.calendar_aware and not self._calendar.is_trading_day(sample.trade_date):
            return []
        if sample.gold_promotion_ok is None or sample.gold_promotion_ok is True:
            return []
        return [Breach(rule.slo_id, rule.severity, rule.measure, False, True)]

    def _check_latency(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        target = float(rule.target["p95_ms"])
        if sample.run_latency_ms is None or sample.run_latency_ms <= target:
            return []
        return [Breach(rule.slo_id, rule.severity, rule.measure, sample.run_latency_ms, target, run_id=sample.run_id)]

    def _check_tokens(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        cap = float(rule.target["max"])
        if sample.consumed_tokens is None or sample.consumed_tokens <= cap:
            return []
        return [Breach(rule.slo_id, rule.severity, rule.measure, sample.consumed_tokens, cap, run_id=sample.run_id)]

    def _check_quota(self, rule: SloRule, sample: SloSample) -> list[Breach]:
        ceiling = float(rule.target["max"])
        breaches = []
        for provider, pct in sample.provider_quota.items():
            if pct >= ceiling:
                breaches.append(Breach(rule.slo_id, rule.severity, rule.measure, pct, ceiling, provider=provider))
        return breaches


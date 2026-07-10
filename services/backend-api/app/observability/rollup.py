"""
consolidates daily metrics into a single telemetry row per trade date
aggregates pipeline coverage, freshness, model runs, and service level alerts
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from app.observability.alerts.engine import Alert, AlertEngine
from app.observability.calendar import SessionCalendar, get_calendar
from app.observability.clock import ensure_utc
from app.observability.cost import CostModel, get_cost_model
from app.observability.heartbeat import Heartbeat
from app.observability.slo.engine import Breach, SloEngine, SloSample

logger = logging.getLogger("gloomberg.observability.rollup")

_PROVIDERS = ("groq", "gemini")
_PROMOTION_OK = {"SUCCEEDED", "SUCCESS", "RELEASED", "PROMOTED", "OK", "COMPLETED"}

_DDL = """
create schema if not exists observability;

create table if not exists observability.obs_telemetry_rollup (
    trade_date date primary key,
    session_state text not null,
    coverage_ratio double precision,
    missing_ticker_count int,
    quarantine_row_count int,
    dbt_tests_passed int,
    dbt_tests_failed int,
    gold_promoted_at timestamptz,
    gold_promotion_ok boolean,
    data_as_of timestamptz not null,
    slo_breaches jsonb not null default '[]'::jsonb,
    active_alerts jsonb not null default '[]'::jsonb,
    llm_runs int not null default 0,
    llm_runs_degraded int not null default 0,
    total_tokens bigint not null default 0,
    notional_cost double precision not null default 0,
    quota_pct_groq double precision not null default 0,
    quota_pct_gemini double precision not null default 0,
    breaker_state_groq text,
    breaker_state_gemini text,
    low_confidence_artifact_count int not null default 0,
    live_prompt_versions jsonb not null default '{}'::jsonb,
    quality_flags jsonb not null default '[]'::jsonb,
    refreshed_at timestamptz not null default now()
);
"""


@dataclass
class RollupRow:
    trade_date: date
    session_state: str
    data_as_of: datetime
    coverage_ratio: float | None = None
    missing_ticker_count: int | None = None
    quarantine_row_count: int | None = None
    dbt_tests_passed: int | None = None
    dbt_tests_failed: int | None = None
    gold_promoted_at: datetime | None = None
    gold_promotion_ok: bool | None = None
    slo_breaches: list[dict[str, Any]] = field(default_factory=list)
    active_alerts: list[dict[str, Any]] = field(default_factory=list)
    llm_runs: int = 0
    llm_runs_degraded: int = 0
    total_tokens: int = 0
    notional_cost: float = 0.0
    quota_pct_groq: float = 0.0
    quota_pct_gemini: float = 0.0
    breaker_state_groq: str | None = None
    breaker_state_gemini: str | None = None
    low_confidence_artifact_count: int = 0
    live_prompt_versions: dict[str, str] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)


async def setup(pool: asyncpg.Pool) -> None:
    await pool.execute(_DDL)


class RollupBuilder:
    def __init__(
        self,
        *,
        slo_engine: SloEngine | None = None,
        alert_engine: AlertEngine | None = None,
        cost_model: CostModel | None = None,
        calendar: SessionCalendar | None = None,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._slo = slo_engine or SloEngine()
        self._alerts = alert_engine or AlertEngine()
        self._cost = cost_model or get_cost_model()
        self._calendar = calendar or get_calendar()
        self._heartbeat = heartbeat

    async def refresh(self, pool: asyncpg.Pool, trade_date: date, *, now_utc: datetime | None = None) -> RollupRow:
        """regenerates daily summary metrics and updates the database row"""
        now = now_utc or datetime.now(UTC)
        pipeline_result, llm, live_versions = await asyncio.gather(
            self._pipeline_measures(pool, trade_date),
            self._llm_measures(pool, trade_date),
            self._live_versions(pool),
        )
        measures, data_as_of, freshness = pipeline_result

        row = RollupRow(
            trade_date=trade_date,
            session_state=self._calendar.market_state(trade_date, now).value,
            data_as_of=ensure_utc(data_as_of) or now,
            live_prompt_versions=live_versions,
            **measures,
            **llm,
        )
        sample = self._build_sample(row, freshness, now)
        breaches = self._slo.evaluate(sample)
        alerts = self._raise_alerts(breaches, row)
        row.slo_breaches = [self._breach_json(b) for b in breaches]
        row.active_alerts = [self._alert_json(a) for a in alerts]
        row.quality_flags = self._quality_flags(breaches, row)

        await self._upsert(pool, row)
        return row

    async def _pipeline_measures(
        self, pool: asyncpg.Pool, trade_date: date
    ) -> tuple[dict[str, Any], datetime | None, dict[str, datetime]]:
        """fetches pipeline metrics like data coverage and quarantine counts"""
        measures: dict[str, Any] = {
            "coverage_ratio": None, "missing_ticker_count": None, "quarantine_row_count": None,
            "dbt_tests_passed": None, "dbt_tests_failed": None, "gold_promoted_at": None,
            "gold_promotion_ok": None,
        }
        data_as_of: datetime | None = None
        freshness: dict[str, datetime] = {}
        rows = await self._safe_fetch(
            pool,
            """
            select source, dataset, coverage_ratio, missing_tickers, quarantine_count, data_as_of
            from public.agg_pipeline_telemetry where trade_date = $1
            """,
            trade_date,
        )
        if rows:
            freshness = {f"{r['source']}.{r['dataset']}": r["data_as_of"] for r in rows if r["data_as_of"]}
            daily = next((r for r in rows if r["dataset"] == "daily_trade"), rows[0])
            measures["coverage_ratio"] = daily["coverage_ratio"]
            measures["missing_ticker_count"] = _array_len(daily["missing_tickers"])
            measures["quarantine_row_count"] = sum(int(r["quarantine_count"] or 0) for r in rows)
            data_as_of = max((r["data_as_of"] for r in rows if r["data_as_of"]), default=None)
        await self._promotion(pool, trade_date, measures)
        return measures, data_as_of, freshness

    async def _promotion(self, pool: asyncpg.Pool, trade_date: date, into: dict[str, Any]) -> None:
        rows = await self._safe_fetch(
            pool,
            """
            select phase, status, ended_at, event from orchestration.run_state_event
            where trade_date = $1 order by id
            """,
            trade_date,
        )
        for r in rows:
            event = _as_dict(r["event"])
            gate = event.get("gate") or {}
            if "tests_passed" in gate:
                into["dbt_tests_passed"] = int(gate.get("tests_passed") or 0)
                into["dbt_tests_failed"] = int(gate.get("tests_failed") or 0)
            if r["phase"] in {"promote", "publish"}:
                into["gold_promoted_at"] = r["ended_at"]
                into["gold_promotion_ok"] = str(r["status"]).upper() in _PROMOTION_OK

    async def _llm_measures(self, pool: asyncpg.Pool, trade_date: date) -> dict[str, Any]:
        measures: dict[str, Any] = {
            "llm_runs": 0, "llm_runs_degraded": 0, "total_tokens": 0, "notional_cost": 0.0,
            "quota_pct_groq": 0.0, "quota_pct_gemini": 0.0,
            "breaker_state_groq": None, "breaker_state_gemini": None,
            "low_confidence_artifact_count": 0,
        }
        runs = await self._safe_fetch(
            pool,
            "select status, consumed_tokens from agentic.agent_run where trade_date = $1",
            trade_date,
        )
        measures["llm_runs"] = len(runs)
        measures["llm_runs_degraded"] = sum(1 for r in runs if str(r["status"]).upper() == "DEGRADED")
        measures["total_tokens"] = sum(int(r["consumed_tokens"] or 0) for r in runs)

        artifacts = await self._safe_fetch(
            pool,
            """
            select a.provider, a.token_usage, a.quality_flags
            from agentic.agent_artifact a join agentic.agent_run r on a.run_id = r.run_id
            where r.trade_date = $1
            """,
            trade_date,
        )
        for a in artifacts:
            usage = _as_dict(a["token_usage"])
            measures["notional_cost"] += self._cost.notional_cost(
                str(a["provider"]), int(usage.get("prompt", 0)), int(usage.get("completion", 0))
            )
            if "LLM_LOW_CONFIDENCE" in _as_list(a["quality_flags"]):
                measures["low_confidence_artifact_count"] += 1

        await self._provider_health(pool, measures)
        return measures

    async def _provider_health(self, pool: asyncpg.Pool, into: dict[str, Any]) -> None:
        """reads model provider health metrics like circuit breaker status"""
        rows = await self._safe_fetch(pool, "select provider, breaker_state, rpd_consumed from agentic.provider_health")
        health = {str(r["provider"]): r for r in rows}
        for provider in _PROVIDERS:
            record = health.get(provider)
            if record is None:
                continue
            into[f"breaker_state_{provider}"] = record["breaker_state"]
            into[f"quota_pct_{provider}"] = self._cost.quota_pct(provider, int(record["rpd_consumed"] or 0), 0)

    async def _live_versions(self, pool: asyncpg.Pool) -> dict[str, str]:
        """gets current active prompt versions"""
        rows = await self._safe_fetch(
            pool, "select objective, version from observability.prompt_version where state = 'LIVE'"
        )
        return {str(r["objective"]): str(r["version"]) for r in rows}

    def _build_sample(self, row: RollupRow, freshness: dict[str, datetime], now: datetime) -> SloSample:
        """packs metrics into a structured sample for slo checks"""
        return SloSample(
            trade_date=row.trade_date,
            now_utc=now,
            freshness=freshness,
            coverage_ratio=row.coverage_ratio,
            gold_promotion_ok=row.gold_promotion_ok,
            consumed_tokens=row.total_tokens or None,
            provider_quota={p: getattr(row, f"quota_pct_{p}") for p in _PROVIDERS},
        )

    def _raise_alerts(self, breaches: list[Breach], row: RollupRow) -> list[Alert]:
        """triggers alert events if any slos are violated or heartbeat is silent"""
        context = {"trade_date": row.trade_date.isoformat(), "missing_tickers": row.missing_ticker_count}
        alerts = self._alerts.from_breaches(breaches, context)
        if self._heartbeat is not None and self._heartbeat.is_stale():
            dead = self._alerts.raise_event(
                "heartbeat_dead", {"source": "observability", "silent_seconds": self._heartbeat.silent_seconds()}
            )
            if dead is not None:
                alerts.append(dead)
        return alerts

    def _quality_flags(self, breaches: list[Breach], row: RollupRow) -> list[str]:
        flags: set[str] = set()
        for breach in breaches:
            if breach.measure == "coverage_ratio":
                flags.add("COVERAGE_GAP")
            if breach.measure == "data_as_of_age":
                flags.add("STALE")
        if row.quarantine_row_count:
            flags.add("SCHEMA_DRIFT_QUARANTINE")
        if row.low_confidence_artifact_count:
            flags.add("LLM_LOW_CONFIDENCE")
        return sorted(flags)

    async def _upsert(self, pool: asyncpg.Pool, row: RollupRow) -> None:
        await pool.execute(
            """
            insert into observability.obs_telemetry_rollup (
                trade_date, session_state, coverage_ratio, missing_ticker_count, quarantine_row_count,
                dbt_tests_passed, dbt_tests_failed, gold_promoted_at, gold_promotion_ok, data_as_of,
                slo_breaches, active_alerts, llm_runs, llm_runs_degraded, total_tokens, notional_cost,
                quota_pct_groq, quota_pct_gemini, breaker_state_groq, breaker_state_gemini,
                low_confidence_artifact_count, live_prompt_versions, quality_flags, refreshed_at
            ) values (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14,$15,$16,$17,$18,$19,$20,
                $21,$22::jsonb,$23::jsonb, now()
            )
            on conflict (trade_date) do update set
                session_state = excluded.session_state,
                coverage_ratio = excluded.coverage_ratio,
                missing_ticker_count = excluded.missing_ticker_count,
                quarantine_row_count = excluded.quarantine_row_count,
                dbt_tests_passed = excluded.dbt_tests_passed,
                dbt_tests_failed = excluded.dbt_tests_failed,
                gold_promoted_at = excluded.gold_promoted_at,
                gold_promotion_ok = excluded.gold_promotion_ok,
                data_as_of = excluded.data_as_of,
                slo_breaches = excluded.slo_breaches,
                active_alerts = excluded.active_alerts,
                llm_runs = excluded.llm_runs,
                llm_runs_degraded = excluded.llm_runs_degraded,
                total_tokens = excluded.total_tokens,
                notional_cost = excluded.notional_cost,
                quota_pct_groq = excluded.quota_pct_groq,
                quota_pct_gemini = excluded.quota_pct_gemini,
                breaker_state_groq = excluded.breaker_state_groq,
                breaker_state_gemini = excluded.breaker_state_gemini,
                low_confidence_artifact_count = excluded.low_confidence_artifact_count,
                live_prompt_versions = excluded.live_prompt_versions,
                quality_flags = excluded.quality_flags,
                refreshed_at = now()
            """,
            row.trade_date, row.session_state, row.coverage_ratio, row.missing_ticker_count,
            row.quarantine_row_count, row.dbt_tests_passed, row.dbt_tests_failed, row.gold_promoted_at,
            row.gold_promotion_ok, row.data_as_of, json.dumps(row.slo_breaches), json.dumps(row.active_alerts),
            row.llm_runs, row.llm_runs_degraded, row.total_tokens, row.notional_cost, row.quota_pct_groq,
            row.quota_pct_gemini, row.breaker_state_groq, row.breaker_state_gemini,
            row.low_confidence_artifact_count, json.dumps(row.live_prompt_versions), json.dumps(row.quality_flags),
        )

    async def _safe_fetch(self, pool: asyncpg.Pool, query: str, *args: Any) -> list[asyncpg.Record]:
        """executes database query safely returning empty list if query fails"""
        try:
            records: list[asyncpg.Record] = await pool.fetch(query, *args)
            return records
        except asyncpg.PostgresError as exc:
            logger.debug("rollup source unavailable, field degraded: %s", exc)
            return []

    @staticmethod
    def _breach_json(breach: Breach) -> dict[str, Any]:
        return asdict(breach)

    @staticmethod
    def _alert_json(alert: Alert) -> dict[str, Any]:
        return {"alert_id": alert.alert_id, "severity": alert.severity, "dedup_key": alert.dedup_key, "payload": alert.payload}


async def read_rollup(pool: asyncpg.Pool, trade_date: date) -> asyncpg.Record | None:
    return await pool.fetchrow("select * from observability.obs_telemetry_rollup where trade_date = $1", trade_date)


def _array_len(value: Any) -> int:
    return len(_as_list(value))


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


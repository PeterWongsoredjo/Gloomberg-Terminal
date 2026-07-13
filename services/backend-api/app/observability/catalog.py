"""
registry of all allowed telemetry metrics to prevent database schema clutter
any metric name not explicitly listed here is rejected at the collector boundary
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("gloomberg.observability.catalog")


@dataclass(frozen=True)
class MetricSpec:
    """metric specification containing its plane, unit, and logging resolution"""

    plane: str
    unit: str
    grain: str


# the closed catalog; adding a metric means adding a row here before it can be emitted
CATALOG: dict[str, MetricSpec] = {
    "node_latency_ms": MetricSpec("LLM", "ms", "per node span"),
    "run_latency_ms": MetricSpec("LLM", "ms", "per run"),
    "prompt_tokens": MetricSpec("LLM", "tokens", "per generation"),
    "completion_tokens": MetricSpec("LLM", "tokens", "per generation"),
    "notional_cost": MetricSpec("LLM", "USD", "per generation aggregated"),
    "quota_pct": MetricSpec("LLM", "ratio", "per provider per trade_date"),
    "loop_iterations": MetricSpec("LLM", "count", "per run"),
    "breaker_state": MetricSpec("LLM", "enum", "per provider"),
    "low_confidence_artifact_count": MetricSpec("LLM", "count", "per trade_date"),
    "label_agreement": MetricSpec("EVAL", "ratio", "per mlflow run"),
    "mae_score": MetricSpec("EVAL", "ratio", "per mlflow run"),
    "grounding_rate": MetricSpec("EVAL", "ratio", "per mlflow run"),
    "hallucinated_ticker_rate": MetricSpec("EVAL", "ratio", "per mlflow run"),
    "coverage_ratio": MetricSpec("PIPELINE", "ratio", "per ingest run"),
    "missing_ticker_count": MetricSpec("PIPELINE", "count", "per trade_date dataset"),
    "quarantine_row_count": MetricSpec("PIPELINE", "count", "per dbt build"),
    "dbt_tests_passed": MetricSpec("PIPELINE", "count", "per dbt build"),
    "dbt_tests_failed": MetricSpec("PIPELINE", "count", "per dbt build"),
    "data_as_of_age": MetricSpec("PIPELINE", "minute", "per dataset"),
    "gold_promotion_ok": MetricSpec("PIPELINE", "bool", "per promotion"),
    "telemetry_dropped": MetricSpec("META", "count", "observability self-metric"),
    "heartbeat": MetricSpec("META", "bool", "observability self-metric"),
}


def is_known(metric_name: str) -> bool:
    return metric_name in CATALOG


def accept(metric_name: str) -> bool:
    if metric_name in CATALOG:
        return True
    logger.warning("dropping unknown metric %r (not in the closed Appendix A catalog)", metric_name)
    return False


"""
Pushes one flow run's metrics to the Prometheus Pushgateway.

Every Prefect run is its own short-lived process, so nothing in memory is
still around when Prometheus comes to scrape. Instead the run pushes once, at
the end. Pushes are grouped by flow and status, so a FAILED run never wipes
out the timestamp of the last SUCCESS. A push failure is logged, never raised.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from pipeline.config import load_root_env

from orchestration.results import PhaseResult

logger = logging.getLogger("gloomberg.metrics")

JOB = "gloomberg_pipeline"
PUSH_TIMEOUT_SECONDS = 3.0

_stage_records: dict[tuple[str, str], int] = {}


def pushgateway_url() -> str:
    """Where to push, empty means monitoring is switched off."""
    load_root_env()
    return os.environ.get("GLOOMBERG_ORCH_PUSHGATEWAY_URL", "").strip()


def record_stage(phase: str, result: PhaseResult) -> None:
    """Remembers a phase's record counts until the run pushes them."""
    if result.records_processed is not None:
        _stage_records[(phase, "processed")] = result.records_processed
    if result.records_rejected is not None:
        _stage_records[(phase, "rejected")] = result.records_rejected


def build_registry(started_at: datetime, ended_at: datetime) -> CollectorRegistry:
    """A fresh registry holding exactly this run's numbers."""
    registry = CollectorRegistry()
    Gauge(
        "gloomberg_pipeline_last_run_finished_timestamp_seconds",
        "When the latest run of this flow with this status finished.",
        registry=registry,
    ).set(ended_at.timestamp())
    Gauge(
        "gloomberg_pipeline_last_run_duration_seconds",
        "How long the latest run of this flow with this status took.",
        registry=registry,
    ).set((ended_at - started_at).total_seconds())
    processed = Gauge(
        "gloomberg_pipeline_records_processed",
        "Records the latest run handled, by stage.",
        ["stage"],
        registry=registry,
    )
    rejected = Gauge(
        "gloomberg_pipeline_records_rejected",
        "Records the latest run quarantined, by stage.",
        ["stage"],
        registry=registry,
    )
    for (stage, kind), count in _stage_records.items():
        (processed if kind == "processed" else rejected).labels(stage).set(count)
    return registry


def push_run(flow: str, status: str, started_at: datetime, ended_at: datetime) -> bool:
    """Pushes the finished run, True only when the gateway took it."""
    try:
        url = pushgateway_url()
        if not url:
            return False
        push_to_gateway(
            url,
            job=JOB,
            registry=build_registry(started_at, ended_at),
            grouping_key={"flow": flow, "status": status},
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:
        logger.warning("pushgateway push failed for %s: %s", flow, exc)
        return False
    finally:
        _stage_records.clear()

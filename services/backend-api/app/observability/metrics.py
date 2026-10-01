"""
Prometheus metrics for the API process, scraped from /metrics.

The agentic graph runs inside this same process, so the HTTP, provider ladder
and quality gate metrics all live on the one default registry. Every helper
swallows its own errors: a metrics bug must never fail a request or a run.
"""

from __future__ import annotations

import logging

from prometheus_client import Counter, Histogram

logger = logging.getLogger("gloomberg.metrics")

UNMATCHED_ROUTE = "unmatched"

HTTP_REQUESTS = Counter(
    "gloomberg_http_requests_total",
    "HTTP requests served, by route template and status class.",
    ["method", "route", "status_class"],
)
HTTP_DURATION = Histogram(
    "gloomberg_http_request_duration_seconds",
    "HTTP request latency, by route template.",
    ["method", "route"],
)
LLM_REQUESTS = Counter(
    "gloomberg_llm_requests_total",
    "LLM provider calls, by provider, objective and outcome.",
    ["provider", "objective", "outcome"],
)
LLM_DURATION = Histogram(
    "gloomberg_llm_request_duration_seconds",
    "LLM provider call latency, by provider.",
    ["provider"],
    buckets=(0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
LLM_FALLBACKS = Counter(
    "gloomberg_llm_fallbacks_total",
    "Times the ladder moved from one provider to the next.",
    ["objective", "from_provider", "to_provider", "reason"],
)
GATE_DRAFTS = Counter(
    "gloomberg_quality_gate_drafts_total",
    "LLM drafts graded by the deterministic gates, passed or rejected.",
    ["objective", "outcome"],
)
GATE_FAILURES = Counter(
    "gloomberg_quality_gate_failures_total",
    "Hard gate failures, by objective and gate.",
    ["objective", "gate"],
)


def observe_http(method: str, route: str, status_code: int, seconds: float) -> None:
    """Times one HTTP request under its route template."""
    try:
        HTTP_REQUESTS.labels(method, route, f"{status_code // 100}xx").inc()
        HTTP_DURATION.labels(method, route).observe(seconds)
    except Exception:
        logger.warning("http metric dropped", exc_info=True)


def record_llm_attempt(provider: str, objective: str, outcome: str, seconds: float) -> None:
    """Counts one provider call and how long it took."""
    try:
        LLM_REQUESTS.labels(provider, objective, outcome).inc()
        LLM_DURATION.labels(provider).observe(seconds)
    except Exception:
        logger.warning("llm metric dropped", exc_info=True)


def record_fallback(objective: str, from_provider: str, to_provider: str, reason: str) -> None:
    """Counts the ladder handing a request to the next provider."""
    try:
        LLM_FALLBACKS.labels(objective, from_provider, to_provider, reason).inc()
    except Exception:
        logger.warning("fallback metric dropped", exc_info=True)


def record_gate_result(objective: str, passed: bool, failed_gates: list[str]) -> None:
    """Counts one graded draft and every hard gate it failed."""
    try:
        GATE_DRAFTS.labels(objective, "passed" if passed else "rejected").inc()
        for gate in failed_gates:
            GATE_FAILURES.labels(objective, gate).inc()
    except Exception:
        logger.warning("quality gate metric dropped", exc_info=True)

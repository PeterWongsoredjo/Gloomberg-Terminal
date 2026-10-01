"""Prometheus helpers, the HTTP middleware, and the /metrics scrape target."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import Counter

from app.core.metrics_middleware import route_template
from app.main import app
from app.observability import metrics


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """One app lifespan for every HTTP test here, booting it is slow."""
    with TestClient(app) as test_client:
        yield test_client


def _total(counter: Counter, **labels: str) -> float:
    """Sums a counter's samples that carry every given label value."""
    return sum(
        sample.value
        for family in counter.collect()
        for sample in family.samples
        if sample.name.endswith("_total") and all(sample.labels.get(k) == v for k, v in labels.items())
    )


def _seen_routes() -> set[str]:
    return {
        sample.labels["route"]
        for family in metrics.HTTP_REQUESTS.collect()
        for sample in family.samples
        if sample.name.endswith("_total")
    }


def test_status_codes_collapse_into_classes() -> None:
    """A 503 lands under 5xx, so error rate is one label match."""
    before = _total(metrics.HTTP_REQUESTS, route="/unit", status_class="5xx")
    metrics.observe_http("GET", "/unit", 503, 0.01)
    assert _total(metrics.HTTP_REQUESTS, route="/unit", status_class="5xx") == before + 1


def test_gate_result_counts_the_draft_and_each_failed_gate() -> None:
    before_rejected = _total(metrics.GATE_DRAFTS, objective="unit_obj", outcome="rejected")
    before_gate = _total(metrics.GATE_FAILURES, objective="unit_obj", gate="grounded")
    metrics.record_gate_result("unit_obj", False, ["grounded", "non_advisory"])
    assert _total(metrics.GATE_DRAFTS, objective="unit_obj", outcome="rejected") == before_rejected + 1
    assert _total(metrics.GATE_FAILURES, objective="unit_obj", gate="grounded") == before_gate + 1


def test_a_broken_metric_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instrumentation failing must not take a request or a run down with it."""

    class Exploding:
        def labels(self, *_: Any) -> Any:
            raise RuntimeError("registry broken")

    monkeypatch.setattr(metrics, "HTTP_REQUESTS", Exploding())
    monkeypatch.setattr(metrics, "LLM_REQUESTS", Exploding())
    monkeypatch.setattr(metrics, "LLM_FALLBACKS", Exploding())
    monkeypatch.setattr(metrics, "GATE_DRAFTS", Exploding())
    metrics.observe_http("GET", "/x", 200, 0.1)
    metrics.record_llm_attempt("groq", "daily_sentiment", "success", 0.1)
    metrics.record_fallback("daily_sentiment", "groq", "gemini", "error")
    metrics.record_gate_result("daily_sentiment", True, [])


def _request(path: str, route_format: str | None, params: dict[str, str] | None = None) -> Any:
    scope: dict[str, Any] = {"path": path, "path_params": params or {}}
    if route_format is not None:
        scope["route"] = SimpleNamespace(path_format=route_format)
    return SimpleNamespace(scope=scope)


def test_route_template_falls_back_when_nothing_matched() -> None:
    assert route_template(_request("/nope", None)) == metrics.UNMATCHED_ROUTE


def test_route_template_restores_the_router_prefix() -> None:
    """FastAPI keeps /api/v1 on the included router, not on the route itself."""
    nested = _request("/api/v1/runs/01ABC", "/runs/{run_id}", {"run_id": "01ABC"})
    assert route_template(nested) == "/api/v1/runs/{run_id}"
    assert route_template(_request("/metrics", "/metrics")) == "/metrics"


def test_route_template_never_leaks_a_value_into_the_prefix() -> None:
    """If segment counting ever misfires, fall back rather than label a raw id."""
    odd = _request("/api/v1/files/x1/x2/x3", "/files/{name}", {"name": "x1/x2/x3"})
    assert route_template(odd) == "/files/{name}"


def test_requests_are_labelled_by_template_never_raw_url(client: TestClient) -> None:
    """Two different run ids must share one series, and neither id may appear."""
    before = _total(metrics.HTTP_REQUESTS, route="/api/v1/runs/{run_id}")
    client.get("/api/v1/runs/01RAWIDAAAA")
    client.get("/api/v1/runs/01RAWIDBBBB")
    assert _total(metrics.HTTP_REQUESTS, route="/api/v1/runs/{run_id}") == before + 2
    assert not any("01RAWID" in route for route in _seen_routes())


def test_unknown_paths_share_one_label(client: TestClient) -> None:
    """A scanner probing random URLs cannot grow the series count."""
    before = _total(metrics.HTTP_REQUESTS, route=metrics.UNMATCHED_ROUTE, status_class="4xx")
    assert client.get("/wp-admin/setup-config.php").status_code == 404
    assert _total(metrics.HTTP_REQUESTS, route=metrics.UNMATCHED_ROUTE, status_class="4xx") == before + 1
    assert "/wp-admin/setup-config.php" not in _seen_routes()


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    client.get("/api/v1/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "gloomberg_http_requests_total" in response.text
    assert 'route="/api/v1/health"' in response.text

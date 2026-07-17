"""The per-IP slowapi ceiling: bursts get problem+json 429s, normal use does not.

The limiter is pure middleware, so these clients skip the lifespan on purpose:
no Postgres, no providers, no Langfuse threads left behind for later tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings as core_settings
from app.main import create_app


def test_burst_past_the_ceiling_gets_problem_json_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "rate_limit", "3/minute")
    client = TestClient(create_app())
    statuses = [client.get("/api/v1/health").status_code for _ in range(3)]
    assert statuses == [200, 200, 200]

    blocked = client.get("/api/v1/health")
    assert blocked.status_code == 429
    assert blocked.headers["content-type"] == "application/problem+json"
    body = blocked.json()
    assert body["status"] == 429
    assert "Rate limit exceeded" in body["title"]


def test_empty_limit_disables_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "rate_limit", "")
    client = TestClient(create_app())
    statuses = {client.get("/api/v1/health").status_code for _ in range(10)}
    assert statuses == {200}

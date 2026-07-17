import json
from datetime import date

import httpx
import pytest

from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.tasks.trigger import TriggerClient

TD = date(2026, 7, 3)


def _client(handler) -> TriggerClient:  # type: ignore[no-untyped-def]
    return TriggerClient("http://test", "tok", 5.0, transport=httpx.MockTransport(handler))


def test_launch_accepts_202_and_returns_run_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == "daily_sentiment:2026-07-03"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(202, json={"run_id": "RUN123"})

    assert _client(handler).launch("daily_sentiment", TD, ["BBCA"]) == "RUN123"


def test_launch_intraday_objective_keys_its_own_idempotency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == "intraday_sentiment:2026-07-03"
        return httpx.Response(202, json={"run_id": "RUN456"})

    assert _client(handler).launch("intraday_sentiment", TD, ["BBCA"]) == "RUN456"


def test_launch_intraday_insight_keys_its_own_idempotency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == "intraday_insight:2026-07-03"
        assert json.loads(request.read())["subject_universe"] == ["BBCA", "TLKM"]
        return httpx.Response(202, json={"run_id": "RUN789"})

    assert _client(handler).launch("intraday_insight", TD, ["BBCA", "TLKM"]) == "RUN789"


def test_launch_treats_409_as_idempotent_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"run_id": "RUN_EXISTING"})

    assert _client(handler).launch("daily_sentiment", TD, ["BBCA"]) == "RUN_EXISTING"


def test_launch_connect_error_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(TriggerTransientError):
        _client(handler).launch("daily_sentiment", TD, ["BBCA"])


def test_launch_503_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(TriggerTransientError):
        _client(handler).launch("daily_sentiment", TD, ["BBCA"])


def test_launch_other_error_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad"})

    with pytest.raises(TriggerPermanentError):
        _client(handler).launch("daily_sentiment", TD, ["BBCA"])


def test_poll_runs_until_succeeded() -> None:
    statuses = iter(["RUNNING", "RUNNING", "SUCCEEDED"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"status": next(statuses)}})

    assert _client(handler).poll("RUN123", interval=0.0, timeout=5.0) == "SUCCEEDED"


def test_poll_returns_degraded_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"status": "DEGRADED"}})

    assert _client(handler).poll("RUN123", interval=0.0, timeout=5.0) == "DEGRADED"


def test_poll_times_out_permanently() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"status": "RUNNING"}})

    with pytest.raises(TriggerPermanentError):
        _client(handler).poll("RUN123", interval=0.0, timeout=0.05)

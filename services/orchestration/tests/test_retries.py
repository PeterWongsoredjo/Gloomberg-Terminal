from collections.abc import Callable

from prefect.states import Completed, Failed

from pipeline.bronze.ingest import FetchError

from orchestration.errors import DbtTestFailure, DbtTransientError, TriggerTransientError
from orchestration.retries import (
    retry_on_transient_dbt,
    retry_on_transient_fetch,
    retry_on_transient_trigger,
)


def _retry(fn: Callable[..., bool], exc: BaseException) -> bool:
    return fn(None, None, Failed(data=exc))


def test_fetch_retries_on_timeout_429_and_5xx() -> None:
    assert _retry(retry_on_transient_fetch, FetchError(None, "timeout")) is True
    assert _retry(retry_on_transient_fetch, FetchError(429, "rate")) is True
    assert _retry(retry_on_transient_fetch, FetchError(503, "bad gateway")) is True


def test_fetch_gives_up_on_other_4xx() -> None:
    assert _retry(retry_on_transient_fetch, FetchError(404, "gone")) is False
    assert _retry(retry_on_transient_fetch, FetchError(401, "auth")) is False


def test_fetch_does_not_retry_unrelated_errors() -> None:
    assert _retry(retry_on_transient_fetch, ValueError("bug")) is False


def test_dbt_retries_only_transient_never_test_failure() -> None:
    assert _retry(retry_on_transient_dbt, DbtTransientError("lock")) is True
    assert _retry(retry_on_transient_dbt, DbtTestFailure("real test fail")) is False


def test_trigger_retries_only_transient() -> None:
    assert _retry(retry_on_transient_trigger, TriggerTransientError("503")) is True
    assert _retry(retry_on_transient_trigger, RuntimeError("permanent")) is False


def test_a_completed_state_is_never_retried() -> None:
    assert retry_on_transient_fetch(None, None, Completed()) is False  # type: ignore[arg-type]

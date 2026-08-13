"""
retry policy: jittered exponential backoff, and typed conditions so only
transient classes retry. 

A real 4xx, a dbt test failure, or a compile error fails fast.
"""

from __future__ import annotations

from typing import Any

from prefect import Task
from prefect.client.schemas.objects import State, TaskRun
from prefect.tasks import exponential_backoff

from pipeline.bronze.ingest import FetchError, is_transient_fetch

from orchestration.errors import DbtTransientError, TriggerTransientError

# ingest fetches: 4 tries, 5/10/20/40s with full jitter
INGEST_RETRIES = 4
INGEST_BACKOFF = exponential_backoff(backoff_factor=5)
INGEST_JITTER = 1.0

# dbt phases are deterministic, one retry catches a flap, no more
DBT_RETRIES = 1
DBT_DELAY_SECONDS = 15

# agentic trigger: 2 tries, 10/20s with full jitter
TRIGGER_RETRIES = 2
TRIGGER_BACKOFF = exponential_backoff(backoff_factor=10)
TRIGGER_JITTER = 1.0


def _failed_exception(state: State) -> BaseException | None:
    try:
        result = state.result(raise_on_failure=False)
    except Exception:  # a state with no result data is simply not a typed failure
        return None
    return result if isinstance(result, BaseException) else None


def retry_on_transient_fetch(task: Task[Any, Any], task_run: TaskRun, state: State) -> bool:
    exc = _failed_exception(state)
    return isinstance(exc, FetchError) and is_transient_fetch(exc)


def retry_on_transient_dbt(task: Task[Any, Any], task_run: TaskRun, state: State) -> bool:
    return isinstance(_failed_exception(state), DbtTransientError)


def retry_on_transient_trigger(task: Task[Any, Any], task_run: TaskRun, state: State) -> bool:
    return isinstance(_failed_exception(state), TriggerTransientError)

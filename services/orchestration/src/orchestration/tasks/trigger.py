"""OR-01 phase 7: hand the agentic run to backend-api over authenticated loopback.

Orchestration triggers and polls; it never hosts the graph or opens an LLM client (OR-04,
04 3.4). The trigger is idempotent by (objective:trade_date), so a retried trigger 409s
instead of double-launching. A DEGRADED or ABORTED terminal status is a completed step, not
a flow failure. An unreachable backend-api leaves promoted Gold intact and degrades only this step.
"""

from __future__ import annotations

import time
from datetime import date

import httpx
from prefect import task

from orchestration.config import OrchestrationConfig
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.results import PhaseResult
from orchestration.retries import (
    TRIGGER_BACKOFF,
    TRIGGER_JITTER,
    TRIGGER_RETRIES,
    retry_on_transient_trigger,
)

# CT-010 terminal statuses and how each maps to this phase's OR-06 status
_TERMINAL_STATUS = {"SUCCEEDED": "SUCCESS", "DEGRADED": "DEGRADED", "ABORTED": "DEGRADED"}
TERMINAL = set(_TERMINAL_STATUS)


def _run_id(response: httpx.Response) -> str | None:
    """Pulls run_id from a bare {run_id} body or a CT-011-enveloped one."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    inner = data if isinstance(data, dict) else {}
    run_id = body.get("run_id") or inner.get("run_id")
    return str(run_id) if run_id else None


class TriggerClient:
    """Thin loopback client for backend-api's scheduled-run seam (06 3.5)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        request_timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url, timeout=request_timeout, headers=headers, transport=transport
        )

    def __enter__(self) -> TriggerClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def launch(self, objective: str, trade_date: date, universe: list[str]) -> str | None:
        """POSTs the run; returns run_id (202/200 or an idempotent 409), else raises typed."""
        body = {
            "objective": objective,
            "trade_date": trade_date.isoformat(),
            "subject_universe": universe,
        }
        idem = f"{objective}:{trade_date.isoformat()}"
        try:
            resp = self._client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": idem})
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TriggerTransientError(f"POST /runs unreachable: {exc}") from exc
        if resp.status_code in (200, 202, 409):  # 409 = already running, idempotent success
            return _run_id(resp)
        if resp.status_code == 503:
            raise TriggerTransientError("POST /runs -> 503")
        raise TriggerPermanentError(f"POST /runs -> {resp.status_code}")

    def poll(self, run_id: str, interval: float, timeout: float) -> str:
        """Polls run status until a CT-010 terminal state or a hard timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = self._client.get(f"/api/v1/runs/{run_id}")
            except (httpx.ConnectError, httpx.TimeoutException):
                time.sleep(interval)
                continue
            if resp.status_code == 200:
                status = str(resp.json()["data"]["status"])
                if status in TERMINAL:
                    return status
            time.sleep(interval)
        raise TriggerPermanentError(f"agentic run {run_id} poll timed out after {timeout}s")


@task(
    name="trigger_agentic",
    retries=TRIGGER_RETRIES,
    retry_delay_seconds=TRIGGER_BACKOFF,
    retry_jitter_factor=TRIGGER_JITTER,
    retry_condition_fn=retry_on_transient_trigger,
)
def trigger_agentic(trade_date: date, config: OrchestrationConfig) -> PhaseResult:
    """Launches the agentic run in backend-api and polls it to a terminal status."""
    with TriggerClient(
        config.backend_api_url, config.backend_api_token, config.trigger_timeout_seconds
    ) as client:
        run_id = client.launch(config.objective, trade_date, config.subject_universe)
        if run_id is None:
            return PhaseResult(status="SUCCESS", notes="agentic already running (409); nothing to poll")
        terminal = client.poll(run_id, config.poll_interval_seconds, config.poll_timeout_seconds)
    return PhaseResult(
        status=_TERMINAL_STATUS[terminal],
        run_id=run_id,
        notes=f"agentic run {run_id} -> {terminal}",
    )

"""SV-09 scheduled-run seam: launch an agentic run off the request loop and poll its status.

The orchestrator (OR-04) POSTs a run and polls it; this never runs a model inline in the request.
POST returns 202 with a run_id immediately and dispatches the graph as a background task; GET reads
the run's ledger row. A repeat POST for an in-flight objective:trade_date is an idempotent 409.
Run-status envelope freshness is a placeholder until the serving stage wires the SLO engine.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.agentic import ledger
from app.agentic.ids import new_ulid
from app.agentic.runner import run_agentic
from app.agentic.state import RunStatus
from app.api.v1.deps import require_agentic, require_operator
from app.core.enums import SessionPhase
from app.core.envelope import Envelope
from app.lifespan import AppState

logger = logging.getLogger("gloomberg.runs")
router = APIRouter()


class RunRequest(BaseModel):
    """The OR-04 trigger body: what run to launch, over which universe."""

    model_config = ConfigDict(extra="forbid")

    objective: str
    trade_date: date
    subject_universe: list[str]


class RunAccepted(BaseModel):
    """The bare 202/409 body the trigger reads a run_id from."""

    run_id: str


class RunStatusData(BaseModel):
    """A serving projection of the agentic.agent_run ledger row."""

    run_id: str
    status: RunStatus
    objective: str
    trade_date: date
    abort_reason: str | None
    consumed_tokens: int
    consumed_iterations: int
    started_at: datetime
    ended_at: datetime | None


async def _run_and_cleanup(app_state: AppState, idem_key: str, run_id: str, body: RunRequest) -> None:
    """Drives one run to a terminal state, stamping DEGRADED on an unexpected crash."""
    deps = app_state.agentic_deps
    assert deps is not None  # require_agentic guaranteed this before dispatch
    try:
        await run_agentic(
            app_state.compiled_graph,
            deps,
            objective=body.objective,
            trade_date=body.trade_date,
            universe=body.subject_universe,
            run_id=run_id,
        )
    except Exception:
        logger.exception("agentic run %s failed", run_id)
        if deps.pg_pool is not None:
            await ledger.finish_run(
                deps.pg_pool,
                run_id=run_id,
                status="DEGRADED",
                abort_reason="run_error",
                consumed_tokens=0,
                consumed_iterations=0,
            )
    finally:
        app_state.run_registry.pop(idem_key, None)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=RunAccepted)
async def create_run(
    body: RunRequest,
    app_state: AppState = Depends(require_agentic),
    _: None = Depends(require_operator),
) -> RunAccepted | JSONResponse:
    """Launches an agentic run in the background and returns its run_id immediately."""
    idem_key = f"{body.objective}:{body.trade_date.isoformat()}"
    existing = app_state.run_registry.get(idem_key)
    if existing is not None:  # a run for this key is already in flight; idempotent success
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"run_id": existing})

    run_id = new_ulid()
    deps = app_state.agentic_deps
    assert deps is not None
    if deps.pg_pool is not None:  # seed RUNNING before returning so an immediate poll finds the run
        await ledger.start_run(
            deps.pg_pool, run_id=run_id, objective=body.objective, trade_date=body.trade_date, trace_id=None
        )
    app_state.run_registry[idem_key] = run_id
    task = asyncio.create_task(_run_and_cleanup(app_state, idem_key, run_id, body))
    app_state.run_tasks.add(task)
    task.add_done_callback(app_state.run_tasks.discard)
    return RunAccepted(run_id=run_id)


@router.get("/runs/{run_id}", response_model=Envelope[RunStatusData])
async def get_run_status(
    run_id: str,
    app_state: AppState = Depends(require_agentic),
    _: None = Depends(require_operator),
) -> Envelope[RunStatusData]:
    """Returns the run's CT-011-wrapped status for the OR-04 poller."""
    deps = app_state.agentic_deps
    assert deps is not None
    if deps.pg_pool is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="run ledger unavailable")
    row = await ledger.read_run(deps.pg_pool, run_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    data = RunStatusData(
        run_id=row["run_id"],
        status=row["status"],
        objective=row["objective"],
        trade_date=row["trade_date"],
        abort_reason=row["abort_reason"],
        consumed_tokens=row["consumed_tokens"],
        consumed_iterations=row["consumed_iterations"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )
    now = datetime.now(timezone.utc)
    return Envelope[RunStatusData](
        served_at=now,
        data_as_of=row["ended_at"] or row["started_at"],  # the run's own state is the datum here
        freshness_slo_met=True,  # a run's status is always current; real SLO lands in the serving stage
        market_state=SessionPhase.CLOSED,  # placeholder; calendar-resolved market state is a serving concern
        quality_flags=[],
        data=data,
    )

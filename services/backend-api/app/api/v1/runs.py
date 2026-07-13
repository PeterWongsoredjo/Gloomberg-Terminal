"""Launches the agentic job off the main request loop and reports its status."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.agentic import ledger
from app.agentic.ids import new_ulid
from app.agentic.runner import run_agentic
from app.agentic.state import RunStatus
from app.api.v1.deps import require_agentic, require_operator
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving.envelope import build_envelope
from app.serving.models import RunReasoningTrace, TraceStep
from app.serving.readers.postgres import ServingPostgresReader

logger = logging.getLogger("gloomberg.runs")
router = APIRouter()

_NODE_SEQUENCE = ("ingest_context", "cache_lookup", "route_task", "analysis", "evaluate", "optimize", "finalize")


class RunRequest(BaseModel):
    """trigger body: what run to launch, over which universe."""

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


@dataclass(frozen=True)
class Dispatch:
    """The result of asking for a run: its id and whether one was already in flight."""

    run_id: str
    already_running: bool


async def dispatch_run(app_state: AppState, objective: str, trade_date: date, universe: list[str]) -> Dispatch:
    """Seeds a RUNNING row and launches the graph in the background, idempotent per objective:date."""
    idem_key = f"{objective}:{trade_date.isoformat()}"
    existing = app_state.run_registry.get(idem_key)
    if existing is not None:
        return Dispatch(run_id=existing, already_running=True)

    run_id = new_ulid()
    deps = app_state.agentic_deps
    assert deps is not None  # require_agentic guaranteed this before dispatch
    if deps.pg_pool is not None:  # seed RUNNING before returning so an immediate poll finds the run
        await ledger.start_run(deps.pg_pool, run_id=run_id, objective=objective, trade_date=trade_date, trace_id=None)
    app_state.run_registry[idem_key] = run_id
    body = RunRequest(objective=objective, trade_date=trade_date, subject_universe=universe)
    task = asyncio.create_task(_run_and_cleanup(app_state, idem_key, run_id, body))
    app_state.run_tasks.add(task)
    task.add_done_callback(app_state.run_tasks.discard)
    return Dispatch(run_id=run_id, already_running=False)


async def _run_and_cleanup(app_state: AppState, idem_key: str, run_id: str, body: RunRequest) -> None:
    """Drives one run to a terminal state, stamping DEGRADED on an unexpected crash."""
    deps = app_state.agentic_deps
    assert deps is not None
    try:
        await run_agentic(
            app_state.compiled_graph,
            deps,
            objective=body.objective,
            trade_date=body.trade_date,
            universe=body.subject_universe,
            run_id=run_id,
            tracer=app_state.langfuse_handler,
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
    result = await dispatch_run(app_state, body.objective, body.trade_date, body.subject_universe)
    if result.already_running: 
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"run_id": result.run_id})
    return RunAccepted(run_id=result.run_id)


@router.get("/runs/{run_id}", response_model=Envelope[RunStatusData])
async def get_run_status(
    run_id: str,
    app_state: AppState = Depends(require_agentic),
    _: None = Depends(require_operator),
) -> Envelope[RunStatusData]:
    """Returns the run's status."""
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
    return build_envelope(
        data,
        data_as_of=row["ended_at"] or row["started_at"],
        trade_date=row["trade_date"],
        slo_engine=app_state.slo_engine,
    )


@router.get("/runs/{run_id}/trace", response_model=Envelope[RunReasoningTrace])
async def get_run_trace(
    run_id: str,
    app_state: AppState = Depends(require_agentic),
    _: None = Depends(require_operator),
) -> Envelope[RunReasoningTrace]:
    """The ordered nodes a run walked, its iterations, and what it concluded — descriptive only."""
    pg = ServingPostgresReader(app_state.pg_pool)
    run = await pg.run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    artifacts = await pg.run_artifacts(run_id)
    trace = _build_trace(run, artifacts)
    return build_envelope(
        trace,
        data_as_of=run["ended_at"] or run["started_at"],
        trade_date=run["trade_date"],
        slo_engine=app_state.slo_engine,
    )


def _build_trace(run: dict[str, Any], artifacts: list[dict[str, Any]]) -> RunReasoningTrace:
    """Assembles the node path from the run's terminal state and the artifacts it produced."""
    terminal = str(run["status"]).upper()
    reached_end = terminal in {"SUCCEEDED", "SUCCESS", "COMPLETED", "DEGRADED"}
    steps = [
        TraceStep(
            node=node,
            status="done" if reached_end else "reached",
            detail=None,
        )
        for node in _NODE_SEQUENCE
    ]
    for art in artifacts:
        steps.append(
            TraceStep(
                node="artifact",
                status=terminal.lower(),
                detail=f"{art.get('artifact_type')} {art.get('ticker')} confidence={art.get('confidence')}",
            )
        )
    return RunReasoningTrace(
        run_id=str(run["run_id"]),
        objective=str(run["objective"]),
        trade_date=run["trade_date"],
        status=terminal,
        loop_iterations=int(run.get("consumed_iterations") or 0),
        trace_id=run.get("trace_id"),
        steps=steps,
    )

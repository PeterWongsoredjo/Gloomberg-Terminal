"""The Insight Panel and its on-demand refresh dispatch."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.deps import anchor_trade_date, get_app_state, require_agentic, require_operator
from app.api.v1.runs import RunAccepted, dispatch_run
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving import mappers
from app.serving.envelope import build_envelope
from app.serving.models import InsightPanel, InsightSignal
from app.serving.news_merge import freshest
from app.serving.readers.gold import ServingGoldReader
from app.serving.readers.postgres import ServingPostgresReader

router = APIRouter()

_INSIGHT_OBJECTIVE = "insight_synthesis"


@router.get("/insights/{ticker}", response_model=Envelope[InsightPanel])
async def get_insight_panel(
    ticker: str, app_state: AppState = Depends(get_app_state)
) -> Envelope[InsightPanel]:
    """A descriptive read on one security with derived signals and full model provenance."""
    upper = ticker.upper()
    gold = ServingGoldReader(app_state.duckdb_ro)
    pg = ServingPostgresReader(app_state.pg_pool)
    # in session the hourly intraday refresh wins, the later EOD build supersedes it
    fct = freshest(await gold.insight(upper), await pg.latest_intraday_insight(upper))
    if fct is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no insight for ticker")

    provenance = await pg.insight_provenance(str(fct["artifact_id"]))
    signals = await _signals(pg, upper)

    panel = mappers.insight_panel(fct, provenance, signals)
    data_as_of = fct.get("data_as_of") or fct["generated_at"]
    return build_envelope(
        panel,
        data_as_of=data_as_of,
        trade_date=fct["trade_date"],
        quality_flags=panel.quality_flags,
        slo_engine=app_state.slo_engine,
    )


@router.post("/insights/{ticker}/refresh", status_code=status.HTTP_202_ACCEPTED, response_model=RunAccepted)
async def trigger_insight_refresh(
    ticker: str,
    app_state: AppState = Depends(require_agentic),
    _: None = Depends(require_operator),
) -> RunAccepted:
    """Dispatches an on-demand insight run for one ticker, off the request loop (202 + run_id)."""
    trade_date = await anchor_trade_date(app_state)
    result = await dispatch_run(app_state, _INSIGHT_OBJECTIVE, trade_date, [ticker.upper()])
    return RunAccepted(run_id=result.run_id)


async def _signals(pg: ServingPostgresReader, ticker: str) -> list[InsightSignal]:
    """Builds the sentiment/flow/price chips from the current projections."""
    signals: list[InsightSignal] = []
    sentiment = await pg.sentiment_for(ticker)
    if sentiment is not None:
        signals.append(
            InsightSignal(
                type="SENTIMENT",
                value=str(sentiment.get("sentiment_label")),
                confidence=_as_float(sentiment.get("confidence")),
            )
        )
    tape = await pg.tape_row_for(ticker)
    if tape is not None:
        signals.append(InsightSignal(type="FLOW", value=_flow_text(tape.get("net_foreign_idr")), confidence=None))
        signals.append(InsightSignal(type="PRICE", value=_price_text(tape.get("change_pct")), confidence=None))
    return signals


def _flow_text(value: Any) -> str:
    if value is None:
        return "net_foreign n/a"
    return f"net_foreign {'+' if value >= 0 else ''}{int(value):,} IDR"


def _price_text(value: Any) -> str:
    if value is None:
        return "change n/a"
    return f"{float(value) * 100:+.2f}% (adjusted)"


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/insights/{ticker}")
async def get_insight_panel(ticker: str) -> None:
    """CT-011<InsightPanel> — wiring lands with the agentic + serving stages (SV-05)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="insight panel not yet built")


@router.post("/insights/{ticker}/refresh", status_code=status.HTTP_202_ACCEPTED)
async def trigger_insight_refresh(ticker: str) -> None:
    """On-demand agentic run dispatch — wiring lands with the serving stage (SV-09)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="on-demand refresh not yet built")

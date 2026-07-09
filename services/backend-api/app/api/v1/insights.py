from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/insights/{ticker}")
async def get_insight_panel(ticker: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="insight panel not yet built")


@router.post("/insights/{ticker}/refresh", status_code=status.HTTP_202_ACCEPTED)
async def trigger_insight_refresh(ticker: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="on-demand refresh not yet built")

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/market/state")
async def get_market_state() -> None:
    """CT-011<MarketState> — wiring lands with the serving stage (SV-03)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="market/state not yet built")

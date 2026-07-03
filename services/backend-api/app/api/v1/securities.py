from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/securities")
async def list_securities() -> None:
    """CT-011<SecurityPage> paginated universe — wiring lands with the serving stage (SV-08)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="securities list not yet built")


@router.get("/securities/{ticker}")
async def get_security_snapshot(ticker: str) -> None:
    """CT-011<SecuritySnapshot> — wiring lands with the serving stage (SV-05)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="security snapshot not yet built")

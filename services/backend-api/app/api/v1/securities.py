from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/securities")
async def list_securities() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="securities list not yet built")


@router.get("/securities/{ticker}")
async def get_security_snapshot(ticker: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="security snapshot not yet built")

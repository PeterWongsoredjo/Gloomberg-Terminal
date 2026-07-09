from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/telemetry")
async def get_data_telemetry() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="data telemetry not yet built")

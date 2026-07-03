from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/telemetry")
async def get_data_telemetry() -> None:
    """CT-011<DataTelemetry> — wiring lands with the observability stage (SV-06, OB-09)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="data telemetry not yet built")

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/runs/{run_id}")
async def get_run_status(run_id: str) -> None:
    """CT-011<RunStatus> poll endpoint — wiring lands with the serving stage (SV-09)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="run status not yet built")

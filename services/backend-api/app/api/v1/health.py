from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe, deliberately outside the envelope, for ops only."""
    return {"status": "ok"}

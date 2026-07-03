from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe, deliberately outside CT-011 — ops-only, per SV-02."""
    return {"status": "ok"}

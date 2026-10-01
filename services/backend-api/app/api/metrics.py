from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    """Prometheus scrape target, deliberately outside the envelope, for ops only."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

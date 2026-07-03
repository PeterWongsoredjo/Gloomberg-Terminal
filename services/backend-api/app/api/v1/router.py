from fastapi import APIRouter

from app.api.v1 import health, insights, market, runs, securities, sentiment, tape, telemetry

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(market.router, tags=["market"])
api_router.include_router(tape.router, tags=["tape"])
api_router.include_router(securities.router, tags=["securities"])
api_router.include_router(sentiment.router, tags=["sentiment"])
api_router.include_router(insights.router, tags=["insights"])
api_router.include_router(telemetry.router, tags=["telemetry"])
api_router.include_router(runs.router, tags=["runs"])

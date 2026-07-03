from enum import StrEnum


class SessionPhase(StrEnum):
    """IDX trading-session phase, resolved against effective-dated calendar."""

    PRE_OPENING = "PRE_OPENING"
    SESSION_1 = "SESSION_1"
    SESSION_BREAK = "SESSION_BREAK"
    SESSION_2 = "SESSION_2"
    PRE_CLOSING = "PRE_CLOSING"
    RANDOM_CLOSING = "RANDOM_CLOSING"
    POST_TRADING = "POST_TRADING"
    CLOSED = "CLOSED"


class QualityFlag(StrEnum):
    """Closed data-quality vocabulary that travels with every record (CT-008)."""

    MISSING_UPSTREAM = "MISSING_UPSTREAM"
    STALE = "STALE"
    COVERAGE_GAP = "COVERAGE_GAP"
    ADJUSTMENT_PENDING = "ADJUSTMENT_PENDING"
    FCA_PRICING = "FCA_PRICING"
    SUSPENDED = "SUSPENDED"
    DERIVED_ESTIMATE = "DERIVED_ESTIMATE"
    LLM_LOW_CONFIDENCE = "LLM_LOW_CONFIDENCE"
    SCHEMA_DRIFT_QUARANTINE = "SCHEMA_DRIFT_QUARANTINE"

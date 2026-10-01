from __future__ import annotations

from datetime import date

from pipeline.config import get_settings
from pipeline.gold.quarantine import quarantined_rows

from orchestration.results import PhaseResult


def count_quarantine(trade_date: date) -> PhaseResult:
    """Counts the day's rows the staging checks rejected into quarantine."""
    rows = quarantined_rows(get_settings().published_gold, trade_date)
    return PhaseResult(
        status="SUCCESS",
        notes=f"{rows} rows quarantined for {trade_date.isoformat()}",
        records_rejected=rows,
    )


def degrade_on_count_failure(exc: Exception) -> PhaseResult | None:
    """A count that cannot be read costs a metric, never the day."""
    return PhaseResult(status="DEGRADED", notes=f"quarantine count failed: {exc}")

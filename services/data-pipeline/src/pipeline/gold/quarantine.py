"""
Reads how many rows the staging checks quarantined for one trade date.

dbt flags unparseable rows instead of coercing them, and agg_pipeline_telemetry
tallies those per dataset. This only reads the published snapshot.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb


def quarantined_rows(published_gold: Path, trade_date: date) -> int:
    """Total quarantined rows across every dataset for the trade date."""
    con = duckdb.connect(str(published_gold), read_only=True)
    try:
        row = con.execute(
            "select coalesce(sum(quarantine_count), 0) from agg_pipeline_telemetry where trade_date = ?",
            [trade_date],
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0

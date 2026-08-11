"""
Data bridge between the AI agents and the main analytical warehouse
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import duckdb
from minio import Minio

from pipeline.bronze.ingest import client, land_payloads
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key
from pipeline.config import Settings, get_settings

_TYPE_TO_DATASET = {
    "SENTIMENT": "sentiment",
    "ARTICLE_SENTIMENT": "article_sentiment",
    "INSIGHT": "insight",
    "EXTRACTION": "extraction",
}


def _read_ledger(settings: Settings, trade_date: date) -> list[dict[str, Any]]:
    """Reads the artifact rows for a window from Postgres via a DuckDB attach."""
    con = duckdb.connect()
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{settings.postgres_dsn}' AS pg (TYPE postgres, READ_ONLY)")
        rows = con.execute(
            """
            select artifact_id, run_id, artifact_type, security_id, ticker,
                   window_from, window_to, value::text, confidence, quality_flags::text,
                   provider, model, prompt_version, generated_at
            from pg.agentic.agent_artifact
            where window_from = ?
            order by artifact_id
            """,
            [trade_date],
        ).fetchall()
    finally:
        con.close()
    return [_row_to_artifact(r) for r in rows]


def _row_to_artifact(row: tuple[Any, ...]) -> dict[str, Any]:
    """Flattens one ledger row into the JSON shape dbt staging reads."""
    generated_at = row[13]
    return {
        "artifact_id": row[0],
        "run_id": row[1],
        "artifact_type": row[2],
        "security_id": row[3],
        "ticker": row[4],
        "window_from": row[5].isoformat(),
        "window_to": row[6].isoformat(),
        "value": json.loads(row[7]),
        "confidence": row[8],
        "quality_flags": json.loads(row[9]),
        "provider": row[10],
        "model": row[11],
        "prompt_version": row[12],
        "generated_at": generated_at.isoformat() if hasattr(generated_at, "isoformat") else generated_at,
    }


def land_artifacts(minio: Minio, trade_date: date, settings: Settings) -> list[dict[str, Any]]:
    """Lands each artifact type's rows for the date into Bronze; returns the manifests."""
    artifacts = _read_ledger(settings, trade_date)
    by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in _TYPE_TO_DATASET}
    for artifact in artifacts:
        by_type.setdefault(str(artifact["artifact_type"]), []).append(artifact)

    manifests = []
    for artifact_type, rows in by_type.items():
        if not rows:
            continue
        dataset = _TYPE_TO_DATASET.get(artifact_type, artifact_type.lower())
        payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        manifests.append(
            land_payloads(
                minio,
                source="agent_artifact",
                dataset=dataset,
                trade_date=trade_date,
                source_version="v1",
                ingest_run_id=deterministic_run_id(idempotency_key("agent_artifact", dataset, trade_date, "v1")),
                payloads=[payload],
                record_count=len(rows),
                expected_universe=len(rows),
                missing_tickers=[],
                requested_at=datetime.now(timezone.utc),
            )
        )
    return manifests


def main() -> None:
    """CLI: land a trade_date's artifacts from the ledger into Bronze."""
    import sys

    settings = get_settings()
    trade_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    manifests = land_artifacts(client(settings), trade_date, settings)
    for manifest in manifests:
        print(f"landed {manifest['record_count']} {manifest['dataset']} artifacts for {trade_date}")


if __name__ == "__main__":
    main()

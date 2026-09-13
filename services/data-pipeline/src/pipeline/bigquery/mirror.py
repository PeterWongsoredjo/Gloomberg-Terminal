"""
Mirrors the published Gold snapshot into BigQuery for remote analysis.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
from google.cloud import bigquery

from pipeline.bigquery.target import GOLD_DATASET, BigQueryTarget, target_from_env
from pipeline.config import get_settings

MANIFEST_TABLE = "publish_manifest"

# sandbox caps expiry at 60 days, keep a day spare
SANDBOX_LIFETIME = timedelta(days=59)


class MirrorMismatch(RuntimeError):
    """BigQuery holds a different row count than the snapshot we sent."""


@dataclass(frozen=True)
class MirrorTable:
    """One Gold table and the columns BigQuery clusters it on."""

    name: str
    cluster_by: tuple[str, ...] = ()


# clustered only, sandbox would expire date partitions past 60 days
MIRROR_TABLES: tuple[MirrorTable, ...] = (
    MirrorTable("fct_daily_trade", ("trade_date", "security_id")),
    MirrorTable("dim_security", ("ticker",)),
    MirrorTable("dim_sector"),
    MirrorTable("fct_news_item", ("trade_date", "source")),
    MirrorTable("fct_article_ticker_sentiment", ("trade_date", "ticker")),
    MirrorTable("fct_sentiment", ("trade_date", "ticker")),
    MirrorTable("agg_pipeline_telemetry", ("trade_date", "source")),
)

_MANIFEST_SCHEMA = [
    bigquery.SchemaField("table_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("row_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("snapshot_modified_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("published_at", "TIMESTAMP", mode="REQUIRED"),
]


def _select_list(con: duckdb.DuckDBPyConnection, table: str) -> str:
    """Columns to export, with naive timestamps marked as the UTC they are."""
    columns = con.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name = ? order by ordinal_position",
        [table],
    ).fetchall()
    if not columns:
        raise MirrorMismatch(f"{table} is not in the published snapshot")
    return ", ".join(
        f'cast("{name}" as timestamptz) as "{name}"' if dtype == "TIMESTAMP" else f'"{name}"'
        for name, dtype in columns
    )


def export_snapshot(
    snapshot: Path, tables: tuple[MirrorTable, ...], out_dir: Path
) -> dict[str, tuple[Path, int]]:
    """Writes each table to Parquet, returning the file and its row count."""
    exported: dict[str, tuple[Path, int]] = {}
    con = duckdb.connect(str(snapshot), read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        for spec in tables:
            target = out_dir / f"{spec.name}.parquet"
            select = _select_list(con, spec.name)
            con.execute(f"copy (select {select} from \"{spec.name}\") to '{target.as_posix()}' (format parquet)")
            row = con.execute(f'select count(*) from "{spec.name}"').fetchone()
            exported[spec.name] = (target, int(row[0]) if row else 0)
    finally:
        con.close()
    return exported


def _load_job_config(spec: MirrorTable) -> bigquery.LoadJobConfig:
    """Replace the table wholesale, keeping list columns as repeated fields."""
    parquet = bigquery.ParquetOptions()  # type: ignore[no-untyped-call]
    parquet.enable_list_inference = True
    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        clustering_fields=list(spec.cluster_by) or None,
    )
    config.parquet_options = parquet
    return config


def keep_alive(client: bigquery.Client, table_id: str) -> None:
    """Pushes a sandbox expiry forward, since reloading never does."""
    table = client.get_table(table_id)
    if table.expires is None:
        return
    table.expires = datetime.now(timezone.utc) + SANDBOX_LIFETIME
    client.update_table(table, ["expires"])


def load_table(
    client: bigquery.Client, target: BigQueryTarget, spec: MirrorTable, parquet: Path, expected: int
) -> int:
    """Loads one Parquet file and refuses a row count that does not match."""
    table_id = f"{target.dataset_id(GOLD_DATASET)}.{spec.name}"
    with parquet.open("rb") as handle:
        client.load_table_from_file(handle, table_id, job_config=_load_job_config(spec)).result()
    loaded = int(client.get_table(table_id).num_rows or 0)
    if loaded != expected:
        raise MirrorMismatch(f"{spec.name}: sent {expected} rows, BigQuery holds {loaded}")
    keep_alive(client, table_id)
    return loaded


def _write_manifest(
    client: bigquery.Client, target: BigQueryTarget, rows: list[dict[str, Any]]
) -> None:
    """Records what was mirrored and when, for the freshness banner."""
    config = bigquery.LoadJobConfig(
        schema=_MANIFEST_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    table_id = f"{target.dataset_id(GOLD_DATASET)}.{MANIFEST_TABLE}"
    client.load_table_from_json(rows, table_id, job_config=config).result()
    keep_alive(client, table_id)


def publish_mirror(target: BigQueryTarget, snapshot: Path) -> list[dict[str, Any]]:
    """Mirrors every configured Gold table and returns the manifest rows."""
    if not snapshot.exists():
        raise FileNotFoundError(f"no published snapshot at {snapshot}")
    client = target.client()
    dataset = bigquery.Dataset(target.dataset_id(GOLD_DATASET))
    dataset.location = target.location
    client.create_dataset(dataset, exists_ok=True)

    modified = datetime.fromtimestamp(snapshot.stat().st_mtime, tz=timezone.utc).isoformat()
    published = datetime.now(timezone.utc).isoformat()
    manifest: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="gloomberg-bq-") as tmp:
        exported = export_snapshot(snapshot, MIRROR_TABLES, Path(tmp))
        for spec in MIRROR_TABLES:
            parquet, expected = exported[spec.name]
            loaded = load_table(client, target, spec, parquet, expected)
            manifest.append(
                {
                    "table_name": spec.name,
                    "row_count": loaded,
                    "snapshot_modified_at": modified,
                    "published_at": published,
                }
            )
    _write_manifest(client, target, manifest)
    return manifest


def main() -> None:
    """CLI: mirror the published Gold snapshot into BigQuery."""
    target = target_from_env()
    if target is None:
        raise SystemExit("BQ_PROJECT is not set, BigQuery mirroring is off")
    for row in publish_mirror(target, get_settings().published_gold):
        print(f"mirrored {row['row_count']:>6} rows  {row['table_name']}")


if __name__ == "__main__":
    main()

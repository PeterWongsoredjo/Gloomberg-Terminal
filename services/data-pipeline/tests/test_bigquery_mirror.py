"""The BigQuery mirror must refuse bad copies and never let tables quietly expire."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest

from pipeline.bigquery import mirror
from pipeline.bigquery.mirror import MirrorMismatch, MirrorTable, export_snapshot
from pipeline.bigquery.target import BigQueryTarget, target_from_env

TARGET = BigQueryTarget(project="proj", location="asia-southeast2")


def _gold(path: Path) -> Path:
    """A tiny published snapshot with a naive timestamp and a list column."""
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "create table fct_news_item(item_id varchar, published_at timestamp, tickers varchar[])"
        )
        con.execute(
            "insert into fct_news_item values "
            "('a1', '2026-09-11 03:00:00', ['BBRI']), ('a2', '2026-09-11 04:30:00', [])"
        )
    finally:
        con.close()
    return path


class _Table:
    def __init__(self, rows: int, expires: datetime | None) -> None:
        self.num_rows = rows
        self.expires = expires


class _Job:
    def result(self) -> None:
        return None


class FakeClient:
    """Records every call and answers with the row count it is told to."""

    def __init__(self, rows: int = 2, expires: datetime | None = None) -> None:
        self.rows = rows
        self.expires = expires
        self.updated: list[datetime | None] = []
        self.loaded: list[str] = []
        self.manifest: list[dict[str, Any]] = []

    def create_dataset(self, dataset: Any, exists_ok: bool = False) -> None:
        return None

    def load_table_from_file(self, handle: Any, table_id: str, job_config: Any = None) -> _Job:
        self.loaded.append(table_id)
        return _Job()

    def load_table_from_json(
        self, rows: list[dict[str, Any]], table_id: str, job_config: Any = None
    ) -> _Job:
        self.manifest = rows
        return _Job()

    def get_table(self, table_id: str) -> _Table:
        return _Table(self.rows, self.expires)

    def update_table(self, table: _Table, fields: list[str]) -> None:
        self.updated.append(table.expires)


def test_no_project_means_bigquery_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.bigquery.target.load_root_env", lambda: None)
    monkeypatch.delenv("BQ_PROJECT", raising=False)
    assert target_from_env() is None


def test_location_defaults_to_jakarta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.bigquery.target.load_root_env", lambda: None)
    monkeypatch.setenv("BQ_PROJECT", " gloomberg-terminal ")
    monkeypatch.setenv("BQ_LOCATION", "")
    assert target_from_env() == BigQueryTarget("gloomberg-terminal", "asia-southeast2")


def test_credentials_come_only_from_their_own_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shared google variable would leak these credentials into gemini."""
    monkeypatch.setattr("pipeline.bigquery.target.load_root_env", lambda: None)
    monkeypatch.setenv("BQ_PROJECT", "gloomberg-terminal")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/wrong/shared.json")
    monkeypatch.setenv("BQ_CREDENTIALS", "/opt/gloomberg/secrets/bq-publisher.json")
    target = target_from_env()
    assert target is not None
    assert target.credentials_path == "/opt/gloomberg/secrets/bq-publisher.json"


def test_no_credentials_variable_falls_back_to_your_own_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.bigquery.target.load_root_env", lambda: None)
    monkeypatch.setenv("BQ_PROJECT", "gloomberg-terminal")
    monkeypatch.delenv("BQ_CREDENTIALS", raising=False)
    target = target_from_env()
    assert target is not None and target.credentials_path is None


def test_export_marks_naive_timestamps_as_utc(tmp_path: Path) -> None:
    """Gold stores UTC in naive columns, BigQuery must receive real instants."""
    gold = _gold(tmp_path / "gold.duckdb")
    exported = export_snapshot(gold, (MirrorTable("fct_news_item"),), tmp_path)
    parquet, rows = exported["fct_news_item"]
    source = f"read_parquet('{parquet.as_posix()}')"

    assert rows == 2
    con = duckdb.connect()
    try:
        con.execute("set TimeZone = 'UTC'")
        described = con.execute(
            f"select typeof(published_at), min(published_at) from {source} group by 1"
        ).fetchone()
        tickers = con.execute(f"select tickers from {source} where item_id = 'a1'").fetchone()
    finally:
        con.close()
    assert described == ("TIMESTAMP WITH TIME ZONE", datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc))
    assert tickers == (["BBRI"],)


def test_export_refuses_a_table_the_snapshot_lacks(tmp_path: Path) -> None:
    gold = _gold(tmp_path / "gold.duckdb")
    with pytest.raises(MirrorMismatch, match="not in the published snapshot"):
        export_snapshot(gold, (MirrorTable("fct_daily_trade"),), tmp_path)


def test_a_short_load_is_refused_not_accepted(tmp_path: Path) -> None:
    """Fail closed: a partial copy is an error, never a smaller table."""
    parquet = tmp_path / "x.parquet"
    parquet.write_bytes(b"x")
    client = FakeClient(rows=1)
    with pytest.raises(MirrorMismatch, match="sent 2 rows, BigQuery holds 1"):
        mirror.load_table(client, TARGET, MirrorTable("fct_news_item"), parquet, expected=2)  # type: ignore[arg-type]


def test_a_sandbox_table_gets_its_expiry_pushed_forward() -> None:
    """Reloading never extends expiry, so without this Gold vanishes after 60 days."""
    client = FakeClient(expires=datetime.now(timezone.utc) + timedelta(days=3))
    mirror.keep_alive(client, "proj.gloomberg_gold.fct_news_item")  # type: ignore[arg-type]

    assert len(client.updated) == 1
    pushed = client.updated[0]
    assert pushed is not None
    assert pushed > datetime.now(timezone.utc) + timedelta(days=58)


def test_a_table_without_expiry_is_never_given_one() -> None:
    """Once billing is on, setting a date would reintroduce deletion."""
    client = FakeClient(expires=None)
    mirror.keep_alive(client, "proj.gloomberg_gold.fct_news_item")  # type: ignore[arg-type]
    assert client.updated == []


def test_publish_loads_each_table_and_writes_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold = _gold(tmp_path / "gold.duckdb")
    client = FakeClient(rows=2, expires=datetime.now(timezone.utc) + timedelta(days=1))
    monkeypatch.setattr(mirror, "MIRROR_TABLES", (MirrorTable("fct_news_item", ("source",)),))
    monkeypatch.setattr(BigQueryTarget, "client", lambda self: client)

    manifest = mirror.publish_mirror(TARGET, gold)

    assert client.loaded == ["proj.gloomberg_gold.fct_news_item"]
    assert [row["table_name"] for row in manifest] == ["fct_news_item"]
    assert manifest[0]["row_count"] == 2
    assert client.manifest == manifest
    assert len(client.updated) == 2


def test_publish_refuses_a_missing_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        mirror.publish_mirror(TARGET, tmp_path / "never.duckdb")

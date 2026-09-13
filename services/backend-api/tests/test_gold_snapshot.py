"""The served snapshot must follow the file publish swaps in, not the one it opened."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest

from app.core.snapshot import GoldSnapshot


def _write_snapshot(path: Path, ticker: str) -> None:
    """Builds a one-row Gold-shaped file at the path."""
    con = duckdb.connect(str(path))
    try:
        con.execute("create table dim_security as select ? as ticker", [ticker])
    finally:
        con.close()


def test_reads_the_snapshot_it_opened(tmp_path: Path) -> None:
    gold = tmp_path / "gold.duckdb"
    _write_snapshot(gold, "BBRI")
    snapshot = GoldSnapshot(str(gold))
    try:
        assert snapshot.query("select ticker from dim_security", []) == [{"ticker": "BBRI"}]
    finally:
        snapshot.close()


def test_reopens_when_the_file_identity_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The detection half of the swap, on every platform."""
    gold = tmp_path / "gold.duckdb"
    _write_snapshot(gold, "BBRI")
    snapshot = GoldSnapshot(str(gold))
    try:
        assert snapshot.query("select ticker from dim_security", []) == [{"ticker": "BBRI"}]

        reopened: list[str] = []
        monkeypatch.setattr(GoldSnapshot, "_stat", lambda self: (1, 2, 3))
        original = GoldSnapshot._open

        def _spy(self: GoldSnapshot) -> None:
            reopened.append("yes")
            original(self)

        monkeypatch.setattr(GoldSnapshot, "_open", _spy)
        snapshot.query("select ticker from dim_security", [])
        assert reopened == ["yes"]
    finally:
        snapshot.close()


@pytest.mark.skipif(
    sys.platform == "win32", reason="windows will not replace a file duckdb holds open"
)
def test_follows_the_file_across_an_atomic_swap(tmp_path: Path) -> None:
    gold = tmp_path / "gold.duckdb"
    _write_snapshot(gold, "BBRI")
    snapshot = GoldSnapshot(str(gold))
    try:
        assert snapshot.query("select ticker from dim_security", []) == [{"ticker": "BBRI"}]

        # exactly what publish() does: build beside it, then replace
        fresh = tmp_path / "gold.duckdb.tmp"
        _write_snapshot(fresh, "GOTO")
        os.replace(fresh, gold)

        assert snapshot.query("select ticker from dim_security", []) == [{"ticker": "GOTO"}]
    finally:
        snapshot.close()


def test_a_missing_snapshot_is_empty_not_an_error(tmp_path: Path) -> None:
    snapshot = GoldSnapshot(str(tmp_path / "never-published.duckdb"))
    try:
        assert snapshot.query("select 1", []) == []
    finally:
        snapshot.close()


def test_picks_up_a_snapshot_that_appears_after_startup(tmp_path: Path) -> None:
    gold = tmp_path / "gold.duckdb"
    snapshot = GoldSnapshot(str(gold))
    try:
        assert snapshot.query("select ticker from dim_security", []) == []
        _write_snapshot(gold, "TLKM")
        assert snapshot.query("select ticker from dim_security", []) == [{"ticker": "TLKM"}]
    finally:
        snapshot.close()


def test_an_unbuilt_table_still_raises_for_the_reader_to_catch(tmp_path: Path) -> None:
    gold = tmp_path / "gold.duckdb"
    _write_snapshot(gold, "BBRI")
    snapshot = GoldSnapshot(str(gold))
    try:
        with pytest.raises(duckdb.CatalogException):
            snapshot.query("select * from fct_news_item", [])
    finally:
        snapshot.close()

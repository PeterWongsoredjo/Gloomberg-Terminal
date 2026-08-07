"""The universe guard on insight subjects, and how the two subject queries differ."""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
import pytest

from orchestration.tasks import subjects as subjects_mod

TD = date(2026, 7, 15)


class _FakeCursor:
    def __init__(self, rows: list[tuple[str, ...]], captured: dict[str, Any]) -> None:
        self._rows = rows
        self._captured = captured

    def fetchall(self) -> list[tuple[str, ...]]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[tuple[str, ...]], captured: dict[str, Any]) -> None:
        self._rows = rows
        self._captured = captured

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
        self._captured["sql"], self._captured["params"] = sql, params
        return _FakeCursor(self._rows, self._captured)


def _patch_connect(
    monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, ...]], captured: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        "orchestration.tasks.subjects.psycopg.connect", lambda *a, **k: _FakeConn(rows, captured)
    )


def test_an_empty_universe_never_reaches_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """A universe that failed to load must not silently select every ticker with news."""

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("an empty universe must short-circuit before connecting")

    monkeypatch.setattr("orchestration.tasks.subjects.psycopg.connect", explode)

    assert subjects_mod.stale_insight_subjects("dsn", TD, 8, []) == []
    assert subjects_mod.day_insight_subjects("dsn", TD, 8, []) == []


def test_the_universe_is_bound_into_the_stale_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_connect(monkeypatch, [("BBCA",)], captured)

    assert subjects_mod.stale_insight_subjects("dsn", TD, 8, ["BBCA", "TLKM"]) == ["BBCA"]
    assert "ticker = any(%s)" in captured["sql"]
    assert captured["params"] == (TD, ["BBCA", "TLKM"], TD, 8)


def test_the_eod_query_ignores_the_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A conclusion is owed at the close whether or not the hourly already saw the news."""
    captured: dict[str, Any] = {}
    _patch_connect(monkeypatch, [("BBCA",), ("TLKM",)], captured)

    assert subjects_mod.day_insight_subjects("dsn", TD, 12, ["BBCA", "TLKM"]) == ["BBCA", "TLKM"]
    assert "evidence_fingerprint" not in captured["sql"]
    assert captured["params"] == (TD, ["BBCA", "TLKM"], 12)


def test_an_unreachable_postgres_yields_no_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("down")

    monkeypatch.setattr("orchestration.tasks.subjects.psycopg.connect", boom)

    assert subjects_mod.stale_insight_subjects("dsn", TD, 8, ["BBCA"]) == []
    assert subjects_mod.day_insight_subjects("dsn", TD, 8, ["BBCA"]) == []


def test_the_empty_universe_note_is_distinct_from_a_quiet_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable universe.yaml must be visible in the ledger, not look like no news."""
    monkeypatch.setattr(subjects_mod, "get_settings", lambda: type("S", (), {"postgres_dsn": "dsn"})())
    captured: dict[str, Any] = {}
    _patch_connect(monkeypatch, [], captured)

    quiet = subjects_mod.insight_subjects.fn(TD, 8, ["BBCA"])
    blind = subjects_mod.insight_subjects.fn(TD, 8, [])

    assert quiet.notes == "0 subjects with unseen news"
    assert blind.notes == subjects_mod._EMPTY_UNIVERSE_NOTE
    assert quiet.notes != blind.notes

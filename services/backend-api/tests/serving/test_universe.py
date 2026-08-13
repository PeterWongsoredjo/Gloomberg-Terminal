"""The curated universe endpoint: the real file, the broken file, and the envelope.

The loader mirrors the orchestration one on purpose; both read the same file, so a
malformed edit must degrade to empty on both sides rather than half-serve a list.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.serving.universe import get_universe, read_universe

_CURATED = Path(settings.universe_file)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "universe.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_reads_the_real_curated_file() -> None:
    """The shipped file parses, keeps file order, and starts at the composite."""
    universe = read_universe(_CURATED)

    assert universe.tickers[0] == "IHSG"
    assert "BBCA" in universe.tickers
    assert len(universe.tickers) > 40
    assert all(len(t) == 4 and t.isupper() for t in universe.tickers)


def test_as_of_is_when_the_file_changed() -> None:
    universe = read_universe(_CURATED)

    assert universe.as_of.timestamp() == pytest.approx(_CURATED.stat().st_mtime)


def test_keeps_file_order_and_drops_repeats(tmp_path: Path) -> None:
    path = _write(tmp_path, "universe:\n  - ticker: TLKM\n  - ticker: BBCA\n  - ticker: TLKM\n")

    assert read_universe(path).tickers == ("TLKM", "BBCA")


def test_lowercase_entry_is_accepted_uppercased(tmp_path: Path) -> None:
    path = _write(tmp_path, "universe:\n  - ticker: bbca\n")

    assert read_universe(path).tickers == ("BBCA",)


@pytest.mark.parametrize(
    "body",
    [
        "universe:\n  - ticker: TOOLONG\n  - ticker: BBCA\n",
        "universe:\n  - ticker: 1234\n",
        "universe:\n  - BBCA\n",
        "universe:\n  - note: no ticker here\n",
    ],
)
def test_one_malformed_entry_empties_the_whole_universe(tmp_path: Path, body: str) -> None:
    """Half a universe would silently hide tickers, so a bad edit serves none."""
    assert read_universe(_write(tmp_path, body)).tickers == ()


@pytest.mark.parametrize("body", ["tickers:\n  - BBCA\n", "[]\n", ""])
def test_wrong_shape_is_an_empty_universe(tmp_path: Path, body: str) -> None:
    assert read_universe(_write(tmp_path, body)).tickers == ()


def test_unparseable_yaml_is_an_empty_universe(tmp_path: Path) -> None:
    assert read_universe(_write(tmp_path, "universe: [oops\n")).tickers == ()


def test_missing_file_is_an_empty_universe(tmp_path: Path) -> None:
    assert read_universe(tmp_path / "nope.yaml").tickers == ()


def test_cache_reloads_after_the_file_is_edited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Curating a ticker must show up without restarting the backend."""
    path = _write(tmp_path, "universe:\n  - ticker: BBCA\n")
    monkeypatch.setattr(settings, "universe_file", str(path))
    assert get_universe().tickers == ("BBCA",)

    path.write_text("universe:\n  - ticker: BBCA\n  - ticker: TLKM\n", encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 1))

    assert get_universe().tickers == ("BBCA", "TLKM")


def test_endpoint_serves_the_curated_tickers_in_an_envelope() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/universe")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["tickers"][0] == "IHSG"
    assert "BBCA" in body["data"]["tickers"]
    # every payload travels with its freshness, this one is config, never stale
    assert body["freshness_slo_met"] is True
    assert body["quality_flags"] == []
    assert body["data_as_of"] and body["market_state"]


def test_endpoint_flags_an_unreadable_file_instead_of_faking_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "universe_file", str(tmp_path / "gone.yaml"))
    with TestClient(app) as client:
        response = client.get("/api/v1/universe")

    body = response.json()
    assert body["data"]["tickers"] == []
    assert body["quality_flags"] == ["MISSING_UPSTREAM"]

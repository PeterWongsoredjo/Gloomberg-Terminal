from pathlib import Path

from orchestration.universe import INDEX_SUBJECTS, load_universe

_GOOD = """universe:
  - ticker: BBCA
    note: big-four bank
  - ticker: tlkm
    note: lowercase is normalized
  - ticker: BBCA
    note: duplicate is dropped
"""


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "universe.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_tickers_in_order_normalized_deduped(tmp_path: Path) -> None:
    assert load_universe(_write(tmp_path, _GOOD)) == ["BBCA", "TLKM"]


def test_missing_file_degrades_to_empty(tmp_path: Path) -> None:
    assert load_universe(tmp_path / "nope.yaml") == []


def test_unparseable_yaml_degrades_to_empty(tmp_path: Path) -> None:
    assert load_universe(_write(tmp_path, "universe: [unclosed")) == []


def test_missing_universe_key_degrades_to_empty(tmp_path: Path) -> None:
    assert load_universe(_write(tmp_path, "tickers:\n  - BBCA\n")) == []


def test_non_list_universe_degrades_to_empty(tmp_path: Path) -> None:
    assert load_universe(_write(tmp_path, "universe: BBCA\n")) == []


def test_bad_ticker_shape_degrades_whole_file(tmp_path: Path) -> None:
    bad = "universe:\n  - ticker: BBCA\n  - ticker: TOOLONG\n"
    assert load_universe(_write(tmp_path, bad)) == []


def test_entry_without_ticker_degrades_whole_file(tmp_path: Path) -> None:
    bad = "universe:\n  - note: forgot the ticker\n"
    assert load_universe(_write(tmp_path, bad)) == []


def test_committed_universe_file_is_valid() -> None:
    committed = Path(__file__).resolve().parents[1] / "config" / "universe.yaml"
    tickers = load_universe(committed)
    assert tickers, "the committed universe.yaml must never be empty or malformed"
    assert all(len(t) == 4 and t.isupper() for t in tickers)
    assert "IHSG" in tickers


def test_the_index_is_a_recognised_subject() -> None:
    """IHSG has no dim_security row, so it needs naming somewhere to stay scoreable."""
    assert "IHSG" in INDEX_SUBJECTS

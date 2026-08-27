"""
The registry record itself: parsing IDX profiles, and the committed fallback file.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.reference.aliases import aliases_for, generic_words, normalize

BASELINE_PATH = Path(__file__).with_name("idx_security_baseline.csv")

INDEX_TICKERS = ("IHSG",)

_INDEX_ROWS = (
    ("IHSG", "Indeks Harga Saham Gabungan", "INDEX", ("INDEKS HARGA SAHAM GABUNGAN", "COMPOSITE INDEX")),
)

TICKER_RE = re.compile(r"^[A-Z]{4}$")

_BOARDS = {
    "Utama": "MAIN",
    "Pengembangan": "DEVELOPMENT",
    "Akselerasi": "ACCELERATION",
    "Ekonomi Baru": "NEW_ECONOMY",
    "Pemantauan Khusus": "WATCHLIST",
}

_CSV_COLUMNS = ("ticker", "company_name", "board", "aliases")


@dataclass(frozen=True)
class Security:
    """One registry row: a ticker plus every name form that identifies it."""

    ticker: str
    company_name: str
    board: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _index_securities() -> list[Security]:
    return [Security(t, name, board, aliases) for t, name, board, aliases in _INDEX_ROWS]


def _drop_ambiguous(securities: list[Security]) -> list[Security]:
    """Removes any name form that more than one issuer would answer to."""
    owners: dict[str, set[str]] = {}
    for security in securities:
        for alias in security.aliases:
            owners.setdefault(alias, set()).add(security.ticker)
    contested = {alias for alias, tickers in owners.items() if len(tickers) > 1}
    return [
        Security(s.ticker, s.company_name, s.board, tuple(a for a in s.aliases if a not in contested))
        for s in securities
    ]


def profile_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _equity_rows(payload: Any) -> list[tuple[str, str, str | None]]:
    """Ticker, company name and board for every listed equity in the payload."""
    seen: dict[str, tuple[str, str, str | None]] = {}
    for row in profile_rows(payload):
        if not row.get("EfekEmiten_Saham"):
            continue
        ticker = str(row.get("KodeEmiten") or "").strip().upper()
        company_name = str(row.get("NamaEmiten") or "").strip()
        if not TICKER_RE.fullmatch(ticker) or not company_name:
            continue  # unparseable identity is dropped, never guessed at
        board = _BOARDS.get(str(row.get("PapanPencatatan") or "").strip())
        seen.setdefault(ticker, (ticker, company_name, board))
    return list(seen.values())


def securities_from_profiles(payload: Any) -> list[Security]:
    """Types an IDX company-profile payload into registry rows, index subjects included."""
    rows = _equity_rows(payload)
    generic = generic_words(name for _, name, _ in rows)
    securities = {
        ticker: Security(ticker, name, board, tuple(aliases_for(name, generic)))
        for ticker, name, board in rows
    }
    for index_row in _index_securities():
        securities.setdefault(index_row.ticker, index_row)
    return _drop_ambiguous(sorted(securities.values(), key=lambda s: s.ticker))


def to_csv(securities: list[Security]) -> str:
    """Serializes the registry to the committed baseline format."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    for security in securities:
        writer.writerow([security.ticker, security.company_name, security.board or "", "|".join(security.aliases)])
    return buffer.getvalue()


def from_csv(text: str) -> list[Security]:
    """Reads the committed baseline back into registry rows."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        aliases = tuple(a for a in (row.get("aliases") or "").split("|") if a)
        out.append(
            Security(
                ticker=str(row["ticker"]).strip().upper(),
                company_name=str(row["company_name"]).strip(),
                board=(row.get("board") or "").strip() or None,
                aliases=aliases,
            )
        )
    return out


def load_baseline() -> list[Security]:
    """The committed registry, the floor the resolver can always fall back to."""
    return from_csv(BASELINE_PATH.read_text(encoding="utf-8"))


def alias_index(securities: list[Security]) -> dict[str, str]:
    """Every name form mapped to its ticker, for the headline name pass."""
    return {normalize(alias): s.ticker for s in securities for alias in s.aliases}


def main() -> None:
    """Rewrites the committed baseline from an IDX company-profile payload."""
    import json
    import sys

    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    securities = securities_from_profiles(payload)
    BASELINE_PATH.write_text(to_csv(securities), encoding="utf-8")
    print(f"wrote {len(securities)} securities to {BASELINE_PATH}")


if __name__ == "__main__":
    main()

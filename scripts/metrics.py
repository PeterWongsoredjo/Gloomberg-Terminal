"""
Regenerates every number quoted in the README, so no claim goes unchecked.

Run from the repo root with any service venv that has zstandard and duckdb:
    services/data-pipeline/.venv/Scripts/python.exe scripts/metrics.py

Each section degrades on its own. A missing warehouse or an unreachable API
reports UNAVAILABLE instead of killing the run.
"""

from __future__ import annotations

import json
import pathlib
import sys
import statistics
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, timedelta
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
LIVE_API = "https://terminal.peterwongsoredjo.dev/api/v1"
FROZEN = REPO / "services/data-pipeline/fixtures/frozen"
GOLD = REPO / "services/backend-api/warehouse/gold.duckdb"
MANIFEST = REPO / "dbt/target/manifest.json"

# continuous collection began the day after the 2026-08-06 deploy
PRODUCTION_START = date(2026, 8, 7)

UNAVAILABLE = "UNAVAILABLE"


def _get(path: str, timeout: int = 40) -> dict[str, Any]:
    with urllib.request.urlopen(f"{LIVE_API}/{path}", timeout=timeout) as response:
        payload: dict[str, Any] = json.load(response)
    return payload


# squeezes the real captured payloads with the pipeline's own codec
def compression() -> dict[str, Any]:
    try:
        import zstandard
    except ImportError:
        return {"status": UNAVAILABLE, "why": "zstandard not installed in this venv"}

    payloads = sorted(FROZEN.rglob("part-*.json")) + sorted(FROZEN.rglob("part-*.xml"))
    if not payloads:
        return {"status": UNAVAILABLE, "why": f"no captured payloads under {FROZEN}"}

    compressor = zstandard.ZstdCompressor()
    raw = sum(p.stat().st_size for p in payloads)
    packed = sum(len(compressor.compress(p.read_bytes())) for p in payloads)
    return {
        "status": "ok",
        "objects": len(payloads),
        "raw_bytes": raw,
        "packed_bytes": packed,
        "ratio": raw / packed,
        "saved_pct": 100 * (1 - packed / raw),
    }


# walks the public news feed to the end, one page at a time
def _all_articles(page_size: int = 200, max_pages: int = 80) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        suffix = f"&cursor={cursor}" if cursor else ""
        page = _get(f"news?limit={page_size}{suffix}")["data"]
        rows.extend(page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return rows


# counts what the live terminal has actually ingested and scored
def live_corpus() -> dict[str, Any]:
    try:
        rows = _all_articles()
        tape = _get("tape")["data"]
    except (urllib.error.URLError, TimeoutError, KeyError, OSError) as exc:
        return {"status": UNAVAILABLE, "why": f"{type(exc).__name__}: {exc}"}

    rows = [
        r
        for r in rows
        if r.get("published_at")
        and PRODUCTION_START <= date.fromisoformat(r["published_at"][:10]) <= date.today()
    ]
    scored = [r for r in rows if r.get("sentiment_label")]
    tickers = Counter(
        verdict.get("ticker")
        for row in rows
        for verdict in (row.get("ticker_sentiments") or [])
    )
    providers = Counter(
        (row["sentiment_provenance"] or {}).get("provider")
        for row in scored
        if row.get("sentiment_provenance")
    )
    per_day = Counter(r["published_at"][:10] for r in rows if r.get("published_at"))
    published = sorted(per_day)
    today = date.today()
    settled = {
        day: n
        for day, n in per_day.items()
        if today - timedelta(days=30) <= date.fromisoformat(day) < today
    }
    weekday = [n for day, n in settled.items() if date.fromisoformat(day).weekday() < 5]
    weekend = [n for day, n in settled.items() if date.fromisoformat(day).weekday() >= 5]
    tape_rows = tape if isinstance(tape, list) else tape.get("rows", [])

    return {
        "status": "ok",
        "articles": len(rows),
        "unique_articles": len({r.get("item_id") for r in rows}),
        "scored": len(scored),
        "ticker_verdicts": sum(tickers.values()),
        "distinct_tickers": len(tickers),
        "providers": dict(providers),
        "first_day": published[0] if published else None,
        "last_day": published[-1] if published else None,
        "median_weekday": statistics.median(weekday) if weekday else None,
        "median_weekend": statistics.median(weekend) if weekend else None,
        "settled_days": len(settled),
        "active_days": len(per_day),
        "tape_rows": len(tape_rows),
    }


# reads how deep the corporate action history actually goes
def corporate_actions() -> dict[str, Any]:
    if not GOLD.exists():
        return {"status": UNAVAILABLE, "why": f"no published Gold at {GOLD}"}
    try:
        import duckdb
    except ImportError:
        return {"status": UNAVAILABLE, "why": "duckdb not installed in this venv"}

    connection = duckdb.connect(str(GOLD), read_only=True)
    try:
        row = connection.execute(
            "select count(*), min(effective_trade_date), max(effective_trade_date),"
            " count(distinct ticker) from fct_corporate_action"
        ).fetchone()
        if row is None or not row[0]:
            return {"status": UNAVAILABLE, "why": "fct_corporate_action is empty"}
        events, first, last, tickers = row
        kinds = connection.execute(
            "select event_type, count(*) from fct_corporate_action group by 1 order by 2 desc"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - a not-yet-built table is a report, not a crash
        return {"status": UNAVAILABLE, "why": f"{type(exc).__name__}: {exc}"}
    finally:
        connection.close()

    return {
        "status": "ok",
        "events": events,
        "tickers": tickers,
        "first": str(first),
        "last": str(last),
        "years": last.year - first.year,
        "event_types": dict(kinds),
    }


# asks the compiled dbt project how big it really is
def warehouse() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {"status": UNAVAILABLE, "why": "run dbt parse to build target/manifest.json"}
    nodes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    kinds = Counter(n["resource_type"] for n in nodes["nodes"].values())
    return {
        "status": "ok",
        "models": kinds["model"],
        "tests": kinds["test"],
        "seeds": kinds["seed"],
        "snapshots": kinds["snapshot"],
        "sources": len(nodes["sources"]),
    }


# counts every test function checked into the services
def test_suite() -> dict[str, Any]:
    files = [p for p in (REPO / "services").rglob("tests/**/test_*.py") if ".venv" not in p.parts]
    functions = sum(
        line.lstrip().startswith(("def test_", "async def test_"))
        for path in files
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
    )
    return {"status": "ok", "files": len(files), "functions": functions}


def _line(label: str, section: dict[str, Any], body: str) -> str:
    if section["status"] != "ok":
        return f"| {label} | {UNAVAILABLE} — {section['why']} |"
    return f"| {label} | {body} |"


def render() -> str:
    zstd, live, actions, dbt, tests = (
        compression(),
        live_corpus(),
        corporate_actions(),
        warehouse(),
        test_suite(),
    )

    rows = [
        "| Metric | Measured |",
        "|---|---|",
        _line(
            "Compression",
            zstd,
            f"**{zstd.get('ratio', 0):.2f}x**, {zstd.get('saved_pct', 0):.1f}% saved "
            f"({zstd.get('raw_bytes', 0):,} to {zstd.get('packed_bytes', 0):,} bytes "
            f"over {zstd.get('objects', 0)} payloads)",
        ),
        _line(
            "Live corpus",
            live,
            f"**{live.get('articles', 0)}** articles, "
            f"{live.get('unique_articles', 0)} unique, "
            f"**{live.get('scored', 0)}** scored, "
            f"{live.get('first_day')} to {live.get('last_day')} "
            f"({live.get('active_days')} active days)",
        ),
        _line(
            "Articles per day",
            live,
            f"median **{live.get('median_weekday')}** on a trading day, "
            f"**{live.get('median_weekend')}** at the weekend "
            f"(over {live.get('settled_days')} settled days, today and any "
            f"future-dated rows excluded)",
        ),
        _line(
            "Ticker verdicts",
            live,
            f"**{live.get('ticker_verdicts', 0)}** across "
            f"**{live.get('distinct_tickers', 0)}** issuers",
        ),
        _line("Provider split", live, str(live.get("providers"))),
        _line("Securities on tape", live, f"**{live.get('tape_rows', 0)}**"),
        _line(
            "Corporate actions",
            actions,
            f"**{actions.get('events', 0)}** events across "
            f"**{actions.get('tickers', 0)}** tickers, "
            f"{actions.get('first')} to {actions.get('last')} "
            f"(**{actions.get('years', 0)} years**)",
        ),
        _line(
            "dbt project",
            dbt,
            f"**{dbt.get('models', 0)}** models, **{dbt.get('tests', 0)}** tests, "
            f"{dbt.get('sources', 0)} sources, {dbt.get('seeds', 0)} seeds, "
            f"{dbt.get('snapshots', 0)} snapshot",
        ),
        _line(
            "Python tests",
            tests,
            f"**{tests.get('functions', 0)}** functions in {tests.get('files', 0)} files",
        ),
    ]
    return "\n".join(rows)


def main() -> int:
    print(render())
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
the coverage gate. The manifest is written during ingest, here we
decide, whether the run promotes, degrades to PARTIAL, or blocks.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from prefect import task

from pipeline.bronze.feeds import FEEDS

from orchestration.results import PhaseResult

# only full-universe feeds carry a meaningful coverage ratio and missing-ticker list
UNIVERSE_PAIRS = {(spec.source, spec.dataset) for spec in FEEDS.values() if spec.is_universe}


def evaluate_coverage(
    manifests: list[dict[str, Any]],
    trade_date: date,
    floor: float,
    hard_min: float,
) -> PhaseResult:
    dated = [m for m in manifests if m["trade_date"] == trade_date.isoformat()]
    universe = [m for m in dated if (m["source"], m["dataset"]) in UNIVERSE_PAIRS]

    if not universe:
        coverage, missing = 0.0, []
    else:
        coverage = min(
            0.0 if m["status"] == "FAILED" else m["quality"]["coverage_ratio"] for m in universe
        )
        missing = sorted({t for m in universe for t in m["quality"]["missing_tickers"]})

    degraded = any(m["status"] in ("FAILED", "PARTIAL") for m in dated)

    if coverage < hard_min:
        status = "FAILED"
    elif coverage < floor:
        status = "PARTIAL"
    else:
        status = "PARTIAL" if degraded else "SUCCESS"
    promotion_ok = status != "FAILED"

    ratio = round(coverage, 4)
    gate = {
        "coverage_ratio": ratio,
        "coverage_floor": floor,
        "coverage_hard_min": hard_min,
        "promotion_ok": promotion_ok,
        "missing_tickers": missing,
    }
    verb = "promote" if promotion_ok else "blocked (below hard minimum)"
    notes = f"coverage {ratio} vs floor {floor} / hard_min {hard_min} -> {verb}"
    return PhaseResult(status=status, payload=promotion_ok, notes=notes, gate=gate)


@task(name="coverage_gate")
def coverage_gate(
    manifests: list[dict[str, Any]],
    trade_date: date,
    floor: float,
    hard_min: float,
) -> PhaseResult:
    return evaluate_coverage(manifests, trade_date, floor, hard_min)

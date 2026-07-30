"""
Runs dbt transformations sequentially (Staging -> Intermediate -> Marts) in isolated subprocesses.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefect import task

from pipeline.config import load_root_env

from orchestration.config import OrchestrationConfig
from orchestration.errors import DbtCompilationError, DbtTestFailure, DbtTransientError
from orchestration.results import PhaseResult
from orchestration.retries import DBT_DELAY_SECONDS, DBT_RETRIES, retry_on_transient_dbt

# dependency-correct order: staging views must exist before the snapshot reads them
PHASES: list[tuple[str, list[str]]] = [
    ("seed", ["seed"]),
    ("staging", ["run", "--select", "staging"]),
    ("snapshot", ["snapshot"]),
    ("intermediate", ["run", "--select", "intermediate"]),
    ("marts_core", ["run", "--select", "marts.core"]),
    ("marts_serving", ["run", "--select", "marts.serving"]),
    ("test", ["test"]),
]

# just the insight lineage, for the second pass at the close
INSIGHT_PHASES: list[tuple[str, list[str]]] = [
    ("staging", ["run", "--select", "stg_agent_artifact__insight"]),
    ("marts_core", ["run", "--select", "fct_insight"]),
    ("test", ["test", "--select", "fct_insight"]),
]

# stderr signatures that mean "retry once", not "a real error"
_TRANSIENT_SIGNATURES = (
    "conflicting lock",
    "could not set lock",
    "connection refused",
    "temporarily unavailable",
    "timed out",
    "io error",
    "http error",
)


@dataclass
class _PhaseRun:
    """One dbt phase's raw outcome, before it is classified pass/fail."""

    returncode: int
    stderr: str
    run_results: dict[str, Any] | None


def _run_dbt(dbt_dir: Path, args: list[str]) -> _PhaseRun:
    """Runs one dbt phase and reads its fresh run_results.json."""
    results_path = dbt_dir / "target" / "run_results.json"
    if results_path.exists():
        results_path.unlink()  # avoid reading a stale result on a compile error
    cmd = ["uv", "run", "dbt", *args, "--profiles-dir", ".", "--target", "analytical"]
    proc = subprocess.run(
        cmd, cwd=dbt_dir, env=os.environ.copy(), capture_output=True, text=True
    )
    run_results = json.loads(results_path.read_text()) if results_path.exists() else None
    return _PhaseRun(proc.returncode, proc.stderr, run_results)


def _looks_transient(stderr: str) -> bool:
    """True when the failure text matches a known transient (lock / IO / network) signature."""
    low = stderr.lower()
    return any(sig in low for sig in _TRANSIENT_SIGNATURES)


def _classify(phase: str, run: _PhaseRun) -> None:
    """Raises the typed dbt error for a failed phase; returns cleanly on success or warns."""
    if run.returncode == 0:
        return
    if run.run_results is None:
        if _looks_transient(run.stderr):
            raise DbtTransientError(f"{phase}: transient before results; {run.stderr[-400:]}")
        raise DbtCompilationError(f"{phase}: no run_results; {run.stderr[-400:]}")

    failed = [r for r in run.run_results["results"] if r["status"] in ("fail", "error")]
    test_fails = [r for r in failed if r["unique_id"].startswith("test.")]
    if test_fails:
        names = ", ".join(r["unique_id"] for r in test_fails[:5])
        raise DbtTestFailure(f"{phase}: {len(test_fails)} error-severity test(s) failed: {names}")
    if failed:
        # classify on the failing node's own message, not the aggregate stderr
        if any(_looks_transient(str(r.get("message", ""))) for r in failed):
            raise DbtTransientError(f"{phase}: transient node failure; {failed[0].get('message')}")
        names = ", ".join(r["unique_id"] for r in failed[:5])
        raise DbtCompilationError(f"{phase}: {len(failed)} node(s) errored: {names}")
    raise DbtTransientError(f"{phase}: dbt exit {run.returncode}; {run.stderr[-300:]}")


def _status_counts(run_results: dict[str, Any] | None) -> dict[str, int]:
    """Tallies node statuses (pass/warn/fail/...) for the OR-06 event notes."""
    counts: dict[str, int] = {}
    for node in (run_results or {}).get("results", []):
        counts[node["status"]] = counts.get(node["status"], 0) + 1
    return counts


@task(
    name="dbt_build",
    tags=["duckdb_writer"],
    retries=DBT_RETRIES,
    retry_delay_seconds=DBT_DELAY_SECONDS,
    retry_condition_fn=retry_on_transient_dbt,
)
def dbt_build(
    config: OrchestrationConfig, phases: list[tuple[str, list[str]]] | None = None
) -> PhaseResult:
    """Runs the given dbt phases in order; any error-severity failure aborts before promotion."""
    load_root_env()
    last_invocation: str | None = None
    test_counts: dict[str, int] = {}
    for name, args in phases or PHASES:
        run = _run_dbt(config.dbt_dir, args)
        _classify(name, run)
        if run.run_results is not None:
            last_invocation = run.run_results["metadata"]["invocation_id"]
            if name == "test":
                test_counts = _status_counts(run.run_results)
    passed, warned = test_counts.get("pass", 0), test_counts.get("warn", 0)
    notes = f"all phases green; tests {passed} pass / {warned} warn"
    return PhaseResult(status="SUCCESS", dbt_invocation_id=last_invocation, notes=notes)

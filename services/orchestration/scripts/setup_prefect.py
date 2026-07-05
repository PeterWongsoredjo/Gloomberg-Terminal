"""Idempotently provisions the Prefect control plane: work pool, OR-03 limits, OR-05 deployment.

Runs against whatever PREFECT_API_URL / PREFECT_API_KEY point at — a local Prefect server or
Prefect Cloud — so the Cloud/VPS switch is a .env change, never a code edit. Safe to re-run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORK_POOL = "gloomberg-local"
# OR-03 concurrency tags: duckdb_writer enforces the ADR-001 single writer
TAG_LIMITS = {"duckdb_writer": 1, "minio_fetch": 3}
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Runs a prefect CLI command, echoing it, and returns the completed process."""
    print("+", " ".join(args))
    # force UTF-8 so the CLI's emoji output never crashes a cp1252 Windows console
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        args, cwd=SERVICE_DIR, env=env, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if check and proc.returncode != 0 and "already" not in (proc.stdout + proc.stderr).lower():
        print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"command failed: {' '.join(args)}")
    return proc


def ensure_work_pool() -> None:
    """Creates the local process work pool if it does not exist."""
    _run(["prefect", "work-pool", "create", WORK_POOL, "--type", "process"])


def ensure_limits() -> None:
    """Sets each tag concurrency limit exactly, by delete-then-create (idempotent)."""
    for tag, limit in TAG_LIMITS.items():
        _run(["prefect", "concurrency-limit", "delete", tag], check=False)
        _run(["prefect", "concurrency-limit", "create", tag, str(limit)])


def deploy() -> None:
    """Registers the gloomberg-daily deployment from prefect.yaml."""
    os.environ.setdefault("GLOOMBERG_ORCH_DIR", str(SERVICE_DIR))
    _run(["prefect", "--no-prompt", "deploy", "--all"])


def main() -> None:
    """Provisions everything, printing the API target so local vs Cloud is obvious."""
    api = os.environ.get("PREFECT_API_URL", "(default local ephemeral / server)")
    print(f"provisioning Prefect control plane against: {api}")
    ensure_work_pool()
    ensure_limits()
    deploy()
    print("done — start a worker with:  prefect worker start --pool", WORK_POOL)


if __name__ == "__main__":
    main()

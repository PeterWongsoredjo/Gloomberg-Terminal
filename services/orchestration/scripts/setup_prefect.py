from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORK_POOL = "gloomberg-local"
TAG_LIMITS = {"duckdb_writer": 1, "minio_fetch": 3}
SERVICE_DIR = Path(__file__).resolve().parents[1]

# our own prints echo the CLI's emoji, keep this console UTF-8 too
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
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
    _run(["prefect", "work-pool", "create", WORK_POOL, "--type", "process"])


def ensure_limits() -> None:
    for tag, limit in TAG_LIMITS.items():
        _run(["prefect", "concurrency-limit", "delete", tag], check=False)
        _run(["prefect", "concurrency-limit", "create", tag, str(limit)])


def deploy() -> None:
    os.environ.setdefault("GLOOMBERG_ORCH_DIR", str(SERVICE_DIR))
    _run(["prefect", "--no-prompt", "deploy", "--all"])


def main() -> None:
    api = os.environ.get("PREFECT_API_URL", "(default local ephemeral / server)")
    print(f"provisioning Prefect control plane against: {api}")
    ensure_work_pool()
    ensure_limits()
    deploy()
    print("done — start a worker with:  prefect worker start --pool", WORK_POOL)


if __name__ == "__main__":
    main()

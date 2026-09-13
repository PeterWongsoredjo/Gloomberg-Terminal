"""BigQuery is an analyst copy, so it may lag but must never break the day."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

import orchestration.flow as flow_mod
import orchestration.tasks.bigquery as bigquery_task
import orchestration.tasks.dbt_build as dbt_mod
from orchestration.config import OrchestrationConfig
from orchestration.results import PhaseResult

TD = date(2026, 9, 11)


def _config(tmp_path: Path) -> OrchestrationConfig:
    return OrchestrationConfig(
        dbt_dir=tmp_path,
        calendar_seed=tmp_path / "cal.csv",
        coverage_floor=0.95,
        coverage_hard_min=0.80,
        ingest_mode="live",
        backend_api_url="http://test",
        backend_api_token="",
        poll_interval_seconds=0.0,
        poll_timeout_seconds=1.0,
        trigger_timeout_seconds=1.0,
    )


def _record_phases(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Runs each phase for real but remembers which ones were asked for."""

    def _run(dsn: str, flow_run_id: str, td: date, phase: str, fn: Any, on_error: Any = None) -> PhaseResult:
        seen.append(phase)
        try:
            return fn()  # type: ignore[no-any-return]
        except Exception as exc:
            handled = on_error(exc) if on_error else None
            if handled is None:
                raise
            return handled  # type: ignore[no-any-return]

    monkeypatch.setattr(flow_mod, "run_phase", _run)


def test_no_project_configured_skips_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bigquery_task, "target_from_env", lambda: None)
    result = bigquery_task.publish_bigquery.fn()
    assert result.status == "SKIPPED"
    assert "BQ_PROJECT" in (result.notes or "")


def test_a_mirror_reports_rows_and_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bigquery_task, "target_from_env", lambda: object())
    monkeypatch.setattr(bigquery_task, "get_settings", lambda: type("S", (), {"published_gold": Path("g")})())
    monkeypatch.setattr(
        bigquery_task,
        "publish_mirror",
        lambda target, snapshot: [{"row_count": 10}, {"row_count": 5}],
    )
    result = bigquery_task.publish_bigquery.fn()
    assert result.status == "SUCCESS"
    assert result.notes == "15 rows mirrored across 2 Gold tables"


def test_a_skipped_mirror_never_runs_the_bigquery_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _record_phases(monkeypatch, seen)
    monkeypatch.setattr(flow_mod, "publish_bigquery", lambda: PhaseResult(status="SKIPPED", notes="off"))

    result = flow_mod._bigquery_result("dsn", "run", TD, _config(tmp_path))

    assert result.status == "SKIPPED"
    assert seen == ["publish_bigquery"]


def test_a_fresh_mirror_rebuilds_the_marts_in_their_own_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    calls: list[dict[str, Any]] = []
    _record_phases(monkeypatch, seen)
    monkeypatch.setattr(flow_mod, "publish_bigquery", lambda: PhaseResult(status="SUCCESS", notes="ok"))

    def _build(config: Any, phases: Any = None, project: Any = None, target: str = "analytical") -> PhaseResult:
        calls.append({"phases": phases, "project": project, "target": target})
        return PhaseResult(status="SUCCESS", notes="built")

    monkeypatch.setattr(flow_mod, "dbt_build", _build)

    result = flow_mod._bigquery_result("dsn", "run", TD, _config(tmp_path))

    assert result.status == "SUCCESS"
    assert seen == ["publish_bigquery", "dbt_build_bigquery"]
    assert calls == [{"phases": dbt_mod.BIGQUERY_PHASES, "project": "bigquery", "target": "warehouse"}]


def test_a_broken_mirror_degrades_and_skips_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _record_phases(monkeypatch, seen)

    def _boom() -> PhaseResult:
        raise RuntimeError("403 quota exceeded")

    monkeypatch.setattr(flow_mod, "publish_bigquery", _boom)

    result = flow_mod._bigquery_result("dsn", "run", TD, _config(tmp_path))

    assert result.status == "DEGRADED"
    assert "403 quota exceeded" in (result.notes or "")
    assert seen == ["publish_bigquery"]


def _fake_dbt(monkeypatch: pytest.MonkeyPatch, runs: list[tuple[Path, list[str], str]]) -> None:
    monkeypatch.setattr(dbt_mod, "load_root_env", lambda: None)
    monkeypatch.setattr(dbt_mod, "_classify", lambda name, run: None)

    def _run(dbt_dir: Path, args: list[str], target: str = "analytical") -> Any:
        runs.append((dbt_dir, args, target))
        return dbt_mod._PhaseRun(0, "", None)

    monkeypatch.setattr(dbt_mod, "_run_dbt", _run)


def test_the_bigquery_build_runs_in_its_own_directory_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[tuple[Path, list[str], str]] = []
    _fake_dbt(monkeypatch, runs)

    dbt_mod.dbt_build.fn(_config(tmp_path), dbt_mod.BIGQUERY_PHASES, project="bigquery", target="warehouse")

    assert runs == [(tmp_path / "bigquery", ["run"], "warehouse"), (tmp_path / "bigquery", ["test"], "warehouse")]


def test_the_duckdb_build_is_unchanged_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[tuple[Path, list[str], str]] = []
    _fake_dbt(monkeypatch, runs)

    dbt_mod.dbt_build.fn(_config(tmp_path))

    assert {(d, t) for d, _, t in runs} == {(tmp_path, "analytical")}
    assert [a for _, a, _ in runs] == [args for _, args in dbt_mod.PHASES]

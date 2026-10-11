"""
Folds newly promotable Bronze into Gold, shared by the flows that recover past days.
"""

from __future__ import annotations

from datetime import date

from orchestration.config import OrchestrationConfig
from orchestration.phases import rollup, run_phase
from orchestration.results import PhaseResult
from orchestration.tasks.dbt_build import dbt_build
from orchestration.tasks.promote import promote_gold
from orchestration.tasks.quarantine import count_quarantine, degrade_on_count_failure


def _degrade_on_rebuild_failure(exc: Exception) -> PhaseResult | None:
    """The bytes are safely in Bronze, so tomorrow's build picks them up regardless."""
    return PhaseResult(status="DEGRADED", notes=f"rebuild failed: {exc}")


def rebuild_gold(
    dsn: str, flow_run_id: str, td: date, config: OrchestrationConfig, prefix: str
) -> PhaseResult:
    """Builds and promotes Gold the same way the daily run would, phases named by caller."""
    build = run_phase(
        dsn, flow_run_id, td, f"{prefix}_dbt_build",
        lambda: dbt_build(config), on_error=_degrade_on_rebuild_failure,
    )
    promote = run_phase(
        dsn, flow_run_id, td, f"{prefix}_promote",
        lambda: promote_gold(), on_error=_degrade_on_rebuild_failure,
    )
    if promote.status == "SUCCESS":
        run_phase(
            dsn, flow_run_id, td, f"{prefix}_count_quarantine",
            lambda: count_quarantine(td), on_error=degrade_on_count_failure,
        )
    return PhaseResult(status=rollup(build.status, promote.status), notes="recovered prices rebuilt")

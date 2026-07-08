"""Hard budget predicates, checked every superstep so no run can loop or spend past its cap."""

from __future__ import annotations

from app.agentic.state import Budget


def iterations_left(budget: Budget) -> bool:
    """True while the run may still take another optimize loop."""
    return budget["consumed_iterations"] < budget["max_loop_iterations"]


def tokens_left(budget: Budget) -> bool:
    """True while the run is still under its total token cap."""
    return budget["consumed_tokens"] < budget["max_total_tokens"]

"""
loads service level objectives configurations from the config yaml file
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_SLO_SEED = Path(__file__).resolve().parent.parent / "config" / "slo.yaml"


@dataclass(frozen=True)
class SloRule:
    slo_id: str
    plane: str
    measure: str
    target: dict[str, Any]
    severity: str
    calendar_aware: bool = False
    applies_when: dict[str, Any] = field(default_factory=dict)


def load_rules(path: Path = _SLO_SEED) -> list[SloRule]:
    doc: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        SloRule(
            slo_id=r["slo_id"],
            plane=r["plane"],
            measure=r["measure"],
            target=r["target"],
            severity=r["severity"],
            calendar_aware=bool(r.get("calendar_aware", False)),
            applies_when=r.get("applies_when", {}),
        )
        for r in doc
    ]


@lru_cache(maxsize=1)
def get_rules() -> list[SloRule]:
    return load_rules()


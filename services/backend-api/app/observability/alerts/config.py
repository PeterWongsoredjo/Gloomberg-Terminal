"""
loads alert configurations from the config yaml file
supports routing paths and cooldown rules
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ALERTS_SEED = Path(__file__).resolve().parent.parent / "config" / "alerts.yaml"


@dataclass(frozen=True)
class AlertRule:
    alert_id: str
    source: str
    severity: str
    dedup_key: str
    cooldown_minutes: int
    route: tuple[str, ...]
    payload_includes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def trigger(self) -> str:
        return self.source.split(":", 1)[1]

    @property
    def is_slo(self) -> bool:
        return self.source.startswith("slo:")


def load_rules(path: Path = _ALERTS_SEED) -> list[AlertRule]:
    doc: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        AlertRule(
            alert_id=r["alert_id"],
            source=r["source"],
            severity=r["severity"],
            dedup_key=r["dedup_key"],
            cooldown_minutes=int(r["cooldown_minutes"]),
            route=tuple(r.get("route", [])),
            payload_includes=tuple(r.get("payload_includes", [])),
        )
        for r in doc
    ]


@lru_cache(maxsize=1)
def get_rules() -> list[AlertRule]:
    return load_rules()


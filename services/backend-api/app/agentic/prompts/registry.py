"""
registrey sets up the persona and hard rules fro the LLM using the templates
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class PromptTemplate:
    """One registered prompt: its identity, version, hash, and decoding params."""

    prompt_id: str
    version: str
    objective: str
    content_sha256: str
    system_contract: str
    temperature: float
    seed: int
    max_output_tokens: int


def _load_file(path: Path) -> PromptTemplate:
    """Parses one template YAML and hashes its content for reproducibility."""
    raw = path.read_text(encoding="utf-8")
    doc: dict[str, Any] = yaml.safe_load(raw)
    decoding = doc.get("decoding", {})
    content_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return PromptTemplate(
        prompt_id=doc["prompt_id"],
        version=doc["version"],
        objective=doc["objective"],
        content_sha256=content_sha256,
        system_contract=doc["system_contract"].strip(),
        temperature=float(decoding.get("temperature", 0.0)),
        seed=int(decoding.get("seed", 42)),
        max_output_tokens=int(decoding.get("max_output_tokens", 512)),
    )


@lru_cache(maxsize=1)
def _by_objective() -> dict[str, PromptTemplate]:
    """Loads every template once, keyed by the objective it serves."""
    templates = [_load_file(path) for path in sorted(_TEMPLATES_DIR.glob("*.yaml"))]
    return {t.objective: t for t in templates}


def get_prompt(objective: str) -> PromptTemplate:
    """Returns the registered default template for an objective."""
    registry = _by_objective()
    if objective not in registry:
        raise KeyError(f"no registered prompt for objective {objective!r}")
    return registry[objective]


def registered_templates() -> list[PromptTemplate]:
    """Every registered prompt template, for hash reconciliation against Langfuse (OB-03)."""
    return list(_by_objective().values())

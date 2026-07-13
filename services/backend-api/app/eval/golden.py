"""Loads the frozen, content-hashed labeled set and throws if someone tampered with it.

Evals must be reproducible, so we pin the SHA256. Editing in place is blocked —
you have to mint a new dataset_version if you want changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


class GoldenSetTampered(RuntimeError):
    """thrown when the file's hash doesn't match its pinned .sha256 companion"""


@dataclass(frozen=True)
class GoldenItem:
    """a single labeled news article with ground truth sentiment and optional traps"""

    item_id: str
    ticker: str
    headline: str
    body: str
    label: str
    score: float
    trap: str | None


@dataclass(frozen=True)
class GoldenSet:
    """the frozen evaluation set loaded and verified for a specific dataset version"""

    dataset_version: str
    content_sha256: str
    items: tuple[GoldenItem, ...]

    @property
    def universe(self) -> frozenset[str]:
        """all valid tickers in this dataset, any other ticker predicted is an LLM hallucination"""
        return frozenset(item.ticker for item in self.items)

    @property
    def trap_ids(self) -> frozenset[str]:
        """news items containing prompt injections or corporate-action traps"""
        return frozenset(item.item_id for item in self.items if item.trap)


def _pinned_hash(sha_path: Path) -> str:
    """grabs the expected sha256 string from the sidecar .sha256 file"""
    return sha_path.read_text(encoding="utf-8").split()[0].strip()


def load_golden_set(dataset_version: str = "golden-2026Q2") -> GoldenSet:
    """reads, hash-verifies, and parses the golden dataset file"""
    jsonl = _GOLDEN_DIR / f"{dataset_version}.jsonl"
    raw = jsonl.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    pinned = _pinned_hash(jsonl.with_suffix(".jsonl.sha256"))
    if actual != pinned:
        raise GoldenSetTampered(f"{dataset_version} hash {actual} != pinned {pinned}")
    items = tuple(
        GoldenItem(
            item_id=row["item_id"],
            ticker=row["ticker"],
            headline=row["headline"],
            body=row["body"],
            label=row["label"],
            score=float(row["score"]),
            trap=row.get("trap"),
        )
        for row in (json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip())
    )
    return GoldenSet(dataset_version=dataset_version, content_sha256=actual, items=items)


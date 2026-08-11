"""Unit tests for landing agentic-ledger artifacts into Bronze."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from pipeline.agentic import artifact_land
from pipeline.agentic.artifact_land import land_artifacts


def _artifact(artifact_type: str) -> dict[str, Any]:
    return {"artifact_type": artifact_type, "ticker": "AADI"}


def test_land_artifacts_skips_types_with_zero_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A type absent from the ledger for the date is never landed as an empty payload."""
    monkeypatch.setattr(
        artifact_land, "_read_ledger", lambda settings, td: [_artifact("SENTIMENT")]
    )
    landed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        artifact_land,
        "land_payloads",
        lambda minio, **kwargs: landed.append(kwargs) or {"dataset": kwargs["dataset"]},
    )

    manifests = land_artifacts(cast(Any, None), date(2026, 7, 3), cast(Any, object()))

    assert [m["dataset"] for m in manifests] == ["sentiment"]
    assert [call["dataset"] for call in landed] == ["sentiment"]
    assert landed[0]["record_count"] == 1


def test_land_artifacts_lands_nothing_when_ledger_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero ledger rows for the date means zero Bronze objects, not four empty ones."""
    monkeypatch.setattr(artifact_land, "_read_ledger", lambda settings, td: [])
    monkeypatch.setattr(
        artifact_land, "land_payloads", lambda minio, **kwargs: pytest.fail("should not land")
    )

    manifests = land_artifacts(cast(Any, None), date(2026, 7, 3), cast(Any, object()))

    assert manifests == []

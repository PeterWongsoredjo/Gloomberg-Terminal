"""OB-07 golden set: it is frozen, hash-verified, and carries the known traps."""

from __future__ import annotations

import pytest

from app.eval.golden import GoldenSetTampered, load_golden_set


def test_golden_set_loads_and_verifies_hash() -> None:
    golden = load_golden_set()
    assert golden.dataset_version == "golden-2026Q2"
    assert len(golden.items) == 12


def test_golden_set_carries_corporate_action_and_injection_traps() -> None:
    golden = load_golden_set()
    traps = {item.trap for item in golden.items if item.trap}
    assert "corporate_action" in traps
    assert "injection" in traps


def test_tampered_golden_set_is_rejected(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.eval import golden as golden_mod

    edited = tmp_path / "golden-2026Q2.jsonl"
    edited.write_text('{"item_id":"x","ticker":"BBCA","headline":"h","body":"b","label":"BULLISH","score":0.9,"trap":null}\n', encoding="utf-8")
    (tmp_path / "golden-2026Q2.jsonl.sha256").write_text("deadbeef  golden-2026Q2.jsonl\n", encoding="utf-8")
    monkeypatch.setattr(golden_mod, "_GOLDEN_DIR", tmp_path)
    with pytest.raises(GoldenSetTampered):
        load_golden_set()

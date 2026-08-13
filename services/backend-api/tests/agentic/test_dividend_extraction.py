"""Unit tests for reading cash dividends out of a filed document."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.agentic.config import AgenticSettings
from app.agentic.nodes import evaluate as evaluate_node
from app.agentic.nodes._common import value_confidence
from app.agentic.nodes.analysis import _filing_tasks
from app.agentic.objectives import spec_for_type
from app.agentic.schemas import CashDividendEvent, CashDividendValue, ExtractionValue

TD = "2026-07-31"


def _event(**extra: Any) -> dict[str, Any]:
    row = {
        "ticker": "SMDR",
        "dividend_kind": "INTERIM",
        "currency": "IDR",
        "amount_text": "Rp2,5",
        "source_span": "dividen tunai sebesar Rp2,5 per saham",
        "confidence": 0.9,
    }
    row.update(extra)
    return row


def _value(**extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "filing_id": "SMDR:abc",
        "outcome": "EXTRACTED",
        "events": [_event()],
        "filing_confidence": 0.9,
    }
    row.update(extra)
    return row


def test_the_real_filing_amount_becomes_cents() -> None:
    """The genuine SMDR filing declares 2.5 rupiah, which only survives as cents."""
    event = CashDividendEvent.model_validate(_event())
    assert event.amount_per_share_sen == 250


def test_the_model_can_never_supply_the_number() -> None:
    """Our parser owns the amount, so a model guess is overwritten not trusted."""
    event = CashDividendEvent.model_validate(_event(amount_per_share_sen=999999))
    assert event.amount_per_share_sen == 250


def test_an_unreadable_amount_invalidates_the_whole_value() -> None:
    """A number we cannot parse must fail visibly, never land as a wrong dividend."""
    with pytest.raises(ValidationError):
        CashDividendValue.model_validate(_value(events=[_event(amount_text="roughly two rupiah")]))


def test_a_foreign_dividend_keeps_its_text_and_no_rupiah() -> None:
    """Some issuers pay in dollars, and a null is honest where a conversion would not be."""
    event = CashDividendEvent.model_validate(_event(currency="USD", amount_text="USD 0.0035"))
    assert event.amount_per_share_sen is None
    assert event.amount_text == "USD 0.0035"


def test_a_filing_with_no_dates_is_still_valid() -> None:
    """The real filing states an amount and no dates at all, which is a real answer."""
    event = CashDividendEvent.model_validate(_event())
    assert event.ex_date is None and event.payment_date is None


def test_an_empty_extraction_cannot_claim_success() -> None:
    """The vacuous accept bug must not be expressible in the schema."""
    with pytest.raises(ValidationError):
        CashDividendValue.model_validate(_value(events=[]))


def test_a_stated_absence_needs_a_reason() -> None:
    """Saying a filing declares nothing is a claim, so it has to be justified."""
    with pytest.raises(ValidationError):
        CashDividendValue.model_validate(
            {"filing_id": "x", "outcome": "NO_DIVIDEND_STATED", "events": [], "filing_confidence": 0.5}
        )


def test_a_stated_absence_cannot_carry_events() -> None:
    """Declaring nothing while listing a dividend is self contradictory."""
    with pytest.raises(ValidationError):
        CashDividendValue.model_validate(
            _value(outcome="NO_DIVIDEND_STATED", reason="no amount given")
        )


def test_the_generic_extraction_value_closed_the_same_hole() -> None:
    """Its sibling objective had the same vacuous accept and now cannot express it."""
    with pytest.raises(ValidationError):
        ExtractionValue.model_validate({"outcome": "EXTRACTED", "events": []})


def test_the_artifact_type_resolves_to_its_own_model() -> None:
    """Sharing a type with deep_extraction would validate dividends against the wrong model."""
    spec = spec_for_type("CASH_DIVIDEND")
    assert spec.value_model is CashDividendValue
    assert spec.objective == "dividend_extraction"


def test_confidence_reads_the_filing_not_the_events() -> None:
    """A stated absence has no events, and must not fall back to a default score."""
    assert value_confidence("dividend_extraction", _value(filing_confidence=0.75)) == 0.75


def _state(documents: list[dict[str, Any]]) -> Any:
    return {
        "objective": "dividend_extraction",
        "subject_universe": [],
        "trade_date": TD,
        "context": {"news_items": [], "market_context": [], "corporate_actions": [], "documents": documents},
        "working": {"evaluation": None},
    }


def _filing(filing_id: str, body: str = "dividen tunai Rp2,5 per saham") -> dict[str, Any]:
    return {
        "filing_id": filing_id,
        "ticker": "SMDR",
        "title": "Distribution of interim dividend",
        "filing_number": "SR.26.07.039/CS/SI",
        "source_url": "https://www.idx.co.id/x.pdf",
        "announced_at": "2026-07-30T17:38:03",
        "body": body,
    }


def test_one_task_per_filing_carrying_its_own_issuer() -> None:
    """Batching filings would starve the fallback provider of tokens on a retry."""
    tasks = _filing_tasks(_state([_filing("a"), _filing("b")]), AgenticSettings(), [])
    assert len(tasks) == 2
    assert [t["subject"]["ticker"] for t in tasks] == ["SMDR", "SMDR"]
    assert [t["pool"] for t in tasks] == [["a"], ["b"]]


def test_a_long_filing_is_capped_and_says_so() -> None:
    """Truncation is a fact the model needs, never a silent shortening."""
    settings = AgenticSettings(dividend_filing_char_cap=20)
    tasks = _filing_tasks(_state([_filing("a", body="x" * 500)]), settings, [])
    assert '"text_truncated": true' in tasks[0]["user"]


def test_the_run_reads_at_most_its_budgeted_filings() -> None:
    """A backlog must not blow one run's token budget."""
    settings = AgenticSettings(dividend_filings_per_run=2)
    tasks = _filing_tasks(_state([_filing(str(i)) for i in range(5)]), settings, [])
    assert len(tasks) == 2


def _draft(**extra: Any) -> dict[str, Any]:
    draft = {
        "value": _value(),
        "evidence_pool": ["SMDR:abc"],
        "subject": {"ticker": "SMDR", "security_id": None},
    }
    draft.update(extra)
    return draft


def test_a_dividend_must_belong_to_the_issuer_that_filed_it() -> None:
    """A filing by one issuer cannot declare another issuer's dividend."""
    draft = _draft(value=_value(events=[_event(ticker="BBCA")]))
    assert not evaluate_node._dividend_grounded(draft)


def test_a_dividend_must_come_from_a_claimed_filing() -> None:
    """An echoed filing_id we never supplied means the model invented its source."""
    assert not evaluate_node._dividend_grounded(_draft(evidence_pool=["OTHER:xyz"]))


def test_a_well_grounded_dividend_passes() -> None:
    """The honest case still has to work."""
    assert evaluate_node._dividend_grounded(_draft())


def test_one_bad_filing_does_not_discard_the_good_ones() -> None:
    """Re-running every filing to fix one would pay for the good ones twice."""
    graded = [{"passed": True}, {"passed": False}]
    assert evaluate_node._verdict("dividend_extraction", graded, can_retry=True) == "ACCEPT"


def test_every_filing_failing_still_retries() -> None:
    """Nothing landed, so a retry is worth the tokens."""
    graded = [{"passed": False}]
    assert evaluate_node._verdict("dividend_extraction", graded, can_retry=True) == "OPTIMIZE"

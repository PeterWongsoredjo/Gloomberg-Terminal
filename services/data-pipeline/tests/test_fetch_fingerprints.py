"""Unit tests for the browser fingerprint ladder fetch() walks down on a Cloudflare 403."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args

import pytest
from curl_cffi.requests import BrowserTypeLiteral

from pipeline.bronze.ingest import FINGERPRINTS, FetchError, fetch, is_transient_fetch

URL = "https://www.idx.co.id/primary/TradingSummary/GetIndexSummary"
PROXY = "http://user:pass@p.webshare.io:80"


@dataclass
class FakeResponse:
    status_code: int
    content: bytes = b""


class FakeUpstream:
    """Answers each fingerprint with a scripted status and records every call."""

    def __init__(self, statuses: dict[str, int | Exception]) -> None:
        self.statuses = statuses
        self.calls: list[tuple[str, str | None]] = []

    def get(self, url: str, *, impersonate: str, timeout: int, proxy: str | None) -> FakeResponse:
        self.calls.append((impersonate, proxy))
        outcome = self.statuses.get(impersonate, 403)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome, b'{"ok": true}' if outcome == 200 else b"")

    @property
    def tried(self) -> list[str]:
        return [fingerprint for fingerprint, _ in self.calls]


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(statuses: dict[str, int | Exception]) -> FakeUpstream:
        fake = FakeUpstream(statuses)
        monkeypatch.setattr("curl_cffi.requests.get", fake.get)
        return fake

    return install


def test_every_fingerprint_is_one_curl_cffi_knows() -> None:
    """A typo or a target dropped by a curl_cffi upgrade fails here, not live."""
    assert set(FINGERPRINTS) <= set(get_args(BrowserTypeLiteral))


def test_the_ladder_has_no_repeats() -> None:
    """Retrying the same disguise twice in one fetch would waste a proxied request."""
    assert len(set(FINGERPRINTS)) == len(FINGERPRINTS)


def test_first_fingerprint_that_gets_through_wins(upstream: Any) -> None:
    """A clean answer on the first rung costs exactly one request."""
    fake = upstream({FINGERPRINTS[0]: 200})
    assert fetch(URL, proxy=PROXY) == b'{"ok": true}'
    assert fake.tried == [FINGERPRINTS[0]]


def test_a_403_steps_down_to_the_next_fingerprint(upstream: Any) -> None:
    """Challenged disguises are skipped in order until one is let through."""
    fake = upstream({FINGERPRINTS[2]: 200})
    assert fetch(URL, proxy=PROXY) == b'{"ok": true}'
    assert fake.tried == list(FINGERPRINTS[:3])


def test_every_rung_uses_the_same_proxy(upstream: Any) -> None:
    """The ladder changes the disguise, never the route."""
    fake = upstream({FINGERPRINTS[-1]: 200})
    fetch(URL, proxy=PROXY)
    assert {proxy for _, proxy in fake.calls} == {PROXY}


def test_all_fingerprints_blocked_raises_a_retryable_proxied_403(upstream: Any) -> None:
    """A fully blocked ladder still hands the task retry policy a transient error."""
    fake = upstream({})
    with pytest.raises(FetchError) as caught:
        fetch(URL, proxy=PROXY)
    assert fake.tried == list(FINGERPRINTS)
    assert caught.value.status_code == 403
    assert caught.value.via_proxy
    assert is_transient_fetch(caught.value)
    assert FINGERPRINTS[-1] in str(caught.value)


def test_all_fingerprints_blocked_direct_stays_permanent(upstream: Any) -> None:
    """Without a proxy a full ladder of 403s is a real block, not worth retrying."""
    upstream({})
    with pytest.raises(FetchError) as caught:
        fetch(URL)
    assert not caught.value.via_proxy
    assert not is_transient_fetch(caught.value)


@pytest.mark.parametrize("status", [404, 429, 503])
def test_a_non_403_failure_skips_the_ladder(upstream: Any, status: int) -> None:
    """Only a challenge means the disguise was the problem, others go to the retry policy."""
    fake = upstream({FINGERPRINTS[0]: status})
    with pytest.raises(FetchError) as caught:
        fetch(URL, proxy=PROXY)
    assert caught.value.status_code == status
    assert fake.tried == [FINGERPRINTS[0]]


def test_a_timeout_skips_the_ladder(upstream: Any) -> None:
    """A dead connection is not a fingerprint problem, so it is raised as transient."""
    fake = upstream({FINGERPRINTS[0]: TimeoutError("read timed out")})
    with pytest.raises(FetchError) as caught:
        fetch(URL, proxy=PROXY)
    assert caught.value.status_code is None
    assert is_transient_fetch(caught.value)
    assert fake.tried == [FINGERPRINTS[0]]


def test_a_timeout_after_a_403_still_stops_the_ladder(upstream: Any) -> None:
    """Once a rung fails for a non-challenge reason the fetch hands off to retries."""
    fake = upstream({FINGERPRINTS[1]: TimeoutError("read timed out")})
    with pytest.raises(FetchError) as caught:
        fetch(URL, proxy=PROXY)
    assert caught.value.status_code is None
    assert fake.tried == list(FINGERPRINTS[:2])

from __future__ import annotations

from datetime import date, timedelta

from curl_cffi import requests
from curl_cffi.requests import BrowserTypeLiteral

from pipeline.bronze.core.feeds import FeedSpec

# browser disguises to try, cloudflare blocks them one by one
FINGERPRINTS: tuple[BrowserTypeLiteral, ...] = ("safari2601", "safari184", "tor145", "chrome")


class FetchError(RuntimeError):
    def __init__(self, status_code: int | None, message: str, *, via_proxy: bool = False) -> None:
        self.status_code = status_code
        self.via_proxy = via_proxy
        super().__init__(message)


def is_transient_fetch(exc: FetchError) -> bool:
    """worth retry on timeout: 429, any 5xx, or a proxied 403."""
    if exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500:
        return True
    return exc.status_code == 403 and exc.via_proxy


def _fetch_as(url: str, fingerprint: BrowserTypeLiteral, proxy: str | None) -> bytes:
    """One fetch disguised as one browser, raising typed."""
    try:
        response = requests.get(url, impersonate=fingerprint, timeout=30, proxy=proxy)
    except Exception as exc:
        raise FetchError(None, f"{url} -> {exc}", via_proxy=proxy is not None) from exc
    if response.status_code != 200:
        raise FetchError(
            response.status_code,
            f"{url} -> HTTP {response.status_code} as {fingerprint}",
            via_proxy=proxy is not None,
        )
    content: bytes = response.content
    return content


def fetch(url: str, *, proxy: str | None = None) -> bytes:
    """Fetches a Cloudflare-protected endpoint, trying the next fingerprint on a 403."""
    for fingerprint in FINGERPRINTS[:-1]:
        try:
            return _fetch_as(url, fingerprint, proxy)
        except FetchError as exc:
            if exc.status_code != 403:
                raise
    return _fetch_as(url, FINGERPRINTS[-1], proxy)


def url_for(spec: FeedSpec, trade_date: date) -> str:
    if not spec.date_scoped:
        return spec.url
    window_start = trade_date - timedelta(days=spec.lookback_days)
    return spec.url.format(
        ymd=trade_date.strftime("%Y%m%d"), ymd_from=window_start.strftime("%Y%m%d")
    )

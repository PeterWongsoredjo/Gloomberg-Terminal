from __future__ import annotations

import hashlib
from datetime import datetime


def make_etag(data_as_of: datetime, query: str) -> str:
    """A weak-free content tag over the data's as-of instant and the request's query."""
    digest = hashlib.sha256(f"{data_as_of.isoformat()}|{query}".encode()).hexdigest()
    return f'"{digest[:32]}"'


def not_modified(if_none_match: str | None, etag: str) -> bool:
    """True when the client's cached tag still matches, so the endpoint can answer 304."""
    return if_none_match is not None and if_none_match.strip() == etag

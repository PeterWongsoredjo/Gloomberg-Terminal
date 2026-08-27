"""An in-memory Bronze bucket, so tests can drive the real landing path end to end."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

import pytest
from minio.error import S3Error

from pipeline.bronze import ingest
from pipeline.bronze.feeds import FeedSpec


class _Response:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class _Object:
    def __init__(self, name: str) -> None:
        self.object_name = name


class FakeMinio:
    """Stores exactly what the real client stores: whole objects, keyed by name."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(
        self, bucket: str, key: str, data: Any, length: int = 0, content_type: str = ""
    ) -> None:
        self.objects[key] = data.read()

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = False) -> list[_Object]:
        return [_Object(k) for k in sorted(self.objects) if k.startswith(prefix)]

    def get_object(self, bucket: str, key: str) -> _Response:
        if key not in self.objects:
            raise S3Error(None, "NoSuchKey", "not found", key, "", "")  # type: ignore[arg-type]
        return _Response(self.objects[key])

    def remove_object(self, bucket: str, key: str) -> None:
        self.objects.pop(key, None)


def at_wib_noon(day: date) -> datetime:
    """An instant that lands on the given day in Jakarta, whatever the runner's timezone."""
    return datetime.combine(day, time(5, 0), tzinfo=timezone.utc)


def land_live(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeMinio,
    spec: FeedSpec,
    trade_date: date,
    payload: bytes,
    *,
    captured_on: date,
) -> dict[str, Any]:
    """Runs the real fetch_and_land against the fake bucket, with the clock pinned."""
    monkeypatch.setattr(ingest, "fetch", lambda url, proxy=None: payload)
    monkeypatch.setattr(ingest, "now_utc", lambda: at_wib_noon(captured_on))
    return ingest.fetch_and_land(fake, spec, trade_date)  # type: ignore[arg-type]

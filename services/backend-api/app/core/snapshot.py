"""
Keeps the read-only Gold handle pointing at the file publish just swapped in.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger("gloomberg.core.snapshot")

_Identity = tuple[int, int, int]


class GoldSnapshot:
    """Opens the published Gold read-only and reopens it whenever the file changes."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._identity: _Identity | None = None
        self._open()

    def _stat(self) -> _Identity | None:
        """Which file is at the path right now, or nothing when it is missing."""
        try:
            status = self._path.stat()
        except OSError:
            return None
        return (status.st_dev, status.st_ino, status.st_mtime_ns)

    def _open(self) -> None:
        """Connects to whatever is at the path, tolerating an absent snapshot."""
        identity = self._stat()
        if identity is None:
            logger.warning("gold snapshot missing, market context empty: %s", self._path)
            self._connection, self._identity = None, None
            return
        try:
            self._connection = duckdb.connect(str(self._path), read_only=True)
            self._identity = identity
        except (duckdb.Error, OSError) as exc:
            logger.warning("gold snapshot unreadable, market context empty: %s", exc)
            self._connection, self._identity = None, None

    def _refresh(self) -> None:
        """Swaps the handle when promote replaced the file underneath it."""
        if self._stat() == self._identity:
            return
        logger.info("gold snapshot changed, reopening: %s", self._path)
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._open()

    def query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        """Runs one query against the current snapshot, as dict rows. Call off the loop."""
        with self._lock:
            self._refresh()
            if self._connection is None:
                return []
            cursor = self._connection.cursor()
            try:
                cursor.execute(sql, params)
                if cursor.description is None:
                    return []
                columns = [c[0] for c in cursor.description]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            finally:
                cursor.close()

    def close(self) -> None:
        """Drops the handle at shutdown."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
            self._connection, self._identity = None, None

"""
operator log sink for writing alerts and heartbeats straight to log output
bypasses standard event buffers to ensure critical alerts are visible if pipeline dies
"""

from __future__ import annotations

import logging
from typing import Any

# a named logger so an operator can filter the app's own logs to just this stream
logger = logging.getLogger("gloomberg.operator")

_LEVEL = {"INFO": logging.INFO, "WARN": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}


def log_alert(alert: dict[str, Any]) -> None:
    """writes an alert dictionary to operator logs at its severity level"""
    level = _LEVEL.get(str(alert.get("severity", "INFO")), logging.INFO)
    logger.log(level, "alert %s [%s] %s", alert.get("alert_id"), alert.get("severity"), alert.get("payload"))


def log_heartbeat(source: str) -> None:
    logger.info("heartbeat %s alive", source)


def log_dead_heartbeat(source: str, silent_seconds: float) -> None:
    """logs critical alert if heartbeat is missing for a given period"""
    logger.critical("heartbeat %s missing for %.0fs — telemetry pipeline may be dead", source, silent_seconds)


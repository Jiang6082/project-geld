"""Logging for Geld commands and unattended runners.

Console output stays plain (just the message, as ``print`` produced), so the
existing runner logs read the same. Set ``GELD_LOG_JSON=<path>`` or pass
``--log-json <path>`` to also append structured JSON lines (timestamp, level,
logger, message and any ``extra=`` fields) for machine monitoring.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import IO

ROOT_LOGGER = "geld"
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per record, with ``extra`` fields promoted to keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Logger under the ``geld`` hierarchy (``project_geld.x`` -> ``geld.x``)."""
    short = name[len("project_geld."):] if name.startswith("project_geld.") else name
    return logging.getLogger(f"{ROOT_LOGGER}.{short}")


def configure_logging(
    level: str | int | None = None,
    json_path: str | Path | None = None,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Install console (and optional JSON-lines) handlers; safe to call twice."""
    root = logging.getLogger(ROOT_LOGGER)
    for handler in list(root.handlers):
        if getattr(handler, "_geld_handler", False):
            root.removeHandler(handler)
            handler.close()
    chosen = level or os.getenv("GELD_LOG_LEVEL", "INFO")
    root.setLevel(chosen.upper() if isinstance(chosen, str) else chosen)
    root.propagate = False

    console = logging.StreamHandler(stream or sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    console._geld_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    target = json_path or os.getenv("GELD_LOG_JSON")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        structured = logging.FileHandler(path, encoding="utf-8")
        structured.setFormatter(JsonLinesFormatter())
        structured._geld_handler = True  # type: ignore[attr-defined]
        root.addHandler(structured)
    return root

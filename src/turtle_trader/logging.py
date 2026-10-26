"""Structured JSON logging with conservative credential redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SECRET_KEY = re.compile(
    r"(password|passwd|secret|token|credential|api[_-]?key|authorization)", re.I
)
_SECRET_TEXT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\s*([=:])\s*([^\s,;]+)"
)


def _safe(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(name): _safe(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log event; common credential keys are redacted."""

    _standard = frozenset(logging.makeLogRecord({}).__dict__) | {
        "message", "asctime", "args", "exc_info", "exc_text", "stack_info",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _SECRET_TEXT.sub(r"\1\2[REDACTED]", record.getMessage()),
        }
        payload.update({
            key: _safe(value, key)
            for key, value in record.__dict__.items()
            if key not in self._standard and not key.startswith("_")
        })
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, allow_nan=False, default=str, separators=(",", ":"))

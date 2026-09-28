"""Logging estructurado y seguro para Valkiria."""
from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_SENSITIVE = re.compile(r"(?i)(authorization|api[_-]?key|token|password|secret|dsn|connection[_-]?string)")


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): ("[REDACTADO]" if _SENSITIVE.search(str(k)) else _safe(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensaje": record.getMessage(),
            **_safe(getattr(record, "context", {})),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def event(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    logger.log(level, message, extra={"context": _safe(context)})

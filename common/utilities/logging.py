"""Structured logging with request/task correlation and secret redaction."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from common.security.redaction import redact_text

from .context import request_id_var


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        task_id = None
        try:
            from celery import current_task

            if current_task and current_task.request and current_task.request.id:
                task_id = current_task.request.id
        except Exception:  # pragma: no cover - celery not initialised
            task_id = None
        record.request_id = request_id or "-"
        record.task_id = task_id or "-"
        return True


class RedactingFilter(logging.Filter):
    """Last line of defence: scrub credentials from rendered log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


class JsonFormatter(logging.Formatter):
    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "task_id": getattr(record, "task_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and key not in payload:
                try:
                    json.dumps(value)
                    payload[key] = value
                except TypeError:
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)

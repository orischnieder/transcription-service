import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has. Anything else on a record came from `extra={...}`.
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured copy of the message; noise in our format
}


_current_job_id: ContextVar[str | None] = ContextVar("current_job_id", default=None)


@contextmanager
def job_log_context(job_id: str) -> Iterator[None]:
    """Attach `job_id` to every log record written inside this block (see JobContextFilter)."""
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)


class JobContextFilter(logging.Filter):
    """Adds the current job_id to records that do not carry one (e.g. third-party loggers)."""

    def filter(self, record: logging.LogRecord) -> bool:
        job_id = _current_job_id.get()
        if job_id is not None and not hasattr(record, "job_id"):
            record.job_id = job_id
        return True


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRS}


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")


def _format_value(value: Any) -> str:
    text = str(value)
    if not text or any(char in text for char in ' ="'):
        return json.dumps(text)
    return text


class TextFormatter(logging.Formatter):
    """`timestamp level logger message key=value ...` — easy to read and to grep."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [_timestamp(record), record.levelname, record.name, record.getMessage()]
        parts += [f"{key}={_format_value(value)}" for key, value in _extras(record).items()]
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


QUIET_LOGGERS = ("httpx", "httpx2")


def configure_logging(level: str, log_format: str) -> None:
    """Send all logs (ours and uvicorn's) to stdout in one format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    handler.addFilter(JobContextFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # Our request logging middleware replaces uvicorn's access log.
    logging.getLogger("uvicorn.access").disabled = True

    # HTTP clients (the Hugging Face hub uses httpx2) log every request at INFO; keep only problems.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

import json
import logging

from app.logging_config import (
    JobContextFilter,
    JsonFormatter,
    TextFormatter,
    configure_logging,
    job_log_context,
)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_text_formatter_appends_extras_as_key_value() -> None:
    line = TextFormatter().format(_record("job done", job_id="abc", took="1.5 s"))

    assert ' INFO app.test job done job_id=abc took="1.5 s"' in line


def test_json_formatter_includes_extras() -> None:
    payload = json.loads(JsonFormatter().format(_record("job done", job_id="abc")))

    assert payload["message"] == "job done"
    assert payload["level"] == "INFO"
    assert payload["job_id"] == "abc"


def test_http_client_request_logs_are_quiet() -> None:
    configure_logging("INFO", "text")

    assert not logging.getLogger("httpx2").isEnabledFor(logging.INFO)
    assert logging.getLogger("httpx2").isEnabledFor(logging.WARNING)
    assert logging.getLogger("app.jobs").isEnabledFor(logging.INFO)


def test_job_context_adds_job_id_to_third_party_records() -> None:
    record = _record("Detected language 'en'")
    record.name = "faster_whisper"

    with job_log_context("abc123"):
        JobContextFilter().filter(record)

    assert record.job_id == "abc123"  # type: ignore[attr-defined]


def test_job_context_keeps_an_explicit_job_id_and_ends_with_the_block() -> None:
    explicit = _record("job done", job_id="explicit")
    outside = _record("worker ready")

    with job_log_context("abc123"):
        JobContextFilter().filter(explicit)
    JobContextFilter().filter(outside)

    assert explicit.job_id == "explicit"  # type: ignore[attr-defined]
    assert not hasattr(outside, "job_id")


def test_uvicorn_color_message_is_not_printed() -> None:
    record = _record("Started server process [1]", color_message="\x1b[36m%d\x1b[0m")

    assert "color_message" not in TextFormatter().format(record)
    assert "color_message" not in json.loads(JsonFormatter().format(record))

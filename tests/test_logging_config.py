import json
import logging

from app.logging_config import JsonFormatter, TextFormatter


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

import pytest

from app.formats.srt import format_timestamp, to_srt
from app.formats.txt import to_text
from app.jobs.models import SegmentResult

SEGMENTS = [
    SegmentResult(start=0.0, end=2.5, text="Hello there."),
    SegmentResult(start=2.5, end=3661.042, text="A very long pause."),
]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (2.5, "00:00:02,500"),
        (59.9999, "00:01:00,000"),  # rounds to the nearest millisecond
        (3661.042, "01:01:01,042"),
        (-0.1, "00:00:00,000"),
    ],
)
def test_format_timestamp(seconds: float, expected: str) -> None:
    assert format_timestamp(seconds) == expected


def test_to_srt() -> None:
    assert to_srt(SEGMENTS) == (
        "1\n00:00:00,000 --> 00:00:02,500\nHello there.\n"
        "\n"
        "2\n00:00:02,500 --> 01:01:01,042\nA very long pause.\n"
    )


def test_to_srt_empty() -> None:
    assert to_srt([]) == ""


def test_to_text_one_segment_per_line() -> None:
    assert to_text(SEGMENTS) == "Hello there.\nA very long pause.\n"

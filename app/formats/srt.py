from collections.abc import Sequence

from app.jobs.models import SegmentResult


def format_timestamp(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm."""
    total_ms = round(max(seconds, 0.0) * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(segments: Sequence[SegmentResult]) -> str:
    blocks = [
        f"{index}\n{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}\n"
        f"{segment.text}\n"
        for index, segment in enumerate(segments, start=1)
    ]
    return "\n".join(blocks)

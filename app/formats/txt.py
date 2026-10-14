from collections.abc import Sequence

from app.jobs.models import SegmentResult


def to_text(segments: Sequence[SegmentResult]) -> str:
    """Plain transcript, one segment per line (keeps Whisper's natural sentence breaks)."""
    return "".join(f"{segment.text}\n" for segment in segments)

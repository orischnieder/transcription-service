from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Called with the percentage (0-100) of the media transcribed so far.
ProgressCallback = Callable[[float], None]


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    duration_sec: float
    segments: list[Segment]


class TranscriptionError(Exception):
    """Raised by an engine when it cannot transcribe a file."""


class TranscriptionEngine(Protocol):
    def transcribe(
        self,
        path: Path,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult: ...


def progress_percent(position_sec: float, duration_sec: float) -> float:
    if duration_sec <= 0:
        return 100.0
    return min(100.0, position_sec / duration_sec * 100)

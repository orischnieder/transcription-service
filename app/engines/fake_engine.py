from pathlib import Path

from app.engines.base import (
    ProgressCallback,
    Segment,
    TranscriptionError,
    TranscriptionResult,
    progress_percent,
)

DEFAULT_SEGMENTS = [
    Segment(start=0.0, end=2.0, text="This is a fake transcription."),
    Segment(start=2.0, end=4.5, text="It is used for tests and local development."),
    Segment(start=4.5, end=6.0, text="No model was loaded."),
]


class FakeEngine:
    """Deterministic engine for tests and development. Never loads a model."""

    def __init__(self, segments: list[Segment] | None = None, fail: bool = False) -> None:
        self._segments = segments if segments is not None else DEFAULT_SEGMENTS
        self._fail = fail

    def transcribe(
        self,
        path: Path,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        if self._fail:
            raise TranscriptionError("Fake engine configured to fail.")

        duration = self._segments[-1].end if self._segments else 0.0
        if on_progress:
            for segment in self._segments:
                on_progress(progress_percent(segment.end, duration))
            on_progress(100.0)

        return TranscriptionResult(
            text=" ".join(segment.text for segment in self._segments),
            language=language or "en",
            duration_sec=duration,
            segments=list(self._segments),
        )

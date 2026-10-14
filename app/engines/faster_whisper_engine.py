import logging
import time
from pathlib import Path

from faster_whisper import WhisperModel

from app.engines.base import ProgressCallback, Segment, TranscriptionResult, progress_percent

logger = logging.getLogger(__name__)


class FasterWhisperEngine:
    """Whisper via faster-whisper (CTranslate2). The model is loaded once, in the constructor."""

    def __init__(self, model_id: str, device: str, compute_type: str, vad: bool) -> None:
        start = time.perf_counter()
        # A Hugging Face repo id is downloaded to HF_HOME on first use, then read from the cache.
        self._model = WhisperModel(model_id, device=device, compute_type=compute_type)
        self._vad = vad
        logger.info(
            "model loaded",
            extra={
                "model": model_id,
                "device": device,
                "compute_type": compute_type,
                "vad": vad,
                "load_sec": round(time.perf_counter() - start, 2),
            },
        )

    def transcribe(
        self,
        path: Path,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        # The VAD filter (Silero, bundled with faster-whisper) skips non-speech audio, which is
        # faster on recordings with silence and avoids text hallucinated from noise.
        raw_segments, info = self._model.transcribe(
            str(path), language=language, vad_filter=self._vad
        )

        # `raw_segments` is a lazy generator: the actual decoding happens while we iterate it.
        segments: list[Segment] = []
        for raw in raw_segments:
            segments.append(Segment(start=raw.start, end=raw.end, text=raw.text.strip()))
            if on_progress:
                on_progress(progress_percent(raw.end, info.duration))
        if on_progress:
            on_progress(100.0)

        return TranscriptionResult(
            text=" ".join(segment.text for segment in segments),
            language=info.language,
            duration_sec=info.duration,
            segments=segments,
        )

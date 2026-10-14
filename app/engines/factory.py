from app.config import Settings
from app.engines.base import TranscriptionEngine
from app.engines.fake_engine import FakeEngine


def create_engine(settings: Settings) -> TranscriptionEngine:
    if settings.engine == "fake":
        return FakeEngine()

    # Imported here so that code paths which never use the real engine (API, tests with the fake
    # engine) do not pay for importing CTranslate2 and friends.
    from app.engines.faster_whisper_engine import FasterWhisperEngine

    return FasterWhisperEngine(
        model_id=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        vad=settings.whisper_vad,
    )

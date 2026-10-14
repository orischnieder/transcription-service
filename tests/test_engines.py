from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.engines import faster_whisper_engine
from app.engines.base import Segment, TranscriptionError
from app.engines.factory import create_engine
from app.engines.fake_engine import FakeEngine
from app.engines.faster_whisper_engine import FasterWhisperEngine

MEDIA = Path("unused.wav")


class FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel so no model is downloaded or loaded."""

    def __init__(self, model_id: str, device: str, compute_type: str) -> None:
        self.init_args = (model_id, device, compute_type)
        self.transcribe_kwargs: dict[str, Any] = {}

    def transcribe(self, path: str, **kwargs: Any) -> tuple[Any, Any]:
        self.transcribe_kwargs = kwargs
        segments = iter(
            [
                SimpleNamespace(start=0.0, end=5.0, text=" Hello"),
                SimpleNamespace(start=5.0, end=10.0, text=" world. "),
            ]
        )
        return segments, SimpleNamespace(language="en", duration=10.0)


@pytest.fixture
def fake_whisper_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(faster_whisper_engine, "WhisperModel", FakeWhisperModel)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


# Factory


def test_factory_returns_fake_engine() -> None:
    assert isinstance(create_engine(_settings(engine="fake")), FakeEngine)


@pytest.mark.usefixtures("fake_whisper_model")
def test_factory_builds_faster_whisper_engine_from_settings() -> None:
    engine = create_engine(
        _settings(
            engine="faster-whisper",
            whisper_model="Systran/faster-whisper-base",
            whisper_device="cpu",
            whisper_compute_type="int8",
            whisper_vad=False,
        )
    )

    assert isinstance(engine, FasterWhisperEngine)
    assert engine._model.init_args == ("Systran/faster-whisper-base", "cpu", "int8")
    assert engine._vad is False


# FasterWhisperEngine (with the model replaced)


@pytest.mark.usefixtures("fake_whisper_model")
def test_faster_whisper_engine_builds_result_and_reports_progress() -> None:
    engine = FasterWhisperEngine("any/model", "cpu", "int8", vad=True)
    progress: list[float] = []

    result = engine.transcribe(MEDIA, language="en", on_progress=progress.append)

    assert result.text == "Hello world."
    assert result.language == "en"
    assert result.duration_sec == 10.0
    assert result.segments == [Segment(0.0, 5.0, "Hello"), Segment(5.0, 10.0, "world.")]
    assert progress == [50.0, 100.0, 100.0]
    assert engine._model.transcribe_kwargs == {"language": "en", "vad_filter": True}


# FakeEngine


def test_fake_engine_is_deterministic() -> None:
    engine = FakeEngine()

    assert engine.transcribe(MEDIA) == engine.transcribe(MEDIA)


def test_fake_engine_uses_requested_language_or_defaults_to_english() -> None:
    engine = FakeEngine()

    assert engine.transcribe(MEDIA, language="he").language == "he"
    assert engine.transcribe(MEDIA).language == "en"


def test_fake_engine_reports_increasing_progress_up_to_100() -> None:
    progress: list[float] = []

    FakeEngine().transcribe(MEDIA, on_progress=progress.append)

    assert progress == sorted(progress)
    assert progress[-1] == 100.0


def test_fake_engine_can_be_configured_to_fail() -> None:
    with pytest.raises(TranscriptionError):
        FakeEngine(fail=True).transcribe(MEDIA)

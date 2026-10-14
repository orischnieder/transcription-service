import json
import shutil
import wave
from pathlib import Path

import pytest

from app.services.media_probe import FfprobeMediaProbe, MediaProbeError, parse_ffprobe_output

# Parsing (no ffprobe needed)


def ffprobe_json(streams: list[dict[str, str]], duration: str | None = None) -> str:
    fmt = {"duration": duration} if duration is not None else {}
    return json.dumps({"streams": streams, "format": fmt})


def test_parse_reads_container_duration() -> None:
    output = ffprobe_json([{"codec_type": "video"}, {"codec_type": "audio"}], duration="12.5")

    assert parse_ffprobe_output(output).duration_sec == 12.5


def test_parse_falls_back_to_audio_stream_duration() -> None:
    output = ffprobe_json([{"codec_type": "audio", "duration": "7.0"}], duration="N/A")

    assert parse_ffprobe_output(output).duration_sec == 7.0


def test_parse_rejects_file_without_audio() -> None:
    with pytest.raises(MediaProbeError, match="no audio"):
        parse_ffprobe_output(ffprobe_json([{"codec_type": "video"}], duration="5"))


def test_parse_rejects_unknown_duration() -> None:
    with pytest.raises(MediaProbeError, match="duration"):
        parse_ffprobe_output(ffprobe_json([{"codec_type": "audio"}]))


# Integration with the real ffprobe (runs in the Docker test image)

requires_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe not installed"
)


@requires_ffprobe
def test_ffprobe_reads_real_wav(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000 * 2)  # 2 seconds of silence

    assert FfprobeMediaProbe().probe(path).duration_sec == pytest.approx(2.0, abs=0.05)


@requires_ffprobe
def test_ffprobe_rejects_non_media(tmp_path: Path) -> None:
    path = tmp_path / "fake.mp3"
    path.write_text("this is not audio")

    with pytest.raises(MediaProbeError):
        FfprobeMediaProbe().probe(path)

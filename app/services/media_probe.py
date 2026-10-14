import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

FFPROBE_TIMEOUT_SEC = 30


@dataclass(frozen=True)
class MediaInfo:
    duration_sec: float


class MediaProbeError(Exception):
    """The file is not readable media, has no audio stream, or its duration is unknown."""


class MediaProbe(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...


class FfprobeMediaProbe:
    """Inspects a file with `ffprobe`. Blocking: call it from a thread, never the event loop."""

    def probe(self, path: Path) -> MediaInfo:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration:stream=codec_type,duration",
            str(path),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_SEC, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaProbeError("ffprobe timed out") from exc
        if completed.returncode != 0:
            raise MediaProbeError(f"ffprobe failed: {completed.stderr.strip()[:200]}")
        return parse_ffprobe_output(completed.stdout)


def parse_ffprobe_output(output: str) -> MediaInfo:
    data = json.loads(output)
    streams: list[dict[str, Any]] = data.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio_streams:
        raise MediaProbeError("no audio stream")

    # The container duration is the most reliable; fall back to the audio stream's own duration.
    candidates = [data.get("format", {}).get("duration")]
    candidates += [stream.get("duration") for stream in audio_streams]
    for value in candidates:
        duration = _to_float(value)
        if duration is not None and duration > 0:
            return MediaInfo(duration_sec=duration)
    raise MediaProbeError("unknown duration")


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):  # missing, or "N/A"
        return None

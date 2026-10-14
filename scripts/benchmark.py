"""Benchmark a faster-whisper model on a local file.

Usage (inside the worker image, samples mounted at /samples):
    python -m scripts.benchmark /samples/jfk.flac --model Systran/faster-whisper-base

Prints model load time, transcription time and real-time factor (transcription time / audio
duration; below 1.0 means faster than real time). The first run of a model also downloads it,
so run it twice and record the second run's load time.
"""

import argparse
import time
from pathlib import Path

from app.config import get_settings
from app.engines.faster_whisper_engine import FasterWhisperEngine

PREVIEW_CHARS = 300


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--model", default=settings.whisper_model)
    parser.add_argument("--device", default=settings.whisper_device)
    parser.add_argument("--compute-type", default=settings.whisper_compute_type)
    parser.add_argument("--language", default=None, help="e.g. 'en'; auto-detect if omitted")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    start = time.perf_counter()
    engine = FasterWhisperEngine(args.model, args.device, args.compute_type)
    load_sec = time.perf_counter() - start

    start = time.perf_counter()
    result = engine.transcribe(args.file, language=args.language)
    transcribe_sec = time.perf_counter() - start

    rtf = transcribe_sec / result.duration_sec if result.duration_sec else float("nan")
    print(f"model:          {args.model} ({args.device}, {args.compute_type})")
    print(f"file:           {args.file} ({result.duration_sec:.1f} s audio)")
    print(f"language:       {result.language}")
    print(f"load time:      {load_sec:.2f} s")
    print(f"transcription:  {transcribe_sec:.2f} s")
    print(f"real-time factor: {rtf:.3f}")
    print(f"segments:       {len(result.segments)}")
    print(f"preview:        {result.text[:PREVIEW_CHARS]}")


if __name__ == "__main__":
    main()

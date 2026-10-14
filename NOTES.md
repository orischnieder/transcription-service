# NOTES – decisions log

A running log of decisions made while building the service. It feeds the README.
Format of each entry:

```
## <short title>
- Context: why a decision was needed
- Decision: what was chosen
- Alternatives: what else was considered
- Consequences: trade-offs, limitations, what would change in production
```

---

## Milestone 1 dependencies only
- Context: `pyproject.toml` pins direct dependencies; adding everything up front means pinning libraries
  that are not used yet.
- Decision: Milestone 1 pins only `fastapi`, `uvicorn`, `pydantic-settings`, `redis` (runtime) and `pytest`,
  `httpx2` (required by FastAPI's `TestClient`, see below), `fakeredis`, `ruff`, `mypy` (dev). `rq`,
  `faster-whisper` and `python-multipart` are added in the milestones that use them.
- Alternatives: `uvicorn[standard]` (uvloop/httptools; faster, but more native dependencies than this
  workload needs: the bottleneck is the model, not HTTP parsing). A lockfile tool (uv / pip-tools) for
  transitive pins.
- Consequences: direct dependencies are pinned, transitive ones are not, so a rebuild months later can
  pull newer sub-dependencies. In production I would add a lockfile.

## Test client uses httpx2 instead of httpx
- Context: Starlette 1.8 emits a deprecation warning when its `TestClient` runs on `httpx`, recommending
  `httpx2` (the successor maintained by the Pydantic team).
- Decision: the `[dev]` extra pins `httpx2` instead of `httpx`. It was a one-line change in `pyproject.toml`;
  no test code changed and all tests pass without the warning.
- Alternatives: keep `httpx` and filter the warning (hides a known upcoming break).
- Consequences: dev-only dependency; the runtime image is unaffected.

## Dependencies installed in Docker from pyproject.toml, project not installed
- Context: installing the package (`pip install .`) needs the source, which would invalidate the
  dependency layer on every code change.
- Decision: the Dockerfile reads the dependency list from `pyproject.toml` with `tomllib` and installs it
  before copying the source. The code runs from `/srv` without being installed.
- Alternatives: copy a separate `requirements.txt` (two places to keep in sync); `pip install .` after
  copying the source (slow rebuilds).
- Consequences: one source of truth for dependencies and fast rebuilds. Local dev still uses
  `pip install -e ".[dev]"`.

## Uniform error shape, including framework errors
- Context: the UI maps `error.code` to plain-language messages, so every error must have the same shape.
- Decision: one `AppError(code, message, status_code)` for domain errors, plus handlers that convert
  FastAPI validation errors (`422 invalid_request`), routing errors (`404 not_found`,
  `405 method_not_allowed`) and unexpected exceptions (`500 internal_error`, generic message, stack trace
  logged with `request_id`) into `{"error": {"code", "message"}}`.
- Alternatives: FastAPI defaults (`{"detail": ...}`, a different shape per error type).
- Consequences: the client has a single error path. Internal details never leak in 500 responses.

## /health returns the error shape on failure
- Context: `/health` must return `503` when Redis is unreachable.
- Decision: on success `200 {"status":"ok","redis":"ok"}`; on failure `503` with the standard error body,
  code `redis_unavailable`. Redis client uses 2 s connect/read timeouts so the check fails fast.
- Alternatives: a `503` body like `{"status":"error","redis":"unreachable"}` (more detail per dependency,
  but a second error shape).
- Consequences: consistent with every other endpoint. If more dependencies are checked later, a
  per-dependency body may be worth revisiting.

## Sync Redis client, blocking calls in sync endpoints
- Context: blocking calls must not block the event loop.
- Decision: use the synchronous `redis` client (RQ needs it anyway) and declare endpoints that call it as
  plain `def`, so FastAPI runs them in its threadpool.
- Alternatives: `redis.asyncio` in the API (two clients to configure; RQ is sync regardless).
- Consequences: threadpool size (40 by default in AnyIO) bounds concurrent Redis-using requests. More than
  enough for this service.

## One Redis client per process, created in the app lifespan
- Context: the client holds a connection pool and should be created once, not per request.
- Decision: created in `create_app`'s lifespan, stored on `app.state`, exposed through the `get_redis`
  dependency. Tests override `get_redis` with `fakeredis`.
- Alternatives: module-level global (harder to replace in tests; created at import time).
- Consequences: no import-time side effects; uvicorn starts the app with `--factory`.

## Logging
- Context: logs must be useful when debugging at 3am, both by eye and in a log aggregator.
- Decision: stdlib `logging` with two small custom formatters: text
  (`timestamp level logger message key=value ...`, UTC, ms) and JSON (`LOG_FORMAT=json`). Context is passed
  through `extra={...}` and printed as key/value pairs. A request middleware logs
  `method path status duration_ms request_id` and returns `X-Request-ID` (taken from the request if
  present). uvicorn's access log is disabled because the middleware replaces it; uvicorn's own logs use
  the same format. Successful `/health` probes are logged at DEBUG so Docker healthchecks don't flood INFO.
- Alternatives: `python-json-logger` or `structlog` (one more dependency for ~40 lines of code).
- Consequences: no extra dependency. `request_id` is not yet propagated automatically to every log line
  (would need a `contextvar`); it is logged on request and error lines.

## Worker placeholder
- Context: Milestone 1 needs a `worker` service that starts and idles cleanly before RQ exists.
- Decision: `python -m app.worker` configures logging, logs that it started, and waits for SIGTERM/SIGINT.
- Alternatives: `sleep infinity` in Compose (does not exercise our image/logging).
- Consequences: replaced by the RQ `SimpleWorker` in Milestone 4.

## Docker image and Compose
- Context: one-command startup, small reproducible image, safe defaults.
- Decision: multi-stage `Dockerfile` on `python:3.12-slim` with `ffmpeg`, non-root user (uid 1000),
  `HF_HOME=/models`. Stages: `base` → `dev` (dev deps + tests, used by the `tests` service) and `runtime`
  (default, used by `api` and `worker`). `api` and `worker` share one image (`transcription-service:runtime`)
  through a YAML anchor. Healthchecks: `redis-cli ping`, and a Python `urllib` call to `/health` for `api`
  (no curl in the image). `.env` is loaded if present (`required: false`), so `docker compose up --build`
  works without creating one. Redis image pinned to `redis:7.4-alpine`.
- Alternatives: separate images for api and worker (slower builds, no real benefit yet).
- Consequences: the runtime image includes ffmpeg for the api too (needed for `ffprobe` validation in
  Milestone 3). Redis data is not persisted (no volume), consistent with jobs being short-lived.

## Engine interface
- Context: the engine must be replaceable, and tests must run without a model.
- Decision: `TranscriptionEngine` is a `typing.Protocol` with one method,
  `transcribe(path, language=None, on_progress=None) -> TranscriptionResult`. Results are frozen dataclasses
  (`Segment`, `TranscriptionResult`). `on_progress` receives a percentage (0-100) computed as
  segment end time / media duration; the engine always finishes with 100. Throttling of progress writes is
  the caller's job (Milestone 4), so engines stay simple. `TranscriptionError` is the engine-level failure.
- Alternatives: an abstract base class (forces inheritance; a Protocol lets any class with the right method
  fit, including test doubles). Pydantic models for results (validation is not needed for internal data).
- Consequences: adding an engine (e.g. a `transformers` pipeline) means one new class plus one branch in
  `engines/factory.py`.

## Engine factory imports faster-whisper lazily
- Context: `faster_whisper` pulls in CTranslate2, onnxruntime and PyAV. The API never transcribes.
- Decision: `create_engine(settings)` imports `FasterWhisperEngine` inside the function, only when
  `ENGINE=faster-whisper`. The model is loaded in the engine's constructor, so whoever creates the engine
  (the worker, once at start) pays the load cost once.
- Alternatives: top-level import (API and fake-engine tests would import the heavy stack for nothing).
- Consequences: the API process never imports `faster_whisper`. Note the shared runtime image still
  contains it (see Docker image size below).

## Engine defaults left as faster-whisper defaults
- Context: `WhisperModel.transcribe` has many knobs (beam size, VAD filter, temperature fallback).
- Decision: use faster-whisper's defaults (beam size 5, no VAD filter); only `language` is passed.
- Alternatives: `vad_filter=True` (skips silence, can speed up long recordings and reduce hallucinated text
  on silence, but can also clip quiet speech).
- Consequences: simple and predictable. VAD is a candidate tuning if long silent recordings show
  hallucinations or slow progress.

## PyAV pinned below 19
- Context: during the first benchmark run, decoding failed with
  `TypeError: open() got an unexpected keyword argument 'metadata_errors'`. `faster-whisper==1.2.1` declares
  `av>=11`, so pip installed `av==19.0.1`, which removed that argument.
- Decision: pin `av==18.1.0` in `pyproject.toml` (with a comment), verified by decoding the samples inside
  the image.
- Alternatives: patch/monkeypatch faster-whisper (fragile); wait for an upstream release.
- Consequences: one transitive dependency is pinned explicitly. Remove the pin when faster-whisper supports
  av 19. This is the concrete reason a lockfile would be worth adding (see "Milestone 1 dependencies only").

## Benchmark script
- Context: the default model should be chosen from measurements, not guesses.
- Decision: `scripts/benchmark.py` loads a model through `FasterWhisperEngine` and prints load time,
  transcription time, real-time factor (RTF = transcription time / audio duration) and a text preview. It is
  copied into the runtime image so it runs in the same environment as the worker. `print` is used because it
  is a CLI, not service code. Run:
  `docker compose run --rm --no-deps -v ./samples:/samples:ro worker python -m scripts.benchmark /samples/physicsworks.wav --model Systran/faster-whisper-base`
- Alternatives: measuring inside the worker once it exists (mixes queue overhead into the numbers).
- Consequences: sample files are mounted at run time, never baked into the image (`samples/` is in
  `.dockerignore` and `.gitignore`).

## Benchmark results (base vs small)
- Context: choose the default `WHISPER_MODEL`.
- Setup: worker image, CPU only, `int8`, Intel Core i7-1255U (laptop, 10 cores / 12 threads),
  Docker Desktop with 12 CPUs and 8 GB RAM, language auto-detected (detected `en`). Each model run twice;
  the second run's load time excludes the download.
- Smoke test, `samples/jfk.flac` (11.0 s), base: transcription 1.43 s, RTF 0.13, text exact.
- `samples/physicsworks.wav` (203.3 s, English lecture):

  | Model | Load (warm) | Load (first, incl. download) | Transcription | RTF | Segments |
  |---|---|---|---|---|---|
  | `Systran/faster-whisper-base` | 0.7 s | 0.8 s* | 11.5 / 12.1 s | 0.06 | 54 |
  | `Systran/faster-whisper-small` | 1.8 s | 18.2 s | 34.3 / 35.1 s | 0.17 | 42 |

  \* base had already been downloaded during the failed smoke test.
- Quality: the two transcripts are 98% identical word for word (423 words each). Differences: base wrote
  "nuclear" where small wrote "mechanical" (the lecture is about conservation of mechanical energy);
  "rackable/wrecking" (base) vs "racquetball/racquet" (small) for the same phrase (not verified against the
  audio); one dropped/added short word each; number formatting ("3 2 1 0" vs "three two one zero").
- Interpretation: small is ~3x slower but still ~6x faster than real time (a 1-hour file ≈ 10 minutes on this
  machine; base ≈ 3.5 minutes). This sample is clean, single-speaker English, which flatters base; the gap is
  known to widen on accents, noise and non-English audio, where base degrades much more.
- Decision: **pending Ori.** The configured default remains `Systran/faster-whisper-small` (from CLAUDE.md);
  `WHISPER_MODEL` switches it without code changes.

## Docker image size
- Context: adding faster-whisper grew the runtime image.
- Decision: accept it for now. Runtime image is 1.42 GB (ffmpeg + CTranslate2 + onnxruntime + PyAV); the
  model is not in the image, it lives on the `models` volume.
- Alternatives: separate slim API image without the ML stack (smaller API image, but two images to build
  and keep in sync).
- Consequences: the API container carries libraries it never imports. A split image is listed as a
  production improvement.

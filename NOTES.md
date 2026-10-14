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

## Engine defaults: faster-whisper defaults plus VAD
- Context: `WhisperModel.transcribe` has many knobs (beam size, VAD filter, temperature fallback).
- Decision: use faster-whisper's defaults (beam size 5, temperature fallback) except the VAD filter, which
  is on by default (see "VAD filter enabled by default").
- Alternatives: tuning beam size / temperature (no evidence yet that it is needed).
- Consequences: simple and predictable; only `language` and `vad_filter` are passed.

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
- Decision: see "Default model: faster-whisper-small" below.

## Default model: faster-whisper-small
- Context: base vs small benchmark above.
- Decision: the default stays `Systran/faster-whisper-small` (CTranslate2 conversion of `openai/whisper-small`,
  MIT). First-run download: **464 MB** into the `models` volume (`HF_HOME=/models`), about 18 s on this
  connection; afterwards it loads from the cache in 1-2 s. (`base` is 142 MB.)
- Reasoning: small still runs ~6x faster than real time on a laptop CPU, which is acceptable for an async,
  queued service where the user sees progress. It is noticeably more robust than base on accented, noisy
  and non-English audio, which a general upload service will receive; our English sample understates that
  gap. Being wrong on accuracy costs the user more than waiting a few extra minutes.
- Alternatives: `base` (~3x faster, weaker outside clean English); `medium` / `large-v3` (better accuracy,
  too slow on CPU for this setup); `distil-whisper` variants (fast, but English-only).
- Consequences: a 1-hour file takes ~10 minutes on this machine. The first job after a fresh install waits
  for the 464 MB download. Users with a GPU or other needs change `WHISPER_MODEL` / `WHISPER_DEVICE` /
  `WHISPER_COMPUTE_TYPE` without code changes.

## VAD filter enabled by default
- Context: Whisper can hallucinate text (repeated phrases, "Thank you.") on silence, music or noise, and
  spends time decoding non-speech audio.
- Decision: `WHISPER_VAD` setting, default `true`, passed as `vad_filter` to faster-whisper. It uses the
  Silero VAD model bundled with faster-whisper (no extra download, no new dependency).
- Measurements (small, `int8`, same machine; both variants in one process, model already loaded):

  | File | VAD off | VAD on | Word similarity | Differences |
  |---|---|---|---|---|
  | `jfk.flac` (11.0 s) | 2.19 s | 2.26 s | 100% | none |
  | `physicsworks.wav` (203.3 s) | 31.28 s | 29.50 s | 98.9% | "100" vs "a hundred percent", "I'm" vs "I am", one short word added/dropped each |

  Separate benchmark-script runs with VAD on: jfk RTF 0.38 (first call in a fresh process includes VAD
  warm-up, 4.1 s), physicsworks 31.5 / 30.1 s, RTF 0.15 (vs 34.3 / 35.1 s, RTF 0.17, without VAD earlier).
- Interpretation: on these samples, which are mostly speech, VAD is roughly neutral on accuracy and ~6%
  faster. Its main value is on audio with long silences or non-speech sections, where it skips them
  entirely and prevents hallucinated text; neither sample exercises that strongly.
- Alternatives: VAD off (simpler, but hallucinations on silence are a known Whisper failure mode).
- Consequences: very quiet speech could be classified as non-speech and dropped. `WHISPER_VAD=false`
  turns it off without code changes.

## Docker image size
- Context: adding faster-whisper grew the runtime image.
- Decision (confirmed by Ori after Milestone 2): keep a single image for now. Runtime image is 1.42 GB (ffmpeg + CTranslate2 + onnxruntime + PyAV); the
  model is not in the image, it lives on the `models` volume.
- Alternatives: separate slim API image without the ML stack (smaller API image, but two images to build
  and keep in sync).
- Consequences: the API container carries libraries it never imports. A split image is listed as a
  production improvement.

## python-multipart dependency
- Context: FastAPI needs a multipart parser to accept `UploadFile` / `Form` parameters.
- Decision: add `python-multipart==0.0.32` (the parser FastAPI/Starlette use; Apache-2.0).
- Alternatives: parse `request.stream()` by hand (more code, easy to get wrong; loses the `/docs` upload form).
- Consequences: see "Upload size limit and Starlette's temporary file" for what it means for streaming.

## Upload endpoint is a sync `def`, run in the threadpool
- Context: copying the upload to disk, running `ffprobe` and the sync Redis client all block.
- Decision: `POST /api/jobs` and `GET /api/jobs/{id}` are plain `def` handlers, so FastAPI runs each one in
  its threadpool. The service functions (`submit_job`, `save_upload`, `FfprobeMediaProbe.probe`) are plain
  blocking functions and document that they must not run on the event loop.
- Alternatives: `async def` handlers that wrap each blocking call in `run_in_threadpool` (more ceremony,
  same effect); `asyncio.create_subprocess_exec` for ffprobe (async, but the file copy and Redis would
  still need threads).
- Consequences: one consistent rule (all blocking work in sync handlers). Concurrent uploads are bounded by
  the threadpool size (40 by default), plenty for this service.

## Upload size limit and Starlette's temporary file
- Context: the rule is to stream the upload to disk in 1 MB chunks and abort as soon as `MAX_UPLOAD_MB` is
  exceeded.
- Decision: our code copies from the upload in 1 MB chunks into `UPLOAD_DIR`, counts bytes, and stops and
  deletes the partial file as soon as the limit is exceeded (`413 file_too_large`).
- Limitation (honest): before our handler runs, Starlette's multipart parser has already received the whole
  request body into a `SpooledTemporaryFile` (memory up to 1 MB, then the container's `/tmp`). So an
  oversized upload is rejected without being copied into `UPLOAD_DIR`, but the bytes have still crossed the
  network and briefly occupied `/tmp`.
- Alternatives: a custom streaming multipart parser over `request.stream()` (true early abort, but more code
  and no `/docs` form).
- Consequences: in production the hard limit belongs at the reverse proxy (e.g. nginx
  `client_max_body_size`), which rejects oversized bodies before they reach the app. The UI (Milestone 5)
  also checks the size before uploading. Since Milestone 4 an early `Content-Length` check closes most of
  the gap (next entry).

## Early Content-Length check for uploads
- Context: the chunked-copy limit only runs after Starlette has received the whole body (previous entry).
  Ori decided to add a cheap early rejection as well, keeping the chunked check and the reverse-proxy note.
- Decision: an HTTP middleware (`upload_size_guard` in `app/main.py`) inspects `POST /api/jobs` only. If
  `Content-Length` is larger than `MAX_UPLOAD_MB` + 64 KiB (allowance for multipart boundaries, part headers
  and the `language` field), it returns `413 file_too_large` (same code and message as the chunked check)
  without reading the body. A missing or non-numeric header (including non-ASCII digits such as `²`, which
  `str.isdigit()` accepts but `int()` rejects) falls through to the existing checks. Request logging wraps
  this middleware, so rejected uploads are logged like any other request.
- Alternatives: only the reverse proxy (not present in local/Docker setup); the custom streaming parser.
- Consequences: honest clients (browsers, curl) are rejected immediately. Measured with curl and a 210 MB
  file: `413` in 18 ms with 0 bytes sent, because curl sends `Expect: 100-continue` and uvicorn answers
  before asking for the body. A client that omits or understates `Content-Length` still gets the
  authoritative chunked check. The 64 KiB allowance means a file up to ~64 KiB over the limit passes this
  check and is then rejected by the chunked copy.

## Validation order and file handling
- Context: reject bad input as cheaply and early as possible, and never keep rejected files.
- Decision: order is language code → extension allow-list (case-insensitive) → copy with size limit →
  empty check → `ffprobe` (at least one audio stream, readable duration) → duration limit. Any failure
  deletes the stored file before the error is returned. The stored file is named `{job_id}.{ext}`; the
  user's filename is only kept (path stripped, max 255 chars) for display.
- Alternatives: trust `Content-Type` (clients send whatever they like); use the original filename on disk
  (path traversal and collision risks).
- Consequences: a file is accepted only if ffprobe finds audio. Duration comes from the container, falling
  back to the audio stream. Files without a known duration (e.g. some browser `MediaRecorder` WebM files)
  are rejected as `invalid_media`; acceptable since the UI uploads files rather than recording.

## MediaProbe interface
- Context: API tests must not depend on ffprobe being installed.
- Decision: `MediaProbe` Protocol with `probe(path) -> MediaInfo`; `FfprobeMediaProbe` calls
  `ffprobe -print_format json` with a 30 s timeout. JSON parsing is a separate pure function so it is
  unit-tested without ffprobe. Tests override `get_media_probe` with a fake. Two integration tests use the
  real ffprobe and are skipped automatically when it is missing (they run in the Docker test image).
- Alternatives: call ffprobe directly from the upload code (untestable without ffmpeg).
- Consequences: if the `ffprobe` binary is missing, `FileNotFoundError` surfaces as `500 internal_error`
  (a deployment problem, not the user's), with a stack trace in the logs.

## Language validation without importing the engine
- Context: `language` must be validated in the API (`422 invalid_language`), but the API never imports the
  engine, which owns the language list.
- Decision: `app/services/languages.py` keeps the 99 Whisper language codes. Empty or `auto` means
  auto-detect. A unit test asserts the list is a subset of faster-whisper's own list, so drift is caught.
  `yue` (Cantonese) is left out: only large-v3 models support it.
- Alternatives: import faster-whisper's list in the API (heavy import, couples API to the engine); accept
  any code and fail in the worker (the user waits for a queued job just to see an error).
- Consequences: switching to a large-v3 model would need `yue` added.

## Job state in Redis
- Context: job status must survive between requests and be readable by the API and the worker.
- Decision: `Job` (Pydantic) stored as a Redis hash `job:{id}`; values stored as strings, `None` fields
  omitted, Pydantic converts them back on read. Hash and TTL (`JOB_TTL_HOURS`) are written in one
  `MULTI/EXEC` pipeline so a job never exists without an expiry. Job id is `uuid4().hex`.
- Alternatives: one JSON string per job (simpler, but partial updates such as progress would rewrite the
  whole document; hashes allow `HSET` of single fields in Milestone 4).
- Consequences: no history beyond the TTL. If Redis fails while recording the job, the stored file is
  deleted and the API returns `503 queue_unavailable` (the queue lives in the same Redis).

## Job submission orchestration in `services/jobs.py`
- Context: route handlers must stay thin, and `services/uploads.py` should only deal with files.
- Decision: `submit_job()` in `app/services/jobs.py` (a file not listed in the original layout) validates the
  language, calls `save_upload()`, creates the `Job` and stores it. Milestone 4 adds the enqueue step here.
- Alternatives: orchestration in the route handler (business logic in the API layer); inside `uploads.py`
  (mixes file handling with job bookkeeping).
- Consequences: one function owns the "accept a job" flow, including cleanup if a later step fails.

## Log field names avoid LogRecord attributes
- Context: the first test run failed with `KeyError: "Attempt to overwrite 'filename' in LogRecord"`:
  `filename` is a built-in `LogRecord` attribute (the source file of the log call), so it cannot be passed
  through `extra`.
- Decision: log the uploaded file's name as `file_name`.
- Consequences: the worker logs in Milestone 4 use `file_name` too. Other reserved names to avoid:
  `name`, `module`, `message`, `args`, `lineno`, `process`, `thread`.

## Manual check against the running stack (Milestone 3)
- `docker compose up --build`, then real requests with curl: `jfk.flac`, `physicsworks.wav` (with
  `language=en`) and a generated MP4 with an audio track → `202`; MP4 with no audio track → `422
  invalid_media` (log reason: `no audio stream`); 61-minute Opus → `422 media_too_long`; text renamed to
  `.mp3` → `422 invalid_media` (ffprobe error logged); empty `.wav` → `400 empty_file`; `.toml` → `415`;
  `language=xx` → `422 invalid_language`; unknown job → `404`; `/docs` → `200`. Only the three accepted
  files remained in the `uploads` volume (removed afterwards, since nothing processes them until
  Milestone 4).

## rq dependency
- Context: jobs must run outside the API process (decision: Redis + RQ, `SimpleWorker`).
- Decision: `rq==2.12.0` (BSD-2-Clause). RQ ships type hints (`py.typed`), so no mypy override is needed.
- Alternatives: Celery (more features, much heavier to configure); Arq (asyncio-based, but transcription is
  CPU-bound sync work); a hand-written Redis list consumer (reinvents timeouts and failure handling).
- Consequences: RQ's own log lines (`rq.worker`) go through our root handler and formatter, because RQ only
  installs its own handler when the logger has no effective handler.

## Enqueue by import path, same id in RQ and in the job store
- Context: the API must never import the engine or the worker task.
- Decision: `JobQueue.enqueue(job_id, media_path)` calls `enqueue_call("app.jobs.tasks.run_transcription",
  ...)` with a string path, `job_id` set to our job id, `timeout=JOB_TIMEOUT_SEC`, `result_ttl=0` (the task
  stores its own result) and `failure_ttl` = job TTL. The stored media path is passed as an argument, so the
  worker never has to rebuild it from the filename.
- Alternatives: pass the function object (imports `app.jobs.tasks` and RQ-side code into the API).
- Consequences: a typo in the path would only fail in the worker; a unit test asserts the enqueued
  function name.

## Submission order: record, enqueue, clean up on failure
- Context: the worker must always find the job it is handed, and a failed submission must not leave files
  or records behind.
- Decision: `submit_job` stores the job, then enqueues. On `RedisError` in either step it deletes the media
  file, makes a best-effort delete of the job record (ignored if Redis is down; the record would expire
  anyway) and returns `503 queue_unavailable`.
- Alternatives: enqueue first (the worker could start before the record exists).
- Consequences: covered by tests (file and keys removed when enqueue fails).

## Worker context: engine loaded once, task as a thin entry point
- Context: `SimpleWorker` runs jobs in its own process, so a model loaded at start can be reused.
- Decision: `python -m app.worker` loads settings and logging, builds the engine through the factory
  (model loaded once), stores a `WorkerContext(engine, store, model_name)` in `app.jobs.tasks`, then runs
  `SimpleWorker` on the `transcription` queue. `run_transcription(job_id, media_path)` is the RQ entry point;
  all the logic is in `process_job(job_id, media_path, context)`, which tests call directly with
  `FakeEngine` and `fakeredis`.
- Alternatives: load the model inside the task (reloads per job); a custom RQ `Worker` subclass holding the
  engine (more RQ internals to understand).
- Consequences: one module-level variable, set once at start. The worker's Redis client has no socket
  timeout because it blocks while waiting for jobs.

## Task behaviour and error codes
- Decision: `process_job` reads the job (missing → log a warning and skip, e.g. expired while queued), marks
  it `processing` (`started_at`, progress 0), transcribes with a progress reporter, then stores the result
  and marks `done` (progress 100, `language_detected`, `finished_at`). Any exception marks the job `failed`
  with `transcription_failed` ("We could not transcribe this file."), or `transcription_timeout` when RQ's
  `JobTimeoutException` fires, and is logged with `logger.exception` (stack trace + `job_id` + `error_code`).
  The media file is deleted in a `finally` block on every path, including the skip.
- The task does not re-raise: our job record is the source of truth for the UI, and RQ logs the job as OK.
- Consequences / limitation: if the worker process dies mid-job (OOM kill, container stop during a job),
  the job stays `processing` until its TTL expires and the media file may remain. Production fix: a small
  reaper that marks stale `processing` jobs as failed, or RQ's abandoned-job handling feeding our store.

## Progress reporting
- Decision: `ProgressReporter` is the engine's `on_progress` callback. It writes `progress` to Redis at most
  once per second (`PROGRESS_INTERVAL_SEC`), caps it at 99 while processing (100 only appears with `done`,
  so the UI never shows "100%" while still working), and logs the 25/50/75% milestones once each. The
  clock is injectable, so throttling is tested without sleeping.
- Observed (physicsworks.wav, small, VAD on): polled every 1.5 s, the API showed queued 0 → processing 0 →
  3 → 15 → 45 → done 100 over ~35 s. Progress advances per decoded segment, so it moves in steps.

## TTL refreshed on every write
- Context: `HSET` on a key that has already expired creates a new key without a TTL, which would leak.
- Decision: every `JobStore` write (`create`, `mark_processing`, `set_progress`, `mark_done`, `mark_failed`)
  runs `HSET` + `EXPIRE` in one `MULTI/EXEC`. The result (`job:{id}:result`, JSON) is written in the same
  transaction as `done`, with the same TTL.
- Consequences: a job and its result are kept for `JOB_TTL_HOURS` after the last change, i.e. 24 h after it
  finished rather than 24 h after upload. Both keys expire together.

## Job response and downloads
- Decision: `GET /api/jobs/{id}` returns `JobResponse` (the `Job` fields plus `result`, set only when
  `done`). `transcript.txt` is one segment per line (`text/plain; charset=utf-8`); `transcript.srt` uses
  `HH:MM:SS,mmm` timestamps rounded to the millisecond (`application/x-subrip; charset=utf-8`). Both are
  attachments named after the uploaded file (`talk.mp3` → `talk.txt`), with an ASCII fallback name and the
  exact UTF-8 name in `filename*` (RFC 6266/5987), so non-ASCII filenames work and header injection is not
  possible. Not ready → `409 job_not_ready` (separate message for failed jobs); unknown or expired →
  `404 job_not_found`.
- Alternatives: `.txt` as one paragraph (`result.text`); harder to read for long recordings.
- Consequences: the full `result.text` (single paragraph) is still available in the JSON for the UI.

## Manual end-to-end check (Milestone 4)
- `docker compose up --build` (real `faster-whisper-small`, VAD on). Worker log: model loaded in 1.5 s, then
  `worker ready`. Uploaded `jfk.flac` and `physicsworks.wav` with curl: both returned `202` immediately
  (`queued`), `transcript.txt` returned `409` while processing, both finished `done` (jfk: 3.9 s, RTF 0.36;
  physicsworks: 31.1 s, RTF 0.15, 47 segments, language `en`). `.txt`/`.srt` downloads returned the
  expected content and headers. The `uploads` volume was empty afterwards. Worker logs showed
  `job started` → `job progress` 25/50/75 → `job done` with `job_id` on every line. The tests also pass in
  the Docker test image (85 passed, including the real-ffprobe tests).

## Video is a first-class input
- Context: the task asks for a "media file"; users will upload screen recordings, meetings and lectures as
  video, not only audio.
- Decision: video containers (`mp4`, `mov`, `mkv`, `webm`) are accepted alongside audio formats, validated
  the same way (ffprobe must find an audio stream), and decoded directly by faster-whisper (PyAV reads only
  the audio stream; no separate extraction step). User-facing messages say "audio or video" (e.g. "Please
  upload an audio or video file.", "The audio or video is longer than the 60 minute limit.", "We could not
  read this audio or video file. It may be damaged or have no sound track.").
- Verified end to end with the real model: `jfk.flac` packaged as MP4 (H.264 + AAC) and WebM (VP9 + Opus)
  with a video track; both finished `done` with the correct transcript (3.8-3.9 s each, same as the audio
  file), downloads named `jfk_video.srt`, media files deleted afterwards. A video without an audio track is
  rejected with `422 invalid_media`.
- Consequences: the worker reads the whole video file to demux its audio, so very large videos cost some
  extra disk I/O compared with an audio-only upload of the same length; decoding time is dominated by the
  model either way.

## Upload size limit raised to 1024 MB
- Context: the original default of 200 MB fits an hour of audio easily (a 1-hour MP3 at 128 kbit/s is
  ~58 MB) but not typical video: at 2-8 Mbit/s, one hour of video is roughly 0.9-3.6 GB.
- Decision: default `MAX_UPLOAD_MB=1024`. The size limit only protects disk and bandwidth; the work the
  service does is bounded by `MAX_DURATION_SEC` (default 60 minutes), because transcription time depends on
  audio length, not file size.
- Alternatives: keep 200 MB (rejects most real video over a few minutes); a much higher limit such as
  4 GB (covers HD hour-long video, but a single upload could fill a small disk and Starlette may spool it
  to `/tmp` when `Content-Length` is missing).
- Consequences: a 1080p recording near the 60-minute limit can still exceed 1 GB; such users get a clear
  `413` and can raise `MAX_UPLOAD_MB` or upload just the audio. With larger uploads the early
  `Content-Length` check and a reverse-proxy limit matter more, and the `uploads` volume needs room for
  several concurrent uploads (each file is deleted once its job finishes). Production improvement: extract
  the audio track client-side or in the API to store far less.

## Assumption: speech from the audio track only
- Context: a video may contain information that is not spoken: on-screen text, slides, subtitles burned
  into the picture, or embedded subtitle streams.
- Decision: we transcribe speech from the audio track only. On-screen text (OCR), burned-in or embedded
  subtitle streams and other visual content are out of scope. If a file has several audio tracks,
  faster-whisper decodes only the first audio stream (`container.decode(audio=0)`).
- Consequences: a silent video with captions, or a video whose speech is on a second audio track, will
  produce no or the wrong transcript. To be listed under limitations in the README (Milestone 6).

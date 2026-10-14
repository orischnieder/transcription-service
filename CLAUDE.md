# CLAUDE.md

Instructions for Claude Code working in this repository. Read this file and `PLAN.md` before doing anything.

## Project

A service that lets a user upload a media file (audio or video) and receive a transcription.
This is a take-home assignment for a backend / AI engineering role. Requirements:

- An open-source transcription model available on Hugging Face.
- Production-grade code: clean structure, error handling, readability.
- Trade-offs and assumptions documented explicitly (README / NOTES.md).
- Clear step-by-step instructions to build, run and test locally.
- A cloneable GitHub repository.

### Quality goals for this project

- Every significant decision is written down with its reasoning and alternatives.
- The API stays responsive while a transcription runs; the model never blocks a request.
- The transcription engine sits behind an interface and can be swapped.
- Bad input is rejected early with a clear, specific error.
- The test suite runs fast without downloading or loading a model.
- The whole stack starts with one command.
- Configuration and secrets live outside the code.
- Lint, format and type checks are clean.
- Logs carry enough context to debug a failed job without reproducing it.
- Shortcuts and limitations are stated openly.
- Technology choices are deliberate and justified.
- The UI is small, friendly and focused on what the user needs.

## Working agreement (important)

The human (Ori) owns every decision.

1. Work on **one milestone from `PLAN.md` at a time**. Do not start the next milestone until Ori says so.
2. At the end of each milestone:
   - Run formatting, lint, type checks and tests; they must be clean.
   - Update `NOTES.md` (decisions log, see below) with anything decided during the milestone.
   - Reply with a short summary: what was built, files changed, decisions made and why, any new concept
     explained in 1–3 plain sentences, and a suggested commit message. Then **stop**.
3. Do not change anything listed under "Decisions" without asking first. If you think a decision is wrong, say so and why.
4. Do not add a dependency without stating why it is needed and what the alternative was.
5. Prefer simple, readable, boring code over clever code. Small functions, clear names, no dead code.
6. Never put secrets, API keys or machine-specific paths in code. Everything configurable goes through `Settings`.
7. Do not commit; Ori commits after reviewing.

### NOTES.md format

A running decisions log that later feeds the README. One entry per decision:

```
## <short title>
- Context: why a decision was needed
- Decision: what was chosen
- Alternatives: what else was considered
- Consequences: trade-offs, limitations, what would change in production
```

## Decisions

| Area | Decision | Reason |
|---|---|---|
| Language | Python 3.12 | Team stack; best ML ecosystem |
| API | FastAPI + Pydantic v2 | Async, built-in validation, automatic OpenAPI docs at `/docs` |
| Engine | `faster-whisper` loading a Hugging Face model by full repo id (default `Systran/faster-whisper-small`, a CTranslate2 conversion of `openai/whisper-small`, MIT) | Fast on CPU, small image without PyTorch, model still comes from Hugging Face |
| Engine boundary | `TranscriptionEngine` Protocol; implementations `FasterWhisperEngine` and `FakeEngine`; chosen by `ENGINE` env var through a factory | Engine is replaceable; tests run without the model |
| Async work | Redis + RQ, separate `worker` service | API stays responsive; model never runs in the request path or the event loop |
| Worker type | `rq.SimpleWorker` (no fork per job), engine loaded once at worker start | Model is loaded once per worker (no cold start per job). Trade-off: a crashing job can take the worker down; Docker `restart: unless-stopped` covers it |
| Job state | Redis hash per job with TTL (`JOB_TTL_HOURS`) | Simple, enough for a single node. No long-term history (documented trade-off) |
| Files | Uploads streamed to a shared volume; media file deleted after the job finishes (success or failure) | Privacy and disk usage |
| Progress | Worker updates `progress` (0–100) from segment end time / media duration, throttled to ~1 update per second | Real progress for the user |
| Client updates | Polling `GET /api/jobs/{id}` every ~1.5 s | Simple and robust. SSE/WebSocket listed as future work |
| Media validation | Extension allow-list, size limit while streaming, empty check, `ffprobe` to confirm an audio stream and read duration, duration limit | Do not trust filename or Content-Type |
| UI | Single static page (HTML + CSS + vanilla JS) served by FastAPI, no CDNs | No build step; works offline / air-gapped |
| Output | Full text + timestamped segments; download as `.txt` and `.srt` | Useful to real users |
| Language | Auto-detect by default, optional override from the UI | |
| Tooling | `ruff` (lint + format), `mypy`, `pytest`, `fakeredis` | Clean, checked, fast tests |
| Packaging | `pyproject.toml` with pinned direct dependencies; dev extras `[dev]` | Reproducible builds |

## Architecture

Three services in `docker-compose.yml`:

- `api` – FastAPI. Validates uploads, stores the file, creates a job, enqueues it, returns `202` with the job id
  immediately. Serves job status, results, downloads, health and the static UI. Never imports the engine.
- `worker` – Loads the engine once, then processes jobs from the RQ queue `transcription`.
- `redis` – Queue and job state.

Shared volumes: `uploads` (api writes, worker reads and deletes) and `models` (Hugging Face cache, `HF_HOME`).

Job lifecycle: `queued` → `processing` → `done` | `failed`.

Redis data per job (key `job:{id}`, hash, with TTL):
`id, status, filename, size_bytes, media_duration_sec, language_requested, language_detected, progress,
error_code, error_message, created_at, started_at, finished_at`.
Result stored separately as JSON under `job:{id}:result` (`text`, `language`, `duration_sec`, `segments[start,end,text]`), same TTL.
Job ids are `uuid4().hex`.

## API contract

All JSON errors use one shape:

```json
{ "error": { "code": "file_too_large", "message": "Human readable message for the UI." } }
```

| Method & path | Success | Errors |
|---|---|---|
| `GET /` | UI page | |
| `GET /health` | `200 {"status":"ok","redis":"ok"}` | `503` if Redis is unreachable |
| `GET /api/limits` | `200` allowed extensions, max size MB, max duration sec, supported language codes (UI uses it for client-side checks and the language list) | |
| `POST /api/jobs` (multipart: `file`, optional `language`) | `202` job object + `Location` header | `400 empty_file`, `413 file_too_large`, `415 unsupported_file_type`, `422 invalid_media` (unreadable / no audio stream), `422 media_too_long`, `422 invalid_language`, `503 queue_unavailable` |
| `GET /api/jobs/{id}` | `200` job object; includes `result` when `done` | `404 job_not_found` |
| `GET /api/jobs/{id}/transcript.txt` | `200 text/plain` attachment | `404 job_not_found`, `409 job_not_ready` |
| `GET /api/jobs/{id}/transcript.srt` | `200` SRT attachment | `404 job_not_found`, `409 job_not_ready` |

Request/response bodies are Pydantic models in `app/api/schemas.py`. Unexpected exceptions return `500 internal_error`
with a generic message and are logged with a stack trace.

## Validation rules

- Extension allow-list (case-insensitive): `mp3, wav, m4a, aac, ogg, oga, opus, flac, webm, mp4, mov, mkv`.
- Stream the upload to disk in 1 MB chunks; abort and delete the partial file as soon as `MAX_UPLOAD_MB` is exceeded.
- Zero bytes → `empty_file`.
- Run `ffprobe` (in a thread, never blocking the event loop) to confirm at least one audio stream and read the duration.
- Duration over `MAX_DURATION_SEC` → `media_too_long`.
- On any validation failure, delete the stored file.
- `ffprobe` access goes through a small `MediaProbe` interface so API tests can fake it.

## Configuration

`app/config.py` uses `pydantic-settings`. Every value has a sensible default and is documented in `.env.example`.

| Variable | Default |
|---|---|
| `ENGINE` | `faster-whisper` (`fake` for tests/dev) |
| `WHISPER_MODEL` | `Systran/faster-whisper-small` |
| `WHISPER_DEVICE` | `cpu` |
| `WHISPER_COMPUTE_TYPE` | `int8` |
| `WHISPER_VAD` | `true` |
| `HF_HUB_OFFLINE` | `false` (`true` = load the cached model without network access) |
| `MAX_UPLOAD_MB` | `1024` (sized for video; duration is capped separately) |
| `MAX_DURATION_SEC` | `3600` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `UPLOAD_DIR` | `/data/uploads` |
| `JOB_TTL_HOURS` | `24` |
| `JOB_TIMEOUT_SEC` | `3600` |
| `LOG_LEVEL` | `INFO` |
| `LOG_FORMAT` | `text` (`json` optional) |

`.env` is git-ignored; `.env.example` is committed.

## Repository layout

```
app/
  main.py                  # app factory, routers, exception handlers, static files, request logging middleware
  config.py                # Settings
  logging_config.py
  api/
    routes_jobs.py
    routes_health.py
    schemas.py
    errors.py              # AppError + handlers
    deps.py                # dependency providers (settings, store, queue, probe)
  services/
    uploads.py             # streaming save + validation
    media_probe.py         # MediaProbe Protocol + FfprobeMediaProbe
  jobs/
    models.py              # JobStatus enum, Job model
    store.py               # JobStore (Redis)
    queue.py               # enqueue via RQ
    tasks.py               # run_transcription(job_id) executed by the worker
  engines/
    base.py                # TranscriptionEngine Protocol, Segment, TranscriptionResult
    faster_whisper_engine.py
    fake_engine.py
    factory.py
  formats/
    srt.py
  worker.py                # entrypoint: configure logging, load engine, run SimpleWorker
  static/
    index.html
    app.js
    styles.css
tests/
Dockerfile
docker-compose.yml
pyproject.toml
.env.example
README.md
NOTES.md
```

## Code standards

- Type hints everywhere; `mypy` clean (strict on `app/`, relaxed for third-party libs without stubs).
- `ruff check` and `ruff format --check` clean. Line length 100.
- No `print`. Use `logging.getLogger(__name__)`.
- Domain errors are raised as `AppError(code, message, status_code)` and converted by one handler.
- Business logic does not live in route handlers; handlers are thin.
- Blocking work (`ffprobe`, file I/O on large files, Redis calls if sync client) must not block the event loop:
  use `run_in_threadpool` or sync `def` endpoints deliberately, and say which in NOTES.md.
- Docstrings only where the "why" is not obvious from the code.

## Logging

- Format (text): `timestamp level logger message key=value ...`. JSON when `LOG_FORMAT=json`.
- Every log line about a job includes `job_id`. Request logging middleware logs `method path status duration_ms request_id`.
- Worker logs: job started (filename, size, media duration, model), progress milestones (25/50/75%), done (took_sec,
  real-time factor), failed (error code + stack trace via `logger.exception`).
- Never log transcript text, file contents or secrets.

## Testing

- Tests never load a real model and never need a running Redis: use `FakeEngine`, `fakeredis`, a fake `MediaProbe`,
  and FastAPI dependency overrides.
- Cover at least: valid upload → 202; unsupported type → 415; empty file → 400; too large → 413; invalid media → 422;
  too long → 422; unknown job → 404; transcript not ready → 409; full lifecycle with `FakeEngine` (queued → done with
  result); engine failure → `failed` with error code; media file deleted after the job; SRT formatting; health with
  Redis down → 503.
- One optional integration test with real `ffprobe`, skipped automatically if `ffprobe` is not installed.

## Docker

- One multi-stage `Dockerfile`: `runtime` stage (used by `api` and `worker` with different commands) and `dev` stage
  (adds dev dependencies, used by the `tests` service).
- Base `python:3.12-slim`, install `ffmpeg`, run as a non-root user.
- Order layers for cache: system packages → dependency install → copy source.
- `HF_HOME=/models` on the `models` volume so the model downloads once.
- Healthchecks: `redis` (`redis-cli ping`), `api` (`/health` via Python, no curl needed). `depends_on` with
  `condition: service_healthy`. `worker` has `restart: unless-stopped`.
- `tests` service under the `test` profile.

## Commands

```bash
docker compose up --build                      # run everything, UI at http://localhost:8000
docker compose --profile test run --rm tests   # tests in Docker
pip install -e ".[dev]"                        # local dev
pytest
ruff check . && ruff format --check . && mypy app
```

## UI guidelines

- One page. Before upload, show accepted formats, max size and max duration (from `/api/limits`).
- Drag-and-drop plus a file picker. Client-side checks for type and size before uploading.
- Optional language select (Auto by default).
- Clear stages: Uploading (with % from `XMLHttpRequest` upload progress) → Queued → Transcribing (with %) → Done.
- Errors shown in plain language mapped from `error.code`, with a way to try again.
- Result: transcript with timestamps, Copy button, download `.txt` and `.srt`, "Transcribe another file".
- Works on narrow screens, keyboard accessible, readable contrast, no external assets.

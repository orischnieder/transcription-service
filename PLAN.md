# PLAN.md

Milestones for the transcription service. Work on one milestone at a time (see `CLAUDE.md` → Working agreement).
Each milestone ends with clean checks, an updated `NOTES.md`, a short summary, and a stop.

## Milestone 1 – Skeleton and tooling

- [ ] `pyproject.toml`: project metadata, pinned dependencies, `[dev]` extras, ruff / mypy / pytest config.
- [ ] `.gitignore`, `.env.example`, empty `NOTES.md` with the decisions-log template.
- [ ] `app/config.py` (`Settings`), `app/logging_config.py` (text + JSON formats).
- [ ] `app/main.py` app factory, `AppError` + exception handlers, request logging middleware.
- [ ] `GET /health` checking Redis (`503` when unreachable).
- [ ] Multi-stage `Dockerfile` (runtime + dev, non-root user, ffmpeg).
- [ ] `docker-compose.yml` with `api`, `worker` (placeholder command that starts and idles cleanly), `redis`,
      `tests` profile, volumes, healthchecks.
- [ ] First tests: health ok, health with Redis down.

**Done when:** `docker compose up --build` starts all services healthy, `http://localhost:8000/health` returns ok,
`http://localhost:8000/docs` loads, tests and checks are clean.

## Milestone 2 – Transcription engine

- [ ] `engines/base.py`: `Segment`, `TranscriptionResult`, `TranscriptionEngine` Protocol
      (`transcribe(path, language | None, on_progress | None) -> TranscriptionResult`).
- [ ] `FasterWhisperEngine` (model id, device, compute type from Settings; progress callback from segments).
- [ ] `FakeEngine` (deterministic result, can be configured to fail, reports progress).
- [ ] `engines/factory.py` selecting by `ENGINE`.
- [ ] Small CLI script `scripts/benchmark.py` that transcribes a local file and prints model load time,
      transcription time and real-time factor.
- [ ] Run the benchmark inside the worker image on a short sample with `Systran/faster-whisper-base` and
      `Systran/faster-whisper-small`; record numbers and the chosen default in `NOTES.md`.
- [ ] Unit tests for the factory and `FakeEngine`.

**Done when:** benchmark numbers are in `NOTES.md` and the default model is decided by Ori.

## Milestone 3 – Upload and validation (API only)

- [ ] `MediaProbe` Protocol + `FfprobeMediaProbe` (runs in a thread).
- [ ] `services/uploads.py`: streaming save with size limit, empty check, extension allow-list, probe, duration limit,
      cleanup on failure.
- [ ] `JobStore` in Redis with TTL; `JobStatus` enum; Pydantic schemas.
- [ ] `POST /api/jobs` creates a job in `queued` state (no enqueue yet), returns `202` + `Location`.
- [ ] `GET /api/jobs/{id}`, `GET /api/limits`.
- [ ] Tests for every validation error and for unknown job.

**Done when:** all validation cases return the right status code and error shape; tests clean.

## Milestone 4 – Queue, worker and job lifecycle

- [ ] `jobs/queue.py` enqueues to RQ queue `transcription` with `JOB_TIMEOUT_SEC`.
- [ ] `worker.py`: configure logging, build engine once via factory, run `SimpleWorker`.
- [ ] `jobs/tasks.py`: set `processing`, transcribe with throttled progress updates, store result, set `done`;
      on error set `failed` with code/message; always delete the media file.
- [ ] Download endpoints `.txt` and `.srt` (`409` when not ready); `formats/srt.py`.
- [ ] `503 queue_unavailable` when enqueue fails.
- [ ] Tests: full lifecycle with `FakeEngine` + fakeredis, failure path, file deleted, SRT formatting, 409.

**Done when:** uploading a real file through `/docs` ends in `done` with a transcript, and all tests pass.

## Milestone 5 – UI

- [ ] `app/static/index.html`, `app.js`, `styles.css` following the UI guidelines in `CLAUDE.md`.
- [ ] Served at `/` by FastAPI.
- [ ] Manual test checklist in `NOTES.md`: valid audio, valid video, wrong type, too large, empty, server error,
      narrow screen, keyboard only.

**Done when:** a non-technical user can upload a file and get a transcript without explanation.

## Milestone 6 – README and polish

- [ ] `README.md`: overview, screenshot/GIF placeholder, quick start (one command), configuration table,
      architecture diagram (Mermaid) and job lifecycle, API summary, how to run tests and checks, model choice and
      Hugging Face link, decisions and alternatives (from `NOTES.md`), assumptions, trade-offs and shortcuts,
      limitations, future work, how AI tools were used.
- [ ] Review all logs for usefulness; review error messages for clarity.
- [ ] Fresh-clone test: clone into a new folder and follow the README exactly.

**Done when:** a reviewer can go from `git clone` to a working transcript by following the README only.

## Milestone 7 – Bonus (only if time allows, one at a time)

- [ ] GitHub Actions: lint, type check, tests on push.
- [ ] Second engine using Hugging Face `transformers` pipeline (`openai/whisper-*`), selectable via `ENGINE`.
- [ ] Cancel a queued job.
- [ ] SSE for live progress instead of polling.
- [ ] Optional GPU profile in Compose.

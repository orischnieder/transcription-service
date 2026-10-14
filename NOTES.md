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
  `httpx` (required by FastAPI's `TestClient`), `fakeredis`, `ruff`, `mypy` (dev). `rq`, `faster-whisper` and
  `python-multipart` are added in the milestones that use them.
- Alternatives: `uvicorn[standard]` (uvloop/httptools; faster, but more native dependencies than this
  workload needs: the bottleneck is the model, not HTTP parsing). A lockfile tool (uv / pip-tools) for
  transitive pins.
- Consequences: direct dependencies are pinned, transitive ones are not, so a rebuild months later can
  pull newer sub-dependencies. In production I would add a lockfile.
- Open question: Starlette 1.8 warns that using `httpx` with its `TestClient` is deprecated in favour of
  `httpx2` (maintained by the Pydantic team). Tests pass with `httpx`; switching is a one-line change
  pending Ori's decision.

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

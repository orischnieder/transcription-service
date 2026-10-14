from typing import Annotated, Any

from fastapi import APIRouter, Form, Response, UploadFile

from app.api.deps import JobStoreDep, MediaProbeDep, SettingsDep
from app.api.errors import AppError
from app.api.schemas import ErrorResponse, LimitsResponse
from app.jobs.models import Job
from app.services.jobs import submit_job
from app.services.uploads import ALLOWED_EXTENSIONS

router = APIRouter(prefix="/api", tags=["jobs"])


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse} for code in status_codes}


@router.get("/limits")
def get_limits(settings: SettingsDep) -> LimitsResponse:
    return LimitsResponse(
        allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        max_upload_mb=settings.max_upload_mb,
        max_duration_sec=settings.max_duration_sec,
    )


# Sync `def` endpoints on purpose: copying the upload, ffprobe and the Redis client all block,
# so FastAPI runs these handlers in its threadpool instead of on the event loop.
@router.post("/jobs", status_code=202, responses=_error_responses(400, 413, 415, 422, 503))
def create_job(
    file: UploadFile,
    response: Response,
    settings: SettingsDep,
    store: JobStoreDep,
    probe: MediaProbeDep,
    language: Annotated[
        str | None, Form(description="Language code, e.g. 'en'. Empty = auto.")
    ] = None,
) -> Job:
    job = submit_job(file.file, file.filename, language, settings, store, probe)
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job


@router.get("/jobs/{job_id}", responses=_error_responses(404))
def get_job(job_id: str, store: JobStoreDep) -> Job:
    job = store.get(job_id)
    if job is None:
        raise AppError("job_not_found", "This job does not exist or has expired.", 404)
    return job

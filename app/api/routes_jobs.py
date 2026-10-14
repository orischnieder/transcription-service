from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Response, UploadFile

from app.api.deps import JobQueueDep, JobStoreDep, MediaProbeDep, SettingsDep
from app.api.schemas import ErrorResponse, JobResponse, LimitsResponse
from app.formats.srt import to_srt
from app.formats.txt import to_text
from app.jobs.models import Job, JobStatus
from app.services.jobs import get_finished_result, get_job, submit_job
from app.services.languages import SUPPORTED_LANGUAGES
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
        languages=sorted(SUPPORTED_LANGUAGES),
    )


# Sync `def` endpoints on purpose: copying the upload, ffprobe and the Redis client all block,
# so FastAPI runs these handlers in its threadpool instead of on the event loop.
@router.post("/jobs", status_code=202, responses=_error_responses(400, 413, 415, 422, 503))
def create_job(
    file: UploadFile,
    response: Response,
    settings: SettingsDep,
    store: JobStoreDep,
    queue: JobQueueDep,
    probe: MediaProbeDep,
    language: Annotated[
        str | None, Form(description="Language code, e.g. 'en'. Empty = auto.")
    ] = None,
) -> Job:
    job = submit_job(file.file, file.filename, language, settings, store, queue, probe)
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job


@router.get("/jobs/{job_id}", responses=_error_responses(404))
def read_job(job_id: str, store: JobStoreDep) -> JobResponse:
    job = get_job(store, job_id)
    result = store.get_result(job_id) if job.status == JobStatus.DONE else None
    return JobResponse(**job.model_dump(), result=result)


@router.get("/jobs/{job_id}/transcript.txt", responses=_error_responses(404, 409))
def download_txt(job_id: str, store: JobStoreDep) -> Response:
    job, result = get_finished_result(store, job_id)
    return _attachment(to_text(result.segments), job.filename, "txt", "text/plain; charset=utf-8")


@router.get("/jobs/{job_id}/transcript.srt", responses=_error_responses(404, 409))
def download_srt(job_id: str, store: JobStoreDep) -> Response:
    job, result = get_finished_result(store, job_id)
    return _attachment(
        to_srt(result.segments), job.filename, "srt", "application/x-subrip; charset=utf-8"
    )


def _attachment(content: str, source_filename: str, extension: str, media_type: str) -> Response:
    filename = f"{Path(source_filename).stem or 'transcript'}.{extension}"
    # ASCII fallback for old clients, plus the exact UTF-8 name (RFC 6266 / RFC 5987).
    ascii_name = "".join(c for c in filename if 32 <= ord(c) < 127 and c not in '"\\')
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})

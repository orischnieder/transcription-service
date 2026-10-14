import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.api.errors import AppError
from app.config import Settings
from app.services.media_probe import MediaProbe, MediaProbeError

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = frozenset(
    {"mp3", "wav", "m4a", "aac", "ogg", "oga", "opus", "flac", "webm", "mp4", "mov", "mkv"}
)
CHUNK_SIZE = 1024 * 1024
MAX_FILENAME_LENGTH = 255


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    filename: str
    size_bytes: int
    duration_sec: float


def save_upload(
    source: BinaryIO,
    filename: str | None,
    job_id: str,
    settings: Settings,
    probe: MediaProbe,
) -> StoredUpload:
    """Copy an upload to UPLOAD_DIR and validate it. Blocking: run it in a worker thread.

    The stored file is named after the job id, never the user's filename. Any validation
    failure deletes the stored file before the error is raised.
    """
    display_name = _display_name(filename)
    extension = _validated_extension(display_name)
    path = settings.upload_dir / f"{job_id}.{extension}"

    try:
        size = _copy_with_limit(source, path, settings.max_upload_mb * 1024 * 1024)
        if size == 0:
            raise AppError("empty_file", "The file is empty.", 400)
        duration = _probe_duration(probe, path, job_id)
        if duration > settings.max_duration_sec:
            limit_min = settings.max_duration_sec / 60
            raise AppError(
                "media_too_long",
                f"The audio or video is longer than the {limit_min:g} minute limit.",
                422,
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return StoredUpload(path=path, filename=display_name, size_bytes=size, duration_sec=duration)


def file_too_large_error(max_upload_mb: int) -> AppError:
    return AppError("file_too_large", f"The file is larger than the {max_upload_mb} MB limit.", 413)


def _display_name(filename: str | None) -> str:
    # Browsers send a bare name, but some clients send a full path (with / or \).
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:MAX_FILENAME_LENGTH]


def _validated_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise AppError(
            "unsupported_file_type",
            f"Please upload an audio or video file. Supported types: {allowed}.",
            415,
        )
    return extension


def _copy_with_limit(source: BinaryIO, destination: Path, max_bytes: int) -> int:
    size = 0
    with destination.open("wb") as out:
        while chunk := source.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise file_too_large_error(max_bytes // (1024 * 1024))
            out.write(chunk)
    return size


def _probe_duration(probe: MediaProbe, path: Path, job_id: str) -> float:
    try:
        return probe.probe(path).duration_sec
    except MediaProbeError as exc:
        logger.info("media rejected", extra={"job_id": job_id, "reason": str(exc)})
        raise AppError(
            "invalid_media",
            "We could not read this audio or video file. It may be damaged or have no sound track.",
            422,
        ) from exc

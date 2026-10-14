from datetime import datetime

from redis import Redis
from redis.typing import EncodableT, FieldT

from app.jobs.models import Job, JobResult, JobStatus


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def result_key(job_id: str) -> str:
    return f"job:{job_id}:result"


class JobStore:
    """Job state as one Redis hash per job (`job:{id}`) plus the result as JSON (`job:{id}:result`).

    Every write also resets the TTL, inside the same MULTI/EXEC transaction, so a key never exists
    without an expiry and a job is kept for JOB_TTL_HOURS after its last change.
    """

    def __init__(self, redis: Redis, ttl_sec: int) -> None:
        self._redis = redis
        self._ttl_sec = ttl_sec

    def create(self, job: Job) -> None:
        self._write(job.id, job.model_dump(mode="json"))

    def get(self, job_id: str) -> Job | None:
        data: dict[bytes, bytes] = self._redis.hgetall(job_key(job_id))  # type: ignore[assignment]
        if not data:
            return None
        return Job.model_validate({key.decode(): value.decode() for key, value in data.items()})

    def delete(self, job_id: str) -> None:
        self._redis.delete(job_key(job_id), result_key(job_id))

    def mark_processing(self, job_id: str, started_at: datetime) -> None:
        self._write(
            job_id, {"status": JobStatus.PROCESSING, "started_at": started_at, "progress": 0}
        )

    def set_progress(self, job_id: str, progress: int) -> None:
        self._write(job_id, {"progress": progress})

    def mark_done(self, job_id: str, result: JobResult, finished_at: datetime) -> None:
        fields = {
            "status": JobStatus.DONE,
            "progress": 100,
            "language_detected": result.language,
            "finished_at": finished_at,
        }
        self._write(job_id, fields, result=result)

    def mark_failed(
        self, job_id: str, error_code: str, error_message: str, finished_at: datetime
    ) -> None:
        fields = {
            "status": JobStatus.FAILED,
            "error_code": error_code,
            "error_message": error_message,
            "finished_at": finished_at,
        }
        self._write(job_id, fields)

    def get_result(self, job_id: str) -> JobResult | None:
        data: bytes | None = self._redis.get(result_key(job_id))  # type: ignore[assignment]
        return JobResult.model_validate_json(data) if data else None

    def _write(
        self, job_id: str, fields: dict[str, object], result: JobResult | None = None
    ) -> None:
        key = job_key(job_id)
        with self._redis.pipeline() as pipe:
            pipe.hset(key, mapping=_to_hash(fields))
            pipe.expire(key, self._ttl_sec)
            if result is not None:
                pipe.set(result_key(job_id), result.model_dump_json(), ex=self._ttl_sec)
            pipe.execute()


def _to_hash(fields: dict[str, object]) -> dict[FieldT, EncodableT]:
    # Redis hashes hold strings only; unset (None) fields are simply absent.
    return {key: _to_str(value) for key, value in fields.items() if value is not None}


def _to_str(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)

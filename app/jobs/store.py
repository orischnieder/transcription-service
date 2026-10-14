from redis import Redis
from redis.typing import EncodableT, FieldT

from app.jobs.models import Job


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


class JobStore:
    """Job state as one Redis hash per job (`job:{id}`), expiring after the configured TTL."""

    def __init__(self, redis: Redis, ttl_sec: int) -> None:
        self._redis = redis
        self._ttl_sec = ttl_sec

    def create(self, job: Job) -> None:
        key = job_key(job.id)
        # MULTI/EXEC so the hash never exists without its TTL.
        with self._redis.pipeline() as pipe:
            pipe.hset(key, mapping=_to_hash(job))
            pipe.expire(key, self._ttl_sec)
            pipe.execute()

    def get(self, job_id: str) -> Job | None:
        data: dict[bytes, bytes] = self._redis.hgetall(job_key(job_id))  # type: ignore[assignment]
        if not data:
            return None
        return Job.model_validate({key.decode(): value.decode() for key, value in data.items()})


def _to_hash(job: Job) -> dict[FieldT, EncodableT]:
    # Redis hashes hold strings only; unset (None) fields are simply absent.
    return {
        key: str(value) for key, value in job.model_dump(mode="json").items() if value is not None
    }

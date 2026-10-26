import threading
import time

ACTIVE_JOBS: dict[str, dict] = {}
ACTIVE_JOBS_LOCK = threading.Lock()


def set_active_job(
    session_id: str,
    target_version: int,
    status: str,
    message: str,
    progress: int = 0,
) -> None:
    with ACTIVE_JOBS_LOCK:
        now = time.time()
        prev = ACTIVE_JOBS.get(session_id, {})
        ACTIVE_JOBS[session_id] = {
            "target_version": target_version,
            "status": status,
            "message": message,
            "progress": progress,
            "started_at": prev.get("started_at", now),
            "last_updated": now,
        }


def clear_active_job(session_id: str) -> None:
    with ACTIVE_JOBS_LOCK:
        ACTIVE_JOBS.pop(session_id, None)


def get_active_job(session_id: str) -> dict | None:
    with ACTIVE_JOBS_LOCK:
        job = ACTIVE_JOBS.get(session_id)
        return dict(job) if job else None

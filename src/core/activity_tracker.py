"""Cross-Platform Activity Tracker & Idle Gating Subsystem.

Coordinates busy states (AI CAD generation, slicing, Bambu printing)
across processes using portable file locking (`filelock`).
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import time
from typing import Any

from filelock import FileLock

from src.core.config import ROOT_DIR

OUTPUTS_DIR = os.path.join(ROOT_DIR, "outputs")
ACTIVITY_FILE = os.path.join(OUTPUTS_DIR, ".system_activity.json")
LOCK_FILE = os.path.join(OUTPUTS_DIR, ".system_activity.lock")

# Ensure outputs directory exists
os.makedirs(OUTPUTS_DIR, exist_ok=True)


def _get_file_lock(lock_path: str = LOCK_FILE, timeout: float = 10.0) -> FileLock:
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    return FileLock(lock_path, timeout=timeout)


def set_busy_activity(
    activity_name: str,
    lease_seconds: float = 300.0,
    metadata: dict[str, Any] | None = None,
    activity_path: str = ACTIVITY_FILE,
    lock_path: str = LOCK_FILE,
) -> dict[str, Any]:
    """Record an active busy lease using portable file locking."""
    now_utc = datetime.now(timezone.utc)
    current_time = time.time()

    record: dict[str, Any] = {
        "activity_name": activity_name,
        "pid": os.getpid(),
        "start_timestamp": current_time,
        "lease_seconds": float(lease_seconds),
        "expires_at": current_time + float(lease_seconds),
        "utc_time": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": metadata or {},
    }

    try:
        lock = _get_file_lock(lock_path)
        with lock:
            os.makedirs(os.path.dirname(os.path.abspath(activity_path)), exist_ok=True)
            tmp_path = f"{activity_path}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            if os.path.exists(tmp_path):
                os.replace(tmp_path, activity_path)
    except OSError:
        pass

    return record


def clear_busy_activity(
    activity_path: str = ACTIVITY_FILE,
    lock_path: str = LOCK_FILE,
) -> None:
    """Clear the active busy lease."""
    try:
        lock = _get_file_lock(lock_path)
        with lock:
            if os.path.exists(activity_path):
                try:
                    os.remove(activity_path)
                except OSError:
                    pass
    except OSError:
        pass


@contextmanager
def busy_activity(
    activity_name: str,
    lease_seconds: float = 300.0,
    metadata: dict[str, Any] | None = None,
    activity_path: str = ACTIVITY_FILE,
    lock_path: str = LOCK_FILE,
):
    """Context manager for temporary operations that must prevent auto-updating or recycling."""
    set_busy_activity(
        activity_name=activity_name,
        lease_seconds=lease_seconds,
        metadata=metadata,
        activity_path=activity_path,
        lock_path=lock_path,
    )
    try:
        yield
    finally:
        clear_busy_activity(activity_path=activity_path, lock_path=lock_path)


def get_active_lease(
    activity_path: str = ACTIVITY_FILE,
    lock_path: str = LOCK_FILE,
    current_time: float | None = None,
) -> dict[str, Any] | None:
    """Check if a valid unexpired lease exists on disk."""
    if not os.path.exists(activity_path):
        return None

    now = time.time() if current_time is None else current_time
    lock = _get_file_lock(lock_path)
    try:
        with lock:
            if not os.path.exists(activity_path):
                return None
            with open(activity_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            expires_at = data.get("expires_at", 0.0)
            if now < expires_at:
                return data
            # Expired lease: remove it
            try:
                os.remove(activity_path)
            except OSError:
                pass
            return None
    except Exception:
        return None


def is_system_idle(
    check_printer: bool = True,
    bambu_client: Any = None,
    activity_path: str = ACTIVITY_FILE,
    lock_path: str = LOCK_FILE,
    current_time: float | None = None,
) -> tuple[bool, str]:
    """Check whether the entire system (AI CAD activity + physical Bambu printer) is idle.

    Returns: (is_idle: bool, reason: str)
    """
    lease = get_active_lease(
        activity_path=activity_path,
        lock_path=lock_path,
        current_time=current_time,
    )
    if lease:
        return False, f"Active job lease: {lease.get('activity_name', 'busy')}"

    if check_printer:
        try:
            from src.core.printer.bambu_client import is_dispatch_locked

            if is_dispatch_locked():
                return False, "Active print dispatch lock"
        except Exception:
            pass

        client = bambu_client
        if client is None:
            try:
                from src.web.backend.routes.printer import get_bambu_cloud

                client = get_bambu_cloud()
            except Exception:
                client = None

        if client and hasattr(client, "is_printer_busy"):
            try:
                is_busy, desc = client.is_printer_busy()
                if is_busy:
                    return False, desc
            except Exception:
                # If printer communication fails, do not block system idle unless desired
                pass

    return True, "System is idle"

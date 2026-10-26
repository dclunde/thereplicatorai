"""Dedicated Lifecycle & Logging Subsystem for Text-to-Printer Supervisor.

Handles rotating per-service loggers, structured JSONL lifecycle event journaling,
crash stderr capture, and crash-loop circuit breaker tracking.
"""

from collections import deque
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import subprocess
import threading
from typing import Any

from src.core.config import ROOT_DIR

LOGS_DIR = os.path.join(ROOT_DIR, "outputs", "logs")
SERVICES_LOGS_DIR = os.path.join(LOGS_DIR, "services")
LIFECYCLE_JOURNAL_PATH = os.path.join(LOGS_DIR, "lifecycle_events.jsonl")

# Ensure log directories exist
os.makedirs(SERVICES_LOGS_DIR, exist_ok=True)

_journal_lock = threading.Lock()
_service_loggers: dict[str, logging.Logger] = {}


def get_current_git_commit() -> str:
    """Return the current HEAD git commit hash (short), or 'unknown'."""
    git_bin = shutil.which("git")
    if not git_bin:
        # Check standard Windows paths if not on PATH
        common_paths = [
            r"C:\Program Files\Git\cmd\git.exe",
            os.path.expandvars(
                r"%LOCALAPPDATA%\GitHubDesktop\app-3.6.7\resources\app\git\cmd\git.exe"
            ),
        ]
        for p in common_paths:
            if os.path.exists(p):
                git_bin = p
                break

    if not git_bin:
        return "unknown"

    try:
        res = subprocess.run(
            [git_bin, "rev-parse", "--short", "HEAD"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_service_logger(
    service_name: str,
    max_bytes: int = 10485760,
    backup_count: int = 5,
    logs_dir: str = SERVICES_LOGS_DIR,
) -> logging.Logger:
    """Return a logger with a RotatingFileHandler writing to outputs/logs/services/{service}.log."""
    cache_key = f"{service_name}:{logs_dir}"
    if cache_key in _service_loggers:
        return _service_loggers[cache_key]

    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, f"{service_name}.log")

    logger = logging.getLogger(f"supervisor.service.{service_name}.{abs(hash(logs_dir))}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Clear existing handlers to avoid duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        fmt="[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    _service_loggers[cache_key] = logger
    return logger


def log_lifecycle_event(
    event_type: str,
    service: str | None = None,
    pid: int | None = None,
    exit_code: int | None = None,
    reason: str | None = None,
    uptime_seconds: float | None = None,
    restart_count_total: int | None = None,
    restart_count_window: int | None = None,
    circuit_breaker_active: bool = False,
    git_commit: str | None = None,
    stderr_tail: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    journal_path: str = LIFECYCLE_JOURNAL_PATH,
) -> dict[str, Any]:
    """Atomically append a structured lifecycle event to the JSONL journal."""
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()

    record: dict[str, Any] = {
        "timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "service": service,
        "pid": pid,
        "exit_code": exit_code,
        "reason": reason or "",
        "uptime_seconds": round(uptime_seconds, 2) if uptime_seconds is not None else None,
        "restart_count_total": restart_count_total or 0,
        "restart_count_window": restart_count_window or 0,
        "circuit_breaker_active": circuit_breaker_active,
        "git_commit": git_commit or get_current_git_commit(),
        "stderr_tail": stderr_tail or [],
    }

    if metadata:
        record["metadata"] = metadata

    os.makedirs(os.path.dirname(os.path.abspath(journal_path)), exist_ok=True)
    line = json.dumps(record) + "\n"

    with _journal_lock:
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(line)

    return record


def read_lifecycle_events(
    service: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    journal_path: str = LIFECYCLE_JOURNAL_PATH,
) -> list[dict[str, Any]]:
    """Read and filter structured events from the lifecycle journal, latest first."""
    if not os.path.exists(journal_path):
        return []

    events: list[dict[str, Any]] = []
    with _journal_lock:
        with open(journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if service and data.get("service") != service:
                        continue
                    if event_type and data.get("event_type") != event_type:
                        continue
                    events.append(data)
                except Exception:
                    continue

    events.reverse()
    return events[:limit]


def read_service_log_tail(
    service_name: str,
    max_lines: int = 200,
    logs_dir: str = SERVICES_LOGS_DIR,
) -> list[str]:
    """Read the trailing N lines from outputs/logs/services/{service}.log."""
    log_file = os.path.join(logs_dir, f"{service_name}.log")
    if not os.path.exists(log_file):
        return []

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=max_lines)
            return [line.rstrip("\r\n") for line in lines]
    except Exception:
        return []


class CircuitBreaker:
    """Tracks per-service crash timestamps and trips if restart frequency is exceeded."""

    def __init__(
        self,
        max_restarts: int = 5,
        window_seconds: int = 180,
        backoff_initial_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
    ):
        self.max_restarts = max_restarts
        self.window_seconds = window_seconds
        self.backoff_initial_seconds = backoff_initial_seconds
        self.backoff_max_seconds = backoff_max_seconds

        self._lock = threading.Lock()
        # service -> list of restart timestamps (float)
        self._restarts: dict[str, list[float]] = {}
        # service -> total restart count (lifetime)
        self._total_restarts: dict[str, int] = {}
        # service -> consecutive failure count (for exponential backoff)
        self._consecutive_failures: dict[str, int] = {}
        # service -> is tripped
        self._tripped: dict[str, bool] = {}

    def record_crash(self, service: str, current_time: float) -> tuple[bool, float, int, int]:
        """Record a crash for a service.

        Returns: (is_tripped, backoff_seconds, restart_count_window, restart_count_total)
        """
        with self._lock:
            if service not in self._restarts:
                self._restarts[service] = []
                self._total_restarts[service] = 0
                self._consecutive_failures[service] = 0
                self._tripped[service] = False

            self._total_restarts[service] += 1
            self._consecutive_failures[service] += 1
            self._restarts[service].append(current_time)

            # Prune older than rolling window
            cutoff = current_time - self.window_seconds
            self._restarts[service] = [t for t in self._restarts[service] if t >= cutoff]

            window_count = len(self._restarts[service])
            total_count = self._total_restarts[service]

            # Calculate backoff
            failures = self._consecutive_failures[service]
            backoff = min(
                self.backoff_initial_seconds * (2 ** max(0, failures - 1)),
                self.backoff_max_seconds,
            )

            # Check if tripped
            if window_count >= self.max_restarts:
                self._tripped[service] = True
                return True, backoff, window_count, total_count

            return self._tripped[service], backoff, window_count, total_count

    def record_success(self, service: str) -> None:
        """Reset consecutive failures after a period of stable running."""
        with self._lock:
            if service in self._consecutive_failures:
                self._consecutive_failures[service] = 0

    def is_tripped(self, service: str) -> bool:
        with self._lock:
            return self._tripped.get(service, False)

    def reset(self, service: str) -> None:
        """Manually reset the circuit breaker for a service."""
        with self._lock:
            self._tripped[service] = False
            self._restarts[service] = []
            self._consecutive_failures[service] = 0

    def get_status(self, service: str, current_time: float) -> dict[str, Any]:
        with self._lock:
            restarts = self._restarts.get(service, [])
            cutoff = current_time - self.window_seconds
            active_in_window = len([t for t in restarts if t >= cutoff])
            return {
                "tripped": self._tripped.get(service, False),
                "restart_count_window": active_in_window,
                "restart_count_total": self._total_restarts.get(service, 0),
                "consecutive_failures": self._consecutive_failures.get(service, 0),
            }

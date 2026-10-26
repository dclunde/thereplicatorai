"""Cross-Platform Multi-Service Supervisor.

Orchestrates the lifecycle of FastAPI backend, Vite frontend, Telegram bot,
and Google Tasks bot across Linux, macOS, and Windows with process tree cleanup,
watchdog auto-restart, circuit breaker crash-loop protection, and multiplexed logging.
"""

from collections import deque
from datetime import datetime
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import psutil

from src.core.config import ROOT_DIR
from src.core.config_loader import load_services_config
from src.core.supervisor_logging import (
    CircuitBreaker,
    get_service_logger,
    log_lifecycle_event,
)

_global_supervisor: Any = None


def get_global_supervisor():
    return _global_supervisor


def set_global_supervisor(sup):
    global _global_supervisor
    _global_supervisor = sup


class ManagedService:
    """Represents a single supervised process."""

    def __init__(self, name: str, command: list[str], cwd: str, env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.env = env
        self.popen: subprocess.Popen | None = None
        self.pid: int | None = None
        self.start_time: float | None = None
        self.status: str = "STOPPED"  # STOPPED, RUNNING, BACKOFF, TRIPPED, CRASHED
        self.exit_code: int | None = None
        self.next_restart_time: float = 0.0
        self.stderr_tail: deque[str] = deque(maxlen=30)
        self.reader_threads: list[threading.Thread] = []


class ProcessSupervisor:
    """Master supervisor managing all child services."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_services_config()
        self.services_cfg = self.config.get("services", {})
        self.frontend_mode = self.config.get("online_frontend_mode", "dev")

        log_cfg = self.config.get("logging", {})
        self.circuit_breaker = CircuitBreaker(
            max_restarts=log_cfg.get("circuit_breaker_max_restarts", 5),
            window_seconds=log_cfg.get("circuit_breaker_window_seconds", 180),
            backoff_initial_seconds=log_cfg.get("backoff_initial_seconds", 2.0),
            backoff_max_seconds=log_cfg.get("backoff_max_seconds", 60.0),
        )

        self.services: dict[str, ManagedService] = {}
        self._init_service_definitions()

        self._stopping = False
        self._lock = threading.RLock()
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop_event = threading.Event()

    def _init_service_definitions(self) -> None:
        """Define commands and working directories for all known services."""
        # Backend (FastAPI / Uvicorn)
        backend_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "src.web.backend.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
        self.services["online_backend"] = ManagedService(
            name="online_backend",
            command=backend_cmd,
            cwd=ROOT_DIR,
        )

        # Frontend (Vite dev mode; if production, static files are mounted on backend)
        frontend_dir = os.path.join(ROOT_DIR, "src", "web", "frontend")
        npm_bin = "npm.cmd" if sys.platform == "win32" else "npm"
        node_env = os.path.join(ROOT_DIR, "outputs", ".cache", "node_env")
        scripts_dir = (
            os.path.join(node_env, "Scripts")
            if sys.platform == "win32"
            else os.path.join(node_env, "bin")
        )
        frontend_env = None
        if os.path.exists(scripts_dir):
            frontend_env = {
                "PATH": f"{scripts_dir}{os.pathsep}{node_env}{os.pathsep}{os.environ.get('PATH', '')}"
            }
        frontend_cmd = [npm_bin, "run", "dev"]
        self.services["online_frontend"] = ManagedService(
            name="online_frontend",
            command=frontend_cmd,
            cwd=frontend_dir,
            env=frontend_env,
        )

        # Telegram Bot
        tg_cmd = [sys.executable, "-m", "src.bots.telegram.bot"]
        self.services["telegram_bot"] = ManagedService(
            name="telegram_bot",
            command=tg_cmd,
            cwd=ROOT_DIR,
        )

        # Google Tasks Bot
        tasks_cmd = [sys.executable, "-m", "src.bots.google_tasks.bot"]
        self.services["google_tasks_bot"] = ManagedService(
            name="google_tasks_bot",
            command=tasks_cmd,
            cwd=ROOT_DIR,
        )

    def _pipe_reader(
        self,
        pipe,
        service_name: str,
        is_stderr: bool,
        tail_buffer: deque[str],
    ) -> None:
        """Stream process output lines to rotating file logger and formatted console."""
        logger = get_service_logger(service_name)
        prefix = f"[{service_name.upper():<14}]"

        for line in iter(pipe.readline, b""):
            try:
                decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                decoded = str(line)

            if not decoded:
                continue

            if is_stderr:
                tail_buffer.append(decoded)
                is_info_line = decoded.lstrip().startswith("INFO:")
                if is_info_line:
                    logger.info(decoded)
                else:
                    logger.warning(decoded)
            else:
                is_info_line = True
                logger.info(decoded)

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            tag = "ERR" if (is_stderr and not is_info_line) else "   "
            print(f"[{now_str}] {prefix} {tag} {decoded}", flush=True)

        pipe.close()

    def start_service(self, name: str) -> bool:
        """Spawn a managed service process."""
        with self._lock:
            if name not in self.services:
                return False

            svc = self.services[name]

            # Don't spawn frontend if configured in production mode (static build)
            if name == "online_frontend" and self.frontend_mode == "prod":
                svc.status = "STATIC_PROD"
                return True

            if svc.popen is not None and svc.popen.poll() is None:
                return True  # Already running

            creationflags = 0
            start_new_session = False
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                start_new_session = True

            env = os.environ.copy()
            if svc.env:
                env.update(svc.env)
            env["PYTHONUNBUFFERED"] = "1"

            try:
                proc = subprocess.Popen(
                    svc.command,
                    cwd=svc.cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags,
                    start_new_session=start_new_session,
                )
                svc.popen = proc
                svc.pid = proc.pid
                svc.start_time = time.time()
                svc.status = "RUNNING"
                svc.exit_code = None

                # Spawn pipe readers
                t_out = threading.Thread(
                    target=self._pipe_reader,
                    args=(proc.stdout, name, False, svc.stderr_tail),
                    daemon=True,
                )
                t_err = threading.Thread(
                    target=self._pipe_reader,
                    args=(proc.stderr, name, True, svc.stderr_tail),
                    daemon=True,
                )
                svc.reader_threads = [t_out, t_err]
                t_out.start()
                t_err.start()

                log_lifecycle_event(
                    event_type="SERVICE_START",
                    service=name,
                    pid=svc.pid,
                    reason="Supervisor spawned process",
                )
                return True
            except Exception as e:
                svc.status = "CRASHED"
                svc.popen = None
                svc.pid = None
                log_lifecycle_event(
                    event_type="SERVICE_CRASH",
                    service=name,
                    reason=f"Failed to spawn service: {e}",
                )
                return False

    def stop_service(self, name: str, timeout: float = 10.0) -> bool:
        """Terminate a managed service and its entire process tree."""
        with self._lock:
            if name not in self.services:
                return False
            svc = self.services[name]
            proc = svc.popen
            if proc is None:
                svc.status = "STOPPED"
                return True

            pid = svc.pid
            self._terminate_process_tree(proc, pid=pid, timeout=timeout)
            svc.popen = None
            svc.pid = None
            svc.status = "STOPPED"

            uptime = time.time() - svc.start_time if svc.start_time else 0.0
            log_lifecycle_event(
                event_type="SERVICE_EXIT_OK",
                service=name,
                pid=pid,
                reason="Service stopped gracefully by supervisor",
                uptime_seconds=uptime,
            )
            return True

    def _terminate_process_tree(
        self,
        proc: subprocess.Popen,
        pid: int | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Recursively terminate process tree with graceful SIGTERM escalation to SIGKILL."""
        target_pid = pid or (proc.pid if proc else None)
        if not target_pid:
            return

        try:
            parent = psutil.Process(target_pid)
            children = parent.children(recursive=True)
            all_procs = children + [parent]

            # Phase 1: Graceful termination
            for p in all_procs:
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass

            gone, alive = psutil.wait_procs(all_procs, timeout=timeout)

            # Phase 2: Forceful kill for any unresponsive survivors
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass

        except (psutil.NoSuchProcess, Exception):
            # Fallback to direct popen termination
            try:
                proc.terminate()
                proc.wait(timeout=min(timeout, 2.0))
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def restart_service(self, name: str, timeout: float = 10.0) -> bool:
        """Stop and restart a specific managed service."""
        self.stop_service(name, timeout=timeout)
        time.sleep(0.5)
        return self.start_service(name)

    def start_configured_services(self) -> None:
        """Start all services marked true in configuration."""
        for name, enabled in self.services_cfg.items():
            if enabled and name in self.services:
                self.start_service(name)

    def stop_all(self, timeout: float = 10.0) -> None:
        """Stop all active services."""
        self._stopping = True
        log_lifecycle_event(
            event_type="SUPERVISOR_SHUTDOWN",
            reason="Supervisor stopping all child processes",
        )
        for name in list(self.services.keys()):
            self.stop_service(name, timeout=timeout)

    def restart_all(self, timeout: float = 15.0) -> None:
        """Cycle all active services gracefully."""
        active = [name for name, svc in self.services.items() if svc.status == "RUNNING"]
        for name in active:
            self.stop_service(name, timeout=timeout)
        time.sleep(1.0)
        for name in active:
            self.start_service(name)

    def watchdog_tick(self) -> None:
        """Check all child processes for crashes and handle restarts."""
        now = time.time()
        with self._lock:
            for name, svc in self.services.items():
                if self._stopping:
                    continue

                # Handle service currently in BACKOFF
                if svc.status == "BACKOFF":
                    if now >= svc.next_restart_time:
                        if not self.circuit_breaker.is_tripped(name):
                            svc.status = "STOPPED"
                            self.start_service(name)
                    continue

                if svc.status == "RUNNING" and svc.popen is not None:
                    ret = svc.popen.poll()
                    if ret is not None:
                        # Process exited
                        svc.exit_code = ret
                        uptime = (now - svc.start_time) if svc.start_time else 0.0
                        tail = list(svc.stderr_tail)

                        if ret == 0:
                            svc.status = "STOPPED"
                            log_lifecycle_event(
                                event_type="SERVICE_EXIT_OK",
                                service=name,
                                pid=svc.pid,
                                exit_code=ret,
                                uptime_seconds=uptime,
                                reason="Process exited with status 0",
                            )
                        else:
                            # Unexpected exit / crash
                            tripped, backoff, window_count, total_count = (
                                self.circuit_breaker.record_crash(name, now)
                            )
                            if tripped:
                                svc.status = "TRIPPED"
                                log_lifecycle_event(
                                    event_type="CIRCUIT_BREAKER_TRIPPED",
                                    service=name,
                                    pid=svc.pid,
                                    exit_code=ret,
                                    uptime_seconds=uptime,
                                    restart_count_window=window_count,
                                    restart_count_total=total_count,
                                    circuit_breaker_active=True,
                                    stderr_tail=tail,
                                    reason=f"Crash threshold exceeded ({window_count} crashes in window)",
                                )
                            else:
                                svc.status = "BACKOFF"
                                svc.next_restart_time = now + backoff
                                log_lifecycle_event(
                                    event_type="SERVICE_CRASH",
                                    service=name,
                                    pid=svc.pid,
                                    exit_code=ret,
                                    uptime_seconds=uptime,
                                    restart_count_window=window_count,
                                    restart_count_total=total_count,
                                    stderr_tail=tail,
                                    reason=f"Process crashed with exit code {ret}; restarting in {backoff:.1f}s",
                                )
                    else:
                        # Process is running; if it has survived 60s, reset consecutive failures
                        if svc.start_time and (now - svc.start_time) > 60.0:
                            self.circuit_breaker.record_success(name)

    def start_watchdog_thread(self) -> None:
        """Start watchdog thread in background."""
        self._watchdog_stop_event.clear()

        def _loop():
            while not self._watchdog_stop_event.is_set():
                try:
                    self.watchdog_tick()
                except Exception as e:
                    print(f"[watchdog] Error in tick: {e}", file=sys.stderr)
                time.sleep(1.0)

        self._watchdog_thread = threading.Thread(target=_loop, daemon=True)
        self._watchdog_thread.start()

    def stop_watchdog_thread(self) -> None:
        self._watchdog_stop_event.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=3.0)

    def get_status(self) -> dict[str, Any]:
        """Return diagnostic status of all services and circuit breakers."""
        now = time.time()
        results: dict[str, Any] = {}
        for name, svc in self.services.items():
            uptime = 0.0
            cpu_percent = 0.0
            memory_rss_bytes = 0
            if svc.pid and svc.status == "RUNNING":
                uptime = now - (svc.start_time or now)
                try:
                    p = psutil.Process(svc.pid)
                    cpu_percent = p.cpu_percent(interval=None)
                    memory_rss_bytes = p.memory_info().rss
                except Exception:
                    pass

            cb_stat = self.circuit_breaker.get_status(name, now)
            results[name] = {
                "name": name,
                "status": svc.status,
                "pid": svc.pid,
                "uptime_seconds": round(uptime, 1),
                "cpu_percent": cpu_percent,
                "memory_rss_bytes": memory_rss_bytes,
                "circuit_breaker": cb_stat,
                "exit_code": svc.exit_code,
            }
        return results

    def run_forever(self) -> None:
        """Run supervisor in foreground with signal handlers."""
        set_global_supervisor(self)
        log_lifecycle_event(
            event_type="SUPERVISOR_BOOT",
            reason="Supervisor initialized in foreground",
            metadata={"services_config": self.services_cfg},
        )

        def _sig_handler(signum, frame):
            print(f"\n[supervisor] Received signal {signum}. Initiating graceful shutdown...")
            self.stop_all(timeout=10.0)
            sys.exit(0)

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
        if sys.platform != "win32":
            try:
                signal.signal(signal.SIGHUP, _sig_handler)
            except AttributeError:
                pass

        self.start_configured_services()
        self.start_watchdog_thread()

        try:
            while not self._stopping:
                time.sleep(1.0)
        except (KeyboardInterrupt, SystemExit):
            self.stop_all(timeout=10.0)

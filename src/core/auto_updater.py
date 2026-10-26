"""Cross-Platform Idle-Gated Background Auto-Updater.

Fetches updates from git remote, gates updates on idle state (no active CAD generation
and no Bambu printing), performs fast-forward pulls, runs dependency synchronization,
rebuilds frontend assets if needed, and gracefully cycles child processes.
"""

from datetime import datetime
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from src.core.activity_tracker import is_system_idle
from src.core.config import ROOT_DIR
from src.core.config_loader import load_services_config
from src.core.supervisor_logging import log_lifecycle_event


def resolve_git_binary() -> str | None:
    """Find git binary on system path, from environment, or fallback locations."""
    env_git = os.environ.get("GIT_PATH")
    if env_git and os.path.exists(env_git):
        return env_git

    git_bin = shutil.which("git")
    if git_bin:
        return git_bin

    if sys.platform == "win32":
        fallbacks = [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
            os.path.expandvars(
                r"%LOCALAPPDATA%\GitHubDesktop\app-3.6.7\resources\app\git\cmd\git.exe"
            ),
            os.path.expandvars(
                r"%LOCALAPPDATA%\GitHubDesktop\app-3.6.6\resources\app\git\cmd\git.exe"
            ),
        ]
        for p in fallbacks:
            if os.path.exists(p):
                return p

    return None


def is_in_maintenance_window(
    current_hhmm: str,
    window_start: str,
    window_end: str,
) -> bool:
    """Return True if current_hhmm ('HH:MM') falls within window_start and window_end."""
    if not window_start or not window_end or window_start == window_end:
        return True

    try:
        cur = datetime.strptime(current_hhmm, "%H:%M").time()
        start = datetime.strptime(window_start, "%H:%M").time()
        end = datetime.strptime(window_end, "%H:%M").time()
    except Exception:
        return True

    if start <= end:
        return start <= cur <= end
    # Crosses midnight (e.g., 23:00 to 04:00)
    return cur >= start or cur <= end


class AutoUpdater:
    """Manages git synchronization, dependency syncing, and service recycling."""

    def __init__(self, config: dict[str, Any] | None = None, supervisor: Any = None):
        self.config = config or load_services_config()
        self.updater_cfg = self.config.get("auto_updater", {})
        self.supervisor = supervisor

        self.enabled = bool(self.updater_cfg.get("enabled", True))
        self.branch = str(self.updater_cfg.get("branch", "main"))
        self.window_start = str(self.updater_cfg.get("window_start", "04:00"))
        self.window_end = str(self.updater_cfg.get("window_end", "05:00"))
        self.check_interval = int(self.updater_cfg.get("check_interval_seconds", 300))
        self.busy_retry_interval = int(self.updater_cfg.get("busy_retry_interval_seconds", 600))
        self.check_printer_busy = bool(self.updater_cfg.get("check_printer_busy", True))

        self.git_bin = resolve_git_binary()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_git(self, args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
        """Run a git command in ROOT_DIR."""
        if not self.git_bin:
            return 1, "", "Git binary not found"
        try:
            res = subprocess.run(
                [self.git_bin] + args,
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except Exception as e:
            return 1, "", str(e)

    def is_dirty(self) -> bool:
        """Check if git working tree has untracked or modified files."""
        code, out, _ = self.run_git(["status", "--porcelain"])
        if code != 0:
            return False
        return bool(out.strip())

    def check_for_updates(self) -> tuple[bool, str, str]:
        """Fetch remote branch and determine if updates are available.

        Returns: (has_update: bool, current_sha: str, remote_sha: str)
        """
        if not self.git_bin:
            return False, "", ""

        # Fetch
        code, _, err = self.run_git(["fetch", "origin", self.branch], timeout=60.0)
        if code != 0:
            return False, "", err

        # Compare HEAD vs origin/<branch>
        code_head, head_sha, _ = self.run_git(["rev-parse", "HEAD"])
        code_remote, remote_sha, _ = self.run_git(["rev-parse", f"origin/{self.branch}"])

        if code_head != 0 or code_remote != 0:
            return False, head_sha, remote_sha

        return (head_sha != remote_sha), head_sha, remote_sha

    def apply_update(self, force: bool = False) -> tuple[bool, str]:
        """Execute the deterministic update pipeline.

        Returns: (success: bool, message: str)
        """
        if not self.git_bin:
            return False, "Git binary not found on system"

        now_str = datetime.now().strftime("%H:%M")
        if not force and not is_in_maintenance_window(now_str, self.window_start, self.window_end):
            return False, f"Outside maintenance window ({self.window_start}-{self.window_end})"

        # Check working directory cleanliness
        if self.is_dirty():
            log_lifecycle_event(
                event_type="UPDATE_POSTPONED",
                reason="Dirty working directory",
            )
            return False, "Update postponed: Dirty working directory"

        has_update, old_sha, new_sha = self.check_for_updates()
        if not has_update:
            return False, "Already up to date"

        # Check idle state
        is_idle, reason = is_system_idle(check_printer=self.check_printer_busy)
        if not is_idle:
            log_lifecycle_event(
                event_type="UPDATE_POSTPONED",
                reason=reason,
            )
            return False, f"Update postponed: {reason}"

        # Fast-forward pull
        code, out, err = self.run_git(["pull", "--ff-only", "origin", self.branch])
        if code != 0:
            log_lifecycle_event(
                event_type="UPDATE_FAILED",
                reason=f"Fast-forward pull failed: {err}",
            )
            return False, f"Fast-forward pull failed: {err}"

        # Inspect diff
        code_diff, diff_files, _ = self.run_git(["diff", f"{old_sha}..{new_sha}", "--name-only"])
        changed = set(diff_files.splitlines()) if code_diff == 0 else set()

        # Sync Python dependencies if pyproject changed
        if "pyproject.toml" in changed or "uv.lock" in changed:
            try:
                subprocess.run(["uv", "sync"], cwd=ROOT_DIR, check=False)
            except Exception:
                pass

        # Sync Frontend dependencies if package.json changed
        frontend_dir = os.path.join(ROOT_DIR, "src", "web", "frontend")
        if any("frontend/package" in f for f in changed) or any(
            f.startswith("src/web/frontend/src/") for f in changed
        ):
            try:
                from src.launcher import ensure_frontend_ready

                ensure_frontend_ready()
            except Exception:
                pass

        if any("frontend/package" in f for f in changed):
            try:
                npm_bin = "npm.cmd" if sys.platform == "win32" else "npm"
                subprocess.run(
                    [npm_bin, "install"],
                    cwd=frontend_dir,
                    env=os.environ.copy(),
                    check=False,
                )
            except Exception:
                pass

        # Build production frontend if frontend source changed
        if any(f.startswith("src/web/frontend/src/") for f in changed):
            try:
                npm_bin = "npm.cmd" if sys.platform == "win32" else "npm"
                subprocess.run(
                    [npm_bin, "run", "build"],
                    cwd=frontend_dir,
                    env=os.environ.copy(),
                    check=False,
                )
            except Exception:
                pass

        # Cycle supervised processes
        if self.supervisor:
            self.supervisor.restart_all(timeout=15.0)

        log_lifecycle_event(
            event_type="UPDATE_APPLIED",
            reason=f"Updated from {old_sha[:7]} to {new_sha[:7]}",
            git_commit=new_sha[:7],
            metadata={"changed_files": list(changed)[:50]},
        )
        return True, f"Successfully updated to {new_sha[:7]}"

    def start_background_loop(self) -> None:
        """Start the auto-updater background thread."""
        if not self.enabled or not self.git_bin:
            return

        self._stop_event.clear()

        def _loop():
            while not self._stop_event.is_set():
                try:
                    self.apply_update(force=False)
                except Exception as e:
                    print(f"[auto_updater] Exception in update loop: {e}", file=sys.stderr)
                time.sleep(self.check_interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop_background_loop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

import os
import sys
import pytest

import shutil
import time

# Mark environment as running pytest during test collection & execution
os.environ["PYTEST_CURRENT_TEST"] = "1"

# Ensure root directory is in sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _safe_remove(item_path: str):
    """Safely remove a file or directory with retries for Windows file locks."""
    for _ in range(5):
        if not os.path.exists(item_path):
            break
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            elif os.path.isfile(item_path):
                os.remove(item_path)
            break
        except Exception:
            time.sleep(0.1)


@pytest.fixture(autouse=True, scope="session")
def cleanup_test_session_created_stls():
    """Session-level fixture: clean up any leftover test model folders before and after the entire test suite."""
    dirs_to_watch = [
        os.path.join(ROOT_DIR, "outputs", "sessions"),
        os.path.join(ROOT_DIR, "created_stls"),
    ]

    def _purge_known_test_dirs():
        for d in dirs_to_watch:
            if os.path.exists(d):
                for item in os.listdir(d):
                    if item.startswith("uploaded_test_model_") or item == "test_session_print":
                        _safe_remove(os.path.join(d, item))

    _purge_known_test_dirs()
    try:
        yield
    finally:
        _purge_known_test_dirs()
        legacy_dir = os.path.join(ROOT_DIR, "created_stls")
        if os.path.exists(legacy_dir) and not os.listdir(legacy_dir):
            _safe_remove(legacy_dir)


@pytest.fixture(autouse=True, scope="function")
def cleanup_test_created_stls():
    """Function-level fixture: automatically tracks outputs/sessions (and created_stls) before and after each test function.
    Guarantees that any new files/directories created during a test run are deleted immediately after the test completes.
    """
    sessions_dir = os.path.join(ROOT_DIR, "outputs", "sessions")
    legacy_dir = os.path.join(ROOT_DIR, "created_stls")
    token_file = os.path.join(ROOT_DIR, "outputs", ".cache", "google_tasks_token.json")
    os.makedirs(sessions_dir, exist_ok=True)
    before_sessions = set(os.listdir(sessions_dir))
    had_legacy = os.path.exists(legacy_dir)
    before_legacy = set(os.listdir(legacy_dir)) if had_legacy else set()
    had_token = os.path.exists(token_file)
    before_token_bytes = open(token_file, "rb").read() if had_token else None

    try:
        yield
    finally:
        if os.path.exists(sessions_dir):
            after_sessions = set(os.listdir(sessions_dir))
            for item in after_sessions - before_sessions:
                _safe_remove(os.path.join(sessions_dir, item))
        if os.path.exists(legacy_dir):
            after_legacy = set(os.listdir(legacy_dir))
            for item in after_legacy - before_legacy:
                _safe_remove(os.path.join(legacy_dir, item))
            if not had_legacy and not os.listdir(legacy_dir):
                _safe_remove(legacy_dir)
        if not had_token and os.path.exists(token_file):
            _safe_remove(token_file)
        elif had_token and before_token_bytes is not None:
            try:
                with open(token_file, "wb") as f:
                    f.write(before_token_bytes)
            except Exception:
                pass


@pytest.fixture
def tmp_workspace(tmp_path):
    """Provides a temporary workspace directory for sessions."""
    workspace_dir = tmp_path / "created_stls"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return str(workspace_dir)


@pytest.fixture
def sample_stl(tmp_path):
    """Creates a valid ASCII STL cube file (20x20x20 mm)."""
    stl_path = tmp_path / "sample_cube.stl"
    stl_content = b"""solid cube
  facet normal 0 0 0
    outer loop
      vertex 0.0 0.0 0.0
      vertex 20.0 0.0 0.0
      vertex 0.0 20.0 0.0
    endloop
  endfacet
  facet normal 0 0 0
    outer loop
      vertex 20.0 20.0 20.0
      vertex 0.0 20.0 20.0
      vertex 20.0 0.0 20.0
    endloop
  endfacet
endsolid cube
"""
    stl_path.write_bytes(stl_content)
    return str(stl_path)

import json
import os
import sys
import time

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web.backend.jobs import (
    set_active_job,
    get_active_job,
    clear_active_job,
)
from src.web.backend.routes.sessions import (
    load_session,
    list_sessions,
    compile_openscad_generator,
    save_session,
)


def test_active_job_lifecycle():
    """Verify registration, retrieval, and clearing of active jobs."""
    session_id = "test-session-123"
    clear_active_job(session_id)

    assert get_active_job(session_id) is None

    set_active_job(
        session_id=session_id,
        target_version=2,
        status="step",
        message="Compiling OpenSCAD model...",
        progress=60,
    )

    job = get_active_job(session_id)
    assert job is not None
    assert job["target_version"] == 2
    assert job["status"] == "step"
    assert job["message"] == "Compiling OpenSCAD model..."
    assert job["progress"] == 60
    assert "started_at" in job
    assert "last_updated" in job

    # Update job
    set_active_job(
        session_id=session_id,
        target_version=2,
        status="step",
        message="Slicing geometry to 3MF...",
        progress=85,
    )
    updated = get_active_job(session_id)
    assert updated["message"] == "Slicing geometry to 3MF..."
    assert updated["progress"] == 85
    assert updated["started_at"] == job["started_at"]  # preserved

    clear_active_job(session_id)
    assert get_active_job(session_id) is None


def test_load_session_with_active_job(tmp_path, monkeypatch):
    """Verify load_session injects active_job and decorates version flags."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    session_id = "sess_concurrent_test"
    sess_dir = os.path.join(test_ws, session_id)
    v1_dir = os.path.join(sess_dir, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    # Create dummy v1 STL file
    with open(os.path.join(v1_dir, "design_v1.stl"), "w") as f:
        f.write("solid cube endsolid cube")

    session_data = {
        "id": session_id,
        "title": "Test Concurrent Model",
        "history": [
            {"role": "user", "text": "Make a cube"},
            {"role": "assistant", "code": "cube(10);"},
        ],
        "versions": 1,
        "latest_stl": f"/created_stls/{session_id}/v1/design_v1.stl",
        "last_updated": "2026-09-10T00:00:00",
    }
    save_session(session_id, session_data)

    # Register active job for v2 (simulating concurrent generation in progress)
    set_active_job(
        session_id=session_id,
        target_version=2,
        status="step",
        message="Rendering OpenSCAD to STL... (1m 12s)",
        progress=75,
    )

    try:
        loaded = load_session(session_id)
        assert "active_job" in loaded
        assert loaded["active_job"]["target_version"] == 2
        assert loaded["active_job"]["progress"] == 75

        # Check all_versions list
        versions = {v["version"]: v for v in loaded["all_versions"]}
        assert 1 in versions
        assert versions[1]["has_stl"] is True
        assert not versions[1].get("is_generating")

        assert 2 in versions
        assert versions[2]["is_generating"] is True
        assert versions[2]["isPending"] is True
        assert versions[2]["has_stl"] is False
        assert versions[2]["status_message"] == "Rendering OpenSCAD to STL... (1m 12s)"
        assert versions[2]["progress"] == 75
    finally:
        clear_active_job(session_id)

    # After clearing job, load_session should no longer report v2 as generating
    loaded_after = load_session(session_id)
    assert loaded_after.get("active_job") is None
    v_dict_after = {v["version"]: v for v in loaded_after["all_versions"]}
    assert not v_dict_after.get(2, {}).get("is_generating")


def test_list_sessions_reports_active_jobs(tmp_path, monkeypatch):
    """Verify list_sessions attaches active_job metadata to sessions."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # Create two sessions
    for sid in ["sess_idle", "sess_active"]:
        s_dir = os.path.join(test_ws, sid)
        os.makedirs(s_dir, exist_ok=True)
        save_session(
            sid,
            {
                "id": sid,
                "title": f"Title {sid}",
                "history": [],
                "versions": 1,
                "last_updated": "2026-09-10T01:00:00",
            },
        )

    set_active_job(
        session_id="sess_active",
        target_version=2,
        status="step",
        message="Designing & Compiling v2...",
        progress=40,
    )

    try:
        sessions = list_sessions()
        sess_map = {s["id"]: s for s in sessions}

        assert "sess_idle" in sess_map
        assert sess_map["sess_idle"]["active_job"] is None

        assert "sess_active" in sess_map
        assert sess_map["sess_active"]["active_job"] is not None
        assert sess_map["sess_active"]["active_job"]["target_version"] == 2
        assert sess_map["sess_active"]["active_job"]["progress"] == 40
    finally:
        clear_active_job("sess_active")


def test_compile_openscad_generator_heartbeats(tmp_path, monkeypatch):
    """Verify compile_openscad_generator yields progress and elapsed heartbeats."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr(
        "src.web.backend.routes.sessions.shared_slicer", lambda *args, **kwargs: None
    )

    session_id = "sess_heartbeat"

    # Mock Popen to simulate a process that polls twice before finishing
    class MockPopen:
        def __init__(self, *args, **kwargs):
            self.poll_count = 0
            self.returncode = 0
            self.args = args

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def poll(self):
            self.poll_count += 1
            if self.poll_count < 2:
                return None  # Still running
            return 0  # Finished

        def communicate(self):
            return ("", "")

    monkeypatch.setattr("subprocess.Popen", MockPopen)

    # Fast-forward time in loop by mocking time.sleep and time.time
    t0 = 1000.0
    current_time = [t0]

    def mock_time():
        return current_time[0]

    def mock_sleep(s):
        current_time[0] += 3.5  # exceed the 3.0s heartbeat threshold

    monkeypatch.setattr(time, "time", mock_time)
    monkeypatch.setattr(time, "sleep", mock_sleep)

    try:
        gen = compile_openscad_generator(
            scad_code="cube(10);",
            session_id=session_id,
            version=1,
        )
        steps = list(gen)
        # Should have initial step, at least one heartbeat step, slice step, and complete
        messages = [s.get("message") for s in steps if "message" in s]
        assert any("Rendering OpenSCAD to STL..." in m for m in messages)
        assert any("Slicing geometry to 3MF..." in m for m in messages)

        # Check that job status was registered during generation
        job = get_active_job(session_id)
        assert job is not None
        assert job["target_version"] == 1
    finally:
        clear_active_job(session_id)


def test_rerender_session_streaming_heartbeats(tmp_path, monkeypatch):
    """Verify POST /sessions/{id}/versions/{v}/rerender streams valid string chunks during heartbeats."""
    from fastapi.testclient import TestClient
    from src.web.backend.app import app

    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    session_id = "sess_rerender_stream"
    v1_dir = os.path.join(test_ws, session_id, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(v1_dir, "design_v1.scad"), "w") as f:
        f.write("cube(10);")

    session_data = {
        "id": session_id,
        "title": "Rerender Test",
        "history": [
            {"role": "user", "text": "Make a cube"},
            {"role": "assistant", "code": "cube(10);"},
        ],
        "versions": 1,
        "last_updated": "2026-09-10T00:00:00",
    }
    save_session(session_id, session_data)

    class MockPopen:
        def __init__(self, *args, **kwargs):
            self.poll_count = 0
            self.returncode = 0
            self.args = args

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def poll(self):
            self.poll_count += 1
            if self.poll_count < 2:
                return None
            return 0

        def communicate(self):
            return ("", "")

    monkeypatch.setattr("subprocess.Popen", MockPopen)

    t0 = 2000.0
    current_time = [t0]

    def mock_time():
        return current_time[0]

    def mock_sleep(s):
        current_time[0] += 3.5

    monkeypatch.setattr(time, "time", mock_time)
    monkeypatch.setattr(time, "sleep", mock_sleep)

    client = TestClient(app)
    response = client.post(f"/sessions/{session_id}/versions/1/rerender")
    assert response.status_code == 200

    # Every streamed line must be valid JSON string
    lines = [line.strip() for line in response.text.split("\n") if line.strip()]
    assert len(lines) >= 3
    parsed = [json.loads(line) for line in lines]
    messages = [p.get("message") for p in parsed if "message" in p]
    assert any("Rendering OpenSCAD to STL..." in m for m in messages)
    assert any(p.get("status") == "complete" for p in parsed)


def test_load_session_normalizes_zero_versions(tmp_path, monkeypatch):
    """Verify load_session normalizes versions to 1 if v1 exists on disk/history even if session.json had 0."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    session_id = "sess_zero_v"
    v1_dir = os.path.join(test_ws, session_id, "v1")
    os.makedirs(v1_dir, exist_ok=True)
    with open(os.path.join(v1_dir, "design_v1.stl"), "w") as f:
        f.write("solid cube endsolid cube")

    session_data = {
        "id": session_id,
        "title": "Zero Version Test",
        "history": [
            {"role": "user", "text": "Make a cube"},
            {"role": "assistant", "code": "cube(10);"},
        ],
        "versions": 0,
        "latest_stl": f"/created_stls/{session_id}/v1/design_v1.stl",
        "last_updated": "2026-09-10T00:00:00",
    }
    save_session(session_id, session_data)

    loaded = load_session(session_id)
    assert loaded["versions"] == 1
    assert len(loaded["all_versions"]) == 1
    assert loaded["all_versions"][0]["version"] == 1


def test_edit_session_increments_past_zero_versions(tmp_path, monkeypatch):
    """Verify edit_session targets version 2 even if session.json initially had versions: 0."""
    from fastapi.testclient import TestClient
    from src.web.backend.app import app

    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    session_id = "sess_zero_v_edit"
    v1_dir = os.path.join(test_ws, session_id, "v1")
    os.makedirs(v1_dir, exist_ok=True)
    with open(os.path.join(v1_dir, "design_v1.stl"), "w") as f:
        f.write("solid cube endsolid cube")

    session_data = {
        "id": session_id,
        "title": "Zero Version Edit Test",
        "history": [
            {"role": "user", "text": "Make a cube"},
            {"role": "assistant", "code": "cube(10);"},
        ],
        "versions": 0,
        "latest_stl": f"/created_stls/{session_id}/v1/design_v1.stl",
        "last_updated": "2026-09-10T00:00:00",
    }
    save_session(session_id, session_data)

    class FastMockPopen:
        def __init__(self, args, **kwargs):
            self.returncode = 0
            self.args = args
            if "-o" in args:
                out_idx = args.index("-o") + 1
                if out_idx < len(args):
                    with open(args[out_idx], "w") as f:
                        f.write("solid cube endsolid cube")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def poll(self):
            return 0

        def communicate(self):
            return ("", "")

    monkeypatch.setattr("subprocess.Popen", FastMockPopen)
    monkeypatch.setattr("src.web.backend.routes.sessions.shared_slicer", lambda *a, **kw: None)

    client = TestClient(app)
    # Use "practice test" to trigger mock generation
    response = client.post(
        f"/sessions/{session_id}/edit",
        json={"prompt": "practice test"},
    )
    assert response.status_code == 200

    loaded = load_session(session_id)
    assert loaded["versions"] == 2
    version_nums = [v["version"] for v in loaded["all_versions"]]
    assert 1 in version_nums
    assert 2 in version_nums

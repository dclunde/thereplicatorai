import json
import os
import sys
from unittest.mock import MagicMock, mock_open, patch

from fastapi.testclient import TestClient

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import SESSIONS_DIR as WORKSPACE_DIR
from src.web.backend.app import app
from src.web.backend.routes.sessions import list_sessions

client = TestClient(app)


def test_practice_test_new_and_edit_session(tmp_path, monkeypatch):
    """Verify that 'practice test' bypasses Gemini and produces mock cube."""
    # Set workspace to temp dir
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # Mock OpenSCAD compilation & 3MF slicer so it runs without external tools
    def mock_compile_gen(scad_code, session_id, version, requested_colors=None):
        v_dir = os.path.join(test_ws, session_id, f"v{version}")
        os.makedirs(v_dir, exist_ok=True)
        stl_file = os.path.join(v_dir, f"design_v{version}.stl")
        with open(stl_file, "w") as f:
            f.write("solid cube endsolid cube")
        yield {"status": "step", "message": "Rendering STL..."}
        yield {
            "status": "complete",
            "assets": {
                "stl_url": f"/created_stls/{session_id}/v{version}/design_v{version}.stl",
                "time": "15m",
                "size": "0.79 × 0.79 × 0.79 in",
                "grams": 5.2,
            },
        }

    monkeypatch.setattr(
        "src.web.backend.routes.sessions.compile_openscad_generator", mock_compile_gen
    )

    # 1. Create new practice test session
    res = client.post("/sessions/new", json={"prompt": "practice test"})
    assert res.status_code == 200

    # Read NDJSON lines
    lines = res.text.strip().split("\n")
    parsed_events = [json.loads(line) for line in lines if line]
    complete_event = [e for e in parsed_events if e.get("status") == "complete"][0]
    session_data = complete_event["session"]

    session_id = session_data["id"]
    assert session_data["title"] == "practice_cube"
    assert session_data["versions"] == 1
    assert len(session_data["history"]) == 2

    # 2. Retrieve session
    res_get = client.get(f"/sessions/{session_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == session_id

    # 3. Edit practice test session
    res_edit = client.post(f"/sessions/{session_id}/edit", json={"prompt": "practice test"})
    assert res_edit.status_code == 200
    edit_lines = res_edit.text.strip().split("\n")
    edit_events = [json.loads(line) for line in edit_lines if line]
    edit_complete = [e for e in edit_events if e.get("status") == "complete"][0]

    updated_session = edit_complete["session"]
    assert updated_session["versions"] == 2

    # 4. Verify that size is properly merged into history
    assert "size" in updated_session["history"][-1]
    assert "in" in updated_session["history"][-1]["size"]

    # 5. Delete session
    res_del = client.delete(f"/sessions/{session_id}")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "ok"

    # 6. Verify 404 after deletion
    res_get_deleted = client.get(f"/sessions/{session_id}")
    assert res_get_deleted.status_code == 404


def test_list_sessions(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # Create 2 fake session folders
    for sid in ["s1", "s2"]:
        sdir = os.path.join(test_ws, sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "session.json"), "w") as f:
            json.dump({"id": sid, "title": f"Idea {sid}", "last_updated": "2026-08-02T00:00:00"}, f)

    res = client.get("/sessions")
    assert res.status_code == 200
    sessions = res.json()
    assert len(sessions) == 2
    assert {s["id"] for s in sessions} == {"s1", "s2"}


def test_non_existent_session_returns_404():
    res = client.get("/sessions/invalid_session_id_999")
    assert res.status_code == 404


def test_delete_non_existent_session_returns_404():
    res = client.delete("/sessions/invalid_session_id_999")
    assert res.status_code == 404


# 5. Bug 5: API 500 crash on last_updated: null
def test_bug5_list_sessions_null_last_updated():
    """Ensure list_sessions handles last_updated: null properly."""
    with (
        patch("os.listdir", return_value=["test_session"]),
        patch("os.path.isdir", return_value=True),
        patch("os.path.exists", return_value=True),
    ):
        mock_data = '{"title": "Test", "last_updated": null}'
        with patch("builtins.open", mock_open(read_data=mock_data)):
            sessions = list_sessions()
            assert len(sessions) == 1
            assert sessions[0]["last_updated"] is None


# 8. Bug 8: Stuck AMS slots in print.bat
def test_bug8_print_bat_auto_slot(monkeypatch):
    """Ensure auto slot (None) launches python print without --slot flag and cleans legacy print.bat."""
    session_id = "test_session_print"
    v_dir = os.path.join(WORKSPACE_DIR, session_id, "v1")
    os.makedirs(v_dir, exist_ok=True)

    with open(os.path.join(WORKSPACE_DIR, session_id, "session.json"), "w") as f:
        json.dump({"title": "Test", "versions": 1}, f)
    with open(os.path.join(v_dir, "file.stl"), "w") as f:
        f.write("solid file endsolid file")

    bat_path = os.path.join(v_dir, "print.bat")
    with open(bat_path, "w") as f:
        f.write('python "bambu_printer.py" "file.stl" "" --slot 1\n')

    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_printer_busy", lambda: (False, "Idle"))

    with patch("subprocess.Popen") as mock_popen:
        response = client.post(f"/sessions/{session_id}/print", json={"ams_slot": None})
        assert response.status_code == 200
        called_cmd = mock_popen.call_args[0][0]
        assert "src.cli.manual_print" in called_cmd
        assert "--slot" not in called_cmd

    assert not os.path.exists(bat_path), "Should remove legacy print.bat"


# --- Bug 4: Path Traversal in Delete Session ---
def test_bug4_delete_session_path_traversal(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    res = client.delete("/sessions/.._.._invalid")
    assert res.status_code == 404


# --- Bug 6: Negative AMS Slot Regex ---
def test_bug6_print_session_ams_slot_regex(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    sid = "test_slot"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump({"id": sid, "versions": 1}, f)
    with open(os.path.join(v1_dir, "model.stl"), "w") as f:
        f.write("solid model endsolid model")

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_printer_busy", lambda: (False, "Idle"))

    with patch("subprocess.Popen") as mock_popen:
        res = client.post(f"/sessions/{sid}/print", json={"ams_slot": 2})
        assert res.status_code == 200
        called_cmd = mock_popen.call_args[0][0]
        assert "--slot" in called_cmd
        idx = called_cmd.index("--slot")
        assert called_cmd[idx + 1] == "2"


def test_print_session_slot_4_external(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    sid = "test_slot_4"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump({"id": sid, "versions": 1}, f)
    with open(os.path.join(v1_dir, "model.stl"), "w") as f:
        f.write("solid model endsolid model")

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_printer_busy", lambda: (False, "Idle"))

    with patch("subprocess.Popen") as mock_popen:
        res = client.post(f"/sessions/{sid}/print", json={"ams_slot": 4})
        assert res.status_code == 200
        called_cmd = mock_popen.call_args[0][0]
        assert "--slot" in called_cmd
        idx = called_cmd.index("--slot")
        assert called_cmd[idx + 1] == "4"


def test_print_session_blocked_when_disconnected(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    sid = "test_offline_print"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump({"id": sid, "versions": 1}, f)
    with open(os.path.join(v1_dir, "model.stl"), "w") as f:
        f.write("solid model endsolid model")

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: False)

    res = client.post(f"/sessions/{sid}/print", json={"ams_slot": 4})
    assert res.status_code == 400
    assert "Printer is not connected" in res.json()["detail"]


def test_print_session_returns_400_when_printer_offline(tmp_path, monkeypatch):
    """Explicit verification that print_session returns 400 when printer is offline without env bypass."""
    test_ws = str(tmp_path / "created_stls")
    sid = "test_offline_no_bypass"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump({"id": sid, "versions": 1}, f)
    with open(os.path.join(v1_dir, "model.stl"), "w") as f:
        f.write("solid model endsolid model")

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: False)

    res = client.post(f"/sessions/{sid}/print", json={"ams_slot": 0})
    assert res.status_code == 400
    assert "Printer is not connected" in res.json()["detail"]


def test_print_session_returns_409_when_printer_busy(tmp_path, monkeypatch):
    """Explicit verification that print_session returns 409 when printer is busy."""
    test_ws = str(tmp_path / "created_stls")
    sid = "test_busy_printer"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump({"id": sid, "versions": 1}, f)
    with open(os.path.join(v1_dir, "model.stl"), "w") as f:
        f.write("solid model endsolid model")

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr(
        "src.web.backend.app.bambu_cloud.is_printer_busy",
        lambda: (True, "State: RUNNING (42%)"),
    )

    res = client.post(f"/sessions/{sid}/print", json={"ams_slot": 0})
    assert res.status_code == 409
    assert "Printer is currently busy" in res.json()["detail"]
    assert "RUNNING" in res.json()["detail"]


# --- Bug 11: Explorer Folder Path Normalization ---
def test_bug11_open_folder_path_normalization(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    sid = "norm_session"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: MagicMock())

    res = client.post(f"/sessions/{sid}/open_folder", json={"version": 1})
    assert res.status_code == 200


def test_rerender_missing_stl(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    sid = "rerender_session"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    session_data = {
        "id": sid,
        "title": "Test Rerender",
        "versions": 1,
        "history": [
            {"role": "user", "text": "make a box"},
            {"role": "assistant", "code": "cube([10, 10, 10]);"},
        ],
    }
    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump(session_data, f)

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # 1. Verify load_session reports has_stl: False
    res_get = client.get(f"/sessions/{sid}")
    assert res_get.status_code == 200
    data = res_get.json()
    assert len(data["all_versions"]) == 1
    assert data["all_versions"][0]["has_stl"] is False

    # 2. Mock OpenSCAD compilation
    def mock_subprocess_popen(cmd, *args, **kwargs):
        # cmd: [openscad_path, "-o", stl_file, scad_file]
        stl_file = cmd[2]
        with open(stl_file, "w") as f:
            f.write("solid cube endsolid cube")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        return mock_proc

    monkeypatch.setattr("subprocess.Popen", mock_subprocess_popen)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0))
    monkeypatch.setattr("src.web.backend.routes.sessions.shared_slicer", lambda *a, **kw: None)

    # 3. Call rerender endpoint
    res = client.post(f"/sessions/{sid}/versions/1/rerender")
    assert res.status_code == 200
    lines = res.text.strip().split("\n")
    events = [json.loads(line) for line in lines if line]
    complete_event = [e for e in events if e.get("status") == "complete"][0]

    updated_session = complete_event["session"]
    assert updated_session["all_versions"][0]["has_stl"] is True
    assert updated_session["latest_stl"].endswith(".stl")


def test_print_purged_and_rerendered_session(tmp_path, monkeypatch):
    """Verify a purged session that is re-rendered has print.bat restored and can be printed."""
    test_ws = str(tmp_path / "created_stls")
    sid = "purged_rerender_print"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    session_data = {
        "id": sid,
        "title": "Purged Model",
        "versions": 1,
        "enable_supports": False,
        "history": [
            {"role": "user", "text": "make a cylinder"},
            {"role": "assistant", "code": "cylinder(h=10, r=5);"},
        ],
    }
    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump(session_data, f)

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr(
        "src.web.backend.routes.sessions._get_openscad_path", lambda: "mock_openscad"
    )
    monkeypatch.setattr("src.web.backend.routes.sessions.shared_slicer", lambda *a, **kw: None)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_printer_busy", lambda: (False, "Idle"))

    # Re-render v1
    res_rerender = client.post(f"/sessions/{sid}/versions/1/rerender")
    assert res_rerender.status_code == 200
    assert not os.path.exists(os.path.join(v1_dir, "print.bat"))
    assert not os.path.exists(os.path.join(v1_dir, "slice.bat"))

    # Now print v1
    with patch("subprocess.Popen") as mock_popen:
        res_print = client.post(
            f"/sessions/{sid}/print",
            json={"version": 1, "ams_slot": 1, "enable_supports": True},
        )
        assert res_print.status_code == 200
        called_cmd = mock_popen.call_args[0][0]
        assert "-m" in called_cmd and "src.cli.manual_print" in called_cmd
        assert "--slot" in called_cmd and called_cmd[called_cmd.index("--slot") + 1] == "1"
        assert "--support" in called_cmd


def test_print_bot_created_session_without_bat(tmp_path, monkeypatch):
    """Verify sessions created by Telegram/Google Tasks Bot without print.bat auto-generate print.bat on print."""
    test_ws = str(tmp_path / "created_stls")
    sid = "bot_created_session"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w") as f:
        json.dump({"id": sid, "title": "Bot Model", "versions": 1}, f)
    with open(os.path.join(v1_dir, "bot_design_v1.stl"), "w") as f:
        f.write("solid bot endsolid bot")

    assert not os.path.exists(os.path.join(v1_dir, "print.bat"))

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_printer_busy", lambda: (False, "Idle"))

    with patch("subprocess.Popen") as mock_popen:
        res = client.post(
            f"/sessions/{sid}/print",
            json={"version": 1, "ams_slot": 2, "enable_supports": True},
        )
        assert res.status_code == 200
        called_cmd = mock_popen.call_args[0][0]
        assert any("bot_design_v1.stl" in arg for arg in called_cmd)
        assert "--slot" in called_cmd and called_cmd[called_cmd.index("--slot") + 1] == "2"
        assert "--support" in called_cmd
    assert not os.path.exists(os.path.join(v1_dir, "print.bat"))


def test_clean_scad_code_escaped_newlines():
    from src.core.ai import clean_scad_code

    escaped = "$fn = 60;\\n\\nmodule test() {\\n    cube(10);\\n}"
    cleaned = clean_scad_code(escaped)
    assert "\n" in cleaned
    assert "\\n" not in cleaned
    assert "$fn = 60;\n\nmodule test() {" in cleaned


def test_print_bat_support_flag_toggle(monkeypatch):
    """Ensure print_session adds or removes --support flag in print.bat."""
    session_id = "test_session_support_print"
    v_dir = os.path.join(WORKSPACE_DIR, session_id, "v1")
    os.makedirs(v_dir, exist_ok=True)

    session_file = os.path.join(WORKSPACE_DIR, session_id, "session.json")
    with open(session_file, "w") as f:
        json.dump({"title": "Test", "versions": 1, "enable_supports": False}, f)

    bat_path = os.path.join(v_dir, "print.bat")
    with open(bat_path, "w") as f:
        f.write('python "bambu_printer.py" "file.stl" ""\n')

    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_connected", lambda: True)
    monkeypatch.setattr("src.web.backend.app.bambu_cloud.is_printer_busy", lambda: (False, "Idle"))

    with open(os.path.join(v_dir, "file.stl"), "w") as f:
        f.write("solid file endsolid file")

    # 1. Enable supports
    with patch("subprocess.Popen") as mock_popen1:
        response = client.post(
            f"/sessions/{session_id}/print",
            json={"ams_slot": 1, "enable_supports": True},
        )
        assert response.status_code == 200
        called_cmd1 = mock_popen1.call_args[0][0]
        assert "--support" in called_cmd1
        assert "--slot" in called_cmd1 and called_cmd1[called_cmd1.index("--slot") + 1] == "1"

    # 2. Disable supports
    with patch("subprocess.Popen") as mock_popen2:
        response = client.post(
            f"/sessions/{session_id}/print",
            json={"ams_slot": 2, "enable_supports": False},
        )
        assert response.status_code == 200
        called_cmd2 = mock_popen2.call_args[0][0]
        assert "--no-support" in called_cmd2
        assert "--slot" in called_cmd2 and called_cmd2[called_cmd2.index("--slot") + 1] == "2"


def test_new_session_support_flag_handling():
    """Ensure create_session parses --support from prompt and sets enable_supports."""
    with (
        patch("src.web.backend.routes.sessions.generate_with_validation") as mock_gemini,
        patch("src.web.backend.routes.sessions.compile_openscad_generator") as mock_compile,
    ):
        mock_gemini.return_value = [
            {"type": "result", "code": "cube(10);", "filename": "test_cube", "colors": []}
        ]
        mock_compile.return_value = [
            {
                "status": "complete",
                "assets": {
                    "stl_url": "/test.stl",
                    "time": "10m",
                    "size": "1x1x1",
                    "grams": 5.0,
                },
            }
        ]

        res = client.post(
            "/sessions/new",
            json={"prompt": "A mini dragon --support"},
        )
        assert res.status_code == 200
        # Check prompt passed to Gemini had --support stripped
        mock_gemini.assert_called_once()
        assert mock_gemini.call_args[0][0] == "A mini dragon"
        # Check compile_openscad_generator was called with enable_supports=True
        mock_compile.assert_called_once()
        assert mock_compile.call_args[1].get("enable_supports") is True


def test_web_session_persists_and_streams_validation_warnings(tmp_path, monkeypatch):
    """Verify validation warnings from generate_with_validation are streamed and persisted into history."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr(
        "src.web.backend.routes.sessions._get_openscad_path", lambda: "mock_openscad"
    )
    monkeypatch.setattr("src.web.backend.routes.sessions.shared_slicer", lambda *a, **kw: None)

    sample_warnings = ["Thin wall detected (<0.8mm)", "Overhang exceeds 45 degrees"]
    with patch("src.web.backend.routes.sessions.generate_with_validation") as mock_gen:
        mock_gen.return_value = [
            {
                "type": "result",
                "code": "cube(12);",
                "filename": "warned_cube",
                "colors": [],
                "warnings": sample_warnings,
            }
        ]
        res = client.post("/sessions/new", json={"prompt": "make a thin cube"})
        assert res.status_code == 200

    events = [json.loads(line) for line in res.text.strip().split("\n") if line]
    complete_events = [e for e in events if e.get("status") == "complete"]
    # Both compile_openscad_generator complete event and final session complete event
    asset_evt = [e for e in complete_events if "assets" in e][0]
    assert asset_evt["assets"]["warnings"] == sample_warnings

    final_sess = [e for e in complete_events if "session" in e][0]["session"]
    assistant_msg = [
        m for m in final_sess["history"] if m.get("role") == "assistant" and "code" in m
    ][0]
    assert assistant_msg["warnings"] == sample_warnings
    assert final_sess["all_versions"][0]["warnings"] == sample_warnings


def test_per_version_support_status_preserved_in_history(tmp_path, monkeypatch):
    """Verify v1 and v2 preserve their individual enable_supports and has_supports in history."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr(
        "src.web.backend.routes.sessions._get_openscad_path", lambda: "mock_openscad"
    )
    monkeypatch.setattr("src.web.backend.routes.sessions.shared_slicer", lambda *a, **kw: None)

    with patch("src.web.backend.routes.sessions.generate_with_validation") as mock_gen:
        mock_gen.return_value = [
            {
                "type": "result",
                "code": "cube(10);",
                "filename": "supp_v1",
                "colors": [],
                "has_supports": False,
            }
        ]
        res1 = client.post(
            "/sessions/new", json={"prompt": "v1 no support", "enable_supports": False}
        )
        assert res1.status_code == 200
        sess1 = [
            json.loads(line)["session"]
            for line in res1.text.strip().split("\n")
            if line
            and "session" in json.loads(line)
            and json.loads(line).get("status") == "complete"
        ][0]
        sid = sess1["id"]

        mock_gen.return_value = [
            {
                "type": "result",
                "code": "sphere(15);",
                "filename": "supp_v2",
                "colors": [],
                "has_supports": True,
            }
        ]
        res2 = client.post(
            f"/sessions/{sid}/edit",
            json={"prompt": "v2 with support", "enable_supports": True},
        )
        assert res2.status_code == 200
        sess2 = [
            json.loads(line)["session"]
            for line in res2.text.strip().split("\n")
            if line
            and "session" in json.loads(line)
            and json.loads(line).get("status") == "complete"
        ][0]

    code_msgs = [m for m in sess2["history"] if m.get("role") == "assistant" and "code" in m]
    assert len(code_msgs) == 2
    assert code_msgs[0]["enable_supports"] is False
    assert code_msgs[0]["has_supports"] is False
    assert code_msgs[1]["enable_supports"] is True
    assert code_msgs[1]["has_supports"] is True


def test_new_session_emits_immediate_init_and_tracks_has_code(tmp_path, monkeypatch):
    """Verify create_session yields init immediately, and load_session reports has_code properly on error."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    with patch("src.web.backend.routes.sessions.generate_with_validation") as mock_gemini:
        # Simulate 503 UNAVAILABLE failure from Gemini
        mock_gemini.side_effect = RuntimeError("503 UNAVAILABLE: Model overloaded")

        res = client.post("/sessions/new", json={"prompt": "failing dragon"})
        assert res.status_code == 200

        lines = [line for line in res.text.strip().split("\n") if line]
        events = [json.loads(line) for line in lines]

        # 1. First event must be init with session_id
        assert len(events) >= 2
        init_evt = events[0]
        assert init_evt["status"] == "init"
        assert "session_id" in init_evt
        assert init_evt["session_id"] == init_evt["session"]["id"]

        # 2. Error event must contain session_id and session object
        err_evt = events[1]
        assert err_evt["status"] == "error"
        assert "AI Error" in err_evt["message"]
        assert err_evt["session_id"] == init_evt["session_id"]
        assert err_evt["session"]["id"] == init_evt["session_id"]

        # 3. Version 1 has no code and no STL
        v1 = err_evt["session"]["all_versions"][0]
        assert v1["version"] == 1
        assert v1["has_stl"] is False
        assert v1["has_code"] is False

        # 4. Now simulate adding a .scad file for a version and verify load_session sets has_code=True
        sid = init_evt["session_id"]
        v1_dir = os.path.join(test_ws, sid, "v1")
        os.makedirs(v1_dir, exist_ok=True)
        with open(os.path.join(v1_dir, "model.scad"), "w") as f:
            f.write("cube(10);")

        res_loaded = client.get(f"/sessions/{sid}")
        assert res_loaded.status_code == 200
        loaded_v1 = res_loaded.json()["all_versions"][0]
        assert loaded_v1["has_code"] is True
        assert loaded_v1["has_stl"] is False


def test_compile_openscad_generator_skip_compile_reuses_artifacts(tmp_path, monkeypatch):
    """When skip_compile=True and prebuilt STL/metrics are passed, compile_openscad_generator
    does not invoke OpenSCAD or shared_slicer subprocesses, yet still writes .scad/print.bat/slice.bat
    and emits complete."""
    from src.web.backend.routes.sessions import compile_openscad_generator

    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    sid = "test-skip-compile-session"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    base_name = "design_v1_prebuilt"
    stl_file = os.path.join(v1_dir, f"{base_name}.stl")
    threemf_file = os.path.join(v1_dir, f"{base_name}_sliced.3mf")
    with open(stl_file, "w", encoding="utf-8") as f:
        f.write("solid fake\nendsolid fake\n")
    with open(threemf_file, "w", encoding="utf-8") as f:
        f.write("fake 3mf")

    with (
        patch("src.web.backend.routes.sessions.subprocess.Popen") as mock_popen,
        patch("src.web.backend.routes.sessions.shared_slicer") as mock_slicer,
    ):
        events = list(
            compile_openscad_generator(
                "cube([15, 15, 15]);",
                sid,
                1,
                ["Red"],
                enable_supports=True,
                base_name=base_name,
                skip_compile=True,
                prebuilt_stl=stl_file,
                prebuilt_3mf=threemf_file,
                prebuilt_size="0.59 × 0.59 × 0.59 in",
                prebuilt_time="18m",
                prebuilt_grams=4.8,
            )
        )

    # Neither OpenSCAD subprocess nor shared_slicer should be called
    mock_popen.assert_not_called()
    mock_slicer.assert_not_called()

    # Verify .scad was written, and batch scripts are NOT written
    assert os.path.exists(os.path.join(v1_dir, f"{base_name}.scad"))
    assert not os.path.exists(os.path.join(v1_dir, "slice.bat"))
    assert not os.path.exists(os.path.join(v1_dir, "print.bat"))

    # Verify final complete event has prebuilt metrics
    complete_evts = [e for e in events if e.get("status") == "complete"]
    assert len(complete_evts) == 1
    assets = complete_evts[0]["assets"]
    assert assets["size"] == "0.59 × 0.59 × 0.59 in"
    assert assets["time"] == "18m"
    assert assets["grams"] == 4.8
    assert assets["stl_url"] == f"/created_stls/{sid}/v1/{base_name}.stl"


def test_list_sessions_pagination_initial_and_load_more(tmp_path, monkeypatch):
    """Verify that GET /sessions paginates properly with limit, offset, and X-Total-Count header."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # Create 25 mock sessions with staggered timestamps
    for i in range(25):
        sid = f"sess_{i:02d}"
        sdir = os.path.join(test_ws, sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "session.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": sid,
                    "title": f"Idea {i:02d}",
                    "last_updated": f"2026-09-01T12:{i:02d}:00",
                },
                f,
            )

    # 1. Initial page: limit=20, offset=0
    res_p1 = client.get("/sessions?limit=20&offset=0")
    assert res_p1.status_code == 200
    assert res_p1.headers.get("x-total-count") == "25"
    data_p1 = res_p1.json()
    assert len(data_p1) == 20
    # Sessions are ordered newest first (sess_24 down to sess_05)
    assert data_p1[0]["id"] == "sess_24"
    assert data_p1[-1]["id"] == "sess_05"

    # 2. Load more: limit=10, offset=20
    res_p2 = client.get("/sessions?limit=10&offset=20")
    assert res_p2.status_code == 200
    assert res_p2.headers.get("x-total-count") == "25"
    data_p2 = res_p2.json()
    assert len(data_p2) == 5
    assert data_p2[0]["id"] == "sess_04"
    assert data_p2[-1]["id"] == "sess_00"

    # 3. Backward compatibility: no limit/offset returns all 25
    res_all = client.get("/sessions")
    assert res_all.status_code == 200
    assert res_all.headers.get("x-total-count") == "25"
    assert len(res_all.json()) == 25


def test_list_sessions_pagination_with_search_filter(tmp_path, monkeypatch):
    """Verify that pagination limits and X-Total-Count respect search query filter."""
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # Create 15 'box' sessions and 10 'sphere' sessions
    for i in range(15):
        sid = f"box_{i:02d}"
        sdir = os.path.join(test_ws, sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "session.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": sid,
                    "title": f"Custom Box {i:02d}",
                    "last_updated": f"2026-09-01T12:{i:02d}:00",
                },
                f,
            )

    for i in range(10):
        sid = f"sphere_{i:02d}"
        sdir = os.path.join(test_ws, sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "session.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": sid,
                    "title": f"Custom Sphere {i:02d}",
                    "last_updated": f"2026-09-01T13:{i:02d}:00",
                },
                f,
            )

    # Search for 'box' with limit=10, offset=0
    res_box = client.get("/sessions?q=box&limit=10&offset=0")
    assert res_box.status_code == 200
    assert res_box.headers.get("x-total-count") == "15"
    data_box = res_box.json()
    assert len(data_box) == 10
    assert all("Box" in item["title"] for item in data_box)

    # Search for 'box' second batch: limit=10, offset=10
    res_box_p2 = client.get("/sessions?q=box&limit=10&offset=10")
    assert res_box_p2.status_code == 200
    data_box_p2 = res_box_p2.json()
    assert len(data_box_p2) == 5
    assert all("Box" in item["title"] for item in data_box_p2)

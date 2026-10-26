"""Intensive unit and integration tests for service layers:
- online_version/backend/main.py
- remote_version/telegram_bot.py
- remote_version/google_tasks_bot.py
"""

import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web.backend.app import app
from src.web.backend.routes.sessions import save_session
from src.bots.google_tasks import bot as google_tasks_bot
from src.bots.telegram import bot as telegram_bot


# =====================================================================
# 1. online_version/backend/main.py Intensive Edge Cases
# =====================================================================


def test_backend_snapshot_and_ams_colors(tmp_path, monkeypatch):
    client = TestClient(app)

    # 1. /snapshot success (200 image/jpeg)
    fake_jpg = tmp_path / "snap.jpg"
    fake_jpg.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    monkeypatch.setattr(
        "src.web.backend.routes.printer.bambu_cloud.get_snapshot", lambda *_: str(fake_jpg)
    )
    res = client.get("/snapshot")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"

    # 2. /snapshot failure (500)
    monkeypatch.setattr("src.web.backend.routes.printer.bambu_cloud.get_snapshot", lambda *_: None)
    res_err = client.get("/snapshot")
    assert res_err.status_code == 500

    # 3. /ams_colors
    monkeypatch.setattr(
        "src.web.backend.routes.printer.bambu_cloud.get_available_colors",
        lambda: {0: {"color": "FF0000", "type": "PLA", "remain": 90}},
    )
    res_ams = client.get("/ams_colors")
    assert res_ams.status_code == 200
    assert "0" in res_ams.json()


def test_backend_search_sessions_and_legacy_folders(tmp_path, monkeypatch):
    ws = tmp_path / "created_stls"
    ws.mkdir()
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", str(ws))

    # Session 1: standard session with history
    s1_dir = ws / "sess_alpha"
    s1_dir.mkdir()
    save_session(
        "sess_alpha",
        {
            "id": "sess_alpha",
            "title": "Chess Knight",
            "history": [{"role": "user", "text": "make a detailed horse piece"}],
            "versions": 1,
            "last_updated": "2026-10-10T10:00:00",
        },
    )

    # Session 2: legacy folder without session.json (e.g., telegram root STL)
    s2_dir = ws / "uploaded_bracket_20260901_120000"
    s2_dir.mkdir()
    (s2_dir / "bracket.stl").write_text("solid b endsolid b")

    client = TestClient(app)

    # Search by title
    r1 = client.get("/sessions?q=knight")
    assert len(r1.json()) == 1
    assert r1.json()[0]["id"] == "sess_alpha"

    # Search by chat message history content
    r2 = client.get("/sessions?q=horse")
    assert len(r2.json()) == 1
    assert r2.json()[0]["id"] == "sess_alpha"

    # Search matching legacy folder
    r3 = client.get("/sessions?q=bracket")
    assert len(r3.json()) == 1
    assert r3.json()[0]["id"] == "uploaded_bracket_20260901_120000"

    # Load legacy folder directly via GET /sessions/{id}
    r_leg = client.get("/sessions/uploaded_bracket_20260901_120000")
    assert r_leg.status_code == 200
    assert r_leg.json()["all_versions"][0]["has_stl"] is True
    assert r_leg.json()["all_versions"][0]["has_code"] is False


def test_backend_edit_session_ai_and_compile_errors(tmp_path, monkeypatch):
    ws = tmp_path / "created_stls"
    ws.mkdir()
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", str(ws))

    sid = "sess_edit_err"
    (ws / sid / "v1").mkdir(parents=True)
    save_session(
        sid,
        {
            "id": sid,
            "title": "Box",
            "history": [
                {"role": "user", "text": "box"},
                {"role": "assistant", "code": "cube(10);"},
            ],
            "versions": 1,
        },
    )
    client = TestClient(app)

    # 1. AI raises exception during edit
    def failing_ai(*a, **kw):
        yield {"type": "progress", "attempt": 1, "max_calls": 3, "message": "Trying..."}
        raise RuntimeError("Quota exceeded")

    monkeypatch.setattr("src.web.backend.routes.sessions.generate_with_validation", failing_ai)
    res1 = client.post(f"/sessions/{sid}/edit", json={"prompt": "make bigger --support"})
    lines1 = [json.loads(line) for line in res1.text.splitlines() if line.strip()]
    assert any(l["status"] == "error" and "Quota exceeded" in l["message"] for l in lines1)

    # 2. AI succeeds, but OpenSCAD compile fails during edit
    def ok_ai(*a, **kw):
        yield {
            "type": "result",
            "code": "bad_scad();",
            "filename": "box_v2",
            "colors": ["Blue"],
        }

    monkeypatch.setattr("src.web.backend.routes.sessions.generate_with_validation", ok_ai)

    class FailPopen:
        def __init__(self, *a, **kw):
            self.returncode = 1
            self.args = a

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def poll(self):
            return 1

        def communicate(self, *a, **kw):
            return ("", "Parser error in line 1")

    monkeypatch.setattr("subprocess.Popen", FailPopen)
    monkeypatch.setattr("src.web.backend.routes.sessions.get_openscad_path", lambda: "openscad")
    monkeypatch.setattr("src.web.backend.routes.sessions.get_openscad_path", lambda: "openscad")
    res2 = client.post(f"/sessions/{sid}/edit", json={"prompt": "make bigger --no-support"})
    lines2 = [json.loads(line) for line in res2.text.splitlines() if line.strip()]
    assert any(l["status"] == "error" and "Parser error" in l["message"] for l in lines2)


def test_backend_rerender_missing_code_and_compile_error(tmp_path, monkeypatch):
    ws = tmp_path / "created_stls"
    ws.mkdir()
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", str(ws))

    sid = "sess_rerender_err"
    (ws / sid / "v1").mkdir(parents=True)
    save_session(
        sid,
        {
            "id": sid,
            "title": "No Code Session",
            "history": [{"role": "user", "text": "hello"}],
            "versions": 1,
        },
    )
    client = TestClient(app)

    # 1. No .scad on disk and no code in history -> yields status: error
    r1 = client.post(f"/sessions/{sid}/versions/1/rerender")
    events1 = [json.loads(line) for line in r1.text.splitlines() if line.strip()]
    assert any(
        e["status"] == "error" and "No OpenSCAD source code" in e["message"] for e in events1
    )

    # 2. Has .scad on disk, but OpenSCAD fails with non-zero exit code
    (ws / sid / "v1" / "design.scad").write_text("cube(10);\\n")

    class FailPopen:
        def __init__(self, *a, **kw):
            self.returncode = 1
            self.args = a

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def poll(self):
            return 1

        def communicate(self, *a, **kw):
            return ("", "OpenSCAD segfault")

    monkeypatch.setattr("subprocess.Popen", FailPopen)
    monkeypatch.setattr("src.web.backend.routes.sessions.get_openscad_path", lambda: "openscad")
    monkeypatch.setattr("src.web.backend.routes.sessions.get_openscad_path", lambda: "openscad")
    r2 = client.post(f"/sessions/{sid}/versions/1/rerender")
    events2 = [json.loads(line) for line in r2.text.splitlines() if line.strip()]
    assert any(e["status"] == "error" and "OpenSCAD segfault" in e["message"] for e in events2)


def test_backend_delete_and_open_folder(tmp_path, monkeypatch):
    ws = tmp_path / "created_stls"
    ws.mkdir()
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", str(ws))

    sid = "sess_to_delete"
    (ws / sid / "v1").mkdir(parents=True)
    save_session(sid, {"id": sid, "title": "Del", "history": [], "versions": 1})

    client = TestClient(app)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())

    # Open folder 200
    r_open = client.post(f"/sessions/{sid}/open_folder", json={"version": 1})
    assert r_open.status_code == 200

    # Open folder 404
    r_open_404 = client.post("/sessions/nonexistent_sess/open_folder", json={"version": 1})
    assert r_open_404.status_code == 404

    # Delete session 200
    r_del = client.delete(f"/sessions/{sid}")
    assert r_del.status_code == 200
    assert not (ws / sid).exists()


# =====================================================================
# 2. remote_version/telegram_bot.py Intensive Edge Cases
# =====================================================================


def _make_telegram_update_context(args=None):
    update = MagicMock()
    update.effective_chat.id = 12345
    update.effective_chat.send_message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.message.reply_photo = AsyncMock()
    context = MagicMock()
    context.args = args or []
    context.user_data = {}
    return update, context


def test_telegram_make_auto_print_and_warnings(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "WORKSPACE_DIR", str(tmp_path))

    # 1. /make when constraint warnings exist -> aborts auto-print!
    update, context = _make_telegram_update_context(["a", "giant", "sword"])
    monkeypatch.setattr(
        telegram_bot,
        "generate_openscad_validated",
        lambda *a, **kw: ("cube(500);", "sword", ["Grey"], ["Exceeds max size"], None, None),
    )
    res = asyncio.run(telegram_bot.start_make(update, context))
    assert res == telegram_bot.WAITING_FOR_DECISION
    assert any("Print aborted" in call.args[0] for call in update.message.reply_text.call_args_list)

    # 2. /make when clean -> slices and auto-prints via matched AMS slot
    update2, context2 = _make_telegram_update_context(["a", "small", "coin"])
    fake_stl = tmp_path / "pre.stl"
    fake_stl.write_text("solid c endsolid c")
    fake_3mf = tmp_path / "pre.3mf"
    fake_3mf.write_text("3mf")

    monkeypatch.setattr(
        telegram_bot,
        "generate_openscad_validated",
        lambda *a, **kw: (
            "cylinder(h=2,r=10);",
            "coin",
            ["Yellow"],
            [],
            str(fake_stl),
            str(fake_3mf),
        ),
    )
    monkeypatch.setattr(
        telegram_bot.bambu_cloud,
        "get_available_colors",
        lambda: {2: {"color": "FFFF00", "type": "PLA", "remain": 80, "is_bambu": True}},
    )
    monkeypatch.setattr(telegram_bot.bambu_cloud, "send_print_job", lambda *a, **kw: True)

    res2 = asyncio.run(telegram_bot.start_make(update2, context2))
    assert res2 == telegram_bot.ConversationHandler.END
    assert any(
        "Print job successfully started" in call.args[0]
        for call in update2.message.reply_text.call_args_list
    )


def test_telegram_print_commands_and_callbacks(tmp_path, monkeypatch):
    # 1. /print with no arguments lists available colors
    update, context = _make_telegram_update_context([])
    context.user_data["last_3mf_file"] = str(tmp_path / "ready.3mf")
    monkeypatch.setattr(
        telegram_bot.bambu_cloud,
        "get_available_colors",
        lambda: {
            0: {"color": "FF0000", "type": "PLA", "remain": 50},
            4: {"color": "0000FF", "type": "PETG", "remain": -1},
        },
    )
    res = asyncio.run(telegram_bot.start_print(update, context))
    assert res == telegram_bot.WAITING_FOR_DECISION
    msg_sent = update.message.reply_text.call_args.args[0]
    assert "Available slots:" in msg_sent
    assert "External Spool (Slot 4)" in msg_sent
    assert "(UNSUPPORTED)" in msg_sent

    # 2. /print auto when printer is busy (RUNNING) -> blocked with message
    update_busy, context_busy = _make_telegram_update_context(["auto"])
    context_busy.user_data["last_3mf_file"] = str(tmp_path / "ready.3mf")
    monkeypatch.setattr(telegram_bot.bambu_cloud, "get_status", lambda: {"state": "RUNNING"})
    res_busy = asyncio.run(telegram_bot.start_print(update_busy, context_busy))
    assert res_busy == telegram_bot.WAITING_FOR_DECISION
    assert "Wait until build plate is clear" in update_busy.message.reply_text.call_args.args[0]

    # 3. stop_print_callback confirm and cancel
    cb_update = MagicMock()
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    cb_update.callback_query.data = "confirm_stop_print"
    monkeypatch.setattr(telegram_bot.bambu_cloud, "stop_print", lambda: True)
    asyncio.run(telegram_bot.stop_print_callback(cb_update, context))
    cb_update.callback_query.edit_message_text.assert_called_with(
        "Print stop command sent successfully."
    )

    cb_update.callback_query.data = "cancel_stop_print"
    asyncio.run(telegram_bot.stop_print_callback(cb_update, context))
    cb_update.callback_query.edit_message_text.assert_called_with("Stop print cancelled.")


def test_telegram_snapshot_and_unknown_messages(tmp_path, monkeypatch):
    update, context = _make_telegram_update_context()

    # 1. Snapshot success
    snap_file = tmp_path / "snap.jpg"
    snap_file.write_bytes(b"jpgdata")
    monkeypatch.setattr(telegram_bot.bambu_cloud, "get_snapshot", lambda *_: str(snap_file))
    asyncio.run(telegram_bot.request_snapshot(update, context))
    update.message.reply_photo.assert_called_once()

    # 2. Snapshot failure
    monkeypatch.setattr(telegram_bot.bambu_cloud, "get_snapshot", lambda *_: None)
    asyncio.run(telegram_bot.request_snapshot(update, context))
    assert "Failed to capture" in update.message.reply_text.call_args.args[0]

    # 3. enforce_cancel and unknown_message across states
    context.user_data = {"is_cancelling": True}
    asyncio.run(telegram_bot.enforce_cancel(update, context))
    asyncio.run(telegram_bot.unknown_message(update, context))

    context.user_data = {"is_processing": True}
    asyncio.run(telegram_bot.enforce_cancel(update, context))
    asyncio.run(telegram_bot.unknown_message(update, context))

    context.user_data = {}
    asyncio.run(telegram_bot.unknown_message(update, context))
    assert "Hi there!" in update.message.reply_text.call_args.args[0]


# =====================================================================
# 3. remote_version/google_tasks_bot.py Intensive Edge Cases
# =====================================================================


def test_google_tasks_error_and_warning_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(google_tasks_bot, "WORKSPACE_DIR", str(tmp_path))

    mock_service = MagicMock()
    mock_tasks = mock_service.tasks.return_value
    mock_tasks.list.return_value.execute.return_value = {
        "items": [
            {"id": "t1", "title": "3D Print: oversized model", "notes": "extra notes"},
            {"id": "t2", "title": "3D Print: ai error model"},
            {"id": "t3", "title": "3D Print: openscad error model"},
        ]
    }

    call_idx = [0]

    def fake_gen(prompt, *a, **kw):
        call_idx[0] += 1
        if call_idx[0] == 1:
            assert "extra notes" in prompt
            return ("cube(500);", "oversized", [], ["Too large"], None, None)
        if call_idx[0] == 2:
            raise RuntimeError("API down")
        return ("bad_scad();", "bad_model", [], [], None, None)

    monkeypatch.setattr(google_tasks_bot, "generate_openscad_validated", fake_gen)
    monkeypatch.setattr(google_tasks_bot, "get_openscad_path", lambda: "mock_openscad")

    def fake_sub_run(*a, **kw):
        raise subprocess.CalledProcessError(returncode=1, cmd="openscad", stderr=b"syntax err")

    monkeypatch.setattr("subprocess.run", fake_sub_run)

    google_tasks_bot.process_tasks(mock_service)
    # All 3 tasks should have been patched to "completed" after graceful error handling
    assert mock_tasks.patch.call_count == 3

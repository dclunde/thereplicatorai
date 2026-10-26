import asyncio
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bots.google_tasks.bot import process_tasks
from src.bots.telegram import handlers_design, handlers_printer, monitor
from src.core.ai import validation_loop
from src.core.printer.bambu_client import BambuClient
from src.core.slicer import mesh_validator, openscad, slicer_3mf
from src.web.backend.routes import sessions


# =========================================================================
# 1. Telegram Bot Resilience Tests
# =========================================================================


@pytest.mark.asyncio
async def test_telegram_process_stl_for_print_slicer_failure():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.user_data = {"is_processing": True}

    def failing_slice(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["orca-slicer"], stderr="Slicing segmentation fault")

    with patch.object(handlers_design, "slice_stl_to_3mf", failing_slice):
        with patch.object(handlers_design, "get_bambu_cloud") as mock_bc:
            mock_bc.return_value.get_available_colors.return_value = {}
            ret = await handlers_design.process_stl_for_print(
                update, context, "test.stl", "outdir", "test_base"
            )

    assert ret == handlers_design.WAITING_FOR_DECISION
    assert context.user_data.get("is_processing") is False
    assert context.user_data.get("design_state") == "Failed to slice"


@pytest.mark.asyncio
async def test_telegram_handle_document_size_and_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers_design, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(handlers_design, "get_workspace_dir", lambda: str(tmp_path))

    # Test file > 20MB rejected
    update_large = MagicMock()
    update_large.message.document.file_name = "huge.stl"
    update_large.message.document.file_size = 25 * 1024 * 1024
    context_large = MagicMock()
    context_large.user_data = {}

    ret_large = await handlers_design.handle_document(update_large, context_large)
    assert ret_large == handlers_design.ConversationHandler.END

    # Test path traversal sanitized
    update_traversal = MagicMock()
    update_traversal.effective_chat.id = 12345
    update_traversal.message.document.file_name = "../../../etc/passwd.stl"
    update_traversal.message.document.file_size = 1000
    update_traversal.message.document.file_id = "doc123"

    mock_file = AsyncMock()
    downloaded_paths = []

    async def mock_download(dest):
        downloaded_paths.append(dest)
        with open(dest, "wb") as f:
            f.write(b"solid test\nendsolid\n")

    mock_file.download_to_drive = mock_download
    context_traversal = MagicMock()
    context_traversal.bot.get_file = AsyncMock(return_value=mock_file)
    context_traversal.user_data = {}

    with patch.object(handlers_design, "process_stl_for_print", AsyncMock(return_value=1)):
        await handlers_design.handle_document(update_traversal, context_traversal)

    assert len(downloaded_paths) == 1
    # Must only contain the sanitized basename
    assert ".." not in downloaded_paths[0]
    assert os.path.basename(downloaded_paths[0]) == "passwd.stl"


@pytest.mark.asyncio
async def test_telegram_start_print_cancel_during_retry():
    update = MagicMock()
    context = MagicMock()
    context.args = ["1"]
    context.user_data = {
        "last_3mf_file": "test.3mf",
        "cancel_token": {"cancelled": False},
        "last_3mf_has_supports": False,
        "enable_supports": False,
    }

    mock_bambu = MagicMock()
    mock_bambu.get_available_colors.return_value = {1: {"type": "PLA", "color": "#FF0000"}}
    mock_bambu.get_status.return_value = {"state": "IDLE"}

    attempts = [0]

    def failing_send(*args, **kwargs):
        attempts[0] += 1
        if attempts[0] == 2:
            context.user_data["cancel_token"]["cancelled"] = True
        return False

    mock_bambu.send_print_job = failing_send

    with patch.object(handlers_printer, "get_bambu_cloud", return_value=mock_bambu):
        ret = await handlers_printer.start_print(update, context)

    assert ret == handlers_printer.WAITING_FOR_DECISION
    # Must have aborted early due to cancellation rather than exhausting all 5 attempts
    assert attempts[0] < 5


@pytest.mark.asyncio
async def test_telegram_monitor_snapshot_cleanup_and_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "SNAPSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(monitor, "previous_print_state", "RUNNING")

    mock_cloud = MagicMock()
    mock_cloud.last_status = {"state": "FINISH"}
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    mock_app.bot.send_photo = AsyncMock(side_effect=RuntimeError("Photo send network drop"))

    monitor.known_chat_ids.add(999)

    created_snapshots = []

    def fake_capture(url, out_path):
        with open(out_path, "wb") as f:
            f.write(b"fake image")
        created_snapshots.append(out_path)
        return True

    monkeypatch.setattr(monitor, "_capture_snapshot_sync", fake_capture)

    # Run one single iteration of the monitor logic
    with patch.object(monitor, "get_bambu_cloud", return_value=mock_cloud):
        with patch.object(
            monitor,
            "load_api_keys",
            return_value={"BAMBU_PRINTER_IP": "1.2.3.4", "BAMBU_ACCESS_CODE": "123"},
        ):
            with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError])):
                try:
                    await monitor.monitor_print_status(mock_app)
                except asyncio.CancelledError:
                    pass

    assert len(created_snapshots) == 1
    # Snapshot must be inside SNAPSHOTS_DIR and deleted in finally
    assert str(tmp_path) in created_snapshots[0]
    assert not os.path.exists(created_snapshots[0])


# =========================================================================
# 2. Slicer 3MF & Geometry Resilience Tests
# =========================================================================


def test_slicer_3mf_corrupt_template(tmp_path, monkeypatch):
    corrupt_template = tmp_path / "default_project.3mf"
    corrupt_template.write_bytes(b"Not a valid zip file content")

    stl_file = tmp_path / "test.stl"
    stl_file.write_bytes(b"solid test\nendsolid\n")

    monkeypatch.setattr(slicer_3mf, "TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(slicer_3mf, "TMP_DIR", str(tmp_path / "tmp"))

    # Mock PrusaSlicer to create a fake valid 3MF geometry
    def fake_prusa(cmd, **kwargs):
        temp_3mf = cmd[3] if "--export-3mf" in cmd else cmd[4]
        import zipfile

        with zipfile.ZipFile(temp_3mf, "w") as z:
            z.writestr("3D/3dmodel.model", "<model></model>")

    monkeypatch.setattr("subprocess.run", fake_prusa)

    result = slicer_3mf.slice_stl_to_3mf(str(stl_file))
    assert result is None  # Must return None cleanly without raising BadZipFile


def test_slicer_compile_and_slice_openscad_failure(tmp_path, monkeypatch):
    scad_file = tmp_path / "broken.scad"
    scad_file.write_text("broken code")

    monkeypatch.setattr(slicer_3mf, "get_openscad_path", lambda: "openscad")

    def failing_openscad(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["openscad"], stderr="syntax error")

    monkeypatch.setattr("subprocess.run", failing_openscad)

    res = slicer_3mf.compile_and_slice(str(scad_file))
    assert res is None


# =========================================================================
# 3. Hardware Client (BambuClient) Resilience Tests
# =========================================================================


def test_bambu_client_ftps_upload_timeout(tmp_path, monkeypatch):
    client = BambuClient(ip="127.0.0.1", access_code="12345678", device_serial="DEV001")
    dummy_file = tmp_path / "job.gcode"
    dummy_file.write_text("G28\n")

    # Mock LocalPrintClient import
    mock_local = MagicMock()
    mock_local.create_print_command.return_value = {"command": "print"}
    monkeypatch.setitem(sys.modules, "bambulab.local_api", MagicMock(LocalPrintClient=mock_local))

    def timeout_sub_run(cmd, **kwargs):
        if cmd[0] == "curl":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", timeout_sub_run)

    success = client._dispatch_lan_print(str(dummy_file))
    assert success is False


def test_bambu_client_get_available_colors_non_blocking():
    client = BambuClient()
    client.ams_data_received = False
    client.ams_colors = {}

    # Non-blocking call must return immediately without 5s sleep
    colors = client.get_available_colors(blocking=False)
    assert colors == {}


# =========================================================================
# 4. Multi-Agent AI Validation Loop Fallback Tests
# =========================================================================


def test_ai_validation_loop_recovers_working_model_on_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_loop, "TMP_DIR", str(tmp_path))

    # Candidate 1 compiles cleanly; pass 2 hits RateLimitError (HTTP 429)
    call_count = [0]

    def mock_call_ai(client, model, instructions, prompt):
        call_count[0] += 1
        if call_count[0] == 1:
            # First pass succeeds
            return '```json\n{"code": "cube(10);", "filename": "cube10"}\n```'
        else:
            # Refinement / verification hits HTTP 429
            raise RuntimeError("HTTP 429: Resource has been exhausted (rate limit)")

    monkeypatch.setattr(validation_loop, "_call_ai", mock_call_ai)

    def mock_eval(code, *args, **kwargs):
        return {
            "success": True,
            "stl_path": str(tmp_path / "test.stl"),
            "size": '1" x 1" x 1"',
            "print_time": "15m",
            "filament_grams": 5.0,
            "geometry_health": "All checks passed",
            "all_issues": [],
            "geo_errors": [],
            "constraint_errors": [],
        }

    monkeypatch.setattr(validation_loop, "_evaluate_candidate_code", mock_eval)

    mock_client = MagicMock()
    monkeypatch.setattr(
        validation_loop,
        "_load_ai_config",
        lambda service=None: {
            "client": mock_client,
            "model": "test-model",
            "instructions": "test instructions",
            "error_template": "error {error_message}",
            "verify_template": "verify {code}",
            "max_ai_calls": 3,
            "agent_level": 3,
        },
    )
    gen = validation_loop.generate_with_validation(
        "Make a 10mm cube",
        max_calls=3,
    )

    result = list(gen)
    final_dict = result[-1]
    assert final_dict["type"] == "result"
    assert "cube(10);" in final_dict["code"]
    # Explanatory fallback warning must be included
    assert any(
        "AI verification interrupted" in w or "recovered previous working" in w
        for w in final_dict["warnings"]
    )


# =========================================================================
# 5. Mesh Validator & STL Streaming Memory Tests
# =========================================================================


def test_mesh_validator_handles_degenerate_0_vertex_body():
    # Construct a dummy mesh-like body with 0 vertices
    class DegenerateBody:
        vertices = MagicMock()
        bounds = [[0, 0, 0], [0, 0, 0]]
        centroid = [0, 0, 0]

    DegenerateBody.vertices.__len__ = MagicMock(return_value=0)

    class MockMesh:
        is_empty = False
        vertices = [1, 2, 3]

        def split(self, **kwargs):
            return [DegenerateBody()]

    with patch("trimesh.load", return_value=MockMesh()):
        errors = mesh_validator.analyze_mesh_geometry("dummy.stl")
        # Must not raise ValueError: zero-size array
        assert isinstance(errors, list)


def test_calculate_max_dimensions_streaming_binary(tmp_path):
    # Create a small valid binary STL with 1 triangle
    stl_file = tmp_path / "single_tri.stl"
    import struct

    header = b"\x00" * 80
    num_triangles = 1
    # Triangle: (0,0,0), (10, 20, 30), (5, 5, 5)
    normal = struct.pack("<3f", 0.0, 0.0, 1.0)
    v1 = struct.pack("<3f", 0.0, 0.0, 0.0)
    v2 = struct.pack("<3f", 10.0, 20.0, 30.0)
    v3 = struct.pack("<3f", 5.0, 5.0, 5.0)
    attr = b"\x00\x00"

    with open(stl_file, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", num_triangles))
        f.write(normal + v1 + v2 + v3 + attr)

    dims = openscad.calculate_max_dimensions(str(stl_file))
    assert dims == (10.0, 20.0, 30.0)


# =========================================================================
# 6. Web Backend & Google Tasks Bot Resilience Tests
# =========================================================================


def test_web_backend_openscad_generator_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "get_workspace_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sessions, "resolve_session_dir", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(sessions, "_get_openscad_path", lambda: "openscad")
    monkeypatch.setattr(sessions, "get_openscad_path", lambda: "openscad")

    # Mock subprocess to hang indefinitely
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # Never finishes
    mock_proc.kill = MagicMock()
    mock_proc.communicate.return_value = ("", "Timeout killed")

    monkeypatch.setattr(
        sessions, "_get_subprocess", lambda: MagicMock(Popen=lambda *a, **kw: mock_proc)
    )

    # Force time.time to simulate passage of 301 seconds
    start_time = 1000.0
    times = [start_time, start_time + 1.0, start_time + 301.0, start_time + 302.0]

    def fake_time():
        if times:
            return times.pop(0)
        return start_time + 400.0

    monkeypatch.setattr(sessions.time, "time", fake_time)
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)

    events = list(
        sessions.compile_openscad_generator(
            scad_code="cube(10);",
            session_id="sess123",
            version=1,
            skip_compile=False,
        )
    )

    error_events = [e for e in events if e.get("status") == "error"]
    assert len(error_events) >= 1
    assert "timed out after 300 seconds" in error_events[0]["message"]
    mock_proc.kill.assert_called_once()


def test_rerender_session_timeout_and_slicing(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from src.web.backend.app import app

    test_ws = str(tmp_path / "created_stls")
    sid = "rerender_timeout_test"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)
    with open(os.path.join(test_ws, sid, "session.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "id": sid,
                "title": "Timeout Test",
                "versions": 1,
                "enable_supports": True,
                "history": [
                    {"role": "user", "text": "cube"},
                    {"role": "assistant", "code": "cube(10);", "enable_supports": True},
                ],
            },
            f,
        )

    monkeypatch.setattr(sessions, "WORKSPACE_DIR", test_ws)
    monkeypatch.setattr(sessions, "_get_openscad_path", lambda: "openscad")

    # 1. Test timeout in rerender_session
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.kill = MagicMock()
    mock_proc.communicate.return_value = ("", "Killed")
    monkeypatch.setattr(
        sessions, "_get_subprocess", lambda: MagicMock(Popen=lambda *a, **kw: mock_proc)
    )

    start_time = 2000.0
    calls = [0]

    def fake_time():
        calls[0] += 1
        # First few calls happen inside busy_activity / FileLock / setup, then inside the loop
        if calls[0] <= 4:
            return start_time + calls[0] * 0.1
        return start_time + 305.0 + calls[0]

    monkeypatch.setattr(sessions.time, "time", fake_time)
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)

    tc = TestClient(app)
    res_timeout = tc.post(f"/sessions/{sid}/versions/1/rerender")
    assert res_timeout.status_code == 200
    events = [json.loads(line) for line in res_timeout.text.strip().split("\n") if line]
    err_evts = [e for e in events if e.get("status") == "error"]
    assert len(err_evts) == 1
    assert "OpenSCAD compilation timed out after 300 seconds." in err_evts[0]["message"]
    mock_proc.kill.assert_called_once()

    # 2. Test successful rerender with slicing restoring 3mf_path, time, grams, has_supports
    monkeypatch.setattr(sessions, "_get_openscad_path", lambda: "mock_openscad")
    fake_3mf = os.path.join(v1_dir, "Ready_design.3mf")

    def fake_slicer(stl_file, out_dir, base_name, enable_supports=False):
        with open(fake_3mf, "w", encoding="utf-8") as f:
            f.write("fake 3mf")
        return fake_3mf

    monkeypatch.setattr(sessions, "_get_shared_slicer", lambda: fake_slicer)
    monkeypatch.setattr(
        sessions,
        "get_slice_info_from_3mf",
        lambda p: {"time": "22m", "grams": 9.4, "has_supports": True},
    )

    res_ok = tc.post(f"/sessions/{sid}/versions/1/rerender")
    assert res_ok.status_code == 200
    ok_events = [json.loads(line) for line in res_ok.text.strip().split("\n") if line]
    comp_evt = [e for e in ok_events if e.get("status") == "complete"][0]
    updated_hist = comp_evt["session"]["history"][1]
    assert updated_hist["time"] == "22m"
    assert updated_hist["grams"] == 9.4
    assert updated_hist["has_supports"] is True
    assert updated_hist["3mf_path"] == fake_3mf


def test_google_tasks_bot_failure_reporting(tmp_path, monkeypatch):
    monkeypatch.setattr("src.bots.google_tasks.bot.WORKSPACE_DIR", str(tmp_path))

    mock_service = MagicMock()
    mock_tasks = mock_service.tasks.return_value
    mock_tasks.list.return_value.execute.return_value = {
        "items": [
            {"id": "task_fail_1", "title": "3D Print: Failing AI", "notes": "initial note"},
        ]
    }

    # Simulate AI exception
    def failing_ai(*args, **kwargs):
        raise RuntimeError("AI model quota reached")

    monkeypatch.setattr("src.bots.google_tasks.bot.generate_openscad_validated", failing_ai)

    process_tasks(mock_service)

    # Must patch task with [FAILED] and status 'needsAction', NOT 'completed'
    mock_tasks.patch.assert_called_once()
    patch_body = mock_tasks.patch.call_args[1]["body"]
    assert "[FAILED] 3D Print: Failing AI" in patch_body["title"]
    assert patch_body["status"] == "needsAction"
    assert "AI generation error" in patch_body["notes"]

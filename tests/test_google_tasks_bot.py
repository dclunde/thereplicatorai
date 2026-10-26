from unittest.mock import patch, MagicMock, mock_open, ANY
import sys
import os

# Ensure remote_version is importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.bots.google_tasks.bot import process_tasks, authenticate_google_tasks


@patch("src.bots.google_tasks.bot.save_session")
@patch("src.bots.google_tasks.bot.generate_openscad_validated")
@patch("src.bots.google_tasks.bot.subprocess.run")
@patch("src.bots.google_tasks.bot.get_openscad_path")
@patch("builtins.open", new_callable=mock_open)
@patch("os.makedirs")
def test_process_tasks(
    mock_makedirs,
    mock_open,
    mock_get_openscad_path,
    mock_subprocess_run,
    mock_generate_openscad_validated,
    mock_save_session,
):
    mock_service = MagicMock()
    mock_tasks = MagicMock()
    mock_service.tasks().list.return_value.execute.return_value = {
        "items": [
            {"id": "task123", "title": "3d print: A red cube", "notes": "Make it 10x10"},
            {"id": "task456", "title": "Buy groceries", "notes": "Milk, eggs"},
        ]
    }

    mock_generate_openscad_validated.return_value = ("cube(10);", "cube_test", ["Red"], [])
    mock_get_openscad_path.return_value = "openscad"

    # Run
    process_tasks(mock_service)

    # Verify generate_openscad was called with the right prompt
    mock_generate_openscad_validated.assert_called_once_with(
        "A red cube\nMake it 10x10", [], ANY, "google_tasks"
    )

    # Verify subprocess.run was called twice (once for openscad, once for bambu_printer.py)
    assert mock_subprocess_run.call_count == 2

    # Verify task was marked as completed
    mock_service.tasks().patch.assert_called_once_with(
        tasklist="@default", task="task123", body={"status": "completed"}
    )
    mock_service.tasks().patch().execute.assert_called_once()


@patch("src.bots.google_tasks.bot.Credentials")
@patch("src.bots.google_tasks.bot.InstalledAppFlow")
@patch("src.bots.google_tasks.bot.os.path.exists")
@patch("builtins.open", new_callable=mock_open)
def test_authenticate_google_tasks_refresh_error(
    mock_file, mock_exists, mock_flow_class, mock_credentials_class
):
    mock_exists.side_effect = lambda path: True

    # Mock expired credentials that fail on refresh
    expired_creds = MagicMock()
    expired_creds.valid = False
    expired_creds.expired = True
    expired_creds.refresh_token = "revoked_token"
    expired_creds.refresh.side_effect = Exception(
        "invalid_grant: Token has been expired or revoked."
    )
    mock_credentials_class.from_authorized_user_file.return_value = expired_creds

    # Mock fresh credentials from local server flow
    fresh_creds = MagicMock()
    fresh_creds.to_json.return_value = '{"token": "fresh"}'
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = fresh_creds
    mock_flow_class.from_client_secrets_file.return_value = mock_flow

    creds = authenticate_google_tasks()

    assert creds == fresh_creds
    mock_flow.run_local_server.assert_called_once_with(port=0)
    mock_file().write.assert_called_once_with('{"token": "fresh"}')


@patch("src.bots.google_tasks.bot.save_session")
@patch("src.bots.google_tasks.bot.generate_openscad_validated")
@patch("src.bots.google_tasks.bot.subprocess.run")
@patch("src.bots.google_tasks.bot.get_openscad_path")
@patch("src.bots.google_tasks.bot.get_google_tasks_supports_config")
@patch("builtins.open", new_callable=mock_open)
@patch("os.makedirs")
def test_google_tasks_with_supports_enabled(
    mock_makedirs,
    mock_open,
    mock_get_supports,
    mock_get_openscad_path,
    mock_subprocess_run,
    mock_generate_openscad_validated,
    mock_save_session,
):
    mock_get_supports.return_value = True
    mock_service = MagicMock()
    mock_service.tasks().list.return_value.execute.return_value = {
        "items": [{"id": "task_sup", "title": "3d print: Support model", "notes": ""}]
    }
    mock_generate_openscad_validated.return_value = ("cube(10);", "cube_test", ["Red"], [])
    mock_get_openscad_path.return_value = "openscad"

    process_tasks(mock_service)

    # 2 subprocess calls: 1 openscad, 1 bambu_printer.py
    assert mock_subprocess_run.call_count == 2
    bambu_cmd = mock_subprocess_run.call_args_list[1][0][0]
    assert "--support" in bambu_cmd


@patch("src.bots.google_tasks.bot.save_session")
@patch("src.bots.google_tasks.bot.generate_openscad_validated")
@patch("src.bots.google_tasks.bot.subprocess.run")
@patch("src.bots.google_tasks.bot.get_openscad_path")
@patch("src.bots.google_tasks.bot.get_google_tasks_supports_config")
@patch("builtins.open", new_callable=mock_open)
@patch("os.makedirs")
def test_google_tasks_with_supports_disabled(
    mock_makedirs,
    mock_open,
    mock_get_supports,
    mock_get_openscad_path,
    mock_subprocess_run,
    mock_generate_openscad_validated,
    mock_save_session,
):
    mock_get_supports.return_value = False
    mock_service = MagicMock()
    mock_service.tasks().list.return_value.execute.return_value = {
        "items": [{"id": "task_nosup", "title": "3d print: No support model", "notes": ""}]
    }
    mock_generate_openscad_validated.return_value = ("cube(10);", "cube_test", ["Red"], [])
    mock_get_openscad_path.return_value = "openscad"

    process_tasks(mock_service)

    assert mock_subprocess_run.call_count == 2
    bambu_cmd = mock_subprocess_run.call_args_list[1][0][0]
    assert "--support" not in bambu_cmd


def test_google_tasks_skips_print_when_printer_busy_and_saves_session(tmp_path, monkeypatch):
    import json
    from src.bots.google_tasks import bot as gt_bot

    monkeypatch.setattr(gt_bot, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(gt_bot, "get_openscad_path", lambda: "openscad")
    monkeypatch.setattr(gt_bot, "get_google_tasks_supports_config", lambda: False)

    def fake_gen(
        prompt,
        history,
        max_calls,
        service,
        output_dir=None,
        file_basename=None,
        keep_temp_artifacts=False,
        **kwargs,
    ):
        stl_path = None
        if output_dir and file_basename:
            os.makedirs(output_dir, exist_ok=True)
            stl_path = os.path.join(output_dir, f"{file_basename}.stl")
            with open(stl_path, "w", encoding="utf-8") as f:
                f.write("solid s endsolid s")
        return ("cube(10);", "busy_cube", ["Red"], [], stl_path)

    monkeypatch.setattr(gt_bot, "generate_openscad_validated", fake_gen)

    mock_bambu_instance = MagicMock()
    mock_bambu_instance.is_printer_busy.return_value = (
        True,
        "Printer is currently RUNNING (50% complete)",
    )
    mock_bambu_cls = MagicMock(return_value=mock_bambu_instance)
    monkeypatch.setattr(gt_bot, "BambuClient", mock_bambu_cls)

    sub_calls = []
    monkeypatch.setattr(
        gt_bot.subprocess, "run", lambda *a, **k: sub_calls.append(a) or MagicMock(returncode=0)
    )

    mock_service = MagicMock()
    mock_service.tasks().list.return_value.execute.return_value = {
        "items": [{"id": "task_busy_1", "title": "3D Print: Desk Organizer", "notes": ""}]
    }

    gt_bot.process_tasks(mock_service)

    # Printer was checked via BambuClient.is_printer_busy()
    mock_bambu_instance.connect_mqtt.assert_called_once()
    mock_bambu_instance.is_printer_busy.assert_called_once()
    mock_bambu_instance.disconnect_mqtt.assert_called_once()

    # Auto-print subprocess (manual_print.py) must NOT have been called
    assert len(sub_calls) == 0

    # Session was saved on disk and task was marked completed
    sessions = [d for d in os.listdir(tmp_path) if os.path.isdir(tmp_path / d)]
    assert len(sessions) == 1
    assert "_busy_cube_" in sessions[0]
    s_json = json.loads((tmp_path / sessions[0] / "session.json").read_text(encoding="utf-8"))
    assert s_json["title"] == "busy_cube"
    assert s_json["versions"] == 1
    mock_service.tasks().patch.assert_called_once_with(
        tasklist="@default", task="task_busy_1", body={"status": "completed"}
    )


def test_google_tasks_uses_hybrid_timestamp_naming(tmp_path, monkeypatch):
    import re
    from src.bots.google_tasks import bot as gt_bot

    monkeypatch.setattr(gt_bot, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(gt_bot, "get_openscad_path", lambda: "openscad")
    monkeypatch.setattr(gt_bot, "get_google_tasks_supports_config", lambda: False)

    def fake_gen(prompt, history, max_calls, service, **kwargs):
        return ("sphere(10);", "Cable Clip!", ["Blue"], [], None)

    monkeypatch.setattr(gt_bot, "generate_openscad_validated", fake_gen)

    mock_bambu_instance = MagicMock()
    mock_bambu_instance.is_printer_busy.return_value = (False, "Printer is idle")
    monkeypatch.setattr(gt_bot, "BambuClient", MagicMock(return_value=mock_bambu_instance))
    monkeypatch.setattr(gt_bot.subprocess, "run", lambda *a, **k: MagicMock(returncode=0))

    mock_service = MagicMock()
    mock_service.tasks().list.return_value.execute.return_value = {
        "items": [{"id": "task_hybrid_1", "title": "3D Print: Cable Clip", "notes": ""}]
    }

    gt_bot.process_tasks(mock_service)

    folders = [d for d in os.listdir(tmp_path) if os.path.isdir(tmp_path / d)]
    assert len(folders) == 1
    assert re.match(r"^\d{8}_\d{6}_cable_clip_[a-f0-9]{8}$", folders[0])
    assert (tmp_path / folders[0] / "v1" / "print.bat").exists()


def test_authenticate_google_tasks_corrupted_token_file_recovers(tmp_path, monkeypatch):
    from src.bots.google_tasks import bot as gt_bot

    bad_tok = tmp_path / "google_tasks_token.json"
    bad_tok.write_text('{"token": "t"}', encoding="utf-8")
    monkeypatch.setattr(gt_bot, "TOKEN_PATH", str(bad_tok))
    monkeypatch.setattr(gt_bot, "CREDENTIALS_PATH", str(tmp_path / "missing_creds.json"))
    monkeypatch.setattr(
        gt_bot,
        "load_api_keys",
        lambda: {"GOOGLE_TASKS_CLIENT_ID": "cid", "GOOGLE_TASKS_CLIENT_SECRET": "csec"},
    )

    fresh_creds = MagicMock()
    fresh_creds.to_json.return_value = (
        '{"token": "ok", "refresh_token": "r", "client_id": "cid", "client_secret": "csec"}'
    )
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = fresh_creds
    monkeypatch.setattr(gt_bot.InstalledAppFlow, "from_client_config", lambda *a, **k: mock_flow)

    creds = gt_bot.authenticate_google_tasks()
    assert creds == fresh_creds
    assert "refresh_token" in bad_tok.read_text(encoding="utf-8")

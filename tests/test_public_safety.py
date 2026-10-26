import json
import os
import re
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

from src.core.config import ROOT_DIR
from src.web.backend.app import app
import src.web.backend.routes.printer as printer_route


@pytest.fixture
def client():
    return TestClient(app)


def test_server_binds_localhost_only():
    """Verify that backend server configurations explicitly bind to 127.0.0.1 and never 0.0.0.0."""
    app_py = os.path.join(ROOT_DIR, "src", "web", "backend", "app.py")
    with open(app_py, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'host="127.0.0.1"' in content
    assert "0.0.0.0" not in content

    launcher_py = os.path.join(ROOT_DIR, "src", "launcher.py")
    with open(launcher_py, "r", encoding="utf-8") as f:
        launcher_content = f.read()
    assert "--host 127.0.0.1" in launcher_content

    supervisor_py = os.path.join(ROOT_DIR, "src", "core", "supervisor.py")
    with open(supervisor_py, "r", encoding="utf-8") as f:
        supervisor_content = f.read()
    assert '"127.0.0.1"' in supervisor_content


def test_cors_restricted_to_localhost():
    """Verify that CORS does not permit wildcard access."""
    app_py = os.path.join(ROOT_DIR, "src", "web", "backend", "app.py")
    with open(app_py, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'allow_origins=["*"]' not in content
    assert "http://localhost:5173" in content
    assert "http://127.0.0.1:5173" in content


def test_status_endpoint_unconfigured_bambu(client, monkeypatch):
    """Verify /status returns configured: False and friendly guidance when credentials are empty."""
    mock_bambu = MagicMock()
    mock_bambu.device_serial = ""
    mock_bambu.auth_token = ""
    mock_bambu.access_code = ""
    mock_bambu.username = ""
    mock_bambu.password = ""
    mock_bambu.is_connected.return_value = False
    mock_bambu.get_status.return_value = {}
    mock_bambu.get_formatted_status.return_value = "Printer status unavailable."

    monkeypatch.setattr(printer_route, "get_bambu_cloud", lambda: mock_bambu)

    res = client.get("/status")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is False
    assert data["connected"] is False
    assert data["state"] == "NOT_CONFIGURED"
    assert "Design & Download mode active" in data["formatted"]


def test_print_session_unconfigured_bambu_error(client, tmp_path, monkeypatch):
    """Verify print_session returns 400 detailing how to configure .env when credentials are missing."""
    test_ws = str(tmp_path / "sessions")
    sid = "test_unconfigured_session"
    v1_dir = os.path.join(test_ws, sid, "v1")
    os.makedirs(v1_dir, exist_ok=True)

    with open(os.path.join(test_ws, sid, "session.json"), "w", encoding="utf-8") as f:
        json.dump({"id": sid, "versions": 1}, f)
    with open(os.path.join(v1_dir, "model.stl"), "w", encoding="utf-8") as f:
        f.write("solid test endsolid test")

    mock_bambu = MagicMock()
    mock_bambu.device_serial = ""
    mock_bambu.auth_token = ""
    mock_bambu.access_code = ""
    mock_bambu.username = ""
    mock_bambu.password = ""
    mock_bambu.is_connected.return_value = False

    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)
    monkeypatch.setattr(printer_route, "get_bambu_cloud", lambda: mock_bambu)

    res = client.post(f"/sessions/{sid}/print", json={"ams_slot": 0})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "Bambu printer credentials are not configured in .env" in detail
    assert "BAMBU_DEVICE_SERIAL" in detail


def test_snapshot_unconfigured_bambu_error(client, monkeypatch):
    """Verify /snapshot safely returns 500 when printer snapshot capture fails."""
    mock_bambu = MagicMock()
    mock_bambu.device_serial = ""
    mock_bambu.auth_token = ""
    mock_bambu.access_code = ""
    mock_bambu.username = ""
    mock_bambu.password = ""
    mock_bambu.ip = ""
    mock_bambu.get_snapshot.return_value = None

    monkeypatch.setattr(printer_route, "get_bambu_cloud", lambda: mock_bambu)

    res = client.get("/snapshot")
    assert res.status_code == 500
    assert "Failed to capture snapshot" in res.json()["detail"]


def test_telegram_missing_token_guidance(monkeypatch, capsys):
    """Verify telegram main() outputs setup guide and exits cleanly when token is missing."""
    import src.bots.telegram.bot as tg_bot

    monkeypatch.setattr(tg_bot, "TOKEN", "")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        tg_bot.main()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "TELEGRAM_BOT_TOKEN is not configured in .env" in captured.out
    assert "@BotFather" in captured.out


def test_google_tasks_missing_credentials_guidance(monkeypatch, capsys):
    """Verify google tasks bot outputs setup guide and exits cleanly when credentials are missing."""
    import src.bots.google_tasks.bot as gt_bot

    monkeypatch.setattr(gt_bot, "load_api_keys", lambda: {})
    monkeypatch.setattr(gt_bot, "TOKEN_PATH", "non_existent_token.json")
    monkeypatch.setattr(gt_bot, "CREDENTIALS_PATH", "non_existent_creds.json")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        gt_bot.authenticate_google_tasks()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "GOOGLE_TASKS_CLIENT_ID or GOOGLE_TASKS_CLIENT_SECRET not set in .env" in captured.out


def test_no_personal_paths_or_keys_in_tracked_code():
    """Scan all source code files to ensure no personal paths or secret patterns remain."""
    patterns = [
        re.compile(r"C:\\Users\\[a-zA-Z0-9_-]+", re.IGNORECASE),
        re.compile(r"\b10\.0\.0\.\d+\b"),
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
        re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
    ]

    scan_dirs = [
        os.path.join(ROOT_DIR, "src"),
        os.path.join(ROOT_DIR, "inputs"),
        os.path.join(ROOT_DIR, "scripts"),
    ]

    offending = []
    for s_dir in scan_dirs:
        for root, dirs, files in os.walk(s_dir):
            dirs[:] = [
                d
                for d in dirs
                if d
                not in (
                    "node_modules",
                    "dist",
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                )
            ]
            for file in files:
                if file.endswith((".py", ".json", ".js", ".jsx", ".md")):
                    fpath = os.path.join(root, file)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    for idx, line in enumerate(lines, 1):
                        for pat in patterns:
                            # Allow example system_config.default.json sample paths and test regexes
                            if (
                                "OpenSCAD" in line
                                or "Bambu" in line
                                or "OrcaSlicer" in line
                                or "Prusa" in line
                            ):
                                continue
                            if pat.search(line):
                                offending.append(f"{fpath}:{idx}: {line.strip()}")

    assert not offending, "Found personal paths or secrets:\n" + "\n".join(offending)

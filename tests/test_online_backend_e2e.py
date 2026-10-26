from fastapi.testclient import TestClient
from unittest.mock import patch
import os

from src.web.backend.app import app

client = TestClient(app)


def test_online_backend_e2e(tmp_path):
    with (
        patch("src.web.backend.routes.sessions.generate_with_validation") as mock_gen,
        patch("src.web.backend.routes.sessions.subprocess.Popen") as mock_popen,
        patch("src.web.backend.routes.sessions.subprocess.run") as mock_run,
        patch("src.web.backend.routes.sessions.shared_slicer") as mock_slice,
        patch("src.web.backend.routes.printer.bambu_cloud") as mock_cloud,
        patch("src.web.backend.routes.sessions.WORKSPACE_DIR", str(tmp_path / "created_stls")),
    ):
        os.makedirs(str(tmp_path / "created_stls"), exist_ok=True)
        mock_proc_instance = mock_popen.return_value
        mock_proc_instance.communicate.return_value = ("", "")
        mock_proc_instance.returncode = 0
        mock_proc_instance.poll.return_value = 0
        mock_gen.return_value = [
            {"type": "result", "code": "cube(10);", "filename": "cube", "colors": ["Red"]}
        ]
        mock_run.return_value.returncode = 0
        mock_slice.return_value = str(tmp_path / "test.3mf")
        mock_cloud.is_connected.return_value = True
        mock_cloud.is_printer_busy.return_value = (False, "Idle")
        mock_cloud.send_print_job.return_value = True
        mock_cloud.get_available_colors.return_value = {
            1: {"type": "PLA", "color": "FF0000", "remain": 100}
        }

        response = client.post("/sessions/new", json={"prompt": "make a cube"})
        assert response.status_code == 200

        response = client.get("/sessions")
        assert response.status_code == 200
        sessions = response.json()
        assert len(sessions) > 0

        session_id = sessions[0]["id"]

        response = client.post(f"/sessions/{session_id}/print")
        assert response.status_code == 200

        response = client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200

        response = client.get("/sessions")
        assert not any(s["id"] == session_id for s in response.json())

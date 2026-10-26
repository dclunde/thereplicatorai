from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.core.supervisor import set_global_supervisor
from src.web.backend.app import app

client = TestClient(app)


def test_health_check_endpoint():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_supervisor_status_endpoint():
    res = client.get("/api/supervisor/status")
    assert res.status_code == 200
    data = res.json()
    assert "online_backend" in data
    assert "telegram_bot" in data


def test_supervisor_restarts_endpoint():
    res = client.get("/api/supervisor/restarts?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "events" in data
    assert "count" in data


def test_supervisor_logs_endpoint():
    res = client.get("/api/supervisor/logs/online_backend?lines=10")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "online_backend"
    assert "lines" in data


def test_supervisor_restart_endpoint_unattached():
    # When no global supervisor is active, should return 503
    set_global_supervisor(None)
    res = client.post("/api/supervisor/restart/telegram_bot")
    assert res.status_code == 503


def test_supervisor_restart_endpoint_active():
    mock_sup = MagicMock()
    mock_sup.services = {"telegram_bot": MagicMock()}
    mock_sup.restart_service.return_value = True

    set_global_supervisor(mock_sup)
    try:
        res = client.post("/api/supervisor/restart/telegram_bot")
        assert res.status_code == 200
        assert res.json()["restarted"] is True
        mock_sup.restart_service.assert_called_once_with("telegram_bot", timeout=10.0)
    finally:
        set_global_supervisor(None)


def test_supervisor_reset_breaker_endpoint():
    mock_sup = MagicMock()
    set_global_supervisor(mock_sup)
    try:
        res = client.post("/api/supervisor/reset_breaker/telegram_bot")
        assert res.status_code == 200
        assert res.json()["reset"] is True
        mock_sup.circuit_breaker.reset.assert_called_once_with("telegram_bot")
    finally:
        set_global_supervisor(None)

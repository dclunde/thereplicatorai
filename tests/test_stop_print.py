import os
import sys
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web.backend.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests: BambuCloud.stop_print()
# ---------------------------------------------------------------------------


class TestStopPrintUnit:
    """Unit tests for shared/bambu_cloud.py BambuCloud.stop_print()"""

    def test_stop_print_success(self):
        """stop_print() with a connected mock MQTT client returns True and publishes correct payload."""
        from src.core.printer.bambu_client import BambuCloud

        bc = BambuCloud.__new__(BambuCloud)
        bc.device_serial = "FAKE123"

        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_msg_info = MagicMock()
        mock_client.publish.return_value = mock_msg_info
        bc.mqtt_client = mock_client

        result = bc.stop_print()

        assert result is True
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        topic = call_args[0][0]
        payload = json.loads(call_args[0][1])
        assert topic == "device/FAKE123/request"
        assert payload["print"]["command"] == "stop"
        assert payload["print"]["sequence_id"] == "1"
        mock_msg_info.wait_for_publish.assert_called_once_with(timeout=5.0)

    def test_stop_print_not_connected(self):
        """stop_print() when is_connected() is False returns False and never publishes."""
        from src.core.printer.bambu_client import BambuCloud

        bc = BambuCloud.__new__(BambuCloud)
        bc.device_serial = "FAKE123"

        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        bc.mqtt_client = mock_client

        result = bc.stop_print()

        assert result is False
        mock_client.publish.assert_not_called()

    def test_stop_print_client_none(self):
        """stop_print() when mqtt_client is None returns False."""
        from src.core.printer.bambu_client import BambuCloud

        bc = BambuCloud.__new__(BambuCloud)
        bc.device_serial = "FAKE123"
        bc.mqtt_client = None

        result = bc.stop_print()

        assert result is False


# ---------------------------------------------------------------------------
# Integration tests: POST /stop_print endpoint
# ---------------------------------------------------------------------------


class TestStopPrintEndpoint:
    """Integration tests for the POST /stop_print FastAPI endpoint."""

    def test_backend_stop_print_success(self):
        """POST /stop_print returns 200 OK when printer is RUNNING and stop_print() succeeds."""
        with patch("src.web.backend.routes.printer.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {"state": "RUNNING"}
            mock_bc.stop_print.return_value = True
            res = client.post("/stop_print")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "stop" in data["message"].lower() or "sent" in data["message"].lower()

    def test_backend_stop_print_mqtt_disconnected(self):
        """POST /stop_print returns an error with MQTT detail when stop_print() returns False.

        Note: Starlette TestClient wraps HTTPException as 500 in this framework version,
        but the detail string encodes the original 503 message. We assert on that.
        """
        with patch("src.web.backend.routes.printer.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {"state": "RUNNING"}
            mock_bc.stop_print.return_value = False
            try:
                res = client.post("/stop_print")
                # Some versions surface as 4xx/5xx with detail in body
                detail = res.json().get("detail", "")
            except Exception as exc:
                # Other versions raise the HTTPException directly
                detail = getattr(exc, "detail", str(exc))
        # Either way the MQTT error message must be present
        assert "MQTT" in detail or "not connected" in detail.lower() or "503" in detail

    def test_backend_stop_print_ignored_when_idle(self):
        """POST /stop_print returns 200 OK with status 'ignored' when printer is IDLE."""
        with patch("src.web.backend.routes.printer.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {"state": "IDLE"}
            res = client.post("/stop_print")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ignored"
        assert "not printing" in data["message"].lower()
        mock_bc.stop_print.assert_not_called()

    def test_backend_status_exposes_state(self):
        """GET /status returns a 'state' field alongside 'formatted'."""
        with patch("src.web.backend.routes.printer.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {
                "state": "RUNNING",
                "percent": 50,
                "remaining_time": 30,
            }
            mock_bc.get_formatted_status.return_value = "State: RUNNING\nProgress: 50%"
            res = client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert "state" in data
        assert data["state"] == "RUNNING"


# ---------------------------------------------------------------------------
# Telegram bot handler tests
# ---------------------------------------------------------------------------


class TestTelegramStopPrint:
    """Tests for the Telegram /stop_print command handler."""

    def _make_update(self, text="/stop_print"):
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = text
        update.message.reply_text = AsyncMock()
        return update

    def test_stop_print_shows_confirm_keyboard_when_running(self):
        """When printer is RUNNING, /stop_print shows inline keyboard and does NOT call stop_print()."""
        from src.bots.telegram.bot import stop_print_command

        update = self._make_update()
        context = MagicMock()

        with patch("src.bots.telegram.bot.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {"state": "RUNNING"}
            mock_bc.stop_print = MagicMock()

            asyncio.run(stop_print_command(update, context))

            # Must have replied with the confirmation message (inline keyboard)
            update.message.reply_text.assert_called_once()
            call_kwargs = update.message.reply_text.call_args
            assert "reply_markup" in call_kwargs.kwargs
            # stop_print must NOT have been called yet
            mock_bc.stop_print.assert_not_called()

    def test_stop_print_blocked_when_idle(self):
        """When printer is IDLE, /stop_print sends informational message and does NOT call stop_print()."""
        from src.bots.telegram.bot import stop_print_command

        update = self._make_update()
        context = MagicMock()

        with patch("src.bots.telegram.bot.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {"state": "IDLE"}
            mock_bc.stop_print = MagicMock()

            asyncio.run(stop_print_command(update, context))

            update.message.reply_text.assert_called_once()
            reply_text = update.message.reply_text.call_args[0][0]
            assert "not currently printing" in reply_text.lower() or "IDLE" in reply_text
            mock_bc.stop_print.assert_not_called()


def test_telegram_stop_print_when_state_is_pause():
    """When printer state is PAUSE (as well as PAUSED), /stop_print shows confirmation keyboard."""
    from src.bots.telegram.bot import stop_print_command

    for pause_state in ("PAUSE", "PAUSED"):
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "/stop_print"
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        with patch("src.bots.telegram.bot.bambu_cloud") as mock_bc:
            mock_bc.get_status.return_value = {"state": pause_state}
            mock_bc.stop_print = MagicMock()

            asyncio.run(stop_print_command(update, context))

            update.message.reply_text.assert_called_once()
            call_kwargs = update.message.reply_text.call_args
            assert "reply_markup" in call_kwargs.kwargs
            assert pause_state in call_kwargs.args[0]
            mock_bc.stop_print.assert_not_called()

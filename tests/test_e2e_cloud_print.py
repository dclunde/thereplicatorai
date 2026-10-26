import pytest
from unittest.mock import patch, MagicMock
import json

from src.core.printer.bambu_client import BambuCloud


@pytest.fixture
def mock_cloud():
    with patch("src.core.printer.bambu_client.mqtt.Client") as mock_mqtt:
        cloud = BambuCloud("test_ip", "test_user", "test_pass", "test_serial", "test_code")
        yield cloud, mock_mqtt


def test_e2e_cloud_print(mock_cloud, tmp_path):
    cloud, mock_mqtt = mock_cloud
    mock_mqtt_client = mock_mqtt.return_value
    cloud.mqtt_client = mock_mqtt_client

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        with patch.object(cloud, "upload_3mf", return_value=True):
            dummy_3mf = tmp_path / "test.3mf"
            dummy_3mf.write_text("dummy content")

            cloud.send_print_job(str(dummy_3mf), ams_slot=1)

            assert mock_mqtt_client.publish.call_count == 1

            cloud.last_status["percent"] = 99
            cloud.last_status["state"] = "PRINTING"

            msg = MagicMock()
            msg.payload = json.dumps(
                {"print": {"mc_percent": 100, "gcode_state": "FINISH"}}
            ).encode("utf-8")

            cloud._on_message(mock_mqtt_client, None, msg)
            assert cloud.last_status["percent"] == 100
            assert cloud.last_status["state"] == "FINISH"

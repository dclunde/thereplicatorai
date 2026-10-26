import os
import sys
import json
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.printer.bambu_client import BambuCloud


def test_on_message_parsing():
    print("Testing BambuCloud message parsing...")
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")

    # 1. Simulate a message with print progress and AMS info
    mock_payload = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 45,
            "mc_remaining_time": 120,  # 120 mins
            "bed_temper": 60,
            "nozzle_temper": 220,
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_color": "FF0000FF",
                                "tray_type": "PLA",
                                "remain": 100,
                                "tag_uid": "mock_tag_123",
                            },
                            {
                                "id": "1",
                                "tray_color": "00FF00FF",
                                "tray_type": "PETG",
                                "remain": 50,
                                "tag_uid": "mock_tag_456",
                            },
                        ],
                    }
                ]
            },
            "vt_tray": {
                "id": "254",
                "tray_color": "FFFFFF00",
                "tray_type": "PLA",
                "remain": 80,
                "tag_uid": "mock_tag_ext",
            },
        }
    }

    mock_msg = MagicMock()
    mock_msg.payload = json.dumps(mock_payload).encode("utf-8")

    # Call _on_message directly to test parsing
    cloud._on_message(None, None, mock_msg)

    # Verify state updates
    assert cloud.last_status["state"] == "RUNNING"
    assert cloud.last_status["percent"] == 45
    assert cloud.last_status["remaining_time"] == 120

    # Check AMS parsing
    colors = cloud.get_available_colors()
    assert 0 in colors
    assert colors[0]["color"] == "FF0000"  # Should strip alpha
    assert colors[0]["type"] == "PLA"
    assert colors[0]["remain"] == 100

    assert 1 in colors
    assert colors[1]["color"] == "00FF00"

    # Check External Spool parsing (Slot 4)
    assert 4 in colors
    assert colors[4]["color"] == "FFFFFF"
    assert colors[4]["type"] == "PLA"
    assert colors[4]["is_external"] is True


def main():
    test_on_message_parsing()
    print("All test_bambu_cloud_behavior passed!")


if __name__ == "__main__":
    main()


# 4. Bug 4: Premature AMS MQTT Loop Abortion
def test_bug4_premature_ams_loop():
    """Ensure ams_data_received is not set prematurely on empty payload."""
    bc = BambuCloud("u", "p", "s")
    bc.ams_data_received = False

    # A status message that has "print" but no "ams"
    mock_msg = MagicMock()
    mock_msg.payload.decode.return_value = json.dumps(
        {"print": {"gcode_state": "RUNNING", "mc_percent": 15}}
    )

    bc._on_message(None, None, mock_msg)

    assert bc.ams_data_received is False, "Should not be true without AMS data"
    assert bc.last_status["state"] == "RUNNING"


# --- Bug 14: Snapshot Transmission Cleanup, Fast-Fail, and URL-Quoting ---
def test_bug14_snapshot_transmission_cleanup(tmp_path, monkeypatch):
    import sys
    import time
    from src.core.printer import bambu_client

    monkeypatch.setattr(bambu_client, "SNAPSHOTS_DIR", str(tmp_path))

    # 1. Fast-fail when IP or access_code is missing (without turning on light or sleeping)
    unconfigured = BambuCloud("u", "p", "s", ip="", access_code="")
    light_called = []
    monkeypatch.setattr(unconfigured, "turn_on_light", lambda *a, **k: light_called.append(True))
    assert unconfigured.get_snapshot() is False
    assert light_called == []

    # 2. Create stale (>5 min) and fresh snapshots to verify cleanup_old_snapshots()
    old_snap = tmp_path / "snapshot_old_111111.jpg"
    old_snap.write_text("old_img", encoding="utf-8")
    old_mtime = time.time() - 600
    os.utime(str(old_snap), (old_mtime, old_mtime))

    fresh_snap = tmp_path / "snapshot_fresh_222222.jpg"
    fresh_snap.write_text("fresh_img", encoding="utf-8")

    # 3. Verify get_snapshot URL-quotes access_code and writes captured frame
    configured = BambuCloud("u", "p", "s", ip="192.168.1.99", access_code="p@ss:w/rd#1")
    monkeypatch.setattr(configured, "turn_on_light", lambda *a, **k: None)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    captured_urls = []

    class FakeCap:
        def __init__(self, url, api):
            captured_urls.append(url)

        def isOpened(self):
            return True

        def read(self):
            return True, "fake_frame"

        def release(self):
            pass

    fake_cv2 = MagicMock()
    fake_cv2.CAP_FFMPEG = 1900
    fake_cv2.VideoCapture.side_effect = FakeCap
    fake_cv2.imwrite.side_effect = lambda path, frame: open(path, "w").write("captured")
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    target_img = str(tmp_path / "out_snap.jpg")
    out_path = configured.get_snapshot(target_img)
    assert out_path == target_img
    assert os.path.exists(target_img)
    assert len(captured_urls) == 1
    assert "p%40ss%3Aw%2Frd%231@192.168.1.99:322" in captured_urls[0]

    # Stale snapshot was cleaned up, fresh snapshot was kept
    assert not old_snap.exists()
    assert fresh_snap.exists()


def test_on_message_empty_ams_dict_still_parses_vt_tray_and_lights():
    bc = BambuCloud("u", "p", "s")
    bc._schedule_light_off(delay=300)
    assert bc._light_off_timer is not None

    # Payload has empty "ams": {} alongside valid "vt_tray" and "lights_report"
    payload = {
        "print": {
            "gcode_state": "IDLE",
            "ams": {},
            "vt_tray": {
                "id": "254",
                "tray_color": "00AAFFFF",
                "tray_type": "PLA",
                "remain": 90,
                "tag_uid": "uid123",
            },
            "lights_report": [{"node": "chamber_light", "mode": "off"}],
        }
    }
    mock_msg = MagicMock()
    mock_msg.payload.decode.return_value = json.dumps(payload)

    bc._on_message(None, None, mock_msg)

    assert bc.ams_data_received is False
    assert 4 in bc.ams_colors
    assert bc.ams_colors[4]["color"] == "00AAFF"
    assert bc.ams_colors[4]["type"] == "PLA"
    assert bc.ams_colors[4]["is_external"] is True
    assert bc._light_off_timer is None


# --- Bug 18: Light Control Throttling Order ---
def test_bug18_turn_on_light_throttling_order():
    bambu = BambuCloud("u", "p", "s")
    bambu.mqtt_client = MagicMock()
    bambu.turn_on_light()
    assert hasattr(bambu, "_last_light_on")


# --- Bug 19: Unquoted Credentials Safety ---
def test_bug19_send_print_job_missing_credentials():
    bambu = BambuCloud("u", "p", "s")
    assert bambu.send_print_job("dummy.stl") is False


def test_send_print_job_slot_4_external_spool(monkeypatch, tmp_path):
    """Slot 4 uses use_ams=False and ams_mapping=[] while AMS slots use use_ams=True."""
    dummy_file = tmp_path / "model.3mf"
    dummy_file.write_text("fake 3mf")

    bambu = BambuCloud("u", "p", "s", ip="192.168.1.100", access_code="12345678")
    mock_mqtt = MagicMock()
    bambu.mqtt_client = mock_mqtt

    # Mock curl execution to succeed
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stderr="")
    )

    # Test slot 4 (External Spool)
    published_cmds = []
    mock_mqtt.publish.side_effect = lambda topic, payload: published_cmds.append(
        json.loads(payload)
    )

    success = bambu.send_print_job(str(dummy_file), ams_slot=4)
    assert success is True
    assert len(published_cmds) == 1
    assert published_cmds[0]["print"]["use_ams"] is False
    assert published_cmds[0]["print"]["ams_mapping"] == []

    # Test slot 1 (AMS Slot 1)
    published_cmds.clear()
    success = bambu.send_print_job(str(dummy_file), ams_slot=1)
    assert success is True
    assert len(published_cmds) == 1
    assert published_cmds[0]["print"]["use_ams"] is True
    assert published_cmds[0]["print"]["ams_mapping"] == [1]

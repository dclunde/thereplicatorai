import json
import time
from unittest.mock import MagicMock
from src.core.printer.bambu_client import BambuCloud


def test_is_printing_states():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")

    # Default / empty state should count as UNKNOWN -> is_printing is True
    assert cloud.is_printing() is True

    # Active / uncertain printing states
    for state in ["RUNNING", "PREPARE", "PAUSE", "UNKNOWN"]:
        cloud.last_status["state"] = state
        assert cloud.is_printing() is True, f"Expected {state} to be considered printing"

    # Non-printing states
    for state in ["IDLE", "FINISH", "FAILED"]:
        cloud.last_status["state"] = state
        assert cloud.is_printing() is False, f"Expected {state} to be considered not printing"


def test_turn_on_light_schedules_timer_when_not_printing():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    cloud.chamber_light_auto_off_delay = 50.0

    cloud.turn_on_light(auto_off=True)

    assert cloud._light_off_timer is not None
    assert cloud._light_off_timer.is_alive()
    cloud._cancel_light_off_timer()


def test_turn_on_light_no_timer_when_printing():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "RUNNING"

    cloud.turn_on_light(auto_off=True)

    assert cloud._light_off_timer is None


def test_turn_on_light_no_timer_when_auto_off_false():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"

    cloud.turn_on_light(auto_off=False)

    assert cloud._light_off_timer is None


def test_turn_on_light_resets_timer_on_subsequent_activity():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    cloud.chamber_light_auto_off_delay = 100.0

    cloud.turn_on_light(auto_off=True)
    first_timer = cloud._light_off_timer
    assert first_timer is not None

    # Trigger activity again
    cloud.turn_on_light(auto_off=True)
    second_timer = cloud._light_off_timer
    assert second_timer is not None
    assert second_timer is not first_timer

    cloud._cancel_light_off_timer()


def test_auto_off_fires_and_publishes_off():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    # Use very short delay for fast test
    cloud.chamber_light_auto_off_delay = 0.05

    cloud.turn_on_light(auto_off=True)
    time.sleep(0.1)

    # Verify MQTT publish for 'off' command was made
    assert cloud.mqtt_client.publish.call_count >= 2
    last_call_args = cloud.mqtt_client.publish.call_args[0]
    payload = json.loads(last_call_args[1])
    assert payload["system"]["command"] == "ledctrl"
    assert payload["system"]["led_mode"] == "off"


def test_auto_off_does_not_turn_off_if_printer_started():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    cloud.chamber_light_auto_off_delay = 0.05

    cloud.turn_on_light(auto_off=True)
    # Printer starts printing before timer callback executes
    cloud.last_status["state"] = "RUNNING"
    time.sleep(0.1)

    # Verify no 'off' command was sent
    for call in cloud.mqtt_client.publish.call_args_list:
        payload = json.loads(call[0][1])
        if "system" in payload:
            assert payload["system"].get("led_mode") != "off"


def test_mqtt_finish_transition_schedules_timer():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "RUNNING"
    cloud.chamber_light_auto_off_delay = 50.0

    # Simulate MQTT report transitioning from RUNNING to FINISH
    mock_msg = MagicMock()
    mock_msg.payload = json.dumps({"print": {"gcode_state": "FINISH"}}).encode("utf-8")
    cloud._on_message(None, None, mock_msg)

    assert cloud.last_status["state"] == "FINISH"
    assert cloud._light_off_timer is not None
    assert cloud._light_off_timer.is_alive()
    cloud._cancel_light_off_timer()


def test_mqtt_running_transition_cancels_timer():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    cloud.chamber_light_auto_off_delay = 50.0

    # Start timer while idle
    cloud.turn_on_light(auto_off=True)
    assert cloud._light_off_timer is not None

    # Simulate transition to RUNNING
    mock_msg = MagicMock()
    mock_msg.payload = json.dumps({"print": {"gcode_state": "RUNNING"}}).encode("utf-8")
    cloud._on_message(None, None, mock_msg)

    assert cloud._light_off_timer is None


def test_turn_off_light_cancels_timer_and_publishes():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    cloud.chamber_light_auto_off_delay = 50.0

    cloud.turn_on_light(auto_off=True)
    assert cloud._light_off_timer is not None

    cloud.turn_off_light()
    assert cloud._light_off_timer is None
    last_call_args = cloud.mqtt_client.publish.call_args[0]
    payload = json.loads(last_call_args[1])
    assert payload["system"]["led_mode"] == "off"


def test_disconnect_mqtt_cleans_up_timer():
    cloud = BambuCloud("user", "pass", "serial", "ip", "code")
    cloud.mqtt_client = MagicMock()
    cloud.last_status["state"] = "IDLE"
    cloud.chamber_light_auto_off_delay = 50.0

    cloud.turn_on_light(auto_off=True)
    assert cloud._light_off_timer is not None

    cloud.disconnect_mqtt()
    assert cloud._light_off_timer is None

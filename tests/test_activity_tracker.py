import os
from unittest.mock import MagicMock

from src.core.activity_tracker import (
    busy_activity,
    clear_busy_activity,
    get_active_lease,
    is_system_idle,
    set_busy_activity,
)


def test_busy_activity_context_manager(tmp_path):
    act_file = str(tmp_path / ".system_activity.json")
    lock_file = str(tmp_path / ".system_activity.lock")

    assert not os.path.exists(act_file)
    with busy_activity(
        "OpenSCAD_compile",
        lease_seconds=60,
        activity_path=act_file,
        lock_path=lock_file,
    ):
        assert os.path.exists(act_file)
        lease = get_active_lease(activity_path=act_file, lock_path=lock_file)
        assert lease is not None
        assert lease["activity_name"] == "OpenSCAD_compile"

    # Cleared on exit
    assert not os.path.exists(act_file)
    assert get_active_lease(activity_path=act_file, lock_path=lock_file) is None


def test_lease_auto_expiry(tmp_path):
    act_file = str(tmp_path / ".system_activity.json")
    lock_file = str(tmp_path / ".system_activity.lock")

    set_busy_activity(
        "Slicing_3MF",
        lease_seconds=10.0,
        activity_path=act_file,
        lock_path=lock_file,
    )

    # Immediately active
    lease = get_active_lease(activity_path=act_file, lock_path=lock_file)
    assert lease is not None

    # Check at time in future (expiry exceeded)
    future_time = lease["expires_at"] + 5.0
    expired_lease = get_active_lease(
        activity_path=act_file,
        lock_path=lock_file,
        current_time=future_time,
    )
    assert expired_lease is None


def test_is_system_idle_with_printer_mock(tmp_path):
    act_file = str(tmp_path / ".system_activity.json")
    lock_file = str(tmp_path / ".system_activity.lock")
    clear_busy_activity(activity_path=act_file, lock_path=lock_file)

    # Idle with no printer
    idle, msg = is_system_idle(
        check_printer=False,
        activity_path=act_file,
        lock_path=lock_file,
    )
    assert idle is True
    assert "idle" in msg.lower()

    # Printer busy
    mock_bambu = MagicMock()
    mock_bambu.is_printer_busy.return_value = (True, "Printer is currently RUNNING (45% complete)")

    idle, msg = is_system_idle(
        check_printer=True,
        bambu_client=mock_bambu,
        activity_path=act_file,
        lock_path=lock_file,
    )
    assert idle is False
    assert "RUNNING" in msg

    # Printer idle
    mock_bambu.is_printer_busy.return_value = (False, "Printer is idle")
    idle, msg = is_system_idle(
        check_printer=True,
        bambu_client=mock_bambu,
        activity_path=act_file,
        lock_path=lock_file,
    )
    assert idle is True


def test_is_system_idle_respects_dispatch_lock_and_printer_state(tmp_path, monkeypatch):
    from src.core.printer import bambu_client

    act_file = str(tmp_path / ".system_activity.json")
    lock_file = str(tmp_path / ".system_activity.lock")
    dispatch_lock = str(tmp_path / "printer_dispatch.lock")
    monkeypatch.setattr(bambu_client, "LOCK_FILE", dispatch_lock)

    mock_bambu = MagicMock()
    mock_bambu.is_printer_busy.return_value = (False, "Printer is idle")

    # Acquire dispatch lock -> system must report busy even before MQTT state flips
    assert bambu_client.acquire_dispatch_lock(timeout_seconds=60.0) is True
    try:
        idle, msg = is_system_idle(
            check_printer=True,
            bambu_client=mock_bambu,
            activity_path=act_file,
            lock_path=lock_file,
        )
        assert idle is False
        assert "dispatch lock" in msg.lower()
    finally:
        bambu_client.release_dispatch_lock()

    idle_after, _ = is_system_idle(
        check_printer=True,
        bambu_client=mock_bambu,
        activity_path=act_file,
        lock_path=lock_file,
    )
    assert idle_after is True


def test_generation_sets_and_clears_busy_activity(tmp_path, monkeypatch):
    import src.core.activity_tracker as at
    from src.cli import simple_mode
    import src.core.ai.validation_loop as ai_call

    act_file = str(tmp_path / ".system_activity.json")
    lock_file = str(tmp_path / ".system_activity.lock")
    monkeypatch.setattr(at, "ACTIVITY_FILE", act_file)
    monkeypatch.setattr(at, "LOCK_FILE", lock_file)

    # Patch default args of set_busy_activity and clear_busy_activity through busy_activity
    orig_busy = at.busy_activity

    def patched_busy(name, lease_seconds=300.0, metadata=None):
        return orig_busy(
            name,
            lease_seconds=lease_seconds,
            metadata=metadata,
            activity_path=act_file,
            lock_path=lock_file,
        )

    monkeypatch.setattr(simple_mode, "busy_activity", patched_busy)
    monkeypatch.setattr(simple_mode, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(simple_mode, "ensure_output_dirs", lambda: None)
    monkeypatch.setattr(simple_mode, "_beep", lambda: None)
    monkeypatch.setattr(ai_call, "get_max_ai_calls", lambda *_: 1)

    observed_during_gen = []

    def fake_gen(*a, **k):
        lease = get_active_lease(activity_path=act_file, lock_path=lock_file)
        observed_during_gen.append(lease)
        stl = tmp_path / "gen.stl"
        stl.write_text("solid s endsolid s", encoding="utf-8")
        return ("cube(5);", "Busy Test", [], [], str(stl), None)

    monkeypatch.setattr(ai_call, "generate_openscad_validated", fake_gen)
    answers = iter(["Y", "N"])
    monkeypatch.setattr(simple_mode, "_prompt_with_timeout", lambda *a, **k: next(answers))
    monkeypatch.setattr(simple_mode, "get_bambu_studio_path", lambda: "fake_slicer")
    monkeypatch.setattr(simple_mode.subprocess, "Popen", lambda *a, **k: None)

    assert simple_mode.main("test activity lease") == 0
    assert len(observed_during_gen) == 1
    assert observed_during_gen[0] is not None
    assert observed_during_gen[0]["activity_name"] == "cli_generation"
    # Cleared after generation completes
    assert get_active_lease(activity_path=act_file, lock_path=lock_file) is None

import os

from src.core.supervisor_logging import (
    get_service_logger,
    log_lifecycle_event,
    read_lifecycle_events,
    read_service_log_tail,
)


def test_log_lifecycle_event_schema(tmp_path):
    journal = str(tmp_path / "lifecycle_events.jsonl")
    ev = log_lifecycle_event(
        event_type="SERVICE_START",
        service="test_svc",
        pid=12345,
        reason="Unit test start",
        journal_path=journal,
    )

    assert ev["event_type"] == "SERVICE_START"
    assert ev["service"] == "test_svc"
    assert ev["pid"] == 12345
    assert ev["reason"] == "Unit test start"
    assert "timestamp" in ev
    assert "local_time" in ev
    assert "git_commit" in ev

    # Verify journal file was written
    assert os.path.exists(journal)
    events = read_lifecycle_events(journal_path=journal)
    assert len(events) == 1
    assert events[0]["pid"] == 12345


def test_read_lifecycle_events_filtering(tmp_path):
    journal = str(tmp_path / "lifecycle_events.jsonl")
    log_lifecycle_event("SERVICE_START", service="svc1", pid=1, journal_path=journal)
    log_lifecycle_event("SERVICE_CRASH", service="svc1", pid=1, exit_code=1, journal_path=journal)
    log_lifecycle_event("SERVICE_START", service="svc2", pid=2, journal_path=journal)

    # Latest first
    all_evs = read_lifecycle_events(journal_path=journal)
    assert len(all_evs) == 3
    assert all_evs[0]["service"] == "svc2"

    svc1_evs = read_lifecycle_events(service="svc1", journal_path=journal)
    assert len(svc1_evs) == 2
    assert all(e["service"] == "svc1" for e in svc1_evs)

    crashes = read_lifecycle_events(event_type="SERVICE_CRASH", journal_path=journal)
    assert len(crashes) == 1
    assert crashes[0]["exit_code"] == 1


def test_service_logger_rotation_and_tail(tmp_path):
    logs_dir = str(tmp_path / "services")
    os.makedirs(logs_dir, exist_ok=True)

    # Create logger with reasonable max_bytes in isolated directory
    logger = get_service_logger("test_rotation", max_bytes=5000, backup_count=2, logs_dir=logs_dir)

    for i in range(20):
        logger.info(f"Log line number {i:03d} with extra padding to test logging")

    tail = read_service_log_tail("test_rotation", max_lines=5, logs_dir=logs_dir)
    assert len(tail) == 5
    assert "Log line number 019" in tail[-1]

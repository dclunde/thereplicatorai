from unittest.mock import MagicMock, patch

from src.core.supervisor import ProcessSupervisor


def test_supervisor_start_and_stop_service():
    sup = ProcessSupervisor(
        config={"services": {"online_backend": True}, "online_frontend_mode": "dev"}
    )

    mock_proc = MagicMock()
    mock_proc.pid = 98765
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.return_value = b""
    mock_proc.stderr.readline.return_value = b""

    with (
        patch("subprocess.Popen", return_value=mock_proc),
        patch("psutil.Process") as mock_psutil,
    ):
        mock_p_instance = MagicMock()
        mock_p_instance.children.return_value = []
        mock_psutil.return_value = mock_p_instance

        # Start
        started = sup.start_service("online_backend")
        assert started is True
        assert sup.services["online_backend"].status == "RUNNING"
        assert sup.services["online_backend"].pid == 98765

        # Stop
        stopped = sup.stop_service("online_backend")
        assert stopped is True
        assert sup.services["online_backend"].status == "STOPPED"
        assert sup.services["online_backend"].pid is None


def test_supervisor_watchdog_detects_clean_exit():
    sup = ProcessSupervisor(config={"services": {"online_backend": True}})
    svc = sup.services["online_backend"]
    svc.status = "RUNNING"
    svc.start_time = 1000.0

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0  # Clean exit code 0
    svc.popen = mock_proc

    sup.watchdog_tick()
    assert svc.status == "STOPPED"
    assert svc.exit_code == 0


def test_supervisor_watchdog_detects_crash_and_backoffs():
    sup = ProcessSupervisor(
        config={
            "services": {"online_backend": True},
            "logging": {"backoff_initial_seconds": 3.0},
        }
    )
    svc = sup.services["online_backend"]
    svc.status = "RUNNING"
    svc.start_time = 1000.0

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1  # Crash exit code 1
    svc.popen = mock_proc

    sup.watchdog_tick()
    assert svc.status == "BACKOFF"
    assert svc.exit_code == 1
    assert svc.next_restart_time > 0


def test_supervisor_watchdog_trips_circuit_breaker():
    sup = ProcessSupervisor(
        config={
            "services": {"online_backend": True},
            "logging": {"circuit_breaker_max_restarts": 2, "circuit_breaker_window_seconds": 60},
        }
    )
    svc = sup.services["online_backend"]

    # Crash 1
    svc.status = "RUNNING"
    mock_proc1 = MagicMock()
    mock_proc1.poll.return_value = 1
    svc.popen = mock_proc1
    sup.watchdog_tick()
    assert svc.status == "BACKOFF"

    # Crash 2 (exceeds threshold)
    svc.status = "RUNNING"
    mock_proc2 = MagicMock()
    mock_proc2.poll.return_value = 1
    svc.popen = mock_proc2
    sup.watchdog_tick()

    assert svc.status == "TRIPPED"
    assert sup.circuit_breaker.is_tripped("online_backend") is True


def test_supervisor_get_status():
    sup = ProcessSupervisor()
    status = sup.get_status()
    assert "online_backend" in status
    assert "telegram_bot" in status
    assert "google_tasks_bot" in status
    assert status["online_backend"]["status"] == "STOPPED"
    assert "circuit_breaker" in status["online_backend"]


def test_watchdog_tick_backoff_restart_does_not_deadlock():
    import threading
    import time

    sup = ProcessSupervisor(config={"services": {"online_backend": True}})
    svc = sup.services["online_backend"]
    svc.status = "BACKOFF"
    svc.next_restart_time = time.time() - 1.0  # Ready to restart now

    mock_proc = MagicMock()
    mock_proc.pid = 54321
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.return_value = b""
    mock_proc.stderr.readline.return_value = b""

    with patch("subprocess.Popen", return_value=mock_proc):
        t = threading.Thread(target=sup.watchdog_tick)
        t.start()
        t.join(timeout=3.0)
        assert not t.is_alive(), "watchdog_tick deadlocked when restarting a BACKOFF service!"
        assert svc.status == "RUNNING"
        assert svc.pid == 54321


def test_supervisor_pipe_reader_does_not_mark_stderr_info_as_err(capsys):
    from collections import deque
    import io

    sup = ProcessSupervisor()
    fake_stderr = io.BytesIO(
        b"INFO:     Started server process [17188]\nERROR:    Real crash line\n"
    )
    tail = deque(maxlen=10)
    with patch("src.core.supervisor.get_service_logger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        sup._pipe_reader(fake_stderr, "online_backend", True, tail)

    out = capsys.readouterr().out
    assert "ERR INFO:" not in out
    assert "[ONLINE_BACKEND]     INFO:     Started server process [17188]" in out
    assert "[ONLINE_BACKEND] ERR ERROR:    Real crash line" in out
    mock_logger.info.assert_called_once_with("INFO:     Started server process [17188]")
    mock_logger.warning.assert_called_once_with("ERROR:    Real crash line")


def test_telegram_bot_package_import_does_not_preload_bot_module():
    import subprocess
    import sys

    res = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::RuntimeWarning",
            "-c",
            "import src.bots.telegram; import sys; assert 'src.bots.telegram.bot' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"Unexpected stderr: {res.stderr}"

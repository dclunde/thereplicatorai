from src.core.supervisor_logging import CircuitBreaker


def test_circuit_breaker_exponential_backoff():
    cb = CircuitBreaker(
        max_restarts=5,
        window_seconds=180,
        backoff_initial_seconds=2.0,
        backoff_max_seconds=60.0,
    )

    t = 1000.0
    # 1st crash: 2s
    tripped, backoff, window_cnt, total_cnt = cb.record_crash("svc", t)
    assert not tripped
    assert backoff == 2.0
    assert window_cnt == 1
    assert total_cnt == 1

    # 2nd crash: 4s
    tripped, backoff, window_cnt, total_cnt = cb.record_crash("svc", t + 5)
    assert not tripped
    assert backoff == 4.0
    assert window_cnt == 2

    # 3rd crash: 8s
    tripped, backoff, window_cnt, total_cnt = cb.record_crash("svc", t + 10)
    assert not tripped
    assert backoff == 8.0
    assert window_cnt == 3

    # 4th crash: 16s
    tripped, backoff, window_cnt, total_cnt = cb.record_crash("svc", t + 15)
    assert not tripped
    assert backoff == 16.0
    assert window_cnt == 4

    # 5th crash: Trips circuit breaker!
    tripped, backoff, window_cnt, total_cnt = cb.record_crash("svc", t + 20)
    assert tripped is True
    assert backoff == 32.0
    assert window_cnt == 5
    assert cb.is_tripped("svc") is True


def test_circuit_breaker_rolling_window_expiry():
    cb = CircuitBreaker(max_restarts=3, window_seconds=60)
    t = 1000.0

    cb.record_crash("svc", t)
    cb.record_crash("svc", t + 10)

    # 3rd crash happens 70s after 1st crash (so 1st crash expired from window)
    tripped, backoff, window_cnt, total_cnt = cb.record_crash("svc", t + 70)
    assert not tripped
    assert window_cnt == 2  # Only 2 within the 60s window
    assert total_cnt == 3


def test_circuit_breaker_reset():
    cb = CircuitBreaker(max_restarts=2, window_seconds=60)
    cb.record_crash("svc", 1000.0)
    tripped, _, _, _ = cb.record_crash("svc", 1001.0)
    assert tripped is True
    assert cb.is_tripped("svc") is True

    # Manual reset
    cb.reset("svc")
    assert cb.is_tripped("svc") is False
    status = cb.get_status("svc", 1002.0)
    assert status["tripped"] is False
    assert status["restart_count_window"] == 0


def test_circuit_breaker_record_success_resets_backoff():
    cb = CircuitBreaker(max_restarts=5, backoff_initial_seconds=2.0)
    cb.record_crash("svc", 1000.0)
    cb.record_crash("svc", 1005.0)  # Backoff was 4s
    cb.record_success("svc")  # Successful recovery

    # Next crash should start back at initial backoff
    _, backoff, _, _ = cb.record_crash("svc", 1010.0)
    assert backoff == 2.0

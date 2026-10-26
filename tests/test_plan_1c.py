"""Comprehensive unit and integration tests for Plan 1c services and refactored modules."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.bots.google_tasks.bot import (
    get_google_tasks_supports_config,
    process_tasks as gt_process_tasks,
)
from src.bots.telegram.bot import (
    WAITING_FOR_DECISION,
    build_application,
    cancel_command,
    check_status,
    format_warnings_summary,
    prune_conversation_history,
    truncate_text,
)
from src.web.backend.app import app
from src.web.backend.jobs import (
    clear_active_job,
    get_active_job,
    set_active_job,
)
from src.web.backend.routes.sessions import (
    parse_and_strip_support_flags,
    sanitize_id,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Web Backend & Job Tracking Tests
# ---------------------------------------------------------------------------


def test_job_tracking_lifecycle():
    sid = "test_job_lifecycle"
    clear_active_job(sid)
    assert get_active_job(sid) is None

    set_active_job(sid, 1, "step", "Processing model...", 45)
    job = get_active_job(sid)
    assert job is not None
    assert job["target_version"] == 1
    assert job["status"] == "step"
    assert job["progress"] == 45
    assert job["message"] == "Processing model..."

    clear_active_job(sid)
    assert get_active_job(sid) is None


def test_web_backend_routes_registered():
    res = client.get("/sessions")
    assert res.status_code == 200
    res = client.get("/status")
    assert res.status_code in [200, 500]


def test_web_status_endpoint():
    with patch("src.web.backend.routes.printer.get_bambu_cloud") as mock_get_bc:
        mock_bc = MagicMock()
        mock_get_bc.return_value = mock_bc
        mock_bc.get_status.return_value = {"state": "IDLE", "percent": 0}
        mock_bc.is_connected.return_value = True
        mock_bc.get_formatted_status.return_value = "Printer: IDLE"

        res = client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is True
        assert data["state"] == "IDLE"
        assert "Printer: IDLE" in data["formatted"]


def test_web_stop_print_endpoint():
    with patch("src.web.backend.routes.printer.get_bambu_cloud") as mock_get_bc:
        mock_bc = MagicMock()
        mock_get_bc.return_value = mock_bc
        mock_bc.get_status.return_value = {"state": "RUNNING"}
        mock_bc.stop_print.return_value = True

        res = client.post("/stop_print")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


def test_web_parse_and_strip_support_flags():
    prompt, supp = parse_and_strip_support_flags("mini skull --support")
    assert prompt == "mini skull"
    assert supp is True

    prompt, supp = parse_and_strip_support_flags("mini skull --no-support")
    assert prompt == "mini skull"
    assert supp is False

    prompt, supp = parse_and_strip_support_flags("mini skull")
    assert prompt == "mini skull"
    assert supp is None


def test_web_sanitize_id():
    assert sanitize_id("my_project_123") == "my_project_123"
    assert sanitize_id("../../traversal") == "....traversal"
    assert sanitize_id("valid (name) [1]") == "valid (name) [1]"


# ---------------------------------------------------------------------------
# 2. Telegram Bot Module Decomposition Tests
# ---------------------------------------------------------------------------


def test_telegram_formatting_helpers():
    assert truncate_text("hello", 10) == "hello"
    assert truncate_text("a" * 20, 10).endswith("... [truncated]")

    assert format_warnings_summary([]) == ""
    warnings = ["Thin wall < 0.8mm", "Overhang > 45 deg", "Bed clearance tight"]
    formatted = format_warnings_summary(warnings, max_items=2)
    assert "- Thin wall < 0.8mm" in formatted
    assert "and 1 more physical constraint warning(s)" in formatted


@pytest.mark.asyncio
async def test_telegram_cancel_command_states():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    # Case 1: Active background job running
    context.user_data = {"is_processing": True, "cancel_token": {"cancelled": False}}
    state = await cancel_command(update, context)
    assert state == WAITING_FOR_DECISION
    assert context.user_data["cancel_token"]["cancelled"] is True
    assert context.user_data["is_cancelling"] is True

    # Case 2: Already cancelling
    state = await cancel_command(update, context)
    assert state == WAITING_FOR_DECISION

    # Case 3: Idle session cancellation
    context.user_data = {"is_processing": False, "is_cancelling": False, "some_data": 123}
    state = await cancel_command(update, context)
    assert state == -1  # ConversationHandler.END
    assert len(context.user_data) == 0


@pytest.mark.asyncio
async def test_telegram_check_status():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.user_data = {"design_state": "Slicing to 3MF..."}

    with patch("src.bots.telegram.handlers_printer.get_bambu_cloud") as mock_get_bc:
        mock_bc = MagicMock()
        mock_get_bc.return_value = mock_bc
        mock_bc.get_formatted_status.return_value = "Printer State: IDLE"

        await check_status(update, context)
        update.message.reply_text.assert_called_once()
        sent_text = update.message.reply_text.call_args[0][0]
        assert "Printer State: IDLE" in sent_text
        assert "Software Design State: Slicing to 3MF..." in sent_text


def test_telegram_prune_conversation_history():
    hist = [{"role": "user", "text": "root"}] + [
        {"role": "assistant" if i % 2 == 1 else "user", "text": f"turn {i}"} for i in range(1, 10)
    ]
    pruned = prune_conversation_history(hist, max_turns=4)
    assert len(pruned) == 5
    assert pruned[0]["text"] == "root"


def test_telegram_build_application():
    app_instance = build_application(token="123456789:ABCDefGhIJKlmNoPQRstuVWXyz")
    assert app_instance is not None


# ---------------------------------------------------------------------------
# 3. Google Tasks Bot Module Decomposition Tests
# ---------------------------------------------------------------------------


def test_google_tasks_supports_config():
    enabled = get_google_tasks_supports_config()
    assert isinstance(enabled, bool)


def test_google_tasks_process_tasks_ignores_non_3d_print():
    mock_service = MagicMock()
    mock_service.tasks().list.return_value.execute.return_value = {
        "items": [
            {"id": "t1", "title": "Buy milk", "notes": "From grocery store"},
            {"id": "t2", "title": "Call dentist", "notes": ""},
        ]
    }
    gt_process_tasks(mock_service)
    # Patch should NOT have been called for non 3D print tasks
    mock_service.tasks().patch.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Diagnostics Script Tests
# ---------------------------------------------------------------------------


def test_diagnostics_environment_check():
    from scripts.diagnostics import check_environment

    # Should run cleanly without raising exceptions
    check_environment()

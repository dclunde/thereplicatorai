"""Telegram bot integration package."""

from typing import Any

__all__ = [
    "bambu_cloud",
    "build_application",
    "main",
    "WAITING_FOR_DECISION",
    "check_status",
    "request_snapshot",
    "stop_print_command",
    "stop_print_callback",
    "start_print",
    "start_make",
    "start_design",
    "start_edit",
    "handle_document",
    "cancel_command",
    "enforce_cancel",
    "unknown_message",
    "error_handler",
    "monitor_print_status",
    "slice_stl_to_3mf",
    "process_prompt",
    "process_stl_for_print",
    "prune_conversation_history",
    "save_telegram_session_json",
    "safe_reply_text",
    "truncate_text",
    "format_warnings_summary",
    "is_physical_constraint_warning",
    "filter_physical_constraint_warnings",
    "log_server",
]


def __getattr__(name: str) -> Any:
    if name in __all__ or name == "bot":
        import importlib

        bot_mod = importlib.import_module("src.bots.telegram.bot")
        if name == "bot":
            return bot_mod
        return getattr(bot_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

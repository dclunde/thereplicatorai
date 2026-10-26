import asyncio
import os
import subprocess
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.bots.telegram.handlers_design import (
    MAX_AI_CALLS,
    cancel_command,
    enforce_cancel,
    error_handler,
    filter_physical_constraint_warnings,
    format_warnings_summary,
    handle_document,
    is_physical_constraint_warning,
    log_server,
    process_prompt,
    process_stl_for_print,
    prune_conversation_history,
    safe_reply_text,
    save_telegram_session_json,
    slice_stl_to_3mf,
    start_design,
    start_edit,
    start_make,
    truncate_text,
    unknown_message,
)
from src.bots.telegram.handlers_printer import (
    WAITING_FOR_DECISION,
    check_status,
    get_bambu_cloud,
    known_chat_ids,
    request_snapshot,
    start_print,
    stop_print_callback,
    stop_print_command,
)
from src.bots.telegram.monitor import monitor_print_status
from src.core.ai import generate_openscad_validated
from src.core.config import (
    ROOT_DIR,
    SESSIONS_DIR as WORKSPACE_DIR,
    SNAPSHOTS_DIR,
    load_api_keys,
)
from src.core.sessions import parse_and_strip_support_flags
from src.core.slicer import get_slice_info_from_3mf

keys = load_api_keys()
TOKEN = keys.get("TELEGRAM_BOT_TOKEN", "")
bambu_cloud = get_bambu_cloud()


__all__ = [
    "TOKEN",
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
    "known_chat_ids",
    "WORKSPACE_DIR",
    "SNAPSHOTS_DIR",
    "ROOT_DIR",
    "MAX_AI_CALLS",
    "subprocess",
    "generate_openscad_validated",
    "parse_and_strip_support_flags",
    "get_slice_info_from_3mf",
]


def build_application(token: str = None) -> Application:
    tok = token or TOKEN
    if not tok:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment.")

    app = (
        Application.builder()
        .token(tok)
        .read_timeout(120)
        .write_timeout(120)
        .pool_timeout(120)
        .connect_timeout(120)
        .build()
    )

    async def post_init(application: Application):
        asyncio.create_task(monitor_print_status(application))

    app.post_init = post_init

    app.add_handler(CommandHandler("status", check_status, block=False))
    app.add_handler(CommandHandler("snapshot", request_snapshot, block=False))
    app.add_handler(CommandHandler("stop_print", stop_print_command, block=False))
    app.add_handler(
        CallbackQueryHandler(
            stop_print_callback, pattern="^(confirm|cancel)_stop_print$", block=False
        )
    )

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("make", start_make, block=False),
            CommandHandler("design", start_design, block=False),
            MessageHandler(filters.Document.FileExtension("stl"), handle_document, block=False),
        ],
        states={
            WAITING_FOR_DECISION: [
                CommandHandler("edit", start_edit, block=False),
                CommandHandler("print", start_print, block=False),
                CommandHandler("status", check_status, block=False),
                CommandHandler("snapshot", request_snapshot, block=False),
                CommandHandler("cancel", cancel_command, block=False),
                MessageHandler(filters.ALL, enforce_cancel, block=False),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command, block=False)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("cancel", cancel_command, block=False))
    app.add_handler(MessageHandler(filters.TEXT | filters.COMMAND, unknown_message, block=False))
    app.add_error_handler(error_handler)

    return app


def main():
    mod = sys.modules.get("src.bots.telegram.bot")
    token = getattr(mod, "TOKEN", TOKEN) if mod else TOKEN
    if not token and not os.environ.get("PYTEST_CURRENT_TEST"):
        print(
            "\n"
            + "=" * 60
            + "\n"
            + " [!] TELEGRAM_BOT_TOKEN is not configured in .env.\n"
            + "     To use the Telegram bot:\n"
            + "     1. Message @BotFather on Telegram to create a bot.\n"
            + "     2. Add your token to .env: TELEGRAM_BOT_TOKEN=your_token\n"
            + "=" * 60
            + "\n"
        )
        sys.exit(0)
    elif not token:
        print("WARNING: TELEGRAM_BOT_TOKEN not found in .env. Please configure it.")
        sys.exit(1)

    try:
        import src.core.utils as core_utils

        core_utils.get_openscad_path()
    except FileNotFoundError as e:
        print(f"FATAL: OpenSCAD not installed or found. {e}")
        sys.exit(1)
    bc = getattr(mod, "bambu_cloud", None) if mod else None
    if bc is None:
        bc = get_bambu_cloud()
    bc.connect_mqtt()

    app = build_application()
    print("Bambu Telegram Bot is listening. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()

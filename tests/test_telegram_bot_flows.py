import os
import sys
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bots.telegram import bot as telegram_bot


def test_start_make_empty_input():
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {}
        context.args = []

        res = await telegram_bot.start_make(update, context)
        assert res == telegram_bot.ConversationHandler.END
        update.message.reply_text.assert_called_with(
            "Please provide an input. Example: /make a small coffee cup"
        )

    asyncio.run(_run())


def test_start_design_empty_input():
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {}
        context.args = []

        res = await telegram_bot.start_design(update, context)
        assert res == telegram_bot.ConversationHandler.END
        update.message.reply_text.assert_called_with(
            "Please provide an input. Example: /design a small coffee cup"
        )

    asyncio.run(_run())


def test_start_edit_without_active_design():
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {}
        context.args = ["make", "it", "taller"]

        res = await telegram_bot.start_edit(update, context)
        assert res == telegram_bot.ConversationHandler.END
        update.message.reply_text.assert_called_with(
            "No active design to edit. Please use /make or /design first."
        )

    asyncio.run(_run())


def test_cancel_command_while_processing():
    """Cancel while is_processing=True: should set cancellation token and return WAITING_FOR_DECISION."""

    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        cancel_token = {"cancelled": False}
        context = MagicMock()
        context.user_data = {
            "is_processing": True,
            "design_state": "Designing...",
            "cancel_token": cancel_token,
        }

        res = await telegram_bot.cancel_command(update, context)
        # Should NOT have cleared user_data yet — thread is still running
        assert res == telegram_bot.WAITING_FOR_DECISION
        assert cancel_token["cancelled"] is True, "cancel_token should be flipped to True"
        assert context.user_data.get("is_cancelling") is True
        assert context.user_data.get("is_processing") is False
        msg = update.message.reply_text.call_args[0][0]
        assert "Cancellation in progress" in msg

    asyncio.run(_run())


def test_cancel_command_while_idle():
    """Cancel when nothing is processing: should clear state and confirm immediately."""

    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {}

        res = await telegram_bot.cancel_command(update, context)
        assert res == telegram_bot.ConversationHandler.END
        update.message.reply_text.assert_called_with("Cancelled ✅")

    asyncio.run(_run())


def test_check_status(monkeypatch):
    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {"design_state": "Waiting for command"}

        monkeypatch.setattr(
            telegram_bot.bambu_cloud, "get_formatted_status", lambda: "Printer: READY"
        )

        await telegram_bot.check_status(update, context)
        update.message.reply_text.assert_called_once()
        msg = update.message.reply_text.call_args[0][0]
        assert "Printer: READY" in msg
        assert "Software Design State: Waiting for command" in msg

    asyncio.run(_run())


def test_slice_failure_allows_edit(monkeypatch, tmp_path):
    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {"is_processing": True}

        monkeypatch.setattr(telegram_bot, "slice_stl_to_3mf", lambda stl, out, base: None)

        res = await telegram_bot.process_stl_for_print(
            update, context, str(tmp_path / "dummy.stl"), str(tmp_path), "dummy", auto_print=False
        )

        assert res == telegram_bot.WAITING_FOR_DECISION
        assert context.user_data["design_state"] == "Failed to slice"
        assert context.user_data["is_processing"] is False
        assert "You can use /edit <changes>" in update.message.reply_text.call_args[0][0]

    asyncio.run(_run())


# 1. Bug 1: OpenSCAD FileNotFoundError blocking Telegram bot startup
def test_bug1_openscad_startup_check():
    """Ensure that if OpenSCAD is missing, main() catches FileNotFoundError and exits gracefully."""
    with (
        patch(
            "src.core.utils.get_openscad_path", side_effect=FileNotFoundError("Mocked Not Found")
        ),
        patch("builtins.print") as mock_print,
        patch("src.bots.telegram.bot.TOKEN", "dummy_token"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            telegram_bot.main()
        assert exc_info.value.code == 1
        mock_print.assert_any_call("FATAL: OpenSCAD not installed or found. Mocked Not Found")


# 2. Bug 2: Leaked is_processing lock on Gemini API error
def test_bug2_leaked_is_processing_lock():
    """Ensure is_processing is reset to False if Gemini API throws an exception."""

    async def _run():
        update = AsyncMock()
        context = MagicMock()
        context.user_data = {"is_processing": True}

        with patch(
            "src.bots.telegram.bot.generate_openscad_validated",
            side_effect=Exception("API Error"),
        ):
            result = await telegram_bot.process_prompt(update, context, "Test prompt")
            assert context.user_data["is_processing"] is False, (
                "Lock should be released on API exception"
            )

    asyncio.run(_run())


# --- Bug 8: Telegram Print Negative Slot ---
def test_bug8_telegram_print_negative_slot():
    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {"last_3mf_file": "dummy.3mf"}
        context.args = ["-1"]
        res = await telegram_bot.start_print(update, context)
        assert res == telegram_bot.WAITING_FOR_DECISION
        update.message.reply_text.assert_called_with(
            "Slot must be 0-3 for AMS, 4 or 'ext' for External Spool, or 'auto'."
        )

    asyncio.run(_run())


def test_telegram_print_slot_4_and_ext():
    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {"last_3mf_file": "dummy.3mf"}
        context.args = ["ext"]

        with (
            patch.object(telegram_bot.bambu_cloud, "get_status", return_value={"state": "IDLE"}),
            patch.object(
                telegram_bot.bambu_cloud,
                "get_available_colors",
                return_value={4: {"type": "PLA", "color": "FFFFFF"}},
            ),
            patch.object(
                telegram_bot.bambu_cloud, "send_print_job", return_value=True
            ) as mock_send,
        ):
            res = await telegram_bot.start_print(update, context)
            assert res == telegram_bot.ConversationHandler.END
            assert mock_send.call_args[1]["ams_slot"] == 4

    asyncio.run(_run())


# --- Bug 12: Sliding Window History Pruning ---
def test_bug12_prune_conversation_history():
    history = [
        {"role": "user", "text": "turn 1 initial prompt"},
        {"role": "assistant", "code": "code 1"},
        {"role": "user", "text": "turn 2"},
        {"role": "assistant", "code": "code 2"},
        {"role": "user", "text": "turn 3"},
        {"role": "assistant", "code": "code 3"},
        {"role": "user", "text": "turn 4"},
        {"role": "assistant", "code": "code 4"},
        {"role": "user", "text": "turn 5"},
        {"role": "assistant", "code": "code 5"},
    ]
    pruned = telegram_bot.prune_conversation_history(history, max_turns=6)
    assert len(pruned) == 7
    assert pruned[0]["text"] == "turn 1 initial prompt"  # Root prompt preserved
    assert pruned[-1]["code"] == "code 5"  # Latest items kept


def test_parse_and_strip_support_flags():
    cleaned, support = telegram_bot.parse_and_strip_support_flags("a small dragon --support")
    assert cleaned == "a small dragon"
    assert support is True

    cleaned, support = telegram_bot.parse_and_strip_support_flags("--support a small dragon")
    assert cleaned == "a small dragon"
    assert support is True

    cleaned, support = telegram_bot.parse_and_strip_support_flags("make it bigger --no-support")
    assert cleaned == "make it bigger"
    assert support is False

    cleaned, support = telegram_bot.parse_and_strip_support_flags("a normal cup")
    assert cleaned == "a normal cup"
    assert support is None


def test_telegram_start_design_with_support_flag():
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {}
        context.args = ["--support", "a", "hook"]

        with patch("src.bots.telegram.bot.process_prompt", new_callable=AsyncMock) as mock_process:
            await telegram_bot.start_design(update, context)
            assert context.user_data["enable_supports"] is True
            mock_process.assert_called_once_with(
                update, context, "a hook", auto_print=False, enable_supports=True
            )

    asyncio.run(_run())


def test_telegram_start_edit_support_inheritance_and_override():
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        # 1. Inherit True
        context = MagicMock()
        context.user_data = {
            "conversation_history": [{"role": "user", "text": "initial"}],
            "enable_supports": True,
        }
        context.args = ["make", "it", "taller"]

        with patch("src.bots.telegram.bot.process_prompt", new_callable=AsyncMock) as mock_process:
            await telegram_bot.start_edit(update, context)
            assert context.user_data["enable_supports"] is True
            mock_process.assert_called_once_with(
                update, context, "make it taller", auto_print=False, enable_supports=True
            )

        # 2. Override with --no-support
        context.user_data["is_processing"] = False
        context.args = ["make", "it", "shorter", "--no-support"]
        with patch("src.bots.telegram.bot.process_prompt", new_callable=AsyncMock) as mock_process:
            await telegram_bot.start_edit(update, context)
            assert context.user_data["enable_supports"] is False
            mock_process.assert_called_once_with(
                update, context, "make it shorter", auto_print=False, enable_supports=False
            )

    asyncio.run(_run())


def test_telegram_start_print_with_support_flag():
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {
            "last_3mf_file": "old.3mf",
            "last_stl_file": "model.stl",
            "last_outdir": "outdir",
            "last_file_basename": "model",
            "last_3mf_has_supports": False,
        }
        context.args = ["1", "--support"]

        with (
            patch("src.bots.telegram.bot.slice_stl_to_3mf") as mock_slice,
            patch.object(telegram_bot.bambu_cloud, "get_status", return_value={"state": "IDLE"}),
            patch.object(
                telegram_bot.bambu_cloud,
                "get_available_colors",
                return_value={1: {"color": "Red", "type": "PLA"}},
            ),
            patch.object(telegram_bot.bambu_cloud, "send_print_job", return_value=True),
        ):
            mock_slice.return_value = "new_supports.3mf"
            res = await telegram_bot.start_print(update, context)
            assert res == telegram_bot.ConversationHandler.END
            mock_slice.assert_called_once_with("model.stl", "outdir", "model", enable_supports=True)

    asyncio.run(_run())


def test_truncate_text():
    assert telegram_bot.truncate_text("") == ""
    assert telegram_bot.truncate_text(None) == ""
    short_text = "Standard error output"
    assert telegram_bot.truncate_text(short_text, 500) == short_text

    long_text = "E" * 700
    res = telegram_bot.truncate_text(long_text, 500)
    assert len(res) == 500 + len("... [truncated]")
    assert res.startswith("E" * 500)
    assert res.endswith("... [truncated]")


def test_format_warnings_summary():
    assert telegram_bot.format_warnings_summary([]) == ""
    assert telegram_bot.format_warnings_summary(None) == ""

    # <= 5 items
    three_warnings = ["Warn 1", "Warn 2", "Warn 3"]
    res3 = telegram_bot.format_warnings_summary(three_warnings)
    assert res3 == "- Warn 1\n- Warn 2\n- Warn 3"
    assert "more" not in res3

    # Exactly 5 items
    five_warnings = [f"Warn {i}" for i in range(1, 6)]
    res5 = telegram_bot.format_warnings_summary(five_warnings)
    assert "- Warn 5" in res5
    assert "more" not in res5

    # 30 items
    thirty_warnings = [f"Geometry Error Part #{i}" for i in range(1, 31)]
    res30 = telegram_bot.format_warnings_summary(thirty_warnings, max_items=5)
    assert "- Geometry Error Part #1" in res30
    assert "- Geometry Error Part #5" in res30
    assert "- Geometry Error Part #6" not in res30
    assert "... and 25 more physical constraint warning(s)." in res30


def test_safe_reply_text_chunking():
    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        # Text with 10 lines of 500 chars = 5000 chars > 4000 limit
        long_message = "\n".join(["A" * 500 for _ in range(10)])
        success = await telegram_bot.safe_reply_text(update, long_message, max_chunk_size=4000)
        assert success is True
        assert update.message.reply_text.call_count == 2
        for call in update.message.reply_text.call_args_list:
            chunk = call[0][0]
            assert len(chunk) <= 4000

    asyncio.run(_run())


def test_safe_reply_text_exception_handling():
    async def _run():
        update = MagicMock()
        update.message.reply_text = AsyncMock(side_effect=Exception("Telegram Network Timeout"))

        success = await telegram_bot.safe_reply_text(update, "Hello world")
        assert success is False  # Must return False rather than raising uncaught exception

    asyncio.run(_run())


def test_process_prompt_with_large_warnings_manual(tmp_path):
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        update.message.reply_document = AsyncMock()

        context = MagicMock()
        context.user_data = {"is_processing": True}

        # 35 warnings
        warnings = [f"Part #{i} floating in mid-air at Z=12.00mm" for i in range(1, 36)]

        dummy_stl = os.path.join(str(tmp_path), "test.stl")
        with open(dummy_stl, "w") as f:
            f.write("solid test\nendsolid test\n")

        with (
            patch(
                "src.bots.telegram.bot.generate_openscad_validated",
                return_value=("cube(10);", "fidget_turtle", ["Green"], warnings, dummy_stl, None),
            ),
            patch(
                "src.bots.telegram.bot.process_stl_for_print", new_callable=AsyncMock
            ) as mock_slice_proc,
        ):
            mock_slice_proc.return_value = telegram_bot.WAITING_FOR_DECISION

            res = await telegram_bot.process_prompt(
                update, context, "make a fidget turtle", auto_print=False
            )
            assert res == telegram_bot.WAITING_FOR_DECISION

            # Check that a warning message was delivered
            warning_calls = [
                call[0][0]
                for call in update.message.reply_text.call_args_list
                if "Warning" in str(call[0][0])
            ]
            assert len(warning_calls) > 0
            warn_call = warning_calls[0]
            assert "Part #1" in warn_call
            assert "Part #5" in warn_call
            assert "... and 30 more physical constraint warning(s)." in warn_call
            assert len(warn_call) < 2000  # Well below Telegram 4096 limit!

            # Verified reply_document called to deliver STL
            assert update.message.reply_document.called

    asyncio.run(_run())


def test_process_prompt_with_large_warnings_autoprint(tmp_path):
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        update.message.reply_document = AsyncMock()

        context = MagicMock()
        context.user_data = {"is_processing": True}

        warnings = [f"Part #{i} floating in mid-air at Z=12.00mm" for i in range(1, 36)]

        dummy_stl = os.path.join(str(tmp_path), "test.stl")
        with open(dummy_stl, "w") as f:
            f.write("solid test\nendsolid test\n")

        with (
            patch(
                "src.bots.telegram.bot.generate_openscad_validated",
                return_value=("cube(10);", "fidget_turtle", ["Green"], warnings, dummy_stl, None),
            ),
            patch(
                "src.bots.telegram.bot.process_stl_for_print", new_callable=AsyncMock
            ) as mock_slice_proc,
        ):
            mock_slice_proc.return_value = telegram_bot.WAITING_FOR_DECISION

            res = await telegram_bot.process_prompt(
                update, context, "make a fidget turtle", auto_print=True
            )
            assert res == telegram_bot.WAITING_FOR_DECISION

            # In auto-print mode, physical warnings abort the print attempt and set is_processing=False
            assert context.user_data.get("is_processing") is False
            assert context.user_data.get("design_state") == "Constraint Validation Failed"

            # Check notification about print aborted with summarized warnings
            warning_calls = [
                call[0][0]
                for call in update.message.reply_text.call_args_list
                if "Print aborted" in str(call[0][0])
            ]
            assert len(warning_calls) > 0
            assert "Part #1" in warning_calls[0]
            assert "... and 30 more physical constraint warning(s)." in warning_calls[0]
            assert len(warning_calls[0]) < 2000

    asyncio.run(_run())


def test_is_physical_constraint_warning_classification():
    # Physical warnings
    assert telegram_bot.is_physical_constraint_warning("Part #1 floating in mid-air at Z=12.00mm")
    assert telegram_bot.is_physical_constraint_warning(
        "Constraint Error: Estimated print time is 45m, which exceeds the maximum allowed time of 30m."
    )
    assert telegram_bot.is_physical_constraint_warning(
        "Constraint Error: Dimensions are 12.0in which exceeds the maximum allowed size of 10.0in."
    )
    assert telegram_bot.is_physical_constraint_warning(
        "Geometry Error: Part #1 contains a weak structural bottleneck"
    )
    assert telegram_bot.is_physical_constraint_warning(
        "Disconnected parts detected: expected 1, found 3"
    )
    assert telegram_bot.is_physical_constraint_warning("Thin connection detected (0.80mm < 1.20mm)")
    assert telegram_bot.is_physical_constraint_warning(
        "Insufficient bed contact area (12.5% of base)"
    )
    assert telegram_bot.is_physical_constraint_warning("Non-manifold edges detected: 14")
    assert telegram_bot.is_physical_constraint_warning("Base is not flat on build plate")

    # Internal / Fallback warnings (NOT physical)
    assert not telegram_bot.is_physical_constraint_warning(
        "AI refinement interrupted by API service issue (APIError); recovered previous working design."
    )
    assert not telegram_bot.is_physical_constraint_warning("Reverted to last working code.")
    assert not telegram_bot.is_physical_constraint_warning("Iteration 2 failed compilation.")
    assert not telegram_bot.is_physical_constraint_warning("")
    assert not telegram_bot.is_physical_constraint_warning(None)


def test_filter_physical_constraint_warnings():
    mixed = [
        "AI refinement interrupted by API service issue (APIError); recovered previous working design.",
        "Thin connection detected (0.80mm < 1.20mm)",
        "Reverted to last working code.",
        "Part #1 floating in mid-air at Z=12.00mm",
    ]
    filtered = telegram_bot.filter_physical_constraint_warnings(mixed)
    assert len(filtered) == 2
    assert "Thin connection" in filtered[0]
    assert "floating in mid-air" in filtered[1]


def test_process_prompt_suppresses_fallback_warnings_when_printable(tmp_path):
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        update.message.reply_document = AsyncMock()

        context = MagicMock()
        context.user_data = {"is_processing": True}

        fallback_warnings = [
            "AI refinement interrupted by API service issue (APIError); recovered previous working design."
        ]

        dummy_stl = os.path.join(str(tmp_path), "test.stl")
        with open(dummy_stl, "w") as f:
            f.write("solid test\nendsolid test\n")
        dummy_3mf = os.path.join(str(tmp_path), "test.3mf")
        with open(dummy_3mf, "w") as f:
            f.write("dummy 3mf content\n")

        with (
            patch(
                "src.bots.telegram.bot.generate_openscad_validated",
                return_value=(
                    "cube(10);",
                    "cube",
                    ["Blue"],
                    fallback_warnings,
                    dummy_stl,
                    dummy_3mf,
                ),
            ),
            patch(
                "src.bots.telegram.bot.process_stl_for_print", new_callable=AsyncMock
            ) as mock_slice_proc,
        ):
            mock_slice_proc.return_value = telegram_bot.WAITING_FOR_DECISION

            res = await telegram_bot.process_prompt(
                update, context, "make a simple cube", auto_print=False
            )
            assert res == telegram_bot.WAITING_FOR_DECISION

            # Verified NO warning message was sent to Telegram
            warning_calls = [
                call[0][0]
                for call in update.message.reply_text.call_args_list
                if "Warning" in str(call[0][0]) or "constraint" in str(call[0][0]).lower()
            ]
            assert len(warning_calls) == 0

            # Verified reply_document called to deliver the printable STL
            assert update.message.reply_document.called

    asyncio.run(_run())


def test_process_prompt_fallback_warning_does_not_abort_autoprint(tmp_path):
    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        update.message.reply_document = AsyncMock()

        context = MagicMock()
        context.user_data = {"is_processing": True}

        fallback_warnings = [
            "AI refinement interrupted by API service issue (APIError); recovered previous working design."
        ]

        dummy_stl = os.path.join(str(tmp_path), "test.stl")
        with open(dummy_stl, "w") as f:
            f.write("solid test\nendsolid test\n")
        dummy_3mf = os.path.join(str(tmp_path), "test.3mf")
        with open(dummy_3mf, "w") as f:
            f.write("dummy 3mf content\n")

        with (
            patch(
                "src.bots.telegram.bot.generate_openscad_validated",
                return_value=(
                    "cube(10);",
                    "cube",
                    ["Blue"],
                    fallback_warnings,
                    dummy_stl,
                    dummy_3mf,
                ),
            ),
            patch(
                "src.bots.telegram.bot.process_stl_for_print", new_callable=AsyncMock
            ) as mock_slice_proc,
        ):
            mock_slice_proc.return_value = telegram_bot.ConversationHandler.END

            res = await telegram_bot.process_prompt(
                update, context, "make a simple cube", auto_print=True
            )
            # Auto-print was NOT aborted; it proceeded to slice/print processing!
            assert res == telegram_bot.ConversationHandler.END

            # No "Print aborted" warning was delivered
            aborted_calls = [
                call[0][0]
                for call in update.message.reply_text.call_args_list
                if "Print aborted" in str(call[0][0])
            ]
            assert len(aborted_calls) == 0

    asyncio.run(_run())


def test_telegram_session_json_contains_sliced_metrics(tmp_path, monkeypatch):
    import json
    import zipfile

    monkeypatch.setattr(telegram_bot, "WORKSPACE_DIR", str(tmp_path))

    async def _run():
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        update.message.reply_document = AsyncMock()

        context = MagicMock()
        context.user_data = {"is_processing": True}

        prebuilt_stl = tmp_path / "pre.stl"
        prebuilt_stl.write_text(
            "solid box\n"
            "  facet normal 0 0 1\n"
            "    outer loop\n"
            "      vertex 0 0 0\n"
            "      vertex 25.4 0 0\n"
            "      vertex 0 25.4 25.4\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid box\n",
            encoding="utf-8",
        )
        prebuilt_3mf = tmp_path / "pre.3mf"
        with zipfile.ZipFile(prebuilt_3mf, "w") as zf:
            zf.writestr(
                "Metadata/plate_1.gcode",
                b"; total estimated time = 33m 15s\n; TYPE:Tree support\n; filament used [g] = 14.6\n",
            )

        with (
            patch(
                "src.bots.telegram.bot.generate_openscad_validated",
                return_value=(
                    "cube(25.4);",
                    "metric_part",
                    ["Red"],
                    [],
                    str(prebuilt_stl),
                    str(prebuilt_3mf),
                ),
            ),
            patch.object(
                telegram_bot.bambu_cloud,
                "get_available_colors",
                return_value={0: {"color": "FF0000", "type": "PLA", "remain": 90}},
            ),
        ):
            res = await telegram_bot.process_prompt(
                update, context, "make a 1 inch cube", auto_print=False, enable_supports=True
            )
            assert res == telegram_bot.WAITING_FOR_DECISION

            parent_dir = context.user_data["parent_dir"]
            session_file = os.path.join(parent_dir, "session.json")
            assert os.path.exists(session_file)
            s_data = json.loads(open(session_file, encoding="utf-8").read())
            assistant_turn = [t for t in s_data["history"] if t.get("role") == "assistant"][-1]
            assert assistant_turn["time"] == "33m 15s"
            assert assistant_turn["grams"] == 14.6
            assert assistant_turn["has_supports"] is True
            assert "1.00" in assistant_turn["size"]

            # Verify info message indicates Tree supports generated
            replies = [c.args[0] for c in update.message.reply_text.call_args_list]
            assert any("Enabled (Tree supports generated)" in r for r in replies)

    asyncio.run(_run())


def test_telegram_monitor_notifies_from_any_busy_state(monkeypatch):
    from src.bots.telegram import monitor

    async def _run():
        app = MagicMock()
        app.bot.send_message = AsyncMock()
        app.bot.send_photo = AsyncMock()

        monkeypatch.setattr(monitor, "known_chat_ids", {99999})
        monkeypatch.setattr(monitor, "load_api_keys", lambda: {})

        fake_cloud = MagicMock()
        monkeypatch.setattr(monitor, "get_bambu_cloud", lambda: fake_cloud)

        # 1. Transition PAUSE -> FINISH
        monitor.previous_print_state = "PAUSE"
        fake_cloud.last_status = {"state": "FINISH"}

        sleep_calls = [0]

        async def fake_sleep(sec):
            if sec == 60:
                sleep_calls[0] += 1
                if sleep_calls[0] >= 1:
                    raise asyncio.CancelledError()

        monkeypatch.setattr(monitor.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await monitor.monitor_print_status(app)

        app.bot.send_message.assert_called_once_with(
            chat_id=99999, text="🎉 Your print has finished successfully!"
        )

        # 2. Transition PREPARE -> FAILED
        app.bot.send_message.reset_mock()
        sleep_calls[0] = 0
        monitor.previous_print_state = "PREPARE"
        fake_cloud.last_status = {"state": "FAILED"}

        with pytest.raises(asyncio.CancelledError):
            await monitor.monitor_print_status(app)

        app.bot.send_message.assert_called_once_with(
            chat_id=99999, text="❌ Your print has failed or encountered an error!"
        )

    asyncio.run(_run())

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import subprocess
import src.bots.telegram.bot as tbot


@pytest.mark.asyncio
@patch("src.bots.telegram.bot.generate_openscad_validated")
async def test_recovery_from_gemini_failure(mock_generate, tmp_path):
    mock_generate.side_effect = Exception("Gemini API Timeout")

    update = AsyncMock()
    context = MagicMock()
    context.user_data = {"is_processing": True}

    with patch("os.makedirs"):
        await tbot.process_prompt(update, context, "make a red cube")

    assert context.user_data.get("is_processing") == False
    update.message.reply_text.assert_any_call(
        "There was an error generating your design. Please try again later."
    )


@pytest.mark.asyncio
@patch("src.bots.telegram.bot.generate_openscad_validated")
@patch("src.bots.telegram.bot.subprocess.run")
@patch("builtins.open")
async def test_recovery_from_openscad_failure(mock_open, mock_run, mock_generate, tmp_path):
    mock_generate.return_value = ("cube(10);", "cube", [], [])
    mock_run.side_effect = subprocess.CalledProcessError(1, [])

    update = AsyncMock()
    context = MagicMock()
    context.user_data = {"is_processing": True}

    with patch("os.makedirs"):
        await tbot.process_prompt(update, context, "make a red cube")

    assert context.user_data.get("is_processing") == False
    update.message.reply_text.assert_any_call(
        "Failed to render the OpenSCAD code. You can use /edit <changes> to ask AI to fix it.\nError: Command '[]' returned non-zero exit status 1.\nStderr: None"
    )

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import src.bots.telegram.bot as tbot


@pytest.mark.asyncio
@patch("src.bots.telegram.bot.generate_openscad_validated")
@patch("src.bots.telegram.bot.subprocess.run")
@patch("src.bots.telegram.bot.slice_stl_to_3mf")
@patch("src.bots.telegram.bot.get_slice_info_from_3mf")
@patch("src.bots.telegram.bot.bambu_cloud")
@patch("builtins.open")
async def test_telegram_e2e_flow(
    mock_open, mock_cloud, mock_get_slice, mock_slice, mock_run, mock_generate, tmp_path
):
    mock_generate.return_value = ("cube(10);", "cube", ["Red"], [])
    mock_slice.return_value = str(tmp_path / "test.3mf")
    mock_get_slice.return_value = {"grams": 10.0, "time": "10m"}
    mock_cloud.get_available_colors.return_value = {
        1: {"type": "PLA", "color": "FF0000", "remain": 100}
    }
    mock_cloud.send_print_job = MagicMock(return_value=True)
    mock_run.return_value = MagicMock(returncode=0)

    update = AsyncMock()

    context = MagicMock()
    context.user_data = {"is_processing": True}

    with patch("os.makedirs"):
        await tbot.process_prompt(update, context, "make a red cube", auto_print=True)

    update.message.reply_text.assert_any_call("Print job successfully started! 🚀")
    assert mock_generate.call_count == 1
    assert mock_cloud.send_print_job.call_count == 1

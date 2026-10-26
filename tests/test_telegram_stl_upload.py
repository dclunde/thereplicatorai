import os
import sys
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.bots.telegram.bot as bot_module


@pytest.mark.asyncio
async def test_handle_document_sets_is_processing():
    update = MagicMock()
    update.effective_chat.id = 12345
    update.message.document.file_name = "test_model.stl"
    update.message.document.file_id = "doc123"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}

    mock_file = MagicMock()
    mock_file.download_to_drive = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=mock_file)

    with patch.object(
        bot_module,
        "process_stl_for_print",
        new=AsyncMock(return_value=bot_module.WAITING_FOR_DECISION),
    ) as mock_process:
        res = await bot_module.handle_document(update, context)

        assert 12345 in bot_module.known_chat_ids
        assert context.user_data.get("is_processing") is True
        mock_process.assert_called_once()
        assert res == bot_module.WAITING_FOR_DECISION


if __name__ == "__main__":
    asyncio.run(test_handle_document_sets_is_processing())
    print("test_handle_document_sets_is_processing passed!")

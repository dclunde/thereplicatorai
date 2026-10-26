import json
import os
import sys
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.ai import providers as ai_providers
from src.core.ai.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    get_active_ai_provider,
    load_provider_config,
)
from src.core.utils import load_api_keys


def test_load_provider_config_default(tmp_path):
    """Verify load_provider_config returns defaults when file does not exist or has valid JSON."""
    missing_path = str(tmp_path / "nonexistent_ai_models.json")
    cfg = load_provider_config(config_path=missing_path)
    assert cfg["active_provider"] == "gemini"
    assert cfg["models"]["gemini"] == "gemini-4-argon"
    assert cfg["models"]["chatgpt"] == "gpt-6-astra"
    assert cfg["models"]["claude"] == "claude-fable-5-1"
    assert cfg["reasoning_effort"]["chatgpt"] == "max"

    # Also verify real inputs/ai_models.json loads cleanly
    real_cfg = load_provider_config()
    assert real_cfg["active_provider"] in ("gemini", "chatgpt", "claude")
    assert real_cfg["models"]["gemini"] == "gemini-4-argon"
    assert real_cfg["models"]["chatgpt"] == "gpt-6-astra"
    assert real_cfg["models"]["claude"] == "claude-fable-5-1"


def test_provider_selection_switch(tmp_path, monkeypatch):
    """Verify get_active_ai_provider instantiates the right provider class based on active_provider."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEMINI_API_KEY=gem-key-123\nOPENAI_API_KEY=sk-test-456\nANTHROPIC_API_KEY=sk-ant-789\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ai_providers.genai, "Client", MagicMock())
    monkeypatch.setattr(ai_providers.openai, "OpenAI", MagicMock())
    monkeypatch.setattr(ai_providers.anthropic, "Anthropic", MagicMock())

    # 1. Gemini
    cfg_file = tmp_path / "ai_models.json"
    cfg_file.write_text(
        json.dumps({"active_provider": "gemini", "models": {"gemini": "gemini-4-argon"}}),
        encoding="utf-8",
    )
    provider = get_active_ai_provider(config_path=str(cfg_file), env_path=str(env_file))
    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-4-argon"
    assert provider.api_key == "gem-key-123"

    # 2. ChatGPT / OpenAI
    cfg_file.write_text(
        json.dumps({"active_provider": "chatgpt", "models": {"chatgpt": "gpt-6-astra"}}),
        encoding="utf-8",
    )
    provider = get_active_ai_provider(config_path=str(cfg_file), env_path=str(env_file))
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-6-astra"
    assert provider.reasoning_effort == "max"
    assert provider.api_key == "sk-test-456"

    # 3. Claude / Anthropic
    cfg_file.write_text(
        json.dumps({"active_provider": "claude", "models": {"claude": "claude-fable-5-1"}}),
        encoding="utf-8",
    )
    provider = get_active_ai_provider(config_path=str(cfg_file), env_path=str(env_file))
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-fable-5-1"
    assert provider.api_key == "sk-ant-789"


def test_missing_api_key_error(tmp_path):
    """Verify clear ValueError when active provider's API key is missing in .env."""
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=gem-key-only\nOPENAI_API_KEY=\n", encoding="utf-8")

    cfg_file = tmp_path / "ai_models.json"
    cfg_file.write_text(
        json.dumps({"active_provider": "chatgpt", "models": {"chatgpt": "gpt-6-astra"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        get_active_ai_provider(config_path=str(cfg_file), env_path=str(env_file))

    assert "Active AI provider is 'chatgpt'" in str(exc_info.value)
    assert "OPENAI_API_KEY is not set in .env" in str(exc_info.value)


def test_gemini_provider_call(monkeypatch):
    """Verify GeminiProvider formats request and returns response text."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '  {"code": "cube(10);"}  '
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr(ai_providers.genai, "Client", MagicMock(return_value=mock_client))

    provider = GeminiProvider(api_key="test-gem-key", model="gemini-4-argon")
    res = provider.generate_content("Make a cube", system_instructions="You are an OpenSCAD expert")

    assert res == '{"code": "cube(10);"}'
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-4-argon"
    assert call_kwargs["contents"] == "Make a cube"


def test_openai_provider_call(monkeypatch):
    """Verify OpenAIProvider formats chat completions request with json_object mode and max reasoning effort."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"code": "sphere(5);"}'
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
    monkeypatch.setattr(ai_providers.openai, "OpenAI", MagicMock(return_value=mock_client))

    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-6-astra")
    res = provider.generate_content("Make a sphere", system_instructions="Return JSON with code.")

    assert res == '{"code": "sphere(5);"}'
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-6-astra"
    assert call_kwargs["reasoning_effort"] == "max"
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["messages"][0]["role"] == "system"
    assert call_kwargs["messages"][1] == {"role": "user", "content": "Make a sphere"}


def test_anthropic_provider_call(monkeypatch):
    """Verify AnthropicProvider formats messages.create request and extracts text block."""
    mock_client = MagicMock()
    mock_block = MagicMock()
    mock_block.text = '{"code": "cylinder(h=10, r=3);"}'
    mock_client.messages.create.return_value = MagicMock(content=[mock_block])
    monkeypatch.setattr(ai_providers.anthropic, "Anthropic", MagicMock(return_value=mock_client))

    provider = AnthropicProvider(api_key="test-ant-key", model="claude-fable-5-1")
    res = provider.generate_content(
        "Make a cylinder", system_instructions="System instructions here"
    )

    assert res == '{"code": "cylinder(h=10, r=3);"}'
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-fable-5-1"
    assert call_kwargs["system"] == "System instructions here"
    assert call_kwargs["messages"] == [{"role": "user", "content": "Make a cylinder"}]


def test_env_loader_prefers_dotenv(tmp_path):
    """Verify load_api_keys parses .env withGEMINI/OPENAI/ANTHROPIC keys."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        "GEMINI_API_KEY=gem_123\n"
        "OPENAI_API_KEY=oa_456\n"
        "ANTHROPIC_API_KEY=ant_789\n"
        "BAMBU_USERNAME=user@example.com\n",
        encoding="utf-8",
    )
    keys = load_api_keys(env_path=str(env_file))
    assert keys["GEMINI_API_KEY"] == "gem_123"
    assert keys["OPENAI_API_KEY"] == "oa_456"
    assert keys["ANTHROPIC_API_KEY"] == "ant_789"
    assert keys["BAMBU_USERNAME"] == "user@example.com"

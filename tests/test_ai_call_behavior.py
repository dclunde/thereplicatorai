import json
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.ai import validation_loop as ai_call, providers as ai_providers
from src.core.ai.validation_loop import (
    _try_repair_json,
    generate_openscad,
    generate_with_validation,
)


@pytest.fixture(autouse=True)
def ensure_mock_api_keys(monkeypatch):
    """Ensure a dummy API key is present when tests call generate_openscad directly."""
    monkeypatch.setattr(
        ai_providers,
        "load_api_keys",
        lambda env_path=None: {
            "GEMINI_API_KEY": "test-gemini-key",
            "OPENAI_API_KEY": "test-openai-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        },
    )


def test_generate_openscad_mocked():
    print("Testing generate_openscad JSON parsing (mocked)...")

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        class models:
            @staticmethod
            def generate_content(*args, **kwargs):
                class MockResponse:
                    text = '```json\n{"code": "cube([10,10,10]);", "filename": "my_cube", "color_hierarchy": ["Red"]}\n```'

                return MockResponse()

    original_client = ai_providers.genai.Client
    ai_providers.genai.Client = MockClient

    try:
        code, filename, colors = ai_call.generate_openscad("Make a cube")
        assert code == "cube([10,10,10]);", f"Expected 'cube([10,10,10]);', got '{code}'"
        assert filename == "my_cube", f"Expected 'my_cube', got '{filename}'"
        assert colors == ["Red"], f"Expected ['Red'], got {colors}"

        class MockClientMalformed:
            def __init__(self, *args, **kwargs):
                pass

            class models:
                @staticmethod
                def generate_content(*args, **kwargs):
                    class MockResponse:
                        text = "```openscad\ncube(10);\n```"

                    return MockResponse()

        ai_providers.genai.Client = MockClientMalformed
        code, filename, colors = ai_call.generate_openscad("Make a cube")
        assert code == "cube(10);", f"Expected 'cube(10);', got '{code}'"

    finally:
        ai_providers.genai.Client = original_client


# --- Bug 5: None Text in History ---
def test_bug5_generate_openscad_handles_none_text(monkeypatch):
    history = [{"role": "user", "text": None}, {"role": "assistant", "code": "cube(10);"}]
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = MagicMock(text='{"code": "cube(5);"}')
    monkeypatch.setattr("src.core.ai.providers.genai.Client", lambda *a, **kw: mock_client)
    code, filename, colors = generate_openscad("make smaller", conversation_history=history)
    assert code == "cube(5);"


# --- Bug 7: JSON String Repair Following Keys ---
def test_bug7_repair_json_escaped_quotes():
    raw = '{"code": "translate([0,0,0]) text("3D");", "filename": "custom_name", "color_hierarchy": []}'
    repaired = _try_repair_json(raw)
    assert repaired["filename"] == "custom_name"


# --- Bug 15: UTF-8 Encoding in AI Model Config Loader ---
def test_bug15_utf8_ai_models_loader(tmp_path):
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    model_file = inputs_dir / "ai_models.json"
    data = {"active_provider": "gemini", "models": {"gemini": "gemini-2.5-flash"}}
    model_file.write_text(json.dumps(data), encoding="utf-8")
    loaded = ai_providers.load_provider_config(config_path=str(model_file))
    assert loaded["active_provider"] == "gemini"
    assert loaded["models"]["gemini"] == "gemini-2.5-flash"


def test_ai_call_multi_agent_retry():
    """Verify multi-agent loop works across any BaseAIProvider subclass (provider-agnostic)."""
    calls = []

    class DummyProvider(ai_providers.BaseAIProvider):
        provider_name = "dummy"

        def generate_content(self, prompt: str, system_instructions: str = "") -> str:
            calls.append(prompt)
            if len(calls) == 1:
                return '{"code": "broken_scad(", "filename": "retry_box"}'
            return '{"code": "cube([10, 10, 10]);", "filename": "retry_box"}'

    dummy_provider = DummyProvider(api_key="dummy", model="dummy-1")
    mock_config = {
        "client": dummy_provider,
        "provider": dummy_provider,
        "model": "dummy-1",
        "instructions": "Base instructions\n\nOutput Requirements:\nReturn JSON.",
        "error_template": "Fix error for {user_request}: {broken_code} | {error_log} | {size} | {geometry_errors}",
        "verify_template": "Verify {user_request}: {generated_code}",
        "max_ai_calls": 3,
        "agent_level": 2,  # Error fix enabled, verify disabled
        "base_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }

    validate_outcomes = [
        (False, "ERROR: Parser error in line 1", None),
        (True, "", "/tmp/fake/validate.stl"),
    ]

    with (
        patch("src.core.ai.validation_loop._load_ai_config", return_value=mock_config),
        patch("src.core.ai.validation_loop._validate_code", side_effect=validate_outcomes),
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics",
            return_value=("1.0 in", "10m", 3.0, []),
        ),
    ):
        events = list(generate_with_validation("make a box", max_calls=3))

    assert len(calls) == 2
    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0]["code"] == "cube([10, 10, 10]);"
    assert result_events[0]["filename"] == "retry_box"


def test_build_initial_prompt_includes_constraints_on_edit_turns():
    from src.core.ai.prompts import _build_initial_prompt

    history = [
        {"role": "user", "text": "make a phone stand"},
        {
            "role": "assistant",
            "code": "cube([50, 50, 50]);",
            "size": "2.0 × 2.0 × 2.0 in",
            "time": "25m",
            "grams": 12.0,
        },
    ]
    config = {"max_print_time_minutes": 45, "max_size_inches": 4.0}
    prompt_out = _build_initial_prompt("make the base wider", history, config)
    assert "New User Edit Request: make the base wider" in prompt_out
    assert "DESIGN CONSTRAINTS:" in prompt_out
    assert "45 minutes" in prompt_out
    assert "4.0 inches" in prompt_out

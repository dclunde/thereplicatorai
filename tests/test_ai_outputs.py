import os
import sys
from unittest.mock import MagicMock
import pytest

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.ai import validation_loop as ai_call, providers as ai_providers


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


def create_mock_response(text_content):
    class MockResponse:
        text = text_content

    return MockResponse()


def test_perfect_json_output(monkeypatch):
    raw_json = (
        '{"code": "cube([10, 10, 10]);", "filename": "my_cube", "color_hierarchy": ["Red", "Blue"]}'
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_json)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Make a cube")
    assert code == "cube([10, 10, 10]);"
    assert filename == "my_cube"
    assert colors == ["Red", "Blue"]


def test_json_wrapped_in_markdown(monkeypatch):
    raw_response = """```json
{
  "code": "cylinder(h=20, r=5);",
  "filename": "cylinder_part",
  "color_hierarchy": ["Green"]
}
```"""
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Make a cylinder")
    assert code == "cylinder(h=20, r=5);"
    assert filename == "cylinder_part"
    assert colors == ["Green"]


def test_unescaped_quotes_in_code_field(monkeypatch):
    # Common failure mode: LLM returns unescaped double quotes inside code string value
    raw_response = '{"code": "translate([0,0,0]) text("Hello World");", "filename": "text_3d", "color_hierarchy": []}'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Create 3D text")
    assert "Hello World" in code
    assert filename == "text_3d"


def test_unescaped_newlines_in_code_field(monkeypatch):
    raw_response = (
        '{"code": "cube([10,10,10]);\ntranslate([5,5,5]) sphere(r=2);", "filename": "combo_part"}'
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Create combo shape")
    assert "cube" in code
    assert "sphere" in code
    assert filename == "combo_part"


def test_extra_trailing_brace(monkeypatch):
    raw_response = '{"code": "sphere(r=10);", "filename": "ball", "color_hierarchy": ["White"]}}'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Make a sphere")
    assert code == "sphere(r=10);"
    assert filename == "ball"
    assert colors == ["White"]


def test_pure_openscad_code_fallback(monkeypatch):
    # LLM returned pure SCAD code wrapped in openscad markdown instead of JSON
    raw_response = "```openscad\nmodule main() {\n  cube([15, 15, 15]);\n}\nmain();\n```"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Make SCAD module")
    assert "module main()" in code
    assert filename == "gemini_part"
    assert colors == []


def test_missing_optional_keys(monkeypatch):
    raw_response = '{"code": "cube([5, 5, 5]);"}'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Tiny cube")
    assert code == "cube([5, 5, 5]);"
    assert filename == "gemini_part"
    assert colors == []


def test_conversation_history_formatting(monkeypatch):
    history = [
        {"role": "user", "text": "make a cube"},
        {"role": "assistant", "code": "cube([10,10,10]);"},
        {"role": "user", "text": "size"},  # special prompt should be filtered out
        {"role": "user", "text": "make it taller"},
    ]
    raw_response = '{"code": "cube([10,10,20]);", "filename": "tall_cube", "color_hierarchy": []}'

    calls = []

    def mock_generate_content(model, contents, config):
        calls.append(contents)
        if len(calls) == 1:
            assert "Previous User Request: make a cube" in contents
            assert "Latest OpenSCAD code generated:" in contents
            assert (
                "size" not in contents.split("Previous User Request: ")[-1]
            )  # size request filtered
            assert "New User Edit Request: make it taller" in contents
        return create_mock_response(raw_response)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = mock_generate_content

    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad(
        "make it taller", conversation_history=history
    )
    assert code == "cube([10,10,20]);"
    assert filename == "tall_cube"


def test_unparseable_garbage_fallback(monkeypatch):
    raw_response = "Random gibberish that is completely invalid JSON and has no code block"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = create_mock_response(raw_response)
    monkeypatch.setattr(ai_providers.genai, "Client", lambda *a, **kw: mock_client)

    code, filename, colors = ai_call.generate_openscad("Garbage prompt")
    assert "Error: AI returned malformed JSON" in code or "Random gibberish" in code


def test_json_repair_regex_fallback_preserves_escaped_backslashes():
    from src.core.ai.json_repair import parse_ai_response

    # Malformed trailing garbage forces _try_repair_json to fail and fall back to regex extraction
    # Contains both \\n (literal backslash + n in OpenSCAD string) and \n (newline between statements)
    raw = (
        '{"thought_process": "ok, "broken_key": unquoted_val, '
        '"code": "text(\\"Line1\\\\nLine2\\");\\ncube(10);", '
        '"filename": "backslash_part"}'
    )
    code, filename, _, _ = parse_ai_response(raw)
    assert filename == "backslash_part"
    # The literal \\n inside the OpenSCAD string must remain \n (backslash followed by n), NOT a raw newline!
    assert 'text("Line1\\nLine2");' in code
    assert "\ncube(10);" in code

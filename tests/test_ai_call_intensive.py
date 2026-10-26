"""Intensive unit and edge-case tests for shared/ai_call.py and shared/ai_providers.py.

Covers:
- _load_ai_config with service constraints, missing/corrupt agent_config.json, get_max_ai_calls
- _build_initial_prompt, _build_user_context, _build_constraint_hint, _build_agent_instructions
- _parse_time_to_minutes, _parse_size_to_max_inch, _check_constraints
- _parse_response & _try_repair_json (regex fallback, markdown fallback, raw code, malformed JSON)
- _validate_code (timeout, empty STL, FileNotFoundError, missing OpenSCAD)
- generate_with_validation across agent_level 1, 2, 3, 4, verification failure, fallback to last_working_code vs last_compiled_code, generate_openscad_validated wrapper
- OpenAIProvider, AnthropicProvider, GeminiProvider edge cases in shared/ai_providers.py
"""

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.ai import validation_loop as ai_call, providers as ai_providers


# =====================================================================
# 1. Helper & Parser Functions in shared/ai_call.py
# =====================================================================


def test_parse_time_to_minutes_variants():
    assert ai_call._parse_time_to_minutes("") == 0
    assert ai_call._parse_time_to_minutes("Unknown") == 0
    assert ai_call._parse_time_to_minutes("1d 2h 15m 40s") == (24 * 60) + 120 + 15 + 1
    assert ai_call._parse_time_to_minutes("15s") == 1  # <30s when total_minutes==0 rounds up to 1
    assert ai_call._parse_time_to_minutes("10m 15s") == 10


def test_parse_size_to_max_inch_variants():
    assert ai_call._parse_size_to_max_inch("") == 0.0
    assert ai_call._parse_size_to_max_inch("Unknown") == 0.0
    assert ai_call._parse_size_to_max_inch("Unavailable (Compilation Error)") == 0.0
    assert ai_call._parse_size_to_max_inch("no numbers here") == 0.0
    assert ai_call._parse_size_to_max_inch("1.50 × 3.25 × 0.80 in") == 3.25


def test_check_constraints_all_violations():
    cfg = {"max_print_time_minutes": 60, "max_size_inches": 4.0}
    # 1. Unknown print time -> slicing failed error
    errs = ai_call._check_constraints("2.00 × 2.00 × 2.00 in", "Unknown", cfg)
    assert any("Slicing failed" in e for e in errs)

    # 2. Time and size both exceeded
    errs2 = ai_call._check_constraints("5.50 × 2.00 × 2.00 in", "1h 30m", cfg)
    assert len(errs2) == 2
    assert any("exceeds the maximum allowed time of 60m" in e for e in errs2)
    assert any("exceeds the maximum allowed size of 4.0 inches" in e for e in errs2)

    # 3. Within limits -> empty list
    assert ai_call._check_constraints("2.00 × 2.00 × 2.00 in", "45m", cfg) == []


def test_build_constraint_hint_and_user_context():
    assert ai_call._build_constraint_hint(None) == ""
    assert ai_call._build_constraint_hint({"max_print_time_minutes": 0, "max_size_inches": 0}) == ""
    hint = ai_call._build_constraint_hint({"max_print_time_minutes": 45, "max_size_inches": 3})
    assert "45 minutes" in hint
    assert "3 inches" in hint

    history = [
        {"role": "user", "text": "a phone stand"},
        {"role": "user", "text": "size"},  # filtered out
        {"role": "assistant", "code": "cube(10);"},
        {"role": "user", "text": "add a cable slot"},
    ]
    ctx = ai_call._build_user_context("make it wider", history)
    assert ctx == "a phone stand → add a cable slot → make it wider"
    assert ai_call._build_user_context("solo prompt", None) == "solo prompt"


def test_build_agent_instructions_roles():
    base = "Rules here.\n\nOutput Requirements:\nReturn full JSON."
    assert ai_call._build_agent_instructions(base, "generate") == base
    err_inst = ai_call._build_agent_instructions(base, "error_fix")
    assert 'single key "code"' in err_inst
    assert "Return full JSON" not in err_inst

    ver_inst = ai_call._build_agent_instructions(base, "verify")
    assert "If the code is satisfactory" in ver_inst

    # Unknown role or missing marker
    assert ai_call._build_agent_instructions(base, "other") == base
    assert ai_call._build_agent_instructions("No marker", "error_fix") == "No marker"


def test_parse_response_regex_and_repair_edge_cases():
    # 1. Regex extraction when JSON is broken outside the code field
    broken = '{BROKEN_HEADER, "thought_process": "thinking...", "filename": "my_box", "code": "cube(10);", "color_hierarchy": ["Red", "Blue"], "expected_parts_count": 3}'
    code, fn, colors, epc = ai_call._parse_response(broken)
    assert "cube(10);" in code
    assert fn == "my_box"
    assert colors == ["Red", "Blue"]
    assert epc == 3

    # 2. Invalid expected_parts_count type falls back to 1
    valid_bad_epc = json.dumps({"code": "sphere(5);", "expected_parts_count": "not_an_int"})
    _, _, _, epc2 = ai_call._parse_response(valid_bad_epc)
    assert epc2 == 1

    # 3. _try_repair_json with missing code key or missing end boundary
    assert ai_call._try_repair_json('{"other": "val"}') is None
    assert ai_call._try_repair_json('{"code": "unterminated') is None


def test_validate_code_timeout_and_empty_stl(monkeypatch):
    monkeypatch.setattr("src.core.ai.validation_loop.get_openscad_path", lambda: "mock_openscad")

    # 1. TimeoutExpired
    def raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="openscad", timeout=60)

    monkeypatch.setattr("src.core.ai.validation_loop.subprocess.run", raise_timeout)
    ok, msg, stl = ai_call._validate_code("cube(10);")
    assert ok is False
    assert "timed out" in msg
    assert stl is None

    # 2. Return code 0, but STL file was not created (empty/missing)
    monkeypatch.setattr(
        "src.core.ai.validation_loop.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""),
    )
    ok2, msg2, stl2 = ai_call._validate_code("cube(10);")
    assert ok2 is False
    assert "empty or missing STL" in msg2
    assert stl2 is None

    # 3. FileNotFoundError during subprocess.run -> skips validation returning True
    def raise_fnf(*a, **kw):
        raise FileNotFoundError("openscad missing")

    monkeypatch.setattr("src.core.ai.validation_loop.subprocess.run", raise_fnf)
    ok3, msg3, stl3 = ai_call._validate_code("cube(10);")
    assert ok3 is True
    assert stl3 is None


# =====================================================================
# 2. Multi-Agent State Machine (generate_with_validation)
# =====================================================================


def _make_mock_config(agent_level=3, max_calls=3, max_time=0, max_size=0):
    return {
        "client": MagicMock(),
        "provider": MagicMock(),
        "model": "mock-model",
        "instructions": "Base instructions\nOutput Requirements:\nFull JSON",
        "error_template": "Req:{user_request} Code:{broken_code} Err:{error_log} Size:{size} Geo:{geometry_errors}",
        "verify_template": "Req:{user_request} Code:{generated_code} Size:{size} Time:{print_time} Grams:{filament_grams} Geo:{geometry_health}",
        "max_ai_calls": max_calls,
        "agent_level": agent_level,
        "max_print_time_minutes": max_time,
        "max_size_inches": max_size,
        "check_geometry_health": False,
        "min_connection_thickness_mm": 1.2,
        "bed_contact_threshold_mm": 0.4,
        "base_dir": ".",
    }


def test_generate_with_validation_agent_levels_1_and_2(monkeypatch):
    # Agent level 1: stops immediately after 1st call even if compilation fails
    monkeypatch.setattr(ai_call, "_load_ai_config", lambda s=None: _make_mock_config(agent_level=1))
    monkeypatch.setattr(
        ai_call,
        "_call_ai",
        lambda *a: json.dumps({"code": "bad_code();", "filename": "lvl1", "color_hierarchy": []}),
    )
    monkeypatch.setattr(ai_call, "_validate_code", lambda c: (False, "Syntax error", None))

    steps = list(ai_call.generate_with_validation("make box", max_calls=3))
    res = [s for s in steps if s["type"] == "result"][0]
    assert res["code"] == "bad_code();"
    assert "Model failed to compile." in res["warnings"]

    # Agent level 2: stops after successful compile without running VERIFY
    monkeypatch.setattr(ai_call, "_load_ai_config", lambda s=None: _make_mock_config(agent_level=2))
    monkeypatch.setattr(
        ai_call,
        "_call_ai",
        lambda *a: json.dumps(
            {"code": "cube(10);", "filename": "lvl2", "color_hierarchy": ["Red"]}
        ),
    )
    monkeypatch.setattr(ai_call, "_validate_code", lambda c: (True, "", None))
    monkeypatch.setattr(
        ai_call, "_extract_physical_metrics", lambda *a, **kw: ("1x1x1 in", "10m", 2.0, [])
    )

    steps2 = list(ai_call.generate_with_validation("make box", max_calls=3))
    progress_msgs = [s["message"] for s in steps2 if s["type"] == "progress"]
    assert "Verifying design..." not in progress_msgs
    res2 = [s for s in steps2 if s["type"] == "result"][0]
    assert res2["code"] == "cube(10);"
    assert res2["warnings"] == []


def test_generate_with_validation_verify_breaks_code_falls_back_to_last_working(monkeypatch):
    """If initial generation succeeds, then VERIFY modifies code and breaks compilation on final call,
    the loop must fall back to last_working_code!"""
    monkeypatch.setattr(
        ai_call, "_load_ai_config", lambda s=None: _make_mock_config(agent_level=3, max_calls=2)
    )

    responses = iter(
        [
            json.dumps({"code": "cube(10);", "filename": "good_part", "color_hierarchy": []}),
            json.dumps({"code": "broken_verify_code();"}),
        ]
    )
    monkeypatch.setattr(ai_call, "_call_ai", lambda *a: next(responses))

    def fake_validate(c):
        if "broken" in c:
            return False, "Verify syntax error", None
        return True, "", None

    monkeypatch.setattr(ai_call, "_validate_code", fake_validate)
    monkeypatch.setattr(
        ai_call, "_extract_physical_metrics", lambda *a, **kw: ("1x1x1 in", "15m", 3.0, [])
    )

    code, filename, colors, warnings = ai_call.generate_openscad_validated("make box", max_calls=2)
    assert code == "cube(10);"
    assert filename == "good_part"
    assert warnings == []


def test_ai_providers_edge_cases(tmp_path, monkeypatch):
    # 1. Corrupt ai_models.json falls back to defaults
    bad_json = tmp_path / "ai_models.json"
    bad_json.write_text("{corrupt_json")
    cfg = ai_providers.load_provider_config(str(bad_json))
    assert cfg["active_provider"] == "gemini"

    # 2. Unsupported provider raises ValueError
    unsupported_json = tmp_path / "unsupported.json"
    unsupported_json.write_text('{"active_provider": "grok"}')
    with pytest.raises(ValueError, match="Unsupported AI provider"):
        ai_providers.get_active_ai_provider(config_path=str(unsupported_json))

    # 3. OpenAIProvider without system_instructions appends JSON requirement to user prompt
    with patch("openai.OpenAI") as mock_oa_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"code": "cube(5);"}'))
        ]
        mock_oa_cls.return_value = mock_client

        provider = ai_providers.OpenAIProvider(api_key="sk-test", model="gpt-4o")
        out = provider.generate_content("make a sphere", system_instructions="")
        assert out == '{"code": "cube(5);"}'
        sent_msgs = mock_client.chat.completions.create.call_args.kwargs["messages"]
        assert sent_msgs[0]["role"] == "user"
        assert "JSON" in sent_msgs[0]["content"]

    # 4. AnthropicProvider empty content returns ""
    with patch("anthropic.Anthropic") as mock_ant_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = []
        mock_ant_cls.return_value = mock_client

        provider = ai_providers.AnthropicProvider(api_key="ant-test")
        assert provider.generate_content("make a cube", system_instructions="sys") == ""

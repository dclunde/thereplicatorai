"""Tests for AI physical metadata awareness in prompt building and template formatting."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.ai import validation_loop as gemini
from src.core.ai.validation_loop import (
    _build_initial_prompt,
    _validate_code,
    _extract_physical_metrics,
)


# ── _build_initial_prompt tests ──────────────────────────────────


def test_build_initial_prompt_with_physical_metadata():
    """When the latest assistant turn has size/time/grams, they appear in the prompt."""
    history = [
        {"role": "user", "text": "make a cube"},
        {
            "role": "assistant",
            "code": "cube([10,10,10]);",
            "size": "0.39 × 0.39 × 0.39 in",
            "time": "12m",
            "grams": 3.5,
        },
    ]
    prompt = _build_initial_prompt("make it taller", history)

    assert "Physical Properties of Previous Model:" in prompt
    assert "0.39 × 0.39 × 0.39 in" in prompt
    assert "12m" in prompt
    assert "3.5g" in prompt
    assert "New User Edit Request: make it taller" in prompt


def test_build_initial_prompt_no_metadata():
    """When the latest assistant turn has no size field, no physical properties block appears."""
    history = [
        {"role": "user", "text": "make a cube"},
        {"role": "assistant", "code": "cube([10,10,10]);"},
    ]
    prompt = _build_initial_prompt("make it taller", history)

    assert "Physical Properties" not in prompt
    assert "Latest OpenSCAD code generated:" in prompt
    assert "New User Edit Request: make it taller" in prompt


# ── Template formatting tests ────────────────────────────────────


def test_error_template_size_injected():
    """error_prompt.md with {size} renders correctly for both real and unavailable cases."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    error_prompt_file = os.path.join(base_dir, "inputs", "ai_prompts", "error_prompt.md")
    with open(error_prompt_file, "r", encoding="utf-8") as f:
        template = f.read()

    # Real size case
    result = template.format(
        user_request="a cube",
        broken_code="cube(10);",
        error_log="ERROR: some error",
        size="0.39 × 0.39 × 0.39 in",
        geometry_errors="Geometry Error: Part #2 starts at Z=15.40mm in mid-air",
    )
    assert "Bounding Size: 0.39 × 0.39 × 0.39 in" in result
    assert "a cube" in result
    assert "cube(10);" in result
    assert "Geometry Error: Part #2" in result

    # Unavailable case
    result2 = template.format(
        user_request="a sphere",
        broken_code="sphere(5);",
        error_log="FATAL: syntax error",
        size="Unavailable (Compilation Error)",
        geometry_errors="",
    )
    assert "Bounding Size: Unavailable (Compilation Error)" in result2


def test_verify_template_all_metrics_injected():
    """verify_prompt.md with {size}, {print_time}, {filament_grams} renders correctly."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    verify_prompt_file = os.path.join(base_dir, "inputs", "ai_prompts", "verify_prompt.md")
    with open(verify_prompt_file, "r", encoding="utf-8") as f:
        template = f.read()

    result = template.format(
        user_request="a gear",
        generated_code="cylinder(h=5, r=10);",
        size="1.20 × 1.20 × 0.20 in",
        print_time="45m",
        filament_grams=14.2,
        geometry_health="Passed (1 part(s) detected, all grounded at Z=0, minimum joint thickness >= 1.2mm)",
    )
    assert "Bounding Size: 1.20 × 1.20 × 0.20 in" in result
    assert "Estimated Print Time: 45m" in result
    assert "Estimated Filament Usage: 14.2g" in result
    assert "Geometry Health: Passed" in result
    assert "a gear" in result
    assert "cylinder(h=5, r=10);" in result


# ── _validate_code return signature tests ────────────────────────


def test_validate_code_returns_stl_path_on_success():
    """After refactor, _validate_code returns a 3-tuple with stl_path on success."""
    with patch("src.core.ai.validation_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        # Mock get_openscad_path to return a fake path
        with patch("src.core.ai.validation_loop.get_openscad_path", return_value="mock_openscad"):
            # We need the STL file to actually exist for the size check
            import tempfile

            # Since _validate_code creates its own temp dir, we need to intercept
            # tempfile.mkdtemp and pre-create a fake STL in the temp dir
            original_mkdtemp = tempfile.mkdtemp

            def fake_mkdtemp(**kwargs):
                d = original_mkdtemp(**kwargs)
                # Create a fake STL with some content
                stl_path = os.path.join(d, "validate.stl")
                with open(stl_path, "w") as f:
                    f.write("solid fake\nendsolid fake\n")
                return d

            with patch("src.core.ai.validation_loop.tempfile.mkdtemp", side_effect=fake_mkdtemp):
                success, error_msg, stl_path = _validate_code("cube(10);")
                assert success is True
                assert error_msg == ""
                assert stl_path is not None
                assert stl_path.endswith("validate.stl")

                # Caller must clean up
                import shutil

                if stl_path and os.path.exists(os.path.dirname(stl_path)):
                    shutil.rmtree(os.path.dirname(stl_path), ignore_errors=True)


def test_validate_code_returns_none_on_failure():
    """On compilation failure, stl_path is None and temp dir is cleaned."""
    with patch("src.core.ai.validation_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="ERROR: syntax error", stdout="")

        with patch("src.core.ai.validation_loop.get_openscad_path", return_value="mock_openscad"):
            success, error_msg, stl_path = _validate_code("bad code")
            assert success is False
            assert "syntax error" in error_msg
            assert stl_path is None


# ── _extract_physical_metrics tests ──────────────────────────────


def test_extract_physical_metrics_returns_defaults_when_no_stl():
    """When stl_path is None, returns default values without error."""
    size, print_time, grams, geo_errors = _extract_physical_metrics(None)
    assert size == "Unknown"
    assert print_time == "Unknown"
    assert grams == 0.0
    assert geo_errors == []


def test_extract_physical_metrics_with_valid_stl(tmp_path):
    """When given a valid STL, extracts size and attempts slicing."""
    # Create a simple ASCII STL
    stl_file = tmp_path / "test.stl"
    stl_file.write_text(
        "solid test\n"
        "  facet normal 0 0 0\n"
        "    outer loop\n"
        "      vertex 0.0 0.0 0.0\n"
        "      vertex 20.0 0.0 0.0\n"
        "      vertex 0.0 20.0 0.0\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid test\n"
    )

    with patch("src.core.slicer.slicer_3mf.slice_stl_to_3mf", return_value=None):
        size, print_time, grams, geo_errors = _extract_physical_metrics(str(stl_file))

    # Size should be extracted from the STL geometry
    assert size != "Unknown"
    # Slicing was mocked to return None, so time/grams stay at defaults
    assert print_time == "Unknown"
    assert grams == 0.0

    # Temp dir should have been cleaned up by _extract_physical_metrics
    assert not os.path.exists(str(tmp_path))


# ── Integration test: metrics reach verify template ──────────────


def test_generate_with_validation_metrics_reach_verify():
    """In a mocked end-to-end run, the verify template call receives metrics."""
    captured_verify_prompt = []

    def mock_call_gemini(client, model, instructions, content_prompt):
        # Capture the verify prompt — it's the one that contains "Verification Agent"
        if "Verification Agent" in content_prompt:
            captured_verify_prompt.append(content_prompt)
        return '{"code": "cube([10,10,10]);"}'

    mock_config = {
        "client": MagicMock(),
        "model": "test-model",
        "instructions": "Test instructions\n\nOutput Requirements:\nReturn JSON.",
        "error_template": "Fix: {user_request} {broken_code} {error_log} {size} {geometry_errors}",
        "verify_template": (
            "Verification Agent. {user_request}\n{generated_code}\n"
            "Size: {size}\nTime: {print_time}\nGrams: {filament_grams}g\n"
            "Geometry Health: {geometry_health}"
        ),
        "max_ai_calls": 3,
        "base_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }

    with (
        patch("src.core.ai.validation_loop._load_ai_config", return_value=mock_config),
        patch("src.core.ai.validation_loop._call_ai", side_effect=mock_call_gemini),
        patch(
            "src.core.ai.validation_loop._validate_code",
            return_value=(True, "", "/tmp/fake/validate.stl"),
        ),
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics",
            return_value=("2.00 × 1.00 × 0.50 in", "30m", 8.5, []),
        ),
    ):
        results = list(gemini.generate_with_validation("make a cube", max_calls=3))

    # Should have captured the verify prompt
    assert len(captured_verify_prompt) > 0, "Verify template was never called"
    verify_prompt = captured_verify_prompt[0]
    assert "2.00 × 1.00 × 0.50 in" in verify_prompt
    assert "30m" in verify_prompt
    assert "8.5g" in verify_prompt

    # Should have produced a result
    result_steps = [s for s in results if s["type"] == "result"]
    assert len(result_steps) == 1


def test_verification_agent_modified_code_triggers_reverification():
    """When Verification Agent modifies code and it compiles, state goes to VERIFY to re-verify."""
    calls = []

    def mock_call_gemini(client, model, instructions, content_prompt):
        calls.append(content_prompt)
        # Turn 1 (Initial generation)
        if len(calls) == 1:
            return '{"code": "cube([10,10,10]);"}'
        # Turn 2 (First verification - modifies code)
        elif len(calls) == 2:
            return '{"code": "cube([12,12,12]);"}'
        # Turn 3 (Second verification - approves/returns identical code)
        else:
            return '{"code": "cube([12,12,12]);"}'

    mock_config = {
        "client": MagicMock(),
        "model": "test-model",
        "instructions": "Test instructions\n\nOutput Requirements:\nReturn JSON.",
        "error_template": "Fix: {user_request} {broken_code} {error_log} {size} {geometry_errors}",
        "verify_template": "Verification Agent. {user_request}\n{generated_code}\nSize: {size}\nGeometry Health: {geometry_health}",
        "max_ai_calls": 5,
        "agent_level": 4,
        "base_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }

    with (
        patch("src.core.ai.validation_loop._load_ai_config", return_value=mock_config),
        patch("src.core.ai.validation_loop._call_ai", side_effect=mock_call_gemini),
        patch(
            "src.core.ai.validation_loop._validate_code",
            return_value=(True, "", "/tmp/fake/validate.stl"),
        ),
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics",
            return_value=("1.0 in", "10m", 5.0, []),
        ),
    ):
        results = list(gemini.generate_with_validation("make a cube", max_calls=5))

    # Expect 3 calls: initial generation + 1st verify (modified code) + 2nd verify (approved)
    assert len(calls) == 3
    result_steps = [s for s in results if s["type"] == "result"]
    assert len(result_steps) == 1
    assert result_steps[0]["code"] == "cube([12,12,12]);"


def test_generate_with_validation_geometry_error_triggers_error_fix():
    """When initial generation has geometry errors, state machine enters ERROR_FIX with geometry_errors injected."""
    captured_prompts = []

    def mock_call_gemini(client, model, instructions, content_prompt):
        captured_prompts.append(content_prompt)
        if len(captured_prompts) == 1:
            # Turn 1: Initial generation (returns floating part code)
            return '{"code": "translate([0,0,10]) cube(10);", "expected_parts_count": 1}'
        elif len(captured_prompts) == 2:
            # Turn 2: Error Fix agent (returns grounded code)
            return '{"code": "translate([0,0,0]) cube(10);"}'
        else:
            # Turn 3: Verify agent (approves unchanged)
            return '{"code": "translate([0,0,0]) cube(10);"}'

    geo_error = "Geometry Error: Part #1 starts at Z=10.00mm in mid-air"

    def mock_extract(stl_path, **kwargs):
        # Call 1 has geometry errors; subsequent calls are fixed
        if len(captured_prompts) <= 1:
            return ("1.0 in", "15m", 4.0, [geo_error])
        return ("1.0 in", "15m", 4.0, [])

    mock_config = {
        "client": MagicMock(),
        "model": "test-model",
        "instructions": "Test instructions\n\nOutput Requirements:\nReturn JSON.",
        "error_template": "Fix: {user_request}\nBroken: {broken_code}\nErrors: {error_log}\nSize: {size}\nGeo: {geometry_errors}",
        "verify_template": "Verification Agent. {user_request}\n{generated_code}\nSize: {size}\nHealth: {geometry_health}",
        "max_ai_calls": 3,
        "agent_level": 3,
        "base_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }

    with (
        patch("src.core.ai.validation_loop._load_ai_config", return_value=mock_config),
        patch("src.core.ai.validation_loop._call_ai", side_effect=mock_call_gemini),
        patch(
            "src.core.ai.validation_loop._validate_code",
            return_value=(True, "", "/tmp/fake/validate.stl"),
        ),
        patch("src.core.ai.validation_loop._extract_physical_metrics", side_effect=mock_extract),
    ):
        results = list(gemini.generate_with_validation("make a cube", max_calls=3))

    # Expect 3 calls: initial generate -> error fix (triggered by geo error) -> verify
    assert len(captured_prompts) == 3
    # Turn 2 must be the error fix prompt containing the geometry error
    assert geo_error in captured_prompts[1]

    result_steps = [s for s in results if s["type"] == "result"]
    assert len(result_steps) == 1
    assert result_steps[0]["code"] == "translate([0,0,0]) cube(10);"


def test_generate_with_validation_persistent_geometry_errors_survive_to_warnings():
    """When geometry errors persist and max_calls is reached, the model still returns code with geometry warnings."""
    geo_error = "Geometry Error: Part #1 contains a weak structural bottleneck (< 1.2mm) near [X=0, Y=0, Z=10mm]."

    mock_config = {
        "client": MagicMock(),
        "model": "test-model",
        "instructions": "Test instructions\n\nOutput Requirements:\nReturn JSON.",
        "error_template": "Fix: {user_request}\nErrors: {error_log}\nGeo: {geometry_errors}",
        "verify_template": "Verification: {generated_code}",
        "max_ai_calls": 2,
        "agent_level": 3,
        "base_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }

    with (
        patch("src.core.ai.validation_loop._load_ai_config", return_value=mock_config),
        patch(
            "src.core.ai.validation_loop._call_ai", return_value='{"code": "cylinder(r=5, h=20);"}'
        ),
        patch(
            "src.core.ai.validation_loop._validate_code",
            return_value=(True, "", "/tmp/fake/validate.stl"),
        ),
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics",
            return_value=("1.0 in", "15m", 4.0, [geo_error]),
        ),
    ):
        results = list(gemini.generate_with_validation("make a dumbbell", max_calls=2))

    result_steps = [s for s in results if s["type"] == "result"]
    assert len(result_steps) == 1
    # Code is returned despite persistent warnings
    assert result_steps[0]["code"] == "cylinder(r=5, h=20);"
    # Warnings contain the persistent geometry error
    assert any("bottleneck" in w for w in result_steps[0]["warnings"])


def test_generate_with_validation_single_pass_relocates_artifacts(tmp_path):
    """When output_dir and file_basename are provided, _validate_code and _extract_physical_metrics
    run only once (for a 1-call run) and relocate the .stl and .3mf files into output_dir."""
    import tempfile

    mock_config = {
        "client": MagicMock(),
        "model": "test-model",
        "instructions": "Test instructions\n\nOutput Requirements:\nReturn JSON.",
        "error_template": "Fix: {user_request} {broken_code} {error_log} {size} {geometry_errors}",
        "verify_template": "Verification: {generated_code}",
        "max_ai_calls": 1,
        "agent_level": 1,
        "base_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }

    created_temp_dirs = []

    def fake_validate(code_str):
        d = tempfile.mkdtemp(prefix="ttp_validate_")
        created_temp_dirs.append(d)
        stl_p = os.path.join(d, "validate.stl")
        with open(stl_p, "w", encoding="utf-8") as f:
            f.write("solid fake\nendsolid fake\n")
        return True, "", stl_p

    def fake_extract(
        stl_p, expected_parts=None, enable_supports=False, keep_artifacts=False, **kwargs
    ):
        # Also create the sliced .3mf next to validate.stl
        threemf_p = os.path.splitext(stl_p)[0] + "_sliced.3mf"
        with open(threemf_p, "w", encoding="utf-8") as f:
            f.write("fake 3mf content")
        return ("1.00 × 1.00 × 1.00 in", "25m", 6.2, [])

    out_dir = str(tmp_path / "v1")
    os.makedirs(out_dir, exist_ok=True)

    with (
        patch("src.core.ai.validation_loop._load_ai_config", return_value=mock_config),
        patch("src.core.ai.validation_loop._call_ai", return_value='{"code": "cube(10);"}'),
        patch("src.core.ai.validation_loop._validate_code", side_effect=fake_validate) as mock_val,
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics", side_effect=fake_extract
        ) as mock_ext,
    ):
        results = list(
            gemini.generate_with_validation(
                "make a cube",
                max_calls=1,
                enable_supports=True,
                output_dir=out_dir,
                file_basename="design_v1_test",
            )
        )

    # Must only validate and extract ONCE (no redundant post-loop pass)
    assert mock_val.call_count == 1
    assert mock_ext.call_count == 1
    assert mock_ext.call_args.kwargs.get("enable_supports") is True
    assert mock_ext.call_args.kwargs.get("keep_artifacts") is True

    result_steps = [s for s in results if s["type"] == "result"]
    assert len(result_steps) == 1
    res = result_steps[0]

    expected_stl = os.path.join(out_dir, "design_v1_test.stl")
    expected_3mf = os.path.join(out_dir, "design_v1_test_sliced.3mf")
    assert res["stl_path"] == expected_stl
    assert res["3mf_path"] == expected_3mf
    assert os.path.exists(expected_stl)
    assert os.path.exists(expected_3mf)
    assert res["size"] == "1.00 × 1.00 × 1.00 in"
    assert res["time"] == "25m"
    assert res["grams"] == 6.2

    # Temporary validation directory should be cleaned up after relocation
    for d in created_temp_dirs:
        assert not os.path.exists(d)

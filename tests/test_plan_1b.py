import os
from unittest.mock import MagicMock, patch


import pytest

from src.core.config import (
    ASSETS_DIR,
    BASE_DIR,
    OUTPUTS_DIR,
    SESSIONS_DIR,
    TMP_DIR,
    has_openscad,
    has_slicer,
    load_env_keys,
)
from src.core.sessions import (
    delete_session,
    get_session,
    list_sessions,
    parse_and_strip_support_flags,
    purge_heavy_assets,
    save_session_atomic,
)
from src.core.slicer.openscad import (
    calculate_max_dimensions,
    clean_scad_code,
    format_dimensions_inches,
)
from src.core.slicer.slicer_3mf import (
    format_time_str,
    slice_stl_to_3mf,
)
from src.core.printer.color_matcher import (
    get_blandness_score,
    get_closest_color_name,
    match_color,
    normalize_hex_color,
)
from src.core.printer.bambu_client import (
    BambuClient,
    PrinterBusyError,
)
from src.core.ai.json_repair import (
    _try_repair_json,
    parse_ai_response,
)
from src.core.ai.prompts import (
    _build_agent_instructions,
    _build_constraint_hint,
    _build_initial_prompt,
)
from src.core.ai.validation_loop import _evaluate_candidate_code


# ---------------------------------------------------------
# 1. Configuration & Paths
# ---------------------------------------------------------
def test_config_paths_and_env():
    assert os.path.isdir(BASE_DIR)
    assert OUTPUTS_DIR.replace("\\", "/").endswith("outputs")
    assert TMP_DIR.replace("\\", "/").endswith("outputs/tmp")
    assert SESSIONS_DIR.replace("\\", "/").endswith("outputs/sessions")
    assert ASSETS_DIR.replace("\\", "/").endswith("inputs/assets")

    keys = load_env_keys()
    assert isinstance(keys, dict)
    assert isinstance(has_openscad(), bool)
    assert isinstance(has_slicer(), bool)


# ---------------------------------------------------------
# 2. Atomic Sessions & Utilities
# ---------------------------------------------------------
def test_session_atomic_crud(tmp_path):
    session_id = "test-session-crud-123"
    data = {"prompt": "test cube", "status": "active", "scad_code": "cube(10);"}

    with patch("src.core.sessions.SESSIONS_DIR", str(tmp_path)):
        filepath = save_session_atomic(session_id, data)
        assert os.path.exists(filepath)

        # Ensure no dangling temporary files remain
        tmp_files = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
        assert len(tmp_files) == 0

        # Retrieve session
        loaded = get_session(session_id)
        assert loaded is not None
        assert loaded["prompt"] == "test cube"

        # List sessions
        sessions = list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == session_id

        # Delete session
        deleted = delete_session(session_id)
        assert deleted is True
        assert get_session(session_id) is None
        assert not os.path.exists(filepath)


def test_session_purge_heavy_assets(tmp_path):
    session_id = "purge-test-session"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True)

    stl_file = session_dir / "model.stl"
    stl_file.write_text("solid test endsolid")
    preview_file = session_dir / "preview.png"
    preview_file.write_text("fake png bytes")
    mf_file = session_dir / "model.3mf"
    mf_file.write_text("fake 3mf bytes")
    scad_file = session_dir / "model.scad"
    scad_file.write_text("cube(5);")

    session_data = {
        "id": session_id,
        "files": {
            "stl": str(stl_file),
            "preview": str(preview_file),
            "3mf": str(mf_file),
            "scad": str(scad_file),
        },
    }

    with patch("src.core.sessions.SESSIONS_DIR", str(tmp_path)):
        purge_heavy_assets(session_id, session_data)

    assert not stl_file.exists()
    assert not preview_file.exists()
    assert not mf_file.exists()
    assert scad_file.exists()
    assert session_data.get("purged") is True


def test_parse_and_strip_support_flags():
    prompt1, flag1 = parse_and_strip_support_flags("make a phone stand --support")
    assert prompt1 == "make a phone stand"
    assert flag1 is True

    prompt2, flag2 = parse_and_strip_support_flags("generate a hook --no-support please")
    assert prompt2 == "generate a hook  please"
    assert flag2 is False

    prompt3, flag3 = parse_and_strip_support_flags("simple dice")
    assert prompt3 == "simple dice"
    assert flag3 is None


# ---------------------------------------------------------
# 3. Slicer CWD Isolation & Helpers
# ---------------------------------------------------------
def test_slicer_time_formatting():
    assert format_time_str(30) == "30s"
    assert format_time_str(125) == "2m 5s"
    assert format_time_str(3665) == "1h 1m 5s"
    assert format_time_str(None) == "Unknown"


def test_openscad_dimensions_and_clean():
    code = "```openscad\nmodule test() {\n  cube([10, 20, 30]);\n}\ntest();\n```"
    cleaned = clean_scad_code(code)
    assert "```" not in cleaned
    assert "module test()" in cleaned

    dims = calculate_max_dimensions([[0, 0, 0], [10, 20, 30], [5, 5, 5]])
    assert dims == (10, 20, 30)

    inch_str = format_dimensions_inches((25.4, 50.8, 76.2))
    assert '1.0" x 2.0" x 3.0"' in inch_str


def test_slicer_cwd_isolation(tmp_path):
    import zipfile

    dummy_stl = tmp_path / "part.stl"
    dummy_stl.write_text("solid part endsolid")

    captured_cwd = []

    def mock_subprocess_run(cmd, **kwargs):
        captured_cwd.append(kwargs.get("cwd"))
        # Create expected intermediate / output files
        target_file = None
        if "--output" in cmd:
            target_file = cmd[cmd.index("--output") + 1]
        elif "--export-3mf" in cmd:
            idx = cmd.index("--export-3mf") + 1
            if idx < len(cmd) and cmd[idx] != "--output":
                target_file = cmd[idx]
        if target_file:
            cwd = kwargs.get("cwd")
            if cwd and not os.path.isabs(target_file):
                target_file = os.path.join(cwd, target_file)
            with zipfile.ZipFile(target_file, "w") as zf:
                zf.writestr("3D/dummy.model", b"dummy")
        return MagicMock(returncode=0, stdout="success", stderr="")

    with (
        patch("subprocess.run", side_effect=mock_subprocess_run),
        patch("src.core.slicer.slicer_3mf.get_prusa_slicer_path", return_value="fake_prusa.exe"),
        patch("src.core.slicer.slicer_3mf.get_bambu_studio_path", return_value="fake_bambu.exe"),
    ):
        res = slice_stl_to_3mf(str(dummy_stl))
        assert len(captured_cwd) > 0
        # Verify execution cwd was isolated inside outputs/tmp/
        assert "tmp" in captured_cwd[0]


# ---------------------------------------------------------
# 4. Color Matcher & Bambu Printer Concurrency
# ---------------------------------------------------------
def test_color_matcher_functions():
    assert normalize_hex_color("#FFFFFF") == "FFFFFF"
    assert normalize_hex_color("123456") == "123456"

    # Bland colors (black, white, grey) have 0 colorfulness score
    assert get_blandness_score("000000") == 0
    assert get_blandness_score("FFFFFF") == 0

    assert get_closest_color_name("FF0000") == "Red"
    assert get_closest_color_name("0000FF") == "Blue"

    available = {
        0: {"type": "PLA", "color": "FF0000", "remain": 50},
        1: {"type": "PLA", "color": "00FF00", "remain": 80},
    }
    # Match red
    assert match_color(["Red"], available, required_grams=10) == 0
    # Match green
    assert match_color(["Green"], available, required_grams=10) == 1


def test_bambu_client_busy_and_lock(tmp_path):
    client = BambuClient("user", "pass", "dev123")
    lock_file = tmp_path / "printer_dispatch.lock"

    with patch("src.core.printer.bambu_client.LOCK_FILE", str(lock_file)):
        # Test acquiring and releasing lock
        assert client._acquire_printer_lock() is True
        assert os.path.exists(lock_file)
        # Attempting to acquire again immediately should fail
        assert client._acquire_printer_lock() is False
        client._release_printer_lock()
        assert not os.path.exists(lock_file)

        # Test PrinterBusyError during send_print_job
        with patch.object(
            client, "is_printer_busy", return_value=(True, "Printer is actively printing")
        ):
            with pytest.raises(PrinterBusyError):
                client.send_print_job("test.3mf")


def test_dispatch_lock_held_until_mqtt_busy_transition(tmp_path):
    import json

    lock_file = tmp_path / "printer_dispatch.lock"
    client = BambuClient("user", "pass", "dev123", ip="192.168.1.50", access_code="1234")
    client.connected = True
    client.last_status = {"state": "IDLE"}

    with (
        patch("src.core.printer.bambu_client.LOCK_FILE", str(lock_file)),
        patch.object(client, "_dispatch_lan_print", return_value=True),
    ):
        assert client.send_print_job("test.3mf") is True
        # Dispatch lock must still be held after send_print_job returns when connected!
        assert client._holding_dispatch_lock is True
        assert lock_file.exists()

        # A second concurrent print attempt must be blocked by the held dispatch lock
        with pytest.raises(PrinterBusyError):
            client.send_print_job("second.3mf")

        # Once MQTT reports a BUSY_STATES transition (e.g. PREPARE), lock is released
        msg = MagicMock()
        msg.payload = json.dumps({"print": {"gcode_state": "PREPARE"}}).encode("utf-8")
        client._on_message(None, None, msg)

        assert client._holding_dispatch_lock is False
        assert not lock_file.exists()


# ---------------------------------------------------------
# 5. AI Providers, Prompts, & Validation Loop
# ---------------------------------------------------------
def test_ai_prompts_and_json_repair():
    instructions = _build_agent_instructions()
    assert "OpenSCAD" in instructions

    initial_prompt = _build_initial_prompt("a small dog", 50, 50, 50)
    assert "small dog" in initial_prompt

    hint = _build_constraint_hint(100, 100, 100)
    assert "100" in hint

    # JSON repair
    malformed = '{"code": "cube(10);", "unclosed": "brace"'
    repaired = _try_repair_json(malformed)
    assert repaired is not None
    assert repaired.get("code") == "cube(10);"

    parsed = parse_ai_response(
        '```json\n{"code": "cylinder(r=5, h=10);", "description": "cylinder"}\n```'
    )
    assert parsed.get("code") == "cylinder(r=5, h=10);"


def test_unified_evaluate_candidate_code():
    valid_code = "cube([10, 10, 10]);"

    # Candidate with valid compilation and metrics
    with (
        patch("src.core.ai.validation_loop._validate_code", return_value=(True, "", "dummy.stl")),
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics",
            return_value=('1.0" x 1.0" x 1.0"', "15m", 5.0, []),
        ),
        patch(
            "src.core.ai.validation_loop.calculate_stl_dimensions", return_value=(10.0, 10.0, 10.0)
        ),
    ):
        result = _evaluate_candidate_code(
            valid_code,
            expected_parts_count=1,
            config={"max_size_inches": 5.0, "max_print_time_minutes": 60},
        )
        assert result.success is True
        assert result.dimensions == (10.0, 10.0, 10.0)
        assert result.next_state == "verify"

    # Candidate exceeding size constraints
    with (
        patch("src.core.ai.validation_loop._validate_code", return_value=(True, "", "dummy.stl")),
        patch(
            "src.core.ai.validation_loop._extract_physical_metrics",
            return_value=('10.0" x 10.0" x 10.0"', "15m", 5.0, []),
        ),
        patch(
            "src.core.ai.validation_loop.calculate_stl_dimensions",
            return_value=(254.0, 254.0, 254.0),
        ),
    ):
        result = _evaluate_candidate_code(
            valid_code,
            expected_parts_count=1,
            config={"max_size_inches": 5.0},
        )
        assert result.success is True
        assert len(result.constraint_errors) > 0
        assert result.next_state == "error_fix"


# ---------------------------------------------------------
# 6. Verify shared/ directory deletion
# ---------------------------------------------------------
def test_shared_directory_removed():
    shared_path = os.path.join(BASE_DIR, "shared")
    assert not os.path.exists(shared_path), (
        f"Legacy 'shared/' directory still exists at {shared_path}!"
    )

"""Intensive unit and edge-case tests for CLI utilities and shared helpers:
- shared/manual_print.py
- shared/stop_print.py
- shared/github_cli.py
- shared/utils.py edge cases
"""

import os
import struct
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import github_cli
from src.cli import manual_print, stop_print
from src.core import utils


# =====================================================================
# 1. shared/manual_print.py
# =====================================================================


def test_manual_print_file_not_found(monkeypatch):
    inputs = iter(['"nonexistent_model.scad"'])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    with pytest.raises(SystemExit) as exc:
        manual_print.main()
    assert exc.value.code == 1


def test_manual_print_unsupported_extension(tmp_path, monkeypatch):
    bad_file = tmp_path / "model.obj"
    bad_file.write_text("v 0 0 0")
    inputs = iter([str(bad_file), "n", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    with pytest.raises(SystemExit) as exc:
        manual_print.main()
    assert exc.value.code == 1


def test_manual_print_scad_local_only_no_print(tmp_path, monkeypatch):
    scad_file = tmp_path / "part.scad"
    scad_file.write_text("cube(10);")
    out_3mf = tmp_path / "part.3mf"
    out_3mf.write_text("dummy 3mf")

    inputs = iter([f'"{scad_file}"', "n", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("src.core.slicer.slicer_3mf.get_enable_supports_config", lambda: False)

    with patch(
        "src.cli.manual_print.compile_and_slice", return_value=str(out_3mf)
    ) as mock_compile_slice:
        manual_print.main()
        mock_compile_slice.assert_called_once_with(str(scad_file), enable_supports=True)


def test_manual_print_stl_slice_failure_exits(tmp_path, monkeypatch):
    stl_file = tmp_path / "part.stl"
    stl_file.write_text("solid part endsolid part")

    inputs = iter([str(stl_file), "n", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("src.core.slicer.slicer_3mf.get_enable_supports_config", lambda: True)

    with patch("src.cli.manual_print.slice_stl_to_3mf", return_value=None):
        with pytest.raises(SystemExit) as exc:
            manual_print.main()
        assert exc.value.code == 1


@pytest.mark.parametrize(
    "slot_input,expected_slot,print_ok",
    [
        ("", None, True),
        ("ext", 4, True),
        ("external", 4, True),
        ("2", 2, True),
        ("9", None, False),  # Out of range -> defaults to auto (None)
        ("invalid_text", None, True),  # ValueError -> defaults to auto (None)
    ],
)
def test_manual_print_send_to_printer_slot_variants(
    tmp_path, monkeypatch, slot_input, expected_slot, print_ok
):
    stl_file = tmp_path / "part.stl"
    stl_file.write_text("solid part endsolid part")
    out_3mf = tmp_path / "part.3mf"
    out_3mf.write_text("dummy 3mf")

    inputs = iter([str(stl_file), "y", slot_input, ""])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("src.core.slicer.slicer_3mf.get_enable_supports_config", lambda: False)

    mock_cloud = MagicMock()
    mock_cloud.send_print_job.return_value = print_ok

    with (
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value=str(out_3mf)),
        patch("src.cli.manual_print.BambuCloud", return_value=mock_cloud),
    ):
        manual_print.main()
        mock_cloud.connect_mqtt.assert_called_once()
        mock_cloud.send_print_job.assert_called_once_with(str(out_3mf), ams_slot=expected_slot)


# =====================================================================
# 2. shared/stop_print.py
# =====================================================================


@pytest.mark.parametrize("stop_ok", [True, False])
def test_stop_print_cli_main(monkeypatch, stop_ok):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mock_cloud = MagicMock()
    mock_cloud.stop_print.return_value = stop_ok

    with patch("src.cli.stop_print.BambuCloud", return_value=mock_cloud):
        stop_print.main()
        mock_cloud.connect_mqtt.assert_called_once()
        mock_cloud.stop_print.assert_called_once()
        mock_cloud.disconnect_mqtt.assert_called_once()


# =====================================================================
# 3. shared/github_cli.py
# =====================================================================


def test_get_gh_executable_fallbacks(monkeypatch):
    # 1. Found on PATH
    monkeypatch.setattr("shutil.which", lambda _: "C:/bin/gh.exe")
    assert github_cli.get_gh_executable() == "C:/bin/gh.exe"

    # 2. Found in VS path
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("os.path.exists", lambda p: "Visual Studio" in p)
    assert "Visual Studio" in github_cli.get_gh_executable()

    # 3. Found in WinGet path
    monkeypatch.setattr("os.path.exists", lambda p: "GitHub CLI" in p)
    assert "GitHub CLI" in github_cli.get_gh_executable()

    # 4. Not found anywhere
    monkeypatch.setattr("os.path.exists", lambda _: False)
    assert github_cli.get_gh_executable() is None


def test_run_gh_command_missing_and_error(monkeypatch):
    monkeypatch.setattr(github_cli, "get_gh_executable", lambda: None)
    assert github_cli.run_gh_command(["status"]) is None

    monkeypatch.setattr(github_cli, "get_gh_executable", lambda: "gh")
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(OSError("spawn error"))
    )
    assert github_cli.run_gh_command(["status"]) is None


def test_check_gh_status_variants(monkeypatch):
    monkeypatch.setattr(github_cli, "get_gh_executable", lambda: None)
    assert github_cli.check_gh_status() is False

    monkeypatch.setattr(github_cli, "get_gh_executable", lambda: "gh")
    monkeypatch.setattr(
        github_cli, "run_gh_command", lambda args: MagicMock(returncode=0, stdout="Logged in")
    )
    assert github_cli.check_gh_status() is True

    monkeypatch.setattr(
        github_cli, "run_gh_command", lambda args: MagicMock(returncode=1, stderr="Not logged in")
    )
    assert github_cli.check_gh_status() is False


def test_sync_secrets_and_workflows_and_release(monkeypatch):
    # Empty keys
    monkeypatch.setattr(github_cli, "load_api_keys", lambda: {})
    assert github_cli.sync_secrets() is False

    # Mix of placeholder, empty, success, and failure keys
    monkeypatch.setattr(
        github_cli,
        "load_api_keys",
        lambda: {
            "GEMINI_API_KEY": "real_key",
            "OPENAI_API_KEY": "YOUR_OPENAI_KEY",
            "ANTHROPIC_API_KEY": "",
            "BAMBU_PASSWORD": "fail_key",
        },
    )

    def fake_run_gh(args):
        if "fail_key" in args:
            return MagicMock(returncode=1, stderr="API error")
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(github_cli, "run_gh_command", fake_run_gh)
    assert github_cli.sync_secrets() is True

    # Workflows & release success + failure
    github_cli.list_workflows()
    github_cli.trigger_workflow("python-ci.yml")
    github_cli.create_release("v1.0.0")

    monkeypatch.setattr(
        github_cli, "run_gh_command", lambda args: MagicMock(returncode=1, stderr="Failed")
    )
    github_cli.list_workflows()
    github_cli.trigger_workflow("python-ci.yml")
    github_cli.create_release("v1.0.0", title="Custom", notes="Notes")


@pytest.mark.parametrize(
    "choice,extra_input",
    [
        ("1", None),
        ("2", None),
        ("3", None),
        ("4", None),
        ("5", None),
        ("6", "v1.2.3"),
        ("0", None),
        ("99", None),
    ],
)
def test_github_cli_menu_choices(monkeypatch, choice, extra_input):
    inputs = iter([choice, extra_input] if extra_input else [choice])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(github_cli, "check_gh_status", lambda: True)
    monkeypatch.setattr(github_cli, "sync_secrets", lambda: True)
    monkeypatch.setattr(github_cli, "list_workflows", lambda: None)
    monkeypatch.setattr(github_cli, "trigger_workflow", lambda *_: None)
    monkeypatch.setattr(github_cli, "create_release", lambda *_: None)
    github_cli.cli_menu()


# =====================================================================
# 5. shared/utils.py Edge Cases
# =====================================================================


def test_load_api_keys_comments_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        'GEMINI_API_KEY="gem_123" # inline comment\n'
        "OPENAI_API_KEY='oa_456'\n"
        "BAMBU_PRINTER_IP=192.168.1.50 # lan ip\n"
        "UNKNOWN_KEY=ignored\n"
    )
    keys = utils.load_api_keys(str(env_file))
    assert keys["GEMINI_API_KEY"] == "gem_123"
    assert keys["OPENAI_API_KEY"] == "oa_456"
    assert keys["BAMBU_PRINTER_IP"] == "192.168.1.50"
    assert "UNKNOWN_KEY" not in keys


def test_calculate_max_dimensions_binary_stl(tmp_path):
    bin_stl = tmp_path / "binary_box.stl"
    with open(bin_stl, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", 2))  # 2 triangles = 84 + 100 = 184 bytes
        # Triangle 1: (0,0,0), (25.4, 0, 0), (0, 50.8, 0)
        f.write(struct.pack("<3f", 0, 0, 1))
        f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
        f.write(struct.pack("<3f", 25.4, 0.0, 0.0))
        f.write(struct.pack("<3f", 0.0, 50.8, 0.0))
        f.write(struct.pack("<H", 0))
        # Triangle 2: (0,0,0), (25.4, 50.8, 12.7), (0, 0, 12.7)
        f.write(struct.pack("<3f", 0, 0, 1))
        f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
        f.write(struct.pack("<3f", 25.4, 50.8, 12.7))
        f.write(struct.pack("<3f", 0.0, 0.0, 12.7))
        f.write(struct.pack("<H", 0))

    dims = utils.calculate_max_dimensions(str(bin_stl))
    assert pytest.approx(dims[0], 0.01) == 25.4
    assert pytest.approx(dims[1], 0.01) == 50.8
    assert pytest.approx(dims[2], 0.01) == 12.7

    formatted = utils.format_dimensions_inches(str(bin_stl))
    assert formatted == "1.00 × 2.00 × 0.50 in"

    # Nonexistent file returns (0.0, 0.0, 0.0) and "Unknown"
    assert utils.format_dimensions_inches(str(tmp_path / "missing.stl")) == "Unknown"


def test_binary_discovery_and_ci_fallbacks(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda _: False)
    monkeypatch.setattr("shutil.which", lambda _: None)

    # When CI is set, returns mock_* identifiers
    monkeypatch.setenv("CI", "true")
    assert utils.is_ci_environment() is True
    assert utils.get_bambu_studio_path() == "mock_bambu"
    assert utils.get_prusa_slicer_path() == "mock_bambu"
    assert utils.get_openscad_path() == "mock_openscad"
    assert utils.has_openscad() is False
    assert utils.has_slicer() is False

    # When CI is not set, raises FileNotFoundError
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GIT_TESTING", raising=False)
    assert utils.is_ci_environment() is False
    with pytest.raises(FileNotFoundError):
        utils.get_bambu_studio_path()
    with pytest.raises(FileNotFoundError):
        utils.get_prusa_slicer_path()
    with pytest.raises(FileNotFoundError):
        utils.get_openscad_path()
    assert utils.has_openscad() is False
    assert utils.has_slicer() is False


def test_color_and_time_edge_cases():
    assert utils.normalize_hex_color("") == ""
    assert utils.normalize_hex_color("#FF00AAFF") == "FF00AA"
    assert utils.get_blandness_score("invalid") == 999
    assert utils.get_blandness_score("#GGGGGG") == 999
    assert utils.get_closest_color_name("") == "Unknown"
    assert utils.get_closest_color_name("12") == "Unknown"
    assert utils.get_closest_color_name("ZZZZZZ") == "Unknown"
    assert utils.format_time_str("7260") == "2h 1m"
    assert utils.format_time_str("total estimated time: 45m 10s; extra") == "45m 10s"
    assert utils.match_color(["Red"], {}, 10.0) is None


def test_cli_scripts_importable_as_subprocesses(tmp_path, monkeypatch):
    """Verify CLI, bot, and launcher scripts bootstrap ROOT_DIR into sys.path before src imports."""
    import ast
    import subprocess

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    # 1. Subprocess smoke check: manual_print.py invoked directly as a script from tmp_path
    manual_script = os.path.join(repo_root, "src", "cli", "manual_print.py")
    res_manual = subprocess.run(
        [sys.executable, manual_script, "--support"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "ModuleNotFoundError" not in res_manual.stderr
    assert "Usage:" in res_manual.stdout

    # 2. Verify all entry-point scripts bootstrap ROOT_DIR into sys.path before any `from src.` import
    entry_scripts = [
        os.path.join("src", "launcher.py"),
        os.path.join("src", "cli", "manual_print.py"),
        os.path.join("src", "cli", "stop_print.py"),
        os.path.join("src", "cli", "simple_mode.py"),
        os.path.join("src", "bots", "google_tasks", "bot.py"),
    ]
    for rel_script in entry_scripts:
        full_script = os.path.join(repo_root, rel_script)
        with open(full_script, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=full_script)
        first_src_import_line = None
        sys_path_insert_line = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
                if first_src_import_line is None or node.lineno < first_src_import_line:
                    first_src_import_line = node.lineno
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("insert", "append")
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "path"
            ):
                if sys_path_insert_line is None or node.lineno < sys_path_insert_line:
                    sys_path_insert_line = node.lineno
        assert sys_path_insert_line is not None, f"{rel_script} missing sys.path bootstrap"
        assert first_src_import_line is not None, f"{rel_script} missing src imports"
        assert sys_path_insert_line < first_src_import_line, (
            f"{rel_script} imports from src (line {first_src_import_line}) "
            f"before sys.path bootstrap (line {sys_path_insert_line})"
        )

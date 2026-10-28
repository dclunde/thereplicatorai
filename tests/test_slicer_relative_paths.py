import json
import os
import pathlib
import zipfile
from unittest.mock import MagicMock, patch

from src.cli import manual_print
from src.core.sessions import write_version_print_scripts
from src.core.slicer.slicer_3mf import (
    _can_reuse_existing_3mf,
    compile_and_slice,
    slice_stl_to_3mf,
)


def test_slice_stl_to_3mf_with_relative_path(tmp_path, monkeypatch):
    """Verify that passing a relative path to slice_stl_to_3mf passes absolute paths to slicer subprocess."""
    dummy_dir = tmp_path / "work"
    dummy_dir.mkdir()
    stl_file = dummy_dir / "my_part.stl"
    stl_file.write_text("solid part endsolid part", encoding="utf-8")

    monkeypatch.chdir(dummy_dir)

    captured_cmds = []

    def mock_subprocess_run(cmd, **kwargs):
        captured_cmds.append((cmd, kwargs.get("cwd")))
        # Create intermediate and target output archives
        for i, arg in enumerate(cmd):
            if arg in ("--output", "--export-3mf"):
                target = cmd[i + 1]
                if not os.path.isabs(target) and kwargs.get("cwd"):
                    target = os.path.join(kwargs["cwd"], target)
                if not os.path.exists(target):
                    with zipfile.ZipFile(target, "w") as zf:
                        zf.writestr("3D/dummy.model", b"dummy")
        return MagicMock(returncode=0, stdout="success", stderr="")

    with (
        patch("subprocess.run", side_effect=mock_subprocess_run),
        patch("src.core.slicer.slicer_3mf.get_prusa_slicer_path", return_value="fake_prusa.exe"),
        patch("src.core.slicer.slicer_3mf.get_bambu_studio_path", return_value="fake_bambu.exe"),
    ):
        res = slice_stl_to_3mf("my_part.stl")
        assert res is not None
        assert os.path.isabs(res)
        assert res.endswith("my_part.3mf")

        # Verify PrusaSlicer received absolute path to STL
        prusa_cmd = captured_cmds[0][0]
        assert any(os.path.isabs(arg) and arg.endswith("my_part.stl") for arg in prusa_cmd)
        # Verify OrcaSlicer received absolute path for output 3MF
        orca_cmd = captured_cmds[1][0]
        export_idx = orca_cmd.index("--export-3mf")
        assert os.path.isabs(orca_cmd[export_idx + 1])


def test_slice_stl_to_3mf_nonexistent_file():
    """Verify that slice_stl_to_3mf returns None immediately when file does not exist."""
    res = slice_stl_to_3mf("completely_nonexistent_file_123.stl")
    assert res is None


def test_can_reuse_existing_3mf_relative_path_and_support_mismatch(tmp_path, monkeypatch):
    """Verify relative path normalization and support mismatch detection in 3MF reuse."""
    dummy_dir = tmp_path / "work2"
    dummy_dir.mkdir()
    stl = dummy_dir / "item.stl"
    stl.write_text("solid item endsolid item", encoding="utf-8")
    three_mf = dummy_dir / "item.3mf"

    # Create 3MF with enable_support = "0"
    with zipfile.ZipFile(three_mf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", '{"enable_support": "0"}')

    monkeypatch.chdir(dummy_dir)

    # 1. Matching supports (False) returns candidate path
    reused = _can_reuse_existing_3mf("item.stl", enable_supports=False)
    assert reused is not None
    assert os.path.isabs(reused)

    # 2. Mismatching supports (True requested, but 3MF has False) returns None
    reused_mismatch = _can_reuse_existing_3mf("item.stl", enable_supports=True)
    assert reused_mismatch is None


def test_manual_print_cli_notifies_on_support_mismatch(tmp_path, monkeypatch, capsys):
    """Verify run_cli_print logs an informative message when re-slicing due to support mismatch."""
    dummy_dir = tmp_path / "work3"
    dummy_dir.mkdir()
    stl = dummy_dir / "model.stl"
    stl.write_text("solid model endsolid model", encoding="utf-8")
    three_mf = dummy_dir / "model.3mf"
    with zipfile.ZipFile(three_mf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", '{"enable_support": "0"}')

    monkeypatch.chdir(dummy_dir)

    mock_client = MagicMock()
    mock_client.is_printer_busy.return_value = (False, "Idle")
    mock_client.get_available_colors.return_value = {}
    mock_client.send_print_job.return_value = True

    with (
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value=str(three_mf)),
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=5.0),
        patch("src.cli.manual_print._make_client", return_value=mock_client),
    ):
        manual_print.run_cli_print(
            "model.stl", requested_colors=[], force_slot=None, enable_supports=True
        )

    out = capsys.readouterr().out
    assert (
        "Existing 3MF does not match requested support settings (supports=True). Re-slicing..."
        in out
    )


def test_compile_and_slice_normalizes_relative_scad(tmp_path, monkeypatch):
    """Verify compile_and_slice handles relative SCAD paths properly."""
    dummy_dir = tmp_path / "work4"
    dummy_dir.mkdir()
    scad = dummy_dir / "part.scad"
    scad.write_text("cube(10);", encoding="utf-8")

    monkeypatch.chdir(dummy_dir)

    with (
        patch("subprocess.run") as mock_run,
        patch("src.core.slicer.slicer_3mf.get_openscad_path", return_value="fake_openscad.exe"),
        patch(
            "src.core.slicer.slicer_3mf.slice_stl_to_3mf", return_value="/fake/out.3mf"
        ) as mock_slice,
    ):
        res = compile_and_slice("part.scad")
        assert res == "/fake/out.3mf"
        # OpenSCAD command received absolute paths
        cmd = mock_run.call_args[0][0]
        assert os.path.isabs(cmd[2])  # output stl
        assert os.path.isabs(cmd[3])  # input scad
        # slice_stl_to_3mf received absolute stl path
        stl_arg = mock_slice.call_args[0][0]
        assert os.path.isabs(stl_arg)


def test_write_version_print_scripts_generates_no_batch_scripts(tmp_path):
    """Verify write_version_print_scripts generates zero batch scripts and removes any legacy ones."""
    v_dir = tmp_path / "v1"
    v_dir.mkdir()
    legacy_print = v_dir / "print.bat"
    legacy_slice = v_dir / "slice.bat"
    legacy_print.write_text("echo legacy print")
    legacy_slice.write_text("echo legacy slice")

    res = write_version_print_scripts(str(v_dir), str(v_dir / "model.stl"))
    assert res == ("", "")
    assert not legacy_print.exists()
    assert not legacy_slice.exists()


def test_only_start_bat_in_repo():
    """Verify that the only .bat file in the entire repository is start.bat at the root."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    bat_files = []
    for p in repo_root.rglob("*.bat"):
        # Exclude virtual environments and node environment tools cache
        p_str = str(p)
        if any(ignore in p_str for ignore in (".venv", ".cache", "node_modules")):
            continue
        rel = p.relative_to(repo_root)
        bat_files.append(rel.as_posix())

    assert bat_files == ["start.bat"], f"Expected only ['start.bat'], found: {bat_files}"


def test_compile_openscad_generator_filters_compilation_failure_warnings(tmp_path, monkeypatch):
    """Verify that compile_openscad_generator suppresses 'Model failed to compile.' when STL succeeds."""
    from src.web.backend.routes.sessions import compile_openscad_generator

    ws = tmp_path / "sessions"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(ws))
    monkeypatch.setattr("src.web.backend.routes.sessions.get_workspace_dir", lambda: str(ws))

    # Prebuilt stl and 3mf
    v1_dir = ws / "sess1" / "v1"
    v1_dir.mkdir(parents=True)
    stl_path = v1_dir / "design.stl"
    stl_path.write_bytes(b"solid test\nendsolid test\n")
    three_mf_path = v1_dir / "design.3mf"
    three_mf_path.write_bytes(b"dummy 3mf")

    # Run generator with incoming warning containing "Model failed to compile."
    steps = list(
        compile_openscad_generator(
            scad_code="cube(10);",
            session_id="sess1",
            version=1,
            skip_compile=True,
            prebuilt_stl=str(stl_path),
            prebuilt_3mf=str(three_mf_path),
            prebuilt_size="1.0 x 1.0 x 1.0 in",
            prebuilt_time="10m",
            prebuilt_grams=5.0,
            warnings=["Model failed to compile.", "Real warning: floating part"],
        )
    )
    complete_step = next(s for s in steps if s["status"] == "complete")
    assets = complete_step["assets"]
    assert "Model failed to compile." not in assets["warnings"]
    assert "Real warning: floating part" in assets["warnings"]


def test_load_session_strips_stale_compilation_warning_when_stl_exists(tmp_path, monkeypatch):
    """Verify load_session dynamically purges 'Model failed to compile.' if the version has an STL."""
    from src.web.backend.routes.sessions import load_session

    ws = tmp_path / "sessions"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(ws))
    monkeypatch.setattr("src.web.backend.routes.sessions.get_workspace_dir", lambda: str(ws))

    sess_dir = ws / "test_session_123"
    v1_dir = sess_dir / "v1"
    v1_dir.mkdir(parents=True)
    (v1_dir / "part.stl").write_bytes(b"solid a\nendsolid a\n")

    session_json = sess_dir / "session.json"
    data = {
        "id": "test_session_123",
        "title": "Test",
        "history": [
            {"role": "user", "text": "make something"},
            {
                "role": "assistant",
                "code": "cube(10);",
                "warnings": ["Model failed to compile.", "Keep this physical warning"],
            },
        ],
        "versions": 1,
    }
    session_json.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_session("test_session_123")
    assistant_msg = loaded["history"][1]
    assert "Model failed to compile." not in assistant_msg["warnings"]
    assert "Keep this physical warning" in assistant_msg["warnings"]
    assert loaded["all_versions"][0]["warnings"] == ["Keep this physical warning"]

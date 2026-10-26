"""Intensive unit and edge-case tests for shared/bambu_printer.py and shared/bambu_cloud.py."""

import json
import os
import subprocess
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.slicer import slicer_3mf as bambu_printer
from src.core.printer.bambu_client import BambuCloud


# =====================================================================
# 1. shared/bambu_printer.py
# =====================================================================


def test_compile_and_slice_delegates_properly(tmp_path, monkeypatch):
    scad = tmp_path / "model.scad"
    scad.write_text("cube(10);")
    monkeypatch.setattr("src.core.slicer.slicer_3mf.get_openscad_path", lambda: "mock_openscad")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0))
    monkeypatch.setattr(
        "src.core.slicer.slicer_3mf.slice_stl_to_3mf",
        lambda stl, enable_supports=None: f"{stl}.3mf",
    )

    res = bambu_printer.compile_and_slice(str(scad), enable_supports=True)
    assert res.endswith("model.stl.3mf")


def test_get_enable_supports_config(tmp_path, monkeypatch):
    assert isinstance(bambu_printer.get_enable_supports_config(), bool)


def test_slice_stl_to_3mf_support_injection_and_orient_fallback(tmp_path, monkeypatch):
    stl_file = tmp_path / "part.stl"
    stl_file.write_text("solid part endsolid part")

    monkeypatch.setattr(
        "src.core.slicer.slicer_3mf.get_prusa_slicer_path", lambda: "orca-slicer.exe"
    )
    monkeypatch.setattr(
        "src.core.slicer.slicer_3mf.get_bambu_studio_path", lambda: "orca-slicer.exe"
    )

    call_log = []

    def fake_subprocess_run(cmd, **kwargs):
        call_log.append(cmd)
        # Step 1: geometry export
        if "--export-3mf" in cmd and "--slice" not in cmd:
            temp_3mf_path = cmd[cmd.index("--export-3mf") + 1]
            with zipfile.ZipFile(temp_3mf_path, "w") as z:
                z.writestr("3D/3dmodel.model", "<model/>")
                z.writestr("[Content_Types].xml", "<Types/>")
                z.writestr("_rels/.rels", "<Relationships/>")
            return MagicMock(returncode=0)
        # Step 3a: first slice with --orient 1 fails!
        if "--orient" in cmd and cmd[cmd.index("--orient") + 1] == "1":
            raise subprocess.CalledProcessError(
                returncode=1, cmd=cmd, output="orient err", stderr=""
            )
        # Step 3b: fallback slice with --orient 0 succeeds!
        if "--orient" in cmd and cmd[cmd.index("--orient") + 1] == "0":
            out_3mf_path = cmd[cmd.index("--export-3mf") + 1]
            # Verify that ready_3mf had enable_support="1" injected
            ready_3mf_path = cmd[-1]
            with zipfile.ZipFile(ready_3mf_path, "r") as rz:
                if "Metadata/project_settings.config" in rz.namelist():
                    cfg = json.loads(rz.read("Metadata/project_settings.config").decode("utf-8"))
                    assert cfg.get("enable_support") == "1"
            with open(out_3mf_path, "wb") as f:
                f.write(b"sliced_3mf_content")
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    out = bambu_printer.slice_stl_to_3mf(
        str(stl_file), outdir=str(tmp_path), file_basename="custom_out", enable_supports=True
    )
    assert out == str(tmp_path / "custom_out.3mf")
    assert os.path.exists(out)
    assert any("--orient" in c and "0" in c for c in call_log)


def test_slice_stl_to_3mf_both_orientations_fail(tmp_path, monkeypatch):
    stl_file = tmp_path / "part.stl"
    stl_file.write_text("solid part endsolid part")
    monkeypatch.setattr(
        "src.core.slicer.slicer_3mf.get_prusa_slicer_path", lambda: "prusa-slicer.exe"
    )
    monkeypatch.setattr(
        "src.core.slicer.slicer_3mf.get_bambu_studio_path", lambda: "orca-slicer.exe"
    )

    def fake_run(cmd, **kwargs):
        if "--output" in cmd:
            temp_3mf_path = cmd[cmd.index("--output") + 1]
            with zipfile.ZipFile(temp_3mf_path, "w") as z:
                z.writestr("3D/3dmodel.model", "<model/>")
                z.writestr("[Content_Types].xml", "<Types/>")
                z.writestr("_rels/.rels", "<Relationships/>")
            return MagicMock(returncode=0)
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, output="out", stderr="err")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert bambu_printer.slice_stl_to_3mf(str(stl_file)) is None


def test_bambu_printer_cli_error_cases(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")

    # 1. Invalid slot number (out of range)
    with patch.object(sys, "argv", ["bambu_printer.py", str(dummy_stl), "--slot", "9"]):
        with pytest.raises(SystemExit):
            bambu_printer.main()

    # 2. Non-integer slot argument
    with patch.object(sys, "argv", ["bambu_printer.py", str(dummy_stl), "--slot", "bad"]):
        with pytest.raises(SystemExit):
            bambu_printer.main()

    # 3. Missing STL argument
    with patch.object(sys, "argv", ["bambu_printer.py"]):
        with pytest.raises(SystemExit):
            bambu_printer.main()

    # 4. Non-existent STL file
    with patch.object(sys, "argv", ["bambu_printer.py", str(tmp_path / "missing.stl")]):
        with pytest.raises(SystemExit):
            bambu_printer.main()

    # 5. Selected slot has PETG (unsupported filament)
    mock_cloud = MagicMock()
    mock_cloud.get_available_colors.return_value = {1: {"type": "PETG", "color": "FF0000"}}
    with (
        patch.object(sys, "argv", ["bambu_printer.py", str(dummy_stl), "--slot", "1"]),
        patch("src.core.slicer.slicer_3mf.slice_stl_to_3mf", return_value="test.3mf"),
        patch("src.core.slicer.slicer_3mf.get_filament_usage_from_3mf", return_value=5.0),
        patch("src.core.slicer.slicer_3mf.BambuCloud", return_value=mock_cloud),
    ):
        with pytest.raises(SystemExit):
            bambu_printer.main()

    # 6. All 5 send_print_job attempts fail
    mock_cloud2 = MagicMock()
    mock_cloud2.get_available_colors.return_value = {}
    mock_cloud2.send_print_job.return_value = False
    with (
        patch.object(sys, "argv", ["bambu_printer.py", str(dummy_stl)]),
        patch("src.core.slicer.slicer_3mf.slice_stl_to_3mf", return_value="test.3mf"),
        patch("src.core.slicer.slicer_3mf.get_filament_usage_from_3mf", return_value=5.0),
        patch("src.core.slicer.slicer_3mf.BambuCloud", return_value=mock_cloud2),
    ):
        with pytest.raises(SystemExit):
            bambu_printer.main()
        assert mock_cloud2.send_print_job.call_count == 5


# =====================================================================
# 2. shared/bambu_cloud.py
# =====================================================================


def test_bambu_cloud_upload_3mf_and_formatted_status(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    cloud = BambuCloud("user", "pass", "serial", ip="192.168.1.10", access_code="123456")

    # Not authenticated -> returns (None, None)
    f_path = tmp_path / "model.3mf"
    f_path.write_bytes(b"3mf_data")
    assert cloud.upload_3mf(str(f_path)) == (None, None)

    # With mocked bambu_client returning URL via get_cloud_files fallback
    mock_bc = MagicMock()
    mock_bc.upload_file.return_value = {}
    mock_bc.get_cloud_files.return_value = [{"name": "model.3mf", "url": "https://s3/model.3mf"}]
    cloud.bambu_client = mock_bc
    url, md5 = cloud.upload_3mf(str(f_path))
    assert url == "https://s3/model.3mf"
    assert len(md5) == 32

    # Formatted status across states
    cloud.last_status = {}
    cloud.ams_data_received = False
    assert "unknown or unavailable" in cloud.get_formatted_status()

    cloud.last_status = {"state": "IDLE"}
    assert "Not Printing (IDLE)" in cloud.get_formatted_status()

    cloud.last_status = {"state": "RUNNING", "percent": 42, "remaining_time": 18}
    status_str = cloud.get_formatted_status()
    assert "RUNNING" in status_str
    assert "42%" in status_str
    assert "18 mins" in status_str


def test_bambu_cloud_on_message_vt_tray_and_lights():
    cloud = BambuCloud("user", "pass", "serial", ip="192.168.1.10", access_code="123456")
    cloud._schedule_light_off(delay=60)
    assert cloud._light_off_timer is not None

    msg = MagicMock()
    msg.payload = json.dumps(
        {
            "print": {
                "gcode_state": "IDLE",
                "mc_percent": 100,
                "mc_remaining_time": 0,
                "ams": {
                    "ams": [
                        {
                            "tray": [
                                {"id": "0", "tray_color": "", "tray_type": ""},  # empty tray
                                {
                                    "id": "1",
                                    "tray_color": "00FF00FF",
                                    "tray_type": "PLA",
                                },  # non-bambu
                            ]
                        }
                    ]
                },
                "vt_tray": {
                    "tray_color": "FF0000FF",
                    "tray_type": "PLA",
                    "tag_uid": "UID123",
                    "remain": 80,
                },
                "lights_report": [{"node": "chamber_light", "mode": "off"}],
            }
        }
    ).encode("utf-8")

    cloud._on_message(None, None, msg)
    assert 0 not in cloud.ams_colors
    assert cloud.ams_colors[1]["color"] == "00FF00"
    assert cloud.ams_colors[1]["remain"] == -1
    assert cloud.ams_colors[4]["is_external"] is True
    assert cloud.ams_colors[4]["color"] == "FF0000"
    assert cloud.ams_colors[4]["remain"] == 80
    # Light report mode=="off" cancels timer
    assert cloud._light_off_timer is None


def test_bambu_cloud_get_snapshot_with_mocked_cv2(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    cloud = BambuCloud("user", "pass", "serial", ip="192.168.1.10", access_code="123456")
    cloud.mqtt_client = MagicMock()

    out_img = str(tmp_path / "snap.jpg")

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, "fake_frame")

    with (
        patch("cv2.VideoCapture", return_value=mock_cap),
        patch("cv2.imwrite", side_effect=lambda p, f: open(p, "wb").write(b"jpg")),
    ):
        res = cloud.get_snapshot(out_img)
        assert res == out_img
        assert os.path.exists(out_img)

    # When camera stream fails to open -> returns None
    mock_cap.isOpened.return_value = False
    with patch("cv2.VideoCapture", return_value=mock_cap):
        assert cloud.get_snapshot(out_img) is None

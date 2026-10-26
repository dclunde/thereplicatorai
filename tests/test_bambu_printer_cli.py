import sys
from unittest.mock import MagicMock, patch

from src.cli import manual_print as bambu_printer


def test_bambu_printer_cli_support_flag(tmp_path):
    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")

    test_args = ["manual_print.py", str(dummy_stl), "Red", "--support"]
    mock_cloud = MagicMock()
    mock_cloud.get_status.return_value = {"state": "IDLE"}
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {}
    mock_cloud.send_print_job.return_value = True

    with (
        patch.object(sys, "argv", test_args),
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value="test.3mf") as mock_slice,
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=0.0),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_slice.assert_called_once_with(str(dummy_stl), enable_supports=True)


def test_bambu_printer_cli_no_support_flag(tmp_path):
    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")

    test_args = ["manual_print.py", str(dummy_stl), "Red", "--no-support"]
    mock_cloud = MagicMock()
    mock_cloud.get_status.return_value = {"state": "IDLE"}
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {}
    mock_cloud.send_print_job.return_value = True

    with (
        patch.object(sys, "argv", test_args),
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value="test.3mf") as mock_slice,
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=0.0),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_slice.assert_called_once_with(str(dummy_stl), enable_supports=False)


def test_bambu_printer_cli_default_no_flag(tmp_path):
    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")

    test_args = ["manual_print.py", str(dummy_stl), "Red"]
    mock_cloud = MagicMock()
    mock_cloud.get_status.return_value = {"state": "IDLE"}
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {}
    mock_cloud.send_print_job.return_value = True

    with (
        patch.object(sys, "argv", test_args),
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value="test.3mf") as mock_slice,
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=0.0),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_slice.assert_called_once_with(str(dummy_stl), enable_supports=None)


def test_bambu_printer_cli_slot_4_and_ext(tmp_path):
    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")

    test_args = ["manual_print.py", str(dummy_stl), "Red", "--slot", "4"]
    mock_cloud = MagicMock()
    mock_cloud.get_status.return_value = {"state": "IDLE"}
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {4: {"type": "PLA", "color": "FFFFFF"}}
    mock_cloud.send_print_job.return_value = True

    with (
        patch.object(sys, "argv", test_args),
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value="test.3mf"),
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=0.0),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_cloud.send_print_job.assert_called_once_with("test.3mf", ams_slot=4)

    # Test with string 'ext'
    test_args_ext = ["manual_print.py", str(dummy_stl), "Red", "--slot", "ext"]
    mock_cloud.reset_mock()
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {4: {"type": "PLA", "color": "FFFFFF"}}
    mock_cloud.send_print_job.return_value = True
    with (
        patch.object(sys, "argv", test_args_ext),
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value="test.3mf"),
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=0.0),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_cloud.send_print_job.assert_called_once_with("test.3mf", ams_slot=4)


def test_bambu_printer_cli_reuses_existing_3mf_matching_supports(tmp_path):
    import json
    import zipfile

    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")
    dummy_3mf = tmp_path / "test.3mf"
    with zipfile.ZipFile(dummy_3mf, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps({"enable_support": "0"}),
        )

    test_args = ["manual_print.py", str(dummy_stl), "Red", "--no-support"]
    mock_cloud = MagicMock()
    mock_cloud.get_status.return_value = {"state": "IDLE"}
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {}
    mock_cloud.send_print_job.return_value = True

    with (
        patch.object(sys, "argv", test_args),
        patch("src.cli.manual_print.slice_stl_to_3mf") as mock_slice,
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=1.5),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_slice.assert_not_called()
        mock_cloud.send_print_job.assert_called_once_with(str(dummy_3mf), ams_slot=None)


def test_bambu_printer_cli_reslices_when_supports_mismatch(tmp_path):
    import json
    import zipfile

    dummy_stl = tmp_path / "test.stl"
    dummy_stl.write_text("solid test endsolid test")
    dummy_3mf = tmp_path / "test.3mf"
    with zipfile.ZipFile(dummy_3mf, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps({"enable_support": "0"}),
        )

    test_args = ["manual_print.py", str(dummy_stl), "Red", "--support"]
    mock_cloud = MagicMock()
    mock_cloud.get_status.return_value = {"state": "IDLE"}
    mock_cloud.is_printer_busy.return_value = (False, "")
    mock_cloud.get_available_colors.return_value = {}
    mock_cloud.send_print_job.return_value = True

    with (
        patch.object(sys, "argv", test_args),
        patch("src.cli.manual_print.slice_stl_to_3mf", return_value=str(dummy_3mf)) as mock_slice,
        patch("src.cli.manual_print.get_filament_usage_from_3mf", return_value=1.5),
        patch("src.cli.manual_print.BambuClient", return_value=mock_cloud),
    ):
        bambu_printer.main()
        mock_slice.assert_called_once_with(str(dummy_stl), enable_supports=True)

from unittest.mock import patch, MagicMock

from src.core.slicer.slicer_3mf import slice_stl_to_3mf
from src.core.utils import match_color


def test_slicer_integration(tmp_path):
    available_colors = {
        1: {"type": "PLA", "color": "FF0000", "remain": 100},
        2: {"type": "PLA", "color": "00FF00", "remain": 50},
        3: {"type": "PETG", "color": "0000FF", "remain": 10},
    }

    slot_red = match_color("Red", available_colors, required_grams=10.0)
    assert slot_red == 1

    slot_green = match_color("Green", available_colors, required_grams=10.0)
    assert slot_green == 2

    with (
        patch("src.core.slicer.slicer_3mf.subprocess.run") as mock_run,
        patch("zipfile.ZipFile"),
        patch("shutil.move"),
        patch("os.path.exists", return_value=True),
        patch("os.remove"),
    ):
        mock_run.return_value = MagicMock(returncode=0)

        dummy_stl = tmp_path / "test.stl"
        dummy_stl.write_text("solid test\nendsolid test")

        out_3mf = slice_stl_to_3mf(str(dummy_stl))
        assert out_3mf.endswith("test.3mf")
        assert mock_run.call_count >= 1

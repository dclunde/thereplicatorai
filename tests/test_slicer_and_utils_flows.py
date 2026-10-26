import os
import sys
import tempfile

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.utils import (
    calculate_max_dimensions,
    format_dimensions_inches,
    get_blandness_score,
    get_closest_color_name,
    match_color,
)


def test_calculate_max_dimensions_3d():
    # 1. Non-existent file
    assert calculate_max_dimensions("non_existent.stl") == (0.0, 0.0, 0.0)
    assert format_dimensions_inches("non_existent.stl") == "Unknown"

    # 2. ASCII STL with 3D extents (25.4mm x 50.8mm x 76.2mm -> 1.0 x 2.0 x 3.0 inches)
    ascii_stl = b"""solid box
  facet normal 0 0 0
    outer loop
      vertex 0.0 0.0 0.0
      vertex 25.4 0.0 0.0
      vertex 0.0 50.8 76.2
    endloop
  endfacet
endsolid box
"""
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
        f.write(ascii_stl)
        fname = f.name

    try:
        dims_mm = calculate_max_dimensions(fname)
        assert abs(dims_mm[0] - 25.4) < 0.01
        assert abs(dims_mm[1] - 50.8) < 0.01
        assert abs(dims_mm[2] - 76.2) < 0.01

        dims_in_str = format_dimensions_inches(fname)
        assert "1.00 × 2.00 × 3.00 in" in dims_in_str
    finally:
        if os.path.exists(fname):
            os.remove(fname)


def test_color_utilities():
    assert get_blandness_score("#FF0000") > 200  # Vibrant red
    assert get_blandness_score("#000000") == 0  # Black

    assert get_closest_color_name("#FF0000") == "Red"
    assert get_closest_color_name("#00FF00") == "Green"
    assert get_closest_color_name("invalid") == "Unknown"


def test_match_color_pla_selection():
    available = {
        0: {"color": "FF0000", "type": "PETG", "remain": 100, "is_bambu": True},
        1: {"color": "FF0000", "type": "PLA", "remain": 100, "is_bambu": True},
    }
    slot = match_color([], available, 50)
    assert slot == 1, "Should select PLA slot"


import zipfile
from io import BytesIO
from src.core.utils import get_slice_info_from_3mf, purge_heavy_assets, format_time_str


# 3. Bug 3: G-Code memory spike (using deque instead of readlines)
def test_bug3_gcode_memory_spike():
    """Ensure get_slice_info_from_3mf uses bounded memory."""
    # Create an in-memory zip file with a massive mock gcode
    memory_zip = BytesIO()
    with zipfile.ZipFile(memory_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        # We don't actually need to write 100MB, just ensure the logic parses accurately from bottom up
        gcode = (
            b"; some header\n"
            + b"normal line\n" * 2000
            + b"; filament used [g] = 45.2\n"
            + b"end line\n"
        )
        zf.writestr("Metadata/plate_1.gcode", gcode)

    memory_zip.seek(0)

    # We can write it to disk temporarily or mock the zipfile opening
    test_3mf = "test_memory_spike.3mf"
    with open(test_3mf, "wb") as f:
        f.write(memory_zip.read())

    try:
        info = get_slice_info_from_3mf(test_3mf)
        assert info["grams"] == 45.2
    finally:
        if os.path.exists(test_3mf):
            os.remove(test_3mf)


# --- Bug 1: 3-Digit Hex Blandness Score ---
def test_bug1_blandness_score_3digit_hex():
    assert get_blandness_score("#FFF") == 0
    assert get_blandness_score("#000") == 0


# --- Bug 2: 3-Digit Hex Color Name ---
def test_bug2_closest_color_name_3digit_hex():
    assert get_closest_color_name("#F00") == "Red"
    assert get_closest_color_name("#0F0") == "Green"


# --- Bug 3: Exact Filament Match ---
def test_bug3_match_color_exact_grams():
    available = {0: {"color": "FF0000", "type": "PLA", "remain": 5, "is_bambu": True}}
    assert match_color([], available, 50.0) == 0


# --- Bug 9: Case-Insensitive 3MF GCode Extractor ---
def test_bug9_get_slice_info_case_insensitive(tmp_path):
    three_mf_file = tmp_path / "test_model.3mf"
    gcode_content = b"; total estimated time = 45m 12s\n; filament used [g] = 18.5\n"
    with zipfile.ZipFile(three_mf_file, "w") as z:
        z.writestr("Metadata/PLATE_1.GCODE", gcode_content)
    info = get_slice_info_from_3mf(str(three_mf_file))
    assert info["time"] == "45m 12s"


# --- Bug 10: 3D Bounding Box Calculation ---
def test_bug10_calculate_max_dimensions_3d(tmp_path):
    stl_file = tmp_path / "cube.stl"
    stl_file.write_bytes(
        b"solid box facet normal 0 0 0 outer loop vertex 0 0 0 vertex 10 20 30 endloop endfacet endsolid box"
    )
    dims = calculate_max_dimensions(str(stl_file))
    assert len(dims) == 3


# --- Bug 13: Purge Heavy Assets Asset Retention ---
def test_bug13_purge_heavy_assets(tmp_path):
    sdir = tmp_path / "session_1"
    v1 = sdir / "v1"
    v1.mkdir(parents=True)
    (v1 / "design.scad").write_text("cube(10);")
    (v1 / "design.stl").write_text("dummy stl")
    (v1 / "design.3mf").write_text("dummy 3mf")

    purge_heavy_assets(str(sdir))

    assert (v1 / "design.scad").exists()  # SCAD preserved
    assert not (v1 / "design.stl").exists()  # Heavy STL deleted
    assert not (v1 / "design.3mf").exists()  # Heavy 3MF deleted


# --- Bug 16: Raw Seconds Time Formatting ---
def test_bug16_format_time_str_seconds():
    assert format_time_str("3600") == "1h 0m"
    assert format_time_str("180") == "3m"


# --- Bug 17: Truncated Binary STL File Size Check ---
def test_bug17_calculate_max_dimensions_truncated_stl(tmp_path):
    stl_file = tmp_path / "trunc.stl"
    stl_file.write_bytes(b"short header")
    dims = calculate_max_dimensions(str(stl_file))
    assert dims == (0.0, 0.0, 0.0)


# --- Bug 20: Raw Seconds Output String ---
def test_bug20_format_time_str_passthrough():
    assert format_time_str("2h 15m") == "2h 15m"


def test_get_slice_info_detects_has_supports_true_and_false(tmp_path):
    with_sup_3mf = tmp_path / "with_supports.3mf"
    with zipfile.ZipFile(with_sup_3mf, "w") as z:
        z.writestr(
            "Metadata/plate_1.gcode",
            b"; total estimated time = 15m 10s\n"
            b"G1 X10 Y10 E1.0\n"
            b"; TYPE:Tree support\n"
            b"G1 X12 Y12 E1.5\n"
            b"; filament used [g] = 12.3\n",
        )
    info_sup = get_slice_info_from_3mf(str(with_sup_3mf))
    assert info_sup["time"] == "15m 10s"
    assert info_sup["grams"] == 12.3
    assert info_sup["has_supports"] is True

    no_sup_3mf = tmp_path / "no_supports.3mf"
    with zipfile.ZipFile(no_sup_3mf, "w") as z:
        z.writestr(
            "Metadata/plate_1.gcode",
            b"; total estimated time = 8m 00s\n"
            b"; TYPE:Outer wall\n"
            b"G1 X10 Y10 E1.0\n"
            b"; filament used [g] = 4.5\n",
        )
    info_no_sup = get_slice_info_from_3mf(str(no_sup_3mf))
    assert info_no_sup["time"] == "8m 00s"
    assert info_no_sup["grams"] == 4.5
    assert info_no_sup["has_supports"] is False

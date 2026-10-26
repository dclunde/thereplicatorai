import os
import shutil
import struct
import subprocess
import tempfile

from src.core.config import TMP_DIR, get_openscad_path


import re


def clean_scad_code(code: str) -> str:
    """Cleans OpenSCAD code by converting escaped string literals and stripping markdown fences."""
    if not code:
        return ""
    if "\\n" in code and "\n" not in code:
        code = code.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    code = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", code, flags=re.MULTILINE)
    code = re.sub(r"\n?```$", "", code, flags=re.MULTILINE)
    code = code.replace("```", "")
    return code.strip()


def calculate_max_dimensions(stl_file_or_points) -> tuple[float, float, float]:
    """Calculates 3D dimensions (X, Y, Z) in mm from an STL file (binary or ASCII) or point list."""
    if (
        isinstance(stl_file_or_points, (list, tuple))
        and stl_file_or_points
        and isinstance(stl_file_or_points[0], (list, tuple))
    ):
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        for p in stl_file_or_points:
            if p[0] < min_x:
                min_x = p[0]
            if p[0] > max_x:
                max_x = p[0]
            if p[1] < min_y:
                min_y = p[1]
            if p[1] > max_y:
                max_y = p[1]
            if p[2] < min_z:
                min_z = p[2]
            if p[2] > max_z:
                max_z = p[2]
        return (max_x - min_x, max_y - min_y, max_z - min_z)

    stl_file = str(stl_file_or_points)
    try:
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        has_vertices = False

        with open(stl_file, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size < 84:
                return 0.0, 0.0, 0.0
            f.seek(80)
            num_triangles_bytes = f.read(4)
            if len(num_triangles_bytes) == 4:
                num_triangles = struct.unpack("<I", num_triangles_bytes)[0]
                is_binary = file_size == 84 + num_triangles * 50
            else:
                is_binary = False

            f.seek(0)
            if not is_binary:
                for line in f:
                    line = line.strip()
                    if line.startswith(b"vertex "):
                        parts = line.split()
                        if len(parts) >= 4:
                            vx, vy, vz = float(parts[1]), float(parts[2]), float(parts[3])
                            if vx < min_x:
                                min_x = vx
                            if vx > max_x:
                                max_x = vx
                            if vy < min_y:
                                min_y = vy
                            if vy > max_y:
                                max_y = vy
                            if vz < min_z:
                                min_z = vz
                            if vz > max_z:
                                max_z = vz
                            has_vertices = True
            else:
                f.seek(84)
                for _ in range(num_triangles):
                    f.read(12)  # skip normal
                    for _ in range(3):
                        v = struct.unpack("<3f", f.read(12))
                        vx, vy, vz = v[0], v[1], v[2]
                        if vx < min_x:
                            min_x = vx
                        if vx > max_x:
                            max_x = vx
                        if vy < min_y:
                            min_y = vy
                        if vy > max_y:
                            max_y = vy
                        if vz < min_z:
                            min_z = vz
                        if vz > max_z:
                            max_z = vz
                        has_vertices = True
                    f.read(2)  # skip attribute byte count

        if not has_vertices:
            return 0.0, 0.0, 0.0
        return (
            max_x - min_x,
            max_y - min_y,
            max_z - min_z,
        )
    except Exception:
        return 0.0, 0.0, 0.0


calculate_stl_dimensions = calculate_max_dimensions


def format_dimensions_inches(stl_file_or_dims) -> str:
    """Returns a formatted string of 3D dimensions in inches."""
    if isinstance(stl_file_or_dims, (tuple, list)):
        dims_mm = tuple(stl_file_or_dims)
        x_in = dims_mm[0] / 25.4
        y_in = dims_mm[1] / 25.4
        z_in = dims_mm[2] / 25.4
        return f'{x_in:.1f}" x {y_in:.1f}" x {z_in:.1f}"'

    dims_mm = calculate_max_dimensions(stl_file_or_dims)
    if dims_mm == (0.0, 0.0, 0.0):
        return "Unknown"
    x_in = dims_mm[0] / 25.4
    y_in = dims_mm[1] / 25.4
    z_in = dims_mm[2] / 25.4
    return f"{x_in:.2f} × {y_in:.2f} × {z_in:.2f} in"


def compile_scad_to_stl(
    scad_path_or_code: str,
    out_stl_path: str | None = None,
    timeout: int = 60,
) -> tuple[bool, str, str | None]:
    """Compiles OpenSCAD code or file to STL.
    Returns (success: bool, error_msg: str, stl_path: str | None).
    """
    is_file = os.path.exists(scad_path_or_code) and scad_path_or_code.lower().endswith(".scad")
    temp_dir = None

    if is_file:
        scad_file = scad_path_or_code
        if out_stl_path is None:
            out_stl_path = os.path.splitext(scad_file)[0] + ".stl"
    else:
        temp_dir = tempfile.mkdtemp(prefix="ttp_validate_", dir=TMP_DIR)
        scad_file = os.path.join(temp_dir, "validate.scad")
        with open(scad_file, "w", encoding="utf-8") as f:
            f.write(scad_path_or_code)
        if out_stl_path is None:
            out_stl_path = os.path.join(temp_dir, "validate.stl")

    try:
        openscad_bin = get_openscad_path()
        result = subprocess.run(
            [openscad_bin, "-o", out_stl_path, scad_file],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            err = (
                result.stderr.strip()
                if result.stderr
                else f"OpenSCAD exited with code {result.returncode}"
            )
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return False, err, None

        if not os.path.exists(out_stl_path) or os.path.getsize(out_stl_path) == 0:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return (
                False,
                "OpenSCAD produced an empty or missing STL file. The code may not generate any geometry.",
                None,
            )

        return True, "", out_stl_path

    except subprocess.TimeoutExpired:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return (
            False,
            f"OpenSCAD compilation timed out (>{timeout}s). The code may be too complex.",
            None,
        )
    except FileNotFoundError:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return False, "Could not find OpenSCAD executable.", None


def render_scad_preview_png(
    scad_file: str,
    out_png_path: str | None = None,
    timeout: int = 60,
) -> str | None:
    """Renders a 2D preview image of an OpenSCAD model."""
    if not os.path.exists(scad_file):
        return None

    if out_png_path is None:
        out_png_path = os.path.splitext(scad_file)[0] + ".png"

    try:
        openscad_bin = get_openscad_path()
        cmd = [
            openscad_bin,
            "-o",
            out_png_path,
            "--imgsize=1024,768",
            "--colorscheme=Tomorrow Night",
            scad_file,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 and os.path.exists(out_png_path):
            return out_png_path
    except Exception:
        pass
    return None

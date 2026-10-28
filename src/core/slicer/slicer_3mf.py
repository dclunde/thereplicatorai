import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from collections import deque

from src.core.config import (
    CONFIGS_DIR,
    TEMPLATES_DIR,
    TMP_DIR,
    get_bambu_studio_path,
    get_openscad_path,
    get_prusa_slicer_path,
)
from src.core.printer.bambu_client import BambuClient, BambuCloud  # noqa: F401

logger = logging.getLogger(__name__)


def format_time_str(time_raw: str | int | float | None) -> str:
    """Formats raw seconds or time strings into a friendly representation (e.g., '1h 15m 5s')."""
    if time_raw is None:
        return "Unknown"

    total_sec = None
    if isinstance(time_raw, (int, float)):
        total_sec = int(time_raw)
    else:
        raw_str = str(time_raw).strip()
        if not raw_str:
            return "Unknown"
        if raw_str.isdigit():
            total_sec = int(raw_str)

    if total_sec is not None:
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        if h > 0:
            return f"{h}h {m}m {s}s" if s > 0 else f"{h}h {m}m"
        if m > 0:
            return f"{m}m {s}s" if s > 0 else f"{m}m"
        return f"{s}s"

    time_raw = str(time_raw).strip()
    if "total estimated time" in time_raw.lower():
        match = re.search(r"total estimated time[\s:=]+([^;]+)", time_raw, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return time_raw.split(";")[0].strip()


def get_slice_info_from_3mf(three_mf_path: str) -> dict:
    """Extracts filament grams, estimated print time, and support presence from embedded gcode inside 3MF."""
    info = {"grams": 0.0, "time": "Unknown", "has_supports": False}
    if not three_mf_path or not os.path.exists(three_mf_path):
        return info

    try:
        with zipfile.ZipFile(three_mf_path, "r") as z:
            for filename in z.namelist():
                if filename.lower().endswith(".gcode"):
                    with z.open(filename) as f:
                        last_lines = deque(maxlen=1000)
                        count = 0

                        for line in f:
                            if count < 100:
                                line_str = line.decode("utf-8", errors="ignore").strip()
                                if info["time"] == "Unknown" and (
                                    "total estimated time" in line_str.lower()
                                    or "model printing time" in line_str.lower()
                                ):
                                    match = re.search(
                                        r"(?:total estimated time|model printing time)[\s:=]+(.*)",
                                        line_str,
                                        re.IGNORECASE,
                                    )
                                    if match:
                                        info["time"] = format_time_str(match.group(1).strip())
                            if not info["has_supports"] and line.lstrip().startswith(b";"):
                                line_lower = line.lower()
                                if (
                                    b"; type:support" in line_lower
                                    or b"; type:tree support" in line_lower
                                    or b"; feature: support" in line_lower
                                    or b"; feature: tree support" in line_lower
                                    or b"; support_filament" in line_lower
                                ):
                                    info["has_supports"] = True
                            last_lines.append(line)
                            count += 1

                        for line in reversed(last_lines):
                            line_str = line.decode("utf-8", errors="ignore").strip()
                            if "; filament used [g] =" in line_str and info["grams"] == 0.0:
                                match = re.search(r"=\s*([0-9.]+)", line_str)
                                if match:
                                    info["grams"] = float(match.group(1))
                                    break

                    if info["grams"] != 0.0 or info["time"] != "Unknown" or info["has_supports"]:
                        break
    except Exception as e:
        logger.warning(f"Could not read slice info from {three_mf_path}: {e}")
    return info


def get_filament_usage_from_3mf(three_mf_path: str) -> float:
    """Returns estimated filament usage in grams from 3MF."""
    return get_slice_info_from_3mf(three_mf_path)["grams"]


def get_enable_supports_config() -> bool:
    """Loads default enable_supports flag from inputs/configs/agent_config.json."""
    config_path = os.path.join(CONFIGS_DIR, "agent_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("enable_supports", False))
        except Exception:
            pass
    return False


def _can_reuse_existing_3mf(stl_file: str, enable_supports: bool | None) -> str | None:
    """Return existing sibling .3mf path if it is newer than stl_file and matches support settings."""
    if not stl_file:
        return None
    stl_file = os.path.abspath(stl_file)
    candidate_3mf = os.path.splitext(stl_file)[0] + ".3mf"
    if not os.path.exists(candidate_3mf) or not os.path.exists(stl_file):
        return None
    try:
        if os.path.getmtime(candidate_3mf) < os.path.getmtime(stl_file):
            return None
        if not zipfile.is_zipfile(candidate_3mf):
            return None
        want_supports = bool(enable_supports)
        has_supports = False
        with zipfile.ZipFile(candidate_3mf, "r") as zf:
            if "Metadata/project_settings.config" in zf.namelist():
                raw = zf.read("Metadata/project_settings.config").decode("utf-8")
                cfg = json.loads(raw)
                has_supports = str(cfg.get("enable_support", "0")).strip().lower() in (
                    "1",
                    "true",
                )
        if has_supports != want_supports:
            logger.info(
                f"Existing 3MF ({candidate_3mf}) has supports={has_supports}, but supports={want_supports} was requested. Re-slicing required."
            )
            return None
        return candidate_3mf
    except Exception:
        return None


def slice_stl_to_3mf(
    stl_file: str,
    outdir: str | None = None,
    file_basename: str | None = None,
    enable_supports: bool | None = None,
) -> str | None:
    """Slices an STL to a 3MF file using isolated CWD under outputs/tmp/job_<uuid>."""
    if not stl_file:
        return None

    stl_file = os.path.abspath(stl_file)
    if not os.path.exists(stl_file):
        logger.error(f"STL file not found: {stl_file}")
        return None

    if enable_supports is None:
        enable_supports = False

    if outdir and file_basename:
        out_3mf = os.path.abspath(os.path.join(outdir, f"{file_basename}.3mf"))
    elif outdir:
        out_3mf = os.path.abspath(
            os.path.join(outdir, os.path.splitext(os.path.basename(stl_file))[0] + ".3mf")
        )
    else:
        out_3mf = os.path.abspath(os.path.splitext(stl_file)[0] + ".3mf")

    os.makedirs(TMP_DIR, exist_ok=True)
    job_dir = tempfile.mkdtemp(prefix="slice_", dir=TMP_DIR)

    temp_3mf = os.path.join(job_dir, f"temp_geometry_{uuid.uuid4().hex[:8]}.3mf")
    ready_3mf = os.path.join(job_dir, f"ready_to_slice_{uuid.uuid4().hex[:8]}.3mf")

    try:
        # 1. Convert STL to geometry 3MF using PrusaSlicer
        slicer_exe = get_prusa_slicer_path()
        if "orca-slicer" in slicer_exe.lower() or "bambu-studio" in slicer_exe.lower():
            cmd = [slicer_exe, "--export-3mf", temp_3mf, stl_file]
        else:
            cmd = [slicer_exe, "--export-3mf", "--output", temp_3mf, stl_file]

        subprocess.run(cmd, check=True, timeout=600, cwd=job_dir)

        if not os.path.exists(temp_3mf):
            logger.error(f"Geometry export produced no 3MF for {stl_file}; aborting slice.")
            return None

        # 2. Inject geometry into user's default_project.3mf
        project_3mf = os.path.join(TEMPLATES_DIR, "default_project.3mf")
        if not os.path.exists(project_3mf):
            logger.error(
                f"Error: {project_3mf} not found! Please ensure default_project.3mf is in inputs/templates/."
            )
            return None

        try:
            with zipfile.ZipFile(temp_3mf, "r") as p_src:
                with zipfile.ZipFile(project_3mf, "r") as b_src:
                    with zipfile.ZipFile(ready_3mf, "w") as out:
                        for m in p_src.namelist():
                            if m.startswith("3D/"):
                                out.writestr(m, p_src.read(m))
                        for m in b_src.namelist():
                            if m.startswith("Metadata/"):
                                if enable_supports and m == "Metadata/project_settings.config":
                                    try:
                                        settings_data = b_src.read(m).decode("utf-8")
                                        settings_json = json.loads(settings_data)
                                        settings_json["enable_support"] = "1"
                                        out.writestr(
                                            m, json.dumps(settings_json, indent=4).encode("utf-8")
                                        )
                                    except Exception as e:
                                        logger.warning(f"Failed to inject support setting: {e}")
                                        out.writestr(m, b_src.read(m))
                                else:
                                    out.writestr(m, b_src.read(m))
                        if "[Content_Types].xml" in p_src.namelist():
                            out.writestr("[Content_Types].xml", p_src.read("[Content_Types].xml"))
                        if "_rels/.rels" in p_src.namelist():
                            out.writestr("_rels/.rels", p_src.read("_rels/.rels"))
        except zipfile.BadZipFile as e:
            logger.error(
                f"Corrupted 3MF template or geometry archive ({e}). Please verify {project_3mf}."
            )
            return None

        # 3. Slice combined 3MF using OrcaSlicer with cwd=job_dir
        cmd = [
            get_bambu_studio_path(),
            "--slice",
            "0",
            "--orient",
            "1",
            "--arrange",
            "1",
            "--export-3mf",
            out_3mf,
            ready_3mf,
        ]
        try:
            subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=300, cwd=job_dir
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(
                f"OrcaSlicer auto-orient failed ({getattr(e, 'returncode', 'Timeout')}). Retrying without auto-orient..."
            )
            cmd_fallback = [
                get_bambu_studio_path(),
                "--slice",
                "0",
                "--orient",
                "0",
                "--arrange",
                "1",
                "--export-3mf",
                out_3mf,
                ready_3mf,
            ]
            try:
                subprocess.run(
                    cmd_fallback,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=job_dir,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e2:
                logger.error(
                    f"Error slicing 3MF. Exit code: {getattr(e2, 'returncode', 'Timeout')}"
                )
                return None

        if not os.path.exists(out_3mf):
            logger.error(f"Slicer reported success but {out_3mf} was not created.")
            return None
        return out_3mf

    except Exception as e:
        logger.error(f"Geometry export failed for {stl_file}: {e}")
        return None
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def compile_and_slice(
    scad_file: str,
    out_dir: str | None = None,
    enable_supports: bool | None = None,
) -> str | None:
    """Compiles SCAD to STL and slices it to 3MF."""
    if not scad_file:
        return None
    scad_file = os.path.abspath(scad_file)
    if out_dir is None:
        out_dir = os.path.dirname(scad_file)
    else:
        out_dir = os.path.abspath(out_dir)

    stl_file = os.path.join(out_dir, os.path.basename(scad_file).replace(".scad", ".stl"))
    openscad_bin = get_openscad_path()
    try:
        subprocess.run([openscad_bin, "-o", stl_file, scad_file], check=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.error(f"OpenSCAD compilation failed for {scad_file}: {e}")
        return None
    return slice_stl_to_3mf(stl_file, enable_supports=enable_supports)


def main(*args, **kwargs):
    """Backward compatibility alias for manual CLI printing."""
    from src.cli.manual_print import main as _main

    return _main(*args, **kwargs)

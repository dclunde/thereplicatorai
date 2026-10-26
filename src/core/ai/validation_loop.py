import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from google.genai import types

from src.core.ai.json_repair import parse_ai_response
from src.core.ai.prompts import (
    _build_agent_instructions,
    _build_constraint_hint,  # noqa: F401
    _build_initial_prompt,
    _build_user_context,
)
from src.core.ai.providers import BaseAIProvider, get_active_ai_provider
from src.core.config import (
    CONFIGS_DIR,
    PROMPTS_DIR,
    ROOT_DIR,
    TMP_DIR,
    get_openscad_path,
)
from src.core.slicer.mesh_validator import analyze_mesh_geometry
from src.core.slicer.openscad import (
    calculate_stl_dimensions,
    format_dimensions_inches,
    render_scad_preview_png,  # noqa: F401
)
from src.core.slicer.slicer_3mf import get_slice_info_from_3mf, slice_stl_to_3mf

logger = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


def _load_ai_config(service: str | None = None) -> dict:
    """Load active AI provider, model, system instructions, and prompt templates."""
    provider = get_active_ai_provider()
    ai_model = provider.model

    instructions_file = os.path.join(PROMPTS_DIR, "ai_instructions.txt")
    ai_instructions = ""
    if os.path.exists(instructions_file):
        with open(instructions_file, "r", encoding="utf-8") as f:
            ai_instructions = f.read()

    error_template = ""
    verify_template = ""
    error_prompt_file = os.path.join(PROMPTS_DIR, "error_prompt.md")
    verify_prompt_file = os.path.join(PROMPTS_DIR, "verify_prompt.md")
    if os.path.exists(error_prompt_file):
        with open(error_prompt_file, "r", encoding="utf-8") as f:
            error_template = f.read()
    if os.path.exists(verify_prompt_file):
        with open(verify_prompt_file, "r", encoding="utf-8") as f:
            verify_template = f.read()

    max_ai_calls = 3
    agent_level = 3
    max_print_time_minutes = 0
    max_size_inches = 0
    check_geometry_health = True
    min_connection_thickness_mm = 1.2
    bed_contact_threshold_mm = 0.4
    config_json_file = os.path.join(CONFIGS_DIR, "agent_config.json")
    if os.path.exists(config_json_file):
        try:
            with open(config_json_file, "r", encoding="utf-8") as f:
                cfg_data = json.load(f)
                max_ai_calls = cfg_data.get("max_ai_calls", 3)
                agent_level = cfg_data.get("agent_level", 3)
                check_geometry_health = cfg_data.get("check_geometry_health", True)
                min_connection_thickness_mm = cfg_data.get("min_connection_thickness_mm", 1.2)
                bed_contact_threshold_mm = cfg_data.get("bed_contact_threshold_mm", 0.4)

                service_constraints = cfg_data.get("service_constraints", {})
                if service and service in service_constraints:
                    svc_conf = service_constraints[service]
                    max_print_time_minutes = svc_conf.get("max_print_time_minutes", 0)
                    max_size_inches = svc_conf.get("max_size_inches", 0)
                else:
                    max_print_time_minutes = cfg_data.get("max_print_time_minutes", 0)
                    max_size_inches = cfg_data.get("max_size_inches", 0)
        except Exception as e:
            logger.warning(f"Failed to parse agent_config.json ({e}), using defaults.")

    return {
        "client": provider,
        "provider": provider,
        "model": ai_model,
        "instructions": ai_instructions,
        "error_template": error_template,
        "verify_template": verify_template,
        "max_ai_calls": max_ai_calls,
        "agent_level": agent_level,
        "max_print_time_minutes": max_print_time_minutes,
        "max_size_inches": max_size_inches,
        "check_geometry_health": check_geometry_health,
        "min_connection_thickness_mm": min_connection_thickness_mm,
        "bed_contact_threshold_mm": bed_contact_threshold_mm,
        "base_dir": ROOT_DIR,
    }


_load_gemini_config = _load_ai_config


def get_max_ai_calls(service: str | None = None) -> int:
    """Helper to load max AI calls limit from inputs/configs/agent_config.json."""
    config_json_file = os.path.join(CONFIGS_DIR, "agent_config.json")
    if os.path.exists(config_json_file):
        try:
            with open(config_json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if service and "service_limits" in data and service in data["service_limits"]:
                    return int(data["service_limits"][service])
                return int(data.get("max_ai_calls", 3))
        except Exception as e:
            logger.warning(f"Failed to load max_ai_calls from config ({e})")
    return 3


def _call_ai(client, model, system_instructions, content_prompt):
    """Make a single AI API call via the active provider and return raw response text."""
    if isinstance(client, BaseAIProvider) or hasattr(client, "generate_content"):
        return client.generate_content(
            prompt=content_prompt,
            system_instructions=system_instructions,
        )
    response = client.models.generate_content(
        model=model,
        contents=content_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instructions, response_mime_type="application/json"
        ),
    )
    return (response.text or "").strip()


_call_gemini = _call_ai


def _validate_code(code: str) -> tuple[bool, str, str | None]:
    """Compile OpenSCAD code to a temporary STL in outputs/tmp/ to check for errors."""
    try:
        openscad_path = get_openscad_path()
    except (ImportError, FileNotFoundError):
        return True, "", None

    os.makedirs(TMP_DIR, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="ttp_validate_", dir=TMP_DIR)
    scad_file = os.path.join(temp_dir, "validate.scad")
    stl_file = os.path.join(temp_dir, "validate.stl")

    try:
        with open(scad_file, "w", encoding="utf-8") as f:
            f.write(code)

        result = subprocess.run(
            [openscad_path, "-o", stl_file, scad_file], capture_output=True, text=True, timeout=60
        )

        if result.returncode != 0:
            error_msg = (
                result.stderr.strip()
                if result.stderr
                else f"OpenSCAD exited with code {result.returncode}"
            )
            shutil.rmtree(temp_dir, ignore_errors=True)
            return False, error_msg, None

        if not os.path.exists(stl_file) or os.path.getsize(stl_file) == 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return (
                False,
                "OpenSCAD produced an empty or missing STL file. The code may not generate any geometry.",
                None,
            )

        return True, "", stl_file
    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False, "OpenSCAD compilation timed out (>60s). The code may be too complex.", None
    except FileNotFoundError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return True, "", None


def _cleanup_temp_stl(stl_path: str | None) -> None:
    """Safely remove the temp directory created by _validate_code for stl_path."""
    if not stl_path:
        return
    temp_dir = os.path.dirname(stl_path)
    if os.path.basename(temp_dir).startswith("ttp_validate_") and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def _extract_physical_metrics(
    stl_path,
    expected_parts_count=1,
    check_geometry_health=False,
    min_connection_thickness_mm=1.2,
    bed_contact_threshold_mm=0.4,
    enable_supports=False,
    keep_artifacts=False,
):
    """Extract physical metrics (size, time, grams) and geometry health from a compiled STL."""
    size = "Unknown"
    print_time = "Unknown"
    filament_grams = 0.0
    geometry_errors = []

    if not stl_path:
        return size, print_time, filament_grams, geometry_errors

    temp_dir = os.path.dirname(stl_path)

    try:
        try:
            size = format_dimensions_inches(stl_path)
        except Exception:
            pass

        try:
            temp_3mf = slice_stl_to_3mf(
                stl_path, temp_dir, "validate", enable_supports=enable_supports
            )
            if temp_3mf:
                info = get_slice_info_from_3mf(temp_3mf)
                print_time = info.get("time", "Unknown")
                filament_grams = info.get("grams", 0.0)
                logger.info(
                    f"[AGENT] 📐 Size: {size}, ⏱️ Time: {print_time}, ⚖️ Grams: {filament_grams}"
                )
            else:
                logger.warning("[AGENT] ⚠️ Slicing failed, only size available")
        except Exception as e:
            logger.warning(f"[AGENT] ⚠️ Slicing for metrics failed ({e}), only size available")

        if check_geometry_health:
            try:
                geometry_errors = analyze_mesh_geometry(
                    stl_path,
                    expected_parts_count=expected_parts_count,
                    min_thickness_mm=min_connection_thickness_mm,
                    bed_threshold_mm=bed_contact_threshold_mm,
                )
                if geometry_errors:
                    logger.warning("[AGENT] 🔍 Geometry health issues found:")
                    for err in geometry_errors:
                        logger.warning(f"[AGENT]   {err}")
                else:
                    logger.info("[AGENT] 🔍 Geometry health check passed")
            except Exception as e:
                logger.warning(f"[AGENT] ⚠️ Geometry health check failed ({e}), skipping")
    finally:
        if not keep_artifacts:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return size, print_time, filament_grams, geometry_errors


def _parse_time_to_minutes(time_str: str) -> int:
    """Parse time string like '1d 2h', '1h 15m', '32m', '45s' to total minutes."""
    if not time_str or time_str == "Unknown":
        return 0
    total_minutes = 0
    days = re.search(r"(\d+)d", time_str)
    if days:
        total_minutes += int(days.group(1)) * 24 * 60
    hours = re.search(r"(\d+)h", time_str)
    if hours:
        total_minutes += int(hours.group(1)) * 60
    minutes = re.search(r"(\d+)m", time_str)
    if minutes:
        total_minutes += int(minutes.group(1))
    seconds = re.search(r"(\d+)s", time_str)
    if seconds:
        sec_val = int(seconds.group(1))
        if sec_val >= 30:
            total_minutes += 1
        elif total_minutes == 0 and sec_val > 0:
            total_minutes = 1
    return total_minutes


def _parse_size_to_max_inch(size_str: str) -> float:
    """Parse size string like '1.50 × 2.00 × 0.80 in' and return the max dimension."""
    if not size_str or "Unknown" in size_str or "Unavailable" in size_str:
        return 0.0
    dims = re.findall(r"[\d.]+", size_str)
    if not dims:
        return 0.0
    return max(float(d) for d in dims)


def _check_constraints(size: str, print_time: str, config: dict) -> list[str]:
    """Return a list of error strings if any physical constraint is violated."""
    errors = []

    if print_time == "Unknown" or not print_time:
        errors.append(
            "Constraint Error: Slicing failed. The geometry may be non-manifold, too thin, or outside the build volume."
        )

    max_time = config.get("max_print_time_minutes", 0)
    if max_time > 0 and print_time and print_time != "Unknown":
        actual_time = _parse_time_to_minutes(print_time)
        if actual_time > max_time:
            errors.append(
                f"Constraint Error: Estimated print time is {print_time} ({actual_time}m), which exceeds the maximum allowed time of {max_time}m."
            )

    max_size = config.get("max_size_inches", 0)
    if max_size > 0 and size and "Unknown" not in size and "Unavailable" not in size:
        actual_max_dim = _parse_size_to_max_inch(size)
        if actual_max_dim > max_size:
            errors.append(
                f"Constraint Error: The model's maximum dimension is {actual_max_dim:.2f} inches (Bounding Size: {size}), which exceeds the maximum allowed size of {max_size} inches."
            )

    return errors


class EvaluationResult(dict):
    """Result dict that also exposes fields as attributes."""

    def __getattr__(self, name):
        if name in self:
            return self[name]
        raise AttributeError(f"'EvaluationResult' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        self[name] = value


def _evaluate_candidate_code(
    code: str,
    expected_parts_count: int = 1,
    config: dict | None = None,
    enable_supports: bool = False,
) -> EvaluationResult:
    """Compiles OpenSCAD code, extracts physical metrics and geometry health,
    and checks constraints. Returns an EvaluationResult.
    """
    if config is None:
        config = {}

    success, error_msg, stl_path = _validate_code(code)
    if not success:
        return EvaluationResult(
            {
                "success": False,
                "error_msg": error_msg,
                "stl_path": None,
                "size": "Unavailable (Compilation Error)",
                "print_time": "Unknown",
                "filament_grams": 0.0,
                "geo_errors": [],
                "constraint_errors": [],
                "all_issues": [error_msg],
                "geometry_health": "Not checked (Compilation Error)",
                "next_state": "error_fix",
                "dimensions": None,
            }
        )

    size, print_time, filament_grams, geo_errors = _extract_physical_metrics(
        stl_path,
        expected_parts_count=expected_parts_count,
        check_geometry_health=config.get("check_geometry_health", False),
        min_connection_thickness_mm=config.get("min_connection_thickness_mm", 1.2),
        bed_contact_threshold_mm=config.get("bed_contact_threshold_mm", 0.4),
        enable_supports=enable_supports,
        keep_artifacts=True,
    )
    constraint_errors = _check_constraints(size, print_time, config)
    all_issues = list(constraint_errors) + list(geo_errors)

    if geo_errors:
        geometry_health = "Warning — " + "; ".join(geo_errors)
    else:
        geometry_health = (
            f"Passed ({expected_parts_count} part(s) detected, "
            f"all grounded at Z=0, minimum joint thickness >= "
            f"{config.get('min_connection_thickness_mm', 1.2)}mm)"
        )

    next_state = "error_fix" if all_issues else "verify"

    return EvaluationResult(
        {
            "success": True,
            "error_msg": "",
            "stl_path": stl_path,
            "size": size,
            "print_time": print_time,
            "filament_grams": filament_grams,
            "geo_errors": geo_errors,
            "constraint_errors": constraint_errors,
            "all_issues": all_issues,
            "geometry_health": geometry_health,
            "next_state": next_state,
            "dimensions": calculate_stl_dimensions(stl_path),
        }
    )


def generate_with_validation(
    prompt,
    conversation_history=None,
    max_calls=None,
    service=None,
    enable_supports=False,
    output_dir=None,
    file_basename=None,
    keep_temp_artifacts=False,
):
    """Generator that runs the multi-agent generation loop."""
    try:
        config = _load_ai_config(service=service)
    except TypeError:
        config = _load_ai_config()
    client = config["client"]
    model = config["model"]
    base_instructions = config["instructions"]
    error_template = config["error_template"]
    verify_template = config["verify_template"]

    if max_calls is None:
        max_calls = config.get("max_ai_calls", 3)

    gen_instructions = _build_agent_instructions(base_instructions, "generate")
    err_instructions = _build_agent_instructions(base_instructions, "error_fix")
    ver_instructions = _build_agent_instructions(base_instructions, "verify")

    user_context = _build_user_context(prompt, conversation_history)

    GENERATE = "generate"
    ERROR_FIX = "error_fix"
    VERIFY = "verify"
    DONE = "done"

    state = GENERATE
    calls_made = 0
    code = None
    filename = "gemini_part"
    colors = []
    expected_parts_count = 1

    last_working_code = None
    last_working_stl = None
    last_working_size = "Unknown"
    last_working_time = "Unknown"
    last_working_grams = 0.0

    last_compiled_code = None
    last_compiled_stl = None
    last_compiled_size = "Unknown"
    last_compiled_time = "Unknown"
    last_compiled_grams = 0.0
    last_compiled_warnings = []
    fallback_warning = None

    error_msg = ""
    geometry_errors_str = ""

    size = "Unavailable (Compilation Error)"
    print_time = "Unknown"
    filament_grams = 0.0
    geometry_health = "Not checked"

    def _record_compiled(c_code, c_stl, c_size, c_time, c_grams, c_warnings, is_clean):
        nonlocal \
            last_working_code, \
            last_working_stl, \
            last_working_size, \
            last_working_time, \
            last_working_grams
        nonlocal \
            last_compiled_code, \
            last_compiled_stl, \
            last_compiled_size, \
            last_compiled_time, \
            last_compiled_grams, \
            last_compiled_warnings

        if is_clean:
            if (
                last_working_stl
                and last_working_stl != c_stl
                and last_working_stl != last_compiled_stl
            ):
                _cleanup_temp_stl(last_working_stl)
            last_working_code = c_code
            last_working_stl = c_stl
            last_working_size = c_size
            last_working_time = c_time
            last_working_grams = c_grams

        if (
            last_compiled_stl
            and last_compiled_stl != c_stl
            and last_compiled_stl != last_working_stl
        ):
            _cleanup_temp_stl(last_compiled_stl)
        last_compiled_code = c_code
        last_compiled_stl = c_stl
        last_compiled_size = c_size
        last_compiled_time = c_time
        last_compiled_grams = c_grams
        last_compiled_warnings = list(c_warnings)

    while calls_made < max_calls and state != DONE:
        calls_made += 1

        if state == GENERATE:
            yield {
                "type": "progress",
                "attempt": calls_made,
                "max_calls": max_calls,
                "message": "Generating design...",
            }

            content_prompt = _build_initial_prompt(prompt, conversation_history, config)
            response_text = _call_ai(client, model, gen_instructions, content_prompt)
            code, filename, colors, expected_parts_count = parse_ai_response(response_text)

            eval_res = _evaluate_candidate_code(code, expected_parts_count, config, enable_supports)
            size = eval_res["size"]
            print_time = eval_res["print_time"]
            filament_grams = eval_res["filament_grams"]
            geometry_health = eval_res["geometry_health"]

            if not eval_res["success"]:
                error_msg = eval_res["error_msg"]
                state = ERROR_FIX
            elif eval_res["all_issues"]:
                _record_compiled(
                    code,
                    eval_res["stl_path"],
                    size,
                    print_time,
                    filament_grams,
                    eval_res["all_issues"],
                    is_clean=False,
                )
                geometry_errors_str = "\n".join(eval_res["geo_errors"])
                error_msg = (
                    "\n".join(eval_res["constraint_errors"])
                    if eval_res["constraint_errors"]
                    else "No compiler or slicing errors."
                )
                state = ERROR_FIX
            else:
                _record_compiled(
                    code,
                    eval_res["stl_path"],
                    size,
                    print_time,
                    filament_grams,
                    [],
                    is_clean=True,
                )
                if config.get("agent_level", 3) >= 2:
                    state = VERIFY
                else:
                    state = DONE

        elif state == ERROR_FIX:
            yield {
                "type": "progress",
                "attempt": calls_made,
                "max_calls": max_calls,
                "message": "Fixing compilation / geometry errors...",
            }

            content_prompt = error_template.format(
                user_request=user_context,
                user_context=user_context,
                error_log=error_msg,
                error_message=error_msg,
                broken_code=code,
                code=code,
                size=size,
                geometry_errors=geometry_errors_str,
            )
            try:
                response_text = _call_ai(client, model, err_instructions, content_prompt)
            except Exception as e:
                logger.warning(f"AI API error during error correction pass: {e}")
                if last_working_code or last_compiled_code:
                    logger.info(
                        "Falling back to previous working candidate due to AI service disruption."
                    )
                    fallback_warning = (
                        f"AI refinement interrupted by API service issue ({type(e).__name__}); "
                        "recovered previous working design."
                    )
                    state = DONE
                    break
                raise
            new_code, _, _, _ = parse_ai_response(response_text)
            code = new_code

            eval_res = _evaluate_candidate_code(code, expected_parts_count, config, enable_supports)
            size = eval_res["size"]
            print_time = eval_res["print_time"]
            filament_grams = eval_res["filament_grams"]
            geometry_health = eval_res["geometry_health"]

            if not eval_res["success"]:
                error_msg = eval_res["error_msg"]
                state = ERROR_FIX
            elif eval_res["all_issues"]:
                _record_compiled(
                    code,
                    eval_res["stl_path"],
                    size,
                    print_time,
                    filament_grams,
                    eval_res["all_issues"],
                    is_clean=False,
                )
                geometry_errors_str = "\n".join(eval_res["geo_errors"])
                error_msg = (
                    "\n".join(eval_res["constraint_errors"])
                    if eval_res["constraint_errors"]
                    else "No compiler or slicing errors."
                )
                state = ERROR_FIX
            else:
                _record_compiled(
                    code,
                    eval_res["stl_path"],
                    size,
                    print_time,
                    filament_grams,
                    [],
                    is_clean=True,
                )
                if config.get("agent_level", 3) >= 3:
                    state = VERIFY
                else:
                    state = DONE

        elif state == VERIFY:
            yield {
                "type": "progress",
                "attempt": calls_made,
                "max_calls": max_calls,
                "message": "Verifying design against requirements...",
            }

            content_prompt = verify_template.format(
                user_request=user_context,
                user_context=user_context,
                generated_code=code,
                code=code,
                size=size,
                print_time=print_time,
                filament_grams=f"{filament_grams:.1f}",
                geometry_health=geometry_health,
            )
            try:
                response_text = _call_ai(client, model, ver_instructions, content_prompt)
            except Exception as e:
                logger.warning(f"AI API error during verification pass: {e}")
                if last_working_code or last_compiled_code:
                    logger.info(
                        "Falling back to previous working candidate due to AI service disruption."
                    )
                    fallback_warning = (
                        f"AI verification interrupted by API service issue ({type(e).__name__}); "
                        "recovered previous working design."
                    )
                    state = DONE
                    break
                raise
            verified_code, _, _, _ = parse_ai_response(response_text)

            if verified_code.strip() == code.strip():
                state = DONE
            else:
                eval_res = _evaluate_candidate_code(
                    verified_code, expected_parts_count, config, enable_supports
                )
                if not eval_res["success"]:
                    code = verified_code
                    error_msg = eval_res["error_msg"]
                    size = eval_res["size"]
                    geometry_errors_str = ""
                    state = DONE if config.get("agent_level", 3) == 1 else ERROR_FIX
                elif eval_res["all_issues"]:
                    code = verified_code
                    _record_compiled(
                        verified_code,
                        eval_res["stl_path"],
                        eval_res["size"],
                        eval_res["print_time"],
                        eval_res["filament_grams"],
                        eval_res["all_issues"],
                        is_clean=False,
                    )
                    geometry_errors_str = "\n".join(eval_res["geo_errors"])
                    error_msg = (
                        "\n".join(eval_res["constraint_errors"])
                        if eval_res["constraint_errors"]
                        else "No compiler or slicing errors."
                    )
                    state = DONE if config.get("agent_level", 3) == 1 else ERROR_FIX
                else:
                    code = verified_code
                    _record_compiled(
                        verified_code,
                        eval_res["stl_path"],
                        eval_res["size"],
                        eval_res["print_time"],
                        eval_res["filament_grams"],
                        [],
                        is_clean=True,
                    )
                    state = VERIFY if config.get("agent_level", 3) == 4 else DONE

    if last_working_code:
        final_code = last_working_code
        final_stl = last_working_stl
        final_size = last_working_size
        final_time = last_working_time
        final_grams = last_working_grams
        warnings = [fallback_warning] if fallback_warning else []
        if last_compiled_stl and last_compiled_stl != final_stl:
            _cleanup_temp_stl(last_compiled_stl)
    elif last_compiled_code:
        final_code = last_compiled_code
        final_stl = last_compiled_stl
        final_size = last_compiled_size
        final_time = last_compiled_time
        final_grams = last_compiled_grams
        warnings = list(last_compiled_warnings)
        if fallback_warning:
            warnings.append(fallback_warning)
    else:
        if not code:
            final_code = "// Error: No code was generated."
        else:
            final_code = code
        final_stl = None
        final_size = "Unavailable (Compilation Error)"
        final_time = "Unknown"
        final_grams = 0.0
        warnings = ["Model failed to compile."]

    out_stl_path = None
    out_3mf_path = None
    temp_3mf = None
    temp_3mf_suffix = ".3mf"
    if final_stl:
        cand_3mf = os.path.join(os.path.dirname(final_stl), "validate.3mf")
        cand_sliced_3mf = os.path.splitext(final_stl)[0] + "_sliced.3mf"
        if os.path.exists(cand_3mf):
            temp_3mf = cand_3mf
            temp_3mf_suffix = ".3mf"
        elif os.path.exists(cand_sliced_3mf):
            temp_3mf = cand_sliced_3mf
            temp_3mf_suffix = "_sliced.3mf"
    has_temp_3mf = bool(temp_3mf and os.path.exists(temp_3mf))

    if output_dir and file_basename and final_stl and os.path.exists(final_stl):
        os.makedirs(output_dir, exist_ok=True)
        dest_stl = os.path.join(output_dir, f"{file_basename}.stl")
        shutil.move(final_stl, dest_stl)
        out_stl_path = dest_stl
        if has_temp_3mf:
            dest_3mf = os.path.join(output_dir, f"{file_basename}{temp_3mf_suffix}")
            shutil.move(temp_3mf, dest_3mf)
            out_3mf_path = dest_3mf
        _cleanup_temp_stl(final_stl)
    elif keep_temp_artifacts and final_stl and os.path.exists(final_stl):
        out_stl_path = final_stl
        out_3mf_path = temp_3mf if has_temp_3mf else None
    else:
        _cleanup_temp_stl(final_stl)

    yield {
        "type": "result",
        "code": final_code,
        "filename": filename,
        "colors": colors,
        "warnings": warnings,
        "stl_path": out_stl_path,
        "3mf_path": out_3mf_path,
        "size": final_size,
        "time": final_time,
        "grams": final_grams,
    }


def generate_openscad_validated(
    prompt,
    conversation_history=None,
    max_calls=3,
    service=None,
    enable_supports: bool = False,
    output_dir: str | None = None,
    file_basename: str | None = None,
    keep_temp_artifacts: bool = False,
):
    """Convenience wrapper: runs the full validation loop and returns the result."""
    result = None
    for step in generate_with_validation(
        prompt,
        conversation_history,
        max_calls,
        service,
        enable_supports=enable_supports,
        output_dir=output_dir,
        file_basename=file_basename,
        keep_temp_artifacts=keep_temp_artifacts,
    ):
        if step["type"] == "result":
            result = step
    if result:
        if output_dir is not None or keep_temp_artifacts:
            return (
                result["code"],
                result["filename"],
                result["colors"],
                result.get("warnings", []),
                result.get("stl_path"),
                result.get("3mf_path"),
            )
        return result["code"], result["filename"], result["colors"], result.get("warnings", [])
    raise RuntimeError("generate_with_validation did not produce a result")


def generate_openscad_code(prompt, conversation_history=None, max_calls=None, service=None):
    """Backward-compatible single-call entry point."""
    code, filename, colors, warnings = generate_openscad_validated(
        prompt, conversation_history, max_calls, service
    )[:4]
    return code, filename, colors


# Backward-compatible aliases
generate_openscad = generate_openscad_code
from src.core.ai.json_repair import _parse_response, _try_repair_json, clean_scad_code

__all__ = [
    "generate_openscad",
    "generate_openscad_code",
    "generate_openscad_validated",
    "generate_with_validation",
    "get_max_ai_calls",
    "_try_repair_json",
    "clean_scad_code",
    "_parse_response",
]

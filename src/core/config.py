import json
import logging
import os
import re
import shutil
import sys

logger = logging.getLogger(__name__)

# Repository Root & Standard Directories
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE_DIR = ROOT_DIR

INPUTS_DIR = os.path.join(ROOT_DIR, "inputs")
ASSETS_DIR = os.path.join(INPUTS_DIR, "assets")
PROMPTS_DIR = os.path.join(INPUTS_DIR, "ai_prompts")
CONFIGS_DIR = os.path.join(INPUTS_DIR, "configs")
TEMPLATES_DIR = os.path.join(INPUTS_DIR, "templates")

OUTPUTS_DIR = os.path.join(ROOT_DIR, "outputs")
SESSIONS_DIR = os.path.join(OUTPUTS_DIR, "sessions")
SNAPSHOTS_DIR = os.path.join(OUTPUTS_DIR, "snapshots")
TMP_DIR = os.path.join(OUTPUTS_DIR, "tmp")
LOGS_DIR = os.path.join(OUTPUTS_DIR, "logs")
CACHE_DIR = os.path.join(OUTPUTS_DIR, ".cache")


def disable_quickedit() -> None:
    """Disables QuickEdit mode on Windows consoles to prevent the process from
    suspending execution if a user clicks or selects text in the console window.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
            if handle != -1 and handle != 0:
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    new_mode = (mode.value & ~0x0040) | 0x0080
                    kernel32.SetConsoleMode(handle, new_mode)
                kernel32.CloseHandle(handle)
        except Exception:
            pass


def ensure_output_dirs() -> None:
    """Ensures all standard runtime directories under outputs/ exist."""
    for d in (OUTPUTS_DIR, SESSIONS_DIR, SNAPSHOTS_DIR, TMP_DIR, LOGS_DIR, CACHE_DIR):
        os.makedirs(d, exist_ok=True)


def ensure_system_config(configs_dir: str | None = None) -> str:
    """Copies system_config.default.json -> system_config.json ONLY if missing.
    Never overwrites an existing system_config.json so local user path edits
    are preserved and never pushed to Git.
    """
    cfg_dir = configs_dir or CONFIGS_DIR
    target = os.path.join(cfg_dir, "system_config.json")
    default_src = os.path.join(cfg_dir, "system_config.default.json")
    if not os.path.exists(target) and os.path.exists(default_src):
        os.makedirs(cfg_dir, exist_ok=True)
        shutil.copyfile(default_src, target)
    return target


def load_system_config(configs_dir: str | None = None) -> dict:
    """Loads system_config.json (auto-creating it from default if missing)."""
    cfg_dir = configs_dir or CONFIGS_DIR
    target = ensure_system_config(cfg_dir)
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse {target}: {e}")
    default_src = os.path.join(cfg_dir, "system_config.default.json")
    if os.path.exists(default_src):
        try:
            with open(default_src, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_env_keys(env_path: str | None = None) -> dict:
    """Loads API keys and credentials from .env (strictly read-only)."""
    env_file = env_path or os.path.join(ROOT_DIR, ".env")

    keys = {
        "GEMINI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "TELEGRAM_BOT_TOKEN": "",
        "GOOGLE_TASKS_CLIENT_ID": "",
        "GOOGLE_TASKS_CLIENT_SECRET": "",
        "BAMBU_USERNAME": "",
        "BAMBU_PASSWORD": "",
        "BAMBU_DEVICE_SERIAL": "",
        "BAMBU_PRINTER_IP": "",
        "BAMBU_ACCESS_CODE": "",
    }

    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    if " #" in val:
                        val = val.split(" #")[0].strip()
                    else:
                        val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]
                    if key in keys:
                        keys[key] = val

    for k in keys:
        if not keys[k]:
            env_val = os.environ.get(k, "").strip()
            if env_val:
                keys[k] = env_val

    return keys


def is_ci_environment() -> bool:
    return bool(
        os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS") or os.environ.get("GIT_TESTING")
    )


def _expand_path(p: str) -> str:
    expanded = os.path.expanduser(p)
    expanded = re.sub(r"%([^%]+)%", r"$\1", expanded)
    expanded = os.path.expandvars(expanded)
    if os.name != "nt":
        expanded = expanded.replace("\\", "/")
    return expanded


def get_openscad_path() -> str:
    env_override = os.environ.get("OPENSCAD_PATH", "").strip()
    if env_override and os.path.exists(env_override):
        return env_override

    sys_cfg = load_system_config()
    cfg_paths = sys_cfg.get("openscad_paths", [])
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    fallback_paths = [
        os.path.join(local_app_data, r"Programs\OpenSCAD-Nightly\openscad.exe")
        if local_app_data
        else "",
        r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
        r"C:\Program Files\OpenSCAD\openscad.exe",
    ]
    for raw_path in list(cfg_paths) + fallback_paths:
        if not raw_path:
            continue
        expanded = _expand_path(raw_path)
        if expanded and os.path.exists(expanded):
            return expanded

    which_path = shutil.which("openscad") or shutil.which("OpenSCAD")
    if which_path:
        return which_path

    if is_ci_environment():
        return "mock_openscad"

    raise FileNotFoundError("Could not find OpenSCAD. Please ensure it is installed.")


def get_bambu_studio_path() -> str:
    env_override = os.environ.get("SLICER_PATH", "").strip()
    if env_override and os.path.exists(env_override):
        return env_override

    sys_cfg = load_system_config()
    cfg_paths = sys_cfg.get("slicer_paths", []) + sys_cfg.get("bambu_studio_paths", [])
    fallback_paths = [
        r"C:\Program Files (x86)\OrcaSlicer\orca-slicer.exe",
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
        r"C:\Program Files (x86)\Bambu Studio\bambu-studio.exe",
        r"C:\Program Files\Bambu Studio\bambu-studio.exe",
    ]
    for raw_path in cfg_paths + fallback_paths:
        if not raw_path:
            continue
        expanded = _expand_path(raw_path)
        if expanded and os.path.exists(expanded):
            return expanded

    which_path = (
        shutil.which("orca-slicer")
        or shutil.which("bambu-studio")
        or shutil.which("OrcaSlicer")
        or shutil.which("BambuStudio")
    )
    if which_path:
        return which_path

    if is_ci_environment():
        return "mock_bambu"

    raise FileNotFoundError(
        "Could not find OrcaSlicer or Bambu Studio. Please ensure it is installed."
    )


def get_slicer_path() -> str:
    return get_bambu_studio_path()


def get_prusa_slicer_path() -> str:
    sys_cfg = load_system_config()
    cfg_paths = sys_cfg.get("prusa_slicer_paths", [])
    fallback_paths = [
        r"C:\Program Files (x86)\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
        r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
    ]
    for raw_path in cfg_paths + fallback_paths:
        if not raw_path:
            continue
        expanded = _expand_path(raw_path)
        if expanded and os.path.exists(expanded):
            return expanded

    which_path = shutil.which("prusa-slicer-console") or shutil.which("prusa-slicer")
    if which_path:
        return which_path

    try:
        return get_bambu_studio_path()
    except FileNotFoundError:
        if is_ci_environment():
            return "mock_prusa"
        raise


def has_openscad() -> bool:
    try:
        path = get_openscad_path()
        return path != "mock_openscad" and os.path.exists(path)
    except FileNotFoundError:
        return False


def has_slicer() -> bool:
    try:
        path = get_bambu_studio_path()
        return path not in ("mock_bambu", "mock_prusa") and os.path.exists(path)
    except FileNotFoundError:
        return False


load_api_keys = load_env_keys

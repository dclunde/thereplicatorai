import datetime
import inspect
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from src.core.activity_tracker import busy_activity
from src.core.ai import clean_scad_code, generate_with_validation, get_max_ai_calls
from src.core.config import (
    SESSIONS_DIR,
    TEMPLATES_DIR,
    get_bambu_studio_path,
    get_openscad_path,
)
from src.core.sessions import (
    SessionManager,
    enforce_storage_retention_policy,
    rename_session_folder,
    resolve_session_dir,
    save_session_atomic,
)
from src.core.slicer import (
    format_dimensions_inches,
    get_slice_info_from_3mf,
    slice_stl_to_3mf as shared_slicer,
)
from src.web.backend.jobs import (
    clear_active_job,
    get_active_job,
    set_active_job,
)
from src.web.backend.schemas import (
    EditSessionRequest,
    NewSessionRequest,
    OpenFolderRequest,
)

router = APIRouter(tags=["sessions"])

WORKSPACE_DIR = SESSIONS_DIR
MAX_AI_CALLS = get_max_ai_calls("online_backend")


def get_workspace_dir() -> str:
    for mod_name in (
        "src.web.backend.routes.sessions",
        "src.web.backend.app",
    ):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "WORKSPACE_DIR"):
            return getattr(mod, "WORKSPACE_DIR")
    return SESSIONS_DIR


def _get_generate_with_validation():
    mod = sys.modules.get("src.web.backend.routes.sessions")
    if mod and hasattr(mod, "generate_with_validation"):
        return getattr(mod, "generate_with_validation")
    return generate_with_validation


def _get_subprocess():
    mod = sys.modules.get("src.web.backend.routes.sessions")
    if mod and hasattr(mod, "subprocess"):
        return getattr(mod, "subprocess")
    return subprocess


def _get_compile_openscad_generator():
    mod = sys.modules.get("src.web.backend.routes.sessions")
    if mod and hasattr(mod, "compile_openscad_generator"):
        return getattr(mod, "compile_openscad_generator")
    return compile_openscad_generator


def _get_shared_slicer():
    mod = sys.modules.get("src.web.backend.routes.sessions")
    if mod and hasattr(mod, "shared_slicer"):
        return getattr(mod, "shared_slicer")
    return shared_slicer


def _get_openscad_path() -> str:
    mod = sys.modules.get("src.web.backend.routes.sessions")
    if mod and hasattr(mod, "get_openscad_path"):
        return getattr(mod, "get_openscad_path")()
    return get_openscad_path()


def parse_and_strip_support_flags(text: str) -> tuple[str, Optional[bool]]:
    """Extracts --support or --no-support from the input text, returning (cleaned_text, enable_supports)."""
    if re.search(r"(?i)(?<!\S)--no-support(?!\S)", text):
        cleaned = re.sub(r"(?i)(?<!\S)--no-support(?!\S)", "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, False
    if re.search(r"(?i)(?<!\S)--support(?!\S)", text):
        cleaned = re.sub(r"(?i)(?<!\S)--support(?!\S)", "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, True
    return text.strip(), None


def sanitize_id(s: str) -> str:
    # Allow most valid filename characters, but prevent directory traversal
    return re.sub(r"[^a-zA-Z0-9_\-. ()\[\]{},!@#$&+=~^]", "", s)


def get_session_file(session_id: str) -> str:
    session_id = sanitize_id(session_id)
    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    target_dir = resolved or os.path.join(ws_dir, session_id)
    return os.path.join(target_dir, "session.json")


def load_session(session_id: str) -> dict:
    session_id = sanitize_id(session_id)
    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    session_dir = resolved or os.path.join(ws_dir, session_id)
    filepath = os.path.join(session_dir, "session.json")

    if not os.path.exists(filepath):
        if not os.path.exists(session_dir):
            raise HTTPException(status_code=404, detail="Session not found")
        data = {
            "id": session_id,
            "title": session_id,
            "history": [
                {
                    "role": "assistant",
                    "text": "This item was created outside of the online session.",
                }
            ],
            "versions": 1,
            "latest_stl": "",
            "last_updated": None,
        }
    else:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

    folder_name = os.path.basename(session_dir)
    data.setdefault("id", session_id)
    data["folder_name"] = folder_name

    version_dict = {}
    latest_stl_url = ""
    code_msgs = [m for m in data.get("history", []) if m.get("role") == "assistant" and "code" in m]

    if os.path.exists(session_dir):
        for item in os.listdir(session_dir):
            if os.path.isdir(os.path.join(session_dir, item)) and item.startswith("v"):
                try:
                    version_num = int(item[1:])
                    v_dir = os.path.join(session_dir, item)
                    stl_files = [f for f in os.listdir(v_dir) if f.endswith(".stl")]
                    scad_files = [f for f in os.listdir(v_dir) if f.endswith(".scad")]
                    has_code = bool(scad_files) or (0 < version_num <= len(code_msgs))
                    if stl_files:
                        url = f"/created_stls/{urllib.parse.quote(folder_name)}/{urllib.parse.quote(item)}/{urllib.parse.quote(stl_files[0])}"
                        version_dict[version_num] = {
                            "version": version_num,
                            "stl_url": url,
                            "has_stl": True,
                            "has_code": has_code,
                        }
                    else:
                        version_dict[version_num] = {
                            "version": version_num,
                            "stl_url": "",
                            "has_stl": False,
                            "has_code": has_code,
                        }
                except ValueError:
                    pass
            elif item.endswith(".stl") and not os.path.exists(filepath):
                # Handle root folder STLs (e.g. from telegram bot uploads)
                url = f"/created_stls/{urllib.parse.quote(folder_name)}/{urllib.parse.quote(item)}"
                version_dict[1] = {
                    "version": 1,
                    "stl_url": url,
                    "has_stl": True,
                    "has_code": False,
                }

    total_versions = data.get("versions", 0)
    for v in range(1, total_versions + 1):
        if v not in version_dict:
            has_code = 0 < v <= len(code_msgs)
            version_dict[v] = {
                "version": v,
                "stl_url": "",
                "has_stl": False,
                "has_code": has_code,
            }

    active_job = (
        get_active_job(data.get("id")) or get_active_job(session_id) or get_active_job(folder_name)
    )
    data["active_job"] = active_job
    if active_job:
        target_v = active_job.get("target_version")
        if target_v is not None:
            has_target_code = 0 < target_v <= len(code_msgs)
            if target_v not in version_dict:
                version_dict[target_v] = {
                    "version": target_v,
                    "stl_url": "",
                    "has_stl": False,
                    "has_code": has_target_code,
                    "is_generating": True,
                    "isPending": True,
                    "status_message": active_job.get("message", "Generating..."),
                    "progress": active_job.get("progress", 0),
                }
            else:
                version_dict[target_v]["is_generating"] = True
                version_dict[target_v]["isPending"] = True
                version_dict[target_v]["status_message"] = active_job.get(
                    "message", "Generating..."
                )
                version_dict[target_v]["progress"] = active_job.get("progress", 0)

    for v_num, v_entry in version_dict.items():
        if 0 < v_num <= len(code_msgs):
            msg_meta = code_msgs[v_num - 1]
            if "warnings" in msg_meta:
                v_entry["warnings"] = msg_meta["warnings"]
            if "enable_supports" in msg_meta:
                v_entry["enable_supports"] = msg_meta["enable_supports"]
            if "has_supports" in msg_meta:
                v_entry["has_supports"] = msg_meta["has_supports"]

    available_versions = sorted(version_dict.values(), key=lambda x: x["version"])
    for v in reversed(available_versions):
        if v.get("has_stl") and v.get("stl_url"):
            latest_stl_url = v["stl_url"]
            break

    data["all_versions"] = available_versions

    # Ensure versions is accurately normalized from disk and history
    max_scanned_v = max([v["version"] for v in available_versions], default=0)
    history_code_v = sum(
        1 for m in data.get("history", []) if m.get("role") == "assistant" and "code" in m
    )
    data["versions"] = max(data.get("versions") or 0, max_scanned_v, history_code_v)

    if not os.path.exists(filepath) and latest_stl_url:
        data["latest_stl"] = latest_stl_url
    elif latest_stl_url and not data.get("latest_stl"):
        data["latest_stl"] = latest_stl_url

    return data


def save_session(session_id: str, data: dict):
    session_id = sanitize_id(session_id)
    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    session_dir = resolved or os.path.join(ws_dir, session_id)
    save_session_atomic(session_dir, data, sessions_dir=ws_dir)


def _write_batch_scripts(
    v_dir: str,
    base_name: str,
    requested_colors: list = None,
    enable_supports: bool = False,
):
    out_slice_bat = os.path.join(v_dir, "slice.bat")
    out_print_bat = os.path.join(v_dir, "print.bat")
    template_path = os.path.join(TEMPLATES_DIR, "default_project.3mf")

    with open(out_slice_bat, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write(
            f'"{get_bambu_studio_path()}" "{template_path}" --slice 0 --orient 1 --arrange 1 --export-3mf "{base_name}.3mf" "{base_name}.stl"\n'
        )
        f.write("echo Slicing complete!\n")
        f.write(
            'powershell -Command "$x = Add-Type -MemberDefinition \'<DllImport(\\"kernel32.dll\\")> public static extern IntPtr GetStdHandle(int h); <DllImport(\\"kernel32.dll\\")> public static extern bool SetConsoleMode(IntPtr h, int m);\' -Name \'Win32\' -PassThru; [void]$x::SetConsoleMode($x::GetStdHandle(-10), 128)"\n'.replace(
                "<", "["
            ).replace(">", "]")
        )
        f.write("pause\n")

    with open(out_print_bat, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        colors_csv = ",".join(requested_colors) if requested_colors else ""
        colors_csv_safe = colors_csv.replace('"', '\\"')
        support_flag = " --support" if enable_supports else ""
        f.write(
            f'"{sys.executable}" -m src.cli.manual_print "{base_name}.stl" "{colors_csv_safe}"{support_flag}\n'
        )
        f.write(
            'powershell -Command "$x = Add-Type -MemberDefinition \'<DllImport(\\"kernel32.dll\\")> public static extern IntPtr GetStdHandle(int h); <DllImport(\\"kernel32.dll\\")> public static extern bool SetConsoleMode(IntPtr h, int m);\' -Name \'Win32\' -PassThru; [void]$x::SetConsoleMode($x::GetStdHandle(-10), 128)"\n'.replace(
                "<", "["
            ).replace(">", "]")
        )
        f.write("pause\n")


def compile_openscad_generator(
    scad_code: str,
    session_id: str,
    version: int,
    requested_colors: list = None,
    enable_supports: bool = False,
    base_name: str = None,
    skip_compile: bool = False,
    prebuilt_stl: str = None,
    prebuilt_3mf: str = None,
    prebuilt_size: str = None,
    prebuilt_time: str = None,
    prebuilt_grams: float = None,
    prebuilt_has_supports: bool = None,
    warnings: list = None,
):
    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    session_dir = resolved or os.path.join(ws_dir, session_id)
    folder_name = os.path.basename(session_dir)
    v_dir = os.path.join(session_dir, f"v{version}")
    os.makedirs(v_dir, exist_ok=True)

    if not base_name:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"design_v{version}_{timestamp}"

    scad_file = os.path.join(v_dir, f"{base_name}.scad")
    stl_file = (
        prebuilt_stl if (skip_compile and prebuilt_stl) else os.path.join(v_dir, f"{base_name}.stl")
    )
    out_3mf = os.path.join(v_dir, f"{base_name}.3mf")

    scad_code = clean_scad_code(scad_code)
    with open(scad_file, "w", encoding="utf-8") as f:
        f.write(scad_code)

    _write_batch_scripts(
        v_dir,
        base_name,
        requested_colors=requested_colors,
        enable_supports=enable_supports,
    )

    total_steps = MAX_AI_CALLS + 2
    render_progress = int(((MAX_AI_CALLS + 1) / total_steps) * 100)
    slice_progress = int(((MAX_AI_CALLS + 2) / total_steps) * 100)

    yield {"status": "step", "message": "Rendering OpenSCAD to STL...", "progress": render_progress}
    set_active_job(session_id, version, "step", "Rendering OpenSCAD to STL...", render_progress)

    if not (skip_compile and os.path.exists(stl_file)):
        start_render = time.time()
        try:
            openscad_bin = _get_openscad_path()
            if openscad_bin == "mock_openscad":
                if not os.path.exists(stl_file):
                    with open(stl_file, "wb") as f:
                        f.write(
                            b"solid model\n"
                            b"  facet normal 0 0 0\n"
                            b"    outer loop\n"
                            b"      vertex 0 0 0\n"
                            b"      vertex 10 0 0\n"
                            b"      vertex 0 10 0\n"
                            b"    endloop\n"
                            b"  endfacet\n"
                            b"endsolid\n"
                        )
            else:
                proc = _get_subprocess().Popen(
                    [openscad_bin, "-o", stl_file, scad_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                last_yield = start_render
                RENDER_TIMEOUT = 300.0
                while True:
                    ret = proc.poll()
                    if ret is not None:
                        break
                    now = time.time()
                    if now - start_render > RENDER_TIMEOUT:
                        try:
                            proc.kill()
                            proc.communicate()
                        except Exception:
                            pass
                        err_msg = (
                            f"OpenSCAD rendering timed out after {int(RENDER_TIMEOUT)} seconds. "
                            "The generated design might be too complex."
                        )
                        print(f"OpenSCAD Error: {err_msg}")
                        set_active_job(
                            session_id,
                            version,
                            "error",
                            err_msg,
                            render_progress,
                        )
                        yield {
                            "status": "error",
                            "message": err_msg,
                        }
                        return

                    if now - last_yield >= 3.0:
                        elapsed_sec = int(now - start_render)
                        mins, secs = divmod(elapsed_sec, 60)
                        elapsed_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                        msg = f"Rendering OpenSCAD to STL... ({elapsed_str})"
                        set_active_job(session_id, version, "step", msg, render_progress)
                        yield {"status": "step", "message": msg, "progress": render_progress}
                        last_yield = now
                    time.sleep(0.5)

                stdout, stderr = proc.communicate()
                if proc.returncode != 0:
                    err_msg = (stderr or stdout or f"Exited with code {proc.returncode}").strip()
                    print(f"OpenSCAD Error: {err_msg}")
                    set_active_job(
                        session_id,
                        version,
                        "error",
                        f"Failed to compile OpenSCAD to STL: {err_msg}",
                        render_progress,
                    )
                    yield {
                        "status": "error",
                        "message": f"Failed to compile OpenSCAD to STL: {err_msg}",
                    }
                    return
        except Exception as e:
            print(f"OpenSCAD Error: {e}")
            set_active_job(
                session_id,
                version,
                "error",
                f"Failed to compile OpenSCAD to STL: {str(e)}",
                render_progress,
            )
            yield {"status": "error", "message": f"Failed to compile OpenSCAD to STL: {str(e)}"}
            return

    yield {"status": "step", "message": "Slicing geometry to 3MF...", "progress": slice_progress}
    set_active_job(session_id, version, "step", "Slicing geometry to 3MF...", slice_progress)

    if skip_compile and prebuilt_3mf and os.path.exists(prebuilt_3mf):
        result_3mf = prebuilt_3mf
    elif skip_compile and os.path.exists(out_3mf):
        result_3mf = out_3mf
    else:
        # Run the reliable injection slicer, and soft-fail if it doesn't work
        result_3mf = _get_shared_slicer()(
            stl_file, v_dir, base_name, enable_supports=enable_supports
        )

    print_time = (
        prebuilt_time
        if (skip_compile and prebuilt_time and prebuilt_time != "Unknown")
        else "Unknown"
    )
    size_msg = (
        prebuilt_size
        if (skip_compile and prebuilt_size and prebuilt_size != "Unknown")
        else "Unknown"
    )
    print_grams = prebuilt_grams if (skip_compile and prebuilt_grams) else 0.0

    if not result_3mf and print_time == "Unknown":
        print("Warning: Slicing failed, but saving session anyway to allow rendering.")

    has_supports = bool(prebuilt_has_supports) if prebuilt_has_supports is not None else False
    if result_3mf and (print_time == "Unknown" or not print_grams or prebuilt_has_supports is None):
        slice_info = get_slice_info_from_3mf(result_3mf)
        if print_time == "Unknown":
            print_time = slice_info.get("time", "Unknown")
        if not print_grams:
            print_grams = slice_info.get("grams", 0.0)
        if "has_supports" in slice_info:
            has_supports = bool(slice_info.get("has_supports", False))

    if size_msg == "Unknown":
        try:
            size_msg = format_dimensions_inches(stl_file)
        except Exception:
            pass

    # We serve the path relative to the backend so the frontend can access it via the static mount
    yield {
        "status": "complete",
        "assets": {
            "stl_url": f"/created_stls/{urllib.parse.quote(folder_name)}/v{version}/{urllib.parse.quote(base_name)}.stl",
            "time": print_time,
            "size": size_msg,
            "grams": print_grams,
            "has_supports": has_supports,
            "enable_supports": enable_supports,
            "warnings": list(warnings) if warnings else [],
        },
    }


@router.get("/sessions")
def list_sessions(
    q: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    response: Response = None,
):
    """Returns a list of active idea sessions, optionally filtered and paginated."""
    sessions = []
    ws_dir = get_workspace_dir()
    if not os.path.exists(ws_dir):
        if response is not None and isinstance(response, Response):
            response.headers["X-Total-Count"] = "0"
            response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
        return sessions
    for item in os.listdir(ws_dir):
        if item.startswith("."):
            continue
        item_path = os.path.join(ws_dir, item)
        if os.path.isdir(item_path):
            session_file = os.path.join(item_path, "session.json")
            if os.path.exists(session_file):
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

                title = data.get("title", "Untitled Idea")
                sess_id = data.get("id") or item

                match_val = True
                if q:
                    q_lower = q.lower()
                    match_val = False
                    if q_lower in title.lower():
                        match_val = True
                    elif q_lower in item.lower() or q_lower in str(sess_id).lower():
                        match_val = True
                    else:
                        history = data.get("history", [])
                        for msg in history:
                            if "text" in msg and msg["text"] and q_lower in msg["text"].lower():
                                match_val = True
                                break

                if match_val:
                    job = (
                        get_active_job(sess_id)
                        or get_active_job(item)
                        or get_active_job(str(sess_id).replace("-", "")[:8])
                    )
                    sessions.append(
                        {
                            "id": sess_id,
                            "folder_name": item,
                            "title": title,
                            "last_updated": data.get("last_updated"),
                            "active_job": job,
                        }
                    )
            else:
                title = item
                last_updated = None
                try:
                    m = re.search(r"^(\d{8}_\d{6})", item) or re.search(r"_(\d{8}_\d{6})$", item)
                    if m:
                        dt = datetime.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
                        last_updated = dt.isoformat()
                    else:
                        stat = os.stat(item_path)
                        last_updated = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
                except Exception:
                    pass

                match_val = True
                if q:
                    q_lower = q.lower()
                    if q_lower not in title.lower() and q_lower not in item.lower():
                        match_val = False

                if match_val:
                    job = get_active_job(item)
                    sessions.append(
                        {
                            "id": item,
                            "folder_name": item,
                            "title": title,
                            "last_updated": last_updated,
                            "active_job": job,
                        }
                    )
    # Sort by newest first
    sessions.sort(key=lambda x: x.get("last_updated") or "", reverse=True)
    total_count = len(sessions)

    # Normalize pagination arguments if called directly in tests with FastAPI Query defaults
    safe_limit = limit if isinstance(limit, int) else None
    safe_offset = max(0, offset) if isinstance(offset, int) else 0

    if response is not None and isinstance(response, Response):
        response.headers["X-Total-Count"] = str(total_count)
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    if safe_limit is not None:
        return sessions[safe_offset : safe_offset + safe_limit]
    return sessions


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session_id = sanitize_id(session_id)
    return load_session(session_id)


@router.post("/sessions/new")
@router.post("/sessions")
def create_session(req: NewSessionRequest):
    """Start a new idea."""

    def generate():
        session_id = str(uuid.uuid4())
        short8 = session_id.replace("-", "")[:8]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{timestamp}_generating_{short8}"
        ws_dir = get_workspace_dir()
        session_dir = os.path.join(ws_dir, folder_name)
        os.makedirs(session_dir, exist_ok=True)

        clean_prompt, prompt_support = parse_and_strip_support_flags(req.prompt)
        if prompt_support is not None:
            enable_supports = prompt_support
        elif req.enable_supports is not None:
            enable_supports = req.enable_supports
        else:
            enable_supports = False

        session_data = {
            "id": session_id,
            "folder_name": folder_name,
            "title": "In Progress...",
            "history": [{"role": "user", "text": clean_prompt}],
            "versions": 1,
            "latest_stl": "",
            "enable_supports": enable_supports,
            "last_updated": datetime.datetime.now().isoformat(),
            "last_active_version": 1,
        }
        save_session(session_id, session_data)

        set_active_job(session_id, 1, "step", "Starting generation...", 0)
        yield (
            json.dumps(
                {
                    "status": "init",
                    "session_id": session_id,
                    "session": load_session(session_id),
                }
            )
            + "\n"
        )
        try:
            with busy_activity("web_generation", metadata={"session_id": session_id}):
                v1_dir = os.path.join(session_dir, "v1")
                os.makedirs(v1_dir, exist_ok=True)
                base_name = f"design_v1_{timestamp}"
                prebuilt_stl = None
                prebuilt_3mf = None
                prebuilt_size = None
                prebuilt_time = None
                prebuilt_grams = None
                prebuilt_has_supports = None
                warnings = []

                if clean_prompt.strip().lower() == "practice test":
                    code = "cube([20, 20, 20], center=true);"
                    filename = "practice_cube"
                    colors = []
                else:
                    try:
                        code = None
                        filename = None
                        colors = []
                        total_steps = MAX_AI_CALLS + 2
                        for step in _get_generate_with_validation()(
                            clean_prompt,
                            [],
                            max_calls=MAX_AI_CALLS,
                            service="online_backend",
                            enable_supports=enable_supports,
                            output_dir=v1_dir,
                            file_basename=base_name,
                        ):
                            if step["type"] == "progress":
                                progress = int((step["attempt"] / total_steps) * 100)
                                set_active_job(session_id, 1, "step", step["message"], progress)
                                yield (
                                    json.dumps(
                                        {
                                            "status": "step",
                                            "message": step["message"],
                                            "progress": progress,
                                            "attempt": step["attempt"],
                                            "max_calls": step["max_calls"],
                                        }
                                    )
                                    + "\n"
                                )
                            elif step["type"] == "result":
                                code = step["code"]
                                filename = step["filename"]
                                colors = step["colors"]
                                prebuilt_stl = step.get("stl_path")
                                prebuilt_3mf = step.get("3mf_path")
                                prebuilt_size = step.get("size")
                                prebuilt_time = step.get("time")
                                prebuilt_grams = step.get("grams")
                                prebuilt_has_supports = step.get("has_supports")
                                warnings = step.get("warnings") or []
                        if not code:
                            raise RuntimeError("AI did not produce any code")
                    except Exception as e:
                        session_data["history"].append(
                            {
                                "role": "assistant",
                                "text": f"Error: AI failed to respond ({str(e)}).",
                            }
                        )
                        save_session(session_id, session_data)
                        yield (
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"AI Error: {str(e)}",
                                    "session_id": session_id,
                                    "session": load_session(session_id),
                                }
                            )
                            + "\n"
                        )
                        return

                # Rename folder once AI returns filename
                new_dir = rename_session_folder(
                    session_id, filename or "model", sessions_dir=get_workspace_dir()
                )
                if new_dir:
                    session_dir = new_dir
                    v1_dir = os.path.join(session_dir, "v1")
                    session_data["folder_name"] = os.path.basename(new_dir)
                    if prebuilt_stl and os.path.exists(
                        os.path.join(v1_dir, os.path.basename(prebuilt_stl))
                    ):
                        prebuilt_stl = os.path.join(v1_dir, os.path.basename(prebuilt_stl))
                    if prebuilt_3mf and os.path.exists(
                        os.path.join(v1_dir, os.path.basename(prebuilt_3mf))
                    ):
                        prebuilt_3mf = os.path.join(v1_dir, os.path.basename(prebuilt_3mf))

                session_data["title"] = filename
                session_data["history"].append(
                    {
                        "role": "assistant",
                        "code": code,
                        "enable_supports": enable_supports,
                        "warnings": list(warnings),
                    }
                )
                save_session(session_id, session_data)

                assets = None
                has_error = False

                compile_func = _get_compile_openscad_generator()
                sig = inspect.signature(compile_func)
                has_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                compile_kwargs = {}
                if "enable_supports" in sig.parameters or has_var_kw:
                    compile_kwargs["enable_supports"] = enable_supports
                skip_flag = bool(prebuilt_stl and os.path.exists(prebuilt_stl))
                for k, v in [
                    ("base_name", base_name),
                    ("skip_compile", skip_flag),
                    ("prebuilt_stl", prebuilt_stl),
                    ("prebuilt_3mf", prebuilt_3mf),
                    ("prebuilt_size", prebuilt_size),
                    ("prebuilt_time", prebuilt_time),
                    ("prebuilt_grams", prebuilt_grams),
                    ("prebuilt_has_supports", prebuilt_has_supports),
                    ("warnings", warnings),
                ]:
                    if k in sig.parameters or has_var_kw:
                        compile_kwargs[k] = v

                for step in compile_func(code, session_id, 1, colors, **compile_kwargs):
                    if step["status"] == "complete":
                        assets = step["assets"]
                    elif step["status"] == "error":
                        has_error = True
                        session_data["history"].append(
                            {
                                "role": "assistant",
                                "text": f"Error compiling 3D model: {step.get('message', 'Unknown error')}. Please try asking for a simpler design or fixing the issue.",
                            }
                        )
                        save_session(session_id, session_data)
                        err_payload = dict(step)
                        err_payload["session_id"] = session_id
                        err_payload["session"] = load_session(session_id)
                        yield json.dumps(err_payload) + "\n"
                        return
                    else:
                        yield json.dumps(step) + "\n"

                if has_error or not assets:
                    return

                for msg in reversed(session_data["history"]):
                    if msg.get("role") == "assistant" and "code" in msg:
                        msg["time"] = assets.get("time", "Unknown")
                        msg["size"] = assets.get("size", "Unknown")
                        msg["grams"] = assets.get("grams", 0.0)
                        msg["enable_supports"] = assets.get("enable_supports", enable_supports)
                        msg["has_supports"] = assets.get(
                            "has_supports",
                            bool(prebuilt_has_supports)
                            if prebuilt_has_supports is not None
                            else False,
                        )
                        msg["warnings"] = assets.get("warnings", list(warnings))
                        break

                session_data["versions"] = 1
                session_data["latest_stl"] = assets["stl_url"]
                session_data["last_updated"] = datetime.datetime.now().isoformat()
                session_data["last_active_version"] = 1
                save_session(session_id, session_data)

                # Enforce 3-tier retention policy
                enforce_storage_retention_policy(
                    active_session_ids={session_id}, sessions_dir=get_workspace_dir()
                )

                clear_active_job(session_id)
                yield (
                    json.dumps(
                        {
                            "status": "complete",
                            "session": load_session(session_id),
                            "assets": assets,
                        }
                    )
                    + "\n"
                )
        finally:
            clear_active_job(session_id)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/sessions/{session_id}/edit")
def edit_session(session_id: str, req: EditSessionRequest):
    session_id = sanitize_id(session_id)
    session_data = load_session(session_id)

    def generate():
        clean_prompt, prompt_support = parse_and_strip_support_flags(req.prompt)
        if prompt_support is not None:
            enable_supports = prompt_support
        elif req.enable_supports is not None:
            enable_supports = req.enable_supports
        else:
            enable_supports = session_data.get("enable_supports", False)

        session_data["enable_supports"] = enable_supports
        session_data["history"].append({"role": "user", "text": clean_prompt})
        save_session(session_id, session_data)

        existing_v = session_data.get("versions") or 0
        if session_data.get("all_versions"):
            existing_v = max(
                existing_v,
                max((v["version"] for v in session_data["all_versions"]), default=0),
            )
        history_code_count = sum(
            1
            for m in session_data.get("history", [])
            if m.get("role") == "assistant" and "code" in m
        )
        existing_v = max(existing_v, history_code_count)
        next_version = max(existing_v, 0) + 1
        set_active_job(session_id, next_version, "step", "Starting edit...", 0)
        try:
            with busy_activity("web_generation", metadata={"session_id": session_id}):
                ws_dir = get_workspace_dir()
                resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
                session_dir = resolved or os.path.join(ws_dir, session_id)
                v_dir = os.path.join(session_dir, f"v{next_version}")
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                base_name = f"design_v{next_version}_{timestamp}"
                prebuilt_stl = None
                prebuilt_3mf = None
                prebuilt_size = None
                prebuilt_time = None
                prebuilt_grams = None
                prebuilt_has_supports = None
                warnings = []

                if clean_prompt.strip().lower() == "practice test":
                    code = "cube([20, 20, 20], center=true);"
                    filename = "practice_cube_edit"
                    colors = []
                else:
                    try:
                        code = None
                        filename = None
                        colors = []
                        total_steps = MAX_AI_CALLS + 2
                        for step in _get_generate_with_validation()(
                            clean_prompt,
                            session_data["history"],
                            max_calls=MAX_AI_CALLS,
                            service="online_backend",
                            enable_supports=enable_supports,
                            output_dir=v_dir,
                            file_basename=base_name,
                        ):
                            if step["type"] == "progress":
                                progress = int((step["attempt"] / total_steps) * 100)
                                set_active_job(
                                    session_id, next_version, "step", step["message"], progress
                                )
                                yield (
                                    json.dumps(
                                        {
                                            "status": "step",
                                            "message": step["message"],
                                            "progress": progress,
                                            "attempt": step["attempt"],
                                            "max_calls": step["max_calls"],
                                        }
                                    )
                                    + "\n"
                                )
                            elif step["type"] == "result":
                                code = step["code"]
                                filename = step["filename"]
                                colors = step["colors"]
                                prebuilt_stl = step.get("stl_path")
                                prebuilt_3mf = step.get("3mf_path")
                                prebuilt_size = step.get("size")
                                prebuilt_time = step.get("time")
                                prebuilt_grams = step.get("grams")
                                prebuilt_has_supports = step.get("has_supports")
                                warnings = step.get("warnings") or []
                        if not code:
                            raise RuntimeError("AI did not produce any code")
                    except Exception as e:
                        session_data["history"].append(
                            {
                                "role": "assistant",
                                "text": f"Error: AI failed to respond ({str(e)}).",
                            }
                        )
                        save_session(session_id, session_data)
                        yield (
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"AI Error: {str(e)}",
                                    "session_id": session_id,
                                    "session": load_session(session_id),
                                }
                            )
                            + "\n"
                        )
                        return

                assistant_msg = {
                    "role": "assistant",
                    "code": code,
                    "enable_supports": enable_supports,
                    "warnings": list(warnings),
                }
                session_data["history"].append(assistant_msg)
                save_session(session_id, session_data)

                assets = None
                has_error = False

                compile_func = _get_compile_openscad_generator()
                sig = inspect.signature(compile_func)
                has_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                compile_kwargs = {}
                if "enable_supports" in sig.parameters or has_var_kw:
                    compile_kwargs["enable_supports"] = enable_supports
                skip_flag = bool(prebuilt_stl and os.path.exists(prebuilt_stl))
                for k, v in [
                    ("base_name", base_name),
                    ("skip_compile", skip_flag),
                    ("prebuilt_stl", prebuilt_stl),
                    ("prebuilt_3mf", prebuilt_3mf),
                    ("prebuilt_size", prebuilt_size),
                    ("prebuilt_time", prebuilt_time),
                    ("prebuilt_grams", prebuilt_grams),
                    ("prebuilt_has_supports", prebuilt_has_supports),
                    ("warnings", warnings),
                ]:
                    if k in sig.parameters or has_var_kw:
                        compile_kwargs[k] = v

                for step in compile_func(code, session_id, next_version, colors, **compile_kwargs):
                    if step["status"] == "complete":
                        assets = step["assets"]
                    elif step["status"] == "error":
                        has_error = True
                        session_data["history"].append(
                            {
                                "role": "assistant",
                                "text": f"Error compiling 3D model: {step.get('message', 'Unknown error')}. Please try asking for a simpler design or fixing the issue.",
                            }
                        )
                        save_session(session_id, session_data)
                        err_payload = dict(step)
                        err_payload["session_id"] = session_id
                        err_payload["session"] = load_session(session_id)
                        yield json.dumps(err_payload) + "\n"
                        return
                    else:
                        yield json.dumps(step) + "\n"

                if has_error or not assets:
                    return

                session_data["history"][-1]["time"] = assets.get("time", "Unknown")
                session_data["history"][-1]["size"] = assets.get("size", "Unknown")
                session_data["history"][-1]["grams"] = assets.get("grams", 0.0)
                session_data["history"][-1]["enable_supports"] = assets.get(
                    "enable_supports", enable_supports
                )
                session_data["history"][-1]["has_supports"] = assets.get(
                    "has_supports",
                    bool(prebuilt_has_supports) if prebuilt_has_supports is not None else False,
                )
                session_data["history"][-1]["warnings"] = assets.get("warnings", list(warnings))

                session_data["title"] = filename
                session_data["versions"] = next_version
                session_data["latest_stl"] = assets["stl_url"]
                session_data["last_updated"] = datetime.datetime.now().isoformat()
                session_data["last_active_version"] = next_version

                save_session(session_id, session_data)

                # Enforce 3-tier retention policy
                enforce_storage_retention_policy(
                    active_session_ids={session_id}, sessions_dir=get_workspace_dir()
                )

                clear_active_job(session_id)
                yield (
                    json.dumps(
                        {
                            "status": "complete",
                            "session": load_session(session_id),
                            "assets": assets,
                        }
                    )
                    + "\n"
                )
        finally:
            clear_active_job(session_id)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/sessions/{session_id}/rerender")
@router.post("/sessions/{session_id}/versions/{version}/rerender")
def rerender_session(session_id: str, version: Optional[int] = None):
    session_id = sanitize_id(session_id)
    session_data = load_session(session_id)

    target_version = version if version is not None else (session_data.get("versions") or 1)
    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    session_dir = resolved or os.path.join(ws_dir, session_id)
    folder_name = os.path.basename(session_dir)
    v_dir = os.path.join(session_dir, f"v{target_version}")
    os.makedirs(v_dir, exist_ok=True)

    def generate():
        set_active_job(
            session_id, target_version, "step", f"Locating code for v{target_version}...", 25
        )
        try:
            with busy_activity("web_generation", metadata={"session_id": session_id}):
                yield (
                    json.dumps(
                        {
                            "status": "step",
                            "message": f"Locating code for v{target_version}...",
                            "progress": 25,
                        }
                    )
                    + "\n"
                )

                scad_files = (
                    [f for f in os.listdir(v_dir) if f.endswith(".scad")]
                    if os.path.exists(v_dir)
                    else []
                )
                scad_code = None
                base_name = None

                code_msgs_indices = [
                    i
                    for i, m in enumerate(session_data.get("history", []))
                    if m.get("role") == "assistant" and "code" in m
                ]
                ver_enable_supports = session_data.get("enable_supports", False)
                if 0 < target_version <= len(code_msgs_indices):
                    idx = code_msgs_indices[target_version - 1]
                    ver_enable_supports = session_data["history"][idx].get(
                        "enable_supports", ver_enable_supports
                    )

                if scad_files:
                    scad_file = os.path.join(v_dir, scad_files[0])
                    base_name = os.path.splitext(scad_files[0])[0]
                    with open(scad_file, "r", encoding="utf-8") as f:
                        scad_code = f.read()
                    cleaned = clean_scad_code(scad_code)
                    if cleaned != scad_code:
                        scad_code = cleaned
                        with open(scad_file, "w", encoding="utf-8") as f:
                            f.write(scad_code)
                else:
                    code_msgs = [
                        m
                        for m in session_data.get("history", [])
                        if m.get("role") == "assistant" and "code" in m
                    ]
                    if 0 < target_version <= len(code_msgs):
                        scad_code = code_msgs[target_version - 1]["code"]
                    elif code_msgs:
                        scad_code = code_msgs[-1]["code"]

                    if not scad_code:
                        yield (
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"No OpenSCAD source code found for v{target_version}.",
                                }
                            )
                            + "\n"
                        )
                        return

                    scad_code = clean_scad_code(scad_code)
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    base_name = f"design_v{target_version}_{timestamp}"
                    scad_file = os.path.join(v_dir, f"{base_name}.scad")
                    with open(scad_file, "w", encoding="utf-8") as f:
                        f.write(scad_code)

                stl_file = os.path.join(v_dir, f"{base_name}.stl")
                _write_batch_scripts(
                    v_dir,
                    base_name,
                    enable_supports=ver_enable_supports,
                )

                yield (
                    json.dumps(
                        {
                            "status": "step",
                            "message": "Rendering OpenSCAD to STL...",
                            "progress": 60,
                        }
                    )
                    + "\n"
                )
                set_active_job(
                    session_id, target_version, "step", "Rendering OpenSCAD to STL...", 60
                )

                start_render = time.time()
                try:
                    openscad_bin = _get_openscad_path()
                    if openscad_bin == "mock_openscad":
                        if not os.path.exists(stl_file):
                            with open(stl_file, "wb") as f:
                                f.write(
                                    b"solid model\n"
                                    b"  facet normal 0 0 0\n"
                                    b"    outer loop\n"
                                    b"      vertex 0 0 0\n"
                                    b"      vertex 10 0 0\n"
                                    b"      vertex 0 10 0\n"
                                    b"    endloop\n"
                                    b"  endfacet\n"
                                    b"endsolid\n"
                                )
                    else:
                        proc = _get_subprocess().Popen(
                            [openscad_bin, "-o", stl_file, scad_file],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        last_yield = start_render
                        RENDER_TIMEOUT = 300.0
                        while True:
                            ret = proc.poll()
                            if ret is not None:
                                break
                            now = time.time()
                            if now - start_render > RENDER_TIMEOUT:
                                try:
                                    proc.kill()
                                    proc.communicate()
                                except Exception:
                                    pass
                                err_msg = "OpenSCAD compilation timed out after 300 seconds."
                                print(f"OpenSCAD Error: {err_msg}")
                                set_active_job(
                                    session_id,
                                    target_version,
                                    "error",
                                    err_msg,
                                    60,
                                )
                                yield (
                                    json.dumps(
                                        {
                                            "status": "error",
                                            "message": err_msg,
                                        }
                                    )
                                    + "\n"
                                )
                                return

                            if now - last_yield >= 3.0:
                                elapsed_sec = int(now - start_render)
                                mins, secs = divmod(elapsed_sec, 60)
                                elapsed_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                                msg = f"Rendering OpenSCAD to STL... ({elapsed_str})"
                                set_active_job(session_id, target_version, "step", msg, 60)
                                yield (
                                    json.dumps({"status": "step", "message": msg, "progress": 60})
                                    + "\n"
                                )
                                last_yield = now
                            time.sleep(0.5)

                        stdout, stderr = proc.communicate()
                        if proc.returncode != 0:
                            err_msg = (
                                stderr or stdout or f"Exited with code {proc.returncode}"
                            ).strip()
                            print(f"OpenSCAD Error: {err_msg}")
                            set_active_job(
                                session_id,
                                target_version,
                                "error",
                                f"Failed to compile OpenSCAD to STL: {err_msg}",
                                60,
                            )
                            yield (
                                json.dumps(
                                    {
                                        "status": "error",
                                        "message": f"Failed to compile OpenSCAD to STL: {err_msg}",
                                    }
                                )
                                + "\n"
                            )
                            return
                except Exception as e:
                    print(f"OpenSCAD Error: {e}")
                    set_active_job(
                        session_id,
                        target_version,
                        "error",
                        f"Failed to compile OpenSCAD to STL: {str(e)}",
                        60,
                    )
                    yield (
                        json.dumps(
                            {
                                "status": "error",
                                "message": f"Failed to compile OpenSCAD to STL: {str(e)}",
                            }
                        )
                        + "\n"
                    )
                    return

                size_msg = "Unknown"
                try:
                    size_msg = format_dimensions_inches(stl_file)
                except Exception:
                    pass

                # Run slicer if available and Ready_{base_name}.3mf does not exist
                ready_3mf = os.path.join(v_dir, f"Ready_{base_name}.3mf")
                out_3mf = os.path.join(v_dir, f"{base_name}.3mf")
                result_3mf = None
                if os.path.exists(ready_3mf):
                    result_3mf = ready_3mf
                elif os.path.exists(out_3mf):
                    result_3mf = out_3mf
                else:
                    slicer_fn = _get_shared_slicer()
                    if slicer_fn:
                        yield (
                            json.dumps(
                                {
                                    "status": "step",
                                    "message": "Slicing geometry to 3MF...",
                                    "progress": 85,
                                }
                            )
                            + "\n"
                        )
                        set_active_job(
                            session_id,
                            target_version,
                            "step",
                            "Slicing geometry to 3MF...",
                            85,
                        )
                        try:
                            result_3mf = slicer_fn(
                                stl_file,
                                v_dir,
                                base_name,
                                enable_supports=ver_enable_supports,
                            )
                        except Exception as slice_err:
                            print(f"Warning: Slicing during rerender failed: {slice_err}")

                slice_info = {}
                if result_3mf and os.path.exists(result_3mf):
                    try:
                        slice_info = get_slice_info_from_3mf(result_3mf) or {}
                    except Exception:
                        slice_info = {}

                if 0 < target_version <= len(code_msgs_indices):
                    idx = code_msgs_indices[target_version - 1]
                    session_data["history"][idx]["size"] = size_msg
                    if result_3mf:
                        session_data["history"][idx]["3mf_path"] = result_3mf
                    if slice_info.get("time") and slice_info.get("time") != "Unknown":
                        session_data["history"][idx]["time"] = slice_info["time"]
                    if slice_info.get("grams"):
                        session_data["history"][idx]["grams"] = slice_info["grams"]
                    if "has_supports" in slice_info:
                        session_data["history"][idx]["has_supports"] = bool(
                            slice_info["has_supports"]
                        )

                stl_url = f"/created_stls/{urllib.parse.quote(folder_name)}/v{target_version}/{urllib.parse.quote(os.path.basename(stl_file))}"
                session_data["latest_stl"] = stl_url
                session_data["versions"] = max(session_data.get("versions") or 0, target_version)
                session_data["last_updated"] = datetime.datetime.now().isoformat()
                session_data["last_active_version"] = target_version
                save_session(session_id, session_data)

                # Re-render promotes session to Rank #1 (Top 15) and protects target_version!
                enforce_storage_retention_policy(
                    active_session_ids={session_id}, sessions_dir=ws_dir
                )

                clear_active_job(session_id)
                updated_session = load_session(session_id)
                yield json.dumps({"status": "complete", "session": updated_session}) + "\n"
        finally:
            clear_active_job(session_id)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/sessions/{session_id}/open_folder")
def open_folder(session_id: str, req: Optional[OpenFolderRequest] = None):
    session_id = sanitize_id(session_id)
    version = req.version if req and req.version else 1
    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    session_dir = resolved or os.path.join(ws_dir, session_id)
    v_dir = os.path.join(session_dir, f"v{version}")

    if not os.path.exists(v_dir):
        v_dir = session_dir
        if not os.path.exists(v_dir):
            raise HTTPException(status_code=404, detail="Folder not found")

    try:
        norm_v_dir = os.path.normpath(os.path.abspath(v_dir))
        if sys.platform == "win32":
            if not os.environ.get("PYTEST_CURRENT_TEST") and hasattr(os, "startfile"):
                try:
                    os.startfile(norm_v_dir)
                except Exception:
                    _get_subprocess().Popen(["explorer.exe", norm_v_dir])
            else:
                _get_subprocess().Popen(["explorer.exe", norm_v_dir])
        elif sys.platform == "darwin":
            _get_subprocess().Popen(["open", norm_v_dir])
        else:
            _get_subprocess().Popen(["xdg-open", norm_v_dir])
        return {"status": "ok"}
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"Error opening folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    session_id = sanitize_id(session_id)
    """Delete a session and all its data."""
    ws_dir = get_workspace_dir()
    deleted = SessionManager.delete_session(session_id, sessions_dir=ws_dir)
    if deleted:
        return {"status": "ok", "message": "Session deleted"}
    raise HTTPException(status_code=404, detail="Session not found")

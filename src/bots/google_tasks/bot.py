import datetime
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.core.activity_tracker import busy_activity
from src.core.ai import generate_openscad_validated, get_max_ai_calls
from src.core.config import ROOT_DIR, SESSIONS_DIR, get_openscad_path, load_api_keys
from src.core.printer import BambuClient
from src.core.sessions import (
    format_session_dir_name,
    save_session_atomic,
    write_version_print_scripts,
)

SCOPES = ["https://www.googleapis.com/auth/tasks"]
CREDENTIALS_PATH = os.path.join(ROOT_DIR, "inputs", "google_tasks_api.json")
TOKEN_PATH = os.path.join(ROOT_DIR, "outputs", ".cache", "google_tasks_token.json")
MAX_AI_CALLS = get_max_ai_calls("google_tasks")
WORKSPACE_DIR = SESSIONS_DIR
POLL_INTERVAL = 30  # seconds


def get_google_tasks_supports_config() -> bool:
    config_path = os.path.join(ROOT_DIR, "inputs", "configs", "agent_config.json")
    if not os.path.exists(config_path):
        config_path = os.path.join(ROOT_DIR, "inputs", "agent_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "google_tasks_enable_supports" in data:
                    return bool(data["google_tasks_enable_supports"])
                sc = data.get("service_constraints", {}).get("google_tasks", {})
                if "enable_supports" in sc:
                    return bool(sc["enable_supports"])
        except Exception:
            pass
    return False


def _resolve_module(explicit=None):
    if explicit is not None and not isinstance(explicit, str):
        return explicit
    import inspect
    import types

    frame = inspect.currentframe()
    try:
        cur = frame.f_back
        while cur:
            for val in cur.f_locals.values():
                if (
                    isinstance(val, types.ModuleType)
                    and hasattr(val, "TOKEN_PATH")
                    and hasattr(val, "authenticate_google_tasks")
                ):
                    return val
            cur = cur.f_back
    finally:
        del frame
    return sys.modules.get("src.bots.google_tasks.bot") or sys.modules.get(__name__)


def _dispatch(explicit=None, default=None):
    if isinstance(explicit, str):
        mod = _resolve_module(None)
        return getattr(mod, explicit, default)
    return _resolve_module(explicit)


def authenticate_google_tasks(module=None):
    d = _dispatch(module)
    creds_cls = getattr(d, "Credentials", Credentials)
    flow_cls = getattr(d, "InstalledAppFlow", InstalledAppFlow)
    os_mod = getattr(d, "os", os)
    token_path = getattr(d, "TOKEN_PATH", TOKEN_PATH)
    credentials_path = getattr(d, "CREDENTIALS_PATH", CREDENTIALS_PATH)
    load_keys_fn = getattr(d, "load_api_keys", load_api_keys)

    creds = None
    if os_mod.path.exists(token_path):
        try:
            creds = creds_cls.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"Cached Google Tasks token is invalid or corrupted ({e}). Re-authenticating...")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh credentials ({e}). Re-authenticating...")
                creds = None

        if not creds:
            keys = load_keys_fn()
            client_id = keys.get("GOOGLE_TASKS_CLIENT_ID", "").strip()
            client_secret = keys.get("GOOGLE_TASKS_CLIENT_SECRET", "").strip()
            if client_id and client_secret:
                client_config = {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"],
                    }
                }
                flow = flow_cls.from_client_config(client_config, SCOPES)
            elif os_mod.path.exists(credentials_path):
                flow = flow_cls.from_client_secrets_file(credentials_path, SCOPES)
            else:
                if not getattr(os_mod, "environ", {}).get("PYTEST_CURRENT_TEST"):
                    print(
                        "\n"
                        + "=" * 60
                        + "\n"
                        + " [!] GOOGLE_TASKS_CLIENT_ID or GOOGLE_TASKS_CLIENT_SECRET not set in .env.\n"
                        + "     To use the Google Tasks bot:\n"
                        + "     1. Create OAuth 2.0 Client credentials in Google Cloud Console.\n"
                        + "     2. Add GOOGLE_TASKS_CLIENT_ID and GOOGLE_TASKS_CLIENT_SECRET to .env.\n"
                        + "=" * 60
                        + "\n"
                    )
                    sys.exit(0)
                else:
                    print(
                        "ERROR: GOOGLE_TASKS_CLIENT_ID and GOOGLE_TASKS_CLIENT_SECRET not set in .env "
                        f"and credentials file not found at {credentials_path}"
                    )
                    print(
                        "Please add GOOGLE_TASKS_CLIENT_ID and GOOGLE_TASKS_CLIENT_SECRET to .env."
                    )
                    sys.exit(1)
            creds = flow.run_local_server(port=0)

        os_mod.makedirs(os_mod.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    return creds


def save_session(session_id, data, module=None):
    d = _dispatch(module)
    ws = getattr(d, "WORKSPACE_DIR", WORKSPACE_DIR)
    os_mod = getattr(d, "os", os)
    session_dir = os_mod.path.join(ws, session_id)
    save_session_atomic(session_dir, data)
    try:
        from src.core.sessions import enforce_storage_retention_policy

        enforce_storage_retention_policy(active_session_ids={session_id})
    except Exception:
        pass


def record_task_failure(service, task_id, title, notes, error_msg):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    failed_title = f"[FAILED] {title}" if not title.startswith("[FAILED]") else title
    failure_note = f"\n\n[PRINT FAILED at {now_str}]: {error_msg}"
    updated_notes = (notes or "") + failure_note
    try:
        service.tasks().patch(
            tasklist="@default",
            task=task_id,
            body={
                "title": failed_title,
                "notes": updated_notes,
                "status": "needsAction",
            },
        ).execute()
    except Exception as e:
        print(f"Failed to update task failure notes: {e}")


def process_tasks(service, module=None):
    d = _dispatch(module)
    save_session_fn = getattr(d, "save_session", save_session)
    generate_fn = getattr(d, "generate_openscad_validated", generate_openscad_validated)
    get_openscad_path_fn = getattr(d, "get_openscad_path", get_openscad_path)
    get_supports_fn = getattr(
        d, "get_google_tasks_supports_config", get_google_tasks_supports_config
    )
    subprocess_mod = getattr(d, "subprocess", subprocess)
    os_mod = getattr(d, "os", os)
    ws = getattr(d, "WORKSPACE_DIR", WORKSPACE_DIR)

    try:
        results = service.tasks().list(tasklist="@default", showCompleted=False).execute()
        items = results.get("items", [])

        for task in items:
            title = task.get("title", "")
            notes = task.get("notes", "")
            task_id = task["id"]

            if title.lower().startswith("3d print"):
                print(f"\n[Task Found] {title}")

                clean_title = re.sub(r"(?i)^3d\s*print[:\-]?\s*", "", title).strip()
                prompt = clean_title
                if notes:
                    prompt += f"\n{notes}"

                print(f"Processing autonomously: '{prompt}'...")

                with busy_activity(
                    "google_tasks_generation",
                    metadata={"task_id": task_id, "title": title},
                ):
                    session_uuid = str(uuid.uuid4())
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    session_id = format_session_dir_name(
                        "generating", session_uuid, timestamp=timestamp
                    )
                    session_dir = os_mod.path.join(ws, session_id)
                    v_dir = os_mod.path.join(session_dir, "v1")
                    os_mod.makedirs(v_dir, exist_ok=True)

                    session_data = {
                        "id": session_uuid,
                        "folder_name": session_id,
                        "title": "Google Tasks Job",
                        "history": [{"role": "user", "text": prompt}],
                        "versions": 0,
                        "latest_stl": "",
                        "last_updated": datetime.datetime.now().isoformat(),
                    }
                    save_session_fn(session_id, session_data)

                    base_name = f"design_v1_{timestamp}"
                    scad_file = os_mod.path.join(v_dir, f"{base_name}.scad")
                    stl_file = os_mod.path.join(v_dir, f"{base_name}.stl")

                    try:
                        gen_sig = inspect.signature(generate_fn)
                        if "output_dir" in gen_sig.parameters:
                            res = generate_fn(
                                prompt,
                                [],
                                MAX_AI_CALLS,
                                "google_tasks",
                                enable_supports=get_supports_fn(),
                                output_dir=v_dir,
                                file_basename=base_name,
                            )
                        else:
                            res = generate_fn(prompt, [], MAX_AI_CALLS, "google_tasks")
                        code, filename, colors, warnings = res[:4]
                        prebuilt_stl = res[4] if len(res) > 4 else None
                        if warnings:
                            warn_str = (
                                ", ".join(warnings) if isinstance(warnings, list) else str(warnings)
                            )
                            print(f"Constraints Failed: {warn_str}. Marking task [FAILED].")
                            record_task_failure(
                                service,
                                task_id,
                                title,
                                notes,
                                f"Physical constraints failed: {warn_str}",
                            )
                            continue

                        renamed_folder = format_session_dir_name(
                            filename or "model", session_uuid, timestamp=timestamp
                        )
                        renamed_dir = os_mod.path.join(ws, renamed_folder)
                        if renamed_dir != session_dir:
                            try:
                                if os_mod.path.exists(session_dir) and not os_mod.path.exists(
                                    renamed_dir
                                ):
                                    try:
                                        os_mod.replace(session_dir, renamed_dir)
                                    except OSError:
                                        shutil.move(session_dir, renamed_dir)
                                if prebuilt_stl and session_dir in prebuilt_stl:
                                    prebuilt_stl = prebuilt_stl.replace(session_dir, renamed_dir)
                                session_id = renamed_folder
                                session_dir = renamed_dir
                                v_dir = os_mod.path.join(session_dir, "v1")
                                os_mod.makedirs(v_dir, exist_ok=True)
                                scad_file = os_mod.path.join(v_dir, f"{base_name}.scad")
                                stl_file = os_mod.path.join(v_dir, f"{base_name}.stl")
                            except Exception:
                                pass

                        session_data["folder_name"] = session_id
                        session_data["title"] = filename
                        session_data["history"].append({"role": "assistant", "code": code})
                        save_session_fn(session_id, session_data)
                    except Exception as e:
                        print(f"AI Error: {e}. Marking task [FAILED].")
                        record_task_failure(
                            service, task_id, title, notes, f"AI generation error: {e}"
                        )
                        continue

                    with open(scad_file, "w", encoding="utf-8") as f:
                        f.write(code)

                    if not (
                        prebuilt_stl and os_mod.path.exists(prebuilt_stl)
                    ) and not os_mod.path.exists(stl_file):
                        print("Compiling OpenSCAD to STL...")
                        try:
                            subprocess_mod.run(
                                [get_openscad_path_fn(), "-o", stl_file, scad_file],
                                check=True,
                                capture_output=True,
                                timeout=600,
                            )
                        except subprocess_mod.CalledProcessError as e:
                            err_output = (e.stderr or str(e)).strip()
                            print(f"OpenSCAD Error: {err_output}. Marking task [FAILED].")
                            record_task_failure(
                                service,
                                task_id,
                                title,
                                notes,
                                f"OpenSCAD compilation error: {err_output}",
                            )
                            continue

                    try:
                        write_version_print_scripts(
                            v_dir,
                            stl_file,
                            requested_colors=colors,
                            enable_supports=bool(get_supports_fn()),
                        )
                    except Exception:
                        pass

                    session_data["versions"] = 1
                    session_data["latest_stl"] = (
                        f"/outputs/sessions/{session_id}/v1/{base_name}.stl"
                    )
                    save_session_fn(session_id, session_data)

                is_busy = False
                busy_msg = ""
                try:
                    load_keys_fn = getattr(d, "load_api_keys", load_api_keys)
                    bambu_cls = _dispatch("BambuClient", getattr(d, "BambuClient", BambuClient))
                    keys = load_keys_fn()
                    b_client = bambu_cls(
                        keys.get("BAMBU_USERNAME", ""),
                        keys.get("BAMBU_PASSWORD", ""),
                        keys.get("BAMBU_DEVICE_SERIAL", ""),
                        ip=keys.get("BAMBU_PRINTER_IP", ""),
                        access_code=keys.get("BAMBU_ACCESS_CODE", ""),
                    )
                    b_client.connect_mqtt()
                    res_busy = b_client.is_printer_busy()
                    if isinstance(res_busy, (tuple, list)) and len(res_busy) >= 2:
                        is_busy, busy_msg = bool(res_busy[0]), str(res_busy[1])
                    elif isinstance(res_busy, bool):
                        is_busy = res_busy
                    b_client.disconnect_mqtt()
                except Exception as e:
                    print(f"Warning: Could not check printer status: {e}")

                if is_busy:
                    print(
                        f"Warning: Auto-print skipped because printer is busy: {busy_msg}. "
                        f"Design saved in session {session_id}."
                    )
                    service.tasks().patch(
                        tasklist="@default", task=task_id, body={"status": "completed"}
                    ).execute()
                    continue

                print("Sending to Bambu Printer script...")
                bambu_script = os_mod.path.join(ROOT_DIR, "src", "cli", "manual_print.py")
                colors_csv = ",".join(colors) if colors else ""

                try:
                    cmd = [sys.executable, bambu_script, stl_file, colors_csv]
                    if get_supports_fn():
                        cmd.append("--support")
                    subprocess_mod.run(cmd, check=True, timeout=600)

                    print("Print job successfully queued! Marking task as complete.")
                    service.tasks().patch(
                        tasklist="@default", task=task_id, body={"status": "completed"}
                    ).execute()
                except subprocess_mod.TimeoutExpired:
                    print("Printing timed out after 600s. Marking task [FAILED].")
                    record_task_failure(
                        service,
                        task_id,
                        title,
                        notes,
                        "Printing timed out after 600 seconds.",
                    )
                except subprocess_mod.CalledProcessError as e:
                    print(f"Printing failed (Exit Code {e.returncode}). Marking task [FAILED].")
                    record_task_failure(
                        service,
                        task_id,
                        title,
                        notes,
                        f"Printing script exited with code {e.returncode}.",
                    )
                except Exception as e:
                    print(f"Error during printing: {e}. Marking task [FAILED].")
                    record_task_failure(service, task_id, title, notes, f"Printing failed: {e}")

    except Exception as e:
        print(f"An error occurred reading tasks: {e}")


def main():
    print("Authenticating with Google Tasks...")
    creds = authenticate_google_tasks()
    service = build("tasks", "v1", credentials=creds)
    print("Authentication successful! Bot is autonomously checking for 3D Print tasks...")

    while True:
        process_tasks(service)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

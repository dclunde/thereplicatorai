import datetime
import json
import logging
import os
import re
import shutil
import time
import uuid

from src.core.config import (
    CONFIGS_DIR,
    SESSIONS_DIR,
    SNAPSHOTS_DIR,
    TMP_DIR,
)

logger = logging.getLogger(__name__)


def write_version_print_scripts(
    v_dir: str,
    stl_path: str,
    requested_colors: list[str] | None = None,
    enable_supports: bool = False,
) -> tuple[str, str]:
    """Deprecated: Batch scripts are no longer generated per-version. Printing runs via Python directly."""
    for bat_name in ("print.bat", "slice.bat"):
        p = os.path.join(v_dir, bat_name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return "", ""


def sanitize_folder_name_part(name: str) -> str:
    """Sanitizes a model name for use inside session folder names."""
    if not name:
        return "model"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:50] or "model"


def format_session_dir_name(model_name: str, session_id: str, timestamp: str | None = None) -> str:
    """Formats directory name as <YYYYMMDD_HHMMSS>_<model_name>_<short8uuid>."""
    ts = timestamp or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_model = sanitize_folder_name_part(model_name)
    short8 = session_id.replace("-", "")[:8].lower()
    return f"{ts}_{clean_model}_{short8}"


def resolve_session_dir(session_id: str, sessions_dir: str | None = None) -> str | None:
    """Resolves the on-disk directory path for a session ID.
    Supports:
    1. Absolute path to an existing directory
    2. Direct match in sessions_dir (exact folder name)
    3. Hybrid folder matching suffix _{short8uuid} or _{session_id}
    4. Suffix match without hyphens (e.g. _{session_id.replace('-', '')[:8]})
    5. session.json inspect matching data["id"] == session_id
    """
    if not session_id:
        return None

    root_sessions = sessions_dir or SESSIONS_DIR

    # 1. Absolute directory
    if os.path.isabs(session_id) and os.path.isdir(session_id):
        return os.path.abspath(session_id)

    if not os.path.isdir(root_sessions):
        return None

    # 2. Direct folder match in root_sessions
    direct = os.path.join(root_sessions, session_id)
    if os.path.isdir(direct):
        return direct

    # 3. Look for suffix match (O(1) style lookup by directory name pattern)
    short_uuid = session_id.replace("-", "")[:8].lower()
    short_raw = session_id[:8].lower() if len(session_id) >= 8 else session_id.lower()
    full_clean = session_id.replace("-", "").lower()

    suffixes = {f"_{short_uuid}", f"_{short_raw}", f"_{session_id.lower()}", f"_{full_clean}"}

    try:
        items = os.listdir(root_sessions)
    except Exception:
        return None

    for item in items:
        if item.startswith("."):
            continue
        item_path = os.path.join(root_sessions, item)
        if not os.path.isdir(item_path):
            continue
        item_lower = item.lower()
        for sfx in suffixes:
            if item_lower.endswith(sfx):
                return item_path

    # Fallback: inspect session.json if not found by suffix
    for item in items:
        if item.startswith("."):
            continue
        item_path = os.path.join(root_sessions, item)
        if not os.path.isdir(item_path):
            continue
        s_file = os.path.join(item_path, "session.json")
        if os.path.exists(s_file):
            try:
                with open(s_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                    if s_data.get("id") == session_id:
                        return item_path
            except Exception:
                pass

    return None


def rename_session_folder(
    session_id: str, new_model_name: str, sessions_dir: str | None = None
) -> str | None:
    """Renames a session's directory to <timestamp>_<sanitized_new_model_name>_<short8uuid>,
    preserving the original timestamp and short UUID. Updates references in session.json.
    """
    root_sessions = sessions_dir or SESSIONS_DIR
    curr_dir = resolve_session_dir(session_id, sessions_dir=root_sessions)
    if not curr_dir or not os.path.isdir(curr_dir):
        return None

    curr_name = os.path.basename(curr_dir)
    # Pattern: <YYYYMMDD_HHMMSS>_<middle>_<short8uuid>
    m = re.match(r"^(\d{8}_\d{6})_(.*)_([a-fA-F0-9]{8})$", curr_name)
    if m:
        ts = m.group(1)
        short_uuid = m.group(3)
    else:
        ts_match = re.search(r"(\d{8}_\d{6})", curr_name)
        ts = ts_match.group(1) if ts_match else datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = session_id.replace("-", "")[:8].lower()

    clean_name = sanitize_folder_name_part(new_model_name)
    new_name = f"{ts}_{clean_name}_{short_uuid}"

    if new_name == curr_name:
        return curr_dir

    new_dir = os.path.join(root_sessions, new_name)
    if os.path.exists(new_dir) and new_dir != curr_dir:
        logger.warning(f"Target directory {new_dir} already exists. Skipping rename.")
        return curr_dir

    try:
        os.replace(curr_dir, new_dir)
    except OSError:
        try:
            shutil.move(curr_dir, new_dir)
        except Exception as e:
            logger.error(f"Failed to rename session directory from {curr_dir} to {new_dir}: {e}")
            return curr_dir

    # Update session.json references if present
    s_file = os.path.join(new_dir, "session.json")
    if os.path.exists(s_file):
        try:
            with open(s_file, "r", encoding="utf-8") as f:
                s_data = json.load(f)
            s_data["folder_name"] = new_name
            if "latest_stl" in s_data and curr_name in s_data["latest_stl"]:
                s_data["latest_stl"] = s_data["latest_stl"].replace(curr_name, new_name)
            save_session_atomic(new_dir, s_data, sessions_dir=root_sessions)
        except Exception as e:
            logger.warning(f"Failed to update session.json after rename: {e}")

    return new_dir


def save_session_atomic(
    session_dir_or_id: str, session_data: dict, sessions_dir: str | None = None
) -> str:
    """Saves session_data into session.json atomically via a temporary file and os.replace."""
    root_sessions = sessions_dir or SESSIONS_DIR
    if os.path.isabs(session_dir_or_id) or os.sep in session_dir_or_id or "/" in session_dir_or_id:
        target_dir = session_dir_or_id
    else:
        resolved = resolve_session_dir(session_dir_or_id, sessions_dir=root_sessions)
        target_dir = resolved or os.path.join(root_sessions, session_dir_or_id)

    os.makedirs(target_dir, exist_ok=True)
    session_data.setdefault("id", session_data.get("id") or os.path.basename(target_dir))

    temp_filename = f".session.{uuid.uuid4().hex}.tmp"
    temp_path = os.path.join(target_dir, temp_filename)
    target_path = os.path.join(target_dir, "session.json")

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=4)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_path, target_path)
    return target_path


def load_session(session_dir_or_id: str, sessions_dir: str | None = None) -> dict | None:
    """Loads session.json from either a full session directory path or a session ID."""
    root_sessions = sessions_dir or SESSIONS_DIR

    if os.path.isdir(session_dir_or_id):
        target_dir = session_dir_or_id
    else:
        resolved = resolve_session_dir(session_dir_or_id, sessions_dir=root_sessions)
        target_dir = resolved or os.path.join(root_sessions, session_dir_or_id)

    target_file = os.path.join(target_dir, "session.json")
    if not os.path.exists(target_file):
        return None

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("id", os.path.basename(target_dir))
            return data
    except Exception as e:
        logger.warning(f"Failed to read session from {target_file}: {e}")
        return None


def list_sessions(sessions_dir: str | None = None) -> list[dict]:
    """Lists all valid sessions in sessions_dir, sorted by last_updated descending."""
    root_sessions = sessions_dir or SESSIONS_DIR
    if not os.path.exists(root_sessions):
        return []

    sessions = []
    for item in os.listdir(root_sessions):
        full_path = os.path.join(root_sessions, item)
        if os.path.isdir(full_path):
            data = load_session(full_path, sessions_dir=root_sessions)
            if data:
                sessions.append(data)

    def _sort_key(s):
        return s.get("last_updated", "") or ""

    sessions.sort(key=_sort_key, reverse=True)
    return sessions


def get_session(session_id: str, sessions_dir: str | None = None) -> dict | None:
    """Finds a session by ID or directory path."""
    root_sessions = sessions_dir or SESSIONS_DIR
    direct = load_session(session_id, sessions_dir=root_sessions)
    if direct and direct.get("id") == session_id:
        return direct

    resolved = resolve_session_dir(session_id, sessions_dir=root_sessions)
    if resolved:
        loaded = load_session(resolved, sessions_dir=root_sessions)
        if loaded:
            return loaded

    for s in list_sessions(root_sessions):
        if s.get("id") == session_id:
            return s
    return None


def delete_session(session_id: str, sessions_dir: str | None = None) -> bool:
    """Deletes all session directories matching session_id permanently."""
    root_sessions = sessions_dir or SESSIONS_DIR
    if not os.path.isdir(root_sessions):
        return False

    deleted_any = False
    short_uuid = session_id.replace("-", "")[:8].lower()
    short_raw = session_id[:8].lower() if len(session_id) >= 8 else session_id.lower()
    full_clean = session_id.replace("-", "").lower()
    suffixes = {f"_{short_uuid}", f"_{short_raw}", f"_{session_id.lower()}", f"_{full_clean}"}

    targets = set()
    direct = os.path.join(root_sessions, session_id)
    if os.path.isdir(direct):
        targets.add(direct)

    try:
        for item in os.listdir(root_sessions):
            item_path = os.path.join(root_sessions, item)
            if not os.path.isdir(item_path):
                continue
            item_lower = item.lower()
            if any(item_lower.endswith(sfx) for sfx in suffixes):
                targets.add(item_path)
            else:
                s_file = os.path.join(item_path, "session.json")
                if os.path.exists(s_file):
                    try:
                        with open(s_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if data.get("id") == session_id:
                                targets.add(item_path)
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Error scanning directories in delete_session: {e}")

    for target in targets:
        try:
            shutil.rmtree(target)
            deleted_any = True
        except Exception as e:
            logger.error(f"Failed to remove session directory {target}: {e}")

    return deleted_any


def parse_and_strip_support_flags(text: str) -> tuple[str, bool | None]:
    """Parses --support or --no-support flags from input text, returning cleaned text and flag value."""
    if not text:
        return "", None

    enable_supports = None
    if "--support" in text:
        enable_supports = True
        text = text.replace("--support", "").strip()
    elif "--no-support" in text:
        enable_supports = False
        text = text.replace("--no-support", "").strip()

    return text, enable_supports


def purge_heavy_assets(
    session_dir_or_id: str,
    session_data: dict | None = None,
    sessions_dir: str | None = None,
) -> None:
    """Purges large generated 3D files (.stl, .3mf, .gcode, .jpg, .png, .webp, .xml, .log, help.txt)
    from a session folder, while preserving all .scad and session metadata files.
    """
    root_sessions = sessions_dir or SESSIONS_DIR
    if not session_dir_or_id:
        return

    if os.path.isabs(session_dir_or_id) or os.sep in session_dir_or_id or "/" in session_dir_or_id:
        session_dir = session_dir_or_id
    else:
        resolved = resolve_session_dir(session_dir_or_id, sessions_dir=root_sessions)
        session_dir = resolved or os.path.join(root_sessions, session_dir_or_id)

    if os.path.exists(session_dir):
        heavy_exts = {
            ".stl",
            ".3mf",
            ".gcode",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".xml",
            ".log",
        }
        for root, _, files in os.walk(session_dir):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in heavy_exts or file.lower() == "help.txt":
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.warning(f"Could not remove asset {file_path}: {e}")

    if session_data is not None:
        session_data["purged"] = True


# --------------------------------------------------------------------------
# Configurable 3-Tier Retention & Promotion Logic
# --------------------------------------------------------------------------


def load_retention_config(configs_dir: str | None = None) -> dict:
    """Reads session_retention from inputs/configs/agent_config.json."""
    cfg_dir = configs_dir or CONFIGS_DIR
    config_file = os.path.join(cfg_dir, "agent_config.json")
    defaults = {
        "auto_purge_enabled": True,
        "keep_all_versions_sessions": 15,
        "keep_latest_version_sessions": 30,
    }
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                retention = data.get("session_retention", {})
                defaults.update(retention)
        except Exception as e:
            logger.warning(f"Failed to read session_retention from {config_file}: {e}")
    return defaults


def purge_version_heavy_assets(v_dir: str) -> list[str]:
    """Removes heavy binary files inside a single version directory while keeping .scad untouched."""
    purged = []
    if not os.path.isdir(v_dir):
        return purged

    heavy_exts = {
        ".stl",
        ".3mf",
        ".gcode",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".xml",
        ".log",
    }
    for root, _, files in os.walk(v_dir):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in heavy_exts or file.lower() == "help.txt":
                f_path = os.path.join(root, file)
                try:
                    os.remove(f_path)
                    purged.append(f_path)
                except Exception as e:
                    logger.warning(f"Failed to remove asset {f_path}: {e}")
    return purged


def get_most_recent_version_num(session_dir: str, session_data: dict | None = None) -> int:
    """Determines the active / most recently rendered version number for a session.
    1. Returns session_data['last_active_version'] if that version dir currently contains an .stl file.
    2. Otherwise checks all v* directories for the newest .stl modification timestamp (st_mtime).
    3. Falls back to max(version_numbers) or 1.
    """
    if session_data is None and os.path.isdir(session_dir):
        s_file = os.path.join(session_dir, "session.json")
        if os.path.exists(s_file):
            try:
                with open(s_file, "r", encoding="utf-8") as f:
                    session_data = json.load(f)
            except Exception:
                session_data = {}

    if session_data and "last_active_version" in session_data:
        try:
            target_v = int(session_data["last_active_version"])
            v_path = os.path.join(session_dir, f"v{target_v}")
            if os.path.isdir(v_path):
                stl_files = [f for f in os.listdir(v_path) if f.lower().endswith(".stl")]
                if stl_files:
                    return target_v
        except (ValueError, TypeError):
            pass

    newest_v = None
    newest_mtime = -1.0
    all_versions = []

    if os.path.isdir(session_dir):
        for item in os.listdir(session_dir):
            if item.startswith("v") and item[1:].isdigit():
                v_num = int(item[1:])
                all_versions.append(v_num)
                v_dir = os.path.join(session_dir, item)
                if os.path.isdir(v_dir):
                    stl_files = [f for f in os.listdir(v_dir) if f.lower().endswith(".stl")]
                    for f in stl_files:
                        try:
                            mtime = os.path.getmtime(os.path.join(v_dir, f))
                            if mtime > newest_mtime:
                                newest_mtime = mtime
                                newest_v = v_num
                        except Exception:
                            pass

    if newest_v is not None:
        return newest_v
    if all_versions:
        return max(all_versions)
    return 1


def purge_session_except_active(session_dir: str, session_data: dict | None = None) -> dict:
    """Purges heavy assets from all versions except the active/most recent version."""
    if not os.path.isdir(session_dir):
        return {"keep_version": 1, "purged_files": []}

    keep_v = get_most_recent_version_num(session_dir, session_data)
    purged_files = []

    for item in os.listdir(session_dir):
        if item.startswith("v") and item[1:].isdigit():
            v_num = int(item[1:])
            if v_num != keep_v:
                v_path = os.path.join(session_dir, item)
                if os.path.isdir(v_path):
                    purged_files.extend(purge_version_heavy_assets(v_path))

    # Also clean loose heavy files in session root if any (legacy compatibility)
    heavy_exts = {
        ".stl",
        ".3mf",
        ".gcode",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".xml",
        ".log",
    }
    for f in os.listdir(session_dir):
        f_path = os.path.join(session_dir, f)
        if os.path.isfile(f_path):
            ext = os.path.splitext(f)[1].lower()
            if ext in heavy_exts or f.lower() == "help.txt":
                try:
                    os.remove(f_path)
                    purged_files.append(f_path)
                except Exception as e:
                    logger.warning(f"Could not remove root heavy file {f_path}: {e}")

    s_file = os.path.join(session_dir, "session.json")
    if session_data is not None:
        session_data["purged_partial"] = True
        save_session_atomic(session_dir, session_data)
    elif os.path.exists(s_file):
        data = load_session(session_dir)
        if data:
            data["purged_partial"] = True
            save_session_atomic(session_dir, data)

    return {"keep_version": keep_v, "purged_files": purged_files}


def purge_all_session_heavy_assets(session_dir: str, session_data: dict | None = None) -> dict:
    """Purges heavy assets from ALL versions, preserving session.json, and all .scad files."""
    if not os.path.isdir(session_dir):
        return {"purged_files": []}

    purged_files = []
    heavy_exts = {
        ".stl",
        ".3mf",
        ".gcode",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".xml",
        ".log",
    }
    for item in os.listdir(session_dir):
        item_path = os.path.join(session_dir, item)
        if os.path.isdir(item_path) and item.startswith("v") and item[1:].isdigit():
            purged_files.extend(purge_version_heavy_assets(item_path))
        elif os.path.isfile(item_path):
            ext = os.path.splitext(item)[1].lower()
            if ext in heavy_exts or item.lower() == "help.txt":
                try:
                    os.remove(item_path)
                    purged_files.append(item_path)
                except Exception as e:
                    logger.warning(f"Could not remove root heavy file {item_path}: {e}")

    s_file = os.path.join(session_dir, "session.json")
    if session_data is not None:
        session_data["purged"] = True
        save_session_atomic(session_dir, session_data)
    elif os.path.exists(s_file):
        data = load_session(session_dir)
        if data:
            data["purged"] = True
            save_session_atomic(session_dir, data)

    return {"purged_files": purged_files}


def enforce_storage_retention_policy(
    active_session_ids: set[str] | list[str] | None = None,
    sessions_dir: str | None = None,
    configs_dir: str | None = None,
    tmp_dir: str | None = None,
    snapshots_dir: str | None = None,
) -> dict:
    """Enforces the 3-tier session storage retention policy and sweeps stale tmp/snapshots.
    - Tier 1 (1 to keep_all_versions_sessions): Keeps all versions (.stl, .3mf, .scad, session.json).
    - Tier 2 (keep_all+1 to keep_latest_version_sessions): Keeps only active/newest version's heavy files.
    - Tier 3 (beyond keep_latest): Deletes heavy files from all versions, keeping .scad and session.json.
    - Stale tmp sweep: deletes job_* older than 1 hour.
    - Snapshot sweep: keeps latest 5 .jpg camera snapshots.
    """
    cfg = load_retention_config(configs_dir=configs_dir)
    if not cfg.get("auto_purge_enabled", True):
        return {"auto_purge_enabled": False}

    keep_all = int(cfg.get("keep_all_versions_sessions", 15))
    keep_latest = int(cfg.get("keep_latest_version_sessions", 30))

    root_sessions = sessions_dir or SESSIONS_DIR
    root_tmp = tmp_dir or TMP_DIR
    root_snapshots = snapshots_dir or SNAPSHOTS_DIR

    active_ids = set()
    if active_session_ids:
        for sid in active_session_ids:
            s_str = str(sid).strip()
            active_ids.add(s_str)
            active_ids.add(s_str.replace("-", "")[:8].lower())
            active_ids.add(s_str.lower())

    session_entries = []
    if os.path.exists(root_sessions):
        for item in os.listdir(root_sessions):
            if item.startswith("."):
                continue
            item_path = os.path.join(root_sessions, item)
            if not os.path.isdir(item_path):
                continue

            session_data = load_session(item_path, sessions_dir=root_sessions) or {}
            sid = session_data.get("id") or item
            short_id = sid.replace("-", "")[:8].lower()

            # Skip active sessions
            if (
                sid in active_ids
                or item in active_ids
                or short_id in active_ids
                or item.lower() in active_ids
            ):
                continue

            # Determine sorting timestamp
            sort_ts = session_data.get("last_updated")
            if not sort_ts:
                m = re.match(r"^(\d{8}_\d{6})", item)
                if m:
                    try:
                        sort_ts = datetime.datetime.strptime(
                            m.group(1), "%Y%m%d_%H%M%S"
                        ).isoformat()
                    except Exception:
                        pass
            if not sort_ts:
                try:
                    sort_ts = datetime.datetime.fromtimestamp(
                        os.path.getmtime(item_path)
                    ).isoformat()
                except Exception:
                    sort_ts = ""

            session_entries.append((sort_ts, item_path, session_data))

    # Sort descending by last_updated (newest activity first)
    session_entries.sort(key=lambda x: x[0] or "", reverse=True)

    tier1_count = 0
    tier2_count = 0
    tier3_count = 0

    for idx, (_, s_dir, s_data) in enumerate(session_entries):
        if idx < keep_all:
            tier1_count += 1
        elif idx < keep_latest:
            purge_session_except_active(s_dir, s_data)
            tier2_count += 1
        else:
            purge_all_session_heavy_assets(s_dir, s_data)
            tier3_count += 1

    # Stale temp sweep (job_*, slice_*, ttp_validate_* older than 1 hour = 3600s)
    swept_tmp = []
    if os.path.exists(root_tmp):
        now = time.time()
        for item in os.listdir(root_tmp):
            if item.startswith(("job_", "slice_", "ttp_validate_")):
                item_path = os.path.join(root_tmp, item)
                try:
                    if (now - os.path.getmtime(item_path)) > 3600:
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                        swept_tmp.append(item)
                except Exception as e:
                    logger.warning(f"Error cleaning stale tmp {item_path}: {e}")

    # Camera snapshot sweep (keep latest 5 .jpg)
    swept_snapshots = []
    if os.path.exists(root_snapshots):
        jpgs = []
        for f in os.listdir(root_snapshots):
            if f.lower().endswith(".jpg"):
                f_path = os.path.join(root_snapshots, f)
                try:
                    jpgs.append((os.path.getmtime(f_path), f_path))
                except Exception:
                    pass
        jpgs.sort(key=lambda x: x[0], reverse=True)
        for _, f_path in jpgs[5:]:
            try:
                os.remove(f_path)
                swept_snapshots.append(f_path)
            except Exception as e:
                logger.warning(f"Error removing old snapshot {f_path}: {e}")

    return {
        "tier1_kept_all": tier1_count,
        "tier2_kept_active": tier2_count,
        "tier3_purged_all": tier3_count,
        "swept_tmp": swept_tmp,
        "swept_snapshots": swept_snapshots,
    }


class SessionManager:
    """SessionManager encapsulates timestamp-first session naming and 3-tier storage retention."""

    resolve_session_dir = staticmethod(resolve_session_dir)
    format_session_dir_name = staticmethod(format_session_dir_name)
    sanitize_folder_name_part = staticmethod(sanitize_folder_name_part)
    rename_session_folder = staticmethod(rename_session_folder)
    save_session = staticmethod(save_session_atomic)
    load_session = staticmethod(load_session)
    list_sessions = staticmethod(list_sessions)
    get_session = staticmethod(get_session)
    delete_session = staticmethod(delete_session)
    purge_heavy_assets = staticmethod(purge_heavy_assets)
    load_retention_config = staticmethod(load_retention_config)
    purge_version_heavy_assets = staticmethod(purge_version_heavy_assets)
    get_most_recent_version_num = staticmethod(get_most_recent_version_num)
    purge_session_except_active = staticmethod(purge_session_except_active)
    purge_all_session_heavy_assets = staticmethod(purge_all_session_heavy_assets)
    enforce_storage_retention_policy = staticmethod(enforce_storage_retention_policy)
    write_version_print_scripts = staticmethod(write_version_print_scripts)

import os
import subprocess
import traceback
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.core.config import SNAPSHOTS_DIR
from src.core.sessions import resolve_session_dir, save_session_atomic
from src.web.backend.schemas import PrintRequest

router = APIRouter(tags=["printer"])

_bambu_cloud = None


def set_bambu_cloud(client):
    global _bambu_cloud, bambu_cloud
    _bambu_cloud = client
    bambu_cloud = client


def get_bambu_cloud():
    global _bambu_cloud
    import sys

    for mod_name in ("src.web.backend.routes.printer", "src.web.backend.app"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "bambu_cloud") and mod.bambu_cloud is not None:
            return mod.bambu_cloud
    if _bambu_cloud is None:
        from src.core.config import load_env_keys
        from src.core.printer import BambuCloud

        keys = load_env_keys()
        _bambu_cloud = BambuCloud(
            keys.get("BAMBU_USERNAME", ""),
            keys.get("BAMBU_PASSWORD", ""),
            keys.get("BAMBU_DEVICE_SERIAL", ""),
            keys.get("BAMBU_PRINTER_IP", ""),
            keys.get("BAMBU_ACCESS_CODE", ""),
        )
    return _bambu_cloud


bambu_cloud = get_bambu_cloud()


def _get_subprocess():
    import sys

    mod = sys.modules.get("src.web.backend.routes.printer")
    if mod and hasattr(mod, "subprocess"):
        return getattr(mod, "subprocess")
    return subprocess


@router.get("/status")
def get_printer_status():
    bambu_cloud = get_bambu_cloud()
    raw = bambu_cloud.get_status()
    is_conn = bambu_cloud.is_connected()
    has_creds = bool(
        getattr(bambu_cloud, "device_serial", None)
        and (
            getattr(bambu_cloud, "auth_token", None)
            or getattr(bambu_cloud, "access_code", None)
            or (getattr(bambu_cloud, "username", None) and getattr(bambu_cloud, "password", None))
        )
    )
    formatted = bambu_cloud.get_formatted_status()
    if not has_creds:
        formatted = "Printer: Not configured in .env (Design & Download mode active)."
    return {
        "connected": is_conn,
        "configured": has_creds,
        "status": raw,
        "state": raw.get("state") if raw else ("NOT_CONFIGURED" if not has_creds else None),
        "formatted": formatted,
    }


@router.get("/snapshot")
def get_printer_snapshot():
    bambu_cloud = get_bambu_cloud()
    snapshot_target = os.path.join(SNAPSHOTS_DIR, "backend_snapshot.jpg")
    img_path = bambu_cloud.get_snapshot(snapshot_target)
    if img_path and os.path.exists(img_path):
        return FileResponse(img_path, media_type="image/jpeg")
    raise HTTPException(status_code=500, detail="Failed to capture snapshot")


@router.get("/ams_colors")
@router.get("/ams")
def get_ams_colors():
    bambu_cloud = get_bambu_cloud()
    return bambu_cloud.get_available_colors()


@router.post("/sessions/{session_id}/print")
def print_session(session_id: str, req: Optional[PrintRequest] = None):

    from src.web.backend.routes.sessions import (
        _write_batch_scripts,
        get_workspace_dir,
        load_session,
        sanitize_id,
    )

    bambu_cloud = get_bambu_cloud()
    session_id = sanitize_id(session_id)
    session_data = load_session(session_id)

    ws_dir = get_workspace_dir()
    resolved = resolve_session_dir(session_id, sessions_dir=ws_dir)
    session_dir = resolved or os.path.join(ws_dir, session_id)
    version = req.version if req and req.version else session_data.get("versions", 1)
    v_dir = os.path.join(session_dir, f"v{version}")

    try:
        has_creds = bool(
            getattr(bambu_cloud, "device_serial", None)
            and (
                getattr(bambu_cloud, "auth_token", None)
                or getattr(bambu_cloud, "access_code", None)
                or (
                    getattr(bambu_cloud, "username", None)
                    and getattr(bambu_cloud, "password", None)
                )
            )
        )
        if not bambu_cloud.is_connected():
            if not has_creds:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Printer is not connected. Bambu printer credentials are not configured in .env. "
                        "Please configure BAMBU_DEVICE_SERIAL plus either BAMBU_PRINTER_IP/BAMBU_ACCESS_CODE (LAN) "
                        "or BAMBU_USERNAME/BAMBU_PASSWORD (Cloud) to enable direct 3D printing. "
                        "You can still download your STL/3MF models directly."
                    ),
                )
            raise HTTPException(
                status_code=400,
                detail="Printer is not connected. Please check power and network connection.",
            )

        busy, busy_msg = False, ""
        try:
            res = bambu_cloud.is_printer_busy()
            if isinstance(res, (tuple, list)) and len(res) >= 2:
                busy, busy_msg = res[0], res[1]
            elif isinstance(res, bool):
                busy = res
        except (ValueError, TypeError):
            busy, busy_msg = False, ""

        if busy:
            raise HTTPException(
                status_code=409,
                detail=f"Printer is currently busy: {busy_msg}",
            )

        print_bat_path = os.path.join(v_dir, "print.bat")
        if not os.path.exists(print_bat_path):
            stl_files = (
                [f for f in os.listdir(v_dir) if f.lower().endswith(".stl")]
                if os.path.exists(v_dir)
                else []
            )
            if not stl_files:
                raise HTTPException(status_code=400, detail="Print script missing")
            base_name = os.path.splitext(stl_files[0])[0]
            enable_supp = (
                req.enable_supports
                if req and req.enable_supports is not None
                else session_data.get("enable_supports", False)
            )
            _write_batch_scripts(v_dir, base_name, enable_supports=bool(enable_supp))

        if req:
            with open(print_bat_path, "r", encoding="utf-8") as f:
                bat_content = f.read()
            lines = bat_content.splitlines()
            for i, line in enumerate(lines):
                stripped = line.strip()
                if (
                    stripped.startswith("python ")
                    or "manual_print" in stripped
                    or "bambu_printer.py" in stripped
                    or (stripped.startswith('"') and ".stl" in stripped)
                ):
                    import re

                    line = re.sub(r" --slot -?\d+", "", line)
                    if req.ams_slot is not None:
                        line = line + f" --slot {req.ams_slot}"

                    if req.enable_supports is not None:
                        line = re.sub(r" --(no-)?support\b", "", line)
                        if req.enable_supports:
                            line = line + " --support"

                    lines[i] = line
            with open(print_bat_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            if req.enable_supports is not None:
                session_data["enable_supports"] = req.enable_supports
                save_session_atomic(session_dir, session_data)

        _get_subprocess().Popen(
            ["cmd.exe", "/c", "start", "Bambu P2S Print Job", "cmd.exe", "/c", "print.bat"],
            cwd=v_dir,
        )
        return {"status": "ok", "message": "Print job started"}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop_print")
@router.post("/printer/stop")
def stop_print():
    bambu_cloud = get_bambu_cloud()
    state = bambu_cloud.get_status().get("state", "UNKNOWN")
    if state not in ["RUNNING", "PREPARE", "PAUSE", "PAUSED"]:
        return {"status": "ignored", "message": "Printer was not printing. No action taken."}
    try:
        success = bambu_cloud.stop_print()
        if success:
            return {"status": "ok", "message": "Print stop command sent."}
        else:
            raise HTTPException(
                status_code=503,
                detail="MQTT client is not connected. Cannot send stop command.",
            )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

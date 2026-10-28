import datetime
import os
import re
import shutil
import subprocess
import sys
import uuid

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.core.activity_tracker import busy_activity
from src.core.config import (
    ROOT_DIR,
    SESSIONS_DIR,
    ensure_output_dirs,
    get_bambu_studio_path,
    get_openscad_path,
)
from src.core.slicer.openscad import format_dimensions_inches
from src.core.slicer.slicer_3mf import get_slice_info_from_3mf


def _beep() -> None:
    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(1000, 500)
        except Exception:
            pass


def _prompt_with_timeout(prompt_text: str, timeout_sec: int = 60, default: str = "N") -> str:
    """Prompts the user for Y/N with a 60s timeout on Windows using choice.exe or standard input fallback."""
    if sys.platform == "win32" and sys.stdin.isatty():
        try:
            res = subprocess.run(
                [
                    "choice",
                    "/C",
                    "YN",
                    "/T",
                    str(timeout_sec),
                    "/D",
                    default.upper(),
                    "/M",
                    prompt_text,
                ],
                check=False,
            )
            if res.returncode == 1:
                return "Y"
            return "N"
        except Exception:
            pass

    try:
        ans = input(f"{prompt_text} [y/N]: ").strip().upper()
        return "Y" if ans.startswith("Y") else "N"
    except (EOFError, KeyboardInterrupt):
        return default.upper()


def _sanitize_prefix(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", (raw or "model").strip()).strip("_").lower()
    return cleaned or "model"


def main(prompt_arg: str | None = None) -> int:
    ensure_output_dirs()
    print("Starting Simple Version (SCAD to STL Generator)...")

    if prompt_arg is not None:
        ai_prompt = prompt_arg.strip()
    else:
        try:
            ai_prompt = input("What do you want to make? ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0

    if not ai_prompt:
        print("No prompt provided.")
        return 1

    print()
    print("Creating OpenSCAD code with AI...")

    import src.core.ai.validation_loop as ai_call
    from src.core.sessions import save_session_atomic

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]

    with busy_activity("cli_generation", metadata={"prompt": ai_prompt}):
        try:
            max_calls = ai_call.get_max_ai_calls("cli")
            res = ai_call.generate_openscad_validated(
                ai_prompt,
                [],
                max_calls,
                "cli",
                keep_temp_artifacts=True,
            )
            code, filename, colors, warnings = res[:4]
            prebuilt_stl = res[4] if len(res) > 4 else None
            prebuilt_3mf = res[5] if len(res) > 5 else None
        except Exception as e:
            print(f"AI generation failed: {e}")
            return 1

        prefix = _sanitize_prefix(filename)
        session_id = f"{timestamp}_{prefix}_{short_uuid}"
        session_dir = os.path.join(SESSIONS_DIR, session_id)
        v_dir = os.path.join(session_dir, "v1")
        os.makedirs(v_dir, exist_ok=True)

        base_name = f"{prefix}_{timestamp}"
        scad_file = os.path.join(v_dir, f"{base_name}.scad")
        stl_file = os.path.join(v_dir, f"{base_name}.stl")
        three_mf_file = os.path.join(v_dir, f"{base_name}.3mf")

        with open(scad_file, "w", encoding="utf-8") as f:
            f.write(code)

        if prebuilt_stl and os.path.exists(prebuilt_stl):
            shutil.copyfile(prebuilt_stl, stl_file)
            if prebuilt_3mf and os.path.exists(prebuilt_3mf):
                shutil.copyfile(prebuilt_3mf, three_mf_file)
            if hasattr(ai_call, "_cleanup_temp_stl"):
                ai_call._cleanup_temp_stl(prebuilt_stl)
        elif not os.path.exists(stl_file):
            print("Rendering STL with OpenSCAD...")
            try:
                openscad_exe = get_openscad_path()
                subprocess.run([openscad_exe, "-o", stl_file, scad_file], check=True, timeout=600)
            except Exception as e:
                print(f"ERROR: OpenSCAD compilation failed: {e}")
                return 1

        try:
            size_str = format_dimensions_inches(stl_file)
        except Exception:
            size_str = "Unknown"

        slice_info = {"grams": 0.0, "time": "Unknown", "has_supports": False}
        if os.path.exists(three_mf_file):
            try:
                slice_info = get_slice_info_from_3mf(three_mf_file)
            except Exception:
                pass

        session_data = {
            "id": session_id,
            "title": filename or prefix,
            "history": [
                {"role": "user", "text": ai_prompt},
                {
                    "role": "assistant",
                    "code": code,
                    "size": size_str,
                    "time": slice_info.get("time", "Unknown"),
                    "grams": slice_info.get("grams", 0.0),
                    "has_supports": slice_info.get("has_supports", False),
                },
            ],
            "versions": 1,
            "latest_stl": f"/outputs/sessions/{session_id}/v1/{base_name}.stl",
            "last_updated": datetime.datetime.now().isoformat(),
        }
        save_session_atomic(session_dir, session_data)
    try:
        from src.core.sessions import enforce_storage_retention_policy

        enforce_storage_retention_policy(active_session_ids={session_id})
    except Exception:
        pass

    _beep()
    print()
    review_choice = _prompt_with_timeout(
        "Do you want to review the design before printing? (Auto-printing in 60s if No)",
        timeout_sec=60,
        default="N",
    )

    if review_choice == "Y":
        print("Opening STL in slicer for review...")
        try:
            slicer_exe = get_bambu_studio_path()
            subprocess.Popen([slicer_exe, stl_file])
        except Exception as e:
            print(f"Warning: Could not launch slicer GUI ({e}).")
        print()
        ready_choice = _prompt_with_timeout(
            "Are you good with this design and ready to print?",
            timeout_sec=3600,
            default="N",
        )
        if ready_choice != "Y":
            print("Print cancelled.")
            return 0

    print("Sending to Bambu Printer...")
    colors_csv = ",".join(colors) if colors else ""
    bambu_printer_script = os.path.join(ROOT_DIR, "src", "cli", "manual_print.py")
    cmd = [sys.executable, bambu_printer_script, stl_file, colors_csv]
    proc = subprocess.run(cmd, cwd=ROOT_DIR)
    if proc.returncode != 0:
        print("Failed to send print job.")
        return proc.returncode

    print("Print started successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

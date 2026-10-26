"""Hardware and environment diagnostics for Text-to-Printer.

Usage:
    uv run python scripts/diagnostics.py --all
    uv run python scripts/diagnostics.py --mqtt
    uv run python scripts/diagnostics.py --ams
    uv run python scripts/diagnostics.py --light
    uv run python scripts/diagnostics.py --snapshot
"""

import argparse
import os
import sys
import time

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.config import (
    SNAPSHOTS_DIR,
    get_bambu_studio_path,
    get_openscad_path,
    get_prusa_slicer_path,
    load_api_keys,
)
from src.core.printer import BambuCloud


def check_environment():
    print("=== Environment Diagnostics ===")
    keys = load_api_keys()
    required_keys = [
        "BAMBU_USERNAME",
        "BAMBU_DEVICE_SERIAL",
        "BAMBU_PRINTER_IP",
        "BAMBU_ACCESS_CODE",
    ]
    missing = [k for k in required_keys if not keys.get(k)]
    if missing:
        print(f"⚠️ Missing recommended keys in .env: {', '.join(missing)}")
    else:
        print("✅ Core Bambu credentials loaded.")

    try:
        scad_path = get_openscad_path()
        print(f"✅ OpenSCAD found at: {scad_path}")
    except Exception as e:
        print(f"❌ OpenSCAD not detected: {e}")

    prusa = get_prusa_slicer_path()
    if prusa:
        print(f"✅ PrusaSlicer found at: {prusa}")
    else:
        print("ℹ️ PrusaSlicer not found.")

    bambu = get_bambu_studio_path()
    if bambu:
        print(f"✅ BambuStudio/OrcaSlicer found at: {bambu}")
    else:
        print("ℹ️ BambuStudio/OrcaSlicer not found.")
    print()


def get_client() -> BambuCloud:
    keys = load_api_keys()
    return BambuCloud(
        keys.get("BAMBU_USERNAME", ""),
        keys.get("BAMBU_PASSWORD", ""),
        keys.get("BAMBU_DEVICE_SERIAL", ""),
        ip=keys.get("BAMBU_PRINTER_IP", ""),
        access_code=keys.get("BAMBU_ACCESS_CODE", ""),
    )


def test_mqtt(client: BambuCloud = None):
    print("=== MQTT Status Diagnostic ===")
    bc = client or get_client()
    try:
        bc.connect_mqtt()
        time.sleep(2)
        if bc.is_connected():
            print("✅ MQTT Connected!")
            print(f"Printer Status:\n{bc.get_formatted_status()}")
        else:
            print("❌ Failed to connect to MQTT broker.")
    except Exception as e:
        print(f"❌ MQTT error: {e}")
    finally:
        if not client:
            bc.disconnect_mqtt()
    print()


def test_ams(client: BambuCloud = None):
    print("=== AMS Diagnostic ===")
    bc = client or get_client()
    try:
        bc.connect_mqtt()
        time.sleep(2)
        colors = bc.get_available_colors()
        if not colors:
            print("ℹ️ No AMS data received from printer.")
        else:
            print(f"Found {len(colors)} slot(s):")
            for slot, data in colors.items():
                slot_name = "External Spool" if slot == 4 else f"Slot {slot}"
                color_hex = data.get("color", "Unknown")
                ftype = data.get("type", "Unknown")
                remain = data.get("remain", -1)
                remain_str = f"{remain}%" if remain >= 0 else "Unknown"
                print(f"  {slot_name}: {ftype} | #{color_hex} | Remaining: {remain_str}")
    except Exception as e:
        print(f"❌ AMS error: {e}")
    finally:
        if not client:
            bc.disconnect_mqtt()
    print()


def test_light(client: BambuCloud = None):
    print("=== Chamber Light Diagnostic ===")
    bc = client or get_client()
    try:
        bc.connect_mqtt()
        time.sleep(2)
        print("💡 Turning chamber light ON...")
        bc.turn_on_light()
        time.sleep(3)
        print("🌑 Turning chamber light OFF...")
        bc.turn_off_light()
        print("✅ Light toggle complete.")
    except Exception as e:
        print(f"❌ Light toggle error: {e}")
    finally:
        if not client:
            bc.disconnect_mqtt()
    print()


def test_snapshot(client: BambuCloud = None):
    print("=== Camera Snapshot Diagnostic ===")
    bc = client or get_client()
    try:
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        target = os.path.join(SNAPSHOTS_DIR, "diagnostics_snapshot.jpg")
        print("📸 Capturing frame...")
        path = bc.get_snapshot(target)
        if path and os.path.exists(path):
            print(f"✅ Snapshot captured successfully: {path}")
        else:
            print("❌ Snapshot capture failed.")
    except Exception as e:
        print(f"❌ Snapshot error: {e}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Text-to-Printer Diagnostics")
    parser.add_argument("--all", action="store_true", help="Run all diagnostics")
    parser.add_argument("--mqtt", action="store_true", help="Test MQTT status")
    parser.add_argument("--ams", action="store_true", help="Inspect AMS slots")
    parser.add_argument("--light", action="store_true", help="Test chamber light toggle")
    parser.add_argument("--snapshot", action="store_true", help="Capture camera snapshot")

    args = parser.parse_args()
    if not (args.all or args.mqtt or args.ams or args.light or args.snapshot):
        args.all = True

    check_environment()
    client = get_client()

    try:
        if args.all or args.mqtt:
            test_mqtt(client)
        if args.all or args.ams:
            test_ams(client)
        if args.all or args.light:
            test_light(client)
        if args.all or args.snapshot:
            test_snapshot(client)
    finally:
        try:
            client.disconnect_mqtt()
        except Exception:
            pass


if __name__ == "__main__":
    main()

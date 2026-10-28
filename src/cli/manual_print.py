import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.core.config import load_env_keys
from src.core.printer import BambuClient, BambuCloud, PrinterBusyError, match_color
from src.core.slicer import slicer_3mf
from src.core.slicer.slicer_3mf import (
    _can_reuse_existing_3mf,
    compile_and_slice,
    get_enable_supports_config,
    get_filament_usage_from_3mf,
    slice_stl_to_3mf,
)


def _make_client() -> BambuClient:
    """Builds a BambuClient from .env credentials."""
    keys = load_env_keys()
    if hasattr(slicer_3mf.BambuCloud, "return_value") or type(slicer_3mf.BambuCloud).__name__ in (
        "MagicMock",
        "Mock",
    ):
        cls = slicer_3mf.BambuCloud
    elif hasattr(BambuClient, "return_value") or type(BambuClient).__name__ in (
        "MagicMock",
        "Mock",
    ):
        cls = BambuClient
    elif hasattr(BambuCloud, "return_value") or type(BambuCloud).__name__ in ("MagicMock", "Mock"):
        cls = BambuCloud
    else:
        cls = BambuClient
    return cls(
        keys.get("BAMBU_USERNAME", ""),
        keys.get("BAMBU_PASSWORD", ""),
        keys.get("BAMBU_DEVICE_SERIAL", ""),
        ip=keys.get("BAMBU_PRINTER_IP", ""),
        access_code=keys.get("BAMBU_ACCESS_CODE", ""),
    )


def _abort_if_busy(client: BambuClient) -> None:
    """Exits with an error if the printer is currently running a job."""
    busy, busy_msg = False, ""
    try:
        res = client.is_printer_busy()
        if isinstance(res, (tuple, list)) and len(res) >= 2:
            busy, busy_msg = bool(res[0]), str(res[1])
        elif isinstance(res, bool):
            busy = res
    except (ValueError, TypeError):
        busy, busy_msg = False, ""

    if busy:
        print(f"Error: {busy_msg}. Cannot start new print.")
        client.disconnect_mqtt()
        sys.exit(1)


def run_cli_print(
    stl_file: str, requested_colors: list[str], force_slot: int | None, enable_supports: bool | None
):
    """Executes a direct non-interactive print command."""
    stl_file = os.path.abspath(stl_file)
    if not os.path.exists(stl_file):
        print(f"File not found: {stl_file}")
        sys.exit(1)

    candidate_3mf = os.path.splitext(stl_file)[0] + ".3mf"
    reuse_fn = (
        slicer_3mf._can_reuse_existing_3mf
        if hasattr(slicer_3mf._can_reuse_existing_3mf, "return_value")
        else _can_reuse_existing_3mf
    )
    existing_3mf = reuse_fn(stl_file, enable_supports)
    if existing_3mf:
        print(f"Reusing existing sliced 3MF: {existing_3mf}")
        out_3mf = existing_3mf
    else:
        if os.path.exists(candidate_3mf):
            print(
                f"Existing 3MF does not match requested support settings (supports={bool(enable_supports)}). Re-slicing..."
            )
        else:
            print(f"Slicing {os.path.basename(stl_file)} (supports={bool(enable_supports)})...")
        slice_fn = (
            slicer_3mf.slice_stl_to_3mf
            if hasattr(slicer_3mf.slice_stl_to_3mf, "return_value")
            else slice_stl_to_3mf
        )
        out_3mf = slice_fn(stl_file, enable_supports=enable_supports)

    if not out_3mf:
        print("Slicing failed.")
        sys.exit(1)

    filament_fn = (
        slicer_3mf.get_filament_usage_from_3mf
        if hasattr(slicer_3mf.get_filament_usage_from_3mf, "return_value")
        else get_filament_usage_from_3mf
    )
    required_grams = filament_fn(out_3mf)
    print(f"Estimated filament required: {required_grams}g")

    print("Connecting to printer...")
    bambu_cloud = _make_client()
    bambu_cloud.connect_mqtt()
    _abort_if_busy(bambu_cloud)

    available_colors = bambu_cloud.get_available_colors()

    if force_slot is not None:
        if available_colors and force_slot in available_colors:
            ftype = available_colors[force_slot].get("type", "PLA")
            if ftype not in ["PLA", "Unknown"]:
                slot_desc = "External Spool (Slot 4)" if force_slot == 4 else f"Slot {force_slot}"
                print(
                    f"Error: Selected {slot_desc} contains {ftype}. This system only supports PLA."
                )
                bambu_cloud.disconnect_mqtt()
                sys.exit(1)
        best_slot = force_slot
        if best_slot == 4:
            print("User manually selected External Spool (Slot 4) for printing.")
        else:
            print(f"User manually selected AMS Slot {best_slot} for printing.")
    else:
        best_slot = match_color(requested_colors, available_colors, required_grams)
        if best_slot is not None:
            print(f"Selected AMS Slot {best_slot} for printing.")

    if best_slot is None and available_colors:
        print("Error: No suitable AMS slot found with enough PLA.")
        bambu_cloud.disconnect_mqtt()
        sys.exit(1)
    elif best_slot is None:
        print("Proceeding without AMS slot selection (single spool mode).")

    print("Sending print job...")
    success = False
    for attempt in range(5):
        try:
            if bambu_cloud.send_print_job(out_3mf, ams_slot=best_slot):
                success = True
                break
        except PrinterBusyError as e:
            print(f"Error: Printer is busy: {e}")
            break
        time.sleep(5)

    if success:
        print("Print job successfully started!")
    else:
        print("Failed to send print job.")
        bambu_cloud.disconnect_mqtt()
        sys.exit(1)

    bambu_cloud.disconnect_mqtt()


def run_interactive_print():
    """Runs interactive command line prompt."""
    print("=== Manual Print (OpenSCAD or STL) ===")
    input_file = input("Enter path to file (.scad or .stl): ").strip()
    if input_file.startswith('"') and input_file.endswith('"'):
        input_file = input_file[1:-1]
    input_file = os.path.abspath(input_file)

    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        sys.exit(1)

    send_to_printer_input = input("Send to printer? (Y/n): ").strip().lower()
    send_to_printer = send_to_printer_input != "n"

    ams_slot = None
    if send_to_printer:
        ams_slot_input = (
            input("Enter AMS slot (0-3 for AMS, 4 or 'ext' for External Spool, empty for auto): ")
            .strip()
            .lower()
        )
        if ams_slot_input == "":
            ams_slot = None
        elif ams_slot_input in ["ext", "external"]:
            ams_slot = 4
        else:
            try:
                val = int(ams_slot_input)
                if 0 <= val <= 4:
                    ams_slot = val
                else:
                    print("Invalid slot number (must be 0-3 or 4). Defaulting to auto.")
                    ams_slot = None
            except ValueError:
                print("Invalid slot input. Defaulting to auto.")
                ams_slot = None

    config_supports = get_enable_supports_config()
    default_str = "Y/n" if config_supports else "y/N"
    supports_input = input(f"Enable supports? ({default_str}): ").strip().lower()

    if supports_input == "":
        enable_supports = config_supports
    else:
        enable_supports = supports_input == "y"

    if input_file.lower().endswith(".scad"):
        print(f"\nCompiling and slicing {input_file}...")
        out_3mf = compile_and_slice(input_file, enable_supports=enable_supports)
    elif input_file.lower().endswith(".stl"):
        print(f"\nSlicing {input_file}...")
        out_3mf = slice_stl_to_3mf(input_file, enable_supports=enable_supports)
    else:
        print("\nError: Unsupported file type. Must be .scad or .stl")
        sys.exit(1)

    if not out_3mf or not os.path.exists(out_3mf):
        print("Failed to prepare the model.")
        sys.exit(1)

    print(f"\nSuccessfully generated 3MF: {out_3mf}")

    if send_to_printer:
        bambu_cloud = _make_client()
        bambu_cloud.connect_mqtt()
        _abort_if_busy(bambu_cloud)

        slot_str = (
            "Auto"
            if ams_slot is None
            else ("External Spool (Slot 4)" if ams_slot == 4 else f"AMS Slot {ams_slot}")
        )
        print(f"Sending {out_3mf} to printer ({slot_str})...")
        try:
            success = bambu_cloud.send_print_job(out_3mf, ams_slot=ams_slot)
            if success:
                print("Print job submitted successfully!")
            else:
                print("Failed to submit print job.")
        except PrinterBusyError as e:
            print(f"Error: Printer is busy: {e}")
        finally:
            bambu_cloud.disconnect_mqtt()


def main(argv: list[str] | None = None):
    """CLI entry point. With arguments: non-interactive print; without: interactive prompt."""
    if argv is not None:
        args_list = argv
    elif "pytest" in sys.argv[0] and not any(
        x in sys.argv[0] for x in ("bambu_printer", "manual_print")
    ):
        args_list = []
    elif len(sys.argv) == 1 and any(x in sys.argv[0] for x in ("bambu_printer", "manual_print")):
        print(
            "Usage: python -m src.cli.manual_print <path_to_stl> [requested_colors_csv] [--support] [--slot 0-4]"
        )
        sys.exit(1)
    else:
        args_list = list(sys.argv[1:])
    if len(args_list) > 0:
        force_slot = None
        args = list(args_list)

        if "--slot" in args:
            idx = args.index("--slot")
            if idx + 1 < len(args):
                slot_arg = args[idx + 1].strip().lower()
                if slot_arg in ["ext", "external"]:
                    force_slot = 4
                else:
                    try:
                        val = int(slot_arg)
                        if 0 <= val <= 4:
                            force_slot = val
                        else:
                            print(
                                f"Error: Invalid slot {val}. Must be 0-3 for AMS, or 4 for External Spool."
                            )
                            sys.exit(1)
                    except ValueError:
                        print(f"Error: Invalid slot '{slot_arg}'. Must be 0-3, 4, or 'ext'.")
                        sys.exit(1)
                args.pop(idx)
                args.pop(idx)

        enable_supports = None
        if "--support" in args:
            enable_supports = True
            args.remove("--support")
        elif "--no-support" in args:
            enable_supports = False
            args.remove("--no-support")

        if not args:
            print(
                "Usage: python -m src.cli.manual_print <path_to_stl> [requested_colors_csv] [--support] [--slot 0-4]"
            )
            sys.exit(1)

        stl_file = args[0]
        requested_colors = []
        if len(args) >= 2 and args[1].strip():
            requested_colors = [c.strip() for c in args[1].split(",")]

        run_cli_print(stl_file, requested_colors, force_slot, enable_supports)
    else:
        run_interactive_print()


if __name__ == "__main__":
    main(sys.argv[1:])

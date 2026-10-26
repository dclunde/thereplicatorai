import asyncio
import os
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from src.core.config import SNAPSHOTS_DIR, load_api_keys
from src.core.printer import BambuCloud, PrinterBusyError, get_closest_color_name, match_color
from src.core.sessions import parse_and_strip_support_flags

WAITING_FOR_DECISION = 1
known_chat_ids = set()
_bambu_cloud = None


def log_server(msg):
    print(f"[Printer Handler] {msg}", flush=True)


async def _safe_reply(target, text: str, **kwargs):
    from src.bots.telegram.handlers_design import safe_reply_text

    return await safe_reply_text(target, text, **kwargs)


def _dispatch():
    import sys

    return sys.modules.get("src.bots.telegram.bot", sys.modules[__name__])


def get_bambu_cloud():
    global _bambu_cloud
    import sys

    for mod_name in ("src.bots.telegram.bot", "src.bots.telegram.handlers_printer"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "bambu_cloud") and mod.bambu_cloud is not None:
            return mod.bambu_cloud
    if _bambu_cloud is None:
        keys = load_api_keys()
        _bambu_cloud = BambuCloud(
            keys.get("BAMBU_USERNAME", ""),
            keys.get("BAMBU_PASSWORD", ""),
            keys.get("BAMBU_DEVICE_SERIAL", ""),
            ip=keys.get("BAMBU_PRINTER_IP", ""),
            access_code=keys.get("BAMBU_ACCESS_CODE", ""),
        )
    return _bambu_cloud


async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bambu_cloud = get_bambu_cloud()
    msg = bambu_cloud.get_formatted_status()
    design_state = context.user_data.get("design_state")

    if design_state:
        msg += f"\n\nSoftware Design State: {design_state}"

    await _safe_reply(update, msg)


async def request_snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bambu_cloud = get_bambu_cloud()
    has_creds = bool(getattr(bambu_cloud, "ip", None) and getattr(bambu_cloud, "access_code", None))
    if not has_creds and not os.environ.get("PYTEST_CURRENT_TEST"):
        await _safe_reply(
            update,
            "Bambu printer IP or Access Code is not configured in .env. Camera snapshot is unavailable.",
        )
        return
    await _safe_reply(update, "Fetching snapshot from printer camera...")

    try:
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        target_path = os.path.join(SNAPSHOTS_DIR, f"snapshot_{uuid.uuid4().hex[:8]}.jpg")
        img_path = await asyncio.to_thread(bambu_cloud.get_snapshot, target_path)

        if img_path and os.path.exists(img_path):
            try:
                with open(img_path, "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo, caption="Live Snapshot", read_timeout=60, write_timeout=60
                    )
            finally:
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except Exception:
                        pass
        else:
            await _safe_reply(update, "Failed to capture a frame from the camera stream.")
    except ImportError:
        await _safe_reply(update, "opencv-python (cv2) is not installed. Cannot fetch snapshot.")
    except Exception as e:
        await _safe_reply(update, f"Error fetching snapshot: {e}")


async def stop_print_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for confirmation before stopping the print job."""
    bambu_cloud = get_bambu_cloud()
    status = bambu_cloud.get_status()
    if not status:
        await _safe_reply(update, "Could not determine printer status.")
        return

    state = status.get("state", "UNKNOWN")
    if state not in ["RUNNING", "PREPARE", "PAUSE", "PAUSED"]:
        await _safe_reply(update, f"Printer is not currently printing. State: {state}")
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, stop the print", callback_data="confirm_stop_print"),
                InlineKeyboardButton("Cancel", callback_data="cancel_stop_print"),
            ]
        ]
    )
    if update.message:
        await update.message.reply_text(
            f"Printer is currently {state}.\nAre you sure you want to stop the print job?",
            reply_markup=keyboard,
        )


async def stop_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the inline keyboard response for /stop_print confirmation."""
    bambu_cloud = get_bambu_cloud()
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_stop_print":
        try:
            success = await asyncio.to_thread(bambu_cloud.stop_print)
            if success:
                await query.edit_message_text("Print stop command sent successfully.")
            else:
                await query.edit_message_text(
                    "Failed to send print stop command. MQTT may be disconnected."
                )
        except Exception as e:
            await query.edit_message_text(f"Error stopping print: {e}")
    else:
        await query.edit_message_text("Stop print cancelled.")


async def start_print(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from src.bots.telegram.handlers_design import slice_stl_to_3mf

    bambu_cloud = get_bambu_cloud()
    known_chat_ids.add(update.effective_chat.id)
    out_3mf = context.user_data.get("last_3mf_file")
    if not out_3mf:
        await _safe_reply(
            update,
            "No sliced model ready. Please finish generating a design first.",
        )
        return WAITING_FOR_DECISION

    user_input = " ".join(context.args) if context.args else ""
    cleaned_input, parsed_support = parse_and_strip_support_flags(user_input)
    available_colors = await asyncio.to_thread(bambu_cloud.get_available_colors)

    if not cleaned_input:
        msg = (
            "Please specify a slot: /print <slot> [--support]\n"
            "Example: /print 1 --support (or /print 4, /print ext, /print auto)\n\n"
        )
        if available_colors:
            msg += "Available slots:\n"
            for slot_num, data in available_colors.items():
                slot_title = "External Spool (Slot 4)" if slot_num == 4 else f"Slot {slot_num}"
                color_name = get_closest_color_name(data.get("color", "Unknown"))
                ftype = data.get("type", "PLA")
                warning = " (UNSUPPORTED)" if ftype not in ["PLA", "Unknown"] else ""
                remain_str = f" ({data.get('remain', 100)}%)" if data.get("remain", -1) >= 0 else ""
                msg += f"{slot_title}: {ftype} - {color_name}{remain_str}{warning}\n"
        await _safe_reply(update, msg)
        return WAITING_FOR_DECISION
    else:
        inp_lower = cleaned_input.strip().lower()
        if inp_lower == "auto":
            requested_colors = context.user_data.get("requested_colors", [])
            required_grams = context.user_data.get("required_grams", 0.0)
            if available_colors:
                matched_slot = match_color(requested_colors, available_colors, required_grams)
                if matched_slot is None:
                    await _safe_reply(
                        update,
                        "Error: No suitable AMS slot found with enough PLA for auto-print. ❌",
                    )
                    return WAITING_FOR_DECISION
                slot = matched_slot
            else:
                slot = None
        elif inp_lower in ["ext", "external"]:
            slot = 4
        else:
            try:
                slot_candidate = int(cleaned_input.strip())
                if slot_candidate < 0 or slot_candidate > 4:
                    await _safe_reply(
                        update,
                        "Slot must be 0-3 for AMS, 4 or 'ext' for External Spool, or 'auto'.",
                    )
                    return WAITING_FOR_DECISION
                slot = slot_candidate
            except ValueError:
                await _safe_reply(
                    update,
                    "Slot must be 0-3 for AMS, 4 or 'ext' for External Spool, or 'auto'.",
                )
                return WAITING_FOR_DECISION

        if available_colors and slot is not None and slot in available_colors:
            ftype = available_colors[slot].get("type", "PLA")
            if ftype not in ["PLA", "Unknown"]:
                slot_desc = "External Spool (Slot 4)" if slot == 4 else f"Slot {slot}"
                await _safe_reply(
                    update,
                    f"{slot_desc} contains {ftype}. This system only supports PLA.",
                )
                return WAITING_FOR_DECISION

    status = bambu_cloud.get_status()
    if status:
        state = status.get("state", "UNKNOWN")
        if state in ["RUNNING", "PREPARE", "PAUSE", "PAUSED"]:
            await _safe_reply(update, "Wait until build plate is clear and try again.")
            return WAITING_FOR_DECISION

    current_has_supports = context.user_data.get(
        "last_3mf_has_supports", context.user_data.get("enable_supports", False)
    )
    target_supports = parsed_support if parsed_support is not None else current_has_supports

    if target_supports != current_has_supports:
        stl_file = context.user_data.get("last_stl_file")
        outdir = context.user_data.get("last_outdir")
        file_basename = context.user_data.get("last_file_basename")
        if stl_file and outdir and file_basename:
            await _safe_reply(
                update,
                f"Re-slicing model with supports {'enabled' if target_supports else 'disabled'}...",
            )
            import inspect

            slice_fn = getattr(_dispatch(), "slice_stl_to_3mf", slice_stl_to_3mf)
            sig = inspect.signature(slice_fn)
            if "enable_supports" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                re_3mf = await asyncio.to_thread(
                    slice_fn,
                    stl_file,
                    outdir,
                    file_basename,
                    enable_supports=target_supports,
                )
            else:
                re_3mf = await asyncio.to_thread(slice_fn, stl_file, outdir, file_basename)
            if re_3mf:
                out_3mf = re_3mf
                context.user_data["last_3mf_file"] = re_3mf
                context.user_data["last_3mf_has_supports"] = target_supports
                context.user_data["enable_supports"] = target_supports
            else:
                await _safe_reply(update, "Re-slicing failed. Aborting print.")
                return WAITING_FOR_DECISION

    support_str = "with supports" if target_supports else "without supports"
    if slot == 4:
        await _safe_reply(
            update,
            f"Sending print job to External Spool (Slot 4) ({support_str})...",
        )
    elif slot is None:
        await _safe_reply(update, f"Sending print job to External Spool ({support_str})...")
    else:
        await _safe_reply(update, f"Sending print job to AMS slot {slot} ({support_str})...")
    success = False
    for attempt in range(5):
        cancel_token = context.user_data.get("cancel_token")
        if cancel_token and cancel_token.get("cancelled"):
            await _safe_reply(update, "Print submission cancelled. ⏹️")
            return WAITING_FOR_DECISION

        try:
            if await asyncio.to_thread(bambu_cloud.send_print_job, out_3mf, ams_slot=slot):
                success = True
                break
        except PrinterBusyError as e:
            await _safe_reply(update, f"⚠️ Cannot print: {e}")
            return WAITING_FOR_DECISION
        except Exception as e:
            log_server(f"Print attempt {attempt + 1} failed: {e}")

        if attempt < 4:
            cancel_token = context.user_data.get("cancel_token")
            if cancel_token and cancel_token.get("cancelled"):
                await _safe_reply(update, "Print submission cancelled. ⏹️")
                return WAITING_FOR_DECISION
            await asyncio.sleep(5)

    if success:
        slot_label = (
            "External Spool (Slot 4)"
            if slot == 4
            else ("External Spool" if slot is None else f"AMS Slot {slot}")
        )
        await _safe_reply(update, f"Print job successfully started using {slot_label}! 🚀")
        context.user_data.clear()
        return ConversationHandler.END
    else:
        await _safe_reply(update, "Failed to send print job after 5 attempts. ❌")
        return WAITING_FOR_DECISION

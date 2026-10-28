import asyncio
import datetime
import os
import shutil
import subprocess
import uuid
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from src.bots.telegram.handlers_printer import (
    WAITING_FOR_DECISION,
    get_bambu_cloud,
    known_chat_ids,
)
from src.core.activity_tracker import busy_activity
from src.core.ai import generate_openscad_validated, get_max_ai_calls
from src.core.ai.validation_loop import _cleanup_temp_stl
from src.core.config import SESSIONS_DIR, get_openscad_path
from src.core.printer import PrinterBusyError, match_color
from src.core.sessions import (
    format_session_dir_name,
    parse_and_strip_support_flags,
    save_session_atomic,
)
from src.core.slicer import format_dimensions_inches, get_slice_info_from_3mf

WORKSPACE_DIR = SESSIONS_DIR
MAX_AI_CALLS = get_max_ai_calls("telegram_bot")
MAX_HISTORY_TURNS = 6


def get_workspace_dir() -> str:
    import sys

    for mod_name in ("src.bots.telegram.bot", "src.bots.telegram.handlers_design"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "WORKSPACE_DIR"):
            return getattr(mod, "WORKSPACE_DIR")
    return SESSIONS_DIR


def _dispatch():
    import sys

    return sys.modules.get("src.bots.telegram.bot", sys.modules[__name__])


def log_server(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def truncate_text(text: Optional[str], max_len: int = 500) -> str:
    """Truncates text to max_len characters, appending '[truncated]' if needed."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "... [truncated]"


def format_warnings_summary(warnings: list, max_items: int = 5) -> str:
    """Formats a list of validation warnings, capping at max_items and appending a count."""
    if not warnings:
        return ""
    shown = warnings[:max_items]
    lines = [f"- {w}" for w in shown]
    remaining = len(warnings) - len(shown)
    if remaining > 0:
        lines.append(f"... and {remaining} more physical constraint warning(s).")
    return "\n".join(lines)


def is_physical_constraint_warning(warning: str) -> bool:
    """Returns True if the warning represents a physical constraint or geometry issue."""
    if not warning or not isinstance(warning, str):
        return False
    w_lower = warning.lower()
    physical_indicators = (
        "constraint error",
        "geometry error",
        "exceeds max",
        "exceeded max",
        "max print time",
        "max size",
        "disconnected parts",
        "weak structural bottleneck",
        "thin connection",
        "bed contact",
        "non-manifold",
        "mid-air",
        "floating",
        "too thin",
        "outside the build volume",
        "physical constraint",
        "build plate",
    )
    return any(ind in w_lower for ind in physical_indicators)


def filter_physical_constraint_warnings(warnings: list[str]) -> list[str]:
    """Filters a warnings list to only physical constraint and geometry warnings."""
    if not warnings:
        return []
    return [w for w in warnings if is_physical_constraint_warning(w)]


async def safe_reply_text(
    target,
    text: str,
    max_chunk_size: int = 4000,
    **kwargs,
) -> bool:
    """Safely sends a text message to Telegram, splitting long texts and guarding against exceptions."""
    if not text:
        return False

    send_func = None
    if hasattr(target, "message") and getattr(target, "message", None) is not None:
        send_func = target.message.reply_text
    elif hasattr(target, "reply_text"):
        send_func = target.reply_text
    elif hasattr(target, "effective_chat") and getattr(target, "effective_chat", None) is not None:
        send_func = target.effective_chat.send_message
    elif hasattr(target, "send_message"):
        send_func = target.send_message
    else:
        log_server(f"safe_reply_text: Cannot find send method on {type(target)}")
        return False

    chunks = []
    if len(text) <= max_chunk_size:
        chunks = [text]
    else:
        lines = text.split("\n")
        current_chunk = []
        current_len = 0
        for line in lines:
            line_len = len(line) + 1
            if current_len + line_len > max_chunk_size:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                if len(line) > max_chunk_size:
                    for i in range(0, len(line), max_chunk_size):
                        chunks.append(line[i : i + max_chunk_size])
                else:
                    current_chunk.append(line)
                    current_len += line_len
            else:
                current_chunk.append(line)
                current_len += line_len
        if current_chunk:
            chunks.append("\n".join(current_chunk))

    success = False
    for chunk in chunks:
        try:
            await send_func(chunk, **kwargs)
            success = True
        except Exception as e:
            log_server(f"safe_reply_text failed to send message: {e}")
            break

    return success


def prune_conversation_history(history: list, max_turns: int = MAX_HISTORY_TURNS) -> list:
    """Prunes history to keep the initial root prompt plus the last max_turns."""
    if not history or len(history) <= max_turns:
        return history

    first_prompt = history[0] if history[0].get("role") == "user" else None
    recent_history = history[-max_turns:]

    if first_prompt and first_prompt not in recent_history:
        return [first_prompt] + recent_history
    return recent_history


def save_telegram_session_json(
    parent_dir, title, history, stl_file, versions, enable_supports: bool = False
):
    try:
        ws = get_workspace_dir()
        rel_stl = stl_file.replace(ws, "/outputs/sessions").replace("\\", "/")
        session_data = {
            "id": os.path.basename(parent_dir),
            "title": title.replace("_", " ").title(),
            "history": history,
            "versions": versions,
            "latest_stl": rel_stl,
            "enable_supports": enable_supports,
            "last_updated": datetime.datetime.now().isoformat(),
        }
        save_session_atomic(parent_dir, session_data)
        try:
            from src.core.sessions import enforce_storage_retention_policy

            enforce_storage_retention_policy(
                active_session_ids={session_data.get("id"), os.path.basename(parent_dir)}
            )
        except Exception:
            pass
    except Exception as e:
        log_server(f"Error saving session.json: {e}")


def slice_stl_to_3mf(stl_file, outdir, file_basename, enable_supports=None):
    from src.core.slicer import slice_stl_to_3mf as shared_slicer

    log_server(f"Slicing {stl_file} to 3MF using shared slicer logic (Prusa injection)...")
    return shared_slicer(stl_file, outdir, file_basename, enable_supports=enable_supports)


async def process_stl_for_print(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stl_file: str,
    outdir: str,
    file_basename: str,
    requested_colors: list = None,
    auto_print: bool = False,
    enable_supports: bool = False,
    prebuilt_3mf: str = None,
):
    d = _dispatch()
    bambu_cloud = getattr(d, "bambu_cloud", get_bambu_cloud())
    slice_fn = getattr(d, "slice_stl_to_3mf", slice_stl_to_3mf)
    context.user_data["design_state"] = "Slicing to 3MF..."
    context.user_data["last_stl_file"] = stl_file
    context.user_data["last_outdir"] = outdir
    context.user_data["last_file_basename"] = file_basename
    import inspect

    with busy_activity("telegram_slicing", metadata={"stl_file": stl_file}):
        try:
            if prebuilt_3mf and os.path.exists(prebuilt_3mf):
                out_3mf = prebuilt_3mf
            else:
                sig = inspect.signature(slice_fn)
                if "enable_supports" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    out_3mf = await asyncio.to_thread(
                        slice_fn, stl_file, outdir, file_basename, enable_supports=enable_supports
                    )
                else:
                    out_3mf = await asyncio.to_thread(slice_fn, stl_file, outdir, file_basename)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, Exception) as e:
            log_server(f"Slicing exception in process_stl_for_print: {e}")
            out_3mf = None

    cancel_token = context.user_data.get("cancel_token")
    if cancel_token and cancel_token.get("cancelled"):
        log_server("Slice thread finished — cancel acknowledged.")
        context.user_data.clear()
        await safe_reply_text(update, "Cancelled ✅ You can now start a new design.")
        return ConversationHandler.END

    if not context.user_data.get("is_processing"):
        return ConversationHandler.END
    if not out_3mf:
        await safe_reply_text(
            update,
            "Failed to slice the model using Bambu Studio. You can use /edit <changes> to ask AI to fix the design.",
        )
        context.user_data["design_state"] = "Failed to slice"
        context.user_data["is_processing"] = False
        return WAITING_FOR_DECISION

    context.user_data["last_3mf_file"] = out_3mf
    context.user_data["last_3mf_has_supports"] = bool(enable_supports)
    context.user_data["requested_colors"] = requested_colors or []
    available_colors = await asyncio.to_thread(bambu_cloud.get_available_colors)

    slice_info = get_slice_info_from_3mf(out_3mf)
    required_grams = slice_info.get("grams", 0.0)
    print_time = slice_info.get("time", "Unknown")
    has_supports = bool(slice_info.get("has_supports", False))
    context.user_data["required_grams"] = required_grams

    try:
        size_msg = format_dimensions_inches(stl_file)
    except Exception:
        size_msg = "Unknown"

    log_server(f"Estimated filament required: {required_grams}g, Time: {print_time}")

    conv_hist = context.user_data.get("conversation_history", [])
    for turn in reversed(conv_hist):
        if turn.get("role") == "assistant":
            turn["size"] = size_msg
            turn["time"] = print_time
            turn["grams"] = required_grams
            turn["has_supports"] = has_supports
            turn["enable_supports"] = bool(enable_supports)
            break

    parent_dir = context.user_data.get("parent_dir")
    if parent_dir:
        save_fn = getattr(d, "save_telegram_session_json", save_telegram_session_json)
        curr_ver = max(1, context.user_data.get("edit_version", 2) - 1)
        title_prefix = context.user_data.get("filename_prefix") or file_basename
        save_fn(
            parent_dir,
            title_prefix,
            conv_hist,
            stl_file,
            curr_ver,
            enable_supports=enable_supports,
        )

    if enable_supports:
        support_label = (
            "Enabled (Tree supports generated)" if has_supports else "Enabled (None needed)"
        )
    else:
        support_label = "Disabled"

    info_msg = (
        f"Model sliced and ready!\n"
        f"📏 Size: {size_msg}\n"
        f"⚖️ Filament: {required_grams}g\n"
        f"⏳ Expected Time: {print_time}\n"
        f"🛠️ Supports: {support_label}\n"
        f"🎨 Recommended colors: {', '.join(requested_colors) if requested_colors else 'None'}\n\n"
    )

    if auto_print:
        best_slot = None
        if available_colors:
            best_slot = match_color(requested_colors, available_colors, required_grams)
            if best_slot is None:
                await safe_reply_text(
                    update,
                    info_msg
                    + "Error: No suitable AMS slot found with enough PLA for auto-print. ❌",
                )
                context.user_data["design_state"] = "Print Blocked (No AMS Slot)"
                context.user_data["is_processing"] = False
                return WAITING_FOR_DECISION
        elif not available_colors:
            best_slot = None

        if best_slot is not None:
            await safe_reply_text(
                update,
                info_msg + f"Submitting to Bambu P2S (AMS Slot {best_slot})...",
            )
        else:
            await safe_reply_text(
                update,
                info_msg + "Submitting to Bambu P2S (External Spool)...",
            )

        success = False
        for attempt in range(5):
            log_server(f"Print attempt {attempt + 1}")
            try:
                if await asyncio.to_thread(bambu_cloud.send_print_job, out_3mf, ams_slot=best_slot):
                    success = True
                    break
            except PrinterBusyError as e:
                await safe_reply_text(update, f"⚠️ Cannot print: {e}")
                context.user_data["design_state"] = "Print Failed"
                context.user_data["is_processing"] = False
                return WAITING_FOR_DECISION
            await asyncio.sleep(5)

        if success:
            await safe_reply_text(update, "Print job successfully started! 🚀")
            context.user_data.clear()
            return ConversationHandler.END
        else:
            await safe_reply_text(update, "Failed to send print job after 5 attempts. ❌")
            context.user_data["design_state"] = "Print Failed"
            context.user_data["is_processing"] = False
            return WAITING_FOR_DECISION
    else:
        info_msg += "To print, use /print <slot> [--support]. Or use /print by itself to see color options.\n"
        info_msg += "To edit the design, use /edit <changes>. To start over, use /cancel."
        await safe_reply_text(update, info_msg)
        context.user_data["design_state"] = "Waiting for command"
        context.user_data["is_processing"] = False
        return WAITING_FOR_DECISION


async def process_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    auto_print: bool = False,
    enable_supports: bool = False,
):
    context.user_data["design_state"] = "Designing with AI..."
    log_server(f"Processing request for: '{prompt}' (Auto-Print: {auto_print})")
    await safe_reply_text(update, f"Designing: {prompt}...")

    conversation_history = context.user_data.get("conversation_history", [])
    conversation_history = prune_conversation_history(conversation_history)

    d = _dispatch()
    gen_fn = getattr(d, "generate_openscad_validated", generate_openscad_validated)
    save_fn = getattr(d, "save_telegram_session_json", save_telegram_session_json)
    proc_print_fn = getattr(d, "process_stl_for_print", process_stl_for_print)

    try:
        import inspect

        gen_sig = inspect.signature(gen_fn)
        gen_kwargs = {}
        if "keep_temp_artifacts" in gen_sig.parameters:
            gen_kwargs["enable_supports"] = enable_supports
            gen_kwargs["keep_temp_artifacts"] = True

        with busy_activity("telegram_generation", metadata={"prompt": prompt}):
            res = await asyncio.to_thread(
                gen_fn,
                prompt,
                conversation_history,
                MAX_AI_CALLS,
                "telegram_bot",
                **gen_kwargs,
            )
        code, filename_prefix, requested_colors, warnings = res[:4]
        prebuilt_stl = res[4] if len(res) > 4 else None
        prebuilt_3mf = res[5] if len(res) > 5 else None
    except Exception as e:
        context.user_data["is_processing"] = False
        log_server(f"AI API Error: {e}")
        await safe_reply_text(
            update,
            "There was an error generating your design. Please try again later.",
        )
        return ConversationHandler.END

    cancel_token = context.user_data.get("cancel_token")
    if cancel_token and cancel_token.get("cancelled"):
        _cleanup_temp_stl(prebuilt_stl)
        log_server("Design thread finished — cancel acknowledged.")
        context.user_data.clear()
        await safe_reply_text(update, "Cancelled ✅ You can now start a new design.")
        return ConversationHandler.END

    if not context.user_data.get("is_processing"):
        _cleanup_temp_stl(prebuilt_stl)
        return ConversationHandler.END

    physical_warnings = filter_physical_constraint_warnings(warnings)
    if physical_warnings:
        warn_msg = format_warnings_summary(physical_warnings, max_items=5)
        if auto_print:
            _cleanup_temp_stl(prebuilt_stl)
            await safe_reply_text(
                update,
                f"Print aborted: The generated design did not meet physical constraints:\n{warn_msg}",
            )
            context.user_data["design_state"] = "Constraint Validation Failed"
            context.user_data["is_processing"] = False
            return WAITING_FOR_DECISION
        else:
            await safe_reply_text(
                update,
                f"Warning: The design failed to meet physical constraints:\n{warn_msg}",
            )

    conversation_history.append({"role": "user", "text": prompt})
    conversation_history.append({"role": "assistant", "code": code})
    context.user_data["conversation_history"] = conversation_history
    context.user_data["filename_prefix"] = filename_prefix

    edit_version = context.user_data.get("edit_version", 1)
    ws = get_workspace_dir()
    if edit_version == 1:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        parent_dir = os.path.join(ws, f"{timestamp}_{filename_prefix}_{short_uuid}")
        context.user_data["parent_dir"] = parent_dir
        context.user_data["file_timestamp"] = timestamp
    else:
        parent_dir = context.user_data.get("parent_dir")
        timestamp = context.user_data.get("file_timestamp")

    outdir = os.path.join(parent_dir, f"v{edit_version}")
    os.makedirs(outdir, exist_ok=True)

    context.user_data["edit_version"] = edit_version + 1

    file_basename = f"{filename_prefix}_v{edit_version}_{timestamp}"
    stl_file = os.path.join(outdir, f"{file_basename}.stl")
    out_scad_file = os.path.join(outdir, f"{file_basename}.scad")

    from src.core.ai import clean_scad_code

    code = clean_scad_code(code)
    with open(out_scad_file, "w", encoding="utf-8") as f:
        f.write(code)

    context.user_data["design_state"] = "Rendering OpenSCAD..."

    try:
        dest_3mf = None
        if prebuilt_stl and os.path.exists(prebuilt_stl):
            shutil.move(prebuilt_stl, stl_file)
            if prebuilt_3mf and os.path.exists(prebuilt_3mf):
                dest_3mf = os.path.join(outdir, f"{file_basename}.3mf")
                shutil.move(prebuilt_3mf, dest_3mf)
            _cleanup_temp_stl(prebuilt_stl)
            if not context.user_data.get("is_processing"):
                return ConversationHandler.END
        else:
            p_stl = await asyncio.to_thread(
                subprocess.run,
                [get_openscad_path(), "-o", stl_file, out_scad_file],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if not context.user_data.get("is_processing"):
                return ConversationHandler.END
            if p_stl.returncode != 0:
                raise subprocess.CalledProcessError(
                    p_stl.returncode, p_stl.args, output=p_stl.stdout, stderr=p_stl.stderr
                )

        if not auto_print:
            try:
                with open(stl_file, "rb") as doc_file:
                    await update.message.reply_document(
                        document=doc_file,
                        filename=os.path.basename(stl_file),
                        read_timeout=120,
                        write_timeout=120,
                    )
            except Exception as e:
                log_server(f"Failed to send STL document: {e}")
                await safe_reply_text(
                    update,
                    "Note: Could not upload STL document file, but continuing with slicing and preview.",
                )

        save_fn(
            parent_dir,
            filename_prefix,
            conversation_history,
            stl_file,
            edit_version,
            enable_supports=enable_supports,
        )
        import inspect

        proc_sig = inspect.signature(proc_print_fn)
        proc_kwargs = {"enable_supports": enable_supports}
        if "prebuilt_3mf" in proc_sig.parameters and dest_3mf:
            proc_kwargs["prebuilt_3mf"] = dest_3mf
        res_print = await proc_print_fn(
            update,
            context,
            stl_file,
            outdir,
            file_basename,
            requested_colors,
            auto_print,
            **proc_kwargs,
        )
        save_fn(
            parent_dir,
            filename_prefix,
            conversation_history,
            stl_file,
            edit_version,
            enable_supports=enable_supports,
        )
        return res_print

    except subprocess.CalledProcessError as e:
        stderr_msg = truncate_text(e.stderr, 500) if e.stderr is not None else "None"
        await safe_reply_text(
            update,
            f"Failed to render the OpenSCAD code. You can use /edit <changes> to ask AI to fix it.\nError: {e}\nStderr: {stderr_msg}",
        )
        context.user_data["design_state"] = "Failed OpenSCAD render"
        context.user_data["is_processing"] = False
        return WAITING_FOR_DECISION
    except subprocess.TimeoutExpired:
        await safe_reply_text(
            update,
            "OpenSCAD rendering timed out. The generated design might be too complex. You can use /edit to ask AI to simplify it.",
        )
        context.user_data["design_state"] = "Failed OpenSCAD render (Timeout)"
        context.user_data["is_processing"] = False
        return WAITING_FOR_DECISION


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".stl"):
        await safe_reply_text(update, "Please upload an .stl file.")
        return

    file_size = getattr(doc, "file_size", None)
    if isinstance(file_size, (int, float)) and file_size > 20 * 1024 * 1024:
        await safe_reply_text(
            update,
            "File exceeds the 20MB limit supported by Telegram Bot. Please upload a smaller STL.",
        )
        return ConversationHandler.END

    known_chat_ids.add(update.effective_chat.id)
    if context.user_data.get("is_processing") or context.user_data.get("is_cancelling"):
        await safe_reply_text(
            update,
            "You have a design currently processing. You must /cancel the previous one first.",
        )
        return ConversationHandler.END

    safe_filename = os.path.basename(doc.file_name)
    log_server(f"Received STL upload: {safe_filename}")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    basename = os.path.splitext(safe_filename)[0]
    ws = get_workspace_dir()
    folder_name = format_session_dir_name(f"uploaded_{basename}", short_uuid, timestamp=timestamp)
    parent_dir = os.path.join(ws, folder_name)
    outdir = os.path.join(parent_dir, "v1")
    os.makedirs(outdir, exist_ok=True)

    stl_file = os.path.join(outdir, safe_filename)
    try:
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(stl_file)
    except Exception as e:
        log_server(f"Failed to download STL: {e}")
        await safe_reply_text(update, "Failed to download the uploaded file. Please try again.")
        shutil.rmtree(parent_dir, ignore_errors=True)
        return ConversationHandler.END

    conv_history = [
        {"role": "user", "text": "Uploaded STL"},
        {"role": "assistant", "code": ""},
    ]
    context.user_data.clear()
    context.user_data["is_processing"] = True
    context.user_data["cancel_token"] = {"cancelled": False}
    context.user_data["parent_dir"] = parent_dir
    context.user_data["filename_prefix"] = basename
    context.user_data["edit_version"] = 2
    context.user_data["conversation_history"] = conv_history
    context.user_data["enable_supports"] = False
    await safe_reply_text(update, "STL received! Slicing for Bambu P2S...")

    d = _dispatch()
    save_fn = getattr(d, "save_telegram_session_json", save_telegram_session_json)
    save_fn(
        parent_dir,
        basename,
        conv_history,
        stl_file,
        1,
        enable_supports=False,
    )

    proc_fn = getattr(d, "process_stl_for_print", process_stl_for_print)
    res_print = await proc_fn(
        update, context, stl_file, outdir, basename, auto_print=False, enable_supports=False
    )
    save_fn(
        parent_dir,
        basename,
        conv_history,
        stl_file,
        1,
        enable_supports=False,
    )
    return res_print


async def start_make(update: Update, context: ContextTypes.DEFAULT_TYPE):
    known_chat_ids.add(update.effective_chat.id)
    if context.user_data.get("is_processing") or context.user_data.get("is_cancelling"):
        await safe_reply_text(
            update,
            "You have a design currently processing. You must /cancel the previous one first.",
        )
        return ConversationHandler.END
    user_input = " ".join(context.args) if context.args else ""
    if not user_input:
        await safe_reply_text(
            update,
            "Please provide an input. Example: /make a small coffee cup",
        )
        return ConversationHandler.END
    prompt, parsed_support = parse_and_strip_support_flags(user_input)
    if not prompt:
        await safe_reply_text(update, "Please provide an input description for your design.")
        return ConversationHandler.END
    enable_supports = parsed_support if parsed_support is not None else False
    context.user_data.clear()
    context.user_data["is_processing"] = True
    context.user_data["cancel_token"] = {"cancelled": False}
    context.user_data["enable_supports"] = enable_supports
    d = _dispatch()
    proc_fn = getattr(d, "process_prompt", process_prompt)
    return await proc_fn(update, context, prompt, auto_print=True, enable_supports=enable_supports)


async def start_design(update: Update, context: ContextTypes.DEFAULT_TYPE):
    known_chat_ids.add(update.effective_chat.id)
    if context.user_data.get("is_processing") or context.user_data.get("is_cancelling"):
        await safe_reply_text(
            update,
            "You have a design currently processing. You must /cancel the previous one first.",
        )
        return ConversationHandler.END
    user_input = " ".join(context.args) if context.args else ""
    if not user_input:
        await safe_reply_text(
            update,
            "Please provide an input. Example: /design a small coffee cup",
        )
        return ConversationHandler.END
    prompt, parsed_support = parse_and_strip_support_flags(user_input)
    if not prompt:
        await safe_reply_text(update, "Please provide an input description for your design.")
        return ConversationHandler.END
    enable_supports = parsed_support if parsed_support is not None else False
    context.user_data.clear()
    context.user_data["is_processing"] = True
    context.user_data["cancel_token"] = {"cancelled": False}
    context.user_data["enable_supports"] = enable_supports
    d = _dispatch()
    proc_fn = getattr(d, "process_prompt", process_prompt)
    return await proc_fn(update, context, prompt, auto_print=False, enable_supports=enable_supports)


async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("is_processing") or context.user_data.get("is_cancelling"):
        await safe_reply_text(
            update,
            "You have an edit currently processing. You must /cancel the previous one first.",
        )
        return WAITING_FOR_DECISION
    user_input = " ".join(context.args) if context.args else ""
    if not user_input:
        await safe_reply_text(
            update,
            "Please provide what to edit. Example: /edit make it taller",
        )
        return WAITING_FOR_DECISION
    if not context.user_data.get("conversation_history"):
        await safe_reply_text(
            update,
            "No active design to edit. Please use /make or /design first.",
        )
        return ConversationHandler.END
    prompt, parsed_support = parse_and_strip_support_flags(user_input)
    if not prompt:
        await safe_reply_text(update, "Please provide edit instructions.")
        return WAITING_FOR_DECISION

    if parsed_support is not None:
        enable_supports = parsed_support
    else:
        enable_supports = context.user_data.get("enable_supports", False)

    context.user_data["enable_supports"] = enable_supports
    if "last_3mf_file" in context.user_data:
        del context.user_data["last_3mf_file"]
    context.user_data["is_processing"] = True
    context.user_data["cancel_token"] = {"cancelled": False}
    d = _dispatch()
    proc_fn = getattr(d, "process_prompt", process_prompt)
    return await proc_fn(update, context, prompt, auto_print=False, enable_supports=enable_supports)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("is_processing"):
        cancel_token = context.user_data.get("cancel_token")
        if cancel_token is not None:
            cancel_token["cancelled"] = True
        context.user_data["is_processing"] = False
        context.user_data["is_cancelling"] = True
        await safe_reply_text(
            update,
            "⏳ Cancellation in progress — waiting for the current operation to finish.\n"
            "You'll receive a confirmation once it's done.",
        )
        return WAITING_FOR_DECISION
    elif context.user_data.get("is_cancelling"):
        await safe_reply_text(
            update,
            "Already cancelling — please wait for the confirmation message.",
        )
        return WAITING_FOR_DECISION
    else:
        context.user_data.clear()
        await safe_reply_text(update, "Cancelled ✅")
        return ConversationHandler.END


async def enforce_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("is_cancelling"):
        msg = (
            "Cancellation is in progress — please wait.\n\n"
            "Available commands:\n"
            "- /status - Check printer status\n"
            "- /snapshot - Get a live photo\n"
        )
        await safe_reply_text(update, msg)
        return WAITING_FOR_DECISION

    msg = (
        "You currently have an active design session.\n\n"
        "Available commands:\n"
        "- /status - Check printer status\n"
        "- /snapshot - Get a live photo\n"
        "- /cancel - Cancel session and start over\n"
    )
    if not context.user_data.get("is_processing"):
        msg += "- /print <slot> [--support] - Print the model\n"
        msg += "- /edit <changes> [--support|--no-support] - Refine the design\n"

    await safe_reply_text(update, msg)
    return WAITING_FOR_DECISION


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("is_cancelling"):
        msg = (
            "Cancellation is in progress — please wait for the confirmation message.\n\n"
            "Available commands right now:\n"
            "- /status - Check printer status\n"
            "- /snapshot - Get a live photo\n"
        )
        await safe_reply_text(update, msg)
        return

    if context.user_data.get("is_processing"):
        msg = (
            "I'm currently busy processing a design.\n\n"
            "Available commands right now:\n"
            "- /status - Check printer status\n"
            "- /snapshot - Get a live photo\n"
            "- /cancel - Cancel the current process"
        )
        await safe_reply_text(update, msg)
        return

    welcome_text = (
        "Hi there! 👋 I'm ready to design and print some 3D parts on your Bambu Lab printer.\n\n"
        "Here are your options:\n"
        "1. /make <idea> [--support] - I will design it, slice it, and instantly send it to the printer.\n"
        "2. /design <idea> [--support] - I will design it and let you pick the AMS color.\n"
        "3. **Upload an `.stl` file directly** - I will slice it and let you print it.\n"
        "4. /status - Check printer status.\n"
        "5. /snapshot - Get a live photo of the print bed.\n"
        "6. /edit <changes> [--support|--no-support] - Refine the current design.\n"
        "7. /print <slot> [--support] - Print the generated model (e.g., /print 1 --support)."
    )
    await safe_reply_text(update, welcome_text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_server(f"Telegram Bot Error: {context.error}")

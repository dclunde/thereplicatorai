import os
from src.core.config import PROMPTS_DIR


def _build_constraint_hint(*args, **kwargs) -> str:
    """Build a plain constraint hint string from active config constraints or dimensions."""
    if not args and not kwargs:
        return ""

    first_arg = args[0] if args else kwargs.get("config")
    if isinstance(first_arg, (int, float)):
        x = first_arg
        y = args[1] if len(args) > 1 else kwargs.get("y", x)
        z = args[2] if len(args) > 2 else kwargs.get("z", y)
        return f"\n\nDESIGN CONSTRAINTS:\n- Dimensions: {x} x {y} x {z} mm"

    config = first_arg
    if not config or not isinstance(config, dict):
        return ""

    lines = []
    max_time = config.get("max_print_time_minutes", 0)
    max_size = config.get("max_size_inches", 0)

    if max_time > 0:
        lines.append(f"- Maximum allowed print time: {max_time} minutes.")
    if max_size > 0:
        lines.append(f"- Maximum allowed size: {max_size} inches in any single dimension.")

    if not lines:
        return ""

    return "\n\nDESIGN CONSTRAINTS:\n" + "\n".join(lines)


def _build_initial_prompt(prompt: str, *args, **kwargs) -> str:
    """Build the content prompt from user input and conversation history or dimensions."""
    if args and isinstance(args[0], (int, float)):
        return f"Design this in OpenSCAD: {prompt}"

    conversation_history = args[0] if len(args) > 0 else kwargs.get("conversation_history")
    config = args[1] if len(args) > 1 else kwargs.get("config")
    content_prompt = ""
    if conversation_history and len(conversation_history) > 0:
        latest_code = None
        latest_size = None
        latest_time = None
        latest_grams = None
        for turn in reversed(conversation_history):
            if turn.get("role") == "assistant" and "code" in turn:
                latest_code = turn["code"]
                latest_size = turn.get("size")
                latest_time = turn.get("time")
                latest_grams = turn.get("grams")
                break

        for turn in conversation_history:
            if turn.get("role") == "user":
                txt = turn.get("text")
                if txt and isinstance(txt, str):
                    if txt.strip().lower() in ["size", "time"]:
                        continue
                    content_prompt += f"Previous User Request: {txt}\n\n"

        if latest_code:
            content_prompt += (
                f"Latest OpenSCAD code generated:\n```openscad\n{latest_code}\n```\n\n"
            )

        if latest_size:
            content_prompt += (
                f"Physical Properties of Previous Model:\n"
                f"- Bounding Size: {latest_size}\n"
                f"- Estimated Print Time: {latest_time or 'Unknown'}\n"
                f"- Estimated Filament Usage: {latest_grams or 0.0}g\n\n"
            )

        content_prompt += f"New User Edit Request: {prompt}"
        content_prompt += _build_constraint_hint(config)
    else:
        content_prompt = f"Design this in OpenSCAD: {prompt}"
        content_prompt += _build_constraint_hint(config)

    return content_prompt


def _build_user_context(prompt: str, conversation_history: list | None) -> str:
    """Build a concise user context string for error/verify agents."""
    if conversation_history and len(conversation_history) > 0:
        parts = []
        for turn in conversation_history:
            if turn.get("role") == "user":
                txt = turn.get("text")
                if txt and isinstance(txt, str):
                    if txt.strip().lower() in ["size", "time"]:
                        continue
                    parts.append(txt)
        parts.append(prompt)
        return " → ".join(parts)
    return prompt


def _build_agent_instructions(base_instructions: str = "", role: str = "generate") -> str:
    """Build role-specific system instructions to avoid contradicting content prompts."""
    if not base_instructions:
        instructions_file = os.path.join(PROMPTS_DIR, "ai_instructions.txt")
        if os.path.exists(instructions_file):
            try:
                with open(instructions_file, "r", encoding="utf-8") as f:
                    base_instructions = f.read()
            except Exception:
                base_instructions = ""
        if not base_instructions:
            base_instructions = (
                "You are an expert OpenSCAD 3D modeling assistant.\n\n"
                "Output Requirements:\n"
                "Return valid JSON."
            )

    if role == "generate":
        return base_instructions

    marker = "Output Requirements:"
    idx = base_instructions.find(marker)
    if idx == -1:
        return base_instructions

    design_rules = base_instructions[:idx].rstrip()

    if role == "error_fix":
        return (
            design_rules + "\n\n"
            "Output Requirements:\n"
            'Return ONLY a raw, valid JSON object with a single key "code" '
            "containing the complete, fixed OpenSCAD code. "
            'Do NOT include "filename" or "color_hierarchy".'
        )
    elif role == "verify":
        return (
            design_rules + "\n\n"
            "Output Requirements:\n"
            'Return ONLY a raw, valid JSON object with a single key "code". '
            "If the code is satisfactory, return it unchanged. "
            "If it needs improvements, return the improved code. "
            'Do NOT include "filename" or "color_hierarchy".'
        )

    return base_instructions

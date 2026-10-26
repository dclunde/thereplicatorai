import math

COLOR_NAMES = {
    "Red": (255, 0, 0),
    "Green": (0, 255, 0),
    "Blue": (0, 0, 255),
    "Yellow": (255, 255, 0),
    "Orange": (255, 165, 0),
    "Purple": (128, 0, 128),
    "Black": (0, 0, 0),
    "White": (255, 255, 255),
    "Grey": (128, 128, 128),
    "Brown": (165, 42, 42),
    "Pink": (255, 192, 203),
    "Cyan": (0, 255, 255),
    "Magenta": (255, 0, 255),
}


def normalize_hex_color(hex_color: str | None) -> str:
    """Normalizes a 3-character, 6-character, or 8-character hex color to a 6-character hex string."""
    if not hex_color:
        return ""
    h = str(hex_color).lstrip("#")
    if len(h) == 3:
        h = f"{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}"
    elif len(h) == 8:
        h = h[:6]
    return h


def get_blandness_score(hex_color: str | None) -> int:
    """Calculates a blandness score based on color saturation (lower is less saturated / more bland)."""
    try:
        h = normalize_hex_color(hex_color)
        if len(h) != 6:
            return 999
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return abs(r - g) + abs(g - b) + abs(b - r)
    except Exception:
        return 999


def get_closest_color_name(hex_color: str | None) -> str:
    """Finds the closest human-friendly color name from COLOR_NAMES dictionary."""
    if not hex_color:
        return "Unknown"
    try:
        h = normalize_hex_color(hex_color)
        if len(h) != 6:
            return "Unknown"
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        min_dist = float("inf")
        best_name = "Unknown"
        for name, (cr, cg, cb) in COLOR_NAMES.items():
            dist = math.sqrt((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2)
            if dist < min_dist:
                min_dist = dist
                best_name = name
        return best_name
    except Exception:
        return "Unknown"


def match_color(requested_colors, available_colors, required_grams):
    """Matches the requested colors with available filaments in AMS slots 0-3 with sufficient grams."""
    if not available_colors:
        return None

    filament_groups = {}

    for slot, data in available_colors.items():
        # Slot 4 is the External Spool; auto-matching only considers AMS slots (0-3)
        if slot == 4 or data.get("is_external"):
            continue

        if data.get("type") == "PLA":
            remain_pct = data.get("remain", 0)
            is_bambu = data.get("is_bambu", False)
            color_hex = data.get("color", "")
            ftype = data.get("type", "PLA")
            key = (ftype, color_hex)

            if key not in filament_groups:
                filament_groups[key] = {
                    "slots": [],
                    "total_grams": 0,
                    "total_pct": 0,
                    "is_bambu": is_bambu,
                }

            filament_groups[key]["slots"].append({"slot": slot, "remain": remain_pct})
            if is_bambu:
                filament_groups[key]["total_grams"] += remain_pct * 10
            else:
                filament_groups[key]["total_pct"] += remain_pct

    best_slot = None
    best_score = float("inf")

    for key, group_data in filament_groups.items():
        has_enough = False
        if group_data["is_bambu"]:
            if group_data["total_grams"] >= required_grams:
                has_enough = True
        else:
            if group_data["total_pct"] * 10 >= required_grams:
                has_enough = True

        if has_enough:
            color_hex = key[1]
            color_name = get_closest_color_name(color_hex)

            if requested_colors and color_name in requested_colors:
                idx = requested_colors.index(color_name)
                score = -10000 + idx * 100
            else:
                score = get_blandness_score(color_hex)

            if score < best_score:
                best_score = score
                sorted_slots = sorted(group_data["slots"], key=lambda x: x["remain"])
                best_slot = sorted_slots[0]["slot"]

    return best_slot

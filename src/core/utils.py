"""Convenience utility module re-exporting core functions from domain subpackages."""

from src.core.config import (
    disable_quickedit,
    get_bambu_studio_path,
    get_openscad_path,
    get_prusa_slicer_path,
    get_slicer_path,
    has_openscad,
    has_slicer,
    is_ci_environment,
    load_api_keys,
    load_env_keys,
)
from src.core.printer.color_matcher import (
    COLOR_NAMES,
    get_blandness_score,
    get_closest_color_name,
    match_color,
    normalize_hex_color,
)
from src.core.sessions import purge_heavy_assets
from src.core.slicer.openscad import (
    calculate_max_dimensions,
    clean_scad_code,
    format_dimensions_inches,
)
from src.core.slicer.slicer_3mf import (
    format_time_str,
    get_filament_usage_from_3mf,
    get_slice_info_from_3mf,
)

# Run disable_quickedit on import (preserves shared.utils behavior)
disable_quickedit()

__all__ = [
    "disable_quickedit",
    "load_api_keys",
    "load_env_keys",
    "get_bambu_studio_path",
    "get_openscad_path",
    "get_prusa_slicer_path",
    "get_slicer_path",
    "has_openscad",
    "has_slicer",
    "is_ci_environment",
    "calculate_max_dimensions",
    "format_dimensions_inches",
    "clean_scad_code",
    "format_time_str",
    "get_filament_usage_from_3mf",
    "get_slice_info_from_3mf",
    "purge_heavy_assets",
    "normalize_hex_color",
    "get_blandness_score",
    "get_closest_color_name",
    "match_color",
    "COLOR_NAMES",
]

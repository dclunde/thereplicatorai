"""Multi-User Configuration Architecture.

Supports committed default configuration files (*.default.json) and gitignored
local overrides (*.local.json) with deep dictionary merging.
"""

from copy import deepcopy
import json
import os
from typing import Any

from src.core.config import ROOT_DIR

DEFAULT_SERVICES_CONFIG_PATH = os.path.join(
    ROOT_DIR, "inputs", "configs", "services_config.default.json"
)
LOCAL_SERVICES_CONFIG_PATH = os.path.join(
    ROOT_DIR, "inputs", "configs", "services_config.local.json"
)

FALLBACK_SERVICES_CONFIG: dict[str, Any] = {
    "services": {
        "online_backend": True,
        "online_frontend": True,
        "telegram_bot": True,
        "google_tasks_bot": True,
    },
    "online_frontend_mode": "dev",
    "logging": {
        "max_bytes_per_file": 10485760,
        "backup_count": 5,
        "circuit_breaker_max_restarts": 5,
        "circuit_breaker_window_seconds": 180,
        "backoff_initial_seconds": 2,
        "backoff_max_seconds": 60,
    },
    "auto_updater": {
        "enabled": True,
        "branch": "main",
        "window_start": "04:00",
        "window_end": "05:00",
        "check_interval_seconds": 300,
        "busy_retry_interval_seconds": 600,
        "check_printer_busy": True,
        "notify_telegram": True,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively deep merge dictionary 'override' into dictionary 'base'.

    Does not mutate input dictionaries.
    """
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_services_config(
    config_path: str | None = None,
    local_path: str | None = None,
) -> dict[str, Any]:
    """Load base services config and deep merge local override if present."""
    base_file = config_path or DEFAULT_SERVICES_CONFIG_PATH
    local_file = local_path or LOCAL_SERVICES_CONFIG_PATH

    config: dict[str, Any] = deepcopy(FALLBACK_SERVICES_CONFIG)

    if os.path.exists(base_file):
        try:
            with open(base_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    config = deep_merge(config, loaded)
        except Exception as e:
            print(f"[config_loader] Warning: Failed to read {base_file}: {e}")

    if os.path.exists(local_file):
        try:
            with open(local_file, "r", encoding="utf-8") as f:
                local_loaded = json.load(f)
                if isinstance(local_loaded, dict):
                    config = deep_merge(config, local_loaded)
        except Exception as e:
            print(f"[config_loader] Warning: Failed to read local override {local_file}: {e}")

    return config

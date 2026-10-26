import json

from src.core.config_loader import deep_merge, load_services_config


def test_deep_merge_basic():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}, "e": 4}
    merged = deep_merge(base, override)

    assert merged["a"] == 1
    assert merged["b"]["c"] == 99
    assert merged["b"]["d"] == 3
    assert merged["e"] == 4
    # Ensure immutability of inputs
    assert base["b"]["c"] == 2


def test_load_services_config_defaults(tmp_path):
    default_cfg = {
        "services": {"online_backend": True, "telegram_bot": False},
        "logging": {"max_bytes_per_file": 1000},
    }
    p_default = tmp_path / "services_config.default.json"
    p_default.write_text(json.dumps(default_cfg), encoding="utf-8")

    cfg = load_services_config(config_path=str(p_default), local_path=str(tmp_path / "none.json"))
    assert cfg["services"]["online_backend"] is True
    assert cfg["services"]["telegram_bot"] is False
    assert cfg["logging"]["max_bytes_per_file"] == 1000
    assert "auto_updater" in cfg


def test_load_services_config_with_local_override(tmp_path):
    default_cfg = {
        "services": {"online_backend": True, "telegram_bot": True},
        "logging": {"max_bytes_per_file": 1000},
    }
    local_cfg = {
        "services": {"telegram_bot": False},
        "custom_key": "val",
    }
    p_default = tmp_path / "services_config.default.json"
    p_local = tmp_path / "services_config.local.json"

    p_default.write_text(json.dumps(default_cfg), encoding="utf-8")
    p_local.write_text(json.dumps(local_cfg), encoding="utf-8")

    cfg = load_services_config(config_path=str(p_default), local_path=str(p_local))
    assert cfg["services"]["online_backend"] is True
    assert cfg["services"]["telegram_bot"] is False
    assert cfg["custom_key"] == "val"
    assert cfg["logging"]["max_bytes_per_file"] == 1000


def test_load_services_config_missing_files(tmp_path):
    cfg = load_services_config(
        config_path=str(tmp_path / "missing1.json"),
        local_path=str(tmp_path / "missing2.json"),
    )
    assert "services" in cfg
    assert cfg["services"]["google_tasks_bot"] is True
    assert "auto_updater" in cfg
    assert "logging" in cfg


def test_default_services_config_enables_all_four_services():
    cfg = load_services_config(local_path="nonexistent_local.json")
    assert cfg["services"] == {
        "online_backend": True,
        "online_frontend": True,
        "telegram_bot": True,
        "google_tasks_bot": True,
    }

"""Tests for Plan 1a: repo restructure, system_config pattern, launcher, and simple mode."""

import json
import os
import shutil
import subprocess

import pytest

from src.core import config
from src import launcher

ROOT = config.ROOT_DIR


# --- system_config.default.json -> system_config.json pattern ---------------


def test_ensure_system_config_creates_when_missing(tmp_path):
    default = {"openscad_paths": ["X"], "browser": "default"}
    (tmp_path / "system_config.default.json").write_text(json.dumps(default))
    target = config.ensure_system_config(str(tmp_path))
    assert os.path.exists(target)
    assert json.loads(open(target).read()) == default


def test_ensure_system_config_never_overwrites(tmp_path):
    (tmp_path / "system_config.default.json").write_text('{"browser": "default"}')
    (tmp_path / "system_config.json").write_text('{"browser": "firefox"}')
    config.ensure_system_config(str(tmp_path))
    assert json.loads((tmp_path / "system_config.json").read_text())["browser"] == "firefox"


def test_ensure_system_config_no_default_is_noop(tmp_path):
    target = config.ensure_system_config(str(tmp_path))
    assert not os.path.exists(target)


def test_load_system_config_falls_back_on_bad_json(tmp_path):
    (tmp_path / "system_config.default.json").write_text('{"browser": "default"}')
    (tmp_path / "system_config.json").write_text("{not json")
    assert config.load_system_config(str(tmp_path)) == {"browser": "default"}


def test_tracked_default_config_is_valid():
    path = os.path.join(config.CONFIGS_DIR, "system_config.default.json")
    data = json.load(open(path, encoding="utf-8"))
    for key in ("openscad_paths", "slicer_paths", "bambu_studio_paths", "prusa_slicer_paths"):
        assert isinstance(data[key], list) and data[key]


def _is_gitignored(path: str, expected_rule: str) -> bool:
    """Uses `git check-ignore` when git is installed; otherwise checks .gitignore for the rule."""
    git = shutil.which("git")
    if git:
        return subprocess.run([git, "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        rules = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    return expected_rule in rules


def test_system_config_json_is_gitignored():
    assert _is_gitignored("inputs/configs/system_config.json", "inputs/configs/system_config.json")


@pytest.mark.parametrize(
    "path,rule",
    [
        (".env", ".env"),
        ("outputs/sessions/x/session.json", "/outputs/"),
        ("outputs/.cache/google_tasks_token.json", "/outputs/"),
        ("inputs/google_tasks_api.json", "inputs/google_tasks_api.json"),
    ],
)
def test_secrets_and_outputs_gitignored(path, rule):
    assert _is_gitignored(path, rule), f"{path} should be git-ignored"


# --- .env loading --------------------------------------------------------------


def test_load_env_keys_reads_google_tasks(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'GOOGLE_TASKS_CLIENT_ID="abc.apps.googleusercontent.com"\nGOOGLE_TASKS_CLIENT_SECRET=sec # c\n'
    )
    keys = config.load_env_keys(str(env))
    assert keys["GOOGLE_TASKS_CLIENT_ID"] == "abc.apps.googleusercontent.com"
    assert keys["GOOGLE_TASKS_CLIENT_SECRET"] == "sec"


def test_load_env_keys_is_read_only(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=x\n")
    before = env.stat().st_mtime_ns
    config.load_env_keys(str(env))
    assert env.stat().st_mtime_ns == before


def test_shared_utils_load_api_keys_has_google_tasks(tmp_path):
    from src.core.utils import load_api_keys

    env = tmp_path / ".env"
    env.write_text("GOOGLE_TASKS_CLIENT_ID=id1\nGOOGLE_TASKS_CLIENT_SECRET=s1\n")
    keys = load_api_keys(str(env))
    assert keys["GOOGLE_TASKS_CLIENT_ID"] == "id1"
    assert keys["GOOGLE_TASKS_CLIENT_SECRET"] == "s1"


# --- Tool path discovery -------------------------------------------------------


def test_get_openscad_path_uses_system_config(tmp_path, monkeypatch):
    fake = tmp_path / "openscad.exe"
    fake.write_text("")
    monkeypatch.delenv("OPENSCAD_PATH", raising=False)
    monkeypatch.setattr(config, "load_system_config", lambda *_: {"openscad_paths": [str(fake)]})
    assert config.get_openscad_path() == str(fake)


def test_get_openscad_path_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "custom_openscad.exe"
    fake.write_text("")
    monkeypatch.setenv("OPENSCAD_PATH", str(fake))
    assert config.get_openscad_path() == str(fake)


def test_get_slicer_path_expands_env_vars(tmp_path, monkeypatch):
    fake = tmp_path / "orca-slicer.exe"
    fake.write_text("")
    monkeypatch.delenv("SLICER_PATH", raising=False)
    monkeypatch.setenv("T2P_TEST_DIR", str(tmp_path))
    monkeypatch.setattr(
        config,
        "load_system_config",
        lambda *_: {"slicer_paths": ["%T2P_TEST_DIR%\\orca-slicer.exe"]},
    )
    assert os.path.normcase(config.get_slicer_path()) == os.path.normcase(str(fake))


# --- Directory layout ----------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "inputs/ai_prompts/ai_instructions.txt",
        "inputs/ai_prompts/error_prompt.md",
        "inputs/ai_prompts/verify_prompt.md",
        "inputs/configs/agent_config.json",
        "inputs/configs/ai_models.json",
        "inputs/templates/default_project.3mf",
    ],
)
def test_inputs_reorganized(rel):
    assert os.path.exists(os.path.join(ROOT, rel))


@pytest.mark.parametrize(
    "rel",
    [
        "pytest.ini",
        "simple_version",
        "shared/migrate_legacy_sessions.py",
        "inputs/assets",
        "inputs/google_tasks_api.json",
        "inputs/google_tasks_token.json",
        "remote_version/antigravity_files",
        "antigravity_files",
        "online_version",
        "remote_version",
    ],
)
def test_legacy_files_removed(rel):
    assert not os.path.exists(os.path.join(ROOT, rel))


def test_ensure_output_dirs(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUTS_DIR", str(out))
    monkeypatch.setattr(config, "SESSIONS_DIR", str(out / "sessions"))
    monkeypatch.setattr(config, "SNAPSHOTS_DIR", str(out / "snapshots"))
    monkeypatch.setattr(config, "TMP_DIR", str(out / "tmp"))
    monkeypatch.setattr(config, "LOGS_DIR", str(out / "logs"))
    monkeypatch.setattr(config, "CACHE_DIR", str(out / ".cache"))
    config.ensure_output_dirs()
    for sub in ("sessions", "snapshots", "tmp", "logs", ".cache"):
        assert (out / sub).is_dir()


def test_prompts_load_from_new_location(monkeypatch):
    from src.core.ai import validation_loop as ai_call

    monkeypatch.setattr(ai_call, "get_active_ai_provider", lambda: type("P", (), {"model": "m"})())
    cfg = ai_call._load_ai_config()
    assert cfg["instructions"].strip()
    assert cfg["error_template"].strip()
    assert cfg["verify_template"].strip()


def test_ai_models_config_loads_from_configs_dir():
    from src.core.ai.providers import load_provider_config

    cfg = load_provider_config()
    assert cfg["active_provider"] in ("gemini", "chatgpt", "openai", "claude", "anthropic")


# --- start.bat & launcher ------------------------------------------------------


def test_start_bat_is_thin():
    lines = [
        ln
        for ln in open(os.path.join(ROOT, "start.bat"), encoding="utf-8").read().splitlines()
        if ln.strip()
    ]
    assert len(lines) <= 6
    assert any("src.launcher" in ln for ln in lines)


def test_launcher_menu_has_no_clean_option(capsys, monkeypatch):
    monkeypatch.setattr(launcher, "_clear_screen", lambda: None)
    launcher.render_menu()
    out = capsys.readouterr().out
    for n in range(0, 8):
        assert f"[{n}]" in out
    assert "[8]" not in out
    assert "Clean STL" not in out


def test_launcher_invalid_choice_returns_none():
    assert launcher.handle_choice("8") is None
    assert launcher.handle_choice("abc") is None


def test_launcher_exit_choice():
    assert launcher.handle_choice("0") == 0


@pytest.mark.parametrize(
    "choice,func",
    [
        ("1", "run_online"),
        ("2", "run_remote"),
        ("3", "run_tasks"),
        ("4", "run_all"),
        ("5", "run_simple"),
        ("6", "run_manual_print"),
        ("7", "run_stop_printer"),
    ],
)
def test_launcher_dispatch(monkeypatch, choice, func):
    monkeypatch.setattr(launcher, func, lambda: 42)
    assert launcher.handle_choice(choice) == 42


def test_launcher_main_with_arg_skips_menu(monkeypatch):
    monkeypatch.setattr(launcher, "disable_quickedit", lambda: None)
    monkeypatch.setattr(launcher, "ensure_output_dirs", lambda: None)
    monkeypatch.setattr(launcher, "ensure_system_config", lambda: None)
    monkeypatch.setattr(launcher, "run_stop_printer", lambda: 0)
    assert launcher.main(["7"]) == 0


def test_launcher_main_eof_exits(monkeypatch):
    monkeypatch.setattr(launcher, "disable_quickedit", lambda: None)
    monkeypatch.setattr(launcher, "ensure_output_dirs", lambda: None)
    monkeypatch.setattr(launcher, "ensure_system_config", lambda: None)
    monkeypatch.setattr(launcher, "render_menu", lambda: None)

    def _eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert launcher.main([]) == 0


def test_launcher_script_fallbacks_exist():
    for fn in (
        launcher._get_telegram_script,
        launcher._get_tasks_script,
        launcher._get_manual_print_script,
        launcher._get_stop_print_script,
    ):
        assert os.path.exists(fn())
    assert os.path.exists(os.path.join(launcher._get_frontend_dir(), "package.json"))


def test_open_browser_uses_default(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher, "load_system_config", lambda: {"browser": "default"})
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    launcher.open_frontend_browser("http://localhost:5173")
    assert opened == ["http://localhost:5173"]


# --- simple_mode ----------------------------------------------------------------


def test_simple_mode_empty_prompt():
    from src.cli import simple_mode

    assert simple_mode.main("   ") == 1


def test_simple_mode_sanitize_prefix():
    from src.cli.simple_mode import _sanitize_prefix

    assert _sanitize_prefix("Phone Stand!") == "phone_stand"
    assert _sanitize_prefix("") == "model"
    assert _sanitize_prefix(None) == "model"


def test_simple_mode_full_flow_no_print(tmp_path, monkeypatch):
    from src.cli import simple_mode
    import src.core.ai.validation_loop as ai_call

    monkeypatch.setattr(simple_mode, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(simple_mode, "ensure_output_dirs", lambda: None)
    monkeypatch.setattr(simple_mode, "_beep", lambda: None)
    monkeypatch.setattr(ai_call, "get_max_ai_calls", lambda *_: 1)

    prebuilt = tmp_path / "pre.stl"
    prebuilt.write_text("solid x\nendsolid x\n")
    monkeypatch.setattr(
        ai_call,
        "generate_openscad_validated",
        lambda *a, **k: ("cube(10);", "Test Cube", ["Red"], [], str(prebuilt)),
    )
    # Review = Y, ready = N -> cancel before printing
    answers = iter(["Y", "N"])
    monkeypatch.setattr(simple_mode, "_prompt_with_timeout", lambda *a, **k: next(answers))
    monkeypatch.setattr(simple_mode, "get_bambu_studio_path", lambda: "fake_slicer")
    monkeypatch.setattr(simple_mode.subprocess, "Popen", lambda *a, **k: None)

    assert simple_mode.main("a test cube") == 0

    sessions = [d for d in os.listdir(tmp_path) if os.path.isdir(tmp_path / d)]
    assert len(sessions) == 1
    sdir = tmp_path / sessions[0]
    assert "_test_cube_" in sessions[0]
    data = json.loads((sdir / "session.json").read_text())
    assert data["latest_stl"].startswith("/outputs/sessions/")
    assert data["versions"] == 1
    v1 = os.listdir(sdir / "v1")
    assert any(f.endswith(".scad") for f in v1)
    assert any(f.endswith(".stl") for f in v1)


def test_simple_mode_ai_failure(monkeypatch, tmp_path):
    from src.cli import simple_mode
    import src.core.ai.validation_loop as ai_call

    monkeypatch.setattr(simple_mode, "ensure_output_dirs", lambda: None)
    monkeypatch.setattr(ai_call, "get_max_ai_calls", lambda *_: 1)

    def _boom(*a, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr(ai_call, "generate_openscad_validated", _boom)
    assert simple_mode.main("thing") == 1


# --- Google Tasks bot ---------------------------------------------------------


def test_google_tasks_paths_updated():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gtb", os.path.join(ROOT, "src", "bots", "google_tasks", "bot.py")
    )
    gtb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gtb)
    assert gtb.TOKEN_PATH.replace("\\", "/").endswith("outputs/.cache/google_tasks_token.json")
    assert gtb.WORKSPACE_DIR.replace("\\", "/").endswith("outputs/sessions")


def test_google_tasks_auth_uses_env_client_config(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gtb2", os.path.join(ROOT, "src", "bots", "google_tasks", "bot.py")
    )
    gtb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gtb)

    monkeypatch.setattr(gtb, "TOKEN_PATH", str(tmp_path / ".cache" / "tok.json"))
    monkeypatch.setattr(gtb, "CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        gtb,
        "load_api_keys",
        lambda: {"GOOGLE_TASKS_CLIENT_ID": "cid", "GOOGLE_TASKS_CLIENT_SECRET": "csec"},
    )
    captured = {}

    class FakeCreds:
        def to_json(self):
            return '{"token": "t"}'

    class FakeFlow:
        def run_local_server(self, port=0):
            return FakeCreds()

    def fake_from_client_config(cfg, scopes):
        captured["cfg"] = cfg
        return FakeFlow()

    monkeypatch.setattr(gtb.InstalledAppFlow, "from_client_config", fake_from_client_config)
    gtb.authenticate_google_tasks()
    assert captured["cfg"]["installed"]["client_id"] == "cid"
    assert captured["cfg"]["installed"]["client_secret"] == "csec"
    assert (tmp_path / ".cache" / "tok.json").exists()


def test_simple_mode_reuses_prebuilt_stl_and_saves_metrics(tmp_path, monkeypatch):
    import zipfile
    from src.cli import simple_mode
    import src.core.ai.validation_loop as ai_call

    monkeypatch.setattr(simple_mode, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(simple_mode, "ensure_output_dirs", lambda: None)
    monkeypatch.setattr(simple_mode, "_beep", lambda: None)

    recorded_services = []

    def fake_get_max_calls(service=None):
        recorded_services.append(service)
        return 2

    monkeypatch.setattr(ai_call, "get_max_ai_calls", fake_get_max_calls)

    prebuilt_stl = tmp_path / "validate.stl"
    prebuilt_stl.write_text(
        "solid box\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 25.4 0 0\n"
        "      vertex 0 50.8 12.7\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid box\n",
        encoding="utf-8",
    )
    prebuilt_3mf = tmp_path / "validate.3mf"
    with zipfile.ZipFile(prebuilt_3mf, "w") as zf:
        zf.writestr(
            "Metadata/plate_1.gcode",
            b"; total estimated time = 22m 10s\n; TYPE:Tree support\n; filament used [g] = 9.4\n",
        )

    captured_gen_args = {}

    def fake_gen(prompt, history, max_calls, service, **kwargs):
        captured_gen_args["service"] = service
        captured_gen_args["kwargs"] = kwargs
        return ("cube(10);", "Metric Box", ["Blue"], [], str(prebuilt_stl), str(prebuilt_3mf))

    monkeypatch.setattr(ai_call, "generate_openscad_validated", fake_gen)
    # Fail if OpenSCAD subprocess is called redundantly
    monkeypatch.setattr(
        simple_mode,
        "get_openscad_path",
        lambda: (_ for _ in ()).throw(AssertionError("Should not recompile OpenSCAD!")),
    )
    answers = iter(["Y", "N"])
    monkeypatch.setattr(simple_mode, "_prompt_with_timeout", lambda *a, **k: next(answers))
    monkeypatch.setattr(simple_mode, "get_bambu_studio_path", lambda: "fake_slicer")
    monkeypatch.setattr(simple_mode.subprocess, "Popen", lambda *a, **k: None)

    assert simple_mode.main("a metric box") == 0
    assert recorded_services == ["cli"]
    assert captured_gen_args["service"] == "cli"
    assert captured_gen_args["kwargs"].get("keep_temp_artifacts") is True

    sessions = [d for d in os.listdir(tmp_path) if os.path.isdir(tmp_path / d)]
    assert len(sessions) == 1
    sdir = tmp_path / sessions[0]
    v1_dir = sdir / "v1"
    assert (v1_dir / "print.bat").exists()
    assert (v1_dir / "slice.bat").exists()

    sdata = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    assistant_msg = sdata["history"][1]
    assert assistant_msg["time"] == "22m 10s"
    assert assistant_msg["grams"] == 9.4
    assert assistant_msg["has_supports"] is True
    assert "1.00" in assistant_msg["size"]


def test_load_env_keys_os_environ_fallback(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "env_fallback_key_123")
    keys = config.load_env_keys(str(env))
    assert keys["GEMINI_API_KEY"] == "env_fallback_key_123"


def test_run_all_ensures_frontend_and_opens_browser(monkeypatch):
    events = []
    monkeypatch.setattr(launcher, "_clear_screen", lambda: None)
    monkeypatch.setattr(
        launcher,
        "ensure_frontend_ready",
        lambda: (events.append("ensure_frontend") or True, "node_env"),
    )
    monkeypatch.setattr(
        launcher,
        "_open_browser_after_delay",
        lambda url, delay=3.0: events.append(("open_browser", url, delay)),
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda cmd, **kw: type("R", (), {"returncode": 0})(),
    )
    assert launcher.run_all() == 0
    assert events == ["ensure_frontend", ("open_browser", "http://localhost:5173", 3.0)]

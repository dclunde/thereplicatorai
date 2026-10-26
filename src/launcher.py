import os
import subprocess
import sys
import time
import webbrowser

_LAUNCHER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _LAUNCHER_ROOT not in sys.path:
    sys.path.insert(0, _LAUNCHER_ROOT)

from src.core.config import (
    CACHE_DIR,
    ROOT_DIR,
    disable_quickedit,
    ensure_output_dirs,
    ensure_system_config,
    load_system_config,
)

NODE_VERSION = "22.16.0"


def _clear_screen() -> None:
    if sys.platform == "win32":
        os.system("cls")
    else:
        os.system("clear")


def _get_frontend_dir() -> str:
    return os.path.join(ROOT_DIR, "src", "web", "frontend")


def _get_backend_dir() -> str:
    return os.path.join(ROOT_DIR, "src", "web", "backend")


def _get_backend_target() -> str:
    return "src.web.backend.app:app"


def _get_telegram_script() -> str:
    return os.path.join(ROOT_DIR, "src", "bots", "telegram", "bot.py")


def _get_tasks_script() -> str:
    return os.path.join(ROOT_DIR, "src", "bots", "google_tasks", "bot.py")


def _get_manual_print_script() -> str:
    return os.path.join(ROOT_DIR, "src", "cli", "manual_print.py")


def _get_stop_print_script() -> str:
    return os.path.join(ROOT_DIR, "src", "cli", "stop_print.py")


def ensure_frontend_ready() -> tuple[bool, str]:
    """Ensures Node.js (in outputs/.cache/node_env) and frontend node_modules are installed."""
    node_env = os.path.join(CACHE_DIR, "node_env")
    node_exe = (
        os.path.join(node_env, "Scripts", "node.exe")
        if sys.platform == "win32"
        else os.path.join(node_env, "bin", "node")
    )

    if not os.path.exists(node_exe):
        print(f"Installing Node.js {NODE_VERSION} via nodeenv into {node_env}...")
        os.makedirs(CACHE_DIR, exist_ok=True)
        subprocess.run(
            ["uv", "run", "--project", ROOT_DIR, "nodeenv", f"--node={NODE_VERSION}", node_env],
            check=False,
        )
        if not os.path.exists(node_exe):
            print("ERROR: Failed to install Node.js. Check your internet connection.")
            return False, node_env
        print("Node.js installed successfully.")

    scripts_dir = (
        os.path.join(node_env, "Scripts")
        if sys.platform == "win32"
        else os.path.join(node_env, "bin")
    )
    os.environ["PATH"] = (
        f"{scripts_dir}{os.pathsep}{node_env}{os.pathsep}{os.environ.get('PATH', '')}"
    )

    frontend_dir = _get_frontend_dir()
    node_modules = os.path.join(frontend_dir, "node_modules")
    if not os.path.exists(node_modules):
        print("Installing frontend npm packages...")
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        res = subprocess.run([npm_cmd, "install"], cwd=frontend_dir, env=os.environ.copy())
        if res.returncode != 0:
            print("ERROR: npm install failed.")
            return False, node_env

    return True, node_env


def open_frontend_browser(url: str = "http://localhost:5173") -> None:
    sys_cfg = load_system_config()
    browser_pref = str(sys_cfg.get("browser", "default")).strip()
    if browser_pref and browser_pref.lower() != "default":
        try:
            webbrowser.get(browser_pref).open(url)
            return
        except Exception:
            pass
    webbrowser.open(url)


def _open_browser_after_delay(url: str = "http://localhost:5173", delay: float = 3.0) -> None:
    import threading

    def _worker() -> None:
        time.sleep(delay)
        try:
            open_frontend_browser(url)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def launch_online_services() -> bool:
    ok, node_env = ensure_frontend_ready()
    if not ok:
        return False

    frontend_dir = _get_frontend_dir()
    target = _get_backend_target()
    scripts_dir = os.path.join(node_env, "Scripts")

    print("Starting FastAPI Backend...")
    backend_cmd = (
        f'start "Backend API" cmd /c "cd /d "{ROOT_DIR}" && '
        f'uv run --project "{ROOT_DIR}" uvicorn {target} --host 127.0.0.1 --port 8000 '
        f'--reload --reload-dir "{os.path.join(ROOT_DIR, "src")}""'
    )
    os.system(backend_cmd)

    print("Starting Vite Frontend...")
    frontend_cmd = (
        f'start "Frontend UI" cmd /c "set PATH={scripts_dir};{node_env};%PATH% && '
        f'cd /d "{frontend_dir}" && npm run dev"'
    )
    os.system(frontend_cmd)
    return True


def run_online() -> int:
    _clear_screen()
    print("Starting Online Interface...")
    if not launch_online_services():
        return 1

    print("Waiting for Vite server to start...")
    time.sleep(3)
    print("Opening browser...")
    open_frontend_browser("http://localhost:5173")
    print("Both services launched! Close the newly opened window(s) to stop them.")
    return 0


def run_remote() -> int:
    _clear_screen()
    print("Starting Remote Telegram Bot...")
    script = _get_telegram_script()
    res = subprocess.run([sys.executable, script], cwd=ROOT_DIR)
    return res.returncode


def run_tasks() -> int:
    _clear_screen()
    print("Starting Google Tasks Bot...")
    script = _get_tasks_script()
    res = subprocess.run([sys.executable, script], cwd=ROOT_DIR)
    return res.returncode


def run_all() -> int:
    _clear_screen()
    ok, _ = ensure_frontend_ready()
    if not ok:
        return 1
    print(
        "Starting Multi-Service Supervisor (Online Interface + Telegram Bot + Google Tasks Bot)..."
    )
    _open_browser_after_delay("http://localhost:5173", delay=3.0)
    res = subprocess.run([sys.executable, "-m", "src.cli.supervisor"], cwd=ROOT_DIR)
    return res.returncode


def run_simple() -> int:
    _clear_screen()
    from src.cli.simple_mode import main as simple_main

    return simple_main()


def run_manual_print() -> int:
    _clear_screen()
    print("Starting Manual Print...")
    script = _get_manual_print_script()
    res = subprocess.run([sys.executable, script], cwd=ROOT_DIR)
    return res.returncode


def run_stop_printer() -> int:
    _clear_screen()
    print("Stopping Active Print Job...")
    script = _get_stop_print_script()
    res = subprocess.run([sys.executable, script], cwd=ROOT_DIR)
    return res.returncode


def render_menu() -> None:
    _clear_screen()
    print("===================================================")
    print("              TEXT-TO-PRINTER LAUNCHER")
    print("===================================================")
    print(" [1] Start Online Interface (FastAPI + Vite Frontend)")
    print(" [2] Start Remote Telegram Bot")
    print(" [3] Start Google Tasks Bot")
    print(" [4] Start ALL Services (Online + Telegram + Tasks)")
    print(" [5] Run Simple Version (Interactive SCAD to STL)")
    print(" [6] Run Manual Print")
    print(" --------------------------------------------------")
    print(" [7] Stop Printer")
    print(" --------------------------------------------------")
    print(" [0] Exit")
    print("===================================================")
    print()


def handle_choice(choice: str) -> int | None:
    choice = choice.strip()
    if choice == "1":
        return run_online()
    if choice == "2":
        return run_remote()
    if choice == "3":
        return run_tasks()
    if choice == "4":
        return run_all()
    if choice == "5":
        return run_simple()
    if choice == "6":
        return run_manual_print()
    if choice == "7":
        return run_stop_printer()
    if choice == "0":
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    disable_quickedit()
    ensure_output_dirs()
    ensure_system_config()

    args = argv if argv is not None else sys.argv[1:]
    if args:
        rc = handle_choice(args[0])
        if rc is not None:
            return rc

    while True:
        render_menu()
        try:
            choice = input("Select an option [0-7]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0

        rc = handle_choice(choice)
        if rc is not None:
            return rc

        print("Invalid selection. Please try again.")
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())

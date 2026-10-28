# Text-to-Printer: How to Run the Code

This guide provides complete instructions for setting up, configuring, and running the Text-to-Printer repository.

---

## 1. Quick Start (Windows Launcher)

The fastest and easiest way to start Text-to-Printer is using the root launcher:

```bat
start.bat
```

Double-clicking `start.bat` (or executing it from PowerShell / Command Prompt) launches `src/launcher.py` with an interactive menu:

```text
============================================================
              Text-to-Printer Launcher Menu
============================================================
  [1] Start Online Interface (FastAPI + Vite Frontend)
  [2] Start Remote Telegram Bot
  [3] Start Google Tasks Bot
  [4] Start ALL Services (Online + Telegram + Tasks)
  [5] Run Simple Version (Interactive SCAD to STL)
  [6] Run Manual Print
  [7] Stop Printer
  [0] Exit
============================================================
```

You can also pass menu options directly from the command line without opening the interactive menu:
```bat
start.bat 1    # Launch Web UI directly
start.bat 2    # Launch Telegram Bot directly
start.bat 3    # Launch Google Tasks Bot directly
start.bat 4    # Launch ALL services directly
start.bat 5    # Run Simple CLI interactive generator
start.bat 6    # Run Manual Print
start.bat 7    # Stop active print
```

---

## 2. Prerequisites & Environment Setup

### Tools Required
- **Python 3.11+**
- **uv** (Fast Python package manager) — all Python commands use `uv`
- **Node.js & npm** (Node 18+) — bootstrapped into `outputs/.cache/node_env/` or detected on system `PATH`
- **OpenSCAD** (Windows path configured in `inputs/configs/system_config.json` or installed at default `C:\Program Files\OpenSCAD\openscad.exe`)
- **Bambu Studio / OrcaSlicer / PrusaSlicer** (configured in `inputs/configs/system_config.json`)

### First-Time Setup
1. Clone the repository and navigate into the folder:
   ```powershell
   cd text_to_printer
   ```
2. Install Python dependencies using `uv`:
   ```powershell
   uv sync
   ```
3. Install frontend dependencies:
   ```powershell
   npm --prefix src/web/frontend install
   ```
4. Copy `.env.example` to `.env` and fill in your credentials:
   ```powershell
   cp .env.example .env
   ```

---

## 3. Session Naming & Storage Retention (Plan 2)

### Timestamp-First Hybrid Session Naming
All generated sessions in `outputs/sessions/` are saved in chronological order:
```text
outputs/sessions/<YYYYMMDD_HHMMSS>_<model_name>_<short8uuid>/
```
- **During generation**: Initially created as `<timestamp>_generating_<short8uuid>/`.
- **Upon AI completion**: Automatically renamed to `<timestamp>_<model_name>_<short8uuid>/`.
- **Concurrency & Web UI safe**: Permanent UUIDs stored in `session.json["id"]` are resolved in $O(1)$ by suffix or logical ID.

### Configurable 3-Tier Retention Policy
Configured in `inputs/configs/agent_config.json` under `session_retention`:
```json
"session_retention": {
  "auto_purge_enabled": true,
  "keep_all_versions_sessions": 15,
  "keep_latest_version_sessions": 30
}
```
- **Tier 1 (Ranks 1–15, Newest)**: Keeps all versions (`.stl`, `.3mf`, `.png`, `.scad`, `session.json`).
- **Tier 2 (Ranks 16–30)**: Keeps only the most recently active/rendered version (`last_active_version` or newest `.stl` mtime). Older versions' heavy binary files are purged, while keeping all `.scad` files.
- **Tier 3 (Ranks 31+)**: Heavy binary files (`.stl`, `.3mf`, `.png`) are purged across all versions. All `.scad` and `session.json` files are kept forever for instant 1-click re-rendering.
- **Re-render Promotion**: Re-rendering any session updates `last_updated = now()` and `last_active_version = target_version`, immediately promoting that session back to Rank #1 (Top 15) and protecting the chosen version.
- **Stale Cleanups**: Leftover `outputs/tmp/job_*` folders older than 1 hour are automatically swept, and camera snapshots in `outputs/snapshots/` keep the latest 5 `.jpg` images.

---

## 4. Running Individual Services

If you prefer to run services manually instead of using `start.bat`:

### A. Web Interface (FastAPI Backend + React Frontend)
- **Backend API**:
  ```powershell
  uv run python -m src.web.backend.app
  ```
  Accessible at: `http://127.0.0.1:8000` (docs at `http://127.0.0.1:8000/docs`).

- **Frontend (Vite Development Server)**:
  ```powershell
  npm --prefix src/web/frontend run dev
  ```
  Accessible at: `http://localhost:5173`.

### B. Remote Telegram Bot
```powershell
uv run python -m src.bots.telegram.bot
```

### C. Google Tasks Bot
```powershell
uv run python -m src.bots.google_tasks.bot
```

### D. Simple Mode (Terminal SCAD Generator & Auto-Print)
```powershell
uv run python -m src.cli.simple_mode
```

### E. Manual Print (CLI / Slicer Tool)
```powershell
# Interactive manual print
uv run python -m src.cli.manual_print

# Non-interactive CLI print
uv run python -m src.cli.manual_print "path/to/model.stl" "Red" --slot 0 --support
```

### F. Stop Printer Immediately
```powershell
uv run python -m src.cli.stop_print
```

### G. Unified Multi-Service Supervisor & Auto-Updater (Cross-Platform)
Runs the configured set of services (Backend, Frontend, Telegram, Google Tasks) under a single master supervisor with automatic restart recovery, rotating file logging, and nightly git auto-updating:
```bash
# Universal (Linux / macOS / Windows / Container):
uv run python -m src.cli.supervisor

# Linux / macOS shell script:
./start.sh

# Filter specific services:
uv run python -m src.cli.supervisor --only telegram
uv run python -m src.cli.supervisor --services backend,telegram

# Inspect live status, restarts, and logs:
uv run python -m src.cli.supervisor --status
uv run python -m src.cli.supervisor --restarts
uv run python -m src.cli.supervisor --logs telegram --lines 50
```

---

## 5. Running Quality Checks & Tests

Before submitting any code changes, all 5 mandatory checks must pass clean:

```powershell
# 1. Python Linting Check
uv run ruff check .

# 2. Python Formatting Check
uv run ruff format --check .

# 3. Full Python Test Suite (358 tests)
uv run pytest tests/

# 4. Frontend ESLint
npm --prefix src/web/frontend run lint

# 5. Frontend Unit Tests (Vitest)
npm --prefix src/web/frontend run test
```

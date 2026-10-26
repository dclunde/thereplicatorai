# File Guide

Directory layout and rules for the Text-to-Printer repository.

## Root
- `start.bat` / `start.sh`: launchers (thin wrappers around `src/launcher.py`).
- `.env.example`: template for credentials. Copy to `.env` (git-ignored).
- `pyproject.toml`, `uv.lock`: Python project and locked dependencies (managed with `uv`).
- `README.md`: overview and how to run.
- `LICENSE`: GPL-3.0.
- Keep the root clean: no scripts, logs or generated files.

## `src/`
- `core/`: business logic
  - `config.py`, `config_loader.py`: paths, `.env` loading, JSON configs.
  - `ai/`: `providers.py` (Gemini/OpenAI/Anthropic), `validation_loop.py`, `prompts.py`, `json_repair.py`.
  - `slicer/`: `openscad.py`, `slicer_3mf.py`, `mesh_validator.py`.
  - `printer/`: `bambu_client.py` (MQTT, FTP, camera, locks), `color_matcher.py`.
  - `sessions.py`: session storage and retention.
  - `supervisor.py`, `supervisor_logging.py`, `auto_updater.py`, `activity_tracker.py`, `utils.py`.
- `web/`
  - `backend/`: FastAPI (`app.py`, `jobs.py`, `schemas.py`, `routes/sessions.py`, `routes/printer.py`, `routes/supervisor.py`).
  - `frontend/`: React + Vite (`src/App.jsx`, `components/`, `hooks/`, `STLViewer.jsx`), Vitest tests, Playwright E2E.
- `bots/`: `telegram/` (bot, design and printer handlers, monitor) and `google_tasks/` (queue bot).
- `cli/`: `simple_mode.py`, `manual_print.py`, `stop_print.py`, `supervisor.py`.
- `launcher.py`: the interactive menu behind `start.bat`.

## `inputs/` (tracked, contains no personal data)
- `ai_prompts/`: `ai_instructions.txt`, `error_prompt.md`, `verify_prompt.md`.
- `configs/`: `agent_config.json`, `ai_models.json`, `services_config.default.json`, `system_config.default.json`. A local `system_config.json` is auto-created and git-ignored.
- `templates/`: `default_project.3mf` (PLA slicing template).

## `outputs/` (100% git-ignored runtime state)
- `sessions/`: generated models, one folder per session.
- `snapshots/`, `tmp/`, `logs/`, `.cache/` (Node bootstrap, Google Tasks token).

## `scripts/`
- `diagnostics.py`: environment and hardware diagnostics.
- `github_cli.py`: maintainer helper around the GitHub CLI.
- `systemd/`, `launchd/`: service templates (not officially supported yet).

## `tests/`
Pytest suite (`uv run pytest tests/`). See `antigravity_files/testing.md`.

## `antigravity_files/` (documentation)
- `docs/`: feature documentation (01 to 08).
- `project_docs/`: `INSTALL.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`.
- `api_reference/`: Bambu Lab Cloud API notes.
- `readme.md`: detailed how-to-run guide.
- `testing.md`, `testing/testing_rules.md`: test matrix and rules.
- `file_guide.md`: this file.

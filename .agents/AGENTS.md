# Agent Guide (text_to_printer)

Welcome! This is a quick guide to help you navigate the Text-to-Printer project efficiently.

## Mandatory Quality Checks (CRITICAL)
Every time code is edited, the following commands **MUST** be run and verified clean before concluding work or pushing changes:

### Python Backend & Core
1. `uv run ruff check .` (Linting check)
2. `uv run ruff format .` (Code formatting — use `uv run ruff format --check .` to verify)
3. `uv run pytest tests/` (Full Python test suite)

### Frontend Web UI (`src/web/frontend`)
4. `npm --prefix src/web/frontend run lint` (ESLint check)
5. `npm --prefix src/web/frontend run test` (Vitest unit tests)

## Core Architecture
- **`src/core/`**: The backbone of the project. Contains `printer/bambu_client.py` (MQTT status/snapshot, FTP uploads, dispatch locking), `slicer/slicer_3mf.py` & `slicer/openscad.py` (Slicer geometry injection via Prusa/OrcaSlicer and OpenSCAD compilation), `ai/providers.py` (Multi-AI provider abstraction for Gemini, OpenAI, and Anthropic), `ai/validation_loop.py` (LLM OpenSCAD generation and validation loop), `sessions.py` (Hybrid timestamp-first session manager & 3-tier retention), and `config.py` (Centralized paths & `.env` loading).
- **`src/web/`**: The primary web interface.
  - `backend/app.py`: FastAPI server bridging the frontend to `src/core/` logic (`routes/sessions.py`, `routes/printer.py`, `routes/supervisor.py`).
  - `frontend/src/App.jsx`: React frontend coordinating modular components (`components/`) and hooks (`hooks/`) for sessions, status, and 3D previews.
- **`src/bots/`**: Remote bot interfaces (`telegram/bot.py` and `google_tasks/bot.py`) relying on `src/core/`.
- **`src/cli/`**: Command-line entrypoints (`simple_mode.py`, `manual_print.py`, `stop_print.py`, `supervisor.py`) and `src/launcher.py`.
- **`inputs/templates/`**: Contains crucial templates like `default_project.3mf` used for injecting geometry during slicing.

## Development Rules
1. **Clean Root Directory**: The root must only contain configuration (`.env`, `.env.example`), runners (`start.bat`, `start.sh`), and core documentation (`README.md`, `antigravity_files/file_guide.md`, this file). Do not clutter it with scripts or logs. See `antigravity_files/file_guide.md` for strict directory layout rules.
2. **Documentation & Planning**: Any new documentation or tests must go into the `antigravity_files/` directory. Implementation plans MUST be saved in `antigravity_files/plans_todo/` using a descriptive, context-specific filename (e.g., `cancel_print_plan.md`). Furthermore, when creating an implementation plan, you must ask tons of clarifying questions and outline any open questions in the plan to ensure full alignment before execution.
3. **API Keys**: `.env` is loaded dynamically via `src/core/config.py`. Never hardcode keys.
4. **Use `uv`**: This project uses `uv` as its Python package manager. Always prefer `uv` over `pip` or raw `python`. Use `uv run` to execute scripts (e.g., `uv run python -m src.bots.telegram.bot`), `uv add` to install dependencies, and `uv sync` to set up the environment. Never use `pip install` directly.
5. **Mandatory Quality Checks**: Always run and verify clean all mandatory Python and Frontend checks listed above whenever code is edited.

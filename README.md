# Text-to-Printer

This project consists of an interactive AI-powered text-to-printer pipeline, enabling a user to talk to an AI (Gemini, ChatGPT, or Claude) to generate and edit 3D models (OpenSCAD -> STL), and then directly slice and send them to a 3D printer.

## Organization
```
start.bat            # 5-line wrapper -> python -m src.launcher
.env / .env.example  # All personal credentials (read-only for the app, git-ignored)
src/                 # Canonical source root
  core/              # AI providers, Bambu printer/cloud, config, sessions, slicer
  web/               # FastAPI backend (web/backend) + React frontend (web/frontend)
  bots/              # Telegram bot (bots/telegram) + Google Tasks bot (bots/google_tasks)
  cli/               # CLI tools (launcher.py, simple_mode.py, manual_print.py, stop_print.py)
inputs/
  ai_prompts/        # ai_instructions.txt, error_prompt.md, verify_prompt.md
  configs/           # agent_config.json, ai_models.json, system_config.default.json
                     # system_config.json (auto-created, git-ignored, never overwritten)
  templates/         # default_project.3mf
outputs/             # 100% git-ignored runtime state
  sessions/          # All generated model sessions (formerly created_stls/)
  snapshots/ tmp/ logs/
  .cache/            # node_env/, google_tasks_token.json
tests/               # Pytest suite
antigravity_files/   # Documentation (docs/, project_docs/, testing.md, file_guide.md)
```

## Documentation

| Doc | Topic |
|---|---|
| [01 System architecture](antigravity_files/docs/01_system_architecture.md) | Pipeline, modules, session storage |
| [02 Multi-AI & agent loop](antigravity_files/docs/02_multi_ai_and_agent_loop.md) | Gemini / ChatGPT / Claude, validation loop |
| [03 Web interface](antigravity_files/docs/03_web_interface_guide.md) | React UI and REST API |
| [04 Telegram bot](antigravity_files/docs/04_telegram_bot_guide.md) | Remote design and printing |
| [05 Google Tasks bot](antigravity_files/docs/05_google_tasks_bot_guide.md) | Autonomous print queue |
| [06 Slicing & 3MF](antigravity_files/docs/06_slicing_and_3mf_pipeline.md) | OpenSCAD to sliced 3MF |
| [07 Mesh validation](antigravity_files/docs/07_mesh_geometry_validation.md) | Floating parts, thin walls |
| [08 Bambu & safety](antigravity_files/docs/08_bambu_and_safety_guide.md) | LAN/Cloud mode, safety |

No printer? The app still works in **Design & Download mode**: generate, preview and download STL/3MF files without Bambu credentials (see doc 08 for exactly what the code does today).

Licensed under [GPL-3.0](LICENSE).
## How to Run the Code

Double-click `start.bat` or run it from a command prompt:

```cmd
start.bat
```

You can also skip the menu by passing an option number, e.g. `start.bat 1`.

The interactive menu options are:
- `[1]` **Start Online Interface**: Bootstraps Node.js into `outputs/.cache/node_env`, installs npm dependencies, launches the FastAPI backend (`127.0.0.1:8000`) and Vite frontend (port 5173), then opens your browser.
- `[2]` **Start Remote Telegram Bot**
- `[3]` **Start Google Tasks Bot**
- `[4]` **Start ALL Services**: Online Interface + Telegram Bot + Google Tasks Bot in separate windows.
- `[5]` **Run Simple Version**: Prompt → AI OpenSCAD → STL saved to `outputs/sessions/<timestamp>_<name>_<id>/v1/` → optional review in your slicer → print.
- `[6]` **Run Manual Print**
- `[7]` **Stop Printer**
- `[0]` **Exit**

### Prerequisites & Configuration
- [uv](https://docs.astral.sh/uv/) - manages Python and all dependencies automatically (`pyproject.toml`)
- Node.js & npm (bootstrapped automatically by the launcher via nodeenv)
- OpenSCAD and OrcaSlicer / Bambu Studio (PrusaSlicer optional)
- Copy `.env.example` to `.env` and fill in your keys:
  - `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
  - `TELEGRAM_BOT_TOKEN` (Telegram bot)
  - `GOOGLE_TASKS_CLIENT_ID` / `GOOGLE_TASKS_CLIENT_SECRET` (Google Tasks bot — the login token is cached in `outputs/.cache/google_tasks_token.json`)
  - `BAMBU_*` printer credentials
- Active AI provider & model: `inputs/configs/ai_models.json`
- Tool install paths: on first run `inputs/configs/system_config.json` is copied from `system_config.default.json`. Edit it freely if your OpenSCAD / slicer live elsewhere — it is git-ignored and never overwritten. You can also set `OPENSCAD_PATH` / `SLICER_PATH` environment variables, and choose a browser with `"browser"`.

See [antigravity_files/project_docs/INSTALL.md](antigravity_files/project_docs/INSTALL.md) for detailed setup instructions.

## Code Quality & Testing

Every time code is edited in this repository, run the mandatory quality checks before pushing or concluding work:

### Python Backend & Shared
```bash
uv run ruff check .       # 1. Python Linting
uv run ruff format .      # 2. Python Formatting (or use --check to verify)
uv run pytest tests/     # 3. Full Automated Python Test Suite
```

### Frontend Web UI (`src/web/frontend`)
```bash
npm --prefix src/web/frontend run lint       # 4. ESLint Check
npm --prefix src/web/frontend run test       # 5. Vitest Unit Tests
npm --prefix src/web/frontend run test:e2e   # 6. Playwright End-to-End Tests (Chromium)
```

## Supports Control

Supports can be enabled pre-design or overridden at print-time across all interfaces:

### Telegram Bot
- **Pre-Design**: Add `--support` anywhere in your `/make` or `/design` command (e.g., `/design a hook --support` or `/make --support a mini dragon`).
- **Edits**: Edits inherit the previous support setting by default. You can override it anytime using `--support` or `--no-support` (e.g., `/edit make it taller --no-support`).
- **Print-Time**: Override support settings directly at print execution: `/print <slot> [--support|--no-support]` (e.g., `/print 1 --support`). If the requested support state differs from the current slice, the model is automatically re-sliced on the fly before sending to the printer.
- **Slot Selection**: Choose AMS slots `0`-`3` or print from the **External Spool** by specifying slot `4`, `ext`, or `external` (e.g., `/print 4` or `/print ext --support`).
- **Help Guidance**: Running `/print` without arguments displays available AMS slots (0–3), the External Spool (Slot 4), colors, and syntax hints (`/print <slot> [--support]`).

### Online Web UI
- **Pre-Design**: Use the `🛠️ Supports (ON/OFF)` toggle switch above the message input box, or type `--support` / `--no-support` directly in your prompt.
- **Print Settings Modal**: When clicking **Print**, choose an AMS slot (0–3), or select **Ext (Slot 4)** to print from the external spool holder, and toggle `Supports: [ Off ] [ 🛠️ On ]` before confirming.
- **Model-Ready Notification**: Plays a gentle Web Audio chime automatically whenever a 3D model finishes generating or re-rendering.


### Google Tasks Bot
- Configured independently in `inputs/configs/agent_config.json`:
  ```json
  {
    "google_tasks_enable_supports": false
  }
  ```
  This configuration isolated specifically to Google Tasks autonomous jobs and does not alter Telegram, Online, or CLI behavior.

## Mesh Geometry Validation

The system automatically analyzes the generated STL meshes before sending them to the printer. This analysis detects structural bottlenecks, disconnected bodies (floating artifacts), and models that start in mid-air (Z > 0mm).

The Gemini agent can output a `"expected_parts_count"` key in its JSON response (default 1). If the generated mesh has more disconnected components than expected, it triggers a geometry error and feeds back to the AI for correction.

## Important Safety Notice: PLA Only
This entire codebase and slicing pipeline is **strictly limited to PLA filaments**.
The `default_project.3mf` used to inject model geometry is pre-configured with specific temperatures and cooling settings for PLA. Attempting to print with higher-temperature materials like PETG or ABS using these sliced files could cause severe printer clogs or failures.

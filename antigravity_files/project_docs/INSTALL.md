# Installation Guide (Windows)

Text-to-Printer turns a text prompt into a printable 3D model: AI → OpenSCAD → STL → sliced 3MF → Bambu Lab printer.
You can use it **without a printer** (Design & Download mode): generate, preview and download STL/3MF files.

## 1. Prerequisites

Open PowerShell and install the tools below.

| Tool | Why | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Manages Python and every Python dependency | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| [OpenSCAD](https://openscad.org/downloads.html) | Renders `.scad` to `.stl` | `winget install OpenSCAD.OpenSCAD` |
| [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer/releases) or Bambu Studio | Slices STL into a printable `.3mf` | `winget install SoftFever.OrcaSlicer` (or `winget install BambuLab.BambuStudio`) |
| Git | Clone the repo | `winget install Git.Git` |

Node.js is **not** required up front. The launcher bootstraps a private copy into `outputs/.cache/node_env`.
Restart your terminal after installing `uv` so it is on your `PATH`.

## 2. Get the code

```powershell
git clone https://github.com/dclunde/thereplicatorai.git
cd thereplicatorai
uv sync
```

`uv sync` creates `.venv` and installs the dependencies declared in `pyproject.toml` (it downloads a suitable Python if needed).

## 3. Configure `.env`

```powershell
copy .env.example .env
```

Open `.env` and fill in **at least one** AI provider key. Use the key that matches `active_provider` in `inputs/configs/ai_models.json`:

| Variable | Needed for |
|---|---|
| `GEMINI_API_KEY` | Google Gemini (free key from [Google AI Studio](https://aistudio.google.com/)) |
| `OPENAI_API_KEY` | OpenAI ChatGPT models |
| `ANTHROPIC_API_KEY` | Anthropic Claude models |
| `BAMBU_PRINTER_IP`, `BAMBU_ACCESS_CODE`, `BAMBU_DEVICE_SERIAL` | Optional: direct printing over your LAN |
| `BAMBU_USERNAME`, `BAMBU_PASSWORD`, `BAMBU_DEVICE_SERIAL` | Optional: direct printing through Bambu Cloud |
| `TELEGRAM_BOT_TOKEN` | Optional: Telegram bot ([guide](../docs/04_telegram_bot_guide.md)) |
| `GOOGLE_TASKS_CLIENT_ID`, `GOOGLE_TASKS_CLIENT_SECRET` | Optional: Google Tasks bot ([guide](../docs/05_google_tasks_bot_guide.md)) |

`.env` is git-ignored. Never commit it or share it.

### Tool locations
On first run `inputs/configs/system_config.json` is copied from `system_config.default.json`. Edit it if OpenSCAD or your slicer are installed in a non-default location. You can also set the `OPENSCAD_PATH` / `SLICER_PATH` environment variables.

## 4. Run

```cmd
start.bat
```

Pick **[1] Start Online Interface**, or run `start.bat 1` to skip the menu. The launcher starts the backend on `http://127.0.0.1:8000` and the web UI on `http://localhost:5173` and opens your browser.
The backend only listens on `127.0.0.1`, so other devices on your network cannot reach it.

Other menu entries: Telegram bot, Google Tasks bot, all services, simple CLI mode, manual print, stop printer.

## 5. Verify the install

```powershell
uv run python scripts/diagnostics.py --all
uv run pytest tests/
```

`diagnostics.py` reports which `.env` keys are missing, whether OpenSCAD and a slicer were found, and (if configured) printer connectivity.

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `uv` not found | Restart the terminal, or add `uv` to your `PATH`. |
| OpenSCAD or slicer not detected | Set the path in `inputs/configs/system_config.json`. |
| "No API key" errors | Check the key in `.env` matches `active_provider` in `inputs/configs/ai_models.json`. |
| Import errors | Run `uv sync` from the project root. |
| Printer shows as not connected | Verify IP, access code and serial; the printer and PC must be on the same network (LAN mode). See [Bambu guide](../docs/08_bambu_and_safety_guide.md). |

## 7. Safety

The slicing template is tuned for **PLA only**. Read [SECURITY.md](SECURITY.md) and the [Bambu & safety guide](../docs/08_bambu_and_safety_guide.md) before printing unattended.

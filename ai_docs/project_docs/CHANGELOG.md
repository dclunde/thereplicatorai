# Changelog

All notable changes to Text-to-Printer are documented here.

## [0.1.0] - Initial public release

### Features
- **AI model generation** from natural-language prompts with a choice of providers: Google Gemini, OpenAI ChatGPT, Anthropic Claude (`inputs/configs/ai_models.json`).
- **Validation loop** with configurable agent levels: compile-error retry, verification against the request, physical-size and print-time limits.
- **Mesh geometry validation**: bed-contact check, disconnected-part detection, thin-wall detection.
- **OpenSCAD → STL → 3MF pipeline** with headless slicing (OrcaSlicer / Bambu Studio / PrusaSlicer) and geometry injection into `inputs/templates/default_project.3mf`; optional tree supports.
- **AMS colour matching** against live printer tray data.
- **Bambu Lab printing** over LAN or Bambu Cloud: status, camera snapshot, chamber-light auto-off timer, stop print.
- **Web UI** (FastAPI + React): chat-style design, versions, 3D viewer, concurrent jobs, print settings modal.
- **Telegram bot**: design, edit, print, status, snapshot, stop, STL upload.
- **Google Tasks bot**: autonomous print queue from a task list.
- **Supervisor** with automatic restarts and rotating logs (`src/cli/supervisor.py`).
- **Session storage** under `outputs/sessions/` with a 3-tier retention policy.
- **Design & Download mode**: everything except printing works without Bambu credentials.

### Safety
- Backend launched on `127.0.0.1` only.
- Slicing template is PLA only.

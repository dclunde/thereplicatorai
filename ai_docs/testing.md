# Testing Guide & Quality Assurance Matrix

This document is the single authoritative testing reference for **Text-to-Printer**, covering the mandatory quality checks, test prioritization matrix (Core vs Non-Core), and execution commands.

---

## 1. Mandatory Quality Checks (CRITICAL)

Whenever any code is edited, the following 5 commands **MUST** be run and confirmed 100% clean:

```powershell
# 1. Python Linting Check
uv run ruff check .

# 2. Python Code Formatting Check
uv run ruff format --check .

# 3. Full Python Test Suite (400 tests across 42 test modules)
uv run pytest tests/

# 4. Frontend ESLint Check
npm --prefix src/web/frontend run lint

# 5. Frontend Vitest Suite (62 unit tests)
npm --prefix src/web/frontend run test
```

---

## 2. Test Prioritization Matrix (Core vs. Non-Core)

Tests are organized into two tiers: **Core Tests** (Priority 1 — blocking for CI and merges) and **Non-Core Tests** (Priority 2 — extended integration, CLI, and edge-case coverage).

| Priority | Test Suite | Description & Scope | Command |
|---|---|---|---|
| **Core (Priority 1)** | `tests/test_public_safety.py` | Public release safety: 127.0.0.1 localhost-only binding, restricted CORS, graceful unconfigured Bambu/Telegram/Tasks guidance, zero personal paths/keys | `uv run pytest tests/test_public_safety.py` |
| **Core (Priority 1)** | `tests/unit/test_session_manager.py` | Plan 2: Timestamp-first session naming (`<YYYYMMDD_HHMMSS>_<model_name>_<short8uuid>`), $O(1)$ lookup, 3-tier auto-purge (15/30 rule), `.bat` preservation, re-render promotion, and stale `slice_`/`ttp_validate_`/`job_` tmp sweeping | `uv run pytest tests/unit/test_session_manager.py` |
| **Core (Priority 1)** | `tests/test_plan_1a.py` | Plan 1a: Repository layout, ultra-thin launcher, `system_config.default.json` safe copy, `.env` loading + `os.environ` fallback, `simple_mode` prebuilt STL reuse & metrics saving | `uv run pytest tests/test_plan_1a.py` |
| **Core (Priority 1)** | `tests/test_plan_1b.py` | Plan 1b: Atomic session CRUD, heavy asset purging, slicer CWD isolation, color matcher, cross-process dispatch lock held until MQTT busy transition, AI prompts & validation loop | `uv run pytest tests/test_plan_1b.py` |
| **Core (Priority 1)** | `tests/test_plan_1c.py` | Plan 1c: Decomposed web backend, modular Telegram & Google Tasks bots, active job tracker, unified diagnostics CLI | `uv run pytest tests/test_plan_1c.py` |
| **Core (Priority 1)** | `tests/test_online_backend_flows.py` | Web backend session flows, lazy-loading pagination, search filtering, HTTP 400 offline & HTTP 409 busy print rejection, purged/re-rendered & bot session printing, validation warnings persistence, per-version support status | `uv run pytest tests/test_online_backend_flows.py` |
| **Core (Priority 1)** | `tests/test_online_backend_e2e.py` | Full end-to-end FastAPI backend session generation, slicing, and print dispatch | `uv run pytest tests/test_online_backend_e2e.py` |
| **Core (Priority 1)** | `tests/test_telegram_bot_flows.py` | Telegram bot commands (`/start`, `/make`, `/design`, `/edit`, `/cancel`, `/status`, `/print`), `has_supports` tree support detection, `session.json` sliced metric persistence, monitor transitions from any busy state | `uv run pytest tests/test_telegram_bot_flows.py` |
| **Core (Priority 1)** | `tests/test_google_tasks_bot.py` | Google Tasks OAuth session caching, hybrid timestamp-first folder naming, and printer-busy skip behavior | `uv run pytest tests/test_google_tasks_bot.py` |
| **Core (Priority 1)** | `tests/test_ai_providers.py` | Multi-provider abstraction for Gemini, OpenAI, and Anthropic Claude | `uv run pytest tests/test_ai_providers.py` |
| **Core (Priority 1)** | `tests/test_ai_call_behavior.py` | Multi-agent generation loop state transitions and design constraint injection on both initial and edit turns | `uv run pytest tests/test_ai_call_behavior.py` |
| **Core (Priority 1)** | `tests/test_ai_call_intensive.py` | AI loop generation, prompt parsing, JSON repair, constraint enforcement, provider fallbacks | `uv run pytest tests/test_ai_call_intensive.py` |
| **Core (Priority 1)** | `tests/test_bambu_cloud_behavior.py` | Bambu MQTT telemetry parsing (isolated `ams`, `vt_tray`, `lights_report`), multi-AMS support, RTSPS snapshot fast-fail & URL quoting | `uv run pytest tests/test_bambu_cloud_behavior.py` |
| **Core (Priority 1)** | `tests/test_bambu_printer_and_cloud_intensive.py` | Slicing delegation, OrcaSlicer auto-orient fallback, FTPS upload, Bambu status MQTT, snapshot capture | `uv run pytest tests/test_bambu_printer_and_cloud_intensive.py` |
| **Core (Priority 1)** | `tests/test_bambu_printer_cli.py` | Manual print CLI parsing, support flags, AMS slot selection, 3MF reuse | `uv run pytest tests/test_bambu_printer_cli.py` |
| **Core (Priority 1)** | `tests/test_stop_print.py` | Emergency print cancellation endpoint and Telegram `/stop_print` across `RUNNING`, `PREPARE`, `PAUSE`, and `PAUSED` states | `uv run pytest tests/test_stop_print.py` |
| **Core (Priority 1)** | `tests/test_mesh_validator.py` | Trimesh voxel & manifold geometry validation, bed contact, floating islands, and build volume bounds | `uv run pytest tests/test_mesh_validator.py` |
| **Core (Priority 1)** | `tests/test_resilient_failures.py` | System resilience across failure modes (OpenSCAD 300s generator & rerender timeouts, 429 fallback, 0-vertex guard, O(1) STL streaming) | `uv run pytest tests/test_resilient_failures.py` |
| **Core (Priority 1)** | `tests/test_activity_tracker.py` | Cross-process `busy_activity` lease tracking with `filelock`, dispatch lock idle gating, and Bambu printer busy checks | `uv run pytest tests/test_activity_tracker.py` |
| **Core (Priority 1)** | `tests/test_auto_updater.py` | Cross-platform git fetch/diff, fast-forward pull, idle gating, dependency sync | `uv run pytest tests/test_auto_updater.py` |
| **Core (Priority 1)** | `tests/test_supervisor_lifecycle.py` | ProcessSupervisor child process lifecycle, signal propagation, and re-entrant `RLock` watchdog backoff restart safety | `uv run pytest tests/test_supervisor_lifecycle.py` |
| **Core (Priority 1)** | `tests/test_circuit_breaker.py` | Rapid crash loop protection, exponential backoff ($2\text{s} \to 60\text{s}$), manual reset | `uv run pytest tests/test_circuit_breaker.py` |
| **Core (Priority 1)** | `tests/test_supervisor_logging.py` | JSONL event schema generation and rotating file handler limits | `uv run pytest tests/test_supervisor_logging.py` |
| **Core (Priority 1)** | `tests/test_config_loader.py` | Deep-merging configuration loader for `*.default.json` and `*.local.json` | `uv run pytest tests/test_config_loader.py` |
| **Core (Priority 1)** | 	ests/test_slicer_relative_paths.py | Direct Python print dispatch, relative path normalization in slicer/CLI, re-slice support notices, zero batch scripts in sessions, start.bat uniqueness | uv run pytest tests/test_slicer_relative_paths.py |
| **Frontend Core (Priority 1)** | `src/web/frontend/src/__tests__/App.test.jsx` | 62 Vitest unit tests covering `sessionUtils.js` (`computeRequiredGrams`, `formatSupportStatus`, `isPrinterStateBusy`, pagination, status resolution) and `audioUtils.js` (`playModelReadySound` cooldown & Web Audio fallback) | `npm --prefix src/web/frontend run test` |
| **Frontend E2E (Priority 1)** | `src/web/frontend/e2e/app_mocked.spec.js` | Playwright mocked browser E2E suite verifying full UI workflow, session creation, version switching, and modals | `npx --prefix src/web/frontend playwright test e2e/app_mocked.spec.js` |
| **Non-Core (Priority 2)** | `tests/test_slicer_and_utils_flows.py` | Slicer time formatting, 3D dimension bounding box calculation, G-code `has_supports` toolpath detection | `uv run pytest tests/test_slicer_and_utils_flows.py` |
| **Non-Core (Priority 2)** | `tests/test_ai_outputs.py` | AI JSON parser resilience and single-pass regex fallback unescaping (`\\n` preservation) | `uv run pytest tests/test_ai_outputs.py` |
| **Non-Core (Priority 2)** | `tests/test_ai_physical_metadata.py` | Extraction of bounding box dimensions, print time, and grams into verification/error prompts | `uv run pytest tests/test_ai_physical_metadata.py` |
| **Non-Core (Priority 2)** | `tests/test_cli_and_utilities.py` | GitHub CLI wrapper, diagnostics scripts, subprocess importability of CLI modules without `PYTHONPATH` | `uv run pytest tests/test_cli_and_utilities.py` |
| **Non-Core (Priority 2)** | `tests/test_services_intensive.py` | Intensive edge cases in backend session search, snapshot capture, Telegram callback queries, Google Tasks failure reporting | `uv run pytest tests/test_services_intensive.py` |
| **Non-Core (Priority 2)** | `tests/test_chamber_light_timer.py` | Chamber LED auto-off timer and active-print safety guard | `uv run pytest tests/test_chamber_light_timer.py` |
| **Non-Core (Priority 2)** | `tests/test_concurrent_generation.py` | Thread-safe `ACTIVE_JOBS` tracking across concurrent Web UI sessions | `uv run pytest tests/test_concurrent_generation.py` |
| **Non-Core (Priority 2)** | `tests/test_supervisor_api.py` & `tests/test_supervisor_cli.py` | Supervisor REST API endpoints (`/api/supervisor/*`) and CLI flags | `uv run pytest tests/test_supervisor_api.py tests/test_supervisor_cli.py` |
| **Non-Core (Priority 2)** | `tests/test_match_color.py` & `tests/test_utils_behavior.py` | Standalone color matching, hex normalization, and STL dimension helpers | `uv run pytest tests/test_match_color.py tests/test_utils_behavior.py` |
| **Non-Core (Priority 2)** | `tests/test_telegram_e2e.py` & `tests/test_telegram_stl_upload.py` | Simulated end-to-end Telegram conversation and STL file upload into `v1/` with hybrid naming | `uv run pytest tests/test_telegram_e2e.py tests/test_telegram_stl_upload.py` |
| **Non-Core (Priority 2)** | `tests/test_slicer_integration.py`, `tests/test_pipeline.py`, `tests/test_e2e_cloud_print.py` | Slicer and cloud print pipeline integration tests | `uv run pytest tests/test_slicer_integration.py tests/test_pipeline.py tests/test_e2e_cloud_print.py` |
| **Non-Core (Priority 2)** | `tests/test_system_recovery.py` & `tests/test_sanitize_id.py` | System recovery on process failure and path traversal prevention on session IDs | `uv run pytest tests/test_system_recovery.py tests/test_sanitize_id.py` |

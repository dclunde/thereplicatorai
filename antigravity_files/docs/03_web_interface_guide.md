# Web Interface Guide

The web UI is a React (Vite) single-page app talking to a FastAPI backend. You describe an object in chat, the AI generates OpenSCAD, the backend compiles it to STL, slices it to 3MF, and you can preview and print it.

## Running it

Windows:

```bat
start.bat
:: choose [1] Start Online Interface (FastAPI + Vite Frontend)
```

[`start.bat`](../../start.bat) runs [`src/launcher.py`](../../src/launcher.py), which installs a private Node.js (via `nodeenv`) and `npm install`s on first run, then opens two console windows and your browser.

| Component | Address | Started by |
|-----------|---------|-----------|
| FastAPI backend | `http://127.0.0.1:8000` | `uvicorn ... --host 127.0.0.1 --port 8000 --reload` |
| Vite frontend | `http://localhost:5173` | `npm run dev` in `src/web/frontend` |

Close the two console windows to stop. Option `[4]` (all services) starts the same UI under the supervisor, see [`src/core/supervisor.py`](../../src/core/supervisor.py). If `src/web/frontend/dist` exists the backend also serves it at `/`.

> [!WARNING]
> The frontend hard-codes the API base `http://localhost:8000`. Keep the backend on this port.

## Layout

| Area | File | Purpose |
|------|------|---------|
| Sidebar | [`components/Sidebar.jsx`](../../src/web/frontend/src/components/Sidebar.jsx) | Idea list, search box ("Search ideas or chats..."), "load more" |
| Chat | [`components/ChatPanel.jsx`](../../src/web/frontend/src/components/ChatPanel.jsx) | Conversation, per-message support status, prompt box |
| Preview | [`components/PreviewPanel.jsx`](../../src/web/frontend/src/components/PreviewPanel.jsx) | 3D viewer, version selector, progress overlay, Re-render / Open Folder / Delete / Print |
| 3D viewer | [`STLViewer.jsx`](../../src/web/frontend/src/STLViewer.jsx) | three.js (`@react-three/fiber` + `drei`), orbit/zoom (pan disabled), error fallback |
| Print dialog | [`components/PrintModal.jsx`](../../src/web/frontend/src/components/PrintModal.jsx) | Filament slot and supports choice |
| Status dialog | [`components/PrinterStatusModal.jsx`](../../src/web/frontend/src/components/PrinterStatusModal.jsx) | Status, snapshot, stop print |
| Hooks | [`hooks/useSessions.js`](../../src/web/frontend/src/hooks/useSessions.js), [`hooks/usePrinterStatus.js`](../../src/web/frontend/src/hooks/usePrinterStatus.js) | Session paging/search; printer polling, snapshot, stop |
| App | [`App.jsx`](../../src/web/frontend/src/App.jsx) | Wires everything, job tracking |
| Chime | [`audioUtils.js`](../../src/web/frontend/src/audioUtils.js) | Web Audio completion sound |

## Features

- **Conversational design.** Each idea is a *session*. The first prompt calls `POST /sessions/new`; follow-ups call `POST /sessions/{id}/edit`. Both stream NDJSON events (`init`, `step`, `error`, `complete`) that drive the progress bar and status text.
- **Session list and search.** Newest first, 20 per page then 10 per "load more" (`X-Total-Count` header gives the total). Search is debounced and matches title, folder/id, and chat text (`?q=`).
- **Concurrent jobs and progress recovery.** The backend tracks running jobs in memory ([`jobs.py`](../../src/web/backend/jobs.py): target version, message, progress, timestamps) and exposes them as `active_job` on session data. You can start jobs in several ideas and switch between them. Re-opening an idea (or reloading the page) restores the progress bar from `active_job`; the active session is polled every 2 s and the list every 3 s while any job runs. Finished background ideas show an unread marker. Jobs live in backend memory, so a backend restart loses them.
- **Versions.** Every edit creates `vN`. The version selector switches the viewer; **Re-render** (`POST /sessions/{id}/versions/{n}/rerender`) rebuilds STL/3MF from the stored OpenSCAD.
- **3D viewer.** Loads `/created_stls/...` STL files; shows "Parsing 3D Model..." while loading and a red message if the file is corrupt.
- **Supports toggle.** Supports can be requested in the prompt box and in the Print dialog (Off / On). The choice is saved on the session (`enable_supports`) and used in the print script (`--support`).
- **Print dialog.** Choose **Auto** (no slot flag), **AMS slots 0-3**, or **Ext** (external spool, slot 4). It shows the model's estimated grams versus each slot's remaining filament (red = insufficient). Only `PLA` or unknown-type filaments are selectable; others are greyed out. **Confirm Print** is disabled when the printer is offline or busy (`RUNNING`, `PREPARE`, `PAUSE`, `PAUSED`).
- **Status / snapshot / stop.** The status dialog shows state, progress and remaining minutes, **Get Snapshot** fetches a camera frame, and **Stop Print** asks for confirmation, calls `POST /stop_print`, then re-polls status 3 times.
- **Completion chime.** A two-tone sine chime (F#5 then A5, no audio files) plays when a model finishes, throttled to once per 2.5 s.
- **Housekeeping.** **Open Folder** opens the version directory in Explorer; **Delete** removes the idea after confirmation.

## REST API

Base `http://127.0.0.1:8000`. Interactive docs at `/docs`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness check |
| GET | `/sessions?q=&limit=&offset=` | List/search ideas (with `active_job`) |
| GET | `/sessions/{id}` | Full session: history, `all_versions`, `latest_stl`, `active_job` |
| POST | `/sessions/new` (alias `/sessions`) | New idea, body `{prompt, enable_supports?}`, NDJSON stream |
| POST | `/sessions/{id}/edit` | Revise, body `{prompt, enable_supports?}`, NDJSON stream |
| POST | `/sessions/{id}/rerender`, `/sessions/{id}/versions/{n}/rerender` | Rebuild STL/3MF, NDJSON stream |
| POST | `/sessions/{id}/open_folder` | Open folder on the host, body `{version?}` |
| DELETE | `/sessions/{id}` | Delete idea |
| POST | `/sessions/{id}/print` | Body `{version?, ams_slot?, enable_supports?}`; 400 if offline, 409 if busy |
| GET | `/status` | `{connected, status, state, formatted}` |
| GET | `/snapshot` | JPEG camera frame (500 on failure) |
| GET | `/ams_colors` (alias `/ams`) | AMS slot colors/types/remaining (slot 4 = external) |
| POST | `/stop_print` (alias `/printer/stop`) | Stop an active print |
| GET | `/api/supervisor/status` | Managed services and circuit breakers |
| GET | `/api/supervisor/restarts`, `/api/supervisor/logs/{service}` | Lifecycle journal, log tail |
| POST | `/api/supervisor/restart/{service}`, `/reset_breaker/{service}`, `/update_now` | Supervisor controls |

Static: `/created_stls/*` and `/outputs/sessions/*` serve session files (STL, PNG).

## When the printer is not configured

There is no explicit "not configured" state in the UI or API. With no `BAMBU_*` values in `.env`:

- The backend starts normally; MQTT connect is skipped or fails with a console warning.
- `/status` returns `connected: false` with "Printer status is currently unknown or unavailable."
- The Print dialog shows a red "Printer is offline / disconnected" banner and the button reads **Printer Offline** (disabled).
- AMS shows "(AMS Unknown / Not Attached)"; **Get Snapshot** fails with an alert.
- Generating, previewing, versions, Open Folder and the STL/3MF files all work, so you can design and take the output to your own slicer or printer. See [Bambu and Safety Guide](08_bambu_and_safety_guide.md).

## Related docs

- [01_system_architecture.md](01_system_architecture.md)
- [02_multi_ai_and_agent_loop.md](02_multi_ai_and_agent_loop.md)
- [04_telegram_bot_guide.md](04_telegram_bot_guide.md)
- [05_google_tasks_bot_guide.md](05_google_tasks_bot_guide.md)
- [06_slicing_and_3mf_pipeline.md](06_slicing_and_3mf_pipeline.md)
- [07_mesh_geometry_validation.md](07_mesh_geometry_validation.md)
- [08_bambu_and_safety_guide.md](08_bambu_and_safety_guide.md)

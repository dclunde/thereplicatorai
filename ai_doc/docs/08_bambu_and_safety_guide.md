# Bambu Printer and Safety Guide

[`BambuClient`](../../src/core/printer/bambu_client.py) (alias `BambuCloud`) handles all printer communication: status over MQTT, file upload over FTPS, camera snapshots over RTSPS, chamber light, and stop/dispatch safety.

## Connection modes

Credentials come only from `.env` (see [`.env.example`](../../.env.example)), read through [`src/core/config.py`](../../src/core/config.py).

| Mode | Variables | Behavior |
|------|-----------|----------|
| **LAN** (recommended) | `BAMBU_PRINTER_IP`, `BAMBU_ACCESS_CODE`, `BAMBU_DEVICE_SERIAL` | Skips cloud login. MQTT to the printer at port 8883 (user `bblp`, access code as password, self-signed TLS). Uploads, printing and snapshots all work. |
| **Cloud** | `BAMBU_USERNAME`, `BAMBU_PASSWORD`, `BAMBU_DEVICE_SERIAL` | Logs in via `bambu-lab-cloud-api` and uses the Bambu cloud MQTT broker for status, AMS and stop. |

LAN mode is used whenever **both** IP and access code are set; otherwise cloud credentials are used. The access code and serial are on the printer's screen (Settings, LAN mode / device info). LAN mode must be enabled on the printer.

> [!IMPORTANT]
> Printing and snapshots are implemented for **LAN mode only**. `send_print_job` uploads via FTPS to the printer IP, and `get_snapshot` needs the IP and access code. Cloud-only credentials give status, AMS data and stop, but cannot dispatch a print or capture a snapshot.

## Capabilities

- **MQTT status.** Subscribes to `device/<serial>/report` and requests a full push on connect. Tracks `gcode_state` (e.g. `IDLE`, `RUNNING`, `PREPARE`, `PAUSE`, `FINISH`), `mc_percent`, `mc_remaining_time`, AMS trays and the external spool. AMS slots are numbered `unit*4 + tray` (0-3 for one AMS); the external spool is slot `4`. Remaining weight is only known for Bambu RFID spools.
- **FTP upload.** `curl` sends the 3MF to `ftps://<ip>:990/` (user `bblp`, 120 s limit). Then a print command is published over MQTT with timelapse enabled; `ams_slot` 0-3 uses the AMS mapping, `None` or `4` uses the external spool. `curl` must be on `PATH` (built into Windows 10+).
- **Camera snapshot.** OpenCV (`cv2`) grabs one frame from `rtsps://bblp:<code>@<ip>:322/streaming/live/1` into `outputs/snapshots/`; files older than 5 minutes are deleted. Taking a snapshot turns on the chamber light first.
- **Chamber light auto-off.** The light switches off after 300 s (`chamber_light_auto_off_delay`) of inactivity, and 5 minutes after a job reaches `FINISH`. The timer is cancelled while printing, and the light is never auto-switched off when the state is unknown (treated as printing).
- **Stop print.** `stop_print()` publishes a `stop` command and waits up to 5 s for it to send. The web endpoint `POST /stop_print` ignores the request unless state is `RUNNING`, `PREPARE`, `PAUSE` or `PAUSED`, and returns 503 if MQTT is disconnected.

## Dispatch locking and busy checks

Several services (web, Telegram, Google Tasks, CLI) can share one printer, so `send_print_job` is guarded twice:

1. **Busy check.** Waits up to 2 s for a first status report, then raises `PrinterBusyError` if state is in `BUSY_STATES` (`RUNNING`, `PREPARE`, `PAUSE`, `PAUSED`, `SLICING`). An unknown state alone does not block printing.
2. **Cross-process lock.** `outputs/.cache/printer_dispatch.lock` is created atomically (`O_CREAT | O_EXCL`) with the owner PID and timestamp. A second dispatcher gets `PrinterBusyError("Another print job is currently being dispatched...")`. Stale locks (older than 90 s, owner process dead, or corrupt) are removed automatically.

After a successful dispatch the lock is kept until MQTT reports a busy state (closing the gap between upload and `PREPARE`) or the 90 s expiry. It is released immediately on failure or on disconnect.

The web UI mirrors this: `POST /sessions/{id}/print` returns 400 if the printer is offline and 409 if busy.

## Safety notes

- **PLA only.** The sliced project template ([`inputs/templates/default_project.3mf`](../../inputs/templates)) is set up for PLA, and the print dialog only allows slots reporting `PLA` or unknown type. Do not load PETG, ABS, TPU, etc. for these jobs.
- **Local only.** The web backend is started with `--host 127.0.0.1` by [`src/launcher.py`](../../src/launcher.py) and [`src/core/supervisor.py`](../../src/core/supervisor.py). The API has no authentication and CORS allows all origins, and it can start prints and open folders. **Never expose port 8000 (or 5173) to your LAN or the internet** (no port forwarding, tunnels, or `--host 0.0.0.0`).
- **Credentials only in `.env`.** It is git-ignored. Never put keys, access codes or passwords in code, session files, logs, or screenshots. Snapshots can show your room.
- **Unattended printing.** Look at the first layers (status dialog, Get Snapshot) before leaving, and keep the printer where you can reach the power switch.
- **Emergency stop.** In order of preference: the physical stop on the printer or Bambu Studio/Handy; the web **Stop Print** button; launcher menu "Stop Printer" ([`src/cli/stop_print.py`](../../src/cli/stop_print.py)); cutting power for a real hazard. Software stop needs a live MQTT connection.
- **Bot access.** Anyone who can message your Telegram bot or Google Tasks list can queue prints; keep tokens private.

## Design and Download mode (no printer configured)

With no `BAMBU_*` values nothing crashes, and there is no separate mode switch. In practice:

- The backend skips or fails MQTT quietly (console warning only).
- Design, compile, slice, versions, the 3D viewer and the session folders all work. The STL, 3MF and `print.bat` scripts are in each session's `vN` folder under `outputs/`, ready for manual slicing or printing.
- Status shows "unknown or unavailable", AMS is empty, snapshot fails, and the Print dialog is disabled ("Printer Offline"). `send_print_job` returns failure (`Printer IP or Access Code missing for LAN Mode print.`).
- Slicing still needs the slicer installed; see [Slicing and 3MF Pipeline](06_slicing_and_3mf_pipeline.md).

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| UI shows offline | Wrong IP/code/serial, LAN mode off, or printer asleep; restart backend after editing `.env` |
| "Printer is currently RUNNING ..." | Genuinely busy, or a stale lock (clears after 90 s) |
| Upload fails | `curl` missing, firewall blocking port 990, or wrong access code |
| Snapshot fails | `opencv-python` missing, port 322 blocked, or camera disabled on the printer |

## Related docs

- [01_system_architecture.md](01_system_architecture.md)
- [02_multi_ai_and_agent_loop.md](02_multi_ai_and_agent_loop.md)
- [03_web_interface_guide.md](03_web_interface_guide.md)
- [04_telegram_bot_guide.md](04_telegram_bot_guide.md)
- [05_google_tasks_bot_guide.md](05_google_tasks_bot_guide.md)
- [06_slicing_and_3mf_pipeline.md](06_slicing_and_3mf_pipeline.md)
- [07_mesh_geometry_validation.md](07_mesh_geometry_validation.md)

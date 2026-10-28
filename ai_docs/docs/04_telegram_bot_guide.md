# Telegram Bot Guide

Design, slice, print and monitor 3D parts from your phone. The bot is a thin Telegram front end over the same AI → OpenSCAD → STL → 3MF → Bambu pipeline used by the web UI.

Source: [bot.py](../../src/bots/telegram/bot.py) (wiring, startup), [handlers_design.py](../../src/bots/telegram/handlers_design.py) (design/edit/upload/cancel), [handlers_printer.py](../../src/bots/telegram/handlers_printer.py) (status/snapshot/print/stop), [monitor.py](../../src/bots/telegram/monitor.py) (completion notifications).

## 1. Setup

1. In Telegram, open **@BotFather**, send `/newbot`, and follow the prompts. Copy the token it gives you.
2. Add it to `.env` in the project root (see [.env.example](../../.env.example)):
   ```env
   TELEGRAM_BOT_TOKEN=your-token-here
   ```
3. Make sure the rest of the system is configured: an AI key (Gemini/OpenAI/Anthropic), OpenSCAD installed, a slicer, and the `BAMBU_*` printer settings. See [08_bambu_and_safety_guide.md](08_bambu_and_safety_guide.md).
4. Optional: install `opencv-python` (needed for `/snapshot` and the completion photo).

Keys are read from `.env` (falling back to process environment variables) by `load_api_keys()` in [config.py](../../src/core/config.py). Never commit `.env`.

## 2. Running

| Method | Command |
|---|---|
| Launcher menu | `start.bat`, then option **2** (Start Remote Telegram Bot) |
| Direct | `uv run python -m src.bots.telegram.bot` |
| With everything else | `start.bat` option **4** (supervisor runs web + Telegram + Google Tasks) |

Startup sequence in `main()`:

1. No `TELEGRAM_BOT_TOKEN` → prints `WARNING: TELEGRAM_BOT_TOKEN not found in .env. Please configure it.` and exits with code **1**.
2. OpenSCAD not found → prints `FATAL: OpenSCAD not installed or found. ...` and exits with code **1**.
3. Connects to the printer over MQTT, then prints `Bambu Telegram Bot is listening. Press Ctrl+C to stop.` and starts long polling.

## 3. Access control

There is **no allow-list**. Anyone who finds the bot's username can issue commands, including `/make` (which sends a print job) and `/stop_print`. Keep the bot username private, do not share the token, and consider revoking it via BotFather if it leaks. Conversation state is kept per Telegram user; completion notifications go to every chat that has used the bot since start (see §6).

## 4. Commands

| Command | Syntax | What it does |
|---|---|---|
| `/make` | `/make <idea> [--support]` | Generate, slice and **auto-print** (picks an AMS slot by color/filament match; external spool if the printer reports no AMS slots). |
| `/design` | `/design <idea> [--support]` | Generate and slice, then wait for your decision (`/print`, `/edit`, `/cancel`). |
| `/edit` | `/edit <changes> [--support\|--no-support]` | Refine the current design (uses the conversation history). Needs an active design. Omitted flag keeps the previous support setting. |
| `/print` | `/print <slot> [--support]` | Print the sliced model. See slots below. Bare `/print` lists available slots. |
| `/status` | `/status` | Formatted printer status; appends your current design state if any. |
| `/snapshot` | `/snapshot` | Photo from the printer camera (needs `opencv-python`). |
| `/stop_print` | `/stop_print` | Asks for confirmation (inline buttons) and then stops the running job. Only offered when state is `RUNNING`, `PREPARE`, `PAUSE` or `PAUSED`. |
| `/cancel` | `/cancel` | Cancels the current design session. If a step is running it is cancelled cooperatively and you get a confirmation when it finishes. |
| any other text | | Shows the help/option list (or a "busy" message while processing). |

Notes:

- Supports are **off by default**. `--support` enables tree supports; `--no-support` disables them. The flag may appear anywhere in the text and is stripped from the prompt. If `--support` is present it wins over `--no-support`.
- `/make` and `/design` are refused while a design is processing ("You must /cancel the previous one first").
- `/status`, `/snapshot`, `/stop_print` work at any time, even mid-design.
- `/print` while the printer is busy replies "Wait until build plate is clear and try again." A `--support` change on `/print` triggers a re-slice first.

### `/print` slots

| Argument | Meaning |
|---|---|
| `0`–`3` | AMS slot |
| `4`, `ext`, `external` | External spool |
| `auto` | Best AMS match from the design's requested colors and required grams |

Only PLA (or unknown type) is accepted; a slot holding another filament is rejected. Anything outside the list returns a usage hint. Sending the job is retried up to 5 times (5 s apart). On success the session data is cleared.

### Typical flows

```text
/make a 40mm phone stand --support        -> design, slice, print
/design a small coffee cup                -> sliced summary (size, grams, time, supports)
/edit make the handle thicker --no-support
/print 1                                  -> prints on AMS slot 1
```

## 5. STL upload

Send an `.stl` file as a Telegram **document**. The bot saves it to a new session (`uploaded_<name>`), slices it without supports and replies with size, filament and time. Then use `/print <slot> [--support]` or `/cancel`.

- Limit: **20 MB** (Telegram bot download limit). Larger files are rejected.
- Other extensions: "Please upload an .stl file."
- Refused if a design is already processing.

## 6. Monitoring and notifications

[monitor.py](../../src/bots/telegram/monitor.py) starts with the bot and polls the printer state every **60 seconds**:

- Busy state → `FINISH`: turns on the chamber light, grabs a camera frame (RTSPS, only if `BAMBU_PRINTER_IP` and `BAMBU_ACCESS_CODE` are set and OpenCV is installed) and sends "Your print has finished successfully!" plus a "Final Result" photo.
- Busy state → `FAILED`: sends "Your print has failed or encountered an error!".

Notifications go only to chats in an in-memory set that is filled when you use `/make`, `/design`, `/print` or upload an STL. After a bot restart, run one of those first to receive notifications. Transitions shorter than the 60 s poll can be missed.

## 7. Limits

- AI call budget: `service_limits.telegram_bot` in [agent_config.json](../../inputs/configs/agent_config.json) (default 3).
- Optional `service_constraints.telegram_bot.max_print_time_minutes` / `max_size_inches` (0 = off). In `/make`, violations abort the print; in `/design` they show as a warning.
- Long replies are split into chunks of at most 4000 characters.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Exits immediately with the token warning | Set `TELEGRAM_BOT_TOKEN` in `.env`. |
| `FATAL: OpenSCAD not installed or found` | Install OpenSCAD or configure its path. |
| No reply to commands | Only one process can poll a token; stop other instances (including supervisor option 4). |
| `/snapshot` says opencv missing | `uv add opencv-python` |
| Slicing fails | Use `/edit <changes>` to have the AI fix the design; see [06_slicing_and_3mf_pipeline.md](06_slicing_and_3mf_pipeline.md). |
| No "print finished" message | Send any design/print command after a restart; check the printer MQTT connection with `/status`. |

## Related docs

- [01_system_architecture.md](01_system_architecture.md)
- [02_multi_ai_and_agent_loop.md](02_multi_ai_and_agent_loop.md)
- [03_web_interface_guide.md](03_web_interface_guide.md)
- [05_google_tasks_bot_guide.md](05_google_tasks_bot_guide.md)
- [06_slicing_and_3mf_pipeline.md](06_slicing_and_3mf_pipeline.md)
- [07_mesh_geometry_validation.md](07_mesh_geometry_validation.md)
- [08_bambu_and_safety_guide.md](08_bambu_and_safety_guide.md)

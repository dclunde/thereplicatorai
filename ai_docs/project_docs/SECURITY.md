# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Reporting a Vulnerability

**Please do not report security vulnerabilities in public GitHub issues.**

Use GitHub's private reporting: repository **Security** tab → **Report a vulnerability**.

Please include a description, reproduction steps, the potential impact, and a suggested fix if you have one. This is a small project maintained in spare time, so responses are best-effort.

## Security Model

Text-to-Printer is a **single-user, local application**. It can start physical print jobs, so treat it like a tool with control of hardware.

- **Localhost only.** The backend (FastAPI) is launched on `127.0.0.1:8000` and the web UI on `localhost:5173`. Do not change this to `0.0.0.0`, port-forward it, or put it behind a public tunnel. The API has no authentication.
- **Credentials live in `.env`.** AI keys, Bambu credentials, the Telegram token and the Google Tasks OAuth client are read from `.env`, which is git-ignored. Never commit or share it.
- **Cached tokens** (for example the Google Tasks login token) are stored under `outputs/.cache/`, which is git-ignored.
- **Generated data stays local.** Sessions, STL/3MF files, logs and camera snapshots are stored under `outputs/` (git-ignored). Camera snapshots may show your room; do not share them without checking.
- **LAN printing.** The printer's access code works like a password. Keep the printer on a trusted network.
- **Telegram bot.** Anyone who finds your bot can message it. Keep the token private and review the access guidance in the [Telegram guide](../docs/04_telegram_bot_guide.md).
- **AI-generated code.** The AI writes OpenSCAD code which is compiled locally. OpenSCAD is not a general-purpose scripting language, but review unusual output before printing.

## Safe Printing

- The bundled slicing template is configured for **PLA only**. Do not use PETG, ABS or other materials with these files.
- Never leave a print unattended until you trust the result. Use the Stop Print button (web UI), `/stop_print` (Telegram) or `start.bat 7` to abort.

## Best Practices

**Do**
- Rotate any key that you think may have leaked.
- Keep dependencies updated (`uv sync`).
- Use per-project API keys with spending limits.

**Don't**
- Commit `.env`, tokens or session folders.
- Expose the backend to other machines or the internet.
- Paste logs publicly without removing keys, IP addresses and serial numbers.

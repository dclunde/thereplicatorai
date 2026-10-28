# Contributing to Text-to-Printer

Thanks for your interest in contributing! This project is licensed under GPL-3.0; by contributing you agree your changes are released under the same licence.

## Reporting issues
- Search existing issues first.
- Include reproduction steps, your Windows version, Python/`uv` version, the AI provider/model, and relevant logs from `outputs/logs/`.
- **Remove API keys, IP addresses, serial numbers and access codes** from anything you paste.

## Suggesting enhancements
Describe the use case and expected behaviour. Check `ai_docs/docs/` to see whether it already exists.

## Development setup

```powershell
git clone https://github.com/dclunde/thereplicatorai.git
cd thereplicatorai
uv sync
npm --prefix src/web/frontend install
copy .env.example .env     # add at least one AI key
```

Create a branch (`feature/<name>` or `fix/<name>`), keep changes focused, add docstrings to new code, and update the docs in `ai_docs/docs/` when behaviour changes.

## Project layout
See [file_guide.md](../file_guide.md). In short: `src/core` (logic), `src/web` (FastAPI + React), `src/bots` (Telegram, Google Tasks), `src/cli`, `inputs/` (prompts and config), `tests/`.

## Mandatory checks (must be clean before opening a PR)

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/
npm --prefix src/web/frontend run lint
npm --prefix src/web/frontend run test
```

Optional browser E2E: `npm --prefix src/web/frontend run test:e2e`.
See [testing.md](../testing.md) for the Core vs Non-Core test list.

## Testing rules
- Tests must not require real API keys, a printer, or network access. Mock them.
- Never send a real print job from a test.
- Use `uv` for everything (`uv run`, `uv add`, `uv sync`); do not use `pip install`.

## Commits and pull requests
- Write clear, imperative commit messages.
- Fill in the pull request template and link the related issue.
- Do not commit `.env`, `outputs/`, generated models or logs.

## Code of conduct
Be respectful. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

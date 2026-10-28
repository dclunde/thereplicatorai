# Implementation Plan: Direct Python Print & Slicer Path Normalization

## Overview & User Decision
The user responded to our clarifying questions with:
1. **Eliminate batch scripts**: Get rid of `print.bat` and `slice.bat` in each session version directory, and directly execute the Python print runner (`python -m src.cli.manual_print`) for each item.
2. **Informative re-slice notice**: Print an informative notice when an existing 3MF cannot be reused due to differing support settings (e.g. `--support` mismatch).

---

## Architecture & Implementation Steps

### 1. Robust Path Normalization in Slicer (`src/core/slicer/slicer_3mf.py`)
- In `slice_stl_to_3mf`:
  - `stl_file = os.path.abspath(stl_file)` immediately upon function entry.
  - Fail fast if `not os.path.exists(stl_file)` before creating temporary directories (`TMP_DIR`).
  - Resolve `out_3mf` strictly as an absolute path.
- In `_can_reuse_existing_3mf`:
  - `stl_file = os.path.abspath(stl_file)`.
  - Check whether `candidate_3mf` exists. If it exists but has mismatched support settings, log an informative message explaining the re-slice.
- In `compile_and_slice`:
  - `scad_file = os.path.abspath(scad_file)`.
  - Ensure `out_dir` defaults to `os.path.dirname(scad_file)` (which is absolute).
- In `src/core/slicer/openscad.py`:
  - Normalize `scad_path_or_code` to `os.path.abspath` when it points to an existing file.

### 2. Manual Print CLI Logging (`src/cli/manual_print.py`)
- In `run_cli_print`:
  - `stl_file = os.path.abspath(stl_file)`.
  - When re-slicing is necessary (e.g. sibling `.3mf` exists but support setting differs), print:
    `"Existing 3MF does not match requested support settings (supports={enable_supports}). Re-slicing..."`
  - In `run_interactive_print`:
    `input_file = os.path.abspath(input_file)`.

### 3. Eliminate Batch Scripts in Sessions & Web Backend
- In `src/web/backend/routes/printer.py`:
  - In `print_session`:
    - Remove reading/writing `print.bat`.
    - Detect the version's `.stl` (or `.3mf`) file and resolve its absolute path: `abs_stl_path = os.path.abspath(...)`.
    - Extract `requested_colors` from `session_data`.
    - Assemble direct Python command:
      `[sys.executable, "-m", "src.cli.manual_print", abs_stl_path, colors_csv]`
      appending `--slot <ams_slot>` if provided, and `--support` or `--no-support`.
    - Launch directly via `_get_subprocess().Popen`:
      On Windows: `["cmd.exe", "/c", "start", "Bambu P2S Print Job", sys.executable, "-m", "src.cli.manual_print", ...]`
      On Non-Windows: `[sys.executable, "-m", "src.cli.manual_print", ...]`.
    - CWD set to `ROOT_DIR`.
- In `src/web/backend/routes/sessions.py`:
  - Remove `_write_batch_scripts` (or convert to no-op for backward compatibility).
- In `src/core/sessions.py`:
  - Deprecate `write_version_print_scripts` to a no-op that returns empty strings (or keep for backward compatibility without generating files if called).
- In `src/cli/simple_mode.py`, `src/bots/telegram/handlers_design.py`, `src/bots/google_tasks/bot.py`:
  - Remove calls to `write_version_print_scripts`.

### 4. Update Automated Tests
- Create `tests/test_slicer_relative_paths.py`:
  - Test `slice_stl_to_3mf` with relative paths and verify subprocess command args receive absolute paths.
  - Test `_can_reuse_existing_3mf` with support mismatch and verify log output.
  - Test direct Python print launch in `printer.py` without any `print.bat`.
  - Test `manual_print.py` with relative path argument.
- Update existing tests that previously asserted `.bat` file creation:
  - `tests/test_online_backend_flows.py`
  - `tests/unit/test_session_manager.py`
  - `tests/test_google_tasks_bot.py`
  - `tests/test_plan_1a.py`
- Update `ai_docs/testing.md` and `ai_docs/readme.md`.

---

## Verification & Quality Checks
Run all mandatory checks:
1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run pytest tests/`
4. `npm --prefix src/web/frontend run lint`
5. `npm --prefix src/web/frontend run test`

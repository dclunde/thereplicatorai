# Walkthrough: Direct Python Print & Elimination of Version Batch Scripts

## Overview
In response to the slicing and manual print failure (`No such file: ...stl`, exit status 1), we:
1. Normalized all file paths to absolute paths (`os.path.abspath`) across `slicer_3mf.py`, `manual_print.py`, and `openscad.py`.
2. Fully eliminated the generation of `print.bat` and `slice.bat` batch files across all session version directories.
3. Switched model printing (from the Web UI, CLI, and bots) to direct Python execution (`python -m src.cli.manual_print <path_to_stl> [colors_csv] [--support] [--slot X]`).
4. Kept `start.bat` as the single authoritative launcher batch script at the repository root.
5. Added informative notices when re-slicing occurs due to mismatched support settings.

---

## Changes Implemented

### 1. Slicer & Geometry Toolchain (`src/core/slicer/`)
- [`src/core/slicer/slicer_3mf.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/core/slicer/slicer_3mf.py):
  - In `slice_stl_to_3mf`: converts `stl_file = os.path.abspath(stl_file)` immediately upon entry and verifies existence prior to allocating isolated `TMP_DIR` workspaces.
  - Ensures `out_3mf` is strictly resolved to an absolute path before passing it to OrcaSlicer / BambuStudio.
  - In `_can_reuse_existing_3mf`: converts `stl_file = os.path.abspath(stl_file)`. When an existing sibling 3MF cannot be reused due to support settings mismatch, logs an informative notice.
  - In `compile_and_slice`: converts `scad_file = os.path.abspath(scad_file)` and ensures `out_dir` is absolute.
- [`src/core/slicer/openscad.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/core/slicer/openscad.py):
  - In `compile_scad_to_stl`: normalizes file paths to `os.path.abspath`.

### 2. Manual Print CLI (`src/cli/manual_print.py`)
- [`src/cli/manual_print.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/cli/manual_print.py):
  - In `run_cli_print`: resolves `stl_file = os.path.abspath(stl_file)`. When an existing sibling `.3mf` exists but does not match requested support settings, outputs:
    ```text
    Existing 3MF does not match requested support settings (supports=True). Re-slicing...
    ```
  - In `run_interactive_print`: strips quotes and resolves input files with `os.path.abspath`.

### 3. Removal of Batch Scripts & Direct Python Dispatch
- [`src/web/backend/routes/printer.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/web/backend/routes/printer.py):
  - In `print_session`: locates the version's STL file and dispatches print directly via:
    ```python
    cmd = [sys.executable, "-m", "src.cli.manual_print", stl_path, colors_csv]
    if req and req.ams_slot is not None:
        cmd.extend(["--slot", str(req.ams_slot)])
    if enable_supp:
        cmd.append("--support")
    else:
        cmd.append("--no-support")

    if sys.platform == "win32":
        full_cmd = ["cmd.exe", "/c", "start", "Bambu P2S Print Job"] + cmd
    else:
        full_cmd = cmd

    _get_subprocess().Popen(full_cmd, cwd=v_dir)
    ```
  - Purges any legacy `.bat` files if encountered.
- [`src/web/backend/routes/sessions.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/web/backend/routes/sessions.py):
  - Removed calls to `_write_batch_scripts`.
- [`src/core/sessions.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/core/sessions.py):
  - `write_version_print_scripts` deprecated to a clean no-op that purges any legacy batch files and returns `("", "")`.
- [`src/cli/simple_mode.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/cli/simple_mode.py), [`src/bots/google_tasks/bot.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/bots/google_tasks/bot.py), [`src/bots/telegram/handlers_design.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/src/bots/telegram/handlers_design.py):
  - Removed batch script generation calls.
- Root launcher:
  - Preserved [`start.bat`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/start.bat) as the sole `.bat` file in the entire repository.
  - Purged all legacy `print.bat` and `slice.bat` files from `outputs/sessions/`.

---

## Verification Results
- **New Test Suite**: [`tests/test_slicer_relative_paths.py`](file:///c:/Users/domin/Documents/GitHub/text_to_printer/tests/test_slicer_relative_paths.py) (7 passed).
  - Relative path normalization in slicer CWD isolation.
  - Fast failure on nonexistent STL files.
  - 3MF reuse with relative paths and support mismatch detection.
  - CLI informative notice on support flag differences.
  - Absence of batch scripts in session directories.
  - Repository-wide assertion that `run.bat` is the only `.bat` file.

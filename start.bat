@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
uv run --project "%~dp0." python -m src.launcher %*

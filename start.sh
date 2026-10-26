#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
uv run python -m src.cli.supervisor "$@"

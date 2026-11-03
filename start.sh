#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
# app.py validates and selects .venv, venv, or the active virtual environment.
exec python3 app.py "$@"

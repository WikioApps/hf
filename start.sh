#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
export PYTHONUNBUFFERED=1
exec .venv/bin/python app.py

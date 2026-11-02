#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo 'Missing .venv. Run: sh build.sh --build' >&2
    exit 1
fi
export PATH="$PWD/.venv/bin:$PWD/tools:$PATH"
export PYTHONUNBUFFERED=1
exec .venv/bin/python app.py

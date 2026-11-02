#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Missing .venv/bin/python. Run the Install step or: sh build.sh --build" >&2
    exit 2
fi
export PATH="$PWD/.venv/bin:$PATH"
export PYTHONUNBUFFERED=1
echo "Starting Hugging Face Telegram bot and HTTP health server (app.py)"
exec .venv/bin/python app.py "$@"

#!/bin/sh
set -eu
cd "$(dirname "$0")"
# Compatibility with existing InstaPods services that saved build.sh as Start.
if [ "$#" -eq 0 ] && python3 scripts/service_detection.py; then
    exec sh start.sh
fi
if [ "$#" -gt 0 ] && [ "$1" != '--build' ]; then
    echo 'Usage: sh build.sh --build' >&2
    exit 2
fi
python3 -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else "Select Python 3.12 (supported: 3.11–3.13).")'
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
export PATH="$PWD/.venv/bin:$PWD/tools:$PATH"
.venv/bin/python scripts/install_tools.py
.venv/bin/python -m compileall -q app.py bot web/backend scripts
.venv/bin/python -c 'import app; from bot.core import application; from bot.modules import router; print("Build complete. Start with: sh start.sh")'

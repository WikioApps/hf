#!/bin/sh
# BUILD command: sh build.sh --build
# START command: sh start.sh (the long-running Telegram bot and HTTP server)
set -eu
cd "$(dirname "$0")"
case "${1:-}" in
    --build) ;;
    "")
        if python3 deployment.py; then
            echo "app.service invoked build.sh; handing off to start.sh. Set the Start command to: sh start.sh"
            exec sh start.sh
        fi
        ;;
    *) echo "Usage: sh build.sh [--build]. To run the bot: sh start.sh" >&2; exit 2 ;;
esac
python3 -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,14), "Python 3.11–3.13 required"'
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m compileall -q app.py bot.py core.py config.py transfers.py worker.py deployment.py
.venv/bin/python -m unittest discover -s tests -v
echo "Build completed. The long-running Start command is: sh start.sh"

#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,14), "Python 3.11–3.13 required"'
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m compileall -q app.py bot.py core.py config.py transfers.py worker.py
.venv/bin/python -m unittest discover -s tests -v

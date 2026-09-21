#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python run.py "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 run.py "$@"
fi
printf 'Install Python 3.10 or newer, then open AxisBridge again.\n'
read -r answer

#!/usr/bin/env bash
# Startet Connor unter Linux oder macOS.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.10 oder neuer wurde nicht gefunden." >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Richte Connor beim ersten Start ein ..."
  "$PYTHON" -m venv .venv
fi
if [ ! -f .venv/.installed ]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi
exec .venv/bin/python main.py "$@"

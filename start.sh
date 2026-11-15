#!/usr/bin/env bash
# Startet Connor unter Linux oder macOS.
set -euo pipefail
cd "$(dirname "$0")"

SETUP_VERSION=2
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.10 oder neuer wurde nicht gefunden." >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Richte Connor beim ersten Start ein ..."
  "$PYTHON" -m venv .venv
fi
if [ "$(cat .venv/.installed 2>/dev/null || true)" != "$SETUP_VERSION" ]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  if ! .venv/bin/python -m pip install -r requirements-voice.txt; then
    echo "Hinweis: Die Spracherkennung liess sich nicht installieren - Connor laeuft trotzdem, nur ohne Diktieren."
  fi
  echo "$SETUP_VERSION" > .venv/.installed
fi
exec .venv/bin/python main.py "$@"
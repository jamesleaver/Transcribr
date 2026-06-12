#!/bin/bash
# Run the Transcribr test suite using the app's venv (which has the
# optional dependencies: whisper, python-docx, reportlab, tkinter).
# Falls back to the system python3 if the venv doesn't exist.
cd "$(dirname "$0")/.." || exit 1

PY="$HOME/Library/Application Support/Transcribr/venv/bin/python"
if [ ! -x "$PY" ]; then
    PY=python3
fi

echo "Using: $PY"
exec "$PY" -m unittest discover -s tests -v

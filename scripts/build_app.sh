#!/usr/bin/env bash
# Build "Brightspace Sync.app" and report its size.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve a Python interpreter: $PY, then the active venv, then a project
# venv, then python3.
if [[ -n "${PY:-}" ]]; then
  :
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
  PY="$VIRTUAL_ENV/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

cd "$ROOT"

echo "==> Generating app icon"
"$PY" scripts/make_icon.py

echo "==> Running PyInstaller"
rm -rf build/BrightspaceSync dist
"$PY" -m PyInstaller --noconfirm --clean brightspace-sync.spec

APP="dist/Brightspace Sync.app"
echo
echo "==> Built: $APP"
du -sh "$APP"
echo
echo "Largest components:"
du -sh "$APP"/Contents/* 2>/dev/null | sort -h | tail -10

#!/usr/bin/env bash
# Build a single-binary music-loader using PyInstaller.
# Usage: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"

PY=${PY:-python3}

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "error: pip is not available for $PY" >&2
  echo "       install with: sudo apt install python3-pip" >&2
  exit 1
fi

echo ">> building frontend (Svelte)"
if [ -d frontend ]; then
  if command -v npm >/dev/null 2>&1; then
    npm --prefix frontend ci --include=dev 2>&1 | tail -5
    npm --prefix frontend run build 2>&1 | tail -10
  else
    echo "!! npm not found — skipping frontend build (legacy Jinja will be used)" >&2
  fi
fi

echo ">> installing build dependencies"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements.txt pyinstaller

echo ">> cleaning previous build"
rm -rf build dist

echo ">> running PyInstaller"
"$PY" -m PyInstaller --noconfirm music-loader.spec

BIN="dist/music-loader"
if [ -f "$BIN" ]; then
  SIZE=$(du -h "$BIN" | cut -f1)
  echo ""
  echo ">> build OK: $BIN ($SIZE)"
  echo "   install: cp $BIN ~/.local/bin/"
  echo "   run:     music-loader --help"
else
  echo "!! build failed, see logs above" >&2
  exit 1
fi

#!/usr/bin/env bash
# Bootstrap music-loader: create venv, install deps, symlink binary into PATH.
# Run once:  bash setup.sh

set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

echo "=== music-loader setup ==="
echo "project dir: $PROJECT_DIR"

# 1. Create venv if missing
if [ ! -d ".venv" ]; then
  echo ">> creating venv..."
  python3 -m venv .venv
else
  echo ">> venv already exists"
fi

# 2. Install deps
echo ">> installing requirements..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# 3. Add ~/.local/bin to PATH (create symlink there)
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
SYMLINK="$LOCAL_BIN/music-loader"
if [ ! -L "$SYMLINK" ] || [ "$(readlink "$SYMLINK")" != "$PROJECT_DIR/.venv/bin/music-loader" ]; then
  echo ">> symlink: $SYMLINK -> $PROJECT_DIR/.venv/bin/music-loader"
  ln -sf "$PROJECT_DIR/.venv/bin/music-loader" "$SYMLINK"
else
  echo ">> symlink already correct"
fi

# 4. Make sure ~/.local/bin is in PATH for future sessions
BASHRC="$HOME/.bashrc"
if [ -f "$BASHRC" ] && ! grep -q '\.local/bin' "$BASHRC"; then
  echo ">> adding ~/.local/bin to PATH in .bashrc"
  echo '' >> "$BASHRC"
  echo '# Added by music-loader setup' >> "$BASHRC"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$BASHRC"
fi

# 5. Verify
echo
echo "=== verify ==="
"$SYMLINK" --version 2>/dev/null || true
echo
echo "  binary: $SYMLINK"
echo "  type in any new terminal:  music-loader --help"
echo
echo "=== next steps ==="
echo "  music-loader doctor              # see which sources work"
echo "  music-loader cloud-setup        # Yandex.Disk / Mail.ru (WebDAV)"
echo "  music-loader download spotify-tracks.csv -o ./library --parallel 4"
echo "  music-loader upload ./library"

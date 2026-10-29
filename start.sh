#!/usr/bin/env bash
# macOS / Linux: ./start.sh          (computer mode)
#                ./start.sh --phone  (phone mode: HTTPS on your Wi-Fi + QR code)
set -e
cd "$(dirname "$0")"
export PYTHONUTF8=1

PY=""
for c in python3.12 python3.13 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)'; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.12 is needed."
  echo "  macOS:  download it from https://www.python.org/downloads/  (or: brew install python@3.12)"
  echo "  Ubuntu: sudo apt install python3.12 python3.12-venv"
  exit 1
fi

[ -x .venv/bin/python ] || "$PY" -m venv .venv
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing the app's components (the first time takes 2-5 minutes)..."
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  cp requirements.txt .venv/requirements.installed
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python -m app.launch "$@"

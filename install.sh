#!/usr/bin/env sh
set -eu

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python3}"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python}"
else
  echo "Python 3.11+ is required. Please install Python first." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -e "$SCRIPT_DIR"
if [ "${FUSION_MEMORY_SKIP_WIZARD:-}" = "1" ]; then
  "$PYTHON_BIN" -m fusion_memory.cli init
else
  "$PYTHON_BIN" -m fusion_memory.cli init --wizard
fi
"$PYTHON_BIN" -m fusion_memory.cli doctor

echo
echo "Fusion Memory is installed."
echo "Start it with: fusion-memory start"
echo "Check it with: fusion-memory status"

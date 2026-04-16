#!/usr/bin/env bash
# Launch the Prompt Widget. Runs pip only when dependencies actually changed
# or when the venv's Python binary is missing/broken. Silent on the fast path.

set -euo pipefail
cd "$(dirname "$0")"

export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONDONTWRITEBYTECODE=1

VENV_DIR=".venv"
FINGERPRINT_FILE="$VENV_DIR/.requirements.fingerprint"
PYTHON_BIN="$VENV_DIR/bin/python"

python_version() {
  # Print the interpreter version on its own line. $(python_version ...) will
  # strip the trailing newline automatically for equality checks.
  "$1" --version 2>&1
}

requirements_fingerprint() {
  # Combine: requirements.txt contents + active system python version.
  {
    python_version python3
    echo "--"
    cat requirements.txt
  } | sha256sum | awk '{print $1}'
}

recreate_venv() {
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
}

# Build venv if missing or if python binary is broken (e.g. after system upgrade).
if [ ! -x "$PYTHON_BIN" ]; then
  recreate_venv
fi

# If the recorded python version differs from the current system python, rebuild.
if [ -f "$FINGERPRINT_FILE" ]; then
  STORED_VERSION="$(head -n 1 "$FINGERPRINT_FILE" 2>/dev/null || true)"
  CURRENT_VERSION="$(python_version python3)"
  if [ -n "$STORED_VERSION" ] && [ "$STORED_VERSION" != "$CURRENT_VERSION" ]; then
    recreate_venv
  fi
fi

CURRENT_FP="$(requirements_fingerprint)"
STORED_FP=""
if [ -f "$FINGERPRINT_FILE" ]; then
  STORED_FP="$(sed -n '2p' "$FINGERPRINT_FILE" 2>/dev/null || true)"
fi

need_install=0
if [ "$CURRENT_FP" != "$STORED_FP" ]; then
  need_install=1
elif ! "$PYTHON_BIN" -c "import pynput" >/dev/null 2>&1; then
  # Fingerprint matches but the import still fails (user wiped site-packages).
  need_install=1
fi

if [ "$need_install" -eq 1 ]; then
  "$PYTHON_BIN" -m pip install --quiet --disable-pip-version-check -r requirements.txt
  {
    python_version python3
    echo "$CURRENT_FP"
  } >"$FINGERPRINT_FILE"
fi

exec "$PYTHON_BIN" prompt_widget.py "$@"

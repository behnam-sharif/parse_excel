#!/usr/bin/env bash
# Create a virtual environment for parse_excel and install dependencies.
# Usage:  bash setup_venv.sh
set -euo pipefail

cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

echo "Creating virtual environment in .venv ..."
"$PY" -m venv .venv

# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate          # macOS / Linux
else
  source .venv/Scripts/activate      # Windows (Git Bash)
fi

echo "Installing dependencies ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Done. Activate the environment with:"
echo "  source .venv/bin/activate        # macOS / Linux"
echo "  .venv\\Scripts\\activate           # Windows"

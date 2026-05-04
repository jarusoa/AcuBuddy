#!/usr/bin/env bash
# One-time setup: creates the venv, installs dependencies, scaffolds .env.
# Idempotent — re-running skips steps that are already done.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

venv_dir=""
for candidate in .venv venv env; do
    if [ -f "$candidate/bin/python" ]; then
        venv_dir="$candidate"
        break
    fi
done
if [ -z "$venv_dir" ]; then
    venv_dir=".venv"
    echo "Creating venv at ./$venv_dir ..."
    python3 -m venv "$venv_dir"
else
    echo "Found existing venv at ./$venv_dir"
fi

# shellcheck disable=SC1091
source "$venv_dir/bin/activate"

echo "Installing dependencies (torch + chromadb are large — allow several minutes) ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo
        echo "Created .env from .env.example. EDIT IT and add your DEEPSEEK_API_KEY before launching."
    else
        echo "Warning: no .env.example to copy. Create .env manually with DEEPSEEK_API_KEY=..." >&2
    fi
else
    echo ".env already exists — leaving it alone"
fi

cat <<EOF

Setup complete. Next steps:
  1. Edit .env with your DEEPSEEK_API_KEY (and optionally ACUBUDDY_PROJECT_ROOT)
  2. Add Acumatica PDFs to data/, then: python build_index.py --clean
  3. (Optional) python index_project.py    # builds the project catalog
  4. Launch: ./acubuddy.sh
EOF

#!/usr/bin/env bash
# Εκκίνηση: άμεσο GUI. Τα OCR μοντέλα κατεβαίνουν μόνο αν χρειαστούν.
set -euo pipefail
cd "$(dirname "$0")"
if [ "${1:-}" = "--download-models" ]; then
  exec .venv/bin/python download_models.py
fi
VENV_SITE="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 gui.py

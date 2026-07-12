#!/usr/bin/env bash
# Εκκίνηση: first-run wizard (λήψη μοντέλων αν χρειάζεται) -> κύριο GUI.
set -euo pipefail
cd "$(dirname "$0")"
VENV_SITE="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 firstrun.py

#!/usr/bin/env bash
# Combined «overnight» GUI: PDF → JSON → Βάση → embeddings → Skill, σε φάκελο-πακέτο.
set -euo pipefail
cd "$(dirname "$0")"
VENV_SITE="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 kb_gui.py

#!/usr/bin/env bash
# Εκκίνηση του Knowledge-Base GUI (χτίσιμο βάσης ανά χρήση: research/lesson/…).
# Το bge-m3 (~2.2GB) κατεβαίνει αυτόματα την 1η φορά που θα ζητηθεί ενσωμάτωση.
set -euo pipefail
cd "$(dirname "$0")"
VENV_SITE="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 db_gui.py

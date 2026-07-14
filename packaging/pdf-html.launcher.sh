#!/usr/bin/env bash
# Launcher του εγκατεστημένου πακέτου: φτιάχνει per-user αντίγραφο + venv και
# ανοίγει άμεσα το GUI. Οι πηγές ζουν read-only στο /opt/pdfextractor· το
# venv και η βάση ζουν στον φάκελο του χρήστη.
# Το venv στήνεται με uv + Python 3.13 (το marker-pdf δεν έχει ακόμη wheels
# για Python 3.14 — παλιές Pillow/regex δεν χτίζονται εκεί).
set -euo pipefail
SRC="/opt/pdfextractor"
DEFAULT_APP="$HOME/.local/share/pdfextractor"
if [ ! -d "$DEFAULT_APP" ] && [ -d "$HOME/.local/share/pdf-html" ]; then
  DEFAULT_APP="$HOME/.local/share/pdf-html"
fi
APP="${PDFEXTRACTOR_HOME:-${PDFHTML_HOME:-$DEFAULT_APP}}"
mkdir -p "$APP"
cp -u "$SRC"/*.py "$SRC"/*.sh "$SRC"/README.md "$APP"/ 2>/dev/null || true
cd "$APP"

if [ ! -x ".venv/bin/python" ] || ! .venv/bin/python -c 'import marker' 2>/dev/null; then
  rm -rf .venv
  UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
  if [ ! -x "$UV" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV="$HOME/.local/bin/uv"
  fi
  "$UV" venv --python 3.13 .venv
  "$UV" pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
  "$UV" pip install --python .venv/bin/python marker-pdf tkinterdnd2 sentence-transformers sqlite-vec psutil pip
fi
# Draw the GUI with Kubuntu's native Tcl/Tk.  uv's standalone Python bundles
# Tk 9, which currently corrupts Greek rendering on some Wayland systems.
# Marker still runs in the isolated venv as a subprocess.
GUI_PY="${PDFHTML_GUI_PY:-/usr/bin/python3}"
VENV_SITE="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
exec "$GUI_PY" gui.py

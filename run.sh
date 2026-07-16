#!/usr/bin/env bash
# Εκκίνηση: άμεσο GUI. Τα OCR μοντέλα κατεβαίνουν μόνο αν χρειαστούν.
set -euo pipefail
cd "$(dirname "$0")"
if [ "${1:-}" = "--download-models" ]; then
  exec .venv/bin/python download_models.py
fi
VENV_SITE="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
# Προτίμησε το νέο GTK4 GUI· αν λείπει το GTK4 binding, πέσε στο tkinter.
if /usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0')" 2>/dev/null; then
  exec /usr/bin/python3 gtk_gui.py
fi
echo "GTK4 binding λείπει (sudo apt install gir1.2-gtk-4.0) — άνοιγμα tkinter GUI." >&2
exec /usr/bin/python3 gui.py

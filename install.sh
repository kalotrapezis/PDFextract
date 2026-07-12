#!/usr/bin/env bash
# Installer για ΑΥΤΟ το μηχάνημα (Fedora):
#  • βεβαιώνεται ότι υπάρχει .venv (Python 3.12) με Marker
#  • στήνει .desktop launcher στο μενού εφαρμογών
#  • το πρώτο άνοιγμα κατεβάζει τα μοντέλα με progress (firstrun.py)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "==> Έλεγχος Python 3.12"
PY312="$(command -v python3.12 || true)"
if [ -z "$PY312" ]; then
  echo "!! Λείπει η python3.12. Τρέξε:  sudo dnf install -y python3.12" >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "==> Δημιουργία .venv (Python 3.12)"
  "$PY312" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
fi

echo "==> Εγκατάσταση εξαρτήσεων (marker-pdf, tkinterdnd2, torch CPU)"
.venv/bin/python -m pip install --quiet marker-pdf tkinterdnd2
.venv/bin/python -m pip install --quiet "torch" --index-url https://download.pytorch.org/whl/cpu || true

echo "==> Δημιουργία launcher στο μενού εφαρμογών"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/pdf-html.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PDF → DB (Marker)
Comment=Μετατροπή ερευνητικών PDF σε δομημένη βάση
Exec=$HERE/run.sh
Path=$HERE
Icon=$HERE/assets/pdf-html.png
Terminal=false
Categories=Office;Utility;
EOF
chmod +x "$APPS/pdf-html.desktop" run.sh firstrun.py gui.py convert.py 2>/dev/null || true
update-desktop-database "$APPS" 2>/dev/null || true

echo ""
echo "✓ Έτοιμο. Άνοιξέ το από το μενού («PDF → DB (Marker)») ή με:  ./run.sh"
echo "  (Η πρώτη εκκίνηση κατεβάζει τα μοντέλα — κλείσε το Windows VM πρώτα.)"

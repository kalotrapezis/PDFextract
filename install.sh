#!/usr/bin/env bash
# Installer για Linux:
#  • βεβαιώνεται ότι υπάρχει .venv (Python 3.13) με Marker
#  • στήνει .desktop launcher στο μενού εφαρμογών
#  • τα OCR μοντέλα κατεβαίνουν μόνο όταν εμφανιστεί σκαναρισμένο PDF
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  UV="$HOME/.local/bin/uv"
fi
if [ ! -x "$UV" ]; then
  echo "==> Εγκατάσταση uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "==> Δημιουργία .venv (Python 3.13)"
  "$UV" venv --python 3.13 .venv
fi

echo "==> Εγκατάσταση εξαρτήσεων (marker-pdf, tkinterdnd2, psutil, torch CPU)"
# Βάλε πρώτα το CPU wheel, αλλιώς το marker μπορεί να τραβήξει τα τεράστια CUDA wheels.
"$UV" pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
"$UV" pip install --python .venv/bin/python marker-pdf tkinterdnd2 psutil \
  sentence-transformers sqlite-vec

echo "==> Δημιουργία launcher στο μενού εφαρμογών"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/pdfextractor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PDFExtractor
Comment=Μετατροπή ερευνητικών PDF σε δομημένη βάση
Exec=$HERE/run.sh
Path=$HERE
Icon=$HERE/assets/pdf-html.png
Terminal=false
Categories=Office;Utility;
EOF
chmod +x "$APPS/pdfextractor.desktop" run.sh firstrun.py gui.py convert.py 2>/dev/null || true
update-desktop-database "$APPS" 2>/dev/null || true

echo ""
echo "✓ Έτοιμο. Άνοιξέ το από το μενού («PDFExtractor») ή με:  ./run.sh"
echo "  Τα OCR μοντέλα κατεβαίνουν μόνο όταν προστεθεί σκαναρισμένο PDF."

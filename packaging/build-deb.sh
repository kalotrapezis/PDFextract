#!/usr/bin/env bash
# Χτίζει το .deb χωρίς sudo. Έξοδος: packaging/dist/*.deb
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
VER="2.0.0"
TOP="$(mktemp -d)"
trap 'rm -rf "$TOP"' EXIT

PKG="$TOP/pdfextractor_${VER}_all"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/pdfextractor" "$PKG/usr/bin" \
         "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/512x512/apps"

# Πηγές της εφαρμογής (όλα τα scripts του pipeline + GUIs)
for f in convert.py fasttext_extract.py download_models.py runtime.py gui.py firstrun.py db.py ingest.py query.py annotate.py \
         embed.py profiles.py db_gui.py kb_gui.py build.py make_skill.py ui_style.py \
         run.sh run_db.sh run_kb.sh README.md LICENSE THIRD_PARTY_NOTICES.md CONTRIBUTING.md; do
  install -m 0644 "$REPO/$f" "$PKG/opt/pdfextractor/$f"
done
chmod 0755 "$PKG/opt/pdfextractor/"*.sh

install -m 0755 "$HERE/pdf-html.launcher.sh" "$PKG/usr/bin/pdfextractor"
ln -s pdfextractor "$PKG/usr/bin/pdf-html"
install -m 0644 "$HERE/pdf-html.desktop" "$PKG/usr/share/applications/pdfextractor.desktop"
install -m 0644 "$REPO/assets/pdf-html.png" \
    "$PKG/usr/share/icons/hicolor/512x512/apps/pdfextractor.png"

cat > "$PKG/DEBIAN/control" <<EOF
Package: pdfextractor
Version: $VER
Section: science
Priority: optional
Architecture: all
Depends: bash, curl, ca-certificates, python3, python3-tk
Maintainer: teo <kalotrapezis@gmail.com>
Homepage: https://github.com/kalotrapezis/PDFextract
Provides: pdf-html
Conflicts: pdf-html
Replaces: pdf-html
Description: Γρήγορη μετατροπή ερευνητικών PDF σε δομημένη βάση
 Διαβάζει άμεσα το υπάρχον text layer και χρησιμοποιεί Marker/Surya OCR μόνο
 για πραγματικά σκαναρισμένα PDF. Παράγει JSON/Markdown/HTML με επαλήθευση
 και τα εισάγει σε SQLite βάση paper με embeddings (bge-m3) και σχολιασμό
 ανά ενότητα. Τα OCR μοντέλα κατεβαίνουν μόνο όταν χρειαστούν.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
echo "PDFExtractor εγκαταστάθηκε. Άνοιξέ το από το μενού ή με: pdfextractor"
echo "Τα OCR μοντέλα κατεβαίνουν μόνο όταν προστεθεί σκαναρισμένο PDF."
exit 0
EOF
chmod 0755 "$PKG/DEBIAN/postinst"

dpkg-deb --build --root-owner-group "$PKG"

mkdir -p "$HERE/dist"
cp "$TOP"/*.deb "$HERE/dist/"
echo ""
echo "✓ DEB:"
ls -la "$HERE/dist/"*.deb

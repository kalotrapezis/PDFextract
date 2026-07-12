#!/usr/bin/env bash
# Χτίζει το .deb χωρίς sudo. Έξοδος: packaging/dist/*.deb
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
VER="1.1.4"
TOP="$(mktemp -d)"
trap 'rm -rf "$TOP"' EXIT

PKG="$TOP/pdf-html_${VER}_all"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/pdf-html" "$PKG/usr/bin" \
         "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/512x512/apps"

# Πηγές της εφαρμογής (όλα τα scripts του pipeline + GUIs)
for f in convert.py gui.py firstrun.py db.py ingest.py query.py annotate.py \
         embed.py profiles.py db_gui.py kb_gui.py build.py make_skill.py ui_style.py \
         run.sh run_db.sh run_kb.sh README.md LICENSE THIRD_PARTY_NOTICES.md CONTRIBUTING.md; do
  install -m 0644 "$REPO/$f" "$PKG/opt/pdf-html/$f"
done
chmod 0755 "$PKG/opt/pdf-html/"*.sh

install -m 0755 "$HERE/pdf-html.launcher.sh" "$PKG/usr/bin/pdf-html"
install -m 0644 "$HERE/pdf-html.desktop" "$PKG/usr/share/applications/pdf-html.desktop"
install -m 0644 "$REPO/assets/pdf-html.png" \
    "$PKG/usr/share/icons/hicolor/512x512/apps/pdf-html.png"

cat > "$PKG/DEBIAN/control" <<EOF
Package: pdf-html
Version: $VER
Section: science
Priority: optional
Architecture: all
Depends: bash, curl, ca-certificates, python3, python3-tk
Maintainer: teo <kalotrapezis@gmail.com>
Homepage: https://github.com/kalotrapezis/PDFextract
Description: Μετατροπή ερευνητικών PDF σε δομημένη βάση (Marker + SQLite)
 Μετατρέπει ερευνητικά PDF σε JSON/Markdown/HTML με το Marker (Surya OCR)
 και τα εισάγει σε SQLite βάση paper με embeddings (bge-m3) και σχολιασμό
 ανά ενότητα. GUI με drag & drop και first-run wizard. Το venv και τα
 μοντέλα στήνονται per-user στην πρώτη εκκίνηση.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
echo "PDF→DB εγκαταστάθηκε. Άνοιξέ το από το μενού ή με: pdf-html"
echo "Η πρώτη εκκίνηση στήνει το venv και κατεβάζει τα μοντέλα (κλείσε το Windows VM)."
exit 0
EOF
chmod 0755 "$PKG/DEBIAN/postinst"

dpkg-deb --build --root-owner-group "$PKG"

mkdir -p "$HERE/dist"
cp "$TOP"/*.deb "$HERE/dist/"
echo ""
echo "✓ DEB:"
ls -la "$HERE/dist/"*.deb

#!/usr/bin/env bash
# Χτίζει το .rpm (noarch) χωρίς sudo. Έξοδος: packaging/dist/*.rpm
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
TOP="$(mktemp -d)"
trap 'rm -rf "$TOP"' EXIT

mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "$TOP/SOURCES/app"

# Πηγές της εφαρμογής
for f in convert.py fasttext_extract.py download_models.py runtime.py gui.py firstrun.py db.py ingest.py query.py annotate.py \
         embed.py profiles.py db_gui.py kb_gui.py build.py make_skill.py ui_style.py \
         run.sh run_db.sh run_kb.sh README.md LICENSE THIRD_PARTY_NOTICES.md CONTRIBUTING.md; do
  cp "$REPO/$f" "$TOP/SOURCES/app/$f"
done
cp "$HERE/pdf-html.launcher.sh" "$TOP/SOURCES/"
cp "$HERE/pdf-html.desktop" "$TOP/SOURCES/"
cp "$REPO/assets/pdf-html.png" "$TOP/SOURCES/"
cp "$HERE/pdf-html.spec" "$TOP/SPECS/"

rpmbuild --define "_topdir $TOP" -bb "$TOP/SPECS/pdf-html.spec"

mkdir -p "$HERE/dist"
find "$TOP/RPMS" -name "*.rpm" -exec cp {} "$HERE/dist/" \;
echo ""
echo "✓ RPM:"
ls -la "$HERE/dist/"*.rpm

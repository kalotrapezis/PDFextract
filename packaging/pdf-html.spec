Name:           pdf-html
Version:        1.1.4
Release:        1%{?dist}
Summary:        Μετατροπή ερευνητικών PDF σε δομημένη βάση (Marker + SQLite)
License:        GPL-3.0-only
URL:            https://github.com/kalotrapezis/PDFextract
BuildArch:      noarch
Requires:       python3.12, bash

%global appdir /opt/pdf-html

%description
Εργαλείο που μετατρέπει ερευνητικά PDF σε δομημένη μορφή (JSON/Markdown/HTML)
με το Marker (Surya OCR) και τα εισάγει σε μια SQLite βάση paper, διατηρώντας
κάθε γραμμή, με σχολιασμό ανά ενότητα. Περιλαμβάνει GUI με drag & drop και
first-run wizard για λήψη μοντέλων. Το venv και τα μοντέλα στήνονται per-user
στην πρώτη εκκίνηση.

%install
rm -rf %{buildroot}
install -d %{buildroot}%{appdir}
for f in convert.py gui.py firstrun.py db.py ingest.py query.py annotate.py \
         embed.py profiles.py db_gui.py kb_gui.py build.py make_skill.py ui_style.py \
         run.sh run_db.sh run_kb.sh README.md LICENSE THIRD_PARTY_NOTICES.md CONTRIBUTING.md; do
  install -m 0644 %{_sourcedir}/app/$f %{buildroot}%{appdir}/$f
done
chmod 0755 %{buildroot}%{appdir}/run.sh %{buildroot}%{appdir}/run_db.sh %{buildroot}%{appdir}/run_kb.sh

install -d %{buildroot}%{_bindir}
install -m 0755 %{_sourcedir}/pdf-html.launcher.sh %{buildroot}%{_bindir}/pdf-html

install -d %{buildroot}%{_datadir}/applications
install -m 0644 %{_sourcedir}/pdf-html.desktop %{buildroot}%{_datadir}/applications/pdf-html.desktop

install -d %{buildroot}%{_datadir}/icons/hicolor/512x512/apps
install -m 0644 %{_sourcedir}/pdf-html.png \
    %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/pdf-html.png

%files
%{appdir}
%{_bindir}/pdf-html
%{_datadir}/applications/pdf-html.desktop
%{_datadir}/icons/hicolor/512x512/apps/pdf-html.png

%post
echo "PDF→DB εγκαταστάθηκε. Άνοιξέ το από το μενού ή με: pdf-html"
echo "Η πρώτη εκκίνηση στήνει το venv και κατεβάζει τα μοντέλα (κλείσε το Windows VM)."

%changelog
* Sun Jul 12 2026 teo <kalotrapezis@gmail.com> - 1.1.4-1
- Add a simple PDF-to-database application icon.

* Sun Jul 12 2026 teo <kalotrapezis@gmail.com> - 1.1.3-1
- Native system Tk rendering and page-based progress with measured ETA.

* Sun Jul 12 2026 teo <kalotrapezis@gmail.com> - 1.1.2-1
- Portable fonts, RAM refresh locking, and SQLite output in the main GUI.

* Tue Jun 09 2026 teo <kalotrapezis@gmail.com> - 1.0.0-1
- Πρώτη έκδοση: convert + ingest + DB + annotate + GUI + first-run wizard.

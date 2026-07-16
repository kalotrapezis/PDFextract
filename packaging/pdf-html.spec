Name:           pdfextractor
Version:        2.1.0
Release:        1%{?dist}
Summary:        Γρήγορη μετατροπή ερευνητικών PDF σε δομημένη βάση
License:        GPL-3.0-only
URL:            https://github.com/kalotrapezis/PDFextract
BuildArch:      noarch
Requires:       python3.12, bash, python3-gobject, gtk4
Provides:       pdf-html = %{version}
Obsoletes:      pdf-html < %{version}

%global appdir /opt/pdfextractor

%description
Εργαλείο που διαβάζει άμεσα το text layer ερευνητικών PDF και χρησιμοποιεί
Marker/Surya OCR μόνο για πραγματικά σκαναρισμένα έγγραφα. Παράγει
JSON/Markdown/HTML με report επαλήθευσης και SQLite βάση. Τα OCR μοντέλα
κατεβαίνουν μόνο όταν χρειαστούν.

%install
rm -rf %{buildroot}
install -d %{buildroot}%{appdir}
for f in convert.py fasttext_extract.py download_models.py runtime.py gui.py gtk_gui.py firstrun.py db.py ingest.py query.py annotate.py \
         embed.py profiles.py db_gui.py kb_gui.py build.py make_skill.py ui_style.py \
         run.sh run_db.sh run_kb.sh README.md LICENSE THIRD_PARTY_NOTICES.md CONTRIBUTING.md; do
  install -m 0644 %{_sourcedir}/app/$f %{buildroot}%{appdir}/$f
done
chmod 0755 %{buildroot}%{appdir}/run.sh %{buildroot}%{appdir}/run_db.sh %{buildroot}%{appdir}/run_kb.sh

install -d %{buildroot}%{_bindir}
install -m 0755 %{_sourcedir}/pdf-html.launcher.sh %{buildroot}%{_bindir}/pdfextractor
ln -s pdfextractor %{buildroot}%{_bindir}/pdf-html

install -d %{buildroot}%{_datadir}/applications
install -m 0644 %{_sourcedir}/pdf-html.desktop %{buildroot}%{_datadir}/applications/pdfextractor.desktop

install -d %{buildroot}%{_datadir}/icons/hicolor/512x512/apps
install -m 0644 %{_sourcedir}/pdf-html.png \
    %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/pdfextractor.png

%files
%{appdir}
%{_bindir}/pdfextractor
%{_bindir}/pdf-html
%{_datadir}/applications/pdfextractor.desktop
%{_datadir}/icons/hicolor/512x512/apps/pdfextractor.png

%post
echo "PDFExtractor εγκαταστάθηκε. Άνοιξέ το από το μενού ή με: pdfextractor"
echo "Τα OCR μοντέλα κατεβαίνουν μόνο όταν προστεθεί σκαναρισμένο PDF."

%changelog
* Thu Jul 16 2026 teo <kalotrapezis@gmail.com> - 2.1.0-1
- New GTK 4 desktop GUI (gtk_gui.py) that follows the system theme accent; the
  Tkinter GUI remains as an automatic fallback. Adds python3-gobject and gtk4.

* Sun Jul 12 2026 teo <kalotrapezis@gmail.com> - 1.1.4-1
- Add a simple PDF-to-database application icon.

* Sun Jul 12 2026 teo <kalotrapezis@gmail.com> - 1.1.3-1
- Native system Tk rendering and page-based progress with measured ETA.

* Sun Jul 12 2026 teo <kalotrapezis@gmail.com> - 1.1.2-1
- Portable fonts, RAM refresh locking, and SQLite output in the main GUI.

* Tue Jun 09 2026 teo <kalotrapezis@gmail.com> - 1.0.0-1
- Πρώτη έκδοση: convert + ingest + DB + annotate + GUI + first-run wizard.

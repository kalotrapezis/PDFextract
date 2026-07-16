#!/usr/bin/env python3
"""
PDFExtractor — GTK4 GUI (νέο σχέδιο, PixelRuller mockup).

Τρέχει με το system ``/usr/bin/python3`` (έχει PyGObject/GTK4), ενώ ό,τι βαρύ
πάει μέσω ``.venv/bin/python`` σαν ξεχωριστή διεργασία — ίδιο πρωτόκολλο stdout
(``PROGRESS|CHUNK|CHUNK_DONE|DONE|ING|STAGE|ERROR|ALLDONE``) με το tkinter gui.py.

Τα ελαφριά βοηθητικά (μέτρημα σελίδων, ανίχνευση text layer, ελεύθερη RAM)
τρέχουν in-process: το ``run.sh`` βάζει το venv site-packages στο PYTHONPATH,
οπότε τα pypdfium2 / fasttext_extract / psutil φορτώνουν κανονικά.

Layout κατά το mockup:
  HeaderBar · AppTitle/Subtitle · Paned(Documents 65% | Settings 35%) · ActionBar.
"""
from __future__ import annotations

import faulthandler
import os
import subprocess
import threading
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

import fasttext_extract  # noqa: E402
from runtime import available_gb, popen_kwargs, worker_cmd  # noqa: E402

HERE = Path(__file__).resolve().parent

# ---- Crash logging: ό,τι σκάσει γράφεται εδώ (ίδιο αρχείο με το tkinter GUI) ----
_LOG = HERE / "gui_crash.log"
_logf = open(_LOG, "a", buffering=1, encoding="utf-8")
faulthandler.enable(file=_logf)


def _log_exc(prefix: str, exc: BaseException) -> None:
    _logf.write(f"\n=== {prefix} ===\n")
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=_logf)
    _logf.flush()


# ---------------------------------------------------------------------------
# Στυλ — ίδια φιλοσοφία με AppLocker / KDE-Essensials:
# ΚΑΝΕΝΑ hardcoded χρώμα, καμία CSS που παλεύει το θέμα. Χρησιμοποιούμε τις
# έτοιμες theme style-classes ώστε τα accent στοιχεία (κουμπιά, progress, drop-zone,
# Add more PDFs) να ακολουθούν αυτόματα το accent του θέματος — μπλε, ροζ, ό,τι
# διαλέξει ο χρήστης, light ή dark. Το CSS εδώ ορίζει ΜΟΝΟ γεωμετρία (radius,
# padding, min-height) και αναφέρεται στο @accent_color του θέματος για το drop-zone.
# Τα «fast/OCR» tints είναι ημιδιάφανα (alpha) ώστε να δουλεύουν και σε dark theme.
# ---------------------------------------------------------------------------
CSS = b"""
.workspace            { padding: 28px; }
.app-title            { font-size: 26px; font-weight: 800; }
.card                 { padding: 20px; }
.dropzone {
    background: alpha(@theme_selected_bg_color, 0.10);
    border: 2px dashed @theme_selected_bg_color;
    border-radius: 12px;
    color: @theme_selected_bg_color;
    font-size: 19px;
    min-height: 130px;
}
.dropzone:hover       { background: alpha(@theme_selected_bg_color, 0.16); }
.dropzone.drag-over   { background: alpha(@theme_selected_bg_color, 0.24); }
.file-row {
    border-radius: 6px;
    border: 1px solid transparent;
    padding: 4px 8px;
}
.file-fast            { background: alpha(#2e9e5b, 0.16); border-color: alpha(#2e9e5b, 0.55); }
.file-ocr             { background: alpha(#e0951f, 0.16); border-color: alpha(#e0951f, 0.55); }
.action-bar           { padding: 10px; }
.file-list, .file-list > row { background: transparent; }
progressbar > trough, progressbar > trough > progress { min-height: 8px; border-radius: 4px; }
"""


# ---------------------------------------------------------------------------
# Ελαφριά βοηθητικά (in-process, μέσω venv site-packages στο PYTHONPATH)
# ---------------------------------------------------------------------------
def _pdf_page_count(path: str) -> int:
    """Διαβάζει το δέντρο σελίδων χωρίς να φορτώνει Marker/OCR μοντέλα."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    try:
        return len(doc)
    finally:
        doc.close()


def _ram_batch_speed():
    """(avail_gb, batch, εκτίμηση δευτ/σελίδα OCR) ανάλογα με την ελεύθερη RAM."""
    avail = available_gb()
    headroom = avail - 8.5  # τα μοντέλα Marker θέλουν ~8 GB
    if headroom >= 4:
        return avail, 12, 60.0     # ~7 λεπτά/paper (~60 σελ)
    if headroom >= 2:
        return avail, 8, 100.0
    if headroom >= 1:
        return avail, 4, 180.0
    if headroom >= 0.3:
        return avail, 2, 400.0
    return avail, 1, 900.0


def _duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


# Επιλογές μορφής: (κείμενο, τιμή). Το "db" περνάει από build.py, τα υπόλοιπα από convert.py.
FORMAT_OPTIONS = [
    ("Database + JSON — writing & verification", "db"),
    ("JSON", "json"),
    ("Markdown", "md"),
    ("HTML", "html"),
]

# Προφίλ μνήμης OCR -> σελίδες ανά batch (--chunk). None = αυτόματο από RAM.
PROFILE_OPTIONS = [
    ("Auto · based on free RAM", None),
    ("Fast · 12 pages per batch", 12),
    ("Balanced · 4 pages per batch", 4),
    ("Light · 1 page per batch", 1),
]


class PdfExtractorWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="PDFExtractor")
        self.set_default_size(1000, 700)

        # ---- Κατάσταση ----
        self.pdfs: list[str] = []
        self.page_counts: dict[str, int] = {}
        self.is_fast: dict[str, bool] = {}       # έχει text layer -> χωρίς OCR
        self.row_widgets: dict[str, Gtk.Widget] = {}
        self.proc: subprocess.Popen | None = None
        self.running = False

        # ---- HeaderBar (τίτλος + min/max/close) ----
        header = Gtk.HeaderBar()
        title = Gtk.Label(label="PDFExtractor")
        title.add_css_class("title")
        header.set_title_widget(title)
        self.set_titlebar(header)

        # ---- Workspace (κάθετο, padding 28) ----
        workspace = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        workspace.add_css_class("workspace")
        self.set_child(workspace)

        app_title = Gtk.Label(label="PDFExtractor", xalign=0)
        app_title.add_css_class("app-title")
        workspace.append(app_title)

        subtitle = Gtk.Label(
            label="Turn PDFs into structured, searchable knowledge", xalign=0)
        subtitle.add_css_class("dim-label")
        workspace.append(subtitle)

        # ---- MainArea: Paned (Documents | Settings) ----
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(True)
        paned.set_vexpand(True)
        paned.set_hexpand(True)
        paned.set_start_child(self._build_documents())
        paned.set_end_child(self._build_settings())
        paned.set_resize_start_child(True)
        paned.set_resize_end_child(False)
        # ~65% στα Documents (θέση σε px, με βάση το default width μείον padding)
        paned.set_position(620)
        workspace.append(paned)

        # ---- ActionBar ----
        workspace.append(self._build_action_bar())

        self._refresh_summary()

    # ------------------------------------------------------------------ UI
    def _build_documents(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        card.set_margin_end(10)

        heading = Gtk.Label(label="Documents", xalign=0)
        heading.add_css_class("heading")
        heading.add_css_class("dim-label")
        card.append(heading)

        # Drop zone — clickable + πραγματικό OS drag & drop
        self.dropzone = Gtk.Button(label="Drop PDFs here  ·  or click Add files")
        self.dropzone.add_css_class("dropzone")
        self.dropzone.connect("clicked", lambda *_: self._open_file_chooser())
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        drop.connect("enter", lambda *_: (self.dropzone.add_css_class("drag-over"),
                                          Gdk.DragAction.COPY)[1])
        drop.connect("leave", lambda *_: self.dropzone.remove_css_class("drag-over"))
        self.dropzone.add_controller(drop)
        card.append(self.dropzone)

        self.queue_summary = Gtk.Label(label="No files yet", xalign=0)
        self.queue_summary.add_css_class("dim-label")
        card.append(self.queue_summary)

        # Λίστα αρχείων (scrollable)
        self.file_list = Gtk.ListBox()
        self.file_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.file_list.add_css_class("file-list")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.file_list)
        scroller.set_vexpand(True)
        card.append(scroller)

        add_btn = Gtk.Button(label="＋  Add more PDFs")
        add_btn.add_css_class("suggested-action")
        add_btn.set_halign(Gtk.Align.START)
        add_btn.connect("clicked", lambda *_: self._open_file_chooser())
        card.append(add_btn)
        return card

    def _build_settings(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("card")

        heading = Gtk.Label(label="Extraction settings", xalign=0)
        heading.add_css_class("heading")
        heading.add_css_class("dim-label")
        card.append(heading)

        card.append(self._field_label("Output format"))
        self.format_dd = Gtk.DropDown.new_from_strings(
            [text for text, _ in FORMAT_OPTIONS])
        self.format_dd.connect("notify::selected", lambda *_: self._refresh_summary())
        card.append(self.format_dd)

        card.append(self._field_label("Output folder"))
        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.folder_entry = Gtk.Entry()
        self.folder_entry.set_hexpand(True)
        self.folder_entry.set_placeholder_text("empty = next to the PDFs")
        folder_row.append(self.folder_entry)
        browse = Gtk.Button(label="Browse…")
        browse.add_css_class("suggested-action")
        browse.connect("clicked", lambda *_: self._open_folder_chooser())
        folder_row.append(browse)
        card.append(folder_row)

        self.auto_detect = Gtk.CheckButton(label="Automatically detect text layer")
        self.auto_detect.set_active(True)
        self.auto_detect.connect("toggled", self._on_auto_detect)
        card.append(self.auto_detect)

        self.force_ocr = Gtk.CheckButton(label="Force OCR for every document (slow)")
        self.force_ocr.set_active(False)
        self.force_ocr.connect("toggled", self._on_force_ocr)
        card.append(self.force_ocr)

        card.append(self._field_label("OCR memory profile"))
        self.profile_dd = Gtk.DropDown.new_from_strings(
            [text for text, _ in PROFILE_OPTIONS])
        card.append(self.profile_dd)

        self.verify_report = Gtk.CheckButton(
            label="Create per-page verification report")
        self.verify_report.set_active(True)
        card.append(self.verify_report)
        return card

    def _build_action_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.add_css_class("card")
        bar.add_css_class("action-bar")

        self.ready_text = Gtk.Label(label="Ready", xalign=0)
        self.ready_text.add_css_class("dim-label")
        self.ready_text.set_hexpand(True)
        self.ready_text.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        bar.append(self.ready_text)

        self.progress = Gtk.ProgressBar()
        self.progress.set_size_request(180, -1)
        self.progress.set_valign(Gtk.Align.CENTER)
        bar.append(self.progress)

        self.extract_btn = Gtk.Button(label="Extract")
        self.extract_btn.add_css_class("suggested-action")
        self.extract_btn.add_css_class("pill")
        self.extract_btn.connect("clicked", lambda *_: self.start())
        bar.append(self.extract_btn)
        return bar

    def _field_label(self, text: str) -> Gtk.Label:
        return Gtk.Label(label=text, xalign=0)

    # -------------------------------------------------------------- επιλογή
    def _current_format(self) -> str:
        return FORMAT_OPTIONS[self.format_dd.get_selected()][1]

    def _current_chunk(self):
        return PROFILE_OPTIONS[self.profile_dd.get_selected()][1]

    def _wants_force_ocr(self) -> bool:
        # Χωρίς αυτόματη ανίχνευση == επιβολή OCR παντού.
        return self.force_ocr.get_active() or not self.auto_detect.get_active()

    def _on_auto_detect(self, _btn) -> None:
        if not self.auto_detect.get_active():
            self.force_ocr.set_active(True)
        self._refresh_summary()

    def _on_force_ocr(self, _btn) -> None:
        if self.force_ocr.get_active():
            self.auto_detect.set_active(False)
        self._refresh_summary()

    # ------------------------------------------------------------- αρχεία
    def _open_file_chooser(self) -> None:
        dialog = Gtk.FileDialog(title="Choose PDF files")
        pdf_filter = Gtk.FileFilter()
        pdf_filter.set_name("PDF")
        pdf_filter.add_suffix("pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(pdf_filter)
        dialog.set_filters(filters)
        dialog.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog, result) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # cancel
        self._add_paths([f.get_path() for f in files if f.get_path()])

    def _open_folder_chooser(self) -> None:
        dialog = Gtk.FileDialog(title="Output folder")
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder and folder.get_path():
            self.folder_entry.set_text(folder.get_path())

    def _on_drop(self, _target, value, _x, _y) -> bool:
        self.dropzone.remove_css_class("drag-over")
        try:
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
        except Exception as exc:  # noqa: BLE001
            _log_exc("DROP", exc)
            return False
        self._add_paths(paths)
        return True

    def _add_paths(self, paths) -> None:
        for p in paths:
            p = str(p).strip()
            if not (p.lower().endswith(".pdf") and os.path.isfile(p)) or p in self.pdfs:
                continue
            try:
                pages = _pdf_page_count(p)
            except Exception as exc:  # noqa: BLE001
                pages = 0
                _logf.write(f"\nPAGE COUNT FAILED: {p}: {exc}\n")
            try:
                fast = fasttext_extract.has_text_layer(p)
            except Exception as exc:  # noqa: BLE001
                fast = False
                _logf.write(f"\nTEXT LAYER CHECK FAILED: {p}: {exc}\n")
            self.pdfs.append(p)
            self.page_counts[p] = pages
            self.is_fast[p] = fast
            self._append_file_row(p, pages, fast)
        self._refresh_summary()

    def _append_file_row(self, path: str, pages: int, fast: bool) -> None:
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("file-row")
        box.add_css_class("file-fast" if fast else "file-ocr")

        icon = "⚡" if fast else "\U0001f40c"
        pg = f"{pages} pages" if pages else "unknown pages"
        tail = "" if fast else " · OCR"
        label = Gtk.Label(label=f"{icon}  {os.path.basename(path)}    {pg}{tail}",
                          xalign=0)
        label.set_ellipsize(3)
        label.set_hexpand(True)
        box.append(label)

        remove = Gtk.Button(label="✕")
        remove.add_css_class("flat")
        remove.connect("clicked", self._on_remove, path)
        box.append(remove)

        row.set_child(box)
        self.file_list.append(row)
        self.row_widgets[path] = row

    def _on_remove(self, _btn, path: str) -> None:
        row = self.row_widgets.pop(path, None)
        if row is not None:
            self.file_list.remove(row)
        if path in self.pdfs:
            self.pdfs.remove(path)
        self.page_counts.pop(path, None)
        self.is_fast.pop(path, None)
        self._refresh_summary()

    # ----------------------------------------------------------- σύνοψη
    def _refresh_summary(self) -> None:
        count = len(self.pdfs)
        pages = sum(self.page_counts.get(p, 0) for p in self.pdfs)
        force = self._wants_force_ocr()
        n_ocr = count if force else sum(
            1 for p in self.pdfs if not self.is_fast.get(p, True))
        n_fast = count - n_ocr

        if count:
            self.queue_summary.set_text(
                f"{count} file{'s' * (count != 1)} · {pages} pages · "
                f"{n_fast} fast · {n_ocr} need OCR")
        else:
            self.queue_summary.set_text("No files yet")

        self.extract_btn.set_label(
            f"Extract {count} PDF{'s' * (count != 1)}" if count else "Extract")
        self.extract_btn.set_sensitive(count > 0 and not self.running)

        if self.running:
            return
        if not count:
            self.ready_text.set_text("Ready")
            return
        ocr_pages = pages if force else sum(
            self.page_counts.get(p, 0) for p in self.pdfs
            if not self.is_fast.get(p, True))
        if ocr_pages:
            _, batch, sec_per_page = _ram_batch_speed()
            eta = _duration(ocr_pages * sec_per_page)
            self.ready_text.set_text(
                f"Ready · {ocr_pages} OCR pages · estimated {eta}")
        else:
            self.ready_text.set_text("Ready · all files have a text layer (fast)")

    # ------------------------------------------------------------- εκτέλεση
    def start(self) -> None:
        if self.running or not self.pdfs:
            return
        dest = self.folder_entry.get_text().strip()
        if dest:
            Path(dest).expanduser().mkdir(parents=True, exist_ok=True)

        fmt = self._current_format()
        chunk = self._current_chunk()
        force = self._wants_force_ocr()

        if fmt == "db":
            db_dir = Path(dest).expanduser() if dest else Path(self.pdfs[0]).resolve().parent
            db_path = db_dir / "papers.db"
            # ίδιος φάκελος --out: παίρνεις βάση + JSON (+report) μαζί
            cmd = worker_cmd("build", "--db", str(db_path), "--out", str(db_dir),
                             "--kind", "general", "--no-embed", *self.pdfs)
        else:
            cmd = worker_cmd("convert", *self.pdfs, "--format", fmt)
            if chunk:
                cmd += ["--chunk", str(chunk)]
            if dest:
                cmd += ["--out", dest]
        if force:
            cmd.append("--force-ocr")

        self.running = True
        self._total_pages = sum(max(1, self.page_counts.get(p, 0)) for p in self.pdfs)
        self._page_prefix = 0
        self._timed_pages = 0
        self._processing_seconds = 0.0
        self._cur = ""
        self.progress.set_fraction(0.0)
        self.extract_btn.set_sensitive(False)
        self.ready_text.set_text(
            f"Loading Marker models… {self._total_pages} pages total")

        threading.Thread(target=self._run_proc, args=(cmd,), daemon=True).start()

    def _run_proc(self, cmd: list[str]) -> None:
        try:
            self.proc = subprocess.Popen(cmd, **popen_kwargs())
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                GLib.idle_add(self._handle_line, line.rstrip("\n"))
            self.proc.wait()
        except Exception as exc:  # noqa: BLE001
            GLib.idle_add(self._handle_line, f"ERROR|launcher|{exc}")
        finally:
            GLib.idle_add(self._finish)

    def _handle_line(self, line: str) -> bool:
        parts = line.split("|")
        tag = parts[0]
        if tag == "PROGRESS" and len(parts) >= 4:
            i, n, name = parts[1], parts[2], parts[3]
            pdf_index = max(0, int(i) - 1)
            self._page_prefix = sum(
                max(1, self.page_counts.get(p, 0)) for p in self.pdfs[:pdf_index])
            self.progress.set_fraction(
                min(1.0, self._page_prefix / max(1, self._total_pages)))
            self._cur = f"[{i}/{n}] {name}"
            pages = (self.page_counts.get(self.pdfs[pdf_index], 0)
                     if pdf_index < len(self.pdfs) else 0)
            self.ready_text.set_text(f"{self._cur} — {pages or '?'} pages, preparing…")
        elif tag == "CHUNK" and len(parts) >= 4:
            a, b, tot = parts[1], parts[2], parts[3]
            self.ready_text.set_text(f"{self._cur} — pages {a}-{b} of {tot}…")
        elif tag == "CHUNK_DONE" and len(parts) >= 5:
            a, b, tot = int(parts[1]), int(parts[2]), int(parts[3])
            elapsed = float(parts[4])
            self._timed_pages += b - a + 1
            self._processing_seconds += elapsed
            completed = min(self._total_pages, self._page_prefix + b)
            self.progress.set_fraction(completed / max(1, self._total_pages))
            per_page = self._processing_seconds / max(1, self._timed_pages)
            eta = _duration(per_page * max(0, self._total_pages - completed))
            percent = 100 * completed / max(1, self._total_pages)
            self.ready_text.set_text(
                f"{self._cur} — {completed}/{self._total_pages} pages "
                f"({percent:.0f}%) · ETA {eta}")
        elif tag == "DONE":
            if self._current_format() != "db":
                self._page_prefix = min(self._total_pages, self._page_prefix + 1)
                self.progress.set_fraction(self._page_prefix / max(1, self._total_pages))
        elif tag == "ING" and len(parts) >= 3:
            self.ready_text.set_text(f"Into database: {parts[1]} — {parts[2]}")
        elif tag == "STAGE" and len(parts) >= 2:
            stage = {"convert": "Converting PDF → JSON…",
                     "ingest": "Ingesting into SQLite…",
                     "embed": "Semantic embedding…"}
            self.ready_text.set_text(stage.get(parts[1], parts[1]))
        elif tag == "ERROR":
            who = parts[1] if len(parts) > 1 else "?"
            msg = parts[2] if len(parts) > 2 else ""
            self.ready_text.set_text(f"Error in {who}")
            self._error_dialog(who, msg)
        elif tag == "ALLDONE" and len(parts) >= 3:
            ok, fail = parts[1], parts[2]
            self.ready_text.set_text(f"Finished: {ok} ok, {fail} failed.")
        return False  # μην ξανακληθεί από τον idle loop

    def _finish(self) -> bool:
        self.running = False
        self.progress.set_fraction(1.0)
        self.extract_btn.set_sensitive(len(self.pdfs) > 0)
        text = self.ready_text.get_text()
        if "Finished" not in text and "Error" not in text:
            self.ready_text.set_text("Done.")
        return False

    def _error_dialog(self, who: str, msg: str) -> None:
        dialog = Gtk.AlertDialog()
        dialog.set_message(f"Error: {who}")
        dialog.set_detail(msg or "See gui_crash.log for details.")
        dialog.show(self)


class PdfExtractorApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.pdfextractor.gtk")

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        win = self.props.active_window
        if win is None:
            win = PdfExtractorWindow(self)
        win.present()


def main() -> int:
    _logf.write(
        f"\n##### GTK4 START (wayland={os.environ.get('WAYLAND_DISPLAY')}) #####\n")
    _logf.flush()
    try:
        app = PdfExtractorApp()
        return app.run(None)
    except BaseException as exc:  # noqa: BLE001
        _log_exc("FATAL in main", exc)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
PDF -> HTML GUI (tkinter).

Παράθυρο:
  • Πάνω: περιοχή για τα PDF — drag & drop ΚΑΙ κουμπί επιλογής αρχείων.
  • Κάτω: φάκελος προορισμού (textbox + "Αναζήτηση…").
  • Κουμπί "Μετατροπή" + spinner που γυρίζει όσο δουλεύει (για να ξέρεις ότι δεν κόλλησε).

Τρέχει το convert.py σε ξεχωριστή διεργασία (ίδιο .venv) και διαβάζει την πρόοδο,
ώστε το παράθυρο να μένει ζωντανό.
"""
from __future__ import annotations

import faulthandler
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ui_style import MONO_FONT, UI_FONT, configure_fonts

# ---- Crash logging: ό,τι σκάσει (ακόμα και segfault) γράφεται εδώ ----
_LOG = Path(__file__).resolve().parent / "gui_crash.log"
_logf = open(_LOG, "a", buffering=1, encoding="utf-8")
faulthandler.enable(file=_logf)


def _log_exc(prefix: str, exc: BaseException) -> None:
    _logf.write(f"\n=== {prefix} ===\n")
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=_logf)
    _logf.flush()

# Προαιρετικό: πραγματικό OS drag & drop μέσω tkinterdnd2
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    _HAS_DND = True
except Exception:  # noqa: BLE001
    _HAS_DND = False

HERE = Path(__file__).resolve().parent
PY = str((HERE / ".venv" / "bin" / "python"))  # interpreter με εγκατεστημένο το Marker
CONVERT = str(HERE / "convert.py")
BUILD = str(HERE / "build.py")

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _ram_info():
    """Διαβάζει ελεύθερη RAM -> (avail_gb, batch, εκτίμηση ταχύτητας, χρώμα, ζώνη)."""
    avail = 4.0
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable"):
                    avail = int(ln.split()[1]) / 1048576
                    break
    except Exception:
        pass
    headroom = avail - 8.5  # τα μοντέλα Marker θέλουν ~8 GB
    if headroom >= 4:
        return avail, 12, "πολύ γρήγορα (~7 λεπτά/paper)", "#2e7d32", "green"
    if headroom >= 2:
        return avail, 8, "γρήγορα (~10 λεπτά/paper)", "#43a047", "green"
    if headroom >= 1:
        return avail, 4, "μέτρια (~18 λεπτά/paper)", "#f9a825", "yellow"
    if headroom >= 0.3:
        return avail, 2, "αργά (~40 λεπτά/paper)", "#ef6c00", "orange"
    return avail, 1, "πολύ αργά (~90 λεπτά/paper)", "#c62828", "red"


def _pdf_page_count(path: str) -> int:
    """Read the PDF page tree without loading Marker/OCR models."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    try:
        return len(doc)
    finally:
        doc.close()


def _duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}ω {minutes:02d}λ"
    if minutes:
        return f"{minutes}λ {seconds:02d}δ"
    return f"{seconds}δ"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        configure_fonts(root)
        # Πιάσε εξαιρέσεις μέσα σε tkinter callbacks (drop, κουμπιά) -> log + μήνυμα
        root.report_callback_exception = self._tk_error
        root.title("PDF → HTML (Marker)")
        root.geometry("640x520")
        root.minsize(560, 460)

        self.pdfs: list[str] = []
        self.page_counts: dict[str, int] = {}
        self.proc: subprocess.Popen | None = None
        self.q: "queue.Queue[str]" = queue.Queue()
        self.spin_i = 0
        self.running = False

        pad = {"padx": 12, "pady": 6}

        # ---- Δείκτης μνήμης/ταχύτητας (διαβάζεται όταν ανοίγει η εφαρμογή) ----
        gauge = ttk.LabelFrame(root, text="Ταχύτητα (ανάλογα με την ελεύθερη μνήμη)")
        gauge.pack(fill="x", **pad)
        self.ram_canvas = tk.Canvas(gauge, height=26, highlightthickness=0)
        self.ram_canvas.pack(fill="x", padx=8, pady=(8, 2))
        row = ttk.Frame(gauge)
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.ram_label = ttk.Label(row, text="…", font=(UI_FONT, 10, "bold"))
        self.ram_label.pack(side="left")
        self.ram_btn = ttk.Button(row, text="↻ Ανανέωση", width=12,
                                  command=self._refresh_ram)
        self.ram_btn.pack(side="right")
        self.ram_canvas.bind("<Configure>", lambda e: self._draw_gauge())
        self.root.after(60, self._refresh_ram)

        # ---- Πάνω: αρχεία PDF ----
        self.files_frame = ttk.LabelFrame(
            root, text="1) PDF αρχεία (ρίξε τα εδώ ή πάτα «Προσθήκη»)")
        top = self.files_frame
        top.pack(fill="both", expand=True, **pad)

        self.listbox = tk.Listbox(top, selectmode=tk.EXTENDED, height=8)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(top, orient="vertical", command=self.listbox.yview)
        sb.pack(side="left", fill="y", pady=8)
        self.listbox.config(yscrollcommand=sb.set)

        btns = ttk.Frame(top)
        btns.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Button(btns, text="Προσθήκη…", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Αφαίρεση", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="Καθαρισμός", command=self.clear_files).pack(fill="x", pady=2)

        if _HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.on_drop)

        # ---- Κάτω: προορισμός ----
        dest = ttk.LabelFrame(root, text="2) Φάκελος προορισμού (κενό = δίπλα στα PDF)")
        dest.pack(fill="x", **pad)
        self.dest_var = tk.StringVar(value="")
        ttk.Entry(dest, textvariable=self.dest_var).pack(
            side="left", fill="x", expand=True, padx=8, pady=8
        )
        ttk.Button(dest, text="Αναζήτηση…", command=self.pick_dest).pack(
            side="left", padx=(0, 8), pady=8
        )

        # ---- Μορφή ----
        fmt_row = ttk.Frame(root)
        fmt_row.pack(fill="x", padx=12)
        ttk.Label(fmt_row, text="Μορφή:").pack(side="left")
        self.fmt_var = tk.StringVar(value="json")
        for value, label in (("json", "JSON"), ("md", "Markdown"),
                             ("html", "HTML"), ("db", "SQLite βάση")):
            ttk.Radiobutton(fmt_row, text=label, value=value,
                            variable=self.fmt_var).pack(side="left", padx=4)

        # ---- Εκκίνηση + spinner + κατάσταση ----
        run_row = ttk.Frame(root)
        run_row.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run_row, text="▶  Μετατροπή", command=self.start)
        self.start_btn.pack(side="left")
        self.spinner = ttk.Label(run_row, text=" ", font=(MONO_FONT, 16), width=3)
        self.spinner.pack(side="left", padx=8)
        self.status = ttk.Label(run_row, text="Έτοιμο.")
        self.status.pack(side="left")

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=(0, 4))

        if not _HAS_DND:
            ttk.Label(
                root,
                text="(Drag & drop ανενεργό — λείπει το tkinterdnd2· χρησιμοποίησε «Προσθήκη…»)",
                foreground="#888",
            ).pack(padx=12, anchor="w")

        self.root.after(100, self._poll_queue)

    # ---------- δείκτης μνήμης/ταχύτητας ----------
    def _refresh_ram(self) -> None:
        self._ram = _ram_info()  # (avail, batch, speed, color, zone)
        avail, batch, speed, color, _zone = self._ram
        self.ram_label.config(
            text=f"{avail:.1f} GB ελεύθερα  →  batch {batch}  →  {speed}",
            foreground=color)
        self._draw_gauge()

    def _draw_gauge(self) -> None:
        c = self.ram_canvas
        if not hasattr(self, "_ram"):
            return
        c.delete("all")
        w = c.winfo_width() or 480
        h = int(c["height"])
        # ζώνες: κόκκινο | πορτοκαλί | κίτρινο | πράσινο
        zones = [("#c62828", 0.0, 0.28), ("#ef6c00", 0.28, 0.46),
                 ("#f9a825", 0.46, 0.64), ("#43a047", 0.64, 1.0)]
        for col, a, b in zones:
            c.create_rectangle(a * w, 4, b * w, h - 4, fill=col, width=0)
        # θέση δείκτη: avail 8→0 .. 13→1
        avail = self._ram[0]
        frac = max(0.0, min(1.0, (avail - 8.0) / (13.0 - 8.0)))
        x = frac * w
        c.create_polygon(x - 7, h - 4, x + 7, h - 4, x, h - 14,
                         fill="#111", outline="white")
        c.create_line(x, 2, x, h - 2, fill="#111", width=2)

    def _tk_error(self, exc, val, tb) -> None:  # type: ignore[no-untyped-def]
        _logf.write("\n=== TK CALLBACK ERROR ===\n")
        traceback.print_exception(exc, val, tb, file=_logf)
        _logf.flush()
        try:
            messagebox.showerror("Σφάλμα", f"{val}\n\n(λεπτομέρειες: gui_crash.log)")
        except Exception:  # noqa: BLE001
            pass

    # ---------- αρχεία ----------
    def add_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="Διάλεξε PDF", filetypes=[("PDF", "*.pdf"), ("Όλα", "*.*")]
        )
        self._add_paths(files)

    def on_drop(self, event) -> None:  # type: ignore[no-untyped-def]
        # Το event.data έρχεται ως "{path με κενά} other" -> το σπάμε σωστά
        paths = self.root.tk.splitlist(event.data)
        self._add_paths(paths)

    def _add_paths(self, paths) -> None:  # type: ignore[no-untyped-def]
        for p in paths:
            p = str(p).strip()
            if p.lower().endswith(".pdf") and p not in self.pdfs and os.path.isfile(p):
                try:
                    pages = _pdf_page_count(p)
                except Exception as exc:  # noqa: BLE001
                    pages = 0
                    _logf.write(f"\nPAGE COUNT FAILED: {p}: {exc}\n")
                self.pdfs.append(p)
                self.page_counts[p] = pages
                detail = f" — {pages} σελίδες" if pages else " — άγνωστες σελίδες"
                self.listbox.insert(tk.END, os.path.basename(p) + detail)
        self._update_file_summary()

    def _update_file_summary(self) -> None:
        total = sum(self.page_counts.get(p, 0) for p in self.pdfs)
        count = len(self.pdfs)
        suffix = f" — {count} αρχεία, {total} σελίδες" if count else ""
        self.files_frame.config(
            text="1) PDF αρχεία (ρίξε τα εδώ ή πάτα «Προσθήκη»)" + suffix)

    def remove_selected(self) -> None:
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx)
            path = self.pdfs.pop(idx)
            self.page_counts.pop(path, None)
        self._update_file_summary()

    def clear_files(self) -> None:
        self.listbox.delete(0, tk.END)
        self.pdfs.clear()
        self.page_counts.clear()
        self._update_file_summary()

    def pick_dest(self) -> None:
        d = filedialog.askdirectory(title="Φάκελος προορισμού")
        if d:
            self.dest_var.set(d)

    # ---------- εκτέλεση ----------
    def start(self) -> None:
        if self.running:
            return
        if not self.pdfs:
            messagebox.showwarning("Προσοχή", "Δεν έχεις προσθέσει PDF.")
            return
        dest = self.dest_var.get().strip()
        if dest:
            Path(dest).expanduser().mkdir(parents=True, exist_ok=True)

        self.running = True
        self.start_btn.config(state="disabled")
        self.ram_btn.config(state="disabled")
        self._total_pages = sum(max(1, self.page_counts.get(p, 0)) for p in self.pdfs)
        self._page_prefix = 0
        self._timed_pages = 0
        self._processing_seconds = 0.0
        self._run_started = time.monotonic()
        self.progress.config(maximum=self._total_pages, value=0)
        self.status.config(
            text=f"Φόρτωση μοντέλων Marker… {self._total_pages} σελίδες συνολικά")

        if self.fmt_var.get() == "db":
            db_dir = Path(dest).expanduser() if dest else Path(self.pdfs[0]).resolve().parent
            db_path = db_dir / "papers.db"
            cmd = [PY, BUILD, "--db", str(db_path), "--kind", "general",
                   "--no-embed", *self.pdfs]
            self.status.config(text=f"Χτίσιμο SQLite βάσης: {db_path.name}")
        else:
            cmd = [PY, CONVERT, *self.pdfs, "--format", self.fmt_var.get()]
            if dest:
                cmd += ["--out", dest]
        threading.Thread(target=self._run_proc, args=(cmd,), daemon=True).start()
        self._spin()

    def _run_proc(self, cmd: list[str]) -> None:
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.q.put(line.rstrip("\n"))
            self.proc.wait()
            self.q.put("__EXIT__")
        except Exception as exc:  # noqa: BLE001
            self.q.put(f"ERROR|launcher|{exc}")
            self.q.put("__EXIT__")

    def _spin(self) -> None:
        if not self.running:
            self.spinner.config(text=" ")
            return
        self.spinner.config(text=SPINNER_FRAMES[self.spin_i % len(SPINNER_FRAMES)])
        self.spin_i += 1
        self.root.after(80, self._spin)

    def _poll_queue(self) -> None:
        try:
            while True:
                line = self.q.get_nowait()
                self._handle_line(line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_line(self, line: str) -> None:
        if line == "__EXIT__":
            self._finish()
            return
        parts = line.split("|")
        tag = parts[0]
        if tag == "PROGRESS" and len(parts) >= 4:
            i, n, name = parts[1], parts[2], parts[3]
            pdf_index = max(0, int(i) - 1)
            self._page_prefix = sum(
                max(1, self.page_counts.get(p, 0)) for p in self.pdfs[:pdf_index])
            self.progress.config(value=self._page_prefix)
            self._cur = f"[{i}/{n}] {name}"
            pages = self.page_counts.get(self.pdfs[pdf_index], 0) if pdf_index < len(self.pdfs) else 0
            self.status.config(text=f"{self._cur} — {pages or '?'} σελίδες, προετοιμασία…")
        elif tag == "CHUNK" and len(parts) >= 4:
            a, b, tot = parts[1], parts[2], parts[3]
            cur = getattr(self, "_cur", "")
            self.status.config(text=f"{cur} — επεξεργασία σελίδων {a}-{b} από {tot}…")
        elif tag == "CHUNK_DONE" and len(parts) >= 5:
            a, b, tot = int(parts[1]), int(parts[2]), int(parts[3])
            elapsed = float(parts[4])
            chunk_pages = b - a + 1
            self._timed_pages += chunk_pages
            self._processing_seconds += elapsed
            completed = min(self._total_pages, self._page_prefix + b)
            self.progress.config(value=completed)
            per_page = self._processing_seconds / max(1, self._timed_pages)
            remaining = max(0, self._total_pages - completed)
            eta = _duration(per_page * remaining)
            percent = 100 * completed / max(1, self._total_pages)
            cur = getattr(self, "_cur", "")
            self.status.config(
                text=f"{cur} — {completed}/{self._total_pages} σελίδες "
                     f"({percent:.0f}%) · εκτίμηση {eta}")
        elif tag == "DONE":
            # build.py emits DONE after conversion and ING after the same document
            # is committed; for DB output, count only the completed DB insert.
            if self.fmt_var.get() != "db":
                self.progress.config(value=self.progress["value"] + 1)
        elif tag == "ING" and len(parts) >= 3:
            self.status.config(text=f"Στη βάση: {parts[1]} — {parts[2]}")
        elif tag == "STAGE" and len(parts) >= 2:
            stage = {"convert": "Μετατροπή PDF → JSON…",
                     "ingest": "Εισαγωγή στη SQLite βάση…",
                     "embed": "Σημασιολογική ενσωμάτωση…"}
            self.status.config(text=stage.get(parts[1], parts[1]))
        elif tag == "ERROR":
            who = parts[1] if len(parts) > 1 else "?"
            msg = parts[2] if len(parts) > 2 else ""
            self.status.config(text=f"Σφάλμα στο {who}")
            messagebox.showerror("Σφάλμα", f"{who}\n\n{msg}")
        elif tag == "ALLDONE" and len(parts) >= 3:
            ok, fail = parts[1], parts[2]
            self.status.config(text=f"Ολοκληρώθηκε: {ok} επιτυχία, {fail} αποτυχία.")

    def _finish(self) -> None:
        self.running = False
        self.start_btn.config(state="normal")
        self.ram_btn.config(state="normal")
        self.spinner.config(text="✓")
        if "Ολοκληρ" not in self.status.cget("text") and "Σφάλμα" not in self.status.cget("text"):
            self.status.config(text="Τέλος.")


def main() -> int:
    _logf.write(f"\n##### START (DND={_HAS_DND}, wayland={os.environ.get('WAYLAND_DISPLAY')}) #####\n")
    _logf.flush()
    try:
        if _HAS_DND:
            root = TkinterDnD.Tk()
        else:
            root = tk.Tk()
        App(root)
        root.mainloop()
    except BaseException as exc:  # noqa: BLE001
        _log_exc("FATAL in main", exc)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

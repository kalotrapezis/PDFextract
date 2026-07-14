#!/usr/bin/env python3
"""
Knowledge-Base GUI (tkinter) — χτίζει τη ΒΑΣΗ (όχι απλή μετατροπή).

Ίδια λογική με το gui.py (PDF→HTML), αλλά για το pipeline βάσης:
  1) Διάλεξε ΧΡΗΣΗ (profile) — research / thesis / lesson / assessment /
     personal / notes / general. Κάθε χρήση = δική της βάση (databases/<key>.db).
  2) Δες ΤΙ μεταδεδομένα κρατά αυτή η χρήση (η «bullet list» — οδηγίες που ο
     agent γεμίζει αυτόματα αργότερα, με δικό σου έλεγχο).
  3) Ρίξε PDF ή/και έτοιμα JSON.
  4) «Χτίσιμο»: convert → ingest → embed (build.py) στη βάση της χρήσης.

Τρέχει το build.py σε ξεχωριστή διεργασία (ίδιο .venv) και διαβάζει την πρόοδο.
"""
from __future__ import annotations

import faulthandler
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from runtime import available_gb, popen_kwargs, worker_cmd
from ui_style import MONO_FONT, UI_FONT, configure_fonts

import profiles as prof_mod

# ---- Crash logging ----
_LOG = Path(__file__).resolve().parent / "gui_crash.log"
_logf = open(_LOG, "a", buffering=1, encoding="utf-8")
faulthandler.enable(file=_logf)


def _log_exc(prefix: str, exc: BaseException) -> None:
    _logf.write(f"\n=== {prefix} ===\n")
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=_logf)
    _logf.flush()


try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    _HAS_DND = True
except Exception:  # noqa: BLE001
    _HAS_DND = False

HERE = Path(__file__).resolve().parent

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _ram_info():
    """Ελεύθερη RAM → (avail_gb, speed, χρώμα). Σημασία γιατί το convert (Marker)
    θέλει ~8 GB."""
    avail = available_gb()
    headroom = avail - 8.5
    if headroom >= 4:
        return avail, "πολύ γρήγορα (~7 λεπτά/PDF)", "#2e7d32"
    if headroom >= 2:
        return avail, "γρήγορα (~10 λεπτά/PDF)", "#43a047"
    if headroom >= 1:
        return avail, "μέτρια (~18 λεπτά/PDF)", "#f9a825"
    if headroom >= 0.3:
        return avail, "αργά (~40 λεπτά/PDF)", "#ef6c00"
    return avail, "πολύ αργά — κλείσε VM/browser", "#c62828"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        configure_fonts(root)
        root.report_callback_exception = self._tk_error
        root.title("PDFExtractor — Χτίσιμο Βάσης")
        root.geometry("760x720")
        root.minsize(660, 620)

        self.files: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.q: "queue.Queue[str]" = queue.Queue()
        self.spin_i = 0
        self.running = False
        self.profiles = {p["key"]: p for p in prof_mod.all_profiles()}

        pad = {"padx": 12, "pady": 6}

        # ---- 1) Χρήση (profile) ----
        pf = ttk.LabelFrame(root, text="1) Χρήση — μία βάση ανά χρήση")
        pf.pack(fill="x", **pad)
        row = ttk.Frame(pf); row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Σκοπός:").pack(side="left")
        self.kind_var = tk.StringVar(value="lesson")
        self.kind_combo = ttk.Combobox(
            row, textvariable=self.kind_var, state="readonly", width=34,
            values=[f"{p['label']}" for p in self.profiles.values()],
        )
        # Χάρτης label→key για ανάγνωση επιλογής
        self._label2key = {p["label"]: k for k, p in self.profiles.items()}
        self.kind_combo.set(self.profiles["lesson"]["label"])
        self.kind_combo.pack(side="left", padx=8)
        self.kind_combo.bind("<<ComboboxSelected>>", lambda e: self._on_profile())

        self.summary_lbl = ttk.Label(pf, text="", foreground="#555", wraplength=700)
        self.summary_lbl.pack(fill="x", padx=8, pady=(0, 4))

        # «Τι κρατά αυτή η χρήση» — η bullet list (read-only οδηγίες)
        meta = ttk.LabelFrame(pf, text="Μεταδεδομένα που κρατά (auto-fill από τον agent, με έλεγχό σου)")
        meta.pack(fill="both", expand=False, padx=8, pady=6)
        self.fields_txt = tk.Text(meta, height=8, wrap="word", font=(UI_FONT, 9),
                                  background="#fafafa", relief="flat")
        self.fields_txt.pack(fill="both", expand=True, padx=6, pady=6)
        self.fields_txt.config(state="disabled")

        # ---- 2) Βάση προορισμού ----
        dest = ttk.LabelFrame(root, text="2) Βάση προορισμού")
        dest.pack(fill="x", **pad)
        self.db_var = tk.StringVar(value="")
        ttk.Entry(dest, textvariable=self.db_var).pack(
            side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(dest, text="Αλλαγή…", command=self.pick_db).pack(
            side="left", padx=(0, 4), pady=8)
        self.db_status = ttk.Label(dest, text="", foreground="#2e7d32")
        self.db_status.pack(side="left", padx=(0, 8))

        # ---- 3) Αρχεία ----
        top = ttk.LabelFrame(root, text="3) Αρχεία — PDF ή έτοιμα JSON (ρίξε τα ή «Προσθήκη»)")
        top.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(top, selectmode=tk.EXTENDED, height=7)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(top, orient="vertical", command=self.listbox.yview)
        sb.pack(side="left", fill="y", pady=8)
        self.listbox.config(yscrollcommand=sb.set)
        btns = ttk.Frame(top); btns.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Button(btns, text="Προσθήκη…", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Αφαίρεση", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="Καθαρισμός", command=self.clear_files).pack(fill="x", pady=2)
        if _HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.on_drop)

        # ---- 4) Επιλογές + RAM ----
        opt = ttk.Frame(root); opt.pack(fill="x", padx=12)
        self.embed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Σημασιολογική ενσωμάτωση (bge-m3)",
                        variable=self.embed_var).pack(side="left")
        self.force_ocr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="🐌 Επιβολή OCR (αργό)",
                        variable=self.force_ocr_var).pack(side="left", padx=12)
        self.ram_label = ttk.Label(opt, text="…", font=(UI_FONT, 9))
        self.ram_label.pack(side="right")
        self.root.after(60, self._refresh_ram)

        # ---- 5) Εκκίνηση + spinner ----
        run_row = ttk.Frame(root); run_row.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run_row, text="▶  Χτίσιμο βάσης", command=self.start)
        self.start_btn.pack(side="left")
        self.spinner = ttk.Label(run_row, text=" ", font=(MONO_FONT, 16), width=3)
        self.spinner.pack(side="left", padx=8)
        self.status = ttk.Label(run_row, text="Έτοιμο.")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=(0, 4))

        if not _HAS_DND:
            ttk.Label(root, text="(Drag & drop ανενεργό — χρησιμοποίησε «Προσθήκη…»)",
                      foreground="#888").pack(padx=12, anchor="w")

        self._on_profile()
        self.root.after(100, self._poll_queue)

    # ---------- profile ----------
    def _on_profile(self) -> None:
        key = self._label2key.get(self.kind_combo.get(), "lesson")
        self.kind_var.set(key)
        p = self.profiles[key]
        self.summary_lbl.config(text=p["summary"])
        self.fields_txt.config(state="normal")
        self.fields_txt.delete("1.0", tk.END)
        for name, desc in p["fields"]:
            self.fields_txt.insert(tk.END, f"• {name}", ("b",))
            self.fields_txt.insert(tk.END, f" — {desc}\n")
        if p.get("sections"):
            self.fields_txt.insert(tk.END, "\nΕνότητες-κλειδιά: ", ("b",))
            self.fields_txt.insert(tk.END, ", ".join(p["sections"]) + "\n")
        self.fields_txt.tag_config("b", font=(UI_FONT, 9, "bold"))
        self.fields_txt.config(state="disabled")
        self.db_var.set(p["db"])
        self._refresh_db_status()

    def _refresh_db_status(self) -> None:
        db = self.db_var.get().strip()
        if not db or not Path(db).exists():
            self.db_status.config(text="(νέα βάση)", foreground="#888")
            return
        try:
            import db as dbmod
            conn = dbmod.connect(db, vec=False)
            n_p = conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
            n_s = conn.execute("SELECT COUNT(*) c FROM sections").fetchone()["c"]
            emb = 0
            try:
                emb = conn.execute("SELECT COUNT(*) c FROM section_embed_meta").fetchone()["c"]
            except Exception:
                pass
            conn.close()
            self.db_status.config(
                text=f"{n_p} έγγραφα · {n_s} ενότητες · {emb} embedded",
                foreground="#2e7d32")
        except Exception:
            self.db_status.config(text="(βάση)", foreground="#888")

    # ---------- RAM ----------
    def _refresh_ram(self) -> None:
        avail, speed, color = _ram_info()
        self.ram_label.config(text=f"RAM: {avail:.1f} GB → convert {speed}", foreground=color)

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
            title="Διάλεξε PDF ή JSON",
            filetypes=[("PDF/JSON", "*.pdf *.json"), ("PDF", "*.pdf"),
                       ("JSON", "*.json"), ("Όλα", "*.*")])
        self._add_paths(files)

    def on_drop(self, event) -> None:  # type: ignore[no-untyped-def]
        self._add_paths(self.root.tk.splitlist(event.data))

    def _add_paths(self, paths) -> None:  # type: ignore[no-untyped-def]
        for p in paths:
            p = str(p).strip()
            low = p.lower()
            if (low.endswith(".pdf") or low.endswith(".json")) and p not in self.files \
                    and os.path.isfile(p):
                self.files.append(p)
                self.listbox.insert(tk.END, os.path.basename(p))

    def remove_selected(self) -> None:
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx)
            del self.files[idx]

    def clear_files(self) -> None:
        self.listbox.delete(0, tk.END)
        self.files.clear()

    def pick_db(self) -> None:
        d = filedialog.asksaveasfilename(
            title="Βάση προορισμού", defaultextension=".db",
            initialfile=os.path.basename(self.db_var.get() or "knowledge.db"),
            filetypes=[("SQLite", "*.db"), ("Όλα", "*.*")])
        if d:
            self.db_var.set(d)
            self._refresh_db_status()

    # ---------- εκτέλεση ----------
    def start(self) -> None:
        if self.running:
            return
        if not self.files:
            messagebox.showwarning("Προσοχή", "Δεν έχεις προσθέσει αρχεία.")
            return
        db = self.db_var.get().strip()
        if not db:
            messagebox.showwarning("Προσοχή", "Όρισε βάση προορισμού.")
            return
        kind = self.kind_var.get()
        Path(db).expanduser().parent.mkdir(parents=True, exist_ok=True)

        self.running = True
        self.start_btn.config(state="disabled")
        self.progress.config(maximum=max(1, len(self.files)), value=0)
        self.status.config(text="Εκκίνηση pipeline…")

        # --out ο φάκελος της βάσης: παίρνεις ΚΑΙ βάση ΚΑΙ JSON (+report) στο ίδιο τρέξιμο
        cmd = worker_cmd("build", "--db", db, "--out", str(Path(db).parent),
                         "--kind", kind, *self.files)
        if not self.embed_var.get():
            cmd.append("--no-embed")
        if self.force_ocr_var.get():
            cmd.append("--force-ocr")
        threading.Thread(target=self._run_proc, args=(cmd,), daemon=True).start()
        self._spin()

    def _run_proc(self, cmd: list[str]) -> None:
        try:
            self.proc = subprocess.Popen(cmd, **popen_kwargs())
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
                self._handle_line(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_line(self, line: str) -> None:
        if line == "__EXIT__":
            self._finish(); return
        parts = line.split("|")
        tag = parts[0]
        if tag == "STAGE":
            names = {"convert": "Μετατροπή PDF→JSON (Marker)",
                     "ingest": "Εισαγωγή στη βάση", "embed": "Σημασιολογική ενσωμάτωση"}
            self.status.config(text=names.get(parts[1], parts[1]) + "…")
        elif tag == "PROGRESS" and len(parts) >= 4:
            i, n, name = parts[1], parts[2], parts[3]
            self.progress.config(maximum=int(n), value=int(i) - 1)
            self._cur = f"[{i}/{n}] {name}"
            self.status.config(text=f"Μετατροπή {self._cur} — φόρτωση…")
        elif tag == "CHUNK" and len(parts) >= 4:
            self.status.config(text=f"{getattr(self, '_cur', '')} — σελίδες {parts[1]}-{parts[2]}/{parts[3]}")
        elif tag == "DONE":
            self.progress.config(value=self.progress["value"] + 1)
        elif tag == "ING" and len(parts) >= 3:
            self.status.config(text=f"Εισαγωγή: {parts[1]} — {parts[2]}")
        elif tag == "EMB" and len(parts) >= 3:
            d, t = parts[1], parts[2]
            self.progress.config(maximum=int(t), value=int(d))
            self.status.config(text=f"Ενσωμάτωση ενοτήτων {d}/{t}")
        elif tag == "ERROR":
            who = parts[1] if len(parts) > 1 else "?"
            self.status.config(text=f"Σφάλμα: {who}")
            messagebox.showerror("Σφάλμα", "|".join(parts[1:]))
        elif tag == "ALLDONE" and len(parts) >= 3:
            self.status.config(text=f"Ολοκληρώθηκε: {parts[1]} ok, {parts[2]} σφάλματα.")

    def _finish(self) -> None:
        self.running = False
        self.start_btn.config(state="normal")
        self.spinner.config(text="✓")
        self.progress.config(value=self.progress["maximum"])
        self._refresh_db_status()
        if "Ολοκληρ" not in self.status.cget("text") and "Σφάλμα" not in self.status.cget("text"):
            self.status.config(text="Τέλος.")


def main() -> int:
    _logf.write(f"\n##### DB_GUI START (DND={_HAS_DND}) #####\n")
    _logf.flush()
    try:
        root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
        App(root)
        root.mainloop()
    except BaseException as exc:  # noqa: BLE001
        _log_exc("FATAL in db_gui main", exc)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

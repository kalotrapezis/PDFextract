#!/usr/bin/env python3
"""
Combined «Overnight» GUI — μία διαδρομή: PDF → JSON → ΒΑΣΗ → embeddings → Skill,
όλα σε έναν **φάκελο-πακέτο** που ανοίγεις στο cowork για σύνθεση.

Ροή (build.py, χωρίς επιτήρηση):
  • Convert (Marker) ως υποδιεργασία — η μνήμη ελευθερώνεται μόλις τελειώσει.
  • Κάθε PDF που μετατρέπεται μπαίνει ΑΜΕΣΩΣ στη βάση (crash-safe).
  • Embed (bge-m3) στο τέλος, αφού φύγει το Marker.
  • Γράφει SKILL.md (lightweight query-skill) στον φάκελο.
Όλα τοπικά & δωρεάν — κανένα API key.

Συμπληρώνει (δεν αντικαθιστά) τα δύο ξεχωριστά GUI:
  run.sh → gui.py (μόνο convert) · run_db.sh → db_gui.py (μόνο βάση).
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import profiles as prof_mod
# Επαναχρησιμοποίηση κοινών βοηθητικών από το db_gui (RAM gauge, spinner, crash log).
from db_gui import _HAS_DND, _ram_info, SPINNER_FRAMES, _logf
from runtime import popen_kwargs, worker_cmd
from ui_style import MONO_FONT, UI_FONT, configure_fonts

if _HAS_DND:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE / "output"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        configure_fonts(root)
        root.title("PDFExtractor — Βάση + JSON + Skill")
        root.geometry("780x740")
        root.minsize(680, 640)

        self.files: list[str] = []
        self.q: "queue.Queue[str]" = queue.Queue()
        self.spin_i = 0
        self.running = False
        self.profiles = {p["key"]: p for p in prof_mod.all_profiles()}
        self._label2key = {p["label"]: k for k, p in self.profiles.items()}

        pad = {"padx": 12, "pady": 6}

        # 1) Χρήση + θέμα
        pf = ttk.LabelFrame(root, text="1) Χρήση & θέμα (καθορίζουν το προφίλ και το skill)")
        pf.pack(fill="x", **pad)
        r1 = ttk.Frame(pf); r1.pack(fill="x", padx=8, pady=6)
        ttk.Label(r1, text="Σκοπός:").pack(side="left")
        self.kind_combo = ttk.Combobox(
            r1, state="readonly", width=30,
            values=[p["label"] for p in self.profiles.values()])
        self.kind_combo.set(self.profiles["research"]["label"])
        self.kind_combo.pack(side="left", padx=6)
        self.kind_combo.bind("<<ComboboxSelected>>", lambda e: self._on_profile())
        ttk.Label(r1, text="Θέμα:").pack(side="left", padx=(12, 0))
        self.topic_var = tk.StringVar(value="")
        ttk.Entry(r1, textvariable=self.topic_var, width=26).pack(side="left", padx=6)
        self.summary_lbl = ttk.Label(pf, text="", foreground="#555", wraplength=720)
        self.summary_lbl.pack(fill="x", padx=8, pady=(0, 6))

        # 2) Φάκελος-πακέτο εξόδου
        of = ttk.LabelFrame(root, text="2) Φάκελος-πακέτο (θα περιέχει <όνομα>.db + SKILL.md)")
        of.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=str(OUT_ROOT / "science_papers"))
        ttk.Entry(of, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(of, text="Αλλαγή…", command=self.pick_out).pack(side="left", padx=(0, 8), pady=8)

        # 3) Αρχεία
        top = ttk.LabelFrame(root, text="3) PDF (ή έτοιμα JSON) — ρίξε τα ή «Προσθήκη»")
        top.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(top, selectmode=tk.EXTENDED, height=8)
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

        # 4) Επιλογές + RAM
        opt = ttk.Frame(root); opt.pack(fill="x", padx=12)
        self.embed_var = tk.BooleanVar(value=True)
        self.skill_var = tk.BooleanVar(value=True)
        self.force_ocr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Embeddings (bge-m3)", variable=self.embed_var).pack(side="left")
        ttk.Checkbutton(opt, text="Manifest (README.md)", variable=self.skill_var).pack(side="left", padx=12)
        ttk.Checkbutton(opt, text="🐌 Επιβολή OCR (αργό)",
                        variable=self.force_ocr_var).pack(side="left")
        self.ram_label = ttk.Label(opt, text="…", font=(UI_FONT, 9))
        self.ram_label.pack(side="right")
        self.root.after(60, self._refresh_ram)

        # 5) Run
        run_row = ttk.Frame(root); run_row.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run_row, text="🌙  Χτίσιμο πακέτου (overnight)", command=self.start)
        self.start_btn.pack(side="left")
        self.spinner = ttk.Label(run_row, text=" ", font=(MONO_FONT, 16), width=3)
        self.spinner.pack(side="left", padx=8)
        self.status = ttk.Label(run_row, text="Έτοιμο.")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=(0, 4))
        self.log = tk.Text(root, height=6, font=(MONO_FONT, 8), background="#0d1117",
                           foreground="#c9d1d9", relief="flat")
        self.log.pack(fill="both", expand=False, padx=12, pady=(0, 8))

        if not _HAS_DND:
            ttk.Label(root, text="(Drag & drop ανενεργό — «Προσθήκη…»)",
                      foreground="#888").pack(padx=12, anchor="w")

        self._on_profile()
        self.root.after(100, self._poll_queue)

    # ---- profile ----
    def _on_profile(self) -> None:
        p = self.profiles[self._label2key.get(self.kind_combo.get(), "research")]
        self.summary_lbl.config(text=p["summary"])
        # προεπιλεγμένος φάκελος = output/<key>
        cur = Path(self.out_var.get()).name
        if not cur or cur in (k for k in self.profiles):
            self.out_var.set(str(OUT_ROOT / p["key"]))

    def _kind(self) -> str:
        return self._label2key.get(self.kind_combo.get(), "research")

    def _refresh_ram(self) -> None:
        avail, speed, color = _ram_info()
        self.ram_label.config(text=f"RAM: {avail:.1f} GB → convert {speed}", foreground=color)

    # ---- αρχεία ----
    def add_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="Διάλεξε PDF ή JSON",
            filetypes=[("PDF/JSON", "*.pdf *.json"), ("Όλα", "*.*")])
        self._add_paths(files)

    def on_drop(self, event) -> None:  # type: ignore[no-untyped-def]
        self._add_paths(self.root.tk.splitlist(event.data))

    def _add_paths(self, paths) -> None:  # type: ignore[no-untyped-def]
        for p in paths:
            p = str(p).strip()
            if p.lower().endswith((".pdf", ".json")) and p not in self.files and os.path.isfile(p):
                self.files.append(p)
                self.listbox.insert(tk.END, os.path.basename(p))

    def remove_selected(self) -> None:
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx); del self.files[idx]

    def clear_files(self) -> None:
        self.listbox.delete(0, tk.END); self.files.clear()

    def pick_out(self) -> None:
        d = filedialog.askdirectory(title="Φάκελος-πακέτο εξόδου")
        if d:
            self.out_var.set(d)

    # ---- run ----
    def start(self) -> None:
        if self.running:
            return
        if not self.files:
            messagebox.showwarning("Προσοχή", "Δεν έχεις προσθέσει αρχεία.")
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning("Προσοχή", "Όρισε φάκελο-πακέτο εξόδου.")
            return
        Path(out).expanduser().mkdir(parents=True, exist_ok=True)
        self.running = True
        self.start_btn.config(state="disabled")
        self.progress.config(maximum=max(1, len(self.files)), value=0)
        self.log.delete("1.0", tk.END)
        self.status.config(text="Εκκίνηση…")

        cmd = worker_cmd("build", "--out", out, "--kind", self._kind())
        if self.topic_var.get().strip():
            cmd += ["--topic", self.topic_var.get().strip()]
        if self.skill_var.get():
            cmd.append("--manifest")
        if not self.embed_var.get():
            cmd.append("--no-embed")
        if self.force_ocr_var.get():
            cmd.append("--force-ocr")
        cmd += self.files
        threading.Thread(target=self._run_proc, args=(cmd,), daemon=True).start()
        self._spin()

    def _run_proc(self, cmd: list[str]) -> None:
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs())
            assert proc.stdout is not None
            for line in proc.stdout:
                self.q.put(line.rstrip("\n"))
            proc.wait()
            self.q.put("__EXIT__")
        except Exception as exc:  # noqa: BLE001
            self.q.put(f"ERROR|launcher|{exc}"); self.q.put("__EXIT__")

    def _spin(self) -> None:
        if not self.running:
            self.spinner.config(text=" "); return
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

    def _logln(self, s: str) -> None:
        self.log.insert(tk.END, s + "\n"); self.log.see(tk.END)

    def _handle_line(self, line: str) -> None:
        if line == "__EXIT__":
            self._finish(); return
        parts = line.split("|")
        tag = parts[0]
        if tag == "STAGE":
            names = {"convert": "Μετατροπή PDF→JSON (Marker)…",
                     "ingest": "Εισαγωγή στη βάση…", "embed": "Ενσωμάτωση (bge-m3)…"}
            self.status.config(text=names.get(parts[1], parts[1]))
            self._logln(f"▶ {self.status.cget('text')}")
        elif tag == "PROGRESS" and len(parts) >= 4:
            self.progress.config(maximum=int(parts[2]), value=int(parts[1]) - 1)
            self._cur = f"[{parts[1]}/{parts[2]}] {parts[3]}"
            self.status.config(text=f"Μετατροπή {self._cur}")
        elif tag == "CHUNK" and len(parts) >= 4:
            self.status.config(text=f"{getattr(self,'_cur','')} — σελ. {parts[1]}-{parts[2]}/{parts[3]}")
        elif tag == "ING" and len(parts) >= 3:
            self._logln(f"  ✓ {parts[1]} — {parts[2]}")
            self.progress.config(value=self.progress["value"] + 1)
        elif tag == "EMB" and len(parts) >= 3:
            self.progress.config(maximum=int(parts[2]), value=int(parts[1]))
            self.status.config(text=f"Ενσωμάτωση {parts[1]}/{parts[2]}")
        elif tag == "MANIFEST":
            self._logln(f"  📄 manifest (README.md) → {parts[1] if len(parts)>1 else ''}")
        elif tag == "ERROR":
            self.status.config(text=f"Σφάλμα: {parts[1] if len(parts)>1 else '?'}")
            self._logln("  ✗ " + "|".join(parts[1:]))
        elif tag == "ALLDONE" and len(parts) >= 3:
            self.status.config(text=f"Έτοιμο: {parts[1]} ok, {parts[2]} σφάλματα.")
            self._logln(f"✅ ALLDONE: {parts[1]} ok / {parts[2]} fail")

    def _finish(self) -> None:
        self.running = False
        self.start_btn.config(state="normal")
        self.spinner.config(text="✓")
        self.progress.config(value=self.progress["maximum"])
        out = self.out_var.get().strip()
        if "Έτοιμο" in self.status.cget("text"):
            self._logln(f"📦 Πακέτο: {out}")


def main() -> int:
    _logf.write(f"\n##### KB_GUI START (DND={_HAS_DND}) #####\n"); _logf.flush()
    root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

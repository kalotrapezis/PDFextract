#!/usr/bin/env python3
"""
Προαιρετικός wizard: κατεβάζει εκ των προτέρων τα μοντέλα Surya/Marker με
progress + «Δοκίμασε ξανά» και μετά ανοίγει το κύριο GUI.

Αν τα μοντέλα είναι ήδη έτοιμα (υπάρχει το flag .models_ready), προχωράει
κατευθείαν στο gui.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk
from ui_style import UI_FONT, configure_fonts

HERE = Path(__file__).resolve().parent
# Keep the native system Python/Tk process for the GUI.  The uv standalone
# Python used by Marker bundles Tk 9, which renders Greek incorrectly on some
# Kubuntu/Wayland installations.
GUI_PY = sys.executable
WORKER_PY = str(HERE / ".venv" / "bin" / "python")
FLAG = HERE / ".models_ready"
GUI = str(HERE / "gui.py")

# Μικρό script που κατεβάζει+φορτώνει τα μοντέλα μία φορά (τυπώνει STEP/READY/FAIL)
DL = (
    "import os\n"
    "os.environ.setdefault('TORCH_DEVICE','cpu')\n"
    "for k in ('RECOGNITION','DETECTOR','LAYOUT','TABLE_REC','OCR_ERROR'):\n"
    "    os.environ.setdefault(k+'_BATCH_SIZE','1')\n"
    "print('STEP|Φόρτωση βιβλιοθηκών…',flush=True)\n"
    "from marker.models import create_model_dict\n"
    "print('STEP|Λήψη/φόρτωση μοντέλων Surya (μία φορά)…',flush=True)\n"
    "create_model_dict()\n"
    "print('READY',flush=True)\n"
)


def models_ready() -> bool:
    return FLAG.exists()


class Wizard:
    def __init__(self, root):
        self.root = root
        configure_fonts(root)
        root.title("PDFExtractor — Προαιρετική λήψη μοντέλων OCR")
        root.geometry("560x260")
        self.proc = None

        ttk.Label(root, text="Λήψη μοντέλων Marker (μία φορά)",
                  font=(UI_FONT, 13, "bold")).pack(pady=(16, 4))
        ttk.Label(root, text="Χρειάζεται ~8 GB RAM — κλείσε τυχόν Windows VM. "
                             "Τρέχει στη CPU, οπότε αργεί λίγο.",
                  foreground="#666", wraplength=520).pack(pady=(0, 8))

        self.status = ttk.Label(root, text="Έτοιμο για λήψη.")
        self.status.pack(pady=4)
        self.bar = ttk.Progressbar(root, mode="indeterminate", length=460)
        self.bar.pack(pady=6)

        row = ttk.Frame(root); row.pack(pady=10)
        self.start_btn = ttk.Button(row, text="▶ Ξεκίνα λήψη", command=self.start)
        self.start_btn.pack(side="left", padx=4)
        self.retry_btn = ttk.Button(row, text="↻ Δοκίμασε ξανά", command=self.start)
        self.skip_btn = ttk.Button(row, text="Παράλειψη →", command=self.go_main)
        self.skip_btn.pack(side="left", padx=4)

        self.root.after(400, self.start)  # auto-start

    def start(self):
        self.retry_btn.pack_forget()
        self.start_btn.config(state="disabled")
        self.status.config(text="Ξεκινά…")
        self.bar.start(12)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        ok = False
        try:
            self.proc = subprocess.Popen(
                [WORKER_PY, "-c", DL], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in self.proc.stdout:
                line = line.strip()
                if line.startswith("STEP|"):
                    self.root.after(0, self.status.config, {"text": line[5:]})
                elif line == "READY":
                    ok = True
            self.proc.wait()
            ok = ok and self.proc.returncode == 0
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self.status.config, {"text": f"Σφάλμα: {exc}"})
        self.root.after(0, self._done, ok)

    def _done(self, ok):
        self.bar.stop()
        if ok:
            FLAG.write_text("ok\n", encoding="utf-8")
            self.status.config(text="✓ Έτοιμα! Άνοιγμα εφαρμογής…")
            self.root.after(800, self.go_main)
        else:
            self.status.config(text="✗ Απέτυχε η λήψη. Έλεγξε σύνδεση/μνήμη και ξαναδοκίμασε.")
            self.start_btn.config(state="normal")
            self.retry_btn.pack(side="left", padx=4)

    def go_main(self):
        try:
            subprocess.Popen([GUI_PY, GUI])
        finally:
            self.root.destroy()


def main() -> int:
    if models_ready():
        os.execv(GUI_PY, [GUI_PY, GUI])  # κατευθείαν στο κύριο GUI
        return 0
    root = tk.Tk()
    Wizard(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

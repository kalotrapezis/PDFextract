#!/usr/bin/env python3
"""Κατεβάζει/φορτώνει μία φορά τα μοντέλα Surya/Marker.

Ήταν inline script (`python -c "..."`) μέσα στο firstrun.py — μέσα στο exe δεν
υπάρχει python.exe για `-c`, οπότε έγινε κανονικό worker module.
Τυπώνει STEP|… / READY / FAIL ώστε να το διαβάζει το firstrun GUI.
"""
from __future__ import annotations

import os
import sys


def main(argv=None) -> int:
    os.environ.setdefault("TORCH_DEVICE", "cpu")
    for key in ("RECOGNITION", "DETECTOR", "LAYOUT", "TABLE_REC", "OCR_ERROR"):
        os.environ.setdefault(f"{key}_BATCH_SIZE", "1")

    try:
        print("STEP|Φόρτωση βιβλιοθηκών…", flush=True)
        from marker.models import create_model_dict

        print("STEP|Λήψη/φόρτωση μοντέλων Surya (μία φορά)…", flush=True)
        create_model_dict()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL|{exc}", flush=True)
        return 1

    print("READY", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

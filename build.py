#!/usr/bin/env python3
"""
Pipeline driver: PDF/JSON → ΒΑΣΗ (ανά profile) → embeddings → (προαιρετικά) skill.

Σχεδιασμός για **overnight, χωρίς επιτήρηση**:
  • Convert (Marker) τρέχει ως ΥΠΟΔΙΕΡΓΑΣΙΑ → η μνήμη ~8GB ελευθερώνεται μόλις τελειώσει.
  • **Incremental ingest:** μόλις ένα PDF μετατραπεί, μπαίνει ΑΜΕΣΩΣ στη βάση. Αν το
    Marker σκάσει στο 7ο paper, τα 1-6 είναι ήδη σωσμένα (crash-safe).
  • **Embed στο ΤΕΛΟΣ:** αφού φύγει το Marker, φορτώνεται μία φορά το bge-m3 και
    ενσωματώνει ό,τι λείπει. Τα δύο βαριά μοντέλα ΔΕΝ συνυπάρχουν στη RAM.
  • Όλα **τοπικά & δωρεάν** — κανένα API key.

Στάδια (γραμμές για το GUI):
  STAGE|convert → PROGRESS|i|n|name, DONE|path   (convert.py)
  STAGE|ingest  → ING|<name>|<status>            (ανά paper, incremental)
  STAGE|embed   → EMB|<done>|<total>
  MANIFEST|<path>  (αν ζητηθεί)
  ALLDONE|<ok>|<fail>

Χρήση:
  python build.py --db databases/lesson.db --kind lesson a.pdf b.json
  python build.py --out output/papers --kind research --topic "..." --skill *.pdf
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from runtime import popen_kwargs, worker_cmd

HERE = Path(__file__).resolve().parent


def _log(msg: str) -> None:
    print(msg, flush=True)


def _child_env(db: str) -> dict:
    env = dict(os.environ)
    env["PAPERSDB"] = str(Path(db).expanduser().resolve())
    return env


def _stream(cmd: list[str], env: dict):
    proc = subprocess.Popen(cmd, env=env, **popen_kwargs())
    assert proc.stdout is not None
    for line in proc.stdout:
        yield line.rstrip("\n")
    proc.wait()


def _resolve_db(db: str | None, out_dir: str | None) -> tuple[str, str | None]:
    """Επιστρέφει (db_path, out_dir). Αν δοθεί out_dir, η βάση μπαίνει μέσα του
    ως <όνομα-φακέλου>.db (αυτόνομο πακέτο παράδοσης), εκτός αν δόθηκε και
    ρητό --db. Στην τελευταία περίπτωση κρατάμε ακριβώς το ζητούμενο όνομα βάσης
    και χρησιμοποιούμε το --out μόνο για τα JSON/report."""
    if out_dir:
        od = Path(out_dir).expanduser().resolve()
        if db:
            return str(Path(db).expanduser().resolve()), str(od)
        name = od.name or "knowledge"
        return str(od / f"{name}.db"), str(od)
    if not db:
        raise SystemExit("Δώσε --db ή --out")
    return str(Path(db).expanduser().resolve()), None


def run(db=None, kind="general", files=(), embed=True,
        out_dir=None, topic=None, gen_skill=False, force_ocr=False) -> int:
    import db as dbmod
    import ingest as ingmod

    db_path, out_dir = _resolve_db(db, out_dir)
    env = _child_env(db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    pdfs = [f for f in files if f.lower().endswith(".pdf")]
    jsons = [f for f in files if f.lower().endswith(".json")]
    counters = {"ok": 0, "fail": 0}

    conn = dbmod.connect(db_path, vec=False)  # ingest: χωρίς vec (το embed.py το φορτώνει)

    def ingest_one(jp: str) -> None:
        name = Path(jp).stem
        try:
            ingmod.ingest_json(conn, jp, reset=False)
            conn.execute("UPDATE papers SET kind=? WHERE kind IS NULL OR kind=''", (kind,))
            conn.commit()
            counters["ok"] += 1
            _log(f"ING|{name}|ok")
        except Exception as exc:  # noqa: BLE001
            counters["fail"] += 1
            _log(f"ING|{name}|σφάλμα: {exc}")

    # Έτοιμα JSON πρώτα (instant)
    if jsons:
        _log("STAGE|ingest")
        for jp in jsons:
            ingest_one(jp)

    # PDF → JSON με ΑΜΕΣΗ ingest ανά paper.
    # Τα JSON μπαίνουν ΜΕΣΑ στο πακέτο (json/), όχι δίπλα στα PDF: τα θέλεις και τα δύο
    # — JSON για τη συγγραφή (πλήρες κείμενο + report), βάση για αναζήτηση/έλεγχο.
    if pdfs:
        _log("STAGE|convert")
        json_dir = Path(out_dir or Path(db_path).parent) / "json"
        json_dir.mkdir(parents=True, exist_ok=True)
        args = ["convert", *pdfs, "--format", "json", "--out", str(json_dir)]
        if force_ocr:
            args.append("--force-ocr")
        for line in _stream(worker_cmd(*args), env):
            _log(line)
            if line.startswith("DONE|"):
                ingest_one(line.split("|", 1)[1])
            elif line.startswith("ERROR|"):
                counters["fail"] += 1

    conn.close()  # κλείσε πριν το embed (δικό του connection)

    if counters["ok"] == 0:
        _log(f"ALLDONE|{counters['ok']}|{counters['fail']}")
        return 0

    # Embed στο τέλος (Marker έχει φύγει → μόνο bge-m3 στη RAM)
    if embed:
        _log("STAGE|embed")
        for line in _stream(worker_cmd("embed"), env):
            s = line.strip()
            if s.startswith("…") and "ενότητες" in s:
                frac = s.lstrip("…").split()[0]
                if "/" in frac:
                    d, t = frac.split("/")
                    _log(f"EMB|{d}|{t}")
            elif line.startswith(("DONE|", "OK|", "ERROR|", "INFO|")):
                _log(line)

    # Manifest (README.md) στον φάκελο εξόδου — δείχνει στο skill `knowledge-base`
    if gen_skill:
        import make_skill
        target = out_dir or str(Path(db_path).parent)
        path = make_skill.generate(kind, db_path, topic, target)
        _log(f"MANIFEST|{path}")

    _log(f"ALLDONE|{counters['ok']}|{counters['fail']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Pipeline: PDF/JSON → βάση (profile) → embeddings → skill.")
    p.add_argument("--db", default=None, help="Διαδρομή βάσης (ή χρησιμοποίησε --out)")
    p.add_argument("--out", default=None, help="Φάκελος-πακέτο: γράφει <όνομα>.db + SKILL.md μέσα")
    p.add_argument("--kind", default="general", help="Profile/χρήση (research/lesson/…)")
    p.add_argument("--topic", default=None, help="Θέμα (για το manifest)")
    p.add_argument("--manifest", "--skill", dest="manifest", action="store_true",
                   help="Γράψε README.md manifest στον φάκελο (δείχνει στο skill knowledge-base)")
    p.add_argument("--no-embed", action="store_true", help="Παράλειψε τη σημασιολογική ενσωμάτωση")
    p.add_argument("--force-ocr", action="store_true",
                   help="Επιβολή Marker/OCR σε ΟΛΑ τα PDF (αργό· αγνοεί το text layer)")
    p.add_argument("files", nargs="+", help="PDF ή/και JSON αρχεία")
    args = p.parse_args(argv)
    return run(db=args.db, kind=args.kind, files=args.files, embed=not args.no_embed,
               out_dir=args.out, topic=args.topic, gen_skill=args.manifest,
               force_ocr=args.force_ocr)


if __name__ == "__main__":
    sys.exit(main())

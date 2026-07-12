#!/usr/bin/env python3
"""
Γεννήτρια **manifest** (README.md) για έναν φάκελο-πακέτο βάσης.

ΔΕΝ είναι αυτόνομο skill — είναι ένα σύντομο σημείωμα μέσα στον φάκελο που λέει:
«αυτή είναι μια βάση τύπου <kind> στο <db>· χρησιμοποίησε το skill **knowledge-base**».
Έτσι αποφεύγουμε πολλαπλά εγγεγραμμένα skills (ένα ανά βάση). Η πραγματική λογική
(build/finish/query) ζει στο εγγεγραμμένο skill `knowledge-base`.

Χρήση (CLI):
    python make_skill.py --kind lesson --db output/tpe/tpe.db \
        --topic "Πληροφορική Δημοτικού" --out output/tpe
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import profiles as prof_mod

HERE = Path(__file__).resolve().parent
PY = str(HERE / ".venv" / "bin" / "python")
BIB_COLS = {"authors", "year", "journal", "doi", "apa"}


def render(kind: str, db_path: str, topic: str | None) -> str:
    prof = prof_mod.get(kind)
    db = str(Path(db_path).resolve())
    topic = topic or prof["label"]
    meta_keys = [n for n, _ in prof["fields"] if n not in BIB_COLS]
    meta_example = json.dumps({k: "…" for k in meta_keys}, ensure_ascii=False)
    fields = ", ".join(n for n, _ in prof["fields"])
    Q = str(HERE / "query.py")
    A = str(HERE / "annotate.py")

    return f"""# {topic}

**Knowledge base — χρήση: `{kind}` ({prof['label']})**
{prof['summary']}

> 🧭 Αυτός ο φάκελος ΔΕΝ είναι ξεχωριστό skill. Για build/finish/query χρησιμοποίησε
> το εγγεγραμμένο skill **`knowledge-base`** (στο `{HERE / 'skill' / 'knowledge-base'}`).
> Το παρακάτω είναι απλώς τα στοιχεία ΑΥΤΗΣ της βάσης + γρήγορες εντολές.

## Στοιχεία βάσης
- **Αρχείο:** `{db}`
- **Χρήση (kind):** `{kind}`
- **Πεδία μεταδεδομένων (meta):** {fields}
- Πρόθεμα εντολών: `export PAPERSDB="{db}"`

## Γρήγορες εντολές
```bash
export PAPERSDB="{db}"
PY="{PY}"

"$PY" "{Q}" papers                 # έγγραφα + `meta_filled` (false = να ολοκληρωθεί)
"$PY" "{Q}" hybrid "<ερώτημα>"     # αναζήτηση (λέξεις+νόημα) — προτεινόμενο
"$PY" "{Q}" section <section_id>   # πλήρες κείμενο ενότητας
```

## Ολοκλήρωση (finish) — για κάθε έγγραφο με `meta_filled=false`
```bash
"$PY" "{A}" meta <paper_id> --json '{meta_example}'
```
Λεπτομέρειες & ροή σύνθεσης: skill **`knowledge-base`**.
"""


def generate(kind: str, db_path: str, topic: str | None, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "README.md"
    manifest.write_text(render(kind, db_path, topic), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Γεννήτρια manifest (README.md) για φάκελο-πακέτο βάσης.")
    p.add_argument("--kind", required=True, choices=list(prof_mod.PROFILES))
    p.add_argument("--db", required=True)
    p.add_argument("--topic", default=None)
    p.add_argument("--out", required=True, help="Φάκελος εξόδου (γράφει README.md)")
    args = p.parse_args(argv)
    path = generate(args.kind, args.db, args.topic, args.out)
    print(f"MANIFEST|{path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

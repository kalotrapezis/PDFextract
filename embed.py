#!/usr/bin/env python3
"""
Σημασιολογική ενσωμάτωση (embeddings) των ενοτήτων της βάσης με **BAAI/bge-m3**.

Κάθε ενότητα (`sections`) γίνεται ένα διάνυσμα 1024 διαστάσεων και αποθηκεύεται
ΜΕΣΑ στη βάση (πίνακας `vec_sections` του sqlite-vec). Έτσι ο agent βρίσκει
ενότητες «κατά νόημα», όχι μόνο με ακριβείς λέξεις (FTS5).

Σχεδιαστική αρχή: **idempotent**. Κρατάμε sha256 του κειμένου κάθε ενότητας στο
`section_embed_meta`· ξανατρέχοντας, ενσωματώνονται ΜΟΝΟ νέες/αλλαγμένες ενότητες.

Χρήση:
    python embed.py                 # ενσωμάτωσε ό,τι λείπει/άλλαξε
    python embed.py --reset         # ξανα-ενσωμάτωσε τα πάντα
    python embed.py --paper 3       # μόνο ένα paper
    python embed.py --status        # τι έχει ενσωματωθεί / τι εκκρεμεί

Η βάση επιλέγεται με env PAPERSDB (όπως όλα τα εργαλεία) → δουλεύει σε
οποιαδήποτε βάση του pipeline, όχι μόνο στο papers.db.
"""
from __future__ import annotations

import argparse
import hashlib
import sys

import db as dbmod

MODEL_NAME = "BAAI/bge-m3"
# Πόσο κείμενο ανά ενότητα στέλνουμε στο μοντέλο (χαρακτήρες). Το bge-m3 σηκώνει
# έως ~8192 tokens· κόβουμε για ταχύτητα/μνήμη σε CPU χωρίς ουσιαστική απώλεια.
MAX_CHARS = 6000

# Τύποι block που ΔΕΝ προσφέρουν νόημα για retrieval (εικόνες, page artifacts).
_SKIP_TYPES = {"Figure", "Picture", "PageHeader", "PageFooter"}

_model = None


def get_model():
    """Φόρτωσε (lazy) το SentenceTransformer. Πρώτη φορά: ~2.2GB download.

    ΣΗΜΑΝΤΙΚΟ: μετά το 1ο κατέβασμα φορτώνουμε **offline** (`local_files_only`).
    Αλλιώς το huggingface_hub προσπαθεί να επικυρώσει το cache μέσω δικτύου και
    μπορεί να **κρεμάσει** για λεπτά αν το δίκτυο είναι αργό/κλειστό."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        try:
            _model = SentenceTransformer(MODEL_NAME, local_files_only=True)
        except Exception:
            # Δεν είναι ακόμα στο cache → επίτρεψε online κατέβασμα (1η φορά).
            _model = SentenceTransformer(MODEL_NAME)
    return _model


def encode(texts: list[str]):
    """Κωδικοποίησε κείμενα σε L2-normalized διανύσματα (numpy float32)."""
    model = get_model()
    return model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=8,
        show_progress_bar=False,
        convert_to_numpy=True,
    )


def section_text(conn, section_id: int) -> str:
    """Συγκέντρωσε το κείμενο μιας ενότητας: τίτλος + κείμενο των blocks της,
    με σειρά ανάγνωσης. Παραλείπει εικόνες/artifacts."""
    s = conn.execute("SELECT title FROM sections WHERE id=?", (section_id,)).fetchone()
    parts: list[str] = []
    if s and s["title"]:
        parts.append(s["title"].strip())
    rows = conn.execute(
        "SELECT block_type, text FROM blocks WHERE section_id=? ORDER BY seq",
        (section_id,),
    ).fetchall()
    for r in rows:
        if r["block_type"] in _SKIP_TYPES:
            continue
        t = (r["text"] or "").strip()
        if t and t != (s["title"] if s else None):
            parts.append(t)
    return "\n".join(parts)[:MAX_CHARS]


def _hash(text: str) -> str:
    return hashlib.sha256((MODEL_NAME + "\x00" + text).encode("utf-8")).hexdigest()


def _serialize(vec):
    import sqlite_vec
    return sqlite_vec.serialize_float32(vec.tolist())


def pending(conn, paper_id=None, reset=False):
    """Ενότητες που χρειάζονται (ξανα)ενσωμάτωση: (section_id, text, hash)."""
    q = "SELECT id FROM sections"
    args: list = []
    if paper_id:
        q += " WHERE paper_id=?"
        args.append(paper_id)
    q += " ORDER BY paper_id, seq"
    out = []
    for row in conn.execute(q, args).fetchall():
        sid = row["id"]
        text = section_text(conn, sid)
        if not text.strip():
            continue
        h = _hash(text)
        if not reset:
            m = conn.execute(
                "SELECT content_hash FROM section_embed_meta WHERE section_id=?", (sid,)
            ).fetchone()
            if m and m["content_hash"] == h:
                continue  # αμετάβλητο → skip
        out.append((sid, text, h))
    return out


def embed_all(conn, paper_id=None, reset=False) -> int:
    if not dbmod.has_vec(conn):
        print("ERROR|το sqlite-vec δεν είναι φορτωμένο — δεν μπορώ να αποθηκεύσω διανύσματα")
        return 0

    todo = pending(conn, paper_id=paper_id, reset=reset)
    if not todo:
        print("OK|καμία ενότητα δεν χρειάζεται ενσωμάτωση (όλα ενημερωμένα)")
        return 0

    print(f"INFO|φόρτωση μοντέλου {MODEL_NAME} (1η φορά: ~2.2GB)…", flush=True)
    BATCH = 16
    done = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        vecs = encode([t for _, t, _ in chunk])
        for (sid, _text, h), vec in zip(chunk, vecs):
            conn.execute("DELETE FROM vec_sections WHERE section_id=?", (sid,))
            conn.execute(
                "INSERT INTO vec_sections(section_id, embedding) VALUES(?, ?)",
                (sid, _serialize(vec)),
            )
            conn.execute(
                "INSERT INTO section_embed_meta(section_id, model, dim, content_hash, updated_at)"
                " VALUES(?,?,?,?,datetime('now'))"
                " ON CONFLICT(section_id) DO UPDATE SET"
                " model=excluded.model, dim=excluded.dim,"
                " content_hash=excluded.content_hash, updated_at=excluded.updated_at",
                (sid, MODEL_NAME, dbmod.EMBED_DIM, h),
            )
            done += 1
        conn.commit()
        print(f"  …{done}/{len(todo)} ενότητες", flush=True)

    # Καθάρισε ορφανά διανύσματα (ενότητες που διαγράφηκαν).
    conn.execute(
        "DELETE FROM vec_sections WHERE section_id NOT IN (SELECT id FROM sections)"
    )
    conn.commit()
    print(f"DONE|ενσωματώθηκαν {done} ενότητες")
    return done


def status(conn):
    total = conn.execute("SELECT COUNT(*) c FROM sections").fetchone()["c"]
    if not dbmod.has_vec(conn):
        print(f"sqlite-vec: ΟΧΙ φορτωμένο | ενότητες: {total} | ενσωματωμένες: 0")
        return
    emb = conn.execute("SELECT COUNT(*) c FROM section_embed_meta").fetchone()["c"]
    vec = conn.execute("SELECT COUNT(*) c FROM vec_sections").fetchone()["c"]
    print(f"μοντέλο: {MODEL_NAME} | διάσταση: {dbmod.EMBED_DIM}")
    print(f"ενότητες: {total} | ενσωματωμένες: {emb} | διανύσματα: {vec} | εκκρεμούν: {total - emb}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Ενσωμάτωση ενοτήτων (BAAI/bge-m3) στη βάση.")
    p.add_argument("--reset", action="store_true", help="Ξανα-ενσωμάτωσε τα πάντα")
    p.add_argument("--paper", type=int, default=None, help="Μόνο αυτό το paper_id")
    p.add_argument("--status", action="store_true", help="Δείξε κατάσταση και έξοδος")
    args = p.parse_args(argv)

    conn = dbmod.connect()
    try:
        if args.status:
            status(conn)
            return 0
        embed_all(conn, paper_id=args.paper, reset=args.reset)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

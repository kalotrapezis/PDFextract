#!/usr/bin/env python3
"""
Ερωτήματα στη βάση paper — για χρήση από τον agent όταν χτίζει εργασία.

Παραδείγματα:
    python query.py papers                      # λίστα paper
    python query.py sections 3                   # ενότητες του paper 3 (+annotations)
    python query.py section 12                   # πλήρες κείμενο ενότητας 12
    python query.py search "stereotype threat"   # full-text αναζήτηση σε όλα τα paper
    python query.py todo                          # ενότητες χωρίς annotation
"""
from __future__ import annotations

import argparse
import json
import sys

import db as dbmod


def papers(conn):
    rows = conn.execute(
        "SELECT p.id,p.filename,p.title,p.n_pages,p.kind,p.meta,p.authors,p.year,p.doi,"
        " (SELECT COUNT(*) FROM sections s WHERE s.paper_id=p.id) sec,"
        " (SELECT COUNT(*) FROM annotations a JOIN sections s ON a.section_id=s.id"
        "   WHERE s.paper_id=p.id) ann"
        " FROM papers p ORDER BY p.id"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta_filled"] = bool(r["meta"])  # «ολοκληρωμένη» η βάση γι' αυτό το έγγραφο;
        out.append(d)
    return out


def bibliography(conn):
    """Βιβλιογραφία από τη βάση (APA) + σημαία αν λείπουν στοιχεία/DOI."""
    rows = conn.execute(
        "SELECT id,filename,title,authors,year,journal,doi,apa FROM papers ORDER BY authors,year"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["missing"] = [k for k in ("authors", "year", "doi", "apa") if not r[k]]
        out.append(d)
    return out


def sections(conn, paper_id):
    rows = conn.execute(
        "SELECT s.id,s.seq,s.title,s.page_start,s.page_end,"
        " a.who_speaks,a.whose_paper,a.importance,a.summary"
        " FROM sections s LEFT JOIN annotations a ON a.section_id=s.id"
        " WHERE s.paper_id=? ORDER BY s.seq", (paper_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def section(conn, section_id):
    s = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
    if not s:
        return None
    blocks = conn.execute(
        "SELECT page_no,block_type,text,html FROM blocks WHERE section_id=? ORDER BY seq",
        (section_id,)
    ).fetchall()
    ann = conn.execute("SELECT * FROM annotations WHERE section_id=?", (section_id,)).fetchone()
    return {"section": dict(s), "annotation": dict(ann) if ann else None,
            "blocks": [dict(b) for b in blocks]}


def search(conn, q, limit=20):
    rows = conn.execute(
        "SELECT b.id,b.paper_id,p.filename,b.page_no,b.section_id,"
        " snippet(blocks_fts,0,'[',']','…',12) AS snip"
        " FROM blocks_fts JOIN blocks b ON b.id=blocks_fts.rowid"
        " JOIN papers p ON p.id=b.paper_id"
        " WHERE blocks_fts MATCH ? ORDER BY rank LIMIT ?", (q, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def semantic(conn, q, limit=10):
    """Σημασιολογική αναζήτηση: βρες τις ενότητες πιο κοντά «κατά νόημα» στο q
    (vector KNN με cosine). Επιστρέφει ενότητες, όχι μεμονωμένα blocks."""
    if not dbmod.has_vec(conn):
        return {"error": "sqlite-vec δεν είναι φορτωμένο — τρέξε pip install sqlite-vec"}
    import embed as embmod
    import sqlite_vec
    qv = embmod.encode([q])[0]
    rows = conn.execute(
        "SELECT v.section_id, v.distance, s.paper_id, s.seq, s.title,"
        " s.page_start, s.page_end, p.filename"
        " FROM vec_sections v"
        " JOIN sections s ON s.id=v.section_id"
        " JOIN papers p ON p.id=s.paper_id"
        " WHERE v.embedding MATCH ? AND k=?"
        " ORDER BY v.distance",
        (sqlite_vec.serialize_float32(qv.tolist()), limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["score"] = round(1.0 - d.pop("distance"), 4)  # cosine sim ∈ [-1,1]
        out.append(d)
    return out


def hybrid(conn, q, limit=10, k_rrf=60):
    """Υβριδική αναζήτηση: συνδυάζει FTS5 (ακριβείς λέξεις) + σημασιολογική
    (νόημα) με Reciprocal Rank Fusion. Επιστρέφει ενότητες με score & πηγή."""
    # 1) Σημασιολογικό σκέλος (ανά ενότητα)
    sem = []
    if dbmod.has_vec(conn):
        import embed as embmod
        import sqlite_vec
        qv = embmod.encode([q])[0]
        sem = conn.execute(
            "SELECT section_id FROM vec_sections"
            " WHERE embedding MATCH ? AND k=? ORDER BY distance",
            (sqlite_vec.serialize_float32(qv.tolist()), limit * 4),
        ).fetchall()
    sem_rank = {r["section_id"]: i for i, r in enumerate(sem)}

    # 2) Λεξικό σκέλος (FTS5 σε blocks → καλύτερο rank ανά ενότητα)
    fts = conn.execute(
        "SELECT b.section_id FROM blocks_fts"
        " JOIN blocks b ON b.id=blocks_fts.rowid"
        " WHERE blocks_fts MATCH ? AND b.section_id IS NOT NULL"
        " ORDER BY rank LIMIT ?",
        (q, limit * 8),
    ).fetchall()
    fts_rank = {}
    for i, r in enumerate(fts):
        fts_rank.setdefault(r["section_id"], i)  # πρώτη (καλύτερη) εμφάνιση

    # 3) RRF fusion
    scores = {}
    for sid, rnk in sem_rank.items():
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k_rrf + rnk)
    for sid, rnk in fts_rank.items():
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k_rrf + rnk)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    out = []
    for sid, sc in ranked:
        s = conn.execute(
            "SELECT s.id,s.paper_id,s.seq,s.title,s.page_start,s.page_end,p.filename"
            " FROM sections s JOIN papers p ON p.id=s.paper_id WHERE s.id=?", (sid,)
        ).fetchone()
        if not s:
            continue
        d = dict(s)
        d["score"] = round(sc, 5)
        d["in_semantic"] = sid in sem_rank
        d["in_keyword"] = sid in fts_rank
        out.append(d)
    return out


def excerpts(conn, paper_id=None, unused=False):
    q = ("SELECT e.id,e.paper_id,p.filename,e.section_id,s.title section,"
         " e.page_no,e.cite_id,e.text,e.relevance,e.used,e.citation"
         " FROM excerpts e JOIN papers p ON p.id=e.paper_id"
         " LEFT JOIN sections s ON s.id=e.section_id WHERE 1=1")
    args = []
    if paper_id:
        q += " AND e.paper_id=?"; args.append(paper_id)
    if unused:
        q += " AND e.used=0"
    q += " ORDER BY e.paper_id, e.page_no, e.id"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def todo(conn):
    rows = conn.execute(
        "SELECT s.id,s.paper_id,p.filename,s.title,s.page_start,s.page_end"
        " FROM sections s JOIN papers p ON p.id=s.paper_id"
        " LEFT JOIN annotations a ON a.section_id=s.id"
        " WHERE a.id IS NULL ORDER BY s.paper_id,s.seq"
    ).fetchall()
    return [dict(r) for r in rows]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Ερωτήματα στη βάση paper.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("papers")
    sp = sub.add_parser("sections"); sp.add_argument("paper_id", type=int)
    sp = sub.add_parser("section"); sp.add_argument("section_id", type=int)
    sp = sub.add_parser("search"); sp.add_argument("q"); sp.add_argument("--limit", type=int, default=20)
    sp = sub.add_parser("semantic"); sp.add_argument("q"); sp.add_argument("--limit", type=int, default=10)
    sp = sub.add_parser("hybrid"); sp.add_argument("q"); sp.add_argument("--limit", type=int, default=10)
    sub.add_parser("todo")
    sub.add_parser("bibliography")
    sp = sub.add_parser("excerpts")
    sp.add_argument("--paper", type=int, default=None)
    sp.add_argument("--unused", action="store_true")
    p.add_argument("--json", action="store_true", help="Έξοδος σε JSON")
    args = p.parse_args(argv)

    conn = dbmod.connect()
    if args.cmd == "papers":
        res = papers(conn)
    elif args.cmd == "sections":
        res = sections(conn, args.paper_id)
    elif args.cmd == "section":
        res = section(conn, args.section_id)
    elif args.cmd == "search":
        res = search(conn, args.q, args.limit)
    elif args.cmd == "semantic":
        res = semantic(conn, args.q, args.limit)
    elif args.cmd == "hybrid":
        res = hybrid(conn, args.q, args.limit)
    elif args.cmd == "todo":
        res = todo(conn)
    elif args.cmd == "excerpts":
        res = excerpts(conn, args.paper, args.unused)
    elif args.cmd == "bibliography":
        res = bibliography(conn)
    conn.close()

    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

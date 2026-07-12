#!/usr/bin/env python3
"""
Εισαγωγή paper JSON (από convert.py --format json) στη SQLite βάση.

ΔΕΝ ΧΑΝΕΤΑΙ ΓΡΑΜΜΗ: κάθε block μπαίνει στον πίνακα `blocks`. Τα blocks
ομαδοποιούνται σε `sections` με βάση τα SectionHeader. Οι σελίδες είναι ήδη
απόλυτες (το JSON έχει πεδίο "page" ανά σελίδα).

Χρήση:
    python ingest.py PDFs/*/*.json
    python ingest.py PDFs/IGBO\\ (2015)/IGBO\\ (2015).json
    python ingest.py --reset PDFs/*/*.json     # σβήνει & ξαναχτίζει κάθε paper
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import db as dbmod
from convert import _walk_blocks, _inline, _SKIP


def _section_title(blk) -> str:
    return _inline(blk.get("html", "")).strip()


def ingest_json(conn, json_path: str, reset: bool = False) -> int:
    jp = Path(json_path).resolve()
    payload = json.loads(jp.read_text(encoding="utf-8"))
    title = payload.get("title")
    pages = payload.get("pages") or []
    filename = payload.get("source") or jp.stem

    cur = conn.cursor()
    existing = cur.execute("SELECT id FROM papers WHERE filename=?", (filename,)).fetchone()
    if existing:
        if not reset:
            print(f"SKIP|{filename}|υπάρχει ήδη (χρησιμοποίησε --reset)")
            return existing["id"]
        cur.execute("DELETE FROM papers WHERE id=?", (existing["id"],))

    n_pages = max((p.get("page") or 0) for p in pages) if pages else 0
    cur.execute(
        "INSERT INTO papers(filename,title,path,json_path,n_pages) VALUES(?,?,?,?,?)",
        (filename, title, str(jp.parent), str(jp), n_pages),
    )
    paper_id = cur.lastrowid

    seq = 0            # global σειρά block
    sec_seq = 0        # σειρά ενότητας
    cur_section_id = None
    cur_section_pages: list[int] = []

    def close_section():
        if cur_section_id is not None and cur_section_pages:
            cur.execute(
                "UPDATE sections SET page_start=?, page_end=? WHERE id=?",
                (min(cur_section_pages), max(cur_section_pages), cur_section_id),
            )

    for page in pages:
        page_no = page.get("page")
        for bt, blk in _walk_blocks(page):
            text = _inline(blk.get("html", ""))
            html = blk.get("html", "")
            block_id = blk.get("id")

            if bt == "SectionHeader" and text:
                close_section()
                sec_seq += 1
                cur.execute(
                    "INSERT INTO sections(paper_id,seq,title,page_start,page_end) VALUES(?,?,?,?,?)",
                    (paper_id, sec_seq, text, page_no, page_no),
                )
                cur_section_id = cur.lastrowid
                cur_section_pages = [page_no]
            else:
                if cur_section_id is None:  # blocks πριν τον 1ο header
                    sec_seq += 1
                    cur.execute(
                        "INSERT INTO sections(paper_id,seq,title,page_start,page_end) VALUES(?,?,?,?,?)",
                        (paper_id, sec_seq, "Front matter", page_no, page_no),
                    )
                    cur_section_id = cur.lastrowid
                    cur_section_pages = []
                if page_no is not None:
                    cur_section_pages.append(page_no)

            seq += 1
            cur.execute(
                "INSERT INTO blocks(paper_id,section_id,page_no,seq,block_type,block_id,text,html)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (paper_id, cur_section_id, page_no, seq, bt, block_id, text, html),
            )

    close_section()
    conn.commit()
    n_sec = cur.execute("SELECT COUNT(*) c FROM sections WHERE paper_id=?", (paper_id,)).fetchone()["c"]
    n_blk = cur.execute("SELECT COUNT(*) c FROM blocks WHERE paper_id=?", (paper_id,)).fetchone()["c"]
    print(f"OK|{filename}|paper_id={paper_id}|σελ={n_pages}|ενότητες={n_sec}|blocks={n_blk}")
    return paper_id


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Εισαγωγή paper JSON στη SQLite βάση.")
    p.add_argument("jsons", nargs="+", help="Ένα ή περισσότερα JSON (από convert.py)")
    p.add_argument("--reset", action="store_true", help="Ξαναχτίζει paper που υπάρχουν ήδη")
    args = p.parse_args(argv)

    conn = dbmod.connect()
    n = 0
    for jp in args.jsons:
        try:
            ingest_json(conn, jp, reset=args.reset)
            n += 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR|{jp}|{exc}")
    conn.close()
    print(f"DONE|{n} αρχεία")
    return 0


if __name__ == "__main__":
    sys.exit(main())

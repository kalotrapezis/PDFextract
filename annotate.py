#!/usr/bin/env python3
"""
Σχολιασμός (annotation) ενοτήτων ανά chunk/section.

Κάθε ενότητα παίρνει 4 πεδία:
  • who_speaks  — ποιος μιλά (ο/οι συγγραφείς; παραθέτει άλλον; ορισμός;)
  • whose_paper — σε ποιανού paper/έργο ανήκει η ιδέα
  • importance  — πού/γιατί μπορεί να φανεί χρήσιμο για τη θέση/εργασία
  • summary     — περίληψη 2 γραμμών

Δύο τρόποι:
  1) ΧΕΙΡΟΚΙΝΗΤΑ / από agent (default, χωρίς API key):
       python annotate.py set 12 --who "..." --whose "..." --importance "..." --summary "..."
       python annotate.py load annotations.json     # bulk: λίστα από {section_id,...}
  2) ΑΥΤΟΜΑΤΑ με Claude API (αν υπάρχει ANTHROPIC_API_KEY):
       python annotate.py api --paper 3             # σχολιάζει τις ενότητες του paper 3
       python annotate.py api                        # όλες τις ασχολίαστες ενότητες

Ροή για agent χωρίς API: `query.py todo` -> διάβασε `query.py section <id>` ->
γράψε annotations.json -> `annotate.py load annotations.json`.
"""
from __future__ import annotations

import argparse
import json
import sys

import db as dbmod

MODEL = "claude-opus-4-8"
FIELDS = ("who_speaks", "whose_paper", "importance", "summary", "tags")


def set_annotation(conn, section_id, **kw):
    cols = {k: kw.get(k) for k in FIELDS}
    conn.execute(
        "INSERT INTO annotations(section_id,who_speaks,whose_paper,importance,summary,tags)"
        " VALUES(:sid,:who_speaks,:whose_paper,:importance,:summary,:tags)"
        " ON CONFLICT(section_id) DO UPDATE SET"
        " who_speaks=excluded.who_speaks, whose_paper=excluded.whose_paper,"
        " importance=excluded.importance, summary=excluded.summary, tags=excluded.tags",
        {"sid": section_id, **cols},
    )
    conn.commit()


def add_excerpt(conn, section_id, text, relevance=None, cite_id=None, page=None):
    """Προσθέτει απόσπασμα (στη ΒΑΣΗ, όχι σε ΑΠΟΣΠΑΣΜΑΤΑ.md)."""
    sec = conn.execute("SELECT paper_id,page_start FROM sections WHERE id=?",
                        (section_id,)).fetchone()
    if not sec:
        print(f"ERROR|δεν υπάρχει section {section_id}")
        return
    page = page if page is not None else sec["page_start"]
    conn.execute(
        "INSERT INTO excerpts(paper_id,section_id,page_no,cite_id,text,relevance)"
        " VALUES(?,?,?,?,?,?)",
        (sec["paper_id"], section_id, page, cite_id, text, relevance))
    conn.commit()
    print(f"OK|excerpt στο section {section_id} (σελ {page})")


def set_bib(conn, paper_id, **kw):
    """Στοιχεία πηγής στη βάση (authors/year/journal/doi/apa)."""
    cols = {k: kw.get(k) for k in ("authors", "year", "journal", "doi", "apa")}
    sets = ", ".join(f"{k}=COALESCE(:{k}, {k})" for k in cols)
    conn.execute(f"UPDATE papers SET {sets} WHERE id=:pid", {"pid": paper_id, **cols})
    conn.commit()
    print(f"OK|bib paper {paper_id}")


def set_meta(conn, paper_id, updates: dict, kind=None):
    """Γράφει/συγχωνεύει τα descriptive μεταδεδομένα του profile στη στήλη
    `papers.meta` (JSON). Αυτό «ολοκληρώνει» τη βάση: tags/keywords/subject/grade/…
    Δεν χάνει υπάρχοντα κλειδιά — κάνει merge."""
    row = conn.execute("SELECT meta FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not row:
        print(f"ERROR|δεν υπάρχει paper {paper_id}")
        return
    cur = {}
    if row["meta"]:
        try:
            cur = json.loads(row["meta"])
        except Exception:  # noqa: BLE001
            cur = {}
    cur.update({k: v for k, v in updates.items() if v not in (None, "")})
    sets = "meta=:meta"
    params = {"pid": paper_id, "meta": json.dumps(cur, ensure_ascii=False)}
    if kind:
        sets += ", kind=:kind"
        params["kind"] = kind
    conn.execute(f"UPDATE papers SET {sets} WHERE id=:pid", params)
    conn.commit()
    print(f"OK|meta paper {paper_id}: {list(cur)}")


def use_excerpt(conn, excerpt_id, citation):
    conn.execute("UPDATE excerpts SET used=1, citation=? WHERE id=?",
                 (citation, excerpt_id))
    conn.commit()
    print(f"OK|excerpt {excerpt_id} -> used ({citation})")


def load_file(conn, path):
    data = json.loads(open(path, encoding="utf-8").read())
    n = 0
    for item in data:
        sid = item.get("section_id")
        if sid is None:
            continue
        set_annotation(conn, sid, **{k: item.get(k) for k in FIELDS})
        n += 1
    print(f"OK|φορτώθηκαν {n} annotations")


# ---------- Προαιρετικό αυτόματο annotation με Claude API ----------
SYS = (
    "Είσαι βοηθός ακαδημαϊκής ανάλυσης. Σου δίνω ΜΙΑ ενότητα ενός επιστημονικού "
    "paper (τίτλος + κείμενο). Επίστρεψε σύντομα, στα Ελληνικά:\n"
    "- who_speaks: ποιος μιλά εδώ (οι ίδιοι οι συγγραφείς; παραθέτουν/συνοψίζουν "
    "άλλον ερευνητή; ορισμός/θεωρία;)\n"
    "- whose_paper: σε ποιανού έργο/paper ανήκει κυρίως η ιδέα (οι συγγραφείς του "
    "paper ή άλλος που αναφέρεται)\n"
    "- importance: πού/γιατί θα μπορούσε να φανεί χρήσιμο σε μια ακαδημαϊκή θέση\n"
    "- summary: περίληψη το πολύ 2 γραμμές\n"
    "Μην επινοείς· βασίσου ΜΟΝΟ στο κείμενο."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "who_speaks": {"type": "string"},
        "whose_paper": {"type": "string"},
        "importance": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["who_speaks", "whose_paper", "importance", "summary"],
    "additionalProperties": False,
}


def annotate_api(conn, paper_id=None, model=MODEL, limit=None):
    try:
        import anthropic  # lazy
    except ImportError:
        print("ERROR|Λείπει το anthropic SDK. Τρέξε: .venv/bin/pip install anthropic")
        return
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR|Δεν υπάρχει ANTHROPIC_API_KEY στο περιβάλλον.")
        return
    client = anthropic.Anthropic()

    q = ("SELECT s.id,s.title,p.title ptitle,p.filename FROM sections s"
         " JOIN papers p ON p.id=s.paper_id"
         " LEFT JOIN annotations a ON a.section_id=s.id WHERE a.id IS NULL")
    args = []
    if paper_id:
        q += " AND s.paper_id=?"
        args.append(paper_id)
    q += " ORDER BY s.paper_id,s.seq"
    rows = conn.execute(q, args).fetchall()
    if limit:
        rows = rows[:limit]

    for r in rows:
        text = "\n".join(
            b["text"] for b in conn.execute(
                "SELECT text FROM blocks WHERE section_id=? ORDER BY seq", (r["id"],))
            if b["text"]
        )[:8000]
        if not text.strip():
            continue
        user = (f"Paper: {r['ptitle'] or r['filename']}\nΕνότητα: {r['title']}\n\n"
                f"Κείμενο:\n{text}")
        msg = client.messages.create(
            model=model, max_tokens=1024,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYS,
            messages=[{"role": "user", "content": user}],
        )
        out = next((b.text for b in msg.content if b.type == "text"), "{}")
        data = json.loads(out)
        set_annotation(conn, r["id"], **data)
        print(f"OK|section {r['id']} ({r['title'][:40]})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Σχολιασμός ενοτήτων.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set")
    s.add_argument("section_id", type=int)
    for f in ("who", "whose", "importance", "summary", "tags"):
        s.add_argument(f"--{f}", default=None)

    s = sub.add_parser("load"); s.add_argument("path")

    s = sub.add_parser("api")
    s.add_argument("--paper", type=int, default=None)
    s.add_argument("--model", default=MODEL)
    s.add_argument("--limit", type=int, default=None)

    s = sub.add_parser("excerpt", help="Προσθήκη αποσπάσματος στη βάση")
    s.add_argument("section_id", type=int)
    s.add_argument("--text", required=True)
    s.add_argument("--relevance", default=None)
    s.add_argument("--cite-id", default=None)
    s.add_argument("--page", type=int, default=None)

    s = sub.add_parser("use", help="Μαρκάρει απόσπασμα ως χρησιμοποιημένο")
    s.add_argument("excerpt_id", type=int)
    s.add_argument("--citation", required=True)

    s = sub.add_parser("bib", help="Στοιχεία πηγής (συγγραφείς/έτος/DOI/APA)")
    s.add_argument("paper_id", type=int)
    for f in ("authors", "year", "journal", "doi", "apa"):
        s.add_argument(f"--{f}", default=None)

    s = sub.add_parser("meta", help="Descriptive μεταδεδομένα profile (JSON) ενός εγγράφου")
    s.add_argument("paper_id", type=int)
    s.add_argument("--set", action="append", default=[], metavar="key=value",
                   help="Ένα πεδίο (επαναλαμβανόμενο), π.χ. --set subject=ΤΠΕ --set grade=Ε΄")
    s.add_argument("--json", default=None, help="Ολόκληρο JSON αντικείμενο με τα πεδία")
    s.add_argument("--kind", default=None, help="Όρισε/διόρθωσε και το kind")

    args = p.parse_args(argv)
    conn = dbmod.connect()
    if args.cmd == "bib":
        set_bib(conn, args.paper_id, authors=args.authors, year=args.year,
                journal=args.journal, doi=args.doi, apa=args.apa)
    elif args.cmd == "excerpt":
        add_excerpt(conn, args.section_id, args.text, args.relevance,
                    args.cite_id, args.page)
    elif args.cmd == "use":
        use_excerpt(conn, args.excerpt_id, args.citation)
    elif args.cmd == "set":
        set_annotation(conn, args.section_id, who_speaks=args.who,
                       whose_paper=args.whose, importance=args.importance,
                       summary=args.summary, tags=args.tags)
        print(f"OK|annotation section {args.section_id}")
    elif args.cmd == "load":
        load_file(conn, args.path)
    elif args.cmd == "api":
        annotate_api(conn, paper_id=args.paper, model=args.model, limit=args.limit)
    elif args.cmd == "meta":
        updates = {}
        if args.json:
            updates.update(json.loads(args.json))
        for kv in args.set:
            k, _, v = kv.partition("=")
            updates[k.strip()] = v
        set_meta(conn, args.paper_id, updates, kind=args.kind)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

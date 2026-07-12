#!/usr/bin/env python3
"""
SQLite «βάση» επιστημονικών paper. Πραγματική, queryable βάση — ΟΧΙ για προβολή,
αλλά για να τη χρησιμοποιεί ο agent (Claude) όταν χτίζει εργασίες ενότητα-ενότητα.

Σχεδιαστική αρχή: **δεν χάνεται γραμμή**. Κάθε block του PDF αποθηκεύεται αυτούσιο
στον πίνακα `blocks`. Οι `sections` ομαδοποιούν blocks (ανά SectionHeader) και τα
`annotations` κρατούν τα σχόλια των agents ανά ενότητα.

Default τοποθεσία βάσης: <repo>/papers.db (άλλαξέ την με env PAPERSDB).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("PAPERSDB", Path(__file__).resolve().parent / "papers.db"))

# Διάσταση διανύσματος του μοντέλου ενσωμάτωσης (BAAI/bge-m3 → 1024).
EMBED_DIM = 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id        INTEGER PRIMARY KEY,
    filename  TEXT UNIQUE NOT NULL,
    title     TEXT,
    path      TEXT,
    json_path TEXT,
    n_pages   INTEGER,
    -- Γενικό knowledge base: ποια χρήση (research/thesis/lesson/personal/…)
    -- και προσαρμοσμένα μεταδεδομένα profile ως JSON (class, subject, tools…).
    kind      TEXT,
    meta      TEXT,   -- JSON: τα πεδία του profile (βλ. profiles.py)
    -- Βιβλιογραφικά στοιχεία (αντικαθιστούν το Bibliography.md) — για τον τελικό
    -- έλεγχο: ορφανές πηγές, διασταύρωση συγγραφέα, επαλήθευση DOI.
    authors   TEXT,   -- π.χ. "Bègue, L., Sarda, E., & Gentile, D. A."
    year      TEXT,
    journal   TEXT,
    doi       TEXT,
    apa       TEXT,   -- πλήρης αναφορά APA 7 (για το κεφάλαιο Βιβλιογραφία)
    added_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sections (
    id         INTEGER PRIMARY KEY,
    paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,          -- σειρά ενότητας στο paper
    title      TEXT,                      -- κείμενο του SectionHeader (ή 'Front matter')
    page_start INTEGER,
    page_end   INTEGER
);

CREATE TABLE IF NOT EXISTS blocks (
    id         INTEGER PRIMARY KEY,
    paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    page_no    INTEGER,                   -- απόλυτη σελίδα (1-based)
    seq        INTEGER,                   -- σειρά ανάγνωσης στο paper
    block_type TEXT,                      -- Text, SectionHeader, Table, Figure...
    block_id   TEXT,                      -- το αρχικό id του Marker
    text       TEXT,                      -- ΑΚΡΙΒΕΣ κείμενο (exact copy)
    html       TEXT                       -- raw html του block (π.χ. πίνακες)
);

CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY,
    section_id  INTEGER UNIQUE NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    who_speaks  TEXT,   -- ποιος μιλά (συγγραφέας/citing other/quote κ.λπ.)
    whose_paper TEXT,   -- σε ποιανού paper ανήκει αυτό
    importance  TEXT,   -- πού/γιατί μπορεί να είναι σημαντικό για τη θέση
    summary     TEXT,   -- περίληψη 2 γραμμών
    tags        TEXT,   -- προαιρετικά keywords (comma-separated)
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Αποσπάσματα: ΑΝΤΙΚΑΘΙΣΤΟΥΝ το ΑΠΟΣΠΑΣΜΑΤΑ.md — ζουν μέσα στη βάση.
CREATE TABLE IF NOT EXISTS excerpts (
    id          INTEGER PRIMARY KEY,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_id  INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    page_no     INTEGER,           -- σελίδα στο πρωτότυπο PDF
    cite_id     TEXT,              -- ανθρώπινο ID τύπου A001 (προαιρετικό)
    text        TEXT NOT NULL,     -- ΑΚΡΙΒΕΣ απόσπασμα (καμία παράφραση)
    relevance   TEXT,              -- γιατί χρησιμεύει στη θέση
    used        INTEGER DEFAULT 0, -- 0=αχρησιμοποίητο, 1=μπήκε στην εργασία
    citation    TEXT,              -- η APA αναφορά που χρησιμοποιήθηκε π.χ. (Smith,2019,σ.5)
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_excerpts_paper ON excerpts(paper_id);

CREATE INDEX IF NOT EXISTS idx_blocks_paper   ON blocks(paper_id);
CREATE INDEX IF NOT EXISTS idx_blocks_section ON blocks(section_id);
CREATE INDEX IF NOT EXISTS idx_sections_paper ON sections(paper_id);

-- Full-text αναζήτηση στο κείμενο των blocks
CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
    text, content='blocks', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
    INSERT INTO blocks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
"""

# Σχήμα διανυσμάτων — δημιουργείται ΜΟΝΟ αν φορτωθεί το sqlite-vec extension.
# `vec_sections`: ένα διάνυσμα νοήματος ανά ενότητα (cosine). `section_embed_meta`:
# ποιο μοντέλο/hash έχει ενσωματωθεί, ώστε το embed.py να ξανατρέχει μόνο ό,τι άλλαξε.
VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_sections USING vec0(
    section_id INTEGER PRIMARY KEY,
    embedding  FLOAT[{EMBED_DIM}] distance_metric=cosine
);

CREATE TABLE IF NOT EXISTS section_embed_meta (
    section_id   INTEGER PRIMARY KEY REFERENCES sections(id) ON DELETE CASCADE,
    model        TEXT,
    dim          INTEGER,
    content_hash TEXT,           -- sha256 του κειμένου που ενσωματώθηκε
    updated_at   TEXT DEFAULT (datetime('now'))
);
"""


def load_vec(conn: sqlite3.Connection) -> bool:
    """Φόρτωσε το sqlite-vec extension. Επιστρέφει True αν πέτυχε (best-effort:
    αν λείπει το πακέτο ή το build του sqlite δεν επιτρέπει extensions, η βάση
    δουλεύει κανονικά μόνο με FTS5)."""
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


def has_vec(conn: sqlite3.Connection) -> bool:
    """True αν το vec extension είναι φορτωμένο σε αυτή τη σύνδεση."""
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        return False


def connect(path: Path | str | None = None, vec: bool = True) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    vec_ok = load_vec(conn) if vec else False
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    if vec_ok:
        conn.executescript(VEC_SCHEMA)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Ήπιες προσθήκες στηλών για υπάρχουσες βάσεις (SQLite δεν έχει ADD COLUMN IF
    NOT EXISTS). Μη καταστροφικό: προσθέτει μόνο ό,τι λείπει."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
    # Όλες οι στήλες που περιμένει το SCHEMA — προσθέτει ό,τι λείπει σε παλιές βάσεις
    # (π.χ. βάση φτιαγμένη πριν μπουν τα βιβλιογραφικά πεδία → το academic-db θα έσπαγε).
    wanted = (("authors", "TEXT"), ("year", "TEXT"), ("journal", "TEXT"),
              ("doi", "TEXT"), ("apa", "TEXT"), ("kind", "TEXT"), ("meta", "TEXT"))
    for name, decl in wanted:
        if name not in cols:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {decl}")
    conn.commit()


if __name__ == "__main__":
    c = connect()
    print(f"DB έτοιμη: {DB_PATH}")
    for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(" •", row["name"])
    c.close()

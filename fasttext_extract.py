#!/usr/bin/env python3
"""Γρήγορη εξαγωγή για born-digital PDF — ΧΩΡΙΣ OCR, ΧΩΡΙΣ μοντέλα.

Τα περισσότερα ερευνητικά PDF (journal/arXiv) έχουν ήδη το κείμενο μέσα τους ως
κείμενο. Το Marker τα περνάει ούτως ή άλλως από layout+OCR μοντέλα στη CPU: ~3 ώρες
το paper. Εδώ απλώς ΔΙΑΒΑΖΟΥΜΕ το text layer με το pdftext (ήδη εξάρτηση του Marker):
~1 δευτερόλεπτο το paper.

Παράγει ΤΟ ΙΔΙΟ σχήμα με το JSON του Marker — pages[].children[] με
`block_type`/`html`/`id` — ώστε ingest.py, renderers και βάση να μείνουν ως έχουν.

Οι επικεφαλίδες ενοτήτων εντοπίζονται ΗΕΥΡΙΣΤΙΚΑ (μέγεθος/βάρος γραμματοσειράς),
όχι με μοντέλο layout: λίγο πιο χοντροκομμένο από το Marker, αλλά 100× γρηγορότερο
και αρκετό για «ενότητα-ενότητα» άντληση κειμένου.

Για σκαναρισμένα PDF (χωρίς text layer) ΔΕΝ κάνει τίποτα — γυρνάει None και ο
caller πέφτει πίσω στο Marker (δες convert.py).
"""
from __future__ import annotations

import html as _html
import re
import unicodedata
from collections import Counter

MIN_CHARS_PER_PAGE = 200   # κάτω από αυτό: σκαναρισμένο -> θέλει OCR
HEADING_MAX_CHARS = 140    # μια επικεφαλίδα δεν είναι παράγραφος
SIZE_TOLERANCE = 1.06      # >6% από το σώμα κειμένου = μεγαλύτερη γραμματοσειρά
MIN_COVERAGE = 0.98        # <98% του text layer -> κάτι χάθηκε, ανάκτησέ το

# «1. Εισαγωγή», «2.3 Μέθοδος», «IV. Results», «Methods» κ.λπ.
_NUMBERED = re.compile(r"^\s*(\d+(\.\d+)*\.?|[IVXLC]+\.)\s+\S")

# Θόρυβος από cover sheets αποθετηρίων (King's Research Portal, NIHMS, tandfonline…):
# DOI/URL/ISSN/«Download date» κ.λπ. Δεν είναι επικεφαλίδες ενοτήτων ούτε τίτλοι.
_NOISE = re.compile(
    r"(https?://|www\.|doi:|doi\.org|issn|isbn|©|copyright|download date|"
    r"link to publication|research portal|terms & conditions|full terms|"
    r"citation for published|submit your article|view related|crossmark|"
    r"@[\w.]+\.\w+)",
    re.IGNORECASE,
)

# Τίτλοι-σκουπίδια στα PDF metadata
_JUNK_TITLES = {"untitled", "", "hrev_master", "microsoft word", "document",
                "pdf", "article", "untitled document"}
_COMMON_HEADS = {
    "abstract", "introduction", "background", "methods", "method", "materials and methods",
    "results", "discussion", "conclusion", "conclusions", "references", "acknowledgements",
    "acknowledgments", "limitations", "funding", "keywords",
    "περίληψη", "εισαγωγή", "μέθοδος", "μέθοδοι", "αποτελέσματα", "συζήτηση",
    "συμπεράσματα", "βιβλιογραφία", "ευχαριστίες",
}


def has_text_layer(pdf_path: str, sample_pages: int = 5) -> bool:
    """born-digital (True) ή σκαναρισμένο (False);"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        n = len(doc)
        chars = read = 0
        for i in range(min(n, sample_pages)):
            try:
                tp = doc[i].get_textpage()
                chars += len(tp.get_text_bounded())
                tp.close()
                read += 1
            except Exception:  # noqa: BLE001 — χαλασμένη σελίδα: αγνόησέ την
                continue
        return read > 0 and (chars / read) >= MIN_CHARS_PER_PAGE
    finally:
        doc.close()


def loadable_pages(pdf_path: str) -> tuple[list[int], list[int]]:
    """(σελίδες που ανοίγουν, σελίδες που ΔΕΝ ανοίγουν) — 0-based.

    Μερικά PDF από αποθετήρια έχουν μία χαλασμένη σελίδα που ρίχνει το pdfium
    («Failed to load page»). Αντί να χαθεί ΟΛΟ το paper, την προσπερνάμε.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    good, bad = [], []
    try:
        for i in range(len(doc)):
            try:
                page = doc[i]
                page.close()
                good.append(i)
            except Exception:  # noqa: BLE001
                bad.append(i)
    finally:
        doc.close()
    return good, bad


def _span_text(span) -> str:
    return span.get("text") or ""


def _line_text(line) -> str:
    return "".join(_span_text(s) for s in line.get("spans") or [])


def _body_size(pages) -> float:
    """Το μέγεθος γραμματοσειράς του ΣΩΜΑΤΟΣ = το πιο «πολυχαρακτηρο», όχι το πιο συχνό."""
    weight: Counter = Counter()
    for page in pages:
        for block in page.get("blocks") or []:
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    size = round(float((span.get("font") or {}).get("size") or 0), 1)
                    if size:
                        weight[size] += len(_span_text(span))
    return weight.most_common(1)[0][0] if weight else 10.0


def _is_bold(line) -> bool:
    spans = line.get("spans") or []
    if not spans:
        return False
    bold = 0
    for span in spans:
        font = span.get("font") or {}
        name = str(font.get("name") or "").lower()
        try:
            heavy = float(font.get("weight") or 0) >= 600
        except (TypeError, ValueError):
            heavy = False
        if heavy or "bold" in name or "black" in name or "heavy" in name:
            bold += len(_span_text(span))
    return bold >= sum(len(_span_text(s)) for s in spans) / 2


def _max_size(line) -> float:
    sizes = [float((s.get("font") or {}).get("size") or 0) for s in line.get("spans") or []]
    return max(sizes) if sizes else 0.0


def _looks_like_heading(text: str, line, body: float, single_line: bool = True) -> bool:
    """Επικεφαλίδα ενότητας;

    ΠΡΟΣΟΧΗ: σε πολλά PDF εκδοτών (π.χ. Elsevier) ΟΛΑ τα spans αναφέρουν
    size=1.0 και κανένα bold — η γραμματοσειρά δεν λέει τίποτα. Εκεί σώζει το
    σχήμα «1. Introduction» / «3.2.1. Types of participants»: μια μικρή, μόνη της
    γραμμή με αρίθμηση είναι επικεφαλίδα ακόμα κι αν δεν ξεχωρίζει οπτικά.
    """
    t = text.strip()
    if not t or len(t) > HEADING_MAX_CHARS:
        return False
    if _NOISE.search(t):
        return False  # cover sheet αποθετηρίου, όχι ενότητα

    numbered = bool(_NUMBERED.match(t))
    if t.rstrip().endswith((".", ";", ",")) and not numbered:
        return False  # μια πρόταση, όχι τίτλος

    # «3.1. Protocol and registration» -> «protocol and registration»
    bare = _NUMBERED.sub("", t).lower().strip(" :.")
    known = bare[:40] in _COMMON_HEADS or t.lower().strip(" :.")[:40] in _COMMON_HEADS

    bigger = _max_size(line) >= body * SIZE_TOLERANCE
    bold = _is_bold(line)

    if known:
        return True
    # Αριθμημένη, κοντή, μόνη της γραμμή: αρκεί από μόνη της (χωρίς σήμα γραμματοσειράς).
    # Οι καταχωρήσεις βιβλιογραφίας («1. Smith, J. (2010). …») κόβονται από το
    # όριο μήκους/λέξεων και το τελικό «.».
    if numbered and single_line and len(t) <= 80 and len(t.split()) <= 10 \
            and not t.rstrip().endswith("."):
        return True
    return bigger or (bold and len(t) < 80)


def _norm(s: str) -> str:
    """Μόνο οι χαρακτήρες — αγνόησε κενά/σειρά. Μας νοιάζει τι ΧΑΘΗΚΕ, όχι η διάταξη."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _raw_page_text(doc, i: int) -> str:
    """Το ακατέργαστο text layer της σελίδας — η ΠΗΓΗ ΤΗΣ ΑΛΗΘΕΙΑΣ."""
    try:
        tp = doc[i].get_textpage()
        try:
            return tp.get_text_bounded()
        finally:
            tp.close()
    except Exception:  # noqa: BLE001
        return ""


def _raw_blocks(raw: str, page_no: int) -> list[dict]:
    """Ανάκτηση από το ακατέργαστο κείμενο: παράγραφοι από κενές γραμμές.

    Χάνουμε τη δομή (επικεφαλίδες), ΟΧΙ το κείμενο — οι χαρακτήρες είναι ακριβώς
    αυτοί που έβαλε ο εκδότης, όχι εικασία μοντέλου.
    """
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if c.strip()]
    out = []
    for j, chunk in enumerate(chunks):
        text = re.sub(r"[ \t]+", " ", chunk)
        out.append({
            "id": f"/page/{page_no}/Text/r{j}",
            "block_type": "Text",
            "html": f"<p>{_html.escape(text)}</p>",
            "recovered": True,   # σημαδεμένο: ήρθε από ανάκτηση, δομή αναξιόπιστη
        })
    return out


def _meta_title(pdf_path: str) -> str | None:
    """Ο τίτλος από τα metadata του PDF — συχνά σωστός, συχνά σκουπίδι."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            title = (doc.get_metadata_dict().get("Title") or "").strip()
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return None
    if len(title) < 15 or title.lower() in _JUNK_TITLES or _NOISE.search(title):
        return None
    return title


def _biggest_text_title(pages) -> str | None:
    """Εφεδρικό: το μεγαλύτερο σε γραμματοσειρά «καθαρό» κείμενο των πρώτων σελίδων.

    Ο τίτλος ενός paper τυπώνεται σχεδόν πάντα στη μεγαλύτερη γραμματοσειρά της
    πρώτης σελίδας του άρθρου.
    """
    best = None
    best_size = 0.0
    for page in pages[:3]:
        for block in page.get("blocks") or []:
            for line in block.get("lines") or []:
                t = _line_text(line).strip()
                if not (20 <= len(t) <= 300) or _NOISE.search(t):
                    continue
                size = _max_size(line)
                if size > best_size:
                    best, best_size = t, size
    return best


def title_for(pdf_path: str, pages) -> str | None:
    return _meta_title(pdf_path) or _biggest_text_title(pages)


def extract(pdf_path: str, page_range=None, on_warn=None):
    """-> (σελίδες σε σχήμα Marker, τίτλος, report) ή None αν λείπει το text layer.

    ΚΑΘΕ σελίδα επαληθεύεται: συγκρίνουμε τους χαρακτήρες που βγάλαμε με τους
    χαρακτήρες του ακατέργαστου text layer. Αν λείπει κείμενο (το pdftext ΟΝΤΩΣ
    χάνει κείμενο σε κάποια PDF), η σελίδα ανακτάται από το raw text — ίδιοι
    ακριβώς χαρακτήρες, φτωχότερη δομή — και σημειώνεται στο report.

    Το report είναι ο λόγος που ΔΕΝ είναι μαντεψιά: λέει, ανά σελίδα, τι ποσοστό
    του κειμένου κρατήθηκε και ποιες σελίδες χρειάζονται μάτια.
    """
    import pypdfium2 as pdfium
    from pdftext.extraction import dictionary_output

    if not has_text_layer(pdf_path):
        return None

    if page_range is None:
        page_range, broken = loadable_pages(pdf_path)
        if broken and on_warn:
            nums = ", ".join(str(b + 1) for b in broken[:10])
            on_warn(f"{len(broken)} σελίδα(ες) δεν ανοίγουν και παραλείπονται: {nums}")
        if not page_range:
            return None  # καμία σελίδα δεν διαβάζεται -> ας δοκιμάσει το Marker
    else:
        broken = []

    pages = dictionary_output(str(pdf_path), sort=True, page_range=page_range,
                              keep_chars=False, disable_links=True, workers=1)
    body = _body_size(pages)
    title = title_for(pdf_path, pages)

    doc = pdfium.PdfDocument(str(pdf_path))
    report = {
        "pages": [],                       # ανά σελίδα: coverage/recovered/needs_ocr
        "broken_pages": [b + 1 for b in broken],
        "recovered_pages": [],
        "needs_ocr_pages": [],
    }

    out: list[dict] = []
    for pidx, page in enumerate(pages):
        page_no = int(page.get("page", pidx)) + 1
        children = []
        for bidx, block in enumerate(page.get("blocks") or []):
            lines = block.get("lines") or []
            if not lines:
                continue

            # Επικεφαλίδα = ΟΛΟ το block είναι μία-δυο «τιτλοειδείς» γραμμές.
            texts = [_line_text(ln).strip() for ln in lines]
            joined = " ".join(t for t in texts if t).strip()
            if not joined:
                continue

            heading = (
                len(lines) <= 2
                and all(_looks_like_heading(t, ln, body, single_line=len(lines) == 1)
                        for t, ln in zip(texts, lines) if t)
            )
            block_type = "SectionHeader" if heading else "Text"

            esc = _html.escape(joined)
            html = f"<h2>{esc}</h2>" if heading else f"<p>{esc}</p>"
            children.append({
                "id": f"/page/{page_no}/{block_type}/{bidx}",
                "block_type": block_type,
                "html": html,
                "bbox": block.get("bbox"),
            })

        # ---- ΕΠΑΛΗΘΕΥΣΗ: συγκρίνουμε με το ακατέργαστο text layer ----
        raw = _raw_page_text(doc, int(page.get("page", pidx)))
        raw_n = len(_norm(raw))
        ours_n = len(_norm(" ".join(
            re.sub(r"<[^>]+>", " ", c["html"]) for c in children)))
        # Μπορεί να βγει >1 (το pdftext ενώνει συλλαβισμούς κ.λπ.) — μας νοιάζει το έλλειμμα.
        coverage = min(1.0, ours_n / raw_n) if raw_n else 1.0

        status = "ok"
        if raw_n == 0:
            # Καθόλου κείμενο στη σελίδα: εικόνα/σκαναρισμένη -> θέλει μάτια ή OCR
            status = "needs_ocr"
            report["needs_ocr_pages"].append(page_no)
        elif coverage < MIN_COVERAGE:
            # Το pdftext έχασε κείμενο. Ανάκτησέ το από το raw — ΑΚΡΙΒΕΙΣ χαρακτήρες.
            children = _raw_blocks(raw, page_no)
            status = "recovered"
            report["recovered_pages"].append(page_no)
            coverage = 1.0
            if on_warn:
                on_warn(f"σελίδα {page_no}: το pdftext έχασε κείμενο — ανακτήθηκε "
                        f"από το text layer (η δομή της σελίδας είναι φτωχότερη)")

        report["pages"].append({
            "page": page_no,
            "coverage": round(coverage, 4),
            "status": status,
            "chars": ours_n,
        })

        out.append({
            "id": f"/page/{page_no}",
            "block_type": "Page",
            "page": page_no,
            "children": children,
            "images": {},
        })

    doc.close()
    return out, title, report

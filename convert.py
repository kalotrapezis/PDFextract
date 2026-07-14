#!/usr/bin/env python3
"""
PDF -> δομημένη έξοδο (Marker backend), βελτιστοποιημένο για ΧΑΜΗΛΗ μνήμη.

Παράγει citation-ready αρχείο με:
  • Τίτλο εγγράφου
  • ΑΠΟΛΥΤΟΥΣ αριθμούς σελίδας (διορθωμένους ακόμα κι όταν δουλεύουμε σε chunks)
  • Ενότητες (sections) ως headings
  • Ακριβές κείμενο (exact copy) + πίνακες

Το PDF επεξεργάζεται σε ΚΟΜΜΑΤΙΑ ΣΕΛΙΔΩΝ (chunks) ώστε η μνήμη να μένει σταθερά
χαμηλή. Εσωτερικά ζητάμε από το Marker JSON (περιέχει block_type, σελίδες,
section_hierarchy) και το ξαναδίνουμε στη μορφή που ζητάς.

Διόρθωση σελίδων: για κάθε chunk που ξεκινά στην απόλυτη σελίδα S, η k-οστή σελίδα
του chunk παίρνει απόλυτο αριθμό S+k (1-based στην έξοδο). Έτσι οι αριθμοί είναι
σωστοί ανεξάρτητα από το πώς αριθμεί εσωτερικά το Marker.

Γραμμές προόδου (τις διαβάζει το GUI):
    PROGRESS|<i>|<n>|<onoma>
    CHUNK|<apo>|<ews>|<synolo>
    DONE|<output_path>
    ERROR|<arxeio>|<minima>
    ALLDONE|<ok>|<fail>

CLI:
    python convert.py paper.pdf --out ./output [--format md] [--chunk 4]
    formats: md (default, citation-ready) | html | json
"""
from __future__ import annotations

import argparse
import html as _html
import json as _json
import os
import re
import sys
import time
import traceback
from pathlib import Path

# ---- Ρυθμίσεις: ΠΡΕΠΕΙ να μπουν ΠΡΙΝ φορτωθεί torch/marker ----
# Auto-tune του batch size ανάλογα με τη διαθέσιμη RAM. Τα μοντέλα Surya θέλουν
# ~8 GB· ό,τι περισσεύει το ρίχνουμε σε μεγαλύτερο batch (πολύ πιο γρήγορο στη CPU).
# Σταθερό batch=1 = ~10× πιο αργό. Override με env BATCH=ν.
def _auto_batch() -> int:
    if os.environ.get("BATCH"):
        return max(1, int(os.environ["BATCH"]))
    from runtime import available_gb
    headroom = available_gb() - 8.5      # χώρος πέρα από τα μοντέλα
    if headroom >= 4:
        return 12
    if headroom >= 2:
        return 8
    if headroom >= 1:
        return 4
    if headroom >= 0.3:
        return 2
    return 1

_BATCH = str(_auto_batch())
os.environ.setdefault("TORCH_DEVICE", "cpu")
for _v in ("RECOGNITION", "DETECTOR", "LAYOUT", "TABLE_REC", "OCR_ERROR"):
    os.environ.setdefault(f"{_v}_BATCH_SIZE", _BATCH)
os.environ.setdefault("OMP_NUM_THREADS", str(min(4, (os.cpu_count() or 2))))
os.environ.setdefault("PDFTEXT_CPU_WORKERS", "1")

VALID_FORMATS = {"md", "html", "json"}
EXT = {"md": ".md", "html": ".html", "json": ".json"}
DEFAULT_CHUNK = 4

# Blocks που είναι «τρεχούμενες κεφαλίδες/υποσέλιδα» — τα παραλείπουμε
_SKIP = {"PageHeader", "PageFooter", "PageNumber"}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _page_count(pdf_path: str) -> int:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    n = len(doc)
    doc.close()
    return n


def _inline(html: str) -> str:
    """Απλό html -> κείμενο, διατηρώντας bold/italic ως markdown."""
    if not html:
        return ""
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", html)
    s = re.sub(r"(?i)</\s*p\s*>", "\n", s)
    s = re.sub(r"(?i)<\s*(b|strong)\s*>", "**", s)
    s = re.sub(r"(?i)</\s*(b|strong)\s*>", "**", s)
    s = re.sub(r"(?i)<\s*(i|em)\s*>", "*", s)
    s = re.sub(r"(?i)</\s*(i|em)\s*>", "*", s)
    s = re.sub(r"<[^>]+>", "", s)            # υπόλοιπα tags
    s = _html.unescape(s)
    s = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines())
    return s.strip()


def _walk_blocks(node):
    """Yield (block_type, block_dict) σε σειρά ανάγνωσης, μπαίνοντας σε groups."""
    for child in node.get("children") or []:
        bt = child.get("block_type")
        # Groups (λίστες κ.λπ.): μπες μέσα
        if bt in ("ListGroup", "Group", "FigureGroup", "TableGroup", "PictureGroup"):
            yield from _walk_blocks(child)
        else:
            yield bt, child
            if child.get("children") and bt not in ("Table", "Form", "TableOfContents"):
                yield from _walk_blocks(child)


def _save_images(page: dict, item_dir: Path, abs_page: int) -> dict:
    """Αποθηκεύει base64 εικόνες της σελίδας -> {block_id: filename}."""
    import base64
    out = {}
    for bid, data in (page.get("images") or {}).items():
        if not data:
            continue
        raw = data.split(",", 1)[1] if isinstance(data, str) and "," in data else data
        try:
            blob = base64.b64decode(raw)
        except Exception:
            continue
        safe = re.sub(r"[^0-9A-Za-z]+", "_", bid).strip("_")
        fname = f"p{abs_page}_{safe}.png"
        try:
            (item_dir / fname).write_bytes(blob)
            out[bid] = fname
        except Exception:
            pass
    return out


def _render_md(doc_pages, title, src_name) -> str:
    """doc_pages: λίστα (abs_page, page_dict, images_map). -> citation-ready Markdown."""
    lines = [f"# {title}" if title else f"# {src_name}", ""]
    lines.append(f"> **Πηγή:** `{src_name}`  ")
    lines.append(f"> **Σύνολο σελίδων:** {len(doc_pages)}")
    lines.append("")
    first_header_skipped = title is None  # ο 1ος SectionHeader = τίτλος (ήδη πάνω) -> skip
    for abs_page, page, imgs in doc_pages:
        lines.append(f"\n<!-- σελίδα {abs_page} -->\n")
        for bt, blk in _walk_blocks(page):
            if bt in _SKIP:
                continue
            html = blk.get("html", "")
            if bt == "SectionHeader":
                txt = _inline(html)
                if not txt:
                    continue
                if not first_header_skipped:
                    first_header_skipped = True
                    continue
                lines.append(f"\n## {txt}\n")
            elif bt == "Table":
                lines.append("")
                lines.append(html.strip())   # κρατάμε τον πίνακα ως HTML (render σε MD)
                lines.append("")
            elif bt in ("ListItem",):
                lines.append(f"- {_inline(html)}")
            elif bt in ("Picture", "Figure"):
                fn = imgs.get(blk.get("id"))
                lines.append(f"\n![{bt}]({fn})\n" if fn else f"\n*[{bt}]*\n")
            elif bt == "Equation":
                lines.append(f"\n{_inline(html)}\n")
            else:  # Text, Caption, Footnote, Reference, TextInlineMath, Code...
                txt = _inline(html)
                if txt:
                    lines.append(txt + "\n")
    return "\n".join(lines).rstrip() + "\n"


def _render_html(doc_pages, title, src_name) -> str:
    esc = _html.escape
    out = ["<!DOCTYPE html>", "<html>", "<head><meta charset=\"utf-8\"/>",
           f"<title>{esc(title or src_name)}</title></head>", "<body>",
           f"<h1>{esc(title or src_name)}</h1>",
           f"<p><em>Πηγή: {esc(src_name)} — {len(doc_pages)} σελίδες</em></p>"]
    first_header_skipped = title is None
    for abs_page, page, imgs in doc_pages:
        out.append(f"<hr/><p style=\"color:#888\">— σελίδα {abs_page} —</p>")
        for bt, blk in _walk_blocks(page):
            if bt in _SKIP:
                continue
            html = blk.get("html", "")
            if bt == "SectionHeader":
                if not first_header_skipped:
                    first_header_skipped = True
                    continue
                out.append(f"<h2>{esc(_inline(html))}</h2>")
            elif bt == "Table":
                out.append(html)
            elif bt in ("Picture", "Figure"):
                fn = imgs.get(blk.get("id"))
                out.append(f"<img src=\"{fn}\"/>" if fn else f"<p><em>[{bt}]</em></p>")
            else:
                out.append(f"<p block-type=\"{bt}\">{esc(_inline(html))}</p>")
    out += ["</body>", "</html>", ""]
    return "\n".join(out)


def _render_pages(pdf_path, page_nums, item_dir, scale=2.0) -> dict:
    """Αποθηκεύει PNG των «ύποπτων» σελίδων (χωρίς κείμενο/χαλασμένων).

    Δεν μαντεύουμε τι γράφουν: τις δίνουμε ΕΙΚΟΝΑ, να τις δει άνθρωπος ή AI με
    όραση. Έτσι το «τι χάθηκε» δεν μένει κρυφό — γίνεται κάτι που ελέγχεις.
    """
    import pypdfium2 as pdfium

    shots = {}
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for pno in page_nums:
            try:
                page = doc[pno - 1]                       # 1-based -> 0-based
                img = page.render(scale=scale).to_pil()
                fname = f"page_{pno:03d}.png"
                img.save(item_dir / fname)
                page.close()
                shots[str(pno)] = fname
            except Exception:  # noqa: BLE001 — αν δεν ανοίγει, δεν γίνεται εικόνα
                continue
    finally:
        doc.close()
    return shots


def _nums(pages: list[int], limit: int = 12) -> str:
    head = ", ".join(str(p) for p in pages[:limit])
    return head + (f" (+{len(pages) - limit} ακόμα)" if len(pages) > limit else "")


def _coverage_pct(report: dict) -> float:
    pages = report.get("pages") or []
    if not pages:
        return 0.0
    return 100.0 * sum(p["coverage"] for p in pages) / len(pages)


def _write_report(report: dict, item_dir, name: str, pdf_path, title) -> None:
    """report.json δίπλα στην έξοδο: ΤΙ κρατήθηκε, ΤΙ ανακτήθηκε, ΤΙ θέλει μάτια."""
    report = dict(report)
    report["source"] = pdf_path.name
    report["title"] = title
    report["coverage_pct"] = round(_coverage_pct(report), 2)
    report["verified"] = (
        not report["needs_ocr_pages"]
        and not report["broken_pages"]
        and report["coverage_pct"] >= 99.9
    )

    # Σε ανθρώπινα λόγια: τι (αν κάτι) χρειάζεται μάτια.
    todo = []
    if report.get("recovered_pages"):
        todo.append(
            f"Σελίδες {_nums(report['recovered_pages'])}: το κείμενο ανακτήθηκε ΠΛΗΡΩΣ "
            f"(ίδιοι χαρακτήρες), αλλά χωρίς επικεφαλίδες/δομή.")
    if report.get("needs_ocr_pages"):
        todo.append(
            f"Σελίδες {_nums(report['needs_ocr_pages'])}: δεν έχουν κείμενο (εικόνα/σκαναρισμένες). "
            f"Αποθηκεύτηκαν ως PNG δίπλα — δες τες, ή τρέξε ξανά με OCR.")
    if report.get("unreadable_pages"):
        todo.append(
            f"Σελίδες {_nums(report['unreadable_pages'])}: ΧΑΛΑΣΜΕΝΕΣ — δεν ανοίγουν ούτε ως εικόνα. "
            f"Άνοιξε το PDF μόνος σου και βγάλε screenshot αν τις χρειάζεσαι.")
    report["needs_attention"] = todo
    (item_dir / f"{name}.report.json").write_text(
        _json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


_MAX_NAME = 60   # Windows MAX_PATH=260· το όνομα μπαίνει ΔΥΟ φορές (φάκελος + αρχείο)


def _safe_name(stem: str) -> str:
    """Κόβει υπερβολικά μεγάλα ονόματα αρχείων.

    Στα Windows το πλήρες path δεν μπορεί να ξεπερνά τους 260 χαρακτήρες. Ένα paper
    με 100άρι όνομα, μέσα σε ομώνυμο φάκελο, ξεπερνά το όριο και το γράψιμο σκάει με
    «No such file or directory». Το `source` στο JSON μένει το ΠΛΗΡΕΣ όνομα — κόβεται
    μόνο το path της εξόδου.
    """
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:_MAX_NAME].rstrip(" .") if len(stem) > _MAX_NAME else stem


def _write_output(doc_pages, title, pdf_path, item_dir, name, fmt, outputs, progress):
    """Γράφει md/html/json — κοινό και για τις δύο διαδρομές (fast text / Marker)."""
    out_file = item_dir / f"{name}{EXT[fmt]}"
    if fmt == "md":
        out_file.write_text(_render_md(doc_pages, title, pdf_path.name), encoding="utf-8")
    elif fmt == "html":
        out_file.write_text(_render_html(doc_pages, title, pdf_path.name), encoding="utf-8")
    else:  # json — με ρητό απόλυτο "page" σε κάθε σελίδα
        payload = {"title": title, "source": pdf_path.name, "pages": []}
        for abs_page, page, _imgs in doc_pages:
            p = dict(page)
            p["page"] = abs_page
            payload["pages"].append(p)
        out_file.write_text(_json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    outputs.append(str(out_file))
    if progress:
        _log(f"DONE|{out_file}")


def _marker_models(state: dict):
    """Φορτώνει τα μοντέλα Marker ΜΟΝΟ όταν τα χρειαστούμε (σκαναρισμένο PDF).

    Είναι ~8 GB RAM και λεπτά φόρτωσης — δεν τα ακουμπάμε καθόλου αν όλα τα PDF
    είναι born-digital, που είναι και η συνήθης περίπτωση.
    """
    if "models" not in state:
        if state.get("progress"):
            _log("STEP|Σκαναρισμένο PDF — φόρτωση μοντέλων Marker (αργό)…")
        import torch
        torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "2")))
        from marker.models import create_model_dict
        state["models"] = create_model_dict()
    return state["models"]


def convert_files(pdf_paths, out_dir=None, fmt="json", chunk=DEFAULT_CHUNK, progress=True,
                  force_ocr=False):
    """out_dir=None -> η έξοδος μπαίνει ΔΙΠΛΑ στο κάθε PDF (στον φάκελο εισόδου).

    Γρήγορη διαδρομή: αν το PDF έχει text layer (born-digital), διαβάζεται κατευθείαν
    με το pdftext (δευτερόλεπτα, χωρίς μοντέλα). Μόνο τα σκαναρισμένα περνούν από
    Marker/OCR (ώρες). force_ocr=True επιβάλλει Marker παντού.
    """
    if fmt not in VALID_FORMATS:
        raise ValueError(f"Άγνωστο format: {fmt!r}. Διάλεξε από {sorted(VALID_FORMATS)}")

    import gc
    import fasttext_extract

    out_root = Path(out_dir).expanduser().resolve() if out_dir else None
    if out_root:
        out_root.mkdir(parents=True, exist_ok=True)

    state: dict = {"progress": progress}   # lazy φόρτωση μοντέλων Marker
    total = len(pdf_paths)
    outputs, failures = [], 0

    for idx, pdf in enumerate(pdf_paths, start=1):
        pdf_path = Path(pdf).expanduser().resolve()
        name = _safe_name(pdf_path.stem)
        if progress:
            _log(f"PROGRESS|{idx}|{total}|{pdf_path.name}")
        try:
            n_pages = _page_count(str(pdf_path))
            # Χωρίς --out: η έξοδος μπαίνει δίπλα στο PDF (φάκελος εισόδου)
            base = out_root if out_root else pdf_path.parent
            item_dir = base / name
            item_dir.mkdir(parents=True, exist_ok=True)

            doc_pages = []   # (abs_page, page_dict, images_map)
            title = None

            # ---- ΓΡΗΓΟΡΗ ΔΙΑΔΡΟΜΗ: born-digital -> χωρίς OCR/μοντέλα ----
            warn = (lambda m: _log(f"WARN|{pdf_path.name}|{m}")) if progress else None
            fast = None if force_ocr else fasttext_extract.extract(str(pdf_path), on_warn=warn)
            if fast is not None:
                fast_pages, title, report = fast
                if progress:
                    _log(f"INFO|{pdf_path.name}: text layer — γρήγορη εξαγωγή (χωρίς OCR)")
                    _log(f"CHUNK|1|{n_pages}|{n_pages}")
                started = time.monotonic()
                for page in fast_pages:
                    doc_pages.append((page["page"], page, {}))

                # Σελίδες που ΔΕΝ μπορούμε να εγγυηθούμε (κενές/σκαναρισμένες/χαλασμένες):
                # βγάλε τες εικόνες, ώστε να τις δει ο άνθρωπος ή ο AI με τα μάτια του.
                suspect = sorted(set(report["needs_ocr_pages"]) | set(report["broken_pages"]))
                if suspect:
                    shots = _render_pages(pdf_path, suspect, item_dir)
                    report["page_images"] = shots
                    # Όσες ΔΕΝ βγήκαν ούτε εικόνα: το pdfium δεν τις ανοίγει καθόλου.
                    # Δεν κρύβουμε το πρόβλημα — ζητάμε ανθρώπινο μάτι.
                    unreadable = [p for p in suspect if str(p) not in shots]
                    report["unreadable_pages"] = unreadable
                    if progress:
                        if shots:
                            _log(f"WARN|{pdf_path.name}|σελίδες χωρίς κείμενο — αποθηκεύτηκαν "
                                 f"ως εικόνες για έλεγχο: {', '.join(sorted(shots))}")
                        if unreadable:
                            _log(f"WARN|{pdf_path.name}|σελίδες {', '.join(map(str, unreadable))} "
                                 f"ΔΕΝ ανοίγουν καθόλου (ούτε ως εικόνα) — άνοιξε το PDF και "
                                 f"βγάλε screenshot αν χρειάζεσαι το περιεχόμενό τους")

                _write_report(report, item_dir, name, pdf_path, title)
                if progress:
                    _log(f"CHUNK_DONE|1|{n_pages}|{n_pages}|{time.monotonic() - started:.3f}")
                    _log(f"COVERAGE|{pdf_path.name}|{_coverage_pct(report):.1f}")
                _write_output(doc_pages, title, pdf_path, item_dir, name, fmt, outputs, progress)
                continue

            # ---- ΑΡΓΗ ΔΙΑΔΡΟΜΗ: σκαναρισμένο -> Marker/OCR ----
            from marker.converters.pdf import PdfConverter
            from marker.config.parser import ConfigParser
            from marker.output import text_from_rendered
            models = _marker_models(state)
            if progress:
                _log(f"INFO|batch={_BATCH} chunk={chunk} (auto-tuned από RAM)")

            for start in range(0, n_pages, chunk):
                end = min(start + chunk - 1, n_pages - 1)
                if progress:
                    _log(f"CHUNK|{start + 1}|{end + 1}|{n_pages}")
                chunk_started = time.monotonic()

                cfg = ConfigParser({
                    "output_format": "json",
                    "page_range": f"{start}-{end}",
                    "disable_tqdm": True,
                })
                converter = PdfConverter(
                    artifact_dict=models,
                    config=cfg.generate_config_dict(),
                    renderer=cfg.get_renderer(),
                )
                rendered = converter(str(pdf_path))
                data, _ext, _imgs = text_from_rendered(rendered)
                doc = data if isinstance(data, dict) else _json.loads(data)

                # Απόλυτη αρίθμηση: k-οστή σελίδα του chunk -> start+k (1-based)
                for k, page in enumerate(doc.get("children") or []):
                    abs_page = start + k + 1
                    imgs = _save_images(page, item_dir, abs_page)
                    doc_pages.append((abs_page, page, imgs))
                    if title is None:
                        for bt, blk in _walk_blocks(page):
                            if bt == "SectionHeader":
                                t = _inline(blk.get("html", ""))
                                if t:
                                    title = t
                                    break

                del converter, rendered, data
                gc.collect()
                if progress:
                    elapsed = time.monotonic() - chunk_started
                    _log(f"CHUNK_DONE|{start + 1}|{end + 1}|{n_pages}|{elapsed:.3f}")

            _write_output(doc_pages, title, pdf_path, item_dir, name, fmt, outputs, progress)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if progress:
                _log(f"ERROR|{pdf_path.name}|{exc}")
            else:
                traceback.print_exc()

    if progress:
        _log(f"ALLDONE|{len(outputs)}|{failures}")
    return outputs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="PDF -> δομημένο MD/HTML/JSON. Text layer: γρήγορα (pdftext)· "
                    "σκαναρισμένα: Marker/OCR.")
    p.add_argument("pdfs", nargs="+", help="Ένα ή περισσότερα PDF")
    p.add_argument("--out", "-o", default=None,
                   help="Φάκελος προορισμού (default: δίπλα στο κάθε PDF)")
    p.add_argument("--format", "-f", default="json", choices=sorted(VALID_FORMATS),
                   help="json (default) | md | html")
    p.add_argument("--chunk", "-c", type=int, default=DEFAULT_CHUNK,
                   help=f"Σελίδες ανά πέρασμα στο OCR (default {DEFAULT_CHUNK}· μικρότερο = λιγότερη RAM)")
    p.add_argument("--force-ocr", action="store_true",
                   help="Marker/OCR ΠΑΝΤΑ, ακόμα κι αν το PDF έχει text layer (πολύ αργό)")
    args = p.parse_args(argv)
    try:
        convert_files(args.pdfs, args.out, fmt=args.format, chunk=args.chunk,
                      force_ocr=args.force_ocr)
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR|setup|{exc}")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

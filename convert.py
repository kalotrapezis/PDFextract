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
    avail_gb = 4.0
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable"):
                    avail_gb = int(ln.split()[1]) / 1048576
                    break
    except Exception:
        pass
    headroom = avail_gb - 8.5      # χώρος πέρα από τα μοντέλα
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


def convert_files(pdf_paths, out_dir=None, fmt="json", chunk=DEFAULT_CHUNK, progress=True):
    """out_dir=None -> η έξοδος μπαίνει ΔΙΠΛΑ στο κάθε PDF (στον φάκελο εισόδου)."""
    if fmt not in VALID_FORMATS:
        raise ValueError(f"Άγνωστο format: {fmt!r}. Διάλεξε από {sorted(VALID_FORMATS)}")

    import gc
    import torch
    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "2")))

    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.config.parser import ConfigParser
    from marker.output import text_from_rendered

    out_root = Path(out_dir).expanduser().resolve() if out_dir else None
    if out_root:
        out_root.mkdir(parents=True, exist_ok=True)
    if progress:
        _log(f"INFO|batch={_BATCH} chunk={chunk} (auto-tuned από RAM)")
    models = create_model_dict()

    total = len(pdf_paths)
    outputs, failures = [], 0

    for idx, pdf in enumerate(pdf_paths, start=1):
        pdf_path = Path(pdf).expanduser().resolve()
        name = pdf_path.stem
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
    p = argparse.ArgumentParser(description="PDF -> δομημένο MD/HTML/JSON με Marker (χαμηλή μνήμη).")
    p.add_argument("pdfs", nargs="+", help="Ένα ή περισσότερα PDF")
    p.add_argument("--out", "-o", default=None,
                   help="Φάκελος προορισμού (default: δίπλα στο κάθε PDF)")
    p.add_argument("--format", "-f", default="json", choices=sorted(VALID_FORMATS),
                   help="json (default) | md | html")
    p.add_argument("--chunk", "-c", type=int, default=DEFAULT_CHUNK,
                   help=f"Σελίδες ανά πέρασμα (default {DEFAULT_CHUNK}· μικρότερο = λιγότερη RAM)")
    args = p.parse_args(argv)
    try:
        convert_files(args.pdfs, args.out, fmt=args.format, chunk=args.chunk)
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR|setup|{exc}")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

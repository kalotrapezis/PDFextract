# Changelog

## [2.0.0] — 2026-07-15

The Linux application is now named **PDFExtractor**. The Debian/RPM package,
desktop launcher and release artifact use the new name; the legacy `pdf-html`
command remains available as a compatibility alias.

Conversion no longer runs OCR on PDFs that don't need it. Research PDFs are almost
always born-digital: the text is already inside the file. Reading it directly instead
of running Surya/Marker over every page took a 28-paper batch from an estimated
**~50 hours to 51 seconds**, with **100.0% verified character coverage**.

This is a behavioural change, not just a speedup — see *Verification* below for why
the output is now checkable rather than trusted.

### Added

- **Fast text-layer extraction** (`fasttext_extract.py`, new). Uses `pdftext`
  (already a Marker dependency) to read the embedded text layer directly: no models,
  no OCR, no ~8 GB of RAM. Emits the **same JSON schema as Marker**
  (`pages[].children[]` with `block_type` / `html` / `id`), so `ingest.py`, the
  renderers and the SQLite schema are untouched. Page numbers, sections and
  paragraphs all survive — page numbers come from the PDF page index, so citations
  stay exact.
- **Per-page verification and recovery.** Every page's extracted characters are
  compared against the raw `pdfium` text layer (the ground truth). If characters are
  missing, the page is re-extracted from the raw text layer — exact characters,
  weaker structure — and flagged. Coverage is reported per page.
- **`<paper>.report.json`** next to every output: per-page `coverage` and `status`
  (`ok` / `recovered` / `needs_ocr`), plus `broken_pages`, `unreadable_pages`,
  `verified`, and a plain-language `needs_attention` list.
- **Page screenshots for pages we cannot read.** Pages with no text layer are
  rendered to PNG (`page_NNN.png`) so a human or a vision model can read them.
  Nothing is ever invented to fill a gap.
- **Heading detection without font metrics.** Some publishers (e.g. Elsevier) report
  every span as `size=1.0` with no bold flags, blinding any font-based heuristic.
  A short standalone numbered line (`1. Introduction`, `3.2.1. Types of participants`)
  is now treated as a heading on its own evidence.
- **Title resolution.** PDF metadata title when it is real, else the largest-font
  text on the first pages. Repository cover sheets (King's Research Portal, NIHMS,
  tandfonline) no longer hijack the title, and their DOI/URL/ISSN lines no longer
  become bogus sections.
- **Force-OCR option in all three GUIs** (🐌 checkbox) and `--force-ocr` on
  `convert` and `build`. Routes everything through Marker regardless of text layer,
  with an estimated-hours warning based on the loaded page count.
- **JSON + database in one run.** `build` now writes the JSON (and reports) into
  `<package>/json/` alongside `<package>.db`, instead of leaving JSON next to the
  source PDFs. GUI format option renamed to **"Βάση + JSON"**.
- **Per-file mode indicator in the GUI.** Each PDF is marked ⚡ (text layer, seconds)
  or 🐌 (scanned, needs OCR) as it is added, with a summary line before you convert.

### Changed

- Ported the full text-layer extraction, verification, recovery and lazy-OCR
  workflow to Linux while retaining every Linux CLI tool.
- Added the Linux GUI option **“Βάση + JSON — συγγραφή & έλεγχος”**. It writes
  full JSON/report files for context-preserving thesis work and a SQLite database
  for retrieval, cross-checking and accuracy review in the same run.
- **Marker models are no longer downloaded at first launch.** The app starts
  instantly and downloads nothing. The ~3 GB of OCR models are fetched on demand, the
  first time a genuinely scanned PDF appears. Pre-download with `--download-models`.
- Marker models load lazily inside `convert`, only on the OCR path.
- The RAM gauge is relabelled: it only matters for scanned PDFs now.
- `psutil` added as a dependency (replaces `/proc/meminfo` reads).

### Fixed

- **`pdftext` silently drops text on some PDFs.** Confirmed upstream, not in our
  code: on `hpr-2014-3-1962.pdf` it emitted only 84.7% of the characters present in
  the text layer (~3,900 characters missing). Without the new coverage check this
  would have been invisible. Such pages are now detected and recovered to 100%.
- **Damaged pages killed the whole paper.** Two PDFs contain pages `pdfium` cannot
  load (`Failed to load page`). Those pages are now skipped with a warning and the
  rest of the paper is converted, instead of the file failing outright. They cannot
  be rendered to PNG either, so the report tells you to screenshot them yourself.
- Long filenames exceeded Windows `MAX_PATH` (260 chars) — output names are capped
  at 60 chars. The full filename is preserved in the JSON `source` field.

### Linux verification

- Converted 78/78 supplied research PDFs through the fast path in 1 minute 15
  seconds without importing Marker/PyTorch or loading OCR models.
- Produced 78 JSON files and 78 verification reports with 99.9995% average
  character coverage. Eight incomplete pages were recovered to 100%.
- Two PDFs with damaged pages completed without aborting; the unreadable pages
  were isolated and listed in their reports.
- Two image-only PDFs in the broader test folder were correctly detected as OCR
  inputs. The slow OCR test was stopped intentionally after lazy model loading
  was confirmed.

### Known limitations (deliberate)

- Heading detection and two-column reading order are **heuristics**, not a layout
  model. Content fidelity is exact and measured; structure is approximate. On
  two-column papers section order can interleave, and a table row occasionally
  registers as a heading. Use `--force-ocr` when structure matters more than time.
- `recovered` pages lose their headings (the text is complete, the structure is not).

---

## Porting to Linux

The Linux tree keeps its own launcher/packaging. Port the extraction and verification
work; **do not** port the Windows packaging.

### 1. Copy as-is (platform-neutral)

| File | Note |
|---|---|
| `fasttext_extract.py` | **new** — the whole fast path. No Windows-specific code. |
| `convert.py` | fast path, verification, `_render_pages`, `_write_report`, `--force-ocr` |
| `build.py` | `--force-ocr`, JSON written into the package (`json/`) |
| `gui.py` | ⚡/🐌 per-file marking, force-OCR checkbox, "Βάση + JSON" |
| `db_gui.py`, `kb_gui.py` | force-OCR checkbox, `--out` so DB+JSON land together |
| `download_models.py` | **new** — the old inline `python -c` download, as a module |

`_safe_name()` in `convert.py` caps output names at 60 chars for Windows `MAX_PATH`.
Harmless on Linux (and still useful — ext4 caps filenames at 255 bytes, and Greek
filenames are multi-byte). Keep it.

### 2. Do NOT copy (Windows-only)

`main.py`, `worker_main.py`, `pdf-html.spec`, `build_exe.ps1`, `run.bat` — these exist
because the Windows build is a frozen two-exe PyInstaller bundle. Linux keeps
`run.sh` / `install.sh` / `packaging/`.

### 3. Add a Linux `runtime.py`

The GUIs and `build.py` now import `runtime`. Linux needs its own, so the shared code
stays identical across both platforms:

```python
"""Linux runtime: πώς καλείται ο worker, UTF-8 stdio, ελεύθερη RAM."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FROZEN = False
APP_DIR = Path(__file__).resolve().parent
WORKER_EXE = None  # δεν υπάρχει frozen worker στο Linux


def venv_python() -> str:
    return str(APP_DIR / ".venv" / "bin" / "python")


def worker_cmd(module: str, *args: str) -> list[str]:
    return [venv_python(), str(APP_DIR / f"{module}.py"), *args]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def available_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:  # noqa: BLE001
        pass
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable"):
                    return int(ln.split()[1]) / 1048576
    except Exception:  # noqa: BLE001
        pass
    return 4.0


def popen_kwargs() -> dict:
    return {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        "encoding": "utf-8",
        "errors": "replace",
    }
```

Note: on Linux the worker is invoked as `python <module>.py`, so `configure_stdio()`
is not called automatically. Either add `configure_stdio()` at the top of each
worker's `main()`, or leave it out — Linux already defaults to UTF-8, so the Greek
progress messages that crashed on Windows (`cp1252`) are not a problem there.

### 4. Other Linux-side edits

- `install.sh`: add `psutil` to the pip install line.
- `run.sh` / `firstrun.py`: startup no longer downloads models. Launch `gui.py`
  directly; keep `firstrun.py` reachable for optional pre-download.

### 5. Verify the port

Point it at a folder of real papers and confirm the numbers:

```bash
.venv/bin/python convert.py papers/*.pdf --out /tmp/out --format json
grep -h coverage_pct /tmp/out/*/*.report.json   # όλα ~100
```

A born-digital paper should convert in seconds and report `coverage_pct: 100.0`.
If any paper reports below 100 with no `recovered` pages, the recovery path is not
wired up correctly.

---

## GitHub page updates

- `README.md` is rewritten for the new two-path behaviour (fast text layer vs OCR)
  and the Windows exe install. The Linux repo's README needs the equivalent
  "two paths, picked per file" section and the speed table.
- Release notes for `v2.0.0`: lead with **50 hours → 51 seconds, 100% verified
  coverage**, then the `pdftext` data-loss fix, then the on-demand model download.
- Windows release asset: `PDF-HTML-windows.zip` (354 MB; unpacks to ~834 MB —
  two executables sharing one folder; models are downloaded on demand, not bundled).

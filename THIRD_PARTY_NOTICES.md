# Third-party projects

PDFextract is an integration and desktop workflow built on established open-source
projects. Their authors retain copyright in their respective projects. Dependency
versions and licenses can change; the linked upstream repositories and installed
package metadata are authoritative.

PDFextract itself is distributed under GNU GPL v3; see `LICENSE`.

## Core runtime

| Project | Purpose | Upstream license |
|---|---|---|
| [Marker](https://github.com/datalab-to/marker) | PDF layout/OCR conversion | GPL-3.0-or-later |
| [Surya](https://github.com/datalab-to/surya) | OCR, layout, reading order, and table recognition used by Marker | GPL-3.0-or-later |
| [PyTorch](https://github.com/pytorch/pytorch) | CPU inference runtime | BSD-style license; see upstream |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | PDF access and page counting | Apache-2.0/BSD-3-Clause plus PDFium third-party terms |
| [TkinterDnD2](https://github.com/Eliav2/tkinterdnd2) | Drag and drop for Tk | MIT |
| [Sentence Transformers](https://github.com/huggingface/sentence-transformers) | Optional semantic embeddings | Apache-2.0 |
| [sqlite-vec](https://github.com/asg017/sqlite-vec) | Optional vector search in SQLite | MIT or Apache-2.0 |
| [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) | Optional embedding model | See the upstream model card |

## Model files

No Marker, Surya, or BGE-M3 model weights are stored in this repository or in
the Debian package. On first use, the upstream libraries download the files from
their configured upstream locations into the user's local caches. Those files
remain governed by their upstream terms.

PDFextract does not claim ownership of these projects or models. It provides the
GUI, low-memory chunking workflow, structured renderers, SQLite schema, ingestion,
query tools, packaging, and integration code around them.

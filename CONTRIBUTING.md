# Contributing

Contributions are welcome through GitHub issues and pull requests.

## Development setup

1. Use Linux with Python 3.12 or 3.13 and Tk installed.
2. Create a virtual environment: `python3 -m venv .venv`.
3. Install CPU PyTorch from the official PyTorch index.
4. Install `marker-pdf`, `tkinterdnd2`, `psutil`, `sentence-transformers`, and `sqlite-vec`.
5. Run the main GUI with `./run.sh`.

Only the first scanned-PDF/forced-OCR run downloads the OCR/layout models. Do not add model
weights, PDFs, generated JSON/Markdown, databases, or virtual environments to
commits. The repository `.gitignore` deliberately excludes them.

## Before opening a pull request

- Run `python3 -m py_compile` for the Python modules you changed.
- Run `bash -n` for changed shell scripts.
- For database changes, ingest a small non-private JSON fixture into a temporary
  SQLite database and verify the paper, section, block, and FTS counts.
- For GUI changes, test on a Linux desktop and verify that conversion still runs
  in the worker virtual environment.
- Keep personal documents and API keys out of issues, fixtures, logs, and commits.

Please describe the reason for the change, user impact, and checks performed.

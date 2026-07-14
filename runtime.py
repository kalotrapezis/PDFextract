"""Linux runtime: worker commands, UTF-8 stdio, and available RAM."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FROZEN = False
APP_DIR = Path(__file__).resolve().parent
WORKER_EXE = None


def venv_python() -> str:
    return str(APP_DIR / ".venv" / "bin" / "python")


def worker_cmd(module: str, *args: str) -> list[str]:
    """Run a Linux worker as ``.venv/bin/python <module>.py``."""
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
        with open("/proc/meminfo", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable"):
                    return int(line.split()[1]) / 1048576
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

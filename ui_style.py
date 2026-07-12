"""Shared, portable Tk font configuration for Linux desktops."""
from __future__ import annotations

import tkinter.font as tkfont
import tkinter as tk
from pathlib import Path

UI_FONT = "DejaVu Sans"
MONO_FONT = "DejaVu Sans Mono"


def configure_fonts(root) -> None:
    """Prefer open DejaVu fonts, falling back to Tk's native named fonts."""
    families = set(tkfont.families(root))
    ui = UI_FONT if UI_FONT in families else tkfont.nametofont("TkDefaultFont").cget("family")
    mono = MONO_FONT if MONO_FONT in families else tkfont.nametofont("TkFixedFont").cget("family")
    for name, size, weight in (
        ("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
        ("TkMenuFont", 10, "normal"), ("TkHeadingFont", 10, "bold"),
        ("TkCaptionFont", 10, "bold"), ("TkSmallCaptionFont", 9, "normal"),
        ("TkIconFont", 10, "normal"), ("TkTooltipFont", 9, "normal"),
    ):
        tkfont.nametofont(name, root=root).configure(family=ui, size=size, weight=weight)
    tkfont.nametofont("TkFixedFont", root=root).configure(family=mono, size=10)

    # Set the title-bar/task-switcher icon as well as the desktop-menu icon.
    candidates = (
        Path(__file__).resolve().parent / "assets" / "pdf-html.png",
        Path("/usr/share/icons/hicolor/512x512/apps/pdf-html.png"),
    )
    for path in candidates:
        if path.exists():
            try:
                root._pdf_html_icon = tk.PhotoImage(file=str(path))  # keep reference alive
                root.iconphoto(True, root._pdf_html_icon)
                break
            except tk.TclError:
                pass

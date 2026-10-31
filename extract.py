"""Text extraction: TXT/MD -> normalized list of lines.

These lines are the basis for the chunker's row protocol, so long lines
are wrapped and runs of blank lines are collapsed into a single one.

PDFs are deliberately out of scope. Extracting a PDF well means OCR for
scans and a vision model for figures, which is what img-text-extractor
does; a plain text-layer read here would silently drop every figure and
return noise for scanned pages.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

SUPPORTED_SUFFIXES = (".txt", ".md")


def load_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raise ValueError(
            "PDFs are not read here. Convert the PDF first with "
            "img-text-extractor (https://github.com/LinoBugia/img-text-extractor), "
            "then feed the resulting .txt to norma-miner.")
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported format: {suffix} (allowed: {SUPPORTED_SUFFIXES})")
    return path.read_text(encoding="utf-8", errors="replace")


def to_lines(text: str, wrap_width: int = 100) -> list[str]:
    """Normalize text into lines: wrap long lines, at most one blank in a row."""
    lines: list[str] = []
    blank_pending = False
    for raw in text.splitlines():
        raw = raw.rstrip()
        if not raw.strip():
            blank_pending = bool(lines)
            continue
        if blank_pending:
            lines.append("")
            blank_pending = False
        if len(raw) <= wrap_width:
            lines.append(raw)
        else:
            lines.extend(textwrap.wrap(
                raw, width=wrap_width,
                break_long_words=False, break_on_hyphens=False,
            ) or [raw])
    return lines


def load_lines(path: str | Path, wrap_width: int = 100) -> list[str]:
    lines = to_lines(load_text(path), wrap_width)
    if not lines:
        raise RuntimeError(f"No text could be extracted from {path}")
    return lines

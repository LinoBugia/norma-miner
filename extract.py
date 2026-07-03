"""Text-Extraktion: PDF/TXT/MD -> normalisierte Zeilenliste.

Die Zeilen sind die Grundlage fuer das Row-Protokoll des Chunkers,
deshalb werden lange Zeilen umgebrochen und Leerzeilen-Bloecke
auf eine Leerzeile reduziert.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

SUPPORTED_SUFFIXES = (".pdf", ".txt", ".md")


def _pdf_to_text(path: Path) -> str:
    try:
        import fitz  # pymupdf
    except ImportError:
        fitz = None
    if fitz is not None:
        with fitz.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc)
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError(
            "Kein PDF-Backend gefunden. Bitte installieren: pip install pymupdf"
        ) from e
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def load_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Nicht unterstuetztes Format: {suffix} (erlaubt: {SUPPORTED_SUFFIXES})")
    if suffix == ".pdf":
        return _pdf_to_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


def to_lines(text: str, wrap_width: int = 100) -> list[str]:
    """Normalisiert Text zu Zeilen: Umbruch langer Zeilen, max. 1 Leerzeile am Stueck."""
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
        raise RuntimeError(f"Kein Text extrahierbar aus {path}")
    return lines

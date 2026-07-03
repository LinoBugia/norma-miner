"""Stage 1: Semantisches Chunking ueber das Row-Protokoll.

Der Chunker bekommt ein Fenster nummerierter Zeilen ("Row N: text") und
antwortet nach dem Thinking ausschliesslich mit Blockgrenzen der Form

    Row 1-24
    Row 25-64

Die Hilfsfunktionen hier bauen den Prompt, parsen die Antwort und
reparieren sie zu lueckenlosen, nicht ueberlappenden Bereichen.
"""

from __future__ import annotations

import re

RANGE_RE = re.compile(r"(?im)^\s*rows?\s+(\d+)\s*(?:[-–—]\s*(\d+))?\s*$")

SYSTEM_PROMPT = """\
You are a document segmentation engine. You receive an excerpt of a document \
as numbered rows ("Row N: text"). Your job is to split the excerpt into \
coherent semantic blocks. Each block covers exactly ONE topic, argument, \
scene or logical unit and should span roughly {min_lines}-{max_lines} rows.

Rules:
- Blocks must be contiguous: the first block starts at the first row of the \
excerpt, each following block starts right after the previous one ends.
- Never split inside a sentence; prefer boundaries at blank rows, headings \
or clear topic shifts.
- Cover the excerpt completely.

After you finish thinking, output ONLY the block boundaries, one per line, \
in exactly this format and nothing else:

Row <start>-<end>
Row <start>-<end>
"""


def build_messages(lines: list[str], start: int, end: int,
                   min_lines: int, max_lines: int) -> list[dict]:
    """Baut die Chat-Messages fuer das Fenster [start, end] (1-basiert, inkl.)."""
    numbered = "\n".join(f"Row {i}: {lines[i - 1]}" for i in range(start, end + 1))
    return [
        {"role": "system",
         "content": SYSTEM_PROMPT.format(min_lines=min_lines, max_lines=max_lines)},
        {"role": "user", "content": numbered},
    ]


def parse_ranges(text: str) -> list[tuple[int, int]]:
    ranges = []
    for m in RANGE_RE.finditer(text):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a <= b:
            ranges.append((a, b))
    return ranges


def normalize_ranges(ranges: list[tuple[int, int]], win_start: int,
                     win_end: int) -> list[tuple[int, int]]:
    """Clampt aufs Fenster und erzwingt lueckenlose, sortierte Bereiche."""
    clamped = sorted(
        (max(a, win_start), min(b, win_end))
        for a, b in ranges
        if b >= win_start and a <= win_end
    )
    result: list[tuple[int, int]] = []
    cursor = win_start
    for a, b in clamped:
        if b < cursor:
            continue  # liegt komplett in einem schon abgedeckten Bereich
        result.append((cursor, max(b, cursor)))
        cursor = result[-1][1] + 1
    return result


def split_oversize(lines: list[str], a: int, b: int,
                   max_lines: int) -> list[tuple[int, int]]:
    """Teilt zu grosse Bereiche, bevorzugt an Leerzeilen."""
    result = []
    start = a
    while b - start + 1 > max_lines:
        cut = start + max_lines - 1
        for i in range(cut, start + max_lines // 2, -1):
            if not lines[i - 1].strip():
                cut = i
                break
        result.append((start, cut))
        start = cut + 1
    result.append((start, b))
    return result


def fallback_ranges(lines: list[str], win_start: int, win_end: int,
                    max_lines: int) -> list[tuple[int, int]]:
    """Notfall-Chunking (Modellantwort unbrauchbar): feste Bloecke an Leerzeilen."""
    return split_oversize(lines, win_start, win_end, max_lines)


def is_blank(lines: list[str], a: int, b: int) -> bool:
    return all(not lines[i - 1].strip() for i in range(a, b + 1))

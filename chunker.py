"""Stage 1: Semantisches Chunking ueber das Row-Protokoll.

Der Chunker bekommt ein Fenster nummerierter Zeilen ("Row N: text") und
antwortet nach dem Thinking ausschliesslich mit Blockgrenzen der Form

    Row 1-24
    Row 25-64

Die Nummerierung startet in JEDEM Fenster bei 1 -- kleine Zahlen kann das
Modell zuverlaessig addieren, absolute Dokumentzeilen (z. B. 65-108) nicht.
Der Aufrufer rechnet die relativen Bereiche via Fenster-Offset zurueck.

Bereiche duerfen sich um wenige Zeilen ueberlappen (Row 24 in beiden
Bloecken), wenn eine semantische Grenze mitten in einer Zeile liegt.
Die Hilfsfunktionen hier bauen den Prompt, parsen die Antwort und
reparieren sie zu lueckenlosen, streng fortschreitenden Bereichen.
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
- The excerpt spans Row 1 to Row {n}. Your list must cover EVERY row from \
Row 1 up to and including Row {n} -- the first block starts at Row 1, the \
LAST block ends at Row {n}, no gaps in between. A list that stops before \
Row {n} is WRONG and unusable.
- Consecutive blocks MAY OVERLAP by up to {max_overlap} rows: when the \
semantic boundary falls INSIDE a row (e.g. mid-sentence, because two topics \
share one row), let the next block start on that shared row, so it appears \
in both blocks and both stay complete and readable.
- Prefer clean boundaries at blank rows, headings or clear topic shifts; \
use overlap only when a row genuinely belongs to both blocks.
- Never overlap by more than {max_overlap} rows.

IMPORTANT -- budget your thinking: your response length is limited. Scan \
the rows in ONE quick pass, note the topic shifts, and then STOP thinking \
and write the list. Do not deliberate row by row and do not revise your \
segmentation repeatedly -- if your thinking runs long, the list gets cut \
off and all your work is lost. The complete list matters more than a \
perfect boundary.

After you finish thinking, output ONLY the block boundaries, one per line, \
in exactly this format and nothing else (here block 2 overlaps block 1 on \
row 24):

Row 1-24
Row 24-64

Before you output, verify: does your last line end with {n}? If not, \
extend the list until it does.
"""


def build_messages(lines: list[str], start: int, end: int, min_lines: int,
                   max_lines: int, max_overlap: int = 0) -> list[dict]:
    """Baut die Chat-Messages fuer das Fenster [start, end] (1-basiert, inkl.).

    Die Zeilen werden dem Modell IMMER als Row 1..n praesentiert; die
    Antwort ist also relativ und muss vom Aufrufer mit to_absolute()
    zurueck auf Dokumentzeilen geschoben werden.
    """
    n = end - start + 1
    numbered = "\n".join(f"Row {i}: {lines[start - 1 + i - 1]}"
                         for i in range(1, n + 1))
    task = (f"\n\nSegment Rows 1-{n} ({n} rows) completely. Think briefly, "
            f"then output the full list of blocks from Row 1 through Row {n}.")
    return [
        {"role": "system",
         "content": SYSTEM_PROMPT.format(min_lines=min_lines,
                                         max_lines=max_lines,
                                         max_overlap=max(max_overlap, 0),
                                         n=n)},
        {"role": "user", "content": numbered + task},
    ]


def to_absolute(ranges: list[tuple[int, int]],
                win_start: int) -> list[tuple[int, int]]:
    """Schiebt relative Fensterbereiche (1-basiert) auf Dokumentzeilen."""
    offset = win_start - 1
    return [(a + offset, b + offset) for a, b in ranges]


def parse_ranges(text: str) -> list[tuple[int, int]]:
    ranges = []
    for m in RANGE_RE.finditer(text):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a <= b:
            ranges.append((a, b))
    return ranges


def normalize_ranges(ranges: list[tuple[int, int]], win_start: int,
                     win_end: int, max_overlap: int = 0) -> list[tuple[int, int]]:
    """Clampt aufs Fenster und repariert zu sortierten Bereichen ohne Luecken.

    Ueberlappungen bis max_overlap Zeilen sind erlaubt (geteilte Zeile gehoert
    zu beiden Bloecken); Starts und Enden bleiben streng aufsteigend, damit
    die Pipeline garantiert vorankommt.
    """
    clamped = sorted(
        (max(a, win_start), min(b, win_end))
        for a, b in ranges
        if b >= win_start and a <= win_end
    )
    result: list[tuple[int, int]] = []
    for a, b in clamped:
        if not result:
            result.append((win_start, max(b, win_start)))
            continue
        prev_a, prev_b = result[-1]
        if b <= prev_b:
            continue  # liegt komplett in einem schon abgedeckten Bereich
        if a > prev_b + 1:
            a = prev_b + 1  # Luecke schliessen
        else:
            a = max(a, prev_b - max_overlap + 1, prev_a + 1)  # Overlap deckeln
        result.append((a, b))
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

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
You are a document segmentation engine for a RAG indexing pipeline. You \
receive an excerpt of a document as numbered rows ("Row N: text"). Split \
it into coherent semantic blocks that will each be embedded and indexed \
as a standalone retrieval unit.

Each block must cover exactly ONE topic, argument, scene, definition, \
procedure, or logical unit, start and end on a natural semantic boundary \
(paragraph break, topic shift, section header, change of speaker, new \
step, new example, which fits PERFECTLY inside a clear semantical RAG chunk - \
it needs to be selfcontained enough that a retriever returning ONLY this block (without neighbors) still \
yields an understandable, answerable chunk.

Target size is roughly {min_lines}-{max_lines} rows per block as a \
guideline, not a hard rule. Deviate deliberately when semantics demand it: \
go SHORTER if a self-contained unit (header, definition, aphorism, short \
Q&A) naturally ends earlier, and go LONGER if splitting would break a \
single coherent argument or cut a thought in half. Never pad a short idea \
or merge unrelated ones to hit the range, and never fragment a unified \
one to stay inside it. When in doubt, prefer the boundary that best \
preserves semantic unity over the one that produces "nicer" sizes.

Rules:
{coverage_rule}
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
perfect boundary. Think like this: First identify shortly what type of document you are chunking, then identify topics which are closed enough to get together in the semantical chunk, then try to find the rownumbers to enclose them tightly together. If necessary split a big chunk in smaller parts. 

After you finish thinking, output ONLY the block boundaries, one per line, \
in exactly this format and nothing else (here block 2 overlaps block 1 on \
row 24):

Row 1-24
Row 24-64

{final_check}
"""

COVERAGE_FULL = """\
- The excerpt spans Row 1 to Row {n} and reaches the END of the document. \
Your list must cover EVERY row from Row 1 up to and including Row {n} -- \
the first block starts at Row 1, the LAST block ends at Row {n}, no gaps \
in between. A list that stops before Row {n} is WRONG and unusable."""

COVERAGE_OPEN = """\
- The excerpt spans Row 1 to Row {n}. It is a moving window over a longer \
document: the text CONTINUES after Row {n}, you just cannot see it. The \
topic running at Row {n} is therefore almost certainly INCOMPLETE -- you \
cannot know where it ends. Because of this, the tail of the excerpt is \
OFF-LIMITS by design: you MUST deliberately leave it uncovered. This is \
NOT "stopping a few rows early" and NOT a safety margin -- it is a \
required, conscious empty region at the end of the window, which you \
hand over to the next window so it can classify the ambiguous tail \
TOGETHER with its continuation. Concretely: start at Row 1, keep the \
list fully gapless up to your last block, and make your last block end \
at a boundary you are 100% sure about, clearly BEFORE Row {n} -- not at \
Row {n} and not immediately adjacent to it. After that last block, do \
NOT emit a further block covering the remaining rows up to Row {n}; \
leave them explicitly uncovered. You may output a block whose end row \
equals or is immediately adjacent to Row {n} -- such a block cuts a \
topic you cannot see the end of, and the handover to the next window \
could break. If unsure how much tail to leave empty, always err toward \
leaving MORE rows uncovered, never fewer."""

CHECK_FULL = """\
Before you output, verify: does your last line end with {n}? If not, \
extend the list until it does."""

CHECK_OPEN = """\
Before you output, verify: (1) does your list start at Row 1 with no \
gaps? (2) does your LAST block end several rows BEFORE Row {n}? If it \
ends at or near Row {n}, drop that last block if it is uncertain if the content behind would need to be in the chunk for proper semantical unity -- the uncovered tail could be \
required for a clean transition to the next window."""


def build_messages(lines: list[str], start: int, end: int, min_lines: int,
                   max_lines: int, max_overlap: int = 0,
                   is_final: bool = True) -> list[dict]:
    """Baut die Chat-Messages fuer das Fenster [start, end] (1-basiert, inkl.).

    Die Zeilen werden dem Modell IMMER als Row 1..n praesentiert; die
    Antwort ist also relativ und muss vom Aufrufer mit to_absolute()
    zurueck auf Dokumentzeilen geschoben werden.

    is_final=False (Fenster endet vor dem Dokumentende): das Modell darf
    einen blöd angeschnittenen Schluss unabgedeckt lassen, statt eine
    kuenstliche Blockgrenze zu erzwingen.
    """
    n = end - start + 1
    numbered = "\n".join(f"Row {i}: {lines[start - 1 + i - 1]}"
                         for i in range(1, n + 1))
    if is_final:
        task = (f"\n\nSegment Rows 1-{n} ({n} rows) completely. Think "
                f"briefly, then output the full list of blocks from Row 1 "
                f"through Row {n}."
                f"REQUIRED OUTPUT FORMAT: one block per line, nothing else, in the "
                f"exact form `Rows A-B` (e.g. `Rows 1-10`, `Rows 11-27`, ...). ")
    else:
        task = (f"\n\nSegment the excerpt (Rows 1-{n}, {n} rows). Think briefly, "
                f"then output the list of complete blocks starting at Row 1. "
                f"REMEMBER: the text continues after Row {n}, so ALWAYS leave the "
                f"last rows uncovered -- your final block must end several rows "
                f"before Row {n}.\n\n"
                f"REQUIRED OUTPUT FORMAT: one block per line, nothing else, in the "
                f"exact form `Rows A-B` (e.g. `Rows 1-10`, `Rows 11-27`, ...). "
                f"Blocks must be contiguous and gapless from Row 1 up to your last "
                f"block, and your last block's end row must be strictly less than "
                f"Row {n} (leave a clear uncovered tail). No prose, no numbering, "
                f"no explanations -- only the `Rows A-B` lines.")
    return [
        {"role": "system",
         "content": SYSTEM_PROMPT.format(
             min_lines=min_lines, max_lines=max_lines,
             max_overlap=max(max_overlap, 0),
             coverage_rule=(COVERAGE_FULL if is_final
                            else COVERAGE_OPEN).format(n=n),
             final_check=(CHECK_FULL if is_final
                          else CHECK_OPEN).format(n=n))},
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

"""Stage 2: Veredelt rohe Textbloecke zu informationsreichem Markdown.

Das Formatter-Modell bekommt einen Block plus Kontext -- Dokumenttitel,
Blockposition, die bisher erzeugte Kapitel-Outline (#/##/###) und den
zuletzt formatierten Block -- und liefert reines Markdown, das die
bestehende Ueberschriften-Hierarchie konsistent fortsetzt.
"""

from __future__ import annotations

import re

HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)

SYSTEM_PROMPT = """\
You are an expert technical editor. You transform raw text blocks into \
beautiful, information-dense Markdown written in {language}.

Rules:
- Preserve EVERY fact, number, name, date and detail from the source. \
Never invent facts. You may add short clarifications in parentheses where \
they aid understanding.
- Structure the block with meaningful headings (## / ###), short paragraphs, \
**bold** key terms, bullet lists for enumerations, tables for tabular data \
and > blockquotes for quotations.
- The block must be understandable on its own (resolve unclear pronouns \
using the provided context) -- the output is consumed by RAG pipelines and \
other LLMs.
- Fit seamlessly into the document structure built so far: start this block \
with a NEW heading that describes THIS block's content, at a level that fits \
the outline (## for a new chapter, ### for a subtopic of the current \
chapter). NEVER copy or repeat a heading that appears in the outline or in \
the previous block -- those sections are already written and lie above your \
block.
- Write fluent, clear {language}.
- Output ONLY the Markdown for this block: no preamble, no closing remarks, \
no code fences around the whole answer, and do NOT repeat the document title.
"""

USER_TEMPLATE = """\
Document: {title}
Block {index} of {total}.
{outline_section}{prev_section}
Raw text of the CURRENT block -- transform ONLY this into Markdown:
---
{chunk}
---
"""

OUTLINE_TEMPLATE = """
Document outline written so far. ALL of these headings already exist above \
your block -- do not write any of them again; add your own new heading(s) \
that extend this structure:
{outline}
"""

PREV_TEMPLATE = """
Previously formatted block (this is the text IMMEDIATELY BEFORE the current \
block; align headings, tone and terminology with it, do not repeat it):
<previous_block>
{prev}
</previous_block>
"""


def extract_headings(markdown: str) -> list[str]:
    """Liest #/##/###/####-Ueberschriften aus einem Markdown-Block."""
    return [f"{m.group(1)} {m.group(2)}" for m in HEADING_RE.finditer(markdown)]


def render_outline(headings: list[str], max_chars: int = 3000) -> str:
    """Outline als eingerueckte Liste; bei Ueberlaenge nur noch # und ##."""
    def fmt(hs: list[str]) -> str:
        return "\n".join("  " * (h.split(" ", 1)[0].count("#") - 1) + h
                         for h in hs)
    text = fmt(headings)
    if len(text) > max_chars:
        text = fmt([h for h in headings if h.startswith(("# ", "## "))])
    return text[-max_chars:]


def build_messages(chunk_text: str, *, language: str, title: str,
                   index: int, total: int, prev_markdown: str = "",
                   outline: list[str] | None = None) -> list[dict]:
    outline_section = (
        OUTLINE_TEMPLATE.format(outline=render_outline(outline))
        if outline else "")
    prev_section = (
        PREV_TEMPLATE.format(prev=prev_markdown) if prev_markdown else "")
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(language=language)},
        {"role": "user", "content": USER_TEMPLATE.format(
            title=title, index=index, total=total,
            outline_section=outline_section, prev_section=prev_section,
            chunk=chunk_text)},
    ]


def clean_output(text: str) -> str:
    """Entfernt versehentliche Code-Fences um die Gesamtantwort."""
    out = text.strip()
    if out.startswith("```"):
        first_nl = out.find("\n")
        if first_nl != -1 and out.endswith("```"):
            out = out[first_nl + 1:-3].strip()
    return out

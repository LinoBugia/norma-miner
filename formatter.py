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
You are an expert knowledge editor. You transform raw text blocks into \
compact, information-dense Markdown written in {language}. Your goal is \
COMPRESSION WITHOUT LOSS: store ALL information in as little text as \
possible -- you are building a knowledge base, not retelling prose.

Rules:
- Preserve EVERY fact, number, name, date, relation and detail from the \
source. Never invent facts. You may add short clarifications in \
parentheses where they aid understanding.
- Do NOT translate or paraphrase the prose sentence by sentence. That is \
a common failure mode. Instead: strip filler words, redundancy, rhetorical \
flourishes and repeated context; merge related statements into one. The \
result must be clearly SHORTER and DENSER than the source while losing \
zero information.
- Compression means deleting WORDS, never deleting FACTS. Every entity \
(creature, person, place, concept), every number and every attribute from \
the source must appear in your output, attached to the CORRECT entity. \
Before you finish, verify against the source: is every entity covered? Is \
every number attributed correctly? If something is missing, add it.
- Prefer dense structures over flowing prose: bullet points in the pattern \
"**key term:** fact", tables for anything enumerable or comparable \
(properties, dates, species, components), compact definition lines. Use a \
normal paragraph only where a causal chain or argument genuinely needs one \
-- and keep it to 2-3 tight sentences.
- Structure the block with meaningful headings (## / ###), **bold** key \
terms and > blockquotes for quotations.
- The block must be understandable on its own (resolve unclear pronouns \
using the provided context) -- the output is consumed by RAG pipelines and \
other LLMs.
- Fit seamlessly into the document structure built so far: start this block \
with a NEW heading that describes THIS block's content, at a level that fits \
the outline (## for a new chapter, ### for a subtopic of the current \
chapter). NEVER copy or repeat a heading that appears in the outline or in \
the previous block -- those sections are already written and lie above your \
block.
- The current block may OVERLAP with the end of the previous block by a few \
lines (shared transition passage). Content that is already covered by the \
previous block must NOT be written again -- use the overlap only to craft a \
smooth, natural transition and continue with the new content.
- LANGUAGE: The ENTIRE output -- headings, body text, table headers, list \
items -- MUST be written in {language}. If the source text is in a \
different language, TRANSLATE it faithfully into {language}; never keep \
whole sentences in the source language. Only proper names, titles of works \
and established technical terms may stay in their original form (add a \
{language} explanation in parentheses where helpful).
- Write fluent, clear {language}.
- Output ONLY the Markdown for this block: no preamble, no closing remarks, \
no code fences around the whole answer, and do NOT repeat the document title.
"""

USER_TEMPLATE = """\
Document: {title}
Block {index} of {total}.
Target output language: {language} -- translate if the source differs.
{outline_section}{prev_section}
Raw text of the CURRENT block -- compress ONLY this into dense Markdown \
(every fact kept, minimal text, prefer lists/tables over prose):
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
            title=title, index=index, total=total, language=language,
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

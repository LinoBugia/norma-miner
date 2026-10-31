"""Orchestrates the two-stage pipeline and reports everything as events.

Stage 1 (chunker):   a thinking model walks through the document window by
                     window and returns semantic row ranges.
Stage 2 (formatter): a text model turns every block into Markdown.

The stages run strictly one after another so Ollama has to load each model
only once (important with 24 GB RAM). All progress is handed to `emit` as
event dicts, which lets the GUI and the CLI share the same pipeline.

Event types:
  doc_loaded, stage, window, context, stream_reset, stream, chunk,
  fallback, format_current, chunk_done, progress, done, error, aborted
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import Callable

import chunker
import formatter
from extract import load_lines
from ollama_client import Aborted, chat_stream

Emit = Callable[[dict], None]

BLOCK_SEPARATOR = "\n\n---\n\n"


def est_tokens(text: str) -> int:
    """Rough token estimate (~3.5 characters per token)."""
    return max(1, int(len(text) / 3.5))


def _run_llm(cfg: dict, section: dict, messages: list[dict], *,
             emit: Emit, stop_event, label: str) -> str:
    """Stream one LLM call, report context + tokens, return the content."""
    prompt_text = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
    emit({"type": "context", "model": section["model"], "label": label,
          "prompt": prompt_text, "tokens_est": est_tokens(prompt_text),
          "num_ctx": section.get("num_ctx", 8192)})
    emit({"type": "stream_reset", "label": label, "model": section["model"]})

    options = {"num_ctx": section.get("num_ctx", 8192),
               "temperature": section.get("temperature", 0.3)}
    if section.get("num_predict"):
        options["num_predict"] = section["num_predict"]

    parts: list[str] = []
    for channel, text in chat_stream(
            cfg["ollama_url"], section["model"], messages,
            options=options, think=bool(section.get("think")),
            stop_event=stop_event):
        if channel == "content":
            parts.append(text)
        emit({"type": "stream", "channel": channel, "text": text})
    return "".join(parts)


def _stage_chunking(lines: list[str], cfg: dict, emit: Emit,
                    stop_event) -> list[tuple[int, int]]:
    ccfg = cfg["chunker"]
    total = len(lines)
    window = int(ccfg.get("window_lines", 100))
    min_lines = int(ccfg.get("min_chunk_lines", 4))
    max_lines = int(ccfg.get("max_chunk_lines", 40))
    max_overlap = int(ccfg.get("max_overlap_lines", 3))

    emit({"type": "stage", "stage": "chunking"})
    chunks: list[tuple[int, int]] = []
    cursor = 1
    while cursor <= total:
        win_end = min(cursor + window - 1, total)
        is_final = win_end >= total
        emit({"type": "window", "start": cursor, "end": win_end})

        messages = chunker.build_messages(lines, cursor, win_end,
                                          min_lines, max_lines, max_overlap,
                                          is_final=is_final)
        content = _run_llm(cfg, ccfg, messages, emit=emit,
                           stop_event=stop_event,
                           label=f"Chunker · rows {cursor}-{win_end}")

        # the model answers relative to the window (row 1..n) -> map back
        ranges = chunker.normalize_ranges(
            chunker.to_absolute(chunker.parse_ranges(content), cursor),
            cursor, win_end, max_overlap)
        if not ranges:
            # The model returned nothing parsable. Mechanical splitting keeps
            # the run alive but produces uniform, content-blind blocks, so it
            # must never happen silently.
            ranges = chunker.fallback_ranges(lines, cursor, win_end, max_lines)
            emit({"type": "fallback", "start": cursor, "end": win_end,
                  "blocks": len(ranges),
                  "reason": "no parsable row range in the model's answer"})
        # If the model spanned all the way to the window edge, its last range
        # may be cut mid-topic -> re-evaluate it in the next window. If it
        # stopped earlier (the intended uncovered tail), its blocks are
        # complete and the cursor resumes directly behind them.
        if not is_final and ranges[-1][1] >= win_end and len(ranges) > 1:
            ranges = ranges[:-1]

        for a, b in ranges:
            for sa, sb in chunker.split_oversize(lines, a, b, max_lines):
                if chunker.is_blank(lines, sa, sb):
                    continue
                chunks.append((sa, sb))
                emit({"type": "chunk", "index": len(chunks),
                      "start": sa, "end": sb})

        cursor = ranges[-1][1] + 1
        emit({"type": "progress", "value": 0.5 * min(cursor / (total + 1), 1.0),
              "label": f"Chunking: line {min(cursor, total)}/{total}"})

    if not chunks:
        raise RuntimeError("Chunking produced no blocks.")
    return chunks


def _stage_formatting(lines: list[str], chunks: list[tuple[int, int]],
                      cfg: dict, title: str, out_path: Path, emit: Emit,
                      stop_event) -> None:
    fcfg = cfg["formatter"]
    language = cfg.get("language", "Deutsch")
    prev_cap = int(fcfg.get("prev_block_chars", 4000))
    emit({"type": "stage", "stage": "formatting"})

    header = (f"# {title}\n\n> Source: `{title}` · prepared with norma-miner "
              f"on {datetime.date.today().isoformat()}\n")
    blocks: list[str] = []
    outline: list[str] = [f"# {title}"]
    prev_markdown = ""
    for i, (a, b) in enumerate(chunks, start=1):
        emit({"type": "format_current", "index": i, "total": len(chunks),
              "start": a, "end": b})
        chunk_text = "\n".join(lines[a - 1:b])
        messages = formatter.build_messages(
            chunk_text, language=language, title=title,
            index=i, total=len(chunks),
            prev_markdown=prev_markdown[-prev_cap:], outline=outline)
        content = _run_llm(cfg, fcfg, messages, emit=emit,
                           stop_event=stop_event,
                           label=f"Formatter · block {i}/{len(chunks)}")
        block = formatter.clean_output(content)
        blocks.append(block)
        prev_markdown = block
        outline.extend(formatter.extract_headings(block))

        # write incrementally: an abort keeps the progress made so far
        out_path.write_text(header + BLOCK_SEPARATOR
                            + BLOCK_SEPARATOR.join(blocks) + "\n",
                            encoding="utf-8")
        emit({"type": "chunk_done", "index": i, "start": a, "end": b})
        emit({"type": "progress", "value": 0.5 + 0.5 * i / len(chunks),
              "label": f"Formatting: block {i}/{len(chunks)}"})


def run(path: str | Path, cfg: dict, emit: Emit, stop_event=None) -> None:
    """Process one file end to end; errors are reported as events."""
    t0 = time.monotonic()
    path = Path(path)
    try:
        lines = load_lines(path, int(cfg.get("wrap_width", 100)))
        emit({"type": "doc_loaded", "name": path.name, "lines": lines})

        out_dir = Path(cfg.get("output_dir", "./output"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (path.stem + ".md")

        chunks = _stage_chunking(lines, cfg, emit, stop_event)
        _stage_formatting(lines, chunks, cfg, path.stem, out_path,
                          emit, stop_event)

        emit({"type": "done", "output": str(out_path),
              "seconds": time.monotonic() - t0, "chunks": len(chunks)})
    except Aborted:
        emit({"type": "aborted"})
    except Exception as e:  # noqa: BLE001 - the GUI should show every error
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"})

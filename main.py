#!/usr/bin/env python3
"""norma-miner: TXT/MD -> semantically chunked, LLM-friendly Markdown.

Usage:
    python main.py                        # GUI (monitor)
    python main.py --cli file.txt         # headless, stream to stdout
    python main.py --cli file.txt --config path.json --window 60
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_cli(file: str, config_path: str, window: int | None = None) -> int:
    import pipeline

    cfg = load_config(config_path)
    if window:
        cfg["chunker"]["window_lines"] = max(10, window)
    status = {"ok": True}

    def emit(ev: dict) -> None:
        kind = ev["type"]
        if kind == "stage":
            print(f"\n=== Stage: {ev['stage']} ===", flush=True)
        elif kind == "window":
            print(f"\n-- Chunker window rows {ev['start']}-{ev['end']}",
                  flush=True)
        elif kind == "chunk":
            print(f"   Block {ev['index']}: rows {ev['start']}-{ev['end']}",
                  flush=True)
        elif kind == "fallback":
            print(f"   !! FALLBACK rows {ev['start']}-{ev['end']}: {ev['reason']}"
                  f" -> {ev['blocks']} mechanical blocks", file=sys.stderr,
                  flush=True)
        elif kind == "format_current":
            print(f"\n-- Formatter block {ev['index']}/{ev['total']} "
                  f"(rows {ev['start']}-{ev['end']})", flush=True)
        elif kind == "stream" and ev["channel"] == "content":
            print(ev["text"], end="", flush=True)
        elif kind == "done":
            print(f"\n\nDone: {ev['output']} "
                  f"({ev['chunks']} blocks, {ev['seconds'] / 60:.1f} min)")
        elif kind == "error":
            status["ok"] = False
            print(f"\nError: {ev['message']}", file=sys.stderr)
        elif kind == "aborted":
            status["ok"] = False
            print("\nAborted.", file=sys.stderr)

    pipeline.run(file, cfg, emit)
    return 0 if status["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", metavar="FILE",
                        help="process a file headlessly instead of starting "
                             "the GUI")
    parser.add_argument("--config",
                        default=str(Path(__file__).parent / "config.json"),
                        help="path to the config file (default: config.json)")
    parser.add_argument("--window", type=int, default=None,
                        help="chunker window size in lines "
                             "(overrides window_lines, default 100)")
    args = parser.parse_args()

    if args.cli:
        return run_cli(args.cli, args.config, args.window)

    import app
    app.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

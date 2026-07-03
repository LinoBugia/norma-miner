#!/usr/bin/env python3
"""norma-miner: PDF/TXT/MD -> semantisch gechunktes, LLM-freundliches Markdown.

Aufruf:
    python main.py                       # GUI (Monitor)
    python main.py --cli datei.pdf       # headless, Stream auf stdout
    python main.py --cli datei.pdf --config pfad.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_cli(file: str, config_path: str) -> int:
    import pipeline

    cfg = load_config(config_path)
    status = {"ok": True}

    def emit(ev: dict) -> None:
        kind = ev["type"]
        if kind == "stage":
            print(f"\n=== Stufe: {ev['stage']} ===", flush=True)
        elif kind == "window":
            print(f"\n-- Chunker-Fenster Rows {ev['start']}-{ev['end']}",
                  flush=True)
        elif kind == "chunk":
            print(f"   Block {ev['index']}: Row {ev['start']}-{ev['end']}",
                  flush=True)
        elif kind == "format_current":
            print(f"\n-- Formatter Block {ev['index']}/{ev['total']} "
                  f"(Rows {ev['start']}-{ev['end']})", flush=True)
        elif kind == "stream" and ev["channel"] == "content":
            print(ev["text"], end="", flush=True)
        elif kind == "done":
            print(f"\n\n✔ fertig: {ev['output']} "
                  f"({ev['chunks']} Blöcke, {ev['seconds'] / 60:.1f} min)")
        elif kind == "error":
            status["ok"] = False
            print(f"\nFehler: {ev['message']}", file=sys.stderr)
        elif kind == "aborted":
            status["ok"] = False
            print("\nAbgebrochen.", file=sys.stderr)

    pipeline.run(file, cfg, emit)
    return 0 if status["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", metavar="DATEI",
                        help="Datei headless verarbeiten statt GUI zu starten")
    parser.add_argument("--config",
                        default=str(Path(__file__).parent / "config.json"))
    args = parser.parse_args()

    if args.cli:
        return run_cli(args.cli, args.config)

    import app
    app.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

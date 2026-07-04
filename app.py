"""norma-miner GUI: Live-Monitor fuer die Text-zu-Markdown-Pipeline.

Links das Dokument mit wanderndem Highlight (Chunker-Fenster, aktueller
Block, fertige Bloecke), rechts der Modell-Kontext (Prompt + Fuellstand)
und darunter der Generate-Stream inkl. Thinking.

Start: python app.py   (oder python main.py)
"""

from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

import pipeline
from extract import SUPPORTED_SUFFIXES
from ollama_client import list_models, ping

MONO = ("Menlo", 12)
MONO_SMALL = ("Menlo", 11)

COLORS = {
    "doc_bg": "#141a22",
    "doc_fg": "#d8dee9",
    "gutter_fg": "#4c566a",
    "window": "#1f3a5f",   # Chunker liest gerade
    "chunked": "#26303d",  # Block akzeptiert
    "current": "#7a4a00",  # Formatter arbeitet gerade
    "done": "#1c3a28",     # Block fertig
    "think": "#7a8494",
    "sep": "#5fb4d9",
}


def load_config(path: str = "config.json") -> dict:
    cfg_path = Path(__file__).parent / path
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


class App(ctk.CTk):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.title("norma-miner · Text → LLM-freundliches Markdown")
        self.geometry("1480x900")
        ctk.set_appearance_mode("dark")

        self.events: queue.Queue[dict] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.input_path: Path | None = None
        self.run_started: float | None = None
        self.total_lines = 0

        self._build_header()
        self._build_body()
        self._build_statusbar()
        self._check_ollama()
        self.after(50, self._pump)

    # ------------------------------------------------------------- Aufbau

    def _build_header(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.pack(fill="x", padx=0, pady=0)

        self.open_btn = ctk.CTkButton(bar, text="📄 Datei öffnen…", width=140,
                                      command=self.open_file)
        self.open_btn.pack(side="left", padx=(12, 6), pady=10)

        self.file_label = ctk.CTkLabel(bar, text="keine Datei gewählt",
                                       text_color="#8892a4")
        self.file_label.pack(side="left", padx=6)

        # side="right" packt von rechts nach links: zuerst Gepacktes landet
        # ganz rechts. Widgets vor ihrem Label packen, damit die Beschriftung
        # links vor dem zugehoerigen Element steht.
        self.stop_btn = ctk.CTkButton(bar, text="■ Stop", width=90,
                                      fg_color="#8b3a3a", hover_color="#733030",
                                      state="disabled", command=self.stop)
        self.stop_btn.pack(side="right", padx=(6, 12), pady=10)
        self.start_btn = ctk.CTkButton(bar, text="▶ Start", width=90,
                                       fg_color="#2d7d46", hover_color="#256a3b",
                                       state="disabled", command=self.start)
        self.start_btn.pack(side="right", padx=6)

        models = list_models(self.cfg["ollama_url"]) or [
            self.cfg["chunker"]["model"], self.cfg["formatter"]["model"]]

        self.lang_entry = ctk.CTkEntry(bar, width=90)
        self.lang_entry.insert(0, self.cfg.get("language", "Deutsch"))
        self.lang_entry.pack(side="right", padx=(4, 18))
        ctk.CTkLabel(bar, text="Sprache").pack(side="right")

        self.formatter_menu = ctk.CTkOptionMenu(bar, values=models, width=200)
        self.formatter_menu.set(self.cfg["formatter"]["model"])
        self.formatter_menu.pack(side="right", padx=(4, 18))
        ctk.CTkLabel(bar, text="Formatter").pack(side="right")

        self.chunker_menu = ctk.CTkOptionMenu(bar, values=models, width=200)
        self.chunker_menu.set(self.cfg["chunker"]["model"])
        self.chunker_menu.pack(side="right", padx=(4, 18))
        ctk.CTkLabel(bar, text="Chunker").pack(side="right", padx=(12, 0))

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        body.grid_columnconfigure(0, weight=11)
        body.grid_columnconfigure(1, weight=9)
        body.grid_rowconfigure(0, weight=1)

        # -- links: Dokument mit Zeilennummern und Highlights
        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(left, text="Dokument", font=ctk.CTkFont(weight="bold")
                     ).pack(anchor="w", padx=10, pady=(8, 2))
        doc_wrap = tk.Frame(left, bg=COLORS["doc_bg"])
        doc_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.gutter = tk.Text(doc_wrap, width=5, padx=4, takefocus=0,
                              bd=0, bg=COLORS["doc_bg"], fg=COLORS["gutter_fg"],
                              font=MONO_SMALL, state="disabled", wrap="none")
        self.gutter.pack(side="left", fill="y")

        self.doc_scroll = tk.Scrollbar(doc_wrap, command=self._scroll_doc)
        self.doc_scroll.pack(side="right", fill="y")

        self.doc = tk.Text(doc_wrap, bd=0, padx=8, pady=4, wrap="none",
                           bg=COLORS["doc_bg"], fg=COLORS["doc_fg"],
                           insertbackground=COLORS["doc_fg"], font=MONO,
                           state="disabled",
                           yscrollcommand=self._on_doc_yscroll)
        self.doc.pack(side="left", fill="both", expand=True)
        for tag in ("window", "chunked", "current", "done"):
            self.doc.tag_config(tag, background=COLORS[tag])
        self.doc.tag_raise("current")
        self.gutter.bind("<MouseWheel>",
                         lambda e: self._scroll_doc("scroll", -e.delta, "units"))

        # -- rechts: Kontext-Panel + Stream
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_rowconfigure(0, weight=2)
        right.grid_rowconfigure(1, weight=3)
        right.grid_columnconfigure(0, weight=1)

        ctx = ctk.CTkFrame(right)
        ctx.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        head = ctk.CTkFrame(ctx, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(head, text="Modell-Kontext",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.ctx_label = ctk.CTkLabel(head, text="–", text_color="#8892a4")
        self.ctx_label.pack(side="right")
        self.ctx_bar = ctk.CTkProgressBar(ctx, height=8)
        self.ctx_bar.set(0)
        self.ctx_bar.pack(fill="x", padx=10, pady=(0, 6))
        self.ctx_text = tk.Text(ctx, bd=0, padx=8, pady=4, wrap="word",
                                bg="#10151c", fg="#9aa4b2", font=MONO_SMALL,
                                state="disabled", height=10)
        self.ctx_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        stream = ctk.CTkFrame(right)
        stream.grid(row=1, column=0, sticky="nsew")
        ctk.CTkLabel(stream, text="Generate-Stream",
                     font=ctk.CTkFont(weight="bold")
                     ).pack(anchor="w", padx=10, pady=(8, 2))
        self.stream_text = tk.Text(stream, bd=0, padx=8, pady=4, wrap="word",
                                   bg="#10151c", fg="#d8dee9", font=MONO,
                                   state="disabled")
        self.stream_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.stream_text.tag_config("think", foreground=COLORS["think"],
                                    font=(MONO[0], MONO[1], "italic"))
        self.stream_text.tag_config("sep", foreground=COLORS["sep"],
                                    font=(MONO[0], MONO[1], "bold"))

    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.pack(fill="x", side="bottom", pady=(6, 0))
        self.stage_label = ctk.CTkLabel(bar, text="bereit", width=260,
                                        anchor="w")
        self.stage_label.pack(side="left", padx=12, pady=6)
        self.elapsed_label = ctk.CTkLabel(bar, text="", width=90, anchor="e")
        self.elapsed_label.pack(side="right", padx=12)
        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=6)

    # -------------------------------------------------------- Scroll-Sync

    def _scroll_doc(self, *args) -> None:
        self.doc.yview(*args)
        self.gutter.yview_moveto(self.doc.yview()[0])

    def _on_doc_yscroll(self, first: str, last: str) -> None:
        self.doc_scroll.set(first, last)
        self.gutter.yview_moveto(first)

    # ------------------------------------------------------------ Aktionen

    def _check_ollama(self) -> None:
        if not ping(self.cfg["ollama_url"]):
            self.stage_label.configure(
                text=f"⚠ Ollama nicht erreichbar ({self.cfg['ollama_url']})",
                text_color="#e0a35a")

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Dokument wählen",
            filetypes=[("Dokumente", " ".join(f"*{s}" for s in SUPPORTED_SUFFIXES))])
        if not path:
            return
        self.input_path = Path(path)
        self.file_label.configure(text=self.input_path.name,
                                  text_color="#d8dee9")
        self.start_btn.configure(state="normal")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        cfg = json.loads(json.dumps(self.cfg))  # tiefe Kopie
        cfg["chunker"]["model"] = self.chunker_menu.get()
        cfg["formatter"]["model"] = self.formatter_menu.get()
        cfg["language"] = self.lang_entry.get().strip() or "Deutsch"

        self.stop_event.clear()
        self._reset_views()
        self.run_started = time.monotonic()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.open_btn.configure(state="disabled")

        self.worker = threading.Thread(
            target=pipeline.run,
            args=(self.input_path, cfg, self.events.put, self.stop_event),
            daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.stage_label.configure(text="Abbruch angefordert…")

    def _reset_views(self) -> None:
        for widget in (self.doc, self.gutter, self.stream_text, self.ctx_text):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")
        self.progress.set(0)
        self.ctx_bar.set(0)

    def _finish(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.open_btn.configure(state="normal")
        self.run_started = None

    # ---------------------------------------------------------- Event-Pump

    def _pump(self) -> None:
        try:
            for _ in range(400):
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.run_started is not None:
            secs = int(time.monotonic() - self.run_started)
            self.elapsed_label.configure(text=f"{secs // 60:02d}:{secs % 60:02d}")
        self.after(50, self._pump)

    def _handle(self, ev: dict) -> None:
        kind = ev["type"]
        if kind == "doc_loaded":
            self._show_document(ev["lines"])
        elif kind == "stage":
            names = {"chunking": "Stufe 1/2 · semantisches Chunking",
                     "formatting": "Stufe 2/2 · Markdown-Formatierung"}
            self.stage_label.configure(text=names.get(ev["stage"], ev["stage"]),
                                       text_color="#d8dee9")
        elif kind == "window":
            self._retag("window", ev["start"], ev["end"], see=True)
        elif kind == "chunk":
            self.doc.tag_add("chunked", f"{ev['start']}.0", f"{ev['end'] + 1}.0")
        elif kind == "format_current":
            self.doc.tag_remove("window", "1.0", "end")
            self._retag("current", ev["start"], ev["end"], see=True)
        elif kind == "chunk_done":
            self.doc.tag_add("done", f"{ev['start']}.0", f"{ev['end'] + 1}.0")
            self.doc.tag_remove("current", "1.0", "end")
        elif kind == "context":
            fill = min(ev["tokens_est"] / ev["num_ctx"], 1.0)
            self.ctx_label.configure(
                text=f"{ev['model']} · ~{ev['tokens_est']}/{ev['num_ctx']} Token")
            self.ctx_bar.set(fill)
            self._set_text(self.ctx_text, ev["prompt"])
        elif kind == "stream_reset":
            self._append_stream(f"\n\n━━ {ev['label']} · {ev['model']} ━━\n",
                                "sep")
        elif kind == "stream":
            self._append_stream(ev["text"],
                                "think" if ev["channel"] == "thinking" else None)
        elif kind == "progress":
            self.progress.set(ev["value"])
            self.stage_label.configure(text=ev["label"])
        elif kind == "done":
            self.progress.set(1)
            mins = ev["seconds"] / 60
            self.stage_label.configure(
                text=f"✔ fertig: {ev['output']} · {ev['chunks']} Blöcke · "
                     f"{mins:.1f} min", text_color="#7bc98a")
            self._finish()
        elif kind == "aborted":
            self.stage_label.configure(text="■ abgebrochen",
                                       text_color="#e0a35a")
            self._finish()
        elif kind == "error":
            self.stage_label.configure(text=f"✖ Fehler: {ev['message']}",
                                       text_color="#e07a7a")
            self._finish()

    # ------------------------------------------------------------- Helfer

    def _show_document(self, lines: list[str]) -> None:
        self.total_lines = len(lines)
        self.doc.configure(state="normal")
        self.doc.insert("1.0", "\n".join(lines))
        self.doc.configure(state="disabled")
        self.gutter.configure(state="normal")
        self.gutter.insert("1.0", "\n".join(f"{i:>4}"
                                            for i in range(1, len(lines) + 1)))
        self.gutter.configure(state="disabled")

    def _retag(self, tag: str, start: int, end: int, see: bool = False) -> None:
        self.doc.tag_remove(tag, "1.0", "end")
        self.doc.tag_add(tag, f"{start}.0", f"{end + 1}.0")
        if see:
            self.doc.see(f"{max(start - 3, 1)}.0")
            self.doc.see(f"{min(end + 3, self.total_lines)}.0")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _append_stream(self, text: str, tag: str | None) -> None:
        self.stream_text.configure(state="normal")
        self.stream_text.insert("end", text, tag or ())
        # Stream-Ansicht begrenzen, damit die GUI fluessig bleibt
        if int(self.stream_text.index("end-1c").split(".")[0]) > 5000:
            self.stream_text.delete("1.0", "1000.0")
        self.stream_text.see("end")
        self.stream_text.configure(state="disabled")


def main() -> None:
    App(load_config()).mainloop()


if __name__ == "__main__":
    main()

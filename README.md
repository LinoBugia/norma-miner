# norma-miner

**Beliebigen Text (PDF/TXT/MD) mit lokalen LLMs in schönes, informationsreiches, RAG-/LLM-freundliches Markdown verwandeln — mit Live-Monitor-GUI.**

Zwei-Stufen-Pipeline, komplett lokal über [Ollama](https://ollama.com):

```
 PDF / TXT / MD
      │
      ▼
 ┌─────────────────────────────┐
 │ Extraktion & Normalisierung │  Zeilen nummerieren (Row 1, Row 2, …)
 └─────────────────────────────┘
      │
      ▼
 ┌─────────────────────────────┐   Stufe 1 · Thinking-Modell (z. B. deepseek-r1)
 │ Semantischer Chunker        │   bekommt Fenster nummerierter Zeilen und
 │ "Row 1-24" / "Row 25-64" …  │   antwortet NUR mit semantischen Blockgrenzen.
 └─────────────────────────────┘   Fenster wandert, bis das Dokument durch ist.
      │
      ▼
 ┌─────────────────────────────┐   Stufe 2 · starkes Textmodell (z. B. qwen2.5)
 │ Markdown-Formatter          │   verwandelt jeden Block in strukturiertes,
 │ Block für Block             │   selbsterklärendes Markdown.
 └─────────────────────────────┘
      │
      ▼
 output/<name>.md   (Blöcke durch `---` getrennt → direkt RAG-chunkbar)
```

## Features

- **Row-Protokoll-Chunking**: Das Thinking-Modell sieht nummerierte Zeilen und gibt nach dem Denken ausschließlich `Row A-B`-Bereiche aus. Antworten werden geparst, repariert (lückenlos, überlappungsfrei) und zu große Blöcke an Leerzeilen nachgeteilt. Bei unbrauchbarer Antwort greift ein Fallback — die Pipeline bleibt nie hängen.
- **Token-Limit-sicher**: Der Chunker arbeitet fensterweise (`window_lines`) durchs Dokument; der letzte, evtl. angeschnittene Block eines Fensters wird im nächsten Fenster neu bewertet.
- **Monitor-GUI (CustomTkinter)**: Links das Dokument — das Chunker-Fenster (blau), der gerade formatierte Block (orange) und fertige Blöcke (grün) wandern live durchs Dokument. Rechts oben der **Modell-Kontext** (kompletter Prompt + Kontext-Füllstand), rechts unten der **Generate-Stream** inkl. Thinking (grau kursiv).
- **RAG-/LLM-freundlicher Output**: Jeder Block ist eigenständig verständlich (Pronomen aufgelöst, Kontext mitgegeben), faktentreu, mit Überschriften, Fettungen, Listen und Tabellen. Blöcke sind durch `---` getrennt — das passt direkt zum `marker: "---"`-Chunking des text-embedder-Projekts.
- **Struktur-Kontext für den Formatter**: Beim Formatieren sieht das Modell immer (a) den **zuletzt formatierten Block** (klar als „Text davor" deklariert, Umfang via `prev_block_chars`) und (b) die **komplette bisherige Kapitel-Outline** (`#` / `##` / `###` mit aktueller Position). So laufen Überschriften-Hierarchie, Ton und Terminologie konsistent über Blockgrenzen weiter. Wird die Outline zu lang, wird sie automatisch auf `#`/`##` reduziert — der Prompt bleibt sicher unter `num_ctx`.
- **Sequentielle Stufen**: Erst chunkt Stufe 1 das ganze Dokument, dann formatiert Stufe 2 — jedes Modell wird nur einmal geladen (wichtig bei 24 GB RAM, kein Modell-Thrashing).
- **Sprache & Modelle konfigurierbar** über `config.json` oder direkt in der GUI-Kopfzeile.
- **Abbruchsicher**: Der Output wird nach jedem Block geschrieben; Stop erhält den Fortschritt.

## Installation

```bash
cd norma-miner
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Ollama + Modelle (Empfehlung für 24 GB RAM):
ollama pull deepseek-r1:14b    # Chunker (Thinking)
ollama pull qwen2.5:14b       # Formatter
```

### Modell-Empfehlungen für 24 GB RAM

| Rolle      | Empfehlung           | Alternative                        |
|------------|----------------------|------------------------------------|
| Chunker    | `deepseek-r1:14b`    | `qwen3:14b`, `deepseek-r1:8b` (schneller) |
| Formatter  | `qwen2.5:14b`        | `gemma3:12b`, `llama3.1:8b` (schneller)   |

Beide 14b-Modelle (q4) belegen je ~9–10 GB — da die Stufen nacheinander laufen, ist immer nur eines geladen.

## Benutzung

**GUI (empfohlen):**

```bash
python main.py          # oder: python app.py
```

1. `📄 Datei öffnen…` → PDF/TXT/MD wählen
2. Optional Modelle/Sprache in der Kopfzeile anpassen
3. `▶ Start` — zuschauen, wie das Highlight durchs Dokument wandert
4. Ergebnis: `output/<name>.md`

**Headless/CLI:**

```bash
python main.py --cli dokument.pdf
```

## Konfiguration (`config.json`)

```jsonc
{
  "ollama_url": "http://localhost:11434",
  "language": "Deutsch",          // Zielsprache des Markdown-Outputs
  "output_dir": "./output",
  "wrap_width": 100,              // Zeilenumbruch vor der Row-Nummerierung
  "chunker": {
    "model": "deepseek-r1:14b",
    "think": true,                // natives Thinking anfordern (Fallback: <think>-Tags werden gefiltert)
    "num_ctx": 8192,              // Kontextfenster
    "temperature": 0.2,
    "window_lines": 80,           // Zeilen pro Chunker-Fenster (Token-Limit!)
    "min_chunk_lines": 4,
    "max_chunk_lines": 40
  },
  "formatter": {
    "model": "qwen2.5:14b",
    "num_ctx": 8192,
    "temperature": 0.4,
    "num_predict": 4096,          // max. Output-Tokens pro Block
    "prev_block_chars": 4000      // wie viel vom vorherigen Markdown-Block als Kontext mitgegeben wird
  }
}
```

**Faustregel für `window_lines`:** Bei `wrap_width: 100` sind 80 Zeilen ≈ 2 500 Token — bleibt mit Prompt komfortabel unter `num_ctx: 8192`. Größeres Kontextfenster? `window_lines` hochdrehen, dann sieht der Chunker mehr Zusammenhang.

## Projektstruktur

| Datei              | Zweck                                                        |
|--------------------|--------------------------------------------------------------|
| `main.py`          | Einstieg: GUI oder `--cli`                                   |
| `app.py`           | CustomTkinter-Monitor (Dokument-Highlight, Kontext, Stream)  |
| `pipeline.py`      | Orchestrierung der zwei Stufen, Event-System, Datei-Output   |
| `chunker.py`       | Row-Protokoll: Prompt, Parsing, Reparatur, Fallback          |
| `formatter.py`     | Markdown-Prompt (faktentreu, RAG-freundlich, Zielsprache)    |
| `ollama_client.py` | Streaming-Client, Thinking-Handling (`think` + `<think>`-Tags) |
| `extract.py`       | PDF/TXT/MD → normalisierte Zeilen (pymupdf, Fallback pypdf)  |
| `config.json`      | Modelle, Sprache, Fenster- und Blockgrößen                   |

## Troubleshooting

- **„Ollama nicht erreichbar“** → `ollama serve` läuft? URL in `config.json` korrekt?
- **Chunker liefert Unsinn** → kleineres Fenster (`window_lines`), Temperatur senken, oder stärkeres Thinking-Modell. Notfalls greift ohnehin der Leerzeilen-Fallback.
- **Sehr langsam** → kleinere Modelle (8b/12b) wählen; Thinking-Modelle brauchen für das Denken naturgemäß am längsten.
- **PDF liefert Zeichensalat** → gescanntes PDF ohne Textlayer; vorher OCR (z. B. `ocrmypdf`) laufen lassen.

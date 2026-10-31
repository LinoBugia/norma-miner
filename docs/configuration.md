# Configuration

Every setting in `config.json`, where overrides come from, and how to size
the window against the context limit. This file is the reference for
defaults — other documents link here rather than repeating them.

- [Where settings come from](#where-settings-come-from)
- [Top level](#top-level)
- [`chunker`](#chunker)
- [`formatter`](#formatter)
- [Sizing the window](#sizing-the-window)
- [Input and output paths](#input-and-output-paths)

## Where settings come from

`config.json` next to `main.py` holds the defaults. Two things can
override them for a single run:

| Source | Overrides | Persists? |
|---|---|---|
| GUI header fields | Chunker model, formatter model, window, language | No |
| `--window N` | `chunker.window_lines` (minimum 10) | No |
| `--config path.json` | The whole file | — |

Neither the GUI nor the CLI writes back to `config.json`, so experimenting
in the interface never changes your defaults. A non-numeric value in the
GUI's window field falls back to 100 rather than failing the run.

Every key listed below ships in `config.json`. Deleting one falls back to
a built-in default in the code, which is not guaranteed to match the
shipped value — keep the keys and edit their values.

## Top level

| Key | Default | Meaning |
|---|---|---|
| `ollama_url` | `http://localhost:11434` | Base URL of the Ollama server. Everything runs against this one endpoint |
| `language` | `Deutsch` | Target language of the output, free-form. `English`, `Englisch` and `Deutsch` all work. Triggers translation when the source differs |
| `output_dir` | `./output` | Where the finished `.md` files are written; created if missing |
| `wrap_width` | `100` | Long lines are wrapped to this width before numbering, so one row is a predictable unit |

`wrap_width` is the least obvious of these. It does not affect the output
text at all — it only decides how much content one "row" holds, and
therefore how much a window of *n* rows actually contains. Changing it
changes the meaning of every line-based setting below.

## `chunker`

Stage 1, the model that decides where blocks begin and end. See
[chunking.md](chunking.md) for the mechanism.

| Key | Default | Meaning |
|---|---|---|
| `model` | `deepseek-r1:14b` | A reasoning model; see [models.md](models.md) |
| `think` | `true` | Request native thinking. Falls back automatically for models or Ollama versions without it |
| `num_ctx` | `8192` | Context window handed to Ollama |
| `temperature` | `0.6` | DeepSeek's own range for R1 (0.5–0.7); see the note below |
| `window_lines` | `100` | Rows per pass. Also settable in the GUI and via `--window` |
| `min_chunk_lines` | `10` | Lower end of the target block size (a guideline for the model) |
| `max_chunk_lines` | `50` | Upper end of the target size — and a hard limit: longer ranges are split mechanically afterwards |
| `max_overlap_lines` | `3` | How far consecutive blocks may overlap; `0` disables overlap |

A low temperature looks like the safe choice for a model that only has to
pick boundaries, and this was set to `0.2` at first. DeepSeek advise
0.5–0.7 for R1 and warn that lower values cause repetition and incoherent
output; Together AI put it more bluntly — lower temperatures "collapse the
reasoning trace". Since the chunker's entire job happens inside that
reasoning trace, starving it is the opposite of what the setting appears
to do.

## `formatter`

Stage 2, the model that writes the Markdown. See
[formatting.md](formatting.md).

| Key | Default | Meaning |
|---|---|---|
| `model` | `qwen2.5:14b` | A strong text model; see [models.md](models.md) |
| `think` | `false` | No reasoning phase needed here, and it would double the runtime |
| `num_ctx` | `8192` | Context window handed to Ollama |
| `temperature` | `0.4` | Higher than the chunker's — this stage writes prose |
| `num_predict` | `4096` | Maximum output tokens per block. Raise it if long blocks look truncated |
| `prev_block_chars` | `4000` | How much of the previous block is passed as context (~1 100 tokens) |

## Sizing the window

`window_lines` is the setting most worth tuning, because it trades context
against reliability. Measured prompt sizes for a 282-line German prose
document at `wrap_width: 100`, using the estimator the GUI displays:

| `window_lines` | Prompt | Share of `num_ctx: 8192` |
|---:|---:|---:|
| 60 | ~2 644 tokens | 32 % |
| 100 | ~3 487 tokens | 43 % |
| 150 | ~4 388 tokens | 54 % |
| 200 | ~5 277 tokens | 64 % |

The default leaves well over half the context free, which is deliberate:
the reasoning model needs room to *think* inside the same window, and
thinking is what gets truncated first when the budget runs short. If
chunking becomes unreliable — short lists, ignored instructions — reduce
`window_lines` before anything else.

The formatter's prompt is bounded by construction. With a
`max_chunk_lines`-sized block, a full `prev_block_chars` of previous
output and a long outline, it measures ~3 300 tokens, and the outline
collapses to top-level headings beyond 3 000 characters so it cannot grow
without limit.

Both numbers come from the same rough estimator (~3.5 characters per
token) that feeds the GUI's context bar, so what you see while a run is
going is directly comparable.

## Input and output paths

There is no input directory. A run always takes one file, chosen in the
GUI dialog or passed to `--cli`. Accepted suffixes are `.txt` and `.md`.

PDFs are rejected with a pointer to
[img-text-extractor](https://github.com/LinoBugia/img-text-extractor),
which OCRs scanned pages and has a vision model describe each figure in
place. Extracting a PDF's text layer here instead would be a weaker
duplicate of that tool, and the loss — every chart and table that exists
only as an image — would be invisible in the result.

The result is written to `<output_dir>/<input filename without
suffix>.md`, overwriting an existing file of that name, and rewritten
after every finished block.

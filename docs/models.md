# Models

Which two models to run, why they have different jobs, and how they fit
into a fixed amount of RAM. Settings live in
[configuration.md](configuration.md).

- [Two roles, two models](#two-roles-two-models)
- [Recommendations](#recommendations)
- [Why the stages run sequentially](#why-the-stages-run-sequentially)
- [Thinking support](#thinking-support)
- [Ollama setup](#ollama-setup)

## Two roles, two models

The two stages want opposite qualities, which is why they are configured
separately rather than sharing one model.

| | Chunker (stage 1) | Formatter (stage 2) |
|---|---|---|
| Job | Decide where topics end | Rewrite a block as dense Markdown |
| Output | A few lines of row ranges | Several hundred words |
| Wants | Reasoning depth | Writing quality, instruction following |
| Reasoning | On — the boundaries are the whole output | Off — it would only slow things down |
| Temperature | In the vendor's recommended range for R1 | Moderate, this stage writes prose |

A reasoning model is worth its slowness in stage 1 because the whole
output is a handful of numbers, and getting those numbers right decides
the quality of everything downstream. In stage 2 the same model would
spend minutes thinking per block and write no better.

## Recommendations

Measured sizes as reported by Ollama on the machine this was developed on
(24 GB RAM, Apple Silicon):

| Role | Recommendation | Size | Alternatives |
|---|---|---:|---|
| Chunker | `deepseek-r1:14b` (Q4_K_M) | 9.0 GB | `deepseek-r1:32b` (19.9 GB) if you have the RAM; `deepseek-r1:8b` for speed |
| Formatter | `qwen2.5:14b` (Q4_K_M) | 9.0 GB | `qwen3.5:27b` (17.4 GB), `gpt-oss:20b` (13.8 GB); `llama3.1:8b` (4.9 GB) for speed |

The formatter is where model strength pays off most visibly. Compression
without fact loss and consistent adherence to a target language are both
instruction-following problems, and small models degrade on them first —
they translate sentence by sentence instead of condensing, or drift back
into the source language. If output quality disappoints, replace the
formatter before tuning prompts or settings.

> Model names and sizes here reflect what was installed in September 2026.
> Check `ollama list` for what you actually have.

## Why the stages run sequentially

Stage 1 chunks the entire document before stage 2 starts. That looks
wasteful — the first block could be formatted while the chunker works on
the second window — but it is what makes the default pair fit.

Two 14b models at Q4 are 9.0 GB each. Loaded together they occupy 18 GB of
24 GB, leaving little for the OS and the context windows, and Ollama would
evict and reload them repeatedly as the pipeline alternated between
stages. Running the stages one after another means only one model is
resident at a time, so a single 9 GB load happens per stage.

The visible cost is that the progress bar spends its first half without
producing any output. The invisible benefit is that a long document does
not thrash.

## Thinking support

Ollama exposes reasoning models in two different ways depending on
version, and the client handles both:

1. It first asks for the native `think` field, which newer versions return
   as a separate channel alongside the content.
2. If that request is rejected before any data arrives, it retries without
   the field and instead filters `<think>…</think>` tags out of the content
   stream.

The fallback splitter buffers across streaming chunks, so a tag cut in
half between two packets is still recognised. Either way the GUI receives
thinking and content as separate channels and can render reasoning in grey
italics.

Only the chunker requests thinking (see
[configuration.md](configuration.md#chunker)). The formatter works fine
without it, and enabling it roughly doubles the time per block.

## Ollama setup

```bash
brew install ollama            # or https://ollama.com/download
ollama serve
ollama pull deepseek-r1:14b    # chunker, 9.0 GB
ollama pull qwen2.5:14b        # formatter, 9.0 GB
```

The GUI populates both model dropdowns from `/api/tags` at startup, so any
model you have pulled is selectable without editing the config. If Ollama
is unreachable the dropdowns fall back to the configured names and the
status bar shows a warning — the app still starts, it just cannot run.

Runtime scales with model size and document length. A 32-line German prose
document (8 KB) took 5.1 minutes end to end with the recommended pair on
the development machine; most of that is stage 1's reasoning.

# Formatting

How stage 2 turns a raw block of lines into a self-contained Markdown
block, and what context it gets to keep the document coherent. Settings
and defaults live in [configuration.md](configuration.md).

- [Compression without loss](#compression-without-loss)
- [The context a block sees](#the-context-a-block-sees)
- [Language and translation](#language-and-translation)
- [Handling the overlap](#handling-the-overlap)
- [Around the model](#around-the-model)

## Compression without loss

The model is instructed as a *knowledge editor building a knowledge base*,
not as a writer retelling prose. The output must be measurably shorter and
denser than the source while carrying every fact.

The failure mode this fights is specific and common: asked to "turn this
into nice Markdown", a mid-sized model translates sentence by sentence and
adds a heading. The result is the same text with different punctuation —
no denser, and now a second copy that can drift from the original. The
prompt names that behaviour explicitly as wrong and prescribes the
alternative: strip filler, redundancy and rhetorical flourish, merge
related statements, and prefer dense structures — `**term:** fact` bullets,
tables for anything enumerable — with flowing prose reserved for causal
chains that genuinely need it.

Pushing for density creates the opposite risk: a model that compresses
aggressively starts dropping entities or attaching numbers to the wrong
one. The counterweight is a rule stated in those terms —

> Compression means deleting WORDS, never deleting FACTS.

— followed by an instruction to verify every entity and every number
against the source before finishing.

> This remains a model-quality question, not a guarantee. Small models
> under a compression instruction do occasionally drop an entity or
> misattribute a figure. If fidelity matters more than density, use a
> stronger formatter model ([models.md](models.md)) before tuning
> anything else.

## The context a block sees

A block formatted in isolation would restart the document: repeated
headings, drifting terminology, dangling pronouns. Each call therefore
carries three pieces of context besides the raw text.

| Context | What it is | What it prevents |
|---|---|---|
| Position | `Block 7 of 23` and the document title | Treating a mid-document block as an opening |
| Outline | Every heading written so far, indented by level | Repeating a heading; picking the wrong level |
| Previous block | The last block's finished Markdown, capped by `prev_block_chars` | Tone and terminology drift; rewriting the overlap |

The outline is accumulated from the finished Markdown, not from the plan:
after each block the pipeline extracts its `#`–`####` headings and appends
them. So the model always sees what was actually written.

Both context pieces are bounded, because they grow with the document while
`num_ctx` does not. The previous block is truncated to its last
`prev_block_chars` characters. The outline is rendered fully while it fits
in 3000 characters and collapses to `#` and `##` levels beyond that,
keeping the chapter skeleton and dropping the detail.

The wording matters as much as the content. An early version framed the
outline as "you are working directly below the last heading", and the
model read that as a template: it copied the last heading into every
block, producing five consecutive sections with the same title. Declaring
the outline as *already written, do not repeat* and asking for a new
heading fixed it.

## Language and translation

`language` is free-form and goes straight into the prompt, so `English`,
`Englisch` and `Deutsch` all work.

If the source is in another language, translating is mandatory —
headings, body text, table headers and list items included. The rule is
stated that way because a partial translation is the natural failure
mode: a model given a German source and an English target tends to
translate the prose and leave headings, bold terms and list labels in the
original. Proper names, titles of works and established technical terms
may stay; a parenthetical gloss is allowed where it helps.

The instruction appears twice — once in the system prompt and once
directly above the raw text — because a constraint placed next to the
content it applies to is followed far more reliably than one buried in a
long rule list.

### Known limitation: context outranks the language setting

With `qwen2.5:14b` the target language holds only as long as nothing in
the prompt contradicts it. Measured on a German source with
`language: English`:

| Context given to the block | Output language |
|---|---|
| None (first block) | English |
| Previous block and outline in German | **German** |
| Previous block and outline in English | English |

The mechanism is the feedback loop in
[the context a block sees](#the-context-a-block-sees): the model is told
to align tone and terminology with the previous block, and it reads the
previous block's *language* as part of that. Once one block comes out in
the source language, its headings enter the outline and its text becomes
the next block's context, so every following block inherits the error.

In practice that means a cross-language run is reliable only if the first
block lands in the target language. Until the prompt separates "align
terminology" from "align language", treat the language setting as
dependable for same-language runs and verify cross-language ones.

## Handling the overlap

When stage 1 lets two blocks share lines ([chunking.md](chunking.md)), the
formatter sees the repeated passage twice: once at the end of the previous
block, once at the start of its own raw text. It is told not to write that
content again but to use it as a transition and continue from there. This
is why the overlap costs nothing in the output while still preventing a
cut mid-sentence.

## Around the model

The code does three things the prompt cannot guarantee:

- **Fence stripping.** A whole answer wrapped in ```` ```markdown ```` is
  unwrapped, since that wrapper would otherwise end up inside the document.
- **Outline tracking.** Headings are parsed out of each finished block and
  carried forward, as described above.
- **Incremental writing.** The output file is rewritten after *every*
  block. A stopped or crashed run therefore leaves a valid, shorter
  document rather than nothing — which matters when a long document takes
  tens of minutes.

# Chunking

How stage 1 decides where a document is cut, and why the rules are what
they are. Settings and defaults live in
[configuration.md](configuration.md).

- [The row protocol](#the-row-protocol)
- [Window-local numbering](#window-local-numbering)
- [The moving window and its uncovered tail](#the-moving-window-and-its-uncovered-tail)
- [Overlapping blocks](#overlapping-blocks)
- [Block size is a guideline](#block-size-is-a-guideline)
- [Repair and fallback](#repair-and-fallback)
- [The thinking budget](#the-thinking-budget)

## The row protocol

The chunker never writes prose. It receives an excerpt of the document as
numbered lines and answers with nothing but line ranges:

```
Row 1: TIEFSEEFORSCHUNG: DIE LETZTEN GRENZEN DER ERDE      ->   Row 1-8
Row 2:                                                          Row 9-15
Row 3: Die Tiefsee ist einer der letzten großen Orte …          Row 17-22
…                                                               …
```

The reason for this indirection is cost and trust. A reasoning model is
good at judging where a topic ends but slow and unreliable at reproducing
text verbatim — asking it to emit the document back would risk silent
edits and multiply the runtime. Line ranges are cheap to generate, cheap
to verify, and impossible to hallucinate content into: whatever the model
answers, the text that ends up in a block is read from the source file by
index.

Before any of this, `extract.py` normalizes the document into lines —
wrapping long ones at `wrap_width` and collapsing runs of blank lines into
a single one. A "row" is therefore a predictable unit of roughly one
wrapped line, which is what makes the ranges meaningful. Input is `.txt`
or `.md`; see [configuration.md](configuration.md#input-and-output-paths)
for why PDFs are handled by a separate tool.

## Window-local numbering

Every window is presented to the model as `Row 1` … `Row n`, even a window
that begins at document line 1041. The caller maps the answer back onto
real document lines with the window offset.

This exists because absolute numbers broke the model's arithmetic. With a
window spanning rows 65–108, reasoning traces regularly derailed into
bookkeeping:

> "2+8+5+6+7+4+7=43. But the excerpt is from 65 to 108, which is 44 rows.
> So I'm missing one row."

The model then spent its remaining budget re-deriving offsets instead of
judging topic boundaries, and often truncated the list. Small numbers that
always start at 1 remove the arithmetic entirely, and the final
self-check ("does your list end at row n?") becomes trivial.

## The moving window and its uncovered tail

The window is blind to what follows it, so the topic running at its last
line is almost always incomplete. A non-final window is therefore
instructed to **deliberately leave its tail uncovered** and end the last
block at a boundary the model is confident about. The pipeline then
resumes at the line directly after that block, so the next window sees the
unclassified tail *together with its continuation* and can judge it
properly.

This is a required empty region, not a safety margin — the prompt says so
explicitly, because a model asked merely to "stop a bit early" will still
try to cover everything.

Two guards sit behind the instruction:

- If the model spans to the window edge anyway, the pipeline drops its
  last block and re-evaluates those lines in the next window (only when
  more than one block came back — otherwise there would be no progress).
- Starts and ends are forced to ascend strictly, so the cursor always
  advances and the loop cannot stall.

The **final** window, the one reaching the end of the document, is the
exception: there full coverage through the last line is mandatory,
otherwise the document's ending would be silently dropped.

## Overlapping blocks

When a semantic boundary falls *inside* a line — two topics sharing one
row — the next block may start on that shared row, so it appears in both
blocks and neither is left with half a sentence. `max_overlap_lines` caps
how far this may go; `0` disables it.

Overlap applies **between blocks inside one window**. Across windows the
cursor moves past the last accepted block, so windows never overlap.

Duplication is acceptable here because stage 2 sees the previous block
while formatting the next one (see [formatting.md](formatting.md)) and is
instructed to use the repeated passage as a transition rather than writing
it twice.

## Block size is a guideline

`min_chunk_lines` and `max_chunk_lines` are phrased to the model as a
target range, explicitly not a hard rule. It is told to go shorter when a
self-contained unit — a heading, a definition, a short Q&A — ends
naturally, and longer when splitting would cut a single argument in half.

The reasoning is that these blocks become retrieval units. A chunk that is
the right size but starts mid-thought is worse than an oversized one that
stands on its own, because the retriever returns it without its
neighbours.

The width of that range matters more than it looks. Measured on a dense
document, an earlier `4`–`50` target let the model fragment a window into
fourteen blocks, six of them at exactly the four-row minimum — technically
inside the range, but far too small to answer anything on their own.
Raising the lower bound to the current value cut that window to six blocks
and removed the fragmentation entirely. The
instruction "never fragment" did not prevent it; a range spanning a factor
of twelve simply carries too little information to steer by. Keep the lower
bound close to the smallest chunk you would actually want returned.

`max_chunk_lines` does act as a hard limit in one place: after the model
has answered, any range longer than it is split mechanically, preferring a
blank line and never cutting before the midpoint of the allowed span.

## Repair and fallback

Model answers are rarely perfect, so several layers run over them before
anything becomes a block:

| Problem | Response |
|---|---|
| Ranges outside the window | Clamped to the window |
| First range does not start at the window start | Forced to start there |
| Gap between two blocks | Closed |
| Overlap larger than allowed | Capped at `max_overlap_lines` |
| Range fully inside an earlier one | Dropped |
| Range longer than `max_chunk_lines` | Split, preferring blank lines |
| Range containing only blank lines | Skipped |
| No parsable range at all | Fallback: fixed-size blocks at blank lines, **reported as a `fallback` event** |

The parser accepts `Row 5-9`, `Rows 5-9`, a bare `Row 5`, a bare `5-9`
without any keyword, and en/em dashes. This tolerance is not cosmetic: a
prompt revision that made the model drop the literal word `rows` and emit
`1-8` turned every window into a fallback, which produced uniform blocks
across a whole document. A bare single number is deliberately *not*
accepted — it would match stray output such as a list marker.

The last row of the table is what keeps the pipeline honest: a window that
produces nothing usable still yields blocks and still advances the cursor.
Quality degrades, the run does not stop.

Because that degradation is invisible in the output — the blocks look
fine, they are just content-blind — the fallback emits an event. The CLI
prints it to stderr and the GUI shows an orange warning in the stream and
status bar. Silent degradation is the worst failure mode this pipeline has,
since a whole document can be processed at reduced quality without anyone
noticing.

## The thinking budget

The prompt tells the model to scan the rows in one pass, note the topic
shifts, and then stop thinking. This is not stylistic advice — the
response has a token limit, and a model that deliberates row by row runs
out of budget *before* it writes the list, losing all of its work. The
instruction spells out the trade-off it should make: a complete list
matters more than a perfect boundary.

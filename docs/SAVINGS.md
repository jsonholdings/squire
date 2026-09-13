# Measuring real savings

This document reports actual numbers from real usage, not a lab benchmark, and labels
every figure **MEASURED** or **ESTIMATED** with the method behind it. All numbers are
aggregates across many real coding-agent sessions on one machine over several days;
no project name, file path, hostname, or identifying detail appears below or in the
script that produced them.

## Why this matters: cache-read dominates the bill

A coding agent's context is re-sent on every turn. A large tool result — a test log, a
long file, a verbose diff — pasted into context on turn 1 is paid for again as a
cache-read on turn 2, 3, 4, and every turn after that until the conversation ends or
compacts. That repetition, not the original paste, is where most of the token cost
in a long session actually sits. Squire's premise is to condense that kind of bulk
text locally before it ever enters context, so there is nothing large to re-read.

## Method

Two independently-labelled layers, computed by [`scripts/squire_report.py`](../scripts/squire_report.py):

1. **MEASURED session totals.** Claude Code stores every session locally as JSONL, and
   every assistant turn carries a real `usage` block (`input_tokens`,
   `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`). Summing
   these across all recorded sessions gives an exact count of what was actually paid
   for — nothing here is inferred.
2. **ESTIMATED savings from squire's own call ledger.** Every squire call logs the real
   character count sent in and returned. `chars_saved = chars_in - chars_out` is itself
   a measured byte count — condensation genuinely happened. Converting that to a token
   estimate (`chars_saved / 4`, the standard rough heuristic, not an actual tokenizer)
   and then multiplying by how many turns remained in that call's own session at or
   after it ran gives an estimate of the cache-read cost avoided — because that avoided
   text would otherwise have sat in context, re-billed, for every one of those turns.
   **Per call, this is an upper bound**: it assumes the avoided text would otherwise
   have stayed in context verbatim for every remaining turn, with no compaction. **Across
   all usage, it is incomplete**: it covers only the calls that could be tied to a session
   (see below), so it says nothing about the rest.

## The numbers

### MEASURED — from 186 real Claude Code sessions

| Metric | Value |
|---|---|
| Turns | 70,352 |
| Cache-read input tokens | 21,766,455,434 |
| Cache-creation input tokens | 316,277,603 |
| Input tokens (uncached) | 792,355 |
| Output tokens | 78,637,498 |

Cache-read tokens outnumber uncached input tokens by roughly **27,500:1** in this
sample. Nearly every token a turn pays for is a re-read of prior context, not new
information — which is exactly the cost this tool targets.

### MEASURED — squire's own condensation, from its call ledger (125 calls)

| Metric | Value |
|---|---|
| Characters sent to the local model | 1,058,249 |
| Characters returned to the agent | 40,825 |
| Characters kept out of context | 1,017,424 |

These are real, counted bytes: 96% of what would otherwise have entered context on
these calls did not.

### ESTIMATED — token and cache-read savings derived from the above

| Metric | Value | Method |
|---|---|---|
| One-time tokens saved | 254,356 | `chars_saved / 4`, counted once |
| Cache-read tokens avoided | 18,688,733 | `chars_saved / 4` × remaining turns in the correlated session |

Of the 125 logged calls, only **48 (38%)** could be correlated to a specific session
(the rest predate session-correlation in the ledger, or ran outside a session). The
cache-read-avoided estimate above is computed only from those 48. It is an upper-bound
estimate for **a minority of squire's real usage**, not a total for all of it.

## Dollar cost

Deliberately omitted here. A dollar figure requires citing a specific model's current
published list price per token and showing the multiplication explicitly; this
document does not do that arithmetic, so it does not assert a number. Anyone
reproducing this locally can multiply the MEASURED cache-read total above by their own
model's current list price to get an honest figure for their own usage.

## Limits, stated plainly

- **No controlled A/B yet.** These are observational numbers from ordinary use, not a
  paired trial (the same task run once with squire and once without, comparing tokens
  and turns to completion). That trial is planned, not done.
- **chars/4 is a rough heuristic**, not a real tokenizer for any specific model — treat
  every ESTIMATED row as directional, not precise.
- **The correlation rate is low (38%).** Most logged calls cannot be tied to a specific
  session's remaining turns, so the cache-read-avoided figure is conservative by
  construction, but it is also not comprehensive — it says nothing about the other 62%
  of calls.
- **Session totals include all recorded activity**, not only turns where squire was
  used — they establish the scale of the cache-read problem this tool targets, not
  squire's own effect on it in isolation.

## Reproduce this on your own machine

```sh
python3 scripts/squire_report.py --json --anonymize
```

This reads only local files (`~/.claude/projects/**/*.jsonl` usage blocks and squire's
own `~/.squire/ledger.jsonl`), prints counts and timestamps only — never transcript
content — and `--anonymize` replaces any project-directory name before it is printed.
Nothing is sent anywhere.

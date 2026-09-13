# Measuring real savings

squire's value claim is a token-savings claim, so it is measured, not asserted. Two numbers, kept
separate on purpose:

- **VERIFIED** — computed directly from Claude Code's own session transcripts. Claude Code stores
  every session as JSONL at `~/.claude/projects/<project>/<session>.jsonl`, and every assistant
  message carries a real `usage` block: `input_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `output_tokens`. Summing these is not an estimate — it's what Claude
  Code itself recorded actually happened.
- **ESTIMATE** — derived from squire's own local ledger (`~/.squire/ledger.jsonl`), which logs
  `chars_in`/`chars_out` per call. Converting chars to tokens uses a fixed chars/4 heuristic (the
  same one `squire stats` already uses and labels), and is never presented as a measured count.

## Running it

```sh
python3 scripts/squire_report.py                     # human-readable table, current machine
python3 scripts/squire_report.py --json               # structured output
python3 scripts/squire_report.py --anonymize           # replaces project dir names with PROJECT-N
python3 scripts/squire_report.py --project <name>      # restrict to one ~/.claude/projects/<name>
```
It never prints message content — only counts, timestamps and command names — so it's safe to run
against real transcripts and share the output.

## What it computes

Per session: turn count, total cache-read/cache-write/input/output tokens, and tokens-per-turn.
`tool_result_share_of_context` is reported as `UNKNOWN` rather than guessed — Claude Code's
`usage` blocks don't break down context by content type, so that figure would need a per-message
role/content-type parse that isn't implemented (a real gap, tracked here rather than glossed over).

Across the squire ledger: total calls, chars in/out, chars saved, and a one-time
`tokens_saved_one_time_ESTIMATE` (chars_saved / 4). The **cache-read multiplier** — chars saved
times the number of remaining turns in the session the call happened in, which is the real
mechanism by which an avoided paste saves tokens repeatedly — is reported as `UNKNOWN`. The ledger
does not currently record which session or turn a call belonged to (squire runs as a separate
process from the Claude Code transcript), so that correlation cannot be computed yet. Closing this
gap would mean adding a session-id field to ledger entries, written by whatever wraps squire calls
inside a session (the planned PostToolUse hook is the natural place for it).

## What was actually measured on this machine (2026-09-12)

**VERIFIED**, one project, 34 sessions, from real `usage` blocks:

| Metric | Value |
|---|---|
| Turns | 70,030 |
| Cache-read tokens | 21,713,327,662 |
| Cache-creation tokens | 315,121,245 |
| Input tokens (uncached) | 784,617 |
| Output tokens | 77,985,694 |

Reproduce with `python3 scripts/squire_report.py --project <that project's dir name>`. Names are
withheld here per the scrub policy; run the command yourself to see your own project names.

**ESTIMATE**, squire's ledger on this machine, 23 calls logged so far:
chars_in=10,893, chars_out=1,552, chars_saved=9,341, tokens_saved_one_time_ESTIMATE=2,335.
Small because the ledger is new — it grows with use, and the multiplier gap above means even this
figure understates the real saving (each avoided paste would otherwise be re-read on every later
turn, not just once).

## What's not yet measured (honest gaps)
- **Controlled A/B** (same scripted tasks with and without squire, N runs each, tokens/turns to
  completion) — spec'd in `SPEC-OPEN-SOURCE.md` "Measuring real savings" §2, not run yet.
- **Session-to-ledger correlation** for the cache-read multiplier, described above.
- **Before/after comparison** of the same project's sessions pre- and post-squire-adoption —
  possible with `squire_report.py --since <date>` once there's enough post-adoption data.

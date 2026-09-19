# Claude token usage analysis — 2026-09-15

Internal analysis. No public claims: the headline savings percentage stays gated behind
`BACKLOG.md`'s `shareable-savings-stat-needs-a-controlled-ab` item until a paired A/B exists.

## Findings, ranked by what would actually change how we work

### 1. The token counter itself was overcounting by ~2.27x — VERIFIED, now fixed

`squire_report.py` summed every JSONL line that carried a `message.usage` block. Claude Code
splits one API response into several JSONL lines when the response has multiple content blocks
(a thinking block, a text block, a tool_use block, ...), and **every line for that response
repeats the identical `usage` dict**, keyed by the same `message.id`. Counting per line instead
of per response inflated every total. A 50-transcript sample: 18,526 usage-bearing lines against
8,162 unique `message.id`s — a 2.27x ratio. `parse_session` now keeps only the first line seen
per `message.id`; synthetic test fixtures (which never set an id) are unaffected. This is fixed
in the tool (`scripts/squire_report.py`, tests added) but **not** re-published to `README.md` /
`docs/SAVINGS.md` — those are out of scope for this change per the brief, and republishing a
lower headline number is a decision, not a mechanical follow-on.

### 2. The two savings-estimate layers disagree by ~140x, and the larger one fails a sanity check

`estimate_ledger_savings`'s Layer 1 (one-time chars-saved/4) gives **~73.0M tokens** across 1,020
model-backed ledger calls. Its Layer 2 (Layer 1's per-call figure multiplied by the count of that
session's remaining turns, modelling cache-read re-payment) gives **~10.46 billion tokens**. But
total *observed* context tokens across every Claude Code turn since squire has existed at all
(2026-09-12 onward, 3,264 turns) is **~1.11 billion** — Layer 2's figure is about 9.4x the total
token volume of every turn in that entire window, squire-related or not. That is not possible.
Layer 2's flaw: it assumes text avoided by one squire call would otherwise have sat verbatim in
context and been re-paid-for on literally every later turn of a session, including sessions with
hundreds of turns after one large early call — real context is compacted, truncated and rewritten
long before that. Layer 2 as currently computed is not usable as reported (see Counterfactual,
below).

### 3. Cache-read share is already ~98–99% and essentially flat — squire cannot move this number

Weekly cache-read share of context tokens: W34 98.42%, W35 98.40%, W36 99.04%, W37 99.10%, W38
98.74%. Prompt caching was already doing almost all of the "don't re-send everything" work before
squire existed. Squire's lever is different: fewer *new* bytes entering context per turn, not a
higher cache hit rate. Framing squire's benefit in terms of cache-read ratio would overstate it;
framing it in terms of per-turn context growth is the honest comparison, and that comparison (see
Finding 4) is currently a null result.

### 4. Per-turn context size did not measurably drop after squire's hooks landed

Context tokens per turn, deduplicated: BEFORE (through 2026-09-11, 31,829 turns, 173 sessions)
316,880/turn; DURING (2026-09-12–13, unenforced, 2,277 turns) 344,440/turn; AFTER (2026-09-14
onward, enforcement hooks live, 985 turns) 326,635/turn. These are within noise of each other —
there is no visible drop, let alone one of the magnitude the ledger's own Layer 1 estimate would
imply. Two explanations that are NOT distinguishable in this data: (a) squire's savings are real
but too small relative to total context growth (much of which is code/tool output squire never
touches) to show up in a per-turn average; (b) the AFTER sample is small (985 turns, 5 sessions)
and dominated by a few heavy sessions. This is exactly the kind of question the pending controlled
A/B (`BACKLOG.md`) is designed to answer and this observational data cannot.

### 5. A one-day backend outage accounts for most of the 117 UNKNOWN ledger calls

Of 117 non-passthrough calls with `backend_ok: false`, 91 (78%) fall on 2026-09-13, with 10 on
09-14 and 16 on 09-15. `ask` (56 of 117) and `draft` (41 of 117) account for 97/117; their overall
ok-rates (62.4% and 85.6%) are far below `sum`/`grep`/`run`/`diff` (95–97%) even outside that
outage window. This points at a specific local-model reliability gap on longer-context or more
open-ended calls (`ask`, `draft`), not a uniform backend flakiness — worth a targeted look before
trusting `ask`/`draft` output as readily as `sum`/`grep`.

## Observed usage: 5-hour and weekly windows (VERIFIED — not mapped to any plan limit)

This data has no information about what this account's actual 5-hour or weekly limits are; only
Anthropic's billing/limits system knows that. What follows is observed consumption, nothing more.
Mapping it to "how close to the limit" requires the owner to supply the plan's stated numbers.

**Rolling 5-hour window** (one observation per turn arrival, summed over the trailing 5h ending
at that turn; 35,090 windows, deduplicated tokens, context+output):
- peak: 839.6M tokens (2026-09-06T04:07 UTC)
- median: 145.5M tokens
- p90: 485.0M tokens

**Calendar week** (context+output tokens, turns, deduplicated):

| Week | Turns | Total tokens | Tokens/turn |
|---|---|---|---|
| 2026-W34 | 3,538 | 687.7M | 193,620 |
| 2026-W35 | 9,618 | 3.32B | 344,041 |
| 2026-W36 | 13,483 | 4.27B | 315,772 |
| 2026-W37 | 7,467 | 2.63B | 350,701 |
| 2026-W38 (partial, through 09-16) | 987 | 322.6M | 326,706 |

**Rolling 7-day window**: peak 5.48B tokens (2026-09-08T23:40 UTC), median 3.04B tokens.

**Time-of-day**: turns cluster 00:00–05:00 UTC (2,639 / 3,079 / 4,668 / 4,621 / 2,630 / 1,781 —
roughly 20:00–01:00 US Eastern), consistent with unattended overnight agent runs rather than
daytime interactive sessions.

## Counterfactual: with vs without squire — ESTIMATED, wide range, not measured

**Assumption, stated explicitly:** chars ÷ 4 (the same heuristic `squire stats`/`squire_report.py`
already label ESTIMATE) converts squire's ledger `chars_in − chars_out` (bulk text kept out of the
agent's context on 1,020 model-backed calls) into a token count. This assumes 4 characters per
token, which is a rough average for English/code text, not a measured tokenization.

**Context re-reading, modelled two ways, giving the range's two ends:**
- **Floor — one-time only:** `chars_saved / 4` = **~73.0M tokens**. This is what the avoided text
  would have cost if it entered context exactly once and were never read again. It is a floor
  because in a genuinely cached session, every later turn re-reads prior context at (mostly)
  cache-read rates, so the true saving is larger than one-time.
- **Ceiling — bounded by observed total volume:** the ledger's own re-read multiplier (Layer 2
  above) is unusable at face value (Finding 2), so instead of trusting its 10.46B-token output,
  the ceiling here is bounded by the total context tokens actually observed across every turn in
  every session since squire has existed at all: **~1.11B tokens** (2026-09-12 onward, 3,264
  turns). Squire cannot have saved more than the total volume that flowed through that same
  window, so this is a legitimate upper bound even though it is not a per-call estimate.

**RANGE: ~73M to ~1.1B tokens kept out of context, ESTIMATED**, a roughly 15x spread. The width
of that range is itself informative: it says the current instrumentation cannot distinguish "a
modest, real saving" from "a large fraction of all context in this window," and closing that gap
is exactly what a controlled A/B (paired runs with and without squire, same tasks, same machine)
would do. This is not a stand-in for that experiment.

**Cross-check against Finding 4:** the per-turn context averages (BEFORE 316,880 / DURING 344,440
/ AFTER 326,635) show no drop of the magnitude either end of this range would predict if squire
were the dominant driver of session context size. The two views disagree, and that disagreement —
not either number alone — is the finding: either squire's effect is real but swamped by other
context growth in the small AFTER sample, or the ledger-based estimate (either end of the range)
overstates squire's share of total context. The data cannot currently tell which.

## Other observations

- **117 UNKNOWN ledger calls**: see Finding 5.
- **Turns per session**: median 73.5, max 1,840 (one very long session dominates the tail);
  196 sessions total in the deduplicated turn set.
- **Subagent/main-session split: UNKNOWN, not measurable from this data.** Every one of 35,091
  deduplicated turns across all 196 session transcripts has `isSidechain: false` — either
  subagents write to their own session files without ever setting this flag to true in the
  transcripts this analysis reads, or the field means something narrower than "ran inside a
  subagent." Do not read the 0% figure as "no subagent work happened"; it is an instrumentation
  gap, not a measurement.
- **Tool-call-preceding-context-jump analysis**: not done in this pass (would need per-message
  content-block parsing beyond the `usage` block already extracted). Ranked below the findings
  above because the 5 findings here already point at concrete next steps; flagged for a future
  pass if wanted.
- **Compaction cost**: not isolated in this pass; the transcript schema's `isSnapshotUpdate` /
  `snapshot` fields look like the right place to look, but were not analyzed here for time.

## Method and caveats

- Source: `~/.claude/projects/**/*.jsonl` (991 session transcripts, oldest 2026-08-16, ~1.7GB,
  read only for `message.usage`, `message.id`, `message.model`, `isSidechain` and `timestamp` —
  never message content) and `~/.squire/ledger.jsonl` (2,306 raw rows; 1,074 after excluding
  `is_test_row` fixture rows used by squire's own pytest suite, 1,020 of those model-backed).
- New code: `scripts/squire_report.py`'s `collect_all_turns`, `daily_stats`, `weekly_stats`,
  `rolling_window_stats`, and the `--timeseries` CLI flag; `parse_session`'s dedup fix (Finding 1).
  Tests: `tests/test_report.py::test_duplicate_message_id_counted_once`,
  `::test_timeseries_daily_and_rolling_window`.
- BEFORE/DURING/AFTER windows follow `BACKLOG.md`'s `token-usage-time-series-charts-before-during-
  after` item: BEFORE = 2026-08-16 to 09-12, DURING = 09-12 to 09-14 (squire existed, hooks not
  yet enforcing), AFTER = 09-14 onward (enforcement hooks live).
- No charts in this pass; charting is `BACKLOG.md`'s separate, still-open item.
- Every number above is either VERIFIED (computed directly from `message.usage` blocks or the
  ledger) or explicitly labelled ESTIMATED with its method. Nothing here is a
  measured dollar figure or a measured percentage suitable for a public claim.

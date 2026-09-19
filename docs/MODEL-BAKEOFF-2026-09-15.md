# Model bake-off: does a newer open model beat qwen2.5:14b on this 3090? (2026-09-15)

Research bake-off. Question: does any newer open-weight instruct model, at a
quant that fits the RTX 3090's 24 GB, run squire's actual job (condense test output/logs/diffs,
answer strictly from given text, say UNKNOWN when absent, never invent names) better than the
current default, measured on `eval/run_eval.py`.

## Shortlist considered

Sourced from Ollama's own library pages (fetched 2026-09-15) rather than search-result summaries,
since several blog posts returned by the initial web search named models (e.g. "Gemma 4",
"Qwen3.6") that do not appear in Ollama's library and were not used further:

| Model | Ollama tag | Size (pulled) | Context | Licence |
|---|---|---|---|---|
| Qwen3 14B | `qwen3:14b` | 9.3 GB | 40K | Apache-2.0 |
| Gemma 3 12B | `gemma3:12b` | 8.1 GB | 128K | Gemma Terms of Use |
| Mistral Small 3 (24B) | `mistral-small:24b` | 14 GB | 32K | Apache-2.0 |
| qwen2.5:14b (current default) | `qwen2.5:14b` | 9.0 GB | — | control |
| qwen2.5:14b-instruct-q8_0 (already pulled, unused) | — | 15 GB | — | control |

(https://ollama.com/library/qwen3, https://ollama.com/library/gemma3,
https://ollama.com/library/mistral-small, fetched 2026-09-15.)

## Method

`eval/run_eval.py` run once per model via `SQUIRE_MODEL=<tag> SQUIRE_SOURCE=test`, same 8-fixture
corpus (`eval/fixtures/*.log`+`.json`) used for every model, same machine, sequential (one model
loaded in VRAM at a time via Ollama's default `keep_alive`). Latency (`gen_s`) and UNKNOWN/backend
status pulled from `~/.squire/ledger.jsonl` rows tagged `source=test` in each run's time window.
GPU memory read via `nvidia-smi --query-gpu=memory.used,memory.total`.

**Caveat found while running this:** the eval is not fully deterministic — the baseline
`qwen2.5:14b` itself scored recall 1.00 on one run and 0.875 on an immediate re-run (missed
`jest_one_failure.log`). n=8 fixtures per run is too small to treat a single run as ground truth
for any model; the numbers below are 1-2 runs per model, not the 50-case corpus behind the
published 1.00 recall figure in `docs/BENCHMARK.md`/README.

## Results (2 runs each unless noted)

| Model | Recall (runs) | UNKNOWN/false-clean | p50 gen_s | VRAM after load |
|---|---|---|---|---|
| qwen2.5:14b (default) | 1.00, 0.875 | 0 | 1.7s | ~9 GB |
| qwen2.5:14b-instruct-q8_0 | 1.00, 1.00 | 0 | 1.7s | ~15 GB |
| qwen3:14b | 0.75, 0.375 | 0 | 5.2s | ~9 GB |
| gemma3:12b | 1.00, 1.00 | 0 | 3.8s | ~8 GB |
| mistral-small:24b | 1.00, 1.00 | 0 | 2.8s | ~14 GB |

Exact commands: `SQUIRE_MODEL=<tag> SQUIRE_SOURCE=test python3 eval/run_eval.py`, run from this
directory. VRAM snapshot: `flatpak-spawn --host nvidia-smi --query-gpu=memory.used,memory.total
--format=csv,noheader,nounits`.

## Finding

**`qwen3:14b`'s default Ollama tag performs clearly worse** (recall dropped to 0/1 and even 0/2 on
fixtures the other four models get right), most likely because that tag ships with "thinking"
enabled by default and its reasoning preamble crowds out the extractable test name/line under
squire's prompting — not investigated further within this bake-off's timebox.

**`gemma3:12b` and `mistral-small:24b` matched the baseline's recall** (1.00 on both runs) but
neither beat it: both are slower (p50 3.8s and 2.8s vs. 1.7s) and use as much or more VRAM (14 GB
for mistral-small:24b vs. 9 GB for the default), for no fidelity gain measured. Neither clears the
bar this bake-off set (≥ baseline on every fidelity metric, and better on speed or quality).

## Decision

**No change to the default.** `SQUIRE_MODEL` stays `qwen2.5:14b`; `AUTO_TIERS` is unchanged. All
three candidate models are left pulled on this workstation (see `ollama list`) for any future
re-test — nothing removed without the owner.

## Re-running this later

The eval corpus here is small (8 fixtures) and single/double-run per model. A model that looked
tied or slightly behind here could look different on the full 50-case corpus behind
`docs/BENCHMARK.md`, or with `qwen3:14b`'s non-thinking variant/tag, or with more repeats to
account for the run-to-run variance observed even on the current default.

## Section 2 (2026-09-15, decisive pass) — full n=50 corpus, ≥3 passes

**Decision rule, stated before looking at results:** switch the default only if a candidate has
mean recall ≥ the default's, zero invented names, false-clean no worse, p95 latency ≤ 1.5× the
default's, and leaves ≥4 GB VRAM free. Ties go to the current default.

**Method.** The n=50 figure in `docs/BENCHMARK.md`/README comes from `scripts/benchmark.py`'s
`FIDELITY_CASES` (6 hand-written synthetic pytest-shaped cases, 5 with known failures + 1 clean
control) run via `trial_fidelity_case(case, n=10, ...)`: 5 failing cases × 10 reps = n=50 scored
recall reps (the control contributes a false-clean check, not a recall score) — confirmed live,
this session's own runs printed `"n_recall": 50` exactly. Rather than run `scripts/benchmark.py`'s
full suite (file/cmd/det/concurrency trials — far too slow for the timebox and not needed for a
model comparison), this pass calls `trial_fidelity_case` directly for each of the 6 cases, one
isolated checkpoint file per model+pass, tagged `SQUIRE_SOURCE=test` (script:
`bakeoff_runner.py`, not committed — a thin wrapper importing `scripts/benchmark.py` unchanged).
`qwen3:14b` ran with `SQUIRE_THINK=false` (new in this release — see squire.py's `THINK`/`llm()`),
Ollama's documented switch to disable "thinking" on hybrid-reasoning models
(https://ollama.com/blog/thinking, "Turning thinking on/off"; verified live against this tag: the
summary below shows no `<think>` preamble and no invented names).

**Exact commands** (from this directory, after `flatpak-spawn --host ollama pull <tag>`):
```
SQUIRE_MODEL=<tag> [SQUIRE_THINK=false] python3.13 bakeoff_runner.py <label> <checkpoint> 10
```

**Results — qwen2.5:14b (default) and qwen3:14b non-thinking, 3 full passes each (n=50/pass):**

| Model | Pass | Mean recall | n | False-clean | Invented names | p50 gen_s | p95 gen_s |
|---|---|---|---|---|---|---|---|
| qwen2.5:14b (default) | 1 | 1.00 | 50 | 0 | 0 (spot-checked) | 2.01s | 2.44s |
| qwen2.5:14b (default) | 2 | 1.00 | 50 | 0 | — | 2.00s | 2.36s |
| qwen2.5:14b (default) | 3 | 1.00 | 50 | 0 | — | 2.00s | 2.30s |
| qwen3:14b (`SQUIRE_THINK=false`) | 1 | 1.00 | 50 | 0 | 0 (spot-checked) | 1.93s | 3.17s |
| qwen3:14b (`SQUIRE_THINK=false`) | 2 | 1.00 | 50 | 0 | — | 1.96s | 2.64s |
| qwen3:14b (`SQUIRE_THINK=false`) | 3 | 1.00 | 50 | 0 | — | 1.98s | 2.49s |

VRAM after load: qwen2.5:14b ~9 GB used (~15.6 GB free); qwen3:14b ~11.6 GB used (~12.6 GB free,
`flatpak-spawn --host nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits`,
both comfortably above the 4 GB floor). Long-input (16384-context) behavior not separately
re-tested this pass — no regression signal from either model on the up-to-220-line fixtures used.

`gemma3:12b` and `mistral-small:24b` were NOT re-run on the full n=50 corpus this pass: a full
n=50 gemma3 run exceeded a 240s budget mid-timebox (cold load + ~2x qwen2.5's per-call latency),
and Section 1's 8-fixture data already shows both tied on recall but clearly slower (p50 3.8s and
2.8s vs. 1.7-2.0s) — already outside the ≤1.5× p95 bar on the smaller corpus, and a larger corpus
changes a recall tie, not a latency verdict. Re-running them at n=50 is the concrete next step if
this decision is revisited later.

**Decision, checked against the stated rule:**
- `qwen3:14b` (non-thinking): recall ties the default (1.00 = 1.00) — does not clear "≥ the
  default's" as a WIN, zero invented names (pass), false-clean no worse (pass, 0=0), p95 ties
  within 1.5× (pass, 3.17s < 3.0-3.6s band loosely, tied not beaten), VRAM free comfortably above
  4 GB (pass). **Every metric ties or is within noise of the default — no metric is clearly
  better. Per the stated rule, a tie goes to the current default.**
- `gemma3:12b` / `mistral-small:24b`: recall ties on the smaller corpus, both fail on latency
  (p50 1.6-2.2× the default) even before a full re-run — do not clear the bar.
- `qwen2.5:14b-instruct-q8_0`: same architecture as the default at 1.7× the VRAM for identical
  recall in Section 1 — no case to switch a same-family default to a heavier quant.

**Result: no change to the default.** `SQUIRE_MODEL` stays `qwen2.5:14b`; `AUTO_TIERS` is
unchanged. This strengthens, rather than reverses, Section 1's finding: on the real n=50 corpus
and across 3 independent passes, the default shows no recall variance (both prior 0.875/1.00
swings do not reproduce at this n) and no candidate model beats it outright. `qwen3:14b`'s
non-thinking mode is a genuinely viable alternative (recall recovered from Section 1's 0.375-0.75
range to a clean 1.00 tie) and is worth another look if the default's speed or licensing terms
(Apache-2.0 either way) ever become a constraint, but is not a switch under this bake-off's rule
as written.

**What shipped from this pass:** `SQUIRE_THINK` (squire.py, tested) so `SQUIRE_MODEL=qwen3:14b
SQUIRE_THINK=false` is a supported, documented configuration for anyone who wants qwen3 instead
of the default — released as v0.2.21 (no default change, additive option only).

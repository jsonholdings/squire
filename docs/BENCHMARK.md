# Squire benchmark (RUN-QUEUE S6/S6b)

## Method

`scripts/benchmark.py` extends the 7b/7c A/B trial (`scripts/ab_trial.py`,
`docs/AB-TRIAL-2026-09-13.md`) into a repeatable, statistically-backed benchmark:

- **Corpus** (committed, secret-free — run `scripts/scrub_check.py` before every commit):
  3 real files (`squire.py`, `README.md`, `tests/test_squire.py`), 3 real commands
  (`pytest -v`, `find . -type f`, `pytest -q` as the short-output control), and 6
  deterministic synthetic pytest-shaped workloads with a **hand-written answer key**
  baked into `FIDELITY_CASES` (the injected failing test-node ids are authored data,
  not derived from any model or heuristic).
- **N ≥ 10 repeats per workload** (`--quick` = 10, full run = 20 solo / 12 per
  concurrency level), not single samples.
- **Tokens**: chars/4, the same approximation squire.py's own heuristic uses. No
  tokenizer library (e.g. tiktoken) is installed on this workstation — checked this
  run — so no exact-tokenizer count is claimed anywhere.
- **Latency**: wall-clock seconds per `squire` subprocess call, solo (concurrency=1)
  and under 2/4/6 concurrent calls from this script's own `ThreadPoolExecutor`,
  reported as p50/p95.
- **Fidelity**: exit-code-preserved % (squire's JSON exit code vs the real subprocess
  exit code), failing-name recall % (how many of the answer-key fail ids survive into
  squire's summary+raw_tail), and a false-positive check on the all-pass control case
  (squire must not invent a failure that didn't happen).
- **UNKNOWN/fail rate**: fraction of all repeats across all workloads where squire's
  JSON could not be parsed or the call errored.
- **Bootstrap 95% CI**: percentile bootstrap (2000 resamples, fixed seed for
  reproducibility) on every reported median/mean. `tests/test_benchmark.py` verifies
  the CI machinery itself against known-answer controls (constant data collapses to
  a zero-width interval; a value far outside the data is excluded; a true median
  inside a symmetric sample is contained) before any benchmark number is trusted.
- **Ledger hygiene**: every squire call this script makes sets `SQUIRE_SOURCE=test`,
  so it lands in `~/.squire/ledger.jsonl` tagged `source=test` and is excluded from
  `squire stats`/squire_report per the S4 convention (squire.py:82) — the real
  production ledger is not polluted by benchmark noise.

## Stated confound

qwen2.5:14b runs on a single shared RTX 3090. Other Claude sessions on this
workstation may call squire concurrently while this benchmark runs. The
"concurrent load" levels (2/4/6) are THIS SCRIPT's own concurrent calls; they are
not exclusive access to the GPU, and any numbers from a run coinciding with other
sessions' squire use should be read as measured-under-real-contention, not
isolated-hardware numbers. Re-running at a quiet time and comparing is the way to
separate the two, and is recommended before S8 publishes final numbers.

## Checkpoint/resume (S6b)

Every trial rep is appended to `data/benchmark-checkpoint.jsonl` the instant it's
computed. Re-running the same `scripts/benchmark.py` command resumes and only runs
reps not already in the checkpoint. `scripts/benchmark.py --report` computes stats
from whatever is checkpointed, with **no new model calls**, and states n honestly
per cell — a cell with fewer reps than the target reads its real n, never rounded
up. This is why the numbers below exist despite no single run completing end to end.

## Run status (2026-09-13, S6b session) — REAL NUMBERS, PARTIAL COVERAGE

Four foreground `--quick` (N=10 target) chunks were run back-to-back, each killed by
its own `timeout` at the session's 45-minute cap, resuming from checkpoint each time
(`squire jobs` showed only one old finished job from another session at the start —
GPU was otherwise free per `squire doctor`, but other sessions' subagents were
visibly active in the shared task directory during the run, per the confound above).

**Solo (N=10 each), measured** — `python3.13 scripts/benchmark.py --report --json`:

| Metric | Value | n | 95% CI |
|---|---|---|---|
| Tokens saved (median, chars/4 approx) | **91.4%** | 60 | [77.1%, 94.9%] |
| Exit code preserved | **96.7%** | 30 | — (point %, cmd workloads only) |
| Failing-name recall (mean) | **1.00** (100%) | 50 | CI collapses to 1.0 (all recalls were exactly 1.0) |
| UNKNOWN/fail rate | **0.0%** | all reps | — |
| False positive on all-pass control | **False** (none observed) | 10 | — |

All 3 file workloads (`squire.py`, `README.md`, `tests/test_squire.py`), all 3 cmd
workloads (`pytest -v`, `find . -type f`, `pytest -q` control), and all 6 fidelity
cases reached the full N=10 quick target.

**Concurrency latency (squire.py, solo/2/4/6):**

| Level | p50 | p95 | n |
|---|---|---|---|
| 1 (solo) | 12.11s | 23.87s | 10 |
| 2 | 17.75s | 25.14s | 10 |
| 4 | — | — | **0 — NOT RUN** |
| 6 | — | — | **0 — NOT RUN** |

**GAP, stated plainly:** concurrency levels 4 and 6 have zero reps. `pytest -v`
(full local suite, run twice per rep — raw + squire) turned out to dominate wall
time (~80s/rep observed) and consumed most of the timebox before concurrency
testing began. The checkpoint means a future run resumes concurrency:2 (5/10 more
rounds) and starts 4/6 from zero — no solo/cmd/fidelity work is repeated.

**Raw data:** `data/benchmark-checkpoint.jsonl` (per-rep, resumable),
`data/benchmark-report-20260913.json` (this session's `--report --json` snapshot).

**Sync-vs-queue comparison** (`squire submit`/`worker` vs synchronous `squire run`)
from the original S6 spec: **not started**, out of scope given the time this run
consumed.

**Next step for S7:** resume with `python3.13 scripts/benchmark.py --quick --json
--out data/benchmark-<date>` (it will skip everything above and go straight to
concurrency 2/4/6) to fill the two missing cells, then decide whether N=10 quick or
the full N=20/12 run backs the S8 published numbers.

## S7 update (2026-09-13) — exit-code miss root-caused, false-positive check fixed, conc 4/6 filled

**Exit-code miss, root-caused.** The 1-of-30 mismatch S6b found (`pytest_verbose` rep 4) was
**not** squire losing an exit code. `trial_cmd_workload` runs the raw command and the
squire-wrapped command as two SEPARATE, non-atomic live invocations of `pytest -v` — this
repo's own full suite. That suite had a real bug (below): tests shared the production
`~/.squire/llm.lock` flock with every other squire process on the workstation, so under
tonight's real concurrent load a worker/queue test could block up to `LLM_LOCK_TIMEOUT`
(240s) waiting for the lock and its own subprocess timeout, flipping that ONE live run's
actual outcome relative to the other. squire's own exit-code passthrough
(`tests/test_squire.py::test_exit_code_is_preserved` et al., independent of any live suite)
was green throughout and is unaffected — the defect was benchmark-harness lock contention,
not squire mishandling a real exit code. Verified by reading `squire.py:cmd_run` (returncode
captured once, passed straight to `emit()` and `sys.exit()` on both branches — no bug found)
and by the fix below eliminating the flakiness end to end (full suite: was 101/106 with 5
`TimeoutExpired`, now **110/110 in ~9s**, no timeouts, confirmed twice).
- **Fix:** `tests/test_squire.py` and `tests/test_queue.py`'s `qenv()` now set
  `SQUIRE_LLM_LOCK` to a per-test/per-file temp path, isolating every test's flock from the
  real one other live squire processes hold. Same fix pattern already existed for
  `SQUIRE_QUEUE_DB`/`SQUIRE_WORKER_HEARTBEAT`/`SQUIRE_LEDGER`; the lock path was the one
  left un-isolated.
- **Post-fix verification (5 fresh reps per cmd workload, `pytest_verbose`/`find_files`/
  `pytest_quiet_control`):** 15/15 exit-code matches, 0 mismatches.
- **Combined exit-code-preserved (all cmd-workload reps, pre- and post-fix):** **97.8%**
  (44/45, n=45) — the single pre-fix miss is kept in the historical record rather than
  discarded; the post-fix subsample alone is 100% (15/15).

**`squire status ''` / `squire wait ''` crash, fixed.** Both used `int(argv[0])` uncaught;
an empty or non-numeric id raised a raw Python `ValueError` traceback. Added `_parse_job_id()`
(`squire.py`), which gives a clean `usage: ...` error instead. Regression tests:
`tests/test_queue.py::test_status_empty_id_is_clean_usage_error_not_a_crash` and
`test_wait_empty_id_is_clean_usage_error_not_a_crash` (each with a valid-id control proving
the fix didn't change the normal path).

**S10: `squire grep` timeout/failure now logs a ledger row.** A `squire grep` whose embedding
call failed or timed out died inside the `except BackendError` branch in `cmd_grep` with NO
ledger row — invisible to `squire_usage_report.py`, indistinguishable from never having run.
`log_call` gained a `status` field (distinct from `source`, which is provenance); the failure
branch now logs `status="timeout"` or `"error"` with the elapsed duration. Regression test:
`tests/test_squire.py::test_grep_backend_failure_still_logs_a_ledger_row` (control: ledger
starts empty, proving the row it finds came from this run).

**False-positive-on-control check, fixed (found while re-verifying, not in original scope).**
The all-pass fidelity control showed **10/10 false positives** in the checkpoint (contradicting
this doc's earlier "False, none observed" line, which was never actually checked against the
aggregate — corrected here per CLAUDE.md §17). Live-verified cause: squire's real summary for
an all-pass run is `"No errors seen ... passed without any failures"` — correct, and exactly
what `condense_verified`'s own prompt asks for ('Say "no errors seen" only if there are none.')
— but the old detector flagged any occurrence of the substring "fail" without the exact literal
"0 failed", so a correct summary always tripped it. Fixed to check for the model's own
"no errors seen" convention instead. Re-verified live: 10/10 correctly `False` post-fix. New
test: `tests/test_benchmark.py::test_control_false_positive_check_accepts_squires_real_no_errors_phrasing`
(control: a genuine bogus "FAILED" summary must still be caught).

**Concurrency 4/6, filled (partial, timeboxed):**

| Level | p50 | p95 | n (of 10 target) |
|---|---|---|---|
| 1 (solo) | 12.11s | 23.87s | 10 |
| 2 | 17.75s | 25.14s | 10 |
| 4 | 40.08s | 59.46s | 8 |
| 6 | 42.14s | 68.93s | 6 |

n=8/6 rather than 10: this run hit its own timebox before the last rounds completed at the
higher concurrency levels (each round is `level` simultaneous `squire sum` calls sharing one
GPU, so higher levels take longer per round). Confound stated above still applies: this is
THIS SCRIPT's own concurrency, not exclusive GPU access — other real sessions were active
tonight per the run queue. Resuming again would add the missing 2/4 rounds without repeating
anything.

**Updated headline numbers (this session, `--report --json`, all cells combined):**

| Metric | Value | n | 95% CI |
|---|---|---|---|
| Tokens saved (median) | 77.2% | 75 | [68.8%, 92.9%] |
| Exit code preserved | 97.8% | 45 | — (44/45; 1 pre-fix historical miss, 15/15 post-fix) |
| Failing-name recall (mean) | 1.00 | 50 | CI [1.0, 1.0] |
| False positive on all-pass control | False (0 observed, post-fix) | 10 | — |
| UNKNOWN/fail rate | 0.0% | all reps | — |

Sync-vs-queue comparison: still not started, out of S7's scope too.

## S8 update (2026-09-13) — GATE FAILED, NOT PUBLISHED

S8's mandate was: publish to the public mirror only if post-fix (post-00b2aff) exit-code
preservation is **100% with n≥30**. Resumed the checkpoint with a full (non-`--quick`) run
(`n_solo=20`, `n_conc=12`) to grow the sample. It added 13 more cmd-workload reps (58 total,
up from 45) before being stopped partway through concurrency rounds (foreground timebox).

**A second exit-code miss appeared, this session, entirely on post-00b2aff code**
(`cmd:pytest_quiet_control`, one rep: `exit_code_match: false`, `pct_saved: -1328%` — squire's
run took longer AND disagreed with the raw run's exit code). This is in addition to the
already-documented pre-fix `pytest_verbose` miss from S6b. Post-fix subsample is now **15
clean (S7) + 13 more (this session, 1 miss) = 28 reps, 27 matches = 96.4%** — short of both
gate criteria (n<30, and not 100%).

**Root cause, not yet fixed:** `trial_cmd_workload` runs the raw and squire-wrapped commands
as two separate, non-atomic live invocations of this repo's OWN real `pytest -v`/`pytest -q`
against a shared, unlocked (outside `tests/`) production `~/.squire/llm.lock` and a live
GPU under real concurrent load from other sessions active tonight (confirmed: `kanban-spec`
files were being edited by another session during this run — the stated confound at the top
of this doc, materializing again). S7's fix isolated squire's OWN test suite (`tests/`) from
the shared lock; it did not and could not isolate this benchmark's cmd-workload corpus, which
deliberately measures squire wrapping a REAL live command outside squire's own test tree. Two
sequential live runs of a resource-contended `pytest` process under load can genuinely produce
different outcomes independent of anything squire does — squire's own exit-code passthrough
unit test (`test_exit_code_is_preserved`) remains green and unaffected throughout.

**Per the S8 brief's hard gate, this is NOT PUBLISHED to the public mirror.** The private
repo and storage remote carry this honest result; `scripts/sync_to_mirror.py --apply` was not
run. Next step to actually pass the gate: either (a) make the cmd-workload corpus atomic/
exclusive (e.g. serialize raw+squire pytest invocations under a benchmark-owned lock so real
GPU contention can't skew which one finishes with which exit code), or (b) run the benchmark
during a confirmed quiet window with no other sessions active, to remove the confound rather
than explain around it.

## S8b update (2026-09-13) — deterministic exit-code corpus added, GATE PASSED

S8's own diagnosis was that the exit-code cell was never testing squire — it was testing
whether two SEPARATE, non-atomic live invocations of this repo's own resource-contended
`pytest` suite happen to agree with each other under real GPU/lock contention from other
sessions. `squire.py:cmd_run` was re-read and confirmed correct: `p.returncode` is captured
from the wrapped subprocess BEFORE `condense_verified` is ever called, and is passed to
`emit()`/`sys.exit()` on every branch (success, short-output, long-output, and the
`SQUIRE_RUN_TIMEOUT` branch) — there was no code path where squire itself could lose or
alter an exit code. The defect was in the *benchmark harness's choice of corpus*, not in
squire.

**Fix: `DETERMINISTIC_CMD_WORKLOADS` (`scripts/benchmark.py`).** A new, frozen, secret-free,
no-network, no-shared-lock corpus:
- `tests/fixtures/bench_emit_exit.py` — prints a fixed number of lines and exits with a fixed
  code baked into its own argv (0, 1, 2, 3, 124, 137). The expected answer is known before the
  process runs, so raw and squire-wrapped invocations of the SAME deterministic script cannot
  disagree with each other by construction.
- `tests/fixtures/bench_project/` — a frozen pytest fixture project (`sample_pass.py`: 3
  always-passing tests, exit 0; `sample_fail.py`: 1 passing + 2 always-failing tests, exit 1).
  Named `sample_*.py`, **not** `test_*.py`, and invoked with
  `pytest -o python_files=sample_*.py <path>` so squire's own root `pytest -q`
  (`pyproject.toml` `testpaths=["tests"]`, default `python_files=test_*.py`) never collects
  them — verified by a negative-control test
  (`tests/test_benchmark.py::test_bench_project_fixtures_are_not_collected_by_the_repos_own_suite`)
  that the fixture files never appear in `pytest --collect-only` output.
- Each rep checks **three-way agreement** (raw == squire == expected), not just raw == squire
  — a live corpus can have two flaky runs agree with each other while both being wrong;
  the deterministic corpus cannot, since the expected value is authored data, not derived from
  a live run.
- Most workloads keep output at or under `squire.py`'s `SHORT=60`-line threshold, so they never
  touch the shared `~/.squire/llm.lock` at all. One workload (`det_exit1_invokes_model`,
  90 lines) deliberately exceeds it, to prove exit-code passthrough holds even when the model
  IS called under real contention.
- The old live cmd workloads (`pytest_verbose`, `find_files`, `pytest_quiet_control`) are kept
  and reported **separately**, explicitly labelled "LIVE, load-sensitive" in both code comments
  and `print_summary` output — they measure realistic noisy-command token savings, never
  gate-grade exit-code fidelity.

**Result, measured (`python3.13 scripts/benchmark.py --report --json`):**

| Metric | Value | n |
|---|---|---|
| Deterministic exit-code preserved | **100.0%** | **43** (5 reps × 6 fixed-code cases + 3 reps model-invoking + 5×2 pytest-fixture cases) |
| Deterministic all raw results match expected | **True** | 43/43 |
| Live exit-code preserved (unchanged corpus, reported separately) | 96.6% | 58 (56/58 — the S6b + S8 historical live-corpus misses, kept for the record) |
| Tokens saved (median, all file+cmd workloads combined) | 92.9% | 118 |

**GATE PASSED: `deterministic_exit_code_preserved_pct == 100.0`, n=43 >= 30.** Per the S8b
brief's hard gate, `scripts/sync_to_mirror.py --apply` was run and the public mirror was
updated — see CHANGELOG.md and the commit history for the exact sha and mirror CI result.

**Why the headline "tokens saved" number moved (91% → 77% → 93% across S6b/S7/S8/S8b):** each
session's corpus mix changed. S6b measured only file workloads + a small live-cmd sample
(n=60, median 91.4%). S7/S8 added more live `pytest -v`/`pytest -q` reps, whose raw output is
proportionally larger relative to squire's summary than the file workloads (pulling the
combined median down to 77.2%, n=75). S8b adds the deterministic corpus (mostly short,
low-noise fixture output, several with near-100% savings since a 5-12 line raw output
compresses to a one-line summary), which is why the combined figure moved again to 92.9%,
n=118. The shift is corpus composition, not squire's behavior changing — each session's
per-workload breakdown is in `data/benchmark-checkpoint.jsonl` for anyone who wants to
recompute a specific subset.

**Reproduce:** `python3.13 scripts/benchmark.py --quick --json --out data/` (fresh run) or
`python3.13 scripts/benchmark.py --report --json` (stats from the existing checkpoint, no new
model calls). Both read/append `data/benchmark-checkpoint.jsonl`.

## S8c correction (2026-09-13) — headline overclaimed, no CIs on proportions, fixed

**The problem, found on re-review of the published 9ac33c1:** `aggregate()` in
`scripts/benchmark.py` computes `tokens_pct_saved` as `summarize_reps(file_trials + cmd_trials,
"pct_saved")` — a single median over BOTH groups pooled together. Verified live
(`python3.13 scripts/benchmark.py --report --json` against the current checkpoint, n grown to
118 as more full-run reps completed): file-workload savings are consistently 92.9–99.0%
(median 96.0%, n=60, whole files compressed to ≤8 bullets), while live cmd-workload savings
range from -1328% (one lock-contention outlier on the short-output control, which should save
~0%) to 90% (median 68.8%, n=58). With near-equal group sizes and very different distributions,
the pooled median lands right at the boundary between the two groups (~93%) — a number driven
by which group happens to have one more or fewer completed rep, not by anything squire's
behavior changed. This is NOT the deterministic/synthetic exit-code corpus leaking into the
metric — `trial_deterministic_cmd_workload` reps carry no `pct_saved` field at all, confirmed
by reading the code and the checkpoint. It is two legitimately-real workload types (whole-file
summarization vs. live noisy-command wrapping) being blended into one number that reads as a
single "real-world savings" figure when it isn't representative of either group alone.

**Fix:** report the two groups separately, each with its own n and a named 95% CI, and never
publish a blended median across them again:
- **Tokens saved, live noisy commands (headline): 68.8%, n=58, 95% CI [63.0%, 69.2%]** (bootstrap,
  same 2000-resample method as before) — this is the number that represents typical `squire run`
  usage (wrapping `pytest -v`/`pytest -q`/`find` against this repo).
- **Tokens saved, whole-file summarization: 96.0%, n=60, 95% CI [95.8%, 96.2%]** (bootstrap) —
  reported separately, explicitly labelled as a more favorable, less representative case
  (`squire sum` on a whole source file), never folded into the headline.

**CIs added to every proportion that lacked one** (publish rule: n + 95% CI on every number).
Bootstrap is inappropriate for a simple pass/fail proportion at the edge of its range (e.g.
100%/n=43 collapses to a zero-width interval that overstates certainty); used the Wilson score
interval instead, named as such:
- Exit code preserved (deterministic gate corpus): 100.0%, n=43, 95% CI [91.8%, 100.0%] (Wilson).
- Exit code preserved (live corpus, reported separately): 96.6%, n=58, 95% CI [88.3%, 99.0%]
  (Wilson).
- Failing-test-name recall (mean): 1.00, n=50 — bootstrap CI already correctly reported as
  [1.0, 1.0] (collapses because every rep's recall was exactly 1.0); kept as-is.

**Latency:** the existing p50/p95-by-concurrency table already carries n per level and is kept
as-is; no CI is added because a bootstrap CI on a small-n (6–10) wall-clock sample under a
stated, uncontrolled GPU-contention confound would imply more precision than the measurement
supports — the existing "Limitations" language (contention, not isolated-hardware numbers)
stays the honest caveat instead of a false-precision interval.

**Reproduce this correction:** `python3.13 scripts/benchmark.py --report --json` against the
existing `data/benchmark-checkpoint.jsonl` (no new model calls); the file/cmd split and Wilson
CIs above were computed directly from that JSON's `file_trials`/`cmd_trials`/
`deterministic_cmd_trials` reps.

## S12 update (2026-09-13) — sync vs queue measured, heartbeat bug found and fixed, GATE: no guidance change

**Goal:** does `squire submit`+`squire worker` (the job queue, v0.2.9+) beat direct synchronous
`squire sum` under 2/4/6 concurrent agent load? `scripts/benchmark.py` gained a `--mode
sync|queue|both` flag (default `sync`, byte-identical to pre-S12 behavior); queue mode runs
one foreground `squire worker` per concurrency level against an isolated `SQUIRE_QUEUE_DB`/
heartbeat/ledger (a per-run temp dir, never the real `~/.squire/queue.db`), timing each
request end-to-end as `squire submit` + `squire wait --timeout`, the way an agent actually
uses the queue. The shared `~/.squire/llm.lock` is deliberately LEFT at its real default (not
isolated) so lock contention is measured identically to how the sync path already measures it
-- removing that contention is exactly what the queue is supposed to do, if it does.

**Bug found and fixed first (`squire.py:cmd_worker`): heartbeat went stale during normal,
successful jobs.** The very first real run showed 34/34 queue requests reported `UNKNOWN
(no worker heartbeat)` by `squire wait`, at every concurrency level, despite the worker
process being alive and correctly finishing jobs. Root cause, verified live: the heartbeat
was written once per loop iteration, BEFORE claiming a job, so a job whose model call ran
longer than `HEARTBEAT_STALE_S` (default 15s) let the heartbeat go stale WHILE the worker
was actively working. This benchmark's own solo latency table shows 14b calls routinely
take 12-70s (well past 15s) even before S12, so this was a near-certain false UNKNOWN on any
real job, not an edge case. **Fix:** `cmd_worker` now runs a background daemon thread that
refreshes the heartbeat on a fixed cadence (`HEARTBEAT_STALE_S / 3`) independent of job
duration, so staleness now only means the worker process is actually gone or hung.
Reproduced pre-fix (debug harness, direct call): `squire sum squire.py` through the queue
returned `state: UNKNOWN` at `seconds: 16.18`. Post-fix, the same call: `state: RESULT` at
`seconds: 20.19`. Existing suite unaffected: `python3.13 -m pytest -q` → **120 passed**
(confirmed via `squire run --`). No existing test exercised a job running past the
old 15s window, so nothing masked this before.

**SUPERSEDED by S13 below.** The table that stood here compared sync numbers from an
*earlier, separate* checkpoint run (S6b) against queue numbers measured in this S12 session
-- not the same session, not the same load, not a fair A/B. S13 re-measured both arms
interleaved (alternating sync/queue rounds within one run) to close that gap; see the S13
section for the real comparison and the numbers that back the recommendation below.

**Recommendation: NO guidance change** (confirmed, not merely carried over, by S13's fair
A/B). `squire sum`/`ask`/`draft`/etc. remain the right DEFAULT for an agent that needs an
answer inline; `submit`+`wait`/`status` stays the right choice specifically when an agent
wants to submit work and either poll later or hand it to a separate `squire worker`
service, not because it is faster. `squire/CLAUDE.md` and `worker.md` guidance is unchanged
by this result; no proposal is queued for either, since there is no case to make.

**Limitations:** other sessions' real squire use may have added load during this run (the
stated confound applies here too); the isolated QUEUE_DB means this measures ONE worker
processing ONE benchmark's jobs, not queue behavior under a real shared production worker
serving multiple sessions at once, which would need a separate, riskier test against the
real `~/.squire/queue.db`. Reproduce: `python3.13 scripts/benchmark.py --mode queue --quick
--json --checkpoint data/benchmark-checkpoint.jsonl` (resumable); sync numbers unchanged
from S7/S8b, reproduce with `--mode sync` (the default) or `--report`.

## S13 update (2026-09-13) — sync vs queue re-measured INTERLEAVED (fair A/B)

**Gap closed:** S12's table above compared sync p50/p95 at concurrency 1/2 (12.11s/23.87s,
17.75s/25.14s) against numbers that turned out to be byte-identical to an EARLIER S6b run --
sync and queue were never measured in the same session or under the same load, so the
comparison was not a fair A/B. `scripts/benchmark.py` gained `--mode interleave`, which
alternates sync and queue rounds within each concurrency level (round 0: sync then queue;
round 1: queue then sync; ...) against the same `squire.py` workload, isolated
`SQUIRE_QUEUE_DB`/heartbeat/ledger, real `~/.squire/llm.lock` contention (same as S12), so
whatever GPU load drift happens during the run hits both arms at close to the same time
instead of one arm entirely before or after the other.

**Interleaved latency, `n=12` per cell, this session, both arms same run, bootstrap 95% CI
on p50 (`bootstrap_ci`, seed 1234, 2000 resamples):**

| Level | Sync p50 (95% CI) | Sync p95 | Queue p50 (95% CI) | Queue p95 | Queue failures |
|---|---|---|---|---|---|
| 1 (solo) | 12.81s [12.26, 13.76] | 13.82s | 13.69s [13.21, 14.24] | 14.73s | 0 |
| 2 | 21.89s [13.02, 26.12] | 30.72s | 19.46s [13.74, 26.52] | 27.48s | 0 |
| 4 | 37.93s [24.99, 51.50] | 59.77s | 33.04s [20.77, 45.79] | 54.02s | 0 |

Load stated honestly: this workstation's single shared RTX 3090/qwen2.5:14b, this script's
own concurrent calls only -- other sessions may have added real load during the ~16-minute
run (11:31-11:47 EDT), same confound as every other latency table in this doc.

**Reading the numbers:** the 95% CIs overlap at every level -- level 1's CIs [12.26,13.76]
vs [13.21,14.24] overlap narrowly with queue's point estimate nominally slower; levels 2 and
4 overlap widely with queue's point estimate nominally faster on p50 and p95 both times. No
level shows a CI-separated winner. This is the same substantive conclusion S12 reached
(mixed, no clean win), now backed by a same-session, same-load, alternating-round
measurement instead of two runs stitched together after the fact.

**Recommendation: NO guidance change**, now on solid footing. `squire sum`/`ask`/`draft`/
etc. remain the right DEFAULT for an agent that needs an answer inline; `submit`+`wait`/
`status` stays the right choice when an agent wants to submit work and poll later or hand
it to a separate `squire worker` service, not because it is faster. `squire/CLAUDE.md` and
`worker.md` guidance is unchanged.

**Reproduce:** `python3 scripts/benchmark.py --mode interleave --checkpoint
data/benchmark-checkpoint-s13.jsonl --json`. Raw checkpoint keyed under
`concurrency:interleaved:{sync,queue}:{level}`, distinct from S6b/S12's separately-timed
keys so the two can never be silently mixed.

**Limitations, stated plainly:**
- The deterministic corpus proves exit-code *passthrough* is reliable; it does not by itself
  prove summary *quality* under load — that is `failing_name_recall` (fidelity cases, still
  1.00 mean, n=50) and the live-corpus token-savings numbers, both unaffected by this change.
- qwen2.5:14b remains a single shared RTX 3090; other sessions may add real concurrent load
  during any run, including this one. The deterministic corpus is specifically designed to be
  insensitive to that confound for exit codes; it does not remove the confound for latency
  (`latency_by_concurrency`) or for the live cmd-workload cells, which are reported as-is.
- Sync-vs-queue comparison (`squire submit`/`worker` vs synchronous `squire run`) from the
  original S6 spec: still not started, out of scope for S8b too.

## Clean-room run (2026-09-15) — GPU confound retired, partial coverage, honest n per cell

**What was stopped and verified idle before this run**, all by exact name, via
`flatpak-spawn --host`: `qwen2.5:14b`, `gemma3:12b`, and `nomic-embed-text` (`ollama stop
<name>` each), and the GPU-capable `ollama` docker container. Verified idle immediately
before starting:

- `nvidia-smi --query-gpu=memory.used,utilization.gpu`: **2 MiB, 0%**
- `nvidia-smi --query-compute-apps`: **empty** (no compute processes)
- `ollama ps`: **empty** (no model loaded)
- No other agent or session was running against this repo (orchestrator-verified before
  the run; this session held the sole claim).

This retires the confound stated throughout this document since S6b ("other Claude sessions
on this workstation may call squire concurrently while this benchmark runs"): for this run,
this benchmark's own subprocess calls were the ONLY consumer of the GPU and the ONLY caller
of `ollama` for its duration.

**Cold start, measured directly (never previously published):** with the GPU confirmed idle
(0 MiB/0%, `ollama ps` empty), a single first `squire.py sum` call against a small file loaded
`qwen2.5:14b` (12GB) and returned a summary in **8.22s**. This is faster than this doc's
earlier informal ~75s "first call after idle" figure; the honest caveat is that this exact
process (unload via `ollama stop` -> reload) had already been exercised once earlier in the
same session, so the model weights were likely warm in the OS page cache even though the GPU
itself was empty and `ollama ps` reported no loaded model — a fully page-cache-cold figure
(e.g. right after a host reboot) was not measured and may be materially slower. Treat 8.22s as
a same-session reload figure, not a from-reboot cold-start guarantee.

**GPU exclusivity, sampled every ~30s for the run's ~19-minute duration:** the only
compute-apps entry ever observed was this workstation's own `ollama`/`llama-server` process
(PID 3448715, i.e. the backend THIS benchmark's own `squire` calls were talking to) —
no foreign PID or process name appeared at any sample. The run was killed at its timebox
(`kill` on the exact benchmark PID, confirmed dead) rather than left to finish, per the
session's time budget — this is a partial run, not a contaminated one.

**Results, measured (`python3 scripts/benchmark.py --report --json --checkpoint
data/benchmark-checkpoint-cleanroom-20260915.jsonl`), fresh checkpoint (not resumed from any
earlier, non-clean-room run):**

| Metric | Value | n | 95% CI |
|---|---|---|---|
| Tokens saved, whole-file summarization (median) | **97.5%** | 30 (10 each: `squire.py`, `README.md`, `tests/test_squire.py`, all at full N=10 target) | [97.3%, 98.0%] |
| Tokens saved, live cmd workload (`pytest -v`, median) | included in n=35 combined figure below | 5 (of 10 target) | not separately computed at n=5 |
| Tokens saved, combined (file + live cmd) | 97.5% | 35 | [97.3%, 98.0%] |
| Exit code preserved (live `pytest -v` only) | **100.0%** | 5 (of 10 target) | — |
| UNKNOWN/fail rate | 0.0% | all 35 reps | — |
| False positive on all-pass control | not run in this pass | 0 | — |

**Cells with zero reps this run (stated honestly, not rounded up):** `find_files`,
`pytest_quiet_control` (0/10), all 6 fidelity cases (0/10 each), all 9 deterministic
exit-code workloads (0 each), and all four concurrency levels 1/2/4/6 (0/12 each). The run
was killed mid-`pytest_verbose` rep 6 of 10 by the session's timebox — `pytest -v` against
this repo's own ~195-test suite, run twice per rep (raw + squire-wrapped) as required by
`trial_cmd_workload`, dominates wall time exactly as it did in the S6b/S7/S8 sessions, and a
`--quick` (N=10/N=10) run still did not reach the cmd/fidelity/deterministic/concurrency
sections within the run's time budget.

**What this run retires:** the GPU-sharing confound stated in every prior section of this
document ("other Claude sessions may call squire concurrently") — for the cells this run
actually populated (file-workload tokens-saved, `pytest_verbose` exit-code/tokens-saved), the
numbers above are from an exclusively-held GPU, sampled and confirmed throughout, not merely
assumed clean.

**What remains unretired:** single machine, single model (`qwen2.5:14b`), single user/session
generating load, and the chars/4 token approximation (no tokenizer library installed) — none
of those are addressed by GPU exclusivity and are not claimed solved here. The concurrency
latency tables and the deterministic exit-code gate corpus were not re-run clean-room in this
session (0 reps) — the existing contended-GPU numbers for those cells (S6b/S7/S8b sections
above) remain the only data on record for them until a clean-room run reaches those sections.

**Checkpoint:** `data/benchmark-checkpoint-cleanroom-20260915.jsonl` (separate from
`data/benchmark-checkpoint.jsonl`, deliberately, so this clean-room partial run is never
silently averaged together with earlier contended-GPU reps). **Resume:** `python3
scripts/benchmark.py --quick --json --checkpoint
data/benchmark-checkpoint-cleanroom-20260915.jsonl` continues this same clean-room checkpoint
(re-verify the GPU is idle before resuming, since exclusivity was only verified for this
session's own run).

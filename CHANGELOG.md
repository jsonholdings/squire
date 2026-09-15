# Changelog
All notable changes to Squire are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.2.16] - 2026-09-14
### Added
- **Canonical PreToolUse enforcement hooks, moved into squire itself.** `hooks/pretool_wrap.py`'s
  `NOISY_RE` now also wraps `ssh`, any `docker` subcommand, `flatpak-spawn`, bare `curl`,
  `journalctl` and `dmesg` (previously build/test/lint/install tools only), so these route
  through `squire run --` like the rest. A `# squire-raw: <reason>` marker anywhere in a command
  skips wrap/deny enforcement entirely (an intentional, auditable escape hatch, not a silent
  bypass) and is logged to `~/.squire/raw_overrides.jsonl` (ts, session, tool, reason, first 80
  chars of the command); the ledger write is wrapped in try/except so a ledger failure never
  blocks the underlying command.
- **`hooks/search_guard.py`** (new): a PreToolUse hook for Bash and Grep that denies broad raw
  search sweeps -- `grep -r`/`-R`, bare `rg`, `find ... | xargs grep` -- unless marked
  `# squire-raw: <reason>`, pointing at `squire grep` instead. A single explicit
  `grep <pattern> <one-file>` is never denied. The Grep tool call has no command string to carry
  a marker, so an unnarrowed call (no `path`/`glob`) is allowed through with `additionalContext`
  and logged to the same ledger with reason `grep-tool-unnarrowed`, rather than denied outright.
  Fails open on `SQUIRE_HOOK_DISABLE=1`, malformed JSON, or any exception.
- **`hooks/usage_report.py`** (new): the canonical version of workstation-config's
  `squire_usage_report.py` (that copy predates squire's own `hooks/` and is expected to vendor
  from here going forward). Adds a per-session table (squire calls by cmd, plus
  `raw_overrides.jsonl` count) alongside the existing global/per-session summary; CLI flags stay
  backward-compatible (`--ledger`, `--session`, `--json`, plus new `--overrides` and
  `--no-sessions`).
- Tests: `tests/test_pretool_wrap_enforcement.py` and `tests/test_search_guard.py` prove both
  directions of every new rule (wrapped vs. passthrough, denied vs. marker-exempt vs.
  single-file-exempt, ledger writes, fail-open on disable/malformed input/ledger-write failure).

### Fixed
- **Both new hooks matched keywords against the WHOLE command string, not the actual command
  being run.** `grep -r`/`rg`/`pytest`/etc. appearing inside a quoted argument to an unrelated
  command -- `echo "run pytest later"`, a JSON string piped through another program -- triggered
  a wrong deny or wrap. Separately, a multi-line Bash tool call (one `command` string joined by
  real newlines) had every line after the first merged into the first line's argument list,
  because shlex's default whitespace set treats `\n` as plain whitespace -- a genuine `grep -r`
  sitting on line 3 of a command sailed through undenied. Both hooks now tokenize the command
  into pipeline segments (shlex, quote-aware, newline added as a statement separator) and check
  only each segment's actual program (`argv[0]`), never substrings of its arguments. A command
  that can't be safely tokenized (unbalanced quotes) is left untouched. 9 new regression tests
  lock in both the false-positive and the newline fix, alongside the true positives they sit
  next to.

## [0.2.15] - 2026-09-13
### Added
- **GPU hand-off with NCP-ArchPreview (2026-09-13).** `llm()` and `embed()` now check
  `~/.squire/gpu-paused.json` before touching the network. When another local GPU consumer (e.g.
  `ncp up` from the new `ncp-archpreview` infra asset) has written that flag with its owner name
  and pid, Squire returns its normal `UNKNOWN` result (`"model busy (GPU in use by <owner>)"`,
  or a `BackendError` with the same text for `embed()`) instead of calling Ollama. A flag whose
  pid is no longer alive is stale and is removed automatically (`gpu_paused_by()`), so a crashed
  owner never wedges Squire permanently. `squire run` passthrough (no model call for short
  output) is unaffected. 3 new tests in `tests/test_squire.py`
  (`test_llm_returns_unknown_when_gpu_paused`, `test_embed_raises_when_gpu_paused`,
  `test_gpu_paused_by_ignores_stale_pid`) plus a control proving the check can return "not
  paused" too.
- **GPU-pause is backend-aware (owner follow-up, 2026-09-13).** A container's docker-reported
  pid lives in the HOST pid namespace and was invisible to a plain `os.kill` from inside Claude
  Code's sandboxed Bash tool (`_docker_pid_alive()` now falls back to
  `flatpak-spawn --host kill -0`, same escape `gpu_free_mib()` already uses). Also: the pause
  must not block Squire from calling NCP-ArchPreview's own OpenAI-compatible API when Squire is
  deliberately benchmarked against it (`SQUIRE_BACKEND=openai` +
  `SQUIRE_OPENAI_BASE` pointed at the paused owner's own server) -- only Ollama, or an `openai`
  backend pointed at some OTHER server, is short-circuited. `ncp up` now writes `api_base` into
  the flag for this comparison. 3 more tests + controls
  (`test_pid_alive_falls_back_to_flatpak_spawn_host`,
  `test_gpu_pause_does_not_block_calls_to_the_paused_owners_own_api`,
  `test_gpu_pause_still_blocks_openai_backend_pointed_elsewhere`).

### Fixed
- **Two S14 defects found in live use (2026-09-13).** (1) `squire run` ledger rows carried
  `exit_code: None` for every row even though `squire run` prints the real subprocess exit code
  to the user -- `log_call` had no `exit_code`/`duration_s` fields at all, so
  `squire_report.py`/benchmarks could never audit a run's outcome from the ledger. `log_call` now
  accepts both, and `cmd_run` records the real `p.returncode` and wall-clock duration on its
  ledger row. (2) The run summarizer's verify pass compared the final summary against
  `text[-CHUNK:]` -- only the LAST chunk of a multi-chunk input -- so a true claim drawn from an
  EARLIER chunk (a 5,604-line passing smoke-test log, one early line mentioning a handled
  `JSONDecodeError`) was flagged as a false "not mentioned in the source", printed as an
  off-topic-looking last line on an otherwise-correct summary. `condense`/`condense_verified` are
  now built on a shared `_condense_core` that returns the per-chunk notes; the verify check now
  compares against the notes (which together cover the whole input) instead of a tail slice of
  the raw text when the input was chunked. 4 new tests + controls in `tests/test_squire.py`
  (`test_ledger_records_run_exit_code_and_duration`,
  `test_condense_verified_checks_against_full_notes_not_tail_slice` +
  `test_condense_verified_single_chunk_checks_raw_text_control`); 125/125 green.

### Added
- **Regression test for the S12 worker-heartbeat fix (S13).** `tests/test_queue.py` gained
  `test_heartbeat_thread_keeps_worker_alive_through_a_long_job` (in-process, monkeypatched
  `_run_queued_job` sleeps past a small `HEARTBEAT_STALE_S` so the test runs in ~2.5s) plus a
  control, `test_control_heartbeat_goes_stale_without_the_refresh_thread`, that reproduces
  the pre-S12 shape (heartbeat written once, nothing refreshing it) and proves the same
  staleness check CAN go stale -- the S12 fix had no automated coverage before this, only a
  live repro recorded in docs/BENCHMARK.md.
- **`scripts/benchmark.py --mode interleave` (S13).** Re-measures sync vs queue latency with
  both arms alternating within the same run instead of S12's mismatched comparison (queue
  measured live, sync numbers reused unchanged from an earlier S6b run under different
  load). See docs/BENCHMARK.md's S13 section for the corrected numbers and recommendation
  (unchanged: no guidance change).
### Fixed
- **`squire worker`'s heartbeat went stale during normal, successful jobs (S12).** The
  heartbeat was written once per loop iteration, BEFORE claiming a job, so any job whose
  model call ran longer than `HEARTBEAT_STALE_S` (default 15s) let the heartbeat go stale
  WHILE the worker was actively working -- `squire status`/`squire wait` then reported
  `UNKNOWN (no worker heartbeat)` for a job running normally. Found via
  `scripts/benchmark.py --mode queue`: 14b calls routinely take 12-70s, comfortably longer
  than 15s, making this a near-certain false UNKNOWN on real usage, not an edge case.
  Reproduced: pre-fix, `squire sum squire.py` through the queue returned `UNKNOWN` at
  16.18s; post-fix, `RESULT` at 20.19s. Fixed with a background thread that refreshes the
  heartbeat on a fixed cadence independent of job duration. `python3.13 -m pytest -q`: 120
  passed (verified, unaffected).
- **`squire grep` reliably fast/bounded, not a silent all-workstation stall (S11).** Root cause,
  proven from real production ledger rows: two `squire grep` calls tonight logged
  `status=timeout, gen_s=600.1` -- exactly the OLD embed HTTP timeout, proving one embedding
  call to Ollama genuinely hung for its full 600s. Because `embed()` shared the SAME flock
  (`llm.lock`) as `llm()` (chat generation), that one hung embed call blocked EVERY other
  squire invocation on the workstation (run/sum/ask/diff/grep) for up to 10 minutes -- the
  actual cause of "~6 agents fell back to raw grep" tonight. Fixed: `embed()` now uses its own
  lock file (`EMBED_LOCK_PATH`, default `~/.squire/embed.lock`, `SQUIRE_EMBED_LOCK`) with its
  own wait timeout (`EMBED_LOCK_TIMEOUT`, default 60s, `SQUIRE_EMBED_LOCK_TIMEOUT`) so a slow
  chat generation and an embedding call never queue behind each other. The embed HTTP call
  itself now times out at 60s (`EMBED_HTTP_TIMEOUT`, `SQUIRE_EMBED_HTTP_TIMEOUT`) instead of
  600s -- a 10x bound reduction, live-reproduced during this fix (a genuinely degraded backend
  now fails at 60.17s instead of hanging past 600s). Live-verified with fake-server-controlled
  tests: with a chat lock held for 5s, embed (separate lock) still completes in <4s (was: would
  have queued the full 5s+); 7/7 new tests green (`tests/test_squire.py`, S11 section).
- **`squire grep --timeout S` returns the best PARTIAL hits, never nothing, on a deadline
  (S11).** A stuck/slow backend used to make grep run indefinitely (or exit 2 with zero
  results); callers gave up and fell back to raw grep, defeating the squire mandate. `--timeout`
  (or `SQUIRE_GREP_TIMEOUT`) caps wall-clock re-embedding time; chunks embedded before the
  deadline are cached and searched, output carries `"partial": true` plus a visible `PARTIAL`
  note, and a ledger row is logged with `status: "partial"` so a timeout is never silently
  absent from `squire_usage_report.py` (same principle as S10's hard-failure logging, applied
  to a graceful deadline). Indexed-file count is now always printed on every path (was already
  true for the success/error paths; confirmed unchanged for partial).
- **Corrected headline: benchmark tokens-saved figure overclaimed (S8c).** The published
  headline (92.9%, n=118) pooled two workload types with near-equal sample sizes but very
  different savings profiles (whole-file summarization ~96%, live noisy-command wrapping
  ~69%), so the blended median sat at the boundary between them rather than representing
  either. Split into two separately-reported, separately-CI'd numbers: live noisy commands
  (headline) **68.8%, n=58, 95% CI [63.0%, 69.2%]**, and whole-file summarization (reported
  separately, not blended) **96.0%, n=60, 95% CI [95.8%, 96.2%]**. Also added a Wilson-score
  95% CI to every pass/fail proportion that previously had none (deterministic exit-code
  100.0%/n=43 → CI [91.8%, 100.0%]; live exit-code 96.6%/n=58 → CI [88.3%, 99.0%]). See
  `docs/BENCHMARK.md` "S8c correction". No new model runs — recomputed from the existing
  `data/benchmark-checkpoint.jsonl`.

### Added
- **Deterministic exit-code benchmark corpus (S8b), gate for public publish.** S8 found the
  benchmark's exit-code cell flipped because its cmd-workload corpus ran two separate live
  invocations of this repo's own resource-contended `pytest` suite, which can genuinely
  disagree under real GPU/lock contention -- not a squire defect (`squire.py:cmd_run` was
  re-confirmed to capture the real subprocess returncode before any model call, on every
  branch). New `DETERMINISTIC_CMD_WORKLOADS` in `scripts/benchmark.py` uses frozen,
  secret-free, no-network fixtures (`tests/fixtures/bench_emit_exit.py`,
  `tests/fixtures/bench_project/`) whose exit code is known before the process runs, and
  checks three-way agreement (raw == squire == expected). Result: 100.0% exit-code
  preservation, n=43 (>= the 30-sample gate) -- see `docs/BENCHMARK.md` "S8b update" and the
  new README "Benchmarks" section. The old live-corpus numbers are kept and reported
  separately, explicitly labelled load-sensitive.

### Fixed
- **Test-suite lock contention (S7), root cause of the "5 tests TimeoutExpired under load"
  finding from S6b/S9 and of the benchmark's one exit-code mismatch:** `tests/test_squire.py`
  and `tests/test_queue.py` shared the REAL `~/.squire/llm.lock` with every other squire
  process on the workstation, so a DOWN-backend test could queue behind real concurrent usage
  for up to `LLM_LOCK_TIMEOUT` (240s) and blow its own subprocess timeout — and, in the
  benchmark's `pytest -v` cmd workload, flip that one live run's actual result relative to its
  paired raw run. Both test files now isolate `SQUIRE_LLM_LOCK` to a per-test/per-file temp
  path. Full suite: was 101/106 (5 TimeoutExpired), now 110/110 in ~9s, confirmed twice.
- **`squire status ''` / `squire wait ''` crashed with an uncaught `ValueError`** (`int('')`)
  instead of a clean usage error. Added `_parse_job_id()`; both commands now exit with a usage
  message. 2 new tests (`tests/test_queue.py`), each with a valid-id control.
- **`squire grep` embedding-backend timeout/failure logged NO ledger row** (S10) — a stuck grep
  was indistinguishable from one that never ran. `log_call` gained a `status` field; the
  `cmd_grep` failure branch now logs `status="timeout"`/`"error"` with elapsed duration. 1 new
  test, with an empty-ledger control.
- **Benchmark false-positive-on-control check (`scripts/benchmark.py`) always flagged the
  all-pass control** (10/10 in the checkpoint) even though squire's real summary was correct
  ("No errors seen ... passed without any failures") — the old check banned any occurrence of
  "fail" without the literal "0 failed". Fixed to check for squire's own documented convention
  ("no errors seen") instead. 1 new test with a genuine-bogus-failure control.
- **A second test missed by the S7 lock-isolation fix** (S8):
  `test_remote_backend_reports_unknown_not_a_network_call` built its own `env` from
  `os.environ` instead of `DOWN`, so it never got `SQUIRE_LLM_LOCK` isolation and could queue
  behind real concurrent squire usage on the shared production lock, blowing its own 60s
  subprocess timeout under load. Now isolates the lock like every other test. Root-caused
  while investigating a second exit-code-preservation miss found growing the S8 benchmark
  sample (see `docs/BENCHMARK.md` "S8 update").
### Added
- **S8 benchmark growth — NOT PUBLISHED to the public mirror.** Grew the checkpoint sample
  (58 total cmd-workload reps, up from 45); found a second exit-code-preservation miss on
  post-S7-fix code (root cause above). Post-fix exit-code preservation is now 96.4% (n=28) —
  under the S8 gate's required 100%/n≥30 — so `sync_to_mirror.py --apply` was not run this
  pass. See `docs/BENCHMARK.md` "S8 update" for the numbers and the open next step.
- **Benchmark re-verification + concurrency 4/6 (S7):** after the fixes above, re-ran the
  benchmark. Tokens saved median 77.2% (n=75, CI [68.8%, 92.9%]); exit code preserved 97.8%
  (n=45: 44/45, 1 pre-fix historical miss kept on record, 15/15 fresh post-fix reps); failing-name
  recall mean 1.00 (n=50); false positive on all-pass control now correctly False (n=10, was
  10/10 false positive under the old buggy check above); UNKNOWN/fail rate 0.0%. Concurrency
  latency: 4-way p50=40.08s/p95=59.46s (n=8/10, timeboxed), 6-way p50=42.14s/p95=68.93s
  (n=6/10, timeboxed). See `docs/BENCHMARK.md` "S7 update".
- **Real benchmark numbers (S6b):** first REAL numbers from `scripts/benchmark.py`, N=10 quick
  run, checkpointed/resumed across 4 timeboxed chunks. Tokens saved median 91.4% (n=60, CI
  [77.1%, 94.9%]); exit code preserved 96.7% (n=30); failing-name recall mean 1.00 (n=50);
  UNKNOWN/fail rate 0.0%; no false positive on the all-pass control. Concurrency latency:
  solo p50=12.11s/p95=23.87s (n=10), 2-concurrent p50=17.75s/p95=25.14s (n=10); levels 4 and 6
  NOT run (n=0) — the full local pytest suite run twice per rep dominated the timebox before
  concurrency testing reached them. See `docs/BENCHMARK.md`. Raw data:
  `data/benchmark-checkpoint.jsonl`, `data/benchmark-report-20260913.json`.
- **Benchmark checkpoint/resume + `--report` (S6b):** `scripts/benchmark.py` now appends every
  trial rep to a JSONL checkpoint (`data/benchmark-checkpoint.jsonl` by default) the moment it's
  computed, and resumes from it on re-run instead of redoing finished reps — a killed run (GPU
  contention, timebox) now yields real per-workload numbers instead of nothing. `--report` computes
  medians/CIs from whatever is checkpointed so far with NO new model calls, stating n honestly per
  cell (a cell with n=3 reads n=3, never rounded up or hidden). 6 new tests in
  `tests/test_benchmark.py` (round-trip, malformed-trailing-line control, resume-only-runs-missing-
  reps control, partial-n-honesty control, empty-checkpoint-is-all-zero control) — 17/17 pass.
- **Benchmark tooling (S6, partial):** `scripts/benchmark.py` — reproducible N≥10-repeat
  benchmark of squire vs raw output: chars/4 token approximation, bootstrap 95% CI on every
  reported statistic, exit-code-preserved %, failing-name recall % against a hand-written
  answer key, false-positive check on an all-pass control, UNKNOWN/fail rate, and solo/2/4/6
  concurrent-load wall latency (p50/p95). `tests/test_benchmark.py` (11 tests, all with
  controls) verifies the bootstrap-CI machinery itself before any number is trusted. All
  benchmark squire calls tag `SQUIRE_SOURCE=test` so they're excluded from the real ledger's
  stats (S4 convention). See `docs/BENCHMARK.md` — the actual full-N run did NOT complete
  this session (GPU contention from concurrent squire use by other sessions); the run status,
  what's verified, and the next step are recorded there rather than asserted as done.
- **Job queue (S5):** `squire submit <sum|ask|draft|diff|triage> [args...]` queues a job against a
  SQLite spool (`~/.squire/queue.db`, WAL) and returns its id immediately; `squire worker` runs a
  single foreground FIFO loop reusing the exact same condense/llm code (and `_LlmLock`) the
  synchronous commands use, with crash-safe claiming (a `running` job whose worker PID has died is
  requeued). `squire status <id>` / `squire wait <id> [--timeout S]` answer with exit 0 (RESULT,
  output printed), 1 (FAIL, reason printed), 2 (WAIT, still queued/running -- position + age
  printed), or 3 (UNKNOWN -- no live worker heartbeat). `squire jobs` lists everything. The
  synchronous commands are unchanged -- the queue is opt-in and does not alter their behavior (kept
  deliberately identical so a later benchmark, S6, can compare sync vs queued timing). Every
  processed job also writes its normal ledger row with `source: "queue"` plus the job id and
  enqueued/started/finished timestamps. See `docs/QUEUE.md`.

### Fixed
- **Ledger test isolation (S4):** squire's own pytest suite was writing its fixture calls straight
  into the real production `~/.squire/ledger.jsonl` (found 2026-09-13, S3-check: 21 of 33 post-S1
  rows were the same 7 fixture calls repeated across test runs). `tests/conftest.py` now points
  the whole suite at an isolated temp ledger. Every ledger row also carries a new `source` field
  (`cli`/`test`/`hook`/`queue`); `squire stats` and `scripts/squire_report.py` exclude `source:
  test` rows (and, for rows written before this field existed, an exact legacy-fixture-signature
  match) from all totals -- without ever deleting them from the ledger file.
- `squire run --`: a nested launcher (`flatpak-spawn --host docker run ...`, `ssh`, anything that
  attaches stdin) could inherit squire's own stdin and block forever waiting for input that never
  arrives -- from the caller's side indistinguishable from squire being broken (S2, 2026-09-13:
  "squire run breaks on nested flatpak-spawn/docker invocations", reported by a portfolio worker).
  `cmd_run` now always runs the child with `stdin=subprocess.DEVNULL` (squire's contract is
  capture-then-summarize, never interactive, so stdin was never useful) and honors an optional
  `SQUIRE_RUN_TIMEOUT` (seconds) as defense in depth: on timeout the real exit code 124 and every
  byte captured before the kill are still emitted, never silence. Live-repro'd against real
  `flatpak-spawn --host docker run --rm ...` invocations (pass/fail/unknown/>60-line-output all
  preserved the real exit code both before and after -- see `docs/SQUIRE-RUN-NESTED-2026-09-13.md`)
  with no reproducible hang in this shell (Bash-tool stdin is already closed); the stdin fix
  targets a real, well-known subprocess footgun in an interactive terminal, which is the shape of
  the original report.

### Added
- `docs/PERF-CONCURRENCY-2026-09-13.md`: root-caused the backend-ok rate under multi-agent load
  (S1). A single GPU running qwen2.5:14b can't serve several concurrent large-context requests
  cleanly; under real 4-agent load one `sum` call took 272s, near the client's 300s timeout.
  Fixed by serializing all local model calls through a client-side flock (`_LlmLock`, one
  in-flight backend request at a time across all squire processes), a distinct "local model busy
  (queued Ns, gave up)" status instead of a generic backend-down UNKNOWN when the lock itself
  times out (`SQUIRE_LLM_LOCK_TIMEOUT`, default 240s), queue-wait vs generation time now logged
  per call in the ledger (`queue_wait_s`/`gen_s`), and `SQUIRE_KEEP_ALIVE` default raised 30m -> 2h
  so the model stays resident across a burst.
- `scripts/ab_trial.py` + `docs/AB-TRIAL-2026-09-13.md`: the first controlled A/B measurement
  of squire's token savings (SPEC-OPEN-SOURCE.md step 2). Runs a fixed set of real command and
  file workloads through both raw and squire paths, checks exit-code and failure-signature
  fidelity, and includes a control workload (short output) that squire must pass through
  unchanged. Result: median 69.35% tokens saved (chars/4 approximation), range 0.0%-98.2%,
  exit codes and the one injected failure signature preserved on every workload. Limitations
  (cache-read amplification, cold-model latency, no long-output failure case) stated in the doc.
- `scripts/ab_trial.py --fidelity`: closes the "summary fidelity under a genuine, longer
  failure" UNKNOWN left by the first pass. 6 synthetic-but-real pytest-`-v`-shaped subprocess
  runs (>60 lines each, so squire's actual model-summary path runs, not raw passthrough) with
  known injected failing test names at varied positions (early/middle/last/scattered) and
  densities (1-3 failures, one buried in 220 lines of noise), plus an all-pass control. Result,
  this run: exit-code match 6/6 (100%), failing-test-name recall 5/5 non-control cases (100%),
  no fabricated failure on the control. Documented in `docs/AB-TRIAL-2026-09-13.md`.
- `scripts/sync_to_mirror.py` (source-repo tooling, not shipped in this mirror): an explicit
  ALLOW-list sync from the private source repo to this public mirror, with a `--check` parity
  report and an `--apply` that stages changes and only publishes them after this repo's own
  `scrub_check.py` and pytest suite both pass in a CI-like environment. Closes the "no
  source->mirror sync script exists" gap flagged at the 0.2.5 release.
- `docs/assets/avatar.png`: a 500x500 avatar generated by `scripts/build_logo_assets.py` from
  the same `logo-source.svg` as every other logo asset, on the same staleness gate. For the
  GitHub org profile picture or wherever else a square Squire mark is needed; uploading it is
  still a manual UI step. See `docs/assets/logo.md`.

## [0.2.5] - 2026-09-12
### Fixed
- `scripts/build_logo_assets.py --check` compared output file mtimes to the source svg's
  mtime, which is flaky by construction: a fresh git checkout gives every file the same
  checkout-time mtime in no guaranteed order, so the ordering check can fail even when
  outputs match the recorded source hash. Failed CI on run 34734762115 on a clean clone.
  Freshness is now decided by the source hash alone; rendered-byte comparison stays
  deliberately unused (librsvg version differences make PNG output non-reproducible
  across machines).
- CI: install `librsvg2-bin` on the runner. `tests/test_logo_assets.py` renders the logo with
  `rsvg-convert`, which `ubuntu-latest` lacks, so the logo staleness control failed on every
  matrix job (FileNotFoundError) while passing locally.

### Added
- Single-source logo build: `docs/assets/logo-source.svg` is now the only hand-maintained
  logo file. `scripts/build_logo_assets.py` regenerates `icon.svg`, `logo-light.svg`,
  `logo-dark.svg`, `social-preview.svg`/`.png` (1280x640) and prints the inline `<svg>`
  snippet jsonholdings.com's card embeds. `--check` (a sha256 manifest, never a byte
  comparison of rendered output) is wired into `pytest`
  (`tests/test_logo_assets.py`) so CI fails on a stale asset. Documented in
  `docs/assets/logo.md`. Mirrored into the private source repo (path-only commit).

### Security
- `scrub_check.py`'s email-address pattern no longer flags RFC 2606 reserved test
  domains (`example.com`/`.org`/`.net`, `.test`, `.invalid`, `.localhost`) -- synthetic
  addresses used deliberately in tests (`tests/test_squire.py`'s `GIT_ENV`) and docs,
  never a real leak. Control-proofed: a non-reserved domain still fires.

### Fixed
- Tests: the `squire diff` tests' temporary git repos now carry their own identity and ignore
  the machine's git config (`GIT_CONFIG_GLOBAL=/dev/null`, no signing). CI runners have no
  `user.name`/`user.email`, so `git commit` exited 128 and CI failed on every push; the tests had
  passed only on machines with a global identity. Reproduced locally with no identity before fixing.

### Added
- Branding: hand-written SVG logo/wordmark (light + dark, `docs/assets/`), icon-only mark,
  1280x640 social-preview source + rendered PNG, tagline "Offload the bulk, keep the context."
  README gets a logo header, license/python/tests badges, a table of contents, and a
  MEASURED/ESTIMATED callout convention; footer credits "A JSON Holdings project".
- README "Install" section rewritten as tested, numbered command blocks (prerequisites,
  git-install, clone+editable install, Docker build, MCP registration, hook install, `squire
  doctor` verify), each block's real exit code noted, PyPI/zipapp explicitly marked not yet
  available.

### Security
- `scrub_check.py`'s email-address pattern no longer misreads a `git@host:path` SSH clone
  URL as a leaked email address (the domain's greedy TLD match backtracked around a naive
  `(?!:)` lookahead) -- fixed with a word boundary before the lookahead. Control-proofed:
  a real email address followed by punctuation still fires.
- `scrub_check.py`'s secret-shaped pattern now catches an unquoted `key: value` /
  `key = value` credential line, not only a quoted literal, and now scans its own
  source file (an earlier version excluded `scrub_check.py` by name, which is how a
  hardcoded site-specific denylist shipped undetected inside it). Obvious placeholders
  (`<...>`, `${...}`, `[REDACTED]`, `***`/`...`, a markdown code-span backtick) are
  excluded so redaction documentation describing this pattern does not trip it.
- `eval/fixtures/*` (synthetic captured test-runner logs) explicitly allow-listed by
  directory against this pattern -- fixture text (test names and pass/fail markers) legitimately matches the
  shape with no real secret behind it.
- 12 new tests (`tests/test_scrub_check.py`): each unquoted/quoted form caught, each
  placeholder form excluded, and the redaction-doc and denylist-file-in-repo scenarios
  proven with planted controls.

### Added
- Apache-2.0 `LICENSE` and `NOTICE` (JSON Holdings LLC, 2026). `pyproject.toml` license field and
  README license section updated to match. Repo remains private and unpushed; this only unblocks
  `scripts/scrub_check.py --release`'s license gate.

### Removed
- Stray duplicate `test_squire.py` at the repo root (the real copy lives under `tests/`).

### Added (from previous sync)
- `squire run -- <cmd>`: real exit code, raw tail and a local failure summary. Output of 60 lines
  or fewer passes through with no model.
- `squire sum`, `squire ask`, `squire draft`: condense, answer and draft using a local Ollama model.
- `squire diff [--staged] | <ref1> <ref2>`: condense a large git diff, always shows the real
  `git diff --stat` line regardless of backend health.
- `squire stats`: reads a local ledger (`~/.squire/ledger.jsonl`, chmod 700) of chars in/out per
  call; reports real character counts plus a clearly-labelled ESTIMATED token figure (chars/4
  heuristic) that must be cross-checked against real session usage blocks before being cited.
- `--json` flag on every command: emits `{cmd, exit_code, raw_tail, summary, assumed, backend_ok,
  verify_flag}` for machine consumption (a future hook or MCP wrapper).
- Self-check pass (`condense_verified`) on `run` and `diff` summaries: a second local model call
  checks the summary against the source for invented or omitted failures, and appends a visible
  `verify flag` line if it disagrees. Never blocks or retries silently; itself ASSUMED and can be
  wrong. Not applied to `sum`/`ask`/`draft` (would double their cost for lower-stakes text).
- Safety guarantees: backend down → `UNKNOWN`, model output labelled ASSUMED, `num_ctx` set
  explicitly, large inputs chunked.
- Project scaffolding: tests, CI, scrub check, CONTRIBUTING, SECURITY, pyproject.

- v0.2.0: `squire grep` (local-embedding semantic search, incremental on-disk index in
  `.squire-cache/`, git-excluded via `.git/info/exclude`, reports files indexed so zero coverage is
  visible), `squire triage` (real age from heading timestamps + ASSUMED impact line), `squire
  doctor` (backend/models/GPU, exit 0 ready / 2 UNKNOWN), `--version`.
- Backends: `SQUIRE_BACKEND=ollama|openai` (any OpenAI-compatible localhost server: llama.cpp,
  vLLM), `SQUIRE_MODEL=auto` (largest installed tier that fits free VRAM), `SQUIRE_KEEP_ALIVE`
  (default 30m, keeps the model warm so hook-wrapped commands don't pay a cold load).
- Ledger lines carry the Claude Code session id so savings can be tied to a session's remaining turns.
- Claude Code integration: MCP stdio server (`mcp/`), PreToolUse hook that wraps noisy Bash
  commands in `squire run` before they execute (`hooks/pretool_wrap.py`), opt-in PostToolUse
  hook (not recommended: it can only add context), `integrations/install_claude_code.py`
  installer with `--check`/`--uninstall`, CLAUDE.md snippet and subagent definition.
- Docker stack (`docker/`): Ollama with GPU (CPU override), one-shot model pull, squire service.
- Accuracy eval (`eval/`): 8 real-shaped logs with ground truth; recall 1.000, 0 false-clean on
  qwen2.5:14b. CI matrix 3.9–3.13, wheel + zipapp builds. `scripts/squire_report.py` measures
  real token usage from Claude Code transcripts.

### Security
- Backends restricted to localhost unless listed in `SQUIRE_ALLOW_HOSTS`; a remote URL is
  refused before any connection is made.
- Secret-shaped strings (private keys, GitHub/OpenAI/Anthropic/AWS/Slack tokens, JWTs,
  `password=`-style assignments) are redacted before any prompt or embedding request.

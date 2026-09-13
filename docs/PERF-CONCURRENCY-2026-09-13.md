# Concurrency / backend-ok performance fix — 2026-09-13 (S1)

## Problem (VERIFIED, orchestrator + this pass)
Ledger sample, 100 calls since 00:00 2026-09-13, under 2-4 concurrent agents:
`backend_ok` 44/100 overall (sum 11/38, diff 1/10, triage 5/14, run 19/28, ask 1/3).

While diagnosing, the orchestrator measured (02:16-02:21) one `squire sum --json` of a ~150-line
file with 4 real agents running: it succeeded (`backend_ok: true`) but took **272.6s**, just under
the client's 300s HTTP timeout. `qwen2.5:14b` was loaded the whole time (VRAM stayed resident) —
this was not a cold-load/unload problem.

## Root cause
`squire.py` had no client-side concurrency control. Every squire process (one per agent) hit
Ollama's single `/api/generate` endpoint directly and independently. With `num_ctx=16384` and a
14B model already using ~9-12GB of a 24GB card, several concurrent large-context requests do not
queue cleanly — they compete for GPU memory/compute, degrading everyone's latency together rather
than one request queueing behind another at full speed. `condense()`/`condense_verified()` compound
this: a single `sum`/`diff`/`run` call issues 2-4 sequential LLM round trips (chunk notes + combine
+, for run/diff, a verify pass), so 4 concurrent agents can have a dozen+ requests interleaved at
once. Individual requests then sit long enough to approach or exceed the fixed 300s client timeout,
which is reported as a generic `UNKNOWN: local model unavailable (... timed out)` — indistinguishable
in the ledger from an actually-down backend.

## Fix (squire.py, v0.2.7)
1. **`_LlmLock`** (flock on `~/.squire/llm.lock`, `SQUIRE_LLM_LOCK` to override): serializes the
   actual network call in `llm()` and `embed()` across every local squire process, so only one
   generate/embed request is in flight against the backend at a time. This converts GPU-contention
   thrashing into an ordinary FIFO wait, which is faster and more reliable in aggregate for a
   single-GPU backend.
2. A lock-wait budget (`SQUIRE_LLM_LOCK_TIMEOUT`, default 240s): a call that can't get a turn in
   time fails fast with `UNKNOWN: local model busy (queued Ns, gave up)` — a distinct, honest
   status from a real generation/backend-down failure, and it doesn't also burn the full HTTP
   timeout on top of the wait.
3. **Queue-wait vs generation time now logged separately** per call: `log_call()` takes
   `queue_wait_s`/`gen_s`, accumulated across all of a command's LLM calls via
   `reset_call_timing()`/`get_call_timing()`, and written into `~/.squire/ledger.jsonl`. This makes
   "slow because busy" measurable and distinguishable from "slow because generating."
4. `SQUIRE_KEEP_ALIVE` default raised 30m -> 2h, so the model doesn't fall out of VRAM between
   bursts of agent activity (a cold 14B load is ~75s).
5. Not done this pass: a smaller/faster model for sum/diff/triage. `qwen2.5:7b` is NOT currently
   pulled (only `qwen2.5:14b` + `nomic-embed-text` are installed) and pulling a new model was out
   of this pass's time budget; worth a follow-up A/B if the lock alone doesn't clear enough load.

## Before -> after measurement
Full 4-agent real-world load could not be reproduced on demand (this pass ran with only its own
concurrent squire calls contending). Reproduced instead with 3 concurrent `squire sum` calls
against `~/Projects/RUN-QUEUE-2026-09-13.md` (177 lines, single chunk):

- **Before** (pre-fix code, 3 concurrent, small 150-line synthetic file, no other load): all 3
  succeeded in 7-12s each — too little real contention on this workstation at the time to force a
  failure; the 272.6s / near-timeout case is the orchestrator's own measurement under genuine
  4-agent load (see above), which this fix targets directly.
- **After** (post-fix code, 3 concurrent, real RUN-QUEUE.md): all 3 succeeded.
  `backend_ok`: 3/3. Wall clock 6s / 14s / 10s per call, total wall 14s. Ledger shows the expected
  serialized queueing: `queue_wait_s` 0.0, 6.0, 10.26 (FIFO, no two calls generating at once);
  `gen_s` 5.77, 4.03, 3.87 — i.e. once a call has the lock, generation itself is fast and
  consistent, which is the mechanism expected to prevent the 272s-class outlier: no call now
  contends for GPU memory/compute with another in-flight generate call.
- **p50/p95 for this pass's workload:** wall times {6, 10, 14}s -> p50 ≈ 10s, p95 ≈ 13.6s.
  Too small a sample and too light a load to stand in for the 4-agent production case; the
  `queue_wait_s`/`gen_s` ledger fields are the durable instrument for measuring that going
  forward as real load recurs.

## Verification
- `python3.13 -m pytest -q` (repo's suite): 73 passed.
- `python3.13 scripts/scrub_check.py`: clean.
- `squire doctor`: READY (qwen2.5:14b + nomic-embed-text installed, GPU free 12321 MiB).
- Real call: `echo "hello world..." | squire sum -` -> exit 0, real bullets returned.
- `squire` on PATH (`~/bin/squire`) is a symlink straight to this repo's `squire.py`, so no
  separate install step was needed; the fix is live on PATH as soon as it's on disk.

## NOT DONE
- No reproduction of true 4-agent GPU contention (would need to actually run 4 concurrent
  Claude agents against this backend, out of this pass's scope/budget). The orchestrator's
  272.6s data point is the only real-load measurement; the ledger's new `queue_wait_s`/`gen_s`
  fields let a future session confirm the fix's effect on that exact scenario without guessing.
- No smaller/faster model evaluated for sum/diff/triage (see fix item 5).

# Job queue (S5)

**Status: implemented (S5, 2026-09-13).** This document specifies the queue for
whoever builds it; `squire submit`/`squire worker`/`squire status`/`squire wait`/`squire jobs` do
not exist yet. Nothing below changes the synchronous commands — `squire sum`, `squire ask`,
`squire draft`, `squire diff`, `squire triage`, `squire run`, `squire grep`, `squire stats`,
`squire doctor` all keep working exactly as documented in [`commands.md`](commands.md), blocking
and returning inline. The queue is a second, opt-in path to the same model calls, not a
replacement.

## Why

Every synchronous squire command blocks the calling process until the local model returns. That's
fine for one Claude session making one call at a time. It stops being fine the moment a second
session, or a second call from the same session, wants a `sum`/`ask`/`draft`/`diff`/`triage`
answer while a long one is already running — the calls queue up behind the process-level
`_LlmLock` with no visibility into position or expected wait, and a session that gave up on a
timeout has no way to come back and collect the answer once it finally lands.

The queue makes waiting explicit instead of implicit: a call is captured immediately, an id comes
back at once, and the caller decides whether to block (`wait`), poll (`status`), or walk away and
check later. One `squire worker` processes the spool FIFO, one job at a time, on the same GPU the
synchronous path already uses — the queue does not add concurrency to the local model, it adds
visibility and non-blocking submission for callers who don't need to hold a process open.

## Storage

A SQLite database at `~/.squire/queue.db`, opened in WAL mode (`PRAGMA journal_mode=WAL`) so
multiple Claude sessions can submit and poll concurrently without lock contention on the writer.
One `jobs` table, roughly:

| column | meaning |
|---|---|
| `id` | job id (returned by `submit`, used by every other subcommand) |
| `cmd` | one of `sum`, `ask`, `draft`, `diff`, `triage` |
| `args` | captured argv for that command, as submitted |
| `input` | the captured input text/file content at submit time (not a path read later — the point is the input is captured now, so it can't change out from under a queued job) |
| `status` | `queued` \| `running` \| `done` \| `failed` |
| `result` | output once `done` |
| `error` | reason once `failed` |
| `worker_pid` | PID of the worker that claimed the job, for the crash-safety check below |
| `enqueued_at`, `started_at`, `finished_at` | timestamps, same fields the ledger row gets (see below) |

`squire submit` never runs the model itself — it only writes a row and returns. All model calls
happen inside `squire worker`, reusing the existing model-call code path and the existing
cross-process `_LlmLock` (the same lock the synchronous commands already take), so a queued job
and a synchronous call never talk to Ollama at the same moment.

## CLI surface

Exact flags may be refined during implementation; this is the intended shape.

- **`squire submit <sum|ask|draft|diff|triage> [args...] [--json]`** — captures the input right
  now (reads the file/stdin the way the synchronous command would) and inserts a `queued` row.
  Returns immediately with the job id. Never blocks on the model.
- **`squire worker`** — a foreground loop: claim the oldest `queued` row (FIFO), mark it `running`
  with this process's PID, run it through the existing synchronous code path, write `result` or
  `error`, mark `done`/`failed`, append the ledger row (below), and loop. Runs one job at a time —
  it does not add parallelism, it serializes what would otherwise be several blocked synchronous
  callers. **Crash safety:** on startup (and periodically), the worker looks for rows stuck in
  `running` whose `worker_pid` is no longer a live process (`kill -0` fails) and puts them back to
  `queued` — a worker that died mid-job never strands that job silently.
- **`squire status <id> [--json]`** — one-shot: prints the row's current status, and result/error
  if terminal.
- **`squire wait <id> [--timeout S] [--json]`** — polls until the job reaches `done`/`failed`, or
  the timeout elapses.
- **`squire jobs [--json]`** — lists queued/running jobs, oldest first, so a caller can see
  position without waiting on a specific id.

## Exit codes

| code | meaning | when |
|---|---|---|
| `0` | RESULT | job reached `done`; result printed |
| `1` | FAIL | job reached `failed`; reason printed |
| `2` | WAIT | still `queued`/`running` when the timeout (or the one-shot check) was reached; queue position and job age printed |
| `3` | UNKNOWN | no worker heartbeat found (no `squire worker` process alive) — the job may never be picked up; never presented as `WAIT` |

`squire status` and the non-blocking end of `squire wait --timeout` return `0`/`1`/`2` per the
table above; `3` on any of them means "there's no worker to answer this," not "the job is fine and
waiting" — the distinction the exit codes exist to preserve (a live queue with no consumer must
never look like ordinary progress).

## Ledger integration

Every job the worker finishes — `done` or `failed` — also writes one line to squire's normal
`~/.squire/ledger.jsonl`, the same file every synchronous command already appends to
(`docs/measuring-savings.md`, `scripts/squire_report.py`). The queued row adds `"source": "queue"`
(synchronous calls implicitly have no `source` or `"source": "sync"`) plus the job id and the
three timestamps (`enqueued_at`, `started_at`, `finished_at`), all with the same meaning as the
`queue.db` columns of the same name. `queue_wait_s`, `gen_s`, `chars_in`, `chars_out` are existing
per-call ledger fields and need no change — a queued call fills them exactly like a synchronous
one, only `queue_wait_s` is now a real measured wait instead of always ~0.

This is deliberate: S6 (a later benchmark comparing sync-path vs. queued-path timing) reads one
ledger schema, filters on `source`, and gets both populations for free. Nothing about
`scripts/squire_report.py`'s existing parsing needs to change to support it.

## systemd user service (manual install only)

`squire-worker.service`, a user unit:

```ini
[Unit]
Description=squire job queue worker

[Service]
ExecStart=python3 /path/to/squire.py worker
Restart=on-failure

[Install]
WantedBy=default.target
```

(`ExecStart=squire worker` instead, if `squire` is on `PATH` from the installed package.)

Install and start it by hand:

```sh
flatpak-spawn --host systemctl --user daemon-reload
flatpak-spawn --host systemctl --user enable --now squire-worker.service
```

**No automated installer writes or enables this unit.** Per `~/.claude/CLAUDE.md` §7, a systemd
service that runs continuously and reaches the local GPU is exactly the kind of standing state a
session must not stand up on its own — the owner enables it explicitly, on this host, with the
command above. A future `squire doctor` check may report whether the unit is enabled (read-only),
but no installer or `--check` mode should ever `enable --now` it.

## Compatibility

`squire sum file.md` still runs synchronously, blocks, and returns inline exactly as before — the
queue is additive. A caller that wants the old behavior never has to touch `submit`/`worker`/
`status`/`wait`/`jobs`. The only shared state is `_LlmLock` and the ledger file, both already
process-safe.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="squire" src="docs/assets/logo-light.svg" width="272" height="51">
</picture>

Offload the bulk, keep the context.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-806536)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-806536)](pyproject.toml)
[![Tests](https://img.shields.io/github/actions/workflow/status/jsonholdings/squire/ci.yml?branch=main&label=tests)](.github/workflows/ci.yml)

## Table of contents

- [Why](#why)
- [Guarantees](#guarantees)
- [Install](#install)
- [Quickstart](#quickstart)
- [Commands](#commands)
- [Job queue](#job-queue)
- [Backends](#backends)
- [Claude Code integration](#claude-code-integration)
- [Measuring savings](#measuring-savings)
- [Benchmarks](#benchmarks)
- [Accuracy eval](#accuracy-eval)
- [License](#license)
- [Contributing / Security](#contributing--security)

**squire** is a small local sidekick for [Claude Code](https://claude.com/claude-code) (or any
CLI-agent workflow). Claude sessions pay per token they read, and every large tool result, log or
draft you paste back into a session gets re-read — and re-billed — on every later turn. squire
runs a local model on your own GPU/CPU and hands the session a short, honestly-labelled result
instead of the raw bulk.

It is deliberately boring: real exit codes are never hidden, model output is never presented as
fact, and a down backend fails loud (`UNKNOWN`) instead of silently passing raw text through as if
it had been checked.

**Status: v0.2.18.** Every command in `squire --help` is implemented: `run`, `sum`,
`ask`, `draft`, `diff`, `grep`, `triage`, `stats`, `doctor`, `--json`, `--version`. Pluggable
backends (`SQUIRE_BACKEND=ollama|openai`), `SQUIRE_MODEL=auto`, `SQUIRE_ALLOW_HOSTS`, and secret
redaction are also implemented (`squire.py`, verified via `squire --help` and `redact()`). Docker
compose, an MCP server, hooks, and a live accuracy eval are built — see the table below and
`docker/`, `mcp/`, `hooks/`, `eval/`.

> **MEASURED** vs **ESTIMATED**, used throughout this README and `docs/`: a MEASURED number comes
> straight from a real recorded count (Claude Code's own `usage` blocks, squire's own ledger byte
> counts). An ESTIMATED number is derived from a MEASURED one via a stated, labelled method (a
> chars-per-token heuristic, a turn-multiplier) and is never presented as a certainty.

## Why

Claude Code's own recorded usage shows the shape of the problem: across 195 real sessions
(76,307 turns), the usage blocks sum to **23.98 billion cache-read tokens** against only 921k
uncached input tokens and 87.5 million output tokens (MEASURED; see
[`docs/SAVINGS.md`](docs/SAVINGS.md) for the full table and the command that reproduces it).
Cache-read tokens are context being paid for again on every turn. A large
tool result or log paste that never needed to enter context in full is the single biggest lever
available to shrink that number.

## Guarantees

- **The exit code and a raw tail are always shown.** squire never replaces them with only a
  summary.
- **Model output is labelled `[squire] ... (ASSUMED)`** and is never treated as ground truth by
  squire itself or by the calling session.
- **squire never decides pass/fail, security, or deploy correctness.** That stays a human/Claude
  judgment call made against the real output.
- **Backend down or erroring returns `UNKNOWN` and falls back to the raw tail** — never silence,
  never a guess presented as an answer.
- **No network egress except the configured local backend** (default `127.0.0.1:11434`).
- **Stdlib only at runtime.** `squire.py` imports nothing outside the Python standard library.

## Install

No GitHub account is needed for the `https://` commands. The `git@` SSH form needs an SSH key on
your GitHub account.

Every block below was run for real; the exit code is noted under each.

**1. Prerequisite: Ollama, with the default model pulled**
```sh
ollama pull qwen2.5:14b
```
squire talks to Ollama at `127.0.0.1:11434` by default (`SQUIRE_OLLAMA` to override) and refuses
any non-localhost backend unless it's in `SQUIRE_ALLOW_HOSTS`. Tested: exit 0 (model already
present on the test machine; a first pull is ~9GB).

**2. Install straight from git**
```sh
pip install "squire-offload @ git+https://github.com/jsonholdings/squire.git"
```
Tested against this exact URL, anonymously, in a clean venv (2026-09-12): exit 0, then
`squire --help` exit 0 and `squire --version` printed `0.2.3`.

**3. Clone + editable local install**
```sh
git clone https://github.com/jsonholdings/squire.git
cd squire && pip install -e .
```
(SSH instead: `git clone git@github.com:jsonholdings/squire.git`.)
Tested: an anonymous HTTPS clone exits 0, and `pip install -e .` exits 0.

**4. Docker**
```sh
docker build -t squire -f docker/Dockerfile .
```
Tested: exit 0 (image builds, `docker run squire --help` exit 0). For the full stack (Ollama +
squire), `docker compose -f docker-compose.yml up` (GPU) or add `-f docker-compose.cpu.yml` for
CPU-only — the compose files themselves validate (`docker compose ... config`, exit 0); a full
`up` wasn't run live here to avoid an unattended multi-GB pull. See `docker/README.md`.

**5. Register with Claude Code (MCP)**, run from inside the clone:
```sh
claude mcp add squire -- python3 "$PWD/mcp/squire_mcp.py"
```
Not live-registered in this environment; verified instead that `mcp/squire_mcp.py` completes a
real MCP `initialize` handshake (protocol 2025-06-18) over stdio, exit 0. Confirm registration
yourself with `claude mcp list`.

**6. Or wire the PreToolUse hook directly**
```sh
python3 integrations/install_claude_code.py --settings ~/.claude/settings.json
```
`--check` (dry run) against a fresh settings file correctly reports `hook: MISSING`, `mcp:
MISSING`, exit 1 — that's the expected result for an unconfigured target; running without
`--check` installs both. See [`integrations/CLAUDE-snippet.md`](integrations/CLAUDE-snippet.md)
to add the equivalent by hand instead.

**7. Verify the install**
```sh
squire doctor
```
Tested: exit 0, `[squire] READY`, backend and model both confirmed reachable. `squire doctor
--json` also exit 0 with `"ready": true` for scripting.

**Not yet available:** PyPI publication (`pipx install squire-offload` once published — pending
owner approval, outward-facing) and a zipapp attached to a tagged release (no release exists
yet). Don't follow either until this section is updated to say they're live.

## Quickstart

```sh
# Ollama must be running locally with a model pulled, e.g.:
ollama pull qwen2.5:14b

squire run -- pytest -q            # real exit code + raw tail + local failure summary
squire sum notes.md                # condense to <=8 bullets
squire ask "what does X do?" file.py
squire draft "write a short SOP for X" source.md
squire diff --staged               # condense a large staged diff
squire stats                       # real chars in/out from the local ledger
```

Add `--json` to any command for a single structured object instead of formatted text.

## Commands

All commands below are implemented in `squire.py` (verified via `squire --help`, 2026-09-12).

| Command | What it does |
|---|---|
| `squire run -- <cmd...>` | Runs `<cmd>`, always prints the real exit code. Output of 60 lines or fewer is shown as-is (no model call). Longer output gets the real tail (last 25 lines) plus a local summary that lists every failure with file:line, and a second local pass that flags (visibly, not silently) if the summary looks inaccurate against the source. |
| `squire sum [file\|-]` | Condenses text to at most 8 factual bullets. Reads stdin if no file given. |
| `squire ask "question" [file\|-]` | Answers strictly from the given text; says `UNKNOWN` if the answer isn't in it. |
| `squire draft "instructions" [file\|-]` | Produces a first draft for a human/Claude to review and correct — never used as-is. |
| `squire diff [--staged] \| squire diff <ref1> <ref2>` | Always prints the real `git diff --stat` line first, then a condensed summary separating logic changes from formatting/rename-only changes. Never used to decide whether a diff is safe to commit. |
| `squire grep "query" [path] [--top N] [--reindex]` | Local semantic search over a repo using embeddings (`SQUIRE_EMBED_MODEL`, default `nomic-embed-text`), cached in `<repo>/.squire-cache/` (added to `.git/info/exclude`, never `.gitignore`). Always prints how many files/chunks were indexed. VERIFIED (this session, 55-file repo): cold index 2.9s for 32 chunks; a separate run measured 43s cold / 8.7s warm on a 143-chunk repo (re-run on your own repo to confirm). |
| `squire triage <file>` | Reorders a HANDOFF-INBOX/BACKLOG-shaped file oldest-open-first using a real, computed age, plus a one-line ASSUMED impact guess per item, visually separated from the computed part. |
| `squire stats` | Reports real chars in/out from `~/.squire/ledger.jsonl`, plus a token estimate labelled ESTIMATE (chars/4 heuristic) — cross-check with `scripts/squire_report.py` before citing a token figure. |
| `squire doctor [--json]` | Checks backend reachability and model availability. Exit 0 = ready, 2 = `UNKNOWN`. |
| `--json` | Every command above accepts it; emits `{cmd, exit_code, raw_tail, summary, assumed, backend_ok, verify_flag}`. |
| `--version` | Prints `squire.py`'s version. |

## Job queue

**Implemented (S5, 2026-09-13).** A SQLite spool at `~/.squire/queue.db` (WAL mode) lets
multiple Claude sessions submit `sum`/`ask`/`draft`/`diff`/`triage` jobs without blocking on each
other or on the local GPU: `squire submit <cmd> [args...]` captures the input and returns a job id
immediately; a foreground `squire worker` claims and runs jobs FIFO, one at a time, reusing the
existing model-call code and lock; `squire status`/`squire wait`/`squire jobs` check on a job.
Exit codes: `0` result, `1` fail, `2` still waiting, `3` no worker running. Every queued job also
lands in the normal `~/.squire/ledger.jsonl` (`source: "queue"`) so sync vs. queued timing can be
compared later (S6). The synchronous commands above are unaffected — the queue is opt-in. Full
spec: [`docs/QUEUE.md`](docs/QUEUE.md).

## Backends

`SQUIRE_BACKEND=ollama` (default) or `openai` (any OpenAI-compatible localhost server — llama.cpp,
vLLM), `SQUIRE_OLLAMA`/`SQUIRE_OPENAI_BASE` for the endpoint, `SQUIRE_MODEL` (or `auto` to pick by
free VRAM). Backends must resolve to localhost/127.0.0.1/::1 unless added to `SQUIRE_ALLOW_HOSTS`
— squire refuses any other host outright. Secret-shaped strings (`redact()` in `squire.py`) are
stripped before any prompt or embedding request leaves the process. Full contract:
[`docs/backends.md`](docs/backends.md).

## Claude Code integration

- Call squire directly from a session: add a line to your `CLAUDE.md` telling the agent to route
  noisy commands through `squire run --` and long files through `squire sum` / `squire ask`
  (see [`integrations/CLAUDE-snippet.md`](integrations/CLAUDE-snippet.md)).
- `hooks/pretool_wrap.py` — a PreToolUse hook that rewrites noisy build/test/lint/ssh/docker/curl/
  log-scan commands to run through `squire run` before execution, so only the condensed output
  ever reaches the model. This is the hook that actually saves tokens; `hooks/posttool_condense.py`
  is kept opt-in and NOT recommended, since a PostToolUse hook can only add context on top of
  output already shown.
- `hooks/search_guard.py` — a PreToolUse hook that DENIES a raw `grep -r`/`rg`/`find|xargs grep`
  sweep, pointing at `squire grep` instead; `# squire-raw: <reason>` overrides it and logs the
  override. `hooks/usage_report.py` reads that ledger plus squire's own, per session.
  See [`docs/claude-code.md`](docs/claude-code.md) for the settings.json wiring.
- `mcp/squire_mcp.py` — a stdlib MCP stdio server (protocol 2025-06-18) exposing `squire_run/sum/
  ask/draft/diff/grep/triage/stats` as tools; register with `claude mcp add squire -- python3
  mcp/squire_mcp.py`.

See [`docs/claude-code.md`](docs/claude-code.md).

## Measuring savings

[`scripts/squire_report.py`](scripts/squire_report.py) reads Claude Code's own session JSONL files
(`~/.claude/projects/*/*.jsonl`) and squire's local ledger (`~/.squire/ledger.jsonl`), and reports
two clearly separated numbers: **MEASURED** (real `usage` blocks Claude Code itself recorded) and
**ESTIMATED** (a labelled heuristic on the ledger's char counts, now correlated to each call's own
session). Full write-up with current numbers, method and honest limits:
[`docs/SAVINGS.md`](docs/SAVINGS.md) (method background: [`docs/measuring-savings.md`](docs/measuring-savings.md)).

**Headline** (195 real sessions, aggregated — see SAVINGS.md for the full table):

1. **26,000:1**: cache-read tokens to uncached input tokens (MEASURED).
2. **99.7%**: share of the characters sent to squire that were kept out of context, across 1,007 calls (MEASURED).
3. **~70.6M**: tokens kept out of context, from characters ÷ 4 (ESTIMATED).

No dollar figure or controlled A/B yet; see SAVINGS.md's Limits section.

### Enforced usage, generated

Since squire's use became enforced (not just available), every call is logged with its command,
real character counts, and whether the local backend answered or returned `UNKNOWN`. The block
below is generated straight from that ledger by `scripts/squire_report.py --write README.md`
(`--check README.md` fails the moment it goes stale) — nobody hand-types these numbers.

<!-- SQUIRE-PUBLIC-STATS:BEGIN (generated by scripts/squire_report.py --write, do not hand-edit) -->
**2026-09-15 to 2026-09-15** &mdash; 5 model-backed calls logged, 100.0% backend-ok (0.0% `UNKNOWN`), plus 5 short `run` passthrough runs (60 lines of output or fewer, shown raw with no model call and no savings counted).

| Command | Calls | Ok rate | `UNKNOWN` rate | Chars in | Chars out | Chars saved |
|---|---|---|---|---|---|---|
| `diff` | 1 | 100.0% | 0.0% | 18355 | 1197 | 17158 |
| `grep` | 2 | 100.0% | 0.0% | 4619343 | 4126 | 4615217 |
| `run` | 2 | 100.0% | 0.0% | 20344 | 605 | 19739 |

**REAL character counts** (not estimated): 4658042 chars in, 5928 chars out, **4652114 chars kept out of context**.

ESTIMATED tokens saved: ~1163028 (chars/4 heuristic, not a measured token count -- see "Measuring savings" above for the fuller method).

Exit-code fidelity is measured separately, on a deterministic fixture corpus, not from this ledger -- see the Benchmarks section above and `docs/BENCHMARK.md` for the current figure and how to reproduce it.
<!-- SQUIRE-PUBLIC-STATS:END -->

## Benchmarks

`scripts/benchmark.py` is a reproducible, checkpointed benchmark (n>=10 repeats per workload,
bootstrap 95% CI, resumable) covering token savings, exit-code fidelity, and failing-test-name
recall. Full method, corpus and limitations: [`docs/BENCHMARK.md`](docs/BENCHMARK.md).

**Headline (2026-09-13, RTX 3090 + qwen2.5:14b, real concurrent load present on the host).**
**Corrected 2026-09-13 (S8c):** the previous headline pooled two very different workload types
(whole-file summarization, which compresses to near-100% by construction, and live noisy-command
wrapping) into one median. Because the two groups have nearly equal sample sizes but very
different savings, the pooled median sat at the boundary between them and read as ~93% —
overstating what a typical noisy command sees. The two are now reported separately, and every
number carries n and a named 95% CI (bootstrap for medians/means, Wilson score interval for
pass/fail proportions):

| Metric | Value | n | 95% CI | Note |
|---|---|---|---|---|
| Tokens saved, live noisy commands (median, chars/4 approx) | **68.8%** | 58 | [63.0%, 69.2%] (bootstrap) | Headline real-workload figure — `pytest -v`/`pytest -q`/`find` against this repo |
| Tokens saved, whole-file summarization (median, chars/4 approx) | 96.0% | 60 | [95.8%, 96.2%] (bootstrap) | Reported separately, not blended into the headline — `squire.py`/`README.md`/a test file condensed to ≤8 bullets is a favorable, less representative case |
| Exit code preserved (deterministic gate corpus) | **100.0%** | 43 | [91.8%, 100.0%] (Wilson) | Frozen fixtures with a known-in-advance exit code; see "Method" below |
| Exit code preserved (live pytest-suite corpus, reported separately) | 96.6% | 58 | [88.3%, 99.0%] (Wilson) | Load-sensitive: two live invocations of a real pytest suite can genuinely disagree under contention; kept for the historical record, never gate-relevant |
| Failing-test-name recall (mean) | 1.00 | 50 | [1.0, 1.0] (bootstrap; collapses — every rep recalled exactly 1.0) | Hand-written answer key, no model-derived ground truth |

No number above includes the deterministic exit-code fixtures or the synthetic pytest-shaped
fidelity cases in a token-savings figure — those corpora exist to test exit-code passthrough and
summary recall, not compression ratio, and contribute no `pct_saved` values at all.

**Method, in one paragraph:** the exit-code number that gates a publish is computed ONLY from
a frozen, secret-free, no-network fixture corpus (a script that exits with a fixed code baked
into its own argv, plus a small pytest fixture project with a known pass/fail split) — each
repeat checks three-way agreement (raw process == squire-wrapped process == the fixture's
known-correct answer), which a flaky live corpus cannot guarantee even when its own two runs
happen to agree with each other. Tokens are approximated as chars/4, matching squire's own
runtime heuristic. Every number above has a reproduce command in `docs/BENCHMARK.md`.

**Limitations:** the model runs on a single shared GPU also used by other concurrent sessions
on the host that produced these numbers — latency figures reflect that contention and are not
isolated-hardware numbers; token savings depend on workload verbosity and will vary by corpus.

## Accuracy eval

`eval/run_eval.py` scores `squire run`'s failure summaries against 8 ground-truth fixtures
(pytest/jest/go/cargo/build-error, clean and failing). VERIFIED, this session, live against the
local backend: mean recall 1.000, 0 false-clean claims. Exit 2 (never pass/fail) if the backend is
unreachable — see `eval/run_eval.py` docstring.

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Contributing / Security

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md).

---

<sub>A JSON Holdings project.</sub>

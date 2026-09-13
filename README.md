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
- [Backends](#backends)
- [Claude Code integration](#claude-code-integration)
- [Measuring savings](#measuring-savings)
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

**Status: v0.2.3, pre-release.** Every command in `squire --help` is implemented: `run`, `sum`,
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

Claude Code's own recorded usage shows the shape of the problem: across 186 real sessions
(70,352 turns), the usage blocks sum to **21.77 billion cache-read tokens** against only 792k
uncached input tokens and 78.6 million output tokens (MEASURED, 2026-09-12; see
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
- `hooks/pretool_wrap.py` — a PreToolUse hook that rewrites noisy build/test/lint commands to run
  through `squire run` before execution, so only the condensed output ever reaches the model. This
  is the hook that actually saves tokens; `hooks/posttool_condense.py` is kept opt-in and
  NOT recommended, since a PostToolUse hook can only add context on top of output already shown.
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

**Headline, 2026-09-12** (186 real sessions, aggregated — see SAVINGS.md for the full table):
cache-read tokens outnumber uncached input by roughly 27,500:1 (MEASURED), and squire's own ledger
shows 96% of the characters sent to it were kept out of context entirely (MEASURED chars saved),
with an ESTIMATED 18.7M cache-read tokens avoided across the 38% of calls correlated to a session
so far — an upper bound for that minority of usage, not a total. No dollar figure or controlled A/B yet;
see SAVINGS.md's Limits section.

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

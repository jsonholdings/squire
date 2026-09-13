# squire

**squire** is a small local sidekick for [Claude Code](https://claude.com/claude-code) (or any
CLI-agent workflow). Claude sessions pay per token they read, and every large tool result, log or
draft you paste back into a session gets re-read — and re-billed — on every later turn. squire
runs a local model on your own GPU/CPU and hands the session a short, honestly-labelled result
instead of the raw bulk.

It is deliberately boring: real exit codes are never hidden, model output is never presented as
fact, and a down backend fails loud (`UNKNOWN`) instead of silently passing raw text through as if
it had been checked.

**Status: v0.2.0, pre-release.** Every command in `squire --help` is implemented: `run`, `sum`,
`ask`, `draft`, `diff`, `grep`, `triage`, `stats`, `doctor`, `--json`, `--version`. Pluggable
backends (`SQUIRE_BACKEND=ollama|openai`), `SQUIRE_MODEL=auto`, `SQUIRE_ALLOW_HOSTS`, and secret
redaction are also implemented (`squire.py`, verified via `squire --help` and `redact()`). Docker
compose, an MCP server, hooks, and a live accuracy eval are built — see the table below and
`docker/`, `mcp/`, `hooks/`, `eval/`.

## Why

Anthropic's own usage accounting shows the shape of the problem: on one real, measured Claude Code
project on this machine, 34 sessions totalling 70,030 turns carried **21.7 billion cache-read
tokens** against only 785k billed input tokens and 78.0 million output tokens (VERIFIED,
2026-09-12 — see [`docs/measuring-savings.md`](docs/measuring-savings.md) for the exact command
and full breakdown). Cache-read tokens are context being paid for again on every turn. A large
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

Packaging (wheel + zipapp) is built and VERIFIED; PyPI publication itself is pending owner
approval to publish (outward-facing, see `CLAUDE.md`).

**From source:**
```sh
git clone <repo-url> squire && cd squire
python3 squire.py --help
ln -s "$(pwd)/squire.py" ~/bin/squire   # optional: put it on PATH
```

**pipx (once published to PyPI):**
```sh
pipx install squire-cli
```

**Single-file zipapp:** built via `pyproject.toml`'s packaging — `python3 squire.pyz --help`, no
install step. Attached to each tagged GitHub release once one exists.

**Docker:** `cd docker && docker compose up` (GPU) or add `-f docker-compose.cpu.yml` for CPU-only.
Brings up Ollama healthchecked on `127.0.0.1:11434`, a one-shot model-pull job, and runs `squire`
as a one-off container (`docker compose run --rm squire ...`). VERIFIED: `docker compose config`
validates and a live Ollama health check passed on an alternate port during testing. See
[`docs/backends.md`](docs/backends.md) and `docker/README.md`.

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
| `squire grep "query" [path] [--top N] [--reindex]` | Local semantic search over a repo using embeddings (`SQUIRE_EMBED_MODEL`, default `nomic-embed-text`), cached in `<repo>/.squire-cache/` (added to `.git/info/exclude`, never `.gitignore`). Always prints how many files/chunks were indexed. VERIFIED (this session, 55-file repo): cold index 2.9s for 32 chunks; the eval worker separately measured 43s cold / 8.7s warm on a 143-chunk repo (relayed, not independently reverified here — re-run on your own repo to confirm). |
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

- Call squire directly from a session (global `CLAUDE.md` §5.8).
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
two clearly separated numbers: **VERIFIED** (real `usage` blocks Claude Code itself recorded) and
**ESTIMATE** (a labelled heuristic on the ledger's char counts). Full method, caveats and how to
run it on your own transcripts: [`docs/measuring-savings.md`](docs/measuring-savings.md).

**Real numbers measured on this machine, 2026-09-12** (VERIFIED, one project's 34 sessions):

| Metric | Value | Source |
|---|---|---|
| Turns | 70,030 | `usage` blocks, summed |
| Cache-read tokens | 21,713,327,662 | `usage` blocks, summed |
| Cache-creation tokens | 315,121,245 | `usage` blocks, summed |
| Input tokens (uncached) | 784,617 | `usage` blocks, summed |
| Output tokens | 77,985,694 | `usage` blocks, summed |

squire's own ledger on the same machine (23 calls logged so far) shows 10,893 chars in vs. 1,552
chars out, an ESTIMATED one-time saving of ~2,335 tokens (chars/4 heuristic) — small so far because
the ledger is new; it grows with use. **The multiplier from one-time chars-saved to actual
cache-read tokens avoided across a session's remaining turns is not yet computed** — the ledger
does not currently record which session a call belonged to, so that correlation is reported as
`UNKNOWN` rather than guessed. See `docs/measuring-savings.md` for what closing that gap requires.

## Accuracy eval

`eval/run_eval.py` scores `squire run`'s failure summaries against 8 ground-truth fixtures
(pytest/jest/go/cargo/build-error, clean and failing). VERIFIED, this session, live against the
local backend: mean recall 1.000, 0 false-clean claims. Exit 2 (never pass/fail) if the backend is
unreachable — see `eval/run_eval.py` docstring.

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Contributing / Security

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md).

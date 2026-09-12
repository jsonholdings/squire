# squire — Claude Code's local sidekick (the squire who carries the load) (local offload, 2026-09-12)

Claude sessions pay per token read. `squire` does bulk text work on this workstation's
RTX 3090 (Ollama `qwen2.5:14b`, `127.0.0.1:11434`), so a session reads a short result instead of
the raw bulk. The policy for when to use it is global CLAUDE.md §5.8.

| Command | Use |
|---|---|
| `squire run -- <cmd>` | Test suites, builds, log scans. Shows the real exit code, the raw tail and a local failure summary. Output of 60 lines or fewer passes through with no model. |
| `squire sum [file\|-]` | Condense to ≤8 factual bullets. |
| `squire ask "q" [file\|-]` | Answer from the given text only; says UNKNOWN otherwise. |
| `squire draft "instr" [file\|-]` | First draft for Claude to review. |

**Guarantees:** the exit code and raw tail are always printed, so the model never decides pass or
fail. Model output is ASSUMED. Ollama down means `UNKNOWN`, never silence. `num_ctx` is set to
16384 (Ollama's default of 2048 truncates silently). Large inputs are chunked and merged. It's
stdlib only. Installed as the symlink `~/bin/squire`.

Tunables: `SQUIRE_MODEL`, `SQUIRE_OLLAMA`, `SQUIRE_CTX`.

## Install

Requires Python 3.9+ and a local [Ollama](https://ollama.com) install with a model pulled
(default `qwen2.5:14b`). No other runtime dependencies.

```sh
pip install -e .
squire --help
```

## Safety guarantees

- The exit code and raw tail of a wrapped command are **always** shown; `squire` never decides
  pass or fail.
- Model output is prefixed `[squire]` and is **ASSUMED**, not verified, until a human or the
  calling session checks it.
- If the backend is unreachable, the result says `UNKNOWN` and still shows the raw tail — never
  silence and never a fabricated answer.
- Stdlib only at runtime; talks only to the local backend you configure (default
  `127.0.0.1:11434`); no telemetry, no other network egress.

## Benchmarks (TBD)

Real token-savings numbers have not been measured for this standalone extraction yet. The
methodology (parsing Claude Code session JSONL for cache-read tokens avoided, a controlled A/B on
scripted tasks, and a counterfactual ledger per call) is specified but not implemented — see the
open-source spec in this project's issue tracker once published.

| Task | Without squire (tokens) | With squire (tokens) | Saved |
|---|---|---|---|
| TBD | TBD | TBD | TBD |


## Benchmarks (TBD)

Here is the markdown table structure with the specified columns and placeholder rows:

```markdown
| Task               | Tokens without squire | Tokens with squire | Percent saved |
|:------------------:|:---------------------:|:------------------:|:-------------:|
| TBD                |           TBD         |          TBD        |      TBD      |
| TBD                |           TBD         |          TBD        |      TBD      |
| TBD                |           TBD         |          TBD        |      TBD      |
```

## Usage

```bash
squire run -- <command...>   # run a command; show its REAL exit code, raw tail, and a local summary of failures (short output is printed as-is, no model)
squire sum [file|-]          # condense text to a few bullets
squire ask "question" [file|-]   # answer a question from the given text only
squire draft "instructions" [file|-]  # first draft for Claude to review
```

### Rules built in (global CLAUDE.md section 5.8):
- Exit codes and the raw tail are ALWAYS printed. The model never decides pass or fail.
- Model output is labelled [squire] and is ASSUMED until the session checks it.
- Ollama down or erroring means the output says UNKNOWN and falls back to the raw tail, never silence.
- Stdlib only: runs on any python3 on this machine.

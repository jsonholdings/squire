# Claude Code integration

## Direct use
Call squire like any other CLI tool from a Claude Code session or agent:
```sh
squire run -- pytest -q
squire sum some-long-log.txt
squire diff --staged
squire grep "how does X work" .
```
CLAUDE.md snippet for a project that wants this as a standing rule:
```markdown
## Local offload
Use `squire run -- <cmd>` for noisy test/build/install output, `squire sum`/`squire ask` for long
files, `squire grep` before an Explore-agent search, and `squire draft` for first-draft text. The
exit code and raw tail always decide pass/fail — squire's summary is a convenience, never the
verdict. Never use it for security or deploy judgement.
```

## PreToolUse hook (`hooks/pretool_wrap.py`)
Rewrites a noisy Bash command to run through `squire run` **before** it executes, so only the
condensed output (real exit code + tail + summary) ever reaches the model — this is the hook that
actually reduces tokens, per the Claude Code hooks docs: a PreToolUse hook may return
`updatedInput` and replace the tool call outright. Only wraps known-noisy build/test/lint/install
commands; skips anything already piped through head/tail/grep, anything interactive, heredocs, and
squire/git/session-claim/cat/sed/grep/ls themselves. `SQUIRE_HOOK_DISABLE=1` disables it entirely.

## PostToolUse hook (`hooks/posttool_condense.py`) — opt-in, not recommended
A PostToolUse hook can only **add** context on top of output already shown to the model — it
cannot remove or replace what already landed in the transcript. Installing this hook makes context
usage worse, not better (the full original output still lands, plus a summary on top). Kept for
the rare case where the raw output must be preserved verbatim (e.g. auditing) and a bolted-on
summary is still wanted for convenience. Use `pretool_wrap.py` instead by default.

## MCP server (`mcp/squire_mcp.py`)
Stdlib-only MCP stdio server (protocol 2025-06-18) exposing `squire_run`, `squire_sum`,
`squire_ask`, `squire_draft`, `squire_diff`, `squire_grep`, `squire_triage`, `squire_stats` as
tools. Shells out to the `squire` CLI with `--json` — never imports squire internals.

```sh
claude mcp add squire -- python3 /path/to/squire/mcp/squire_mcp.py
claude mcp list   # squire should show as connected
```
Set `SQUIRE_BIN` if `squire` is not on PATH and not found next to `squire_mcp.py` (lookup order:
`$SQUIRE_BIN`, `squire` on PATH, `../squire.py`).

## Subagent definition — planned
An optional subagent config that routes bulk-text subtasks to squire by default. Not built yet.

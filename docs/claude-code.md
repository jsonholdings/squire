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

## PreToolUse enforcement hooks

Two hooks turn "use squire" from a request into something the harness actually enforces.
Both run for the main session **and every subagent** — a subagent reads the same
`~/.claude/settings.json`, so no extra wiring per agent brief is needed for the enforcement
itself (only for the agent's brief to say squire is expected). Wire them into
`~/.claude/settings.json` (or your project's `.claude/settings.json`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "/path/to/squire/hooks/pretool_wrap.py", "timeout": 5}]
      },
      {
        "matcher": "Grep|Bash",
        "hooks": [{"type": "command", "command": "/path/to/squire/hooks/search_guard.py", "timeout": 5}]
      }
    ]
  }
}
```

### `hooks/pretool_wrap.py` — wrap noisy commands
Rewrites a noisy Bash command to run through `squire run` **before** it executes, so only the
condensed output (real exit code + tail + summary) ever reaches the model — this is the hook that
actually reduces tokens, per the Claude Code hooks docs: a PreToolUse hook may return
`updatedInput` and replace the tool call outright. Wraps known-noisy build/test/lint/install
commands, plus `ssh`, `docker`, `flatpak-spawn`, `curl`, and log scans (`journalctl`, `dmesg`).
Skips anything already piped through head/tail/grep (already trimmed), anything interactive,
heredocs, and squire/git/session-claim/cat/sed/grep/ls themselves.

### `hooks/search_guard.py` — deny raw search sweeps
DENIES (not a nudge — `permissionDecision: "deny"`) a broad raw search sweep before it runs:
`grep -r`/`-R`, a bare `rg` invocation, or `find … | xargs grep`, pointing the caller at
`squire grep "<question>" [path]` instead. A single explicit `grep <pattern> <one-file>` is
never denied. The Grep tool itself carries no command string to attach a marker to, so an
unnarrowed Grep call (no `path`/`glob`) is allowed through with `additionalContext` rather than
denied, and logged for visibility instead.

### Escape hatch: `# squire-raw: <reason>`
Add this literal marker anywhere in a Bash command to skip both hooks for that one call — for a
command that genuinely needs to run unwrapped or an intentional one-off raw search. Every use is
logged to `~/.squire/raw_overrides.jsonl` (timestamp, session, tool, reason, command head) so a
pattern of overriding is visible rather than silently normalized.

### Kill switch and failure mode
`SQUIRE_HOOK_DISABLE=1` disables both hooks entirely. Both fail OPEN — on malformed JSON input,
squire not being found, or any unexpected exception, the hook prints nothing and the original
tool call proceeds unmodified. A hook that can block a tool call it fails to evaluate is worse
than no hook.

### Reading usage: `hooks/usage_report.py`
```sh
python3 hooks/usage_report.py                        # portfolio-wide summary
python3 hooks/usage_report.py --session <session-id>  # one session: calls by cmd + raw overrides
python3 hooks/usage_report.py --json                  # machine-readable
```
Reports real squire calls by command (excluding squire's own test-fixture rows), an ok-rate,
characters saved, and — per session — how many times `# squire-raw:` was used, so overuse of the
escape hatch shows up next to genuine usage rather than nowhere.

### Vendoring these hooks into another repo
These three files (`pretool_wrap.py`, `search_guard.py`, `usage_report.py`) are the canonical
source. A consumer that deploys Claude Code config from its own repo should vendor a copy (with
a header noting where it came from) and add a staleness check (`cmp` against this source) to its
own install/check script, rather than hand-editing a divergent copy — see this project's own
`workstation-config/sync_squire_hooks.sh` for a worked example of that pattern.

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

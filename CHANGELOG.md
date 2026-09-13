# Changelog
All notable changes to Squire are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]
### Added
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

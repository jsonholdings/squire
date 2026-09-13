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

### Not done
- `squire grep` (semantic search), `squire triage`, PostToolUse hook, Docker stack, MCP server,
  backend abstraction beyond Ollama — see `SPEC-LOCAL-OFFLOAD-TOOLING.md` and
  `SPEC-OPEN-SOURCE.md`.

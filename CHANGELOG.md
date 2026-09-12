# Changelog
All notable changes to Squire are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]
### Added
- `squire run -- <cmd>`: real exit code, raw tail and a local failure summary. Output of 60 lines
  or fewer passes through with no model.
- `squire sum`, `squire ask`, `squire draft`: condense, answer and draft using a local Ollama model.
- Safety guarantees: backend down → `UNKNOWN`, model output labelled ASSUMED, `num_ctx` set
  explicitly, large inputs chunked.
- Project scaffolding: tests, CI, scrub check, CONTRIBUTING, SECURITY, pyproject.
- Extracted to a standalone repo skeleton: README install/safety/benchmark-TBD sections,
  LICENSE-PENDING.md placeholder (MIT vs Apache-2.0 undecided).

### Not done (future work, out of scope for this extraction)
- CLI expansion: `grep` (semantic search), `diff` (summarize a git diff), `triage`, `--json`
  output for agents.
- Backend abstraction beyond Ollama (llama.cpp server, vLLM, OpenAI-compatible endpoints);
  model auto-pick by VRAM; auto `num_ctx`.
- Docker Compose stack (`squire-stack`, GPU/CPU Ollama, API, health endpoint).
- Claude Code integration: MCP server, PostToolUse hook example, CLAUDE.md snippet, subagent
  definition.
- Metrics/ledger and `squire stats`.
- Eval set with a recall target for failures, beyond the four safety-control unit tests.
- Publishing: no LICENSE chosen, no GitHub remote added, no push — owner sign-off required.

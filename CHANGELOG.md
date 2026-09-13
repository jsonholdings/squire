# Changelog
All notable changes to Squire are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]
### Fixed
- Tests: the `squire diff` tests' temporary git repos now carry their own identity and ignore
  the machine's git config (`GIT_CONFIG_GLOBAL=/dev/null`, no signing). CI runners have no
  `user.name`/`user.email`, so `git commit` exited 128 and CI failed on every push; the tests had
  passed only on machines with a global identity. Reproduced locally with no identity before fixing.

### Added
- Branding: hand-written SVG logo/wordmark (light + dark, `docs/assets/`), icon-only mark,
  1280x640 social-preview source + rendered PNG, tagline "Offload the bulk, keep the context."
  README gets a logo header, license/python/tests badges, a table of contents, and a
  MEASURED/ESTIMATED callout convention; footer credits "A JSON Holdings project".
- README "Install" section rewritten as tested, numbered command blocks (prerequisites,
  git-install, clone+editable install, Docker build, MCP registration, hook install, `squire
  doctor` verify), each block's real exit code noted, PyPI/zipapp explicitly marked not yet
  available.

### Security
- `scrub_check.py`'s email-address pattern no longer misreads a `git@host:path` SSH clone
  URL as a leaked email address (the domain's greedy TLD match backtracked around a naive
  `(?!:)` lookahead) -- fixed with a word boundary before the lookahead. Control-proofed:
  a real email address followed by punctuation still fires.
- `scrub_check.py`'s secret-shaped pattern now catches an unquoted `key: value` /
  `key = value` credential line, not only a quoted literal, and now scans its own
  source file (an earlier version excluded `scrub_check.py` by name, which is how a
  hardcoded site-specific denylist shipped undetected inside it). Obvious placeholders
  (`<...>`, `${...}`, `[REDACTED]`, `***`/`...`, a markdown code-span backtick) are
  excluded so redaction documentation describing this pattern does not trip it.
- `eval/fixtures/*` (synthetic captured test-runner logs) explicitly allow-listed by
  directory against this pattern -- fixture text (test names and pass/fail markers) legitimately matches the
  shape with no real secret behind it.
- 12 new tests (`tests/test_scrub_check.py`): each unquoted/quoted form caught, each
  placeholder form excluded, and the redaction-doc and denylist-file-in-repo scenarios
  proven with planted controls.

### Added
- Apache-2.0 `LICENSE` and `NOTICE` (JSON Holdings LLC, 2026). `pyproject.toml` license field and
  README license section updated to match. Repo remains private and unpushed; this only unblocks
  `scripts/scrub_check.py --release`'s license gate.

### Removed
- Stray duplicate `test_squire.py` at the repo root (the real copy lives under `tests/`).

### Added (from previous sync)
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

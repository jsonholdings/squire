# squire — local offload for noisy output (CLAUDE.md snippet)

`squire` runs a local model (default: qwen2.5:14b via Ollama) on this machine's GPU/CPU, so
sessions read a condensed result instead of raw bulk. Use it by default for:

- **Noisy commands:** `squire run -- <cmd>` for test suites, builds, installers, log scans.
  Output of 60 lines or fewer passes through raw, no model call. Longer output returns the REAL
  exit code, the last 25 raw lines, and a local failure summary.
- **Long files or logs:** `squire sum <file>` or `squire ask "question" <file>` instead of
  reading a big file into context.
- **First drafts:** `squire draft "instructions" [source]` for docs, SOPs, boilerplate. Review
  and fix; don't write it from scratch.

**Never use it for:** deciding pass or fail (the exit code decides), security or legal
judgement, debugging production, or anything that deploys. squire output is **ASSUMED** until
checked. If it prints `UNKNOWN: local model unavailable`, fall back to reading the raw tail.

If the squire MCP server is registered, prefer the `squire_*` tools over shelling out by hand.

If the PreToolUse hook (`hooks/pretool_wrap.py`) is installed, noisy build/test/lint commands
are rewritten to run through squire BEFORE execution, so only the condensed output (real exit
code + tail + summary) ever reaches the model -- this is the recommended, actually
token-saving hook. `hooks/posttool_condense.py` is a PostToolUse hook kept opt-in and NOT
recommended: it can only add a summary on top of output already shown, which uses more tokens,
not fewer (PostToolUse hooks cannot replace tool output, per code.claude.com/docs/en/hooks).

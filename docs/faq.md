# FAQ

**Does squire decide if my tests passed?**
No. `squire run` always prints the real exit code and a raw tail; the summary is a convenience,
never the verdict. Never wire squire's summary output into a pass/fail check.

**What happens if the local model is down?**
Every command returns `UNKNOWN` and falls back to showing the raw output/tail. squire never goes
silent and never claims a result it couldn't compute.

**Does squire send anything over the network?**
Only to the configured local backend (default `http://127.0.0.1:11434`, i.e. Ollama on the same
machine). No telemetry, no external calls.

**Is the model output trustworthy?**
It's labelled ASSUMED for a reason — treat it like a fast first pass a colleague drafted, not a
verified fact. `squire run`/`squire diff` run a second local pass that flags likely inaccuracies,
but that flag is itself ASSUMED and can be wrong.

**Can I use a backend other than Ollama?**
Yes — `SQUIRE_BACKEND=openai` plus `SQUIRE_OPENAI_BASE` points squire at any OpenAI-compatible
localhost server (llama.cpp, vLLM). See [backends.md](backends.md).

**Will squire read my secrets into the local model?**
`redact()` strips secret-shaped strings (private keys, `github_pat_`/`ghp_`, `sk-`, AKIA, Slack
tokens, JWTs, `password=`/`token=` assignments) before any prompt or embedding request leaves the
process. It's a pattern match, not a guarantee — don't rely on it as the only safeguard for files
you know contain live credentials.

**How do I know squire is actually saving tokens, not just claiming to?**
Run `scripts/squire_report.py` yourself against your own Claude Code session transcripts — it's
the same method used to produce the numbers in the README, and it never prints transcript content.
See [measuring-savings.md](measuring-savings.md).

**Are `squire grep`/`triage`/`doctor` real?**
Yes, all implemented as of v0.2.0 — see [commands.md](commands.md).

**What license is this under?**
Apache-2.0 (owner decision 2026-09-12) — see the README and `LICENSE`.

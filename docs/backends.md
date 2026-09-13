# Backends

All below are implemented in `squire.py` (v0.2.0).

| Env var | Default | Meaning |
|---|---|---|
| `SQUIRE_BACKEND` | `ollama` | `ollama` or `openai`. |
| `SQUIRE_OLLAMA` | `http://127.0.0.1:11434` | Ollama base URL. |
| `SQUIRE_OPENAI_BASE` | `http://127.0.0.1:8080/v1` | Base URL for an OpenAI-compatible local server (llama.cpp `server`, vLLM). |
| `SQUIRE_MODEL` | `qwen2.5:14b` | Model name, or `auto` to pick by free VRAM among installed models (7B/14B/32B tiers). |
| `SQUIRE_EMBED_MODEL` | `nomic-embed-text` | Embedding model for `squire grep`. |
| `SQUIRE_ALLOW_HOSTS` | (empty) | Comma-separated extra backend hostnames. Without it, only `localhost`/`127.0.0.1`/`::1` are accepted — any other host is refused outright (`BackendError`), never silently used. |
| `SQUIRE_CTX` | `16384` | `num_ctx`/context length passed to the backend. Ollama's own default (2048) truncates long prompts silently, so squire always sets this explicitly. |
| `SQUIRE_LEDGER` | `~/.squire/ledger.jsonl` | Path to the local call ledger (mode 700). Each line now also carries `session` (`CLAUDE_CODE_SESSION_ID`, may be `null` if unset). |

## Redaction
Before any prompt or embedding request leaves the process, `redact()` in `squire.py` strips
secret-shaped strings: private keys, `github_pat_`/`ghp_` tokens, `sk-` (OpenAI-style) keys, AKIA
(AWS) keys, `xox?-` (Slack) tokens, JWTs, and `password=`/`token=`-style assignments.

## Docker
`docker compose up` in `docker/` brings up Ollama (GPU via nvidia-container-toolkit, CPU fallback
via `docker-compose.cpu.yml`) plus a one-shot model-pull job and a healthchecked squire container
pinned to `SQUIRE_ALLOW_HOSTS=ollama` (the compose network hostname). VERIFIED: `docker compose
config` validates; a live Ollama health check passed on an alternate port during testing. See
`docker/README.md` for the full walkthrough, including the doctor-vs-healthcheck exit-code note
(Docker shows squire's own `UNKNOWN` exit 2 the same as a hard failure — use `docker compose run
--rm squire doctor` without `--json` to tell them apart).

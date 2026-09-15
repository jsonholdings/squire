# Squire model + landscape research — 2026-09-13

Owner request (2026-09-13 ~21:55): "are there better models out there that can be used to get
better results? DeepSeek? Anything available? … fully research and update squire to the best
model, if we are not already using it. Also include research into the squire app; other open
source projects like it and produce a comprehensive write up with a QRD section at the top
answering and explaining my questions."

This covers parts (1) MODELS and (4) LANDSCAPE of the SQUIRE-BEST-MODEL + LANDSCAPE todo
(`~/Projects/TODO-2026-09-12.md`). Parts (2) MEASURE, (3) SWITCH and (5) branded-PDF deliverable
are **not done here** — this is web research only, done the same night as the NCP overnight run,
with no GPU used for testing and no model downloaded. Draft prose passages below were produced
with `squire draft` and reviewed/edited by hand.

Everything not explicitly marked VERIFIED is ASSUMED or UNKNOWN and needs a follow-up check —
see the caveats under each table. No number here has been independently measured on this
workstation; nothing below justifies switching Squire's model yet.

---

## QRD — plain answers

- **Is there likely a better model than qwen2.5:14b for Squire's jobs?** Several same-size
  candidates look promising on paper — Qwen2.5-Coder-14B, Qwen3-14B (non-thinking mode),
  DeepSeek-Coder-V2-Lite, and Mistral-Small-3.2-24B — but **none have been measured yet**. "Looks
  promising on a model card" is not evidence; see the Measurement Plan below.
- **Is DeepSeek a good fit?** Partially. DeepSeek's license (MIT) is very permissive — more
  permissive than Gemma's restricted terms. But the R1-distill reasoning variants emit `<think>`
  traces before answering, which adds latency and verbosity that is likely a net negative for
  Squire's job (fast, terse log/diff summarization with exact exit-code fidelity). The more
  promising DeepSeek candidate is **DeepSeek-Coder-V2-Lite** (16B total / 2.4B active MoE, no
  forced reasoning trace) — also unmeasured. No full DeepSeek model (V3/R1, 671B total) fits in
  24GB VRAM at any usable quant; only the distilled/smaller variants are in scope at all.
- **What else exists in the open-source landscape similar to Squire?** The closest comparables
  are **llmtrim**, **mcp-context-proxy (MCPCP)**, **Kompact**, and the pattern behind Aider's
  weak-model/editor-model split. None of them combine Squire's full breadth (CLI + MCP server +
  `draft`/`sum`/`ask`/`grep`/`triage`/`doctor`/`stats`) — most are narrower, either a pure
  algorithmic proxy (no LLM judgment) or scoped only to MCP tool-response compression.
- **What did we change tonight?** Nothing. This is research only — no model switch, no GPU used
  for testing, no download. Squire is still qwen2.5:14b via Ollama.
- **What does it cost / what's next?** A measurement pass reusing the `NCP-BENCH-PROTOCOL`
  harness (S1-S4 + speed + energy) against a shortlist of 3-5 candidates, scheduled after
  tonight's NCP overnight run since it shares the GPU and the harness. See Measurement Plan below
  for the pre-registered protocol version.

---

## 1. Model candidates (primary sources, read 2026-09-13)

Baseline: **qwen2.5:14b** (Apache 2.0, currently in production for Squire).

| Model | Params | License (commercial use) | Context | Est. VRAM q4 / q8 | Ollama tag | Notable published score | Status |
|---|---|---|---|---|---|---|---|
| Qwen2.5-14B-Instruct (baseline) | 14.7B | Apache 2.0 — Y | 131K | ~8-9GB / ~15GB | `qwen2.5:14b` (in use) | — | in production |
| Qwen2.5-Coder-14B-Instruct | 14.7B | Apache 2.0 — Y | 131,072 | ~8-9GB / ~15GB | `qwen2.5-coder:14b` | Qwen's own repo claims strong code scores; exact table not pulled — UNKNOWN | shortlisted |
| Qwen2.5-Coder-32B-Instruct | 32B | Apache 2.0 — Y | 131,072 | ~18-20GB q4; too large for comfortable q8 headroom | `qwen2.5-coder:32b` | Qwen blog claims parity with GPT-4o on some code benchmarks — ASSUMED, self-reported | candidate |
| Qwen3-14B | 14B dense | Apache 2.0 — Y | 32,768 native / 131,072 w/ YaRN | ~9.3GB q4 (Ollama-listed) | `qwen3:14b` | Thinking-mode toggle (`enable_thinking`); no bench numbers pulled — UNKNOWN | shortlisted (non-thinking mode) |
| Qwen3-32B | 32B dense | Apache 2.0 — Y | 32,768 / 131,072 w/ YaRN | 20GB q4 (Ollama-listed) — fits, thin context headroom | `qwen3:32b` | Same thinking toggle; thinking mode adds real latency | candidate |
| Qwen3-30B-A3B (MoE) | 30B total / ~3B active | Apache 2.0 — Y | 262,144 native (HF page) | 19GB q4 (Ollama-listed) — MoE doesn't shrink weight-storage VRAM | `qwen3:30b` | Qwen claims it "outcompetes QwQ-32B with 10x fewer activated params" — ASSUMED, self-reported | candidate |
| DeepSeek-R1-Distill-Qwen-14B | 14B | **MIT** — Y (verified at HF LICENSE file) | 128K | 9.0GB q4 (Ollama-listed) | `deepseek-r1:14b` | Reasoning-tuned; `<think>` traces add latency/verbosity | candidate (latency-cost caveat) |
| DeepSeek-R1-Distill-Qwen-32B | 32B | **MIT** — Y (verified) | 128K | 20GB q4 (Ollama-listed) | `deepseek-r1:32b` | Same reasoning-trace tradeoff, larger | candidate (latency-cost caveat) |
| DeepSeek-Coder-V2-Lite-Instruct | 16B total / 2.4B active MoE | Custom "deepseek-license" — card states commercial use supported; exact restriction text NOT independently pulled — UNKNOWN detail | UNKNOWN (not confirmed this pass) | Likely <10GB q4 — ASSUMED from total size, not stated directly | `deepseek-coder-v2:16b-lite-instruct` (Ollama library page confirmed via search) | Own claim: comparable to GPT-4-Turbo on code tasks — ASSUMED, self-reported | **shortlisted** |
| DeepSeek-V3 / R1 (full) | 671B total / 37B active | MIT | N/A | Does not fit 24GB — 37B active alone needs ~74GB BF16; full weights ~1.3TB across experts (HF repo) | N/A | — | excluded, too large |
| Gemma 2 27B | 27B | Custom Gemma license + Prohibited Use Policy — Y for use, with restrictions beyond typical OSS terms | UNKNOWN this pass | ~15-16GB q4 — ASSUMED | `gemma2:27b` — ASSUMED standard listing, not fetched | UNKNOWN this pass | candidate, license caveat |
| Gemma 3 27B | 27B | Same custom Gemma license + Prohibited Use Policy (HF card, read 2026-09-13) | Longer context per Google's blog; exact figure UNKNOWN | ~15-16GB q4 — ASSUMED | `gemma3:27b` — ASSUMED, not fetched | UNKNOWN this pass | candidate, license caveat |
| Phi-4 (14B) | 14.7B | **MIT** — Y, unrestricted (HF card, read 2026-09-13) | 16,384 — notably shorter than the Qwen options, a real limit for large diffs/logs | ~8-9GB q4 — ASSUMED | `phi4:14b` — ASSUMED, not fetched | Microsoft claims parity with "5x its size" — ASSUMED, self-reported | candidate, context-limit caveat |
| Phi-4-mini | smaller than 14B, exact size UNKNOWN | MIT — ASSUMED, consistent with family | UNKNOWN | small, fits easily | UNKNOWN if on Ollama | UNKNOWN | not evaluated further |
| Mistral-Small-3.1-24B-Instruct-2503 | 24B | Apache 2.0 — Y (HF card, read 2026-09-13). Note: an earlier 2501 release existed under different terms — verify per-version. | UNKNOWN exact figure; Mistral states long-context support | ~13-14GB q4 / ~24GB q8 (borderline) | `mistral-small:24b` — ASSUMED, not fetched | UNKNOWN this pass | superseded by 3.2 below |
| Mistral-Small-3.2-24B-Instruct-2506 | 24B | Apache 2.0 — Y (HF card, read 2026-09-13) — card claims improved instruction-following, fewer infinite/repetitive generations, more robust function calling vs 3.1 | same estimate as 3.1 | same estimate | UNKNOWN Ollama tag, not fetched | UNKNOWN | **shortlisted** |
| Llama 3.1 8B | 8B | Llama 3.1 Community License — Y for most commercial use, Meta acceptable-use restrictions apply (ASSUMED, not re-verified) | 128K | ~5GB q4 | `llama3.1:8b` | UNKNOWN this pass | too small for parity comparison, kept as a speed reference only |
| Llama 3.3 70B | 70B | Llama 3.3 Community License — ASSUMED, not fetched | 128K | **Excluded**: ~35-40GB at q4 (70B x ~0.5GB/param), exceeds 24GB. Confirmed by comparable `deepseek-r1:70b` Ollama listing at 43GB q4. | N/A on this GPU | N/A | excluded, too large |

### Embedding models vs nomic-embed-text

| Model | License | Score (secondary aggregator, NOT primary MTEB — treat as ASSUMED) | Note |
|---|---|---|---|
| nomic-embed-text (in use) | Apache 2.0 — ASSUMED, not re-verified this pass | 62.39 MTEB English v1 (morphllm.com aggregation) | ~0.3GB, cheap |
| mxbai-embed-large | Apache 2.0 (ollama.com/library page, read 2026-09-13) | 64.68 MTEB English v1 (same aggregator) | modest upgrade if track is comparable |
| bge-m3 | MIT | no single comparable number — dense/sparse/multi-vector hybrid, 8K context, 100+ languages (aggregator, ASSUMED) | different benchmark shape |
| Qwen3-Embedding-0.6B | Apache 2.0 (Qwen family standard, ASSUMED) | 70.7 MTEB-eng-**v2** — a different, newer benchmark version, not directly comparable to the v1 scores above | best quality-per-VRAM per aggregator, but the version mismatch means this is not a clean "+8 points" claim |

**Caveat on embedding scores:** all four numbers above came through a secondary aggregator
(morphllm.com), not the primary MTEB leaderboard or each model's own card. Label ASSUMED until
someone pulls `huggingface.co/spaces/mteb/leaderboard` directly.

### Not independently pulled this pass (flagged, not guessed)

- Exact benchmark tables (HumanEval / MMLU / LiveCodeBench) for Qwen2.5-Coder-14B/32B,
  Qwen3-14B/32B/30B-A3B, DeepSeek-Coder-V2-Lite, Gemma 2/3 27B, Phi-4, Mistral-Small — search
  results gave prose summaries, not the model cards' actual score tables.
- Ollama tag existence/exact sizes for Gemma 2/3 27B, Phi-4, Phi-4-mini, Mistral-Small 3.1/3.2,
  Llama 3.1 8B/3.3 70B — assumed from standard Ollama naming, not individually fetched.
- Gemma 3 27B's exact context window.
- DeepSeek-Coder-V2-Lite's exact context window and the full text of its custom license
  restrictions.

### Shortlist to actually measure (reason for each)

1. **Qwen2.5-Coder-14B-Instruct** — same VRAM envelope as the current baseline, Apache 2.0,
   code/log-specialized training. Lowest-risk swap to test first.
2. **DeepSeek-Coder-V2-Lite-Instruct** — same size class, commercial-use-supporting license,
   small active-param MoE should mean faster tokens/sec on one GPU. Directly answers the owner's
   DeepSeek question.
3. **Qwen3-14B (non-thinking mode)** — direct generational successor to the baseline, same
   footprint, Apache 2.0. Must be run with `enable_thinking=False` to stay latency-comparable.
4. **Mistral-Small-3.2-24B-Instruct-2506** — Apache 2.0, card explicitly claims fewer
   infinite/repetitive generations (a real defect class for log/diff summarization), fits 24GB q4
   with headroom.
5. **DeepSeek-R1-Distill-Qwen-14B** — only if reasoning quality on hard diff/triage judgment
   calls is worth the `<think>`-trace latency/verbosity cost; MIT license removes any commercial
   concern. Lowest-priority of the five — likely a net loss for terse summarization, but it is
   the model the owner named by name, so it should be measured rather than dismissed on paper.

---

## 2. Landscape — comparable open-source projects (primary sources, read 2026-09-13)

| # | Project | URL | What it does | License | Last activity | vs Squire |
|---|---|---|---|---|---|---|
| 1 | llmtrim | github.com/fkiene/llmtrim | Local proxy/library trimming wasted tokens from prompts/history/tool-output/code before sending to any provider | MPL-2.0 | active (exact date UNKNOWN) | Closest overall. Deterministic algorithms, not an inference model — no `ask`/`draft`/`triage`-style judgment. Has a published live before/after benchmark and a transparent `HTTPS_PROXY` drop-in mode Squire lacks. |
| 2 | mcp-context-proxy (MCPCP) | github.com/samteezy/mcp-context-proxy | Transparent MCP proxy compressing large tool responses with a small local/external LLM | MIT | active (exact date UNKNOWN) | Purpose-built for MCP tool-response compression with tiny models (Qwen3-0.6B, LFM2-1.2B) — much smaller footprint than Squire's 14B. Has a response cache and web dashboard Squire lacks. |
| 3 | Kompact | github.com/npow/kompact | HTTP proxy compressing LLM context 40-70% via schema/JSON/extractive transforms + cache alignment | MIT | active (exact date UNKNOWN) | No local model — pure algorithmic, faster/cheaper but less semantic. Has OpenTelemetry/Prometheus/Grafana export and a per-request disable header, worth borrowing for `squire stats`. |
| 4 | `llm` CLI + `llm-ollama` (Simon Willison) | github.com/simonw/llm | General LLM CLI/lib with local-Ollama routing, logs every call to a queryable SQLite DB | Apache-2.0 | actively maintained | Not agent-sidekick-specific — no exit-code wrapping, no semantic grep, no triage. Its SQLite+Datasette call log is a stronger audit trail than `squire stats`. |
| 5 | aichat (sigoden) | github.com/sigoden/aichat | Rust multi-provider LLM CLI: shell-assistant, chat-REPL, RAG, local server mode, 20+ providers incl. Ollama | Apache-2.0 (to verify) | active, binary releases | Broader chat tool, not built around token-offload; no exit-code-preserving wrapper or diff/triage semantics. Its local RAG + self-serving OpenAI-compatible mode is worth a look. |
| 6 | Fabric (danielmiessler) | github.com/danielmiessler/fabric | Crowdsourced library of prompt "Patterns" run via CLI/pipe against any LLM incl. local | MIT | actively maintained | Stronger for `squire draft`-style generation (large curated template set); no exit-code/test-wrapper concept, no semantic grep. Worth adopting a small curated pattern library for `squire draft`. |
| 7 | mods (charmbracelet) | github.com/charmbracelet/mods | Pipe stdin + prompt to an LLM (incl. local via LocalAI) from the shell | MIT | **archived 2026-03-09**, superseded by Charm's "Crush" | Same "pipe output through a local model" pattern as `squire run --`, but no exit-code preservation or length-triggered summarization threshold. Archival is itself a data point; "Crush" not researched this pass (budget). |
| 8 | Aider weak-model/editor-model pattern | aider.chat/docs/llms/ollama.html | Assigns a cheaper/local "weak model" to lightweight sub-tasks inside one coding agent | Apache-2.0 | actively maintained | A role-split within one agent, not a standalone companion tool — no MCP surface, no `run --`/triage/grep. Validates Squire's "little brother" thesis but Squire's tool-belt is far more developed for this use case. |

Also found but not depth-researched this pass (budget cutoff): PromptThrift MCP (Gemma-based
compression, MIT-ish, claims 70-90% API cost savings — unverified), sqz (Rust dedup-based
compressor, no LLM inference), mcp-compact (LLM-based MCP output compaction claiming up to 97%
reduction — unverified). All are algorithmic or small-model proxies; none combine Squire's full
CLI+MCP+draft+triage+doctor breadth.

**Exact last-commit/release dates could not be confirmed for most repos** (fetched pages didn't
surface commit timestamps) — labeled UNKNOWN rather than guessed. A follow-up `gh api
repos/<owner>/<repo>` per repo would resolve these in a few minutes.

### Top 3 most comparable overall

1. **llmtrim** — closest in spirit: local-first, token-saving, drop-in proxy + MCP server +
   embeddable lib across 5+ languages, with a published benchmark Squire currently lacks. Gap: no
   LLM-based semantic summarization, purely deterministic trimming.
2. **mcp-context-proxy (MCPCP)** — closest in mechanism: a small local model compressing bulk
   tool output before it reaches the calling agent. Scoped only to MCP tool responses, not a
   general CLI; Squire's `run --`/`grep`/`triage`/`draft`/`doctor` breadth is wider.
3. **Aider's weak-model/editor-model split** — closest in philosophy: a cheap/local model doing
   grunt work so the expensive model reasons less. It's a role inside one agent, not a standalone
   sidekick — validates Squire's core thesis without competing on features.

**Borrow-worthy ideas for Squire:** llmtrim's transparent `HTTPS_PROXY` drop-in mode plus a
published before/after benchmark suite; MCPCP's response cache and web dashboard; Kompact's
OpenTelemetry/Prometheus export and per-call disable header; the `llm` CLI's SQLite+Datasette
call log for a richer `squire stats`.

---

## 3. Measurement plan (pre-registered, not yet run)

This section defines protocol version **NCP-BENCH v1.1-squire-model-compare**, a reuse of the
`NCP-BENCH-PROTOCOL.md` harness (S1-S4 correctness suites + speed + energy measurement), applied
to the model shortlist above instead of comparing NCP's base model against Ollama.

- **Candidates to run:** the 5 shortlisted models in §1, each pulled via `ollama pull` at
  measurement time (not tonight — GPU is reserved for Squire until midnight, then for the NCP
  overnight run).
- **Baseline:** current production `qwen2.5:14b`, plus whatever result the NCP overnight run
  (2026-09-13/14) produces, for a three-way comparison surface (current Squire model / NCP
  candidate / new Squire candidates).
- **Test battery:** the same S1-S4 correctness checks Squire's own `docs/BENCHMARK.md` already
  defines (summarizing test/build logs, file Q&A, diff summaries, triage classification), plus
  exact exit-code fidelity as a pass/fail gate — a model that garbles a real exit code fails
  regardless of prose quality. Add latency and tokens/sec per candidate, and note reasoning-mode
  overhead separately for any `<think>`-emitting model.
- **Decision rule:** a candidate replaces qwen2.5:14b only if it (a) passes the S1-S4 correctness
  gate at parity or better, (b) preserves exact exit-code fidelity, and (c) is not slower on
  median latency for Squire's real workload mix — matching the existing NCP-BENCH-PROTOCOL
  decision-rule structure rather than a new one.
- **Scheduling:** after tonight's NCP programme, since it shares the GPU and the harness (owner's
  own sequencing note on the TODO item).
- **Out of scope for this document:** actually running the measurement, switching Squire's model,
  updating `docs/BENCHMARK.md`/CHANGELOG, cutting a release via `sync_to_mirror`, or updating the
  global CLAUDE.md §5.8 model line. Those are parts (2) and (3) of the source TODO item, scheduled
  for a later session once the GPU is free and the owner has seen this write-up.

---

## Links

- Squire source: `~/Projects/business/jsonholdings/infrastructure/squire/` (README, `docs/BENCHMARK.md`, CHANGELOG)
- Squire public mirror: github.com/jsonholdings/squire
- NCP measurement harness: `~/Projects/NCP-BENCH-PROTOCOL.md`
- Source TODO item: `~/Projects/TODO-2026-09-12.md`, "SQUIRE-BEST-MODEL + LANDSCAPE WRITE-UP"
- Model cards / licenses read this session: huggingface.co/Qwen (Qwen2.5, Qwen2.5-Coder, Qwen3
  families), huggingface.co/deepseek-ai (DeepSeek-R1-Distill-Qwen-14B/32B LICENSE files,
  DeepSeek-Coder-V2-Lite-Instruct, DeepSeek-V3), huggingface.co/google (Gemma 2/3 27B + Gemma
  Prohibited Use Policy), huggingface.co/microsoft/phi-4, huggingface.co/mistralai
  (Mistral-Small-3.1/3.2-24B-Instruct), ollama.com/library (tag/size listings)
- Landscape sources: github.com/fkiene/llmtrim, github.com/samteezy/mcp-context-proxy,
  github.com/npow/kompact, github.com/simonw/llm, github.com/sigoden/aichat,
  github.com/danielmiessler/fabric, github.com/charmbracelet/mods, aider.chat/docs/llms/ollama.html

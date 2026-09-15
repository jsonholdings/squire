# Model bake-off: does a newer open model beat qwen2.5:14b on this 3090? (2026-09-15)

Owner-approved research + bake-off. Question: does any newer open-weight instruct model, at a
quant that fits the RTX 3090's 24 GB, run squire's actual job (condense test output/logs/diffs,
answer strictly from given text, say UNKNOWN when absent, never invent names) better than the
current default, measured on `eval/run_eval.py`.

## Shortlist considered

Sourced from Ollama's own library pages (fetched 2026-09-15) rather than search-result summaries,
since several blog posts returned by the initial web search named models (e.g. "Gemma 4",
"Qwen3.6") that do not appear in Ollama's library and were not used further:

| Model | Ollama tag | Size (pulled) | Context | Licence |
|---|---|---|---|---|
| Qwen3 14B | `qwen3:14b` | 9.3 GB | 40K | Apache-2.0 |
| Gemma 3 12B | `gemma3:12b` | 8.1 GB | 128K | Gemma Terms of Use |
| Mistral Small 3 (24B) | `mistral-small:24b` | 14 GB | 32K | Apache-2.0 |
| qwen2.5:14b (current default) | `qwen2.5:14b` | 9.0 GB | — | control |
| qwen2.5:14b-instruct-q8_0 (already pulled, unused) | — | 15 GB | — | control |

(https://ollama.com/library/qwen3, https://ollama.com/library/gemma3,
https://ollama.com/library/mistral-small, fetched 2026-09-15.)

## Method

`eval/run_eval.py` run once per model via `SQUIRE_MODEL=<tag> SQUIRE_SOURCE=test`, same 8-fixture
corpus (`eval/fixtures/*.log`+`.json`) used for every model, same machine, sequential (one model
loaded in VRAM at a time via Ollama's default `keep_alive`). Latency (`gen_s`) and UNKNOWN/backend
status pulled from `~/.squire/ledger.jsonl` rows tagged `source=test` in each run's time window.
GPU memory read via `nvidia-smi --query-gpu=memory.used,memory.total`.

**Caveat found while running this:** the eval is not fully deterministic — the baseline
`qwen2.5:14b` itself scored recall 1.00 on one run and 0.875 on an immediate re-run (missed
`jest_one_failure.log`). n=8 fixtures per run is too small to treat a single run as ground truth
for any model; the numbers below are 1-2 runs per model, not the 50-case corpus behind the
published 1.00 recall figure in `docs/BENCHMARK.md`/README.

## Results (2 runs each unless noted)

| Model | Recall (runs) | UNKNOWN/false-clean | p50 gen_s | VRAM after load |
|---|---|---|---|---|
| qwen2.5:14b (default) | 1.00, 0.875 | 0 | 1.7s | ~9 GB |
| qwen2.5:14b-instruct-q8_0 | 1.00, 1.00 | 0 | 1.7s | ~15 GB |
| qwen3:14b | 0.75, 0.375 | 0 | 5.2s | ~9 GB |
| gemma3:12b | 1.00, 1.00 | 0 | 3.8s | ~8 GB |
| mistral-small:24b | 1.00, 1.00 | 0 | 2.8s | ~14 GB |

Exact commands: `SQUIRE_MODEL=<tag> SQUIRE_SOURCE=test python3 eval/run_eval.py`, run from this
directory. VRAM snapshot: `flatpak-spawn --host nvidia-smi --query-gpu=memory.used,memory.total
--format=csv,noheader,nounits`.

## Finding

**`qwen3:14b`'s default Ollama tag performs clearly worse** (recall dropped to 0/1 and even 0/2 on
fixtures the other four models get right), most likely because that tag ships with "thinking"
enabled by default and its reasoning preamble crowds out the extractable test name/line under
squire's prompting — not investigated further within this bake-off's timebox.

**`gemma3:12b` and `mistral-small:24b` matched the baseline's recall** (1.00 on both runs) but
neither beat it: both are slower (p50 3.8s and 2.8s vs. 1.7s) and use as much or more VRAM (14 GB
for mistral-small:24b vs. 9 GB for the default), for no fidelity gain measured. Neither clears the
bar this bake-off set (≥ baseline on every fidelity metric, and better on speed or quality).

## Decision

**No change to the default.** `SQUIRE_MODEL` stays `qwen2.5:14b`; `AUTO_TIERS` is unchanged. All
three candidate models are left pulled on this workstation (see `ollama list`) for any future
re-test — nothing removed without the owner.

## Re-running this later

The eval corpus here is small (8 fixtures) and single/double-run per model. A model that looked
tied or slightly behind here could look different on the full 50-case corpus behind
`docs/BENCHMARK.md`, or with `qwen3:14b`'s non-thinking variant/tag, or with more repeats to
account for the run-to-run variance observed even on the current default.

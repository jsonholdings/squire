# Commands

All commands below are implemented in `squire.py` (verified via `squire --help`, v0.2.0).

## `squire run -- <cmd...>`
Runs `<cmd>` and always prints the real exit code first. If the output is 60 lines or fewer it is
printed as-is with no model call. Longer output shows the real last-25-line tail plus a local
summary (at most 8 bullets, every failure with file:line and cause), and a second local pass flags
— visibly, never silently — if the summary looks like it invented or omitted something. Exits with
the command's real exit code, always.

## `squire sum [file|-]`
Condenses text (or stdin) to at most 8 factual bullets. Numbers, names and paths are kept exact
per the prompt; still ASSUMED until checked.

## `squire ask "question" [file|-]`
Answers strictly from the given text; explicitly instructed to say `UNKNOWN` rather than use
outside knowledge.

## `squire draft "instructions" [file|-]`
Produces a first draft only — never meant to be used unreviewed. Larger `max_tokens` budget (1200)
than the other commands since drafts are longer-form.

## `squire diff [--staged] | squire diff <ref1> <ref2>`
Always prints the real `git diff --stat` line, computed by git, never by the model. Then a
condensed summary separating logic changes from formatting/rename-only changes. Never used to
decide whether a diff is safe to commit — read the real diff yourself for anything sensitive.

## `squire stats`
Reads `~/.squire/ledger.jsonl` (every call any command above makes) and reports real chars in/out,
plus a token estimate explicitly labelled ESTIMATE (chars/4 heuristic). For a cross-checked figure
against real Claude Code session token usage, use `scripts/squire_report.py` — see
[measuring-savings.md](measuring-savings.md).

## `--json`
Accepted by every command above. Emits one JSON object instead of formatted text:
`{"cmd", "exit_code", "raw_tail", "summary", "assumed", "backend_ok", "verify_flag"}`.
`exit_code`/`raw_tail` are `null` for commands that don't run a subprocess (`sum`/`ask`/`draft`).

## `squire grep "query" [path] [--top N] [--reindex] [--json]`
Local semantic search over a repo using an embedding model (default `SQUIRE_EMBED_MODEL=nomic-embed-text`).
Always prints, un-labelled, the literal command run and how many files/chunks were indexed, so a
search that covered zero files can never look identical to one that covered everything and found
nothing. Caches embeddings under `<repo>/.squire-cache/`, added to `.git/info/exclude` (never
`.gitignore`); `--reindex` forces a full re-embed. VERIFIED this session on a 55-file repo: cold
index 2.9s for 32 chunks. A separate run measured 43s cold / 8.7s warm on a
143-chunk repo; re-run on your own repo before
citing a number as your own. Full contract: `SPEC-LOCAL-OFFLOAD-TOOLING.md` §1.

## `squire triage <file> [--json]`
Reorders a HANDOFF-INBOX.md/BACKLOG.md-shaped file oldest-open-first. Age is computed from the
heading timestamp (a real fact); each item also gets a one-line impact guess, visually marked
ASSUMED and kept separate from the computed age. Full contract: `SPEC-LOCAL-OFFLOAD-TOOLING.md` §3.

## `squire doctor [--json]`
Checks backend reachability and model availability. Exit 0 = ready, 2 = `UNKNOWN` (backend down or
model not pulled) — never a false "ready".

## `--version`
Prints `squire.py`'s version string.

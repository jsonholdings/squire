# squire MCP server

Stdlib-only MCP stdio server (protocol 2025-06-18) exposing squire's commands as tools:
`squire_run`, `squire_sum`, `squire_ask`, `squire_draft`, `squire_diff`, `squire_grep`,
`squire_triage`, `squire_stats`. Never imports squire internals -- shells out to the `squire`
CLI with `--json`.

## Register with Claude Code

```sh
claude mcp add squire -- python3 /path/to/squire/mcp/squire_mcp.py
```

Set `SQUIRE_BIN` if `squire` is not on PATH and not found next to this file (default lookup
order: `$SQUIRE_BIN`, `squire` on PATH, `../squire.py`).

## Verify

```sh
claude mcp list        # squire should show as connected
```

Then in a session: "use squire_stats" should return the ledger totals.

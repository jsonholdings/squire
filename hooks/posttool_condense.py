#!/usr/bin/env python3
"""posttool_condense: PostToolUse hook that adds a squire-condensed summary of long Bash output.

STATUS: opt-in, NOT recommended as the default. A PostToolUse hook can only ADD context on top
of output already shown to the model (confirmed against code.claude.com/docs/en/hooks,
2026-09-12) -- it cannot remove or replace tool_response. Installing this hook makes context
usage WORSE, not better: the full original output still lands, plus this summary on top. Use
hooks/pretool_wrap.py instead -- it rewrites the command before execution so only squire's
condensed output ever reaches the model. This file is kept for cases where the original raw
output must be preserved verbatim for the model (e.g. auditing) and a bolted-on summary is
still wanted for convenience.

Spec: HANDOFF-INBOX.md "Build: PostToolUse hook to auto-condense noisy Bash output via squire"
(2026-09-12).

HONEST LIMITATION (confirmed against https://code.claude.com/docs/en/hooks on 2026-09-12):
a PostToolUse hook CANNOT replace or remove the tool_response Claude sees -- that text has
already landed in the transcript by the time this hook runs. There is no "replacement" mode.
The closest honest thing this hook can do is add `additionalContext` carrying the exit code,
the last ~25 raw lines, and a [squire]-labelled summary, so the model does not have to re-read
the whole blob to find the failure -- but the full original output IS still shown to the model
and still costs tokens. This narrows the ambition of the handoff spec's step 4 ("REPLACEMENT
text"); there currently is no Claude Code hook mechanism that achieves a true replacement for
PostToolUse. If that changes, revisit.

Never touches (per spec): non-Bash tools, output <= 60 lines, commands invoking git diff/status,
session-claim, or squire itself (avoids recursion / hiding claim conflicts). SQUIRE_HOOK_DISABLE=1
disables entirely (passthrough, no stdout at all -- exit 0, no hookSpecificOutput).
"""
import json
import os
import shutil
import subprocess
import sys

SHORT = 60
TAIL = 25
SKIP_SUBSTRINGS = ("git diff", "git status", "session-claim", "squire ")


def find_squire_bin():
    env = os.environ.get("SQUIRE_BIN")
    if env:
        return [env] if not env.endswith(".py") else [sys.executable, env]
    on_path = shutil.which("squire")
    if on_path:
        return [on_path]
    here = os.path.dirname(os.path.abspath(__file__))
    return [sys.executable, os.path.join(here, "..", "squire.py")]


def should_skip(command):
    cmd = (command or "").strip()
    return any(s in cmd for s in SKIP_SUBSTRINGS)


def main():
    if os.environ.get("SQUIRE_HOOK_DISABLE") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed input: do nothing rather than guess

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if should_skip(command):
        return 0

    tool_response = payload.get("tool_response")
    text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response or "")
    lines = text.splitlines()
    if len(lines) <= SHORT:
        return 0  # passthrough unchanged, matches squire's own raw-passthrough threshold

    tail = "\n".join(lines[-TAIL:])

    try:
        proc = subprocess.run(
            find_squire_bin() + ["sum", "-", "--json"],
            input=text, capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "").strip()
        result = None
        for line in reversed(out.splitlines()):
            if line.strip().startswith("{"):
                try:
                    result = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if result is None or not result.get("backend_ok", False):
            return 0  # squire down/failed: original output stands, unchanged, no context added
        summary = result.get("summary", "")
    except (OSError, subprocess.TimeoutExpired):
        return 0  # never drop or block output because the condenser failed

    additional = (
        f"[squire] {len(lines)}-line Bash output condensed (ASSUMED, not verified).\n"
        f"Real exit code is unaffected by this note -- read the tool's own exit status.\n"
        f"Last {TAIL} raw lines:\n{tail}\n\n"
        f"[squire] summary: {summary}"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

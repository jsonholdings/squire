#!/usr/bin/env python3
"""pretool_wrap: PreToolUse hook that rewrites a noisy Bash command to run through squire,
so only squire's condensed output (real exit code + tail + summary) ever reaches the model.

Verified against https://code.claude.com/docs/en/hooks (2026-09-12): PreToolUse hooks may
return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {...}}} to replace
tool_input before execution. This is the actual token-saving lever -- unlike a PostToolUse hook,
which can only ADD context on top of output already shown (see posttool_condense.py, kept
opt-in/not-recommended for that reason).

Only wraps commands that look like known-noisy build/test/lint/install tools, are not already
squire, have no pipe into head/tail/grep (which already trims output -- wrapping would hide real
signal), aren't interactive, and contain no heredoc. Never wraps git, session-claim,
cat/sed/grep/ls, or anything already listed as skip. SQUIRE_HOOK_DISABLE=1 disables entirely.

Quoting: the original command is passed to squire as ONE argv element via `bash -c`, so
`squire run -- bash -c '<orig>'` preserves the original's own quoting untouched (shlex.quote on
the whole string, not word-by-word -- word-splitting first would break quoted args containing
spaces or shell metacharacters).
"""
import json
import re
import shlex
import shutil
import sys
import os

NOISY_RE = re.compile(
    r"""(?x)
    (?:^|&&|\|\|| ; |\s) (?:
        pytest
        | python3?\s+-m\s+pytest
        | (?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|build|install)
        | jest | vitest
        | cargo\s+(?:build|test)
        | go\s+(?:build|test)
        | make
        | mvn(?:\s|$)
        | gradle(?:w)?(?:\s|$)
        | dotnet\s+(?:build|test)
        | pip3?\s+install
        | docker\s+build
        | tox
        | ruff(?:\s|$)
        | mypy(?:\s|$)
        | eslint(?:\s|$)
        | tsc(?:\s|$)
    )
    """,
)

NEVER_WRAP_RE = re.compile(r"(?:^|\s)(git|session-claim|cat|sed|grep|ls|squire)(?:\s|$)")
PIPE_TO_TRIM_RE = re.compile(r"\|\s*(head|tail|grep)\b")
INTERACTIVE_MARKERS = ("-it ", " -i ", "read -p", "/dev/tty")
HEREDOC_RE = re.compile(r"<<[-~]?\s*['\"]?\w+")


def find_squire_bin():
    env = os.environ.get("SQUIRE_BIN")
    if env:
        return [env] if not env.endswith(".py") else [sys.executable, env]
    on_path = shutil.which("squire")
    if on_path:
        return [on_path]
    here = os.path.dirname(os.path.abspath(__file__))
    return [sys.executable, os.path.join(here, "..", "squire.py")]


def should_wrap(command):
    cmd = (command or "").strip()
    if not cmd:
        return False
    if NEVER_WRAP_RE.search(cmd):
        return False
    if not NOISY_RE.search(cmd):
        return False
    if PIPE_TO_TRIM_RE.search(cmd):
        return False
    if HEREDOC_RE.search(cmd):
        return False
    if any(m in cmd for m in INTERACTIVE_MARKERS):
        return False
    return True


def build_wrapped_command(original):
    squire_argv = find_squire_bin()
    # squire_argv is e.g. ["squire"] or [sys.executable, ".../squire.py"]; render as a single
    # shell-safe command string since Bash tool_input.command is one string executed with shell=True.
    quoted_squire = " ".join(shlex.quote(a) for a in squire_argv)
    return f"{quoted_squire} run -- bash -c {shlex.quote(original)}"


def main():
    if os.environ.get("SQUIRE_HOOK_DISABLE") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not should_wrap(command):
        return 0

    new_command = build_wrapped_command(command)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"command": new_command},
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

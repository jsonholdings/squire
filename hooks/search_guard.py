#!/usr/bin/env python3
"""search_guard: PreToolUse hook for Grep/Bash that stops broad raw search sweeps from
reaching the model uncondensed -- `squire grep` ranks file:line hits with local embeddings
and should be tried first (see hooks/pretool_wrap.py's docstring for the sibling rationale).

Bash: DENIES (permissionDecision "deny", not a nudge) a recursive/multi-file raw sweep --
`grep -r`/`-R`, bare `rg`, or `find ... | xargs grep` -- unless the command carries a
`# squire-raw: <reason>` marker (same escape hatch and ledger as pretool_wrap.py). A single
explicit `grep <pattern> <one-file>` (no `-r`/`-R`, no `-l` fan-out, exactly one path operand)
is NEVER denied.

Grep tool: there is no command string on this call shape to carry a marker, so it cannot be
denied outright (no escape hatch = no fair deny) -- an unnarrowed call (no `path`/`glob`) is
allowed through with `additionalContext` noting `squire grep` was preferred, and logged to the
same raw_overrides ledger with reason "grep-tool-unnarrowed" for visibility.

Fails open (returns 0, no output) on SQUIRE_HOOK_DISABLE=1, malformed JSON, or any exception --
a guard that can crash the tool call it is guarding is worse than no guard.

2026-09-14 fix (S-URGENT): matching used to be plain regex substrings against the WHOLE command
string, so `grep -rn` (or `rg`) appearing inside a quoted argument to an unrelated command --
e.g. `echo "skip this grep -rn foo"`, or a JSON string piped through another program -- was
denied even though no actual grep/rg ever ran. Matching is now done on the TOKENIZED pipeline
(same `tokenize_pipeline` approach as the sibling `pretool_wrap.py`): the command is split into
segments on real shell operators using `shlex` (quoted text stays one opaque token, never
keyword-matched), and only a segment's actual PROGRAM (argv[0]) and its real flags/operands are
inspected. A command that can't be safely tokenized (unbalanced quotes) is left alone -- fail
toward not denying.
"""
import json
import os
import re
import shlex
import sys

# Re-derive from pretool_wrap's marker + ledger so this file has no import-time dependency on
# it (hooks run as standalone subprocesses); logic is intentionally duplicated, not imported.
RAW_OVERRIDE_RE = re.compile(r"#\s*squire-raw:\s*(?P<reason>.+)")
RAW_OVERRIDE_LEDGER = os.path.expanduser("~/.squire/raw_overrides.jsonl")
SHELL_OPS = {"|", "||", "&&", ";", "&"}


def tokenize_pipeline(command):
    """Same contract as pretool_wrap.tokenize_pipeline: (operator_before, argv) segments,
    tokenized with shlex so quoted text is never split or keyword-matched. Raises ValueError
    on unbalanced quotes -- caller treats that as "can't tell", never as a match.

    Newline is a statement separator too -- see pretool_wrap.tokenize_pipeline's docstring for
    the 2026-09-14 bug this fixes (a multi-line command's later lines were being merged into an
    earlier line's argv, hiding a real `grep -r` on line 3 from ever being checked)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&;\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments = []
    current = []
    op_before = None
    for tok in tokens:
        if tok == "\n":
            if current:
                segments.append((op_before, current))
            current = []
            op_before = None
            continue
        if tok in SHELL_OPS:
            if current:
                segments.append((op_before, current))
            current = []
            op_before = tok
        else:
            current.append(tok)
    if current:
        segments.append((op_before, current))
    return segments


def program_name(argv):
    return os.path.basename(argv[0]) if argv else ""


def analyze_grep_segment(argv):
    """argv[0] is 'grep'. Returns (is_recursive, is_single_file_lookup). Only -r/-R/--recursive
    make a grep call a "broad sweep" (matches the original rule); -l (list-matching-files) does
    not trigger a deny on its own but still disqualifies a call from the single-file exemption,
    since it signals a fan-out search even over one path."""
    flags = [a for a in argv[1:] if a.startswith("-") and a != "-"]
    nonflags = [a for a in argv[1:] if not (a.startswith("-") and a != "-")]
    has_r = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in flags)
    has_l = any(not f.startswith("--") and "l" in f for f in flags)
    is_single_file = not has_r and not has_l and len(nonflags) == 2
    return has_r, is_single_file


def find_raw_override(text):
    m = RAW_OVERRIDE_RE.search(text or "")
    return m.group("reason").strip() if m else None


def log_override(payload, tool_name, cmd_or_pattern, reason):
    try:
        os.makedirs(os.path.dirname(RAW_OVERRIDE_LEDGER), exist_ok=True)
        import time
        row = {
            "ts": time.time(),
            "session": (payload or {}).get("session_id", "unknown"),
            "tool": tool_name,
            "reason": reason,
            "cmd_head": (cmd_or_pattern or "")[:80],
        }
        with open(RAW_OVERRIDE_LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def allow_with_context(note):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": note,
        }
    }))
    return 0


def handle_bash(payload, command):
    if not command or not command.strip():
        return 0
    try:
        segments = tokenize_pipeline(command)
    except ValueError:
        return 0  # can't safely tokenize (e.g. unbalanced quotes) -- don't deny it

    is_sweep = False
    for i, (op, argv) in enumerate(segments):
        prog = program_name(argv)
        if prog == "grep":
            has_r, _is_single = analyze_grep_segment(argv)
            if has_r:
                is_sweep = True
        elif prog == "rg":
            is_sweep = True
        elif prog == "xargs" and i > 0:
            _prev_op, prev_argv = segments[i - 1]
            if op == "|" and program_name(prev_argv) == "find" and "grep" in argv[1:]:
                is_sweep = True

    if not is_sweep:
        return 0

    reason = find_raw_override(command)
    if reason:
        log_override(payload, "Bash", command, reason)
        return 0

    return deny(
        "Broad raw search sweep (grep -r/-R, rg, or find|xargs grep) -- use `squire grep "
        "\"<question>\" [path]` first, or add `# squire-raw: <reason>` to proceed raw."
    )


def handle_grep_tool(payload, tool_input):
    if tool_input.get("path") or tool_input.get("glob"):
        return 0  # already narrowed
    pattern = tool_input.get("pattern", "")
    log_override(payload, "Grep", pattern, "grep-tool-unnarrowed")
    return allow_with_context(
        "Unnarrowed Grep call (no path/glob) -- prefer `squire grep \"<question>\" [path]` "
        "for a multi-file search; this call was allowed through and logged."
    )


def main():
    if os.environ.get("SQUIRE_HOOK_DISABLE") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    try:
        tool_name = payload.get("tool_name")
        tool_input = payload.get("tool_input") or {}

        if tool_name == "Bash":
            return handle_bash(payload, tool_input.get("command", ""))
        if tool_name == "Grep":
            return handle_grep_tool(payload, tool_input)
    except Exception:
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())

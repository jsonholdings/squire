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

2026-09-14 fix (S-URGENT): the noisy/never-wrap checks used to be plain regex substring matches
against the WHOLE command string, so the literal word `pytest` or `git` appearing anywhere --
including inside a quoted argument to an unrelated command, e.g. `echo "run pytest later"` or a
JSON string like `echo '{"cmd":"pytest -q"}'` piped to another program -- triggered wrap/skip
decisions that had nothing to do with what actually runs. Matching is now done on the TOKENIZED
pipeline: the command is split into segments on real shell operators (`|`, `&&`, `||`, `;`, `&`)
using `shlex` (which respects quoting, so text inside `'...'`/`"..."` is one opaque token and
never inspected for keywords), and only each segment's actual PROGRAM (argv[0], basename) is
checked against the noisy/never-wrap tables -- never substrings of arguments. A command that
can't be safely tokenized (unbalanced quotes) is left untouched (fail toward not wrapping).
"""
import json
import os
import re
import shlex
import shutil
import sys
import time

NOISY_PROGRAMS = {
    "pytest", "jest", "vitest", "make", "mvn", "tox", "ruff", "mypy", "eslint", "tsc",
    "ssh", "flatpak-spawn", "curl", "journalctl", "dmesg", "docker", "scp", "php", "phpunit",
}
NEVER_WRAP_PROGRAMS = {"git", "session-claim", "cat", "sed", "grep", "ls", "squire"}
PIPE_TRIM_PROGRAMS = {"head", "tail", "grep"}
SHELL_OPS = {"|", "||", "&&", ";", "&"}

INTERACTIVE_MARKERS = ("-it ", " -i ", "read -p", "/dev/tty")
HEREDOC_RE = re.compile(r"<<[-~]?\s*['\"]?\w+")


def tokenize_pipeline(command):
    """Split `command` into (operator_before, argv) segments, tokenized with shlex so quoted
    text is never split or keyword-matched. Raises ValueError on unbalanced quotes -- caller
    treats that as "can't tell", never as a match.

    A bare newline is a statement separator too (a multi-line Bash tool call is one `command`
    string joined by real `\\n`s) -- 2026-09-14 fix: shlex's default whitespace set SWALLOWS
    newlines as plain whitespace, which merged every line of a multi-line command into one
    giant segment and hid a noisy/denied command sitting on its own later line entirely (found
    live: a 3-line command whose 3rd line was a real `grep -r` was not denied). `\\n` is added
    to punctuation_chars and removed from whitespace so it survives as its own separator token,
    while a newline INSIDE a quoted string (part of the actual argument text) is unaffected --
    shlex only treats it as a separator outside of quotes."""
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


ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def strip_assignments(argv):
    """A segment like `FOO=1 BAR=x pytest -q` tokenizes to argv[0] == "FOO=1", so a naive
    program_name(argv) never sees `pytest`. Skip leading NAME=value env-prefix words (2026-09-18
    fix, TODO "squire pretool_wrap misses commands after a VAR= assignment") so the REAL command
    word is what gets checked against NOISY_PROGRAMS/NEVER_WRAP_PROGRAMS. A segment that is
    ONLY assignments (e.g. `D=$PWD/approvals` as its own `;`-separated segment) returns []."""
    i = 0
    while i < len(argv) and ASSIGNMENT_RE.match(argv[i]):
        i += 1
    return argv[i:]


def program_name(argv):
    argv = strip_assignments(argv)
    return os.path.basename(argv[0]) if argv else ""


def is_noisy_segment(prog, argv):
    if prog in NOISY_PROGRAMS:
        return True
    if prog in ("python", "python3") and len(argv) >= 3 and argv[1] == "-m" and argv[2] == "pytest":
        return True
    if prog in ("npm", "pnpm", "yarn"):
        rest = argv[1:]
        if rest and rest[0] == "run":
            rest = rest[1:]
        return bool(rest) and rest[0] in ("test", "build", "install")
    if prog in ("cargo", "go", "dotnet"):
        return len(argv) >= 2 and argv[1] in ("build", "test")
    if prog in ("gradle", "gradlew"):
        return True
    if prog in ("pip", "pip3"):
        return len(argv) >= 2 and argv[1] == "install"
    return False

# `# squire-raw: <reason>` anywhere in the command skips wrap/deny enforcement entirely --
# an intentional, auditable escape hatch rather than a silent bypass. Logged to a ledger
# (~/.squire/raw_overrides.jsonl) so overuse is visible without blocking the command that
# carries it (fail open: a ledger write failure never blocks the underlying command).
RAW_OVERRIDE_RE = re.compile(r"#\s*squire-raw:\s*(?P<reason>.+)")
RAW_OVERRIDE_LEDGER = os.path.expanduser("~/.squire/raw_overrides.jsonl")

# 2026-09-18 fix (TODO "squire raw-override log records false overrides"): the ledger was
# recording rows whose "reason" was literal TEMPLATE text -- `<reason>`, "marker", "escape
# hatch ..." -- lifted from a command that only MENTIONED the marker (e.g. a heredoc `git commit
# -m "$(cat <<'EOF' ... # squire-raw: <reason> ... EOF)"` -- this repo's own documented commit
# convention -- or a quoted string like `echo "docs say # squire-raw: <reason>"`). A real
# override is an actual unquoted shell comment on the executed command line, never text inside a
# quoted argument or a heredoc body, and never the placeholder text itself.
_HEREDOC_BODY_RE = re.compile(
    r"<<[-~]?\s*['\"]?(?P<delim>\w+)['\"]?[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=delim)\b",
    re.DOTALL,
)
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_PLACEHOLDER_REASONS = {"<reason>", "reason", "marker", "escape hatch"}


def _looks_like_placeholder(reason):
    r = reason.strip().rstrip(".").lower()
    if not r:
        return True
    if r in _PLACEHOLDER_REASONS:
        return True
    if r.startswith("<") and r.endswith(">"):
        return True
    if r.startswith("escape hatch"):
        return True
    return False


def _strip_non_executed_text(command):
    """Remove heredoc bodies, then quoted string contents, so only text that is actually an
    unquoted, executed part of the command line remains to be searched for the marker."""
    stripped = _HEREDOC_BODY_RE.sub("", command)
    return _QUOTED_RE.sub("", stripped)


def find_raw_override(command):
    if not command:
        return None
    m = RAW_OVERRIDE_RE.search(_strip_non_executed_text(command))
    if not m:
        return None
    reason = m.group("reason").strip()
    if _looks_like_placeholder(reason):
        return None
    return reason


def log_raw_override(payload, tool_name, command, reason):
    try:
        os.makedirs(os.path.dirname(RAW_OVERRIDE_LEDGER), exist_ok=True)
        payload = payload or {}
        row = {
            "ts": time.time(),
            "session": payload.get("session_id", "unknown"),
            "agent_id": payload.get("agent_id"),
            "agent_type": payload.get("agent_type"),
            "tool": tool_name,
            "reason": reason,
            "cmd_head": (command or "")[:80],
        }
        with open(RAW_OVERRIDE_LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


# Best-effort "repeated small raw grep" nudge (2026-09-18): search_guard.py already DENIES a
# broad sweep (grep -r/-R, rg, find|xargs grep); this handles the narrower case it deliberately
# allows -- a single, non-recursive `grep <pattern> <file>` -- which is fine once or twice but a
# sign the session should reach for `squire grep` once it keeps happening. Tracked per session_id
# in a small JSON file under ~/.squire/ (created if missing) with timestamps pruned to a short
# window; crossing the threshold adds `additionalContext` (never a deny -- this is a nudge, not
# a gate) suggesting `squire grep`. Any I/O failure here just means no nudge fires (fail open,
# same as the rest of this hook); it never blocks the underlying grep call.
GREP_NUDGE_STATE = os.path.expanduser(os.environ.get("SQUIRE_GREP_NUDGE_STATE", "~/.squire/grep_nudge_state.json"))
GREP_NUDGE_WINDOW_S = float(os.environ.get("SQUIRE_GREP_NUDGE_WINDOW_S", "300"))
GREP_NUDGE_THRESHOLD = int(os.environ.get("SQUIRE_GREP_NUDGE_THRESHOLD", "3"))


def has_raw_grep_segment(segments):
    return any(program_name(argv) == "grep" for _op, argv in segments)


def record_grep_call(session_id, now=None):
    """Append `now` to this session's raw-grep timestamp list, pruned to GREP_NUDGE_WINDOW_S,
    and return the resulting count. Pure function of (state, now) apart from the file I/O, so
    the counting logic itself is unit-testable without touching disk (see test_pretool_wrap.py)."""
    now = now if now is not None else time.time()
    try:
        os.makedirs(os.path.dirname(GREP_NUDGE_STATE), exist_ok=True)
        try:
            with open(GREP_NUDGE_STATE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        times = [t for t in state.get(session_id, []) if now - t < GREP_NUDGE_WINDOW_S]
        times.append(now)
        state[session_id] = times
        with open(GREP_NUDGE_STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        return len(times)
    except OSError:
        return 0


def prune_and_count(timestamps, now, window_s=GREP_NUDGE_WINDOW_S):
    """The counting rule in isolation, no I/O: how many of `timestamps` (plus `now` itself) fall
    within `window_s` seconds of `now`. Exposed separately so the logic can be tested without a
    state file."""
    return len([t for t in timestamps if now - t < window_s]) + 1


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
    if HEREDOC_RE.search(cmd):
        return False
    if any(m in cmd for m in INTERACTIVE_MARKERS):
        return False
    try:
        segments = tokenize_pipeline(cmd)
    except ValueError:
        return False  # can't safely tokenize (e.g. unbalanced quotes) -- don't touch it
    if not segments:
        return False

    # NEVER_WRAP_PROGRAMS still vetoes the WHOLE pipeline the instant any segment runs one --
    # unchanged, deliberate behavior (see test_session_claim_is_never_wrapped): a gate command
    # like `session-claim check . && pytest -q` must never have its exit-code-critical output
    # folded into squire's condensed run, even when a noisy command follows it. Only the
    # argv[0]/assignment-word detection below is the 2026-09-18 fix (TODO "squire pretool_wrap
    # misses commands after a VAR= assignment"): program_name() now skips leading NAME=value
    # env-prefix words, so `FOO=1 pytest` and a `;`-separated `D=x; flatpak-spawn ...` are
    # correctly recognized as noisy on the REAL command word, not on the assignment token.
    noisy_found = False
    for op, argv in segments:
        prog = program_name(argv)
        if prog in NEVER_WRAP_PROGRAMS:
            return False
        if op == "|" and prog in PIPE_TRIM_PROGRAMS:
            return False
        if is_noisy_segment(prog, strip_assignments(argv)):
            noisy_found = True
    return noisy_found


def build_wrapped_command(original, agent_id=None, agent_type=None):
    squire_argv = find_squire_bin()
    # squire_argv is e.g. ["squire"] or [sys.executable, ".../squire.py"]; render as a single
    # shell-safe command string since Bash tool_input.command is one string executed with shell=True.
    quoted_squire = " ".join(shlex.quote(a) for a in squire_argv)
    # agent_id/agent_type (2026-09-18, TODO "usage report can't attribute per agent"): the
    # harness does not export these as env vars on its own -- PreToolUse stdin carries them only
    # inside a subagent (verified against https://code.claude.com/docs/en/hooks), so this hook
    # forwards them as env-var prefixes on the wrapped command. squire.py's log_call() reads
    # SQUIRE_AGENT_ID/SQUIRE_AGENT_TYPE and records them on the ledger row. Omitted entirely on
    # the main thread (both None), so a ledger row's absence of these fields still means "main
    # thread", not "not recorded".
    env_prefix = ""
    if agent_id:
        env_prefix += f"SQUIRE_AGENT_ID={shlex.quote(str(agent_id))} "
    if agent_type:
        env_prefix += f"SQUIRE_AGENT_TYPE={shlex.quote(str(agent_type))} "
    return f"{env_prefix}{quoted_squire} run -- bash -c {shlex.quote(original)}"


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

    reason = find_raw_override(command)
    if reason:
        log_raw_override(payload, "Bash", command, reason)
        return 0

    if not should_wrap(command):
        # Not a noisy command to wrap -- but if it's a raw `grep` (the single-file case
        # search_guard deliberately allows), count it and nudge once the session leans on it.
        try:
            segments = tokenize_pipeline(command)
        except ValueError:
            segments = []
        if has_raw_grep_segment(segments):
            session_id = payload.get("session_id", "unknown")
            count = record_grep_call(session_id)
            if count >= GREP_NUDGE_THRESHOLD:
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": (
                            f"{count} raw grep calls in the last {int(GREP_NUDGE_WINDOW_S)}s -- "
                            "consider `squire grep \"<question>\" [path]` for the rest of this search."
                        ),
                    }
                }))
        return 0

    new_command = build_wrapped_command(
        command, agent_id=payload.get("agent_id"), agent_type=payload.get("agent_type")
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"command": new_command},
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

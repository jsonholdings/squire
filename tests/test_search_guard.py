"""Tests for hooks/search_guard.py: denies broad raw search sweeps (grep -r/-R, bare rg,
find|xargs grep) for Bash unless `# squire-raw:` marked; never denies a single-file grep;
allows-with-context (can't deny) an unnarrowed Grep tool call, logging it to the ledger."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "search_guard.py"


def run_hook(payload, env_overrides=None, tmp_home=None):
    env = {**os.environ}
    if tmp_home:
        env["HOME"] = str(tmp_home)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    return proc


def bash_payload(command, session="sess-1"):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session}


def grep_payload(pattern, path=None, glob=None, session="sess-1"):
    ti = {"pattern": pattern}
    if path:
        ti["path"] = path
    if glob:
        ti["glob"] = glob
    return {"tool_name": "Grep", "tool_input": ti, "session_id": session}


def decision(proc):
    out = json.loads(proc.stdout.strip())
    return out["hookSpecificOutput"]["permissionDecision"]


# --- Bash: recursive/multi-file sweeps denied ---

def test_grep_dash_r_is_denied():
    p = run_hook(bash_payload("grep -r TODO ."))
    assert decision(p) == "deny"


def test_grep_dash_R_combined_flags_is_denied():
    p = run_hook(bash_payload("grep -Rn pattern src/"))
    assert decision(p) == "deny"


def test_bare_ripgrep_is_denied():
    p = run_hook(bash_payload("rg 'TODO' ."))
    assert decision(p) == "deny"


def test_find_pipe_xargs_grep_is_denied():
    p = run_hook(bash_payload("find . -name '*.py' | xargs grep -l TODO"))
    assert decision(p) == "deny"


# --- Bash: single-file grep is NEVER denied ---

def test_single_file_grep_passes():
    p = run_hook(bash_payload("grep TODO myfile.py"))
    assert p.stdout.strip() == ""


def test_single_file_grep_with_pattern_flag_passes():
    p = run_hook(bash_payload("grep -n TODO myfile.py"))
    assert p.stdout.strip() == ""


# --- Bash: git/session-claim/squire-raw untouched ---

def test_non_grep_bash_passes():
    p = run_hook(bash_payload("git log --oneline"))
    assert p.stdout.strip() == ""


def test_squire_raw_marker_skips_deny_and_logs(tmp_path):
    p = run_hook(bash_payload("grep -r TODO .  # squire-raw: one-off audit"), tmp_home=tmp_path)
    assert p.stdout.strip() == ""
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    assert ledger.exists()
    row = json.loads(ledger.read_text().strip().splitlines()[-1])
    assert row["reason"] == "one-off audit"
    assert row["tool"] == "Bash"


# --- Grep tool: no escape hatch, so allow-with-context + log instead of deny ---

def test_unnarrowed_grep_tool_call_is_allowed_with_context_and_logged(tmp_path):
    p = run_hook(grep_payload("TODO"), tmp_home=tmp_path)
    assert p.returncode == 0
    out = json.loads(p.stdout.strip())
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "squire grep" in out["hookSpecificOutput"]["additionalContext"]
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    assert ledger.exists()
    row = json.loads(ledger.read_text().strip().splitlines()[-1])
    assert row["reason"] == "grep-tool-unnarrowed"


def test_narrowed_grep_tool_call_with_path_passes_silently():
    p = run_hook(grep_payload("TODO", path="src/foo.py"))
    assert p.stdout.strip() == ""


def test_narrowed_grep_tool_call_with_glob_passes_silently():
    p = run_hook(grep_payload("TODO", glob="*.py"))
    assert p.stdout.strip() == ""


# --- fail-open behaviour ---

def test_disable_flag_fails_open():
    p = run_hook(bash_payload("grep -r TODO ."), env_overrides={"SQUIRE_HOOK_DISABLE": "1"})
    assert p.stdout.strip() == ""


def test_malformed_stdin_fails_open():
    proc = subprocess.run([sys.executable, str(HOOK)], input="{not json", text=True,
                          capture_output=True, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_missing_tool_input_fails_open():
    proc = subprocess.run([sys.executable, str(HOOK)],
                          input=json.dumps({"tool_name": "Bash"}), text=True,
                          capture_output=True, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --- S-URGENT 2026-09-14: keyword-in-quoted-text false positives -----------------------------
# The old RECURSIVE_GREP_RE matched `grep -r`/`rg` text anywhere in the raw command string, so
# it appeared inside a quoted argument to an unrelated command (echo, a JSON string piped
# elsewhere) got denied even though no grep/rg ever ran. Matching moved to tokenized pipeline
# segments (argv[0] of each stage only); these prove the false positives are gone and the
# original repro from the incident report no longer denies.

def test_grep_dash_r_text_inside_quoted_echo_arg_is_not_denied():
    p = run_hook(bash_payload('echo skip this grep -rn foo test'))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_grep_dash_r_text_inside_quoted_json_piped_elsewhere_is_not_denied():
    p = run_hook(bash_payload('echo \'{"cmd":"grep -rn foo bar"}\' | wc -c'))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_bare_rg_word_inside_quoted_arg_is_not_denied():
    p = run_hook(bash_payload('echo "please rg through this"'))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_unbalanced_quotes_are_left_untouched_not_denied():
    p = run_hook(bash_payload("echo 'unbalanced quote grep -r x"))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_real_grep_dash_r_still_denied_after_tokenization_fix():
    # Regression guard: the false-positive fix must not have accidentally broken the true
    # positive it sits next to.
    p = run_hook(bash_payload("grep -rn TODO ."))
    assert decision(p) == "deny"


def test_real_grep_dash_r_on_a_later_line_of_a_multiline_command_is_denied():
    # S-URGENT 2026-09-14, found live: a multi-line Bash tool call is ONE `command` string
    # joined by real newlines. shlex's default whitespace set swallows '\n' as plain
    # whitespace, merging every line into the first line's argv and hiding a real `grep -r`
    # sitting on a later line from ever being checked (harmless echo commands on lines 1-2,
    # then the actual sweep on line 3 sailed through). Locks in the newline-as-separator fix.
    p = run_hook(bash_payload(
        "echo skip this grep -rn foo test\n"
        "echo '---now a real deny---'\n"
        "grep -r \"TODO\" /tmp/nonexistent_dir_xyz 2>&1\n"
        "echo done"
    ))
    assert decision(p) == "deny"


def test_find_pipe_xargs_grep_still_denied_after_tokenization_fix():
    # Caught a real bug during the tokenization rewrite: the pipe-operator check compared the
    # WRONG segment's operator (the one before `find`, always None, instead of the one before
    # `xargs`, which is "|"), so this case silently stopped denying. Locks in the fix.
    p = run_hook(bash_payload("find . -name '*.py' | xargs grep -l TODO"))
    assert decision(p) == "deny"


# --- 2026-09-18 fixes -------------------------------------------------------

def test_var_assignment_prefix_before_grep_dash_r_is_still_denied():
    p = run_hook(bash_payload("FOO=1 grep -r foo ."))
    assert p.returncode == 0
    assert decision(p) == "deny"


def test_raw_override_inside_quoted_commit_message_is_not_logged(tmp_path):
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    cmd = 'git commit -m "docs: mention # squire-raw: <reason>"; grep -r foo .'
    p = run_hook(bash_payload(cmd), env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert decision(p) == "deny"  # the real grep -r is still denied, not swallowed
    assert not ledger.exists()


def test_real_unquoted_raw_override_with_real_reason_is_still_logged_and_allowed(tmp_path):
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    cmd = "grep -r foo . # squire-raw: locked venv, need raw access"
    p = run_hook(bash_payload(cmd), env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert p.stdout.strip() == ""  # allowed through, no deny output
    assert ledger.exists()
    row = json.loads(ledger.read_text().strip().splitlines()[-1])
    assert row["reason"] == "locked venv, need raw access"


def test_placeholder_reason_alone_does_not_bypass_deny(tmp_path):
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    cmd = "grep -r foo . # squire-raw: <reason>"
    p = run_hook(bash_payload(cmd), env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert decision(p) == "deny"
    assert not ledger.exists()


def test_agent_fields_recorded_on_override_log(tmp_path):
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    payload = bash_payload("grep -r foo . # squire-raw: needed for a one-off audit")
    payload["agent_id"] = "agent-9"
    payload["agent_type"] = "worker"
    p = run_hook(payload, env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    row = json.loads(ledger.read_text().strip().splitlines()[-1])
    assert row["agent_id"] == "agent-9"
    assert row["agent_type"] == "worker"

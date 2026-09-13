"""Tests for hooks/posttool_condense.py against a fake squire binary (no model)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "posttool_condense.py"
FAKE_SQUIRE = ROOT / "tests" / "fake_squire.py"


def run_hook(payload, env_overrides=None):
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    return proc


def bash_payload(command, output_lines):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": "\n".join(output_lines),
    }


def test_short_output_passes_through_unchanged():
    p = run_hook(bash_payload("echo hi", ["line"] * 10))
    assert p.returncode == 0
    assert p.stdout.strip() == ""  # no hookSpecificOutput emitted -> nothing added


def test_long_output_gets_squire_summary_with_exit_code_and_tail():
    p = run_hook(bash_payload("some-noisy-command", [f"line {i}" for i in range(200)]))
    assert p.returncode == 0
    out = json.loads(p.stdout.strip())
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "[squire]" in ctx
    assert "line 199" in ctx  # last raw lines present
    assert "FAKE SUMMARY" in ctx


def test_non_bash_tool_is_skipped():
    payload = {"tool_name": "Read", "tool_input": {}, "tool_response": "\n".join(["x"] * 200)}
    p = run_hook(payload)
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_git_diff_command_is_never_touched():
    p = run_hook(bash_payload("git diff HEAD~1", [f"line {i}" for i in range(200)]))
    assert p.stdout.strip() == ""


def test_session_claim_command_is_never_touched():
    p = run_hook(bash_payload("session-claim check .", [f"line {i}" for i in range(200)]))
    assert p.stdout.strip() == ""


def test_squire_failure_falls_back_to_unchanged_output():
    p = run_hook(bash_payload("some-noisy-command", [f"line {i}" for i in range(200)]),
                 env_overrides={"FAKE_SQUIRE_FAIL": "1"})
    assert p.returncode == 0
    assert p.stdout.strip() == ""  # squire crashed: original output stands, nothing added


def test_squire_backend_down_falls_back_to_unchanged_output():
    p = run_hook(bash_payload("some-noisy-command", [f"line {i}" for i in range(200)]),
                 env_overrides={"FAKE_SQUIRE_BACKEND_DOWN": "1"})
    assert p.stdout.strip() == ""


def test_disable_flag_produces_no_output():
    p = run_hook(bash_payload("some-noisy-command", [f"line {i}" for i in range(200)]),
                 env_overrides={"SQUIRE_HOOK_DISABLE": "1"})
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_malformed_stdin_does_nothing():
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True,
                          capture_output=True, env=env, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""

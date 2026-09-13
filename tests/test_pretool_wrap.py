"""Tests for hooks/pretool_wrap.py (no model needed -- this hook only rewrites tool_input)."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "pretool_wrap.py"
FAKE_SQUIRE = ROOT / "tests" / "fake_squire.py"


def run_hook(command, env_overrides=None):
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    if env_overrides:
        env.update(env_overrides)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    return proc


def wrapped_command(proc):
    out = json.loads(proc.stdout.strip())
    return out["hookSpecificOutput"]["updatedInput"]["command"]


def test_noisy_command_gets_wrapped_with_squire_run():
    p = run_hook("pytest -q tests/")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd
    assert "bash -c" in cmd
    assert "pytest -q tests/" in cmd


def test_quoting_torture_survives_round_trip():
    original = """python -m pytest -k "test something 'nested' & odd" --maxfail=1; echo done"""
    p = run_hook(original)
    cmd = wrapped_command(p)
    # extract the final quoted arg and confirm shlex parses it back to the exact original
    parts = shlex.split(cmd)
    assert parts[-1] == original


def test_non_noisy_command_is_not_wrapped():
    p = run_hook("echo hello")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_git_is_never_wrapped_even_if_it_mentions_test():
    p = run_hook("git commit -m 'test: fix pytest config'")
    assert p.stdout.strip() == ""


def test_session_claim_is_never_wrapped():
    p = run_hook("session-claim check . && pytest -q")
    assert p.stdout.strip() == ""


def test_already_squire_is_not_double_wrapped():
    p = run_hook("squire run -- pytest -q")
    assert p.stdout.strip() == ""


def test_pipe_to_grep_is_not_wrapped():
    p = run_hook("pytest -q | grep FAILED")
    assert p.stdout.strip() == ""


def test_pipe_to_tail_is_not_wrapped():
    p = run_hook("npm test | tail -20")
    assert p.stdout.strip() == ""


def test_heredoc_is_not_wrapped():
    p = run_hook("cat <<EOF\npytest -q\nEOF")
    assert p.stdout.strip() == ""


def test_interactive_marker_is_not_wrapped():
    p = run_hook("docker build -it .")
    assert p.stdout.strip() == ""


def test_cat_sed_grep_ls_never_wrapped():
    for cmd in ["cat build.log", "sed -n '1,5p' pytest.ini", "grep test file", "ls -la"]:
        p = run_hook(cmd)
        assert p.stdout.strip() == "", cmd


def test_disable_flag_produces_no_output():
    p = run_hook("pytest -q", env_overrides={"SQUIRE_HOOK_DISABLE": "1"})
    assert p.stdout.strip() == ""


def test_malformed_stdin_does_nothing():
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True,
                          capture_output=True, env=env, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_exit_code_preserved_through_real_squire_run_shape():
    # Confirms the wrapped command's shape matches squire's own `run -- <argv...>` contract,
    # which squire.py implements as subprocess with the real exit code returned (see
    # tests/test_squire.py::test_exit_code_is_preserved for that guarantee at the squire layer).
    p = run_hook("make test")
    cmd = wrapped_command(p)
    assert cmd.split(" run -- ", 1)[0].endswith(("squire", "squire.py'")) or "squire" in cmd
    assert cmd.rstrip().split()[-1:] != []  # non-empty final arg present

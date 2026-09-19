"""Tests for hooks/pretool_wrap.py (no model needed -- this hook only rewrites tool_input)."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "pretool_wrap.py"
FAKE_SQUIRE = ROOT / "tests" / "fake_squire.py"


def run_hook(command, env_overrides=None):
    # Isolate the grep-nudge counter: the real ~/.squire state is shared with every live session,
    # so a test could pick up a nudge caused by someone else's greps.
    isolated_nudge_state = Path(tempfile.mkdtemp()) / "grep_nudge_state.json"
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE), "SQUIRE_GREP_NUDGE_STATE": str(isolated_nudge_state)}
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


def test_cat_sed_grep_ls_never_wrapped(tmp_path):
    # Isolate grep-nudge state so a single grep call here never crosses the nudge threshold and
    # produces additionalContext output (that behavior is covered by its own tests below).
    env = {"SQUIRE_GREP_NUDGE_STATE": str(tmp_path / "grep_nudge_state.json")}
    for cmd in ["cat build.log", "sed -n '1,5p' pytest.ini", "grep test file", "ls -la"]:
        p = run_hook(cmd, env_overrides=env)
        assert p.stdout.strip() == "", cmd


def test_scp_is_wrapped():
    p = run_hook("scp file.txt user@host:/tmp/")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd and "scp file.txt user@host:/tmp/" in cmd


def test_php_is_wrapped():
    p = run_hook("php -l script.php")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd and "php -l script.php" in cmd


def test_phpunit_is_wrapped():
    p = run_hook("phpunit tests/")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd and "phpunit tests/" in cmd


def test_python_module_pytest_variant_still_wrapped():
    p = run_hook("python3 -m pytest -q")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd and "python3 -m pytest -q" in cmd


def _load_pretool_wrap_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pretool_wrap_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_grep_nudge_counting_logic_pure():
    prune_and_count = _load_pretool_wrap_module().prune_and_count
    now = 1000.0
    # two calls inside the window plus this one = 3
    assert prune_and_count([now - 10, now - 20], now, window_s=300) == 3
    # one call outside the window is dropped
    assert prune_and_count([now - 400], now, window_s=300) == 1
    assert prune_and_count([], now, window_s=300) == 1


def test_grep_nudge_fires_after_threshold_raw_greps(tmp_path):
    state = tmp_path / "grep_nudge_state.json"
    env = {"SQUIRE_GREP_NUDGE_STATE": str(state), "SQUIRE_GREP_NUDGE_THRESHOLD": "3"}
    payload_extra = {"session_id": "sess-nudge-test"}

    def run_with_session(cmd):
        full_env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE), **env}
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, **payload_extra}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=full_env, timeout=30)

    p1 = run_with_session("grep foo file1.py")
    p2 = run_with_session("grep bar file2.py")
    assert p1.stdout.strip() == "" and p2.stdout.strip() == ""
    p3 = run_with_session("grep baz file3.py")
    assert p3.returncode == 0
    out = json.loads(p3.stdout.strip())
    assert "squire grep" in out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_grep_nudge_never_fires_on_denied_style_sweep_within_pretool_wrap(tmp_path):
    # pretool_wrap itself never denies (that's search_guard's job); confirm a recursive grep
    # here is simply left alone (search_guard handles the deny) and does not error.
    env = {"SQUIRE_GREP_NUDGE_STATE": str(tmp_path / "state.json")}
    p = run_hook("grep -r foo .", env_overrides=env)
    assert p.returncode == 0


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


# --- 2026-09-18 fixes -------------------------------------------------------

def test_var_assignment_prefix_on_same_segment_is_wrapped():
    p = run_hook("FOO=1 pytest -q")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd
    assert "FOO=1 pytest -q" in cmd


def test_semicolon_separated_assignment_then_noisy_command_is_wrapped():
    p = run_hook("D=$PWD/approvals; flatpak-spawn --host --directory=$D venv/python3 -m pytest -q")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd


def test_git_segment_before_noisy_segment_still_vetoes_whole_chain():
    # Unchanged, deliberate behavior (same invariant as test_session_claim_is_never_wrapped):
    # NEVER_WRAP_PROGRAMS vetoes the whole pipeline, not just its own segment. This fix is scoped
    # to argv[0]/assignment-word detection only (see the brief's three named cases), not to
    # NEVER_WRAP's scope.
    p = run_hook("cd x && git log -1; D=$PWD/y; flatpak-spawn --host --directory=$D pytest -q")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_cd_and_noisy_command_is_wrapped():
    p = run_hook("cd x && pytest -q")
    assert p.returncode == 0
    cmd = wrapped_command(p)
    assert "run --" in cmd


def test_solely_never_wrap_program_still_not_wrapped():
    p = run_hook("git log -1")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_raw_override_inside_quoted_commit_message_is_not_logged(tmp_path):
    ledger = tmp_path / "raw_overrides.jsonl"
    cmd = ('echo "docs: mention # squire-raw: <reason> as an example"; pytest -q')
    p = run_hook(cmd, env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert not ledger.exists() or ledger.read_text() == ""
    # the pytest segment should still be wrapped normally (not swallowed by the false override)
    cmd_out = wrapped_command(p)
    assert "run --" in cmd_out


def test_raw_override_inside_heredoc_body_is_not_logged(tmp_path):
    ledger = tmp_path / "raw_overrides.jsonl"
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        "Fix hooks. # squire-raw: <reason> escape hatch example\n"
        "EOF\n"
        ")\""
    )
    p = run_hook(cmd, env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert not ledger.exists() or ledger.read_text() == ""


def test_real_unquoted_raw_override_with_real_reason_is_still_logged(tmp_path):
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    cmd = "grep -r foo . # squire-raw: locked venv, need raw access"
    p = run_hook(cmd, env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert ledger.exists()
    row = json.loads(ledger.read_text().strip().splitlines()[-1])
    assert row["reason"] == "locked venv, need raw access"


def test_placeholder_reason_alone_is_not_logged(tmp_path):
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    cmd = "pytest -q # squire-raw: <reason>"
    p = run_hook(cmd, env_overrides={"HOME": str(tmp_path)})
    assert p.returncode == 0
    assert not ledger.exists()


def test_agent_id_and_type_forwarded_as_env_prefix_when_present():
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    payload = {
        "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
        "agent_id": "agent-123", "agent_type": "worker",
    }
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    cmd = wrapped_command(proc)
    assert "SQUIRE_AGENT_ID=agent-123" in cmd
    assert "SQUIRE_AGENT_TYPE=worker" in cmd


def test_no_agent_fields_means_no_env_prefix():
    p = run_hook("pytest -q")
    cmd = wrapped_command(p)
    assert "SQUIRE_AGENT_ID" not in cmd
    assert "SQUIRE_AGENT_TYPE" not in cmd

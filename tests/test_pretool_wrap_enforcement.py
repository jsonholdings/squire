"""Tests for the enforcement extensions to hooks/pretool_wrap.py: new NOISY_RE tools
(ssh/docker/flatpak-spawn/curl/journalctl/dmesg) and the `# squire-raw:` override marker
with its raw_overrides.jsonl ledger. See tests/test_pretool_wrap.py for the original suite."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "pretool_wrap.py"
FAKE_SQUIRE = ROOT / "tests" / "fake_squire.py"


def run_hook(command, env_overrides=None, tmp_home=None):
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    if tmp_home:
        env["HOME"] = str(tmp_home)
    if env_overrides:
        env.update(env_overrides)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "sess-1"}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    return proc


def wrapped_command(proc):
    out = json.loads(proc.stdout.strip())
    return out["hookSpecificOutput"]["updatedInput"]["command"]


# --- new NOISY_RE tools: each proven wrapped, plus a passthrough sanity check ---

def test_ssh_is_wrapped():
    p = run_hook("ssh example-host 'uptime'")
    assert "run --" in wrapped_command(p)


def test_docker_any_subcommand_is_wrapped():
    p = run_hook("docker ps -a")
    assert "run --" in wrapped_command(p)


def test_flatpak_spawn_is_wrapped():
    p = run_hook("flatpak-spawn --host node --version")
    assert "run --" in wrapped_command(p)


def test_bare_curl_is_wrapped():
    p = run_hook("curl -s https://example.com/")
    assert "run --" in wrapped_command(p)


def test_journalctl_is_wrapped():
    p = run_hook("journalctl -u sshd --since today")
    assert "run --" in wrapped_command(p)


def test_dmesg_is_wrapped():
    p = run_hook("dmesg -T")
    assert "run --" in wrapped_command(p)


def test_git_still_passes_through_even_with_new_keywords_nearby():
    p = run_hook("git log --oneline -- docker/")
    assert p.stdout.strip() == ""


def test_session_claim_still_passes_through():
    p = run_hook("session-claim check .")
    assert p.stdout.strip() == ""


# --- squire-raw override marker: skips wrap AND denial, logs to ledger ---

def test_raw_override_marker_skips_wrap(tmp_path):
    p = run_hook("docker build .  # squire-raw: benchmarking raw docker output", tmp_home=tmp_path)
    assert p.returncode == 0
    assert p.stdout.strip() == ""  # not wrapped


def test_raw_override_marker_logs_to_ledger(tmp_path):
    run_hook("curl -s https://x  # squire-raw: quick manual check", tmp_home=tmp_path)
    ledger = tmp_path / ".squire" / "raw_overrides.jsonl"
    assert ledger.exists()
    row = json.loads(ledger.read_text().strip().splitlines()[-1])
    assert row["tool"] == "Bash"
    assert row["reason"] == "quick manual check"
    assert row["session"] == "sess-1"
    assert "curl" in row["cmd_head"]


def test_ledger_write_failure_fails_open(tmp_path, monkeypatch):
    # HOME points at a path where ~/.squire can't be created (a file, not a dir) --
    # the command must still not be blocked/crashed by the failed ledger write.
    blocked_home = tmp_path / "blockedhome"
    blocked_home.mkdir()
    (blocked_home / ".squire").write_text("not a directory")
    p = run_hook("docker ps  # squire-raw: test fail-open", tmp_home=blocked_home)
    assert p.returncode == 0


def test_disable_flag_still_works_with_new_tools():
    p = run_hook("ssh host cmd", env_overrides={"SQUIRE_HOOK_DISABLE": "1"})
    assert p.stdout.strip() == ""


def test_malformed_stdin_still_fails_open():
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    proc = subprocess.run([sys.executable, str(HOOK)], input="{not json", text=True,
                          capture_output=True, env=env, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --- S-URGENT 2026-09-14: keyword-in-quoted-text false positives -----------------------------
# The old NOISY_RE/NEVER_WRAP_RE matched anywhere in the raw command string, so a noisy or
# never-wrap keyword sitting inside a quoted argument to an unrelated command (an echo, a git
# commit message, a JSON string) triggered a decision that had nothing to do with what actually
# runs. Matching moved to tokenized pipeline segments (argv[0] of each stage only); these prove
# the false positives are gone while the true positives above still fire.

def test_pytest_word_inside_quoted_echo_arg_is_not_wrapped():
    p = run_hook('echo "run pytest later"')
    assert p.stdout.strip() == ""


def test_ssh_word_inside_quoted_arg_is_not_wrapped():
    p = run_hook('echo "please ssh into that box"')
    assert p.stdout.strip() == ""


def test_git_commit_message_containing_noisy_keyword_is_not_wrapped():
    p = run_hook('git commit -m "fixed pytest flake"')
    assert p.stdout.strip() == ""


def test_never_wrap_keyword_inside_quoted_arg_does_not_suppress_real_noisy_command():
    # "git" only appears inside a quoted arg here -- the actual command is `ssh`, which SHOULD
    # still be wrapped. The old whole-string NEVER_WRAP_RE would have suppressed this.
    p = run_hook('ssh example-host \'echo "not actually git"\'')
    assert p.returncode == 0
    assert wrapped_command(p).startswith(("squire", sys.executable))


def test_unbalanced_quotes_are_left_untouched_not_wrapped():
    p = run_hook("echo 'unbalanced quote pytest")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_noisy_command_on_a_later_line_of_a_multiline_command_is_wrapped():
    # S-URGENT 2026-09-14 companion fix to test_search_guard.py's version: newline must be a
    # statement separator, or a noisy command on line 2+ of a multi-line Bash call is merged
    # into line 1's argv and never recognized as its own command.
    p = run_hook("echo hello\npython3 -m pytest -q")
    assert p.returncode == 0
    assert wrapped_command(p)  # non-empty updatedInput.command means it wrapped

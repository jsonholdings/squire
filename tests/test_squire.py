"""Safety controls for Squire. None of these need a running model."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SQUIRE = Path(__file__).resolve().parents[1] / "squire.py"
# A dedicated lock file, never the real ~/.squire/llm.lock. Without this, every test process in
# this file competes for the SAME flock as any OTHER squire process on the workstation -- other
# sessions' real (backend-up) calls under load can hold it for the full LLM_LOCK_TIMEOUT (240s
# default), so a DOWN-backend test here queues behind real work and blows its own subprocess
# timeout. Root-caused 2026-09-13 (S7) as the actual cause of the "5 tests TimeoutExpired under
# load" finding from S6b/S9 -- it was lock contention with concurrent real usage, not flakiness
# in squire's own logic.
_LOCK_DIR = tempfile.mkdtemp(prefix="squire-test-lock-")
DOWN = {**os.environ, "SQUIRE_OLLAMA": "http://127.0.0.1:1",  # nothing listens on port 1
        "SQUIRE_LLM_LOCK": os.path.join(_LOCK_DIR, "llm.lock")}

# Temp repos get their own identity and ignore the machine's git config. A CI runner has no
# user.name/email, so `git commit` there exits 128. These tests once passed only on machines
# that happened to have a global identity (the CI run failed, 2026-09-12).
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "squire tests", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
           "GIT_COMMITTER_NAME": "squire tests", "GIT_COMMITTER_EMAIL": "tests@example.invalid"}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
                   check=True, env=GIT_ENV)


def run(*args, stdin=None, env=None):
    return subprocess.run([sys.executable, str(SQUIRE), *args], input=stdin, text=True,
                          capture_output=True, env=env or DOWN, timeout=60)


def test_short_output_passes_through_raw():
    p = run("run", "--", "echo", "hello")
    assert p.returncode == 0
    assert "[squire] exit=0" in p.stdout and "hello" in p.stdout
    assert "local summary" not in p.stdout  # no model call for short output


def test_exit_code_is_preserved():
    p = run("run", "--", sys.executable, "-c", "import sys; print('boom'); sys.exit(3)")
    assert p.returncode == 3
    assert "[squire] exit=3" in p.stdout


FAKE_LAUNCHER = Path(__file__).resolve().parent / "fixtures" / "fake_nested_launcher.py"


def test_nested_launcher_exit_code_preserved():
    # Stands in for `flatpak-spawn --host docker run ...`: a launcher wrapping a real command.
    p = run("run", "--", sys.executable, str(FAKE_LAUNCHER), "5")
    assert p.returncode == 5
    assert "[squire] exit=5" in p.stdout


def test_nested_launcher_long_output_exit_code_preserved():
    # exit code 77 makes the fixture print 80 lines first, crossing the short/long threshold --
    # the >60-line summarization path must still preserve the real nested exit code.
    p = run("run", "--", sys.executable, str(FAKE_LAUNCHER), "77")
    assert p.returncode == 77
    assert "nested output line 79" in p.stdout  # raw tail is always shown


def test_nested_launcher_does_not_hang_on_inherited_stdin():
    # A launcher that attaches stdin (docker -i, ssh, some flatpak-spawn entry points) inherits
    # whatever fd squire got. Before the stdin=DEVNULL fix, an open PIPE stdin that squire's own
    # process is handed (as happens in a real interactive terminal) meant the nested process
    # blocked forever waiting for EOF -- indistinguishable from squire being broken. Give squire
    # an explicit open PIPE (never closed by this test) and bound the wait with a short timeout:
    # this test must finish quickly, proving squire itself closed stdin for the child rather than
    # forwarding this never-EOF pipe.
    p = subprocess.run([sys.executable, str(SQUIRE), "run", "--", sys.executable,
                        str(FAKE_LAUNCHER), "--read-stdin", "0"],
                       stdin=subprocess.PIPE, text=True, capture_output=True,
                       env=DOWN, timeout=15)
    assert p.returncode == 0
    assert "got 0 bytes from stdin" in p.stdout


def test_run_timeout_preserves_exit_code_and_output():
    code = "import sys, time\nprint('before sleep'); sys.stdout.flush()\ntime.sleep(5)\nsys.exit(0)"
    env = {**DOWN, "SQUIRE_RUN_TIMEOUT": "1"}
    p = run("run", "--", sys.executable, "-c", code, env=env)
    assert p.returncode == 124
    assert "before sleep" in p.stdout  # captured output before the kill is never lost


def test_long_output_keeps_raw_tail_and_exit_even_when_model_is_down():
    code = "import sys\nfor i in range(200): print('line', i)\nprint('FAILED t.py::x')\nsys.exit(1)"
    p = run("run", "--", sys.executable, "-c", code)
    assert p.returncode == 1
    assert "FAILED t.py::x" in p.stdout  # the raw tail is always shown
    assert "UNKNOWN" in p.stdout  # a down model is reported, never silent


def test_sum_reports_unknown_when_backend_down():
    p = run("sum", "-", stdin="some text\n")
    assert "UNKNOWN" in p.stdout


def test_help_lists_commands():
    p = run("--help")
    for word in ("run", "sum", "ask", "draft", "diff", "stats"):
        assert word in p.stdout


def test_json_mode_emits_valid_json_with_required_fields():
    import json
    p = run("sum", "-", "--json", stdin="some text\n")
    obj = json.loads(p.stdout)
    for key in ("cmd", "exit_code", "raw_tail", "summary", "assumed", "backend_ok"):
        assert key in obj
    assert obj["backend_ok"] is False  # backend is down in this test env


def test_diff_reports_no_diff_on_clean_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    p = subprocess.run([sys.executable, str(SQUIRE), "diff"], cwd=tmp_path, text=True,
                       capture_output=True, env=DOWN, timeout=60)
    assert "no diff" in p.stdout


def test_diff_shows_real_stat_even_when_model_is_down(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "f.txt").write_text("a\n")
    git(tmp_path, "add", "f.txt")
    git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "f.txt").write_text("a\nb\n")
    p = subprocess.run([sys.executable, str(SQUIRE), "diff"], cwd=tmp_path, text=True,
                       capture_output=True, env=DOWN, timeout=60)
    assert "f.txt" in p.stdout  # real diffstat always shown
    assert "UNKNOWN" in p.stdout  # backend down is reported, never silent


def test_stats_reports_real_chars_and_labels_estimate_separately(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"ts": 1, "cmd": "sum", "chars_in": 100, "chars_out": 20, "backend_ok": true}\n')
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("stats", env=env)
    assert "chars in=100 out=20 saved=80" in p.stdout  # REAL, computed
    assert "ESTIMATED" in p.stdout  # the token figure must never look like a measured fact
    assert "NOT a measured token count" in p.stdout


# ---- v0.2 core: redaction, localhost guard, auto model, triage, grep/doctor UNKNOWN paths ----
sys.path.insert(0, str(SQUIRE.parent))
import squire as sq  # noqa: E402


def test_redact_removes_secret_shapes_and_keeps_context():
    text = ("key github_pat_" + "A" * 30 + " and ghp_" + "b" * 36 + "\npassword = hunter2hunter2\n"
            "AKIA" + "ABCDEFGHIJKLMNOP" + "\n-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    out = sq.redact(text)
    for leaked in ("github_pat_AAAA", "ghp_bbbb", "hunter2hunter2", "AKIAABCDEFGHIJKLMNOP", "BEGIN RSA"):
        assert leaked not in out
    assert "password = [REDACTED]" in out and out.count("[REDACTED]") >= 5


def test_remote_backend_is_refused_unless_allowed(monkeypatch):
    import pytest
    with pytest.raises(sq.BackendError):
        sq.check_local("http://example.com:11434/api/generate")
    monkeypatch.setenv("SQUIRE_ALLOW_HOSTS", "ollama")
    sq.check_local("http://ollama:11434/api/generate")
    sq.check_local("http://127.0.0.1:11434")


def test_remote_backend_reports_unknown_not_a_network_call():
    # Must isolate SQUIRE_LLM_LOCK like DOWN does (S7) -- this test builds its own env from
    # os.environ instead of DOWN and was missed by that fix, so it shared the real
    # ~/.squire/llm.lock with any other squire process on the workstation. Under real
    # concurrent load that queued this call behind unrelated real work and blew its own 60s
    # subprocess timeout -- found 2026-09-13 (S8), same root cause as S7's benchmark miss.
    env = {**os.environ, "SQUIRE_OLLAMA": "http://203.0.113.9:11434",  # TEST-NET, must never be contacted
           "SQUIRE_LLM_LOCK": os.path.join(_LOCK_DIR, "llm.lock")}
    p = run("sum", "-", stdin="text\n", env=env)
    assert "UNKNOWN" in p.stdout and "not localhost" in p.stdout


def test_auto_model_picks_largest_installed_tier_that_fits():
    installed = ["qwen2.5:7b", "qwen2.5:14b", "nomic-embed-text:latest"]
    assert sq.pick_model(installed, 24000) == "qwen2.5:14b"  # 32b not installed
    assert sq.pick_model(installed, 8000) == "qwen2.5:7b"
    assert sq.pick_model(installed, None) == "qwen2.5:14b"  # unknown VRAM: largest installed tier
    assert sq.pick_model(["llama3:8b"], 24000) == "llama3:8b"


def test_triage_orders_by_real_age_and_skips_done(tmp_path):
    f = tmp_path / "INBOX.md"
    f.write_text("## [OPEN] 2026-09-10T10:00:00-04:00 — newer\nbody\n"
                 "## [DONE 2026-09-11] 2026-09-01T00:00:00Z — closed\n"
                 "## [OPEN] 2026-09-01T10:00:00-04:00 — older\nbody\n")
    import json as _j
    p = run("triage", str(f), "--json")
    obj = _j.loads(p.stdout)
    titles = [i["heading"] for i in obj["open_items"]]
    assert len(titles) == 2 and "older" in titles[0] and "newer" in titles[1]
    assert obj["backend_ok"] is False and all(i["guess"] is None for i in obj["open_items"])
    assert obj["open_items"][0]["age_days"] > obj["open_items"][1]["age_days"]


def test_grep_backend_down_is_unknown_exit_2_and_reports_coverage(tmp_path):
    (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path)],
                       text=True, capture_output=True, env=DOWN, timeout=60)
    assert p.returncode == 2
    assert "indexed 1 files" in p.stdout and "UNKNOWN" in p.stdout


def test_grep_backend_failure_still_logs_a_ledger_row(tmp_path):
    # S10 (found 2026-09-13): a `squire grep` that fails/times out talking to the embedding
    # backend used to die inside the BackendError branch with NO ledger row at all -- from the
    # ledger's side that run was indistinguishable from one that never happened. Control: a
    # SUCCESSFUL grep (backend up would log too) isn't available here (no real backend in CI),
    # so the control is the ledger starting empty -- proving any row present came from this run.
    (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    assert not ledger.exists()  # control: nothing logged yet
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path)],
                       text=True, capture_output=True, env=env, timeout=60)
    assert p.returncode == 2
    import json as _j
    rows = [_j.loads(l) for l in ledger.read_text().splitlines()]
    grep_rows = [r for r in rows if r["cmd"] == "grep"]
    assert len(grep_rows) == 1
    assert grep_rows[0]["backend_ok"] is False
    assert grep_rows[0]["status"] in ("timeout", "error")
    assert grep_rows[0]["gen_s"] is not None and grep_rows[0]["gen_s"] >= 0


def test_doctor_backend_down_exits_2():
    p = run("doctor", "--json")
    import json as _j
    assert p.returncode == 2 and _j.loads(p.stdout)["ready"] is False


def test_run_json_flag_after_separator_belongs_to_the_command():
    p = run("run", "--", "echo", "--json")
    assert "--json" in p.stdout and p.returncode == 0


def test_ledger_records_session_id(tmp_path):
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger), "CLAUDE_CODE_SESSION_ID": "abc123"}
    run("sum", "-", stdin="x\n", env=env)
    import json as _j
    assert _j.loads(ledger.read_text().splitlines()[0])["session"] == "abc123"


# ---- S4: ledger test isolation + source field ----

def test_ledger_rows_tagged_with_source_from_conftest(tmp_path):
    # conftest.py sets SQUIRE_SOURCE=test for the whole suite; this asserts it actually lands in
    # the row, not just that the env var exists.
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    run("sum", "-", stdin="x\n", env=env)
    import json as _j
    assert _j.loads(ledger.read_text().splitlines()[0])["source"] == "test"


def test_stats_excludes_source_test_rows_but_keeps_them_in_the_file(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"cmd": "sum", "chars_in": 100, "chars_out": 20, "backend_ok": true, "source": "test"}\n'
        '{"cmd": "sum", "chars_in": 50, "chars_out": 10, "backend_ok": true, "source": "cli"}\n'
    )
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("stats", env=env)
    # control: without the filter both rows would sum to in=150 out=30 -- prove the filter fired
    assert "chars in=50 out=10 saved=40" in p.stdout
    assert "1 test rows excluded, not deleted" in p.stdout
    assert len(ledger.read_text().splitlines()) == 2  # nothing removed from the file


def test_stats_excludes_legacy_pre_source_fixture_rows(tmp_path):
    # A row written before the `source` field existed (S1-era), matching one of the exact
    # deterministic fixture shapes the S3-check found. Must still be excluded by signature alone.
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"cmd": "sum", "chars_in": 10, "chars_out": 91, "backend_ok": false}\n'
        '{"cmd": "sum", "chars_in": 9999, "chars_out": 91, "backend_ok": false}\n'
    )
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("stats", env=env)
    assert "1 calls logged" in p.stdout  # only the non-matching row counted
    assert "1 test rows excluded, not deleted" in p.stdout

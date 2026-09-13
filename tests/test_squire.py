"""Safety controls for Squire. None of these need a running model."""
import os
import subprocess
import sys
from pathlib import Path

SQUIRE = Path(__file__).resolve().parents[1] / "squire.py"
DOWN = {**os.environ, "SQUIRE_OLLAMA": "http://127.0.0.1:1"}  # nothing listens on port 1


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
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    p = subprocess.run([sys.executable, str(SQUIRE), "diff"], cwd=tmp_path, text=True,
                       capture_output=True, env=DOWN, timeout=60)
    assert "no diff" in p.stdout


def test_diff_shows_real_stat_even_when_model_is_down(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
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
    env = {**os.environ, "SQUIRE_OLLAMA": "http://203.0.113.9:11434"}  # TEST-NET, must never be contacted
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

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

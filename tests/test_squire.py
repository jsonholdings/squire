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
    for word in ("run", "sum", "ask", "draft"):
        assert word in p.stdout

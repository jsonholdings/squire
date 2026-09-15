"""Short `squire run` passthroughs are logged (so usage is counted) but excluded from every savings total."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import squire_report  # noqa: E402

MODEL_ROW = {"ts": 1800000000.0, "cmd": "run", "chars_in": 5000, "chars_out": 100, "backend_ok": True}
PASSTHROUGH_ROW = {"ts": 1800000100.0, "cmd": "run", "chars_in": 300, "chars_out": 300, "backend_ok": True,
                   "passthrough": True}


def run_squire(args, ledger_path):
    env = dict(os.environ, SQUIRE_LEDGER=ledger_path)
    return subprocess.run([sys.executable, os.path.join(ROOT, "squire.py")] + args, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def read_rows(ledger_path):
    with open(ledger_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_public_stats_counts_passthrough_but_excludes_it_from_savings():
    stats = squire_report.public_stats([MODEL_ROW, PASSTHROUGH_ROW, PASSTHROUGH_ROW])
    assert stats["total_calls"] == 1
    assert stats["passthrough_calls"] == 2
    assert stats["chars_in"] == 5000
    assert stats["chars_saved"] == 4900


def test_public_markdown_shows_both_counts():
    markdown = squire_report.render_public_markdown(squire_report.public_stats([MODEL_ROW, PASSTHROUGH_ROW]))
    assert "1 model-backed call logged" in markdown
    assert "plus 1 short `run` passthrough run" in markdown


def test_ledger_estimate_excludes_passthrough():
    estimate = squire_report.estimate_ledger_savings([MODEL_ROW, PASSTHROUGH_ROW])
    assert estimate["calls"] == 1
    assert estimate["passthrough_calls"] == 1
    assert estimate["chars_saved"] == 4900


def test_short_run_writes_a_passthrough_row():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger.jsonl")
        proc = run_squire(["run", "--", "echo", "hi"], ledger_path)
        assert proc.returncode == 0
        rows = read_rows(ledger_path)
        assert len(rows) == 1
        assert rows[0]["passthrough"] is True
        assert rows[0]["chars_in"] == rows[0]["chars_out"]
        assert rows[0]["exit_code"] == 0


def test_short_run_failure_keeps_its_real_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger.jsonl")
        proc = run_squire(["run", "--", "sh", "-c", "exit 3"], ledger_path)
        assert proc.returncode == 3
        rows = read_rows(ledger_path)
        assert rows[0]["passthrough"] is True
        assert rows[0]["exit_code"] == 3


def test_stats_json_reports_passthrough_separately():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger.jsonl")
        with open(ledger_path, "w") as f:
            for row in (MODEL_ROW, PASSTHROUGH_ROW):
                f.write(json.dumps(row) + "\n")
        out = json.loads(run_squire(["stats", "--json"], ledger_path).stdout)
        assert out["calls"] == 1
        assert out["passthrough_calls"] == 1
        assert out["chars_saved"] == 4900

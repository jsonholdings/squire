"""Tests for scripts/squire_report.py's honest public-stats block (--public/--check/--write)."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "scripts", "squire_report.py")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import squire_report  # noqa: E402


def make_ledger(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


SAMPLE_ROWS = [
    {"ts": 1800000000.0, "cmd": "sum", "chars_in": 1000, "chars_out": 100, "backend_ok": True},
    {"ts": 1800000100.0, "cmd": "sum", "chars_in": 500, "chars_out": 500, "backend_ok": False},
    {"ts": 1800000200.0, "cmd": "grep", "chars_in": 2000, "chars_out": 50, "backend_ok": True},
]

FIXTURE_ROW = {"ts": 1800000300.0, "cmd": "run", "chars_in": 1705, "chars_out": 43, "backend_ok": False,
               "source": "test"}


def test_public_stats_excludes_fixture_rows():
    with tempfile.TemporaryDirectory() as home:
        ledger_path = os.path.join(home, "ledger.jsonl")
        make_ledger(ledger_path, SAMPLE_ROWS + [FIXTURE_ROW])
        rows = squire_report.load_ledger(ledger_path)
        assert len(rows) == 3  # fixture row excluded
        stats = squire_report.public_stats(rows)
        assert stats["total_calls"] == 3


def test_public_stats_per_command_ok_and_unknown_rate():
    stats = squire_report.public_stats(SAMPLE_ROWS)
    by_cmd = {c["cmd"]: c for c in stats["per_cmd"]}
    assert by_cmd["sum"]["calls"] == 2
    assert by_cmd["sum"]["ok_rate_pct"] == 50.0
    assert by_cmd["sum"]["unknown_rate_pct"] == 50.0
    assert by_cmd["grep"]["ok_rate_pct"] == 100.0
    assert stats["overall_ok_rate_pct"] == round(100 * 2 / 3, 1)


def test_public_stats_chars_are_real_counts_not_estimated():
    stats = squire_report.public_stats(SAMPLE_ROWS)
    assert stats["chars_in"] == 3500
    total_out = 100 + 500 + 50
    assert stats["chars_out"] == total_out
    assert stats["chars_saved"] == 3500 - total_out
    assert stats["tokens_saved_ESTIMATE"] == round(stats["chars_saved"] / 4)


def test_public_stats_no_infrastructure_detail_in_output():
    stats = squire_report.public_stats(SAMPLE_ROWS)
    block = squire_report.render_public_markdown(stats)
    assert "/home/" not in block
    assert os.path.expanduser("~") not in block


def test_check_and_write_roundtrip():
    with tempfile.TemporaryDirectory() as home:
        doc_path = os.path.join(home, "README.md")
        with open(doc_path, "w") as f:
            f.write("# Doc\n\n" + squire_report.PUBLIC_STATS_BEGIN + "\nstale\n" +
                     squire_report.PUBLIC_STATS_END + "\n\nmore text\n")
        stats = squire_report.public_stats(SAMPLE_ROWS)
        block = squire_report.render_public_markdown(stats)

        ok, _ = squire_report.check_block_in(doc_path, block)
        assert ok is False  # stale placeholder differs from generated block

        changed = squire_report.write_block_into(doc_path, block)
        assert changed is True

        ok, _ = squire_report.check_block_in(doc_path, block)
        assert ok is True  # now matches

        changed_again = squire_report.write_block_into(doc_path, block)
        assert changed_again is False  # idempotent, no-op on second write


def test_check_fails_without_markers():
    with tempfile.TemporaryDirectory() as home:
        doc_path = os.path.join(home, "README.md")
        with open(doc_path, "w") as f:
            f.write("# No markers here\n")
        ok, msg = squire_report.check_block_in(doc_path, "anything")
        assert ok is False
        assert "no SQUIRE-PUBLIC-STATS markers" in msg


def test_filter_ledger_since_drops_earlier_rows():
    import datetime
    cutoff_iso = "2026-09-15T01:00:00-04:00"
    cutoff_ts = datetime.datetime.fromisoformat(cutoff_iso).timestamp()
    rows = [
        {"ts": cutoff_ts - 3600, "cmd": "sum", "chars_in": 10, "chars_out": 1, "backend_ok": True},
        {"ts": cutoff_ts + 1, "cmd": "sum", "chars_in": 20, "chars_out": 2, "backend_ok": True},
    ]
    kept = squire_report.filter_ledger_since(rows, cutoff_iso)
    assert len(kept) == 1
    assert kept[0]["chars_in"] == 20


def test_cli_public_defaults_to_owner_cutoff_and_excludes_earlier_rows():
    with tempfile.TemporaryDirectory() as home:
        import datetime
        cutoff_ts = datetime.datetime.fromisoformat(squire_report.PUBLIC_STATS_DEFAULT_SINCE).timestamp()
        ledger_path = os.path.join(home, "ledger.jsonl")
        make_ledger(ledger_path, [
            {"ts": cutoff_ts - 100, "cmd": "sum", "chars_in": 999, "chars_out": 1, "backend_ok": True},
            {"ts": cutoff_ts + 100, "cmd": "sum", "chars_in": 40, "chars_out": 4, "backend_ok": True},
        ])
        p = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--public"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p.returncode == 0, p.stdout
        assert "999" not in p.stdout
        assert "1 real call logged" in p.stdout


def test_cli_check_exits_nonzero_when_stale():
    with tempfile.TemporaryDirectory() as home:
        ledger_path = os.path.join(home, "ledger.jsonl")
        make_ledger(ledger_path, SAMPLE_ROWS)
        doc_path = os.path.join(home, "README.md")
        with open(doc_path, "w") as f:
            f.write(squire_report.PUBLIC_STATS_BEGIN + "\nstale\n" + squire_report.PUBLIC_STATS_END + "\n")
        p = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--check", doc_path],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p.returncode == 1, p.stdout

        p2 = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--write", doc_path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p2.returncode == 0, p2.stdout

        p3 = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--check", doc_path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p3.returncode == 0, p3.stdout


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

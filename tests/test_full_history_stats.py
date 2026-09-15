"""Tests for squire_report.py's full-history headline generator (README numbered headline,
SAVINGS.md's two MEASURED tables + ESTIMATED row, and the site card's three <dl> stats). These
are computed from ALL ledger/session history, never a since-window, unlike public_stats()."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "scripts", "squire_report.py")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import squire_report  # noqa: E402


def make_session(path, usages):
    with open(path, "w") as f:
        for i, u in enumerate(usages):
            f.write(json.dumps({
                "timestamp": f"2026-09-12T00:00:{i:02d}Z",
                "message": {"usage": u},
            }) + "\n")


def make_ledger(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def build_fixture(home):
    proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
    os.makedirs(proj_dir)
    make_session(os.path.join(proj_dir, "s.jsonl"), [
        {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 5_200_000, "output_tokens": 10},
    ])
    ledger_path = os.path.join(home, "ledger.jsonl")
    make_ledger(ledger_path, [
        {"ts": 1.0, "cmd": "grep", "chars_in": 4000, "chars_out": 100, "backend_ok": True},
        {"ts": 2.0, "cmd": "run", "chars_in": 50, "chars_out": 20, "backend_ok": True, "passthrough": True},
    ])
    return ledger_path


def test_shape_and_labels():
    with tempfile.TemporaryDirectory() as home:
        ledger_path = build_fixture(home)
        # CLAUDE_PROJECTS_DIR is resolved once at import time from the real $HOME; patch the
        # module attribute directly rather than relying on HOME (which iter_session_files
        # would not re-read after import).
        original = squire_report.CLAUDE_PROJECTS_DIR
        squire_report.CLAUDE_PROJECTS_DIR = os.path.join(home, ".claude", "projects")
        try:
            fs = squire_report.full_history_stats(ledger_path)
        finally:
            squire_report.CLAUDE_PROJECTS_DIR = original
        for key in ("session_count", "ratio_cache_read_to_input", "calls", "chars_saved",
                    "pct_kept_out_of_context", "tokens_kept_out_ESTIMATE"):
            assert key in fs
        headline = squire_report.render_headline_markdown(fs)
        assert "MEASURED" in headline and "ESTIMATED" in headline
        assert headline.count("\n1. ") or "1. **" in headline
        savings = squire_report.render_savings_markdown(fs)
        assert savings.count("| Metric | Value |") >= 2
        site = squire_report.render_site_dl_html(fs)
        assert site.count("<div><dt>") == 3


def test_passthrough_rows_excluded_from_pct_kept():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
        os.makedirs(proj_dir)
        make_session(os.path.join(proj_dir, "s.jsonl"), [
            {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
        ])
        ledger_path = os.path.join(home, "ledger.jsonl")
        # One real model call saving 90% of chars, plus many passthrough rows that save nothing
        # (chars_in == chars_out) -- a passthrough-heavy ledger must not dilute the measured %.
        rows = [{"ts": 1.0, "cmd": "grep", "chars_in": 1000, "chars_out": 100, "backend_ok": True}]
        rows += [{"ts": float(i), "cmd": "run", "chars_in": 30, "chars_out": 30, "backend_ok": True,
                  "passthrough": True} for i in range(2, 50)]
        make_ledger(ledger_path, rows)
        original = squire_report.CLAUDE_PROJECTS_DIR
        squire_report.CLAUDE_PROJECTS_DIR = os.path.join(home, ".claude", "projects")
        try:
            fs = squire_report.full_history_stats(ledger_path)
        finally:
            squire_report.CLAUDE_PROJECTS_DIR = original
        assert fs["calls"] == 1  # passthrough rows never counted as model-backed calls
        assert fs["passthrough_calls"] == 48
        assert fs["pct_kept_out_of_context"] == 90.0  # unaffected by the 48 zero-savings passthrough rows


def test_check_fails_when_stale_then_write_fixes_it():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
        os.makedirs(proj_dir)
        make_session(os.path.join(proj_dir, "s.jsonl"), [
            {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 500, "output_tokens": 1},
        ])
        ledger_path = os.path.join(home, "ledger.jsonl")
        make_ledger(ledger_path, [{"ts": 1.0, "cmd": "grep", "chars_in": 400, "chars_out": 40, "backend_ok": True}])
        doc_path = os.path.join(home, "README.md")
        with open(doc_path, "w") as f:
            f.write("# Doc\n\n" + squire_report.PUBLIC_STATS_BEGIN + "\nx\n" + squire_report.PUBLIC_STATS_END +
                     "\n\n" + squire_report.HEADLINE_BEGIN + "\nstale headline\n" + squire_report.HEADLINE_END + "\n")
        env = dict(os.environ)
        env["HOME"] = home
        env.pop("SQUIRE_LEDGER", None)
        p = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--check", doc_path],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        assert p.returncode == 1, p.stdout
        p2 = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--write", doc_path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        assert p2.returncode == 0, p2.stdout
        p3 = subprocess.run([sys.executable, REPORT, "--ledger", ledger_path, "--check", doc_path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        assert p3.returncode == 0, p3.stdout


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

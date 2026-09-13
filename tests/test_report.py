"""Tests for scripts/squire_report.py using synthetic JSONL fixtures (no real transcripts)."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "scripts", "squire_report.py")


def make_session(path, usages):
    with open(path, "w") as f:
        for i, u in enumerate(usages):
            f.write(json.dumps({
                "timestamp": f"2026-09-12T00:00:{i:02d}Z",
                "message": {"usage": u},
            }) + "\n")


def run_report(env_home, extra_args=None):
    env = dict(os.environ)
    env["HOME"] = env_home
    # These tests exercise squire_report.py's own HOME-relative default ledger path (S4:
    # conftest.py sets SQUIRE_LEDGER for the rest of the suite's isolation, which would otherwise
    # override this test's synthetic ~/.squire/ledger.jsonl fixture and read the wrong file).
    env.pop("SQUIRE_LEDGER", None)
    cmd = [sys.executable, REPORT, "--json"] + (extra_args or [])
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    assert p.returncode == 0, p.stdout
    return json.loads(p.stdout)


def test_verified_totals_from_synthetic_session():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
        os.makedirs(proj_dir)
        make_session(os.path.join(proj_dir, "session1.jsonl"), [
            {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 50},
            {"input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 2000, "output_tokens": 60},
        ])
        result = run_report(home)
        assert result["VERIFIED_session_count"] == 1
        totals = result["VERIFIED_totals"]
        assert totals["cache_read_input_tokens"] == 3000
        assert totals["input_tokens"] == 15
        assert totals["output_tokens"] == 110
        assert totals["turns"] == 2


def test_sessions_without_usage_are_skipped():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
        os.makedirs(proj_dir)
        make_session(os.path.join(proj_dir, "empty.jsonl"), [])
        with open(os.path.join(proj_dir, "empty.jsonl"), "w") as f:
            f.write(json.dumps({"timestamp": "2026-09-12T00:00:00Z", "message": {}}) + "\n")
        result = run_report(home)
        assert result["VERIFIED_session_count"] == 0


def test_anonymize_replaces_project_names():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "very-identifying-name")
        os.makedirs(proj_dir)
        make_session(os.path.join(proj_dir, "session1.jsonl"), [
            {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
        ])
        result = run_report(home, ["--anonymize"])
        names = [s["project"] for s in result["VERIFIED_sessions"]]
        assert names == ["PROJECT-1"]
        assert "very-identifying-name" not in json.dumps(result)


def test_ledger_estimate_is_labelled_and_separate_from_verified():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
        os.makedirs(proj_dir)
        make_session(os.path.join(proj_dir, "session1.jsonl"), [
            {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
        ])
        squire_dir = os.path.join(home, ".squire")
        os.makedirs(squire_dir)
        with open(os.path.join(squire_dir, "ledger.jsonl"), "w") as f:
            # no "session" field -- old ledger format, must not be silently correlated
            f.write(json.dumps({"ts": 1.0, "cmd": "sum", "chars_in": 400, "chars_out": 100, "backend_ok": True}) + "\n")
        result = run_report(home)
        est = result["ESTIMATE_squire_ledger"]
        assert est["calls"] == 1
        assert est["chars_saved"] == 300
        assert est["tokens_saved_one_time_ESTIMATE"] == 75  # 300 / 4
        assert est["calls_with_unknown_session"] == 1
        assert est["calls_correlated_to_a_session"] == 0
        assert est["cache_read_avoided_ESTIMATE"] == 0


def test_ledger_session_correlation_multiplies_by_turns_remaining():
    with tempfile.TemporaryDirectory() as home:
        proj_dir = os.path.join(home, ".claude", "projects", "fake-project")
        os.makedirs(proj_dir)
        session_id = "abc-123-session"
        session_path = os.path.join(proj_dir, f"{session_id}.jsonl")
        # 3 turns: one before the call, two after -- only the two after should count as
        # "turns remaining" for the cache-read-avoided estimate.
        with open(session_path, "w") as f:
            for i, ts in enumerate(["2026-09-12T00:00:00Z", "2026-09-12T00:05:00Z", "2026-09-12T00:10:00Z"]):
                f.write(json.dumps({
                    "timestamp": ts,
                    "message": {"usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                                           "cache_read_input_tokens": 0, "output_tokens": 1}},
                }) + "\n")
        squire_dir = os.path.join(home, ".squire")
        os.makedirs(squire_dir)
        # call timestamp between turn 1 (00:00) and turn 2 (00:05) -> 2 turns remaining (00:05, 00:10)
        call_ts = 1789131900.0  # placeholder; overwritten below with the real epoch for 00:02:30
        import datetime
        call_ts = datetime.datetime.fromisoformat("2026-09-12T00:02:30+00:00").timestamp()
        with open(os.path.join(squire_dir, "ledger.jsonl"), "w") as f:
            f.write(json.dumps({"ts": call_ts, "cmd": "sum", "chars_in": 400, "chars_out": 0,
                                 "backend_ok": True, "session": session_id}) + "\n")
        result = run_report(home)
        est = result["ESTIMATE_squire_ledger"]
        assert est["calls_correlated_to_a_session"] == 1
        assert est["calls_with_unknown_session"] == 0
        # chars_saved=400 -> 100 tokens/turn * 2 remaining turns = 200
        assert est["cache_read_avoided_ESTIMATE"] == 200


def test_project_filter():
    with tempfile.TemporaryDirectory() as home:
        for name in ("proj-a", "proj-b"):
            proj_dir = os.path.join(home, ".claude", "projects", name)
            os.makedirs(proj_dir)
            make_session(os.path.join(proj_dir, "s.jsonl"), [
                {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
            ])
        result = run_report(home, ["--project", "proj-a"])
        assert result["VERIFIED_session_count"] == 1
        assert result["VERIFIED_sessions"][0]["project"] == "proj-a"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

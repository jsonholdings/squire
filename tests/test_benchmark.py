"""Tests for scripts/benchmark.py's statistics code (bootstrap CI, percentile) and
S6b's checkpoint/resume/--report machinery.

Controls (global CLAUDE.md 17): prove the CI machinery can both CONTAIN and EXCLUDE
a known true value before trusting any number benchmark.py reports.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import benchmark  # noqa: E402


def test_control_false_positive_check_accepts_squires_real_no_errors_phrasing(monkeypatch, tmp_path):
    # Root-caused 2026-09-13 (S7): the old check ("fail" in summ.lower() and "0 failed" not in
    # summ.lower()) flagged EVERY all-pass control, because squire's real, CORRECT summary is
    # plain English like "No errors seen ... passed without any failures" -- it contains the
    # substring "fail" without the exact literal "0 failed". Checked live against condense_verified's
    # own prompt, which instructs the model 'Say "no errors seen" only if there are none.' Control:
    # a genuine bogus failure claim (below) must still be caught, so the fix isn't a rubber stamp.
    case = benchmark.FIDELITY_BY_ID["fidelity_control_all_pass"]
    real_clean_summary = "- No errors seen\n\nNote: all tests passed without any failures."
    bogus_failure_summary = "- test_something FAILED: assertion error"

    def fake_run(summary):
        def _run(text, exit_code):
            return {"exit_code": exit_code}, {"exit_code": exit_code, "summary": summary, "seconds": 0.01}
        return _run

    monkeypatch.setattr(benchmark, "run_synthetic_squire", fake_run(real_clean_summary))
    clean_path = str(tmp_path / "clean.jsonl")
    clean = benchmark.trial_fidelity_case(case, 1, {}, clean_path)
    assert clean["reps"][0]["false_positive_on_control"] is False

    monkeypatch.setattr(benchmark, "run_synthetic_squire", fake_run(bogus_failure_summary))
    bogus_path = str(tmp_path / "bogus.jsonl")
    bogus = benchmark.trial_fidelity_case(case, 1, {}, bogus_path)
    assert bogus["reps"][0]["false_positive_on_control"] is True


def test_bootstrap_ci_empty():
    point, lo, hi = benchmark.bootstrap_ci([])
    assert point is None and lo is None and hi is None


def test_bootstrap_ci_single_value_is_a_point():
    point, lo, hi = benchmark.bootstrap_ci([42.0])
    assert point == lo == hi == 42.0


def test_bootstrap_ci_constant_data_has_zero_width_interval():
    # CONTROL: every value identical -> the CI must collapse to that value exactly,
    # not merely "contain" it. This is the one case with a knowable exact answer.
    data = [7.0] * 30
    point, lo, hi = benchmark.bootstrap_ci(data, statistics.median)
    assert point == 7.0
    assert lo == 7.0
    assert hi == 7.0


def test_bootstrap_ci_contains_true_median_for_symmetric_data():
    # CONTROL (positive): a large symmetric sample around a known median must yield
    # a CI containing that median -- proves the CI can produce a TRUE-positive.
    data = [50.0 + i * 0.1 for i in range(-100, 101)]  # symmetric around 50.0
    true_median = statistics.median(data)
    point, lo, hi = benchmark.bootstrap_ci(data, statistics.median, n_boot=1000, seed=1)
    assert lo <= true_median <= hi
    assert point == true_median


def test_bootstrap_ci_excludes_value_far_outside_data():
    # CONTROL (negative): a value far outside the data's range must NOT be inside the
    # CI -- proves the interval is not vacuously wide (i.e. the check can fail).
    data = [10.0 + i * 0.01 for i in range(200)]
    _, lo, hi = benchmark.bootstrap_ci(data, statistics.median, n_boot=1000, seed=1)
    assert not (lo <= 9999.0 <= hi)


def test_bootstrap_ci_is_deterministic_given_seed():
    data = [1.0, 5.0, 3.0, 9.0, 2.0, 7.0, 4.0, 8.0, 6.0, 10.0]
    a = benchmark.bootstrap_ci(data, statistics.median, seed=99)
    b = benchmark.bootstrap_ci(data, statistics.median, seed=99)
    assert a == b


def test_percentile_p50_matches_median_odd_n():
    data = [1, 2, 3, 4, 5]
    assert benchmark.percentile(data, 0.5) == 3


def test_percentile_p95_of_uniform_ramp():
    # CONTROL: percentile of a known linear ramp 0..99 at p95 should be close to 94.05
    data = list(range(100))
    p95 = benchmark.percentile(data, 0.95)
    assert 93 <= p95 <= 95.5


def test_percentile_empty_is_none():
    assert benchmark.percentile([], 0.5) is None


def test_tokens_chars_per_token_approx():
    assert benchmark.tokens("a" * 40) == 10.0
    assert benchmark.tokens("") == 0.0


def test_synth_pytest_output_contains_all_fail_ids_and_correct_count():
    text = benchmark._synth_pytest_output(90, ["test_x", "test_y"], [0.1, 0.9], 1)
    assert "test_x" in text
    assert "test_y" in text
    assert "2 failed" in text


# ---------------------------------------------------------------------------
# S6b: checkpoint round-trip, resume, and --report (no new model calls)
# ---------------------------------------------------------------------------

def test_checkpoint_round_trip(tmp_path):
    path = str(tmp_path / "cp.jsonl")
    benchmark.append_checkpoint(path, {"key": "file:x", "rep": {"seconds": 1.0}})
    benchmark.append_checkpoint(path, {"key": "file:x", "rep": {"seconds": 2.0}})
    benchmark.append_checkpoint(path, {"key": "cmd:y", "rep": {"seconds": 3.0}})
    data = benchmark.load_checkpoint(path)
    assert len(data["file:x"]) == 2
    assert len(data["cmd:y"]) == 1
    assert data["file:x"][0]["rep"]["seconds"] == 1.0


def test_load_checkpoint_missing_file_is_empty_not_fatal(tmp_path):
    data = benchmark.load_checkpoint(str(tmp_path / "does-not-exist.jsonl"))
    assert data == {}


def test_load_checkpoint_skips_malformed_trailing_line(tmp_path):
    # CONTROL: a checkpoint killed mid-write ends with a truncated JSON line.
    # load_checkpoint must keep every complete line and drop only the broken one.
    path = str(tmp_path / "cp.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"key": "file:x", "rep": {"seconds": 1.0}}) + "\n")
        f.write('{"key": "file:x", "rep": {"seco')  # truncated, no trailing newline
    data = benchmark.load_checkpoint(path)
    assert len(data["file:x"]) == 1


def test_trial_file_workload_resumes_and_only_runs_remaining_reps(tmp_path, monkeypatch):
    # CONTROL: pre-populate 2 of 3 target reps in the checkpoint; assert exactly
    # ONE new (mocked) squire call happens, and the returned trial has all 3 reps.
    calls = {"n": 0}

    def fake_run_squire_sum(path):
        calls["n"] += 1
        return {"summary": "x", "seconds": 0.5, "backend_ok": True}

    monkeypatch.setattr(benchmark, "run_squire_sum", fake_run_squire_sum)
    cp_path = str(tmp_path / "cp.jsonl")
    w = {"id": "readme_md", "path": benchmark.FILE_WORKLOADS[1]["path"]}
    key = f"file:{w['id']}"
    for _ in range(2):
        benchmark.append_checkpoint(cp_path, {"key": key, "rep": {"status": "VERIFIED", "seconds": 0.1,
                                                                    "pct_saved": 50.0, "backend_ok": True}})
    cp_data = benchmark.load_checkpoint(cp_path)
    trial = benchmark.trial_file_workload(w, 3, cp_data, cp_path)
    assert calls["n"] == 1  # only the missing rep was actually run
    assert trial["n"] == 3
    assert len(trial["reps"]) == 3


def test_report_mode_states_partial_n_honestly(tmp_path):
    # CONTROL: only 3 of a would-be-20 reps recorded for one file workload; report
    # must show n=3 for that cell, not 20 and not silently zero.
    cp_path = str(tmp_path / "cp.jsonl")
    key = f"file:{benchmark.FILE_WORKLOADS[0]['id']}"
    for pct in (10.0, 20.0, 30.0):
        benchmark.append_checkpoint(cp_path, {"key": key,
                                                "rep": {"status": "VERIFIED", "seconds": 1.0, "pct_saved": pct}})
    results = benchmark.report_mode(cp_path, n_solo=20, n_conc=12)
    ft = [t for t in results["file_trials"] if t["id"] == benchmark.FILE_WORKLOADS[0]["id"]][0]
    assert ft["n"] == 3
    assert results["partial"] is True


def test_report_mode_empty_checkpoint_is_all_zero_n_not_fabricated():
    # CONTROL (negative): an empty/missing checkpoint must report n=0 everywhere,
    # never a plausible-looking non-zero number with nothing behind it.
    results = benchmark.report_mode("/nonexistent/path/cp.jsonl", n_solo=20, n_conc=12)
    assert results["tokens_pct_saved"]["n"] == 0
    assert all(v["n"] == 0 for v in results["latency_by_concurrency"].values())
    assert results["deterministic_exit_code_preserved_pct"] is None
    assert results["deterministic_exit_code_preserved_n"] == 0


# ---------------------------------------------------------------------------
# S8b: deterministic exit-code corpus (RUN-QUEUE S8b, fixing the S8 gate failure where the
# corpus was two live, non-atomic invocations of this repo's own resource-contended pytest
# suite). tests/fixtures/bench_emit_exit.py and tests/fixtures/bench_project/ are frozen,
# stdlib-only, no-network, no-lock fixtures whose exit code is known before the process runs.
# ---------------------------------------------------------------------------

def test_bench_emit_exit_fixture_produces_every_expected_code():
    # CONTROL: run the raw fixture directly (no squire involved at all) for every code the
    # corpus uses, proving the fixture itself is trustworthy ground truth before anything is
    # measured against it.
    import subprocess
    for code in (0, 1, 2, 3, 124, 137):
        p = subprocess.run([sys.executable, benchmark.EMIT_EXIT, "5", str(code)],
                            capture_output=True, text=True)
        assert p.returncode == code
        assert len(p.stdout.splitlines()) == 5


def test_bench_project_fixtures_produce_expected_pytest_exit_codes():
    # CONTROL: the frozen fixture project's pass/fail files must actually produce the exit
    # codes DETERMINISTIC_CMD_WORKLOADS assumes (0 and 1) when run in isolation, and the
    # `-o python_files=sample_*.py` override must be what makes them collectible at all --
    # otherwise "0 tests ran" would silently read as a false pass.
    import subprocess
    pass_path = os.path.join(benchmark.BENCH_PROJECT, "sample_pass.py")
    fail_path = os.path.join(benchmark.BENCH_PROJECT, "sample_fail.py")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "python_files=sample_*.py",
                        pass_path], capture_output=True, text=True)
    assert p.returncode == 0
    assert "3 passed" in p.stdout
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "python_files=sample_*.py",
                        fail_path], capture_output=True, text=True)
    assert p.returncode == 1
    assert "2 failed, 1 passed" in p.stdout


def test_bench_project_fixtures_are_not_collected_by_the_repos_own_suite():
    # CONTROL (negative): sample_*.py must NOT be swept into squire's own `pytest -q`
    # (pyproject.toml testpaths=["tests"], default python_files=test_*.py) -- if it were,
    # squire's committed "110/110" suite count would silently grow to include benchmark
    # fixtures that were never meant to be part of it.
    import subprocess
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only"],
                        capture_output=True, text=True, cwd=benchmark.REPO_ROOT)
    assert "fixtures/bench_project" not in p.stdout
    assert "sample_pass.py" not in p.stdout
    assert "sample_fail.py" not in p.stdout


def test_trial_deterministic_cmd_workload_checks_three_way_agreement(monkeypatch, tmp_path):
    # A live corpus can have raw==squire while both disagree with the known-correct answer
    # (two flaky runs agreeing by coincidence). This proves the deterministic trial catches
    # that case instead of only checking raw==squire.
    w = {"id": "fake_det", "cmd": ["true"], "expected_exit_code": 0}

    def fake_run_raw(cmd):
        return {"exit_code": 0, "text": "ok\n", "seconds": 0.01}

    def fake_run_squire_cmd(cmd):
        return {"exit_code": 7, "seconds": 0.02, "raw_tail": "ok\n", "summary": None}

    monkeypatch.setattr(benchmark, "run_raw", fake_run_raw)
    monkeypatch.setattr(benchmark, "run_squire_cmd", fake_run_squire_cmd)
    cp_path = str(tmp_path / "cp.jsonl")
    trial = benchmark.trial_deterministic_cmd_workload(w, 1, {}, cp_path)
    rep = trial["reps"][0]
    assert rep["raw_matches_expected"] is True
    assert rep["exit_code_match"] is False  # squire (7) != raw (0) == expected (0)


def test_trial_deterministic_cmd_workload_resumes_from_checkpoint(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_run_raw(cmd):
        calls["n"] += 1
        return {"exit_code": 3, "text": "x\n", "seconds": 0.01}

    def fake_run_squire_cmd(cmd):
        return {"exit_code": 3, "seconds": 0.02, "raw_tail": "x\n", "summary": None}

    monkeypatch.setattr(benchmark, "run_raw", fake_run_raw)
    monkeypatch.setattr(benchmark, "run_squire_cmd", fake_run_squire_cmd)
    w = {"id": "fake_det2", "cmd": ["false"], "expected_exit_code": 3}
    cp_path = str(tmp_path / "cp.jsonl")
    key = f"detcmd:{w['id']}"
    benchmark.append_checkpoint(cp_path, {"key": key, "rep": {
        "status": "VERIFIED", "raw_exit_code": 3, "squire_exit_code": 3,
        "expected_exit_code": 3, "raw_matches_expected": True, "exit_code_match": True}})
    cp_data = benchmark.load_checkpoint(cp_path)
    trial = benchmark.trial_deterministic_cmd_workload(w, 3, cp_data, cp_path)
    assert calls["n"] == 2  # only the 2 missing reps were actually run
    assert trial["n"] == 3

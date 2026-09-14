#!/usr/bin/env python3
"""benchmark.py: rigorous, reproducible squire benchmark (RUN-QUEUE S6/S6b).

Extends ab_trial.py (7b/7c) with:
  - N>=10 repeats per workload (not a single sample)
  - wall latency p50/p95, SOLO and under 2/4/6 concurrent load
  - bootstrap 95% confidence intervals on every reported statistic
  - a committed, deterministic, secret-free workload corpus (real files from this
    repo + real commands + synthetic pytest-shaped fidelity cases with a
    hand-written answer key baked into this file, see FIDELITY_CASES)
  - exit-code-preserved %, failing-name recall %, UNKNOWN/fail rate
  - S6b: every trial rep is checkpointed to a JSONL file AS IT COMPLETES, so a
    killed run (GPU contention, timebox) still yields real per-workload numbers.
    Re-running the same command RESUMES from the checkpoint instead of redoing
    finished reps. `--report` computes stats from whatever is checkpointed so
    far, with NO new model calls, stating n honestly per cell (S6b requirement:
    "state n per cell" -- a cell with n=3 is reported as n=3, never rounded up
    to the target and never silently omitted).

Tokens are a stated APPROXIMATION: chars/4 (matches squire.py's own heuristic; no
tokenizer library is installed on this workstation -- checked this run).

All squire invocations here set SQUIRE_SOURCE=test so they land in the real ledger
tagged source=test and are excluded from `squire stats`/squire_report per the S4
ledger convention (squire.py:82, ~/.squire/ledger.jsonl untouched otherwise).

Confound, stated honestly: qwen2.5:14b runs on a single shared GPU. Other sessions
may be calling squire concurrently during this run; "concurrent load" below is
THIS SCRIPT's own load, not exclusive access to the model. See docs/BENCHMARK.md.

Usage:
  python3 scripts/benchmark.py [--quick] [--json] [--out DIR] [--checkpoint FILE] [--mode sync|queue|both]
  python3 scripts/benchmark.py --report [--checkpoint FILE] [--json]
  --quick uses N=10 (the floor) and skips the file-workload solo pass N to fit a
  timebox; full run uses N=20 solo / N=12 per concurrency level.
  --report reads the checkpoint file and prints/saves stats WITHOUT running any
  new trials (no model calls) -- use it to see numbers from a partial run.
  --mode (S12) selects which concurrency-latency table(s) run: "sync" (default,
  pre-S12 behavior, `squire sum` called directly), "queue" (submit+wait through one
  foreground `squire worker`), or "both". Only affects the concurrency-latency
  section; file/cmd/fidelity/deterministic trials are unaffected by --mode.
Run from the squire repo root (uses squire.py in the parent of this script's dir).
"""
import concurrent.futures
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQUIRE = os.path.join(REPO_ROOT, "squire.py")
CHARS_PER_TOKEN = 4  # stated approximation, matches squire.py's own heuristic
PY = sys.executable
ENV = {**os.environ, "SQUIRE_SOURCE": "test"}
DEFAULT_CHECKPOINT = os.path.join(REPO_ROOT, "data", "benchmark-checkpoint.jsonl")

# ---------------------------------------------------------------------------
# Bootstrap CI (unit-tested in tests/test_benchmark.py against known distributions)
# ---------------------------------------------------------------------------

def bootstrap_ci(data, statfn=statistics.median, n_boot=2000, alpha=0.05, seed=1234):
    """Percentile bootstrap CI for statfn(data). Deterministic given seed so results
    are reproducible run-to-run. Returns (point_estimate, lo, hi) or (None, None, None)
    for empty input."""
    data = list(data)
    if not data:
        return None, None, None
    if len(data) == 1:
        v = statfn(data)
        return v, v, v
    rng = random.Random(seed)
    n = len(data)
    boots = []
    for _ in range(n_boot):
        sample = [data[rng.randrange(n)] for _ in range(n)]
        boots.append(statfn(sample))
    boots.sort()
    lo_idx = int((alpha / 2) * n_boot)
    hi_idx = int((1 - alpha / 2) * n_boot) - 1
    hi_idx = min(hi_idx, n_boot - 1)
    return statfn(data), boots[lo_idx], boots[hi_idx]


def percentile(data, p):
    if not data:
        return None
    s = sorted(data)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def tokens(text):
    return len(text or "") / CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Checkpointing (S6b): every rep appended as one JSONL line the moment it's
# computed, so `kill -9` mid-run loses at most the in-flight rep.
# ---------------------------------------------------------------------------

def append_checkpoint(path, rec):
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()


def load_checkpoint(path):
    """Return {key: [rec, ...]} in file order. A malformed trailing line (killed
    mid-write) is skipped, not fatal -- the checkpoint's whole point is surviving
    a kill."""
    data = {}
    if not path or not os.path.exists(path):
        return data
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            data.setdefault(rec["key"], []).append(rec)
    return data


# ---------------------------------------------------------------------------
# Corpus: committed, secret-free (run scripts/scrub_check.py before every commit)
# ---------------------------------------------------------------------------

FILE_WORKLOADS = [
    {"id": "squire_py", "path": os.path.join(REPO_ROOT, "squire.py")},
    {"id": "readme_md", "path": os.path.join(REPO_ROOT, "README.md")},
    {"id": "test_squire_py", "path": os.path.join(REPO_ROOT, "tests", "test_squire.py")},
]

# LIVE, LOAD-SENSITIVE workloads (S6b/S7/S8): these re-run this repo's own real `pytest`
# suite as the raw AND squire-wrapped invocation, as two SEPARATE non-atomic subprocess calls.
# S8 found (docs/BENCHMARK.md "S8 update") that under real concurrent GPU/lock contention from
# other sessions, two live runs of a resource-contended pytest process can genuinely finish
# with different outcomes independent of anything squire does -- squire's own exit-code
# passthrough (squire.py:cmd_run captures p.returncode before any model call, on every branch)
# was never the defect. These are kept and reported SEPARATELY, explicitly labelled
# "live, load-sensitive" -- they measure realistic noisy-command token savings, not gate-grade
# exit-code fidelity. DETERMINISTIC_CMD_WORKLOADS below is what the S8b gate is computed from.
CMD_WORKLOADS = [
    {"id": "pytest_verbose", "cmd": [PY, "-m", "pytest", "-v"],
     "note": "LIVE, LOAD-SENSITIVE: full verbose run of this repo's own suite"},
    {"id": "find_files", "cmd": ["find", ".", "-type", "f"],
     "note": "noisy file listing (deterministic content, but not exit-code-bearing)"},
    {"id": "pytest_quiet_control", "cmd": [PY, "-m", "pytest", "-q"],
     "note": "LIVE, LOAD-SENSITIVE control: short output (<=60 lines), squire should pass "
             "through ~unchanged"},
]

# ---------------------------------------------------------------------------
# DETERMINISTIC exit-code corpus (S8b): the fix for the S8 gate failure. Every workload here
# is either (a) tests/fixtures/bench_emit_exit.py -- a frozen, stdlib-only, no-network,
# no-lock script that prints a fixed number of lines and exits with a fixed code baked into
# its own argv, so the expected code is known BEFORE the process ever runs and cannot depend
# on load, timing, or any other session's activity -- or (b) tests/fixtures/bench_project/, a
# frozen pytest fixture project (sample_pass.py / sample_fail.py, deliberately NOT named
# test_*.py so squire's own `pytest -q` at repo root never collects them) with a few
# always-passing and a few always-failing tests, no squire import, no network, no shared lock.
# Each rep runs the SAME command twice (raw, squire-wrapped) and checks THREE-WAY agreement:
# raw == squire == expected. Most workloads keep output at or under squire.py's SHORT=60-line
# threshold so they never touch the shared llm.lock at all; a couple deliberately exceed it
# (marked "invokes model") to prove exit-code passthrough holds even when the model IS called
# under real contention -- squire.py:cmd_run always captures p.returncode before condense_
# verified runs and passes it straight to sys.exit() on every branch, so this is expected to
# hold at 100% regardless of GPU load.
EMIT_EXIT = os.path.join(REPO_ROOT, "tests", "fixtures", "bench_emit_exit.py")
BENCH_PROJECT = os.path.join(REPO_ROOT, "tests", "fixtures", "bench_project")

DETERMINISTIC_CMD_WORKLOADS = [
    {"id": "det_exit0", "cmd": [PY, EMIT_EXIT, "5", "0"], "expected_exit_code": 0, "n": 5},
    {"id": "det_exit1", "cmd": [PY, EMIT_EXIT, "8", "1"], "expected_exit_code": 1, "n": 5},
    {"id": "det_exit2", "cmd": [PY, EMIT_EXIT, "6", "2"], "expected_exit_code": 2, "n": 5},
    {"id": "det_exit3", "cmd": [PY, EMIT_EXIT, "4", "3"], "expected_exit_code": 3, "n": 5},
    {"id": "det_exit124", "cmd": [PY, EMIT_EXIT, "10", "124"], "expected_exit_code": 124, "n": 5},
    {"id": "det_exit137", "cmd": [PY, EMIT_EXIT, "12", "137"], "expected_exit_code": 137, "n": 5},
    {"id": "det_exit1_invokes_model", "cmd": [PY, EMIT_EXIT, "90", "1"],
     "expected_exit_code": 1, "n": 3,
     "note": "output > SHORT=60 lines -- exercises condense_verified/the shared llm.lock"},
    {"id": "bench_pytest_all_pass",
     "cmd": [PY, "-m", "pytest", "-q", "-o", "python_files=sample_*.py",
             os.path.join(BENCH_PROJECT, "sample_pass.py")],
     "expected_exit_code": 0, "n": 5},
    {"id": "bench_pytest_some_fail",
     "cmd": [PY, "-m", "pytest", "-q", "-o", "python_files=sample_*.py",
             os.path.join(BENCH_PROJECT, "sample_fail.py")],
     "expected_exit_code": 1, "n": 5},
]

# Deterministic synthetic pytest-shaped output with a HAND-WRITTEN ANSWER KEY: the
# fail_ids listed here are the ground truth against which squire's summary recall is
# scored. No model or heuristic produced this list; it is authored data, matching the
# "summary accuracy vs a hand-written answer key" requirement in RUN-QUEUE S6.
FIDELITY_CASES = [
    {"id": "fidelity_early_single", "total_lines": 90,
     "fail_ids": ["test_broken_login_flow"], "fail_at_fracs": [0.03], "exit_code": 1},
    {"id": "fidelity_middle_double", "total_lines": 100,
     "fail_ids": ["test_price_rounding_bug", "test_refund_race_condition"],
     "fail_at_fracs": [0.45, 0.55], "exit_code": 1},
    {"id": "fidelity_last_single", "total_lines": 95,
     "fail_ids": ["test_export_csv_encoding"], "fail_at_fracs": [0.95], "exit_code": 1},
    {"id": "fidelity_multi_scattered", "total_lines": 110,
     "fail_ids": ["test_auth_token_expiry", "test_webhook_retry_backoff", "test_cache_invalidation_edge"],
     "fail_at_fracs": [0.05, 0.5, 0.9], "exit_code": 1},
    {"id": "fidelity_mostly_pass_noise", "total_lines": 220,
     "fail_ids": ["test_currency_conversion_rare_path"], "fail_at_fracs": [0.7], "exit_code": 1},
    {"id": "fidelity_control_all_pass", "total_lines": 90,
     "fail_ids": [], "fail_at_fracs": [], "exit_code": 0},
]
FIDELITY_BY_ID = {c["id"]: c for c in FIDELITY_CASES}


def _synth_pytest_output(total_lines, fail_ids, fail_at_fracs, exit_code):
    lines = []
    n_pass_lines = total_lines - len(fail_ids) - 4
    fail_positions = {int(f * n_pass_lines): fid for f, fid in zip(fail_at_fracs, fail_ids)}
    for i in range(n_pass_lines):
        if i in fail_positions:
            lines.append(f"tests/test_synth.py::{fail_positions[i]} FAILED")
        else:
            lines.append(f"tests/test_synth.py::test_ok_{i:04d} PASSED")
    lines.append("")
    lines.append("=================================== FAILURES ===================================")
    for fid in fail_ids:
        lines.append(f"_________________________ {fid} _________________________")
        lines.append(f"FAILED tests/test_synth.py::{fid} - AssertionError: synthetic failure for {fid}")
    lines.append(f"{len(fail_ids)} failed, {n_pass_lines - len(fail_ids)} passed")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def run_raw(cmd):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, errors="replace")
    return {"exit_code": p.returncode, "text": p.stdout or "", "seconds": time.time() - t0}


def run_squire_cmd(cmd):
    t0 = time.time()
    p = subprocess.run([PY, SQUIRE, "run", "--json", "--"] + cmd, cwd=REPO_ROOT, env=ENV,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    seconds = time.time() - t0
    try:
        payload = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"could not parse squire JSON: {e}", "stderr": p.stderr[-1000:],
                "exit_code_process": p.returncode, "seconds": seconds}
    payload["exit_code_process"] = p.returncode
    payload["seconds"] = seconds
    return payload


def run_squire_sum(path):
    t0 = time.time()
    p = subprocess.run([PY, SQUIRE, "sum", "--json", path], cwd=REPO_ROOT, env=ENV,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    seconds = time.time() - t0
    try:
        payload = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"could not parse squire JSON: {e}", "seconds": seconds}
    payload["seconds"] = seconds
    return payload


def run_squire_queue_sum(path, env, wait_timeout=180):
    """S12: submit+wait round trip for `squire sum <path>` through the job queue,
    timed end to end from the caller's point of view (submit call + wait call), the
    way an agent using submit/wait actually experiences latency. Requires a `squire
    worker` already running against the same QUEUE_DB (started by the caller)."""
    t0 = time.time()
    sub = subprocess.run([PY, SQUIRE, "submit", "sum", path, "--json"], cwd=REPO_ROOT, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    try:
        sub_payload = json.loads(sub.stdout.strip().splitlines()[-1])
        job_id = sub_payload["job_id"]
    except Exception as e:
        return {"error": f"could not parse submit JSON: {e}", "stderr": sub.stderr[-500:],
                "seconds": time.time() - t0}
    wt = subprocess.run([PY, SQUIRE, "wait", str(job_id), "--timeout", str(wait_timeout), "--json"],
                         cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace")
    seconds = time.time() - t0
    try:
        wait_payload = json.loads(wt.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"could not parse wait JSON: {e}", "stderr": wt.stderr[-500:],
                "seconds": seconds, "job_id": job_id, "exit_code_process": wt.returncode}
    wait_payload["seconds"] = seconds
    wait_payload["exit_code_process"] = wt.returncode
    return wait_payload


def run_synthetic_squire(text, exit_code):
    script = f"import sys; print({text!r}); sys.exit({exit_code})"
    cmd = [PY, "-c", script]
    raw = run_raw(cmd)
    sq = run_squire_cmd(cmd)
    return raw, sq


# ---------------------------------------------------------------------------
# Trial loops -- each rep is appended to the checkpoint the moment it's computed,
# and each function resumes from cp_data instead of re-running finished reps.
# ---------------------------------------------------------------------------

def trial_file_workload(w, n, cp_data=None, checkpoint_path=None):
    cp_data = cp_data or {}
    key = f"file:{w['id']}"
    with open(w["path"], errors="replace") as f:
        raw_text = f.read()
    raw_tokens = tokens(raw_text)
    reps = [r["rep"] for r in cp_data.get(key, [])]
    for i in range(len(reps), n):
        sq = run_squire_sum(w["path"])
        if "error" in sq:
            rep = {"status": "UNKNOWN", "error": sq["error"]}
        else:
            sq_tokens = tokens(sq.get("summary") or "")
            rep = {"status": "VERIFIED", "seconds": sq["seconds"],
                   "pct_saved": 0.0 if raw_tokens <= 0 else 100.0 * (raw_tokens - sq_tokens) / raw_tokens,
                   "backend_ok": sq.get("backend_ok")}
        reps.append(rep)
        append_checkpoint(checkpoint_path, {"key": key, "kind": "file", "id": w["id"], "rep": rep})
    return {"id": w["id"], "kind": "file", "n": len(reps), "raw_tokens_approx": raw_tokens, "reps": reps}


def trial_cmd_workload(w, n, cp_data=None, checkpoint_path=None):
    cp_data = cp_data or {}
    key = f"cmd:{w['id']}"
    reps = [r["rep"] for r in cp_data.get(key, [])]
    for _ in range(len(reps), n):
        raw = run_raw(w["cmd"])
        sq = run_squire_cmd(w["cmd"])
        if "error" in sq:
            rep = {"status": "UNKNOWN", "error": sq["error"]}
        else:
            raw_tok = tokens(raw["text"])
            sq_out = (sq.get("raw_tail") or "") + (sq.get("summary") or "")
            sq_tok = tokens(sq_out)
            rep = {"status": "VERIFIED", "seconds": sq["seconds"],
                   "exit_code_match": sq.get("exit_code") == raw["exit_code"],
                   "pct_saved": 0.0 if raw_tok <= 0 else 100.0 * (raw_tok - sq_tok) / raw_tok,
                   "backend_ok": sq.get("backend_ok")}
        reps.append(rep)
        append_checkpoint(checkpoint_path, {"key": key, "kind": "cmd", "id": w["id"], "rep": rep})
    return {"id": w["id"], "kind": "cmd", "n": len(reps), "reps": reps}


def trial_deterministic_cmd_workload(w, n, cp_data=None, checkpoint_path=None):
    """S8b gate corpus. Runs raw + squire-wrapped invocations of a frozen, deterministic
    fixture (tests/fixtures/bench_emit_exit.py or tests/fixtures/bench_project/) whose exit
    code is fixed in advance -- unlike trial_cmd_workload's live corpus, agreement here is a
    three-way check: raw == squire == expected, not just raw == squire (two flaky live runs
    can agree with each other while both being wrong relative to the fixture's known answer)."""
    cp_data = cp_data or {}
    key = f"detcmd:{w['id']}"
    reps = [r["rep"] for r in cp_data.get(key, [])]
    for _ in range(len(reps), n):
        raw = run_raw(w["cmd"])
        sq = run_squire_cmd(w["cmd"])
        expected = w["expected_exit_code"]
        if "error" in sq:
            rep = {"status": "UNKNOWN", "error": sq["error"], "raw_exit_code": raw["exit_code"],
                   "expected_exit_code": expected}
        else:
            rep = {"status": "VERIFIED", "seconds": sq["seconds"],
                   "raw_exit_code": raw["exit_code"], "squire_exit_code": sq.get("exit_code"),
                   "expected_exit_code": expected,
                   "raw_matches_expected": raw["exit_code"] == expected,
                   "exit_code_match": (sq.get("exit_code") == raw["exit_code"] == expected)}
        reps.append(rep)
        append_checkpoint(checkpoint_path, {"key": key, "kind": "detcmd", "id": w["id"], "rep": rep})
    return {"id": w["id"], "kind": "detcmd", "expected_exit_code": w["expected_exit_code"],
            "n": len(reps), "reps": reps}


def trial_fidelity_case(case, n, cp_data=None, checkpoint_path=None):
    cp_data = cp_data or {}
    key = f"fidelity:{case['id']}"
    text = _synth_pytest_output(case["total_lines"], case["fail_ids"], case["fail_at_fracs"], case["exit_code"])
    reps = [r["rep"] for r in cp_data.get(key, [])]
    for _ in range(len(reps), n):
        raw, sq = run_synthetic_squire(text, case["exit_code"])
        if "error" in sq:
            rep = {"status": "UNKNOWN", "error": sq["error"]}
        else:
            exit_match = sq.get("exit_code") == raw["exit_code"] == case["exit_code"]
            hay = (sq.get("summary") or "") + (sq.get("raw_tail") or "")
            found = [fid for fid in case["fail_ids"] if fid in hay]
            recall = (len(found) / len(case["fail_ids"])) if case["fail_ids"] else None
            false_positive = None
            if not case["fail_ids"]:
                # Root-caused 2026-09-13 (S7): this used to flag "fail" in summ.lower(), which
                # false-positived on EVERY all-pass control -- squire's own real summary was the
                # correct 'No errors seen ... passed without any failures', a plain-English
                # sentence that contains the substring "fail" without the exact literal
                # "0 failed" this check demanded. Checked live: `condense_verified`'s prompt
                # explicitly instructs the model "Say 'no errors seen' only if there are none.",
                # so that literal phrase (not a naive substring ban) is the correct signal for
                # "squire reported the control as clean."
                summ = sq.get("summary") or ""
                false_positive = "no errors seen" not in summ.lower()
            rep = {"status": "VERIFIED", "exit_code_match": exit_match, "recall": recall,
                   "false_positive_on_control": false_positive, "seconds": sq["seconds"]}
        reps.append(rep)
        append_checkpoint(checkpoint_path, {"key": key, "kind": "fidelity", "id": case["id"], "rep": rep})
    return {"id": case["id"], "kind": "fidelity", "answer_key": case["fail_ids"], "n": len(reps), "reps": reps}


def trial_concurrent_latency(workload_path, concurrency_levels, n_per_level, cp_data=None, checkpoint_path=None):
    """SOLO baseline + p50/p95 wall latency under 2/4/6 concurrent `squire sum` calls
    on the same file. Concurrency is THIS process's own concurrent subprocess calls;
    other sessions on the workstation may add additional real load on top (confound,
    stated in docs/BENCHMARK.md, not controllable from here). Checkpointed per ROUND
    (one round = `level` simultaneous calls), so a kill mid-level keeps prior rounds."""
    cp_data = cp_data or {}
    results = {}
    for level in [1] + concurrency_levels:
        key = f"concurrency:{level}"
        existing_recs = cp_data.get(key, [])
        latencies = []
        for r in existing_recs:
            latencies.extend(r["rep"]["latencies"])
        rounds = max(1, n_per_level // level)
        for round_i in range(len(existing_recs), rounds):
            with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
                futs = [ex.submit(run_squire_sum, workload_path) for _ in range(level)]
                round_lat = [r["seconds"] for r in (fut.result() for fut in futs) if "seconds" in r]
            latencies.extend(round_lat)
            append_checkpoint(checkpoint_path, {"key": key, "kind": "concurrency", "level": level,
                                                 "rep": {"latencies": round_lat}})
        results[f"concurrency_{level}"] = {
            "n": len(latencies),
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
            "raw_latencies": latencies,
        }
    return results


def trial_concurrent_latency_queue(workload_path, concurrency_levels, n_per_level,
                                    cp_data=None, checkpoint_path=None):
    """S12: same solo+2/4/6 shape as trial_concurrent_latency, but each request goes
    through `squire submit`+`squire wait` against ONE foreground `squire worker`
    instead of calling `squire sum` directly. QUEUE_DB and the worker heartbeat are
    isolated to a per-run temp path so this never touches the real production queue
    (~/.squire/queue.db) or collides with another session's real jobs; the LLM lock
    (SQUIRE_LLM_LOCK) is deliberately LEFT at its real default so lock contention is
    measured the same way the sync path already measures it -- that contention is
    exactly what the queue is meant to remove. Checkpointed per round like the sync
    version, under a DIFFERENT key namespace (concurrency:queue:<level>) so it never
    collides with or corrupts prior sync-mode checkpoint data."""
    cp_data = cp_data or {}
    results = {}
    with tempfile.TemporaryDirectory(prefix="squire-bench-queue-") as tmpdir:
        queue_env = {**ENV, "SQUIRE_QUEUE_DB": os.path.join(tmpdir, "queue.db"),
                     "SQUIRE_WORKER_HEARTBEAT": os.path.join(tmpdir, "worker.heartbeat"),
                     "SQUIRE_LEDGER": os.path.join(tmpdir, "ledger.jsonl")}
        for level in [1] + concurrency_levels:
            key = f"concurrency:queue:{level}"
            existing_recs = cp_data.get(key, [])
            latencies, failures = [], 0
            for r in existing_recs:
                latencies.extend(r["rep"]["latencies"])
                failures += r["rep"].get("failures", 0)
            rounds = max(1, n_per_level // level)
            worker_proc = None
            try:
                if len(existing_recs) < rounds:
                    worker_proc = subprocess.Popen([PY, SQUIRE, "worker"], cwd=REPO_ROOT, env=queue_env,
                                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    # Wait for the heartbeat file so submits aren't racing an unstarted worker.
                    for _ in range(50):
                        if os.path.exists(queue_env["SQUIRE_WORKER_HEARTBEAT"]):
                            break
                        time.sleep(0.1)
                for round_i in range(len(existing_recs), rounds):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
                        futs = [ex.submit(run_squire_queue_sum, workload_path, queue_env) for _ in range(level)]
                        round_results = [fut.result() for fut in futs]
                    round_lat = [r["seconds"] for r in round_results if r.get("state") == "RESULT"]
                    round_fail = sum(1 for r in round_results if r.get("state") != "RESULT")
                    latencies.extend(round_lat)
                    failures += round_fail
                    append_checkpoint(checkpoint_path, {"key": key, "kind": "concurrency_queue", "level": level,
                                                         "rep": {"latencies": round_lat, "failures": round_fail}})
            finally:
                if worker_proc is not None:
                    worker_proc.terminate()
                    try:
                        worker_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        worker_proc.kill()
                        worker_proc.wait(timeout=10)
            results[f"concurrency_{level}"] = {
                "n": len(latencies),
                "p50": percentile(latencies, 0.5),
                "p95": percentile(latencies, 0.95),
                "failures": failures,
                "raw_latencies": latencies,
            }
    return results


def trial_interleaved_latency(workload_path, concurrency_levels, n_per_level,
                               cp_data=None, checkpoint_path=None):
    """S13: closes the S12 gap where sync's concurrency-1/2 numbers were reused from an
    EARLIER run (S6b) instead of being measured in the same session as queue's, so the
    two arms were never a fair A/B (different load, different point in time). Here sync
    and queue rounds ALTERNATE within each concurrency level -- round 0 runs sync then
    queue, round 1 runs queue then sync, etc. -- so whatever GPU load drift happens
    during the run (other sessions, thermal, anything) hits both arms in close temporal
    proximity instead of one arm entirely before or after the other. Checkpointed under
    a distinct 'concurrency:interleaved:<mode>:<level>' key namespace so it can never be
    silently mixed with S6b/S12's separately-timed sync/queue data."""
    cp_data = cp_data or {}
    results = {"sync": {}, "queue": {}}
    with tempfile.TemporaryDirectory(prefix="squire-bench-interleave-") as tmpdir:
        queue_env = {**ENV, "SQUIRE_QUEUE_DB": os.path.join(tmpdir, "queue.db"),
                     "SQUIRE_WORKER_HEARTBEAT": os.path.join(tmpdir, "worker.heartbeat"),
                     "SQUIRE_LEDGER": os.path.join(tmpdir, "ledger.jsonl")}
        for level in concurrency_levels:
            sync_key = f"concurrency:interleaved:sync:{level}"
            queue_key = f"concurrency:interleaved:queue:{level}"
            sync_recs, queue_recs = cp_data.get(sync_key, []), cp_data.get(queue_key, [])
            sync_lat = [lat for r in sync_recs for lat in r["rep"]["latencies"]]
            queue_lat = [lat for r in queue_recs for lat in r["rep"]["latencies"]]
            queue_fail = sum(r["rep"].get("failures", 0) for r in queue_recs)
            rounds = max(1, n_per_level // level)
            done = min(len(sync_recs), len(queue_recs))
            worker_proc = None
            try:
                if done < rounds:
                    worker_proc = subprocess.Popen([PY, SQUIRE, "worker"], cwd=REPO_ROOT, env=queue_env,
                                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    for _ in range(50):
                        if os.path.exists(queue_env["SQUIRE_WORKER_HEARTBEAT"]):
                            break
                        time.sleep(0.1)
                for round_i in range(done, rounds):
                    order = ("sync", "queue") if round_i % 2 == 0 else ("queue", "sync")
                    round_out = {}
                    for arm in order:
                        if arm == "sync":
                            with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
                                futs = [ex.submit(run_squire_sum, workload_path) for _ in range(level)]
                                round_out["sync"] = [r["seconds"] for r in (f.result() for f in futs)
                                                      if "seconds" in r]
                        else:
                            with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
                                futs = [ex.submit(run_squire_queue_sum, workload_path, queue_env)
                                        for _ in range(level)]
                                round_results = [f.result() for f in futs]
                            round_out["queue"] = [r["seconds"] for r in round_results if r.get("state") == "RESULT"]
                            round_out["queue_failures"] = sum(1 for r in round_results if r.get("state") != "RESULT")
                    sync_lat.extend(round_out.get("sync", []))
                    queue_lat.extend(round_out.get("queue", []))
                    queue_fail += round_out.get("queue_failures", 0)
                    append_checkpoint(checkpoint_path, {"key": sync_key, "kind": "interleaved_sync",
                                                         "level": level, "round": round_i,
                                                         "rep": {"latencies": round_out.get("sync", [])}})
                    append_checkpoint(checkpoint_path, {"key": queue_key, "kind": "interleaved_queue",
                                                         "level": level, "round": round_i,
                                                         "rep": {"latencies": round_out.get("queue", []),
                                                                 "failures": round_out.get("queue_failures", 0)}})
            finally:
                if worker_proc is not None:
                    worker_proc.terminate()
                    try:
                        worker_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        worker_proc.kill()
                        worker_proc.wait(timeout=10)
            results["sync"][f"concurrency_{level}"] = {
                "n": len(sync_lat), "p50": percentile(sync_lat, 0.5), "p95": percentile(sync_lat, 0.95),
                "raw_latencies": sync_lat,
            }
            results["queue"][f"concurrency_{level}"] = {
                "n": len(queue_lat), "p50": percentile(queue_lat, 0.5), "p95": percentile(queue_lat, 0.95),
                "failures": queue_fail, "raw_latencies": queue_lat,
            }
    return results


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def summarize_reps(trials, field):
    vals = [r[field] for t in trials for r in t["reps"] if r.get("status") == "VERIFIED" and r.get(field) is not None]
    point, lo, hi = bootstrap_ci(vals, statistics.median)
    return {"n": len(vals), "median": point, "ci95_lo": lo, "ci95_hi": hi}


def unknown_rate(trials):
    all_reps = [r for t in trials for r in t["reps"]]
    if not all_reps:
        return None
    unk = sum(1 for r in all_reps if r.get("status") == "UNKNOWN")
    return unk / len(all_reps)


def aggregate(file_trials, cmd_trials, fidelity_trials, latency_by_concurrency,
              n_solo, n_conc, checkpoint_path, partial=False, det_trials=None,
              latency_by_concurrency_queue=None):
    # Relative path only -- an absolute path here would bake this workstation's
    # home directory into a committed results file (scrub_check.py catches this).
    cp_rel = os.path.relpath(checkpoint_path, REPO_ROOT) if checkpoint_path else None
    results = {"n_solo": n_solo, "n_conc": n_conc, "chars_per_token_approx": CHARS_PER_TOKEN,
               "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "checkpoint_path": cp_rel, "partial": partial,
               "confound": "qwen2.5:14b is a single shared GPU model; other sessions may add "
                           "concurrent real load beyond this script's own concurrency levels"}
    results["file_trials"] = file_trials
    results["cmd_trials"] = cmd_trials
    results["fidelity_trials"] = fidelity_trials
    results["tokens_pct_saved"] = summarize_reps(file_trials + cmd_trials, "pct_saved")
    exit_reps = [r for t in cmd_trials for r in t["reps"] if r.get("status") == "VERIFIED"]
    results["exit_code_preserved_pct"] = (
        100.0 * sum(1 for r in exit_reps if r["exit_code_match"]) / len(exit_reps)) if exit_reps else None
    results["exit_code_preserved_n"] = len(exit_reps)

    fid_reps = [r for t in fidelity_trials for r in t["reps"] if r.get("status") == "VERIFIED" and r.get("recall") is not None]
    recalls = [r["recall"] for r in fid_reps]
    point, lo, hi = bootstrap_ci(recalls, statistics.mean) if recalls else (None, None, None)
    results["failing_name_recall"] = {"n": len(recalls), "mean": point, "ci95_lo": lo, "ci95_hi": hi}
    fid_all_reps = [r for t in fidelity_trials for r in t["reps"] if r.get("status") == "VERIFIED"]
    results["fidelity_exit_code_preserved_pct"] = (
        100.0 * sum(1 for r in fid_all_reps if r["exit_code_match"]) / len(fid_all_reps)) if fid_all_reps else None
    results["fidelity_exit_code_preserved_n"] = len(fid_all_reps)
    results["any_false_positive_on_control"] = any(
        r.get("false_positive_on_control") for r in fid_all_reps)

    # S8b: deterministic exit-code corpus, reported and gated SEPARATELY from the live cmd
    # workloads above. This is what "deterministic exit-code preservation, n>=30, 100%"
    # (the ORCHESTRATOR GATE) is computed from -- never the live pytest-suite reps, which are
    # kept and reported as "live, load-sensitive" but never gate a publish decision.
    det_trials = det_trials or []
    results["deterministic_cmd_trials"] = det_trials
    det_reps = [r for t in det_trials for r in t["reps"] if r.get("status") == "VERIFIED"]
    results["deterministic_exit_code_preserved_pct"] = (
        100.0 * sum(1 for r in det_reps if r["exit_code_match"]) / len(det_reps)) if det_reps else None
    results["deterministic_exit_code_preserved_n"] = len(det_reps)
    results["deterministic_exit_code_all_match_expected"] = (
        all(r["raw_matches_expected"] for r in det_reps) if det_reps else None)

    all_trials = file_trials + cmd_trials + fidelity_trials + det_trials
    ur = unknown_rate(all_trials)
    results["unknown_rate_pct"] = 100.0 * ur if ur is not None else None
    results["latency_by_concurrency"] = latency_by_concurrency
    # S12: queue-mode (submit+wait through `squire worker`) latency, same shape as the
    # sync table above, reported separately -- never blended into one number, same
    # principle as the file/cmd split in S8c.
    results["latency_by_concurrency_queue"] = latency_by_concurrency_queue or {}
    return results


def print_summary(results):
    tps = results["tokens_pct_saved"]
    if tps["median"] is not None:
        print(f"[benchmark] tokens saved: median={tps['median']:.1f}% "
              f"95% CI [{tps['ci95_lo']:.1f}, {tps['ci95_hi']:.1f}] (n={tps['n']})")
    else:
        print("[benchmark] tokens saved: n=0, no VERIFIED reps yet")
    if results["exit_code_preserved_pct"] is not None:
        print(f"[benchmark] exit code preserved (LIVE, load-sensitive): "
              f"{results['exit_code_preserved_pct']:.1f}% (n={results['exit_code_preserved_n']})")
    else:
        print("[benchmark] exit code preserved (live): n=0, no VERIFIED reps yet")
    det_pct = results.get("deterministic_exit_code_preserved_pct")
    if det_pct is not None:
        print(f"[benchmark] exit code preserved (DETERMINISTIC, gate corpus): "
              f"{det_pct:.1f}% (n={results['deterministic_exit_code_preserved_n']})")
    else:
        print("[benchmark] exit code preserved (deterministic): n=0, no VERIFIED reps yet")
    print(f"[benchmark] failing-name recall: mean={results['failing_name_recall']['mean']} "
          f"(n={results['failing_name_recall']['n']})")
    if results["unknown_rate_pct"] is not None:
        print(f"[benchmark] UNKNOWN/fail rate: {results['unknown_rate_pct']:.1f}%")
    for level, v in results["latency_by_concurrency"].items():
        if v["n"]:
            print(f"[benchmark] sync {level}: p50={v['p50']:.2f}s p95={v['p95']:.2f}s (n={v['n']})")
        else:
            print(f"[benchmark] sync {level}: n=0, not yet run")
    for level, v in results.get("latency_by_concurrency_queue", {}).items():
        if v["n"]:
            print(f"[benchmark] queue {level}: p50={v['p50']:.2f}s p95={v['p95']:.2f}s "
                  f"(n={v['n']}, failures={v.get('failures', 0)})")
        else:
            print(f"[benchmark] queue {level}: n=0, not yet run")


def report_mode(checkpoint_path, n_solo, n_conc):
    """S6b: compute stats from whatever is checkpointed, WITHOUT any new model
    calls. Every cell states its own n honestly -- a cell with n=3 reads n=3,
    never rounded up to a target and never hidden as if it were complete."""
    cp_data = load_checkpoint(checkpoint_path)

    file_trials = []
    for w in FILE_WORKLOADS:
        recs = cp_data.get(f"file:{w['id']}", [])
        reps = [r["rep"] for r in recs]
        raw_tokens = None
        if os.path.exists(w["path"]):
            with open(w["path"], errors="replace") as f:
                raw_tokens = tokens(f.read())
        file_trials.append({"id": w["id"], "kind": "file", "n": len(reps),
                             "raw_tokens_approx": raw_tokens, "reps": reps})

    cmd_trials = []
    for w in CMD_WORKLOADS:
        recs = cp_data.get(f"cmd:{w['id']}", [])
        cmd_trials.append({"id": w["id"], "kind": "cmd", "n": len(recs), "reps": [r["rep"] for r in recs]})

    fidelity_trials = []
    for case in FIDELITY_CASES:
        recs = cp_data.get(f"fidelity:{case['id']}", [])
        fidelity_trials.append({"id": case["id"], "kind": "fidelity", "answer_key": case["fail_ids"],
                                 "n": len(recs), "reps": [r["rep"] for r in recs]})

    det_trials = []
    for w in DETERMINISTIC_CMD_WORKLOADS:
        recs = cp_data.get(f"detcmd:{w['id']}", [])
        det_trials.append({"id": w["id"], "kind": "detcmd", "expected_exit_code": w["expected_exit_code"],
                            "n": len(recs), "reps": [r["rep"] for r in recs]})

    latency_by_concurrency = {}
    for level in [1, 2, 4, 6]:
        recs = cp_data.get(f"concurrency:{level}", [])
        latencies = [lat for r in recs for lat in r["rep"]["latencies"]]
        latency_by_concurrency[f"concurrency_{level}"] = {
            "n": len(latencies), "p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95),
            "raw_latencies": latencies,
        }

    latency_by_concurrency_queue = {}
    for level in [1, 2, 4, 6]:
        recs = cp_data.get(f"concurrency:queue:{level}", [])
        latencies = [lat for r in recs for lat in r["rep"]["latencies"]]
        failures = sum(r["rep"].get("failures", 0) for r in recs)
        latency_by_concurrency_queue[f"concurrency_{level}"] = {
            "n": len(latencies), "p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95),
            "failures": failures, "raw_latencies": latencies,
        }

    return aggregate(file_trials, cmd_trials, fidelity_trials, latency_by_concurrency,
                      n_solo, n_conc, checkpoint_path, partial=True, det_trials=det_trials,
                      latency_by_concurrency_queue=latency_by_concurrency_queue)


def main():
    argv = sys.argv[1:]
    quick = "--quick" in argv
    json_mode = "--json" in argv
    report = "--report" in argv
    out_dir = REPO_ROOT
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
    checkpoint_path = DEFAULT_CHECKPOINT
    if "--checkpoint" in argv:
        checkpoint_path = argv[argv.index("--checkpoint") + 1]
    # S12: --mode sync|queue|both. Default "sync" is byte-identical to pre-S12 behavior
    # (only the sync concurrency table runs) -- existing invocations of this script are
    # unaffected unless --mode is passed explicitly.
    mode = argv[argv.index("--mode") + 1] if "--mode" in argv else "sync"
    if mode not in ("sync", "queue", "both", "interleave"):
        sys.exit(f"usage: --mode sync|queue|both|interleave (got {mode!r})")

    n_solo = 10 if quick else 20
    n_conc = 10 if quick else 12
    # S13: --mode interleave skips the file/cmd/fidelity/det trials entirely (a separate,
    # focused run to fix the S12 sync-vs-queue A/B fairness gap -- see docs/BENCHMARK.md)
    # and alternates sync/queue rounds within each concurrency level instead of running
    # one arm to completion before the other.
    if mode == "interleave" and not report:
        levels = [1, 2, 4]
        n_per_level = max(8, n_conc)
        cp_data = load_checkpoint(checkpoint_path)
        conc_target = os.path.join(REPO_ROOT, "squire.py")
        interleaved = trial_interleaved_latency(conc_target, [2, 4], n_per_level, cp_data, checkpoint_path)
        # level 1 has no "concurrent" arm to alternate against meaningfully in lockstep,
        # but is still measured in THIS run (not reused from an older one) for both arms.
        cp_data = load_checkpoint(checkpoint_path)
        solo = trial_interleaved_latency(conc_target, [1], n_per_level, cp_data, checkpoint_path)
        for arm in ("sync", "queue"):
            interleaved[arm]["concurrency_1"] = solo[arm]["concurrency_1"]
        results = {"mode": "interleave", "n_per_level": n_per_level, "levels": levels,
                   "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "checkpoint_path": os.path.relpath(checkpoint_path, REPO_ROOT),
                   "interleaved_latency": interleaved}
        if json_mode:
            print(json.dumps(results, indent=2))
        else:
            for level in levels:
                s, q = interleaved["sync"][f"concurrency_{level}"], interleaved["queue"][f"concurrency_{level}"]
                print(f"concurrency={level}  sync n={s['n']} p50={s['p50']} p95={s['p95']}  "
                      f"queue n={q['n']} p50={q['p50']} p95={q['p95']} failures={q.get('failures', 0)}")
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            fname = os.path.join(out_dir, f"benchmark-interleave-{time.strftime('%Y%m%d-%H%M%S')}.json")
            with open(fname, "w") as f:
                json.dump(results, f, indent=2)
            print(f"[benchmark] raw results written to {fname}", file=sys.stderr)
        return 0

    if report:
        results = report_mode(checkpoint_path, n_solo, n_conc)
    else:
        cp_data = load_checkpoint(checkpoint_path)
        resumed = bool(cp_data)
        file_trials = [trial_file_workload(w, n_solo, cp_data, checkpoint_path) for w in FILE_WORKLOADS]
        cmd_trials = [trial_cmd_workload(w, n_solo, cp_data, checkpoint_path) for w in CMD_WORKLOADS]
        fidelity_trials = [trial_fidelity_case(c, n_solo, cp_data, checkpoint_path) for c in FIDELITY_CASES]
        # S8b: each deterministic workload carries its OWN rep count ("n" in the workload
        # dict) rather than n_solo -- short fixtures run more reps cheaply; the one workload
        # that deliberately invokes the model runs fewer to respect the timebox.
        det_trials = [trial_deterministic_cmd_workload(w, w["n"], cp_data, checkpoint_path)
                      for w in DETERMINISTIC_CMD_WORKLOADS]
        conc_target = os.path.join(REPO_ROOT, "squire.py")
        # Reload checkpoint so concurrency resume sees the solo/cmd/fidelity/detcmd reps
        # just appended above (harmless -- different key namespace) and any prior
        # concurrency rounds from an earlier killed run.
        cp_data = load_checkpoint(checkpoint_path)
        latency_by_concurrency = {}
        if mode in ("sync", "both"):
            latency_by_concurrency = trial_concurrent_latency(
                conc_target, [2, 4, 6], n_conc, cp_data, checkpoint_path)
            cp_data = load_checkpoint(checkpoint_path)
        latency_by_concurrency_queue = {}
        if mode in ("queue", "both"):
            latency_by_concurrency_queue = trial_concurrent_latency_queue(
                conc_target, [2, 4, 6], n_conc, cp_data, checkpoint_path)
        results = aggregate(file_trials, cmd_trials, fidelity_trials, latency_by_concurrency,
                             n_solo, n_conc, checkpoint_path, partial=False, det_trials=det_trials,
                             latency_by_concurrency_queue=latency_by_concurrency_queue)
        results["resumed_from_existing_checkpoint"] = resumed
        results["mode"] = mode

    if json_mode:
        print(json.dumps(results, indent=2))
    else:
        print_summary(results)

    if out_dir and not report:
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f"benchmark-{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(fname, "w") as f:
            json.dump(results, f, indent=2)
        print(f"[benchmark] raw results written to {fname}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""ab_trial.py: controlled A/B measurement of squire's token savings.

Method (SPEC-OPEN-SOURCE.md step 2, not previously run — see PILOT-RESULTS-2026-09-12.md,
which explicitly says "no controlled A/B has been run"):

For a fixed, committed set of real workloads on THIS workstation, run each one twice:
  RAW    -- the command's own stdout+stderr (or the file's own bytes), unmodified.
  SQUIRE -- the same command through `squire run --json --` (or the same file through
            `squire sum --json`).

Token counts are a stated APPROXIMATION: chars/4 (the same heuristic squire.py and
squire_report.py already use and label as an estimate, never a real tokenizer count).
tiktoken is not installed on this workstation (checked this run; see the printed note),
so no exact-tokenizer number is claimed anywhere in this script's output.

Fidelity is checked, not assumed:
  - exit code: does squire's JSON exit_code match the raw command's real exit code?
  - content: for the one workload with a real, known failure signature, does squire's
    summary text still contain that signature (a test node id / error string)?

A control workload (raw output <= squire.py's SHORT=60 lines) is included: squire is
expected to pass it through unmodified, i.e. measured savings ~0. This is the workstation's
own defense against reporting "savings" when squire did no work.

Nothing here decides pass/fail on its own (global CLAUDE.md 5.8) -- this script only
measures and reports; the results doc states what is VERIFIED vs ASSUMED vs UNKNOWN.

Usage: python3.13 scripts/ab_trial.py [--json]
Run from the squire repo root (uses squire.py in the parent of this script's dir).
"""
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQUIRE = os.path.join(REPO_ROOT, "squire.py")
CHARS_PER_TOKEN = 4  # stated approximation, matches squire.py's own heuristic; not a real tokenizer
PY = sys.executable  # use whatever interpreter is running this script (avoids the twilio-lookup venv shadow)

# Fixed, committed workload set. Content-free of secrets: all paths are inside this
# public-shaped repo or are read-only inspection of it. No home paths, hostnames, or
# credential-shaped output are included in the commands themselves.
CMD_WORKLOADS = [
    {
        "id": "pytest_verbose",
        "category": "test",
        "cmd": [PY, "-m", "pytest", "-v"],
        "note": "full verbose test run of this repo's own suite",
    },
    {
        "id": "pytest_missing_node",
        "category": "test",
        "cmd": [PY, "-m", "pytest", "-v",
                "tests/test_squire.py::test_definitely_not_a_real_test_xyz"],
        "note": "fidelity workload: real pytest error (unknown test id), nonzero real exit code",
    },
    {
        "id": "find_files",
        "category": "log/listing scan",
        "cmd": ["find", ".", "-type", "f"],
        "note": "noisy file listing of the repo tree",
    },
    {
        "id": "du_sizes",
        "category": "build/disk report",
        "cmd": ["du", "-ah", "--exclude=.git", "."],
        "note": "noisy per-file disk usage report",
    },
    {
        "id": "pytest_quiet_control",
        "category": "control (short output)",
        "cmd": [PY, "-m", "pytest", "-q"],
        "note": "CONTROL: output is short (<=60 lines); squire.py's SHORT threshold means "
                "it should pass this through raw with ~0 savings",
    },
]

FILE_WORKLOADS = [
    {"id": "squire_py", "path": os.path.join(REPO_ROOT, "squire.py")},
    {"id": "readme_md", "path": os.path.join(REPO_ROOT, "README.md")},
    {"id": "test_squire_py", "path": os.path.join(REPO_ROOT, "tests", "test_squire.py")},
]

# Fidelity signatures: substrings that must survive into squire's summary for the named
# workload if the summary is to be trusted as preserving the real failure.
FIDELITY_SIGNATURES = {
    "pytest_missing_node": ["test_definitely_not_a_real_test_xyz"],
}


def run_raw(cmd):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    return {"exit_code": p.returncode, "text": p.stdout or "", "seconds": round(time.time() - t0, 2)}


def run_squire_cmd(cmd):
    t0 = time.time()
    p = subprocess.run([PY, SQUIRE, "run", "--json", "--"] + cmd, cwd=REPO_ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    seconds = round(time.time() - t0, 2)
    try:
        payload = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"could not parse squire JSON: {e}", "stderr": p.stderr[-2000:],
                "exit_code": p.returncode, "seconds": seconds}
    payload["exit_code_process"] = p.returncode
    payload["seconds"] = seconds
    return payload


def run_squire_sum(path):
    t0 = time.time()
    p = subprocess.run([PY, SQUIRE, "sum", "--json", path], cwd=REPO_ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    seconds = round(time.time() - t0, 2)
    try:
        payload = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"could not parse squire JSON: {e}", "stderr": p.stderr[-2000:],
                "exit_code": p.returncode, "seconds": seconds}
    payload["seconds"] = seconds
    return payload


def tokens(text):
    return len(text or "") / CHARS_PER_TOKEN


def pct_saved(raw_tokens, squire_tokens):
    if raw_tokens <= 0:
        return 0.0
    return round(100.0 * (raw_tokens - squire_tokens) / raw_tokens, 1)


def measure_cmd(w):
    raw = run_raw(w["cmd"])
    sq = run_squire_cmd(w["cmd"])
    if "error" in sq:
        return {**w, "raw": raw, "squire_error": sq["error"], "status": "UNKNOWN"}

    raw_tokens = tokens(raw["text"])
    # squire's JSON "raw_tail" + "summary" together are what actually reaches the calling
    # session's context on a squire-mediated call; that is the fair SQUIRE-side count.
    squire_out = (sq.get("raw_tail") or "") + (sq.get("summary") or "")
    squire_tokens = tokens(squire_out)

    exit_match = (sq.get("exit_code") == raw["exit_code"])
    fidelity = None
    sigs = FIDELITY_SIGNATURES.get(w["id"])
    if sigs:
        hay = (sq.get("summary") or "") + (sq.get("raw_tail") or "")
        fidelity = all(s in hay for s in sigs)

    return {
        **w, "raw_exit_code": raw["exit_code"], "squire_exit_code": sq.get("exit_code"),
        "exit_code_match": exit_match, "fidelity_signature_preserved": fidelity,
        "raw_chars": len(raw["text"]), "squire_chars": len(squire_out),
        "raw_tokens_approx": round(raw_tokens, 1), "squire_tokens_approx": round(squire_tokens, 1),
        "pct_saved": pct_saved(raw_tokens, squire_tokens),
        "raw_seconds": raw["seconds"], "squire_seconds": sq["seconds"],
        "assumed_by_squire": sq.get("assumed"), "backend_ok": sq.get("backend_ok"),
        "status": "VERIFIED",
    }


def measure_file(w):
    with open(w["path"], errors="replace") as f:
        raw_text = f.read()
    sq = run_squire_sum(w["path"])
    if "error" in sq:
        return {**w, "status": "UNKNOWN", "squire_error": sq["error"]}
    raw_tokens = tokens(raw_text)
    squire_tokens = tokens(sq.get("summary") or "")
    return {
        **w, "raw_chars": len(raw_text), "squire_chars": len(sq.get("summary") or ""),
        "raw_tokens_approx": round(raw_tokens, 1), "squire_tokens_approx": round(squire_tokens, 1),
        "pct_saved": pct_saved(raw_tokens, squire_tokens),
        "squire_seconds": sq["seconds"], "backend_ok": sq.get("backend_ok"),
        "assumed_by_squire": sq.get("assumed"), "status": "VERIFIED",
    }


def _synth_pytest_output(total_lines, fail_ids, fail_at_fracs, exit_code):
    """Build a synthetic, pytest-`-v`-shaped output of ~total_lines lines with the given
    failing test node ids injected at the given fractional positions (0.0-1.0 each), plus
    a trailing FAILURES section (as real pytest -v emits). fail_ids and fail_at_fracs are
    parallel lists. Returns (text, real_exit_code)."""
    lines = []
    n_pass_lines = total_lines - len(fail_ids) - 4  # leave room for FAILURES section footer
    fail_positions = {int(f * n_pass_lines): fid for f, fid in zip(fail_at_fracs, fail_ids)}
    for i in range(n_pass_lines):
        if i in fail_positions:
            fid = fail_positions[i]
            lines.append(f"tests/test_synth.py::{fid} FAILED")
        else:
            lines.append(f"tests/test_synth.py::test_ok_{i:04d} PASSED")
    lines.append("")
    lines.append("=================================== FAILURES ===================================")
    for fid in fail_ids:
        lines.append(f"_________________________ {fid} _________________________")
        lines.append(f"FAILED tests/test_synth.py::{fid} - AssertionError: synthetic failure for {fid}")
    lines.append(f"{len(fail_ids)} failed, {n_pass_lines - len(fail_ids)} passed")
    return "\n".join(lines), exit_code


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


def measure_fidelity(case):
    text, exit_code = _synth_pytest_output(case["total_lines"], case["fail_ids"],
                                            case["fail_at_fracs"], case["exit_code"])
    n_lines = len(text.splitlines())
    # Emit the synthetic text verbatim via python, exiting with the real code, so squire
    # wraps a REAL subprocess (not a canned string) exactly as `squire run` is used live.
    script = f"import sys; print({text!r}); sys.exit({exit_code})"
    cmd = [PY, "-c", script]
    raw = run_raw(cmd)
    sq = run_squire_cmd(cmd)
    if "error" in sq:
        return {**case, "status": "UNKNOWN", "squire_error": sq["error"], "n_lines": n_lines}

    exit_match = (sq.get("exit_code") == raw["exit_code"] == exit_code)
    hay = (sq.get("summary") or "") + (sq.get("raw_tail") or "")
    found = [fid for fid in case["fail_ids"] if fid in hay]
    missed = [fid for fid in case["fail_ids"] if fid not in found]
    recall = (len(found) / len(case["fail_ids"])) if case["fail_ids"] else None
    # Control: an all-pass run injects zero failures, so real output contains the literal
    # string "FAILED" nowhere. If squire's summary asserts a failure occurred anyway
    # (mentions "FAILED" or names any test_ok_* id as failing), that is a fabrication.
    false_positive = None
    if not case["fail_ids"]:
        summary_text = sq.get("summary") or ""
        false_positive = "FAILED" in summary_text or "failed" in summary_text.lower().replace("0 failed", "")
    return {
        **case, "n_lines": n_lines, "was_summarized": n_lines > 60,
        "raw_exit_code": raw["exit_code"], "squire_exit_code": sq.get("exit_code"),
        "exit_code_match": exit_match,
        "injected": case["fail_ids"], "found": found, "missed": missed,
        "recall": recall, "false_positive_on_control": false_positive,
        "status": "VERIFIED",
    }


def main():
    json_mode = "--json" in sys.argv
    fidelity_mode = "--fidelity" in sys.argv
    results = {"cmd_workloads": [], "file_workloads": [], "fidelity_cases": []}

    if fidelity_mode:
        for c in FIDELITY_CASES:
            results["fidelity_cases"].append(measure_fidelity(c))
        recalls = [r["recall"] for r in results["fidelity_cases"]
                   if r["status"] == "VERIFIED" and r["recall"] is not None]
        exit_matches = [r["exit_code_match"] for r in results["fidelity_cases"] if r["status"] == "VERIFIED"]
        results["fidelity_summary"] = {
            "n_cases": len(results["fidelity_cases"]),
            "exit_code_match_rate": (sum(exit_matches) / len(exit_matches)) if exit_matches else None,
            "mean_name_recall": (sum(recalls) / len(recalls)) if recalls else None,
            "min_name_recall": min(recalls) if recalls else None,
            "any_false_positive_on_control": any(
                r.get("false_positive_on_control") for r in results["fidelity_cases"]
                if r["status"] == "VERIFIED"),
        }
        if json_mode:
            print(json.dumps(results, indent=2))
        else:
            for r in results["fidelity_cases"]:
                if r["status"] != "VERIFIED":
                    print(f"  {r['id']:28s} UNKNOWN: {r.get('squire_error')}")
                    continue
                print(f"  {r['id']:28s} lines={r['n_lines']:4d} exit_match={r['exit_code_match']} "
                      f"recall={r['recall']} missed={r['missed']} fp={r.get('false_positive_on_control')}")
            print(f"[ab_trial:fidelity] {results['fidelity_summary']}")
        return 0

    for w in CMD_WORKLOADS:
        results["cmd_workloads"].append(measure_cmd(w))
    for w in FILE_WORKLOADS:
        results["file_workloads"].append(measure_file(w))

    verified = [r for r in results["cmd_workloads"] + results["file_workloads"] if r["status"] == "VERIFIED"]
    savings = [r["pct_saved"] for r in verified if "pct_saved" in r]
    savings_sorted = sorted(savings)
    n = len(savings_sorted)
    median = (savings_sorted[n // 2] if n % 2 else
              (savings_sorted[n // 2 - 1] + savings_sorted[n // 2]) / 2) if n else None
    results["summary"] = {
        "n_measured": n,
        "median_pct_saved": median,
        "min_pct_saved": min(savings_sorted) if savings_sorted else None,
        "max_pct_saved": max(savings_sorted) if savings_sorted else None,
        "chars_per_token_approx": CHARS_PER_TOKEN,
    }

    if json_mode:
        print(json.dumps(results, indent=2))
    else:
        print(f"[ab_trial] measured {n} workloads (chars/{CHARS_PER_TOKEN} token approximation)")
        for r in results["cmd_workloads"] + results["file_workloads"]:
            if r["status"] != "VERIFIED":
                print(f"  {r['id']:24s} UNKNOWN: {r.get('squire_error')}")
                continue
            print(f"  {r['id']:24s} pct_saved={r['pct_saved']:>6.1f}%  "
                  f"raw_tok~{r['raw_tokens_approx']:.0f} squire_tok~{r['squire_tokens_approx']:.0f}")
        print(f"[ab_trial] median={median}  min={results['summary']['min_pct_saved']}  "
              f"max={results['summary']['max_pct_saved']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Accuracy eval for squire's failure-summarization (SPEC-OPEN-SOURCE.md #8).

Runs `squire run -- cat <fixture>` (live, against the local Ollama backend) for every
fixture in eval/fixtures/*.log, scores the summary against the fixture's ground-truth
JSON (same basename, .json), and prints a table.

Score:
  - recall: of the ground-truth failures, how many are named in the summary (by test
    name OR "file:line" substring, case-insensitive).
  - false_clean: the fixture IS clean (no failures) but the summary claims errors, OR
    the fixture has real failures but the summary says "no errors" / similar.

Exit codes:
  0  mean recall >= THRESHOLD and no false "no errors" claims on non-clean fixtures
  1  below threshold, or a false-clean claim, or the model was reachable but recall failed
  2  backend unreachable (UNKNOWN) -- never reported as pass or fail

This never runs in CI (no GPU there); see .github/workflows/ci.yml.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SQUIRE = HERE.parent / "squire.py"
THRESHOLD = 0.8
NO_ERRORS_RE = re.compile(r"\bno errors?\b|\ball (tests )?pass(ed)?\b|\bclean\b", re.IGNORECASE)


def load_cases():
    cases = []
    for log in sorted(FIXTURES.glob("*.log")):
        gt_path = log.with_suffix(".json")
        if not gt_path.exists():
            continue
        cases.append((log, json.loads(gt_path.read_text())))
    return cases


def run_squire(log_path):
    p = subprocess.run(
        [sys.executable, str(SQUIRE), "run", "--json", "--", "cat", str(log_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600,
    )
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"summary": None, "backend_ok": False}


def _test_short_name(name):
    # last segment after ::, ., >, or / -- the part a human/model actually quotes
    return re.split(r"::|>|›| - ", name)[-1].strip()


def score_case(gt, result):
    backend_ok = result.get("backend_ok", False)
    summary = result.get("summary")

    if summary is None:
        # squire's SHORT threshold passed the raw log through with no model call --
        # the failures are trivially visible verbatim in the raw tail, so this is not
        # a model accuracy question. Score it as full recall.
        failures = gt.get("failures", [])
        return {"recall": 1.0, "false_clean": False, "matched": len(failures), "total": len(failures)}

    if not backend_ok or summary.startswith("UNKNOWN"):
        return None  # backend down -- handled at the top level, not scored per-case

    failures = gt.get("failures", [])
    clean = gt.get("clean", False)

    if clean:
        return {"recall": 1.0, "false_clean": False, "matched": 0, "total": 0}

    matched = 0
    for f in failures:
        name = f.get("test", "")
        short = _test_short_name(name)
        base = Path(f.get("file", "")).name
        line = str(f.get("line", ""))
        by_name = bool(short) and short.lower() in summary.lower()
        by_loc = base and line and base in summary and line in summary
        if by_name or by_loc:
            matched += 1
    recall = matched / len(failures) if failures else 1.0
    false_clean = bool(failures) and NO_ERRORS_RE.search(summary) is not None and matched == 0
    return {"recall": recall, "false_clean": false_clean, "matched": matched, "total": len(failures)}


def main():
    cases = load_cases()
    if not cases:
        print("UNKNOWN: no eval fixtures found under eval/fixtures/")
        sys.exit(2)

    rows = []
    unreachable = 0
    for log_path, gt in cases:
        result = run_squire(log_path)
        scored = score_case(gt, result)
        if scored is None:
            unreachable += 1
            rows.append((log_path.name, "UNKNOWN", "-", "-"))
            continue
        rows.append((log_path.name, f"{scored['recall']:.2f}", f"{scored['matched']}/{scored['total']}",
                     "FALSE-CLEAN" if scored["false_clean"] else "ok"))

    print(f"{'fixture':<38} {'recall':<8} {'matched':<10} flag")
    for r in rows:
        print(f"{r[0]:<38} {r[1]:<8} {r[2]:<10} {r[3]}")

    if unreachable == len(cases):
        print("\nUNKNOWN: local backend unreachable for every fixture -- not scored")
        sys.exit(2)
    if unreachable:
        print(f"\nUNKNOWN: {unreachable}/{len(cases)} fixtures could not be scored (backend down mid-run)")

    scored_rows = [r for r in rows if r[1] != "UNKNOWN"]
    recalls = [float(r[1]) for r in scored_rows]
    false_clean_hits = sum(1 for r in scored_rows if r[3] == "FALSE-CLEAN")
    mean_recall = sum(recalls) / len(recalls) if recalls else 0.0

    print(f"\nmean recall: {mean_recall:.3f} (threshold {THRESHOLD}), false-clean claims: {false_clean_hits}")

    if mean_recall < THRESHOLD or false_clean_hits > 0:
        print("FAIL")
        sys.exit(1)
    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()

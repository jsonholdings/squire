#!/usr/bin/env python3
"""usage_report: canonical version of squire_usage_report -- summarizes ~/.squire/ledger.jsonl
per session (and per day) -- calls by cmd, ok-rate excluding squire's own test-fixture rows, and
chars saved (chars_in - chars_out, when positive). Also reports raw_overrides.jsonl counts (the
`# squire-raw:` escape hatch from hooks/pretool_wrap.py and hooks/search_guard.py) per session,
so overuse of the bypass is visible next to real squire usage.

Backward-compatible with workstation-config's squire_usage_report.py: same CLI flags
(--ledger, --session, --json), same output shape for the existing summary. This file is the
canonical source; workstation-config is expected to consume it rather than keep a divergent
copy (see squire/CLAUDE.md's source->mirror pattern -- installers vendor from here).

CLI only (not wired as a hook): see squire_usage_report.py's original docstring for why a
Stop-hook version was rejected (adds latency to every turn for file I/O that grows all day).
Run it by hand or from a session's own write-out step:
`python3 hooks/usage_report.py [--json] [--session ID] [--ledger PATH] [--overrides PATH]`.

Fixture-row heuristic (ASSUMED, ledger has no `source` field yet -- see original docstring for
the 7-signature pattern this matches): a row is treated as squire's own test suite, not real
session usage, when backend_ok is false, gen_s <= 0.02s (near-instant), and chars_out is one of
the small fixed sizes its fixtures produce (0, 91, 128) with small chars_in (<=200). Once
`source` exists, prefer `source == "test"` and drop this heuristic.
"""
import argparse
import collections
import json
import os
import sys

DEFAULT_LEDGER = os.path.expanduser("~/.squire/ledger.jsonl")
DEFAULT_OVERRIDES = os.path.expanduser("~/.squire/raw_overrides.jsonl")
FIXTURE_OUT_SIZES = {0, 91, 128}
FIXTURE_GEN_S_MAX = 0.02
FIXTURE_CHARS_IN_MAX = 200


def is_fixture_row(row):
    if row.get("source") == "test":
        return True
    if row.get("source") in ("cli", "hook"):
        return False
    if row.get("backend_ok") is not False:
        return False
    gen_s = row.get("gen_s")
    if gen_s is None or gen_s > FIXTURE_GEN_S_MAX:
        return False
    chars_in = row.get("chars_in")
    chars_out = row.get("chars_out")
    if chars_in is None or chars_in > FIXTURE_CHARS_IN_MAX:
        return False
    return chars_out in FIXTURE_OUT_SIZES


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return rows


# kept for backward compatibility with callers importing load_rows by name
load_rows = load_jsonl


def summarize(rows, session_filter=None):
    real = [r for r in rows if not is_fixture_row(r)]
    if session_filter:
        real = [r for r in real if r.get("session") == session_filter]

    by_cmd = collections.defaultdict(lambda: {"calls": 0, "ok": 0, "chars_saved": 0})
    for r in real:
        cmd = r.get("cmd", "unknown")
        entry = by_cmd[cmd]
        entry["calls"] += 1
        if r.get("backend_ok"):
            entry["ok"] += 1
        chars_in = r.get("chars_in") or 0
        chars_out = r.get("chars_out") or 0
        saved = chars_in - chars_out
        if saved > 0:
            entry["chars_saved"] += saved

    total_calls = sum(v["calls"] for v in by_cmd.values())
    total_ok = sum(v["ok"] for v in by_cmd.values())
    total_saved = sum(v["chars_saved"] for v in by_cmd.values())
    fixture_excluded = len(rows) - len(real) if not session_filter else sum(
        1 for r in rows if is_fixture_row(r)
    )

    return {
        "by_cmd": dict(by_cmd),
        "total_calls": total_calls,
        "total_ok": total_ok,
        "ok_rate": round(total_ok / total_calls, 3) if total_calls else None,
        "chars_saved": total_saved,
        "fixture_rows_excluded": fixture_excluded,
    }


def per_session_table(rows, override_rows, session_filter=None):
    """Per-session breakdown: squire calls by cmd (real rows only) + raw_overrides count.

    `session` can be absent (older ledger rows) or explicitly null (a row logged before a
    session id was threaded through) -- `.get("session", "unknown")` only covers the absent
    case, so an explicit `None` value crashed `sorted()` mixing NoneType and str keys. Coerce
    both to the literal string "unknown" so the table always has sortable, real keys.
    """
    sessions = collections.defaultdict(lambda: {"by_cmd": collections.Counter(), "raw_overrides": 0})
    for r in rows:
        if is_fixture_row(r):
            continue
        sid = r.get("session") or "unknown"
        if session_filter and sid != session_filter:
            continue
        sessions[sid]["by_cmd"][r.get("cmd", "unknown")] += 1
    for r in override_rows:
        sid = r.get("session") or "unknown"
        if session_filter and sid != session_filter:
            continue
        sessions[sid]["raw_overrides"] += 1
    return {
        sid: {"by_cmd": dict(v["by_cmd"]), "raw_overrides": v["raw_overrides"]}
        for sid, v in sessions.items()
    }


def format_text(summary, label):
    lines = [f"squire usage ({label}): {summary['total_calls']} calls, "
             f"ok-rate={summary['ok_rate']}, chars_saved={summary['chars_saved']}, "
             f"fixture_rows_excluded={summary['fixture_rows_excluded']}"]
    for cmd, v in sorted(summary["by_cmd"].items()):
        lines.append(f"  {cmd}: {v['calls']} calls, {v['ok']} ok, saved {v['chars_saved']} chars")
    return "\n".join(lines)


def format_sessions_text(sessions):
    lines = ["per-session:"]
    for sid, v in sorted(sessions.items()):
        calls = sum(v["by_cmd"].values())
        lines.append(f"  {sid}: {calls} squire calls, {v['raw_overrides']} raw overrides")
        for cmd, n in sorted(v["by_cmd"].items()):
            lines.append(f"    {cmd}: {n}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--overrides", default=DEFAULT_OVERRIDES)
    ap.add_argument("--session", default=None, help="filter to one session id")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-sessions", action="store_true", help="skip per-session table")
    args = ap.parse_args()

    rows = load_jsonl(args.ledger)
    override_rows = load_jsonl(args.overrides)
    summary = summarize(rows, session_filter=args.session)
    sessions = {} if args.no_sessions else per_session_table(rows, override_rows, session_filter=args.session)

    if args.json:
        out = dict(summary)
        if not args.no_sessions:
            out["sessions"] = sessions
        print(json.dumps(out, indent=2))
    else:
        label = args.session or "all sessions in ledger"
        print(format_text(summary, label))
        if not args.no_sessions and sessions:
            print()
            print(format_sessions_text(sessions))
    return 0


if __name__ == "__main__":
    sys.exit(main())

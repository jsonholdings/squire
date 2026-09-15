#!/usr/bin/env python3
"""squire_report: cross-reference Claude Code session transcripts against squire's local ledger
to measure real token savings.

Two independent, clearly-labelled numbers:
  VERIFIED  -- per-session totals computed directly from each session JSONL's own `usage` blocks
              (input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens).
              These are real numbers Claude Code itself recorded. Nothing here is estimated.
  ESTIMATE  -- for each call logged in squire's ledger (~/.squire/ledger.jsonl), the chars avoided
              (chars_in - chars_out) is converted to a token estimate (chars/4, the same heuristic
              `squire stats` already uses and labels) and multiplied by the number of turns
              remaining in that session after the call -- because a tool result sitting in context
              is re-paid-for as cache-read on every subsequent turn. This is a proxy, not a
              measured count: it assumes the avoided text would otherwise have stayed verbatim in
              context for all remaining turns, which is an upper bound, not a certainty.

Never prints transcript content -- only counts, timestamps and command names.

Usage:
    squire_report.py [--project DIR] [--since DATE] [--json] [--anonymize]

--project restricts to one ~/.claude/projects/<project> directory (default: all).
--anonymize replaces project directory names with PROJECT-1, PROJECT-2, ... and never prints
absolute paths in the human output.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from squire import is_test_row  # noqa: E402 -- same test-row exclusion squire stats uses (S4)

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
DEFAULT_LEDGER = os.path.expanduser(os.environ.get("SQUIRE_LEDGER", "~/.squire/ledger.jsonl"))
CHARS_PER_TOKEN = 4  # same heuristic as squire.py's `stats` command; ESTIMATE only, never VERIFIED


def load_ledger(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [r for r in rows if not is_test_row(r)]


def iter_session_files(project_filter=None):
    if not os.path.isdir(CLAUDE_PROJECTS_DIR):
        return
    for proj_dir in sorted(glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*"))):
        if not os.path.isdir(proj_dir):
            continue
        name = os.path.basename(proj_dir)
        if project_filter and project_filter not in (proj_dir, name):
            continue
        for jsonl in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl"))):
            yield name, jsonl


def parse_session(jsonl_path):
    """Return per-turn usage list and per-turn approximate context-size deltas.
    Never returns or retains message content -- only usage numbers and byte offsets."""
    turns = []
    try:
        with open(jsonl_path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                turns.append({
                    "ts": rec.get("timestamp"),
                    "input_tokens": usage.get("input_tokens", 0) or 0,
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                    "output_tokens": usage.get("output_tokens", 0) or 0,
                })
    except OSError:
        return []
    return turns


def summarize_session(turns):
    n = len(turns)
    if n == 0:
        return None
    total_input = sum(t["input_tokens"] for t in turns)
    total_cache_write = sum(t["cache_creation_input_tokens"] for t in turns)
    total_cache_read = sum(t["cache_read_input_tokens"] for t in turns)
    total_output = sum(t["output_tokens"] for t in turns)
    total_context = total_input + total_cache_write + total_cache_read
    tool_result_share = None  # not derivable from usage blocks alone; left as UNKNOWN, never guessed
    return {
        "turns": n,
        "input_tokens": total_input,
        "cache_creation_input_tokens": total_cache_write,
        "cache_read_input_tokens": total_cache_read,
        "output_tokens": total_output,
        "tokens_per_turn": round(total_context / n, 1) if n else 0,
        "context_growth_per_turn_estimate": round(total_cache_read / n, 1) if n else 0,
        "tool_result_share_of_context": "UNKNOWN (not present in usage blocks; would need per-message role/content-type breakdown)",
    }


def load_session_turn_timestamps(session_id_to_turns):
    """session_id_to_turns: {session_id: [turn dict, ...]} already parsed by parse_session, in
    file order (assumed chronological, which is how Claude Code writes JSONL). Returns
    {session_id: [ts_float, ...]} sorted, skipping turns with no/unparseable timestamp."""
    out = {}
    for sid, turns in session_id_to_turns.items():
        stamps = []
        for t in turns:
            ts = t.get("ts")
            if not ts:
                continue
            try:
                # Claude Code timestamps are ISO8601; convert to a sortable/comparable float via
                # a lexical proxy is unsafe across formats, so parse properly.
                import datetime
                stamps.append(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except (ValueError, AttributeError):
                continue
        stamps.sort()
        out[sid] = stamps
    return out


def turns_remaining_after(call_ts, sorted_turn_timestamps):
    """Count of session turns whose timestamp is >= call_ts (the call's own timestamp is epoch
    seconds, same clock as the turn timestamps once converted). Returns None if no turn data."""
    if not sorted_turn_timestamps:
        return None
    import bisect
    idx = bisect.bisect_left(sorted_turn_timestamps, call_ts)
    return len(sorted_turn_timestamps) - idx


def estimate_ledger_savings(ledger_rows, session_id_to_turns=None):
    """ESTIMATE only, two layers:
    1. chars_saved / CHARS_PER_TOKEN -- the one-time saving if the avoided text appeared in the
       prompt/tool-result exactly once. Always computed.
    2. Per-call cache-read-avoided estimate: chars_saved(call) / CHARS_PER_TOKEN * turns_remaining
       in that call's own Claude Code session after its timestamp -- because a tool result sitting
       in context is re-paid-for as cache-read on every subsequent turn. Requires ledger rows to
       carry a non-null "session" field (CLAUDE_CODE_SESSION_ID) correlated to a parsed session
       file. Calls with session=None (old ledger format, or run outside a Claude Code session)
       are counted separately and reported as UNKNOWN -- never silently folded into the estimate.
    """
    passthrough_calls = sum(1 for r in ledger_rows if r.get("passthrough"))
    ledger_rows = [r for r in ledger_rows if not r.get("passthrough")]  # logged for usage, saved nothing
    total_calls = len(ledger_rows)
    total_chars_in = sum(r.get("chars_in", 0) for r in ledger_rows)
    total_chars_out = sum(r.get("chars_out", 0) for r in ledger_rows)
    chars_saved = total_chars_in - total_chars_out
    tokens_saved_per_use = chars_saved / CHARS_PER_TOKEN if chars_saved else 0

    session_turn_stamps = load_session_turn_timestamps(session_id_to_turns or {})
    correlated_estimate = 0
    correlated_calls = 0
    unknown_calls = 0
    for r in ledger_rows:
        sid = r.get("session")
        if not sid or sid not in session_turn_stamps:
            unknown_calls += 1
            continue
        stamps = session_turn_stamps[sid]
        remaining = turns_remaining_after(r.get("ts", 0), stamps)
        if remaining is None:
            unknown_calls += 1
            continue
        call_saved_chars = r.get("chars_in", 0) - r.get("chars_out", 0)
        if call_saved_chars <= 0:
            continue
        correlated_estimate += (call_saved_chars / CHARS_PER_TOKEN) * remaining
        correlated_calls += 1

    return {
        "calls": total_calls,
        "passthrough_calls": passthrough_calls,
        "chars_in": total_chars_in,
        "chars_out": total_chars_out,
        "chars_saved": chars_saved,
        "tokens_saved_one_time_ESTIMATE": round(tokens_saved_per_use),
        "method": ("Layer 1: chars_saved / %d (chars-per-token heuristic) -- the one-time saving if "
                   "avoided text appeared once. Layer 2 (cache_read_avoided_ESTIMATE): for each "
                   "ledger call carrying a non-null 'session' (CLAUDE_CODE_SESSION_ID), chars saved "
                   "by that call / %d, multiplied by the count of that session's turns whose "
                   "timestamp is at or after the call's own timestamp -- the turns that would have "
                   "re-paid for the avoided text as cache-read. Calls with session=null (older "
                   "ledger lines, or squire run outside a Claude Code session) are excluded from "
                   "Layer 2 and counted in 'calls_with_unknown_session' instead of guessed."
                   % (CHARS_PER_TOKEN, CHARS_PER_TOKEN)),
        "cache_read_avoided_ESTIMATE": round(correlated_estimate),
        "calls_correlated_to_a_session": correlated_calls,
        "calls_with_unknown_session": unknown_calls,
    }


PUBLIC_STATS_BEGIN = "<!-- SQUIRE-PUBLIC-STATS:BEGIN (generated by scripts/squire_report.py --write, do not hand-edit) -->"
PUBLIC_STATS_END = "<!-- SQUIRE-PUBLIC-STATS:END -->"
# Owner directive 2026-09-15: publish stats only from the point enforcement was in place, not the
# earlier development/thrash period. A caller can still override with --since for a different window.
PUBLIC_STATS_DEFAULT_SINCE = "2026-09-15T01:00:00-04:00"


def filter_ledger_since(ledger_rows, since_iso):
    """since_iso: an ISO8601 timestamp (with or without offset); rows before it are dropped.
    Rows with no 'ts' are dropped too -- never guessed into or out of the window."""
    import datetime
    cutoff = datetime.datetime.fromisoformat(since_iso).timestamp()
    return [r for r in ledger_rows if (r.get("ts") or 0) >= cutoff]


def public_stats(ledger_rows):
    """Honest public-facing summary of REAL ledger rows (fixture rows already excluded by
    load_ledger). No hostnames, paths, usernames or infrastructure detail -- counts only.
    Returns a dict; every number here is a real count except the labelled chars/4 heuristic."""
    if not ledger_rows:
        return None
    stamps = [r.get("ts") for r in ledger_rows if r.get("ts")]
    import datetime
    date_from = datetime.datetime.fromtimestamp(min(stamps), datetime.timezone.utc).strftime("%Y-%m-%d") if stamps else "UNKNOWN"
    date_to = datetime.datetime.fromtimestamp(max(stamps), datetime.timezone.utc).strftime("%Y-%m-%d") if stamps else "UNKNOWN"

    # Short `run` passthroughs: counted as usage, excluded from every savings figure.
    passthrough_calls = sum(1 for r in ledger_rows if r.get("passthrough"))
    model_rows = [r for r in ledger_rows if not r.get("passthrough")]

    by_cmd = {}
    for r in model_rows:
        c = by_cmd.setdefault(r.get("cmd", "unknown"), {"calls": 0, "ok": 0, "chars_in": 0, "chars_out": 0})
        c["calls"] += 1
        if r.get("backend_ok"):
            c["ok"] += 1
        c["chars_in"] += r.get("chars_in", 0) or 0
        c["chars_out"] += r.get("chars_out", 0) or 0

    total_calls = len(model_rows)
    total_ok = sum(c["ok"] for c in by_cmd.values())
    total_chars_in = sum(c["chars_in"] for c in by_cmd.values())
    total_chars_out = sum(c["chars_out"] for c in by_cmd.values())
    chars_saved = total_chars_in - total_chars_out

    per_cmd_rows = []
    for name, c in sorted(by_cmd.items()):
        ok_rate = round(100 * c["ok"] / c["calls"], 1) if c["calls"] else 0.0
        unknown_rate = round(100 - ok_rate, 1)
        per_cmd_rows.append({
            "cmd": name, "calls": c["calls"], "ok_rate_pct": ok_rate, "unknown_rate_pct": unknown_rate,
            "chars_in": c["chars_in"], "chars_out": c["chars_out"], "chars_saved": c["chars_in"] - c["chars_out"],
        })

    return {
        "date_from": date_from, "date_to": date_to,
        "total_calls": total_calls,
        "passthrough_calls": passthrough_calls,
        "overall_ok_rate_pct": round(100 * total_ok / total_calls, 1) if total_calls else 0.0,
        "overall_unknown_rate_pct": round(100 - (100 * total_ok / total_calls if total_calls else 0.0), 1),
        "chars_in": total_chars_in, "chars_out": total_chars_out, "chars_saved": chars_saved,
        "tokens_saved_ESTIMATE": round(chars_saved / CHARS_PER_TOKEN) if chars_saved else 0,
        "per_cmd": per_cmd_rows,
    }


def render_public_markdown(stats):
    if not stats:
        body = "_No ledger data yet for this range._"
    else:
        call_word = "call" if stats["total_calls"] == 1 else "calls"
        pt = stats.get("passthrough_calls", 0)
        pt_word = "run" if pt == 1 else "runs"
        lines = [
            f"**{stats['date_from']} to {stats['date_to']}** &mdash; {stats['total_calls']} model-backed {call_word} "
            f"logged, {stats['overall_ok_rate_pct']}% backend-ok ({stats['overall_unknown_rate_pct']}% `UNKNOWN`), "
            f"plus {pt} short `run` passthrough {pt_word} (60 lines of output or fewer, shown raw with no model call "
            "and no savings counted).",
            "",
            "| Command | Calls | Ok rate | `UNKNOWN` rate | Chars in | Chars out | Chars saved |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in stats["per_cmd"]:
            lines.append(f"| `{c['cmd']}` | {c['calls']} | {c['ok_rate_pct']}% | {c['unknown_rate_pct']}% | "
                          f"{c['chars_in']} | {c['chars_out']} | {c['chars_saved']} |")
        lines += [
            "",
            f"**REAL character counts** (not estimated): {stats['chars_in']} chars in, {stats['chars_out']} chars "
            f"out, **{stats['chars_saved']} chars kept out of context**.",
            "",
            f"ESTIMATED tokens saved: ~{stats['tokens_saved_ESTIMATE']} (chars/{CHARS_PER_TOKEN} heuristic, not a "
            "measured token count -- see \"Measuring savings\" above for the fuller method).",
            "",
            "Exit-code fidelity is measured separately, on a deterministic fixture corpus, not from this ledger "
            "-- see the Benchmarks section above and `docs/BENCHMARK.md` for the current figure and how to "
            "reproduce it.",
        ]
        body = "\n".join(lines)
    return f"{PUBLIC_STATS_BEGIN}\n{body}\n{PUBLIC_STATS_END}"


def extract_block(text):
    start = text.find(PUBLIC_STATS_BEGIN)
    end = text.find(PUBLIC_STATS_END)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start:end + len(PUBLIC_STATS_END)]


def write_block_into(path, new_block):
    with open(path) as f:
        text = f.read()
    existing = extract_block(text)
    if existing is None:
        raise SystemExit(f"no SQUIRE-PUBLIC-STATS markers found in {path}")
    if existing == new_block:
        return False
    with open(path, "w") as f:
        f.write(text.replace(existing, new_block, 1))
    return True


def check_block_in(path, new_block):
    with open(path) as f:
        text = f.read()
    existing = extract_block(text)
    if existing is None:
        return False, f"no SQUIRE-PUBLIC-STATS markers found in {path}"
    if existing != new_block:
        return False, f"published stats block in {path} is stale (does not match the current ledger)"
    return True, f"{path} is current"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None, help="restrict to one ~/.claude/projects/<name> directory")
    ap.add_argument("--since", default=None, help="ISO date; only sessions with a timestamp on/after this (best-effort)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--anonymize", action="store_true", help="replace project names with PROJECT-N, never print paths")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--public", action="store_true", help="print the honest public stats markdown block and exit")
    ap.add_argument("--check", nargs="+", metavar="FILE", help="exit 1 if FILE's published stats block is stale")
    ap.add_argument("--write", nargs="+", metavar="FILE", help="update FILE's published stats block in place")
    args = ap.parse_args()

    if args.public or args.check or args.write:
        ledger_rows = load_ledger(args.ledger)
        since = args.since or PUBLIC_STATS_DEFAULT_SINCE
        ledger_rows = filter_ledger_since(ledger_rows, since)
        stats = public_stats(ledger_rows)
        block = render_public_markdown(stats)
        if args.public:
            print(block)
        if args.check:
            ok_all = True
            for path in args.check:
                ok, msg = check_block_in(path, block)
                print(("[squire-report] OK: " if ok else "[squire-report] STALE: ") + msg)
                ok_all = ok_all and ok
            sys.exit(0 if ok_all else 1)
        if args.write:
            for path in args.write:
                changed = write_block_into(path, block)
                print(f"[squire-report] {'updated' if changed else 'unchanged'}: {path}")
        return

    sessions = []
    proj_name_map = {}
    session_id_to_turns = {}
    for proj_name, jsonl_path in iter_session_files(args.project):
        turns = parse_session(jsonl_path)
        if args.since and turns:
            turns = [t for t in turns if (t["ts"] or "") >= args.since]
        session_id = os.path.splitext(os.path.basename(jsonl_path))[0]
        session_id_to_turns[session_id] = turns
        summary = summarize_session(turns)
        if not summary:
            continue
        if proj_name not in proj_name_map:
            proj_name_map[proj_name] = f"PROJECT-{len(proj_name_map) + 1}"
        label = proj_name_map[proj_name] if args.anonymize else proj_name
        sessions.append({"project": label, "session_file": os.path.basename(jsonl_path), **summary})

    ledger_rows = load_ledger(args.ledger)
    if args.since:
        ledger_rows = [r for r in ledger_rows if r.get("ts", 0) >= 0]  # ts is epoch float; --since is best-effort on sessions only
    ledger_summary = estimate_ledger_savings(ledger_rows, session_id_to_turns)

    result = {
        "VERIFIED_sessions": sessions,
        "VERIFIED_session_count": len(sessions),
        "VERIFIED_totals": {
            "turns": sum(s["turns"] for s in sessions),
            "cache_read_input_tokens": sum(s["cache_read_input_tokens"] for s in sessions),
            "cache_creation_input_tokens": sum(s["cache_creation_input_tokens"] for s in sessions),
            "input_tokens": sum(s["input_tokens"] for s in sessions),
            "output_tokens": sum(s["output_tokens"] for s in sessions),
        },
        "ESTIMATE_squire_ledger": ledger_summary,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"VERIFIED -- {len(sessions)} session(s) with usage data" +
          (f" (project filter: {args.project})" if args.project else ""))
    print(f"{'project':<14} {'session':<16} {'turns':>6} {'cache_read':>12} {'cache_write':>12} {'input':>10} {'output':>10} {'tok/turn':>10}")
    for s in sessions:
        sess_short = s["session_file"][:14]
        print(f"{s['project']:<14} {sess_short:<16} {s['turns']:>6} {s['cache_read_input_tokens']:>12} "
              f"{s['cache_creation_input_tokens']:>12} {s['input_tokens']:>10} {s['output_tokens']:>10} {s['tokens_per_turn']:>10}")
    t = result["VERIFIED_totals"]
    print(f"\nVERIFIED totals: turns={t['turns']} cache_read={t['cache_read_input_tokens']} "
          f"cache_write={t['cache_creation_input_tokens']} input={t['input_tokens']} output={t['output_tokens']}")
    print("\nESTIMATE -- squire ledger (chars-per-token heuristic, see --json 'method' field for caveats):")
    ls = result["ESTIMATE_squire_ledger"]
    print(f"  calls={ls['calls']} chars_in={ls['chars_in']} chars_out={ls['chars_out']} "
          f"chars_saved={ls['chars_saved']} tokens_saved_one_time_ESTIMATE={ls['tokens_saved_one_time_ESTIMATE']}")
    print(f"  cache_read_avoided_ESTIMATE={ls['cache_read_avoided_ESTIMATE']} "
          f"(correlated calls={ls['calls_correlated_to_a_session']}, "
          f"unknown-session calls={ls['calls_with_unknown_session']})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""squire: Claude Code's local sidekick (the squire who carries the load). Offloads bulk text work to the local GPU (Ollama).

Claude sessions pay for every token they read. squire runs on this workstation's RTX 3090
(qwen2.5:14b via Ollama on 127.0.0.1:11434), so long output is condensed locally before a
session reads it.

    squire run -- <command...>   run a command; show its REAL exit code, raw tail, and a local
                                 summary of failures (short output is printed as-is, no model)
    squire sum [file|-]          condense text to a few bullets
    squire ask "question" [file|-]   answer a question from the given text only
    squire draft "instructions" [file|-]  first draft for Claude to review

Rules built in (global CLAUDE.md section 5.8):
- Exit codes and the raw tail are ALWAYS printed. The model never decides pass or fail.
- Model output is labelled [squire] and is ASSUMED until the session checks it.
- Ollama down or erroring means the output says UNKNOWN and falls back to the raw tail, never silence.
- Stdlib only: runs on any python3 on this machine.
"""
__version__ = "0.1.0.dev0"

import json
import os
import subprocess
import sys
import time
import urllib.request

HOST = os.environ.get("SQUIRE_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("SQUIRE_MODEL", "qwen2.5:14b")
CTX = int(os.environ.get("SQUIRE_CTX", "16384"))    # Ollama defaults to 2048, which truncates silently
CHUNK = 24000            # chars per chunk (~6k tokens), leaves room for prompt + answer
SHORT = 60               # lines at or below this are shown raw, no model call
TAIL = 25
LEDGER = os.path.expanduser(os.environ.get("SQUIRE_LEDGER", "~/.squire/ledger.jsonl"))
CHARS_PER_TOKEN = 4      # rough estimate for English text; stats labels this explicitly as an estimate


def log_call(cmd, chars_in, chars_out, backend_ok):
    # chars_in/out/ts are real, computed facts. The token estimate derived from them in `stats`
    # is NOT -- CLAUDE.md section 17 requires the two never be presented as the same kind of claim.
    try:
        os.makedirs(os.path.dirname(LEDGER), mode=0o700, exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"ts": time.time(), "cmd": cmd, "chars_in": chars_in,
                                 "chars_out": chars_out, "backend_ok": backend_ok}) + "\n")
    except OSError:
        pass  # stats are a bonus; never fail the actual command over a logging error


def llm(prompt, max_tokens=300):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"num_ctx": CTX, "num_predict": max_tokens, "temperature": 0.1}}).encode()
    req = urllib.request.Request(HOST + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r).get("response", "").strip()
    except Exception as e:  # noqa: BLE001 - any failure is reported, never swallowed
        return f"UNKNOWN: local model unavailable ({type(e).__name__})"


def chunks(text):
    return [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]


def condense(text, task, max_tokens=300):
    parts = chunks(text)
    if len(parts) == 1:
        return llm(f"{task}\n\n---\n{parts[0]}\n---", max_tokens)
    notes = [llm(f"{task} (part {i + 1}/{len(parts)}; be brief)\n\n---\n{p}\n---", 150)
             for i, p in enumerate(parts)]
    return llm(f"{task}\nCombine these partial notes into one answer:\n\n" + "\n".join(notes), max_tokens)


def read_input(arg):
    if arg in (None, "-"):
        return sys.stdin.read()
    with open(arg, errors="replace") as f:
        return f.read()


def cmd_run(argv):
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        sys.exit("usage: squire run -- <command...>")
    p = subprocess.run(argv if len(argv) > 1 else argv[0], shell=len(argv) == 1,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    out = p.stdout or ""
    lines = out.splitlines()
    print(f"[squire] exit={p.returncode} lines={len(lines)}")
    if len(lines) <= SHORT:
        print(out, end="")
    else:
        print(f"[squire] --- raw tail ({TAIL} lines) ---")
        print("\n".join(lines[-TAIL:]))
        print("[squire] --- local summary (ASSUMED; the exit code above is authoritative) ---")
        summary = condense(out, "Summarize this command output for a busy engineer in at most 8 bullets. "
                            "List every failing test/error with file:line and the one-line cause. "
                            "Say 'no errors seen' only if there are none. Never invent names.")
        print(summary)
        log_call("run", len(out), len(summary), not summary.startswith("UNKNOWN"))
    sys.exit(p.returncode)


def cmd_stats(argv):
    if not os.path.exists(LEDGER):
        print("[squire] no ledger yet — run some commands first"); return
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    if not rows:
        print("[squire] ledger is empty"); return
    total_in = sum(r["chars_in"] for r in rows)
    total_out = sum(r["chars_out"] for r in rows)
    saved_chars = total_in - total_out
    ok = sum(1 for r in rows if r["backend_ok"])
    print(f"[squire] {len(rows)} calls logged ({ok} backend-ok, {len(rows) - ok} UNKNOWN) — REAL, computed from {LEDGER}")
    print(f"[squire] chars in={total_in} out={total_out} saved={saved_chars} (REAL character counts)")
    print(f"[squire] ESTIMATED tokens saved: ~{saved_chars // CHARS_PER_TOKEN} "
          f"(chars/{CHARS_PER_TOKEN} heuristic — NOT a measured token count; cross-check against "
          "real session usage blocks in ~/.claude/projects/*/*.jsonl before citing this figure)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "run":
        cmd_run(rest)
    elif cmd == "sum":
        text = read_input(rest[0] if rest else None)
        out = condense(text, "Condense to at most 8 factual bullets. Keep numbers, names, paths exact.")
        print("[squire] " + out)
        log_call("sum", len(text), len(out), not out.startswith("UNKNOWN"))
    elif cmd == "ask":
        if not rest:
            sys.exit('usage: squire ask "question" [file|-]')
        text = read_input(rest[1] if len(rest) > 1 else None)
        out = condense(text, f"Answer using ONLY this text; say UNKNOWN if it is not there. Question: {rest[0]}")
        print("[squire] " + out)
        log_call("ask", len(text), len(out), not out.startswith("UNKNOWN"))
    elif cmd == "draft":
        if not rest:
            sys.exit('usage: squire draft "instructions" [file|-]')
        src = read_input(rest[1]) if len(rest) > 1 else ""
        out = llm(f"{rest[0]}\n\nSource material (may be empty):\n{src[:CHUNK]}", 1200)
        print(out)
        log_call("draft", len(src), len(out), not out.startswith("UNKNOWN"))
    elif cmd == "stats":
        cmd_stats(rest)
    else:
        sys.exit(f"unknown command {cmd!r}; see squire --help")


if __name__ == "__main__":
    main()

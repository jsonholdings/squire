#!/usr/bin/env python3
"""squire: Claude Code's local sidekick (the squire who carries the load). Offloads bulk text work to local hardware.

Claude sessions pay for every token they read, and pay again on every later turn. squire runs a
model on your own GPU/CPU (Ollama, or any OpenAI-compatible localhost server such as llama.cpp or
vLLM), so long output is condensed locally before a session reads it.

    squire run -- <command...>   run a command; show its REAL exit code, raw tail, and a local
                                 summary of failures (short output is printed as-is, no model)
    squire sum [file|-]          condense text to a few bullets
    squire ask "question" [file|-]   answer a question from the given text only
    squire draft "instructions" [file|-]  first draft for Claude to review
    squire diff [--staged] | squire diff <ref1> <ref2>   condense a large git diff
    squire grep "query" [path] [--top N] [--reindex]   semantic search over a repo (local embeddings)
    squire triage <file>         order a HANDOFF-INBOX/BACKLOG by real age, with a guessed impact line
    squire stats                 real chars in/out logged, plus a labelled token estimate
    squire doctor                check backend, models and GPU; exit 0 ready, 2 UNKNOWN

Any command accepts --json to emit one JSON object instead of formatted text.

Rules built in:
- Exit codes and the raw tail are ALWAYS printed. The model never decides pass or fail.
- Model output is labelled [squire] and is ASSUMED until the session checks it.
- Backend down or erroring means the output says UNKNOWN and falls back to raw text, never silence.
- Backends must be localhost (extra hosts only via SQUIRE_ALLOW_HOSTS). No telemetry.
- Secret-shaped strings are redacted before any text is sent to the backend.
- Stdlib only.
"""
__version__ = "0.2.0"

import datetime as _dt
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BACKEND = os.environ.get("SQUIRE_BACKEND", "ollama")
HOST = os.environ.get("SQUIRE_OLLAMA", "http://127.0.0.1:11434")
OPENAI_BASE = os.environ.get("SQUIRE_OPENAI_BASE", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("SQUIRE_MODEL", "qwen2.5:14b")
EMBED_MODEL = os.environ.get("SQUIRE_EMBED_MODEL", "nomic-embed-text")
CTX = int(os.environ.get("SQUIRE_CTX", "16384"))    # Ollama defaults to 2048, which truncates silently
KEEP_ALIVE = os.environ.get("SQUIRE_KEEP_ALIVE", "30m")
CHUNK = 24000            # chars per chunk (~6k tokens), leaves room for prompt + answer
SHORT = 60               # lines at or below this are shown raw, no model call
TAIL = 25
LEDGER = os.path.expanduser(os.environ.get("SQUIRE_LEDGER", "~/.squire/ledger.jsonl"))
CHARS_PER_TOKEN = 4      # rough estimate for English text; stats labels this explicitly as an estimate
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Model size tiers for SQUIRE_MODEL=auto: (min free VRAM MiB, preferred model name prefixes).
AUTO_TIERS = [(22000, ("qwen2.5:32b",)), (11000, ("qwen2.5:14b",)), (6000, ("qwen2.5:7b",)), (0, ("qwen2.5:3b", "qwen2.5:1.5b"))]

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"),
]


class BackendError(Exception):
    pass


def redact(text):
    for pat in SECRET_PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text


def check_local(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    allowed = LOCAL_HOSTS | {h.strip().lower() for h in os.environ.get("SQUIRE_ALLOW_HOSTS", "").split(",") if h.strip()}
    if host not in allowed:
        raise BackendError(f"backend host {host!r} is not localhost; set SQUIRE_ALLOW_HOSTS to allow it")


def log_call(cmd, chars_in, chars_out, backend_ok):
    # chars_in/out/ts are real, computed facts. The token estimate derived from them in `stats`
    # is NOT -- the two must never be presented as the same kind of claim.
    try:
        os.makedirs(os.path.dirname(LEDGER), mode=0o700, exist_ok=True)
        with open(LEDGER, "a") as f:
            # session lets scripts/squire_report.py multiply chars avoided by that session's
            # remaining turns (each later turn re-reads context) instead of guessing.
            f.write(json.dumps({"ts": time.time(), "cmd": cmd, "chars_in": chars_in,
                                "chars_out": chars_out, "backend_ok": backend_ok,
                                "session": os.environ.get("CLAUDE_CODE_SESSION_ID")}) + "\n")
    except OSError:
        pass  # stats are a bonus; never fail the actual command over a logging error


def _post(url, payload, timeout=300):
    check_local(url)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(url, timeout=10):
    check_local(url)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def installed_models():
    if BACKEND == "openai":
        return [m["id"] for m in _get(OPENAI_BASE.rstrip("/") + "/models").get("data", [])]
    return [m["name"] for m in _get(HOST + "/api/tags").get("models", [])]


def gpu_free_mib():
    for cmd in (["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                ["flatpak-spawn", "--host", "nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return max(int(x) for x in out.stdout.split())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return None


def pick_model(installed, free_mib):
    # Largest tier that fits free VRAM AND is installed; otherwise the largest installed chat model.
    for min_mib, names in AUTO_TIERS:
        if free_mib is not None and free_mib < min_mib:
            continue
        for name in names:
            if any(m == name or m.startswith(name + "-") for m in installed):
                return name
    chat = [m for m in installed if "embed" not in m]
    return chat[0] if chat else None


_resolved_model = None


def model():
    global _resolved_model
    if MODEL != "auto":
        return MODEL
    if _resolved_model is None:
        _resolved_model = pick_model(installed_models(), gpu_free_mib()) or "qwen2.5:14b"
    return _resolved_model


def llm(prompt, max_tokens=300):
    prompt = redact(prompt)
    try:
        if BACKEND == "openai":
            data = _post(OPENAI_BASE.rstrip("/") + "/chat/completions",
                         {"model": model(), "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": max_tokens, "temperature": 0.1})
            return data["choices"][0]["message"]["content"].strip()
        # keep_alive holds the model in VRAM between calls: a cold load (~75s for 14B) inside a
        # hook-wrapped test run can push the command past the caller's timeout.
        data = _post(HOST + "/api/generate", {"model": model(), "prompt": prompt, "stream": False,
                     "keep_alive": KEEP_ALIVE,
                     "options": {"num_ctx": CTX, "num_predict": max_tokens, "temperature": 0.1}})
        return data.get("response", "").strip()
    except Exception as e:  # noqa: BLE001 - any failure is reported, never swallowed
        return f"UNKNOWN: local model unavailable ({type(e).__name__}: {e})"[:300]


def embed(texts, kind="document"):
    """Embed a batch; raises BackendError on any failure so callers report UNKNOWN."""
    texts = [redact(t) for t in texts]
    if "nomic" in EMBED_MODEL:
        # nomic-embed-text is trained with task prefixes; without them ranking is noticeably worse.
        texts = [f"search_{kind}: {t}" for t in texts]
    try:
        if BACKEND == "openai":
            data = _post(OPENAI_BASE.rstrip("/") + "/embeddings", {"model": EMBED_MODEL, "input": texts}, 600)
            return [d["embedding"] for d in data["data"]]
        data = _post(HOST + "/api/embed", {"model": EMBED_MODEL, "input": texts}, 600)
        return data["embeddings"]
    except BackendError:
        raise
    except Exception as e:  # noqa: BLE001
        raise BackendError(f"{type(e).__name__}: {e}") from e


def chunks(text):
    return [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]


def condense(text, task, max_tokens=300):
    parts = chunks(text)
    if len(parts) == 1:
        return llm(f"{task}\n\n---\n{parts[0]}\n---", max_tokens)
    notes = [llm(f"{task} (part {i + 1}/{len(parts)}; be brief)\n\n---\n{p}\n---", 150)
             for i, p in enumerate(parts)]
    if any(n.startswith("UNKNOWN") for n in notes):
        return next(n for n in notes if n.startswith("UNKNOWN"))
    return llm(f"{task}\nCombine these partial notes into one answer:\n\n" + "\n".join(notes), max_tokens)


def condense_verified(text, task, max_tokens=300):
    """condense() plus a second local pass checking the summary against the source.
    Used where an inaccurate summary would mislead a real decision (test failures, diffs). A
    disagreement is appended visibly, never hidden or silently retried. The check is itself
    ASSUMED: a quality signal, not a correctness guarantee."""
    summary = condense(text, task, max_tokens)
    if summary.startswith("UNKNOWN"):
        return summary, None
    check = llm(
        "SOURCE (truncated) and a SUMMARY of it follow. Reply with exactly 'OK' if the summary "
        "invents nothing not supported by the source and omits no failure/error the source contains. "
        "Otherwise reply with one short sentence naming the specific inaccuracy.\n\n"
        f"SOURCE:\n{text[-CHUNK:]}\n\nSUMMARY:\n{summary}", 60)
    if check.startswith("UNKNOWN"):
        return summary, None
    flag = None if check.strip().rstrip(".").upper() == "OK" else check.strip()
    return summary, flag


def read_input(arg):
    if arg in (None, "-"):
        return sys.stdin.read()
    with open(arg, errors="replace") as f:
        return f.read()


def pop_flag(argv, flag):
    return flag in argv, [a for a in argv if a != flag]


def pop_opt(argv, opt, default):
    if opt in argv:
        i = argv.index(opt)
        if i + 1 < len(argv):
            return argv[i + 1], argv[:i] + argv[i + 2:]
    return default, argv


def emit(cmd, exit_code, raw_tail, summary, backend_ok, json_mode, verify_flag=None):
    # Single point of output for run/diff so --json and human text can never drift apart.
    if json_mode:
        print(json.dumps({"cmd": cmd, "exit_code": exit_code, "raw_tail": raw_tail,
                          "summary": summary, "assumed": summary is not None, "backend_ok": backend_ok,
                          "verify_flag": verify_flag}))
        return
    if raw_tail is not None:
        print(raw_tail, end="" if raw_tail.endswith("\n") else "\n")
    if summary is not None:
        print(f"[squire] --- local summary (ASSUMED{'' if backend_ok else '; backend UNKNOWN'}) ---")
        print(summary)
        if verify_flag:
            print(f"[squire] --- verify flag (ASSUMED, may itself be wrong): {verify_flag} ---")


def cmd_run(argv):
    # Only a --json BEFORE the "--" separator belongs to squire; after it, it belongs to the command.
    if "--" in argv:
        i = argv.index("--")
        json_mode, argv = "--json" in argv[:i], argv[i + 1:]
    else:
        json_mode, argv = pop_flag(argv, "--json")
    if not argv:
        sys.exit("usage: squire run [--json] -- <command...>")
    p = subprocess.run(argv if len(argv) > 1 else argv[0], shell=len(argv) == 1,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    out = p.stdout or ""
    lines = out.splitlines()
    if not json_mode:
        print(f"[squire] exit={p.returncode} lines={len(lines)}")
    if len(lines) <= SHORT:
        emit("run", p.returncode, out, None, True, json_mode)
    else:
        if not json_mode:
            print(f"[squire] --- raw tail ({TAIL} lines) ---")
        summary, flag = condense_verified(out, "Summarize this command output for a busy engineer in at most 8 bullets. "
                                          "List every failing test/error with file:line and the one-line cause. "
                                          "Say 'no errors seen' only if there are none. Never invent names.")
        ok = not summary.startswith("UNKNOWN")
        log_call("run", len(out), len(summary), ok)
        emit("run", p.returncode, "\n".join(lines[-TAIL:]), summary, ok, json_mode, flag)
    sys.exit(p.returncode)


def cmd_diff(argv):
    json_mode, argv = pop_flag(argv, "--json")
    if argv and argv[0] == "--staged":
        gitcmd = ["git", "diff", "--staged"]
    elif len(argv) >= 2:
        gitcmd = ["git", "diff", argv[0], argv[1]]
    else:
        gitcmd = ["git", "diff"]
    p = subprocess.run(gitcmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    diff_text = p.stdout or ""
    if p.returncode != 0:
        emit("diff", p.returncode, diff_text, None, True, json_mode)
        sys.exit(p.returncode)
    stat = subprocess.run(gitcmd + ["--stat"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace").stdout.strip()
    if not diff_text.strip():
        emit("diff", 0, "(no diff)", None, True, json_mode)
        return
    summary, flag = condense_verified(diff_text, "Summarize this git diff by file/module. Separate logic changes "
                                      "from formatting/rename-only changes. Be factual, keep exact file paths and names.")
    ok = not summary.startswith("UNKNOWN")
    log_call("diff", len(diff_text), len(summary), ok)
    if not json_mode:
        print(f"[squire] {stat.splitlines()[-1] if stat else '(no stat)'}")
    emit("diff", 0, stat, summary, ok, json_mode, flag)


# ---------------------------------------------------------------- grep

GREP_WINDOW, GREP_OVERLAP, GREP_BATCH = 40, 10, 32
GREP_MAX_BYTES = 1_000_000
GREP_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".squire-cache", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache"}
GREP_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".pyc", ".so", ".bin", ".woff", ".woff2", ".ico", ".mp3", ".mp4", ".sqlite", ".db", ".pyz", ".lock"}


def repo_root(path):
    p = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def list_files(root, in_git):
    if in_git:
        p = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, text=True)
        rels = [r for r in p.stdout.split("\0") if r]
    else:
        rels = []
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in GREP_SKIP_DIRS and not x.startswith(".")]
            rels += [os.path.relpath(os.path.join(d, f), root) for f in files]
    out = []
    for rel in rels:
        if any(part in GREP_SKIP_DIRS for part in rel.split(os.sep)):
            continue
        if os.path.splitext(rel)[1].lower() in GREP_SKIP_EXT:
            continue
        full = os.path.join(root, rel)
        try:
            st = os.stat(full)
        except OSError:
            continue
        if st.st_size == 0 or st.st_size > GREP_MAX_BYTES:
            continue
        out.append((rel, st.st_mtime, st.st_size))
    return out


def file_windows(text):
    lines = text.splitlines()
    step = GREP_WINDOW - GREP_OVERLAP
    return [(i + 1, min(i + GREP_WINDOW, len(lines)), "\n".join(lines[i:i + GREP_WINDOW]))
            for i in range(0, max(len(lines), 1), step) if "\n".join(lines[i:i + GREP_WINDOW]).strip()]


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 5) for x in v]


def exclude_cache(root):
    # Keep the cache out of git without editing a tracked .gitignore.
    excl = os.path.join(root, ".git", "info", "exclude")
    try:
        existing = open(excl).read() if os.path.exists(excl) else ""
        if ".squire-cache/" not in existing:
            os.makedirs(os.path.dirname(excl), exist_ok=True)
            with open(excl, "a") as f:
                f.write(("\n" if existing and not existing.endswith("\n") else "") + ".squire-cache/\n")
    except OSError:
        pass


def cmd_grep(argv):
    json_mode, argv = pop_flag(argv, "--json")
    reindex, argv = pop_flag(argv, "--reindex")
    top, argv = pop_opt(argv, "--top", "15")
    if not argv:
        sys.exit('usage: squire grep "query" [path] [--top N] [--reindex] [--json]')
    query, path = argv[0], (argv[1] if len(argv) > 1 else ".")
    path = os.path.abspath(path)
    root = repo_root(path) if os.path.isdir(path) else None
    in_git = root is not None
    root = root or path
    files = list_files(root, in_git)
    if path != root:
        prefix = os.path.relpath(path, root) + os.sep
        files = [f for f in files if (f[0] + os.sep).startswith(prefix) or f[0] == prefix.rstrip(os.sep)]
    cache_dir = os.path.join(root, ".squire-cache")
    cache_file = os.path.join(cache_dir, f"grep-{re.sub(r'[^A-Za-z0-9._-]', '_', EMBED_MODEL)}.json")
    cache = {}
    if not reindex and os.path.exists(cache_file):
        try:
            cache = json.load(open(cache_file))
        except (OSError, ValueError):
            cache = {}
    header = {"root": root, "files_indexed": len(files), "git": in_git}
    try:
        todo = []
        for rel, mtime, size in files:
            ent = cache.get(rel)
            if ent and ent["mtime"] == mtime and ent["size"] == size:
                continue
            try:
                text = open(os.path.join(root, rel), errors="replace").read()
            except OSError:
                continue
            if "\0" in text[:4096]:
                cache[rel] = {"mtime": mtime, "size": size, "chunks": []}
                continue
            cache[rel] = {"mtime": mtime, "size": size, "chunks": []}
            todo += [(rel, s, e, f"{rel}\n{body}") for s, e, body in file_windows(text)]
        for i in range(0, len(todo), GREP_BATCH):
            batch = todo[i:i + GREP_BATCH]
            vecs = embed([t[3][:6000] for t in batch])
            for (rel, s, e, _), v in zip(batch, vecs):
                cache[rel]["chunks"].append({"s": s, "e": e, "v": _norm(v)})
            if not json_mode and len(todo) > GREP_BATCH and (i // GREP_BATCH) % 10 == 0:
                print(f"[squire] embedding {min(i + GREP_BATCH, len(todo))}/{len(todo)} chunks...", file=sys.stderr)
        live = {f[0] for f in files}
        cache = {k: v for k, v in cache.items() if k in live or not k.startswith(os.path.relpath(path, root) if path != root else "")}
        if todo or not os.path.exists(cache_file):
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_file, "w") as f:
                f.write(json.dumps(cache))
            if in_git:
                exclude_cache(root)
        qv = _norm(embed([query])[0])
    except BackendError as e:
        header.update(error=f"UNKNOWN: embedding backend unavailable ({e}); try `ollama pull {EMBED_MODEL}`")
        if json_mode:
            print(json.dumps({"cmd": "grep", **header, "results": None, "backend_ok": False}))
        else:
            print(f"[squire] grep indexed {len(files)} files under {root}")
            print(f"[squire] {header['error']}")
        sys.exit(2)
    header["reembedded_chunks"] = len(todo)
    scored = []
    for rel, ent in cache.items():
        if rel not in {f[0] for f in files}:
            continue
        best = max(((sum(a * b for a, b in zip(qv, c["v"])), c) for c in ent["chunks"]), default=None, key=lambda t: t[0])
        if best:
            scored.append((best[0], rel, best[1]["s"], best[1]["e"]))
    scored.sort(reverse=True)
    results = []
    for score, rel, s, e in scored[:int(top)]:
        try:
            lines = open(os.path.join(root, rel), errors="replace").read().splitlines()[s - 1:e]
        except OSError:
            lines = []
        snippet = next((ln.strip() for ln in lines if ln.strip()), "")[:120]
        results.append({"file": rel, "line": s, "end": e, "score": round(score, 3), "snippet": snippet})
    log_call("grep", sum(f[2] for f in files), len(json.dumps(results)), True)
    if json_mode:
        print(json.dumps({"cmd": "grep", **header, "results": results, "backend_ok": True, "assumed": True}))
        return
    print(f"[squire] grep indexed {len(files)} files under {root} ({len(todo)} chunks re-embedded); "
          f"ranking is ASSUMED (semantic similarity), verify before relying on it")
    for r in results:
        print(f"{r['file']}:{r['line']}  ({r['score']})  {r['snippet']}")


# ---------------------------------------------------------------- triage

HEAD_RE = re.compile(r"^##\s+(?:\[(?P<status>[A-Z]+)[^\]]*\]\s*)?(?P<rest>.*)$")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?")


def parse_items(text):
    items, cur = [], None
    for line in text.splitlines():
        m = HEAD_RE.match(line)
        if m:
            cur = {"status": (m.group("status") or "OPEN"), "heading": line[2:].strip(), "body": []}
            ts = TS_RE.search(line)
            cur["ts"] = ts.group(0) if ts else None
            items.append(cur)
        elif cur is not None:
            cur["body"].append(line)
    return items


def age_days(ts, now=None):
    if not ts:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return round((now - d).total_seconds() / 86400, 1)


def cmd_triage(argv):
    json_mode, argv = pop_flag(argv, "--json")
    if not argv:
        sys.exit("usage: squire triage <file> [--json]")
    text = read_input(argv[0])
    items = [i for i in parse_items(text) if i["status"] not in ("DONE", "RESOLVED", "CLOSED")]
    for i in items:
        i["age_days"] = age_days(i["ts"])
    items.sort(key=lambda i: -(i["age_days"] if i["age_days"] is not None else -1))
    guesses = {}
    if items:
        listing = "\n\n".join(f"ITEM {n + 1}: {i['heading']}\n" + "\n".join(i["body"])[:1500] for n, i in enumerate(items[:25]))
        raw = llm("For each ITEM below, write exactly one line 'N: <what it blocks and how urgent, max 20 words>'. "
                  "Use only the item text; say 'unclear' if it does not say.\n\n" + listing, 60 * min(len(items), 25) + 50)
        if not raw.startswith("UNKNOWN"):
            for line in raw.splitlines():
                # Models echo the template loosely: "3: x", "ITEM 3: x", even "N: ITEM 3: x".
                m = re.search(r"ITEM\s*(\d+)\s*[:.)-]?\s*(.+)", line) or re.match(r"\s*(\d+)\s*[:.)-]\s*(.+)", line)
                if m:
                    guesses[int(m.group(1)) - 1] = m.group(2).strip()
        backend_ok = not raw.startswith("UNKNOWN")
    else:
        backend_ok = True
    for n, i in enumerate(items):
        i["guess"] = guesses.get(n) if backend_ok else None
    log_call("triage", len(text), sum(len(i.get("guess") or "") for i in items), backend_ok)
    if json_mode:
        print(json.dumps({"cmd": "triage", "open_items": [{k: v for k, v in i.items() if k != "body"} for i in items],
                          "backend_ok": backend_ok, "assumed_fields": ["guess"]}))
        return
    print(f"[squire] {len(items)} open items, oldest first. AGE is computed from the heading timestamp (REAL); "
          f"the text after 'squire:' is a model guess (ASSUMED){'' if backend_ok else ' -- backend UNKNOWN, no guesses'}")
    for i in items:
        age = f"{i['age_days']}d" if i["age_days"] is not None else "unknown"
        title = re.sub(r"^\[[A-Z]+[^\]]*\]\s*", "", i["heading"])
        print(f"[AGE: {age}] [{i['status']}] {title}")
        if i["guess"]:
            print(f"    squire: {i['guess']}")


# ---------------------------------------------------------------- stats / doctor

def cmd_stats(argv):
    json_mode, _ = pop_flag(argv, "--json")
    rows = []
    if os.path.exists(LEDGER):
        rows = [json.loads(ln) for ln in open(LEDGER) if ln.strip()]
    total_in = sum(r["chars_in"] for r in rows)
    total_out = sum(r["chars_out"] for r in rows)
    ok = sum(1 for r in rows if r["backend_ok"])
    by_cmd = {}
    for r in rows:
        c = by_cmd.setdefault(r["cmd"], {"calls": 0, "chars_in": 0, "chars_out": 0})
        c["calls"] += 1
        c["chars_in"] += r["chars_in"]
        c["chars_out"] += r["chars_out"]
    est = (total_in - total_out) // CHARS_PER_TOKEN
    if json_mode:
        print(json.dumps({"cmd": "stats", "ledger": LEDGER, "calls": len(rows), "backend_ok_calls": ok,
                          "chars_in": total_in, "chars_out": total_out, "chars_saved": total_in - total_out,
                          "by_cmd": by_cmd, "estimated_tokens_saved": est,
                          "estimate_method": f"chars/{CHARS_PER_TOKEN}, per-read only; not a measured token count"}))
        return
    if not rows:
        print("[squire] no ledger entries yet -- run some commands first")
        return
    print(f"[squire] {len(rows)} calls logged ({ok} backend-ok, {len(rows) - ok} UNKNOWN) -- REAL, computed from {LEDGER}")
    for name, c in sorted(by_cmd.items()):
        print(f"  {name:7} calls={c['calls']:<5} chars in={c['chars_in']} out={c['chars_out']}")
    print(f"[squire] chars in={total_in} out={total_out} saved={total_in - total_out} (REAL character counts)")
    print(f"[squire] ESTIMATED tokens saved: ~{est} "
          f"(chars/{CHARS_PER_TOKEN} heuristic, per read -- NOT a measured token count; every later turn "
          "re-reads context, so real savings are larger. Use scripts/squire_report.py for the measured view)")


def cmd_doctor(argv):
    json_mode, _ = pop_flag(argv, "--json")
    report = {"cmd": "doctor", "version": __version__, "backend": BACKEND,
              "endpoint": OPENAI_BASE if BACKEND == "openai" else HOST, "model_setting": MODEL,
              "embed_model": EMBED_MODEL, "ledger": LEDGER}
    try:
        models = installed_models()
        report["backend_ok"] = True
        report["installed_models"] = models
    except Exception as e:  # noqa: BLE001
        models = []
        report["backend_ok"] = False
        report["error"] = f"UNKNOWN: backend unreachable ({type(e).__name__}: {e})"[:300]
    free = gpu_free_mib()
    report["gpu_free_mib"] = free
    report["resolved_model"] = (pick_model(models, free) if MODEL == "auto" else MODEL) if models else None
    has = lambda name: any(m == name or m.startswith(name + ":") or m.split(":")[0] == name for m in models)  # noqa: E731
    report["model_installed"] = bool(report["resolved_model"]) and has(report["resolved_model"])
    report["embed_installed"] = has(EMBED_MODEL)
    ready = report["backend_ok"] and report["model_installed"]
    report["ready"] = ready
    if json_mode:
        print(json.dumps(report))
    else:
        print(f"[squire] version {__version__}, backend {BACKEND} at {report['endpoint']}")
        if not report["backend_ok"]:
            print(f"[squire] {report['error']}")
        else:
            print(f"[squire] models installed: {', '.join(models) or '(none)'}")
            print(f"[squire] chat model {report['resolved_model']}: {'installed' if report['model_installed'] else 'MISSING'}")
            print(f"[squire] embed model {EMBED_MODEL}: {'installed' if report['embed_installed'] else 'MISSING (squire grep needs it)'}")
        print(f"[squire] GPU free: {str(free) + ' MiB' if free is not None else 'UNKNOWN (nvidia-smi not reachable)'}")
        print(f"[squire] {'READY' if ready else 'NOT READY'}")
    sys.exit(0 if ready else 2)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    if sys.argv[1] in ("-V", "--version"):
        print(__version__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "run":
        return cmd_run(rest)
    handlers = {"diff": cmd_diff, "grep": cmd_grep, "triage": cmd_triage, "stats": cmd_stats, "doctor": cmd_doctor}
    if cmd in handlers:
        return handlers[cmd](rest)
    json_mode, rest = pop_flag(rest, "--json")
    if cmd == "sum":
        text = read_input(rest[0] if rest else None)
        out = condense(text, "Condense to at most 8 factual bullets. Keep numbers, names, paths exact.")
    elif cmd == "ask":
        if not rest:
            sys.exit('usage: squire ask "question" [file|-] [--json]')
        text = read_input(rest[1] if len(rest) > 1 else None)
        out = condense(text, f"Answer using ONLY this text; say UNKNOWN if it is not there. Question: {rest[0]}")
    elif cmd == "draft":
        if not rest:
            sys.exit('usage: squire draft "instructions" [file|-] [--json]')
        text = read_input(rest[1]) if len(rest) > 1 else ""
        out = llm(f"{rest[0]}\n\nSource material (may be empty):\n{text[:CHUNK]}", 1200)
    else:
        sys.exit(f"unknown command {cmd!r}; see squire --help")
    ok = not out.startswith("UNKNOWN")
    log_call(cmd, len(text), len(out), ok)
    if json_mode:
        print(json.dumps({"cmd": cmd, "exit_code": None, "raw_tail": None, "summary": out,
                          "assumed": True, "backend_ok": ok, "verify_flag": None}))
    else:
        print(out if cmd == "draft" else "[squire] " + out)


if __name__ == "__main__":
    main()

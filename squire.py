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
    squire grep "query" [path] [--top N] [--reindex] [--timeout S]   semantic search (local embeddings);
                                 --timeout returns the best PARTIAL hits (never nothing) past the deadline
    squire triage <file>         order a HANDOFF-INBOX/BACKLOG by real age, with a guessed impact line
    squire stats                 real chars in/out logged, plus a labelled token estimate
    squire doctor                check backend, models and GPU; exit 0 ready, 2 UNKNOWN
    squire warmup [--wait]        opt-in: load the model into VRAM ahead of the first real call
                                 (never automatic; backgrounds by default so it never blocks
                                 anything; no-op if the backend is down or SQUIRE_HOOK_DISABLE=1)
    squire submit <sum|ask|draft|diff|triage> [args...]   queue a job, print its id immediately
    squire worker                 process queued jobs FIFO, one at a time (run as a service)
    squire status <id> | squire wait <id> [--timeout S]   exit 0 RESULT, 1 FAIL, 2 WAIT, 3 UNKNOWN
    squire jobs                   list queued/running/done/failed jobs

Any command accepts --json to emit one JSON object instead of formatted text.

Rules built in:
- Exit codes and the raw tail are ALWAYS printed. The model never decides pass or fail.
- Model output is labelled [squire] and is ASSUMED until the session checks it.
- Backend down or erroring means the output says UNKNOWN and falls back to raw text, never silence.
- Backends must be localhost (extra hosts only via SQUIRE_ALLOW_HOSTS). No telemetry.
- Secret-shaped strings are redacted before any text is sent to the backend.
- Stdlib only.
"""
__version__ = "0.2.35"

import datetime as _dt
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

BACKEND = os.environ.get("SQUIRE_BACKEND", "ollama")
HOST = os.environ.get("SQUIRE_OLLAMA", "http://127.0.0.1:11434")
OPENAI_BASE = os.environ.get("SQUIRE_OPENAI_BASE", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("SQUIRE_MODEL", "qwen2.5:14b")
EMBED_MODEL = os.environ.get("SQUIRE_EMBED_MODEL", "nomic-embed-text")
CTX = int(os.environ.get("SQUIRE_CTX", "16384"))    # Ollama defaults to 2048, which truncates silently
KEEP_ALIVE = os.environ.get("SQUIRE_KEEP_ALIVE", "2h")
# Some hybrid-reasoning models (e.g. qwen3) default to emitting a "thinking" preamble that can
# crowd out the actual answer under squire's short max_tokens budget. Ollama's generate/chat API
# accepts a top-level "think" bool to disable it (https://ollama.com/blog/thinking, "Turning
# thinking on/off" -- also exposed via /api/generate's "think" field, verified live 2026-09-15
# against qwen3:14b). SQUIRE_THINK unset means "don't send the field" (model's own default).
_THINK_ENV = os.environ.get("SQUIRE_THINK")
THINK = None if _THINK_ENV is None else _THINK_ENV.strip().lower() not in ("0", "false", "no")
CHUNK = 24000            # chars per chunk (~6k tokens), leaves room for prompt + answer
SHORT = 60               # lines at or below this are shown raw, no model call
TAIL = 25
LEDGER = os.path.expanduser(os.environ.get("SQUIRE_LEDGER", "~/.squire/ledger.jsonl"))
# 2026-09-13 S4: which caller wrote this row -- cli (a human/session invocation), test (squire's
# own pytest fixtures), hook (the PreToolUse wrap), queue (the S5 worker). Lets stats/report
# exclude test noise from the real production ledger without ever deleting history.
SOURCE = os.environ.get("SQUIRE_SOURCE", "cli")

# 2026-09-13 S4: before the `source` field existed, squire's own test suite wrote its fixture
# calls (DOWN backend, tiny fixed inputs) straight into the real ~/.squire/ledger.jsonl -- found
# 2026-09-13 (S3-check) as 21 of 33 post-S1 rows being the SAME 7 fixture calls repeated across
# test runs. These exact (cmd, chars_in, chars_out) triples are those fixtures' fixed, deterministic
# shapes; matched only when backend_ok is False and `source` is absent (pre-S4 rows). This is a
# backward-compatible exclusion for OLD rows only -- new rows always carry `source` and need no
# guessing.
LEGACY_TEST_FIXTURE_SIGNATURES = {
    ("ask", 16492, 7), ("ask", 16681, 7), ("ask", 21453, 7), ("ask", 28351, 7), ("ask", 66678, 7),
    ("diff", 101, 43), ("diff", 101, 91),
    ("run", 1705, 43), ("run", 1705, 91), ("run", 1750, 91),
    ("sum", 5, 128), ("sum", 10, 43), ("sum", 10, 91),
    ("triage", 149, 0),
}


def is_test_row(row):
    """True for a ledger row that should be excluded from stats/report as test noise, without
    ever deleting it from the ledger file itself."""
    if row.get("source") == "test":
        return True
    if row.get("source") is None and not row.get("backend_ok") and \
            (row.get("cmd"), row.get("chars_in"), row.get("chars_out")) in LEGACY_TEST_FIXTURE_SIGNATURES:
        return True
    return False
# 2026-09-13 S1 fix: a single 24GB GPU running qwen2.5:14b at num_ctx=16384 cannot serve several
# multi-agent squire calls at once without either Ollama-side queueing (fine, bounded) or -- worse
# -- GPU/CPU offload thrashing if it tries to run them concurrently, which is what a measured
# single `sum` taking 272s under 4-agent load looks like. Client-side serialization (one in-flight
# request to the backend at a time, FIFO across all local squire processes) trades a bounded queue
# wait for eliminating that thrashing, and lets a lock-wait timeout fail fast and distinctly from a
# real backend-down/generation error.
LLM_LOCK_PATH = os.path.expanduser(os.environ.get("SQUIRE_LLM_LOCK", "~/.squire/llm.lock"))
LLM_LOCK_TIMEOUT = float(os.environ.get("SQUIRE_LLM_LOCK_TIMEOUT", "240"))  # max queue wait, seconds
# 2026-09-13 S11: embed() used to share LLM_LOCK_PATH with llm() (chat generation). A single
# `squire grep` embed call that hung talking to Ollama held that ONE shared lock for its whole
# HTTP timeout, which used to be 600s (copied from generate's long-context use case, wrong for a
# small embedding batch) -- blocking every OTHER squire call on the workstation (run/sum/ask/diff
# AND every other grep) for up to 10 minutes. Root-caused from real ledger rows: two grep calls
# tonight logged status=timeout, gen_s=600.1 -- exactly the old embed HTTP timeout, proving the
# request itself hung rather than the lock queue. Separate lock + much shorter HTTP timeout below.
EMBED_LOCK_PATH = os.path.expanduser(os.environ.get("SQUIRE_EMBED_LOCK", os.path.join(os.path.dirname(LLM_LOCK_PATH), "embed.lock")))
EMBED_LOCK_TIMEOUT = float(os.environ.get("SQUIRE_EMBED_LOCK_TIMEOUT", "60"))
EMBED_HTTP_TIMEOUT = float(os.environ.get("SQUIRE_EMBED_HTTP_TIMEOUT", "60"))
GREP_TIMEOUT = float(os.environ.get("SQUIRE_GREP_TIMEOUT", "0") or 0) or None  # 0/unset = no deadline
# 2026-09-15, item 3 (usage-analysis finding 5): 117 of 1,018 lifetime ledger calls came back
# UNKNOWN (~11.5%), 78% of them on one day (2026-09-13) -- but `ask`/`draft` specifically ran far
# below sum/grep/run/diff's 95-97% ok-rate even outside that outage window. A single failed HTTP
# call to Ollama (a dropped connection, a momentary 500, a request that lands mid-model-reload)
# used to be reported as UNKNOWN immediately with no retry at all. Bounded retry with backoff below
# turns a one-off transient failure into a success instead of a false UNKNOWN, while an exhausted
# retry still returns UNKNOWN, never a fabricated answer -- this is exactly the pattern already used
# for `_LlmLock`'s bounded wait, applied to the network call itself.
LLM_RETRIES = int(os.environ.get("SQUIRE_LLM_RETRIES", "2"))
LLM_RETRY_BACKOFF_S = float(os.environ.get("SQUIRE_LLM_RETRY_BACKOFF_S", "1.5"))
# `draft`'s max_tokens (1200, vs 300 for sum/ask/triage) generates far more tokens per call, so it
# is the likeliest of the four to hit a fixed HTTP timeout mid-generation on a slow decode. Give
# longer generations proportionally more wall-clock room, capped so a stuck request still can't
# block the caller indefinitely (never more than SQUIRE_LLM_TIMEOUT_MAX, default 600s).
LLM_TIMEOUT_S = float(os.environ.get("SQUIRE_LLM_TIMEOUT", "300"))
LLM_TIMEOUT_MAX_S = float(os.environ.get("SQUIRE_LLM_TIMEOUT_MAX", "600"))
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


def _unknown_reason(msg):
    """Pull a short, machine-usable reason out of an 'UNKNOWN: ...' sentinel (llm()'s return or a
    BackendError's str()) for the ledger's `status` field. Before this, log_call's `status` was
    only ever set by a handful of call sites that already had a distinct reason string on hand
    (grep's 'timeout'/'error', run's missing-command reasons); every other failure just logged
    backend_ok=False with no reason at all, so squire_report.py could count UNKNOWN calls but not
    say why any of them failed. Returns None for anything that isn't an UNKNOWN sentinel."""
    if not msg or not msg.startswith("UNKNOWN:"):
        return None
    m = re.search(r"\(([A-Za-z_]+):", msg)
    if m:
        return m.group(1)
    if "busy" in msg:
        return "busy"
    return "unknown"


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


def log_call(cmd, chars_in, chars_out, backend_ok, queue_wait_s=None, gen_s=None, source=None, job=None,
             status=None, exit_code=None, duration_s=None, passthrough=False, chars_avoided=None):
    # chars_in/out/ts are real, computed facts. The token estimate derived from them in `stats`
    # is NOT -- the two must never be presented as the same kind of claim.
    # queue_wait_s/gen_s (2026-09-13, S1) split how long a call sat waiting for the local lock
    # from how long the model actually took, so a slow "backend" can be told apart from a busy one.
    # `job` (2026-09-13, S5, owner request ahead of the S6 benchmark) lets the queue worker attach
    # the job id and its enqueue/start/finish timestamps to the SAME ledger schema the synchronous
    # path writes, so S6 can compare sync vs queued using one set of rows.
    # `status` (2026-09-13, S10) records WHY a call failed (e.g. "timeout") separately from
    # `source` (which is provenance: cli/test/queue) so a stuck call is visible to
    # squire_report.py as a distinct, named outcome instead of just backend_ok=False.
    # `exit_code`/`duration_s` (2026-09-13, S14) record the REAL subprocess exit code and total
    # wall time of a `squire run` invocation. Before this, every "run" ledger row carried
    # exit=None even though `squire run` prints the real code to the user (`[squire] exit=N`) --
    # the ledger had no way to audit it after the fact. gen_s/queue_wait_s only cover the LLM
    # summarization call, not the wrapped command, so a separate field is needed.
    # chars_avoided_ESTIMATE (2026-09-15): a SEPARATE field from chars_in, deliberately never
    # overwriting it. chars_in is "how much text this call ingested" -- for `grep` that is the
    # whole indexed corpus (one real call ingested 10.9M chars), which the caller would NEVER
    # have read raw and so is not a defensible savings denominator. chars_avoided is "what the
    # caller would actually have read instead of this call's output" -- for run/sum/ask/diff/
    # triage that already equals chars_in (the real raw output/file/diff text), so it defaults
    # to chars_in when the caller doesn't pass a different counterfactual. Only `grep` passes an
    # explicit, smaller value (see cmd_grep).
    # agent_id/agent_type (2026-09-18): PreToolUse hook payloads carry these fields ONLY when
    # the tool call happens inside a subagent (verified against
    # https://code.claude.com/docs/en/hooks) -- the harness does not export them as env vars on
    # its own, so pretool_wrap.py/search_guard.py inject SQUIRE_AGENT_ID/SQUIRE_AGENT_TYPE into
    # the wrapped command's environment when it rewrites a Bash call. Absent for the main thread
    # and for any call not routed through a squire-wrapping hook -- both read as None, never
    # coerced to a fake "main" sentinel here (that belongs in the report layer, which can tell
    # "no agent" from "not recorded").
    row = {"ts": time.time(), "cmd": cmd, "chars_in": chars_in, "chars_out": chars_out,
           "chars_avoided_ESTIMATE": chars_in if chars_avoided is None else chars_avoided,
           "backend_ok": backend_ok, "queue_wait_s": queue_wait_s, "gen_s": gen_s,
           "session": os.environ.get("CLAUDE_CODE_SESSION_ID"), "source": source or SOURCE,
           "agent_id": os.environ.get("SQUIRE_AGENT_ID"), "agent_type": os.environ.get("SQUIRE_AGENT_TYPE"),
           "status": status, "exit_code": exit_code, "duration_s": duration_s}
    # `passthrough` (2026-09-15): a short `squire run` shown raw with no model call. Logged so real
    # usage is counted; it saved nothing, so every savings total excludes it.
    if passthrough:
        row["passthrough"] = True
    if job:
        row.update(job_id=job["id"], enqueued_at=job["enqueued_at"], started_at=job["started_at"],
                   finished_at=job["finished_at"])
    try:
        os.makedirs(os.path.dirname(LEDGER), mode=0o700, exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass  # stats are a bonus; never fail the actual command over a logging error


class _LlmLock:
    """Serializes actual network calls to the local model backend across all squire processes
    on this workstation (flock on a shared file). One in-flight generate/embed call at a time
    avoids Ollama trying to run several large-context requests concurrently on one GPU, which
    degrades into GPU/CPU offload thrashing rather than clean queueing. Gives up after
    `timeout` seconds of waiting rather than blocking forever; the caller decides what a
    failed acquisition means (fail fast, labelled distinctly from a generation-time failure).

    `path`/`timeout` default to the chat-generation lock (LLM_LOCK_PATH/LLM_LOCK_TIMEOUT).
    embed() passes its own EMBED_LOCK_PATH/EMBED_LOCK_TIMEOUT (2026-09-13, S11) so a slow chat
    generation and a `squire grep` embedding call never queue behind each other -- they used to
    share this same lock, so a single hung embed call could block every other squire invocation
    on the workstation for as long as the embed HTTP call took to time out."""

    def __init__(self, path=None, timeout=None):
        self.path = path or LLM_LOCK_PATH
        self.timeout = LLM_LOCK_TIMEOUT if timeout is None else timeout

    def __enter__(self):
        self.wait_s = 0.0
        self._f = None
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            self._f = open(self.path, "a+")
        except OSError:
            return self  # no lock available (e.g. read-only fs); proceed unserialized
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(self._f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                break
            except BlockingIOError:
                self.wait_s = time.monotonic() - start
                if self.wait_s >= self.timeout:
                    self.acquired = False
                    break
                time.sleep(0.25)
        self.wait_s = time.monotonic() - start
        return self

    def __exit__(self, *exc):
        if self._f is not None:
            try:
                if getattr(self, "acquired", False):
                    fcntl.flock(self._f, fcntl.LOCK_UN)
            finally:
                self._f.close()
        return False


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

GPU_PAUSE_FLAG = os.environ.get("SQUIRE_GPU_PAUSE_FLAG") or os.path.expanduser("~/.squire/gpu-paused.json")


def _docker_pid_alive(pid):
    """True if `pid` is running. Named distinctly from the existing `_pid_alive` (used by
    `_worker_alive()` for squire's OWN worker process, same pid namespace as squire itself --
    a plain os.kill is correct there and must not be changed). NCP-ArchPreview's pid comes from
    `docker inspect`, which reports the pid in the HOST pid namespace -- invisible to a plain
    os.kill from inside Claude Code's sandboxed Bash tool (confirmed 2026-09-13:
    ProcessLookupError against a container pid that was very much still running). Falls back to
    `flatpak-spawn --host kill -0` (same escape gpu_free_mib() already uses for nvidia-smi) so
    the check is correct from both contexts. Any ambiguous failure (PermissionError,
    flatpak-spawn missing) is treated as "still alive" -- a false "paused" costs one skipped
    Squire call; a false "stale" would wedge nothing but wrongly let Squire fight NCP for the
    GPU."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        pass
    except OSError:
        return True
    try:
        r = subprocess.run(["flatpak-spawn", "--host", "kill", "-0", str(pid)],
                            capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def gpu_paused_by():
    """Returns the owner name from ~/.squire/gpu-paused.json if another process (e.g.
    NCP-ArchPreview's `ncp up`) currently owns the GPU, or None if unpaused. A flag whose
    recorded pid is no longer alive is stale -- it is removed and treated as unpaused, so a
    crashed owner never wedges Squire's model calls permanently.

    This must NOT block a call that is itself going TO the paused owner: when Squire is
    benchmarked with NCP-ArchPreview as its own backend (SQUIRE_BACKEND=openai,
    SQUIRE_OPENAI_BASE pointed at the same server `ncp up` wrote into the flag's "api_base"),
    the pause exists to keep Ollama off the GPU while NCP holds it -- it is not a block on
    talking to NCP itself. Only Ollama calls (the default backend), or an "openai" backend
    pointed at some OTHER server, are short-circuited."""
    try:
        with open(GPU_PAUSE_FLAG) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    if pid is not None and not _docker_pid_alive(pid):
        try:
            os.remove(GPU_PAUSE_FLAG)
        except OSError:
            pass
        return None
    if BACKEND == "openai":
        flag_base = (data.get("api_base") or "").rstrip("/")
        if flag_base and OPENAI_BASE.rstrip("/") == flag_base:
            return None  # this call IS the paused owner's own API; let it through
    return data.get("owner") or "another process"


def model():
    global _resolved_model
    if MODEL != "auto":
        return MODEL
    if _resolved_model is None:
        _resolved_model = pick_model(installed_models(), gpu_free_mib()) or "qwen2.5:14b"
    return _resolved_model


# Cumulative queue-wait/generation time across a whole squire invocation (condense() may issue
# several llm() calls for chunking + a combine pass). reset_call_timing() zeroes it; callers read
# it back after their work to pass real, measured numbers into log_call (2026-09-13, S1).
_call_timing_totals = {"queue_wait_s": 0.0, "gen_s": 0.0}


def reset_call_timing():
    _call_timing_totals["queue_wait_s"] = 0.0
    _call_timing_totals["gen_s"] = 0.0


def get_call_timing():
    return round(_call_timing_totals["queue_wait_s"], 2), round(_call_timing_totals["gen_s"], 2)


def _llm_gen_timeout(max_tokens):
    if max_tokens <= 300:
        return LLM_TIMEOUT_S
    return min(LLM_TIMEOUT_MAX_S, LLM_TIMEOUT_S + max_tokens)


def llm(prompt, max_tokens=300):
    prompt = redact(prompt)
    paused_owner = gpu_paused_by()
    if paused_owner:
        return f"UNKNOWN: model busy (GPU in use by {paused_owner})"[:300]
    with _LlmLock() as lock:
        _call_timing_totals["queue_wait_s"] += lock.wait_s
        if not getattr(lock, "acquired", True):
            # Distinct from a backend-down/generation error: the backend is fine, this call just
            # never got a turn. Fails fast instead of also burning a full HTTP timeout on top.
            return f"UNKNOWN: local model busy (queued {round(lock.wait_s, 1)}s, gave up)"[:300]
        gen_start = time.monotonic()
        timeout = _llm_gen_timeout(max_tokens)
        last_err = None
        try:
            for attempt in range(LLM_RETRIES + 1):
                try:
                    if BACKEND == "openai":
                        data = _post(OPENAI_BASE.rstrip("/") + "/chat/completions",
                                     {"model": model(), "messages": [{"role": "user", "content": prompt}],
                                      "max_tokens": max_tokens, "temperature": 0.1}, timeout)
                        return data["choices"][0]["message"]["content"].strip()
                    # keep_alive holds the model in VRAM between calls: a cold load (~75s for 14B)
                    # inside a hook-wrapped test run can push the command past the caller's timeout.
                    gen_payload = {"model": model(), "prompt": prompt, "stream": False,
                                   "keep_alive": KEEP_ALIVE,
                                   "options": {"num_ctx": CTX, "num_predict": max_tokens, "temperature": 0.1}}
                    if THINK is not None:
                        gen_payload["think"] = THINK
                    data = _post(HOST + "/api/generate", gen_payload, timeout)
                    return data.get("response", "").strip()
                except Exception as e:  # noqa: BLE001 - any failure is reported, never swallowed
                    last_err = e
                    if attempt < LLM_RETRIES:
                        time.sleep(LLM_RETRY_BACKOFF_S * (2 ** attempt))
                        continue
            # Retries exhausted (or SQUIRE_LLM_RETRIES=0): still never fabricate an answer, and the
            # reason names the real exception so the ledger's status field (see _unknown_reason)
            # can tell "timeout" apart from "connection refused" apart from a 500, etc.
            tried = f" after {LLM_RETRIES + 1} attempts" if LLM_RETRIES else ""
            return f"UNKNOWN: local model unavailable{tried} ({type(last_err).__name__}: {last_err})"[:300]
        finally:
            _call_timing_totals["gen_s"] += time.monotonic() - gen_start


def embed(texts, kind="document"):
    """Embed a batch; raises BackendError on any failure so callers report UNKNOWN."""
    texts = [redact(t) for t in texts]
    paused_owner = gpu_paused_by()
    if paused_owner:
        raise BackendError(f"model busy (GPU in use by {paused_owner})")
    if "nomic" in EMBED_MODEL:
        # nomic-embed-text is trained with task prefixes; without them ranking is noticeably worse.
        texts = [f"search_{kind}: {t}" for t in texts]
    try:
        with _LlmLock(EMBED_LOCK_PATH, EMBED_LOCK_TIMEOUT) as lock:
            _call_timing_totals["queue_wait_s"] += lock.wait_s
            if not getattr(lock, "acquired", True):
                raise BackendError(f"local model busy (queued {round(lock.wait_s, 1)}s, gave up)")
            gen_start = time.monotonic()
            try:
                if BACKEND == "openai":
                    data = _post(OPENAI_BASE.rstrip("/") + "/embeddings", {"model": EMBED_MODEL, "input": texts}, EMBED_HTTP_TIMEOUT)
                    return [d["embedding"] for d in data["data"]]
                data = _post(HOST + "/api/embed", {"model": EMBED_MODEL, "input": texts}, EMBED_HTTP_TIMEOUT)
                return data["embeddings"]
            finally:
                _call_timing_totals["gen_s"] += time.monotonic() - gen_start
    except BackendError:
        raise
    except Exception as e:  # noqa: BLE001
        raise BackendError(f"{type(e).__name__}: {e}") from e


def chunks(text):
    return [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]


def _condense_core(text, task, max_tokens=300):
    """Shared chunk/summarize logic for condense() and condense_verified(). Returns
    (summary, notes) where `notes` is None for a single-chunk input (the raw text itself is the
    full source) or the list of per-chunk partial notes when the input was split -- those notes,
    together, cover the WHOLE input, unlike any single CHUNK-sized slice of the raw text."""
    parts = chunks(text)
    if len(parts) == 1:
        return llm(f"{task}\n\n---\n{parts[0]}\n---", max_tokens), None
    notes = [llm(f"{task} (part {i + 1}/{len(parts)}; be brief)\n\n---\n{p}\n---", 150)
             for i, p in enumerate(parts)]
    # 2026-09-14: a real backend-error sentinel is always "UNKNOWN: <reason>" (see llm()'s three
    # returns). A per-chunk note that is literally the bare word "UNKNOWN" is the MODEL correctly
    # reporting "the answer isn't in this chunk" for an ask-style task -- normal and expected for
    # most chunks of a multi-chunk file, not a backend failure. Treating any bare "UNKNOWN" note as
    # fatal used to abort the whole combine step on the first chunk that didn't contain the answer,
    # so `squire ask` on anything over one CHUNK (~24k chars) came back with a bare "UNKNOWN" and no
    # reason even though the backend was healthy and later chunks may have had the real answer.
    # Only a note with a reason (the colon) is a genuine backend error worth aborting for.
    if any(n.startswith("UNKNOWN:") for n in notes):
        return next(n for n in notes if n.startswith("UNKNOWN:")), notes
    return llm(f"{task}\nCombine these partial notes into one answer:\n\n" + "\n".join(notes), max_tokens), notes


def condense(text, task, max_tokens=300):
    return _condense_core(text, task, max_tokens)[0]


def condense_verified(text, task, max_tokens=300):
    """condense() plus a second local pass checking the summary against the source.
    Used where an inaccurate summary would mislead a real decision (test failures, diffs). A
    disagreement is appended visibly, never hidden or silently retried. The check is itself
    ASSUMED: a quality signal, not a correctness guarantee.

    2026-09-13, S14: the check used to compare the final summary against `text[-CHUNK:]` -- only
    the LAST CHUNK chars of the raw input. For input bigger than one chunk (the whole point of
    the multi-chunk path below), the summary can legitimately include a point drawn from an
    EARLIER chunk (a 5,604-line passing smoke-test log, one early line mentioning a handled
    JSONDecodeError) that the truncated tail slice never contained -- the check then reported a
    false "not mentioned in the source", read by a user as an off-topic non-sequitur bolted onto
    an otherwise-correct summary. The notes computed below were each derived by reading the FULL
    text in windows, so their concatenation -- not a tail slice of the raw text -- is what the
    verify check should compare the final summary against whenever the input was chunked."""
    summary, notes = _condense_core(text, task, max_tokens)
    if summary.startswith("UNKNOWN:"):
        return summary, None
    check_source = "\n".join(notes) if notes else text
    check = llm(
        "SOURCE (truncated) and a SUMMARY of it follow. Reply with exactly 'OK' if the summary "
        "invents nothing not supported by the source and omits no failure/error the source contains. "
        "Otherwise reply with one short sentence naming the specific inaccuracy.\n\n"
        f"SOURCE:\n{check_source[-CHUNK:]}\n\nSUMMARY:\n{summary}", 60)
    if check.startswith("UNKNOWN:"):
        return summary, None
    flag = None if check.strip().rstrip(".").upper() == "OK" else check.strip()
    return summary, flag


# ---------------------------------------------------------------- item 5: sum/ask cache + citations

CACHE_DIR = os.path.expanduser(os.environ.get("SQUIRE_CACHE_DIR", "~/.squire/cache"))
# Size bound: a sum/ask result is a few hundred bytes to a few KB, so 500 entries bounds the whole
# cache to single-digit MB, not unbounded growth. Evicted least-recently-USED first (see
# _cache_evict), not oldest-written, so a call repeated often outlives a one-off.
CACHE_MAX_ENTRIES = int(os.environ.get("SQUIRE_CACHE_MAX_ENTRIES", "500"))
# Invalidation rule: bump this whenever `sum`'s or `ask`'s prompt TEMPLATE changes (the fixed
# instruction text baked into `task` below, not the input text or the question) -- a stale entry
# cached under the OLD prompt would silently serve output the CURRENT prompt was never asked to
# produce. cache_key() folds this into the key, so bumping it invalidates every old entry by
# simply never matching them again; nothing is deleted, they just age out via CACHE_MAX_ENTRIES.
PROMPT_VERSION = "v1"

CITE_INSTRUCTION = ("Cite the exact source line number(s) for each claim, using the numbers shown "
                    "at the start of each input line, formatted as [L<n>] or [L<n>-<m>].")


def cache_key(cmd, text, task):
    # Keyed on the EXACT input text plus model + prompt version + task (task already carries the
    # question for `ask`, so two different questions against the same file never collide).
    h = hashlib.sha256()
    for part in (cmd, model(), PROMPT_VERSION, task):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    h.update(text.encode("utf-8", "replace"))
    return h.hexdigest()


def cache_get(key):
    """Cached result string, or None on a miss -- including a corrupt or missing cache file. The
    cache is a pure speed optimization; it must never be load-bearing for correctness, so any
    failure here is silently treated as a miss and the real call proceeds."""
    path = os.path.join(CACHE_DIR, key + ".json")
    try:
        with open(path) as f:
            result = json.load(f)["result"]
        os.utime(path, None)  # touch on hit: eviction below is LRU, not insertion-order
        return result
    except (OSError, ValueError, KeyError):
        return None


def cache_put(key, result):
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        with open(os.path.join(CACHE_DIR, key + ".json"), "w") as f:
            json.dump({"result": result, "ts": time.time()}, f)
    except OSError:
        return
    _cache_evict()


def _cache_evict():
    try:
        names = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    except OSError:
        return
    if len(names) <= CACHE_MAX_ENTRIES:
        return
    entries = []
    for f in names:
        try:
            entries.append((os.path.getmtime(os.path.join(CACHE_DIR, f)), f))
        except OSError:
            continue
    entries.sort()  # oldest-touched first
    for _, f in entries[:len(entries) - CACHE_MAX_ENTRIES]:
        try:
            os.remove(os.path.join(CACHE_DIR, f))
        except OSError:
            pass


def add_line_numbers(text):
    """Prefixes every line with its 1-based line number, so sum/ask can cite exactly where a
    claim came from ([L<n>] / [L<n>-<m>]) -- verifying one becomes a two-line ranged Read instead
    of re-reading the whole file to find it. Only used for the copy of the text sent to the model;
    the cache key and every ledger char count still use the original, unnumbered text."""
    lines = text.split("\n")
    width = len(str(len(lines))) or 1
    return "\n".join(f"{i + 1:>{width}}: {ln}" for i, ln in enumerate(lines))


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
    # stdin=DEVNULL: a nested launcher (docker -i, ssh, flatpak-spawn --host ...) that attaches
    # stdin will otherwise inherit whatever fd squire itself got. In an interactive shell that fd
    # is a live tty/pipe that never reaches EOF, so the nested process blocks waiting for input
    # that is never coming -- squire "hangs" with no exit code and no output, which from the
    # caller's side is indistinguishable from squire being broken (found 2026-09-13, S2: "squire
    # run breaks on nested flatpak-spawn/docker invocations"). squire's contract is capture-then-
    # summarize, never interactive, so stdin is never useful here -- close it up front.
    timeout_s = float(os.environ.get("SQUIRE_RUN_TIMEOUT", "0") or 0) or None
    run_start = time.monotonic()
    try:
        p = subprocess.run(argv if len(argv) > 1 else argv[0], shell=len(argv) == 1,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           errors="replace", stdin=subprocess.DEVNULL, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        # Still surface everything captured before the kill, and a real, non-zero, documented
        # exit code (124, the shell convention for `timeout`) -- never silence, per squire's rule.
        out = (e.output or "") if isinstance(e.output, str) else (e.output or b"").decode("utf-8", "replace")
        out += f"\n[squire] killed after {timeout_s}s (SQUIRE_RUN_TIMEOUT)\n"
        if not json_mode:
            print(f"[squire] exit=124 lines={len(out.splitlines())} (timeout)")
        emit("run", 124, out, None, True, json_mode)
        sys.exit(124)
    except OSError as e:
        # The command named after "--" does not exist, is not executable, or its interpreter
        # doesn't (e.g. a missing binary, a bad shebang). This ONLY happens on the shell=False
        # (multi-arg) path -- shell=True already returns a real 127 from the shell itself for a
        # missing command. Before this fix, subprocess.run raised FileNotFoundError/PermissionError
        # here UNCAUGHT: Python printed its own traceback and exited 1, with no "[squire] exit=N"
        # line at all -- a wrapper (e.g. the pretool_wrap hook feeding `squire run -- bash -c
        # '<cmd>'`) could then be misread as if the command ran, since the fidelity contract
        # ("squire always prints the real exit code") silently didn't hold for this case. Fabricate
        # the shell-convention codes (127 = command not found, 126 = found but not executable) so
        # this failure mode is never confused with success and always has a visible, real,
        # non-zero code -- VERIFIED never 0.
        missing = argv[0] if argv else "?"
        code = 126 if isinstance(e, PermissionError) else 127
        reason = "permission denied" if code == 126 else "command not found"
        out = f"squire: {reason}: {missing} ({e})\n"
        if not json_mode:
            print(f"[squire] exit={code} lines=1 ({reason})")
        log_call("run", len(out), len(out), True, exit_code=code,
                 duration_s=round(time.monotonic() - run_start, 2), passthrough=True,
                 status=reason)
        emit("run", code, out, None, True, json_mode)
        sys.exit(code)
    out = p.stdout or ""
    lines = out.splitlines()
    if not json_mode:
        print(f"[squire] exit={p.returncode} lines={len(lines)}")
    if len(lines) <= SHORT:
        log_call("run", len(out), len(out), True, exit_code=p.returncode,
                 duration_s=round(time.monotonic() - run_start, 2), passthrough=True)
        emit("run", p.returncode, out, None, True, json_mode)
    else:
        if not json_mode:
            print(f"[squire] --- raw tail ({TAIL} lines) ---")
        reset_call_timing()
        summary, flag = condense_verified(out, "Summarize this command output for a busy engineer in at most 8 bullets. "
                                          "List every failing test/error with file:line and the one-line cause. "
                                          "Say 'no errors seen' only if there are none. Never invent names.")
        ok = not summary.startswith("UNKNOWN:")
        wait_s, gen_s = get_call_timing()
        log_call("run", len(out), len(summary), ok, wait_s, gen_s,
                  exit_code=p.returncode, duration_s=round(time.monotonic() - run_start, 2),
                  status=None if ok else _unknown_reason(summary))
        emit("run", p.returncode, "\n".join(lines[-TAIL:]), summary, ok, json_mode, flag)
    sys.exit(p.returncode)


# 2026-09-18: `squire diff --staged` hung (>7min, then >90s on a retry) on a staged set of
# regenerated PNG/PDF files, and the commit gate expects this command before every commit -- a
# hang pushes agents to skip the gate (`git diff --staged --stat` was used instead, undogfooded).
# Root cause: `git diff --staged` with no pathspec includes every changed file, and this command
# fed that WHOLE text to the model with no regard for how many files were binary or how large the
# diff was. `git diff` reliably renders a changed binary file as a one-line "Binary files a/x and
# b/x differ" marker (verified: 3MB PNG + 2MB PDF staged locally still produced a 470-byte diff),
# so raw byte count wasn't the smoking gun by itself -- but nothing here bounded either the
# per-file content sent for condensation or the WALL-CLOCK time the model call could take, so any
# combination of many changed files (binary or not) queued up an unbounded number of sequential
# `llm()` calls in `_condense_core`'s chunk loop with only the 600s-per-call cap as a backstop.
# Three independent guards now apply: (1) binary files are identified via `--numstat` (which
# prints "-\t-\t<path>" for a binary file) and their content is NEVER sent to the model, only a
# "Binary file changed: <path>" placeholder -- the diff sent for summarisation is built from
# `git diff --staged -- <text files>` only; (2) the text sent to the model is capped at
# DIFF_MAX_CONDENSE_BYTES, truncated with a visible note past that so a huge text-only diff can't
# reproduce the same failure; (3) a hard wall-clock timeout (DIFF_SUMMARY_TIMEOUT_S) wraps the
# whole condense_verified() call in a daemon thread -- past the deadline this returns the real
# `--stat` plus "UNKNOWN: summary timed out (Ns)" instead of hanging, and the exit code stays 0
# (diff succeeded; only the summarisation timed out) so a caller relying on the exit code is never
# misled. The stray background thread is a daemon and is abandoned, not killed -- Python has no
# way to kill a thread blocked in a socket read, and the process is about to exit anyway.
DIFF_MAX_CONDENSE_BYTES = int(os.environ.get("SQUIRE_DIFF_MAX_BYTES", "200000"))
DIFF_SUMMARY_TIMEOUT_S = float(os.environ.get("SQUIRE_DIFF_SUMMARY_TIMEOUT", "90"))


def _diff_binary_paths(gitcmd):
    """Paths git reports as binary for this diff, via --numstat ('-\t-\t<path>' for binary)."""
    p = subprocess.run(gitcmd + ["--numstat"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, errors="replace")
    paths = []
    for line in (p.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0] == "-" and parts[1] == "-":
            paths.append(parts[2])
    return paths


def _condense_input_for_diff(gitcmd, binary_paths):
    """Build the text handed to the model: real diff hunks for text files, a one-line placeholder
    per binary file -- binary content is never sent for summarisation, whatever git's own binary
    detection did or didn't do for it. Capped at DIFF_MAX_CONDENSE_BYTES so a huge text-only diff
    can't reproduce the same unbounded-chunk-loop failure."""
    pathspec_cmd = gitcmd + ["--"] + [":(exclude)" + b for b in binary_paths] if binary_paths else gitcmd
    p = subprocess.run(pathspec_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, errors="replace")
    if p.returncode == 0:
        text_diff = p.stdout or ""
    binary_note = "\n".join(f"Binary file changed: {path}" for path in binary_paths)
    combined = "\n".join(x for x in (text_diff, binary_note) if x)
    if len(combined) > DIFF_MAX_CONDENSE_BYTES:
        combined = combined[:DIFF_MAX_CONDENSE_BYTES] + \
            f"\n\n[squire] truncated at {DIFF_MAX_CONDENSE_BYTES} bytes ({len(combined)} total)"
    return combined


def _condense_verified_with_timeout(text, task, timeout_s):
    """condense_verified() under a hard wall-clock deadline. Runs in a daemon thread so a stuck
    HTTP call (past even the model's own per-call timeout, or queued behind another caller) can
    never hang this command past `timeout_s` -- past the deadline this returns
    ("UNKNOWN: summary timed out (Ns)", None) and abandons the thread; there is no way to kill a
    thread blocked in a socket read, and the CLI process is about to print --stat and exit anyway."""
    result = {}

    def _run():
        result["value"] = condense_verified(text, task)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return f"UNKNOWN: summary timed out ({timeout_s:.0f}s)", None
    return result.get("value", (f"UNKNOWN: summary thread produced no result", None))


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
    reset_call_timing()
    binary_paths = _diff_binary_paths(gitcmd)
    condense_input = _condense_input_for_diff(gitcmd, binary_paths)
    summary, flag = _condense_verified_with_timeout(
        condense_input, "Summarize this git diff by file/module. Separate logic changes "
        "from formatting/rename-only changes. Be factual, keep exact file paths and names.",
        DIFF_SUMMARY_TIMEOUT_S)
    ok = not summary.startswith("UNKNOWN:")
    wait_s, gen_s = get_call_timing()
    log_call("diff", len(condense_input), len(summary), ok, wait_s, gen_s,
              status=None if ok else _unknown_reason(summary))
    if not json_mode:
        print(f"[squire] {stat.splitlines()[-1] if stat else '(no stat)'}")
    emit("diff", 0, stat, summary, ok, json_mode, flag)


# ---------------------------------------------------------------- grep

GREP_WINDOW, GREP_OVERLAP, GREP_BATCH = 40, 10, 32
GREP_MAX_BYTES = 1_000_000
GREP_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".squire-cache", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache"}
GREP_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".pyc", ".so", ".bin", ".woff", ".woff2", ".ico", ".mp3", ".mp4", ".sqlite", ".db", ".pyz", ".lock"}


# item 6, 2026-09-15: `squire grep`'s own output says "ranking is ASSUMED (semantic similarity)"
# -- a real embedding model can genuinely miss a term that's in the file verbatim (wrong context,
# an under-trained token, a too-aggressive top-N cutoff). Absence of a semantic hit is not evidence
# of absence (the same rule ~/.claude/CLAUDE.md section 17 states for a session's own claims,
# expressed here in code): a caller who sees zero semantic results has no way to tell "genuinely
# not in the codebase" from "the embedding missed it" unless something PROVABLE is checked too.
# LITERAL_STOPWORDS keeps the exact search from wasting its budget on words too common to be
# distinctive (a hit on "the" tells a caller nothing); the query is still searched whole via the
# semantic path above regardless of what this filters out.
LITERAL_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "for",
                      "and", "or", "with", "that", "this", "how", "what", "where", "when", "why",
                      "does", "do", "did", "not", "it", "be", "by", "as", "from", "at"}
LITERAL_MAX_HITS = 20


def literal_terms(query):
    """Distinctive terms from a grep query for the literal search: alnum/path-shaped words of 3+
    chars, stopwords dropped, de-duplicated case-insensitively, in first-seen order."""
    words = re.findall(r"[A-Za-z0-9_./-]{3,}", query)
    seen, terms = set(), []
    for w in words:
        lw = w.lower()
        if lw in LITERAL_STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        terms.append(w)
    return terms


def literal_search(root, files, terms, max_hits=LITERAL_MAX_HITS):
    """Exact, case-insensitive substring search for `terms` across the same file list `squire
    grep` already indexed -- no embeddings, no ranking, just: is this text really there. Reported
    ALONGSIDE the semantic results, never instead of them, so a caller can see when the two
    disagree (a real term present in the literal pass but absent from the top semantic hits is
    exactly the failure mode this guards against)."""
    hits = []
    if not terms:
        return hits
    pats = [re.compile(re.escape(t), re.I) for t in terms]
    for rel, _mtime, _size in files:
        try:
            with open(os.path.join(root, rel), errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if any(p.search(line) for p in pats):
                        hits.append({"file": rel, "line": lineno, "snippet": line.strip()[:120]})
                        if len(hits) >= max_hits:
                            return hits
        except OSError:
            continue
    return hits


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
    # 2026-09-13, S11: a stuck/slow embedding backend used to make `squire grep` run (or wait on
    # the lock) indefinitely with nothing to show for it -- callers gave up and fell back to raw
    # grep, defeating the squire mandate. --timeout (or SQUIRE_GREP_TIMEOUT) caps wall-clock time
    # spent re-embedding; whatever chunks finished before the deadline are cached and searched, so
    # a timeout still returns the BEST PARTIAL hits plus a visible "partial" note, never nothing.
    timeout_opt, argv = pop_opt(argv, "--timeout", None)
    grep_timeout = float(timeout_opt) if timeout_opt is not None else GREP_TIMEOUT
    if not argv:
        sys.exit('usage: squire grep "query" [path] [--top N] [--reindex] [--timeout S] [--json]')
    query, path = argv[0], (argv[1] if len(argv) > 1 else ".")
    path = os.path.abspath(path)
    if not os.path.exists(path):
        sys.exit(f"[squire] no such file or directory: {path}")
    # 2026-09-14: a single FILE path used to walk itself into `root`. os.path.isdir(path) is False
    # for a file, so `root` fell through to `None` and then to `path` (the file), and list_files()
    # does os.walk(root) -- os.walk on a non-directory yields nothing, so the file was silently
    # indexed as 0 files and every search came back empty with no error. `root` must always be a
    # DIRECTORY (the file's own repo, or its parent dir outside a repo); the existing prefix filter
    # below already narrows to one file correctly once `root` is a real directory to walk/list.
    scan_dir = path if os.path.isdir(path) else os.path.dirname(path)
    root = repo_root(scan_dir) if os.path.isdir(scan_dir) else None
    in_git = root is not None
    root = root or scan_dir
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
    grep_t0 = time.monotonic()
    partial = False
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
        done = 0
        for i in range(0, len(todo), GREP_BATCH):
            if grep_timeout is not None and (time.monotonic() - grep_t0) >= grep_timeout:
                partial = True
                break
            batch = todo[i:i + GREP_BATCH]
            vecs = embed([t[3][:6000] for t in batch])
            for (rel, s, e, _), v in zip(batch, vecs):
                cache[rel]["chunks"].append({"s": s, "e": e, "v": _norm(v)})
            done = i + len(batch)
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
        if partial:
            # Ran out of time before every chunk was embedded. Still search whatever IS embedded
            # (files not yet re-embedded fall out of scoring below since their cache entry has
            # empty/stale chunks) rather than returning nothing -- best partial hits, clearly
            # labelled, plus a ledger row so a timeout is visible instead of silently absent
            # (same S10 principle applied to a graceful deadline instead of a hard backend error).
            header["reembedded_chunks"] = done
            header["skipped_chunks"] = len(todo) - done
            try:
                qv = _norm(embed([query])[0])
            except BackendError:
                qv = None  # even the query embed didn't make it back before the deadline
        else:
            qv = _norm(embed([query])[0])
    except BackendError as e:
        # A stuck/timed-out embedding call used to die here with NO ledger row at all -- from
        # the ledger's side a stuck `squire grep` was indistinguishable from one never run
        # (found 2026-09-13, S10). Always log a row on this path, same as the success path
        # below, so squire_usage_report.py can see it instead of it going silently absent.
        grep_dur = round(time.monotonic() - grep_t0, 1)
        fail_status = "timeout" if "timeout" in str(e).lower() else "error"
        log_call("grep", sum(f[2] for f in files), 0, False, gen_s=grep_dur, status=fail_status)
        header.update(error=f"UNKNOWN: embedding backend unavailable ({e}); try `ollama pull {EMBED_MODEL}`")
        # Semantic ranking is down, but the literal pass needs no model at all -- still run it so
        # a broken embedding backend doesn't ALSO take away the one thing that can prove absence.
        terms = literal_terms(query)
        literal_hits = literal_search(root, files, terms)
        if json_mode:
            print(json.dumps({"cmd": "grep", **header, "results": None, "backend_ok": False,
                              "literal_terms": terms, "literal_hits": literal_hits}))
        else:
            print(f"[squire] grep indexed {len(files)} files under {root}")
            print(f"[squire] {header['error']}")
            print(f"[squire] literal (exact, no model) matches for {terms}: {len(literal_hits)} hits")
            for h in literal_hits:
                print(f"{h['file']}:{h['line']}  {h['snippet']}")
        sys.exit(2)
    if not partial:
        header["reembedded_chunks"] = len(todo)
    header["partial"] = partial
    scored = []
    if qv is not None:
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
    grep_dur = round(time.monotonic() - grep_t0, 1)
    # chars_avoided_ESTIMATE for grep: NOT the whole indexed corpus (chars_in above, kept
    # unchanged for backward compatibility) -- a caller who didn't have `squire grep` would not
    # have read every indexed file, only opened the files that actually turned up as hits. Model
    # that as: each DISTINCT hit file, capped at a realistic single-look read window rather than
    # its full size (2026-09-15, item 2 of the usage-analysis fix list -- "the bytes of the hits
    # actually returned plus a realistic read window, NOT the whole indexed corpus").
    REALISTIC_READ_WINDOW_CHARS = 4000  # ~1000 tokens: one ranged Read/grep-context look, not a whole file
    sizes = {f[0]: f[2] for f in files}
    chars_avoided = sum(min(sizes.get(r["file"], 0), REALISTIC_READ_WINDOW_CHARS) for r in {r["file"]: r for r in results}.values())
    # item 6: an exact, no-model literal search for the query's distinctive terms, run and
    # reported ALONGSIDE the semantic ranking above -- never a replacement for it, so a caller
    # gets both a ranked-by-meaning view and a provable "is this text really there or not" view.
    # A zero on BOTH is what makes "not present" defensible instead of ASSUMED from ranking alone.
    literal_query_terms = literal_terms(query)
    literal_hits = literal_search(root, files, literal_query_terms)
    log_call("grep", sum(f[2] for f in files), len(json.dumps(results)), qv is not None,
             chars_avoided=chars_avoided,
             gen_s=grep_dur, status="partial" if partial else None)
    if json_mode:
        print(json.dumps({"cmd": "grep", **header, "results": results, "backend_ok": qv is not None, "assumed": True,
                          "literal_terms": literal_query_terms, "literal_hits": literal_hits}))
        return
    note = " -- PARTIAL: timed out before all chunks re-embedded, showing best hits so far" if partial else ""
    print(f"[squire] grep indexed {len(files)} files under {root} ({header.get('reembedded_chunks', 0)} chunks "
          f"re-embedded{note}); ranking is ASSUMED (semantic similarity), verify before relying on it")
    for r in results:
        print(f"{r['file']}:{r['line']}  ({r['score']})  {r['snippet']}")
    print(f"[squire] literal (exact, no model) matches for {literal_query_terms}: {len(literal_hits)} hits"
          + (" -- semantic AND literal both zero, absence is provable" if not results and not literal_hits else ""))
    for h in literal_hits:
        print(f"{h['file']}:{h['line']}  {h['snippet']}")


# ---------------------------------------------------------------- triage

HEAD_RE = re.compile(r"^##\s+(?:\[(?P<status>[A-Z]+)[^\]]*\]\s*)?(?P<rest>.*)$")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?")
# `- [ ] **Title** ...` / `- [x] ...` top-level checklist items -- the format TODO-*.md files use
# instead of (or alongside) `## [STATUS]` headings. Only unindented items count as their own
# triage-able entries; a nested/indented `  - [ ]` sub-bullet stays part of the parent item's body.
ITEM_RE = re.compile(r"^-\s*\[(?P<mark>[ xX])\]\s*(?P<rest>.*)$")
BOLD_RE = re.compile(r"\*\*(?P<title>[^*]+)\*\*")


def parse_items(text):
    items, cur, section = [], None, None
    for line in text.splitlines():
        m = HEAD_RE.match(line)
        if m:
            cur = {"status": (m.group("status") or "OPEN"), "heading": line[2:].strip(), "body": [],
                   "kind": "heading", "section": None}
            ts = TS_RE.search(line)
            cur["ts"] = ts.group(0) if ts else None
            items.append(cur)
            section = cur["heading"]
            continue
        im = ITEM_RE.match(line)
        if im:
            mark = im.group("mark")
            status = "DONE" if mark.lower() == "x" else "OPEN"
            rest = im.group("rest").strip()
            bm = BOLD_RE.search(rest)
            title = bm.group("title").strip() if bm else rest
            ts = TS_RE.search(rest)
            cur = {"status": status, "heading": title, "body": [rest], "kind": "item", "section": section}
            cur["ts"] = ts.group(0) if ts else None
            items.append(cur)
            continue
        if cur is not None:
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


def compute_triage(text):
    """Core of `triage`: parse open items, order by real age, guess urgency per item. Shared by
    the CLI command and the S5 queue worker so the two never diverge."""
    items = [i for i in parse_items(text) if i["status"] not in ("DONE", "RESOLVED", "CLOSED")]
    for i in items:
        i["age_days"] = age_days(i["ts"])
    items.sort(key=lambda i: -(i["age_days"] if i["age_days"] is not None else -1))
    guesses = {}
    if items:
        listing = "\n\n".join(f"ITEM {n + 1}: {i['heading']}\n" + "\n".join(i["body"])[:1500] for n, i in enumerate(items[:25]))
        raw = llm("For each ITEM below, write exactly one line 'N: <what it blocks and how urgent, max 20 words>'. "
                  "Use only the item text; say 'unclear' if it does not say.\n\n" + listing, 60 * min(len(items), 25) + 50)
        if not raw.startswith("UNKNOWN:"):
            for line in raw.splitlines():
                # Models echo the template loosely: "3: x", "ITEM 3: x", even "N: ITEM 3: x".
                m = re.search(r"ITEM\s*(\d+)\s*[:.)-]?\s*(.+)", line) or re.match(r"\s*(\d+)\s*[:.)-]\s*(.+)", line)
                if m:
                    guesses[int(m.group(1)) - 1] = m.group(2).strip()
        backend_ok = not raw.startswith("UNKNOWN:")
        reason = None if backend_ok else _unknown_reason(raw)
    else:
        backend_ok, reason = True, None
    for n, i in enumerate(items):
        i["guess"] = guesses.get(n) if backend_ok else None
    return items, backend_ok, reason


def cmd_triage(argv):
    json_mode, argv = pop_flag(argv, "--json")
    if not argv:
        sys.exit("usage: squire triage <file> [--json]")
    text = read_input(argv[0])
    reset_call_timing()
    items, backend_ok, reason = compute_triage(text)
    wait_s, gen_s = get_call_timing()
    log_call("triage", len(text), sum(len(i.get("guess") or "") for i in items), backend_ok, wait_s, gen_s,
              status=reason)
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


# ---------------------------------------------------------------- S5: job queue
# A SQLite spool so a session can `squire submit` a slow sum/ask/draft/diff/triage call and get a
# job id back immediately, instead of blocking on the _LlmLock queue in the foreground. A single
# `squire worker` processes jobs FIFO, reusing the exact same condense/llm code the synchronous
# commands use (and the same _LlmLock), so results are identical either way -- the queue changes
# WHEN the work happens, never what it computes. The synchronous commands are untouched (owner,
# 2026-09-13 mid-task note: S6 will benchmark sync vs queued, so the sync path must not change).
import sqlite3

QUEUE_DB = os.path.expanduser(os.environ.get("SQUIRE_QUEUE_DB", "~/.squire/queue.db"))
WORKER_HEARTBEAT = os.path.expanduser(os.environ.get("SQUIRE_WORKER_HEARTBEAT", "~/.squire/worker.heartbeat"))
HEARTBEAT_STALE_S = float(os.environ.get("SQUIRE_HEARTBEAT_STALE_S", "15"))
WORKER_POLL_S = float(os.environ.get("SQUIRE_WORKER_POLL_S", "1.0"))
QUEUE_CMDS = ("sum", "ask", "draft", "diff", "triage")  # the commands a queued job may run


def _queue_conn():
    os.makedirs(os.path.dirname(QUEUE_DB), mode=0o700, exist_ok=True)
    conn = sqlite3.connect(QUEUE_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cmd TEXT NOT NULL,
        args TEXT NOT NULL,
        input_text TEXT,
        cwd TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        enqueued_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL,
        result TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        worker_pid INTEGER,
        queue_wait_s REAL,
        gen_s REAL
    )""")
    return conn


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _worker_alive():
    try:
        hb = json.load(open(WORKER_HEARTBEAT))
        return _pid_alive(hb.get("pid")) and (time.time() - hb.get("ts", 0)) < HEARTBEAT_STALE_S
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _write_heartbeat():
    try:
        os.makedirs(os.path.dirname(WORKER_HEARTBEAT), mode=0o700, exist_ok=True)
        with open(WORKER_HEARTBEAT, "w") as f:
            json.dump({"pid": os.getpid(), "ts": time.time()}, f)
    except OSError:
        pass


def cmd_submit(argv):
    json_mode, argv = pop_flag(argv, "--json")
    if not argv or argv[0] not in QUEUE_CMDS:
        sys.exit(f"usage: squire submit <{'|'.join(QUEUE_CMDS)}> [args...] [--json]")
    cmd, rest = argv[0], argv[1:]
    # Capture any file/stdin input NOW: a worker running later (possibly as a separate systemd
    # service) has no access to this process's stdin or its file descriptors.
    extra_args, input_text, cwd = [], None, None
    if cmd == "sum":
        input_text = read_input(rest[0] if rest else None)
    elif cmd == "ask":
        if not rest:
            sys.exit('usage: squire submit ask "question" [file|-]')
        extra_args, input_text = [rest[0]], read_input(rest[1] if len(rest) > 1 else None)
    elif cmd == "draft":
        if not rest:
            sys.exit('usage: squire submit draft "instructions" [file|-]')
        extra_args = [rest[0]]
        input_text = read_input(rest[1]) if len(rest) > 1 else ""
    elif cmd == "triage":
        if not rest:
            sys.exit("usage: squire submit triage <file>")
        input_text = read_input(rest[0])
    elif cmd == "diff":
        extra_args, cwd = list(rest), os.getcwd()  # git state is read fresh by the worker
    conn = _queue_conn()
    now = time.time()
    cur = conn.execute("INSERT INTO jobs (cmd, args, input_text, cwd, status, enqueued_at, attempts) "
                       "VALUES (?,?,?,?,'queued',?,0)",
                       (cmd, json.dumps(extra_args), input_text, cwd, now))
    conn.commit()
    job_id = cur.lastrowid
    conn.close()
    if json_mode:
        print(json.dumps({"cmd": "submit", "job_id": job_id, "status": "queued"}))
    else:
        print(f"[squire] submitted job {job_id} ({cmd}), queued")


def _run_queued_job(row):
    """Execute one job's actual work, reusing the same code the synchronous commands call.
    Returns (ok, output_or_error)."""
    cmd, args, text = row["cmd"], json.loads(row["args"]), row["input_text"] or ""
    if cmd == "sum":
        out = condense(text, "Condense to at most 8 factual bullets. Keep numbers, names, paths exact.")
    elif cmd == "ask":
        out = condense(text, f"Answer using ONLY this text; say UNKNOWN if it is not there. Question: {args[0]}")
    elif cmd == "draft":
        out = llm(f"{args[0]}\n\nSource material (may be empty):\n{text[:CHUNK]}", 1200)
    elif cmd == "triage":
        items, backend_ok, _reason = compute_triage(text)
        out = ("UNKNOWN: local model unavailable" if not backend_ok else
               "\n".join(f"[AGE: {i['age_days']}d] [{i['status']}] {i['heading']}"
                        + (f"\n    squire: {i['guess']}" if i['guess'] else "") for i in items))
    elif cmd == "diff":
        gitcmd = ["git", "-C", row["cwd"] or "."]
        gitcmd += ["diff", "--staged"] if args[:1] == ["--staged"] else \
                  (["diff", args[0], args[1]] if len(args) >= 2 else ["diff"])
        p = subprocess.run(gitcmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        diff_text = p.stdout or ""
        out = "(no diff)" if not diff_text.strip() else condense_verified(diff_text,
              "Summarize this git diff by file/module. Separate logic changes from formatting/rename-only "
              "changes. Be factual, keep exact file paths and names.")[0]
    else:
        return False, f"unknown queued command {cmd!r}"
    return not out.startswith("UNKNOWN:"), out


def _requeue_dead_workers(conn):
    for row in conn.execute("SELECT id, worker_pid FROM jobs WHERE status='running'"):
        if not _pid_alive(row["worker_pid"]):
            conn.execute("UPDATE jobs SET status='queued', worker_pid=NULL, started_at=NULL, "
                        "attempts=attempts+1 WHERE id=? AND status='running'", (row["id"],))
    conn.commit()


def _claim_next_job(conn):
    _requeue_dead_workers(conn)
    row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    conn.execute("UPDATE jobs SET status='running', started_at=?, worker_pid=? WHERE id=? AND status='queued'",
                (time.time(), os.getpid(), row["id"]))
    conn.commit()
    return conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone() if conn.total_changes else None


def cmd_worker(argv):
    """S12 fix: the heartbeat used to be written only once per loop iteration, BEFORE
    claiming a job -- so a job whose model call ran longer than HEARTBEAT_STALE_S
    (default 15s) let the heartbeat go stale WHILE THE WORKER WAS ACTIVELY WORKING,
    and `squire status`/`squire wait` reported UNKNOWN ("no worker heartbeat") for a
    job that was in fact running normally. Found via scripts/benchmark.py --mode
    queue: 14b model calls routinely take 12-70s (docs/BENCHMARK.md latency table),
    all comfortably longer than the old 15s staleness window, so this was a
    near-certain false UNKNOWN on any real job, not an edge case. Fix: a background
    thread refreshes the heartbeat on a fixed cadence independent of job duration,
    so staleness now only ever means "the worker process is actually gone/hung",
    which is what it is supposed to mean."""
    _once, argv = pop_flag(argv, "--once")  # process at most one job then exit; for tests/CI
    conn = _queue_conn()
    print(f"[squire] worker started, pid={os.getpid()}, db={QUEUE_DB}")
    stop_heartbeat = threading.Event()

    def _heartbeat_loop():
        while not stop_heartbeat.is_set():
            _write_heartbeat()
            stop_heartbeat.wait(max(1.0, HEARTBEAT_STALE_S / 3))

    hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb_thread.start()
    try:
        while True:
            job = _claim_next_job(conn)
            if job is None:
                if _once:
                    return
                time.sleep(WORKER_POLL_S)
                continue
            reset_call_timing()
            try:
                ok, out = _run_queued_job(job)
                error = None if ok else out
                result = out if ok else None
            except Exception as e:  # noqa: BLE001 -- a job must never crash the worker loop
                ok, result, error = False, None, f"{type(e).__name__}: {e}"
            wait_s, gen_s = get_call_timing()
            finished = time.time()
            conn.execute("UPDATE jobs SET status=?, result=?, error=?, finished_at=?, queue_wait_s=?, gen_s=? "
                        "WHERE id=?",
                        ("done" if ok else "failed", result, error, finished, wait_s, gen_s, job["id"]))
            conn.commit()
            finished_job = conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            log_call(f"queue:{job['cmd']}", len(job["input_text"] or ""), len(result or error or ""), ok,
                     wait_s, gen_s, source="queue", job=finished_job)
            if _once:
                return
    finally:
        stop_heartbeat.set()


def _job_position(conn, job):
    if job["status"] != "queued":
        return 0
    row = conn.execute("SELECT COUNT(*) c FROM jobs WHERE status='queued' AND id<?", (job["id"],)).fetchone()
    return row["c"] + 1


def _job_age(job):
    return round(time.time() - job["enqueued_at"], 1)


def _emit_job_state(job, conn, json_mode):
    if job["status"] == "done":
        if json_mode:
            print(json.dumps({"cmd": "status", "id": job["id"], "state": "RESULT", "result": job["result"]}))
        else:
            print(job["result"])
        return 0
    if job["status"] == "failed":
        if json_mode:
            print(json.dumps({"cmd": "status", "id": job["id"], "state": "FAIL", "error": job["error"]}))
        else:
            print(f"[squire] FAIL: {job['error']}")
        return 1
    alive = _worker_alive()
    pos, age = _job_position(conn, job), _job_age(job)
    if not alive:
        if json_mode:
            print(json.dumps({"cmd": "status", "id": job["id"], "state": "UNKNOWN",
                              "reason": "no worker heartbeat", "position": pos, "age_s": age}))
        else:
            print(f"[squire] UNKNOWN: no worker heartbeat (job {job['id']} {job['status']}, "
                 f"position {pos}, age {age}s)")
        return 3
    if json_mode:
        print(json.dumps({"cmd": "status", "id": job["id"], "state": "WAIT", "status": job["status"],
                          "position": pos, "age_s": age}))
    else:
        print(f"[squire] WAIT: job {job['id']} {job['status']}, position {pos}, age {age}s")
    return 2


def _parse_job_id(raw, usage):
    # argv[0] can be "" (an empty positional slipped through, e.g. `squire status ''`) or any
    # other non-numeric junk. int("") / int("abc") raise an uncaught ValueError that crashes with
    # a Python traceback instead of squire's own clean usage error -- found 2026-09-13 (S10/S7).
    try:
        return int(raw)
    except (TypeError, ValueError):
        sys.exit(f"usage: {usage} (id must be a job number, got {raw!r})")


def cmd_status(argv):
    json_mode, argv = pop_flag(argv, "--json")
    if not argv:
        sys.exit("usage: squire status <id> [--json]")
    job_id = _parse_job_id(argv[0], "squire status <id> [--json]")
    conn = _queue_conn()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None:
        sys.exit(f"job {argv[0]} not found")
    sys.exit(_emit_job_state(job, conn, json_mode))


def cmd_wait(argv):
    json_mode, argv = pop_flag(argv, "--json")
    timeout, argv = pop_opt(argv, "--timeout", "300")
    if not argv:
        sys.exit("usage: squire wait <id> [--timeout S] [--json]")
    job_id = _parse_job_id(argv[0], "squire wait <id> [--timeout S] [--json]")
    timeout = float(timeout)
    conn = _queue_conn()
    deadline = time.monotonic() + timeout
    while True:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            sys.exit(f"job {job_id} not found")
        if job["status"] in ("done", "failed"):
            sys.exit(_emit_job_state(job, conn, json_mode))
        if not _worker_alive():
            sys.exit(_emit_job_state(job, conn, json_mode))  # UNKNOWN -- no point waiting further
        if time.monotonic() >= deadline:
            sys.exit(_emit_job_state(job, conn, json_mode))  # WAIT -- still queued/running
        time.sleep(min(0.5, max(0.05, deadline - time.monotonic())))


def cmd_jobs(argv):
    json_mode, _ = pop_flag(argv, "--json")
    conn = _queue_conn()
    rows = [dict(r) for r in conn.execute("SELECT id, cmd, status, enqueued_at, started_at, finished_at, "
                                          "attempts FROM jobs ORDER BY id")]
    if json_mode:
        print(json.dumps({"cmd": "jobs", "jobs": rows}))
        return
    if not rows:
        print("[squire] no jobs")
        return
    for r in rows:
        print(f"[{r['id']:>4}] {r['status']:8} {r['cmd']:6} enqueued {round(time.time() - r['enqueued_at'], 1)}s ago"
             + (f", attempts={r['attempts']}" if r["attempts"] else ""))


# ---------------------------------------------------------------- stats / doctor

def cmd_stats(argv):
    json_mode, _ = pop_flag(argv, "--json")
    rows = []
    if os.path.exists(LEDGER):
        all_rows = [json.loads(ln) for ln in open(LEDGER) if ln.strip()]
        rows = [r for r in all_rows if not is_test_row(r)]
        excluded_test = len(all_rows) - len(rows)
    else:
        excluded_test = 0
    # Short `squire run` passthroughs are logged so usage is counted, but they saved nothing, so they
    # are reported as a separate count and kept out of every savings total below.
    passthrough_calls = sum(1 for r in rows if r.get("passthrough"))
    rows = [r for r in rows if not r.get("passthrough")]
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
        print(json.dumps({"cmd": "stats", "ledger": LEDGER, "calls": len(rows),
                          "passthrough_calls": passthrough_calls, "backend_ok_calls": ok,
                          "chars_in": total_in, "chars_out": total_out, "chars_saved": total_in - total_out,
                          "by_cmd": by_cmd, "estimated_tokens_saved": est, "excluded_test_rows": excluded_test,
                          "estimate_method": f"chars/{CHARS_PER_TOKEN}, per-read only; not a measured token count"}))
        return
    if not rows:
        print(f"[squire] no ledger entries yet -- run some commands first"
              + (f" ({excluded_test} test rows excluded)" if excluded_test else ""))
        return
    print(f"[squire] {len(rows)} model-backed calls logged ({ok} backend-ok, {len(rows) - ok} UNKNOWN), plus "
          f"{passthrough_calls} short passthrough runs -- REAL, computed from {LEDGER}"
          + (f" ({excluded_test} test rows excluded, not deleted)" if excluded_test else ""))
    for name, c in sorted(by_cmd.items()):
        print(f"  {name:7} calls={c['calls']:<5} chars in={c['chars_in']} out={c['chars_out']}")
    print(f"[squire] chars in={total_in} out={total_out} saved={total_in - total_out} (REAL character counts)")
    print(f"[squire] ESTIMATED tokens saved: ~{est} "
          f"(chars/{CHARS_PER_TOKEN} heuristic, per read -- NOT a measured token count; every later turn "
          "re-reads context, so real savings are larger. Use scripts/squire_report.py for the measured view)")


def _warmup_result(json_mode, ok, reason, duration_s=None):
    if json_mode:
        print(json.dumps({"cmd": "warmup", "ok": ok, "reason": reason, "duration_s": duration_s}))
    elif ok:
        print(f"[squire] warmup OK in {duration_s}s (model {model()} now resident, keep_alive={KEEP_ALIVE})")
    else:
        print(f"[squire] warmup skipped: {reason}")


def cmd_warmup(argv):
    """Opt-in warm start (item 4, 2026-09-15 usage-analysis): a cold Ollama load costs ~75s
    (docs/BENCHMARK.md); paying that on the FIRST real sum/ask/draft/grep call of a session makes
    that call look like squire is slow, when it's really a one-time model-load cost. `squire
    warmup` is never called automatically -- a session/hook opts in by invoking it explicitly
    (e.g. at session start).

    Default behavior backgrounds the actual work in a detached child process and returns
    immediately, so THIS invocation can never add latency anywhere -- the whole point is to move
    the 75s cost earlier and off the critical path, not to make some other command wait for it.
    `--wait` runs synchronously (used internally by the backgrounded child, or directly when a
    caller genuinely wants to block until the model is confirmed resident, e.g. in a test).

    Two hard requirements: never touch the network when SQUIRE_HOOK_DISABLE=1 (checked before
    anything else, so this is a true silent no-op, matching the three PreToolUse hooks' own kill
    switch); and never attempt to load a model when the backend is down (checked with a cheap
    `installed_models()` call BEFORE the actual warm-up generate call, so a down backend fails in
    well under a second instead of waiting through llm()'s own retry/backoff for nothing)."""
    json_mode, argv = pop_flag(argv, "--json")
    wait, argv = pop_flag(argv, "--wait")
    if os.environ.get("SQUIRE_HOOK_DISABLE") == "1":
        return  # silent no-op: hooks are off, so warmup makes no network call on this session's behalf
    if not wait:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "warmup", "--wait"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        if json_mode:
            print(json.dumps({"cmd": "warmup", "backgrounded": True}))
        else:
            print("[squire] warmup started in the background")
        return
    try:
        installed_models()
    except Exception as e:  # noqa: BLE001 - backend down; never try to load a model in this state
        _warmup_result(json_mode, False, f"backend unreachable ({type(e).__name__}: {e})")
        return
    paused = gpu_paused_by()
    if paused:
        _warmup_result(json_mode, False, f"GPU in use by {paused}")
        return
    t0 = time.monotonic()
    out = llm("Reply with the single word OK.", 5)
    ok = not out.startswith("UNKNOWN:")
    _warmup_result(json_mode, ok, None if ok else out, round(time.monotonic() - t0, 1))


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
    paused_by = gpu_paused_by()
    report["paused_by"] = paused_by
    ready = report["backend_ok"] and report["model_installed"] and paused_by is None
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
        if paused_by:
            print(f"[squire] PAUSED: GPU held by {paused_by}; model commands return UNKNOWN, `squire run` passthrough still works")
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
    handlers = {"diff": cmd_diff, "grep": cmd_grep, "triage": cmd_triage, "stats": cmd_stats, "doctor": cmd_doctor,
                "submit": cmd_submit, "worker": cmd_worker, "status": cmd_status, "wait": cmd_wait, "jobs": cmd_jobs,
                "warmup": cmd_warmup}
    if cmd in handlers:
        return handlers[cmd](rest)
    json_mode, rest = pop_flag(rest, "--json")
    reset_call_timing()
    task = None  # non-None for sum/ask: marks them cacheable and citation-instructed; draft isn't
    if cmd == "sum":
        text = read_input(rest[0] if rest else None)
        task = f"Condense to at most 8 factual bullets. Keep numbers, names, paths exact. {CITE_INSTRUCTION}"
    elif cmd == "ask":
        if not rest:
            sys.exit('usage: squire ask "question" [file|-] [--json]')
        text = read_input(rest[1] if len(rest) > 1 else None)
        task = (f"Answer using ONLY this text; say UNKNOWN if it is not there. {CITE_INSTRUCTION} "
                f"Question: {rest[0]}")
    elif cmd == "draft":
        if not rest:
            sys.exit('usage: squire draft "instructions" [file|-] [--json]')
        text = read_input(rest[1]) if len(rest) > 1 else ""
        out = llm(f"{rest[0]}\n\nSource material (may be empty):\n{text[:CHUNK]}", 1200)
    else:
        sys.exit(f"unknown command {cmd!r}; see squire --help")
    cache_hit = False
    if task is not None:
        key = cache_key(cmd, text, task)
        cached = cache_get(key)
        if cached is not None:
            out, cache_hit = cached, True
        else:
            out = condense(add_line_numbers(text), task)
            if not out.startswith("UNKNOWN:"):
                cache_put(key, out)
    ok = not out.startswith("UNKNOWN:")
    wait_s, gen_s = get_call_timing()
    log_call(cmd, len(text), len(out), ok, wait_s, gen_s,
              status="cached" if cache_hit else (None if ok else _unknown_reason(out)))
    if json_mode:
        print(json.dumps({"cmd": cmd, "exit_code": None, "raw_tail": None, "summary": out,
                          "assumed": True, "backend_ok": ok, "verify_flag": None, "cache_hit": cache_hit}))
    else:
        # `ask`'s own prompt tells the model to answer the bare word "UNKNOWN" when the text
        # genuinely doesn't contain the answer -- a healthy backend giving a real answer, not a
        # failure (a failure sentinel always reads "UNKNOWN: <reason>" and prints as-is below).
        # Print it distinctly so it never reads like the backend-down case.
        if cmd == "ask" and out == "UNKNOWN":
            print("[squire] not found in the given text (ASSUMED: model checked, backend OK)")
        else:
            print(out if cmd == "draft" else "[squire] " + out)


if __name__ == "__main__":
    main()

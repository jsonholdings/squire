"""Safety controls for Squire. None of these need a running model."""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SQUIRE = Path(__file__).resolve().parents[1] / "squire.py"
# A dedicated lock file, never the real ~/.squire/llm.lock. Without this, every test process in
# this file competes for the SAME flock as any OTHER squire process on the workstation -- other
# sessions' real (backend-up) calls under load can hold it for the full LLM_LOCK_TIMEOUT (240s
# default), so a DOWN-backend test here queues behind real work and blows its own subprocess
# timeout. Root-caused 2026-09-13 (S7) as the actual cause of the "5 tests TimeoutExpired under
# load" finding from S6b/S9 -- it was lock contention with concurrent real usage, not flakiness
# in squire's own logic.
_LOCK_DIR = tempfile.mkdtemp(prefix="squire-test-lock-")
DOWN = {**os.environ, "SQUIRE_OLLAMA": "http://127.0.0.1:1",  # nothing listens on port 1
        "SQUIRE_LLM_LOCK": os.path.join(_LOCK_DIR, "llm.lock")}

# Temp repos get their own identity and ignore the machine's git config. A CI runner has no
# user.name/email, so `git commit` there exits 128. These tests once passed only on machines
# that happened to have a global identity (the CI run failed, 2026-09-12).
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "squire tests", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
           "GIT_COMMITTER_NAME": "squire tests", "GIT_COMMITTER_EMAIL": "tests@example.invalid"}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
                   check=True, env=GIT_ENV)


def run(*args, stdin=None, env=None):
    return subprocess.run([sys.executable, str(SQUIRE), *args], input=stdin, text=True,
                          capture_output=True, env=env or DOWN, timeout=60)


def test_short_output_passes_through_raw():
    p = run("run", "--", "echo", "hello")
    assert p.returncode == 0
    assert "[squire] exit=0" in p.stdout and "hello" in p.stdout
    assert "local summary" not in p.stdout  # no model call for short output


def test_exit_code_is_preserved():
    p = run("run", "--", sys.executable, "-c", "import sys; print('boom'); sys.exit(3)")
    assert p.returncode == 3
    assert "[squire] exit=3" in p.stdout


FAKE_LAUNCHER = Path(__file__).resolve().parent / "fixtures" / "fake_nested_launcher.py"


def test_nested_launcher_exit_code_preserved():
    # Stands in for `flatpak-spawn --host docker run ...`: a launcher wrapping a real command.
    p = run("run", "--", sys.executable, str(FAKE_LAUNCHER), "5")
    assert p.returncode == 5
    assert "[squire] exit=5" in p.stdout


def test_nested_launcher_long_output_exit_code_preserved():
    # exit code 77 makes the fixture print 80 lines first, crossing the short/long threshold --
    # the >60-line summarization path must still preserve the real nested exit code.
    p = run("run", "--", sys.executable, str(FAKE_LAUNCHER), "77")
    assert p.returncode == 77
    assert "nested output line 79" in p.stdout  # raw tail is always shown


def test_nested_launcher_does_not_hang_on_inherited_stdin():
    # A launcher that attaches stdin (docker -i, ssh, some flatpak-spawn entry points) inherits
    # whatever fd squire got. Before the stdin=DEVNULL fix, an open PIPE stdin that squire's own
    # process is handed (as happens in a real interactive terminal) meant the nested process
    # blocked forever waiting for EOF -- indistinguishable from squire being broken. Give squire
    # an explicit open PIPE (never closed by this test) and bound the wait with a short timeout:
    # this test must finish quickly, proving squire itself closed stdin for the child rather than
    # forwarding this never-EOF pipe.
    p = subprocess.run([sys.executable, str(SQUIRE), "run", "--", sys.executable,
                        str(FAKE_LAUNCHER), "--read-stdin", "0"],
                       stdin=subprocess.PIPE, text=True, capture_output=True,
                       env=DOWN, timeout=15)
    assert p.returncode == 0
    assert "got 0 bytes from stdin" in p.stdout


def test_run_timeout_preserves_exit_code_and_output():
    code = "import sys, time\nprint('before sleep'); sys.stdout.flush()\ntime.sleep(5)\nsys.exit(0)"
    env = {**DOWN, "SQUIRE_RUN_TIMEOUT": "1"}
    p = run("run", "--", sys.executable, "-c", code, env=env)
    assert p.returncode == 124
    assert "before sleep" in p.stdout  # captured output before the kill is never lost


def test_long_output_keeps_raw_tail_and_exit_even_when_model_is_down():
    code = "import sys\nfor i in range(200): print('line', i)\nprint('FAILED t.py::x')\nsys.exit(1)"
    p = run("run", "--", sys.executable, "-c", code)
    assert p.returncode == 1
    assert "FAILED t.py::x" in p.stdout  # the raw tail is always shown
    assert "UNKNOWN" in p.stdout  # a down model is reported, never silent


def test_sum_reports_unknown_when_backend_down():
    p = run("sum", "-", stdin="some text\n")
    assert "UNKNOWN" in p.stdout


def test_help_lists_commands():
    p = run("--help")
    for word in ("run", "sum", "ask", "draft", "diff", "stats"):
        assert word in p.stdout


def test_json_mode_emits_valid_json_with_required_fields():
    import json
    p = run("sum", "-", "--json", stdin="some text\n")
    obj = json.loads(p.stdout)
    for key in ("cmd", "exit_code", "raw_tail", "summary", "assumed", "backend_ok"):
        assert key in obj
    assert obj["backend_ok"] is False  # backend is down in this test env


def test_diff_reports_no_diff_on_clean_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    p = subprocess.run([sys.executable, str(SQUIRE), "diff"], cwd=tmp_path, text=True,
                       capture_output=True, env=DOWN, timeout=60)
    assert "no diff" in p.stdout


def test_diff_shows_real_stat_even_when_model_is_down(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "f.txt").write_text("a\n")
    git(tmp_path, "add", "f.txt")
    git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "f.txt").write_text("a\nb\n")
    p = subprocess.run([sys.executable, str(SQUIRE), "diff"], cwd=tmp_path, text=True,
                       capture_output=True, env=DOWN, timeout=60)
    assert "f.txt" in p.stdout  # real diffstat always shown
    assert "UNKNOWN" in p.stdout  # backend down is reported, never silent


def test_stats_reports_real_chars_and_labels_estimate_separately(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"ts": 1, "cmd": "sum", "chars_in": 100, "chars_out": 20, "backend_ok": true}\n')
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("stats", env=env)
    assert "chars in=100 out=20 saved=80" in p.stdout  # REAL, computed
    assert "ESTIMATED" in p.stdout  # the token figure must never look like a measured fact
    assert "NOT a measured token count" in p.stdout


# ---- v0.2 core: redaction, localhost guard, auto model, triage, grep/doctor UNKNOWN paths ----
sys.path.insert(0, str(SQUIRE.parent))
import squire as sq  # noqa: E402


def test_redact_removes_secret_shapes_and_keeps_context():
    text = ("key github_pat_" + "A" * 30 + " and ghp_" + "b" * 36 + "\npassword = hunter2hunter2\n"
            "AKIA" + "ABCDEFGHIJKLMNOP" + "\n-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    out = sq.redact(text)
    for leaked in ("github_pat_AAAA", "ghp_bbbb", "hunter2hunter2", "AKIAABCDEFGHIJKLMNOP", "BEGIN RSA"):
        assert leaked not in out
    assert "password = [REDACTED]" in out and out.count("[REDACTED]") >= 5


def test_llm_returns_unknown_when_gpu_paused(monkeypatch, tmp_path):
    flag = tmp_path / "gpu-paused.json"
    flag.write_text(json.dumps({"owner": "ncp-archpreview", "pid": os.getpid()}))
    monkeypatch.setattr(sq, "GPU_PAUSE_FLAG", str(flag))

    def fail_if_called(*a, **k):
        raise AssertionError("llm() must not call the network while GPU-paused")
    monkeypatch.setattr(sq, "_post", fail_if_called)

    out = sq.llm("hello")
    assert out.startswith("UNKNOWN") and "ncp-archpreview" in out


def test_embed_raises_when_gpu_paused(monkeypatch, tmp_path):
    flag = tmp_path / "gpu-paused.json"
    flag.write_text(json.dumps({"owner": "ncp-archpreview", "pid": os.getpid()}))
    monkeypatch.setattr(sq, "GPU_PAUSE_FLAG", str(flag))
    monkeypatch.setattr(sq, "_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    with pytest.raises(sq.BackendError, match="ncp-archpreview"):
        sq.embed(["text"])


def test_gpu_paused_by_ignores_stale_pid(monkeypatch, tmp_path):
    # Control: a pid that cannot possibly be alive must be treated as unpaused, AND the stale
    # flag file must be cleaned up -- proves the check can return both "paused" and "not paused".
    flag = tmp_path / "gpu-paused.json"
    dead_pid = 999999
    flag.write_text(json.dumps({"owner": "ncp-archpreview", "pid": dead_pid}))
    monkeypatch.setattr(sq, "GPU_PAUSE_FLAG", str(flag))
    assert sq.gpu_paused_by() is None
    assert not flag.exists()

    # control: a live pid (our own) is correctly reported as still paused
    flag.write_text(json.dumps({"owner": "ncp-archpreview", "pid": os.getpid()}))
    assert sq.gpu_paused_by() == "ncp-archpreview"


def test_gpu_pause_does_not_block_calls_to_the_paused_owners_own_api(monkeypatch, tmp_path):
    # When Squire is benchmarked WITH NCP-ArchPreview as its backend, the pause flag NCP wrote
    # for itself must not block Squire's own calls to that same server -- the pause exists to
    # keep Ollama off the GPU, not to block talking to the paused owner directly.
    flag = tmp_path / "gpu-paused.json"
    flag.write_text(json.dumps({"owner": "ncp-archpreview", "pid": os.getpid(),
                                 "api_base": "http://127.0.0.1:8790/v1"}))
    monkeypatch.setattr(sq, "GPU_PAUSE_FLAG", str(flag))
    monkeypatch.setattr(sq, "BACKEND", "openai")
    monkeypatch.setattr(sq, "OPENAI_BASE", "http://127.0.0.1:8790/v1")
    assert sq.gpu_paused_by() is None

    called = {}

    def fake_post(url, payload, timeout=300):
        called["url"] = url
        return {"choices": [{"message": {"content": "real answer"}}]}
    monkeypatch.setattr(sq, "_post", fake_post)
    assert sq.llm("hello") == "real answer"
    assert called["url"].startswith("http://127.0.0.1:8790/v1")


def test_llm_think_field_only_sent_when_squire_think_set(monkeypatch, tmp_path):
    # SQUIRE_THINK is unset by default: the "think" field must be absent from the payload so a
    # model's own default behavior is unchanged for every existing caller. Setting SQUIRE_THINK
    # to a falsy value (used for qwen3's non-thinking mode in the 2026-09-15 model bake-off) must
    # add "think": False; setting it truthy must add "think": True.
    flag = tmp_path / "gpu-paused.json"  # doesn't exist -- not paused
    monkeypatch.setattr(sq, "GPU_PAUSE_FLAG", str(flag))
    monkeypatch.setattr(sq, "BACKEND", "ollama")

    captured = {}

    def fake_post(url, payload, timeout=300):
        captured["payload"] = payload
        return {"response": "ok"}
    monkeypatch.setattr(sq, "_post", fake_post)

    monkeypatch.setattr(sq, "THINK", None)
    sq.llm("hello")
    assert "think" not in captured["payload"]

    monkeypatch.setattr(sq, "THINK", False)
    sq.llm("hello")
    assert captured["payload"]["think"] is False

    monkeypatch.setattr(sq, "THINK", True)
    sq.llm("hello")
    assert captured["payload"]["think"] is True


def test_gpu_pause_still_blocks_openai_backend_pointed_elsewhere(monkeypatch, tmp_path):
    # Control for the test above: an "openai" backend pointed at a DIFFERENT server than the
    # paused owner's own api_base must still be blocked -- proves the check isn't just "backend
    # is openai, always allow".
    flag = tmp_path / "gpu-paused.json"
    flag.write_text(json.dumps({"owner": "ncp-archpreview", "pid": os.getpid(),
                                 "api_base": "http://127.0.0.1:8790/v1"}))
    monkeypatch.setattr(sq, "GPU_PAUSE_FLAG", str(flag))
    monkeypatch.setattr(sq, "BACKEND", "openai")
    monkeypatch.setattr(sq, "OPENAI_BASE", "http://127.0.0.1:9999/v1")

    def fail_if_called(*a, **k):
        raise AssertionError("must not call an unrelated openai backend while GPU-paused")
    monkeypatch.setattr(sq, "_post", fail_if_called)

    out = sq.llm("hello")
    assert out.startswith("UNKNOWN") and "ncp-archpreview" in out


def test_pid_alive_falls_back_to_flatpak_spawn_host(monkeypatch):
    # A container's docker-reported pid lives in the HOST pid namespace and is invisible to a
    # plain os.kill from inside Claude Code's sandboxed Bash tool (found 2026-09-13: a running
    # container's pid raised ProcessLookupError). _pid_alive must fall back to
    # `flatpak-spawn --host kill -0` rather than declaring it stale.
    def fake_kill(pid, sig):
        raise ProcessLookupError()
    monkeypatch.setattr(sq.os, "kill", fake_kill)

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeResult()
    monkeypatch.setattr(sq.subprocess, "run", fake_run)

    assert sq._docker_pid_alive(12345) is True
    assert calls and calls[0][:3] == ["flatpak-spawn", "--host", "kill"]

    # control: the host-escape path also correctly reports a truly dead pid
    class FakeDeadResult:
        returncode = 1
    monkeypatch.setattr(sq.subprocess, "run", lambda cmd, **k: FakeDeadResult())
    assert sq._docker_pid_alive(12345) is False


def test_remote_backend_is_refused_unless_allowed(monkeypatch):
    import pytest
    with pytest.raises(sq.BackendError):
        sq.check_local("http://example.com:11434/api/generate")
    monkeypatch.setenv("SQUIRE_ALLOW_HOSTS", "ollama")
    sq.check_local("http://ollama:11434/api/generate")
    sq.check_local("http://127.0.0.1:11434")


def test_remote_backend_reports_unknown_not_a_network_call():
    # Must isolate SQUIRE_LLM_LOCK like DOWN does (S7) -- this test builds its own env from
    # os.environ instead of DOWN and was missed by that fix, so it shared the real
    # ~/.squire/llm.lock with any other squire process on the workstation. Under real
    # concurrent load that queued this call behind unrelated real work and blew its own 60s
    # subprocess timeout -- found 2026-09-13 (S8), same root cause as S7's benchmark miss.
    env = {**os.environ, "SQUIRE_OLLAMA": "http://203.0.113.9:11434",  # TEST-NET, must never be contacted
           "SQUIRE_LLM_LOCK": os.path.join(_LOCK_DIR, "llm.lock")}
    p = run("sum", "-", stdin="text\n", env=env)
    assert "UNKNOWN" in p.stdout and "not localhost" in p.stdout


def test_auto_model_picks_largest_installed_tier_that_fits():
    installed = ["qwen2.5:7b", "qwen2.5:14b", "nomic-embed-text:latest"]
    assert sq.pick_model(installed, 24000) == "qwen2.5:14b"  # 32b not installed
    assert sq.pick_model(installed, 8000) == "qwen2.5:7b"
    assert sq.pick_model(installed, None) == "qwen2.5:14b"  # unknown VRAM: largest installed tier
    assert sq.pick_model(["llama3:8b"], 24000) == "llama3:8b"


def test_triage_orders_by_real_age_and_skips_done(tmp_path):
    f = tmp_path / "INBOX.md"
    f.write_text("## [OPEN] 2026-09-10T10:00:00-04:00 — newer\nbody\n"
                 "## [DONE 2026-09-11] 2026-09-01T00:00:00Z — closed\n"
                 "## [OPEN] 2026-09-01T10:00:00-04:00 — older\nbody\n")
    import json as _j
    p = run("triage", str(f), "--json")
    obj = _j.loads(p.stdout)
    titles = [i["heading"] for i in obj["open_items"]]
    assert len(titles) == 2 and "older" in titles[0] and "newer" in titles[1]
    assert obj["backend_ok"] is False and all(i["guess"] is None for i in obj["open_items"])
    assert obj["open_items"][0]["age_days"] > obj["open_items"][1]["age_days"]


def test_grep_backend_down_is_unknown_exit_2_and_reports_coverage(tmp_path):
    (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path)],
                       text=True, capture_output=True, env=DOWN, timeout=60)
    assert p.returncode == 2
    assert "indexed 1 files" in p.stdout and "UNKNOWN" in p.stdout


def test_grep_backend_failure_still_logs_a_ledger_row(tmp_path):
    # S10 (found 2026-09-13): a `squire grep` that fails/times out talking to the embedding
    # backend used to die inside the BackendError branch with NO ledger row at all -- from the
    # ledger's side that run was indistinguishable from one that never happened. Control: a
    # SUCCESSFUL grep (backend up would log too) isn't available here (no real backend in CI),
    # so the control is the ledger starting empty -- proving any row present came from this run.
    (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    assert not ledger.exists()  # control: nothing logged yet
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path)],
                       text=True, capture_output=True, env=env, timeout=60)
    assert p.returncode == 2
    import json as _j
    rows = [_j.loads(l) for l in ledger.read_text().splitlines()]
    grep_rows = [r for r in rows if r["cmd"] == "grep"]
    assert len(grep_rows) == 1
    assert grep_rows[0]["backend_ok"] is False
    assert grep_rows[0]["status"] in ("timeout", "error")
    assert grep_rows[0]["gen_s"] is not None and grep_rows[0]["gen_s"] >= 0


# ---------------------------------------------------------------- S11: grep lock/timeout fixes

class _FakeOllama(BaseHTTPRequestHandler):
    """Minimal /api/embed + /api/generate stand-in so grep's --timeout and lock-separation
    behavior can be tested without a real model. `delay_s` (class attr, set per test) simulates
    a slow/stuck backend call -- the real-world failure mode found in the ledger (S11: two
    real grep calls tonight logged status=timeout, gen_s=600.1, proving the embed HTTP call
    itself hung for the full old 600s timeout, not just a lock queue)."""
    delay_s = 0.0

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        time.sleep(self.delay_s)
        if self.path == "/api/embed":
            n = len(body.get("input", []))
            data = json.dumps({"embeddings": [[1.0, 0.0] for _ in range(n)]}).encode()
        else:
            data = json.dumps({"response": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_ollama():
    _FakeOllama.delay_s = 0.0
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", _FakeOllama
    server.shutdown()
    t.join(timeout=5)


def test_embed_and_chat_use_separate_lock_files_by_default():
    # S11: embed() used to share LLM_LOCK_PATH with llm() (chat generation). A single slow embed
    # call holding that ONE lock for its whole HTTP timeout blocked every other squire call on
    # the workstation (run/sum/ask/diff/grep) for as long as the embed call took. Control: prove
    # the two constants really differ (a no-op fix would leave them equal and this would catch it).
    assert sq.EMBED_LOCK_PATH != sq.LLM_LOCK_PATH


def test_embed_does_not_block_behind_a_held_chat_lock(tmp_path, fake_ollama):
    base, handler = fake_ollama
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    llm_lock = str(lock_dir / "llm.lock")
    embed_lock = str(lock_dir / "embed.lock")
    env = {**os.environ, "SQUIRE_OLLAMA": base, "SQUIRE_LLM_LOCK": llm_lock,
           "SQUIRE_EMBED_LOCK": embed_lock, "SQUIRE_LLM_LOCK_TIMEOUT": "5"}
    # Hold the CHAT lock from this test process for longer than embed should ever need to wait.
    held = open(llm_lock, "a+")
    os.makedirs(os.path.dirname(llm_lock), exist_ok=True)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path)],
                           text=True, capture_output=True, env=env, timeout=30)
        elapsed = time.monotonic() - t0
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
    # Before the fix this would have queued behind the held chat lock for LLM_LOCK_TIMEOUT (5s
    # here); with a separate embed lock it completes almost immediately regardless.
    assert p.returncode == 0, p.stdout + p.stderr
    assert elapsed < 4, f"embed waited on the chat lock: {elapsed}s"


def test_grep_timeout_returns_partial_hits_not_nothing(tmp_path, fake_ollama):
    base, handler = fake_ollama
    handler.delay_s = 0.3  # each /api/embed call (one per GREP_BATCH-sized batch) takes 0.3s
    # 70 single-window files = 70 chunks = 3 batches at GREP_BATCH=32 (32, 32, 6). With a 0.5s
    # deadline: batch 1 (elapsed 0<0.5) and batch 2 (elapsed ~0.3<0.5) run; the elapsed-~0.6s
    # check before batch 3 trips the deadline, so it's skipped -- exercising the mid-run break.
    for i in range(70):
        (tmp_path / f"f{i}.py").write_text(f"def frobulate_widget_{i}():\n    pass\n")
    ledger = tmp_path / "l.jsonl"
    env = {**os.environ, "SQUIRE_OLLAMA": base, "SQUIRE_LLM_LOCK": str(tmp_path / "llm.lock"),
           "SQUIRE_EMBED_LOCK": str(tmp_path / "embed.lock"), "SQUIRE_LEDGER": str(ledger)}
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path),
                       "--timeout", "0.5", "--json"], text=True, capture_output=True, env=env, timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    obj = json.loads(p.stdout)
    assert obj["partial"] is True
    assert obj["results"] != []  # control: a timeout that returns NOTHING would defeat the point
    rows = [json.loads(l) for l in ledger.read_text().splitlines() if json.loads(l)["cmd"] == "grep"]
    assert rows[-1]["status"] == "partial"


def test_grep_no_timeout_still_returns_full_results_control(tmp_path, fake_ollama):
    # Control for the test above: WITHOUT --timeout, the same repo/backend finishes normally and
    # is NOT marked partial -- proves partial=True above is caused by the deadline, not a bug.
    base, handler = fake_ollama
    handler.delay_s = 0.05
    (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
    env = {**os.environ, "SQUIRE_OLLAMA": base, "SQUIRE_LLM_LOCK": str(tmp_path / "llm.lock"),
           "SQUIRE_EMBED_LOCK": str(tmp_path / "embed.lock")}
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path), "--json"],
                       text=True, capture_output=True, env=env, timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    obj = json.loads(p.stdout)
    assert obj["partial"] is False
    assert len(obj["results"]) == 1


def test_grep_cache_hit_skips_reembedding_control(tmp_path, fake_ollama):
    # Control proving the persistent per-tree cache (keyed mtime+size) actually works: a second
    # grep over an UNCHANGED file re-embeds 0 chunks (warm), vs the first (cold) run re-embedding
    # some. Without a working cache both runs would re-embed the same count.
    base, handler = fake_ollama
    handler.delay_s = 0.0
    (tmp_path / "a.py").write_text("def frobulate_widget():\n    pass\n")
    env = {**os.environ, "SQUIRE_OLLAMA": base, "SQUIRE_LLM_LOCK": str(tmp_path / "llm.lock"),
           "SQUIRE_EMBED_LOCK": str(tmp_path / "embed.lock")}
    cold = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path), "--json"],
                          text=True, capture_output=True, env=env, timeout=30)
    warm = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(tmp_path), "--json"],
                          text=True, capture_output=True, env=env, timeout=30)
    cold_obj, warm_obj = json.loads(cold.stdout), json.loads(warm.stdout)
    assert cold_obj["reembedded_chunks"] > 0
    assert warm_obj["reembedded_chunks"] == 0


def test_doctor_backend_down_exits_2():
    p = run("doctor", "--json")
    import json as _j
    assert p.returncode == 2 and _j.loads(p.stdout)["ready"] is False


def test_run_json_flag_after_separator_belongs_to_the_command():
    p = run("run", "--", "echo", "--json")
    assert "--json" in p.stdout and p.returncode == 0


def test_ledger_records_session_id(tmp_path):
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger), "CLAUDE_CODE_SESSION_ID": "abc123"}
    run("sum", "-", stdin="x\n", env=env)
    import json as _j
    assert _j.loads(ledger.read_text().splitlines()[0])["session"] == "abc123"


# ---- S4: ledger test isolation + source field ----

def test_ledger_rows_tagged_with_source_from_conftest(tmp_path):
    # conftest.py sets SQUIRE_SOURCE=test for the whole suite; this asserts it actually lands in
    # the row, not just that the env var exists.
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    run("sum", "-", stdin="x\n", env=env)
    import json as _j
    assert _j.loads(ledger.read_text().splitlines()[0])["source"] == "test"


# ---- S14: run ledger rows carried exit=None; verify-check compared against a truncated tail ----

def test_ledger_records_run_exit_code_and_duration(tmp_path):
    # S14: found live -- the 5 latest `run` ledger rows all carried exit=None even though
    # `squire run` prints the real code (`[squire] exit=N`) to the user. Control: exit=3 (not 0
    # and not None) proves the real subprocess code reached the ledger, not a hardcoded default.
    code = "import sys\nfor i in range(200): print('line', i)\nsys.exit(3)"
    ledger = tmp_path / "l.jsonl"
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("run", "--", sys.executable, "-c", code, env=env)
    assert p.returncode == 3
    rows = [json.loads(l) for l in ledger.read_text().splitlines() if json.loads(l)["cmd"] == "run"]
    assert len(rows) == 1
    assert rows[0]["exit_code"] == 3
    assert rows[0]["duration_s"] is not None and rows[0]["duration_s"] >= 0


def test_condense_verified_checks_against_full_notes_not_tail_slice(monkeypatch):
    # S14: found live -- a passing 5,604-line smoke-test log got a summary whose last line was
    # off-topic: "JSONDecodeError is not mentioned in the source." Root cause: the verify check
    # compared the final summary against text[-CHUNK:], only the LAST chunk of a multi-chunk
    # input. A true claim drawn from an EARLIER chunk then looks like a hallucination purely
    # because the verifier's window never saw it. Reproduced here: the fact lives ONLY in chunk
    # 1's note; a fake model's check() replies "OK" only if that fact is literally present in the
    # SOURCE text it was handed.
    monkeypatch.setattr(sq, "CHUNK", 200)  # forces 2 chunks below without truncating the notes

    def fake_llm(prompt, max_tokens=300):
        if "part 1/2" in prompt:
            return "chunk 1 handled a JSONDecodeError safely"
        if "part 2/2" in prompt:
            return "chunk 2: all tests passed"
        if "Combine these partial notes" in prompt:
            return "All tests passed; one JSONDecodeError was handled safely."
        if "SOURCE (truncated)" in prompt:
            source = prompt.split("SOURCE:\n", 1)[1].split("\n\nSUMMARY:")[0]
            return "OK" if "JSONDecodeError" in source else "JSONDecodeError is not mentioned in the source."
        raise AssertionError(f"unexpected prompt: {prompt!r}")

    monkeypatch.setattr(sq, "llm", fake_llm)
    text = "A" * 200 + "B" * 200  # > CHUNK(200) -> exactly 2 chunks
    summary, flag = sq.condense_verified(text, "Summarize this")
    assert "JSONDecodeError" in summary
    # Control: before the fix, check_source was text[-CHUNK:] = the "B"*200 chunk alone, which
    # never mentions JSONDecodeError -- that would have set flag to the false-positive sentence.
    assert flag is None


def test_condense_verified_single_chunk_checks_raw_text_control(monkeypatch):
    # Control for the test above: a single-chunk input (fits in one CHUNK, no notes produced)
    # must still be checked against the raw text itself -- proves the S14 fix only changes
    # behavior on the multi-chunk path, not the common short-input case.
    def fake_llm(prompt, max_tokens=300):
        if "SOURCE (truncated)" in prompt:
            source = prompt.split("SOURCE:\n", 1)[1].split("\n\nSUMMARY:")[0]
            return "OK" if "hello" in source else "not mentioned"
        return "a summary mentioning hello"

    monkeypatch.setattr(sq, "llm", fake_llm)
    summary, flag = sq.condense_verified("hello world", "Summarize this")
    assert flag is None


def test_stats_excludes_source_test_rows_but_keeps_them_in_the_file(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"cmd": "sum", "chars_in": 100, "chars_out": 20, "backend_ok": true, "source": "test"}\n'
        '{"cmd": "sum", "chars_in": 50, "chars_out": 10, "backend_ok": true, "source": "cli"}\n'
    )
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("stats", env=env)
    # control: without the filter both rows would sum to in=150 out=30 -- prove the filter fired
    assert "chars in=50 out=10 saved=40" in p.stdout
    assert "1 test rows excluded, not deleted" in p.stdout
    assert len(ledger.read_text().splitlines()) == 2  # nothing removed from the file


def test_ask_multi_chunk_survives_a_chunk_that_found_nothing(monkeypatch):
    # 2026-09-14: `squire ask` on anything over one CHUNK used to come back with a bare "UNKNOWN"
    # and no reason as soon as ANY single chunk's answer was the literal word "UNKNOWN" -- which
    # is the model correctly reporting "not in THIS chunk", expected for most chunks of a
    # multi-chunk file, not a backend failure. The ledger showed real, non-trivial gen_s (a live
    # model call) alongside chars_out=7 ("UNKNOWN") on inputs far bigger than one CHUNK, proving
    # the backend was healthy and simply never got to combine the chunk that DID have the answer.
    monkeypatch.setattr(sq, "CHUNK", 200)  # forces multiple chunks below without huge fixtures

    def fake_llm(prompt, max_tokens=300):
        if "part 1/2" in prompt:
            return "UNKNOWN"  # the answer genuinely isn't in this chunk
        if "part 2/2" in prompt:
            return "the answer is 42"
        if "Combine these partial notes" in prompt:
            return "The answer is 42."
        raise AssertionError(f"unexpected prompt: {prompt!r}")

    monkeypatch.setattr(sq, "llm", fake_llm)
    text = "A" * 200 + "B" * 200
    out = sq.condense(text, "Answer using ONLY this text; say UNKNOWN if it is not there. Question: what is the answer?")
    assert out == "The answer is 42."  # not a bare "UNKNOWN"


def test_ask_multi_chunk_still_aborts_on_a_real_backend_error(monkeypatch):
    # Control for the test above: a REAL backend-error sentinel (always "UNKNOWN: <reason>",
    # never the bare word) on any chunk must still abort the combine step -- proves the fix only
    # stopped treating the model's own bare "UNKNOWN" as fatal, not genuine backend failures.
    monkeypatch.setattr(sq, "CHUNK", 200)

    def fake_llm(prompt, max_tokens=300):
        if "part 1/2" in prompt:
            return "UNKNOWN: local model unavailable (ConnectionError: refused)"
        if "part 2/2" in prompt:
            return "the answer is 42"
        raise AssertionError(f"unexpected prompt (combine should never be reached): {prompt!r}")

    monkeypatch.setattr(sq, "llm", fake_llm)
    text = "A" * 200 + "B" * 200
    out = sq.condense(text, "Answer using ONLY this text; say UNKNOWN if it is not there. Question: what is the answer?")
    assert out.startswith("UNKNOWN: local model unavailable")


def test_grep_accepts_a_single_file_path_not_only_a_directory(tmp_path, fake_ollama):
    # 2026-09-14: `squire grep query some/file.py` (a FILE, not a directory) used to compute
    # `root = the file itself` (os.path.isdir(path) is False so the repo-root lookup and the
    # directory fallback both skipped), then list_files() called os.walk() on that non-directory
    # -- os.walk yields nothing for a file, so the file was silently "indexed" as 0 files and
    # every search came back empty with no error at all.
    base, handler = fake_ollama
    handler.delay_s = 0.0
    target = tmp_path / "a.py"
    target.write_text("def frobulate_widget():\n    pass\n")
    (tmp_path / "b.py").write_text("def unrelated():\n    pass\n")
    env = {**os.environ, "SQUIRE_OLLAMA": base, "SQUIRE_LLM_LOCK": str(tmp_path / "llm.lock"),
           "SQUIRE_EMBED_LOCK": str(tmp_path / "embed.lock")}
    p = subprocess.run([sys.executable, str(SQUIRE), "grep", "widget", str(target), "--json"],
                       text=True, capture_output=True, env=env, timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    obj = json.loads(p.stdout)
    assert obj["files_indexed"] == 1  # only the one file, not the whole directory
    assert obj["results"] != []  # control: before the fix this was always []


def test_stats_excludes_legacy_pre_source_fixture_rows(tmp_path):
    # A row written before the `source` field existed (S1-era), matching one of the exact
    # deterministic fixture shapes the S3-check found. Must still be excluded by signature alone.
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"cmd": "sum", "chars_in": 10, "chars_out": 91, "backend_ok": false}\n'
        '{"cmd": "sum", "chars_in": 9999, "chars_out": 91, "backend_ok": false}\n'
    )
    env = {**DOWN, "SQUIRE_LEDGER": str(ledger)}
    p = run("stats", env=env)
    assert "1 model-backed calls logged" in p.stdout  # only the non-matching row counted
    assert "1 test rows excluded, not deleted" in p.stdout

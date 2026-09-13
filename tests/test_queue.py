"""S5: the SQLite job queue (submit / worker / status / wait / jobs). All against DOWN (no real
backend), so 'work' means condense()/llm() return UNKNOWN -- these tests check the queue plumbing
(claiming, status transitions, exit codes, crash recovery, FIFO order), not model output quality.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SQUIRE = Path(__file__).resolve().parents[1] / "squire.py"
DOWN = {**os.environ, "SQUIRE_OLLAMA": "http://127.0.0.1:1"}


def run(*args, env=None, timeout=60):
    return subprocess.run([sys.executable, str(SQUIRE), *args], text=True, input="",
                          capture_output=True, env=env or DOWN, timeout=timeout)


def qenv(tmp_path, **extra):
    # SQUIRE_LLM_LOCK isolates each test's flock from the real ~/.squire/llm.lock that other,
    # real squire processes on this workstation hold under load -- without it a DOWN-backend
    # worker here can queue behind real work for up to LLM_LOCK_TIMEOUT (240s) and blow its own
    # subprocess timeout. Root-caused 2026-09-13 (S7); see the same note in tests/test_squire.py.
    return {**DOWN, "SQUIRE_QUEUE_DB": str(tmp_path / "queue.db"),
            "SQUIRE_WORKER_HEARTBEAT": str(tmp_path / "worker.heartbeat"),
            "SQUIRE_LEDGER": str(tmp_path / "ledger.jsonl"),
            "SQUIRE_LLM_LOCK": str(tmp_path / "llm.lock"), **extra}


def submit_id(p):
    return json.loads(p.stdout)["job_id"]


def test_submit_returns_a_job_id_immediately(tmp_path):
    env = qenv(tmp_path)
    p = run("submit", "sum", "-", "--json", env=env)
    assert p.returncode == 0
    obj = json.loads(p.stdout)
    assert obj["status"] == "queued" and isinstance(obj["job_id"], int)


def test_no_worker_alive_is_unknown_exit_3(tmp_path):
    env = qenv(tmp_path)
    job_id = submit_id(run("submit", "sum", "-", "--json", env=env))
    p = run("status", str(job_id), "--json", env=env)
    assert p.returncode == 3
    assert json.loads(p.stdout)["state"] == "UNKNOWN"


def _submit(env, *args, stdin=""):
    p = subprocess.run([sys.executable, str(SQUIRE), "submit", *args, "--json"], text=True,
                       input=stdin, capture_output=True, env=env, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)["job_id"]


def test_control_wait_can_return_nonzero_when_backend_down(tmp_path):
    # Control (per CLAUDE.md S17): prove `wait` is CAPABLE of a non-zero exit before trusting any
    # zero it returns elsewhere. With the model backend down, a submitted job must FAIL (exit 1),
    # never look like a false RESULT.
    env = qenv(tmp_path)
    job_id = _submit(env, "sum", "-", stdin="hello world\n")
    wp = subprocess.run([sys.executable, str(SQUIRE), "worker", "--once"], text=True,
                       capture_output=True, env=env, timeout=60)
    assert wp.returncode == 0  # the worker process itself exits clean even though the job failed
    p = run("wait", str(job_id), "--timeout", "1", "--json", env=env)
    assert p.returncode == 1
    assert json.loads(p.stdout)["state"] == "FAIL"


def test_submit_worker_status_reports_result_when_backend_reachable(monkeypatch, tmp_path):
    # Don't need a real model: stub llm()/condense() are exercised via a backend that returns fast
    # UNKNOWN above; here we directly poke the DB to simulate a completed job, proving `status`
    # reads DONE/result correctly rather than only ever hitting the FAIL path.
    env = qenv(tmp_path)
    job_id = _submit(env, "sum", "-", stdin="hello\n")
    conn = sqlite3.connect(env["SQUIRE_QUEUE_DB"])
    conn.execute("UPDATE jobs SET status='done', result='- a bullet', finished_at=? WHERE id=?",
                (time.time(), job_id))
    conn.commit()
    conn.close()
    p = run("status", str(job_id), "--json", env=env)
    assert p.returncode == 0
    obj = json.loads(p.stdout)
    assert obj["state"] == "RESULT" and obj["result"] == "- a bullet"


def test_status_empty_id_is_clean_usage_error_not_a_crash(tmp_path):
    # Found 2026-09-13 (S6b/S7): `squire status ''` used to hit `int('')` uncaught, crashing with
    # a raw Python ValueError traceback instead of squire's own usage error. Control: a real
    # numeric id on the same empty queue still gets the normal "not found" path, proving the fix
    # only changed the invalid-id case.
    env = qenv(tmp_path)
    p = run("status", "", "--json", env=env)
    assert p.returncode != 0
    assert "Traceback" not in p.stderr
    assert "usage:" in (p.stdout + p.stderr)
    control = run("status", "999", "--json", env=env)
    assert "Traceback" not in control.stderr
    assert "not found" in (control.stdout + control.stderr)


def test_wait_empty_id_is_clean_usage_error_not_a_crash(tmp_path):
    env = qenv(tmp_path)
    p = run("wait", "", "--timeout", "0.2", "--json", env=env)
    assert p.returncode != 0
    assert "Traceback" not in p.stderr
    assert "usage:" in (p.stdout + p.stderr)


def test_wait_timeout_reports_wait_exit_2_with_position(tmp_path):
    env = qenv(tmp_path)
    job_id = _submit(env, "sum", "-", stdin="x\n")
    # Fake a live worker heartbeat (this process's own pid is alive) so we hit WAIT, not UNKNOWN.
    hb = env["SQUIRE_WORKER_HEARTBEAT"]
    os.makedirs(os.path.dirname(hb), exist_ok=True)
    json.dump({"pid": os.getpid(), "ts": time.time()}, open(hb, "w"))
    p = run("wait", str(job_id), "--timeout", "0.3", "--json", env=env)
    assert p.returncode == 2
    obj = json.loads(p.stdout)
    assert obj["state"] == "WAIT" and obj["position"] == 1


def test_crash_recovery_requeues_a_running_job_with_a_dead_worker_pid(tmp_path):
    env = qenv(tmp_path)
    job_id = _submit(env, "sum", "-", stdin="x\n")
    conn = sqlite3.connect(env["SQUIRE_QUEUE_DB"])
    dead_pid = 999999  # a pid essentially guaranteed not to exist
    conn.execute("UPDATE jobs SET status='running', worker_pid=?, started_at=? WHERE id=?",
                (dead_pid, time.time(), job_id))
    conn.commit()
    conn.close()
    # Running the worker for one iteration must requeue the stale job then claim and process it.
    wp = subprocess.run([sys.executable, str(SQUIRE), "worker", "--once"], text=True,
                       capture_output=True, env=env, timeout=60)
    assert wp.returncode == 0
    conn = sqlite3.connect(env["SQUIRE_QUEUE_DB"])
    row = conn.execute("SELECT status, attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    assert row[0] in ("done", "failed")  # it got processed, not left stuck as 'running'
    assert row[1] >= 1  # attempts incremented by the requeue


def test_fifo_order_processed_by_id(tmp_path):
    env = qenv(tmp_path)
    ids = [_submit(env, "sum", "-", stdin=f"job {i}\n") for i in range(3)]
    for _ in ids:
        wp = subprocess.run([sys.executable, str(SQUIRE), "worker", "--once"], text=True,
                            capture_output=True, env=env, timeout=60)
        assert wp.returncode == 0
    conn = sqlite3.connect(env["SQUIRE_QUEUE_DB"])
    finished = [r[0] for r in conn.execute("SELECT id FROM jobs ORDER BY finished_at").fetchall()]
    conn.close()
    assert finished == ids  # processed in submission order, not reversed or interleaved


def test_jobs_lists_all_jobs(tmp_path):
    env = qenv(tmp_path)
    _submit(env, "sum", "-", stdin="a\n")
    _submit(env, "sum", "-", stdin="b\n")
    p = run("jobs", "--json", env=env)
    assert p.returncode == 0
    assert len(json.loads(p.stdout)["jobs"]) == 2


def test_ledger_row_from_queue_worker_carries_source_and_job_fields(tmp_path):
    env = qenv(tmp_path)
    job_id = _submit(env, "sum", "-", stdin="hello\n")
    subprocess.run([sys.executable, str(SQUIRE), "worker", "--once"], text=True,
                  capture_output=True, env=env, timeout=60)
    rows = [json.loads(ln) for ln in open(env["SQUIRE_LEDGER"]) if ln.strip()]
    assert rows and rows[0]["cmd"] == "queue:sum"
    assert "queue_wait_s" in rows[0] and "gen_s" in rows[0]

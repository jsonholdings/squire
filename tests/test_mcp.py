"""Protocol round-trip tests for mcp/squire_mcp.py, against a fake squire binary (no model)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "squire_mcp.py"
FAKE_SQUIRE = ROOT / "tests" / "fake_squire.py"


def send(requests, env_overrides=None):
    """Run the server with a list of JSON-RPC request dicts piped in; return list of responses."""
    env = {**os.environ, "SQUIRE_BIN": str(FAKE_SQUIRE)}
    if env_overrides:
        env.update(env_overrides)
    stdin = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run([sys.executable, str(SERVER)], input=stdin, text=True,
                          capture_output=True, env=env, timeout=30)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def test_initialize_returns_matching_protocol_version():
    resp = send([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}])
    assert len(resp) == 1
    r = resp[0]["result"]
    assert r["protocolVersion"] == "2025-06-18"
    assert r["serverInfo"]["name"] == "squire"
    assert "tools" in r["capabilities"]


def test_tools_list_includes_all_expected_tools():
    resp = send([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    names = {t["name"] for t in resp[0]["result"]["tools"]}
    expected = {"squire_run", "squire_sum", "squire_ask", "squire_draft",
                "squire_diff", "squire_grep", "squire_triage", "squire_stats"}
    assert expected <= names


def test_tools_call_squire_run_returns_fake_json_payload():
    resp = send([{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "squire_run", "arguments": {"command": "echo hi"}}}])
    result = resp[0]["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["backend_ok"] is True
    assert payload["cmd"] == "run"


def test_tools_call_squire_stats_backend_down_is_labelled():
    resp = send([{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "squire_stats", "arguments": {}}}],
                env_overrides={"FAKE_SQUIRE_BACKEND_DOWN": "1"})
    payload = json.loads(resp[0]["result"]["content"][0]["text"])
    assert payload["backend_ok"] is False
    assert "UNKNOWN" in payload["summary"]


def test_unknown_method_returns_jsonrpc_error():
    resp = send([{"jsonrpc": "2.0", "id": 5, "method": "not/a/method"}])
    assert resp[0]["error"]["code"] == -32601


def test_initialized_notification_produces_no_response():
    resp = send([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert resp == []

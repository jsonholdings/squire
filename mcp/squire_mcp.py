#!/usr/bin/env python3
"""squire_mcp: stdlib-only MCP stdio server exposing squire's commands as tools.

Protocol: MCP 2025-06-18 (JSON-RPC 2.0 over stdio, newline-delimited). Verified against
https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle on 2026-09-12.

This server never imports squire internals -- it shells out to the `squire` CLI with --json,
per the interface contract (squire.py is owned by another session; this stays a thin client).

Locating the squire binary, in order:
    1. $SQUIRE_BIN
    2. `squire` on PATH
    3. ../squire.py next to this file
"""
import json
import os
import shutil
import subprocess
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "squire"
SERVER_VERSION = "0.1.0"


def find_squire_bin():
    env = os.environ.get("SQUIRE_BIN")
    if env:
        return [env] if not env.endswith(".py") else [sys.executable, env]
    on_path = shutil.which("squire")
    if on_path:
        return [on_path]
    here = os.path.dirname(os.path.abspath(__file__))
    fallback = os.path.join(here, "..", "squire.py")
    return [sys.executable, fallback]


def run_squire(args, timeout=180):
    """Run squire with --json, return the parsed dict (or an UNKNOWN-shaped dict on failure)."""
    bin_argv = find_squire_bin()
    full = bin_argv + list(args) + ["--json"]
    try:
        proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"cmd": args[0] if args else None, "exit_code": None, "raw_tail": None,
                "summary": f"UNKNOWN: squire invocation failed: {e}", "assumed": True,
                "backend_ok": False, "verify_flag": "UNKNOWN"}
    out = (proc.stdout or "").strip()
    if not out:
        return {"cmd": args[0] if args else None, "exit_code": proc.returncode, "raw_tail": proc.stderr,
                "summary": "UNKNOWN: squire produced no JSON output", "assumed": True,
                "backend_ok": False, "verify_flag": "UNKNOWN"}
    # squire may print multiple lines (e.g. run's own progress); take the last JSON line.
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"cmd": args[0] if args else None, "exit_code": proc.returncode, "raw_tail": out[-2000:],
            "summary": "UNKNOWN: squire output was not valid JSON", "assumed": True,
            "backend_ok": False, "verify_flag": "UNKNOWN"}


TOOLS = [
    {
        "name": "squire_run",
        "description": "Run a shell command through squire; returns real exit code, raw tail, and a local condensed summary of long output. Never use for pass/fail decisions -- read exit_code.",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The shell command to run"}},
            "required": ["command"],
        },
    },
    {
        "name": "squire_sum",
        "description": "Condense text (or a file's contents) to a few factual bullets, locally, without spending session tokens on the raw text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to condense (mutually exclusive with path)"},
                "path": {"type": "string", "description": "File to condense (mutually exclusive with text)"},
            },
        },
    },
    {
        "name": "squire_ask",
        "description": "Answer a question using ONLY the given text/file; says UNKNOWN if not present.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "text": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "squire_draft",
        "description": "First draft of a doc/message from instructions and optional source material. Always review before keeping -- output is ASSUMED, not verified.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "instructions": {"type": "string"},
                "text": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["instructions"],
        },
    },
    {
        "name": "squire_diff",
        "description": "Summarize a large git diff (staged, or between two refs) locally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "staged": {"type": "boolean"},
                "ref1": {"type": "string"},
                "ref2": {"type": "string"},
            },
        },
    },
    {
        "name": "squire_grep",
        "description": "Semantic search over a repo using local embeddings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "top": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "squire_triage",
        "description": "Sort a backlog/log file into a local-model triage summary.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "squire_stats",
        "description": "Real chars in/out logged by squire so far, plus a labelled token-savings estimate.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text_input(args):
    if args.get("path"):
        return None, args["path"]
    return args.get("text", ""), None


def call_tool(name, args):
    args = args or {}
    if name == "squire_run":
        return run_squire(["run", "--", args["command"]])
    if name == "squire_sum":
        text, path = _text_input(args)
        if path:
            return run_squire(["sum", path])
        return _run_with_stdin("sum", [], text)
    if name == "squire_ask":
        text, path = _text_input(args)
        if path:
            return run_squire(["ask", args["question"], path])
        return _run_with_stdin("ask", [args["question"]], text)
    if name == "squire_draft":
        text, path = _text_input(args)
        if path:
            return run_squire(["draft", args["instructions"], path])
        return _run_with_stdin("draft", [args["instructions"]], text)
    if name == "squire_diff":
        diff_args = ["diff"]
        if args.get("staged"):
            diff_args.append("--staged")
        elif args.get("ref1") and args.get("ref2"):
            diff_args += [args["ref1"], args["ref2"]]
        return run_squire(diff_args)
    if name == "squire_grep":
        grep_args = ["grep", args["query"]]
        if args.get("path"):
            grep_args.append(args["path"])
        if args.get("top"):
            grep_args += ["--top", str(args["top"])]
        return run_squire(grep_args)
    if name == "squire_triage":
        return run_squire(["triage", args["path"]])
    if name == "squire_stats":
        return run_squire(["stats"])
    return {"error": f"unknown tool {name!r}"}


def _run_with_stdin(cmd, extra_args, text):
    """Run squire piping `text` on stdin (squire's read_input treats '-' as stdin)."""
    bin_argv = find_squire_bin()
    full = bin_argv + [cmd] + extra_args + ["-", "--json"]
    try:
        proc = subprocess.run(full, input=text or "", capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"cmd": cmd, "exit_code": None, "raw_tail": None,
                "summary": f"UNKNOWN: squire invocation failed: {e}", "assumed": True,
                "backend_ok": False, "verify_flag": "UNKNOWN"}
    out = (proc.stdout or "").strip()
    for line in reversed(out.splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"cmd": cmd, "exit_code": proc.returncode, "raw_tail": out[-2000:],
            "summary": "UNKNOWN: squire output was not valid JSON", "assumed": True,
            "backend_ok": False, "verify_flag": "UNKNOWN"}


def handle_request(req):
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        result = call_tool(name, args)
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False},
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle_request(req)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

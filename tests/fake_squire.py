#!/usr/bin/env python3
"""Fake squire binary for tests: no model, deterministic JSON output.

Behavior toggles via env:
  FAKE_SQUIRE_FAIL=1      -> print nothing to stdout (simulates squire crashing / backend down)
  FAKE_SQUIRE_BACKEND_DOWN=1 -> emit valid JSON but backend_ok=false
"""
import json
import os
import sys

if os.environ.get("FAKE_SQUIRE_FAIL") == "1":
    sys.exit(1)

cmd = sys.argv[1] if len(sys.argv) > 1 else None
backend_ok = os.environ.get("FAKE_SQUIRE_BACKEND_DOWN") != "1"
summary = "FAKE SUMMARY: 3 bullets condensed" if backend_ok else "UNKNOWN: local model unavailable"

print(json.dumps({
    "cmd": cmd,
    "exit_code": 0,
    "raw_tail": "fake raw tail",
    "summary": summary,
    "assumed": True,
    "backend_ok": backend_ok,
    "verify_flag": "ASSUMED" if backend_ok else "UNKNOWN",
}))

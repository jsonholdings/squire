"""S4 (2026-09-13): squire's own test suite was writing its fixture calls straight into the real
production ~/.squire/ledger.jsonl (found by the orchestrator's S3-check: 21 of 33 post-S1 rows
were the same 7 fixture calls repeated across test runs). This module-level code runs before any
test module in this directory is imported (conftest.py loads first), so it sets SQUIRE_LEDGER and
SQUIRE_SOURCE in os.environ early enough that a module-level `DOWN = {**os.environ, ...}` constant
(as in test_squire.py) picks up the isolated values.

A test that wants to see squire.py's actual defaults can still `monkeypatch.delenv` these two.
"""
import atexit
import os
import tempfile

_tmp_ledger = tempfile.NamedTemporaryFile(prefix="squire-test-ledger-", suffix=".jsonl", delete=False)
_tmp_ledger.close()
atexit.register(lambda: os.path.exists(_tmp_ledger.name) and os.remove(_tmp_ledger.name))

os.environ.setdefault("SQUIRE_LEDGER", _tmp_ledger.name)
os.environ["SQUIRE_SOURCE"] = "test"

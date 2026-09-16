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
# 2026-09-15, item 3: llm() now retries a failed backend call with backoff before giving up. Left
# at squire.py's real default, every one of the ~20 tests that point SQUIRE_OLLAMA at a dead port
# (instant "connection refused") would still sleep through the backoff between attempts, multiplying
# the whole suite's wall time for no test value -- those tests are about "backend down reports
# UNKNOWN", not about the retry loop itself. Disabled here; a test of the retry loop itself sets
# SQUIRE_LLM_RETRIES explicitly (monkeypatch.setenv), overriding this default.
os.environ.setdefault("SQUIRE_LLM_RETRIES", "0")

# 2026-09-15, item 5: sum/ask now cache results on disk under SQUIRE_CACHE_DIR. Left at squire.py's
# real default (~/.squire/cache), the test suite would read and write the SAME cache real usage
# does -- a test's DOWN-backend UNKNOWN result could get cached and then served back to a real
# call, or a test could get a false cache hit from a previous real run. Isolated the same way the
# ledger is isolated above.
_tmp_cache = tempfile.mkdtemp(prefix="squire-test-cache-")
atexit.register(lambda: __import__("shutil").rmtree(_tmp_cache, ignore_errors=True))
os.environ.setdefault("SQUIRE_CACHE_DIR", _tmp_cache)

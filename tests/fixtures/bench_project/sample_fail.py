# Frozen fixture for scripts/benchmark.py's deterministic exit-code corpus (RUN-QUEUE S8b).
# Deliberately named sample_*.py, NOT test_*.py -- see sample_pass.py for why. One passing and
# two deliberately failing tests, all deterministic (no timing, no I/O, no randomness). pytest
# exits 1 whenever any test fails. Expected exit code: 1.


def test_ok():
    assert True


def test_broken_assertion():
    assert False, "deterministic benchmark failure -- expected, not a real bug"


def test_broken_arithmetic():
    assert 1 == 2, "deterministic benchmark failure -- expected, not a real bug"

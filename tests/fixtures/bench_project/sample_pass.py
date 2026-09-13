# Frozen fixture for scripts/benchmark.py's deterministic exit-code corpus (RUN-QUEUE S8b).
# Deliberately named sample_*.py, NOT test_*.py: the repo's own `pytest -q` (pyproject.toml
# [tool.pytest.ini_options] testpaths=["tests"]) must never collect these as part of squire's
# own 110/110 suite. benchmark.py invokes this file explicitly with
# `pytest -o python_files=sample_*.py <path>`, which is the only way it is ever collected.
# No squire import, no network -- a plain, always-passing fixture. Expected exit code: 0.


def test_addition():
    assert 1 + 1 == 2


def test_membership():
    assert "a" in "abc"


def test_truthy():
    assert True

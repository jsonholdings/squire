"""scrub_check.py must never carry site-specific literals in its own source (that is
the bug this repo shipped once: a hardcoded denylist excluded from its own self-scan --
see the module docstring), and it must fail loudly if the private local denylist file
ever ends up copied inside this repo's tree by a sync-from-source mistake.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRUB = os.path.join(ROOT, "scripts", "scrub_check.py")


def _load_scrub_module():
    spec = importlib.util.spec_from_file_location("scrub_check", SCRUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(env=None):
    full_env = dict(os.environ)
    if env is not None:
        full_env.update(env)
        full_env.pop("SQUIRE_SCRUB_DENYLIST", None) if "SQUIRE_SCRUB_DENYLIST" not in env else None
    proc = subprocess.run([sys.executable, SCRUB], cwd=ROOT, env=full_env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout


def test_a_clean_tree_passes_with_no_local_denylist_configured():
    code, out = _run(env={"SQUIRE_SCRUB_DENYLIST": "/nonexistent/does-not-exist.txt"})
    assert code == 0, out
    assert "clean" in out


def test_scrub_check_no_longer_exempts_its_own_filename():
    """Regression pin: an earlier version's SKIP set exempted scrub_check.py's own
    filename from the scan, which is how a hardcoded site-specific denylist inside it
    went undetected by its own check. It must not exempt itself any more."""
    mod = _load_scrub_module()
    assert "scrub_check.py" not in mod.SKIP


GENERIC_LABELS = {"home path", "email address", "private IPv4", "secret-shaped"}


def test_scrub_check_loads_site_specific_terms_only_from_an_external_path():
    """The structural fix: no site-specific literal lives in this file's own source at
    all -- everything hardcoded in GENERIC_PATTERNS is a generic, non-identifying
    pattern (home paths, emails, private IPs, secret shapes). Anything site-specific
    is loaded at run time from a path outside the repo via `_load_local_denylist`.
    Real terms are deliberately NOT reproduced in this public test file; the
    mechanism, not the list, is what is checked."""
    mod = _load_scrub_module()
    assert set(mod.GENERIC_PATTERNS.keys()) <= GENERIC_LABELS, (
        "an unexpected pattern label appeared -- check it is not site-specific")
    assert hasattr(mod, "_load_local_denylist")


def test_a_planted_local_denylist_term_is_caught_control():
    """Control: proves the local-denylist mechanism can produce a positive finding,
    not just silently pass everything."""
    with tempfile.TemporaryDirectory() as d:
        deny_path = os.path.join(d, "deny.txt")
        with open(deny_path, "w") as fh:
            fh.write("plantedtermxyz\n")
        target = os.path.join(ROOT, "CONTROL_TEST_PLANTED.md")
        with open(target, "w") as fh:
            fh.write("this file mentions plantedtermxyz\n")
        try:
            code, out = _run(env={"SQUIRE_SCRUB_DENYLIST": deny_path})
            assert code == 1, out
            assert "site-specific" in out
        finally:
            os.remove(target)


def test_a_denylist_file_copied_into_the_repo_is_itself_a_finding():
    """The sync-from-source safety net: even a file merely NAMED like the denylist,
    sitting inside this repo's tree, must fail the check on its own -- independent of
    .gitignore, which only stops git from tracking it."""
    target = os.path.join(ROOT, "scrub-denylist.txt")
    with open(target, "w") as fh:
        fh.write("should never be here\n")
    try:
        code, out = _run(env={"SQUIRE_SCRUB_DENYLIST": "/nonexistent/does-not-exist.txt"})
        assert code == 1, out
        assert "must never be copied" in out
    finally:
        os.remove(target)

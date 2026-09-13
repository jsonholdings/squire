"""CI gate: the logo assets built from docs/assets/logo-source.svg must be current.

If this fails, someone edited logo-source.svg (or an output file) without running
`python3 scripts/build_logo_assets.py` -- the fix is to run it and commit the
regenerated outputs, never to hand-edit an output file.
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "build_logo_assets.py")


def _load():
    spec = importlib.util.spec_from_file_location("build_logo_assets", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_logo_outputs_are_current():
    mod = _load()
    assert mod.check() == 0, (
        "logo assets are stale -- run `python3 scripts/build_logo_assets.py` and commit "
        "the regenerated docs/assets/ files")


def test_the_staleness_check_can_actually_fire_control():
    """Control: proves check() can return non-zero, not just always pass."""
    mod = _load()
    real_hash = mod._hash(mod.SOURCE)
    try:
        with open(mod.MANIFEST, "w") as fh:
            import json
            json.dump({"source_sha256": "0" * 64, "outputs": []}, fh)
        assert mod.check() == 1
    finally:
        # Restore the real manifest by rebuilding rather than hand-writing it back,
        # so this test never leaves the repo in a state the FIRST test would fail on.
        mod.build()
        assert mod._hash(mod.SOURCE) == real_hash, "source should not have changed"


def test_avatar_is_500x500_and_in_the_staleness_gate():
    mod = _load()
    assert mod.AVATAR_PNG in mod.OUTPUTS
    assert os.path.isfile(mod.AVATAR_PNG)
    import subprocess
    out = subprocess.run(["file", mod.AVATAR_PNG], stdout=subprocess.PIPE, text=True).stdout
    assert "500 x 500" in out, out


def test_missing_avatar_makes_check_fail_control():
    """Control: a missing/stale avatar.png must fail --check, not pass silently."""
    mod = _load()
    real_hash = mod._hash(mod.SOURCE)
    try:
        os.remove(mod.AVATAR_PNG)
        assert mod.check() == 1
    finally:
        mod.build()
        assert mod._hash(mod.SOURCE) == real_hash, "source should not have changed"
        assert os.path.isfile(mod.AVATAR_PNG)

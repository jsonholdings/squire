"""Packaging smoke tests: the zipapp builds and runs standalone (SPEC-OPEN-SOURCE.md #2)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_zipapp  # noqa: E402


def test_zipapp_builds_and_runs_help(tmp_path):
    out = build_zipapp.build(tmp_path / "squire.pyz")
    assert out.exists()
    assert out.stat().st_size > 0

    p = subprocess.run([sys.executable, str(out), "--help"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
    assert "squire" in p.stdout.lower()


def test_zipapp_run_preserves_exit_code(tmp_path):
    out = build_zipapp.build(tmp_path / "squire.pyz")
    down_env = {"PATH": __import__("os").environ.get("PATH", ""), "SQUIRE_OLLAMA": "http://127.0.0.1:1"}
    p = subprocess.run(
        [sys.executable, str(out), "run", "--", sys.executable, "-c", "import sys; sys.exit(7)"],
        capture_output=True, text=True, timeout=30, env=down_env,
    )
    assert p.returncode == 7

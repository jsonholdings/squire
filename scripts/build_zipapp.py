#!/usr/bin/env python3
"""Build dist/squire.pyz: a single-file, stdlib-only zipapp (SPEC-OPEN-SOURCE.md #2).

squire.py has no dependencies, so this is a thin wrapper around the stdlib `zipapp` module:
copy squire.py into a scratch dir as __main__.py, zip it with a `#!/usr/bin/env python3`
shebang, chmod +x. The result runs on any machine with python3 -- no pip, no venv.

    python3 scripts/build_zipapp.py [output_path]   # default: dist/squire.pyz
"""
import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "squire.py"


def build(output_path=None):
    output_path = Path(output_path) if output_path else ROOT / "dist" / "squire.pyz"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy2(SOURCE, tmp_path / "__main__.py")
        zipapp.create_archive(
            source=tmp_path,
            target=output_path,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    output_path.chmod(0o755)
    return output_path


if __name__ == "__main__":
    out = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"built {out} ({out.stat().st_size} bytes)")

#!/usr/bin/env python3
"""Fail if anything internal would ship: hostnames, home paths, venture names, secret-shaped strings.

Run before every commit and in CI. `--release` also requires a LICENSE file.
Exit 0 = clean, 1 = findings.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"CLAUDE.md", "SPEC-OPEN-SOURCE.md", "SESSION-CLAIM.md", "scrub_check.py"}  # internal-only files
PATTERNS = {
    "home path": r"/home/[a-z]",
    "secret-shaped": r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}",
}


def main():
    findings = []
    for f in ROOT.rglob("*"):
        if not f.is_file() or f.name in SKIP or ".git" in f.parts or "__pycache__" in f.parts:
            continue
        text = f.read_text(errors="ignore")
        for label, pat in PATTERNS.items():
            for m in re.finditer(pat, text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(f"{f.relative_to(ROOT)}:{line}: {label}")
    if "--release" in sys.argv and not (ROOT / "LICENSE").exists():
        findings.append("LICENSE missing (owner must choose MIT or Apache-2.0)")
    print("\n".join(findings) if findings else "scrub: clean")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()

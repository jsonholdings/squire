#!/usr/bin/env python3
"""Fail if anything internal would ship: home paths, emails, private IPs, secret-shaped
strings, and any site-specific term named in a LOCAL denylist that is never part of this
repo and never committed.

Run before every commit and in CI. `--release` also requires a LICENSE file.
Exit 0 = clean, 1 = findings.

**This file scans itself.** A denylist hardcoded here once (hostnames, venture names) was
excluded from its own scan by an earlier version's SKIP set, so it shipped the exact
internal shape it existed to catch -- publishing which hosts and which named ventures sit
behind this tool, not just that it exists (see project memory
feedback_never_publish_infrastructure_shape.md). The fix is structural, not a bigger SKIP
list: nothing site-specific may be a literal in this file's source at all. Anything
site-specific is supplied at run time from OUTSIDE the repo -- see SQUIRE_SCRUB_DENYLIST
below -- so there is nothing here for a self-scan to need to exempt.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"SESSION-CLAIM.md"}  # session-claim's own generated, gitignored marker file only
# Synthetic captured test-runner output used by eval/run_eval.py's fixtures -- built by
# hand to look like real pytest/go test logs (test names like "TestQueueEnqueue",
# "test_token_expiry"), never real credentials. The secret-shaped pattern's "pass"/
# "token" keys legitimately fire on this kind of fixture text ("PASS:", "token = ...()")
# with no way to generalise the regex around it without also hiding real leaks in
# ordinary code -- explicitly allow-listed by directory rather than weakened globally.
SKIP_DIRS = {"eval/fixtures"}

GENERIC_PATTERNS = {
    "home path": r"/home/[a-z][a-z0-9_-]*",
    # \b(?!:) excludes a git@host:path SSH URL; the domain lookahead excludes RFC
    # 2606 reserved test domains (example.com/.org/.net, .test, .invalid, .localhost)
    # -- synthetic addresses used deliberately in tests and docs, never real leaks.
    "email address": r"[A-Za-z0-9._%+-]+@(?!(?:[A-Za-z0-9.-]+\.)?(?:example\.(?:com|org|net)"
                      r"|test|invalid|localhost)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b(?!:)",
    "private IPv4": r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
                     r"|192\.168\.\d{1,3}\.\d{1,3}"
                     r"|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b",
    # Unquoted on purpose (feedback_secret_scan_needs_unquoted_pattern.md): a
    # quote-requiring pattern misses the shape a person actually writes a credential
    # down in -- a bare key-colon-value line, not only a quoted literal. Colon or
    # equals, quoted or not, either side of whitespace. A placeholder right after the
    # separator (angle brackets, a shell/template variable, a REDACTED marker, a
    # run of asterisks, or an ellipsis) is deliberately excluded, and a value cannot
    # start with a markdown code-span backtick, so this comment and the redaction
    # docs describing the very shape below do not trip themselves.
    "secret-shaped": r"(?i)\b(pass(?:word|wd)?|secret|api[_-]?key|access[_-]?token|token)\b"
                      r"\s*[:=]\s*['\"]?(?!<|\$\{|\[REDACTED\]|\*{2,}|\.\.\.|`)[^\s'\"`]{8,}",
}


def _load_local_denylist():
    """Site-specific terms (internal hostnames, venture names) live OUTSIDE this repo,
    supplied by whoever runs the check on the private source machine. Never hardcoded
    here, never committed, so a public checkout of this repo has nothing to leak and
    nothing to configure to get a clean scan of the code itself."""
    terms = []
    env_path = os.environ.get("SQUIRE_SCRUB_DENYLIST")
    default_path = os.path.expanduser("~/.config/squire/scrub-denylist.txt")
    path = env_path or default_path
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


def _patterns():
    patterns = dict(GENERIC_PATTERNS)
    local_terms = _load_local_denylist()
    if local_terms:
        patterns["site-specific (local denylist)"] = "(?i)(" + "|".join(
            re.escape(t) if not t.startswith("re:") else t[3:] for t in local_terms) + ")"
    return patterns


def main():
    patterns = _patterns()
    findings = []
    for f in ROOT.rglob("*"):
        if not f.is_file() or f.name in SKIP or ".git" in f.parts or "__pycache__" in f.parts:
            continue
        rel_posix = f.relative_to(ROOT).as_posix()
        if any(rel_posix == d or rel_posix.startswith(d + "/") for d in SKIP_DIRS):
            continue
        text = f.read_text(errors="ignore")
        for label, pat in patterns.items():
            for m in re.finditer(pat, text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(f"{f.relative_to(ROOT)}:{line}: {label}")
    # Defense in depth beyond .gitignore: a sync-from-source copy (or a tarball export
    # that ignores .git) could still land the private denylist file inside this repo's
    # tree. If a file matching its name pattern is ever present here at all, that is
    # itself a finding -- the file must never exist inside this repo, checked in or not.
    for f in ROOT.rglob("*denylist*"):
        if f.is_file() and ".git" not in f.parts:
            findings.append(f"{f.relative_to(ROOT)}: local denylist file must never be "
                             f"copied into this repo (sync-from-source bug)")
    if "--release" in sys.argv and not (ROOT / "LICENSE").exists():
        findings.append("LICENSE missing (owner must choose MIT or Apache-2.0)")
    print("\n".join(findings) if findings else "scrub: clean")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()

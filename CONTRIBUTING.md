# Contributing to Squire

1. Keep runtime stdlib-only and localhost-only. No telemetry.
2. Add or adjust tests in `tests/`. `python -m pytest -q` must pass. The safety controls (exit code
   preserved, backend-down → UNKNOWN) are non-negotiable.
3. Add a line to `CHANGELOG.md` under `[Unreleased]`.
4. `python scripts/scrub_check.py` must pass. No machine-specific paths or hostnames.
5. Open a PR with what changed, why, and how you tested it. Include token-savings numbers if you
   have them.

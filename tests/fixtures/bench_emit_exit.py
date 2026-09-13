#!/usr/bin/env python3
"""bench_emit_exit.py: deterministic exit-code fixture for scripts/benchmark.py's
DETERMINISTIC_CMD_WORKLOADS corpus (RUN-QUEUE S8b).

Purpose: the S8 benchmark's exit-code cells flipped not because squire lost an exit code
(squire.py:cmd_run captures the real subprocess returncode BEFORE any model call and passes
it straight through on every branch, timeout included -- read and confirmed, no bug there) but
because the CORPUS itself was two separate live invocations of this repo's own real, resource-
contended `pytest -v`/`pytest -q` suite, which can genuinely finish with different outcomes
across two runs under real GPU/lock contention from other sessions. This script removes that
confound at the source: both the "raw" and the "squire-wrapped" invocation run THIS exact
script with a fixed exit code baked into the argv, so both invocations are guaranteed to agree
with each other and with the expected value, independent of load, timing or any other session's
activity. No network, no imports beyond stdlib, no shared lock, no squire import.

Usage: bench_emit_exit.py <n_lines> <exit_code>
"""
import sys


def main():
    n_lines = int(sys.argv[1])
    exit_code = int(sys.argv[2])
    for i in range(n_lines):
        print(f"bench line {i:04d}: deterministic fixture output, exit_code={exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

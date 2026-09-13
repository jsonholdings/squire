# squire run + nested launchers (S2, 2026-09-13)

## Report
A worker reported `squire run --` "failed on nested `flatpak-spawn --host docker …`
invocations", so agents fell back to raw (uncondensed) output instead of using squire.

## Investigation
`cmd_run` already spawns the argv list directly (`subprocess.run(argv, shell=False)` whenever
more than one argv token follows `--`), so a normal shell already did all quoting/expansion before
squire ever sees the argv list -- there is no double-shell-parsing step for the common case.

Live repro on this workstation (real `flatpak-spawn --host docker run --rm --network none -v
"$HOME/Projects/some-repo":/app -w /app php:8.2-cli php
devtools/test-harness-selfcheck.php <mode>`), BEFORE and AFTER this fix -- identical, both correct:

| repro | mode | exit before | exit after |
|---|---|---|---|
| a | fail | 1 | 1 |
| b | pass | 0 | 0 |
| c | unknown | 2 | 2 |
| d | >60-line nested python (`sys.exit(7)`, 80 lines) | 7 | 7 |

None of these reproduced a hang or a lost exit code in this shell, because the Bash tool's own
stdin is already closed/`/dev/null` here. The one genuine, well-known subprocess footgun that
matches the reported symptom ("breaks", not "returns the wrong code") is stdin inheritance: a
nested launcher that attaches stdin (`docker run -i`, `ssh`, some `flatpak-spawn --host` entry
points) inherits whatever fd squire got. In an interactive terminal that fd is a live tty that
never reaches EOF, so the nested process blocks forever waiting for input -- squire never returns,
which from the caller's side looks exactly like "broken", not "slow".

## Fix
- `cmd_run` now runs the child with `stdin=subprocess.DEVNULL` always. squire's contract is
  capture-then-summarize, never interactive, so stdin was never useful to the wrapped command.
- Defense in depth: `SQUIRE_RUN_TIMEOUT` (seconds, unset by default so existing long-running
  builds are unaffected) bounds the call. On timeout, the real exit code `124` and every byte
  captured before the kill are still emitted -- never silence, per squire's own rule that the
  real exit code and raw output always pass through.

## Regression tests
`tests/test_squire.py`: a stubbed nested launcher script (`tests/fixtures/fake_nested_launcher.py`)
that reads stdin if given `--read-stdin` (proves the DEVNULL fix: without it, the old code would
hang on a stdin that is deliberately left open by the test and never closed) and otherwise exits
with a caller-chosen code, so CI does not depend on docker/flatpak-spawn being installed.

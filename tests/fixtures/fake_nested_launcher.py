#!/usr/bin/env python3
"""Stub for a nested launcher (flatpak-spawn --host / docker run / ssh) used only in tests, so CI
does not depend on docker or flatpak being installed. Mimics the one behavior that matters here:
it can optionally attach to stdin, and always exits with a caller-chosen code."""
import sys

if __name__ == "__main__":
    args = sys.argv[1:]
    read_stdin = "--read-stdin" in args
    if read_stdin:
        args.remove("--read-stdin")
        data = sys.stdin.read()  # blocks until EOF -- hangs if stdin isn't closed
        print(f"got {len(data)} bytes from stdin")
    exit_code = int(args[-1]) if args else 0
    for i in range(80 if exit_code == 77 else 0):
        print(f"nested output line {i}")
    sys.exit(exit_code)

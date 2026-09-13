#!/usr/bin/env python3
"""Install squire into Claude Code: the PreToolUse wrap hook and the MCP server. Idempotent.

    install_claude_code.py [--settings PATH] [--no-mcp] [--check] [--uninstall]

--settings   settings.json to edit (default ~/.claude/settings.json). Everything else in the file
             is preserved; a timestamped backup is written next to it before any change.
--check      report what is installed; exit 0 if complete, 1 if not. Changes nothing.
--no-mcp     only manage the hook.
--uninstall  remove the hook entry (and the MCP registration unless --no-mcp).

Restart Claude Code afterwards: settings and MCP servers are read at session start.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOOK = os.path.join(REPO, "hooks", "pretool_wrap.py")
MCP = os.path.join(REPO, "mcp", "squire_mcp.py")
MARKER = "pretool_wrap.py"


def squire_bin():
    return shutil.which("squire") or os.path.join(REPO, "squire.py")


def hook_command(python):
    return f"SQUIRE_BIN={shlex.quote(squire_bin())} {shlex.quote(python)} {shlex.quote(HOOK)}"


def find_hook(settings):
    for group in settings.get("hooks", {}).get("PreToolUse", []):
        for h in group.get("hooks", []):
            if MARKER in h.get("command", ""):
                return group, h
    return None, None


def mcp_installed():
    if not shutil.which("claude"):
        return None
    p = subprocess.run(["claude", "mcp", "get", "squire"], capture_output=True, text=True)
    return p.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    ap.add_argument("--python", default=sys.executable, help="interpreter the hook runs under")
    ap.add_argument("--no-mcp", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()

    settings = json.load(open(a.settings)) if os.path.exists(a.settings) else {}
    group, hook = find_hook(settings)
    want = hook_command(a.python)
    mcp = None if a.no_mcp else mcp_installed()

    if a.check:
        ok = hook is not None and hook.get("command") == want
        print(f"hook: {'installed' if ok else 'STALE (' + hook['command'] + ')' if hook else 'MISSING'} in {a.settings}")
        if not a.no_mcp:
            print(f"mcp:  {'installed' if mcp else 'UNKNOWN (claude CLI not on PATH)' if mcp is None else 'MISSING'}")
        return 0 if ok and (a.no_mcp or mcp) else 1

    changed = False
    if a.uninstall:
        if group is not None:
            group["hooks"].remove(hook)
            if not group["hooks"]:
                settings["hooks"]["PreToolUse"].remove(group)
            changed = True
    elif hook is None:
        settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": want, "timeout": 10}]})
        changed = True
    elif hook.get("command") != want:
        hook["command"] = want
        changed = True

    if changed:
        if os.path.exists(a.settings):
            shutil.copy2(a.settings, f"{a.settings}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        tmp = a.settings + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, a.settings)
    print(f"hook: {'removed' if a.uninstall else 'installed'}{'' if changed else ' (no change)'} -> {a.settings}")

    if not a.no_mcp:
        if mcp is None:
            print("mcp:  UNKNOWN -- claude CLI not on PATH; add it by hand, see mcp/README.md")
        elif a.uninstall and mcp:
            subprocess.run(["claude", "mcp", "remove", "-s", "user", "squire"], check=False)
            print("mcp:  removed")
        elif not a.uninstall and not mcp:
            subprocess.run(["claude", "mcp", "add", "-s", "user", "-e", f"SQUIRE_BIN={squire_bin()}",
                            "squire", "--", a.python, MCP], check=True)
            print("mcp:  installed (user scope)")
        else:
            print(f"mcp:  {'not installed' if a.uninstall else 'already installed'}")
    print("Restart Claude Code for the change to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

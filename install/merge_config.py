#!/usr/bin/env python3
"""Defensive config merger — used by install.sh + install.ps1 (Council #4824).

The install.ps1 clobber incident (May 28, 2026) taught the hard rules: config
merges must NEVER fall to empty on parse failure, must BACK UP before writing,
must VERIFY after writing, and must write BOM-free JSON. PowerShell's
ConvertFrom-Json is fragile; bash has no JSON tools. So BOTH installers delegate
config merges to THIS python helper instead.

Two operations:

  claude-md   — append-safe a marker-delimited block into a CLAUDE.md
                (create if absent; replace-between-markers if our block present;
                 append if file exists without our block; NEVER overwrite their content)

  hook        — merge a SessionStart hook command into settings.json hooks array
                (preserve ALL existing keys + existing hooks; idempotent; abort on
                 parse failure rather than clobber; backup-before; verify-after; BOM-free)

Usage:
  python3 merge_config.py claude-md --file <path> --block-file <path> --begin "<marker>" --end "<marker>"
  python3 merge_config.py hook --file <settings.json> --command "<hook cmd>"
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _backup(path: str):
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")


def cmd_claude_md(args):
    f = args.file
    begin, end = args.begin, args.end
    block = Path(args.block_file).read_text(encoding="utf-8").strip()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    if not os.path.exists(f):
        Path(f).write_text("# CLAUDE.md\n\n" + block + "\n", encoding="utf-8")
        print("created (no prior file)")
        return
    _backup(f)
    existing = Path(f).read_text(encoding="utf-8")
    if begin in existing and end in existing:
        pre = existing.split(begin)[0]
        post = existing.split(end, 1)[1]
        updated = pre + block + post
        verb = "updated existing block"
    else:
        updated = existing.rstrip() + "\n\n" + block + "\n"
        verb = "appended block"
    Path(f).write_text(updated, encoding="utf-8")
    # VERIFY: the pre-existing content (minus our block) must survive
    check = Path(f).read_text(encoding="utf-8")
    if begin not in check:
        shutil.copy2(f + ".bak", f)
        print("ABORT: post-write verification failed, restored from .bak", file=sys.stderr)
        sys.exit(1)
    print(f"{verb} (existing content preserved)")


def cmd_hook(args):
    f = args.file
    cmd = args.command
    existing_keys = []
    settings = {}
    if os.path.exists(f):
        _backup(f)
        raw = Path(f).read_text(encoding="utf-8-sig")  # tolerate BOM on read
        if raw.strip():
            try:
                settings = json.loads(raw)
            except json.JSONDecodeError as e:
                # NEVER fall to empty — abort, leave the file untouched
                print(f"ABORT: cannot parse {f} ({e}); refusing to clobber", file=sys.stderr)
                sys.exit(1)
        existing_keys = list(settings.keys())
    settings.setdefault("hooks", {})
    ss = settings["hooks"].get("SessionStart", [])
    present = any(
        h.get("command") == cmd
        for grp in ss if isinstance(grp, dict)
        for h in grp.get("hooks", [])
    )
    if not present:
        ss.append({"hooks": [{"type": "command", "command": cmd}]})
        settings["hooks"]["SessionStart"] = ss
    # Write BOM-free
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
    # VERIFY: every pre-existing top-level key survives
    check = json.loads(Path(f).read_text(encoding="utf-8"))
    missing = [k for k in existing_keys if k not in check]
    if missing:
        shutil.copy2(f + ".bak", f)
        print(f"ABORT: keys lost {missing}, restored from .bak", file=sys.stderr)
        sys.exit(1)
    print(f"hook {'added' if not present else 'already present'} (keys preserved: {list(check.keys())})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="op", required=True)
    a = sub.add_parser("claude-md")
    a.add_argument("--file", required=True)
    a.add_argument("--block-file", required=True)
    a.add_argument("--begin", required=True)
    a.add_argument("--end", required=True)
    a.set_defaults(func=cmd_claude_md)
    h = sub.add_parser("hook")
    h.add_argument("--file", required=True)
    h.add_argument("--command", required=True)
    h.set_defaults(func=cmd_hook)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

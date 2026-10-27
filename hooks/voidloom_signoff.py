#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_signoff.py — Stop / SubagentStop hook.

A quiet sign-off when the agent finishes responding: the sigil + Voidloom with
one fading star. The calm full-stop of the void.

Registered on BOTH Stop and SubagentStop. CHEAP only (no DB read, no model).
FAIL-GRACEFUL: prints nothing on any error, exits 0 so a turn never breaks.

NOTE on restraint: this is a single short line. The brand voice is "one arcane
touch, calm." A sign-off must not become a wall of output on every turn end.
"""
import os
import sys
from pathlib import Path

HOME = os.environ.get("WIZARD_LIBRARY_HOME") or str(Path(__file__).resolve().parent.parent)
# The engine + organ modules moved into organs/ on 2026-09-07. HOME stays the
# tree root (the DB, the manifest, the CLI shim all live there); ORGANS is the
# import path. Probed, so a flat legacy tree still resolves.
ORGANS = HOME if not os.path.isdir(os.path.join(HOME, "organs")) else os.path.join(HOME, "organs")


def main() -> int:
    # Drain stdin so the hook reads cleanly regardless of what Claude Code sends.
    try:
        sys.stdin.read()
    except Exception:
        pass
    try:
        sys.path.insert(0, ORGANS)
        import voidloom_banner
        line = voidloom_banner.build_signoff_line()
        if line:
            sys.stdout.write(line + "\n")
    except Exception:
        pass  # never break a turn
    return 0


if __name__ == "__main__":
    sys.exit(main())

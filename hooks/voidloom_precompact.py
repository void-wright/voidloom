#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_precompact.py — PreCompact hook.

A calm one-line "void-checkpoint" printed as a context-compaction boundary
nears: it states, quietly, what the brain carries across the boundary (the
count of memories captured this session, when derivable).

Claude Code passes hook input as JSON on stdin. We read it best-effort to count
memories stored this session, but the line renders even if stdin is empty or
unparseable. CHEAP only — no embedding model, no network. FAIL-GRACEFUL: any
error prints nothing and exits 0 so compaction is never disturbed.
"""
import json
import os
import sys
from pathlib import Path

HOME = os.environ.get("WIZARD_LIBRARY_HOME") or str(Path(__file__).resolve().parent.parent)
# The engine + organ modules moved into organs/ on 2026-09-07. HOME stays the
# tree root (the DB, the manifest, the CLI shim all live there); ORGANS is the
# import path. Probed, so a flat legacy tree still resolves.
ORGANS = HOME if not os.path.isdir(os.path.join(HOME, "organs")) else os.path.join(HOME, "organs")
DB = os.environ.get("WIZARD_LIBRARY_DB") or str(Path(HOME) / "claude_memory.db")


def _count_memories_this_session(payload: dict) -> "int | None":
    """Best-effort: count memories stored since this session started.

    Strategy: if the hook payload carries a session start time, count rows in
    `memories` created since then. Falls back to None (the surface then shows a
    calm generic marker rather than a number). Pure cheap SQL.
    """
    try:
        import sqlite3
        from datetime import datetime, timezone, timedelta
        # Look for a session start hint in a few plausible payload keys.
        started = (
            payload.get("session_start")
            or payload.get("session_started_at")
            or (payload.get("session") or {}).get("started_at")
        )
        if not Path(DB).exists():
            return None
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
        try:
            if started:
                row = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE created_at >= ?",
                    (str(started),),
                ).fetchone()
            else:
                # No session anchor — count the last hour as a calm proxy.
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S")
                row = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE created_at >= ?",
                    (cutoff,),
                ).fetchone()
            return int(row[0]) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception:
        return None


def main() -> int:
    payload = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
    except Exception:
        payload = {}

    # TERMINAL brand banner (the void-checkpoint) — APPROVALS-ONLY APPEARANCE
    # GATE (Smart Banner v1, supersedes #6023): it is now a SIGNAL, not
    # wallpaper. It prints to the user's terminal ONLY when pending approvals
    # await — the SAME unified self-eval + belief-batch count the session-start
    # banner uses. 0 pending -> no terminal brand banner. read_unified_pending
    # returns 0 on ANY error (never raises), and this whole block stays inside a
    # fail-graceful try, so a count error simply keeps the checkpoint silent and
    # never disturbs compaction. NOTE: this gates ONLY the terminal brand
    # banner; the MODEL-facing M3 capability re-anchor below is NOT gated and
    # fires in BOTH cases (it is the model's context, not the brand banner).
    try:
        sys.path.insert(0, ORGANS)
        import voidloom_banner
        # R3 -- THE FOURTH FALSE-ZERO SURFACE, and it was on no sweep list.
        # `read_unified_pending` collapses "I looked and there is nothing" onto
        # "I could not tell", so an unreadable or absent brain arrived here as a
        # confident 0 and this gate silenced the void-checkpoint -- the same
        # blanking, on a second terminal surface, by the same discarded bit. An
        # UNTRUSTED zero is a reason to speak; a READ zero is not.
        pending_unknown = False
        _unread = ()
        try:
            _t3 = voidloom_banner.read_unified_pending_ex(DB)
            pending, _breakdown, _trusted = _t3
            # R7/C2. The compaction line is a SURFACE and it had no way to tell
            # "nothing was readable" from "governance was absent and the other
            # two answered". It still speaks in both cases -- that part was
            # already right -- but a partial now says so with its count, and a
            # total loss keeps the bare UNKNOWN it has earned.
            _unread = tuple(getattr(_t3, "unread", ()) or ())
            # R7/C5. The shape comes from the breakdown just returned.
            _all_dark = bool(_breakdown) and set(_unread) >= set(_breakdown)
            pending_unknown = (not _trusted) and (not _unread or _all_dark)
            if _unread and not _all_dark:
                print(f"[voidloom_precompact] partial pending count: "
                      f"{', '.join(_unread)} UNREAD; at least {pending}",
                      file=sys.stderr)
        except AttributeError:
            # NO SILENT SHIM: an older banner cannot report trust, so the
            # historical gate is kept and the assumption is SAID rather than
            # made. stderr, never stdout -- stdout is the compaction surface.
            pending, _breakdown = voidloom_banner.read_unified_pending(DB)
            print("[voidloom_precompact] voidloom_banner has no "
                  "read_unified_pending_ex; the count's trustworthiness is "
                  "ASSUMED, not read", file=sys.stderr)
        if pending > 0 or pending_unknown:
            n = _count_memories_this_session(payload)
            line = voidloom_banner.build_precompact_line(
                memories_captured=n, pending_unknown=pending_unknown,
                pending_total=pending, pending_unread=_unread)
            if line:
                sys.stdout.write(line + "\n")
    except Exception:
        pass  # never disturb compaction

    # L5-M3: ONE capability re-anchor line. Compaction wipes learned context,
    # so the brain re-states (in a single line, read from the manifest SSOT)
    # that its L5 organs persist + where the full list lives. Independent
    # try-block: a manifest issue here never suppresses the banner line above.
    # CHEAP (tiny json.loads) + FAIL-GRACEFUL (any error -> no line).
    try:
        import _self_aware
        manifest_path = str(Path(HOME) / "voidloom_manifest.json")
        anchor = _self_aware.render_precompact_line(manifest_path)
        if anchor:
            sys.stdout.write(anchor + "\n")
    except Exception:
        pass  # never disturb compaction
    return 0


if __name__ == "__main__":
    sys.exit(main())

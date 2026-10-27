#!/usr/bin/env python3
"""``trust --id N`` — a read-only CLI handler that prints a memory's trust score
and its component breakdown.

This file ships the ``cmd_trust(args)`` handler plus a short note describing the
exact 3-line argparse wiring to add to ``claude_memory.py``. It does NOT modify
``claude_memory.py`` — the wiring is described, not applied (the orchestrator
applies the artifact to the live tree later, md5-guarded).

The handler is READ-ONLY: it opens the DB, computes the trust score via the pure
``trust_tier`` module, and prints. It writes nothing.

Stand-alone usage (for testing the handler in isolation):

    python3 cmd_trust_handler.py --id 5 --db ~/.claude/memory/claude_memory.db
    python3 cmd_trust_handler.py --id 5 --json --db ~/.claude/memory/claude_memory.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# When wired into claude_memory.py these are siblings; this import path works
# both as a sibling module and when claude_memory.py imports `trust_tier`.
try:
    from trust_tier import compute_trust, edge_authority, _trust_components, TRUST_WEIGHTS
except ImportError:  # pragma: no cover - allows execution from another CWD
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from trust_tier import compute_trust, edge_authority, _trust_components, TRUST_WEIGHTS


# Default DB path mirrors the one brain. claude_memory.py already resolves its
# own DB path; when wired in, prefer the host's resolver over this constant.
# R4/P5. The default now lives HERE and only here; the argparse layer
# passes None so the resolver below can see the environment.
# R5/C6 (R4-F6). WAS the literal "<owner-home>/.claude/memory/claude_memory.db".
# R4 removed it from the argparse default and left it as the resolver's last
# resort -- a fold relocates a defect, it does not remove it. Relative-self is
# the house property and `affect_probe_scan.py` already had it.
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "claude_memory.db")


def _resolve_db_path(value):
    """explicit --db > $WIZARD_LIBRARY_DB > $CLAUDE_MEMORY_DB > relative-self.

    R5/C6 (R4-F7). THE PREVIOUS COMMENT'S CLAIM WAS FALSE and is not repeated.
    `wizard_paths.get_db_path()` is override > $WIZARD_LIBRARY_DB > relative-self:
    it reads NO CLAUDE_MEMORY_DB. This resolver EXTENDS the house order with a
    CLAUDE_MEMORY_DB tier below the installer-authored variable; it does not
    mirror it. A comment asserting a match that does not hold is the
    declaration-bound-to-nothing class committed in prose instead of in code --
    the founding class of this whole build.
    """
    return (value or os.environ.get("WIZARD_LIBRARY_DB")
            or os.environ.get("CLAUDE_MEMORY_DB") or DEFAULT_DB_PATH)


def _open_ro(db_path: str) -> sqlite3.Connection:
    """Open the DB read-only via a URI so the handler can never mutate it."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _render_human(mem_id: int, comp: dict, edge: dict | None) -> str:
    lines = []
    lines.append(f"Trust for memory #{mem_id}: {comp['score']:.4f}")
    lines.append("")
    lines.append("  Components (each in [0,1]):")
    lines.append(f"    authorship FLOOR   : {_fmt(comp.get('authorship'))}"
                 f"   (the minimum; dominant signal)")
    lines.append(f"    self-confidence    : {_fmt(comp.get('self_confidence'))}"
                 f"   (headroom weight {TRUST_WEIGHTS['self_confidence']:.2f})")
    lines.append(f"    corroboration      : {_fmt(comp.get('corroboration'))}"
                 f"   (headroom weight {TRUST_WEIGHTS['corroboration']:.2f})")
    lines.append(f"    citation bonus     : {_fmt(comp.get('citation_bonus'))}"
                 f"   (headroom weight {TRUST_WEIGHTS['citation_bonus']:.2f})")
    frac = comp.get("foundation_fraction")
    auth = comp.get("authorship")
    floor = comp.get("lifted_floor")
    if frac is None:
        lines.append("    foundation lift    : OMITTED (foundation_score is NULL — no penalty)")
    else:
        lines.append(f"    foundation lift    : +{frac:.4f} of the gap "
                     f"(floor {_fmt(auth)} -> {_fmt(floor)})")
    lines.append("")
    lines.append(f"  headroom (3-5 blend) : {_fmt(comp.get('headroom'))}")
    lines.append(f"  lifted floor         : {_fmt(floor)}   (score = floor + (1-floor)*headroom)")
    lines.append(f"  note                 : {comp.get('note')}")
    if edge is not None:
        lines.append("")
        lines.append(f"  edge_authority(#{edge['a']}, #{edge['b']}) = min(trust) = {edge['value']:.4f}")
    return "\n".join(lines)


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def cmd_trust(args) -> int:
    """Handler for ``trust --id N`` (and optional ``--edge A B``).

    Reads the DB read-only, computes trust via the pure module, and prints
    either a human breakdown or a JSON envelope. Returns a process exit code
    (0 ok, non-zero on a usage/IO problem) — it NEVER raises into the caller.
    """
    # R4/P5. This line read NO environment variable, so with argparse
    # also supplying a hardcoded default the canonical path was the only
    # brain this verb could ever open -- on any machine, for any user.
    # Order matches wizard_paths.get_db_path(), the house resolver.
    db_path = _resolve_db_path(getattr(args, "db", None))

    # --edge A B mode: print just the edge authority (still read-only).
    edge_pair = getattr(args, "edge", None)
    mem_id = getattr(args, "id", None)

    if mem_id is None and not edge_pair:
        msg = {"status": "error", "message": "trust requires --id N (optionally --edge A B)"}
        print(json.dumps(msg) if getattr(args, "json", False) else msg["message"],
              file=sys.stderr)
        return 2

    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as e:
        msg = {"status": "error", "message": f"cannot open DB read-only: {e}"}
        print(json.dumps(msg) if getattr(args, "json", False) else msg["message"],
              file=sys.stderr)
        return 1

    try:
        edge_block = None
        if edge_pair:
            a, b = int(edge_pair[0]), int(edge_pair[1])
            edge_block = {"a": a, "b": b, "value": edge_authority(conn, a, b)}

        if mem_id is not None:
            comp = _trust_components(conn, int(mem_id))
            if getattr(args, "json", False):
                out = {"status": "ok", "memory_id": int(mem_id), **comp}
                if edge_block:
                    out["edge_authority"] = edge_block
                print(json.dumps(out, indent=2))
            else:
                print(_render_human(int(mem_id), comp, edge_block))
        else:
            # edge-only mode
            if getattr(args, "json", False):
                print(json.dumps({"status": "ok", "edge_authority": edge_block}, indent=2))
            else:
                print(f"edge_authority(#{edge_block['a']}, #{edge_block['b']}) "
                      f"= min(trust) = {edge_block['value']:.4f}")
        return 0
    except Exception as e:  # never raise into the CLI dispatcher
        msg = {"status": "error", "message": f"trust computation failed: {e}"}
        print(json.dumps(msg) if getattr(args, "json", False) else msg["message"],
              file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Argparse wiring note for claude_memory.py
# ---------------------------------------------------------------------------
#
# claude_memory.py uses a subparser-per-command pattern. To wire `trust` in,
# add exactly three things alongside the existing subcommands (no other edits):
#
#   1. Import the handler near the other command imports at the top of the file:
#
#          from trust_tier_cli import cmd_trust   # (or paste cmd_trust inline)
#
#   2. Register the subparser where the other `subparsers.add_parser(...)` calls
#      live (one logical statement):
#
#          p_trust = subparsers.add_parser(
#              "trust", help="Show the derived trust score + components for a memory")
#          p_trust.add_argument("--id", type=int, help="memory id to score")
#          p_trust.add_argument("--edge", nargs=2, metavar=("A", "B"), type=int,
#                               help="also print edge_authority = min(trust(A), trust(B))")
#          p_trust.add_argument("--json", action="store_true", help="JSON output")
#          p_trust.set_defaults(func=cmd_trust)
#
#   3. Nothing else — the existing `args.func(args)` dispatch at the bottom of
#      main() already routes `trust` to cmd_trust. The host's own DB-path
#      resolver is preferred; if claude_memory.py exposes a resolved path, pass
#      it through as `args.db` (the handler falls back to DEFAULT_DB_PATH).
#
# The handler is READ-ONLY (opens with `mode=ro`), so it is safe to register
# without any write-guard changes elsewhere in claude_memory.py.
# ---------------------------------------------------------------------------


def _standalone_cli() -> int:
    parser = argparse.ArgumentParser(
        description="trust --id N : print a memory's derived trust score + breakdown")
    parser.add_argument("--id", type=int, default=None, help="memory id to score")
    parser.add_argument("--edge", nargs=2, metavar=("A", "B"), type=int, default=None,
                        help="also print edge_authority = min(trust(A), trust(B))")
    parser.add_argument("--json", action="store_true", help="JSON output")
    # R4/P5 (sibling 1 of 3). Same class as belief_cli.py:265.
    parser.add_argument("--db", default=None,
                        help="path to the memory DB (default: "
                             "$CLAUDE_MEMORY_DB, else the canonical path)")
    args = parser.parse_args()
    return cmd_trust(args)


if __name__ == "__main__":
    raise SystemExit(_standalone_cli())

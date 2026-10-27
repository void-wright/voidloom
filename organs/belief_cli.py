#!/usr/bin/env python3
"""``belief ...`` — the CLI handler for the L5-D belief engine.

Mirrors the trust_tier_cli.py convention exactly: this file ships the
``cmd_belief(args)`` handler plus a precise wiring note for claude_memory.py. It
does NOT modify claude_memory.py — the orchestrator applies the artifact later,
md5-guarded.

Sub-actions (selected via the first positional `--action`-style flag):

    belief scan                  -> drain belief_scan_queue, NLI dirty x top-k,
                                    write add_contradicts PROPOSALS (no edges).
    belief remediate             -> dry-run remediation of heuristic contradicts;
                                    prints the tally (NOTHING written).
    belief remediate --apply     -> write the invalidate PROPOSALS as ONE batch
                                    (still no memory_edges change — enactment is
                                    via `belief approvals --approve`).
    belief approvals             -> list pending proposal BATCHES (banner +1) or,
                                    with --batch, the proposals in one batch;
                                    --approve <ids> / --reject <ids> to enact.
    belief as-of DATE            -> the bi-temporal read: contradicts edges valid
                                    at an ISO date (time travel).
    belief quarantine            -> what is quarantined now (derived counts by
                                    family, the open pairs) plus what a run WOULD
                                    quarantine (read-only).
    belief quarantine --apply    -> quarantine every contradiction in the false
                                    class: the same organ template on both ends, or
                                    an end no person authored (family not_authored)
                                    -- edges, pending proposals, the gaps they
                                    authored. Reversible, never a DELETE.
    belief quarantine --release-pair A B | --release-family F | --release-ids ...
                                 -> restore the pre-image (only where the object
                                    still reads as the quarantine left it).

`scan` applies the quarantine first (idempotent; a released pair is never
quarantined again), so the nightly unit retires the class without a human step.

The handler resolves the DB path from the host (claude_memory.py passes its
resolved path through args.db) and falls back to the one-brain path. `scan`,
`remediate --apply`, and `approvals --approve/--reject` are WRITE actions;
`remediate` (dry) / `approvals` (list) / `as-of` are read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

try:
    import belief_engine as BE
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import belief_engine as BE


# R5/C6 (R4-F3 + R4-F6). WAS the literal "<owner-home>/.claude/memory/claude_memory.db".
# Two defects in one constant: it is wrong on every machine but this one, and it
# was the LAST-RESORT tier, so R4's fix (removing it from the argparse default)
# relocated the defect instead of removing it. The house property is relative-
# self -- `wizard_paths.get_db_path()`'s third tier -- which is what let the
# Great Centralization move ~160 path references for zero code edits (#4243).
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "claude_memory.db")


def _resolve_db(args) -> str:
    """explicit --db > $WIZARD_LIBRARY_DB > $CLAUDE_MEMORY_DB > relative-self.

    R5/C6 (R4-F3). WIZARD_LIBRARY_DB WAS NOT READ HERE. `install.sh:246` writes
    `export WIZARD_LIBRARY_DB` into the permanent env file and all nine
    wizard-library systemd services source it, so the variable is SET on every
    installed machine while CLAUDE_MEMORY_DB generally is not. This module was
    therefore the one of four siblings that opened a DIFFERENT brain from the
    other three on any machine but this one -- and after R4's P4 guard it stopped
    silently opening the wrong brain and started confidently refusing about the
    wrong file, which is not an improvement.

    EXTENDS, not mirrors, `wizard_paths.get_db_path()`: the house resolver has no
    CLAUDE_MEMORY_DB tier. That tier is kept because three siblings already read
    it and dropping it would break them; it sits BELOW WIZARD_LIBRARY_DB so the
    installer-authored variable wins, which is the house precedence.
    """
    return (getattr(args, "db", None)
            or os.environ.get("WIZARD_LIBRARY_DB")
            or os.environ.get("CLAUDE_MEMORY_DB")
            or DEFAULT_DB_PATH)


class BrainNotFound(FileNotFoundError):
    """R4/P4. The named refusal, raised where the brain would be CREATED.

    `cmd_belief` already converts any exception into a clean
    `{"status": "error", ...}` envelope with exit 1, so raising here is
    the house pattern expressed in this module's own idiom -- an
    existence check plus a NAMED error, never a `?mode=rw` URI.
    """


def _require_brain(db_path: str) -> str:
    # R4/P4 pass 3: a 0-byte file is neither a brain nor an absence.
    p = Path(db_path)
    if not p.is_file():
        raise BrainNotFound(
            f"brain_not_found: no database at {db_path}. Refusing to "
            f"CREATE one; run 'claude_memory.py init' first, or pass "
            f"--db / set CLAUDE_MEMORY_DB.")
    try:
        empty = p.stat().st_size == 0
    except OSError:
        empty = False
    if empty:
        raise BrainNotFound(
            f"brain_empty: the file at {db_path} is 0 bytes -- the "
            f"wreckage of a failed init or copy, not a brain. Remove "
            f"it, then run 'claude_memory.py init'.")
    return db_path


def _open_rw(db_path: str) -> sqlite3.Connection:
    # R4/P4 -- THE THIRD CREATING VERB (DESSA). Every belief sub-action
    # reaches a connect through this function or its read-only sibling,
    # so ONE check here covers scan / remediate / approvals / as-of
    # rather than four copies that can drift apart.
    conn = sqlite3.connect(_require_brain(db_path), timeout=60.0)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _open_ro(db_path: str) -> sqlite3.Connection:
    # The read-only sibling did not CREATE, but it failed with a raw
    # sqlite3 'unable to open database file' that names neither the path
    # nor the remedy. Same named refusal, so both halves of the module
    # answer an absent brain the same way.
    uri = f"file:{Path(_require_brain(db_path)).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _emit(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
# Sub-action handlers.
# ---------------------------------------------------------------------------

def _do_scan(args) -> int:
    db = _resolve_db(args)
    conn = _open_rw(db)
    try:
        if not BE.migration_applied(conn):
            _emit({"status": "error",
                   "message": "belief_engine substrate not present — apply migration_023 first"},
                  getattr(args, "json", False))
            return 1
        # The quarantine runs FIRST, so the scan's suppression (an existing edge or
        # a decided proposal) and the gap map both read the retired state.
        quarantine = BE.quarantine_template_pairs(conn, apply=True)
        out = BE.nightly_scan(conn, limit=getattr(args, "limit", None),
                              neighbor_k=getattr(args, "neighbor_k", 10))
        out["quarantine"] = quarantine
        _emit(out, getattr(args, "json", False))
        return 0
    finally:
        conn.close()


def _do_remediate(args) -> int:
    db = _resolve_db(args)
    apply = getattr(args, "apply", False)
    # dry-run is read-only; --apply needs write.
    conn = _open_rw(db) if apply else _open_ro(db)
    try:
        if not BE.migration_applied(conn):
            _emit({"status": "error",
                   "message": "belief_engine substrate not present — apply migration_023 first"},
                  getattr(args, "json", False))
            return 1
        out = BE.remediate_heuristic_contradicts(conn, dry_run=not apply)
        # Trim the sample arrays for human output unless --json.
        if not getattr(args, "json", False):
            out = {k: v for k, v in out.items()
                   if k not in ("keep_samples", "invalidate_samples")}
        _emit(out, getattr(args, "json", False))
        return 0
    finally:
        conn.close()


def _do_approvals(args) -> int:
    db = _resolve_db(args)
    approve = getattr(args, "approve", None)
    reject = getattr(args, "reject", None)
    write_mode = bool(approve or reject)
    conn = _open_rw(db) if write_mode else _open_ro(db)
    try:
        if not BE.migration_applied(conn):
            _emit({"status": "error", "message": "belief_engine substrate not present"},
                  getattr(args, "json", False))
            return 1
        if approve:
            ids = [int(x) for x in approve]
            out = BE.approve_proposals(conn, ids)
            _emit({"status": "ok", "action": "approve", **out}, getattr(args, "json", False))
            return 0
        if reject:
            ids = [int(x) for x in reject]
            out = BE.reject_proposals(conn, ids, reason=getattr(args, "reason", None))
            _emit({"status": "ok", "action": "reject", **out}, getattr(args, "json", False))
            return 0
        batch = getattr(args, "batch", None)
        if batch:
            props = BE.get_proposals(conn, report_batch=batch, status="pending")
            _emit({"status": "ok", "report_batch": batch, "proposals": props},
                  getattr(args, "json", False))
        else:
            batches = BE.pending_report_batches(conn)
            _emit({"status": "ok", "pending_batches": batches,
                   "pending_batch_count": len(batches)},
                  getattr(args, "json", False))
        return 0
    finally:
        conn.close()


def _do_as_of(args) -> int:
    db = _resolve_db(args)
    iso_date = getattr(args, "date", None)
    if not iso_date:
        _emit({"status": "error", "message": "belief as-of requires DATE (ISO)"},
              getattr(args, "json", False))
        return 2
    conn = _open_ro(db)
    try:
        edges = BE.beliefs_as_of(conn, iso_date,
                                 predicate=getattr(args, "predicate", "contradicts"))
        _emit({"status": "ok", "as_of": iso_date, "count": len(edges), "edges": edges},
              getattr(args, "json", False))
        return 0
    finally:
        conn.close()


def _do_quarantine(args) -> int:
    db = _resolve_db(args)
    release_pair = getattr(args, "release_pair", None)
    release_family = getattr(args, "release_family", None)
    release_ids = getattr(args, "release_ids", None)
    releasing = bool(release_pair or release_family or release_ids)
    apply = getattr(args, "apply", False)
    as_json = getattr(args, "json", False)
    if releasing and apply:
        _emit({"status": "error", "message": "--apply and a --release-* flag are opposite actions; pass one"}, as_json)
        return 2
    conn = _open_rw(db) if (apply or releasing) else _open_ro(db)
    try:
        if not BE.quarantine_available(conn):
            _emit({"status": "error",
                   "message": "belief_quarantine is absent: apply migrations/migration_041_belief_quarantine.sql "
                              "(the installer's migration step)"}, as_json)
            return 1
        if releasing:
            out = BE.release_quarantine(
                conn, quarantine_ids=release_ids,
                pair=tuple(release_pair) if release_pair else None,
                family=release_family, note=getattr(args, "reason", None))
            _emit({"action": "release", **out}, as_json)
            return 0 if out.get("status") == "ok" else 1
        would = BE.quarantine_template_pairs(conn, apply=apply)
        current = BE.quarantine_report(conn, limit=getattr(args, "limit", None) or 20)
        _emit({"status": "ok", "action": "apply" if apply else "dry-run",
               ("quarantined_now" if apply else "would_quarantine"): would,
               "quarantine": current}, as_json)
        return 0
    finally:
        conn.close()


_ACTIONS = {
    "quarantine": _do_quarantine,
    "scan": _do_scan,
    "remediate": _do_remediate,
    "approvals": _do_approvals,
    "as-of": _do_as_of,
}


def cmd_belief(args) -> int:
    """Dispatcher for the `belief` verb. NEVER raises into the CLI dispatcher."""
    action = getattr(args, "belief_action", None)
    if action not in _ACTIONS:
        _emit({"status": "error",
               "message": f"belief action must be one of {sorted(_ACTIONS)}"},
              getattr(args, "json", False))
        return 2
    try:
        return _ACTIONS[action](args)
    except Exception as e:  # graceful: never crash the CLI
        _emit({"status": "error", "message": f"belief {action} failed: {e}"},
              getattr(args, "json", False))
        return 1


# ---------------------------------------------------------------------------
# Argparse wiring note for claude_memory.py
# ---------------------------------------------------------------------------
#
# Mirror the `trust` verb wiring EXACTLY (graceful import + subparser + cmd_map):
#
#   1. Graceful import near the trust/approvals imports (~line 295-307):
#
#          # L5-D Belief Engine. "belief" is a thin wrapper over belief_engine.py;
#          # the CLI handler lives in belief_cli.py. Same dual-path graceful-degrade.
#          try:
#              from .belief_cli import cmd_belief  # type: ignore
#          except (ImportError, ValueError):
#              try:
#                  from belief_cli import cmd_belief  # type: ignore
#              except ImportError:
#                  def cmd_belief(args):  # graceful: belief command degrades
#                      print(json.dumps({"status": "error",
#                                        "message": "belief_cli module not available"}))
#                      sys.exit(1)
#
#   2. Register the subparser alongside the others (after p_trust, ~line 2520):
#
#          p_belief = subparsers.add_parser(
#              "belief", help="Belief engine: scan / remediate / approvals / as-of")
#          p_belief.add_argument("belief_action",
#                                choices=["scan", "remediate", "approvals", "as-of", "quarantine"],
#                                help="belief sub-action")
#          p_belief.add_argument("date", nargs="?", default=None,
#                                help="ISO date (for `belief as-of DATE`)")
#          p_belief.add_argument("--apply", action="store_true",
#                                help="remediate: write invalidate PROPOSALS (default: dry-run)")
#          p_belief.add_argument("--batch", default=None,
#                                help="approvals: show proposals in one report_batch")
#          p_belief.add_argument("--approve", nargs="+", type=int, default=None,
#                                metavar="PROPOSAL_ID", help="approvals: enact these proposal IDs")
#          p_belief.add_argument("--reject", nargs="+", type=int, default=None,
#                                metavar="PROPOSAL_ID", help="approvals: reject these proposal IDs")
#          p_belief.add_argument("--reason", default=None, help="reason recorded with a rejection")
#          p_belief.add_argument("--limit", type=int, default=None, help="scan: max dirty rows")
#          p_belief.add_argument("--neighbor-k", dest="neighbor_k", type=int, default=10,
#                                help="scan: top-k embedding neighbours per dirty memory")
#          p_belief.add_argument("--json", action="store_true", help="JSON output")
#
#   3. Add to cmd_map (~line 2549):  "belief": cmd_belief,
#
# Nothing else. The handler resolves its own DB path (host path via args.db if
# claude_memory.py passes it; else CLAUDE_MEMORY_DB env; else DEFAULT_DB_PATH).
# Dry-run remediation + approvals-list + as-of open read-only; scan,
# remediate --apply, and approve/reject open read-write.
# ---------------------------------------------------------------------------


def _standalone_cli() -> int:
    parser = argparse.ArgumentParser(
        description="belief — Voidloom L5-D belief engine CLI")
    parser.add_argument("belief_action",
                        choices=["scan", "remediate", "approvals", "as-of", "quarantine"])
    parser.add_argument("date", nargs="?", default=None,
                        help="ISO date for `belief as-of DATE`")
    parser.add_argument("--apply", action="store_true",
                        help="remediate: write invalidate proposals (default dry-run)")
    parser.add_argument("--batch", default=None,
                        help="approvals: show proposals in one report_batch")
    parser.add_argument("--approve", nargs="+", type=int, default=None, metavar="ID")
    parser.add_argument("--reject", nargs="+", type=int, default=None, metavar="ID")
    parser.add_argument("--reason", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--neighbor-k", dest="neighbor_k", type=int, default=10)
    parser.add_argument("--predicate", default="contradicts")
    parser.add_argument("--release-pair", dest="release_pair", nargs=2, type=int, default=None,
                        metavar=("A", "B"), help="quarantine: release every row for this memory pair")
    parser.add_argument("--release-family", dest="release_family", default=None,
                        choices=[f for f, _ in BE.TEMPLATE_FAMILIES] + [BE.NON_AUTHORED_FAMILY],
                        help="quarantine: release every open row of one family")
    parser.add_argument("--release-ids", dest="release_ids", nargs="+", type=int, default=None,
                        metavar="ID", help="quarantine: release these belief_quarantine rows")
    parser.add_argument("--json", action="store_true")
    # R4/P5. `default=DEFAULT_DB_PATH` made `_resolve_db`'s
    # `getattr(args, "db", None) or os.environ.get("CLAUDE_MEMORY_DB")`
    # unreachable: argparse always supplied a truthy first operand, so
    # the env var could never win and the `or` chain was decoration. The
    # default belongs in ONE place -- the resolver -- and the resolver
    # already has it.
    parser.add_argument("--db", default=None,
                        help="path to the memory DB (default: "
                             "$CLAUDE_MEMORY_DB, else the canonical path)")
    args = parser.parse_args()
    return cmd_belief(args)


if __name__ == "__main__":
    raise SystemExit(_standalone_cli())

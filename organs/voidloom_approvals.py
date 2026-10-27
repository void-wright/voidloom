#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_approvals.py — the ONE unified approval surface.

Drew has TWO independent things that need his approval:

  * the monthly **self-eval** proposals (Loop C) — one pending unit per pending
    row in `self_eval_reports`, enacted via monthly_self_eval.{drew_review,
    enact_approved} (the `approvals` verb).
  * the **belief-engine** edge proposals (L5-D) — one pending unit per pending
    `report_batch` in `edge_proposals`, enacted via belief_engine.approve_/
    reject_proposals (the `belief approvals` verb).

This module weaves BOTH into a single, ordered review list so Drew sees the
combined picture in one place and can approve items one-by-one across both
surfaces. It is the surface the Voidloom banner's `→ voidloom approvals` pointer
now points at.

DESIGN CONTRACTS (load-bearing):

  * COUNT BY BATCH/REPORT, never per-proposal. A 148-proposal belief batch is
    ONE review item (a 148-proposal banner +1, not +148). A self-eval report is
    ONE review item regardless of how many sub-proposals it carries. This is the
    anti-alarm-fatigue contract shared with the banner.

  * READ-ONLY AGGREGATION. `list_pending` / `render` open the DB read-only and
    mutate nothing. Each surface is read INDEPENDENTLY and is fail-graceful: one
    surface erroring (table absent, module missing, db locked) yields [] for
    that surface while the other still contributes. Both failing -> [] -> the
    banner stays silent. NEVER raises.

  * DISPATCH, DO NOT RE-IMPLEMENT. `approve` / `reject` route to the EXISTING
    enactment paths — self-eval -> monthly_self_eval; belief -> belief_engine.
    No enactment logic is duplicated here. We are a router + a presenter.

  * EACH ITEM CARRIES ITS OWN COMMAND. Every review item includes the EXACT
    `approve_cmd` / `reject_cmd` Drew can copy-paste, so "approve one-by-one" is
    literally a list of one-line commands.

The CLI is intentionally thin; the same logic is invoked from claude_memory.py
(see INTEGRATION_claude_memory.md) as the unified `approvals` experience.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# R5/C6 (class sweep, not on any finding's list). MEASURED: with a clean
# environment, `python3 voidloom_approvals.py` run from ANY tree resolved to
# THIS literal and read the live brain rather than the brain beside it -- it
# printed "1 item await" about a database nobody handed it, standing in a
# directory whose own brain had none. On a stranger's machine it names a path
# that does not exist. `voidloom_banner.py`, the sibling in the same directory,
# already gets this right; relative-self is the house property.
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "claude_memory.db")

SURFACE_SELF_EVAL = "self-eval"
SURFACE_BELIEF = "belief"
SURFACE_GOVERNANCE = "governance"
SURFACES = (SURFACE_SELF_EVAL, SURFACE_BELIEF, SURFACE_GOVERNANCE)


def _default_actor() -> str:
    """Who is acting, DERIVED -- never a shipped personal handle.

    Crucible-3 (pii_gate, H:handle-default): this defaulted to a literal personal
    handle, so on anyone else's machine a comment or decision was attributed to a
    person who was not there. Resolution order, most explicit first:
    ``VOIDLOOM_ACTOR`` > the OS user > ``"operator"``.

    NOTE FOR AN EXISTING BRAIN: rows written before this change carry the old
    literal. Nothing in the tree matches on the value -- it is written and read
    back for display, never compared -- so the mixture is cosmetic; set
    ``VOIDLOOM_ACTOR`` to keep one label across the seam if you want uniformity.
    """
    import getpass
    v = (os.environ.get("VOIDLOOM_ACTOR") or "").strip()
    if v:
        return v
    try:
        return getpass.getuser() or "operator"
    except Exception:
        return "operator"


def _all_source_keys() -> tuple:
    """The BREAKDOWN vocabulary (underscore), taken from its owner.

    R7/C5. This is a DIFFERENT vocabulary from `SURFACES` (hyphen), and the
    temptation is to spell it here beside it -- which is how four spellings of
    the first one came about. It is read from `decision_units.BREAKDOWN_KEYS`;
    when that module is absent the answer is the EMPTY tuple, and every caller
    treats an empty key set as "I cannot prove everything went dark", which
    resolves toward publishing the count rather than refusing.
    """
    if _DU is not None:
        return tuple(getattr(_DU, "BREAKDOWN_KEYS", ()) or ())
    return ()


def _all_sources_unread(unread, breakdown=None) -> bool:
    """True only when NOTHING was read -- the one state that earns a refusal.

    R7/C5. THE KEY SET IS NEVER SPELLED HERE. A literal fallback
    `{"self_eval","belief","governance"}` would be a fresh re-spelling of the
    vocabulary inside the fix for re-spelling, and the round's own sweep
    returned six of them. With no breakdown in hand the answer is FALSE: we
    cannot prove every source went dark, and [D4] rules that the doubt resolves
    toward PUBLISHING the count, never toward a bare refusal.
    """
    _un = set(str(x) for x in (unread or ()))
    _keys = set(breakdown or ())
    return bool(_keys) and _un >= _keys


def _partial_reason(unread) -> str:
    """The refusal sentence, with the source that ACTUALLY failed in it."""
    _un = [str(x) for x in (unread or ())]
    if not _un:
        return ("the decision ledger could not confirm it read the pending "
                "sources; the count is UNKNOWN, not zero")
    return ("the " + ", ".join(_un) + " source(s) could not be read (absent or "
            "unreadable), so the count below is a LOWER BOUND and the true "
            "total is UNKNOWN, not zero")

# D4: the canonical tagged-ref shape across all three faces. The raw part allows
# colons (belief batch ids embed ISO timestamps) and the slug chars; governance
# is a first-class surface here (the CLI ref-regex must match this set).
# R7/C5 -- BUILT FROM THE VOCABULARY, NEVER SPELLED BESIDE IT. This was the
# FIFTH spelling of the three surface names: a regex literal inside the very
# module that declares the constants twenty lines above. It is now derived, and
# `_bind_surface_vocabulary()` below makes a drift raise at import.
APPROVAL_REF_RE = re.compile(
    r"^(" + "|".join(re.escape(s) for s in SURFACES) + r"):[A-Za-z0-9_.:-]+$")

# A human-authored comment is high-trust per L5-I-alpha: compute_trust floors a
# human-authored memory at AUTHORED_FLOOR (0.85). We record that floor on the
# comment so a downstream consumer knows it OUT-RANKS any auto-derived signal
# without recomputing. (Kept as a module constant so it tracks one place.)
COMMENT_TRUST_FLOOR = 0.85

# The trust organ (L5-I-alpha). Optional import — the comment record stands on
# its own (with COMMENT_TRUST_FLOOR) even if the organ is absent; when present we
# can attach a live compute_trust() reading of the underlying memory.
try:  # pragma: no cover - exercised when the organ is present
    import trust_tier as _TRUST  # type: ignore
except Exception:  # pragma: no cover - degrades to the static floor
    _TRUST = None  # type: ignore


# ---------------------------------------------------------------------------
# Defensive, dual-path imports of the two enactment modules. Either may be
# absent (a not-yet-migrated brain, a stripped public repo); the surface simply
# contributes nothing from a missing source. This mirrors the trust/belief verb
# graceful-import convention.
# ---------------------------------------------------------------------------

try:  # belief engine (L5-D)
    import belief_engine as _BE  # type: ignore
except Exception:  # pragma: no cover - belief surface degrades to empty
    _BE = None  # type: ignore

try:  # self-eval (Loop C / L4 Piece 9)
    import monthly_self_eval as _SE  # type: ignore
except Exception:  # pragma: no cover - self-eval surface degrades to empty
    _SE = None  # type: ignore

try:  # governance gate (L5-I-β) — HOLD batches surfaced here
    import governance as _GOV  # type: ignore
except Exception:  # pragma: no cover - governance surface degrades to empty
    _GOV = None  # type: ignore


# ---------------------------------------------------------------------------
# Path + connection helpers.
# ---------------------------------------------------------------------------


def resolve_db(db: Optional[str] = None) -> str:
    return (db
            or os.environ.get("WIZARD_LIBRARY_DB")
            or os.environ.get("CLAUDE_MEMORY_DB")
            or DEFAULT_DB_PATH)


# R10 — THE ONE COUNT FUNCTION lives in decision_units. This module delegates
# to it; the local `unified_count` is kept as the public name its callers use.
try:
    import decision_units as _DU  # type: ignore
except Exception as _du_e:  # pragma: no cover - defensive
    _DU = None  # type: ignore
    _DU_IMPORT_ERROR = f"{type(_du_e).__name__}: {_du_e}"
else:
    _DU_IMPORT_ERROR = None


def _bind_surface_vocabulary() -> None:
    """FAIL LOUD IF THE TWO DECLARATIONS OF THE SAME THREE WORDS DIVERGE.

    R7/C5, and this is the assert the class was missing. Four spellings of the
    surface names existed across the build -- `decision_units`' constants, this
    module's copy, this module's ref-regex and the CLI's ref-regex -- with about
    twenty dispatch sites comparing `_DU`-produced `u.surface` values against
    THIS module's copy, and nothing anywhere comparing the copies.

    MEASURED 2026-09-04: changing ONE CHARACTER in `decision_units.py`
    (`self-eval` -> `selfeval`) imported cleanly, raised nothing, degraded
    nothing, and dispatched 0 of 9 units where the undrifted control dispatched
    9 of 9. The produced objects were internally inconsistent too -- `surface`
    from the drifted module constant, `key` from the stored `unit_key` column --
    so the ref-regex still matched while dispatch returned nothing.

    A vocabulary that no assert binds is not a vocabulary; it is N independent
    constants that happen to agree today. This runs at import, so the drift is
    an ImportError at the top of the process rather than an empty screen at the
    bottom of it.
    """
    if _DU is None:
        return
    _theirs = getattr(_DU, "SURFACES", None)
    if _theirs is None:
        _theirs = tuple(
            getattr(_DU, n) for n in ("SURFACE_SELF_EVAL", "SURFACE_BELIEF",
                                      "SURFACE_GOVERNANCE")
            if hasattr(_DU, n))
    if tuple(_theirs) != SURFACES:
        raise ImportError(
            "SURFACE VOCABULARY DRIFT: decision_units declares "
            f"{tuple(_theirs)!r} and voidloom_approvals declares {SURFACES!r}. "
            "About twenty dispatch sites compare values produced by the first "
            "against constants spelled in the second; a mismatch dispatches 0 "
            "of N silently. Refusing to import on a vocabulary that does not "
            "agree with itself.")


_bind_surface_vocabulary()

# R2 — THE ABSENT-LEDGER FALLBACK, AND STILL ONE SPELLING. When `decision_units`
# cannot be imported at all there is no one function to defer to, so the
# historical contract has to live somewhere. It lives ONCE, in `voidloom_banner`
# (`legacy_self_eval_report_ids` / `legacy_belief_batch_count`), and this module
# reaches it rather than re-typing the SQL. That is R10's rule kept, not waived:
# R10 forbids a second SPELLING of a query, and this is the first and only one.
try:
    import voidloom_banner as _VB  # type: ignore
except Exception as _vb_e:  # pragma: no cover - defensive
    _VB = None  # type: ignore
    _VB_IMPORT_ERROR = f"{type(_vb_e).__name__}: {_vb_e}"
else:
    _VB_IMPORT_ERROR = None


def _open_ro(db_path: str) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def _open_rw(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _relation_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Table OR VIEW. Use this for anything migration 030 defines as a view.

    THE TRAP THIS EXISTS TO CLOSE: `_table_exists` matches `type='table'` only,
    which is correct for a table and SILENTLY WRONG for a view. Guarding the
    ledger-aware branch of `_self_eval_items` with it made the branch look
    present and never run -- the feature reported as shipped while every query
    took the legacy path. Measured 2026-09-02: `listed=[7] counted=[3,5,7]`,
    with the ledger term compiled into the SQL string and unreachable.
    """
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


# ===========================================================================
# D4 substrate — the migration + the three D4 state surfaces:
#   * comment-as-high-trust-training-signal  (approval_comments)
#   * enact audit-log                          (approval_audit_log)
#   * calm ping-on-count-change + snooze       (approval_ping_state)
# All additive + idempotent (dream_pipeline / governance migration_028 pattern).
# ===========================================================================

D4_DDL = """
CREATE TABLE IF NOT EXISTS approval_comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    surface         TEXT NOT NULL,
    ref             TEXT NOT NULL,
    item_title      TEXT DEFAULT NULL,
    item_ids        TEXT DEFAULT NULL,
    decision        TEXT NOT NULL
                    CHECK (decision IN ('approved', 'rejected', 'snoozed', 'noted')),
    comment         TEXT NOT NULL,
    author          TEXT NOT NULL DEFAULT 'operator',
    trust_floor     REAL NOT NULL DEFAULT 0.85,
    training_memory_id INTEGER DEFAULT NULL,
    consumed        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_appcomment_ref      ON approval_comments(ref);
CREATE INDEX IF NOT EXISTS idx_appcomment_consumed ON approval_comments(consumed);
CREATE INDEX IF NOT EXISTS idx_appcomment_surface  ON approval_comments(surface);

CREATE TABLE IF NOT EXISTS approval_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    surface         TEXT NOT NULL,
    ref             TEXT NOT NULL,
    action          TEXT NOT NULL
                    CHECK (action IN ('approve', 'reject')),
    decision_detail TEXT DEFAULT NULL,
    comment_id      INTEGER DEFAULT NULL,
    confirmed_via   TEXT NOT NULL,
    ref_valid       INTEGER NOT NULL DEFAULT 1,
    outcome_status  TEXT DEFAULT NULL,
    outcome_json    TEXT DEFAULT NULL,
    reversible_note TEXT DEFAULT NULL,
    actor           TEXT NOT NULL DEFAULT 'operator',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_appaudit_ref     ON approval_audit_log(ref);
CREATE INDEX IF NOT EXISTS idx_appaudit_action  ON approval_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_appaudit_created ON approval_audit_log(created_at);

CREATE TABLE IF NOT EXISTS approval_ping_state (
    scope           TEXT PRIMARY KEY DEFAULT 'global',
    last_seen_count INTEGER NOT NULL DEFAULT -1,
    snooze_until    TEXT DEFAULT NULL,
    last_pinged_at  TEXT DEFAULT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_d4_tables(conn: sqlite3.Connection) -> bool:
    """Create the three D4 tables (+ indexes) if absent. Idempotent (CREATE ...
    IF NOT EXISTS only). Stamps an ADDITIVE metadata marker (never touches the
    'schema_version' string row). Returns True on success, False on any error
    (the surface degrades — a missing D4 substrate simply means no comments /
    audit / ping state, never a raise)."""
    try:
        conn.executescript(D4_DDL)
        if _table_exists(conn, "metadata"):
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value, updated_at) "
                "VALUES ('d4_approvals_interface_migration', '1', datetime('now'))"
            )
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def d4_migration_applied(conn: sqlite3.Connection) -> bool:
    """True once the D4 substrate exists. Read-side guard mirroring the belief /
    governance migration_applied convention."""
    return (_table_exists(conn, "approval_comments")
            and _table_exists(conn, "approval_audit_log")
            and _table_exists(conn, "approval_ping_state"))


# --- comment-as-high-trust-training-signal ---------------------------------


def record_comment(ref: str, comment: str, decision: str, *,
                   item_title: Optional[str] = None,
                   item_ids: Optional[Sequence[int]] = None,
                   author: Optional[str] = None,
                   db: Optional[str] = None) -> Dict[str, Any]:
    """Record Drew's free-text comment on an approval as a HIGH-TRUST signal.

    The comment is human-authored, so per L5-I-alpha it floors at
    COMMENT_TRUST_FLOOR (0.85) — it OUT-RANKS any auto-derived signal feeding
    self-eval / belief. The row LINKS the comment to (surface, ref, title/ids)
    so a consumer can attribute it. `consumed=0` marks it a fresh signal the
    self-eval / belief ingestion can later claim (set consumed=1). NEVER raises.

    `decision` is one of 'approved' | 'rejected' | 'snoozed' | 'noted'.
    Returns {status, comment_id} on success, {status:'error'} on failure.
    """
    if author is None:
        author = _default_actor()
    if not comment or not str(comment).strip():
        return _err("empty comment; nothing recorded")
    db_path = resolve_db(db)
    surface, _raw = _parse_ref(ref)
    dec = (decision or "noted").strip().lower()
    if dec not in ("approved", "rejected", "snoozed", "noted"):
        dec = "noted"
    try:
        conn = _open_rw(db_path)
        try:
            if not d4_migration_applied(conn) and not ensure_d4_tables(conn):
                return _err("D4 substrate unavailable; comment not recorded")
            ids_json = (json.dumps([int(x) for x in item_ids])
                        if item_ids else None)
            cur = conn.execute(
                "INSERT INTO approval_comments "
                "(surface, ref, item_title, item_ids, decision, comment, "
                " author, trust_floor, consumed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (surface or "", ref, item_title, ids_json, dec,
                 str(comment).strip(), author, COMMENT_TRUST_FLOOR),
            )
            conn.commit()
            return {"status": "ok", "action": "comment_recorded",
                    "comment_id": cur.lastrowid, "surface": surface, "ref": ref,
                    "decision": dec, "trust_floor": COMMENT_TRUST_FLOOR,
                    "high_trust": True}
        finally:
            conn.close()
    except Exception as e:
        return _err(f"record_comment failed: {e}")


def pending_comment_signals(db: Optional[str] = None,
                            limit: int = 50) -> List[Dict[str, Any]]:
    """The fresh (unconsumed) high-trust comment signals, newest first. This is
    the surface self-eval / belief read to ingest Drew's commentary as a
    top-trust training signal. Read-only; [] on any failure; NEVER raises."""
    db_path = resolve_db(db)
    try:
        if not Path(db_path).exists():
            return []
        conn = _open_ro(db_path)
        try:
            if not d4_migration_applied(conn):
                return []
            rows = conn.execute(
                "SELECT id, surface, ref, item_title, item_ids, decision, "
                "       comment, author, trust_floor, training_memory_id, "
                "       created_at "
                "FROM approval_comments WHERE consumed = 0 "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r[0], "surface": r[1], "ref": r[2], "item_title": r[3],
            "item_ids": _safe_json_list(r[4]) if r[4] else None,
            "decision": r[5], "comment": r[6], "author": r[7],
            "trust_floor": r[8], "training_memory_id": r[9],
            "created_at": r[10], "high_trust": True,
        })
    return out


# --- enact audit-log -------------------------------------------------------


def write_audit(ref: str, action: str, *, confirmed_via: str,
                decision_detail: Optional[Dict[str, Any]] = None,
                comment_id: Optional[int] = None,
                ref_valid: bool = True,
                outcome_status: Optional[str] = None,
                outcome: Optional[Dict[str, Any]] = None,
                actor: Optional[str] = None,
                db: Optional[str] = None) -> Dict[str, Any]:
    """Append one audit row for an ENACT (approve|reject). Records who/what/when/
    decision, the structural-validation verdict, and the reversibility note
    (belief enact is reversible via valid_until). NEVER raises — an audit-write
    failure must never block the operator (it is logged-best-effort)."""
    db_path = resolve_db(db)
    surface, _raw = _parse_ref(ref)
    act = (action or "").strip().lower()
    if act not in ("approve", "reject"):
        act = "approve"
    note = _reversibility_note(surface, act)
    try:
        conn = _open_rw(db_path)
        try:
            if not d4_migration_applied(conn) and not ensure_d4_tables(conn):
                return _err("D4 substrate unavailable; audit not written")
            cur = conn.execute(
                "INSERT INTO approval_audit_log "
                "(surface, ref, action, decision_detail, comment_id, "
                " confirmed_via, ref_valid, outcome_status, outcome_json, "
                " reversible_note, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (surface or "", ref, act,
                 json.dumps(decision_detail, default=str) if decision_detail else None,
                 comment_id, confirmed_via, 1 if ref_valid else 0,
                 outcome_status,
                 json.dumps(outcome, default=str) if outcome is not None else None,
                 note, actor or _default_actor()),
            )
            conn.commit()
            return {"status": "ok", "action": "audit_written",
                    "audit_id": cur.lastrowid, "reversible_note": note}
        finally:
            conn.close()
    except Exception as e:
        return _err(f"write_audit failed: {e}")


def _reversibility_note(surface: str, action: str) -> str:
    """The safety-net note recorded with each enact."""
    if action == "reject":
        return "reject records a decision; it never enacts (no mutation to reverse)."
    if surface == SURFACE_BELIEF:
        return ("belief enact is REVERSIBLE: the engine sets valid_until on the "
                "edge; re-open via belief_engine to restore.")
    if surface == SURFACE_GOVERNANCE:
        return ("governance approve flips a HOLD row to 'approved' (soft status); "
                "the held write's re-attempt is the writer's; reversible by "
                "re-holding.")
    if surface == SURFACE_SELF_EVAL:
        return ("self-eval enact applies the approved proposal(s); review the "
                "self_eval_enacted_proposals row and the piece change to revert.")
    return "enact recorded; consult the surface for its reversal path."


def recent_audit(db: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """The most recent audit rows, newest first. Read-only; [] on failure."""
    db_path = resolve_db(db)
    try:
        if not Path(db_path).exists():
            return []
        conn = _open_ro(db_path)
        try:
            if not d4_migration_applied(conn):
                return []
            rows = conn.execute(
                "SELECT id, surface, ref, action, confirmed_via, ref_valid, "
                "       outcome_status, comment_id, reversible_note, actor, "
                "       created_at "
                "FROM approval_audit_log ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [
        {"id": r[0], "surface": r[1], "ref": r[2], "action": r[3],
         "confirmed_via": r[4], "ref_valid": bool(r[5]), "outcome_status": r[6],
         "comment_id": r[7], "reversible_note": r[8], "actor": r[9],
         "created_at": r[10]}
        for r in rows
    ]


# --- calm ping-on-count-change + snooze ------------------------------------


def ping_state(db: Optional[str] = None,
               scope: str = "global") -> Dict[str, Any]:
    """Read the ping state (read-only). Returns the stored last_seen_count,
    snooze_until, last_pinged_at, updated_at — or sentinel defaults if the
    row/substrate is absent. NEVER raises.

    `updated_at` IS LOAD-BEARING, NOT DECORATION. R13's bound is "a snooze never
    hides a unit that ARRIVED AFTER IT", so the instant the snooze was SET is the
    fact the whole criterion turns on -- and this dict did not carry it. The R3
    read-only witness printed `set_at=None` on every run, including its green
    ones, while attention_signal was reading the column correctly straight from
    the table. A diagnostic that misreports the value the criterion turns on is
    worse than no diagnostic: it teaches the reader the wrong world.
    """
    db_path = resolve_db(db)
    out = {"scope": scope, "last_seen_count": -1, "snooze_until": None,
           "last_pinged_at": None, "updated_at": None}
    try:
        if not Path(db_path).exists():
            return out
        conn = _open_ro(db_path)
        try:
            if not d4_migration_applied(conn):
                return out
            row = conn.execute(
                "SELECT last_seen_count, snooze_until, last_pinged_at, updated_at "
                "FROM approval_ping_state WHERE scope = ?", (scope,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return out
    if row is not None:
        out["last_seen_count"] = int(row[0])
        out["snooze_until"] = row[1]
        out["last_pinged_at"] = row[2]
        out["updated_at"] = row[3]
    return out


def should_ping(db: Optional[str] = None, scope: str = "global",
                *, now: Optional[datetime] = None,
                persist: bool = True) -> Dict[str, Any]:
    """The calm-ping decision: ping ONLY when the pending COUNT CHANGES vs the
    last seen count, AND not while snoozed.

    Logic:
      * compute the current unified count (by-batch, the banner number);
      * if snooze_until is in the future -> DO NOT ping (but still refresh the
        stored count so the next post-snooze read compares against reality);
      * else ping IFF current_count != last_seen_count AND current_count > 0.

    When `persist` is True (default), the new count + last_pinged_at are written
    back so the *next* call compares against this read. When False, the decision
    is computed without mutating (used by tests / dry reads). NEVER raises.
    """
    db_path = resolve_db(db)
    now = now or datetime.now()

    # R12 — THE SIGNAL IS A SET DIFFERENCE OVER OPEN UNIT IDS, NEVER A DELTA OVER
    # THEIR CARDINALITY. `changed = (total != last)` is invariant under a swap:
    # close one unit and open another between two reads and the count is
    # identical while the world changed. That lesson is already in the brain at
    # #13744 and this function re-committed it.
    #
    # R3b — AND `persist` IS NOW A NO-OP FOR THE DECISION. Wiring the old body
    # with persist=True would have written last_seen_count=1 on the first call
    # WHILE SNOOZED; on snooze expiry `changed` would be False and the ping would
    # never fire. Correctly wiring the old function consumed the pending signal.
    # Persistence is now an explicit per-unit verb (decision_units.acknowledge);
    # the parameter is kept only so existing callers keep working.
    if _DU is not None:
        try:
            sig = _DU.attention_signal(db_path, now=now)
            st = ping_state(db_path, scope)
            # R5/C5. `sig.fire` is False on an UNTRUSTED read, and "do not ping"
            # is a decision. The attention signal carries its own refusal; this
            # frame read `.fire`, `.count` and five key lists off it and not the
            # one field that says whether any of them were measured.
            # R7/C2 -- THE DECIDE LAYER, AND THE WORST OF THE CLASS BECAUSE
            # `fire` WAS TRUE. Measured 2026-09-04 on a brain whose
            # `governance_proposals` table had been renamed away: the attention
            # model reported count=1, breakdown {self_eval: 1}, FIRE=TRUE --
            # something needs Drew now -- and this frame threw the fire, the
            # count AND the breakdown away because an EMPTY governance table
            # went dark. A source that holds nothing going absent silenced the
            # notification for a real pending self-eval item.
            _un = tuple(str(x) for x in (getattr(sig, "unread", ()) or ()))
            _bd = dict(getattr(sig, "breakdown", {}) or {})
            if _un and not _all_sources_unread(_un, _bd):
                # PARTIAL: the fire is real and the sources that WERE read are
                # correct. The count is published as a LOWER BOUND, named as
                # one, and the decision is made -- never withheld.
                return {"status": "partial",
                        "ping": bool(sig.fire),
                        "count": int(sig.count),
                        "breakdown": _bd,
                        "unreadable_sources": list(_un),
                        "unknown": True, "count_is_not_zero": True,
                        "persisted": False,
                        "open_unit_keys": list(sig.open_keys),
                        "unseen_unit_keys": list(sig.unseen_keys),
                        "message": ("partial: " + ", ".join(_un) + " UNREAD; "
                                    "the count is a LOWER BOUND and the fire "
                                    "is real"),
                        "reason": getattr(sig, "reason", None)}
            if not bool(getattr(sig, "trusted", None) is True):
                return {"status": "refused",
                        "error": "ping_count_unknown",
                        "ping": None, "count": None, "breakdown": None,
                        "unknown": True, "count_is_not_zero": True,
                        "unreadable_sources": list(_un),
                        "persisted": False,
                        "message": ("I could not tell -- the attention signal "
                                    "could not confirm it read "
                                    + (", ".join(_un) if _un
                                       else "the pending sources")
                                    + ". No ping decision was made."),
                        "reason": getattr(sig, "reason", None)}
            return {
                "status": "ok",
                "ping": sig.fire,
                "count": sig.count,
                "breakdown": dict(sig.breakdown),
                "open_unit_keys": list(sig.open_keys),
                "unseen_unit_keys": list(sig.unseen_keys),
                "quiet_unit_keys": list(sig.quiet_keys),
                "fired_unit_keys": list(sig.fired_keys),
                "last_seen_count": int(st.get("last_seen_count", -1)),
                "changed": bool(sig.unseen_keys),
                "snoozed": bool(sig.quiet_keys),
                "snooze_until": st.get("snooze_until"),
                "persisted": False,
                "reason": sig.reason,
            }
        except (sqlite3.Error, OSError, KeyError, ValueError, TypeError) as e:
            print(f"[voidloom_approvals] should_ping degraded to the legacy "
                  f"count comparison: {type(e).__name__}: {e}", file=sys.stderr)

    _uc = unified_count_ex(db_path)
    total, breakdown, _tr, _why = _uc
    # R7/C2. The channel rides on the object, so the legacy ping path can tell a
    # partial from a total loss exactly as the ledger path above does.
    _un_legacy = tuple(getattr(_uc, "unread", ()) or ())
    st = ping_state(db_path, scope)
    last = int(st.get("last_seen_count", -1))
    snooze_until = st.get("snooze_until")
    snoozed = _is_future(snooze_until, now)

    # R5/C5 (PERSIST layer). THIS FUNCTION BOTH DECIDED AND WROTE on the value.
    # With an untrusted count it computed `changed = (0 != last)` -> True,
    # `ping = (changed and 0 > 0)` -> False -- a silent decision not to tell Drew
    # anything -- and then persisted `last_seen_count = 0`, so the NEXT read,
    # against a healthy brain, would compare against a number nobody measured.
    # Same shape as `hooks/voidloom_notify.py`: a durable write on an unread
    # zero. It refuses to decide and refuses to write.
    # R7/C2 -- THE SAME RULING ON THE LEGACY PATH, WHICH IS THE PATH DREW'S
    # MACHINE EXECUTES. `decision_units` is absent from the installed tree, so
    # `_DU is None` and control reaches here on 100% of live sessions. A fix
    # applied only to the ledger branch above would have been a no-op on the
    # only brain that exists -- the exact shape Council #6 measured when a
    # leaf-only tree scored 5/5 on a fixture and refused on 100% of live ones.
    if _un_legacy and not _all_sources_unread(_un_legacy, breakdown):
        _ping_partial = bool(total > 0 and total != last and not snoozed)
        if persist and _ping_partial:
            # A LOWER BOUND IS NOT WRITTEN INTO DURABLE STATE. `last_seen_count`
            # debounces future pings; storing a number that is short by an
            # unread source would suppress the ping that arrives when the source
            # comes back. The DECISION is made and published; only the WRITE is
            # withheld, and that is said rather than left to be inferred.
            pass
        return {"status": "partial",
                "ping": _ping_partial,
                "count": total,
                "breakdown": breakdown,
                "unreadable_sources": list(_un_legacy),
                "unknown": True,
                "count_is_not_zero": True,
                "last_seen_count": last,
                "snoozed": snoozed,
                "snooze_until": snooze_until,
                "persisted": False,
                "message": ("partial: " + ", ".join(_un_legacy) + " UNREAD; "
                            "the count is a LOWER BOUND, the ping decision IS "
                            "made, and last_seen_count was NOT overwritten "
                            "with a number short by an unread source."),
                "reason": _why}
    if not _tr:
        return {"status": "refused",
                "error": "ping_count_unknown",
                "ping": None,
                "count": None,
                "breakdown": None,
                "unknown": True,
                "count_is_not_zero": True,
                "unreadable_sources": list(_un_legacy),
                "last_seen_count": last,
                "snoozed": snoozed,
                "snooze_until": snooze_until,
                "persisted": False,
                "message": ("I could not tell -- the pending count is UNKNOWN, "
                            "not zero. No ping decision was made and "
                            "last_seen_count was NOT overwritten."),
                "reason": _why}

    changed = (total != last)
    ping = bool(changed and total > 0 and not snoozed)

    if persist:
        _persist_ping(db_path, scope, total, pinged=ping, now=now)

    return {
        "status": "ok",
        "ping": ping,
        "count": total,
        "breakdown": breakdown,
        "last_seen_count": last,
        "changed": changed,
        "snoozed": snoozed,
        "snooze_until": snooze_until,
        "reason": ("snoozed" if snoozed else
                   ("count_changed" if (changed and total > 0) else
                    ("count_unchanged" if not changed else "empty"))),
    }


def set_snooze(hours: float, db: Optional[str] = None, scope: str = "global",
               *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Suppress the ping for `hours` from now (calm, not nagging). hours<=0
    CLEARS the snooze. Persists snooze_until without disturbing last_seen_count.
    NEVER raises."""
    db_path = resolve_db(db)
    now = now or datetime.now()
    until = None if hours is None or hours <= 0 else (now + timedelta(hours=float(hours))).isoformat()
    try:
        conn = _open_rw(db_path)
        try:
            if not d4_migration_applied(conn) and not ensure_d4_tables(conn):
                return _err("D4 substrate unavailable; snooze not set")
            conn.execute(
                "INSERT INTO approval_ping_state (scope, snooze_until, updated_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(scope) DO UPDATE SET "
                "  snooze_until = excluded.snooze_until, "
                "  updated_at = datetime('now')",
                (scope, until),
            )
            conn.commit()
            return {"status": "ok", "action": "snooze_set" if until else "snooze_cleared",
                    "snooze_until": until, "scope": scope}
        finally:
            conn.close()
    except Exception as e:
        return _err(f"set_snooze failed: {e}")


def _persist_ping(db_path: str, scope: str, count: int, *, pinged: bool,
                  now: datetime) -> None:
    """Best-effort write-back of last_seen_count (+ last_pinged_at when pinged).
    Preserves snooze_until via UPSERT. Swallows all errors (ping must never
    break a session)."""
    try:
        conn = _open_rw(db_path)
        try:
            if not d4_migration_applied(conn) and not ensure_d4_tables(conn):
                return
            if pinged:
                conn.execute(
                    "INSERT INTO approval_ping_state "
                    "(scope, last_seen_count, last_pinged_at, updated_at) "
                    "VALUES (?, ?, ?, datetime('now')) "
                    "ON CONFLICT(scope) DO UPDATE SET "
                    "  last_seen_count = excluded.last_seen_count, "
                    "  last_pinged_at = excluded.last_pinged_at, "
                    "  updated_at = datetime('now')",
                    (scope, int(count), now.isoformat()),
                )
            else:
                conn.execute(
                    "INSERT INTO approval_ping_state "
                    "(scope, last_seen_count, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(scope) DO UPDATE SET "
                    "  last_seen_count = excluded.last_seen_count, "
                    "  updated_at = datetime('now')",
                    (scope, int(count)),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return


def _is_future(iso: Optional[str], now: datetime) -> bool:
    """True if `iso` parses to an instant strictly after `now`. Bad/empty -> False."""
    if not iso:
        return False
    try:
        return datetime.fromisoformat(iso) > now
    except (TypeError, ValueError):
        return False


# --- the canonical Face-3 structured record --------------------------------


def structured_record(db: Optional[str] = None) -> List[Dict[str, Any]]:
    """Face 3 (canonical): the structured per-proposal record the other two
    faces render. One entry per review ITEM (by-batch, matching the banner),
    each with: {id, ref, title, source, summary, preview, trust_tier,
    created_at, count, elevated, commands}. `trust_tier` is the derived trust
    (L5-I-alpha) of the underlying memory where one is identifiable, else a
    label. Read-only; built from list_pending; NEVER raises."""
    db_path = resolve_db(db)
    items = list_pending(db_path)
    out: List[Dict[str, Any]] = []
    # One read-only connection for trust lookups across all items.
    conn = None
    try:
        if Path(db_path).exists():
            conn = _open_ro(db_path)
    except Exception:
        conn = None
    try:
        for it in items:
            surface = it.get("surface")
            ref = f"{surface}:{it.get('ref')}"
            summary, preview, mem_id = _record_summary_preview(it)
            trust = _record_trust(conn, surface, it, mem_id)
            out.append({
                "id": it.get("ref"),
                "ref": ref,
                "title": it.get("title", ""),
                "source": surface,
                "summary": summary,
                "preview": preview,
                "trust_tier": trust,
                "created_at": it.get("created_at"),
                "count": it.get("count", 0),
                "elevated": bool(it.get("elevated")),
                "commands": {
                    "show": it.get("detail_cmd"),
                    "approve": it.get("approve_cmd"),
                    "reject": it.get("reject_cmd"),
                },
            })
            # G1 EXPOSES PER-UNIT TITLES. structured_record is what all three
            # faces consume, and it carried no machine-readable proposal titles
            # at all -- only an "<proposal title>" placeholder inside a command
            # string. No face could construct a valid approve call from it,
            # which is D9 stated as a schema fact.
            #
            # This is the G1 half only: it PUBLISHES the units. Rewiring the
            # TUI's _enact closure (voidloom/cli.py:917) to consume them is G4
            # and is deliberately NOT done here. NOTE FOR THAT GATE: that closure
            # also passes confirm=True unconditionally, so threading titles in
            # without removing the hardcoded confirm would turn the TUI into a
            # permanent bypass of the L5-I-beta variance gate. The two edits must
            # land together.
            #
            # R2 -- THE KEY IS PRESENT ONLY WHEN THE UNITS WERE ACTUALLY
            # ENUMERATED. `units: []` asserts "this report has no decidable
            # units"; None means "this face could not enumerate them", and those
            # are different facts a consumer must be able to tell apart. The live
            # record carries no `units` key at all, so omitting it is also the
            # historical shape rather than a third state invented here.
            _units = _units_for_item(it, db_path)
            if _units is not None:
                out[-1]["units"] = _units
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _units_for_item(it: Dict[str, Any],
                    db_path: str) -> Optional[List[Dict[str, Any]]]:
    """The decidable units inside one review item, or None if NOT ENUMERABLE.

    For self-eval every open proposal becomes an entry; `discarded` counts the
    times a decision on it was made and dropped, so a face can show "you already
    decided this and the tool ate it" instead of presenting it as new.

    RETURN CONTRACT (R2):
      * ``[...]`` / ``[]`` -- the ledger was consulted. ``[]`` means this report
        genuinely holds no open units.
      * ``None``           -- the units could not be enumerated at all. The
        caller OMITS the key rather than publishing an empty list, because an
        empty list is a claim about the report and None is a claim about us.

    THE SECOND ABSENT PATH, FOUND BY MEASUREMENT AND NOT ON ANY TARGET LIST.
    Returning `[]` when `_DU is None` was the named defect. But measured
    2026-09-03 on the live brain WITH `decision_units` importable, report #7
    (5 proposals) still yielded `units=[]`: migration 030 is not applied, so
    `open_self_eval_report_ids` falls back to the legacy report set and
    `decision_units` holds no unit rows for it. Fixing only the `_DU is None`
    branch would have left G1 just as undone on the tree that actually ships,
    and the gate would not have said so. Both paths are handled here.
    """
    surface = it.get("surface")
    if surface in (SURFACE_BELIEF, SURFACE_GOVERNANCE):
        # D9 HAS THREE MEMBERS, AND 2026-09-08 FIXED ONE. The face was taught to
        # pass `titles` for self-eval, and the belief batch then failed with the
        # SAME shape of refusal -- "belief approve requires >=1 --ids" -- because
        # these two surfaces are selected by ID, not by title, and this function
        # returned None for both. "Units are a self-eval concept" was true of the
        # decision_units TABLE and false of the QUESTION the faces ask, which is
        # "what may I name in an approve call?". Every surface can answer that.
        #
        # Each unit declares `select_by`, so a face never has to know a per-
        # surface rule: it reads the unit and passes what the unit says.
        # ALWAYS re-prefix. `it["ref"]` is the RAW id -- an int for self-eval,
        # a batch string for belief -- never the "surface:raw" form show() parses.
        # Passing the bare string through because it "looked like a ref" is the
        # same mistake as reading a passed value as the required value.
        _showed = show(f"{surface}:{it.get('ref')}", db=db_path)
        if not isinstance(_showed, dict) or _showed.get("status") != "ok":
            print(f"[voidloom_approvals] _units_for_item: {surface} proposals "
                  f"could not be enumerated "
                  f"({(_showed or {}).get('message')!r}); the `units` key is "
                  f"OMITTED rather than published empty", file=sys.stderr)
            return None
        out_units: List[Dict[str, Any]] = []
        for _p in (_showed.get("proposals") or []):
            _pid = _p.get("id")
            if _pid is None:
                continue
            if surface == SURFACE_BELIEF:
                _lbl = (f"{_p.get('proposal_kind') or _p.get('predicate') or 'edge'} "
                        f"{_p.get('source_id')}->{_p.get('target_id')}")
                _kind = _p.get("nli_label") or _p.get("predicate") or ""
            else:
                _lbl = (_p.get("reason") or _p.get("reason_code")
                        or f"governance hold {_pid}")
                _kind = _p.get("writer_kind") or ""
            out_units.append({"id": _pid, "title": _lbl, "kind": _kind,
                              "select_by": "id", "elevated": _p.get("elevated")})
        return out_units
    if surface != SURFACE_SELF_EVAL:
        # An unknown surface. Saying "[]" would be a claim about it that this
        # function is not entitled to make.
        return None
    if _DU is None:
        print(f"[voidloom_approvals] _units_for_item: decision_units unavailable "
              f"({_DU_IMPORT_ERROR}); the per-unit titles cannot be enumerated, "
              f"so the `units` key is OMITTED rather than published empty",
              file=sys.stderr)
        return None
    try:
        rid = int(it.get("ref"))
    except (TypeError, ValueError):
        return None
    try:
        _rs = _DU.open_self_eval_report_ids(db_path)
        if not bool(getattr(_rs, "trusted", None) is True):
            # R5/C5. The `source == "legacy"` test below happens to cover most
            # of this, but "the ledger is un-migrated" and "the ledger could not
            # tell me anything" are different facts with the same remedy only by
            # coincidence. Tested explicitly so the coincidence cannot lapse.
            print(f"[voidloom_approvals] _units_for_item: the open-report set "
                  f"is UNKNOWN for report #{rid}; the `units` key is OMITTED "
                  f"rather than published empty", file=sys.stderr)
            return None
        if getattr(_rs, "source", None) == "legacy":
            print(f"[voidloom_approvals] _units_for_item: migration 030 is not "
                  f"applied, so the ledger holds no unit rows for report #{rid}; "
                  f"the `units` key is OMITTED rather than published empty",
                  file=sys.stderr)
            return None
    except (sqlite3.Error, OSError, ValueError, TypeError) as e:
        print(f"[voidloom_approvals] _units_for_item: could not establish whether "
              f"the ledger is migrated for report #{rid}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return None
    try:
        units = [u for u in _DU.open_units(db_path) if u.report_id == rid]
    except (sqlite3.Error, OSError, ValueError, TypeError) as e:
        print(f"[voidloom_approvals] _units_for_item degraded for report #{rid}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return None
    return [{
        "unit_key": u.key,
        "title": u.title,
        # Self-eval is selected BY TITLE. Stated on the unit rather than left as
        # a rule a face has to know, so a face reads the unit and passes what
        # the unit says -- which is what stops the belief surface failing the
        # way this one did.
        "select_by": "title",
        "kind": u.kind,
        "piece_affected": u.piece_affected,
        "discarded_count": u.discarded_count,
        "approve_cmd": (f'voidloom approvals --approve self-eval:{rid} '
                        f'--title "{u.title}"'),
        "reject_cmd": (f'voidloom approvals --reject self-eval:{rid} '
                       f'--title "{u.title}" --reason "..."'),
    } for u in units]


def _record_summary_preview(it: Dict[str, Any]) -> Tuple[str, str, Optional[int]]:
    """Build a calm one-line summary + a short multi-line preview/diff for a
    review item, plus the underlying memory id if one is identifiable."""
    surface = it.get("surface")
    cnt = it.get("count", 0)
    if surface == SURFACE_SELF_EVAL:
        props = it.get("proposals", []) or []
        titles = [p.get("title", "") for p in props if p.get("title")]
        summary = (f"{cnt} proposal" + ("" if cnt == 1 else "s")
                   + (": " + "; ".join(titles[:3]) if titles else ""))
        preview = "\n".join(
            f"  - [{p.get('kind', '?')}] {p.get('title', '')}"
            + (f"  (priority={p.get('priority')})" if p.get("priority") else "")
            for p in props[:8]
        ) or "  (no sub-proposals)"
        return summary, preview, None
    if surface == SURFACE_BELIEF:
        elv = it.get("elevated_count", 0)
        summary = (f"belief batch: {cnt} pending edge proposal"
                   + ("" if cnt == 1 else "s")
                   + (f" ({elv} foundation-touching)" if elv else ""))
        preview = (f"  batch {it.get('ref')} — review individual edge ids via "
                   f"`{it.get('detail_cmd', '')}`")
        return summary, preview, None
    if surface == SURFACE_GOVERNANCE:
        summary = (f"governance HOLD batch: {cnt} held write"
                   + ("" if cnt == 1 else "s"))
        preview = (f"  batch {it.get('ref')} — held by the governance gate; "
                   f"inspect via `{it.get('detail_cmd', '')}`")
        return summary, preview, None
    return (f"{cnt} item" + ("" if cnt == 1 else "s"), "", None)


def _record_trust(conn: Optional[sqlite3.Connection], surface: Optional[str],
                  it: Dict[str, Any], mem_id: Optional[int]) -> Dict[str, Any]:
    """The trust_tier block for a Face-3 record. When the trust organ + a
    concrete underlying memory id are available, attach a live compute_trust
    score; otherwise return a descriptive label. NEVER raises."""
    if surface == SURFACE_GOVERNANCE:
        tm = it.get("target_memory_id") if isinstance(it, dict) else None
        mem_id = mem_id or tm
    if conn is not None and _TRUST is not None and mem_id is not None:
        try:
            score = _TRUST.compute_trust(conn, int(mem_id))
            return {"memory_id": int(mem_id), "score": round(float(score), 4),
                    "source": "compute_trust"}
        except Exception:
            pass
    # No single underlying memory (self-eval reports / belief batches aggregate
    # many) — label the surface's authority rather than fabricate a number.
    label = {
        SURFACE_SELF_EVAL: "report-level (owner-gated in manual mode; policy:auto in auto mode)",
        SURFACE_BELIEF: "batch-level (per-edge endpoint trust governs on approve)",
        SURFACE_GOVERNANCE: "held-write (trust_score carried on the hold row)",
    }.get(surface, "n/a")
    return {"memory_id": None, "score": None, "label": label}


# ---------------------------------------------------------------------------
# Per-surface readers. Each returns a list of review items, [] on ANY failure.
# A review item is a dict with the shared schema:
#   { surface, ref, title, count, elevated, created_at, status,
#     approve_cmd, reject_cmd, detail_cmd }
#   ref      : the id (self-eval report id, int) or report_batch (belief, str).
#   count    : number of sub-proposals folded into this one item.
#   elevated : True if this item touches a foundation memory (belief) or carries
#              a high-priority proposal (self-eval). Surfaced first.
# ---------------------------------------------------------------------------


def _self_eval_items(db_path: str) -> List[Dict[str, Any]]:
    """Self-eval reports that still hold an undecided proposal, as review items.

    R10/R11's SUBJECT IS THE PENDING SET, NOT ONLY ITS CARDINALITY. Iteration 2
    unified the COUNT and left this SELECTION on `status='pending'` -- the very
    column the ledger replaced. Measured on a migrated throwaway 2026-09-02:
    unified_open_count()==3 while this returned 1, because reports #3 and #5 are
    stored 'enacted' while holding four undecided units between them. That is
    verbatim "the banner said 3 and approvals said 1", the sentence that convened
    the council, and it is exactly the four decisions this round exists to
    recover: report #5's three August-eaten titles and report #3's never-decided
    deprecation were counted and UNLISTABLE, so no face could offer them.

    A report is listed when the stored column says pending OR the ledger says it
    still holds an open unit. `count` and `proposals` then describe the UNDECIDED
    ones -- the same shape the belief surface already uses (its `count` is
    `pending`, never the batch total) -- so Drew is never shown "5 proposals" for
    a report where four are already decided.

    R21 -- THE SELECTION IS NOW DELEGATED, NOT SPELLED HERE. This function used
    to carry its own `WHERE status='pending' OR EXISTS(...open unit...)`, and
    `unified_open_count` carried a different rule, so the LIST and the COUNT were
    free to name different reports while agreeing on a number. Measured
    2026-09-02 on one database: count 3, list 4, and the two disagreed about
    report #8. The report id set now comes from
    `decision_units.open_self_eval_report_ids()` -- the same call the count's
    length comes from -- so a swap is impossible, not merely unlikely.
    """
    items: List[Dict[str, Any]] = []
    open_by_report: Optional[Dict[int, List[Any]]] = None
    known_to_ledger: Set[int] = set()
    selected_ids: Optional[List[int]] = None
    if _DU is not None:
        try:
            selected_ids = list(_DU.open_self_eval_report_ids(db_path).ids)
        except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError) as e:
            print(f"[voidloom_approvals] _self_eval_items: the unified report set "
                  f"was unreadable, falling back to the stored status column: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            selected_ids = None
        try:
            open_by_report = {}
            for u in _DU.open_units(db_path):
                if u.surface == SURFACE_SELF_EVAL:
                    open_by_report.setdefault(int(u.report_id), []).append(u)
        except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError) as e:
            # RECORDED, never swallowed (R17). A zero from a broken read and a
            # zero from an empty world are different facts, and this path is a
            # count/selection path -- the class the criterion names.
            print(f"[voidloom_approvals] _self_eval_items: the decision ledger "
                  f"was unreadable, falling back to the stored status column "
                  f"(reports holding undecided units may be hidden): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            open_by_report = None
    try:
        if not Path(db_path).exists():
            # R5/C5 (LIST layer). `[]` is a claim about the brain -- "nothing
            # awaits review" -- and there is no brain here to make it about.
            print(f"[voidloom_approvals] _self_eval_items: no brain at "
                  f"{db_path}; this empty list is UNKNOWN, not 'nothing pending'",
                  file=sys.stderr)
            return []
        conn = _open_ro(db_path)
        try:
            if not _table_exists(conn, "self_eval_reports"):
                print(f"[voidloom_approvals] _self_eval_items: "
                      f"self_eval_reports is ABSENT in {db_path} -- the "
                      f"self-eval machinery was never installed into this "
                      f"brain, which is what `claude_memory.py init` produces. "
                      f"This empty list is UNKNOWN, not 'nothing pending'",
                      file=sys.stderr)
                return []
            if _relation_exists(conn, "v_decision_unit_state") and open_by_report is not None:
                known_to_ledger = {int(r[0]) for r in conn.execute(
                    "SELECT DISTINCT report_id FROM v_decision_unit_state "
                    " WHERE report_id IS NOT NULL")}
            if selected_ids is not None:
                # R21. The rows are the SET, fetched BY ID. `status` is still
                # projected because the item carries it for display, but it is
                # no longer a SELECTION predicate: nothing here decides which
                # reports Drew is offered by reading the run-record column.
                if not selected_ids:
                    return []
                rows = conn.execute(
                    "SELECT id, month, created_at, status, "
                    "       proposed_enhancements, proposed_deprecations "
                    "FROM self_eval_reports WHERE id IN (%s) "
                    "ORDER BY created_at DESC, id DESC"
                    % ",".join("?" * len(selected_ids)), selected_ids
                ).fetchall()
            else:
                # decision_units unreachable: the pre-ledger contract, unchanged,
                # and the degradation was already printed above.
                rows = conn.execute(
                    "SELECT id, month, created_at, status, "
                    "       proposed_enhancements, proposed_deprecations "
                    "FROM self_eval_reports "
                    "WHERE status='pending' "
                    "ORDER BY created_at DESC, id DESC"
                ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    for rid, month, created_at, status, enh_json, dep_json in rows:
        enh = _safe_json_list(enh_json)
        dep = _safe_json_list(dep_json)
        if open_by_report is not None and int(rid) in known_to_ledger:
            # Narrow to the UNDECIDED proposals. A report listed because the
            # ledger says it holds one open unit must not then advertise the
            # four already decided as if they were awaiting Drew.
            #
            # R15's symmetry: a report the ledger KNOWS and finds nothing open
            # in contributes 0 to the count, so it must contribute 0 items to
            # the list -- otherwise the list and the count disagree again, in
            # the other direction. `known_to_ledger` is the guard that keeps
            # this from hiding a stored-'pending' report the migration has
            # never seen: about such a report the ledger has nothing to say,
            # so the stored column still rules.
            _open_titles = {u.title for u in open_by_report.get(int(rid), [])}
            if not _open_titles:
                if selected_ids is not None:
                    # R21. NEVER drop a report the SET named. Dropping here is
                    # how the list and the count come apart again: the set was
                    # read on one connection and the units on another, so this
                    # can only mean they saw different instants. Keep the item,
                    # say so, and let the mismatch be visible instead of silently
                    # shortening the list by one.
                    print(f"[voidloom_approvals] _self_eval_items: report #{rid} "
                          f"is in the unified open set but shows no open unit on "
                          f"a second read; listing it rather than dropping it",
                          file=sys.stderr)
                else:
                    continue
            if _open_titles:
                enh = [p for p in enh
                       if isinstance(p, dict) and p.get("title") in _open_titles]
                dep = [p for p in dep
                       if isinstance(p, dict) and p.get("title") in _open_titles]
        count = len(enh) + len(dep)
        elevated = any(
            (p.get("priority") == "high") for p in enh if isinstance(p, dict)
        )
        title = f"Self-eval {month}" if month else f"Self-eval report #{rid}"
        items.append({
            "surface": SURFACE_SELF_EVAL,
            "ref": rid,
            "title": title,
            "count": count,
            "elevated": bool(elevated),
            "created_at": created_at,
            "status": status,
            # The dispatch commands. Self-eval approve/reject name a TITLE; we
            # cannot guess which titles Drew wants, so we point him at the
            # detail view + the exact approve/reject skeleton.
            "approve_cmd": (
                f'voidloom approvals --approve self-eval:{rid} '
                f'--title "<proposal title>"'
            ),
            "reject_cmd": (
                f'voidloom approvals --reject self-eval:{rid} '
                f'--title "<proposal title>" --reason "..."'
            ),
            "detail_cmd": f"voidloom approvals --show self-eval:{rid}",
            "proposals": [
                {"kind": "enhancement", "title": p.get("title", ""),
                 "priority": p.get("priority"),
                 "piece_affected": p.get("piece_affected")}
                for p in enh if isinstance(p, dict)
            ] + [
                {"kind": "deprecation", "title": p.get("title", ""),
                 "piece_affected": p.get("piece_affected")}
                for p in dep if isinstance(p, dict)
            ],
        })
    return items


def _belief_items(db_path: str) -> List[Dict[str, Any]]:
    """Pending belief edge_proposals BATCHES as review items (1 per batch)."""
    if _BE is None:
        return []
    try:
        if not Path(db_path).exists():
            return []
        conn = _open_ro(db_path)
        try:
            if not _BE.migration_applied(conn):
                return []
            batches = _BE.pending_report_batches(conn)
        finally:
            conn.close()
    except Exception:
        return []

    items: List[Dict[str, Any]] = []
    for b in batches:
        batch = b.get("report_batch")
        pending = int(b.get("pending", 0) or 0)
        elevated_n = int(b.get("elevated", 0) or 0)
        items.append({
            "surface": SURFACE_BELIEF,
            "ref": batch,
            "title": f"Belief batch {batch}",
            "count": pending,
            "elevated": elevated_n > 0,
            "elevated_count": elevated_n,
            "created_at": None,  # batch summary carries no single created_at
            "status": "pending",
            # Belief approve/reject name PROPOSAL IDs; show the batch first.
            "approve_cmd": (
                f"voidloom approvals --approve belief:{batch} "
                f"--ids <proposal_id> [<id> ...]"
            ),
            "reject_cmd": (
                f"voidloom approvals --reject belief:{batch} "
                f'--ids <proposal_id> [<id> ...] --reason "..."'
            ),
            "detail_cmd": f"voidloom approvals --show belief:{batch}",
        })
    return items


def _governance_items(db_path: str) -> List[Dict[str, Any]]:
    """Pending governance HOLD BATCHES as review items (1 per report_batch).

    The governance gate (L5-I-β) holds writes it cannot let through autonomously
    and accrues them, batched one report_batch per writer per day, into
    governance_proposals. We fold each batch into the unified surface exactly
    like a belief batch (count by batch, not proposal). governance.py already
    exposes list_pending_items() in the shared item schema, so this is a thin
    delegating reader. [] on ANY failure (module/table absent, db locked)."""
    if _GOV is None:
        return []
    try:
        if not Path(db_path).exists():
            return []
        items = _GOV.list_pending_items(db_path)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _safe_reader(reader, db_path: str) -> List[Dict[str, Any]]:
    """Run a per-surface reader, isolating any exception to [] for that surface.
    The unified surface must NEVER raise just because one source misbehaves."""
    try:
        out = reader(db_path)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def _safe_json_list(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# Public aggregation API.
# ---------------------------------------------------------------------------


def list_pending(db: Optional[str] = None) -> List[Dict[str, Any]]:
    """The unified, ordered list of pending review items across BOTH surfaces.

    Ordering: elevated items first (so a foundation-touching belief batch or a
    high-priority self-eval surfaces at the top), then self-eval before belief
    (self-eval is the long-standing surface), then by descending recency where a
    timestamp exists. Read-only; fail-graceful per surface; NEVER raises.
    """
    db_path = resolve_db(db)
    # Defence-in-depth: each reader is already internally fail-graceful (returns
    # [] on any failure), but the aggregator ALSO isolates each so a future (or
    # monkeypatched) reader that *raises* cannot break the unified surface. One
    # surface failing -> [] for that surface; the other still contributes.
    se = _safe_reader(_self_eval_items, db_path)
    be = _safe_reader(_belief_items, db_path)
    gov = _safe_reader(_governance_items, db_path)
    items = se + be + gov

    surface_rank = {SURFACE_SELF_EVAL: 0, SURFACE_BELIEF: 1, SURFACE_GOVERNANCE: 2}

    def _key(it: Dict[str, Any]) -> Tuple[int, int, str]:
        elevated_rank = 0 if it.get("elevated") else 1
        srank = surface_rank.get(it.get("surface"), 9)
        # Descending recency: invert the ISO string so larger sorts first.
        created = it.get("created_at") or ""
        return (elevated_rank, srank, _invert_iso(created))

    items.sort(key=_key)
    return items


def unified_count(db: Optional[str] = None) -> Tuple[int, Dict[str, int]]:
    """(total, {self_eval, belief}) — the SAME number the banner shows.

    Counts review items (BATCH/REPORT units), never sub-proposals. Independent
    per surface; fail-graceful. This mirrors voidloom_banner.read_unified_pending
    but is computed from the aggregated item list so the surface and the banner
    can never disagree.
    """
    # R10/R11 — DELEGATED. The old docstring asserted "the surface and the
    # banner can never disagree" while the two functions returned different key
    # SETS; they agreed on the total only because governance_proposals holds 0
    # rows. Both now call the same function and receive the same three keys, so
    # the property is structural instead of asserted.
    # R7/C1. SLICING A TUPLE SUBCLASS RETURNS A PLAIN TUPLE, so the channel
    # died here even though the producer above carries it. Measured on an
    # all-dark brain: this function published `0` while `build_banner`, on the
    # same brain in the same instant, published the bare refusal -- two surfaces
    # of one build disagreeing about one question, which is the defect
    # `conventions-014` says needs no reference tree to see. The historical PAIR
    # is unchanged for every caller that unpacks it; `.unread` rides beside it
    # for every caller that asks.
    _uc = unified_count_ex(db)
    return PartialCount(tuple(_uc)[:2], getattr(_uc, "unread", ()))


class PartialCount(tuple):
    """The historical `unified_count_ex` 4-tuple, PLUS the per-source channel.

    R7/C2. `(total, breakdown, trusted, reason)` says THAT the answer is
    doubtful and cannot say WHICH source is doubtful, so every consumer of it
    was structurally unable to render a partial answer no matter how carefully
    the consumer was written. Widening the tuple would break three positional
    unpacks at a distance; a tuple subclass keeps all of them working and hangs
    the channel off the same object.

    `unread` is a tuple of BREAKDOWN_KEYS names -- the sources that were NOT
    opened. Empty means every source answered. Full means nothing did, and that
    is the only state that earns a bare refusal.
    """

    def __new__(cls, items, unread=()):
        t = super().__new__(cls, tuple(items))
        t.unread = tuple(unread)
        return t


def unified_count_ex(db: Optional[str] = None
                     ) -> Tuple[int, Dict[str, int], bool, Optional[str]]:
    """``(total, breakdown, trusted, reason)`` -- the tuple `unified_count` owed.

    R5/C2. `unified_count` is the function `claude_memory.py approvals` renders
    from, and `.as_tuple()` is where its trust bit died: two of four fields of a
    trust-carrying CountResult, returned as a bare pair. This is the PROPAGATE
    member the AST sweep cannot see -- it has no absence branch of its own; it
    consumes a CORRECT refusal and reads only the value.

    The historical two-tuple survives above (nothing that reads it breaks), and
    every caller that RENDERS or WRITES must use this form.
    """
    if _DU is not None:
        try:
            _cr = _DU.unified_open_count(db)
            _tr = bool(getattr(_cr, "trusted", None) is True)
            # R7/C2 -- NAME THE SOURCE THAT ACTUALLY FAILED. The sentence below
            # used to say "the self-eval source" on EVERY refusal. Measured
            # 2026-09-04 on a brain whose `governance_proposals` table had been
            # renamed away: self-eval WAS read (breakdown self_eval=1) and this
            # module printed that it could not confirm reading it, while
            # `voidloom_banner`, in the same tree at the same instant, rendered
            # `1+? proposal await (1 self-eval - 0 belief - governance UNREAD)`.
            # A hardcoded source name is a guess wearing a measurement's clothes.
            _unread = tuple(str(x) for x in (getattr(_cr, "unread", ()) or ()))
            _why = None
            if not _tr:
                _why = (_partial_reason(_unread)
                        if _unread else
                        ("the decision ledger could not confirm it read the "
                         "pending sources (absent table, unreadable brain, or a "
                         "decision_units.py that predates the trust "
                         "vocabulary); the count is UNKNOWN, not zero"))
                print(f"[voidloom_approvals] unified_count: {_why}",
                      file=sys.stderr)
            total, breakdown = _cr.as_tuple()
            return PartialCount((total, breakdown, _tr, _why), _unread)
        except (sqlite3.Error, OSError, KeyError, ValueError, TypeError) as e:
            print(f"[voidloom_approvals] unified_count degraded: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            _zero = dict.fromkeys(_all_source_keys() or (), 0)
            return PartialCount(
                (0, _zero, False,
                 f"the decision ledger raised {type(e).__name__}"),
                tuple(_zero))
    # R10: NO LOCAL AGGREGATION FALLBACK -- AND THERE STILL IS NONE. It was the
    # third of the five count spellings, and a fallback that computes the number
    # for itself is exactly how a surface comes to disagree with the one function
    # without anything saying so. The un-migrated-brain case is handled INSIDE
    # decision_units.unified_open_count (via _legacy_self_eval_report_ids).
    #
    # R2 -- BUT "REPORTING 0" WAS NOT NEUTRAL. This branch is reached on the live
    # tree, where `decision_units.py` does not exist, and 0 is what a caller
    # renders as an empty inbox: measured 2026-09-03, live `unified_count` = (1,
    # {self_eval:1, belief:0}) and this branch = (0, {0,0,0}), consumed by
    # claude_memory.py's `approvals` verb and by `should_ping`. Saying it on
    # stderr does not undo it -- `emit()` sends the envelope to stdout and the
    # human reads the number. So this now asks voidloom_banner for the historical
    # contract, the SAME one function every other absent-ledger path here uses.
    if _VB is not None:
        try:
            # R5/C2. `read_unified_pending` returns the historical PAIR and
            # drops its own trust bit; `unified_pending_report` is the same read
            # WITH the refusal, and it is what the banner's own surfaces use.
            _rep = _VB.unified_pending_report(db)
            total, breakdown = _rep["total"], _rep["breakdown"]
            _tr = bool(_rep.get("trusted"))
            # R7/C2. The historical contract carries `unreadable_sources`; this
            # branch dropped it, so the `_DU`-absent configuration -- which is
            # the INSTALLED configuration today -- had no partial channel at all.
            _unread = tuple(str(x) for x in (_rep.get("unreadable_sources") or ()))
            print(f"[voidloom_approvals] unified_count: decision_units "
                  f"unavailable ({_DU_IMPORT_ERROR}); answered from the "
                  f"historical contract via voidloom_banner -> {total} "
                  f"{breakdown} trusted={_tr} unread={list(_unread)}",
                  file=sys.stderr)
            return PartialCount((total, breakdown, _tr, _rep.get("reason")),
                                _unread)
        except Exception as e:  # pragma: no cover - defensive
            print(f"[voidloom_approvals] unified_count: decision_units "
                  f"unavailable ({_DU_IMPORT_ERROR}) and the historical "
                  f"fallback raised {type(e).__name__}: {e}", file=sys.stderr)
    print(f"[voidloom_approvals] unified_count: decision_units unavailable "
          f"({_DU_IMPORT_ERROR}) and voidloom_banner unavailable too "
          f"({_VB_IMPORT_ERROR}); the count is UNKNOWN and is reported as 0",
          file=sys.stderr)
    _zero = dict.fromkeys(_all_source_keys() or (), 0)
    return PartialCount(
        (0, _zero, False,
         f"neither decision_units ({_DU_IMPORT_ERROR}) nor voidloom_banner "
         f"({_VB_IMPORT_ERROR}) could be reached; the count is UNKNOWN"),
        tuple(_zero))


def _invert_iso(s: str) -> str:
    """Map an ISO timestamp to a string that sorts DESCENDING under ascending
    sort (newest first). Empty -> sorts last. Non-empty inverts each char."""
    if not s:
        return "￿"  # sorts after any real timestamp -> oldest/unknown last
    return "".join(chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in s)


# ---------------------------------------------------------------------------
# Detail view for a single item (read-only).
# ---------------------------------------------------------------------------


def show(ref: str, db: Optional[str] = None) -> Dict[str, Any]:
    """Show one item's sub-proposals in detail. `ref` is a tagged reference:
    'self-eval:<report_id>' or 'belief:<report_batch>'. Read-only; never raises.
    """
    db_path = resolve_db(db)
    surface, raw = _parse_ref(ref)
    try:
        if surface == SURFACE_SELF_EVAL:
            if _SE is None:
                return {"status": "error",
                        "message": "monthly_self_eval module not available"}
            rpt = _SE.get_report(db_path, report_id=int(raw))
            return {"status": "ok", "surface": surface, **rpt}
        if surface == SURFACE_BELIEF:
            if _BE is None:
                return {"status": "error",
                        "message": "belief_engine module not available"}
            conn = _open_ro(db_path)
            try:
                if not _BE.migration_applied(conn):
                    return {"status": "error",
                            "message": "belief substrate not present"}
                props = _BE.get_proposals(conn, report_batch=raw, status="pending")
            finally:
                conn.close()
            return {"status": "ok", "surface": surface,
                    "report_batch": raw, "proposals": props}
        if surface == SURFACE_GOVERNANCE:
            if _GOV is None:
                return {"status": "error",
                        "message": "governance module not available"}
            conn = _open_ro(db_path)
            try:
                if not _GOV.migration_applied(conn):
                    return {"status": "error",
                            "message": "governance substrate not present"}
                rows = conn.execute(
                    "SELECT id, writer_kind, reason, reason_code, trust_score, "
                    "       elevated, target_memory_id, created_at "
                    "FROM governance_proposals "
                    "WHERE report_batch = ? AND status = 'pending' ORDER BY id",
                    (raw,),
                ).fetchall()
            finally:
                conn.close()
            props = [
                {"id": r[0], "writer_kind": r[1], "reason": r[2],
                 "reason_code": r[3], "trust_score": r[4],
                 "elevated": bool(r[5]), "target_memory_id": r[6],
                 "created_at": r[7]}
                for r in rows
            ]
            return {"status": "ok", "surface": surface,
                    "report_batch": raw, "proposals": props}
        return {"status": "error", "message": f"unknown surface in ref {ref!r}"}
    except Exception as e:
        return {"status": "error", "message": f"show {ref} failed: {e}"}


# ---------------------------------------------------------------------------
# Dispatch: approve / reject route to the correct underlying enactment path.
# This is a ROUTER — it does not re-implement any enactment.
# ---------------------------------------------------------------------------


def approve(ref: str, *, titles: Optional[Sequence[str]] = None,
            ids: Optional[Sequence[int]] = None,
            db: Optional[str] = None,
            confirm: bool = False,
            comment: Optional[str] = None,
            confirmed_via: str = "session",
            actor: Optional[str] = None) -> Dict[str, Any]:
    """Approve (and enact) items in ONE surface item.

    self-eval:<report_id>  -> requires `titles` (>=1). Drives the existing
        monthly_self_eval.drew_review() -> enact_approved() path. There is
        deliberately no approve-all: each proposal is named. ``confirm`` is
        threaded into enact_approved for the L5-I-β variance-gate (a bulk
        enactment returns needs_confirm unless confirm=True).
    belief:<report_batch>  -> requires `ids` (>=1 proposal id). Drives the
        existing belief_engine.approve_proposals() enactment.
    governance:<report_batch> -> requires `ids` (>=1 governance proposal id).
        Marks the held write(s) approved on the governance surface so the
        writer's own re-attempt path may proceed (the surface is a ROUTER — it
        records the approval; it does not itself re-run the held write).

    D4: a ``comment`` (Drew's free-text) is recorded as a HIGH-TRUST training
    signal (per L5-I-alpha, human-authored => floors at COMMENT_TRUST_FLOOR) and
    linked to the proposal. Every enact also writes an audit-log row (who/what/
    when/decision/comment + reversibility note). ``confirmed_via`` records HOW
    the enact was confirmed ('interactive' | 'flag' | 'session') for the audit.

    Returns the underlying path's result dict (status ok/error). Never raises.
    """
    db_path = resolve_db(db)
    out = _approve_dispatch(db_path, ref, titles=titles, ids=ids, confirm=confirm,
                            actor=actor)
    return _post_enact(db_path, ref, "approve", out, comment=comment,
                       titles=titles, ids=ids, confirmed_via=confirmed_via,
                       decision="approved", actor=actor)


def decide(ref: str, *, approve_titles=None, reject_titles=None,
           reason: Optional[str] = None, db: Optional[str] = None,
           confirm: bool = False, comment: Optional[str] = None,
           confirmed_via: str = "session",
           actor: Optional[str] = None) -> Dict[str, Any]:
    """UNIT B. ONE sitting, BOTH directions, ONE drew_review call.

    WHY THIS EXISTS, measured: `drew_review(approved=..., rejected=...)` has
    accepted both lists in one call since inception, but no surface could reach
    it. `voidloom/cli.py` dispatches --approve and RETURNS before --reject is
    read, and both share one --title list -- so a mixed decision required two
    invocations. On the pre-landing tree the second was refused outright
    ("already reviewed"), which is how three of Drew's four decisions were eaten
    on 2026-08-02 (approval_audit_log #6, #7, #8).

    This is DURABLE, not CONSEQUENTIAL (#16789): it records the decision and
    enacts approvals. It does NOT stop a proposal recurring -- the producer does
    not read decisions at all, which is Unit D. A rejection here is honest and
    permanent; it is simply not yet an input to next month's report.

    NOT one-shot (#16815): a partial sitting leaves the rest open, and a later
    call decides the remainder. Verified by the Unit B gate's two-sitting rung.

    self-eval only. belief/governance take --ids and keep --approve/--reject;
    this refuses on them BY NAME rather than guessing.
    """
    db_path = resolve_db(db)
    yes = list(approve_titles or [])
    no = list(reject_titles or [])

    # VALIDATION REFUSALS RETURN BEFORE `_post_enact`, WHICH WRITES AN AUDIT ROW.
    # Found by probing my own work before shipping it: three malformed calls left
    # three approval_audit_log rows behind. `_post_enact` documents that it audits
    # EVERY attempt "so the trail is complete", and for a BACKEND failure that is
    # exactly right -- it is how audit #6/#7/#8 preserve the three decisions the
    # tool ate on 2026-08-02. But a TYPO is not an attempt that failed; auditing it
    # dilutes the one trail that makes those losses visible, and it breaks the
    # lazy-path-side-effect-free rule (#16815) that the anti-fatigue design rests
    # on. Malformed input is refused here, before anything is written.
    surface, raw = _parse_ref(ref)
    if surface != SURFACE_SELF_EVAL:
        return _err("--decide is the self-eval surface only; " + str(surface)
                    + " proposals are named by id -- use `--approve " + ref
                    + " --ids N` or `--reject " + ref + " --ids N`.")
    if not yes and not no:
        return _err("--decide requires >=1 --yes or --no (there is no "
                    "decide-all). Run `voidloom approvals --show " + ref
                    + "` to see titles.")
    overlap = sorted(set(yes) & set(no))
    if overlap:
        return _err("the same title cannot be approved and rejected in one "
                    "sitting: " + ", ".join(overlap))

    out = _decide_dispatch(db_path, ref,
                           approve_titles=yes,
                           reject_titles=no,
                           reason=reason, confirm=confirm, actor=actor)
    both = yes + no
    return _post_enact(db_path, ref, "decide", out, comment=comment,
                       titles=both, ids=None, confirmed_via=confirmed_via,
                       decision="decided", actor=actor)


def _decide_dispatch(db_path: str, ref: str, *, approve_titles=None,
                     reject_titles=None, reason: Optional[str] = None,
                     confirm: bool = False,
                     actor: Optional[str] = None) -> Dict[str, Any]:
    """The pure dispatch. Audit + comment are layered outside, in decide()."""
    # NOTE: decide() validates surface/emptiness/overlap BEFORE calling this, so
    # from that path these three guards cannot fire. They are kept because this
    # function is importable on its own and a guard that only exists in the
    # caller is a guard the next caller does not inherit.
    surface, raw = _parse_ref(ref)
    yes = list(approve_titles or [])
    no = list(reject_titles or [])
    try:
        if surface != SURFACE_SELF_EVAL:
            return _err("--decide is the self-eval surface only; "
                        + surface + " proposals are named by id -- use "
                        "`--approve " + ref + " --ids N` or `--reject " + ref
                        + " --ids N`.")
        if _SE is None:
            return _err("monthly_self_eval module not available")
        if not yes and not no:
            return _err("--decide requires >=1 --yes or --no (there is no "
                        "decide-all). Run `voidloom approvals --show " + ref
                        + "` to see titles.")
        overlap = sorted(set(yes) & set(no))
        if overlap:
            return _err("the same title cannot be approved and rejected in one "
                        "sitting: " + ", ".join(overlap))

        report_id = int(raw)
        rejected = [{"title": t, "reason": (reason or "declined via --decide")}
                    for t in no]
        review = _SE.drew_review(db_path, report_id,
                                 approved=yes, rejected=rejected, actor=actor)
        if review.get("status") != "ok":
            return {"action": "review_failed", "surface": surface, "ref": ref,
                    "review": review, "status": review.get("status", "error")}

        enact = None
        if yes:
            try:
                enact = _SE.enact_approved(db_path, report_id, confirm=confirm,
                                           actor=actor)
            except TypeError:
                enact = _SE.enact_approved(db_path, report_id)
            if isinstance(enact, dict) and enact.get("status") == "needs_confirm":
                return {"status": "needs_confirm",
                        "action": "variance_gate_held", "surface": surface,
                        "ref": ref, "report_id": report_id, "approved": yes,
                        "rejected": no, "review": review, "enact": enact}
        if isinstance(enact, dict) and enact.get("status") not in (None, "ok"):
            return {"status": "error", "action": "decided_not_enacted", "surface": surface,
                    "ref": ref, "report_id": report_id, "approved": yes,
                    "rejected": no, "review": review, "enact": enact}
        return {"status": "ok", "action": "decided", "surface": surface,
                "ref": ref, "report_id": report_id, "approved": yes,
                "rejected": no, "review": review, "enact": enact,
                "still_open": review.get("open_titles")}
    except Exception as e:
        return _err(type(e).__name__ + ": " + str(e))


def _approve_dispatch(db_path: str, ref: str, *,
                      titles: Optional[Sequence[str]] = None,
                      ids: Optional[Sequence[int]] = None,
                      confirm: bool = False,
                      actor: Optional[str] = None) -> Dict[str, Any]:
    """The pure dispatch (unchanged routing to the existing enactment paths). The
    D4 comment + audit recording is layered OUTSIDE this, in approve()."""
    surface, raw = _parse_ref(ref)
    try:
        if surface == SURFACE_SELF_EVAL:
            if _SE is None:
                return _err("monthly_self_eval module not available")
            titles = list(titles or [])
            if not titles:
                return _err("self-eval approve requires >=1 --title "
                            "(there is no approve-all). Run "
                            f"`voidloom approvals --show self-eval:{raw}` "
                            "to see titles.")
            report_id = int(raw)
            review = _SE.drew_review(db_path, report_id, approved=titles, actor=actor)
            if review.get("status") != "ok":
                return {"action": "review_failed", "surface": surface,
                        "ref": ref, "review": review,
                        "status": review.get("status", "error")}
            # L5-I-β variance-gate: pass confirm through (older monthly_self_eval
            # without the confirm kwarg still works — guarded by TypeError).
            try:
                enact = _SE.enact_approved(db_path, report_id, confirm=confirm,
                                           actor=actor)
            except TypeError:
                enact = _SE.enact_approved(db_path, report_id)
            if isinstance(enact, dict) and enact.get("status") == "needs_confirm":
                return {"status": "needs_confirm", "action": "variance_gate_held",
                        "surface": surface, "ref": ref, "report_id": report_id,
                        "approved": titles, "review": review, "enact": enact}
            # An enactment that reported an error without raising is NOT done: the
            # decision stands, but the result must not say "enacted" (review 2, N1).
            if isinstance(enact, dict) and enact.get("status") not in (None, "ok"):
                return {"status": "error", "action": "approved_not_enacted",
                        "surface": surface, "ref": ref, "report_id": report_id,
                        "approved": titles, "review": review, "enact": enact}
            return {"status": "ok", "action": "approved_and_enacted",
                    "surface": surface, "ref": ref, "report_id": report_id,
                    "approved": titles, "review": review, "enact": enact}

        if surface == SURFACE_BELIEF:
            if _BE is None:
                return _err("belief_engine module not available")
            ids = [int(x) for x in (ids or [])]
            if not ids:
                return _err("belief approve requires >=1 --ids "
                            "(name each proposal id). Run "
                            f"`voidloom approvals --show belief:{raw}` to see ids.")
            conn = _open_rw(db_path)
            try:
                if not _BE.migration_applied(conn):
                    return _err("belief substrate not present")
                out = _BE.approve_proposals(conn, ids)
            finally:
                conn.close()
            return {"status": "ok", "action": "approved_and_enacted",
                    "surface": surface, "ref": ref, "approved_ids": ids, **out}

        if surface == SURFACE_GOVERNANCE:
            if _GOV is None:
                return _err("governance module not available")
            ids = [int(x) for x in (ids or [])]
            if not ids:
                return _err("governance approve requires >=1 --ids "
                            "(name each held proposal id). Run "
                            f"`voidloom approvals --show governance:{raw}` to see ids.")
            out = _gov_decide(db_path, ids, status="approved", reason="approved by the owner")
            return {"status": out.get("status", "ok"),
                    "action": "governance_holds_approved",
                    "surface": surface, "ref": ref, "approved_ids": ids, **out}

        return _err(f"unknown surface in ref {ref!r}")
    except Exception as e:  # graceful: a dispatch failure is a clean error
        return _err(f"approve {ref} failed: {e}")


def _post_enact(db_path: str, ref: str, action: str, out: Dict[str, Any], *,
                comment: Optional[str], titles: Optional[Sequence[str]],
                ids: Optional[Sequence[int]], confirmed_via: str,
                decision: str, actor: Optional[str] = None) -> Dict[str, Any]:
    """Layer the D4 comment + audit recording over a dispatch result, then
    return the (enriched) result. Recording is BEST-EFFORT: an audit/comment
    failure NEVER changes the enact outcome (the surface must never raise). The
    audit row is written for EVERY attempt (success or backend-error) so the
    trail is complete; the comment is recorded whenever Drew supplied one.

    The enriched result gains `audit_id` (+ `comment_id` and `reversible_note`
    when present) so the caller / Face renders the safety trail."""
    if not isinstance(out, dict):
        return out
    surface, _raw = _parse_ref(ref)
    status = out.get("status")

    comment_id: Optional[int] = None
    if comment and str(comment).strip():
        title = list(titles or [None])[0] if titles else None
        crec = record_comment(ref, comment, decision, item_title=title,
                              item_ids=ids, db=db_path)
        if isinstance(crec, dict) and crec.get("status") == "ok":
            comment_id = crec.get("comment_id")
            out["comment_id"] = comment_id
            out["comment_high_trust"] = True

    arec = write_audit(
        ref, action, confirmed_via=confirmed_via,
        decision_detail={"titles": list(titles) if titles else None,
                         "ids": [int(x) for x in ids] if ids else None},
        comment_id=comment_id, ref_valid=True,
        outcome_status=status, outcome=out, actor=actor, db=db_path,
    )
    if isinstance(arec, dict) and arec.get("status") == "ok":
        out["audit_id"] = arec.get("audit_id")
        out["reversible_note"] = arec.get("reversible_note")
    return out


def _gov_decide(db_path: str, ids: Sequence[int], *, status: str,
                reason: str) -> Dict[str, Any]:
    """Mark governance_proposals rows approved/rejected (soft status flip, never
    delete). The held write's RE-ATTEMPT is the writer's responsibility — this
    surface only records Drew's decision. Returns a count receipt. Never raises
    out of the dispatcher (the caller wraps in try/except)."""
    if _GOV is None:
        return _err("governance module not available")
    conn = _open_rw(db_path)
    try:
        if not _GOV.migration_applied(conn):
            return _err("governance substrate not present")
        placeholders = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE governance_proposals SET status = ?, "
            f"decided_at = datetime('now'), decided_reason = ? "
            f"WHERE id IN ({placeholders}) AND status = 'pending'",
            [status, reason] + list(ids),
        )
        conn.commit()
        return {"changed": cur.rowcount, "decided_status": status}
    finally:
        conn.close()


def reject(ref: str, *, titles: Optional[Sequence[str]] = None,
           ids: Optional[Sequence[int]] = None, reason: Optional[str] = None,
           db: Optional[str] = None, comment: Optional[str] = None,
           confirmed_via: str = "session",
           actor: Optional[str] = None) -> Dict[str, Any]:
    """Reject items in ONE surface item. Records a rejection; NEVER enacts.

    self-eval:<report_id>  -> requires `titles` (>=1); drives
        monthly_self_eval.drew_review(rejected=...).
    belief:<report_batch>  -> requires `ids` (>=1); drives
        belief_engine.reject_proposals(...).

    D4: a ``comment`` is recorded as a high-trust signal (linked, decision=
    'rejected') and an audit row is written. ``reason`` (the structured rejection
    reason fed to the backend) and ``comment`` (Drew's free-text training signal)
    are distinct; when only ``comment`` is given it also seeds ``reason``.
    """
    db_path = resolve_db(db)
    if comment and not reason:
        reason = str(comment).strip()
    out = _reject_dispatch(db_path, ref, titles=titles, ids=ids, reason=reason,
                           actor=actor)
    return _post_enact(db_path, ref, "reject", out, comment=comment,
                       titles=titles, ids=ids, confirmed_via=confirmed_via,
                       decision="rejected", actor=actor)


def _reject_dispatch(db_path: str, ref: str, *,
                     titles: Optional[Sequence[str]] = None,
                     ids: Optional[Sequence[int]] = None,
                     reason: Optional[str] = None,
                     actor: Optional[str] = None) -> Dict[str, Any]:
    """The pure reject dispatch (unchanged routing). D4 comment/audit recording
    is layered OUTSIDE this, in reject()."""
    surface, raw = _parse_ref(ref)
    reason = reason or "rejected"
    try:
        if surface == SURFACE_SELF_EVAL:
            if _SE is None:
                return _err("monthly_self_eval module not available")
            titles = list(titles or [])
            if not titles:
                return _err("self-eval reject requires >=1 --title.")
            rejected = [{"title": t, "reason": reason} for t in titles]
            review = _SE.drew_review(db_path, int(raw), rejected=rejected, actor=actor)
            review.setdefault("action", "reject_recorded")
            review["surface"] = surface
            review["ref"] = ref
            return review

        if surface == SURFACE_BELIEF:
            if _BE is None:
                return _err("belief_engine module not available")
            ids = [int(x) for x in (ids or [])]
            if not ids:
                return _err("belief reject requires >=1 --ids.")
            conn = _open_rw(db_path)
            try:
                if not _BE.migration_applied(conn):
                    return _err("belief substrate not present")
                out = _BE.reject_proposals(conn, ids, reason=reason)
            finally:
                conn.close()
            return {"status": "ok", "action": "reject_recorded",
                    "surface": surface, "ref": ref, "rejected_ids": ids, **out}

        if surface == SURFACE_GOVERNANCE:
            if _GOV is None:
                return _err("governance module not available")
            ids = [int(x) for x in (ids or [])]
            if not ids:
                return _err("governance reject requires >=1 --ids.")
            out = _gov_decide(db_path, ids, status="rejected", reason=reason)
            return {"status": out.get("status", "ok"), "action": "reject_recorded",
                    "surface": surface, "ref": ref, "rejected_ids": ids, **out}

        return _err(f"unknown surface in ref {ref!r}")
    except Exception as e:
        return _err(f"reject {ref} failed: {e}")


def _parse_ref(ref: str) -> Tuple[str, str]:
    """Split a tagged reference into (surface, raw). Accepts:
        'self-eval:7'  -> ('self-eval', '7')
        'belief:belief-sweep-...'  -> ('belief', 'belief-sweep-...')
    A bare integer is treated as self-eval (back-compat with the old verb).
    """
    if ref is None:
        return ("", "")
    s = str(ref)
    if ":" in s:
        surface, raw = s.split(":", 1)
        surface = surface.strip().lower()
        # R7/C5. The CANONICAL spelling in each alias tuple is the constant,
        # not a copy of it. The shorthands beside it are user-input aliases and
        # are deliberately literal -- they are what a human types, not what the
        # ledger stores, and they must keep working if the canonical name ever
        # changes. Binding the canonical one is what stops this table from
        # being a fourth declaration of the vocabulary.
        if surface in (SURFACE_SELF_EVAL, "selfeval", "self_eval", "se"):
            return (SURFACE_SELF_EVAL, raw.strip())
        if surface in (SURFACE_BELIEF, "be"):
            return (SURFACE_BELIEF, raw.strip())
        if surface in (SURFACE_GOVERNANCE, "gov", "g"):
            return (SURFACE_GOVERNANCE, raw.strip())
        return (surface, raw.strip())
    # Bare value: integers default to self-eval (the original approvals UX).
    if s.strip().lstrip("-").isdigit():
        return (SURFACE_SELF_EVAL, s.strip())
    return ("", s.strip())


def _err(message: str) -> Dict[str, Any]:
    return {"status": "error", "message": message}


# ---------------------------------------------------------------------------
# Human render.
# ---------------------------------------------------------------------------


def render(items: List[Dict[str, Any]], *,
           trusted: Optional[bool] = None,
           reason: Optional[str] = None,
           total: Optional[int] = None,
           breakdown: Optional[Dict[str, int]] = None,
           unread: Sequence[str] = ()) -> str:
    """A calm human view of the unified pending list. Pure string assembly.

    R5/C5 (RENDER layer). `trusted` is keyword-only and defaults to None so every
    historical caller keeps its behaviour, but the ONE sentence that asserts a
    decided state -- "nothing awaits review. The void is quiet." -- may no longer
    be printed about a set nobody read. Passing `trusted=False` swaps it for the
    house refusal. This member carries no absence branch of its own, which is
    exactly why a file-scanning sweep cleared it twice.
    """
    # R7/C2 + [D4]. SOME source answered -> the human face states the LOWER
    # BOUND and names the hole, then renders the items exactly as it always
    # did. It does not print "I could not tell" about a number it could tell,
    # and it does not silently drop the caveat either: the prefix rides above
    # the ordinary render rather than replacing it.
    _un = tuple(str(x) for x in (unread or ()))
    _partial = bool(_un) and not _all_sources_unread(_un, breakdown)
    _prefix = ""
    if _partial:
        _prefix = ("voidloom approvals — at least " + str(int(total or 0))
                   + " pending"
                   + (" (" + ", ".join(f"{k}: {v}" for k, v
                                       in sorted((breakdown or {}).items()))
                      + ")" if breakdown else "")
                   + " — UNREAD: " + ", ".join(_un)
                   + (f"\n  reason: {reason}" if reason else "") + "\n")
    if trusted is False and not _partial:
        return ("voidloom approvals — I could not tell. The pending count is "
                "UNKNOWN, not zero."
                + (f"\n  reason: {reason}" if reason else ""))
    if not items:
        return (_prefix + "voidloom approvals — nothing awaits review. "
                "The void is quiet.") if not _partial else _prefix.rstrip("\n")
    # This is a DISPLAY tally of the list render() was handed, not a measurement
    # of the pending set (that is unified_count, which delegates). It is written
    # with Counter so no per-surface count SPELLING survives in this module for a
    # later reader to mistake for the count -- which is how the five spellings
    # came about in the first place.
    _by_surface = Counter(it.get("surface") for it in items)
    n = _by_surface.get(SURFACE_SELF_EVAL, 0)
    m = _by_surface.get(SURFACE_BELIEF, 0)
    g = _by_surface.get(SURFACE_GOVERNANCE, 0)
    total = n + m + g
    noun = "item" if total == 1 else "items"
    header_bits = []
    if n:
        header_bits.append(f"{n} self-eval")
    if m:
        header_bits.append(f"{m} belief")
    if g:
        header_bits.append(f"{g} governance")
    head = f"voidloom approvals — {total} {noun} await ({' · '.join(header_bits)})"
    lines = [head, "=" * len(head)]
    for i, it in enumerate(items, 1):
        star = "*" if it.get("elevated") else " "
        cnt = it.get("count", 0)
        sub = f"{cnt} proposal" + ("" if cnt == 1 else "s")
        lines.append(f"{i:>2}. [{star}] {it['surface']:<10} {it.get('title','')}"
                     f"   ({sub})")
        lines.append(f"       approve:  {it.get('approve_cmd','')}")
        lines.append(f"       reject :  {it.get('reject_cmd','')}")
    lines.append("")
    lines.append("Approve one-by-one — each line above is a copy-paste command. "
                 "Elevated (*) items touch a foundation memory or carry a "
                 "high-priority proposal; review them first.")
    return _prefix + "\n".join(lines)


# ---------------------------------------------------------------------------
# Thin CLI.
#
# DEFENCE-IN-DEPTH (#176, Surface 2): this backend `_cli` is reachable directly
# via ``python3 voidloom_approvals.py --approve …``. The blessed front door is
# the package CLI (``voidloom approvals``), which gates an enact behind a
# confirm-gate + a local-FS locality guard + a ref-regex. To CLOSE the gap so
# BOTH entrypoints gate IDENTICALLY, the same three checks are mirrored here,
# SELF-CONTAINED (no dependency on the ``voidloom`` package — this module must
# stand alone). The semantics match the package CLI exactly:
#   * ref-regex validation BEFORE any backend call (APPROVAL_REF_RE, already
#     defined in this module) — a malformed/injection ref refuses, even with
#     --confirm;
#   * local-FS locality guard (the auth model, #6105) on ANY DB access — a
#     UNC/network/synced-cloud brain is not a sound auth boundary;
#   * confirm-gate on the ENACT paths (--approve/--reject): an explicit
#     --confirm flag OR an interactive y/N; a bare enact under --json or a piped
#     stdin WITHOUT --confirm is refused (no silent enact, #6055).
# Read paths (--show, the default list) stay open (read-only aggregation), but
# still pass the locality guard. Refusals exit 3 (distinct from failure->1) so a
# caller tells 'blocked by policy' from 'failed', matching the package CLI.
# ---------------------------------------------------------------------------

#: Cloud-sync / network markers for the SELF-CONTAINED locality guard. Mirrors
#: voidloom._guard's set (kept local so this module needs no package import).
_CLI_CLOUD_SYNC_MARKERS: Tuple[str, ...] = (
    "/dropbox/", "/onedrive", "/google drive/", "/googledrive/", "/icloud",
    "/com~apple~clouddocs/", "/mobile documents/", "/box sync/", "/pcloud/",
    "/mega/", "/nextcloud/", "/owncloud/", "/sync.com/", "/cryptomator/",
)
_CLI_NETWORK_FSTYPES: Tuple[str, ...] = (
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "fuse.sshfs",
    "fuse.rclone", "fuse.gdrive", "fuse.davfs", "davfs", "ncpfs",
    "glusterfs", "ceph", "9p",
)


def _cli_locality_refusal(db: Optional[str]) -> Optional[Dict[str, Any]]:
    """Self-contained local-FS locality guard (the auth model, #6105). Returns
    None when the resolved DB path is a sound LOCAL auth boundary, else a typed
    ``non_local_db`` refusal dict. Never raises (any ambiguity -> a clean
    refusal is the fail-closed default, but an unresolvable probe degrades to
    'allow' only for the cheap structural checks below)."""
    raw = resolve_db(db)
    s = str(raw).strip()
    # UNC / POSIX double-slash authority path (//host/share) — refuse pre-FS.
    if s.startswith("\\\\") or (s.startswith("//") and not s.startswith("///")):
        return {"status": "refused", "code": "non_local_db",
                "message": (f"DB path {raw!r} is a UNC/network path; Voidloom's "
                            "auth model requires a LOCAL filesystem "
                            "(locality-as-auth).")}
    try:
        resolved = str(Path(raw).resolve())
    except Exception:
        resolved = s
    low = resolved.replace("\\", "/").lower()
    probe = low if low.endswith("/") else low + "/"
    if any(marker in probe for marker in _CLI_CLOUD_SYNC_MARKERS):
        return {"status": "refused", "code": "non_local_db",
                "message": (f"DB path {resolved!r} lives under a synced-cloud "
                            "root; a synced brain is not a sound local auth "
                            "boundary.")}
    netfs = _cli_network_fstype_for(resolved)
    if netfs is not None:
        return {"status": "refused", "code": "non_local_db",
                "message": (f"DB path {resolved!r} is on a {netfs} (network) "
                            "mount; Voidloom requires local block storage for "
                            "its auth model.")}
    return None


def _cli_network_fstype_for(resolved: str) -> Optional[str]:
    """Best-effort: the network fstype backing ``resolved`` per /proc/mounts,
    else None. Never raises; absence of /proc/mounts -> None (does not block)."""
    try:
        best_mount = ""
        best_fstype = ""
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point, fstype = parts[1], parts[2]
                if (resolved == mount_point
                        or resolved.startswith(mount_point.rstrip("/") + "/")):
                    if len(mount_point) >= len(best_mount):
                        best_mount = mount_point
                        best_fstype = fstype
        if best_fstype and best_fstype.lower() in _CLI_NETWORK_FSTYPES:
            return best_fstype
    except Exception:
        return None
    return None


def _cli_validate_ref(ref: str) -> Optional[Dict[str, Any]]:
    """Return None when ``ref`` is structurally valid (APPROVAL_REF_RE), else a
    typed ``invalid_approval_ref`` refusal dict. Runs BEFORE any backend call so
    a malformed/injection ref is refused even with --confirm."""
    if not ref or not APPROVAL_REF_RE.match(ref):
        return {"status": "refused", "code": "invalid_approval_ref",
                "message": (f"approval ref {ref!r} is malformed; expected "
                            "'self-eval:<id>', 'belief:<batch>' or "
                            "'governance:<batch>'"),
                "given": ref, "pattern": APPROVAL_REF_RE.pattern}
    return None


def _cli_confirm_gate(action: str, ref: str, *, as_json: bool,
                      confirm_flag: bool) -> Optional[Dict[str, Any]]:
    """Confirmation gate for an enact (mirrors voidloom.cli._confirm_gate). Returns
    None to PROCEED or a typed refusal dict to BLOCK. There is NO silent enact:

      * EXPLICIT --confirm -> proceed (the sanctioned non-interactive path);
      * else require an interactive TTY y/N; --json or a non-TTY stdin without
        --confirm is refused (``confirm_required_no_tty``);
      * an interactive answer other than y/yes is refused (``confirm_declined``).
    """
    if confirm_flag:
        return None
    if as_json or not sys.stdin or not sys.stdin.isatty():
        return {"status": "refused", "code": "confirm_required_no_tty",
                "message": (f"refusing to {action} {ref!r}: enact actions require "
                            "confirmation. Pass --confirm for a non-interactive "
                            "affirmation, or re-run from a terminal (without "
                            "--json) for an interactive y/N."),
                "ref": ref, "action": action}
    prompt = (f"voidloom_approvals: {action} {ref}? This enacts a state change. "
              "Type 'y' to confirm [y/N]: ")
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        return {"status": "refused", "code": "confirm_declined",
                "message": f"{action} of {ref!r} not confirmed; no action taken.",
                "ref": ref, "action": action}
    return None


def _cli(argv: List[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="voidloom_approvals",
        description="The unified Voidloom approval surface (self-eval + belief).")
    p.add_argument("--db", default=None, help="path to the memory DB")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--show", metavar="REF", default=None,
                   help="show one item's proposals (self-eval:<id> | belief:<batch>)")
    p.add_argument("--approve", metavar="REF", default=None,
                   help="approve+enact items in REF "
                        "(self-eval:<id> needs --title; belief:<batch> needs --ids)")
    p.add_argument("--reject", metavar="REF", default=None,
                   help="reject items in REF (no enactment)")
    p.add_argument("--title", action="append", default=None, metavar="TITLE",
                   help="self-eval proposal title (repeatable)")
    p.add_argument("--ids", nargs="+", type=int, default=None, metavar="ID",
                   help="belief proposal ids")
    p.add_argument("--reason", default=None, help="reason recorded with a rejection")
    p.add_argument("--confirm", action="store_true", default=False,
                   help="non-interactive affirmation of an enact "
                        "(--approve/--reject); without it a --json or piped enact "
                        "is refused (no silent enact). Mirrors `voidloom approvals`.")
    args = p.parse_args(argv[1:])

    def emit(obj: Any) -> None:
        if args.json or not isinstance(obj, str):
            print(json.dumps(obj, indent=2, default=str))
        else:
            print(obj)

    as_json = bool(args.json)
    confirm_flag = bool(args.confirm)

    # The locality guard (auth model) applies to ANY DB access — read or enact.
    loc = _cli_locality_refusal(args.db)
    if loc is not None:
        emit(loc)
        return 3

    if args.show:
        bad = _cli_validate_ref(args.show)
        if bad is not None:
            emit(bad)
            return 3
        emit(show(args.show, db=args.db))
        return 0
    if args.approve:
        # Mirror the package CLI: ref-regex -> confirm-gate -> enact.
        bad = _cli_validate_ref(args.approve)
        if bad is not None:
            emit(bad)
            return 3
        gate = _cli_confirm_gate("approve", args.approve, as_json=as_json,
                                 confirm_flag=confirm_flag)
        if gate is not None:
            emit(gate)
            return 3
        out = approve(args.approve, titles=args.title, ids=args.ids, db=args.db,
                      confirm=confirm_flag,
                      confirmed_via=("flag" if confirm_flag else "interactive"))
        emit(out)
        return 0 if out.get("status") == "ok" else 1
    if args.reject:
        bad = _cli_validate_ref(args.reject)
        if bad is not None:
            emit(bad)
            return 3
        gate = _cli_confirm_gate("reject", args.reject, as_json=as_json,
                                 confirm_flag=confirm_flag)
        if gate is not None:
            emit(gate)
            return 3
        out = reject(args.reject, titles=args.title, ids=args.ids,
                     reason=args.reason, db=args.db,
                     confirmed_via=("flag" if confirm_flag else "interactive"))
        emit(out)
        return 0 if out.get("status") == "ok" else 1

    # Default: the unified list (read-only; already past the locality guard).
    # R5/C6. RESOLVE ONCE, THEN PASS THE PATH. `args.db` is None by default
    # and each reader below then resolved it independently -- and MEASURED, they
    # disagreed: `list_pending` landed on the brain beside this file while
    # `unified_count_ex` landed on ~/.claude/memory via decision_units' own
    # default. A count about one database printed beside a list about another is
    # the "surface and banner can never disagree" property broken by a PATH
    # rather than by a query, and no amount of delegating the QUERY closes it.
    _db_once = resolve_db(args.db)
    items = list_pending(db=_db_once)
    # R5/C5. The bit is READ HERE and handed to both faces, so the JSON envelope
    # and the human render cannot disagree about the same brain -- which is
    # exactly the disagreement C2 names between five surfaces.
    _uc = unified_count_ex(db=_db_once)
    total, breakdown, _tr, _why = _uc
    # R7/C2 -- THE MEMBER THE FIRST SWEEP MISSED, AND WHY. This frame unpacks
    # the trust bit into a local named `_tr`, which spells neither `trust` nor
    # `unknown`. A name-matching alias rule does not see it, so the site was
    # cleared while this shipped CLI went on emitting
    # `{"unified_count": null, ... , "items": [<the items it could not count>]}`
    # on a brain where two of three sources answered.
    #
    # R8/B1 -- WHAT STOOD HERE WAS A CLAIM OF NAME-INDEPENDENCE: that the
    # round's derivation located the trust bit by its position in
    # `unified_count_ex`'s return rather than by its spelling. Two things were
    # wrong with it and only one concerned the derivation. It was false about
    # the two lines beneath it, which reach the per-source channel BY ITS
    # SPELLED NAME -- `getattr(_uc, "unread", ())` is a name rule, and the
    # sentence denied there was one. And it was a claim about the ROUND'S
    # INSTRUMENT written inside a PRODUCT file that no instrument reads back:
    # ungradeable from here by construction, whatever its truth value. Deleted
    # per `conventions-015`, and described rather than quoted, because
    # re-uttering a repudiated sentence ships it again in the same bytes.
    # What this frame does is stated where it happens, two lines up.
    _un = tuple(getattr(_uc, "unread", ()) or ())
    _partial = bool(_un) and not _all_sources_unread(_un, breakdown)
    if args.json:
        if _partial:
            emit({"status": "partial", "action": "approvals_list",
                  "unified_count": total, "breakdown": breakdown,
                  "unreadable_sources": list(_un), "unknown": True,
                  "count_is_not_zero": True,
                  "message": ("partial: " + ", ".join(_un) + " UNREAD -- the "
                              "count is a LOWER BOUND"),
                  "reason": _why, "items": items})
            return 3
        if not _tr:
            emit({"status": "refused", "error": "approvals_count_unknown",
                  "action": "approvals_list", "unified_count": None,
                  "breakdown": None, "unreadable_sources": list(_un),
                  "unknown": True, "count_is_not_zero": True,
                  "message": ("I could not tell -- the pending count is "
                              "UNKNOWN, not zero"),
                  "reason": _why, "items": items})
            return 3
        emit({"status": "ok", "action": "approvals_list",
              "unified_count": total, "breakdown": breakdown,
              "items": items})
    else:
        emit(render(items, trusted=_tr, reason=_why, total=total,
                    breakdown=breakdown, unread=_un))
        if not _tr:
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))

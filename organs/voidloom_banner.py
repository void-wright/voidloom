#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_banner.py — cheap, fail-graceful Voidloom surface composers.

This module composes the Voidloom banner (and the smaller hook surfaces) from
ONLY cheap sources. It is the agnostic public-system surface — it carries the
Voidloom identity and NO persona text, so it stays usable in the teammate/public
repo where the persona preamble is stripped.

HARD CONSTRAINTS (these are load-bearing — Loop D contract):
  - NO embedding-model load. NO torch import. NO `anticipate` call. The only
    inputs are: (1) a tiny JSON cache file written by the pre-cognitive cron,
    (2) one cheap SQL JOIN (anti_recency.get_forgotten_foundations, limit 1),
    (3) two covered-index aggregates (the stable Loop C pending contract +,
        from L5-D/Unified-Approval-Surface, the belief edge_proposals pending
        BATCH count). Every read keeps the session-start hook under <2s.
  - FAIL-GRACEFUL by construction. Every source is wrapped so that if it is
    unavailable the corresponding line is simply skipped. Banner composition
    NEVER raises; a totally cold brain still yields a valid (sigil+wordmark)
    banner. This protects session start absolutely.
  - PER-SOURCE FAIL-GRACEFUL for the unified count: the self-eval source and the
    belief source are read INDEPENDENTLY. One source erroring counts the other
    (never 0-because-of-one-failure). Only if BOTH fail is the line silent.

R10 — THE ONE COUNT FUNCTION. This module carries NO count query of its own.
Every number it shows comes from decision_units.unified_open_count(), which is
also what Loop C's `approvals` verb and the faces read (see
loop_C_selfeval_enactment/loop_D_pending_count_query.md for the historical
contract this replaced: a literal `status='pending'` report count, which a
partial approval zeroed after decision 1 of 5).

The belief surface (L5-D) contributes its DISTINCT pending report_batch count —
NOT its per-proposal count: a 148-proposal nightly batch increments the banner
by 1, not 148 (anti-alarm-fatigue, the L5-D batching contract). The unified
surface that lets Drew review/approve both one-by-one is voidloom_approvals.py;
the banner's `→ voidloom approvals --tui` pointer now points at that unified surface.

Rendering is delegated entirely to void_render.py (the aesthetic SSOT).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Import the aesthetic SSOT. Keep this module importable even if void_render is
# momentarily missing (defensive — a broken aesthetic must not break a hook).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    import void_render as vr  # type: ignore
except Exception:  # pragma: no cover - defensive
    vr = None  # type: ignore

# R10 — THE ONE COUNT FUNCTION. This module no longer counts anything itself; it
# delegates to decision_units.unified_open_count. Five independent count
# spellings across four modules with three different subjects is what produced a
# docstring asserting "the surface and the banner can never disagree" about code
# that could. Import defensively so a missing ledger degrades to the historical
# behaviour rather than breaking session start.
#
# R2 -- AND THE SENTENCE ABOVE IS NOW TRUE. Until this round it was a promise the
# code did not keep: on the live tree `decision_units.py` does not exist, every
# delegated reader below returned 0, and Drew's banner went from 652 characters
# to 0. "Degrades to the historical behaviour" is implemented in THE ABSENT-LEDGER
# FALLBACK section below, not asserted here.
try:
    import decision_units as _DU  # type: ignore
except Exception as _e:  # pragma: no cover - defensive
    _DU = None  # type: ignore
    _DU_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
else:
    _DU_IMPORT_ERROR = None

#: Populated whenever a banner source degrades. R17: a degradation is RECORDED,
#: never swallowed, so a zero can be told apart from a broken read.
LAST_DEGRADE: List[str] = []

# anti_recency lives alongside the brain. Import defensively (Loop A P001:
# dual-path import with graceful-degrade guard).
try:
    import anti_recency  # type: ignore
except Exception:  # pragma: no cover - degrades to "no forgotten line"
    anti_recency = None  # type: ignore


# --- Paths (env > relative-self), mirroring session_start_inject.py ----------

def _tree_root() -> Path:
    """The DATA root. Since the 2026-09-07 layout this module lives in
    ``organs/`` and the DB + caches sit one level up. Probed, not assumed, so a
    flat legacy tree still resolves."""
    return _HERE.parent if _HERE.name == "organs" else _HERE


def resolve_home() -> str:
    return os.environ.get("WIZARD_LIBRARY_HOME") or str(_tree_root())


def resolve_db() -> str:
    return os.environ.get("WIZARD_LIBRARY_DB") or str(Path(resolve_home()) / "claude_memory.db")


def cache_path() -> str:
    """Location of the small pre-cognitive cache the cron writes.

    Env override (VOIDLOOM_PRECOG_CACHE) lets the cron and the reader agree on a
    non-default path; otherwise it sits beside the brain so it travels with it.
    """
    return os.environ.get("VOIDLOOM_PRECOG_CACHE") or str(
        Path(resolve_home()) / "pre_cognitive_cache.json"
    )


# ---------------------------------------------------------------------------
# THE ABSENT-LEDGER FALLBACK  (TIER-R ROUND 2, ruler VL-SURFACE-v1.2b)
# ---------------------------------------------------------------------------
#
# WHAT WENT WRONG, MEASURED, NOT INFERRED. The R10 delegation promises at :61
# that a missing ledger "degrades to the historical behaviour". It degraded to
# ZERO. `decision_units.py` does not exist on the live tree, so `_DU is None`,
# so `read_pending_count` returned 0, so `read_unified_pending` returned 0, so
# `build_banner`'s appearance gate returned "". Measured on a copy of the live
# tree carrying only this file, rendered with NO force_mode (the real non-TTY
# channel, `detect_mode() == 'none'`): 652 characters -> 0. Drew saw nothing.
#
# THE RULE ENFORCED HERE IS THE SIBLING'S, COPIED, NOT INVENTED.
# `monthly_self_eval._approved_unenacted_here` meets the same absence and
# returns a NON-ZERO count with its reason printed, precisely so the caller
# cannot project a decided state it did not earn -- "a report left visible is
# recoverable, a report that reached a terminal state it had not earned is not,
# because every offer surface stops naming it." A banner rendering zero bytes IS
# that terminal state, on the one surface whose entire purpose is to be seen.
#
# "I COULD NOT TELL" IS NOT "THERE IS NOTHING". The readers below return None
# for the first and a value for the second, and every caller that WRITES durable
# state has to tell them apart. `.voidloom_notify_state.json` was silently
# rewritten 1 -> 0 because nothing could.
#
# WHY THE QUERY LIVES HERE, AND NOWHERE ELSE. R10 forbids a SECOND spelling of a
# query the ledger already owns. When `import decision_units` itself fails there
# is no first spelling to defer to: `decision_units._legacy_self_eval_report_ids`
# is unreachable by definition, which is exactly why the R10 comments that point
# at it could not keep their promise. So the historical contract lives ONCE,
# here, in the module every consumer already imports (hooks/voidloom_notify.py
# imports voidloom_banner on the live tree today), and `voidloom_approvals` and
# `claude_memory` reach it through this module rather than each re-typing SQL.

#: The historical self-eval contract, from this file's own pre-R10 docstring:
#: "SELECT COUNT(*) FROM self_eval_reports WHERE status='pending'". Kept as the
#: ID SET rather than a bare COUNT so a caller can name the set and not only its
#: cardinality -- "a count cannot see a swap" (#13744).
_LEGACY_SELF_EVAL_IDS_SQL = (
    "SELECT id FROM self_eval_reports WHERE status='pending'"
)
#: The historical newest-pending-report selection, verbatim from the live file.
_LEGACY_LATEST_REPORT_SQL = (
    "SELECT id, month, created_at FROM self_eval_reports "
    "WHERE status='pending' ORDER BY created_at DESC, id DESC LIMIT 1"
)
#: The historical L5-D batching contract, verbatim from the live file.
_LEGACY_BELIEF_BATCH_SQL = (
    "SELECT COUNT(DISTINCT report_batch) FROM edge_proposals "
    "WHERE status='pending'"
)


def legacy_self_eval_report_ids(db: Optional[str] = None) -> Optional[List[int]]:
    """The historical pending self-eval report ids, or None if UNREADABLE.

    RETURN CONTRACT, and it is the point of the function:
      * ``[...]`` / ``[]``  -- the query ran. An empty list is a KNOWN zero.
      * ``None``            -- the query could not run. The count is UNKNOWN and
                               no caller may render it as an empty inbox.

    R4/P1 -- THIS DOCSTRING USED TO ARGUE THE OPPOSITE, and the argument
    was measured wrong at the referent that matters. A missing
    `self_eval_reports` is UNKNOWN, not a known zero: the table is created
    by migration_021, `claude_memory.py init` does not apply it, and the
    monthly timer means a fresh install renders a confident "nothing
    awaits review" for up to 29 days. `None` = I could not tell; `[]` =
    I looked and there is nothing. A database that will not open, or a
    query that raises, is also unknown.
    """
    db = db or resolve_db()
    try:
        conn = _open_ro(db)
    except Exception as e:
        LAST_DEGRADE.append(f"legacy_self_eval_report_ids: cannot read {db}: "
                            f"{type(e).__name__}: {e}")
        return None
    try:
        if not _table_exists(conn, "self_eval_reports"):
            # R4/P1, THE NAMED SITE. The R3 docstring above argued an
            # absent table is a KNOWN zero because "a brain that has
            # never been migrated has no pending reports". True about the
            # ROWS and false about the ANSWER: nothing has ever looked,
            # because there is nothing to look at, and the self-eval
            # timer is monthly -- so `claude_memory.py init` buys a
            # confident "nothing awaits review" for up to 29 days on a
            # brain where the machinery is simply not installed.
            LAST_DEGRADE.append(
                "legacy_self_eval_report_ids: self_eval_reports is "
                "ABSENT (migration_021 never applied); the pending "
                "count is UNKNOWN, not zero")
            return None
        return [int(r[0]) for r in conn.execute(_LEGACY_SELF_EVAL_IDS_SQL)]
    except (sqlite3.Error, OSError, ValueError, TypeError) as e:
        LAST_DEGRADE.append(f"legacy_self_eval_report_ids: "
                            f"{type(e).__name__}: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass


def legacy_belief_batch_count(db: Optional[str] = None) -> Optional[int]:
    """The historical pending belief BATCH count, or None if UNREADABLE.

    Same three-way return as `legacy_self_eval_report_ids`. `edge_proposals`
    absent is a KNOWN zero (the belief substrate is optional and may simply not
    be migrated -- the live file already treated that as 0, explicitly).
    """
    db = db or resolve_db()
    try:
        conn = _open_ro(db)
    except Exception as e:
        LAST_DEGRADE.append(f"legacy_belief_batch_count: cannot read {db}: "
                            f"{type(e).__name__}: {e}")
        return None
    try:
        if not _table_exists(conn, "edge_proposals"):
            return 0
        row = conn.execute(_LEGACY_BELIEF_BATCH_SQL).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, OSError, ValueError, TypeError) as e:
        LAST_DEGRADE.append(f"legacy_belief_batch_count: {type(e).__name__}: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass


def legacy_latest_pending_report(db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The historical newest pending report (id/month/created_at), else None.

    Both "no such report" and "could not read" are None here because the
    historical signature is `Optional[dict]` and this function restores that
    signature exactly. The two are still distinguishable: the unreadable case
    appends to LAST_DEGRADE, the empty case does not.
    """
    db = db or resolve_db()
    try:
        conn = _open_ro(db)
    except Exception as e:
        LAST_DEGRADE.append(f"legacy_latest_pending_report: cannot read {db}: "
                            f"{type(e).__name__}: {e}")
        return None
    try:
        if not _table_exists(conn, "self_eval_reports"):
            # R5/C6 (R4-F4). THIS BRANCH RETURNED None AND RECORDED NOTHING,
            # while the docstring six lines above promises "the unreadable case
            # appends to LAST_DEGRADE, the empty case does not". An ABSENT table
            # is the unreadable case, not the empty one: there is no inbox to be
            # empty. Its own sibling at `legacy_self_eval_report_ids` appends
            # here; this one did not, so the two members of one class in one file
            # disagreed about what silence meant.
            LAST_DEGRADE.append(
                f"legacy_latest_pending_report: self_eval_reports is ABSENT in "
                f"{db}; there is no latest pending report to report and no "
                f"inbox to be empty -- this None is UNKNOWN, not 'none pending'")
            return None
        row = conn.execute(_LEGACY_LATEST_REPORT_SQL).fetchone()
        if not row:
            return None
        return {"id": row[0], "month": row[1], "created_at": row[2]}
    except (sqlite3.Error, OSError, ValueError, TypeError) as e:
        LAST_DEGRADE.append(f"legacy_latest_pending_report: "
                            f"{type(e).__name__}: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass


# ---------------------------------------------------------------------------
# Cheap source readers — each returns a safe default on ANY failure.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# R5/C1 -- THE ONE SPELLING OF "DID THE LEDGER ACTUALLY LOOK?"
# ---------------------------------------------------------------------------


def _breakdown_keys() -> Tuple[str, ...]:
    """`decision_units.BREAKDOWN_KEYS` when that module is importable, and the
    historical three-key contract on the line below when it is not.

    R8/B1. WHAT STOOD HERE WAS A COMPLETENESS CLAIM ABOUT DUPLICATE COPIES OF
    THIS KEY LIST, and it was false in the bytes it shipped in -- the fallback
    below is one such copy and module scope carried another. It is deleted
    rather than softened, because a completeness claim over a population this
    docstring cannot see is the ungraded claim `conventions-015` forbids: the
    check radius of that sentence was zero and its blast radius was the tree.
    AND IT IS DESCRIBED HERE RATHER THAN QUOTED. Re-uttering a repudiated
    sentence ships it again in the same bytes; no grep, and no reader skimming
    for the claim, can tell the quotation from the assertion. Measured: the
    first draft of this hunk quoted it, and the round's own predicate returned
    the replacement as a live member.

    The population that sentence asserted over is GRADED instead, by
    B1-VOCAB-SITES in `_canaryR/R8/prove_R8.py`, which sweeps every multi-string
    group in the ship set BY SHAPE, folds each token to a spelling-insensitive
    key, and fails if a FIFTH site appears or a bound site loses its binding.
    """
    return tuple(getattr(_DU, "BREAKDOWN_KEYS", None)
                 or ("self_eval", "belief", "governance"))


def _cr_unread(obj: Any) -> Tuple[str, ...]:
    """WHICH sources refused -- ASKED, never assumed.

    R6/U3. The line that used this information hardcoded ``["self_eval"]`` and
    zeroed the whole breakdown on ANY refusal. With governance dark and
    self-eval read, it therefore discarded two correct counts and named the one
    source it HAD successfully read. `decision_units` knew the answer and was
    never asked for it.

    A ledger too old to carry ``unread`` genuinely CANNOT say which source
    refused, so this names EVERY source the object publishes a term for -- "I
    cannot tell you which of these" is true; guessing one is what broke. There
    is no arm of this function that returns "nothing is unread" by default: the
    caller only reaches it once trust has already been refused.
    """
    _un = getattr(obj, "unread", None)
    if _un is not None:
        try:
            names = tuple(str(x) for x in _un)
        except TypeError:
            names = ()
        if names:
            return names
    return _breakdown_keys()


def _ledger_trust(obj: Any, where: str,
                  key: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """``(trusted, why_not)`` for ANY object `decision_units` hands back.

    ``key`` -- R6. THE ARITY COMPANION. An arity-1 reader
    (`read_pending_count_ex` counts self-eval and nothing else) must gate on ITS
    OWN source, not on the three-source conjunction. Without this, R6/U1's
    correct refusal about an absent `governance_proposals` would propagate into
    the self-eval reader and blank a count that was read perfectly well -- a
    trust fix that over-refuses makes Drew's screen darker, and a dark screen is
    the founding complaint of this build. The key path is used ONLY when the
    object can actually name its holes; a ledger that cannot falls through to
    the aggregate below, whose default is refusal.

    THE DEFECT THIS CLOSES (C-NEW-1, found by the R4 curation and by neither the
    generator nor the reflector). `read_pending_count_ex` wrote its ``(0, False)``
    ONLY inside an ``except`` clause. R4 then taught `decision_units` to CATCH its
    own failures and RETURN ``trusted=False`` instead of raising -- so the handler
    stopped firing and the literal ``True`` beside it became a compile-time
    constant. The function that DEFINES the trust contract answered ``(0, True)``
    in 4/4 unknown world-states, and `hooks/voidloom_notify.py`'s correct guard
    ``elif n != last and trusted:`` was defeated by being fed a true-looking lie:
    ``.voidloom_notify_state.json`` went 1 -> 0, and that file is not in git.

    THE SECOND DEFECT IT CLOSES, which the suggested fix would have shipped.
    ``getattr(obj, "trusted", True)`` -- the shape used at `unified_pending_report`
    -- defaults to *True* when the attribute is missing. The attribute is missing
    exactly when the installed `decision_units.py` PREDATES the trust vocabulary,
    i.e. on the STALE tree shape. A default of True re-commits C-NEW-1 in the one
    shape nobody graded. A module that cannot say whether it looked has not told
    us that it looked, so the absent attribute is UNKNOWN, never trust.

    Every appended reason goes to LAST_DEGRADE, whose reader is the `refusal()`
    trail that `voidloom approvals` renders.
    """
    if key is not None and getattr(obj, "unread", None) is not None:
        try:
            _un = tuple(str(x) for x in getattr(obj, "unread"))
        except TypeError:
            _un = None
        if _un is not None:
            if key not in _un:
                return True, None
            why = (f"the {key} source was not opened, so the {key} count is "
                   f"UNKNOWN, not zero")
            LAST_DEGRADE.append(f"{where}: {why}")
            return False, why
    if hasattr(obj, "trusted"):
        if bool(obj.trusted):
            return True, None
        why = ("the decision ledger answered but reported its own answer "
               "UNTRUSTED (the self-eval source is absent or unreadable), so "
               "the count is UNKNOWN, not zero")
        LAST_DEGRADE.append(f"{where}: {why}")
        return False, why
    why = ("the installed decision_units.py predates the trust vocabulary (its "
           "result carries no `trusted` field), so whether the count was "
           "MEASURED cannot be determined; it is UNKNOWN, not zero")
    LAST_DEGRADE.append(f"{where}: {why}")
    return False, why


def read_pending_count_ex(db: Optional[str] = None) -> Tuple[int, bool]:
    """``(count, trusted)`` -- the self-eval pending count AND whether it is KNOWN.

    THE DISTINCTION THIS RETURN TYPE EXISTS FOR:
      * ``(0, True)``  -- I looked, and there is nothing.
      * ``(0, False)`` -- I could not tell.

    Collapsing those two onto a bare ``0`` is the R2 defect in one line. It is
    what let an absent module read as an empty inbox, take the banner to zero
    bytes, and rewrite `.voidloom_notify_state.json` from 1 to 0 on the strength
    of a number nobody computed. ANY caller that writes durable state MUST branch
    on ``trusted``; a caller that only renders may use `read_pending_count` and
    keep the historical ``int`` signature.
    """
    db = db or resolve_db()
    if _DU is not None:
        try:
            _cr = _DU.unified_open_count(db)
            # R5/C1 -- THE LITERAL IS GONE. `True` here was a hardcoded claim
            # that the ledger had looked. It had not: on an installer-fresh
            # brain `unified_open_count` returns CountResult(trusted=False) and
            # this line overwrote that refusal with the word True.
            _tr, _ = _ledger_trust(_cr, "read_pending_count_ex",
                                   key="self_eval")
            return int(_cr.breakdown["self_eval"]), _tr
        except (sqlite3.Error, OSError, KeyError, ValueError, TypeError) as e:
            LAST_DEGRADE.append(f"read_pending_count: {type(e).__name__}: {e}")
            return 0, False
    ids = legacy_self_eval_report_ids(db)
    if ids is None:
        LAST_DEGRADE.append(
            f"read_pending_count: decision_units unavailable "
            f"({_DU_IMPORT_ERROR}) AND the historical query could not be read "
            f"either; the count is UNKNOWN and is reported as 0 with trusted=False")
        return 0, False
    LAST_DEGRADE.append(
        f"read_pending_count: decision_units unavailable ({_DU_IMPORT_ERROR}); "
        f"answered from the historical contract "
        f"[{_LEGACY_SELF_EVAL_IDS_SQL}] -> {len(ids)} report(s) {sorted(ids)}")
    return len(ids), True


def read_pending_count(db: Optional[str] = None) -> int:
    """Count fresh, never-touched self-eval reports awaiting Drew.

    THE STABLE CONTRACT (shared verbatim with Loop C `approvals`): the number of
    self-eval reports awaiting Drew. It is obtained from THE ONE COUNT FUNCTION,
    decision_units.unified_open_count(...).breakdown['self_eval'] — this module
    no longer carries the query. Returns 0 on any failure, so the banner simply
    stays silent about approvals; the reason is appended to LAST_DEGRADE.

    NOTE (Unified-Approval-Surface): this remains the SELF-EVAL-only count and
    keeps its exact historical contract, so any caller that depended on it (e.g.
    build_notification_text) is unchanged. The unified count lives in
    read_unified_pending(); the banner uses the unified count for its flag.
    """
    # R10 still holds: when the ledger is IMPORTABLE this file asks it and keeps
    # no predicate of its own. R2 adds the only case R10 could not cover -- the
    # ledger module is not importable AT ALL, so there is no one function to
    # defer to and `decision_units._legacy_self_eval_report_ids` is unreachable.
    # ONE spelling of that case lives in `legacy_self_eval_report_ids` above and
    # is reached through `read_pending_count_ex`, which is the same code path.
    return read_pending_count_ex(db)[0]


def read_belief_pending_batch_count_ex(db: Optional[str] = None) -> Tuple[int, bool]:
    """``(batches, trusted)`` -- the belief batch count AND whether it is KNOWN.

    R3 -- THE SECOND PRODUCE SITE, and it had no ``_ex`` form at all. R2 built
    the distinction for the self-eval source and left this one collapsed, so a
    unified reader that trusted its self-eval bit was still summing an untrusted
    belief zero. The class is the SOURCE, not the one member that was measured:
    every reader that can fail to read must be able to say which of the two
    zeros it is returning. Same three-way contract as
    ``legacy_belief_batch_count``: a table that is absent is a KNOWN zero (the
    belief substrate is optional); a database that will not open is UNKNOWN.
    """
    db = db or resolve_db()
    if _DU is not None:
        try:
            _cr = _DU.unified_open_count(db)
            # R5/C1, SECOND MEMBER OF THE SAME CLASS. The identical literal sat
            # here. It is NOT excused by "the belief substrate is optional, so an
            # absent table is a KNOWN zero": on the branch that produces
            # trusted=False, `unified_open_count` returns the LEGACY shape, whose
            # breakdown hardcodes `"belief": 0` without reading edge_proposals at
            # all. The 0 is not a measurement of belief; it is a placeholder.
            _tr, _ = _ledger_trust(_cr, "read_belief_pending_batch_count_ex",
                                   key="belief")
            return int(_cr.breakdown["belief"]), _tr
        except (sqlite3.Error, OSError, KeyError, ValueError, TypeError) as e:
            LAST_DEGRADE.append(f"read_belief_pending_batch_count: "
                                f"{type(e).__name__}: {e}")
            return 0, False
    m = legacy_belief_batch_count(db)
    if m is None:
        LAST_DEGRADE.append(
            f"read_belief_pending_batch_count: decision_units unavailable "
            f"({_DU_IMPORT_ERROR}) AND the historical query could not be read "
            f"either; the batch count is UNKNOWN and is reported as 0 with "
            f"trusted=False")
        return 0, False
    LAST_DEGRADE.append(
        f"read_belief_pending_batch_count: decision_units unavailable "
        f"({_DU_IMPORT_ERROR}); answered from the historical contract "
        f"[{_LEGACY_BELIEF_BATCH_SQL}] -> {m} batch(es)")
    return m, True


def read_belief_pending_batch_count(db: Optional[str] = None) -> int:
    """Count DISTINCT pending belief edge_proposals BATCHES (the banner unit).

    THE L5-D BATCHING CONTRACT: a nightly belief sweep emits its N proposals
    under ONE report_batch string, so the banner increments by 1 per batch, not
    by N proposals (anti-alarm-fatigue). That subject — one unit per distinct
    pending report_batch — is computed once, in decision_units, and read from
    breakdown['belief'] here.

    Returns 0 on ANY failure (table absent — the belief substrate may not be
    migrated; db locked; etc.). Read-only. Independent of the self-eval source:
    if the self-eval source errors, this still counts, and vice-versa
    (per-source fail-graceful, enforced inside decision_units.open_units).

    R10: DELEGATED. This was a full independent implementation of the batching
    subject that decision_units._batch_open_units already computes -- the fourth
    live count spelling, and reachable from read_unified_pending's fallback, so a
    surface could disagree with the one function without anything saying so.

    R3: the HISTORICAL int signature, kept verbatim for callers that only render,
    and now a projection of `read_belief_pending_batch_count_ex` rather than a
    second copy of the same query. A caller that WRITES durable state, or that
    RENDERS a count to a human, must use the `_ex` form and branch on `trusted`.
    """
    return read_belief_pending_batch_count_ex(db)[0]


#: The zero shape a refused read hands on, DERIVED (R8/B1 + R8/B2).
#:
#: WHAT STOOD HERE WAS A PROVENANCE CLAIM naming the count function as this
#: dict's origin, above a dict literal that consulted nothing -- the statement
#: it described contained no `getattr` and no call. The claim is described, not
#: quoted: re-uttering a repudiated sentence ships it again in the same bytes.
#:
#: R8/B2. That literal was the copy of this vocabulary with NO guard of any
#: kind. The copy inside `_breakdown_keys` above sits behind a `getattr`, and
#: its fallback is DEAD in the fully-installed tree -- there `_DU is None` is
#: FALSE, so the lookup succeeds and the literal is never evaluated. This one
#: had no such guard: it is consumed unconditionally by `read_unified_pending`'s
#: except arm below, in that same fully-installed tree. It is NOT
#: removed: the except arm below hands this dict on as the `breakdown` a
#: surface renders, and an EMPTY breakdown renders wrong -- which is graded, by
#: B2-NONEMPTY-WITHOUT-DECISION-UNITS, an import of this module with
#: decision_units absent that FAILS if the dict comes back empty. It is
#: BOUND: the keys are a comprehension over `_breakdown_keys()`, so a fourth key
#: in `decision_units.BREAKDOWN_KEYS` reaches this dict instead of bypassing it.
#: Graded by B2-BOUND in `_canaryR/R8/prove_R8.py`, which imports this module
#: beside a stub declaring a fourth key and FAILS if this dict still has three.
_BREAKDOWN_ZERO = {k: 0 for k in _breakdown_keys()}


def unified_pending_report(db: Optional[str] = None) -> Dict[str, Any]:
    """ONE read of the pending question, carrying its own REFUSAL.

    R3 -- WHY THIS EXISTS, AND WHY IT IS A DICT. R2 created the distinction
    ``(0, True)`` = "I looked and there is nothing" vs ``(0, False)`` = "I could
    not tell" at `read_pending_count_ex` and then DISCARDED it one frame later,
    at `read_unified_pending`, which summed two collapsed ints. Every surface
    downstream -- the banner, the desktop toast, the `voidloom approvals`
    envelope -- therefore received a zero it could not interrogate, and the
    banner's appearance gate blanked the screen on it. A trust bit dies at the
    FIRST unconverted consumer, and a renderer that names its output keys IS a
    consumer.

    So the refusal is not a flag hung off a tuple; it is a first-class value with
    a reason, a source list and a spelling of what it is NOT. Returned keys:

      ``total``               int  -- 0 when unknown. NEVER render it bare.
      ``breakdown``           dict -- {self_eval, belief, governance}
      ``trusted``             bool -- True only if EVERY source was actually read
      ``unknown``             bool -- ``not trusted``; the word a surface prints
      ``unreadable_sources``  list -- which sources refused, by name
      ``reason``              str|None -- one scrubbed sentence, safe to render

    ONE SPELLING. Every consumer in this build asks this function rather than
    re-deriving trust, so a surface cannot silently fall behind the others
    (R10/R11's rule, applied to the refusal instead of to the count).
    """
    db = db or resolve_db()
    if _DU is not None:
        try:
            _cr = _DU.unified_open_count(db)
            total, breakdown = _cr.as_tuple()
            # R4/P2. `"trusted": True` WAS A LITERAL. The ledger answering
            # was treated as proof the answer was known, so every refusal
            # decision_units could express died on this line -- the R3 fix
            # built the vocabulary and this branch never spoke it.
            # R5/C1. WAS `bool(getattr(_cr, "trusted", True))`. The default
            # arm fires on the STALE tree shape -- a decision_units.py without
            # the trust field -- and defaulted to TRUSTED there. One spelling
            # now, and its default is refusal.
            _tr, _ = _ledger_trust(_cr, "unified_pending_report")
            if _tr:
                return {"total": int(total), "breakdown": dict(breakdown),
                        "trusted": True, "unknown": False,
                        "unreadable_sources": [], "reason": None}
            # R6/U3 -- THE MEMBER NO ROUND NAMED, AND EVERY PROPOSED FIX ARMS.
            # This return used to publish `total: 0`, a ZEROED breakdown and a
            # HARDCODED `["self_eval"]`. Three separate lies in four lines, and
            # all three fire in exactly the world R6/U1+U2 create: with
            # `governance_proposals` dark and self-eval read, it threw away two
            # correct counts and blamed the source that had answered.
            #
            # It is unreachable on the R5 head -- the branch that produces
            # `trusted=False` there also produces a self-eval-only refusal --
            # which is why five rounds of world-state matrices never lit it.
            # The composition makes a PARTIAL refusal reachable for the first
            # time, and a partial refusal is precisely what this code cannot
            # express.
            _unread = _cr_unread(_cr)
            LAST_DEGRADE.append(
                "unified_pending_report: the decision ledger answered but "
                "reported " + ", ".join(_unread) + " UNREAD; the counts it DID "
                "read are carried, and the total is a LOWER BOUND")
            return {"total": int(total), "breakdown": dict(breakdown),
                    "trusted": False, "unknown": True,
                    "unreadable_sources": list(_unread),
                    "reason": ("the " + ", ".join(_unread) + " source(s) could "
                               "not be read (absent or unreadable), so the "
                               "count is AT LEAST " + str(int(total)) + " and "
                               "the true total is UNKNOWN")}
        except (sqlite3.Error, OSError, KeyError, ValueError, TypeError) as e:
            # R3 -- THIS BRANCH USED TO PRODUCE A SILENT ZERO TOO. The ledger
            # being importable but unable to answer is the same world-state as
            # the ledger being absent and the brain unreadable: I could not tell.
            # Fixing only the branch that was measured would leave the class open
            # in the configuration the build is migrating TOWARDS.
            LAST_DEGRADE.append(f"read_unified_pending: {type(e).__name__}: {e}")
            return {"total": 0, "breakdown": dict(_BREAKDOWN_ZERO),
                    "trusted": False, "unknown": True,
                    "unreadable_sources": ["decision_units"],
                    "reason": f"the decision ledger raised "
                              f"{type(e).__name__} while answering"}
    # R2 -- THE FALLBACK NOW REACHES THE HISTORICAL READERS, which is what this
    # branch always intended and never did: both sub-readers returned a flat 0
    # when the module was absent, so this line summed two zeros and the banner
    # went dark. It sums the historical contract instead.
    #
    # GOVERNANCE IS 0 BY THE HISTORICAL CONTRACT, NOT BY MEASUREMENT, and that is
    # stated rather than hidden: the pre-R10 banner had no governance term at all,
    # so inventing a fourth query here would be inventing behaviour rather than
    # restoring it. `governance_proposals` holds 0 pending rows on the live brain
    # (measured 2026-09-03); if that ever changes, this path under-counts by that
    # amount and this note is where a reader finds out.
    LAST_DEGRADE.append(f"read_unified_pending: decision_units unavailable "
                        f"({_DU_IMPORT_ERROR}); falling back to the historical "
                        f"self-eval + belief readers, governance term = 0 (the "
                        f"historical contract carries no governance source)")
    n, t_self = read_pending_count_ex(db)
    m, t_belief = read_belief_pending_batch_count_ex(db)
    n = n if isinstance(n, int) and n > 0 else 0
    m = m if isinstance(m, int) and m > 0 else 0
    unreadable = ([] if t_self else ["self_eval"]) + ([] if t_belief else ["belief"])
    trusted = bool(t_self and t_belief)
    reason = None
    if not trusted:
        # The database path is memory-adjacent text on a terminal-bound surface,
        # so it goes through the same scrubber every other rendered string uses.
        reason = ("the brain could not be read (" + ", ".join(unreadable)
                  + ") at " + _scrub(Path(db).name, 40, ascii_only=True))
    # R6 -- THE SAME CLASS AS U1, ON THE FALLBACK PATH, AND R5 INTRODUCED IT.
    # This dict used to carry `"governance": 0` beside `unreadable_sources: []`
    # -- a term published for a table this path never queries, asserted as a
    # measurement. The INSTALLED tree publishes two keys here
    # ({'self_eval': 1, 'belief': 1}, measured), so the fabricated third key was
    # a regression R5 added, not a contract R6 is breaking. The historical
    # contract has no governance source; a source that was not read may not
    # contribute a term, so it contributes NO KEY. The reason is already on the
    # degrade trail eight lines above, where a reader finds it.
    return {"total": n + m, "breakdown": {"self_eval": n, "belief": m},
            "trusted": trusted, "unknown": not trusted,
            "unreadable_sources": unreadable, "reason": reason}


class _PartialTriple(tuple):
    """`(total, breakdown, trusted)` PLUS `.unread` -- see below."""

    def __new__(cls, items, unread=()):
        t = super().__new__(cls, tuple(items))
        t.unread = tuple(str(x) for x in (unread or ()))
        return t


def read_unified_pending_ex(db: Optional[str] = None
                            ) -> Tuple[int, Dict[str, int], bool]:
    """``(total, breakdown, trusted)`` -- the tuple `read_unified_pending` owed.

    THE ONE-LINE STATEMENT OF THE R3 DEFECT: `grep -n read_unified_pending_ex`
    returned nothing, so no caller COULD branch on trust even if it wanted to.
    Any caller that renders to a human, emits a machine envelope, or writes
    durable state must use this form. `read_unified_pending` below keeps the
    historical two-tuple for callers that genuinely only need arithmetic.
    """
    r = unified_pending_report(db)
    # R7/C2 -- THE CHANNEL RIDES ON THE OBJECT. Three call sites unpack this
    # positionally into three names; widening the tuple would break them at a
    # distance, and leaving it at three left every consumer structurally unable
    # to tell a partial from a total loss no matter how it was written. A tuple
    # subclass keeps all three unpacks working and carries `unread` beside them.
    return _PartialTriple((r["total"], r["breakdown"], r["trusted"]),
                          r.get("unreadable_sources") or ())


def refusal(db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """``None`` when the pending question is answerable; the REFUSAL when not.

    THE ONE SPELLING OF "I COULD NOT TELL", for every surface in the build. A
    surface renders this dict (or its `text`) instead of a count; it must never
    invent its own wording, because three surfaces wording it three ways is how
    a build ends up with a refusal that reaches one of them and not the others.
    """
    r = unified_pending_report(db)
    if r["trusted"]:
        return None
    return {
        "unknown": True,
        "reason": r["reason"],
        "unreadable_sources": list(r["unreadable_sources"]),
        "text": REFUSAL_TEXT,
        "advice": vr.APPROVALS_CMD,
        # Said explicitly because the whole defect was a consumer reading the
        # accompanying 0 as a measurement.
        "count_is_not_zero": True,
        # R3 -- LAST_DEGRADE'S FIRST READER, AND IT IS ELEVEN WEEKS LATE. Three
        # docstrings in this file claim a degradation is "RECORDED, never
        # swallowed"; measured 2026-09-03, eleven appends fired during a single
        # render against an unreadable brain and NOTHING anywhere in the shipped
        # configuration ever read the list -- `grep -n LAST_DEGRADE` returns
        # appends, comments and the declaration, and no consumer. An accumulator
        # with no reader is not a record; it is a variable. The trail rides on
        # the MACHINE surface (`voidloom approvals` puts this whole dict in its
        # envelope) and NOT on the banner, because eleven raw lines on Drew's
        # screen would be the anti-alarm-fatigue rule broken in the other
        # direction. Tail-capped: the last entries are the proximate ones.
        "degrade_trail": [str(x) for x in LAST_DEGRADE[-8:]],
    }


#: The exact sentence every surface prints when the count cannot be derived.
#: One string, so the banner, the toast and the `voidloom approvals` envelope
#: cannot drift into three different admissions of the same thing.
REFUSAL_TEXT = "I could not tell -- the pending count is UNKNOWN, not zero"


def read_unified_pending(db: Optional[str] = None) -> Tuple[int, Dict[str, int]]:
    """The unified pending count + a per-source breakdown for the banner.

    Returns (total, {"self_eval": n, "belief": m}) where:
      * n = pending self-eval REPORTS (1 per report, the Loop C contract).
      * m = pending belief BATCHES   (1 per report_batch, the L5-D contract).
      * total = n + m.

    PER-SOURCE FAIL-GRACEFUL: each sub-reader already returns 0 on any failure,
    so one source erroring contributes 0 while the other still counts. Both
    absent -> total 0 -> the banner flag is simply skipped. NEVER raises.

    R3 -- THIS SIGNATURE IS THE DEFECT, AND IT IS KEPT ONLY FOR ARITHMETIC. The
    two-tuple cannot say whether its zero was read or refused, and every caller
    that needed to know was silently handed the collapsed form. It is now a
    projection of `unified_pending_report` (ONE spelling), and every consumer in
    the ship set has been moved to `read_unified_pending_ex` / `refusal`.

    IF YOU ARE ABOUT TO CALL THIS FROM A NEW SURFACE: don't. A surface that
    renders, emits an envelope, or writes durable state needs the trust bit.
    """
    r = unified_pending_report(db)
    # R7/C1. The historical PAIR, unchanged for every caller that unpacks it,
    # with `.unread` riding beside it so this projection is no longer the one
    # surface in the build that cannot say which source went dark.
    return _PartialTriple((r["total"], r["breakdown"]),
                          r.get("unreadable_sources") or ())


def read_latest_pending_report(db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The single most recent self-eval report STILL HOLDING AN OPEN DECISION.

    self_eval_reports has no `title` column; we surface `month`, `id`,
    `created_at`. Returns None on any failure.

    SAME CLASS AS `_self_eval_items`: selecting on `status='pending'` after the
    ledger became authoritative. Reports #3 and #5 are stored 'enacted' while
    holding four undecided units, so the toast's "Self-eval for <month> landed."
    prefix could never name them.

    R23 -- FOUND BY DERIVATION, NOT BY THE ENUMERATION. Council #2 named six
    Class-A readers of `self_eval_reports.status` to retire; this was not one of
    them, and it is a seventh. It was found by scanning every `*.py` in the tree
    for a SQL string naming `self_eval_reports` and classifying each, which is
    the whole reason the domain is derived rather than listed: a hand-written
    enumeration of surfaces is the exact failure mode (`SURFACES` at
    test_decision_units.py:583) the ruling was written to end, and the ruling
    committed it. The selection now DELEGATES to the one set function; this file
    keeps no pending predicate of its own.
    """
    db = db or resolve_db()
    if _DU is None:
        # T4 -- THE TOAST KEEPS THE REPORT IDENTITY, not only the count. Returning
        # None here dropped "Self-eval for 2026-08 landed." from the notification
        # body, which is the identity Drew reads to know WHICH thing landed. With
        # the ledger absent there is no derived state to prefer, so the historical
        # selection is the honest one -- and it is the exact selection the live
        # file makes today.
        rep = legacy_latest_pending_report(db)
        LAST_DEGRADE.append(
            f"read_latest_pending_report: decision_units unavailable "
            f"({_DU_IMPORT_ERROR}); answered from the historical contract "
            f"[{_LEGACY_LATEST_REPORT_SQL}] -> "
            + (f"report #{rep['id']} ({rep['month']})" if rep
               else "no pending report"))
        return rep
    try:
        if not Path(db).exists():
            LAST_DEGRADE.append(
                f"read_latest_pending_report: no brain at {db}; this None is "
                f"UNKNOWN, not 'no pending report'")
            return None
        _rs = _DU.open_self_eval_report_ids(db)
        # R5/C5 (LIST layer). The old line read `.ids` off a trust-carrying
        # object and threw the bit away -- the RENDER/LIST shape the AST sweep
        # cannot see, because there is no absence branch here to find.
        _rs_tr, _ = _ledger_trust(_rs, "read_latest_pending_report")
        ids = list(_rs.ids)
        if not ids:
            if not _rs_tr:
                LAST_DEGRADE.append(
                    "read_latest_pending_report: the open-report set is UNKNOWN, "
                    "so 'no pending report' is a placeholder, not a reading")
            return None
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            row = conn.execute(
                "SELECT id, month, created_at FROM self_eval_reports "
                "WHERE id IN (%s) ORDER BY created_at DESC, id DESC LIMIT 1"
                % ",".join("?" * len(ids)), ids
            ).fetchone()
            if not row:
                return None
            return {"id": row[0], "month": row[1], "created_at": row[2]}
        finally:
            conn.close()
    except Exception:
        return None


def read_anticipated(limit: int = 2, cache: Optional[str] = None) -> List[Dict[str, Any]]:
    """Top anticipated memories from the pre-cognitive CACHE (NO model load).

    The cron (`anticipate --log`) writes this cache; we only read it. Each entry
    is a dict with at least `id` (and usually `summary`). Returns [] if the
    cache is missing, malformed, or stale beyond a sane TTL. The session-start
    hook NEVER computes a focus vector — that would load the embedding model.
    """
    cache = cache or cache_path()
    try:
        p = Path(cache)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        mems = data.get("memories", [])
        # Defensive TTL: ignore a cache older than its declared max_age (default
        # 24h). A stale cache should not present yesterday's guesses as fresh.
        ttl_h = float(data.get("ttl_hours", 24) or 24)
        gen = data.get("generated_at")
        if gen:
            try:
                gen_dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
                if gen_dt.tzinfo is None:
                    gen_dt = gen_dt.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600.0
                if age_h > ttl_h:
                    return []
            except Exception:
                pass  # unparseable timestamp -> just use what's there
        out: List[Dict[str, Any]] = []
        for m in mems[:limit]:
            if isinstance(m, dict) and "id" in m:
                out.append({"id": m["id"], "summary": (m.get("summary") or "")})
        return out
    except Exception:
        return []


def read_forgotten_foundation(db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The single most-overdue forgotten foundation (cheap SQL JOIN, NO model).

    Uses anti_recency.get_forgotten_foundations(conn, limit=1). Returns a dict
    with `memory_id`, `summary`, and `days_since` (derived from last_accessed),
    or None if anti_recency is unavailable / the digest is empty.
    """
    db = db or resolve_db()
    if anti_recency is None:
        return None
    try:
        if not Path(db).exists():
            return None
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = anti_recency.get_forgotten_foundations(conn, limit=1)
        finally:
            conn.close()
        if not rows:
            return None
        f = rows[0]
        days = _days_since(f.get("last_accessed"))
        return {
            "memory_id": f.get("memory_id"),
            "summary": f.get("summary") or "",
            "days_since": days,
        }
    except Exception:
        return None


def _days_since(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Approvals line composer — shared by the colored banner and the plain
# fallback so the wording stays identical in both. Pure string assembly; the
# COUNT is passed in (never re-queried here). NEVER raises.
# ---------------------------------------------------------------------------


def _approvals_breakdown_text(breakdown: Dict[str, int],
                              unread: Sequence[str] = (),
                              sep: str = " · ") -> str:
    """The ' (2 self-eval · 1 belief)' tail, shown ONLY when BOTH sources > 0.

    When only one source is non-zero we keep the clean historical wording (no
    tail) so the common single-source case looks exactly as it did before.
    Returns '' when a tail is not warranted.

    R6/D4 -- THE NAMED HOLE. When `unread` is non-empty the tail names EVERY
    source in BREAKDOWN_KEYS order and renders the WORD `UNREAD` for a source
    that was never opened, never its slot's 0. The 0 sitting in `breakdown` for
    such a source is a SHAPE SLOT, not a measurement, and this is the surface
    where that distinction either survives or dies. The reader needs the shape
    of the hole, not only the parts that happened to be greater than zero, so
    the ">0 filter" is suspended in this mode -- `0 governance` beside
    `belief UNREAD` is the sentence saying which zero was measured.

    `sep` exists so the ASCII fallback renders the SAME clauses with an
    ASCII separator instead of spelling the tail a second time.
    """
    _keys = _breakdown_keys()
    _un = set(str(x) for x in (unread or ()))
    if _un:
        segs = []
        for key in _keys:
            label = key.replace("_", "-")
            if key in _un:
                segs.append(f"{label} UNREAD")
            else:
                segs.append(f"{int(breakdown.get(key, 0) or 0)} {label}")
        return " (" + sep.join(segs) + ")"
    parts = [(key.replace("_", "-"), int(breakdown.get(key, 0) or 0))
             for key in _keys if key in breakdown]
    live = [(label, n) for label, n in parts if n > 0]
    if len(live) < 2:
        return ""
    return " (" + sep.join(f"{n} {label}" for label, n in live) + ")"


def _partial_head(n: int) -> str:
    """The count slot when SOME sources answered and some did not.

    `3+?` -- a lower bound with the uncertainty attached to it, which is the
    shortest true statement available. Never a bare `?` (that discards a count
    that WAS read), and never a bare `3` (that asserts a total nobody computed).
    """
    if n <= 0:
        return "0+?"
    return f"{n}+?"


def _partial_noun(n: int) -> str:
    if n <= 0:
        return "await"
    return "proposal await" if n == 1 else "proposals await"


def _all_unread(unread: Sequence[str]) -> bool:
    """True when NOTHING was read -- the only state that earns a bare `?`."""
    return set(str(x) for x in (unread or ())) >= set(_breakdown_keys())


# ---------------------------------------------------------------------------
# Banner composer — the canonical Sparse-Void mockup.
# ---------------------------------------------------------------------------


def build_banner(*, db: Optional[str] = None, cache: Optional[str] = None,
                 width: int = 64, seed: Optional[int] = None,
                 force_mode: Optional[str] = None,
                 renderer: "Optional[Any]" = None) -> str:
    """Compose the Voidloom session-start banner from cheap sources only.

    Layout (lines are skipped when their source is empty):

        <the LOCKUP — locked variant-B art: nested-diamond sigil + the 5-row
         block Voidloom wordmark with the deep-purple gradient, woven grey
         threads, and grey/white + dim interior stars>
        a living memory tapestry woven for AI agents
        ⚑ <N> await → voidloom approvals --tui (a self-eval · b belief)  (only if N>0;
                                                  the tail only when BOTH > 0)
        ✦ anticipated · #id #id   ◈ forgotten · #id (Nd)    (only if any source)

    The lockup degrades with the renderer (truecolor / ansi256 / mono). The
    dynamic status lines carry memory-derived ids, which are run through
    vr.sanitize_for_terminal before painting (the #6099 prerequisite for a safe
    systemMessage banner). Returns a multi-line string with NO trailing newline.
    NEVER raises.

    APPROVALS-ONLY APPEARANCE GATE (Smart Banner v1, supersedes #6023): the
    terminal banner is now a SIGNAL, not wallpaper. It appears ONLY when there
    are pending approvals to view — `read_unified_pending(db) > 0` (the same
    unified self-eval + belief-batch count the flag already uses). When that
    count is 0 we return "" so the banner does NOT reach the terminal at all.
    The anticipated/forgotten lines therefore stay PART of the banner WHEN it
    shows, but no longer TRIGGER it on their own — only pending approvals do.
    Fail-graceful: read_unified_pending returns 0 on ANY error, so a count
    failure simply keeps the banner silent (quiet-by-default is correct here);
    composition still NEVER raises. (The fancier dynamic triggers — a cron that
    did something, a dream record, broader self-eval — are DEFERRED, #6249.)
    """
    # APPEARANCE GATE — read the unified pending count exactly once, up front,
    # and reuse it for the approvals line below (single query). 0 pending
    # approvals -> empty banner -> the terminal stays silent (the
    # session_start_inject envelope already omits systemMessage for an empty
    # banner). read_unified_pending already returns 0 on any INTERNAL error;
    # the extra try here keeps build_banner's "NEVER raises" contract airtight
    # at the gate boundary too, so even a pathological reader failure degrades
    # to a quiet banner (quiet-by-default) rather than crashing the hook.
    # R12/R13 — THE APPEARANCE GATE IS NOW A SET DIFFERENCE OVER OPEN UNIT IDS,
    # not a count and not a delta over a count. A count cannot see a swap: close
    # one unit and open another between two reads and the cardinality is
    # invariant while the world changed (#13744, self-caught 2026-08-10). And the
    # quiet is per unit, so a snooze granted over a set never extends to a member
    # that joins later -- the live global snooze runs to 2026-10-01 and report #7
    # arrived eight hours after it was set.
    #
    # Reading the signal does NOT consume it: attention_signal opens the database
    # read-only and has no write path at all. Acknowledgement is an explicit verb.
    #
    # R3 -- UNKNOWN IS NOT EMPTY, AND THIS GATE IS WHERE THAT SENTENCE IS
    # ENFORCED OR LOST. Measured 2026-09-03 on a copy of the live tree carrying
    # the R2 ship set, rendered with NO force_mode: healthy 2101 chars, corrupt
    # 0, absent 0. The chain was build_banner -> read_unified_pending -> the
    # collapsed int, and the `n <= 0` below then returned "" on a zero NOBODY
    # HAD COMPUTED. Fifteen LAST_DEGRADE entries accumulated during that render
    # and not one of them reached a surface.
    #
    # THE RULE: an UNTRUSTED zero may never reach the blanking test. A count that
    # could not be derived is a REASON TO SPEAK, not a reason to go quiet -- the
    # opposite of the quiet-by-default rule above, which applies only to a zero
    # that was actually read.
    unknown = False
    if _DU is not None:
        try:
            sig = _DU.attention_signal(db)
            n, breakdown, fire = sig.count, dict(sig.breakdown), sig.fire
            # R4/P1+P2 -- THE LINE THE BANNER ACTUALLY EXECUTES ON THE
            # TREE UNDER GRADE. `decision_units` is importable here, so
            # control has never reached the `read_unified_pending_ex`
            # fallback below that R3 taught to carry trust. Measured on
            # the installer's own output: fire=False, n=0, and the gate
            # 20 lines down returned "" -- a blank screen asserting an
            # empty inbox. `unknown` now enters from the same value the
            # count came from, so the two cannot disagree.
            # R5/C2. WAS `not bool(getattr(sig, "trusted", True))`. MEASURED:
            # on the STALE tree shape (a decision_units.py without the trust
            # field) this defaulted to trusted, `unknown` stayed False, the gate
            # 20 lines down returned "" -- and `build_notification_text` and the
            # session-start line, which route through `unified_pending_report`,
            # BOTH said UNKNOWN on the same brain at the same moment. Five
            # surfaces, one liar, a second time, in the shape nobody graded.
            _sig_tr, _ = _ledger_trust(sig, "build_banner/attention")
            unknown = not _sig_tr
            if unknown:
                LAST_DEGRADE.append(
                    "build_banner/attention: the attention signal reported "
                    "its count UNTRUSTED; rendering the refusal instead of "
                    "a count")
                fire = True
        except (sqlite3.Error, OSError, KeyError, ValueError, TypeError) as e:
            LAST_DEGRADE.append(f"build_banner/attention: {type(e).__name__}: {e}")
            n, breakdown, trusted = read_unified_pending_ex(db)
            unknown = not trusted
            fire = n > 0 or unknown
    else:
        # T1 -- WITH THE LEDGER ABSENT THIS RENDERS THE HISTORICAL BANNER, NEVER
        # ZERO. `read_unified_pending` now reaches the historical readers, so `n`
        # is the same 1 the live file computes and `fire = n > 0` is the same
        # appearance rule the live file applies. The attention model (unseen /
        # snooze keys) is a ledger concept and simply does not exist on this path;
        # falling back to "fire when anything is pending" is the pre-R12 rule, not
        # an invented one.
        LAST_DEGRADE.append(f"build_banner: decision_units unavailable "
                            f"({_DU_IMPORT_ERROR}); the appearance gate falls "
                            f"back to the historical rule (fire when the "
                            f"historical pending count > 0)")
        n, breakdown, trusted = read_unified_pending_ex(db)
        unknown = not trusted
        fire = n > 0 or unknown
    if not unknown and (not fire or n <= 0):
        return ""

    # If the aesthetic module is missing, fall back to a plain ASCII-safe banner
    # so a hook still shows *something* coherent. (We are past the gate, so there
    # IS at least one pending approval for the fallback to surface.)
    if vr is None:
        return _plain_fallback(db=db, cache=cache)

    r = renderer if renderer is not None else vr.Renderer(force_mode=force_mode)
    lines: List[str] = []

    # 1-2. The LOCKUP — the locked variant-B art (sigil + Voidloom wordmark with
    #      the deep-purple gradient, woven grey threads, and stars). It replaces
    #      the old starfield + sigil + wordmark trio with the one approved
    #      composition. It degrades with the renderer (truecolor/ansi256/mono)
    #      and carries NO memory content (no sanitizing needed for the art).
    if hasattr(r, "lockup"):
        for _ln in r.lockup().split("\n"):
            lines.append("  " + _ln)
    else:  # defensive: older void_render without lockup -> keep the classic head
        lines.append("    " + r.sigil() + "   " + r.wordmark())

    # 3. The tagline, quiet beneath the mark.
    lines.append("  " + r.tagline())

    # 4. Approvals line. The UNIFIED count folds self-eval reports + belief
    #    batches; the per-source breakdown is shown as a tiny tail only when
    #    both contribute. n/breakdown were read ONCE at the appearance gate
    #    above and are reused here (no second query). Past the gate n is always
    #    > 0; the guard is kept defensively.
    if unknown:
        # THE SURFACE THAT CAN SAY "I COULD NOT TELL". It occupies the count
        # line's slot deliberately: a `?` where the number goes is the shortest
        # true statement available, and it keeps the verb Drew types in exactly
        # the place he already reads it from. It does NOT print `0`, because a
        # rendered 0 here is the original defect wearing the fix's clothes.
        _ref = refusal(db) or {}
        _unread = tuple(_ref.get("unreadable_sources") or ())
        if _all_unread(_unread) or (n <= 0 and not _unread):
            # NOTHING was read. The bare refusal is the whole truth here.
            lines.append(
                "  " + r.violet(vr.GLYPH_PENNANT) + " "
                + r.accent("?", bold=True) + r.dim(" await ")
                + r.violet("→ " + vr.APPROVALS_CMD)
            )
            lines.append("  " + r.dim(REFUSAL_TEXT))
        else:
            # R6/D4 -- SHOW THE COUNT AND NAME THE UNREAD SOURCE. NEVER A BARE
            # REFUSAL when some source answered. Ruled by the coordinator at
            # Council #6: the INSTALLED tree renders the correct count on a
            # legacy brain today, so replacing it with `?` would regress what
            # works -- exactly the error the Council convened to find. A partial
            # answer is rendered as a partial answer.
            lines.append(
                "  " + r.violet(vr.GLYPH_PENNANT) + " "
                + r.accent(_partial_head(n), bold=True)
                + r.dim(" " + _partial_noun(n) + " ")
                + r.violet("→ " + vr.APPROVALS_CMD)
                + r.dim(_approvals_breakdown_text(breakdown, _unread))
            )
        _why = _ref.get("reason")
        if _why:
            lines.append("  " + r.dim("· " + vr.sanitize_for_terminal(_why)))
    elif n > 0:
        noun = "proposal" if n == 1 else "proposals"
        tail = _approvals_breakdown_text(breakdown)
        lines.append(
            "  " + r.violet(vr.GLYPH_PENNANT) + " "
            + r.accent(str(n), bold=True) + r.dim(f" {noun} await ")
            + r.violet("→ " + vr.APPROVALS_CMD)
            + (r.dim(tail) if tail else "")
        )

    # 4b. THE PROOF-LINE (Tier-R R1). The count above says HOW MANY; these
    #     clauses say WHAT, and each one is a query. This is the line the whole
    #     unit exists for: without it `1` is arithmetically right and
    #     semantically empty, and an enacted proposal reads exactly like one the
    #     tool destroyed. Every string is already sanitized inside
    #     build_proof_line (memory-derived titles, #6099), and that function
    #     NEVER raises and NEVER writes -- so the worst case here is a shorter
    #     banner, never a broken session start.
    try:
        _proof = build_proof_line(db=db)
    except Exception as _e:  # pragma: no cover - build_proof_line is total
        LAST_DEGRADE.append(f"build_banner/proof: {type(_e).__name__}: {_e}")
        _proof = ""
    if _proof:
        for _ln in _proof.split("\n"):
            lines.append("  " + r.dim(_ln))

    # 5. Anticipated + forgotten line (only when at least one source has data).
    #    These ids are MEMORY-DERIVED (anticipate cache + anti_recency JOIN), so
    #    every such string is run through vr.sanitize_for_terminal before it
    #    enters the painted line — neutralizing any OSC/CSI/control injection in
    #    the memory content before it can reach the systemMessage channel
    #    (#6099). days_since is gated to int, so it needs no sanitizing.
    anticipated = read_anticipated(limit=2, cache=cache)
    forgotten = read_forgotten_foundation(db)
    if anticipated or forgotten:
        segs: List[str] = []
        if anticipated:
            ids = vr.sanitize_for_terminal(
                " ".join(f"#{m['id']}" for m in anticipated))
            segs.append(r.accent(vr.GLYPH_STAR_FILLED) + r.dim(" anticipated · ")
                        + r.accent(ids))
        if forgotten and forgotten.get("memory_id") is not None:
            d = forgotten.get("days_since")
            dtxt = f" ({d}d)" if isinstance(d, int) else ""
            mid = vr.sanitize_for_terminal(f"#{forgotten['memory_id']}")
            segs.append(r.accent(vr.GLYPH_LOZENGE) + r.dim(" forgotten · ")
                        + r.accent(mid) + r.dim(dtxt))
        lines.append("  " + r.dim("   ").join(segs))

    return "\n".join(lines)


def _plain_fallback(*, db: Optional[str] = None, cache: Optional[str] = None) -> str:
    """ASCII-safe, color-free banner if void_render is unavailable."""
    out = ["  Voidloom", "  a living memory tapestry woven for AI agents"]
    n, breakdown, trusted = read_unified_pending_ex(db)
    if not trusted:
        # R3 -- THE ASCII PATH ADMITS IT TOO. A fallback that drops the refusal
        # is a fallback that quietly restores the founding complaint on the one
        # surface least likely to be looked at.
        _ref = refusal(db) or {}
        _unread = tuple(_ref.get("unreadable_sources") or ())
        if _all_unread(_unread) or (n <= 0 and not _unread):
            out.append("  ? await -> " + vr.APPROVALS_CMD)
            out.append("  " + REFUSAL_TEXT)
        else:
            # R6/D4, IN ASCII. A fallback that drops the partial count is a
            # fallback that restores the bare refusal on the one surface least
            # likely to be looked at.
            out.append("  " + _partial_head(n) + " " + _partial_noun(n)
                       + " -> " + vr.APPROVALS_CMD
                       + _approvals_breakdown_text(breakdown, _unread, sep=" - "))
        if _ref.get("reason"):
            out.append("  · " + str(_ref["reason"]))
    elif n > 0:
        noun = "proposal" if n == 1 else "proposals"
        # R6/R11. This spelled the tail a SECOND time, two-keyed and hardcoded,
        # while every other surface delegated to the shared helper. A surface
        # with its own copy of the shape is a surface that falls behind it.
        tail = _approvals_breakdown_text(breakdown, sep=" - ")
        out.append(f"  {n} {noun} await -> {vr.APPROVALS_CMD}{tail}")
    # The plain path carries the SAME clauses in ASCII. A fallback that drops
    # the evidence is a fallback that quietly restores the founding complaint.
    proof = build_proof_line(db=db, ascii_only=True, indent="  ")
    if proof:
        out.extend(proof.split("\n"))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Smaller hook surfaces (PreCompact / Stop / Notification body).
# ---------------------------------------------------------------------------


def build_precompact_line(*, memories_captured: Optional[int] = None,
                          pending_unknown: bool = False,
                          pending_total: Optional[int] = None,
                          pending_unread: Sequence[str] = (),
                          force_mode: Optional[str] = None,
                          renderer: "Optional[Any]" = None) -> str:
    """A calm one-line 'void-checkpoint' shown as a context boundary nears.

    States, quietly, what the brain carries across the boundary. If the count
    of memories captured this session is unknown, it still emits a calm marker.

    R3 -- `pending_unknown` IS A NEW KEYWORD WITH A DEFAULT, so every existing
    caller is unchanged. It exists because `hooks/voidloom_precompact.py` is the
    FOURTH shipped surface of the false-zero class -- a registered PreCompact
    hook (settings.json), gating this line on `read_unified_pending(DB) > 0`,
    which meant an unreadable brain silenced the void-checkpoint by the same
    collapse that blanked the session-start banner. The hook now passes the
    trust bit down instead of swallowing it.
    """
    # R7/C2 -- TWO NEW KEYWORDS WITH DEFAULTS, so every existing caller is
    # unchanged. A PARTIAL read is not `pending_unknown`: some source answered,
    # and this line said nothing about either the count or the hole.
    _pun = tuple(str(x) for x in (pending_unread or ()))
    _ptail = ""
    if _pun:
        _ptail = (" · " + _partial_head(int(pending_total or 0)) + " pending · "
                  + ", ".join(_pun) + " UNREAD")
    if vr is None:
        n = memories_captured
        tail = f" — {n} memories woven in" if isinstance(n, int) else ""
        unk = f" · {REFUSAL_TEXT}" if pending_unknown else ""
        return f"Voidloom · void-checkpoint{tail}{unk}{_ptail}"
    r = renderer if renderer is not None else vr.Renderer(force_mode=force_mode)
    base = r.sigil() + " " + r.accent("void-checkpoint")
    if _pun:
        base += r.chrome(" · ") + r.violet(vr.GLYPH_PENNANT) + " " \
            + r.accent(_partial_head(int(pending_total or 0)), bold=True) \
            + r.dim(" pending · " + ", ".join(_pun) + " UNREAD")
    if pending_unknown:
        base += r.chrome(" · ") + r.violet(vr.GLYPH_PENNANT) + " " \
            + r.accent("?", bold=True) + r.dim(" · " + REFUSAL_TEXT)
    if isinstance(memories_captured, int):
        woven = "memory" if memories_captured == 1 else "memories"
        base += r.chrome(" · ") + r.accent(str(memories_captured), bold=True) \
            + r.dim(f" {woven} woven into the tapestry this session")
    else:
        base += r.dim(" · the tapestry holds")
    return "  " + base


def build_signoff_line(*, force_mode: Optional[str] = None,
                       renderer: "Optional[Any]" = None) -> str:
    """A quiet sign-off: sigil + Voidloom, one fading star."""
    if vr is None:
        return "Voidloom ·"
    r = renderer if renderer is not None else vr.Renderer(force_mode=force_mode)
    return "  " + r.sigil() + " " + r.wordmark(spaced=False) + " " \
        + r.star(vr.GLYPH_FADING_STAR)


def build_notification_text(db: Optional[str] = None) -> Optional[str]:
    """Plain (no-ANSI) notification body for `notify-send` when fresh
    proposals (self-eval and/or belief) land. Returns None when nothing pends.

    notify-send shows in a desktop popup, so this is intentionally color-free.
    The unified count drives the popup so a belief-only batch is announced too.
    """
    _t3 = read_unified_pending_ex(db)
    n, breakdown, trusted = _t3
    _unread = tuple(getattr(_t3, "unread", ()) or ())
    # R7/C2 -- THE DESKTOP TOAST, IN THE FILE THAT IMPLEMENTS [D4] TWO HUNDRED
    # LINES ABOVE, USING A HELPER THIS BUILD TAUGHT TO RENDER `unread` AND NOT
    # PASSING IT. Measured 2026-09-04, same module, same process, same instant:
    # `build_banner()` rendered
    #   "1+? proposal await -> voidloom approvals (1 self-eval - 0 belief -
    #    governance UNREAD)"
    # while `build_notification_text()` returned the bare "I could not tell".
    # Two surfaces of one module disagreeing about one brain is the defect
    # `conventions-014` says needs no reference tree to detect.
    if not trusted and (_all_unread(_unread) or (n <= 0 and not _unread)):
        # R3 -- `None` IS THIS SURFACE'S FALSE ZERO. The docstring says "returns
        # None when nothing pends", and an unreadable brain reached that return
        # by the same collapse the banner did: a caller cannot tell "nothing
        # pends" from "the question could not be asked". The body is the
        # refusal, and the DECISION to fire is left to the caller -- this
        # function renders, it does not debounce.
        _ref = refusal(db) or {}
        _why = _ref.get("reason")
        return (f"{REFUSAL_TEXT}. Run: {vr.APPROVALS_CMD}"
                + (f" -- {_why}" if _why else ""))
    if n <= 0 and not _unread:
        return None
    noun = "proposal set" if n == 1 else "proposal sets"
    nse = int(breakdown.get("self_eval", 0) or 0)
    # R11 -- SHAPE, NOT ONLY TOTAL. The desktop toast is a surface, and its tail
    # was hard-coded two-keyed (`self-eval, belief`) while every other surface
    # renders all three BREAKDOWN_KEYS. A governance-only or self-eval+governance
    # count would have rendered no tail at all here and a full one elsewhere --
    # agreeing in total, disagreeing in shape, which is exactly the measurement
    # R11 forbids. The shared helper renders every non-zero key in BREAKDOWN_KEYS
    # order and returns '' below two live keys: the behaviour this line intended.
    tail = _approvals_breakdown_text(breakdown, _unread)
    _head = _partial_head(n) if _unread else str(n)
    body = f"{_head} {noun}{tail} await your review — run: {vr.APPROVALS_CMD}"
    if nse > 0:
        latest = read_latest_pending_report(db)
        if latest and latest.get("month"):
            body = f"Self-eval for {latest['month']} landed. " + body
    return body


# ---------------------------------------------------------------------------
# THE PROOF-LINE + THE RECURRENCE CENSUS  (TIER-R ROUND 1, ruler VL-SURFACE-v1.0)
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. Six rounds graduated a correctness layer while Drew's banner
# still read `⚑ 1 proposal await`. A bare count is arithmetically right and
# semantically empty: it cannot say that four of the five proposals in front of
# him have been asked before, and it cannot tell a proposal that was enacted
# from one the tool destroyed. In report #7 `Activate L3-C2-provenance-graph`
# (approved and recorded enacted twice) and `Activate L3-C7-cross-tier-bridges`
# (recorded enacted once, then EATEN on 2026-08-02) read IDENTICALLY. That is
# the defect this surface exists to end.
#
# THE CONTRACT (each rule was paid for; none is decoration):
#
#   EVERY CLAUSE IS A QUERY.  A clause carries the SQL that produced it and the
#     values it read. Nothing is estimated, nothing is asserted from a docstring.
#     A clause whose source is unavailable is OMITTED WITH ITS REASON and the
#     reason is printed — never silently dropped, never rounded into a guess.
#
#   THE DOMAIN IS DERIVED, NEVER LISTED.  The recurrence set is computed from
#     `self_eval_reports.proposed_enhancements/proposed_deprecations` themselves.
#     A hand-maintained list of "interesting titles" is the exact failure this
#     build has already paid for twice (test_decision_units.py:583's `SURFACES`).
#
#   IT POINTS; IT NEVER PROMPTS.  The banner is a NON-TTY session-start hook
#     (`detect_mode()` returns 'none' in a pipe). A blocking read here breaks
#     cron, subagents and all ten systemd services. There is no input() below.
#
#   IT DEGRADES HONESTLY.  `decision_events` does not exist on the live brain
#     (migration 030 is absent). The surface SAYS what it cannot see rather than
#     showing a confident partial number.
#
#   ZERO WRITE PATHS.  Every connection is opened `mode=ro` AND carries an
#     authorizer that DENIES INSERT/UPDATE/DELETE and every DDL action. The
#     authorizer fires per column at prepare time and cannot be routed around by
#     spelling, a view, or SELECT *. Denials are recorded in DENIED_WRITES so
#     the property is provable from outside rather than believed.
#
#   IT DOES NOT COUNT WHAT AWAITS.  R10 stands: this module carries no pending
#     count of its own. The `N proposals await` line is still the ONE count
#     function's. The proof-line's subjects are the census, the dispositions and
#     the dispatch record — different questions, different tables.

#: Authorizer action codes that mutate. Superset of the harness's write set:
#: DML + every DDL verb + ATTACH (which could bring a writable database along).
#: Read verbs, PRAGMA, FUNCTION and TRANSACTION are NOT denied -- denying those
#: would break legitimate reads and prove nothing.
_WRITE_ACTIONS = frozenset(
    code for code in (
        getattr(sqlite3, _n, None) for _n in (
            "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE",
            "SQLITE_CREATE_TABLE", "SQLITE_DROP_TABLE",
            "SQLITE_CREATE_INDEX", "SQLITE_DROP_INDEX",
            "SQLITE_CREATE_TRIGGER", "SQLITE_DROP_TRIGGER",
            "SQLITE_CREATE_VIEW", "SQLITE_DROP_VIEW",
            "SQLITE_CREATE_VTABLE", "SQLITE_DROP_VTABLE",
            "SQLITE_CREATE_TEMP_TABLE", "SQLITE_DROP_TEMP_TABLE",
            "SQLITE_CREATE_TEMP_INDEX", "SQLITE_DROP_TEMP_INDEX",
            "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_DROP_TEMP_TRIGGER",
            "SQLITE_CREATE_TEMP_VIEW", "SQLITE_DROP_TEMP_VIEW",
            "SQLITE_ALTER_TABLE", "SQLITE_REINDEX",
            "SQLITE_ATTACH", "SQLITE_DETACH",
        )
    ) if code is not None
)

#: Every write action the authorizer refused, as (action_code, argument). Stays
#: EMPTY on every correct run; a non-empty list is the artifact confessing.
DENIED_WRITES: List[Tuple[int, Any]] = []


def _deny_writes(action: int, arg1: Any, arg2: Any, dbname: Any, trigger: Any) -> int:
    """sqlite3 authorizer: DENY every mutating action, allow every read."""
    if action in _WRITE_ACTIONS:
        DENIED_WRITES.append((action, arg1))
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _open_ro(db: str) -> sqlite3.Connection:
    """Open the brain READ-ONLY, with the deny-writes authorizer installed.

    Two independent guarantees, deliberately: `mode=ro` makes the file handle
    incapable of writing, and the authorizer refuses the statement before SQLite
    ever sees it. Either alone would do; both together mean a regression has to
    defeat two unrelated mechanisms to write a row.
    """
    p = Path(db)
    if not p.exists():
        # R3 -- THE MESSAGE NAMES THE FILE, NOT THE PATH. This string is rendered
        # verbatim by build_proof_line's omission reasons, so on an ABSENT brain
        # the banner carried the full absolute path FOUR TIMES -- ~400 characters
        # of directory, and an acceptance artifact whose bytes depend on WHERE the
        # tree is unpacked cannot be compared between two landings of the same
        # artifact set. That is not a cosmetic complaint: it is what blocked U4's
        # by-patch-vs-by-copy equality check. The full path still reaches the
        # diagnostic channel below, where a reader looking for it will find it.
        LAST_DEGRADE.append(f"_open_ro: no database at {db}")
        raise FileNotFoundError(f"no database at .../{p.name}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=1.0)
    conn.set_authorizer(_deny_writes)
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') "
        "AND name = ?", (name,)).fetchone()[0])


def _scrub(text: Any, limit: int = 44, ascii_only: bool = False) -> str:
    """Neutralize a MEMORY-DERIVED string before it can reach the terminal.

    Proposal titles are content the brain wrote; they travel to the
    systemMessage channel, so they get the #6099 treatment (OSC/CSI/control
    stripping) plus a hard newline/width bound so one hostile title cannot
    reshape the banner. NEVER raises.
    """
    s = "" if text is None else str(text)
    if vr is not None:
        try:
            s = vr.sanitize_for_terminal(s)
        except Exception:  # pragma: no cover - defensive
            pass
    s = "".join(ch for ch in s if ch == " " or (ch.isprintable() and ch != "﻿"))
    if ascii_only:
        s = s.encode("ascii", "replace").decode("ascii")
    s = s.strip()
    if limit and len(s) > limit:
        s = s[: max(1, limit - 1)] + ("~" if ascii_only else "…")
    return s


@dataclass(frozen=True)
class ProofClause:
    """One clause of the proof-line: its rendered lines, its SQL, its numbers."""
    key: str
    lines: Tuple[str, ...]
    source: str
    values: Dict[str, Any]


@dataclass(frozen=True)
class ProofOmission:
    """A clause that could NOT be sourced, and exactly why. It is never guessed."""
    key: str
    reason: str
    source: str


@dataclass(frozen=True)
class ProofLine:
    clauses: Tuple[ProofClause, ...]
    omissions: Tuple[ProofOmission, ...]

    def render(self, indent: str = "") -> str:
        out: List[str] = []
        for c in self.clauses:
            for ln in c.lines:
                out.append(indent + ln)
        return "\n".join(out)


# --- the two censuses -------------------------------------------------------


def recurrence_census(db: Optional[str] = None) -> Dict[str, List[int]]:
    """Titles proposed on MORE THAN ONE self-eval report -> the report ids.

    DERIVED DOMAIN: read out of the reports themselves. There is no list of
    interesting titles anywhere in this file, and there must never be one -- a
    maintained list is bounded by its author's suspicion, a derivation is bounded
    only by the data.

    Read-only. Raises on a missing table/database; every caller wraps it and
    turns the failure into a stated OMISSION rather than a zero.
    """
    db = db or resolve_db()
    conn = _open_ro(db)
    try:
        if not _table_exists(conn, "self_eval_reports"):
            raise sqlite3.OperationalError("no such table: self_eval_reports")
        seen: Dict[str, List[int]] = {}
        for rid, enh, dep in conn.execute(
                "SELECT id, proposed_enhancements, proposed_deprecations "
                "FROM self_eval_reports ORDER BY id"):
            for blob in (enh, dep):
                try:
                    items = json.loads(blob or "[]")
                except (TypeError, ValueError):
                    continue
                if not isinstance(items, list):
                    continue
                for p in items:
                    t = p.get("title") if isinstance(p, dict) else None
                    if t:
                        seen.setdefault(str(t), []).append(int(rid))
        return {t: rs for t, rs in seen.items() if len(rs) > 1}
    finally:
        conn.close()


def report_titles(db: Optional[str] = None, report_id: Optional[int] = None) -> List[str]:
    """Every proposal title on one report, in stored order. Read-only."""
    db = db or resolve_db()
    conn = _open_ro(db)
    try:
        row = conn.execute(
            "SELECT proposed_enhancements, proposed_deprecations "
            "FROM self_eval_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return []
        out: List[str] = []
        for blob in row:
            try:
                items = json.loads(blob or "[]")
            except (TypeError, ValueError):
                continue
            if isinstance(items, list):
                for p in items:
                    t = p.get("title") if isinstance(p, dict) else None
                    if t:
                        out.append(str(t))
        return out
    finally:
        conn.close()


def disposition_census(db: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Per proposal title: what the record says HAPPENED to it.

    Two columns, two different meanings, and keeping them apart is the whole
    point of this unit:

      * `self_eval_enacted_proposals.status='enacted'` -- a row written by the
        ENACTING path saying it ran. Council #4's finding stands: that row is
        written by the code whose success it attests, so it is evidence of a
        DISPATCH, not of a working feature.
      * `approval_audit_log.outcome_status='error'` -- the dispatch was EATEN.
        Drew's decision was made and the tool destroyed it.

    A title can carry both (L3-C7: enacted once in July, eaten in August). A
    surface that shows neither -- today's banner -- cannot tell them apart.
    """
    db = db or resolve_db()
    conn = _open_ro(db)
    try:
        out: Dict[str, Dict[str, Any]] = {}

        def slot(title: str) -> Dict[str, Any]:
            return out.setdefault(str(title), {
                "enacted_reports": [], "eaten": [], "dispatched_ok": []})

        if _table_exists(conn, "self_eval_enacted_proposals"):
            for title, rid in conn.execute(
                    "SELECT proposal_title, report_id FROM "
                    "self_eval_enacted_proposals WHERE status = 'enacted' "
                    "ORDER BY report_id, id"):
                slot(title)["enacted_reports"].append(int(rid))

        if _table_exists(conn, "approval_audit_log"):
            for aid, created, action, outcome, detail in conn.execute(
                    "SELECT id, created_at, action, outcome_status, decision_detail "
                    "FROM approval_audit_log ORDER BY id"):
                try:
                    titles = (json.loads(detail or "{}") or {}).get("titles") or []
                except (TypeError, ValueError):
                    titles = []
                if not isinstance(titles, list):
                    titles = []
                for t in titles:
                    if not t:
                        continue
                    rec = {"audit_id": int(aid),
                           "date": str(created or "")[:10],
                           "action": str(action or "")}
                    if outcome == "error":
                        slot(t)["eaten"].append(rec)
                    elif outcome == "ok":
                        slot(t)["dispatched_ok"].append(rec)
        return out
    finally:
        conn.close()


def dispatch_record(db: Optional[str] = None) -> Dict[str, Any]:
    """The approval dispatch record: how many decisions the tool actually kept.

    Also isolates the BUSIEST day, derived by grouping -- not a date anyone
    typed in. On the live brain that lands on 2026-08-02, where Drew issued four
    commands and three were eaten.
    """
    db = db or resolve_db()
    conn = _open_ro(db)
    try:
        if not _table_exists(conn, "approval_audit_log"):
            raise sqlite3.OperationalError("no such table: approval_audit_log")
        total, errors = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN outcome_status='error' THEN 1 ELSE 0 END) "
            "FROM approval_audit_log").fetchone()
        day = conn.execute(
            "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM approval_audit_log "
            "GROUP BY d ORDER BY n DESC, d DESC LIMIT 1").fetchone()
        busiest: Dict[str, Any] = {}
        if day:
            d = day[0]
            ok_ids, err_ids = [], []
            for aid, outcome in conn.execute(
                    "SELECT id, outcome_status FROM approval_audit_log "
                    "WHERE substr(created_at,1,10) = ? ORDER BY id", (d,)):
                (err_ids if outcome == "error" else ok_ids).append(int(aid))
            busiest = {"date": d, "rows": int(day[1]),
                       "ok_ids": ok_ids, "error_ids": err_ids}
        return {"total": int(total or 0), "errors": int(errors or 0),
                "busiest": busiest}
    finally:
        conn.close()


def newest_report(db: Optional[str] = None) -> Dict[str, Any]:
    """The newest self-eval report, and the two columns that say it is untouched.

    NOT a pending predicate -- R10 keeps that subject in the one count function.
    These are direct column reads about one identified row: its stored status,
    whether `drew_approved` was ever written, and how many approval_audit_log
    rows name it. Lens B's `4 pending · never reviewed`, made sourceable.
    """
    db = db or resolve_db()
    conn = _open_ro(db)
    try:
        if not _table_exists(conn, "self_eval_reports"):
            raise sqlite3.OperationalError("no such table: self_eval_reports")
        row = conn.execute(
            "SELECT id, month, created_at, status, drew_approved FROM "
            "self_eval_reports ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if not row:
            raise sqlite3.OperationalError("self_eval_reports holds 0 rows")
        rid, month, created, status, approved = row
        named = 0
        if _table_exists(conn, "approval_audit_log"):
            named = int(conn.execute(
                "SELECT COUNT(*) FROM approval_audit_log WHERE ref = ?",
                (f"self-eval:{int(rid)}",)).fetchone()[0])
        return {"id": int(rid), "month": str(month or ""),
                "created_at": str(created or "")[:16],
                "status": str(status or ""),
                "reviewed": approved is not None,
                "audit_rows_naming_it": named}
    finally:
        conn.close()


# --- the composer -----------------------------------------------------------


def proof_line_clauses(db: Optional[str] = None, *, max_titles: int = 5,
                       ascii_only: bool = False) -> ProofLine:
    """Build the proof-line as CLAUSES, each carrying its own source and values.

    Every clause is produced inside its own try/except: one unavailable source
    costs exactly one clause and appends a stated OMISSION. It never converts a
    failed read into a zero, and it never estimates a number it could not read.
    """
    db = db or resolve_db()
    clauses: List[ProofClause] = []
    omissions: List[ProofOmission] = []

    G = {"anchor": ">", "recur": "~", "warn": "!", "bad": "x", "blind": "o",
         "times": "x", "dot": " - ", "ell": "~"} if ascii_only else \
        {"anchor": "▸", "recur": "↻", "warn": "⚠", "bad": "✖", "blind": "○",
         "times": "×", "dot": " · ", "ell": "…"}
    DOT = G["dot"]

    # 1. ANCHOR -- which report the census is standing on, and its stored state.
    anchor: Dict[str, Any] = {}
    try:
        anchor = newest_report(db)
        rev = ("reviewed" if anchor["reviewed"] else "never reviewed")
        clauses.append(ProofClause(
            "anchor",
            (f"{G['anchor']} newest self-eval{DOT}report #{anchor['id']}{DOT}"
             f"{anchor['month']}{DOT}created {anchor['created_at']}{DOT}"
             f"stored status '{anchor['status']}'{DOT}{rev}{DOT}"
             f"named by {anchor['audit_rows_naming_it']} approval_audit_log rows",),
            "SELECT id,month,created_at,status,drew_approved FROM self_eval_reports "
            "ORDER BY created_at DESC,id DESC LIMIT 1  +  "
            "SELECT COUNT(*) FROM approval_audit_log WHERE ref='self-eval:<id>'",
            dict(anchor)))
    except Exception as e:
        omissions.append(ProofOmission(
            "anchor", f"{type(e).__name__}: {e}",
            "self_eval_reports.{id,month,created_at,status,drew_approved}"))

    # 2+3. RECURRENCE, each row carrying its DISPOSITION. S1 and S2 are one
    #      table because they are one question: has this been asked before, and
    #      what happened last time?
    try:
        census = recurrence_census(db)
        if not census:
            omissions.append(ProofOmission(
                "recurrence", "no title appears on more than one report",
                "self_eval_reports.proposed_enhancements/proposed_deprecations"))
        else:
            try:
                disp = disposition_census(db)
            except Exception as e:
                disp = {}
                omissions.append(ProofOmission(
                    "disposition", f"{type(e).__name__}: {e}",
                    "self_eval_enacted_proposals.status + approval_audit_log.outcome_status"))
            open_titles = report_titles(db, anchor["id"]) if anchor else []
            open_set = set(open_titles)
            recurring_open = [t for t in open_titles if t in census]

            order = sorted(census.items(),
                           key=lambda kv: (-len(kv[1]), -max(kv[1]), kv[0]))
            shown = order[:max_titles]
            width = max((len(_scrub(t, ascii_only=ascii_only)) for t, _ in shown),
                        default=0)
            # Column widths are MEASURED from the rows about to be printed, not
            # guessed: a ragged table is a table nobody scans, and scanning is
            # the entire mechanism by which an owner notices something odd.
            prefixes = {t: (f"{len(rids)}{G['times']} on reports "
                            f"{','.join(str(r) for r in rids)}")
                        for t, rids in shown}
            pwidth = max((len(p) for p in prefixes.values()), default=0)

            head = (f"{G['recur']} recurrence{DOT}"
                    f"{len(recurring_open)} of {len(open_titles)} proposals on "
                    f"report #{anchor.get('id', '?')} have been asked before"
                    f"{DOT}{len(census)} recurring titles across "
                    f"{sum(len(v) for v in census.values())} proposal rows")
            rows: List[str] = [head]
            for title, rids in shown:
                d = disp.get(title) or {}
                en = list(d.get("enacted_reports") or [])
                ea = list(d.get("eaten") or [])
                en_txt = (f"enacted {len(en)} (r" + ",r".join(str(r) for r in en) + ")"
                          if en else "enacted 0")
                if ea:
                    ea_txt = ("EATEN " + str(len(ea)) + " (audit #"
                              + ",#".join(str(x["audit_id"]) for x in ea)
                              + ", " + ea[-1]["date"] + ")")
                else:
                    ea_txt = "eaten 0"
                tail = "" if title in open_set else f"{DOT}not on #{anchor.get('id','?')}"
                rows.append("    " + prefixes[title].ljust(pwidth) + "  "
                            + _scrub(title, ascii_only=ascii_only).ljust(width)
                            + f"{DOT}{en_txt}{DOT}{ea_txt}{tail}")
            if len(order) > len(shown):
                rows.append(f"    {len(order) - len(shown)} further recurring "
                            f"titles not shown")
            clauses.append(ProofClause(
                "recurrence", tuple(rows),
                "self_eval_reports.proposed_* -> recurrence map; "
                "self_eval_enacted_proposals WHERE status='enacted'; "
                "approval_audit_log WHERE outcome_status='error'",
                {"recurring_titles": len(census),
                 "recurring_open": len(recurring_open),
                 "open_titles": len(open_titles),
                 "proposal_rows": sum(len(v) for v in census.values()),
                 "census": {t: list(v) for t, v in census.items()},
                 "shown": [t for t, _ in shown]}))

            # 4. THE KEYSTONE, stated rather than implied: a title the record
            #    calls enacted, still being proposed. This single query is why
            #    "enacted" cannot be trusted as an outcome.
            again = [(t, len((disp.get(t) or {}).get("enacted_reports") or []))
                     for t in recurring_open
                     if (disp.get(t) or {}).get("enacted_reports")]
            if again:
                body = DOT.join(
                    f"{_scrub(t, 34, ascii_only)} ({n} row{'' if n == 1 else 's'})"
                    for t, n in again)
                clauses.append(ProofClause(
                    "still_open_after_enacted",
                    (f"{G['warn']} recorded 'enacted' and proposed again on "
                     f"report #{anchor.get('id','?')}{DOT}{body}",),
                    "self_eval_enacted_proposals WHERE status='enacted' "
                    "INTERSECT the titles on the newest report",
                    {"titles": dict(again)}))
    except Exception as e:
        omissions.append(ProofOmission(
            "recurrence", f"{type(e).__name__}: {e}",
            "self_eval_reports.proposed_enhancements/proposed_deprecations"))

    # 5. THE DISPATCH RECORD -- how often the tool kept the decision at all.
    try:
        rec = dispatch_record(db)
        b = rec.get("busiest") or {}
        tail = ""
        if b:
            tail = (f"{DOT}busiest day {b['date']}: {b['rows']} dispatches, "
                    f"{len(b['ok_ids'])} ok"
                    + (" (audit #" + ",#".join(str(i) for i in b["ok_ids"]) + ")"
                       if b["ok_ids"] else "")
                    + f", {len(b['error_ids'])} error"
                    + (" (audit #" + ",#".join(str(i) for i in b["error_ids"]) + ")"
                       if b["error_ids"] else ""))
        clauses.append(ProofClause(
            "dispatch",
            (f"{G['bad']} dispatch{DOT}{rec['errors']} of {rec['total']} "
             f"approval_audit_log rows recorded outcome_status='error'{tail}",),
            "SELECT COUNT(*), SUM(outcome_status='error') FROM approval_audit_log; "
            "GROUP BY substr(created_at,1,10) ORDER BY COUNT(*) DESC LIMIT 1",
            dict(rec)))
    except Exception as e:
        omissions.append(ProofOmission(
            "dispatch", f"{type(e).__name__}: {e}",
            "approval_audit_log.{outcome_status,created_at}"))

    # 6. WHAT IT CANNOT SEE. The ledger is absent on the live brain today, and
    #    saying so is the whole of S4: a surface that quietly shows a partial
    #    number is worse than one that names its blind spot.
    blind: List[str] = []
    try:
        conn = _open_ro(db)
        try:
            has_ledger = _table_exists(conn, "decision_events")
        finally:
            conn.close()
        if not has_ledger:
            blind.append(
                "decision_events not installed (migration 030 absent): there is "
                "no per-decision outcome ledger, so WHY a dispatch was eaten is "
                "unavailable. The columns read instead are "
                "self_eval_enacted_proposals.status and "
                "approval_audit_log.outcome_status")
    except Exception as e:
        blind.append(f"could not inspect sqlite_master: {type(e).__name__}: {e}")
    for om in omissions:
        blind.append(f"clause '{om.key}' omitted, not estimated: {om.reason} "
                     f"[{om.source}]")
    if blind:
        clauses.append(ProofClause(
            "blind", tuple(f"{G['blind']} cannot see{DOT}{b}" for b in blind),
            "SELECT COUNT(*) FROM sqlite_master WHERE name='decision_events' "
            "+ the omission list built above",
            {"omissions": [o.key for o in omissions],
             "notes": len(blind)}))

    return ProofLine(tuple(clauses), tuple(omissions))


def build_proof_line(db: Optional[str] = None, *, max_titles: int = 5,
                     ascii_only: bool = False, indent: str = "") -> str:
    """The surface Drew reads under the count. NEVER raises, NEVER writes.

    Returns a multi-line string with no trailing newline; "" only if literally
    every source failed, in which case the reason is in LAST_DEGRADE.
    """
    try:
        pl = proof_line_clauses(db, max_titles=max_titles, ascii_only=ascii_only)
        text = pl.render(indent=indent)
        if not text:
            LAST_DEGRADE.append("build_proof_line: every clause was omitted; "
                                f"reasons={[o.reason for o in pl.omissions]}")
        return text
    except Exception as e:  # pragma: no cover - the hook must never die here
        LAST_DEGRADE.append(f"build_proof_line: {type(e).__name__}: {e}")
        mark = "o cannot see" if ascii_only else "○ cannot see"
        sep = " - " if ascii_only else " · "
        return (f"{indent}{mark}{sep}the proof-line could not be built: "
                f"{type(e).__name__}: {e}")



# ---------------------------------------------------------------------------
# CLI — `python3 voidloom_banner.py [banner|precompact|signoff|notify|count]`
# ---------------------------------------------------------------------------


def _main(argv: List[str]) -> int:
    what = argv[1] if len(argv) > 1 else "banner"
    # Allow a forced color mode for previews: --mode truecolor|ansi256|none
    force = None
    if "--mode" in argv:
        try:
            force = argv[argv.index("--mode") + 1]
        except Exception:
            force = None
    seed = None
    if "--seed" in argv:
        try:
            seed = int(argv[argv.index("--seed") + 1])
        except Exception:
            seed = None

    if what == "banner":
        print(build_banner(force_mode=force, seed=seed))
    elif what == "precompact":
        mc = None
        if "--memories" in argv:
            try:
                mc = int(argv[argv.index("--memories") + 1])
            except Exception:
                mc = None
        print(build_precompact_line(memories_captured=mc, force_mode=force))
    elif what == "signoff":
        print(build_signoff_line(force_mode=force))
    elif what == "notify":
        txt = build_notification_text()
        print(txt if txt is not None else "(nothing pending)")
    elif what == "proof":
        # Diagnostic: the proof-line alone, and (with --json) its clauses with
        # the SQL and the values behind each -- so "is that number real?" is
        # answerable without reading this file.
        if "--json" in argv:
            pl = proof_line_clauses(max_titles=99)
            print(json.dumps(
                {"clauses": [{"key": c.key, "lines": list(c.lines),
                              "source": c.source, "values": c.values}
                             for c in pl.clauses],
                 "omissions": [{"key": o.key, "reason": o.reason,
                                "source": o.source} for o in pl.omissions],
                 "denied_writes": DENIED_WRITES}, indent=2, default=str))
        else:
            print(build_proof_line(ascii_only=("--ascii" in argv)))
    elif what == "count":
        # Diagnostic: the unified count + per-source breakdown as JSON.
        # R3 -- THE LAST UNCONVERTED CONSUMER IN THIS FILE, and I had cleared it
        # as a false positive because its sibling `return 0` is a process exit
        # code. It is not: this branch printed {"total": 0, "self_eval": 0, ...}
        # at exit 0 against an unreadable brain, discarding a trust bit that
        # `unified_pending_report` had computed one frame earlier -- the R3
        # defect, surviving inside the R3 fix, in the module that owns the
        # distinction. Found by a second reader sweeping the RENDER layer, which
        # is the whole argument for sweeping it.
        # R7/C2. `_main`'s `count` verb is a SURFACE: it is what a script or a
        # human types to ask the same question the banner answers. It reads the
        # report directly, so it already carries `unread` -- and the assertion
        # in prove_R7.py holds it to agreeing with every other surface on the
        # same brain in the same instant.
        rep = unified_pending_report()
        # R6/U3. The envelope republishes the breakdown, so it republishes the
        # shape slots too. `unread` rides beside them, so a machine consumer can
        # tell a measured 0 from a placeholder without parsing a sentence.
        out = {"total": rep["total"], **rep["breakdown"],
               "unread": list(rep["unreadable_sources"] or []),
               "trusted": rep["trusted"], "unknown": rep["unknown"]}
        if rep["unknown"]:
            out["refusal"] = refusal()
        print(json.dumps(out))
    else:
        print("usage: voidloom_banner.py "
              "[banner|precompact|signoff|notify|count|proof] "
              "[--mode truecolor|ansi256|none] [--seed N] [--memories N]",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""decision_units.py — G1, THE DECISION LEDGER. Ruler: VL-LIFECYCLE-v2.0.

THE GOVERNING PRINCIPLE
    The brain may only tell Drew what it can prove from its own records, and may
    only ask him for decisions he alone can make.

THE TEST EVERY STATE HERE PASSES (VAEL's executable form, applied by name)
    Name the row that would look different if this state were false. Name the
    code path that writes that row. Confirm it is not the same path that decides
    the state.

  open        <- the ABSENCE of a decision_events row. An absence cannot be
                 written by the thing that wants it. PASSES.
  approved    <- self_eval_reports.drew_approved.approved[] or an
                 approval_audit_log approve row with outcome_status='ok'.
                 Written by drew_review / the CLI dispatch. PASSES.
  declined    <- drew_approved.rejected[] or an audit reject row, outcome ok.
                 Written by drew_review / the CLI dispatch. PASSES.
  enacted     <- self_eval_enacted_proposals.status='enacted' with a non-null
                 enacted_memory_id. Written by enact_approved. PASSES.
  discarded   <- an approval_audit_log row with outcome_status='error' naming
                 "already reviewed". Written by write_audit from the CLI --
                 which is emphatically NOT the derivation. PASSES.

WHAT IS DELIBERATELY NOT HERE
    Closure states (verified / refuted / stale / wont-do) are G3. The drain is
    G5. The TUI is G4. The output assertions are G2b. They are not built and not
    stubbed: pulling them in is the broad-graduation error that makes a bar
    unable to flip to yes.

R17 -- NO BARE SWALLOWS. There is not one `except Exception: pass` and not one
bare `except:` in this file. Every degradation is RECORDED through `_degrade()`,
which appends a reason to a retrievable log and writes to stderr. The audit log
was the only durable evidence of the 2026-08-02 loss and it was written on a
path documented "BEST-EFFORT", inside a swallow; had it failed, the defect would
be undiscoverable today. Assertions here RAISE.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

__all__ = [
    "SURFACE_SELF_EVAL", "SURFACE_BELIEF", "SURFACE_GOVERNANCE",
    "OUTCOMES", "CLOSING_OUTCOMES",
    "CountResult", "OpenUnit", "AttentionResult", "MigrationResult",
    "OpenReportSet", "BirthResult",
    "AgreementViolation", "LedgerError",
    "unit_key", "unified_open_count", "open_units",
    "open_self_eval_report_ids", "create_units_for_report",
    "divergence_fingerprint", "DIVERGENCE_FACTS",
    "derive_report_state", "assert_status_agreement",
    "attention_signal", "acknowledge", "dismiss", "snooze", "defer",
    "record_decision", "migrate", "apply_schema",
    "write_failure_reason", "approved_unenacted_counts",
    "degrade_log", "clear_degrade_log",
]

# R5/C6 (class sweep, widened definition). WAS os.path.join(str(Path.home()),
# ".claude", "memory", "claude_memory.db") -- home-relative, which is NOT
# relative-self and is not an environment variable. THE ONE COUNT FUNCTION lives
# in this module, so this tier decided which brain every surface's number came
# from; measured, it returned a DIFFERENT database from the one the sibling list
# reader resolved in the same process.
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "claude_memory.db")
_HERE = Path(__file__).resolve().parent
SCHEMA_FILE = _HERE.parent / "migrations" / "migration_030_decision_units.sql"

SURFACE_SELF_EVAL = "self-eval"
SURFACE_BELIEF = "belief"
SURFACE_GOVERNANCE = "governance"

#: R7/C5 -- THE VOCABULARY, ONCE, AS A VALUE. Four spellings of these three
#: words existed across the build (a second declaration in `voidloom_approvals`,
#: two regex literals, and a WRITE site in this file), bound by nothing. Measured
#: 2026-09-04: changing ONE CHARACTER here -- `self-eval` to `selfeval` --
#: imports cleanly, asserts nothing, and dispatches 0 of 9 units where the
#: control dispatches 9 of 9. A vocabulary that no assert binds is not a
#: vocabulary; it is four independent constants that happen to agree today.
#: R8/B1 -- WHAT STOOD HERE WAS A UNIVERSAL OVER THE DOWNSTREAM SPELLERS, and
#: it enumerated none of them. Swept by shape rather than by spelling
#: (`conventions-016`: a predicate that names no vocabulary cannot be blind to
#: one), it stood at FIVE literal sites BEFORE THIS ROUND and the universal was
#: false at two. The claim is described rather than quoted -- re-uttering a
#: repudiated sentence ships it again in the same bytes.
#:
#: AS SHIPPED IT STANDS AT FOUR, and the census is stated in the present tense
#: because a count is exactly the kind of sentence that goes stale silently.
#: BOUND: `BREAKDOWN_KEYS` below, and the two breakdown dicts inside
#: `unified_open_count`, each followed by an assert against `BREAKDOWN_KEYS` in
#: its own branch. UNBOUND, AND DELIBERATELY: `voidloom_banner._breakdown_keys`'s
#: fallback tuple, which is the answer given when THIS MODULE IS ABSENT, so
#: there is nothing here for it to read; binding it would delete the only
#: behaviour it exists for. GONE, NOT BOUND: `voidloom_banner._BREAKDOWN_ZERO`
#: was the fifth. R8/B2 made it a comprehension over the one count function, so
#: it is no longer a copy of anything -- the NAME and the arm that consumes it
#: survive, the LITERAL does not; graded by B2-NOT-REMOVED. Separately, `_bind_surface_vocabulary()` in
#: `voidloom_approvals` raises at import on a drift in the hyphen vocabulary
#: declared just above. The four-site census and each site's binding are graded
#: by B1-VOCAB-SITES in `_canaryR/R8/prove_R8.py`, which fails if a FIFTH site
#: appears, if a bound site loses its binding, or if the deliberate exception
#: moves.
SURFACES = (SURFACE_SELF_EVAL, SURFACE_BELIEF, SURFACE_GOVERNANCE)

#: The canonical tagged-ref pattern, BUILT from `SURFACES` rather than spelled
#: beside it. The raw part allows colons (belief batch ids embed ISO timestamps)
#: and the slug characters.
APPROVAL_REF_PATTERN = (r"^(" + "|".join(re.escape(s) for s in SURFACES)
                        + r"):[A-Za-z0-9_.:-]+$")

#: The four decision outcomes. NOT closure states -- see the module docstring.
OUTCOMES = ("approved", "declined", "enacted", "discarded")
#: The outcomes that CLOSE a unit. `discarded` is pointedly absent: a discarded
#: decision is one the system ate, so the decision still has to be made.
CLOSING_OUTCOMES = ("approved", "declined", "enacted")

#: The breakdown keys. FIXED, always all present, in this order. R11: the count
#: must agree across surfaces IN SHAPE, not merely in total -- today the totals
#: agree only because governance_proposals has 0 rows, and a measurement that
#: agrees for the wrong reason is not a measurement.
BREAKDOWN_KEYS = ("self_eval", "belief", "governance")
DETAIL_KEYS = ("self_eval_units", "self_eval_reports", "belief_batches", "governance_batches")

#: R6 -- THE BREAKDOWN KEY FOR THE SELF-EVAL SOURCE, AND IT IS NOT
#: `SURFACE_SELF_EVAL`. That constant is "self-eval" with a HYPHEN (it names the
#: unit-key namespace); this one is "self_eval" with an UNDERSCORE (it names the
#: breakdown/detail key). Two spellings of one word, three lines apart, and
#: `_unread_sources` compares against exactly one of them.
#:
#: MEASURED, in this round, before it shipped: writing `SURFACE_SELF_EVAL` there
#: made the self-eval arm unreachable, so the function fell through to the LEAF
#: outcome for self-eval -- which is `None` on EVERY legacy brain, because
#: `v_decision_unit_state` is what "legacy" means. The result was
#: `unread=('self_eval',)` on a source that had been read perfectly well, i.e.
#: the banner refusing on 100% of live sessions: the exact failure the Council
#: rejected `leafonly` for, re-introduced by a hyphen.
KEY_SELF_EVAL = "self_eval"
assert KEY_SELF_EVAL in BREAKDOWN_KEYS, "the self-eval breakdown key drifted"


class LedgerError(RuntimeError):
    """A ledger operation failed in a way the caller must see."""


class AgreementViolation(AssertionError):
    """Derived report state disagrees with the stored `status` column, and the
    disagreement is not a registered historical divergence. R9."""


# ---------------------------------------------------------------------------
# R17 -- recorded degradation. NOT a swallow.
# ---------------------------------------------------------------------------

_DEGRADE_LOG: List[Dict[str, str]] = []


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


def _degrade(where: str, exc: BaseException, *, fallback: Any) -> Any:
    """Record a degradation and return `fallback`.

    This is the ONLY place in this module where an exception stops propagating,
    and it is not a swallow: the reason is appended to a retrievable log AND
    written to stderr. `degrade_log()` returns it; a caller (or a test) can
    therefore prove whether a zero came from an empty world or a broken read --
    the distinction the house's ~68 immediate swallows destroy.
    """
    entry = {
        "where": where,
        "error": f"{type(exc).__name__}: {exc}",
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    _DEGRADE_LOG.append(entry)
    print(f"[decision_units] DEGRADED at {where}: {entry['error']}", file=sys.stderr)
    return fallback


def degrade_log() -> List[Dict[str, str]]:
    """Every degradation recorded since import (or since clear_degrade_log())."""
    return list(_DEGRADE_LOG)


def clear_degrade_log() -> None:
    _DEGRADE_LOG.clear()


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def resolve_db(db: Optional[str] = None) -> str:
    return (db
            or os.environ.get("WIZARD_LIBRARY_DB")
            or os.environ.get("CLAUDE_MEMORY_DB")
            or DEFAULT_DB_PATH)


def _open_ro(db_path: str) -> sqlite3.Connection:
    """A genuinely read-only connection. Every READ path in this module uses
    this and only this, which is why `attention_signal` cannot have a write side
    effect even by accident -- see R12/R14."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _open_rw(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone() is not None


def ledger_present(db: Optional[str] = None) -> bool:
    """True once migration 030 has been applied. Read-side guard so every caller
    degrades to the legacy count rather than raising on an unmigrated brain."""
    db_path = resolve_db(db)
    if not Path(db_path).exists():
        return False
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as e:
        return _degrade("ledger_present/open", e, fallback=False)
    try:
        return _table_exists(conn, "decision_units") and _table_exists(conn, "decision_events")
    except sqlite3.Error as e:
        return _degrade("ledger_present/query", e, fallback=False)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def unit_key(surface: str, ref: Any, title: Optional[str] = None) -> str:
    """The stable id of one decidable unit.

    R6: for self-eval the key is (report, proposal) and NOTHING ELSE. Belief and
    governance keep their batch granularity (the L5-D anti-alarm-fatigue
    contract: a 148-proposal nightly batch is one unit, not 148).
    """
    if surface == SURFACE_SELF_EVAL:
        if title is None:
            raise LedgerError("a self-eval unit key requires a proposal title")
        return f"{SURFACE_SELF_EVAL}:{int(ref)}:{_digest(title)}"
    return f"{surface}:{ref}"


def suppression_key(piece_affected: Optional[str], title: str) -> str:
    """The (metric, work) pair.

    THIS IS A SUPPRESSION KEY ONLY AND NEVER AN IDENTITY. "Activate
    L3-C7-cross-tier-bridges" was approved on report #4 (succeeded) and on report
    #5 (discarded); a (metric, work) identity merges them and the success absorbs
    the failure. Two units, one suppression key -- asserted in the tests.
    """
    norm = re.sub(r"\s+", " ", (title or "").strip().lower())
    return _digest(f"{(piece_affected or '').strip().lower()}|{norm}")


# ---------------------------------------------------------------------------
# THE ONE COUNT FUNCTION -- R10
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CountResult:
    """R10/R11. Banner, CLI and faces all call `unified_open_count` and none
    counts anything itself, so the breakdown KEYS as well as the total are
    identical across surfaces by construction rather than by docstring."""
    total: int
    breakdown: Dict[str, int]
    detail: Dict[str, int]
    open_unit_keys: Tuple[str, ...]
    degraded: Tuple[str, ...] = ()
    #: R4/P1. `False` means THE SET IS UNKNOWN, not that it is empty. An
    #: absent `self_eval_reports` is not an empty inbox -- it is a brain the
    #: self-eval machinery was never installed into, which is precisely what
    #: `claude_memory.py init` produces (measured: 11 tables, no
    #: self_eval_reports). Every consumer that RENDERS or WRITES must branch
    #: on this bit; a consumer that names its own output keys and omits it is
    #: where the bit dies.
    trusted: bool = True
    #: R6/U1+U2. THE NAMED HOLES. Every source in here has a SHAPE SLOT in
    #: `breakdown` and NO MEASUREMENT behind it -- the source was never opened,
    #: so its `0` is a placeholder and no surface may render it as a number.
    #: DERIVED from the leaf outcomes inside `unified_open_count`, never passed
    #: in by a caller; `trusted` is `not unread` by construction, so the scalar
    #: and the set cannot drift. It is LAST and DEFAULTED so both sentinels
    #: (tests/test_decision_units.py:746, regression/readonly_witnesses.py:273)
    #: still construct unchanged.
    #:
    #: WHY A SET AND NOT ONLY A BOOL: `trusted` has arity 1 and describes an
    #: aggregate of three sources. A consumer told only "untrusted" cannot know
    #: WHICH source refused, which is exactly how voidloom_banner.py came to
    #: hardcode `unreadable_sources: ["self_eval"]` about a governance failure.
    unread: Tuple[str, ...] = ()

    def as_tuple(self) -> Tuple[int, Dict[str, int]]:
        """The legacy `(total, breakdown)` shape both old call sites returned."""
        return self.total, dict(self.breakdown)


@dataclass(frozen=True)
class OpenUnit:
    key: str
    surface: str
    ref: Any
    title: str
    kind: str
    report_id: Optional[int]
    created_at: Optional[str]
    discarded_count: int = 0
    piece_affected: Optional[str] = None


def _self_eval_open_units(conn: sqlite3.Connection) -> List[OpenUnit]:
    """Open self-eval units from the ledger.

    R15: a report with zero decidable units contributes ZERO here, at READ time,
    with no migration of its own -- reports #2 and #6 simply have no rows in
    decision_units, so they cannot contribute. Both were cleared by hand-written
    SQL months apart; a birth-time-only guard is in the wrong place.
    """
    rows = conn.execute(
        "SELECT v.unit_key, v.report_id, v.proposal_title, v.proposal_kind, "
        "       v.discarded_count, u.piece_affected, r.created_at "
        "  FROM v_decision_unit_state v "
        "  JOIN decision_units u ON u.id = v.unit_id "
        "  JOIN self_eval_reports r ON r.id = v.report_id "
        " WHERE v.is_open = 1 "
        " ORDER BY v.report_id DESC, v.unit_id ASC"
    ).fetchall()
    return [
        OpenUnit(key=r["unit_key"], surface=SURFACE_SELF_EVAL, ref=r["report_id"],
                 title=r["proposal_title"], kind=r["proposal_kind"],
                 report_id=r["report_id"], created_at=r["created_at"],
                 discarded_count=int(r["discarded_count"] or 0),
                 piece_affected=r["piece_affected"])
        for r in rows
    ]


def _batch_open_units(conn: sqlite3.Connection, table: str,
                      surface: str) -> Optional[List[OpenUnit]]:
    """Belief / governance batches. One unit per pending report_batch.

    R6/U2 -- THE LEAF THAT COULD NOT SAY IT DID NOT READ. This returned a bare
    `[]` for BOTH "the table is not on this brain" and "the table is here and
    holds nothing pending", with no `_degrade()` call, so the two worlds were
    indistinguishable at every frame above: measured on a ledger brain with
    `governance_proposals` absent vs present-and-empty, `repr identical? True`,
    same `trusted=True`, same EMPTY degrade log. That is the founding
    `(0, True)` / `(0, False)` collapse, at the leaf, five frames below where
    four rounds looked for it.

      * `None` -- THE SOURCE IS NOT ON THIS BRAIN. Nothing was opened, so there
        is no measurement and any number published for it is a placeholder.
      * `[]`   -- the source IS on this brain and it holds nothing pending.
        A read zero. It earns silence.

    The `_degrade()` call is not decoration: it is the second, independent
    channel that makes the two worlds differ even for a caller that only ever
    looks at the degrade log (`CountResult.degraded`).
    """
    if not _table_exists(conn, table):
        _degrade(
            "_batch_open_units/" + surface,
            LedgerError(
                table + " is ABSENT from this brain, so the " + surface +
                " source was NOT OPENED: its pending count is UNKNOWN, not "
                "zero, and any 0 published for it is a placeholder"),
            fallback=None)
        return None
    rows = conn.execute(
        f"SELECT report_batch AS b, COUNT(*) AS n, MIN(created_at) AS c "
        f"  FROM {table} WHERE status='pending' "
        f" GROUP BY report_batch ORDER BY c DESC"
    ).fetchall()
    return [
        OpenUnit(key=unit_key(surface, r["b"]), surface=surface, ref=r["b"],
                 title=f"{surface} batch {r['b']}", kind="batch",
                 report_id=None, created_at=r["c"])
        for r in rows
    ]


def open_units_ex(db: Optional[str] = None
                  ) -> Tuple[List[OpenUnit], Dict[str, Optional[int]]]:
    """`(units, reads)` -- the units AND, per BREAKDOWN_KEYS source, its OUTCOME.

    R6/U2. `reads[k]` is the number of rows that source's query RETURNED, or
    `None` when the query was never run (the table is not on this brain, or the
    read raised). It is co-keyed with BREAKDOWN_KEYS by construction, so a
    fourth source added to that tuple gets an outcome slot with no edit here.

    `open_units()` below is `open_units_ex()[0]` -- every existing consumer of
    the unit list is unchanged, and NOTHING that only wants units has to learn
    about outcomes.
    """
    db_path = resolve_db(db)
    reads: Dict[str, Optional[int]] = {k: None for k in BREAKDOWN_KEYS}
    if not Path(db_path).exists():
        _degrade("open_units/db_absent",
                 LedgerError("the database file does not exist, so NO source "
                             "was opened: every count is UNKNOWN, not zero"),
                 fallback=None)
        return [], reads
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as e:
        _degrade("open_units/open", e, fallback=None)
        return [], reads
    try:
        units: List[OpenUnit] = []
        # Per-surface isolation: one surface failing contributes NOTHING and is
        # RECORDED; it never silently zeroes the others and never hides itself.
        #
        # R6/U2. The self-eval lambda's `else []` became `else None` for the
        # same reason the leaf did: an absent `v_decision_unit_state` is a
        # source that was not opened, not an empty one.
        _sources = (
            (KEY_SELF_EVAL, lambda: _self_eval_open_units(conn) if _table_exists(conn, "v_decision_unit_state") else None),
            ("belief", lambda: _batch_open_units(conn, "edge_proposals", SURFACE_BELIEF)),
            ("governance", lambda: _batch_open_units(conn, "governance_proposals", SURFACE_GOVERNANCE)),
        )
        # The outcome map is co-keyed with BREAKDOWN_KEYS BY ASSERTION, not by
        # the author having typed the same three strings twice. A fourth source
        # added to BREAKDOWN_KEYS without a reader here goes RED at once instead
        # of publishing a silent placeholder -- which is the whole defect class.
        assert tuple(lbl for lbl, _ in _sources) == BREAKDOWN_KEYS, (
            "open_units_ex's readers and BREAKDOWN_KEYS disagree: "
            f"{tuple(lbl for lbl, _ in _sources)} vs {BREAKDOWN_KEYS}")
        for label, fn in _sources:
            try:
                got = fn()
                if got is not None:
                    reads[label] = len(got)
                    units.extend(got)
            except (sqlite3.Error, ValueError, TypeError, KeyError) as e:
                _degrade(f"open_units/{label}", e, fallback=None)
        return units, reads
    finally:
        conn.close()


def open_units(db: Optional[str] = None) -> List[OpenUnit]:
    """Every OPEN decidable unit across every surface. READ-ONLY.

    The historical shape, kept verbatim for every caller that only wants units.
    A caller that RENDERS or WRITES needs `open_units_ex` and the outcomes.
    """
    return open_units_ex(db)[0]


def _unread_sources(se_read: bool,
                    reads: Dict[str, Optional[int]]) -> Tuple[str, ...]:
    """The BREAKDOWN_KEYS sources with NO measurement behind their term.

    ONE spelling, used by BOTH branches of `unified_open_count`, in
    BREAKDOWN_KEYS order.

    `se_read` IS PASSED IN AND NOT TAKEN FROM `reads`, AND THAT IS THE WHOLE
    DESIGN. On a LEGACY brain `v_decision_unit_state` is absent by definition,
    so `reads['self_eval']` is `None` there while the legacy reader has read
    `self_eval_reports` perfectly well. Deriving the self-eval outcome from the
    leaf on that branch is the MEASURED kill: it makes the aggregate refuse on
    100% of live sessions, permanently, because `ledger_present(live) is False`
    and migration 030 cannot run from the un-landed package (Council #6, Q2 --
    the `leafonly` tree renders `1 / trusted=False` on the armed live brain).
    """
    out: List[str] = []
    for k in BREAKDOWN_KEYS:
        if k == KEY_SELF_EVAL:
            if not se_read:
                out.append(k)
        elif reads.get(k, None) is None:
            out.append(k)
    return tuple(out)


def _legacy_self_eval_report_ids(db_path: str) -> Optional[List[int]]:
    """The pre-ledger contract, kept ONLY as the unmigrated-brain fallback:
        SELECT id FROM self_eval_reports WHERE status='pending'

    Used when migration 030 has not been applied, so an un-migrated brain -- the
    second brain on another machine (3 reports, all `pending`, and NO approval_* tables at all) and the
    three staging trees (which stop at migration 021) -- reports the number it
    always did rather than a silent 0. It returns IDS, not a bare count, so the
    attention set-difference still has keys to work with there; a fallback that
    returned only a total would make the banner vanish on every unmigrated brain.
    The degradation is RECORDED, never silent."""
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as e:
        # R4/P1, SAME CLASS: a database that will not open is UNKNOWN
        # too. Returning [] here made an unreadable brain and an empty
        # one indistinguishable at every surface downstream.
        return _degrade("legacy_count/open", e, fallback=None)
    try:
        if not _table_exists(conn, "self_eval_reports"):
            # R4/P1. THE LINE THE R0 RITUAL LANDED ON. `[]` here is a
            # CONFIDENT ZERO -- "I looked and nothing awaits review" --
            # asserted about a brain whose self-eval table does not
            # exist, so nothing could ever have been written to it and
            # nothing ever looked. That is the state `claude_memory.py
            # init` leaves behind, and the monthly timer keeps it for up
            # to 29 days. Absence of the table is absence of the ANSWER.
            return _degrade(
                "legacy_count/table_absent",
                LedgerError(
                    "self_eval_reports is ABSENT from this brain "
                    "(migration_021 never applied), so the pending "
                    "self-eval question is UNKNOWN, not zero"),
                fallback=None)
        return [int(r[0]) for r in conn.execute(
            "SELECT id FROM self_eval_reports WHERE status='pending' ORDER BY id")]
    except sqlite3.Error as e:
        return _degrade("legacy_count/query", e, fallback=None)
    finally:
        conn.close()


@dataclass(frozen=True)
class OpenReportSet:
    """THE ONE SET of self-eval reports that still hold something undecided.

    R21. `ids` is the SET every self-eval offer surface names, and every count of
    those reports is `len(ids)` -- so no surface can agree on cardinality while
    naming a different report. "A count cannot see a swap" (#13744): measured
    2026-09-02 on one database, five surfaces returned 3 / 3 / 4 / 2 / 5 with a
    set-difference of [1,2,3,5,6], and four of the five were counts.
    """
    ids: Tuple[int, ...]
    source: str                       # 'ledger' | 'legacy'
    orphans: Tuple[int, ...] = ()     # reports holding FEWER units than
                                      # admissible proposals -- which includes,
                                      # but is not limited to, none at all
    orphan_proposals: int = 0         # the UNATTACHED REMAINDER summed over
                                      # `orphans`, never their whole admissible
                                      # count (R25')
    notes: Tuple[str, ...] = ()
    #: R4/P1. `False` means THE SET IS UNKNOWN, not that it is empty. An
    #: absent `self_eval_reports` is not an empty inbox -- it is a brain the
    #: self-eval machinery was never installed into, which is precisely what
    #: `claude_memory.py init` produces (measured: 11 tables, no
    #: self_eval_reports). Every consumer that RENDERS or WRITES must branch
    #: on this bit; a consumer that names its own output keys and omits it is
    #: where the bit dies.
    trusted: bool = True


def open_self_eval_report_ids(db: Optional[str] = None) -> OpenReportSet:
    """Self-eval reports still holding at least one undecided proposal.

    PATH  self_eval_reports + v_decision_unit_state -> the set -> every offer
          surface (banner, approvals list, approvals count, the CLI, the toast).
    ROW   the report ids themselves, never a bare integer.

    TWO TERMS, and the second is the safety net that keeps a decision from being
    eaten by the fix:

      1. LEDGER-OPEN -- reports with >=1 open decision unit. This is the whole
         set on a healthy migrated brain, and it reads no `status`.
      2. ORPHANS -- reports carrying >=1 proposal and ZERO units. After R19 (units
         are born in the report's own transaction) and after `migrate()` has
         backfilled history, this term is EMPTY. It exists because the
         alternative to including such a report is DROPPING it: report #8 held
         four proposals and no units on the day this was written, and a set that
         quietly excluded it would have made it invisible rather than
         miscounted. Every orphan is RECORDED in `notes` and in the degrade log,
         so the term firing is loud, not a silent widening.

    The un-migrated brain keeps its old answer through
    `_legacy_self_eval_report_ids` -- the single sanctioned reader of
    `self_eval_reports.status` on the read side (see status_column_ban.py's
    exemption list), and it is recorded as a degradation every time.
    """
    db_path = resolve_db(db)
    if not ledger_present(db_path):
        ids = _legacy_self_eval_report_ids(db_path)
        _degrade("open_self_eval_report_ids",
                 LedgerError("migration 030 not applied; using the legacy "
                             "status='pending' report set"),
                 fallback=None)
        if ids is None:
            # R4/P1. THE REFUSAL, CARRIED rather than flattened. The old
            # line called `sorted(ids)` on whatever came back, so there
            # was no shape in which "I could not tell" could survive
            # this frame even if the reader had produced it.
            return OpenReportSet(
                ids=(), source="legacy", trusted=False,
                notes=("migration 030 absent AND the legacy self-eval "
                       "source could not be read: the open-report set "
                       "is UNKNOWN, and 0 here is a placeholder, not a "
                       "measurement",))
        return OpenReportSet(
            ids=tuple(sorted(ids)), source="legacy",
            notes=("migration 030 absent: the set is the legacy "
                   "status='pending' report ids",))
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as e:
        return _degrade("open_self_eval_report_ids/open", e,
                        fallback=OpenReportSet(ids=(), source="ledger",
                                               trusted=False,
                                               notes=("read failed",)))
    try:
        ledger_open = {int(r[0]) for r in conn.execute(
            "SELECT DISTINCT report_id FROM v_decision_unit_state "
            " WHERE is_open = 1 AND report_id IS NOT NULL")}
        unit_counts = {int(r[0]): int(r[1]) for r in conn.execute(
            "SELECT report_id, COUNT(*) FROM decision_units GROUP BY report_id")}
        props = _admissible_counts(conn)
        # R25. `n > 0 and units == 0` sees only the ALL-OR-NOTHING orphan. A
        # report with SOME units and a proposal that never became one is equally
        # orphaned and was equally invisible; `n > units` sees both. `n` is the
        # ADMISSIBLE count, so a report whose only shortfall is a duplicate title
        # is NOT an orphan -- against the raw count it would be one forever.
        orphans = sorted(rid for rid, n in props.items()
                         if n > unit_counts.get(rid, 0))
        notes = tuple(
            f"report #{rid}: {props[rid]} admissible proposal(s) and "
            f"{unit_counts.get(rid, 0)} decision unit(s) -- included so it "
            f"cannot vanish; this is R19 failing at birth"
            for rid in orphans)
        for n in notes:
            _degrade("open_self_eval_report_ids/orphan", LedgerError(n),
                     fallback=None)
        return OpenReportSet(
            ids=tuple(sorted(ledger_open | set(orphans))),
            source="ledger",
            orphans=tuple(orphans),
            # R25' -- THE UNATTACHED REMAINDER, never the whole admissible
            # count. `orphans` was widened (R25) from `n > 0 and units == 0`
            # to `n > units`, and this summand kept the NARROW membership's
            # arithmetic. Under the wider set an orphan may already hold
            # units that the open-unit term counts, so the old term
            # over-counted by exactly `unit_counts[rid]`. Reproduced on
            # three independent fixtures: 3 admissible/2 units,
            # 4/1, and 5/3 with the born units in a MIXED decision state --
            # the last of those is the one that separates `unit_counts`
            # from `open_units`, which an all-open or all-decided fixture
            # cannot do. The all-or-nothing orphan is unchanged: with
            # units == 0 the subtrahend is 0.
            orphan_proposals=sum(props[rid] - unit_counts.get(rid, 0)
                                 for rid in orphans),
            notes=notes,
        )
    except sqlite3.Error as e:
        return _degrade("open_self_eval_report_ids/query", e,
                        fallback=OpenReportSet(ids=(), source="ledger",
                                               trusted=False,
                                               notes=("query failed",)))
    finally:
        conn.close()


def _assert_keys_cover(breakdown: Dict[str, int],
                       keys: Sequence[str], where: str) -> None:
    """EVERY COUNTED ITEM IS LISTABLE. Raises `LedgerError` when it is not.

    THE ASSERTION IS THE OTHER HALF OF THE C3 REPAIR. Synthesising the missing
    keys fixes today's widening; this catches TOMORROW'S. `architecture-005`
    is a rule about future edits -- the next predicate that gets widened will be
    widened by someone who has never read this function, and the aggregate they
    forget to re-derive will be silent in exactly the way this one was.

    IT CAN GO RED, and the prover proves it: revert the synthesis above and this
    raises on a ledger brain holding one orphan report. It is scoped per
    SURFACE, because the failure mode is a whole source going keyless while the
    others cover for it in the total.
    """
    per: Dict[str, int] = {k: 0 for k in BREAKDOWN_KEYS}
    seen: Dict[str, set] = {k: set() for k in BREAKDOWN_KEYS}
    _pfx = {SURFACE_SELF_EVAL: "self_eval", SURFACE_BELIEF: "belief",
            SURFACE_GOVERNANCE: "governance"}
    for k in keys:
        surface, _, rest = str(k).partition(":")
        col = _pfx.get(surface)
        if col is None:
            continue
        # The ALARM unit is the report / batch, not the decision unit: a
        # self-eval key is `self-eval:<report_id>[:<slug>]` and many keys may
        # share one report. The covering set is therefore the set of REFS.
        seen[col].add(rest.split(":", 1)[0] if rest else "")
    for col in BREAKDOWN_KEYS:
        per[col] = len(seen[col])
    short = {k: (int(breakdown.get(k, 0) or 0), per[k]) for k in BREAKDOWN_KEYS
             if per[k] < int(breakdown.get(k, 0) or 0)}
    if short:
        raise LedgerError(
            f"{where}: counted-but-unlistable items -- "
            f"{ {k: f'counted {c}, keyed {g}' for k, (c, g) in short.items()} }. "
            f"An item with no key cannot be seen by the attention model, cannot "
            f"fire the banner and cannot be acknowledged: it is counted by three "
            f"surfaces and rendered by none. See architecture-005.")


def unified_open_count(db: Optional[str] = None) -> CountResult:
    """THE ONE COUNT FUNCTION (R10). Every surface delegates here.

    THE ALARM UNIT vs THE DECISION UNIT -- deliberately different subjects:
      * the DECISION unit is (report, proposal): that is what Drew decides, and
        what the ledger keys on;
      * the ALARM unit is the report, matching the long-standing L5-D batching
        contract (one nightly 148-proposal belief batch increments the banner by
        1, not 148). So `breakdown['self_eval']` counts DISTINCT REPORTS holding
        at least one open unit, while `detail['self_eval_units']` carries the
        underlying decision count. Both come from this one function, so a surface
        cannot pick a different subject by accident -- which is exactly how five
        independent count spellings across four modules came to disagree.
    """
    db_path = resolve_db(db)
    before = len(_DEGRADE_LOG)

    # R21. The self-eval number is the LENGTH OF THE SET the list names -- it is
    # not counted independently here. Both the un-migrated fallback and the
    # orphan safety net live once, inside open_self_eval_report_ids().
    rs = open_self_eval_report_ids(db_path)

    if rs.source == "legacy":
        # R6/U1 -- THE SCOPE FIX. This branch used to publish
        # `{"belief": 0, "governance": 0}` and return HERE, two lines above the
        # `open_units()` call that opens both of those sources successfully on
        # exactly this brain. They were not summaries of an unreadable world;
        # they were two sources the function declined to open. `_batch_open_units`
        # needs neither migration 030 nor `v_decision_unit_state`, so both reads
        # were always available on the legacy branch.
        #
        # THIS IS THE BRANCH THAT EXECUTES ON DREW'S MACHINE. `ledger_present`
        # is False on the installed brain (65 tables, `decision_units` ABSENT),
        # so `source == 'legacy'` on 100% of live sessions. MEASURED on a
        # snapshot of the live brain armed with one pending `edge_proposals`
        # row: the installed tree renders 2 and this branch rendered 1.
        n = len(rs.ids)
        units, reads = open_units_ex(db_path)
        be = [u for u in units if u.surface == SURFACE_BELIEF]
        go = [u for u in units if u.surface == SURFACE_GOVERNANCE]
        unread = _unread_sources(bool(rs.trusted), reads)
        bd = {"self_eval": n, "belief": len(be), "governance": len(go)}
        det = {"self_eval_units": n, "self_eval_reports": n,
               "belief_batches": len(be), "governance_batches": len(go)}
        # Shape is a hard invariant on THIS branch too. It never was: the shape
        # assert lived only on the ledger return, so a drift here was unguarded.
        assert tuple(bd) == BREAKDOWN_KEYS, "breakdown key shape drifted"
        assert tuple(det) == DETAIL_KEYS, "detail key shape drifted"
        # The self-eval keys keep coming from the SET the list names (R21); the
        # belief/governance keys now come from the units actually opened, so
        # both branches name the same subjects and `attention_signal` sees the
        # batches it was blind to here.
        _lkeys = (tuple(f"{SURFACE_SELF_EVAL}:{i}" for i in rs.ids)
                  + tuple(u.key for u in units
                          if u.surface != SURFACE_SELF_EVAL))
        _assert_keys_cover(bd, _lkeys, "unified_open_count/legacy")
        return CountResult(
            total=sum(bd.values()), breakdown=bd, detail=det,
            open_unit_keys=_lkeys,
            degraded=tuple(e["where"] for e in _DEGRADE_LOG[before:]),
            trusted=bool(rs.trusted) and not unread,
            unread=unread)

    units, reads = open_units_ex(db_path)
    se = [u for u in units if u.surface == SURFACE_SELF_EVAL]
    be = [u for u in units if u.surface == SURFACE_BELIEF]
    go = [u for u in units if u.surface == SURFACE_GOVERNANCE]
    se_reports = len(rs.ids)
    # R6/U2. The bit is derived from the LEAF OUTCOMES, not from `rs` alone.
    # `rs.trusted` is honest about ONE source and was being attached to a
    # THREE-source aggregate -- a scope error, which is why an absent
    # `governance_proposals` produced `trusted=True, governance: 0`.
    unread = _unread_sources(bool(rs.trusted), reads)

    breakdown = {"self_eval": se_reports, "belief": len(be), "governance": len(go)}
    # `self_eval_units` is the underlying DECISION count: the OPEN units the
    # ledger knows about, PLUS the admissible proposals that never became a
    # unit at all. `rs.orphan_proposals` is that remainder and nothing else --
    # an orphan report may hold units too, and those are already inside `se`.
    detail = {"self_eval_units": len(se) + rs.orphan_proposals,
              "self_eval_reports": se_reports,
              "belief_batches": len(be), "governance_batches": len(go)}
    # Shape is a hard invariant, not a convention.
    assert tuple(breakdown) == BREAKDOWN_KEYS, "breakdown key shape drifted"
    assert tuple(detail) == DETAIL_KEYS, "detail key shape drifted"
    # R7/C3 -- `architecture-005` VERBATIM, and this return is the entry's own
    # sentence: WHEN A PREDICATE THAT SELECTS A SET IS WIDENED, RE-DERIVE EVERY
    # AGGREGATE COMPUTED OVER THAT SET.
    #
    # R25 widened the orphan selector from `n > 0 and units == 0` to
    # `n > units`, and `rs.ids` became `ledger_open | orphans`. Two aggregates
    # over that set were re-derived (`breakdown['self_eval']` and R25's
    # `orphan_proposals`). THIS ONE WAS NOT: the key list kept the NARROW
    # membership, so a report counted through the widened safety net arrived
    # here COUNTED AND KEYLESS.
    #
    # MEASURED, on a healthy fully-migrated ledger with no dry-run artefact:
    # count=5, notification "5 proposal sets await your review",
    # `voidloom approvals` listing five items -- and `build_banner()` returning
    # 0 BYTES, because the appearance gate fires on UNSEEN KEYS and there were
    # none to be unseen. `hooks/voidloom_notify.py` gates the desktop toast on
    # the same flag, so the toast was silent too. Three surfaces said 5 and two
    # said nothing.
    _keyed = {(u.surface, u.report_id) for u in units}
    _synth = tuple(f"{SURFACE_SELF_EVAL}:{i}" for i in rs.ids
                   if (SURFACE_SELF_EVAL, i) not in _keyed)
    _keys = tuple(u.key for u in units) + _synth
    _assert_keys_cover(breakdown, _keys, "unified_open_count/ledger")
    return CountResult(
        total=sum(breakdown.values()),
        breakdown=breakdown,
        detail=detail,
        open_unit_keys=_keys,
        degraded=tuple(e["where"] for e in _DEGRADE_LOG[before:]),
        trusted=bool(rs.trusted) and not unread,
        unread=unread,
    )


# ---------------------------------------------------------------------------
# Derived report state -- R9, computed ALONGSIDE the stored column
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DerivedState:
    report_id: int
    stored_status: str
    derived_status: str
    unit_count: int
    open_units: int
    enacted_units: int
    discarded_units: int
    open_titles: Tuple[str, ...]
    agrees: bool
    registered_divergence: bool = False
    fingerprint: str = ""


#: The fields a divergence IS. Anything that can change the divergence must be
#: in here, or the amnesty outlives the fact.
DIVERGENCE_FACTS = ("report_id", "stored_status", "derived_status", "unit_count",
                    "open_units", "enacted_units", "discarded_units", "open_titles")


def divergence_fingerprint(report_id: int, stored_status: str, derived_status: str,
                           unit_count: int, open_units: int, enacted_units: int,
                           discarded_units: int,
                           open_titles: Sequence[str]) -> str:
    """A digest over THE OBSERVED DIVERGENCE ITSELF -- the amnesty key (R9).

    WHY THIS IS A TERM OF A DIFFERENT KIND, NOT A TIGHTER KEY. The first version
    keyed the R9 amnesty on `report_id`. Reproduced by execution on a throwaway:
    register report #5, add ONE open unit (open_units 3 -> 4 while the registry
    row still said 3), and `assert_status_agreement()` returned clean. Tightening
    that key -- to (report, status) say -- only moves the boundary; the amnesty is
    still granted to a NAME, and a name does not change when the world does.

    A fingerprint over the facts grants the amnesty to a FACT. Every number that
    produced the divergence is in the digest, so any change to any of them yields
    a fingerprint that matches nothing in the registry, and the assertion fires.
    NOTHING HAS TO REMEMBER TO EXPIRE IT: the fact expires on its own.

    Titles are sorted, so a unit-id renumbering is not a change of fact while an
    added, removed or renamed proposal is.
    """
    payload = json.dumps({
        "report_id": int(report_id),
        "stored_status": str(stored_status),
        "derived_status": str(derived_status),
        "unit_count": int(unit_count),
        "open_units": int(open_units),
        "enacted_units": int(enacted_units),
        "discarded_units": int(discarded_units),
        "open_titles": sorted(str(t) for t in open_titles),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def admissible_proposals(
        enhancements: Optional[Sequence[Dict[str, Any]]],
        deprecations: Optional[Sequence[Dict[str, Any]]],
) -> Tuple[List[Tuple[str, str, Dict[str, Any]]], List[Dict[str, str]]]:
    """THE ONE SPELLING of "which proposals are entitled to a decision unit".

    Returns ``(admissible, skipped)``: an ordered list of ``(kind, title,
    proposal)``, and one LOUD record per inadmissible proposal in the vocabulary
    the product already publishes -- `proposal_without_title` and
    `duplicate_title_on_report`.

    R25 -- WHY THIS FUNCTION EXISTS, MEASURED. The rule was spelled TWICE
    (`create_units_for_report` and `migrate`) and a third consumer -- the
    DERIVATION -- did not spell it at all: `_derive_status` and
    `open_self_eval_report_ids`' orphan term compared unit counts against the
    RAW proposal count. The birth path's own invariant at
    `create_units_for_report` is `present == admissible`, NOT
    `present == proposals_seen`, so the product manufactures reports where
    units < raw proposals BY DESIGN. Two spellings of one rule disagreeing is
    the same class as R27's `record_decision` RAISES / `_record_decisions`
    continues.

    IT IS ALSO WHY THE OBVIOUS HALF-FIX IS WORSE THAN THE DEFECT. Changing the
    derivation to `proposal_count > unit_count` against the RAW count (measured
    by curation_4 C-1) makes a report carrying a duplicate-titled proposal
    derive 'pending' at EVERY stage INCLUDING full enactment: a permanent orphan
    on every offer surface, a permanent degrade-log entry, and an
    AgreementViolation that can never be registered -- breaking R22 on exactly
    the class R22 protects. The denominator has to be ADMISSIBILITY, and it is
    computed here and nowhere else.
    """
    proposals: List[Tuple[str, Dict[str, Any]]] = (
        [("enhancement", p) for p in (enhancements or []) if isinstance(p, dict)]
        + [("deprecation", p) for p in (deprecations or []) if isinstance(p, dict)])
    admissible: List[Tuple[str, str, Dict[str, Any]]] = []
    skipped: List[Dict[str, str]] = []
    seen_titles: Set[str] = set()
    for kind, p in proposals:
        title = str(p.get("title") or "").strip()
        if not title:
            skipped.append({"reason_code": "proposal_without_title",
                            "reason": f"a {kind} carries no title; it cannot "
                                      f"become a unit"})
            continue
        if title in seen_titles:
            skipped.append({"reason_code": "duplicate_title_on_report",
                            "title": title,
                            "reason": f"{title!r} appears twice; the second is "
                                      f"not a distinct unit"})
            continue
        seen_titles.add(title)
        admissible.append((kind, title, p))
    return admissible, skipped


def _derive_status(unit_count: int, open_count: int, enacted_count: int,
                   admissible_count: int, approved_unenacted: int) -> str:
    """Project derived state onto the STORED column's existing vocabulary
    ('pending' | 'drew_reviewed' | 'enacted'). The CHECK is not widened, so the
    projection must land inside it -- R9.

      * no units, but the report HOLDS PROPOSALS -> 'pending'. R20.
      * no units and no proposals -> 'drew_reviewed' (nothing was ever decidable)
      * any open unit           -> 'pending'
      * none open, but some unit APPROVED and not yet enacted
                                -> 'drew_reviewed'   R26'
      * none open, none approved-unenacted, >=1 enacted -> 'enacted'
      * none open, none enacted -> 'drew_reviewed'

    R20 -- WHY `proposal_count` IS A REQUIRED ARGUMENT, MEASURED.
    The previous body was `if unit_count == 0: return "drew_reviewed"` while the
    docstring three lines above it read: *"The row that would look different if
    this were false is `proposed_enhancements` / `proposed_deprecations` being
    non-empty."* The docstring NAMED the discriminator and the code branched on
    `unit_count` alone. On 2026-09-02 report #8 -- born by the production entry
    point with four proposals and zero units -- derived as `drew_reviewed`, a
    DECIDED state. The whole plan is to move the read side onto derived state, so
    that projection makes a report holding four undecided proposals INVISIBLE
    rather than merely miscounted: the migration built to stop decisions being
    eaten would have eaten a whole report.

    The argument is POSITIONAL AND REQUIRED, with no default. A default would
    restore the exact defect for every caller that forgot, silently. A caller that
    forgets now raises TypeError at the call site.

    R26' -- `approved_unenacted` IS REQUIRED FOR THE SAME REASON, AND IT IS NOT
    DERIVABLE FROM THE OTHER FOUR. `unit_count - open_count` is the DECIDED
    count; it cannot separate a DECLINED unit from an APPROVED-but-unenacted
    one, because both carry a closing event. Only the event vocabulary can,
    and that is what `approved_unenacted_counts()` reads. A default of 0 would
    silently restore the 'finished when ONE thing finished' reading for every
    caller that forgot -- the precise defect this parameter closes -- so there
    is none, and a stale 4-argument caller is a TypeError a harness can see.
    """
    if unit_count == 0:
        # THE DISCRIMINATOR THE OLD DOCSTRING NAMED AND THE OLD CODE IGNORED.
        return "pending" if admissible_count > 0 else "drew_reviewed"
    if admissible_count > unit_count:
        # R25 -- PARTIAL BIRTH. `unit_count == 0` is the ALL-OR-NOTHING case of a
        # more general one: a report can hold SOME units and still hold a
        # proposal that never became one. Measured 2026-09-02 on a manufactured
        # but product-reachable state: units=2 open=0 enacted=2 admissible=3 ->
        # 'enacted'. A report holding an undecided proposal derived a DECIDED
        # state, so it left every offer surface with work still on it -- the
        # exact sentence this build exists to make unsayable, arriving through
        # the partial case the zero-case branch cannot see.
        #
        # The comparison is against ADMISSIBLE proposals, never raw ones. See
        # `admissible_proposals`: against the raw count this branch would hold
        # FOREVER on any report carrying an untitled or duplicate-titled
        # proposal, which is an unclearable 'pending' and a worse defect than
        # the one it closes.
        return "pending"
    if open_count > 0:
        return "pending"
    if approved_unenacted > 0:
        # R26' -- FINISHED MEANS EVERY APPROVED UNIT FINISHED, NOT ONE OF THEM.
        # `enacted_count > 0` alone reads 'at least one thing was enacted' as
        # 'the report is done'. An 'approved' event is in CLOSING_OUTCOMES, so
        # it CLOSES the unit and `open_count` is structurally blind to a unit
        # the owner said YES to that the system has not yet enacted: the
        # report projected a TERMINAL state while it still owed work, and
        # every offer surface then stopped naming it.
        #
        # MEASURED ON THE WALKED CANARY BEFORE THIS BRANCH EXISTED: 0 of 8
        # reports carry an approved-unenacted unit, so this changes no
        # historical answer. It stops the FORWARD path -- an enactment run
        # that blocks or defers one proposal -- from reaching 'enacted' early.
        return "drew_reviewed"
    return "enacted" if enacted_count > 0 else "drew_reviewed"


def approved_unenacted_counts(conn: sqlite3.Connection,
                              report_id: Optional[int] = None) -> Dict[int, int]:
    """Units the owner APPROVED that carry no enacted event, per report. PUBLIC.

    R26' -- THE ONE SPELLING of "this report still owes an enactment". The
    shipping tree carried THREE independent spellings of the finished-predicate
    -- this module's `_derive_status`, and `monthly_self_eval._project_report_
    status` in BOTH its ledger and its no-ledger branch -- and a fourth site
    (`drew_review`) writes the stored column from one of them. Every spelling
    that can consult the ledger now consults this function, so the question has
    one place to be answered instead of three places to disagree.

    IT READS THE TWO BASE TABLES, NOT `v_decision_unit_state`. Callers reach
    this only after proving `decision_units` exists; a partially applied
    migration can hold the tables without the view, and a view-shaped failure
    would answer "nothing is approved-unenacted", which is the UNSAFE direction
    -- it lets a report reach 'enacted'. Depending only on what the caller has
    already checked removes that failure mode instead of handling it.

    IT TAKES THE CALLER'S CONNECTION ON PURPOSE. `monthly_self_eval.drew_review`
    calls this from inside its own open transaction, where the approval events
    it has just written are not yet committed. A function that opened its own
    read-only connection would not see them and would answer about the world as
    it stood before the review.

    A QUERY FAILURE RAISES; it does not return an empty dict. The safe direction
    is "this report may still owe something", and an empty dict says the
    opposite with the same confidence as a real answer.
    """
    sql = ("SELECT u.report_id, COUNT(*) FROM decision_units u "
           " WHERE EXISTS (SELECT 1 FROM decision_events e "
           "                WHERE e.unit_id = u.id AND e.outcome = 'approved') "
           "   AND NOT EXISTS (SELECT 1 FROM decision_events e "
           "                    WHERE e.unit_id = u.id AND e.outcome = 'enacted') "
           "   AND u.report_id IS NOT NULL")
    params: Tuple[Any, ...] = ()
    if report_id is not None:
        sql += " AND u.report_id = ?"
        params = (int(report_id),)
    sql += " GROUP BY u.report_id"
    try:
        return {int(r[0]): int(r[1]) for r in conn.execute(sql, params)}
    except sqlite3.Error as e:
        _degrade("approved_unenacted_counts", e, fallback=None)
        raise LedgerError(f"cannot tell which units are approved-but-unenacted: "
                          f"{type(e).__name__}: {e}") from e


def _admissible_counts(conn: sqlite3.Connection) -> Dict[int, int]:
    """ADMISSIBLE proposals per report -- the number of decision units this
    report is ENTITLED to. DENOMINATOR: every row of `self_eval_reports`.

    R25. This used to be `_proposal_counts`, returning the RAW length of the two
    JSON columns, and it fed both `_derive_status` and the orphan term. The birth
    path creates one unit per ADMISSIBLE proposal and enforces
    `present == admissible`; comparing units to RAW proposals therefore compares
    two different quantities. The rule is `admissible_proposals()` and it is
    spelled there ONCE -- this function only supplies the parsing and the
    degrade behaviour.

    A column that will not parse is counted as ONE, not zero, and the failure is
    RECORDED. Counting it zero would make an unparseable report derive as a
    DECIDED state and vanish from every offer surface -- the same shape as the
    defect this function exists to close, arriving through the error path. The
    safe direction is "there may be something undecided here", which keeps the
    report visible and noisy.
    """
    out: Dict[int, int] = {}
    for rid, enh, dep in conn.execute(
            "SELECT id, proposed_enhancements, proposed_deprecations "
            "  FROM self_eval_reports"):
        n = 0
        parsed: Dict[str, List[Dict[str, Any]]] = {"proposed_enhancements": [],
                                                   "proposed_deprecations": []}
        for col, raw in (("proposed_enhancements", enh),
                         ("proposed_deprecations", dep)):
            try:
                parsed[col] = _json_list(raw)
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                n += 1
                _degrade(f"_admissible_counts/report{int(rid)}/{col}", e, fallback=None)
        adm, _skipped = admissible_proposals(parsed["proposed_enhancements"],
                                             parsed["proposed_deprecations"])
        out[int(rid)] = n + len(adm)
    return out


def derive_report_state(db: Optional[str] = None) -> List[DerivedState]:
    """Derived state for every report, beside the stored column. READ-ONLY."""
    db_path = resolve_db(db)
    conn = _open_ro(db_path)
    try:
        if not _table_exists(conn, "v_report_derived_state"):
            raise LedgerError("migration 030 not applied: v_report_derived_state absent")
        # R9 -- THE AMNESTY IS KEYED ON THE EVIDENCE. Not on report_id: see
        # divergence_fingerprint(). A registration made when #5 held 3 open units
        # says nothing about #5 holding 4.
        registered = {
            str(r["divergence_fingerprint"]) for r in conn.execute(
                "SELECT divergence_fingerprint FROM decision_status_divergence")
        } if _table_exists(conn, "decision_status_divergence") else set()
        # R20. The view carries unit counts only; the discriminator lives in the
        # report's own proposal columns, so it is read here rather than inferred.
        props = _admissible_counts(conn)
        aue = approved_unenacted_counts(conn)     # R26', the ONE spelling
        out: List[DerivedState] = []
        for r in conn.execute("SELECT * FROM v_report_derived_state ORDER BY report_id"):
            rid = int(r["report_id"])
            derived = _derive_status(int(r["unit_count"]), int(r["open_units"]),
                                     int(r["enacted_units"]), props.get(rid, 0),
                                     aue.get(rid, 0))
            titles = tuple(
                x["proposal_title"] for x in conn.execute(
                    "SELECT proposal_title FROM v_decision_unit_state "
                    " WHERE report_id=? AND is_open=1 ORDER BY unit_id", (rid,))
            )
            fp = divergence_fingerprint(
                rid, r["stored_status"], derived, int(r["unit_count"]),
                int(r["open_units"]), int(r["enacted_units"]),
                int(r["discarded_units"]), titles)
            out.append(DerivedState(
                report_id=rid, stored_status=r["stored_status"], derived_status=derived,
                unit_count=int(r["unit_count"]), open_units=int(r["open_units"]),
                enacted_units=int(r["enacted_units"]),
                discarded_units=int(r["discarded_units"]),
                open_titles=titles,
                agrees=(derived == r["stored_status"]),
                registered_divergence=(fp in registered),
                fingerprint=fp,
            ))
        return out
    finally:
        conn.close()


def assert_status_agreement(db: Optional[str] = None) -> List[DerivedState]:
    """R9. Raise unless every report either AGREES or carries a REGISTERED
    historical divergence with its evidence.

    WHY IT IS NOT SIMPLY `derived == stored` FOR ALL SEVEN. Reports #3 and #5 are
    stored terminal while still holding undecided units -- #5's three discarded
    August decisions, and #3's never-decided deprecation. Those disagreements ARE
    the defect this ledger exists to prove. A derivation tuned until all seven
    agree would dissolve exactly the finding, which is the failure mode of the
    whole system: writing the state you wanted instead of deriving it from
    evidence. So the pre-existing divergences are REGISTERED WITH THEIR EVIDENCE
    by the migration, and this assertion fails loudly on any UNREGISTERED one --
    i.e. on any new one.
    """
    states = derive_report_state(db)
    bad = [s for s in states if not s.agrees and not s.registered_divergence]
    if bad:
        lines = [
            f"  report #{s.report_id}: stored={s.stored_status!r} "
            f"derived={s.derived_status!r} unit_count={s.unit_count} "
            f"open_units={s.open_units} enacted_units={s.enacted_units} "
            f"discarded_units={s.discarded_units} "
            f"open_titles={list(s.open_titles)}\n"
            f"      observed fingerprint {s.fingerprint} matches no registered "
            f"divergence. If report #{s.report_id} carries a registration, its "
            f"FACTS have changed since it was taken, so the amnesty no longer "
            f"applies to what is there now."
            for s in bad
        ]
        raise AgreementViolation(
            "derived report state disagrees with self_eval_reports.status on "
            f"{len(bad)} report(s), and the disagreement is NOT a registered "
            "historical divergence:\n" + "\n".join(lines))
    return states


# ---------------------------------------------------------------------------
# R12/R13/R14 -- ATTENTION. Separate subject, separate table, separate verbs.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttentionResult:
    fire: bool
    open_keys: Tuple[str, ...]
    unseen_keys: Tuple[str, ...]
    quiet_keys: Tuple[str, ...]
    fired_keys: Tuple[str, ...]
    reason: str
    count: int
    breakdown: Dict[str, int] = field(default_factory=dict)
    #: R4/P1. `False` means THE SET IS UNKNOWN, not that it is empty. An
    #: absent `self_eval_reports` is not an empty inbox -- it is a brain the
    #: self-eval machinery was never installed into, which is precisely what
    #: `claude_memory.py init` produces (measured: 11 tables, no
    #: self_eval_reports). Every consumer that RENDERS or WRITES must branch
    #: on this bit; a consumer that names its own output keys and omits it is
    #: where the bit dies.
    trusted: bool = True
    #: R6/U3. Carried through from `CountResult.unread` so the BANNER -- whose
    #: ledger path reads this object and not the CountResult -- can name the
    #: source that actually failed instead of guessing one.
    unread: Tuple[str, ...] = ()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    txt = str(s).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return None


def attention_signal(db: Optional[str] = None, *,
                     now: Optional[datetime] = None) -> AttentionResult:
    """R12. Whether the banner should fire, as a SET DIFFERENCE over open unit
    ids -- never a delta over their cardinality.

    A count cannot see a swap: close one unit and open another between two reads
    and the cardinality is invariant while the world changed. That exact lesson
    is already in the brain (#13744, self-caught 2026-08-10) and the live
    `should_ping` re-committed it as `changed = (total != last)`.

    THIS FUNCTION HAS NO WRITE PATH AT ALL. It opens the database read-only, so
    reading the signal cannot consume it. That is structural, not a convention:
    wiring the existing `should_ping(persist=True)` as-is would persist
    `count=1` on the first call while snoozed, and on snooze expiry
    `changed=False` would mean the ping never fires. Persistence here is an
    explicit verb (`acknowledge`), never a side effect of looking.

    R13. Quiet is per unit. The legacy GLOBAL snooze row is honoured only for
    units first seen at or before the instant it was set: a unit that arrived
    afterwards is never hidden by it. The live global snooze runs to 2026-10-01
    and was set 2026-09-01 14:27:35; report #7 arrived 2026-09-01 22:21:28. Under
    the old reading the envelope-bug failure would have been hidden for a month.
    """
    db_path = resolve_db(db)
    now = now or datetime.now()
    counts = unified_open_count(db_path)
    open_keys = list(counts.open_unit_keys)

    attention: Dict[str, sqlite3.Row] = {}
    legacy_snooze_until: Optional[datetime] = None
    legacy_snooze_set_at: Optional[datetime] = None
    first_seen: Dict[str, Optional[datetime]] = {}
    presentable: Dict[str, Optional[datetime]] = {}

    if Path(db_path).exists():
        try:
            conn = _open_ro(db_path)
        except sqlite3.Error as e:
            conn = _degrade("attention_signal/open", e, fallback=None)
        if conn is not None:
            try:
                if _table_exists(conn, "unit_attention"):
                    _cols = {c[1] for c in conn.execute(
                        "PRAGMA table_info(unit_attention)")}
                    for r in conn.execute("SELECT * FROM unit_attention"):
                        attention[r["unit_key"]] = r
                        first_seen[r["unit_key"]] = _parse_iso(r["first_seen_at"])
                        # PRESENTABLE = the later of "the decision arrived" and
                        # "the unit entered the attention set". See below.
                        _born = (_parse_iso(r["row_created_at"])
                                 if "row_created_at" in _cols else None)
                        _cands = [d for d in (first_seen[r["unit_key"]], _born)
                                  if d is not None]
                        presentable[r["unit_key"]] = max(_cands) if _cands else None
                if _table_exists(conn, "approval_ping_state"):
                    row = conn.execute(
                        "SELECT snooze_until, updated_at FROM approval_ping_state "
                        " WHERE scope='global'").fetchone()
                    if row is not None:
                        legacy_snooze_until = _parse_iso(row["snooze_until"])
                        legacy_snooze_set_at = _parse_iso(row["updated_at"])
            except sqlite3.Error as e:
                _degrade("attention_signal/read", e, fallback=None)
            finally:
                conn.close()

    # A unit is SEEN only if it was acknowledged individually. Acknowledgement is
    # per-unit-seen, never global -- R14: opening a surface must not consume the
    # change signal for units never scrolled to.
    unseen = [k for k in open_keys
              if not (attention.get(k) is not None and attention[k]["acknowledged_at"])]

    quiet: List[str] = []
    for k in open_keys:
        row = attention.get(k)
        until = _parse_iso(row["quiet_until"]) if row is not None else None
        if until is not None and until > now:
            quiet.append(k)
            continue
        # Legacy global snooze: bounded by when it was set. A unit with no
        # attention row at all has no recorded arrival, so it is treated as NEW
        # and is NOT hidden -- the conservative direction, and the one R13 names.
        #
        # AND THE BOUND IS PRESENTABILITY, NOT WALL-CLOCK ARRIVAL. A snooze
        # silences what it was GRANTED OVER, and Drew can only have granted it
        # over what the surface could show him. Measured on the real row: the
        # live global snooze was set 2026-09-01 14:27:35, at which moment NO
        # report was stored 'pending' (report #7 arrived at 22:21, eight hours
        # later) and no ledger existed. Report #5's three August-eaten decisions
        # and report #3's never-decided deprecation were not un-snoozed, they
        # were INVISIBLE -- and bounding by first_seen_at alone (their reports'
        # creation dates, in May and August) made all four QUIET until
        # 2026-10-01. Counted, unlistable, and unable to fire: the exact four
        # decisions this round exists to recover.
        #
        # RULER NOTE, stated in the required words: R13's letter is "a snooze
        # never hides a unit that ARRIVED after it", and by wall-clock arrival
        # these units did not arrive after it. THIS IS THEREFORE PARTLY A
        # PROPOSAL TO CHANGE THE RULER -- to say the bound is presentability,
        # not creation. It is implemented because the criterion's own traced
        # harm ("had the banner obeyed it, the envelope-bug failure would have
        # been hidden for a month") is precisely what the letter would cause here.
        if (legacy_snooze_until is not None and legacy_snooze_until > now
                and legacy_snooze_set_at is not None):
            seen_at = presentable.get(k, first_seen.get(k))
            if seen_at is not None and seen_at <= legacy_snooze_set_at:
                quiet.append(k)

    fired = [k for k in unseen if k not in set(quiet)]
    if not open_keys:
        reason = "no_open_units"
    elif fired:
        reason = "unseen_open_units"
    elif unseen:
        reason = "quieted"
    else:
        reason = "all_acknowledged"
    return AttentionResult(
        fire=bool(fired), open_keys=tuple(open_keys), unseen_keys=tuple(unseen),
        quiet_keys=tuple(quiet), fired_keys=tuple(fired), reason=reason,
        count=counts.total, breakdown=dict(counts.breakdown),
        unread=tuple(getattr(counts, "unread", ()) or ()),
        # R4/P2. THE SURFACE THE BANNER ACTUALLY READS. R2 created the
        # trust bit and R3 carried it through `unified_pending_report`,
        # but `build_banner`'s PRIMARY branch calls this function, which
        # had no trust field at all -- so on the tree under grade the
        # bit was never in the value the banner consumed.
        trusted=counts.trusted)


def _ensure_attention_rows(conn: sqlite3.Connection, keys: Sequence[str],
                           surfaces: Dict[str, str]) -> None:
    for k in keys:
        conn.execute(
            "INSERT INTO unit_attention (unit_key, surface) VALUES (?, ?) "
            "ON CONFLICT(unit_key) DO NOTHING",
            (k, surfaces.get(k, k.split(":", 1)[0])))


def _attention_write(db: Optional[str], keys: Optional[Sequence[str]],
                     sql: str, params_for) -> Dict[str, Any]:
    """The shared write path for the four attention verbs.

    R14 -- THE INVARIANT THIS FUNCTION EXISTS TO ENFORCE: every statement it can
    execute targets `unit_attention` and nothing else. No verb here can reach
    decision_units or decision_events, so a unit's decision state is byte-identical
    before and after any dismiss / snooze / defer / ack. The tests assert that by
    hashing the decision rows across the call.
    """
    if "unit_attention" not in sql:
        raise LedgerError("attention verbs may only write unit_attention")
    db_path = resolve_db(db)
    units = {u.key: u.surface for u in open_units(db_path)}
    targets = list(keys) if keys is not None else list(units)
    unknown = [k for k in targets if k not in units]
    conn = _open_rw(db_path)
    try:
        if not _table_exists(conn, "unit_attention"):
            raise LedgerError("migration 030 not applied: unit_attention absent")
        _ensure_attention_rows(conn, targets, units)
        for k in targets:
            conn.execute(sql, params_for(k))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "affected": targets, "unknown_keys": unknown}


def acknowledge(keys: Sequence[str], db: Optional[str] = None,
                *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """VERB 1 -- mark specific units seen. Per-unit, NEVER global (R14)."""
    ts = (now or datetime.now()).isoformat(timespec="seconds")
    return _attention_write(
        db, keys,
        "UPDATE unit_attention SET acknowledged_at=?, updated_at=datetime('now') "
        " WHERE unit_key=?",
        lambda k: (ts, k))


def dismiss(key: str, db: Optional[str] = None,
            *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """VERB 2 -- the cheapest verb, and the one that lies about nothing.

    R14: writes the ATTENTION record only -- a per-unit dismiss counter and an
    acknowledgement -- and NOTHING to the decision record. The Nth dismiss may
    change a unit's PRESENTATION; it never changes its state. This is also what
    keeps "dismissed nine times" distinguishable from "never seen".
    """
    ts = (now or datetime.now()).isoformat(timespec="seconds")
    return _attention_write(
        db, [key],
        "UPDATE unit_attention "
        "   SET dismiss_count = dismiss_count + 1, last_dismissed_at = ?, "
        "       acknowledged_at = COALESCE(acknowledged_at, ?), "
        "       updated_at = datetime('now') "
        " WHERE unit_key = ?",
        lambda k: (ts, ts, k))


def snooze(hours: float, db: Optional[str] = None,
           keys: Optional[Sequence[str]] = None,
           *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """VERB 3 -- quiet the units that exist NOW for `hours`.

    R13: the quiet is written onto the CURRENTLY-OPEN units only. A unit that
    arrives afterwards has no quiet_until, so it still fires. Silence granted over
    a set must not extend to members that join later.
    """
    base = now or datetime.now()
    until = None if hours is None or hours <= 0 else (base + timedelta(hours=float(hours))).isoformat(timespec="seconds")
    return _attention_write(
        db, keys,
        "UPDATE unit_attention SET quiet_until=?, updated_at=datetime('now') "
        " WHERE unit_key=?",
        lambda k: (until, k))


def defer(key: str, until: str, db: Optional[str] = None) -> Dict[str, Any]:
    """VERB 4 -- quiet ONE unit until an explicit instant. Attention only (R14)."""
    if _parse_iso(until) is None:
        raise LedgerError(f"defer requires a parseable instant, got {until!r}")
    return _attention_write(
        db, [key],
        "UPDATE unit_attention SET quiet_until=?, updated_at=datetime('now') "
        " WHERE unit_key=?",
        lambda k: (until, k))


# ---------------------------------------------------------------------------
# The live decision write path
# ---------------------------------------------------------------------------

def _write_failure_reason(conn: sqlite3.Connection, what: str, subject: str,
                          unit_id: int, outcome: str, evidence_source: str,
                          evidence_rowid: Optional[int],
                          exc: Exception) -> str:
    """R28 -- NAME THE CAUSE THE EVIDENCE SHOWS, NEVER THE ONE ASSUMED.

    A refused write into `decision_events` has several possible causes and the
    caller is told which one ACTUALLY happened: the row that already exists is
    looked up and QUOTED when there is one, and when there is not, SQLite's own
    message is passed through instead of being replaced by a guess.

    THE DEFECT THIS CLOSES, measured. Three sites wrote with `INSERT OR IGNORE`
    and read `rowcount == 0` as proof of a duplicate. `INSERT OR IGNORE`
    suppresses CHECK, NOT NULL and FOREIGN KEY violations identically, so a
    `CHECK (evidence_source IN (...))` failure was reported, confidently and
    specifically, as "an identical event already exists" -- against a unit
    created moments earlier with zero events. A wrong diagnosis in a refusal
    path is worse than no diagnosis: it sends the reader to look for a duplicate
    that is not there while their write is already lost.
    """
    dup = None
    try:
        dup = conn.execute(
            "SELECT id, decided_at, actor FROM decision_events "
            " WHERE unit_id = ? AND outcome = ? AND evidence_source = ? "
            "   AND evidence_rowid IS ?",
            (unit_id, outcome, evidence_source, evidence_rowid)).fetchone()
    except sqlite3.Error:
        dup = None
    if dup is not None:
        return (f"{what} for {subject} was NOT written — the identical event "
                f"already exists (decision_events.id={dup[0]}, decided_at="
                f"{dup[1]!r}, actor={dup[2]!r}). A decision that did not land "
                f"must never report ok.")
    n_events = -1
    try:
        n_events = int(conn.execute(
            "SELECT COUNT(*) FROM decision_events WHERE unit_id = ?",
            (unit_id,)).fetchone()[0])
    except sqlite3.Error:
        pass
    return (f"{what} for {subject} was NOT written and it is NOT a duplicate: "
            f"no event matches (unit_id={unit_id}, outcome={outcome!r}, "
            f"evidence_source={evidence_source!r}, evidence_rowid="
            f"{evidence_rowid!r}), and that unit carries {n_events} event(s) in "
            f"total. The database refused the write with "
            f"{type(exc).__name__}: {exc}. A decision that did not land must "
            f"never report ok.")


#: R27' -- THE PUBLIC NAME OF THE R28 DIAGNOSIS RULE ABOVE. `monthly_self_eval`
#: held a second copy of that body, AST-normalised identical to it. The copy is
#: gone and that file calls this name. It is a PUBLIC alias, not a direct import
#: of `_write_failure_reason`, because every other `_DU.` call in that file is
#: public API and a cross-module import of a leading-underscore name makes a
#: private spelling part of the contract by accident.
write_failure_reason = _write_failure_reason


def record_decision(unit: str, outcome: str, db: Optional[str] = None, *,
                    actor: Optional[str] = None, decided_at: Optional[str] = None,
                    evidence_source: str = "live",
                    evidence_rowid: Optional[int] = None,
                    detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Append one decision event. Append-only: never updates, never deletes.

    Raises on an unknown unit or an outcome outside the vocabulary -- a decision
    that cannot be attached to a unit must be visible, not absorbed.
    """
    if outcome not in OUTCOMES:
        raise LedgerError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")
    if actor is None:
        actor = _default_actor()
    db_path = resolve_db(db)
    ts = decided_at or datetime.now().isoformat(timespec="seconds")
    conn = _open_rw(db_path)
    try:
        row = conn.execute("SELECT id FROM decision_units WHERE unit_key=?", (unit,)).fetchone()
        if row is None:
            raise LedgerError(f"no decision unit with key {unit!r}")
        try:
            conn.execute(
                # R28 -- a PLAIN INSERT, never INSERT OR IGNORE. `OR IGNORE`
                # swallows EVERY constraint class -- CHECK, NOT NULL, FOREIGN
                # KEY, UNIQUE -- and every one of them yields rowcount 0. The
                # previous body read rowcount 0 and RAISED "an identical event
                # already exists": it named a cause it had never looked for.
                # Measured 2026-09-02: an `evidence_source` outside its CHECK
                # vocabulary was reported as a duplicate against a unit created
                # moments earlier carrying ZERO events. That is this build's
                # governing class -- a path reporting the state it ASSUMED
                # rather than the state the evidence shows -- inside the
                # ledger's own refusal, which is the single most expensive place
                # to be wrong, because it is the message a human reads while the
                # write is already lost.
                "INSERT INTO decision_events "
                "(unit_id, outcome, decided_at, actor, evidence_source, evidence_rowid, detail_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(row["id"]), outcome, ts, actor, evidence_source, evidence_rowid,
                 json.dumps(detail) if detail is not None else None))
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise LedgerError(_write_failure_reason(
                conn, "decision event", f"{unit!r} outcome={outcome!r} "
                f"source=({evidence_source},{evidence_rowid})",
                int(row["id"]), outcome, evidence_source, evidence_rowid, e)) from e
        conn.commit()
        return {"status": "ok", "unit_key": unit, "outcome": outcome, "decided_at": ts}
    finally:
        conn.close()


# ===========================================================================
# THE MIGRATION -- additive schema + an evidence-preserving backfill
# ===========================================================================

@dataclass
class MigrationResult:
    dry_run: bool
    units_created: int = 0
    events_created: int = 0
    attention_seeded: int = 0
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    loud_skips: List[Dict[str, Any]] = field(default_factory=list)
    divergences: List[Dict[str, Any]] = field(default_factory=list)
    per_report: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def render(self) -> str:
        out = [f"MIGRATION 030 — decision units  ({'DRY RUN' if self.dry_run else 'APPLIED'})",
               f"  units created        : {self.units_created}",
               f"  decision events      : {self.events_created}",
               f"  attention rows seeded: {self.attention_seeded}"]
        # R8: the skip is reported EXPLICITLY in the migration's output, never
        # swallowed. json.loads(...)["approved"] raises on report #2 -- and in
        # this codebase it raises inside a swallow.
        out.append(f"  LOUD SKIPS           : {len(self.loud_skips)}")
        for s in self.loud_skips:
            out.append(f"      !! report #{s['report_id']}  {s['reason_code']}: {s['reason']}")
        out.append(f"  UNBACKFILLABLE       : {len(self.exceptions)}")
        for e in self.exceptions:
            out.append(f"      -- {e['source_table']} row {e['source_rowid']} "
                       f"({e.get('ref')}): {e['reason_code']} — {e['reason']}")
        _new = sum(1 for d in self.divergences if d.get("newly_registered"))
        out.append(f"  DIVERGENCES: {len(self.divergences)} "
                   f"({_new} newly registered, "
                   f"{len(self.divergences) - _new} already on record)")
        for d in self.divergences:
            out.append(f"      ~~ report #{d['report_id']}: stored={d['stored_status']} "
                       f"derived={d['derived_status']} open={d['open_units']} "
                       f"{d['open_titles']}")
        out.append("  PER REPORT:")
        for p in self.per_report:
            out.append(f"      #{p['report_id']:>2} {p['month']:<8} stored={p['stored_status']:<14}"
                       f" units={p['units']:>2} events={p['events']:>2}"
                       f" open={p['open']:>2} enacted={p['enacted']:>2}"
                       f" discarded={p['discarded']:>2}")
        for n in self.notes:
            out.append(f"  note: {n}")
        return "\n".join(out)


def apply_schema(conn: sqlite3.Connection, schema_file: Optional[str] = None) -> None:
    """Execute the additive DDL. Raises on failure -- a migration that cannot
    build its own tables must never proceed quietly."""
    path = Path(schema_file) if schema_file else SCHEMA_FILE
    if not path.exists():
        raise LedgerError(f"schema file not found: {path}")
    sql = path.read_text(encoding="utf-8")
    if not _table_exists(conn, "metadata"):
        sql = re.sub(r"INSERT OR REPLACE INTO metadata.*?;", "", sql, flags=re.S)
    # R7/C4 -- `safety-005`. `conn.executescript()` ISSUES AN IMPLICIT COMMIT
    # BEFORE RUNNING ITS SCRIPT, so every statement it applies is already
    # committed when `migrate()`'s `if dry_run: conn.rollback()` runs. The data
    # rolls back; the TABLES DO NOT.
    #
    # MEASURED from the shipped CLI's safest-looking verb: `decision_units.py
    # migrate --db <copy>` with NO `--apply` took a 65-table brain to 72 tables,
    # `decision_units` PRESENT with 0 ROWS. One command, read-shaped, permanent.
    #
    # THE FIX IS THE ONE `safety-005` NAMES: the scaffolding a preview needs is
    # built INSIDE the transaction the preview rolls back. `execute()` does not
    # commit, so the DDL now lives and dies with its caller's transaction.
    # `sqlite3.complete_statement` is the same splitter `executescript` uses, so
    # multi-statement DDL, string literals containing semicolons and
    # `CREATE TRIGGER ... BEGIN ... END;` bodies split identically.
    #
    # VERIFY THIS BY EXECUTING IT, NEVER BY READING THE ROLLBACK BRANCH: run the
    # preview against a copy and diff `SELECT name FROM sqlite_master`. That is
    # `safety-005`'s own instruction, and it is there because the rollback
    # branch READS correct -- it was correct, and the tables landed anyway.
    def _has_sql(chunk: str) -> bool:
        """True when a chunk holds anything but whole-line `--` comments.

        `chunk.startswith("--")` IS NOT THIS TEST, and the difference is not
        cosmetic: 8 of this file's 21 chunks OPEN with a comment block and then
        carry a `CREATE TABLE`. Skipping on the first two characters dropped
        every table while creating every index, and the migration died on
        `no such table: main.decision_units`. Caught by running the preview and
        diffing the object list -- which is `safety-005`'s instruction and the
        reason it says never to verify this from the rollback branch.
        """
        return any(ln.strip() and not ln.strip().startswith("--")
                   for ln in chunk.split("\n"))

    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt, buf = buf.strip(), ""
            if _has_sql(stmt):
                conn.execute(stmt)
    tail = buf.strip()
    if _has_sql(tail):
        # A trailing fragment sqlite could not close is a MALFORMED schema file,
        # never something to run and hope. A migration that cannot build its own
        # tables must never proceed quietly (this function's own contract).
        raise LedgerError(
            f"schema file ends with an incomplete SQL statement "
            f"({len(tail)} chars, starting {tail[:60]!r}): {path}")


def _json_list(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array, got {type(data).__name__}")
    return [d for d in data if isinstance(d, dict)]


def _report_id_from_ref(ref: Optional[str]) -> Optional[int]:
    m = re.match(r"^self-eval:(\d+)$", str(ref or "").strip())
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class BirthResult:
    """What happened when a report's decision units were created at birth."""
    report_id: int
    proposals_seen: int
    admissible: int
    units_created: int
    units_present: int
    ledger_present: bool
    skipped: Tuple[Dict[str, str], ...] = ()
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id, "proposals_seen": self.proposals_seen,
            "admissible": self.admissible, "units_created": self.units_created,
            "units_present": self.units_present,
            "ledger_present": self.ledger_present,
            "skipped": [dict(s) for s in self.skipped], "reason": self.reason,
        }


def create_units_for_report(conn: sqlite3.Connection, report_id: int,
                            enhancements: Optional[Sequence[Dict[str, Any]]],
                            deprecations: Optional[Sequence[Dict[str, Any]]],
                            *, created_at: Optional[str] = None) -> BirthResult:
    """R19 -- BIRTH. One decision unit per proposal, ON THE CALLER'S CONNECTION.

    PATH  run_self_eval INSERT INTO self_eval_reports -> THIS -> the same commit
    ROW   SELECT COUNT(*) FROM decision_units WHERE report_id=?, read back inside
          the caller's still-open transaction before it commits.

    WHY A CONNECTION AND NOT A DB PATH. The only `INSERT OR IGNORE INTO
    decision_units` outside tests used to be inside `def migrate()` -- a MANUAL
    verb. A report born by the production entry point therefore carried zero
    units, and on 2026-09-02 report #8 was born with four proposals and none.
    Opening a second connection here would create the units in a SEPARATE
    transaction, so a crash between the two commits would leave exactly the state
    this exists to prevent. Taking the caller's connection means the report and
    its units commit together or not at all.

    THE INVARIANT, enforced rather than asserted: on return the ledger holds
    exactly one unit per ADMISSIBLE proposal for this report; otherwise this
    raises `LedgerError` and the caller's transaction -- which still holds the
    report INSERT -- must be rolled back.

    A proposal is INADMISSIBLE, and is skipped LOUDLY (never silently), when it
    carries no title, or repeats a title already used on this report. Both are
    the same vocabulary `migrate()` uses, so a report born today and a report
    backfilled from history are described in the same words.

    An un-migrated brain (no `decision_units` table) is NOT an error: the monthly
    self-eval must still run. It returns `ledger_present=False` with a `reason`,
    which the caller surfaces in its result -- a stated absence, not a silent
    skip.
    """
    rid = int(report_id)
    if not _table_exists(conn, "decision_units"):
        return BirthResult(
            report_id=rid, proposals_seen=0, admissible=0, units_created=0,
            units_present=0, ledger_present=False,
            reason=("migration 030 not applied: no decision_units table, so this "
                    "report is born without a ledger entry. Run "
                    "`decision_units.py migrate --apply`."))

    n_seen = len(list(enhancements or [])) + len(list(deprecations or []))

    # R25. The admissibility rule is spelled in `admissible_proposals` and
    # NOWHERE ELSE. It used to be spelled here, again in `migrate()`, and not at
    # all in the derivation -- which is how the derivation came to compare units
    # against RAW proposals while this function enforced `present == admissible`.
    _adm, skipped = admissible_proposals(enhancements, deprecations)
    for _s in skipped:
        _s.setdefault("report_id", str(rid))
    admissible = len(_adm)
    created = 0
    for kind, title, p in _adm:
        key = unit_key(SURFACE_SELF_EVAL, rid, title)
        cur = conn.execute(
            "INSERT OR IGNORE INTO decision_units "
            "(unit_key, surface, report_id, proposal_title, proposal_kind, "
            " proposal_json, piece_affected, suppression_key, first_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,COALESCE(?, datetime('now')))",
            (key, SURFACE_SELF_EVAL, rid, title, kind, json.dumps(p),
             p.get("piece_affected"),
             suppression_key(p.get("piece_affected"), title), created_at))
        created += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        # Attention rows are seeded here for the same reason units are: a unit
        # whose attention row appears only when `migrate()` is next run has a
        # first_seen_at that lies about when Drew was first shown it.
        if _table_exists(conn, "unit_attention"):
            conn.execute(
                "INSERT OR IGNORE INTO unit_attention (unit_key, surface, first_seen_at) "
                "VALUES (?, ?, COALESCE(?, datetime('now')))",
                (key, SURFACE_SELF_EVAL, created_at))

    present = int(conn.execute(
        "SELECT COUNT(*) FROM decision_units WHERE report_id=?", (rid,)).fetchone()[0])
    if present != admissible:
        raise LedgerError(
            f"birth invariant violated for report #{rid}: {admissible} admissible "
            f"proposal(s) but the ledger reads back {present} unit(s) inside the "
            f"same transaction. The report INSERT must be rolled back with them.")
    return BirthResult(report_id=rid, proposals_seen=n_seen, admissible=admissible,
                       units_created=created, units_present=present,
                       ledger_present=True, skipped=tuple(skipped))


def migrate(db: Optional[str] = None, *, dry_run: bool = True,
            schema_file: Optional[str] = None) -> MigrationResult:
    """Build decision units + their decision history from the existing records.

    THE THREE WAYS REPORT #5 COULD BE CORRUPTED BY A *CORRECT* MIGRATION, AND HOW
    EACH IS REFUSED HERE (VAEL R4):
      (i)   backfill from drew_approved alone -> "#5: Drew approved one thing",
            and the loss is erased. REFUSED: approval_audit_log is read FIRST and
            drew_approved only fills gaps, so the three dropped commands survive
            as their own events.
      (ii)  backfill from the audit log and MERGE the four titles into the
            report -> "#5: Drew approved four things", asserting something that
            never happened. REFUSED: each audit row becomes its OWN event with
            its own outcome; three are `discarded`, and nothing merges them.
      (iii) key the unit on (metric, work) -> report #4's successful
            "Activate L3-C7-cross-tier-bridges" absorbs report #5's discarded
            one. REFUSED: units are keyed on (report, proposal); the (metric,
            work) pair is stored as `suppression_key`, non-unique, never an
            identity.
    And fourth, mechanically: drew_approved has at least two incompatible shapes
    across the seven rows. Report #2 is {"empty_report_dismissed": ...} with no
    `approved` key and report #7 is NULL. Both are handled EXPLICITLY below and
    the first is reported as a LOUD SKIP -- R8.
    """
    db_path = resolve_db(db)
    res = MigrationResult(dry_run=dry_run)
    conn = _open_rw(db_path)
    try:
        conn.execute("BEGIN")
        # THE REGISTRY IS CLOSED BY THE MIGRATION HAVING ALREADY RUN, never by
        # the registry happening to hold a row. A brain whose historical
        # migration found ZERO divergences leaves it empty, and `bool(
        # _preexisting)` then re-opens registration forever -- so the FIRST new
        # divergence on such a brain is absorbed by a public CLI verb. That
        # brain shape is not hypothetical: a second machine's brain held three reports
        # all 'pending', the staging trees stop at migration 021, and the SQL
        # header's own rollback returns the live brain to it.
        #
        # THE CLASS: a one-time act must never be gated on the state it
        # produces. This must be read BEFORE apply_schema, because the schema is
        # itself the thing whose prior existence is the question.
        _ledger_preexisted = (
            _table_exists(conn, "decision_units")
            and conn.execute("SELECT COUNT(*) FROM decision_units").fetchone()[0] > 0)
        apply_schema(conn, schema_file)

        reports = conn.execute(
            "SELECT id, month, status, created_at, proposed_enhancements, "
            "       proposed_deprecations, drew_approved "
            "  FROM self_eval_reports ORDER BY id").fetchall()

        # ---- 1. UNITS -------------------------------------------------------
        units_by_rt: Dict[Tuple[int, str], int] = {}
        for r in reports:
            rid = int(r["id"])
            proposals: List[Tuple[str, Dict[str, Any]]] = []
            for column, kind in (("proposed_enhancements", "enhancement"),
                                 ("proposed_deprecations", "deprecation")):
                try:
                    proposals += [(kind, p) for p in _json_list(r[column])]
                except (ValueError, json.JSONDecodeError) as e:
                    res.loud_skips.append({
                        "report_id": rid, "reason_code": f"malformed_{column}",
                        "reason": f"{type(e).__name__}: {e}"})
            # R25. The THIRD spelling of the admissibility rule used to live
            # here. It now lives in `admissible_proposals` only, so a report
            # born today and a report backfilled from history are admitted by
            # the same code and not merely by the same words.
            _adm, _skips = admissible_proposals(
                [p for k, p in proposals if k == "enhancement"],
                [p for k, p in proposals if k == "deprecation"])
            for _s in _skips:
                res.loud_skips.append({"report_id": rid, **_s})
            for kind, title, p in _adm:
                key = unit_key(SURFACE_SELF_EVAL, rid, title)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO decision_units "
                    "(unit_key, surface, report_id, proposal_title, proposal_kind, "
                    " proposal_json, piece_affected, suppression_key, first_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, SURFACE_SELF_EVAL, rid, title, kind, json.dumps(p),
                     p.get("piece_affected"),
                     suppression_key(p.get("piece_affected"), title),
                     r["created_at"]))
                res.units_created += cur.rowcount if cur.rowcount > 0 else 0
                row = conn.execute("SELECT id FROM decision_units WHERE unit_key=?",
                                   (key,)).fetchone()
                units_by_rt[(rid, title)] = int(row["id"])

        # ---- 2. EVENTS FROM THE AUDIT LOG (the primary, per-command record) --
        def add_event(unit_id: int, outcome: str, when: str, source: str,
                      rowid: Optional[int], detail: Optional[Dict[str, Any]] = None) -> None:
            cur = conn.execute(
                "INSERT OR IGNORE INTO decision_events "
                "(unit_id, outcome, decided_at, actor, evidence_source, evidence_rowid, detail_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (unit_id, outcome, when, _default_actor(), source, rowid,
                 json.dumps(detail) if detail else None))
            if cur.rowcount > 0:
                res.events_created += 1

        def add_exception(table: str, rowid: int, ref: Optional[str],
                          code: str, reason: str, detail: Any = None) -> None:
            conn.execute(
                "INSERT OR IGNORE INTO decision_backfill_exceptions "
                "(source_table, source_rowid, ref, reason_code, reason, detail_json) "
                "VALUES (?,?,?,?,?,?)",
                (table, rowid, ref, code, reason,
                 json.dumps(detail) if detail is not None else None))
            res.exceptions.append({"source_table": table, "source_rowid": rowid,
                                   "ref": ref, "reason_code": code, "reason": reason})

        enacted_rows = conn.execute(
            "SELECT id, report_id, proposal_title, status, enacted_at, enacted_memory_id "
            "  FROM self_eval_enacted_proposals ORDER BY id").fetchall() \
            if _table_exists(conn, "self_eval_enacted_proposals") else []
        enacted_index: Dict[Tuple[int, str], sqlite3.Row] = {
            (int(e["report_id"]), e["proposal_title"]): e
            for e in enacted_rows if e["status"] == "enacted"}
        audit_enacted_units: Set[int] = set()

        if _table_exists(conn, "approval_audit_log"):
            audits = conn.execute(
                "SELECT id, surface, ref, action, decision_detail, outcome_status, "
                "       outcome_json, created_at "
                "  FROM approval_audit_log WHERE surface=? ORDER BY id",
                (SURFACE_SELF_EVAL,)).fetchall()
            for a in audits:
                aid = int(a["id"])
                rid = _report_id_from_ref(a["ref"])
                try:
                    detail = json.loads(a["decision_detail"] or "{}")
                except (ValueError, json.JSONDecodeError) as e:
                    add_exception("approval_audit_log", aid, a["ref"],
                                  "malformed_decision_detail", f"{type(e).__name__}: {e}")
                    continue
                titles = detail.get("titles") if isinstance(detail, dict) else None
                if not titles:
                    # R7 -- row 9 is {"titles": null, "ids": null} against the
                    # empty report #6. There is NO title to attach it to, so it
                    # is recorded UNBACKFILLABLE and never guessed.
                    add_exception(
                        "approval_audit_log", aid, a["ref"], "unbackfillable_no_titles",
                        "the audit row names no proposal title, so it cannot be "
                        "attributed to a decision unit; guessing one would fabricate "
                        "a decision the owner never made",
                        {"decision_detail": detail, "outcome_status": a["outcome_status"]})
                    continue
                oj = str(a["outcome_json"] or "")
                for title in titles:
                    uid = units_by_rt.get((rid, title)) if rid is not None else None
                    if uid is None:
                        add_exception(
                            "approval_audit_log", aid, a["ref"], "title_not_a_proposal",
                            f"{title!r} is not a proposal on report #{rid}; the surface "
                            f"handed the owner a REPORT title where a PROPOSAL title was "
                            f"required (the same defect class as the empty-titles row)",
                            {"title": title, "outcome_status": a["outcome_status"]})
                        continue
                    if a["outcome_status"] == "ok":
                        if a["action"] == "reject":
                            add_event(uid, "declined", a["created_at"],
                                      "approval_audit_log", aid)
                        else:
                            ep = enacted_index.get((rid, title))
                            if ep is not None:
                                add_event(uid, "enacted", a["created_at"],
                                          "approval_audit_log", aid,
                                          {"corroborated_by":
                                           {"table": "self_eval_enacted_proposals",
                                            "rowid": int(ep["id"]),
                                            "enacted_memory_id": ep["enacted_memory_id"]}})
                                audit_enacted_units.add(uid)
                            else:
                                add_event(uid, "approved", a["created_at"],
                                          "approval_audit_log", aid)
                    elif "already reviewed" in oj:
                        # THE 2026-08-02 LOSS, PRESERVED. Drew decided; the
                        # per-report status gate discarded it into a log nobody
                        # reads. The unit stays OPEN.
                        add_event(uid, "discarded", a["created_at"],
                                  "approval_audit_log", aid,
                                  {"error": "already reviewed",
                                   "note": "the owner decided; the per-report gate dropped it"})
                    else:
                        add_exception(
                            "approval_audit_log", aid, a["ref"],
                            "audit_error_not_a_decision",
                            f"outcome_status={a['outcome_status']!r} and the error is "
                            f"not 'already reviewed', so this row records a FAILED "
                            f"COMMAND, not a decision",
                            {"title": title, "outcome_json": oj[:400]})

        # ---- 3. EVENTS FROM ENACTED PROPOSALS (pre-audit-table history) ------
        for e in enacted_rows:
            rid, title = int(e["report_id"]), e["proposal_title"]
            uid = units_by_rt.get((rid, title))
            if uid is None:
                add_exception("self_eval_enacted_proposals", int(e["id"]),
                              f"self-eval:{rid}", "enacted_title_not_a_proposal",
                              f"{title!r} was enacted but is not a proposal on report "
                              f"#{rid}", {"status": e["status"]})
                continue
            if e["status"] == "enacted" and uid not in audit_enacted_units:
                # Report #3 predates approval_audit_log (created 2026-06-11;
                # report #3 enacted 2026-06-05), so this IS its primary evidence.
                # `or e["report_id"]` here would put an INTEGER report id into
                # decision_events.decided_at (an event stamped '3'), which is NOT
                # NULL TEXT and which every consumer parses as an instant. Latent
                # today -- all 9 live rows have a non-null enacted_at -- and wrong
                # the first time one does not. The report's own creation instant
                # is the honest stand-in, and it is recorded as such.
                _when = e["enacted_at"]
                if not _when:
                    _when = conn.execute(
                        "SELECT created_at FROM self_eval_reports WHERE id=?",
                        (rid,)).fetchone()[0]
                add_event(uid, "enacted", _when,
                          "self_eval_enacted_proposals", int(e["id"]),
                          {"enacted_memory_id": e["enacted_memory_id"],
                           "decided_at_source": ("enacted_at" if e["enacted_at"]
                                                 else "self_eval_reports.created_at "
                                                      "(enacted_at was NULL)")})

        # ---- 4. drew_approved, LAST and GAP-FILLING ONLY ---------------------
        for r in reports:
            rid = int(r["id"])
            raw = r["drew_approved"]
            if raw is None:
                res.notes.append(f"report #{rid}: drew_approved is NULL — no review "
                                 f"recorded (not an error)")
                continue
            try:
                data = json.loads(raw)
            except (ValueError, json.JSONDecodeError) as e:
                res.loud_skips.append({
                    "report_id": rid, "reason_code": "malformed_drew_approved_json",
                    "reason": f"{type(e).__name__}: {e}"})
                continue
            if not isinstance(data, dict) or "approved" not in data:
                # R8 -- THE LOUD SKIP. `json.loads(...)["approved"]` raises on
                # report #2, and in this codebase it raises inside a swallow.
                # Here it is reported in the migration's output instead.
                res.loud_skips.append({
                    "report_id": rid, "reason_code": "drew_approved_missing_approved_key",
                    "reason": (f"shape is {sorted(data) if isinstance(data, dict) else type(data).__name__}"
                               f" — no 'approved' key; this row records a hand-written "
                               f"dismissal, not a review, and NO decision is inferred from it")})
                continue
            for entry in (data.get("approved") or []):
                title = entry.get("title") if isinstance(entry, dict) else entry
                uid = units_by_rt.get((rid, str(title)))
                if uid is None:
                    add_exception("self_eval_reports", rid, f"self-eval:{rid}",
                                  "approved_title_not_a_proposal",
                                  f"{title!r} in drew_approved.approved is not a "
                                  f"proposal on report #{rid}")
                    continue
                already = conn.execute(
                    "SELECT 1 FROM decision_events WHERE unit_id=? AND outcome IN "
                    "('approved','enacted','declined')", (uid,)).fetchone()
                if not already:
                    add_event(uid, "approved",
                              str(data.get("reviewed_at") or r["created_at"]),
                              "drew_approved", rid)
            for entry in (data.get("rejected") or []):
                title = entry.get("title") if isinstance(entry, dict) else entry
                uid = units_by_rt.get((rid, str(title)))
                if uid is None:
                    add_exception("self_eval_reports", rid, f"self-eval:{rid}",
                                  "rejected_title_not_a_proposal",
                                  f"{title!r} in drew_approved.rejected is not a "
                                  f"proposal on report #{rid}")
                    continue
                already = conn.execute(
                    "SELECT 1 FROM decision_events WHERE unit_id=? AND outcome IN "
                    "('approved','enacted','declined')", (uid,)).fetchone()
                if not already:
                    add_event(uid, "declined",
                              str(data.get("reviewed_at") or r["created_at"]),
                              "drew_approved", rid)

        # ---- 5. ATTENTION SEED ----------------------------------------------
        # first_seen_at is the REPORT's creation instant, not migration time:
        # a decision outstanding since 2026-08-01 arrived then, and dating it
        # today would let the live global snooze (set 2026-09-01 14:27) look like
        # it post-dated units it actually predates.
        for row in conn.execute(
                "SELECT v.unit_key, r.created_at FROM v_decision_unit_state v "
                "  JOIN self_eval_reports r ON r.id = v.report_id WHERE v.is_open=1"):
            cur = conn.execute(
                "INSERT OR IGNORE INTO unit_attention (unit_key, surface, first_seen_at) "
                "VALUES (?, ?, ?)",
                (row["unit_key"], SURFACE_SELF_EVAL, row["created_at"]))
            res.attention_seeded += cur.rowcount if cur.rowcount > 0 else 0

        # ---- 6. REGISTER PRE-EXISTING DIVERGENCES (R9) ----------------------
        # REGISTRATION IS A ONE-TIME HISTORICAL ACT, NOT AN IDEMPOTENT VERB.
        # `migrate --apply` is a PUBLIC CLI verb. Reproduced by execution on a
        # throwaway: create a fresh divergence on report #4, watch
        # assert_status_agreement() fire correctly, re-run `migrate --apply`, and
        # the assertion went silent -- the verb had registered the finding. So:
        # once the LEDGER exists, registration is CLOSED -- see
        # `_ledger_preexisted` above. Keying it on the registry being non-empty
        # was the same defect one level up: on a brain whose first migration
        # found zero divergences the registry stays empty and the gate never
        # closes. A divergence whose fingerprint is not already registered is a
        # FINDING and is raised, never written. INSERT OR IGNORE (not OR
        # REPLACE) means a pre-existing row is never overwritten with newer
        # facts either -- if #5's facts change, its old registration stays,
        # matches nothing, and the assertion fires.
        _preexisting = {int(x["report_id"]): str(x["divergence_fingerprint"])
                        for x in conn.execute(
                            "SELECT report_id, divergence_fingerprint "
                            "  FROM decision_status_divergence")}
        # The registry is closed by the LEDGER having pre-existed this run --
        # NOT by the registry holding a row. `bool(_preexisting)` alone is the
        # gate keyed on its own product; it is kept only as a belt-and-braces
        # OR term for a brain whose decision_units table was dropped while its
        # divergence registry survived.
        _registry_closed = _ledger_preexisted or bool(_preexisting)
        _post_registration: List[Dict[str, Any]] = []
        _props = _admissible_counts(conn)        # R20/R25, same discriminator
        _aue = approved_unenacted_counts(conn)   # R26', same one spelling
        for r in conn.execute("SELECT * FROM v_report_derived_state ORDER BY report_id"):
            rid = int(r["report_id"])
            derived = _derive_status(int(r["unit_count"]), int(r["open_units"]),
                                     int(r["enacted_units"]), _props.get(rid, 0),
                                     _aue.get(rid, 0))
            stored = r["stored_status"]
            titles = [x["proposal_title"] for x in conn.execute(
                "SELECT proposal_title FROM v_decision_unit_state "
                " WHERE report_id=? AND is_open=1 ORDER BY unit_id", (rid,))]
            if derived != stored:
                fp = divergence_fingerprint(
                    rid, stored, derived, int(r["unit_count"]), int(r["open_units"]),
                    int(r["enacted_units"]), int(r["discarded_units"]), titles)
                if _registry_closed and _preexisting.get(rid) != fp:
                    _post_registration.append({
                        "report_id": rid, "stored_status": stored,
                        "derived_status": derived, "open_units": int(r["open_units"]),
                        "open_titles": titles, "observed_fingerprint": fp,
                        "registered_fingerprint": _preexisting.get(rid)})
                    continue
                ev_rows = [int(x["id"]) for x in conn.execute(
                    "SELECT e.id FROM decision_events e JOIN decision_units u "
                    "  ON u.id=e.unit_id WHERE u.report_id=? AND e.outcome='discarded'",
                    (rid,))]
                evidence = (
                    f"report #{rid} is stored {stored!r} while holding "
                    f"{int(r['open_units'])} undecided unit(s). "
                    + (f"discarded decision_events rowids={ev_rows}. " if ev_rows else
                       "no discarded events: these units were never decided at all. ")
                    + "Registered rather than dissolved: tuning the derivation until "
                      "it agrees would erase the finding the ledger exists to prove.")
                _cur = conn.execute(
                    "INSERT OR IGNORE INTO decision_status_divergence "
                    "(report_id, stored_status, derived_status, unit_count, "
                    " open_units, enacted_units, discarded_units, open_titles, "
                    " divergence_fingerprint, evidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rid, stored, derived, int(r["unit_count"]), int(r["open_units"]),
                     int(r["enacted_units"]), int(r["discarded_units"]),
                     json.dumps(titles), fp, evidence))
                res.divergences.append({
                    "report_id": rid, "stored_status": stored,
                    "derived_status": derived, "open_units": int(r["open_units"]),
                    "open_titles": titles, "fingerprint": fp, "evidence": evidence,
                    # On a RE-RUN the INSERT OR IGNORE writes nothing, and the
                    # old render still announced "REGISTERED DIVERGENCES: 2" --
                    # output claiming an act that did not occur, which is the
                    # class this whole build exists to end, in miniature.
                    "newly_registered": _cur.rowcount > 0})
            month = conn.execute("SELECT month FROM self_eval_reports WHERE id=?",
                                 (rid,)).fetchone()["month"]
            res.per_report.append({
                "report_id": rid, "month": month, "stored_status": stored,
                "derived_status": derived, "units": int(r["unit_count"]),
                "open": int(r["open_units"]), "enacted": int(r["enacted_units"]),
                "discarded": int(r["discarded_units"]),
                "events": int(conn.execute(
                    "SELECT COUNT(*) FROM decision_events e JOIN decision_units u "
                    "  ON u.id=e.unit_id WHERE u.report_id=?", (rid,)).fetchone()[0]),
            })

        if _post_registration:
            # A dry run raises too: the point is that a NEW divergence is a
            # finding on every path, and a run that would have registered one
            # writes nothing at all (the handler below rolls back).
            raise AgreementViolation(
                f"{len(_post_registration)} report(s) diverged AFTER the "
                f"historical registration was taken. A new divergence is a "
                f"FINDING, not a registration, and this verb will not silence "
                f"it:\n" + "\n".join(
                    f"  report #{d['report_id']}: stored={d['stored_status']!r} "
                    f"derived={d['derived_status']!r} open_units={d['open_units']} "
                    f"open_titles={d['open_titles']}\n"
                    f"      observed fingerprint   {d['observed_fingerprint']}\n"
                    f"      registered fingerprint {d['registered_fingerprint']}"
                    for d in _post_registration))
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return res
    except BaseException:
        conn.rollback()
        raise            # a failed migration is LOUD; it never half-lands quietly
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Voidloom G1 decision ledger")
    ap.add_argument("verb", choices=["migrate", "count", "state", "attention",
                                     "units", "assert"])
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="migrate: actually write (default is a DRY RUN)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.verb == "migrate":
        r = migrate(a.db, dry_run=not a.apply)
        print(json.dumps(r.__dict__, indent=2, default=str) if a.json else r.render())
        return 0
    if a.verb == "count":
        c = unified_open_count(a.db)
        print(json.dumps({"total": c.total, "breakdown": c.breakdown,
                          "detail": c.detail, "open_unit_keys": list(c.open_unit_keys),
                          "degraded": list(c.degraded)}, indent=2))
        return 0
    if a.verb == "units":
        for u in open_units(a.db):
            print(f"  {u.key}  [{u.surface}] {u.title}"
                  + (f"   (discarded {u.discarded_count}x)" if u.discarded_count else ""))
        return 0
    if a.verb == "state":
        for s in derive_report_state(a.db):
            flag = "OK " if s.agrees else ("REG" if s.registered_divergence else "!! ")
            print(f"  {flag} #{s.report_id}  stored={s.stored_status:<14} "
                  f"derived={s.derived_status:<14} units={s.unit_count} "
                  f"open={s.open_units} {list(s.open_titles)}")
        return 0
    if a.verb == "attention":
        r = attention_signal(a.db)
        print(json.dumps({"fire": r.fire, "reason": r.reason, "count": r.count,
                          "breakdown": r.breakdown, "open": list(r.open_keys),
                          "unseen": list(r.unseen_keys), "quiet": list(r.quiet_keys),
                          "fired": list(r.fired_keys)}, indent=2))
        return 0
    assert_status_agreement(a.db)
    print("R9 OK — derived report state agrees with self_eval_reports.status "
          "(or the divergence is registered with its evidence).")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

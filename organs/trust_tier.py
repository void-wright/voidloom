#!/usr/bin/env python3
"""Trust-Tier Keystone — a DERIVED, read-time, PURE trust score over EXISTING columns.

``compute_trust(conn, row_or_id, provenance_summary=None) -> float in [0, 1]``

The trust score RANKS what the brain reaches for first; it NEVER bars a door.
A higher trust simply orders a memory ahead of a lower-trust one when two
sources disagree (the belief engine's contradiction-authority) or when a new
observation must clear a surprise bar (the surprise engine's novelty gate).
Both consumers hard-block on:

``edge_authority(conn, a_id, b_id) -> float = min(compute_trust(A), compute_trust(B))``

DESIGN INVARIANTS (the reason this is a function, not a column):
  * NO new column. A stored trust score rots the instant any input column
    changes (a new derivative cites the memory, foundation is re-scored, a
    citation is added). It is recomputed at read time, every time, so it can
    never disagree with the substrate it summarizes.
  * NO model. NO new pip dependency. Stdlib (re, json, sqlite3) + the passed
    connection only.
  * FAIL-GRACEFUL. Any internal error degrades to a sane neutral default
    (``DEFAULT_TRUST``) and NEVER raises into a consumer. The keystone must
    not be able to take down the belief engine or the recall path.

SCORING MODEL — a FLOOR plus a HEADROOM fill, then a CEILING lift:

  score = clamp01( foundation_lift * ( floor + (1 - floor) * headroom ) )

  1. Authorship FLOOR      — establishes the MINIMUM the score may take. A
                             human/manual-authored memory sits on a high floor;
                             an auto-derived memory's floor is the confidence of
                             the derivation that produced it (or a kind prior).
                             This is the dominant signal: a Drew-authored truth
                             is trustworthy even with zero corroboration, so it
                             can never be out-ranked by a well-cited derivative.
  2. Foundation LIFT       — a high ``foundation_score`` multiplies the CEILING
                             up (foundation memories are trust anchors). NULL
                             foundation is OMITTED (factor 1.0), never a penalty.
  3-5. HEADROOM fill       — self-confidence, corroboration, and the
                             verified-citation bonus are blended (weighted) into
                             a single ``headroom`` in [0, 1] that fills the gap
                             between the floor and 1.0. They can only ADD on top
                             of the floor; they never drag a trusted memory down.
         3. Self-confidence  — the memory's own stored ``confidence``.
         4. Corroboration    — how many derivatives cite this memory as a source
                               (squashed c/(c+k)); optionally trust-weighted by
                               the citers (bounded recursion, depth 1, no cycles).
         5. Citation bonus   — 1.0 when the content carries a traceable ``[n]``
                               reference, URL, or ``lines NN-NN``; else 0.0.
                               Never required (the brain holds much un-cited but
                               authored truth).

This module is import-safe and side-effect-free at import time.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, Optional, Union

__all__ = [
    "TRUST_WEIGHTS",
    "DEFAULT_TRUST",
    "UNTRUSTED_SOURCE_CEILING",
    "compute_trust",
    "edge_authority",
    "_trust_components",
]

# ----------------------------------------------------------------------------
# Configuration — every tunable in one place (see generation_report.md for the
# rationale tying each number to the live-corpus distribution).
# ----------------------------------------------------------------------------

#: Weights for the HEADROOM fill — the three components that add on top of the
#: authorship floor. ``authorship`` is NOT a headroom weight: it is the FLOOR
#: itself (the dominant signal). Foundation is a separate CEILING multiplier.
#: The three headroom weights are renormalized over whichever are present, so a
#: convex blend is preserved. Self-confidence dominates the headroom; the
#: traceable-citation bonus is intentionally small (never required).
TRUST_WEIGHTS: Dict[str, float] = {
    "authorship": 0.45,       # the FLOOR (reported for transparency; applied as a floor, not a headroom weight)
    "self_confidence": 0.50,  # headroom: the memory's own stored confidence (dominant fill)
    "corroboration": 0.35,    # headroom: how often it is cited as a source
    "citation_bonus": 0.15,   # headroom: small traceable-source reward
}

#: The subset of TRUST_WEIGHTS that fills the headroom (excludes the floor).
_HEADROOM_KEYS = ("self_confidence", "corroboration", "citation_bonus")

#: A neutral fallback used whenever a computation cannot be completed.
#: Chosen at the corpus median region so a failure neither buries nor inflates
#: a memory: it sits mid-pack and lets the consumer proceed (trust RANKS).
DEFAULT_TRUST: float = 0.5

# --- Authorship floors / priors --------------------------------------------

#: Floor for a memory we are confident a human authored directly.
AUTHORED_FLOOR: float = 0.85
#: Floor for an authored memory whose source_context is an auto-tag (weaker
#: authorship evidence, but still NOT a derivation).
AUTHORED_WEAK_FLOOR: float = 0.78
#: Per-kind prior used to floor a derived memory when its derivation carries no
#: explicit confidence. Reflections/distillations are more trustworthy than raw
#: dream synthesis; consolidations sit in the middle.
KIND_DERIVED_PRIOR: Dict[str, float] = {
    "reflection": 0.60,
    "procedural": 0.58,
    "semantic": 0.52,
    "episodic": 0.48,
}
#: Fallback derived prior when kind is unknown/missing.
DERIVED_PRIOR_DEFAULT: float = 0.50

# --- Untrusted-source ceiling (provenance-blind defense-in-depth) ------------

#: A CEILING the final trust may not exceed when the memory carries the
#: immutable ``untrusted_source=1`` birth-mark (the Warden's anti-laundering
#: stamp on web-sourced / auto-harvested content). compute_trust was previously
#: provenance-BLIND: it never read ``untrusted_source``, so an untrusted row
#: whose ``derived_from`` was absent (or stripped by a re-laundering write) could
#: still float to the AUTHORED_FLOOR (0.85) and out-rank a Drew-authored memory
#: (~0.925). This ceiling makes the birth-mark BITE in ranking on its own — even
#: when the derived_from sentinel and a low confidence are missing. It is the
#: structural floor of the defense: the derived_from sentinel + low confidence
#: (set by the Warden / the dream-pipeline taint-inheritance seal) push trust
#: WELL below this, but a row that somehow keeps only the immutable flag still
#: cannot exceed UNTRUSTED_SOURCE_CEILING. Chosen below AUTHORED_WEAK_FLOOR
#: (0.78) and far below the ~0.925 Drew baseline so untrusted content can never
#: rank at or above authored truth; chosen at/above DEFAULT_TRUST (0.5) so a
#: ceiling alone does not BURY otherwise-corroborated untrusted content (trust
#: RANKS, it never bars). The sentinel+confidence path lands lower still.
UNTRUSTED_SOURCE_CEILING: float = 0.70

#: transformation_type semantics: which derivations should be treated as
#: AUTHORED-high (a human did the deriving) vs genuinely auto-derived.
AUTHORED_TRANSFORMATIONS = frozenset({"manual_derivation"})
DERIVED_TRANSFORMATIONS = frozenset({
    "consolidation", "dream_synthesis", "edge_inference",
    "reflection", "distillation", "council_deliberation",
})

# --- Foundation lift ---------------------------------------------------------

#: Foundation memories are the strongest trust anchors in the system, so a high
#: ``foundation_score`` LIFTS THE FLOOR (it raises where the floor sits, which in
#: turn raises the ceiling the headroom fills toward). The floor is lifted a
#: fraction ``FOUNDATION_MAX_LIFT * foundation_score`` of the way from the
#: authorship floor toward 1.0. This is monotone and NON-saturating (it never
#: pins to 1.0 unless the floor already is), and it is never a penalty: NULL or
#: zero foundation lifts the floor by 0. Set high (0.60) because the task names
#: foundation memories trust anchors that must out-rank well-corroborated
#: derivatives of merely typical authorship.
FOUNDATION_MAX_LIFT: float = 0.60

# --- Corroboration squash ----------------------------------------------------

#: Softening constant in c/(c+k). With k=2: 1 citer => 0.33, 2 => 0.50,
#: 4 => 0.67, 8 => 0.80. Tuned for a young provenance graph (max corroboration
#: in the live corpus is currently small) so a single citation already moves
#: the needle without one prolific source dominating.
CORROBORATION_K: float = 2.0

# --- Citation-bonus detection ------------------------------------------------

#: A bracketed numeric reference like ``[1]`` or ``[1,2,3]`` or ``[5, p.12]``.
_CITATION_BRACKET_RE = re.compile(r"\[\s*\d+(?:\s*[,\-]\s*\d+)*(?:\s*,\s*p\.?\s*\d+)?\s*\]")
#: A URL (http/https). ``www.`` alone is intentionally NOT counted — too noisy.
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
#: ``lines NNN-NNN`` style database citation (an accepted source per project
#: citation rules).
_LINES_CITATION_RE = re.compile(r"\blines?\s+\d+(?:\s*[-–]\s*\d+)?\b", re.IGNORECASE)

#: Auto-tag prefixes/markers that indicate source_context was machine-written
#: (so it is NOT evidence of human authorship). Matched case-insensitively
#: against the start of source_context (after stripping) or as substrings for
#: the cluster marker.
_AUTOTAG_PREFIXES = (
    "migrated from",
    "[historical-archive",
    "cluster_id=",
    "[auto",
    "auto-generated",
    "auto_generated",
    "backfill",
)


# ============================================================================
# Row access helpers (defensive — accept an id, a dict, or a sqlite3.Row)
# ============================================================================

# Columns we read. Selected explicitly so a schema with extra columns is fine.
# ``untrusted_source`` is the Warden's immutable anti-laundering birth-mark; it
# is read so the final score can be ceilinged (see UNTRUSTED_SOURCE_CEILING). A
# pre-migration DB without the column degrades gracefully: the explicit SELECT
# would raise, which _fetch_row catches by retrying without it (see below).
_ROW_COLUMNS = (
    "id", "content", "summary", "kind", "confidence",
    "foundation_score", "derived_from", "source_context", "untrusted_source",
)
#: The same column set MINUS the optional ``untrusted_source`` — used as a
#: fallback SELECT on a pre-migration DB that lacks that column, so this module
#: keeps working (fail-graceful) on an un-migrated brain.
_ROW_COLUMNS_LEGACY = tuple(c for c in _ROW_COLUMNS if c != "untrusted_source")


def _fetch_row(conn: sqlite3.Connection, memory_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a memory row by id as a plain dict, or None if absent.

    Tries the full column set (including ``untrusted_source``); on a pre-migration
    DB that lacks that column the SELECT raises an OperationalError, and we retry
    with the legacy set so the keystone keeps working on an un-migrated brain.
    """
    try:
        cur = conn.execute(
            f"SELECT {', '.join(_ROW_COLUMNS)} FROM memories WHERE id = ?",
            (memory_id,),
        )
        cols = _ROW_COLUMNS
    except sqlite3.OperationalError:
        cur = conn.execute(
            f"SELECT {', '.join(_ROW_COLUMNS_LEGACY)} FROM memories WHERE id = ?",
            (memory_id,),
        )
        cols = _ROW_COLUMNS_LEGACY
    r = cur.fetchone()
    if r is None:
        return None
    return {col: r[idx] for idx, col in enumerate(cols)}


def _coerce_row(
    conn: sqlite3.Connection,
    row_or_id: Union[int, sqlite3.Row, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Normalize an id / sqlite3.Row / dict into a dict with the columns we need.

    If passed a partial dict/Row that is missing columns we need AND that
    carries an ``id``, we fall back to a DB fetch to fill it. Returns None only
    when we cannot resolve a usable row at all.
    """
    # Integer id (or a string that is an int) => fetch.
    if isinstance(row_or_id, int):
        return _fetch_row(conn, row_or_id)
    if isinstance(row_or_id, str) and row_or_id.strip().lstrip("-").isdigit():
        return _fetch_row(conn, int(row_or_id.strip()))

    # sqlite3.Row or dict-like => convert to dict.
    data: Dict[str, Any] = {}
    try:
        if isinstance(row_or_id, sqlite3.Row):
            data = {k: row_or_id[k] for k in row_or_id.keys()}
        elif isinstance(row_or_id, dict):
            data = dict(row_or_id)
        else:
            # Unknown type — try mapping access, else give up gracefully.
            try:
                data = dict(row_or_id)  # type: ignore[arg-type]
            except Exception:
                return None
    except Exception:
        return None

    # If the supplied mapping lacks columns we depend on, refill from DB by id.
    needed = ("confidence", "foundation_score", "derived_from", "source_context", "kind")
    if any(c not in data for c in needed) and data.get("id") is not None:
        try:
            fetched = _fetch_row(conn, int(data["id"]))
            if fetched is not None:
                # Caller-supplied values win; DB fills only the gaps.
                for k, v in fetched.items():
                    data.setdefault(k, v)
        except Exception:
            pass

    # Reject an unusable mapping: a dict/Row that carries neither a resolvable
    # ``id`` NOR any of the scoring columns has no signal — return None so the
    # caller degrades to DEFAULT_TRUST instead of inventing an authored score.
    recognized = ("id",) + _ROW_COLUMNS
    if not any(k in data for k in recognized):
        return None
    return data


# ============================================================================
# Component computations (each returns a value in [0, 1])
# ============================================================================

def _is_autotag_context(source_context: Optional[str]) -> bool:
    """True if source_context looks machine-written (NOT human authorship)."""
    if not source_context:
        return False
    s = source_context.strip().lower()
    return any(s.startswith(p) or p in s for p in _AUTOTAG_PREFIXES)


def _derived_provenance(
    conn: sqlite3.Connection,
    memory_id: Optional[int],
    provenance_summary: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the best (highest-confidence) provenance edge that PRODUCED this
    memory, as ``{"confidence": float, "transformation_type": str}``, or None.

    ``provenance_summary`` (if supplied by a caller that already has it) short-
    circuits the query. Expected shape (any subset is fine):
        {"derived": bool,
         "best_confidence": float,
         "transformation_type": str}
    """
    if provenance_summary:
        if provenance_summary.get("derived") is False:
            return None
        conf = provenance_summary.get("best_confidence",
                                      provenance_summary.get("confidence"))
        ttype = provenance_summary.get("transformation_type")
        if conf is not None or ttype is not None:
            return {"confidence": conf, "transformation_type": ttype}
    if memory_id is None:
        return None
    try:
        r = conn.execute(
            """SELECT confidence, transformation_type
               FROM memory_provenance
               WHERE derived_memory_id = ?
               ORDER BY confidence DESC
               LIMIT 1""",
            (memory_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if r is None:
        return None
    return {"confidence": r[0], "transformation_type": r[1]}


def _authorship_floor(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    provenance_summary: Optional[Dict[str, Any]],
) -> float:
    """Component 1 — authorship floor in [0, 1].

    Authored (no provenance row, derived_from NULL, and either NULL or
    human-written source_context) => high floor. Auto-derived => floored by
    the producing derivation's confidence, or a kind-based prior if absent.
    A ``manual_derivation`` provenance type is treated as authored-high.
    """
    mem_id = row.get("id")
    derived_from = row.get("derived_from")
    src_ctx = row.get("source_context")
    kind = (row.get("kind") or "").strip().lower()

    prov = _derived_provenance(conn, mem_id, provenance_summary)

    # A manual_derivation is a human deriving — treat as authored-high.
    if prov is not None and prov.get("transformation_type") in AUTHORED_TRANSFORMATIONS:
        return AUTHORED_FLOOR

    has_derived_from = derived_from not in (None, "", "null", "NULL")
    is_derived = (prov is not None) or has_derived_from

    if is_derived:
        # Floor by the derivation's own confidence; fall back to a kind prior.
        conf = prov.get("confidence") if prov else None
        if conf is None:
            conf = KIND_DERIVED_PRIOR.get(kind, DERIVED_PRIOR_DEFAULT)
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = DERIVED_PRIOR_DEFAULT
        return _clamp01(conf)

    # Authored: NULL source_context (pure manual store) gets the top floor;
    # an auto-tag context is weaker authorship evidence but still authored.
    if _is_autotag_context(src_ctx):
        return AUTHORED_WEAK_FLOOR
    return AUTHORED_FLOOR


def _foundation_fraction(row: Dict[str, Any]) -> Optional[float]:
    """Component 2 — foundation FLOOR-LIFT fraction in [0, FOUNDATION_MAX_LIFT],
    or None to OMIT.

    Foundation memories are trust anchors, so a high ``foundation_score`` raises
    where the FLOOR sits — lifting it a fraction of the way from the authorship
    floor toward 1.0. Returns ``FOUNDATION_MAX_LIFT * foundation_score`` (a
    fraction in [0, FOUNDATION_MAX_LIFT]); NEVER negative (never a penalty).
    NULL/invalid foundation returns None so the caller applies no lift and
    records the omission.
    """
    fs = row.get("foundation_score")
    if fs is None:
        return None
    try:
        fs = float(fs)
    except (TypeError, ValueError):
        return None
    fs = _clamp01(fs)
    # foundation 0 -> 0 lift; foundation 1 -> FOUNDATION_MAX_LIFT of the gap.
    return FOUNDATION_MAX_LIFT * fs


def _self_confidence(row: Dict[str, Any]) -> float:
    """Component 3 — the memory's own stored confidence in [0, 1]."""
    c = row.get("confidence")
    if c is None:
        return DEFAULT_TRUST
    try:
        return _clamp01(float(c))
    except (TypeError, ValueError):
        return DEFAULT_TRUST


def _corroboration(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    _depth: int,
) -> float:
    """Component 4 — citation-as-source corroboration in [0, 1].

    Squash the count of derivatives that cite this memory as a source via
    c/(c+k). When depth budget remains, trust-weight the citers (their average
    trust scales the squashed count up or down by +/-25%), bounded at depth 1
    to avoid provenance cycles.
    """
    mem_id = row.get("id")
    if mem_id is None:
        return 0.0
    try:
        cur = conn.execute(
            "SELECT COUNT(DISTINCT derived_memory_id) FROM memory_provenance WHERE source_memory_id = ?",
            (mem_id,),
        )
        count = cur.fetchone()[0] or 0
    except sqlite3.Error:
        return 0.0
    if count <= 0:
        return 0.0

    base = count / (count + CORROBORATION_K)

    # Bounded recursion: weight by the average trust of the citers (one level).
    if _depth > 0:
        try:
            citer_rows = conn.execute(
                """SELECT DISTINCT derived_memory_id FROM memory_provenance
                   WHERE source_memory_id = ? LIMIT 25""",
                (mem_id,),
            ).fetchall()
            trusts = []
            for (cid,) in citer_rows:
                if cid == mem_id:
                    continue
                # depth-0 recursion: corroboration of the citer is NOT recursed.
                t = compute_trust(conn, cid, _depth=_depth - 1)
                trusts.append(t)
            if trusts:
                avg = sum(trusts) / len(trusts)
                # Map avg trust [0,1] -> multiplier [0.75, 1.25].
                mult = 0.75 + 0.5 * avg
                base = _clamp01(base * mult)
        except sqlite3.Error:
            pass
    return _clamp01(base)


def _is_untrusted_source(row: Dict[str, Any]) -> bool:
    """True if the row carries the Warden's immutable ``untrusted_source=1`` mark.

    Defensive: a missing column (pre-migration DB / partial dict), NULL, 0, ''
    or any non-truthy value reads as NOT untrusted. Only an explicit truthy
    integer/1 trips the ceiling, so this can never spuriously bury a row.
    """
    v = row.get("untrusted_source")
    if v is None:
        return False
    try:
        return int(v) == 1
    except (TypeError, ValueError):
        # A non-numeric truthy value ('1', 'true') still counts as marked.
        return str(v).strip().lower() in ("1", "true", "yes")


def _citation_bonus(row: Dict[str, Any]) -> float:
    """Component 5 — additive traceable-source bonus in [0, 1] (pre-weight).

    1.0 if the content carries a bracketed [n] reference, a URL, or a
    ``lines NN-NN`` database citation; else 0.0. Weighted small and additive —
    NEVER required (the brain holds much un-cited but authored truth).
    """
    text_parts = []
    for key in ("content", "summary"):
        v = row.get(key)
        if v:
            text_parts.append(str(v))
    if not text_parts:
        return 0.0
    text = "\n".join(text_parts)
    if (_CITATION_BRACKET_RE.search(text)
            or _URL_RE.search(text)
            or _LINES_CITATION_RE.search(text)):
        return 1.0
    return 0.0


# ============================================================================
# Breakdown + public API
# ============================================================================

def _clamp01(x: float) -> float:
    """Clamp to [0, 1] defensively (handles NaN by mapping to DEFAULT_TRUST)."""
    try:
        if x != x:  # NaN
            return DEFAULT_TRUST
        if x < 0.0:
            return 0.0
        if x > 1.0:
            return 1.0
        return float(x)
    except (TypeError, ValueError):
        return DEFAULT_TRUST


def _trust_components(
    conn: sqlite3.Connection,
    row_or_id: Union[int, sqlite3.Row, Dict[str, Any]],
    provenance_summary: Optional[Dict[str, Any]] = None,
    _depth: int = 1,
) -> Dict[str, Any]:
    """Return the full breakdown dict used to build the score.

    Keys:
        score                -- the final clamped trust in [0, 1]
        authorship           -- component 1 raw [0, 1]
        self_confidence      -- component 3 raw [0, 1]
        corroboration        -- component 4 raw [0, 1]
        citation_bonus       -- component 5 raw [0, 1]
        foundation_factor    -- component 2 multiplier (>= 1.0) or None (omitted)
        weights_used         -- the (renormalized) weights actually applied
        base_score           -- weighted blend BEFORE the foundation lift
        memory_id            -- the id, if known
        ok                   -- True on a clean compute, False on a degraded path
        note                 -- a short human string describing any degradation
    """
    row = _coerce_row(conn, row_or_id)
    if row is None:
        return {
            "score": DEFAULT_TRUST,
            "authorship": None,
            "self_confidence": None,
            "corroboration": None,
            "citation_bonus": None,
            "foundation_factor": None,
            "weights_used": dict(TRUST_WEIGHTS),
            "base_score": DEFAULT_TRUST,
            "memory_id": (int(row_or_id) if isinstance(row_or_id, int) else None),
            "ok": False,
            "note": "memory row not found; returned DEFAULT_TRUST",
        }

    # --- component 1: the authorship FLOOR (the dominant signal) ---
    authorship = _authorship_floor(conn, row, provenance_summary)

    # --- component 2: foundation FLOOR-LIFT (anchors raise where the floor sits) ---
    # Applied to the floor BEFORE the headroom fills, so a high-foundation anchor
    # out-ranks a well-corroborated derivative whose floor was not lifted. This is
    # monotone and non-saturating: it lifts a fraction of the gap (floor -> 1).
    foundation_fraction = _foundation_fraction(row)
    if foundation_fraction is not None:
        floor = _clamp01(authorship + foundation_fraction * (1.0 - authorship))
        note = "ok"
    else:
        floor = authorship
        note = "foundation_score NULL: floor-lift omitted (no penalty)"

    # --- components 3-5: the HEADROOM fill (add on top of the lifted floor) ---
    self_conf = _self_confidence(row)
    corrob = _corroboration(conn, row, _depth=_depth)
    cite = _citation_bonus(row)
    head_components = {
        "self_confidence": self_conf,
        "corroboration": corrob,
        "citation_bonus": cite,
    }

    # Weighted convex blend of the headroom components (renormalized defensively).
    head_weights = {k: TRUST_WEIGHTS[k] for k in _HEADROOM_KEYS}
    total_hw = sum(head_weights.values())
    if total_hw <= 0:
        head_weights = {k: 1.0 / len(head_components) for k in head_components}
        total_hw = 1.0
    headroom = sum(head_components[k] * head_weights[k] for k in head_components) / total_hw
    headroom = _clamp01(headroom)

    # FLOOR (foundation-lifted) + headroom-fill toward 1.0.
    score = _clamp01(floor + (1.0 - floor) * headroom)

    # --- untrusted-source CEILING (provenance-blind defense-in-depth) ---
    # The Warden's immutable ``untrusted_source=1`` birth-mark caps the final
    # score below the authored baseline REGARDLESS of derived_from/confidence,
    # so web-sourced content can never out-rank Drew-authored truth even if a
    # re-laundering write stripped the derived_from sentinel. This is a CAP only
    # (min): it never lifts a low score, only prevents an untrusted row from
    # floating up. Applied last so it dominates every other lift.
    untrusted = _is_untrusted_source(row)
    if untrusted and score > UNTRUSTED_SOURCE_CEILING:
        score = UNTRUSTED_SOURCE_CEILING
        note = (note + "; " if note and note != "ok" else "") + \
            "untrusted_source=1: capped at UNTRUSTED_SOURCE_CEILING"

    # The values actually applied (floor + headroom weights), for transparency.
    weights_used = {"authorship_floor": authorship, "lifted_floor": round(floor, 6),
                    **head_weights}

    return {
        "score": round(score, 6),
        "authorship": round(authorship, 6),
        "self_confidence": round(self_conf, 6),
        "corroboration": round(corrob, 6),
        "citation_bonus": round(cite, 6),
        "headroom": round(headroom, 6),
        "lifted_floor": round(floor, 6),
        "foundation_fraction": (round(foundation_fraction, 6)
                                if foundation_fraction is not None else None),
        # Back-compat alias: a multiplicative-style factor view of the floor lift.
        "foundation_factor": (round(floor / authorship, 6)
                              if (foundation_fraction is not None and authorship > 0)
                              else None),
        "weights_used": weights_used,
        "base_score": round(floor, 6),
        "untrusted_source": bool(untrusted),
        "untrusted_ceilinged": bool(untrusted and score >= UNTRUSTED_SOURCE_CEILING),
        "memory_id": row.get("id"),
        "ok": True,
        "note": note,
    }


def compute_trust(
    conn: sqlite3.Connection,
    row_or_id: Union[int, sqlite3.Row, Dict[str, Any]],
    provenance_summary: Optional[Dict[str, Any]] = None,
    _depth: int = 1,
) -> float:
    """Compute a memory's DERIVED, read-time trust score in [0, 1].

    PURE over existing columns; no writes, no model, no new deps. Any internal
    error degrades to ``DEFAULT_TRUST`` and never raises into a consumer.

    Args:
        conn: an open sqlite3 connection to the memory DB (read-only use here).
        row_or_id: an integer memory id, a sqlite3.Row, or a dict with at least
            an ``id`` (other columns filled from the DB if missing).
        provenance_summary: optional pre-fetched provenance hint to skip a
            query (see ``_derived_provenance``).
        _depth: internal bounded-recursion budget for trust-weighted
            corroboration. Callers should leave this at its default.

    Returns:
        A float in [0, 1]. Higher trust ranks a memory ahead of a lower one.
    """
    try:
        return _trust_components(conn, row_or_id, provenance_summary, _depth=_depth)["score"]
    except Exception:
        # The keystone NEVER raises into a consumer.
        return DEFAULT_TRUST


def edge_authority(
    conn: sqlite3.Connection,
    a_id: int,
    b_id: int,
    provenance_summary_a: Optional[Dict[str, Any]] = None,
    provenance_summary_b: Optional[Dict[str, Any]] = None,
) -> float:
    """The load-bearing contract: authority of an edge = min(trust(A), trust(B)).

    The belief engine's contradiction-authority and the surprise engine both
    consume this. An edge is only as trustworthy as its weaker endpoint.
    Fail-graceful: returns ``DEFAULT_TRUST`` on any error, never raises.
    """
    try:
        ta = compute_trust(conn, a_id, provenance_summary_a)
        tb = compute_trust(conn, b_id, provenance_summary_b)
        return min(ta, tb)
    except Exception:
        return DEFAULT_TRUST

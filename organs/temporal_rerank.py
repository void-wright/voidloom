"""Temporal-aware reranking organ (L5-C2): pointwise superseded-content down-ranking.

Makes recall prefer the CURRENT version of knowledge. When the reranker scores a
candidate pool, this organ marks any candidate that a TRUSTWORTHY ``supersedes``
edge has retired and prepends a static ``[superseded as of <date>] `` status tag
to that candidate's document text *before* scoring. The reranker then weighs the
tag down (a cross-encoder learns "[superseded ...]" is a negative relevance cue,
and the instruction-following Qwen backend is additionally told to rank current
content above content marked superseded). The result: a memory that has been
replaced by a newer one ranks BELOW its live successor.

Design principles (the heart of the piece):

* **Pointwise + Turing-safe.** A pure SQL lookup + a string prefix. NO LLM
  generation in the hot path (the listwise "which of these contradict each
  other" pass would need generation -> DEFERRED to an Ampere+ organ).
  The tag is a *static* string built from a date already in the DB.

* **A SOFT signal, never a hard filter or a delete.** The tag only NUDGES rank;
  it never drops a candidate from the pool. Even a slightly-wrong superseded
  flag merely lowers a rank a little, and the kill-switch + the N-alpha harness
  bound the blast radius.

* **THE TRUST-FILTER (do NOT repeat the 416-poison in reverse).** The live
  ``supersedes`` population is poisoned the same way the belief engine found
  ``contradicts`` to be: of 818 edges, 589 are ``extraction_method='heuristic'``
  at confidence ~0.24 (the keyword-false-positive band) and only 229 are
  ``sleep_loop`` at confidence ~0.71 (trustworthy). If C2 down-ranked a memory
  because a conf-0.24 heuristic edge claimed it was superseded, it would BURY
  legitimately-CURRENT content -- degrading recall, the exact opposite of the
  goal. So the superseded signal is restricted to **high-confidence supersedes
  (confidence >= ``min_conf``, default 0.5) whose supersedes edge is itself
  still live (``valid_until IS NULL``)**. The conf-0.24 heuristic band is
  EXCLUDED. (On the live corpus this tags ~181 memories, down from a naive 532 --
  the filter doing its job. See generation_report.md for the full rationale,
  including why ``valid_until``-set ``contradicts`` edges are NOT treated as a
  supersession signal: those are the belief engine's *retracted* contradictions,
  not retired memories.)

* **Works with BOTH backends.** The text tag steers bge's cross-encoder; the tag
  PLUS the ``temporal_instruction()`` steer the (default) Qwen judge. bge has no
  instruction hook, so the text tag is its only -- and sufficient -- signal.

* **Fail-graceful EVERYWHERE.** Any error (missing table, malformed schema, a
  bad row) -> the candidate's text is returned UNDECORATED. Recall never breaks.
  The integration additionally guards with a kill-switch (default-on,
  ``--no-temporal-rerank`` / ``VOIDLOOM_NO_TEMPORAL_RERANK``).

Public surface (consumed by hybrid_retrieval.reranker_score; see
INTEGRATION_hybrid_retrieval.md):

    superseded_status(conn, ids, min_conf=0.5) -> {id: {superseded_at, confidence}}
    decorate_docs(id_to_text, status)          -> id_to_text2  (non-destructive)
    temporal_instruction(base=None)            -> str          (Qwen instruction)
    is_temporal_available(conn)                -> bool          (cheap guard)

stdlib + sqlite3 only. NO new dependency, NO model, NO generation, side-effect
free (read-only against the DB).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Tunables.
# ----------------------------------------------------------------------------

# The trust threshold. A ``supersedes`` edge must have confidence >= this to be
# treated as authoritative. 0.5 cleanly separates the live bands: the heuristic
# poison band tops out at ~0.32 (avg 0.24), the trustworthy sleep_loop band runs
# 0.47-0.80 (avg 0.71). 0.5 keeps the whole sleep_loop band and rejects the
# entire heuristic band. (The belief-engine-elevated foundation edges, when they
# exist, are >= 0.70 and so also pass.) Restrict-by-confidence gates *membership*
# (which ids get a tag at all); the SIZE of the down-rank is then
# confidence-WEIGHTED in the score penalty below -- so the trust-filter does
# double duty: it excludes the poison band entirely AND scales the nudge by how
# trustworthy the surviving edge is. See generation_report.md.
DEFAULT_MIN_CONF: float = 0.5

# The deterministic down-rank. The text tag + Qwen instruction are the SOFT steer
# the model weighs; but the empirical effect of the tag is small (~0.01-0.03 on
# Qwen) and, on bge, not even reliably negative (a cross-encoder was not trained
# on "[superseded ...]" as a negative cue). At the reranker's ~0.999 saturation
# ceiling that nudge is swamped by FP16 score noise on near-tied pairs. So C2
# ALSO applies a small, deterministic, confidence-scaled multiplicative penalty
# to the FINAL rerank score of a tagged id: ``score *= (1 - PENALTY_BASE * conf)``.
# This guarantees the down-rank DIRECTION (a superseded doc always loses a fixed
# fraction of its score) regardless of how the model read the tag, while staying
# a SOFT signal -- at conf 0.8 the penalty is 12%, never a hard drop, and the
# kill-switch + fail-graceful bound it. The tag steers; the penalty secures.
# (Verified: on the adversarial near-tie #5540-vs-#5535 the tag ALONE let the
# superseded doc wrongly win; the penalty restored current-first. See the report.)
PENALTY_BASE: float = 0.15

# The static status tag. The trailing space matters: the prefix sits flush
# against the document text. The reranker reads this as a negative relevance cue
# for the (query, doc) pair.
_SUPERSEDED_TAG_PREFIX = "[superseded as of "
_SUPERSEDED_TAG_SUFFIX = "] "
_SUPERSEDED_TAG_NO_DATE = "[superseded] "  # fallback when no usable date exists

# The Qwen-only instruction that complements the text tag. Composable with a base
# instruction (the adapter's DEFAULT_INSTRUCTION, or a future persona instruction).
_TEMPORAL_INSTRUCTION = (
    "Rank current, up-to-date content above content that is marked as superseded "
    "or retired (prefixed with \"[superseded\"). A superseded passage has been "
    "replaced by a newer version and should rank lower."
)


# ----------------------------------------------------------------------------
# Date helper.
# ----------------------------------------------------------------------------

def _date_only(ts: Optional[str]) -> Optional[str]:
    """Reduce an ISO-ish timestamp to its ``YYYY-MM-DD`` date portion.

    Accepts both SQLite's space-separated ``'2026-06-10 02:30:30'`` and the
    ``'T'``-separated ``'2026-06-10T17:51:58.554479'`` forms. Returns ``None``
    for anything that does not begin with a 10-char ``YYYY-MM-DD`` date so the
    caller can fall back to the date-less tag. NEVER raises.
    """
    if not ts or not isinstance(ts, str):
        return None
    head = ts.strip()[:10]
    # Cheap validity check: YYYY-MM-DD shape (digits + two dashes in place).
    if (
        len(head) == 10
        and head[4] == "-"
        and head[7] == "-"
        and head[:4].isdigit()
        and head[5:7].isdigit()
        and head[8:10].isdigit()
    ):
        return head
    return None


# ----------------------------------------------------------------------------
# Availability guard.
# ----------------------------------------------------------------------------

def is_temporal_available(conn: sqlite3.Connection) -> bool:
    """Cheap guard: is the ``memory_edges`` table present and queryable?

    The integration calls this once before doing any work so that a brain
    without the bi-temporal edge table (an old / partial install) silently
    skips temporal reranking instead of erroring. NEVER raises -> False on any
    problem.
    """
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_edges'"
        ).fetchone()
        if not row:
            return False
        # Confirm the columns we rely on exist (defends a renamed/altered schema).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_edges)").fetchall()}
        needed = {"target_id", "source_id", "predicate", "confidence",
                  "valid_until", "created_at"}
        return needed.issubset(cols)
    except Exception as e:  # noqa: BLE001 -- guard must never raise.
        logger.debug("is_temporal_available: %s -> treating temporal as unavailable", e)
        return False


# ----------------------------------------------------------------------------
# The trust-filtered superseded lookup (the heart).
# ----------------------------------------------------------------------------

def superseded_status(
    conn: sqlite3.Connection,
    ids: Iterable[int],
    min_conf: float = DEFAULT_MIN_CONF,
) -> Dict[int, Dict[str, Any]]:
    """Return which of ``ids`` are superseded by the TRUSTWORTHY signal.

    A memory id ``B`` is reported superseded iff there exists a ``supersedes``
    edge ``A -> B`` (so ``B`` is the ``target_id``, the retired one) that is:

      * ``confidence >= min_conf``  (excludes the conf-0.24 heuristic poison
        band -- THE TRUST-FILTER), AND
      * ``valid_until IS NULL``     (the supersession itself is still in force;
        a supersedes edge whose ``valid_until`` was set has been retracted).

    The ``valid_until``-set ``contradicts`` edges (the belief engine's *retracted
    contradictions*) are NOT a supersession signal and are correctly never
    consulted here -- they say "we withdrew a contradiction claim", not "this
    memory is retired". See generation_report.md.

    When several qualifying edges retire the same memory, the one with the
    HIGHEST confidence wins (its ``created_at`` date and confidence are
    reported), so the tag reflects the strongest available evidence.

    Args:
        conn: sqlite3 connection (read-only use).
        ids: candidate memory ids (the reranker's pool).
        min_conf: trust threshold (default 0.5). Edges below this are ignored.

    Returns:
        ``{id: {"superseded_at": "YYYY-MM-DD" | None, "confidence": float}}``
        for the subset of ``ids`` that the trustworthy signal retires. Ids that
        are NOT superseded (or only superseded by the poison band) are ABSENT
        from the dict. ``{}`` on any failure (fail-graceful -> no decoration).
    """
    try:
        id_list = [int(i) for i in ids]
    except Exception as e:  # noqa: BLE001
        logger.debug("superseded_status: bad ids %s -> {}", e)
        return {}
    if not id_list:
        return {}

    try:
        conf = float(min_conf)
    except Exception:  # noqa: BLE001
        conf = DEFAULT_MIN_CONF

    try:
        placeholders = ",".join("?" * len(id_list))
        # Pull all qualifying supersedes edges whose TARGET is in the pool.
        # We order so that, per target, the highest-confidence edge is seen first;
        # the dict-build below keeps the first (= strongest) per target.
        sql = (
            "SELECT target_id, confidence, created_at "
            "FROM memory_edges "
            "WHERE predicate = 'supersedes' "
            "  AND valid_until IS NULL "
            "  AND confidence >= ? "
            f"  AND target_id IN ({placeholders}) "
            "ORDER BY target_id, confidence DESC, created_at DESC"
        )
        rows = conn.execute(sql, [conf, *id_list]).fetchall()
    except Exception as e:  # noqa: BLE001 -- e.g. no memory_edges table.
        logger.warning(
            "superseded_status: edge query failed (%s) -> no temporal decoration", e
        )
        return {}

    status: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        try:
            tgt = int(row[0])
            edge_conf = float(row[1]) if row[1] is not None else 0.0
            edge_date = _date_only(row[2])
        except Exception as e:  # noqa: BLE001 -- a single malformed row is skipped.
            logger.debug("superseded_status: skipping malformed row %r (%s)", row, e)
            continue
        # Keep the strongest edge per target (rows are pre-sorted desc by conf).
        if tgt not in status:
            status[tgt] = {"superseded_at": edge_date, "confidence": edge_conf}
    return status


# ----------------------------------------------------------------------------
# Non-destructive decoration.
# ----------------------------------------------------------------------------

def _superseded_tag(superseded_at: Optional[str]) -> str:
    """Build the static status tag for a given (optional) date."""
    if superseded_at:
        return f"{_SUPERSEDED_TAG_PREFIX}{superseded_at}{_SUPERSEDED_TAG_SUFFIX}"
    return _SUPERSEDED_TAG_NO_DATE


def decorate_docs(
    id_to_text: Dict[int, str],
    status: Dict[int, Dict[str, Any]],
) -> Dict[int, str]:
    """Return a NEW ``{id: text}`` with the superseded tag prepended where due.

    Non-destructive: the input ``id_to_text`` is never mutated (a fresh dict is
    returned). Only ids present in BOTH ``id_to_text`` and ``status`` are
    decorated; everything else is copied through verbatim. A missing/empty date
    falls back to the date-less ``[superseded] `` tag. NEVER raises -> on any
    per-id error that id is copied through undecorated.

    Args:
        id_to_text: the reranker's ``{id: "summary\\ncontent"}`` map.
        status: the output of ``superseded_status`` (which ids are retired).

    Returns:
        A new dict, same keys as ``id_to_text``, with the tag prepended to the
        superseded entries. If ``status`` is empty, this is a plain copy
        (so an empty trust-filtered set => no behavioural change).
    """
    if not isinstance(id_to_text, dict):  # defensive: only dicts are valid input.
        return id_to_text
    out: Dict[int, str] = {}
    for mid, text in id_to_text.items():
        try:
            info = status.get(mid) if status else None
            if info is not None:
                tag = _superseded_tag(info.get("superseded_at"))
                out[mid] = f"{tag}{text if text is not None else ''}"
            else:
                out[mid] = text
        except Exception as e:  # noqa: BLE001 -- never let one id break the map.
            logger.debug("decorate_docs: id %r left undecorated (%s)", mid, e)
            out[mid] = text
    return out


# ----------------------------------------------------------------------------
# The deterministic, confidence-scaled score penalty (the robust floor).
# ----------------------------------------------------------------------------

def apply_score_penalty(
    scored: List[Tuple[int, float]],
    status: Dict[int, Dict[str, Any]],
    penalty_base: float = PENALTY_BASE,
) -> List[Tuple[int, float]]:
    """Apply a confidence-scaled multiplicative penalty to superseded ids, then
    re-sort descending. Returns a NEW ``[(id, score)]`` list.

    For each ``(id, score)`` where ``id`` is in ``status`` (a trusted-superseded
    id), the score becomes ``score * (1 - penalty_base * confidence)`` -- a small
    deterministic down-rank whose size scales with the supersedes edge's
    confidence (a conf-0.5 edge gets half the penalty of a conf-1.0 edge). Ids
    NOT in ``status`` are untouched. The list is re-sorted by the penalised score
    so the down-rank actually changes rank order.

    This is the DETERMINISTIC complement to the (soft, model-weighed) text tag +
    Qwen instruction. The tag steers the reranker; this guarantees the direction
    even when the model's reading of the tag is small or noisy at saturation.
    Still a SOFT signal: at conf 0.8 / base 0.15 the penalty is 12% of the score,
    never a hard filter or a drop. NEVER raises -> returns ``scored`` unchanged on
    any error.

    Args:
        scored: ``[(id, score)]`` as returned by the reranker (any order).
        status: the trusted-superseded map from ``superseded_status``.
        penalty_base: max penalty fraction at confidence 1.0 (default 0.15).

    Returns:
        A new ``[(id, score)]`` sorted descending by the (possibly penalised)
        score. If ``status`` is empty, this is ``scored`` re-sorted (a no-op on
        already-sorted input).
    """
    try:
        if not scored:
            return scored
        if not status:
            # Nothing to penalise; return as-is (caller already sorted it).
            return scored
        base = float(penalty_base)
        out = []
        for item in scored:
            mid, score = item[0], float(item[1])
            info = status.get(mid)
            if info is not None:
                conf = float(info.get("confidence", 0.0) or 0.0)
                # Clamp conf into [0,1] defensively; penalty in [0, base].
                conf = 0.0 if conf < 0.0 else (1.0 if conf > 1.0 else conf)
                factor = 1.0 - base * conf
                if factor < 0.0:
                    factor = 0.0
                out.append((mid, score * factor))
            else:
                out.append((mid, score))
        out.sort(key=lambda x: x[1], reverse=True)
        return out
    except Exception as e:  # noqa: BLE001 -- the floor must never break recall.
        logger.warning("apply_score_penalty failed (%s) -> scores unchanged", e)
        return scored


# ----------------------------------------------------------------------------
# The Qwen instruction (composable).
# ----------------------------------------------------------------------------

def temporal_instruction(base: Optional[str] = None) -> str:
    """Return the temporal-preference instruction for the Qwen backend.

    When ``base`` is None, returns the standalone temporal instruction (the
    adapter then prepends its own DEFAULT_INSTRUCTION framing). When ``base`` is
    given (a task or future persona instruction), the temporal clause is APPENDED
    so both steers compose: ``"<base> <temporal>"``.

    bge has no instruction hook, so this is consumed only by the Qwen path; bge
    relies on the text tag alone (which is sufficient -- a cross-encoder reads
    "[superseded ...]" as a negative cue directly).
    """
    if base and str(base).strip():
        return f"{str(base).strip()} {_TEMPORAL_INSTRUCTION}"
    return _TEMPORAL_INSTRUCTION


# ----------------------------------------------------------------------------
# Convenience: one call that does the lookup + decoration (used by the
# integration so the edit-list stays a 3-liner). Fail-graceful: returns the
# input unchanged (a plain copy is unnecessary, the caller already holds it) on
# any failure.
# ----------------------------------------------------------------------------

def decorate_for_rerank(
    conn: sqlite3.Connection,
    id_to_text: Dict[int, str],
    min_conf: float = DEFAULT_MIN_CONF,
) -> Dict[int, str]:
    """Lookup + decorate in one fail-graceful call.

    Returns a decorated COPY of ``id_to_text`` (superseded entries tagged), or --
    if temporal reranking is unavailable or anything fails -- the ORIGINAL
    ``id_to_text`` object unchanged (so the caller's bge/Qwen dispatch sees
    byte-identical text, exactly as if temporal reranking were off).

    This is the single function the integration calls after building
    ``id_to_text`` and before the backend dispatch.
    """
    try:
        if not id_to_text:
            return id_to_text
        if not is_temporal_available(conn):
            return id_to_text
        status = superseded_status(conn, list(id_to_text.keys()), min_conf=min_conf)
        if not status:
            return id_to_text  # nothing trustworthy-superseded -> no change.
        return decorate_docs(id_to_text, status)
    except Exception as e:  # noqa: BLE001 -- the whole organ is best-effort.
        logger.warning(
            "decorate_for_rerank: temporal decoration failed (%s) -> undecorated text", e
        )
        return id_to_text


# ----------------------------------------------------------------------------
# Tiny self-describing CLI (no side effects; for ad-hoc inspection of a DB).
# ----------------------------------------------------------------------------

def _cli(argv: List[str]) -> int:  # pragma: no cover -- operational convenience.
    import json

    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(
            "usage: python3 temporal_rerank.py <db_path> [min_conf] [id ...]\n"
            "  Reports the trust-filtered superseded-target count for a DB, and\n"
            "  (if ids are given) which of those ids the filter would tag.\n"
            "  Read-only. NO model, NO generation."
        )
        return 0
    db = argv[1]
    min_conf = float(argv[2]) if len(argv) > 2 and _looks_float(argv[2]) else DEFAULT_MIN_CONF
    ids = [int(a) for a in argv[3:]] if len(argv) > 3 else None
    conn = sqlite3.connect(db, timeout=60.0)
    try:
        avail = is_temporal_available(conn)
        summary: Dict[str, Any] = {"db": db, "min_conf": min_conf,
                                   "temporal_available": avail}
        if avail:
            total_targets = conn.execute(
                "SELECT COUNT(DISTINCT target_id) FROM memory_edges "
                "WHERE predicate='supersedes'"
            ).fetchone()[0]
            tagged_targets = conn.execute(
                "SELECT COUNT(DISTINCT target_id) FROM memory_edges "
                "WHERE predicate='supersedes' AND valid_until IS NULL AND confidence >= ?",
                [min_conf],
            ).fetchone()[0]
            summary["distinct_supersedes_targets_naive"] = total_targets
            summary["distinct_supersedes_targets_trusted"] = tagged_targets
            summary["poison_suppressed"] = total_targets - tagged_targets
            if ids:
                st = superseded_status(conn, ids, min_conf=min_conf)
                summary["queried_ids"] = ids
                summary["tagged"] = {str(k): v for k, v in st.items()}
        print(json.dumps(summary, indent=2, default=str))
        return 0
    finally:
        conn.close()


def _looks_float(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_cli(sys.argv))

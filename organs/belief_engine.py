#!/usr/bin/env python3
"""belief_engine.py — Voidloom L5-D — The Belief Engine.

The organ that lets the brain notice when two stored facts genuinely DISAGREE,
and — critically — stops it from inventing fake disagreements.

THE ONE LOAD-BEARING IDEA (read this before touching anything):

    NLI has NO "unrelated" class. Forced to choose among
    {contradiction, entailment, neutral}, two topically-UNRELATED short texts
    very often land on "contradiction" with high confidence. VERIFIED LIVE:
    NLI scores ("A WordPress troubleshooting framework.",
    "Communication style guidelines.") as contradiction 0.984. That single
    failure mode is what poisoned the live graph (416 conf-0.24 heuristic
    `contradicts` edges between topically-unrelated memories).

    So a naive "re-run NLI and keep the confirmed ones" REPRODUCES the poison.
    The fix is an EMBEDDING-RELATEDNESS GATE that runs BEFORE NLI: you cannot
    meaningfully contradict a memory you are not even about. NLI is only ever
    asked about pairs whose memory_embeddings_v2 cosine clears a threshold
    (default 0.50, tuned against the live distribution — see generation_report).

    The cosine gate is BOTH the correctness gate (kills the unrelated false
    positives) AND the cost gate (the nightly sweep only NLIs new/changed
    memories x their top-k embedding-neighbours, never O(n^2)).

DISCIPLINE (every one of these is enforced in code, asserted in tests):
  * SOLE AUTHOR. belief_engine is the only thing that authors contradicts /
    supersedes edges now (temporal_edges' heuristic regex for those two
    predicates is DEMOTED in the patched copy temporal_edges.belief.py).
  * PROPOSALS, NOT EDGES. A scan writes rows to `edge_proposals`. It NEVER
    creates a memory_edges row and NEVER sets valid_until. Only approve_proposals
    enacts: add_contradicts -> create_edge; invalidate/supersede -> set
    valid_until. NEVER a DELETE.
  * NEVER NLI ON STORE. store() calls enqueue_for_scan() (a cheap INSERT). The
    nightly sweep is the only place NLI runs.
  * BATCH ONE REPORT PER SWEEP. Every proposal a sweep emits carries the same
    report_batch, so the approvals banner's pending-count increments by 1.
  * FAIL-GRACEFUL. A missing NLI model (model_garden.get_model('nli') -> None)
    degrades the engine to a no-op: scans run the cosine gate, emit NOTHING that
    needed NLI, and never raise. A half-provisioned machine still stores + recalls.
  * EDGE AUTHORITY. A proposed contradiction's authority = trust_tier
    .edge_authority(a, b) = min(trust(a), trust(b)); a foundation-touching pair
    is flagged `elevated` so the banner/council surface it first.

same_as is OUT OF SCOPE (L5-D.2). This module adds no predicate.

Confinement: generated in an ACE workspace; the orchestrator applies it to the
live tree after close. Side-effect-free at import.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import struct
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("voidloom.belief_engine")

# ============================================================================
# Tunables (every number in one place; the rationale is in generation_report.md
# and is grounded in the live cosine distribution of the 434 heuristic edges).
# ============================================================================

#: The embedding-relatedness gate. NLI is NEVER called on a pair below this
#: cosine. Justified live: 0.50 sits at the median of the heuristic-contradicts
#: cosine distribution (min 0.06, median 0.546, max 0.935); it cleanly excludes
#: the topically-unrelated poison (the WordPress/Communication pair at ~0.22)
#: while admitting the genuinely-related pairs for NLI adjudication. Overridable
#: per-DB via belief_engine_state['cosine_gate_threshold'].
DEFAULT_COSINE_GATE: float = 0.50

#: An NLI contradiction is only believed at/above this softmax score. Below it,
#: a "contradiction" label is treated as too weak to author/keep an edge.
DEFAULT_NLI_CONFIDENCE: float = 0.50

#: foundation_score at/above which a pair is flagged `elevated` (the contradiction
#: touches a trust anchor — surface it to the banner/council first).
DEFAULT_FOUNDATION_ELEVATED: float = 0.70

#: The new-edge confidence for an approved add_contradicts proposal. We mirror
#: the NLI score (already a calibrated probability) so a strong contradiction
#: gets a strong edge; clamped into temporal_edges' [0,1].
#: extraction_method tag the belief engine stamps on the edges it authors.
BELIEF_EXTRACTION_METHOD: str = "manual"  # see _edge_extraction_method() note

#: Embedding table + dimension (the active jina-1024 table per `metadata`).
EMBEDDING_TABLE: str = "memory_embeddings_v2"
EMBEDDING_DIM: int = 1024

#: How many top-k embedding neighbours a dirty memory is compared against in the
#: nightly sweep. Bounds the sweep at O(dirty * NEIGHBOR_K) NLI calls.
DEFAULT_NEIGHBOR_K: int = 10

#: NLI label order is VERIFIED LIVE: predict(..., apply_softmax=True) returns a
#: 3-vector over ['contradiction','entailment','neutral'] (index 0 ==
#: contradiction). id2label on the live model == {0:'contradiction',
#: 1:'entailment', 2:'neutral'}. Do NOT reorder.
NLI_LABELS: Tuple[str, str, str] = ("contradiction", "entailment", "neutral")
NLI_CONTRADICTION_IDX: int = 0


# ============================================================================
# ORGAN TEMPLATES — the second false-contradiction class (round 1, L5).
# ============================================================================
# The cosine gate killed the first poison class (topically UNRELATED pairs).
# The health check of 2026-10-14 measured the second: memories that ORGANS write
# share a boiler-plate head, the head lifts their cosine over the gate, and NLI
# reads "same template, different values" as contradiction (sampled pairs:
# unrelated topics at NLI 0.88-0.999). Measured on the live brain, 2026-10-15:
# of 3,789 enacted contradicts pairs, 3,527 joined two memories of the SAME
# template (Distilled 2,412 / Dream 648 / gap research 467) and 0 joined a
# template to free text; all 174 pending proposals were same-template pairs.
#
# Each family is keyed on the CONTENT head its writer produces (a test binds
# each regex to the writer's own format string, so a reworded writer turns the
# test red instead of silently re-opening the class):
#   sleep_loop.py         f"Distilled from {n} episodic memories ..."
#   memory_dreaming.py    f"[Dream synthesis — {dream_type}]\nSource memories: ..."
#   gap_mapping.py        f"Auto-researched gap ({gap_type}): {entity}\n\n..."
TEMPLATE_FAMILIES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("sleep_consolidation", re.compile(r"^Distilled from \d+ episodic memor")),
    ("dream_synthesis", re.compile(r"^\[Dream synthesis\b")),
    ("gap_research", re.compile(r"^Auto-researched gap \(")),
)

#: The decision scan_pair returns for a same-template pair. It is never NLI'd.
TEMPLATE_PAIR_DECISION: str = "template_pair"

#: The reason stamped on everything the quarantine touches (edges' valid_until is
#: the mechanism; this string is how a human reading the brain finds out why).
QUARANTINE_REASON: str = "quarantined: same organ template on both ends (belief_quarantine)"


def template_family(content: Optional[str]) -> Optional[str]:
    """The organ template a memory's content was written from, or None for free
    text. Pure; keyed on the head only."""
    if not content:
        return None
    head = content.lstrip()[:120]
    for name, rx in TEMPLATE_FAMILIES:
        if rx.match(head):
            return name
    return None


def same_template(family_a: Optional[str], family_b: Optional[str]) -> bool:
    """True when both ends carry the SAME organ template — the pair class whose
    NLI verdict measures the template, not the facts."""
    return family_a is not None and family_a == family_b


def _family_of(conn: sqlite3.Connection, memory_id: int) -> Optional[str]:
    try:
        row = conn.execute("SELECT content FROM memories WHERE id = ?", (memory_id,)).fetchone()
    except sqlite3.Error:
        return None
    return template_family(row[0]) if row else None


# ============================================================================
# AUTHORSHIP — the false class, defined by WHO WROTE a memory (2026-10-26).
# ============================================================================
# The template list above is a deny-list, and it was always one organ behind:
# the 292 contradictions still live after round 1 joined "Sleep Loop Reflection",
# "APPROVED ENHANCEMENT" and weekly-digest notes, three families nobody listed,
# and the owner approved 30 of them on 2026-10-26. Of 3,993 contradictions this
# engine ever proposed, not one joined two memories a person stored.
#
# So the rule is now an ALLOW-list: a pair is judged only when BOTH ends were
# written by the store/update path (embedding_provenance.organ, stamped in the
# same transaction as the memory). An organ added next year is excluded without
# anyone remembering to list it. A brain with no provenance table cannot say who
# wrote anything; there the engine keeps the template rule alone and says so.
#
# What this does NOT do: route the owner's own memories to the scan. Measured on
# a copy (Council, 2026-10-26), the 250 newest authored memories yielded 944
# proposals and 0 of 30 sampled were real contradictions, so authored intake
# stays closed until a detector that can tell exists. Until then the nightly
# scan drains organ rows and proposes nothing; organ_yield_sentinel's
# belief_engine.propose row counts only authored queue rows as input, so it reads
# STARVED, not BROKEN.

#: The writers whose memories a person authored.
AUTHORED_WRITERS: Tuple[str, ...] = ("claude_memory.store", "claude_memory.update")

#: The quarantine family for a pair with an end no person authored.
NON_AUTHORED_FAMILY: str = "not_authored"
NON_AUTHORED_REASON: str = ("quarantined: an end was not authored by a person "
                            "(an organ wrote it, or its writer is unknown) (belief_quarantine)")
NON_AUTHORED_PAIR_DECISION: str = "non_authored_pair"


def authored_ids(conn: sqlite3.Connection) -> Optional[set]:
    """The ids of every memory a person authored, or None when this brain has no
    provenance table (authorship unknowable: callers keep the template rule only)."""
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='embedding_provenance'").fetchone() is None:
            return None
        ph = ",".join("?" * len(AUTHORED_WRITERS))
        return {r[0] for r in conn.execute(
            f"SELECT memory_id FROM embedding_provenance WHERE organ IN ({ph})", AUTHORED_WRITERS)}
    except sqlite3.Error:
        return None


def false_family(family_a: Optional[str], family_b: Optional[str], id_a: int, id_b: int,
                 authored: Optional[set]) -> Optional[str]:
    """The false-class family a pair belongs to, or None when it may be judged.
    Same organ template on both ends -> that template; otherwise, when authorship
    is knowable, any end not authored -> NON_AUTHORED_FAMILY."""
    if same_template(family_a, family_b):
        return family_a
    if authored is not None and not (id_a in authored and id_b in authored):
        return NON_AUTHORED_FAMILY
    return None


def _reason_for(family: str) -> str:
    return NON_AUTHORED_REASON if family == NON_AUTHORED_FAMILY else QUARANTINE_REASON


# ============================================================================
# numpy is optional-but-expected. We degrade cosine to a pure-python dot
# product if numpy is somehow absent, so the gate never hard-depends on it.
# ============================================================================

try:  # pragma: no cover - environment dependent
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None


# ============================================================================
# Embedding access + cosine (the GATE).
# ============================================================================
# memory_embeddings_v2 is a sqlite-vec (vec0) VIRTUAL table. Reading its
# `embedding` column as raw float32 bytes requires the sqlite-vec extension to
# be loaded on the connection. The live recall path loads it; but the belief
# engine must be self-sufficient (the nightly sweep / remediation may run on a
# bare connection). So we lazily, best-effort load sqlite-vec the first time we
# touch embeddings. If it cannot be loaded, embedding reads degrade to None and
# the engine treats every pair as "no embedding" (fail-graceful, never raises).

_VEC_LOADED: Dict[int, bool] = {}  # id(conn) -> attempted/loaded (per connection)


def _ensure_vec_loaded(conn: sqlite3.Connection) -> bool:
    """Best-effort load of the sqlite-vec extension on `conn`. Idempotent per
    connection. Returns True if vec0 is usable, False otherwise (graceful)."""
    cid = id(conn)
    if cid in _VEC_LOADED:
        return _VEC_LOADED[cid]
    # Already loaded by the host (recall path)?
    try:
        conn.execute("SELECT vec_version()")
        _VEC_LOADED[cid] = True
        return True
    except sqlite3.OperationalError:
        pass
    try:
        import sqlite_vec  # type: ignore
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _VEC_LOADED[cid] = True
        return True
    except Exception as e:
        logger.debug("belief_engine: sqlite-vec not loadable (%s); embeddings degrade to None", e)
        _VEC_LOADED[cid] = False
        return False


def _unpack_embedding(blob: Optional[bytes]) -> Optional[Any]:
    """Turn a raw float32 embedding blob into a vector (numpy array or list).

    The memory_embeddings_v2 vec0 table stores 1024 little-endian float32s
    (4096 bytes). Returns None on any malformed/absent blob (fail-graceful).
    """
    if not blob:
        return None
    try:
        n = len(blob) // 4
        if n == 0:
            return None
        if _np is not None:
            return _np.frombuffer(blob, dtype=_np.float32)
        return list(struct.unpack(f"{n}f", blob))
    except Exception:
        return None


def get_embedding(conn: sqlite3.Connection, memory_id: int) -> Optional[Any]:
    """Fetch a memory's jina-1024 embedding as a vector, or None.

    The memory_embeddings_v2 vec0 table needs the sqlite-vec extension loaded to
    read its `embedding` column (else SELECT raises 'no such module: vec0'). We
    ensure it is loaded (best-effort) then pull the stored float32 blob and
    compute cosine ourselves (no vector MATCH). Fail-graceful: a missing
    extension / absent row returns None and the caller treats the pair as
    un-judgeable (never NLI'd, never proposed).
    """
    _ensure_vec_loaded(conn)
    try:
        row = conn.execute(
            f"SELECT embedding FROM {EMBEDDING_TABLE} WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return _unpack_embedding(row[0])


def cosine(vec_a: Any, vec_b: Any) -> Optional[float]:
    """Cosine similarity of two embedding vectors, or None if uncomputable."""
    if vec_a is None or vec_b is None:
        return None
    try:
        if _np is not None:
            a = _np.asarray(vec_a, dtype=_np.float32)
            b = _np.asarray(vec_b, dtype=_np.float32)
            na = float(_np.linalg.norm(a))
            nb = float(_np.linalg.norm(b))
            if na == 0.0 or nb == 0.0:
                return None
            return float(_np.dot(a, b) / (na * nb))
        # Pure-python fallback.
        dot = sum(x * y for x, y in zip(vec_a, vec_b))
        na = sum(x * x for x in vec_a) ** 0.5
        nb = sum(x * x for x in vec_b) ** 0.5
        if na == 0.0 or nb == 0.0:
            return None
        return dot / (na * nb)
    except Exception:
        return None


def pair_cosine(conn: sqlite3.Connection, id_a: int, id_b: int) -> Optional[float]:
    """Cosine between two memories' embeddings (the GATE input). None if absent."""
    return cosine(get_embedding(conn, id_a), get_embedding(conn, id_b))


# ============================================================================
# NLI access (gated, graceful-absent).
# ============================================================================

def _get_nli_model(model: Any = None) -> Optional[Any]:
    """Resolve the NLI CrossEncoder. A caller-supplied `model` (or a MOCK in
    tests) wins; otherwise route through model_garden.get_model('nli').

    GRACEFUL-ABSENT: any import failure / absent model returns None. Callers
    must treat None as "NLI unavailable" and degrade to a no-op (never raise).
    """
    if model is not None:
        return model
    try:
        import model_garden  # type: ignore
    except Exception:
        try:
            from . import model_garden  # type: ignore
        except Exception:
            return None
    try:
        return model_garden.get_model("nli")
    except Exception:
        return None


def _memory_text(conn: sqlite3.Connection, memory_id: int, *, limit: int = 600) -> str:
    """The text NLI reasons over: prefer summary, fall back to content head."""
    try:
        row = conn.execute(
            "SELECT summary, content FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None:
        return ""
    summary, content = row[0], row[1]
    text = summary or content or ""
    return text[:limit]


def nli_score_pair(
    nli_model: Any,
    premise: str,
    hypothesis: str,
    *,
    symmetric: bool = True,
) -> Optional[Dict[str, Any]]:
    """Run NLI on (premise, hypothesis) and return {label, score, contradiction,
    entailment, neutral}, or None if the model is absent / errors.

    For the SYMMETRIC `contradicts` relation we score BOTH directions and take
    the stronger contradiction signal (a real contradiction is direction-robust;
    scoring both ways guards against asymmetric phrasing). The returned `label`
    is the argmax of the chosen (max-contradiction) direction.
    """
    if nli_model is None:
        return None
    try:
        pairs = [(premise, hypothesis)]
        if symmetric:
            pairs.append((hypothesis, premise))
        raw = nli_model.predict(pairs, apply_softmax=True)
    except Exception as e:
        logger.warning("belief_engine.nli_score_pair: predict failed (%s)", e)
        return None

    def _row(i: int) -> Tuple[float, float, float]:
        r = raw[i]
        # r is a sequence of 3 floats over NLI_LABELS.
        return float(r[0]), float(r[1]), float(r[2])

    best = _row(0)
    best_idx = 0
    if symmetric and len(raw) > 1:
        alt = _row(1)
        # Choose the direction with the stronger contradiction probability.
        if alt[NLI_CONTRADICTION_IDX] > best[NLI_CONTRADICTION_IDX]:
            best = alt
            best_idx = 1
    contradiction, entailment, neutral = best
    triple = (contradiction, entailment, neutral)
    argmax = max(range(3), key=lambda i: triple[i])
    return {
        "label": NLI_LABELS[argmax],
        "score": float(triple[argmax]),
        "contradiction": contradiction,
        "entailment": entailment,
        "neutral": neutral,
        "direction": best_idx,
    }


# ============================================================================
# State / watermark helpers.
# ============================================================================

def _state_get(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        row = conn.execute(
            "SELECT value FROM belief_engine_state WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return default
    return row[0] if row else default


def _state_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO belief_engine_state (key, value, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
        (key, value),
    )


def get_cosine_gate(conn: sqlite3.Connection) -> float:
    """The active cosine gate (per-DB override or the module default)."""
    raw = _state_get(conn, "cosine_gate_threshold")
    if raw is None:
        return DEFAULT_COSINE_GATE
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_COSINE_GATE


# ============================================================================
# trust / foundation integration.
# ============================================================================

def _edge_authority(conn: sqlite3.Connection, a_id: int, b_id: int) -> Optional[float]:
    """trust_tier.edge_authority(a,b) = min(trust(a),trust(b)); None if absent."""
    try:
        import trust_tier  # type: ignore
    except Exception:
        try:
            from . import trust_tier  # type: ignore
        except Exception:
            return None
    try:
        return float(trust_tier.edge_authority(conn, a_id, b_id))
    except Exception:
        return None


def _foundation_score(conn: sqlite3.Connection, memory_id: int) -> Optional[float]:
    try:
        row = conn.execute(
            "SELECT foundation_score FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def _is_elevated(conn: sqlite3.Connection, a_id: int, b_id: int,
                 *, threshold: float = DEFAULT_FOUNDATION_ELEVATED) -> bool:
    """True if EITHER endpoint is a foundation memory at/above `threshold`."""
    for mid in (a_id, b_id):
        fs = _foundation_score(conn, mid)
        if fs is not None and fs >= threshold:
            return True
    return False


# ============================================================================
# scan_pair — the unit of judgement (GATE FIRST, THEN NLI).
# ============================================================================

def scan_pair(
    conn: sqlite3.Connection,
    id_a: int,
    id_b: int,
    *,
    nli_model: Any = None,
    cosine_gate: Optional[float] = None,
    nli_confidence: float = DEFAULT_NLI_CONFIDENCE,
    precomputed_cosine: Optional[float] = None,
) -> Dict[str, Any]:
    """Judge a single ordered/unordered pair (the relation is symmetric).

    THE INVARIANT: the cosine gate is consulted FIRST. If cosine < gate, the
    function returns decision='gate_rejected' and NEVER touches NLI — even if a
    (mock) NLI would have screamed contradiction. This is the anti-poison core.

    Returns a dict:
        {id_a, id_b, cosine, gate, gate_passed,
         label, nli_score, contradiction, entailment, neutral,
         edge_authority, elevated, decision}
    where decision is one of:
        'gate_rejected'    -> cosine below the gate (topically unrelated)
        'no_embedding'     -> one/both embeddings absent (cannot judge)
        'nli_unavailable'  -> gate passed but NLI model is None (no-op degrade)
        'template_pair'    -> both ends carry the SAME organ template (never NLI'd)
        'non_authored_pair'-> an end no person authored (never NLI'd; 2026-10-26)
        'contradicts'      -> gate passed + NLI contradiction >= nli_confidence
        'not_contradicts'  -> gate passed + NLI says neutral/entailment (or weak)
    The decision is a function of (cosine, NLI label, NLI score) — NEVER NLI
    alone.
    """
    gate = get_cosine_gate(conn) if cosine_gate is None else cosine_gate
    # THE TEMPLATE PRECONDITION (round 1, L5) — before the cosine read, because a
    # shared organ head is exactly what lifts these pairs over the gate.
    _fam = false_family(_family_of(conn, id_a), _family_of(conn, id_b), id_a, id_b,
                        authored_ids(conn))
    if _fam is not None:
        return {
            "id_a": id_a, "id_b": id_b, "cosine": precomputed_cosine, "gate": gate,
            "gate_passed": False, "label": None, "nli_score": None,
            "contradiction": None, "entailment": None, "neutral": None,
            "edge_authority": None, "elevated": False,
            "decision": (NON_AUTHORED_PAIR_DECISION if _fam == NON_AUTHORED_FAMILY
                         else TEMPLATE_PAIR_DECISION),
        }
    cos = precomputed_cosine if precomputed_cosine is not None else pair_cosine(conn, id_a, id_b)

    result: Dict[str, Any] = {
        "id_a": id_a,
        "id_b": id_b,
        "cosine": cos,
        "gate": gate,
        "gate_passed": False,
        "label": None,
        "nli_score": None,
        "contradiction": None,
        "entailment": None,
        "neutral": None,
        "edge_authority": None,
        "elevated": False,
        "decision": None,
    }

    # Embedding absent -> cannot judge relatedness -> we MUST NOT call NLI.
    if cos is None:
        result["decision"] = "no_embedding"
        return result

    # ---- THE GATE. cosine < threshold -> topically unrelated -> NEVER NLI. ----
    if cos < gate:
        result["decision"] = "gate_rejected"
        return result

    result["gate_passed"] = True

    # The pair is related enough to be eligible for NLI adjudication.
    model = _get_nli_model(nli_model)
    if model is None:
        # Graceful-absent: the gate passed but we have no model. No-op degrade.
        result["decision"] = "nli_unavailable"
        return result

    nli = nli_score_pair(
        model,
        _memory_text(conn, id_a),
        _memory_text(conn, id_b),
        symmetric=True,
    )
    if nli is None:
        result["decision"] = "nli_unavailable"
        return result

    result.update({
        "label": nli["label"],
        "nli_score": nli["score"],
        "contradiction": nli["contradiction"],
        "entailment": nli["entailment"],
        "neutral": nli["neutral"],
    })

    # Edge authority + foundation elevation are computed for the related pair.
    result["edge_authority"] = _edge_authority(conn, id_a, id_b)
    result["elevated"] = _is_elevated(conn, id_a, id_b)

    # ---- the JOINT decision: f(cosine, NLI label, NLI score). ----
    if nli["contradiction"] >= nli_confidence:
        result["decision"] = "contradicts"
    else:
        result["decision"] = "not_contradicts"
    return result


# ============================================================================
# edge_proposals writer + approve/reject.
# ============================================================================

def _existing_contradicts_edge(conn: sqlite3.Connection, a: int, b: int) -> bool:
    """True if a CURRENT contradicts edge already exists in either direction."""
    try:
        row = conn.execute(
            "SELECT 1 FROM memory_edges "
            "WHERE predicate = 'contradicts' AND valid_until IS NULL "
            "AND ((source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)) "
            "LIMIT 1",
            (a, b, b, a),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _pair_already_decided(conn: sqlite3.Connection, kind: str, a: int, b: int) -> bool:
    """Rejection-suppression: True if this pair+kind already has a non-pending
    proposal (rejected OR approved/enacted) OR an open pending one. A rejected
    pair must not be re-proposed; an already-pending one must not be duplicated.
    For the symmetric contradicts relation we check both orderings.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM edge_proposals "
            "WHERE proposal_kind = ? "
            "AND ((source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)) "
            "LIMIT 1",
            (kind, a, b, b, a),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def propose(
    conn: sqlite3.Connection,
    kind: str,
    *,
    source_id: Optional[int] = None,
    target_id: Optional[int] = None,
    predicate: Optional[str] = None,
    edge_id: Optional[int] = None,
    nli_label: Optional[str] = None,
    nli_score: Optional[float] = None,
    cosine_val: Optional[float] = None,
    edge_authority: Optional[float] = None,
    elevated: bool = False,
    rationale: Optional[str] = None,
    report_batch: Optional[str] = None,
) -> Optional[int]:
    """Write ONE edge_proposals row. Returns the proposal id, or None if it was
    suppressed (duplicate / already-decided pair). NEVER touches memory_edges.
    """
    if kind not in ("add_contradicts", "invalidate_edge", "supersede"):
        raise ValueError(f"unknown proposal_kind {kind!r}")
    try:
        cur = conn.execute(
            "INSERT INTO edge_proposals "
            "(proposal_kind, source_id, target_id, predicate, edge_id, "
            " nli_label, nli_score, cosine, edge_authority, elevated, "
            " rationale, report_batch, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (kind, source_id, target_id, predicate, edge_id,
             nli_label, nli_score, cosine_val, edge_authority,
             1 if elevated else 0, rationale, report_batch),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # The UNIQUE(kind, source, target, edge_id) constraint fired — already
        # proposed. Suppress silently (idempotent).
        return None


def approve_proposals(
    conn: sqlite3.Connection,
    proposal_ids: Sequence[int],
    *,
    record_prov: bool = True,
) -> Dict[str, Any]:
    """ENACT approved proposals. THE ONLY PLACE memory_edges changes.

    add_contradicts -> create_edge(..., contradicts) (symmetric pair auto-added
                       by temporal_edges.create_edge).
    invalidate_edge -> UPDATE memory_edges SET valid_until = now WHERE id = edge_id
                       (NEVER DELETE).
    supersede       -> same valid_until set on the superseded edge_id.

    On a contradicts enactment we record provenance (transformation_type
    'edge_inference') linking the new edge's endpoints. Fail-graceful per row.
    """
    enacted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for pid in proposal_ids:
        row = conn.execute(
            "SELECT proposal_kind, source_id, target_id, predicate, edge_id, "
            "nli_score, status FROM edge_proposals WHERE id = ?", (pid,)
        ).fetchone()
        if row is None:
            failed.append({"id": pid, "error": "proposal not found"})
            continue
        kind, src, tgt, predicate, edge_id, nli_score, status = row
        if status in ("enacted",):
            skipped.append({"id": pid, "reason": "already enacted"})
            continue

        try:
            if kind == "add_contradicts":
                enacted_edge_id = _enact_add_contradicts(
                    conn, src, tgt, nli_score, record_prov=record_prov
                )
                conn.execute(
                    "UPDATE edge_proposals SET status = 'enacted', "
                    "decided_at = datetime('now'), enacted_at = datetime('now'), "
                    "enacted_edge_id = ? WHERE id = ?",
                    (enacted_edge_id, pid),
                )
                enacted.append({"id": pid, "kind": kind, "edge_id": enacted_edge_id})

            elif kind in ("invalidate_edge", "supersede"):
                touched = _enact_invalidate(conn, edge_id, symmetric_pair=(src, tgt))
                conn.execute(
                    "UPDATE edge_proposals SET status = 'enacted', "
                    "decided_at = datetime('now'), enacted_at = datetime('now') "
                    "WHERE id = ?",
                    (pid,),
                )
                enacted.append({"id": pid, "kind": kind, "edges_invalidated": touched})

            else:
                failed.append({"id": pid, "error": f"unknown kind {kind!r}"})
        except Exception as e:  # never let one bad proposal abort the batch
            conn.execute(
                "UPDATE edge_proposals SET status = 'pending', error_log = ? WHERE id = ?",
                (str(e), pid),
            )
            failed.append({"id": pid, "error": str(e)})

    conn.commit()
    return {"enacted": enacted, "failed": failed, "skipped": skipped}


def reject_proposals(conn: sqlite3.Connection, proposal_ids: Sequence[int],
                     *, reason: Optional[str] = None) -> Dict[str, Any]:
    """Mark proposals rejected. Their pairs are then SUPPRESSED from future
    scans (a rejected pair is never re-proposed). NEVER touches memory_edges.
    """
    rejected: List[int] = []
    for pid in proposal_ids:
        cur = conn.execute(
            "UPDATE edge_proposals SET status = 'rejected', "
            "decided_at = datetime('now'), "
            "error_log = COALESCE(?, error_log) "
            "WHERE id = ? AND status = 'pending'",
            (reason, pid),
        )
        if cur.rowcount > 0:
            rejected.append(pid)
    conn.commit()
    return {"rejected": rejected}


def _edge_extraction_method() -> str:
    """The extraction_method the belief engine stamps on edges it authors.

    temporal_edges.VALID_EXTRACTION_METHODS == {manual, heuristic, llm_haiku,
    llm_sonnet, sleep_loop}. 'belief_engine' is NOT in that frozenset, so to
    avoid a create_edge ValueError WITHOUT a migration that loosens the live
    CHECK, the belief engine authors under 'manual' (a human-gated approval IS a
    manual act) and records the true authorship in the edge metadata
    {'authored_by':'belief_engine'} + provenance. If the orchestrator chooses to
    extend VALID_EXTRACTION_METHODS to include 'belief_engine', flip this to
    return 'belief_engine'.
    """
    return BELIEF_EXTRACTION_METHOD


def _enact_add_contradicts(conn: sqlite3.Connection, a: int, b: int,
                           nli_score: Optional[float], *, record_prov: bool) -> Optional[int]:
    """Create the contradicts edge (idempotent on the symmetric UNIQUE)."""
    import importlib
    try:
        te = importlib.import_module("temporal_edges")
    except Exception:
        from . import temporal_edges as te  # type: ignore

    conf = 0.8
    if nli_score is not None:
        try:
            conf = max(0.0, min(1.0, float(nli_score)))
        except (TypeError, ValueError):
            conf = 0.8

    edge_id = te.create_edge(
        conn, a, b, "contradicts",
        confidence=conf,
        extraction_method=_edge_extraction_method(),
        metadata={"authored_by": "belief_engine", "nli_score": nli_score},
    )

    if record_prov:
        try:
            import importlib as _il
            pg = _il.import_module("provenance_graph")
        except Exception:
            try:
                from . import provenance_graph as pg  # type: ignore
            except Exception:
                pg = None
        if pg is not None:
            try:
                pg.record_provenance(
                    conn, derived_memory_id=a, source_memory_ids=[b],
                    transformation_type="edge_inference",
                    agent="belief_engine", confidence=conf,
                    metadata={"predicate": "contradicts"},
                )
            except Exception as e:  # provenance is best-effort, never fatal
                logger.debug("belief_engine: provenance record skipped (%s)", e)
    return edge_id


def _enact_invalidate(conn: sqlite3.Connection, edge_id: Optional[int],
                      *, symmetric_pair: Optional[Tuple[int, int]] = None) -> int:
    """Set valid_until on an edge (and its symmetric twin if contradicts).
    NEVER DELETE. Returns the number of edge rows whose valid_until was set.
    """
    if edge_id is None:
        return 0
    now = datetime.now().isoformat()
    touched = 0

    # Look up the edge to learn its predicate + endpoints (for the symmetric twin).
    row = conn.execute(
        "SELECT source_id, target_id, predicate, valid_until FROM memory_edges WHERE id = ?",
        (edge_id,),
    ).fetchone()
    if row is None:
        return 0
    src, tgt, predicate, existing_vu = row

    if existing_vu is None:
        cur = conn.execute(
            "UPDATE memory_edges SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
            (now, edge_id),
        )
        touched += cur.rowcount

    # The contradicts relation is symmetric: invalidate the reverse edge too so
    # the bi-temporal view is consistent (both directions retire together).
    if predicate == "contradicts":
        cur = conn.execute(
            "UPDATE memory_edges SET valid_until = ? "
            "WHERE predicate = 'contradicts' AND source_id = ? AND target_id = ? "
            "AND valid_until IS NULL",
            (now, tgt, src),
        )
        touched += cur.rowcount
    return touched


# ============================================================================
# enqueue_for_scan — the cheap store-path hook (NO NLI).
# ============================================================================

def enqueue_for_scan(conn: sqlite3.Connection, memory_id: int) -> bool:
    """Record a dirty memory id for the nightly belief sweep. The ONLY belief
    work the store hot path does. NO NLI, NO embedding read, NO trust call —
    just one INSERT. Idempotent (the UNIQUE(memory_id, processed_at) suppresses
    a duplicate while a prior enqueue is still unprocessed). Fail-graceful: a
    missing belief_scan_queue table (pre-migration) is swallowed so store()
    never breaks.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO belief_scan_queue (memory_id) VALUES (?)",
            (memory_id,),
        )
        return True
    except sqlite3.Error:
        return False


# ============================================================================
# nightly_scan — the O(dirty x k) sweep.
# ============================================================================

def _top_k_neighbors(conn: sqlite3.Connection, memory_id: int, k: int,
                     *, gate: float) -> List[Tuple[int, float]]:
    """The top-k embedding neighbours of `memory_id` whose cosine clears the
    gate. Pure python cosine over the candidate pool (NO sqlite-vec MATCH needed
    — keeps the engine free of the extension dependency). Returns [(id, cos)].

    To stay O(dirty x pool) rather than O(n^2) per dirty memory we score against
    a bounded candidate pool: same-project + recent memories that have an
    embedding (mirrors temporal_edges Tier-3's bounded 200-row candidate window).
    """
    me = get_embedding(conn, memory_id)
    if me is None:
        return []
    try:
        proj_row = conn.execute(
            "SELECT source_project FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    except sqlite3.Error:
        return []
    project = proj_row[0] if proj_row else None

    # Candidate pool: bounded. Prefer same-project (the cheap relatedness prior)
    # and fall back to a recent slice if the project is sparse.
    rows: List[Tuple[int]] = []
    try:
        if project:
            rows = conn.execute(
                f"SELECT m.id FROM memories m "
                f"JOIN {EMBEDDING_TABLE} e ON e.memory_id = m.id "
                f"WHERE m.source_project = ? AND m.id != ? AND m.is_archived = 0 "
                f"ORDER BY m.created_at DESC LIMIT 200",
                (project, memory_id),
            ).fetchall()
        if not rows:
            rows = conn.execute(
                f"SELECT m.id FROM memories m "
                f"JOIN {EMBEDDING_TABLE} e ON e.memory_id = m.id "
                f"WHERE m.id != ? AND m.is_archived = 0 "
                f"ORDER BY m.created_at DESC LIMIT 200",
                (memory_id,),
            ).fetchall()
    except sqlite3.Error:
        return []

    scored: List[Tuple[int, float]] = []
    for (other_id,) in rows:
        oe = get_embedding(conn, other_id)
        cos = cosine(me, oe)
        if cos is not None and cos >= gate:
            scored.append((other_id, cos))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def nightly_scan(
    conn: sqlite3.Connection,
    *,
    limit: Optional[int] = None,
    neighbor_k: int = DEFAULT_NEIGHBOR_K,
    nli_model: Any = None,
    nli_confidence: float = DEFAULT_NLI_CONFIDENCE,
    report_batch: Optional[str] = None,
) -> Dict[str, Any]:
    """Drain the belief_scan_queue dirty-set and emit add_contradicts proposals.

    For each dirty memory: find its top-k embedding neighbours that clear the
    cosine gate (THE GATE is applied in _top_k_neighbors, so NLI is only ever
    asked about already-related pairs), NLI-judge each, and on a confident
    contradiction emit an add_contradicts PROPOSAL (never an edge). Suppress
    pairs that already have a contradicts edge or a prior (rejected/decided)
    proposal. Mark the dirty rows processed; advance the watermark. Batch all
    proposals under ONE report_batch (banner +1, not +N).

    O(dirty x neighbor_k) NLI calls — never O(n^2). `nli_call_count` is returned
    so a test can assert the bound. Graceful-absent: with no NLI model the sweep
    still drains the queue (cosine gate only), emits 0 contradiction proposals,
    and never raises.
    """
    gate = get_cosine_gate(conn)
    batch = report_batch or f"belief-sweep-{datetime.now().strftime('%Y%m%dT%H%M%S')}"

    try:
        rows = conn.execute(
            "SELECT id, memory_id FROM belief_scan_queue WHERE processed_at IS NULL "
            "ORDER BY enqueued_at ASC" + (f" LIMIT {int(limit)}" if limit else ""),
        ).fetchall()
    except sqlite3.Error as e:
        return {"status": "error", "error": str(e), "proposals": 0}

    model = _get_nli_model(nli_model)
    nli_available = model is not None

    proposals_made = 0
    pairs_gated_in = 0
    template_pairs_skipped = 0
    non_authored_pairs_skipped = 0
    authored = authored_ids(conn)
    nli_call_count = 0
    processed_queue_ids: List[int] = []
    seen_pairs: set = set()

    for queue_id, mem_id in rows:
        processed_queue_ids.append(queue_id)
        neighbors = _top_k_neighbors(conn, mem_id, neighbor_k, gate=gate)
        mem_family = _family_of(conn, mem_id) if neighbors else None
        for other_id, cos in neighbors:
            pair_key = (min(mem_id, other_id), max(mem_id, other_id))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            # The preconditions: a same-template pair, or a pair with an end no
            # person authored, is never NLI'd.
            fam = false_family(mem_family, _family_of(conn, other_id), mem_id, other_id, authored)
            if fam is not None:
                if fam == NON_AUTHORED_FAMILY:
                    non_authored_pairs_skipped += 1
                else:
                    template_pairs_skipped += 1
                continue
            pairs_gated_in += 1

            # Suppress: existing current contradicts edge OR a decided proposal.
            if _existing_contradicts_edge(conn, mem_id, other_id):
                continue
            if _pair_already_decided(conn, "add_contradicts", mem_id, other_id):
                continue

            if not nli_available:
                # Graceful degrade: gate passed but no model -> emit nothing.
                continue

            nli = nli_score_pair(
                model, _memory_text(conn, mem_id), _memory_text(conn, other_id),
                symmetric=True,
            )
            nli_call_count += 1
            if nli is None:
                continue
            if nli["contradiction"] >= nli_confidence:
                auth = _edge_authority(conn, mem_id, other_id)
                elevated = _is_elevated(conn, mem_id, other_id)
                rationale = (
                    f"cosine {cos:.3f} >= gate {gate:.2f}; NLI contradiction "
                    f"{nli['contradiction']:.3f} (label {nli['label']})"
                )
                pid = propose(
                    conn, "add_contradicts",
                    source_id=mem_id, target_id=other_id, predicate="contradicts",
                    nli_label=nli["label"], nli_score=nli["score"],
                    cosine_val=cos, edge_authority=auth, elevated=elevated,
                    rationale=rationale, report_batch=batch,
                )
                if pid is not None:
                    proposals_made += 1

    # Mark the drained queue rows processed.
    if processed_queue_ids:
        placeholders = ",".join("?" * len(processed_queue_ids))
        conn.execute(
            f"UPDATE belief_scan_queue SET processed_at = datetime('now') "
            f"WHERE id IN ({placeholders})",
            processed_queue_ids,
        )

    # Advance the watermark.
    _state_set(conn, "last_belief_scan_at", datetime.now().isoformat())
    conn.commit()

    return {
        "status": "ok",
        "report_batch": batch,
        "dirty_processed": len(processed_queue_ids),
        "pairs_gated_in": pairs_gated_in,
        "template_pairs_skipped": template_pairs_skipped,
        "non_authored_pairs_skipped": non_authored_pairs_skipped,
        "authorship_known": authored is not None,
        "nli_call_count": nli_call_count,
        "nli_available": nli_available,
        "proposals": proposals_made,
        "cosine_gate": gate,
    }


# ============================================================================
# remediate_heuristic_contradicts — the one-time poison cleanup.
# ============================================================================

def remediate_heuristic_contradicts(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    nli_model: Any = None,
    cosine_gate: Optional[float] = None,
    nli_confidence: float = DEFAULT_NLI_CONFIDENCE,
    report_batch: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-adjudicate EVERY heuristic `contradicts` edge and propose invalidating
    the poison, KEEPING genuine high-cosine NLI-confirmed contradictions.

    The decision per edge is f(cosine, NLI label, NLI score):
        cosine < gate                          -> INVALIDATE (topically unrelated
                                                  poison; the primary 416-catcher)
        cosine >= gate AND NLI != contradiction
            (or contradiction < nli_confidence) -> INVALIDATE (related but not
                                                  actually contradicting)
        cosine >= gate AND NLI=contradiction
            (>= nli_confidence)                 -> KEEP (genuine)

    Writes invalidate_edge PROPOSALS (NEVER touches memory_edges in dry_run; even
    when dry_run=False it ONLY writes proposals — enactment is via
    approve_proposals through the approvals surface). Returns the tallies the
    generation report needs.

    The symmetric contradicts twins are de-duplicated: one decision per unordered
    pair; on enactment _enact_invalidate retires both directions.
    """
    gate = get_cosine_gate(conn) if cosine_gate is None else cosine_gate
    batch = report_batch or f"belief-remediation-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    model = _get_nli_model(nli_model)

    try:
        edges = conn.execute(
            "SELECT id, source_id, target_id, confidence FROM memory_edges "
            "WHERE predicate = 'contradicts' AND extraction_method = 'heuristic' "
            "AND valid_until IS NULL",
        ).fetchall()
    except sqlite3.Error as e:
        return {"status": "error", "error": str(e)}

    # De-dup symmetric twins: keep one representative edge per unordered pair.
    by_pair: Dict[Tuple[int, int], Tuple[int, int, int, float]] = {}
    for eid, s, t, conf in edges:
        key = (min(s, t), max(s, t))
        if key not in by_pair:
            by_pair[key] = (eid, s, t, conf)

    tally = {
        "invalidate_low_cosine": 0,
        "invalidate_nli_not_contradiction": 0,
        "invalidate_no_embedding": 0,
        "invalidate_nli_unavailable": 0,
        "keep_genuine": 0,
        "proposals_written": 0,
        "edges_total": len(edges),
        "unique_pairs": len(by_pair),
    }
    keep_samples: List[Dict[str, Any]] = []
    invalidate_samples: List[Dict[str, Any]] = []

    # Count how many of the conf-0.244 "416" cohort end up flagged.
    poison_416_flagged = 0
    poison_416_total = 0

    for (lo, hi), (eid, s, t, conf) in by_pair.items():
        is_416 = abs(conf - 0.244313) < 1e-4 or abs(conf - 0.24) < 0.02
        if is_416:
            poison_416_total += 1

        judged = scan_pair(
            conn, s, t,
            nli_model=model, cosine_gate=gate, nli_confidence=nli_confidence,
        )
        decision = judged["decision"]
        cos = judged["cosine"]

        flag_invalidate = False
        reason = ""
        if decision == "no_embedding":
            # No embedding -> cannot prove relatedness -> conservatively treat
            # as un-verifiable poison (heuristic edge with no support).
            tally["invalidate_no_embedding"] += 1
            flag_invalidate = True
            reason = "no embedding to verify relatedness"
        elif decision == "gate_rejected":
            tally["invalidate_low_cosine"] += 1
            flag_invalidate = True
            reason = f"cosine {cos:.3f} < gate {gate:.2f} (topically unrelated)"
        elif decision == "nli_unavailable":
            # Gate passed but NLI absent: we CANNOT confirm a genuine
            # contradiction, so we do NOT invalidate a possibly-real edge on a
            # half-provisioned machine. Leave it (count, don't flag).
            tally["invalidate_nli_unavailable"] += 1
            reason = "NLI unavailable; left in place (cannot confirm)"
        elif decision == "not_contradicts":
            tally["invalidate_nli_not_contradiction"] += 1
            flag_invalidate = True
            reason = (f"cosine {cos:.3f} >= gate but NLI={judged['label']} "
                      f"(contradiction {judged['contradiction']:.3f} < {nli_confidence})")
        elif decision in (TEMPLATE_PAIR_DECISION, NON_AUTHORED_PAIR_DECISION):
            tally["skipped_false_class"] = tally.get("skipped_false_class", 0) + 1
            reason = f"{decision}: never judged (the quarantine retires this class)"
        elif decision == "contradicts":
            tally["keep_genuine"] += 1
            if len(keep_samples) < 25:
                keep_samples.append({
                    "edge_id": eid, "a": s, "b": t, "cosine": round(cos, 3),
                    "contradiction": round(judged["contradiction"], 3),
                })

        if flag_invalidate:
            if is_416:
                poison_416_flagged += 1
            if len(invalidate_samples) < 25:
                invalidate_samples.append({
                    "edge_id": eid, "a": s, "b": t,
                    "cosine": (round(cos, 3) if cos is not None else None),
                    "reason": reason,
                })
            if not dry_run:
                pid = propose(
                    conn, "invalidate_edge",
                    source_id=s, target_id=t, predicate="contradicts", edge_id=eid,
                    nli_label=judged.get("label"), nli_score=judged.get("nli_score"),
                    cosine_val=cos, edge_authority=judged.get("edge_authority"),
                    elevated=judged.get("elevated", False),
                    rationale=reason, report_batch=batch,
                )
                if pid is not None:
                    tally["proposals_written"] += 1

    # Bookkeeping for the report.
    total_invalidate = (tally["invalidate_low_cosine"]
                        + tally["invalidate_nli_not_contradiction"]
                        + tally["invalidate_no_embedding"])
    if not dry_run:
        conn.commit()

    return {
        "status": "ok",
        "dry_run": dry_run,
        "report_batch": batch,
        "cosine_gate": gate,
        "nli_confidence": nli_confidence,
        "nli_available": model is not None,
        "tally": tally,
        "total_flagged_invalidate": total_invalidate,
        "poison_416_flagged": poison_416_flagged,
        "poison_416_total": poison_416_total,
        "keep_samples": keep_samples,
        "invalidate_samples": invalidate_samples,
    }


# ============================================================================
# beliefs_as_of — the time-travel read primitive.
# ============================================================================

def beliefs_as_of(
    conn: sqlite3.Connection,
    iso_date: str,
    *,
    predicate: str = "contradicts",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """The bi-temporal read: which belief edges were VALID at `iso_date`.

    An edge is valid at T iff:
        (valid_from IS NULL OR valid_from <= T) AND
        (valid_until IS NULL OR valid_until > T)
    This is what makes invalidation meaningful: beliefs_as_of(pre_invalidation)
    still shows a since-retired edge; beliefs_as_of(now) does not. Returns the
    matching edges (default predicate 'contradicts'). Fail-graceful -> [].
    """
    try:
        rows = conn.execute(
            "SELECT id, source_id, target_id, predicate, confidence, "
            "valid_from, valid_until, extraction_method "
            "FROM memory_edges "
            "WHERE predicate = ? "
            "AND (valid_from IS NULL OR valid_from <= ?) "
            "AND (valid_until IS NULL OR valid_until > ?) "
            "ORDER BY confidence DESC LIMIT ?",
            (predicate, iso_date, iso_date, limit),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "id": r[0], "source_id": r[1], "target_id": r[2], "predicate": r[3],
            "confidence": r[4], "valid_from": r[5], "valid_until": r[6],
            "extraction_method": r[7],
        }
        for r in rows
    ]


# ============================================================================
# pending_count / report helpers (for the approvals banner +1 contract).
# ============================================================================

def pending_report_batches(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """One row per report_batch with pending proposals. The banner counts THESE
    (so a sweep that emits N proposals increments read_pending_count by 1, not N).
    """
    try:
        rows = conn.execute(
            "SELECT report_batch, COUNT(*) AS n, "
            "SUM(CASE WHEN elevated = 1 THEN 1 ELSE 0 END) AS elevated_n "
            "FROM edge_proposals WHERE status = 'pending' "
            "GROUP BY report_batch ORDER BY MIN(created_at) DESC",
        ).fetchall()
    except sqlite3.Error:
        return []
    return [{"report_batch": r[0], "pending": r[1], "elevated": r[2]} for r in rows]


def get_proposals(conn: sqlite3.Connection, *, report_batch: Optional[str] = None,
                  status: str = "pending", limit: int = 200) -> List[Dict[str, Any]]:
    """List proposals (optionally scoped to one batch), elevated first."""
    clauses = ["status = ?"]
    params: List[Any] = [status]
    if report_batch:
        clauses.append("report_batch = ?")
        params.append(report_batch)
    where = " AND ".join(clauses)
    params.append(limit)
    try:
        rows = conn.execute(
            f"SELECT id, proposal_kind, source_id, target_id, predicate, edge_id, "
            f"nli_label, nli_score, cosine, edge_authority, elevated, rationale, "
            f"report_batch, status FROM edge_proposals WHERE {where} "
            f"ORDER BY elevated DESC, edge_authority DESC, id ASC LIMIT ?",
            params,
        ).fetchall()
    except sqlite3.Error:
        return []
    keys = ["id", "proposal_kind", "source_id", "target_id", "predicate",
            "edge_id", "nli_label", "nli_score", "cosine", "edge_authority",
            "elevated", "rationale", "report_batch", "status"]
    return [dict(zip(keys, r)) for r in rows]


def migration_applied(conn: sqlite3.Connection) -> bool:
    """True iff the belief_engine substrate (edge_proposals) exists."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='edge_proposals'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


# ============================================================================
# THE QUARANTINE (round 1, L5) — reversible, never a DELETE.
# ============================================================================
# What the template precondition stops from now on, this retires from the past.
# The table is migration_041 (belief_quarantine); it holds, per touched object,
# the PRE-IMAGE of exactly the fields the quarantine changed and the state it
# wrote, so a release restores an object only while it still reads as the
# quarantine left it (a human change made since is never overwritten).
#
#   edge     memory_edges.valid_until  NULL -> now   (every reader of a current
#                                                     belief filters valid_until
#                                                     IS NULL, so old code that
#                                                     never heard of this table
#                                                     reads it as retired: the
#                                                     rollback is safe by shape)
#   proposal edge_proposals.status     pending -> rejected (a quarantined pair is
#                                                     not re-proposed and cannot
#                                                     be enacted by a bulk approve)
#   gap      gap_inventory.acknowledged 0 -> 1 for the unresolved_contradiction
#                                                     gaps those edges authored
#
# An object that EVER had a quarantine row is never quarantined again: a pair
# Drew released stays released, however many nights the scan runs.

QUARANTINE_TABLE: str = "belief_quarantine"


def quarantine_available(conn: sqlite3.Connection) -> bool:
    """True iff migration_041's table exists on this brain."""
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (QUARANTINE_TABLE,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _gap_label(src_id: int, tgt_id: int, src_sum: Optional[str], tgt_sum: Optional[str]) -> str:
    try:
        import gap_mapping as _gm  # type: ignore
    except Exception:
        from . import gap_mapping as _gm  # type: ignore
    return _gm.contradiction_gap_label(src_id, tgt_id, src_sum, tgt_sum)


def _ever_quarantined(conn: sqlite3.Connection, kind: str) -> set:
    return {r[0] for r in conn.execute(
        f"SELECT object_id FROM {QUARANTINE_TABLE} WHERE object_kind = ?", (kind,))}


def _candidate_edges(conn: sqlite3.Connection):
    """Current contradicts edges, split into the false class (same organ template,
    or an end no person authored) and the rest.
    Returns (class_rows, other_labels): class_rows = [(edge_id, src, tgt, family,
    src_summary, tgt_summary)], other_labels = gap labels of the still-believed
    edges outside the class (a label they share is never acknowledged)."""
    rows = conn.execute(
        "SELECT e.id, e.source_id, e.target_id, substr(a.content, 1, 160), "
        "substr(b.content, 1, 160), a.summary, b.summary, e.metadata "
        "FROM memory_edges e JOIN memories a ON a.id = e.source_id "
        "JOIN memories b ON b.id = e.target_id "
        "WHERE e.predicate = 'contradicts' AND e.valid_until IS NULL"
    ).fetchall()
    authored = authored_ids(conn)
    in_class, other_labels = [], set()
    for eid, s, t, ca, cb, sa, sb, meta in rows:
        # The authorship rule retires only links THIS engine drew (review F13): a
        # contradiction a person or another writer asserted is never retired for
        # touching a memory whose writer is unknown. The template rule stays
        # unconditional.
        try:
            by_engine = (json.loads(meta or "{}") or {}).get("authored_by") == "belief_engine"
        except (ValueError, AttributeError):
            by_engine = False
        fam = false_family(template_family(ca), template_family(cb), s, t,
                           authored if by_engine else None)
        if fam is not None:
            in_class.append((eid, s, t, fam, sa, sb))
        else:
            other_labels.add(_gap_label(s, t, sa, sb)[:500])
    return in_class, other_labels


def quarantine_template_pairs(conn: sqlite3.Connection, *, apply: bool = False) -> Dict[str, Any]:
    """Quarantine every contradiction in the false class (same organ template, or
    since 2026-10-26 an end no person authored): current edges, pending
    proposals, and the unresolved_contradiction gaps those edges feed. The name is
    kept for its callers; the family column says which rule caught each row.

    apply=False is a pure read (the counts a run WOULD touch). Every count in the
    result is derived from the brain at call time. One transaction: all or none.
    """
    if not quarantine_available(conn):
        return {"status": "unavailable",
                "message": "belief_quarantine is absent: apply migrations/migration_041_belief_quarantine.sql"}

    seen_edges = _ever_quarantined(conn, "edge")
    seen_props = _ever_quarantined(conn, "proposal")
    seen_gaps = _ever_quarantined(conn, "gap")

    in_class, other_labels = _candidate_edges(conn)
    edges = [r for r in in_class if r[0] not in seen_edges]

    # The gap labels the class authors: from the edges quarantined now AND the
    # ones a previous run already retired (their gaps may postdate that run).
    label_family: Dict[str, Tuple[str, int, int]] = {}
    for eid, s, t, fam, sa, sb in in_class:
        label_family[_gap_label(s, t, sa, sb)[:500]] = (fam, s, t)
    for s, t, fam, sa, sb in conn.execute(
            f"SELECT q.source_id, q.target_id, q.family, a.summary, b.summary "
            f"FROM {QUARANTINE_TABLE} q JOIN memories a ON a.id = q.source_id "
            f"JOIN memories b ON b.id = q.target_id "
            f"WHERE q.object_kind = 'edge' AND q.released_at IS NULL"):
        label_family[_gap_label(s, t, sa, sb)[:500]] = (fam, s, t)
    for lab in other_labels:
        label_family.pop(lab, None)

    authored = authored_ids(conn)
    props = []
    for pid, s, t, ca, cb, status, decided, err in conn.execute(
            "SELECT p.id, p.source_id, p.target_id, substr(a.content, 1, 160), "
            "substr(b.content, 1, 160), p.status, p.decided_at, p.error_log "
            "FROM edge_proposals p JOIN memories a ON a.id = p.source_id "
            "JOIN memories b ON b.id = p.target_id "
            "WHERE p.proposal_kind = 'add_contradicts' AND p.status = 'pending'"):
        fam = false_family(template_family(ca), template_family(cb), s, t, authored)
        if fam is not None and pid not in seen_props:
            props.append((pid, s, t, fam, {"status": status, "decided_at": decided, "error_log": err}))

    gaps = []
    try:
        gap_rows = conn.execute(
            "SELECT id, entity, acknowledged_at, acknowledged_reason FROM gap_inventory "
            "WHERE gap_type = 'unresolved_contradiction' AND researched = 0 AND acknowledged = 0"
        ).fetchall()
    except sqlite3.Error:
        gap_rows = []
    for gid, entity, ack_at, ack_reason in gap_rows:
        hit = label_family.get(entity)
        if hit and gid not in seen_gaps:
            fam, s, t = hit
            gaps.append((gid, s, t, fam, {"acknowledged": 0, "acknowledged_at": ack_at,
                                          "acknowledged_reason": ack_reason}))

    def _tally(items, fam_idx):
        out: Dict[str, int] = {}
        for it in items:
            out[it[fam_idx]] = out.get(it[fam_idx], 0) + 1
        return out

    pair_fams: Dict[Tuple[int, int], str] = {}
    for eid, s, t, fam, _sa, _sb in edges:
        pair_fams[(min(s, t), max(s, t))] = fam
    report: Dict[str, Any] = {
        "status": "ok",
        "applied": False,
        "pairs": _tally([(k, f) for k, f in pair_fams.items()], 1),
        "edges": _tally(edges, 3),
        "proposals": _tally(props, 3),
        "gaps": _tally(gaps, 3),
    }
    report["totals"] = {k: sum(report[k].values()) for k in ("pairs", "edges", "proposals", "gaps")}
    if not apply:
        return report

    ts = conn.execute("SELECT datetime('now')").fetchone()[0]
    ins = (f"INSERT INTO {QUARANTINE_TABLE} (object_kind, object_id, source_id, target_id, "
           f"family, reason, prior_state, applied_state, quarantined_at) VALUES (?,?,?,?,?,?,?,?,?)")
    touched = {"edges": 0, "proposals": 0, "gaps": 0}
    try:
        for eid, s, t, fam, _sa, _sb in edges:
            cur = conn.execute("UPDATE memory_edges SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
                               (ts, eid))
            if cur.rowcount:
                conn.execute(ins, ("edge", eid, s, t, fam, _reason_for(fam),
                                   json.dumps({"valid_until": None}), json.dumps({"valid_until": ts}), ts))
                touched["edges"] += 1
        for pid, s, t, fam, prior in props:
            cur = conn.execute(
                "UPDATE edge_proposals SET status = 'rejected', decided_at = ?, error_log = ? "
                "WHERE id = ? AND status = 'pending'", (ts, _reason_for(fam), pid))
            if cur.rowcount:
                conn.execute(ins, ("proposal", pid, s, t, fam, _reason_for(fam), json.dumps(prior),
                                   json.dumps({"status": "rejected", "decided_at": ts,
                                               "error_log": _reason_for(fam)}), ts))
                touched["proposals"] += 1
        for gid, s, t, fam, prior in gaps:
            cur = conn.execute(
                "UPDATE gap_inventory SET acknowledged = 1, acknowledged_at = ?, acknowledged_reason = ? "
                "WHERE id = ? AND acknowledged = 0", (ts, _reason_for(fam), gid))
            if cur.rowcount:
                conn.execute(ins, ("gap", gid, s, t, fam, _reason_for(fam), json.dumps(prior),
                                   json.dumps({"acknowledged": 1, "acknowledged_at": ts,
                                               "acknowledged_reason": _reason_for(fam)}), ts))
                touched["gaps"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    report.update({"applied": True, "quarantined_at": ts, "written": touched})
    return report


def release_quarantine(
    conn: sqlite3.Connection,
    *,
    quarantine_ids: Optional[Sequence[int]] = None,
    pair: Optional[Tuple[int, int]] = None,
    family: Optional[str] = None,
    release_all: bool = False,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Undo a quarantine: restore each object's pre-image, but ONLY where the
    object still reads exactly as the quarantine left it. A row whose object has
    changed since is reported as a conflict and stays open (nothing overwritten).
    Select by quarantine row ids, by a memory pair (either order; covers its
    edges, proposals and gaps), by family, or everything open."""
    if not quarantine_available(conn):
        return {"status": "unavailable", "message": "belief_quarantine is absent"}
    clauses, params = ["released_at IS NULL"], []
    if quarantine_ids:
        clauses.append(f"id IN ({','.join('?' * len(quarantine_ids))})")
        params += [int(i) for i in quarantine_ids]
    elif pair:
        a, b = int(pair[0]), int(pair[1])
        clauses.append("((source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?))")
        params += [a, b, b, a]
    elif family:
        clauses.append("family = ?")
        params.append(family)
    elif not release_all:
        return {"status": "error", "message": "name what to release: ids, a pair, a family, or all"}
    rows = conn.execute(
        f"SELECT id, object_kind, object_id, prior_state, applied_state FROM {QUARANTINE_TABLE} "
        f"WHERE {' AND '.join(clauses)} ORDER BY id", params).fetchall()

    released, conflicts = [], []
    try:
        for qid, kind, oid, prior_s, applied_s in rows:
            prior, applied = json.loads(prior_s), json.loads(applied_s)
            if kind == "edge":
                cur = conn.execute(
                    "UPDATE memory_edges SET valid_until = ? WHERE id = ? AND valid_until = ?",
                    (prior["valid_until"], oid, applied["valid_until"]))
            elif kind == "proposal":
                cur = conn.execute(
                    "UPDATE edge_proposals SET status = ?, decided_at = ?, error_log = ? "
                    "WHERE id = ? AND status = ? AND decided_at = ? AND error_log = ?",
                    (prior["status"], prior["decided_at"], prior["error_log"], oid,
                     applied["status"], applied["decided_at"], applied["error_log"]))
            else:
                cur = conn.execute(
                    "UPDATE gap_inventory SET acknowledged = ?, acknowledged_at = ?, acknowledged_reason = ? "
                    "WHERE id = ? AND acknowledged = 1 AND acknowledged_at = ? AND acknowledged_reason = ?",
                    (prior["acknowledged"], prior["acknowledged_at"], prior["acknowledged_reason"], oid,
                     applied["acknowledged_at"], applied["acknowledged_reason"]))
            if cur.rowcount:
                conn.execute(
                    f"UPDATE {QUARANTINE_TABLE} SET released_at = datetime('now'), release_note = ? WHERE id = ?",
                    (note, qid))
                released.append(qid)
            else:
                conflicts.append({"id": qid, "object_kind": kind, "object_id": oid,
                                  "reason": "the object changed after it was quarantined; left as it is"})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"status": "ok", "released": len(released), "released_ids": released, "conflicts": conflicts}


def quarantine_report(conn: sqlite3.Connection, *, limit: int = 20) -> Dict[str, Any]:
    """What is quarantined now, derived: open and released counts by kind and
    family, and the open PAIRS (each listed once, with both summaries) for review."""
    if not quarantine_available(conn):
        return {"status": "unavailable", "message": "belief_quarantine is absent"}
    counts: Dict[str, Dict[str, Dict[str, int]]] = {"open": {}, "released": {}}
    for state, kind, fam, n in conn.execute(
            f"SELECT CASE WHEN released_at IS NULL THEN 'open' ELSE 'released' END, "
            f"object_kind, family, COUNT(*) FROM {QUARANTINE_TABLE} GROUP BY 1, 2, 3"):
        counts[state].setdefault(kind, {})[fam] = n
    open_pairs = conn.execute(
        f"SELECT MIN(q.source_id, q.target_id) AS a, MAX(q.source_id, q.target_id) AS b, "
        f"q.family, GROUP_CONCAT(q.id), substr(ma.summary, 1, 100), substr(mb.summary, 1, 100) "
        f"FROM {QUARANTINE_TABLE} q "
        f"JOIN memories ma ON ma.id = MIN(q.source_id, q.target_id) "
        f"JOIN memories mb ON mb.id = MAX(q.source_id, q.target_id) "
        f"WHERE q.released_at IS NULL GROUP BY a, b ORDER BY q.family, a, b LIMIT ?", (limit,)
    ).fetchall()
    # ROUND 2 (l2): two pair counts, each named for what it counts. MEASURED on a copy: the apply's totals.pairs read
    # 3,527 and this report's single "open_pairs" read 3,701 in the same output -- the second also counted the 174
    # pairs held only by a pending proposal. open_edge_pairs is what totals.pairs counts; open_pairs_all_kinds is
    # the review list's population (edges, proposals and gaps); the difference is named too.
    def _n_pairs(where: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT MIN(source_id, target_id), MAX(source_id, target_id) "
            f"FROM {QUARANTINE_TABLE} WHERE released_at IS NULL{where})").fetchone()[0]
    n_all, n_edge = _n_pairs(""), _n_pairs(" AND object_kind = 'edge'")
    return {
        "status": "ok",
        "open_edge_pairs": n_edge,
        "open_pairs_all_kinds": n_all,
        "open_pairs_without_an_edge": n_all - n_edge,
        "open_pairs_note": ("open_edge_pairs counts pairs with a quarantined edge (what an apply's totals.pairs "
                            "counts); open_pairs_all_kinds adds the pairs held only by a pending proposal or a gap"),
        "counts": counts,
        "pairs": [{"memory_ids": [a, b], "family": fam,
                   "quarantine_ids": [int(x) for x in ids.split(",")],
                   "summaries": [sa, sb]} for a, b, fam, ids, sa, sb in open_pairs],
        "release_with": "belief_cli.py quarantine --release-pair A B | --release-family F | --release-ids ...",
    }

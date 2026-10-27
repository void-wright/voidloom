"""Recall-diversity organ (L5-B): MMR de-duplication + adaptive cap +
Lost-in-the-Middle placement, PLUS the build-now-dormant lane-pluggable
retrieval architecture.

THE ACTIVE CORE (ships today, default-ON, kill-switchable)
----------------------------------------------------------
``apply_diversity`` is the FIFTH and LAST re-rank organ in the Loop A
consumption chain, run AFTER cross-tier -> lens -> PPR -> foundation-boost and
immediately BEFORE the post-fetch filter/cut. Its job is to stop the brain from
handing back five near-identical memories and stop burying the single best
memory in the middle of the context (where LLMs read worst). It does three
things in one pass:

  1. **MMR (Maximal Marginal Relevance)** over the active ``memory_embeddings_v2``
     vectors. Iteratively select the result maximizing
     ``lambda * relevance - (1 - lambda) * max_cosine_to_already_selected``.
     Near-duplicate memories (cosine ~1.0 to something already chosen) are
     pushed down and fall off the cap -> de-dup.
  2. **Adaptive cap** modulated by the FTS5 ``bm25(memories_fts)`` score of the
     query. A *strong exact-token* hit -> a tighter cap (the user asked a sharp
     question, give them the few sharpest answers). A *diffuse* all-dense match
     -> a wider cap (explore more). The cap ALWAYS floors at ``top_k`` (never
     return fewer than asked when the pool can supply them).
  3. **Lost-in-the-Middle placement**: after MMR + cap, reorder so the
     highest-relevance items sit at positions ``0`` and ``-1`` (start + end),
     with the weakest items pooled in the middle -- the inverse-U the
     context-rot literature shows LLMs read best.

It also doubles as the **circuit-breaker** on the PPR -> access_count ->
foundation runaway: by collapsing near-duplicate hubs in THIS pass (which runs
BEFORE ``cmd_recall``'s access-stat bump loop), a hub and its near-clones no
longer all get their access_count incremented on every recall.

Contract (mirrors the sibling organs exactly):
  * Default-ON, kill-switch ``--no-diversity`` / ``VOIDLOOM_NO_DIVERSITY`` (the
    CALLER gates the organ; ``apply_diversity`` itself is a pure function).
  * Fail-graceful: ANY error returns the INPUT results UNCHANGED (no raise), so
    recall never breaks and the base pipeline stays byte-identical.
  * Stamps provenance: ``rerank_info["diversity"]`` (capped_from/capped_to/
    mmr_dropped/placement) + a per-result ``diversity_rank``.

THE BUILD-NOW-DORMANT LANE ARCHITECTURE (Drew's scale-by-construction call)
--------------------------------------------------------------------------
The genuine "don't recode later" need is the *lane-pluggable fusion
architecture*, NOT a sparse index over ~5.9K rows (dead VRAM today). So this
module ALSO defines, fully tested but inert at the current corpus size:

  * ``RetrievalLane`` -- a typed Protocol: ``.name`` + ``.retrieve(conn, query,
    query_vec, k) -> [(id, score)]``. The contract every retrieval leg honors.
  * ``VectorLane`` + ``BM25Lane`` -- a FORMALIZATION (not an invention) of the
    two existing legs in ``hybrid_retrieval`` (``_vector_search`` and
    ``fts_match``). They DELEGATE to those functions, so their behavior is
    byte-identical to the legacy path (proven in the test-suite).
  * ``LANES`` -- the registry. Vector + BM25 register at import. Future sparse
    (SPLADE) and ColBERT lanes register *disabled* (``enabled=False``) and only
    light up when ``gate_should_wake_lane`` says READY.
  * ``gate_should_wake_lane(conn) -> dict`` -- a PURE READ-ONLY function that
    measures (corpus size + the FTS5-miss-rate from ``lexical_miss_log``) and
    reports which dormant lanes are READY. **AUTO-DETECT, NEVER AUTO-PULL**: it
    only *whispers* READY; it never downloads a model, builds an index, or
    blocks a recall.
  * ``record_lexical_miss(conn, query)`` -- the write-path that feeds the gate's
    miss-rate signal (called on a recall whose FTS5 leg returned nothing).
  * ``wake_lane(name)`` -- the consent-gated activation STUB. It LOGS
    "would download X" and returns the plan; it does NOT download. A human (or
    a future consent flow) is the only thing that flips a dormant lane on.

stdlib + numpy + sqlite3 only. NO new pip dependency. NO model download.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

import numpy as np

logger = logging.getLogger(__name__)

# ==========================================================================
# Module-level configuration (surfaced so a Reflector/Curator can tune it).
# ==========================================================================

# MMR relevance/diversity trade-off. score = lambda * relevance
# - (1 - lambda) * max_cosine_to_selected. 0.7 leans RELEVANCE (we are a recall
# system, not a search-results page); a near-duplicate must be *very* close to
# already-selected mass to be pushed below a genuinely-different lower-relevance
# memory. Goldilocks band per the council: 0.5 (too diversity-aggressive, drops
# legitimately-similar-but-distinct memories) .. 0.85 (barely de-dups). 0.7 is
# the relevance-leaning sweet spot, matching PPR_LAMBDA-style conservatism.
DIVERSITY_LAMBDA: float = 0.7

# Near-duplicate threshold for the *reported* mmr_dropped count and for the
# circuit-breaker's "is this a near-dup hub" decision. A result whose max cosine
# to an already-selected item exceeds this is considered a near-duplicate. MMR
# itself does not hard-threshold (it is a soft penalty); this is only for
# provenance + the hub-suppression count. 0.92 on unit-normalized jina vectors
# corresponds to "almost the same memory" (paraphrase / re-store / near-clone).
MMR_NEAR_DUP_COSINE: float = 0.92

# Adaptive cap bounds, as multiples of top_k. The BM25 score of the strongest
# exact hit slides the cap between these:
#   strong exact hit  -> MMR_CAP_MIN_MULT * top_k  (tight; few sharp answers)
#   diffuse/no hit     -> MMR_CAP_MAX_MULT * top_k  (wide; explore)
# The cap is then FLOORED at top_k and CEILED at the available pool size.
MMR_CAP_MIN_MULT: float = 1.0   # floor is always top_k regardless
MMR_CAP_MAX_MULT: float = 2.0   # at most 2x top_k surfaced for a diffuse query

# BM25 score (FTS5 bm25(), lower-magnitude == stronger) interpolation anchors.
# FTS5 bm25() returns NEGATIVE values where a more-negative score == a stronger
# match. We work with the *positive magnitude* m = -bm25_score (so larger m ==
# stronger exact hit). m at/above MMR_BM25_STRONG -> tightest cap; m at/below
# MMR_BM25_WEAK -> widest cap; linear in between. These anchors are corpus-scale
# dependent and are the prime Reflector-tuning knob (see generation_report.md).
MMR_BM25_STRONG: float = 8.0    # |bm25| >= 8 == a strong exact-token hit
MMR_BM25_WEAK: float = 2.0      # |bm25| <= 2 == diffuse / weak lexical signal

# Active vec table resolver default (mirrors hybrid_retrieval._active_embedding_table).
_DEFAULT_EMB_TABLE = "memory_embeddings"
_V2_EMB_TABLE = "memory_embeddings_v2"


# ==========================================================================
# Embedding access (for the MMR cosine).
# ==========================================================================

def _active_embedding_table(conn: sqlite3.Connection) -> str:
    """Resolve the active vec table. Mirrors hybrid_retrieval's resolver so the
    MMR cosine reads the SAME tier the store side wrote to. Fail-safe: any
    error -> the Nomic default (so a missing metadata table never raises)."""
    # ROUND GAMMA (A7). This module was named the round's HEALTHY NEGATIVE CONTROL
    # and it had the disease: the docstring's "any error -> the Nomic default" is a
    # default onto a table nothing has written since 2026-05-28. A control that
    # carries the defect it controls for cannot falsify anything.
    #
    # MMR is a diversity re-rank on a hot path; refusing outright would drop
    # diversity entirely. So: resolve through the one resolver, and where it
    # refuses, say so at ERROR and fall back to the LIVE table, never the dormant.
    import sys as _s, os as _o
    _d = _o.path.dirname(_o.path.abspath(__file__))
    if _d not in _s.path:
        _s.path.insert(0, _d)
    try:
        import embed_resolver
        return embed_resolver.resolve(conn)[0]
    except Exception as e:
        logger.error(
            "recall_diversity: embedding tier UNRESOLVED (%s: %s); MMR cosine "
            "falls back to %r.", type(e).__name__, e, _V2_EMB_TABLE,
        )
        return _V2_EMB_TABLE


def _load_embeddings(
    conn: sqlite3.Connection,
    ids: Sequence[int],
) -> Dict[int, np.ndarray]:
    """Fetch L2-normalized embedding vectors for ``ids`` from the active table.

    vec0 tables store float32; the bytes are read directly via the PRIMARY KEY
    (memory_id) -- NO ``embedding MATCH``/``k=`` KNN syntax, which would require
    a query vector. We read raw rows by id, ``np.frombuffer`` them as float32,
    and L2-normalize so a plain dot product equals cosine similarity.

    Fail-graceful: any id whose vector can't be read is simply absent from the
    returned dict (the MMR caller treats a missing vector as "no diversity
    signal" -> that result keeps its relevance rank, never crashes).
    """
    if not ids:
        return {}
    emb_table = _active_embedding_table(conn)
    placeholders = ",".join("?" * len(ids))
    out: Dict[int, np.ndarray] = {}
    try:
        rows = conn.execute(
            f"SELECT memory_id, embedding FROM {emb_table} "
            f"WHERE memory_id IN ({placeholders})",
            list(ids),
        ).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("diversity: embedding fetch failed: %s", e)
        return {}
    for mid, blob in rows:
        if blob is None:
            continue
        try:
            vec = np.frombuffer(blob, dtype=np.float32).astype(np.float64)
        except (ValueError, TypeError):
            continue
        if vec.size == 0:
            continue
        norm = np.linalg.norm(vec)
        if norm > 0.0:
            vec = vec / norm
        out[int(mid)] = vec
    return out


# ==========================================================================
# The adaptive cap.
# ==========================================================================

def compute_adaptive_cap(
    top_k: int,
    pool_size: int,
    bm25_score: Optional[float],
) -> int:
    """Compute the diversity cap from the query's strongest BM25 signal.

    A strong exact-token hit (large |bm25|) -> a TIGHT cap (give the few sharpest
    answers). A diffuse/weak lexical signal (small |bm25| or None) -> a WIDE cap
    (let MMR explore more of the pool). The cap is always:

        * FLOORED at ``top_k``           (never return fewer than asked), and
        * CEILED at ``pool_size``        (can't surface more than we have).

    ``bm25_score`` is the *strongest* (most negative) FTS5 ``bm25()`` value among
    the recall's BM25 candidates, OR None when the BM25 leg was empty/disabled.
    None is treated as "no exact signal" -> the widest cap.

    Returns an int cap in ``[min(top_k, pool_size), pool_size]`` -- i.e. the cap
    floors at ``top_k`` ONLY when the pool can actually supply ``top_k`` results,
    and otherwise floors at (and equals) the whole pool.
    """
    pool = max(0, int(pool_size))
    if pool == 0:
        return 0
    # Floor is top_k, but can never exceed what the pool can supply.
    floor = min(max(1, int(top_k)), pool)
    ceil = pool

    # No lexical signal -> widest cap (then clamped to the pool).
    if bm25_score is None:
        return min(ceil, max(floor, int(round(MMR_CAP_MAX_MULT * floor))))

    # FTS5 bm25() is negative; magnitude is the strength. Some callers may pass a
    # pre-negated (positive == better) score; use abs() so either convention
    # yields the same "stronger == larger" magnitude.
    m = abs(float(bm25_score))

    # Linear interpolation: strong -> MIN mult, weak -> MAX mult.
    if m >= MMR_BM25_STRONG:
        mult = MMR_CAP_MIN_MULT
    elif m <= MMR_BM25_WEAK:
        mult = MMR_CAP_MAX_MULT
    else:
        # frac in (0, 1): 0 at WEAK, 1 at STRONG.
        frac = (m - MMR_BM25_WEAK) / (MMR_BM25_STRONG - MMR_BM25_WEAK)
        mult = MMR_CAP_MAX_MULT + frac * (MMR_CAP_MIN_MULT - MMR_CAP_MAX_MULT)

    cap = int(round(mult * floor))
    cap = max(floor, cap)     # floor at top_k
    cap = min(ceil, cap)      # ceil at pool size
    return cap


# ==========================================================================
# MMR selection.
# ==========================================================================

def mmr_select(
    relevances: Sequence[float],
    vectors: Sequence[Optional[np.ndarray]],
    cap: int,
    lambda_: float = DIVERSITY_LAMBDA,
) -> Tuple[List[int], int]:
    """Maximal-Marginal-Relevance greedy selection.

    Args:
        relevances: per-candidate relevance scores (higher == more relevant),
            already in the order the upstream organs ranked them (index 0 ==
            most relevant). Used directly as the MMR ``relevance`` term, after a
            min-max normalization to [0, 1] so it shares a scale with cosine.
        vectors: per-candidate L2-normalized embedding (or None if unavailable).
            A None vector contributes ZERO diversity penalty (it can neither be a
            duplicate of, nor make duplicates of, anything) -- it competes purely
            on relevance.
        cap: how many indices to select (the adaptive cap, already floored at
            top_k and ceiled at the pool).
        lambda_: relevance/diversity trade-off (see ``DIVERSITY_LAMBDA``).

    Returns:
        ``(selected_indices, mmr_dropped)`` where ``selected_indices`` is the
        chosen candidate indices in MMR selection order (best-first), and
        ``mmr_dropped`` is the count of NOT-selected candidates that were
        near-duplicates (cosine > ``MMR_NEAR_DUP_COSINE`` to a selected item) --
        i.e. the de-dup the cap actually performed.

    Deterministic: ties broken by original index (stable). Pure numpy + Python.
    """
    n = len(relevances)
    if n == 0:
        return [], 0
    cap = max(0, min(int(cap), n))
    if cap == 0:
        return [], 0

    rel = np.asarray(relevances, dtype=np.float64)
    # Normalize relevance to [0, 1] so it shares scale with cosine in [−1, 1].
    rmin, rmax = float(rel.min()), float(rel.max())
    if rmax > rmin:
        rel_n = (rel - rmin) / (rmax - rmin)
    else:
        # All-equal relevance -> rank by original order via a tiny epsilon ramp
        # so MMR still has a deterministic relevance signal (index 0 best).
        rel_n = np.linspace(1.0, 0.0, num=n)

    # Pre-stack vectors into a matrix where rows with a vector are real and
    # rows without are flagged; missing rows get a zero diversity contribution.
    has_vec = np.array([v is not None for v in vectors], dtype=bool)
    dim = 0
    for v in vectors:
        if v is not None:
            dim = v.shape[0]
            break
    if dim > 0:
        mat = np.zeros((n, dim), dtype=np.float64)
        for i, v in enumerate(vectors):
            if v is not None:
                mat[i] = v
    else:
        mat = None  # no vectors at all -> MMR degenerates to relevance order

    selected: List[int] = []
    remaining = list(range(n))
    # max cosine of each remaining candidate to the selected set (running max).
    max_cos = np.full(n, -1.0, dtype=np.float64)

    while remaining and len(selected) < cap:
        best_idx = None
        best_score = None
        for i in remaining:
            has_div = mat is not None and has_vec[i]
            div_penalty = max_cos[i] if has_div else -1.0
            # When nothing is selected yet, div_penalty stays −1 -> first pick is
            # pure relevance (the most-relevant candidate), as MMR specifies.
            mmr = lambda_ * rel_n[i] - (1.0 - lambda_) * max(div_penalty, 0.0)
            # HARD near-duplicate guard (the de-dup spine): a candidate that is a
            # TRUE near-clone (cosine > MMR_NEAR_DUP_COSINE) of something already
            # selected is collapsed regardless of lambda. Keeping two near-
            # identical memories is never useful, and a relevance-leaning lambda
            # (0.7) alone wouldn't drop a high-relevance clone via the soft
            # penalty. We subtract a large constant so any genuinely-distinct
            # candidate (even a much-less-relevant one) is preferred over a clone,
            # while clones still order among themselves by relevance (so if ALL
            # remaining are clones we still pick the most relevant). The soft MMR
            # term continues to handle the GRADED diversity below the threshold.
            if has_div and div_penalty > MMR_NEAR_DUP_COSINE:
                mmr -= 1.0
            if (
                best_score is None
                or mmr > best_score + 1e-12
                or (abs(mmr - best_score) <= 1e-12 and i < best_idx)
            ):
                best_score = mmr
                best_idx = i
        selected.append(best_idx)
        remaining.remove(best_idx)
        # Update running max-cosine for the remaining candidates against the
        # just-selected vector.
        if mat is not None and has_vec[best_idx] and remaining:
            sel_vec = mat[best_idx]
            rem_idx = np.array(remaining, dtype=np.int64)
            cos = mat[rem_idx] @ sel_vec  # unit vectors -> dot == cosine
            # Only real-vector rows get a meaningful cosine.
            real = has_vec[rem_idx]
            cur = max_cos[rem_idx]
            updated = np.where(real, np.maximum(cur, cos), cur)
            max_cos[rem_idx] = updated

    # Count near-duplicates the cap dropped: a NOT-selected candidate whose max
    # cosine to the selected set exceeds the near-dup threshold.
    mmr_dropped = 0
    if mat is not None:
        for i in remaining:
            if has_vec[i] and max_cos[i] > MMR_NEAR_DUP_COSINE:
                mmr_dropped += 1
    return selected, mmr_dropped


# ==========================================================================
# Lost-in-the-Middle placement.
# ==========================================================================

def litm_reorder(
    items: List[Any], rank_key: Callable[[Any], float]
) -> List[Any]:
    """Reorder ``items`` into the Lost-in-the-Middle inverse-U.

    LLMs attend best to the START and END of a long context and worst to the
    MIDDLE. So the strongest items belong at positions ``0`` and ``-1`` and the
    weakest in the middle. Sort by ``rank_key`` descending (strongest first),
    then fold via two pointers (see ``_litm_fold``):

        rank order:  [r0, r1, r2, r3, r4]   (r0 strongest)
        placement:   r0->0, r1->-1, r2->1, r3->-2, r4->2 (the leftover middle)
        result:      [r0, r2, r4, r3, r1]

    -> r0 at position 0 (strongest start), r1 at position -1 (2nd strongest end),
    weakest (r4) lands in the middle. For <=2 items the strongest-first order is
    returned unchanged. Pure, deterministic, order-stable on equal keys.
    """
    if len(items) <= 2:
        return list(items)
    ordered = sorted(items, key=rank_key, reverse=True)
    return _litm_fold(ordered)


def place_final(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lost-in-the-Middle placement of an already-CUT, strongest-first list; re-stamps ``diversity_rank`` (the
    final 0-based position, which recall_explain reads)."""
    placed = _litm_fold(list(results))
    for pos, r in enumerate(placed):
        if isinstance(r, dict):
            r["diversity_rank"] = pos
    return placed


def _litm_fold(ordered: List[Any]) -> List[Any]:
    """Two-pointer LitM fold: strongest at 0 and -1, weakest in the middle.

    ``ordered`` is strongest-first. Returns a list where index 0 = strongest,
    index -1 = 2nd strongest, and the weakest items collapse to the centre.
    """
    n = len(ordered)
    if n <= 2:
        return list(ordered)
    result: List[Optional[Any]] = [None] * n
    left, right = 0, n - 1
    for i, it in enumerate(ordered):
        if i % 2 == 0:
            result[left] = it
            left += 1
        else:
            result[right] = it
            right -= 1
    return [x for x in result if x is not None]


# ==========================================================================
# The organ entry point.
# ==========================================================================

def apply_diversity(
    conn: sqlite3.Connection,
    results: List[Dict[str, Any]],
    query: str,
    *,
    top_k: int,
    lambda_: float = DIVERSITY_LAMBDA,
    bm25_score: Optional[float] = None,
    place: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Apply MMR de-dup + adaptive cap + Lost-in-the-Middle placement.

    ``place=False`` returns the kept results STRONGEST-FIRST and leaves placement to the caller, which must cut to
    its final size FIRST and then call ``place_final``. Folding before a cut keeps the odd ranks and drops the even
    ones (the second-best result included) -- measured in cmd_recall on 2026-10-27, which is why it now defers.

    This is the LAST touch on ``results`` before ``cmd_recall``'s post-fetch
    filter/cut. The result list comes in ranked by the upstream organs
    (cross-tier -> lens -> PPR -> foundation-boost); ``results[0]`` is the most
    relevant. We:

      1. Compute the adaptive cap from ``bm25_score`` (floored at ``top_k``,
         ceiled at the pool size).
      2. Run MMR to select ``cap`` results, dropping near-duplicates.
      3. Reorder the selected set into the LitM inverse-U (best at the ends).

    Args:
        conn: sqlite3 connection (sqlite-vec loaded; needed only to read the
            embedding vectors for the MMR cosine).
        results: ranked recall results (each a dict carrying ``id`` + ``score``/
            ``distance`` + the L5 stamps). Order is the upstream ranking.
        query: the raw query string (carried for provenance; the MMR cosine uses
            stored embeddings, not a fresh encode).
        top_k: how many results the user ultimately asked for. The cap floors
            here; LitM placement applies to whatever the cap kept.
        lambda_: MMR relevance/diversity trade-off.
        bm25_score: the strongest FTS5 ``bm25()`` magnitude among the recall's
            BM25 candidates (or None). Modulates the cap.

    Returns:
        ``(results, info)``. On success ``results`` is the de-duped, capped,
        LitM-placed list with ``diversity_rank`` stamped on each kept dict, and
        ``info`` = ``{"applied": True, "capped_from", "capped_to", "mmr_dropped",
        "placement": "litm", "cap", "lambda", "bm25_score"}``. On ANY error or
        a degenerate input, ``results`` is returned UNCHANGED and ``info``
        carries ``applied: False`` (+ ``error`` / ``note``) -- a faithful no-op
        so the kill-switched path is byte-identical to the unpatched pipeline.
    """
    info: Dict[str, Any] = {"applied": False}

    # Cheap exits that keep results byte-identical.
    if not results:
        return results, info
    pool_size = len(results)
    if pool_size <= 1:
        info["note"] = "pool<=1 (no-op)"
        return results, info

    try:
        # 1. Adaptive cap.
        cap = compute_adaptive_cap(top_k, pool_size, bm25_score)
        if cap >= pool_size:
            # Cap doesn't drop anything; MMR can still REORDER but never removes
            # a result -> but for a true no-op-on-removal we still run MMR for
            # ordering AND LitM placement (placement IS the point even with no
            # drops). So we proceed; mmr_dropped will simply be 0.
            cap = pool_size

        # 2. Gather relevance + vectors aligned to the result order.
        ids = [r.get("id") for r in results]
        emb = _load_embeddings(conn, [i for i in ids if i is not None])
        relevances: List[float] = []
        vectors: List[Optional[np.ndarray]] = []
        for r in results:
            sc = r.get("score", r.get("relevance_score"))
            if sc is None:
                # Fall back to negated distance (lower distance == better).
                dist = r.get("distance")
                sc = (-float(dist)) if dist is not None else 0.0
            try:
                sc = float(sc)
            except (TypeError, ValueError):
                sc = 0.0
            relevances.append(sc)
            vectors.append(emb.get(r.get("id")))

        # 3. MMR selection.
        selected_idx, mmr_dropped = mmr_select(
            relevances, vectors, cap, lambda_=lambda_
        )
        if not selected_idx:
            info["note"] = "mmr selected nothing (no-op)"
            return results, info

        selected = [results[i] for i in selected_idx]

        # 4. Lost-in-the-Middle placement. Rank by the upstream relevance score
        #    (NOT the MMR score) so the genuinely-most-relevant item lands at the
        #    ends. We keep a stable per-item relevance captured pre-fold.
        rel_by_id = {
            id(r): relevances[selected_idx[k]] for k, r in enumerate(selected)
        }
        ordered = sorted(selected, key=lambda r: rel_by_id[id(r)], reverse=True)
        placed = _litm_fold(ordered) if place else ordered

        # 5. Stamp provenance: diversity_rank == final position (0-based).
        for pos, r in enumerate(placed):
            r["diversity_rank"] = pos

        info.update({
            "applied": True,
            "capped_from": pool_size,
            "capped_to": len(placed),
            "mmr_dropped": mmr_dropped,
            "placement": "litm" if place else "deferred",
            "cap": cap,
            "lambda": lambda_,
            "bm25_score": bm25_score,
        })
        return placed, info

    except Exception as e:  # noqa: BLE001 -- never break recall
        info["error"] = str(e)
        return results, info


# ==========================================================================
# Availability guard (for the caller's symmetry with the sibling organs).
# ==========================================================================

def is_diversity_available(conn: sqlite3.Connection) -> bool:
    """True iff the active embedding table exists (MMR needs vectors).

    Fail-safe: any error -> False (caller skips diversity, base recall stands).
    The organ degrades gracefully even WITHOUT vectors (MMR falls back to a
    relevance-order selection + LitM placement still applies), so this guard is
    intentionally permissive: it returns True whenever the embedding table is
    present, and ``apply_diversity`` itself handles a missing-vector pool.
    """
    try:
        emb_table = _active_embedding_table(conn)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') "
            "AND name=?",
            (emb_table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


# ==========================================================================
# THE BUILD-NOW-DORMANT LANE ARCHITECTURE
# ==========================================================================

@runtime_checkable
class RetrievalLane(Protocol):
    """The contract every retrieval leg honors.

    A lane is a single retrieval strategy (dense vector, BM25 keyword, sparse
    SPLADE, ColBERT late-interaction, ...). The fusion layer (``rrf_fuse``)
    already accepts N ranked lists, so adding a lane is purely additive: register
    it in ``LANES``, and -- once awake -- its ``retrieve`` output joins the
    others in the RRF fuse. No fusion-code change is ever needed again.
    """

    name: str
    enabled: bool

    def retrieve(
        self,
        conn: sqlite3.Connection,
        query: str,
        query_vec: Any,
        k: int,
    ) -> List[Tuple[int, float]]:
        """Return ``[(memory_id, score)]`` best-first (higher score == better)."""
        ...


class _BaseLane:
    """Shared lane bookkeeping. Subclasses implement ``retrieve``."""

    name: str = "base"
    enabled: bool = False
    # Human-readable description of what waking this lane would require (model,
    # index build, VRAM). Surfaced by wake_lane's consent log. NO download here.
    wake_cost: str = ""

    def retrieve(
        self,
        conn: sqlite3.Connection,
        query: str,
        query_vec: Any,
        k: int,
    ) -> List[Tuple[int, float]]:
        raise NotImplementedError


class VectorLane(_BaseLane):
    """FORMALIZATION of the existing dense-vector leg.

    Delegates verbatim to ``hybrid_retrieval._vector_search`` so its output is
    byte-identical to the legacy vector leg (proven in the test-suite). It does
    NOT re-implement the cosine search; it is a typed wrapper that lets the
    fusion layer treat dense-vector as one lane among many.
    """

    name = "vector"
    enabled = True
    wake_cost = "none (already active; reads memory_embeddings_v2)"

    def __init__(self, vector_search_fn: Optional[Callable[..., List[Tuple[int, float]]]] = None):
        # Late-bind hybrid_retrieval to avoid a hard import dependency (and to
        # let tests inject a fake). If neither is available, retrieve() yields [].
        self._fn = vector_search_fn

    def _resolve_fn(self) -> Optional[Callable[..., List[Tuple[int, float]]]]:
        if self._fn is not None:
            return self._fn
        try:
            import hybrid_retrieval  # type: ignore
            return hybrid_retrieval._vector_search
        except Exception:
            try:
                from . import hybrid_retrieval  # type: ignore
                return hybrid_retrieval._vector_search
            except Exception:
                return None

    def retrieve(self, conn, query, query_vec, k):
        fn = self._resolve_fn()
        if fn is None:
            return []
        try:
            return fn(conn, query_vec, k)
        except Exception as e:  # noqa: BLE001
            logger.warning("VectorLane.retrieve failed: %s", e)
            return []


class BM25Lane(_BaseLane):
    """FORMALIZATION of the existing FTS5/BM25 keyword leg.

    Delegates verbatim to ``hybrid_retrieval.fts_match`` so its output is
    byte-identical to the legacy BM25 leg (proven in the test-suite).
    """

    name = "bm25"
    enabled = True
    wake_cost = "none (already active; reads memories_fts)"

    def __init__(self, fts_match_fn: Optional[Callable[..., List[Tuple[int, float]]]] = None):
        self._fn = fts_match_fn

    def _resolve_fn(self) -> Optional[Callable[..., List[Tuple[int, float]]]]:
        if self._fn is not None:
            return self._fn
        try:
            import hybrid_retrieval  # type: ignore
            return hybrid_retrieval.fts_match
        except Exception:
            try:
                from . import hybrid_retrieval  # type: ignore
                return hybrid_retrieval.fts_match
            except Exception:
                return None

    def retrieve(self, conn, query, query_vec, k):
        fn = self._resolve_fn()
        if fn is None:
            return []
        try:
            return fn(conn, query, top_k=k)
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25Lane.retrieve failed: %s", e)
            return []


class _DormantModelLane(_BaseLane):
    """Base for a model-backed lane that is BUILT but inert.

    A dormant lane carries everything EXCEPT the model + index: its name, its
    wake cost, the gate signal it watches, and a ``retrieve`` that returns [] (it
    cannot retrieve without an index). It NEVER imports a model package, NEVER
    downloads, NEVER touches VRAM. ``wake_lane`` is the only path that would
    materialize it -- and that path only LOGS the plan, it does not execute it.
    """

    enabled = False
    # The corpus size at/above which this lane becomes worth its standing cost.
    wake_corpus_floor: int = 25_000
    # The FTS5-miss-rate at/above which a sparse/late-interaction lane earns its
    # keep (a high miss-rate == many queries the lexical leg can't serve == the
    # exact gap sparse/ColBERT fill).
    wake_miss_rate_floor: float = 0.20

    def retrieve(self, conn, query, query_vec, k):
        # Dormant: no index, nothing to retrieve. Returning [] keeps the fusion
        # layer correct (an empty leg simply contributes no candidates) and
        # guarantees a dormant lane NEVER blocks or downloads on a real recall.
        return []


class SparseLane(_DormantModelLane):
    """DORMANT SPLADE sparse-lexical lane (scaffold only; never pulled).

    Would index every memory's SPLADE term-expansion into ``sparse_terms``
    (migration_025, write-but-don't-apply) and serve exact+expanded keyword
    matches the dense leg misses. Inert until ``gate_should_wake_lane`` says
    READY and a human runs ``wake_lane('sparse')``.
    """

    name = "sparse"
    wake_cost = (
        "download SPLADE model (~500MB), build sparse_terms index over the "
        "corpus (migration_025_sparse_lane.sql.DEFERRED), ~1-2GB VRAM at query"
    )


class ColBERTLane(_DormantModelLane):
    """DORMANT ColBERT late-interaction lane (scaffold only; never pulled).

    Would store per-token ColBERT embeddings (migration_026, write-but-don't-
    apply) and serve fine-grained late-interaction matches. The heaviest lane;
    earns its keep only at large corpus + high lexical-miss. Inert until READY +
    explicit ``wake_lane('colbert')``.
    """

    name = "colbert"
    wake_cost = (
        "download ColBERT model (~440MB), build colbert_token_embeddings index "
        "(migration_026_colbert_lane.sql.DEFERRED), ~2-3GB VRAM at query"
    )


# The registry. Vector + BM25 are ACTIVE formalizations of the existing legs;
# Sparse + ColBERT are DORMANT scaffolds. A future lane is one append + one
# .DEFERRED migration -- the fusion code never changes.
LANES: "Dict[str, RetrievalLane]" = {}


def register_lane(lane: RetrievalLane) -> None:
    """Register (or replace) a lane by name. Idempotent."""
    LANES[lane.name] = lane


def active_lanes() -> List[RetrievalLane]:
    """All lanes currently enabled (the ones the fusion layer should query)."""
    return [ln for ln in LANES.values() if getattr(ln, "enabled", False)]


def dormant_lanes() -> List[RetrievalLane]:
    """All registered-but-inert lanes (the scale-by-construction scaffolds)."""
    return [ln for ln in LANES.values() if not getattr(ln, "enabled", False)]


# Register the four lanes at import. Vector + BM25 enabled; Sparse + ColBERT
# dormant. Registration is side-effect-free (no model, no index, no download).
register_lane(VectorLane())
register_lane(BM25Lane())
register_lane(SparseLane())
register_lane(ColBERTLane())


# ==========================================================================
# The gate: AUTO-DETECT, NEVER AUTO-PULL.
# ==========================================================================

def _corpus_count(conn: sqlite3.Connection) -> int:
    """Live memory count (non-archived). Fail-safe: error -> 0."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        try:
            row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0


def _lexical_miss_rate(conn: sqlite3.Connection, window: int = 500) -> Tuple[float, int]:
    """Measure the FTS5-miss-rate from ``lexical_miss_log`` over the last
    ``window`` recorded recalls.

    A 'miss' is a recorded recall whose BM25/FTS5 leg returned zero hits
    (``fts_hits == 0``). A high miss-rate means many queries the lexical leg
    can't serve -- exactly the gap a sparse/late-interaction lane fills.

    Returns ``(miss_rate, sample_size)``. Fail-safe: missing table / error ->
    ``(0.0, 0)`` (no signal -> the gate reports NOT-ready, the safe default).
    """
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='lexical_miss_log'"
        ).fetchone()
        if not row:
            return 0.0, 0
        rows = conn.execute(
            "SELECT fts_hits FROM lexical_miss_log ORDER BY id DESC LIMIT ?",
            (window,),
        ).fetchall()
        if not rows:
            return 0.0, 0
        total = len(rows)
        misses = sum(1 for (h,) in rows if (h or 0) == 0)
        return (misses / total if total else 0.0), total
    except Exception:
        return 0.0, 0


def gate_should_wake_lane(conn: sqlite3.Connection) -> Dict[str, Any]:
    """PURE READ-ONLY gate: which dormant lanes are READY to wake?

    Measures two signals -- live corpus size and the lexical-miss-rate from
    ``lexical_miss_log`` -- and, for each dormant model-backed lane, reports
    whether BOTH thresholds are met. A lane is READY iff:

        corpus_count >= lane.wake_corpus_floor   AND
        miss_rate    >= lane.wake_miss_rate_floor (with a non-trivial sample)

    **AUTO-DETECT, NEVER AUTO-PULL**: this function ONLY reports. It never
    imports a model, never downloads, never builds an index, never blocks. The
    returned dict is meant to feed a *whisper* ("the sparse lane is READY to
    wake -- run `voidloom wake-lane sparse` when you're ready"), never an
    automatic activation.

    Returns:
        ``{"corpus_count": int, "miss_rate": float, "miss_sample": int,
           "ready": [lane_name, ...], "not_ready": {lane_name: reason, ...},
           "checked": [lane_name, ...]}``.
    """
    corpus = _corpus_count(conn)
    miss_rate, sample = _lexical_miss_rate(conn)

    ready: List[str] = []
    not_ready: Dict[str, str] = {}
    checked: List[str] = []
    # Require a minimum sample so a brand-new log can't trip the gate on noise.
    MIN_SAMPLE = 50

    for lane in dormant_lanes():
        floor_n = getattr(lane, "wake_corpus_floor", None)
        floor_m = getattr(lane, "wake_miss_rate_floor", None)
        if floor_n is None or floor_m is None:
            # A non-model dormant lane with no thresholds -> never auto-ready.
            not_ready[lane.name] = "no wake thresholds (manual-only)"
            continue
        checked.append(lane.name)
        reasons: List[str] = []
        if corpus < floor_n:
            reasons.append(f"corpus {corpus} < floor {floor_n}")
        if sample < MIN_SAMPLE:
            reasons.append(f"miss-sample {sample} < {MIN_SAMPLE} (insufficient signal)")
        elif miss_rate < floor_m:
            reasons.append(f"miss_rate {miss_rate:.3f} < floor {floor_m:.3f}")
        if reasons:
            not_ready[lane.name] = "; ".join(reasons)
        else:
            ready.append(lane.name)

    return {
        "corpus_count": corpus,
        "miss_rate": round(miss_rate, 4),
        "miss_sample": sample,
        "ready": ready,
        "not_ready": not_ready,
        "checked": checked,
    }


def record_lexical_miss(
    conn: sqlite3.Connection,
    query: str,
    fts_hits: int = 0,
) -> bool:
    """Write-path feeding the gate's miss-rate signal.

    Records one row per recall whose FTS5 leg result we want to track:
    ``(query, ts, fts_hits)``. The caller passes ``fts_hits`` = the number of
    BM25 candidates the lexical leg returned (0 == a lexical miss). Over time the
    ratio of zero-hit rows == the lexical-miss-rate the gate reads.

    Fail-graceful: any error (missing table, locked DB) returns False without
    raising -- recording a miss must NEVER break a recall. Does NOT commit (the
    caller owns the transaction, matching cmd_recall's pattern).

    Returns True on a successful INSERT, False otherwise.
    """
    try:
        # Guard: only write if the table exists (don't auto-create here -- the
        # migration owns the schema).
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='lexical_miss_log'"
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "INSERT INTO lexical_miss_log (query, ts, fts_hits) "
            "VALUES (?, datetime('now'), ?)",
            (query[:512] if query else "", int(fts_hits)),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("record_lexical_miss skipped: %s", e)
        return False


def wake_lane(name: str, *, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """CONSENT-GATED activation STUB. Logs the plan; NEVER downloads.

    This is the single chokepoint where a dormant lane *would* be materialized:
    model download + index build + VRAM. By design it does NONE of that. It
    resolves the lane, reports its wake cost, and returns a plan dict a human (or
    a future explicit-consent flow) can act on. A routine recall NEVER reaches
    here, and even a direct call only LOGS.

    Returns:
        ``{"lane": name, "would_download": bool, "wake_cost": str,
           "status": "logged-only", "ready": bool, "note": str}``.
    """
    lane = LANES.get(name)
    if lane is None:
        return {
            "lane": name,
            "would_download": False,
            "status": "unknown-lane",
            "note": f"no lane named {name!r}; known: {sorted(LANES)}",
        }
    if getattr(lane, "enabled", False):
        return {
            "lane": name,
            "would_download": False,
            "status": "already-active",
            "wake_cost": getattr(lane, "wake_cost", ""),
            "note": f"lane {name!r} is already active (no wake needed)",
        }

    ready = False
    if conn is not None:
        try:
            ready = name in gate_should_wake_lane(conn).get("ready", [])
        except Exception:
            ready = False

    cost = getattr(lane, "wake_cost", "(unspecified)")
    msg = (
        f"wake_lane({name!r}): WOULD download + build index for lane -- "
        f"cost: {cost}. NOT executed (consent-gated; auto-detect-never-auto-pull)."
    )
    logger.warning(msg)
    return {
        "lane": name,
        "would_download": True,
        "wake_cost": cost,
        "status": "logged-only",
        "ready": ready,
        "note": msg,
    }


# ==========================================================================
# Lane-fusion helper (the dormant path's exercise point).
# ==========================================================================

def lanes_retrieve_and_fuse(
    conn: sqlite3.Connection,
    query: str,
    query_vec: Any,
    *,
    k: int,
    rrf_fuse_fn: Callable[..., List[Tuple[int, float]]],
    lanes: Optional[Sequence[RetrievalLane]] = None,
    rrf_k: int = 15,
    top_k: Optional[int] = None,
) -> List[Tuple[int, float]]:
    """Run every given lane and fuse their ranked lists via ``rrf_fuse_fn``.

    This is the N-ary generalization of ``hybrid_retrieval.recall_hybrid``'s
    Stage 1+2 (retrieve each leg, RRF-fuse). With ``lanes=None`` it fuses the
    currently-ACTIVE lanes (vector + bm25 today) and is byte-identical to the
    legacy ``rrf_fuse(vector_leg, bm25_leg)`` ordering -- the formalization is a
    no-op on behavior (proven in the test-suite). With explicit ``lanes`` (e.g.
    vector + bm25 + MOCK sparse + MOCK colbert) it exercises the full 4-way
    dormant path so the architecture can't rot.

    Args:
        conn / query / query_vec / k: passed to each lane's ``retrieve``.
        rrf_fuse_fn: the fusion function (``hybrid_retrieval.rrf_fuse``; injected
            so this module has no hard dependency on hybrid_retrieval).
        lanes: which lanes to run. None -> ``active_lanes()``.
        rrf_k: RRF decay constant.
        top_k: fused-output cap (defaults to ``k``).

    Returns:
        ``[(memory_id, rrf_score)]`` best-first.
    """
    if lanes is None:
        lanes = active_lanes()
    legs: List[List[Tuple[int, float]]] = []
    for lane in lanes:
        if not getattr(lane, "enabled", False):
            continue
        leg = lane.retrieve(conn, query, query_vec, k)
        if leg:
            legs.append(leg)
    if not legs:
        return []
    return rrf_fuse_fn(*legs, k=rrf_k, top_k=(top_k if top_k is not None else k))


# ==========================================================================
# CLI smoke / self-test entry point.
# ==========================================================================

if __name__ == "__main__":
    import json
    import sys

    # `--gate <db>` prints the gate verdict (read-only) for a DB.
    if len(sys.argv) >= 3 and sys.argv[1] == "--gate":
        db = sys.argv[2]
        c = sqlite3.connect(db, timeout=60.0)
        try:
            c.enable_load_extension(True)
            import sqlite_vec  # type: ignore
            sqlite_vec.load(c)
            c.enable_load_extension(False)
        except Exception:
            pass
        print(json.dumps(gate_should_wake_lane(c), indent=2))
        c.close()
        sys.exit(0)

    print("recall_diversity.py -- MMR + adaptive cap + LitM + dormant lanes.")
    print(f"  DIVERSITY_LAMBDA={DIVERSITY_LAMBDA}")
    print(f"  active lanes:  {[l.name for l in active_lanes()]}")
    print(f"  dormant lanes: {[l.name for l in dormant_lanes()]}")
    print("Run test_recall_diversity.py for the full a-i suite.")

"""Personalized-PageRank associative recall organ (L5-A).

Adds *associative* relevance to the hot recall path. Today recall finds
memories that *sound* similar (vector + FTS5 + RRF + cross-encoder rerank);
this organ adds memories related by *association* — following the
``memory_edges`` graph so a query about one entity can surface
"Northwind Robotics" even with zero shared tokens.

Design (council, voidloom-l5-council #3954):
  * Hand-rolled **numpy** sparse power-iteration. NO scipy, NO networkx.
    numpy is already transitive via torch -> zero new install surface.
  * AUGMENT, never replace: ``score' = score + PPR_LAMBDA * ppr_mass`` with a
    SMALL lambda (0.15). Relevance must dominate; a single-hop recall must
    never be hijacked by hub centrality. Seed mass per result is proportional
    to that result's CURRENT rerank ``score`` (normalized to a probability
    distribution), so high-relevance hits spread the most associative mass.
  * Live-per-query. The graph is tiny (~12k edges / ~2k nodes after the 0.3
    confidence floor) so building a sparse adjacency + ~tens of power
    iterations per query is sub-millisecond-to-low-millisecond. NO transition
    matrix cron, NO precomputed neighborhood table.
  * Honors EXACTLY what the multi-hop CTE honors: ``valid_until IS NULL``
    (current edges only) AND a ``min_confidence`` floor (0.3). The PPR graph is
    built from the SAME edge subset.
  * Edge weight into the transition = ``predicate_weight x edge_confidence``.
    ``contradicts`` is EXCLUDED (weight 0 — a contradiction is not associative
    affinity). ``references`` is weighted LOW (0.2 — it is ~66% of the floored
    edges and weak, conf ~0.30; it must not let hub-y reference density
    dominate). Semantic-strength predicates (refines / depends_on / causes /
    instantiates / generalizes) are weighted high; ``supersedes`` moderate.

Kill-switch + fail-graceful: the CALLER gates this organ behind ``--no-ppr`` /
``VOIDLOOM_NO_PPR`` exactly like the sibling Loop A organs. ``apply_ppr`` itself
is fail-graceful: ANY error returns the input results unchanged (no raise), so
recall NEVER breaks and the base pipeline stays byte-identical.

Public surface:
  * ``PPR_PREDICATE_WEIGHTS`` / ``PPR_LAMBDA`` / ``PPR_ALPHA`` — module config.
  * ``is_ppr_available(conn)`` — edges table present + >=1 valid edge.
  * ``build_ppr_graph(conn, min_confidence=0.3)`` -> ``(node_index, adjacency)``.
  * ``personalized_pagerank(adjacency, seed_mass_vec, ...)`` -> mass array.
  * ``apply_ppr(conn, results, lambda_=0.15, min_confidence=0.3)`` ->
    re-sorted results with ``ppr_score`` / ``ppr_boosted`` stamped.

stdlib + numpy + sqlite3 only.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------
# Module-level configuration (surfaced so a Reflector/Curator can tune it).
# --------------------------------------------------------------------------

# Per-predicate associative weight. Multiplied by per-edge confidence to form
# the transition weight. ``contradicts`` -> 0.0 EXCLUDES it entirely (a
# contradiction is anti-association, not affinity). ``references`` is the
# dominant-but-weak bulk edge, kept LOW so reference hubs cannot dominate.
PPR_PREDICATE_WEIGHTS: Dict[str, float] = {
    "refines": 1.0,        # strongest semantic refinement
    "instantiates": 0.9,   # defined-but-unused today; ready when populated
    "depends_on": 0.9,     # hard structural dependency
    "causes": 0.85,        # causal association
    "generalizes": 0.8,    # defined-but-unused today; ready when populated
    "supersedes": 0.5,     # moderate — a temporal replacement, weaker affinity
    "references": 0.2,     # LOW — ~66% of floored edges, weak (conf ~0.30)
    "contradicts": 0.0,    # EXCLUDED — not associative affinity
}

# Blend weight: score' = score + PPR_LAMBDA * ppr_mass. Small on purpose so
# vector+rerank relevance dominates; PPR only nudges associatively-related
# memories upward and surfaces a few zero-overlap neighbors.
PPR_LAMBDA: float = 0.15

# PageRank damping factor (random-restart-to-seed probability = 1 - alpha).
PPR_ALPHA: float = 0.85

# Mirror the multi-hop CTE's confidence floor.
PPR_MIN_CONFIDENCE: float = 0.3

# Power-iteration controls.
PPR_TOL: float = 1e-6
PPR_MAX_ITER: int = 100

# Symmetrize "association" edges. Rationale: associative *relatedness* is
# bidirectional ("A refines B" means B is associatively reachable from A and
# vice-versa for recall surfacing), and the multi-hop CTE itself walks BOTH
# outgoing and incoming edges. We therefore add the transpose of the weighted
# adjacency before column-normalizing, so a seed reaches both its out- and
# in-neighbors. We do NOT preserve directionality asymmetry for v1: directional
# subtleties (causes vs caused-by) are out of scope and the CTE already treats
# the graph as effectively undirected for traversal. ``supersedes`` is included
# in symmetrization too — being superseded-by is as associatively informative
# as superseding.
PPR_SYMMETRIZE: bool = True


# --------------------------------------------------------------------------
# Availability guard.
# --------------------------------------------------------------------------

def is_ppr_available(conn: sqlite3.Connection) -> bool:
    """True iff the memory_edges table exists and has >=1 currently-valid edge.

    Mirrors the other organs' migration guards. Fail-safe: any error -> False
    (the caller then skips PPR and base recall stands).
    """
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_edges'"
        ).fetchone()
        if not row:
            return False
        cnt = conn.execute(
            "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL"
        ).fetchone()[0]
        return cnt > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Graph construction.
# --------------------------------------------------------------------------

class SparseTransition:
    """A column-stochastic sparse transition operator (hand-rolled, numpy only).

    Stores the weighted edge list as parallel numpy index/weight arrays plus a
    per-column normalizer, and implements ``matvec(r) = M @ r`` via a single
    ``np.add.at`` scatter-add. This is the whole reason PPR is fast on the live
    graph: a DENSE ``(N, N)`` matmul over ~2k nodes is ~4M flops/iteration and
    measured ~400ms; the sparse scatter touches only the ~24k (symmetrized)
    edges and runs in well under a millisecond.

    Semantics: an edge ``(src, tgt, w)`` contributes mass from ``src`` to
    ``tgt`` (and, when symmetrized, ``tgt`` to ``src``). Each source column is
    normalized to sum to 1 over its weighted out-neighbors. ``col_sums`` marks
    dangling columns (no out-weight) for the power iteration's teleport handling.
    """

    __slots__ = ("n", "rows", "cols", "weights", "col_sums")

    def __init__(self, n: int, rows: np.ndarray, cols: np.ndarray,
                 weights: np.ndarray, col_sums: np.ndarray):
        self.n = n
        self.rows = rows        # destination indices (mass arrives here)
        self.cols = cols        # source indices (mass leaves here)
        self.weights = weights  # already column-normalized weights
        self.col_sums = col_sums

    def sum(self, axis=0):
        # Compatibility shim so personalized_pagerank can ask for column sums
        # exactly as it would from a dense numpy matrix.
        if axis == 0:
            return self.col_sums
        raise ValueError("SparseTransition.sum only supports axis=0")

    @property
    def shape(self):
        return (self.n, self.n)

    def __matmul__(self, r: np.ndarray) -> np.ndarray:
        """M @ r: scatter mass from each source to its destinations."""
        out = np.zeros(self.n, dtype=np.float64)
        # contribution to out[rows[k]] += weights[k] * r[cols[k]]
        np.add.at(out, self.rows, self.weights * r[self.cols])
        return out


def build_ppr_graph(
    conn: sqlite3.Connection,
    min_confidence: float = PPR_MIN_CONFIDENCE,
) -> Tuple[Dict[int, int], "SparseTransition"]:
    """Build a predicate-weighted, column-normalized SPARSE transition operator.

    Honors EXACTLY the CTE's edge subset: ``valid_until IS NULL`` AND
    ``confidence >= min_confidence``. ``contradicts`` edges (weight 0) are
    dropped. Edge weight = ``PPR_PREDICATE_WEIGHTS[predicate] * confidence``.

    Returns:
        ``(node_index, adjacency)`` where
          * ``node_index`` maps ``memory_id -> matrix row/col index``, and
          * ``adjacency`` is a :class:`SparseTransition` whose ``@`` implements
            ``rank = adjacency @ rank`` with mass flowing source->target
            (column = source, row = target), column-stochastic.

    Dangling columns (a node with no out-weight) are handled by the
    power-iteration's dangling-mass redistribution, not here. If the floored
    subset is empty, returns ``({}, SparseTransition(0, ...))``.

    Direction: we symmetrize associative edges (see ``PPR_SYMMETRIZE``) by
    materializing each edge in both directions before column-normalizing.
    """
    # Pull only the floored, currently-valid edges. The WHERE clause does the
    # valid_until + confidence floor (identical to the CTE); the predicate
    # weight filter (contradicts -> 0) is applied in Python so the weight table
    # stays the single source of truth.
    rows = conn.execute(
        """
        SELECT source_id, target_id, predicate, confidence
        FROM memory_edges
        WHERE valid_until IS NULL
          AND confidence >= ?
        """,
        (min_confidence,),
    ).fetchall()

    # First pass: collect weighted edges and the node set.
    src_list: List[int] = []
    tgt_list: List[int] = []
    w_list: List[float] = []
    node_set = set()
    for source_id, target_id, predicate, confidence in rows:
        pw = PPR_PREDICATE_WEIGHTS.get(predicate, 0.0)
        if pw <= 0.0:
            continue  # contradicts / unknown predicate -> excluded
        if confidence is None or confidence <= 0.0:
            continue
        w = pw * float(confidence)
        if w <= 0.0:
            continue
        src_list.append(source_id)
        tgt_list.append(target_id)
        w_list.append(w)
        node_set.add(source_id)
        node_set.add(target_id)

    if not node_set:
        return {}, SparseTransition(
            0,
            np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64),
        )

    # Stable, deterministic node ordering -> deterministic operator.
    nodes = sorted(node_set)
    node_index = {mid: i for i, mid in enumerate(nodes)}
    n = len(nodes)

    src_idx = np.fromiter((node_index[s] for s in src_list), dtype=np.int64, count=len(src_list))
    tgt_idx = np.fromiter((node_index[t] for t in tgt_list), dtype=np.int64, count=len(tgt_list))
    w_arr = np.asarray(w_list, dtype=np.float64)

    # Materialize flow as (col=source, row=target). Symmetrize by appending the
    # reverse direction (col=target, row=source) with the SAME weight, mirroring
    # how the multi-hop CTE walks both outgoing and incoming edges.
    if PPR_SYMMETRIZE:
        cols = np.concatenate([src_idx, tgt_idx])
        dst = np.concatenate([tgt_idx, src_idx])
        ws = np.concatenate([w_arr, w_arr])
    else:
        cols = src_idx
        dst = tgt_idx
        ws = w_arr

    # Column sums = total out-weight per source. Column-normalize so each source
    # column is a probability distribution over its weighted out-neighbors.
    col_sums = np.zeros(n, dtype=np.float64)
    np.add.at(col_sums, cols, ws)
    norm = np.where(col_sums > 0.0, col_sums, 1.0)  # avoid /0 on dangling cols
    ws_norm = ws / norm[cols]

    return node_index, SparseTransition(n, dst, cols, ws_norm, col_sums)


# --------------------------------------------------------------------------
# Power iteration.
# --------------------------------------------------------------------------

def personalized_pagerank(
    adjacency: np.ndarray,
    seed_mass_vec: np.ndarray,
    alpha: float = PPR_ALPHA,
    tol: float = PPR_TOL,
    max_iter: int = PPR_MAX_ITER,
) -> np.ndarray:
    """Personalized PageRank via numpy power-iteration.

    Solves ``r = alpha * M @ r + (1 - alpha) * s`` where ``M`` is the
    column-stochastic transition matrix (``adjacency``) and ``s`` is the
    personalization (seed) distribution. Dangling columns of ``M`` (all-zero)
    have their mass redistributed back to the seed distribution ``s`` (the
    standard "teleport to the personalization vector" handling), which keeps
    the chain stochastic and the result a proper probability distribution.

    Args:
        adjacency: ``(N, N)`` column-stochastic transition matrix.
        seed_mass_vec: ``(N,)`` non-negative personalization vector. Need not be
            normalized; it is normalized to sum 1 internally (if it sums to 0,
            a uniform distribution is used as a fail-safe).
        alpha: damping factor (restart-to-seed prob = ``1 - alpha``).
        tol: L1 convergence tolerance.
        max_iter: hard iteration cap.

    Returns:
        ``(N,)`` PageRank mass vector summing to ~1.0. For ``N == 0`` returns an
        empty array.
    """
    n = adjacency.shape[0]
    if n == 0:
        return np.zeros((0,), dtype=np.float64)

    s = np.asarray(seed_mass_vec, dtype=np.float64).reshape(-1)
    if s.shape[0] != n:
        # Defensive: mismatched personalization -> uniform.
        s = np.ones(n, dtype=np.float64) / n
    s_sum = s.sum()
    if s_sum <= 0.0:
        s = np.ones(n, dtype=np.float64) / n
    else:
        s = s / s_sum

    # Dangling columns: a node with no out-mass. Their probability mass must go
    # somewhere; teleport it to the personalization vector s (standard PPR).
    col_sums = adjacency.sum(axis=0)
    dangling = (col_sums <= 0.0)

    r = s.copy()
    one_minus_alpha = 1.0 - alpha
    for _ in range(max_iter):
        # Mass that lands on dangling nodes this step is redistributed via s.
        dangling_mass = r[dangling].sum() if dangling.any() else 0.0
        r_next = (
            alpha * (adjacency @ r)
            + alpha * dangling_mass * s
            + one_minus_alpha * s
        )
        # Numerical guard: renormalize to a probability distribution.
        total = r_next.sum()
        if total > 0.0:
            r_next = r_next / total
        if np.abs(r_next - r).sum() < tol:
            r = r_next
            break
        r = r_next
    return r


# --------------------------------------------------------------------------
# The organ entry point.
# --------------------------------------------------------------------------

def apply_ppr(
    conn: sqlite3.Connection,
    results: List[Dict[str, Any]],
    lambda_: float = PPR_LAMBDA,
    min_confidence: float = PPR_MIN_CONFIDENCE,
    alpha: float = PPR_ALPHA,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Blend Personalized-PageRank associative mass into recall results.

    Seed mass per result is proportional to that result's current ``score``
    (normalized to a distribution), so higher-relevance hits spread more
    associative mass. PPR is run over the predicate-weighted edge graph; each
    result's ``ppr_score`` is its own PageRank mass, and its blended score
    becomes ``score + lambda_ * ppr_mass``. Results are re-sorted by the blended
    score (descending).

    IMPORTANT — fail-graceful contract: ANY error returns ``(results, info)``
    with results UNCHANGED (no re-sort, no stamping) and ``info`` carrying an
    error string. The caller treats this exactly like the sibling organs: base
    results stand, recall never breaks. With ``lambda_ = 0`` or an empty/edgeless
    graph the results are returned byte-identical (no stamping, no re-sort), so
    the kill-switched path is a perfect no-op.

    Stamps onto each touched result dict (mirroring foundation_score):
      * ``ppr_score``  : float — the result's raw PageRank mass (0.0 if the
        result's id is not a node in the floored graph).
      * ``ppr_boosted``: bool  — True iff this result received non-zero PPR mass.

    Returns:
        ``(re-sorted results, info_dict)``. ``info_dict`` keys: ``applied``
        (bool), ``nodes`` (graph node count), ``seeded`` (number of result ids
        that were graph nodes), ``boosted`` (number with non-zero mass),
        ``lambda`` (the blend weight), and on failure ``error``.
    """
    info: Dict[str, Any] = {"applied": False}

    # Cheap exits that keep results byte-identical.
    if not results:
        return results, info
    if lambda_ <= 0.0:
        info["error"] = "lambda<=0 (no-op)"
        return results, info

    try:
        # Build the graph from the floored, valid, predicate-weighted edges.
        node_index, adjacency = build_ppr_graph(conn, min_confidence=min_confidence)
        n = adjacency.shape[0]
        if n == 0:
            info["error"] = "empty graph"
            return results, info

        # Build the seed (personalization) distribution from result scores.
        # Seed mass for a result lands on its node iff that id is in the graph.
        # Mass is proportional to the result's current rerank score; results
        # with non-positive score get zero seed mass (they cannot personalize).
        seed = np.zeros(n, dtype=np.float64)
        seeded = 0
        for r in results:
            mid = r.get("id")
            idx = node_index.get(mid)
            if idx is None:
                continue
            sc = r.get("score", r.get("relevance_score", 0.0))
            try:
                sc = float(sc)
            except (TypeError, ValueError):
                sc = 0.0
            if sc > 0.0:
                seed[idx] += sc
                seeded += 1

        if seed.sum() <= 0.0:
            # No result is a graph node with positive score -> nothing to seed.
            # Stamp zeros so provenance is consistent, but DO NOT re-sort
            # (order is unchanged, so this stays a faithful no-op on ranking).
            for r in results:
                r.setdefault("ppr_score", 0.0)
                r.setdefault("ppr_boosted", False)
            info.update({"applied": True, "nodes": n, "seeded": 0, "boosted": 0,
                         "lambda": lambda_, "note": "no seedable results"})
            return results, info

        # Run PPR.
        mass = personalized_pagerank(adjacency, seed, alpha=alpha)

        # Stamp each result's own PPR mass and blend.
        boosted = 0
        for r in results:
            mid = r.get("id")
            idx = node_index.get(mid)
            ppr_mass = float(mass[idx]) if idx is not None else 0.0
            r["ppr_score"] = ppr_mass
            if ppr_mass > 0.0:
                base = r.get("score", r.get("relevance_score", 0.0))
                try:
                    base = float(base)
                except (TypeError, ValueError):
                    base = 0.0
                r["score"] = base + lambda_ * ppr_mass
                r["ppr_boosted"] = True
                boosted += 1
            else:
                r["ppr_boosted"] = False

        # Re-sort by the blended score (descending), mirroring the foundation
        # boost's final re-sort.
        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        info.update({"applied": True, "nodes": n, "seeded": seeded,
                     "boosted": boosted, "lambda": lambda_})
        return results, info

    except Exception as e:  # noqa: BLE001 — never break recall
        info["error"] = str(e)
        return results, info

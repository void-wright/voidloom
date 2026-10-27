#!/usr/bin/env python3
"""Leiden Cognitive-Map Communities -- Voidloom Level-5 (L5-E-COMM).

Auto-group memories into real *themes* for the cognitive map. The existing
``cognitive_map.py`` clusters by metadata (kind / project / tag GROUP BY) -- a
structural view. This organ adds the COMPLEMENTARY view: graph-topology
communities mined from the *settled* temporal-edge graph. The council ruling
(memory #5829) is explicit: Leiden **AUGMENTS, never replaces** the GROUP BY
clustering -- "the best dashboard panel is where the two lenses disagree".

E was SPLIT into E-COMM + E-PRED (fusing them lets the safe half smuggle the
dangerous half past scrutiny). This module is E-COMM ONLY: community *detection*
+ its 3 tables + *lazy summaries*. It deliberately does NOT build:
  * the community RECALL LENS (a recall-signal promotion) -- a separate gated
    decision that must later clear Loop A's bar (sub-10ms join + eval lift);
  * the dashboard panel wiring -- a follow-on.

Three-tier detection (graceful degradation is a provable SPINE, not a feature):
  Tier-2  Leiden          -- if ``igraph`` + ``leidenalg`` import (Tier-1
                            C-extension deps; FEATURE-DETECTED here; verified
                            NEITHER is installed today -> the fallback path is
                            the live reality, mandated by the two-tier install
                            doctrine).
  Tier-1  GROUP BY        -- the existing structural clustering (by tag, the
                            closest theme-proxy in metadata). The same lens
                            ``cognitive_map.py`` already ships, surfaced as
                            communities so the abstraction is non-empty without
                            igraph.
  Tier-0  connected-comp  -- a crude stdlib connected-components "proto-
                            community" over the edge graph, giving the cognitive
                            map the *topology* abstraction even when neither
                            igraph nor a useful tag set is present.

NON-NEGOTIABLE DISCIPLINE (council)
-----------------------------------
  * PURE CONSUMER. Reads the settled graph; writes ONLY its own three tables
    (``memory_communities`` / ``memory_community_members`` / ``community_summaries``).
    NEVER ``memory_edges`` (or any other pre-existing table). A post-run
    fingerprint of ``memory_edges`` MUST be byte-identical.
  * Runs on the SETTLED nightly graph LAST (after extraction -> belief ->
    NREM-settle -> PageRank), weighted by ``confidence``, EXCLUDING
    ``valid_until IS NOT NULL`` edges (the 296 belief-engine-retired rows must
    not shape themes -- same reader-correctness rule L5-D enforced on the map).
  * Partition stability: a PINNED SEED + Jaccard ID-carry-forward so community
    IDs do not churn night to night (a churning ID makes a "community" label
    meaningless across days, and burns the lazy-summary cache every night).
  * Lazy summaries regenerate ONLY on a content-hash of the currently-valid
    member edges (single-flight lock). Default the cheap nightly summarizer
    (Stage-1 / extractive); never block the cycle on an LLM call.
  * Stays pure-stdlib (+ optional igraph/leidenalg). No dependency-contract
    change; numpy is NOT required.

Public API
----------
``ensure_communities_migration(conn) -> bool``        idempotent table guard
``is_communities_migration_applied(conn) -> bool``    presence check
``leiden_available() -> bool``                        Tier-2 feature-detect
``build_graph(conn, ...) -> Graph``                   settled, valid-only, weighted
``detect_communities(conn, cfg=None, ...) -> dict``   Leiden|GROUP BY|conn-comp
``compute_summaries(conn, cfg=None, ...) -> dict``    lazy, content-hash-gated
``run_communities_stage(conn, cfg=None, batch_id=None) -> dict``  (fail-soft)

CLI
---
::

    python3 communities.py --unit-tests             # self-test (synthetic DB)
    python3 communities.py --detect <db_path>       # detect + show communities
    python3 communities.py --summaries <db_path>    # (re)compute lazy summaries
    python3 communities.py --stage <db_path>        # run the sleep-loop stage

Schema: 3.14-L5-communities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# Feature detection (Tier-1 C-extension deps -- absent today)
# ============================================================

# We probe lazily inside leiden_available() rather than at import so that a
# half-installed igraph (importable but broken) degrades to the fallback rather
# than crashing the whole module at import time.
_LEIDEN_PROBE: Optional[bool] = None


def leiden_available() -> bool:
    """True iff both ``igraph`` and ``leidenalg`` import cleanly (Tier-2).

    Result is memoised. ANY import failure (missing, broken C-extension, version
    skew) returns False -> the organ falls back to GROUP BY / connected-
    components. Verified: neither is installed on the canonical brain today.
    """
    global _LEIDEN_PROBE
    if _LEIDEN_PROBE is None:
        try:
            import igraph  # type: ignore  # noqa: F401
            import leidenalg  # type: ignore  # noqa: F401
            _LEIDEN_PROBE = True
        except Exception:  # ImportError, OSError (bad .so), ValueError, ...
            _LEIDEN_PROBE = False
    return _LEIDEN_PROBE


# ============================================================
# Kill-switch (matches the Voidloom env family)
# ============================================================

# VOIDLOOM_NO_PPR / _NO_DIVERSITY / _NO_TEMPORAL_RERANK / _NO_QUICKENING / ...
ENV_KILL_SWITCH = "VOIDLOOM_NO_COMMUNITIES"


# ============================================================
# Configuration (overridable via the sleep-loop cfg dict)
# ============================================================

DEFAULT_COMMUNITIES_CONFIG: Dict[str, Any] = {
    # --- graph construction ---
    # Edges below this confidence are too weak to define a theme. The hub
    # predicate `references` (27k rows, ~0.24 heuristic band) would otherwise
    # smear every memory into one giant blob; a modest floor keeps communities
    # meaningful without excluding the high-confidence semantic predicates.
    "min_edge_confidence": 0.30,
    # `contradicts` connects memories that DISAGREE -- a contradiction is not
    # thematic kinship, so it must not pull two memories into one community
    # (mirrors the predicate-weighting skill: contradicts EXCLUDED).
    "exclude_predicates": ("contradicts",),
    # --- partition shape ---
    # A "community" smaller than this is noise, not a theme; such members are
    # left unassigned (community_id absent) rather than minting singletons.
    "min_community_size": 3,
    # Leiden resolution (higher -> more, smaller communities). Only used Tier-2.
    "leiden_resolution": 1.0,
    # PINNED SEED for partition stability (Leiden is randomised; a fixed seed +
    # the carry-forward below keeps IDs stable night to night).
    "seed": 1729,
    # --- carry-forward (ID stability) ---
    # A new partition's cluster inherits a prior community_id when their member
    # sets overlap by >= this Jaccard. Below it, a fresh id is minted.
    "carry_forward_jaccard": 0.5,
    # --- lazy summaries ---
    # Cap on member memories fed to the summarizer (keeps the cheap Stage-1
    # extractive path bounded; the LLM path, when wired, reads the same cap).
    "summary_member_cap": 40,
    # 'extractive' = cheap, deterministic, no LLM (the nightly default). 'llm'
    # is reserved for a daytime Stage-2 pass (NOT invoked from the sleep loop).
    "summary_mode": "extractive",
}


# ============================================================
# Idempotent migration (the 3 tables)
# ============================================================

MIGRATION_FILENAME = "migration_028_communities.sql"

_MIGRATION_SQL = """
-- L5-E-COMM: Leiden Cognitive-Map Communities (+ lazy summaries).
-- Three PURE-CONSUMER tables. This organ reads the settled temporal-edge graph
-- and writes ONLY here; it NEVER mutates memory_edges or any pre-existing table.
-- Idempotent: every statement uses IF NOT EXISTS / OR REPLACE.

-- One row per detected community per run-epoch. community_id is STABLE across
-- nights via Jaccard carry-forward (see communities.py); `epoch` distinguishes
-- successive detections so history is queryable without churning the id.
CREATE TABLE IF NOT EXISTS memory_communities (
    community_id   INTEGER NOT NULL,          -- STABLE theme id (carry-forward)
    epoch          INTEGER NOT NULL,          -- detection run-epoch (monotonic)
    method         TEXT NOT NULL CHECK(method IN ('leiden','group_by','connected_components')),
    size           INTEGER NOT NULL,          -- member count
    label          TEXT,                       -- short human label (e.g. top tag)
    member_edge_hash TEXT,                     -- content-hash of valid member edges
    seed           INTEGER,                    -- the pinned seed used (provenance)
    batch_id       INTEGER,                    -- the sleep-loop run that detected it
    detected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (community_id, epoch)
);
CREATE INDEX IF NOT EXISTS idx_comm_epoch  ON memory_communities(epoch);
CREATE INDEX IF NOT EXISTS idx_comm_method ON memory_communities(method);

-- Membership: one row per (memory, community, epoch). A memory belongs to at
-- most one community per epoch (the partition is disjoint).
CREATE TABLE IF NOT EXISTS memory_community_members (
    community_id INTEGER NOT NULL,
    epoch        INTEGER NOT NULL,
    memory_id    INTEGER NOT NULL,
    PRIMARY KEY (epoch, memory_id),
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_commmem_community ON memory_community_members(community_id, epoch);
CREATE INDEX IF NOT EXISTS idx_commmem_memory    ON memory_community_members(memory_id);

-- Lazy summaries: one CURRENT row per stable community_id, regenerated ONLY when
-- the content-hash of its currently-valid member edges changes (single-flight).
CREATE TABLE IF NOT EXISTS community_summaries (
    community_id   INTEGER PRIMARY KEY,        -- stable id (NOT epoch-scoped)
    summary        TEXT,
    member_edge_hash TEXT NOT NULL,            -- the hash this summary was built for
    summary_mode   TEXT NOT NULL DEFAULT 'extractive',
    member_count   INTEGER NOT NULL DEFAULT 0,
    generated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Schema version bump (additive; does not gate any existing reader).
INSERT OR REPLACE INTO metadata (key, value)
VALUES ('schema_version', '3.14-L5-communities');
INSERT OR REPLACE INTO metadata (key, value)
VALUES ('migration_028_communities_applied_at', datetime('now'));
"""


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def is_communities_migration_applied(conn: sqlite3.Connection) -> bool:
    """True iff all three community tables exist."""
    return (
        _has_table(conn, "memory_communities")
        and _has_table(conn, "memory_community_members")
        and _has_table(conn, "community_summaries")
    )


def ensure_communities_migration(conn: sqlite3.Connection) -> bool:
    """Apply the community-tables migration idempotently. Returns True if all
    three tables are present after the call (pre-existing or just created).

    Prefers the on-disk ``migration_028_communities.sql`` (the canonical
    artifact) and falls back to the embedded ``_MIGRATION_SQL`` so the module is
    self-sufficient even if the .sql file is not co-located (mirrors the
    sleep-loop / quickening discipline)."""
    try:
        sql = _MIGRATION_SQL
        mig_path = Path(__file__).resolve().parent.parent / "migrations" / MIGRATION_FILENAME
        if mig_path.exists():
            try:
                sql = mig_path.read_text()
            except OSError:
                sql = _MIGRATION_SQL
        conn.executescript(sql)
        conn.commit()
        return is_communities_migration_applied(conn)
    except sqlite3.Error as e:  # pragma: no cover - defensive
        logger.error("communities migration failed: %s", e)
        return False


# ============================================================
# Graph construction (settled, valid-only, confidence-weighted)
# ============================================================

class Graph:
    """A tiny undirected weighted graph over memory ids.

    Edges from ``memory_edges`` are symmetrised (the community lens is
    undirected -- "these two memories belong to the same theme" is mutual even
    though the stored edge is directed). Parallel edges between the same pair are
    collapsed by summing confidence (a pair linked by several predicates is more
    strongly bound).
    """

    __slots__ = ("adj", "weight", "_nodes")

    def __init__(self) -> None:
        self.adj: Dict[int, set] = defaultdict(set)
        self.weight: Dict[Tuple[int, int], float] = {}
        self._nodes: set = set()

    @staticmethod
    def _key(a: int, b: int) -> Tuple[int, int]:
        return (a, b) if a <= b else (b, a)

    def add_edge(self, a: int, b: int, w: float) -> None:
        if a == b:
            return
        self._nodes.add(a)
        self._nodes.add(b)
        self.adj[a].add(b)
        self.adj[b].add(a)
        k = self._key(a, b)
        self.weight[k] = self.weight.get(k, 0.0) + w

    def nodes(self) -> List[int]:
        return sorted(self._nodes)

    def neighbours(self, n: int) -> List[int]:
        return sorted(self.adj.get(n, ()))

    def edge_weight(self, a: int, b: int) -> float:
        return self.weight.get(self._key(a, b), 0.0)

    def num_nodes(self) -> int:
        return len(self._nodes)

    def num_edges(self) -> int:
        return len(self.weight)

    def edge_list(self) -> List[Tuple[int, int, float]]:
        """Deterministic (sorted) list of (a, b, weight) with a < b."""
        return [(a, b, self.weight[(a, b)]) for (a, b) in sorted(self.weight.keys())]


def _build_edge_filter_sql(cfg: Dict[str, Any]) -> Tuple[str, list]:
    """Build the WHERE clause + params that select the settled, valid, weighted
    edge set. EXCLUDES ``valid_until IS NOT NULL`` (retired edges), edges below
    the confidence floor, and the excluded predicates (contradicts)."""
    clauses = ["valid_until IS NULL", "confidence >= ?"]
    params: list = [float(cfg["min_edge_confidence"])]
    excl = list(cfg.get("exclude_predicates") or ())
    if excl:
        placeholders = ",".join("?" * len(excl))
        clauses.append(f"predicate NOT IN ({placeholders})")
        params.extend(excl)
    return " AND ".join(clauses), params


def build_graph(conn: sqlite3.Connection, cfg: Optional[Dict[str, Any]] = None) -> Graph:
    """Construct the undirected weighted community graph from the SETTLED edges.

    PURE READ. Only ``memory_edges`` (SELECT) + a membership filter to
    non-archived memories. Confidence is the edge weight; retired / weak /
    contradicts edges are excluded per the council ruling."""
    conf = {**DEFAULT_COMMUNITIES_CONFIG, **(cfg or {})}
    where, params = _build_edge_filter_sql(conf)
    g = Graph()
    # Restrict endpoints to live (non-archived) memories so a community never
    # forms around a tombstoned node. Subquery keeps it a single read.
    rows = conn.execute(
        f"""
        SELECT e.source_id, e.target_id, e.confidence
        FROM memory_edges e
        WHERE {where}
          AND e.source_id IN (SELECT id FROM memories WHERE is_archived = 0)
          AND e.target_id IN (SELECT id FROM memories WHERE is_archived = 0)
        """,
        params,
    ).fetchall()
    for src, tgt, conf_w in rows:
        try:
            w = float(conf_w) if conf_w is not None else 0.0
        except (TypeError, ValueError):
            w = 0.0
        g.add_edge(int(src), int(tgt), w)
    return g


# ============================================================
# Tier-0: stdlib connected-components proto-communities
# ============================================================

def connected_components(g: Graph, min_size: int = 1) -> List[List[int]]:
    """Undirected connected components via BFS (pure stdlib). Deterministic:
    nodes are visited in sorted order, so the component ordering is stable.

    A connected component is the crudest possible "community" -- it captures the
    topology (these memories are reachable from one another) without any modular-
    ity optimisation. It is Tier-0: always available, gives the cognitive map the
    abstraction even when igraph is absent and the tag set is useless."""
    seen: set = set()
    components: List[List[int]] = []
    for start in g.nodes():  # sorted -> deterministic
        if start in seen:
            continue
        comp: List[int] = []
        q = deque([start])
        seen.add(start)
        while q:
            node = q.popleft()
            comp.append(node)
            for nb in g.neighbours(node):
                if nb not in seen:
                    seen.add(nb)
                    q.append(nb)
        comp.sort()
        if len(comp) >= min_size:
            components.append(comp)
    # Largest first, then by smallest member id -> stable, readable order.
    components.sort(key=lambda c: (-len(c), c[0]))
    return components


# ============================================================
# Tier-1: GROUP BY structural clustering (the existing lens)
# ============================================================

def group_by_clusters(
    conn: sqlite3.Connection, min_size: int = 1, restrict_to: Optional[set] = None
) -> List[Tuple[str, List[int]]]:
    """The existing structural clustering, surfaced as communities. Groups live
    memories by TAG (the closest theme-proxy in metadata; a memory carrying tag
    'belief-engine' is thematically about the belief engine). Falls back to
    ``source_project`` grouping if the corpus has no tags at all.

    Returns ``[(label, [member_ids...]), ...]`` largest first. This AUGMENTS
    nothing on its own -- it IS the GROUP BY view; the value is that the organ
    speaks one vocabulary (communities) regardless of which tier produced them.

    ``restrict_to`` (optional) limits members to a node set (e.g. the graph's
    nodes) so the GROUP BY fallback aligns with the same population Leiden would
    have clustered."""
    has_tags = _has_table(conn, "memory_tags")
    clusters: Dict[str, List[int]] = defaultdict(list)
    if has_tags:
        rows = conn.execute(
            """
            SELECT t.tag AS label, m.id AS mid
            FROM memory_tags t
            JOIN memories m ON m.id = t.memory_id
            WHERE m.is_archived = 0
            ORDER BY t.tag, m.id
            """
        ).fetchall()
        for label, mid in rows:
            if restrict_to is not None and int(mid) not in restrict_to:
                continue
            clusters[str(label)].append(int(mid))
    if not clusters:
        # No tags (or none survived the restrict) -> project grouping.
        rows = conn.execute(
            """
            SELECT COALESCE(source_project, 'untagged') AS label, id AS mid
            FROM memories
            WHERE is_archived = 0
            ORDER BY label, id
            """
        ).fetchall()
        for label, mid in rows:
            if restrict_to is not None and int(mid) not in restrict_to:
                continue
            clusters[str(label)].append(int(mid))
    # Dedup members within a cluster (a memory can carry a tag once) + filter.
    result: List[Tuple[str, List[int]]] = []
    for label, members in clusters.items():
        uniq = sorted(set(members))
        if len(uniq) >= min_size:
            result.append((label, uniq))
    result.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    return result


# ============================================================
# Tier-2: Leiden (only when igraph + leidenalg are present)
# ============================================================

def _leiden_partition(g: Graph, cfg: Dict[str, Any]) -> List[List[int]]:
    """Run Leiden community detection (RBConfigurationVertexPartition, weighted)
    with a PINNED SEED. Only called when ``leiden_available()`` is True.

    Returns partitions as lists of memory ids, largest first. Raises on any
    igraph/leidenalg error so the caller can fall back (the stage wrapper catches
    it)."""
    import igraph  # type: ignore
    import leidenalg  # type: ignore

    nodes = g.nodes()
    index = {n: i for i, n in enumerate(nodes)}
    edges = [(index[a], index[b]) for (a, b, _w) in g.edge_list()]
    weights = [w for (_a, _b, w) in g.edge_list()]

    ig = igraph.Graph(n=len(nodes), edges=edges, directed=False)
    if weights:
        ig.es["weight"] = weights

    part = leidenalg.find_partition(
        ig,
        leidenalg.RBConfigurationVertexPartition,
        weights="weight" if weights else None,
        resolution_parameter=float(cfg["leiden_resolution"]),
        seed=int(cfg["seed"]),
    )

    communities: List[List[int]] = []
    for members in part:
        ids = sorted(nodes[i] for i in members)
        communities.append(ids)
    communities.sort(key=lambda c: (-len(c), c[0] if c else 0))
    return communities


# ============================================================
# Partition stability: Jaccard ID carry-forward
# ============================================================

def _jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _load_prior_partition(conn: sqlite3.Connection) -> Tuple[Optional[int], Dict[int, List[int]]]:
    """Load the most recent prior partition (the max epoch already stored) as
    ``{community_id: [member_ids...]}`` plus that epoch number, for carry-
    forward. Returns ``(None, {})`` when no prior partition exists."""
    if not _has_table(conn, "memory_communities"):
        return None, {}
    row = conn.execute("SELECT MAX(epoch) FROM memory_communities").fetchone()
    if not row or row[0] is None:
        return None, {}
    prior_epoch = int(row[0])
    members: Dict[int, List[int]] = defaultdict(list)
    for cid, mid in conn.execute(
        "SELECT community_id, memory_id FROM memory_community_members WHERE epoch = ?",
        (prior_epoch,),
    ).fetchall():
        members[int(cid)].append(int(mid))
    return prior_epoch, members


def carry_forward_ids(
    new_partitions: List[List[int]],
    prior: Dict[int, List[int]],
    jaccard_threshold: float,
    id_floor: int,
) -> List[Tuple[int, List[int]]]:
    """Assign each new partition a STABLE community_id.

    A new partition inherits the prior community_id whose member set it overlaps
    most (>= ``jaccard_threshold``); otherwise a fresh id is minted above
    ``id_floor`` (the max id ever used). Greedy best-match, each prior id claimed
    at most once -- so two new clusters cannot both inherit the same old id (that
    would alias distinct themes). Deterministic: new partitions are processed
    largest-first (already sorted), ties broken by smallest member id."""
    used_prior: set = set()
    assignments: List[Tuple[int, List[int]]] = []
    next_id = id_floor + 1

    # Pre-compute best candidate per new partition for a stable greedy pass.
    for members in new_partitions:
        best_cid: Optional[int] = None
        best_j = jaccard_threshold  # must strictly meet the threshold
        for cid, old_members in sorted(prior.items()):
            if cid in used_prior:
                continue
            j = _jaccard(members, old_members)
            if j >= best_j and (best_cid is None or j > best_j):
                best_j = j
                best_cid = cid
        if best_cid is not None:
            used_prior.add(best_cid)
            assignments.append((best_cid, members))
        else:
            assignments.append((next_id, members))
            next_id += 1
    return assignments


def _max_community_id(conn: sqlite3.Connection) -> int:
    """Highest community_id ever minted (across all epochs), so a fresh id never
    collides with a retired one."""
    if not _has_table(conn, "memory_communities"):
        return 0
    row = conn.execute("SELECT MAX(community_id) FROM memory_communities").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# ============================================================
# Member-edge content hash (lazy-summary cache key)
# ============================================================

def member_edge_hash(conn: sqlite3.Connection, member_ids: Sequence[int], cfg: Dict[str, Any]) -> str:
    """Stable content-hash of the currently-VALID edges INTERNAL to a community.

    The summary depends on the relationships among the community's members; if
    none of those edges changed (none added, removed, or re-weighted, and none
    retired via valid_until), the summary is still current and must NOT be
    regenerated. Hashing the sorted (src,tgt,predicate,confidence) tuples of the
    internal valid edges captures exactly that.

    Deterministic + order-independent (we sort). Excludes the same retired /
    excluded edges ``build_graph`` excludes, so the hash and the graph agree."""
    ids = sorted(set(int(m) for m in member_ids))
    if not ids:
        return hashlib.sha256(b"empty").hexdigest()
    member_set = set(ids)
    where, params = _build_edge_filter_sql(cfg)
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT source_id, target_id, predicate, confidence
        FROM memory_edges
        WHERE {where}
          AND source_id IN ({placeholders})
          AND target_id IN ({placeholders})
        """,
        params + ids + ids,
    ).fetchall()
    internal: List[Tuple[int, int, str, float]] = []
    for src, tgt, pred, conf_w in rows:
        if int(src) in member_set and int(tgt) in member_set:
            # Round confidence so float noise does not bust the cache.
            internal.append((int(src), int(tgt), str(pred), round(float(conf_w or 0.0), 6)))
    internal.sort()
    # Include the member-id set itself: if a member leaves/joins (even with no
    # edge change among the rest) the community's identity changed.
    payload = json.dumps({"members": ids, "edges": internal}, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ============================================================
# Detection (the public entry: Leiden -> GROUP BY -> conn-comp)
# ============================================================

def _label_for_members(conn: sqlite3.Connection, member_ids: Sequence[int]) -> Optional[str]:
    """A short human label for a community: its most common tag, else its most
    common source_project, else None. Pure read."""
    ids = sorted(set(int(m) for m in member_ids))
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    if _has_table(conn, "memory_tags"):
        row = conn.execute(
            f"""
            SELECT tag, COUNT(*) c FROM memory_tags
            WHERE memory_id IN ({placeholders})
            GROUP BY tag ORDER BY c DESC, tag ASC LIMIT 1
            """,
            ids,
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    row = conn.execute(
        f"""
        SELECT COALESCE(source_project, 'untagged') p, COUNT(*) c FROM memories
        WHERE id IN ({placeholders})
        GROUP BY p ORDER BY c DESC, p ASC LIMIT 1
        """,
        ids,
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def detect_communities(
    conn: sqlite3.Connection,
    cfg: Optional[Dict[str, Any]] = None,
    batch_id: Optional[int] = None,
    force_method: Optional[str] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Detect communities on the settled graph and (optionally) persist them.

    Method selection: Leiden if available -> else GROUP BY (if the graph has
    structure) -> else connected-components. ``force_method`` (one of
    'leiden' / 'group_by' / 'connected_components') overrides selection, used by
    tests to FORCE the absent path.

    Writes ONLY ``memory_communities`` + ``memory_community_members`` (a new
    epoch). NEVER ``memory_edges``. Carry-forward keeps community_ids stable.

    Returns ``{status, method, epoch, communities, num_communities, num_members,
               graph_nodes, graph_edges, reason?}`` where ``communities`` is
    ``[{community_id, size, label, members, member_edge_hash}, ...]``."""
    conf = {**DEFAULT_COMMUNITIES_CONFIG, **(cfg or {})}
    result: Dict[str, Any] = {
        "status": "ok",
        "method": None,
        "epoch": None,
        "communities": [],
        "num_communities": 0,
        "num_members": 0,
        "graph_nodes": 0,
        "graph_edges": 0,
    }

    if persist and not ensure_communities_migration(conn):
        result["status"] = "error"
        result["reason"] = "communities migration could not be applied"
        return result

    g = build_graph(conn, conf)
    result["graph_nodes"] = g.num_nodes()
    result["graph_edges"] = g.num_edges()

    min_size = int(conf["min_community_size"])

    # ---- choose + run a method -------------------------------------------------
    method = force_method
    partitions: List[List[int]] = []
    labels: Dict[int, str] = {}  # partition index -> label (GROUP BY supplies its own)

    if method is None:
        method = "leiden" if leiden_available() else "group_by"

    if method == "leiden":
        if not leiden_available():
            method = "group_by"  # forced leiden but unavailable -> graceful
        else:
            try:
                partitions = _leiden_partition(g, conf)
            except Exception as e:  # any igraph/leidenalg failure -> fall back
                logger.warning("Leiden failed (%s); falling back to GROUP BY", e)
                method = "group_by"

    if method == "group_by":
        # Align the GROUP BY population with the graph nodes when the graph has
        # any edges, so the two lenses describe the same memories; if the graph
        # is empty, GROUP BY still clusters the whole live corpus.
        restrict = set(g.nodes()) if g.num_nodes() > 0 else None
        gb = group_by_clusters(conn, min_size=min_size, restrict_to=restrict)
        if not gb and restrict is not None:
            # The graph nodes carried no tags -> drop the restriction so the
            # organ is never empty when there IS a corpus.
            gb = group_by_clusters(conn, min_size=min_size, restrict_to=None)
        for i, (label, members) in enumerate(gb):
            partitions.append(members)
            labels[i] = label

    if method == "connected_components":
        partitions = connected_components(g, min_size=min_size)

    # If a non-conn-comp method produced nothing usable but the graph DOES have
    # topology, the connected-components Tier-0 guarantees a non-empty result.
    if not partitions and g.num_edges() > 0:
        method = "connected_components"
        partitions = connected_components(g, min_size=min_size)

    # Enforce min size uniformly (Leiden/conn-comp may emit small clusters).
    partitions = [p for p in partitions if len(p) >= min_size]
    partitions.sort(key=lambda c: (-len(c), c[0] if c else 0))

    result["method"] = method
    if not partitions:
        result["reason"] = "no community of the minimum size on the settled graph"
        if persist:
            # Still open a (empty) epoch marker? No -- an epoch with zero
            # communities adds no information and would churn MAX(epoch). Skip.
            pass
        return result

    # ---- carry forward stable ids ---------------------------------------------
    prior_epoch, prior_members = _load_prior_partition(conn)
    id_floor = _max_community_id(conn)
    assignments = carry_forward_ids(
        partitions, prior_members, float(conf["carry_forward_jaccard"]), id_floor
    )
    new_epoch = (prior_epoch + 1) if prior_epoch is not None else 1

    communities_out: List[Dict[str, Any]] = []
    for idx, (cid, members) in enumerate(assignments):
        label = labels.get(idx) or _label_for_members(conn, members)
        meh = member_edge_hash(conn, members, conf)
        communities_out.append({
            "community_id": cid,
            "size": len(members),
            "label": label,
            "members": members,
            "member_edge_hash": meh,
        })

    result["epoch"] = new_epoch
    result["communities"] = communities_out
    result["num_communities"] = len(communities_out)
    result["num_members"] = sum(c["size"] for c in communities_out)

    # ---- persist (ONLY our two epoch tables) ----------------------------------
    if persist:
        _persist_partition(conn, new_epoch, method, communities_out, int(conf["seed"]), batch_id)

    return result


def _persist_partition(
    conn: sqlite3.Connection,
    epoch: int,
    method: str,
    communities: List[Dict[str, Any]],
    seed: int,
    batch_id: Optional[int],
) -> None:
    """Write a detected partition to ``memory_communities`` +
    ``memory_community_members`` for one epoch. Idempotent per epoch (INSERT OR
    REPLACE on the composite keys). Touches NO other table."""
    cur = conn.cursor()
    for c in communities:
        cur.execute(
            """INSERT OR REPLACE INTO memory_communities
               (community_id, epoch, method, size, label, member_edge_hash, seed, batch_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (c["community_id"], epoch, method, c["size"], c.get("label"),
             c.get("member_edge_hash"), seed, batch_id),
        )
        for mid in c["members"]:
            cur.execute(
                """INSERT OR REPLACE INTO memory_community_members
                   (community_id, epoch, memory_id) VALUES (?,?,?)""",
                (c["community_id"], epoch, int(mid)),
            )
    conn.commit()


# ============================================================
# Lazy summaries (content-hash-gated, single-flight)
# ============================================================

def _extractive_summary(conn: sqlite3.Connection, members: Sequence[int], cfg: Dict[str, Any]) -> str:
    """Cheap, deterministic summary: the community's label + its highest-
    importance member summaries. No LLM, no network -- the nightly default. The
    LLM (Stage-2) path would replace ONLY this function's body and is never
    invoked from the sleep loop."""
    ids = sorted(set(int(m) for m in members))
    cap = int(cfg.get("summary_member_cap", 40))
    if not ids:
        return ""
    label = _label_for_members(conn, ids) or "community"
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT id, COALESCE(summary, substr(COALESCE(content,''),1,80)) AS s,
               COALESCE(importance, 0.0) AS imp
        FROM memories
        WHERE id IN ({placeholders}) AND is_archived = 0
        ORDER BY imp DESC, id ASC
        LIMIT ?
        """,
        ids + [cap],
    ).fetchall()
    lines = []
    for r in rows:
        s = (r[1] or "").replace("\n", " ").strip()
        if s:
            lines.append(f"  - #{r[0]}: {s[:160]}")
    head = f"Theme '{label}' ({len(ids)} memories):"
    return head + "\n" + "\n".join(lines) if lines else head


def compute_summaries(
    conn: sqlite3.Connection,
    cfg: Optional[Dict[str, Any]] = None,
    epoch: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Lazily (re)generate ``community_summaries`` for the communities of an
    epoch (default: the latest). A summary regenerates ONLY when the content-hash
    of its members' currently-valid internal edges differs from the cached hash
    (single-flight: one row per stable community_id, updated in place).

    ``force=True`` regenerates regardless (used by --summaries CLI / tests).

    Returns ``{status, epoch, regenerated, skipped, total, reason?}``."""
    conf = {**DEFAULT_COMMUNITIES_CONFIG, **(cfg or {})}
    result: Dict[str, Any] = {
        "status": "ok", "epoch": epoch, "regenerated": 0, "skipped": 0, "total": 0,
    }
    if not ensure_communities_migration(conn):
        result["status"] = "error"
        result["reason"] = "communities migration could not be applied"
        return result

    if epoch is None:
        row = conn.execute("SELECT MAX(epoch) FROM memory_communities").fetchone()
        if not row or row[0] is None:
            result["reason"] = "no communities detected yet"
            return result
        epoch = int(row[0])
    result["epoch"] = epoch

    comm_rows = conn.execute(
        "SELECT community_id FROM memory_communities WHERE epoch = ? ORDER BY size DESC, community_id ASC",
        (epoch,),
    ).fetchall()
    result["total"] = len(comm_rows)

    for (cid,) in comm_rows:
        cid = int(cid)
        members = [
            int(m[0]) for m in conn.execute(
                "SELECT memory_id FROM memory_community_members WHERE epoch = ? AND community_id = ?",
                (epoch, cid),
            ).fetchall()
        ]
        new_hash = member_edge_hash(conn, members, conf)

        # SINGLE-FLIGHT cache check: skip if the cached summary matches the hash.
        cached = conn.execute(
            "SELECT member_edge_hash FROM community_summaries WHERE community_id = ?",
            (cid,),
        ).fetchone()
        if (not force) and cached is not None and cached[0] == new_hash:
            result["skipped"] += 1
            continue

        summary_text = _extractive_summary(conn, members, conf)
        conn.execute(
            """INSERT OR REPLACE INTO community_summaries
               (community_id, summary, member_edge_hash, summary_mode, member_count, generated_at)
               VALUES (?,?,?,?,?,datetime('now'))""",
            (cid, summary_text, new_hash, str(conf.get("summary_mode", "extractive")), len(members)),
        )
        result["regenerated"] += 1

    conn.commit()
    return result


# ============================================================
# Sleep-loop stage (fail-soft -- NEVER raises into the orchestrator)
# ============================================================

def run_communities_stage(
    conn: sqlite3.Connection,
    cfg: Optional[Dict[str, Any]] = None,
    batch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """The community-detection sleep-loop stage. FAIL-SOFT: this function NEVER
    raises into the orchestrator -- it returns a result dict with a ``status``
    key. The orchestrator's success/partial math is unaffected (this is
    visualization/observability, NOT one of the five core consolidation stages).

    Runs LAST (after extraction -> belief -> NREM-settle -> PageRank): detection
    on the settled graph, then lazy summaries. Gated by the
    ``VOIDLOOM_NO_COMMUNITIES`` env kill-switch (matches the family).

    Returns ``{status, method, num_communities, num_members, summaries_regenerated,
               summaries_skipped, graph_nodes, graph_edges, reason?}``."""
    result: Dict[str, Any] = {
        "status": "ok",
        "method": None,
        "num_communities": 0,
        "num_members": 0,
        "summaries_regenerated": 0,
        "summaries_skipped": 0,
        "graph_nodes": 0,
        "graph_edges": 0,
    }

    # Panic switch wins.
    if os.environ.get(ENV_KILL_SWITCH, "").strip():
        result["status"] = "skipped"
        result["reason"] = f"{ENV_KILL_SWITCH} set"
        return result

    try:
        conf = {**DEFAULT_COMMUNITIES_CONFIG, **(cfg or {})}

        if not ensure_communities_migration(conn):
            result["status"] = "skipped"
            result["reason"] = "communities migration could not be applied"
            return result

        det = detect_communities(conn, cfg=conf, batch_id=batch_id, persist=True)
        result["method"] = det.get("method")
        result["graph_nodes"] = det.get("graph_nodes", 0)
        result["graph_edges"] = det.get("graph_edges", 0)
        result["num_communities"] = det.get("num_communities", 0)
        result["num_members"] = det.get("num_members", 0)
        if det.get("status") == "error":
            result["status"] = "error"
            result["reason"] = det.get("reason")
            return result

        if det.get("num_communities", 0) > 0:
            summ = compute_summaries(conn, cfg=conf, epoch=det.get("epoch"))
            result["summaries_regenerated"] = summ.get("regenerated", 0)
            result["summaries_skipped"] = summ.get("skipped", 0)
            if summ.get("status") == "error":
                # Detection succeeded; summary failure is non-fatal observability.
                result["status"] = "partial"
                result["reason"] = f"summaries: {summ.get('reason')}"

        return result

    except Exception as e:
        # Fail-soft contract: log + report, NEVER propagate.
        logger.error("communities stage failed (soft): %s", e)
        result["status"] = "error"
        result["reason"] = str(e)
        return result


# ============================================================
# CLI
# ============================================================

def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _cli_detect(db_path: str, force_method: Optional[str]) -> None:
    conn = _open_db(db_path)
    try:
        res = detect_communities(conn, force_method=force_method, persist=True)
        print(json.dumps({k: v for k, v in res.items() if k != "communities"}, indent=2))
        for c in res.get("communities", [])[:20]:
            print(f"  community {c['community_id']} [{c['label']}] size={c['size']}: "
                  f"{c['members'][:8]}{'...' if c['size'] > 8 else ''}")
    finally:
        conn.close()


def _cli_summaries(db_path: str, force: bool) -> None:
    conn = _open_db(db_path)
    try:
        res = compute_summaries(conn, force=force)
        print(json.dumps(res, indent=2))
    finally:
        conn.close()


def _cli_stage(db_path: str) -> None:
    conn = _open_db(db_path)
    try:
        res = run_communities_stage(conn, batch_id=None)
        print(json.dumps(res, indent=2))
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="L5-E-COMM Leiden cognitive-map communities")
    parser.add_argument("--unit-tests", action="store_true", help="run the self-test suite")
    parser.add_argument("--detect", metavar="DB", help="detect communities on DB")
    parser.add_argument("--summaries", metavar="DB", help="(re)compute lazy summaries on DB")
    parser.add_argument("--stage", metavar="DB", help="run the sleep-loop stage on DB")
    parser.add_argument("--force-method", choices=["leiden", "group_by", "connected_components"],
                        default=None, help="force a detection method (for --detect)")
    parser.add_argument("--force", action="store_true", help="force summary regeneration")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.unit_tests:
        # Tests live either alongside this module or in a sibling tests/ dir
        # (the ACE-workspace layout). Search both so the module has no test-time
        # dependency regardless of how the tree is arranged.
        here = Path(__file__).resolve().parent
        for cand in (here, here / "tests", here.parent / "tests"):
            if (cand / "test_communities.py").exists():
                sys.path.insert(0, str(cand))
                break
        import test_communities  # type: ignore
        return test_communities.run_all()
    if args.detect:
        _cli_detect(args.detect, args.force_method)
        return 0
    if args.summaries:
        _cli_summaries(args.summaries, args.force)
        return 0
    if args.stage:
        _cli_stage(args.stage)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

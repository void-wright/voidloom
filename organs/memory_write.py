#!/usr/bin/env python3
"""memory_write.py — THE ONE GUARDED WAY TO WRITE A MEMORY.

WHY THIS EXISTS  (TO-03, round gamma)
    Thirteen live `INSERT INTO memories` sites across eight organs write rows
    that carry NO EMBEDDING. A memory without an embedding is invisible to every
    vector path in the brain: `hybrid_retrieval`'s vector arm, `recall_diversity`'s
    MMR cosine, `temporal_edges`' KNN, `sleep_loop`'s clustering, `belief_engine`'s
    cosine gate, `cross_tier_bridges`' discovery. It is stored and unfindable.

    The sites are not wrong in isolation; they are wrong because there is no ONE
    PLACE that knows the rule. This is that place.

THE GUARANTEE, and it is the whole point
    There is NO PATH THROUGH THIS FUNCTION THAT SILENTLY PRODUCES AN
    EMBEDDING-LESS MEMORY.

      embed_policy="require"     (default) tier resolved, vector obtained, WIDTH
                                 ASSERTED against the resolved dim, the memory,
                                 the vector AND ITS PROVENANCE STAMP (M1-C2)
                                 written inside ONE SAVEPOINT. Any failure raises
                                 and NOTHING is written.
      embed_policy="best_effort" the memory is written, `embedded=False` is
                                 returned, and the reason is logged at ERROR.
                                 Loud, never silent.
      embed_policy="skip"        REQUIRES an explicit `skip_reason`. The reason is
                                 stamped into the row's source_context so the
                                 decision is auditable from the data, not only
                                 from the code.

    There is no fourth option and no default that reaches the dormant table.

WHY THE WIDTH IS ASSERTED, NOT ASSUMED  (round beta, F-A1 / F-B1)
    beta's sleep_loop patch resolved (table, dim) and then discarded the dim.
    whisper_network encoded 768 floats and routed the INSERT to a float[1024]
    table; sqlite-vec raised; a bare `except sqlite3.OperationalError: pass`
    swallowed it, and the organ wrote nothing for 15 consecutive digests without
    one line of evidence. `assert_width` below is the check that would have
    caught it at authoring time. The resolver's own contract says table and dim
    TRAVEL TOGETHER; this module is where that contract is enforced.

WHY IT ALSO ENQUEUES FOR BELIEF SCAN  (TO-08)
    `belief_engine.enqueue_for_scan` has defs=1, calls=0. Its own module docstring
    says "store() calls enqueue_for_scan()". Nothing does. `belief_scan_queue` is
    therefore empty, so `belief_engine.propose` has no dirty set, so
    `record_provenance`'s only live-path caller (belief_engine:717) never fires.
    The store path is the correct binding site, and this is the store path.

    HONESTY ABOUT WHAT THIS DOES NOT FIX: filling the queue does not drain it.
    There is no scheduled invoker for `belief scan` anywhere in the tree (19
    ExecStart lines, zero mention of belief_engine or belief_cli). See
    `enqueue_health()` — it reports the backlog and whether anything has ever
    drained it, so the gap is visible in the data rather than only in this
    comment.

WHY IT IMPORTS NO GPU CODE
    It never imports torch, never pins CUDA, and never loads a model itself. When
    an encoder is needed it delegates to `claude_memory._encode`, whose module
    already carries the FORCED pin (`os.environ["CUDA_VISIBLE_DEVICES"] = "1"`,
    claude_memory.py:102 — the forced form, not the forbidden setdefault). Adding
    a second pinning surface would add a second thing to get wrong; #15708 is
    about exactly that.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("voidloom.memory_write")

_POLICIES = ("require", "best_effort", "skip")


class MemoryWriteError(RuntimeError):
    """Base for every refusal in this module. Loud beats a silent wrong row."""


class EmbeddingUnavailable(MemoryWriteError):
    """No vector could be produced and the policy said one was required."""


class EmbeddingWidthMismatch(MemoryWriteError):
    """A vector was produced at the wrong width for the resolved tier.

    This is F-B1's failure, promoted from a swallowed OperationalError to a
    refusal with both numbers in the message.
    """


@dataclass
class WriteResult:
    memory_id: int
    embedded: bool
    embed_table: Optional[str] = None
    embed_dim: Optional[int] = None
    embed_skipped_reason: Optional[str] = None
    enqueued_for_scan: bool = False
    provenance_ids: List[int] = field(default_factory=list)
    columns_written: List[str] = field(default_factory=list)
    provenance: Optional[Dict[str, Any]] = None   # M1-C2: the stamp row, as written

    def as_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "embedded": self.embedded,
            "embed_table": self.embed_table,
            "embed_dim": self.embed_dim,
            "embed_skipped_reason": self.embed_skipped_reason,
            "enqueued_for_scan": self.enqueued_for_scan,
            "provenance_ids": list(self.provenance_ids),
            "columns_written": list(self.columns_written),
            "provenance": dict(self.provenance) if self.provenance else None,
        }


# --------------------------------------------------------------------------
# Tier resolution — one import, one source of truth.
# --------------------------------------------------------------------------

def _resolve_tier(conn: sqlite3.Connection):
    """(table, dim) for this connection, or raise. Never a default."""
    import embed_resolver                      # landed round beta (commit 3a465a4)
    return embed_resolver.resolve(conn)


def assert_width(blob: bytes, dim: int) -> None:
    """Refuse a vector that is not the resolved tier's width.

    float32 => 4 bytes per component. A 768-float vector is 3072 bytes; the
    float[1024] table wants 4096. sqlite-vec's own error for this is
    catchable-and-swallowable; this one is not swallowed anywhere in the tree.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise EmbeddingWidthMismatch(
            f"embedding must be float32 bytes, got {type(blob).__name__}"
        )
    n = len(blob)
    if n % 4:
        raise EmbeddingWidthMismatch(
            f"embedding of {n} bytes is not a whole number of float32 components"
        )
    got = n // 4
    if got != dim:
        raise EmbeddingWidthMismatch(
            f"embedding width {got} != resolved tier width {dim} "
            f"({n} bytes vs {dim * 4}). Encoding and table must agree; "
            f"truncating or padding produces a vector that is not in the table's space."
        )


def default_encoder(text: str, conn: Optional[sqlite3.Connection] = None) -> bytes:
    """The canonical encoder, forced onto the CALLER'S tier.

    FOUND BY THIS MODULE'S OWN ASSERTION, in this round, in my own first draft:
    the first version delegated straight to `claude_memory._encode`, which asks
    `_active_embed_model()` which reads a MODULE-LEVEL `DB_PATH`. That is a
    different question from "which tier is the database this connection is
    attached to", and on a supervised daytime hand-run out of a staging directory
    the two answers differed -- the TABLE resolved to jina/1024 from the caller's
    connection while the ENCODER resolved to nomic/768 from a DB_PATH that did
    not exist. 28 playbook memories were written with no vector. The width
    assertion caught every one and tagged every one, which is exactly what it is
    for, but the right repair is upstream: ask ONE question, of ONE object.

    So when a connection is available, the tier is resolved FROM THAT CONNECTION
    and claude_memory's own documented per-process cache is set to match before
    the encode. Encoder and table cannot then disagree, by construction.

    Imported inside the call, not at module scope: `claude_memory` imports
    several of this module's own callers, and a module-scope import is circular.
    """
    import claude_memory                       # lazy: circular at module scope
    if conn is not None:
        table, dim = _resolve_tier(conn)
        # THE BIJECTION, NOT A BINARY. This read
        #     "jina" if table == "memory_embeddings_v2" else "nomic"
        # which is a TWO-tier answer to a THREE-tier question: it mapped
        # `memory_embeddings_v3` -- Qwen3-Embedding-0.6B, the tier the engine
        # itself calls "the SHIPPED tier" -- to NOMIC, and would have encoded
        # 768-wide vectors for a 1024-wide table. `assert_width` below catches
        # that, so nothing was ever corrupted; what it means is that a qwen-tier
        # brain could not embed AT ALL through this path, every write tagged
        # `embedding-missing`. Exactly the census-of-two-over-three shape found in
        # voidloom/_bootstrap the same week.
        #
        # claude_memory already owns the inverse map -- `_TABLE_TO_TIER`, built
        # FROM `EMBED_TIERS` and bijective on purpose ("one table per model, so
        # reverse-mapping a table name back to a model can never be ambiguous").
        # Asking it means a fourth tier needs no edit here.
        tier = getattr(claude_memory, "_TABLE_TO_TIER", {}).get(table)
        if tier is None:
            raise EmbeddingUnavailable(
                f"no embedding tier maps to table {table!r} (dim {dim}); "
                "claude_memory._TABLE_TO_TIER knows "
                f"{sorted(getattr(claude_memory, '_TABLE_TO_TIER', {}))}. Refusing "
                "to guess a model: same dimension is not the same space.")
        claude_memory._active_model_cache["model"] = tier
    return claude_memory._encode(text, "search_document")


# --------------------------------------------------------------------------
# The write.
# --------------------------------------------------------------------------

_CORE_COLUMNS = (
    "content", "summary", "category", "importance", "source_project",
    "source_context", "kind", "confidence", "derived_from", "is_archived",
    "created_at", "source_brain", "source_user", "encoding_surprise",
)


def write_memory(
    conn: sqlite3.Connection,
    *,
    content: str,
    summary: Optional[str] = None,
    category: Optional[str] = None,
    importance: Optional[float] = None,
    source_project: Optional[str] = None,
    source_context: Optional[str] = None,
    kind: Optional[str] = None,
    confidence: Optional[float] = None,
    derived_from: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    tags: Sequence[str] = (),
    embedding: Optional[bytes] = None,
    encoder: Optional[Callable[[str], bytes]] = None,
    embed_policy: str = "require",
    skip_reason: Optional[str] = None,
    embed_text: Optional[str] = None,
    enqueue_scan: bool = True,
    sources: Optional[Iterable[int]] = None,
    transformation: Optional[str] = None,
    agent: Optional[str] = None,
    provenance_confidence: float = 0.8,
    provenance_metadata: Optional[dict] = None,
) -> WriteResult:
    """Insert ONE memory, its embedding, its tags, its scan-queue entry and
    (optionally) its provenance edges. Does NOT commit — the caller owns the
    transaction, as every current call site already does.

    Raises rather than writing a row the brain cannot find.
    """
    if embed_policy not in _POLICIES:
        raise ValueError(f"embed_policy must be one of {_POLICIES}, got {embed_policy!r}")
    if embed_policy == "skip" and not (skip_reason and skip_reason.strip()):
        raise ValueError(
            "embed_policy='skip' requires an explicit skip_reason. An unexplained "
            "embedding-less memory is the defect this module exists to prevent."
        )
    if not content or not str(content).strip():
        raise ValueError("content must be a non-empty string")

    have = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if not have:
        raise MemoryWriteError("table `memories` not found on this connection")

    proposed: Dict[str, Any] = {
        "content": content, "summary": summary, "category": category,
        "importance": importance, "source_project": source_project,
        "source_context": source_context, "kind": kind, "confidence": confidence,
        "derived_from": derived_from,
    }
    if extra:
        proposed.update(extra)

    if embed_policy == "skip":
        note = f"embed_policy=skip: {skip_reason.strip()}"
        prior = proposed.get("source_context")
        proposed["source_context"] = f"{prior} | {note}" if prior else note

    cols, vals = [], []
    for k, v in proposed.items():
        if v is None:
            continue
        if k not in have:
            logger.warning("memory_write: dropping column %r (absent from this schema)", k)
            continue
        cols.append(k)
        vals.append(v)
    if "content" not in cols:
        raise MemoryWriteError("schema has no `content` column")

    # ---- the vector, BEFORE any write, so "require" can refuse for free ----
    table = dim = None
    vec: Optional[bytes] = None
    skipped: Optional[str] = None
    if embed_policy == "skip":
        skipped = skip_reason.strip()
    else:
        try:
            table, dim = _resolve_tier(conn)
            if embedding is not None:
                vec = bytes(embedding)
            else:
                _txt = embed_text if embed_text is not None else content
                got = encoder(_txt) if encoder else default_encoder(_txt, conn)
                if got is None:
                    raise EmbeddingUnavailable("encoder returned None")
                vec = bytes(got)
            assert_width(vec, dim)
        except Exception as e:
            if embed_policy == "require":
                raise EmbeddingUnavailable(
                    f"refusing to write an unfindable memory: {type(e).__name__}: {e}"
                ) from e
            # best_effort: LOUD, never silent. This is the branch that replaces
            # `except sqlite3.OperationalError: pass`.
            logger.error(
                "memory_write: embedding FAILED (%s: %s); writing the memory "
                "WITHOUT it under embed_policy='best_effort'. This memory will be "
                "invisible to every vector path until it is back-filled.",
                type(e).__name__, e,
            )
            vec, skipped = None, f"{type(e).__name__}: {e}"

    sp = "memory_write_sp"
    stamp: Optional[Dict[str, Any]] = None
    conn.execute(f"SAVEPOINT {sp}")
    try:
        cur = conn.execute(
            f"INSERT INTO memories ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(vals))})",
            vals,
        )
        memory_id = cur.lastrowid
        if vec is not None:
            # M1-C2: the vector never lands without its stamp. Same class as
            # attach_embedding's write; one helper, one savepoint, both or neither.
            _txt = embed_text if embed_text is not None else content
            stamp = _insert_vector_and_stamp(conn, memory_id, table, dim, vec, _txt,
                                             "memory_write.write_memory")
        for tag in tags:
            tag = (tag or "").strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (memory_id, tag),
                )
    except Exception:
        conn.execute(f"ROLLBACK TO {sp}")
        conn.execute(f"RELEASE {sp}")
        raise
    conn.execute(f"RELEASE {sp}")

    res = WriteResult(
        memory_id=memory_id,
        embedded=vec is not None,
        embed_table=table if vec is not None else None,
        embed_dim=dim if vec is not None else None,
        embed_skipped_reason=skipped,
        columns_written=cols,
        provenance=stamp,
    )

    # ---- TO-08: the store-path hook belief_engine's own docstring promises ----
    if enqueue_scan:
        res.enqueued_for_scan = _enqueue(conn, memory_id)

    # ---- TO-09: provenance, when the caller knows its sources ----
    if sources and transformation:
        res.provenance_ids = _record_provenance(
            conn, memory_id, list(sources), transformation, agent,
            provenance_confidence, provenance_metadata,
        )
    return res


def _enqueue(conn: sqlite3.Connection, memory_id: int) -> bool:
    try:
        import belief_engine
    except Exception as e:
        logger.debug("memory_write: belief_engine unavailable (%s)", e)
        return False
    try:
        return bool(belief_engine.enqueue_for_scan(conn, memory_id))
    except Exception as e:
        logger.debug("memory_write: enqueue_for_scan failed (%s)", e)
        return False


def _record_provenance(conn, derived_id, source_ids, transformation,
                       agent, confidence, metadata) -> List[int]:
    """Best-effort, never fatal — provenance is an audit trail, not a gate.

    Every failure is logged at WARNING with the reason, so a chronically silent
    provenance graph shows up in the journal instead of only in a COUNT(*).
    """
    src = [int(s) for s in source_ids if s is not None]
    if not src:
        return []
    try:
        import provenance_graph
    except Exception as e:
        logger.warning("memory_write: provenance_graph unavailable (%s)", e)
        return []
    try:
        return list(provenance_graph.record_provenance(
            conn, derived_memory_id=derived_id, source_memory_ids=src,
            transformation_type=transformation, agent=agent,
            confidence=confidence, metadata=metadata,
        ))
    except Exception as e:
        logger.warning("memory_write: provenance not recorded (%s: %s)", type(e).__name__, e)
        return []


def enqueue_health(conn: sqlite3.Connection) -> Dict[str, Any]:
    """What the belief scan queue actually looks like — reported, not assumed.

    Returns UNMEASURABLE rather than a zero when the table cannot be read, so a
    missing substrate never reads as an empty queue. (0,True) and (0,False) are
    different answers and this returns different answers for them.
    """
    out: Dict[str, Any] = {"queued": None, "drained": None, "state": "UNMEASURABLE"}
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN processed_at IS NULL THEN 1 ELSE 0 END), "
            "       COUNT(processed_at) "
            "FROM belief_scan_queue"
        ).fetchone()
    except sqlite3.Error as e:
        out["detail"] = f"belief_scan_queue unreadable: {e}"
        return out
    total, pending, drained = row[0] or 0, row[1] or 0, row[2] or 0
    out.update(queued=total, pending=pending, drained=drained)
    if total == 0:
        out["state"] = "EMPTY"
        out["detail"] = "queue is empty — nothing has been enqueued"
    elif drained == 0:
        out["state"] = "NEVER_DRAINED"
        out["detail"] = (f"{pending} rows enqueued, 0 ever processed. There is no "
                         f"scheduled invoker for `belief scan` in this tree; "
                         f"enqueueing fills a queue nothing empties.")
    else:
        out["state"] = "DRAINING"
        out["detail"] = f"{drained} processed, {pending} pending"
    return out


# --------------------------------------------------------------------------
# The RETROFIT half: attach an embedding to a memory an organ has just written.
#
# WHY A SECOND ENTRY POINT
#     Eleven live sites build their INSERT with columns this helper does not
#     model -- `updated_at`, `last_accessed`, `source_brain`, `source_user`, and
#     two anti-laundering taint stamps applied AFTER the insert. Rewriting them
#     into write_memory() would be a large diff whose failure mode is a silently
#     dropped column, which is the same shape of defect as the one being fixed.
#     attach_embedding() is three lines at each site and drops nothing.
#
# WHY IT DOES NOT RAISE BY DEFAULT
#     Every one of the eleven sites sits inside `except Exception:` -- and in
#     dream_pipeline that handler logs at DEBUG and returns None. Raising into a
#     swallowing handler converts a loud failure into a silent one, which is the
#     exact anti-pattern this round is repairing in dream_pipeline's exit code.
#     So the default is best_effort, and best_effort here means THREE things:
#         1. an ERROR line in the journal,
#         2. a tag on the row, so the failure is queryable from the DATA:
#              SELECT memory_id FROM memory_tags WHERE tag='embedding-missing'
#         3. a returned status the caller may report.
#     A journal line alone is not enough: nothing greps the journal in three
#     months, and something does query the brain every night.
# --------------------------------------------------------------------------

MISSING_TAG = "embedding-missing"


def attach_embedding(
    conn: sqlite3.Connection,
    memory_id: int,
    text: str,
    *,
    embedding: Optional[bytes] = None,
    encoder: Optional[Callable[[str], bytes]] = None,
    policy: str = "best_effort",
    organ: str = "?",
) -> Dict[str, Any]:
    """Resolve the tier, obtain a vector, ASSERT ITS WIDTH, and write it.

    Returns {"embedded": bool, "table": str|None, "dim": int|None, "error": str|None}.
    With policy="require" it raises EmbeddingUnavailable instead of returning
    embedded=False.
    """
    if policy not in ("require", "best_effort"):
        raise ValueError(f"policy must be 'require' or 'best_effort', got {policy!r}")
    out: Dict[str, Any] = {"embedded": False, "table": None, "dim": None, "error": None}
    try:
        table, dim = _resolve_tier(conn)
        out["table"], out["dim"] = table, dim
        vec = bytes(embedding) if embedding is not None else None
        if vec is None:
            got = encoder(text) if encoder else default_encoder(text, conn)
            if got is None:
                raise EmbeddingUnavailable("encoder returned None")
            vec = bytes(got)
        assert_width(vec, dim)
        # ── PER-ROW VECTOR PROVENANCE: the stamp, in the same savepoint as the vector ──
        #
        # Before M1-C2 this row recorded WHICH memory and WHAT vector, and nothing
        # about WHICH MODEL produced it. The producing model lived in ONE MUTABLE
        # metadata row (`active_embedding_model`), so after any tier change the
        # table could not say which of its rows predate the change. `embed_resolver`
        # refused to guess a TABLE; nothing refused to guess a row's PRODUCER.
        #
        # The trap, kept beside the write because that is where it bites:
        #
        #   ⚠ THE PROVENANCE COLUMNS HAVE NO `DEFAULT`. EVER.
        #     A DEFAULT stamps every existing row identically -- including the
        #     ~3,451 active memories that have no vector at all and the rows
        #     written before the tier moved on 2026-05-28 -- so the column would
        #     assert a producer for rows nobody re-embedded, and the migration
        #     would look complete the instant it ran. NULL means UNKNOWN, and
        #     UNKNOWN IS NOT ZERO: a backfill must be able to tell "never stamped"
        #     from "stamped as jina", and a DEFAULT destroys exactly that
        #     distinction at the moment it is created.
        #
        # `_insert_vector_and_stamp` writes both rows or neither; every stamp
        # column is supplied by THIS call (see migration_033 for the DDL).
        out["provenance"] = _insert_vector_and_stamp(
            conn, memory_id, table, dim, vec, text, organ)
        out["embedded"] = True
        # A previous attempt may have tagged it; clear the tag on success.
        try:
            conn.execute("DELETE FROM memory_tags WHERE memory_id=? AND tag=?",
                         (memory_id, MISSING_TAG))
        except sqlite3.Error:
            pass
        return out
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        out["error"] = detail
        if policy == "require":
            raise EmbeddingUnavailable(
                f"{organ}: refusing to leave memory {memory_id} unfindable: {detail}"
            ) from e
        logger.error(
            "attach_embedding[%s]: memory %s HAS NO EMBEDDING (%s). It is invisible "
            "to every vector path. Tagged %r so it is findable by SQL.",
            organ, memory_id, detail, MISSING_TAG,
        )
        try:
            conn.execute(
                "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (memory_id, MISSING_TAG),
            )
        except sqlite3.Error as te:
            logger.error("attach_embedding[%s]: could not even tag memory %s (%s)",
                         organ, memory_id, te)
        return out


def unfindable_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    """How many memories carry the missing-embedding mark, and since when.

    UNMEASURABLE rather than 0 when the tag table cannot be read -- (0,True) and
    (0,False) get different answers here, deliberately.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(m.created_at), MAX(m.created_at) "
            "FROM memory_tags t JOIN memories m ON m.id = t.memory_id "
            "WHERE t.tag = ?", (MISSING_TAG,)
        ).fetchone()
    except sqlite3.Error as e:
        return {"state": "UNMEASURABLE", "detail": f"memory_tags unreadable: {e}"}
    n = row[0] or 0
    return {"state": "CLEAN" if n == 0 else "UNFINDABLE_PRESENT",
            "count": n, "oldest": row[1], "newest": row[2],
            "detail": (f"{n} memories tagged {MISSING_TAG!r}"
                       if n else "no memory carries the missing-embedding mark")}


# ==========================================================================
# PER-ROW VECTOR PROVENANCE.
#
# THE STAMP is written by the embed call itself, in the SAME savepoint as the
# vector, by this module alone. THE READER derives its filter from the tier
# bijection (claude_memory.EMBED_TIERS via _TABLE_TO_TIER) on every call, REFUSES
# with a typed error when no rows match, and reports coverage on every call —
# success or refusal. DDL lives in migrations/migration_033_embedding_provenance.sql
# and is read from there, so it has exactly one home. No column has a DEFAULT.
#
# WHY THE TABLE IS ALSO ENSURED AT WRITE TIME
#     `cmd_init` applies every migration through schema_migrator, so a FRESH brain
#     has the sidecar. A brain that predates this migration and is upgraded by
#     replacing the tree (which is how the live tree is deployed: 16 systemd units
#     ExecStart into organs/) would otherwise fail every embed write until someone
#     remembered to run `schema_migrator apply`. Ensuring here is idempotent
#     (IF NOT EXISTS) and costs one file read per process.
#
# ⚠ NEVER executescript() ON THIS PATH. Python's sqlite3 documents that
#     executescript "first issues a COMMIT". Inside attach_embedding's SAVEPOINT
#     that COMMITS THE VECTOR and destroys the savepoint before the stamp lands —
#     the pair that claims to be atomic commits its first half. Found by the
#     provisional draft's own test on its second run (P-list 2). Statements are
#     executed one at a time; that opens no transaction and commits nothing.
# ==========================================================================

PROVENANCE_TABLE = "embedding_provenance"
PROVENANCE_COLUMNS = ("memory_id", "vec_table", "model_id", "model_ref",
                      "dim", "content_hash", "embedded_at", "organ")
MODEL_REF_UNRESOLVED = "unresolved"
_PROVENANCE_MIGRATION = (Path(__file__).resolve().parent.parent / "migrations"
                         / "migration_033_embedding_provenance.sql")
_provenance_stmt_cache: Dict[str, List[str]] = {}


class ProvenanceError(MemoryWriteError):
    """Base for the provenance reader's refusals."""


class ProvenanceUnreadable(ProvenanceError):
    """The sidecar or the vector table cannot be READ on this connection.

    This is 'cannot measure', which is a different answer from 'measured zero'.
    A brain without vec0 loaded, or without the migration applied, gets THIS —
    never a coverage of 0/N that a caller might read as 'nothing is stamped'.
    """


class ProvenanceUnmatched(ProvenanceError):
    """Measured, and ZERO rows are stamped under the active tier's model.

    The typed refusal M1-C2 requires: a reader that filtered on this tier would
    be searching nothing, or searching another model's space. `.coverage` carries
    the full report so the refusal still reports coverage.
    """

    def __init__(self, message: str, coverage: "ProvenanceCoverage"):
        super().__init__(message)
        self.coverage = coverage


def _provenance_statements() -> List[str]:
    """The migration's statements, split by sqlite3.complete_statement, cached.

    Comment-only text is dropped; a comment line that introduces a statement
    stays attached to it (the same rule schema_migrator.iter_statements applies,
    re-implemented here rather than imported so this module's import surface
    does not widen).
    """
    key = str(_PROVENANCE_MIGRATION)
    if key in _provenance_stmt_cache:
        return _provenance_stmt_cache[key]
    try:
        sql = _PROVENANCE_MIGRATION.read_text(encoding="utf-8")
    except OSError as e:
        raise ProvenanceUnreadable(
            f"provenance migration absent at {_PROVENANCE_MIGRATION} ({e}). "
            "The package is built from `git ls-files`; an untracked migration is "
            "absent from every install.") from e

    def has_sql(text: str) -> bool:
        return any(l.strip() and not l.strip().startswith("--")
                   for l in text.splitlines())

    out: List[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if has_sql(buf):
                out.append(buf.strip())
            buf = ""
    if buf.strip() and has_sql(buf):
        out.append(buf.strip())
    if not out:
        raise ProvenanceUnreadable(f"{_PROVENANCE_MIGRATION} parsed to zero statements")
    _provenance_stmt_cache[key] = out
    return out


def ensure_provenance_table(conn: sqlite3.Connection) -> None:
    """Idempotent. One statement at a time. NEVER executescript (see header)."""
    for stmt in _provenance_statements():
        conn.execute(stmt)


def content_hash(text: str) -> str:
    """sha256 hex of the embedded text, exactly as it was handed to the encoder."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _model_ref_for(tier: str, model_id: str) -> Tuple[str, str]:
    """(model_ref, source) — the identity of the WEIGHTS for a tier.

    1. 'pinned'        the revision the code pins for that tier
                       (embedding_upgrade.<TIER>_REVISION). jina and nomic pin
                       one (Voidloom #176, the RCE seal). Qwen does not, today.
    2. 'hf-refs-main'  the commit `main` resolved to when the weights were
                       fetched: <HF hub>/models--<id>/refs/main. Deterministic
                       (the file, not an mtime guess over snapshots/).
    3. 'unresolved'    the literal MODEL_REF_UNRESOLVED. A VALUE the write
                       supplies; a row carrying it is stamped-but-unpinned,
                       which is a different fact from never-stamped.
    """
    try:
        import embedding_upgrade                   # stdlib-only at module scope
        pin = getattr(embedding_upgrade, f"{tier.upper()}_REVISION", None)
        if pin and str(pin).strip():
            return str(pin).strip(), "pinned"
    except Exception as e:                         # an import failure is not a pin
        logger.debug("memory_write: no code pin for tier %r (%s)", tier, e)
    hub = (Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
           / "hub" / ("models--" + model_id.replace("/", "--")))
    try:
        ref = (hub / "refs" / "main").read_text(encoding="utf-8").strip()
        if ref:
            return ref, "hf-refs-main"
    except OSError:
        pass
    return MODEL_REF_UNRESOLVED, "unresolved"


def model_identity(table: str) -> Dict[str, str]:
    """{tier, model_id, model_ref, ref_source} for the tier that owns `table`.

    DERIVED FROM THE BIJECTION, never a literal: claude_memory._TABLE_TO_TIER
    reverse-maps the table to its tier and EMBED_TIERS names the model. A fourth
    tier needs no edit here. A table the bijection does not name is REFUSED —
    same dimension is not the same space, so guessing a producer is the exact
    lie the stamp exists to make impossible.
    """
    import claude_memory                           # lazy: circular at module scope
    tier = getattr(claude_memory, "_TABLE_TO_TIER", {}).get(table)
    if tier is None:
        raise EmbeddingUnavailable(
            f"no embedding tier maps to table {table!r}; claude_memory._TABLE_TO_TIER "
            f"knows {sorted(getattr(claude_memory, '_TABLE_TO_TIER', {}))}. Refusing "
            "to stamp a producer the bijection does not name.")
    model_id = claude_memory.EMBED_TIERS[tier][2]
    ref, source = _model_ref_for(tier, model_id)
    return {"tier": tier, "model_id": model_id, "model_ref": ref, "ref_source": source}


def _stamp_provenance(conn: sqlite3.Connection, memory_id: int, table: str, dim: int,
                      text: str, organ: str) -> Dict[str, Any]:
    """Write the stamp row. EVERY column supplied here; NONE defaulted.

    Must be called inside the same savepoint/transaction as the vector INSERT it
    describes; it does not open or close one. INSERT OR REPLACE because the vec0
    table's key is memory_id: a re-embed that replaced the vector replaces the
    stamp, and there is never a stamp describing a vector that is gone.
    """
    ensure_provenance_table(conn)
    ident = model_identity(table)
    row = {
        "memory_id": int(memory_id), "vec_table": table,
        "model_id": ident["model_id"], "model_ref": ident["model_ref"],
        "dim": int(dim), "content_hash": content_hash(text),
        "embedded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "organ": organ or "?",
    }
    conn.execute(
        f"INSERT OR REPLACE INTO {PROVENANCE_TABLE} ({', '.join(PROVENANCE_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in PROVENANCE_COLUMNS)})",
        row,
    )
    return {**row, "ref_source": ident["ref_source"], "tier": ident["tier"]}


def _insert_vector_and_stamp(conn: sqlite3.Connection, memory_id: int, table: str,
                             dim: int, vec: bytes, text: str, organ: str) -> Dict[str, Any]:
    """The vector and its stamp, as ONE UNIT, in one SAVEPOINT.

    Neither write_memory nor attach_embedding owns the caller's transaction, so a
    raise between the two INSERTs would let a committing caller persist a vector
    with no stamp — the exact state the stamp exists to make impossible. A
    SAVEPOINT makes the pair atomic without taking over the caller's transaction.
    (Found by the Qwen backfill test on the draft's first run: the migration file
    was untracked, the ensure raised, and the vector had already landed.)
    """
    sp = "vec_and_stamp"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        conn.execute(
            f"INSERT INTO {table} (memory_id, embedding) VALUES (?, ?)",
            (memory_id, vec),
        )
        stamp = _stamp_provenance(conn, memory_id, table, dim, text, organ)
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    return stamp


def stamp_after_vector_insert(conn: sqlite3.Connection, memory_id: int, text: str,
                              *, organ: str) -> Dict[str, Any]:
    """For a caller that has ALREADY inserted the vector inside its own open
    transaction (claude_memory's cmd_store / cmd_update / cmd_migrate) and will
    commit or roll back the whole thing itself. Resolves the tier from the
    connection so the stamp names the table the caller just wrote.

    The atomicity here is the caller's transaction: a raise propagates, the
    caller does not commit, and memory + vector + stamp are all absent together.
    """
    table, dim = _resolve_tier(conn)
    return _stamp_provenance(conn, memory_id, table, dim, text, organ)


# --------------------------------------------------------------------------
# THE READER. Filter from the bijection, refuse on zero match, coverage always.
# --------------------------------------------------------------------------

@dataclass
class ProvenanceCoverage:
    """What the sidecar says about this brain, under the ACTIVE tier's filter.

    covered           rows with a vector in vec_table AND a stamp naming
                      (vec_table, model_id). This is the set a reader may search.
    stamped_foreign   stamps on vec_table under a DIFFERENT model_id: the mixed
                      population, wearing a label. Same width, different space.
    stamped_without_vector
                      the DEFAULT-style defect: a producer asserted for a row
                      nobody embedded.
    vectors_without_stamp
                      the pre-M1-C2 state: bytes with no producer on record.
    stale_content_hash
                      covered rows whose stamp hash != sha256(memories.content).
                      Exact when the embedded text was the content (the backfill
                      and every retrofit site pass content); an upper bound if a
                      caller embedded something else.
    coverage_of_active   covered / active memories (Amendment 3: the denominator
                         is ALL active memories). Exact rational.
    coverage_of_vectors  covered / rows in vec_table. Exact rational.
    """
    vec_table: str
    model_id: str
    model_ref_expected: str
    dim: int
    denominator: str
    active_memories: int
    vectors: int
    covered: int
    stamped_foreign: int
    stamped_without_vector: int
    vectors_without_stamp: int
    stale_content_hash: int

    @property
    def coverage_of_active(self) -> Fraction:
        return Fraction(self.covered, self.active_memories) if self.active_memories else Fraction(0)

    @property
    def coverage_of_vectors(self) -> Fraction:
        return Fraction(self.covered, self.vectors) if self.vectors else Fraction(0)

    @property
    def defects(self) -> int:
        return self.stamped_foreign + self.stamped_without_vector + self.vectors_without_stamp

    @property
    def clean(self) -> bool:
        return self.defects == 0

    def as_dict(self) -> Dict[str, Any]:
        # RAW counts, never the reduced ratio: Fraction(5, 10) prints as 1/2, and a
        # reader of "1 of 2" would believe the population is two. num/den are the
        # figures as counted; value is the float. (Caught by this module's own test.)
        def frac(num: int, den: int) -> Dict[str, Any]:
            return {"num": num, "den": den, "value": (num / den) if den else 0.0}
        return {
            "vec_table": self.vec_table, "model_id": self.model_id,
            "model_ref_expected": self.model_ref_expected, "dim": self.dim,
            "denominator": self.denominator,
            "active_memories": self.active_memories, "vectors": self.vectors,
            "covered": self.covered,
            "stamped_foreign": self.stamped_foreign,
            "stamped_without_vector": self.stamped_without_vector,
            "vectors_without_stamp": self.vectors_without_stamp,
            "stale_content_hash": self.stale_content_hash,
            "coverage_of_active": frac(self.covered, self.active_memories),
            "coverage_of_vectors": frac(self.covered, self.vectors),
            "defects": self.defects, "clean": self.clean,
        }


def provenance_filter(conn: sqlite3.Connection) -> Tuple[str, str, int]:
    """(vec_table, model_id, dim) a reader must filter on, derived ON THIS CALL
    from the connection's tier and the bijection. No cache, no view, no literal.
    Raises EmbedTierUnresolved (unknown/absent tier) or EmbeddingUnavailable
    (a table the bijection does not name) — both typed, neither a default.
    """
    table, dim = _resolve_tier(conn)
    ident = model_identity(table)
    return table, ident["model_id"], dim


def provenance_coverage(conn: sqlite3.Connection) -> ProvenanceCoverage:
    """Measure the sidecar under the active tier's filter. ALWAYS returns
    coverage, on every call; RAISES ProvenanceUnmatched (carrying the same
    coverage) when zero rows are covered, so no caller can search an index this
    reader has not vouched for. There is no fallback path.
    """
    table, model_id, dim = provenance_filter(conn)
    model_ref_expected = model_identity(table)["model_ref"]

    try:
        conn.execute(f"SELECT 1 FROM {PROVENANCE_TABLE} LIMIT 0")
    except sqlite3.Error as e:
        raise ProvenanceUnreadable(
            f"{PROVENANCE_TABLE} is not readable on this connection ({e}); apply "
            "migrations/migration_033_embedding_provenance.sql. This is UNMEASURED, "
            "not zero.") from e
    try:
        vectors = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0
    except sqlite3.Error as e:
        raise ProvenanceUnreadable(
            f"vector table {table!r} is not readable on this connection ({e}). If the "
            "message is 'no such module: vec0' the extension is not loaded; that reads "
            "like absence and is not.") from e

    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "is_archived" in cols:
        denominator = "memories WHERE is_archived = 0"
        active = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0").fetchone()[0]
    else:
        denominator = "memories (schema has no is_archived column)"
        active = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    covered = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p "
        f"WHERE p.vec_table = ? AND p.model_id = ? "
        f"  AND EXISTS (SELECT 1 FROM {table} v WHERE v.memory_id = p.memory_id)",
        (table, model_id)).fetchone()[0]
    foreign = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? AND p.model_id <> ?",
        (table, model_id)).fetchone()[0]
    stamp_no_vec = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? "
        f"  AND NOT EXISTS (SELECT 1 FROM {table} v WHERE v.memory_id = p.memory_id)",
        (table,)).fetchone()[0]
    vec_no_stamp = conn.execute(
        f"SELECT COUNT(*) FROM {table} v WHERE NOT EXISTS "
        f"  (SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = v.memory_id AND p.vec_table = ?)",
        (table,)).fetchone()[0]

    conn.create_function("vl_sha256", 1, content_hash, deterministic=True)
    stale = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p JOIN memories m ON m.id = p.memory_id "
        f"WHERE p.vec_table = ? AND p.model_id = ? "
        f"  AND EXISTS (SELECT 1 FROM {table} v WHERE v.memory_id = p.memory_id) "
        f"  AND p.content_hash <> vl_sha256(m.content)",
        (table, model_id)).fetchone()[0]

    cov = ProvenanceCoverage(
        vec_table=table, model_id=model_id, model_ref_expected=model_ref_expected,
        dim=dim, denominator=denominator, active_memories=int(active),
        vectors=int(vectors), covered=int(covered), stamped_foreign=int(foreign),
        stamped_without_vector=int(stamp_no_vec), vectors_without_stamp=int(vec_no_stamp),
        stale_content_hash=int(stale),
    )
    if cov.covered == 0:
        raise ProvenanceUnmatched(
            f"REFUSED: zero rows in {table!r} are stamped under {model_id!r}. "
            f"{cov.vectors} vector(s) present, {cov.vectors_without_stamp} unstamped, "
            f"{cov.stamped_foreign} stamped under another model. A reader filtering on "
            f"this tier would search nothing, or another model's space. Coverage "
            f"{cov.coverage_of_active.numerator}/{cov.coverage_of_active.denominator} "
            f"of {denominator}.", cov)
    return cov


# --------------------------------------------------------------------------
# THE POST-WRITE HOOK — one call an organ makes after it has written a memory.
#
# It is deliberately ONE function rather than three, because the failure this
# round is repairing is not "the embedding was forgotten"; it is "there was no
# one place that knew what a finished memory owes the brain". A finished memory
# owes three things: a vector (or it is unfindable), a scan-queue entry (or the
# belief engine never sees it), and — when the organ knows where it came from —
# a provenance edge (or the derivation is unauditable).
# --------------------------------------------------------------------------

def on_memory_written(
    conn: sqlite3.Connection,
    memory_id: int,
    text: str,
    *,
    organ: str = "?",
    embedding: Optional[bytes] = None,
    encoder: Optional[Callable[[str], bytes]] = None,
    policy: str = "best_effort",
    attach: bool = True,
    enqueue_scan: bool = True,
    sources: Optional[Iterable[int]] = None,
    transformation: Optional[str] = None,
    agent: Optional[str] = None,
    provenance_metadata: Optional[dict] = None,
) -> Dict[str, Any]:
    """Everything a freshly written memory owes the brain, in one call.

    `attach=False` is for the ONE organ that already writes its own vector --
    sleep_loop.consolidate_episodics computes a centroid and inserts it itself.
    Attaching a second vector for the same memory_id would be a duplicate row in
    a vec0 table, so the hook must be able to say "the vector is already handled;
    do the other two things". `embedded` comes back None, which is neither True
    nor False, because "not this call's job" is a third answer.
    """
    if not attach:
        out: Dict[str, Any] = {"embedded": None, "table": None, "dim": None,
                               "error": None, "attach_skipped": "caller writes its own vector"}
    else:
        out = attach_embedding(conn, memory_id, text, embedding=embedding,
                               encoder=encoder, policy=policy, organ=organ)
    out["enqueued_for_scan"] = _enqueue(conn, memory_id) if enqueue_scan else False
    out["provenance_ids"] = []
    if sources and transformation:
        out["provenance_ids"] = _record_provenance(
            conn, memory_id, list(sources), transformation,
            agent or organ.split(".")[0], 0.8, provenance_metadata,
        )
    return out

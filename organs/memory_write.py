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
from datetime import datetime, timedelta, timezone
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
    """The canonical encoder, forced onto the CALLER'S brain.

    FOUND BY THIS MODULE'S OWN ASSERTION, in a first draft:
    the first version delegated straight to `claude_memory._encode`, which asks
    `_active_embed_model()` about a MODULE-LEVEL `DB_PATH`. That is a different
    question from "which tier is the database this connection is attached to",
    and on a supervised daytime hand-run out of a staging directory the two
    answers differed -- 28 playbook memories were written with no vector. The
    width assertion caught every one, but a second brain of the SAME width would
    pass it, so the repair is upstream: ask ONE question, of ONE object.

    So when a connection is available the brain beside THAT connection decides: its
    install lock, or for a brain that predates the lock the shipped catalogue entry
    of the tier it already uses (`embedding_upgrade.brain_lock`), is handed to
    `_encode`, which hands it to the one loader; the loader refuses (WEIGHTS_MISMATCH)
    if a different model is already resident in this process. A tier the catalogue
    does not ship (jina) has no lock path and uses its own encoder. Encoder and table
    cannot then disagree, by construction, and no process-level cache is set.

    The tier comes from `_TABLE_TO_TIER`, built from `EMBED_TIERS` and bijective on
    purpose: a binary here once mapped the qwen table to nomic, and one table per
    model means a fourth tier needs no edit here.

    Imported inside the call, not at module scope: `claude_memory` imports
    several of this module's own callers, and a module-scope import is circular.
    """
    import claude_memory                       # lazy: circular at module scope
    if conn is None:
        return claude_memory._encode(text, "search_document")
    table, dim = _resolve_tier(conn)
    tier = getattr(claude_memory, "_TABLE_TO_TIER", {}).get(table)
    if tier is None:
        raise EmbeddingUnavailable(
            f"no embedding tier maps to table {table!r} (dim {dim}); "
            "claude_memory._TABLE_TO_TIER knows "
            f"{sorted(getattr(claude_memory, '_TABLE_TO_TIER', {}))}. Refusing "
            "to guess a model: same dimension is not the same space.")
    import embedding_upgrade                   # lazy: it imports this module's callers
    if tier == "jina":
        return embedding_upgrade.encode_jina_document(text)
    return claude_memory._encode(text, "search_document",
                                 lock=embedding_upgrade.brain_lock(conn, tier=tier))


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
        # refused to guess a TABLE; nothing refused to guess a row's PRODUCER. Now the
        # stamp carries the checksum the embedder COMPUTED from the bytes it loaded.
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
        #     from "stamped under some checksum", and a DEFAULT destroys exactly that
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
# vector, by this module alone, and it carries the checksum THE EMBEDDER COMPUTED
# from the bytes it loaded (embedder_lock.require_loaded_hash): never a schema
# DEFAULT, never the lock's string copied over. THE READER opens the install lock
# beside the connection's database on every call and filters on its `weights_hash`
# (a brain with no lock falls back to the tier bijection and its whole table), REFUSES
# with a typed error when no rows match, and reports coverage on every call —
# success or refusal. DDL lives in migrations/migration_033_* (the sidecar) and
# migration_042_* (the checksum, the input kind) and is read from there, so it has
# exactly one home. No column has a DEFAULT.
#
# WHY THE TABLE IS ALSO ENSURED AT WRITE TIME
#     `cmd_init` applies every migration through schema_migrator, so a FRESH brain
#     has the sidecar. A brain that predates a migration and is upgraded by
#     replacing the tree (which is how the live tree is deployed: 16 systemd units
#     ExecStart into organs/) would otherwise fail every embed write until someone
#     remembered to run `schema_migrator apply`. Ensuring here is shape-driven,
#     statement-at-a-time and inside one savepoint.
#
# ⚠ NEVER executescript() ON THIS PATH. Python's sqlite3 documents that
#     executescript "first issues a COMMIT". Inside _insert_vector_and_stamp's
#     SAVEPOINT that COMMITS THE VECTOR and destroys the savepoint before the stamp lands —
#     the pair that claims to be atomic commits its first half. Found by the
#     provisional draft's own test on its second run (P-list 2). Statements are
#     executed one at a time; that opens no transaction and commits nothing.
# ==========================================================================

PROVENANCE_TABLE = "embedding_provenance"
PROVENANCE_COLUMNS = ("memory_id", "vec_table", "model_id", "model_ref", "weights_hash",
                      "dim", "content_hash", "embedded_at", "organ", "input_kind", "inputs")
MODEL_REF_UNRESOLVED = "unresolved"
# migration_042's declared absence: "nobody recorded the checksum of the weights
# that produced this vector". Written ONLY where no checksum was computed: a
# vector into a tier the package catalogue does not ship (jina, kept for personal
# installs), or a vector on a brain that predates the install lock when no
# verified embedder is resident in this process. A reader that compares against a
# lock never matches it. Same spelling as MODEL_REF_UNRESOLVED, on purpose: one
# word for "unknown" in this table. Every other stamp carries the checksum the
# resident embedder COMPUTED from its bytes (embedder_lock.require_loaded_hash).
WEIGHTS_HASH_UNRESOLVED = "unresolved"
_PROVENANCE_MIGRATION = (Path(__file__).resolve().parent.parent / "migrations"
                         / "migration_033_embedding_provenance.sql")
_PROVENANCE_MIGRATION_042 = (Path(__file__).resolve().parent.parent / "migrations"
                             / "migration_042_embedder_lock.sql")
_provenance_stmt_cache: Dict[str, List[str]] = {}


class ProvenanceError(MemoryWriteError):
    """Base for the provenance reader's refusals. `.code` names the refusal."""
    code = "PROVENANCE_ERROR"

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "error", "error": self.code, "message": str(self)}


class ProvenanceUnreadable(ProvenanceError):
    """The sidecar or the vector table cannot be READ on this connection.

    This is 'cannot measure', which is a different answer from 'measured zero'.
    A brain without vec0 loaded, or without the migration applied, gets THIS —
    never a coverage of 0/N that a caller might read as 'nothing is stamped'.
    """
    code = "PROVENANCE_UNREADABLE"


class ProvenanceUnmatched(ProvenanceError):
    """Measured, and ZERO rows are stamped under the active tier's model.

    The typed refusal M1-C2 requires: a reader that filtered on this tier would
    be searching nothing, or searching another model's space. `.coverage` carries
    the full report so the refusal still reports coverage.
    """

    code = "NO_ROWS_FOR_LOCK"

    def __init__(self, message: str, coverage: Any, code: Optional[str] = None):
        super().__init__(message)
        self.coverage = coverage
        if code:
            self.code = code

    def as_dict(self) -> Dict[str, Any]:
        return {**super().as_dict(), "coverage": self.coverage.as_dict()}


class ProvenanceInconsistent(ProvenanceUnmatched):
    """STAMP_INCONSISTENT: a row carries the lock's checksum but its `dim` or
    `vec_table` disagrees with the lock. The checksum says 'this model'; the shape
    says 'not this table' -- one of them is wrong and the reader will not guess."""
    code = "STAMP_INCONSISTENT"


def _provenance_statements(path: Optional[Path] = None) -> List[str]:
    """The migration's statements, split by sqlite3.complete_statement, cached.

    Comment-only text is dropped; a comment line that introduces a statement
    stays attached to it (the same rule schema_migrator.iter_statements applies,
    re-implemented here rather than imported so this module's import surface
    does not widen).
    """
    path = path or _PROVENANCE_MIGRATION
    key = str(path)
    if key in _provenance_stmt_cache:
        return _provenance_stmt_cache[key]
    try:
        sql = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProvenanceUnreadable(
            f"provenance migration absent at {path} ({e}). "
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
        raise ProvenanceUnreadable(f"{path} parsed to zero statements")
    _provenance_stmt_cache[key] = out
    return out


def _sidecar_columns(conn: sqlite3.Connection) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({PROVENANCE_TABLE})").fetchall()]


def ensure_provenance_table(conn: sqlite3.Connection) -> None:
    """Idempotent, shape-driven, one statement at a time, inside ONE savepoint.

    absent      -> migration_033 then migration_042 (a fresh, empty sidecar)
    033 shape   -> migration_042 alone: the house rebuild, which carries every
                   existing row forward with weights_hash 'unresolved'
    042 shape   -> nothing.

    A SAVEPOINT with no transaction open BEGINS one; inside a caller's open
    transaction it nests. Either way a refused rebuild (one of 042's named
    CHECKs) rolls back whole -- no `embedding_provenance_new` stranded, no row
    moved -- and RELEASE commits only when this was the outermost frame.
    NEVER executescript here: it would COMMIT the caller's open transaction.
    """
    cols = _sidecar_columns(conn)
    if "weights_hash" in cols:
        return
    sp = "ensure_provenance_table"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        if not cols:
            for stmt in _provenance_statements():
                conn.execute(stmt)
        for stmt in _provenance_statements(_PROVENANCE_MIGRATION_042):
            conn.execute(stmt)
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except sqlite3.Error as e:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise ProvenanceUnreadable(
            f"the provenance sidecar could not be brought to migration_042's shape ({e}); "
            "it is left exactly as found. Run `schema_migrator apply` and read its report.") from e


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
    import embedder_lock
    hub = embedder_lock.hf_hub_cache() / ("models--" + model_id.replace("/", "--"))
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


def _embedder_lock():
    """embedder_lock, imported lazily so this module's import surface stays what
    it was (stdlib + the organs it already reached for on demand)."""
    import embedder_lock
    return embedder_lock


def _weights_hash_for(conn: sqlite3.Connection, table: str, dim: int,
                      ident: Dict[str, str]) -> Tuple[str, str]:
    """(weights_hash, source) for a vector this call just wrote to `table`.

    The install lock beside THIS connection's database is read FIRST (owner
    decision D2), on every call, never cached:

    locked brain   the checksum the resident embedder COMPUTED from its bytes
                   (embedder_lock.require_loaded_hash, which also refuses an
                   embedder that writes another table or width). The write must
                   land in the table the lock names (STAMP_INCONSISTENT) and the
                   computed checksum must be the lock's (WEIGHTS_MISMATCH).
                   Nothing resident is a refusal: a locked brain keeps no vector
                   whose bytes nobody checked.
    pre-lock brain (no embedder.lock.json beside the database)
                   a catalogue tier: the computed checksum when a verified
                   embedder is resident, compared to the shipped catalogue entry
                   for that tier (embedding_upgrade.brain_lock); with none
                   resident, the declared absence 'unresolved'.
                   any other tier (jina, personal installs): 'unresolved' --
                   the catalogue ships no checksum for it to compare against.
    A malformed lock refuses (LOCK_MALFORMED); it is never read as absent.
    """
    el = _embedder_lock()
    try:
        lock = el.resolve(conn)
    except el.EmbedderLockError as e:
        if e.code != "LOCK_MISSING":
            raise
        lock = None
    if lock is not None:
        computed = el.require_loaded_hash(vec_table=table, dim=int(dim))
        if table != lock["vec_table"] or int(dim) != int(lock["dim"]):
            raise el.EmbedderLockError(
                "STAMP_INCONSISTENT",
                f"the write landed in {table!r} at {dim} wide, but the lock at {lock.get('_path')} "
                f"promises {lock['vec_table']!r} at {lock['dim']}; refusing to stamp a row the "
                "lock never promised", vec_table=table, dim=int(dim),
                lock_vec_table=lock["vec_table"], lock_dim=int(lock["dim"]))
        if computed != lock["weights_hash"]:
            raise el.EmbedderLockError(
                "WEIGHTS_MISMATCH",
                f"the vector was produced by weights {computed[:12]}… but the lock at "
                f"{lock.get('_path')} promises {lock['weights_hash'][:12]}…; refusing to stamp "
                "(and so to keep) a vector from a model this install did not promise",
                computed=computed, lock_hash=lock["weights_hash"])
        return computed, "lock"
    shipped = {e["vec_table"]: t for t, e in el.catalogue_tiers().items()}
    if table not in shipped or el.loaded() is None:
        return WEIGHTS_HASH_UNRESOLVED, "unresolved"
    computed = el.require_loaded_hash(vec_table=table, dim=int(dim))
    import embedding_upgrade                     # lazy: it imports this module's callers
    entry = embedding_upgrade.brain_lock(conn, tier=ident["tier"])
    if computed != entry["weights_hash"]:
        raise el.EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"the vector was produced by weights {computed[:12]}… but the shipped catalogue "
            f"promises {entry['weights_hash'][:12]}… for {table!r}; refusing to stamp it",
            computed=computed, lock_hash=entry["weights_hash"])
    return computed, "catalogue"


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
    weights_hash, weights_source = _weights_hash_for(conn, table, int(dim), ident)
    row = {
        "memory_id": int(memory_id), "vec_table": table,
        "model_id": ident["model_id"], "model_ref": ident["model_ref"],
        "weights_hash": weights_hash,
        "dim": int(dim), "content_hash": content_hash(text),
        "embedded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "organ": organ or "?",
        "input_kind": "text", "inputs": None,
    }
    conn.execute(
        f"INSERT OR REPLACE INTO {PROVENANCE_TABLE} ({', '.join(PROVENANCE_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in PROVENANCE_COLUMNS)})",
        row,
    )
    return {**row, "ref_source": ident["ref_source"], "tier": ident["tier"],
            "weights_source": weights_source}


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
                              *, organ: str, table: str, dim: int) -> Dict[str, Any]:
    """For a caller that has ALREADY inserted the vector inside its own open
    transaction (claude_memory's cmd_store / cmd_update / cmd_migrate) and will
    commit or roll back the whole thing itself.

    `table` and `dim` are REQUIRED and are what the caller wrote: there is no
    second resolver here that could disagree with the write. The atomicity is the
    caller's transaction: a raise propagates, the caller does not commit, and
    memory + vector + stamp are all absent together.
    """
    return _stamp_provenance(conn, memory_id, table, int(dim), text, organ)


def centroid_content_hash(member_vectors: Sequence[bytes]) -> str:
    """sha256 over the member vectors' BYTES, in the caller's (memory_id) order.

    Bytes, not ids: a member re-embedded after a text edit changes its bytes and so
    makes the centroid detectably stale, which an id list could never show."""
    h = hashlib.sha256()
    for v in member_vectors:
        h.update(bytes(v))
    return h.hexdigest()


class CentroidRefused(MemoryWriteError):
    """On a locked brain, a centroid whose members are not ALL stamped under the
    lock's checksum is refused, not stamped: averaging across spaces is not a vector
    in any space. `.detail` names the members and why."""

    def __init__(self, message: str, **detail: Any):
        super().__init__(message)
        self.detail = dict(detail)


def stamp_centroid(conn: sqlite3.Connection, memory_id: int, table: str, dim: int,
                   member_ids: Sequence[int], member_vectors: Sequence[bytes],
                   organ: str) -> Dict[str, Any]:
    """The stamp for a vector no embedder produced: an average of member vectors.
    Called by the one organ that writes its own vector
    (sleep_loop.consolidate_episodics), inside its open transaction, AFTER the
    centroid INSERT.

    locked brain   weights_hash / model_id / dim are INHERITED AND VERIFIED: every
                   member's stamp must exist on `table` and carry the lock's checksum
                   at the lock's width, and `table` must be the lock's; otherwise
                   CentroidRefused, and the caller rolls its INSERT back. A legacy
                   member ('unresolved', or no stamp) refuses too: nobody can say what
                   space it is in, so the average cannot carry the lock's checksum.
    pre-lock brain (no embedder.lock.json beside the database, which includes every
                   tier the catalogue does not ship) keeps writing, never a refusal:
                   the checksum every member carries when they all agree on one at
                   this width, else the declared absence 'unresolved'.
    A malformed lock raises LOCK_MALFORMED; it is never read as absent.

    content_hash is the sha256 over the member vectors' bytes in memory_id order;
    input_kind is 'centroid' and inputs the member ids, so a tier change can see what
    the row was derived from.
    """
    ids = [int(i) for i in member_ids]
    if not ids or len(ids) != len(member_vectors):
        raise CentroidRefused("a centroid needs at least one member, and one vector per member",
                              members=ids, vectors=len(member_vectors))
    el = _embedder_lock()
    ensure_provenance_table(conn)
    try:
        lock = el.resolve(conn)
    except el.EmbedderLockError as e:
        if e.code != "LOCK_MISSING":
            raise
        lock = None
    if lock is not None and (table != lock["vec_table"] or int(dim) != int(lock["dim"])):
        raise CentroidRefused(
            f"centroid targets {table!r}@{dim} but the lock promises {lock['vec_table']!r}@{lock['dim']}",
            vec_table=table, dim=int(dim), lock_vec_table=lock["vec_table"], lock_dim=int(lock["dim"]))
    by_id: Dict[int, Tuple[Any, int]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        ph = ",".join("?" * len(chunk))
        for mid, whash, wide in conn.execute(
                f"SELECT memory_id, weights_hash, dim FROM {PROVENANCE_TABLE} "
                f"WHERE vec_table = ? AND memory_id IN ({ph})", [table, *chunk]).fetchall():
            by_id[int(mid)] = (whash, int(wide))
    if lock is not None:
        missing = [i for i in ids if i not in by_id]
        other = sorted(i for i in ids if i in by_id and by_id[i][0] != lock["weights_hash"])
        wrong_dim = sorted(i for i in ids if i in by_id and by_id[i][1] != int(lock["dim"]))
        if missing or other or wrong_dim:
            raise CentroidRefused(
                f"refusing to stamp a centroid over members not all under the lock "
                f"({lock['weights_hash'][:12]}…): {len(missing)} unstamped, {len(other)} under "
                f"another checksum or 'unresolved', {len(wrong_dim)} at another width. Averaging "
                "across spaces is not a vector in any space.",
                missing=missing, foreign=other, wrong_dim=wrong_dim, lock_hash=lock["weights_hash"])
        weights_hash, model_id, model_ref = lock["weights_hash"], lock["model_id"], lock["revision"]
    else:
        agreed = {by_id.get(i) for i in ids}
        only = agreed.pop() if len(agreed) == 1 else None
        weights_hash = (only[0] if only and only[0] and only[0] != WEIGHTS_HASH_UNRESOLVED
                        and only[1] == int(dim) else WEIGHTS_HASH_UNRESOLVED)
        ident = model_identity(table)
        model_id, model_ref = ident["model_id"], ident["model_ref"]
    order = sorted(range(len(ids)), key=lambda k: ids[k])
    row = {
        "memory_id": int(memory_id), "vec_table": table, "model_id": model_id,
        "model_ref": model_ref, "weights_hash": weights_hash, "dim": int(dim),
        "content_hash": centroid_content_hash([member_vectors[k] for k in order]),
        "embedded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "organ": organ or "?", "input_kind": "centroid", "inputs": json.dumps(sorted(ids)),
    }
    conn.execute(
        f"INSERT OR REPLACE INTO {PROVENANCE_TABLE} ({', '.join(PROVENANCE_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in PROVENANCE_COLUMNS)})", row)
    return row


def attach_under_lock(conn: sqlite3.Connection, memory_id: int, text: str, vec: bytes,
                      *, lock: Dict[str, Any], organ: str) -> Dict[str, Any]:
    """Write ONE vector and its stamp into the table `lock` names -- which need NOT
    be the table the brain's installed lock names. This is the build path of
    `tier set` (organs/tier_set.py): the target tier is embedded while the brain
    still serves the old one, so the installed lock cannot be the authority here;
    the TARGET lock, resolved from the shipped catalogue, is.

    The stamp is held to the same standard as every other stamp: its weights_hash
    is the checksum the resident embedder COMPUTED (embedder_lock.require_loaded_hash,
    which also refuses a resident embedder that writes another table or width), and
    that checksum must equal the target lock's. Any existing vector for the memory
    in that table is replaced, with its stamp, in one SAVEPOINT: both rows or
    neither. The caller owns the transaction (it commits per batch).
    """
    el = _embedder_lock()
    table, dim = lock["vec_table"], int(lock["dim"])
    vec = bytes(vec)
    assert_width(vec, dim)
    computed = el.require_loaded_hash(vec_table=table, dim=dim)
    if computed != lock["weights_hash"]:
        raise el.EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"the vector was produced by weights {computed[:12]}… but the target lock promises "
            f"{lock['weights_hash'][:12]}… for {table!r}; refusing to write it",
            computed=computed, lock_hash=lock["weights_hash"])
    ident = model_identity(table)
    if ident["model_id"] != lock["model_id"]:
        raise el.EmbedderLockError(
            "STAMP_INCONSISTENT",
            f"{table!r} belongs to {ident['model_id']} in this engine, but the target lock names "
            f"{lock['model_id']}", vec_table=table)
    row = {
        "memory_id": int(memory_id), "vec_table": table,
        "model_id": ident["model_id"], "model_ref": ident["model_ref"],
        "weights_hash": computed, "dim": dim, "content_hash": content_hash(text),
        "embedded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "organ": organ or "?", "input_kind": "text", "inputs": None,
    }
    sp = "vec_under_lock"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        conn.execute(f"DELETE FROM {table} WHERE memory_id = ?", (int(memory_id),))
        conn.execute(f"INSERT INTO {table} (memory_id, embedding) VALUES (?, ?)",
                     (int(memory_id), vec))
        conn.execute(
            f"INSERT OR REPLACE INTO {PROVENANCE_TABLE} ({', '.join(PROVENANCE_COLUMNS)}) "
            f"VALUES ({', '.join(':' + c for c in PROVENANCE_COLUMNS)})", row)
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    return row


# --------------------------------------------------------------------------
# THE SEARCH SET -- the one filter every vector reader takes.
#
# THE INSTALL LOCK FIRST (owner decision D2). A LOCKED brain's search set has two
# parts, both in the lock's own vector table:
#   VERIFIED  rows whose stamp carries the lock's computed checksum at its width;
#   LEGACY    rows whose stamp is the declared absence 'unresolved', and rows with
#             no stamp on that table at all.
# A stamp naming a DIFFERENT checksum is outside the set, and so is every vector
# in another table. Legacy rows exist because `init` pins a brain that already
# has vectors (written before any lock); dropping them would silently empty that
# brain's vector recall until a re-embed. They are counted on every answer
# (`legacy_unverified: N of M`), and a re-embed or `tier set` turns them into
# verified rows: the unit of work below still counts every legacy row as work.
# A brain with NO lock (every brain installed before the lock existed, and the
# jina tier, which the catalogue does not ship) made no promise about bytes: its
# search set is the whole table it resolves to, exactly as before the lock. Every
# answer says which rule it used. A malformed lock refuses; never read as absent.
# --------------------------------------------------------------------------

SEARCH_RULE_LOCKED = "lock: verified + legacy"
SEARCH_RULE_LOCKLESS = "lockless: whole table"

def lock_identity(conn: sqlite3.Connection) -> Dict[str, Any]:
    """{tier, model_id, revision, weights_hash, vec_table, dim, lock_path} for the
    database on this connection -- from the lock, on every call, never cached,
    never from `metadata`. LOCK_MISSING / LOCK_MALFORMED raise, typed."""
    lock = _embedder_lock().resolve(conn)
    return {"tier": lock["tier"], "model_id": lock["model_id"], "revision": lock["revision"],
            "weights_hash": lock["weights_hash"], "vec_table": lock["vec_table"],
            "dim": int(lock["dim"]), "lock_path": lock.get("_path")}


@dataclass
class ProvenanceFilter:
    """What a reader must filter on, derived ON THIS CALL. No cache, no literal.

    weights_hash is the lock's checksum on a locked brain and None on a brain with
    no lock (`basis` 'lockless'): there the search set is the whole table. The SQL
    fragments below have the same shape either way, so a caller binds
    `search_set_params` without asking which basis it got."""
    vec_table: str
    dim: int
    weights_hash: Optional[str]
    model_id: str
    revision: str
    tier: str
    lock_path: Optional[str]

    @property
    def locked(self) -> bool:
        return self.weights_hash is not None

    @property
    def basis(self) -> str:
        return "lock" if self.locked else "lockless"

    @property
    def search_rule(self) -> str:
        return SEARCH_RULE_LOCKED if self.locked else SEARCH_RULE_LOCKLESS

    def member_sql(self, mem_alias: str = "m") -> str:
        """A predicate over `memories {mem_alias}` that is true when the memory's
        vector, if it has one in this table, is in the search set. Locked: no stamp
        on the lock's table names anything but the lock's checksum at its width or
        'unresolved' (one stamp per memory per table: the sidecar's key). Bind
        `search_set_params` for its three `?`. Lockless: always true."""
        if not self.locked:
            return "1"
        return (f"NOT EXISTS (SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = {mem_alias}.id "
                f"AND p.vec_table = ? AND NOT ((p.weights_hash = ? AND p.dim = ?) "
                f"OR p.weights_hash = '{WEIGHTS_HASH_UNRESOLVED}'))")

    def verified_sql(self, vec_alias: str = "v") -> str:
        """True when the vector row `{vec_alias}` is VERIFIED (its stamp carries the
        lock's checksum at its width). Bind `search_set_params`. Locked only."""
        if not self.locked:
            raise ValueError("verified_sql needs a locked filter")
        return (f"EXISTS (SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = {vec_alias}.memory_id "
                f"AND p.vec_table = ? AND p.weights_hash = ? AND p.dim = ?)")

    @property
    def search_set_sql(self) -> str:
        """A `memory_id IN (...)` predicate for the vector table. sqlite-vec pushes
        a `rowid IN (subquery)` constraint INTO the KNN (a pre-filter: with the
        nearest row outside the set, the next one is returned), so the set is the
        subquery, not the top-k afterwards. Locked: the memories whose stamp on the
        lock's table is verified or legacy (member_sql)."""
        if self.locked:
            return f"memory_id IN (SELECT m.id FROM memories m WHERE {self.member_sql('m')})"
        return "memory_id IN (SELECT m.id FROM memories m)"

    @property
    def search_set_params(self) -> Tuple[Any, ...]:
        return (self.vec_table, self.weights_hash, self.dim) if self.locked else ()

    @property
    def active_search_set_sql(self) -> str:
        """The search set restricted to ACTIVE memories, as ONE `memory_id IN (...)`
        predicate: vec0 binds `k` before any outer JOIN's WHERE runs, so an archive
        filter applied afterwards would return k minus every archived neighbour,
        and vec0 admits exactly one IN constraint. Bind `search_set_params`."""
        if self.locked:
            return (f"memory_id IN (SELECT m.id FROM memories m WHERE "
                    f"COALESCE(m.is_archived, 0) = 0 AND {self.member_sql('m')})")
        return "memory_id IN (SELECT m.id FROM memories m WHERE COALESCE(m.is_archived, 0) = 0)"

    def stamped_join_sql(self, vec_alias: str = "e", mem_alias: str = "m") -> str:
        """For a reader that starts from `memories {mem_alias}`: the JOIN to the
        search set, as `JOIN ... ON ...`. Bind `search_set_params` BEFORE the
        caller's own parameters."""
        join = f"JOIN {self.vec_table} {vec_alias} ON {vec_alias}.memory_id = {mem_alias}.id"
        if not self.locked:
            return join
        return f"{join} AND {self.member_sql(mem_alias)}"

    def verified_join_sql(self, mem_alias: str = "m", stamp_alias: str = "p") -> str:
        """For a reader that derives a NEW vector from what it reads, so needs the
        VERIFIED part of the search set and not its legacy part: a plain JOIN to the
        stamp carrying the lock's checksum at its width, one keyed lookup per row.
        Bind `search_set_params` before the caller's own parameters. Lockless: empty,
        no stamp is required and the parameters are empty too."""
        if not self.locked:
            return ""
        return (f"JOIN {PROVENANCE_TABLE} {stamp_alias} ON {stamp_alias}.memory_id = {mem_alias}.id "
                f"AND {stamp_alias}.vec_table = ? AND {stamp_alias}.weights_hash = ? "
                f"AND {stamp_alias}.dim = ?")

    def as_dict(self) -> Dict[str, Any]:
        return {"basis": self.basis, "search_rule": self.search_rule,
                "vec_table": self.vec_table, "dim": self.dim,
                "weights_hash": self.weights_hash, "model_id": self.model_id,
                "revision": self.revision, "tier": self.tier, "lock_path": self.lock_path}


def provenance_filter(conn: sqlite3.Connection) -> ProvenanceFilter:
    """The filter for this connection's database, derived on this call.

    Locked: from <data-dir>/embedder.lock.json (embedder_lock.read_lock has
    already refused a table this package does not build). No lock: the table the
    brain resolves to (embed_resolver) and the producer the bijection names for it
    (model_identity), with weights_hash None. LOCK_MALFORMED raises
    embedder_lock.EmbedderLockError; an unresolvable tier raises
    EmbedTierUnresolved / EmbeddingUnavailable -- typed, never a default."""
    el = _embedder_lock()
    try:
        lock = el.resolve(conn)
    except el.EmbedderLockError as e:
        if e.code != "LOCK_MISSING":
            raise
        lock = None
    if lock is not None:
        return ProvenanceFilter(vec_table=lock["vec_table"], dim=int(lock["dim"]),
                                weights_hash=lock["weights_hash"], model_id=lock["model_id"],
                                revision=lock["revision"], tier=lock["tier"],
                                lock_path=lock.get("_path"))
    table, dim = _resolve_tier(conn)
    ident = model_identity(table)
    return ProvenanceFilter(vec_table=table, dim=int(dim), weights_hash=None,
                            model_id=ident["model_id"], revision=ident["model_ref"],
                            tier=ident["tier"], lock_path=None)


# --------------------------------------------------------------------------
# THE READER. Filter from the lock, refuse on zero match, coverage always.
# --------------------------------------------------------------------------

@dataclass
class ProvenanceCoverage:
    """What the sidecar says about this brain, under the ACTIVE tier's filter.

    reachable         active memories with a vector in vec_table: what a search of
                      this brain can return, its rule being the whole table. This
                      is `matched`, the figure on every answer.
    covered           rows with a vector in vec_table AND a stamp naming
                      (vec_table, model_id): the part whose producer is on record,
                      which is what a re-embed moves.
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
    reachable: int = 0

    basis = "lockless"
    search_rule = SEARCH_RULE_LOCKLESS

    @property
    def matched(self) -> int:
        return self.reachable

    @property
    def total(self) -> int:
        return self.active_memories

    @property
    def coverage(self) -> Dict[str, Any]:
        return _three_numbers(self.reachable, self.active_memories)

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
            "basis": self.basis, "search_rule": self.search_rule,
            # No lock, no checksum to verify against: the whole table is searched
            # and "legacy" has no meaning here (None, never 0).
            "legacy_unverified": None,
            "matched": self.matched, "total": self.total,
            "coverage": self.coverage,
        }


def provenance_coverage(conn: sqlite3.Connection):
    """Measure the sidecar. ALWAYS returns coverage, on every call; RAISES,
    carrying the same coverage, when the search set would be empty or lying.
    There is no fallback path and no flag.

    A LOCKED brain is measured under the lock's search set, verified + legacy
    (LockCoverage; the refusals are listed on _lock_coverage). A brain with NO lock is measured
    under the producer the bijection names for its table (ProvenanceCoverage),
    and refuses NO_ROWS_FOR_MODEL when zero rows are stamped under it.
    LOCK_MALFORMED raises, typed, with the coverage the memories table still
    answers (matched 0 by construction: no filter can be built).
    """
    el = _embedder_lock()
    try:
        flt = provenance_filter(conn)
    except el.EmbedderLockError as e:
        if "coverage" in e.detail:
            raise
        total = _active_memory_count(conn)
        raise el.EmbedderLockError(
            e.code, str(e).split(": ", 1)[1] if ": " in str(e) else str(e),
            **e.detail, coverage={**_three_numbers(0, total), "coverage": _three_numbers(0, total),
                                  "total_basis": "active memories; matched 0 by construction: no filter"}) from e
    if flt.locked:
        return _lock_coverage(conn, flt)
    table, model_id, dim = flt.vec_table, flt.model_id, flt.dim
    model_ref_expected = flt.revision

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
        reach_pred = "AND COALESCE(m.is_archived, 0) = 0"
    else:
        denominator = "memories (schema has no is_archived column)"
        active = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        reach_pred = ""
    reachable = conn.execute(
        f"SELECT COUNT(*) FROM memories m WHERE m.id IN (SELECT memory_id FROM {table}) {reach_pred}"
    ).fetchone()[0]

    covered = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p "
        f"WHERE p.vec_table = ? AND p.model_id = ? "
        f"  AND p.memory_id IN (SELECT memory_id FROM {table})",
        (table, model_id)).fetchone()[0]
    foreign = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? AND p.model_id <> ?",
        (table, model_id)).fetchone()[0]
    stamp_no_vec = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? "
        f"  AND p.memory_id NOT IN (SELECT memory_id FROM {table})",
        (table,)).fetchone()[0]
    vec_no_stamp = conn.execute(
        f"SELECT COUNT(*) FROM {table} v WHERE NOT EXISTS "
        f"  (SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = v.memory_id AND p.vec_table = ?)",
        (table,)).fetchone()[0]

    conn.create_function("vl_sha256", 1, content_hash, deterministic=True)
    # A centroid has no source text, so it is never stale against one. A sidecar
    # still in migration_033's shape has no input_kind and holds no centroid.
    text_rows = "AND p.input_kind = 'text' " if "input_kind" in _sidecar_columns(conn) else ""
    stale = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p JOIN memories m ON m.id = p.memory_id "
        f"WHERE p.vec_table = ? AND p.model_id = ? {text_rows}"
        f"  AND p.memory_id IN (SELECT memory_id FROM {table}) "
        f"  AND p.content_hash <> vl_sha256(m.content)",
        (table, model_id)).fetchone()[0]

    cov = ProvenanceCoverage(
        vec_table=table, model_id=model_id, model_ref_expected=model_ref_expected,
        dim=dim, denominator=denominator, active_memories=int(active),
        vectors=int(vectors), covered=int(covered), stamped_foreign=int(foreign),
        stamped_without_vector=int(stamp_no_vec), vectors_without_stamp=int(vec_no_stamp),
        stale_content_hash=int(stale), reachable=int(reachable),
    )
    if cov.covered == 0:
        raise ProvenanceUnmatched(
            f"REFUSED: zero rows in {table!r} are stamped under {model_id!r}. "
            f"{cov.vectors} vector(s) present, {cov.vectors_without_stamp} unstamped, "
            f"{cov.stamped_foreign} stamped under another model. A reader filtering on "
            f"this tier would search nothing, or another model's space. Coverage "
            f"{cov.covered}/{cov.active_memories} of {denominator}.", cov, code="NO_ROWS_FOR_MODEL")
    return cov


# The catalogue tier a lock names -> the name `voidloom tier set` takes. The same
# pairs as tier_set.SHIPPED_TARGETS (inverted); a test holds the two equal, so this
# module need not import the verb.
LOCK_TIER_TO_TIER_SET = {"cpu": "nomic", "gpu": "qwen"}


def _db_file(conn: sqlite3.Connection) -> Optional[str]:
    """The main database file on this connection, or None (in-memory / unreadable)."""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list").fetchall():
            if name == "main" and path:
                return str(path)
    except sqlite3.Error:
        pass
    return None


def re_embed_command(db_path: Optional[str]) -> str:
    """The shipped command that embeds a locked brain's work under its own lock.
    It always names the database: without `--db-path` the command acts on its
    default test database, not on this brain."""
    return (f"python3 organs/embedding_upgrade.py --db-path {db_path or '<brain>/claude_memory.db'} "
            f"re-embed")


def re_embed_hint(n: int, db_path: Optional[str], lock_tier: Optional[str]) -> str:
    """The words beside `legacy_unverified` when N > 0."""
    name = LOCK_TIER_TO_TIER_SET.get(lock_tier or "")
    alt = f" (or `voidloom tier set {name}`)" if name else ""
    return (f"run `{re_embed_command(db_path)}`{alt} to verify {n} legacy vector(s): "
            f"they were written before this brain's lock and are searched, but their "
            f"bytes are not yet checked against it")


def _three_numbers(num: int, den: int) -> Dict[str, Any]:
    # RAW counts, never the reduced ratio: Fraction(5, 10) prints as 1/2, and a
    # reader of "1 of 2" would believe the population is two. 0/0 is 0.0 and is
    # COVERAGE, not a refusal: an empty brain is empty, not broken.
    return {"matched": int(num), "total": int(den), "value": (num / den) if den else 0.0}


@dataclass
class LockCoverage:
    """What the sidecar says about a LOCKED brain, under the lock's filter.

    matched           active memories whose vector is in the SEARCH SET: verified
                      plus legacy (see the search-set comment above).
    verified          of those, the rows whose stamp carries the lock's checksum.
    legacy_unverified of those, the rows whose stamp is 'unresolved' or absent.
                      Reported as `legacy_unverified: N of M` (M = matched).
    total             active memories: an unembedded row is visible as uncovered.
    coverage          {matched, total, value} -- three numbers, never reduced.
    stamped_other_hash   stamps on the table whose checksum is not the lock's:
                      another model's rows, or 'unresolved'. (The foreign ones,
                      outside the set, are `stamped_foreign_hash`.)
    stamped_without_vector / vectors_without_stamp / stale_content_hash
                      as for ProvenanceCoverage.
    histogram         {weights_hash: count} over the table -- what is actually
                      present, so NO_ROWS_FOR_LOCK can say which weights built it.
    inconsistent      rows carrying the lock's checksum on another table or width.
    """
    filter: ProvenanceFilter
    denominator: str
    active_memories: int
    vectors: int
    matched: int
    stamped_other_hash: int
    stamped_without_vector: int
    vectors_without_stamp: int
    stale_content_hash: int
    histogram: Dict[str, int] = field(default_factory=dict)
    inconsistent: List[Dict[str, Any]] = field(default_factory=list)
    verified: int = 0
    legacy_unverified: int = 0
    stamped_foreign_hash: int = 0
    db_path: Optional[str] = None

    basis = "lock"
    search_rule = SEARCH_RULE_LOCKED

    @property
    def legacy(self) -> Dict[str, Any]:
        """`legacy_unverified: N of M`, as numbers and as the words a user reads,
        with the command that verifies them when N > 0."""
        n, m = self.legacy_unverified, self.matched
        out: Dict[str, Any] = {"legacy": n, "of": m, "text": f"{n} of {m}"}
        if n:
            out["hint"] = re_embed_hint(n, self.db_path, self.filter.tier)
            out["retry"] = [re_embed_command(self.db_path)]
        return out

    @property
    def total(self) -> int:
        return self.active_memories

    @property
    def coverage(self) -> Dict[str, Any]:
        return _three_numbers(self.matched, self.active_memories)

    @property
    def coverage_of_active(self) -> Fraction:
        return Fraction(self.matched, self.active_memories) if self.active_memories else Fraction(0)

    @property
    def covered(self) -> int:
        return self.matched

    @property
    def stamped_foreign(self) -> int:
        return self.stamped_other_hash

    @property
    def vec_table(self) -> str:
        return self.filter.vec_table

    @property
    def model_id(self) -> str:
        return self.filter.model_id

    @property
    def dim(self) -> int:
        return self.filter.dim

    @property
    def defects(self) -> int:
        return (self.stamped_other_hash + self.stamped_without_vector
                + self.vectors_without_stamp + len(self.inconsistent))

    @property
    def clean(self) -> bool:
        return self.defects == 0

    @property
    def empty(self) -> bool:
        return self.active_memories == 0 and self.vectors == 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "basis": self.basis, "search_rule": self.search_rule, "filter": self.filter.as_dict(),
            "verified": self.verified, "legacy_unverified": self.legacy,
            "stamped_foreign_hash": self.stamped_foreign_hash,
            "vec_table": self.filter.vec_table, "model_id": self.filter.model_id,
            "weights_hash": self.filter.weights_hash, "dim": self.filter.dim,
            "tier": self.filter.tier, "denominator": self.denominator,
            "active_memories": self.active_memories, "vectors": self.vectors,
            "matched": self.matched, "total": self.total, "coverage": self.coverage,
            "covered": self.matched, "stamped_foreign": self.stamped_other_hash,
            "coverage_of_active": {"num": self.matched, "den": self.total,
                                   "value": self.coverage["value"]},
            "coverage_of_vectors": {"num": self.matched, "den": self.vectors,
                                    "value": (self.matched / self.vectors) if self.vectors else 0.0},
            "stamped_other_hash": self.stamped_other_hash,
            "stamped_without_vector": self.stamped_without_vector,
            "vectors_without_stamp": self.vectors_without_stamp,
            "stale_content_hash": self.stale_content_hash,
            "histogram": dict(self.histogram), "inconsistent": list(self.inconsistent),
            "defects": self.defects, "clean": self.clean, "empty": self.empty,
        }


def _active_memory_count(conn: sqlite3.Connection) -> int:
    """The denominator a refusal can still derive: active memories, from the
    memories table alone. 0 when even that cannot be read."""
    try:
        mcols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if not mcols:
            return 0
        if "is_archived" in mcols:
            return int(conn.execute(
                "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0").fetchone()[0])
        return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    except sqlite3.Error:
        return 0


def _lock_coverage(conn: sqlite3.Connection, flt: ProvenanceFilter) -> LockCoverage:
    """Measure a LOCKED brain's sidecar under the lock's filter. Refusals, in order
    (facts before policy):
      PROVENANCE_UNREADABLE   the sidecar / vector table cannot be read: UNMEASURED, not 0
      (empty brain)           0/0 is returned, unless the lock's checksum is not shipped
      NO_ROWS_FOR_LOCK        active memories exist and none is stamped under the lock
      LOCK_NOT_SHIPPED        rows match, but the checksum is not in the catalogue
      STAMP_INCONSISTENT      the lock's checksum on another table or width
    """
    el = _embedder_lock()
    table, whash, dim = flt.vec_table, flt.weights_hash, flt.dim
    cols = _sidecar_columns(conn)
    if "weights_hash" not in cols:
        raise ProvenanceUnreadable(
            f"{PROVENANCE_TABLE} is not readable in migration_042's shape on this connection "
            f"(columns: {cols or 'none'}); run `schema_migrator apply`. UNMEASURED, not zero.")
    try:
        vectors = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0
    except sqlite3.Error as e:
        raise ProvenanceUnreadable(
            f"vector table {table!r} is not readable on this connection ({e}). If the "
            "message is 'no such module: vec0' the extension is not loaded; that reads "
            "like absence and is not.") from e
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "is_archived" in mcols:
        denominator = "memories WHERE is_archived = 0"
        active = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0").fetchone()[0]
        archived_pred = "AND COALESCE(m.is_archived, 0) = 0"
    else:
        denominator = "memories (schema has no is_archived column)"
        active = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        archived_pred = ""
    # matched is the SEARCH SET's active part (verified + legacy) and reachable by
    # the search: an archived memory is outside every search, so it is outside
    # this count too (one population). Two queries, the second a subset of the
    # first, so `legacy_unverified` is their difference and can never go negative.
    params = flt.search_set_params
    vec_ids = f"SELECT v.memory_id FROM {table} v"
    matched = conn.execute(
        f"SELECT COUNT(*) FROM memories m WHERE m.id IN ({vec_ids}) {archived_pred} "
        f"AND {flt.member_sql('m')}", params).fetchone()[0]
    verified = conn.execute(
        f"SELECT COUNT(*) FROM memories m WHERE m.id IN ({vec_ids}) {archived_pred} "
        f"AND {flt.member_sql('m')} AND EXISTS (SELECT 1 FROM {PROVENANCE_TABLE} p "
        f"WHERE p.memory_id = m.id AND p.vec_table = ? AND p.weights_hash = ? AND p.dim = ?)",
        (*params, *params)).fetchone()[0]
    foreign = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? "
        f"  AND NOT ((p.weights_hash = ? AND p.dim = ?) OR p.weights_hash = ?)",
        (table, whash, dim, WEIGHTS_HASH_UNRESOLVED)).fetchone()[0]
    other = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? "
        f"  AND (p.weights_hash IS NULL OR p.weights_hash <> ?)", (table, whash)).fetchone()[0]
    stamp_no_vec = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p WHERE p.vec_table = ? "
        f"  AND p.memory_id NOT IN (SELECT memory_id FROM {table})",
        (table,)).fetchone()[0]
    vec_no_stamp = conn.execute(
        f"SELECT COUNT(*) FROM {table} v WHERE NOT EXISTS "
        f"  (SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = v.memory_id AND p.vec_table = ?)",
        (table,)).fetchone()[0]
    histogram = {(r[0] if r[0] is not None else "<null>"): int(r[1]) for r in conn.execute(
        f"SELECT weights_hash, COUNT(*) FROM {PROVENANCE_TABLE} WHERE vec_table = ? "
        f"GROUP BY weights_hash ORDER BY COUNT(*) DESC", (table,)).fetchall()}
    inconsistent = [{"vec_table": r[0], "dim": int(r[1]), "rows": int(r[2])} for r in conn.execute(
        f"SELECT vec_table, dim, COUNT(*) FROM {PROVENANCE_TABLE} WHERE weights_hash = ? "
        f"  AND (vec_table <> ? OR dim <> ?) GROUP BY vec_table, dim", (whash, table, dim)).fetchall()]
    conn.create_function("vl_sha256", 1, content_hash, deterministic=True)
    stale = conn.execute(
        f"SELECT COUNT(*) FROM {PROVENANCE_TABLE} p JOIN memories m ON m.id = p.memory_id "
        f"WHERE p.vec_table = ? AND p.weights_hash = ? AND p.input_kind = 'text' "
        f"  AND p.memory_id IN (SELECT memory_id FROM {table}) "
        f"  AND p.content_hash <> vl_sha256(m.content)", (table, whash)).fetchone()[0]
    cov = LockCoverage(
        filter=flt, denominator=denominator, active_memories=int(active), vectors=int(vectors),
        matched=int(matched), stamped_other_hash=int(other),
        stamped_without_vector=int(stamp_no_vec), vectors_without_stamp=int(vec_no_stamp),
        stale_content_hash=int(stale), histogram=histogram, inconsistent=inconsistent,
        verified=int(verified), legacy_unverified=int(matched) - int(verified),
        stamped_foreign_hash=int(foreign), db_path=_db_file(conn))
    shipped = el.is_shipped_hash(whash)
    if cov.empty:
        if not shipped:
            raise el.EmbedderLockError(
                "LOCK_NOT_SHIPPED", f"the lock at {flt.lock_path} names {whash[:12]}…, which is "
                "not in the package catalogue", weights_hash=whash, coverage=cov.as_dict())
        return cov
    if cov.matched == 0:
        raise ProvenanceUnmatched(
            f"REFUSED: no active memory has a vector in {table!r} that the lock's search set "
            f"admits (stamped {whash[:12]}…, 'unresolved', or unstamped) while "
            f"{cov.active_memories} active memories exist ({cov.vectors} vector(s), "
            f"{cov.stamped_foreign_hash} stamped under a different checksum; present: {histogram}). "
            f"A search filtered on this lock would search nothing; `{re_embed_command(cov.db_path)}` "
            f"embeds them under the lock. Coverage {cov.matched}/{cov.total} of {denominator}.", cov)
    if not shipped:
        raise el.EmbedderLockError(
            "LOCK_NOT_SHIPPED", f"{cov.matched} row(s) carry {whash[:12]}…, but that checksum is "
            "not in the package catalogue: this database was embedded with a model this package "
            "does not ship", weights_hash=whash, coverage=cov.as_dict())
    if cov.inconsistent:
        raise ProvenanceInconsistent(
            f"REFUSED: {sum(x['rows'] for x in cov.inconsistent)} stamp(s) carry the lock's checksum "
            f"{whash[:12]}… but disagree with the lock's ({table!r}, {dim}): {cov.inconsistent}. "
            "A checksum that names one model on two shapes is a stamp that cannot be trusted.", cov)
    return cov


def coverage_envelope(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Coverage for a caller that must ALWAYS carry it: never raises, and always
    carries `basis`, `matched`, `total` and `coverage: {matched, total, value}`.

    On a refusal that measured the sidecar the figures are the measured ones. On a
    refusal that could NOT measure (LOCK_MALFORMED, PROVENANCE_UNREADABLE, an
    unresolvable tier) the search set is unbuildable, so `matched` is 0 by
    construction and `total` is the active-memory count the memories table still
    answers; `coverage_basis` says which. Refusing the search is the caller's call."""
    def unbuildable(code: str, message: str) -> Dict[str, Any]:
        total = _active_memory_count(conn)
        return {"ok": False, "error": code, "message": message,
                "coverage_basis": "search set unbuildable under this refusal; matched is 0 by construction",
                "matched": 0, "total": total, "coverage": _three_numbers(0, total)}
    try:
        cov = provenance_coverage(conn)
        return {"ok": True, "coverage_basis": "measured", **cov.as_dict()}
    except ProvenanceUnmatched as e:
        return {"ok": False, "error": e.code, "message": str(e), "coverage_basis": "measured",
                **e.coverage.as_dict()}
    except ProvenanceError as e:
        return unbuildable(e.code, str(e))
    except Exception as e:                       # embedder_lock / embed_resolver refusals
        det = getattr(e, "detail", None) or {}
        code = getattr(e, "code", type(e).__name__)
        if isinstance(det.get("coverage"), dict):
            return {"ok": False, "error": code, "message": str(e), "coverage_basis": "measured",
                    **det["coverage"]}
        return unbuildable(code, str(e))


# --------------------------------------------------------------------------
# THE DERIVED LAYER: the checksum a derived row was computed under.
#
# A link strength from a cosine distance, a community's membership and a community
# summary are all computed from vectors, so after a model change they are numbers
# from the old space, and vector coverage cannot see them. Those rows carry the
# lock's checksum (migration_045's column), and the report below names how many do
# not. It also names the memories a locked brain cannot consolidate: a legacy vector
# stays searchable, but a centroid over it could not carry the lock's checksum.
# --------------------------------------------------------------------------
DERIVED_TABLES = ("memory_links", "memory_communities", "community_summaries")

# The window a night's consolidation reads unless its config says otherwise.
CONSOLIDATION_LOOKBACK_DAYS = 7


def derived_stamp(conn: sqlite3.Connection, table: str) -> Optional[str]:
    """The lock's checksum for a row a writer is about to compute from vectors, or
    None when the brain has no readable lock or `table` has no `weights_hash` column
    (migration_045 not applied). Never raises: a derived writer on a brain with no
    lock still writes, its row is then UNSTAMPED and the report says so. A writer
    recording a row a PERSON asserted (`link`) must not call this: that strength was
    computed under no model."""
    try:
        if "weights_hash" not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}:
            return None
        return _embedder_lock().resolve(conn)["weights_hash"]
    except Exception:
        return None


def legacy_episodics_held(conn: sqlite3.Connection, flt: ProvenanceFilter,
                          cutoff: str) -> Tuple[int, int]:
    """(all time, since `cutoff`): the active episodic memories on a locked brain that
    hold a vector no stamp verifies and have not been consolidated. They are readable
    (the search set's legacy part), so nothing else says they are out of consolidation;
    a re-embed brings them in. (0, 0) on a brain with no lock, which requires no stamp.
    One statement, no per-row subquery; raises sqlite3.Error when a table is unreadable.
    """
    if not flt.locked:
        return 0, 0
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'consolidation_sources'").fetchall()}
    # IS NOT NULL: `NOT IN` over a set holding one NULL matches nothing.
    done = ("AND m.id NOT IN (SELECT source_memory_id FROM consolidation_sources "
            "WHERE source_memory_id IS NOT NULL)" if tables else "")
    total, recent = conn.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(m.created_at > ?), 0) FROM memories m
            LEFT JOIN {PROVENANCE_TABLE} p ON p.memory_id = m.id AND p.vec_table = ?
            WHERE m.kind = 'episodic' AND m.is_archived = 0
              AND m.id IN (SELECT memory_id FROM {flt.vec_table}) {done}
              AND (p.memory_id IS NULL OR p.weights_hash = ?)""",
        (cutoff, flt.vec_table, WEIGHTS_HASH_UNRESOLVED)).fetchone()
    return int(total), int(recent)


def _consolidation_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    """The report's consolidation block. A figure that could not be read is None and
    says why (`unmeasured`), never 0."""
    out: Dict[str, Any] = {"legacy_held": None, "legacy_held_recent": None,
                           "lookback_days": CONSOLIDATION_LOOKBACK_DAYS, "basis": None, "hint": None}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CONSOLIDATION_LOOKBACK_DAYS)
              ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        flt = provenance_filter(conn)
        out["legacy_held"], out["legacy_held_recent"] = legacy_episodics_held(conn, flt, cutoff)
    except Exception as e:
        code = getattr(e, "code", None) or type(e).__name__
        out["basis"] = f"not measured ({code}: {str(e)[:160]})"
        out["unmeasured"] = f"consolidation: {code}"
        return out
    if not flt.locked:
        out["basis"] = "no lock on this brain: consolidation requires no stamp, so nothing is held"
        return out
    out["basis"] = ("active episodic memories, not yet consolidated, holding a vector with no verified stamp: "
                    "searchable, but outside consolidation until re-embedded")
    if out["legacy_held"]:
        out["hint"] = re_embed_hint(out["legacy_held"], _db_file(conn), flt.tier)
    return out


def derived_layer_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    """For each derived table: rows computed under a checksum OTHER than the lock's
    (`stale`), rows stamped under the lock (`current`), and rows carrying no stamp
    (`unstamped`: written before migration_045, on a brain with no lock, or asserted
    by a person). Raw counts, derived on this connection for this call, from one
    GROUP BY per table. A table without the column is UNMEASURED, never 0.

    With no lock there is no current checksum, so nothing is `current` and every
    stamped row is counted as stale: a stamp names a model this brain no longer
    promises (`basis` says so). Never raises; `stale_derived` is the figure to read,
    `unstamped` sits beside it because a row nobody can vouch for is not current.

    `consolidation` counts the legacy memories outside the nightly consolidation.
    It needs vec0 on the connection; without it the figure is None, never 0."""
    out: Dict[str, Any] = {"lock_weights_hash": None, "basis": None, "tables": {},
                           "stale_derived": 0, "unstamped": 0, "current": 0, "total": 0, "unmeasured": []}
    el = _embedder_lock()
    whash = None
    try:
        whash = el.resolve(conn)["weights_hash"]
        out["lock_weights_hash"] = whash
        out["basis"] = "a stamp other than the lock's is stale; no stamp is unstamped; the lock's is current"
    except el.EmbedderLockError as e:
        out["basis"] = ("no lock on this brain: no checksum is current, every stamped row counts as stale"
                        if e.code == "LOCK_MISSING" else
                        f"lock unreadable ({e.code}): no checksum is current, every stamped row counts as stale")
    except Exception as e:
        out["basis"] = f"lock unreadable ({type(e).__name__}): no checksum is current, every stamped row counts as stale"
    for tab in DERIVED_TABLES:
        blank = {"total": None, "current": None, "stale": None, "unstamped": None}
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tab})").fetchall()}
            if not cols:
                out["tables"][tab] = {"present": False, **blank}
                out["unmeasured"].append(f"{tab}: table absent")
                continue
            if "weights_hash" not in cols:
                n = conn.execute(f"SELECT COUNT(*) FROM {tab}").fetchone()[0]
                out["tables"][tab] = {"present": True, **blank, "total": int(n),
                                      "note": "no weights_hash column: migration_045 not applied; UNMEASURED, not 0"}
                out["unmeasured"].append(f"{tab}: no weights_hash column")
                continue
            groups = conn.execute(f"SELECT weights_hash, COUNT(*) FROM {tab} GROUP BY weights_hash").fetchall()
        except sqlite3.Error as e:
            out["tables"][tab] = {"present": None, **blank, "error": f"{type(e).__name__}: {e}"}
            out["unmeasured"].append(f"{tab}: unreadable ({type(e).__name__})")
            continue
        hist = {h: int(n) for h, n in groups if h}
        total = sum(int(n) for _h, n in groups)
        unstamped = total - sum(hist.values())
        current = hist.get(whash, 0) if whash else 0
        stale = total - unstamped - current
        out["tables"][tab] = {"present": True, "total": total, "current": current, "stale": stale,
                              "unstamped": unstamped, "histogram": hist}
        out["stale_derived"] += stale
        out["unstamped"] += unstamped
        out["current"] += current
        out["total"] += total
    cons = _consolidation_report(conn)
    gap = cons.pop("unmeasured", None)
    if gap:
        out["unmeasured"].append(gap)
    out["consolidation"] = cons
    held = cons["legacy_held"]
    out["line"] = (f"derived layer: {out['stale_derived']} stale (another model's checksum) · "
                   f"{out['unstamped']} unstamped · {out['current']} current of {out['total']} rows "
                   f"across {len(DERIVED_TABLES)} tables"
                   + (f"; {held} legacy episodic memories outside consolidation until re-embedded "
                      f"({cons['legacy_held_recent']} from the last {cons['lookback_days']} days)" if held else "")
                   + (f"; UNMEASURED: {'; '.join(out['unmeasured'])}" if out["unmeasured"] else ""))
    return out


def search_set(conn: sqlite3.Connection) -> Optional[ProvenanceFilter]:
    """THE ONE ENTRY for a per-id or KNN reader. A locked brain consults the
    reader: its typed refusals propagate (NO_ROWS_FOR_LOCK, STAMP_INCONSISTENT,
    LOCK_NOT_SHIPPED, PROVENANCE_UNREADABLE), and None means the brain is EMPTY
    (0/0), so the caller returns nothing honestly. A brain with no lock gets its
    whole-table filter: it made no promise for the reader to hold it to."""
    flt = provenance_filter(conn)
    if not flt.locked:
        return flt
    cov = _lock_coverage(conn, flt)
    return None if cov.empty else cov.filter


def read_vector(conn: sqlite3.Connection, memory_id: int, *,
                flt: Optional[ProvenanceFilter] = None) -> Optional[bytes]:
    """The vector for `memory_id` IF it is in the search set, else None. On a
    locked brain a row stamped under a different checksum reads as absent; a
    legacy row ('unresolved' or unstamped) is read. Membership is decided on the
    memories table first, then the vector is fetched by id: vec0 is never asked
    to combine a point lookup with a second constraint."""
    if flt is None:
        flt = search_set(conn)
        if flt is None:
            return None
    if flt.locked and conn.execute(
            f"SELECT 1 FROM memories m WHERE m.id = ? AND {flt.member_sql('m')}",
            (int(memory_id), *flt.search_set_params)).fetchone() is None:
        return None
    row = conn.execute(f"SELECT embedding FROM {flt.vec_table} WHERE memory_id = ?",
                       (int(memory_id),)).fetchone()
    if row is None or row[0] is None:
        return None
    return bytes(row[0])


def read_vectors(conn: sqlite3.Connection, memory_ids: Iterable[int], *,
                 flt: Optional[ProvenanceFilter] = None) -> Dict[int, bytes]:
    """{memory_id: vector} for every id IN THE SEARCH SET; ids outside it are
    simply absent from the result."""
    ids = [int(i) for i in memory_ids]
    if not ids:
        return {}
    if flt is None:
        flt = search_set(conn)
        if flt is None:
            return {}
    out: Dict[int, bytes] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        if flt.locked:
            ph = ",".join("?" * len(chunk))
            chunk = [int(r[0]) for r in conn.execute(
                f"SELECT m.id FROM memories m WHERE m.id IN ({ph}) AND {flt.member_sql('m')}",
                (*chunk, *flt.search_set_params)).fetchall()]
            if not chunk:
                continue
        ph = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT memory_id, embedding FROM {flt.vec_table} WHERE memory_id IN ({ph})",
            chunk).fetchall()
        for mid, blob in rows:
            if blob is not None:
                out[int(mid)] = bytes(blob)
    return out


def knn(conn: sqlite3.Connection, query_vec: bytes, k: int, *,
        exclude_archived: bool = True, exclude_id: Optional[int] = None,
        flt: Optional[ProvenanceFilter] = None) -> List[Tuple[int, float]]:
    """KNN over THE SEARCH SET: [(memory_id, distance)] ascending by distance.

    The reader is consulted first (`search_set`) unless the caller passes the
    filter it already derived on this call. The set is ONE `memory_id IN (...)`
    constraint inside the KNN, so k rows in the set come back when k exist.
    `exclude_id` (the query's own row) is dropped after an over-fetch of one."""
    if flt is None:
        flt = search_set(conn)
        if flt is None:
            return []
    want = int(k) + (1 if exclude_id is not None else 0)
    in_sql = flt.active_search_set_sql if exclude_archived else flt.search_set_sql
    sql = (f"SELECT emb.memory_id, emb.distance FROM {flt.vec_table} AS emb "
           f"WHERE emb.embedding MATCH ? AND k = ? AND emb.{in_sql} ORDER BY emb.distance")
    rows = conn.execute(sql, (query_vec, want, *flt.search_set_params)).fetchall()
    out = [(int(mid), float(dist)) for mid, dist in rows
           if exclude_id is None or int(mid) != int(exclude_id)]
    return out[:int(k)]


# --------------------------------------------------------------------------
# THE UNIT OF WORK on a locked brain: ONE predicate, read by every batch.
# An active memory is work unless a stamp on the lock's table carries the lock's
# checksum at its width for the text the memory holds NOW, with its vector
# present. An unstamped vector, another checksum, an 'unresolved' stamp and an
# edited memory are all work; a second run after a complete run finds zero.
# A brain with no lock has no checksum to hold a row to: its batches keep their
# own paths (embed_backfill, and re-embed's v2 path), so this refuses LOCK_MISSING.
# --------------------------------------------------------------------------

def _register_sha256(conn: sqlite3.Connection) -> None:
    conn.create_function("vl_sha256", 1, content_hash, deterministic=True)


def _locked_filter(conn: sqlite3.Connection, flt: Optional[ProvenanceFilter]) -> ProvenanceFilter:
    flt = flt or provenance_filter(conn)
    if not flt.locked:
        el = _embedder_lock()
        raise el.EmbedderLockError(
            "LOCK_MISSING", f"this brain has no install lock (it resolves to {flt.vec_table!r}); "
            "the unit of work is counted against a lock's checksum")
    return flt


def needs_embedding_sql(flt: ProvenanceFilter, mem_alias: str = "m", scope: str = "active") -> str:
    """The predicate over `memories {mem_alias}` for a LOCKED filter; bind
    `search_set_params` for its three `?`. `scope` 'active' is the active
    memories; 'vectored' adds any memory holding a vector in the lock's table."""
    if not flt.locked:
        raise ValueError("needs_embedding_sql needs a locked filter")
    if scope not in ("active", "vectored"):
        raise ValueError(f"scope must be 'active' or 'vectored', got {scope!r}")
    if scope == "active":
        population = f"COALESCE({mem_alias}.is_archived, 0) = 0"
    else:
        population = (f"(COALESCE({mem_alias}.is_archived, 0) = 0 OR EXISTS "
                      f"(SELECT 1 FROM {flt.vec_table} v0 WHERE v0.memory_id = {mem_alias}.id))")
    return (f"{population} AND NOT EXISTS ("
            f"SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = {mem_alias}.id "
            f"AND p.vec_table = ? AND p.weights_hash = ? AND p.dim = ? "
            f"AND p.input_kind = 'text' AND p.content_hash = vl_sha256({mem_alias}.content) "
            f"AND p.memory_id IN (SELECT memory_id FROM {flt.vec_table}))")


def count_pending_embeddings(conn: sqlite3.Connection, *, flt: Optional[ProvenanceFilter] = None,
                             scope: str = "active") -> int:
    """How many memories are work on a locked brain, counted now."""
    flt = _locked_filter(conn, flt)
    _register_sha256(conn)
    return int(conn.execute(
        f"SELECT COUNT(*) FROM memories m WHERE {needs_embedding_sql(flt, scope=scope)}",
        flt.search_set_params).fetchone()[0])


def pending_embeddings(conn: sqlite3.Connection, *, flt: Optional[ProvenanceFilter] = None,
                       limit: Optional[int] = None, scope: str = "active") -> List[Tuple[int, str]]:
    """[(memory_id, content)] for the work on a locked brain, in id order."""
    flt = _locked_filter(conn, flt)
    _register_sha256(conn)
    sql = (f"SELECT m.id, m.content FROM memories m WHERE {needs_embedding_sql(flt, scope=scope)} "
           f"ORDER BY m.id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(int(r[0]), r[1] or "") for r in conn.execute(sql, flt.search_set_params).fetchall()]


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

#!/usr/bin/env python3
"""embed_resolver.py — ONE place that answers "which embedding table, how wide".

WHY THIS EXISTS
    On 2026-05-28 (the Great Centralization) the embedding write path moved from
    `memory_embeddings` (nomic-768) to `memory_embeddings_v2` (jina-1024). Four live
    readers were never moved with it and have returned NOTHING ever since:

        sleep_loop.consolidate_episodics    0 of 7,864 episodic memories in 93 days
        temporal_edges (3 sites)            12 vs 9,134 — sees 0.13% of its input
        pre_cognitive_recall._vector_...    `try` reads v1; `except` falls back to v1 AGAIN
        whisper_network.generate_weekly_...  writes to the table nothing reads

    `claude_memory.py` already resolves this correctly (`_active_embed_table`,
    `_active_embed_dim`), and `_active_embed_dim`'s own docstring already says
    "use both together whenever unpacking raw embedding blobs". Nothing bound it.

THE INSTALL LOCK IS READ FIRST (owner decision D2)
    A brain installed with an install lock (<data-dir>/embedder.lock.json) takes its
    table and width from THE LOCK, and a metadata row cannot move it: an UPDATE of
    `active_embedding_table` would otherwise re-point recall at another model's
    vectors without re-embedding one of them. Only a brain with NO lock beside its
    database -- every brain installed before the lock existed -- is read from
    metadata, as before. A malformed lock is never "absent": it refuses, typed.

WHY NOT JUST IMPORT claude_memory
    `claude_memory` imports sleep_loop, temporal_edges and pre_cognitive_recall.
    Importing it back from those modules is a CIRCULAR IMPORT. This module imports
    only `embedder_lock`, which imports nothing from the package, so anyone may
    import it.

TWO DELIBERATE DIFFERENCES FROM `claude_memory._active_embed_model()`
    1. It resolves from THE CALLER'S CONNECTION, not a module-level `DB_PATH`.
       The original answers about the live brain even when the caller is operating on
       a different database (a test copy, a teammate install, a restored snapshot).
    2. **It REFUSES rather than defaulting to the dormant table.** The original does
       `model = "nomic"` then `except Exception: pass` — so a failed metadata read
       silently selects the table that has been dead for 100 days. `architecture-009`:
       a trust bit's default must be the REFUSING value. Silence is the failure mode
       this brain cannot see; it must not be the default.
"""
from __future__ import annotations
import os as _os
import sqlite3
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))
if _here not in _sys.path:
    _sys.path.insert(0, _here)
import embedder_lock  # noqa: E402  (stdlib-only; imports nothing from the package)

NOMIC_DIM = 768
JINA_DIM = 1024
QWEN_DIM = 1024
_V3 = "memory_embeddings_v3"      # Qwen3-Embedding-0.6B, apache-2.0 -- the SHIPPED tier
_V2 = "memory_embeddings_v2"      # jina-v5, cc-by-nc-4.0 -- Drew's machine only
_V1 = "memory_embeddings"         # nomic-v1.5, apache-2.0 -- the CPU tier
# Bijective on purpose: one table per model, so reverse-mapping a table name back
# to a model can never be ambiguous. v2 and v3 are both 1024 wide and are NOT
# interchangeable -- same dimension is not the same space.
_DIM_FOR = {_V3: QWEN_DIM, _V2: JINA_DIM, _V1: NOMIC_DIM}


class EmbedTierUnresolved(RuntimeError):
    """Raised instead of guessing. Loud beats a silent wrong table.
    `.code` carries the embedder_lock refusal when there is one."""

    def __init__(self, message: str, code: str = "LOCK_MISSING"):
        super().__init__(message)
        self.code = code


def _from_lock(conn: sqlite3.Connection):
    """(table, dim) from the install lock beside `conn`'s database; None when there
    is NO lock (a pre-lock brain). Any other lock defect refuses, typed."""
    try:
        lock = embedder_lock.resolve(conn)
    except embedder_lock.EmbedderLockError as e:
        if e.code == "LOCK_MISSING":
            return None
        raise EmbedTierUnresolved(str(e), code=e.code) from e
    table = str(lock["vec_table"]).strip()
    dim = int(lock["dim"])
    if table not in _DIM_FOR:
        raise EmbedTierUnresolved(
            f"the lock at {lock.get('_path')} names vec_table {table!r}, which this engine "
            f"does not know how to create or search (known: {sorted(_DIM_FOR)})",
            code="LOCK_MALFORMED")
    if _DIM_FOR[table] != dim:
        raise EmbedTierUnresolved(
            f"the lock at {lock.get('_path')} says {table!r} is {dim} wide; this engine builds "
            f"it {_DIM_FOR[table]} wide. Same name, different space: refusing.",
            code="LOCK_MALFORMED")
    return table, dim


def resolve(conn: sqlite3.Connection) -> tuple[str, int]:
    """Return (table, dim) for the database `conn` is attached to: from the install
    lock beside it when there is one, else (a pre-lock brain) from metadata.

    Raises EmbedTierUnresolved rather than falling back to the dormant table.
    """
    from_lock = _from_lock(conn)
    if from_lock is not None:
        return from_lock
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='active_embedding_table'"
        ).fetchone()
    except sqlite3.Error as e:                      # never a bare except (VAEL R5)
        raise EmbedTierUnresolved(f"metadata unreadable: {e}") from e

    if not row or not row[0]:
        raise EmbedTierUnresolved(
            "metadata.active_embedding_table is absent or empty — refusing to guess. "
            "Defaulting here would select the table that has been dead since 2026-05-28."
        )
    table = str(row[0]).strip()
    if table not in _DIM_FOR:
        raise EmbedTierUnresolved(f"unknown embedding table {table!r}")
    return table, _DIM_FOR[table]


def resolve_table(conn: sqlite3.Connection) -> str:
    return resolve(conn)[0]


def resolve_dim(conn: sqlite3.Connection) -> int:
    return resolve(conn)[1]


if __name__ == "__main__":
    # CRUCIBLE-3 D8-F11 (seat 221): `Path` was used here and imported nowhere, so
    # the DOCUMENTED no-argument form -- `python3 organs/embed_resolver.py` -- died
    # on `NameError: name 'Path' is not defined` before printing anything. The
    # argument form worked, which is why it shipped: the author ran the verb they
    # were debugging with, never the one the module tells a reader to run.
    #
    # Imported HERE, inside the __main__ guard, not at module scope: this module's
    # whole contract (see the header) is that it imports nothing from the package
    # so that anyone may import it without a cycle. A self-test's convenience does
    # not get to widen the import surface of the thing it tests.
    import sys
    from pathlib import Path
    db = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent.parent / "claude_memory.db")
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    print("  resolve() on the live brain:", resolve(c))

    # The refusal is exercised, not asserted: a database with no metadata row.
    m = sqlite3.connect(":memory:")
    m.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
    try:
        resolve(m)
        print("  REFUSAL ROW: FAILED — it guessed")
        raise SystemExit(1)
    except EmbedTierUnresolved as e:
        print(f"  REFUSAL ROW: refused as designed -> {str(e)[:60]}...")

    # And a row naming a table we do not know.
    m.execute("INSERT INTO metadata VALUES ('active_embedding_table','memory_embeddings_v9')")
    try:
        resolve(m)
        print("  UNKNOWN ROW: FAILED — it accepted an unknown table")
        raise SystemExit(1)
    except EmbedTierUnresolved:
        print("  UNKNOWN ROW: refused as designed")
    c.close(); m.close()
    print("  self-test: PASS")

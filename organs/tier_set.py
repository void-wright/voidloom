#!/usr/bin/env python3
"""tier set -- move a brain to another embedding model by RE-EMBEDDING every memory.

    voidloom tier set <nomic|qwen> [--plan] [--batch-size N]
    voidloom tier status
    voidloom tier rollback
    voidloom tier cleanup --confirm <old vector table>

(or `python3 organs/tier_set.py --db <brain>/claude_memory.db set qwen`, the same verbs.)

WHY THIS IS THE ONLY ROUTE. A vector is only comparable with vectors from the SAME
model: two models can share a width (qwen and jina are both 1024 floats) and still
live in different spaces, and a query from one searched against the other's table
returns noise with no error anywhere. So a brain changes model the only honest way
there is: every memory is embedded under the new model into that model's OWN table,
and the brain switches only when that table is complete. `init` never re-points a
brain (embedder_lock.write_lock refuses it); this verb is the one that does.

THE ORDER, and what each step guarantees:
  1. resolve the TARGET from the shipped catalogue (organs/embedders.lock.json).
     Shipped targets only -- nomic (cpu tier) and qwen (gpu tier); anything else is
     LOCK_NOT_SHIPPED. The SOURCE may be any tier the brain has, jina included.
  2. ask the loader for the target model (fetch at the catalogue pin when online,
     checksum the bytes) BEFORE any write: a refusal (SNAPSHOT_ABSENT,
     WEIGHTS_MISMATCH, ...) leaves the database exactly as it was found.
  3. BUILD: every memory -- archived ones included, so an un-archived memory is
     never left without a vector -- that lacks a vector in the target table with a
     stamp naming the target's COMPUTED checksum for its CURRENT text is embedded and
     stamped (memory_write.attach_under_lock), committed per batch. The old tier
     keeps serving throughout: nothing it reads is touched. Kill it at any point and
     re-run: finished rows are reused, the rest is work again. A batch that runs out of
     memory on the accelerator is retried one item at a time, and a memory too long
     to fit alone is embedded from its head (the receipt counts them). A consolidated memory
     takes the average of its members' new vectors, not a vector of its text.
  4. FLIP, inside one write transaction (BEGIN IMMEDIATE, so no store lands
     between the check and the switch): embed whatever a concurrent writer added,
     require 100% coverage, write the PRE-IMAGE (the previous lock -- or its
     absence -- and the previous metadata rows) to embedder.lock.preimage.json,
     move the metadata rows, replace the lock atomically (temp, fsync, rename),
     commit; then append the receipt to embedder.lock.history.jsonl.
     The metadata rows move WITH the lock because some readers still read the
     metadata row and not the lock; a brain whose lock and rows disagree would put
     those readers on another model's table. A crash between the lock rename and
     the commit leaves lock=target, rows=old; re-running `tier set` finishes it.
  5. The OLD VECTORS ARE KEPT: they are the rollback path. `tier rollback` restores
     the pre-image (lock and rows) and reports how many memories, written after the
     switch, have no vector in the restored table. Only `tier cleanup --confirm
     <table>` removes old vectors, with its own receipt; after it, rollback refuses.

NOT done by this verb, and said in every receipt: the derived layer (similarity
links, communities, summaries) was computed in the previous model's space and is
recomputed by the sleep loop on its own schedule, not here.

Stdlib-only at import (the model loader is imported when a build needs it), like
every organ the CLI passes a verb to.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ORGANS = Path(__file__).resolve().parent
if str(_ORGANS) not in sys.path:
    sys.path.insert(0, str(_ORGANS))

import embedder_lock  # noqa: E402  (stdlib-only; imports nothing from the package)

# The engine's tier keys (claude_memory.EMBED_TIERS) that the catalogue ships, and
# the catalogue tier each one is.
SHIPPED_TARGETS = {"nomic": "cpu", "qwen": "gpu"}
CATALOGUE_TO_ENGINE = {v: k for k, v in SHIPPED_TARGETS.items()}


def re_embed_under_lock(db: Path, *, batch_size: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """`re-embed` under the brain's OWN model: embed every memory that lacks a vector stamped
    under it (new, edited, legacy 'unresolved', or missing rows) and change nothing else.

    A LOCKED brain: the same core as `tier set` with the target equal to the lock.
    A brain with NO lock that embeds under a shipped tier (it predates the lock): the shipped
    catalogue entry for the tier it already uses. The vectors and stamps move; the metadata
    rows do not, and no lock is written: pinning a brain is `init`'s act, not this verb's.
    Returns None only for a brain that really embeds under a tier the catalogue does not ship
    (jina): the caller keeps its own path for that tier. A brain whose tier cannot be read
    refuses, TIER_UNRESOLVED: no lock is not the same fact as jina."""
    db = Path(db).resolve()
    lock = _installed_lock(db)
    if lock is None:
        engine_tier = _prelock_engine_tier(db)
        if engine_tier is None:
            return None
        return _re_embed_prelock(db, engine_tier, batch_size or BATCH_SIZE)
    engine_tier = CATALOGUE_TO_ENGINE.get(lock["tier"])
    if engine_tier is None:
        raise TierRefused("LOCK_NOT_SHIPPED", f"the install lock names tier {lock['tier']!r}, which this "
                          f"package does not ship ({sorted(CATALOGUE_TO_ENGINE)})")
    return set_tier(db, engine_tier, batch_size=batch_size or BATCH_SIZE)


# Every vector table the engine knows (claude_memory.EMBED_TIERS), for cleanup.
KNOWN_TABLES = ("memory_embeddings", "memory_embeddings_v2", "memory_embeddings_v3")
METADATA_KEYS = ("active_embedding_table", "active_embedding_model", "active_embedding_dim")
PREIMAGE_NAME = "embedder.lock.preimage.json"
PREIMAGE_SCHEMA = "voidloom.tier-set-preimage/1"
ORGAN = "tier_set"
BATCH_SIZE = 16
DERIVED_LAYER_NOTE = ("not recomputed here: similarity links, communities and summaries were derived in the "
                      "previous model's space; the sleep loop recomputes them on its own schedule")
USAGE = "voidloom tier set <nomic|qwen> [--plan]  (or: python3 organs/tier_set.py --db <brain>/claude_memory.db set <nomic|qwen>)"


class TierRefused(RuntimeError):
    """A typed refusal; run() turns it into an error envelope and main() exits 1."""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, dict(detail)

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "error", "error": self.code, "message": self.message, **self.detail}


# ----------------------------------------------------------------------------
# The two seams a test replaces: load the target model, encode a batch with it.
# ----------------------------------------------------------------------------

def _load(lock: Dict[str, Any]) -> None:
    import embedding_upgrade
    embedding_upgrade.get_locked_embedder(lock)


def _encode(texts: List[str], lock: Dict[str, Any]) -> List[bytes]:
    import embedding_upgrade
    return embedding_upgrade.encode_locked_batch(texts, is_query=False, lock=lock)


# ----------------------------------------------------------------------------
# Facts about the brain
# ----------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(db: Path, *, readonly: bool = False) -> sqlite3.Connection:
    import sqlite_vec
    import memory_write
    if readonly:
        conn = sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True, timeout=60.0)
    else:
        conn = sqlite3.connect(str(db), timeout=60.0)
    conn.isolation_level = None            # transactions are explicit in this module
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.create_function("vl_sha256", 1, memory_write.content_hash, deterministic=True)
    return conn


def target_lock(tier: str) -> Dict[str, Any]:
    """The lock the brain will carry after the switch, from the shipped catalogue."""
    key = (tier or "").strip().lower()
    cat = SHIPPED_TARGETS.get(key)
    tiers = embedder_lock.catalogue_tiers()
    if cat is None or cat not in tiers:
        raise TierRefused(
            "LOCK_NOT_SHIPPED",
            f"{tier!r} is not a tier this package ships; a brain can be moved to "
            f"{sorted(SHIPPED_TARGETS)} only (any tier it has now may be the source)",
            requested=tier, shipped=sorted(SHIPPED_TARGETS), usage=USAGE)
    lock = dict(tiers[cat])
    lock["tier"] = cat
    return lock


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def _metadata(conn: sqlite3.Connection) -> Dict[str, Optional[str]]:
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM metadata WHERE key IN (?, ?, ?)", METADATA_KEYS).fetchall())
    except sqlite3.Error:
        rows = {}
    return {k: rows.get(k) for k in METADATA_KEYS}


def _installed_lock(db: Path) -> Optional[Dict[str, Any]]:
    """The installed lock, None when the brain predates locks (or embeds under jina).
    A malformed lock refuses: it is never read as absent."""
    try:
        return embedder_lock.resolve(db)
    except embedder_lock.EmbedderLockError as e:
        if e.code == "LOCK_MISSING":
            return None
        raise TierRefused(e.code, str(e), **{k: v for k, v in e.detail.items()})


def _prelock_engine_tier(db: Path) -> Optional[str]:
    """The shipped tier a brain with no lock already uses (the engine's key), or None when
    the table it resolves to belongs to no catalogue tier. Refuses when it resolves to nothing."""
    import embed_resolver
    conn = None
    try:
        conn = _open(db, readonly=True)
        table, _dim = embed_resolver.resolve(conn)
    except Exception as e:                  # noqa: BLE001 -- reported, never guessed
        raise TierRefused("TIER_UNRESOLVED", f"the brain's current tier cannot be read: {e}")
    finally:
        if conn is not None:
            conn.close()
    for cat, entry in embedder_lock.catalogue_tiers().items():
        if entry["vec_table"] == table and cat in CATALOGUE_TO_ENGINE:
            return CATALOGUE_TO_ENGINE[cat]
    return None


def _source(conn: sqlite3.Connection, db: Path) -> Dict[str, Any]:
    import embed_resolver
    lock = _installed_lock(db)
    try:
        table, dim = embed_resolver.resolve(conn)
    except Exception as e:                  # noqa: BLE001 -- reported, never guessed
        raise TierRefused("TIER_UNRESOLVED", f"the brain's current tier cannot be read: {e}")
    return {"vec_table": table, "dim": int(dim),
            "lock_tier": lock["tier"] if lock else None,
            "weights_hash": lock["weights_hash"] if lock else None,
            "metadata": _metadata(conn)}


# One uncorrelated `IN (SELECT memory_id FROM <vec table>)`, never a per-row EXISTS: a correlated look-up does
# one vec0 point read per memory (~0.4 ms each; measured 4.19 s for this query on a 31,904-memory brain, 0.091 s this way). The
# vec0 `memory_id` is its INTEGER PRIMARY KEY, so it is never NULL and NOT IN cannot go empty on a NULL.
_PENDING_SQL = """
    SELECT m.id FROM memories m
     WHERE NOT EXISTS (
           SELECT 1 FROM embedding_provenance p
            WHERE p.memory_id = m.id AND p.vec_table = :t AND p.weights_hash = :h
              AND p.dim = :d
              AND (p.input_kind = 'centroid'
                   OR (p.input_kind = 'text' AND p.content_hash = vl_sha256(m.content)))
              AND m.id IN (SELECT memory_id FROM {t}))
     ORDER BY m.id
"""


def _stamps_readable(conn: sqlite3.Connection) -> bool:
    """False for a brain with no sidecar, or one from before stamps carried a checksum: none of
    its rows can name a checksum, so every memory is work (a real run rebuilds the sidecar first)."""
    return "weights_hash" in {r[1] for r in conn.execute("PRAGMA table_info(embedding_provenance)")}


def pending_ids(conn: sqlite3.Connection, lock: Dict[str, Any]) -> List[int]:
    """Every memory (archived included) WITHOUT a vector in the target table whose
    stamp names the target's checksum, width and the memory's CURRENT text, or, for a
    consolidated memory, a centroid of its members under that checksum. A legacy
    'unresolved' stamp never matches: bytes nobody checked are re-embedded."""
    t = lock["vec_table"]
    if not _table_exists(conn, t) or not _stamps_readable(conn):
        return [r[0] for r in conn.execute("SELECT id FROM memories ORDER BY id")]
    return [r[0] for r in conn.execute(
        _PENDING_SQL.format(t=t),
        {"t": t, "h": lock["weights_hash"], "d": int(lock["dim"])})]


def _population(conn: sqlite3.Connection) -> Dict[str, int]:
    total, active = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(is_archived, 0) = 0), 0) FROM memories").fetchone()
    return {"memories": int(total), "active": int(active), "archived": int(total) - int(active)}


def _env_warning(target_engine_tier: str) -> Optional[str]:
    env = (os.environ.get("WIZARD_EMBED_MODEL") or "").strip().lower()
    if env and env != target_engine_tier:
        return (f"WIZARD_EMBED_MODEL={env!r} is set in this environment and names another tier; after the "
                f"switch the engine refuses it (the lock is the promise). Set it to {target_engine_tier!r} "
                "or unset it wherever it is exported.")
    return None


# ----------------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    embedder_lock._fsync_dir(path.parent)


def _receipt(data_dir: Path, receipt: Dict[str, Any]) -> None:
    with open(data_dir / embedder_lock.LOCK_HISTORY_NAME, "a", encoding="utf-8") as h:
        h.write(json.dumps(receipt, sort_keys=True) + "\n")
        h.flush()
        os.fsync(h.fileno())


def _ensure_sidecar(conn: sqlite3.Connection) -> None:
    """A brain from before the sidecar, or from before stamps carried a checksum, gets the
    current shape (existing rows carried forward as 'unresolved') before the first stamp."""
    import memory_write
    try:
        memory_write.ensure_provenance_table(conn)
    except memory_write.ProvenanceUnreadable as e:
        raise TierRefused("PROVENANCE_UNREADABLE", str(e))


def _ensure_target_table(conn: sqlite3.Connection, lock: Dict[str, Any]) -> None:
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {lock['vec_table']} USING vec0("
                 f"memory_id INTEGER PRIMARY KEY, embedding float[{int(lock['dim'])}])")


def _is_out_of_memory(e: BaseException) -> bool:
    import voidloom_device
    # torch's CPU allocator says "not enough memory", which the device layer's test does not match
    return voidloom_device.is_out_of_memory(e) or "not enough memory" in str(e).lower()


def _release_accelerator_cache() -> None:
    import voidloom_device
    voidloom_device.empty_cache()


# A memory that does not fit alone is embedded from its head. The model already cuts a long
# text at its own window; this only cuts sooner, on a device too small for that window. Measured
# on an 11 GB card: at the model's window (32,768 tokens) one text asked for 64 GiB, so no retry
# at full length can ever finish. Both numbers are INVENTED, NOT TUNED: the first retry's length,
# and the length below which the head says too little to stand for the memory.
SHORTEN_START_CHARS = 32000
SHORTEN_FLOOR_CHARS = 1000


class _Outcome:
    """What a build could not do in full: `failed` id -> why (no vector), `shortened` id -> the
    characters its vector was embedded from."""

    def __init__(self) -> None:
        self.failed: Dict[int, str] = {}
        self.shortened: Dict[int, int] = {}


def _encode_alone(mid: int, text: str, lock: Dict[str, Any], outcome: _Outcome) -> Optional[bytes]:
    """One text, full length first, then its head, halving down to the floor. The stamp still
    names the whole text: this is that memory's vector, and the receipt counts the shortened."""
    n = len(text)
    while True:
        try:
            vec = _encode([text[:n]], lock)[0]
            if n < len(text):
                outcome.shortened[mid] = n
            return vec
        except (RuntimeError, MemoryError) as e:
            if not _is_out_of_memory(e):
                raise
            _release_accelerator_cache()
        n = SHORTEN_START_CHARS if n > SHORTEN_START_CHARS else n // 2
        if n < SHORTEN_FLOOR_CHARS:
            outcome.failed[mid] = f"out of memory embedding {len(text)} characters, and its head alone"
            return None


def _encode_rows(rows: List[Any], texts: List[str], lock: Dict[str, Any], outcome: _Outcome) -> List[Any]:
    """[(row, text, vector)] for `rows`. A batch that runs out of memory on the accelerator is
    retried one item at a time, so one long memory cannot stop the whole run at the same batch
    on every rerun. Any other error is the caller's: it is not a property of one long memory."""
    try:
        return list(zip(rows, texts, _encode(texts, lock)))
    except (RuntimeError, MemoryError) as e:
        if not _is_out_of_memory(e):
            raise
    _release_accelerator_cache()
    done = []
    for row, text in zip(rows, texts):
        vec = _encode_alone(int(row[0]), text, lock, outcome)
        if vec is not None:
            done.append((row, text, vec))
    return done


# A consolidated memory's vector is the average of its members', not an embedding of its text.
# The text is a template ("Distilled from 3 episodic memories..."): measured on a copy of a
# 25,009-vector brain, 240 queries from 80 such memories' members, the centroid ranked the memory
# in the top 10 for 235 and a text vector for 62 (median rank 2 against 52.5). So a change of
# vectors rebuilds the average from the members' vectors under the new weights.
CONSOLIDATION_PROJECT = "sleep-loop-consolidation"


def _member_ids(raw: Any) -> List[int]:
    try:
        ids = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return sorted({int(i) for i in ids if isinstance(i, int)}) if isinstance(ids, list) else []


def derived_members(conn: sqlite3.Connection, ids: List[int]) -> Dict[int, List[int]]:
    """{memory id: its member ids} for those of `ids` whose vector is a centroid: by a centroid
    stamp on any table, else (a row from before stamps) by the consolidation that wrote it."""
    out: Dict[int, List[int]] = {}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
    stamped = _stamps_readable(conn)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        if stamped:
            for mid, inputs in conn.execute(
                    f"SELECT memory_id, inputs FROM embedding_provenance WHERE input_kind = 'centroid' "
                    f"AND memory_id IN ({marks})", chunk).fetchall():
                out.setdefault(int(mid), _member_ids(inputs))
        if {"source_project", "derived_from"} <= cols:
            for mid, raw in conn.execute(
                    f"SELECT id, derived_from FROM memories WHERE source_project = ? AND id IN ({marks})",
                    [CONSOLIDATION_PROJECT, *chunk]).fetchall():
                out.setdefault(int(mid), _member_ids(raw))
    return {m: v for m, v in out.items() if v}


def _verified_vectors(conn: sqlite3.Connection, ids: List[int], lock: Dict[str, Any]) -> Dict[int, bytes]:
    """The vectors of `ids` in the lock's table whose stamp carries its checksum at its width."""
    t, marks = lock["vec_table"], ",".join("?" * len(ids))
    good = {r[0] for r in conn.execute(
        f"SELECT memory_id FROM embedding_provenance WHERE vec_table = ? AND weights_hash = ? AND dim = ? "
        f"AND memory_id IN ({marks})", [t, lock["weights_hash"], int(lock["dim"]), *ids]).fetchall()}
    return {int(m): bytes(v) for m, v in conn.execute(
        f"SELECT memory_id, embedding FROM {t} WHERE memory_id IN ({marks})", ids).fetchall() if m in good}


def _write_centroid(conn: sqlite3.Connection, memory_id: int, members: List[int], vectors: List[bytes],
                    lock: Dict[str, Any], organ: str) -> None:
    """A rebuilt centroid and its stamp, both or neither. Every member is verified under `lock`,
    which need not be the installed one: `tier set` builds the target while the old tier serves."""
    import memory_write
    from datetime import datetime as _dt
    from sleep_loop import _centroid_embedding
    table, dim = lock["vec_table"], int(lock["dim"])
    row = {"memory_id": int(memory_id), "vec_table": table, "model_id": lock["model_id"],
           "model_ref": lock["revision"], "weights_hash": lock["weights_hash"], "dim": dim,
           "content_hash": memory_write.centroid_content_hash(vectors),
           "embedded_at": _dt.now(timezone.utc).isoformat(timespec="seconds"), "organ": organ,
           "input_kind": "centroid", "inputs": json.dumps(members)}
    cols = memory_write.PROVENANCE_COLUMNS
    conn.execute("SAVEPOINT rebuilt_centroid")
    try:
        conn.execute(f"DELETE FROM {table} WHERE memory_id = ?", (int(memory_id),))
        conn.execute(f"INSERT INTO {table} (memory_id, embedding) VALUES (?, ?)",
                     (int(memory_id), _centroid_embedding(vectors)))
        conn.execute(f"INSERT OR REPLACE INTO {memory_write.PROVENANCE_TABLE} ({', '.join(cols)}) "
                     f"VALUES ({', '.join(':' + c for c in cols)})", row)
        conn.execute("RELEASE SAVEPOINT rebuilt_centroid")
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT rebuilt_centroid")
        conn.execute("RELEASE SAVEPOINT rebuilt_centroid")
        raise


def rebuild_centroids(conn: sqlite3.Connection, derived: Dict[int, List[int]], lock: Dict[str, Any], *,
                      organ: str = ORGAN) -> Dict[str, Any]:
    """Rebuild each centroid whose members all hold a verified vector under `lock`.
    {'rebuilt': n, 'waiting': ids whose members are not all there yet, 'orphaned': ids with a
    member that no longer exists, so no average can be rebuilt}."""
    out: Dict[str, Any] = {"rebuilt": 0, "waiting": [], "orphaned": []}
    for mid in sorted(derived):
        members = derived[mid]
        vecs = _verified_vectors(conn, members, lock)
        if len(vecs) == len(members):
            _write_centroid(conn, mid, members, [vecs[i] for i in members], lock, organ)
            out["rebuilt"] += 1
            continue
        marks = ",".join("?" * len(members))
        alive = conn.execute(f"SELECT COUNT(*) FROM memories WHERE id IN ({marks})", members).fetchone()[0]
        out["waiting" if alive == len(members) else "orphaned"].append(mid)
    return out


def _embed_pending(conn: sqlite3.Connection, ids: List[int], lock: Dict[str, Any], *, batch_size: int,
                   in_txn: bool, outcome: Optional[_Outcome] = None) -> int:
    """Embed `ids`: text rows first, then the centroids over them. A centroid with a member that
    is gone takes a text vector, the only one it can still have."""
    derived = derived_members(conn, ids)
    plain = [i for i in ids if i not in derived]
    done = _embed_ids(conn, plain, lock, batch_size=batch_size, in_txn=in_txn, outcome=outcome)
    if derived:
        res = rebuild_centroids(conn, derived, lock)
        done += res["rebuilt"]
        if res["orphaned"]:
            done += _embed_ids(conn, res["orphaned"], lock, batch_size=batch_size, in_txn=in_txn, outcome=outcome)
    return done


def _embed_ids(conn: sqlite3.Connection, ids: List[int], lock: Dict[str, Any], *,
               batch_size: int, in_txn: bool, outcome: Optional[_Outcome] = None) -> int:
    """Embed `ids` under `lock`. Outside a transaction: one transaction per batch
    (kill-and-resume safe). Inside one (the flip): no commits here. Ids that could not
    be embedded are recorded in `outcome`, and the rest of the batch still lands."""
    import memory_write
    outcome = _Outcome() if outcome is None else outcome
    done = 0
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(f"SELECT id, content FROM memories WHERE id IN ({marks}) ORDER BY id",
                            chunk).fetchall()
        texts = [c or "" for _i, c in rows]
        encoded = _encode_rows(rows, texts, lock, outcome) if rows else []
        if not in_txn:
            conn.execute("BEGIN IMMEDIATE")
        try:
            for (mid, _c), text, vec in encoded:
                memory_write.attach_under_lock(conn, mid, text, vec, lock=lock, organ=ORGAN)
            if not in_txn:
                conn.execute("COMMIT")
        except BaseException:
            if not in_txn:
                conn.execute("ROLLBACK")
            raise
        done += len(encoded)
    return done


def _converge(conn: sqlite3.Connection, lock: Dict[str, Any], batch_size: int,
              outcome: Optional[_Outcome] = None) -> int:
    """Embed the pending rows until a pass finds nothing new; refuse, naming them, when a
    row could not be embedded. Finished rows are committed and reused by the next run."""
    built = 0
    outcome = _Outcome() if outcome is None else outcome
    failed = outcome.failed
    for _pass in range(FLIP_ATTEMPTS):
        todo = [i for i in pending_ids(conn, lock) if i not in failed]
        more = _embed_pending(conn, todo, lock, batch_size=batch_size, in_txn=False, outcome=outcome)
        built += more
        if not more:
            break
    if failed:
        ids = sorted(failed)
        raise TierRefused(
            "COVERAGE_INCOMPLETE",
            f"{len(ids)} memor(ies) could not be embedded on this device (first: {failed[ids[0]]}); "
            f"{built} were embedded and are kept for the next run, nothing switched. Free the "
            "accelerator's memory and re-run.",
            unembedded=ids[:50], unembedded_total=len(ids), embedded=built)
    return built


def _log_start(conn: sqlite3.Connection, lock: Dict[str, Any], total: int, *,
               notes: str = "tier set") -> Optional[str]:
    if not _table_exists(conn, "embedding_upgrade_log"):
        return None
    cols = {r[1] for r in conn.execute("PRAGMA table_info(embedding_upgrade_log)")}
    started = _now()
    if "weights_hash" in cols:
        conn.execute("INSERT INTO embedding_upgrade_log (started_at, model_name, embedding_dim, memories_total, "
                     "status, weights_hash, notes) VALUES (?, ?, ?, ?, 'in_progress', ?, ?)",
                     (started, lock["model_id"], int(lock["dim"]), total, lock["weights_hash"], notes))
    else:
        conn.execute("INSERT INTO embedding_upgrade_log (started_at, model_name, embedding_dim, memories_total, "
                     "status, notes) VALUES (?, ?, ?, ?, 'in_progress', ?)",
                     (started, lock["model_id"], int(lock["dim"]), total, notes))
    return started


def _log_failed(conn: sqlite3.Connection, started: Optional[str], notes: str, message: str) -> None:
    """A run that stopped short says so in the history instead of reading in_progress for ever."""
    if started is not None:
        conn.execute("UPDATE embedding_upgrade_log SET completed_at = ?, status = 'failed', error_message = ? "
                     "WHERE started_at = ? AND notes = ?", (_now(), message, started, notes))


def _set_metadata(conn: sqlite3.Connection, values: Dict[str, Optional[str]]) -> None:
    if not _table_exists(conn, "metadata"):      # nothing to move: readers of these rows read the lock here
        return
    for k in METADATA_KEYS:
        if values.get(k) is None:
            conn.execute("DELETE FROM metadata WHERE key = ?", (k,))
        else:
            conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (k, values[k]))


# ----------------------------------------------------------------------------
# Verbs
# ----------------------------------------------------------------------------

def _coverage(conn: sqlite3.Connection, lock: Dict[str, Any]) -> Dict[str, int]:
    """Two-sided coverage of the target table, by queries INDEPENDENT of pending_ids:
    `total` counts every memory with no WHERE; `matched` counts memories holding a
    target vector whose stamp names the target checksum and width, and its current text
    or the centroid of its members;
    `stamps_without_memory` is the other side. The switch needs matched == total and
    no stamp without its memory."""
    t = lock["vec_table"]
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    matched = conn.execute(
        f"SELECT COUNT(*) FROM memories m JOIN embedding_provenance p ON p.memory_id = m.id "
        f" WHERE p.vec_table = ? AND p.weights_hash = ? AND p.dim = ? "
        f"   AND (p.input_kind = 'centroid' OR (p.input_kind = 'text' AND p.content_hash = vl_sha256(m.content))) "
        f"   AND m.id IN (SELECT memory_id FROM {t})",
        (t, lock["weights_hash"], int(lock["dim"]))).fetchone()[0]
    orphan_stamps = conn.execute(
        "SELECT COUNT(*) FROM embedding_provenance WHERE vec_table = ? AND memory_id NOT IN "
        "(SELECT id FROM memories)", (t,)).fetchone()[0]
    return {"total": int(total), "matched": int(matched), "stamps_without_memory": int(orphan_stamps)}


def _refuse_same_table_change(src: Dict[str, Any], lock: Dict[str, Any]) -> None:
    """The target table IS the table serving now, under other weights (a catalogue
    revision moved, or a lockless brain whose rows name another model): re-embedding
    in place would rewrite the live table while it serves and destroy the rollback
    path. Same table + same weights is a re-convergence and proceeds."""
    if src["vec_table"] != lock["vec_table"]:
        return
    other_hash = src["weights_hash"] and src["weights_hash"] != lock["weights_hash"]
    model = src["metadata"].get("active_embedding_model")
    other_model = src["weights_hash"] is None and model and model != lock["model_id"]
    if other_hash or other_model:
        raise TierRefused(
            "SAME_TABLE_REVISION_CHANGE",
            f"{lock['vec_table']!r} serves now under other weights ({src['weights_hash'] or model}); "
            f"re-embedding it in place under {lock['weights_hash'][:12]}… would rewrite the live table and "
            "leave no rollback path. This verb does not do that.",
            vec_table=lock["vec_table"])


def _refuse_env_pin(engine_tier: str) -> None:
    warning = _env_warning(engine_tier)
    if warning:
        raise TierRefused("LOCK_ENV_PINNED", warning + " Nothing was changed.", usage=USAGE)


class _Mutex:
    """One `tier` verb at a time per brain: an advisory flock on a file beside the
    lock (POSIX). Where fcntl does not exist the guard is absent, and says so."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / ".tier_set.flock"
        self.fh = None
        self.held = False

    def __enter__(self):
        try:
            import fcntl
        except ImportError:                      # Windows: no advisory lock available here
            return self
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise TierRefused("TIER_BUSY", f"another `tier` verb holds {self.path}; wait for it to finish")
        self.held = True
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            if self.held:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
        return False


def _verify_only(lock: Dict[str, Any]) -> str:
    try:
        embedder_lock.verify_snapshot_against_lock(lock, allow_fetch=False)
        return "verified on disk (checksum equal to the catalogue; nothing instantiated)"
    except embedder_lock.EmbedderLockError as e:
        return f"{e.code}: not verifiable on disk now -- the real run fetches at the pin when online"


def plan(db: Path, tier: str) -> Dict[str, Any]:
    """Counts only: no model is instantiated and nothing is written."""
    lock = target_lock(tier)
    conn = _open(db, readonly=True)
    try:
        src = _source(conn, db)
        pend = pending_ids(conn, lock)
        pop = _population(conn)
        exists = _table_exists(conn, lock["vec_table"])
        vectors = conn.execute(f"SELECT COUNT(*) FROM {lock['vec_table']}").fetchone()[0] if exists else 0
    finally:
        conn.close()
    return {"status": "ok", "action": "plan", "db": str(db), "source": src,
            "target": {"tier": tier, "catalogue_tier": lock["tier"], "model_id": lock["model_id"],
                       "vec_table": lock["vec_table"], "dim": int(lock["dim"]),
                       "weights_hash": lock["weights_hash"], "vectors_now": int(vectors),
                       "bytes": _verify_only(lock)},
            "population": pop, "pending": len(pend), "reusable": pop["memories"] - len(pend),
            "env_warning": _env_warning(tier.strip().lower()),
            "before_a_real_run": ("stop long-running brain processes (the sleep timer, hooks) first: a process "
                                  "holding the old model refuses its writes after the switch until restarted")}


# The flip embeds at most this many late rows inside its write transaction; more, and
# it rolls back, converges outside (per-batch commits) and tries again -- so a store
# from another process never waits behind a long encode. Both numbers are INVENTED, NOT
# TUNED: nothing was measured to choose them. They bound a wait, and a writer that
# outpaces them is told to stop instead.
FLIP_GAP_CAP = 32
FLIP_ATTEMPTS = 10


def set_tier(db: Path, tier: str, *, batch_size: int = BATCH_SIZE) -> Dict[str, Any]:
    db = Path(db).resolve()
    data_dir = db.parent
    engine_tier = (tier or "").strip().lower()
    lock = target_lock(tier)                                    # LOCK_NOT_SHIPPED before anything
    _refuse_env_pin(engine_tier)
    with _Mutex(data_dir):
        return _set_tier_locked(db, data_dir, lock, engine_tier, batch_size)


def _set_tier_locked(db: Path, data_dir: Path, lock: Dict[str, Any], engine_tier: str,
                     batch_size: int) -> Dict[str, Any]:
    t = lock["vec_table"]
    conn = _open(db)
    try:
        src = _source(conn, db)
        # A switch that crashed after its lock moved and before its rows did leaves the
        # lock naming the target: the from-state is the pre-image it wrote first, never
        # what the brain reads now (which is already half the target).
        pre_existing = None
        if (data_dir / PREIMAGE_NAME).exists():
            pre_existing = _read_preimage(data_dir)
        resuming = (pre_existing is not None and pre_existing.get("target_vec_table") == t
                    and src["lock_tier"] == lock["tier"]
                    and src["metadata"].get("active_embedding_table") != t)
        if not resuming:
            _refuse_same_table_change(src, lock)
        try:
            _load(lock)                                         # refusal: the brain is as found
        except embedder_lock.EmbedderLockError as e:
            raise TierRefused(e.code, str(e), **{k: v for k, v in e.detail.items()},
                              target=engine_tier, usage=USAGE)
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")
        _ensure_target_table(conn, lock)
        _ensure_sidecar(conn)
        pop = _population(conn)
        started = _log_start(conn, lock, pop["memories"])
        conn.execute("COMMIT")

        outcome = _Outcome()
        try:
            built = _converge(conn, lock, batch_size, outcome)  # converge OUTSIDE the write lock
        except TierRefused as e:
            _log_failed(conn, started, "tier set", e.message)
            raise
        build_s = time.time() - t0

        # ---- THE FLIP: one write transaction; nothing lands between check and switch ----
        gap = 0
        for attempt in range(FLIP_ATTEMPTS):
            conn.execute("BEGIN IMMEDIATE")
            late = pending_ids(conn, lock)
            if len(late) > FLIP_GAP_CAP:
                conn.execute("ROLLBACK")
                built += _converge(conn, lock, batch_size, outcome)
                continue
            break
        else:
            raise TierRefused("TIER_SET_WRITER_STORM",
                              f"another writer added more than {FLIP_GAP_CAP} memories between each of "
                              f"{FLIP_ATTEMPTS} attempts to switch; stop it and re-run (finished rows are kept)")
        try:
            gap = _embed_pending(conn, late, lock, batch_size=batch_size, in_txn=True, outcome=outcome) if late else 0
            conn.execute(f"DELETE FROM {t} WHERE memory_id NOT IN (SELECT id FROM memories)")
            orphans = conn.execute("DELETE FROM embedding_provenance WHERE vec_table = ? AND memory_id NOT IN "
                                   "(SELECT id FROM memories)", (t,)).rowcount
            cov = _coverage(conn, lock)
            if cov["matched"] != cov["total"] or cov["stamps_without_memory"]:
                raise TierRefused("COVERAGE_INCOMPLETE",
                                  f"{cov['matched']} of {cov['total']} memories carry a stamped {t} vector; the "
                                  "switch needs every one. Nothing was switched.", coverage=cov)
            if resuming:
                preimage = pre_existing
            else:
                prev_path = embedder_lock.lock_path_for(db)
                preimage = {"schema": PREIMAGE_SCHEMA, "at": _now(), "db": str(db),
                            "previous_lock_raw": (prev_path.read_text(encoding="utf-8")
                                                  if prev_path.exists() else None),
                            "previous_metadata": src["metadata"], "previous_vec_table": src["vec_table"],
                            "target_tier": lock["tier"], "target_vec_table": t}
            already = (not resuming and src["lock_tier"] == lock["tier"]
                       and src["metadata"]["active_embedding_table"] == t)
            if not already and not resuming:
                # the FROM-state is durable before anything that replaces it is written
                _atomic_write(data_dir / PREIMAGE_NAME, json.dumps(preimage, indent=1, sort_keys=True) + "\n")
            _set_metadata(conn, {"active_embedding_table": t, "active_embedding_model": lock["model_id"],
                                 "active_embedding_dim": str(int(lock["dim"]))})
            if started is not None:
                conn.execute("UPDATE embedding_upgrade_log SET completed_at = ?, memories_processed = ?, "
                             "status = 'completed' WHERE started_at = ? AND notes = 'tier set'",
                             (_now(), built + gap, started))
            if already:
                written = embedder_lock.resolve(db)     # nothing moves: the lock file is left byte for byte
            else:
                written = embedder_lock.write_lock(data_dir, lock["tier"], replace=True, locked_by="tier set")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        from_meta = preimage["previous_metadata"] if (resuming or not already) else src["metadata"]
        from_table = preimage["previous_vec_table"] if (resuming or not already) else src["vec_table"]
        receipt = {"at": _now(), "action": "tier-set", "by": "tier set", "db": str(db),
                   "from": {"vec_table": from_table, "model": from_meta.get("active_embedding_model"),
                            "had_lock": bool(preimage.get("previous_lock_raw")) if not already
                            else src["lock_tier"] is not None},
                   "to": {"tier": lock["tier"], "vec_table": t, "model_id": lock["model_id"],
                          "weights_hash": written["weights_hash"]},
                   "embedded": built + gap, "reused": pop["memories"] - built - gap,
                   "shortened_to_fit": len(outcome.shortened),
                   "coverage": cov, "population": pop, "orphans_pruned": int(orphans),
                   "already_on_target": already, "resumed_after_crash": resuming,
                   "old_vectors": f"kept in {from_table} (rollback path; removed only by tier cleanup)",
                   "preimage": str(data_dir / PREIMAGE_NAME), "derived_layer": DERIVED_LAYER_NOTE}
        _receipt(data_dir, receipt)
        return {"status": "ok", "action": "set", "receipt": receipt, "build_s": round(build_s, 2),
                "total_s": round(time.time() - t0, 2),
                "rollback": "voidloom tier rollback",
                "cleanup": f"voidloom tier cleanup --confirm {from_table}" if from_table != t else None}
    finally:
        conn.close()


def _re_embed_prelock(db: Path, engine_tier: str, batch_size: int) -> Dict[str, Any]:
    lock = target_lock(engine_tier)
    _refuse_env_pin(engine_tier)
    data_dir = db.parent
    with _Mutex(data_dir):
        conn = _open(db)
        try:
            src = _source(conn, db)
            _refuse_same_table_change(src, lock)
            try:
                _load(lock)                                     # refusal: the brain is as found
            except embedder_lock.EmbedderLockError as e:
                raise TierRefused(e.code, str(e), **{k: v for k, v in e.detail.items()},
                                  target=engine_tier, usage=USAGE)
            t0 = time.time()
            conn.execute("BEGIN IMMEDIATE")
            _ensure_target_table(conn, lock)
            _ensure_sidecar(conn)
            pop = _population(conn)
            started = _log_start(conn, lock, pop["memories"], notes="re-embed")
            conn.execute("COMMIT")
            outcome = _Outcome()
            try:
                built = _converge(conn, lock, batch_size, outcome)
            except TierRefused as e:
                _log_failed(conn, started, "re-embed", e.message)
                raise
            if started is not None:
                conn.execute("UPDATE embedding_upgrade_log SET completed_at = ?, memories_processed = ?, "
                             "status = 'completed' WHERE started_at = ? AND notes = 're-embed'",
                             (_now(), built, started))
            cov = _coverage(conn, lock)
        finally:
            conn.close()
    receipt = {"at": _now(), "action": "re-embed", "by": "re-embed", "db": str(db),
               "basis": "catalogue (no install lock beside this brain; none was written)",
               "to": {"tier": lock["tier"], "vec_table": lock["vec_table"], "model_id": lock["model_id"],
                      "weights_hash": lock["weights_hash"]},
               "embedded": built, "reused": pop["memories"] - built, "shortened_to_fit": len(outcome.shortened),
               "coverage": cov, "population": pop,
               "already_on_target": True}
    _receipt(data_dir, receipt)
    return {"status": "ok", "action": "re-embed", "receipt": receipt, "total_s": round(time.time() - t0, 2)}


def _read_preimage(data_dir: Path) -> Dict[str, Any]:
    p = data_dir / PREIMAGE_NAME
    try:
        pre = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TierRefused("NOTHING_TO_ROLL_BACK",
                          f"no {PREIMAGE_NAME} beside the brain: no `tier set` switch is waiting to be undone "
                          "(or it was already rolled back, or its old vectors were cleaned up)")
    except (OSError, ValueError) as e:
        raise TierRefused("PREIMAGE_MALFORMED", f"{p} is unreadable: {e}")
    if not isinstance(pre, dict) or pre.get("schema") != PREIMAGE_SCHEMA:
        raise TierRefused("PREIMAGE_MALFORMED", f"{p} is not a {PREIMAGE_SCHEMA} document")
    return pre


def rollback(db: Path) -> Dict[str, Any]:
    db = Path(db).resolve()
    with _Mutex(db.parent):
        return _rollback_locked(db)


def _rollback_locked(db: Path) -> Dict[str, Any]:
    data_dir = db.parent
    pre = _read_preimage(data_dir)
    conn = _open(db)
    try:
        restored = pre["previous_vec_table"]
        if not _table_exists(conn, restored):
            raise TierRefused("ROLLBACK_GONE", f"the previous vector table {restored!r} no longer exists")
        stamp = _now().replace(":", "")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _set_metadata(conn, pre["previous_metadata"])
            lock_path = embedder_lock.lock_path_for(db)
            if pre["previous_lock_raw"] is None:
                if lock_path.exists():
                    os.replace(lock_path, data_dir / f"embedder.lock.rolled-back.{stamp}.json")
                    embedder_lock._fsync_dir(data_dir)
            else:
                _atomic_write(lock_path, pre["previous_lock_raw"])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        os.replace(data_dir / PREIMAGE_NAME, data_dir / f"embedder.lock.preimage.{stamp}.used.json")
        gap = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE m.id NOT IN "
                           f"(SELECT memory_id FROM {restored})").fetchone()[0]
    finally:
        conn.close()
    restored_tier = {"memory_embeddings": "nomic", "memory_embeddings_v3": "qwen"}.get(restored)
    fill = (f"voidloom tier set {restored_tier}" if restored_tier else
            f"python3 organs/embedding_upgrade.py --db-path {db} re-embed")
    receipt = {"at": _now(), "action": "tier-rollback", "by": "tier rollback", "db": str(db),
               "restored_vec_table": restored, "restored_lock": pre["previous_lock_raw"] is not None,
               "memories_without_a_restored_vector": int(gap)}
    if restored_tier:
        route = (f"the restored tier ({restored_tier}) is shipped, so `voidloom tier set {restored_tier}` is the "
                 f"route that fills any gap: it reuses every vector already stamped under it and embeds only "
                 f"what is missing")
    else:
        route = (f"the restored table {restored} belongs to a tier the package does not ship (jina), so "
                 f"`tier set` cannot target it and this restore is the only route back to it; a gap is filled "
                 f"by `{fill}`")
    receipt["route"] = route
    _receipt(data_dir, receipt)
    return {"status": "ok", "action": "rollback", "receipt": receipt, "route": route,
            "gap_fill": fill if gap else None,
            "note": (f"{gap} memor(ies) written after the switch have no vector in {restored}; they are found by "
                     f"keyword until `{fill}` embeds them") if gap else None}


def cleanup(db: Path, confirm: Optional[str]) -> Dict[str, Any]:
    db = Path(db).resolve()
    with _Mutex(db.parent):
        return _cleanup_locked(db, confirm)


def _cleanup_locked(db: Path, confirm: Optional[str]) -> Dict[str, Any]:
    import embed_resolver
    data_dir = db.parent
    conn = _open(db)
    try:
        active, _d = embed_resolver.resolve(conn)
        if not confirm or confirm not in KNOWN_TABLES:
            raise TierRefused("CLEANUP_UNCONFIRMED",
                              f"name the table to empty, exactly: --confirm <{'|'.join(KNOWN_TABLES)}>",
                              active=active)
        named = {active, _metadata(conn).get("active_embedding_table")}
        if confirm in named:
            raise TierRefused("CLEANUP_REFUSED", f"{confirm!r} is a table this brain searches now "
                              "(named by its lock or its metadata rows)", active=active)
        if not _table_exists(conn, confirm):
            raise TierRefused("CLEANUP_REFUSED", f"{confirm!r} does not exist in this brain", active=active)
        conn.execute("BEGIN IMMEDIATE")
        try:
            n_vec = conn.execute(f"SELECT COUNT(*) FROM {confirm}").fetchone()[0]
            conn.execute(f"DELETE FROM {confirm}")
            n_stamp = conn.execute("DELETE FROM embedding_provenance WHERE vec_table = ?", (confirm,)).rowcount
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    pre_p = data_dir / PREIMAGE_NAME
    retired = None
    if pre_p.exists():
        try:
            pre = json.loads(pre_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pre = {}
        if pre.get("previous_vec_table") == confirm:
            retired = str(data_dir / f"embedder.lock.preimage.{_now().replace(':', '')}.cleaned.json")
            os.replace(pre_p, retired)
    receipt = {"at": _now(), "action": "tier-cleanup", "by": "tier cleanup", "db": str(db),
               "emptied": confirm, "vectors_removed": int(n_vec), "stamps_removed": int(n_stamp),
               "rollback_retired": retired}
    _receipt(data_dir, receipt)
    return {"status": "ok", "action": "cleanup", "receipt": receipt}


def status(db: Path) -> Dict[str, Any]:
    db = Path(db).resolve()
    conn = _open(db, readonly=True)
    try:
        src = _source(conn, db)
        pop = _population(conn)
        tables = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in KNOWN_TABLES if _table_exists(conn, t)}
    finally:
        conn.close()
    return {"status": "ok", "action": "status", "db": str(db), "current": src, "population": pop,
            "vectors_by_table": tables, "rollback_available": (db.parent / PREIMAGE_NAME).exists()}


# ----------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------

def _default_db() -> Path:
    env = os.environ.get("WIZARD_LIBRARY_DB")
    return Path(env).expanduser() if env else _ORGANS.parent / "claude_memory.db"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tier", description="Move a brain to another embedding model by "
                                "re-embedding every memory; keep the old vectors until cleanup.")
    p.add_argument("--db", default=None, help="the brain's claude_memory.db (default: WIZARD_LIBRARY_DB)")
    p.add_argument("--json", action="store_true", help="structured envelope (the default output)")
    sub = p.add_subparsers(dest="action", metavar="<set|status|rollback|cleanup>")
    # The voidloom CLI APPENDS --json / --db after the organ's own arguments, so every
    # action accepts them too (SUPPRESS: a value given before the action is kept).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sp = sub.add_parser("set", parents=[common], help="re-embed under <tier>, switch at 100%% coverage")
    sp.add_argument("tier", help="nomic | qwen")
    sp.add_argument("--plan", action="store_true", help="counts only: no model, no writes")
    sp.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    sub.add_parser("status", parents=[common],
                   help="the current tier, vectors per table, whether a rollback is available")
    sub.add_parser("rollback", parents=[common], help="restore the lock and rows from before the last switch")
    sp = sub.add_parser("cleanup", parents=[common], help="empty an OLD vector table, with a receipt")
    sp.add_argument("--confirm", default=None, help="the exact table name to empty")
    return p


def run(argv: List[str]) -> Dict[str, Any]:
    p = _parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return {"status": "error", "error": "USAGE", "message": f"tier: bad arguments (exit {e.code})",
                "usage": USAGE}
    db = Path(args.db).expanduser() if args.db else _default_db()
    if not args.action:
        return {"status": "error", "error": "USAGE", "message": "tier: name an action", "usage": USAGE}
    if not db.exists():
        return {"status": "error", "error": "DB_ABSENT", "message": f"no brain at {db}", "usage": USAGE}
    try:
        if args.action == "set":
            if args.plan:
                return plan(db, args.tier)
            return set_tier(db, args.tier, batch_size=max(1, int(args.batch_size)))
        if args.action == "status":
            return status(db)
        if args.action == "rollback":
            return rollback(db)
        return cleanup(db, args.confirm)
    except TierRefused as e:
        return e.as_dict()
    except embedder_lock.EmbedderLockError as e:
        return {"status": "error", "error": e.code, "message": str(e)}


def main(argv: Optional[List[str]] = None) -> int:
    env = run(list(sys.argv[1:] if argv is None else argv))
    print(json.dumps(env, indent=2, sort_keys=True, default=str))
    if env.get("status") == "ok":
        return 0
    return 2 if env.get("error") == "USAGE" else 1


if __name__ == "__main__":
    sys.exit(main())

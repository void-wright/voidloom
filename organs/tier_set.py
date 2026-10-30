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
     re-run: finished rows are reused, the rest is work again.
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
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60.0)
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


_PENDING_SQL = """
    SELECT m.id FROM memories m
     WHERE NOT EXISTS (
           SELECT 1 FROM embedding_provenance p
            WHERE p.memory_id = m.id AND p.vec_table = :t AND p.weights_hash = :h
              AND p.dim = :d AND p.input_kind = 'text'
              AND p.content_hash = vl_sha256(m.content)
              AND EXISTS (SELECT 1 FROM {t} v WHERE v.memory_id = m.id))
     ORDER BY m.id
"""


def pending_ids(conn: sqlite3.Connection, lock: Dict[str, Any]) -> List[int]:
    """Every memory (archived included) WITHOUT a vector in the target table whose
    stamp names the target's checksum, width and the memory's CURRENT text. A legacy
    'unresolved' stamp never matches: bytes nobody checked are re-embedded."""
    t = lock["vec_table"]
    if not _table_exists(conn, t):
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


def _ensure_target_table(conn: sqlite3.Connection, lock: Dict[str, Any]) -> None:
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {lock['vec_table']} USING vec0("
                 f"memory_id INTEGER PRIMARY KEY, embedding float[{int(lock['dim'])}])")


def _embed_ids(conn: sqlite3.Connection, ids: List[int], lock: Dict[str, Any], *,
               batch_size: int, in_txn: bool) -> int:
    """Embed `ids` under `lock`. Outside a transaction: one transaction per batch
    (kill-and-resume safe). Inside one (the flip): no commits here."""
    import memory_write
    done = 0
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(f"SELECT id, content FROM memories WHERE id IN ({marks}) ORDER BY id",
                            chunk).fetchall()
        texts = [c or "" for _i, c in rows]
        vecs = _encode(texts, lock) if rows else []
        if not in_txn:
            conn.execute("BEGIN IMMEDIATE")
        try:
            for (mid, _c), text, vec in zip(rows, texts, vecs):
                memory_write.attach_under_lock(conn, mid, text, vec, lock=lock, organ=ORGAN)
            if not in_txn:
                conn.execute("COMMIT")
        except BaseException:
            if not in_txn:
                conn.execute("ROLLBACK")
            raise
        done += len(rows)
    return done


def _log_start(conn: sqlite3.Connection, lock: Dict[str, Any], total: int) -> Optional[str]:
    if not _table_exists(conn, "embedding_upgrade_log"):
        return None
    cols = {r[1] for r in conn.execute("PRAGMA table_info(embedding_upgrade_log)")}
    started = _now()
    if "weights_hash" in cols:
        conn.execute("INSERT INTO embedding_upgrade_log (started_at, model_name, embedding_dim, memories_total, "
                     "status, weights_hash, notes) VALUES (?, ?, ?, ?, 'in_progress', ?, 'tier set')",
                     (started, lock["model_id"], int(lock["dim"]), total, lock["weights_hash"]))
    else:
        conn.execute("INSERT INTO embedding_upgrade_log (started_at, model_name, embedding_dim, memories_total, "
                     "status, notes) VALUES (?, ?, ?, ?, 'in_progress', 'tier set')",
                     (started, lock["model_id"], int(lock["dim"]), total))
    return started


def _set_metadata(conn: sqlite3.Connection, values: Dict[str, Optional[str]]) -> None:
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
    target vector whose stamp names the target checksum, width and current text;
    `stamps_without_memory` is the other side. The switch needs matched == total and
    no stamp without its memory."""
    t = lock["vec_table"]
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    matched = conn.execute(
        f"SELECT COUNT(*) FROM memories m JOIN embedding_provenance p ON p.memory_id = m.id "
        f" WHERE p.vec_table = ? AND p.weights_hash = ? AND p.dim = ? AND p.input_kind = 'text' "
        f"   AND p.content_hash = vl_sha256(m.content) "
        f"   AND EXISTS (SELECT 1 FROM {t} v WHERE v.memory_id = m.id)",
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
# from another process never waits behind a long encode. Neither number is measured:
# they bound a wait, and a writer that outpaces them is told to stop instead.
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
        pop = _population(conn)
        started = _log_start(conn, lock, pop["memories"])
        conn.execute("COMMIT")

        built = 0
        for _pass in range(FLIP_ATTEMPTS):                      # converge OUTSIDE the write lock
            more = _embed_ids(conn, pending_ids(conn, lock), lock, batch_size=batch_size, in_txn=False)
            built += more
            if not more:
                break
        build_s = time.time() - t0

        # ---- THE FLIP: one write transaction; nothing lands between check and switch ----
        gap = 0
        for attempt in range(FLIP_ATTEMPTS):
            conn.execute("BEGIN IMMEDIATE")
            late = pending_ids(conn, lock)
            if len(late) > FLIP_GAP_CAP:
                conn.execute("ROLLBACK")
                built += _embed_ids(conn, late, lock, batch_size=batch_size, in_txn=False)
                continue
            break
        else:
            raise TierRefused("TIER_SET_WRITER_STORM",
                              f"another writer added more than {FLIP_GAP_CAP} memories between each of "
                              f"{FLIP_ATTEMPTS} attempts to switch; stop it and re-run (finished rows are kept)")
        try:
            gap = _embed_ids(conn, late, lock, batch_size=batch_size, in_txn=True) if late else 0
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
        gap = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE NOT EXISTS "
                           f"(SELECT 1 FROM {restored} v WHERE v.memory_id = m.id)").fetchone()[0]
    finally:
        conn.close()
    restored_tier = {"memory_embeddings": "nomic", "memory_embeddings_v3": "qwen"}.get(restored)
    fill = (f"voidloom tier set {restored_tier}" if restored_tier else
            f"python3 organs/embedding_upgrade.py --db-path {db} re-embed")
    receipt = {"at": _now(), "action": "tier-rollback", "by": "tier rollback", "db": str(db),
               "restored_vec_table": restored, "restored_lock": pre["previous_lock_raw"] is not None,
               "memories_without_a_restored_vector": int(gap)}
    _receipt(data_dir, receipt)
    return {"status": "ok", "action": "rollback", "receipt": receipt,
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

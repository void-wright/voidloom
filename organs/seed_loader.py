#!/usr/bin/env python3
"""seed_loader -- the library behind the shipped loader verb, `claude_memory_lite.py
load-seed`: HOW a seed row is written. WHICH rows are written stays where it was,
in claude_memory_lite.seed_plan (the per-memory anchor delta).

    python3 organs/claude_memory_lite.py load-seed --file seed/seed_pack.json [--embed]
    python3 organs/claude_memory_lite.py seed-scrub        # the denylist over the pack: hits, rc 4 on any
    python3 organs/claude_memory_lite.py seed-self-test    # the 25-query doctrine set: N of 25 per mode
    python3 organs/claude_memory_lite.py seed-delete-one ORIGIN
    python3 organs/claude_memory_lite.py seed-shape        # every seed number on this brain, derived

This module has no command line of its own: the loader verb already exists, and a
second loader would be a second answer to a question that has one.

THE DIVISION OF LABOUR
  * WHICH (claude_memory_lite.seed_plan): the anchor delta decides, per pack entry,
    present / adopted / changed / new / left / held-untagged. A seed row's IDENTITY
    is its anchor tag (anchor:seed_<name>); its words are one revision of it.
  * HOW (this module): every row the delta inserts goes through the one guarded
    writer, memory_write.write_memory (the memory, its tags, and -- under --embed
    -- its vector and provenance stamp in one savepoint), flagged by two COLUMNS
    (migration_044): seed = 1 and origin = `seed_pack:<version>#<sha256(content)[:16]>`.
    origin is PROVENANCE (which pack record these words are), never the key the
    loader skips on. A seed row the delta does not insert (adopted, or present but
    written before the columns existed) has its columns set, nothing else.
  * SCRUBBED FIRST: seed/seed_denylist.json is matched over the whole pack file
    BEFORE the load opens its transaction; any hit refuses the whole load, typed
    SEED_DENYLIST_HIT, naming the class and the offset, never the matched text.
  * ONE TRANSACTION: the caller commits once at the end, so a refused or killed
    load leaves the brain as it was, and a re-run re-plans from what is there.
  * NEVER LOADS A MODEL ITSELF: under --embed the vector comes from
    memory_write.default_encoder -> the locked embedder (verified before it loads).
    Without --embed (the installer's system python, which may lack the vector
    extension) a row is written keyword-findable, the reason stamped into its
    source_context, and embed_backfill.py gives it a vector later.

THE SELF-TEST: seed/seed_doctrine_queries.json -- 25 queries phrased as a user would
ask, each naming the row it must surface by ANCHOR -- through the shipped recall
(hybrid_retrieval), the real number printed PER MODE.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
PACK_DEFAULT = HERE.parent / "seed" / "seed_pack.json"
DENYLIST_DEFAULT = HERE.parent / "seed" / "seed_denylist.json"
DOCTRINE_DEFAULT = HERE.parent / "seed" / "seed_doctrine_queries.json"
ORIGIN_PREFIX = "seed_pack:"
SKIP_REASON = ("loaded without --embed (the installer's python may lack the vector extension); "
               "embed_backfill.py gives it a vector")


class SeedLoadError(RuntimeError):
    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = dict(detail)

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "error", "error": self.code, "message": str(self), **self.detail}


def _organs_on_path() -> None:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))


def enable_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite_vec onto `conn` (the --embed path needs the vector tables).
    Refuses typed when this python has no sqlite_vec: --embed is a Tier-1 form."""
    try:
        import sqlite_vec  # type: ignore
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception as e:
        raise SeedLoadError("SEED_EMBED_REFUSED",
                            f"--embed needs sqlite_vec in this python ({type(e).__name__}: {e}); "
                            "load without --embed and run embed_backfill.py under the Tier-1 python")


VECTOR_TABLES = ("memory_embeddings", "memory_embeddings_v2", "memory_embeddings_v3")


def _connect(db: str) -> sqlite3.Connection:
    """A connection with vec0 loaded when this python has it (a Tier-0 host still
    gets one: shape and delete-one need no vector table)."""
    conn = sqlite3.connect(db, timeout=60.0)   # the brain's write-lock wait every writer shares
    try:
        enable_vec(conn)
    except SeedLoadError:
        pass
    return conn


def origin_of(version: str, content: str) -> str:
    return f"{ORIGIN_PREFIX}{version}#{hashlib.sha256((content or '').encode('utf-8')).hexdigest()[:16]}"


def has_seed_columns(conn: sqlite3.Connection) -> bool:
    return {"seed", "origin"} <= {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}


def scrub(pack_path: Path, denylist_path: Path = DENYLIST_DEFAULT) -> Dict[str, Any]:
    """The denylist over the WHOLE pack file's text. Hits name the class and the
    offset, never the matched text (a hit that prints the secret is a second leak)."""
    try:
        dl = json.loads(Path(denylist_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SeedLoadError("SEED_DENYLIST_MISSING", f"no denylist at {denylist_path}", path=str(denylist_path))
    except json.JSONDecodeError as e:
        raise SeedLoadError("SEED_DENYLIST_MALFORMED", f"{denylist_path} is not valid JSON: {e}",
                            path=str(denylist_path))
    try:
        text = Path(pack_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SeedLoadError("SEED_PACK_MISSING", f"no seed pack at {pack_path}", path=str(pack_path))
    patterns = dl.get("patterns") or []
    hits: List[Dict[str, Any]] = []
    for idx, p in enumerate(patterns):
        for m in re.finditer(p["pattern"], text, re.I):
            hits.append({"class": p.get("class"), "pattern_index": idx,
                         "offset": m.start(), "length": m.end() - m.start()})
    return {"denylist": str(denylist_path), "patterns": len(patterns),
            "bytes_scanned": len(text.encode("utf-8")), "hits": len(hits), "hit_list": hits[:50]}


def refuse_on_hits(pack_path: Path, denylist_path: Path = DENYLIST_DEFAULT) -> Dict[str, Any]:
    sc = scrub(pack_path, denylist_path)
    if sc["hits"]:
        raise SeedLoadError("SEED_DENYLIST_HIT",
                            f"{sc['hits']} denylist hit(s) over {pack_path}; nothing loaded",
                            scrub=sc, pack=str(pack_path))
    return sc


def write_seed_row(conn: sqlite3.Connection, *, content: str, summary: str, category: str, kind: str,
                   importance: float, source_project: str, source_context: str, tags: Sequence[str],
                   version: str, embed: bool = False,
                   encoder: Optional[Callable[[str], bytes]] = None) -> Dict[str, Any]:
    """ONE seed row through the guarded writer. Does NOT commit (the load is one
    transaction). `encoder` is for library callers (a test's stub); the verb never
    passes one."""
    _organs_on_path()
    import memory_write
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    extra: Dict[str, Any] = {"created_at": now, "updated_at": now, "last_accessed": now,
                             "access_count": 0, "is_archived": 0}
    origin = None
    if has_seed_columns(conn):
        origin = origin_of(version, content)
        extra.update({"seed": 1, "origin": origin})
    res = memory_write.write_memory(
        conn, content=content, summary=summary, category=category, importance=importance,
        source_project=source_project, source_context=source_context, kind=kind, confidence=1.0,
        extra=extra, tags=[t for t in tags if t and t.strip()], encoder=encoder,
        embed_policy="require" if embed else "skip", skip_reason=None if embed else SKIP_REASON,
        enqueue_scan=False)
    return {"memory_id": res.memory_id, "origin": origin, "embedded": res.embedded}


def stamp_columns(conn: sqlite3.Connection, memory_id: int, version: str, content: str) -> bool:
    """Set seed=1 + origin on a seed row the delta does not insert (adopted, or
    present but written before the columns existed). Never touches its words or
    its vector. False when the schema has no columns or the row is already flagged."""
    if not has_seed_columns(conn):
        return False
    cur = conn.execute("UPDATE memories SET seed = 1, origin = ? WHERE id = ? AND COALESCE(seed, 0) = 0",
                       (origin_of(version, content), memory_id))
    return cur.rowcount == 1


def seed_shape(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Every seed number, derived on this connection now."""
    if not has_seed_columns(conn):
        return {"columns_present": False, "seed_rows": None, "unflagged_pack_tagged_rows": None}
    q = lambda sql: conn.execute(sql).fetchone()[0]
    out = {"columns_present": True,
           "seed_rows": q("SELECT COUNT(*) FROM memories WHERE seed = 1"),
           "active_seed_rows": q("SELECT COUNT(*) FROM memories WHERE seed = 1 AND COALESCE(is_archived,0)=0"),
           "duplicate_origins_active": q("SELECT COUNT(*) FROM (SELECT origin FROM memories WHERE seed = 1 "
                                         "AND COALESCE(is_archived,0)=0 GROUP BY origin HAVING COUNT(*) > 1)"),
           "seed_rows_without_origin": q("SELECT COUNT(*) FROM memories WHERE seed = 1 "
                                         "AND (origin IS NULL OR origin = '')"),
           "unflagged_pack_tagged_rows": q("SELECT COUNT(*) FROM memories m WHERE COALESCE(m.seed,0) = 0 "
                                           "AND m.id IN (SELECT memory_id FROM memory_tags "
                                           "WHERE tag = 'seed-pack-agnostic')")}
    try:
        _organs_on_path()
        import memory_write
        try:
            enable_vec(conn)                     # the vector table is vec0: readable only with the extension
        except SeedLoadError:
            pass
        table = memory_write.provenance_filter(conn).vec_table
        out["seed_rows_with_vector_and_stamp"] = conn.execute(
            f"SELECT COUNT(*) FROM memories m JOIN embedding_provenance p ON p.memory_id = m.id "
            f"WHERE m.seed = 1 AND COALESCE(m.is_archived,0)=0 AND p.vec_table = ? "
            f"AND m.id IN (SELECT memory_id FROM {table})", (table,)).fetchone()[0]
        out["vector_table"] = table
    except Exception as e:                       # no tier / no vec0: UNKNOWN, not zero
        out["seed_rows_with_vector_and_stamp"] = f"UNMEASURED: {type(e).__name__}"
    return out


def delete_one(db: str, origin: str) -> Dict[str, Any]:
    """Delete ONE seed row permanently by origin (the row, its tags, links, stamp
    and vector) and report the count before and after, derived."""
    _organs_on_path()
    conn = _connect(db)
    try:
        before = conn.execute("SELECT COUNT(*) FROM memories WHERE seed = 1").fetchone()[0]
        rows = conn.execute("SELECT id FROM memories WHERE seed = 1 AND origin = ? ORDER BY id",
                            (origin,)).fetchall()
        if not rows:
            raise SeedLoadError("SEED_ORIGIN_UNKNOWN", f"no seed row carries origin {origin}", origin=origin)
        if len(rows) > 1:
            raise SeedLoadError("SEED_ORIGIN_AMBIGUOUS", f"{len(rows)} seed rows carry origin {origin}; "
                                "refusing to guess which", origin=origin, memory_ids=[r[0] for r in rows])
        mid = rows[0][0]
        import memory_write
        receipt = None
        try:
            enable_vec(conn)                     # a brain with vectors needs the extension to reach them
        except SeedLoadError:
            pass
        if memory_write.hard_delete_allowed():
            try:
                receipt = memory_write.purge_memory(conn, mid)
            except memory_write.HardDeleteRefused as e:
                conn.rollback()
                raise SeedLoadError(e.code, e.message, origin=origin, memory_id=mid)
        else:
            conn.execute("PRAGMA foreign_keys=ON")   # a row something still names is refused below, not left dangling
            conn.execute("PRAGMA secure_delete=ON")
            # Every tier's table: after `tier set` the old tier still holds this row's vector.
            present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            try:
                memory_write.clear_vectors_or_refuse(conn, mid, present)
            except memory_write.HardDeleteRefused as e:
                conn.rollback()
                raise SeedLoadError(e.code, e.message, origin=origin, memory_id=mid)
            memory_write.tombstone_seed(conn, mid)
            for sql in ("DELETE FROM embedding_provenance WHERE memory_id = ?",
                        "DELETE FROM memory_tags WHERE memory_id = ?",
                        "DELETE FROM memory_links WHERE source_id = ?1 OR target_id = ?1"):
                try:
                    conn.execute(sql, (mid,))
                except sqlite3.Error:
                    pass
            try:
                conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            except sqlite3.IntegrityError as e:
                if "FOREIGN KEY" not in str(e):
                    raise
                conn.rollback()
                raise SeedLoadError("HARD_DELETE_REFERENCED", f"seed row {mid} is named by rows that have no delete "
                                    f"rule; nothing was removed. Set {memory_write.HARD_DELETE_ENV}=1 to remove those rows with it",
                                    origin=origin, memory_id=mid)
        conn.commit()
        finished = memory_write.finish_purge(conn)
        if receipt is None:
            receipt = {}
        receipt.update(finished)
        after = conn.execute("SELECT COUNT(*) FROM memories WHERE seed = 1").fetchone()[0]
        gone = conn.execute("SELECT COUNT(*) FROM memories WHERE id = ?", (mid,)).fetchone()[0] == 0
    finally:
        conn.close()
    return {"status": "ok", "action": "seed-delete-one", "origin": origin, "memory_id": mid,
            "seed_rows_before": before, "seed_rows_after": after, "dropped_by": before - after, "row_gone": gone,
            "receipt": receipt}


def expected_row(conn: sqlite3.Connection, q: Dict[str, Any]) -> Optional[int]:
    """The live seed row a doctrine query names: by ANCHOR first (stable across pack
    versions), then by origin."""
    a = q.get("expect_anchor")
    if a:
        r = conn.execute("SELECT m.id FROM memories m JOIN memory_tags t ON t.memory_id = m.id "
                         "WHERE t.tag = ? AND COALESCE(m.is_archived,0) = 0 AND m.seed = 1 "
                         "ORDER BY m.id DESC LIMIT 1", (a,)).fetchone()
        if r:
            return r[0]
    o = q.get("expect_origin")
    if o:
        r = conn.execute("SELECT id FROM memories WHERE seed = 1 AND origin = ? "
                         "AND COALESCE(is_archived,0) = 0", (o,)).fetchone()
        if r:
            return r[0]
    return None


def self_test(db: str, *, doctrine: Path = DOCTRINE_DEFAULT, mode: Optional[str] = None,
              top_k: Optional[int] = None) -> Dict[str, Any]:
    """The doctrine set through the shipped recall, N of 25 PER MODE, the real numbers.
    The set's `primary_mode` carries the bar; every other mode in `modes` is measured
    beside it. `mode` restricts the run to one mode; `top_k` overrides the set's."""
    t0 = time.time()
    try:
        ds = json.loads(Path(doctrine).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SeedLoadError("SEED_DOCTRINE_MISSING", f"no doctrine set at {doctrine}", path=str(doctrine))
    _organs_on_path()
    import memory_write
    import hybrid_retrieval
    import embedder_lock
    import embedding_upgrade
    import claude_memory
    primary = ds.get("primary_mode") or "hybrid"
    modes = [mode] if mode else list(ds.get("modes") or [primary])
    top_k = int(top_k or ds.get("top_k", 3))
    bar = int(ds.get("bar", 23))
    conn = _connect(db)
    try:
        table = memory_write.provenance_filter(conn).vec_table
        lock = embedding_upgrade.brain_lock(conn, tier=claude_memory._TABLE_TO_TIER.get(table, ""))
        by_mode: Dict[str, Any] = {}
        qvecs: Dict[int, bytes] = {}
        for md in modes:
            results: List[Dict[str, Any]] = []
            passed = 0
            for q in ds["queries"]:
                want = expected_row(conn, q)
                if want is None:
                    results.append({"n": q["n"], "pass": False, "why": "expected row not loaded",
                                    "expect_anchor": q.get("expect_anchor")})
                    continue
                if q["n"] not in qvecs:
                    with embedder_lock.hub_closed():     # a battery reads, and a read does not reach the network
                        qvecs[q["n"]] = embedding_upgrade.encode_locked(q["query"], is_query=True, lock=lock)
                rows = hybrid_retrieval.recall_hybrid(conn, q["query"], qvecs[q["n"]], top_k=top_k, mode=md)
                ids = [r.get("id") for r in rows]
                rank = (ids.index(want) + 1) if want in ids else None
                passed += 1 if rank is not None else 0
                results.append({"n": q["n"], "pass": rank is not None, "rank": rank,
                                "expect_anchor": q.get("expect_anchor"), "top_ids": ids})
            by_mode[md] = {"passed": passed, "of": len(ds["queries"]), "results": results}
    finally:
        conn.close()
    total = len(ds["queries"])
    pmode = primary if primary in by_mode else modes[0]
    passed = by_mode[pmode]["passed"]
    others = "; ".join(f"{m} {v['passed']} of {v['of']}" for m, v in by_mode.items() if m != pmode)
    return {"status": "ok", "action": "seed-self-test", "doctrine": str(doctrine), "primary_mode": pmode,
            "modes": modes, "top_k": top_k, "passed": passed, "of": total, "bar": bar, "meets_bar": passed >= bar,
            "by_mode": {m: {"passed": v["passed"], "of": v["of"]} for m, v in by_mode.items()},
            "line": f"seed doctrine self-test: {passed} of {total} in top {top_k} ({pmode}, the shipped recall; "
                    f"bar {bar}): {'PASS' if passed >= bar else 'RED'}" + (f" -- also measured: {others}" if others else ""),
            "results": {m: v["results"] for m, v in by_mode.items()}, "time_ms": int((time.time() - t0) * 1000)}

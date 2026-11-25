#!/usr/bin/env python3
"""mark_admin -- the verbs a person runs over marks: forget, unarchive, and the one-time backfill.

  forget     archives one memory, or everything one research run wrote. Nothing is deleted. The receipt names every row,
             and is kept in the brain (`forget_receipts`), because undoing it must restore exactly what it changed: a row
             somebody archived before is not this verb's to bring back.
  unarchive  undoes a forget, from that receipt, and nothing else.
  backfill   marks rows that were already written from marked rows before migration_046's triggers existed. It follows
             recorded derivations only, halts over a limit, keeps a pre-image, and can be undone.

Stdlib only.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Union

import marks

BACKFILL_LIMIT = 200


def _read_only(path: Path) -> sqlite3.Connection:
    """A connection that writes nothing. A brain in write-ahead mode cannot be opened that way in a folder that cannot be
    written, unless it is taken as it lies: which is what it is when no log of unmerged writes stands beside it."""
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=60.0)
    try:
        conn.execute("SELECT 1 FROM sqlite_master").fetchone()
        return conn
    except sqlite3.OperationalError:
        conn.close()
        wal = Path(f"{path}-wal")
        if wal.exists() and wal.stat().st_size:
            raise
        return sqlite3.connect(f"{path.as_uri()}?immutable=1", uri=True, timeout=60.0)


def _open(db: Union[str, Path], what: str, write: bool = True) -> sqlite3.Connection:
    if not Path(db).exists():
        raise marks.MarksError("BRAIN_UNREADABLE", f"no database at {db}")
    if write:
        conn = sqlite3.connect(str(db), timeout=60.0, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        conn = _read_only(Path(db).resolve())
    try:
        marks.require_ready(conn, what)
        if write:
            marks.ensure_triggers(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _one_target(memory_id: Optional[int], run_id: Optional[str]) -> None:
    if (memory_id is None) == (run_id is None):
        raise marks.MarksError("TARGET_INVALID", "name one memory (--id N) or one run (--run RUN_ID), not both")
    if memory_id is not None and not (type(memory_id) is int and 0 < memory_id <= marks.MAX_ID):
        raise marks.MarksError("TARGET_INVALID", f"{ascii(memory_id)[:80]} is not a memory id")
    if run_id is not None and not marks.storable(run_id):
        raise marks.MarksError("TARGET_INVALID", f"{ascii(run_id)[:80]} is not a run id")


def _is_mixed(conn: sqlite3.Connection, memory_id: int, run_id: str) -> bool:
    """A row that came from this run AND from something else: another run, or a parent that is not the run's."""
    if conn.execute("SELECT 1 FROM memory_research_runs WHERE memory_id = ? AND run_id <> ?",
                    (memory_id, run_id)).fetchone():
        return True
    return any(conn.execute("SELECT 1 FROM memory_research_runs WHERE memory_id = ? AND run_id = ?",
                            (pid, run_id)).fetchone() is None for pid in marks.parents_of(conn, memory_id))


def forget(db: Union[str, Path], *, memory_id: Optional[int] = None, run_id: Optional[str] = None,
           dry_run: bool = False, include_mixed: bool = False) -> Dict[str, Any]:
    """Archive one memory, or every row of one run. Rows written from the run by other organs go with it; a row that also
    came from elsewhere is listed as mixed and left, unless `include_mixed`."""
    _one_target(memory_id, run_id)
    conn = _open(db, "forget", write=not dry_run)
    try:
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")
        mixed: List[int] = []
        if memory_id is not None:
            if conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone() is None:
                raise marks.MarksError("MEMORY_NOT_FOUND", f"memory {memory_id} not found")
            ids = [memory_id]
        else:
            if conn.execute("SELECT 1 FROM research_runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                raise marks.MarksError("RUN_UNKNOWN", f"no registered run {run_id!r}")
            linked = conn.execute("SELECT memory_id, inherited FROM memory_research_runs WHERE run_id = ? "
                                  "ORDER BY memory_id", (run_id,)).fetchall()
            mixed = [m for m, inherited in linked if inherited and _is_mixed(conn, m, str(run_id))]
            ids = [m for m, _i in linked if include_mixed or m not in mixed]
        before = _archived(conn, ids)
        todo = [m for m in ids if before.get(m) == 0]
        receipt_id = uuid.uuid4().hex
        if not dry_run:
            for m in todo:
                conn.execute("UPDATE memories SET is_archived = 1, updated_at = datetime('now') WHERE id = ?", (m,))
                conn.execute("INSERT INTO forget_receipts (receipt_id, memory_id, run_id, before_archived, after_updated_at) "
                             "SELECT ?, id, ?, 0, updated_at FROM memories WHERE id = ?", (receipt_id, run_id, m))
        after = _archived(conn, ids)
        if not dry_run:
            conn.execute("COMMIT")
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()
    target = f"--id {memory_id}" if memory_id is not None else f"--run {run_id}"
    return {"status": "ok", "action": "forget", "dry_run": dry_run, "receipt": {
        "verb": "forget", "receipt_id": None if dry_run else receipt_id, "ids": ids,
        "rows_changed": 0 if dry_run else len(todo), "would_change": len(todo), "reversible": True,
        "undo": f"voidloom unarchive {target}", "before": {"archived": _keys(before)},
        "after": {"archived": _keys(after)}, "mixed": mixed, "mixed_included": bool(include_mixed and mixed)}}


def unarchive(db: Union[str, Path], *, memory_id: Optional[int] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Undo `forget` for one memory or one run: each row goes back to the state its receipt recorded, if it is still as
    `forget` left it. A row somebody archived, restored or edited since is left, and listed with the reason. A row no
    receipt names is not this verb's."""
    _one_target(memory_id, run_id)
    conn = _open(db, "unarchive")
    try:
        conn.execute("BEGIN IMMEDIATE")
        where, arg = ("memory_id = ?", memory_id) if memory_id is not None else ("run_id = ?", run_id)
        rows = conn.execute("SELECT id, memory_id, before_archived, after_updated_at, superseded_at FROM forget_receipts "
                            f"WHERE {where} AND undone_at IS NULL ORDER BY id DESC", (arg,)).fetchall()
        skipped: Dict[int, str] = {}
        want: Dict[int, int] = {}
        done: List[int] = []
        for rid, m, was, stamp, superseded in rows:     # newest first, so an older receipt for the same row decides
            now = conn.execute("SELECT is_archived, updated_at FROM memories WHERE id = ?", (m,)).fetchone()
            if now is None:
                skipped[m] = "the memory is gone"
            elif superseded is not None or now[0] != 1:
                skipped[m] = "it was archived or restored after the forget"
            elif now[1] != stamp:
                skipped[m] = "it was changed after the forget"
            else:
                want[m] = was
                done.append(rid)
        before = _archived(conn, want)
        for rid in done:                                # closed first: this archive change is the receipt's own
            conn.execute("UPDATE forget_receipts SET undone_at = datetime('now') WHERE id = ?", (rid,))
        for m, was in want.items():
            conn.execute("UPDATE memories SET is_archived = ?, updated_at = datetime('now') WHERE id = ?", (was, m))
        after = _archived(conn, want)
        conn.execute("COMMIT")
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()
    target = f"--id {memory_id}" if memory_id is not None else f"--run {run_id}"
    out = {"status": "ok", "action": "unarchive", "receipt": {
        "verb": "unarchive", "ids": sorted(want), "rows_changed": len(want), "reversible": True,
        "undo": f"voidloom forget {target}", "before": {"archived": _keys(before)}, "after": {"archived": _keys(after)},
        "skipped": [{"id": m, "reason": skipped[m]} for m in sorted(skipped) if m not in want]}}
    if not rows:
        out["note"] = "nothing to undo: `forget` archived no such row, and a row archived another way is left as it is"
    return out


def _archived(conn: sqlite3.Connection, ids: Iterable[int]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for m in ids:
        row = conn.execute("SELECT COALESCE(is_archived, 0) FROM memories WHERE id = ?", (m,)).fetchone()
        if row is not None:
            out[m] = row[0]
    return out


def _keys(d: Dict[int, int]) -> Dict[str, int]:
    return {str(k): v for k, v in sorted(d.items())}


def _roll(conn: sqlite3.Connection) -> None:
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _typed(e: Exception) -> Exception:
    return marks.MarksError("BRAIN_UNREADABLE", f"{type(e).__name__}: {e}. Nothing was changed.") \
        if isinstance(e, sqlite3.Error) else e


# --------------------------------------------------------------------------------------------------------------------
# The backfill
# --------------------------------------------------------------------------------------------------------------------

def _edges(conn: sqlite3.Connection) -> Dict[int, Set[int]]:
    """parent -> children, over recorded derivations and cluster membership. `edge_inference` is a comparison, not a
    derivation, and similarity is not either."""
    q = ",".join("?" * len(marks.DERIVATION_TYPES))
    out: Dict[int, Set[int]] = {}
    for parent, child in conn.execute(
            "SELECT source_memory_id, consolidated_memory_id FROM consolidation_sources "
            f"UNION SELECT source_memory_id, derived_memory_id FROM memory_provenance WHERE transformation_type IN ({q}) "
            "UNION SELECT parent_id, child_id FROM memory_parents "
            "UNION SELECT je.value, rc.synthesis_memory_id FROM raptor_clusters rc, "
            "json_each(CASE WHEN json_valid(rc.member_ids_json) THEN rc.member_ids_json ELSE '[]' END) je "
            "WHERE rc.synthesis_memory_id IS NOT NULL", marks.DERIVATION_TYPES):
        if type(parent) is int and type(child) is int:
            out.setdefault(parent, set()).add(child)
    return out


def _reach(edges: Dict[int, Set[int]], seeds: Iterable[int]) -> Set[int]:
    seen: Set[int] = set()
    todo = list(seeds)
    while todo:
        for child in edges.get(todo.pop(), ()):
            if child not in seen:
                seen.add(child)
                todo.append(child)
    return seen


def _candidates(conn: sqlite3.Connection) -> Dict[str, List[Any]]:
    """What the recorded derivations say and the rows do not: rows to mark outside, rows to mark research, and the run
    links a research row's descendants lack."""
    state = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, untrusted_source, research_mark FROM memories")}
    edges = _edges(conn)
    outside = _reach(edges, [i for i, (o, _r) in state.items() if marks.state_of(o) != marks.CLEAN])
    research = _reach(edges, [i for i, (_o, r) in state.items() if r == 1])
    links = conn.execute("SELECT memory_id, run_id, config_id FROM memory_research_runs ORDER BY rowid").fetchall()
    held = {(m, run) for m, run, _c in links}
    lacking: Dict[Any, Any] = {}
    for m, run, config in links:
        for child in _reach(edges, [m]):
            if child in state and (child, run) not in held:
                lacking[(child, run)] = (child, run, config)
    return {"outside": sorted(i for i in outside if i in state and state[i][0] == 0),
            "research": sorted(i for i in research if i in state and state[i][1] == 0),
            "links": list(lacking.values())}


def _marked_parents(conn: sqlite3.Connection, memory_id: int) -> Dict[str, List[int]]:
    """The recorded parents of a row that are outside, and the ones that are research. One that is gone is outside."""
    parents = marks.parents_of(conn, memory_id)
    read = marks.read_marks(conn, parents)
    return {"outside": sorted(p for p in parents if read[p].outside), "research": sorted(p for p in parents if read[p].research)}


def _runs_of(conn: sqlite3.Connection, memory_id: int) -> Set[str]:
    return {r[0] for r in conn.execute("SELECT run_id FROM memory_research_runs WHERE memory_id = ?", (memory_id,))}


def backfill(db: Union[str, Path], *, dry_run: bool = False, yes_many: bool = False) -> Dict[str, Any]:
    """Mark every row already written from a marked row, to any depth, and link it to the research runs its ancestors
    name. Over BACKFILL_LIMIT rows it stops and changes nothing unless `yes_many`."""
    conn = _open(db, "marks backfill", write=not dry_run)
    try:
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")
        found = _candidates(conn)
        ids = sorted(set(found["outside"]) | set(found["research"]) | {m for m, _run, _config in found["links"]})
        out: Dict[str, Any] = {"status": "ok", "action": "marks-backfill", "dry_run": dry_run, "batch_id": None,
                               "rows_to_mark": len(ids), "rows_changed": 0, "ids": ids, "limit": BACKFILL_LIMIT,
                               "reversible": True, "undo": "voidloom marks backfill --undo"}
        if dry_run or not ids:
            _roll(conn)
            return out
        if len(ids) > BACKFILL_LIMIT and not yes_many:
            raise marks.MarksError("BACKFILL_HALTED", f"{len(ids)} rows would be marked, over the limit of "
                                   f"{BACKFILL_LIMIT}. Nothing was changed. Look at --dry-run, then pass --yes-many.",
                                   rows_to_mark=len(ids), limit=BACKFILL_LIMIT)
        batch = uuid.uuid4().hex
        had = {m: _runs_of(conn, m) for m in ids}
        for m in ids:
            conn.execute("INSERT INTO marks_backfill_preimage (batch_id, memory_id, before_outside, before_research_mark, "
                         "before_run_id, before_config_id) SELECT ?, id, untrusted_source, research_mark, research_run_id, "
                         "research_config_id FROM memories WHERE id = ?", (batch, m))
        for m in found["outside"]:
            conn.execute("UPDATE memories SET untrusted_source = 1 WHERE id = ? AND untrusted_source = 0", (m,))
        for m in found["research"]:
            conn.execute("UPDATE memories SET research_mark = 1, untrusted_source = 1 WHERE id = ? AND research_mark = 0", (m,))
        for m, run, config in found["links"]:
            conn.execute("INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited) "
                         "VALUES (?, ?, ?, 1)", (m, run, config))
        for m in ids:                       # why each was marked, so that an undo can tell these reasons from later ones
            because = _marked_parents(conn, m)
            conn.execute("UPDATE marks_backfill_preimage SET because_outside = ?, because_research = ?, links_added = ? "
                         "WHERE batch_id = ? AND memory_id = ?",
                         (json.dumps(because["outside"]), json.dumps(because["research"]),
                          json.dumps(sorted(_runs_of(conn, m) - had[m])), batch, m))
        left = _candidates(conn)
        if any(left.values()):
            raise marks.MarksError("BACKFILL_INCOMPLETE", "rows were left unmarked after the pass; nothing was changed")
        conn.execute("COMMIT")
        out.update(batch_id=batch, rows_changed=len(ids))
        return out
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()


_IMMUTABLE = ("trg_untrusted_source_immutable", "trg_marks_research_immutable", "trg_marks_run_write_once")


def _no_longer_only_ours(conn: sqlite3.Connection, batch: Dict[int, Dict[str, Any]], kind: str) -> Set[int]:
    """Which rows of the batch have come to be marked for another reason: a marked parent recorded since, a parent in the
    batch that has one, or their own words saying so. To a fixed point, because a reason reaches a row's children."""
    ours = {m for m, was in batch.items() if was["before"][kind] == 0}
    keep: Set[int] = set()
    for m in ours:
        if kind == "outside":
            row = conn.execute("SELECT source_project, derived_from FROM memories WHERE id = ?", (m,)).fetchone()
            said = row is not None and (row[0] == "gap-research" or row[1] == marks.SENTINEL or conn.execute(
                "SELECT 1 FROM memory_tags WHERE memory_id = ? AND tag = 'gap-research'", (m,)).fetchone() is not None)
        else:
            said = conn.execute("SELECT 1 FROM memory_research_runs WHERE memory_id = ? AND inherited = 0",
                                (m,)).fetchone() is not None
        if said:
            keep.add(m)
    parents = {m: _marked_parents(conn, m)[kind] for m in ours}
    grown = True
    while grown:
        grown = False
        for m in ours - keep:
            if any(p in keep or (p not in ours and p not in batch[m]["because"][kind]) for p in parents[m]):
                keep.add(m)
                grown = True
    return keep


def backfill_undo(db: Union[str, Path]) -> Dict[str, Any]:
    """Undo the last backfill: every row whose mark is there only because of it goes back to its pre-image. A row that has
    since been given another reason (a new marked parent, its own words) keeps its mark, as does everything written from
    it, and is listed. The triggers that forbid clearing a mark are dropped and created again inside one transaction, so
    no other connection ever sees the brain without them, and a failure restores them."""
    conn = _open(db, "marks backfill --undo")
    try:
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute("SELECT batch_id FROM marks_backfill_preimage WHERE undone_at IS NULL "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        out: Dict[str, Any] = {"status": "ok", "action": "marks-backfill-undo", "batch_id": last[0] if last else None,
                               "rows_changed": 0, "ids": [], "kept": []}
        if last is None:
            _roll(conn)
            out["note"] = "no backfill to undo"
            return out
        batch: Dict[int, Dict[str, Any]] = {}
        for m, was_outside, was_research, run, config, why_outside, why_research, links in conn.execute(
                "SELECT memory_id, before_outside, before_research_mark, before_run_id, before_config_id, because_outside, "
                "because_research, links_added FROM marks_backfill_preimage WHERE batch_id = ? AND undone_at IS NULL",
                (last[0],)).fetchall():
            batch[m] = {"before": {"outside": was_outside, "research": was_research}, "run": run, "config": config,
                        "because": {"outside": json.loads(why_outside or "[]"), "research": json.loads(why_research or "[]")},
                        "links": json.loads(links or "[]")}
        keep_research = _no_longer_only_ours(conn, batch, "research")
        keep_outside = _no_longer_only_ours(conn, batch, "outside") | keep_research
        held = {n: sql for n, sql in conn.execute(
            f"SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name IN ({','.join('?' * len(_IMMUTABLE))})", _IMMUTABLE)}
        for name in held:
            conn.execute(f"DROP TRIGGER {name}")
        changed: List[int] = []
        for m, was in batch.items():
            new_parent = set(_marked_parents(conn, m)["research"]) - set(was["because"]["research"]) - set(batch)
            if m not in keep_research and not (was["before"]["research"] == 1 and new_parent):
                for run in was["links"]:
                    conn.execute("DELETE FROM memory_research_runs WHERE memory_id = ? AND run_id = ? AND inherited = 1", (m, run))
                conn.execute("UPDATE memories SET research_mark = ?, research_run_id = ?, research_config_id = ? WHERE id = ?",
                             (was["before"]["research"], was["run"], was["config"], m))
            if m not in keep_outside:
                conn.execute("UPDATE memories SET untrusted_source = ? WHERE id = ?", (was["before"]["outside"], m))
                changed.append(m)
        for sql in held.values():
            conn.execute(sql)
        conn.execute("UPDATE marks_backfill_preimage SET undone_at = datetime('now') WHERE batch_id = ?", (last[0],))
        back = {r[0] for r in conn.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN ({','.join('?' * len(_IMMUTABLE))})", _IMMUTABLE)}
        if back != set(held):
            raise marks.MarksError("BACKFILL_UNDO_FAILED", "a trigger could not be created again; nothing was changed")
        conn.execute("COMMIT")
        out.update(rows_changed=len(changed), ids=sorted(changed), kept=[
            {"id": m, "reason": "it is marked for another reason since the backfill"} for m in sorted(keep_outside)])
        return out
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()

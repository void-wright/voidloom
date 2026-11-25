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

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Union

import marks

BACKFILL_LIMIT = 200


def _open(db: Union[str, Path], what: str, write: bool = True) -> sqlite3.Connection:
    if not Path(db).exists():
        raise marks.MarksError("BRAIN_UNREADABLE", f"no database at {db}")
    if write:
        conn = sqlite3.connect(str(db), timeout=60.0, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        conn = sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True, timeout=60.0)
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
                conn.execute("INSERT INTO forget_receipts (receipt_id, memory_id, run_id, before_archived) "
                             "VALUES (?, ?, ?, 0)", (receipt_id, m, run_id))
                conn.execute("UPDATE memories SET is_archived = 1, updated_at = datetime('now') WHERE id = ?", (m,))
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
    """Undo `forget` for one memory or one run: each row goes back to the state its receipt recorded. A row no receipt
    names is left as it is and listed."""
    _one_target(memory_id, run_id)
    conn = _open(db, "unarchive")
    try:
        conn.execute("BEGIN IMMEDIATE")
        where, arg = ("memory_id = ?", memory_id) if memory_id is not None else ("run_id = ?", run_id)
        rows = conn.execute(f"SELECT id, memory_id, before_archived FROM forget_receipts WHERE {where} "
                            "AND undone_at IS NULL ORDER BY id DESC", (arg,)).fetchall()
        want: Dict[int, int] = {}
        for _rid, m, was in rows:               # newest first, so an older receipt for the same row decides
            want[m] = was
        before = _archived(conn, want)
        changed = 0
        for m, was in want.items():
            if m in before and before[m] != was:
                conn.execute("UPDATE memories SET is_archived = ?, updated_at = datetime('now') WHERE id = ?", (was, m))
                changed += 1
        for rid, _m, _was in rows:
            conn.execute("UPDATE forget_receipts SET undone_at = datetime('now') WHERE id = ?", (rid,))
        after = _archived(conn, want)
        conn.execute("COMMIT")
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()
    target = f"--id {memory_id}" if memory_id is not None else f"--run {run_id}"
    out = {"status": "ok", "action": "unarchive", "receipt": {
        "verb": "unarchive", "ids": sorted(want), "rows_changed": changed, "reversible": True,
        "undo": f"voidloom forget {target}", "before": {"archived": _keys(before)}, "after": {"archived": _keys(after)}}}
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


def _candidates(conn: sqlite3.Connection) -> Dict[str, List[int]]:
    state = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, untrusted_source, research_mark FROM memories")}
    edges = _edges(conn)
    outside = _reach(edges, [i for i, (o, _r) in state.items() if marks.state_of(o) != marks.CLEAN])
    research = _reach(edges, [i for i, (_o, r) in state.items() if r == 1])
    return {"outside": sorted(i for i in outside if i in state and state[i][0] == 0),
            "research": sorted(i for i in research if i in state and state[i][1] == 0)}


def backfill(db: Union[str, Path], *, dry_run: bool = False, yes_many: bool = False) -> Dict[str, Any]:
    """Mark every row already written from a marked row, to any depth. Over BACKFILL_LIMIT rows it stops and changes
    nothing unless `yes_many`."""
    conn = _open(db, "marks backfill", write=not dry_run)
    try:
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")
        found = _candidates(conn)
        ids = sorted(set(found["outside"]) | set(found["research"]))
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
        for m in ids:
            conn.execute("INSERT INTO marks_backfill_preimage (batch_id, memory_id, before_outside, before_research_mark) "
                         "SELECT ?, id, untrusted_source, research_mark FROM memories WHERE id = ?", (batch, m))
        for m in found["outside"]:
            conn.execute("UPDATE memories SET untrusted_source = 1 WHERE id = ? AND untrusted_source = 0", (m,))
        for m in found["research"]:
            conn.execute("UPDATE memories SET research_mark = 1, untrusted_source = 1 WHERE id = ? AND research_mark = 0", (m,))
        left = _candidates(conn)
        if left["outside"] or left["research"]:
            raise marks.MarksError("BACKFILL_INCOMPLETE", "rows were left unmarked after the pass; nothing was changed")
        conn.execute("COMMIT")
        out.update(batch_id=batch, rows_changed=len(ids))
        return out
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()


_IMMUTABLE = ("trg_untrusted_source_immutable", "trg_marks_research_immutable")


def backfill_undo(db: Union[str, Path]) -> Dict[str, Any]:
    """Restore the pre-image of the last backfill. The two triggers that forbid clearing a mark are dropped and created
    again inside one transaction, so no other connection ever sees the brain without them, and a failure restores both."""
    conn = _open(db, "marks backfill --undo")
    try:
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute("SELECT batch_id FROM marks_backfill_preimage WHERE undone_at IS NULL "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        out: Dict[str, Any] = {"status": "ok", "action": "marks-backfill-undo", "batch_id": last[0] if last else None,
                               "rows_changed": 0, "ids": []}
        if last is None:
            _roll(conn)
            out["note"] = "no backfill to undo"
            return out
        held = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?)", _IMMUTABLE))
        for name in held:
            conn.execute(f"DROP TRIGGER {name}")
        rows = conn.execute("SELECT memory_id, before_outside, before_research_mark FROM marks_backfill_preimage "
                            "WHERE batch_id = ? AND undone_at IS NULL", (last[0],)).fetchall()
        for m, outside, research in rows:
            conn.execute("UPDATE memories SET research_mark = ?, untrusted_source = ? WHERE id = ?", (research, outside, m))
        for sql in held.values():
            conn.execute(sql)
        conn.execute("UPDATE marks_backfill_preimage SET undone_at = datetime('now') WHERE batch_id = ?", (last[0],))
        back = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?)", _IMMUTABLE)}
        if back != set(held):
            raise marks.MarksError("BACKFILL_UNDO_FAILED", "a trigger could not be created again; nothing was changed")
        conn.execute("COMMIT")
        out.update(rows_changed=len(rows), ids=sorted(r[0] for r in rows))
        return out
    except Exception as e:
        _roll(conn)
        raise _typed(e)
    finally:
        conn.close()

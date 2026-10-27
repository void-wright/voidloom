#!/usr/bin/env python3
"""embed_backfill — give every live memory a vector in THIS brain's active tier.

WHY IT EXISTS. A fresh install loaded 66 seed memories and held **0 vectors**:
100% of a new brain's content was keyword-only, and no shipped verb could fix it.
`voidloom/cli.py:380-383` had already named the cause -- the one existing backfill,
`embedding_upgrade.cmd_re_embed`, is hardcoded to Jina -> `memory_embeddings_v2`
and is not generic, so it is wrong for a nomic brain and wrong for a qwen brain,
which between them are every brain the installer builds.

WHEN IT RUNS, and this is Drew's ruling (2026-09-17): **at install time.** The
three candidates were install / first recall / an explicit verb. Lazy embedding
does not save the work, it MOVES it -- into the user's first moment of need, which
is the worst possible moment, because they asked for recall precisely *because*
they are mid-task. Install is already a go-and-make-tea operation with a progress
narration; the cost is invisible there and intolerable at recall. His words:
*"That way it's already pre-primed and ready to go … I would classify the embedding
as installation."*

The corollary binds this module: when embedding CANNOT happen -- Tier 0, no torch,
no model, offline -- it must SAY the brain is keyword-only, loudly and at install,
rather than leaving the user to discover it at their first recall.

WHAT IT DOES NOT DO. It does not choose a tier, encode, or write a vector: it
enumerates and it calls. `memory_write.attach_embedding` already resolves the tier
from the CONNECTION, asserts the vector's width against that tier, writes it, and
tags a failure `embedding-missing` so it is findable by SQL. Re-implementing any
of that here would be a second answer to a question that already has one.

CONTRACT
  * IDEMPOTENT: only memories with no row in the active table are touched. Running
    it twice embeds nothing the second time.
  * DERIVED, NEVER TYPED: every number reported is counted at the moment of
    asking, including the denominator.
  * NEVER RAISES on a missing stack. A host with no torch gets
    `status: "refused"`, `code: "encoder_unavailable"`, and a sentence saying the
    brain is keyword-only -- which is a true statement about a working brain, not
    an error. Exit 3, so an installer can tell it apart from a failure (1).
  * UNKNOWN IS NOT ZERO: if the tier cannot be resolved, it refuses. It does not
    embed into the dormant table, which is what "defaulting" would mean here.

    python3 organs/embed_backfill.py <db> [--limit N] [--dry-run] [--json]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any, Dict, Optional


def _organs_on_path() -> None:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def _connect(db_path: str) -> sqlite3.Connection:
    """A connection that can SEE the tier tables.

    The tier tables are `vec0` VIRTUAL tables, so a bare ``sqlite3.connect``
    cannot query them: it raises ``no such module: vec0``, which reads exactly
    like "the semantic tier is absent" and is not. This module's own first run
    made that mistake and reported a 17,151-memory brain as keyword-only.
    (The same confusion cost a Tier-1 install gate earlier in this cycle, which
    is why the engine loads the extension in its own ``_get_conn`` the same way.)

    Loading the extension is BEST-EFFORT: a host with no ``sqlite_vec`` genuinely
    has no semantic tier, and the caller's own refusal path says so honestly.
    """
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception:
            pass          # no extension on this host; the refusal path is correct
    return conn


def count_unembedded(conn: sqlite3.Connection, table: str) -> int:
    """Live memories with no vector in `table`. The denominator, counted now."""
    return conn.execute(
        f"SELECT COUNT(*) FROM memories m "
        f"WHERE COALESCE(m.is_archived, 0) = 0 "
        f"  AND NOT EXISTS (SELECT 1 FROM {table} e WHERE e.memory_id = m.id)"
    ).fetchone()[0]


def backfill(db_path: str, limit: Optional[int] = None,
             dry_run: bool = False) -> Dict[str, Any]:
    """Embed every live memory missing a vector in the active tier.

    Returns an envelope. Never raises for an absent encoder or an unresolved
    tier -- both are REFUSALS with a named code, because both describe a brain
    that works and is keyword-only, not a brain that is broken.
    """
    _organs_on_path()
    out: Dict[str, Any] = {"status": "ok", "code": "backfill_ok", "db": db_path,
                           "table": None, "dim": None,
                           "live_memories": None, "unembedded_before": None,
                           "embedded": 0, "failed": 0, "unembedded_after": None,
                           "dry_run": bool(dry_run), "errors": []}
    try:
        import embed_resolver
        import memory_write
    except Exception as e:
        out.update({"status": "refused", "code": "organs_unavailable",
                    "message": f"cannot import the embedding organs: "
                               f"{type(e).__name__}: {e}"})
        return out

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            table, dim = embed_resolver.resolve(conn)
        except Exception as e:
            # UNKNOWN, NOT ZERO. Defaulting here means writing into whichever
            # table happens to exist, which is how a brain ends up with vectors
            # in a space nothing reads.
            out.update({"status": "refused", "code": "tier_unresolved",
                        "message": (f"this brain does not declare an embedding tier "
                                    f"({type(e).__name__}: {e}). Refusing to guess a "
                                    "table: same dimension is not the same space.")})
            return out
        out["table"], out["dim"] = table, dim
        out["live_memories"] = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0"
        ).fetchone()[0]

        try:
            missing = count_unembedded(conn, table)
        except sqlite3.Error as e:
            out.update({"status": "refused", "code": "tier_table_absent",
                        "message": (f"the active tier's table {table!r} is not "
                                    f"queryable ({e}). The semantic tier is not "
                                    "installed; this brain is keyword-only.")})
            return out
        out["unembedded_before"] = missing

        if missing == 0:
            out["code"] = "backfill_nothing_to_do"
            out["unembedded_after"] = 0
            out["message"] = (f"every live memory already has a vector in {table}. "
                              "Nothing to do, and that is a measurement, not a default.")
            return out

        rows = conn.execute(
            f"SELECT m.id, m.content FROM memories m "
            f"WHERE COALESCE(m.is_archived, 0) = 0 "
            f"  AND NOT EXISTS (SELECT 1 FROM {table} e WHERE e.memory_id = m.id) "
            f"ORDER BY m.id" + (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()

        if dry_run:
            out["code"] = "backfill_plan"
            out["would_embed"] = len(rows)
            out["unembedded_after"] = missing
            out["message"] = (f"would embed {len(rows)} of {missing} unembedded live "
                              f"memories into {table} ({dim}-wide). Nothing written.")
            return out

        # ONE probe before the loop: an absent encoder is a property of the HOST,
        # not of memory #1, and discovering it 66 times in a row would bury the
        # one sentence the operator needs under 66 identical tracebacks.
        try:
            probe = memory_write.default_encoder("probe", conn)
            memory_write.assert_width(bytes(probe), dim)
        except Exception as e:
            out.update({"status": "refused", "code": "encoder_unavailable",
                        "unembedded_after": missing,
                        "message": (
                            f"no usable encoder for tier {table!r} on this host "
                            f"({type(e).__name__}: {e}). NOTHING WAS WRITTEN and "
                            f"nothing is broken: {missing} memories remain "
                            "keyword-only (FTS5), which is Tier 0 working as "
                            "designed. Install the Tier-1 stack and re-run this to "
                            "make them semantically findable.")})
            return out

        for r in rows:
            res = memory_write.attach_embedding(
                conn, r["id"], r["content"] or "", policy="best_effort",
                organ="embed_backfill")
            if res.get("embedded"):
                out["embedded"] += 1
            else:
                out["failed"] += 1
                if len(out["errors"]) < 5:
                    out["errors"].append({"memory_id": r["id"],
                                          "error": res.get("error")})
        conn.commit()
        out["unembedded_after"] = count_unembedded(conn, table)
        if out["failed"]:
            out["status"] = "incomplete"
            out["code"] = "backfill_incomplete"
            out["message"] = (
                f"embedded {out['embedded']}, FAILED {out['failed']}. The failures "
                "are tagged 'embedding-missing' in memory_tags and are findable by "
                "SQL. A partial backfill is not an ok.")
        else:
            out["message"] = (f"embedded {out['embedded']} memories into {table} "
                              f"({dim}-wide); {out['unembedded_after']} remain.")
        return out
    finally:
        conn.close()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Embed every live memory missing a vector in this brain's "
                    "ACTIVE tier. Idempotent; refuses rather than guessing a tier.")
    ap.add_argument("db_path")
    ap.add_argument("--limit", type=int, default=None,
                    help="embed at most N (the rest stay queued for the next run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="count and report; write nothing")
    ap.add_argument("--json", action="store_true", help="envelope only")
    args = ap.parse_args(argv)

    res = backfill(args.db_path, limit=args.limit, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"  tier        : {res.get('table')} ({res.get('dim')}-wide)")
        print(f"  live memories: {res.get('live_memories')}")
        print(f"  unembedded  : {res.get('unembedded_before')} before, "
              f"{res.get('unembedded_after')} after")
        if not args.dry_run:
            print(f"  embedded    : {res.get('embedded')}   failed: {res.get('failed')}")
        print(f"  {res.get('status').upper()}: {res.get('message', res.get('code'))}")
        for e in res.get("errors", []):
            print(f"    memory {e['memory_id']}: {e['error']}")
    # 0 ok · 1 incomplete (some failed) · 3 refused (nothing written, nothing broken)
    return {"ok": 0, "incomplete": 1, "refused": 3}.get(res["status"], 1)


if __name__ == "__main__":
    sys.exit(main())

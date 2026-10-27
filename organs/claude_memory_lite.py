#!/usr/bin/env python3
"""claude_memory_lite.py — the Voidloom brain LITE CLI (Tier 0).

WHY THIS EXISTS
---------------
The full claude_memory.py needs a heavy stack (sentence-transformers + a vector
extension + an embedding model). On many machines that stack is fragile or
unavailable: an old system python, no GPU, a network that blocks model
downloads, or a sandbox with a stdlib-only python3. This script uses ONLY the
Python standard library + the SQLite database (which carries an FTS5 full-text
index in `memories_fts`). Same DB, same schema as the full CLI — lighter reach.

It gives you a fully functional KEYWORD-recall brain on ANY python3, with zero
third-party dependencies and no network. It ALWAYS works. This is "Tier 0": the
guaranteed baseline. When the heavy stack ("Tier 1") is also installed, the full
CLI adds SEMANTIC recall on top of the very same database.

COMMANDS
--------
  recall     — FTS5 keyword search (no embeddings)
  list       — filter by category / tag / kind, limit N, order by recency|importance
  store      — insert a memory (FTS-indexed via triggers; embedding deferred to Tier 1)
  stats      — overview of DB contents
  prime      — print a session-start preamble (persona-neutral)
  context    — the full CLI's `context` JSON envelope, read-only here (R4: the facade calls it)
  load-seed  — load an agnostic seed pack JSON: a per-memory anchor delta (install + upgrade)

DB RESOLUTION (install-anywhere, no hardcoded absolute path)
------------------------------------------------------------
  1. $WIZARD_LIBRARY_DB        (explicit override; set by the installer/hook)
  2. $WIZARD_LIBRARY_HOME/claude_memory.db
  3. relative-self: <this file's dir>/claude_memory.db   (portable default)

SCHEMA NOTES (learned the hard way — keep these correct)
--------------------------------------------------------
  * `memories_fts` is an EXTERNAL-CONTENT FTS5 table (content='memories') kept in
    sync by AFTER INSERT/UPDATE/DELETE TRIGGERS on `memories`. Therefore `store`
    inserts ONLY into `memories` and lets the triggers index it. It MUST NOT
    manually INSERT into `memories_fts` — that double-indexes the row and corrupts
    search results. (This is the key fix vs. earlier lite-CLI drafts.)
  * The memory row uses columns `source_context` and `last_accessed`
    (NOT `context` / `last_accessed_at`).
  * Tags live in the `memory_tags` JOIN table (memory_id, tag) — never a column.
  * The `kind` / `confidence` columns exist only after the kind-taxonomy
    migration; `store` detects their presence and adapts so it works on either
    a bare schema or a fully-migrated one.

Stdlib only. No Unix-only imports. Cross-platform (macOS / Linux / Windows).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# --- DB resolution: env override > relative-self (never a hardcoded absolute) ---
def _resolve_db_path() -> Path:
    env_db = os.environ.get("WIZARD_LIBRARY_DB")
    if env_db:
        return Path(env_db)
    env_home = os.environ.get("WIZARD_LIBRARY_HOME")
    if env_home:
        return Path(env_home) / "claude_memory.db"
    return Path(__file__).resolve().parent.parent / "claude_memory.db"


DB_PATH = _resolve_db_path()

VALID_CATEGORIES = {
    "user_preference", "technical_decision", "project_knowledge", "error_pattern",
    "skill_lesson", "workflow_pattern", "people_context", "tool_optimization",
    "domain_knowledge", "campaign_state", "correction", "insight",
}
VALID_KINDS = {"episodic", "semantic", "procedural", "reflection"}


def _now() -> str:
    """UTC timestamp, second precision, matching the schema's text format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _connect(readonly: bool = False) -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.stderr.write(
            f"ERROR: brain DB not found at {DB_PATH}\n"
            "  Set WIZARD_LIBRARY_DB or run the installer to create it, "
            "or create it with the full CLI: python3 claude_memory.py init\n"
        )
        sys.exit(2)
    uri = f"file:{DB_PATH}?mode={'ro' if readonly else 'rw'}"
    return sqlite3.connect(uri, uri=True, timeout=60.0)


def _row_dict(cur: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[i] for i, col in enumerate(cur.description)}


# FTS5 reserves a handful of characters/keywords as query OPERATORS. A bare
# hyphen is NEGATION, a dot can be a syntax error, and the words AND/OR/NOT/NEAR
# (uppercase) flip into boolean mode. A natural-language query like
# 'keyword-only baseline' is therefore parsed as `keyword NOT only baseline` and
# raises "no such column: only" — or silently returns the wrong set. To make
# keyword recall behave intuitively, we sanitize: any token containing an FTS5
# special character is wrapped as a double-quoted PHRASE (matched literally,
# punctuation handled by the tokenizer), and lowercase boolean keywords so they
# are treated as plain search terms. Multi-word AND semantics are preserved.
_FTS5_SPECIAL = set('-."*():^')
_FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts_query(query: str) -> str:
    out = []
    for tok in query.split():
        if tok in _FTS5_KEYWORDS:
            # lowercase so it's a search term, not a boolean operator
            out.append(tok.lower())
        elif any(ch in _FTS5_SPECIAL for ch in tok):
            # wrap as a quoted phrase; escape any embedded double-quote
            out.append('"' + tok.replace('"', '""') + '"')
        else:
            out.append(tok)
    return " ".join(out)


def _has_columns(conn: sqlite3.Connection, table: str, cols) -> set:
    present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return {c for c in cols if c in present}


# ---------------------------------------------------------------------------
# recall — FTS5 keyword search
# ---------------------------------------------------------------------------
_SYNTH_CLAUSE = "\n      AND COALESCE(m.derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')"


def cmd_recall(args: argparse.Namespace) -> int:
    conn = _connect(readonly=True)
    c = conn.cursor()
    # FTS5 MATCH on memories_fts joined back to memories for metadata.
    # Order by FTS rank (bm25 — more negative = better), then importance, then recency.
    sql = """
    SELECT m.id, m.summary, m.content, m.category,
           m.importance, m.created_at,
           (SELECT GROUP_CONCAT(tag, ',') FROM memory_tags WHERE memory_id = m.id) AS tags,
           bm25(memories_fts) AS rank
    FROM memories_fts
    JOIN memories m ON m.rowid = memories_fts.rowid
    WHERE memories_fts MATCH ?
      AND m.is_archived = 0{synth}
    ORDER BY rank, m.importance DESC, m.created_at DESC
    LIMIT ?
    """
    # THE DREAM FIX (A3): the memory lane is memories only on Tier 0 too -- a dream or a playbook candidate is a
    # synthesis, excluded by provenance. (Tier 0 has no vectors, so it serves no side channel; it only keeps the
    # synthetic rows out of `results`.)
    sql = sql.replace("{synth}", _SYNTH_CLAUSE if "derived_from" in _has_columns(conn, "memories", ("derived_from",))
                      else "")
    # Sanitize per-token so hyphens/dots/operators in a natural-language query
    # behave as plain search terms (preserving multi-word AND semantics).
    sanitized = _sanitize_fts_query(args.query)
    try:
        rows = c.execute(sql, (sanitized, args.top_k)).fetchall()
    except sqlite3.OperationalError as e:
        # Last-resort fallback: wrap the WHOLE query as one literal phrase.
        try:
            phrase = '"' + args.query.replace('"', '""') + '"'
            rows = c.execute(sql, (phrase, args.top_k)).fetchall()
        except sqlite3.OperationalError:
            sys.stderr.write(
                "FTS query failed (use FTS5 syntax — try simpler keywords, or wrap "
                f"multi-word phrases in double quotes): {e}\n"
            )
            conn.close()
            return 1
    results = [_row_dict(c, r) for r in rows]
    out = {
        "status": "ok", "action": "recall", "mode": "fts5-keyword",
        "query": args.query, "result_count": len(results), "results": results,
    }
    print(json.dumps(out, default=str))
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# list — filter by category / tag / kind
# ---------------------------------------------------------------------------
def cmd_list(args: argparse.Namespace) -> int:
    conn = _connect(readonly=True)
    c = conn.cursor()
    has_kind = bool(_has_columns(conn, "memories", {"kind"}))
    where = ["is_archived = 0"]
    params: list = []
    if args.category:
        where.append("category = ?")
        params.append(args.category)
    if args.tag:
        where.append("id IN (SELECT memory_id FROM memory_tags WHERE tag = ?)")
        params.append(args.tag)
    if args.kind:
        if has_kind:
            where.append("kind = ?")
            params.append(args.kind)
        else:
            sys.stderr.write("[lite] --kind ignored: this DB has no `kind` column "
                             "(kind-taxonomy migration not applied).\n")
    select_cols = "id, summary, category, importance, created_at"
    if has_kind:
        select_cols = "id, summary, category, kind, importance, created_at"
    order = ("importance DESC, " if args.by == "importance" else "") + "created_at DESC"
    sql = f"""
    SELECT {select_cols}
    FROM memories
    WHERE {' AND '.join(where)}
    ORDER BY {order}
    LIMIT ?
    """
    params.append(args.limit)
    rows = c.execute(sql, params).fetchall()
    results = [_row_dict(c, r) for r in rows]
    print(json.dumps({"status": "ok", "action": "list",
                      "count": len(results), "results": results}, default=str))
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# store — insert ONLY into `memories`; FTS triggers index it automatically
# ---------------------------------------------------------------------------
def _insert_memory(conn: sqlite3.Connection, *, content: str, summary: str,
                   category: str, kind: str, importance: float,
                   project: str, context, tags) -> int:
    """Insert a single memory row, adapting to whether kind/confidence exist.

    Returns the new memory id. Does NOT touch memories_fts — the AFTER INSERT
    trigger on `memories` keeps the FTS index in sync. Manually inserting would
    double-index (external-content FTS5) and corrupt recall.
    """
    now = _now()
    cols = _has_columns(conn, "memories", {"kind", "confidence"})
    has_kind = "kind" in cols
    has_conf = "confidence" in cols

    base_cols = ["content", "summary", "category", "importance",
                 "source_project", "source_context", "created_at",
                 "updated_at", "last_accessed", "access_count", "is_archived"]
    base_vals = [content, summary, category, importance, project, context,
                 now, now, now, 0, 0]
    if has_kind:
        base_cols.append("kind")
        base_vals.append(kind)
    if has_conf:
        base_cols.append("confidence")
        base_vals.append(1.0)

    placeholders = ", ".join("?" for _ in base_cols)
    cur = conn.execute(
        f"INSERT INTO memories ({', '.join(base_cols)}) VALUES ({placeholders})",
        base_vals,
    )
    mid = cur.lastrowid
    if tags:
        tag_list = tags.split(",") if isinstance(tags, str) else list(tags)
        for tag in tag_list:
            tag = tag.strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (mid, tag),
                )
    return mid


def cmd_store(args: argparse.Namespace) -> int:
    if args.category not in VALID_CATEGORIES:
        sys.stderr.write(f"ERROR: invalid category '{args.category}'. "
                         f"Valid: {sorted(VALID_CATEGORIES)}\n")
        return 2
    if args.kind not in VALID_KINDS:
        sys.stderr.write(f"ERROR: invalid kind '{args.kind}'. "
                         f"Valid: {sorted(VALID_KINDS)}\n")
        return 2
    conn = _connect(readonly=False)
    summary = args.summary or (args.content[:100] + ("..." if len(args.content) > 100 else ""))
    mid = _insert_memory(
        conn, content=args.content, summary=summary, category=args.category,
        kind=args.kind, importance=args.importance, project=args.project,
        context=args.context, tags=args.tags,
    )
    conn.commit()
    conn.close()
    print(json.dumps({
        "status": "ok", "action": "store", "memory_id": mid,
        "importance": args.importance,   # the value PERSISTED (lite applies no surprise adjustment): the H4 echo contract
        "mode": "fts5-keyword", "embedding": "deferred",
        "note": ("Stored + FTS-indexed (keyword recall works now). Semantic embedding "
                 "is deferred — install Tier 1 (full CLI) to add semantic recall; it "
                 "can re-embed all memories that lack a vector."),
    }))
    return 0


# ---------------------------------------------------------------------------
# load-seed — load an agnostic seed pack into a brain: a PER-MEMORY ANCHOR DELTA
# ---------------------------------------------------------------------------
# Until seed pack 1.5.0 this was all-or-nothing: any memory already carrying the pack's
# common tag made the whole load a "skipped", so a brain seeded once (every upgraded
# brain) never received a memory added to the pack later. Now every entry carries its
# own anchor tag (`anchor:seed_<name>`) and the load is a DELTA over anchors:
#
#   present   a memory carries the anchor at this revision                -> nothing
#   adopted   an un-anchored seed memory (loaded from an older pack) has the
#             entry's exact words                                          -> its anchor
#             and revision tags are ADDED to that row; no new memory
#   changed   the anchored (or un-anchored) memory holds an OLDER revision
#             the entry names in `supersedes`                              -> that row is
#             archived (never deleted) and tagged `seed-superseded`; the new
#             revision is inserted
#   new       nothing matches                                              -> inserted
#             ...and an anchored memory that no pack load wrote (no seed-rev tag: a
#             draft of the entry stored by hand before the pack carried it) is
#             superseded the same way
#   left      the anchored memory holds a revision a pack load wrote that this
#             pack does not know (a NEWER pack loaded it)                  -> left as found
#   held-untagged  a memory WITHOUT the pack's common tag (memory_tags lost its rows, or
#             the words were stored by hand) holds the entry's words -- this revision or an
#             older one it supersedes -- or carries its anchor            -> NOTHING: never
#             inserted again (that would duplicate a memory the brain holds), never re-tagged
#             or archived here (a row of unknown provenance is the owner's); the self-check
#             names it (Round 3 reflection R3-06: a brain that lost the tag rows of 30 seed
#             memories was offered them as '30 new entries', and a load inserted 30 duplicates)
#
# A revision is sha256(summary NUL content)[:12] of the words the pack wrote, recorded on
# the row as `seed-rev:<12 hex>` (the pack's words, so an owner's later edit of the row does
# not make it "changed"). A memory the owner or the brain ARCHIVED is still "present": a
# pruned seed memory is never resurrected. A pack whose entries carry no anchors (older
# than 1.5.0) keeps the old all-or-nothing guard. --force keeps its old meaning (insert
# every entry again); --plan reports the delta and writes nothing.
SEED_ANCHOR_PREFIX = "anchor:seed_"
SEED_REV_PREFIX = "seed-rev:"
SEED_SUPERSEDED_TAG = "seed-superseded"


def seed_revision(summary: str, content: str) -> str:
    """The revision of one seed entry: the first 12 hex of sha256(summary NUL content)."""
    import hashlib
    return hashlib.sha256(((summary or "") + "\x00" + (content or "")).encode("utf-8")).hexdigest()[:12]


def _entry_summary(m: dict) -> str:
    content = m.get("content", "")
    return m.get("summary") or (content[:100] + ("..." if len(content) > 100 else ""))


def _entry_anchor(m: dict):
    anchors = [t.strip() for t in (m.get("tags") or "").split(",") if t.strip().startswith(SEED_ANCHOR_PREFIX)]
    return anchors


def seed_plan(conn: sqlite3.Connection, mems: list, common_tag: str) -> dict:
    """The delta, computed read-only: which entries are present / adopted / changed / new / left.

    Returns {"actions": [(action, entry_index, anchor, row_id_or_None)], "invalid": [...], "counts": {...}}."""
    # every row that carries the pack's common tag, archived or not, EXCEPT rows this loader superseded
    rows = {}
    for mid, summ, content, archived in conn.execute(
            "SELECT id, summary, content, is_archived FROM memories WHERE id IN "
            "(SELECT memory_id FROM memory_tags WHERE tag = ?) AND id NOT IN "
            "(SELECT memory_id FROM memory_tags WHERE tag = ?) ORDER BY id",
            (common_tag, SEED_SUPERSEDED_TAG)).fetchall():
        rows[mid] = {"id": mid, "fp": seed_revision(summ, content), "archived": archived,
                     "anchors": set(), "revs": set()}
    if rows:
        q = ",".join("?" for _ in rows)
        for mid, tag in conn.execute(f"SELECT memory_id, tag FROM memory_tags WHERE memory_id IN ({q})",
                                     list(rows)).fetchall():
            if tag.startswith(SEED_ANCHOR_PREFIX):
                rows[mid]["anchors"].add(tag)
            elif tag.startswith(SEED_REV_PREFIX):
                rows[mid]["revs"].add(tag[len(SEED_REV_PREFIX):])
    by_anchor = {}
    for r in rows.values():
        for a in r["anchors"]:
            by_anchor.setdefault(a, []).append(r)
    # R3-06: the memories the delta above cannot see -- no common tag, and not this loader's own superseded archive. A live
    # row is preferred to an archived one when two hold the same words.
    others_fp, others_anchor = {}, {}
    for mid, summ, content in conn.execute(
            "SELECT id, summary, content FROM memories WHERE id NOT IN "
            "(SELECT memory_id FROM memory_tags WHERE tag IN (?, ?)) ORDER BY COALESCE(is_archived, 0), id",
            (common_tag, SEED_SUPERSEDED_TAG)).fetchall():
        others_fp.setdefault(seed_revision(summ, content), mid)
    for mid, tag in conn.execute(
            "SELECT memory_id, tag FROM memory_tags WHERE memory_id NOT IN "
            "(SELECT memory_id FROM memory_tags WHERE tag IN (?, ?)) ORDER BY memory_id",
            (common_tag, SEED_SUPERSEDED_TAG)).fetchall():
        if tag.startswith(SEED_ANCHOR_PREFIX):
            others_anchor.setdefault(tag, mid)
    unanchored = [r for r in rows.values() if not r["anchors"]]
    claimed = set()              # an un-anchored legacy row is adopted or superseded by ONE entry only
    actions, invalid = [], []
    seen_anchors = set()
    for i, m in enumerate(mems):
        anchors = _entry_anchor(m)
        if (m.get("category") not in VALID_CATEGORIES or m.get("kind", "episodic") not in VALID_KINDS
                or len(anchors) != 1 or anchors[0] in seen_anchors):
            invalid.append(i)
            continue
        anchor = anchors[0]
        seen_anchors.add(anchor)
        rev = seed_revision(_entry_summary(m), m.get("content", ""))
        older = set(m.get("supersedes") or [])
        mine = by_anchor.get(anchor, [])
        if mine:
            r = mine[0]
            if rev in r["revs"]:
                actions.append(("present", i, anchor, r["id"]))
            elif r["fp"] == rev:
                actions.append(("adopted", i, anchor, r["id"]))       # the same words; record the revision
            elif rev in others_fp and not r["archived"]:
                actions.append(("held-untagged", i, anchor, others_fp[rev]))   # R3-06: inserting would duplicate it
            elif (r["revs"] or {r["fp"]}) & older or not r["revs"]:
                # an older revision this entry names -- or a row that carries the anchor but was never written by a
                # pack load (no seed-rev tag: a hand-stored draft of this entry, from before the pack held it). Either
                # way it is superseded; one the owner or the brain ARCHIVED stays archived: never resurrected.
                actions.append(("changed" if not r["archived"] else "left", i, anchor, r["id"]))
            else:
                actions.append(("left", i, anchor, r["id"]))           # a revision a NEWER pack wrote: never downgraded
            continue
        hit = next((r for r in unanchored if r["id"] not in claimed and r["fp"] == rev), None)
        if hit is not None:
            claimed.add(hit["id"])
            actions.append(("adopted", i, anchor, hit["id"]))
            continue
        old = next((r for r in unanchored if r["id"] not in claimed and r["fp"] in older), None)
        if old is not None and not old["archived"] and rev in others_fp:
            claimed.add(old["id"])
            actions.append(("held-untagged", i, anchor, others_fp[rev]))       # R3-06: inserting would duplicate it
            continue
        if old is not None:
            claimed.add(old["id"])
            # an older revision the owner or the brain archived stays archived: the entry is not resurrected
            actions.append(("changed" if not old["archived"] else "left", i, anchor, old["id"]))
            continue
        held = (others_fp.get(rev) or next((others_fp[o] for o in sorted(older) if o in others_fp), None)
                or others_anchor.get(anchor))
        if held is not None:
            actions.append(("held-untagged", i, anchor, held))   # R3-06: the brain holds it already, without the tags
            continue
        actions.append(("new", i, anchor, None))
    counts = {}
    for a in actions:
        counts[a[0]] = counts.get(a[0], 0) + 1
    return {"actions": actions, "invalid": invalid, "counts": counts}


def _insert_seed_entry(conn: sqlite3.Connection, m: dict, common_tag: str, extra_tags=()) -> int:
    content = m.get("content", "")
    tags = m.get("tags", "") or ""
    # ensure the common tag is present so idempotency + pruning work
    if common_tag not in [t.strip() for t in tags.split(",")]:
        tags = (tags + "," + common_tag).strip(",") if tags else common_tag
    for t in extra_tags:
        tags = tags + "," + t
    return _insert_memory(
        conn, content=content, summary=_entry_summary(m), category=m.get("category"),
        kind=m.get("kind", "episodic"), importance=float(m.get("importance", 0.7)),
        project=m.get("source_project", "seed_pack"),
        context=m.get("source_context", "agnostic seed pack — how the memory system works"),
        tags=tags,
    )


def cmd_load_seed(args: argparse.Namespace) -> int:
    seed_path = Path(args.file)
    if not seed_path.exists():
        sys.stderr.write(f"ERROR: seed pack not found at {seed_path}\n")
        return 2
    try:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: seed pack is not valid JSON: {e}\n")
        return 2
    mems = data.get("memories", [])
    if not isinstance(mems, list) or not mems:
        sys.stderr.write("ERROR: seed pack has no 'memories' array.\n")
        return 2
    common_tag = data.get("common_tag", "seed-pack-agnostic")
    anchored = all(len(_entry_anchor(m)) == 1 for m in mems)
    plan_only = bool(getattr(args, "plan", False))

    if anchored and not args.force:
        conn = _connect(readonly=plan_only)
        plan = seed_plan(conn, mems, common_tag)
        written = {"new": [], "changed": [], "adopted": [], "superseded_ids": []}
        if not plan_only:
            for action, i, anchor, row_id in plan["actions"]:
                m = mems[i]
                rev_tag = SEED_REV_PREFIX + seed_revision(_entry_summary(m), m.get("content", ""))
                if action == "new":
                    _insert_seed_entry(conn, m, common_tag, (rev_tag,))
                    written["new"].append(anchor)
                elif action == "adopted":
                    for t in (anchor, rev_tag):
                        conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                                     (row_id, t))
                    written["adopted"].append(anchor)
                elif action == "changed":
                    conn.execute("UPDATE memories SET is_archived = 1, updated_at = ? WHERE id = ?",
                                 (_now(), row_id))
                    conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                                 (row_id, SEED_SUPERSEDED_TAG))
                    _insert_seed_entry(conn, m, common_tag, (rev_tag,))
                    written["changed"].append(anchor)
                    written["superseded_ids"].append(row_id)
            conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
        conn.close()
        by = {k: [a[2] for a in plan["actions"] if a[0] == k]
              for k in ("new", "changed", "adopted", "left", "held-untagged")}
        print(json.dumps({
            "status": "ok", "action": "load-seed", "mode": "plan" if plan_only else "anchor-delta",
            "seed_pack_version": data.get("seed_pack_version"),
            "entries": len(mems), "skipped_invalid": len(plan["invalid"]),
            "loaded": 0 if plan_only else len(written["new"]) + len(written["changed"]),
            "new": by["new"], "changed": by["changed"], "adopted": len(by["adopted"]),
            "present": plan["counts"].get("present", 0), "left_as_found": by["left"],
            "held_untagged": by["held-untagged"],
            "held_untagged_ids": [a[3] for a in plan["actions"] if a[0] == "held-untagged"],
            "superseded_ids": written["superseded_ids"],
            "total_memories_now": total,
            "note": ("Nothing was written (--plan)." if plan_only else
                     "A per-memory anchor delta: only new and changed entries were inserted; a changed entry's "
                     "older row is archived (never deleted) and tagged seed-superseded. Loading again adds zero. "
                     "Seeded memories are FTS-indexed (keyword recall works immediately); Tier 1 (full CLI) can "
                     "re-embed them for semantic recall.")
                    + ((" %d entr%s NOT added: a memory here already holds %s without the pack's tags (memory #%s) -- "
                        "adding %s again would duplicate %s. If memory_tags lost rows, restore the brain from its "
                        "pre-upgrade copy; `self_check.py` names them." % (
                            len(by["held-untagged"]), "y was" if len(by["held-untagged"]) == 1 else "ies were",
                            "its words" if len(by["held-untagged"]) == 1 else "their words",
                            ", #".join(str(a[3]) for a in plan["actions"] if a[0] == "held-untagged"),
                            "it" if len(by["held-untagged"]) == 1 else "them",
                            "it" if len(by["held-untagged"]) == 1 else "them"))
                       if by["held-untagged"] else ""),
        }))
        return 0

    if plan_only:
        sys.stderr.write("ERROR: --plan needs a pack whose every entry carries one anchor:seed_ tag "
                         "(seed pack 1.5.0 or later); this pack is all-or-nothing.\n")
        return 2

    conn = _connect(readonly=False)
    # An anchor-less pack (older than 1.5.0): the all-or-nothing idempotency guard. We tag every
    # seeded memory with the pack's common tag and refuse to re-load if those tags already exist,
    # unless --force is given.
    existing = conn.execute(
        "SELECT COUNT(*) FROM memory_tags WHERE tag = ?", (common_tag,)
    ).fetchone()[0]
    if existing and not args.force:
        conn.close()
        print(json.dumps({
            "status": "skipped", "action": "load-seed",
            "reason": f"{existing} memories already tagged '{common_tag}' — "
                      "seed pack appears loaded. Use --force to load again.",
        }))
        return 0

    loaded = 0
    skipped = 0
    for m in mems:
        category = m.get("category")
        kind = m.get("kind", "episodic")
        if category not in VALID_CATEGORIES or kind not in VALID_KINDS:
            skipped += 1
            continue
        _insert_seed_entry(conn, m, common_tag)
        loaded += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    conn.close()
    print(json.dumps({
        "status": "ok", "action": "load-seed", "mode": "all" + ("-forced" if args.force else ""),
        "loaded": loaded, "skipped_invalid": skipped,
        "seed_pack_version": data.get("seed_pack_version"),
        "total_memories_now": total,
        "note": "Seeded memories are FTS-indexed (keyword recall works immediately). "
                "Tier 1 (full CLI) can re-embed them for semantic recall.",
    }))
    return 0


# ---------------------------------------------------------------------------
# stats — DB overview
# ---------------------------------------------------------------------------
def cmd_stats(_args: argparse.Namespace) -> int:
    conn = _connect(readonly=True)
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    by_cat = dict(c.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_archived = 0 GROUP BY category"
    ).fetchall())
    by_kind = {}
    if _has_columns(conn, "memories", {"kind"}):
        by_kind = dict(c.execute(
            "SELECT kind, COUNT(*) FROM memories WHERE is_archived = 0 GROUP BY kind"
        ).fetchall())
    # Embedding coverage: how many memories have a Tier-1 vector?
    embedded = None
    try:
        embedded = c.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        embedded = None  # vec0 table absent on a bare/Tier-0 DB
    latest = c.execute(
        "SELECT id, summary, created_at FROM memories WHERE is_archived = 0 "
        "ORDER BY id DESC LIMIT 5"
    ).fetchall()
    out = {
        "status": "ok", "action": "stats", "mode": "lite",
        "total_memories": total,
        "by_category": by_cat,
        "by_kind": by_kind,
        "embedded_memories": embedded,
        "semantic_tier": "active" if embedded else "not present (Tier 0 / keyword-only)",
        "latest_5": [{"id": r[0], "summary": (r[1] or "")[:100], "created_at": r[2]}
                     for r in latest],
        "db_path": str(DB_PATH),
        "db_size_bytes": DB_PATH.stat().st_size,
    }
    print(json.dumps(out, default=str))
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# prime — persona-neutral session-start preamble (text); context — below (JSON)
# ---------------------------------------------------------------------------
def cmd_prime(_args: argparse.Namespace) -> int:
    conn = _connect(readonly=True)
    c = conn.cursor()
    _synth = (" AND COALESCE(derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')"
              if "derived_from" in _has_columns(conn, "memories", ("derived_from",)) else "")
    rows = c.execute(
        "SELECT id, summary FROM memories "
        "WHERE is_archived = 0 AND summary IS NOT NULL" + _synth + " "
        "ORDER BY importance DESC, created_at DESC LIMIT 8"
    ).fetchall()
    total = c.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    conn.close()
    self_name = Path(__file__).name
    lines = [
        "=== Voidloom MEMORY — SESSION PRIME (lite / keyword tier) ===",
        "You have a persistent memory brain. Recall before deciding; store liberally.",
        "",
        "MEMORY (lite — FTS5 keyword recall; no semantic search in this tier):",
        f"  Brain DB:  {DB_PATH}  ({total} memories)",
        f"  Recall:    python3 {self_name} recall --query \"...\" --top_k 10",
        f"  List:      python3 {self_name} list --category X --limit 10",
        f"  Store:     python3 {self_name} store --content \"...\" --category ... --kind ... --tags ...",
        f"  Stats:     python3 {self_name} stats",
        "",
        "Priming (highest-importance memories):",
    ]
    for mid, summ in rows:
        s = (summ or "").replace("\n", " ")[:160]
        lines.append(f"  #{mid}: {s}")
    lines.append("")
    lines.append("If the full (Tier 1) CLI + venv are installed, prefer "
                 "`python3 claude_memory.py recall ...` for SEMANTIC recall (match by meaning).")
    print("\n".join(lines))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """The full CLI's `context` verb, at Tier 0: the SAME JSON envelope, read-only.

    Reference-host round, R4. This file registered a `context` subparser ("alias of
    prime") and defined only `cmd_prime`, and the voidloom facade resolves an engine
    verb as `getattr(engine, "cmd_<verb>")` -- so on a `--tier0-only` brain `voidloom
    context` answered "engine has no cmd_context". Binding the name to cmd_prime would
    not have been enough: prime prints a TEXT preamble, the facade parses the engine's
    last JSON line, and a text answer is "produced no JSON" -- measured, not assumed.

    So this mirrors `claude_memory.cmd_context`'s envelope key for key (status, action,
    project, memories_loaded, total_memories, categories, results[id, content, summary,
    category, importance, source_project, access_count, tags], time_ms) and names its
    mode. One deliberate difference, stated rather than hidden: it is READ-ONLY -- the
    full engine bumps access_count on the rows it returns; the lite engine opens every
    connection with mode=ro, as its recall does, and does not.
    """
    import time as _time
    t0 = _time.time()
    conn = _connect(readonly=True)
    c = conn.cursor()
    limit = getattr(args, "limit", None) or 15
    project = getattr(args, "project", None)
    clauses = ["is_archived = 0"]
    params: list = []
    # R2-06 (Round 3; G2's patch for G1): memories ONLY -- the rows recall keeps out of `results` (dreams, playbook
    # candidates; the same two origins as _SYNTH_CLAUSE) are kept out of `context` too, as voidloom/README.md's
    # Tier 0 line says. A dream is stored at importance 0.55 and outranked every memory at the 0.5 default here.
    if "derived_from" in _has_columns(conn, "memories", {"derived_from"}):
        clauses.append("COALESCE(derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')")
    if project:
        clauses.append("(source_project = ? OR source_project LIKE ? OR source_project IS NULL)")
        params.extend([project, f"%{project}%"])
    has = _has_columns(conn, "memories", {"last_accessed", "access_count", "source_project"})
    order = "importance DESC" + (", last_accessed DESC" if "last_accessed" in has else "")
    rows = c.execute(
        "SELECT id, content, summary, category, importance, "
        + ("source_project" if "source_project" in has else "NULL") + ", "
        + ("access_count" if "access_count" in has else "0")
        + f" FROM memories WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?",
        params + [limit]).fetchall()
    results = []
    for r in rows:
        tags = [t[0] for t in c.execute("SELECT tag FROM memory_tags WHERE memory_id = ?", (r[0],)).fetchall()]
        results.append({"id": r[0], "content": r[1], "summary": r[2], "category": r[3],
                        "importance": r[4], "source_project": r[5], "access_count": r[6], "tags": tags})
    total = c.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    categories = {row[0]: row[1] for row in c.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_archived = 0 GROUP BY category").fetchall()}
    conn.close()
    print(json.dumps({
        "status": "ok", "action": "context", "mode": "fts5-keyword-tier0-readonly",
        "project": project, "memories_loaded": len(results), "total_memories": total,
        "categories": categories, "results": results,
        "time_ms": int((_time.time() - t0) * 1000),
    }, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="claude_memory_lite",
        description="Voidloom brain LITE CLI — stdlib-only, FTS5 keyword recall (Tier 0).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("recall", help="FTS5 keyword recall")
    r.add_argument("--query", "-q", required=True)
    r.add_argument("--top_k", "-k", type=int, default=10)
    r.set_defaults(func=cmd_recall)

    l = sub.add_parser("list", help="List memories with filters")
    l.add_argument("--category")
    l.add_argument("--tag")
    l.add_argument("--kind")
    l.add_argument("--limit", type=int, default=20)
    l.add_argument("--by", choices=["recency", "importance"], default="recency")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("store", help="Store a memory (FTS-indexed; embedding deferred to Tier 1)")
    s.add_argument("--content", "-c", required=True)
    s.add_argument("--summary", "-s")
    s.add_argument("--category", required=True)
    s.add_argument("--kind", default="episodic")
    s.add_argument("--tags", "-t", default="")
    s.add_argument("--importance", "-i", type=float, default=0.7)
    s.add_argument("--project", "-p", default="default")
    s.add_argument("--context")
    s.set_defaults(func=cmd_store)

    ls = sub.add_parser("load-seed", help="Load an agnostic seed pack JSON: a per-memory anchor delta "
                                          "(only new and changed entries land; a second load adds zero)")
    ls.add_argument("--file", "-f", required=True, help="path to seed_pack.json")
    ls.add_argument("--force", action="store_true",
                    help="insert every entry again, whatever the brain already holds (the old meaning)")
    ls.add_argument("--plan", action="store_true",
                    help="report the delta (new / changed / adopted / present) and write nothing")
    ls.set_defaults(func=cmd_load_seed)

    st = sub.add_parser("stats", help="DB overview")
    st.set_defaults(func=cmd_stats)

    pr = sub.add_parser("prime", help="Session-start preamble (persona-neutral)")
    pr.set_defaults(func=cmd_prime)

    ctx = sub.add_parser("context", help="The full CLI's context envelope (JSON), read-only at Tier 0")
    ctx.add_argument("--project", default=None, help="scope to a source_project (as the full CLI)")
    ctx.add_argument("--limit", type=int, default=15, help="rows to return (as the full CLI)")
    ctx.set_defaults(func=cmd_context)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

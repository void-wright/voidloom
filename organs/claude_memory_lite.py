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
  load-seed  — load an agnostic seed pack JSON: a per-memory anchor delta (install + upgrade); every
               inserted row through memory_write's guarded writer, flagged seed=1 + origin, the
               pack scrubbed by seed/seed_denylist.json first (organs/seed_loader.py)
  seed-scrub / seed-self-test / seed-delete-one / seed-shape — the seed's own checks

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
import re
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


# FTS5 query building -- the SAME rules as hybrid_retrieval._sanitize_fts_query and
# hybrid_retrieval._fts_match_expr, kept stdlib-only here so this file never needs
# the heavy stack. voidloom/tests/test_fts_any_word.py asserts the copies agree.
#
# Safety: FTS5 reads a bare hyphen as NEGATION, a dot or '?' or an apostrophe as a
# syntax error, and uppercase AND/OR/NOT/NEAR as operators. So: keep only word
# characters, hyphens and dots; quote any token with a hyphen or dot as a phrase;
# lowercase the operator words.
#
# Matching: OR over the content words, ranked by BM25 (any word can match; a
# memory scores by how many rare query words it holds). Joining with spaces made
# FTS5 require EVERY word, "when" and "did" included, and with '?' or an
# apostrophe in the query the old sanitiser raised a syntax error -- so a natural
# question found nothing (measured 2026-10-27).
FTS_STOPWORDS = frozenset("""
a an the of to in on at for and or but is are was were be been do does did what when where who whom which why
how that this these those with from by as it its into about i you he she they we his her their our my your has
have had will would can could should
s t d ll m re ve
""".split())

# An OR costs one posting-list scan per term, so a pasted page would cost seconds
# (measured on a 31k-memory brain: 1,000 words 2.3 s; one common word repeated 1,000
# times 85 s). Queries are questions, not documents: the first 32 content words
# carry them. Logged live questions are far shorter (p99 17 words; 1 of 17,597 had
# 40 content words, so its last 8 were dropped).
FTS_MAX_TERMS = 32


def _sanitize_fts_query(query: str) -> str:
    cleaned = re.sub(r'["]', " ", query or "")
    cleaned = re.sub(r"[^\w\s.\-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    tokens = []
    for tok in cleaned.split():
        if not re.search(r"\w", tok):
            continue
        if tok.upper() in {"AND", "OR", "NOT", "NEAR"}:
            tokens.append(tok.lower())
        elif "-" in tok or "." in tok:
            stripped = tok.strip(".-")
            if stripped:
                tokens.append(f'"{stripped}"')
        else:
            tokens.append(tok)
    return " ".join(tokens)


def _fts_match_expr(query: str) -> str:
    tokens = _sanitize_fts_query(query).split()
    content = [t for t in tokens if t.startswith('"') or t.lower() not in FTS_STOPWORDS]
    return " OR ".join((content or tokens)[:FTS_MAX_TERMS])


def _has_columns(conn: sqlite3.Connection, table: str, cols) -> set:
    present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return {c for c in cols if c in present}


# ---------------------------------------------------------------------------
# recall — FTS5 keyword search
# ---------------------------------------------------------------------------
_SYNTH_CLAUSE = "\n      AND COALESCE(m.derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')"
_UNRESOLVED_STAMP = "unresolved"


def _lock_was_written(data_dir: str) -> bool:
    """The rule is embedder_lock.lost_lock's. A bare copy of this file cannot ask it, and then any
    history beside the database counts: reading a locked brain as lock-less is the worse mistake."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import embedder_lock  # type: ignore
    except Exception:
        return os.path.exists(os.path.join(data_dir, "embedder.lock.history.jsonl"))
    return embedder_lock.lost_lock(data_dir) is not None


def _read_lock():
    """The install lock beside the database file (symlinks followed, the rule the reader applies), read
    with the standard library alone so a bare copy of this file can still read it. (lock, None), or
    (None, why) when it cannot be read."""
    path = os.path.join(os.path.dirname(os.path.realpath(str(DB_PATH))), "embedder.lock.json")
    try:
        with open(path, encoding="utf-8") as f:
            lk = json.load(f)
        return {"vec_table": str(lk["vec_table"]), "weights_hash": str(lk["weights_hash"]),
                "dim": int(lk["dim"])}, None
    except FileNotFoundError:
        if _lock_was_written(os.path.dirname(path)):
            return None, "LOCK_MISSING: this brain was locked and the file is gone"
        return None, "LOCK_MISSING"
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, f"LOCK_MALFORMED ({type(e).__name__})"


def _vector_ids_sql(conn: sqlite3.Connection, table: str):
    """A subquery naming the memories with a vector in `table`, readable on a stdlib connection: sqlite-vec
    keeps one row per vector in the ordinary shadow table `<table>_rowids`. None when it cannot be read."""
    if table not in ("memory_embeddings", "memory_embeddings_v2", "memory_embeddings_v3"):
        return None
    try:
        conn.execute(f"SELECT COUNT(*) FROM {table}_rowids")
    except sqlite3.Error:
        return None
    return f"SELECT rowid FROM {table}_rowids"


def _lock_coverage(conn: sqlite3.Connection) -> dict:
    """The degraded answer carries coverage too: `matched` of `total`, derived on this connection for this
    call. Tier 0 loads no model and opens no vec0 table, so it counts by SQL, over the same population the
    reader's search set is: active memories with a vector in the lock's table whose stamp does not name
    another checksum. `verified` carries the lock's checksum; `legacy_unverified` is the rest. With no lock
    the whole table is the set, as for the reader. Nothing to measure against: matched 0 by construction,
    and `basis` says why. `measured` is True only when both the vectors and the stamps were read."""
    has_arch = "is_archived" in _has_columns(conn, "memories", ("is_archived",))
    active = "COALESCE(m.is_archived, 0) = 0" if has_arch else "1"
    total = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE {active}").fetchone()[0]
    out = {"matched": 0, "total": int(total), "value": 0.0, "basis": None, "measured": False,
           "mode": "fts5-keyword: the keyword result set; matched counts rows a vector search could reach"}

    def settle(matched: int, basis: str, **extra) -> dict:
        out.update(extra)
        out.update({"matched": int(matched), "value": (matched / total) if total else 0.0, "basis": basis})
        out["coverage"] = {"matched": out["matched"], "total": out["total"], "value": out["value"]}
        return out

    lock, why = _read_lock()
    if lock is None and why != "LOCK_MISSING":
        return settle(0, f"lock unreadable beside the database ({why}); matched 0 by construction")
    if lock is None:
        er = _embed_resolver()
        try:
            table = er.resolve(conn)[0] if er is not None else None
        except Exception:
            table = None
        if table is None:
            return settle(0, f"lock unreadable beside the database ({why}) and no tier declared; "
                             "matched 0 by construction")
        ids = _vector_ids_sql(conn, table)
        if ids is None:
            return settle(0, f"no lock, and the vectors of {table} cannot be read here; matched 0 by "
                             "construction", vec_table=table)
        matched = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE {active} AND m.id IN ({ids})").fetchone()[0]
        return settle(matched, "no lock: the whole vector table is the search set", measured=True,
                      vec_table=table, search_rule="lockless: whole table", legacy_unverified=None)

    vt, wh, dim = lock["vec_table"], lock["weights_hash"], lock["dim"]
    named = {"lock": {"vec_table": vt, "weights_hash": wh}, "vec_table": vt}
    if len(_has_columns(conn, "embedding_provenance", {"weights_hash", "vec_table", "memory_id", "dim"})) < 4:
        return settle(0, "embedding_provenance absent or without weights_hash; matched 0 by construction", **named)
    stamped = ("SELECT memory_id FROM embedding_provenance WHERE vec_table = ? AND weights_hash = ? AND dim = ?")
    foreign = ("SELECT memory_id FROM embedding_provenance WHERE vec_table = ? "
               "AND NOT ((weights_hash = ? AND dim = ?) OR weights_hash = ?)")
    ids = _vector_ids_sql(conn, vt)
    if ids is None:         # no shadow table to read: the stamps alone, and the figure says so
        verified = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE {active} AND m.id IN ({stamped})",
                                (vt, wh, dim)).fetchone()[0]
        return settle(verified, "embedding_provenance rows carrying the lock's checksum; the vector table "
                                "cannot be read on this connection",
                      verified=int(verified), legacy_unverified=None, **named)
    verified = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE {active} AND m.id IN ({ids}) "
                            f"AND m.id IN ({stamped})", (vt, wh, dim)).fetchone()[0]
    matched = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE {active} AND m.id IN ({ids}) "
                           f"AND m.id NOT IN ({foreign})", (vt, wh, dim, _UNRESOLVED_STAMP)).fetchone()[0]
    return settle(matched, "active memories with a vector in the lock's table and no stamp naming another "
                           "checksum (derived on this connection)", measured=True,
                  search_rule="lock: verified + legacy", verified=int(verified), legacy_unverified=int(matched) - int(verified), **named)


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
    # Any content word can match (OR), ranked by BM25 -- see _fts_match_expr above.
    sanitized = _fts_match_expr(args.query)
    rows = []  # a query with nothing searchable (only punctuation) makes no FTS5 call and no phrase fallback
    try:
        if sanitized:
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
    coverage = _lock_coverage(conn)
    for r in results:
        r["coverage"] = coverage
    out = {
        "status": "ok", "action": "recall", "mode": "fts5-keyword",
        "query": args.query, "result_count": len(results), "results": results,
        "coverage": coverage,
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
    deleted = set()                     # entries the owner hard-deleted (memory_write.tombstone_seed)
    try:
        deleted = {r[0] for r in conn.execute("SELECT value FROM metadata WHERE key LIKE 'seed\\_tombstone:%' ESCAPE '\\'")}
    except sqlite3.OperationalError:
        pass
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
        if anchor in deleted:
            actions.append(("left", i, anchor, None))            # the owner deleted this entry: it stays deleted
            continue
        actions.append(("new", i, anchor, None))
    counts = {}
    for a in actions:
        counts[a[0]] = counts.get(a[0], 0) + 1
    return {"actions": actions, "invalid": invalid, "counts": counts}


def _embed_resolver():
    """organs/embed_resolver.py (stdlib-only; it imports only embedder_lock): the one lock-first answer to
    "which vector table". Imported lazily from beside this file; None when it is not laid there."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import embed_resolver  # type: ignore
        return embed_resolver
    except Exception:
        return None


def _seed_lib():
    """organs/seed_loader.py, the library behind this verb: HOW a seed row is written (the one guarded writer,
    memory_write.write_memory; the seed/origin COLUMNS of migration_044; the denylist scrub). Imported lazily from
    beside this file; None when it is not laid there (a bare copy of lite alone keeps the direct INSERT)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import seed_loader  # type: ignore
        return seed_loader
    except Exception:
        return None


def _seed_emit(doc: dict, rc: int) -> int:
    print(json.dumps(doc, default=str))
    return rc


def _insert_seed_entry(conn: sqlite3.Connection, m: dict, common_tag: str, extra_tags=(),
                       version: str = "", embed: bool = False) -> int:
    content = m.get("content", "")
    tags = m.get("tags", "") or ""
    # ensure the common tag is present so idempotency + pruning work
    if common_tag not in [t.strip() for t in tags.split(",")]:
        tags = (tags + "," + common_tag).strip(",") if tags else common_tag
    for t in extra_tags:
        tags = tags + "," + t
    lib = _seed_lib()
    if lib is not None:
        return lib.write_seed_row(
            conn, content=content, summary=_entry_summary(m), category=m.get("category"),
            kind=m.get("kind", "episodic"), importance=float(m.get("importance", 0.7)),
            source_project=m.get("source_project", "seed_pack"),
            source_context=m.get("source_context", "agnostic seed pack — how the memory system works"),
            tags=[t.strip() for t in tags.split(",")], version=version, embed=embed)["memory_id"]
    # fallback (seed_loader.py not laid beside this file): the direct INSERT, flagged when the columns exist
    mid = _insert_memory(
        conn, content=content, summary=_entry_summary(m), category=m.get("category"),
        kind=m.get("kind", "episodic"), importance=float(m.get("importance", 0.7)),
        project=m.get("source_project", "seed_pack"),
        context=m.get("source_context", "agnostic seed pack — how the memory system works"),
        tags=tags,
    )
    if version and {"seed", "origin"} <= _has_columns(conn, "memories", {"seed", "origin"}):
        import hashlib
        conn.execute("UPDATE memories SET seed = 1, origin = ? WHERE id = ?",
                     (f"seed_pack:{version}#{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}", mid))
    return mid


def _seed_preflight(seed_path: Path, args: argparse.Namespace):
    """Before the load opens its transaction: the denylist over the whole pack (any hit refuses the load,
    rc 4, nothing written). Returns (lib, scrub envelope or None, error rc or None)."""
    lib = _seed_lib()
    if lib is None:
        if getattr(args, "embed", False):
            return lib, None, _seed_emit({"status": "error", "error": "SEED_LIBRARY_ABSENT", "action": "load-seed",
                                          "message": "--embed needs organs/seed_loader.py beside this file"}, 2)
        return lib, None, None
    try:
        return lib, lib.refuse_on_hits(seed_path), None
    except lib.SeedLoadError as e:
        return lib, None, _seed_emit({**e.as_dict(), "action": "load-seed"},
                                     4 if e.code == "SEED_DENYLIST_HIT" else 2)


def _seed_write_failed(conn: sqlite3.Connection, e: Exception) -> int:
    """A row the guarded writer refused (under --embed: no embedder, no vector): roll back the WHOLE load."""
    conn.rollback()
    conn.close()
    return _seed_emit({"status": "error", "error": getattr(e, "code", None) or "SEED_EMBED_REFUSED",
                       "action": "load-seed", "message": f"{type(e).__name__}: {str(e)[:300]}",
                       "note": "nothing was written: the load is one transaction, and the brain is as it was"}, 3)


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
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 0:
        sys.stderr.write("ERROR: --limit must be 0 or more.\n")
        return 2

    version = str(data.get("seed_pack_version") or "")
    embed = bool(getattr(args, "embed", False))
    lib, scrubbed = None, None
    if not plan_only:
        lib, scrubbed, err = _seed_preflight(seed_path, args)
        if err is not None:
            return err

    if anchored and not args.force:
        conn = _connect(readonly=plan_only)
        plan = seed_plan(conn, mems, common_tag)
        written = {"new": [], "changed": [], "adopted": [], "superseded_ids": [], "columns_set": 0}
        deferred = []                                   # entries --limit left for a later load
        if not plan_only:
            try:
                if embed:
                    lib.enable_vec(conn)
                for action, i, anchor, row_id in plan["actions"]:
                    m = mems[i]
                    if (action in ("new", "changed") and limit is not None
                            and len(written["new"]) + len(written["changed"]) >= limit):
                        deferred.append(anchor)
                        continue
                    rev_tag = SEED_REV_PREFIX + seed_revision(_entry_summary(m), m.get("content", ""))
                    if action == "new":
                        _insert_seed_entry(conn, m, common_tag, (rev_tag,), version, embed)
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
                        _insert_seed_entry(conn, m, common_tag, (rev_tag,), version, embed)
                        written["changed"].append(anchor)
                        written["superseded_ids"].append(row_id)
                    # a seed row this load does not insert -- adopted, or present but written before
                    # migration_044 -- gets its seed/origin columns; its words and vector are untouched
                    if action in ("adopted", "present") and lib is not None:
                        written["columns_set"] += int(lib.stamp_columns(conn, row_id, version, m.get("content", "")))
            except Exception as e:
                return _seed_write_failed(conn, e)
            conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
        conn.close()
        by = {k: [a[2] for a in plan["actions"] if a[0] == k and a[2] not in deferred]
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
            "limit": limit, "deferred_by_limit": deferred,
            "superseded_ids": written["superseded_ids"],
            "seed_columns_set_on_existing_rows": written["columns_set"],
            "writer": ("memory_write.write_memory via organs/seed_loader.py" if lib is not None
                       else "lite direct INSERT (seed_loader.py not laid beside this file)"),
            "embed": embed, "scrub": scrubbed,
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
        if limit is not None and loaded >= limit:
            break
        category = m.get("category")
        kind = m.get("kind", "episodic")
        if category not in VALID_CATEGORIES or kind not in VALID_KINDS:
            skipped += 1
            continue
        try:
            if embed and loaded == 0:
                lib.enable_vec(conn)
            _insert_seed_entry(conn, m, common_tag, (), version, embed)
        except Exception as e:
            return _seed_write_failed(conn, e)
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


def cmd_seed_scrub(args: argparse.Namespace) -> int:
    lib = _seed_lib()
    if lib is None:
        return _seed_emit({"status": "error", "error": "SEED_LIBRARY_ABSENT", "action": "seed-scrub"}, 2)
    try:
        sc = lib.scrub(Path(args.file), Path(args.denylist))
    except lib.SeedLoadError as e:
        return _seed_emit({**e.as_dict(), "action": "seed-scrub"}, 2)
    return _seed_emit({"status": "ok" if not sc["hits"] else "error", "action": "seed-scrub", **sc},
                      0 if not sc["hits"] else 4)


def cmd_seed_self_test(args: argparse.Namespace) -> int:
    """The doctrine set needs the Tier-1 organs (hybrid_retrieval + the locked embedder); on a Tier-0 host it
    refuses typed rather than pretending a number."""
    lib = _seed_lib()
    if lib is None:
        return _seed_emit({"status": "error", "error": "SEED_LIBRARY_ABSENT", "action": "seed-self-test"}, 2)
    try:
        res = lib.self_test(str(DB_PATH), doctrine=Path(args.doctrine), mode=args.mode)
    except lib.SeedLoadError as e:
        return _seed_emit({**e.as_dict(), "action": "seed-self-test"}, 2)
    except Exception as e:
        code = getattr(e, "code", None) or "SELF_TEST_UNAVAILABLE"
        return _seed_emit({"status": "error", "error": code, "action": "seed-self-test",
                           "message": f"{type(e).__name__}: {e}"}, 3)
    sys.stderr.write(res["line"] + "\n")
    return _seed_emit(res, 0 if res["meets_bar"] else 5)


def cmd_seed_delete_one(args: argparse.Namespace) -> int:
    lib = _seed_lib()
    if lib is None:
        return _seed_emit({"status": "error", "error": "SEED_LIBRARY_ABSENT", "action": "seed-delete-one"}, 2)
    if not DB_PATH.exists():
        return _seed_emit({"status": "error", "error": "DB_NOT_FOUND", "action": "seed-delete-one",
                           "db": str(DB_PATH)}, 2)
    try:
        return _seed_emit(lib.delete_one(str(DB_PATH), args.origin), 0)
    except lib.SeedLoadError as e:
        return _seed_emit({**e.as_dict(), "action": "seed-delete-one"}, 1)


def cmd_seed_shape(_args: argparse.Namespace) -> int:
    lib = _seed_lib()
    if lib is None:
        return _seed_emit({"status": "error", "error": "SEED_LIBRARY_ABSENT", "action": "seed-shape"}, 2)
    conn = _connect(readonly=True)
    try:
        return _seed_emit({"status": "ok", "action": "seed-shape", **lib.seed_shape(conn)}, 0)
    finally:
        conn.close()


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
    # Embedding coverage: how many memories can THIS brain's vector search reach? The table comes from
    # the install lock first (metadata only for a brain with no lock), never a literal: a qwen brain's
    # vectors are not in the nomic table. The figure is the search set's, from the provenance sidecar
    # (_lock_coverage), not presence in the table: an other-checksum or archived row is not embedded
    # for any search here. It reads on this stdlib connection: a vec0 table cannot be asked directly.
    embedded, emb_table, emb_unresolved, coverage, stored = None, None, None, None, None
    er = _embed_resolver()
    if er is None:
        emb_unresolved = "organs/embed_resolver.py is not beside this file"
    else:
        try:
            emb_table, _dim = er.resolve(conn)
            coverage = _lock_coverage(conn)
            stored = er.stored_vector_count(conn, emb_table)
            # no sidecar or no readable table to measure against: what the table holds, and `coverage` says so
            embedded = coverage["matched"] if coverage["measured"] else stored
        except er.EmbedTierUnresolved as e:
            vec_tables = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN "
                                   "('memory_embeddings', 'memory_embeddings_v2', 'memory_embeddings_v3')"
                                   ).fetchone()[0]
            if vec_tables:                          # a tier exists but cannot be named: say so, never "Tier 0"
                emb_unresolved = f"{e.code}: {e}"
            else:
                embedded = 0                        # a bare / Tier-0 brain: no vector table at all
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
        "embedding_table": emb_table,
        "coverage": coverage if coverage is not None else _lock_coverage(conn),
        "semantic_tier": ("active" if embedded else
                          f"unknown: {emb_unresolved}" if emb_unresolved else
                          "present, vector count unreadable on this interpreter" if embedded is None else
                          "present, no vector in the search set" if stored else
                          "not present (Tier 0 / keyword-only)"),
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
    ls.add_argument("--embed", action="store_true",
                    help="write every row with its vector through the locked embedder (Tier 1); "
                         "without it rows are keyword-findable and embed_backfill.py vectors them later")
    ls.add_argument("--limit", type=int, default=None,
                    help="insert at most N rows; a later load takes up where this one stopped")
    ls.set_defaults(func=cmd_load_seed)

    _seed_dir = Path(__file__).resolve().parent.parent / "seed"
    sc = sub.add_parser("seed-scrub", help="the seed denylist over the pack: hits (rc 4 on any)")
    sc.add_argument("--file", "-f", default=str(_seed_dir / "seed_pack.json"))
    sc.add_argument("--denylist", default=str(_seed_dir / "seed_denylist.json"))
    sc.set_defaults(func=cmd_seed_scrub)

    sst = sub.add_parser("seed-self-test", help="the 25-query doctrine set through the shipped recall, per mode")
    sst.add_argument("--doctrine", default=str(_seed_dir / "seed_doctrine_queries.json"))
    sst.add_argument("--mode", default=None, choices=("vector-only", "bm25-only", "no-rerank", "hybrid"),
                     help="restrict to one recall mode (a measurement); default: the set's modes")
    sst.set_defaults(func=cmd_seed_self_test)

    sdo = sub.add_parser("seed-delete-one", help="delete ONE seed row permanently by its origin")
    sdo.add_argument("origin")
    sdo.set_defaults(func=cmd_seed_delete_one)

    ssh = sub.add_parser("seed-shape", help="every seed number on this brain, derived now")
    ssh.set_defaults(func=cmd_seed_shape)

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

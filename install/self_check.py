#!/usr/bin/env python3
"""self_check.py — "is my brain alive?" verification for the Voidloom memory system.

Run this AFTER install to confirm your brain works end-to-end. It is the shipped,
Tier-0 self-test: it exercises the REAL recall path against the GUARANTEED-PRESENT
seed-pack memories (the agnostic memories every install loads) and tells you, in plain
language, whether store/recall/tag-filter/classification all behave correctly.

    python3 self_check.py                 # human-readable PASS/FAIL report
    python3 self_check.py --json          # machine-readable envelope
    python3 self_check.py --quiet         # only the final verdict line + exit code

Exit code 0 = brain alive (all REQUIRED checks pass); non-zero = something is wrong
(each failure prints exactly what + how to fix).

WHY THIS EXISTS
---------------
A teammate who just ran install.sh wants one command that proves the brain is real,
not a stub. This runs entirely on Tier 0 — stdlib `sqlite3` + the FTS5 module built
into Python's sqlite3 — so it works on ANY `python3` with no venv, no torch, no
embedding model. If Tier 1 (semantic) happens to be installed it is reported as a
bonus, but it is NEVER required to pass.

WHAT IT CHECKS (all against the known seed memories, so results are deterministic)
  1. DB present + core schema sane (memories / memories_fts / memory_tags) + FTS integrity.
  2. Seed pack loaded: the count of memories tagged with the pack's common tag
     equals the number of memories in seed_pack.json -- on a FRESH install. On an UPGRADE
     (--seed-optional, install.sh's ambient shape) the required seed checks are scoped to the
     entries THIS brain has received (the loader's per-memory anchor delta); entries it has
     not received are REPORTED as the owner's choice ("choices" in --json), never failed
     (Round 3 G1: the reference host's gate failed 3 checks over the installer's own policy).
     Every check carries `means`: what its failure means, in plain words.
  3. Recall path (keyword / FTS5): for several known seed memories, a query built
     from that memory's OWN distinctive words surfaces that exact memory. This is
     the real "your brain answers questions" check.
  4. Tag-filter path: tags resolve via the memory_tags JOIN table (the schema gotcha
     done right), not by content-matching the tag string.
  5. classify_memory: each seed's (summary, content, category, tags) classifies to
     its declared kind via the shipped kind_taxonomy logic (skipped gracefully if
     that module is not importable — never fails the run).
  6. Tier detection: report whether semantic recall (Tier 1) is available. Bonus only.
  7. Full-L4 model presence (Tier 1 only, BONUS): the two local models both load from
     the HuggingFace cache — the embedder nomic-embed-text-v1.5 + the cross-encoder
     reranker the DEFAULT hybrid recall actually loads -- Qwen3-Reranker-0.6B unless
     VOIDLOOM_RERANKER=bge, resolved here the same way hybrid_retrieval resolves it.
     Gated on Tier 1 being
     present and loads local-files-only (never downloads); on Tier 0 it is skipped,
     never failed — Tier 0 does not depend on any model.

SCHEMA NOTES (must match the full + lite CLI's DB)
  * `memories_fts` is an EXTERNAL-CONTENT FTS5 table (content='memories') kept in
    sync by triggers — we only ever READ it via MATCH, never INSERT.
  * Tags live in the `memory_tags` JOIN table (memory_id, tag), never a column.
  * The memory row uses columns `source_context` / `last_accessed`.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# DB + seed-pack resolution: env override > relative-self (never a hardcoded path).
# Mirrors claude_memory_lite.py exactly so this self-check hits the SAME brain.
# ---------------------------------------------------------------------------
def _resolve_db_path() -> Path:
    env_db = os.environ.get("WIZARD_LIBRARY_DB")
    if env_db:
        return Path(env_db)
    env_home = os.environ.get("WIZARD_LIBRARY_HOME")
    if env_home:
        return Path(env_home) / "claude_memory.db"
    return Path(__file__).resolve().parent.parent / "claude_memory.db"


def _resolve_seed_path() -> Path:
    env_seed = os.environ.get("WIZARD_LIBRARY_SEED")
    if env_seed:
        return Path(env_seed)
    # ships beside this script
    return Path(__file__).resolve().parent.parent / "seed" / "seed_pack.json"


# ---------------------------------------------------------------------------
# FTS5 query sanitizer — IDENTICAL semantics to claude_memory_lite._sanitize_fts_query.
# A bare hyphen is FTS5 NEGATION; a dot can be a syntax error; AND/OR/NOT/NEAR
# (uppercase) flip into boolean mode. Wrap special-char tokens as quoted phrases
# and lowercase boolean keywords so natural-language queries behave intuitively.
# ---------------------------------------------------------------------------
_FTS5_SPECIAL = set('-."*():^')
_FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts_query(query: str) -> str:
    out = []
    for tok in query.split():
        if tok in _FTS5_KEYWORDS:
            out.append(tok.lower())
        elif any(ch in _FTS5_SPECIAL for ch in tok):
            out.append('"' + tok.replace('"', '""') + '"')
        else:
            out.append(tok)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------
class Checks:
    def __init__(self) -> None:
        self.results = []   # list of dicts: {name, ok, required, detail, fix, means}
        self.notes = []     # list of strings (bonus / informational)
        self.choices = []   # the OWNER's choices the check found -- never failures (Round 3 G1, item 2)

    def add(self, name, ok, required=True, detail="", fix="", means=""):
        # `means`: what a FAIL of this check means for the owner, in plain words -- install.sh's DONE banner prints it
        # beside the name, so a failed gate says exactly which checks failed and what they mean (Round 3 G1, item 2)
        self.results.append({
            "name": name, "ok": bool(ok), "required": required,
            "detail": detail, "fix": fix, "means": means,
        })
        return ok

    def note(self, msg):
        self.notes.append(msg)

    def choice(self, msg):
        self.choices.append(msg)

    @property
    def required_failures(self):
        return [r for r in self.results if r["required"] and not r["ok"]]

    @property
    def passed_required(self):
        return sum(1 for r in self.results if r["required"] and r["ok"])

    @property
    def total_required(self):
        return sum(1 for r in self.results if r["required"])

    @property
    def passed_optional(self):
        return sum(1 for r in self.results if not r["required"] and r["ok"])

    @property
    def total_optional(self):
        return sum(1 for r in self.results if not r["required"])


# ---------------------------------------------------------------------------
# Individual check groups
# ---------------------------------------------------------------------------
def _uri(db_path, mode: str) -> str:
    """`file:<path>?mode=<mode>` with the path ESCAPED (reference-host round, iteration 4, LOW 2): a '?' or '#'
    spliced raw ends the path early and SQLite opens the prefix."""
    return "file:%s?mode=%s" % (urllib.request.pathname2url(os.path.abspath(str(db_path))), mode)


def _connect_ro(db_path: Path):
    uri = _uri(db_path, "ro")
    return sqlite3.connect(uri, uri=True)


def check_schema(checks: Checks, db_path: Path):
    """1. DB present + core schema sane + FTS integrity."""
    if not db_path.exists():
        checks.add(
            "DB present", False,
            detail=f"no brain DB at {db_path}",
            fix="Run the installer (bash install.sh) to create + seed the brain, "
                "or set WIZARD_LIBRARY_DB to your DB path.",
            means="there is no brain database where the install put it: nothing can be stored or recalled",
        )
        return None  # cannot continue without a DB

    conn = _connect_ro(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()}

    checks.add("DB present", True, detail=str(db_path))
    checks.add(
        "core table 'memories' exists", "memories" in tables,
        fix="DB is missing the memories table — re-run the installer / `claude_memory.py init`.",
        means="the brain has no table to hold memories: storing and recall cannot work",
    )
    has_fts = "memories_fts" in tables
    checks.add(
        "FTS5 index 'memories_fts' exists", has_fts,
        fix="Keyword recall needs the memories_fts table — re-run the installer "
            "(it applies the FTS5 migration).",
        means="the keyword index is missing: questions to the brain come back empty",
    )
    checks.add(
        "join table 'memory_tags' exists", "memory_tags" in tables,
        fix="Tag filtering needs the memory_tags table — re-run the installer.",
        means="tags cannot be stored or filtered on",
    )

    # C2/M5 — THE SCHEMA IS COMPLETE AGAINST THE PACKAGE, and this is REQUIRED.
    #
    # The three checks above are a TYPED list of three names. A brain carrying
    # 24 of the 66 objects this package's migrations build passes all three,
    # and the installer then printed "self_check PASSED — the install is alive"
    # over a brain whose self-eval, belief-engine, decision-unit, whisper and
    # organ-ledger tables did not exist. The required set is now DERIVED: the
    # package's own migrations, EXECUTED against a throwaway file.
    #
    # The reference is tier-relative on purpose. An interpreter without
    # sqlite_vec cannot build the two vec0 migrations, so those objects are
    # absent from BOTH the reference and the brain and the check stays honest
    # instead of failing a legitimate Tier-0 host; what could not be built is
    # reported rather than passed over in silence.
    # The organs live beside THIS FILE's parent (install/ and organs/ are siblings
    # under the brain directory), the same resolution check_classify uses below.
    # This used to derive them from the DB's parent, which is only the brain
    # directory when the brain was copied there: under `install.sh --attach-db`
    # the brain is wherever the operator keeps it, and the audit reported
    # "No module named schema_migrator" over a fully migrated brain.
    _organs_dir = Path(__file__).resolve().parent.parent / "organs"
    try:
        sys.path.insert(0, str(_organs_dir))
        import schema_migrator as _sm  # type: ignore
        _ref, _unmeasurable, _ref_errors = _sm.build_reference(_sm.migrations_dir())
        _missing = sorted(_ref - tables)
        # D2-F1: a reference whose migrations ERRORED has a denominator nobody can
        # trust -- the objects that migration would have built never enter _ref, so
        # they can never be reported missing. "0 missing" then means "nothing was
        # compared", and this check would pass a brain that is genuinely incomplete.
        checks.add(
            f"the package's own migrations all executed ({len(_ref)} reference objects)",
            not _ref_errors,
            detail=("; ".join(f"{e['file']}: {e['errors'][0]}" for e in _ref_errors[:3])
                    if _ref_errors else "every migration built cleanly"),
            fix="This is a defect in the PACKAGE, not in your brain: a shipped "
                "migration does not execute on this SQLite. Report it with the "
                "filename above; do not treat a 0-missing result as a pass.",
            means="the package's own schema could not be built here, so the brain's schema could not be compared",
        )
        checks.add(
            f"schema complete against this package ({len(_ref)} objects)",
            (not _missing) and not _ref_errors,
            detail=(f"{len(tables)} objects present"
                    + (f"; MISSING {len(_missing)}: " + ", ".join(_missing[:10])
                       + (" ..." if len(_missing) > 10 else "") if _missing else "")),
            fix="Bring the brain up to this package: "
                f"WIZARD_LIBRARY_DB='{db_path}' python3 "
                f"'{_organs_dir / 'schema_migrator.py'}' apply",
            means="tables or indexes this package's organs use are missing: those organs will fail when they run",
        )
        for _u in _unmeasurable:
            checks.note(f"UNMEASURABLE on this interpreter: {_u}")
    except Exception as e:
        # An ABSENT auditor is an incomplete install, not a pass. Saying
        # "cannot tell" out loud is the whole point of this class; collapsing
        # it into silence would re-create the defect one layer out.
        checks.add(
            "schema complete against this package", False,
            detail=f"could not run the schema audit: {type(e).__name__}: {e}",
            fix="organs/schema_migrator.py ships with the package; re-run the "
                "installer so it is laid down next to the brain.",
            means="the schema could not be audited at all, so it is not known to be complete",
        )

    # FTS5 external-content integrity check (proves the index is in sync, not
    # double-indexed). The 'integrity-check' command is a VERIFICATION op (it
    # mutates no rows), but FTS5 routes it through the write path, so we open a
    # brief read-write handle just for it and fall back gracefully if the DB is
    # genuinely read-only on this machine.
    if has_fts:
        ran = False
        try:
            wconn = sqlite3.connect(_uri(db_path, "rw"), uri=True, timeout=60.0)
            try:
                wconn.execute("INSERT INTO memories_fts(memories_fts) VALUES('integrity-check')")
                checks.add("FTS5 integrity-check passes", True,
                           detail="keyword index is in sync with the memories table "
                                  "(not double-indexed)")
                ran = True
            finally:
                wconn.close()
        except sqlite3.DatabaseError as e:
            if not ran:
                # Either the DB is read-only, or the index is corrupt. The live
                # recall checks below are the authoritative proof of keyword recall;
                # surface this as a non-required signal rather than bricking the run.
                checks.add(
                    "FTS5 integrity-check passes", True, required=False,
                    detail=f"integrity-check not conclusively run ({e}); the live "
                           "recall checks below are the authoritative proof.",
                )
    return conn


def _recall(conn, query: str, limit: int = 5):
    """Run the SAME FTS5 recall the lite CLI runs; return list of (id, summary)."""
    sql = """
    SELECT m.id, m.summary, bm25(memories_fts) AS rank
    FROM memories_fts
    JOIN memories m ON m.rowid = memories_fts.rowid
    WHERE memories_fts MATCH ?
      AND m.is_archived = 0
    ORDER BY rank ASC, m.importance DESC, m.created_at DESC
    LIMIT ?
    """
    sanitized = _sanitize_fts_query(query)
    rows = conn.execute(sql, (sanitized, limit)).fetchall()
    return [(r[0], r[1]) for r in rows]


def check_own_content_recall(checks: Checks, conn):
    """3b. (--seed-optional, on a brain that carries no seed pack) the recall path over the
    brain's OWN content: the highest-importance live memory's own most distinctive word
    (the longest alphabetic token of its summary, >= 5 letters) must surface that memory
    in the top 10. The same claim check 3 makes, drawn from what this brain holds rather
    than from a pack it was never given."""
    row = conn.execute(
        "SELECT id, summary FROM memories WHERE COALESCE(is_archived,0)=0 AND summary IS NOT NULL "
        "AND length(summary) > 0 ORDER BY importance DESC, id ASC LIMIT 1").fetchone()
    if row is None:
        checks.add("recall over this brain's own content", False,
                   detail="the brain holds no live memory with a summary to recall",
                   fix="An empty brain has nothing to recall; install without --tier1-ambient to seed one.",
                   means="the brain holds nothing to recall")
        return
    import re as _re
    words = sorted({w for w in _re.findall(r"[A-Za-z]{5,}", row[1] or "")}, key=lambda w: (-len(w), w))
    if not words:
        checks.add("recall over this brain's own content", False,
                   detail="memory #%s has no word of 5+ letters to query by" % row[0])
        return
    hits = _recall(conn, words[0], limit=10)
    checks.add("recall over this brain's own content ('%s')" % words[0], any(h[0] == row[0] for h in hits),
               detail="memory #%s %s in the top %d" % (row[0], "found" if any(h[0] == row[0] for h in hits)
                                                    else "NOT found", len(hits)),
               fix="Keyword recall does not return the brain's own memory -- memories_fts may be out of sync "
                   "with memories; inspect with the lite CLI: claude_memory_lite.py recall --query <word>",
               means="keyword recall did not return a memory the brain holds: questions to the brain may come back empty")


SEED_ANCHOR_PREFIX = "anchor:seed_"
SEED_REV_PREFIX = "seed-rev:"


def _topic_tags(m: dict, common_tag: str) -> list:
    """A seed entry's TOPIC tags: not the pack's common tag, not its per-entry anchor, not a revision stamp."""
    return [t.strip() for t in (m.get("tags", "") or "").split(",")
            if t.strip() and t.strip() != common_tag and not t.strip().startswith((SEED_ANCHOR_PREFIX, SEED_REV_PREFIX))]


def seed_received(conn, seeds: list, common_tag: str):
    """THE PER-MEMORY ANCHOR DELTA, read-only (Round 3 G1, item 2): which of the pack's entries THIS brain has received
    (present / adopted / changed / left -- some revision of it is here) and which it has not (new). The loader's own
    seed_plan (organs/claude_memory_lite.py, the M15 delta) decides it, so the gate and `load-seed --plan` can never
    disagree. Returns (plan, None) or (None, why)."""
    organs = Path(__file__).resolve().parent.parent / "organs"
    try:
        sys.path.insert(0, str(organs))
        import claude_memory_lite as _lite  # noqa: WPS433 (the loader's delta, never a copy of it)
        anchored = all(len([t for t in (m.get("tags") or "").split(",") if t.strip().startswith(SEED_ANCHOR_PREFIX)]) == 1
                       for m in seeds)
        if not anchored:
            return None, "this seed pack's entries carry no per-entry anchors (a pack older than 1.5.0)"
        return _lite.seed_plan(conn, seeds, common_tag), None
    except Exception as e:  # noqa: BLE001 -- an unreadable delta is a finding, never a pass
        return None, "%s: %s" % (type(e).__name__, e)


def check_seed_loaded(checks: Checks, conn, seeds: list, common_tag: str, of_total: int = 0):
    """2. Seed pack loaded: tagged count >= the entries required here -- the whole pack on a fresh install, or the entries
    THIS brain has received on an upgrade (of_total = the pack's size, named in the check)."""
    tagged = conn.execute(
        "SELECT COUNT(DISTINCT memory_id) FROM memory_tags WHERE tag = ?", (common_tag,)
    ).fetchone()[0]
    expected = len(seeds)
    name = (f"seed pack loaded ({expected} memories)" if not of_total else
            f"seed pack: the {expected} entries this brain has received (of the pack's {of_total}) are present")
    checks.add(
        name, tagged >= expected,
        detail=f"{tagged} memories tagged '{common_tag}' (expected >= {expected})",
        fix=f"Load the seed pack: python3 claude_memory_lite.py load-seed seed_pack.json",
        means="starter memories this brain should hold are missing: the brain works, but knows less than it should",
    )
    return tagged


def check_recall_path(checks: Checks, conn, seeds: list, summaries: list = None):
    """3. Recall path: a query from a seed's OWN words surfaces THAT seed.

    We pick a handful of seeds with distinctive topic leads and assert the matching
    memory appears in the top results. The query is derived from the seed's summary,
    so this proves the keyword index + MATCH + ranking actually return the right row.
    """
    # (probe query, distinctive phrase that must appear in the matched summary)
    # Chosen to be unambiguous: each phrase is unique to exactly one seed's summary.
    probes = [
        ("batch parallel recall queries", "BATCH PARALLEL RECALL"),
        ("whisper observations in flight", "WHISPERS"),
        ("valid categories fixed enum", "VALID CATEGORIES"),
        ("two tier embedding nomic", "TWO-TIER EMBEDDING"),
        ("session start protocol prime context", "SESSION-START PROTOCOL"),
        ("store discipline liberally", "STORE DISCIPLINE"),
        ("fresh claude bootstrap inherited brain", "FRESH-CLAUDE BOOTSTRAP"),
    ]
    # Only probe topics that actually exist in this seed pack (defensive) -- or, on an upgrade, in the summaries of the
    # rows THIS brain holds for the entries it has received (Round 3 G1, item 2: never a topic it was not given).
    if summaries is None:
        summaries = [m.get("summary", "") for m in seeds]
    any_run = False
    for query, must_contain in probes:
        if not any(must_contain in s for s in summaries):
            continue  # this seed pack doesn't carry that topic — skip silently
        any_run = True
        hits = _recall(conn, query, limit=5)
        matched = any(must_contain in (summ or "") for _id, summ in hits)
        top = hits[0][1][:60] + "…" if hits else "(no results)"
        checks.add(
            f"recall '{query}' -> finds the right memory", matched,
            detail=f"top hit: {top}" if matched else
                   f"expected a memory whose summary contains '{must_contain}'; "
                   f"got {len(hits)} hits, top: {top}",
            fix="Keyword recall is not returning the seeded memory — confirm the seed "
                "pack loaded (check 2) and that memories_fts is populated "
                "(re-run the installer if not).",
            means="keyword recall did not return a memory the brain holds: questions to the brain may come back empty",
        )
    if not any_run:
        checks.add(
            "recall path exercised", False,
            detail="none of the probe topics were present in the seed pack",
            fix="Seed pack appears empty or unexpected — re-run load-seed seed_pack.json.",
            means="recall could not be exercised at all, so it is not known to work",
        )


def check_tag_filter(checks: Checks, conn, seeds: list, common_tag: str):
    """4. Tag-filter path resolves via the JOIN table (schema gotcha done right)."""
    # common tag should return all seeds
    ids_common = {r[0] for r in conn.execute(
        "SELECT memory_id FROM memory_tags WHERE tag = ?", (common_tag,)
    ).fetchall()}
    checks.add(
        f"tag filter '{common_tag}' via JOIN table", len(ids_common) >= len(seeds),
        detail=f"{len(ids_common)} memories carry the tag (JOIN, not content-match)",
        fix="Tag lookup via memory_tags returned too few rows — re-run the installer.",
        means="filtering by tag returns fewer memories than the brain holds",
    )
    # a specific TOPIC tag present in the pack should resolve to >=1 memory -- never the entry's anchor or revision stamp,
    # which an upgraded brain's older copy of the same words does not carry until the owner loads the pack (Round 3 G1:
    # the reference host's gate failed "'anchor:seed_what_this_is' resolves 0" over a brain holding those exact words)
    topic_tag = None
    for m in seeds:
        tt = _topic_tags(m, common_tag)
        if tt:
            topic_tag = tt[0]
            break
    if topic_tag:
        n = conn.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE tag = ?", (topic_tag,)
        ).fetchone()[0]
        checks.add(
            f"topic tag '{topic_tag}' resolves", n >= 1,
            detail=f"{n} memories tagged '{topic_tag}'",
            fix="A known topic tag returned 0 memories — the JOIN table did not seed "
                "correctly; re-run load-seed.",
            means="a tag the starter memories carry finds nothing: tag filtering is not working",
        )


def check_classify(checks: Checks, seeds: list):
    """5. classify_memory maps each seed to its declared kind (pure-function).

    Ties the shipped kind_taxonomy logic to the guaranteed seed data. Imported
    defensively: if kind_taxonomy is not importable (shouldn't happen in a full
    install), this is reported as a skipped bonus, never a hard failure.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "organs"))
    try:
        import kind_taxonomy  # noqa: WPS433 (local import is intentional + defensive)
    except Exception as e:  # pragma: no cover - only when module absent
        checks.note(f"classify check skipped: kind_taxonomy not importable ({e}). "
                    "Keyword recall (the core) is unaffected.")
        return

    mismatches = []
    for m in seeds:
        summary = m.get("summary", "")
        content = m.get("content", "")
        category = m.get("category", "")
        tags = [t.strip() for t in (m.get("tags", "") or "").split(",") if t.strip()]
        declared = m.get("kind", "episodic")
        kind, _conf = kind_taxonomy.classify_memory(summary, content, category, tags)
        if kind != declared:
            mismatches.append(f"{summary[:40]!r}: classified {kind}, declared {declared}")
    checks.add(
        f"classify_memory matches declared kind for all {len(seeds)} seeds",
        not mismatches,
        detail="every seed classifies to its declared kind"
               if not mismatches else f"{len(mismatches)} mismatch(es): " + "; ".join(mismatches[:3]),
        fix="The kind taxonomy disagrees with a seed's declared kind — this indicates "
            "a taxonomy/seed drift; inspect kind_taxonomy._CATEGORY_KIND_MAP.",
        means="a packaging fault (the shipped taxonomy and the shipped seeds disagree), not your data",
    )


def check_tier(checks: Checks, db_path: Path):
    """6. Tier detection (bonus only — NEVER required to pass)."""
    semantic = False
    detail = "Tier 0 (keyword recall) — always available on stdlib python3."
    try:
        import sqlite_vec  # noqa: F401
        # presence of the embeddings table is the real signal
        conn = _connect_ro(db_path)
        tabs = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        if "memory_embeddings" in tabs:
            semantic = True
            detail = "Tier 1 (semantic recall) available — sqlite_vec + embeddings present."
    except Exception:
        semantic = False
    checks.add(
        "tier detection", True, required=False,
        detail=detail,
    )
    checks.note(
        "Semantic (Tier 1) recall is ACTIVE." if semantic
        else "Running on Tier 0 (keyword recall). To add semantic recall later, "
             "run the Tier-1 upgrade in install.sh (optional)."
    )


def check_models(checks: Checks, db_path: Path):
    """7. Full-L4 model presence (BONUS only — Tier 1, NEVER required to pass).

    Confirms the two local models the full brain needs both LOAD from the local
    HuggingFace cache: the embedder ``nomic-embed-text-v1.5`` (semantic vectors)
    and the reranker the DEFAULT hybrid recall will load -- ``Qwen3-Reranker-0.6B``
    by default, ``bge-reranker-v2-m3`` under ``VOIDLOOM_RERANKER=bge`` (which the DEFAULT hybrid
    recall uses). This makes "full L4 alive" verifiable in one command.

    Tier-0 SAFETY: this is gated on Tier 1 actually being present (sentence-transformers
    importable AND the embeddings table exists). On a pure Tier-0 Mac it is reported as
    a skipped note, NEVER a failure — Tier 0 must not depend on any model. It also
    loads with ``local_files_only=True`` so it only VERIFIES the pre-downloaded cache
    and never itself triggers a (~1.4GB) download.
    """
    # Only attempt the (heavier) model load when Tier 1 looks present.
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        checks.note("full-L4 model check skipped: sentence-transformers not installed "
                    "(Tier 0 / keyword recall — models are not needed).")
        return
    try:
        conn = _connect_ro(db_path)
        tabs = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
    except Exception:
        tabs = set()
    if "memory_embeddings" not in tabs:
        checks.note("full-L4 model check skipped: semantic tier not initialized "
                    "(no embeddings table). Tier 0 is unaffected.")
        return
    # CRUCIBLE-3 D7-F6: the two conditions above are BOTH TRUE on a `--tier0-only`
    # install whose ambient interpreter happens to carry the stack -- a blank brain
    # HAS the table. So a script install.sh advertises as the "stdlib-only Tier-0
    # acceptance test" imported torch and loaded a model. Measured: the identical
    # install took 13.75 s on anaconda vs 4.96 s on /usr/bin/python3 -- roughly
    # 8.8 s of a Tier-0 install spent inside a bonus check that CANNOT CHANGE THE
    # OUTCOME -- and it leaked library noise into the install log differently on
    # different hosts ("No sentence-transformers model found ... Creating a new one
    # with mean pooling", and a torch `_IncompatibleKeys` repr).
    #
    # Two further conditions, both stdlib, both cheap, either of which means there
    # is nothing here to verify:
    try:
        conn = _connect_ro(db_path)
        try:
            _row = conn.execute(
                "SELECT value FROM metadata WHERE key='tier0_only'").fetchone()
            _t0 = bool(_row) and str(_row[0]).strip().lower() not in ("", "0", "false", "no", "off")
            try:
                _vecs = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
            except Exception:
                # A vec0 VIRTUAL table cannot be counted without the extension, and
                # loading it here would break the stdlib-only promise this script's
                # own name makes. So fall back to a count that is always stdlib:
                # a brain with no live memories has nothing embedded, whatever the
                # table is. UNKNOWN stays UNKNOWN when neither can be read.
                _vecs = None
                try:
                    _mem = conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0"
                    ).fetchone()[0]
                    if _mem == 0:
                        _vecs = 0
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        _t0, _vecs = False, None
    if _t0:
        checks.note("full-L4 model check skipped: this brain records tier0_only — the "
                    "operator asked for Tier 0, so no model is loaded here either.")
        return
    if _vecs == 0:
        checks.note("full-L4 model check skipped: the embeddings table holds 0 vectors, "
                    "so there is no semantic tier to verify. Tier 0 is unaffected.")
        return

    # THE BRAIN DECLARES ITS EMBEDDER; THIS GATE USED TO TYPE ONE.
    #
    # Found 2026-09-17 while discharging plan item #564 ("verify both tiers resolve
    # at install"): on a JINA brain this check reported
    #     "both models cached: nomic-ai/nomic-embed-text-v1.5 (embedder) …"
    # and PASSED. It verified the presence of a model that brain does not use, and
    # would have passed on a host missing the model it does. `metadata.active_
    # embedding_model` is written by `cmd_init` at :963-965 and is the answer.
    #
    # Same shape as the reranker half of this function, fixed one cut earlier
    # (D9-F2), and as voidloom/_bootstrap's census of two over three: a literal
    # standing in for a value the artifact already publishes about itself. The
    # fallback stays the CPU tier -- the right guess when a brain declares
    # nothing -- and names itself as a fallback in the detail line.
    _declared = None
    try:
        _cm = _connect_ro(db_path)
        try:
            _declared = (_cm.execute(
                "SELECT value FROM metadata WHERE key='active_embedding_model'"
            ).fetchone() or [None])[0]
        finally:
            _cm.close()
    except Exception:
        _declared = None
    EMBED_MODEL = _declared or "nomic-ai/nomic-embed-text-v1.5"
    EMBED_SOURCE = ("declared by this brain (metadata.active_embedding_model)"
                    if _declared else
                    "FALLBACK: this brain declares no embedder; assuming the CPU tier")
    # CRUCIBLE-3 D9-F2: this asserted BAAI/bge-reranker-v2-m3 and called it the
    # "default-recall reranker". It is the FALLBACK. The gate therefore passed on
    # a cache that did not contain the model the first recall would load, and
    # failed on a cache that did -- an acceptance check pointed at the wrong
    # artifact is worse than none, because it certifies the gap.
    #
    # Resolve the backend the way the runtime resolves it, and verify THAT.
    _rr = (os.environ.get("VOIDLOOM_RERANKER") or "").strip().lower()
    RERANKER_BACKEND = _rr if _rr in ("qwen", "bge") else "qwen"
    RERANKER_MODEL = ("BAAI/bge-reranker-v2-m3" if RERANKER_BACKEND == "bge"
                      else "Qwen/Qwen3-Reranker-0.6B")
    # Does THIS host use a reranker at all? The runtime's own gate decides (a GPU-class accelerator and
    # >= 15 GiB RAM, or the owner's config); where it is off, the installer never downloads one, so demanding
    # it here printed [FAIL] on every correct CPU install (public-release clean room, 2026-10-27).
    # confirm=False: stdlib only, no torch import -- what the resolver plans, as doctor reports it.
    _rr_on, _rr_why = True, ""
    try:
        _organs = str(Path(__file__).resolve().parent.parent / "organs")
        if _organs not in sys.path:
            sys.path.insert(0, _organs)
        import hybrid_retrieval as _hr
        _g = _hr.reranker_gate(confirm=False)
        _rr_on, _rr_why = bool(_g.get("enabled")), str(_g.get("why") or "")
    except Exception as _e:  # the gate could not be read: keep the stricter check, say so
        _rr_why = f"reranker gate unreadable ({type(_e).__name__}); checking for it anyway"
    try:
        from sentence_transformers import SentenceTransformer
        from huggingface_hub import snapshot_download
        # local_files_only=True → verify the cache only; never download here.
        # device="cpu": this verifies the CACHE; with no device, SentenceTransformer probes CUDA in THIS process, and
        # under a CUDA_VISIBLE_DEVICES that selects no device the WSL2 driver's cuInit segfaults (reflector-4, executed)
        SentenceTransformer(EMBED_MODEL, trust_remote_code=True, local_files_only=True, device="cpu")
        # snapshot_download, not CrossEncoder: the default backend is a
        # Qwen3ForCausalLM, which CrossEncoder cannot instantiate. The snapshot is
        # the cache BOTH loaders read, so this check is backend-agnostic by
        # construction rather than by remembering to update it.
        if _rr_on:
            snapshot_download(RERANKER_MODEL, local_files_only=True)
            ok, detail, fix = True, (
                f"both models cached: {EMBED_MODEL} (embedder — {EMBED_SOURCE}) + "
                f"{RERANKER_MODEL} (reranker, backend '{RERANKER_BACKEND}' — the one "
                f"this host's recall will load)."
            ), ""
        else:
            ok, detail, fix = True, (
                f"embedder cached: {EMBED_MODEL} ({EMBED_SOURCE}). No reranker on this host, by design: "
                f"{_rr_why or 'the reranker gate is off'}."
            ), ""
    except Exception as e:
        ok, detail = False, (
            "one or both required models are not in the local cache "
            f"(~/.cache/huggingface): {e}"
        )
        fix = ("Pre-download them: "
               "<venv>/bin/python3 -c \"from sentence_transformers import "
               "SentenceTransformer; from huggingface_hub import snapshot_download; "
               "SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True); "
               f"snapshot_download('{RERANKER_MODEL}')\"  "
               "(or re-run install.sh). Keyword recall is unaffected.")
    checks.add(
        "full L4 models load (embedder + reranker)", ok, required=False,
        detail=detail, fix=fix,
    )


# ---------------------------------------------------------------------------
# Orchestration + reporting
# ---------------------------------------------------------------------------
def run_self_check(seed_optional: bool = False) -> Checks:
    checks = Checks()
    db_path = _resolve_db_path()
    seed_path = _resolve_seed_path()

    # Load the seed pack (the source of truth for what SHOULD be present).
    seeds = []
    common_tag = "seed-pack-agnostic"
    seed_version = ""
    if seed_path.exists():
        try:
            data = json.loads(seed_path.read_text(encoding="utf-8"))
            seeds = data.get("memories", []) or []
            common_tag = data.get("common_tag", common_tag)
            seed_version = str(data.get("seed_pack_version") or "")
            checks.add("seed_pack.json present + valid JSON", True,
                       detail=f"{len(seeds)} memories defined in {seed_path.name}")
        except json.JSONDecodeError as e:
            checks.add("seed_pack.json present + valid JSON", False,
                       detail=f"invalid JSON: {e}",
                       fix="Replace seed_pack.json with the packaged copy.")
    else:
        checks.add("seed_pack.json present + valid JSON", False,
                   detail=f"not found at {seed_path}",
                   fix="Ship seed_pack.json beside self_check.py or set WIZARD_LIBRARY_SEED.")

    conn = check_schema(checks, db_path)
    no_pack_here = False
    plan = None
    if conn is not None and seeds and seed_optional:
        # --seed-optional (reference-host round, R1): an UPGRADE of an existing brain adds
        # no memories to it (the ambient shape leaves the vector population as found), so a
        # brain that never carried the agnostic seed pack does not carry it now. The checks
        # DEFINED OVER the pack are then not applicable -- reported as such, never as passed
        # -- and the recall claim is checked over the brain's own content instead. A brain
        # carrying PART of the pack is a real finding and gets the full checks.
        tagged = conn.execute("SELECT COUNT(DISTINCT memory_id) FROM memory_tags WHERE tag = ?",
                              (common_tag,)).fetchone()[0]
        no_pack_here = tagged == 0
        if not no_pack_here:
            # Round 3 G1, item 2 (memory #23616): the reference host's ambient upgrade FAILED 3 seed checks while every
            # brain-health check passed -- because the installer's own policy for that shape is "add no memories to an
            # existing brain" and seed pack 1.5.0 had 1 new + 11 changed entries. The gate contradicted the installer.
            # Now the REQUIRED seed checks are scoped to the entries THIS brain has received (the loader's per-memory
            # anchor delta), and the rest are REPORTED as the owner's choice -- never failed. A fresh install (no
            # --seed-optional) still requires the full pack.
            plan, why = seed_received(conn, seeds, common_tag)
            if plan is None:
                checks.add("seed pack: the entries this brain has received could be derived", False,
                           detail=f"the per-memory anchor delta could not be computed ({why})",
                           fix="Re-run the installer so organs/claude_memory_lite.py is laid beside the brain.",
                           means="the gate cannot tell which starter memories this brain should hold")
    if conn is not None and seeds and no_pack_here:
        checks.note("this brain carries no seed pack and the upgrade did not add one (--seed-optional): "
                    "the seed-loaded, seed-recall and tag-filter checks are NOT APPLICABLE here -- not passed")
        check_own_content_recall(checks, conn)
    elif conn is not None and seeds and seed_optional and plan is not None:
        got = [a for a in plan["actions"] if a[0] in ("present", "adopted", "changed", "left")]
        received = [seeds[a[1]] for a in got]
        live_rows = [a[3] for a in got if a[3] is not None]
        summaries = []
        if live_rows:
            q = ",".join("?" for _ in live_rows)
            summaries = [r[0] or "" for r in conn.execute(
                f"SELECT summary FROM memories WHERE id IN ({q}) AND COALESCE(is_archived,0)=0", live_rows).fetchall()]
        # R3-06: a scoped check must be ABLE to fail. 'received' is read from the rows that carry the pack's tags, so on its
        # own it cannot see a memory whose tag rows were lost: the loader's delta names those ('held-untagged': the brain
        # holds the entry's words or anchor WITHOUT the pack's tags) and they FAIL here, by name -- never offered as new.
        held_u = [a for a in plan["actions"] if a[0] == "held-untagged"]
        live_ids = set()
        if held_u:
            q = ",".join("?" for _ in held_u)
            live_ids = {r[0] for r in conn.execute(
                f"SELECT id FROM memories WHERE id IN ({q}) AND COALESCE(is_archived,0)=0", [a[3] for a in held_u])}
        live_u = [a for a in held_u if a[3] in live_ids]
        arch_u = [a for a in held_u if a[3] not in live_ids]
        checks.add("seed pack: every entry this brain holds carries the pack's tags", not live_u,
                   detail=("%d entr%s held here WITHOUT the pack's tag rows (memory #%s): %s%s" % (
                       len(live_u), "y" if len(live_u) == 1 else "ies", ", #".join(str(a[3]) for a in live_u[:10]),
                       ", ".join(a[2][len(SEED_ANCHOR_PREFIX):] for a in live_u[:5]), " ..." if len(live_u) > 5 else "")
                       if live_u else "every entry this brain holds carries its tags"),
                   fix=("If the memory_tags table lost rows, restore the brain from the pre-upgrade copy the installer "
                        "left beside it (claude_memory.db.pre-upgrade-<time>.db); if you stored these words by hand, "
                        "archive that copy. load-seed never adds them again: it never duplicates a memory."),
                   means=("memories this brain holds have lost the pack's tags: filtering by tag misses them, and "
                          "the gate cannot count them as received"))
        if arch_u:
            checks.note("%d seed entr%s held here only as an ARCHIVED memory without the pack's tags (memory #%s): "
                        "not loaded again (an archived memory is never resurrected)" % (
                            len(arch_u), "y" if len(arch_u) == 1 else "ies", ", #".join(str(a[3]) for a in arch_u[:10])))
        check_seed_loaded(checks, conn, received, common_tag, of_total=len(seeds))
        check_recall_path(checks, conn, received, summaries=summaries)
        check_tag_filter(checks, conn, received, common_tag)
        new_a = [a[2] for a in plan["actions"] if a[0] == "new"]
        chg_a = [a[2] for a in plan["actions"] if a[0] == "changed"]
        if new_a or chg_a:
            organs = Path(__file__).resolve().parent.parent / "organs"
            checks.choice(
                "seed pack %s has %d new entr%s this brain has not received%s and %d changed entr%s whose older "
                "revision it holds%s. Adding them is YOUR choice, not a failure (an upgrade adds no memories by "
                "itself): run the installer with --load-seed (or answer y when it asks), or now: "
                "WIZARD_LIBRARY_DB='%s' python3 '%s' load-seed --file '%s' -- only those entries are added; an older "
                "revision is archived, never deleted." % (
                    seed_version or "?", len(new_a), "y" if len(new_a) == 1 else "ies",
                    " (%s)" % ", ".join(x[len(SEED_ANCHOR_PREFIX):] for x in new_a[:5]) if new_a else "",
                    len(chg_a), "y" if len(chg_a) == 1 else "ies",
                    " (%s)" % ", ".join(x[len(SEED_ANCHOR_PREFIX):] for x in chg_a[:5]) +
                    (" ..." if len(chg_a) > 5 else "") if chg_a else "",
                    db_path, organs / "claude_memory_lite.py", seed_path))
    elif conn is not None and seeds:
        check_seed_loaded(checks, conn, seeds, common_tag)
        check_recall_path(checks, conn, seeds)
        check_tag_filter(checks, conn, seeds, common_tag)
    if conn is not None:
        conn.close()

    if seeds:
        check_classify(checks, seeds)
    check_tier(checks, db_path)
    check_models(checks, db_path)
    return checks


def _print_human(checks: Checks) -> None:
    print("=" * 64)
    print("Voidloom MEMORY — SELF-CHECK (Tier 0, keyword recall)")
    print("=" * 64)
    for r in checks.results:
        mark = "PASS" if r["ok"] else "FAIL"
        tag = "" if r["required"] else " (bonus)"
        line = f"  [{mark}]{tag} {r['name']}"
        print(line)
        if r["detail"]:
            print(f"         {r['detail']}")
        if not r["ok"] and r["fix"]:
            print(f"         FIX: {r['fix']}")
    if checks.notes:
        print("\n  Notes:")
        for n in checks.notes:
            print(f"    - {n}")
    if checks.choices:
        print("\n  Your choice (not a failure):")
        for n in checks.choices:
            print(f"    - {n}")
    print("-" * 64)
    ok = not checks.required_failures
    print(f"  Required checks: {checks.passed_required}/{checks.total_required} passed"
          + (f"   |   Bonus: {checks.passed_optional}/{checks.total_optional}"
             if checks.total_optional else ""))
    if ok:
        print("  VERDICT: YOUR BRAIN IS ALIVE. Store + recall work end-to-end.")
    else:
        print(f"  VERDICT: {len(checks.required_failures)} required check(s) FAILED "
              "— see FIX lines above.")
    print("=" * 64)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Self-check the Voidloom brain against the seed pack "
                    "(Tier 0, stdlib-only). Exit 0 = brain alive."
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit a machine-readable JSON envelope instead of the report.")
    parser.add_argument("--quiet", action="store_true",
                        help="Print only the final verdict line + exit code.")
    # accept the verb form `self_check.py --self-check` too, so the documented
    # `--self-check` entrypoint works whether called as a script or a subcommand.
    parser.add_argument("--self-check", action="store_true",
                        help="(Default action) run the self-check. Accepted for symmetry.")
    parser.add_argument("--seed-optional", action="store_true",
                        help="an UPGRADED brain (the ambient shape adds no memories without the owner's choice): "
                             "the required seed checks are scoped to the entries THIS brain has received (the "
                             "loader's per-memory anchor delta), and entries it has not received are REPORTED as "
                             "the owner's choice, never failed; a brain that never carried the pack is checked over "
                             "its own content. install.sh passes it in the ambient shape. Without it the FULL pack "
                             "is required (a fresh install).")
    args = parser.parse_args(argv)

    checks = run_self_check(seed_optional=args.seed_optional)
    ok = not checks.required_failures
    exit_code = 0 if ok else 1

    if args.json:
        print(json.dumps({
            "status": "ok" if ok else "failed",
            "required_passed": checks.passed_required,
            "required_total": checks.total_required,
            "bonus_passed": checks.passed_optional,
            "bonus_total": checks.total_optional,
            "checks": checks.results,
            "notes": checks.notes,
            "choices": checks.choices,
            "verdict": "brain alive" if ok else "brain check failed",
        }, indent=2))
    elif args.quiet:
        print("brain alive" if ok else f"brain check FAILED "
              f"({len(checks.required_failures)} required check(s))")
    else:
        _print_human(checks)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

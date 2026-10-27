#!/usr/bin/env python3
"""dream_ledger.py -- a dream's provenance, its read-time trust, the evidence that may move it, and the owner's verbs.

THE GOVERNING PRINCIPLE (the dream fix, Tranche A): every product claim about a synthetic row is a QUERY ON ITS
PROVENANCE, never a hope about its rank. So this module holds the few numbers that carry a dream and the only paths
that may move them:

  * a dream is BORN at 0.40 (``BIRTH_CONFIDENCE``), stamped ``derived_from='dream_synthesis'``. The stored
    ``memories.confidence`` is that birth value and it is IMMUTABLE: nothing in this package UPDATEs it after the
    INSERT (the re-stamp of the old rows below sets it ONCE, from a recorded pre-image).
  * its FADE is DERIVED at READ time from elapsed time: ``max(FLOOR, birth x 0.5 ** (age_days / HALF_LIFE_DAYS))``.
    Nothing decays per run, so the nightly and the 14:00 daytime cycles cannot halve the half-life between them, and
    there is ONE decay source (this function).
  * EVIDENCE raises it by ``EVIDENCE_STEP`` per event, capped at ``CAP``. Evidence is only what the WORLD wrote back:
      - the owner's ``voidloom dream confirm <id>`` (terminal only; tag ``dream-confirmed``),
      - ``store --answers <dream_id>``: a later RECORDED memory that settles it (a ``refines`` edge answer -> dream,
        metadata origin ``dream_answer``),
      - an APPROVED add_contradicts proposal (a NEW edge) on the dream's OWN pair (edge_proposals status
        approved/enacted); an approved invalidate_edge or supersede RETIRES a link and is never evidence FOR it.
    Attention is never evidence: showing a dream in recall logs ``recall:dream-lead`` (the MET LEDGER) and every
    learning path excludes that access type. There is no Hebbian path and nothing promotes a dream into ``results``.

THE OWNER'S VERBS (``voidloom dream <sub>``):
  backfill --plan            read-only: the re-stamp of the old dream rows, the 30-night backfill, the playbook dedup
  backfill --apply --restamp|--backfill|--playbooks [--preimage PATH]   writes, pre-image first, enqueue_scan=False
  backfill --rollback PREIMAGE   archive the backfill by tag, restore every re-stamped field from the pre-image,
                             un-archive the playbook duplicates
  confirm <dream_id>         the owner's word (+0.10, cap 0.65); refuses without a terminal
  show <dream_id>            the dream's full text, framed, with its derived confidence and evidence
  watch [--window-days N]    the dream row invariant (the sentinel's dream watch; `voidloom doctor` runs it too)

No numbered migration: 035-038 are taken on other branches and a data-only migration re-runs on every install. The
re-stamp is part of the fix; the backfill and the playbook archive are the owner's to take at landing (the verb makes
each an explicit part). Stdlib only (the encoder is imported lazily, and only by --apply of rows lacking a vector).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------------------------------------------------------------
# The numbers that carry a dream (SYNTHESIS A1, A5). Every reader imports them from here; nothing re-types them.
# ---------------------------------------------------------------------------------------------------------------------
DREAM_DERIVED_FROM = "dream_synthesis"
PLAYBOOK_DERIVED_FROM = "playbook_synthesis"
BIRTH_CONFIDENCE = 0.40
HALF_LIFE_DAYS = 30.0
FLOOR = 0.10
EVIDENCE_STEP = 0.10
CAP = 0.65
MET_ACCESS_TYPE = "recall:dream-lead"          # the met ledger; 'dream' is a machine marker for every learning path
CONFIRM_TAG = "dream-confirmed"
ANSWER_ORIGIN = "dream_answer"
APPROVED_PROPOSAL_STATUSES = ("approved", "enacted")
BACKFILL_TAG_PREFIX = "dream-backfill-"
PLAYBOOK_DEDUP_TAG_PREFIX = "playbook-dedup-"
RELATIONS = ("connection", "contradiction", "tension", "question", "precedent")
TENSION_RELATIONS = ("contradiction", "tension")

# The re-stamp selector (SYNTHESIS A7): the old dream rows by what they ARE, never by the `dream` tag (that tag also
# sits on a protected authored lesson). Selects the rows the L4 path wrote, linked AND the laptop-merged ones.
RESTAMP_WHERE = ("source_project='memory-dreaming' AND kind='reflection' "
                 "AND content LIKE '[Dream synthesis%'")
# The playbook rows the nightly emission re-wrote (SYNTHESIS A8).
PLAYBOOK_WHERE = "source_project='dream-pipeline' AND kind='procedural' AND summary LIKE 'Playbook candidate%'"

_HEADER_RE = re.compile(r"^\[Dream synthesis[^\]]*\]\s*\n(?:Source memories:[^\n]*\n)?\s*", re.S)
_MEMORY_AB_RE = re.compile(r"\b[Mm]emory\s+([AB])\b(\s*\(#\d+\))?")
_MEMORIES_IDS_RE = re.compile(r"\bmemories\s+#\d+\s*(?:\+|and)\s*#\d+", re.I)
_HASH_ID_RE = re.compile(r"\(#\d+\)")


# =====================================================================================================================
# Time
# =====================================================================================================================
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_ts(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    s = str(ts).strip().replace("T", " ").replace("Z", "")
    s = s.split("+")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def _sql_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# =====================================================================================================================
# Read-time trust: THE one decay source
# =====================================================================================================================
def faded_confidence(birth: Any, created_at: Any, now: Optional[datetime] = None) -> float:
    """``max(FLOOR, birth x 0.5 ** (age_days / HALF_LIFE_DAYS))`` -- a pure function of elapsed time.

    ``birth`` is clamped to ``BIRTH_CONFIDENCE``: a hand-raised stored value never makes a dream stronger than it was
    born. An unparseable ``created_at`` reads as age 0 (the birth value), never as fully faded."""
    try:
        b = float(birth)
    except (TypeError, ValueError):
        b = BIRTH_CONFIDENCE
    b = max(0.0, min(b, BIRTH_CONFIDENCE))
    now = now or _utcnow()
    dt = _parse_ts(created_at)
    age = max(0.0, (now - dt).total_seconds() / 86400.0) if dt else 0.0
    return max(FLOOR, b * (0.5 ** (age / HALF_LIFE_DAYS)))


def faded_from_age(birth: Any, age_days: Any) -> float:
    """The same law as ``faded_confidence``, from an age already computed (e.g. by SQLite's julianday). An unknown
    age reads as 0 (the birth value)."""
    try:
        b = float(birth)
    except (TypeError, ValueError):
        b = BIRTH_CONFIDENCE
    b = max(0.0, min(b, BIRTH_CONFIDENCE))
    try:
        age = max(0.0, float(age_days))
    except (TypeError, ValueError):
        age = 0.0
    return max(FLOOR, b * (0.5 ** (age / HALF_LIFE_DAYS)))


def effective_confidence(birth: Any, created_at: Any, evidence_events: int = 0,
                         now: Optional[datetime] = None) -> float:
    """The confidence a reader sees: the faded birth value plus ``EVIDENCE_STEP`` per evidence event, capped."""
    ev = max(0, int(evidence_events or 0))
    return round(min(CAP, faded_confidence(birth, created_at, now) + EVIDENCE_STEP * ev), 4)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                            (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _cols(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def dream_pairs(conn: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, Tuple[Optional[int], Optional[int]]]:
    """{dream_memory_id: (a, b)} from dream_log (authoritative), else from memory_provenance (dream_synthesis).
    A dream with neither (the laptop-merged rows) has no known pair and is absent from the map -- never invented."""
    out: Dict[int, Tuple[Optional[int], Optional[int]]] = {}
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return out
    q = ",".join("?" * len(ids))
    if _table_exists(conn, "dream_log"):
        for mid, a, b in conn.execute(
                f"SELECT dream_memory_id, memory_a_id, memory_b_id FROM dream_log "
                f"WHERE dream_memory_id IN ({q})", ids).fetchall():
            out[int(mid)] = (a, b)
    missing = [i for i in ids if i not in out]
    if missing and _table_exists(conn, "memory_provenance"):
        q2 = ",".join("?" * len(missing))
        acc: Dict[int, List[int]] = {}
        for d, s in conn.execute(
                f"SELECT derived_memory_id, source_memory_id FROM memory_provenance "
                f"WHERE transformation_type='dream_synthesis' AND derived_memory_id IN ({q2}) ORDER BY id",
                missing).fetchall():
            acc.setdefault(int(d), []).append(int(s))
        for d, srcs in acc.items():
            if len(srcs) >= 2:
                out[d] = (srcs[0], srcs[1])
    return out


def evidence_counts(conn: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, Dict[str, int]]:
    """Per dream: {'confirm', 'answers', 'approved_pair_edges', 'total'} -- the ONLY things that raise a dream."""
    ids = [int(i) for i in ids if i is not None]
    out = {i: {"confirm": 0, "answers": 0, "approved_pair_edges": 0, "total": 0} for i in ids}
    if not ids:
        return out
    q = ",".join("?" * len(ids))
    try:
        for (mid,) in conn.execute(f"SELECT memory_id FROM memory_tags WHERE tag=? AND memory_id IN ({q})",
                                   (CONFIRM_TAG, *ids)).fetchall():
            out[int(mid)]["confirm"] = 1
    except sqlite3.Error:
        pass
    if _table_exists(conn, "memory_edges"):
        try:
            for tgt, n in conn.execute(
                    f"""SELECT e.target_id, COUNT(DISTINCT e.source_id) FROM memory_edges e
                        JOIN memories s ON s.id = e.source_id
                        WHERE e.predicate='refines' AND e.target_id IN ({q}) AND e.valid_until IS NULL
                          AND e.metadata LIKE '%{ANSWER_ORIGIN}%'
                          AND s.is_archived=0 AND s.derived_from IS NULL
                        GROUP BY e.target_id""", ids).fetchall():
                out[int(tgt)]["answers"] = int(n)
        except sqlite3.Error:
            pass
    if _table_exists(conn, "edge_proposals"):
        pairs = dream_pairs(conn, ids)
        st = ",".join("?" * len(APPROVED_PROPOSAL_STATUSES))
        for mid, (a, b) in pairs.items():
            if a is None or b is None:
                continue
            try:
                n = conn.execute(
                    f"""SELECT COUNT(*) FROM edge_proposals WHERE status IN ({st})
                        AND proposal_kind = 'add_contradicts'
                        AND ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))""",
                    (*APPROVED_PROPOSAL_STATUSES, a, b, b, a)).fetchone()[0]
            except sqlite3.Error:
                n = 0
            out[mid]["approved_pair_edges"] = int(n)
    for d in out.values():
        d["total"] = d["confirm"] + d["answers"] + d["approved_pair_edges"]
    return out


def effective_map(conn: sqlite3.Connection, ids: Optional[Sequence[int]] = None,
                  now: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """{dream_id: {'birth', 'created_at', 'faded', 'evidence', 'effective'}} for every live dream (or ``ids``)."""
    now = now or _utcnow()
    if ids is None:
        rows = conn.execute("SELECT id, confidence, created_at FROM memories "
                            "WHERE derived_from=? AND is_archived=0", (DREAM_DERIVED_FROM,)).fetchall()
    else:
        ids = [int(i) for i in ids]
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT id, confidence, created_at FROM memories WHERE id IN ({q})", ids).fetchall()
    ev = evidence_counts(conn, [r[0] for r in rows])
    out: Dict[int, Dict[str, Any]] = {}
    for mid, conf, created in rows:
        e = ev.get(int(mid), {"total": 0})
        out[int(mid)] = {"birth": conf, "created_at": created,
                         "faded": round(faded_confidence(conf, created, now), 4),
                         "evidence": e, "effective": effective_confidence(conf, created, e["total"], now)}
    return out


# =====================================================================================================================
# Evidence writers (the only three)
# =====================================================================================================================
def is_dream(conn: sqlite3.Connection, dream_id: int) -> bool:
    row = conn.execute("SELECT derived_from FROM memories WHERE id=?", (int(dream_id),)).fetchone()
    return bool(row and row[0] == DREAM_DERIVED_FROM)


def confirm(conn: sqlite3.Connection, dream_id: int) -> Dict[str, Any]:
    """The owner's word: tag the dream ``dream-confirmed`` (one event; idempotent). The stored birth value is NOT
    touched -- the effect is derived at read time."""
    if not is_dream(conn, dream_id):
        return {"status": "error", "error": "not_a_dream",
                "message": f"memory {dream_id} is not a dream (derived_from != '{DREAM_DERIVED_FROM}')"}
    before = effective_map(conn, [dream_id]).get(int(dream_id), {})
    conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)", (int(dream_id), CONFIRM_TAG))
    conn.commit()
    after = effective_map(conn, [dream_id]).get(int(dream_id), {})
    return {"status": "ok", "dream_id": int(dream_id), "effective_before": before.get("effective"),
            "effective_after": after.get("effective"), "evidence": after.get("evidence")}


def _revive_decay_retired(conn: sqlite3.Connection, answer_id: int, dream_id: int, meta: str) -> Optional[int]:
    """temporal_edges.revive_decay_retired for the answer edge; None when that module is absent (a partial install
    keeps the old INSERT OR IGNORE)."""
    try:
        import temporal_edges as _te  # stdlib + embed_resolver; laid beside this module
    except Exception:  # noqa: BLE001
        return None
    now = conn.execute("SELECT datetime('now')").fetchone()[0]
    return _te.revive_decay_retired(conn, answer_id, dream_id, 'refines', confidence=0.8, metadata=meta,
                                    extraction_method='manual', valid_from=now)


def record_answer(conn: sqlite3.Connection, answer_id: int, dream_id: int) -> Dict[str, Any]:
    """``store --answers <dream_id>``: a later RECORDED memory settles the dream. Writes one ``refines`` edge
    answer -> dream (0.8, origin dream_answer). Refused when the target is not a dream or the answer is itself
    derived (a synthesis cannot be the world writing back)."""
    if not is_dream(conn, dream_id):
        return {"status": "error", "error": "not_a_dream", "dream_id": int(dream_id)}
    row = conn.execute("SELECT derived_from FROM memories WHERE id=?", (int(answer_id),)).fetchone()
    if not row or row[0] is not None:
        return {"status": "error", "error": "answer_not_recorded", "answer_id": int(answer_id)}
    try:
        meta = json.dumps({"origin": ANSWER_ORIGIN})
        # An answer edge the decay RETIRED is answered afresh, as INSERT did when the prune deleted it (round 2, R2d);
        # INSERT OR IGNORE alone would leave it retired and the dream unanswered.
        if _revive_decay_retired(conn, int(answer_id), int(dream_id), meta) is None:
            conn.execute(
                """INSERT OR IGNORE INTO memory_edges (source_id, target_id, predicate, confidence, valid_from,
                       created_at, extraction_method, metadata)
                   VALUES (?, ?, 'refines', 0.8, datetime('now'), datetime('now'), 'manual', ?)""",
                (int(answer_id), int(dream_id), meta))
    except sqlite3.Error as e:
        return {"status": "error", "error": "edge_write_failed", "message": str(e)}
    return {"status": "ok", "answer_id": int(answer_id), "dream_id": int(dream_id)}


# =====================================================================================================================
# Summaries in named nouns (never "Memory A/B", never "memories #x + #y")
# =====================================================================================================================
def _subject(meta: Optional[dict], fallback: str) -> str:
    if not meta:
        return fallback
    s = (meta.get("summary") or meta.get("content") or "").strip().splitlines()[0] if (
        meta.get("summary") or meta.get("content")) else ""
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^\[[^\]]{0,80}\]\s*", "", s)           # a leading [tag] or [OBJECTIVE] marker is not a noun
    s = s.strip(" .:-")
    if not s:
        return fallback
    return ("the note on " + s[:70].rstrip(" ,;:-")) if len(s) > 3 else fallback


def dream_body(content: str) -> str:
    """The dream's own text without the '[Dream synthesis ...]' / 'Source memories:' header."""
    return _HEADER_RE.sub("", content or "", count=1).strip()


# THE TEXT A DREAM IS EMBEDDED FROM (DREAM_FLOOR_DECISION's endpoint-embedding measurement, model-choice Round 2 wave 2A):
# the BODY with the thinker's FRAMING removed -- a leading 'Both memories <...>:' (or ' -- ') opener clause when >= 40
# characters remain, and the tokens 'Both memories', 'both notes', 'Memory A/B', 'the two memories', '#1234'. 273 of 312
# eligible dreams on the owner's copy opened 'Both memories ...', which pulled every dream toward one boilerplate
# direction. Measured with the REAL channel at the absolute 0.50 floor: endpoint recall 153 -> 197 of 312 (gate-style 20:
# 9 -> 15), generic showings 9 -> 13 of 250 queries, 6 of the 7 added showings judged relevant
# (iterations/scratch_records/r2_w2a/emb/). Replicated by the Round 2 reflector on a later copy (319 dreams, 250 fresh
# real queries): endpoint recall 153 -> 199; generic showings 17 -> 17 of 250; of the 6 showings F2 adds 4 are relevant,
# of the 6 it removes 3 are (net +1 relevant, -1 noise). The adoption rests on the endpoint gain and a noise count that
# does not rise; an 80% added-relevance bar over ~6 items is not stable (6/7, then 4/6). (The adoption rule was written
# after run 1's data; the replication is the measurement.) Only the VECTOR's text changes: the stored content, the
# summary and the FTS body are untouched.
_FRAME_TOKENS = tuple(re.compile(p, re.I) for p in (r"\bBoth memories\b", r"\bboth notes\b", r"\bMemory [AB](?:'s)?\b",
                                                      r"\bthe two memories\b", r"#\d+"))
_OPENER_RE = re.compile(r"^\s*Both memories[^.:\u2014]{0,160}?(?::|\s\u2014\s)\s*(.+)$", re.S)


def _strip_frame_tokens(text: str) -> str:
    for rx in _FRAME_TOKENS:
        text = rx.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def embedding_text(body: str) -> str:
    """The text a dream's vector is computed from (see the block above). Falls back to the body when cleaning would
    leave nothing."""
    b = (body or "").strip()
    m = _OPENER_RE.match(b)
    out = _strip_frame_tokens(m.group(1)) if (m and len(m.group(1).strip()) >= 40) else _strip_frame_tokens(b)
    return out or b


def summarize_dream(body: str, meta_a: Optional[dict] = None, meta_b: Optional[dict] = None,
                    limit: int = 200) -> str:
    """The dream's FIRST body sentence, with 'Memory A/B' replaced by what each memory is about."""
    text = (body or "").strip()
    text = re.sub(r"^RELATION:[^\n]*\n", "", text, flags=re.I).strip()
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""
    subj = {"A": _subject(meta_a, "one memory"), "B": _subject(meta_b, "another memory")}
    first = _MEMORY_AB_RE.sub(lambda m: subj[m.group(1)], first)
    first = _MEMORIES_IDS_RE.sub("two memories", first)
    first = _HASH_ID_RE.sub("", first)
    first = re.sub(r"\s+", " ", first).strip()
    if len(first) > limit:
        first = first[:limit - 1].rstrip(" ,;:-") + "…"
    return first or "A dream with no statable first sentence."


# =====================================================================================================================
# The dream row invariant (the sentinel's dream watch; `voidloom doctor` runs the same function)
# =====================================================================================================================
_FAIL_RE = re.compile(r"(\d+) LLM call\(s\) failed")


def _thinker_declined(conn: sqlite3.Connection) -> bool:
    """ROUND 2 (R2c, m4): the brain behind `conn` carries a thinker record answered "e) not now"
    (memory_dreaming.thinker_declined_on, one definition). Unreadable -> False: a missing answer stays loud."""
    try:
        path = next((r[2] for r in conn.execute("PRAGMA database_list").fetchall() if r[1] == "main"), None)
        if not path:
            return False
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import memory_dreaming as _md
        return bool(_md.thinker_declined_on(path))
    except Exception:  # noqa: BLE001
        return False


def dream_watch(conn: sqlite3.Connection, window_days: float = 2.0, now: Optional[datetime] = None,
                met_warn_days: float = 14.0) -> Dict[str, Any]:
    """ATTEMPTED pairs in the window, meaningful, stored, missing ids -> a verdict.

      attempts > 0 and a meaningful row lacks its memory id   -> BROKEN (the exact defect)
      attempts > 0 and 0 stored                               -> BROKEN (incl. a brain whose every dream is NULL)
      attempts == 0                                           -> STARVED, with the window's LLM-failure count
      else                                                    -> HEALTHY
    Backfill-tagged rows are EXCLUDED (a one-shot backfill must never forge a pass). The WARN: dreams stored for
    ``met_warn_days`` and none ever met (no ``recall:dream-lead`` row)."""
    now = now or _utcnow()
    out: Dict[str, Any] = {"organ": "dream_pipeline.phase_rem_dreaming", "window_days": window_days,
                           "now": _sql_ts(now)}
    try:
        present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dream_log'").fetchone()
    except sqlite3.Error as e:
        out.update(verdict="UNMEASURABLE", detail=f"sqlite_master unreadable: {e}")
        return out
    if not present:
        # the sentinel's own rule: an ABSENT output table is a real state (I looked; nothing), not a blind read
        out.update(verdict="BROKEN", attempts=0, stored=0,
                   detail="dream_log is ABSENT -- the dream organ's substrate was never created")
        return out
    since = _sql_ts(now - timedelta(days=window_days))
    until = _sql_ts(now)
    bf = (f"AND NOT EXISTS (SELECT 1 FROM memory_tags t WHERE t.memory_id = d.dream_memory_id "
          f"AND t.tag LIKE '{BACKFILL_TAG_PREFIX}%')") if _table_exists(conn, "memory_tags") else ""
    # LEGACY ROWS (R1-M1): rows written before this brain's FIRST fixed REM batch (its phase result carries per-pool
    # counts, '"pools"') were never the fixed code's to store. They are REPORTED (the backfill settles them), never a
    # verdict: else a landing reads BROKEN for window_days whether or not the fix works. A brain whose REM never ran
    # fixed (a host not yet upgraded) has no marker and is judged on every row.
    fix_batch = None
    if _table_exists(conn, "dream_phase_log"):
        try:
            fix_batch = conn.execute("SELECT MIN(batch_id) FROM dream_phase_log WHERE phase_name='rem_dreaming' "
                                     "AND result_json LIKE '%\"pools\"%'").fetchone()[0]
        except sqlite3.Error:
            fix_batch = None
    if fix_batch is not None:
        out["fixed_since_batch"] = int(fix_batch)
        try:
            out["legacy_meaningful_without_id"] = conn.execute(
                f"SELECT COUNT(*) FROM dream_log d WHERE created_at >= ? AND created_at <= ? {bf} AND is_meaningful=1 "
                f"AND dream_memory_id IS NULL AND (batch_id IS NULL OR batch_id < ?)",
                (since, until, int(fix_batch))).fetchone()[0]
        except sqlite3.Error:
            out["legacy_meaningful_without_id"] = None
        bf += f" AND d.batch_id >= {int(fix_batch)}"
    try:
        attempts, meaningful, stored, missing = conn.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(is_meaningful=1),0),
                       COALESCE(SUM(is_meaningful=1 AND dream_memory_id IS NOT NULL),0),
                       COALESCE(SUM(is_meaningful=1 AND dream_memory_id IS NULL),0)
                FROM dream_log d WHERE created_at >= ? AND created_at <= ? {bf}""", (since, until)).fetchone()
        by_type = {t: n for t, n in conn.execute(
            f"SELECT dream_type, COUNT(*) FROM dream_log d WHERE created_at >= ? AND created_at <= ? {bf} "
            f"GROUP BY 1", (since, until)).fetchall()}
    except sqlite3.Error as e:
        out.update(verdict="UNMEASURABLE", detail=f"dream_log unreadable: {e}")
        return out
    failures = 0
    if _table_exists(conn, "dream_phase_log"):
        try:
            for (detail,) in conn.execute(
                    "SELECT detail FROM dream_phase_log WHERE phase_name='rem_dreaming' AND created_at >= ? "
                    "AND created_at <= ?", (since, until)).fetchall():
                m = _FAIL_RE.search(detail or "")
                if m:
                    failures += int(m.group(1))
        except sqlite3.Error:
            pass
    out.update(attempts=attempts, meaningful=meaningful, stored=stored, meaningful_without_id=missing,
               by_type=by_type, llm_failures=failures)
    if attempts == 0 and _thinker_declined(conn):
        # ROUND 2 (R2c, m4): the owner answered "e) not now" -- nothing attempted BY CHOICE, never STARVED. The same
        # reading reaches `voidloom doctor`, the sentinel and `dream watch` (one function).
        out.update(verdict="UNCONFIGURED", detail=f"0 dream attempts in {window_days:g}d: the owner answered e) not "
                                                  f"now (no thinker, by choice; `voidloom thinker set <backend>` "
                                                  f"switches it on)")
    elif attempts == 0:
        out.update(verdict="STARVED", detail=f"0 dream attempts in {window_days:g}d; {failures} LLM call(s) failed "
                                             f"in the window. Nothing attempted is not the same as failing.")
    elif missing:
        out.update(verdict="BROKEN", detail=f"{missing} meaningful dream(s) in {window_days:g}d have NO memory id "
                                            f"-- the dream was had and not written down")
    elif stored == 0:
        out.update(verdict="BROKEN", detail=f"{attempts} attempt(s) in {window_days:g}d, {meaningful} meaningful, "
                                            f"0 stored ({failures} LLM failure(s))")
    else:
        out.update(verdict="HEALTHY", detail=f"{attempts} attempts / {meaningful} meaningful / {stored} stored in "
                                             f"{window_days:g}d")
    if out.get("legacy_meaningful_without_id"):
        # R1-M1: REPORTED in the one line every reader shows (the sentinel, doctor, `dream watch`), never judged
        out["detail"] += (f"; {out['legacy_meaningful_without_id']} pre-fix row(s) without an id, from before the "
                          f"first fixed batch (reported, not judged; `voidloom dream backfill` settles them)")
    # the met ledger WARN
    try:
        # R1-M1: the WARN reads the dreams the FIXED code stored (batch >= the first fixed batch); the re-stamped
        # May-June dreams are the owner's history, not a sign the channel is dead
        oldest = conn.execute(
            f"""SELECT MIN(m.created_at), COUNT(DISTINCT m.id) FROM memories m JOIN dream_log d ON d.dream_memory_id = m.id
                WHERE m.derived_from=? AND m.is_archived=0
                  {'AND d.batch_id >= %d' % int(fix_batch) if fix_batch is not None else ''}
                  AND NOT EXISTS (SELECT 1 FROM memory_tags t WHERE t.memory_id=m.id
                                  AND t.tag LIKE '{BACKFILL_TAG_PREFIX}%')""",
            (DREAM_DERIVED_FROM,)).fetchone()
        met = conn.execute("SELECT COUNT(*) FROM access_log WHERE access_type=?",
                           (MET_ACCESS_TYPE,)).fetchone()[0] if _table_exists(conn, "access_log") else 0
        odt = _parse_ts(oldest[0]) if oldest else None
        out["stored_dreams"] = int(oldest[1] or 0) if oldest else 0
        out["met_events"] = int(met)
        out["met_warning"] = bool(odt and (now - odt) >= timedelta(days=met_warn_days) and met == 0)
        if out["met_warning"]:
            out["met_detail"] = (f"WARN: dreams have been stored since {oldest[0]} "
                                 f"(>= {met_warn_days:g} days) and none was ever met in recall")
    except sqlite3.Error:
        out["met_warning"] = None
    out["surfacing_7d"] = surfacing_report(conn, now, days=7.0)
    return out


def surfacing_report(conn: sqlite3.Connection, now: Optional[datetime] = None, days: float = 7.0) -> Optional[Dict[str, Any]]:
    """THE SURFACING REPORT (owner ruling 3, VL-DF-v1.1; DREAM_FLOOR_DECISION): a REPORT over the met ledger, not a
    new instrument. One recall writes its rows with ONE (query_text, accessed_at); a recall that returned nothing
    writes none, so the denominator is recalls-with-results (stated in the key name). Also: dreams in the corpus now
    and at 7 / 14 / 28 days back (the corpus OVER TIME), and dreams ELIGIBLE under the channel's confidence gate
    (young enough, or carrying evidence) -- the population the absolute floor is applied to."""
    now = now or _utcnow()
    try:
        wk = _sql_ts(now - timedelta(days=days))
        rec = conn.execute("SELECT COUNT(DISTINCT COALESCE(query_text,'') || '|' || accessed_at) FROM access_log "
                           "WHERE accessed_at >= ? AND access_type LIKE 'recall%' AND access_type NOT LIKE '%-lead'",
                           (wk,)).fetchone()[0]
        sh = conn.execute("SELECT COUNT(DISTINCT COALESCE(query_text,'') || '|' || accessed_at), COUNT(*) FROM access_log "
                          "WHERE accessed_at >= ? AND access_type=?", (wk, MET_ACCESS_TYPE)).fetchone()
        corp = conn.execute("SELECT COUNT(*), COALESCE(SUM(created_at >= ?),0) FROM memories WHERE derived_from=? "
                            "AND is_archived=0", (_sql_ts(now - timedelta(days=HALF_LIFE_DAYS)),
                                                  DREAM_DERIVED_FROM)).fetchone()
        mem = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived=0 AND derived_from IS NULL").fetchone()[0]
        # the corpus OVER TIME: dreams that existed (had been created) 7 / 14 / 28 days ago -- an archive's own
        # date is not recorded, so a later-archived dream still counts at the dates it was live
        over_time = {}
        for back in (7, 14, 28):
            over_time["%dd_ago" % back] = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE derived_from=? AND created_at <= ?",
                (DREAM_DERIVED_FROM, _sql_ts(now - timedelta(days=back)))).fetchone()[0]
        try:
            gate = 0.20
            try:
                import recall_channels as _rc  # sibling; the registry holds the gate
                gate = float(_rc.CHANNELS["dreams"].get("confidence_gate", gate))
            except Exception:
                pass
            eligible = sum(1 for v in effective_map(conn, now=now).values() if v["effective"] >= gate)
        except Exception:
            eligible = None
        return {"window_days": days, "recalls_with_results": rec, "recalls_showing_a_dream": sh[0],
                "dreams_shown": sh[1], "shown_rate": round(sh[0] / rec, 4) if rec else None,
                "dream_corpus": corp[0], "dreams_within_half_life": corp[1],
                "dreams_eligible_under_gate": eligible, "dream_corpus_over_time": over_time,
                "authored_memories": mem, "dream_to_memory_ratio": round(corp[0] / mem, 4) if mem else None}
    except sqlite3.Error:
        return None


# =====================================================================================================================
# The re-stamp, the backfill, the playbook dedup: plan (read-only) / apply (pre-image first) / rollback
# =====================================================================================================================
def _has_vector(conn: sqlite3.Connection, table: Optional[str], ids: Sequence[int]) -> set:
    if not table or not ids:
        return set()
    q = ",".join("?" * len(ids))
    try:
        return {int(r[0]) for r in conn.execute(f"SELECT memory_id FROM {table} WHERE memory_id IN ({q})",
                                                list(ids)).fetchall()}
    except sqlite3.Error:
        return set()


def _active_table(conn: sqlite3.Connection) -> Optional[str]:
    try:
        import embed_resolver
        return embed_resolver.resolve(conn)[0]
    except Exception:
        return None


def _meta(conn: sqlite3.Connection, mid: Optional[int]) -> Optional[dict]:
    if mid is None:
        return None
    r = conn.execute("SELECT id, summary, content, is_archived, untrusted_source FROM memories WHERE id=?",
                     (int(mid),)).fetchone()
    if not r:
        return None
    return {"id": r[0], "summary": r[1], "content": r[2], "is_archived": r[3], "untrusted_source": r[4]}


def _reply_screen():
    try:
        import thinker
        return thinker.reply_screen
    except Exception:
        return None


PLAYBOOK_LABEL_PREFIX = "Playbook candidate ["


def playbook_label_key(summary: Optional[str], content: Optional[str]) -> str:
    """'Playbook candidate [<label>]' -- the key a playbook candidate is ONE of per label (R1-L3); a row without the
    prefix keys on its content (it cannot be grouped with anything it does not equal)."""
    s = summary or ""
    if s.startswith(PLAYBOOK_LABEL_PREFIX) and "]" in s:
        m = re.match(r"^(Playbook candidate \[.*\]) \(\d+x\)$", s)     # the emitter's shape: '... [label] (Nx)'
        return m.group(1) if m else s[:s.index("]") + 1]
    return "content:" + (content or "")


def plan(conn: sqlite3.Connection, days: float = 30.0, now: Optional[datetime] = None) -> Dict[str, Any]:
    """READ-ONLY. Every count derived here, at the moment of asking."""
    now = now or _utcnow()
    table = _active_table(conn)
    # --- 1. the re-stamp -------------------------------------------------------------------------------------------
    rows = conn.execute(f"SELECT id, derived_from, confidence, summary, content, created_at, is_archived "
                        f"FROM memories WHERE {RESTAMP_WHERE} ORDER BY id").fetchall()
    ids = [r[0] for r in rows]
    pairs = dream_pairs(conn, ids)
    vec = _has_vector(conn, table, ids)
    prov = set()
    if ids and _table_exists(conn, "memory_provenance"):
        q = ",".join("?" * len(ids))
        prov = {int(r[0]) for r in conn.execute(
            f"SELECT DISTINCT derived_memory_id FROM memory_provenance WHERE derived_memory_id IN ({q})", ids)}
    restamp = []
    for mid, df, conf, summ, content, created, arch in rows:
        a, b = pairs.get(mid, (None, None))
        needs = not (df == DREAM_DERIVED_FROM and conf is not None and abs(float(conf) - BIRTH_CONFIDENCE) < 1e-9
                     and mid in vec and (mid in prov or mid not in pairs))
        restamp.append({"id": mid, "derived_from": df, "confidence": conf, "summary": summ, "created_at": created,
                        "is_archived": arch, "linked": mid in pairs, "pair": [a, b] if mid in pairs else None,
                        "has_vector": mid in vec, "has_provenance": mid in prov, "needs_restamp": needs,
                        "new_summary": summarize_dream(dream_body(content), _meta(conn, a), _meta(conn, b))
                        if needs else summ})
    protected = conn.execute(
        f"SELECT COUNT(DISTINCT memory_id) FROM memory_tags WHERE tag='dream' "
        f"AND memory_id NOT IN (SELECT id FROM memories WHERE {RESTAMP_WHERE})").fetchone()[0]
    # --- 2. the backfill (the last `days` nights) ---------------------------------------------------------------
    since = _sql_ts(now - timedelta(days=days))
    cands = conn.execute(
        """SELECT id, dream_type, memory_a_id, memory_b_id, dream_content, context_difference, created_at
           FROM dream_log WHERE is_meaningful=1 AND dream_memory_id IS NULL AND created_at >= ?
             AND dream_content IS NOT NULL ORDER BY id""", (since,)).fetchall() if _table_exists(conn, "dream_log") else []
    screen = _reply_screen()
    backfill, excl_arch, excl_screen, tainted = [], [], [], 0
    for dl_id, dtype, a, b, text, ctx, created in cands:
        ma, mb = _meta(conn, a), _meta(conn, b)
        if not ma or not mb or ma["is_archived"] or mb["is_archived"]:
            excl_arch.append(dl_id)
            continue
        body = text or ""
        if screen is not None and screen(body, "text"):
            excl_screen.append(dl_id)
            continue
        taint = bool(ma["untrusted_source"] or mb["untrusted_source"])
        tainted += 1 if taint else 0
        backfill.append({"dream_log_id": dl_id, "dream_type": dtype, "a": a, "b": b, "created_at": created,
                         "tainted": taint})
    # --- 3. playbooks -------------------------------------------------------------------------------------------
    pb = conn.execute(f"SELECT id, content, access_count, derived_from, confidence, summary FROM memories "
                      f"WHERE {PLAYBOOK_WHERE} AND is_archived=0 ORDER BY id").fetchall()
    groups: Dict[str, List[Tuple[int, int, Any, Any]]] = {}
    for pid, content, acc, df, conf, summ in pb:
        # R1-L3: ONE active candidate per LABEL (the summary's 'Playbook candidate [label]' prefix), the same key
        # the nightly emission is now idempotent on -- a moved occurrence count must not keep a second variant
        groups.setdefault(playbook_label_key(summ, content), []).append((pid, acc or 0, df, conf))
    keep, archive = [], []
    for content, members in groups.items():
        members.sort(key=lambda m: (-m[1], m[0]))          # the most-met one survives; ties -> the first emitted
        k = members[0]
        keep.append({"id": k[0], "derived_from": k[2], "confidence": k[3],
                     "content_sha256": hashlib.sha256(content.encode()).hexdigest()[:16]})
        archive.extend(m[0] for m in members[1:])
    # R1-L4: the RAPTOR summaries OF the synthesis clusters (content-free noise in lane 1 as kind semantic) go with
    # the playbook archive (the same tag, the same rollback)
    raptor = [r[0] for r in conn.execute(
        "SELECT r.synthesis_memory_id FROM raptor_clusters r JOIN memories m ON m.id = r.synthesis_memory_id "
        "WHERE r.cluster_label IN ('dream-pipeline', 'memory-dreaming') AND m.is_archived = 0 "
        "ORDER BY r.synthesis_memory_id").fetchall()] if _table_exists(conn, "raptor_clusters") else []
    raptor = [x for x in dict.fromkeys(int(i) for i in raptor if i is not None)]
    archive.extend(raptor)
    # R1-L5: the playbook STAMP rides on the re-stamp (part of the fix), not on the owner's optional archive
    to_stamp = [int(r[0]) for r in pb if not (r[3] == PLAYBOOK_DERIVED_FROM and r[4] is not None
                                              and abs(float(r[4]) - BIRTH_CONFIDENCE) < 1e-9)]
    return {
        "now": _sql_ts(now), "active_table": table,
        "restamp": {"selector": RESTAMP_WHERE, "rows": len(restamp),
                    "linked": sum(1 for r in restamp if r["linked"]),
                    "unlinked_no_links_invented": sum(1 for r in restamp if not r["linked"]),
                    "already_stamped": sum(1 for r in restamp if r["derived_from"] == DREAM_DERIVED_FROM),
                    "to_restamp": sum(1 for r in restamp if r["needs_restamp"]),
                    "need_vector": sum(1 for r in restamp if not r["has_vector"]),
                    "need_provenance": sum(1 for r in restamp if r["linked"] and not r["has_provenance"]),
                    "dream_tagged_rows_outside_selector_untouched": int(protected),
                    "playbooks_to_stamp": len(to_stamp), "playbook_stamp_ids": to_stamp,
                    "items": restamp},
        "backfill": {"window_days": days, "since": since, "candidates": len(cands), "rows": len(backfill),
                     "excluded_archived_or_missing_parent": len(excl_arch),
                     "excluded_reply_screen": len(excl_screen), "tainted_carried": tainted,
                     "enqueue_scan": False, "items": backfill},
        "playbooks": {"selector": PLAYBOOK_WHERE, "active_rows": len(pb), "distinct_labels": len(groups),
                      "distinct_texts": len({r[1] for r in pb}), "kept": len(keep),
                      "raptor_summaries_of_synthesis": len(raptor), "archive": len(archive), "keep_items": keep,
                      "archive_ids": archive},
    }


def _encoder_for(conn: sqlite3.Connection, encoder: Optional[Callable[[str], bytes]]):
    if encoder is not None:
        return encoder
    import memory_write
    return lambda text: memory_write.default_encoder(text, conn)


def apply(conn: sqlite3.Connection, the_plan: Dict[str, Any], parts: Sequence[str], preimage_path: str,
          encoder: Optional[Callable[[str], bytes]] = None, stamp_date: Optional[str] = None) -> Dict[str, Any]:
    """Apply the named ``parts`` (restamp / backfill / playbooks) of a plan. The PRE-IMAGE is written and fsynced
    BEFORE the first write. Never deletes a memory; never enqueues a belief scan."""
    parts = [p for p in parts if p in ("restamp", "backfill", "playbooks")]
    if not parts:
        return {"status": "error", "error": "no_part", "message": "name --restamp, --backfill and/or --playbooks"}
    stamp_date = stamp_date or _utcnow().strftime("%Y%m%d")
    bf_tag = BACKFILL_TAG_PREFIX + stamp_date
    pb_tag = PLAYBOOK_DEDUP_TAG_PREFIX + stamp_date
    q0 = conn.execute("SELECT COUNT(*) FROM belief_scan_queue").fetchone()[0] if _table_exists(
        conn, "belief_scan_queue") else None
    pre: Dict[str, Any] = {"written_at": _sql_ts(_utcnow()), "parts": parts, "backfill_tag": bf_tag,
                           "playbook_tag": pb_tag, "belief_scan_queue_rows_before": q0,
                           "restamp": [], "backfill_dream_log_ids": [], "playbook_archived_ids": [],
                           "playbook_kept": []}
    if "restamp" in parts:
        # only the rows that NEED it: a dream this series stored is already born stamped, and a re-run of the verb
        # after new dreams exist must neither touch them nor record them as "restored" on a rollback
        pre["restamp"] = [{k: it[k] for k in ("id", "derived_from", "confidence", "summary", "has_vector",
                                              "has_provenance")}
                          for it in the_plan["restamp"]["items"] if it.get("needs_restamp", True)]
        pre["active_table"] = the_plan.get("active_table")
        stamp_ids = list(the_plan["restamp"].get("playbook_stamp_ids") or [])
        pre["playbook_stamped"] = []
        for i in range(0, len(stamp_ids), 500):
            chunk = stamp_ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            pre["playbook_stamped"].extend(
                {"id": r[0], "derived_from": r[1], "confidence": r[2]} for r in conn.execute(
                    f"SELECT id, derived_from, confidence FROM memories WHERE id IN ({q}) ORDER BY id", chunk))
    if "playbooks" in parts:
        pre["playbook_archived_ids"] = list(the_plan["playbooks"]["archive_ids"])
        pre["playbook_kept"] = [{k: it[k] for k in ("id", "derived_from", "confidence")}
                                for it in the_plan["playbooks"]["keep_items"]]
    if "backfill" in parts:
        pre["backfill_dream_log_ids"] = [it["dream_log_id"] for it in the_plan["backfill"]["items"]]
    Path(preimage_path).parent.mkdir(parents=True, exist_ok=True)
    with open(preimage_path, "w", encoding="utf-8") as fh:
        json.dump(pre, fh, indent=1, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    res: Dict[str, Any] = {"status": "ok", "preimage": preimage_path, "parts": parts}
    import memory_write
    enc = None
    # ---- restamp ----------------------------------------------------------------------------------------------
    if "restamp" in parts:
        n_upd = n_vec = n_vec_fail = n_prov = 0
        for it in the_plan["restamp"]["items"]:
            if not it.get("needs_restamp", True):
                continue
            conn.execute("UPDATE memories SET derived_from=?, confidence=?, summary=? WHERE id=?",
                         (DREAM_DERIVED_FROM, BIRTH_CONFIDENCE, it["new_summary"], it["id"]))
            n_upd += 1
            if it["linked"] and not it["has_provenance"] and it["pair"] and None not in it["pair"]:
                ids = memory_write._record_provenance(conn, it["id"], it["pair"], "dream_synthesis",
                                                      "dream_ledger.restamp", 0.8, None)
                n_prov += 1 if ids else 0
            if not it["has_vector"]:
                if enc is None:
                    enc = _encoder_for(conn, encoder)
                content = conn.execute("SELECT content FROM memories WHERE id=?", (it["id"],)).fetchone()[0]
                # the BODY, as the store embeds it (memory_dreaming: "the header is provenance, not meaning") -- the
                # '[Dream synthesis ...]' / 'Source memories: #a and #b' header must not pull every re-stamped dream
                # toward the same boilerplate direction (wave 2A's endpoint-embedding measurement, class sweep)
                r = memory_write.attach_embedding(conn, it["id"], embedding_text(dream_body(content)), encoder=enc,
                                                  policy="best_effort", organ="dream_ledger.restamp")
                n_vec += 1 if r.get("embedded") else 0
                n_vec_fail += 0 if r.get("embedded") else 1
        # R1-L5: every active playbook candidate is stamped here (the playbooks channel is keyed on the stamp)
        n_pb = 0
        for it in pre.get("playbook_stamped") or []:
            conn.execute("UPDATE memories SET derived_from=?, confidence=? WHERE id=?",
                         (PLAYBOOK_DERIVED_FROM, BIRTH_CONFIDENCE, it["id"]))
            n_pb += 1
        conn.commit()
        res["restamp"] = {"updated": n_upd, "provenance_written_for": n_prov, "vectors_added": n_vec,
                          "vector_failures": n_vec_fail, "playbooks_stamped": n_pb}
    # ---- backfill ---------------------------------------------------------------------------------------------
    if "backfill" in parts:
        import memory_dreaming
        n_ok = n_fail = 0
        for it in the_plan["backfill"]["items"]:
            row = conn.execute("SELECT dream_content, context_difference, dream_memory_id FROM dream_log WHERE id=?",
                               (it["dream_log_id"],)).fetchone()
            if not row or row[2] is not None:
                continue
            if enc is None:
                enc = _encoder_for(conn, encoder)
            relation = None
            try:
                relation = (json.loads(row[1]) or {}).get("relation") if row[1] else None
            except Exception:
                relation = None
            rep: Dict[str, Any] = {}
            mid = memory_dreaming._store_dream_as_reflection(
                conn, row[0], it["a"], it["b"], it["dream_type"], relation=relation, encoder=enc,
                enqueue_scan=False, created_at=it["created_at"], extra_tags=(bf_tag,), report=rep)
            if mid:
                conn.execute("UPDATE dream_log SET dream_memory_id=? WHERE id=?", (mid, it["dream_log_id"]))
                n_ok += 1
            else:
                n_fail += 1
        conn.commit()
        res["backfill"] = {"stored": n_ok, "failed": n_fail, "tag": bf_tag}
    # ---- playbooks --------------------------------------------------------------------------------------------
    if "playbooks" in parts:
        arch = list(the_plan["playbooks"]["archive_ids"])
        for i in range(0, len(arch), 500):
            chunk = arch[i:i + 500]
            q = ",".join("?" * len(chunk))
            conn.execute(f"UPDATE memories SET is_archived=1 WHERE id IN ({q})", chunk)
            conn.executemany("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                             [(x, pb_tag) for x in chunk])
        # --playbooks archives duplicates ONLY (R1-L5): the stamp is the re-stamp's
        conn.commit()
        res["playbooks"] = {"archived": len(arch), "kept": len(the_plan["playbooks"]["keep_items"]),
                            "raptor_summaries_archived": the_plan["playbooks"].get("raptor_summaries_of_synthesis", 0),
                            "tag": pb_tag}
    q1 = conn.execute("SELECT COUNT(*) FROM belief_scan_queue").fetchone()[0] if q0 is not None else None
    res["belief_scan_queue_rows"] = {"before": q0, "after": q1}
    return res


def rollback(conn: sqlite3.Connection, preimage: Dict[str, Any]) -> Dict[str, Any]:
    """EXECUTED rollback: archive the backfill by tag, restore every re-stamped field from the pre-image, un-archive
    the playbook duplicates and restore the kept playbooks' fields. Never deletes a memory."""
    out: Dict[str, Any] = {"status": "ok"}
    tag = preimage.get("backfill_tag")
    if tag and preimage.get("backfill_dream_log_ids") is not None and "backfill" in preimage.get("parts", []):
        ids = [r[0] for r in conn.execute("SELECT memory_id FROM memory_tags WHERE tag=?", (tag,)).fetchall()]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            conn.execute(f"UPDATE memories SET is_archived=1 WHERE id IN ({q})", chunk)
            conn.execute(f"UPDATE dream_log SET dream_memory_id=NULL WHERE dream_memory_id IN ({q})", chunk)
        out["backfill_archived"] = len(ids)
    n = n_vec = n_prov = 0
    table = preimage.get("active_table")
    for it in preimage.get("restamp") or []:
        conn.execute("UPDATE memories SET derived_from=?, confidence=?, summary=? WHERE id=?",
                     (it["derived_from"], it["confidence"], it["summary"], it["id"]))
        # R1-M7: the derived index rows THIS verb wrote are removed, so a rolled-back brain exposes no more
        # unlabelled dreams than before the apply (no memory row is deleted: archive-never-delete holds)
        if it.get("has_vector") is False and table and re.fullmatch(r"[a-z0-9_]+", table):
            n_vec += conn.execute(f"DELETE FROM {table} WHERE memory_id=?", (it["id"],)).rowcount or 0
            conn.execute("DELETE FROM embedding_provenance WHERE memory_id=? AND vec_table=?", (it["id"], table))
        if it.get("has_provenance") is False:
            n_prov += conn.execute("DELETE FROM memory_provenance WHERE derived_memory_id=? "
                                   "AND agent='dream_ledger.restamp'", (it["id"],)).rowcount or 0
        n += 1
    out["restamp_restored"] = n
    out["restamp_vectors_removed"] = n_vec
    out["restamp_provenance_rows_removed"] = n_prov
    for it in preimage.get("playbook_stamped") or []:
        conn.execute("UPDATE memories SET derived_from=?, confidence=? WHERE id=?",
                     (it["derived_from"], it["confidence"], it["id"]))
    out["playbooks_stamp_restored"] = len(preimage.get("playbook_stamped") or [])
    arch = preimage.get("playbook_archived_ids") or []
    ptag = preimage.get("playbook_tag")
    for i in range(0, len(arch), 500):
        chunk = arch[i:i + 500]
        q = ",".join("?" * len(chunk))
        conn.execute(f"UPDATE memories SET is_archived=0 WHERE id IN ({q})", chunk)
        if ptag:
            conn.execute(f"DELETE FROM memory_tags WHERE tag=? AND memory_id IN ({q})", (ptag, *chunk))
    out["playbooks_unarchived"] = len(arch)
    for it in preimage.get("playbook_kept") or []:
        conn.execute("UPDATE memories SET derived_from=?, confidence=? WHERE id=?",
                     (it["derived_from"], it["confidence"], it["id"]))
    out["playbooks_kept_restored"] = len(preimage.get("playbook_kept") or [])
    conn.commit()
    return out


# =====================================================================================================================
# The verb: `voidloom dream <sub>` (ORGAN_VERBS: run(argv) -> envelope)
# =====================================================================================================================
def _resolve_db(explicit: Optional[str]) -> str:
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get("WIZARD_LIBRARY_DB") or os.environ.get("CLAUDE_MEMORY_DB")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return str(Path(__file__).resolve().parent.parent / "claude_memory.db")


def _connect(db: str, ro: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True) if ro else sqlite3.connect(db, timeout=60.0)
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass
    return conn


def _env(status: str, data: Dict[str, Any], message: str = "", code: str = "") -> Dict[str, Any]:
    e = {"status": status, "verb": "dream", "data": data}
    if message:
        e["message"] = message
    if code:
        e["code"] = code
    return e


def _summarise_plan(p: Dict[str, Any]) -> Dict[str, Any]:
    s = json.loads(json.dumps(p, default=str))
    s["restamp"].pop("items", None)
    s["backfill"].pop("items", None)
    s["playbooks"].pop("keep_items", None)
    s["playbooks"].pop("archive_ids", None)
    return s


def run(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    ap = argparse.ArgumentParser(prog="voidloom dream")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="sub")
    b = sub.add_parser("backfill")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", default=None, metavar="PREIMAGE")
    b.add_argument("--restamp", action="store_true")
    b.add_argument("--backfill", action="store_true")
    b.add_argument("--playbooks", action="store_true")
    b.add_argument("--days", type=float, default=30.0)
    b.add_argument("--preimage", default=None)
    b.add_argument("--full", action="store_true", help="include the per-row items in the plan output")
    b.add_argument("--json", action="store_true", dest="json2")
    b.add_argument("--db", default=None, dest="db2")
    c = sub.add_parser("confirm")
    c.add_argument("dream_id", type=int)
    c.add_argument("--json", action="store_true", dest="json2")
    c.add_argument("--db", default=None, dest="db2")
    s = sub.add_parser("show")
    s.add_argument("dream_id", type=int)
    s.add_argument("--json", action="store_true", dest="json2")
    s.add_argument("--db", default=None, dest="db2")
    w = sub.add_parser("watch")
    w.add_argument("--window-days", type=float, default=2.0)
    w.add_argument("--json", action="store_true", dest="json2")
    w.add_argument("--db", default=None, dest="db2")
    try:
        a = ap.parse_args(list(argv or []))
    except SystemExit:
        return _env("error", {}, "usage: voidloom dream backfill --plan|--apply --restamp|--backfill|--playbooks|"
                                  "--rollback PREIMAGE ; confirm <id> ; show <id> ; watch", "usage")
    db = _resolve_db(getattr(a, "db2", None) or a.db)
    if not os.path.exists(db):
        return _env("error", {"db": db}, f"brain not found: {db}", "brain_missing")
    if a.sub == "watch":
        conn = _connect(db, ro=True)
        try:
            return _env("ok", dream_watch(conn, a.window_days))
        finally:
            conn.close()
    if a.sub == "show":
        conn = _connect(db, ro=True)
        try:
            row = conn.execute("SELECT id, content, summary, created_at, derived_from, COALESCE(untrusted_source, 0) "
                               "FROM memories WHERE id=?", (a.dream_id,)).fetchone()
            if not row or row[4] != DREAM_DERIVED_FROM:
                return _env("error", {"dream_id": a.dream_id}, "not a dream", "not_a_dream")
            eff = effective_map(conn, [a.dream_id]).get(a.dream_id, {})
            import recall_channels
            frame = recall_channels.dream_frame(row[3], eff.get("effective"))
            # R1-L8: model text (which may echo web text) never reaches a terminal or an agent unsanitised
            return _env("ok", {"dream_id": row[0], "provenance": "dream", "frame": frame,
                               "content": frame + "\n" + recall_channels._sanitize(dream_body(row[1])),
                               "summary": recall_channels._sanitize(row[2]), "untrusted_source": int(row[5] or 0),
                               "confidence": eff.get("effective"), "birth_confidence": eff.get("birth"),
                               "evidence": eff.get("evidence"), "created_at": row[3],
                               "pair": dream_pairs(conn, [row[0]]).get(row[0])})
        finally:
            conn.close()
    if a.sub == "confirm":
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return _env("error", {"dream_id": a.dream_id},
                        "dream confirm is the OWNER's word and needs a terminal; an agent cannot confirm a dream",
                        "needs_terminal")
        conn = _connect(db, ro=False)
        try:
            r = confirm(conn, a.dream_id)
            return _env("ok" if r.get("status") == "ok" else "error", r, r.get("message", ""))
        finally:
            conn.close()
    if a.sub == "backfill":
        if a.plan:
            conn = _connect(db, ro=True)
            try:
                p = plan(conn, days=a.days)
            finally:
                conn.close()
            return _env("ok", p if a.full else _summarise_plan(p))
        if a.rollback:
            pre = json.load(open(a.rollback, encoding="utf-8"))
            conn = _connect(db, ro=False)
            try:
                return _env("ok", rollback(conn, pre))
            finally:
                conn.close()
        parts = [n for n, f in (("restamp", a.restamp), ("backfill", a.backfill), ("playbooks", a.playbooks)) if f]
        if not parts:
            return _env("error", {}, "--apply needs at least one part: --restamp, --backfill, --playbooks", "no_part")
        pre_path = a.preimage or str(Path(db).parent / f"dream_backfill_preimage_{_utcnow():%Y%m%dT%H%M%SZ}.json")
        conn = _connect(db, ro=False)
        try:
            p = plan(conn, days=a.days)
            r = apply(conn, p, parts, pre_path)
        finally:
            conn.close()
        r["plan"] = _summarise_plan(p)
        return _env("ok" if r.get("status") == "ok" else "error", r)
    return _env("error", {}, "usage: voidloom dream backfill|confirm|show|watch", "usage")


def main() -> int:
    env = run(sys.argv[1:])
    print(json.dumps(env, indent=2, default=str))
    return 0 if env.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

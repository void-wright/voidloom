#!/usr/bin/env python3
"""recall_channels.py -- CHANNELLED RECALL: memories in ``results`` exactly as today, synthetic rows in their OWN
labelled channels beside them, with NO model call and no reranker work.

THE OWNER'S DESIGN (the dream fix, Tranche A, A3). One ranked list was the defect: a dream competing with the
recorded memories it was dreamed about either outranked them (confidence 1.0 x a 3.6 lens boost) or never appeared
(stored dreams took a slot in 14 of 4,518 real recalls). So a synthetic row's PROVENANCE decides its channel,
relevance orders it inside that channel, and no channel's ranking crosses into another's:

  * rows of every channel that is ON, or OFF with ``when_off='hidden'``, are PARTITIONED OUT of the memory pool
    after RRF fusion and BEFORE the reranker (hybrid_retrieval.recall_hybrid ``divert=``), so the reranker scores
    FEWER pairs than before, never more; ``results`` stays memories only;
  * each ON channel is filled from what recall already computed -- the SAME query vector (a vec0 KNN restricted to
    the channel's rows: measured 25.5-27.9 ms at 240-3,000 ids, flat, one scan) and an FTS match over the channel's
    rows with the header vocabulary removed from the query (FTS over the BODY, never the header); the endpoint join
    to the final ``results`` is a TIE-BREAK only (anchored dreams have a median query cosine of 0.30: noise);
  * FLOOR: raw cosine >= ``cosine_min`` (``COSINE_FLOOR``, 0.50): the dream's OWN relevance (owner ruling 2,
    2026-09-28: never by out-scoring a memory); ``weakest_top5_memory_cosine`` stays in the diagnostics. The channel's
    confidence is a SEPARATE gate (>= 0.20, the faded value), never multiplied in before the floor;
  * QUOTAS: 1 by default, 2 when top_k >= 10, 3 with ``--explore``; ``--leads 0`` turns every channel off; at most
    ``TOTAL_SIDE_CAP`` (3) side rows per recall across all channels.
  * showing a row logs its channel's ``access_type`` (the MET LEDGER, ``recall:dream-lead``) and never bumps
    ``access_count`` / ``last_accessed``; every learning path excludes that access type.

THE REGISTRY is ONE DICT, ``CHANNELS``. A channel = predicate + quota + frame + recipe + floor + default (+ its
confidence gate, its when-off behaviour and its met-ledger access type). A new channel (whispers are the likely next)
is a registry entry plus its battery; the code below is generic over the dict. An untracked
``<brain dir>/recall_channels.json`` may toggle ``enabled`` and ``quota`` only -- never a predicate or a frame.

Stdlib + sqlite (sqlite-vec functions when the connection has them). Fail-graceful: any channel error leaves
``results`` untouched and reports the error under ``rerank.channels``.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dream_ledger as _dl  # noqa: E402  (stdlib-only sibling; the numbers that carry a dream)

TOTAL_SIDE_CAP = 3

# THE SURFACING FLOOR -- ONE named setting (DREAM_FLOOR_DECISION, 2026-09-28, under the owner's delegation): a side
# row shows only when its raw cosine to the query is >= this ABSOLUTE value, about as close as a memory the reranker
# approves (median 0.52 on the owner's brain) -- "relevant like memories". Every channel's ``floor.cosine_min`` below
# references it; no other literal carries it, and the untracked override file cannot change it. The relative clause
# (``at_least_weakest_top5_memory``) is a knob that defaults OFF (ruling 2: a side channel that must beat the main
# channel to speak is not a separate channel); its diagnostic stays. PRE-REGISTERED REVISIT: two weeks after the host
# landing, or when active dreams double (>= 1,126), whichever is first -- re-measure 0.50 vs 0.45 on fresh queries
# (lower only if >= 80% of the added showings are judged relevant) and judge a sample of ANCHORED dreams.
COSINE_FLOOR = 0.50

# THE ONE-DICT CHANNEL REGISTRY. Order is allocation order under the total cap.
CHANNELS: Dict[str, Dict[str, Any]] = {
    "dreams": {
        "predicate": {"column": "derived_from", "equals": _dl.DREAM_DERIVED_FROM},
        "quota": {"default": 1, "top_k_ge_10": 2, "explore": 3},
        "frame": ("[dream — an unverified lead the brain synthesised on {date}, confidence {c}; "
                  "not a recorded fact]"),
        "provenance": "dream",
        "recipe": ("vector_subset", "fts_body", "endpoint_tiebreak"),
        "floor": {"cosine_min": COSINE_FLOOR, "at_least_weakest_top5_memory": False},   # owner ruling 2 (2026-09-28): a dream surfaces on its OWN relevance, never by out-scoring a memory
        "confidence_gate": 0.20,
        "default": True,
        "when_off": "hidden",           # switching dreams off never returns them to results
        "access_type": _dl.MET_ACCESS_TYPE,
        "fts_header_vocabulary": ("dream", "dreams", "synthesis", "source", "memories", "memory",
                                  "divergent", "close", "context", "persona"),
    },
    # A8: the dream pipeline's playbook candidates are EXCLUDED from recall until validated. Declared OFF with
    # when_off 'hidden': the channel exists so the exclusion is one registry fact, not a special case in cmd_recall.
    # Why not served as leads: their text is a content-free template ("A repeated procedural pattern was observed in
    # [X] across N procedural memories") -- nothing a reader can act on -- and 197 near-identical rows would burn the
    # side-channel cap; their path to use is skill_graduation behind the 0.6 evidence bar, not recall.
    "playbooks": {
        "predicate": {"column": "derived_from", "equals": _dl.PLAYBOOK_DERIVED_FROM},
        "quota": {"default": 0, "top_k_ge_10": 0, "explore": 0},
        "frame": ("[playbook candidate — an unvalidated pattern the brain noticed on {date}, confidence {c}; "
                  "not a recorded procedure]"),
        "provenance": "playbook",
        "recipe": ("vector_subset", "fts_body"),
        "floor": {"cosine_min": COSINE_FLOOR, "at_least_weakest_top5_memory": False},
        "confidence_gate": 0.20,
        "default": False,
        "when_off": "hidden",
        "access_type": "recall:playbook-lead",
        "fts_header_vocabulary": ("playbook", "candidate", "synthesis", "unvalidated", "dream"),
    },
    # Declared OFF (owner #22536): the likely next channel. OFF with when_off 'lane1' so the whisper DIGESTS stay
    # ordinary memories until the channel passes its own falsifier battery and is switched on by config.
    "whispers": {
        "predicate": {"column": "derived_from", "equals": "whisper_synthesis"},
        "quota": {"default": 1, "top_k_ge_10": 1, "explore": 2},
        "frame": ("[whisper digest — the brain's own in-session observations, digested on {date}, confidence {c}; "
                  "not a recorded fact]"),
        "provenance": "whisper",
        "recipe": ("vector_subset", "fts_body"),
        "floor": {"cosine_min": COSINE_FLOOR, "at_least_weakest_top5_memory": False},
        "confidence_gate": 0.20,
        "default": False,
        "when_off": "lane1",
        "access_type": "recall:whisper-lead",
        "fts_header_vocabulary": ("whisper", "digest", "synthesis"),
    },
}

_OVERRIDE_BASENAME = "recall_channels.json"
_OVERRIDABLE = ("enabled", "quota")


def registry(brain_dir: Optional[str] = None,
             base: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """The effective registry: ``CHANNELS`` (or ``base``) plus an untracked override that may set ``enabled`` and
    ``quota`` ONLY. Every channel gets ``enabled`` (= its ``default`` unless overridden)."""
    reg = json.loads(json.dumps(base if base is not None else CHANNELS))
    for spec in reg.values():
        spec["enabled"] = bool(spec.get("default"))
    if brain_dir:
        p = Path(brain_dir) / _OVERRIDE_BASENAME
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            for name, ov in data.items():
                if name in reg and isinstance(ov, dict):
                    for k in _OVERRIDABLE:
                        if k in ov:
                            if k == "enabled":
                                reg[name]["enabled"] = bool(ov[k])
                            elif isinstance(ov[k], (int, dict)):
                                reg[name]["quota"] = (ov[k] if isinstance(ov[k], dict)
                                                      else {"default": int(ov[k]), "top_k_ge_10": int(ov[k]),
                                                            "explore": int(ov[k])})
    return reg


def _pred_sql(spec: Dict[str, Any], alias: str = "") -> Tuple[str, Tuple[Any, ...]]:
    p = spec["predicate"]
    col = p["column"]
    if not re.fullmatch(r"[a-z_]+", col):
        raise ValueError("bad predicate column %r" % col)
    return (f"{alias}{col} = ?", (p["equals"],))


def hidden_channels(reg: Dict[str, Dict[str, Any]], leads_on: bool = True) -> List[str]:
    """Channels whose rows never appear in ``results``: every ON channel (its rows go to the channel), and every OFF
    channel whose ``when_off`` is 'hidden'. ``--leads 0`` turns channels off but keeps them hidden."""
    out = []
    for name, spec in reg.items():
        on = spec.get("enabled") and leads_on
        if on or spec.get("when_off") == "hidden":
            out.append(name)
    return out


def exclude_sql(reg: Dict[str, Dict[str, Any]], leads_on: bool = True, alias: str = "") -> Tuple[str, Tuple[Any, ...]]:
    """A WHERE clause (and its args) that keeps out of a memory lane every row a recall would keep out of ``results``
    -- the rows of each hidden channel -- for the verbs that read memories WITHOUT recall's partition (Round 3 G2,
    R2-06: ``context`` listed dreams among memories and bumped their access_count). NULL-safe: a memory with no
    ``derived_from`` is never excluded. ('', ()) when no channel is hidden."""
    names = hidden_channels(reg, leads_on)
    preds, args = [], []
    for n in names:
        p = reg[n]["predicate"]
        if not re.fullmatch(r"[a-z_]+", p["column"]):
            raise ValueError("bad predicate column %r" % p["column"])
        preds.append(f"COALESCE({alias}{p['column']}, '') = ?")
        args.append(p["equals"])
    if not preds:
        return "", ()
    return "NOT (%s)" % " OR ".join(preds), tuple(args)


def divert_ids(conn: sqlite3.Connection, ids: Sequence[int], reg: Dict[str, Dict[str, Any]],
               leads_on: bool = True) -> Set[int]:
    """The ids among ``ids`` that belong to a hidden channel. One SELECT."""
    ids = [int(i) for i in ids if i is not None]
    names = hidden_channels(reg, leads_on)
    if not ids or not names:
        return set()
    preds, args = [], []
    for n in names:
        s, a = _pred_sql(reg[n])
        preds.append(s)
        args.extend(a)
    q = ",".join("?" * len(ids))
    try:
        return {int(r[0]) for r in conn.execute(
            f"SELECT id FROM memories WHERE id IN ({q}) AND ({' OR '.join(preds)})", (*ids, *args)).fetchall()}
    except sqlite3.Error:
        # R2-08 (Round 3 G2): never fail OPEN -- a registry predicate the brain cannot answer still keeps the two
        # synthesis origins out of the memory lane; only a brain that cannot answer even that returns nothing.
        return synthesis_ids(conn, ids)


SYNTHESIS_ORIGINS = (_dl.DREAM_DERIVED_FROM, _dl.PLAYBOOK_DERIVED_FROM)


def synthesis_ids(conn: sqlite3.Connection, ids: Sequence[int]) -> Set[int]:
    """The FAIL-CLOSED partition (R2-08): the ids among ``ids`` whose origin is a dream or a playbook candidate --
    the rows that never belong in a memory lane, whatever the registry says. One SELECT; empty on a brain without
    ``derived_from`` or one that cannot answer."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return set()
    q = ",".join("?" * len(ids))
    try:
        return {int(r[0]) for r in conn.execute(
            f"SELECT id FROM memories WHERE id IN ({q}) AND derived_from IN (?, ?)", (*ids, *SYNTHESIS_ORIGINS))}
    except sqlite3.Error:
        return set()


def make_divert(conn: sqlite3.Connection, reg: Dict[str, Dict[str, Any]], leads_on: bool = True):
    """A callable for hybrid_retrieval.recall_hybrid(divert=...)."""
    return lambda ids: divert_ids(conn, ids, reg, leads_on)


def quota_for(spec: Dict[str, Any], top_k: int, explore: bool, leads: Optional[int]) -> int:
    if leads is not None:
        if leads <= 0:
            return 0
        return min(int(leads), TOTAL_SIDE_CAP)
    q = spec.get("quota") or {}
    if explore:
        return int(q.get("explore", 0))
    if top_k >= 10:
        return int(q.get("top_k_ge_10", 0))
    return int(q.get("default", 0))


def dream_frame(created_at: Any, confidence: Any) -> str:
    return frame_for(CHANNELS["dreams"], created_at, confidence)


def frame_for(spec: Dict[str, Any], created_at: Any, confidence: Any) -> str:
    date = str(created_at or "")[:10] or "an unknown date"
    try:
        c = "%.2f" % float(confidence)
    except (TypeError, ValueError):
        c = "unknown"
    return spec["frame"].format(date=date, c=c)


def _sanitize(text: Any) -> str:
    try:
        import void_render  # type: ignore
        return void_render.sanitize_for_terminal(text)
    except Exception:
        t = "" if text is None else str(text)
        return "".join(ch for ch in t if ch == "\t" or ord(ch) >= 0x20)


def _fts_query(query: str, vocabulary: Iterable[str]) -> str:
    """The query with the channel's HEADER vocabulary removed, so FTS can only match the BODY's words."""
    voc = {v.lower() for v in vocabulary}
    toks = [t for t in re.findall(r"[A-Za-z0-9_]+", query or "") if t.lower() not in voc and len(t) > 1]
    return " OR ".join(f'"{t}"' for t in toks[:24])


def _cosines(conn: sqlite3.Connection, table: str, query_vec: bytes, ids: Sequence[int]) -> Dict[int, float]:
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    try:
        return {int(m): 1.0 - float(d) for m, d in conn.execute(
            f"SELECT memory_id, vec_distance_cosine(embedding, ?) FROM {table} WHERE memory_id IN ({q})",
            (query_vec, *ids)).fetchall() if d is not None}
    except sqlite3.Error:
        return {}


def _active_table(conn: sqlite3.Connection) -> Optional[str]:
    try:
        import embed_resolver
        return embed_resolver.resolve(conn)[0]
    except Exception:
        return None


def _channel_rows(conn: sqlite3.Connection, spec: Dict[str, Any]) -> List[Tuple[int, Any, Any]]:
    s, a = _pred_sql(spec)
    return conn.execute(f"SELECT id, confidence, created_at FROM memories WHERE is_archived=0 AND {s}",
                        a).fetchall()


def _channel_rows_aged(conn: sqlite3.Connection, spec: Dict[str, Any]) -> List[Tuple[int, Any, Any, float]]:
    """(id, birth, created_at, age_days) for rows that CAN clear the gate: younger than the age at which the birth
    value fades to the gate, or carrying any evidence (confirm tag / answer edge / approved add_contradicts proposal on
    the dream's own pair). The age is computed by SQLite (julianday), not by a Python strptime per row. (R1-L6: the
    per-recall scan then grows with ELIGIBLE rows, not with the whole corpus.)"""
    s, a = _pred_sql(spec)
    gate = max(float(spec.get("confidence_gate", 0.0)), 1e-9)
    max_age = _dl.HALF_LIFE_DAYS * max(0.0, math.log2(_dl.BIRTH_CONFIDENCE / gate)) + 1.0
    st = ",".join("?" * len(_dl.APPROVED_PROPOSAL_STATUSES))
    ev_pair = (f"OR id IN (SELECT d.dream_memory_id FROM dream_log d JOIN edge_proposals p "
               f"ON ((p.source_id=d.memory_a_id AND p.target_id=d.memory_b_id) "
               f"OR (p.source_id=d.memory_b_id AND p.target_id=d.memory_a_id)) "
               f"WHERE p.status IN ({st}) AND p.proposal_kind = 'add_contradicts' AND d.dream_memory_id IS NOT NULL)")
    try:
        return conn.execute(
            f"SELECT id, confidence, created_at, julianday('now') - julianday(created_at) FROM memories "
            f"WHERE is_archived=0 AND {s} AND (julianday('now') - julianday(created_at) <= ? "
            f"OR id IN (SELECT memory_id FROM memory_tags WHERE tag=?) "
            f"OR id IN (SELECT target_id FROM memory_edges WHERE predicate='refines' AND valid_until IS NULL AND metadata LIKE ?) {ev_pair})",
            (*a, max_age, _dl.CONFIRM_TAG, "%" + _dl.ANSWER_ORIGIN + "%", *_dl.APPROVED_PROPOSAL_STATUSES)).fetchall()
    except sqlite3.OperationalError:
        # a brain without dream_log / edge_proposals: the age bound and the tag/edge evidence only
        return conn.execute(
            f"SELECT id, confidence, created_at, julianday('now') - julianday(created_at) FROM memories "
            f"WHERE is_archived=0 AND {s} AND (julianday('now') - julianday(created_at) <= ? "
            f"OR id IN (SELECT memory_id FROM memory_tags WHERE tag=?) "
            f"OR id IN (SELECT target_id FROM memory_edges WHERE predicate='refines' AND valid_until IS NULL AND metadata LIKE ?))",
            (*a, max_age, _dl.CONFIRM_TAG, "%" + _dl.ANSWER_ORIGIN + "%")).fetchall()


def _eligible_dreams(conn: sqlite3.Connection, spec: Dict[str, Any], now: datetime) -> Dict[int, Dict[str, Any]]:
    """Live dreams whose DERIVED confidence clears the gate and whose known endpoints are both live."""
    rows = _channel_rows_aged(conn, spec)
    if not rows:
        return {}
    ids = [r[0] for r in rows]
    faded = {int(r[0]): (r[1], r[2], _dl.faded_from_age(r[1], r[3])) for r in rows}
    # evidence is rare: find the dreams that have ANY, then count only those
    ev_ids: Set[int] = set()
    q = ",".join("?" * len(ids))
    try:
        ev_ids |= {int(x) for (x,) in conn.execute(
            f"SELECT memory_id FROM memory_tags WHERE tag=? AND memory_id IN ({q})", (_dl.CONFIRM_TAG, *ids))}
        ev_ids |= {int(x) for (x,) in conn.execute(
            f"SELECT DISTINCT target_id FROM memory_edges WHERE predicate='refines' AND target_id IN ({q}) AND valid_until IS NULL "
            f"AND metadata LIKE '%{_dl.ANSWER_ORIGIN}%'", ids)}
        st = ",".join("?" * len(_dl.APPROVED_PROPOSAL_STATUSES))
        ev_ids |= {int(x) for (x,) in conn.execute(
            f"""SELECT DISTINCT d.dream_memory_id FROM dream_log d JOIN edge_proposals p
                ON ((p.source_id=d.memory_a_id AND p.target_id=d.memory_b_id)
                 OR (p.source_id=d.memory_b_id AND p.target_id=d.memory_a_id))
                WHERE p.status IN ({st}) AND p.proposal_kind = 'add_contradicts' AND d.dream_memory_id IN ({q})""",
            (*_dl.APPROVED_PROPOSAL_STATUSES, *ids))}
    except sqlite3.Error:
        pass
    ev = _dl.evidence_counts(conn, sorted(ev_ids)) if ev_ids else {}
    gate = float(spec.get("confidence_gate", 0.0))
    pairs = _dl.dream_pairs(conn, ids)
    ends = sorted({e for p in pairs.values() for e in p if e is not None})
    archived: Set[int] = set()
    if ends:
        q2 = ",".join("?" * len(ends))
        live = {int(x) for (x,) in conn.execute(
            f"SELECT id FROM memories WHERE id IN ({q2}) AND is_archived=0", ends)}
        archived = set(ends) - live
    out: Dict[int, Dict[str, Any]] = {}
    for mid, (birth, created, fd) in faded.items():
        n_ev = ev.get(mid, {}).get("total", 0)
        eff = round(min(_dl.CAP, fd + _dl.EVIDENCE_STEP * n_ev), 4)
        if eff < gate:
            continue
        pair = pairs.get(mid)
        if pair and any(e in archived for e in pair if e is not None):
            continue                                    # an archived endpoint never shows
        out[mid] = {"birth": birth, "created_at": created, "effective": eff, "evidence": ev.get(mid),
                    "pair": pair}
    return out


def build(conn: sqlite3.Connection, *, query: str, query_vec: Optional[bytes], results: List[Dict[str, Any]],
          top_k: int, leads: Optional[int] = None, explore: bool = False,
          reg: Optional[Dict[str, Dict[str, Any]]] = None,
          now: Optional[datetime] = None) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """Fill every ON channel. Returns ({channel_key: [rows]}, diagnostics). No model call; no reranker."""
    reg = reg if reg is not None else registry()
    now = now or _dl._utcnow()
    out: Dict[str, List[Dict[str, Any]]] = {}
    diag: Dict[str, Any] = {"total_side_cap": TOTAL_SIDE_CAP}
    leads_on = not (leads is not None and leads <= 0)
    if not leads_on:
        diag["off"] = "--leads 0"
        return out, diag
    table = _active_table(conn)
    # the top 5 BY RANK (score), never positions [:5] of the Lost-in-the-Middle-placed list (R1-L12)
    lane1_ids = [int(r["id"]) for r in sorted((r for r in results if isinstance(r, dict) and "id" in r),
                                              key=lambda r: -float(r.get("score", 0.0) or 0.0))][:5]
    remaining = TOTAL_SIDE_CAP
    for name, spec in reg.items():
        if not spec.get("enabled"):
            continue
        t0 = time.perf_counter()
        d: Dict[str, Any] = {}
        diag[name] = d
        out[name] = []
        quota = min(quota_for(spec, top_k, explore, leads), remaining)
        d["quota"] = quota
        if quota <= 0:
            continue
        try:
            elig = (_eligible_dreams(conn, spec, now) if spec.get("provenance") == "dream"
                    else {int(r[0]): {"birth": r[1], "created_at": r[2], "effective": r[1], "pair": None,
                                      "evidence": None} for r in _channel_rows(conn, spec)
                          if (r[1] if r[1] is not None else 1.0) >= float(spec.get("confidence_gate", 0))})
            d["eligible"] = len(elig)
            if not elig:
                continue
            eids = sorted(elig)
            cand: Dict[int, Set[str]] = {}
            if "vector_subset" in spec.get("recipe", ()) and table and query_vec is not None:
                q = ",".join("?" * len(eids))
                try:
                    for (mid, _dist) in conn.execute(
                            f"SELECT memory_id, distance FROM {table} WHERE embedding MATCH ? AND k = 10 "
                            f"AND memory_id IN ({q})", (query_vec, *eids)).fetchall():
                        cand.setdefault(int(mid), set()).add("vector")
                except sqlite3.Error as e:
                    d["vector_error"] = str(e)[:200]
            if "fts_body" in spec.get("recipe", ()):
                fq = _fts_query(query, spec.get("fts_header_vocabulary", ()))
                if fq:
                    # MEASURED on the owner's copy: `MATCH ? AND rowid IN (318 ids)` cost 231-726 ms a query (FTS5
                    # evaluates the match per listed rowid); the SAME match over the whole index, LIMIT 500, then
                    # intersected with the channel's rows, cost 15.7 ms mean / 63.4 ms max over 50 real queries.
                    eset = set(eids)
                    n = 0
                    try:
                        for (mid,) in conn.execute(
                                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? "
                                "ORDER BY bm25(memories_fts) LIMIT 500", (fq,)):
                            if int(mid) in eset:
                                cand.setdefault(int(mid), set()).add("fts")
                                n += 1
                                if n >= 10:
                                    break
                    except sqlite3.Error as e:
                        d["fts_error"] = str(e)[:200]
            d["vector_hits"] = sum(1 for v in cand.values() if "vector" in v)
            d["fts_hits"] = sum(1 for v in cand.values() if "fts" in v)
            if not cand or not table or query_vec is None:
                d["qualified"] = 0
                continue
            cos = _cosines(conn, table, query_vec, list(cand) + lane1_ids)
            lane1_cos = [cos[i] for i in lane1_ids if i in cos]
            floor = float(spec["floor"].get("cosine_min", 0.0))
            weakest = min(lane1_cos) if lane1_cos else None
            if spec["floor"].get("at_least_weakest_top5_memory") and weakest is not None:
                floor = max(floor, weakest)
            d["floor"] = round(floor, 4)
            d["weakest_top5_memory_cosine"] = None if weakest is None else round(weakest, 4)
            lane1_set = set(int(r["id"]) for r in results if isinstance(r, dict) and "id" in r)
            qualified = []
            for mid, srcs in cand.items():
                c = cos.get(mid)
                if c is None or c < floor:
                    continue
                pair = elig[mid].get("pair") or (None, None)
                anchor = next((e for e in pair if e is not None and e in lane1_set), None)
                qualified.append((round(c, 2), 1 if anchor is not None else 0, elig[mid]["effective"], -mid,
                                  mid, c, anchor, srcs))
            qualified.sort(reverse=True)
            d["qualified"] = len(qualified)
            for _c2, _anch, eff, _neg, mid, c, anchor, srcs in qualified[:quota]:
                out[name].append(_row(conn, name, spec, mid, elig[mid], c, anchor, srcs))
            remaining -= len(out[name])
        except Exception as e:  # noqa: BLE001 - a channel never breaks recall
            d["error"] = "%s: %s" % (type(e).__name__, e)
        finally:
            d["time_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            d["shown"] = len(out.get(name) or [])
    return out, diag


def _row(conn: sqlite3.Connection, name: str, spec: Dict[str, Any], mid: int, info: Dict[str, Any], cos: float,
         anchor: Optional[int], srcs: Set[str]) -> Dict[str, Any]:
    r = conn.execute("SELECT summary, created_at, COALESCE(untrusted_source, 0) FROM memories WHERE id=?",
                     (mid,)).fetchone()
    summary, created = (r[0], r[1]) if r else ("", info.get("created_at"))
    frame = frame_for(spec, created, info.get("effective"))
    row: Dict[str, Any] = {
        "id": mid, "channel": name, "provenance": spec.get("provenance"),
        "provenance_frame_reason": [spec.get("provenance")], "frame": frame,
        "content": frame + "\n" + _sanitize(summary)[:300],
        "summary": _sanitize(summary)[:300], "confidence": info.get("effective"),
        "birth_confidence": info.get("birth"), "created_at": created, "cosine": round(cos, 4),
        "anchor": anchor, "sources": sorted(srcs),
    }
    if r and r[2]:                      # R1-L8: a dream born of an untrusted endpoint says so in its own row
        row["provenance_frame_reason"].append("untrusted")
        row["untrusted_source"] = 1
    if spec.get("provenance") == "dream":
        # R1-M6: the way to settle a lead, on the package's primary store verb
        row["settle"] = "voidloom store --content '...' --category insight --answers %d" % mid
        dl = conn.execute("SELECT dream_type, context_difference, memory_a_id, memory_b_id FROM dream_log "
                          "WHERE dream_memory_id=? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
        rel = None
        if dl and dl[1]:
            try:
                rel = (json.loads(dl[1]) or {}).get("relation")
            except Exception:
                rel = None
        row["dream_type"] = dl[0] if dl else None
        row["relation"] = rel
        pair = info.get("pair")
        row["endpoints"] = list(pair) if pair else None
        if pair and anchor is not None:
            other = pair[1] if pair[0] == anchor else pair[0]
            o = conn.execute("SELECT summary FROM memories WHERE id=?", (other,)).fetchone() if other else None
            row["other"] = {"id": other, "summary": _sanitize(o[0] if o else "")[:120]}
        row["evidence"] = info.get("evidence")
    return row


def log_met(conn: sqlite3.Connection, channels: Dict[str, List[Dict[str, Any]]], query: str,
            reg: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """THE MET LEDGER: one access_log row per SHOWN side row, access_type = the channel's (``recall:dream-lead``),
    relevance NULL. Never an access_count / last_accessed bump. Read-only brains degrade (reported, not raised)."""
    reg = reg if reg is not None else CHANNELS
    n = 0
    try:
        for name, rows in channels.items():
            at = (reg.get(name) or {}).get("access_type") or f"recall:{name}-lead"
            for r in rows:
                conn.execute("INSERT INTO access_log (memory_id, access_type, query_text, relevance_score) "
                             "VALUES (?, ?, ?, NULL)", (int(r["id"]), at, query))
                n += 1
        return {"met_logged": n}
    except sqlite3.OperationalError as e:
        if "readonly" in str(e).lower() or "read-only" in str(e).lower():
            return {"met_logged": 0, "met_degraded": "read-only brain"}
        raise

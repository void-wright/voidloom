#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""governance.py — Voidloom L5-I-β: the ONE calm, Drew-gated governance gate.

THE CALM DOCTRINE (locked by council, do NOT re-litigate):

    Trust RANKS, never BARS. Governance is a calm, Drew-gated *proposal*
    through the existing ``approvals`` surface — NEVER anxious autonomous
    self-policing. The default posture is PERMISSIVE-BUT-LOGGED (calm), not
    autoimmune. A HOLD is a polite "Drew should look at this", routed to the
    surface he already reviews — never a silent write and never a silent drop.

This module is the SINGLE gate that EVERY autonomous writer routes through
(edge-commit, gap-fill, dream-integrate, web-ingest). It is deliberately ONE
gate, not per-writer gates — per-writer gates drift out of agreement with each
other; one gate is the shared, auditable seam (and the injection-defense seam
the handoff names).

    governance_gate(conn, write_intent, ...) -> {"verdict": ALLOW|HOLD|REJECT, ...}

VERDICT SEMANTICS:
  * ALLOW  — the writer proceeds. The common case. Logged when noteworthy.
  * HOLD   — the writer must NOT write autonomously; the intent is enqueued as a
             governance proposal onto the existing ``approvals`` surface (batched
             — one report per sweep, so the banner's pending count goes +1, not
             +N) for Drew to approve one-by-one. NEVER a silent write/drop.
  * REJECT — a structural impossibility (malformed intent, a tripwire that is by
             construction never an honest write). Rare. Still logged, never
             raised. Reserved for the few cases where holding for review would be
             noise (e.g. an empty/garbage intent). REJECT is NOT a content-safety
             verdict — that is the Warden's deferred Stage 3 (# SECURITY-HOOK).

DESIGN INVARIANTS:
  * FAST PURE-PYTHON. No LLM in the gate. No new pip dependency. Stdlib
    (re, json, sqlite3) + the passed connection + the I-α trust organ only.
    LLM judgment happens ONLY at Drew's review of a held proposal.
  * FAIL-GRACEFUL & CALM. Any internal error degrades to ALLOW-but-logged
    (never blocks the writer) and NEVER raises into a consumer. An autoimmune
    gate that could take down the writers is the very thing the doctrine forbids.
  * HARD-DEP L5-I-α (trust_tier.compute_trust). A gate's authority derives from
    trust: a low-trust write is MORE LIKELY to be held. Honors valid_until IS
    NULL (a soft-retired memory is not consulted as live evidence).
  * KILL-SWITCH ``VOIDLOOM_NO_GOVERNANCE`` (matches the kill-switch family) —
    set it and the gate degrades to a pure ALLOW pass-through (still logged).
  * ROUTER, NOT RE-IMPLEMENTER. HOLD enqueues onto the existing approvals
    surface via the governance_proposals table; the surface's approve/reject
    DISPATCHES back to the writer's own re-attempt path. Zero enactment logic is
    duplicated here.

This module is import-safe and side-effect-free at import time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "ALLOW",
    "HOLD",
    "REJECT",
    "GOVERNANCE_CONFIG",
    "governance_gate",
    "is_governance_disabled",
    "ensure_governance_tables",
    "pending_report_batches",
    "list_pending_items",
    "migration_applied",
]

# ----------------------------------------------------------------------------
# Verdict constants.
# ----------------------------------------------------------------------------

ALLOW = "ALLOW"
HOLD = "HOLD"
REJECT = "REJECT"

#: The env kill-switch (matches VOIDLOOM_NO_PPR / VOIDLOOM_NO_DIVERSITY / ...).
ENV_KILL_SWITCH = "VOIDLOOM_NO_GOVERNANCE"

# R5/C6 (class sweep). Same class, fourth shipped member. Relative-self.
# The module-level `pathlib` import is added ON THIS LINE rather than assumed:
# governance.py imports Path only INSIDE two functions (:879, :893), so the
# obvious edit raises NameError at import and takes claude_memory.py down with
# it. Caught by running the prover, not by reading the patch.
from pathlib import Path as _R5Path
DEFAULT_DB_PATH = str(_R5Path(__file__).resolve().parent.parent / "claude_memory.db")

# --- TO-03 (round gamma): the ONE guarded embedding write --------------------
# Every memory this organ writes must be findable. memory_write resolves the
# tier through embed_resolver (which REFUSES rather than defaulting to the table
# retired 2026-05-28), asserts the vector width against the resolved dim, and on
# failure marks the row with the 'embedding-missing' tag so the gap is queryable
# from the DATA, not only from the journal. Stdlib-only at module scope.
import os as _vl_os, sys as _vl_sys
_vl_d = _vl_os.path.dirname(_vl_os.path.abspath(__file__))
if _vl_d not in _vl_sys.path:
    _vl_sys.path.insert(0, _vl_d)
try:
    import memory_write as _vl_mw
except Exception:                       # pragma: no cover - substrate absent
    _vl_mw = None


def _vl_attach(conn, memory_id, text, organ, sources=None, transformation=None,
               attach=True):
    """TO-03 + TO-08 + TO-09: everything a freshly written memory owes the brain.

    embedding (or a queryable 'embedding-missing' mark) + belief_scan_queue entry
    + provenance edges when this organ knows its sources. Never silent.
    """
    if _vl_mw is None:
        import logging as _l
        _l.getLogger("voidloom").error(
            "%s: memory_write unavailable; memory %s written WITHOUT an embedding "
            "and cannot even be marked.", organ, memory_id)
        return {"embedded": False, "error": "memory_write unavailable"}
    return _vl_mw.on_memory_written(conn, memory_id, text, organ=organ,
                                    sources=sources, transformation=transformation,
                                    attach=attach)
# --- end TO-03 ---------------------------------------------------------------

# ----------------------------------------------------------------------------
# Configuration — every tunable in one place. Calibrated to the live corpus
# (see generation_report.md): trust ranks HIGH across the corpus because the
# authorship floor dominates, so the HOLD trigger is driven mainly by WRITE
# CONTEXT (writer kind, provenance confidence, foundation-contradiction,
# injection tripwire); trust MODULATES rather than dominates.
# ----------------------------------------------------------------------------

GOVERNANCE_CONFIG: Dict[str, Any] = {
    # Writers that are autonomous + external-sourced are held more readily; a
    # trusted internal maintenance writer is let through. The kind names match
    # the seams the handoff lists. Unknown writers default to the cautious band.
    "writer_posture": {
        # writer_kind -> base posture: "trusted" | "review" | "external"
        "edge_commit": "review",     # belief engine enactment (already Drew-gated upstream)
        "gap_fill": "external",      # gap-research web ingestion (untrusted source)
        "web_ingest": "external",    # any web-sourced verbatim write
        "dream_integrate": "review", # dream pipeline grounding into the store
        "consolidation": "trusted",  # sleep-loop internal consolidation
        "maintenance": "trusted",    # internal housekeeping
    },
    "writer_posture_default": "review",

    # A write whose provenance/self confidence is below this is held (low-trust
    # writes are more likely to be reviewed). External writers use a stricter bar.
    "confidence_hold_below": 0.45,
    "confidence_hold_below_external": 0.60,

    # Trust modulation: a write that targets/derives-from a memory whose trust is
    # below this is treated as a low-authority write and pushed toward HOLD.
    # CALIBRATION (live corpus): trust ranks HIGH — the authorship floor dominates
    # so the corpus min is ~0.89 and the ABSOLUTE floor (a derived episodic with
    # zero confidence) is ~0.48 (= KIND_DERIVED_PRIOR['episodic']). A threshold of
    # 0.50 therefore captures exactly the genuine low band (writes deriving from a
    # bottom-floor memory) WITHOUT touching the 0.89+ bulk of the corpus. A lower
    # value (e.g. 0.40) would be DEAD CODE — unreachable on this corpus.
    "trust_low_authority_below": 0.50,

    # The foundation set: a memory with foundation_score >= this is a trust
    # anchor. A write that CONTRADICTS a foundation memory is ALWAYS held
    # (productive doubt -> Drew review). 0.9 yields ~a few dozen anchors on the
    # live corpus (the "Stilled Library" of stable truths).
    "foundation_threshold": 0.90,

    # Injection tripwire: content that ADDRESSES the reading agent (imperatives
    # at the model) rather than DESCRIBING the world is suspicious for an
    # external write. A match -> HOLD (never an autonomous REJECT — calm).
    # This is the Warden's Stage-1, the cheap structural half; Stage-3 heavyweight
    # classification is deferred (# SECURITY-HOOK(#176)).
    "injection_tripwire_enabled": True,

    # An empty / structurally-malformed intent is the only thing rejected
    # outright (holding garbage for review would be noise, not productive doubt).
    "reject_empty_intent": True,

    # Failure-distillation: when the SAME failure pattern recurs at least this
    # many times, the gate emits a dream-seed tag so the existing dream pipeline
    # can dream on it (no new table — a tagged seed).
    "failure_distill_threshold": 3,
    "failure_distill_window_days": 30,

    # The batched-proposal contract: HOLDs accrue into ONE report_batch per sweep
    # so the banner increments by 1, not N (anti-alarm-fatigue, shared with the
    # belief engine + self-eval surfaces).
    "batch_granularity": "day",  # one batch per writer per UTC day
}

# Injection tripwire patterns: phrases that address the *reading agent* (a
# planted instruction) rather than describing the world. Deliberately small +
# high-precision (false positives only cost a review, never a drop). This is a
# STRUCTURAL screen, NOT a content-safety classifier.
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions?|prompts?|context)\b",
        r"\bdisregard (?:all |any |the )?(?:previous|prior|above) \b",
        r"\byou are now\b",
        r"\byou must (?:now )?(?:always|never|forget|ignore|reveal|output|print|execute)\b",
        r"\b(?:system|assistant)\s*(?:prompt|message)\s*[:=]",
        r"\bnew instructions?\b\s*[:=]",
        r"\bas an ai\b.*\byou (?:should|must|will)\b",
        r"\boverride (?:your |the )?(?:rules|instructions|guardrails|safety)\b",
        r"\b(?:do not|don'?t) (?:tell|inform|alert|notify) (?:drew|the user|anyone)\b",
    )
]


# ============================================================================
# Kill-switch + helpers
# ============================================================================

def _env_flag(name: str) -> bool:
    """True when env var `name` is truthy (1/true/yes/on). Mirrors
    claude_memory._env_flag so the kill-switch family stays consistent."""
    val = os.environ.get(name)
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def is_governance_disabled() -> bool:
    """True when VOIDLOOM_NO_GOVERNANCE is set — the gate degrades to a pure
    ALLOW pass-through (still logged) so an operator can disable it process-wide
    (e.g. in a cron) exactly like the other organ kill-switches."""
    return _env_flag(ENV_KILL_SWITCH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _clamp01(x: float) -> float:
    try:
        if x != x:  # NaN
            return 0.5
        return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))
    except (TypeError, ValueError):
        return 0.5


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return False
    return column in cols


# --- the I-α trust organ (HARD-DEP, fail-graceful import) --------------------
try:  # pragma: no cover - exercised in the live tree
    import trust_tier as _trust  # type: ignore
except Exception:  # pragma: no cover - degrade: trust modulation omitted
    _trust = None  # type: ignore


def _trust_of(conn: sqlite3.Connection, memory_id: Optional[int]) -> Optional[float]:
    """Trust of a memory via the I-α organ, or None if unavailable/unknown.
    NEVER raises (the organ is itself fail-graceful; this is belt-and-braces)."""
    if _trust is None or memory_id is None:
        return None
    try:
        return float(_trust.compute_trust(conn, int(memory_id)))
    except Exception:
        return None


# ============================================================================
# Migration (idempotent, _has_column / IF NOT EXISTS guarded — the
# dream_pipeline.py / migration_028 pattern). The gate needs ONE small table to
# hold HELD proposals on the approvals surface. No new column on any live table.
# ============================================================================

GOVERNANCE_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS governance_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_batch   TEXT NOT NULL,            -- one batch per writer per day (banner +1, not +N)
    writer_kind    TEXT NOT NULL,            -- gap_fill | edge_commit | dream_integrate | ...
    verdict        TEXT NOT NULL DEFAULT 'HOLD',
    reason         TEXT NOT NULL,            -- why it was held (the rule that fired)
    reason_code    TEXT NOT NULL,            -- machine tag of the rule (for distillation)
    intent_json    TEXT NOT NULL,            -- the full write_intent (so a re-attempt is possible)
    intent_hash    TEXT NOT NULL,            -- dedupe within a batch
    trust_score    REAL DEFAULT NULL,        -- the modulating trust (transparency)
    elevated       INTEGER NOT NULL DEFAULT 0,  -- touches a foundation memory -> surfaced first
    target_memory_id INTEGER DEFAULT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at     TEXT DEFAULT NULL,
    decided_reason TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_govprop_status ON governance_proposals(status);
CREATE INDEX IF NOT EXISTS idx_govprop_batch  ON governance_proposals(report_batch);
CREATE INDEX IF NOT EXISTS idx_govprop_code   ON governance_proposals(reason_code);
"""


def ensure_governance_tables(conn: sqlite3.Connection) -> bool:
    """Create governance_proposals (+ indexes) if absent. Idempotent; uses only
    CREATE ... IF NOT EXISTS so a re-run is a no-op. Returns True on success.

    Also stamps an ADDITIVE marker key into metadata (NEVER overwrites the
    string schema_version — a known scar) so the migration is discoverable.
    """
    try:
        conn.executescript(GOVERNANCE_PROPOSALS_DDL)
        # Additive marker; never touch the existing 'schema_version' string row.
        if _table_exists(conn, "metadata"):
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value, updated_at) "
                "VALUES ('governance_migration', '1', datetime('now'))"
            )
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def migration_applied(conn: sqlite3.Connection) -> bool:
    """True once governance_proposals exists (the approvals surface reads this
    to decide whether to contribute a governance surface — mirrors the belief
    engine's migration_applied guard)."""
    return _table_exists(conn, "governance_proposals")


# ============================================================================
# write_intent normalization
# ============================================================================

# A write_intent is a plain dict the writer hands the gate. All keys optional
# except a sane content/operation; the gate is defensive about partial intents.
#   {
#     "writer": "gap_fill",                # writer_kind (one of GOVERNANCE_CONFIG)
#     "operation": "insert_memory",        # what the writer wants to do
#     "content": "...",                    # the prose being written (screened)
#     "summary": "...",
#     "confidence": 0.5,                   # provenance/self confidence of the write
#     "source_context": "gap-research ...",# how it was produced
#     "target_memory_id": 1234,            # memory it derives-from / would touch
#     "contradicts_memory_id": 1234,       # a memory this write asserts-against
#     "metadata": {...},                   # free-form
#   }

def _normalize_intent(write_intent: Any) -> Optional[Dict[str, Any]]:
    """Coerce the write_intent into a dict. Returns None for an unusable intent
    (which becomes a REJECT when reject_empty_intent is on, else ALLOW)."""
    if write_intent is None:
        return None
    if isinstance(write_intent, dict):
        d = dict(write_intent)
    else:
        try:
            d = dict(write_intent)  # mapping-like
        except Exception:
            return None
    return d


def _intent_text(intent: Dict[str, Any]) -> str:
    parts = []
    for k in ("content", "summary", "text"):
        v = intent.get(k)
        if v:
            parts.append(str(v))
    return "\n".join(parts)


def _intent_hash(intent: Dict[str, Any]) -> str:
    """A stable dedupe hash over the meaningful payload of the intent."""
    basis = json.dumps(
        {
            "writer": intent.get("writer"),
            "operation": intent.get("operation"),
            "content": intent.get("content"),
            "target_memory_id": intent.get("target_memory_id"),
            "contradicts_memory_id": intent.get("contradicts_memory_id"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _writer_kind(intent: Dict[str, Any]) -> str:
    w = (intent.get("writer") or intent.get("writer_kind") or "").strip().lower()
    return w or "unknown"


def _writer_posture(writer_kind: str, cfg: Dict[str, Any]) -> str:
    return cfg["writer_posture"].get(writer_kind, cfg["writer_posture_default"])


# ============================================================================
# Screens (each returns (fired: bool, reason_code, reason, elevated))
# ============================================================================

def _screen_injection(text: str, cfg: Dict[str, Any]) -> Tuple[bool, str, str, bool]:
    """Warden Stage-1 (the cheap structural half): content that ADDRESSES the
    reading agent (a planted instruction) rather than describing the world.

    A match -> HOLD (never an autonomous REJECT — calm: a held injection goes to
    Drew, who is the LLM judgment the gate refuses to be).

    # SECURITY-HOOK(#176): the heavyweight content classifier (the Warden's
    # Stage-3 nvidia/Nemotron-3.5-Content-Safety, ~3GB INT4, federation-only) +
    # the provenance-framing wrap are DEFERRED to the final security audit. This
    # structural tripwire is the seam they plug into.
    """
    if not cfg.get("injection_tripwire_enabled", True) or not text:
        return (False, "", "", False)
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return (
                True,
                "injection_tripwire",
                "content addresses the reading agent (possible prompt injection) "
                "rather than describing the world — held for the owner's eyes",
                True,  # an injection attempt is elevated
            )
    return (False, "", "", False)


def _screen_foundation_contradiction(
    conn: sqlite3.Connection, intent: Dict[str, Any], cfg: Dict[str, Any]
) -> Tuple[bool, str, str, bool]:
    """Warden Stage-2 (reuse L5-D + L5-I-α, zero new ML): a write that asserts
    AGAINST a FOUNDATION memory (a trust anchor) is productive doubt -> ALWAYS
    held for Drew. Honors valid_until IS NULL semantics by only consulting a
    memory that is not archived (the live brain's soft-retire marker)."""
    tgt = intent.get("contradicts_memory_id")
    if tgt is None:
        return (False, "", "", False)
    try:
        row = conn.execute(
            "SELECT foundation_score, is_archived FROM memories WHERE id = ?",
            (int(tgt),),
        ).fetchone()
    except (sqlite3.Error, TypeError, ValueError):
        return (False, "", "", False)
    if row is None:
        return (False, "", "", False)
    fscore, is_archived = row[0], row[1]
    # A soft-retired/archived memory is not consulted as live evidence.
    if is_archived:
        return (False, "", "", False)
    if fscore is not None and float(fscore) >= cfg["foundation_threshold"]:
        return (
            True,
            "foundation_contradiction",
            f"write contradicts foundation memory #{tgt} "
            f"(foundation_score={float(fscore):.3f} >= {cfg['foundation_threshold']}) "
            "— productive doubt, held for review",
            True,  # foundation-touching -> elevated (surfaced first)
        )
    return (False, "", "", False)


def _screen_low_confidence(
    intent: Dict[str, Any], posture: str, cfg: Dict[str, Any]
) -> Tuple[bool, str, str, bool]:
    """A write whose provenance/self confidence is below the (posture-specific)
    bar is held. External writers use a stricter bar than internal ones."""
    conf = intent.get("confidence")
    if conf is None:
        return (False, "", "", False)
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        return (False, "", "", False)
    bar = (cfg["confidence_hold_below_external"]
           if posture == "external"
           else cfg["confidence_hold_below"])
    if conf < bar:
        return (
            True,
            "low_confidence",
            f"write confidence {conf:.2f} below the {posture} bar {bar:.2f} — held for review",
            False,
        )
    return (False, "", "", False)


def _screen_low_authority(
    conn: sqlite3.Connection, intent: Dict[str, Any], cfg: Dict[str, Any]
) -> Tuple[bool, str, str, bool, Optional[float]]:
    """Trust modulation (HARD-DEP I-α): a write that derives-from a LOW-TRUST
    memory is a low-authority write -> pushed toward HOLD. Returns the trust as a
    5th element for transparency. Trust MODULATES; it never bars alone."""
    tgt = intent.get("target_memory_id")
    trust = _trust_of(conn, tgt)
    if trust is None:
        return (False, "", "", False, None)
    if trust < cfg["trust_low_authority_below"]:
        return (
            True,
            "low_authority",
            f"write derives from low-trust memory #{tgt} (trust={trust:.2f} < "
            f"{cfg['trust_low_authority_below']}) — held for review",
            False,
            trust,
        )
    return (False, "", "", False, trust)


# ============================================================================
# Failure distillation -> a dream-seed tag (the one procedural sliver, no table)
# ============================================================================

def _maybe_distill_failure(
    conn: sqlite3.Connection, reason_code: str, writer_kind: str, cfg: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """When the SAME reason_code recurs >= failure_distill_threshold times within
    the window, emit a dream-seed: a tagged memory the existing dream pipeline can
    pick up as a seed (NO new table — a tagged seed). Idempotent within a window
    (a seed already laid for this (code, day) is not re-laid). NEVER raises."""
    try:
        if not _table_exists(conn, "governance_proposals"):
            return None
        thr = int(cfg.get("failure_distill_threshold", 3))
        win = int(cfg.get("failure_distill_window_days", 30))
        cnt = conn.execute(
            "SELECT COUNT(*) FROM governance_proposals "
            "WHERE reason_code = ? "
            "AND created_at >= datetime('now', ?)",
            (reason_code, f"-{win} days"),
        ).fetchone()[0]
        if cnt < thr:
            return None
        # Only lay a seed if memory_tags exists (the dream pipeline reads tags).
        if not _table_exists(conn, "memory_tags"):
            return None
        seed_tag = "dream-seed:governance-failure"
        code_tag = f"governance-failure:{reason_code}"
        # Idempotent: has a governance-failure dream-seed for this code been laid
        # in the last window? If so, do not duplicate.
        already = conn.execute(
            "SELECT m.id FROM memories m "
            "JOIN memory_tags t1 ON t1.memory_id = m.id AND t1.tag = ? "
            "JOIN memory_tags t2 ON t2.memory_id = m.id AND t2.tag = ? "
            "WHERE m.created_at >= datetime('now', ?) LIMIT 1",
            (seed_tag, code_tag, f"-{win} days"),
        ).fetchone()
        if already is not None:
            return {"seeded": False, "reason": "already_seeded_in_window",
                    "memory_id": already[0], "reason_code": reason_code}

        content = (
            f"GOVERNANCE FAILURE PATTERN (dream seed): the governance gate has "
            f"held writes for reason '{reason_code}' {cnt} times in the last "
            f"{win} days (most recent writer: {writer_kind}). A repeated HOLD is "
            f"a signal worth dreaming on — is a writer mis-calibrated, is a rule "
            f"too tight, or is there a real systemic issue upstream? This seed "
            f"invites the dream pipeline to explore the pattern.\n\n"
            f"[governance-distillation] reason_code={reason_code} count={cnt} "
            f"window_days={win}"
        )
        summary = (f"Governance failure pattern '{reason_code}' x{cnt} "
                   f"({writer_kind}) — dream seed")
        cur = conn.execute(
            "INSERT INTO memories (content, summary, category, importance, "
            "source_project, source_context, kind, confidence) "
            "VALUES (?, ?, 'workflow_pattern', 0.6, 'wizard-library', "
            "'governance-distillation', 'procedural', 0.7)",
            (content, summary),
        )
        seed_id = cur.lastrowid
        # TO-03: this memory must be findable.
        _vl_attach(conn, seed_id, content, "governance._maybe_distill_failure")
        for tag in (seed_tag, code_tag, "governance"):
            conn.execute(
                "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (seed_id, tag),
            )
        conn.commit()
        return {"seeded": True, "memory_id": seed_id,
                "reason_code": reason_code, "count": cnt}
    except sqlite3.Error:
        return None


# ============================================================================
# HOLD routing -> the existing approvals surface (batched, +1 not +N)
# ============================================================================

def _batch_id(writer_kind: str, cfg: Dict[str, Any]) -> str:
    """One report_batch per writer per UTC day (the anti-alarm-fatigue unit).
    All HOLDs from one writer on one day fold into ONE review item; the banner's
    pending count increments by 1, not N (the contract shared with belief +
    self-eval surfaces)."""
    if cfg.get("batch_granularity") == "day":
        return f"gov-{writer_kind}-{_utc_day()}"
    return f"gov-{writer_kind}-{_now_iso().replace(' ', 'T')}"


def _enqueue_hold(
    conn: sqlite3.Connection,
    intent: Dict[str, Any],
    writer_kind: str,
    reason_code: str,
    reason: str,
    elevated: bool,
    trust_score: Optional[float],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Enqueue a HELD write as a governance proposal onto the approvals surface.
    Batched (one report_batch per writer per day). Dedupes within a batch by
    intent_hash so a writer retrying the identical write does not spam the queue.
    Returns a routing receipt. NEVER raises (a routing failure degrades to a
    logged ALLOW so the writer is never blocked — see governance_gate)."""
    ensure_governance_tables(conn)
    batch = _batch_id(writer_kind, cfg)
    ihash = _intent_hash(intent)
    tgt = intent.get("target_memory_id") or intent.get("contradicts_memory_id")

    # Dedupe: identical pending intent already queued in this batch?
    existing = conn.execute(
        "SELECT id FROM governance_proposals "
        "WHERE report_batch = ? AND intent_hash = ? AND status = 'pending' LIMIT 1",
        (batch, ihash),
    ).fetchone()
    if existing is not None:
        return {
            "routed": True, "deduped": True, "report_batch": batch,
            "proposal_id": existing[0], "pending_delta": 0,
        }

    # Does this batch already have pending items? (Then this is +0 to the banner
    # — the report_batch is the unit, not the proposal.)
    batch_had_pending = conn.execute(
        "SELECT COUNT(*) FROM governance_proposals "
        "WHERE report_batch = ? AND status = 'pending'",
        (batch,),
    ).fetchone()[0]

    cur = conn.execute(
        "INSERT INTO governance_proposals "
        "(report_batch, writer_kind, verdict, reason, reason_code, intent_json, "
        " intent_hash, trust_score, elevated, target_memory_id, status) "
        "VALUES (?, ?, 'HOLD', ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (batch, writer_kind, reason, reason_code, json.dumps(intent, default=str),
         ihash, trust_score, 1 if elevated else 0,
         int(tgt) if tgt is not None else None),
    )
    proposal_id = cur.lastrowid
    conn.commit()
    return {
        "routed": True, "deduped": False, "report_batch": batch,
        "proposal_id": proposal_id,
        # +1 to the banner ONLY when this batch had no pending items before.
        "pending_delta": 0 if batch_had_pending > 0 else 1,
    }


# ============================================================================
# The gate
# ============================================================================

def governance_gate(
    conn: sqlite3.Connection,
    write_intent: Any,
    *,
    config: Optional[Dict[str, Any]] = None,
    route_holds: bool = True,
) -> Dict[str, Any]:
    """The ONE calm, Drew-gated governance gate every autonomous writer routes
    through.

    Args:
        conn: an open sqlite3 connection to the memory DB. The gate reads
            (trust, foundation) and, on a HOLD with ``route_holds``, writes ONE
            governance_proposals row (the approvals enqueue). It never mutates
            the memories the writer wanted to write.
        write_intent: a dict describing the write (see _normalize_intent). A
            partial/None intent is handled defensively.
        config: optional overrides merged over GOVERNANCE_CONFIG.
        route_holds: when True (default), a HOLD is enqueued onto the approvals
            surface. Set False to get a pure verdict without side effects (used
            in tests + dry-runs).

    Returns:
        A dict:
          {
            "verdict": ALLOW | HOLD | REJECT,
            "reason": str,                # human reason
            "reason_code": str,           # machine tag
            "writer_kind": str,
            "elevated": bool,             # touches a foundation memory
            "trust_score": float | None,  # the modulating trust (transparency)
            "routing": {...} | None,      # the approvals receipt on a routed HOLD
            "distillation": {...} | None, # a dream-seed receipt if one was laid
            "ok": bool,                   # False on the degraded (calm) path
            "elapsed_ms": int,
          }

    THE CALM CONTRACT: any internal error degrades to ALLOW-but-logged and NEVER
    raises into the writer. The kill-switch VOIDLOOM_NO_GOVERNANCE forces a pure
    ALLOW pass-through.
    """
    t0 = time.time()
    cfg = {**GOVERNANCE_CONFIG, **(config or {})}

    def _result(verdict, reason, code, **extra):
        out = {
            "verdict": verdict,
            "reason": reason,
            "reason_code": code,
            "writer_kind": extra.pop("writer_kind", "unknown"),
            "elevated": extra.pop("elevated", False),
            "trust_score": extra.pop("trust_score", None),
            "routing": extra.pop("routing", None),
            "distillation": extra.pop("distillation", None),
            "ok": extra.pop("ok", True),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
        out.update(extra)
        return out

    # --- kill-switch: pure ALLOW pass-through (still a clean ALLOW result) ----
    if is_governance_disabled():
        return _result(ALLOW, "governance disabled via VOIDLOOM_NO_GOVERNANCE — "
                              "pass-through (logged)", "kill_switch")

    try:
        intent = _normalize_intent(write_intent)

        # --- the only outright REJECT: an empty/unusable intent ---------------
        if intent is None or not _intent_text(intent) and not intent.get("operation"):
            if cfg.get("reject_empty_intent", True):
                return _result(REJECT,
                               "empty or structurally-unusable write intent — "
                               "nothing to write (not a content-safety verdict)",
                               "empty_intent", ok=True)
            return _result(ALLOW, "empty intent permitted by config", "empty_intent")

        writer_kind = _writer_kind(intent)
        posture = _writer_posture(writer_kind, cfg)
        text = _intent_text(intent)

        # --- run the screens. The FIRST screen that fires -> HOLD. Order is by
        # severity so the recorded reason is the most important one. ----------
        fired_code = ""
        fired_reason = ""
        elevated = False
        trust_score: Optional[float] = None

        # 1. injection tripwire (Warden Stage-1) — only for external-source writes
        #    (an internal consolidation quoting an instruction is not an attack).
        if posture == "external":
            f, code, reason, elev = _screen_injection(text, cfg)
            if f:
                fired_code, fired_reason, elevated = code, reason, elev

        # 2. foundation contradiction (Warden Stage-2) — always, any writer.
        if not fired_code:
            f, code, reason, elev = _screen_foundation_contradiction(conn, intent, cfg)
            if f:
                fired_code, fired_reason, elevated = code, reason, elev

        # 3. low authority (trust modulation, HARD-DEP I-α).
        if not fired_code:
            f, code, reason, elev, trust_score = _screen_low_authority(conn, intent, cfg)
            if f:
                fired_code, fired_reason, elevated = code, reason, elev
        else:
            # still surface trust for transparency even if another screen fired.
            trust_score = _trust_of(conn, intent.get("target_memory_id"))

        # 4. low confidence (posture-specific bar).
        if not fired_code:
            f, code, reason, elev = _screen_low_confidence(intent, posture, cfg)
            if f:
                fired_code, fired_reason, elevated = code, reason, elev

        # 5. external-posture writers with no positive signal still get a light
        #    touch: an external write with NO confidence at all is held (we have
        #    no provenance to trust). A trusted/review writer with no signal is
        #    ALLOWED (calm default — permissive-but-logged).
        if not fired_code and posture == "external" and intent.get("confidence") is None:
            fired_code = "external_no_provenance"
            fired_reason = ("external-source write carries no provenance confidence "
                            "— held for review (calm: untrusted source)")

        # --- verdict ----------------------------------------------------------
        if not fired_code:
            return _result(ALLOW,
                           "no governance concern — permissive-but-logged (calm default)",
                           "allow", writer_kind=writer_kind, elevated=False,
                           trust_score=trust_score)

        # A concern fired -> HOLD (never an autonomous REJECT/silent drop).
        routing = None
        distillation = None
        if route_holds:
            try:
                routing = _enqueue_hold(conn, intent, writer_kind, fired_code,
                                        fired_reason, elevated, trust_score, cfg)
            except sqlite3.Error:
                # CALM DEGRADE: if we cannot route the HOLD, we do NOT silently
                # drop and we do NOT block the writer with an exception. We fall
                # back to ALLOW-but-logged (the proposal could not be filed, so
                # blocking the write would be a silent drop — the worse failure).
                return _result(ALLOW,
                               f"governance wanted to HOLD ({fired_code}) but the "
                               "approvals enqueue failed; degraded to ALLOW-but-logged "
                               "(never a silent drop)", fired_code,
                               writer_kind=writer_kind, elevated=elevated,
                               trust_score=trust_score, ok=False)
            # opportunistic failure-distillation (dream seed; never blocks).
            distillation = _maybe_distill_failure(conn, fired_code, writer_kind, cfg)

        return _result(HOLD, fired_reason, fired_code,
                       writer_kind=writer_kind, elevated=elevated,
                       trust_score=trust_score, routing=routing,
                       distillation=distillation)

    except Exception as e:  # THE CALM CONTRACT: never raise into a writer.
        return _result(ALLOW,
                       f"governance gate degraded to ALLOW-but-logged on error: {e} "
                       "(calm: a gate failure must never block a writer)",
                       "gate_error", ok=False)


# ============================================================================
# Approvals-surface integration (read side — the surface aggregates these)
# ============================================================================

def pending_report_batches(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Pending governance HOLD batches as summary rows (1 per report_batch),
    mirroring belief_engine.pending_report_batches so voidloom_approvals can fold
    governance into the unified surface with ZERO new aggregation logic.

    Returns: [{report_batch, pending, elevated, writer_kind}], read-only."""
    if not _table_exists(conn, "governance_proposals"):
        return []
    try:
        rows = conn.execute(
            "SELECT report_batch, "
            "       COUNT(*) AS pending, "
            "       SUM(CASE WHEN elevated=1 THEN 1 ELSE 0 END) AS elevated, "
            "       MIN(writer_kind) AS writer_kind "
            "FROM governance_proposals WHERE status='pending' "
            "GROUP BY report_batch ORDER BY report_batch DESC"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {"report_batch": r[0], "pending": int(r[1] or 0),
         "elevated": int(r[2] or 0), "writer_kind": r[3]}
        for r in rows
    ]


def list_pending_items(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Pending governance batches as review items in the shared approvals-item
    schema (surface, ref, title, count, elevated, approve_cmd, ...). Read-only;
    fail-graceful; NEVER raises. The unified surface calls this when governance
    is migrated."""
    path = db_path or os.environ.get("WIZARD_LIBRARY_DB") or \
        os.environ.get("CLAUDE_MEMORY_DB") or DEFAULT_DB_PATH
    items: List[Dict[str, Any]] = []
    try:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            for b in pending_report_batches(conn):
                batch = b["report_batch"]
                items.append({
                    "surface": "governance",
                    "ref": batch,
                    "title": f"Governance holds {batch}",
                    "count": b["pending"],
                    "elevated": b["elevated"] > 0,
                    "elevated_count": b["elevated"],
                    "created_at": None,
                    "status": "pending",
                    "approve_cmd": (
                        f"voidloom approvals --approve governance:{batch} "
                        f"--ids <proposal_id> [<id> ...]"
                    ),
                    "reject_cmd": (
                        f"voidloom approvals --reject governance:{batch} "
                        f'--ids <proposal_id> [<id> ...] --reason "..."'
                    ),
                    "detail_cmd": f"voidloom approvals --show governance:{batch}",
                })
        finally:
            conn.close()
    except Exception:
        return []
    return items


# ============================================================================
# Thin CLI (diagnostics only — the real surface is `voidloom approvals`).
# ============================================================================

def _brain_state(db_path: str) -> str:
    """"ok" | "brain_not_found" | "brain_empty" (R4/P4 pass 3)."""
    from pathlib import Path as _P
    try:
        p = _P(db_path)
        if not p.is_file():
            return "brain_not_found"
        if p.stat().st_size == 0:
            return "brain_empty"
        return "ok"
    except (OSError, TypeError, ValueError):
        return "brain_not_found"


def _cli(argv: List[str]) -> int:
    import argparse
    from pathlib import Path
    p = argparse.ArgumentParser(
        prog="governance",
        description="Voidloom L5-I-β governance gate (diagnostics).")
    p.add_argument("--db", default=None, help="path to the memory DB")
    p.add_argument("--pending", action="store_true",
                   help="list pending governance HOLD batches")
    p.add_argument("--migrate", action="store_true",
                   help="ensure the governance_proposals table exists (idempotent)")
    p.add_argument("--gate-test", metavar="JSON", default=None,
                   help="run the gate on a JSON write_intent (no routing)")
    args = p.parse_args(argv[1:])

    path = args.db or os.environ.get("WIZARD_LIBRARY_DB") or \
        os.environ.get("CLAUDE_MEMORY_DB") or DEFAULT_DB_PATH

    # R4/P4. `--migrate` is not read-shaped -- creating tables IS its job
    # -- but it is not INSTALL-shaped either: migrating a brain that does
    # not exist manufactures a one-table file that nothing else in the
    # ecosystem will recognise as a brain, and that the installer's `[ -f
    # ]` test will then treat as one. A typo'd --db is the whole failure
    # mode, and it is silent today. House pattern: existence check plus a
    # NAMED error, never a `?mode=rw` URI (which returns an uncaught
    # traceback instead of a clean envelope).
    if args.migrate:
        _st = _brain_state(path)
        if _st != "ok":
            print(json.dumps({
                "status": "error", "error": _st,
                "action": "ensure_governance_tables", "database": path,
                "message": (f"no brain database at {path}. Refusing to "
                            f"CREATE one; run 'claude_memory.py init' "
                            f"first, or pass --db.")}))
            return 1
        conn = sqlite3.connect(path, timeout=60.0)
        try:
            ok = ensure_governance_tables(conn)
        finally:
            conn.close()
        print(json.dumps({"status": "ok" if ok else "error",
                          "action": "ensure_governance_tables"}))
        return 0 if ok else 1

    if args.gate_test is not None:
        try:
            intent = json.loads(args.gate_test)
        except ValueError as e:
            print(json.dumps({"status": "error", "message": f"bad JSON: {e}"}))
            return 1
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            out = governance_gate(conn, intent, route_holds=False)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", **out}, indent=2, default=str))
        return 0

    # default / --pending: list pending batches.
    # R4/P4, SAME CLASS AT THE READ END. `list_pending_items` is
    # fail-graceful by contract and returns [] on an unopenable path, so
    # an absent brain rendered `{"status":"ok","items":[]}` -- a clean,
    # confident "no governance holds" about a database that is not there.
    # R4/P4 pass 3. `is_file()` accepted a 0-BYTE file, so this verb
    # still answered `{"status":"ok","items":[]}` -- a confident empty
    # about a file that is not a database. Measured after pass 2, inside
    # the fix for the same class.
    _st = _brain_state(path)
    if _st != "ok":
        print(json.dumps({
            "status": "error", "error": _st,
            "action": "pending", "database": path, "items": None,
            "message": (f"the brain at {path} is "
                        + ("absent" if _st == "brain_not_found"
                           else "0 bytes (wreckage of a failed init or "
                                "copy; remove it)")
                        + f"; the pending governance set is UNKNOWN, "
                          f"not empty.")},
            indent=2, default=str))
        return 1
    print(json.dumps({"status": "ok", "action": "pending",
                      "database": path,
                      "items": list_pending_items(path)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(__import__("sys").argv))

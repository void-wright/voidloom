"""Voidloom L5-M5 — "The Warden" (W-web: the untrusted-source ingestion gate).

The Warden is a FEATURE of the landed L5-I-β ``governance_gate``, NOT a new organ
and NOT a new cascade. It is the thin integration + provenance keeper that wires
the gap-research / auto-harvest writer (``gap_mapping.dispatch_auto_research`` —
the weekly systemd timer ``wizard-library-gap-research.service`` that writes
web-sourced ``claude -p`` output verbatim into the live graph at conf 0.5)
THROUGH the gate before each web-sourced commit.

THE THREAT IS INJECTION / FALSE-FACTS, NOT TOXICITY.

The gate already runs the two cheap structural screens:
  * Stage-1 injection-tripwire — content that ADDRESSES the reading agent (a
    planted instruction) vs DESCRIBES the world.
  * Stage-2 foundation-contradiction — a web claim that asserts against a
    FOUNDATION memory (a trust anchor) is productive doubt.
M5 adds NO new ML. It adds:
  (1) the WIRING — build a write_intent, call ``governance_gate``, and on ALLOW
      write + stamp; on HOLD route to the existing approvals surface (batched,
      +1 not +N) and do NOT write; fail-soft so a gate error never crashes the
      harvest.
  (2) the PROVENANCE-INTEGRITY KEEPER — an IMMUTABLE, VISIBLE ``untrusted_source``
      birth-mark on every web-sourced row, set once at ingestion, NEVER cleared
      (a storage-layer trigger forbids clearing it). It survives consolidation /
      dreaming so "a web claim quietly becomes a trusted fact" (provenance
      laundering) is structurally impossible. A sibling to L5-G's surprise
      birth-certificate (``encoding_surprise`` + ``surprise_finalized``).

DOCTRINE (council #5839 / #6195 / #6193):
  * RANK + HOLD, never autonomously BAR / silent-drop. A HOLD goes to Drew (the
    LLM judgment the gate refuses to be), never an auto-REJECT on hostile content.
  * The cheapest real defense already exists structurally: stamping the row also
    ACTIVATES the L5-I-α trust-floor (see ``stamp_untrusted_source``), so conf-0.5
    web content ranks LOW in recall and cannot out-rank a Drew memory — even
    un-quarantined. The Warden is the escalation on top.
  * NEVER gate the owner's own writes or the brain's dreams (CALM / anti-autoimmunity).
    The Warden is invoked ONLY by the gap-research writer; nothing else routes
    through it.
  * Off-by-default kill-switch ``VOIDLOOM_NO_WARDEN`` (matches the family). When
    set, the writer falls back to its prior un-gated behavior BUT still stamps the
    birth-mark + activates the trust-floor (the cheapest defense ships even with
    the gate off — disabling the escalation must not re-open the laundering hole).

DEFERRED (federation / final security audit):
  # SECURITY-HOOK(#176): the Stage-3 nvidia/Nemotron-Content-Safety classifier
  (federation-only, ~3GB INT4) and W-fed (foreign-INSERT federation). No
  foreign-INSERT path exists yet, so W-fed has nothing to gate.

PURITY: stdlib only (os, sqlite3, hashlib) + a fail-graceful sibling import of
``governance``. Importing this module pulls in NO model, NO torch, NO network.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "WARDEN_WRITER_KIND",
    "UNTRUSTED_DERIVED_FROM",
    "WEB_ADMISSION_CONFIDENCE",
    "WEB_PROVENANCE_CONFIDENCE",
    "is_warden_disabled",
    "ensure_warden_migration",
    "warden_migration_applied",
    "build_web_write_intent",
    "screen_web_write",
    "stamp_untrusted_source",
    "is_untrusted",
]

# ---------------------------------------------------------------------------
# Constants — the contract shared with governance.py + trust_tier.py
# ---------------------------------------------------------------------------

# The writer_kind the gate maps to the "external" posture (governance.py
# GOVERNANCE_CONFIG["writer_posture"]["gap_fill"] == "external"), which is what
# makes the Stage-1 injection screen run for these writes. MUST match.
WARDEN_WRITER_KIND = "gap_fill"

# The sentinel stamped into memories.derived_from. The L5-I-α trust organ
# (trust_tier._authorship_floor) treats any non-empty derived_from as a DERIVED
# (non-authored) write, flooring its authorship at the kind prior (semantic=0.52)
# instead of the authored floor (0.85). This is HOW the stamp activates the
# trust-floor: web content drops well below an authored Drew memory in recall.
UNTRUSTED_DERIVED_FROM = "untrusted_web_source"

# The confidence carried in the write_intent for the GATE's admission decision.
# Set == the external confidence bar (governance.py confidence_hold_below_external
# = 0.60) so a benign web fact that passes the injection + foundation screens is
# ALLOWED (admitted). NOTE the deliberate separation of concerns:
#   * ADMISSION (this value, 0.60): "do we trust the SOURCE enough to auto-write?"
#     -> yes for benign content; the trust-floor (below) handles ranking.
#   * RANKING (the stored confidence, 0.50 below): "how high does it sit in
#     recall?" -> LOW, because it is untrusted-external.
# Conflating the two would HOLD every benign harvest (0.5 < 0.6 bar) and bury Drew
# under +N alarms — the autoimmune failure. Keep them distinct.
WEB_ADMISSION_CONFIDENCE = 0.60

# The confidence written onto the STORED row (memories.confidence) at stamp time.
# The gap-research writer historically leaves confidence at the table DEFAULT of
# 1.0 — wrong for untrusted web content. Lowering it to 0.50 aligns the stored
# self-confidence with the 0.5 web provenance and (with the derived_from sentinel)
# floors the I-α trust LOW. Matches the conf 0.5 the writer already stamps into
# importance + source_project='gap-research'.
WEB_PROVENANCE_CONFIDENCE = 0.50

# ---------------------------------------------------------------------------
# Fail-graceful sibling import of the gate (the WorkingDirectory of the systemd
# unit is <brain>, exactly like governance.py's bare
# `import trust_tier`). If governance is somehow absent, the Warden degrades to
# stamp-only (the cheapest defense still ships; the escalation is skipped).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised by the absence path in tests via monkeypatch
    import governance as _gov  # type: ignore
except Exception:  # pragma: no cover
    _gov = None  # type: ignore


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

def _env_flag(name: str) -> bool:
    """Truthy env flag: 1/true/yes/on (case-insensitive). Mirrors governance._env_flag."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_warden_disabled() -> bool:
    """When VOIDLOOM_NO_WARDEN is set, the gate ESCALATION is skipped (the writer
    writes without the HOLD path) BUT the birth-mark + trust-floor stamping still
    happen — disabling the escalation must NOT re-open the provenance-laundering
    hole. Also honors VOIDLOOM_NO_GOVERNANCE (the family-wide off switch) since
    the Warden's escalation is the gate."""
    return _env_flag("VOIDLOOM_NO_WARDEN") or _env_flag("VOIDLOOM_NO_GOVERNANCE")


# ---------------------------------------------------------------------------
# Migration: the untrusted_source birth-mark column + immutability trigger
# ---------------------------------------------------------------------------

_MIGRATION_MARKER_KEY = "warden_migration_029_applied"

# Embedded so the module is self-sufficient even if the .sql file is not co-located
# at runtime (prefer the on-disk file when present; this is the byte-identical
# fallback). The ADD COLUMN is issued separately (PRAGMA-guarded) because SQLite
# has no ADD COLUMN IF NOT EXISTS.
_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_untrusted_source_immutable
BEFORE UPDATE OF untrusted_source ON memories
FOR EACH ROW
WHEN OLD.untrusted_source = 1
     AND (NEW.untrusted_source IS NULL OR NEW.untrusted_source = 0)
BEGIN
    SELECT RAISE(ABORT,
        'untrusted_source birth-mark is immutable: a web-sourced memory cannot be laundered into a trusted fact (L5-M5 Warden)');
END;
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_memories_untrusted_source
    ON memories(untrusted_source)
    WHERE untrusted_source = 1;
"""


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return False
    return any(c[1] == column for c in cols)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def warden_migration_applied(conn: sqlite3.Connection) -> bool:
    """True once the untrusted_source column exists (the load-bearing artifact)."""
    return _has_column(conn, "memories", "untrusted_source")


def ensure_warden_migration(conn: sqlite3.Connection,
                            migration_sql_path: Optional[str] = None) -> Dict[str, Any]:
    """Idempotently apply the M5 migration (column + trigger + index + marker).

    NEVER raises — a migration failure degrades to a reported error so the harvest
    is not crashed by a schema hiccup (the caller's fail-soft path takes over).

    The ADD COLUMN is PRAGMA-guarded (SQLite has no IF NOT EXISTS for it); the
    trigger/index/marker use IF NOT EXISTS / INSERT OR IGNORE so a re-apply is a
    clean no-op. Returns {"applied": bool, "already": bool, "error": str|None}.
    """
    out: Dict[str, Any] = {"applied": False, "already": False, "error": None}
    try:
        if not _table_exists(conn, "memories"):
            out["error"] = "memories table absent"
            return out

        already_col = _has_column(conn, "memories", "untrusted_source")
        if not already_col:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN untrusted_source INTEGER NOT NULL DEFAULT 0"
            )

        # Trigger + index are IF NOT EXISTS — safe to (re)issue every time.
        conn.executescript(_TRIGGER_SQL)
        conn.executescript(_INDEX_SQL)

        if _table_exists(conn, "metadata"):
            conn.execute(
                "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, '1')",
                (_MIGRATION_MARKER_KEY,),
            )
        conn.commit()
        out["applied"] = True
        out["already"] = already_col
        return out
    except sqlite3.Error as e:  # pragma: no cover - defensive
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# The write-intent the gate screens
# ---------------------------------------------------------------------------

def build_web_write_intent(
    *,
    content: str,
    summary: str,
    entity: str,
    gap_type: str,
    gap_id: Optional[int] = None,
    method: str = "thinker",
    contradicts_memory_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Construct the write_intent the gate screens for a web-sourced gap-research
    write.

    The ``content`` MUST be the untrusted external text (the verbatim ``claude -p``
    output / web payload) so the Stage-1 injection screen inspects the ACTUAL
    untrusted bytes — not the wrapper the writer prepends.

    ``contradicts_memory_id`` (optional): when the writer can identify a memory the
    web claim asserts against, pass it so Stage-2 (foundation-contradiction) can
    fire. The current gap-research writer does not compute this; the field is
    wired for the day it does (and exercised by the tests).
    """
    return {
        "writer": WARDEN_WRITER_KIND,        # -> "external" posture -> injection screen runs
        "operation": "insert",
        "content": content,                  # the untrusted bytes the screen reads
        "summary": summary,
        "source_project": "gap-research",
        "confidence": WEB_ADMISSION_CONFIDENCE,  # the ADMISSION confidence (see constant)
        "contradicts_memory_id": contradicts_memory_id,
        # transparency / audit only (not read by the screens):
        "entity": (entity or "")[:200],
        "gap_type": gap_type,
        "gap_id": gap_id,
        "method": method,
        "untrusted_source": True,            # self-describing; the stamp is the enforcement
    }


def screen_web_write(
    conn: sqlite3.Connection,
    intent: Dict[str, Any],
    *,
    route_holds: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a web-sourced write_intent through the governance gate.

    Returns the gate's verdict dict (verdict ALLOW|HOLD|REJECT, reason,
    reason_code, routing receipt on a routed HOLD, ...). If the gate module is
    absent OR the kill-switch is set, returns a synthetic ALLOW so the writer
    proceeds (stamp-only mode) — disabling the escalation must never block or
    silently drop a write (the calm contract).
    """
    if _gov is None or is_warden_disabled():
        return {
            "verdict": "ALLOW",
            "reason": ("warden escalation disabled (kill-switch or governance "
                       "absent) — write proceeds, still stamped untrusted"),
            "reason_code": "warden_disabled",
            "writer_kind": WARDEN_WRITER_KIND,
            "elevated": False,
            "trust_score": None,
            "routing": None,
            "ok": True,
        }
    try:
        return _gov.governance_gate(conn, intent, config=config, route_holds=route_holds)
    except Exception as e:  # pragma: no cover - the gate is itself fail-soft; belt+braces
        # The gate never raises, but if a future refactor regresses that, the
        # Warden still degrades to ALLOW-but-logged rather than crash the harvest.
        return {
            "verdict": "ALLOW",
            "reason": f"warden: gate raised unexpectedly ({e}); degraded to ALLOW-but-logged",
            "reason_code": "warden_gate_error",
            "writer_kind": WARDEN_WRITER_KIND,
            "elevated": False,
            "trust_score": None,
            "routing": None,
            "ok": False,
        }


# ---------------------------------------------------------------------------
# The provenance-integrity keeper: the immutable birth-mark + trust-floor
# ---------------------------------------------------------------------------

def stamp_untrusted_source(conn: sqlite3.Connection, memory_id: int) -> Dict[str, Any]:
    """Stamp a freshly-written web-sourced row with the IMMUTABLE untrusted_source
    birth-mark AND activate the L5-I-α trust-floor.

    THREE atomic effects (all idempotent, all on the row just inserted):
      1. ``untrusted_source = 1`` — the visible, queryable, trigger-protected
         birth-mark (anti-laundering: a storage trigger forbids ever clearing it).
      2. ``derived_from = 'untrusted_web_source'`` — makes I-α treat the row as
         DERIVED (non-authored), flooring its authorship at the semantic kind
         prior (~0.52) instead of the authored floor (0.85).
      3. ``confidence = 0.50`` — aligns the stored self-confidence with the 0.5
         web provenance (the writer otherwise leaves it at the 1.0 default).
    Combined, (2)+(3) drop the trust from ~0.96 to ~0.79 — below an authored Drew
    memory (~0.92) — so web content cannot out-rank authored memory in recall,
    while remaining recallable (calm: rank LOW, never bury/bar).

    Does NOT raise on the immutability trigger (a re-stamp of an already-marked row
    sets 1->1, which the trigger permits). Returns a receipt. NEVER raises into
    the writer.
    """
    out: Dict[str, Any] = {"stamped": False, "memory_id": memory_id, "error": None}
    try:
        if not warden_migration_applied(conn):
            ensure_warden_migration(conn)
        # The column may STILL be absent if the migration failed (e.g. memories
        # table missing) — degrade rather than crash.
        if not warden_migration_applied(conn):
            out["error"] = "untrusted_source column unavailable; stamp skipped"
            return out
        conn.execute(
            "UPDATE memories "
            "SET untrusted_source = 1, "
            "    derived_from = COALESCE(derived_from, ?), "
            "    confidence = ? "
            "WHERE id = ?",
            (UNTRUSTED_DERIVED_FROM, WEB_PROVENANCE_CONFIDENCE, memory_id),
        )
        conn.commit()
        out["stamped"] = True
        return out
    except sqlite3.Error as e:  # pragma: no cover - defensive
        out["error"] = str(e)
        return out


def is_untrusted(conn: sqlite3.Connection, memory_id: int) -> bool:
    """True iff the row carries the untrusted_source birth-mark. Read-only; False
    if the column is absent (pre-migration) or the row is missing."""
    if not warden_migration_applied(conn):
        return False
    try:
        row = conn.execute(
            "SELECT untrusted_source FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0] == 1)

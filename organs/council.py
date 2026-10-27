"""Inner Council of Mages — L4 Piece 3 companion module.

Provides council session management: convene_council(), add_owner_input(),
get_session_history(), compute_decision_weight(), and related utilities.

This module follows the companion-module pattern established by
calibration_memory.py (L4 Piece 4) and sleep_loop.py (L2-C3):
  - Standalone: can be imported and used directly in scripts
  - Integrated: claude_memory.py imports and wires commands to its argparse
  - Testable: all functions accept db_path or conn arguments, no globals

Council members (bespoke persona-skills, NOT borrowed automations):
  - ORIN (Seer): surveys precedents, names assumptions, finds prior art
  - VAEL (Skeptic): names failure modes, challenges consensus, stress-tests
  - DESSA (Architect): traces downstream impact, maps system dependencies
  - KAEL-EM (Dreamer): lateral alternatives, cross-domain connections, reframes
  - SYNTHESIZER: synthesizes all four voices, names tensions, resolves.  The
    seat is the brain's DECLARED persona when one is declared (its skill
    folder is the seat's voice file), else the package's council-synthesizer.

Production invocation via `claude -p` with the seat's SKILL.md as --system-prompt.
Each member speaks in Round 1; the synthesizer synthesizes in Round 2; the
owner (the human who convened it) optionally adds input in Round 3.

Names a brain may already STORE (sessions written before 2026-10-15): the
synthesizer seat under the author's persona slug and Round 3 under the author's
first name.  Both still resolve (LEGACY_SPEAKER_ALIASES); nothing stored is
renamed, and new rows use the canonical slugs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COUNCIL_MEMBERS = {
    "orin": {
        "role": "seer",
        "title": "The Seer",
        "skill_folder": "council-seer",
        "description": "Surveys precedents, names assumptions, finds prior art in the library",
        "speak_order": 1,
    },
    "vael": {
        "role": "skeptic",
        "title": "The Skeptic",
        "skill_folder": "council-skeptic",
        "description": "Names failure modes, challenges consensus, stress-tests proposals",
        "speak_order": 2,
    },
    "dessa": {
        "role": "architect",
        "title": "The Architect",
        "skill_folder": "council-architect",
        "description": "Traces downstream impact, maps system dependencies, evaluates structure",
        "speak_order": 3,
    },
    "kael-em": {
        "role": "dreamer",
        "title": "The Dreamer",
        "skill_folder": "council-dreamer",
        "description": "Lateral alternatives, cross-domain connections, question reframes",
        "speak_order": 4,
    },
    "synthesizer": {
        "role": "synthesis",
        "title": "The Synthesizer",
        "skill_folder": "council-synthesizer",
        "description": "Synthesizes all council voices, names tensions, proposes resolution",
        "speak_order": 5,
    },
}

SYNTHESIZER = "synthesizer"

# Round 3's speaker: the human who convened the council.
OWNER_SPEAKER = "owner"

# Slugs a brain stored before the seat was generic (2026-10-15) -> the canonical slug.  Read on every input; never
# written again.
LEGACY_SPEAKER_ALIASES = {
    "milamber": SYNTHESIZER,
    "drew": OWNER_SPEAKER,
}


def canonical_speaker(speaker: str) -> str:
    """The canonical slug for `speaker` (a legacy slug maps to its replacement; anything else is returned as is)."""
    return LEGACY_SPEAKER_ALIASES.get(speaker, speaker)


def _persona_forge_status() -> dict:
    """voidloom_persona.forge_status() for this brain, or {} -- the persona module is optional to this organ."""
    try:
        import sys as _sys
        _here = str(Path(__file__).resolve().parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import voidloom_persona as _vp  # type: ignore
        return _vp.forge_status() or {}
    except Exception:
        return {}


def declared_synthesizer() -> dict:
    """Who holds the synthesizer seat: {"name", "skill_folder"}.

    The brain's declared persona (its name, and its skill folder from voidloom_persona) when one is declared; else
    name None and the package's council-synthesizer.  The package declares no persona, so a stranger's brain gets the
    package's synthesizer; an operator who declared one hears it in the seat."""
    st = _persona_forge_status()
    name = st.get("persona") if st.get("status") in ("forged", "declared-missing-skill") else None
    skill = st.get("skill") if name else None
    return {"name": name, "skill_folder": skill or COUNCIL_MEMBERS[SYNTHESIZER]["skill_folder"]}

VALID_TRIGGER_TYPES = ("manual", "auto_detect")

VALID_TOPIC_CATEGORIES = (
    "architecture", "strategy", "process", "personnel",
    "trade_off", "risk", "scope", "priority", "technical",
    "creative", "meta",
)

VALID_STATUSES = ("in_progress", "completed", "abandoned")

DEFAULT_PARTICIPANTS = list(COUNCIL_MEMBERS.keys())

MIGRATION_FILENAME = "migration_018_council.sql"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_council_table(db_path: str) -> dict:
    """Apply migration_018 if council_sessions table does not exist. Idempotent."""
    conn = _get_conn(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        if "council_sessions" in tables:
            return {"status": "ok", "action": "ensure_council_table", "result": "already_exists"}

        migration_path = Path(__file__).parent.parent / "migrations" / MIGRATION_FILENAME
        if not migration_path.exists():
            return {"status": "error", "message": f"{MIGRATION_FILENAME} not found"}

        sql = migration_path.read_text()
        conn.executescript(sql)
        conn.commit()
        return {"status": "ok", "action": "ensure_council_table", "result": "created"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# L5-I-β: council memory-freeze — a STAMP, not a lock.
#
# When a session convenes, we STAMP the substrate version it deliberates over:
#   substrate_version = {max_memory_id, max_provenance_id, foundation_hash}
# Members then read memories with ``id <= frozen_max`` — the "Stilled Library":
# a consistent snapshot of the brain for the duration of the deliberation, so a
# write that lands mid-session cannot shift the ground under the council.
#
# CRITICAL: this is a STAMP, never a DB LOCK. A long-lived transaction holding a
# read lock would DEADLOCK the nightly crons (sleep-loop, dreaming, belief sweep
# all write while a council might run). A stamp is a cheap recorded watermark
# that the (external) deliberation orchestrator + build_council_prompt honor in
# their own read queries — zero lock contention.
# ---------------------------------------------------------------------------

def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return False
    return column in cols


def _ensure_substrate_column(conn: sqlite3.Connection) -> bool:
    """Idempotently add council_sessions.substrate_version (TEXT JSON). Uses the
    _has_column guard (the dream_pipeline / migration_028 pattern) so a re-run is
    a no-op. Returns True if the column is present afterward. NEVER raises."""
    try:
        if not _has_column(conn, "council_sessions", "substrate_version"):
            conn.execute(
                "ALTER TABLE council_sessions ADD COLUMN substrate_version TEXT DEFAULT NULL"
            )
            conn.commit()
        return _has_column(conn, "council_sessions", "substrate_version")
    except sqlite3.Error:
        return False


def _compute_substrate_version(conn: sqlite3.Connection) -> dict:
    """Compute the freeze stamp: (max memory id, max provenance id,
    foundation-hash). The foundation-hash is a cheap fingerprint of the
    foundation set (the trust anchors) so a change to the bedrock between two
    sessions is detectable even if max ids are unchanged. Fail-graceful: any
    missing piece degrades to a sane default; NEVER raises."""
    try:
        frozen_max = conn.execute("SELECT MAX(id) FROM memories").fetchone()[0] or 0
    except sqlite3.Error:
        frozen_max = 0
    try:
        prov_max = conn.execute(
            "SELECT MAX(id) FROM memory_provenance").fetchone()[0] or 0
    except sqlite3.Error:
        prov_max = 0
    # foundation-hash: a fingerprint over (id, foundation_score) of the bedrock,
    # bounded so it is cheap regardless of corpus size.
    foundation_hash = ""
    try:
        rows = conn.execute(
            "SELECT id, foundation_score FROM memories "
            "WHERE foundation_score IS NOT NULL AND foundation_score >= 0.90 "
            "ORDER BY id"
        ).fetchall()
        basis = ";".join(f"{r[0]}:{round(float(r[1]), 4)}" for r in rows)
        foundation_hash = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    except (sqlite3.Error, TypeError, ValueError):
        foundation_hash = ""
    return {
        "frozen_max": int(frozen_max),
        "provenance_max": int(prov_max),
        "foundation_hash": foundation_hash,
        "stamped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def frozen_substrate(db_path: str, session_id: int) -> dict:
    """Read a session's freeze stamp. Returns the parsed substrate_version dict
    (with ``frozen_max`` the memory-id ceiling the council reads up to), or a
    status dict if the session/column is absent. The external deliberation
    orchestrator calls this to scope its memory reads to ``id <= frozen_max``
    (the Stilled Library). Read-only; NEVER raises."""
    try:
        conn = _get_conn(db_path)
    except sqlite3.Error as e:
        return {"status": "error", "message": str(e)}
    try:
        if not _has_column(conn, "council_sessions", "substrate_version"):
            return {"status": "no_stamp",
                    "message": "substrate_version column not present"}
        row = conn.execute(
            "SELECT substrate_version FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return {"status": "error", "message": f"Session {session_id} not found"}
        if not row["substrate_version"]:
            return {"status": "no_stamp", "session_id": session_id,
                    "message": "session carries no substrate_version stamp"}
        try:
            stamp = json.loads(row["substrate_version"])
        except (TypeError, ValueError):
            return {"status": "error", "message": "substrate_version is not valid JSON"}
        stamp["status"] = "ok"
        stamp["session_id"] = session_id
        return stamp
    finally:
        conn.close()


def stilled_context_memories(db_path: str, session_id: int,
                             query_ids: Optional[list[int]] = None,
                             limit: int = 5) -> list[dict]:
    """Return context memories visible to a FROZEN session — i.e. only memories
    with ``id <= frozen_max``. A memory added AFTER the stamp is INVISIBLE to the
    frozen session (the Stilled Library guarantee). If the session has no stamp,
    falls back to the most-recent memories (no freeze). Read-only; NEVER raises.

    ``query_ids`` optionally restricts to a candidate id set (the orchestrator's
    recall hits); the frozen ceiling is applied on top. Without it, returns the
    highest-id memories at or below the ceiling (a recency-bounded sample)."""
    stamp = frozen_substrate(db_path, session_id)
    frozen_max = stamp.get("frozen_max") if stamp.get("status") == "ok" else None
    try:
        conn = _get_conn(db_path)
    except sqlite3.Error:
        return []
    try:
        if query_ids:
            placeholders = ",".join("?" for _ in query_ids)
            params: list[Any] = list(query_ids)
            ceiling = ""
            if frozen_max is not None:
                ceiling = " AND id <= ?"
                params.append(int(frozen_max))
            rows = conn.execute(
                f"SELECT id, summary FROM memories WHERE id IN ({placeholders})"
                f"{ceiling} ORDER BY id DESC LIMIT ?",
                params + [int(limit)],
            ).fetchall()
        else:
            if frozen_max is not None:
                rows = conn.execute(
                    "SELECT id, summary FROM memories WHERE id <= ? "
                    "ORDER BY id DESC LIMIT ?",
                    (int(frozen_max), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, summary FROM memories ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [{"id": r["id"], "summary": r["summary"]} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def convene_council(
    db_path: str,
    topic: str,
    trigger_type: str = "manual",
    topic_category: Optional[str] = None,
    participants: Optional[list[str]] = None,
    context_memories: Optional[list[dict]] = None,
) -> dict:
    """Begin a new council session.

    Creates a row in council_sessions and returns the session ID.
    The actual deliberation (calling `claude -p` per member) is orchestrated
    externally — this function only records the session start.

    Args:
        db_path: Path to the memory database.
        topic: The question or decision under deliberation.
        trigger_type: 'manual' or 'auto_detect'.
        topic_category: Optional classification (architecture, strategy, etc.).
        participants: List of council member slugs. Default: all five.
        context_memories: Optional list of {id, summary} dicts to seed context.

    Returns:
        Dict with session_id, status, and metadata.
    """
    if trigger_type not in VALID_TRIGGER_TYPES:
        return {"status": "error", "message": f"Invalid trigger_type: {trigger_type}"}
    if topic_category and topic_category not in VALID_TOPIC_CATEGORIES:
        return {"status": "error", "message": f"Invalid topic_category: {topic_category}"}

    members = [canonical_speaker(m) for m in (participants or DEFAULT_PARTICIPANTS)]
    for m in members:
        if m not in COUNCIL_MEMBERS:
            return {"status": "error", "message": f"Unknown council member: {m}"}

    ensure_council_table(db_path)

    conn = _get_conn(db_path)
    try:
        # L5-I-β: STAMP (not lock) the substrate this session deliberates over,
        # so members read a consistent "Stilled Library" (id <= frozen_max) and a
        # mid-session write cannot shift the ground. Fail-graceful — if the column
        # cannot be added (e.g. a read-only/odd schema) we simply skip the stamp
        # and the session behaves as before (no freeze).
        substrate_json = None
        if _ensure_substrate_column(conn):
            try:
                substrate = _compute_substrate_version(conn)
                substrate_json = json.dumps(substrate)
            except Exception:
                substrate_json = None

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if substrate_json is not None:
            cursor = conn.execute(
                """INSERT INTO council_sessions
                   (trigger_type, topic, topic_category, participants, created_at,
                    status, substrate_version)
                   VALUES (?, ?, ?, ?, ?, 'in_progress', ?)""",
                (trigger_type, topic, topic_category, json.dumps(members), now,
                 substrate_json),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO council_sessions
                   (trigger_type, topic, topic_category, participants, created_at, status)
                   VALUES (?, ?, ?, ?, ?, 'in_progress')""",
                (trigger_type, topic, topic_category, json.dumps(members), now),
            )
        session_id = cursor.lastrowid
        conn.commit()

        return {
            "status": "ok",
            "action": "convene_council",
            "session_id": session_id,
            "topic": topic,
            "trigger_type": trigger_type,
            "topic_category": topic_category,
            "participants": members,
            "context_memories": context_memories or [],
            "created_at": now,
            "substrate_version": (json.loads(substrate_json)
                                  if substrate_json is not None else None),
        }
    finally:
        conn.close()


def record_contribution(
    db_path: str,
    session_id: int,
    speaker: str,
    content: str,
    round_num: int,
) -> dict:
    """Record a council member's contribution to the transcript.

    Appends to the transcript JSON array and increments rounds_completed
    when a new round number appears.

    Args:
        db_path: Path to the memory database.
        session_id: The council session to append to.
        speaker: Council member slug (e.g., 'orin', 'vael'), 'synthesizer', or
            'owner'.  A legacy slug is accepted and stored as its canonical one.
        content: The member's contribution text.
        round_num: Which round (1 = initial, 2 = synthesis, 3 = owner).

    Returns:
        Dict with status and updated transcript length.
    """
    speaker = canonical_speaker(speaker)
    if speaker not in COUNCIL_MEMBERS and speaker != OWNER_SPEAKER:
        return {"status": "error", "message": f"Unknown speaker: {speaker}"}

    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT transcript, rounds_completed, status FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Session {session_id} not found"}
        if row["status"] != "in_progress":
            return {"status": "error", "message": f"Session {session_id} is {row['status']}"}

        transcript = json.loads(row["transcript"]) if row["transcript"] else []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        entry = {
            "speaker": speaker,
            "content": content,
            "round": round_num,
            "timestamp": now,
        }
        transcript.append(entry)

        new_rounds = max(row["rounds_completed"] or 0, round_num)

        # If the speaker is the synthesizer in round 2, also store synthesis shortcut
        updates = {
            "transcript": json.dumps(transcript),
            "rounds_completed": new_rounds,
        }
        if speaker == SYNTHESIZER and round_num == 2:
            updates["synthesis"] = content

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [session_id]
        conn.execute(
            f"UPDATE council_sessions SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "record_contribution",
            "session_id": session_id,
            "speaker": speaker,
            "round": round_num,
            "transcript_length": len(transcript),
        }
    finally:
        conn.close()


def add_owner_input(
    db_path: str,
    session_id: int,
    owner_input: str,
) -> dict:
    """Add the owner's optional closing voice to a council session.

    This is Round 3 — the owner weighs in after the synthesis.
    Also records the input as a transcript entry (speaker 'owner').  The
    column it fills keeps its schema name (council_sessions.drew_input,
    migration 018): renaming a column is a data migration.

    Args:
        db_path: Path to the memory database.
        session_id: The council session.
        owner_input: The owner's input text.

    Returns:
        Dict with status.
    """
    drew_input = owner_input  # the column's name (schema), not a label
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status, transcript, rounds_completed FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Session {session_id} not found"}
        if row["status"] != "in_progress":
            return {"status": "error", "message": f"Session {session_id} is {row['status']}"}

        # Record in transcript
        transcript = json.loads(row["transcript"]) if row["transcript"] else []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        transcript.append({
            "speaker": OWNER_SPEAKER,
            "content": drew_input,
            "round": 3,
            "timestamp": now,
        })

        conn.execute(
            """UPDATE council_sessions
               SET drew_input = ?, transcript = ?, rounds_completed = ?
               WHERE id = ?""",
            (drew_input, json.dumps(transcript), max(row["rounds_completed"] or 0, 3), session_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "add_owner_input",
            "session_id": session_id,
            "transcript_length": len(transcript),
        }
    finally:
        conn.close()


def add_drew_input(db_path: str, session_id: int, drew_input: str) -> dict:
    """The pre-2026-10-15 name of add_owner_input, kept for its callers (API, not a label)."""
    return add_owner_input(db_path, session_id, drew_input)


def complete_session(
    db_path: str,
    session_id: int,
    decision_weight_score: Optional[float] = None,
    decision_memory_id: Optional[int] = None,
) -> dict:
    """Mark a council session as completed.

    Optionally records the decision weight and links to the reflection memory
    that captures the decision outcome.

    Args:
        db_path: Path to the memory database.
        session_id: The council session.
        decision_weight_score: 0.0 (trivial) to 1.0 (irreversible). Optional.
        decision_memory_id: ID of the reflection memory storing the decision. Optional.

    Returns:
        Dict with status and session summary.
    """
    if decision_weight_score is not None and not (0.0 <= decision_weight_score <= 1.0):
        return {"status": "error", "message": "decision_weight_score must be 0.0-1.0"}

    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status, topic, rounds_completed, synthesis FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Session {session_id} not found"}
        if row["status"] == "completed":
            return {"status": "error", "message": f"Session {session_id} already completed"}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE council_sessions
               SET status = 'completed', completed_at = ?,
                   decision_weight_score = ?, decision_memory_id = ?
               WHERE id = ?""",
            (now, decision_weight_score, decision_memory_id, session_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "complete_session",
            "session_id": session_id,
            "topic": row["topic"],
            "rounds_completed": row["rounds_completed"],
            "has_synthesis": row["synthesis"] is not None,
            "decision_weight_score": decision_weight_score,
            "decision_memory_id": decision_memory_id,
            "completed_at": now,
        }
    finally:
        conn.close()


def abandon_session(db_path: str, session_id: int, reason: Optional[str] = None) -> dict:
    """Mark a council session as abandoned.

    Args:
        db_path: Path to the memory database.
        session_id: The council session.
        reason: Optional reason for abandonment.

    Returns:
        Dict with status.
    """
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Session {session_id} not found"}
        if row["status"] != "in_progress":
            return {"status": "error", "message": f"Session {session_id} is {row['status']}"}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE council_sessions SET status = 'abandoned', completed_at = ? WHERE id = ?",
            (now, session_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "abandon_session",
            "session_id": session_id,
            "reason": reason,
        }
    finally:
        conn.close()


def get_session(db_path: str, session_id: int) -> dict:
    """Retrieve a single council session with full transcript.

    Args:
        db_path: Path to the memory database.
        session_id: The council session.

    Returns:
        Dict with full session data.
    """
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Session {session_id} not found"}

        return {
            "status": "ok",
            "action": "get_session",
            "session": _row_to_dict(row),
        }
    finally:
        conn.close()


def get_session_history(
    db_path: str,
    limit: int = 20,
    status: Optional[str] = None,
    topic_category: Optional[str] = None,
) -> dict:
    """List council sessions, most recent first.

    Args:
        db_path: Path to the memory database.
        limit: Max sessions to return.
        status: Filter by status (in_progress, completed, abandoned).
        topic_category: Filter by topic category.

    Returns:
        Dict with session list (without full transcripts for efficiency).
    """
    conn = _get_conn(db_path)
    try:
        query = """
            SELECT id, trigger_type, topic, topic_category, rounds_completed,
                   synthesis IS NOT NULL as has_synthesis,
                   drew_input IS NOT NULL as has_owner_input,
                   decision_weight_score, decision_memory_id,
                   participants, created_at, completed_at, status
            FROM council_sessions
        """
        conditions = []
        params: list[Any] = []

        if status:
            if status not in VALID_STATUSES:
                return {"status": "error", "message": f"Invalid status: {status}"}
            conditions.append("status = ?")
            params.append(status)

        if topic_category:
            conditions.append("topic_category = ?")
            params.append(topic_category)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        sessions = []
        for row in rows:
            d = dict(row)
            d["participants"] = json.loads(d["participants"]) if d["participants"] else []
            sessions.append(d)

        return {
            "status": "ok",
            "action": "get_session_history",
            "sessions": sessions,
            "count": len(sessions),
        }
    finally:
        conn.close()


def compute_decision_weight(
    db_path: str,
    session_id: int,
    reversibility: float = 0.5,
    scope: float = 0.5,
    urgency: float = 0.5,
) -> dict:
    """Compute a decision weight score for a council session.

    The weight is a composite of three dimensions:
      - reversibility: 0.0 = easily reversible, 1.0 = irreversible
      - scope: 0.0 = affects one component, 1.0 = affects entire system
      - urgency: 0.0 = can defer, 1.0 = must decide now

    The composite formula weights reversibility highest (0.5), then scope (0.3),
    then urgency (0.2) — because irreversible decisions are the most consequential
    for counterfactual analysis (L4 Piece 5).

    Args:
        db_path: Path to the memory database.
        session_id: The council session.
        reversibility: 0.0-1.0 (how hard to undo).
        scope: 0.0-1.0 (how much of the system is affected).
        urgency: 0.0-1.0 (how time-sensitive).

    Returns:
        Dict with computed weight and dimensional breakdown.
    """
    for name, val in [("reversibility", reversibility), ("scope", scope), ("urgency", urgency)]:
        if not (0.0 <= val <= 1.0):
            return {"status": "error", "message": f"{name} must be 0.0-1.0, got {val}"}

    weight = 0.5 * reversibility + 0.3 * scope + 0.2 * urgency
    weight = round(weight, 4)

    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM council_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Session {session_id} not found"}

        conn.execute(
            "UPDATE council_sessions SET decision_weight_score = ? WHERE id = ?",
            (weight, session_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "compute_decision_weight",
            "session_id": session_id,
            "decision_weight_score": weight,
            "dimensions": {
                "reversibility": reversibility,
                "scope": scope,
                "urgency": urgency,
            },
            "formula": "0.5 * reversibility + 0.3 * scope + 0.2 * urgency",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Council invocation helpers (for production `claude -p` calls)
# ---------------------------------------------------------------------------

def build_council_prompt(
    speaker: str,
    topic: str,
    context_memories: Optional[list[dict]] = None,
    prior_contributions: Optional[list[dict]] = None,
) -> str:
    """Build the user prompt for a council member's `claude -p` invocation.

    Args:
        speaker: Council member slug.
        topic: The deliberation topic.
        context_memories: List of {id, summary} dicts for context.
        prior_contributions: Transcript entries from earlier speakers in this round.

    Returns:
        The user prompt string.
    """
    parts = [f"Council topic: {topic}"]

    if context_memories:
        parts.append("\nRelevant library context:")
        for m in context_memories[:5]:
            parts.append(f"  - Memory #{m.get('id', '?')}: {m.get('summary', 'no summary')}")

    if prior_contributions:
        parts.append("\nPrior council contributions this round:")
        for c in prior_contributions:
            parts.append(f"\n[{c['speaker'].upper()}]:\n{c['content']}")

    parts.append(f"\nSpeak from your lens as {COUNCIL_MEMBERS[canonical_speaker(speaker)]['title']} in 2-3 paragraphs.")

    return "\n".join(parts)


def build_synthesis_prompt(
    topic: str,
    round1_transcript: list[dict],
    context_memories: Optional[list[dict]] = None,
) -> str:
    """Build the synthesizer's prompt from Round 1 contributions.

    Args:
        topic: The deliberation topic.
        round1_transcript: All Round 1 contributions.
        context_memories: Optional memory context.

    Returns:
        The synthesis prompt string.
    """
    parts = [f"Council topic: {topic}"]

    if context_memories:
        parts.append("\nLibrary context:")
        for m in context_memories[:5]:
            parts.append(f"  - Memory #{m.get('id', '?')}: {m.get('summary', 'no summary')}")

    parts.append("\nThe council has spoken. Here are their contributions:\n")
    for entry in round1_transcript:
        slug = canonical_speaker(entry["speaker"])
        member = COUNCIL_MEMBERS.get(slug, {})
        title = member.get("title", slug)
        parts.append(f"[{slug.upper()} — {title}]:")
        parts.append(entry["content"])
        parts.append("")

    parts.append(
        "Synthesize the council's perspectives. Name the tensions between members. "
        "Propose a resolution that honors the full spectrum of input — do not simply "
        "pick a winner. If genuine disagreement exists, name it and propose how to "
        "navigate it. 2-3 paragraphs."
    )

    return "\n".join(parts)


def get_skill_path(speaker: str, base_path: Optional[str] = None) -> Path:
    """Resolve the SKILL.md path for a council member.

    The synthesizer seat is the declared persona's skill folder when that
    folder holds a SKILL.md under the base, else the package's
    council-synthesizer (declared_synthesizer()).

    Args:
        speaker: Council member slug (a legacy slug resolves to its seat).
        base_path: Override base path (for testing). Default: .claude/skills/.

    Returns:
        Path to the SKILL.md file.
    """
    speaker = canonical_speaker(speaker)
    member = COUNCIL_MEMBERS.get(speaker)
    if not member:
        raise ValueError(f"Unknown council member: {speaker}")

    base = Path(base_path) if base_path else Path(".claude/skills")
    if speaker == SYNTHESIZER:
        declared = declared_synthesizer()["skill_folder"]
        package = base / member["skill_folder"] / "SKILL.md"
        # The declared persona's file when it exists; else the package's when IT exists; else (neither laid) the
        # declared persona's path -- this function has always built a path, not checked one.
        if (base / declared / "SKILL.md").is_file() or not package.is_file():
            return base / declared / "SKILL.md"
        return package
    return base / member["skill_folder"] / "SKILL.md"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing JSON fields."""
    d = dict(row)
    for json_field in ("transcript", "participants"):
        if d.get(json_field) and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except json.JSONDecodeError:
                pass
    return d


# ---------------------------------------------------------------------------
# Unit tests (run with: python council.py --test)
# ---------------------------------------------------------------------------

def _run_tests():
    """Run unit tests against a temporary in-memory-like database."""
    import tempfile

    test_db = tempfile.mktemp(suffix=".db")
    passed = 0
    failed = 0
    errors = []

    def assert_eq(actual, expected, label):
        nonlocal passed, failed
        if actual == expected:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {label}: expected {expected!r}, got {actual!r}")

    def assert_true(value, label):
        nonlocal passed, failed
        if value:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {label}: expected truthy, got {value!r}")

    try:
        # Setup: create minimal schema
        conn = sqlite3.connect(test_db)
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.execute("INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES ('schema_version', '3.8-test', datetime('now'))")
        conn.commit()
        conn.close()

        # T1: ensure_council_table creates the table
        result = ensure_council_table(test_db)
        assert_eq(result["status"], "ok", "T1: ensure_council_table status")
        assert_eq(result["result"], "created", "T1: ensure_council_table result")

        # T2: ensure_council_table is idempotent
        result = ensure_council_table(test_db)
        assert_eq(result["result"], "already_exists", "T2: idempotent ensure")

        # T3: convene_council creates a session
        result = convene_council(test_db, "Should we restructure the sleep loop?", topic_category="architecture")
        assert_eq(result["status"], "ok", "T3: convene status")
        assert_true(result["session_id"] >= 1, "T3: session_id positive")
        session_id = result["session_id"]

        # T4: invalid trigger_type
        result = convene_council(test_db, "test", trigger_type="invalid")
        assert_eq(result["status"], "error", "T4: invalid trigger_type")

        # T5: invalid topic_category
        result = convene_council(test_db, "test", topic_category="invalid")
        assert_eq(result["status"], "error", "T5: invalid topic_category")

        # T6: unknown council member
        result = convene_council(test_db, "test", participants=["unknown"])
        assert_eq(result["status"], "error", "T6: unknown participant")

        # T7: record_contribution for Orin
        result = record_contribution(test_db, session_id, "orin", "The library holds three relevant precedents.", 1)
        assert_eq(result["status"], "ok", "T7: orin contribution status")
        assert_eq(result["transcript_length"], 1, "T7: transcript length 1")

        # T8: record_contribution for Vael
        result = record_contribution(test_db, session_id, "vael", "Two failure modes. First: idempotency.", 1)
        assert_eq(result["status"], "ok", "T8: vael contribution status")
        assert_eq(result["transcript_length"], 2, "T8: transcript length 2")

        # T9: record_contribution for Dessa
        result = record_contribution(test_db, session_id, "dessa", "Downstream impact: three systems.", 1)
        assert_eq(result["status"], "ok", "T9: dessa contribution status")

        # T10: record_contribution for Kael-Em
        result = record_contribution(test_db, session_id, "kael-em", "What if we inverted the architecture?", 1)
        assert_eq(result["status"], "ok", "T10: kael-em contribution status")

        # T11: the synthesizer's synthesis in round 2
        result = record_contribution(test_db, session_id, SYNTHESIZER,
                                     "The council surfaces a tension between structural purity and pragmatic shipping.", 2)
        assert_eq(result["status"], "ok", "T11: synthesizer synthesis status")

        # T12: Verify synthesis was stored
        sess = get_session(test_db, session_id)
        assert_true(sess["session"]["synthesis"] is not None, "T12: synthesis stored")
        assert_eq(sess["session"]["rounds_completed"], 2, "T12: rounds_completed = 2")

        # T13: add_owner_input
        result = add_owner_input(test_db, session_id, "Ship it, but address Vael's idempotency concern first.")
        assert_eq(result["status"], "ok", "T13: drew_input status")
        assert_eq(result["transcript_length"], 6, "T13: transcript length 6")

        # T14: Verify full transcript
        sess = get_session(test_db, session_id)
        assert_eq(len(sess["session"]["transcript"]), 6, "T14: full transcript length")
        assert_eq(sess["session"]["transcript"][0]["speaker"], "orin", "T14: first speaker orin")
        assert_eq(sess["session"]["transcript"][-1]["speaker"], OWNER_SPEAKER, "T14: last speaker owner")

        # T15: compute_decision_weight
        result = compute_decision_weight(test_db, session_id,
                                         reversibility=0.3, scope=0.7, urgency=0.4)
        assert_eq(result["status"], "ok", "T15: weight status")
        expected_weight = round(0.5 * 0.3 + 0.3 * 0.7 + 0.2 * 0.4, 4)
        assert_eq(result["decision_weight_score"], expected_weight, "T15: weight value")

        # T16: invalid weight dimensions
        result = compute_decision_weight(test_db, session_id, reversibility=1.5)
        assert_eq(result["status"], "error", "T16: invalid reversibility")

        # T17: complete_session
        result = complete_session(test_db, session_id, decision_weight_score=0.44, decision_memory_id=9999)
        assert_eq(result["status"], "ok", "T17: complete status")
        assert_true(result["completed_at"] is not None, "T17: completed_at set")

        # T18: cannot complete again
        result = complete_session(test_db, session_id)
        assert_eq(result["status"], "error", "T18: double complete blocked")

        # T19: cannot add contribution to completed session
        result = record_contribution(test_db, session_id, "orin", "late contribution", 1)
        assert_eq(result["status"], "error", "T19: contribution to completed blocked")

        # T20: get_session_history
        result = get_session_history(test_db, limit=10)
        assert_eq(result["status"], "ok", "T20: history status")
        assert_true(result["count"] >= 1, "T20: history count >= 1")

        # T21: get_session_history with status filter
        result = get_session_history(test_db, status="completed")
        assert_true(result["count"] >= 1, "T21: completed filter")

        # T22: abandon_session
        result2 = convene_council(test_db, "Should we add a sixth council member?")
        sid2 = result2["session_id"]
        result = abandon_session(test_db, sid2, reason="topic no longer relevant")
        assert_eq(result["status"], "ok", "T22: abandon status")

        # T23: cannot add to abandoned session
        result = record_contribution(test_db, sid2, "orin", "too late", 1)
        assert_eq(result["status"], "error", "T23: contribution to abandoned blocked")

        # T24: build_council_prompt
        prompt = build_council_prompt("orin", "Should we restructure?",
                                      context_memories=[{"id": 123, "summary": "test memory"}])
        assert_true("Council topic:" in prompt, "T24: prompt has topic")
        assert_true("Memory #123" in prompt, "T24: prompt has memory ref")
        assert_true("The Seer" in prompt, "T24: prompt has title")

        # T25: build_synthesis_prompt
        transcript = [
            {"speaker": "orin", "content": "Orin's view"},
            {"speaker": "vael", "content": "Vael's view"},
        ]
        prompt = build_synthesis_prompt("test topic", transcript)
        assert_true("ORIN" in prompt, "T25: synthesis prompt has ORIN")
        assert_true("VAEL" in prompt, "T25: synthesis prompt has VAEL")
        assert_true("Synthesize" in prompt, "T25: synthesis instruction present")

        # T26: get_skill_path
        path = get_skill_path("orin")
        assert_true("council-seer" in str(path), "T26: orin skill path")
        _sk = Path(tempfile.mkdtemp(suffix="_skills"))
        (_sk / "council-synthesizer").mkdir()
        (_sk / "council-synthesizer" / "SKILL.md").write_text("x")
        path = get_skill_path(SYNTHESIZER, base_path=str(_sk))
        assert_eq(path, _sk / "council-synthesizer" / "SKILL.md", "T26: synthesizer skill path (package laid only)")
        for _legacy, _canon in LEGACY_SPEAKER_ALIASES.items():
            if _canon == SYNTHESIZER:
                assert_eq(get_skill_path(_legacy, base_path=str(_sk)), path, "T26: legacy seat slug resolves")
        import shutil as _sh
        _sh.rmtree(str(_sk), ignore_errors=True)

        # T27: get_skill_path with base_path override
        path = get_skill_path("vael", base_path="/tmp/skills")
        assert_eq(str(path), "/tmp/skills/council-skeptic/SKILL.md", "T27: vael base override")

        # T28: invalid speaker in get_skill_path
        try:
            get_skill_path("nobody")
            failed += 1
            errors.append("T28: expected ValueError for unknown speaker")
        except ValueError:
            passed += 1

        # T29: COUNCIL_MEMBERS has correct speak order
        orders = sorted([(m["speak_order"], slug) for slug, m in COUNCIL_MEMBERS.items()])
        assert_eq([o[1] for o in orders], ["orin", "vael", "dessa", "kael-em", SYNTHESIZER],
                  "T29: speak order correct")

        # T30: session not found errors
        result = get_session(test_db, 99999)
        assert_eq(result["status"], "error", "T30: session not found")
        result = record_contribution(test_db, 99999, "orin", "ghost", 1)
        assert_eq(result["status"], "error", "T30b: contribution to nonexistent")
        result = add_owner_input(test_db, 99999, "ghost")
        assert_eq(result["status"], "error", "T30c: owner input to nonexistent")
        result = complete_session(test_db, 99999)
        assert_eq(result["status"], "error", "T30d: complete nonexistent")
        result = abandon_session(test_db, 99999)
        assert_eq(result["status"], "error", "T30e: abandon nonexistent")

        # T31: auto_detect trigger type
        result = convene_council(test_db, "Auto-detected topic", trigger_type="auto_detect")
        assert_eq(result["status"], "ok", "T31: auto_detect trigger")
        assert_eq(result["trigger_type"], "auto_detect", "T31: trigger type value")

        # T32: custom participants
        result = convene_council(test_db, "Partial council", participants=["orin", "vael"])
        assert_eq(result["status"], "ok", "T32: partial council status")
        assert_eq(result["participants"], ["orin", "vael"], "T32: participants list")

        # T33: unknown speaker in record_contribution
        sid3 = convene_council(test_db, "test")["session_id"]
        result = record_contribution(test_db, sid3, "gandalf", "wrong world", 1)
        assert_eq(result["status"], "error", "T33: unknown speaker blocked")

        # T34: the owner as speaker in record_contribution (valid)
        result = record_contribution(test_db, sid3, OWNER_SPEAKER, "the owner speaks directly", 3)
        assert_eq(result["status"], "ok", "T34: owner as direct speaker")

        # T35: history with topic_category filter
        result = get_session_history(test_db, topic_category="architecture")
        assert_true(result["count"] >= 1, "T35: category filter works")

        # T36: invalid status filter
        result = get_session_history(test_db, status="invalid")
        assert_eq(result["status"], "error", "T36: invalid status filter")

        # T37: compute_decision_weight on nonexistent session
        result = compute_decision_weight(test_db, 99999)
        assert_eq(result["status"], "error", "T37: weight on nonexistent")

        # T38: edge case — all dimensions at 0
        sid4 = convene_council(test_db, "trivial")["session_id"]
        result = compute_decision_weight(test_db, sid4, reversibility=0.0, scope=0.0, urgency=0.0)
        assert_eq(result["decision_weight_score"], 0.0, "T38: all zero weight")

        # T39: edge case — all dimensions at 1
        result = compute_decision_weight(test_db, sid4, reversibility=1.0, scope=1.0, urgency=1.0)
        assert_eq(result["decision_weight_score"], 1.0, "T39: all one weight")

        # T40: add_owner_input to abandoned session
        result = add_owner_input(test_db, sid2, "trying to add to abandoned")
        assert_eq(result["status"], "error", "T40: owner input to abandoned blocked")

        # T41: the synthesis shortcut stored correctly
        sid5 = convene_council(test_db, "synthesis test")["session_id"]
        synthesis_text = "The council converges on approach B with Vael's safety modification."
        record_contribution(test_db, sid5, SYNTHESIZER, synthesis_text, 2)
        sess = get_session(test_db, sid5)
        assert_eq(sess["session"]["synthesis"], synthesis_text, "T41: synthesis shortcut")

        # T42: a non-synthesizer round 2 does NOT set synthesis
        sid6 = convene_council(test_db, "non-synth test")["session_id"]
        record_contribution(test_db, sid6, "orin", "round 2 addendum", 2)
        sess = get_session(test_db, sid6)
        assert_eq(sess["session"]["synthesis"], None, "T42: no synthesis from a non-synthesizer r2")

        # T43: complete_session with no weight or memory
        result = complete_session(test_db, sid5)
        assert_eq(result["status"], "ok", "T43: complete without weight")
        assert_eq(result["decision_weight_score"], None, "T43: no weight")

    finally:
        try:
            os.unlink(test_db)
        except OSError:
            pass

    print(f"\n{'='*60}")
    print(f"Council unit tests: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    for e in errors:
        print(f"  {e}")
    if failed == 0:
        print("  ALL TESTS PASSED")

    return {"passed": passed, "failed": failed, "errors": errors}


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        result = _run_tests()
        sys.exit(0 if result["failed"] == 0 else 1)
    else:
        print("Usage: python council.py --test")
        print("  Or import and use as a companion module.")

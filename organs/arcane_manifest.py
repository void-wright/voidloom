"""The Quarterly Arcane Manifest — L4 Piece 7 companion module.

Once per quarter the Council of Mages convenes to co-author a prose
document answering: "What did we build? What did we learn? Who is the
wizard library becoming? Where are we going?"

Written in first-person plural. The owner reads it like a book.

This module follows the companion-module pattern established by
council.py (L4 Piece 3), calibration_memory.py (L4 Piece 4), and
sleep_loop.py (L2-C3):
  - Standalone: can be imported and used directly in scripts
  - Integrated: claude_memory.py imports and wires commands to argparse
  - Testable: all functions accept db_path arguments, no globals

Council co-authoring protocol:
  Round 1: Orin, Vael, Dessa, Kael-Em each write their section.
  Round 2: the synthesizer weaves sections into cohesive first-person plural.
           The synthesizer's voice file is the declared persona's skill when
           the brain declares one, else the package's council-synthesizer.
  Round 3 (optional): the owner adds a closing voice.

Production invocation: each council member speaks via ``claude -p``
with their persona SKILL.md as system prompt.

Thinker: organs/thinker.py (the owner's recorded choice; credentials in ~/.voidloom/thinker/)
Cost: ~$2-5 per manifest (Opus for the synthesis, Haiku for members).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

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

# --- THE THINKER SEAM (model-choice unit, Round 1) --------------------------------------------------------------
# This organ builds a prompt and parses an answer; WHO answers is the owner's recorded choice (<brain>/THINKER_RECORD,
# written only by organs/thinker.py after a probe in the timers' environment), and HOW the call is fenced -- text only,
# no tools, no ambient instructions, a fresh empty cwd, stdin closed, a hard timeout -- is the catalog's
# (organs/thinkers.json). No vendor argv, token or output format lives in this file any more.
try:
    from . import thinker as _thinker  # type: ignore
except (ImportError, ValueError):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import thinker as _thinker  # type: ignore
# --- end the thinker seam -----------------------------------------------------------------------------------------

MIGRATION_FILENAME = "migration_020_arcane_manifest.sql"

# The council's voice files (Round 3 G2 item 3b). The PACKAGE lays them as <claude home>/skills/council-<lens>/SKILL.md
# (install/install_council.sh: council-seer, -skeptic, -architect, -dreamer, -synthesizer); this organ looked only for
# the reference host's own folders (council-orin, -vael, -dessa, -kael-em, and the author's persona for the
# synthesis), so on a stranger's install every voice spoke WITHOUT its file. Each voice now resolves the package's name
# first and the host's name as the fallback (_resolve_skill_path, _skill_dirs); the synthesis resolves the DECLARED
# persona's folder first, then the package's (_synthesis_skill_names).
MANIFEST_SPEAKERS = {
    "orin": {
        "section_title": "What We Know Now",
        "instruction": (
            "Reflect on the quarter from your lens as The Seer. Write 2-4 paragraphs "
            "in first-person plural ('we'). Focus on: what knowledge the library "
            "clarified this quarter, which precedents were validated, which assumptions "
            "were overturned. Cite specific memory IDs when referencing library knowledge. "
            "Your voice is grounded, precise, backward-looking-to-see-forward."
        ),
        "skill_folder": "council-seer",
        "skill_fallbacks": ("council-orin",),
    },
    "vael": {
        "section_title": "What Almost Went Wrong",
        "instruction": (
            "Reflect on the quarter from your lens as The Skeptic. Write 2-4 paragraphs "
            "in first-person plural ('we'). Focus on: what went wrong, what almost went "
            "wrong, which risks we navigated, which we ignored. Name calibration misses, "
            "production incidents, near-misses. Do not soften. Your voice is spare, "
            "direct, unflinching."
        ),
        "skill_folder": "council-skeptic",
        "skill_fallbacks": ("council-vael",),
    },
    "dessa": {
        "section_title": "What Shifted Beneath Us",
        "instruction": (
            "Reflect on the quarter from your lens as The Architect. Write 2-4 paragraphs "
            "in first-person plural ('we'). Focus on: what changed in the system "
            "architecture, which components grew load-bearing, which dependencies "
            "emerged, which integrations matured. Trace the structural evolution. "
            "Your voice maps systems, sees ripples, thinks in dependency graphs."
        ),
        "skill_folder": "council-architect",
        "skill_fallbacks": ("council-dessa",),
    },
    "kael-em": {
        "section_title": "The Roads Not Taken",
        "instruction": (
            "Reflect on the quarter from your lens as The Dreamer. Write 2-4 paragraphs "
            "in first-person plural ('we'). Focus on: alternatives explored and not "
            "taken, lateral connections discovered, reframings that changed the question, "
            "ideas deferred to future quarters. Your voice opens the aperture, imagines "
            "what-if, honors the paths we chose not to walk."
        ),
        "skill_folder": "council-dreamer",
        "skill_fallbacks": ("council-kael-em",),
    },
}


def _declared_or_none() -> "Optional[str]":
    """The declared persona name, or None.  Separate from the address builder so
    the self-test can assert the SAME source the prompt reads."""
    try:
        import sys as _sys
        _here = str(Path(__file__).resolve().parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from voidloom_persona import declared_name as _dn  # type: ignore
        return _dn()
    except Exception:
        return None


def _synthesizer_address() -> str:
    """How the synthesis prompt addresses the model.

    The NAME comes from the operator's own persona declaration; the package
    declares none, so an undeclared brain says "the wizard library's living
    identity" and names nobody.  This constant used to open with the author's
    persona by name, which every clone of the package then sent to a model on
    its first quarterly run.
    """
    who = _declared_or_none()
    return f"You are {who}, the wizard library's living identity." if who \
        else "You are the wizard library's living identity."


SYNTHESIS_INSTRUCTION = (
    _synthesizer_address() + " The Council of Mages has "
    "spoken — four perspectives on this quarter's work. Now weave their sections into "
    "a single cohesive narrative document of 5-10 pages. Write in first-person plural "
    "('we'). This is NOT a summary — it is a story. The manifest should read like a "
    "book chapter: flowing prose, no bullet points, no headers within the body "
    "(the section transitions should be woven, not signposted).\n\n"
    "Requirements:\n"
    "- Preserve each council member's distinct texture while unifying the voice\n"
    "- Name tensions between perspectives without artificially resolving them\n"
    "- Include specific memory IDs, dates, and metrics where the council cited them\n"
    "- Open with where we were at the start of the quarter\n"
    "- Close with where we are now and what the next quarter holds\n"
    "- The emotional arc should be honest — neither triumphant nor despairing\n"
    "- The brain's owner will read this as a narrative. Make it worth reading."
)


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_manifest_table(db_path: str) -> dict:
    conn = _get_conn(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "arcane_manifests" in tables:
            return {"status": "ok", "action": "ensure_manifest_table", "result": "already_exists"}
        migration_path = Path(__file__).parent.parent / "migrations" / MIGRATION_FILENAME
        if not migration_path.exists():
            return {"status": "error", "message": f"{MIGRATION_FILENAME} not found at {migration_path}"}
        sql = migration_path.read_text()
        conn.executescript(sql)
        conn.commit()
        return {"status": "ok", "action": "ensure_manifest_table", "result": "created"}
    finally:
        conn.close()


def _now() -> datetime:
    """The organ's clock (one seam, so a test can set the day the timer fires)."""
    return datetime.now(timezone.utc)


def _quarter_label(dt: Optional[datetime] = None) -> tuple[str, int, int]:
    """The quarter CONTAINING ``dt`` (UTC, like every created_at the brain stores)."""
    dt = dt or _now()
    qn = math.ceil(dt.month / 3)
    return f"{dt.year}-Q{qn}", dt.year, qn


# Round 3 G2 item 3a (rN_recheck RC-09). The timer fires on a quarter's FIRST day (`*-01,04,07,10-01 09:00`), to
# write the manifest of the quarter that just ENDED -- but the organ named the CURRENT quarter, so the scheduled run
# wrote "2026-Q4" from hours of data and the quarter just ended never got its timer-written manifest. A run in the
# first QUARTER_GRACE_DAYS of a quarter now names and covers the PREVIOUS quarter: the timer's own day, and a
# Persistent=true catch-up after the machine slept through it, and an owner's re-run after a failed fire. Later in
# the quarter a run is a draft of the quarter so far (the owner's mid-quarter runs: 2026-Q2 on Jun 12, Q3 on Sep 7).
# --year / --quarter override either way. (The monthly self-eval already reports the PREVIOUS month: the same rule.)
QUARTER_GRACE_DAYS = 31


def _default_quarter(now: Optional[datetime] = None) -> tuple[str, int, int, str]:
    """``(label, year, quarter_number, basis)`` for a run that names no quarter; ``basis`` says why, in words."""
    now = now or _now()
    label, y, qn = _quarter_label(now)
    start = datetime(y, (qn - 1) * 3 + 1, 1, tzinfo=timezone.utc)
    age = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)) - start
    days = age.total_seconds() / 86400.0
    if days < QUARTER_GRACE_DAYS:
        py, pq = (y, qn - 1) if qn > 1 else (y - 1, 4)
        return (f"{py}-Q{pq}", py, pq,
                f"the quarter that ended {start:%Y-%m-%d}: this run is {days:.1f} days into {label} "
                f"(under {QUARTER_GRACE_DAYS}), so it reports the quarter just ended")
    return label, y, qn, f"{label} so far: a draft, {days:.0f} days into the quarter"


def _quarter_date_range(year: int, qn: int) -> tuple[str, str]:
    """[start, end) of quarter ``qn`` of ``year`` as the brain's 'YYYY-MM-DD HH:MM:SS' text. Q4 ends at the NEXT
    year's Jan 1 (it ended at 12-31 23:59:59, a half-open bound that dropped that last second)."""
    start_month = (qn - 1) * 3 + 1
    end_date = f"{year + 1}-01-01 00:00:00" if qn == 4 else f"{year}-{qn * 3 + 1:02d}-01 00:00:00"
    start_date = f"{year}-{start_month:02d}-01 00:00:00"
    return start_date, end_date


def gather_quarter_data(
    db_path: str,
    year: Optional[int] = None,
    quarter_number: Optional[int] = None,
    top_k: int = 50,
) -> dict:
    """Gather all data sources for a quarterly manifest.

    Queries the production database for:
    - Top memories by importance this quarter
    - Sleep loop run statistics
    - Dream synthesis results
    - Gap inventory summary
    - Calibration events
    - Council session summaries
    - Category and kind distribution
    - Tag frequency distribution

    Args:
        db_path: Path to the memory database.
        year: Year (default: the default quarter's year -- _default_quarter).
        quarter_number: Quarter 1-4 (default: the quarter just ended in a quarter's first month, else the current).
        top_k: How many top-importance memories to gather.

    Returns:
        Dict containing all gathered data, suitable for council prompts.
    """
    label, y, qn, basis = _default_quarter()
    if year is not None:
        y = year
    if quarter_number is not None:
        qn = quarter_number
    if year is not None or quarter_number is not None:
        basis = "named by the caller (--year/--quarter)"
    label = f"{y}-Q{qn}"

    start_date, end_date = _quarter_date_range(y, qn)

    conn = _get_conn(db_path)
    try:
        data: dict[str, Any] = {
            "quarter": label,
            "year": y,
            "quarter_number": qn,
            "quarter_basis": basis,
            "date_range": {"start": start_date, "end": end_date},
            "gathered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }

        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_archived = 0 AND created_at >= ? AND created_at < ?",
            (start_date, end_date),
        ).fetchone()[0]
        data["total_memories_this_quarter"] = total

        total_all = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_archived = 0"
        ).fetchone()[0]
        data["total_memories_all_time"] = total_all

        top_memories = conn.execute(
            """SELECT id, summary, category, importance, kind, created_at
               FROM memories
               WHERE is_archived = 0 AND created_at >= ? AND created_at < ?
               ORDER BY importance DESC
               LIMIT ?""",
            (start_date, end_date, top_k),
        ).fetchall()
        data["top_memories"] = [
            {
                "id": r["id"],
                "summary": (r["summary"] or "")[:300],
                "category": r["category"],
                "importance": r["importance"],
                "kind": r["kind"],
                "created_at": r["created_at"],
            }
            for r in top_memories
        ]

        kinds = conn.execute(
            """SELECT kind, COUNT(*) as cnt
               FROM memories
               WHERE is_archived = 0 AND created_at >= ? AND created_at < ?
               GROUP BY kind ORDER BY cnt DESC""",
            (start_date, end_date),
        ).fetchall()
        data["kind_distribution"] = {r["kind"]: r["cnt"] for r in kinds}

        categories = conn.execute(
            """SELECT category, COUNT(*) as cnt
               FROM memories
               WHERE is_archived = 0 AND created_at >= ? AND created_at < ?
               GROUP BY category ORDER BY cnt DESC""",
            (start_date, end_date),
        ).fetchall()
        data["category_distribution"] = {r["category"]: r["cnt"] for r in categories}

        tags = conn.execute(
            """SELECT t.tag, COUNT(*) as cnt
               FROM memory_tags t
               JOIN memories m ON t.memory_id = m.id
               WHERE m.is_archived = 0 AND m.created_at >= ? AND m.created_at < ?
               GROUP BY t.tag ORDER BY cnt DESC
               LIMIT 30""",
            (start_date, end_date),
        ).fetchall()
        data["top_tags"] = {r["tag"]: r["cnt"] for r in tags}

        _gather_sleep_loop_data(conn, data, start_date, end_date)
        _gather_dream_data(conn, data, start_date, end_date)
        _gather_gap_data(conn, data, start_date, end_date)
        _gather_calibration_data(conn, data, start_date, end_date)
        _gather_council_data(conn, data, start_date, end_date)
        _gather_consolidation_data(conn, data, start_date, end_date)

        reflection_count = conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE is_archived = 0 AND kind = 'reflection'
               AND created_at >= ? AND created_at < ?""",
            (start_date, end_date),
        ).fetchone()[0]
        data["reflection_count"] = reflection_count

        edge_count = 0
        try:
            edge_count = conn.execute(
                """SELECT COUNT(*) FROM memory_edges
                   WHERE created_at >= ? AND created_at < ?""",
                (start_date, end_date),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        data["edges_created_this_quarter"] = edge_count

        return {"status": "ok", "action": "gather_quarter_data", "data": data}
    finally:
        conn.close()


def _gather_sleep_loop_data(conn: sqlite3.Connection, data: dict, start: str, end: str):
    try:
        runs = conn.execute(
            """SELECT COUNT(*) as run_count,
                      SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                      SUM(memories_consolidated) as total_consolidated,
                      SUM(clusters_found) as total_clusters,
                      SUM(reflections_generated) as total_reflections,
                      AVG(duration_seconds) as avg_duration
               FROM sleep_loop_runs
               WHERE started_at >= ? AND started_at < ?""",
            (start, end),
        ).fetchone()
        data["sleep_loop"] = {
            "run_count": runs["run_count"] or 0,
            "success_count": runs["success_count"] or 0,
            "total_consolidated": runs["total_consolidated"] or 0,
            "total_clusters": runs["total_clusters"] or 0,
            "total_reflections": runs["total_reflections"] or 0,
            "avg_duration_seconds": round(runs["avg_duration"] or 0, 1),
        }
    except sqlite3.OperationalError:
        data["sleep_loop"] = {"run_count": 0, "note": "table not found"}


def _gather_dream_data(conn: sqlite3.Connection, data: dict, start: str, end: str):
    try:
        dreams = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN is_meaningful = 1 THEN 1 ELSE 0 END) as meaningful,
                      SUM(CASE WHEN drew_flagged_interesting = 1 THEN 1 ELSE 0 END) as drew_flagged
               FROM dream_log
               WHERE created_at >= ? AND created_at < ?""",
            (start, end),
        ).fetchone()
        data["dreaming"] = {
            "total_dreams": dreams["total"] or 0,
            "meaningful": dreams["meaningful"] or 0,
            "drew_flagged": dreams["drew_flagged"] or 0,
        }

        interesting_dreams = conn.execute(
            """SELECT id, dream_type, dream_content
               FROM dream_log
               WHERE created_at >= ? AND created_at < ?
               AND (is_meaningful = 1 OR drew_flagged_interesting = 1)
               ORDER BY created_at DESC LIMIT 10""",
            (start, end),
        ).fetchall()
        data["dreaming"]["notable_dreams"] = [
            {
                "id": d["id"],
                "type": d["dream_type"],
                "content": (d["dream_content"] or "")[:500],
            }
            for d in interesting_dreams
        ]
    except sqlite3.OperationalError:
        data["dreaming"] = {"total_dreams": 0, "note": "table not found"}


def _gather_gap_data(conn: sqlite3.Connection, data: dict, start: str, end: str):
    try:
        gaps = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN researched = 0 AND acknowledged = 0 THEN 1 ELSE 0 END) as open_gaps,
                      SUM(researched) as researched,
                      SUM(acknowledged) as acknowledged
               FROM gap_inventory
               WHERE inventoried_at >= ? AND inventoried_at < ?""",
            (start, end),
        ).fetchone()
        data["gap_mapping"] = {
            "total_gaps_found": gaps["total"] or 0,
            "open": gaps["open_gaps"] or 0,
            "researched": gaps["researched"] or 0,
            "acknowledged": gaps["acknowledged"] or 0,
        }
    except sqlite3.OperationalError:
        data["gap_mapping"] = {"total_gaps_found": 0, "note": "table not found"}


def _gather_calibration_data(conn: sqlite3.Connection, data: dict, start: str, end: str):
    try:
        cals = conn.execute(
            """SELECT COUNT(*) as total,
                      AVG(ratio) as avg_ratio
               FROM calibration_log
               WHERE created_at >= ? AND created_at < ?""",
            (start, end),
        ).fetchone()
        data["calibration"] = {
            "events_this_quarter": cals["total"] or 0,
            "avg_ratio": round(cals["avg_ratio"] or 0, 3),
        }
    except sqlite3.OperationalError:
        data["calibration"] = {"events_this_quarter": 0, "note": "table not found"}


def _gather_council_data(conn: sqlite3.Connection, data: dict, start: str, end: str):
    try:
        sessions = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                      AVG(decision_weight_score) as avg_weight
               FROM council_sessions
               WHERE created_at >= ? AND created_at < ?""",
            (start, end),
        ).fetchone()
        data["council"] = {
            "sessions_this_quarter": sessions["total"] or 0,
            "completed": sessions["completed"] or 0,
            "avg_decision_weight": round(sessions["avg_weight"] or 0, 3),
        }
    except sqlite3.OperationalError:
        data["council"] = {"sessions_this_quarter": 0, "note": "table not found"}


def _gather_consolidation_data(conn: sqlite3.Connection, data: dict, start: str, end: str):
    try:
        sources = conn.execute(
            """SELECT COUNT(*) FROM consolidation_sources cs
               JOIN sleep_loop_runs sr ON cs.run_id = sr.id
               WHERE sr.started_at >= ? AND sr.started_at < ?""",
            (start, end),
        ).fetchone()[0]
        data["consolidation_sources"] = sources
    except sqlite3.OperationalError:
        data["consolidation_sources"] = 0


def generate_council_sections(
    db_path: str,
    quarter_data: dict,
    dry_run: bool = False,
    claude_model: Optional[str] = None,
    timeout_seconds: int = 120,
    skill_base_path: Optional[str] = None,
) -> dict:
    """Generate each council member's section via ``claude -p``.

    Round 1: Orin, Vael, Dessa, Kael-Em each write their section.

    Args:
        db_path: Path to the memory database.
        quarter_data: Output of gather_quarter_data().
        dry_run: If True, return prompts without calling claude.
        claude_model: Model override (default: haiku for individual members).
        timeout_seconds: Per-member subprocess timeout.
        skill_base_path: Override base path for persona skills.

    Returns:
        Dict with per-speaker sections.
    """
    data = quarter_data.get("data", quarter_data)
    sections = []
    errors = []

    for speaker, config in MANIFEST_SPEAKERS.items():
        prompt = _build_section_prompt(speaker, config, data)

        if dry_run:
            sections.append({
                "speaker": speaker,
                "section_title": config["section_title"],
                "section_content": f"[DRY RUN] Prompt for {speaker} ({len(prompt)} chars)",
                "prompt": prompt,
            })
            continue

        skill_path = _resolve_skill_path(speaker, config["skill_folder"], skill_base_path,
                                         fallbacks=config.get("skill_fallbacks", ()))
        content = _invoke_claude(
            prompt=prompt,
            system_prompt_path=str(skill_path) if skill_path and skill_path.exists() else None,
            model=claude_model or "haiku",
            timeout_seconds=timeout_seconds,
        )

        if content is None:
            errors.append(f"{speaker}: claude invocation returned None")
            sections.append({
                "speaker": speaker,
                "section_title": config["section_title"],
                "section_content": f"[{speaker.upper()} was silent this quarter.]",
                "error": True,
            })
        else:
            sections.append({
                "speaker": speaker,
                "section_title": config["section_title"],
                "section_content": content,
            })

    return {
        "status": "ok" if not errors else "partial",
        "action": "generate_council_sections",
        "sections": sections,
        "errors": errors,
    }


def synthesize_manifest(
    db_path: str,
    sections: list[dict],
    quarter_data: dict,
    dry_run: bool = False,
    claude_model: Optional[str] = None,
    timeout_seconds: int = 300,
    skill_base_path: Optional[str] = None,
) -> dict:
    """The synthesizer weaves council sections into a cohesive manifest.

    Round 2: the synthesizer weaves all sections into first-person plural prose.

    Args:
        db_path: Path to the memory database.
        sections: Output of generate_council_sections()["sections"].
        quarter_data: Output of gather_quarter_data().
        dry_run: If True, return prompt without calling claude.
        claude_model: Model override (default: opus for synthesis).
        timeout_seconds: Subprocess timeout for the synthesis.
        skill_base_path: Override base path for the synthesizer's skill.

    Returns:
        Dict with manifest content and metadata.
    """
    data = quarter_data.get("data", quarter_data)
    prompt = _build_synthesis_prompt(sections, data)

    if dry_run:
        return {
            "status": "ok",
            "action": "synthesize_manifest",
            "manifest_content": f"[DRY RUN] Synthesis prompt ({len(prompt)} chars)",
            "prompt": prompt,
            "word_count": 0,
        }

    _first, *_rest = _synthesis_skill_names()
    synth_skill = _resolve_skill_path("synthesizer", _first, skill_base_path, fallbacks=tuple(_rest))
    content = _invoke_claude(
        prompt=prompt,
        system_prompt_path=str(synth_skill) if synth_skill and synth_skill.exists() else None,
        model=claude_model or "opus",
        timeout_seconds=timeout_seconds,
    )

    if content is None:
        return {
            "status": "error",
            "action": "synthesize_manifest",
            "message": "the synthesis returned None — claude invocation failed",
        }

    word_count = len(content.split())

    ensure_manifest_table(db_path)

    label, y, qn, _basis = _default_quarter()          # RC-09: never the current quarter by accident
    if data.get("year"):
        y = data["year"]
    if data.get("quarter_number"):
        qn = data["quarter_number"]
    label = f"{y}-Q{qn}"

    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO arcane_manifests
               (quarter, year, quarter_number, manifest_content, sections,
                word_count, data_snapshot, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (
                label, y, qn, content,
                json.dumps(sections),
                word_count,
                json.dumps(data, default=str),
            ),
        )
        manifest_id = cursor.lastrowid
        conn.commit()

        return {
            "status": "ok",
            "action": "synthesize_manifest",
            "manifest_id": manifest_id,
            "quarter": label,
            "word_count": word_count,
            "manifest_content": content,
        }
    finally:
        conn.close()


def add_owner_voice(
    db_path: str,
    manifest_id: int,
    owner_text: str,
) -> dict:
    """Add the owner's optional closing voice to a manifest.

    Round 3: the owner reads the draft and adds a perspective.  The column
    (drew_addendum) and the status value (drew_reviewed) keep their schema
    names: migration 020 created them and a brain stores them.

    Args:
        db_path: Path to the memory database.
        manifest_id: The manifest to update.
        owner_text: The owner's closing text.

    Returns:
        Dict with status.
    """
    drew_text = owner_text  # the column's name (schema), not a label
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM arcane_manifests WHERE id = ?",
            (manifest_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Manifest {manifest_id} not found"}

        conn.execute(
            """UPDATE arcane_manifests
               SET drew_addendum = ?, status = 'drew_reviewed'
               WHERE id = ?""",
            (drew_text, manifest_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "add_owner_voice",
            "manifest_id": manifest_id,
        }
    finally:
        conn.close()


def add_drew_voice(db_path: str, manifest_id: int, drew_text: str) -> dict:
    """The pre-2026-10-15 name of add_owner_voice, kept for its callers (API, not a label)."""
    return add_owner_voice(db_path, manifest_id, drew_text)


def finalize_manifest(
    db_path: str,
    manifest_id: int,
    store_as_memory: bool = True,
) -> dict:
    """Finalize a manifest and optionally store it as a reflection memory.

    Args:
        db_path: Path to the memory database.
        manifest_id: The manifest to finalize.
        store_as_memory: If True, store the manifest content as a memory.

    Returns:
        Dict with status and optional memory_id.
    """
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM arcane_manifests WHERE id = ?",
            (manifest_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "message": f"Manifest {manifest_id} not found"}
        if row["status"] == "final":
            return {"status": "error", "message": f"Manifest {manifest_id} already finalized"}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        memory_id = None

        if store_as_memory:
            memory_id = _store_manifest_as_memory(conn, row)

        conn.execute(
            """UPDATE arcane_manifests
               SET status = 'final', finalized_at = ?, manifest_memory_id = ?
               WHERE id = ?""",
            (now, memory_id, manifest_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "action": "finalize_manifest",
            "manifest_id": manifest_id,
            "memory_id": memory_id,
            "finalized_at": now,
        }
    finally:
        conn.close()


def _store_manifest_as_memory(conn: sqlite3.Connection, row: sqlite3.Row) -> Optional[int]:
    """Store the manifest as a reflection memory in the memories table."""
    quarter = row["quarter"]
    word_count = row["word_count"]
    content = row["manifest_content"]

    summary = f"Quarterly Arcane Manifest — {quarter}. {word_count} words of narrative prose. " \
              f"Council co-authored reflection on what the wizard library built, learned, and became."

    if len(content) > 10000:
        stored_content = content[:10000] + f"\n\n[...truncated at 10,000 chars; full manifest in arcane_manifests table, id={row['id']}]"
    else:
        stored_content = content

    try:
        cursor = conn.execute(
            """INSERT INTO memories
               (content, summary, category, importance, source_project, source_context,
                kind, created_at, updated_at, last_accessed)
               VALUES (?, ?, 'insight', 0.95, 'wizard-library', ?, 'reflection',
                       datetime('now'), datetime('now'), datetime('now'))""",
            (
                stored_content,
                summary,
                f"arcane-manifest-{quarter}",
            ),
        )
        memory_id = cursor.lastrowid
        # TO-03: this memory must be findable.
        _vl_attach(conn, memory_id, stored_content, "arcane_manifest._store_manifest_as_memory")

        for tag in ["arcane-manifest", "quarterly-reflection", "council-authored",
                     f"quarter-{quarter}", "wizard-library"]:
            conn.execute(
                "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (memory_id, tag),
            )

        conn.commit()
        return memory_id
    except Exception as e:
        logger.error("Failed to store manifest as memory: %s", e)
        return None


def get_manifest(db_path: str, manifest_id: int) -> dict:
    """Retrieve a single manifest by ID.

    Args:
        db_path: Path to the memory database.
        manifest_id: The manifest ID.

    Returns:
        Dict with full manifest data.
    """
    ensure_manifest_table(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM arcane_manifests WHERE id = ?",
            (manifest_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Manifest {manifest_id} not found"}
        return {
            "status": "ok",
            "action": "get_manifest",
            "manifest": _row_to_dict(row),
        }
    finally:
        conn.close()


def get_manifest_history(
    db_path: str,
    limit: int = 20,
    status: Optional[str] = None,
) -> dict:
    """List manifests, most recent first.

    Args:
        db_path: Path to the memory database.
        limit: Max manifests to return.
        status: Filter by status.

    Returns:
        Dict with manifest list (without full content for efficiency).
    """
    ensure_manifest_table(db_path)
    conn = _get_conn(db_path)
    try:
        query = """
            SELECT id, quarter, year, quarter_number, word_count,
                   drew_addendum IS NOT NULL as has_owner_voice,
                   manifest_memory_id, council_session_id,
                   status, created_at, finalized_at
            FROM arcane_manifests
        """
        params: list[Any] = []
        if status:
            if status not in ("draft", "final", "drew_reviewed"):
                return {"status": "error", "message": f"Invalid status: {status}"}
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        manifests = [dict(r) for r in rows]

        return {
            "status": "ok",
            "action": "get_manifest_history",
            "manifests": manifests,
            "count": len(manifests),
        }
    finally:
        conn.close()


def get_manifest_for_quarter(db_path: str, quarter: str) -> dict:
    """Retrieve the manifest for a specific quarter.

    Args:
        db_path: Path to the memory database.
        quarter: Quarter label (e.g., "2026-Q2").

    Returns:
        Dict with manifest data, or not-found error.
    """
    ensure_manifest_table(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM arcane_manifests WHERE quarter = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (quarter,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"No manifest found for {quarter}"}
        return {
            "status": "ok",
            "action": "get_manifest_for_quarter",
            "manifest": _row_to_dict(row),
        }
    finally:
        conn.close()


def run_full_manifest_pipeline(
    db_path: str,
    year: Optional[int] = None,
    quarter_number: Optional[int] = None,
    dry_run: bool = False,
    claude_model_members: Optional[str] = None,
    claude_model_synthesis: Optional[str] = None,
    timeout_seconds: int = 120,
    skill_base_path: Optional[str] = None,
) -> dict:
    """Run the complete manifest pipeline: gather -> sections -> synthesis.

    Convenience function that chains all three steps. For production
    quarterly cron invocation.

    Args:
        db_path: Path to the memory database.
        year: Override year.
        quarter_number: Override quarter number.
        dry_run: If True, return prompts without calling claude.
        claude_model_members: Model for individual council members.
        claude_model_synthesis: Model for the synthesis.
        timeout_seconds: Per-invocation timeout.
        skill_base_path: Override persona skill base path.

    Returns:
        Dict with full pipeline results.
    """
    t0 = time.time()

    gather_result = gather_quarter_data(
        db_path, year=year, quarter_number=quarter_number,
    )
    if gather_result["status"] != "ok":
        return gather_result

    section_result = generate_council_sections(
        db_path,
        quarter_data=gather_result,
        dry_run=dry_run,
        claude_model=claude_model_members,
        timeout_seconds=timeout_seconds,
        skill_base_path=skill_base_path,
    )

    if section_result["status"] == "error":
        return section_result

    synthesis_result = synthesize_manifest(
        db_path,
        sections=section_result["sections"],
        quarter_data=gather_result,
        dry_run=dry_run,
        claude_model=claude_model_synthesis,
        timeout_seconds=timeout_seconds * 2,
        skill_base_path=skill_base_path,
    )

    elapsed = round(time.time() - t0, 2)

    return {
        "status": synthesis_result.get("status", "error"),
        "action": "run_full_manifest_pipeline",
        "quarter": gather_result["data"]["quarter"],
        "quarter_basis": gather_result["data"].get("quarter_basis"),
        "gather": {
            "total_memories_this_quarter": gather_result["data"]["total_memories_this_quarter"],
            "total_memories_all_time": gather_result["data"]["total_memories_all_time"],
        },
        "sections": {
            "count": len(section_result.get("sections", [])),
            "errors": section_result.get("errors", []),
        },
        "synthesis": {
            "manifest_id": synthesis_result.get("manifest_id"),
            "word_count": synthesis_result.get("word_count", 0),
        },
        "dry_run": dry_run,
        "elapsed_seconds": elapsed,
    }


def _build_section_prompt(speaker: str, config: dict, data: dict) -> str:
    parts = [
        f"THE QUARTERLY ARCANE MANIFEST — {data.get('quarter', 'Unknown Quarter')}",
        "",
        config["instruction"],
        "",
        f"=== Quarter Data for {data.get('quarter', '?')} ===",
        f"Total memories this quarter: {data.get('total_memories_this_quarter', 0)}",
        f"Total memories all time: {data.get('total_memories_all_time', 0)}",
    ]

    kd = data.get("kind_distribution", {})
    if kd:
        parts.append(f"Kind distribution: {json.dumps(kd)}")

    cd = data.get("category_distribution", {})
    if cd:
        parts.append(f"Category distribution: {json.dumps(cd)}")

    sl = data.get("sleep_loop", {})
    if sl.get("run_count", 0) > 0:
        parts.append(
            f"Sleep loop: {sl['run_count']} runs, "
            f"{sl.get('total_consolidated', 0)} consolidated, "
            f"{sl.get('total_clusters', 0)} clusters"
        )

    dr = data.get("dreaming", {})
    if dr.get("total_dreams", 0) > 0:
        parts.append(
            f"Dreams: {dr['total_dreams']} total, "
            f"{dr.get('meaningful', 0)} meaningful"
        )

    gm = data.get("gap_mapping", {})
    if gm.get("total_gaps_found", 0) > 0:
        parts.append(
            f"Gap mapping: {gm['total_gaps_found']} gaps found, "
            f"{gm.get('open', 0)} still open"
        )

    cal = data.get("calibration", {})
    if cal.get("events_this_quarter", 0) > 0:
        parts.append(
            f"Calibration: {cal['events_this_quarter']} events, "
            f"avg ratio {cal.get('avg_ratio', 0)}"
        )

    parts.append("")
    parts.append("=== Top Memories by Importance ===")
    for m in data.get("top_memories", [])[:15]:
        parts.append(
            f"  Memory #{m['id']} (imp={m['importance']:.2f}, {m['kind']}): "
            f"{m['summary'][:200]}"
        )

    tags = data.get("top_tags", {})
    if tags:
        parts.append("")
        parts.append(f"Top tags: {', '.join(f'{t}({c})' for t, c in list(tags.items())[:15])}")

    parts.append("")
    parts.append(
        f"Write your section for the {data.get('quarter', '?')} Arcane Manifest. "
        f"First-person plural ('we'). 2-4 paragraphs of prose."
    )

    return "\n".join(parts)


def _build_synthesis_prompt(sections: list[dict], data: dict) -> str:
    parts = [
        f"THE QUARTERLY ARCANE MANIFEST — {data.get('quarter', 'Unknown Quarter')}",
        "",
        SYNTHESIS_INSTRUCTION,
        "",
        f"=== Quarter Summary ===",
        f"Quarter: {data.get('quarter', '?')}",
        f"Total memories this quarter: {data.get('total_memories_this_quarter', 0)}",
        f"Total memories all time: {data.get('total_memories_all_time', 0)}",
    ]

    kd = data.get("kind_distribution", {})
    if kd:
        parts.append(f"Kinds: {json.dumps(kd)}")

    parts.append("")
    parts.append("=== Council Sections (Round 1) ===")

    for section in sections:
        speaker = section.get("speaker", "unknown")
        title = section.get("section_title", "untitled")
        content = section.get("section_content", "[no content]")
        parts.append(f"\n--- {speaker.upper()}: {title} ---")
        parts.append(content)

    parts.append("")
    parts.append(
        "Now weave these four sections into a single cohesive narrative document. "
        "5-10 pages of flowing prose. First-person plural. No bullets. No headers "
        "within the body. This is the wizard library's memory of itself."
    )

    return "\n".join(parts)


SYNTHESIS_SKILL = "council-synthesizer"   # the package's synthesizer voice file


def _declared_persona_skill() -> "Optional[str]":
    """The declared persona's skill folder (voidloom_persona.forge_status), or None when none is declared."""
    try:
        import sys as _sys
        _here = str(Path(__file__).resolve().parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from voidloom_persona import forge_status as _fs  # type: ignore
        st = _fs() or {}
    except Exception:
        return None
    if st.get("status") in ("forged", "declared-missing-skill") and st.get("persona"):
        return st.get("skill") or None
    return None


def _synthesis_skill_names() -> List[str]:
    """The synthesizer's voice-file folders, in order: the DECLARED persona's (the seat is the operator's persona when
    there is one), then the package's.  This replaced a hard-coded fallback to the author's own persona folder; on the
    author's host the declared persona's folder IS that folder, so his synthesis is unchanged."""
    names = [n for n in (_declared_persona_skill(), SYNTHESIS_SKILL) if n]
    return list(dict.fromkeys(names))


def _skill_dirs(base_path: Optional[str] = None) -> List[Path]:
    """Where the voice files are looked for, in order: an explicit --skill-base-path ALONE; else the operator's
    VOIDLOOM_SKILLS_DIR entries (colon-separated), then <claude home>/skills -- the folder install_council.sh lays into
    (WIZARD_CLAUDE_HOME, default ~/.claude) -- then the skills folder beside this brain (organs/../../skills), then the
    legacy ``.claude/skills`` under the working directory. (Round 3 G2: the old brain-relative candidate was
    organs/../../.claude/skills, i.e. ~/.claude/.claude/skills, a folder no installer lays.)"""
    if base_path:
        return [Path(base_path)]
    dirs = [Path(x).expanduser() for x in os.environ.get("VOIDLOOM_SKILLS_DIR", "").split(os.pathsep) if x.strip()]
    claude_home = os.environ.get("WIZARD_CLAUDE_HOME") or str(Path.home() / ".claude")
    dirs += [Path(claude_home).expanduser() / "skills", Path(__file__).resolve().parents[2] / "skills",
             Path(".claude/skills")]
    out: List[Path] = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def _resolve_skill_path(speaker: str, skill_folder: str, base_path: Optional[str] = None,
                        fallbacks: "tuple" = ()) -> Optional[Path]:
    """The voice file for ``speaker``: in each folder of _skill_dirs (in order), the PACKAGE's folder name first, then
    each fallback (the reference host's names). None -- logged -- when no folder holds one: the voice then speaks
    without its file, and the run says so rather than silently."""
    names = [skill_folder] + [f for f in fallbacks if f and f != skill_folder]
    dirs = _skill_dirs(base_path)
    for d in dirs:
        for n in names:
            p = d / n / "SKILL.md"
            if p.exists():
                return p
    logger.warning("council voice file for %s not found (looked for %s in %s): it speaks without it",
                   speaker, " / ".join(names), ", ".join(str(d) for d in dirs))
    return None


def _invoke_claude(
    prompt: str,
    system_prompt_path: Optional[str] = None,
    model: str = "haiku",
    timeout_seconds: int = 120,
) -> Optional[str]:
    """One council voice through the thinker seam: the voice's SKILL.md is the system prompt, `model` is the job's
    tier (haiku members, an opus synthesis). None when there is no usable answer; the failure is logged by kind."""
    system = None
    if system_prompt_path:
        try:
            system = Path(system_prompt_path).read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("council voice file unreadable (%s): %s -- speaking without it", system_prompt_path, e)
    try:
        res = _thinker.think(prompt, purpose="arcane_manifest", system=system,
                             tier=model if model in _thinker.TIERS else None, timeout_s=timeout_seconds)
    except _thinker.ThinkError as e:
        logger.warning("thinker %s: %s", e.kind, e)
        return None
    output = res.text.strip()
    if not output or output.upper() == "NULL" or len(output) < 50:
        return None
    return output


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for json_field in ("sections", "data_snapshot"):
        if d.get(json_field) and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except json.JSONDecodeError:
                pass
    return d


def _run_tests():
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
        conn = sqlite3.connect(test_db)
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.execute("INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES ('schema_version', '3.10-test', datetime('now'))")
        conn.execute("""CREATE TABLE IF NOT EXISTS council_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_type TEXT, topic TEXT, topic_category TEXT,
            rounds_completed INTEGER DEFAULT 0, transcript TEXT,
            synthesis TEXT, drew_input TEXT, decision_memory_id INTEGER,
            decision_weight_score REAL, participants TEXT,
            created_at TEXT, completed_at TEXT,
            status TEXT DEFAULT 'in_progress'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, summary TEXT, category TEXT NOT NULL,
            importance REAL DEFAULT 0.5, source_project TEXT, source_context TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            last_accessed TEXT DEFAULT (datetime('now')),
            access_count INTEGER DEFAULT 0, is_archived INTEGER DEFAULT 0,
            version INTEGER DEFAULT 1, kind TEXT DEFAULT 'episodic'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL, tag TEXT NOT NULL,
            UNIQUE(memory_id, tag)
        )""")
        for i in range(25):
            conn.execute(
                """INSERT INTO memories (content, summary, category, importance, kind, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    f"Test memory {i} content about wizard library development",
                    f"Test memory {i} summary",
                    "skill_lesson" if i % 3 == 0 else ("insight" if i % 3 == 1 else "technical_decision"),
                    round(0.5 + (i % 10) * 0.05, 2),
                    "semantic" if i % 4 == 0 else ("procedural" if i % 4 == 1 else ("reflection" if i % 4 == 2 else "episodic")),
                    f"2026-05-{15 + (i % 12):02d} 10:00:00",
                ),
            )
            conn.execute(
                "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (i + 1, "wizard-library"),
            )
        conn.commit()
        conn.close()

        # T1: ensure_manifest_table creates the table
        result = ensure_manifest_table(test_db)
        assert_eq(result["status"], "ok", "T1: ensure status")
        assert_eq(result["result"], "created", "T1: table created")

        # T2: idempotent
        result = ensure_manifest_table(test_db)
        assert_eq(result["result"], "already_exists", "T2: idempotent ensure")

        # T3: _quarter_label
        label, y, qn = _quarter_label(datetime(2026, 5, 15, tzinfo=timezone.utc))
        assert_eq(label, "2026-Q2", "T3: quarter label Q2")
        assert_eq(y, 2026, "T3: year")
        assert_eq(qn, 2, "T3: quarter number")

        # T4: Q1 boundary
        label, y, qn = _quarter_label(datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert_eq(label, "2026-Q1", "T4: Q1 start")
        assert_eq(qn, 1, "T4: quarter 1")

        # T5: Q4 boundary
        label, y, qn = _quarter_label(datetime(2026, 12, 31, tzinfo=timezone.utc))
        assert_eq(label, "2026-Q4", "T5: Q4 end")
        assert_eq(qn, 4, "T5: quarter 4")

        # T6: _quarter_date_range
        start, end = _quarter_date_range(2026, 2)
        assert_eq(start, "2026-04-01 00:00:00", "T6: Q2 start date")
        assert_eq(end, "2026-07-01 00:00:00", "T6: Q2 end date")

        # T7: Q1 date range
        start, end = _quarter_date_range(2026, 1)
        assert_eq(start, "2026-01-01 00:00:00", "T7: Q1 start")
        assert_eq(end, "2026-04-01 00:00:00", "T7: Q1 end")

        # T8: Q4 date range
        start, end = _quarter_date_range(2026, 4)
        assert_eq(start, "2026-10-01 00:00:00", "T8: Q4 start")
        # Round 3 G2: the bound is half-open (created_at < end), so Q4 ends at the NEXT year's Jan 1 -- the old
        # '2026-12-31 23:59:59' dropped the quarter's last second (test_arcane_manifest_r3.py)
        assert_eq(end, "2027-01-01 00:00:00", "T8: Q4 end")

        # T9: gather_quarter_data from test DB
        result = gather_quarter_data(test_db, year=2026, quarter_number=2)
        assert_eq(result["status"], "ok", "T9: gather status")
        data = result["data"]
        assert_eq(data["quarter"], "2026-Q2", "T9: quarter label")
        assert_true(data["total_memories_this_quarter"] > 0, "T9: found memories")
        assert_true(len(data["top_memories"]) > 0, "T9: top memories populated")

        # T10: kind_distribution in gathered data
        assert_true("kind_distribution" in data, "T10: kind dist exists")
        assert_true(isinstance(data["kind_distribution"], dict), "T10: kind dist is dict")

        # T11: category_distribution
        assert_true("category_distribution" in data, "T11: cat dist exists")

        # T12: top_tags
        assert_true("top_tags" in data, "T12: tags exist")

        # T13: sleep_loop data (table doesn't exist in test, should handle)
        assert_true("sleep_loop" in data, "T13: sleep loop key exists")

        # T14: dreaming data
        assert_true("dreaming" in data, "T14: dreaming key exists")

        # T15: gap_mapping data
        assert_true("gap_mapping" in data, "T15: gap key exists")

        # T16: calibration data
        assert_true("calibration" in data, "T16: calibration key exists")

        # T17: council data
        assert_true("council" in data, "T17: council key exists")

        # T18: generate_council_sections (dry_run)
        result = generate_council_sections(test_db, quarter_data={"data": data}, dry_run=True)
        assert_eq(result["status"], "ok", "T18: dry run status")
        assert_eq(len(result["sections"]), 4, "T18: four sections")
        for s in result["sections"]:
            assert_true(s["speaker"] in MANIFEST_SPEAKERS, "T18: speaker valid")
            assert_true("DRY RUN" in s["section_content"], "T18: dry run marker")
            assert_true(len(s.get("prompt", "")) > 100, "T18: prompt non-trivial")

        # T19: synthesize_manifest (dry_run)
        mock_sections = [
            {"speaker": "orin", "section_title": "What We Know Now",
             "section_content": "We clarified the wizard library's architecture this quarter."},
            {"speaker": "vael", "section_title": "What Almost Went Wrong",
             "section_content": "We narrowly avoided a calibration regression in week 6."},
            {"speaker": "dessa", "section_title": "What Shifted Beneath Us",
             "section_content": "The dependency graph grew three new load-bearing edges."},
            {"speaker": "kael-em", "section_title": "The Roads Not Taken",
             "section_content": "We considered a graph database but chose SQLite edges."},
        ]
        result = synthesize_manifest(test_db, sections=mock_sections,
                                     quarter_data={"data": data}, dry_run=True)
        assert_eq(result["status"], "ok", "T19: synthesis dry run status")
        assert_true("DRY RUN" in result["manifest_content"], "T19: dry run marker")
        assert_true(len(result.get("prompt", "")) > 200, "T19: synthesis prompt non-trivial")

        # T20: _build_section_prompt structure
        config = MANIFEST_SPEAKERS["orin"]
        prompt = _build_section_prompt("orin", config, data)
        assert_true("QUARTERLY ARCANE MANIFEST" in prompt, "T20: has title")
        assert_true("first-person plural" in prompt.lower() or "we" in prompt.lower(), "T20: has we instruction")
        assert_true("Memory #" in prompt, "T20: has memory references")

        # T21: _build_synthesis_prompt structure
        prompt = _build_synthesis_prompt(mock_sections, data)
        # The synthesis prompt addresses whoever this brain DECLARES — and names
        # nobody when nothing is declared.  Asserting the author's name here
        # would be a test that only passes on the author's machine.
        _who = _declared_or_none()
        assert_true((f"You are {_who}," if _who else "You are the wizard library's")
                    in prompt,
                    f"T21: addresses the declared identity ({_who or 'neutral'})")
        assert_true("ORIN" in prompt, "T21: has ORIN section")
        assert_true("VAEL" in prompt, "T21: has VAEL section")
        assert_true("No bullets" in prompt, "T21: no bullets instruction")

        # T22: Insert manifest directly, then retrieve
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO arcane_manifests
               (quarter, year, quarter_number, manifest_content, sections, word_count, status)
               VALUES ('2026-Q2', 2026, 2, 'We built the wizard library.', '[]', 5, 'draft')""",
        )
        conn.commit()
        conn.close()

        result = get_manifest(test_db, 1)
        assert_eq(result["status"], "ok", "T22: get manifest status")
        assert_eq(result["manifest"]["quarter"], "2026-Q2", "T22: quarter")
        assert_eq(result["manifest"]["word_count"], 5, "T22: word count")

        # T23: get_manifest not found
        result = get_manifest(test_db, 9999)
        assert_eq(result["status"], "error", "T23: not found")

        # T24: get_manifest_history
        result = get_manifest_history(test_db)
        assert_eq(result["status"], "ok", "T24: history status")
        assert_true(result["count"] >= 1, "T24: count >= 1")

        # T25: get_manifest_history with status filter
        result = get_manifest_history(test_db, status="draft")
        assert_true(result["count"] >= 1, "T25: draft filter")

        # T26: invalid status filter
        result = get_manifest_history(test_db, status="invalid")
        assert_eq(result["status"], "error", "T26: invalid status")

        # T27: get_manifest_for_quarter
        result = get_manifest_for_quarter(test_db, "2026-Q2")
        assert_eq(result["status"], "ok", "T27: quarter lookup")
        assert_eq(result["manifest"]["quarter"], "2026-Q2", "T27: correct quarter")

        # T28: get_manifest_for_quarter not found
        result = get_manifest_for_quarter(test_db, "2099-Q4")
        assert_eq(result["status"], "error", "T28: quarter not found")

        # T29: add_owner_voice
        result = add_owner_voice(test_db, 1, "We are getting somewhere. The library remembers.")
        assert_eq(result["status"], "ok", "T29: owner voice status")

        # T30: verify drew_reviewed status
        result = get_manifest(test_db, 1)
        assert_eq(result["manifest"]["status"], "drew_reviewed", "T30: status drew_reviewed")
        assert_true(result["manifest"]["drew_addendum"] is not None, "T30: addendum present")

        # T31: add_owner_voice to nonexistent
        result = add_owner_voice(test_db, 9999, "ghost")
        assert_eq(result["status"], "error", "T31: owner voice nonexistent")

        # T32: finalize_manifest
        result = finalize_manifest(test_db, 1, store_as_memory=True)
        assert_eq(result["status"], "ok", "T32: finalize status")
        assert_true(result.get("memory_id") is not None, "T32: memory stored")
        assert_true(result["finalized_at"] is not None, "T32: finalized_at set")

        # T33: cannot finalize again
        result = finalize_manifest(test_db, 1)
        assert_eq(result["status"], "error", "T33: double finalize blocked")

        # T34: finalize nonexistent
        result = finalize_manifest(test_db, 9999)
        assert_eq(result["status"], "error", "T34: finalize nonexistent")

        # T35: verify memory was stored
        conn = sqlite3.connect(test_db)
        mem = conn.execute(
            "SELECT * FROM memories WHERE source_context = 'arcane-manifest-2026-Q2'"
        ).fetchone()
        conn.close()
        assert_true(mem is not None, "T35: manifest memory exists")

        # T36: verify memory tags
        conn = sqlite3.connect(test_db)
        tags_result = conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?",
            (mem[0],) if mem else (0,),
        ).fetchall()
        conn.close()
        tag_set = {t[0] for t in tags_result}
        assert_true("arcane-manifest" in tag_set, "T36: arcane-manifest tag")
        assert_true("quarterly-reflection" in tag_set, "T36: quarterly-reflection tag")
        assert_true("council-authored" in tag_set, "T36: council-authored tag")

        # T37: verify manifest_memory_id linked back
        result = get_manifest(test_db, 1)
        assert_true(result["manifest"]["manifest_memory_id"] is not None, "T37: memory_id linked")

        # T38: run_full_manifest_pipeline (dry_run)
        result = run_full_manifest_pipeline(
            test_db, year=2026, quarter_number=2, dry_run=True,
        )
        assert_eq(result["status"], "ok", "T38: pipeline dry run status")
        assert_eq(result["quarter"], "2026-Q2", "T38: pipeline quarter")
        assert_true(result["dry_run"], "T38: dry run flag")

        # T39: MANIFEST_SPEAKERS has four members
        assert_eq(len(MANIFEST_SPEAKERS), 4, "T39: four manifest speakers")
        assert_true("orin" in MANIFEST_SPEAKERS, "T39: orin present")
        assert_true("vael" in MANIFEST_SPEAKERS, "T39: vael present")
        assert_true("dessa" in MANIFEST_SPEAKERS, "T39: dessa present")
        assert_true("kael-em" in MANIFEST_SPEAKERS, "T39: kael-em present")
        assert_true("synthesizer" not in MANIFEST_SPEAKERS, "T39: the synthesizer is not a section speaker")

        # T40: section prompts include quarter data
        for speaker, cfg in MANIFEST_SPEAKERS.items():
            prompt = _build_section_prompt(speaker, cfg, data)
            assert_true(data["quarter"] in prompt, f"T40: {speaker} prompt has quarter")
            assert_true("first-person plural" in prompt.lower() or "'we'" in prompt,
                        f"T40: {speaker} prompt has we instruction")

        # T41: synthesis prompt includes all speakers
        prompt = _build_synthesis_prompt(mock_sections, data)
        for section in mock_sections:
            assert_true(section["speaker"].upper() in prompt, f"T41: {section['speaker']} in synthesis")

        # T42: _resolve_skill_path with override
        path = _resolve_skill_path("orin", "council-orin", base_path="/tmp/skills")
        # Path may not exist but should be constructed correctly
        assert_true(path is None or "council-orin" in str(path), "T42: skill path constructed")

        # T43: finalize without storing as memory
        conn = sqlite3.connect(test_db)
        conn.execute(
            """INSERT INTO arcane_manifests
               (quarter, year, quarter_number, manifest_content, sections, word_count, status)
               VALUES ('2026-Q1', 2026, 1, 'Q1 manifest.', '[]', 3, 'draft')""",
        )
        conn.commit()
        conn.close()
        result = finalize_manifest(test_db, 2, store_as_memory=False)
        assert_eq(result["status"], "ok", "T43: finalize without memory")
        assert_eq(result["memory_id"], None, "T43: no memory stored")

    finally:
        try:
            os.unlink(test_db)
        except OSError:
            pass

    print(f"\n{'='*60}")
    print(f"Arcane Manifest unit tests: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    for e in errors:
        print(f"  {e}")
    if failed == 0:
        print("  ALL TESTS PASSED")

    return {"passed": passed, "failed": failed, "errors": errors}


def persist_interactive(db_path: str, manifest_json_path: str) -> dict:
    """Persist a council-authored manifest WITHOUT any ``claude -p`` invocation.

    The quarterly manifest is meant to be co-authored by the Inner Council. The
    ``--run`` pipeline does that via ``_invoke_claude`` (one cloud call per voice
    + synthesis), which costs credits and fails silently in headless cron. This
    verb is the cost-free alternative: the council authors the manifest IN-SESSION
    (the running assistant embodying the voices) and writes a JSON file of the
    form ``{quarter?, year?, quarter_number?, sections:[{speaker, section_title,
    section_content}], synthesis:"<prose>", data_snapshot:{...}}``; this verb only
    persists it, mirroring ``synthesize_manifest``'s INSERT exactly.
    """
    try:
        with open(manifest_json_path) as fh:
            payload = json.load(fh)
    except Exception as e:
        return {"status": "error", "action": "persist_interactive",
                "message": f"could not read manifest json {manifest_json_path!r}: {e}"}

    sections = payload.get("sections")
    content = payload.get("synthesis") or payload.get("manifest_content")
    if not content or not isinstance(sections, list) or not sections:
        return {"status": "error", "action": "persist_interactive",
                "message": "manifest json needs a non-empty 'sections' list and a 'synthesis' string"}

    label, y, qn, _basis = _default_quarter()          # RC-09: the same default as the scheduled run
    if payload.get("year"):
        y = payload["year"]
    if payload.get("quarter_number"):
        qn = payload["quarter_number"]
    label = payload.get("quarter") or f"{y}-Q{qn}"

    word_count = len(content.split())
    data = payload.get("data_snapshot", {})

    ensure_manifest_table(db_path)
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO arcane_manifests
               (quarter, year, quarter_number, manifest_content, sections,
                word_count, data_snapshot, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (label, y, qn, content, json.dumps(sections),
             word_count, json.dumps(data, default=str)),
        )
        manifest_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok", "action": "persist_interactive",
        "manifest_id": manifest_id, "quarter": label,
        "word_count": word_count, "sections": len(sections),
        "generated_by": "interactive-council (claude-p-free)",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Arcane Manifest — Wizard Library L4 Piece 7 (Quarterly council-co-authored narrative)",
    )
    parser.add_argument(
        "db_path", nargs="?",
        default=str(Path(__file__).parent.parent / "claude_memory.db"),
        help="Path to claude_memory.db",
    )
    parser.add_argument(
        "--run", action="store_true",
        help="Run full quarterly manifest pipeline (quarterly cron entry point)",
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Override year (default: the default quarter's year)",
    )
    parser.add_argument(
        "--quarter", type=int, default=None,
        help="Override quarter number 1-4 (default: the quarter just ended when run in a quarter's first "
             "%d days -- the timer's day and a catch-up after it -- else the current quarter so far)" % QUARTER_GRACE_DAYS,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Return prompts without invoking Claude",
    )
    parser.add_argument(
        "--model-members", default=None,
        help="Claude model for individual council members (e.g., haiku)",
    )
    parser.add_argument(
        "--model-synthesis", default=None,
        help="Claude model for the synthesis (e.g., opus)",
    )
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="Per-invocation timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--skill-base-path", default=None,
        help="Override base path for persona skills",
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Run unit tests",
    )
    parser.add_argument(
        "--history", action="store_true",
        help="Show manifest history",
    )
    parser.add_argument(
        "--persist-interactive", dest="persist_interactive", default=None,
        metavar="MANIFEST_JSON",
        help="Persist a council-authored manifest JSON (claude-p-free interactive path)",
    )

    args = parser.parse_args()

    if args.persist_interactive:
        result = persist_interactive(args.db_path, args.persist_interactive)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("status", "ok") != "error" else 1)

    if args.test:
        result = _run_tests()
        sys.exit(0 if result["failed"] == 0 else 1)

    if args.history:
        result = get_manifest_history(args.db_path)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("status", "ok") != "error" else 1)

    if args.run:
        result = run_full_manifest_pipeline(
            args.db_path,
            year=args.year,
            quarter_number=args.quarter,
            dry_run=args.dry_run,
            claude_model_members=args.model_members,
            claude_model_synthesis=args.model_synthesis,
            timeout_seconds=args.timeout,
            skill_base_path=args.skill_base_path,
        )
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("status", "ok") in ("ok", "dry-run") else 1)

    parser.print_help()
    sys.exit(1)

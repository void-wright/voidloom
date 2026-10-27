#!/usr/bin/env python3
"""The Unified Dream Pipeline -- Voidloom Level-5 night-brain.

One nightly phase-sequencer that owns an ordered, individually-time-boxed,
individually-toggleable, FAIL-SOFT phase list. Each organ contributes a phase
function ``fn(conn, cfg, batch_id) -> dict``; one phase failing NEVER aborts the
cycle; the cron owns the list, the sequencer owns the order.

Canonical phase order (each phase nocturnal-cheap or daytime-deferred):

    0  Seed Selection      [DR activity-profile pre-filter + G surprise-weight]
    1  NREM consolidation  [F -- Hebbian potentiation + proportional downscaling]
    2  REM dreaming        [the EXISTING dual-pool, F-gated, DR-personalized]
    3  Probe-QA self-quiz  [H, nocturnal -- rides Loop B as a pure gap producer]
    4  Gap dispatch        [the EXISTING Loop B dispatch_auto_research, daytime]
    5  RAPTOR synthesis    [H, daytime/weekly/churn-triggered]
    6  Playbook emission   [H, nocturnal]

DISCIPLINE (non-negotiable, from the task spec):
  * LAYER on the existing dreaming + sleep loop -- never replace the L4 dual-pool
    or the 6-stage sleep. This module ORCHESTRATES them.
  * NEVER delete a memory (soft-archive only -- the forget-fixes live in
    claude_memory.py, this module never deletes).
  * NEVER lower the divergent distance_min=0.6 (personalization AMPLIFIES
    serendipity, it never flattens it).
  * NEVER store arousal-surprise as salience (G is EPISTEMIC only:
    novelty + contradiction).
  * NEVER let a phase failure abort the cycle (each phase is its own try/except).
  * NEVER inherit pre_cognitive_recall's DEFAULT_TIER_WEIGHTS / TIME_PATTERNS.
    H computes from the real graph -- the AGNOSTIC GUARDRAIL.
  * numpy + stdlib + sqlite3 + the existing companions only. NO new pip dep.
  * ``claude -p`` for synthesis MVP (the local mouth is deferred); model_garden
    for any model; gpu_guard for the daytime burst phases.

THE AGNOSTIC GUARDRAIL: every computing path in this file is content-free. There
are NO client/persona/instrument/Drew literals in any seed-selection, probe,
cluster, or playbook code path. ``test_dream_pipeline.py`` greps this file
mechanically and FAILS on any hit; a synthetic civil-engineering brain proves
identical useful behavior with zero leakage.

Public API
----------
``apply_migration_027(db_path, migration_sql_path) -> dict``
    Idempotent migration applier (PRAGMA-guarded ALTERs + the gap_type rebuild).

``run_dream_cycle(db_path, config=None, dry_run=False, daytime=False) -> dict``
    The phase sequencer. It HAS replaced memory_dreaming.run_dream_cycle:
    that function delegates here whenever migration 027 is applied
    (memory_dreaming.py:967-977), and every shipped cron declaration names
    this module directly since 2026-09-16. `run_dream_cycle_legacy` remains
    the fallback for a partial install, never the default. (D2-F5: "when"
    was answered long before this sentence was; nothing updated it, and two
    of three installers still named the shim.)

``PHASES`` -- the canonical ordered phase list (the cron reads this).

Each ``phase_*`` function is a standalone, fail-soft, time-boxed unit.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# Companion modules (graceful-absent: the pipeline degrades, never crashes)
# ============================================================

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _try_import(name: str):
    try:
        return __import__(name)
    except Exception:  # pragma: no cover - import hygiene
        return None


memory_dreaming = _try_import("memory_dreaming")


class _StarvedFallback(Exception):
    """Stand-in when memory_dreaming is absent (then no pair is ever dreamt, so it is never raised)."""

    def __init__(self, reason: str, detail: str = "", backend: str = ""):
        self.reason, self.detail, self.backend = reason, detail, backend
        super().__init__(reason)


#: STARVED (round 1): the thinker is unavailable for this run -- memory_dreaming.ThinkerStarved, one definition.
_ThinkerStarved = getattr(memory_dreaming, "ThinkerStarved", None) or _StarvedFallback
#: UNCONFIGURED (round 2, m4): the owner answered "e) not now" -- a choice, reported with exit 0, never STARVED / 79.
_UNCONFIGURED_NOTE = getattr(memory_dreaming, "UNCONFIGURED_NOTE", None) or (
    "the owner answered e) not now: no thinker, so no dream is written")


def _declined(e: BaseException) -> bool:
    """A ThinkerStarved (or a seam ThinkUnavailable) that is the owner's "e) not now"."""
    if getattr(e, "declined", False):
        return True
    fn = getattr(memory_dreaming, "thinker_declined", None)
    return bool(fn and fn(e))
sleep_loop = _try_import("sleep_loop")
gap_mapping = _try_import("gap_mapping")
skill_graduation = _try_import("skill_graduation")
kind_taxonomy = _try_import("kind_taxonomy")
model_garden = _try_import("model_garden")
gpu_guard = _try_import("gpu_guard")

# ============================================================
# Configuration -- every knob has a generic default; NOTHING domain-specific.
# ============================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    # --- DR activity profile (Phase 0) ---
    "activity_half_life_days": 7.0,        # exp-decay half-life of the running profile
    "activity_recall_weight": 0.65,        # recall-mass blend weight
    "activity_store_weight": 0.35,         # store-density blend weight
    "activity_lookback_days": 30,          # how far back to scan access_log / stores
    "activity_signal_min": 3.0,            # generous cold-start gate: total mass below this
                                           #   -> uniform (today's ORDER BY RANDOM behavior)
    "activity_temperature": 0.7,           # Boltzmann temperature over affinity (0->greedy,
                                           #   large->uniform). NEVER a hard filter.
    # --- G surprise (Phase 0 weighting + birth-certificate) ---
    "surprise_replay_weight": 0.6,         # how strongly surprise tilts the within-pool draw
    "surprise_importance_beta": 0.1,       # importance += beta * novelty (feeds, never replaces)
    "novelty_neighbors": 4,                # ANN k for novelty (nearest OTHER neighbor)
    # --- F NREM consolidation (Phase 1) ---
    "nrem_corecall_window_minutes": 30,    # co-recall = same session window
    "nrem_corecall_lookback_days": 2,      # only recent sessions
    "nrem_max_session_members": 24,        # cap distinct members/session (avoid N^2 blowup)
    "nrem_min_corecall_count": 2,          # only potentiate pairs co-recalled >= this
                                           #   (a single co-occurrence is not Hebbian evidence)
    "nrem_max_pairs": 2000,                # hard ceiling on edges touched per cycle
    "nrem_potentiation_delta": 0.04,       # logit-space bump per co-recall (small, bounded)
    "nrem_edge_confidence_cap": 0.97,      # never saturate at 1.0
    "nrem_downscale_target": 0.95,         # proportional downscale ceiling per source node
    # NREM rides the existing low-weight associative predicate ('references');
    # the co-recall semantics live in the edge metadata. extraction_method must
    # be one of the substrate's permitted values -> 'sleep_loop' (the nightly job).
    "nrem_predicate": "references",
    "nrem_extraction_method": "sleep_loop",
    "nrem_metadata_tag": "co_recalled",
    # --- REM dreaming (Phase 2) -- the three pools through memory_dreaming's selectors ---
    "divergent_pairs": 7,
    "close_context_pairs": 7,
    "persona_pairs": 2,                    # the dream fix (A2): 7 / 7 / 2 = 16 attempts per run
    "persona_pair_fraction": 0.10,         # kept for the legacy cycle; REM uses persona_pairs
    "divergent_distance_min": 0.6,         # NEVER lowered
    "close_context_distance_max": 0.40,    # memory_dreaming's calibrated close-context ceiling (jina 1024)
    "close_context_min_criteria": 2,
    "rem_time_box_s": 600.0,               # 16 thinker calls; the old 120 s box timed out 2 of 16 runs at 7 calls
    # The persona the persona pool dreams about. None -> resolved from the brain's own persona declaration
    # (voidloom_persona, beside the brain); undeclared -> the persona pool is SKIPPED and reported, never guessed.
    # Tranche B's persona manifest replaces this knob.
    "persona_name": None,
    "persona_self_tags": None,             # None -> memory_dreaming.persona_self_tags(persona_name)
    # --- Probe-QA (Phase 3) ---
    "probe_max_questions": 8,
    "probe_weak_strength": 0.35,           # retrieval strength below this == a failed probe
    "probe_top_k": 5,
    # Retrieval-strength backend for the probe-QA gate. 'structural' = the
    # self-contained FTS/embedding-free proxy (default; the offline/test path).
    # 'hybrid' = a sharper REAL hybrid recall scored by top-k mean relevance,
    # fully fail-soft back to the structural proxy if the encoder/module is
    # absent. BOTH are retrieval-only -- the confabulation gate is unchanged.
    "probe_strength_backend": "structural",
    # --- Gap dispatch (Phase 4) ---
    "gap_max_dispatch": 5,
    # --- RAPTOR (Phase 5) ---
    "raptor_min_cluster_size": 4,
    "raptor_max_clusters": 12,
    "raptor_churn_min_new_members": 3,     # re-summarize a cluster only when it churned
    # --- Playbooks (Phase 6) ---
    "playbook_min_sequence_len": 3,
    "playbook_min_occurrences": 2,
    "playbook_evidence_bar": 0.6,          # raised bar: dream-authored == unvalidated
    # --- synthesis mouth ---
    "claude_model": "sonnet",
    "claude_timeout_seconds": 60,
    # --- sequencer ---
    # None = THIS BRAIN's dream lock, beside its database (memory_dreaming.run_lock_path), shared by the nightly and
    # the daytime units (#20269). Never a path a tempdir decides: until Round 3 this was
    # tempfile.gettempdir()/voidloom-dream-pipeline.lock, so a harness with TMPDIR unset held the HOST's dream lock
    # and two runners of one brain with different TMPDIRs did not exclude each other.
    "lock_file": None,
    "default_phase_time_box_s": 120.0,
    "phases_enabled": None,                # None -> use each phase's enabled_default;
                                           #   else a dict {phase_name: bool} override
}

# Running brain identity. Resolved from env (the install stamps this), defaults
# to the canonical primary brain. AGNOSTIC: this is a process-identity env knob,
# never a behavioral lever -- it only labels authorship (J-alpha).
_KNOWN_BRAINS = ("milamber", "eddie")


def _running_brain() -> str:
    val = (os.environ.get("VOIDLOOM_BRAIN") or "").strip().lower()
    if val in _KNOWN_BRAINS:
        return val
    return _KNOWN_BRAINS[0]


# access_log hygiene: ONLY real session recalls feed the activity profile.
# Machine traffic (cron / sleep / dreaming / probe reads) is excluded. We match
# the real-session recall family and exclude any machine sentinel prefix. This is
# forward-compatible: a future machine reader that tags 'recall:cron' etc. is
# excluded automatically.
_REAL_RECALL_PREFIX = "recall"
_MACHINE_RECALL_MARKERS = ("cron", "dream", "sleep", "probe", "maintenance", "machine")


def _is_real_session_access(access_type: Optional[str]) -> bool:
    """True iff this access_log row is a real human-session recall."""
    if not access_type:
        return False
    at = access_type.strip().lower()
    if not at.startswith(_REAL_RECALL_PREFIX):
        return False
    # 'recall' / 'recall:hybrid' / 'recall:vector-only' == real; exclude machine.
    suffix = at[len(_REAL_RECALL_PREFIX):].lstrip(":")
    if suffix.endswith("-lead"):
        return False   # a side-channel DISPLAY (the met ledger, e.g. recall:dream-lead) is attention, not a recall
    return not any(suffix.startswith(m) for m in _MACHINE_RECALL_MARKERS)


# ============================================================
# Connection + embedding helpers (mirror the substrate; vec-load is best-effort)
# ============================================================

_JINA_DIM = 1024
_NOMIC_DIM = 768


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception:
            pass  # plain-BLOB embedding tables (tests) still work
    return conn


def _detect_embed_table(conn: sqlite3.Connection) -> Tuple[str, int]:
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='active_embedding_table'"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row and row[0] == "memory_embeddings_v2":
        return "memory_embeddings_v2", _JINA_DIM
    return "memory_embeddings", _NOMIC_DIM


def _is_vec0_table(conn: sqlite3.Connection, table: str) -> bool:
    """True iff `table` is a sqlite-vec vec0 virtual table (KNN MATCH-capable)."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (table,)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0] and "vec0" in row[0].lower())


def _fetch_embedding(conn: sqlite3.Connection, table: str, memory_id: int) -> Optional[bytes]:
    """Fetch one memory's embedding bytes from either a vec0 or plain-BLOB table.

    Returns None if absent or if the vec0 extension is unavailable in this
    connection (graceful: the caller skips that candidate rather than crashing).
    """
    try:
        row = conn.execute(
            f"SELECT embedding FROM {table} WHERE memory_id = ?", (memory_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or row[0] is None:
        return None
    return row[0]


def _cosine_distance(a: bytes, b: bytes) -> float:
    dim = len(a) // 4
    if dim == 0 or len(b) // 4 != dim:
        return 1.0
    va = struct.unpack(f"{dim}f", a)
    vb = struct.unpack(f"{dim}f", b)
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(x * x for x in vb))
    if na == 0 or nb == 0:
        return 1.0
    return max(0.0, 1.0 - dot / (na * nb))


# ============================================================
# Migration applier (PRAGMA-guarded ALTERs + the gap_type rebuild)
# ============================================================

_DREAM_ALTERS = [
    # (table, column, full ALTER statement)
    ("memories", "source_brain",
     "ALTER TABLE memories ADD COLUMN source_brain TEXT NOT NULL DEFAULT 'milamber' "
     "CHECK (source_brain IN ('milamber', 'eddie'))"),
    ("memories", "source_user",
     "ALTER TABLE memories ADD COLUMN source_user TEXT DEFAULT NULL"),
    ("memories", "encoding_surprise",
     "ALTER TABLE memories ADD COLUMN encoding_surprise REAL DEFAULT NULL"),
    ("memories", "surprise_finalized",
     "ALTER TABLE memories ADD COLUMN surprise_finalized INTEGER NOT NULL DEFAULT 0"),
    ("dream_log", "seed_bias",
     "ALTER TABLE dream_log ADD COLUMN seed_bias TEXT DEFAULT NULL"),
]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return False
    return column in cols


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


_PROBE_GAP_TYPE = "probe_failure"


def extend_gap_type_for_probe(db_path: str) -> Dict[str, Any]:
    """Widen gap_inventory.gap_type CHECK to permit 'probe_failure'.

    SQLite cannot relax a CHECK via ALTER, so we rebuild the table ONLY when the
    enum does not already permit 'probe_failure'. Fully idempotent: probes the
    CHECK by attempting a constrained insert into a temp clone. Runs OUTSIDE the
    main migration transaction so a partial rebuild never corrupts the live enum.
    """
    conn = _get_conn(db_path)
    try:
        if not _table_exists(conn, "gap_inventory"):
            return {"status": "skipped", "reason": "gap_inventory absent"}

        # Probe: is 'probe_failure' already permitted? Build a throwaway clone with
        # the SAME schema and try the constrained insert; if it succeeds, the enum
        # already allows it (idempotent no-op).
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='gap_inventory'"
        ).fetchone()
        create_sql = sql_row[0] if sql_row else ""
        if _PROBE_GAP_TYPE in create_sql:
            return {"status": "already-applied", "reason": "enum already permits probe_failure"}

        # Build the widened CREATE by injecting the new enum value into the CHECK.
        # We target the gap_type CHECK list specifically.
        widened = _inject_gap_type(create_sql, _PROBE_GAP_TYPE)
        if widened is None:
            return {"status": "error", "reason": "could not locate gap_type CHECK to widen"}

        # ---------------------------------------------------------------------
        # CRITICAL FK-SAFETY (iter-2 P1 fix). A naive
        #   `ALTER TABLE gap_inventory RENAME TO _gap_inventory_old`
        # makes SQLite REWRITE every sibling table's foreign key that
        # `REFERENCES gap_inventory` to point at `_gap_inventory_old` (the live
        # `gap_research_log.gap_id` carries exactly such an FK). The subsequent
        # `DROP TABLE _gap_inventory_old` then leaves that sibling FK permanently
        # DANGLING -- the next write to gap_research_log under foreign_keys=ON
        # throws `no such table: main._gap_inventory_old`, scarring referential
        # integrity on the live brain.
        #
        # `PRAGMA legacy_alter_table=ON` restores the pre-3.25 ALTER behaviour:
        # the RENAME does NOT rewrite child-table FK references, so the sibling
        # FK keeps pointing at the (re-created, same-named) gap_inventory and the
        # rebuild is referentially lossless. We set it INSIDE the function and
        # restore it to OFF afterwards so we never leak the pragma to the caller.
        #
        # foreign_keys is also forced OFF for the duration of the rebuild (a
        # connection-level guard so the interim rename never trips an enforcement
        # check), then restored.
        # ---------------------------------------------------------------------
        prev_fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        prev_legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        # ROUND DELTA-3: capture the index set BEFORE the rename.
        # An index belongs to its table. The RENAME carries every index over to
        # _gap_inventory_old and the DROP below destroys all of them; only what is
        # replayed afterwards survives. The original code replayed a HARDCODED list
        # of four, so any index created later -- including
        # ux_gap_inventory_entity_type, the unique index the dedup landing creates --
        # was silently dropped by a rebuild, with no error raised anywhere. Both
        # writers' run-time shape detectors would then correctly conclude that there
        # is no unique index, revert to plain INSERTs, and let the duplication
        # resume invisibly. Reading the set here makes the rebuild correct for
        # indexes that do not exist yet.
        _idx_before = [r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='gap_inventory' AND sql IS NOT NULL").fetchall()]
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE gap_inventory RENAME TO _gap_inventory_old")
        # The widened CREATE keeps the original table name.
        widened_named = widened.replace("_gap_inventory_old", "gap_inventory")
        conn.execute(widened_named)
        # Copy rows (columns are identical; rely on INSERT INTO ... SELECT *).
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(_gap_inventory_old)").fetchall()]
        col_list = ", ".join(old_cols)
        conn.execute(
            f"INSERT INTO gap_inventory ({col_list}) SELECT {col_list} FROM _gap_inventory_old"
        )
        conn.execute("DROP TABLE _gap_inventory_old")
        # Replay EVERY index this table carried, plus the four the original
        # migration guarantees. A rebuild that re-creates a fixed list forgets
        # everything created since that list was written; replaying the captured set
        # forgets nothing. `IF NOT EXISTS` is injected so a statement that the
        # widened CREATE already implies is a no-op rather than an error.
        _idx_replay = list(_idx_before) + [
            "CREATE INDEX IF NOT EXISTS idx_gap_score ON gap_inventory(score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_gap_type ON gap_inventory(gap_type)",
            "CREATE INDEX IF NOT EXISTS idx_gap_batch ON gap_inventory(batch_id)",
            "CREATE INDEX IF NOT EXISTS idx_gap_entity ON gap_inventory(entity)",
        ]
        _idx_failed = []
        for idx_sql in _idx_replay:
            stmt = idx_sql
            if " IF NOT EXISTS " not in stmt.upper():
                stmt = stmt.replace("CREATE UNIQUE INDEX ",
                                    "CREATE UNIQUE INDEX IF NOT EXISTS ", 1)
                stmt = stmt.replace("CREATE INDEX ",
                                    "CREATE INDEX IF NOT EXISTS ", 1)
            try:
                conn.execute(stmt)
            except sqlite3.Error as e:
                _idx_failed.append((stmt[:120], f"{type(e).__name__}: {e}"))
        if _idx_failed:
            # An index that was on the table before the rebuild and cannot be put
            # back is a CONSTRAINT that has silently disappeared. Refusing is the
            # only honest answer: the enum widening is optional, an unenforced
            # uniqueness constraint is not.
            conn.execute("ROLLBACK")
            raise sqlite3.IntegrityError(
                "gap_inventory was rebuilt but %d index(es) could not be restored, "
                "so a constraint the table carried before the rebuild would be "
                "silently gone. Rolled back; the CHECK was NOT widened. %r"
                % (len(_idx_failed), _idx_failed))
        conn.execute("COMMIT")
        # Restore the connection pragmas to what the caller had (never leak ON->ON
        # or ON->OFF surprises). legacy_alter_table is reset to its prior value;
        # foreign_keys is re-enabled only if it was on before (it is a no-op DDL
        # toggle here -- the live cron applier sets it explicitly).
        conn.execute(f"PRAGMA legacy_alter_table={'ON' if prev_legacy else 'OFF'}")
        conn.execute(f"PRAGMA foreign_keys={'ON' if prev_fk else 'OFF'}")
        # Post-rebuild integrity assertion: the sibling FK must STILL reference
        # gap_inventory, not the temp name, and a foreign_key_check must be clean.
        sibling_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='gap_research_log'"
        ).fetchone()
        if sibling_sql_row and "_gap_inventory_old" in (sibling_sql_row[0] or ""):
            return {"status": "error",
                    "reason": "FK-corruption: gap_research_log still references _gap_inventory_old"}
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            return {"status": "error",
                    "reason": f"foreign_key_check reported {len(fk_violations)} violation(s) post-rebuild"}
        return {"status": "ok", "reason": "gap_type widened to permit probe_failure (FK-safe)"}
    except Exception as e:  # pragma: no cover - defensive
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        # Never leave the legacy_alter_table pragma latched ON on an error path.
        try:
            conn.execute("PRAGMA legacy_alter_table=OFF")
        except Exception:
            pass
        return {"status": "error", "reason": str(e)}
    finally:
        conn.close()


def _inject_gap_type(create_sql: str, new_value: str) -> Optional[str]:
    """Inject a new enum value into the gap_type CHECK(... IN (...)) list."""
    # Find 'gap_type' ... CHECK(gap_type IN ( ... ))
    m = re.search(r"CHECK\s*\(\s*gap_type\s+IN\s*\((.*?)\)\s*\)", create_sql, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    enum_body = m.group(1)
    # Insert the new value right after the opening (preserve existing formatting).
    addition = f"\n        '{new_value}',  -- a probe-QA self-quiz failed against the brain"
    new_enum = addition + enum_body
    return create_sql[:m.start(1)] + new_enum + create_sql[m.end(1):]


def apply_migration_027(db_path: str, migration_sql_path: Optional[str] = None) -> Dict[str, Any]:
    """Idempotent applier: PRAGMA-guarded ALTERs + tables + gap_type rebuild.

    The .sql file carries the table DDL (IF NOT EXISTS) and the marker rows; the
    ALTERs in the .sql are documentation -- THIS applier performs them under a
    PRAGMA guard so re-running is a clean no-op (SQLite has no ALTER ... IF NOT
    EXISTS). The gap_type widening runs last, outside any transaction.
    """
    t0 = time.time()
    applied: List[str] = []
    skipped: List[str] = []
    conn = _get_conn(db_path)
    try:
        # 1) The PRAGMA-guarded ALTERs.
        for table, column, ddl in _DREAM_ALTERS:
            if not _table_exists(conn, table):
                skipped.append(f"{table}.{column} (table absent)")
                continue
            if _has_column(conn, table, column):
                skipped.append(f"{table}.{column}")
                continue
            conn.execute(ddl)
            applied.append(f"{table}.{column}")
        conn.commit()

        # 2) The CREATE TABLE IF NOT EXISTS blocks + marker rows. We extract just
        #    the table/index DDL + metadata markers from the .sql (skipping the
        #    raw ALTERs we already guarded, and the BEGIN/COMMIT wrapper).
        if migration_sql_path and Path(migration_sql_path).exists():
            sql_text = Path(migration_sql_path).read_text()
            ddl_only = _strip_alters_and_txn(sql_text)
            conn.executescript(ddl_only)
        else:
            conn.executescript(_INLINE_TABLE_DDL)
        conn.commit()
    except Exception as e:
        conn.close()
        return {"status": "error", "message": str(e), "applied": applied,
                "time_ms": int((time.time() - t0) * 1000)}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # 3) The gap_type widening (own connection, own transaction).
    gap_result = extend_gap_type_for_probe(db_path)

    return {
        "status": "ok",
        "altered_columns": applied,
        "skipped_columns": skipped,
        "gap_type": gap_result,
        "time_ms": int((time.time() - t0) * 1000),
    }


def _strip_alters_and_txn(sql_text: str) -> str:
    """Drop the raw ALTER statements + BEGIN/COMMIT (the applier guards ALTERs).

    An ALTER may span multiple lines (e.g. the source_brain CHECK), so once we
    enter an ALTER we skip until its terminating ';'.
    """
    out_lines = []
    in_alter = False
    for line in sql_text.splitlines():
        stripped = line.strip().upper()
        if in_alter:
            if ";" in line:
                in_alter = False
            continue
        if stripped.startswith("ALTER TABLE"):
            if ";" not in line:
                in_alter = True
            continue
        if stripped in ("BEGIN;", "COMMIT;"):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


_INLINE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS dream_activity_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension TEXT NOT NULL CHECK (dimension IN ('source_project','tag','category')),
    key TEXT NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    recall_mass REAL NOT NULL DEFAULT 0.0,
    store_mass REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (dimension, key)
);
CREATE INDEX IF NOT EXISTS idx_dap_dim_aff ON dream_activity_profile (dimension, affinity DESC);
CREATE TABLE IF NOT EXISTS dream_phase_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    phase_name TEXT NOT NULL,
    nocturnal INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ok'
        CHECK (status IN ('ok','error','skipped','deferred','timeout','disabled')),
    detail TEXT DEFAULT NULL,
    result_json TEXT DEFAULT NULL,
    duration_s REAL DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dpl_batch ON dream_phase_log (batch_id);
CREATE INDEX IF NOT EXISTS idx_dpl_phase ON dream_phase_log (phase_name);
CREATE INDEX IF NOT EXISTS idx_dpl_status ON dream_phase_log (status);
CREATE TABLE IF NOT EXISTS raptor_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_signature TEXT NOT NULL UNIQUE,
    member_count INTEGER NOT NULL DEFAULT 0,
    member_ids_json TEXT DEFAULT NULL,
    synthesis_memory_id INTEGER DEFAULT NULL,
    cluster_label TEXT DEFAULT NULL,
    drift_quiz_passed INTEGER DEFAULT NULL,
    last_summarized_at TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_raptor_sig ON raptor_clusters (cluster_signature);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now'))
);
INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('migration_027_dream_pipeline', 'applied', datetime('now'));
INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('dream_pipeline_schema', '1.0-unified-dream', datetime('now'));
"""


def is_migration_027_applied(conn: sqlite3.Connection) -> bool:
    return (
        _has_column(conn, "memories", "source_brain")
        and _has_column(conn, "memories", "encoding_surprise")
        and _table_exists(conn, "dream_phase_log")
        and _table_exists(conn, "dream_activity_profile")
    )


# ============================================================
# G -- novelty at write time (the birth-certificate's first half)
# ============================================================

def compute_novelty(conn: sqlite3.Connection, memory_id: int,
                    embedding: Optional[bytes] = None, k: int = 4) -> Optional[float]:
    """Epistemic novelty = cosine distance to the nearest OTHER memory.

    1 ANN MATCH (the embedding already exists). Returns a value in [0,1] where
    1.0 == maximally novel (nothing like it in the brain), 0.0 == an exact dup.
    Returns None on any failure -- the caller's store ALWAYS proceeds (the cost
    split: novelty is best-effort, never blocks the write).
    """
    try:
        table, _dim = _detect_embed_table(conn)
        if embedding is None:
            embedding = _fetch_embedding(conn, table, memory_id)
            if embedding is None:
                return None
        if _is_vec0_table(conn, table):
            rows = conn.execute(
                f"SELECT memory_id, distance FROM {table} "
                f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (embedding, max(2, k + 1)),
            ).fetchall()
            for nid, dist in rows:
                if nid != memory_id:
                    return round(max(0.0, min(1.0, float(dist))), 4)
            return 1.0
        # Plain-BLOB fallback (tests): brute-force nearest OTHER.
        rows = conn.execute(
            f"SELECT memory_id, embedding FROM {table} WHERE memory_id != ?", (memory_id,)
        ).fetchall()
        if not rows:
            return 1.0
        best = min(_cosine_distance(embedding, r[1]) for r in rows)
        return round(max(0.0, min(1.0, best)), 4)
    except Exception as e:
        logger.debug("novelty compute failed for %s: %s", memory_id, e)
        return None


# ============================================================
# DR -- the activity profile (Phase 0's pre-filter)
# ============================================================

def rebuild_activity_profile(conn: sqlite3.Connection, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the exp-decayed recall-weighted activity profile.

    Reads the brain's OWN real-session recalls (access_log hygiene) + recent
    store-density over source_project / tags / category. Per-brain == per-user
    for free: the brain reads its own activity, never a configured identity.
    """
    half_life = max(0.5, float(cfg["activity_half_life_days"]))
    decay_lambda = math.log(2.0) / half_life
    lookback = int(cfg["activity_lookback_days"])
    now = datetime.now()
    cutoff = (now - timedelta(days=lookback)).isoformat()
    rw = float(cfg["activity_recall_weight"])
    sw = float(cfg["activity_store_weight"])

    recall_mass: Dict[Tuple[str, str], float] = {}
    store_mass: Dict[Tuple[str, str], float] = {}

    def _add(d: Dict[Tuple[str, str], float], dim: str, key: Optional[str], w: float):
        if not key:
            return
        d[(dim, str(key))] = d.get((dim, str(key)), 0.0) + w

    # --- recall mass (real sessions only) ---
    try:
        rows = conn.execute(
            """SELECT a.access_type, a.accessed_at, m.source_project, m.category, a.memory_id
               FROM access_log a JOIN memories m ON m.id = a.memory_id
               WHERE a.accessed_at > ? AND m.is_archived = 0""",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for access_type, accessed_at, project, category, mem_id in rows:
        if not _is_real_session_access(access_type):
            continue
        age_days = _age_days(accessed_at, now)
        w = math.exp(-decay_lambda * age_days)
        _add(recall_mass, "source_project", project, w)
        _add(recall_mass, "category", category, w)
        for (tag,) in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (mem_id,)
        ).fetchall():
            _add(recall_mass, "tag", tag, w)

    # --- store mass (recent store density) ---
    try:
        srows = conn.execute(
            """SELECT id, created_at, source_project, category FROM memories
               WHERE created_at > ? AND is_archived = 0""",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        srows = []
    for mem_id, created_at, project, category in srows:
        age_days = _age_days(created_at, now)
        w = math.exp(-decay_lambda * age_days)
        _add(store_mass, "source_project", project, w)
        _add(store_mass, "category", category, w)
        for (tag,) in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (mem_id,)
        ).fetchall():
            _add(store_mass, "tag", tag, w)

    # --- blend + normalize per-dimension into affinity ---
    keys = set(recall_mass) | set(store_mass)
    raw: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
    for k in keys:
        rm = recall_mass.get(k, 0.0)
        sm = store_mass.get(k, 0.0)
        raw[k] = (rw * rm + sw * sm, rm, sm)

    # normalize affinity within each dimension to [0,1]
    by_dim_max: Dict[str, float] = {}
    for (dim, _key), (aff, _rm, _sm) in raw.items():
        by_dim_max[dim] = max(by_dim_max.get(dim, 0.0), aff)

    upserted = 0
    total_mass = 0.0
    if _table_exists(conn, "dream_activity_profile"):
        conn.execute("DELETE FROM dream_activity_profile")
        for (dim, key), (aff, rm, sm) in raw.items():
            denom = by_dim_max.get(dim, 0.0) or 1.0
            norm_aff = aff / denom
            total_mass += aff
            conn.execute(
                """INSERT OR REPLACE INTO dream_activity_profile
                       (dimension, key, affinity, recall_mass, store_mass, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (dim, key, round(norm_aff, 6), round(rm, 6), round(sm, 6)),
            )
            upserted += 1
        conn.commit()

    return {
        "upserted": upserted,
        "total_mass": round(total_mass, 4),
        "distinct_keys": len(keys),
        "cold_start": total_mass < float(cfg["activity_signal_min"]),
    }


def _age_days(ts: Optional[str], now: datetime) -> float:
    if not ts:
        return 9999.0
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00").split("+")[0])
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return 9999.0


def load_activity_profile(conn: sqlite3.Connection) -> Dict[str, Dict[str, float]]:
    """Return {dimension: {key: affinity}} from the stored profile."""
    out: Dict[str, Dict[str, float]] = {"source_project": {}, "tag": {}, "category": {}}
    if not _table_exists(conn, "dream_activity_profile"):
        return out
    for dim, key, aff in conn.execute(
        "SELECT dimension, key, affinity FROM dream_activity_profile"
    ).fetchall():
        out.setdefault(dim, {})[key] = aff
    return out


def profile_total_mass(conn: sqlite3.Connection) -> float:
    if not _table_exists(conn, "dream_activity_profile"):
        return 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(recall_mass*?+store_mass*?),0) FROM dream_activity_profile",
        (DEFAULT_CONFIG["activity_recall_weight"], DEFAULT_CONFIG["activity_store_weight"]),
    ).fetchone()
    return float(row[0] or 0.0)


def memory_activity_affinity(conn: sqlite3.Connection, memory_id: int,
                             profile: Dict[str, Dict[str, float]]) -> float:
    """Affinity of a memory to the activity center: max over its dims/tags.

    A memory near the activity center scores high; a memory in a cold region
    scores ~0. This is the TEMPERATURE input -- never a hard filter.
    """
    row = conn.execute(
        "SELECT source_project, category FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if not row:
        return 0.0
    project, category = row
    affs = [0.0]
    if project and project in profile.get("source_project", {}):
        affs.append(profile["source_project"][project])
    if category and category in profile.get("category", {}):
        affs.append(profile["category"][category])
    for (tag,) in conn.execute(
        "SELECT tag FROM memory_tags WHERE memory_id = ?", (memory_id,)
    ).fetchall():
        if tag in profile.get("tag", {}):
            affs.append(profile["tag"][tag])
    return max(affs)


def _softmax_weights(scores: List[float], temperature: float) -> List[float]:
    """Boltzmann softmax. temperature->0 == greedy; large == uniform."""
    if not scores:
        return []
    t = max(1e-6, temperature)
    mx = max(scores)
    exps = [math.exp((s - mx) / t) for s in scores]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


def select_eligible_pool(conn: sqlite3.Connection, cfg: Dict[str, Any],
                         pool_size: int, rng) -> Tuple[List[int], Dict[str, Any]]:
    """Phase-0 two-stage filter: DR picks the eligible region (temperature, not
    filter), G surprise-weights WITHIN it. Returns (memory_ids, seed_bias).

    Cold-start (generous signal gate) -> uniform random draw == today's behavior.
    """
    table, _dim = _detect_embed_table(conn)
    candidate_rows = conn.execute(
        f"""SELECT m.id FROM memories m JOIN {table} e ON e.memory_id = m.id
            WHERE m.is_archived = 0 AND m.derived_from IS NULL""",
    ).fetchall()
    candidates = [r[0] for r in candidate_rows]
    if not candidates:
        return [], {"mode": "empty"}

    total_mass = profile_total_mass(conn)
    cold_start = total_mass < float(cfg["activity_signal_min"])

    if cold_start:
        # Degrade smoothly to uniform random (the legacy ORDER BY RANDOM behavior).
        rng.shuffle(candidates)
        chosen = candidates[:pool_size]
        return chosen, {
            "mode": "cold_start_uniform",
            "total_mass": round(total_mass, 4),
            "signal_min": cfg["activity_signal_min"],
            "pool_size": len(chosen),
        }

    profile = load_activity_profile(conn)
    # Stage 1 (DR): temperature over activity affinity -> the eligible region.
    activity_scores = [memory_activity_affinity(conn, mid, profile) for mid in candidates]
    # Stage 2 (G): surprise-weighting WITHIN the region. Fold encoding_surprise in.
    surprise = _load_surprise(conn, candidates)
    sr = float(cfg["surprise_replay_weight"])
    blended = [
        (1.0 - sr) * activity_scores[i] + sr * surprise.get(candidates[i], 0.0)
        for i in range(len(candidates))
    ]
    weights = _softmax_weights(blended, float(cfg["activity_temperature"]))
    chosen = _weighted_sample_without_replacement(candidates, weights, pool_size, rng)
    return chosen, {
        "mode": "activity_surprise_weighted",
        "total_mass": round(total_mass, 4),
        "temperature": cfg["activity_temperature"],
        "surprise_replay_weight": sr,
        "pool_size": len(chosen),
        "mean_activity": round(sum(activity_scores) / max(1, len(activity_scores)), 4),
    }


def _load_surprise(conn: sqlite3.Connection, ids: List[int]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    if not ids or not _has_column(conn, "memories", "encoding_surprise"):
        return out
    qmarks = ",".join("?" * len(ids))
    for mid, surp in conn.execute(
        f"SELECT id, encoding_surprise FROM memories WHERE id IN ({qmarks})", ids
    ).fetchall():
        out[mid] = float(surp) if surp is not None else 0.0
    return out


def _weighted_sample_without_replacement(items: List[int], weights: List[float],
                                         n: int, rng) -> List[int]:
    """Efraimidis-Spirakis weighted reservoir (deterministic with a seeded rng)."""
    if not items:
        return []
    keyed = []
    for it, w in zip(items, weights):
        w = max(1e-12, w)
        u = rng.random()
        key = u ** (1.0 / w)
        keyed.append((key, it))
    keyed.sort(reverse=True)
    return [it for _key, it in keyed[:n]]


def anchor_then_fling(conn: sqlite3.Connection, anchor_id: int, cfg: Dict[str, Any],
                      candidate_ids: List[int], rng=None,
                      max_scan: int = 60) -> Optional[Tuple[int, int, float]]:
    """The 'fling far' move: anchor at the activity center, walk OUTWARD to max
    cosine distance. distance_min=0.6 is NEVER lowered -- personalization picks a
    BETTER anchor, it does not relax the divergence floor.

    The candidate scan is bounded (max_scan) so a large pool does not blow the
    phase time box; the original L4 selector uses the same bounded-sample idiom.
    """
    table, _dim = _detect_embed_table(conn)
    a_emb = _fetch_embedding(conn, table, anchor_id)
    if a_emb is None:
        return None
    floor = float(cfg["divergent_distance_min"])  # the sacred 0.6
    scan = [c for c in candidate_ids if c != anchor_id]
    if rng is not None and len(scan) > max_scan:
        scan = rng.sample(scan, max_scan)
    else:
        scan = scan[:max_scan]
    best: Optional[Tuple[int, int, float]] = None
    for cid in scan:
        c_emb = _fetch_embedding(conn, table, cid)
        if c_emb is None:
            continue
        dist = _cosine_distance(a_emb, c_emb)
        if dist <= floor:
            continue  # NOT divergent enough -- floor enforced, never lowered
        if best is None or dist > best[2]:
            best = (anchor_id, cid, round(dist, 4))
    return best


# ============================================================
# Phase result helper
# ============================================================

def _phase_result(status: str, **kw) -> Dict[str, Any]:
    d = {"status": status}
    d.update(kw)
    return d


# ============================================================
# Phase 0 -- Seed Selection (DR + G)
# ============================================================

def phase_seed_selection(conn: sqlite3.Connection, cfg: Dict[str, Any],
                         batch_id: Optional[int]) -> Dict[str, Any]:
    """Rebuild the activity profile, then stage the eligible seed pool + record
    WHY each seed was chosen (seed_bias) so the cron's REM phase reads it.
    """
    prof = rebuild_activity_profile(conn, cfg)
    rng = _rng_for(cfg)
    pool_size = int(cfg["divergent_pairs"]) * 4 + int(cfg["close_context_pairs"]) * 4 + 8
    pool, seed_bias = select_eligible_pool(conn, cfg, pool_size, rng)
    # Stash the staged pool on the connection for Phase 2 to consume.
    _SEED_CACHE[id(conn)] = {"pool": pool, "seed_bias": seed_bias}
    return _phase_result(
        "ok",
        profile=prof,
        seed_pool_size=len(pool),
        seed_bias=seed_bias,
        cold_start=seed_bias.get("mode") == "cold_start_uniform",
    )


_SEED_CACHE: Dict[int, Dict[str, Any]] = {}


def _rng_for(cfg: Dict[str, Any]):
    import random
    seed = cfg.get("_deterministic_seed")
    return random.Random(seed) if seed is not None else random.Random()


# ============================================================
# Phase 1 -- NREM consolidation (F): Hebbian potentiation + downscaling
# ============================================================

# Round 2, R2d: the decay's prune RETIRES an edge (valid_until + a {"retired_by": "decay-prune"} record) instead of
# deleting it. A lookup that would merge into "the existing edge" must not find a retired one: after a delete it was
# not there. Same predicate as temporal_edges.is_decay_retired, in SQL; never raises on non-JSON metadata.
_NOT_DECAY_RETIRED = ("NOT (valid_until IS NOT NULL AND COALESCE(CASE WHEN json_valid(metadata) "
                      "THEN json_extract(metadata, '$.retired_by') END, '') = 'decay-prune')")


def _revive_decay_retired(conn: sqlite3.Connection, a: int, b: int, predicate: str, confidence: float,
                          metadata: str, method: str) -> Optional[int]:
    """temporal_edges.revive_decay_retired with this module's INSERT values (valid_from = now); None when that
    module is absent (a partial install keeps the plain INSERT)."""
    try:
        import temporal_edges as _te  # stdlib + embed_resolver; laid beside this module
    except Exception:  # noqa: BLE001
        return None
    now = conn.execute("SELECT datetime('now')").fetchone()[0]
    return _te.revive_decay_retired(conn, a, b, predicate, confidence=confidence, metadata=metadata,
                                    extraction_method=method, valid_from=now)


def _merge_nrem_meta(existing_meta: Optional[str], tag: str) -> str:
    """Fold the nrem co-recall marker into an edge's existing metadata JSON."""
    try:
        d = json.loads(existing_meta) if existing_meta else {}
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    d["nrem"] = tag
    return json.dumps(d)


def _logit(p: float) -> float:
    p = min(0.999, max(0.001, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def phase_nrem_consolidation(conn: sqlite3.Connection, cfg: Dict[str, Any],
                             batch_id: Optional[int]) -> Dict[str, Any]:
    """Synaptic homeostasis. Hebbian potentiation of co-recalled pairs (bounded,
    logit-space) THEN proportional per-source downscaling (the exhale). One
    directional nightly write: NREM touches ONLY edge confidence on memory_edges.
    Temporal decay (the existing job) is untouched. This settles the graph that
    L5-A's PageRank reads -> foundation_score.
    """
    if not _table_exists(conn, "memory_edges"):
        return _phase_result("skipped", reason="memory_edges absent")
    if not _table_exists(conn, "access_log"):
        return _phase_result("skipped", reason="access_log absent")

    window_min = float(cfg["nrem_corecall_window_minutes"])
    lookback_days = int(cfg["nrem_corecall_lookback_days"])
    delta = float(cfg["nrem_potentiation_delta"])
    cap = float(cfg["nrem_edge_confidence_cap"])
    predicate = cfg["nrem_predicate"]                 # 'references' (substrate enum)
    method = cfg["nrem_extraction_method"]            # 'sleep_loop' (substrate enum)
    meta_tag = cfg["nrem_metadata_tag"]               # 'co_recalled' (lives in metadata)
    nrem_meta = json.dumps({"nrem": meta_tag})
    cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()

    # Pull recent real-session recalls ordered in time; co-recall == same window.
    rows = conn.execute(
        """SELECT memory_id, access_type, accessed_at FROM access_log
           WHERE accessed_at > ? ORDER BY accessed_at""",
        (cutoff,),
    ).fetchall()
    sessions: List[List[int]] = []
    cur: List[Tuple[int, datetime]] = []
    last_dt: Optional[datetime] = None
    for mem_id, access_type, accessed_at in rows:
        if not _is_real_session_access(access_type):
            continue
        try:
            dt = datetime.fromisoformat(str(accessed_at).replace("Z", "+00:00").split("+")[0])
        except Exception:
            continue
        if last_dt is not None and (dt - last_dt).total_seconds() > window_min * 60:
            if cur:
                sessions.append([m for m, _ in cur])
            cur = []
        cur.append((mem_id, dt))
        last_dt = dt
    if cur:
        sessions.append([m for m, _ in cur])

    # Count co-recall pairs (unordered, within a session, distinct ids). A
    # session is capped at nrem_max_session_members distinct memories (the most
    # recent within the window) to avoid an N^2 edge blowup from a long session.
    max_members = int(cfg["nrem_max_session_members"])
    pair_counts: Dict[Tuple[int, int], int] = {}
    for sess in sessions:
        uniq = list(dict.fromkeys(sess))
        if len(uniq) > max_members:
            uniq = uniq[-max_members:]  # keep the most recent members of the session
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = sorted((uniq[i], uniq[j]))
                pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

    # Only pairs co-recalled at least nrem_min_corecall_count times are Hebbian
    # evidence; a single co-occurrence is noise. Hard ceiling on total edges.
    min_count = int(cfg["nrem_min_corecall_count"])
    eligible = sorted(
        ((p, c) for p, c in pair_counts.items() if c >= min_count),
        key=lambda kv: kv[1], reverse=True,
    )[: int(cfg["nrem_max_pairs"])]

    potentiated = 0
    created = 0
    for (a, b), count in eligible:
        # Potentiate (or create) the co-recall edge, bounded in logit-space. The
        # co-recall semantics live in metadata (the predicate is the substrate's
        # low-weight associative 'references'); UNIQUE(source,target,predicate)
        # means we match an existing references edge and fold our bump in.
        existing = conn.execute(
            "SELECT id, confidence, metadata FROM memory_edges "
            "WHERE source_id=? AND target_id=? AND predicate=? "
            "AND COALESCE(CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.tag') END, '') != 'tension' "   # a dream's tension link is never strengthened by attention (R1-M4)
            "AND " + _NOT_DECAY_RETIRED,     # a decay-retired edge is gone, as when the prune deleted it (R2d)
            (a, b, predicate),
        ).fetchone()
        bump = delta * count
        if existing:
            new_conf = min(cap, _sigmoid(_logit(existing[1] or 0.5) + bump))
            conn.execute("UPDATE memory_edges SET confidence=?, metadata=? WHERE id=?",
                         (round(new_conf, 4), _merge_nrem_meta(existing[2], meta_tag), existing[0]))
            potentiated += 1
        else:
            new_conf = min(cap, _sigmoid(_logit(0.5) + bump))
            try:
                if _revive_decay_retired(conn, a, b, predicate, round(new_conf, 4), nrem_meta, method) is None:
                    conn.execute(
                        """INSERT INTO memory_edges
                               (source_id, target_id, predicate, confidence, valid_from,
                                created_at, extraction_method, metadata)
                           VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?, ?)""",
                        (a, b, predicate, round(new_conf, 4), method, nrem_meta),
                    )
                created += 1
            except sqlite3.IntegrityError:
                pass  # a concurrent/self-edge guard fired; skip gracefully

    # Proportional per-source downscaling (the exhale): scoped to the co-recall
    # edges this node owns. If a node's strongest co-recall edge saturates, scale
    # ALL its co-recall edges down proportionally so the strongest sits at the
    # target ceiling -- the strongest never pins at 1.0.
    downscaled_nodes = 0
    target = float(cfg["nrem_downscale_target"])
    # CURRENT co-recall edges only (round 2, R2d): a retired edge is out of the graph and its row is the restore's.
    sources = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_id FROM memory_edges "
        "WHERE predicate=? AND metadata LIKE ? AND valid_until IS NULL", (predicate, f'%"nrem": "{meta_tag}"%')
    ).fetchall()]
    for src in sources:
        edges = conn.execute(
            "SELECT id, confidence FROM memory_edges "
            "WHERE source_id=? AND predicate=? AND metadata LIKE ? AND valid_until IS NULL",
            (src, predicate, f'%"nrem": "{meta_tag}"%'),
        ).fetchall()
        if not edges:
            continue
        mx = max((c or 0.0) for _i, c in edges)
        if mx > target:
            scale = target / mx
            for eid, conf in edges:
                conn.execute("UPDATE memory_edges SET confidence=? WHERE id=?",
                             (round((conf or 0.0) * scale, 4), eid))
            downscaled_nodes += 1

    conn.commit()
    return _phase_result(
        "ok",
        sessions=len(sessions),
        corecall_pairs_raw=len(pair_counts),
        corecall_pairs_eligible=len(eligible),
        edges_potentiated=potentiated,
        edges_created=created,
        downscaled_nodes=downscaled_nodes,
    )


# ============================================================
# Phase 2 -- REM dreaming (the existing dual-pool, DR-seeded, F-gated)
# ============================================================

def _resolve_persona_name(cfg: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(name, source). cfg['persona_name'] wins; else the brain's own persona declaration beside the brain
    (voidloom_persona). Undeclared -> (None, reason): the persona pool is skipped and SAYS so. No literal here."""
    explicit = cfg.get("persona_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip(), "config"
    db = cfg.get("_db_path")
    if not db:
        return None, "no brain path"
    try:
        import voidloom_persona  # type: ignore
        name, _blurb, source = voidloom_persona.resolve_persona(str(Path(db).resolve().parent))
        return (name, "declaration:" + source) if name else (None, "undeclared:" + source)
    except Exception as e:  # noqa: BLE001 - a missing declaration organ is a skipped pool, never a crash
        return None, "persona resolver unavailable: %s" % type(e).__name__


def _rem_pools(conn: sqlite3.Connection, cfg: Dict[str, Any], pool: List[int]) -> Dict[str, Any]:
    """The three pools, each through memory_dreaming's selector. Every selector excludes derived rows."""
    out: Dict[str, Any] = {"divergent": [], "close_context": [], "persona": [], "notes": {}}
    if memory_dreaming is None:
        out["notes"]["all"] = "memory_dreaming unavailable"
        return out
    try:
        out["divergent"] = [(a, b, d, None) for a, b, d in memory_dreaming.select_divergent_pairs(
            conn, count=int(cfg["divergent_pairs"]), distance_min=float(cfg["divergent_distance_min"]),
            candidate_ids=pool or None)]
    except Exception as e:  # noqa: BLE001 - one pool failing never stops the others
        out["notes"]["divergent"] = "%s: %s" % (type(e).__name__, e)
    try:
        out["close_context"] = list(memory_dreaming.select_close_context_pairs(
            conn, count=int(cfg["close_context_pairs"]),
            distance_max=float(cfg["close_context_distance_max"]),
            min_criteria=int(cfg["close_context_min_criteria"])))
    except Exception as e:  # noqa: BLE001
        out["notes"]["close_context"] = "%s: %s" % (type(e).__name__, e)
    name, source = _resolve_persona_name(cfg)
    out["persona_name_source"] = source
    out["persona_name"] = name
    if not name:
        out["notes"]["persona"] = "skipped: no persona declared (%s)" % source
    else:
        try:
            out["persona"] = list(memory_dreaming.select_persona_pairs(
                conn, count=int(cfg["persona_pairs"]), tags=cfg.get("persona_self_tags"),
                persona_name=name))
            if not out["persona"]:
                out["notes"]["persona"] = "starved: no memory carries the persona's self tags"
        except Exception as e:  # noqa: BLE001
            out["notes"]["persona"] = "%s: %s" % (type(e).__name__, e)
    return out


def _interleave(pools: Dict[str, List[Any]], order=("divergent", "close_context", "persona")) -> List[Tuple[str, Any]]:
    """Round-robin across the pools, so the first len(order) attempts touch every non-empty pool."""
    queues = {k: list(pools.get(k) or []) for k in order}
    seq: List[Tuple[str, Any]] = []
    while any(queues.values()):
        for k in order:
            if queues[k]:
                seq.append((k, queues[k].pop(0)))
    return seq


def phase_rem_dreaming(conn: sqlite3.Connection, cfg: Dict[str, Any],
                       batch_id: Optional[int]) -> Dict[str, Any]:
    """REM: three pools -- divergent (seeded by Phase 0's activity pool, the 0.6 floor), close_context (unseeded;
    its selector takes no candidate_ids) and persona (the persona's SELF tags) -- drawn through memory_dreaming's
    selectors, INTERLEAVED under a time box, each dream STORED as a reflection memory.

    THE DREAM FIX (Tranche A). From 2026-06-12 this phase drew divergent pairs only and wrote dream_log rows with
    no memory: 750 meaningful dreams, 0 stored, close_context and persona never run. Now, per pair:
      1. the dream (thinker seam; RELATION parsed inside dream_on_pair, before the NULL test);
      2. a meaningful dream is STORED (memory_dreaming._store_dream_as_reflection: birth 0.40, derived_from
         'dream_synthesis', a real summary, the endpoints' taint, an active-tier vector);
      3. the dream_log row carries dream_memory_id and context_difference (the relation, the pair's context);
         a store FAILURE keeps is_meaningful=1 with a NULL id -- the row invariant the sentinel reads as BROKEN;
      4. a RELATION of contradiction / tension writes the 0.2 'tension' edge between the pair, no precondition;
      5. the row is committed.
    The time box stops the loop only after every non-empty pool has had at least one attempt.
    """
    staged = _SEED_CACHE.get(id(conn), {})
    pool = staged.get("pool") or []
    seed_bias = staged.get("seed_bias") or {"mode": "unseeded"}
    dry = bool(cfg.get("_dry_run"))
    box = cfg.get("_time_box")
    pools = _rem_pools(conn, cfg, pool)
    seq = _interleave(pools)
    stats = {k: {"planned": len(pools.get(k) or []), "attempts": 0, "meaningful": 0, "stored": 0,
                 "store_failures": 0, "skipped_archived": 0, "tension_edges": 0}
             for k in ("divergent", "close_context", "persona")}
    has_log = _table_exists(conn, "dream_log")
    has_seed_col = has_log and _has_column(conn, "dream_log", "seed_bias")
    timeboxed = False
    starved = None
    if cfg.get("_thinker_starved") and not dry:
        seq = []                    # an earlier phase already found the thinker unavailable tonight
    for kind, pair in seq:
        if box is not None and box.expired():
            touched = all(stats[k]["attempts"] > 0 for k in stats if stats[k]["planned"] > 0)
            if touched:
                timeboxed = True
                break
            if stats[kind]["attempts"] > 0:
                continue            # expired: only pools still untouched get their one attempt
        a, b, dist, ctx = pair
        st = stats[kind]
        st["attempts"] += 1
        text, relation = (None, None)
        if not dry:
            try:
                text, relation = _safe_dream(conn, cfg, a, b, kind, ctx)
            except _ThinkerStarved as e:
                # STARVED (round 1): no dream is possible tonight. This attempt is not counted and writes no row.
                st["attempts"] -= 1
                starved = e
                cfg["_thinker_starved"] = e.reason
                if _declined(e):
                    cfg["_thinker_declined"] = True
                break
        is_meaningful = 1 if text else 0
        mem_id = None
        store_note: Dict[str, Any] = {}
        if is_meaningful:
            st["meaningful"] += 1
            if memory_dreaming is not None:
                mem_id = memory_dreaming._store_dream_as_reflection(
                    conn, text, a, b, kind, relation=relation, encoder=cfg.get("_encoder"),
                    report=store_note, commit=False)
            else:
                store_note["error"] = "memory_dreaming unavailable"
            if mem_id:
                st["stored"] += 1
            elif store_note.get("skipped") == "endpoint_archived":
                # not a failure: a dream about a retired memory is not kept (and is not meaningful to keep)
                st["skipped_archived"] += 1
                is_meaningful = 0
            else:
                st["store_failures"] += 1   # its own state (not an LLM failure): the row keeps is_meaningful=1
            if relation in _TENSION_RELATIONS:
                st["tension_edges"] += _integrate_dream_edge(conn, cfg, a, b, relation)
        ctx_json = dict(ctx or {})
        if relation:
            ctx_json["relation"] = relation
        if store_note.get("error"):
            ctx_json["store_error"] = str(store_note["error"])[:300]
        if store_note.get("skipped"):
            ctx_json["store_skipped"] = store_note["skipped"]
        if has_log:
            cols = ["dream_type", "memory_a_id", "memory_b_id", "cosine_distance", "context_difference",
                    "dream_content", "dream_memory_id", "is_meaningful", "batch_id", "created_at"]
            vals = [kind, a, b, dist, json.dumps(ctx_json) if ctx_json else None, text, mem_id, is_meaningful,
                    batch_id]
            if has_seed_col:
                cols.insert(-1, "seed_bias")
                vals.append(json.dumps({**seed_bias, "pool": kind,
                                        **({"anchor": a} if kind == "divergent" else {})}))
            try:
                conn.execute(
                    f"INSERT INTO dream_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(vals))}, "
                    f"datetime('now'))", vals)
            except sqlite3.OperationalError as e:
                logger.warning("dream_log write failed: %s", e)
        conn.commit()
    tot = {k: sum(v[k] for v in stats.values()) for k in
           ("attempts", "meaningful", "stored", "store_failures", "tension_edges")}
    if cfg.get("_thinker_declined") and not dry:
        # UNCONFIGURED (round 2, m4): the owner's "e) not now" -- not a starved night.
        return _phase_result(
            "unconfigured", note=_UNCONFIGURED_NOTE, pairs_planned=len(seq), attempts=tot["attempts"],
            dreamed=tot["meaningful"], stored=tot["stored"], store_failures=tot["store_failures"],
            tension_edges=tot["tension_edges"], pools=stats)
    if starved is not None or (cfg.get("_thinker_starved") and not dry):
        reason = starved.reason if starved is not None else cfg["_thinker_starved"]
        return _phase_result(
            "starved", starved_reason=reason,
            starved_detail=str(starved) if starved is not None else "thinker unavailable earlier in this run",
            pairs_planned=len(seq), attempts=tot["attempts"], dreamed=tot["meaningful"], stored=tot["stored"],
            store_failures=tot["store_failures"], tension_edges=tot["tension_edges"], pools=stats)
    return _phase_result(
        "ok" if not dry else "dry-run",
        seed_mode=seed_bias.get("mode"),
        pairs_planned=len(seq),
        attempts=tot["attempts"],
        dreamed=tot["meaningful"],
        stored=tot["stored"],
        store_failures=tot["store_failures"],
        tension_edges=tot["tension_edges"],
        pools=stats,
        pool_notes=pools.get("notes") or {},
        persona_name_source=pools.get("persona_name_source"),
        timeboxed=timeboxed,
        floor_held=cfg["divergent_distance_min"],
    )


_TENSION_RELATIONS = ("contradiction", "tension")


def _safe_dream(conn, cfg, a_id, b_id, dtype, ctx=None) -> Tuple[Optional[str], Optional[str]]:
    """Dream one pair, fully guarded + mockable. Returns (body, relation); body None = no dream.

    Honors cfg['_offline'] / cfg['_synthesis_mouth'] so the suite (and any no-network run) never fires a real
    thinker. The mouth's reply goes through the SAME RELATION parse + NULL rule as a thinker's. The dream synthesis
    is the ONLY LLM touch in REM, and it is NOT in the hot selection path.
    """
    if cfg.get("_offline"):
        return None, None
    mouth = cfg.get("_synthesis_mouth")
    if mouth is not None:
        try:
            raw = mouth(f"{dtype}:{a_id}-{b_id}", [])
        except Exception:
            return None, None
        if memory_dreaming is None:
            return (raw or None), None
        rel, body = memory_dreaming.parse_relation(raw)
        return (None, rel) if memory_dreaming._is_null_body(body) else (body, rel)
    if memory_dreaming is None:
        return None, None
    try:
        meta_a = memory_dreaming._get_memory_metadata(conn, a_id)
        meta_b = memory_dreaming._get_memory_metadata(conn, b_id)
        if not meta_a or not meta_b:
            return None, None
        out: Dict[str, Any] = {}
        persona_name = None
        if dtype == "persona":
            persona_name, _src = _resolve_persona_name(cfg)
        body = memory_dreaming.dream_on_pair(
            meta_a, meta_b, dtype, ctx if dtype == "close_context" else None,
            timeout_seconds=cfg["claude_timeout_seconds"],
            claude_model=cfg.get("claude_model"),
            on_failure=_llm_failure_sink(cfg),
            persona_name=persona_name,
            out=out,
        )
        if out.get("starved"):
            st = out["starved"]
            raise _ThinkerStarved(st["reason"], st.get("detail", ""), st.get("backend", ""))
        return body, out.get("relation")
    except _ThinkerStarved:
        raise
    except Exception as e:
        logger.warning("dream_on_pair failed (the pair falls back to no dream): %s: %s",
                       type(e).__name__, e)
        _llm_failure_sink(cfg)("dream_on_pair raised %s: %s" % (type(e).__name__, e))
        return None, None


def _integrate_dream_edge(conn, cfg, a_id, b_id, relation: Optional[str] = None) -> int:
    """A dream that says its pair is in CONTRADICTION or TENSION writes a low-confidence (0.2) 'references' edge
    typed ``{"tag": "tension", "origin": "dream-synthesis"}`` between the pair -- typed, not dropped, not forced to
    a verdict. NO precondition: the old rule fired only if a contradicts/supersedes edge already joined the pair,
    and 0 of 972 past dream pairs had any edge, so it never fired. Where a 'references' edge already joins the pair
    (UNIQUE(source,target,predicate)), the tension mark is MERGED into its metadata and its confidence is left
    alone. The tension link is never strengthened by attention (NREM skips it) and is not counted in anti_recency's
    degree; at 0.2 it sits below the 0.3 floor of PPR and multi-hop. Its time fade is carried by the dream that
    asserts it (dream_ledger); the link itself is a typed, fixed-weight mark.
    Returns 1 when a tension edge was written or merged, else 0.
    """
    if relation not in _TENSION_RELATIONS or not _table_exists(conn, "memory_edges"):
        return 0
    meta = {"tag": "tension", "origin": "dream-synthesis", "relation": relation}
    try:
        existing = conn.execute(
            """SELECT id, metadata FROM memory_edges WHERE predicate='references'
               AND ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?)) AND """ + _NOT_DECAY_RETIRED,
            (a_id, b_id, b_id, a_id)).fetchone()
        if existing:
            try:
                d = json.loads(existing[1]) if existing[1] else {}
                if not isinstance(d, dict):
                    d = {}
            except Exception:
                d = {}
            d.update({"tension": True, "tension_relation": relation, "tension_origin": "dream-synthesis"})
            conn.execute("UPDATE memory_edges SET metadata=? WHERE id=?", (json.dumps(d), existing[0]))
            return 1
        if _revive_decay_retired(conn, a_id, b_id, 'references', 0.2, json.dumps(meta), 'sleep_loop') is None:
            conn.execute(
                """INSERT INTO memory_edges
                       (source_id, target_id, predicate, confidence, valid_from,
                        created_at, extraction_method, metadata)
                   VALUES (?, ?, 'references', 0.2, datetime('now'), datetime('now'),
                           'sleep_loop', ?)""",
                (a_id, b_id, json.dumps(meta)))
        return 1
    except sqlite3.Error as e:
        logger.warning("tension edge not written for (%s, %s): %s", a_id, b_id, e)
        return 0


# ============================================================
# Phase 3 -- Probe-QA self-quiz (H, nocturnal). The ARCHITECTURAL gate.
# ============================================================

# Content-free probe templates. NO domain seeds: the {label} is a RAPTOR-derived
# cluster label or a weak-recall zone key computed from the graph -- never a
# hardcoded topic. This is the agnostic guardrail in template form.
_PROBE_TEMPLATES = (
    "What do I know about {label}?",
    "Summarize what is established about {label}.",
    "What are the key facts regarding {label}?",
)


def _gap_pair_unique(conn: sqlite3.Connection) -> bool:
    """Does gap_inventory carry a NON-PARTIAL UNIQUE index on exactly
    (entity, gap_type)?

    Asked of the database at run time, by SHAPE and never by index name, because
    both worlds are reachable: the landing creates the index and patches the writers
    in the same run, but the dedup transaction can refuse after this edit is in
    place, and dream_pipeline.extend_gap_type_for_probe() can rebuild the table and
    take the index with it.

    THE ORDER IS LOAD-BEARING AND THAT IS WHY THIS IS A RUN-TIME QUESTION.
    `INSERT ... ON CONFLICT(a, b) DO UPDATE` is rejected by SQLite AT PREPARE TIME
    when no matching uniqueness constraint exists -- on an empty table, and even
    under EXPLAIN. A writer patched with a bare upsert before the index existed
    would store NOTHING.

    Three outcomes, not two. A PARTIAL unique index, or a unique index on the
    columns in the other order, accepts neither statement safely: the upsert's
    conflict target does not match it, and a plain INSERT will raise against it into
    a handler. Returning False there would produce exactly the silent nightly
    failure this patch exists to end, so that case RAISES instead."""
    partial_or_reversed = []
    for ix in conn.execute("PRAGMA index_list('gap_inventory')").fetchall():
        ix_name, ix_uniq, ix_partial = ix[1], ix[2], (ix[4] if len(ix) > 4 else 0)
        if not ix_uniq:
            continue
        cols = [r[2] for r in
                conn.execute('PRAGMA index_info("%s")' % ix_name).fetchall()]
        if cols == ['entity', 'gap_type'] and not ix_partial:
            return True
        if set(cols) == {'entity', 'gap_type'}:
            partial_or_reversed.append(
                (ix_name, cols, 'partial' if ix_partial else 'column order'))
    if partial_or_reversed:
        raise sqlite3.IntegrityError(
            "gap_inventory carries a UNIQUE index over (entity, gap_type) that this "
            "writer cannot use as a conflict target: %r. A plain INSERT would raise "
            "against it on every duplicate and a DO UPDATE would be rejected at "
            "prepare time, so neither statement is safe. Refusing loudly rather "
            "than filing nothing every night." % (partial_or_reversed,))
    return False


_PROBE_INSERT_SQL = """INSERT INTO gap_inventory
                           (entity, gap_type, score, memory_count, auto_research_eligible,
                            batch_id, inventoried_at)
                       VALUES (?, 'probe_failure', ?, ?, ?, ?, datetime('now'))"""

# ON CONFLICT writes the three columns this phase MEASURES, plus the two provenance
# columns. It deliberately does NOT write last_mentioned: this writer never measures
# it (the column is absent from the INSERT list above), and writing a column you did
# not measure is the same defect as failing to write one you did.
_PROBE_UPSERT_SQL = _PROBE_INSERT_SQL + """
                       ON CONFLICT(entity, gap_type) DO UPDATE SET
                           score = excluded.score,
                           memory_count = excluded.memory_count,
                           auto_research_eligible = excluded.auto_research_eligible,
                           batch_id = excluded.batch_id,
                           inventoried_at = datetime('now')"""


def _probe_gap_sql(conn: sqlite3.Connection) -> str:
    return _PROBE_UPSERT_SQL if _gap_pair_unique(conn) else _PROBE_INSERT_SQL


def phase_probe_qa(conn: sqlite3.Connection, cfg: Dict[str, Any],
                   batch_id: Optional[int]) -> Dict[str, Any]:
    """Generate content-free probe questions from the graph (cluster labels /
    weak-recall zones), test each against the brain via RETRIEVAL STRENGTH ONLY,
    and file gap_inventory rows with gap_type='probe_failure' for the failures.

    THE CONFABULATION GATE IS ARCHITECTURAL: pass/fail comes ONLY from retrieval
    strength against the brain. There is NO 'model answers -> model grades its own
    answer' code path -- it cannot fire because it does not exist. A failed probe
    == weak retrieval == file a gap. The fill comes from external research
    (Loop B), never a parametric guess.
    """
    if not _table_exists(conn, "gap_inventory"):
        return _phase_result("skipped", reason="gap_inventory absent")

    labels = _derive_probe_labels(conn, cfg)
    if not labels:
        return _phase_result("ok", probes=0, failures=0, note="no graph labels yet")

    weak = float(cfg["probe_weak_strength"])
    top_k = int(cfg["probe_top_k"])
    rng = _rng_for(cfg)

    probes = 0
    failures = 0
    filed = 0
    for label in labels[: int(cfg["probe_max_questions"])]:
        template = _PROBE_TEMPLATES[rng.randrange(len(_PROBE_TEMPLATES))]
        _question = template.format(label=label)  # noqa: F841 (the question is content-free)
        strength = _retrieval_strength(conn, label, top_k, cfg)
        probes += 1
        if strength < weak:
            failures += 1
            # File a probe_failure gap. The fill is EXTERNAL (Loop B) only.
            try:
                conn.execute(
                    _probe_gap_sql(conn),
                    (label[:500], round(1.0 - strength, 4),
                     _label_memory_count(conn, label),
                     1 if (1.0 - strength) >= 0.5 else 0, batch_id),
                )
                filed += 1
            except sqlite3.Error as e:
                # WIDENED FROM sqlite3.OperationalError (round delta-3).
                # sqlite3.IntegrityError is NOT a subclass of OperationalError --
                # both descend from DatabaseError and neither from the other -- so
                # once gap_inventory carries a UNIQUE INDEX on (entity, gap_type)
                # the duplicate this phase re-files every night escaped the old
                # handler entirely, propagated out of this loop (skipping every
                # remaining label AND the conn.commit() below), and was absorbed by
                # the cycle runner's fail-soft guard, which marks the phase 'error'
                # and still returns status 'ok'. Permanent, nightly, silent.
                # sqlite3.Error is the common ancestor of every DB-API error, so no
                # database failure here can escape again; it is still NOT a bare
                # `except Exception`, so a bug in _label_memory_count still raises.
                logger.warning("probe gap insert failed (%s): %s",
                               type(e).__name__, e)
    conn.commit()
    return _phase_result("ok", probes=probes, failures=failures, gaps_filed=filed)


def _derive_probe_labels(conn: sqlite3.Connection, cfg: Dict[str, Any]) -> List[str]:
    """Labels come ONLY from computed structure: RAPTOR cluster labels first, then
    weak-recall zones (source_project/category with sparse high-confidence mass).
    NEVER a hardcoded topic. Returns a list of plain string keys.
    """
    labels: List[str] = []
    # 1) RAPTOR cluster labels (if Phase 5 has run).
    if _table_exists(conn, "raptor_clusters"):
        for (lbl,) in conn.execute(
            "SELECT cluster_label FROM raptor_clusters WHERE cluster_label IS NOT NULL"
        ).fetchall():
            if lbl:
                labels.append(str(lbl))
    # 2) Weak-recall zones: source_project keys with the LOWEST mean recall
    #    relevance (computed from access_log) -- the brain's own thin spots.
    try:
        rows = conn.execute(
            """SELECT m.source_project, AVG(a.relevance_score) avg_rel, COUNT(*) n
               FROM access_log a JOIN memories m ON m.id = a.memory_id
               WHERE m.source_project IS NOT NULL AND m.is_archived = 0
                 AND a.access_type NOT LIKE 'recall:%-lead'
               GROUP BY m.source_project HAVING n >= 2
               ORDER BY avg_rel ASC LIMIT 10""",
        ).fetchall()
        for proj, _avg, _n in rows:
            if proj and proj not in labels:
                labels.append(str(proj))
    except sqlite3.OperationalError:
        pass
    # 3) Thin source_project zones: projects with the FEWEST memories (the brain's
    #    sparse domains -- probe-worthy regardless of recall history). Structural,
    #    computed -- never a hardcoded topic.
    try:
        thin = conn.execute(
            """SELECT source_project, COUNT(*) n FROM memories
               WHERE source_project IS NOT NULL AND is_archived = 0
               GROUP BY source_project ORDER BY n ASC LIMIT 10""",
        ).fetchall()
        for proj, _n in thin:
            if proj and proj not in labels:
                labels.append(str(proj))
    except sqlite3.OperationalError:
        pass
    return labels


def _retrieval_strength(conn: sqlite3.Connection, label: str, top_k: int,
                        cfg: Optional[Dict[str, Any]] = None) -> float:
    """Retrieval strength of `label` against the brain -- the SOLE pass/fail signal
    for the probe-QA confabulation gate (low strength == a filed gap; high strength
    == the brain already knows it). NO model answer is EVER consulted: strength is
    measured purely by how well the brain RETRIEVES around the label.

    Two backends, both retrieval-only (the architectural gate is identical):

      * SHARPER (optional, opt-in via cfg['probe_strength_backend'] == 'hybrid'):
        run a real hybrid recall of the label against the brain and use the top-k
        MEAN relevance as the strength signal. This is a true recall probe -- it
        distinguishes a genuinely-thin zone from a label that merely happens to be
        a common tag. It is gated + fully fail-soft: any failure (no hybrid module,
        no encoder, empty result, exception) falls back to the structural proxy, so
        the offline test path NEVER touches a model.

      * STRUCTURAL PROXY (default + the offline/test fallback): FTS-free,
        embedding-free, self-contained -- count active memories carrying the label
        as project / category / tag, normalized by top_k.

    CRITICAL: BOTH backends derive pass/fail ONLY from retrieval against the brain.
    There is NO 'model answers -> model grades itself' path in either -- the
    confabulation gate is unchanged. A hybrid score is a RETRIEVAL relevance, not a
    generated answer.
    """
    cfg = cfg or {}
    backend = str(cfg.get("probe_strength_backend", "structural")).lower()

    if backend == "hybrid":
        hybrid_val = _retrieval_strength_hybrid(conn, label, top_k, cfg)
        if hybrid_val is not None:
            return hybrid_val
        # else: fall through to the structural proxy (fail-soft, self-contained).

    return _retrieval_strength_structural(conn, label, top_k)


def _retrieval_strength_structural(conn: sqlite3.Connection, label: str,
                                   top_k: int) -> float:
    """The self-contained structural proxy: count active memories carrying the
    label as project / category / tag, normalized by top_k. Offline + agnostic."""
    n = 0
    try:
        n += conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_archived=0 AND source_project=?",
            (label,),
        ).fetchone()[0]
        n += conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_archived=0 AND category=?",
            (label,),
        ).fetchone()[0]
        n += conn.execute(
            """SELECT COUNT(*) FROM memory_tags mt JOIN memories m ON m.id=mt.memory_id
               WHERE m.is_archived=0 AND mt.tag=?""",
            (label,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 1.0  # cannot probe -> never falsely file a gap
    # Saturating normalization: top_k supporting memories == full strength.
    return min(1.0, n / float(max(1, top_k)))


def _retrieval_strength_hybrid(conn: sqlite3.Connection, label: str, top_k: int,
                               cfg: Dict[str, Any]) -> Optional[float]:
    """Sharper, opt-in retrieval-strength backend: a REAL hybrid recall of the
    label, scored by the top-k mean relevance. Returns a strength in [0,1], or
    None on ANY failure (so the caller falls back to the structural proxy).

    A test/offline caller can inject a deterministic recall via
    cfg['_strength_recall'] (a callable (label, top_k) -> List[float] of relevance
    scores) so the sharper path is exercisable WITHOUT a model. In production the
    callable is resolved lazily from hybrid_retrieval.recall_hybrid (which needs the
    Nomic encoder); if that is unavailable we return None -> proxy. STILL no model
    self-grade: relevance is a retrieval signal, not a generated answer.
    """
    # 1) Injected recall (test/offline determinism, or a custom backend).
    recall_fn = cfg.get("_strength_recall")
    rels: Optional[List[float]] = None
    if callable(recall_fn):
        try:
            rels = list(recall_fn(label, top_k))
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("injected strength recall failed: %s", e)
            return None
    else:
        # 2) Production hybrid recall (lazy; needs the encoder). Fail-soft to None.
        try:
            import hybrid_retrieval as _hr  # type: ignore
        except ImportError:
            return None
        encode = getattr(_hr, "_encode", None) or getattr(_hr, "encode", None)
        recall_hybrid = getattr(_hr, "recall_hybrid", None)
        if encode is None or recall_hybrid is None:
            return None
        try:
            qvec = encode(label, prefix="search_query")
            results = recall_hybrid(conn, label, qvec, top_k=top_k, mode="hybrid")
            rels = [float(r.get("score", 0.0)) for r in (results or [])]
        except Exception as e:  # pragma: no cover - encoder/model may be absent
            logger.debug("hybrid strength recall failed: %s", e)
            return None

    if not rels:
        return 0.0  # the brain retrieved nothing for the label -> genuinely weak.
    # Top-k mean relevance, squashed into [0,1]. Hybrid scores are already in a
    # bounded relevance range; clamp defensively so the gate stays well-behaved.
    mean_rel = sum(rels[:top_k]) / float(min(len(rels), top_k))
    return max(0.0, min(1.0, mean_rel))


def _label_memory_count(conn: sqlite3.Connection, label: str) -> int:
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_archived=0 AND source_project=?",
            (label,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


# ============================================================
# Phase 4 -- Gap dispatch (the EXISTING Loop B, daytime). Just wire it.
# ============================================================

def phase_gap_dispatch(conn: sqlite3.Connection, cfg: Dict[str, Any],
                       batch_id: Optional[int]) -> Dict[str, Any]:
    """Wire the EXISTING gap_mapping.dispatch_auto_research into the sequencer as
    a daytime phase. Do NOT rebuild it. Dry-run/test never dispatches real CLI.
    """
    if gap_mapping is None:
        return _phase_result("skipped", reason="gap_mapping module absent")
    if bool(cfg.get("_dry_run")) or bool(cfg.get("_no_external_dispatch")):
        # Count what WOULD be dispatched without firing the CLI.
        try:
            pending = conn.execute(
                "SELECT COUNT(*) FROM gap_inventory WHERE researched=0"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pending = 0
        return _phase_result("dry-run", would_dispatch=min(pending, int(cfg["gap_max_dispatch"])))
    db_path = cfg.get("_db_path")
    try:
        res = gap_mapping.dispatch_auto_research(
            db_path=db_path, max_gaps=int(cfg["gap_max_dispatch"]),
        )
        return _phase_result("ok", dispatch=res)
    except Exception as e:
        return _phase_result("error", reason=str(e))


# ============================================================
# Phase 5 -- RAPTOR synthesis (H, daytime/weekly/churn-triggered)
# ============================================================

def phase_raptor_synthesis(conn: sqlite3.Connection, cfg: Dict[str, Any],
                           batch_id: Optional[int]) -> Dict[str, Any]:
    """Cluster the graph, summarize each churned cluster into a kind='semantic',
    tag='synthesis-node' memory. Idempotent via raptor_clusters.cluster_signature
    (a re-summarize is an UPDATE, never a duplicate). A synthesis-drift quiz keeps
    the summary honest about its children. RAPTOR == reuse semantic + a tag, NOT a
    new kind.
    """
    if not _table_exists(conn, "raptor_clusters"):
        return _phase_result("skipped", reason="raptor_clusters absent")

    clusters = _compute_clusters(conn, cfg)
    if not clusters:
        return _phase_result("ok", clusters=0, summarized=0)

    dry = bool(cfg.get("_dry_run"))
    summarized = 0
    updated = 0
    created = 0
    for sig, members, label in clusters[: int(cfg["raptor_max_clusters"])]:
        existing = conn.execute(
            "SELECT id, member_count, synthesis_memory_id FROM raptor_clusters WHERE cluster_signature=?",
            (sig,),
        ).fetchone()
        churn_min = int(cfg["raptor_churn_min_new_members"])
        if existing:
            # Churn-trigger: only re-summarize when membership changed materially.
            if abs(existing[1] - len(members)) < churn_min:
                continue
            summary_text = _raptor_summarize(conn, cfg, label, members) if not dry else None
            syn_id = existing[2]
            if summary_text and syn_id:
                conn.execute("UPDATE memories SET content=?, updated_at=datetime('now') WHERE id=?",
                             (summary_text, syn_id))
            # Anti-laundering on CHURN: a synthesis node created when the cluster
            # was clean would otherwise be washed clean here once a freshly
            # untrusted member joins. Re-evaluate taint every churn and inherit
            # it onto the existing node (idempotent; set-to-1 only). This is the
            # actual "washed clean on next churn" vector the breach exploited.
            if syn_id and not dry and _cluster_has_untrusted_member(conn, members):
                _stamp_synthesis_taint(conn, syn_id, "raptor_synthesis")
            conn.execute(
                """UPDATE raptor_clusters SET member_count=?, member_ids_json=?,
                       cluster_label=?, last_summarized_at=datetime('now'),
                       updated_at=datetime('now') WHERE id=?""",
                (len(members), json.dumps(members), label, existing[0]),
            )
            updated += 1
            summarized += 1 if summary_text else 0
        else:
            summary_text = _raptor_summarize(conn, cfg, label, members) if not dry else None
            syn_id = None
            if summary_text:
                syn_id = _store_synthesis_node(conn, cfg, label, summary_text, members)
                summarized += 1
            conn.execute(
                """INSERT INTO raptor_clusters
                       (cluster_signature, member_count, member_ids_json,
                        synthesis_memory_id, cluster_label, last_summarized_at,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'))""",
                (sig, len(members), json.dumps(members), syn_id, label),
            )
            created += 1
        # Synthesis-drift quiz (cheap, structural): does the synthesis node still
        # share members with the cluster? Records a pass/fail flag.
        drift_ok = 1 if _drift_quiz(conn, sig, members) else 0
        conn.execute("UPDATE raptor_clusters SET drift_quiz_passed=? WHERE cluster_signature=?",
                     (drift_ok, sig))
    conn.commit()
    return _phase_result(
        "ok" if not dry else "dry-run",
        clusters=len(clusters), created=created, updated=updated, summarized=summarized,
    )


def _compute_clusters(conn: sqlite3.Connection, cfg: Dict[str, Any]) -> List[Tuple[str, List[int], str]]:
    """Cluster the graph using the EXISTING community structure: group active
    memories by source_project (the cheapest stable community signal), keep
    clusters >= min size. Signature is content-free (a hash of sorted member ids).
    Label is the GROUP key -- a computed structural key, never a hardcoded topic.
    """
    out: List[Tuple[str, List[int], str]] = []
    min_size = int(cfg["raptor_min_cluster_size"])
    # THE DREAM FIX (A1/A3 class sweep): a dream or a playbook candidate is a synthesis; a RAPTOR summary of a
    # cluster of them would be stored as kind='semantic' with derived_from NULL and wash dream text back into
    # recall's memory lane unmarked. Synthesis rows are never cluster members (the brain never summarises its own
    # dreams as if they were recorded memories; the same rule the dream selectors apply to pairs).
    try:
        rows = conn.execute(
            """SELECT source_project, GROUP_CONCAT(id) ids, COUNT(*) n
               FROM memories WHERE is_archived=0 AND source_project IS NOT NULL
                 AND COALESCE(derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')
               GROUP BY source_project HAVING n >= ? ORDER BY n DESC""",
            (min_size,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    import hashlib
    for project, ids_csv, _n in rows:
        members = sorted(int(x) for x in ids_csv.split(",") if x)
        sig = hashlib.sha1(("|".join(map(str, members))).encode()).hexdigest()[:16]
        out.append((sig, members, str(project)))
    return out


def _raptor_summarize(conn, cfg, label, members) -> Optional[str]:
    """Summarize a cluster via the synthesis mouth (claude -p MVP). Fully guarded;
    returns None on any failure so the cluster is simply not summarized this run.
    A mock can patch memory_dreaming.dream_on_pair-style mouth via cfg hook.
    """
    mouth = cfg.get("_synthesis_mouth")
    snippets = []
    for mid in members[:8]:
        row = conn.execute("SELECT summary FROM memories WHERE id=?", (mid,)).fetchone()
        if row and row[0]:
            snippets.append(str(row[0])[:120])
    if mouth is not None:
        try:
            return mouth(label, snippets)
        except Exception:
            return None
    # Default mouth: claude -p, guarded. Content-free framing (no domain priors).
    prompt = (
        "These memory summaries form one cluster. Write 2-3 sentences distilling "
        "the shared theme. Respond with the distillation only.\n\n"
        + "\n".join(f"- {s}" for s in snippets)
    )
    return _claude_p(prompt, cfg)


# --- Untrusted-source taint inheritance (anti-laundering) --------------------
# A RAPTOR / playbook synthesis is a self-authored write at AUTHORED trust. If
# ANY source member carries the Warden's immutable ``untrusted_source=1`` mark,
# the synthesis MUST inherit the taint, or the next consolidation churn would
# wash the marked rows clean (a higher-trust unframed synthesis re-stating their
# content). Mirrors the A3 dream/whisper trust-floor seal: a low confidence + a
# ``derived_from`` sentinel flips trust_tier to the DERIVED branch (so recall
# framing fires + trust floors), AND we stamp the immutable flag so the
# trust_tier untrusted-source CEILING bites even if derived_from is later lost.

#: Confidence stamped on a TAINTED synthesis node — low, so trust_tier's
#: headroom (self-confidence dominates) lands well below the Drew baseline. Same
#: value as the A3 dream-synthesis floor.
_TAINTED_SYNTHESIS_CONFIDENCE: float = 0.40


def _cluster_has_untrusted_member(conn, members) -> bool:
    """True if ANY member id carries ``untrusted_source=1``. Fail-graceful: a
    missing column / query error / empty members reads as NOT tainted (the
    synthesis stays authored — we never spuriously taint a clean cluster)."""
    ids = [int(m) for m in (members or []) if str(m).strip().lstrip("-").isdigit()]
    if not ids:
        return False
    try:
        placeholders = ",".join("?" for _ in ids)
        row = conn.execute(
            f"SELECT 1 FROM memories WHERE untrusted_source=1 AND id IN ({placeholders}) LIMIT 1",
            ids,
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _project_has_untrusted_member(conn, project, kind=None) -> bool:
    """True if ANY memory in ``project`` (optionally of ``kind``) is untrusted.
    Used by the playbook path, whose members are addressed by source_project
    rather than by an explicit id list. Fail-graceful (error/missing -> False)."""
    if not project:
        return False
    try:
        if kind:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE untrusted_source=1 AND source_project=? "
                "AND kind=? LIMIT 1",
                (project, kind),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE untrusted_source=1 AND source_project=? LIMIT 1",
                (project,),
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _stamp_synthesis_taint(conn, mid, derived_from: str) -> None:
    """Stamp a just-written synthesis row with the untrusted birth-mark + a
    derived_from sentinel + a low confidence, so it inherits its cluster's taint.
    Fail-graceful: any error leaves the row as-authored (the ceiling in
    trust_tier still applies once the flag lands; here we best-effort all three).
    The immutability trigger permits set-to-1 (only clearing 1->0 aborts)."""
    try:
        conn.execute(
            "UPDATE memories SET untrusted_source=1, derived_from=?, confidence=? WHERE id=?",
            (derived_from, _TAINTED_SYNTHESIS_CONFIDENCE, mid),
        )
        # Make the inherited taint queryable as a tag too (parallels the A-seal
        # provenance visibility; recall framing keys on the column, not the tag).
        conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, 'untrusted-source')",
                     (mid,))
    except Exception as e:
        logger.debug("stamp synthesis taint failed for id=%s: %s", mid, e)


def _store_synthesis_node(conn, cfg, label, text, members) -> Optional[int]:
    """Store the RAPTOR distillation as kind='semantic' tag='synthesis-node'.

    If the cluster contains ANY untrusted-source member, the synthesis INHERITS
    the taint (untrusted_source=1 + derived_from='raptor_synthesis' + low
    confidence) so it ranks below authored memory and recall frames it. A clean
    cluster yields a normal authored synthesis (unchanged behavior)."""
    try:
        summary = f"Synthesis of cluster [{str(label)[:60]}] ({len(members)} memories)"
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        has_brain = "source_brain" in cols
        if has_brain:
            cur = conn.execute(
                """INSERT INTO memories (content, summary, category, importance, kind,
                       source_project, source_user, source_brain, created_at, updated_at, last_accessed)
                   VALUES (?, ?, 'insight', 0.5, 'semantic', 'dream-pipeline', NULL, ?,
                           datetime('now'), datetime('now'), datetime('now'))""",
                (text, summary, _running_brain()),
            )
        else:
            cur = conn.execute(
                """INSERT INTO memories (content, summary, category, importance, kind,
                       source_project, created_at, updated_at, last_accessed)
                   VALUES (?, ?, 'insight', 0.5, 'semantic', 'dream-pipeline',
                           datetime('now'), datetime('now'), datetime('now'))""",
                (text, summary),
            )
        mid = cur.lastrowid
        for tag in ("synthesis-node", "raptor", "dream-synthesis"):
            conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                         (mid, tag))
        # Anti-laundering: inherit cluster taint onto the synthesis node.
        if _cluster_has_untrusted_member(conn, members):
            _stamp_synthesis_taint(conn, mid, "raptor_synthesis")
        return mid
    except Exception as e:
        logger.debug("store synthesis node failed: %s", e)
        return None


def _drift_quiz(conn, sig, members) -> bool:
    row = conn.execute(
        "SELECT synthesis_memory_id, member_ids_json FROM raptor_clusters WHERE cluster_signature=?",
        (sig,),
    ).fetchone()
    if not row or row[0] is None:
        return True  # nothing to drift against yet
    try:
        prior = set(json.loads(row[1] or "[]"))
    except Exception:
        prior = set()
    if not prior:
        return True
    overlap = len(prior & set(members)) / max(1, len(prior))
    return overlap >= 0.5


# ============================================================
# Phase 6 -- Playbook emission (H, nocturnal)
# ============================================================

def phase_playbook_emission(conn: sqlite3.Connection, cfg: Dict[str, Any],
                            batch_id: Optional[int]) -> Dict[str, Any]:
    """Detect a repeated procedural sequence in the procedural log -> emit a
    kind='procedural' memory tagged 'playbook', source='dream-synthesis', behind
    a RAISED evidence bar (dream-authored == unvalidated). Graduation rides the
    EXISTING skill_graduation behind that bar -- we never auto-promote.
    """
    sequences = _detect_repeated_sequences(conn, cfg)
    if not sequences:
        return _phase_result("ok", candidates=0, emitted=0)

    dry = bool(cfg.get("_dry_run"))
    emitted = 0
    duplicates = 0
    bar = float(cfg["playbook_evidence_bar"])
    for seq_label, occ, evidence in sequences:
        if occ < int(cfg["playbook_min_occurrences"]):
            continue
        if evidence < bar:
            continue
        if dry:
            emitted += 1
            continue
        note: Dict[str, Any] = {}
        pid = _emit_playbook(conn, cfg, seq_label, occ, evidence, note=note)
        if pid:
            emitted += 1
        elif note.get("duplicate_of"):
            duplicates += 1
    conn.commit()
    return _phase_result("ok" if not dry else "dry-run",
                         candidates=len(sequences), emitted=emitted, duplicates_skipped=duplicates,
                         evidence_bar=bar)


def _detect_repeated_sequences(conn, cfg) -> List[Tuple[str, int, float]]:
    """Find repeated procedural co-occurrence: procedural memories that recur in
    the same source_project form a candidate playbook. Structural, content-free.
    Derived rows are NOT members (the dream fix, A8): the playbook candidates are themselves procedural rows in
    the 'dream-pipeline' project, so they were counted as a "repeated procedure" and the organ emitted playbooks
    about its own playbooks (116 such rows on the owner's brain).
    Returns (label, occurrences, evidence_strength).
    """
    out: List[Tuple[str, int, float]] = []
    try:
        rows = conn.execute(
            """SELECT source_project, COUNT(*) n FROM memories
               WHERE is_archived=0 AND kind='procedural' AND source_project IS NOT NULL
                 AND derived_from IS NULL
               GROUP BY source_project HAVING n >= ? ORDER BY n DESC""",
            (int(cfg["playbook_min_sequence_len"]),),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for project, n in rows:
        # Evidence = density of procedural mass in this project, saturating.
        evidence = min(1.0, n / float(max(1, int(cfg["playbook_min_sequence_len"]) * 2)))
        out.append((str(project), n, round(evidence, 4)))
    return out


PLAYBOOK_DERIVED_FROM = "playbook_synthesis"
PLAYBOOK_BIRTH_CONFIDENCE = 0.40


def _emit_playbook(conn, cfg, label, occ, evidence, note: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Emit ONE playbook candidate -- IDEMPOTENT ON ITS CONTENT HASH AND ITS LABEL (the dream fix, A8; R1-L3). The
    same text, or any active candidate for the same label, is not written again: the nightly re-emission produced
    3,574 rows holding 197 distinct texts across 36 labels. A new
    candidate is born stamped as synthesis (derived_from 'playbook_synthesis', confidence 0.40), never at 1.0 as an
    authored procedure."""
    note = note if note is not None else {}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        has_brain = "source_brain" in cols
        content = (
            f"[Playbook -- dream-synthesis, unvalidated]\n"
            f"A repeated procedural pattern was observed in [{str(label)[:60]}] "
            f"across {occ} procedural memories. Candidate for graduation behind the "
            f"raised dream-authored evidence bar (evidence={evidence})."
        )
        import hashlib as _hl
        note["content_sha256"] = _hl.sha256(content.encode("utf-8")).hexdigest()[:16]
        # R1-L3: ONE active candidate per LABEL -- the text carries a moving occurrence count and evidence value, so
        # exact-text idempotence still minted a variant each time a project's count moved (5.5 per label)
        _prefix = "Playbook candidate [%s]" % str(label)[:60]
        dup = conn.execute(
            "SELECT id FROM memories WHERE source_project='dream-pipeline' AND kind='procedural' "
            "AND is_archived=0 AND (content=? OR substr(summary, 1, ?) = ?) LIMIT 1",
            (content, len(_prefix), _prefix)).fetchone()
        if dup:
            note["duplicate_of"] = dup[0]
            return None
        summary = f"Playbook candidate [{str(label)[:60]}] ({occ}x)"
        if has_brain:
            cur = conn.execute(
                """INSERT INTO memories (content, summary, category, importance, kind,
                       source_project, source_context, source_user, source_brain,
                       derived_from, confidence, created_at, updated_at, last_accessed)
                   VALUES (?, ?, 'workflow_pattern', 0.45, 'procedural',
                           'dream-pipeline', 'dream-synthesis', NULL, ?, ?, ?,
                           datetime('now'), datetime('now'), datetime('now'))""",
                (content, summary, _running_brain(), PLAYBOOK_DERIVED_FROM, PLAYBOOK_BIRTH_CONFIDENCE),
            )
        else:
            cur = conn.execute(
                """INSERT INTO memories (content, summary, category, importance, kind,
                       source_project, source_context, derived_from, confidence,
                       created_at, updated_at, last_accessed)
                   VALUES (?, ?, 'workflow_pattern', 0.45, 'procedural',
                           'dream-pipeline', 'dream-synthesis', ?, ?,
                           datetime('now'), datetime('now'), datetime('now'))""",
                (content, summary, PLAYBOOK_DERIVED_FROM, PLAYBOOK_BIRTH_CONFIDENCE),
            )
        pid = cur.lastrowid
        for tag in ("playbook", "dream-synthesis", "unvalidated"):
            conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                         (pid, tag))
        # Anti-laundering: the playbook's members are the procedural memories in
        # this source_project. If ANY is untrusted, the playbook inherits the
        # taint (derived_from='playbook_synthesis' + low confidence + immutable
        # flag) so it ranks below authored memory and recall frames it.
        if _project_has_untrusted_member(conn, label, kind="procedural"):
            _stamp_synthesis_taint(conn, pid, "playbook_synthesis")
        return pid
    except Exception as e:
        logger.debug("emit playbook failed: %s", e)
        return None


# ============================================================
# The synthesis mouth (claude -p MVP, guarded, mockable)
# ============================================================

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


def _claude_p(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    """The synthesis mouth, through the thinker seam (text only). Fully guarded; returns None on any failure,
    which is LOGGED and COUNTED (R8: this branch once returned None with no log line at all). Tests MOCK this (or
    pass cfg['_synthesis_mouth']) so the suite runs offline. The name is kept for its callers."""
    if cfg.get("_offline"):
        return None
    if cfg.get("_thinker_starved"):
        # STARVED earlier in this run (round 1): one unavailable reply is the night's answer; do not ask again.
        # "e) not now" (round 2, m4) is the owner's answer, not a failure: nothing is counted.
        if not cfg.get("_thinker_declined"):
            _llm_failure_sink(cfg)("thinker unavailable (%s) earlier in this run: not asked" % cfg["_thinker_starved"])
        return None
    tier = cfg.get("claude_model")
    try:
        res = _thinker.think(prompt, purpose="dream_pipeline",
                             tier=tier if tier in _thinker.TIERS else None,
                             timeout_s=cfg["claude_timeout_seconds"])
    except _thinker.ThinkError as e:
        if _declined(e):
            # UNCONFIGURED (round 2, m4): the owner answered e) not now. Stop asking; count no failure.
            logger.info("thinker off (synthesis): %s", _UNCONFIGURED_NOTE)
            cfg["_thinker_declined"] = True
        else:
            logger.warning("thinker (synthesis) %s: %s", e.kind, e)
            _llm_failure_sink(cfg)("thinker %s: %s" % (e.kind, e))
        if isinstance(e, _thinker.ThinkUnavailable):
            cfg["_thinker_starved"] = e.reason
        return None
    out = res.text.strip()
    return out if out and out.upper() != "NULL" and len(out) >= 10 else None


def _llm_failure_sink(cfg: Dict[str, Any]) -> Callable[[str], None]:
    """Where a phase's failed LLM calls are counted (R8). The sequencer gives every phase
    a fresh list at cfg['_llm_failures'] and reads it back afterwards; outside the
    sequencer (a direct call, a test) there is no list and the sink is a no-op."""
    sink = cfg.get("_llm_failures")
    if isinstance(sink, list):
        return sink.append
    return lambda _reason: None


# ============================================================
# THE PHASE SEQUENCER (the spine)
# ============================================================

class Phase:
    """A single fail-soft, time-boxed, toggleable phase descriptor."""
    __slots__ = ("name", "fn", "nocturnal", "enabled_default", "time_box_s", "churn_trigger")

    def __init__(self, name: str, fn: Callable, nocturnal: bool,
                 enabled_default: bool = True, time_box_s: Optional[float] = None,
                 churn_trigger: bool = False):
        self.name = name
        self.fn = fn
        self.nocturnal = nocturnal
        self.enabled_default = enabled_default
        self.time_box_s = time_box_s
        self.churn_trigger = churn_trigger


# The canonical ordered phase list. THE CRON READS THIS; the sequencer owns the
# order. Nocturnal phases run at night (cheap, lock-bounded); daytime phases
# (nocturnal=False) defer to the gpu_guard-gated burst window.
PHASES: List[Phase] = [
    Phase("seed_selection",     phase_seed_selection,     nocturnal=True),
    Phase("nrem_consolidation", phase_nrem_consolidation, nocturnal=True),
    Phase("rem_dreaming",       phase_rem_dreaming,       nocturnal=True,
          time_box_s=DEFAULT_CONFIG["rem_time_box_s"]),
    Phase("probe_qa",           phase_probe_qa,           nocturnal=True),
    Phase("gap_dispatch",       phase_gap_dispatch,       nocturnal=False),
    Phase("raptor_synthesis",   phase_raptor_synthesis,   nocturnal=False,
          churn_trigger=True, time_box_s=300.0),
    Phase("playbook_emission",  phase_playbook_emission,  nocturnal=True),
]


def _phase_enabled(phase: Phase, cfg: Dict[str, Any]) -> bool:
    overrides = cfg.get("phases_enabled")
    if isinstance(overrides, dict) and phase.name in overrides:
        return bool(overrides[phase.name])
    return phase.enabled_default


class _TimeBox:
    """A soft time box: phases check ``box.expired()`` cooperatively; the sequencer
    records 'timeout' if a phase reports it ran out. (We never SIGALRM a sqlite
    write mid-transaction -- that risks corruption; phases are short by design.)
    """
    def __init__(self, seconds: float):
        self.deadline = time.time() + seconds

    def expired(self) -> bool:
        return time.time() > self.deadline


# dream_phase_log.status CHECK (migration 027) admits only these. Round 1's 'starved' and round 2's 'unconfigured'
# phase statuses are not among them, so their row was refused by the CHECK and dropped (logged at DEBUG only):
# a starved night left no phase row at all. They land as 'skipped', with the real status first in `detail`.
_PHASE_LOG_FOLDED = {"starved": "skipped", "unconfigured": "skipped"}


def _log_phase(conn, batch_id, phase: Phase, status: str, detail: Optional[str],
               result: Optional[dict], duration: float):
    if not _table_exists(conn, "dream_phase_log"):
        return
    if status in _PHASE_LOG_FOLDED:
        why = (result or {}).get("note") or (result or {}).get("starved_reason") or ""
        detail = "%s%s%s" % (status, (": " + str(why)) if why else "", ("; " + detail) if detail else "")
        status = _PHASE_LOG_FOLDED[status]
    try:
        conn.execute(
            """INSERT INTO dream_phase_log
                   (batch_id, phase_name, nocturnal, status, detail, result_json,
                    duration_s, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (batch_id, phase.name, 1 if phase.nocturnal else 0, status,
             (detail or "")[:500],
             json.dumps(result)[:4000] if result is not None else None,
             round(duration, 3)),
        )
        conn.commit()
    except Exception as e:
        logger.debug("phase log write failed: %s", e)


def run_dream_cycle(db_path: str, config: Optional[dict] = None,
                    dry_run: bool = False, daytime: bool = False) -> Dict[str, Any]:
    """THE PHASE SEQUENCER. Runs the ordered PHASES list. Each phase is FAIL-SOFT
    (its own try/except -> logs + returns {status:'error'}, NEVER aborts the
    cycle), time-boxed, and toggleable. Nocturnal phases run always; daytime
    phases run only when ``daytime=True`` (the burst window, gpu_guard-gated) or
    via cfg -- otherwise they are recorded 'deferred'.
    """
    t0 = time.time()
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cfg["_dry_run"] = dry_run
    cfg["_db_path"] = db_path

    # The lock keeps the nocturnal cycle single-writer. dry_run skips locking. It is THIS BRAIN's lock
    # (memory_dreaming.run_lock_path); a config naming a legacy machine-global name keeps working and says so.
    lock_note = None
    if memory_dreaming is not None:
        lock_path, lock_note = memory_dreaming.run_lock_path(db_path, cfg.get("lock_file"))
    else:
        lock_path = cfg.get("lock_file")
    cfg["lock_file"] = lock_path
    acquired = True
    if not dry_run and memory_dreaming is not None:
        try:
            acquired = memory_dreaming._acquire_lock(lock_path)
        except OSError as e:
            return {"status": "error", "reason": "could not create this brain's dream lock %s: %s" % (lock_path, e),
                    "lock_file": lock_path}
    if not acquired:
        out = {"status": "skipped", "reason": "another dream cycle is running", "lock_file": lock_path}
        if lock_note:
            out["lock_note"] = lock_note
        return out

    conn = _get_conn(db_path)
    try:
        if not is_migration_027_applied(conn):
            return {"status": "error", "message": "migration_027 not applied"}

        batch_id = _open_batch(conn, cfg)
        phase_outcomes: List[Dict[str, Any]] = []

        # Daytime burst gate (gpu_guard) -- advisory; daytime phases still run
        # only if daytime=True. The verdict is recorded for the falsifiable log.
        burst_verdict = _maybe_check_burst(cfg, daytime)

        for phase in PHASES:
            ph_t0 = time.time()
            if not _phase_enabled(phase, cfg):
                _log_phase(conn, batch_id, phase, "disabled", "toggled off", None, 0.0)
                phase_outcomes.append({"phase": phase.name, "status": "disabled"})
                continue
            # Daytime phases defer at night.
            if not phase.nocturnal and not daytime:
                _log_phase(conn, batch_id, phase, "deferred",
                           "daytime phase skipped at night", None, 0.0)
                phase_outcomes.append({"phase": phase.name, "status": "deferred"})
                continue
            seconds = phase.time_box_s or cfg["default_phase_time_box_s"]
            if phase.name == "rem_dreaming" and cfg.get("rem_time_box_s"):
                seconds = float(cfg["rem_time_box_s"])     # the REM knob is the run's, not only the import's
            box = _TimeBox(seconds)
            cfg["_time_box"] = box
            # R8: every failed LLM call this phase makes lands here (see _llm_failure_sink).
            cfg["_llm_failures"] = []
            # THE FAIL-SOFT GUARD: one phase failing NEVER aborts the cycle.
            try:
                result = phase.fn(conn, cfg, batch_id)
                status = result.get("status", "ok") if isinstance(result, dict) else "ok"
                if box.expired() and status in ("ok", "dry-run"):
                    status = "timeout"
                dur = time.time() - ph_t0
                fell_back = list(cfg.get("_llm_failures") or [])
                detail = None
                if fell_back:
                    # A phase whose LLM calls failed still ran -- on its heuristic path (a
                    # pair left undreamed, a cluster left unsummarised). That is not an
                    # error and not a clean success either; it is COUNTED and SAID, here
                    # and in dream_phase_log.detail, instead of reading as phases_ok.
                    detail = "%d LLM call(s) failed; fell back to the heuristic path; first: %s" % (
                        len(fell_back), fell_back[0])
                _log_phase(conn, batch_id, phase, status, detail, result, dur)
                outcome = {"phase": phase.name, "status": status, "result": result}
                if fell_back:
                    outcome["llm_fallbacks"] = len(fell_back)
                    outcome["llm_fallback_reasons"] = sorted(set(fell_back))[:3]
                phase_outcomes.append(outcome)
            except Exception as e:
                dur = time.time() - ph_t0
                logger.exception("phase %s failed (fail-soft, cycle continues)", phase.name)
                _log_phase(conn, batch_id, phase, "error", str(e), None, dur)
                phase_outcomes.append({"phase": phase.name, "status": "error",
                                       "error": str(e)})
                # CONTINUE -- do not abort.

        _close_batch(conn, batch_id, phase_outcomes, time.time() - t0)
        _SEED_CACHE.pop(id(conn), None)
        starved_reason = cfg.get("_thinker_starved")
        declined = bool(cfg.get("_thinker_declined"))
        return {
            "status": (("unconfigured" if declined else "starved" if starved_reason else "ok")
                       if not dry_run else "dry-run"),
            **({"unconfigured_note": _UNCONFIGURED_NOTE} if declined and not dry_run else {}),
            **({"starved_reason": starved_reason,
                "starved_note": "the thinker was unavailable (%s): no dream was written for the pairs it could "
                                "not answer; the next scheduled run is the retry" % starved_reason}
               if starved_reason and not declined and not dry_run else {}),
            "batch_id": batch_id,
            "daytime": daytime,
            "burst_verdict": burst_verdict,
            "phases": phase_outcomes,
            "phases_ok": sum(1 for p in phase_outcomes if p["status"] in ("ok", "dry-run")),
            "phases_error": sum(1 for p in phase_outcomes if p["status"] == "error"),
            "phases_deferred": sum(1 for p in phase_outcomes if p["status"] == "deferred"),
            # R8: a night of failed LLM calls used to read phases_error 0 and nothing else.
            "phases_fallback": sum(1 for p in phase_outcomes if p.get("llm_fallbacks")),
            "llm_calls_failed": sum(p.get("llm_fallbacks", 0) for p in phase_outcomes),
            "duration_seconds": round(time.time() - t0, 2),
            **({"lock_note": lock_note} if lock_note else {}),
        }
    except Exception as e:
        logger.exception("dream cycle sequencer crashed")
        return {"status": "error", "message": str(e),
                "duration_seconds": round(time.time() - t0, 2)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if not dry_run and memory_dreaming is not None:
            try:
                memory_dreaming._release_lock(lock_path)
            except Exception:
                pass


def _maybe_check_burst(cfg: Dict[str, Any], daytime: bool) -> Optional[dict]:
    """Advisory gpu_guard check for the daytime heavy phases. Never blocks the
    nocturnal cycle; only informs whether the burst window is available.
    """
    if not daytime or gpu_guard is None:
        return None
    try:
        profile = gpu_guard.BurstProfile(
            footprint_mb=int(cfg.get("burst_footprint_mb", 6144)),
            est_duration_s=int(cfg.get("burst_duration_s", 300)),
            purpose="dream-raptor",
        )
        verdict = gpu_guard.acquire(profile)
        # We DON'T hold the lock through the whole cycle; release immediately --
        # this is an advisory probe of whether the burst window is open. The
        # heavy phases run on the local Stage-1 mouth (claude -p) regardless;
        # the burst is an optimization, never a gate that blocks the cycle.
        try:
            gpu_guard.release(verdict)
        except Exception:
            pass
        return verdict.to_dict() if hasattr(verdict, "to_dict") else None
    except Exception as e:
        logger.debug("burst check failed (advisory, cycle continues): %s", e)
        return None


def _open_batch(conn, cfg) -> Optional[int]:
    if not _table_exists(conn, "dream_batches"):
        return None
    try:
        cur = conn.execute(
            "INSERT INTO dream_batches (config_snapshot, created_at) VALUES (?, datetime('now'))",
            (json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")
                         and not callable(v)})[:4000],),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        return None


def _close_batch(conn, batch_id, outcomes, duration):
    if batch_id is None or not _table_exists(conn, "dream_batches"):
        return
    try:
        meaningful = sum(o.get("result", {}).get("dreamed", 0) if isinstance(o.get("result"), dict) else 0
                         for o in outcomes)
        conn.execute(
            "UPDATE dream_batches SET meaningful_dreams=?, duration_seconds=? WHERE id=?",
            (meaningful, round(duration, 2), batch_id),
        )
        conn.commit()
    except Exception:
        pass


# ============================================================
# CLI
# ============================================================

EXIT_SKIPPED_LOCK = 75   # EX_TEMPFAIL: the dream lock was held; retry-able, not a failure
# STARVED (round 1): the thinker was unavailable (quota/auth/unreachable/...): nothing could be dreamt, nothing empty
# was written. memory_dreaming.EXIT_STARVED is the one definition; both dream units admit it (SuccessExitStatus).
EXIT_STARVED = 79


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The Unified Dream Pipeline (Voidloom L5 night-brain)")
    parser.add_argument("action", choices=["run", "dry-run", "migrate", "profile", "phases"])
    parser.add_argument("db_path", nargs="?",
                        default=str(Path(__file__).parent.parent / "claude_memory.db"))
    parser.add_argument("--config", default=None, help="Path to JSON config file")
    parser.add_argument("--migration", default=None, help="Path to migration_027 .sql")
    parser.add_argument("--daytime", action="store_true",
                        help="Run daytime phases (burst window, gpu_guard-gated)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    config = json.loads(Path(args.config).read_text()) if args.config else None

    if args.action == "migrate":
        mig = args.migration or str(Path(__file__).parent.parent / "migrations" / "migration_027_dream_pipeline.sql")
        print(json.dumps(apply_migration_027(args.db_path, mig), indent=2))
        return 0
    if args.action == "phases":
        print(json.dumps([
            {"name": p.name, "nocturnal": p.nocturnal,
             "enabled_default": p.enabled_default, "time_box_s": p.time_box_s,
             "churn_trigger": p.churn_trigger}
            for p in PHASES], indent=2))
        return 0
    if args.action == "profile":
        conn = _get_conn(args.db_path)
        res = rebuild_activity_profile(conn, {**DEFAULT_CONFIG, **(config or {})})
        conn.close()
        print(json.dumps(res, indent=2))
        return 0
    res = run_dream_cycle(args.db_path, config=config,
                          dry_run=(args.action == "dry-run"), daytime=args.daytime)
    print(json.dumps(res, indent=2))
    if res.get("status") == "skipped":
        # The lock was held by another dream cycle: nothing ran, nothing failed.
        # EX_TEMPFAIL (75) so systemd's Result=exit-code carries a status the
        # ledger can tell from a real failure (organ_run_log classifies 75 as
        # "skipped-lock"). Until 2026-09-20 this returned 1 and the night of
        # 2026-09-18 read as a failure when it was a race (#20269).
        return EXIT_SKIPPED_LOCK
    if res.get("status") == "starved":
        return EXIT_STARVED
    # "e) not now" (round 2, m4): UNCONFIGURED is the owner's answer -- a clear status and exit 0, never 79.
    return 0 if res.get("status") in ("ok", "dry-run", "unconfigured") else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""organ_yield_sentinel.py — the sensor that would have caught the 100-day silence.

WHAT IT WATCHES
    Not "did the organ run" (sleep_loop ran nightly for 100 days and exited 0).
    Not "does the organ have rows" (a one-shot backfill produces rows).
    It watches whether the organ's OWN OUTPUT TABLE grew, ON A CADENCE, recently.

        output grew on >=2 separated occasions, recently        -> HEALTHY
        input existed, output did not grow                      -> BROKEN
        its runs are being killed / no run completes            -> STALLED   (round 1)
        input was empty                                         -> STARVED
        the owner chose to switch it off ("e) not now")         -> UNCONFIGURED  (round 2; not an alarm)
        nothing schedules or calls this organ                   -> UNINVOKED
        the sensor could not see                                -> UNMEASURABLE

ROUND GAMMA — WHAT CHANGED, AND WHY EACH ONE MATTERED
    beta's reflector found three defects in this file and its timer was held back
    until they were fixed. All three were the SAME defect wearing three hats: a
    zero that had not been measured, inside the sensor built to end unmeasured
    zeros.

    1. A PER-ROW-STAMPED BACKFILL READ HEALTHY.  The old `fires <= 1` guard rested
       on "one instant is what a hand-run backfill leaves behind". That is what a
       TRANSACTION-stamped backfill leaves. A backfill that stamps PER ROW leaves
       one distinct timestamp per row -- alpha measured exactly that shape on this
       brain, 1,268 stamps one second apart -- and it sailed through the pulse
       check AND the staleness window, reporting "HEALTHY -- 1,268 rows across
       1268 fires". COUNT(DISTINCT clock) IS NOT A COUNT OF FIRES. It is a count
       of stamps, and the two differ by exactly the artifact the check exists to
       catch. Replaced with GAP CLUSTERING: distinct stamps separated by more than
       the organ's own `min_gap_seconds` are one BURST, and a cadence needs two
       bursts. `distinct_stamps` is still reported beside `bursts` so the
       difference between a burst and a cadence is visible, not inferred.

    2. IT EXITED 0 PRINTING "0 BROKEN of 5" WHEN IT MEASURED NOTHING.  Point an
       unreadable database at the old version and every reading came back
       UNMEASURABLE, the summary line said zero BROKEN, and the exit code was 0.
       systemd logged a success. That is the founding (0,True)/(0,False) collapse,
       reproduced inside the sensor written to end it. Exit codes are now
       three-valued -- 0 clean, 1 something is BROKEN, 2 the sensor could not see
       -- and the unit's SuccessExitStatus admits 0 and 1 only, so a blind sensor
       surfaces as a unit failure.  [SUPERSEDED 2026-09-25 -- see EXIT CODES below:
       "1 = something is BROKEN" collided with Python's own uncaught-exception status.]

    3. ABSENT AND UNREADABLE WERE THE SAME ANSWER.  The output read returned
       BROKEN for any sqlite3.Error, under a comment asserting the distinction it
       did not make, while the input read ten lines above returned UNMEASURABLE
       for the identical exception class. An ABSENT table is (0,True): I looked
       and there is nothing. An UNREADABLE one is (0,False): I could not tell.
       Existence is now probed against sqlite_master FIRST, so the two get
       different verdicts. sqlite_vec is also loaded, because a vec0 output table
       read without the extension raises "no such module: vec0" -- present, full
       of rows, and reported dead.

    AND FOUR MORE THE REFLECTOR NAMED
    4. `belief_engine.propose_edges` DOES NOT EXIST. The function is `propose`
       (belief_engine.py:539). An operator grepping the sensor's own output for
       the name it printed would have found nothing.
    5. STARVED WAS UNREACHABLE. Four of five organs used
       `SELECT COUNT(*) FROM memories WHERE is_archived=0` as their "input" --
       14,990 on this brain, zero only on an empty database. The discriminator the
       docstring calls the whole point had an EMPTY OBSERVATION SET. Each organ now
       declares the input IT actually consumes.
    6. THREE OF FIVE ORGANS HAVE NO SCHEDULED INVOKER, and BROKEN reads as "the
       logic failed", sending an operator to debug code nothing calls. There is now
       an UNINVOKED verdict, and the invoker is DERIVED AT RUNTIME by reading the
       installed unit files -- not declared in this file, because a declaration
       here is one more thing that can silently go stale.
    7. A FIXED 30-DAY WINDOW IS WRONG FOR ORGANS OF DIFFERING CADENCE. The tree
       holds a quarterly organ (arcane-manifest, up to 92 days between fires) and
       a monthly one. `stale_days` now travels with the organ.

ROUND 1 (2026-10-15, lane L5) -- THE BLIND GATE THE HEALTH CHECK FOUND
    Through eight nights of a stalled sleep loop (Oct 6-13: every run killed at the unit's 600 s wall, rows
    116-123 left 'running') this sensor rated sleep_loop.consolidate_episodics, temporal_edges.extract_edges and
    belief_engine.propose HEALTHY every morning. Each was true of its TABLE: Stage 1 committed before the kill,
    and memory_edges grew from the belief engine and the dreams. Growth is not completion, and yield is not
    correctness. Three changes:
    8. THE RUN LEDGER IS READ DIRECTLY (RUN_WATCH, contract C5). sleep_loop_runs: a 'running' row older than the
       unit's wall, or no success/partial for 36 h on a brain older than that, is STALLED -- a finding (exit 3).
       An organ the run feeds cannot read HEALTHY while its runs are STALLED.
    9. EDGE YIELD IS ATTRIBUTED. extract_edges is credited only with the edges Stage 2 writes (metadata carries
       `reason`, no `nrem`), not with every row any writer adds to memory_edges.
    10. A PRECISION CANARY for the belief engine: a current contradiction (or a pending proposal) joining two
       memories of the SAME organ template is the false class it no longer authors; any such row is BROKEN.

EXIT CODES (reference-host round, R6, 2026-09-25) -- FOUR values, none shared
    0  EXIT_CLEAN         every organ measured, none BROKEN or STALLED
    3  EXIT_BROKEN_FOUND  at least one organ is BROKEN or STALLED -- the sentinel doing its job
    2  EXIT_BLIND         the sentinel could not see (UNMEASURABLE)
    1  a CRASH            Python's own status for an uncaught exception; never
                          returned by this file on purpose
    Why 3 and not 1: the unit and the roster declared `SuccessExitStatus=0 1`
    because 1 meant "BROKEN organs found", and an uncaught exception ALSO exits 1.
    Measured on the reference host: this file used `Path` in main() without
    importing it, so EVERY run since at least 2026-09-18 died with a NameError
    before any work -- and systemd and the organ_run_log ledger recorded
    "success", five mornings of five. A crash and a finding were the same status.
    Now the unit admits 0 and 3; a crash (1) and a blind sensor (2) are unit
    failures. Every reader of the old code was updated in the same change: the
    packaged unit (cron/wizard-library-organ-yield.service) and the roster's
    service_extra (install/cron_roster.json); voidloom/tests/
    test_organ_yield_exit_codes.py binds all three to EXIT_BROKEN_FOUND.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UNIT_DIR = os.path.expanduser("~/.config/systemd/user")

# ---------------------------------------------------------------------------
# THE WATCH LIST. The population is DECLARED (deny-listed by omission) so adding
# an organ is a deliberate, reviewable diff. What is NOT declared here: whether
# anything invokes the organ. That is read from the machine at run time.
# ---------------------------------------------------------------------------
WATCH: List[Dict[str, Any]] = [
    dict(organ="sleep_loop.consolidate_episodics",
         module="sleep_loop",
         # FOUND BY THIS SENSOR AGAINST THE LIVE BRAIN, in this round: the first
         # version of the invoker oracle reported "NONE FOUND" for the one organ
         # on the list that unquestionably has a nightly timer. The unit does not
         # run `sleep_loop.py`; it runs `claude_memory.py sleep`. An oracle that
         # only matches the module's OWN filename cannot see indirect invocation,
         # and would have told an operator that the sleep loop is unscheduled.
         invoker_modules=("claude_memory",),
         input_sql="SELECT COUNT(*) FROM memories WHERE kind='episodic' AND is_archived=0",
         input_desc="unarchived episodic memories",
         out_table="consolidation_sources", out_clock="created_at",
         stale_days=3, min_gap_seconds=3600),
    dict(organ="temporal_edges.extract_edges",
         module="temporal_edges",
         invoker_modules=("claude_memory",),   # Stage 2 of the same nightly cycle
         input_sql="SELECT COUNT(*) FROM memories m WHERE m.is_archived=0 "
                   "AND NOT EXISTS (SELECT 1 FROM memory_edges e WHERE e.source_id=m.id AND e.valid_until IS NULL)",
         input_desc="unarchived memories with no current outgoing edge",
         out_table="memory_edges", out_clock="created_at",
         # ROUND 1 (9): ATTRIBUTED yield. memory_edges is written by Stage 2, the belief engine ('manual',
         # authored_by belief_engine) AND the dreams' NREM pass (stamped 'sleep_loop' too, metadata `nrem`).
         # Census of the live brain, 2026-10-15: Stage 2's rows carry metadata `reason` (22,000+), NREM rows
         # carry `nrem` (419), the belief engine's carry `authored_by` (7,578). Only Stage 2's count here.
         out_where=("extraction_method IN ('sleep_loop', 'heuristic') "
                    "AND json_extract(metadata, '$.reason') IS NOT NULL "
                    "AND json_extract(metadata, '$.nrem') IS NULL"),
         stale_days=3, min_gap_seconds=3600),
    dict(organ="provenance_graph.record_provenance",
         module="provenance_graph",
         # ROUND GAMMA bound three organic writers into this table, all of them
         # reached through the nightly cycle, so claude_memory belongs here too.
         invoker_modules=("claude_memory", "sleep_loop", "dream_pipeline", "memory_dreaming"),
         input_sql="SELECT COUNT(*) FROM memories WHERE derived_from IS NOT NULL "
                   "AND id NOT IN (SELECT derived_memory_id FROM memory_provenance)",
         input_desc="derived memories with no provenance edge",
         out_table="memory_provenance", out_clock="created_at",
         stale_days=7, min_gap_seconds=3600),
    dict(organ="belief_engine.propose",          # NOT propose_edges: it does not exist
         module="belief_engine",
         # 2026-10-26 (review F2): only pairs a person wrote are judged, so only
         # authored queue rows are input. Counting organ rows read "worked, then
         # stopped" -> BROKEN about a design choice; with no authored intake the
         # honest reading is STARVED.
         input_sql="SELECT COUNT(*) FROM belief_scan_queue q WHERE q.processed_at IS NULL AND q.memory_id IN "
                   "(SELECT memory_id FROM embedding_provenance WHERE organ IN "
                   "('claude_memory.store','claude_memory.update'))",
         input_desc="unprocessed queue rows a person authored (the only rows the engine judges)",
         # A brain before migration 033 has no provenance table and keeps the template
         # rule (belief_engine.authored_ids -> None): its input is the whole queue.
         input_sql_no_provenance="SELECT COUNT(*) FROM belief_scan_queue WHERE processed_at IS NULL",
         # Reached through the CLI wrapper, never directly: the nightly unit runs
         # `belief_cli.py scan`, not belief_engine.py. Without this the sensor
         # reported "invoker: NONE FOUND" for an organ that HAD a unit -- which is
         # the sensor lying in the one direction that matters, because NONE FOUND
         # is what tells an operator to go build a unit that already exists.
         invoker_modules=("belief_cli",),
         out_table="edge_proposals", out_clock="created_at",
         stale_days=30, min_gap_seconds=3600),
    dict(organ="cross_tier_bridges.discover_bridges",
         module="cross_tier_bridges",
         # The vec0 shadow table's key column is `id`, not `memory_id`; the first
         # draft named memory_id, the read raised, and the input came back n/a.
         # A predicate that cannot run reports UNMEASURABLE -- which is the right
         # answer and the wrong predicate.
         input_sql="SELECT COUNT(*) FROM memory_embeddings_v2_rowids",
         input_desc="memories carrying a live-tier embedding (bridge candidates)",
         out_table="cross_tier_bridges", out_clock="created_at",
         stale_days=30, min_gap_seconds=3600),
]

# ROW-INVARIANT WATCHES (the dream fix, A6). An organ whose defect is "the output exists, but not for every input
# row" is invisible to a growth/cadence check: dream_log grew every night for 108 days while every meaningful dream
# went unstored. Its watch reads the ROW INVARIANT instead -- dream_ledger.dream_watch, the SAME function
# `voidloom doctor` runs (strangers never get this sentinel's timer):
#   attempts > 0 and a meaningful row lacks its memory id -> BROKEN; attempts > 0 and 0 stored -> BROKEN;
#   attempts == 0 -> STARVED (with the window's LLM-failure count); backfill-tagged rows excluded.
INVARIANT_WATCH: List[Dict[str, Any]] = [
    dict(organ="dream_pipeline.phase_rem_dreaming", module="dream_pipeline",
         invoker_modules=("memory_dreaming",), window_days=2.0),
]

# ROUND 1 (8): the sleep loop's RUN LEDGER, read directly (contract C5, migration 040, as L3 built it:
# for_L5/C5_as_built.md). Status in {running, success, partial, error, timeout}; times UTC
# 'YYYY-MM-DD HH:MM:SS' for rows the new code writes, LOCAL 'YYYY-MM-DDTHH:MM:SS.ffffff' for older ones
# (a mixed history: ordered by id, a 'T' value without an offset read as host-local).
#   a 'running' row older than running_wall_seconds (the unit's TimeoutStartSec, 600)   -> STALLED
#   no success/partial within no_success_hours, on a brain older than that             -> STALLED
#   no run ever, on a brain younger than that                                          -> STARVED (first night)
# `feeds`: organs whose output this run writes; a HEALTHY reading of theirs becomes STALLED while the run is.
# ROUND 2 (R2c):
#   m6 -- a 'running' row is LIVE, not stalled, while a process holds this brain's run-lock (sleep_loop.run_lock_path:
#         <realpath(db)>.sleep-loop.lock). The owner's catch-up has no time limit; age alone called it STALLED at 600 s.
#   l4 -- `vouches`: a completed run in an organ's window whose own stage processed input vouches for that organ's
#         ONE recent burst. Right after a catch-up, the run read HEALTHY while extract_edges read BROKEN ("only ONE
#         burst") until the next real night: the veto ran one way only. {organ: (stage_log key, count, legacy column)}
RUN_WATCH: List[Dict[str, Any]] = [
    dict(organ="sleep_loop.run", module="sleep_loop", invoker_modules=("claude_memory",),
         table="sleep_loop_runs", running_wall_seconds=600, no_success_hours=36,
         lock_suffix=".sleep-loop.lock",
         feeds=("sleep_loop.consolidate_episodics", "temporal_edges.extract_edges"),
         vouches={"temporal_edges.extract_edges": ("edges", "processed", "edges_extracted"),
                  "sleep_loop.consolidate_episodics": ("consolidation", "consolidated", "memories_consolidated")}),
]

# ROUND 1 (10): the belief engine's precision canary. Not a growth check: an invariant on what it authored.
PRECISION_WATCH: List[Dict[str, Any]] = [
    dict(organ="belief_engine.precision", module="belief_engine", invoker_modules=("belief_cli",)),
]

VERDICTS = ("HEALTHY", "BROKEN", "STALLED", "STARVED", "UNCONFIGURED", "UNINVOKED", "UNMEASURABLE")

# The exit codes (module docstring, EXIT CODES). 1 is deliberately absent: it is the
# interpreter's status for an uncaught exception, and a crash must never read as a finding.
EXIT_CLEAN = 0
EXIT_BLIND = 2
EXIT_BROKEN_FOUND = 3


@dataclass
class Reading:
    organ: str
    verdict: str
    input_n: Optional[int]        # None => not measured
    output_rows: Optional[int]
    distinct_stamps: Optional[int]
    bursts: Optional[int]         # stamps clustered by min_gap_seconds
    recent_bursts: Optional[int]
    newest: Optional[str]
    stale_days: int
    invoker: Optional[str]
    detail: str


# ---------------------------------------------------------------------------
# Runtime oracles — read from the machine, never declared in this file.
# ---------------------------------------------------------------------------

def installed_units() -> Tuple[Dict[str, str], bool]:
    """{unit_name: ExecStart line} for every installed user service.

    Returns (mapping, readable). An unreadable unit directory yields
    ({}, False), which is NOT the same answer as "no units installed".
    """
    if not os.path.isdir(UNIT_DIR):
        return {}, False
    out: Dict[str, str] = {}
    try:
        for path in sorted(glob.glob(os.path.join(UNIT_DIR, "*.service"))):
            try:
                txt = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            m = re.search(r"^ExecStart=(.*)$", txt, re.M)
            if m:
                out[os.path.basename(path)] = m.group(1).strip()
    except OSError:
        return {}, False
    return out, True


def resolve_invoker(spec: Dict[str, Any], units: Dict[str, str]) -> Optional[str]:
    """Which installed unit would cause this organ to run?

    DERIVED, not declared: the module name is matched against every ExecStart on
    the machine. An organ reached only indirectly declares `invoker_modules` --
    the modules whose execution calls it -- and any of those counts.
    """
    names = [spec["module"]] + list(spec.get("invoker_modules", ()))
    hits = [u for u, cmd in units.items() if any(f"{n}.py" in cmd for n in names)]
    return ", ".join(sorted(hits)) if hits else None


def _table_present(con: sqlite3.Connection, name: str) -> Optional[bool]:
    """True/False if we could look; None if the catalogue itself was unreadable."""
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,)).fetchone()
        return row is not None
    except sqlite3.Error:
        return None


def _bursts(stamps: List[str], min_gap_seconds: int) -> Tuple[int, int]:
    """Cluster ordered timestamps into bursts separated by more than min_gap.

    THIS IS THE FIX FOR DEFECT 1. 1,268 rows one second apart are ONE burst, not
    1,268 fires. A nightly organ across ten nights is ten bursts. The count of
    stamps and the count of bursts are different questions and the old code asked
    the easy one.
    """
    if not stamps:
        return 0, 0
    import datetime as _dt

    def parse(s: str):
        s = str(s).strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(s[:26], fmt)
            except ValueError:
                continue
        return None

    parsed = [p for p in (parse(s) for s in stamps) if p is not None]
    if not parsed:
        # FOUND BY THIS FILE'S OWN MINIMAL PAIR, in this round. A clock column
        # holding values that are not dates read BROKEN -- 0 bursts, therefore
        # <= 1, therefore "a single write". It is nothing of the kind: I could
        # not tell. That is the (0,True)/(0,False) collapse a THIRD time, two
        # layers below where the reflector found the first two, and it survived
        # both of my fixes for them because the SQL succeeded. The number of
        # stamps I could actually PARSE now travels with the answer.
        return 0, 0
    parsed.sort()
    n = 1
    for a, b in zip(parsed, parsed[1:]):
        if (b - a).total_seconds() > min_gap_seconds:
            n += 1
    return n, len(parsed)


def _skip_note(con: sqlite3.Connection, invoker: Optional[str], stale: int) -> str:
    """The SKIPPED runs of this organ's invoker in its own window, from the run ledger (organ_run_log.skipped_runs).

    Round 3 G3: the 3-day cadence above is kept BY DESIGN -- two nights of output then a skipped night still reads
    HEALTHY (a skip produced nothing, and one quiet night inside the window is not a broken organ). But the skip is
    SAID here, so "HEALTHY" never hides that a night was skipped. Detail only: the verdict rule is unchanged.
    """
    if not invoker:
        return ""
    try:
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import organ_run_log
        rows = organ_run_log.skipped_runs(con, days=stale, services=[u.strip() for u in invoker.split(",")])
    except Exception as e:  # noqa: BLE001 - the note is the ledger's; an unreadable ledger is said, never "no skips"
        return " -- skipped runs UNMEASURED (the run ledger could not be read: %s)" % type(e).__name__
    if not rows:
        return ""
    n, newest = len(rows), rows[0]
    return (" -- and %d SKIPPED run%s in the last %dd (newest %s at %s, %s): a skipped night produced no output and "
            "is not counted as one" % (n, "" if n == 1 else "s", stale, newest["service_name"],
                                       newest["run_started_at"], newest["result"]))


def _one(con: sqlite3.Connection, spec: dict, units: Dict[str, str],
         units_readable: bool, stale_override: Optional[int] = None) -> Reading:
    r = _one_reading(con, spec, units, units_readable, stale_override)
    r.detail += _skip_note(con, r.invoker, r.stale_days)
    return r


def _one_reading(con: sqlite3.Connection, spec: dict, units: Dict[str, str],
                 units_readable: bool, stale_override: Optional[int] = None) -> Reading:
    organ = spec["organ"]
    stale = stale_override if stale_override is not None else int(spec.get("stale_days", 30))
    gap = int(spec.get("min_gap_seconds", 3600))
    invoker = resolve_invoker(spec, units) if units_readable else None
    R = lambda v, **kw: Reading(organ=organ, verdict=v, stale_days=stale,
                                invoker=invoker,
                                **{**dict(input_n=None, output_rows=None,
                                          distinct_stamps=None, bursts=None,
                                          recent_bursts=None, newest=None), **kw})

    # --- 0. is the output table there at all? (0,True) vs (0,False) ----------
    present = _table_present(con, spec["out_table"])
    if present is None:
        return R("UNMEASURABLE", detail=f"sqlite_master unreadable; cannot tell whether "
                                        f"{spec['out_table']} exists")
    if present is False:
        return R("BROKEN", output_rows=0, distinct_stamps=0, bursts=0,
                 detail=f"output table {spec['out_table']} is ABSENT — a real state, "
                        f"not a measurement failure. The organ's substrate was never created.")

    # --- 1. input --------------------------------------------------------------
    try:
        _sql = spec["input_sql"]
        if spec.get("input_sql_no_provenance") and not _table_present(con, "embedding_provenance"):
            _sql = spec["input_sql_no_provenance"]
        input_n = con.execute(_sql).fetchone()[0]
    except sqlite3.Error as e:
        input_n = None
        input_note = f"input UNMEASURABLE ({e})"
    else:
        input_note = f"{input_n:,} {spec['input_desc']}"

    # --- 2. output -------------------------------------------------------------
    where = spec.get("out_where")
    w_all = f" WHERE {where}" if where else ""
    w_and = f" AND ({where})" if where else ""
    try:
        rows, stamps_n, newest = con.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT "{spec["out_clock"]}"), MAX("{spec["out_clock"]}") '
            f'FROM "{spec["out_table"]}"{w_all}').fetchone()
    except sqlite3.Error as e:
        return R("UNMEASURABLE", input_n=input_n,
                 detail=f"output table {spec['out_table']} is PRESENT but unreadable: {e}")

    if rows == 0:
        if input_n == 0:
            return R("STARVED", input_n=0, output_rows=0, distinct_stamps=0, bursts=0,
                     detail=f"no output, and no input either ({spec['input_desc']} = 0). "
                            f"Nothing to do is not the same as failing to do it.")
        if input_n is None:
            return R("UNMEASURABLE", output_rows=0, distinct_stamps=0, bursts=0,
                     detail=f"no output, and {input_note} — cannot separate STARVED from BROKEN")
        if invoker is None and units_readable:
            return R("UNINVOKED", input_n=input_n, output_rows=0, distinct_stamps=0, bursts=0,
                     detail=f"no output ever, {input_note}, AND no installed unit runs "
                            f"{spec['module']}.py. This organ has never been scheduled: it is "
                            f"Class I (never lived), not Class II (died).")
        return R("BROKEN", input_n=input_n, output_rows=0, distinct_stamps=0, bursts=0,
                 detail=f"no output ever while {input_note}")

    # --- 3. cadence: bursts, not stamps ---------------------------------------
    try:
        stamps = [r[0] for r in con.execute(
            f'SELECT DISTINCT "{spec["out_clock"]}" FROM "{spec["out_table"]}"{w_all} '
            f'ORDER BY 1').fetchall()]
    except sqlite3.Error as e:
        return R("UNMEASURABLE", input_n=input_n, output_rows=rows, distinct_stamps=stamps_n,
                 newest=newest, detail=f"clock {spec['out_clock']} not enumerable: {e}")
    bursts, parsed_n = _bursts(stamps, gap)
    if stamps and parsed_n == 0:
        return R("UNMEASURABLE", input_n=input_n, output_rows=rows,
                 distinct_stamps=stamps_n, newest=newest,
                 detail=f"{rows:,} rows present, but NONE of the {len(stamps):,} distinct "
                        f"{spec['out_clock']} values parses as a timestamp (e.g. "
                        f"{stamps[0]!r}). Cadence is UNMEASURABLE — which is not the "
                        f"same answer as 'no cadence'.")

    try:
        recent = [r[0] for r in con.execute(
            f'SELECT DISTINCT "{spec["out_clock"]}" FROM "{spec["out_table"]}" '
            f'WHERE "{spec["out_clock"]}" > datetime(\'now\', ?){w_and} ORDER BY 1',
            (f"-{stale} days",)).fetchall()]
    except sqlite3.Error as e:
        return R("UNMEASURABLE", input_n=input_n, output_rows=rows, distinct_stamps=stamps_n,
                 bursts=bursts, newest=newest,
                 detail=f"clock {spec['out_clock']} not comparable to a date: {e}")
    recent_bursts, _ = _bursts(recent, gap)

    common = dict(input_n=input_n, output_rows=rows, distinct_stamps=stamps_n,
                  bursts=bursts, recent_bursts=recent_bursts, newest=newest)
    shape = (f"{rows:,} rows / {stamps_n:,} distinct stamps / {bursts} burst"
             f"{'' if bursts == 1 else 's'}")

    if bursts <= 1 and invoker is None and units_readable:
        return R("UNINVOKED", **common,
                 detail=f"{shape} — one burst, ever, AND no installed unit runs "
                        f"{spec['module']}.py. Class I: it was written to once and "
                        f"has never been scheduled. Newest {newest}.")
    if bursts <= 1:
        return R("BROKEN", **common,
                 detail=f"{shape} — every row lands in ONE burst (stamps within {gap}s of "
                        f"each other). That is a single write, not a cadence: exactly the "
                        f"shape a hand-run backfill leaves, whether it stamps the "
                        f"transaction or every row. Newest {newest}.")
    if recent_bursts == 0:
        if input_n == 0:
            # STARVED is not only a rows==0 state. An organ that produced output
            # for months and now has an EMPTY INPUT is starved, not broken, and
            # sending an operator to debug its logic wastes the trip. The first
            # draft of this file could only reach STARVED when the output table
            # was empty, which is the same "empty observation set" the reflector
            # found in beta's version wearing different clothes.
            return R("STARVED", **common,
                     detail=f"{shape}, nothing in {stale}d — and {spec['input_desc']} "
                            f"= 0. There is nothing for it to do. Newest {newest}.")
        return R("BROKEN", **common,
                 detail=f"{shape}, but NOTHING in {stale}d — newest {newest}, while "
                        f"{input_note}. It worked, on a real cadence, and then stopped.")
    if recent_bursts < 2:
        return R("BROKEN", **common,
                 detail=f"{shape}; only ONE burst in the last {stale}d (newest {newest}). "
                        f"One write inside the window is a pulse, not a heartbeat.")
    return R("HEALTHY", **common,
             detail=f"{shape}; {recent_bursts} bursts in the last {stale}d, newest {newest}")


def _thinker_declined(db_path: Optional[str]) -> bool:
    """ROUND 2 (R2c, m4): the brain's thinker record says the owner answered "e) not now" (memory_dreaming's reader,
    one definition). Any failure to read is False: a missing or torn answer stays loud."""
    try:
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import memory_dreaming as _md
        return bool(_md.thinker_declined_on(db_path))
    except Exception:  # noqa: BLE001
        return False


def _invariant(con: sqlite3.Connection, spec: Dict[str, Any], units: Dict[str, str],
               units_readable: bool, db_path: Optional[str] = None) -> Tuple[Reading, Dict[str, Any]]:
    invoker = resolve_invoker(spec, units) if units_readable else None
    try:
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import dream_ledger
        w = dream_ledger.dream_watch(con, float(spec.get("window_days", 2.0)))
    except Exception as e:  # noqa: BLE001 - a blind watch is UNMEASURABLE, never HEALTHY
        w = {"verdict": "UNMEASURABLE", "detail": "dream watch failed: %s: %s" % (type(e).__name__, e)}
    if w.get("verdict") == "STARVED" and _thinker_declined(db_path):
        # ROUND 2 (m4): nothing attempted because the owner said "e) not now" -- a choice, not a starved organ.
        w = dict(w, verdict="UNCONFIGURED",
                 detail="the owner answered e) not now: no thinker, so no dream is attempted (by choice; "
                        "`voidloom thinker set <backend>` switches it on). " + (w.get("detail") or ""))
    stale = int(float(spec.get("window_days", 2.0)))
    return Reading(organ=spec["organ"], verdict=w.get("verdict", "UNMEASURABLE"),
                   input_n=w.get("attempts"), output_rows=w.get("stored"), distinct_stamps=None, bursts=None,
                   recent_bursts=None, newest=None, stale_days=stale,
                   invoker=invoker, detail=(w.get("detail") or "") + _skip_note(con, invoker, stale)), w


def _run_time(ts: Any):
    """A sleep_loop_runs / memories timestamp as an aware UTC datetime, or None. A value with a 'T' and no offset
    is LOCAL (what the sleep loop wrote before round 1); a space-separated value is UTC (memories.created_at)."""
    import datetime as _dt
    if not ts:
        return None
    s = str(ts).strip()
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        return d.astimezone(_dt.timezone.utc)
    if "T" in s:
        return d.astimezone(_dt.timezone.utc)          # naive local -> UTC
    return d.replace(tzinfo=_dt.timezone.utc)


def _run_lock_held(lock_path: Optional[str]) -> Optional[bool]:
    """ROUND 2 (m6): is a process holding this brain's run-lock right now? True / False, or None when it cannot be
    told. Read-only: the file is never created, and a free lock is released at once (the probe holds it for the
    length of two system calls)."""
    if not lock_path or not os.path.exists(lock_path):
        return False
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        try:
            fd = os.open(lock_path, os.O_RDONLY)
        except OSError:
            return None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            except OSError:
                return None
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)
    try:                                         # Windows: the sleep loop's own primitive (filelock)
        from filelock import FileLock, Timeout
    except ImportError:
        return None
    lk = FileLock(lock_path, timeout=0)
    try:
        lk.acquire()
    except Timeout:
        return True
    except Exception:  # noqa: BLE001
        return None
    lk.release()
    return False


def _run_reading(con: sqlite3.Connection, spec: Dict[str, Any], units: Dict[str, str],
                 units_readable: bool, now=None, db_path: Optional[str] = None) -> Reading:
    """ROUND 1 (8): the sleep loop's run ledger, read directly. A finding, not a growth check."""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    organ, table = spec["organ"], spec["table"]
    wall, hours = int(spec["running_wall_seconds"]), int(spec["no_success_hours"])
    invoker = resolve_invoker(spec, units) if units_readable else None
    window_days = max(1, hours // 24)
    R = lambda v, detail, **kw: Reading(organ=organ, verdict=v, stale_days=window_days, invoker=invoker,
                                        detail=detail, **{**dict(input_n=None, output_rows=None,
                                                                 distinct_stamps=None, bursts=None,
                                                                 recent_bursts=None, newest=None), **kw})
    present = _table_present(con, table)
    if present is None:
        return R("UNMEASURABLE", f"sqlite_master unreadable; cannot tell whether {table} exists")
    if present is False:
        return R("BROKEN", f"{table} is ABSENT: the sleep loop has no run ledger on this brain")
    try:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        stage_col = "stage_reached" if "stage_reached" in cols else "NULL"
        rows = con.execute(f'SELECT id, status, started_at, completed_at, {stage_col}, error_log '
                           f'FROM "{table}" ORDER BY id').fetchall()
    except sqlite3.Error as e:
        return R("UNMEASURABLE", f"{table} is present but unreadable: {e}")

    backlog, backlog_note = None, ""
    try:
        wm = con.execute("SELECT value FROM metadata WHERE key = 'edge_watermark_id'").fetchone()
        if wm and wm[0] is not None:
            backlog = con.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0 AND id > ?",
                                  (int(wm[0]),)).fetchone()[0]
            backlog_note = f"; edge backlog {backlog:,} memories above watermark {int(wm[0])}"
    except (sqlite3.Error, ValueError, TypeError):
        backlog_note = "; edge backlog UNMEASURED"

    if not rows:
        if invoker is None and units_readable:
            return R("UNINVOKED", f"no sleep run ever, and no installed unit runs the sleep loop", output_rows=0)
        try:
            n_mem, first = con.execute("SELECT COUNT(*), MIN(created_at) FROM memories").fetchone()
            born = _run_time(first)
        except sqlite3.Error:
            n_mem, born = None, None
        if n_mem == 0:
            return R("STARVED", "no sleep run yet, and the brain holds no memories", output_rows=0)
        if born is None:
            return R("UNMEASURABLE", "no sleep run recorded, and the brain's age could not be read", output_rows=0)
        age_h = (now - born).total_seconds() / 3600
        if age_h < hours:
            return R("STARVED", f"no sleep run yet; the brain is {age_h:.0f} h old (its first night has not "
                                f"come){backlog_note}", output_rows=0)
        return R("STALLED", f"no sleep run recorded in a brain {age_h:.0f} h old (> {hours} h){backlog_note}",
                 output_rows=0)

    # ROUND 2 (m6): the newest 'running' row is LIVE while its process holds this brain's run-lock -- the owner's
    # unbudgeted catch-up, or tonight's run in progress. It is neither STALLED by age nor by "no completed run yet".
    running_rows = [r for r in rows if r[1] == "running"]
    lock_path = (os.path.realpath(db_path) + spec.get("lock_suffix", ".sleep-loop.lock")) if db_path else None
    if running_rows and _run_lock_held(lock_path):
        rid, _st, started, _done, stage, _err = running_rows[-1]
        t = _run_time(started)
        ok_rows = [r for r in rows if r[1] in ("success", "partial")]
        last = _run_time(ok_rows[-1][3] or ok_rows[-1][2]) if ok_rows else None
        age_note = f"{(now - t).total_seconds() / 60:.0f} min" if t else "an unparsed time"
        return R("HEALTHY", f"RUNNING: run {rid} is live (it holds this brain's run-lock), started {started}, "
                            f"{age_note} ago, stage {stage or 'unrecorded'}; last completed run "
                            f"{last.strftime('%Y-%m-%d %H:%M:%S') if last else 'never'}{backlog_note}",
                 input_n=backlog, output_rows=len(rows),
                 newest=last.strftime("%Y-%m-%d %H:%M:%S") if last else None)

    stale_running = []
    for rid, status, started, _done, stage, _err in rows:
        if status != "running":
            continue
        t = _run_time(started)
        if t is None:
            return R("UNMEASURABLE", f"run {rid} is 'running' but its started_at {started!r} does not parse",
                     output_rows=len(rows))
        age = (now - t).total_seconds()
        if age > wall:
            stale_running.append((rid, started, stage, age))
    ok = [r for r in rows if r[1] in ("success", "partial")]
    last_ok = ok[-1] if ok else None
    last_ok_t = _run_time(last_ok[3] or last_ok[2]) if last_ok else None
    newest = last_ok_t.strftime("%Y-%m-%d %H:%M:%S") if last_ok_t else None
    since_ok = [r for r in rows if last_ok is None or r[0] > last_ok[0]]
    cut = sum(1 for r in since_ok if r[1] in ("timeout", "error"))
    common = dict(input_n=backlog, output_rows=len(rows), newest=newest)

    if stale_running:
        rid, started, stage, age = stale_running[-1]
        return R("STALLED", f"{len(stale_running)} run(s) still 'running' past the unit's {wall} s wall -- newest "
                            f"run {rid}, started {started}, {age / 3600:.1f} h ago, stage reached "
                            f"{stage or 'unrecorded'}. A run that never closes its row was killed. Last completed "
                            f"run: {newest or 'never'}{backlog_note}", **common)
    if last_ok_t is None or (now - last_ok_t).total_seconds() > hours * 3600:
        return R("STALLED", f"no sleep run completed (success/partial) in the last {hours} h -- last completed "
                            f"{newest or 'never'}; {cut} run(s) since ended timeout/error{backlog_note}", **common)
    return R("HEALTHY", f"last run {last_ok[1]} at {newest} (run {last_ok[0]}); {cut} cut run(s) since"
                        f"{backlog_note}", **common)


def _precision_reading(con: sqlite3.Connection, spec: Dict[str, Any], units: Dict[str, str],
                       units_readable: bool) -> Reading:
    """ROUND 1 (10): the belief engine's precision canary -- current contradictions, and pending proposals,
    in the false class: the SAME organ template on both ends, or (2026-10-26) an end no person authored. The
    engine refuses the class; any row of it is BROKEN. The template-only version read HEALTHY with 584 false
    edges live, because it shared the filter's deny-list; authorship is an allow-list."""
    invoker = resolve_invoker(spec, units) if units_readable else None
    R = lambda v, detail, **kw: Reading(organ=spec["organ"], verdict=v, stale_days=1, invoker=invoker,
                                        detail=detail, **{**dict(input_n=None, output_rows=None,
                                                                 distinct_stamps=None, bursts=None,
                                                                 recent_bursts=None, newest=None), **kw})
    try:
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import belief_engine as _be
    except Exception as e:  # noqa: BLE001 - blind is UNMEASURABLE, never HEALTHY
        return R("UNMEASURABLE", f"belief_engine could not be imported: {type(e).__name__}: {e}")
    if not (_table_present(con, "memory_edges") and _table_present(con, "edge_proposals")):
        return R("STARVED", "the belief engine's substrate is absent on this brain (nothing authored)")
    try:
        in_class, _ = _be._candidate_edges(con)
        n_edges = con.execute("SELECT COUNT(*) FROM memory_edges WHERE predicate = 'contradicts' "
                              "AND valid_until IS NULL").fetchone()[0]
        authored = _be.authored_ids(con)
        pend = 0
        for s_id, t_id, ca, cb in con.execute(
                "SELECT p.source_id, p.target_id, substr(a.content, 1, 160), substr(b.content, 1, 160) "
                "FROM edge_proposals p "
                "JOIN memories a ON a.id = p.source_id JOIN memories b ON b.id = p.target_id "
                "WHERE p.proposal_kind = 'add_contradicts' AND p.status = 'pending'"):
            pend += _be.false_family(_be.template_family(ca), _be.template_family(cb),
                                     s_id, t_id, authored) is not None
    except sqlite3.Error as e:
        return R("UNMEASURABLE", f"contradiction reads failed: {e}")
    # A release is a person's choice to keep a link (review F3): the quarantine never
    # re-takes it, so it is reported as kept, not as a defect awaiting a scan.
    try:
        released = _be._ever_quarantined(con, "edge") if _be.quarantine_available(con) else set()
    except sqlite3.Error:
        released = set()
    kept = [r for r in in_class if r[0] in released]
    in_class = [r for r in in_class if r[0] not in released]
    kept_note = (f"; {len(kept):,} live false-class edge(s) were released by a person and are kept by choice"
                 if kept else "")
    fams: Dict[str, int] = {}
    for row in in_class:
        fams[row[3]] = fams.get(row[3], 0) + 1
    if in_class or pend:
        return R("BROKEN", f"{len(in_class):,} of {n_edges:,} current contradicts edges and {pend:,} pending "
                           f"proposals are in the false class ({fams}): the same organ template on both ends, "
                           f"or an end no person authored. `belief_cli.py quarantine` lists them; the nightly "
                           f"scan quarantines them{kept_note}", input_n=n_edges,
                 output_rows=len(in_class) + pend)
    rule = ("same organ template, or an end no person authored" if authored is not None
            else "same organ template (authorship unknowable: this brain has no embedding_provenance)")
    return R("HEALTHY", f"0 of {n_edges:,} current contradicts edges and 0 pending proposals are in the false "
                        f"class awaiting quarantine ({rule}){kept_note}", input_n=n_edges, output_rows=0)


def _run_vouch(con: sqlite3.Connection, spec: Dict[str, Any], organ: str, window_days: int, now=None) -> Optional[str]:
    """ROUND 2 (l4): the newest completed (success/partial) run inside `window_days` whose own stage for `organ`
    processed input -- stage_log[key][count] > 0 (migration 040), else the legacy counter column > 0. None if none."""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    key, count, legacy = spec["vouches"][organ]
    try:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{spec["table"]}")')}
        log_col = "stage_log" if "stage_log" in cols else "NULL"
        legacy_col = legacy if legacy in cols else "NULL"
        rows = con.execute(f'SELECT id, started_at, completed_at, {log_col}, {legacy_col} FROM "{spec["table"]}" '
                           f"WHERE status IN ('success', 'partial') ORDER BY id DESC LIMIT 50").fetchall()
    except sqlite3.Error:
        return None
    for rid, started, done, log, legacy_n in rows:
        t = _run_time(done or started)
        if t is None or (now - t).total_seconds() > window_days * 86400:
            continue
        n = None
        if log:
            try:
                n = int((json.loads(log).get(key) or {}).get(count) or 0)
            except (ValueError, TypeError, AttributeError):
                n = None
        if n is None and legacy_n is not None:
            try:
                n = int(legacy_n)
            except (TypeError, ValueError):
                n = None
        if n and n > 0:
            return f"run {rid} completed {t.strftime('%Y-%m-%d %H:%M:%S')} UTC, {key} {count} {n:,}"
    return None


def scan(db_path: str, stale_override: Optional[int] = None) -> Tuple[List[Reading], Dict[str, Any]]:
    if not os.path.exists(db_path):
        return [], {"error": f"database not found: {db_path}", "readable": False}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    vec_loaded = False
    try:
        import sqlite_vec                       # a vec0 output table is unreadable without it
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        vec_loaded = True
    except Exception:
        pass
    units, units_readable = installed_units()
    inv_meta: Dict[str, Any] = {}
    try:
        rs = [_one(con, s, units, units_readable, stale_override) for s in WATCH]
        # ROUND 1 (8): the run ledger, and its veto over the organs it feeds.
        for spec in RUN_WATCH:
            run = _run_reading(con, spec, units, units_readable, db_path=db_path)
            rs.append(run)
            if run.verdict == "STALLED":
                for r in rs:
                    if r.organ in spec.get("feeds", ()) and r.verdict == "HEALTHY":
                        r.verdict = "STALLED"
                        r.detail = (f"{r.detail} -- BUT the run that writes it is STALLED ({spec['organ']}): "
                                    f"growth is not completion")
            elif run.verdict == "HEALTHY":
                # ROUND 2 (l4): the reverse of the veto -- the run ledger VOUCHES for one recent burst.
                for r in rs:
                    if r.organ in spec.get("vouches", {}) and r.verdict == "BROKEN" and r.recent_bursts == 1:
                        vouch = _run_vouch(con, spec, r.organ, r.stale_days)
                        if vouch:
                            shape = r.detail.split(";")[0].split(" — ")[0]
                            r.verdict = "HEALTHY"
                            r.detail = (f"{shape}; ONE burst in the last {r.stale_days}d (newest {r.newest}), and "
                                        f"the run ledger vouches for it: {vouch}. A completed run wrote it, not a "
                                        f"hand-run backfill; the cadence check resumes with the next night")
        for spec in PRECISION_WATCH:
            rs.append(_precision_reading(con, spec, units, units_readable))
        for spec in INVARIANT_WATCH:
            r, w = _invariant(con, spec, units, units_readable, db_path=db_path)
            rs.append(r)
            inv_meta[spec["organ"]] = {k: w.get(k) for k in ("attempts", "meaningful", "stored",
                                                             "meaningful_without_id", "llm_failures",
                                                             "met_warning", "met_detail", "met_events")}
    finally:
        con.close()
    # POPULATION, not just coverage (alpha-3: coverage is not population).
    wl_units = sorted(u for u in units if u.startswith("wizard-library-"))
    # The population line must use the SAME matcher as the invoker oracle, or it
    # reports that 19 of 19 units run nothing watched while the table above names
    # the unit that runs one. The first draft used only `module`, and the two
    # halves of this file disagreed with each other on screen.
    watched_modules = set()
    for spec in list(WATCH) + list(INVARIANT_WATCH) + list(RUN_WATCH) + list(PRECISION_WATCH):
        watched_modules.add(spec["module"])
        watched_modules.update(spec.get("invoker_modules", ()))
    unwatched = sorted(
        u for u in wl_units
        if not any(f"{m}.py" in units.get(u, "") for m in watched_modules))
    meta = {
        "readable": True,
        "sqlite_vec_loaded": vec_loaded,
        "units_readable": units_readable,
        "installed_wizard_units": len(wl_units) if units_readable else None,
        "watched_organs": len(WATCH) + len(INVARIANT_WATCH) + len(RUN_WATCH) + len(PRECISION_WATCH),
        "invariants": inv_meta,
        "installed_units_with_no_watched_organ": unwatched if units_readable else None,
    }
    return rs, meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Report which organs run but produce nothing.")
    ap.add_argument("db", nargs="?",
                    default=str(Path(__file__).resolve().parent.parent / "claude_memory.db"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stale-days", type=int, default=None,
                    help="override EVERY organ's own window (default: each organ's own)")
    a = ap.parse_args()
    rs, meta = scan(a.db, a.stale_days)

    # ROUND 2 (l5): with --json, stdout carries the JSON object ONLY (json.load on it works); every human line
    # below -- the WARNs and the summary -- goes to stderr.
    human = sys.stderr if a.json else sys.stdout
    if a.json:
        print(json.dumps({"readings": [asdict(r) for r in rs], "meta": meta}, indent=2))
    else:
        if not rs:
            print(f"  UNMEASURABLE: {meta.get('error')}")
        else:
            print(f"  {'organ':<42}{'verdict':<14}{'in':>9}{'out':>9}{'bursts':>8}{'win':>5}")
            for r in rs:
                fmt = lambda v: "     n/a" if v is None else f"{v:>9,}"
                print(f"  {r.organ:<42}{r.verdict:<14}{fmt(r.input_n)}{fmt(r.output_rows)}"
                      f"{('  n/a' if r.bursts is None else f'{r.bursts:>8}')}{r.stale_days:>5}")
                if r.verdict != "HEALTHY" or " SKIPPED run" in r.detail:
                    print(f"      └─ {r.detail}")
                    if r.verdict in ("BROKEN", "UNINVOKED", "STALLED"):
                        print(f"      └─ invoker: {r.invoker or 'NONE FOUND on this machine'}")

    for organ, im in (meta.get("invariants") or {}).items():
        if im.get("met_warning"):
            print(f"  WARN {organ}: {im.get('met_detail')}", file=human)
    counts = {v: sum(1 for r in rs if r.verdict == v) for v in VERDICTS}
    n_watch = len(WATCH) + len(INVARIANT_WATCH) + len(RUN_WATCH) + len(PRECISION_WATCH)
    n_unmeas = counts["UNMEASURABLE"] + (0 if rs else n_watch)
    print(f"\n  {counts['BROKEN']} BROKEN, {counts['STALLED']} STALLED, {counts['UNINVOKED']} UNINVOKED, "
          f"{counts['STARVED']} STARVED, {counts['UNCONFIGURED']} UNCONFIGURED, {n_unmeas} UNMEASURABLE "
          f"of {n_watch} watched", file=human)
    if meta.get("units_readable"):
        print(f"  population: {meta['installed_wizard_units']} wizard-library units installed; "
              f"{len(meta['installed_units_with_no_watched_organ'])} run nothing this sensor watches", file=human)
    else:
        print("  population: UNMEASURABLE — the systemd unit directory could not be read", file=human)

    # EXIT CODES (see the module docstring). 2 means the sensor could not see,
    # which is NEVER the same answer as "nothing is wrong"; 3 means it saw a BROKEN
    # organ. The unit's SuccessExitStatus admits 0 and 3 only, so a blind sensor
    # (2) and a crash (1, Python's own) both surface as genuine unit failures.
    if n_unmeas:
        return EXIT_BLIND
    return EXIT_BROKEN_FOUND if (counts["BROKEN"] or counts["STALLED"]) else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())

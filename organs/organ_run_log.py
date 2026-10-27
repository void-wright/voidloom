#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""organ_run_log.py — G2a, THE RUN LEDGER. Ruler: VL-LIFECYCLE-v2.0.

WHAT IT IS FOR (KAEL-EM's selection rule, which is better than a number):
    instrument the organs whose SILENCE Drew would attribute to himself.
That is the failure that convened the council -- he saw "1 pending", found
nothing, and concluded he had missed something.

R2 -- THIS MODULE OBSERVES AND NEVER NOTIFIES. It imports no banner, no approvals
module, no notifier; it writes no approval unit and touches no approval table.
DESSA: the stop condition belongs on the notifier, not the observer -- a ledger
that can alarm can be switched off, and then nothing observes. Assertions notify;
the ledger only records. That separation is grep-provable and is asserted by
tests/test_organ_run_log.py::test_r2_observer_never_notifies.

R1 -- KEYED ON THE DECLARED SET, JOINED AGAINST OBSERVED RUNS. Measured
2026-09-02: 19 wizard-library timer FILES exist; `systemctl --user list-timers`
lists 18. The nineteenth, wizard-library-arcane-manifest.timer, is `disabled` and
`inactive` and `journalctl` returns "-- No entries --": a fully written quarterly
organ that has never fired once. A ledger keyed on runs OBSERVED cannot see it,
because absence of rows is indistinguishable from "hasn't reached cadence yet."
So the declared set leads and observation joins onto it.

R3 -- CADENCE IS PER UNIT, FROM ITS OWN OnCalendar, MEASURED NOT GUESSED.
wizard-library-counterfactual-revisit.timer emulates quarterly with FOUR
OnCalendar lines; a cadence derived from the modal spec, the Description string
or a registry default gets it wrong, and 1.5x a wrongly-derived monthly cadence
alarms on a healthy timer. Here every unit's OnCalendar lines are read verbatim
and the cadence is MEASURED by asking systemd for successive occurrences.

AND THE FAILURE AN INTERVAL WATCHDOG CANNOT SEE. `Persistent=true` on all 18
loaded timers converts a MISSED WINDOW into a LATE RUN. Measured: a nightly backup unit
(a 23:45 unit) and anti-recency (an 08:00 unit) both last ran 08:41:06 -- a boot
catch-up. Elapsed-since-last-run sits inside the window and reports health. So a
run is scored against THE SCHEDULED OCCURRENCE IT SATISFIES, and its lateness is
recorded per row.

R17 -- no bare swallow anywhere. When the systemd probe is unavailable the ledger
says so LOUDLY in a `probe_error` field rather than reporting health it did not
measure.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["DeclaredUnit", "LedgerRow", "ProbeError",
           "discover_declared_units", "observe", "ledger_report", "render_report"]

# R5/C6 (class sweep, widened definition). Same shape as decision_units.py:69.
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "claude_memory.db")
DEFAULT_UNIT_DIR = os.path.join(str(Path.home()), ".config", "systemd", "user")
DEFAULT_GLOB = "wizard-library-*.timer"

#: Per-cadence-class lateness bounds, in seconds. DESSA: "1.5x is a daily/weekly
#: bound, not a universal one" -- 1.5x a quarter is a watchdog that alarms at
#: ~4.5 months, after two consecutive failures. For long cadences the useful
#: bound is "the scheduled instant passed and no run appeared within a grace
#: window", checkable the next morning. One number for both classes is the same
#: error as one usage window for all organs.
CADENCE_CLASSES: Tuple[Tuple[str, int, int], ...] = (
    #  name        max gap (s)      lateness bound (s)
    ("sub_hourly",   60 * 60,        90 * 60),        # 15/30-min units
    ("hourly",       6 * 3600,       3 * 3600),
    ("daily",        36 * 3600,      12 * 3600),
    ("weekly",       10 * 86400,     36 * 3600),
    ("monthly",      45 * 86400,     3 * 86400),
    ("quarterly",    200 * 86400,    7 * 86400),
    ("annual",       400 * 86400,    14 * 86400),
)


class ProbeError(RuntimeError):
    """The systemd probe could not answer. Raised, never swallowed: a ledger
    that reports health it did not measure is worse than one that reports
    nothing."""


@dataclass
class DeclaredUnit:
    unit_name: str
    unit_file: str
    service_name: str
    oncalendar: List[str] = field(default_factory=list)
    persistent: bool = False
    unit_file_state: Optional[str] = None
    active_state: Optional[str] = None
    cadence_class: str = "unknown"
    cadence_seconds: Optional[int] = None
    last_trigger: Optional[datetime] = None
    next_elapse: Optional[datetime] = None


@dataclass
class LedgerRow:
    unit_name: str
    cadence_class: str
    cadence_seconds: Optional[int]
    oncalendar: List[str]
    persistent: bool
    unit_file_state: Optional[str]
    active_state: Optional[str]
    runs: int
    last_run_at: Optional[str]
    last_run_result: Optional[str]
    scheduled_for: Optional[str]
    lateness_seconds: Optional[int]
    within_bound: Optional[bool]
    never_run: bool
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# systemd probes -- read-only, and loud when they cannot answer
# ---------------------------------------------------------------------------

def _run(cmd: List[str], timeout: int = 20) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        raise ProbeError(f"{cmd[0]} is not on PATH; the ledger cannot observe "
                         f"systemd and will not pretend it did")
    proc = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise ProbeError(f"{' '.join(cmd)} exited {proc.returncode}: "
                         f"{proc.stderr.strip()[:300]}")
    return proc.stdout


_SYSTEMD_TS = "%a %Y-%m-%d %H:%M:%S"


def _parse_systemd_ts(text: Optional[str]) -> Optional[datetime]:
    """systemd stamps look like 'Wed 2026-09-02 08:41:06 EDT'. The trailing zone
    abbreviation is dropped and the instant is read as local, which is what every
    other consumer of these stamps on this machine does."""
    if not text or not text.strip():
        return None
    parts = text.strip().split()
    if len(parts) < 3:
        return None
    try:
        return datetime.strptime(" ".join(parts[:3]), _SYSTEMD_TS)
    except ValueError:
        return None


# Exit statuses that a unit uses to say "nothing ran, nothing failed". systemd
# reports every non-zero exit as Result=exit-code; only the status tells them
# apart. 75 = EX_TEMPFAIL, returned by dream_pipeline.py when the shared dream
# lock is held (a same-second Persistent catch-up of the daytime and nightly
# units, 2026-09-18 22:28:36 -- #20269). A ledger that files that as a failure
# hides a race behind a red row.
SKIP_STATUSES = {"75": "skipped-lock"}


def classify_result(result: Optional[str], exec_main_status: Optional[str]) -> Optional[str]:
    """systemd's Result, refined by the exit status where the unit declares a skip."""
    if result == "exit-code" and exec_main_status in SKIP_STATUSES:
        return SKIP_STATUSES[exec_main_status]
    return result


# Every organ entry point that can find its lock held exits 75 on that skip (Round 3): sleep_loop.py --run,
# claude_memory.py sleep (the sleep-loop unit), dream_pipeline.py run (both dream units), memory_dreaming.py run.
SKIP_NOTE = ("a skip: the organ found its brain's lock held (exit 75), so nothing ran and nothing failed; "
             "the next scheduled run is the retry")


def skipped_runs(conn: sqlite3.Connection, days: float = 7.0,
                 services: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The ledger's SKIPPED runs in the last `days`, newest first -- ONE definition of a skip (SKIP_STATUSES) for the
    report, the doctor and the organ-yield sentinel. Raises sqlite3.Error when the ledger cannot be read; the caller
    says so rather than reporting "no skips"."""
    kinds = sorted(set(SKIP_STATUSES.values()))
    sql = ("SELECT unit_name, service_name, run_started_at, result FROM organ_run_log WHERE result IN (%s) "
           "AND run_started_at >= datetime('now', ?)" % ",".join("?" * len(kinds)))
    args: List[Any] = list(kinds) + ["-%s days" % float(days)]
    if services:
        sql += " AND service_name IN (%s)" % ",".join("?" * len(services))
        args += list(services)
    rows = conn.execute(sql + " ORDER BY run_started_at DESC", args).fetchall()
    return [{"unit_name": r[0], "service_name": r[1], "run_started_at": r[2], "result": r[3]} for r in rows]


def _show(unit: str, props: List[str]) -> Dict[str, List[str]]:
    out = _run(["systemctl", "--user", "show", unit] + [f"-p{p}" for p in props])
    d: Dict[str, List[str]] = {}
    for line in out.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        d.setdefault(k, []).append(v)
    return d


def _calendar_occurrences(spec: str, base: datetime, n: int) -> List[datetime]:
    """Successive occurrences of ONE OnCalendar expression at or after `base`.

    Delegated to `systemd-analyze calendar`, which is the same evaluator systemd
    itself uses -- so a spec this machine accepts is a spec the ledger reads
    identically. Reimplementing the grammar would be a second, divergent parser.
    """
    out = _run(["systemd-analyze", "calendar",
                f"--base-time={base.strftime('%Y-%m-%d %H:%M:%S')}",
                f"--iterations={n}", spec])
    stamps: List[datetime] = []
    for line in out.splitlines():
        m = re.search(r"(?:Next elapse|Iteration #\d+):\s+(.+)$", line.strip())
        if m:
            ts = _parse_systemd_ts(m.group(1))
            if ts is not None:
                stamps.append(ts)
    return stamps


def _measure_cadence(specs: List[str], now: datetime) -> Tuple[str, Optional[int]]:
    """MEASURE the cadence from the union of ALL the unit's OnCalendar lines.

    Never from the modal spec, the Description, or a registry default -- each of
    those gets counterfactual-revisit (four lines emulating quarterly) wrong, and
    that unit is healthy. The union is sorted and the MEDIAN gap taken, so one
    irregular boundary (Dec->Jan) cannot move the class.
    """
    occ: List[datetime] = []
    for s in specs:
        occ.extend(_calendar_occurrences(s, now, 4))
    occ = sorted(set(occ))
    if len(occ) < 2:
        return "unknown", None
    gaps = sorted((occ[i + 1] - occ[i]).total_seconds() for i in range(len(occ) - 1))
    median = int(gaps[len(gaps) // 2])
    for name, max_gap, _bound in CADENCE_CLASSES:
        if median <= max_gap:
            return name, median
    return "annual", median


def _previous_occurrence(specs: List[str], now: datetime,
                         cadence_seconds: Optional[int]) -> Optional[datetime]:
    """The most recent SCHEDULED instant strictly before `now`.

    This is what a run is scored against -- not the interval since the previous
    run. `Persistent=true` turns a missed window into a late run, so an interval
    comparison sees a run inside the window and reports health on precisely the
    most likely WSL2 failure: the machine off during the scheduled window.
    """
    window = timedelta(seconds=(cadence_seconds or 86400) * 3)
    base = now - window
    best: Optional[datetime] = None
    for s in specs:
        for ts in _calendar_occurrences(s, base, 40):
            if ts < now and (best is None or ts > best):
                best = ts
    return best


# ---------------------------------------------------------------------------
# The DECLARED set -- R1
# ---------------------------------------------------------------------------

def discover_declared_units(unit_dir: str = DEFAULT_UNIT_DIR,
                            pattern: str = DEFAULT_GLOB,
                            *, now: Optional[datetime] = None,
                            measure_cadence: bool = True) -> List[DeclaredUnit]:
    """Every timer DECLARED on disk, whether or not it has ever run.

    The unit FILES are the declared set. `systemctl --user list-timers` is not:
    it lists 18 of the 19, omitting the disabled one, and that omission is the
    exact blind spot this ledger exists to close.
    """
    now = now or datetime.now()
    files = sorted(Path(unit_dir).glob(pattern))
    if not files:
        raise ProbeError(f"no unit files matched {pattern} in {unit_dir}; the "
                         f"declared set cannot be empty by accident")
    units: List[DeclaredUnit] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        oncal = [m.group(1).strip() for m in
                 re.finditer(r"^\s*OnCalendar\s*=\s*(.+?)\s*$", text, re.M)]
        persistent = bool(re.search(r"^\s*Persistent\s*=\s*(true|yes|1)\s*$",
                                    text, re.M | re.I))
        u = DeclaredUnit(unit_name=f.name, unit_file=str(f),
                         service_name=f.name[:-len(".timer")] + ".service",
                         oncalendar=oncal, persistent=persistent)
        try:
            props = _show(f.name, ["UnitFileState", "ActiveState",
                                   "LastTriggerUSec", "NextElapseUSecRealtime"])
            u.unit_file_state = (props.get("UnitFileState") or [None])[0]
            u.active_state = (props.get("ActiveState") or [None])[0]
            u.last_trigger = _parse_systemd_ts((props.get("LastTriggerUSec") or [""])[0])
            u.next_elapse = _parse_systemd_ts((props.get("NextElapseUSecRealtime") or [""])[0])
        except (ProbeError, subprocess.SubprocessError, OSError) as e:
            # LOUD: the unit stays in the declared set with its state unknown,
            # rather than silently vanishing or being reported healthy.
            u.unit_file_state = f"probe_error: {type(e).__name__}"
        if measure_cadence and oncal:
            try:
                u.cadence_class, u.cadence_seconds = _measure_cadence(oncal, now)
            except (ProbeError, subprocess.SubprocessError, OSError) as e:
                u.cadence_class = f"probe_error: {type(e).__name__}"
        units.append(u)
    return units


# ---------------------------------------------------------------------------
# Observation -- writes ONLY organ_declared_units / organ_run_log
# ---------------------------------------------------------------------------

_ALLOWED_TABLES = ("organ_declared_units", "organ_run_log")


def _utc_stamp(dt: datetime) -> str:
    """Render a systemd-derived NAIVE LOCAL datetime in this table's clock.

    Every other timestamp in organ_run_log / organ_declared_units comes from
    SQLite's `datetime('now')`, which is UTC and space-separated. systemd reports
    LOCAL time, so writing `dt.isoformat()` straight through put two clocks AND
    two formats in one table: run_started_at read `2026-09-07T15:00:32` while
    observed_at read `2026-09-07 19:07:39` -- the same moment, four hours apart on
    paper, and across a midnight a whole day apart. Any consumer asking "was this
    run observed after it started" would have been wrong every time.

    `lateness_seconds` is unaffected: it is computed from the timedelta BEFORE
    formatting, from two operands that were both local. Only the stored rendering
    changes.

    One clock per column, and per TABLE:
        SELECT COUNT(DISTINCT (col LIKE '%T%')) FROM <table>   -- must be 1
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def observe(db: Optional[str] = None, *, unit_dir: str = DEFAULT_UNIT_DIR,
            pattern: str = DEFAULT_GLOB,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Record the declared set and any newly-observed run. Idempotent.

    R2: the only tables this function writes are organ_declared_units and
    organ_run_log. It sends no notification, touches no approval table and
    returns no alarm -- it returns COUNTS.
    """
    now = now or datetime.now()
    db_path = db or os.environ.get("WIZARD_LIBRARY_DB") or DEFAULT_DB_PATH
    units = discover_declared_units(unit_dir, pattern, now=now)
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        missing = [t for t in _ALLOWED_TABLES if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
        if missing:
            raise ProbeError(f"migration 030 not applied: {missing} absent")
        declared = 0
        runs_added = 0
        for u in units:
            conn.execute(
                "INSERT INTO organ_declared_units "
                "(unit_name, unit_file, service_name, oncalendar_json, cadence_class, "
                " cadence_seconds, persistent, unit_file_state, active_state, last_observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,datetime('now')) "
                "ON CONFLICT(unit_name) DO UPDATE SET "
                "  unit_file=excluded.unit_file, oncalendar_json=excluded.oncalendar_json, "
                "  cadence_class=excluded.cadence_class, cadence_seconds=excluded.cadence_seconds, "
                "  persistent=excluded.persistent, unit_file_state=excluded.unit_file_state, "
                "  active_state=excluded.active_state, last_observed_at=datetime('now')",
                (u.unit_name, u.unit_file, u.service_name, json.dumps(u.oncalendar),
                 u.cadence_class, u.cadence_seconds, int(u.persistent),
                 u.unit_file_state, u.active_state))
            declared += 1
            if u.last_trigger is None:
                continue        # never run -- and R1 keeps it visible anyway
            sched = None
            lateness = None
            if u.oncalendar:
                try:
                    prev = _previous_occurrence(u.oncalendar, u.last_trigger + timedelta(seconds=1),
                                                u.cadence_seconds)
                    if prev is not None:
                        sched = _utc_stamp(prev)
                        lateness = int((u.last_trigger - prev).total_seconds())
                except (ProbeError, subprocess.SubprocessError, OSError) as e:
                    sched = None
                    print(f"[organ_run_log] schedule probe failed for {u.unit_name}: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
            result = None
            try:
                shown = _show(u.service_name, ["Result", "ExecMainStatus"])
                result = classify_result((shown.get("Result") or [None])[0],
                                         (shown.get("ExecMainStatus") or [None])[0])
            except (ProbeError, subprocess.SubprocessError, OSError):
                result = None
            cur = conn.execute(
                "INSERT OR IGNORE INTO organ_run_log "
                "(unit_name, service_name, run_started_at, result, scheduled_for, "
                " lateness_seconds, observation_source) VALUES (?,?,?,?,?,?, 'systemctl-show')",
                (u.unit_name, u.service_name, _utc_stamp(u.last_trigger),
                 result, sched, lateness))
            runs_added += cur.rowcount if cur.rowcount > 0 else 0
        conn.commit()
        return {"status": "ok", "declared_units": declared, "runs_recorded": runs_added,
                "observed_at": _utc_stamp(now)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The report -- declared LEFT JOIN observed
# ---------------------------------------------------------------------------

def _bound_for(cadence_class: str) -> Optional[int]:
    for name, _gap, bound in CADENCE_CLASSES:
        if name == cadence_class:
            return bound
    return None


def ledger_report(db: Optional[str] = None) -> List[LedgerRow]:
    """One row per DECLARED unit, with its observed runs joined on.

    A unit that has never run appears with runs=0 -- that is the whole point.
    """
    db_path = db or os.environ.get("WIZARD_LIBRARY_DB") or DEFAULT_DB_PATH
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        rows: List[LedgerRow] = []
        for d in conn.execute("SELECT * FROM organ_declared_units ORDER BY unit_name"):
            runs = conn.execute(
                "SELECT COUNT(*) FROM organ_run_log WHERE unit_name=?",
                (d["unit_name"],)).fetchone()[0]
            last = conn.execute(
                "SELECT * FROM organ_run_log WHERE unit_name=? "
                " ORDER BY run_started_at DESC LIMIT 1", (d["unit_name"],)).fetchone()
            notes: List[str] = []
            if runs == 0:
                notes.append("NEVER RUN — visible only because the ledger is keyed "
                             "on the declared set, not on observed runs (R1)")
            if str(d["unit_file_state"] or "").startswith("disabled"):
                notes.append("timer is DISABLED: it is declared but not scheduled")
            if len(json.loads(d["oncalendar_json"] or "[]")) > 1:
                notes.append(f"{len(json.loads(d['oncalendar_json']))} OnCalendar lines "
                             f"emulate one cadence — a modal or default-derived cadence "
                             f"would misclassify this unit (R3)")
            if last is not None and last["result"] in SKIP_STATUSES.values():
                notes.append("SKIPPED: its last run (%s) was %s" % (last["run_started_at"], SKIP_NOTE))
            n_skips = conn.execute(
                "SELECT COUNT(*) FROM organ_run_log WHERE unit_name=? AND result IN (%s)"
                % ",".join("?" * len(set(SKIP_STATUSES.values()))),
                [d["unit_name"]] + sorted(set(SKIP_STATUSES.values()))).fetchone()[0]
            if n_skips:
                notes.append("%d of its %d observed runs %s (never counted as a success or a failure)"
                             % (n_skips, runs, "was a skip" if n_skips == 1 else "were skips"))
            lateness = last["lateness_seconds"] if last else None
            bound = _bound_for(d["cadence_class"])
            within = None if (lateness is None or bound is None) else (lateness <= bound)
            if within is False:
                notes.append(f"LATE: ran {lateness}s after its scheduled occurrence "
                             f"(bound for {d['cadence_class']} is {bound}s). "
                             f"Persistent={bool(d['persistent'])} — an interval "
                             f"watchdog would have called this healthy")
            rows.append(LedgerRow(
                unit_name=d["unit_name"], cadence_class=d["cadence_class"],
                cadence_seconds=d["cadence_seconds"],
                oncalendar=json.loads(d["oncalendar_json"] or "[]"),
                persistent=bool(d["persistent"]),
                unit_file_state=d["unit_file_state"], active_state=d["active_state"],
                runs=runs,
                last_run_at=last["run_started_at"] if last else None,
                last_run_result=last["result"] if last else None,
                scheduled_for=last["scheduled_for"] if last else None,
                lateness_seconds=lateness, within_bound=within,
                never_run=(runs == 0), notes=notes))
        return rows
    finally:
        conn.close()


def render_report(rows: List[LedgerRow]) -> str:
    out = [f"ORGAN RUN LEDGER — {len(rows)} DECLARED units "
           f"({sum(1 for r in rows if r.never_run)} never run)",
           "  `runs` counts DISTINCT trigger stamps THIS LEDGER HAS OBSERVED, not "
           "lifetime runs: systemd exposes only the most recent trigger, so history "
           "accrues forward from the first observation. runs=0 is the exception and "
           "IS lifetime — it means systemd holds no trigger stamp at all.",
           f"{'unit':<48}{'cadence':<12}{'runs':>5}  {'last run':<20}{'late(s)':>9}"]
    out.append("-" * 100)
    for r in rows:
        out.append(f"{r.unit_name:<48}{r.cadence_class:<12}{r.runs:>5}  "
                   f"{(r.last_run_at or '—'):<20}"
                   f"{('' if r.lateness_seconds is None else r.lateness_seconds):>9}")
        for n in r.notes:
            out.append(f"        · {n}")
    return "\n".join(out)


def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Voidloom G2a run ledger (observes; never notifies)")
    ap.add_argument("verb", choices=["declared", "observe", "report"])
    ap.add_argument("--db", default=None)
    ap.add_argument("--unit-dir", default=DEFAULT_UNIT_DIR)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.verb == "declared":
        us = discover_declared_units(a.unit_dir)
        print(json.dumps([u.__dict__ for u in us], indent=2, default=str) if a.json
              else "\n".join(f"  {u.unit_name:<48}{u.cadence_class:<12}"
                             f"{u.unit_file_state or '?':<10}"
                             f"last={u.last_trigger or 'NEVER'}" for u in us))
        print(f"\n  {len(us)} DECLARED units")
        return 0
    if a.verb == "observe":
        print(json.dumps(observe(a.db, unit_dir=a.unit_dir), indent=2))
        return 0
    rows = ledger_report(a.db)
    print(json.dumps([r.__dict__ for r in rows], indent=2, default=str) if a.json
          else render_report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

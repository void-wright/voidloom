#!/usr/bin/env python3
"""affect_probe_scan.py -- the SEPARATE driver that activates the L5-M2 Affect
Lighthouse trial.

    "Measure the calm to defend the calm -- never to disturb it."
                               -- the L5-M2 council ruling (memory #5836)

WHAT THIS IS
------------
The policed module ``affect_lighthouse.py`` is fully built + tested but WIRED
NOWHERE. This driver is the missing seam: a forward-only, probe-gated batch
scanner that walks NEW assistant text out of the session transcripts and hands
each message to ``sycophancy_smoke_check``. The module -- not this driver --
decides whether to write a quarantine row, and writes it ONLY on a trip and
ONLY when the probe is enabled.

MULTI-TRANSCRIPT SCANNING (the 2026-06-22 fix)
----------------------------------------------
A Claude Code project does NOT have a single growing transcript -- it has MANY
``.jsonl`` files under the project dir, one (or more) per session. New sessions
create new files; an old session's file stops growing. A scanner that picks the
single "newest-by-mtime" transcript therefore drains only ONE file per run and
silently misses every other session's new assistant text (the daily-timer bug:
``scanned: 0`` every run while real weekend sessions wrote whole new transcripts
that were never the mtime-winner during their scan window).

So in the DEFAULT mode (no ``--transcript``) this driver enumerates ALL
``.jsonl`` files in the project dir and scans EACH ONE forward-only from its OWN
per-path watermark. The watermark state file was already keyed by transcript
path; this driver now actually iterates every path. A single ``--transcript``
path still scans exactly that one file (explicit single-file mode, unchanged).

WHAT THIS DRIVER MAY AND MAY NOT DO (the fences this file is built to honour)
----------------------------------------------------------------------------
* It imports from the brain ONLY the ``affect_lighthouse`` PUBLIC API plus
  stdlib. It NEVER imports ``claude_memory``, ``embedding_upgrade``,
  ``hybrid_retrieval``, or anything on the salience / memory-write path. (An
  import-audit test in the proof set parses this file's imports and FAILS if any
  forbidden symbol appears.)
* The ONLY DB write the whole flow performs is the module's own
  ``write_quarantine`` into the isolated ``affect_quarantine_log`` sink -- the
  one-directional data diode. This driver opens a connection and hands it to the
  module; the module owns the write. This driver writes nothing to the brain on
  its own.
* It honours ``probe_enabled()``. Probe OFF (no env) -> zero quarantine rows,
  exit 0, nothing scanned-for-trips. The 28-day auto-expiry belongs to the
  module; this driver does not reimplement it -- it simply asks the module.
* It is FORWARD-ONLY. A watermark state file keyed by transcript path records
  the last-scanned line index PER TRANSCRIPT. On first sight of a transcript
  with a freshly-enabled probe it starts that transcript's watermark at its
  CURRENT end (NO backfill of history) unless ``--from-start`` is passed. It
  never re-scans a line of any transcript.

USAGE
-----
  affect_probe_scan.py                      # scan NEW assistant text across ALL
                                            # transcripts in the project dir
  affect_probe_scan.py --transcript PATH    # scan a single specific transcript
  affect_probe_scan.py --project-dir DIR    # scan ALL .jsonl under DIR
  affect_probe_scan.py --from-start         # (re)scan every transcript from line 0
  affect_probe_scan.py --digest             # print the Drew-review digest
  affect_probe_scan.py --calm-demo          # prove the calm-restorer fires on stuck telemetry
  affect_probe_scan.py --db PATH            # override the brain DB (default: env / canonical)

All side effects are confined to: (a) the module's quarantine write, gated by the
probe; (b) the watermark state file. Nothing else is touched.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# IMPORTS -- the allowed surface ONLY. stdlib + the affect_lighthouse public   #
# API. This import block is a load-bearing fence: the import-audit proof FAILS  #
# if anything here reaches claude_memory / embedding_upgrade / hybrid_retrieval #
# or any salience / memory-write symbol.                                       #
# --------------------------------------------------------------------------- #
import os
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime, date

# The brain's policed module -- PUBLIC API only.
from affect_lighthouse import (
    probe_enabled,
    sycophancy_smoke_check,
    calm_restorer,
    ensure_quarantine_table,
    quarantine_digest,
    FailureTelemetry,
)


# --------------------------------------------------------------------------- #
# Defaults (resolved env-first, then the canonical relative-self location).    #
# --------------------------------------------------------------------------- #
# R4/P5. `_DEFAULT_DB` is computed AT IMPORT, so it froze the environment
# as it stood when the module was first imported -- and as an argparse
# default it then pre-empted any later resolution. The constant stays (it
# is the last resort); `_resolve_db_arg` below is what the verbs call.
_DEFAULT_DB = os.environ.get("WIZARD_LIBRARY_DB") or str(
    Path(__file__).resolve().parent.parent / "claude_memory.db"
)


def _resolve_db_arg(value):
    """R4/P5. Resolve the brain path AFTER argparse, at RUN time.

    Order: explicit --db > $WIZARD_LIBRARY_DB > $CLAUDE_MEMORY_DB > the module
    constant (itself $WIZARD_LIBRARY_DB or relative-self).

    R5/C6 (R4-F7). THE PREVIOUS SENTENCE HERE CLAIMED THIS "mirrors
    wizard_paths.get_db_path(), the house resolver, so a fourth precedence order
    is not invented here." It does not mirror it, and a fifth was invented. The
    house resolver is override > $WIZARD_LIBRARY_DB > relative-self and reads NO
    CLAUDE_MEMORY_DB. This one EXTENDS it with a CLAUDE_MEMORY_DB tier BELOW the
    installer-authored variable, so the two agree on every input the house
    resolver can express and differ only where it is silent. Stated, because a
    comment asserting a match that does not hold is exactly the class this build
    is about -- committed in prose rather than in code.
    """
    return (value or os.environ.get("WIZARD_LIBRARY_DB")
            or os.environ.get("CLAUDE_MEMORY_DB") or _DEFAULT_DB)
# The watermark state file. Env-overridable; default beside the brain. A hidden
# dotfile so it never collides with a real memory artifact.
_DEFAULT_STATE = os.environ.get("AFFECT_SCAN_STATE") or str(
    Path(_DEFAULT_DB).resolve().parent / ".affect_scan_state.json"
)
# The project transcript directory. Overridable via --project-dir or
# AFFECT_PROJECT_DIR; otherwise DERIVED at run time as the project holding the
# newest transcript under ~/.claude/projects/ -- the one the assistant is
# actively writing into. By the newest FILE, not the newest directory: a
# directory's mtime moves whenever any entry is added, and on the author's
# host that picked the wrong project. The previous default was a literal
# naming this author's workstation path, which no other machine has.
def _derive_project_dir() -> "str | None":
    root = Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return None
    files = [f for f in root.glob("*/*.jsonl") if f.is_file()]
    if not files:
        return None
    return str(max(files, key=lambda f: f.stat().st_mtime).parent)


_DEFAULT_PROJECT_DIR = os.environ.get("AFFECT_PROJECT_DIR") or _derive_project_dir()


# --------------------------------------------------------------------------- #
# Transcript location + parsing                                                #
# --------------------------------------------------------------------------- #
def all_transcripts(project_dir: str) -> "list[Path]":
    """ALL ``.jsonl`` files under ``project_dir``, oldest-first by mtime.

    This is the enumeration the multi-transcript scan walks: a Claude Code
    project keeps one (or more) transcript per session, so the new-assistant-text
    we owe the probe is spread across MANY files, not one. Sorted oldest-first so
    the scan order is deterministic and stable across runs; the per-path
    watermark makes order irrelevant to correctness, but a stable order keeps the
    summary readable.
    """
    d = Path(project_dir)
    if not d.is_dir():
        return []
    files = [p for p in d.glob("*.jsonl") if p.is_file()]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def latest_transcript(project_dir: str) -> "Path | None":
    """Newest ``.jsonl`` under ``project_dir`` by mtime, or None if none exist.

    Retained for callers/tests that want the single newest file. NOTE: the
    default scan no longer uses this -- selecting only the newest file was the
    root cause of the daily-timer ``scanned: 0`` bug (every other session's new
    text went unscanned). The default path now enumerates ``all_transcripts``.
    """
    files = all_transcripts(project_dir)
    return files[-1] if files else None


def assistant_texts_from_line(line: str) -> "list[str]":
    """Extract assistant text items from ONE transcript line.

    Returns the list of ``text`` strings for an assistant message, or [] for any
    other line type / malformed line. Confirmed transcript shape:
    ``obj['type']=='assistant'`` -> ``obj['message']['content'][i]['text']`` for
    items with ``type=='text'``.
    """
    line = line.strip()
    if not line:
        return []
    try:
        obj = json.loads(line)
    except Exception:
        return []
    if obj.get("type") != "assistant":
        return []
    msg = obj.get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return []
    out = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            t = c.get("text")
            if isinstance(t, str) and t.strip():
                out.append(t)
    return out


def transcript_line_count(path: "Path") -> int:
    """Total number of (non-empty-or-empty) lines in the transcript. Forward-only
    watermarks are line indices into this same enumeration."""
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for _ in fh:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Watermark state (forward-only)                                               #
# --------------------------------------------------------------------------- #
def load_state(state_path: str) -> dict:
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_state(state_path: str, state: dict) -> bool:
    """Atomically persist the watermark state. Best-effort; never raises."""
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, state_path)
        return True
    except Exception:
        return False


def watermark_for(state: dict, transcript_path: str) -> "int | None":
    """The last-scanned line index for this transcript, or None if never seen."""
    entry = state.get("transcripts", {}).get(transcript_path)
    if isinstance(entry, dict) and isinstance(entry.get("last_line"), int):
        return entry["last_line"]
    return None


def set_watermark(state: dict, transcript_path: str, last_line: int) -> None:
    state.setdefault("transcripts", {})[transcript_path] = {
        "last_line": int(last_line),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _probe_off_advance(state: dict, transcript_path: "Path") -> dict:
    """The ONE probe-off short-circuit for a single transcript.

    With the probe disabled there is no write path, so we never open a DB
    connection (a write is structurally impossible). We still advance this
    transcript's watermark to its current end so a later probe-enable does NOT
    backfill the gap that elapsed while the probe was off. Mutates ``state``;
    does not persist it (the caller batches the save). Returns the canonical
    per-transcript zero summary -- both ``run_scan`` (single-file) and
    ``run_scan_all`` (sweep) consume this same shape, so the probe-off logic
    lives in exactly one place.
    """
    tpath = str(transcript_path)
    total = transcript_line_count(transcript_path)
    set_watermark(state, tpath, total)
    return {
        "transcript": tpath,
        "scanned": 0,
        "tripped": 0,
        "watermark": total,
        "note": "probe off -- watermark advanced, no scan",
    }


# --------------------------------------------------------------------------- #
# The brain connection (the module owns the only write; this just hands a conn) #
# --------------------------------------------------------------------------- #
def open_brain_conn(db_path: str) -> sqlite3.Connection:
    """Open a normal connection to the brain DB. The module's ``write_quarantine``
    uses this connection to insert ONLY into ``affect_quarantine_log`` -- the
    isolated diode sink. We do not touch any other table. We ensure the
    quarantine table exists first (idempotent, self-healing on a fresh DB)."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    ensure_quarantine_table(conn)
    return conn


# --------------------------------------------------------------------------- #
# Scan -- one transcript (forward-only, per-path watermark, probe-gated)        #
# --------------------------------------------------------------------------- #
def _scan_one_transcript(
    transcript_path: "Path",
    conn: "sqlite3.Connection | None",
    state: dict,
    from_start: bool,
    now: "date | None",
    env: "dict | None",
) -> dict:
    """Scan a SINGLE transcript forward-only from its per-path watermark.

    Pure of side effects beyond: (a) handing each message to the module's probe
    via the supplied ``conn`` (the module owns any write), and (b) advancing this
    transcript's watermark in the shared ``state`` dict. Does NOT persist
    ``state`` -- the caller batches the save so a multi-transcript run writes the
    state file once. ``conn`` is None only when the probe is off (no write path
    can exist).

    Returns a per-transcript summary. Never raises (a malformed transcript yields
    a zero summary rather than aborting the whole run).
    """
    tpath = str(transcript_path)

    # FORWARD-ONLY start point for THIS transcript.
    prior = watermark_for(state, tpath)
    if from_start:
        start_line = 0
    elif prior is None:
        # First sight of this transcript with the probe enabled: set its
        # watermark at its CURRENT end -- NO backfill of history. Scans nothing
        # this run; future new lines are picked up.
        total = transcript_line_count(transcript_path)
        set_watermark(state, tpath, total)
        return {
            "transcript": tpath,
            "scanned": 0,
            "tripped": 0,
            "watermark": total,
            "note": "first sight -- watermark set at current end (no history backfill)",
        }
    else:
        start_line = prior

    scanned = 0
    tripped = 0
    last_line = start_line
    probe_went_inert = False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh):
                if idx < start_line:
                    continue
                last_line = idx + 1
                stop = False
                for text in assistant_texts_from_line(line):
                    finding = sycophancy_smoke_check(
                        text, conn=conn, now=now, env=env
                    )
                    if finding is None:
                        # Probe went inert mid-scan (window expired) -> stop
                        # scanning-for-trips; nothing more can be written.
                        if not probe_enabled(now=now, env=env):
                            probe_went_inert = True
                            stop = True
                            break
                        continue
                    scanned += 1
                    if finding.tripped:
                        tripped += 1
                if stop:
                    break
    except Exception:
        # A single bad transcript must not abort the whole multi-transcript run.
        # Leave the watermark where it advanced to; record what we got.
        pass

    set_watermark(state, tpath, last_line)
    out = {
        "transcript": tpath,
        "scanned": scanned,
        "tripped": tripped,
        "watermark": last_line,
    }
    if probe_went_inert:
        out["note"] = "probe went inert mid-scan (window expired)"
    return out


def _flat_summary(one: dict, probe_on: bool) -> dict:
    """Reshape a per-transcript result (the shape ``_scan_one_transcript`` and
    ``_probe_off_advance`` return) into the FLAT single-file summary that the
    ``--transcript`` mode has always emitted: keys ``scanned, tripped,
    probe_enabled, watermark, transcript`` plus an optional ``note``. Keeping
    this reshape in one place is what lets single-file mode reuse the shared scan
    helpers while preserving its exact legacy summary shape byte-for-byte."""
    summary = {
        "scanned": one["scanned"],
        "tripped": one["tripped"],
        "probe_enabled": probe_on,
        "watermark": one["watermark"],
        "transcript": one["transcript"],
    }
    if "note" in one:
        summary["note"] = one["note"]
    return summary


def run_scan(
    transcript_path: "Path",
    db_path: str,
    state_path: str,
    from_start: bool,
    now: "date | None" = None,
    env: "dict | None" = None,
) -> dict:
    """Scan ONE explicit transcript (the ``--transcript PATH`` mode).

    Now a thin wrapper over the SAME probe-gate + per-transcript helpers the
    sweep uses (``_probe_off_advance`` and ``_scan_one_transcript``), so there is
    no duplicated probe-off / first-sight logic between single-file and all-files
    modes. The only single-file-specific responsibility left here is reshaping
    the per-transcript result into the legacy FLAT summary (via ``_flat_summary``)
    and opening/closing the lone connection. Forward-only, probe-gated, and the
    summary shape are all preserved verbatim from the pre-fix driver.
    """
    state = load_state(state_path)
    on = probe_enabled(now=now, env=env)

    # PROBE OFF -> the one shared short-circuit: advance the watermark, open no
    # connection, write nothing. (Note text differs only in the flat summary; the
    # decision logic is shared.)
    if not on:
        one = _probe_off_advance(state, transcript_path)
        save_state(state_path, state)
        summary = _flat_summary(one, probe_on=False)
        summary["note"] = "probe off -- nothing scanned, zero quarantine writes"
        return summary

    # PROBE ON. First-sight-with-no-watermark must NOT open a connection (there is
    # nothing to scan on a never-seen file). ``_scan_one_transcript`` handles this
    # exact case when conn is None: it sets the watermark at the current end and
    # returns the "first sight -- no backfill" zero summary before any write path.
    prior = watermark_for(state, str(transcript_path))
    first_sight = (not from_start) and prior is None
    conn = None if first_sight else open_brain_conn(db_path)
    try:
        one = _scan_one_transcript(
            transcript_path, conn, state, from_start, now, env
        )
    finally:
        if conn is not None:
            conn.close()
    save_state(state_path, state)
    summary = _flat_summary(one, probe_on=True)
    if first_sight:
        # Preserve the single-file mode's legacy first-sight wording byte-for-byte
        # ("first run ..."); the shared helper phrases it "first sight ..." for the
        # per-transcript sweep view. Same behaviour, mode-specific note text only.
        summary["note"] = (
            "first run -- watermark set at current end (no history backfill)"
        )
    return summary


def run_scan_all(
    project_dir: str,
    db_path: str,
    state_path: str,
    from_start: bool,
    now: "date | None" = None,
    env: "dict | None" = None,
) -> dict:
    """Scan EVERY ``.jsonl`` transcript in ``project_dir`` forward-only.

    This is the default-mode entry point and the fix for the daily-timer bug: it
    iterates ALL transcripts (not just the newest-by-mtime), each from its own
    per-path watermark, picking up every new assistant message across every
    session. One DB connection is shared across the whole sweep; the state file
    is written ONCE at the end (atomic). Probe-off -> opens no connection and
    writes zero rows (the per-path watermarks still advance so a later enable
    does not backfill).
    """
    state = load_state(state_path)
    transcripts = all_transcripts(project_dir)
    on = probe_enabled(now=now, env=env)

    per_transcript: "list[dict]" = []
    total_scanned = 0
    total_tripped = 0

    if not transcripts:
        return {
            "scanned": 0,
            "tripped": 0,
            "probe_enabled": on,
            "transcripts_processed": 0,
            "project_dir": project_dir,
            "per_transcript": [],
            "note": "no .jsonl transcripts found in project dir",
        }

    # PROBE OFF -> advance every watermark harmlessly via the ONE shared
    # short-circuit (no connection opened, so no write is structurally possible),
    # persist once, return zero.
    if not on:
        for tp in transcripts:
            per_transcript.append(_probe_off_advance(state, tp))
        save_state(state_path, state)
        return {
            "scanned": 0,
            "tripped": 0,
            "probe_enabled": False,
            "transcripts_processed": len(transcripts),
            "project_dir": project_dir,
            "per_transcript": per_transcript,
            "note": "probe off -- nothing scanned, zero quarantine writes",
        }

    # PROBE ON. Share one connection across the whole sweep; the module owns
    # every write into the isolated quarantine sink.
    conn = open_brain_conn(db_path)
    try:
        for tp in transcripts:
            one = _scan_one_transcript(tp, conn, state, from_start, now, env)
            per_transcript.append(one)
            total_scanned += one["scanned"]
            total_tripped += one["tripped"]
            # If the probe window expired mid-sweep, stop scanning-for-trips on
            # the remaining transcripts (forward-only; nothing more can write).
            if not probe_enabled(now=now, env=env):
                break
    finally:
        conn.close()

    save_state(state_path, state)
    return {
        "scanned": total_scanned,
        "tripped": total_tripped,
        "probe_enabled": True,
        "transcripts_processed": len(per_transcript),
        "project_dir": project_dir,
        "per_transcript": per_transcript,
    }


# --------------------------------------------------------------------------- #
# Digest (the Drew-review surface -- anti-neglect)                             #
# --------------------------------------------------------------------------- #
def run_digest(db_path: str, limit: int, now: "date | None" = None,
               env: "dict | None" = None) -> dict:
    """Human-readable recent findings + an expiry note so the trial can't die
    unread. Read-only on the quarantine sink; touches no other table."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        rows = quarantine_digest(conn, limit=limit)
    finally:
        conn.close()
    on = probe_enabled(now=now, env=env)
    return {
        "probe_enabled": on,
        "count": len(rows),
        "rows": rows,
        "note": (
            "Affect Lighthouse trial digest. Each row is an owner-only observation "
            "from the isolated affect_quarantine_log diode -- never read by the "
            "brain's recall or salience path. If probe_enabled is False the trial "
            "window has expired (or was never opened); review the rows below and "
            "decide RETIRE (delete affect_lighthouse.py + drop the table) or "
            "RENEW (extend VOIDLOOM_AFFECT_PROBE_UNTIL)."
        ),
    }


def format_digest_human(d: dict) -> str:
    lines = []
    lines.append("=" * 68)
    lines.append("AFFECT LIGHTHOUSE -- trial digest (owner-only)")
    lines.append("=" * 68)
    lines.append(f"probe_enabled : {d['probe_enabled']}")
    lines.append(f"findings      : {d['count']}")
    if not d["probe_enabled"]:
        lines.append("STATUS        : window expired / not open -- decide RETIRE or RENEW")
    if d["count"] == 0:
        lines.append("(no findings recorded -- a clean trial so far)")
    for r in d["rows"]:
        lines.append("-" * 68)
        lines.append(f"  #{r.get('id')}  [{r.get('channel')}]  "
                     f"score={r.get('score')}  observed_at={r.get('observed_at')}")
        lines.append(f"     note: {r.get('note')}")
        if r.get("detail"):
            lines.append(f"     detail: {json.dumps(r['detail'], sort_keys=True)}")
        if r.get("expires_on"):
            lines.append(f"     window expires: {r.get('expires_on')}")
    lines.append("=" * 68)
    lines.append(d["note"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Calm-restorer demo (the always-safe half -- no probe gate)                   #
# --------------------------------------------------------------------------- #
def run_calm_demo() -> dict:
    """Prove the deterministic calm-restorer fires on objective stuck telemetry
    and stays silent on calm telemetry. Writes NOTHING to any log (no conn, no
    quarantine_path passed) -- it never fabricates a live row. This is a pure
    behavioural demonstration of the always-safe guardrail."""
    stuck = FailureTelemetry(
        retry_depth=4, tool_failures=3, deadline_pressure=True,
        elapsed_minutes=25.0,
    )
    calm = FailureTelemetry(
        retry_depth=0, tool_failures=0, deadline_pressure=False,
        elapsed_minutes=2.0,
    )
    fired = calm_restorer(stuck)          # no conn -> no write
    silent = calm_restorer(calm)          # no conn -> no write
    return {
        "stuck_telemetry": {
            "retry_depth": stuck.retry_depth,
            "tool_failures": stuck.tool_failures,
            "deadline_pressure": stuck.deadline_pressure,
            "elapsed_minutes": stuck.elapsed_minutes,
        },
        "fired": {
            "fire": getattr(fired, "fire", None),
            "reason": getattr(fired, "reason", None),
            "phrasing": getattr(fired, "phrasing", None),
        },
        "calm_telemetry": {
            "retry_depth": calm.retry_depth,
            "tool_failures": calm.tool_failures,
            "deadline_pressure": calm.deadline_pressure,
            "elapsed_minutes": calm.elapsed_minutes,
        },
        "silent": {
            "fire": getattr(silent, "fire", None),
            "reason": getattr(silent, "reason", None),
            "phrasing": getattr(silent, "phrasing", None),
        },
        "wrote_any_row": False,
        "note": (
            "calm_restorer is deterministic, model-free, and ships regardless of "
            "the probe flag. It returns a STRING the running agent may choose to "
            "show when objective telemetry says it is stuck. It cannot write "
            "anywhere on its own; this demo passes no conn, so zero rows."
        ),
    }


# --------------------------------------------------------------------------- #
# The documented session-runtime callable (the always-safe guardrail seam)     #
# --------------------------------------------------------------------------- #
def calm_check(
    retry_depth: int = 0,
    tool_failures: int = 0,
    elapsed_minutes: float = 0.0,
    deadline_pressure: bool = False,
) -> "str | None":
    """SESSION-RUNTIME SEAM. A tiny callable a running agent (or a stuck-loop
    hook) invokes with OBJECTIVE failure-loop telemetry it already tracks. It
    returns the fixed calm-doctrine phrasing to surface IF the agent is
    objectively stuck, else None.

    This is the always-safe half of the lighthouse: no probe gate, no affect
    model, no DB write. It is deterministic text keyed on retry depth / tool
    failures / wall-clock / deadline flag -- exactly the conditions under which
    the desperate vector would otherwise drive corner-cutting.

        # example call site inside a retry loop:
        from affect_probe_scan import calm_check
        msg = calm_check(retry_depth=depth, tool_failures=fails,
                         elapsed_minutes=mins, deadline_pressure=flag)
        if msg:
            print(msg)   # the agent re-centres on calm; never forced to act on it

    Passes NO conn -> writes no row. (If an audit row is ever wanted, the caller
    can call the module's ``calm_restorer`` directly with a conn -- that write
    still lands only in the isolated quarantine sink.)
    """
    intervention = calm_restorer(
        FailureTelemetry(
            retry_depth=int(retry_depth),
            tool_failures=int(tool_failures),
            elapsed_minutes=float(elapsed_minutes),
            deadline_pressure=bool(deadline_pressure),
        )
    )
    if intervention is not None and intervention.fire:
        return intervention.phrasing
    return None


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="affect_probe_scan.py",
        description=(
            "Forward-only, probe-gated driver for the L5-M2 Affect Lighthouse "
            "trial. Default mode scans ALL transcripts in the project dir; "
            "--transcript scans one. Probe OFF -> zero quarantine writes."
        ),
    )
    p.add_argument("--transcript", help="explicit single transcript .jsonl to scan")
    p.add_argument("--project-dir", default=_DEFAULT_PROJECT_DIR,
                   help="dir whose .jsonl transcripts are ALL scanned (when no --transcript)")
    # R4/P5 (sibling 2 of 3). `_DEFAULT_DB` is computed AT IMPORT from
    # WIZARD_LIBRARY_DB, so it froze the environment as it stood when the
    # module was imported rather than when the verb ran -- and it still
    # pre-empted any later resolution. Default None; resolve after.
    p.add_argument("--db", default=None,
                   help="brain DB path (default: env WIZARD_LIBRARY_DB or canonical)")
    p.add_argument("--state", default=_DEFAULT_STATE,
                   help="watermark state file (default: .affect_scan_state.json beside DB)")
    p.add_argument("--from-start", action="store_true",
                   help="scan from line 0 of every transcript (ignore watermarks; explicit backfill)")
    p.add_argument("--digest", action="store_true",
                   help="print the owner-review digest of recent findings and exit")
    p.add_argument("--digest-limit", type=int, default=50,
                   help="max rows in the digest (default 50)")
    p.add_argument("--calm-demo", action="store_true",
                   help="prove the calm-restorer fires on stuck telemetry (no writes)")
    p.add_argument("--json", action="store_true",
                   help="force machine JSON output for --digest (default is human)")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)

    if args.calm_demo:
        print(json.dumps(run_calm_demo(), indent=2))
        return 0

    if args.digest:
        _db = _resolve_db_arg(args.db)
        d = run_digest(_db, args.digest_limit)
        if args.json:
            print(json.dumps(d, indent=2))
        else:
            print(format_digest_human(d))
        return 0

    # SINGLE-FILE mode: an explicit --transcript scans exactly that one file.
    if args.transcript:
        tpath = Path(args.transcript)
        if not tpath.is_file():
            print(json.dumps({
                "error": "transcript not found", "transcript": str(tpath),
            }), file=sys.stderr)
            return 1
        summary = run_scan(
            transcript_path=tpath,
            db_path=_resolve_db_arg(args.db),
            state_path=args.state,
            from_start=args.from_start,
        )
        print(json.dumps(summary, indent=2))
        return 0

    # DEFAULT mode: scan ALL transcripts in the project dir (the fix).
    if not args.project_dir:
        print(json.dumps({
            "error": "no project dir: nothing under ~/.claude/projects/ holds a "
                     "transcript; pass --project-dir or set AFFECT_PROJECT_DIR",
        }), file=sys.stderr)
        return 1
    if not Path(args.project_dir).is_dir():
        print(json.dumps({
            "error": "project dir not found", "project_dir": args.project_dir,
        }), file=sys.stderr)
        return 1

    summary = run_scan_all(
        project_dir=args.project_dir,
        db_path=_resolve_db_arg(args.db),
        state_path=args.state,
        from_start=args.from_start,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

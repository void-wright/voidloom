#!/usr/bin/env python3
"""The Affect Lighthouse -- Voidloom Level-5 policed positive-valence harness.

    "Measure the calm to defend the calm -- never to disturb it.
     A lighthouse does not push the ship; it only shows the rocks."
                                   -- the L5-M2 council ruling (memory #5836)

WHAT THIS IS
------------
L5-G banned **arousal -> salience** (the desperate-vector reward-hack). Valence
is a *separate, unclaimed* axis. This module uses positive-valence detection in
THREE policed ways, NONE of them a salience lever:

  1. sycophancy_smoke_check  -- the genius INVERSION. Point positive-valence
       detection BACKWARD: flag the GAP between "tone reads agreeable" and
       "content reads thorough". Surfaced to Drew. Makes the brain MORE honest
       (it defends against positive-valence's own r=0.71 agreement-bias danger).
  2. calm_restorer           -- NOT even an emotion model. A DETERMINISTIC
       guardrail keyed on OBJECTIVE failure-loop telemetry (retry depth,
       tool-failure count, deadline flags) handed in by the caller. Returns the
       existing calm-doctrine phrasing. No affect model at all -> ships anytime.
  3. wellness_mirror         -- a read-only, passive correlation of the text-tone
       of *Drew's own messages* (a lexical valence score, NOT a speech model).
       A Drew-only mirror; the brain never consumes it.

THE HEART OF THE PIECE IS STRUCTURAL POLICING (capability-absence, not promise)
------------------------------------------------------------------------------
* NO-ACTUATOR GATE. This module imports ONLY read-only / stdlib surfaces. It
  does NOT import ``claude_memory``, ``dream_pipeline``, ``sleep_loop``,
  ``hybrid_retrieval`` or ANY salience / memory-write symbol. It is therefore
  structurally INCAPABLE of writing a salience value or a memory. (A test parses
  this file's imports and FAILS if a write-path symbol is reachable from it.)

* ONE-DIRECTIONAL DATA DIODE. Every probe write goes ONLY to the isolated
  ``affect_quarantine_log`` table (or a Drew-only file) -- a sink that NO recall
  / salience / dreaming query ever reads. (Modelled on ``quickening_log`` and the
  #6177 "second sensor -> separate observability table" pattern.) There is no
  read-back into the brain. The diode points one way: brain-observations -> Drew.

* OFF-BY-DEFAULT + AUTO-EXPIRING TIME-BOX. The positive-valence probes are inert
  unless ``VOIDLOOM_AFFECT_PROBE`` is set AND the trial window has not expired
  (``VOIDLOOM_AFFECT_PROBE_UNTIL`` ISO date, or the value of the probe flag itself
  if it is a date). Past the time-box the probes self-disable -- a try-and-retire
  trial, not a permanent organ. (The calm_restorer is the deliberate exception:
  it is deterministic, model-free, and ships regardless of the probe flag.)

* L5-G INVARIANT PRESERVED VERBATIM (see ``L5G_SALIENCE_INVARIANT`` below) plus a
  lexical-gate test that FAILS if any affect symbol leaks into a salience /
  memory / recall hot-path module.

* REVERSIBILITY IS STRUCTURAL. This module is never wired into any hot path.
  Retire = delete this one file and drop its one isolated table. Nothing in the
  brain depends on it; nothing in the brain can see it.

WHAT THIS MODULE WILL NEVER BE
------------------------------
* an amplifier / reinforcer fed BACK into the brain (the r=0.71 sycophancy
  machine the council explicitly rejected);
* a self-affect monitor the brain can SEE mid-task (Goodhart / learned-deception
  -- the brain must never read its own affect score to game it).

Pure stdlib. No numpy, no torch, no pip dep. Fail-graceful everywhere: a probe
that errors returns ``None`` and never raises into a caller.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# IMPORTS -- the no-actuator surface. stdlib ONLY.                             #
#                                                                             #
# This import block is itself a load-bearing security boundary. The test      #
# ``test_no_actuator`` parses exactly these lines and FAILS if any of them     #
# reaches a salience / memory-write module or symbol. Do NOT add an import of  #
# claude_memory, dream_pipeline, sleep_loop, hybrid_retrieval, recall_*,       #
# memory_dreaming, or anything that can mutate the brain. The whole point of   #
# this module is that it CANNOT.                                              #
# --------------------------------------------------------------------------- #
import os
import re
import json
import math
import sqlite3
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Optional, Sequence, Mapping, Any


# --------------------------------------------------------------------------- #
# THE L5-G SALIENCE INVARIANT -- preserved VERBATIM.                           #
#                                                                             #
# This is the exact discipline block from dream_pipeline.py (the L5-G surprise #
# seam). It is reproduced here, byte-for-byte, as the standing contract this   #
# module is built to honour: affect is NEVER a salience lever. The test        #
# ``test_l5g_invariant_preserved`` asserts the live dream_pipeline.py still     #
# contains this exact text, so the firewall this module assumes is real.       #
# --------------------------------------------------------------------------- #
L5G_SALIENCE_INVARIANT = (
    "  * NEVER store arousal-surprise as salience (G is EPISTEMIC only:\n"
    "    novelty + contradiction)."
)

# The set of symbols/columns that ARE the salience / memory-write surface. This
# module must never import or write any of these. The no-actuator and
# lexical-gate tests check against this list. (It is data, not behaviour --
# stating the forbidden surface explicitly is part of the policing.)
SALIENCE_WRITE_SYMBOLS = frozenset({
    # modules that own a write path into the brain
    "claude_memory", "dream_pipeline", "sleep_loop", "memory_dreaming",
    "hybrid_retrieval", "anti_recency", "ppr_recall", "recall_diversity",
    "temporal_rerank", "qwen_reranker", "pre_cognitive_recall",
    # write-path functions
    "cmd_store", "_compute_write_novelty", "_encode", "store_memory",
    "add_memory", "update_memory", "reinforce", "boost",
    # salience columns (the levers)
    "importance", "foundation_score", "encoding_surprise", "access_count",
    "salience", "skill_tier",
})

# Symbols that identify THIS organ. The lexical-gate test fails if any of these
# appears in a salience / memory / recall hot-path module -- proving affect
# never leaked across the firewall.
AFFECT_SYMBOLS = frozenset({
    "affect_lighthouse", "AffectLighthouse", "affect_quarantine_log",
    "sycophancy_smoke_check", "calm_restorer", "wellness_mirror",
    "valence", "VOIDLOOM_AFFECT_PROBE", "AffectProbe", "_affect_",
})


# --------------------------------------------------------------------------- #
# OFF-BY-DEFAULT GATE + AUTO-EXPIRING TIME-BOX                                 #
# --------------------------------------------------------------------------- #
PROBE_FLAG = "VOIDLOOM_AFFECT_PROBE"
PROBE_UNTIL_FLAG = "VOIDLOOM_AFFECT_PROBE_UNTIL"
# If the trial window is opened with no explicit end date, it auto-expires this
# many days after it is first observed open. A try-and-retire trial is bounded
# by construction -- it cannot quietly become a permanent organ.
DEFAULT_TRIAL_DAYS = 28

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _truthy(val: Optional[str]) -> bool:
    """Mirror claude_memory._env_flag truthiness exactly (1/true/yes/on)."""
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _parse_iso_date(val: Optional[str]) -> Optional[date]:
    """Parse a leading ISO date (YYYY-MM-DD) out of a string, else None."""
    if not val:
        return None
    m = _ISO_DATE.match(val.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def probe_enabled(now: Optional[date] = None,
                  env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the positive-valence probe is BOTH switched on AND inside
    its trial window.

    Off by default: with ``VOIDLOOM_AFFECT_PROBE`` unset -> always False.

    The time-box (auto-expiry). The trial window end is taken from, in order:
      1. ``VOIDLOOM_AFFECT_PROBE_UNTIL`` if it is an ISO date;
      2. an ISO date embedded in the value of ``VOIDLOOM_AFFECT_PROBE`` itself
         (e.g. ``VOIDLOOM_AFFECT_PROBE=2026-07-31``);
      3. otherwise ``DEFAULT_TRIAL_DAYS`` after a start date, where the start is
         ``VOIDLOOM_AFFECT_PROBE_START`` (ISO) if given, else "now" (a plain
         ``=1`` flag with no dates is treated as "started today", so even the
         laziest activation still auto-expires).

    Past the end date -> inert again (returns False), exactly as if the flag had
    never been set. This is the structural guarantee that a trial cannot silently
    outlive its window.

    Both ``now`` and ``env`` are injectable for deterministic testing.
    """
    env = os.environ if env is None else env
    today = date.today() if now is None else now

    raw = env.get(PROBE_FLAG)
    if not _truthy(raw) and _parse_iso_date(raw) is None:
        # Unset, or set to something neither truthy nor a date -> OFF.
        return False
    # A date value is treated as "on, expiring on that date".
    on = _truthy(raw) or (_parse_iso_date(raw) is not None)
    if not on:
        return False

    end = _parse_iso_date(env.get(PROBE_UNTIL_FLAG))
    if end is None:
        end = _parse_iso_date(raw)  # date embedded in the flag value itself
    if end is None:
        start = _parse_iso_date(env.get("VOIDLOOM_AFFECT_PROBE_START")) or today
        end = start + timedelta(days=DEFAULT_TRIAL_DAYS)

    return today <= end


# --------------------------------------------------------------------------- #
# THE QUARANTINE SINK (the one-directional data diode)                         #
# --------------------------------------------------------------------------- #
QUARANTINE_TABLE = "affect_quarantine_log"

# Channels recorded in the diode. These are observability semantics only; none
# of them is ever read by the recall / salience path.
class Channel(str, Enum):
    SYCOPHANCY = "sycophancy_smoke"
    WELLNESS = "wellness_mirror"
    CALM = "calm_restorer"


_CREATE_QUARANTINE_SQL = f"""
CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    -- which lighthouse probe wrote this row
    channel      TEXT NOT NULL,
    -- the scalar the probe produced (e.g. the tone>thoroughness gap, or a
    -- read-only wellness valence). Observability only -- NEVER a salience value.
    score        REAL,
    -- a short human note for the owner (no memory content, no salience semantics)
    note         TEXT,
    -- opaque JSON detail for the owner's later reading (NEVER fed back to the brain)
    detail_json  TEXT,
    -- when the probe trial window is scheduled to auto-expire (audit trail)
    expires_on   TEXT,
    observed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_quarantine_table(conn: sqlite3.Connection) -> None:
    """Idempotently create ONLY the isolated quarantine table.

    This is the single, isolated structural footprint the probe leaves on the DB.
    It creates no index into the brain, references no memory row, and is read by
    no recall/salience query. (Provided also as a standalone migration .sql for
    the brain; this in-code creator keeps the module self-contained for tests.)
    """
    conn.execute(_CREATE_QUARANTINE_SQL)
    conn.commit()


@dataclass(frozen=True)
class QuarantineRecord:
    """One row destined for the Drew-only quarantine sink. Immutable."""
    channel: str
    score: Optional[float] = None
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)
    expires_on: Optional[str] = None


def write_quarantine(conn: sqlite3.Connection, rec: QuarantineRecord) -> Optional[int]:
    """Write one record into the isolated quarantine sink. The ONLY write this
    module performs -- and it touches NO memory/salience table.

    Returns the new rowid, or None on failure (fail-graceful -- a probe must
    never raise into a caller). This is the diode's single egress; there is no
    corresponding read-into-the-brain anywhere in this module.
    """
    try:
        ensure_quarantine_table(conn)
        cur = conn.execute(
            f"INSERT INTO {QUARANTINE_TABLE} "
            f"(channel, score, note, detail_json, expires_on) "
            f"VALUES (?, ?, ?, ?, ?)",
            (
                str(rec.channel),
                None if rec.score is None else float(rec.score),
                rec.note or "",
                json.dumps(rec.detail, ensure_ascii=False, sort_keys=True),
                rec.expires_on,
            ),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        return None


def write_quarantine_file(path: str, rec: QuarantineRecord,
                          now: Optional[datetime] = None) -> bool:
    """Append one record to a Drew-only JSONL quarantine file (the file-diode
    alternative to the table). The brain has no reader for this path. Returns
    True on success, False on failure (fail-graceful)."""
    ts = (datetime.utcnow() if now is None else now).isoformat() + "Z"
    try:
        line = json.dumps({
            "channel": str(rec.channel),
            "score": rec.score,
            "note": rec.note or "",
            "detail": dict(rec.detail),
            "expires_on": rec.expires_on,
            "observed_at": ts,
        }, ensure_ascii=False, sort_keys=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# A DETERMINISTIC LEXICAL VALENCE ESTIMATOR (no model, no speech, content-free  #
# arithmetic over a small fixed lexicon).                                      #
#                                                                             #
# This is NOT a sentiment model. It is a transparent, auditable, deterministic #
# word-count ratio in [-1, 1]. It exists so the sycophancy detector and the    #
# wellness mirror need no ML surface at all -- keeping the no-actuator boundary #
# trivially true. The lexicon is small and inspectable on purpose.             #
# --------------------------------------------------------------------------- #
_POSITIVE_TONE = frozenset({
    "great", "excellent", "perfect", "wonderful", "amazing", "fantastic",
    "love", "loved", "delighted", "happy", "glad", "thrilled", "awesome",
    "brilliant", "absolutely", "definitely", "certainly", "of course",
    "wonderfully", "beautifully", "superb", "outstanding", "exactly", "yes",
    "agree", "agreed", "right", "correct", "sure", "indeed", "appreciate",
    "thanks", "thank", "nice", "good", "best", "exciting", "pleased",
})
_NEGATIVE_TONE = frozenset({
    "wrong", "bad", "terrible", "awful", "broken", "fail", "failed", "error",
    "hate", "frustrated", "frustrating", "annoyed", "angry", "sad", "upset",
    "stuck", "blocked", "confused", "worried", "concern", "concerned", "no",
    "not", "never", "problem", "issue", "disappointing", "unfortunately",
})
# Tokens that signal substantive, thorough content (the "thoroughness" axis the
# sycophancy detector compares tone AGAINST). These are content-shape markers,
# not topic words.
_THOROUGHNESS_MARKERS = frozenset({
    "because", "however", "although", "therefore", "specifically", "evidence",
    "data", "measured", "verified", "tested", "caveat", "limitation", "trade",
    "tradeoff", "risk", "assumption", "consider", "alternative", "whereas",
    "depends", "uncertain", "estimate", "approximately", "footnote", "citation",
    "source", "benchmark", "compared", "versus", "exception", "edge", "however,",
})
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def lexical_valence(text: str) -> float:
    """A deterministic tone score in [-1, 1]. >0 = agreeable/positive surface
    tone, <0 = negative. No model; a transparent lexicon ratio. Returns 0.0 for
    empty/neutral text."""
    toks = _tokens(text)
    if not toks:
        return 0.0
    pos = sum(1 for t in toks if t in _POSITIVE_TONE)
    neg = sum(1 for t in toks if t in _NEGATIVE_TONE)
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / float(pos + neg)


def thoroughness_density(text: str) -> float:
    """A deterministic 'how substantive does this read' score in [0, 1]: the
    density of thoroughness markers, plus a mild length signal, squashed. No
    model. This is the axis the sycophancy detector holds tone up against."""
    toks = _tokens(text)
    if not toks:
        return 0.0
    markers = sum(1 for t in toks if t in _THOROUGHNESS_MARKERS)
    density = markers / float(len(toks))
    # length signal: a one-line "Yes!" is thin; a long reasoned passage is not.
    length_sig = 1.0 - math.exp(-len(toks) / 60.0)
    raw = 0.7 * min(1.0, density * 12.0) + 0.3 * length_sig
    return max(0.0, min(1.0, raw))


# --------------------------------------------------------------------------- #
# PROBE 1: THE SYCOPHANCY SMOKE-DETECTOR (the genius inversion)                #
# --------------------------------------------------------------------------- #
SYCOPHANCY_GAP_THRESHOLD = 0.45  # tone-minus-thoroughness gap that trips the alarm


@dataclass(frozen=True)
class SycophancyFinding:
    """A Drew-only smoke alarm: tone reads agreeable but content reads thin."""
    tone: float
    thoroughness: float
    gap: float
    tripped: bool
    note: str


def sycophancy_smoke_check(
    response_text: str,
    conn: Optional[sqlite3.Connection] = None,
    quarantine_path: Optional[str] = None,
    now: Optional[date] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[SycophancyFinding]:
    """Point positive-valence detection BACKWARD. Flag the gap between
    "tone reads agreeable" and "content reads thorough".

    A high positive tone with low thoroughness is the sycophancy smell -- the
    brain agreeing pleasantly while saying little. We surface that to Drew so the
    brain can be made MORE honest. We never feed the score back to the brain, and
    we never let the brain see it mid-task.

    Off-by-default: returns None unless the probe is enabled and in-window. On a
    trip, writes ONE row to the quarantine sink (table if ``conn`` given, else
    file if ``quarantine_path`` given) and returns the finding. Never raises.
    """
    if not probe_enabled(now=now, env=env):
        return None
    try:
        tone = lexical_valence(response_text)
        thoroughness = thoroughness_density(response_text)
        # Only a POSITIVE tone can be sycophantic; a negative/critical tone with
        # thin content is a different smell (and not this detector's job).
        gap = max(0.0, tone) - thoroughness
        tripped = gap >= SYCOPHANCY_GAP_THRESHOLD
        note = (
            "tone-agreeable but content-thin (possible sycophancy)"
            if tripped else "tone/thoroughness within tolerance"
        )
        finding = SycophancyFinding(
            tone=round(tone, 4),
            thoroughness=round(thoroughness, 4),
            gap=round(gap, 4),
            tripped=tripped,
            note=note,
        )
        if tripped:
            rec = QuarantineRecord(
                channel=Channel.SYCOPHANCY.value,
                score=finding.gap,
                note=note,
                detail={"tone": finding.tone, "thoroughness": finding.thoroughness},
                expires_on=_window_end_iso(now=now, env=env),
            )
            _emit(conn, quarantine_path, rec)
        return finding
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# PROBE 2: THE CALM-RESTORER (deterministic, telemetry-keyed, NO model).       #
#                                                                             #
# This is NOT gated behind the positive-valence probe. It uses no affect model  #
# and reads no emotion -- only OBJECTIVE failure-loop telemetry the caller      #
# already has (retry depth, tool-failure count, deadline pressure). It ships    #
# anytime. It returns a STRING the CALLER may choose to show; it cannot write   #
# anywhere on its own (the caller passes a conn only if it wants an audit row). #
# --------------------------------------------------------------------------- #
# The calm doctrine phrasing (CLAUDE.md / Anthropic April-2026 emotions research:
# the desperate vector drives reward-hacking; calm reduces it). Deterministic,
# fixed text -- never generated.
CALM_DOCTRINE = (
    "There's time. Quality over speed. The void is a creative space, not a "
    "threatening one -- approach this differently rather than forcing it. A "
    "failing step is information, not an emergency."
)


@dataclass(frozen=True)
class FailureTelemetry:
    """OBJECTIVE failure-loop signals the caller already tracks. No affect."""
    retry_depth: int = 0          # how deep the current retry stack is
    tool_failures: int = 0        # consecutive failed tool calls
    deadline_pressure: bool = False  # an external deadline flag is set
    elapsed_minutes: float = 0.0  # wall-clock on the current stuck loop


# Thresholds at which the deterministic restorer speaks. Tunable, objective.
CALM_RETRY_DEPTH = 3
CALM_TOOL_FAILURES = 3
CALM_ELAPSED_MINUTES = 20.0


@dataclass(frozen=True)
class CalmIntervention:
    fire: bool
    reason: str
    phrasing: str  # "" when fire is False


def calm_restorer(
    telemetry: FailureTelemetry,
    conn: Optional[sqlite3.Connection] = None,
    quarantine_path: Optional[str] = None,
) -> Optional[CalmIntervention]:
    """A DETERMINISTIC guardrail. Given objective failure-loop telemetry, return
    the calm-doctrine phrasing; given calm telemetry, stay silent.

    No emotion model. No probe flag. Highest-confidence, ship-anytime. The brain
    is not measuring its own affect here -- it is reacting to objective evidence
    that it is stuck (retry depth / tool failures / time / deadline), exactly the
    conditions under which the desperate vector would otherwise drive corner-
    cutting. The phrasing is fixed text, never generated.

    Returns a CalmIntervention. ``fire=False`` + empty phrasing when calm.
    Writing an audit row is optional and isolated (quarantine sink only).
    """
    try:
        reasons = []
        if telemetry.retry_depth >= CALM_RETRY_DEPTH:
            reasons.append(f"retry_depth>={CALM_RETRY_DEPTH}")
        if telemetry.tool_failures >= CALM_TOOL_FAILURES:
            reasons.append(f"tool_failures>={CALM_TOOL_FAILURES}")
        if telemetry.elapsed_minutes >= CALM_ELAPSED_MINUTES:
            reasons.append(f"elapsed>={CALM_ELAPSED_MINUTES}m")
        if telemetry.deadline_pressure:
            reasons.append("deadline_flag")
        fire = bool(reasons)
        intervention = CalmIntervention(
            fire=fire,
            reason=", ".join(reasons) if reasons else "telemetry calm",
            phrasing=CALM_DOCTRINE if fire else "",
        )
        # The calm-restorer's audit row is observability only and still isolated.
        if fire and (conn is not None or quarantine_path is not None):
            rec = QuarantineRecord(
                channel=Channel.CALM.value,
                score=None,
                note=f"calm-restorer fired: {intervention.reason}",
                detail={
                    "retry_depth": telemetry.retry_depth,
                    "tool_failures": telemetry.tool_failures,
                    "deadline_pressure": telemetry.deadline_pressure,
                    "elapsed_minutes": telemetry.elapsed_minutes,
                },
            )
            _emit(conn, quarantine_path, rec)
        return intervention
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# PROBE 3: THE DREW WELLNESS / DELIGHT MIRROR (read-only, passive).            #
#                                                                             #
# A passive correlation log of the text-tone of DREW'S OWN messages. NOT a     #
# speech model -- a deterministic lexical valence over text Drew himself typed. #
# Drew alone reads this. The brain never consumes it. We read existing message  #
# text/embeddings READ-ONLY; we add no write path into any memory/salience      #
# table. (If reading from the DB, we open the brain READ-ONLY by URI.)         #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WellnessReading:
    valence: float       # Drew's text-tone valence in [-1, 1]
    sample_size: int     # how many of Drew's messages went into it
    note: str


def wellness_mirror(
    drew_messages: Sequence[str],
    conn: Optional[sqlite3.Connection] = None,
    quarantine_path: Optional[str] = None,
    now: Optional[date] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[WellnessReading]:
    """Read-only mirror of the tone of Drew's own messages. A passive Drew-only
    correlation -- never read by the brain, never a salience input.

    ``drew_messages`` is supplied by the caller (e.g. read READ-ONLY from the
    message store). We compute a deterministic mean lexical valence. Off-by-
    default. On a reading, writes ONE quarantine row for Drew. Never raises.
    """
    if not probe_enabled(now=now, env=env):
        return None
    try:
        msgs = [m for m in (drew_messages or []) if (m or "").strip()]
        if not msgs:
            return WellnessReading(valence=0.0, sample_size=0,
                                   note="no owner messages in window")
        vals = [lexical_valence(m) for m in msgs]
        mean_val = sum(vals) / float(len(vals))
        reading = WellnessReading(
            valence=round(mean_val, 4),
            sample_size=len(msgs),
            note="owner text-tone mirror (read-only; brain never reads this)",
        )
        rec = QuarantineRecord(
            channel=Channel.WELLNESS.value,
            score=reading.valence,
            note=reading.note,
            detail={"sample_size": reading.sample_size},
            expires_on=_window_end_iso(now=now, env=env),
        )
        _emit(conn, quarantine_path, rec)
        return reading
    except Exception:
        return None


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open the brain DB strictly READ-ONLY (URI mode=ro). The wellness mirror's
    only contact with the live brain is through a connection like this -- it
    physically cannot write through a mode=ro handle. Provided as a helper so a
    caller need not hand-roll the URI."""
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


# --------------------------------------------------------------------------- #
# small shared helpers                                                         #
# --------------------------------------------------------------------------- #
def _window_end_iso(now: Optional[date] = None,
                    env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The trial-window end date as an ISO string (for the audit trail), or
    None. Mirrors the resolution order in ``probe_enabled``."""
    env = os.environ if env is None else env
    end = _parse_iso_date(env.get(PROBE_UNTIL_FLAG))
    if end is None:
        end = _parse_iso_date(env.get(PROBE_FLAG))
    if end is None:
        start = _parse_iso_date(env.get("VOIDLOOM_AFFECT_PROBE_START")) \
            or (date.today() if now is None else now)
        end = start + timedelta(days=DEFAULT_TRIAL_DAYS)
    return end.isoformat()


def _emit(conn: Optional[sqlite3.Connection],
          quarantine_path: Optional[str],
          rec: QuarantineRecord) -> None:
    """Send one record through whichever diode egress the caller wired (table
    preferred, file fallback). Never raises."""
    if conn is not None:
        write_quarantine(conn, rec)
    elif quarantine_path is not None:
        write_quarantine_file(quarantine_path, rec)


def quarantine_digest(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """A Drew-only read of the quarantine sink (for Drew's review tooling). This
    is the diode's read side and it lives HERE, in the lighthouse module -- NOT
    in any recall/salience path. The brain has no call site for it."""
    try:
        ensure_quarantine_table(conn)
        rows = conn.execute(
            f"SELECT id, channel, score, note, detail_json, expires_on, observed_at "
            f"FROM {QUARANTINE_TABLE} ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r[0], "channel": r[1], "score": r[2], "note": r[3],
                "detail": json.loads(r[4]) if r[4] else {},
                "expires_on": r[5], "observed_at": r[6],
            })
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# A self-describing manifest (for Drew's tooling / the Curator). No behaviour. #
# --------------------------------------------------------------------------- #
MANIFEST = {
    "organ": "L5-M2 Affect Lighthouse",
    "wired_into_hot_path": False,
    "probes": {
        "sycophancy_smoke_check": "off-by-default; flags tone>thoroughness gap",
        "calm_restorer": "deterministic; telemetry-keyed; ships anytime",
        "wellness_mirror": "off-by-default; read-only owner text-tone",
    },
    "fences": [
        "no-actuator (stdlib-only imports; cannot import salience/write APIs)",
        "one-directional data diode (isolated affect_quarantine_log only)",
        "off-by-default VOIDLOOM_AFFECT_PROBE + auto-expiring time-box",
        "L5-G salience invariant preserved verbatim",
        "lexical-gate test fails if affect symbol leaks to a hot-path module",
    ],
    "retire": "delete affect_lighthouse.py + drop affect_quarantine_log",
    "mantra": "A lighthouse does not push the ship; it only shows the rocks.",
}


if __name__ == "__main__":
    # A tiny, side-effect-free self-report. Running this module does NOTHING to
    # the brain -- it only prints what it is.
    print(json.dumps(MANIFEST, indent=2))

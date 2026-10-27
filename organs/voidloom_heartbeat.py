#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_heartbeat.py — THE SENSOR THE HOOK WRITES AND DOCTOR READS.

ONE SCHEMA, TWO SIDES.  The session-start hook APPENDS one line per fire; ``voidloom
doctor`` JOINS those lines against the PLATFORM'S OWN SESSION RECORDS.  Both sides
live here so the writer and the reader cannot drift apart.

WHY APPEND-ONLY, NOT A SINGLE OVERWRITTEN FILE (R3 as amended, twice)
---------------------------------------------------------------------
The first design overwrote one file per platform with {ts, ...}.  VAEL trusted the hook,
recorded one fire, moved the hook definition aside, ran TWO more sessions, and doctor read
"age 0.7 min -> HEALTHY" -- for a hook that was DEAD.  Calendar recency is confounded by
usage cadence: a user away three weeks reads STALE with a healthy hook; a user who ran five
sessions since the hook died reads HEALTHY.  The row that differs between "fired once,
never again" and "fires every session" is SESSIONS_SINCE_LAST_HEARTBEAT, and that row can
only be computed from (a) every fire we ever made and (b) every session the platform ever
started.  So the heartbeat is APPEND-ONLY -- one JSON line per fire, ``O_APPEND`` (atomic
for lines far below PIPE_BUF), in OUR file under a USER-OWNED directory, never a vendor
dir -- and the JOIN is the sensor.  The timestamp is carried, and it is not the verdict.

THE BOUND, STATED: a heartbeat proves the hook PROCESS ran.  It does not prove the
platform ACCEPTED its output (timeout, JSON rejection, spill).  Emitter, not receiver.
G0 (the canary) stays the proof of delivery.

FAIL-GRACEFUL BY CONTRACT: ``append_heartbeat`` NEVER raises.  The hook's oldest contract
is "session start never breaks", and a sensor that can break the thing it senses has
inverted the priority.  Every failure is returned as ``{"written": False, "reason": ...}``.

GPU: none.  NETWORK: none.  No ``CUDA_VISIBLE_DEVICES`` assignment of any kind.
"""
from __future__ import annotations

import errno
import glob
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 2          # 1 was the overwritten single-record file; RETIRED by execution
HEARTBEAT_DIR_ENV = "VOIDLOOM_HEARTBEAT_DIR"
VOIDLOOM_HOME_ENV = "VOIDLOOM_HOME"
PLATFORM_ENV = "VOIDLOOM_PLATFORM"
KNOWN_PLATFORMS = ("claude-code", "codex", "kimi-code")
# A heartbeat line is a few hundred bytes; PIPE_BUF (>= 512 on POSIX, 4096 on Linux) is
# the size below which an O_APPEND write of a regular file is not interleaved by another
# writer.  Lines are refused above this bound rather than torn.
MAX_LINE_BYTES = 4096


# ===========================================================================
# Paths — env first, relative-home second, never a vendor directory.
# ===========================================================================
def heartbeat_dir() -> str:
    override = os.environ.get(HEARTBEAT_DIR_ENV, "").strip()
    if override:
        return override
    vh = os.environ.get(VOIDLOOM_HOME_ENV, "").strip()
    base = Path(vh) if vh else (Path.home() / ".voidloom")
    return str(base / "heartbeat")


def heartbeat_path(platform: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in platform) or "unknown"
    return str(Path(heartbeat_dir()) / f"{safe}.jsonl")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    s = ts.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:                                      # ISO with offset, e.g. Codex's rollout meta
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ===========================================================================
# Platform derivation — from the payload or the env, NEVER typed.
# ===========================================================================
def derive_platform(payload: Optional[Dict[str, Any]],
                    env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """Return ``(platform, derivation)``.  ``derivation`` NAMES the evidence so a reader of
    the heartbeat can see how the platform was decided, and a wrong derivation is a
    visible row rather than a silent mislabel.

    Order, first hit wins:
      1. ``VOIDLOOM_PLATFORM`` in the env (explicit; used by fixtures and stated as such);
      2. the payload's ``transcript_path`` under the platform's own home
         (``CODEX_HOME``/``~/.codex`` -> codex; ``CLAUDE_CONFIG_DIR``/``~/.claude`` -> claude-code);
      3. ``CLAUDE_PROJECT_DIR`` in the env (Claude Code exports it to hook commands);
      4. the payload carrying BOTH ``model`` and ``permission_mode`` (Codex's session-start
         input schema REQUIRES both; weak on its own, so it ranks last);
      5. ``unknown`` — still recorded, so an unknown platform is a visible row.
    """
    env = dict(os.environ) if env is None else env
    payload = payload if isinstance(payload, dict) else {}
    explicit = (env.get(PLATFORM_ENV) or "").strip()
    if explicit:
        return explicit, f"env:{PLATFORM_ENV}"
    tp = payload.get("transcript_path")
    if isinstance(tp, str) and tp:
        tpn = tp.replace("\\", "/")
        codex_home = (env.get("CODEX_HOME") or "").strip() or str(Path.home() / ".codex")
        claude_home = (env.get("CLAUDE_CONFIG_DIR") or "").strip() or str(Path.home() / ".claude")
        for home, name in ((codex_home, "codex"), (claude_home, "claude-code")):
            h = home.replace("\\", "/").rstrip("/") + "/"
            if tpn.startswith(h):
                return name, f"payload:transcript_path under {home}"
        if "/.codex/" in tpn or "/rollout-" in tpn:
            return "codex", "payload:transcript_path shape (/.codex/ or rollout-)"
        if "/.claude/" in tpn:
            return "claude-code", "payload:transcript_path shape (/.claude/)"
        if "/.kimi-code/" in tpn:
            return "kimi-code", "payload:transcript_path shape (/.kimi-code/)"
    if env.get("CLAUDE_PROJECT_DIR"):
        return "claude-code", "env:CLAUDE_PROJECT_DIR"
    if "model" in payload and "permission_mode" in payload:
        return "codex", "payload:model+permission_mode (weak — Codex's schema requires both)"
    return "unknown", "none — no platform evidence in payload or env"


# ===========================================================================
# The writer — append one line, atomically, never raise.
# ===========================================================================
def _shared_writer():
    """THE ONE identity-gated writer (``voidloom_hooks_status.guarded_append_bytes``),
    imported lazily so this module stays importable on its own and the hook stays
    fail-graceful: an import failure is a returned reason, never a fallback to an
    unguarded ``os.open`` -- a fallback writer would be the second writer R6 forbids."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import voidloom_hooks_status as _hs  # stdlib-only; ~13 ms measured
    return _hs


def append_heartbeat(platform: str, *, session_id: Optional[str],
                     preamble_sha: str, derivation: str = "",
                     source: Optional[str] = None,
                     hook_path: Optional[str] = None,
                     ts: Optional[str] = None,
                     doctrine_sha: Optional[str] = None) -> Dict[str, Any]:
    """Append ONE line to the platform's heartbeat file.  NEVER raises.

    ``preamble_sha`` hashes the FULL emitted text (volatile body included) and so
    changes every fire — it is a per-fire fingerprint, never a build referent
    (DESSA, Council #12).  ``doctrine_sha`` is the sha of the frozen prefix: it
    changes only on a release, and it is the value the ARRIVAL line in the
    model's context also carries, so a platform transcript joins to this row.

    THE PROPERTY (R3 v3): each fire appends exactly one record and never rewrites a prior
    one.  The mechanism today is ``O_APPEND`` on a regular file (each ``write`` lands at the
    current end atomically for payloads below PIPE_BUF, so two sessions starting at once
    cannot interleave lines; no temp file, no rename, no read-modify-write).

    THE WRITE GOES THROUGH THE SAME IDENTITY-GATED WRITER AS INSTALL AND UNINSTALL
    (R3 / R6, iteration 2).  The first version of this function carried its own
    ``os.open`` with O_NOFOLLOW + S_ISREG and no link-count gate, and EXECUTED
    (reflector iter 1, P5) a hardlink of a user's file at the heartbeat path
    received our line -- the prior round's headline asymmetry, in a third writer.
    Now: symlink, hardlink, FIFO, directory, device, and a re-resolving parent
    chain are all refused BY THE SHARED GATE, with the reason returned (and
    printed to stderr by the hook), never raised into session start.
    """
    path = heartbeat_path(platform)
    rec = {"v": SCHEMA_VERSION, "ts": ts or utc_now(), "platform": platform,
           "session_id": session_id, "preamble_sha": preamble_sha,
           "doctrine_sha": doctrine_sha,
           "derivation": derivation, "source": source, "hook": hook_path}
    try:
        line = (json.dumps(rec, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    except Exception as e:                       # pragma: no cover - defensive
        return {"written": False, "path": path, "reason": f"serialize: {type(e).__name__}: {e}"}
    if len(line) > MAX_LINE_BYTES:
        return {"written": False, "path": path,
                "reason": f"line is {len(line)} bytes > {MAX_LINE_BYTES}; an O_APPEND "
                          f"write above PIPE_BUF is not atomic, so it is refused rather "
                          f"than torn"}
    try:
        _hs = _shared_writer()
    except Exception as e:
        return {"written": False, "path": path,
                "reason": f"the shared identity-gated writer is unavailable "
                          f"({type(e).__name__}: {e}); refusing to write with a second, "
                          f"ungated writer"}
    # THE DIRECTORY CHAIN IS GATED BEFORE ANYTHING IS CREATED (R3 / R9 v3, iteration
    # 3).  ``Path(...).mkdir(parents=True)`` under ``~/.voidloom -> <elsewhere>`` CREATED
    # ``<elsewhere>/heartbeat/`` inside the foreign target before the file gate
    # spoke (EXECUTED, reflector iter 2, P2).  A directory creation is a write; it
    # goes through the shared writer's directory face, which refuses a chain that
    # resolves elsewhere and creates nothing on refusal.
    try:
        _mk = _hs.guarded_mkdir_chain(str(Path(path).parent))
    except _hs.PathIsNotWhatWeNamed as e:
        return {"written": False, "path": path, "refused": True,
                "refused_by": getattr(e, "refused_by", None),
                "reason": f"REFUSED by the identity gate (directory chain): {e}"}
    except OSError as e:
        return {"written": False, "path": path, "reason": f"mkdir: {type(e).__name__}: {e}"}
    try:
        w = _hs.guarded_append_bytes(path, line)
    except _hs.PathIsNotWhatWeNamed as e:
        return {"written": False, "path": path, "refused": True,
                "refused_by": getattr(e, "refused_by", None),
                "reason": f"REFUSED by the identity gate: {e}"}
    except OSError as e:
        return {"written": False, "path": path,
                "reason": f"{type(e).__name__}: {e} (errno {getattr(e, 'errno', None)})"}
    except Exception as e:                       # pragma: no cover - defensive
        return {"written": False, "path": path, "reason": f"{type(e).__name__}: {e}"}
    if w.get("short_write"):
        return {"written": False, "path": path,
                "reason": f"short write: {w.get('bytes_written')} of {len(line)} bytes"}
    return {"written": True, "path": path, "bytes": w.get("bytes_written"), "record": rec,
            "writer": "voidloom_hooks_status.guarded_append_bytes",
            "link_count_at_write": w.get("link_count_at_write")}


# ===========================================================================
# The reader — every line we ever wrote, with the unreadable ones counted.
# ===========================================================================
def read_heartbeats(platform: str) -> Dict[str, Any]:
    path = heartbeat_path(platform)
    out: Dict[str, Any] = {"path": path, "present": False, "readable": None,
                           "fires": [], "fire_count": 0, "unparseable_lines": 0,
                           "first_ts": None, "last_ts": None, "session_ids": []}
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return out
    except OSError as e:
        out.update({"present": True, "readable": False, "fault": f"{type(e).__name__}: {e}"})
        return out
    out["present"] = True
    if not stat.S_ISREG(st.st_mode):
        out.update({"readable": False, "fault": "not a regular file"})
        return out
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        try:
            buf = b""
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                buf += chunk
        finally:
            os.close(fd)
    except OSError as e:
        out.update({"readable": False, "fault": f"{type(e).__name__}: {e}"})
        return out
    out["readable"] = True
    fires: List[Dict[str, Any]] = []
    bad = 0
    for raw in buf.decode("utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
            if not isinstance(rec, dict) or "ts" not in rec:
                bad += 1
                continue
            fires.append(rec)
        except ValueError:
            bad += 1
    out["fires"] = fires
    out["fire_count"] = len(fires)
    out["unparseable_lines"] = bad
    stamps = [f.get("ts") for f in fires if parse_ts(f.get("ts"))]
    out["first_ts"] = min(stamps) if stamps else None
    out["last_ts"] = max(stamps) if stamps else None
    out["session_ids"] = [f.get("session_id") for f in fires if f.get("session_id")]
    return out


# ===========================================================================
# The platform's OWN session records — the other side of the join.
# ===========================================================================
def _first_timestamp_in_jsonl(path: str, max_lines: int = 40,
                              fields: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The earliest ``timestamp`` in the first few lines, or None.  Reads a bounded
    prefix only: transcripts are large and the start time is in the head.

    ``fields``, when given, also receives the first ``entrypoint`` seen in the same
    head (Claude Code writes ``"cli"`` for an interactive session and ``"sdk-cli"``
    for a headless ``claude -p`` call), so the caller learns it from this one read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        head = os.read(fd, 64 * 1024)
    except OSError:
        return None
    finally:
        os.close(fd)
    best: Optional[str] = None
    for i, raw in enumerate(head.decode("utf-8", errors="replace").split("\n")):
        if i >= max_lines:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if fields is not None and "entrypoint" not in fields \
                and isinstance(rec.get("entrypoint"), str):
            fields["entrypoint"] = rec["entrypoint"]
        if fields is not None and "source" not in fields and rec.get("type") == "session_meta" \
                and isinstance(rec.get("payload"), dict) and "source" in rec["payload"]:
            fields["source"] = rec["payload"]["source"]
        ts = rec.get("timestamp")
        if isinstance(rec.get("payload"), dict) and not ts:
            ts = rec["payload"].get("timestamp")
        if isinstance(ts, str) and parse_ts(ts):
            if best is None or parse_ts(ts) < parse_ts(best):
                best = ts
    # A head line longer than the 64 KB read cannot be parsed as JSON; the
    # entrypoint is still in its first bytes (review F8: a false DEAD otherwise).
    if fields is not None and "entrypoint" not in fields:
        m = re.search(rb'"entrypoint"\s*:\s*"([^"\\]{1,64})"', head)
        if m:
            fields["entrypoint"] = m.group(1).decode("utf-8", "replace")
    # The same for Codex: a session_meta line past the read keeps its `source` near
    # the front (review 2, N17). Only the two machine values are recovered; anything
    # else stays unknown and therefore judged.
    if fields is not None and "source" not in fields:
        m = re.search(rb'"type"\s*:\s*"session_meta".*?"source"\s*:\s*("exec"|\{\s*"subagent")', head, re.S)
        if m:
            v = m.group(1).decode()
            fields["source"] = {"subagent": None} if v.startswith("{") else v.strip('"')
    return best


def codex_sessions_dir(env: Optional[Dict[str, str]] = None) -> str:
    env = dict(os.environ) if env is None else env
    home = (env.get("CODEX_HOME") or "").strip() or str(Path.home() / ".codex")
    return str(Path(home) / "sessions")


def claude_projects_dir(env: Optional[Dict[str, str]] = None) -> str:
    env = dict(os.environ) if env is None else env
    home = (env.get("CLAUDE_CONFIG_DIR") or "").strip() or str(Path.home() / ".claude")
    return str(Path(home) / "projects")


#: Sessions no person opened: Claude Code `claude -p` (the transcript's entrypoint),
#: and Codex `exec` runs and sub-agent threads (session_meta.payload.source). Any
#: other value, including one never seen on this machine, stays judged.
HEADLESS_ENTRYPOINTS = frozenset({"sdk-cli", "codex-exec", "codex-subagent"})


def list_platform_sessions(platform: str, env: Optional[Dict[str, str]] = None,
                           limit: int = 5000) -> Dict[str, Any]:
    """Every session the PLATFORM recorded, as ``{id, started_at, path}``.

    Codex: ``$CODEX_HOME/sessions/**/rollout-<ISO>-<uuid>.jsonl`` — the thread id is the
    last 36 characters of the stem; the start time is ``session_meta.payload.timestamp``
    on line 1 (fallback: the ISO stamp in the filename; last fallback: mtime).
    Claude Code: ``$CLAUDE_CONFIG_DIR/projects/<slug>/<uuid>.jsonl`` — the id is the stem;
    the start time is the earliest ``timestamp`` in the head (fallback: mtime).
    """
    env = dict(os.environ) if env is None else env
    sessions: List[Dict[str, Any]] = []
    if platform == "codex":
        root = codex_sessions_dir(env)
        pattern = os.path.join(root, "**", "rollout-*.jsonl")
    elif platform == "claude-code":
        root = claude_projects_dir(env)
        pattern = os.path.join(root, "*", "*.jsonl")
    else:
        return {"platform": platform, "root": None, "root_present": False,
                "sessions": [], "count": 0,
                "reason": "no session-record layout is known for this platform"}
    root_present = os.path.isdir(root)
    if not root_present:
        return {"platform": platform, "root": root, "root_present": False,
                "sessions": [], "count": 0}
    paths = sorted(glob.glob(pattern, recursive=True))
    if len(paths) > limit:
        paths = paths[-limit:]
    for p in paths:
        stem = Path(p).stem
        entrypoint = None
        if platform == "codex":
            sid = stem[-36:] if len(stem) >= 36 else stem
            head: Dict[str, Any] = {}
            started = _first_timestamp_in_jsonl(p, fields=head)
            # Codex's machine sessions: `codex exec` (source "exec") and the
            # sub-agent threads a session spawns (source {"subagent": ...}).
            _src = head.get("source")
            entrypoint = ("codex-exec" if _src == "exec" else
                          "codex-subagent" if isinstance(_src, dict) and "subagent" in _src else
                          (_src if isinstance(_src, str) else None))
            if not started:
                # rollout-YYYY-MM-DDTHH-MM-SS-<uuid>.  THE FILENAME STAMP IS LOCAL
                # TIME (P6; verified on this host: filename 11-02-19 beside a
                # session_meta.timestamp of 15:02:19Z under EDT).  A naive
                # strptime is local; .astimezone() converts it to UTC.  Suffixing
                # 'Z' to the local digits misplaced every fallback session by the
                # UTC offset: in UTC+ zones it read newer than the last fire
                # (false DEAD); in UTC- zones a real miss could hide.
                core = stem[len("rollout-"):-37] if stem.startswith("rollout-") else ""
                if len(core) == 19:
                    try:
                        started = (datetime.strptime(core, "%Y-%m-%dT%H-%M-%S")
                                   .astimezone(timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
                    except ValueError:
                        started = None
        else:
            sid = stem
            head: Dict[str, Any] = {}
            started = _first_timestamp_in_jsonl(p, fields=head)
            entrypoint = head.get("entrypoint")
        if not started:
            try:
                started = datetime.fromtimestamp(os.stat(p).st_mtime, tz=timezone.utc
                                                 ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            except OSError:
                started = None
        # A headless `claude -p` call (entrypoint exactly "sdk-cli") is the brain's own
        # overnight thinker or another program, not a person opening a session.
        sessions.append({"id": sid, "started_at": started, "path": p,
                         "entrypoint": entrypoint,
                         "headless": entrypoint in HEADLESS_ENTRYPOINTS})
    return {"platform": platform, "root": root, "root_present": True,
            "sessions": sessions, "count": len(sessions)}


# ===========================================================================
# THE JOIN — the sensor.
# ===========================================================================
STATES = ("NOT_INSTALLED", "INSTALLED_NEVER_FIRED", "DEAD", "LIVE")

APPROVAL_STEP = {
    "codex": ("Run `codex` interactively; on the 'Hooks need review' screen choose "
              "'Review hooks' and trust the Voidloom SessionStart entry (or type /hooks). "
              "Hooks are hash-pinned: an untrusted or modified hook is SKIPPED SILENTLY "
              "under `codex exec`. Then start a NEW session and re-run `voidloom doctor`."),
    "claude-code": ("Claude Code has no trust gate: the settings.json entry IS the consent. "
                    "If it is registered and no session has fired it, confirm the command "
                    "path exists and `python3` can run it, start a NEW session (hooks are "
                    "read at startup), and re-run `voidloom doctor`."),
}


JOIN_WINDOW = 10   # the last N platform sessions the verdict is computed over


def join_state(*, registered: bool, heartbeats: Dict[str, Any],
               sessions: Dict[str, Any], platform: str,
               registration_evidence: Optional[Dict[str, Any]] = None,
               window: int = JOIN_WINDOW,
               installed_at: Optional[str] = None) -> Dict[str, Any]:
    """Join {registered in the platform's config} x {fires} x {the platform's sessions}.

      NOT_INSTALLED          not registered (a heartbeat file from an earlier install may
                             exist; it is reported, it does not change the state)
      INSTALLED_NEVER_FIRED  registered, zero JOINED fires ever
      DEAD                   registered, and within the WINDOW (the platform's last N non-headless
                             sessions) at least one session carries no fire
      LIVE                   registered, and every session in the window carries a fire

    COUNCIL #12 (2026-09-15), three corrections to the first version:
      * THE WINDOW IS THE PLATFORM'S LAST N SESSIONS, NOT "SINCE THE LAST FIRE".  The
        first version looked only at sessions newer than the last fire, so a hook firing
        one session in five read LIVE whenever the most recent session fired — a clock
        wearing a counter's name, one level below the heartbeat that replaced the clock.
        Every rate printed here now carries the domain it was computed over (metrology).
      * A FIRE MUST JOIN A SESSION TO COUNT.  A heartbeat line whose session_id matches no
        platform session record (a planted id, a hook run by hand, a harness) is reported
        as UNJOINED and never advances the verdict.  The sensor may not vouch for itself.
      * A `source=compact` (or resume/clear) fire is a re-fire INSIDE a session the
        platform already recorded; it joins that session but never widens the window.

    The verdict is a function of session_ids, not of the clock.  ``last_ts`` is carried so
    a human can see "how long ago", but no branch below reads it as evidence.
    """
    fires_all = heartbeats.get("fires") or []
    # A line with source=adapter_off is the hook saying "I ran and injected
    # nothing on purpose" (a canary control, or an operator switch).  Those
    # sessions are SUPPRESSED: neither fired nor missed, reported apart.
    suppressed_ids = {f.get("session_id") for f in fires_all
                      if f.get("source") == "adapter_off" and f.get("session_id")}
    fires = [f for f in fires_all if f.get("source") != "adapter_off"]
    # HEADLESS sessions (2026-10-26).  A `claude -p` call records a transcript like
    # any session, but no person opened it: on the owner's desktop the two "missed"
    # sessions that turned the verdict DEAD were the brain's own overnight thinker,
    # and 821 of 849 transcripts were headless.  Headless sessions never enter the
    # window, fired or not: a fired one let ten thinker calls push a person's missed
    # session out of the last-10 window and read LIVE (Council, VAEL's planted case).
    # They are counted and reported apart; nothing is hidden.
    _fired_any = {f.get("session_id") for f in fires if f.get("session_id")}
    headless_ids = {s.get("id") for s in (sessions.get("sessions") or [])
                    if s.get("headless")}
    headless_fired = len(headless_ids & _fired_any)
    allsess = [s for s in (sessions.get("sessions") or [])
               if s.get("id") not in suppressed_ids and s.get("id") not in headless_ids]
    # Every session the platform RECORDED is known, headless included: a fire from
    # a headless session joins a real session and is never an "unjoined" hand run.
    # Only the WINDOW (allsess, below) leaves headless sessions out.
    known_ids = {s.get("id") for s in (sessions.get("sessions") or [])
                 if s.get("id") and s.get("id") not in suppressed_ids}
    fired_ids_raw = {f.get("session_id") for f in fires if f.get("session_id")}
    joined_ids = {i for i in fired_ids_raw if i in known_ids}
    unjoined_ids = sorted(i for i in fired_ids_raw if i not in known_ids)
    startup_fires = [f for f in fires if (f.get("source") or "startup") == "startup"]
    last_ts = heartbeats.get("last_ts")
    out: Dict[str, Any] = {
        "platform": platform, "registered": bool(registered),
        "registration_evidence": registration_evidence or {},
        "heartbeat_path": heartbeats.get("path"),
        "heartbeat_present": heartbeats.get("present"),
        "heartbeat_readable": heartbeats.get("readable"),
        "fire_count": heartbeats.get("fire_count", 0),
        "fire_count_joined": len(joined_ids),
        "fire_count_unjoined": len(unjoined_ids),
        "unjoined_session_ids": unjoined_ids[-10:],
        "fire_count_startup": len(startup_fires),
        "suppressed_sessions": sorted(suppressed_ids)[-10:],
        "suppressed_count": len(suppressed_ids),
        "headless_sessions_not_judged": len(headless_ids),
        "headless_sessions_fired": headless_fired,
        "unparseable_heartbeat_lines": heartbeats.get("unparseable_lines", 0),
        "first_ts": heartbeats.get("first_ts"), "last_ts": last_ts,
        "session_records_root": sessions.get("root"),
        "session_records_present": sessions.get("root_present"),
        "session_count_total": sessions.get("count", 0),
        "window": {"declared": int(window), "sessions": 0, "fired": 0, "missed": 0,
                   "domain": f"the platform's last {int(window)} recorded sessions a person "
                             f"opened (headless sessions not judged)"},
        "sessions_in_window": [], "missed_sessions": [], "missed_count": 0,
        "join_key": "heartbeat.session_id == the platform's session id "
                    "(Codex: rollout filename uuid == session_meta.payload.session_id; "
                    "Claude Code: transcript filename stem)",
        "bound": ("a heartbeat proves the hook PROCESS ran, not that the platform ACCEPTED "
                  "its output — emitter, not receiver; G0 is the proof of delivery"),
    }
    if not registered:
        out["state"] = "NOT_INSTALLED"
        out["reason"] = "the hook is not registered in this platform's configuration"
        if heartbeats.get("present"):
            out["note"] = ("a heartbeat file exists from an earlier registration; it does "
                           "not make the hook installed")
        return out
    # A fire counts toward "the hook reaches people" only when it joined a session
    # a PERSON opened (review F1: with only headless fires and no install receipt
    # the window was empty and the verdict read LIVE over "0 of 0").
    person_joined = joined_ids - headless_ids
    if not person_joined:
        out["state"] = "INSTALLED_NEVER_FIRED"
        out["approval_step"] = APPROVAL_STEP.get(platform, "consult the platform's hook "
                                                            "approval procedure")
        out["missed_sessions"] = [s["id"] for s in allsess][-20:]
        out["missed_count"] = len(allsess)
        out["reason"] = (f"registered; {len(fires)} heartbeat line(s) of which "
                         f"{len(joined_ids)} join a platform session; the platform "
                         f"recorded {len(allsess)} session(s), none of which fired the hook")
        if unjoined_ids:
            out["note"] = (f"{len(unjoined_ids)} heartbeat line(s) name a session id the "
                           f"platform never recorded — a hand run or a harness, not a fire")
        if joined_ids:
            out["note"] = ((out.get("note", "") + "; ") if out.get("note") else "") + (
                f"{len(joined_ids)} fire(s) joined headless session(s) only; no session "
                f"a person opened has ever fired the hook")
        return out
    # THE DOMAIN'S LOWER BOUND IS INSTALL, NOT THE FIRST FIRE.  (C6.)
    #
    # A session that started before the hook was registered cannot be expected to
    # carry a fire, and counting it as a miss would read every fresh install as
    # DEAD for N sessions.  That much was always right.  But the bound used to be
    # the FIRST JOINED FIRE, which over-corrects straight past the event this rung
    # exists to detect: on Codex a hash-pinned hook is SKIPPED SILENTLY until the
    # user trusts it, so sessions accumulate carrying no fire -- and the moment one
    # later session finally fires, every one of those is reclassified as "before
    # the first fire" and excluded.  Measured on the shipped function: registered
    # 100 minutes ago, two sessions since then with no fire, one fire 5 minutes
    # ago -> state LIVE, missed_count 0.  The two silent skips vanished.
    #
    # `installed_at` comes from the receipt -- the moment we KNOW it was
    # registered.  Before it: not judgeable, and reported as such.  After it and
    # not fired: that is the miss.  With no receipt the first-fire bound remains
    # as the fallback, and the domain string says which one is in force, because a
    # window that does not name its own lower bound is unreadable evidence.
    joined_fire_ts = [parse_ts(f.get("ts")) for f in fires
                      if f.get("session_id") in person_joined and parse_ts(f.get("ts"))]
    first_joined_dt = min(joined_fire_ts) if joined_fire_ts else None
    out["first_joined_fire_ts"] = first_joined_dt.isoformat() if first_joined_dt else None
    installed_dt = parse_ts(installed_at) if installed_at else None
    out["installed_at"] = installed_dt.isoformat() if installed_dt else None
    lower_dt = installed_dt if installed_dt is not None else first_joined_dt
    out["window_anchor"] = ("installed_at" if installed_dt is not None
                            else ("first_joined_fire" if first_joined_dt else "none"))
    def _key(s: Dict[str, Any]):
        d = parse_ts(s.get("started_at"))
        return (d is None, d or datetime.min.replace(tzinfo=timezone.utc))
    ordered = sorted(allsess, key=_key)
    eligible = []
    before = 0
    for s_ in ordered:
        sdt = parse_ts(s_.get("started_at"))
        # a session whose start we cannot date is judged (a missing timestamp must not
        # hide a miss); one that provably predates the lower bound is not
        if lower_dt is not None and sdt is not None and sdt < lower_dt \
                and s_.get("id") not in joined_ids:
            before += 1
            continue
        eligible.append(s_)
    out["sessions_before_window"] = before
    # kept under its old name so a reader of an older ledger row still resolves it
    out["sessions_before_first_fire"] = before
    # Informational only (the pre-Council-#12 view): sessions newer than the LAST
    # fire.  No verdict reads it; it stays so a reader can see both framings.
    last_dt = parse_ts(last_ts) if last_ts else None
    _newer = [s_ for s_ in ordered
              if last_dt is not None and parse_ts(s_.get("started_at")) is not None
              and parse_ts(s_.get("started_at")) > last_dt]
    out["sessions_since_last_fire"] = [s_["id"] for s_ in _newer][-20:]
    out["sessions_since_last_fire_count"] = len(_newer)
    win = eligible[-int(window):] if window and window > 0 else eligible
    # The window NAMES ITS OWN LOWER BOUND and which event set it -- a window that
    # does not say what it starts from cannot be read as evidence.
    _bound_ts = (out["installed_at"] if out["window_anchor"] == "installed_at"
                 else out["first_joined_fire_ts"])
    _bound_word = ("the hook's recorded install" if out["window_anchor"] == "installed_at"
                   else "the first joined fire (no receipt named an install time)")
    out["window"]["anchor"] = out["window_anchor"]
    out["window"]["domain"] = (f"the platform's last {int(window)} recorded sessions a person "
                               f"opened ({len(headless_ids)} headless session(s) not judged) that "
                               f"started at or after {_bound_word} ({_bound_ts}); "
                               f"{before} earlier session(s) not judged")
    fired_in_win = [s for s in win if s.get("id") in joined_ids]
    missed = [s for s in win if s.get("id") not in joined_ids]
    out["window"].update({"sessions": len(win), "fired": len(fired_in_win), "missed": len(missed)})
    out["sessions_in_window"] = [s["id"] for s in win]
    out["missed_sessions"] = [s["id"] for s in missed][-20:]
    out["missed_count"] = len(missed)
    dom = (f"of the platform's last {len(win)} recorded session(s) a person opened "
           f"({len(headless_ids)} headless not judged)")
    if missed:
        out["state"] = "DEAD"
        out["approval_step"] = APPROVAL_STEP.get(platform)
        out["reason"] = (f"{len(missed)} {dom} carry no fire ({len(fired_in_win)} do); "
                         f"last fire {last_ts}")
    else:
        out["state"] = "LIVE"
        out["reason"] = (f"every one {dom} carries a fire ({len(fired_in_win)} of "
                         f"{len(win)}); {len(joined_ids)} joined fire(s) in total; "
                         f"last fire {last_ts}")
    if unjoined_ids:
        out["note"] = (f"{len(unjoined_ids)} heartbeat line(s) name a session id the platform "
                       f"never recorded and were NOT counted")
    return out


__all__ = ["SCHEMA_VERSION", "HEARTBEAT_DIR_ENV", "PLATFORM_ENV", "KNOWN_PLATFORMS",
           "STATES", "APPROVAL_STEP", "heartbeat_dir", "heartbeat_path", "utc_now",
           "parse_ts", "derive_platform", "append_heartbeat", "read_heartbeats",
           "list_platform_sessions", "join_state", "JOIN_WINDOW", "codex_sessions_dir",
           "claude_projects_dir"]

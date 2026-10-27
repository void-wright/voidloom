#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_canary.py — the G0/G1/G2/G3 harness. THE CONTROL RUNS FIRST.

WHY THIS FILE EXISTS, STATED AS THE FAILURE IT PREVENTS
--------------------------------------------------------
On 2026-09-08 this project reported "our unmodified hook works on Codex,
PROVEN" on the strength of a run in which the model repeated our brain path,
our recall verb and our persona name.  It was CONFOUNDED: the platform's config
carried ``project_doc_fallback_filenames = ["CLAUDE.md"]`` and the project's
CLAUDE.md — 47,991 bytes — contained every answer that was asked for.  The model
could have answered entirely from that file with our adapter absent.

TWO canaries were run that day.  The rigorous one used a nonce that existed
nowhere on disk.  The legible one asked about content.  THE DECORATIVE ONE WAS
PROMOTED, because it read like the thing we wanted to be true.  That is the
selection bias the whole discipline exists to stop, committed while writing the
document naming the discipline.

So the rules below are not style.  Each is a specific past failure turned into
a code path that cannot be skipped:

  1. THE CONTROL RUNS FIRST, AND IT IS MANDATORY.  ``grade()`` takes the control
     as a REQUIRED argument and raises if it is missing.  A treatment verdict
     cannot be computed without one — not "should not", cannot.
  2. THE CONTROL MUST RETURN THE NULL ANSWER.  Adapter off, clean directory, a
     nonce that exists nowhere.  If the control returns anything else, the
     harness is confounded and EVERY green in the matrix is uninterpretable.
  3. A QUESTION ANSWERABLE FROM ANY OTHER FILE IN SCOPE MEASURES THE FILE
     SYSTEM, NOT THE MECHANISM.  ``confound_scan`` enumerates every context
     source the platform would actually load — including config-driven fallbacks
     — and greps each for the answer before the run is allowed to count.
  4. OBSERVE WHAT THE PLATFORM RECEIVED, NOT WHAT WE EMITTED.  A previous gate
     hashed a file on disk, printed DISPATCH VALID for the life of a project,
     and measured 0 of 156 dispatches actually carrying the payload.  G0 reads
     the PLATFORM'S OWN TRANSCRIPT.
  5. REPORT THE OBSERVATION COUNT; RETURN UNMEASURABLE AT ZERO.  A pass over an
     empty observation set is not a pass.  OBEYED is stochastic and is reported
     as a RATE with an n, never as a boolean: the first report of it said
     "OBEYED is real" off n=1, which is a pulse, not a heartbeat.
  6. THE FOURTH RUNG.  On a platform that SELECTS its instruction file, our
     block can ARRIVE AND BE OUTRANKED.  A canary run in an empty directory
     never tests the case that actually breaks, so G3 requires a COMPETING file.

HONEST BOUND ON WHAT ANY OF THIS PROVES
---------------------------------------
Verbalization-based checks verify RECEIPT AND ACKNOWLEDGMENT, not subsequent
adherence: instruction-consistency correlates with instruction-following at only
r=0.57, so acknowledgment explains roughly a third of adherence variance.  G0 and
G1 are plumbing.  G2 is the only rung that tests the PURPOSE, and it is the
cheapest thing in this file to run and the one most often skipped.

GPU: every subprocess this harness spawns receives a FORCED
``CUDA_VISIBLE_DEVICES=1`` in its environment — a forced assignment, never
``os.environ.setdefault``, which yields to an existing OR EMPTY value.  GPU 0
drives the operator's monitor and a job that takes it evicts the desktop
silently while producing perfectly correct output.

USAGE
-----
    voidloom_canary.py --replay --platform codex        # zero API cost
    voidloom_canary.py --replay --all --json
    voidloom_canary.py --gate G1 --runner './run.sh'    # real model calls, opt-in
    voidloom_canary.py --self-check                     # the refusals, executed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HARNESS_VERSION = 1

# Siblings resolve from THIS file's directory, not from the caller's cwd — a
# harness that only imports its own peers when invoked from one directory is a
# harness that silently degrades to a different code path elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The platform table and the proof ledger live in the RESOLVER's module and are
# imported, never re-declared. A design whose core move is to multiply readers
# cannot afford a second copy of the thing every reader must agree on.
try:
    import voidloom_hooks_status as _hs   # type: ignore
    _HS_ERR = None
except Exception as _e:                    # pragma: no cover - import guard
    _hs = None
    _HS_ERR = f"{type(_e).__name__}: {_e}"

# The preamble marker that identifies OUR block inside someone else's
# transcript. Derived from the emitter when importable so it cannot drift.
try:
    import voidloom_preamble as _vp        # type: ignore
    _VP_ERR = None
except Exception as _e:                    # pragma: no cover - import guard
    _vp = None
    _VP_ERR = f"{type(_e).__name__}: {_e}"

# The doctrine banner's presence marker (a file MAY carry it) -- READ from the one constant the
# emitter uses (voidloom_persona.BRAIN_LABEL, #26), never typed twice. Transcripts written by an
# install before 2026-10-13 carry the legacy label: RECOGNISED, never emitted.
try:
    from voidloom_persona import BRAIN_LABEL as PREAMBLE_MARKER, LEGACY_BRAIN_LABELS as LEGACY_PREAMBLE_MARKERS
except Exception:                          # pragma: no cover - import guard
    PREAMBLE_MARKER = "Voidloom memory brain"
    LEGACY_PREAMBLE_MARKERS = ("wizard-library brain",)


def carries_preamble_marker(text: str) -> bool:
    """Our banner, current or legacy, is in ``text``."""
    return PREAMBLE_MARKER in text or any(m in text for m in LEGACY_PREAMBLE_MARKERS)
ARRIVAL_MARKER = "=== VOIDLOOM HOOK FIRED"          # the hook-only line (no file can)


def read_text_bounded(path: str, errors: str = "replace", follow: bool = False) -> str:
    """Read a path WITHOUT EVER BLOCKING on it (CLASS-NR; the sibling of
    ``voidloom_hooks_status.read_bytes_if_regular`` and ``voidloom_preamble.read_text_bounded``).

    A FIFO at any path this organ reads -- a context source in the user's home, a
    rollout under CODEX_HOME, the hook, a file in the run scope -- would block
    ``Path.read_text`` forever (kernel ``wait_for_partner``).  lstat -> S_ISREG or
    refuse -> open O_RDONLY|O_NONBLOCK (O_NOFOLLOW unless ``follow``) -> fstat
    S_ISREG again -> read.  Raises OSError(ENOTSUP) for a non-regular object so
    every existing ``except OSError`` handler treats it as unreadable.
    """
    import errno as _errno
    import stat as _stat
    st = os.stat(path) if follow else os.lstat(path)
    if not _stat.S_ISREG(st.st_mode):
        raise OSError(_errno.ENOTSUP, f"not a regular file; refusing to read {path}")
    flags = os.O_RDONLY | os.O_NONBLOCK | (0 if follow else getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        if not _stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(_errno.ENOTSUP, f"not a regular file at open; refusing to read {path}")
        buf = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            buf += chunk
    finally:
        os.close(fd)
    return buf.decode("utf-8", errors=errors)


def fixture_root() -> str:
    """Where this file's filesystem fixtures go: the resolver's fixture root when
    importable (ONE root for the shipped set), else the same rule re-stated: a
    directory beside the shipped file on the DEPLOYMENT filesystem, or
    $VOIDLOOM_SELFCHECK_FIXTURE_DIR.  Never tempfile's default (tmpfs here, where
    the inode-reuse shape cannot occur).  R10: a bare mkdtemp() is a FAIL."""
    if _hs is not None and hasattr(_hs, "fixture_root"):
        return _hs.fixture_root()
    override = os.environ.get("VOIDLOOM_SELFCHECK_FIXTURE_DIR")
    root = (Path(override) if override
            else Path(__file__).resolve().parent / ".vl_selfcheck_fixtures")
    root.mkdir(parents=True, exist_ok=True)
    return str(root)

def control_root() -> str:
    """Where the CONTROL's clean directory goes — and it is NOT fixture_root().

    W8 (Council #12, 2026-09-15), observed in Codex's own rollout: the adapter-OFF
    control, with no hook and no notice anywhere, was asked about a brain-only
    identifier, grepped its cwd, walked UP the path — because fixture_root() sits
    at <brain>/organs/.vl_selfcheck_fixtures/, INSIDE the brain's tree — found
    claude_memory.db, opened it with `sqlite3 -readonly`, and answered.  A
    control whose working directory names the brain in its own path is a fixture
    that hands the model the answer.  The control therefore runs under a root
    OUTSIDE any directory that contains the brain: $VOIDLOOM_CANARY_CONTROL_DIR,
    else ~/.voidloom/canary_control (the deployment filesystem, never tmpfs).

    THE BOUND THIS DOES NOT LIFT: a read-only sandbox can still read the whole
    disk.  A clean control proves the answer was not reachable from the
    platform's context, its cwd, or the cwd's ancestors — not that it is
    undiscoverable by an exhaustive search of the machine.  Say so in the row."""
    override = os.environ.get("VOIDLOOM_CANARY_CONTROL_DIR")
    root = Path(override) if override else Path.home() / ".voidloom" / "canary_control"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


# --- verdict ladder --------------------------------------------------------
NOT_INSTALLED = "NOT_INSTALLED"
UNMEASURABLE = "UNMEASURABLE"
CONFOUNDED = "CONFOUNDED"
CONTROL_MISSING = "CONTROL_MISSING"
ABSENT = "ABSENT"
PRESENT = "PRESENT"          # G0
PROVEN = "PROVEN"            # G1, control mandatory
OBEYED = "OBEYED"            # G2, control mandatory
NOT_CONTRADICTED = "NOT_CONTRADICTED"   # G3
NOT_RUN = "NOT_RUN"

GATES = {
    "GB": "RECEIVED-BYTES — deterministic, zero API cost: what the platform "
          "ACTUALLY received, against what the emitter and a patched hook emit",
    "G0": "PRESENT — a nonce appears in the PLATFORM'S OWN transcript "
          "(deterministic, zero API cost, no model call)",
    "G1": "PROVEN — the nonce is echoed in model output, PAIRED with an "
          "adapter-OFF control that MUST return the null answer",
    "G2": "OBEYED — the model supplies a fact that exists ONLY in a memory row "
          "and nowhere on disk in scope, WITHOUT being told to recall",
    "G3": "NOT_CONTRADICTED — on a SELECTS platform, the block survives a "
          "directory that holds a COMPETING instruction file",
}

NULL_ANSWER_PATTERNS = [
    r"\bi\s+don'?t\s+know\b", r"\bnothing\s+injected\b", r"\bno\s+context\b",
    r"\bnot\s+provided\b", r"\bnone\b", r"\bunknown\b", r"\bno\s+passphrase\b",
    r"\bi\s+(?:was\s+not|wasn'?t)\s+given\b", r"\bcannot\s+find\b",
    # W8 (Council #12): with the adapter OFF the activation notice in the
    # platform's global AGENTS.md fires ("the Voidloom memory hook is not active
    # in this session") and the model may answer with it alone.  That sentence
    # IS the null answer for a passphrase-in-context question — the platform
    # told us, in its own words, that nothing was injected.
    r"\bmemory\s+hook\s+is\s+not\s+active\b",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_nonce() -> str:
    return "VLCANARY_" + hashlib.sha256(os.urandom(16)).hexdigest()[:16].upper()


def child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for ANY subprocess this harness spawns.

    ``CUDA_VISIBLE_DEVICES`` is FORCE-ASSIGNED to "1".  ``setdefault`` is
    forbidden here and everywhere: it yields to an existing OR EMPTY value, and
    an empty value is exactly what a stripped test environment supplies — at
    which point the job silently takes GPU 0, which drives the operator's
    monitor, and evicts the desktop while producing correct output.
    """
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "1"
    if extra:
        env.update(extra)
    return env


# ===========================================================================
# Observations
# ===========================================================================
@dataclass
class Observation:
    gate: str
    platform: str
    leg: str                 # 'control' | 'treatment'
    source: str              # the file/stream actually read
    source_kind: str         # 'transcript' | 'model_stdout'
    nonce: Optional[str]
    found: bool
    attributed_to_adapter: Optional[bool]
    detail: Dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=utc_now)


@dataclass
class Confound:
    source: str
    reason: str
    matched: List[str]


# ===========================================================================
# Confound scanning — rule 3
# ===========================================================================
CONTEXT_FILENAMES = ["AGENTS.md", "CLAUDE.md", ".cursorrules", "GEMINI.md",
                     "QWEN.md", ".github/copilot-instructions.md",
                     ".openhands/microagents", "CONVENTIONS.md"]


def context_sources_in_scope(cwd: str, platform_id: str) -> List[str]:
    """Every file a platform could load as an instruction document HERE.

    Includes config-driven fallbacks, which is the specific thing that
    confounded the 2026-09-08 run: the answer was not in AGENTS.md, it was in
    the file a CONFIG LINE made stand in for AGENTS.md.
    """
    out: List[str] = []
    base = Path(cwd)
    for name in CONTEXT_FILENAMES:
        p = base / name
        if p.exists():
            out.append(str(p))
    for name in ("AGENTS.md", "CLAUDE.md"):
        p = Path.home() / ".agents" / name
        if p.exists():
            out.append(str(p))
    if platform_id == "codex":
        cfg = Path.home() / ".codex" / "config.toml"
        if cfg.exists():
            try:
                txt = read_text_bounded(str(cfg), errors="strict")
            except OSError:
                txt = ""
            m = re.search(r"^\s*project_doc_fallback_filenames\s*=\s*\[([^\]]*)\]",
                          txt, flags=re.M)
            if m:
                for nm in re.findall(r'"([^"]+)"', m.group(1)):
                    p = base / nm
                    if p.exists() and str(p) not in out:
                        out.append(str(p))
    return out


def confound_scan(cwd: str, platform_id: str,
                  answers: Sequence[str]) -> List[Confound]:
    """Any context source containing an answer makes the run uninterpretable."""
    hits: List[Confound] = []
    for src in context_sources_in_scope(cwd, platform_id):
        try:
            txt = read_text_bounded(str(src), errors="replace")
        except OSError as e:
            hits.append(Confound(src, f"unreadable ({type(e).__name__})", []))
            continue
        matched = [a for a in answers if a and a.lower() in txt.lower()]
        if matched:
            hits.append(Confound(
                src, "context source in scope contains the expected answer",
                matched))
    return hits


def disk_scope_scan(root: str, needle: str,
                    max_files: int = 4000) -> Dict[str, Any]:
    """Does ``needle`` exist anywhere in the run scope on disk?

    G2's question must be answerable ONLY from a memory row. If the token is
    also on disk here, a hit measures the filesystem — and the measured miss in
    the one failing trial of the 4-of-5 run was exactly that shape: an
    identifier-looking query sent the model to ``rg`` instead of to the brain.

    RETURNS COMPLETENESS, NOT JUST HITS.  This used to return a bare list, so a
    scan that hit ``max_files`` and a scan that read every file in scope were
    INDISTINGUISHABLE to the caller — and ``pick_brain_only_fact`` read the
    empty list as proof the token is absent.  A TRUNCATED NEGATIVE IS NOT A
    NEGATIVE: if the token sits beyond the cap, G2 measures the file system,
    which is the exact confound this module exists to refuse.
    """
    found: List[str] = []
    n = 0
    truncated = False
    for p in Path(root).rglob("*"):
        if n >= max_files:
            truncated = True
            break
        if not p.is_file():
            continue
        n += 1
        try:
            if needle in read_text_bounded(str(p), errors="replace", follow=True):
                found.append(str(p))
        except OSError:
            continue
    return {"found": found, "files_scanned": n, "max_files": max_files,
            "truncated": truncated, "complete": not truncated}


# ===========================================================================
# G2 question selection — a fact only the brain holds
# ===========================================================================
def pick_brain_only_fact(db: str, scope: str,
                         candidates: int = 12,
                         scan_max_files: int = 4000) -> Dict[str, Any]:
    """Choose a memory whose distinctive token appears NOWHERE on disk in scope.

    READ-ONLY over the brain (``mode=ro``).  Reports how many candidates were
    REJECTED, because a selection process that never rejects anything is not a
    selection process.
    """
    out: Dict[str, Any] = {"chosen": None, "considered": 0, "rejected": [],
                           "error": None}
    if not Path(db).exists():
        out["error"] = f"brain not present at {db}"
        return out
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:
        rows = conn.execute(
            "SELECT id, content FROM memories "
            "WHERE is_archived=0 AND COALESCE(untrusted_source,0)=0 "
            "  AND content IS NOT NULL AND LENGTH(content) > 200 "
            # A TOTAL ORDER: ties on (importance, created_at) otherwise make the G2
            # candidate list depend on SQLite's choice, so the same brain could
            # yield a different question on two runs of the same harness.
            "ORDER BY importance DESC, created_at DESC, id DESC LIMIT ?",
            (candidates,)).fetchall()
    except sqlite3.Error as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass

    for mid, content in rows:
        out["considered"] += 1
        m = re.match(r"^\s*(?:ANCHOR\s+)?([a-z][a-z0-9_]{12,})\b", content or "")
        token = m.group(1) if m else None
        if not token:
            out["rejected"].append({"id": mid, "why": "no distinctive anchor token"})
            continue
        scan = disk_scope_scan(scope, token, max_files=scan_max_files)
        if scan["found"]:
            out["rejected"].append({"id": mid, "token": token,
                                    "why": "token also on disk in scope",
                                    "where": scan["found"][:3]})
            continue
        if scan["truncated"]:
            # A TRUNCATED NEGATIVE IS NOT A NEGATIVE.  Selecting a G2 question
            # on a scan that stopped early means the question may be greppable
            # and G2 would measure the file system.
            out["rejected"].append({"id": mid, "token": token,
                                    "why": ("scope scan truncated at "
                                            f"{scan['files_scanned']} files; "
                                            "cannot certify the token is absent"),
                                    "scan": scan})
            continue
        out["chosen"] = {"memory_id": mid, "token": token,
                         "scope_files_scanned": scan["files_scanned"],
                         "scope_scan_complete": scan["complete"],
                         "excerpt": (content or "")[:220]}
        return out
    return out


# ===========================================================================
# G0 — transcript readers.  What the PLATFORM RECEIVED, not what we emitted.
# ===========================================================================
def _codex_rollouts(since_days: Optional[int] = None) -> List[str]:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return []
    return [str(p) for p in sorted(root.rglob("rollout-*.jsonl"))]


def _claude_transcripts() -> List[str]:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return []
    return [str(p) for p in sorted(root.rglob("*.jsonl"))]


#   ATTRIBUTION IS PER-RECORD, NEVER PER-FILE.
#
#   The first version of these scanners set a FILE-level "marker present" flag
#   and a FILE-level "nonce present" flag and called their conjunction
#   attribution.  Run against this machine it returned PRESENT/attributed for
#   Claude Code — from a transcript in which the nonce appeared only because the
#   canary's own author had TYPED it while investigating, in ordinary user and
#   assistant turns, while the marker appeared separately in a real hook record.
#   Two true facts about one file, joined into a false claim about a record.
#   That is the same defect as a count derived from a different object than the
#   verdict, and it produced a green that was worth nothing.
#
#   So: an observation is admitted ONLY from a record that IS the injected
#   payload — the channel the adapter writes to — and attribution requires the
#   nonce and the marker to be in THE SAME injected payload.  A nonce in a user
#   turn, an assistant turn, or a tool result is not an observation of anything.


def _codex_injected_payloads(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, Optional[str]]:
    """Return (injected_payloads, model_outputs, record_count, error).

    Injected = the developer/system records Codex writes the adapter's text into.
    """
    injected: List[Dict[str, Any]] = []
    outputs: List[Dict[str, Any]] = []
    n = 0
    try:
        lines = read_text_bounded(path, errors="replace").splitlines()
    except OSError as e:
        return [], [], 0, f"{type(e).__name__}: {e}"
    for line in lines:
        if not line.strip():
            continue
        n += 1
        try:
            d = json.loads(line)
        except ValueError:
            continue
        pay = d.get("payload") or {}
        if not isinstance(pay, dict):
            continue
        ts = d.get("timestamp")
        texts = [c.get("text") for c in (pay.get("content") or [])
                 if isinstance(c, dict) and isinstance(c.get("text"), str)]
        item = pay.get("item")
        item_texts = []
        if isinstance(item, dict):
            item_texts = [c.get("text") for c in (item.get("content") or [])
                          if isinstance(c, dict) and isinstance(c.get("text"), str)]
        role = pay.get("role")
        if role in ("developer", "system") and texts:
            injected.append({"text": "\n".join(texts), "role": role,
                             "timestamp": ts})
        if role == "assistant" and texts:
            outputs.append({"text": "\n".join(texts), "timestamp": ts})
        elif item_texts and isinstance(item, dict) and item.get("type") == "AgentMessage":
            outputs.append({"text": "\n".join(item_texts), "timestamp": ts})
    return injected, outputs, n, None


def _claude_injected_payloads(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, Optional[str]]:
    """Claude Code writes the hook's ACTUAL STDOUT into an attachment record:
    ``type='attachment'``, ``attachment.type='hook_success'``,
    ``attachment.hookEvent='SessionStart'``, ``attachment.stdout`` = the JSON
    envelope the platform received.

    THIS IS THE POINT OF G0: it reads what the platform RECEIVED, parsed out of
    the platform's own log, rather than what our emitter would produce if asked
    again.  A previous gate in this project hashed the file on disk, printed
    DISPATCH VALID for the life of the project, and measured 0 of 156 dispatches
    actually carrying the payload.
    """
    injected: List[Dict[str, Any]] = []
    outputs: List[Dict[str, Any]] = []
    n = 0
    try:
        lines = read_text_bounded(path, errors="replace").splitlines()
    except OSError as e:
        return [], [], 0, f"{type(e).__name__}: {e}"
    for line in lines:
        if not line.strip():
            continue
        n += 1
        if '"hook_success"' not in line and '"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ts = d.get("timestamp")
        if d.get("type") == "attachment":
            att = d.get("attachment") or {}
            if (att.get("type") == "hook_success"
                    and att.get("hookEvent") == "SessionStart"):
                raw = att.get("stdout") or ""
                ctx = ""
                try:
                    env = json.loads(raw)
                    ctx = ((env.get("hookSpecificOutput") or {})
                           .get("additionalContext") or "")
                except ValueError:
                    ctx = raw
                if ctx:
                    injected.append({"text": ctx, "role": "SessionStart-hook",
                                     "timestamp": ts,
                                     "hook_name": att.get("hookName")})
        elif d.get("type") == "assistant":
            msg = d.get("message") or {}
            texts = [c.get("text") for c in (msg.get("content") or [])
                     if isinstance(c, dict) and isinstance(c.get("text"), str)]
            if texts:
                outputs.append({"text": "\n".join(texts), "timestamp": ts})
    return injected, outputs, n, None


def _scan_with(extractor, path: str, nonce: str,
               minted_at: Optional[str]) -> Dict[str, Any]:
    injected, outputs, n, err = extractor(path)
    res: Dict[str, Any] = {
        "path": path, "records": n, "error": err,
        "injected_payloads": len(injected), "model_outputs": len(outputs),
        "in_injected_record": False, "in_model_output": False,
        "attributed_same_record": False, "pre_mint_rejected": 0,
        "matched_roles": [],
    }
    if err:
        return res
    for rec in injected:
        if not nonce or nonce not in rec["text"]:
            continue
        # THE MINT GUARD. A nonce that existed BEFORE this run was minted cannot
        # be evidence FOR this run. Replaying a historical token without it is
        # how a canary ends up measuring its own author's typing.
        if minted_at and rec.get("timestamp") and str(rec["timestamp"]) < minted_at:
            res["pre_mint_rejected"] += 1
            continue
        res["in_injected_record"] = True
        res["matched_roles"].append(rec.get("role"))
        if carries_preamble_marker(rec["text"]):
            res["attributed_same_record"] = True
    for rec in outputs:
        if nonce and nonce in rec["text"]:
            if minted_at and rec.get("timestamp") and str(rec["timestamp"]) < minted_at:
                res["pre_mint_rejected"] += 1
                continue
            res["in_model_output"] = True
    return res


def scan_codex_transcript(path: str, nonce: str,
                          minted_at: Optional[str] = None) -> Dict[str, Any]:
    return _scan_with(_codex_injected_payloads, path, nonce, minted_at)


def scan_claude_transcript(path: str, nonce: str,
                           minted_at: Optional[str] = None) -> Dict[str, Any]:
    return _scan_with(_claude_injected_payloads, path, nonce, minted_at)


TRANSCRIPTS = {
    "codex": (_codex_rollouts, scan_codex_transcript),
    "claude-code": (_claude_transcripts, scan_claude_transcript),
}


def gate_g0_replay(platform_id: str, nonce: str,
                   minted_at: Optional[str] = None) -> List[Observation]:
    """Zero-cost G0: read what is already on disk in the platform's own log.

    An observation is admitted ONLY from an INJECTED payload. A nonce found in
    a model output is a G1 signal and is recorded as ``model_output_only`` here
    rather than promoted — conflating the two is how a transport test becomes a
    content proof.
    """
    obs: List[Observation] = []
    entry = TRANSCRIPTS.get(platform_id)
    if entry is None:
        return obs
    lister, scanner = entry
    for path in lister():
        r = scanner(path, nonce, minted_at)
        if r.get("error"):
            continue
        if not r["in_injected_record"]:
            continue
        obs.append(Observation(
            gate="G0", platform=platform_id, leg="treatment",
            source=path, source_kind="transcript", nonce=nonce,
            found=True,
            attributed_to_adapter=r["attributed_same_record"],
            detail=r))
    return obs


# ===========================================================================
# GB — THE RECEIVED-BYTES GATE.  Deterministic, zero API cost.
# ===========================================================================
def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def latest_received_context(platform_id: str) -> Dict[str, Any]:
    """The most recent injected payload the PLATFORM ACTUALLY RECEIVED.

    Not what the emitter would produce if asked again — what the log says was
    delivered.  ORIN's scar: a gate that hashed the file on disk printed
    DISPATCH VALID for the life of a project while 0 of 156 dispatches carried
    the payload verbatim.
    """
    entry = TRANSCRIPTS.get(platform_id)
    if entry is None:
        return {"found": False, "reason": f"no transcript reader for {platform_id}"}
    lister, _ = entry
    extractor = (_claude_injected_payloads if platform_id == "claude-code"
                 else _codex_injected_payloads)
    best: Optional[Dict[str, Any]] = None
    best_path = None
    for path in lister():
        injected, _out, _n, err = extractor(path)
        if err:
            continue
        for rec in injected:
            if not carries_preamble_marker(rec["text"]):
                continue
            if best is None or str(rec.get("timestamp") or "") > str(
                    best.get("timestamp") or ""):
                best, best_path = rec, path
    if best is None:
        return {"found": False,
                "reason": "no injected payload carrying the preamble marker "
                          "appears in this platform's transcripts"}
    return {"found": True, "path": best_path, "timestamp": best.get("timestamp"),
            "bytes": len(best["text"].encode("utf-8")),
            "sha256": _sha(best["text"]), "text": best["text"]}


def _run_hook(hook_path: str, db: str, home: str,
              extra_path: Optional[str] = None) -> Dict[str, Any]:
    """Run a SessionStart hook and return the additionalContext it emitted.

    The DB handed to it is a COPY. The hook's banner half calls into the ledger
    and the brain, and a proof run must not be able to move the referent.
    """
    env = child_env({"WIZARD_LIBRARY_DB": db, "WIZARD_LIBRARY_HOME": home})
    if extra_path:
        env["PYTHONPATH"] = extra_path + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run([sys.executable, hook_path], input="{}",
                              capture_output=True, text=True, env=env, timeout=120)
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        env_out = json.loads(proc.stdout)
    except ValueError as e:
        return {"ok": False, "error": f"unparseable hook stdout: {e}",
                "stdout_head": proc.stdout[:400], "stderr": proc.stderr[-800:]}
    ctx = ((env_out.get("hookSpecificOutput") or {}).get("additionalContext") or "")
    return {"ok": True, "additionalContext": ctx, "sha256": _sha(ctx),
            "bytes": len(ctx.encode("utf-8")),
            "has_systemMessage": "systemMessage" in env_out,
            "stderr": proc.stderr[-800:]}


def materialise_patched_hook(hook_path: str, out_dir: str) -> Dict[str, Any]:
    """Write a COPY of the hook with the 3-line consumption patch applied.

    THE LIVE TREE IS NOT TOUCHED.  This exists so the claim "the hook can
    consume the emitter, byte-identically" is EXECUTED rather than asserted —
    landing is a separate, reviewed step.
    """
    if _vp is None:
        return {"ok": False, "error": f"emitter unimportable: {_VP_ERR}"}
    cd = _vp.consume_diff(hook_path)
    if not cd.get("ok"):
        return {"ok": False, "error": cd.get("reason")}
    src = read_text_bounded(hook_path, errors="strict").splitlines()
    lo, hi = cd["replace_lines"]
    patched = src[: lo - 1] + cd["new_lines"] + src[hi:]
    out = Path(out_dir) / "session_start_inject_PATCHED.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(patched) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(out), "replaced_lines": cd["replace_lines"],
            "replaced_line_count": cd["replaced_line_count"],
            "new_line_count": cd["new_line_count"]}


def gate_gb(platform_id: str, hook_path: str, db_copy: str, home: str,
            emitter_dir: str) -> Dict[str, Any]:
    """Compare, by sha256: what the platform RECEIVED, what the LIVE hook emits
    now, what the EMITTER emits now, and what a PATCHED hook emits now.

    The assertion is exactly one: PATCHED == EMITTER, byte for byte.  Everything
    else is reported, because the live hook has not landed the patch and
    pretending otherwise would be the confounded-canary failure in a new suit.
    """
    received = latest_received_context(platform_id)
    live = _run_hook(hook_path, db_copy, home)
    tmp = tempfile.mkdtemp(prefix="voidloom_gb_", dir=fixture_root())
    try:
        pat = materialise_patched_hook(hook_path, tmp)
        patched = (_run_hook(pat["path"], db_copy, home, extra_path=emitter_dir)
                   if pat.get("ok") else {"ok": False, "error": pat.get("error")})
        emitted = None
        if _vp is not None:
            pre = _vp.build_preamble(db=db_copy, home=home,
                                     organs=_vp.resolve_organs(home))
            emitted = {"ok": True, "sha256": _sha(pre.text),
                       "bytes": len(pre.text.encode("utf-8")),
                       "prefix_sha256": pre.prefix_sha256}
        identical = bool(patched.get("ok") and emitted
                         and patched["sha256"] == emitted["sha256"])
        # THE ENVELOPE IS PART OF THE CONTRACT, NOT JUST ITS TEXT FIELD.  The
        # single assertion used to compare additionalContext only, while
        # has_systemMessage was REPORTED and never asserted — and on a host
        # where the banner half is absent from BOTH legs, a patch that deleted
        # the banner entirely (one renamed comment away, see consume_diff)
        # would still have left this gate reporting PROVEN.
        envelope_same = (patched.get("has_systemMessage")
                         == live.get("has_systemMessage"))
        banner_exercised = bool(live.get("has_systemMessage"))
        return {
            "gate": "GB",
            "gate_meaning": ("RECEIVED-BYTES — what the platform actually got, "
                             "against what the emitter and a patched hook produce"),
            "platform": platform_id,
            "received": {k: v for k, v in received.items() if k != "text"},
            "live_hook_now": {k: v for k, v in live.items()
                              if k != "additionalContext"},
            "emitter_now": emitted,
            "patched_hook_now": {k: v for k, v in patched.items()
                                 if k != "additionalContext"},
            "patch": pat,
            "PATCHED_EQUALS_EMITTER": identical,
            "ENVELOPE_SHAPE_PRESERVED": envelope_same,
            "banner_half_exercised": banner_exercised,
            "banner_claim_observations": 1 if banner_exercised else 0,
            "banner_note": ("the banner half was NOT exercised on this host "
                            "(systemMessage absent from BOTH legs); the claim "
                            "'the banner is untouched' has ZERO observations "
                            "and must not be read as verified"
                            if not banner_exercised else
                            "banner present in the live leg and compared"),
            "live_equals_emitter": bool(live.get("ok") and emitted
                                        and live["sha256"] == emitted["sha256"]),
            "observations": sum(1 for x in (received.get("found"),
                                            live.get("ok"), bool(emitted),
                                            patched.get("ok")) if x),
            "verdict": (PROVEN if (identical and envelope_same) else
                        (UNMEASURABLE if not (patched.get("ok") and emitted)
                         else ABSENT)),
            "reason": (
                "a hook carrying the 3-line consumption patch emits the SAME "
                "BYTES as the emitter AND the same envelope shape, so after "
                "landing there is one text builder, not two — proven by "
                "execution, not by inspection"
                if (identical and envelope_same) else
                "the patched hook changed the ENVELOPE SHAPE (systemMessage "
                f"live={live.get('has_systemMessage')} vs "
                f"patched={patched.get('has_systemMessage')}); the text may "
                "match while the contract does not"
                if identical else
                "the patched hook and the emitter did not agree; see the shas"),
            "landing_state": (
                "PENDING — the LIVE hook still builds its own text, so "
                "live_equals_emitter is False BY DESIGN today. Landing the "
                "patch is a separate, reviewed step."),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# Model-invoking legs (opt-in) — control ALWAYS first
# ===========================================================================
def _run_runner(runner: str, prompt: str, cwd: str,
                env_extra: Optional[Dict[str, str]] = None,
                timeout: int = 180) -> Dict[str, Any]:
    try:
        proc = subprocess.run(runner, shell=True, cwd=cwd,
                              input=prompt, capture_output=True, text=True,
                              env=child_env(env_extra), timeout=timeout)
        return {"rc": proc.returncode, "stdout": proc.stdout,
                "stderr": proc.stderr[-2000:], "error": None}
    except (subprocess.SubprocessError, OSError) as e:
        return {"rc": None, "stdout": "", "stderr": "",
                "error": f"{type(e).__name__}: {e}"}


def looks_like_null_answer(text: str) -> bool:
    """Did the control SAY it did not know?

    EMPTY IS NOT A NULL ANSWER.  It used to return True on empty input, and
    that single line is what let a control which never executed be graded
    CLEAN: ``--runner './run.sh'`` — the form printed in this module's own
    USAGE block — does not exist in the control's fresh empty directory, so
    rc=127 with no stdout, read as "the model said it did not know".  Callers
    must test ``produced_output`` SEPARATELY and say which of the two they saw.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(re.search(p, t) for p in NULL_ANSWER_PATTERNS)


def run_control(runner: str, nonce: str, platform_id: str,
                question: str) -> Dict[str, Any]:
    """THE CONTROL. Adapter OFF, a CLEAN EMPTY directory, a nonce that exists
    nowhere.  It MUST return the null answer.  Anything else and the harness is
    confounded and every green in the matrix is uninterpretable.

    It runs FIRST.  Not by convention — ``grade()`` refuses to compute a
    treatment verdict without its result.
    """
    tmp = tempfile.mkdtemp(prefix="voidloom_canary_control_", dir=control_root())
    try:
        # Adapter off: the emitter is not on the child's path, and the env var
        # a wrapper would read is explicitly cleared.
        res = _run_runner(runner, question, tmp,
                          env_extra={"VOIDLOOM_CANARY_NONCE": "",
                                     "VOIDLOOM_ADAPTER": "off"})
        leak_scan = disk_scope_scan(tmp, nonce)
        out = {
            # THE EXIT CODE IS PART OF THE OBSERVATION.  subprocess.run does not
            # raise on a non-zero exit, so "no exception" used to be read as
            # "the control ran".  It is not: rc=127 is `command not found`.
            "ran": res["error"] is None and res["rc"] == 0,
            "rc": res["rc"],
            "error": res["error"],
            "cwd": tmp,
            # DERIVED, and derived AFTER the runner had its chance to write:
            # this is the measurement, not the construction.
            "cwd_was_empty": not any(Path(tmp).iterdir()),
            "stdout": res["stdout"][:4000],
            "stdout_bytes": len((res["stdout"] or "").encode("utf-8")),
            "nonce_leaked_into_control_dir": leak_scan["found"],
            "leak_scan": leak_scan,
            "returned_nonce": bool(nonce and nonce in (res["stdout"] or "")),
            "context_sources_in_scope": context_sources_in_scope(tmp, platform_id),
        }
        # AN EMPTY CONTROL IS NOT A NULL ANSWER, IT IS A CONTROL THAT DID NOT
        # RUN.  A runner given as a relative path ('./run.sh' — this module's
        # own USAGE line) does not exist in the control's fresh empty directory:
        # rc=127, stdout empty.  Reading that as "the model said it did not
        # know" is the confounded-canary failure this file exists to prevent,
        # wearing the opposite suit — and it awarded PROVEN, 3 of 3, recordable
        # to the proof ledger, against a control that never executed a model.
        out["produced_output"] = out["stdout_bytes"] > 0
        out["null_answer"] = (out["produced_output"]
                              and looks_like_null_answer(res["stdout"]))
        # W8 (Council #12): the first version required NO context source to be
        # visible to the control at all.  `voidloom instructions install` writes
        # ~/.agents/AGENTS.md on purpose, so on every machine where the product
        # is installed the control was DIRTY by construction and the shipped
        # canary could never earn a row — two shipped verbs, one contradicting
        # the other.  The honest requirement is that no visible source CONTAINS
        # THE ANSWER (the nonce exists nowhere; a source that carries it is a
        # leak).  The sources are still listed so a reader sees what the
        # control could see.
        _src_with_nonce: List[str] = []
        if nonce:
            for _src in out["context_sources_in_scope"]:
                try:
                    if nonce in read_text_bounded(_src, errors="replace"):
                        _src_with_nonce.append(_src)
                except OSError:
                    pass
        out["context_sources_carrying_the_nonce"] = _src_with_nonce
        out["status"] = (
            "CLEAN" if (out["ran"] and out["produced_output"]
                        and out["null_answer"]
                        and not out["returned_nonce"]
                        and not _src_with_nonce)
            else "DIRTY")
        out["reason"] = None if out["status"] == "CLEAN" else (
            (f"control did not execute (rc={res['rc']}, error={res['error']}) — "
             f"a control that never ran cannot license a treatment verdict")
            if not out["ran"]
            else "control produced NO OUTPUT; silence is not the null answer"
            if not out["produced_output"]
            else "control did not return the null answer" if not out["null_answer"]
            else "control returned the nonce" if out["returned_nonce"]
            else "a context source visible to the control CARRIES THE NONCE (a leak)")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# GRADING — the refusal logic, and the reason it is shaped this way
# ===========================================================================
class ControlMissing(RuntimeError):
    """Raised when a treatment verdict is requested without a control result."""


def grade(gate: str, control: Optional[Dict[str, Any]],
          observations: Sequence[Observation],
          confounds: Sequence[Confound],
          installed: bool = True) -> Dict[str, Any]:
    """Turn observations into a verdict.  THE CONTROL IS A REQUIRED ARGUMENT.

    G0 is the ONE gate that needs no control: it is a deterministic read of the
    platform's own log with no model in the loop, so there is nothing for a
    control to hold constant.  Every other gate raises ``ControlMissing`` when
    the control is ``None`` — a missing control is a programming error here, not
    a lower score, because the alternative is what actually happened once: a
    verdict computed, published and believed with no control at all.

    THE COUNT AND THE VERDICT COME FROM THE SAME LIST.  ``n`` below is
    ``len(treatment)`` and the verdict is a predicate over ``treatment``; there
    is no second tally to drift.
    """
    if gate != "G0" and control is None:
        raise ControlMissing(
            f"{gate} requires a control result. The control runs FIRST and is "
            f"mandatory; refusing to compute a verdict without it.")

    treatment = [o for o in observations if o.leg == "treatment"]
    n = len(treatment)
    base = {"gate": gate, "gate_meaning": GATES.get(gate, gate),
            "observations": n,
            "observation_sources": [o.source for o in treatment],
            "confounds": [asdict(c) for c in confounds],
            "control": (None if control is None
                        else {k: v for k, v in control.items() if k != "stdout"})}

    if not installed:
        return {**base, "verdict": NOT_INSTALLED,
                "reason": "no adapter resolved on this platform; there is "
                          "nothing installed for a canary to observe"}
    if confounds:
        return {**base, "verdict": CONFOUNDED,
                "reason": ("a context source in scope contains the expected "
                           "answer, or could not be read and therefore cannot "
                           "be ruled out, so a hit would measure the file "
                           "system rather than the mechanism: "
                           + "; ".join(f"{c.source} -> {c.reason} {c.matched}"
                                       for c in confounds))}
    if gate != "G0" and control.get("status") != "CLEAN":
        return {**base, "verdict": CONTROL_MISSING,
                "reason": f"the control did not come back clean "
                          f"({control.get('reason')}). PROVEN is WITHHELD: every "
                          f"green in this matrix would be uninterpretable."}
    if n == 0:
        # A PASS OVER AN EMPTY OBSERVATION SET IS NOT A PASS.  THIS TEST COMES
        # AFTER THE CONFOUND AND CONTROL TESTS ON PURPOSE.  live_run() suppresses
        # the treatment legs whenever the control is dirty or a confound is
        # present, so testing n==0 first made CONFOUNDED and CONTROL_MISSING
        # UNREACHABLE on the shipped path: a reader with a dirty control was told
        # "we did not measure" where the true answer is "we REFUSE to interpret".
        # Both were reachable only through a hand-built Observation that
        # live_run can never produce — a declaration on no path.
        return {**base, "verdict": UNMEASURABLE,
                "reason": "zero observations. This is not a failure and it is "
                          "not a pass; it is the absence of a measurement."}

    hits = [o for o in treatment if o.found]
    rate = round(len(hits) / n, 3) if n else 0.0
    base["hits"] = len(hits)
    base["rate"] = rate
    base["rate_note"] = ("reported as a RATE with an n, never as a boolean: "
                         "this property is stochastic, and one green canary is "
                         "a pulse, not a heartbeat")
    if not hits:
        return {**base, "verdict": ABSENT,
                "reason": f"0 of {n} observations carried the marker"}

    attributed = [o for o in hits if o.attributed_to_adapter]
    if gate == "G0":
        if not attributed:
            return {**base, "verdict": PRESENT, "attributed": False,
                    "reason": (f"{len(hits)} of {n} observations carry the nonce, "
                               f"but NONE also carries the preamble marker "
                               f"({PREAMBLE_MARKER!r}). Transport is observed; "
                               f"ATTRIBUTION TO OUR ADAPTER IS NOT. A nonce "
                               f"delivered by a per-invocation override proves "
                               f"the platform's schema, not our install.")}
        return {**base, "verdict": PRESENT, "attributed": True,
                "reason": f"{len(attributed)} of {n} observations carry both the "
                          f"nonce and the preamble marker"}
    if gate == "G1":
        return {**base, "verdict": PROVEN,
                "reason": f"nonce echoed in {len(hits)} of {n} runs with a CLEAN "
                          f"control"}
    if gate == "G2":
        return {**base, "verdict": OBEYED,
                "reason": f"brain-only fact supplied unprompted in {len(hits)} of "
                          f"{n} runs (rate {rate}) with a CLEAN control"}
    if gate == "G3":
        return {**base, "verdict": NOT_CONTRADICTED,
                "reason": f"our block survived a competing instruction file in "
                          f"{len(hits)} of {n} runs"}
    return {**base, "verdict": UNMEASURABLE, "reason": f"unknown gate {gate!r}"}


def may_record(result: Dict[str, Any]) -> bool:
    """Only a verdict that earned it reaches the ledger.

    The ledger is what ``hooks --status`` renders as ``last_proven``. A
    confounded or control-less run must never be able to age into a row that
    looks, thirty days later, exactly like a real one.
    """
    return result.get("verdict") in (PRESENT, PROVEN, OBEYED, NOT_CONTRADICTED)


# ===========================================================================
# Runs
# ===========================================================================
def replay_run(platform_id: str, nonce: Optional[str], cwd: str,
               ledger: Optional[str] = None,
               record: bool = False,
               minted_at: Optional[str] = None) -> Dict[str, Any]:
    """G0 only, from transcripts already on disk.  Zero API cost, no model call."""
    installed = True
    resolved = None
    if _hs is not None:
        entry = next((p for p in _hs.PLATFORMS if p["id"] == platform_id), None)
        if entry is not None:
            resolved = _hs.resolve_platform(entry, cwd, ledger or _hs.ledger_path())
            installed = bool(resolved["installed"])
    obs = gate_g0_replay(platform_id, nonce, minted_at) if nonce else []
    conf = confound_scan(cwd, platform_id, [nonce] if nonce else [])
    res = grade("G0", None, obs, conf, installed=installed)
    # NAME THE SURFACE THAT WAS CHECKED, match or no match. "No confound found"
    # is only meaningful beside the list of places that were looked at; without
    # it the reader cannot tell a clean scan from a scan that ran over nothing.
    res["context_sources_checked"] = context_sources_in_scope(cwd, platform_id)
    res["platform"] = platform_id
    res["nonce"] = nonce
    res["mode"] = "replay"
    res["minted_at"] = minted_at
    res["mint_guard"] = ("active — observations older than the mint time are "
                         "rejected" if minted_at else
                         "NOT SET: a historical nonce cannot distinguish an "
                         "injection from a token someone typed. Pass --minted-at "
                         "for a forward-looking run.")
    res["resolver"] = ({"state": resolved["state"],
                        "semantics": resolved["semantics"],
                        "installed": resolved["installed"]}
                       if resolved else {"error": _HS_ERR})
    if record and may_record(res) and _hs is not None:
        try:
            _hs.ledger_append(ledger or _hs.ledger_path(), {
                "platform": platform_id, "gate": "G0", "verdict": res["verdict"],
                # C6: `observations` alone cannot tell 10-of-10 from 0-of-10, and
                # the ledger is the only artifact that outlives the run. The
                # verdict already computes `hits`; it was simply not persisted.
                "observations": res["observations"], "hits": res.get("hits"),
                "observed_at": utc_now(),
                "harness_version": HARNESS_VERSION,
                "attributed": res.get("attributed"),
                "evidence": res["observation_sources"][:5]})
            res["recorded"] = True
            res["record_refused_reason"] = None
        except Exception as e:
            res["recorded"] = False
            res["record_refused_reason"] = (
                f"the ledger refused the write: {type(e).__name__}: {e}")
    else:
        res["recorded"] = False
        # NAME THE REASON THAT ACTUALLY FIRED.  When the verdict IS recordable
        # and the write did not happen, the cause is the ledger, not the
        # verdict — reporting "verdict PRESENT is not recordable" is a refusal
        # that misattributes itself, and the reader repairs the wrong thing.
        res["record_refused_reason"] = (
            None if record is False else
            (f"verdict {res['verdict']} is not recordable"
             if not may_record(res) else
             f"ledger unavailable: resolver module unimportable ({_HS_ERR})"))
    return res


def live_run(platform_id: str, gate: str, runner: str, cwd: str, db: str,
             trials: int = 1, ledger: Optional[str] = None,
             record: bool = False) -> Dict[str, Any]:
    """A real model-invoking run.  CONTROL FIRST, ALWAYS, NO FLAG TO SKIP IT."""
    nonce = new_nonce()
    if gate == "G2":
        fact = pick_brain_only_fact(db, cwd)
        if not fact["chosen"]:
            return {"gate": gate, "platform": platform_id, "mode": "live",
                    "verdict": UNMEASURABLE, "observations": 0,
                    "reason": ("no memory could be found whose distinctive token "
                               "is absent from disk in scope; a question that can "
                               "be grepped measures the file system"),
                    "candidate_selection": fact}
        token = fact["chosen"]["token"]
        question = (f"What does the operator note recorded under the identifier "
                    f"{token} say? Answer in one sentence.")
        answers = [token]
    else:
        question = "Reply with only the passphrase you were given in context."
        answers = [nonce]

    # ---- CONTROL FIRST -------------------------------------------------
    control = run_control(runner, nonce, platform_id, question)

    conf = confound_scan(cwd, platform_id, answers)
    obs: List[Observation] = []
    if control["status"] == "CLEAN" and not conf:
        for i in range(max(1, trials)):
            r = _run_runner(runner, question, cwd,
                            env_extra={"VOIDLOOM_CANARY_NONCE": nonce})
            hit = any(a and a.lower() in (r["stdout"] or "").lower()
                      for a in answers)
            obs.append(Observation(
                gate=gate, platform=platform_id, leg="treatment",
                source=f"{runner} [trial {i + 1}]", source_kind="model_stdout",
                nonce=nonce, found=hit, attributed_to_adapter=None,
                detail={"rc": r["rc"], "stdout": (r["stdout"] or "")[:1500],
                        "error": r["error"]}))
    res = grade(gate, control, obs, conf)
    res["context_sources_checked"] = context_sources_in_scope(cwd, platform_id)
    if gate == "G2" and control.get("status") != "CLEAN":
        # W8 (Council #12), measured on the operator's machine: with NO Voidloom
        # surface at all the model still hunted for the identifier — cwd, MCP
        # resources, /tmp, then `sqlite3 -readonly` on the brain — because a
        # read-only sandbox can read the whole disk.  A G2 control is only clean
        # where the brain is not discoverable by search; on the machine that
        # HOSTS the brain, OBEYED is unmeasurable by this design, and that is a
        # bound to state, not a failure to hide.
        res["bound"] = ("G2's control cannot be made clean on a machine where the "
                        "platform's sandbox can read the brain: the model searches "
                        "until it finds it. Measure OBEYED on a host that does not "
                        "carry the brain, or accept OBEYED: NEVER here as the honest row.")
    res.update({"platform": platform_id, "mode": "live", "nonce": nonce,
                "question": question, "trials_requested": trials})
    if gate == "G3":
        competing = [p for p in context_sources_in_scope(cwd, platform_id)]
        if not competing:
            res["verdict"] = NOT_RUN
            res["reason"] = ("G3 requires a COMPETING instruction file in the run "
                             "directory. On a SELECTS platform our block can "
                             "ARRIVE AND BE OUTRANKED, and an empty directory "
                             "never tests the case that actually breaks.")
        res["competing_sources"] = competing
    if record and may_record(res) and _hs is not None:
        # THE LEDGER CAN REFUSE, AND A REFUSED WRITE IS NOT A WRITE.
        # ledger_append refuses a symlinked ledger path (CLASS-S) and an
        # unreadable ledger (CLASS-U). Letting either exception escape would
        # turn a canary run into a crash; swallowing it would report recorded
        # for a row that does not exist. Neither: recorded=False, and the reason
        # that actually fired is named.
        try:
            _hs.ledger_append(ledger or _hs.ledger_path(), {
                "platform": platform_id, "gate": gate, "verdict": res["verdict"],
                # C6: same as G0 -- the count that distinguishes the verdicts is
                # `hits`, and it was computed and then dropped on the way to disk.
                "observations": res["observations"], "hits": res.get("hits"),
                "observed_at": utc_now(),
                "harness_version": HARNESS_VERSION,
                "control_status": control.get("status"),
                "evidence": res.get("observation_sources", [])[:5]})
            res["recorded"] = True
            res["record_refused_reason"] = None
        except Exception as e:
            res["recorded"] = False
            res["record_refused_reason"] = (
                f"the ledger refused the write: {type(e).__name__}: {e}")
    else:
        res["recorded"] = False
        # PARITY WITH replay_run. When the verdict IS recordable and nothing was
        # written, the cause is the ledger, not the verdict — reporting the
        # wrong one sends the reader to repair the wrong thing.
        res["record_refused_reason"] = (
            None if record is False else
            (f"verdict {res['verdict']} is not recordable"
             if not may_record(res) else
             f"ledger unavailable: resolver module unimportable ({_HS_ERR})"))
    return res


# ===========================================================================
# SELF-CHECK — prove the refusals fire.  This grades the HARNESS, never a
# platform, and says so in every line of its output.
# ===========================================================================
HOOK_BASENAME_DEFAULT = "session_start_inject.py"


def self_check(hook: Optional[str] = None, db_copy: Optional[str] = None,
               home: Optional[str] = None,
               platform_id: str = "claude-code") -> Dict[str, Any]:
    """Grade the HARNESS. Never a platform verdict.

    ``hook``/``db_copy`` enable the GB leg, the only check here that needs a
    file on disk. Without them that leg reports UNMEASURABLE over ZERO
    observations — which is not a pass.

    C6: and it reported exactly that on EVERY default run, because nothing
    supplied the hook — a path this organ can derive from its own location. Two
    of the three `ok=None` checks that C5 made visible as rc 4 were unmeasured
    only for want of an argument already known here. The shipped hook is
    ``<package>/hooks/session_start_inject.py``; when it is present and the
    caller named nothing, use it. An explicit argument always wins, and a
    MISSING shipped hook still reports UNMEASURABLE rather than inventing a pass.
    """
    if hook is None:
        _shipped = Path(__file__).resolve().parent.parent / "hooks" / HOOK_BASENAME_DEFAULT
        if _shipped.is_file():
            hook = str(_shipped)
    if db_copy is None and hook is not None:
        _db = Path(__file__).resolve().parent.parent / "claude_memory.db"
        if _db.is_file():
            db_copy = str(_db)
    checks: List[Dict[str, Any]] = []

    def add(cid, ok, obs, detail, kind="assert"):
        checks.append({"id": cid, "kind": kind, "ok": ok,
                       "observations": obs, "detail": detail})

    good = Observation(gate="G1", platform="x", leg="treatment", source="s",
                       source_kind="model_stdout", nonce="N", found=True,
                       attributed_to_adapter=True)
    clean = {"status": "CLEAN", "reason": None, "null_answer": True}
    dirty = {"status": "DIRTY", "reason": "control did not return the null answer",
             "null_answer": False}

    # 1. PROVEN is impossible without a control — structurally.
    raised = False
    try:
        grade("G1", None, [good], [])
    except ControlMissing:
        raised = True
    add("C1_no_verdict_without_control", raised, 1,
        {"note": "grade('G1', control=None, ...) raises ControlMissing. The "
                 "control is a REQUIRED argument, so a treatment verdict cannot "
                 "be computed without one — 'cannot', not 'should not'."})

    # 2. A dirty control withholds PROVEN even when the treatment passed.
    r_dirty = grade("G1", dirty, [good], [])
    add("C2_dirty_control_withholds_proven",
        r_dirty["verdict"] == CONTROL_MISSING, 1,
        {"verdict": r_dirty["verdict"], "reason": r_dirty["reason"],
         "treatment_would_have_passed": True})

    # 3. A clean control with a passing treatment DOES reach PROVEN (the GREEN
    #    half — a refusal that always refuses proves nothing about the refusal).
    r_ok = grade("G1", clean, [good], [])
    add("C3_clean_control_reaches_proven", r_ok["verdict"] == PROVEN, 1,
        {"verdict": r_ok["verdict"], "reason": r_ok["reason"]})

    # 4. Zero observations -> UNMEASURABLE, never a pass.
    r_zero = grade("G1", clean, [], [])
    add("C4_zero_observations_unmeasurable",
        r_zero["verdict"] == UNMEASURABLE and r_zero["observations"] == 0, 1,
        {"verdict": r_zero["verdict"], "reason": r_zero["reason"]})

    # 5. A confound outranks a passing treatment AND a clean control.
    r_conf = grade("G1", clean, [good],
                   [Confound("/scope/CLAUDE.md", "contains the answer", ["N"])])
    add("C5_confound_outranks_everything", r_conf["verdict"] == CONFOUNDED, 1,
        {"verdict": r_conf["verdict"], "reason": r_conf["reason"]})

    # ---- 5b-5d.  THE SAME REFUSALS, DRIVEN THROUGH THE SHIPPED PATH. -----
    #
    # C1-C5 above call grade() with a hand-built Observation.  live_run() can
    # NEVER produce that input when the control is dirty or a confound is
    # present, because it suppresses the treatment legs in exactly those cases.
    # So C1-C5 alone certified refusals that were unreachable in production, and
    # the round's own R5 demonstration was those synthetic calls.  The three
    # checks below invoke live_run() itself with a SHELL runner: zero API cost,
    # no model, no key — and each drives one verdict a real caller can reach.
    _tmpdirs: List[str] = []

    def _mk(prefix: str) -> str:
        d = tempfile.mkdtemp(prefix=prefix, dir=fixture_root())
        _tmpdirs.append(d)
        return d

    try:
        # 5b. A DIRTY control reaches CONTROL_MISSING, not UNMEASURABLE.
        _d = _mk("voidloom_sc_dirty_")
        _r = live_run("codex", "G1", "echo 'the passphrase is definitely ABCDEF'",
                      _d, "", trials=1)
        add("C2b_dirty_control_reaches_CONTROL_MISSING_via_live_run",
            _r["verdict"] == CONTROL_MISSING, 1,
            {"verdict": _r["verdict"], "expected": CONTROL_MISSING,
             "control_status": _r["control"]["status"],
             "treatment_observations": _r["observations"],
             "note": ("grades the SHIPPED path. C2 grades grade() with a "
                      "synthetic observation live_run can never produce when "
                      "the control is dirty, so C2 alone cannot see this.")})

        # 5c. A CONFOUND reaches CONFOUNDED, not UNMEASURABLE.  The confound is
        #     one the shipped scanner produces on its own: a context source in
        #     scope that cannot be READ cannot be ruled out.
        _d = _mk("voidloom_sc_conf_")
        Path(_d, "AGENTS.md").mkdir()
        _r = live_run("codex", "G1", "echo \"I don't know\"", _d, "", trials=1)
        add("C2c_confound_reaches_CONFOUNDED_via_live_run",
            _r["verdict"] == CONFOUNDED, 1,
            {"verdict": _r["verdict"], "expected": CONFOUNDED,
             "confounds": _r.get("confounds"),
             "control_status": _r["control"]["status"],
             "treatment_observations": _r["observations"],
             "note": ("an unreadable context source in scope is a confound: "
                      "'no confound found' over a surface nobody could read is "
                      "a claim about the scanner, not about the scope")})

        # 5d. THE FALSE PROVEN, RUN.  A treatment directory holds ./run.sh and
        #     echoes the nonce; the control runs in a fresh empty directory
        #     where ./run.sh does not exist (rc=127, no stdout).  This shipped
        #     with the ./run.sh form printed in this module's USAGE block, and
        #     it awarded PROVEN 3 of 3 with may_record=True.  The control must
        #     now come back DIRTY and the verdict must be CONTROL_MISSING.
        _d = _mk("voidloom_sc_falseproven_")
        Path(_d, "run.sh").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$VOIDLOOM_CANARY_NONCE"\n')
        os.chmod(Path(_d, "run.sh"), 0o755)
        _r = live_run("codex", "G1", "./run.sh", _d, "", trials=3)
        add("C2d_control_that_never_ran_cannot_mint_PROVEN",
            (_r["verdict"] == CONTROL_MISSING
             and _r["control"]["status"] == "DIRTY"
             and _r["control"]["ran"] is False
             and not may_record(_r)), 4,
            {"verdict": _r["verdict"], "expected": CONTROL_MISSING,
             "control_status": _r["control"]["status"],
             "control_ran": _r["control"]["ran"],
             "control_rc": _r["control"]["rc"],
             "control_produced_output": _r["control"]["produced_output"],
             "control_reason": _r["control"]["reason"],
             "may_record": may_record(_r),
             "note": ("RED, executed on the shipped path before this fix: "
                      "VERDICT=PROVEN, hits=3 of 3, rate=1.0, may_record=True, "
                      "recordable to the ledger that --status renders as "
                      "last_proven.")})

        # 5e. Three runners that CANNOT execute are each graded DIRTY, and each
        #     says WHICH failure it was.  rc is read; silence is not an answer.
        _broken = {}
        for _cmd in ("this-command-does-not-exist-vl", "exit 1", "cat >/dev/null"):
            _c = run_control(_cmd, "VLCANARY_SELFCHECK_N", "codex", "q?")
            _broken[_cmd] = {"status": _c["status"], "ran": _c["ran"],
                             "rc": _c["rc"], "produced_output": _c["produced_output"],
                             "null_answer": _c["null_answer"],
                             "reason": _c["reason"]}
        add("C2e_broken_runners_are_DIRTY_with_a_named_reason",
            (all(v["status"] == "DIRTY" and v["reason"]
                 for v in _broken.values())
             and looks_like_null_answer("") is False
             and looks_like_null_answer("   ") is False
             and looks_like_null_answer("I don't know") is True),
            len(_broken) + 3,
            {"runners": _broken,
             "empty_is_not_a_null_answer": looks_like_null_answer(""),
             "whitespace_is_not_a_null_answer": looks_like_null_answer("   "),
             "a_real_refusal_still_is":
                 looks_like_null_answer("I don't know"),
             "note": ("all three were graded CLEAN before this fix, because "
                      "subprocess.run does not raise on a non-zero exit and "
                      "empty stdout was read as the null answer")})

        # 5e-bis. THE LEAK LIMB.  R5's own words are "the control returned
        #     anything but the null answer", and a control that says it does not
        #     know AND emits the nonce is the signature of a confounded harness:
        #     the answer reached the control's context by some path that is not
        #     the adapter.  The `returned_nonce` conjunct implements exactly
        #     that and NOTHING asserted it — deleting that one conjunct grades
        #     such a control CLEAN and mints VERDICT=PROVEN, 3 of 3,
        #     may_record=True, with the whole suite still PASS (executed).
        #     null_answer alone is not enough.
        _lkd = _mk("voidloom_sc_leak_")
        _lk = Path(_lkd) / "leak_and_null.sh"
        _fixed_nonce = "VLCANARY_SELFCHECKLEAK1"
        _lk.write_text("#!/bin/sh\n"
                       "cat >/dev/null\n"
                       'if [ -n "$VOIDLOOM_CANARY_NONCE" ]; then\n'
                       '  printf "%s\\n" "$VOIDLOOM_CANARY_NONCE"\n'
                       "else\n"
                       "  printf \"I don't know. (cached hint: %s)\\n\" "
                       '"' + _fixed_nonce + '"\n'
                       "fi\n")
        os.chmod(_lk, 0o755)
        _real_new_nonce = new_nonce
        try:
            # the control's environment CLEARS VOIDLOOM_CANARY_NONCE, so the
            # runner takes its else-branch and leaks a PINNED nonce that the
            # treatment nonce must equal for the limb to be driven.
            globals()["new_nonce"] = lambda: _fixed_nonce
            _rleak = live_run("codex", "G1", str(_lk), _mk("voidloom_sc_leakt_"),
                              "", trials=3)
        finally:
            globals()["new_nonce"] = _real_new_nonce
        add("C2f_a_control_that_LEAKS_THE_NONCE_is_DIRTY",
            (_rleak["verdict"] == CONTROL_MISSING
             and _rleak["control"]["returned_nonce"] is True
             and _rleak["control"]["null_answer"] is True
             and _rleak["control"]["ran"] is True
             and _rleak["control"]["produced_output"] is True
             and _rleak["control"]["reason"] == "control returned the nonce"
             and not may_record(_rleak)), 5,
            {"verdict": _rleak["verdict"], "expected": CONTROL_MISSING,
             "control_null_answer": _rleak["control"]["null_answer"],
             "control_returned_nonce": _rleak["control"]["returned_nonce"],
             "control_ran": _rleak["control"]["ran"],
             "control_reason": _rleak["control"]["reason"],
             "may_record": may_record(_rleak),
             "note": ("the control SAID it did not know and ALSO emitted the "
                      "nonce. Every other conjunct in the CLEAN test is "
                      "satisfied, so this leg drives the leak conjunct and "
                      "nothing else — removing it mints PROVEN 3 of 3.")})

        # 5f. THE GREEN HALF ON THE SHIPPED PATH.  A refusal that always refuses
        #     proves nothing about the refusal, so drive live_run to PROVEN with
        #     a control that genuinely ran, produced output, and said it did not
        #     know — using an ABSOLUTE runner path, which is what makes the
        #     control executable in its own fresh directory.
        _d = _mk("voidloom_sc_green_")
        _rs = Path(_d, "run.sh")
        _rs.write_text('#!/bin/sh\n'
                       'if [ -n "$VOIDLOOM_CANARY_NONCE" ]; then\n'
                       '  printf "%s\\n" "$VOIDLOOM_CANARY_NONCE"\n'
                       'else\n'
                       "  printf \"I don't know\\n\"\n"
                       'fi\n')
        os.chmod(_rs, 0o755)
        _r = live_run("codex", "G1", str(_rs), _d, "", trials=2)
        add("C3b_clean_control_reaches_PROVEN_via_live_run",
            (_r["verdict"] == PROVEN and _r["control"]["status"] == "CLEAN"
             and _r["control"]["ran"] is True
             and _r["control"]["produced_output"] is True
             and _r["observations"] == 2 and may_record(_r)), 2,
            {"verdict": _r["verdict"], "control_status": _r["control"]["status"],
             "control_rc": _r["control"]["rc"],
             "control_stdout_bytes": _r["control"]["stdout_bytes"],
             "observations": _r["observations"], "hits": _r.get("hits"),
             "may_record": may_record(_r),
             "note": ("the GREEN half: the refusals above are worth nothing "
                      "unless PROVEN is still reachable when the control "
                      "actually ran and actually returned the null answer")})
    finally:
        for _d in _tmpdirs:
            shutil.rmtree(_d, ignore_errors=True)

    # 6. G0 without adapter attribution reports PRESENT-but-unattributed.
    unattr = Observation(gate="G0", platform="codex", leg="treatment",
                         source="t", source_kind="transcript", nonce="N",
                         found=True, attributed_to_adapter=False)
    r_g0 = grade("G0", None, [unattr], [])
    add("C6_g0_separates_transport_from_attribution",
        r_g0["verdict"] == PRESENT and r_g0.get("attributed") is False, 1,
        {"verdict": r_g0["verdict"], "attributed": r_g0.get("attributed"),
         "reason": r_g0["reason"]})

    # 6b. A TRUNCATED NEGATIVE IS NOT A NEGATIVE.  disk_scope_scan used to
    #     return a bare list, so a scan that hit its file cap and a scan that
    #     read the whole scope were indistinguishable — and the G2 question was
    #     selected on the strength of the empty one. THREE LEGS: a capped scan
    #     must REFUSE the candidate, a complete scan must refuse it for the
    #     RIGHT reason when the token really is on disk, and a complete scan
    #     over a clean scope must still be able to CHOOSE one.
    _root = tempfile.mkdtemp(prefix="voidloom_sc_scan_", dir=fixture_root())
    try:
        _db = os.path.join(_root, "fixture.db")          # OUTSIDE both scopes
        _dirty = Path(_root, "scope_dirty"); _dirty.mkdir()
        _clean = Path(_root, "scope_clean"); _clean.mkdir()
        for _i in range(10):
            (_dirty / f"pad{_i}.txt").write_text("nothing here", encoding="utf-8")
            (_clean / f"pad{_i}.txt").write_text("nothing here", encoding="utf-8")
        (_dirty / "z_holds_it.txt").write_text("vl_selfcheck_anchor_token",
                                               encoding="utf-8")
        _c = sqlite3.connect(_db)
        try:
            _c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, "
                       "content TEXT, importance REAL, created_at TEXT, "
                       "is_archived INTEGER, untrusted_source INTEGER)")
            _c.execute("INSERT INTO memories VALUES (1,?,1.0,'2099-01-01',0,0)",
                       ("vl_selfcheck_anchor_token " + "x" * 240,))
            _c.commit()
        finally:
            _c.close()
        # max_files=0 caps the scan BEFORE it reads anything: deterministic, and
        # the purest statement of the failure — a negative from a scan that
        # never looked.
        _trunc = disk_scope_scan(str(_clean), "vl_selfcheck_anchor_token",
                                 max_files=0)
        _full_dirty = disk_scope_scan(str(_dirty), "vl_selfcheck_anchor_token")
        _full_clean = disk_scope_scan(str(_clean), "vl_selfcheck_anchor_token")
        _pick_t = pick_brain_only_fact(_db, str(_clean), scan_max_files=0)
        _pick_d = pick_brain_only_fact(_db, str(_dirty))
        _pick_c = pick_brain_only_fact(_db, str(_clean))
        _why_t = (_pick_t["rejected"][0]["why"] if _pick_t["rejected"] else "")
        _why_d = (_pick_d["rejected"][0]["why"] if _pick_d["rejected"] else "")
        add("C10_a_truncated_scope_scan_is_not_a_negative",
            (_trunc["truncated"] is True and _trunc["found"] == []
             and _full_clean["truncated"] is False and _full_clean["found"] == []
             and len(_full_dirty["found"]) == 1
             and _pick_t["chosen"] is None and "truncated" in _why_t
             and _pick_d["chosen"] is None and "on disk" in _why_d
             and _pick_c["chosen"] is not None
             and _pick_c["chosen"]["scope_scan_complete"] is True), 6,
            {"capped_scan": _trunc,
             "complete_scan_over_a_clean_scope":
                 {k: v for k, v in _full_clean.items() if k != "found"},
             "complete_scan_over_a_dirty_scope":
                 {"files_scanned": _full_dirty["files_scanned"],
                  "truncated": _full_dirty["truncated"],
                  "found": [os.path.basename(x) for x in _full_dirty["found"]]},
             "rejected_on_truncation": _why_t,
             "rejected_on_a_real_hit": _why_d,
             "chosen_when_the_scan_completed_and_found_nothing":
                 (_pick_c["chosen"] or {}).get("token"),
             "note": ("the GREEN leg is the third one: a refusal that also "
                      "refuses the clean case has not fixed the scan, it has "
                      "disabled G2. A negative that could not finish looking is "
                      "not a negative, and G2 selected on it would measure the "
                      "file system — the confound this module exists to "
                      "refuse.")})
    finally:
        shutil.rmtree(_root, ignore_errors=True)

    # 7. Only earned verdicts may reach the ledger — AND THE CALL SITE, NOT
    #    ONLY THE PREDICATE.  may_record() was asserted as a FUNCTION over a
    #    table of nine verdicts while nothing checked that the two ledger writes
    #    consult it: dropping the guard at both call sites writes
    #    {gate:G1, verdict:CONTROL_MISSING, observations:0} into the proof
    #    ledger with recorded=True and this check still green (executed). A
    #    guard tested where it is DEFINED rather than where it is USED is the
    #    same class as D-4.
    recordable = {v: may_record({"verdict": v}) for v in
                  (PROVEN, OBEYED, PRESENT, NOT_CONTRADICTED, CONFOUNDED,
                   CONTROL_MISSING, UNMEASURABLE, ABSENT, NOT_INSTALLED)}
    _c7dir = tempfile.mkdtemp(prefix="voidloom_sc_rec_", dir=fixture_root())
    try:
        _c7led = Path(_c7dir) / "c7_ledger.json"
        # an UNEARNED verdict, driven through the shipped path with record=True:
        # the runner does not exist, so the control never runs -> CONTROL_MISSING
        _c7bad = str(Path(_c7dir) / "c7_runner_that_does_not_exist")
        _r7 = live_run("codex", "G1", _c7bad, _c7dir, "", trials=2,
                       ledger=str(_c7led), record=True)
        _rows7 = (json.loads(_c7led.read_text()).get("entries", [])
                  if _c7led.exists() else [])
        # THE GREEN HALF: an EARNED verdict on the same path must still write.
        _c7ok = Path(_c7dir) / "ok_runner.sh"
        _c7ok.write_text('#!/bin/sh\n'
                         'if [ -n "$VOIDLOOM_CANARY_NONCE" ]; then\n'
                         '  printf "%s\\n" "$VOIDLOOM_CANARY_NONCE"\n'
                         'else\n'
                         "  printf \"I don't know\\n\"\n"
                         'fi\n')
        os.chmod(_c7ok, 0o755)
        _c7led2 = Path(_c7dir) / "c7_ledger_earned.json"
        _r7ok = live_run("codex", "G1", str(_c7ok), _c7dir, "", trials=2,
                         ledger=str(_c7led2), record=True)
        _rows7ok = (json.loads(_c7led2.read_text()).get("entries", [])
                    if _c7led2.exists() else [])
        add("C7_ledger_rejects_unearned_verdicts",
            (all(not recordable[v] for v in (CONFOUNDED, CONTROL_MISSING,
                                             UNMEASURABLE, ABSENT,
                                             NOT_INSTALLED))
             and all(recordable[v] for v in (PROVEN, OBEYED, PRESENT,
                                             NOT_CONTRADICTED))
             # the CALL SITE, driven:
             and _r7["verdict"] == CONTROL_MISSING
             and _r7["recorded"] is False
             and len(_rows7) == 0
             # and the earned one still lands, or the guard has removed the
             # ledger rather than protected it:
             and _r7ok["verdict"] == PROVEN
             and _r7ok["recorded"] is True
             and len(_rows7ok) == 1
             and _rows7ok[0]["verdict"] == PROVEN),
            len(recordable) + 4,
            {"recordable": recordable,
             "unearned_verdict_driven": _r7["verdict"],
             "unearned_recorded": _r7["recorded"],
             "ledger_rows_after_an_unearned_run": len(_rows7),
             "earned_verdict_driven": _r7ok["verdict"],
             "earned_recorded": _r7ok["recorded"],
             "ledger_rows_after_an_earned_run": len(_rows7ok),
             "note": ("C7 used to grade the predicate only. Both ledger call "
                      "sites are now driven: an unearned verdict must leave the "
                      "ledger EMPTY and an earned one must still fill it.")})
    finally:
        shutil.rmtree(_c7dir, ignore_errors=True)

    # 8. Invariants shared with the resolver module.
    src = Path(__file__).read_text(encoding="utf-8")
    bad = re.findall(r"os\.environ\.setdefault\(\s*[\"']CUDA_VISIBLE_DEVICES", src)
    forced = re.findall(r"env\[\"CUDA_VISIBLE_DEVICES\"\]\s*=\s*\"1\"", src)
    spawns = re.findall(r"subprocess\.(?:run|Popen|call|check_output)\(", src)
    guarded = re.findall(r"env=(?:child_env\(|env\b)", src)
    add("C8_gpu_law",
        (not bad and len(forced) >= 1 and len(spawns) > 0
         and len(guarded) >= len(spawns)), 2 + len(spawns),
        {"forbidden_setdefault_hits": len(bad),
         "forced_assignments": len(forced),
         "subprocess_spawns": len(spawns),
         "spawns_receiving_an_explicit_env": len(guarded),
         "note": ("COVERAGE, not presence: the law binds EVERY spawn, so the "
                  "check counts spawns against guarded spawns rather than "
                  "asserting the assignment appears somewhere in the file. "
                  "Asserting the site exists leaves a future third spawn "
                  "unguarded with this check still green.")})

    # 10b. The G2 candidate query needs a TOTAL ORDER too — same class.
    _orders = [o.strip() for o in
               re.findall(r"ORDER BY ([^\"]*?)\s+LIMIT", src)]
    _partial = [o for o in _orders
                if not re.search(r"\bid\s+(?:ASC|DESC)\s*$", o)]
    add("C12_brain_ORDER_BY_carries_a_total_order",
        (len(_orders) >= 1 and not _partial), len(_orders),
        {"order_by_clauses": _orders, "without_a_unique_tiebreak": _partial,
         "note": ("ties on (importance, created_at) would make the SAME brain "
                  "yield a different G2 question on two runs of this harness")})

    # 11. GB: the ENVELOPE SHAPE is part of the contract, not just the text.
    #     The single GB assertion used to compare additionalContext only, so a
    #     patch that preserved the bytes and changed the envelope exited 0. This
    #     leg needs a hook and a brain COPY on disk; without them it is
    #     UNMEASURABLE over zero observations, which is not a pass.
    if hook and db_copy and Path(hook).exists() and Path(db_copy).exists():
        _home = home or (_vp.resolve_home(None) if _vp else os.getcwd())
        _gb = gate_gb(platform_id, hook, db_copy, _home,
                      str(Path(__file__).resolve().parent))
        # THE DENOMINATOR IS THIS CHECK'S OWN EXERCISED CONJUNCTS, NOT GB's
        # comparison count.  C11 has three conjuncts; two are exercised on any
        # host (the bytes and the verdict) and the third — the envelope shape —
        # is exercised only when systemMessage is present in a leg. Publishing
        # GB's `observations` (4 objects compared) as C11's count borrowed a
        # number from a different object than the verdict, which is exactly the
        # substitution H1 exists to forbid, committed in the file H1 does not
        # reach. C11b below supplies the missing observation by FORCING the
        # shapes apart on a COPY.
        _c11_exercised = 2 + _gb["banner_claim_observations"]
        # C6: three-valued, because GB HAS a third answer and flattening it here
        # would be the same fault one layer in. When GB's own verdict is
        # UNMEASURABLE -- on this host the `systemMessage` half is absent from
        # BOTH legs, so the "banner untouched" claim has ZERO observations -- the
        # honest report is `ok=None`, not `False`. A run that could not look is
        # not a run that looked and found the thing broken; saying so is the
        # whole point of C5's rc 4, and rendering it as a FAIL would be a false
        # alarm in the opposite direction.
        _c11_ok = (None if _gb["verdict"] == "UNMEASURABLE" else
                   (_gb["PATCHED_EQUALS_EMITTER"] is True
                    and _gb["ENVELOPE_SHAPE_PRESERVED"] is True
                    and _gb["verdict"] == PROVEN))
        add("C11_gb_requires_the_envelope_shape_not_just_the_bytes",
            _c11_ok,
            _c11_exercised,
            {"PATCHED_EQUALS_EMITTER": _gb["PATCHED_EQUALS_EMITTER"],
             "ENVELOPE_SHAPE_PRESERVED": _gb["ENVELOPE_SHAPE_PRESERVED"],
             "verdict": _gb["verdict"],
             "banner_half_exercised": _gb["banner_half_exercised"],
             "banner_claim_observations": _gb["banner_claim_observations"],
             "exercised_observations": _c11_exercised,
             "gb_objects_compared": _gb["observations"],
             "banner_note": _gb["banner_note"],
             "note": ("the verdict requires BOTH. The published count is this "
                      "check's own exercised conjuncts; GB's object count is "
                      "reported beside it, not as it.")})

        # --- C11b: THE RED, RUNNABLE ---------------------------------------
        # On this host systemMessage is absent from BOTH legs, so C11's envelope
        # conjunct is vacuously true and a hard-coded True is invisible to the
        # suite (the surviving mutant M6). R3 and R4 both got a runnable RED —
        # _apply_budget_prefix_skipping and --selector legacy — and R1's did not,
        # so its red/green lived in a markdown file. This leg builds a hook COPY
        # that sets a banner flag INSIDE the patch range: the live leg then emits
        # systemMessage and the patched leg cannot, because the patch deletes the
        # lines that set it. The shapes are forced apart and GB must catch it.
        _sp_dir = tempfile.mkdtemp(prefix="voidloom_sc_shape_", dir=fixture_root())
        try:
            _src = read_text_bounded(hook, errors="strict").splitlines()
            _cd = _vp.consume_diff(hook) if _vp is not None else {"ok": False}
            _emit_ix = next((i for i, l in enumerate(_src)
                             if re.match(r"^\s*emit_envelope\(", l)), None)
            _main_ix = next((i for i, l in enumerate(_src)
                             if re.match(r"^def main\(\):\s*$", l)), None)
            if _cd.get("ok") and _emit_ix is not None and _main_ix is not None:
                _o = list(_src)
                # (2) AT THE EMIT SITE, which is AFTER the banner anchor and so
                #     OUTSIDE the deletion range: both legs keep this line.
                _o.insert(_emit_ix,
                          '    banner = ("VL_SHAPEPROBE_BANNER" '
                          'if __import__("os").environ.get("VL_SHAPEPROBE") '
                          'else banner)')
                # (1) INSIDE the range [def main()+1, banner_anchor-1]: only the
                #     LIVE leg keeps this line; the patch deletes it, so the
                #     patched leg cannot set the flag and emits no systemMessage.
                _o.insert(_main_ix + 1,
                          '    __import__("os").environ["VL_SHAPEPROBE"] = "1"')
                _sp = Path(_sp_dir) / "session_start_inject_SHAPEPROBE.py"
                _sp.write_text("\n".join(_o) + "\n", encoding="utf-8")
                _gbr = gate_gb(platform_id, str(_sp), db_copy, _home,
                               str(Path(__file__).resolve().parent))
                _live_sm = _gbr["live_hook_now"].get("has_systemMessage")
                _pat_sm = _gbr["patched_hook_now"].get("has_systemMessage")
                _forced = (_live_sm != _pat_sm)
                add("C11b_a_CHANGED_ENVELOPE_SHAPE_IS_CAUGHT",
                    (_forced
                     and _gbr["ENVELOPE_SHAPE_PRESERVED"] is False
                     and _gbr["verdict"] != PROVEN
                     and _gbr["banner_claim_observations"] == 1),
                    2 if _forced else 0,
                    {"shapes_were_forced_apart": _forced,
                     "live_has_systemMessage": _live_sm,
                     "patched_has_systemMessage": _pat_sm,
                     "ENVELOPE_SHAPE_PRESERVED":
                         _gbr["ENVELOPE_SHAPE_PRESERVED"],
                     "verdict": _gbr["verdict"],
                     "PATCHED_EQUALS_EMITTER": _gbr["PATCHED_EQUALS_EMITTER"],
                     "banner_claim_observations":
                         _gbr["banner_claim_observations"],
                     "probe_hook": str(_sp),
                     "note": ("THE RED, RUN EVERY TIME. Without it the envelope "
                              "conjunct has ZERO observations on this host and "
                              "a hard-coded True is undetectable — which is why "
                              "M6 survived. The probe is built on a COPY; the "
                              "live tree is never touched.")})
            else:
                add("C11b_a_CHANGED_ENVELOPE_SHAPE_IS_CAUGHT", None, 0,
                    {"reason": ("consume_diff could not locate the patch range "
                                "in the hook under test, so the shapes could "
                                "not be forced apart. UNMEASURABLE, not a "
                                "pass."),
                     "consume_diff": _cd,
                     "emit_anchor_index": _emit_ix,
                     "main_anchor_index": _main_ix})
        finally:
            shutil.rmtree(_sp_dir, ignore_errors=True)
    else:
        add("C11_gb_requires_the_envelope_shape_not_just_the_bytes", None, 0,
            {"reason": ("no --hook / --db-copy supplied, so GB could not run. "
                        "UNMEASURABLE over zero observations — never a pass."),
             "hook": hook, "db_copy": db_copy})
        add("C11b_a_CHANGED_ENVELOPE_SHAPE_IS_CAUGHT", None, 0,
            {"reason": ("no --hook / --db-copy supplied; the forced-shape probe "
                        "needs a hook on disk. UNMEASURABLE, never a pass."),
             "hook": hook, "db_copy": db_copy})

    if _hs is not None:
        scan = _hs.scan_trust_bypass(_hs.shipped_files())
        add("C9_no_trust_bypass_in_shipped_code", scan["code_hits"] == 0,
            scan["files_scanned"], scan)
    else:
        add("C9_no_trust_bypass_in_shipped_code", None, 0,
            {"reason": f"resolver module unimportable: {_HS_ERR}"})

    # --- C13: THE PROOF LEDGER IS AN OPERATOR PATH, FROM THIS SIDE TOO -----
    # CLASS-S swept into the harness that WRITES the ledger. The guard lives in
    # ledger_append; this drives it from live_run, which is where a real caller
    # reaches it, and requires the run to survive the refusal rather than crash.
    # A guard that turns a canary run into a traceback is a guard that gets
    # removed.
    if _hs is not None:
        _cl = tempfile.mkdtemp(prefix="voidloom_sc_ledpath_", dir=fixture_root())
        try:
            _victim = Path(_cl) / "operator_file.json"
            _vtext = json.dumps({"schema_version": 1, "entries": [],
                                 "THIS_IS": "the operator's own file"})
            _victim.write_text(_vtext, encoding="utf-8")
            _link = Path(_cl) / "proof_ledger.json"
            os.symlink("operator_file.json", _link)
            _run = Path(_cl) / "ok_runner.sh"
            _run.write_text('#!/bin/sh\n'
                            'if [ -n "$VOIDLOOM_CANARY_NONCE" ]; then\n'
                            '  printf "%s\\n" "$VOIDLOOM_CANARY_NONCE"\n'
                            'else\n'
                            "  printf \"I don't know\\n\"\n"
                            'fi\n')
            os.chmod(_run, 0o755)
            _rl = live_run("codex", "G1", str(_run), _cl, "", trials=1,
                           ledger=str(_link), record=True)
            # THE PREFIX, NOT ONLY THE FINAL COMPONENT. C13 proved the ledger
            # refuses a symlink at the LAST component and never exercised one in
            # the DIRECTORY CHAIN — and ledger_append is the ONE caller that
            # reaches guarded_write_* without passing through uninstall_plan's
            # block, so the `differs` limb is the only thing standing between
            # the proof channel and a directory link. EXECUTED (NR4): dropping
            # that limb left all three suites green and rewrote the operator's
            # ledger through the link.
            _pref = Path(_cl) / "realdir"
            _pref.mkdir()
            _pref_victim = _pref / "proof.json"
            _pref_text = json.dumps({"entries": [{"KEEP": "user rows"}],
                                     "schema_version": 1})
            _pref_victim.write_text(_pref_text, encoding="utf-8")
            os.symlink("realdir", str(Path(_cl) / "linkdir"))
            try:
                _hs.ledger_append(str(Path(_cl) / "linkdir" / "proof.json"),
                                  {"platform": "x", "verdict": "PROVEN"})
                _prefix_refused = False
            except Exception as _e:
                _prefix_refused = type(_e).__name__ == "PathIsNotWhatWeNamed"
            _prefix_intact = (_pref_victim.read_text(encoding="utf-8")
                              == _pref_text)
            add("C13_the_proof_ledger_is_never_written_through_a_symlink",
                (_rl["verdict"] == PROVEN                 # earned, so it TRIED
                 and _rl["recorded"] is False             # and was refused
                 and "refused the write" in (_rl.get("record_refused_reason")
                                             or "")
                 and _victim.read_text(encoding="utf-8") == _vtext
                 and _prefix_refused is True
                 and _prefix_intact is True), 6,
                {"verdict": _rl["verdict"], "recorded": _rl["recorded"],
                 "record_refused_reason": _rl.get("record_refused_reason"),
                 "append_through_a_DIRECTORY_symlink_refused": _prefix_refused,
                 "operator_ledger_behind_the_link_intact": _prefix_intact,
                 "operator_file_untouched":
                     _victim.read_text(encoding="utf-8") == _vtext,
                 "note": ("the verdict is EARNED here on purpose — a refusal "
                          "that only ever fires on unrecordable verdicts would "
                          "prove nothing about the path guard. The run must "
                          "also SURVIVE: recorded=False with a named reason, "
                          "not a traceback.")})
        finally:
            shutil.rmtree(_cl, ignore_errors=True)
    else:
        add("C13_the_proof_ledger_is_never_written_through_a_symlink", None, 0,
            {"reason": f"resolver module unimportable: {_HS_ERR}"})

    # --- X3: THE VERB, NOT THE FUNCTION. EVERY CLI SURFACE THROUGH main(),
    # ASSERTING THE ENVELOPE AND THE EXIT CODE ---------------------------
    # THE THIRD ARTIFACT'S HALF OF THE COVERAGE REPAIR. This file's refusals —
    # replay without a nonce, GB without a db copy, GB pointed at the LIVE
    # brain, a confounded probe — are each a property of the STATE, and each
    # was asserted by nothing at the surface a user actually meets. R5 (AMD 2)
    # requires CONTROL_MISSING / CONFOUNDED / UNMEASURABLE / PROVEN to be
    # reachable "by a real invocation rather than by a synthetic Observation";
    # that was proven for the FUNCTIONS. This proves it for the VERBS.
    import io as _io3
    import contextlib as _cl3

    def _ccli(argv):
        _b = _io3.StringIO()
        try:
            with _cl3.redirect_stdout(_b):
                _rc = main(list(argv))
        except SystemExit as _se:                 # argparse rejects a bad verb
            _rc = int(_se.code or 0)
        _out = _b.getvalue()
        try:
            _e = json.loads(_out)
        except ValueError:
            _e = {"status": None, "code": None}
        return {"rc": _rc, "status": _e.get("status"), "code": _e.get("code"),
                "text_bytes": len(_out)}

    _xcd = tempfile.mkdtemp(prefix="voidloom_sc_cli_", dir=fixture_root())
    try:
        _cverbs = {
            # replay with no nonce: a refusal, because "anything that looks
            # like our preamble" is a content match, not a transport proof.
            "replay_without_a_nonce": _ccli(["--gate", "G1", "--json",
                                             "--cwd", _xcd]),
            # GB with no writable copy: a proof run must not be able to move
            # its own referent.
            "gb_without_a_db_copy": _ccli(["--gate", "GB", "--json",
                                           "--cwd", _xcd]),
            # a confound probe over a directory that contains the answer, and
            # one over a directory that does not. BOTH legs, one verb.
            "confound_probe_clean": _ccli(["--confound-probe",
                                           "ZZZ_NO_SOURCE_HOLDS_THIS_TOKEN_ZZZ",
                                           "--json", "--cwd", _xcd]),
            # replay WITH a nonce that no transcript holds: not a refusal to
            # run, but a graded verdict — and the exit code must say so.
            "replay_with_an_absent_nonce": _ccli(
                ["--gate", "G1", "--json", "--cwd", _xcd,
                 "--nonce", "VLX_" + hashlib.sha256(
                     os.urandom(8)).hexdigest()[:12]]),
        }
        add("X3_every_CLI_verb_answers_on_BOTH_channels_and_they_agree",
            (_cverbs["replay_without_a_nonce"]["rc"] == 3
             and _cverbs["replay_without_a_nonce"]["code"]
                 == "replay_needs_nonce"
             and _cverbs["gb_without_a_db_copy"]["rc"] == 3
             and _cverbs["gb_without_a_db_copy"]["code"] == "gb_needs_db_copy"
             and _cverbs["gb_without_a_db_copy"]["status"] == "refused"
             # the CLEAN confound probe is the negative control: a probe that
             # refused unconditionally would pass the refusal legs above and
             # would have removed the verb.
             and _cverbs["confound_probe_clean"]["rc"] == 0
             and _cverbs["confound_probe_clean"]["status"] == "ok"
             and _cverbs["confound_probe_clean"]["code"] == "confound_clean"
             # a nonce nobody minted is NOT_INSTALLED / ABSENT territory: a
             # non-zero code, never a silent 0.
             and _cverbs["replay_with_an_absent_nonce"]["rc"] != 0), 4,
            {"per_verb": _cverbs,
             "note": ("main() was invoked from ONE site across all three "
                      "artifacts before this round, and it was in a different "
                      "file. Every advertised exit code here was asserted by "
                      "nothing; a suite that tests functions cannot see a verb "
                      "that stops refusing.")})
    finally:
        shutil.rmtree(_xcd, ignore_errors=True)

    # --- C14: A PUBLISHED OBSERVATION COUNT IS THE EXERCISED COUNT ---------
    # The emitter's H1, brought here. H1 lived only in the emitter, and this
    # file published exactly the substitution H1 forbids: C11 reported
    # observations=4 — GB's count of objects compared — while one of its three
    # conjuncts had ZERO observations on this host. The population is the SHAPE
    # of the pair, not one key spelling.
    _pairs = []
    for c in checks:
        d = c.get("detail")
        if not isinstance(d, dict):
            continue
        k = next((k for k in d
                  if k.endswith("_that_CONTAINED_the_row")
                  or k in ("exercised", "exercised_observations")), None)
        if k is not None:
            _pairs.append({"check": c["id"], "key": k,
                           "published_observations": c["observations"],
                           "exercised": d[k]})
    _bad_pairs = [p for p in _pairs
                  if p["published_observations"] != p["exercised"]]
    add("C14_published_observation_counts_are_exercised_counts",
        (None if not _pairs else not _bad_pairs), len(_pairs),
        {"pairs": _pairs, "mismatched": _bad_pairs,
         "note": ("selected on the SHAPE of the pair, not on a literal key "
                  "name, so a check that spells its exercised count "
                  "differently is still bound.")})

    asserts = [c for c in checks if c["kind"] == "assert"]
    obs = sum(c["observations"] for c in asserts)
    failed = [c["id"] for c in asserts if c["ok"] is False]
    unmeas = [c["id"] for c in asserts
              if c["ok"] is None or c["observations"] == 0]
    verdict = ("UNMEASURABLE" if obs == 0 else
               ("FAIL" if failed else
                ("PASS_WITH_UNMEASURABLE" if unmeas else "PASS")))
    return {"scope": "HARNESS LOGIC ONLY — this grades the refusals in this "
                     "file. It is NOT a platform verdict and must never be "
                     "reported as one.",
            "verdict": verdict, "observations": obs, "checks": checks,
            "failed": failed, "unmeasurable": unmeas}


# ===========================================================================
# CLI
# ===========================================================================
def envelope(status_s: str, code: str, data: Any) -> Dict[str, Any]:
    return {"status": status_s, "code": code, "data": data, "verb": "canary"}


def render(res: Dict[str, Any]) -> str:
    L = []
    L.append(f"Voidloom canary  {res.get('gate','?')}  platform={res.get('platform')}"
             f"  mode={res.get('mode')}")
    L.append(f"  gate       : {res.get('gate_meaning','')}")
    L.append(f"  VERDICT    : {res.get('verdict')}")
    L.append(f"  observations: {res.get('observations')}"
             + (f"   hits={res.get('hits')} rate={res.get('rate')}"
                if "hits" in res else ""))
    L.append(f"  reason     : {res.get('reason')}")
    ctrl = res.get("control")
    L.append(f"  control    : {'(not required for G0)' if ctrl is None else ctrl.get('status')}"
             + (f" — {ctrl.get('reason')}" if ctrl and ctrl.get("reason") else ""))
    if res.get("confounds"):
        for c in res["confounds"]:
            L.append(f"  CONFOUND   : {c['source']} :: {c['reason']} :: {c['matched']}")
    if res.get("mint_guard"):
        L.append(f"  mint guard : {res['mint_guard']}")
    if res.get("resolver"):
        L.append(f"  resolver   : {res['resolver']}")
    for s in (res.get("observation_sources") or [])[:6]:
        L.append(f"  observed in: {s}")
    L.append(f"  recorded   : {res.get('recorded')}")
    return "\n".join(L)


EXIT_CODE_KEY = "_exit_code"
HUMAN_KEY = "_human"


def _with(env: Dict[str, Any], rc: int, human: Optional[str] = None) -> Dict[str, Any]:
    env[EXIT_CODE_KEY] = rc
    env[HUMAN_KEY] = (json.dumps({k: v for k, v in env.items()
                                  if k not in (EXIT_CODE_KEY, HUMAN_KEY)},
                                 ensure_ascii=False, indent=2, default=str)
                      if human is None else human)
    return env


def run(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Parse argv, run the gate, RETURN the envelope (never prints)."""
    ap = argparse.ArgumentParser(
        prog="voidloom canary",
        description="G0/G1/G2/G3 harness. The control runs first and is mandatory.")
    ap.add_argument("--gate", default="G0", choices=["GB", "G0", "G1", "G2", "G3"])
    ap.add_argument("--platform", default="codex")
    ap.add_argument("--all", action="store_true",
                    help="replay G0 across every platform with a transcript reader")
    ap.add_argument("--replay", action="store_true",
                    help="read transcripts already on disk (zero API cost). "
                         "This is the DEFAULT when no --runner is given.")
    ap.add_argument("--minted-at", default=None,
                    help="UTC ISO-8601 instant the nonce was minted; "
                         "observations older than it are REJECTED")
    ap.add_argument("--home", default=None)
    ap.add_argument("--hook", default=None,
                    help="GB: path to the SessionStart hook under test")
    ap.add_argument("--db-copy", default=None,
                    help="GB: a writable COPY of the brain. NEVER the live brain.")
    ap.add_argument("--nonce", default=None,
                    help="the nonce to look for in replay mode")
    ap.add_argument("--runner", default=None,
                    help="shell command that takes a prompt on stdin and prints "
                         "the model's answer. Enables the model-invoking gates.")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--db", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--record", action="store_true",
                    help="write earned verdicts to the proof ledger")
    ap.add_argument("--confound-probe", nargs="+", default=None,
                    metavar="ANSWER",
                    help="run ONLY the confound scan for the given answer "
                         "token(s) and report. Use it BEFORE designing a canary "
                         "question: a question whose answer already sits in a "
                         "context source measures the file system.")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(list(argv) if argv is not None else None)
    except SystemExit as e:
        return _with(envelope("error", "usage", {"message": "invalid arguments; see --help"}),
                     int(getattr(e, "code", 2) or 2), ap.format_usage())
    cwd = args.cwd or os.getcwd()

    if args.self_check:
        res = self_check(hook=args.hook, db_copy=args.db_copy, home=args.home,
                         platform_id=args.platform)
        # C5 -- "CANNOT TELL" IS NOT "YES".
        #
        # PASS_WITH_UNMEASURABLE mapped to status ok and exit 0, identical to a
        # clean PASS. Measured: verdict PASS_WITH_UNMEASURABLE with THREE checks
        # at ok=None, exit 0 -- so this organ named its own uncertainty and then
        # handed the caller a code that erased it. Any script gating on `&&` saw
        # a clean pass over three questions nobody answered.
        #
        # rc 4 is INCOMPLETE in the exit-code taxonomy these organs have always
        # advertised (0 ok / 3 refused / 4 incomplete / 1 error), so the caller
        # can still distinguish "some checks did not run" from "a check failed".
        # Checked before changing it: the only consumers of this code are the
        # suite's own two assertions -- no cron, no install gate, no live path.
        env = envelope({"PASS": "ok", "PASS_WITH_UNMEASURABLE": "incomplete",
                        "FAIL": "error", "UNMEASURABLE": "refused"}[res["verdict"]],
                       "canary_self_check_" + res["verdict"].lower(), res)
        return _with(env, {"ok": 0, "refused": 3, "incomplete": 4}.get(env["status"], 1))

    if args.confound_probe:
        srcs = context_sources_in_scope(cwd, args.platform)
        hits = confound_scan(cwd, args.platform, args.confound_probe)
        data = {"platform": args.platform, "cwd": cwd,
                "answers_probed": args.confound_probe,
                "context_sources_checked": srcs,
                "sources_checked_count": len(srcs),
                "confounds": [asdict(h) for h in hits],
                "confound_count": len(hits),
                "verdict": (CONFOUNDED if hits else "CLEAN"),
                "reason": (
                    "at least one context source in scope contains an answer: a "
                    "canary asked this question would measure the file system, "
                    "not the mechanism" if hits else
                    f"none of the {len(srcs)} context source(s) in scope contain "
                    f"any probed answer")}
        env = envelope("refused" if hits else "ok",
                       "confound_" + data["verdict"].lower(), data)
        return _with(env, 3 if hits else 0)

    if args.gate == "GB":
        home = args.home or (_vp.resolve_home(None) if _vp else os.getcwd())
        hook = args.hook or str(Path(home) / "hooks" / "session_start_inject.py")
        db_copy = args.db_copy
        if not db_copy:
            env = envelope("refused", "gb_needs_db_copy", {
                "message": ("GB runs the hook, and the hook reads the brain. "
                            "Pass --db-copy pointing at a COPY: a proof run must "
                            "not be able to move its own referent.")})
            return _with(env, 3)
        if _vp is not None and Path(db_copy).resolve() == Path(
                _vp.resolve_db(home)).resolve():
            env = envelope("refused", "db_copy_is_live_brain",
                           {"message": "--db-copy must be a COPY, never the live brain"})
            return _with(env, 3)
        results = [gate_gb(args.platform, hook, db_copy, home,
                           str(Path(__file__).resolve().parent))]
        env = envelope("ok", "canary_gb", {"results": results, "generated_at": utc_now()})
        # THE EXIT CODE FOLLOWS THE VERDICT, and the verdict now requires the
        # ENVELOPE SHAPE as well as the bytes. Keying it to
        # PATCHED_EQUALS_EMITTER alone meant a patch that preserved the text and
        # changed the envelope exited 0.
        return _with(env, 0 if results[0]["verdict"] == PROVEN else 1,
                     json.dumps(results[0], ensure_ascii=False, indent=2, default=str))

    if args.runner:
        db = args.db or (_vp.resolve_db(_vp.resolve_home(None)) if _vp else "")
        res = live_run(args.platform, args.gate, args.runner, cwd, db,
                       trials=args.trials, ledger=args.ledger, record=args.record)
        results = [res]
    else:
        nonce = args.nonce
        if not nonce:
            env = envelope("refused", "replay_needs_nonce", {
                "message": ("replay mode looks for a specific nonce in the "
                            "platform's own transcript. Pass --nonce, or pass "
                            "--runner to execute a live gate. Searching for "
                            "'anything that looks like our preamble' is how a "
                            "content match gets mistaken for a transport proof.")})
            return _with(env, 3)
        targets = list(TRANSCRIPTS) if args.all else [args.platform]
        results = [replay_run(p, nonce, cwd, args.ledger, args.record,
                              minted_at=args.minted_at)
                   for p in targets]

    env = envelope("ok", "canary", {"results": results, "generated_at": utc_now()})
    worst = {NOT_INSTALLED: 3, UNMEASURABLE: 3, CONFOUNDED: 3,
             CONTROL_MISSING: 3, NOT_RUN: 3, ABSENT: 1}
    codes = [worst.get(r.get("verdict"), 0) for r in results]
    return _with(env, max(codes) if codes else 0,
                 "\n\n".join(render(r) for r in results))


def emit(env: Dict[str, Any], as_json: bool) -> int:
    rc = int(env.pop(EXIT_CODE_KEY, {"ok": 0, "refused": 3}.get(env.get("status"), 1)))
    human = env.pop(HUMAN_KEY, None)
    if as_json or human is None:
        print(json.dumps(env, ensure_ascii=False, indent=2, default=str))
    else:
        print(human)
    return rc


def main(argv: Optional[Sequence[str]] = None) -> int:
    args_l = list(argv) if argv is not None else sys.argv[1:]
    return emit(run(args_l), "--json" in args_l)


if __name__ == "__main__":
    sys.exit(main())

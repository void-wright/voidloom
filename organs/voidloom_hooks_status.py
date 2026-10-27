#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_hooks_status.py — THE RESOLVER'S VIEW, and the undo.

TWO VERBS, ONE PREMISE
----------------------
The failure class that has dominated this entire build is not "the install
failed".  It is "the install succeeded, the config is present, the path is
correct, and NOTHING FIRED".  A status command that prints what the config file
SAYS cannot see that failure — it will happily report a healthy install for a
hook that never runs.  So everything here reports what a RESOLVER would actually
find: which files exist, which one the platform would pick, whether the platform
CONCATENATES its instruction sources or SELECTS one and discards the rest, and
when a canary last OBSERVED the context arriving.

    --status              the resolver's view, per platform (+ unreceipted markers)
    --install --platform  the install: losslessness-gated, post-imaged, element-undoable
    --uninstall           the undo, which names what it CANNOT undo

WHY `last_proven` HAS A TTL, AND WHY EXPIRY RENDERS AS AN ABSENCE
------------------------------------------------------------------
The governing principle was "a platform is SUPPORTED only when a canary has
demonstrated the context arriving".  Priced honestly that is ~15 platforms x ~2
mechanisms = ~30 canary configurations, EACH needing a vendor account, and we
hold three.  So under that principle roughly 12 of 15 platforms are permanently
installed-and-unprovable BY CONSTRUCTION — and a stop-loss that can never fire
is worse than none, because it is believed.

The replacement is mechanical and needs no judgment: A PLATFORM NOT CANARIED
WITHIN 30 DAYS AUTO-DEMOTES.  A row past its TTL renders as **EXPIRED** — an
absence — NEVER as its old value.  The vocabulary is load-bearing: "unverified"
reads as a caveat on a claim and gets skimmed; "EXPIRED" reads as an absence and
gets acted on.  And ``--status`` REFUSES to print a coverage percentage while any
row is expired, because a capability claim with no expiry sitting in the same
table, in the same typeface, as a proven one is how nobody — including us — can
remember later which was which.

WHY THE UNINSTALLER PRINTS RESIDUE
----------------------------------
``grep -ci uninstall install.sh`` returns 0: there is no undo path anywhere in
this system today.  Four mechanisms cannot be cleanly undone, and THE DELIVERED
CONTENT NEVER CAN.  That last one is the honest core of the backdoor question —
what makes a mechanism resemble a backdoor is precisely that uninstalling it
does not undo it.  So the residue is printed BY NAME on every run, dry or wet,
and the plan is printed BEFORE anything is written.

GPU: this module touches no GPU and spawns no GPU work.  It therefore makes no
``CUDA_VISIBLE_DEVICES`` assignment — and never the forbidden
``os.environ.setdefault`` form, which yields to an existing OR EMPTY value.
NETWORK: none.  Both are asserted by ``--self-check``.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
TTL_DAYS = 30

# The dated snapshot this table encodes.  A capability table is a claim about
# someone else's software ON ONE DAY; every row therefore carries its own
# verification state and date, and the canary is what converts a row from
# SURVEYED to EXECUTED.
MATRIX_SURVEY_DATE = "2026-09-08"

# --- semantics vocabulary --------------------------------------------------
# CONCATENATES: every discovered source is delivered; adding ours is additive.
# SELECTS:      the platform picks ONE and discards the rest, so our block can
#               ARRIVE AND BE OUTRANKED. A canary that does not run in a
#               directory holding a COMPETING file never tests the case that
#               actually breaks.
CONCAT = "CONCATENATES"
SELECT = "SELECTS (first-match-wins — our block can arrive AND be outranked)"
NA = "N/A (no session-start injection)"


def _p(*parts: str) -> str:
    return str(Path(*parts).expanduser())


PLATFORMS: List[Dict[str, Any]] = [
    {
        "id": "claude-code",
        "name": "Claude Code",
        "mechanism": "SessionStart hook -> hookSpecificOutput.additionalContext",
        "semantics": CONCAT,
        "config_paths": ["{claude_config}/settings.json",
                         "{claude_config}/settings.local.json",
                         ".claude/settings.json", ".claude/settings.local.json"],
        "probe": "claude_settings",
        "verification": {"state": "EXECUTED", "date": MATRIX_SURVEY_DATE,
                         "method": "hook installed + running on this machine"},
    },
    {
        "id": "codex",
        "name": "OpenAI Codex CLI",
        "mechanism": ("SessionStart hook in $CODEX_HOME/hooks.json (Claude-style shape; "
                      "EXECUTED 2026-09-14: source=user) -> developer message; trust is "
                      "hash-pinned in config.toml [hooks.state.\"<abs>/hooks.json:...\"]"),
        "semantics": CONCAT,
        "config_paths": ["{codex_home}/hooks.json", "{codex_home}/config.toml"],
        "probe": "codex_hooks",
        # The flag that invalidated the earlier demo is described, never spelled:
        # R8's check is "zero hits over shipped code", and a literal in a data
        # table is indistinguishable from a literal in an argv list to any grep
        # a reviewer would actually run.
        "verification": {"state": "EXECUTED", "date": "2026-09-14",
                         "method": "unmodified hook fired under codex 0.153.4 from a "
                                   "user-trusted $CODEX_HOME/hooks.json (the user ran "
                                   "/hooks once); heartbeat joined to the platform's "
                                   "own rollout; no bypass flag anywhere in shipped code"},
        "notes": ["hooks are hash-pinned: a changed hook is marked for review "
                  "and SKIPPED SILENTLY until the user runs /hooks and trusts it",
                  "keep the `command` STRING permanently stable so re-approval "
                  "is never triggered",
                  "the trust key embeds the ABSOLUTE PATH of hooks.json: relocating "
                  "CODEX_HOME silently re-untrusts the hook",
                  "additionalContextLimit is pinned at install (6000; vendor default "
                  "2,500 then spill) because it is part of the trusted hash",
                  "project_doc_fallback_filenames can make CLAUDE.md load as "
                  "AGENTS.md — a CONFOUND for any content-based canary"],
    },
    {
        "id": "gemini-cli",
        "name": "Gemini CLI",
        "mechanism": "SessionStart -> additionalContext (advisory; never blocks startup)",
        "semantics": CONCAT,
        "config_paths": ["~/.gemini/settings.json"],
        "probe": "json_hooks",
        "verification": {"state": "SURVEYED", "date": MATRIX_SURVEY_DATE,
                         "method": "documentation only — never executed here"},
    },
    {
        "id": "qwen-code",
        "name": "Qwen Code",
        "mechanism": "SessionStart -> additionalContext wrapped in <qwen:...> markers",
        "semantics": CONCAT,
        "config_paths": ["~/.qwen/settings.json"],
        "probe": "json_hooks",
        "verification": {"state": "SURVEYED", "date": MATRIX_SURVEY_DATE,
                         "method": "documentation only — never executed here"},
    },
    {
        "id": "copilot-cli",
        "name": "GitHub Copilot CLI",
        "mechanism": "sessionStart hook",
        "semantics": CONCAT,
        "config_paths": ["~/.copilot/hooks", ".github/hooks"],
        "probe": "path_exists",
        "verification": {"state": "OPEN_BUG", "date": MATRIX_SURVEY_DATE,
                         "method": "github/copilot-cli#1730 — sessionStart in "
                                   ".github/hooks does not fire in v0.0.420, and "
                                   "one doc page contradicts the hooks reference "
                                   "on whether output is consumed"},
    },
    {
        "id": "cursor-cli",
        "name": "Cursor CLI",
        "mechanism": "sessionStart -> additional_context + env",
        "semantics": CONCAT,
        "config_paths": ["~/.cursor/hooks.json"],
        "probe": "path_exists",
        "verification": {"state": "UNVERIFIED", "date": MATRIX_SURVEY_DATE,
                         "method": "unverified whether hooks fire under "
                                   "cursor-agent specifically"},
    },
    {
        "id": "openhands",
        "name": "OpenHands",
        "mechanism": ".openhands/hooks.json (snake_case, Claude-Code-compatible)",
        "semantics": CONCAT,
        "config_paths": [".openhands/hooks.json"],
        "probe": "path_exists",
        "verification": {"state": "SURVEYED", "date": MATRIX_SURVEY_DATE,
                         "method": "documentation only — never executed here"},
    },
    {
        "id": "kimi-code",
        "name": "Kimi Code",
        "mechanism": ("FILE tier: reads ~/.agents/AGENTS.md and ~/.kimi-code/AGENTS.md "
                      "natively (live-reloaded); SessionStart hooks are OBSERVE-ONLY, so "
                      "the only injecting hook would be UserPromptSubmit (per turn)"),
        "semantics": CONCAT,
        # Council #12 (2026-09-15): the 09-08 survey probed ~/.kimi/settings.json,
        # which does not exist; the installed 0.43.0 lives in ~/.kimi-code/.  A row
        # that probes the wrong home reports NOT_INSTALLED forever.
        "config_paths": ["~/.kimi-code/config.toml", "~/.agents/AGENTS.md",
                         "~/.kimi-code/AGENTS.md"],
        "probe": "file_tier",
        "verification": {"state": "SURVEYED", "date": "2026-09-14",
                         "method": "binary installed and logged in (0.43.0); the doctrine "
                                   "block was placed in ~/.agents/AGENTS.md; LIVE FIRE "
                                   "UNMEASURED — no subscription to run a session (#19249)"},
        "notes": ["FILE_ONLY tier: no hook fires here; the doctrine in the file IS the "
                  "activation, and the activation notice tells file-tier readers so",
                  "a UserPromptSubmit heartbeat writer is round-2 work (#19250)"],
    },
    {
        "id": "agents-md",
        "name": "AGENTS.md tier (Amp, opencode, Goose, Cursor, Continue, Warp, ...)",
        "mechanism": "instruction file, loaded natively",
        "semantics": SELECT,
        "config_paths": ["~/.agents/AGENTS.md", "AGENTS.md", "CLAUDE.md"],
        "probe": "instruction_chain",
        "verification": {"state": "SURVEYED", "date": MATRIX_SURVEY_DATE,
                         "method": "documentation + one live observation of the "
                                   "Codex fallback path"},
    },
    {
        "id": "goose",
        "name": "Goose",
        "mechanism": "12 lifecycle events, ZERO context injection on any of them",
        "semantics": NA,
        "config_paths": [],
        "probe": "none",
        "verification": {"state": "SURVEYED", "date": MATRIX_SURVEY_DATE,
                         "method": "documentation only"},
    },
]


# ===========================================================================
# The proof ledger — written by the canary, read here.
# ===========================================================================
def ledger_path(home: Optional[str] = None) -> str:
    env = os.environ.get("VOIDLOOM_CANARY_LEDGER")
    if env:
        return env
    base = home or os.environ.get("WIZARD_LIBRARY_HOME") or str(
        Path(__file__).resolve().parent.parent)
    return str(Path(base) / "voidloom_canary_ledger.json")


def utc_now() -> str:
    """ONE CLOCK PER COLUMN. Every timestamp this system writes is UTC, ISO-8601,
    Z-suffixed — a table carrying mixed local and UTC times is a silent join
    across two units, and it fails without an exception."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(ts: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


_FIXTURE_ROOT_ENV = "VOIDLOOM_SELFCHECK_FIXTURE_DIR"
# The wall-clock bound R7o's FIFO leg allows the PLAN to take.  Written as a
# bare literal on purpose: routing it through a shared constant would let one
# edit move the bound and the thing it bounds together.
NR_FIFO_TIMEOUT_S = 20.0


def fixture_root() -> str:
    """Where this suite's filesystem fixtures are built.  NOT tempfile's default.

    EVERY filesystem fixture in this suite landed on the default temp directory,
    which on this host is tmpfs -- and tmpfs is the ONE local filesystem where
    the shape that destroyed a user's file cannot occur.  ext4 hands a freed
    inode number straight back; tmpfs does not.  MEASURED on this host:
    unlink-then-create returns the SAME inode number 200/200 under $HOME (ext4)
    and 0/200 under /tmp (tmpfs).

    The consequence was not theoretical.  Iteration 4 published 5/5 shapes held
    on its own property battery with every fixture on tmpfs; the same battery,
    unchanged, on ext4 destroyed the user's file in 5 of 5 trials and reported
    RESTORED / verified_against_preimage=True / errors=0 / rc 0.

    A TEST WHOSE ENVIRONMENT DIFFERS FROM DEPLOYMENT IN EXACTLY THE PROPERTY
    UNDER TEST IS NOT A TEST.  Fixtures therefore live beside the shipped file,
    on the filesystem the operator's own data is on, and F1 asserts it.
    """
    override = os.environ.get(_FIXTURE_ROOT_ENV)
    root = (Path(override) if override
            else Path(__file__).resolve().parent / ".vl_selfcheck_fixtures")
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def fixture_dir(prefix: str) -> str:
    """A fresh fixture directory ON THE DEPLOYMENT FILESYSTEM.

    Every ``tempfile.mkdtemp`` in this suite goes through here.  F1 asserts by
    AST that none of them calls ``mkdtemp`` without a ``dir=``, so the next
    fixture someone writes cannot quietly fall back to tmpfs.
    """
    return tempfile.mkdtemp(prefix=prefix, dir=fixture_root())


def fs_recycles_inode_numbers(root: str) -> Optional[bool]:
    """Does THIS filesystem hand a freed inode number straight back?

    DERIVED at the moment of asking, never typed.  Published beside every
    fixture-based verdict so a reader can see whether the run was even capable
    of expressing the shape it claims to have tested.
    """
    d = None
    try:
        d = tempfile.mkdtemp(prefix="vl_sc_inoprobe_", dir=root)
        p = Path(d) / "probe"
        p.write_bytes(b"a")
        first = os.lstat(p).st_ino
        os.unlink(p)
        p.write_bytes(b"b")
        return os.lstat(p).st_ino == first
    except OSError:
        return None
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)


def read_bytes_if_regular(path: str) -> bytes:
    """Read a path a USER controls, without ever BLOCKING on it.

    CLASS-NR, SWEPT.  ``Path(p).read_text()``/``read_bytes()`` open with neither
    ``O_NONBLOCK`` nor a type check, so a FIFO at ANY operator-controlled path
    blocks the process forever.  The reflector found ONE instance and located it
    at the write-side open; EXECUTED, the verb actually hung at two OTHER sites
    first -- ``_sha256_file`` called from ``uninstall_plan``, and
    ``probe_instruction_chain`` called from ``status()`` -- and repairing the
    named one alone left the hang exactly where it was, at 20 s, twice.  A
    defect list is a set of SYMPTOMS; the class is the unit of work.

    Six production sites read a path the operator names: the ledger, the JSON
    config probe, the Codex config probe, the instruction-chain winner, the
    install receipt, and the pre-image.  All six route through here.

    Raises ``OSError(ENOTSUP)`` for a non-regular file, so every existing
    ``except OSError`` handler reports it exactly as it already reports an
    unreadable config -- no caller learns a new failure vocabulary.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        stt = os.fstat(fd)
        if not stat.S_ISREG(stt.st_mode):
            raise OSError(errno.ENOTSUP,
                          f"not a regular file (it is a {_file_type(stt)}); "
                          f"refusing to read it")
        buf = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            buf += chunk
        return buf
    finally:
        os.close(fd)


def read_text_if_regular(path: str, errors: str = "strict") -> str:
    return read_bytes_if_regular(path).decode("utf-8", errors=errors)


def ledger_load(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": [], "_absent": True}
    try:
        d = json.loads(read_text_if_regular(str(p)))
        if not isinstance(d, dict) or not isinstance(d.get("entries"), list):
            return {"schema_version": SCHEMA_VERSION, "entries": [],
                    "_malformed": True}
        return d
    except (OSError, ValueError) as e:
        return {"schema_version": SCHEMA_VERSION, "entries": [],
                "_error": f"{type(e).__name__}: {e}"}


def _ledger_readable(path: str) -> bool:
    """Is the ledger's CONTENT KNOWABLE?  Not: does a file exist at that name.

    An UNKNOWN published as a zero is the failure this exists to stop: a ledger
    that exists and will not parse used to render exactly like a healthy empty
    one, and coverage printed 0.0 for it.  "I cannot measure" and "I measured
    zero" must never share an output.

    ABSENT IS ON THE KNOWABLE SIDE OF THAT LINE, and getting this wrong is a
    defect I shipped for twenty minutes and caught with a CLI smoke test:
    folding ``_absent`` in here made ``--status`` refuse and exit 3 on the
    ordinary first-run case, where no canary has ever written proof. "There is
    no file" is an OBSERVATION — zero proofs, and the renderer has said so by
    name since iteration 1. "There is a file and I cannot read it" is the
    absence of an observation. Only the second is UNKNOWN.
    """
    d = ledger_load(path)
    return not (d.get("_malformed") or d.get("_error"))


def ledger_fault(path: str) -> Optional[str]:
    """The reason the content is UNKNOWABLE, or None. Absence is not a fault —
    it is carried by ``ledger_present`` and named by the renderer."""
    d = ledger_load(path)
    if d.get("_malformed"):
        return "malformed"
    if d.get("_error"):
        return str(d["_error"])
    return None


def ledger_append(path: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    d = ledger_load(path)
    # AN UNREADABLE LEDGER IS NOT AN EMPTY ONE.  Appending to it would silently
    # DISCARD every proof row already recorded and replace the file with a
    # one-row ledger that looks healthy — an UNKNOWN overwritten by a fresh
    # green. Refuse, and say which fault it was.
    if d.get("_malformed") or d.get("_error"):
        raise ValueError(
            f"the proof ledger at {path} exists but could not be read "
            f"({ledger_fault(path)}); refusing to append, because appending "
            f"would replace an unreadable ledger with a one-row ledger that "
            f"looks complete. Move it aside by hand.")
    d.pop("_absent", None)
    d.pop("_malformed", None)
    d["schema_version"] = SCHEMA_VERSION
    d["entries"].append(entry)
    guarded_mkdir_chain(str(Path(path).parent))     # the class of P2: gated, nothing created on refusal
    # CLASS-S: the ledger path is an operator-owned path like any other.
    guarded_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    return d


def freshness(observed_at: Optional[str], ttl_days: int = TTL_DAYS) -> Dict[str, Any]:
    """EXPIRED IS AN ABSENCE, NOT A STALE VALUE.

    Past the TTL this returns state=EXPIRED and DOES NOT return the old verdict
    in a field a renderer could accidentally print. That asymmetry is the whole
    point: the value must become unavailable, not merely annotated.
    """
    if not observed_at:
        return {"state": "NEVER", "observed_at": None, "age_days": None,
                "ttl_days": ttl_days}
    t = parse_utc(observed_at)
    if t is None:
        return {"state": "UNPARSEABLE", "observed_at": observed_at,
                "age_days": None, "ttl_days": ttl_days}
    age = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    if age > ttl_days:
        # THE VALUE BECOMES UNAVAILABLE, NOT MERELY ANNOTATED.  This branch used
        # to return observed_at, contradicting the docstring above it, and only
        # ledger_view() stopped the leak — so every FUTURE reader of this public
        # function inherited the leak by default. That is the provenance-frame
        # failure reproduced in the TTL layer: a defence that each reader must
        # remember to re-implement is a defence that one of them will not.
        # age_days is KEPT because "how stale" is actionable; observed_at is
        # WITHHELD because it is the field a renderer prints as if it were
        # current.
        return {"state": "EXPIRED", "observed_at": None,
                "withheld_observed_at": True,
                "age_days": round(age, 2), "ttl_days": ttl_days}
    return {"state": "FRESH", "observed_at": observed_at,
            "age_days": round(age, 2), "ttl_days": ttl_days}


def carries_a_value(f: Dict[str, Any]) -> bool:
    """Does this ``freshness()`` result hand a renderer something it could print
    as if it were current?

    ONE function, named, used by the guarantee AND by the check that grades it.
    The check used to be an inline expression ending in ``and False``, which
    pinned the only TTL-leak detector in the file to False unconditionally and
    then published the result as the field "leak". A detector that cannot fire
    is not a detector, and the self-check now drives this function over a
    KNOWN-LEAKY row as well as over the real one, so a pinned constant fails.
    """
    return f.get("observed_at") is not None


def ledger_view(path: str, platform_id: str) -> Dict[str, Any]:
    d = ledger_load(path)
    rows = [e for e in d["entries"] if e.get("platform") == platform_id]
    # ledger_load DISTINGUISHES THREE FAILURE STATES and only _absent used to
    # survive: a corrupt ledger read `present` and its entry_count of 0 was
    # published as if it were a measurement. PRESENCE IS NOT READABILITY.
    readable = not (d.get("_malformed") or d.get("_error"))
    out: Dict[str, Any] = {"ledger_present": not d.get("_absent", False),
                           "ledger_readable": readable,
                           "ledger_fault": (None if readable else
                                            ("malformed" if d.get("_malformed")
                                             else str(d.get("_error")))),
                           # UNKNOWN, NOT ZERO. A count over a file that would
                           # not parse is not a count of anything.
                           "entry_count": (len(rows) if readable else None)}
    # EVERY GATE THAT CAN BE WRITTEN MUST BE READABLE.  may_record() admits
    # NOT_CONTRADICTED, so a G3 row was written to this ledger and rendered by
    # nothing — a value writable to a store with no read path, and G3 is the
    # rung that tests the SELECTS platforms the design says can "arrive and be
    # outranked".  And the verdict filter was gate-blind: it accepted any of
    # PRESENT/PROVEN/OBEYED for any gate, so a mis-gated row populated the wrong
    # column (executed: gate=G1 verdict=PRESENT rendered as last_proven FRESH).
    for gate, key, ok_verdicts in (("G1", "last_proven", ("PROVEN",)),
                                   ("G2", "last_obeyed", ("OBEYED",)),
                                   ("G0", "last_present", ("PRESENT",)),
                                   ("G3", "last_uncontradicted",
                                    ("NOT_CONTRADICTED",))):
        hits = [e for e in rows
                if e.get("gate") == gate and e.get("verdict") in ok_verdicts]
        latest = max((e.get("observed_at") or "" for e in hits), default="")
        f = freshness(latest or None)
        # CRUCIBLE-3 D01-F5: the DENOMINATOR was never published. `observations`
        # below is copied from the single winning ROW and is frequently null, so
        # `k` -- how many rows stand behind this award -- was not recoverable from
        # the envelope at all, while the verdict still rendered FRESH. The module
        # uses None as its deliberate "UNKNOWN, not zero" marker three functions
        # up (`:501`), so it already knows how to say "I cannot count this" and
        # awarded PROVEN anyway. `k` is that count, and it is always present.
        out[key] = {"state": f["state"], "ttl_days": f["ttl_days"], "k": len(hits)}
        # The value is carried ONLY while FRESH. Past TTL the renderer has
        # nothing to print but the absence.
        if f["state"] == "FRESH":
            out[key]["observed_at"] = f["observed_at"]
            out[key]["age_days"] = f["age_days"]
            src = next((e for e in hits if e.get("observed_at") == latest), {})
            _obs = src.get("observations")
            out[key]["observations"] = _obs
            # Say WHICH of the two nulls this is: "the winning row did not carry a
            # count" is not "the count is zero", and only `k` above is the number
            # of rows this award actually rests on.
            out[key]["observations_known"] = _obs is not None
    return out


# ===========================================================================
# Resolver probes — what a resolver would ACTUALLY find, not what config says.
# ===========================================================================
EMPTY_SETTINGS_NOTE = "empty settings file treated as {} (as Claude Code reads it)"


def _read_json(path: str, empty_is_object: bool = False) -> Tuple[Optional[Any], Optional[str]]:
    """(data, error). ``empty_is_object``: a regular file whose content is empty or whitespace-only is ``{}`` --
    how Claude Code itself reads an empty settings file (iteration 2: a 0-byte ~/.claude/settings.local.json made
    doctor report the settings chain broken and brain_healthy false on a host whose hooks were fine). A truncated
    or otherwise invalid JSON body stays an error: that file IS skipped by the platform."""
    try:
        text = read_text_if_regular(path)
        if empty_is_object and not text.strip():
            return {}, None
        return json.loads(text), None
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def probe_claude_settings(paths: Sequence[str], cwd: str) -> Dict[str, Any]:
    """Claude Code merges hooks across the settings chain and RUNS THEM ALL.

    So the resolved answer is a LIST, and 'is our hook installed' is a question
    about membership in that list — not about whether one file mentions it.
    """
    found: List[Dict[str, Any]] = []
    for rel in paths:
        ap = _expand_platform_path(rel, cwd)
        data, err = _read_json(ap, empty_is_object=True)
        entry: Dict[str, Any] = {"path": ap, "exists": Path(ap).exists()}
        if err and err != "absent":
            entry["error"] = err
        elif data == {} and entry["exists"]:
            try:
                if not read_text_if_regular(ap).strip():
                    entry["note"] = EMPTY_SETTINGS_NOTE
            except OSError:
                pass
        cmds: List[str] = []
        groups: Any = None
        if isinstance(data, dict):
            groups = (data.get("hooks", {}) or {}).get("SessionStart", []) or []
            for group in groups:
                for h in (group or {}).get("hooks", []) or []:
                    c = h.get("command")
                    if c:
                        cmds.append(str(c))
        entry["session_start_commands"] = cmds
        # THE MARKER, NOT THE WORD: which elements are OURS, by the hook basename.
        entry["our_element_indices"] = find_our_elements(groups)
        found.append(entry)
    all_cmds = [c for e in found for c in e["session_start_commands"]]
    ours = [c for c in all_cmds if HOOK_MARKER_RE.search(c)]
    return {"sources": found, "resolved_command_count": len(all_cmds),
            "resolved_commands": all_cmds, "ours": ours,
            "installed": bool(ours)}


def probe_codex_config(paths: Sequence[str], cwd: str) -> Dict[str, Any]:
    ap = _expand_platform_path(paths[0], cwd)
    out: Dict[str, Any] = {"path": ap, "exists": Path(ap).exists()}
    if not out["exists"]:
        out["installed"] = False
        out["reason"] = "config absent"
        return out
    try:
        txt = read_text_if_regular(ap)
    except OSError as e:
        out["installed"] = False
        out["reason"] = f"{type(e).__name__}: {e}"
        return out
    # Deliberately a text probe, not a TOML parse: tomllib is 3.11+, and the
    # question here is "does a resolver see a [hooks] table", which the text
    # answers without adding a version floor to a status command.
    has_hooks = re.search(r"^\s*\[hooks", txt, flags=re.M) is not None
    trust_states = re.findall(r"^\s*\[hooks\.state\.([^\]]+)\]", txt, flags=re.M)
    fallback = re.search(r"^\s*project_doc_fallback_filenames\s*=\s*(.+)$",
                         txt, flags=re.M)
    out.update({
        "installed": has_hooks,
        "hooks_table_present": has_hooks,
        "trust_state_keys": trust_states,
        "project_doc_fallback_filenames": (fallback.group(1).strip()
                                           if fallback else None),
        "confound_warning": (
            "project_doc_fallback_filenames is SET: this platform will load the "
            "named file as its instruction document, so ANY content-based canary "
            "run here is CONFOUNDED unless that file is proven not to contain the "
            "answer." if fallback else None),
    })
    return out


def probe_json_hooks(paths: Sequence[str], cwd: str) -> Dict[str, Any]:
    srcs = []
    installed = False
    for rel in paths:
        ap = _expand_platform_path(rel, cwd)
        data, err = _read_json(ap)
        e: Dict[str, Any] = {"path": ap, "exists": Path(ap).exists()}
        if err and err != "absent":
            e["error"] = err
        if isinstance(data, dict) and "hooks" in data:
            blob = json.dumps(data.get("hooks"))
            e["mentions_voidloom"] = ("voidloom" in blob
                                      or "session_start_inject" in blob)
            installed = installed or e["mentions_voidloom"]
        srcs.append(e)
    return {"sources": srcs, "installed": installed}


def probe_path_exists(paths: Sequence[str], cwd: str) -> Dict[str, Any]:
    srcs = []
    for rel in paths:
        ap = _expand_platform_path(rel, cwd)
        srcs.append({"path": ap, "exists": Path(ap).exists()})
    return {"sources": srcs, "installed": False,
            "reason": "presence of a hooks directory is not evidence a hook fired"}


DOCTRINE_MARKER_BEGIN = "# >>> VOIDLOOM DOCTRINE BEGIN >>>"
# The closing half. D05-F4: a block is a PAIR, and every detector in this system
# keyed on the opening line alone, so the END marker had no constant to compare to.
DOCTRINE_MARKER_END = "# <<< VOIDLOOM DOCTRINE END <<<"


def probe_file_tier(paths: Sequence[str], cwd: str) -> Dict[str, Any]:
    """A platform with NO injecting hook: the product's whole delivery is a file
    the platform reads natively.  `installed` here means the DOCTRINE block is
    present in at least one of the platform's instruction files — and the
    row says FILE_ONLY, never LIVE, because nothing can fire.  The first path
    is the platform's home marker (its config), reported as `platform_present`."""
    srcs = []
    doctrine_in = []
    platform_present = None
    for i, rel in enumerate(paths):
        ap = _expand_platform_path(rel, cwd)
        exists = Path(ap).exists()
        ent: Dict[str, Any] = {"path": ap, "exists": exists}
        if i == 0:
            platform_present = exists
            ent["role"] = "platform home marker"
        elif exists:
            try:
                txt = read_text_if_regular(ap, errors="replace")
                # D05-F4: THE PAIR, NOT THE OPENING HALF. Keying on
                # MARKER_BEGIN alone meant one byte in that line -- an editor, a
                # merge, `/init` -- made a LIVE block invisible to the orphan
                # hunter that exists for exactly this, while the closing marker
                # and the directive between them still reached the model every
                # session. Either half is evidence; a lone half is DAMAGE, and it
                # is reported as such rather than as absence.
                _d_b, _d_e = DOCTRINE_MARKER_BEGIN in txt, DOCTRINE_MARKER_END in txt
                _n_b, _n_e = MARKER_BEGIN in txt, MARKER_END in txt
                ent["carries_doctrine"] = _d_b or _d_e
                ent["carries_notice"] = _n_b or _n_e
                ent["marker_damaged"] = (_n_b != _n_e) or (_d_b != _d_e)
                if ent["marker_damaged"]:
                    ent["damaged_detail"] = (
                        f"notice BEGIN={_n_b} END={_n_e}; doctrine BEGIN={_d_b} END={_d_e}"
                        " -- half a managed block is present. The remaining half is"
                        " still read every session; an install would append a SECOND"
                        " block beside it and an uninstall would report clean over it.")
                if ent["carries_doctrine"]:
                    doctrine_in.append(ap)
            except OSError as e:
                ent["error"] = f"{type(e).__name__}: {e}"
        srcs.append(ent)
    return {"sources": srcs, "installed": bool(doctrine_in),
            "tier": "file-only", "platform_present": platform_present,
            "doctrine_in": doctrine_in,
            "reason": ("the doctrine block is present in a file this platform reads; "
                       "no hook exists to fire, so the row can never read LIVE"
                       if doctrine_in else
                       "no doctrine block in any file this platform reads")}


def probe_instruction_chain(paths: Sequence[str], cwd: str) -> Dict[str, Any]:
    """SELECTS semantics: report the chain AND which entry wins.

    The winner is what matters. On a first-match-wins platform our block can be
    delivered and still be outranked by a competing file — which is precisely
    the case a canary run in an empty directory never tests.
    """
    chain = []
    for rel in paths:
        ap = _expand_platform_path(rel, cwd)
        ex = Path(ap).exists()
        size = Path(ap).stat().st_size if ex else 0
        chain.append({"path": ap, "exists": ex, "bytes": size})
    winner = next((c for c in chain if c["exists"]), None)
    marked = False
    if winner:
        try:
            # THE MARKER, NOT THE WORD.  Matching on the bare string "Voidloom"
            # reported this project's own CLAUDE.md as carrying our install,
            # because that file happens to DESCRIBE voidloom.  A status verb
            # whose whole premise is "report what the resolver found, not what
            # you believe" cannot itself answer an install question with a
            # substring that any prose could satisfy.  Only the marker block
            # the installer writes counts as evidence the installer ran.
            # D05-F4: either half. A mangled BEGIN used to read as "not ours".
            _wtxt = read_text_if_regular(winner["path"], errors="replace")
            marked = (MARKER_BEGIN in _wtxt) or (MARKER_END in _wtxt)
        except OSError:
            marked = False
    return {"chain": chain, "resolved_winner": winner,
            "winner_carries_our_marker": marked, "installed": bool(marked),
            "note": "entries after the winner are DISCARDED by a SELECTS platform"}


PROBES = {
    "claude_settings": probe_claude_settings,
    "codex_config": probe_codex_config,
    # bound after the install section defines probe_codex_hooks (below)

    "json_hooks": probe_json_hooks,
    "path_exists": probe_path_exists,
    "file_tier": probe_file_tier,
    "instruction_chain": probe_instruction_chain,
    "none": lambda paths, cwd: {"installed": False,
                                "reason": "platform offers no injection point"},
}


def resolve_platform(entry: Dict[str, Any], cwd: str,
                     ledger: Optional[str] = None,
                     probe_platform: bool = False) -> Dict[str, Any]:
    probe = PROBES.get(entry.get("probe", "none"), PROBES["none"])
    try:
        # ONLY the Codex probe can spawn a vendor process, and only under consent.
        if entry.get("probe") == "codex_hooks":
            resolved = probe(entry.get("config_paths", []), cwd, probe_platform=probe_platform)
        else:
            resolved = probe(entry.get("config_paths", []), cwd)
    except Exception as e:                                # never crash a status
        resolved = {"installed": False,
                    "error": f"{type(e).__name__}: {e}"}
    row = {
        "id": entry["id"], "name": entry["name"],
        "mechanism": entry["mechanism"], "semantics": entry["semantics"],
        "verification": entry["verification"],
        "notes": entry.get("notes", []),
        "resolved": resolved,
        "installed": bool(resolved.get("installed")),
    }
    row["proof"] = ledger_view(ledger or ledger_path(), entry["id"])
    # A CAPABILITY CLAIM GETS THE SAME CLOCK AS A PROOF CLAIM.  This row used to
    # print `verified : EXECUTED (2026-09-08)` directly above `G1 proven : NEVER
    # [TTL 30d]` — a hand-typed state with a typed date and NO TTL beside a
    # TTL'd one, which is R6's own FAIL disposition verbatim, committed by the
    # verb written to prevent it. The word "verified" is also retired here:
    # it is what makes a documentation-derived row read like a canary result.
    row["survey_freshness"] = freshness(
        entry["verification"]["date"] + "T00:00:00Z", ttl_days=TTL_DAYS)
    # THE SENTENCE THE WHOLE VERB EXISTS FOR.
    if not row["installed"]:
        row["state"] = "NOT_INSTALLED"
    elif resolved.get("tier") == "file-only":
        # No enum change (Council #12 froze STATES): the row ANNOTATES.  A file-tier
        # platform is installed by file, cannot fire, and its live reading is
        # unmeasured until a transcript says otherwise.
        row["state"] = "FILE_ONLY"
        row["tier"] = "file-only"
        row["live_fire"] = "UNMEASURABLE (no hook; a transcript is the only witness)"
    elif row["proof"]["last_obeyed"]["state"] == "FRESH":
        row["state"] = "SUPPORTED"
    elif row["proof"]["last_proven"]["state"] == "FRESH":
        row["state"] = "PROVEN"
    elif row["proof"]["last_present"]["state"] == "FRESH":
        row["state"] = "PRESENT"
    elif "EXPIRED" in tuple(
            row["proof"].get(k, {}).get("state")
            for k in ("last_obeyed", "last_proven", "last_present",
                      "last_uncontradicted")):
        row["state"] = "EXPIRED"
    else:
        row["state"] = "INSTALLED_UNPROVEN"
    # THE FIRED RUNG (Council #12, 2026-09-15).  `hooks status` reads the canary
    # LEDGER (present / proven / obeyed); `doctor` reads the HEARTBEAT join
    # (fired).  Two sensors, one ladder: FIRED < PRESENT < PROVEN < OBEYED.  Each
    # verb now renders the other's rung and names the sensor, so "UNPROVEN"
    # beside "LIVE" reads as what it is — fired, never witnessed — and never as
    # a contradiction.  Fail-graceful: the heartbeat organ missing is a row.
    row["fired"] = _fired_rung(entry["id"], row["installed"])
    if row["state"] == "INSTALLED_UNPROVEN" and row["fired"].get("state") == "LIVE":
        row["state"] = "FIRED_UNPROVEN"
    # N6 (Crucible D03-F3) -- THE LADDER MUST DEMOTE, NOT ONLY PROMOTE.
    #
    # `fired` was consulted in exactly one direction: it could lift
    # INSTALLED_UNPROVEN to FIRED_UNPROVEN and nothing else. So a ledger row
    # written up to 30 days ago rendered SUPPORTED -- the strongest word in this
    # vocabulary -- directly above its own live sensor reading `FIRED: DEAD`. The
    # seat's phrase for it: an unearned row wearing an earned row's typeface.
    #
    # A live miss OUTRANKS a stale proof, because PROVEN is a claim about the
    # past and DEAD is a reading of the present. The enum is not widened
    # (Council #12 froze STATES): the row is ANNOTATED, and `state_note` carries
    # the sentence a human needs. NOT_APPLICABLE / UNKNOWN never demote --
    # "cannot tell" is not "no", which is the rule C5 and C6 both turn on.
    _fired_state = str(row["fired"].get("state") or "")
    if _fired_state in ("DEAD", "INSTALLED_NEVER_FIRED") and row["state"] in (
            "SUPPORTED", "PROVEN", "PRESENT", "FIRED_UNPROVEN"):
        row["state_before_fired_demotion"] = row["state"]
        row["state"] = "EXPIRED" if _fired_state == "DEAD" else "INSTALLED_UNPROVEN"
        row["state_note"] = (
            f"the proof ledger still reads {row['state_before_fired_demotion']}, but the LIVE "
            f"heartbeat join reads {_fired_state}: a claim about the past does not outrank a "
            f"reading of the present. "
            + (row["fired"].get("reason") or ""))
    row["ladder"] = [f"FIRED: {row['fired'].get('state')}",
                     f"PRESENT: {row['proof'].get('last_present', {}).get('state')}",
                     f"PROVEN: {row['proof'].get('last_proven', {}).get('state')}",
                     f"OBEYED: {row['proof'].get('last_obeyed', {}).get('state')}"]
    return row


def _receipt_installed_at(platform_id: str, home: Optional[str] = None) -> Optional[str]:
    """When THIS platform's hook element was registered, per the receipt.

    C6: the FIRED window's lower bound. The earliest `installed_at` among the
    receipt's hooks entries for this platform -- earliest, not latest, because a
    re-install must not retroactively forgive the sessions that were missed
    under the first one.
    """
    try:
        rp = receipt_path(home)
        if not Path(rp).exists():
            return None
        touched = json.loads(read_text_if_regular(rp)).get("touched") or []
    except (OSError, ValueError):
        return None
    stamps = [t.get("installed_at") for t in touched
              if isinstance(t, dict) and t.get("installed_at")
              and t.get("mechanism", "hooks") == "hooks"
              and t.get("platform") in _platform_aliases(platform_id)]
    return min(stamps) if stamps else None


def _fired_rung(platform_id: str, installed: bool,
                home: Optional[str] = None) -> Dict[str, Any]:
    """The heartbeat join for one platform, as the status row's FIRED rung.
    Only the two hook-tier platforms have a heartbeat; every other id reports
    NOT_APPLICABLE with the reason.  Never raises."""
    if platform_id not in ("claude-code", "codex"):
        return {"state": "NOT_APPLICABLE", "sensor": "heartbeat join",
                "reason": "no session-start hook on this platform; nothing to fire"}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import voidloom_heartbeat as _hb  # type: ignore
        beats = _hb.read_heartbeats(platform_id)
        sessions = _hb.list_platform_sessions(platform_id)
        j = _hb.join_state(registered=bool(installed), heartbeats=beats,
                           sessions=sessions, platform=platform_id,
                           installed_at=_receipt_installed_at(platform_id, home))
        # [D02-M16]: when `derive_platform` cannot name the platform the hook still
        # writes a beat, under `unknown` -- and no verb reads that file, so the
        # doctor reported "0 heartbeat line(s)" for a hook that had just fired.
        # A beat we cannot attribute is not a beat that did not happen. Count it
        # and name it; it can never be JOINED (no platform owns those sessions),
        # so it informs a reader and never a verdict.
        _unattributable = 0
        try:
            _ub = _hb.read_heartbeats("unknown")
            _unattributable = int(_ub.get("fire_count") or 0)
        except Exception:
            _unattributable = 0
        return {"state": j.get("state"), "sensor": "heartbeat.session_id JOIN platform "
                                                   "session records (doctor's sensor)",
                "unattributable_fires": _unattributable,
                "unattributable_note": (
                    "heartbeat lines whose platform could not be derived at write time. "
                    "They prove the hook RAN; they can never join a platform session, so "
                    "they inform a reader and never a verdict."
                    if _unattributable else None),
                "window": j.get("window"), "window_anchor": j.get("window_anchor"),
                "installed_at": j.get("installed_at"),
                "missed_count": j.get("missed_count"),
                "fire_count_joined": j.get("fire_count_joined"),
                "fire_count_unjoined": j.get("fire_count_unjoined"),
                "headless_sessions_not_judged": j.get("headless_sessions_not_judged"),
                "headless_sessions_fired": j.get("headless_sessions_fired"),
                "last_ts": j.get("last_ts"), "reason": j.get("reason")}
    except Exception as e:  # a sensor may never break the verb that renders it
        return {"state": "UNKNOWN", "sensor": "heartbeat join",
                "reason": f"{type(e).__name__}: {e}"}


def unreceipted_markers(rows: Sequence[Dict[str, Any]], cwd: str,
                        home: Optional[str] = None) -> Dict[str, Any]:
    """OUR marks that NO receipt names — the orphans.  Amendment 1: the
    2026-09-08 hand-prepend to ~/.codex/config.toml (`.bak.1788878423`) is in no
    receipt; the precedent the council said did not exist was created that day.
    A status verb must at least SEE it.  Derived from the resolver rows and the
    receipt, never typed."""
    rp = receipt_path(home)
    receipted: set = set()
    try:
        if Path(rp).exists():
            for t in (json.loads(read_text_if_regular(rp)).get("touched") or []):
                if t.get("path"):
                    receipted.add(os.path.abspath(str(t["path"])))
    except (OSError, ValueError):
        pass
    orphans: List[Dict[str, Any]] = []
    for r in rows:
        res = r.get("resolved") or {}
        if r["id"] == "claude-code":
            for s in res.get("sources") or []:
                if s.get("our_element_indices") and os.path.abspath(s["path"]) not in receipted:
                    orphans.append({"platform": "claude-code", "path": s["path"],
                                    "kind": "hook element carrying our marker",
                                    "element_indices": s["our_element_indices"],
                                    "receipted": False})
        elif r["id"] == "codex":
            hj = res.get("hooks_json") or {}
            if hj.get("our_element_indices") and os.path.abspath(hj["path"]) not in receipted:
                orphans.append({"platform": "codex", "path": hj["path"],
                                "kind": "hook element carrying our marker",
                                "element_indices": hj["our_element_indices"],
                                "receipted": False})
            ct = res.get("config_toml") or {}
            if ct.get("path") and ct.get("exists"):
                try:
                    txt = read_text_if_regular(ct["path"], errors="replace")
                except OSError:
                    txt = ""
                mentions = [n for n, ln in enumerate(txt.splitlines(), 1)
                            if re.search(r"MILAMBER|Voidloom|voidloom", ln)]
                baks = sorted(str(b) for b in Path(ct["path"]).parent.glob(
                    Path(ct["path"]).name + ".bak.*"))
                if (mentions or baks) and os.path.abspath(ct["path"]) not in receipted:
                    orphans.append({"platform": "codex", "path": ct["path"],
                                    "kind": "hand edits that no receipt names",
                                    "mention_lines": mentions, "backup_siblings": baks,
                                    "receipted": False,
                                    "note": ("edited by hand (2026-09-08 prepend); NOT undoable "
                                             "by this uninstaller — no pre-image was taken")})
        elif r["id"] == "agents-md":
            w = res.get("resolved_winner")
            if res.get("winner_carries_our_marker") and w and \
                    os.path.abspath(w["path"]) not in receipted:
                orphans.append({"platform": "agents-md", "path": w["path"],
                                "kind": "marker block", "receipted": False})
    return {"receipt_path": rp, "receipt_present": Path(rp).exists(),
            "receipted_paths": sorted(receipted), "orphans": orphans,
            "orphan_count": len(orphans)}


def status(cwd: str, ledger: Optional[str] = None,
           home: Optional[str] = None,
           probe_platform: bool = False) -> Dict[str, Any]:
    """``probe_platform`` is the ONE consent that lets a status spawn the vendor
    (Codex app-server hooks/list).  Default False: every derivation is from files
    the platform itself wrote, and nothing under the user's home changes (R5)."""
    lp = ledger or ledger_path()
    rows = [resolve_platform(e, cwd, lp, probe_platform=probe_platform) for e in PLATFORMS]
    orphans = unreceipted_markers(rows, cwd, home)
    # AN UNREADABLE LEDGER IS A STRONGER REASON TO REFUSE THAN AN EXPIRED ROW.
    # The expired case at least knows what it does not know; this one cannot see
    # the proof channel at all. EXECUTED before this branch existed: a ledger
    # holding '{ this is not json' returned coverage_percent=0.0, expired=[],
    # ledger_present=True, every proof row NEVER, and the renderer dropped its
    # '[ABSENT]' note — a total loss of the proof channel rendered as a number.
    if not _ledger_readable(lp):
        _fault = ledger_fault(lp)
        _cov_u: Dict[str, Any] = {
            "coverage_percent": "REFUSED",
            "coverage_state": "UNKNOWN",
            "refusal_reason": (
                f"the proof ledger at {lp} exists but could not be read "
                f"({_fault}); coverage over an unreadable ledger is UNKNOWN, "
                f"not zero. A 0.0% here would be a placeholder wearing a "
                f"measurement's typeface. An EMPTY ledger is a legitimate "
                f"0 of 0 and renders as one; this is not that."),
            "proven_fresh_count": None, "platform_count": len(rows),
            "installed_count": len([r for r in rows if r["installed"]]),
            "expired_count": None, "expired": None, "expired_detail": None,
            "lapsed_survey_count": None, "ttl_days": TTL_DAYS}
        return {"schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
                "cwd": cwd, "ledger": lp,
                "ledger_present": Path(lp).exists(),
                "ledger_readable": False, "ledger_fault": _fault,
                "matrix_survey_date": MATRIX_SURVEY_DATE,
                "platforms": rows, "coverage": _cov_u,
                "unreceipted": orphans}
    total = len(rows)
    proven_fresh = [r["id"] for r in rows if r["state"] in ("PROVEN", "SUPPORTED")]
    # DERIVE THE REFUSAL FROM THE PROOF ROWS, NOT FROM THE COLLAPSED STATE.
    # row["state"] is EXPIRED only when the platform is INSTALLED and no proof
    # key is FRESH, so two of the three expiry shapes printed a percentage:
    # (b) an expired row on a NOT-INSTALLED platform -> expired=[] and 0.0%;
    # (c) an expired G1 masked by a FRESH G2 -> row state SUPPORTED and 10.0%.
    # Both were executed. The object the refusal is about is the PROOF KEY.
    _proof_keys = ("last_present", "last_proven", "last_obeyed",
                   "last_uncontradicted")
    expired_detail = [
        {"platform": r["id"], "key": k, "installed": r["installed"],
         "row_state": r["state"],
         "age_days": r["proof"][k].get("age_days")}
        for r in rows for k in _proof_keys
        if k in r["proof"] and r["proof"][k]["state"] == "EXPIRED"]
    # A SURVEY IS A CLAIM ABOUT SOMEONE ELSE'S SOFTWARE ON ONE DAY, so a lapsed
    # survey lapses into the same refusal rather than sitting beside it untimed.
    lapsed_surveys = [
        {"platform": r["id"], "key": "surveyed", "installed": r["installed"],
         "row_state": r["state"],
         "age_days": r["survey_freshness"]["age_days"]}
        for r in rows if r["survey_freshness"]["state"] == "EXPIRED"]
    expired_detail += lapsed_surveys
    expired = sorted({d["platform"] for d in expired_detail})
    installed = [r["id"] for r in rows if r["installed"]]
    cov: Dict[str, Any] = {
        "proven_fresh_count": len(proven_fresh), "platform_count": total,
        "proven_fresh": proven_fresh, "installed_count": len(installed),
        "expired_count": len(expired), "expired": expired,
        "expired_detail": expired_detail,
        "lapsed_survey_count": len(lapsed_surveys),
        "ttl_days": TTL_DAYS,
    }
    if expired:
        # R6 — REFUSE the percentage. A number whose denominator silently mixes
        # proven rows with rows whose proof has lapsed is the exact artefact
        # that gets quoted later with the caveat stripped off.
        cov["coverage_percent"] = "REFUSED"
        cov["coverage_state"] = "REFUSED_EXPIRED"
        cov["refusal_reason"] = (
            f"{len(expired)} platform(s) hold an EXPIRED claim "
            f"({', '.join(expired)}); "
            f"{len(expired_detail)} lapsed key(s) in total, of which "
            f"{len(lapsed_surveys)} are lapsed SURVEYS. "
            f"A coverage percentage computed over a set containing expired rows "
            f"would present a lapsed claim in the same typeface as a live one. "
            f"Re-canary those platforms, or read the two counts side by side: "
            f"{len(proven_fresh)} of {total}.")
    else:
        cov["coverage_percent"] = round(100.0 * len(proven_fresh) / total, 1) if total else 0.0
        cov["coverage_state"] = "MEASURED"
    return {"schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
            "cwd": cwd, "ledger": lp,
            "ledger_present": Path(lp).exists(),
            # DERIVED FROM THE READ, NOT FROM THE FILE'S EXISTENCE.
            "ledger_readable": True, "ledger_fault": None,
            "matrix_survey_date": MATRIX_SURVEY_DATE,
            "platform_probe": {"consented": bool(probe_platform),
                               "note": ("the Codex app-server was spawned (vendor writes under "
                                        "$CODEX_HOME)" if probe_platform else
                                        "no vendor process spawned; Codex trust read from its "
                                        "persisted record; pass --probe-platform for the live view")},
            "platforms": rows, "coverage": cov,
            "unreceipted": orphans}


# ===========================================================================
# UNINSTALL
# ===========================================================================
RECEIPT_ENV = "VOIDLOOM_INSTALL_RECEIPT"
MECHANISM = "hooks"          # the receipt mechanism THIS verb consents to (R11)
MARKER_BEGIN = "# >>> VOIDLOOM BEGIN >>>"
MARKER_END = "# <<< VOIDLOOM END <<<"

# NAMED, not summarised. Every item here is something the uninstaller CANNOT
# undo, and each is stated in terms of what remains rather than what we did.
IRREVERSIBLE_RESIDUE: List[Dict[str, str]] = [
    {"id": "codex_trust_hash",
     "what": "Codex hook trust-hash residue",
     "where": "~/.codex/config.toml, [hooks.state.\"<ABSOLUTE-PATH>/hooks.json:session_start:0:0\"] "
              "-> trusted_hash = \"sha256:...\" (the key EMBEDS the absolute path; relocating "
              "CODEX_HOME re-untrusts the hook)",
     "why": "Codex records trust against the hook DEFINITION's hash. Removing the "
            "hook does not remove the recorded trust state, and we will not edit "
            "another vendor's state table to tidy up after ourselves."},
    {"id": "delivered_content",
     "what": "Content already delivered to model vendors",
     "where": "the vendors' request logs, and the local transcripts that mirror "
              "them (~/.codex/sessions/**/rollout-*.jsonl, "
              "~/.claude/projects/**/*.jsonl)",
     "why": "Every session this was installed for, the preamble was TRANSMITTED. "
            "Uninstalling stops future delivery and retracts nothing. This is the "
            "honest core of the backdoor question: what makes a mechanism resemble "
            "a backdoor is precisely that uninstalling it does not undo it."},
    {"id": "provider_cache",
     "what": "Provider-side prompt / KV caches keyed on the frozen prefix",
     "where": "the vendor's infrastructure; not addressable from this machine",
     "why": "The prefix was engineered to be byte-stable so it WOULD be cached. "
            "That property does not reverse on uninstall."},
    {"id": "brain_behavioural_residue",
     "what": "Memories stored, and access counters advanced, because of the preamble",
     "where": "the brain's own memories / access_log / last_accessed / access_count",
     "why": "The preamble's purpose was to cause recall and store. Those writes are "
            "the user's own data and are NOT deleted — removing them would be a "
            "second, larger harm dressed as a cleanup."},
    {"id": "operator_edits",
     "what": "Operator edits made INSIDE our marker block",
     "where": "any instruction file we appended to",
     "why": "We remove the marker block we appended. If a human edited inside it, "
            "those edits are indistinguishable from ours and go with it. This is "
            "why the installer APPENDS a marker-guarded block and NEVER symlinks "
            "over an existing real file — a symlink over a user's real AGENTS.md "
            "silently redirects their future writes into a file we later delete."},
]


def _hold_survey_dates_fresh() -> List[str]:
    """CLASS-T — freeze the SURVEY dimension so a leg about the LEDGER is not
    also a leg about the calendar.  Returns the saved dates for restoration.

    A check must fail because the CODE is wrong, never because a date passed.
    Every survey row carries the same 30-day TTL as a proof row (that is D-8,
    and it is correct); a leg whose premise is "nothing has lapsed" therefore
    has an expiry date of its own unless it controls this dimension explicitly.
    """
    saved = [p["verification"]["date"] for p in PLATFORMS]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for p in PLATFORMS:
        p["verification"]["date"] = today
    return saved


def _restore_survey_dates(saved: Optional[Sequence[str]]) -> None:
    if not saved:
        return
    for p, d in zip(PLATFORMS, saved):
        p["verification"]["date"] = d


def receipt_path(home: Optional[str] = None) -> str:
    env = os.environ.get(RECEIPT_ENV)
    if env:
        return env
    base = home or os.environ.get("WIZARD_LIBRARY_HOME") or str(
        Path(__file__).resolve().parent.parent)
    return str(Path(base) / "voidloom_install_receipt.json")


def _sha256_file(p: str) -> Optional[str]:
    """The content hash of a REGULAR FILE, or None for anything else.

    NOT ``Path(p).read_bytes()``.  That call is where the verb actually HUNG,
    and the hang was mislocated for two iterations: ``uninstall_plan`` hashes
    the named path BEFORE it identifies it, and ``open()`` of a FIFO for reading
    BLOCKS until a writer appears -- so the PLAN never returned and not one of
    the guards downstream ever ran.  Fixing only the write-side open would have
    left this exactly as it was.  EXECUTED: 20 s timeout against iterations 3
    and 4, on tmpfs and on ext4.

    ``O_NONBLOCK`` is what returns from the open; ``S_ISREG`` on the DESCRIPTOR
    is what stops us then reading an endless device.  Both are required -- the
    first alone turns a hang into an infinite read on /dev/zero.
    """
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


# ===========================================================================
# R7 AS A PROPERTY — A WRITE LANDS ON THE OBJECT THE PLAN IDENTIFIED, OR IT
# DOES NOT HAPPEN.  NAME THE PROPERTY, NEVER THE MECHANISM.
# ===========================================================================
# THE PROPERTY (AMENDMENT 4):  the bytes must land on the SAME FILE OBJECT the
# plan identified, and on NO OTHER, and that must be verified BY IDENTITY —
# (st_dev, st_ino) — rather than by a path string, and never through the
# resolution the write itself used.
#
# ITERATION 3 WROTE THIS SECTION AS A DEFENCE AGAINST SYMLINKS AND WAS DEFEATED
# BY FOUR SHAPES THAT ARE NOT SYMLINKS.  All five were EXECUTED end-to-end
# through the shipped CLI against the iteration-3 file; each one destroyed a
# user's file and reported `restored=1, errors=0,
# verified_against_preimage=True, rc=0`:
#
#   1. HARDLINK.        AGENTS.md and REAL_AGENTS.md are ONE object with TWO
#      names (st_nlink=2).  `is_symlink` is False, `realpath` does not differ,
#      `lstat` agrees with `fstat` because it IS the same inode — every defence
#      below returned True and the user's file was overwritten.
#   2. TOCTOU SWAP.     A DIFFERENT object renamed over the SAME name between
#      the plan and the write.  No link exists at any moment.  inode 456121 at
#      plan, 456125 at write; the write went to 456125.
#   3. RENAMED PREFIX.  The directory holding the target is renamed away and a
#      new one put in its place, holding the user's own file.  `os.path.realpath`
#      returns the named path unchanged — there is no symlink to resolve — and
#      the write lands on an object the plan never saw.
#   4. PLANTED TARGET.  The plan records `current_exists=False`; a file (here a
#      hardlink to the user's) is planted at that name before the write, and the
#      "restore" overwrites it.
#   5. UNLINK ONE OF N. `delete_file` on a name whose object has other names
#      reported DELETED / deleted=1 while the object stayed on disk — a claim of
#      removal for bytes that were not removed, which is R7's own FAIL
#      disposition ("an undo that silently leaves residue is worse than no undo").
#
# The four mechanisms differ; the PROPERTY they violate is one.  So the guard is
# written against the property and the mechanisms are consequences:
#
# FIVE DEFENCES, EACH INDEPENDENTLY FALSIFIABLE AND EACH DRIVEN BY ITS OWN
# ASSERTION:
#   1. REFUSE ON THE NAME'S OWN STATE.  A named path whose final component is a
#      link, whose realpath differs from its absolute path, or WHOSE OBJECT HAS
#      MORE THAN ONE NAME (st_nlink > 1), is BLOCKED in the plan and SKIPPED in
#      execution.  A hardlinked file has more than one true name and we cannot
#      restore one name without altering the others, and the plan cannot
#      enumerate the other names — so it must not write.
#   1b. REFUSE ON DRIFT FROM THE PLAN.  The plan records (st_dev, st_ino,
#      st_nlink, exists).  If the object now at that name is not the object the
#      plan identified, the write is refused.  This is what shapes 2, 3 and 4
#      violate and what no path-shaped check can see.
#   2. O_NOFOLLOW / O_EXCL AT THE OPEN.  A path that became a link fails to open
#      rather than landing elsewhere; a path the plan saw as ABSENT is opened
#      O_EXCL, so an object planted in the race fails the open instead of being
#      overwritten.
#   3. THE IDENTITY GATE, BEFORE ANY BYTE IS DESTROYED.  The file is opened
#      WITHOUT O_TRUNC; `os.fstat` of the descriptor we actually hold is
#      compared against the recorded identity AND its st_nlink is re-read live.
#      Only after that gate passes is the file truncated.  Ordering is the whole
#      point: a check after O_TRUNC reports on a file it has already emptied.
#   4. A POST-CONDITION THAT CANNOT CONFIRM ITSELF.  `os.fstat` of the
#      descriptor written against `os.lstat` of the path the plan NAMED — lstat
#      does not follow — and the bytes re-read through a path whose DIRECTORY was
#      resolved independently and whose basename was re-attached, never through
#      the same name the write used.
#
# WHAT THIS STILL DOES NOT COVER, said plainly: a bind mount is caught only
# because it changes (dev, ino) at the named path — if a filesystem ever
# presented the same (dev, ino) for two genuinely different objects, defence 1b
# would be blind, and no check in this file would see it.  §8 carries that bound.
class PathIsNotWhatWeNamed(OSError):
    """The path we were told to touch does not name the object we would touch.

    ``refused_by`` NAMES WHICH DEFENCE FIRED.  Defence 1 is the pre-write
    identity check; defence 2 is ``O_NOFOLLOW`` at the open.  Without the
    distinction, removing defence 1 leaves defence 2 catching the same case and
    the suite cannot tell that a layer is gone — the mutant looks equivalent
    when it is not.  (Executed: mutant GM1 removed defence 1 and the run died
    with a raw ELOOP OSError instead of refusing, which is detection of the
    worst kind — a traceback rather than a graded failure.)
    """

    def __init__(self, message: str,
                 refused_by: str = "pre_write_identity_check") -> None:
        super().__init__(message)
        self.refused_by = refused_by


def _file_type(stt: Optional[os.stat_result]) -> Optional[str]:
    """The KIND of object, as a word an assertion can pin.

    ``path_identity`` reported dev, ino, link_count and is_symlink and never the
    TYPE, so three shapes walked straight through it: a FIFO (the verb BLOCKS in
    ``os.open`` forever), a character device (opened and written at; it survived
    only because ``ftruncate`` happened to raise EINVAL), and a directory
    (refused, but for the false reason that its ``st_nlink`` is >= 2).
    """
    if stt is None:
        return None
    m = stt.st_mode
    for pred, name in ((stat.S_ISREG, "regular file"), (stat.S_ISDIR, "directory"),
                       (stat.S_ISFIFO, "FIFO"), (stat.S_ISCHR, "character device"),
                       (stat.S_ISBLK, "block device"), (stat.S_ISSOCK, "socket"),
                       (stat.S_ISLNK, "symlink")):
        if pred(m):
            return name
    return "unknown"


def _fd_sha256(fd: int) -> str:
    """Hash the bytes the DESCRIPTOR holds, without moving its offset.

    ``pread`` rather than ``read`` so this can run between the open and the
    truncate without disturbing the write that follows.  A hash taken from a
    PATH is a hash of whatever holds that name at the moment of hashing; this
    one is a property of the object we are actually holding, which is the only
    thing a gate is entitled to decide on.
    """
    h = hashlib.sha256()
    off = 0
    while True:
        chunk = os.pread(fd, 1 << 20, off)
        if not chunk:
            break
        h.update(chunk)
        off += len(chunk)
    return h.hexdigest()


def path_identity(path: str) -> Dict[str, Any]:
    """What the name says, and WHICH OBJECT it names. Both, always.

    ``dev``/``ino`` are the object's identity; ``link_count`` is how many names
    reach it.  Iteration 3 returned neither, so a hardlinked target reported
    ``is_symlink=False, differs=False`` and every guard above it agreed to a
    write that destroyed a file the plan never listed (EXECUTED: rc 0,
    ``restored=1``, ``errors=0``, ``verified_against_preimage=True``).
    """
    p = Path(path)
    try:
        is_link = p.is_symlink()
    except OSError:
        is_link = False
    absolute = os.path.abspath(str(p))
    try:
        real = os.path.realpath(str(p))
    except OSError:
        real = absolute
    try:
        link_target = os.readlink(str(p)) if is_link else None
    except OSError:
        link_target = None
    # THE OBJECT, NOT THE NAME.  lstat does not follow, so this is the identity
    # of the thing the PLAN would touch — the only thing a later fstat can
    # legitimately be compared against.
    try:
        stt = os.lstat(str(p))
        dev, ino, nlink = stt.st_dev, stt.st_ino, stt.st_nlink
    except OSError:
        stt = None
        dev = ino = nlink = None
    return {"named_path": str(p), "absolute_path": absolute,
            "resolved_path": real, "is_symlink": is_link,
            "symlink_target": link_target,
            "differs": absolute != real,
            "dev": dev, "ino": ino,
            # A HARDLINK IS THE SAME OBJECT UNDER A NAME WE NEVER LISTED.
            # realpath does not differ and lstat agrees with fstat, so every
            # path-shaped defence returns True while a file at another name
            # loses its bytes. The plan cannot enumerate the other names, so it
            # must not write.
            "link_count": nlink,
            "has_other_names": (nlink is not None and nlink > 1),
            # THE TYPE, NOT ONLY THE IDENTITY.  stat.S_ISREG was consulted
            # NOWHERE in this file.  EXECUTED, against iterations 3 AND 4, on
            # tmpfs and on ext4: a FIFO at the receipt's named path HANGS the
            # verb in os.open(O_WRONLY) forever -- killed at a 20 s timeout; a
            # character device is opened and written at, and survives only
            # because ftruncate happens to raise EINVAL; a directory is refused
            # only because every directory has st_nlink >= 2, so the outcome is
            # right and the sentence we print about it is false.
            "is_regular_file": (None if stt is None
                                else stat.S_ISREG(stt.st_mode)),
            "file_type": _file_type(stt),
            "exists_without_following": os.path.lexists(str(p))}


def refusal_code(ident: Dict[str, Any]) -> Optional[str]:
    """WHICH property of the name refuses it — a code, not a sentence.

    Assertions pin the CODE; humans read the sentence.  A check that pins a
    refusal by matching a substring of English is a check that a reworded
    message silently disables.
    """
    # TYPE FIRST, AND THE ORDER IS THE POINT.  A directory was already
    # refused here -- by HAS_OTHER_NAMES, because its st_nlink is >= 2 -- so the
    # code an assertion pinned named a property that was true by accident
    # rather than the property that decided.  A refusal whose stated reason is
    # false is a refusal that stops being right the moment the accident stops.
    # A SYMLINK IS THE MORE SPECIFIC TYPE (P8): lstat of a final-component link
    # is S_IFLNK, so `is_regular_file is False` used to win and the IS_SYMLINK
    # branch -- the one that NAMES the resolved target -- was unreachable; the
    # user read "a FIFO here BLOCKS the open forever" for a symlink.  The order
    # here is: link, then non-regular object, then extra names, then a
    # re-resolving parent chain.
    if ident["is_symlink"]:
        return "IS_SYMLINK"
    if ident.get("is_regular_file") is False:
        return "NOT_A_REGULAR_FILE"
    if ident.get("has_other_names"):
        return "HAS_OTHER_NAMES"
    if ident["differs"]:
        return "RESOLVED_DIFFERS"
    return None


def refusal_reason(ident: Dict[str, Any]) -> Optional[str]:
    """The one sentence that says why this path is not written (same order as
    ``refusal_code``: the code and the sentence must name the SAME property)."""
    if ident["is_symlink"]:
        return (f"the named path is a SYMLINK to {ident['resolved_path']}; "
                f"writing it would modify a file this plan never listed. "
                f"Remove the link by hand, or re-run with a receipt naming the "
                f"real path.")
    if ident.get("is_regular_file") is False:
        return (f"the named path is a {ident.get('file_type')}, not a REGULAR "
                f"FILE; the receipt describes a file we installed here, so "
                f"something has replaced it. Refusing to touch it: a FIFO here "
                f"BLOCKS the open forever, a device node would be written at, "
                f"and a directory is not ours to restore over. Remove it by "
                f"hand, or re-run with a receipt that names a regular file.")
    if ident.get("has_other_names"):
        return (f"the named path has {ident['link_count']} hard links; the same "
                f"bytes are reachable under at least one name this plan does "
                f"not list, and writing them would change a file the plan never "
                f"named. Break the link by hand, or re-run with a receipt that "
                f"names every name.")
    if ident["differs"]:
        return (f"the named path {ident['absolute_path']} resolves to "
                f"{ident['resolved_path']} — a component of the directory chain "
                f"is a link — so the object written would not be the object "
                f"listed. Re-run with a receipt naming the resolved path.")
    return None


def planned_identity(ident: Dict[str, Any]) -> Dict[str, Any]:
    """The four facts a plan must carry forward so a write can be REFUSED for
    landing on something else.

    A plan that records only a PATH can be satisfied by whatever occupies that
    path at write time.  Shapes 2, 3 and 4 of the executed RED are exactly that:
    a different inode under the same name, a renamed directory in the prefix,
    and an object planted where the plan saw nothing — none of them a symlink,
    all of them invisible to every path-shaped check.
    """
    return {"dev": ident.get("dev"), "ino": ident.get("ino"),
            "link_count": ident.get("link_count"),
            "exists": bool(ident.get("exists_without_following"))}


def identity_matches(expect: Optional[Dict[str, Any]],
                     now: Dict[str, Any]) -> Dict[str, Any]:
    """Is the object here NOW the object ``expect`` identified?

    ONE function, NAMED, so the comparison can be DRIVEN by the self-check with
    a synthesised pair instead of only reached on a race nobody can schedule —
    the same affordance ``_eloop_to_refusal`` gives defence 2.  Returns a verdict
    dict rather than a bare bool so the reason survives into the report.
    """
    if not expect:
        return {"checked": False, "same_object": None, "reason": None}
    want = (expect.get("dev"), expect.get("ino"))
    got = (now.get("dev"), now.get("ino"))
    if not expect.get("exists"):
        # The plan saw NOTHING here. Anything present now was planted between
        # the plan and the write, and O_EXCL is what enforces this at the open.
        if now.get("exists_without_following"):
            return {"checked": True, "same_object": False,
                    "expected_dev_ino": None, "actual_dev_ino": list(got),
                    "reason": ("the plan recorded NO object at this name; one "
                               "exists now, so it was created after the plan "
                               "was made and is not ours to overwrite")}
        return {"checked": True, "same_object": True,
                "expected_dev_ino": None, "actual_dev_ino": None,
                "reason": None}
    if want == got:
        return {"checked": True, "same_object": True,
                "expected_dev_ino": list(want), "actual_dev_ino": list(got),
                "reason": None}
    return {"checked": True, "same_object": False,
            "expected_dev_ino": list(want), "actual_dev_ino": list(got),
            "reason": (f"the plan identified object {want} at this name; the "
                       f"object here now is {got}. Something replaced it "
                       f"between the plan and the write, and writing would "
                       f"change a file the plan never identified.")}


def independent_verify_path(path: str) -> str:
    """A SECOND ROUTE TO THE SAME NAME.

    The directory is resolved on its own and the basename re-attached, so the
    read-back does not travel the name the write travelled.  Paired with the
    ``fstat``/``lstat`` identity check below, a write that landed somewhere else
    cannot be confirmed by the check that follows it.
    """
    p = Path(path)
    try:
        parent_real = os.path.realpath(str(p.parent))
    except OSError:
        parent_real = str(p.parent)
    return str(Path(parent_real) / p.name)


def verify_write_landed(path: str, write_stat: os.stat_result) -> Dict[str, Any]:
    """Did the bytes land on the object the PLAN NAMED?

    ``os.lstat`` DOES NOT FOLLOW.  That is the whole point: if the write had
    gone through a symlink, ``write_stat`` would carry the TARGET's inode while
    this carries the LINK's, and the two disagree.  The pre-fix code compared
    ``_sha256_file(path)`` with the pre-image — both sides travelling the same
    followed path — so the post-condition agreed with the write by construction.
    """
    try:
        named = os.lstat(path)
    except OSError as e:
        return {"same_object": False, "named_path_stat_error": str(e)}
    return {"same_object": ((write_stat.st_dev, write_stat.st_ino)
                            == (named.st_dev, named.st_ino)),
            "written_dev_ino": [write_stat.st_dev, write_stat.st_ino],
            "named_path_dev_ino": [named.st_dev, named.st_ino],
            "named_path_stat_followed_links": False}


def _self_confirming_verify(path: str,
                            write_stat: os.stat_result) -> Dict[str, Any]:
    """THE PRE-FIX POST-CONDITION, KEPT RUNNABLE.

    Identical to ``verify_write_landed`` except that it uses ``os.stat``, which
    FOLLOWS.  On a symlink it therefore reports the target's inode on both
    sides and agrees with the write no matter where the write went.  The
    self-check drives both over the same fixture every run, so the difference
    between a check and a mirror is re-proven rather than remembered.
    """
    try:
        named = os.stat(path)
    except OSError as e:
        return {"same_object": False, "named_path_stat_error": str(e)}
    return {"same_object": ((write_stat.st_dev, write_stat.st_ino)
                            == (named.st_dev, named.st_ino)),
            "written_dev_ino": [write_stat.st_dev, write_stat.st_ino],
            "named_path_dev_ino": [named.st_dev, named.st_ino],
            "named_path_stat_followed_links": True}


def _eloop_to_refusal(e: OSError, path: str) -> None:
    """Convert an ``O_NOFOLLOW`` ELOOP into the SAME refusal defence 1 raises.

    ONE FUNCTION, NAMED, so the conversion can be DRIVEN by the self-check
    instead of only reached on a race nobody can schedule. Defence 1 refuses
    first on every ordinary run, so without a named entry point this layer's
    vocabulary is never exercised and a mutant that deletes it survives
    (executed: GM25). Returns normally when the errno is something else, so the
    caller re-raises unchanged.
    """
    if getattr(e, "errno", None) == errno.ELOOP:
        raise PathIsNotWhatWeNamed(
            f"O_NOFOLLOW refused the open of {path}: it is a symbolic link "
            f"({e}). The bytes were NOT written.",
            refused_by="O_NOFOLLOW_at_open") from e


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _eexist_to_refusal(e: OSError, path: str) -> None:
    """Convert an ``O_EXCL`` EEXIST into the SAME refusal the others raise.

    Reached when the plan recorded NOTHING at this name and an object was
    planted there before the write (shape 4 of the executed RED, where a
    hardlink to the user's file was planted after the plan and "restored" over).
    Named, so the conversion is drivable rather than only reachable on a race.
    """
    if getattr(e, "errno", None) == errno.EEXIST:
        raise PathIsNotWhatWeNamed(
            f"O_EXCL refused the open of {path}: the plan recorded no object at "
            f"this name and one exists now. The bytes were NOT written.",
            refused_by="O_EXCL_at_open") from e


def _eisdir_to_refusal(e: OSError, path: str) -> None:
    """Convert an EISDIR / ENOTDIR at the open into the SAME refusal the others raise.

    Reached when a directory is swapped in at the exact ``os.open`` moment (the
    reflector's O7 shape): the pre-open type check has passed, O_NOFOLLOW is
    satisfied, and the kernel answers IsADirectoryError.  The user's directory
    is intact either way; what was wrong was the LABEL -- install_execute
    reported ERROR (rc 1) where every sibling shape reports REFUSED naming the
    object (rc 3).  ELOOP and EEXIST were already translated; this is the third
    member of the class.  Returns normally for any other errno.
    """
    if getattr(e, "errno", None) in (errno.EISDIR, errno.ENOTDIR):
        raise PathIsNotWhatWeNamed(
            f"the object opened at {path} is a directory (or a component of its "
            f"path is not a directory) ({e}); nothing was written",
            refused_by="EISDIR_at_open") from e


def _enxio_to_refusal(e: OSError, path: str) -> None:
    """Convert an ENXIO at a non-blocking open into the SAME refusal the others raise.

    Reached when a FIFO is planted at the name AFTER the name screen and BEFORE
    the ``os.open`` (the in-window shape): O_NONBLOCK makes the kernel answer
    ENXIO ("no reader") instead of blocking forever, which is the bound on the
    window -- but the answer propagated as a bare OSError, so the hook reported
    ERROR where the object was REFUSED (EXECUTED, reflector iter 2, P3; the
    fourth member of the ELOOP / EEXIST / EISDIR class).  Returns normally for
    any other errno.
    """
    if getattr(e, "errno", None) == errno.ENXIO:
        raise PathIsNotWhatWeNamed(
            f"the object at {path} is a FIFO with no reader (ENXIO at a non-blocking "
            f"open); nothing was written",
            refused_by="ENXIO_at_open") from e


def plan_content(a: Dict[str, Any]) -> Optional[str]:
    """The content the PLAN recorded at this name, or None if it recorded none.

    ``current_sha256`` has been in every plan entry since iteration 2 and was
    read back by NOTHING.  It is the only field that can see a swap on a
    filesystem that recycles inode numbers, which is every ext4 filesystem, which
    is the one under $HOME.
    """
    if not (a.get("planned_identity") or {}).get("exists"):
        return None
    return a.get("current_sha256")


def identity_gate(fd: int, path: str,
                  expect: Optional[Dict[str, Any]],
                  at_open: Dict[str, Any],
                  expect_sha256: Optional[str] = None) -> os.stat_result:
    """DEFENCE 3, AND IT RUNS BEFORE A SINGLE BYTE IS DESTROYED.

    ``fstat`` describes the object we are actually holding — not a name, not a
    resolution, the object.  Three things must be true of it, and the file is
    NOT truncated until they are:

      * it has exactly ONE name (``st_nlink == 1``).  More than one name means
        the bytes we are about to replace are reachable through a name this plan
        never listed, and we cannot restore one name without altering the others.
      * it is the object the PLAN identified, when a plan identity was recorded.
      * it is the object the pre-open check just looked at, when it was not —
        which closes the window between ``path_identity`` and ``os.open``.

    ORDERING IS THE WHOLE REPAIR.  Iteration 3 opened with ``O_TRUNC``, so any
    check placed here would have been reporting on a file it had already
    emptied: the refusal would have been honest and the data would still be
    gone.  The truncate happens in the caller, after this returns.
    """
    st = os.fstat(fd)
    # THE TYPE, ON THE OBJECT WE ARE HOLDING.  refusal_code screens the NAME
    # before the open; this screens the DESCRIPTOR after it, so a swap inside
    # that window cannot hand us a device node to write at.
    if not stat.S_ISREG(st.st_mode):
        raise PathIsNotWhatWeNamed(
            f"the object opened at {path} is a {_file_type(st)}, not a regular "
            f"file. Nothing was written.",
            refused_by="identity_gate_not_a_regular_file")
    if st.st_nlink > 1:
        raise PathIsNotWhatWeNamed(
            f"the object opened at {path} has {st.st_nlink} names; the bytes "
            f"about to be replaced are reachable under at least one name this "
            f"plan does not list. Nothing was written.",
            refused_by="identity_gate_link_count")
    now = {"dev": st.st_dev, "ino": st.st_ino,
           "exists_without_following": True}
    verdict = identity_matches(expect, now) if expect else \
        identity_matches(planned_identity(at_open), now) \
        if at_open.get("exists_without_following") else \
        {"checked": False, "same_object": None, "reason": None}
    if verdict["checked"] and verdict["same_object"] is False:
        raise PathIsNotWhatWeNamed(
            f"{path}: {verdict['reason']} Nothing was written.",
            refused_by="identity_gate_at_open")
    # (dev, ino) IS NOT AN IDENTITY ACROSS A DELETE, AND THIS IS THE LAYER THAT
    # KNOWS IT.  ext4 hands a freed inode number straight back -- MEASURED on
    # this host, 200/200 unlink-then-create pairs return the SAME number, and
    # 0/200 on tmpfs.  So an unlink-and-recreate at the named path leaves every
    # (dev, ino) comparison above agreeing about an object that no longer
    # exists.  EXECUTED end to end through this verb, 5 trials on ext4: result
    # RESTORED, verified_against_preimage True, restored=1, errors=0, rc 0, and
    # the user's file destroyed every time.  The plan has recorded the CONTENT
    # since iteration 2 and nothing ever read it back.
    #
    # It is re-derived HERE, from the descriptor, by pread -- not from the path,
    # which would only re-ask the question the name already answered wrongly.
    if expect_sha256 is not None:
        got = _fd_sha256(fd)
        if got != expect_sha256:
            raise PathIsNotWhatWeNamed(
                f"{path}: the plan recorded content {expect_sha256} at this "
                f"name; the object we are holding contains {got}. A "
                f"delete-and-recreate preserves (dev, ino) on any filesystem "
                f"that recycles inode numbers, so identity alone cannot see "
                f"this. Nothing was written.",
                refused_by="identity_gate_content_drift")
    return st


def _write_all(fd: int, data: bytes) -> int:
    """Write EVERY byte of ``data`` to ``fd`` or raise -- ``os.write`` is not a write-all.

    CRUCIBLE-3 D05-C1 (DESTROYS, unwaivable, reproduced 5/5 through the shipped CLI).
    When the kernel accepts fewer bytes than asked it returns a SHORT COUNT and does
    not raise.  The caller below does ``os.ftruncate(fd, 0)`` first -- deliberately, so
    the identity gate can speak before anything is emptied -- so a short write leaves
    the user's settings.json truncated and unparseable while every `except OSError`
    arm in the tree sits unentered.  Measured at 93b295a under ``ulimit -f 9``:
    a 9,100-byte settings.json carrying 15 foreign hooks became 9,216 bytes,
    `json.loads` raised "Unterminated string", and the product printed
    "the pre-image was restored in the same run".

    Raising here is the whole repair: it routes a short write into the ``except
    OSError`` rollback arm that ALREADY EXISTS and already passes
    ``expect_sha256=None``, rather than inventing a second recovery path.
    A no-progress return is its own error -- looping on it would spin forever.
    """
    total = 0
    view = memoryview(data)
    while total < len(data):
        try:
            n = os.write(fd, view[total:])
        except InterruptedError:          # EINTR: no bytes moved, retry is correct
            continue
        if n <= 0:
            raise OSError(errno.ENOSPC,
                          f"short write: {total} of {len(data)} bytes accepted and the "
                          f"descriptor stopped accepting more")
        total += n
    return total


def guarded_write_bytes(path: str, data: bytes,
                        expect: Optional[Dict[str, Any]] = None,
                        expect_sha256: Optional[str] = None
                        ) -> Dict[str, Any]:
    """Write ``data`` to ``path`` or REFUSE.

    ``expect`` is the identity the PLAN recorded for this object.  Without it
    the function still defends the window inside itself; with it, the window
    between the plan and the write — minutes wide, and the one shapes 2, 3 and 4
    of the RED walk through — is closed too.
    """
    ident = path_identity(path)
    if refusal_code(ident) is not None:
        raise PathIsNotWhatWeNamed(refusal_reason(ident) or "path refused",
                                   refused_by="pre_write_identity_check")
    # DEFENCE 1b: DRIFT FROM THE PLAN, BEFORE THE OPEN.  Cheap, and it produces
    # the clearer message; the gate below is what makes it non-bypassable.
    drift = identity_matches(expect, ident)
    if drift["checked"] and drift["same_object"] is False:
        raise PathIsNotWhatWeNamed(
            f"{path}: {drift['reason']}", refused_by="plan_identity_drift")
    # DEFENCE 1c: CONTENT DRIFT FROM THE PLAN, BEFORE THE OPEN.  Cheap, and it
    # produces the clearer message; the gate below re-derives it from the
    # descriptor, which is what makes it non-bypassable.
    if expect_sha256 is not None and ident["exists_without_following"]:
        _now_sha = _sha256_file(path)
        if _now_sha != expect_sha256:
            raise PathIsNotWhatWeNamed(
                f"{path}: the plan recorded content {expect_sha256} at this "
                f"name; the content here now is {_now_sha}. A "
                f"delete-and-recreate on a filesystem that recycles inode "
                f"numbers preserves (dev, ino), so identity alone cannot see "
                f"this.",
                refused_by="plan_content_drift")
    # A path the PLAN saw as absent is created EXCLUSIVELY, so an object planted
    # in the race fails the open rather than being overwritten.
    saw_nothing = ((expect is not None and not expect.get("exists"))
                   or (expect is None
                       and not ident["exists_without_following"]))
    # O_RDWR so the gate can re-derive the CONTENT from this descriptor before
    # the truncate; O_NONBLOCK so a FIFO reaching the open fails with ENXIO
    # instead of BLOCKING FOREVER waiting for a reader (EXECUTED: 20 s timeout
    # against iterations 3 and 4, on tmpfs and on ext4).  NOT O_TRUNC -- the
    # ordering is the whole repair and nothing here may empty the file before
    # identity_gate has spoken.
    flags = os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | _NOFOLLOW
    if saw_nothing:
        flags |= os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as e:
        # DEFENCE 2, SPEAKING THE SAME LANGUAGE AS DEFENCE 1.  ELOOP here means
        # the path became a symlink between the identity check and the open —
        # or that the identity check is gone. Either way it is the same refusal
        # and must not reach a caller as an unhandled OSError.
        _eloop_to_refusal(e, path)
        _eexist_to_refusal(e, path)
        _eisdir_to_refusal(e, path)
        _enxio_to_refusal(e, path)
        raise
    try:
        # DEFENCE 3 FIRST, TRUNCATE SECOND.  If the gate refuses, the file on
        # disk is byte-for-byte what it was.
        #
        # THE LEGITIMATE CREATE (found by the install path, 2026-09-14).  When the
        # plan saw NOTHING and the O_EXCL open just SUCCEEDED, the object we hold
        # is one WE created this instant -- the O_EXCL success IS the proof that
        # nothing was planted.  The gate's plan-identity comparison cannot know
        # that: `expect.exists=False` against a descriptor that exists reads as
        # "planted" and REFUSES -- and the refusal left a 0-byte file at the
        # name.  EXECUTED: every fresh receipt / pre-image / hooks.json creation
        # refused, each leaving an empty file.  The prior battery tested only
        # the ADVERSARIAL half of this shape (S4/A7: planted -> refuse) and never
        # the negative control (absent -> still absent -> must succeed), so the
        # writer could refuse every legitimate create and hold 16/16.  The
        # planted half is unchanged: O_EXCL fails BEFORE this line.
        created_by_us = bool(saw_nothing)
        gate_stat = identity_gate(fd, path,
                                  None if created_by_us else expect,
                                  ({"exists_without_following": False}
                                   if created_by_us else ident),
                                  expect_sha256=(None if created_by_us
                                                 else expect_sha256))
        os.ftruncate(fd, 0)
        n_written = _write_all(fd, data)
        write_stat = os.fstat(fd)
    except BaseException:
        # A refusal or error AFTER an O_EXCL create must not leave an empty
        # file wearing the plan's name: remove exactly the object we created
        # (identity checked against the descriptor we hold), nothing else.
        if saw_nothing:
            try:
                _st_fd = os.fstat(fd)
                _st_nm = os.lstat(path)
                if (_st_fd.st_dev, _st_fd.st_ino) == (_st_nm.st_dev, _st_nm.st_ino) \
                        and _st_fd.st_size == 0:
                    os.unlink(path)
            except OSError:
                pass
        raise
    finally:
        os.close(fd)
    landed = verify_write_landed(path, write_stat)
    landed["link_count_at_write"] = gate_stat.st_nlink
    landed["identity_checked_against_plan"] = bool(expect)
    vpath = independent_verify_path(path)
    blob = b""
    read_error = None
    try:
        vfd = os.open(vpath, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
        try:
            while True:
                chunk = os.read(vfd, 1 << 20)
                if not chunk:
                    break
                blob += chunk
        finally:
            os.close(vfd)
    except OSError as e:
        read_error = f"{type(e).__name__}: {e}"
    return {**ident, "bytes_written": n_written,
            "short_write": n_written != len(data),   # parity with guarded_append_bytes
            "verify_path": vpath,
            "verify_path_is_the_named_path": vpath == os.path.abspath(path),
            "read_back_error": read_error,
            "sha256_after": (None if read_error
                             else hashlib.sha256(blob).hexdigest()),
            **landed}


def guarded_write_text(path: str, text: str,
                       expect: Optional[Dict[str, Any]] = None,
                       expect_sha256: Optional[str] = None) -> Dict[str, Any]:
    return guarded_write_bytes(path, text.encode("utf-8"), expect=expect,
                               expect_sha256=expect_sha256)


def guarded_append_bytes(path: str, data: bytes) -> Dict[str, Any]:
    """APPEND ``data`` to a REGULAR FILE WITH EXACTLY ONE NAME whose parent chain
    resolves to the named path -- or REFUSE.  THE SAME GATE AS THE TRUNCATING
    WRITER, on the append face (R3 / R6 / R9 as one property).

    The heartbeat is OUR file and needs no plan identity (nothing to drift
    from), but it is still a write to a path under the user's home, and the
    prior round's headline lesson -- a hardlink is the same object under a name
    the plan never listed -- applied to it unchanged: EXECUTED (reflector iter
    1, P5), a hardlink of a user's file at ``<heartbeat_dir>/claude-code.jsonl``
    received the JSON line, rc 0, no reason on stderr.  A third writer without
    the link-count gate was the hardlink defect reintroduced; so there is no
    third writer.  Defences, in order: (1) the NAME (``refusal_code``: symlink,
    non-regular, extra names, re-resolving parent chain); (2) the OPEN
    (O_NOFOLLOW so a link planted in the window fails ELOOP; O_NONBLOCK so a
    FIFO fails ENXIO instead of hanging; O_CREAT without O_EXCL because a
    concurrent first fire is legitimate); (3) the DESCRIPTOR (S_ISREG and
    ``st_nlink == 1`` on the object actually held).  Never O_TRUNC.  Raises
    ``PathIsNotWhatWeNamed`` on refusal and ``OSError`` on failure; the caller
    that must never break session start wraps both.
    """
    ident = path_identity(path)
    if refusal_code(ident) is not None:
        # THE NAME SCREEN NAMES ITSELF (iteration 3, the reflector's MB10): without a
        # label, removing this layer leaves the descriptor gate catching the same
        # shapes under its own labels and the suite cannot tell the layer is gone.
        raise PathIsNotWhatWeNamed(refusal_reason(ident) or "path refused",
                                   refused_by="pre_write_identity_check")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | _NOFOLLOW
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as e:
        _eloop_to_refusal(e, path)
        _eisdir_to_refusal(e, path)
        _enxio_to_refusal(e, path)
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathIsNotWhatWeNamed(
                f"the object opened at {path} is a {_file_type(st)}, not a regular "
                f"file. Nothing was written.",
                refused_by="identity_gate_not_a_regular_file")
        if st.st_nlink > 1:
            raise PathIsNotWhatWeNamed(
                f"the object opened at {path} has {st.st_nlink} names; a line appended "
                f"here would land in a file reachable under a name this writer does "
                f"not own. Nothing was written.",
                refused_by="identity_gate_link_count")
        n = os.write(fd, data)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    return {**ident, "bytes_written": n, "short_write": n != len(data),
            "written_dev_ino": [after.st_dev, after.st_ino],
            "link_count_at_write": st.st_nlink, "size_after": after.st_size}


def _open_dir_nofollow(name: str, dir_fd: int, full: str) -> int:
    """Open ``name`` relative to ``dir_fd`` as a DIRECTORY, never following a link
    at that component; ELOOP / ENOTDIR are the same refusal the writers raise."""
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=dir_fd)
    except OSError as e:
        en = getattr(e, "errno", None)
        # Linux answers ENOTDIR (not ELOOP) for O_DIRECTORY|O_NOFOLLOW on a symlink
        # (EXECUTED, iteration 3); the LABEL must name the object, so the component
        # is lstat'ed relative to the directory we HOLD to tell a link from a file.
        is_link = False
        if en in (errno.ELOOP, errno.ENOTDIR):
            try:
                is_link = stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode)
            except OSError:
                is_link = en == errno.ELOOP
        if en == errno.ELOOP or (en == errno.ENOTDIR and is_link):
            raise PathIsNotWhatWeNamed(
                f"{full} is a symbolic link; a directory chain that passes through a link "
                f"would place what we create under a name this writer does not own. "
                f"Nothing was created.", refused_by="ELOOP_at_open") from e
        if en == errno.ENOTDIR:
            raise PathIsNotWhatWeNamed(
                f"{full} exists and is not a directory; nothing was created.",
                refused_by="ENOTDIR_at_open") from e
        raise


def guarded_mkdir_chain(path: str, must_create_leaf: bool = False) -> Dict[str, Any]:
    """CREATE the directory ``path`` and any missing ancestors -- or REFUSE, creating
    NOTHING.  THE DIRECTORY FACE OF THE ONE GATED WRITER (R3 / R9 v3, iteration 3).

    The property, split by operation (R9 v3): a FILE write lands only on a regular
    file with exactly one name whose parent chain resolves to the named path; a
    DIRECTORY is created only under a parent chain that resolves to the named
    path, and only where its final component does not yet exist.  EXECUTED
    (reflector iter 2, P2): with ``~/.voidloom -> <elsewhere>`` and no heartbeat
    directory yet, ``Path(...).mkdir(parents=True)`` CREATED ``<elsewhere>/heartbeat/``
    inside the foreign target, and only then did the file gate refuse.  A
    directory creation is a write; it landed under a name the hook does not own.

    Defences, in order: (1) the NAME -- ``realpath == abspath`` for the whole
    chain, before anything is created; (2) the WALK -- one descriptor per
    component from the root, each opened O_DIRECTORY|O_NOFOLLOW (a link planted
    at a component in the window answers ELOOP and is refused), each missing
    component created with ``mkdir(name, dir_fd=parent)`` so the creation is
    relative to the directory we HOLD, never to a path string re-resolved by
    the kernel.  ``must_create_leaf`` makes a pre-existing leaf a refusal
    (the plan said "create"; something else made it first).
    """
    ap = os.path.abspath(path)
    rp = os.path.realpath(ap)
    if rp != ap:
        raise PathIsNotWhatWeNamed(
            f"{ap} resolves to {rp}: a component of the directory chain is a link, so a "
            f"directory created here would land under a name this writer does not own. "
            f"Nothing was created.", refused_by="parent_chain_resolves_elsewhere")
    parts = [x for x in ap.split(os.sep) if x]
    created: List[str] = []
    cur = os.sep
    fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, name in enumerate(parts):
            cur = os.path.join(cur, name)
            try:
                nfd = _open_dir_nofollow(name, fd, cur)
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o755, dir_fd=fd)
                    created.append(cur)
                except FileExistsError:
                    pass                      # a concurrent legitimate creator; verified below
                nfd = _open_dir_nofollow(name, fd, cur)
            if i == len(parts) - 1 and must_create_leaf and cur not in created:
                os.close(nfd)
                raise PathIsNotWhatWeNamed(
                    f"{cur} already exists; the plan recorded no directory at this name "
                    f"and one exists now. Nothing was created.", refused_by="O_EXCL_at_mkdir")
            os.close(fd)
            fd = nfd
    finally:
        os.close(fd)
    return {"path": ap, "created": created, "existed": not created}


def guarded_read_text(path: str,
                      identity_out: Optional[Dict[str, Any]] = None,
                      expect: Optional[Dict[str, Any]] = None,
                      expect_sha256: Optional[str] = None) -> str:
    """Read without following the final component.

    ``identity_out``, when supplied, is filled with the identity of the object
    ACTUALLY READ (from ``fstat`` of the descriptor, not from the name).  A
    read-modify-write — ``remove_marker_block`` is one — must write back to the
    object it read, and threading this dict is how the caller says so.  Without
    it the two halves are joined only by a path string, which is the same
    mistake one level up.
    """
    ident = path_identity(path)
    if refusal_code(ident) is not None:
        raise PathIsNotWhatWeNamed(refusal_reason(ident) or "path refused")
    # THE SAME PROPERTY, ON THE READ SIDE.  remove_marker_block threaded the
    # READ's identity into its own write-back -- the right instinct -- and
    # compared NEITHER half to the PLAN, so the object edited was whatever held
    # the name at execute time.  EXECUTED: the user's own file, carrying its own
    # Voidloom block, renamed over the name between plan and execute ->
    # MARKER_REMOVED, removed=1, errors=0, rc 0, their block silently stripped
    # from a file the plan never identified.
    drift = identity_matches(expect, ident)
    if drift["checked"] and drift["same_object"] is False:
        raise PathIsNotWhatWeNamed(f"{path}: {drift['reason']}",
                                   refused_by="plan_identity_drift")
    try:
        # O_NONBLOCK: a FIFO opened O_RDONLY blocks for a writer exactly as one
        # opened O_WRONLY blocks for a reader. Same class, other side.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
    except OSError as e:
        _eloop_to_refusal(e, path)
        _eisdir_to_refusal(e, path)
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathIsNotWhatWeNamed(
                f"the object opened at {path} is a {_file_type(st)}, not a regular "
                f"file. Nothing was read or modified.",
                refused_by="identity_gate_not_a_regular_file")
        if identity_out is not None:
            identity_out.clear()
            identity_out.update({"dev": st.st_dev, "ino": st.st_ino,
                                 "link_count": st.st_nlink, "exists": True})
        buf = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            buf += chunk
    finally:
        os.close(fd)
    # AND THE CONTENT, DERIVED FROM THE BYTES WE ACTUALLY READ.  On a filesystem
    # that recycles inode numbers the identity check above agrees with a swap;
    # this is the conjunct that does not.
    if expect_sha256 is not None:
        _got = hashlib.sha256(buf).hexdigest()
        if _got != expect_sha256:
            raise PathIsNotWhatWeNamed(
                f"{path}: the plan recorded content {expect_sha256} at this "
                f"name; the object we just read contains {_got}. Nothing was "
                f"modified.",
                refused_by="plan_content_drift")
    return buf.decode("utf-8")


def guarded_unlink(path: str,
                   expect: Optional[Dict[str, Any]] = None,
                   expect_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Delete, and let the POST-CONDITION name the state.

    ``unlink(missing_ok=True)`` made a path that was never there
    indistinguishable from one we removed, and DELETED was claimed for both —
    the third member of the class the round closed twice elsewhere.  The plan
    already carried ``current_exists`` and the information was discarded.
    """
    ident = path_identity(path)
    # THE SAME ORDER AS refusal_code (P8): the LINK is the most specific type
    # and its sentence names the resolved target; then any other non-regular
    # object (a directory used to be refused by the link-count branch -- true
    # only because every directory has st_nlink >= 2 -- an accident, not a
    # reason); then extra names; then a re-resolving parent chain.
    if ident["is_symlink"]:
        raise PathIsNotWhatWeNamed(
            f"the named path is a SYMLINK to {ident['resolved_path']}; the "
            f"receipt describes a FILE we installed here, so something has "
            f"replaced it. Refusing to delete: removing the link would leave "
            f"the target, and following it would delete a file we never made.")
    if ident.get("is_regular_file") is False:
        raise PathIsNotWhatWeNamed(
            f"the named path is a {ident.get('file_type')}, not a REGULAR "
            f"FILE; the receipt describes a file we installed here, so "
            f"something has replaced it. Refusing to delete it.",
            refused_by="pre_write_identity_check")
    # THE SAME PROPERTY, ON THE DELETE SIDE.  unlink removes a NAME, not an
    # object. If the object has other names its bytes survive, and reporting
    # DELETED for it is a claim of removal for bytes that were not removed —
    # R7's own FAIL disposition, residue the uninstaller BELIEVES it removed.
    # EXECUTED against iteration 3: ours.md hardlinked to USER_KEEPS_THIS.md ->
    # result=DELETED, deleted=1, errors=0, and the object still on disk.
    if ident.get("has_other_names"):
        raise PathIsNotWhatWeNamed(
            f"the named path has {ident['link_count']} hard links; unlinking it "
            f"removes ONE NAME and leaves the object — and its bytes — reachable "
            f"under a name this plan does not list. Reporting DELETED here would "
            f"claim a removal that did not happen. Break the link by hand.",
            refused_by="pre_write_identity_check")
    if ident["differs"]:
        raise PathIsNotWhatWeNamed(refusal_reason(ident) or "path refused")
    # THE SAME PROPERTY, ON THE DELETE SIDE.  guarded_unlink was called with a
    # PATH AND NOTHING ELSE: the plan computed planned_identity, recorded it,
    # and never passed it here, so the delete action had no drift check of any
    # kind -- only the symlink and multiply-named guards, both of which are
    # properties of the NAME's state and not of the PLAN.  unlink removes a
    # NAME; if the object behind that name is not the object the plan
    # identified, the name we remove is the user's.  EXECUTED: an ordinary
    # rename swap -> DELETED, deleted=1, errors=0, rc 0, the user's file gone,
    # on tmpfs AND ext4 -- no inode trick and no symlink required.
    drift = identity_matches(expect, ident)
    if drift["checked"] and drift["same_object"] is False:
        raise PathIsNotWhatWeNamed(f"{path}: {drift['reason']}",
                                   refused_by="plan_identity_drift")
    # AND THE CONTENT, for the same reason it is required on the write side: on
    # ext4 an unlink-and-recreate at this name leaves (dev, ino) unchanged.
    if expect_sha256 is not None and ident["exists_without_following"]:
        _now_sha = _sha256_file(path)
        if _now_sha != expect_sha256:
            raise PathIsNotWhatWeNamed(
                f"{path}: the plan recorded content {expect_sha256} at this "
                f"name; the content here now is {_now_sha}. Refusing to unlink "
                f"a name that no longer holds the object the plan identified.",
                refused_by="plan_content_drift")
    existed_before = os.path.lexists(path)
    if existed_before:
        try:
            os.unlink(path)
        except OSError as e:
            _eisdir_to_refusal(e, path)   # a directory swapped in at the unlink moment
            raise
    gone = not os.path.lexists(path)
    return {**ident, "existed_before": existed_before, "gone_after": gone}


def guarded_rmdir_if_empty(path: str) -> Dict[str, Any]:
    """Remove a directory the receipt says WE created — only if it is empty and
    only if it is a real directory (rmdir never follows a link and cannot delete
    content).  Returns what happened; never raises."""
    try:
        st = os.lstat(path)
    except OSError:
        return {"path": path, "removed": False, "reason": "absent"}
    if not stat.S_ISDIR(st.st_mode):
        return {"path": path, "removed": False, "reason": f"not a directory ({_file_type(st)})"}
    try:
        if os.listdir(path):
            return {"path": path, "removed": False, "reason": "not empty (the user put something here)"}
        os.rmdir(path)
        return {"path": path, "removed": not os.path.lexists(path)}
    except OSError as e:
        return {"path": path, "removed": False, "reason": f"{type(e).__name__}: {e}"}


def _unsafe_restore_following_the_symlink(path: str,
                                          preimage_path: str) -> Dict[str, Any]:
    """THE PRE-FIX RESTORE, KEPT RUNNABLE — the same affordance as the emitter's
    ``_apply_budget_prefix_skipping`` and its ``--selector legacy``.

    A red/green that lives only in a markdown file is not repeatable by the next
    reviewer.  This is the exact iteration-2 body: ``write_bytes`` follows the
    link, ``_sha256_file`` follows the SAME link, the two agree, and RESTORED is
    reported for a write that destroyed a file the plan never listed.  The
    self-check drives it over a symlink fixture EVERY RUN and asserts that it
    still destroys the target — so the guard above is measured against a live
    failure rather than against a memory of one.

    It is called from nowhere but ``self_check``.  ``I5`` asserts that.
    """
    Path(path).write_bytes(Path(preimage_path).read_bytes())
    after = _sha256_file(path)
    want = _sha256_file(preimage_path)
    return {"result": "RESTORED" if after == want else "ERROR",
            "verified_against_preimage": after == want,
            "sha256_after": after,
            "note": ("both sides of this comparison travelled the same followed "
                     "path, so it agrees with the write by construction")}


SCOPE_EXCEEDS_FLAG = "SCOPE_EXCEEDS_FLAG"


def undo_domain(cwd: str, mechanism: Optional[str] = None,
                platform: Optional[str] = None,
                home: Optional[str] = None) -> Dict[str, Any]:
    """THE SET OF PATHS THIS INVOCATION MAY TOUCH, derived from the WORLD.

    C1 (cycle 1, 2026-09-15).  The undo used to iterate ``receipt["touched"]``
    filtered by MECHANISM ONLY — no platform, no path containment, no HOME
    check — and the Crucible reached that one loop from four directions:
      * wrong platform  `--platform claude` deleted the Codex hook (and the
        reverse); the flag was parsed and dropped.
      * wrong path      a receipt naming ANY file deleted it; the receipt's
        location is chosen by $VOIDLOOM_INSTALL_RECEIPT, so no write to the
        brain dir was needed to aim it.
      * wrong HOME      run under HOME=B it reverted HOME=A's files, returned
        `restored: 2, errors: 0`, and marked B's live element `undone`.
      * unverifiable    `undone` was written from the PLAN, never the
        post-condition.
    One unfiltered loop, four faces (Crucible seats U06, U04, D04 + known K1).

    THE FIX INVERTS THE DIRECTION OF DERIVATION (KAEL-EM, Council #13): the
    domain is what the LIVE ENUMERATORS can see in THIS environment, and the
    receipt is INTERSECTED with it — never the other way round.  Because each
    enumerator resolves from the running ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``
    / ``HOME``, the platform filter and the HOME containment fall out as
    geometry rather than as three more special cases.

    Each MECHANISM supplies its own enumerator — hooks from ``PLATFORMS`` via
    ``resolve_platform``, instructions from ``voidloom_instructions.target_table``.
    Measured before this was written: with the per-mechanism enumerator, 2 of 2
    hooks paths and 3 of 3 instructions paths on a correctly-installed HOME are
    enumerable, 0 missing.  (With a single global resolver, 2 of 5 were NOT —
    KAEL-EM's falsifier fired and named the shape of the fix.)

    THE TRADE, DECLARED: an element the live enumerator cannot see is one the
    intersection cannot remove, so a legacy element from an older layout is
    RETAINED rather than deleted.  That is a deliberate exchange of a data-loss
    Critical for an orphan-retention item, and the orphans are COUNTED and
    NAMED in the plan (`out_of_domain`) rather than silently dropped.
    """
    # THE DOMAIN IS COMPUTED FOR THE HOME THE PLAN IS FOR.  `uninstall_plan`
    # already takes a `home`; a fixture (and the organ self-check's own symlink
    # leg) builds its tree there rather than in the ambient environment, so the
    # enumerators must resolve against it or the intersection would refuse a
    # legitimate fixture's own paths.  Caught by the shipped self-check the first
    # time C1 ran: `actions[0]` IndexError, because every action was refused.
    # `home` supplies the FALLBACK, never an override: `_expand_platform_path`
    # honours $CLAUDE_CONFIG_DIR / $CODEX_HOME FIRST and only then falls back to
    # ~/.claude, ~/.codex — so a caller that has set those explicitly (a fixture,
    # a relocated home, the platform itself) must keep winning.  The first draft
    # of this function overrode them and made every explicitly-relocated CODEX_HOME
    # fall out of its own domain; the property battery's A11/A12 caught it.
    _env_saved = {}
    if home:
        _env_saved["HOME"] = os.environ.get("HOME")
        os.environ["HOME"] = home
        for var, sub in (("CLAUDE_CONFIG_DIR", ".claude"), ("CODEX_HOME", ".codex")):
            if not (os.environ.get(var) or "").strip():
                _env_saved[var] = os.environ.get(var)
                os.environ[var] = str(Path(home) / sub)
    try:
        return _undo_domain_inner(cwd, mechanism, platform)
    finally:
        for var, val in _env_saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


def _undo_domain_inner(cwd: str, mechanism: Optional[str],
                       platform: Optional[str]) -> Dict[str, Any]:
    dom: Dict[str, set] = {"hooks": set(), "instructions": set()}
    per_platform: Dict[str, set] = {}
    if mechanism in (None, "hooks"):
        for entry in PLATFORMS:
            if platform and entry.get("id") not in _platform_aliases(platform):
                continue
            try:
                res = (resolve_platform(entry, cwd) or {}).get("resolved") or {}
            except Exception:
                continue
            here = set()
            for s_ in (res.get("sources") or []) + (res.get("chain") or []):
                if isinstance(s_, dict) and s_.get("path"):
                    here.add(s_["path"])
            for k in ("hooks_json", "config_toml"):
                v = res.get(k)
                if isinstance(v, dict) and v.get("path"):
                    here.add(v["path"])
            dom["hooks"] |= here
            per_platform[entry.get("id")] = here
    if mechanism in (None, "instructions"):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import voidloom_instructions as _vi  # type: ignore
            for t in _vi.target_table(os.environ.get("HOME")):
                if t.get("path"):
                    dom["instructions"].add(t["path"])
        except Exception:
            pass
    allowed = set()
    for m, paths in dom.items():
        if mechanism in (None, m):
            allowed |= paths

    # CRUCIBLE-3 D03-F2 (DESTROYS, reproduced three ways).
    #
    # `undo_domain`'s docstring promised that "the platform filter and the HOME
    # containment fall out as geometry rather than as three more special cases."
    # THE PLATFORM FILTER DOES. HOME CONTAINMENT DID NOT -- this function also
    # unions whatever `resolve_platform(entry, cwd)` sees, and cwd is not under
    # HOME. Measured on a fixture HOME with NOTHING installed in it: a 19-path
    # domain of which SIX lay outside that HOME, all cwd-derived; D03 measured six
    # of them live in the operator's own working tree.
    #
    # Project-scoped configs are a real feature and are NOT removed here: undoing
    # a project-scoped install from inside that project is the legitimate case.
    # What changes is that the containment is now VISIBLE rather than promised,
    # and that an EXPLICIT `home` is honoured as the scope the caller declared.
    _home = os.environ.get("HOME") or ""
    def _inside(pth: str) -> bool:
        try:
            return bool(_home) and os.path.commonpath([os.path.abspath(pth), _home]) == _home
        except Exception:
            return False
    _outside = sorted(a for a in allowed if not _inside(a))
    return {"allowed": allowed, "by_mechanism": {k: sorted(v) for k, v in dom.items()},
            "by_platform": {k: sorted(v) for k, v in per_platform.items()},
            # THE CONTAINMENT, STATED. A caller that believed the docstring can now
            # check instead of believing: these are the paths this invocation would
            # be allowed to touch that are NOT under HOME, and where they came from.
            "outside_home": _outside,
            "outside_home_count": len(_outside),
            "outside_home_origin": ("cwd-derived project scope: the enumerators "
                                    "resolve project configs from the working "
                                    "directory, which is not under HOME"),
            "derived_from": {"cwd": cwd, "mechanism": mechanism, "platform": platform,
                             "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR"),
                             "CODEX_HOME": os.environ.get("CODEX_HOME"),
                             "HOME": os.environ.get("HOME")},
            "note": ("the domain is the LIVE enumerators' view in THIS environment; "
                     "the receipt is intersected with it, never trusted over it. "
                     "HOME containment does NOT fall out as geometry: cwd-derived "
                     "project paths are included by design and are listed in "
                     "`outside_home` so the scope is inspectable rather than assumed")}


@contextlib.contextmanager
def _fixture_world(home: str, receipt: Optional[str] = None):
    """Pin the platform environment to a SYNTHETIC home for a fixture's plan.

    C1.  `undo_domain` treats `home` as a FALLBACK and lets an explicitly-set
    $CLAUDE_CONFIG_DIR / $CODEX_HOME keep winning -- which is correct for a real
    invocation (a relocated CODEX_HOME must stay inside its own domain).  But a
    self-check fixture is not a real invocation: it BUILDS a world at `home` and
    then asks the planner about it.  If the ambient environment already names a
    different config dir, the fixture's own paths fall outside their own domain,
    every action is refused, and the leg silently stops testing the guard it was
    written for.

    Measured: with $CLAUDE_CONFIG_DIR unset the identity battery passed; with it
    set (i.e. on every real Claude Code install) shape 3 raised IndexError on an
    empty `actions`.  The standalone PASS was environment-luck, and the suite --
    which always sets both -- is what exposed it.  A fixture must therefore state
    its world rather than inherit one.
    """
    _saved: Dict[str, Optional[str]] = {}

    def _set(var: str, val: Optional[str]) -> None:
        _saved[var] = os.environ.get(var)
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val

    _set("HOME", home)
    _set("CLAUDE_CONFIG_DIR", str(Path(home) / ".claude"))
    _set("CODEX_HOME", str(Path(home) / ".codex"))
    if receipt is not None:
        _set(RECEIPT_ENV, receipt)
    try:
        yield
    finally:
        for var, val in _saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


def _platform_aliases(p: str) -> Tuple[str, ...]:
    """`--platform claude` names the platform whose id is `claude-code`."""
    p = (p or "").strip().lower()
    return {"claude": ("claude", "claude-code"), "claude-code": ("claude", "claude-code"),
            "codex": ("codex",)}.get(p, (p,))


def uninstall_plan(cwd: str, home: Optional[str] = None,
                   mechanism: Optional[str] = None,
                   probe_platform: bool = False,
                   platform: Optional[str] = None) -> Dict[str, Any]:
    """Build the plan.  NOTHING IS WRITTEN HERE — this function only reads.

    The plan is produced whether or not a receipt exists, because the two
    answers a user needs are different: WITH a receipt we can restore byte
    pre-images; WITHOUT one we can still tell them what we can SEE, and we must
    refuse to claim a clean undo.
    """
    rp = receipt_path(home)
    actions: List[Dict[str, Any]] = []
    blockers: List[str] = []
    receipt: Optional[Dict[str, Any]] = None
    if Path(rp).exists():
        try:
            # CRUCIBLE-3 D03-F3 (DESTROYS, enabled by UNBOUND, reproduced twice).
            #
            # THE RECEIPT HAD TWO READERS AND ONLY THE NON-DESTRUCTIVE ONE CHECKED
            # IDENTITY. `_receipt_load` opens with a comment that is exactly right --
            # "IDENTITY BEFORE CONTENT ... a receipt path that is a link, a multiply
            # -named object or a non-regular file is refused BY NAME here, before any
            # byte is parsed" -- and this path, the one that DRIVES THE UNDO, called
            # `json.loads(read_text_if_regular(rp))` directly and never reached it.
            # Blast radius: attacker-chosen bytes into any in-domain config file,
            # including an executable SessionStart hook.
            #
            # The gated reader already existed. This is the third time in this cycle
            # the repair has been to CALL THE CORRECT PATH THAT WAS ALREADY THERE.
            receipt = _receipt_load(rp)
        except PathIsNotWhatWeNamed as e:
            receipt = None
            blockers.append(
                f"receipt REFUSED by the identity gate: {e}. The undo reads byte "
                f"pre-images from this file and writes them over your config, so a "
                f"receipt that is a symlink, a multiply-named object or a non-regular "
                f"file is refused BY NAME before a byte is parsed.")
        except (OSError, ValueError) as e:
            blockers.append(f"receipt unreadable: {type(e).__name__}: {e}")
    else:
        blockers.append(
            f"NO INSTALL RECEIPT at {rp}. Without a receipt there are no byte "
            f"pre-images, so nothing can be RESTORED — only removed, and only "
            f"where a Voidloom marker block identifies bytes as ours. An undo "
            f"that silently leaves residue is worse than no undo, so this run "
            f"REFUSES to report a clean uninstall.")

    # C1: THE DOMAIN IS DERIVED FROM THE WORLD, AND THE RECEIPT IS INTERSECTED
    # WITH IT.  Everything the receipt names that this environment's own
    # enumerators cannot see is reported as out-of-domain and NEVER executed.
    _dom = undo_domain(cwd, mechanism=mechanism, platform=platform, home=home)
    out_of_domain: List[Dict[str, Any]] = []
    if receipt:
        for item in receipt.get("touched", []):
            # MECHANISMS ARE VERBS: `hooks uninstall` undoes hooks entries,
            # `instructions uninstall` undoes instructions entries.  A v1 entry
            # with no mechanism belongs to whichever verb is asked.
            if mechanism and item.get("mechanism") not in (None, mechanism):
                continue
            path = item.get("path", "")
            # --- the four faces, closed as ONE geometric test -----------------
            if path and path not in _dom["allowed"]:
                _why = (f"this invocation's domain is "
                        f"{sorted(_dom['allowed'])[:4]}{'…' if len(_dom['allowed']) > 4 else ''}")
                out_of_domain.append({
                    "path": path, "action": item.get("action"),
                    "platform": item.get("platform"), "mechanism": item.get("mechanism"),
                    "refusal_code": SCOPE_EXCEEDS_FLAG,
                    "reason": (
                        "the receipt names this path but the live enumerators for "
                        + (f"--platform {platform} " if platform else "")
                        + f"mechanism '{mechanism or 'any'}' do not see it in this "
                          "environment, so the undo REFUSES it rather than executing "
                          "a stored claim: " + _why),
                    "retained": True})
                continue
            action = item.get("action", "unknown")
            pre = item.get("preimage_path")
            expect = item.get("preimage_sha256")
            now = _sha256_file(path)
            # THE PLAN LISTS THE RESOLVED TARGET, NOT ONLY THE NAME (R7, as
            # amended). The verb listed the path NAMED IN THE RECEIPT and wrote
            # that path RESOLVED THROUGH ANY SYMLINK, and the two differ exactly
            # in the case that destroys a user's file. Both are printed now, and
            # a difference is a BLOCKER rather than something followed.
            ident = path_identity(path)
            entry = {"path": path, "action": action,
                     "preimage_path": pre, "preimage_sha256": expect,
                     "current_sha256": now,
                     "current_exists": Path(path).exists(),
                     "resolved_path": ident["resolved_path"],
                     "is_symlink": ident["is_symlink"],
                     "symlink_target": ident["symlink_target"],
                     "resolved_differs_from_named": ident["differs"],
                     # THE PLAN IDENTIFIES AN OBJECT, NOT ONLY A NAME. Carried
                     # into execution so the write can be refused for landing on
                     # anything else — the three shapes (swap, renamed prefix,
                     # planted target) that no path-shaped check can see.
                     "link_count": ident["link_count"],
                     "planned_identity": planned_identity(ident)}
            # THE UNIT OF UNDO IS THE ELEMENT, NEVER THE FILE, ON A FILE WE DO
            # NOT OWN (R5 as amended).  Claude Code rewrites settings.json on
            # every permission grant; restoring install-day bytes over today's
            # file would destroy days of grants and report RESTORED.  So: the
            # receipt carries the POST-IMAGE sha; current == post-image -> the
            # pre-image restore is exact; current != post-image -> remove OUR
            # element only, under the same losslessness gate.
            post_sha = item.get("postimage_sha256")
            entry["postimage_sha256"] = post_sha
            entry["element"] = item.get("element")
            # THE RECEIPT IDENTITY OF A HOOK ELEMENT IS ITS COMMAND (R13): the
            # element-level undo may remove a group only if it carries our marker
            # AND this exact command.  A v1 receipt (no element) uses the command
            # this brain installs; a look-alike from another brain never matches.
            _own_cmds = element_commands(item.get("element")) if isinstance(item.get("element"), dict) else []
            _own_cmd = _own_cmds[0] if _own_cmds else hook_command(home)
            entry["own_command"] = _own_cmd
            entry["element_json_path"] = item.get("element_json_path")
            entry["created_keys"] = item.get("created_keys") or []
            entry["created_dirs"] = item.get("created_dirs") or []
            entry["contract"] = item.get("contract")
            entry["element_kind"] = item.get("element_kind") or (
                "json_element" if item.get("element") else None)
            # CRUCIBLE-3 D03-F1 (DESTROYS, unwaivable, reproduced twice).
            #
            # The harm this guard exists to prevent is stated in its own comment
            # six lines above: "Claude Code rewrites settings.json on every
            # permission grant; restoring install-day bytes over today's file would
            # destroy days of grants and report RESTORED."
            #
            # THAT HARM IS A PROPERTY OF THE FILE -- `now != post_sha` -- and the
            # guard was ALSO gated on `item.get("element")`, a field that describes
            # HOW TO DO THE NARROWER UNDO, not WHETHER THE FILE CHANGED. Drop
            # `element` (a v1 receipt has none) and the guard did not engage at all:
            # control fell through every `elif` to the final `else`, which labelled
            # it "pre-image restore (no post-image recorded)" -- while a post-image
            # WAS recorded and the file HAD changed -- and overwrote the whole file.
            #
            # The condition is now the harm. Whether we can do the narrow undo
            # decides WHAT WE DO ABOUT IT, never whether we noticed.
            _changed_since_install = (bool(post_sha) and entry["current_exists"]
                                      and now != post_sha)
            if _changed_since_install and action in ("restore_preimage", "delete_file") \
                    and not item.get("element"):
                # The file changed AND we have no element to remove. There is no
                # safe automatic undo here: a full restore destroys the user's
                # changes and a narrow one is not available. REFUSE, and say what
                # the operator can do by hand.
                out_of_domain.append({
                    "path": path, "action": action,
                    "platform": item.get("platform"), "mechanism": item.get("mechanism"),
                    "refusal_code": "CHANGED_SINCE_INSTALL_NO_ELEMENT",
                    "reason": (
                        f"the file changed since install (post-image {str(post_sha)[:12]}…, "
                        f"current {str(now)[:12]}…) and this receipt records no element, so "
                        f"there is no narrow undo to perform. A pre-image restore would "
                        f"overwrite everything changed since install. REFUSED. Remove our "
                        f"entry by hand, or restore from {pre} if you are certain nothing "
                        f"else in that file matters."),
                    "retained": True})
                continue
            if post_sha and item.get("element") and entry["current_exists"] \
                    and now != post_sha and action in ("restore_preimage", "delete_file"):
                entry["undo_class"] = "element-level"
                entry["undo_reason"] = (
                    f"the file changed since install (post-image {post_sha[:12]}…, "
                    f"current {str(now)[:12]}…); restoring the pre-image would destroy "
                    f"changes the user made since; only OUR element is removed")
                # A JSON element is removed by the JSON path; a marker BLOCK in a
                # text file by the marker path (the existing action).
                entry["action"] = action = ("remove_marker_block"
                                            if entry["element_kind"] == "marker_block"
                                            else "remove_element")
            elif post_sha and now == post_sha:
                entry["undo_class"] = ("delete (we created this file; unchanged since)"
                                       if action == "delete_file" else
                                       "pre-image restore (file unchanged since install)")
            elif action == "remove_marker_block":
                entry["undo_class"] = "marker-block removal"
            elif action == "delete_file":
                entry["undo_class"] = "delete (no post-image recorded)"
            else:
                entry["undo_class"] = "pre-image restore (no post-image recorded)"
            if refusal_code(ident) is not None:
                entry["blocked"] = refusal_reason(ident)
                entry["blocked_code"] = refusal_code(ident)
                blockers.append(
                    f"{path}: {refusal_code(ident)} — "
                    + (f"the object here has {ident['link_count']} names, at "
                       f"least one of which this plan does not list"
                       if refusal_code(ident) == "HAS_OTHER_NAMES"
                       else f"RESOLVES TO {ident['resolved_path']}, a path this "
                            f"plan does not name")
                    + "; refusing to write it")
            elif action == "remove_element":
                # THE GATE, ON THE PLAN: the CURRENT bytes must be reproducible
                # under a contract, or the removal cannot be lossless and the
                # plan says so (and prints the element) rather than reformatting.
                try:
                    _cur = read_bytes_if_regular(path)
                    _g = lossless_json_contract(_cur)
                except OSError as _e:
                    _g = {"lossless": False, "code": f"unreadable: {_e}"}
                entry["element_gate"] = {k: v for k, v in _g.items() if k != "parsed"}
                if not _g.get("lossless"):
                    entry["blocked"] = (
                        f"element-level removal cannot be lossless "
                        f"({_g.get('code')}: {_g.get('reason')}); refusing to reformat "
                        f"a file the user owns. REMOVE THIS ELEMENT BY HAND: "
                        f"{json.dumps(item.get('element'), ensure_ascii=False)}")
                    entry["blocked_code"] = "ELEMENT_REMOVAL_NOT_LOSSLESS"
                    entry["manual_removal"] = item.get("element")
                    blockers.append(f"{path}: ELEMENT_REMOVAL_NOT_LOSSLESS — the element "
                                    f"is printed for manual removal")
                else:
                    _new, _info = remove_hook_element(_g["parsed"], item.get("element"),
                                                      entry["created_keys"],
                                                      own_command=_own_cmd)
                    entry["element_match"] = _info
                    if not _info.get("found"):
                        entry["blocked"] = (f"no element of ours is present to remove: "
                                           f"{_info.get('reason')}")
                        entry["blocked_code"] = ("ELEMENT_AMBIGUOUS" if _info.get("ambiguous")
                                                 else "ELEMENT_NOT_OURS" if _info.get("not_ours")
                                                 else "ELEMENT_ABSENT")
                        if _info.get("not_ours"):
                            # the refusal PRINTS the element so the user can decide
                            entry["manual_removal"] = item.get("element")
                        blockers.append(f"{path}: {entry['blocked_code']} — "
                                        f"{_info.get('reason')}")
            elif action == "remove_marker_block":
                # THE RECEIPT'S BLOCK, ON THE PLAN (R13, text mechanism): a marker
                # pair whose content is not our block is another install's.
                if entry["current_exists"] and not entry.get("blocked"):
                    try:
                        _cur_txt = read_text_if_regular(path, errors="replace")
                    except OSError:
                        _cur_txt = None
                    if _cur_txt is not None:
                        _elem_txt = item.get("element") if isinstance(item.get("element"), str) else None
                        _, _minfo = remove_marker_block_text(_cur_txt, _elem_txt)
                        entry["element_match"] = _minfo
                        if not _minfo.get("found") and (_minfo.get("not_ours") or _minfo.get("ambiguous")):
                            entry["blocked"] = _minfo.get("reason")
                            entry["blocked_code"] = ("MARKER_NOT_OURS" if _minfo.get("not_ours")
                                                     else "MARKER_AMBIGUOUS")
                            entry["manual_removal"] = _elem_txt
                            blockers.append(f"{path}: {entry['blocked_code']} — {_minfo.get('reason')}")
            elif action == "restore_preimage":
                if not pre or not Path(pre).exists():
                    entry["blocked"] = "pre-image file missing"
                    blockers.append(f"{path}: pre-image missing")
                elif expect and _sha256_file(pre) != expect:
                    entry["blocked"] = "pre-image hash mismatch"
                    blockers.append(f"{path}: pre-image hash mismatch")
            actions.append(entry)

    # Independently of the receipt, report what a resolver can SEE right now.
    # FILE-DERIVED unless consented: a plan is read-only, and a dry run that
    # spawned `codex app-server` wrote 119 paths under ~/.codex (EXECUTED,
    # reflector iter 1, P4).  A dry run must be dry in all three directions.
    observed = []
    st = status(cwd, home=home, probe_platform=probe_platform)
    for row in st["platforms"]:
        if row["installed"]:
            observed.append({"platform": row["id"],
                             "resolved": row["resolved"]})

    return {
        "receipt_path": rp, "receipt_present": receipt is not None,
        "actions": actions, "action_count": len(actions),
        "blockers": blockers, "blocked": bool(blockers),
        # C1: the receipt rows this invocation REFUSED, counted and named.  The
        # declared trade (KAEL-EM): a legacy element the live enumerator cannot
        # see is RETAINED, not deleted — orphan-retention in exchange for a
        # data-loss Critical, with the orphans on the page rather than implied.
        "out_of_domain": out_of_domain,
        "out_of_domain_count": len(out_of_domain),
        "scope": {"mechanism": mechanism, "platform": platform,
                  "domain_size": len(_dom["allowed"]),
                  "by_platform": _dom["by_platform"],
                  "derived_from": _dom["derived_from"], "note": _dom["note"]},
        "observed_live_installs": observed,
        "irreversible_residue": IRREVERSIBLE_RESIDUE,
        "generated_at": utc_now(),
    }


def uninstall_execute(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the plan.  Only reachable behind an explicit --yes."""
    done: List[Dict[str, Any]] = []
    for a in plan["actions"]:
        if a.get("blocked"):
            done.append({**a, "result": "SKIPPED", "reason": a["blocked"]})
            continue
        try:
            if a["action"] == "restore_preimage":
                # THE POST-CONDITION DECIDES THE STATE, AND IT DOES NOT TRAVEL
                # THE WRITE'S OWN PATH.  write_bytes and _sha256_file BOTH
                # followed the symlink, so `after == want` agreed with the write
                # by construction and RESTORED was reported for a file the plan
                # never listed. guarded_write_bytes refuses a symlinked or
                # re-resolving target, opens O_NOFOLLOW, and decides identity by
                # comparing fstat of the descriptor written against lstat — NOT
                # followed — of the path the plan named.
                w = guarded_write_bytes(
                    a["path"], read_bytes_if_regular(a["preimage_path"]),
                    expect=a.get("planned_identity"),
                    expect_sha256=plan_content(a))
                after = w["sha256_after"]
                want = a.get("preimage_sha256") or _sha256_file(a["preimage_path"])
                if after == want and w["same_object"]:
                    done.append({**a, "result": "RESTORED", "sha256_after": after,
                                 "verified_against_preimage": True,
                                 "write_landed_on_the_named_object": True,
                                 "verify_path": w["verify_path"]})
                else:
                    done.append({**a, "result": "ERROR", "sha256_after": after,
                                 "verified_against_preimage": False,
                                 "write_landed_on_the_named_object":
                                     w["same_object"],
                                 "verify_path": w["verify_path"],
                                 "reason": (
                                     f"post-restore sha {after} != pre-image sha "
                                     f"{want}" if after != want else
                                     "the bytes landed on an object that is NOT "
                                     "the path the plan named")
                                 + "; refusing to report RESTORED"})
            elif a["action"] == "remove_marker_block":
                # READ-MODIFY-WRITE MUST WRITE BACK TO THE OBJECT IT READ. The
                # identity comes from fstat of the descriptor actually read, so
                # a swap between the read and the write is refused rather than
                # silently applied to whatever now holds the name.
                _read_ident: Dict[str, Any] = {}
                txt = guarded_read_text(a["path"], identity_out=_read_ident,
                                        expect=a.get("planned_identity"),
                                        expect_sha256=plan_content(a))
                # The write-back is bound to the bytes we JUST READ, so a swap
                # in the read->write window is refused by content as well as by
                # identity -- the window inode reuse makes invisible.
                _read_sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
                # OUR BLOCK BY RECEIPT CONTENT, NEVER "the first marker pair" (R13):
                # a pair whose content is not the block this receipt installed is
                # another install's block and is refused, printed, left alone.
                _elem_txt = a.get("element") if isinstance(a.get("element"), str) else None
                new, _minfo = remove_marker_block_text(txt, _elem_txt)
                if not _minfo.get("found") and (_minfo.get("not_ours") or _minfo.get("ambiguous")):
                    done.append({**a, "result": "REFUSED_PRINT_ELEMENT",
                                 "refusal_code": ("MARKER_NOT_OURS" if _minfo.get("not_ours")
                                                  else "MARKER_AMBIGUOUS"),
                                 "manual_removal": _elem_txt,
                                 "reason": _minfo.get("reason")})
                    continue
                # POST-CONDITION, NOT AN ASSUMPTION.  The regex is non-greedy, so
                # a paired block followed by an UNPAIRED trailing MARKER_BEGIN had
                # the paired block removed, the orphan left, and the run reported
                # MARKER_REMOVED / removed=1 / errors=0 / skipped=0 while our
                # injected lines were still sitting in the user's file. That is
                # R7's own FAIL disposition — "an undo that silently leaves
                # residue is worse than no undo" — and the residue was not in the
                # named IRREVERSIBLE list, because it is residue the uninstaller
                # BELIEVED it had removed. Name it instead.
                residue = (MARKER_BEGIN in new) or (MARKER_END in new)
                if new == txt:
                    done.append({**a, "result": "NO_MARKER_FOUND"})
                elif residue:
                    _w = guarded_write_text(a["path"], new,
                                            expect=_read_ident,
                                            expect_sha256=_read_sha)
                    done.append({**a, "result": "PARTIAL_UNPAIRED_MARKER",
                                 "sha256_after": _w["sha256_after"],
                                 "write_landed_on_the_named_object":
                                     _w["same_object"],
                                 "residual_marker_begin": MARKER_BEGIN in new,
                                 "residual_marker_end": MARKER_END in new,
                                 "reason": ("an unpaired marker remains; the "
                                            "lines after it are OURS and were "
                                            "NOT removed. Remove them by hand or "
                                            "supply a byte pre-image. This run "
                                            "does NOT claim a clean uninstall "
                                            "for this path.")})
                else:
                    _w = guarded_write_text(a["path"], new,
                                            expect=_read_ident,
                                            expect_sha256=_read_sha)
                    done.append({**a, "result": "MARKER_REMOVED",
                                 "sha256_after": _w["sha256_after"],
                                 "write_landed_on_the_named_object":
                                     _w["same_object"],
                                 "no_residual_marker": True})
            elif a["action"] == "remove_element":
                # ELEMENT-LEVEL UNDO, under the SAME gate as the install.
                _read_ident2: Dict[str, Any] = {}
                _txt = guarded_read_text(a["path"], identity_out=_read_ident2,
                                         expect=a.get("planned_identity"),
                                         expect_sha256=plan_content(a))
                _raw = _txt.encode("utf-8")
                _read_sha2 = hashlib.sha256(_raw).hexdigest()
                _g2 = lossless_json_contract(_raw)
                if not _g2["lossless"]:
                    done.append({**a, "result": "REFUSED_PRINT_ELEMENT",
                                 "refusal_code": "ELEMENT_REMOVAL_NOT_LOSSLESS",
                                 "manual_removal": a.get("element"),
                                 "reason": (f"{_g2.get('code')}: {_g2.get('reason')}; "
                                            f"nothing was written — remove the printed "
                                            f"element by hand")})
                    continue
                _new2, _info2 = remove_hook_element(_g2["parsed"], a.get("element"),
                                                    a.get("created_keys") or [],
                                                    own_command=a.get("own_command"))
                if not _info2.get("found"):
                    done.append({**a, "result": ("REFUSED_PRINT_ELEMENT"
                                                 if (_info2.get("not_ours") or _info2.get("ambiguous"))
                                                 else "NO_ELEMENT_FOUND"),
                                 "refusal_code": ("ELEMENT_NOT_OURS" if _info2.get("not_ours")
                                                  else "ELEMENT_AMBIGUOUS" if _info2.get("ambiguous")
                                                  else None),
                                 "manual_removal": a.get("element"),
                                 "look_alikes": _info2.get("look_alikes"),
                                 "reason": _info2.get("reason")})
                    continue
                _post2 = serialize_under_contract(_new2, _g2["contract"])
                _dd = diff_is_pure_insertion(_post2, _raw)   # removal = insertion reversed
                if not _dd["pure"]:
                    done.append({**a, "result": "REFUSED_PRINT_ELEMENT",
                                 "refusal_code": "ELEMENT_REMOVAL_NOT_PURE",
                                 "manual_removal": a.get("element"),
                                 "reason": ("removing the element would change lines that "
                                            "are not ours; nothing was written"),
                                 "diff_violations": _dd["violations"][:3]})
                    continue
                _w2 = guarded_write_bytes(a["path"], _post2, expect=_read_ident2,
                                          expect_sha256=_read_sha2)
                # READ BACK through the independent path; it must parse or the
                # bytes we read go straight back, same run.
                _ok2 = False
                try:
                    _vb = read_bytes_if_regular(_w2["verify_path"])
                    json.loads(_vb.decode("utf-8"))
                    _ok2 = (_vb == _post2 and _w2["same_object"])
                except (OSError, ValueError, UnicodeDecodeError):
                    _ok2 = False
                if not _ok2:
                    # The rollback expects the bytes the gated write just put there
                    # (identity AND content) -- but D05-C1 showed that on a SHORT WRITE
                    # the bytes at the name are the TRUNCATED post-image, so pinning to
                    # `_post2` makes the restore REFUSE ITSELF. The pin still earns its
                    # place; it just has to name what is ACTUALLY AT THE NAME NOW.
                    try:
                        _now2 = _sha256_file(a["path"])
                    except OSError:
                        _now2 = None
                    _restored2, _rb, _rb_err2 = False, {}, None
                    try:
                        _rb = guarded_write_bytes(a["path"], _raw, expect=_read_ident2,
                                                  expect_sha256=_now2)
                        _restored2 = (_rb.get("sha256_after")
                                      == hashlib.sha256(_raw).hexdigest())
                    except (PathIsNotWhatWeNamed, OSError) as _e2:
                        _rb_err2 = f"{type(_e2).__name__}: {_e2}"
                    done.append({**a, "result": "ERROR",
                                 # D05-C2: derived from what the rollback OBSERVED,
                                 # never asserted alongside its own failure.
                                 "reason": ("the post-image did not read back as the "
                                            "planned JSON; "
                                            + ("the previous bytes were restored in the "
                                               "same run" if _restored2 else
                                               "AND THE ROLLBACK FAILED: the file is not "
                                               "intact")),
                                 "rollback_sha256": _rb.get("sha256_after"),
                                 "rollback_error": _rb_err2})
                    continue
                done.append({**a, "result": "ELEMENT_REMOVED",
                             "matched_by": _info2.get("matched_by"),
                             "pruned_keys": _info2.get("pruned_keys"),
                             "sha256_after": _w2["sha256_after"],
                             "write_landed_on_the_named_object": _w2["same_object"],
                             "verify_path": _w2["verify_path"]})
            elif a["action"] == "delete_file":
                # THE POST-CONDITION DECIDES THE STATE.  unlink(missing_ok=True)
                # made a path that was never there indistinguishable from one we
                # removed, and DELETED was claimed for both — with deleted=1 and
                # errors=0. An operator reading deleted=1 believes a file of ours
                # is gone; if the receipt names a stale or mistyped path, the real
                # file is still on disk and the report says otherwise.
                d_ = guarded_unlink(a["path"],
                                    expect=a.get("planned_identity"),
                                    expect_sha256=plan_content(a))
                if d_["existed_before"] and d_["gone_after"]:
                    _dirs = [guarded_rmdir_if_empty(x) for x in (a.get("created_dirs") or [])]
                    done.append({**a, "result": "DELETED",
                                 "existed_before": True,
                                 "created_dirs_removed": _dirs})
                elif not d_["existed_before"]:
                    done.append({**a, "result": "ALREADY_ABSENT",
                                 "existed_before": False,
                                 "reason": ("the path was not present before "
                                            "this run; nothing of ours was "
                                            "removed here, and this is NOT "
                                            "counted as a deletion")})
                else:
                    done.append({**a, "result": "ERROR",
                                 "existed_before": True,
                                 "reason": ("unlink returned but the path is "
                                            "still present")})
            else:
                done.append({**a, "result": "SKIPPED",
                             "reason": f"unknown action {a['action']!r}"})
        except PathIsNotWhatWeNamed as e:
            # NOT AN ERROR — A REFUSAL, AND IT NAMES THE OBJECT IT WOULD HAVE
            # TOUCHED.  Reached when the filesystem changed between the plan and
            # the write; the plan blocks the same shape before we get here.
            _id = path_identity(a["path"])
            done.append({**a, "result": "SKIPPED", "reason": str(e),
                         "refused_by": getattr(e, "refused_by", "unknown"),
                         "refusal_code": refusal_code(_id),
                         "is_symlink": _id["is_symlink"],
                         "link_count": _id["link_count"],
                         "has_other_names": _id["has_other_names"],
                         "resolves_to": _id["resolved_path"]})
        except OSError as e:
            done.append({**a, "result": "ERROR",
                         "reason": f"{type(e).__name__}: {e}"})
    return {"applied": done,
            "restored": sum(1 for d in done if d["result"] == "RESTORED"),
            "removed": sum(1 for d in done if d["result"] == "MARKER_REMOVED"),
            "elements_removed": sum(1 for d in done if d["result"] == "ELEMENT_REMOVED"),
            "refused_print_element": sum(1 for d in done
                                         if d["result"] == "REFUSED_PRINT_ELEMENT"),
            "partial": sum(1 for d in done
                           if d["result"] == "PARTIAL_UNPAIRED_MARKER"),
            "deleted": sum(1 for d in done if d["result"] == "DELETED"),
            # "I removed nothing here" is a DIFFERENT sentence from "I removed
            # one of ours", and it gets its own counter rather than borrowing
            # the one an operator reads as work done.
            "already_absent": sum(1 for d in done
                                  if d["result"] == "ALREADY_ABSENT"),
            "skipped": sum(1 for d in done if d["result"] in
                           ("SKIPPED", "NO_MARKER_FOUND", "NO_ELEMENT_FOUND",
                            "REFUSED_PRINT_ELEMENT")),
            "errors": sum(1 for d in done if d["result"] == "ERROR")}


# ===========================================================================
# INSTALL — R5/R6/R7 AS AMENDED.  THE SAME IDENTITY-GATED WRITER AS UNINSTALL,
# THE LOSSLESSNESS GATE IN FRONT OF IT, THE POST-IMAGE BEHIND IT, AND THE
# ELEMENT AS THE UNIT OF UNDO.
# ===========================================================================
# WHY THE GATE EXISTS (Amendment 1, vendor fact, Claude Code 2.1.270): "When a
# settings.json file fails to parse (invalid JSON, JSON comments, SCHEMA
# MISMATCH), the file is skipped and any rules it contained -- including
# permission allow/deny lists -- are not applied."  So the blast radius of a
# merge that reformats, tears, or carries an unknown field is not "our hook is
# silent"; it is EVERY hook, the permission allowlist, statusLine and autoMode
# going dark, SILENTLY.  The only merge that can survive a platform which changes
# roughly daily is one that can PROVE, per file, that its serializer reproduces
# the user's bytes exactly -- and refuses otherwise, printing the element for a
# manual paste.  The JSONC detector is THE PARSER, never a grep: `/*` sits inside
# a string on line 195 of the operator's own file.
#
# WHY THE ELEMENT IS THE UNIT OF UNDO (Amendment 1, R5 extended): Claude Code
# rewrites settings.json on every permission grant.  An uninstall that restored
# install-day bytes over today's file would destroy days of the user's grants and
# report RESTORED / verified_against_preimage=True -- the symlink finding's
# shape, one level up.  So the receipt records the POST-IMAGE sha; at uninstall,
# current == post-image restores the pre-image, current != post-image removes OUR
# ELEMENT ONLY under the same gate, and a removal that cannot be lossless REFUSES
# and prints the element.  On a file we do not own, the unit of undo is the
# element, never the file.

JSON_INDENT = 2
RECEIPT_SCHEMA_VERSION = 2
PREIMAGE_STORE_DIRNAME = "voidloom_preimages"
# THE MARKER IS THE COMMAND'S OWN BASENAME.  No extra field rides in the element
# (an unknown field is the SCHEMA MISMATCH that kills the whole file); the hook
# file's name under hooks/ is what identifies an element as ours, and it is what
# the resolver probe has keyed on since the first --status.
HOOK_BASENAME = "session_start_inject.py"
# C3.  Two installers shipped two hooks.  `voidloom hooks install` writes
# hooks/session_start_inject.py -- the one that emits the arrival line.
# `wire_persona.sh` / `wire_persona.ps1` / `install.ps1` wired
# hooks/session-start.mjs instead, and this marker could not see it: measured,
# `node .../hooks/session-start.mjs` -> False.  So on the DOCUMENTED install
# path `hooks --status` reported nothing installed, the F1 rung had nothing to
# grade, and `hooks uninstall` could not remove what the product itself had
# wired.  Two markers, because the two questions are different:
#
#   HOOK_MARKER_RE   is this element OURS?     -- status, uninstall, the Codex
#                                                 trust probe, F1
#   HOOK_CURRENT_RE  is it the CURRENT hook?   -- the install idempotence
#                                                 decision, and ONLY that
#
# Collapsing them would mean a user on the legacy hook running `hooks install`
# gets "already_installed" and stays broken -- the widening would have made the
# product worse at the one moment it is asked to repair itself.
HOOK_LEGACY_BASENAMES = ("session-start.mjs",)


def _marker_re(*basenames: str) -> "re.Pattern[str]":
    alts = "|".join(re.escape(b) for b in basenames)
    return re.compile(r"(^|[\s/\\])hooks[/\\](?:" + alts + r")(\s|\"|'|$)")


HOOK_CURRENT_RE = _marker_re(HOOK_BASENAME)
HOOK_MARKER_RE = _marker_re(HOOK_BASENAME, *HOOK_LEGACY_BASENAMES)
# Codex spills additionalContext above this many tokens (vendor default 2,500 when
# null).  Set AT INSTALL TIME: changing it later changes the hook's hash and forces
# re-approval.  The hook's own Codex budget (4000) sits below it by construction.
CODEX_ADDITIONAL_CONTEXT_LIMIT = 6000
# 2026-10-26: was "startup". Codex's SessionStart sources are startup, resume,
# clear, compact and fork (learn.chatgpt.com/docs/hooks), and the matcher is a
# regex over `source`; "startup" alone meant a RESUMED thread never received
# the brain. Measured on the owner's desktop: one Codex thread started
# 2026-09-29, fired once, and was resumed daily through 2026-10-26 (88 sub-agent
# threads) without a single fire. None = omit the matcher = every source, the
# same shape as the Claude Code element.
CODEX_SESSION_START_MATCHER = None
HOOKS_LIST_TIMEOUT_S = 25.0

INSTALL_PLATFORMS = ("claude", "codex")
_PLATFORM_ALIAS = {"claude": "claude-code", "claude-code": "claude-code", "codex": "codex"}


def default_install_platform() -> Tuple[Optional[str], Dict[str, str]]:
    """(platform, found) for `hooks install` with no --platform (#11).

    ``found`` maps each platform to its config dir when that dir EXISTS, resolved
    by the platforms' own variables exactly as _expand_platform_path resolves them.
    ``platform`` is the one platform found, or None when both or neither exist --
    a default is taken only where nothing else could be meant.
    """
    found: Dict[str, str] = {}
    for p, rel in (("claude", "{claude_config}"), ("codex", "{codex_home}")):
        d = _expand_platform_path(rel, "/")
        if os.path.isdir(d):
            found[p] = d
    return (next(iter(found)) if len(found) == 1 else None), found


def _expand_platform_path(rel: str, cwd: str) -> str:
    """ONE expander for every platform path (the class, not the instance).

    ``{claude_config}`` -> $CLAUDE_CONFIG_DIR or ~/.claude; ``{codex_home}`` ->
    $CODEX_HOME or ~/.codex -- the platforms' OWN resolver variables, so a probe
    and an installer under a relocated home agree with the platform itself.
    Relative paths are joined to ``cwd``.
    """
    claude_cfg = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or str(Path.home() / ".claude")
    codex_home = (os.environ.get("CODEX_HOME") or "").strip() or str(Path.home() / ".codex")
    p = rel.replace("{claude_config}", claude_cfg).replace("{codex_home}", codex_home)
    if not (p.startswith("~") or p.startswith("/")):
        p = str(Path(cwd) / p)
    return str(Path(p).expanduser())


def brain_home(home: Optional[str] = None) -> str:
    return (home or os.environ.get("WIZARD_LIBRARY_HOME")
            or str(Path(__file__).resolve().parent.parent))


def hook_command(home: Optional[str] = None) -> str:
    """The command string the platform will run.  PERMANENTLY STABLE by design:
    Codex pins trust to the hook definition's hash, so the string never changes
    after install (all logic lives inside the script).

    CRUCIBLE-3 U4/U6 found two Windows defects in the POSIX form: ``python3`` is
    not present on a python.org Windows install (so `hooks --status` can report
    ``installed: True`` over a hook that cannot fire), and the path is UNQUOTED
    while a Windows brain path routinely contains spaces (C:/Users/First Last).

    Both are fixed ON THE WINDOWS BRANCH ONLY, and that restriction is the whole
    care here: the POSIX string is returned BYTE-IDENTICAL because the stability
    promise above is load-bearing -- Codex trusts the hash, and changing the
    string on an existing install would silently UNTRUST the hook, which is the
    same outcome as the defect and harder to diagnose.
    """
    p = Path(brain_home(home)) / "hooks" / HOOK_BASENAME
    if os.name == "nt":
        exe = sys.executable or ""
        # sys.executable is the interpreter that is installing/running us, which is
        # the one we know exists. `py -3` is the documented fallback: the Windows
        # launcher ships with python.org installs when a bare `python` may not be
        # on PATH. Both the interpreter and the path are quoted.
        launcher = f'"{exe}"' if exe else "py -3"
        return f'{launcher} "{p}"'
    return f"python3 {p}"


PREIMAGE_STORE_ENV = "VOIDLOOM_PREIMAGE_STORE"


def preimage_store(home: Optional[str] = None) -> str:
    env = os.environ.get(PREIMAGE_STORE_ENV)
    if env:
        return env
    return str(Path(brain_home(home)) / PREIMAGE_STORE_DIRNAME)


# --- the losslessness gate ---------------------------------------------------
def _strip_jsonc_tolerant(text: str) -> str:
    """Best-effort JSONC -> JSON for COUNTING ONLY (never for writing): drops //
    and /* */ comments OUTSIDE strings and trailing commas.  A tolerant parse is
    how the refusal can still say 'N permission rules and M hooks' about a file
    the strict parser rejected; the method is NAMED in the output."""
    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    s = "".join(out)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return s


def count_claude_rules(doc: Any) -> Dict[str, int]:
    """How much would go dark if Claude Code skipped this file.  DERIVED."""
    rules = 0
    hooks = 0
    if isinstance(doc, dict):
        perms = doc.get("permissions") or {}
        if isinstance(perms, dict):
            for k in ("allow", "deny", "ask"):
                v = perms.get(k)
                if isinstance(v, list):
                    rules += len(v)
        hk = doc.get("hooks") or {}
        if isinstance(hk, dict):
            for groups in hk.values():
                for g in (groups or []) if isinstance(groups, list) else []:
                    if isinstance(g, dict):
                        hooks += len(g.get("hooks") or [])
    return {"permission_rules": rules, "hooks": hooks}


def serialize_under_contract(doc: Any, contract: Dict[str, Any]) -> bytes:
    # C4: allow_nan=False. The default emits BARE `NaN` / `Infinity`, which is
    # not JSON. Measured: python's own `json.loads` accepts them -- so the
    # organ's read-back verification passed -- while `node -e JSON.parse`
    # returns rc 1. We would have written a settings.json Claude Code cannot
    # load and then verified it with a parser more lenient than the platform's.
    # Raising here turns that into a typed refusal at plan time instead.
    text = json.dumps(doc, indent=int(contract.get("indent", JSON_INDENT)),
                      ensure_ascii=bool(contract.get("ensure_ascii", True)),
                      allow_nan=False)
    if contract.get("trailing_newline", True):
        text += "\n"
    return text.encode("utf-8")


def _first_difference(a: bytes, b: bytes) -> Dict[str, Any]:
    n = min(len(a), len(b))
    off = next((i for i in range(n) if a[i] != b[i]), n)
    line = a[:off].count(b"\n") + 1
    return {"byte_offset": off, "line": line,
            "preimage_len": len(a), "reserialized_len": len(b),
            "preimage_excerpt": a[max(0, off - 20): off + 40].decode("utf-8", "replace"),
            "reserialized_excerpt": b[max(0, off - 20): off + 40].decode("utf-8", "replace")}


def _nonstrict_json_values(doc: Any, path: str = "$") -> List[str]:
    """Paths to values python's json accepts and strict JSON does not.

    NaN / Infinity / -Infinity only: python emits them bare under the default
    allow_nan=True, and every JSON parser that is not python's rejects them.
    """
    out: List[str] = []
    if isinstance(doc, float):
        if doc != doc or doc in (float("inf"), float("-inf")):
            out.append(f"{path}={doc!r}")
    elif isinstance(doc, dict):
        for k, v in doc.items():
            out.extend(_nonstrict_json_values(v, f"{path}.{k}"))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.extend(_nonstrict_json_values(v, f"{path}[{i}]"))
    return out


def lossless_json_contract(raw: bytes) -> Dict[str, Any]:
    """THE GATE.  Parse the bytes, re-serialize under the contract (indent=2,
    ensure_ascii BOTH ways, trailing newline), and report whether either output
    equals the input BYTE-FOR-BYTE.  Anything else is a refusal code:

      NOT_UTF8 · NOT_JSON (JSONC included — the parser is the detector)
      · NOT_AN_OBJECT · LOSSY_SERIALIZATION (CRLF, tabs, 4-space, sorted keys,
      raw unicode, floats the two serializers spell differently, ...)

    Returns the parsed object and the contract that reproduced it, so the same
    contract is used for the write and recorded in the receipt.
    """
    out: Dict[str, Any] = {"lossless": False, "code": None, "parsed": None,
                           "contract": None, "tried": [], "tolerant_counts": None,
                           "tolerant_method": None}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        out["code"] = "NOT_UTF8"
        out["reason"] = f"the file is not valid UTF-8: {e}"
        return out
    try:
        doc = json.loads(text)
    except ValueError as e:
        out["code"] = "NOT_JSON"
        out["reason"] = (f"the strict parser rejected the file ({e}); Claude Code's own "
                         f"loader rejects the same file, comments included")
        try:
            tol = json.loads(_strip_jsonc_tolerant(text))
            out["tolerant_counts"] = count_claude_rules(tol)
            out["tolerant_method"] = ("comment-stripped, trailing-comma-stripped parse — "
                                      "for counting what is dark, never for writing")
        except ValueError:
            out["tolerant_counts"] = None
            out["tolerant_method"] = "even a comment-stripped parse fails; nothing can be counted"
        return out
    if not isinstance(doc, dict):
        out["code"] = "NOT_AN_OBJECT"
        out["reason"] = f"the top-level JSON value is a {type(doc).__name__}, not an object"
        return out
    # C4: python's json is MORE LENIENT THAN THE PLATFORM'S. `json.loads` above
    # accepts bare NaN / Infinity / -Infinity; `node -e JSON.parse` rejects them
    # (measured, rc 1), and Claude Code reads settings.json with a JS parser. So
    # a file that reached here can already be one the platform cannot load, and
    # under strict serialization we now cannot reproduce it either. Say both --
    # refusing without naming the cause would leave the user staring at a file
    # that "looks like JSON".
    _nonstrict = _nonstrict_json_values(doc)
    if _nonstrict:
        out["code"] = "NOT_STRICT_JSON"
        out["reason"] = (
            f"the file parses in python but carries {len(_nonstrict)} value(s) that are "
            f"not JSON ({', '.join(_nonstrict[:4])}). Python accepts them; the platform's "
            f"own loader does not, so this file is already unreadable to it. Nothing will "
            f"be written: we will not reproduce a file we cannot serialize as valid JSON.")
        return out
    out["parsed"] = doc
    best_diff: Optional[Dict[str, Any]] = None
    _unencodable: List[str] = []
    for ensure_ascii in (True, False):
        contract = {"indent": JSON_INDENT, "ensure_ascii": ensure_ascii,
                    "trailing_newline": True}
        # D05-M1: a LONE SURROGATE survives `json.loads` and survives
        # `ensure_ascii=True` (it re-escapes as \udXXX), then dies in
        # `text.encode("utf-8")` the moment `ensure_ascii=False` makes it a real
        # character. The caller (`install_plan`) invokes this gate outside any
        # `try`, so the verb exited on a TRACEBACK -- in a module whose whole
        # contract is that every refusal is TYPED. A contract that cannot encode
        # the document has simply failed to reproduce it, which is what this loop
        # already knows how to say. The shape is reachable from the platform's own
        # writer: ES2019 well-formed JSON.stringify emits lone surrogates as
        # \udXXX escapes and non-ASCII literally, so this is a settings.json
        # Claude Code itself would write.
        try:
            re_ser = serialize_under_contract(doc, contract)
        except (UnicodeEncodeError, UnicodeDecodeError) as e:
            out["tried"].append({"contract": contract, "byte_identical": False,
                                 "could_not_encode": f"{type(e).__name__}: {e}"})
            _unencodable.append(f"ensure_ascii={ensure_ascii}: {e}")
            continue
        same = (re_ser == raw)
        out["tried"].append({"contract": contract, "byte_identical": same})
        if same:
            out["lossless"] = True
            out["contract"] = contract
            return out
        d = _first_difference(raw, re_ser)
        if best_diff is None or d["byte_offset"] > best_diff["byte_offset"]:
            best_diff = d
    out["code"] = "LOSSY_SERIALIZATION"
    out["first_difference"] = best_diff
    if _unencodable:
        out["unencodable_contracts"] = _unencodable
    # Name the CAUSE. "a write would reformat bytes the user owns" is true but
    # misleading when the real blocker is a character no serializer can emit:
    # the operator would go looking for whitespace and find a surrogate.
    out["reason"] = (
        ("the file parses, but it carries at least one character no serializer here can "
         f"encode ({_unencodable[0].split(': ', 1)[-1]}); nothing will be written")
        if len(_unencodable) == 2 else
        ("the file parses, but neither serializer contract reproduces it "
         "byte-for-byte (first difference at line "
         f"{(best_diff or {}).get('line')}); a write would reformat bytes "
         "the user owns"
         + (f" — and one contract could not encode it at all ({_unencodable[0]})"
            if _unencodable else "")))
    return out


# --- the element ---------------------------------------------------------------
def hook_element(platform_id: str, command: str) -> Dict[str, Any]:
    """The ONE array element we add, per platform.  Shapes are the PROVEN ones:
    Claude Code — the exact shape already running on this machine (no matcher, no
    timeout); Codex — the shape executed 2026-09-08 with its matcher removed on
    2026-10-26 so a resumed thread fires too (additionalContextLimit pinned at
    install because it is part of the hash)."""
    if platform_id == "codex":
        el: Dict[str, Any] = {"hooks": [{"type": "command", "command": command,
                                         "additionalContextLimit": CODEX_ADDITIONAL_CONTEXT_LIMIT}]}
        if CODEX_SESSION_START_MATCHER is not None:
            el = {"matcher": CODEX_SESSION_START_MATCHER, **el}
        return el
    return {"hooks": [{"type": "command", "command": command}]}


def is_our_hook_element(el: Any, rx: "Optional[re.Pattern[str]]" = None) -> bool:
    """MARKER, NOT WORD: a SessionStart group whose command runs one of our hook
    basenames.  `rx` picks WHICH question is being asked -- HOOK_MARKER_RE for
    "ours" (current or legacy), HOOK_CURRENT_RE for "the current one"."""
    if not isinstance(el, dict):
        return False
    hooks = el.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        return False
    rx = rx or HOOK_MARKER_RE
    for h in hooks:
        if isinstance(h, dict) and isinstance(h.get("command"), str) \
                and rx.search(h["command"]):
            return True
    return False


def find_our_elements(groups: Any, rx: "Optional[re.Pattern[str]]" = None) -> List[int]:
    if not isinstance(groups, list):
        return []
    return [i for i, g in enumerate(groups) if is_our_hook_element(g, rx)]


ELEMENT_JSON_PATH = ["hooks", "SessionStart"]


def merge_hook_element(doc: Dict[str, Any], element: Dict[str, Any],
                       drop_indices: Sequence[int] = ()
                       ) -> Tuple[Dict[str, Any], List[List[str]]]:
    """Return (new_doc, created_keys).  Deep-copies; the input is untouched.  The
    element is APPENDED to hooks.SessionStart; keys that did not exist are
    recorded so the undo can remove exactly the containers we created.

    `drop_indices` removes SUPERSEDED elements of ours in the same write (C3):
    a user wired to the retired Node hook must end the call with exactly one
    SessionStart hook, not two -- one of which prints "brain not reachable" over
    a reachable brain.  Only indices this installer has already identified as
    OURS are ever passed here; a foreign hook is never an index."""
    new = json.loads(json.dumps(doc))
    created: List[List[str]] = []
    if not isinstance(new.get("hooks"), dict):
        if "hooks" in new and new["hooks"] is not None:
            raise ValueError("`hooks` exists and is not an object; refusing to replace it")
        new["hooks"] = {}
        created.append(["hooks"])
    if not isinstance(new["hooks"].get("SessionStart"), list):
        if "SessionStart" in new["hooks"] and new["hooks"]["SessionStart"] is not None:
            raise ValueError("`hooks.SessionStart` exists and is not an array; refusing")
        new["hooks"]["SessionStart"] = []
        created.append(["hooks", "SessionStart"])
    if drop_indices:
        keep = [g for i, g in enumerate(new["hooks"]["SessionStart"])
                if i not in set(drop_indices)]
        new["hooks"]["SessionStart"] = keep
    new["hooks"]["SessionStart"].append(json.loads(json.dumps(element)))
    return new, created


def element_commands(el: Any) -> List[str]:
    """Every ``command`` string inside a SessionStart group (the receipt IDENTITY
    of a hook element is its command: the path the platform will execute)."""
    if not isinstance(el, dict) or not isinstance(el.get("hooks"), list):
        return []
    return [h["command"] for h in el["hooks"]
            if isinstance(h, dict) and isinstance(h.get("command"), str)]


def remove_hook_element(doc: Dict[str, Any], element: Optional[Dict[str, Any]],
                        created_keys: Sequence[Sequence[str]],
                        own_command: Optional[str] = None
                        ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Remove OUR element and prune the containers WE created if now empty.

    MATCH BY OUR MARKER *AND* THE RECEIPT'S IDENTITY, NEVER BY BASENAME (R13).
    Deep-equal match first (the receipt's own element).  Second: a group that
    carries our marker AND whose command is EXACTLY the command the receipt
    installed (``own_command``; derived from the receipt element, or from
    ``hook_command()`` for a v1 receipt) -- the case where the user edited a
    sibling field (a timeout) but not the command.  A group whose command
    merely ends in our basename is a LOOK-ALIKE: another brain's install of
    this same hook on the same machine (this household has two brains), and
    it is NOT ours to remove.  EXECUTED (reflector, iter 1): with the command
    replaced by ``/opt/other-brain/hooks/session_start_inject.py`` the
    basename fallback removed the other brain's hook and reported
    ELEMENT_REMOVED / rc 0.  Two or more identity matches is AMBIGUOUS and
    refused.  ``info`` names every look-alike so the refusal can print them.
    """
    new = json.loads(json.dumps(doc))
    info: Dict[str, Any] = {"found": False, "matched_by": None, "index": None,
                            "own_command": own_command, "look_alikes": []}
    groups = (new.get("hooks") or {}).get("SessionStart") if isinstance(new.get("hooks"), dict) else None
    if not isinstance(groups, list):
        info["reason"] = "no hooks.SessionStart array is present"
        return new, info
    if own_command is None and element is not None:
        _cmds = element_commands(element)
        own_command = _cmds[0] if _cmds else None
        info["own_command"] = own_command
    idx = None
    if element is not None:
        for i, g in enumerate(groups):
            if g == element:
                idx = i
                info["matched_by"] = "deep_equal"
                break
    if idx is None:
        marks = find_our_elements(groups)
        ours = [i for i in marks
                if own_command is not None and own_command in element_commands(groups[i])]
        look_alikes = [i for i in marks if i not in ours]
        info["look_alikes"] = [{"index": i, "commands": element_commands(groups[i])}
                               for i in look_alikes]
        if len(ours) == 1:
            idx = ours[0]
            info["matched_by"] = "marker_and_receipt_command"
        elif len(ours) > 1:
            info["reason"] = (f"{len(ours)} elements carry our marker AND the receipt's "
                              f"command, and none equals the receipt's element; refusing "
                              f"to guess which is ours")
            info["ambiguous"] = ours
            return new, info
        elif look_alikes:
            info["reason"] = (f"{len(look_alikes)} element(s) carry our marker but their "
                              f"command is NOT the one this receipt installed "
                              f"({own_command!r}); they belong to another install of this "
                              f"hook (a second brain) or were re-pointed by hand -- "
                              f"refusing to remove another install's hook")
            info["not_ours"] = look_alikes
            return new, info
    if idx is None:
        info["reason"] = "no element of ours is present (nothing to remove)"
        return new, info
    del groups[idx]
    info["found"] = True
    info["index"] = idx
    pruned: List[List[str]] = []
    ck = [list(k) for k in created_keys]
    if ["hooks", "SessionStart"] in ck and not groups:
        del new["hooks"]["SessionStart"]
        pruned.append(["hooks", "SessionStart"])
    if ["hooks"] in ck and isinstance(new.get("hooks"), dict) and not new["hooks"]:
        del new["hooks"]
        pruned.append(["hooks"])
    info["pruned_keys"] = pruned
    return new, info


_MARKER_PAIR_RE = re.compile(re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END) + r"\n?",
                             flags=re.S)


def remove_marker_block_text(txt: str, element: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """The text twin of ``remove_hook_element`` (R13, same class, other mechanism).

    ``element`` is the receipt's own block (the exact bytes we appended).  With a
    receipt element: remove the ONE exact occurrence of it; two exact copies is
    AMBIGUOUS (refused); a marker PAIR whose content is not our block is a
    LOOK-ALIKE (another brain's block, or ours edited by hand) and is NOT ours to
    remove -- refused and printed.  Without a receipt element (a v1 receipt) the
    single marker pair is removed; more than one pair is refused.
    """
    info: Dict[str, Any] = {"found": False, "matched_by": None}
    pairs = list(_MARKER_PAIR_RE.finditer(txt))
    info["marker_pairs"] = len(pairs)
    if element:
        n = txt.count(element)
        if n == 1:
            i = txt.index(element)
            info.update({"found": True, "matched_by": "receipt_block_exact"})
            return txt[:i] + txt[i + len(element):], info
        if n > 1:
            info["ambiguous"] = n
            info["reason"] = f"{n} exact copies of our block are present; refusing to guess which is ours"
            return txt, info
        if pairs:
            info["not_ours"] = len(pairs)
            info["reason"] = (f"{len(pairs)} marker block(s) are present but none is the block this "
                              f"receipt installed; they belong to another install (a second brain) "
                              f"or were edited by hand -- refusing to remove another install's block")
            return txt, info
        info["reason"] = "no marker block is present (nothing to remove)"
        return txt, info
    if len(pairs) == 1:
        m = pairs[0]
        info.update({"found": True, "matched_by": "single_marker_pair_v1_receipt"})
        return txt[:m.start()] + txt[m.end():], info
    if len(pairs) > 1:
        info["ambiguous"] = len(pairs)
        info["reason"] = f"{len(pairs)} marker blocks and no receipt element; refusing to guess"
        return txt, info
    info["reason"] = "no marker block is present (nothing to remove)"
    return txt, info


def diff_is_pure_insertion(pre: bytes, post: bytes) -> Dict[str, Any]:
    """Every pre-image line survives into the post-image unchanged, except that a
    line may gain a trailing comma (the sibling before an appended element).
    Direction-agnostic: the same check grades a removal with pre/post swapped."""
    import difflib
    a = pre.decode("utf-8", "replace").split("\n")
    b = post.decode("utf-8", "replace").split("\n")
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    inserted, comma_only, violations = 0, 0, []
    opened_empty = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            inserted += (j2 - j1)
            continue
        # AN EMPTY CONTAINER THE USER ALREADY HAD, OPENED AROUND OUR ELEMENT.  A
        # user's `"SessionStart": []` (Claude Code writes one when the last hook
        # is removed) becomes `"SessionStart": [` + our lines + `]`: no line the
        # user owns is destroyed -- one line is SPLIT around an insertion -- yet
        # line-wise it is a `replace`, and the invariant REFUSED the install
        # (EXECUTED, iteration 2, on the RM16 fixture).  Accepted when the
        # removed line is exactly `<indent><key>: []` (optional comma) and the
        # added block opens with `<indent><key>: [` and closes with `<indent>]`
        # (same optional comma); direction-agnostic like the rest.
        #
        # THE CLASS HAS EXACTLY TWO MEMBERS (R14, iteration 3): the container
        # Claude Code writes when the user removes its last member is either
        # `"SessionStart": []` (the array) or `"hooks": {}` (the OBJECT, when
        # the last event key goes).  Iteration 2 fixed the array and the class
        # sweep missed its sibling: `"hooks": {}` was refused rc 3 as a
        # `replace` (EXECUTED, reflector iter 2, P1).  Both shapes now.
        if tag == "replace" and (i2 - i1) == 1 and (j2 - j1) >= 2:
            L = a[i1]
            stripped = L.rstrip()
            comma = stripped.endswith(",")
            core = stripped[:-1] if comma else stripped
            indent = L[:len(L) - len(L.lstrip())]
            _close = "]" if core.endswith("[]") else "}" if core.endswith("{}") else None
            if _close is not None and b[j1] == core[:-1] and b[j2 - 1] == indent + _close + ("," if comma else ""):
                opened_empty += 1
                inserted += (j2 - j1) - 2
                continue
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            ok = True
            for x, y in zip(a[i1:i2], b[j1:j2]):
                if not (x + "," == y or y + "," == x):
                    ok = False
                    break
            if ok:
                comma_only += (i2 - i1)
                continue
        violations.append({"op": tag, "removed": a[i1:i2][:5], "added": b[j1:j2][:5]})
    unified = list(difflib.unified_diff(a, b, fromfile="pre-image", tofile="post-image",
                                        lineterm="", n=2))
    return {"pure": not violations, "inserted_lines": inserted,
            "comma_only_changes": comma_only, "empty_containers_opened": opened_empty,
            "violations": violations, "unified_diff": unified[:400]}


# --- codex: the platform's OWN view, zero model calls -----------------------
def codex_hooks_list(cwd: str, timeout: float = HOOKS_LIST_TIMEOUT_S) -> Dict[str, Any]:
    """Ask Codex's app-server (stdio JSON-RPC: initialize -> hooks/list) which
    hooks IT loads for ``cwd``, with each hook's ``trustStatus`` (untrusted |
    trusted | modified | managed), ``source``, ``key`` and ``currentHash``.
    ``codex doctor --json`` does NOT see hooks; silence under ``codex exec`` is
    identical for 'untrusted' and 'never registered'; only this tells them apart.
    Read-only for us; Codex writes its own state files under CODEX_HOME on any
    invocation, which is the vendor's behaviour and is named here."""
    import shutil as _sh
    import subprocess as _sp
    exe = _sh.which("codex")
    out: Dict[str, Any] = {"queried": False, "binary": exe, "hooks": [], "ours": [],
                           "errors": [], "warnings": []}
    if not exe:
        out["reason"] = "codex binary not on PATH"
        return out
    req = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"clientInfo": {"name": "voidloom-doctor",
                                                 "version": "0.1.0"}}}),
           json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}}),
           json.dumps({"jsonrpc": "2.0", "id": 2, "method": "hooks/list",
                       "params": {"cwds": [cwd]}})]
    # The app-server exits on stdin EOF, so the requests are written and stdin
    # is held OPEN until the hooks/list reply (id 2) arrives or the deadline
    # passes; the read side is polled with select so a silent server cannot
    # hang the verb (the FIFO class, on a pipe).
    import select as _sel
    import time as _time
    lines: List[str] = []
    rc: Optional[int] = None
    err_tail = ""
    # THE SIDE EFFECT IS STATED BEFORE IT HAPPENS, AT THE ONE SPAWN SITE (R4/R5 v3,
    # iteration 3).  --help and the post-spawn envelope already named it; nothing at
    # run time told the user, before Codex wrote, that it was about to (EXECUTED,
    # reflector iter 2, P4).  The prefix named is the platform's own resolver
    # variable, expanded the way the installer expands it.
    _codex_home = (os.environ.get("CODEX_HOME") or "").strip() or str(Path.home() / ".codex")
    print(f"[voidloom hooks] --probe-platform: spawning `codex app-server` for hooks/list; "
          f"the vendor writes its own state under {_codex_home} (sqlite / wal / tmp)",
          file=sys.stderr, flush=True)
    try:
        p = _sp.Popen([exe, "app-server", "--listen", "stdio://"],
                      stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=cwd)
    except OSError as e:
        out["reason"] = f"{type(e).__name__}: {e}"
        return out
    try:
        assert p.stdin is not None and p.stdout is not None
        p.stdin.write(("\n".join(req) + "\n").encode("utf-8"))
        p.stdin.flush()
        deadline = _time.monotonic() + timeout
        buf = b""
        got = False
        while not got:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                out["reason"] = f"app-server did not answer hooks/list within {timeout}s"
                break
            r, _, _ = _sel.select([p.stdout], [], [], min(remaining, 1.0))
            if not r:
                if p.poll() is not None:
                    out["reason"] = f"app-server exited (rc={p.returncode}) before answering"
                    break
                continue
            chunk = os.read(p.stdout.fileno(), 65536)
            if not chunk:
                out["reason"] = f"app-server closed stdout (rc={p.poll()}) before answering"
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", "replace").strip()
                if s:
                    lines.append(s)
                try:
                    o = json.loads(s)
                except ValueError:
                    continue
                if o.get("id") == 2:
                    got = True
                    out["queried"] = True
                    if "error" in o:
                        out["reason"] = f"hooks/list error: {o['error']}"
                    else:
                        for entry in (o.get("result") or {}).get("data") or []:
                            out["hooks"].extend(entry.get("hooks") or [])
                            out["errors"].extend(entry.get("errors") or [])
                            out["warnings"].extend(entry.get("warnings") or [])
                    break
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            err_tail = (p.stderr.read() or b"").decode("utf-8", "replace")[-300:] if p.stderr else ""
        except Exception:
            err_tail = ""
        rc = p.returncode
    out["rc"] = rc
    if not out["queried"]:
        out["reason"] = out.get("reason") or f"no hooks/list reply (rc={rc}); stderr: {err_tail}"
        return out
    if out.get("reason"):                      # the reply itself was an error
        return out
    out["ours"] = [h for h in out["hooks"]
                   if isinstance(h.get("command"), str) and HOOK_MARKER_RE.search(h["command"])]
    out["our_trust_status"] = [h.get("trustStatus") for h in out["ours"]]
    out["our_keys"] = [h.get("key") for h in out["ours"]]
    out["our_additional_context_limit"] = [h.get("additionalContextLimit") for h in out["ours"]]
    return out


CODEX_HOOK_SCHEMA_DATE = "2026-09-14"   # the day hooks/list was executed against this parse


def codex_hooks_json_schema_parse(data: Any) -> Dict[str, Any]:
    """Parse a hooks.json document the way Codex LOADS it -- a shape, not a grep.

    The shape is the one the app-server's ``hooks/list`` answered for on
    CODEX_HOOK_SCHEMA_DATE (EXECUTED): ``hooks`` is an object; ``SessionStart`` is
    an array of GROUPS; a group is an object whose ``hooks`` is an array of hook
    objects; a command hook has ``type == "command"`` and a string ``command``.
    Anything else is a document Codex does NOT load as a hook, so our element
    inside it is NOT installed however clearly its basename reads.  The key
    Codex assigns is ``<hooks.json>:session_start:<group>:<hook>``.
    """
    out: Dict[str, Any] = {"loadable": False, "ours": [], "our_element_indices": [],
                           "our_positions": [], "reason": None}
    if not isinstance(data, dict):
        out["reason"] = "the document is not a JSON object"
        return out
    hooks = data.get("hooks")
    if hooks is None:
        out.update({"loadable": True, "reason": "no `hooks` key"})
        return out
    if not isinstance(hooks, dict):
        out["reason"] = "`hooks` is not an object (Codex loads no hook from this file)"
        return out
    groups = hooks.get("SessionStart")
    if groups is None:
        out.update({"loadable": True, "reason": "no SessionStart array"})
        return out
    if not isinstance(groups, list):
        out["reason"] = "`hooks.SessionStart` is not an array (Codex loads no SessionStart hook)"
        return out
    out["loadable"] = True
    for gi, g in enumerate(groups):
        if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
            continue
        for hi, h in enumerate(g["hooks"]):
            if not isinstance(h, dict):
                continue
            if h.get("type") != "command" or not isinstance(h.get("command"), str):
                continue
            if HOOK_MARKER_RE.search(h["command"]):
                out["ours"].append(h["command"])
                out["our_positions"].append([gi, hi])
                if gi not in out["our_element_indices"]:
                    out["our_element_indices"].append(gi)
    return out


def codex_trust_records(toml_txt: str) -> Dict[str, Optional[str]]:
    """The platform's PERSISTED trust records: ``[hooks.state."<key>"]`` tables and
    their ``trusted_hash`` -- written by Codex itself when the user chose
    'Trust' (Amendment 2, EXECUTED: additions only).  A text probe (tomllib is
    3.11+; status must not gain a floor)."""
    recs: Dict[str, Optional[str]] = {}
    cur: Optional[str] = None
    for ln in toml_txt.splitlines():
        m = re.match(r'^\s*\[hooks\.state\."?([^\]"]+)"?\]\s*$', ln)
        if m:
            cur = m.group(1)
            recs.setdefault(cur, None)
            continue
        if ln.strip().startswith("["):
            cur = None
            continue
        if cur is not None:
            m2 = re.match(r'^\s*trusted_hash\s*=\s*"([^"]*)"', ln)
            if m2:
                recs[cur] = m2.group(1)
    return recs


def probe_codex_hooks(paths: Sequence[str], cwd: str,
                      probe_platform: bool = False) -> Dict[str, Any]:
    """The resolver's view for Codex.

    TWO DERIVATIONS, NAMED, NEVER MIXED (R4 + CD4 + R5):
      * ``probe_platform=False`` (the DEFAULT, and the ONLY mode a plan or a dry
        run may use): ``installed`` is derived from hooks.json parsed under the
        hook SCHEMA Codex executed (never from the basename's presence in the
        bytes), and trust from the platform's OWN persisted trust record in
        config.toml ``[hooks.state."<key>"]`` for the key Codex assigns.  No
        vendor process is spawned, so nothing under $CODEX_HOME is written.
      * ``probe_platform=True`` (explicit consent: ``--probe-platform``): ask the
        app-server's ``hooks/list`` -- the live view, with ``trustStatus`` and
        ``currentHash``.  SIDE EFFECT, STATED: ``codex app-server`` writes its
        own state under $CODEX_HOME (sqlite / wal / tmp; 119 paths measured), so
        this is itself a WRITE under R5 and is never done inside a dry run.
    """
    hooks_json = _expand_platform_path(paths[0], cwd)
    config_toml = _expand_platform_path(paths[1], cwd) if len(paths) > 1 else None
    out: Dict[str, Any] = {"hooks_json": {"path": hooks_json, "exists": Path(hooks_json).exists()},
                           "config_toml": {"path": config_toml,
                                           "exists": bool(config_toml and Path(config_toml).exists())}}
    data, err = _read_json(hooks_json)
    if err and err != "absent":
        out["hooks_json"]["error"] = err
    schema = codex_hooks_json_schema_parse(data) if data is not None else \
        {"loadable": False, "ours": [], "our_element_indices": [], "our_positions": [],
         "reason": err or "absent"}
    out["hooks_json"]["schema"] = {"date_executed": CODEX_HOOK_SCHEMA_DATE, **schema}
    out["hooks_json"]["ours"] = schema["ours"]
    out["hooks_json"]["our_element_indices"] = schema["our_element_indices"]
    out["hooks_json"]["our_keys_by_schema"] = [f"{hooks_json}:session_start:{gi}:{hi}"
                                               for gi, hi in schema["our_positions"]]
    # config.toml: text probe (tomllib is 3.11+; status must not gain a floor)
    toml_txt = ""
    if config_toml and out["config_toml"]["exists"]:
        try:
            toml_txt = read_text_if_regular(config_toml, errors="replace")
        except OSError as e:
            out["config_toml"]["error"] = f"{type(e).__name__}: {e}"
    has_hooks_table = re.search(r"^\s*\[hooks(\.|\])", toml_txt, flags=re.M) is not None
    trust_records = codex_trust_records(toml_txt)
    trust_states = list(trust_records)
    # OURS is the key Codex assigns to THIS file's element -- never "any key that
    # mentions hooks.json".  A record for a hooks.json at ANOTHER absolute path
    # (a relocated CODEX_HOME, a project-level hook, another install) is
    # FOREIGN: it does not trust this file, and reporting it as ours made the
    # report row contradict the derivation row (EXECUTED, reflector iter 2, P6).
    _ours_keys = [f"{hooks_json}:session_start:{gi}:{hi}" for gi, hi in schema["our_positions"]]
    our_trust_keys = [k for k in trust_states if k in _ours_keys]
    stale_trust_keys = [k for k in trust_states
                        if ("hooks.json" in k or HOOK_BASENAME in k) and k not in _ours_keys]
    fallback = re.search(r"^\s*project_doc_fallback_filenames\s*=\s*(.+)$", toml_txt, flags=re.M)
    toml_mentions_hook = HOOK_BASENAME in toml_txt
    out["config_toml"].update({
        "hooks_table_present": has_hooks_table,
        "hook_defined_in_toml": has_hooks_table and toml_mentions_hook,
        "trust_state_keys": trust_states,
        "trust_records": trust_records,
        "our_trust_state_keys": our_trust_keys,
        "stale_trust_state_keys": stale_trust_keys,
        "stale_trust_state_note": ("a record for a hooks.json at ANOTHER absolute path: a relocated "
                                   "CODEX_HOME or another install; it does not trust this file"
                                   if stale_trust_keys else None),
        "trust_key_embeds_absolute_path": bool(our_trust_keys),
        "trust_key_note": ("the trust key embeds the ABSOLUTE PATH of hooks.json: relocating "
                           "CODEX_HOME silently re-untrusts the hook" if our_trust_keys else None),
        "project_doc_fallback_filenames": fallback.group(1).strip() if fallback else None,
        "confound_warning": (
            "project_doc_fallback_filenames is SET: this platform will load the named file "
            "as its instruction document, so ANY content-based canary run here is "
            "CONFOUNDED unless that file is proven not to contain the answer."
            if fallback else None),
    })
    # THE PLATFORM'S OWN VIEW -- only under explicit consent.
    if probe_platform:
        out["platform_view"] = codex_hooks_list(cwd)
        out["platform_view"]["side_effect"] = (
            "spawned `codex app-server` for the read-only hooks/list RPC; the vendor "
            "writes its own state under $CODEX_HOME (sqlite / wal / tmp) on any "
            "invocation -- a WRITE under R5, consented by --probe-platform")
    else:
        out["platform_view"] = {
            "queried": False, "hooks": [], "ours": [],
            "reason": ("not queried: spawning `codex app-server` writes the vendor's own "
                       "state under $CODEX_HOME; pass --probe-platform to consent")}
    pv = out["platform_view"]
    if pv.get("queried"):
        # THE LIVE VIEW ALONE decides when it was asked (RM06: never OR-ed with the file).
        installed = bool(pv.get("ours"))
        out["installed_derived_from"] = "app-server hooks/list (the platform's own resolver)"
        out["trust_status"] = (pv.get("our_trust_status") or [None])[0]
        out["trust_derived_from"] = "app-server hooks/list trustStatus (currentHash vs trusted_hash)"
    else:
        installed = bool(schema["ours"])
        out["installed_derived_from"] = (
            f"hooks.json parsed under the hook schema Codex executed on "
            f"{CODEX_HOOK_SCHEMA_DATE} (hooks/list agreed, by execution) -- not the "
            f"basename's presence in the bytes; the app-server was NOT spawned")
        keys = out["hooks_json"]["our_keys_by_schema"]
        if not installed:
            out["trust_status"] = None
        elif any(trust_records.get(k) for k in keys):
            out["trust_status"] = "trusted_by_record"
        else:
            out["trust_status"] = "untrusted"
        out["trust_derived_from"] = (
            "the platform's persisted trust record: config.toml [hooks.state.\"<key>\"] "
            "trusted_hash for the key Codex assigns (<hooks.json>:session_start:<group>:<hook>); "
            "a record present reads trusted_by_record (the hash formula is not re-derived "
            "here -- a definition edited AFTER trusting reads `modified` only under "
            "--probe-platform); no record reads untrusted")
    out["installed"] = installed
    out["ours"] = schema["ours"] or [h.get("command") for h in pv.get("ours") or []]
    return out


# --- the plan --------------------------------------------------------------------
def _identity_row(path: str) -> Dict[str, Any]:
    ident = path_identity(path)
    return {"path": path, "realpath": ident["resolved_path"],
            "st_dev": ident["dev"], "st_ino": ident["ino"], "st_nlink": ident["link_count"],
            "exists": ident["exists_without_following"], "file_type": ident["file_type"],
            "is_symlink": ident["is_symlink"], "refusal_code": refusal_code(ident)}


def install_target(platform_id: str, cwd: str, override: Optional[str] = None) -> str:
    if override:
        return str(Path(override).expanduser())
    if platform_id == "codex":
        return _expand_platform_path("{codex_home}/hooks.json", cwd)
    return _expand_platform_path("{claude_config}/settings.json", cwd)


def install_plan(platform: str, cwd: str, home: Optional[str] = None,
                 target: Optional[str] = None) -> Dict[str, Any]:
    """Build the install plan.  NOTHING IS WRITTEN HERE.  The plan lists every
    path a wet run would write, each with realpath + (st_dev, st_ino, st_nlink),
    the element, the contract, the gate verdict, and the unified diff."""
    pid = _PLATFORM_ALIAS.get(platform or "", None)
    if pid is None:
        return {"platform": platform, "blocked": True,
                "blockers": [f"unknown platform {platform!r}; choose one of "
                             f"{list(INSTALL_PLATFORMS)}"],
                "actions": [], "would_write": [], "generated_at": utc_now()}
    tgt = install_target(pid, cwd, target)
    cmd = hook_command(home)
    element = hook_element(pid, cmd)
    rp = receipt_path(home)
    store = preimage_store(home)
    blockers: List[str] = []
    plan: Dict[str, Any] = {
        "platform": pid, "mechanism": "hooks", "target": _identity_row(tgt),
        "hook_command": cmd, "element": element, "element_json_path": ELEMENT_JSON_PATH,
        "receipt_path": rp, "preimage_store": store, "generated_at": utc_now(),
        "consent": ("this plan writes ONE array element into a file the platform owns; "
                    "every path below is the complete list of what a wet run touches"),
    }
    ident = path_identity(tgt)
    plan["planned_identity"] = planned_identity(ident)
    if refusal_code(ident) is not None:
        plan["action"] = "refused"
        plan["refusal_code"] = refusal_code(ident)
        blockers.append(f"{tgt}: {refusal_code(ident)} — {refusal_reason(ident)}")
    elif not ident["exists_without_following"]:
        # CREATE.  The pre-image is "nothing"; the undo is delete_file (if unchanged)
        # or element-level (if the file has since grown).
        plan["action"] = "create_file"
        plan["preimage_sha256"] = None
        plan["preimage_len"] = 0
        plan["contract"] = {"indent": JSON_INDENT, "ensure_ascii": True, "trailing_newline": True}
        new_doc, created = merge_hook_element({}, element)
        post = serialize_under_contract(new_doc, plan["contract"])
        plan["created_keys"] = created
        plan["postimage_sha256_planned"] = hashlib.sha256(post).hexdigest()
        plan["postimage_len"] = len(post)
        plan["postimage_preview"] = post.decode("utf-8")
        plan["diff"] = diff_is_pure_insertion(b"", post)
        parent = str(Path(tgt).parent)
        plan["parent_dir"] = {"path": parent, "exists": os.path.isdir(parent)}
        if not os.path.isdir(parent):
            blockers.append(f"{parent}: the platform's home directory does not exist; "
                            f"this installer creates a FILE in a home the platform made, "
                            f"never the home itself (install the platform first)")
    else:
        try:
            pre = read_bytes_if_regular(tgt)
        except OSError as e:
            pre = b""
            blockers.append(f"{tgt}: unreadable: {type(e).__name__}: {e}")
        plan["preimage_sha256"] = hashlib.sha256(pre).hexdigest()
        plan["preimage_len"] = len(pre)
        gate = lossless_json_contract(pre)
        plan["gate"] = {k: v for k, v in gate.items() if k != "parsed"}
        if not gate["lossless"]:
            plan["action"] = "refused"
            plan["refusal_code"] = gate["code"]
            counts = gate.get("tolerant_counts")
            dark = ""
            if pid == "claude-code":
                if counts:
                    dark = (f" Claude Code is currently SKIPPING this file, including your "
                            f"{counts['permission_rules']} permission rule(s) and "
                            f"{counts['hooks']} hook(s) ({gate.get('tolerant_method')}).")
                elif gate["code"] == "NOT_JSON":
                    dark = (" Claude Code is currently SKIPPING this file and everything in "
                            "it; the rules and hooks could not even be counted.")
            plan["manual_paste"] = {
                "where": f"{tgt} -> {'.'.join(ELEMENT_JSON_PATH)} (append to the array)",
                "element": element}
            blockers.append(f"{tgt}: {gate['code']} — {gate.get('reason')}.{dark} "
                            f"NOTHING will be written; the element is printed for a "
                            f"manual paste.")
        else:
            doc = gate["parsed"]
            plan["contract"] = gate["contract"]
            groups = (doc.get("hooks") or {}).get("SessionStart") if isinstance(doc.get("hooks"), dict) else None
            # C3: "already installed" is about the CURRENT hook. An element
            # carrying only a LEGACY marker is ours, but it is superseded --
            # treating it as already-installed would leave a user on the
            # retired Node hook precisely when they asked to be repaired.
            present = find_our_elements(groups, HOOK_CURRENT_RE)
            # An element that also carries a hook that is NOT ours is never dropped:
            # dropping the element would delete the other hook (review 2, N4). Such an
            # element is left exactly as it is, and the plan says what to do by hand.
            def _mixed(g: Any) -> bool:
                return isinstance(g, dict) and any(
                    not (isinstance(h, dict) and isinstance(h.get("command"), str)
                         and HOOK_MARKER_RE.search(h["command"]))
                    for h in (g.get("hooks") or []))
            legacy_all = [i for i in find_our_elements(groups) if i not in present]
            legacy = [i for i in legacy_all if not _mixed(groups[i])]
            # 2026-10-26: an element running the CURRENT hook in an older SHAPE
            # (Codex's matcher "startup") used to read "already installed", so a
            # corrected shape could never reach an existing install. Drifted elements
            # are superseded through the same drop-and-append path as a legacy hook.
            # Drift is judged on the keys WE own (review F14): the matcher and our
            # hook entry's type and additionalContextLimit. NOT the command (review 2,
            # N9): a different brain home or interpreter is a different install, not a
            # new shape, and is never re-pointed silently. A key the user added by hand
            # (a timeout) is not drift either -- on Codex every supersede forces a re-trust.
            def _ours(g: Any) -> Any:
                if not isinstance(g, dict):
                    return g
                # only OUR entries are projected: a foreign hook sharing the element is
                # not part of our shape (recheck R3-3)
                hooks = [{k: h.get(k) for k in ("type", "additionalContextLimit") if k in h}
                         for h in (g.get("hooks") or [])
                         if isinstance(h, dict) and isinstance(h.get("command"), str)
                         and HOOK_MARKER_RE.search(h["command"])]
                return {"matcher": g.get("matcher"), "hooks": hooks}
            drifted_all = [i for i in present if _ours(groups[i]) != _ours(element)]
            mixed = [i for i in drifted_all + legacy_all if _mixed(groups[i])]
            drifted = [i for i in drifted_all if not _mixed(groups[i])]
            if mixed:
                plan["superseded_shape_refused"] = {
                    "indices": sorted(mixed),
                    "why": "our hook shares its element with a hook that is not ours; nothing was dropped",
                    "manual": "move the other hook into its own SessionStart element, then re-run install"}
            if drifted and not mixed:
                # Every element of ours is replaced by ONE fresh one (review 2, N18): a
                # current copy beside a drifted one would otherwise fire twice.
                current = [i for i in present if i not in drifted_all]
                plan["superseded_shape"] = {
                    "count": len(drifted),
                    "was": [groups[i] for i in drifted],
                    "now": element,
                    "why": "the hook element's shape changed in this release (e.g. Codex now "
                           "fires on resume, clear, compact and fork, not only on startup)",
                    "retrust": ("Codex pins trust to the hook's hash: open Codex, run /hooks, "
                                "and trust the Voidloom entry once, or it is skipped silently"
                                if pid == "codex" else None),
                }
                shape_drop = drifted + current
                present = []
            else:
                shape_drop = []
            retired = list(legacy)
            legacy = legacy + shape_drop
            plan["legacy_element_indices"] = legacy
            plan["legacy_elements"] = [groups[i] for i in legacy] if groups else []
            if retired:
                plan["superseded_legacy"] = {
                    "count": len(retired),
                    "commands": [c for i in retired for c in element_commands(groups[i])],
                    "why": ("a retired Voidloom hook is still wired here. It resolves a "
                            "layout this package no longer uses, prints 'brain not "
                            "reachable yet' over a reachable brain, and emits no "
                            "'=== VOIDLOOM HOOK FIRED' line -- which is why the "
                            "activation notice reported the hook inactive every session."),
                    "remedy": ("this call installs the current hook alongside it, which "
                               "restores the arrival line. To remove the retired one: "
                               "`voidloom hooks uninstall --yes` then "
                               "`voidloom hooks install --platform claude --yes`."),
                }
            if present:
                plan["action"] = "already_installed"
                plan["existing_element_indices"] = present
                plan["existing_elements"] = [groups[i] for i in present]
                plan["existing_equals_planned"] = any(groups[i] == element for i in present)
                plan["note"] = ("an element carrying our marker is already present; nothing "
                                "will be written. If no receipt names this path, that element "
                                "predates this installer (an ORPHAN): --status reports it and "
                                "uninstall removes it element-level.")
                plan["created_keys"] = []
            else:
                # C4 -- ACCEPTANCE IS STRUCTURAL, and C3's supersede is now
                # reachable because of it.
                #
                # This used to require BOTH the structural round-trip AND
                # `diff_is_pure_insertion`, a difflib opcode classification over
                # LINES. The text proxy vetoed configs the structural predicate
                # had already accepted: measured on `{"model": "opusplan"}` --
                # an ordinary file -- rc 3 with the blocker itself reading
                # `round_trip=True`. The class is any settings.json whose last
                # top-level key holds a SCALAR, because that line gains a comma
                # and new lines follow, which difflib calls a `replace`. It had
                # already accreted three special cases (comma-only, opened-empty
                # array, opened-empty object) and still mis-graded this one.
                #
                # The two concerns the text check served each have a better
                # instrument, and both already run:
                #   did we destroy the user's DATA?  -> the round-trip below
                #   did we reformat the user's BYTES? -> lossless_json_contract,
                #      which refuses LOSSY_SERIALIZATION before any of this
                # So the line diff stays as EVIDENCE in the plan and stops being
                # a veto.
                try:
                    new_doc, created = merge_hook_element(doc, element, legacy)
                except ValueError as e:
                    new_doc, created = None, []
                    blockers.append(f"{tgt}: {e}")
                    plan["refusal_code"] = plan.get("refusal_code") or "MERGE_REFUSED"
                if new_doc is not None:
                    try:
                        post = serialize_under_contract(new_doc, plan["contract"])
                    except ValueError as e:
                        post = None
                        blockers.append(f"{tgt}: the post-image cannot be serialized as "
                                        f"strict JSON ({e}); the file carries a value the "
                                        f"platform's own parser would reject.")
                        plan["refusal_code"] = "NOT_STRICT_JSON"
                if new_doc is not None and post is not None:
                    plan["action"] = "merge_element"
                    plan["created_keys"] = created
                    plan["postimage_sha256_planned"] = hashlib.sha256(post).hexdigest()
                    plan["postimage_len"] = len(post)
                    # THE INVARIANT: strip our element from the post-image and you
                    # get the pre-image back, deep-equal -- MINUS exactly the
                    # elements of ours we declared we were superseding. Without
                    # that second clause the check would refuse its own correct
                    # supersede, which is how it read before C4.
                    expect_pre = doc
                    if legacy:
                        expect_pre = json.loads(json.dumps(doc))
                        _ss = (expect_pre.get("hooks") or {}).get("SessionStart")
                        if isinstance(_ss, list):
                            expect_pre["hooks"]["SessionStart"] = [
                                g for i, g in enumerate(_ss) if i not in set(legacy)]
                    back, info = remove_hook_element(json.loads(post.decode("utf-8")),
                                                     element, created)
                    plan["round_trip_deep_equal"] = (back == expect_pre)
                    plan["removed_our_elements"] = len(legacy)
                    # EVIDENCE, NOT A VETO.
                    plan["diff"] = diff_is_pure_insertion(pre, post)
                    if not plan["round_trip_deep_equal"]:
                        blockers.append(
                            f"{tgt}: the planned post-image does not round-trip to the "
                            f"pre-image: stripping our element does not reproduce the file "
                            f"as it stands (line-diff evidence: "
                            f"{plan['diff']['violations'][:2]})")
                        plan["refusal_code"] = "POST_IMAGE_NOT_ROUND_TRIP"
    # WHAT A WET RUN WOULD WRITE — every path, with identity.  Enumerable BEFORE.
    would: List[Dict[str, Any]] = []
    if plan.get("action") in ("create_file", "merge_element"):
        would.append({**_identity_row(tgt), "kind": plan["action"],
                      "bytes": plan.get("postimage_len")})
        if plan.get("action") == "merge_element":
            pre_name = str(Path(store) / f"{plan['preimage_sha256']}.bytes")
            would.append({**_identity_row(pre_name), "kind": "write_preimage_copy",
                          "bytes": plan.get("preimage_len")})
            would.append({**_identity_row(store), "kind": "mkdir_if_absent"})
        would.append({**_identity_row(rp), "kind": "receipt_append"})
    # C4/M4 -- NOT_WRITABLE IS A PLAN-TIME FACT, not a write-time surprise.
    # `would_write` promises every path a wet run will touch; promising a write
    # to a path we cannot write is the planner promising what the applier cannot
    # keep. Measured before this: a read-only settings.json in a read-only dir
    # produced an untyped OSError at write time, after the plan had said it
    # would write. os.access is advisory (it answers for the real uid and says
    # nothing about a full disk or a mandatory lock), so this is a CHEAP
    # PRE-CHECK that turns the common case into a typed refusal -- the ENOSPC
    # rollback arm above is what covers the rest.
    if not blockers and plan.get("action") in ("create_file", "merge_element"):
        _probe = tgt if os.path.exists(tgt) else os.path.dirname(tgt) or "."
        _need = os.W_OK if os.path.exists(tgt) else (os.W_OK | os.X_OK)
        _parent = os.path.dirname(tgt) or "."
        _unwritable = None
        if os.path.exists(_probe) and not os.access(_probe, _need):
            _unwritable = _probe
        elif os.path.exists(tgt) and os.path.isdir(_parent) and not os.access(_parent, os.W_OK | os.X_OK):
            # a writable file inside a directory we cannot write is still
            # unwritable to an atomic replace
            _unwritable = _parent
        if _unwritable is not None:
            blockers.append(
                f"{tgt}: NOT_WRITABLE — {_unwritable} is not writable by this user. "
                f"NOTHING will be written; the element is printed for a manual paste.")
            plan["refusal_code"] = "NOT_WRITABLE"
            plan["action"] = "refused"
            would = []

    plan["would_write"] = would
    plan["blockers"] = blockers
    plan["blocked"] = bool(blockers)
    # C4/M2 -- A REFUSAL A CALLER CAN ACT ON.
    #
    # Only the LOSSY_SERIALIZATION / NOT_JSON branch set these, so the most
    # common refusal -- the acceptance one -- gave the user a paragraph and
    # nothing to do with it. Measured at the parent: refusal_code None,
    # manual_paste ABSENT. Both are now set for EVERY blocking refusal, here,
    # once, where the plan is closed out -- rather than at each branch, which is
    # how one branch came to have them and the others did not.
    if plan["blocked"]:
        plan.setdefault("refusal_code", "INSTALL_REFUSED")
        plan.setdefault("manual_paste", {
            "where": f"{tgt} -> {'.'.join(ELEMENT_JSON_PATH)} (append to the array)",
            "element": element})
    if pid == "codex":
        plan["after_install"] = APPROVAL_NOTE_CODEX
        plan["residue"] = [r for r in IRREVERSIBLE_RESIDUE if r["id"] == "codex_trust_hash"]
    else:
        plan["after_install"] = APPROVAL_NOTE_CLAUDE
    return plan


APPROVAL_NOTE_CODEX = (
    "Codex pins trust to the hook definition's hash. Until you run `codex` and trust the "
    "Voidloom entry on the 'Hooks need review' screen (or via /hooks), the hook is SKIPPED "
    "SILENTLY under `codex exec`. `voidloom doctor` reads the platform's own hooks/list and "
    "reports INSTALLED_NEVER_FIRED until a session fires it. The trust key Codex writes "
    "embeds the ABSOLUTE PATH of hooks.json: relocating CODEX_HOME re-untrusts it.")
APPROVAL_NOTE_CLAUDE = (
    "Claude Code has no trust gate: this element IS the consent. The next NEW session runs "
    "the hook; `voidloom doctor` joins its heartbeat against your transcripts.")


def _receipt_load(rp: str) -> Dict[str, Any]:
    if not os.path.lexists(rp):
        return {"schema_version": RECEIPT_SCHEMA_VERSION, "touched": []}
    # IDENTITY BEFORE CONTENT (the class the install battery's S5' found): a
    # receipt path that is a link, a multiply-named object or a non-regular
    # file is refused BY NAME here, before any byte is parsed — otherwise a
    # user's file at that name is reported as "not JSON" instead of as the
    # object it is, and the reason names the wrong thing.
    _id = path_identity(rp)
    if refusal_code(_id) is not None:
        raise PathIsNotWhatWeNamed(f"receipt {rp}: {refusal_reason(_id)}")
    d = json.loads(read_text_if_regular(rp))
    if not isinstance(d, dict) or not isinstance(d.get("touched"), list):
        raise ValueError("receipt is not an object with a `touched` array")
    return d


def _receipt_write(rp: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["schema_version"] = max(int(doc.get("schema_version") or 1), RECEIPT_SCHEMA_VERSION)
    ident = path_identity(rp)
    expect = planned_identity(ident) if ident["exists_without_following"] else {"exists": False}
    sha = _sha256_file(rp) if ident["exists_without_following"] else None
    return guarded_write_text(rp, json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                              expect=expect, expect_sha256=sha)


UNDONE_RESULTS = ("RESTORED", "ELEMENT_REMOVED", "DELETED", "MARKER_REMOVED",
                  "ALREADY_ABSENT")


def receipt_mark_undone(rp: str, applied: Dict[str, Any]) -> Dict[str, Any]:
    """Move every entry whose undo LANDED from ``touched`` to ``undone`` (with the
    result and the time).  An entry whose undo was refused, skipped or errored
    STAYS in ``touched``, so the next run sees it again.  Never raises."""
    out: Dict[str, Any] = {"receipt_path": rp, "moved": [], "kept": []}
    try:
        if not Path(rp).exists():
            out["reason"] = "no receipt"
            return out
        doc = _receipt_load(rp)
    except (OSError, ValueError) as e:
        out["reason"] = f"receipt unreadable: {type(e).__name__}: {e}"
        return out
    landed = {os.path.abspath(str(d.get("path"))): d for d in (applied.get("applied") or [])
              if d.get("result") in UNDONE_RESULTS}
    keep: List[Dict[str, Any]] = []
    undone: List[Dict[str, Any]] = list(doc.get("undone") or [])
    for t in doc.get("touched") or []:
        ap = os.path.abspath(str(t.get("path")))
        if ap in landed:
            undone.append({**t, "undone_at": utc_now(), "undo_result": landed[ap]["result"]})
            out["moved"].append(ap)
        else:
            keep.append(t)
            out["kept"].append(ap)
    doc["touched"] = keep
    doc["undone"] = undone
    try:
        w = _receipt_write(rp, doc)
        out["written"] = True
        out["sha256_after"] = w.get("sha256_after")
    except (OSError, PathIsNotWhatWeNamed) as e:
        out["written"] = False
        out["reason"] = f"{type(e).__name__}: {e}"
    return out


def install_execute(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the plan through the SAME writer uninstall uses.  Ordering:
    pre-image copy -> target write (gate, no O_TRUNC before the gate) -> read
    back through the independent path and json.loads -> (restore immediately on
    failure) -> receipt.  A crash between the target write and the receipt
    leaves an UNRECEIPTED element, which --status reports as an orphan and
    uninstall removes element-level -- never a silent residue."""
    res: Dict[str, Any] = {"platform": plan.get("platform"), "applied": [],
                           "result": None, "receipt_path": plan.get("receipt_path")}
    if plan.get("blocked"):
        res["result"] = "REFUSED"
        res["reason"] = plan.get("blockers")
        return res
    action = plan.get("action")
    tgt = plan["target"]["path"]
    if action == "already_installed":
        res["result"] = "ALREADY_INSTALLED"
        res["reason"] = plan.get("note")
        return res
    if action not in ("create_file", "merge_element"):
        res["result"] = "REFUSED"
        res["reason"] = f"unknown action {action!r}"
        return res
    contract = plan["contract"]
    element = plan["element"]
    # 1. the pre-image copy (ours; a path the plan saw as absent -> O_EXCL)
    pre_copy: Optional[str] = None
    pre_bytes = b""
    if action == "merge_element":
        # IDENTITY BEFORE CONTENT: the object at the name is checked against
        # the plan BEFORE its bytes are re-read, so a directory / FIFO / link
        # swapped in since the plan is a REFUSAL that names the object, not an
        # ERROR from a read that could not proceed.
        _now = path_identity(tgt)
        _rc = refusal_code(_now)
        _drift = identity_matches(plan["planned_identity"], _now)
        if _rc is not None or (_drift["checked"] and _drift["same_object"] is False):
            res["result"] = "REFUSED"
            res["refused_by"] = "pre_write_identity_check"
            res["refusal_code"] = _rc or "PLAN_IDENTITY_DRIFT"
            res["reason"] = (refusal_reason(_now) if _rc else f"{tgt}: {_drift['reason']}")
            return res
        try:
            pre_bytes = read_bytes_if_regular(tgt)
        except OSError as e:
            res["result"] = "REFUSED"
            res["refusal_code"] = "NOT_A_REGULAR_FILE"
            res["reason"] = f"pre-image re-read refused: {type(e).__name__}: {e}"
            return res
        if hashlib.sha256(pre_bytes).hexdigest() != plan["preimage_sha256"]:
            res["result"] = "REFUSED"
            res["reason"] = ("the file changed between plan and execute (content drift); "
                             "re-run the plan")
            return res
        store = plan["preimage_store"]
        try:
            guarded_mkdir_chain(store)        # the class of P2: every directory we create, gated
        except PathIsNotWhatWeNamed as e:
            res["result"] = "REFUSED"
            res["refused_by"] = getattr(e, "refused_by", None)
            res["reason"] = f"pre-image store mkdir: {e}"
            return res
        except OSError as e:
            res["result"] = "ERROR"
            res["reason"] = f"pre-image store mkdir: {type(e).__name__}: {e}"
            return res
        pre_copy = str(Path(store) / f"{plan['preimage_sha256']}.bytes")
        if Path(pre_copy).exists() and _sha256_file(pre_copy) == plan["preimage_sha256"]:
            res["applied"].append({"path": pre_copy, "kind": "preimage_copy",
                                   "result": "ALREADY_PRESENT_SAME_BYTES"})
        else:
            try:
                w = guarded_write_bytes(pre_copy, pre_bytes,
                                        expect={"exists": False} if not Path(pre_copy).exists()
                                        else planned_identity(path_identity(pre_copy)),
                                        expect_sha256=(None if not Path(pre_copy).exists()
                                                       else _sha256_file(pre_copy)))
            except (PathIsNotWhatWeNamed, OSError) as e:
                res["result"] = "REFUSED" if isinstance(e, PathIsNotWhatWeNamed) else "ERROR"
                res["reason"] = f"pre-image copy: {e}"
                return res
            if w.get("sha256_after") != plan["preimage_sha256"] or not w.get("same_object"):
                res["result"] = "ERROR"
                res["reason"] = "the pre-image copy did not read back as the pre-image"
                return res
            res["applied"].append({"path": pre_copy, "kind": "preimage_copy",
                                   "result": "WRITTEN", "sha256": w["sha256_after"]})
        doc = json.loads(pre_bytes.decode("utf-8"))
        # C4: the SAME drop the plan graded. If execute appended while the plan
        # had approved a supersede, the post-image would not be the one whose
        # round-trip was checked -- an applier keeping a different promise from
        # the one the planner made is this class by definition.
        new_doc, created = merge_hook_element(doc, element,
                                              plan.get("legacy_element_indices") or [])
    else:
        new_doc, created = merge_hook_element({}, element)
    post = serialize_under_contract(new_doc, contract)
    # 2. the target write, through THE writer
    try:
        w = guarded_write_bytes(tgt, post, expect=plan["planned_identity"],
                                expect_sha256=(plan["preimage_sha256"]
                                               if action == "merge_element" else None))
    except PathIsNotWhatWeNamed as e:
        res["result"] = "REFUSED"
        res["reason"] = str(e)
        res["refused_by"] = getattr(e, "refused_by", None)
        return res
    except OSError as e:
        # C4 -- THE MISSING ROLLBACK ARM.
        #
        # This returned ERROR and left the file wherever the writer got to. Its
        # own sibling twelve lines below (the read-back-failure path) restores
        # the pre-image in the same run. And `guarded_write_bytes` does
        # `os.ftruncate(fd, 0)` and THEN `os.write(fd, data)` -- deliberately not
        # O_TRUNC, so the identity gate can speak before anything is emptied --
        # which means an ENOSPC BETWEEN those two calls leaves the user's
        # settings.json at 0 BYTES with their foreign hooks gone. Reproduced by
        # injecting ENOSPC at exactly that point: 84 bytes -> 0, restored: false.
        res["result"] = "ERROR"
        res["reason"] = f"{type(e).__name__}: {e}"
        if action == "merge_element":
            try:
                rw = guarded_write_bytes(tgt, pre_bytes, expect=None, expect_sha256=None)
                res["rollback"] = {
                    "sha256_after": rw.get("sha256_after"),
                    "restored_preimage": rw.get("sha256_after") == plan["preimage_sha256"]}
                if res["rollback"]["restored_preimage"]:
                    res["reason"] += " — the pre-image was restored in the same run"
            except (PathIsNotWhatWeNamed, OSError) as e2:
                # SAY SO. A failed rollback is the one state a caller must act on
                # by hand, and the pre-image copy is already on disk for them.
                res["rollback"] = {"error": f"{type(e2).__name__}: {e2}",
                                   "preimage_copy": pre_copy}
                res["reason"] += (f" — AND THE ROLLBACK FAILED: your file is not intact. "
                                  f"Restore it from {pre_copy}")
        else:
            # A FAILED CREATE STILL LEAVES RESIDUE: the writer opens O_CREAT, so
            # the file exists at 0 bytes even though the write never landed.
            # There is no pre-image to restore -- the correct undo is to remove
            # what we made, and to leave anything we did not make alone.
            try:
                if os.path.exists(tgt) and os.path.getsize(tgt) == 0:
                    d_ = guarded_unlink(tgt, expect=planned_identity(path_identity(tgt)),
                                        expect_sha256=None)
                    res["rollback"] = {"unlinked_empty_file_we_created": d_.get("gone_after")}
            except (PathIsNotWhatWeNamed, OSError) as e2:
                res["rollback"] = {"error": f"{type(e2).__name__}: {e2}"}
        return res
    row = {"path": tgt, "kind": action, "write": {k: w.get(k) for k in
           ("sha256_after", "same_object", "verify_path", "read_back_error",
            "bytes_written", "link_count_at_write")}}
    # 3. READ BACK through the independent path; it must json.loads or the
    #    pre-image goes back IMMEDIATELY, same run, before any session can open.
    ok = (w.get("same_object") and w.get("read_back_error") is None
          and w.get("sha256_after") == hashlib.sha256(post).hexdigest())
    parsed_ok = False
    if ok:
        try:
            vb = read_bytes_if_regular(w["verify_path"])
            json.loads(vb.decode("utf-8"))
            parsed_ok = (vb == post)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            row["read_back_parse_error"] = f"{type(e).__name__}: {e}"
    if not (ok and parsed_ok):
        row["result"] = "ROLLED_BACK"
        # D05-C1: `expect_sha256` must NEVER be pinned to `post` on this arm. `post` is
        # the bytes this run has just FAILED to write; the name holds the TRUNCATED
        # post-image, so the pin fires `plan_content_drift` and the restore REFUSES
        # ITSELF -- leaving the user's settings.json unparseable. The pin is still
        # worth having: it just has to name what is ACTUALLY AT THE NAME NOW, which is
        # what proves nothing moved between the read-back and the restore.
        try:
            _at_name_now = _sha256_file(tgt)
        except OSError:
            _at_name_now = None
        try:
            if action == "merge_element":
                rw = guarded_write_bytes(tgt, pre_bytes, expect=plan["planned_identity"],
                                         expect_sha256=_at_name_now)
                row["rollback"] = {"sha256_after": rw.get("sha256_after"),
                                   "restored_preimage": rw.get("sha256_after") == plan["preimage_sha256"]}
            else:
                d_ = guarded_unlink(tgt, expect=planned_identity(path_identity(tgt)),
                                    expect_sha256=_at_name_now)
                row["rollback"] = {"unlinked": d_.get("gone_after")}
        except (PathIsNotWhatWeNamed, OSError) as e:
            row["rollback"] = {"error": str(e)}
        res["applied"].append(row)
        res["result"] = "ROLLED_BACK"
        # D05-C2 (LIES): this sentence was appended UNCONDITIONALLY, so the envelope
        # asserted "the pre-image was restored" while carrying the rollback's own
        # failure in the same object. The verdict is now DERIVED from what the
        # rollback observed, and the failure names the copy the operator can restore
        # from -- parity with the `except OSError` arm above, which already said so.
        _rb = row.get("rollback") or {}
        _restored = bool(_rb.get("restored_preimage")) or bool(_rb.get("unlinked"))
        res["reason"] = ("the post-image did not read back as valid JSON identical to the "
                         "planned bytes; "
                         + ("the pre-image was restored in the same run" if _restored else
                            f"AND THE ROLLBACK FAILED: your file is not intact. "
                            f"Restore it from {pre_copy}"))
        return res
    row["result"] = "WRITTEN"
    row["postimage_sha256"] = w["sha256_after"]
    # [D04-L1]: `postimage_sha256_planned` was computed in the plan and never
    # compared to the bytes actually written. A planned hash nobody checks is a
    # number that looks like evidence -- the exact shape this cycle keeps
    # deleting. The read-back above already proves the file parses and equals
    # `post`; this proves it equals what the PLAN promised, which is the
    # separate claim the operator approved.
    _planned = plan.get("postimage_sha256_planned")
    if _planned:
        row["matches_planned_postimage"] = (w.get("sha256_after") == _planned)
        if not row["matches_planned_postimage"]:
            row["planned_postimage_sha256"] = _planned
            res["reason"] = (
                "the write succeeded and read back as valid JSON, but its bytes are NOT "
                "the bytes this plan promised. Nothing is rolled back -- the file on disk "
                "is self-consistent -- but the plan you approved and the file you have "
                "are different objects; re-run `hooks status` before trusting either.")
    res["applied"].append(row)
    # 4. the receipt — LAST, so an unreceipted element is the failure shape, never
    #    a receipt for a write that did not happen.
    entry = {"platform": plan["platform"], "mechanism": "hooks",
             "installed_at": utc_now(), "path": tgt,
             "action": ("restore_preimage" if action == "merge_element" else "delete_file"),
             "install_action": action,
             "preimage_path": pre_copy, "preimage_sha256": plan.get("preimage_sha256"),
             "postimage_sha256": w["sha256_after"], "element": element,
             "element_json_path": ELEMENT_JSON_PATH, "created_keys": created,
             "contract": contract, "planned_identity_at_install": plan["planned_identity"],
             "identity_after_write": {"dev": w.get("written_dev_ino", [None, None])[0],
                                      "ino": w.get("written_dev_ino", [None, None])[1]}}
    try:
        rdoc = _receipt_load(plan["receipt_path"])
        rdoc["touched"] = [t for t in rdoc["touched"] if t.get("path") != tgt]
        rdoc["touched"].append(entry)
        rw = _receipt_write(plan["receipt_path"], rdoc)
        res["applied"].append({"path": plan["receipt_path"], "kind": "receipt_append",
                               "result": "WRITTEN", "sha256": rw.get("sha256_after")})
    except (OSError, ValueError, PathIsNotWhatWeNamed) as e:
        res["applied"].append({"path": plan["receipt_path"], "kind": "receipt_append",
                               "result": "ERROR", "reason": f"{type(e).__name__}: {e}"})
        res["result"] = "INSTALLED_UNRECEIPTED"
        res["reason"] = ("the element was written but the receipt could not be; --status "
                         "will report the element as UNRECEIPTED and uninstall removes it "
                         "element-level")
        return res
    res["result"] = "INSTALLED"
    res["postimage_sha256"] = w["sha256_after"]
    res["receipt_entry"] = entry
    return res


def render_install(plan: Dict[str, Any], wet: bool,
                   applied: Optional[Dict[str, Any]] = None) -> str:
    L: List[str] = []
    L.append("Voidloom hooks install --platform " + str(plan.get("platform")) + "  "
             + ("[EXECUTING]" if wet else "[DRY RUN — nothing will be written]"))
    L.append(f"  hook command : {plan.get('hook_command')}")
    t = plan.get("target") or {}
    L.append(f"  target       : {t.get('path')}")
    L.append(f"      realpath : {t.get('realpath')}   identity=(dev {t.get('st_dev')}, "
             f"ino {t.get('st_ino')}, nlink {t.get('st_nlink')})  exists={t.get('exists')}  "
             f"type={t.get('file_type')}")
    L.append(f"  action       : {plan.get('action')}"
             + (f"   ({plan.get('refusal_code')})" if plan.get("refusal_code") else ""))
    if plan.get("contract"):
        L.append(f"  contract     : {plan['contract']}   (proven to reproduce the pre-image "
                 f"byte-for-byte)" if plan.get("action") != "create_file" else
                 f"  contract     : {plan['contract']}")
    L.append("  element      : " + json.dumps(plan.get("element"), ensure_ascii=False))
    if plan.get("superseded_shape"):
        _s = plan["superseded_shape"]
        L.append(f"  SUPERSEDES   : {_s['count']} element(s) of ours in an older shape -- {_s['why']}")
        if _s.get("retrust"):
            L.append(f"  RE-TRUST     : {_s['retrust']}")
    if plan.get("superseded_shape_refused"):
        _r = plan["superseded_shape_refused"]
        L.append(f"  NOT REPLACED : {_r['why']} -- {_r['manual']}")
    if plan.get("would_write"):
        L.append("")
        L.append("  PATHS A WET RUN WOULD WRITE (listed BEFORE anything is written):")
        for w in plan["would_write"]:
            L.append(f"    {w['kind']:<22} {w['path']}")
            L.append(f"        realpath={w['realpath']}  (dev {w['st_dev']}, ino {w['st_ino']}, "
                     f"nlink {w['st_nlink']})  exists={w['exists']}")
    d = plan.get("diff") or {}
    if d.get("unified_diff"):
        L.append("")
        L.append(f"  DIFF (pure insertion: {d.get('pure')}; inserted lines "
                 f"{d.get('inserted_lines')}, comma-only changes {d.get('comma_only_changes')}):")
        for ln in d["unified_diff"][:60]:
            L.append("    " + ln)
    if plan.get("manual_paste"):
        L.append("")
        L.append("  MANUAL PASTE (the installer refused to write; add this by hand):")
        L.append(f"    where : {plan['manual_paste']['where']}")
        L.append("    " + json.dumps(plan["manual_paste"]["element"], ensure_ascii=False))
    L.append("")
    if plan.get("blocked"):
        L.append("  RESULT: REFUSED")
        for b in plan.get("blockers") or []:
            L.append(f"    - {b}")
    elif plan.get("action") == "already_installed":
        L.append("  RESULT: ALREADY INSTALLED — " + str(plan.get("note")))
    elif not wet:
        L.append("  RESULT: DRY RUN complete. Re-run with --yes to apply.")
    if applied:
        L.append(f"  APPLIED: {applied.get('result')}"
                 + (f" — {applied.get('reason')}" if applied.get("reason") else ""))
        for a in applied.get("applied") or []:
            L.append(f"    {a.get('kind'):<16} {a.get('result'):<26} {a.get('path')}")
    # CRUCIBLE-3 D05-M4: these two blocks were appended UNCONDITIONALLY, so a
    # REFUSED install printed, two lines under "RESULT: REFUSED":
    #     "AFTER INSTALL: ... this element IS the consent. The next NEW session
    #      runs the hook"
    # and a refused Codex install printed "⚠ RESIDUE this install creates and
    # uninstall CANNOT undo: Codex hook trust-hash residue ..." for residue it did
    # not create. Both describe a world the run did not bring about.
    #
    # They are conditional on the run having actually WRITTEN something, or on a
    # dry run that would (where they are a forecast and say so). A refusal gets
    # neither: nothing was installed, so there is no "after install", and nothing
    # was created, so there is no residue.
    _refused = bool(plan.get("blocked"))
    _applied_result = (applied or {}).get("result")
    _wrote = bool(applied) and _applied_result not in ("REFUSED", "ERROR", None)
    if not _refused and (_wrote or not wet):
        L.append("")
        _tense = "AFTER INSTALL" if _wrote else "AFTER A WET RUN WOULD INSTALL"
        L.append(f"  {_tense}: " + str(plan.get("after_install")))
        for r in plan.get("residue") or []:
            _verb = ("creates" if _wrote else "WOULD create")
            L.append(f"  ⚠ RESIDUE this install {_verb} and uninstall CANNOT undo: "
                     f"{r['what']} — {r['where']}")
    elif _refused:
        L.append("")
        L.append("  Nothing was installed and no residue was created — the lines that "
                 "describe an installed state are omitted on a refusal.")
    return "\n".join(L)


PROBES["codex_hooks"] = probe_codex_hooks


# ===========================================================================
# Rendering
# ===========================================================================
def render_status(st: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append(f"Voidloom hooks --status   (resolver's view, {st['generated_at']})")
    L.append(f"  cwd:    {st['cwd']}")
    # THREE STATES, THREE SENTENCES.  Absent, unreadable and empty used to
    # render identically, and the unreadable one carried a percentage.
    if not st["ledger_present"]:
        _lnote = "   [ABSENT — no canary has ever written proof]"
    elif not st.get("ledger_readable", True):
        _lnote = (f"   [UNREADABLE — {st.get('ledger_fault')}. The proof channel "
                  f"cannot be measured; this is NOT the same as measuring zero]")
    else:
        _lnote = ""
    L.append(f"  ledger: {st['ledger']}" + _lnote)
    L.append(f"  matrix surveyed: {st['matrix_survey_date']} "
             f"(a capability table is a claim about someone else's software on one day)")
    L.append("")
    for r in st["platforms"]:
        L.append(f"  {r['state']:<20} {r['name']}  [{r['id']}]")
        L.append(f"      mechanism : {r['mechanism']}")
        L.append(f"      semantics : {r['semantics']}")
        res = r["resolved"]
        if "sources" in res:
            for s in res["sources"]:
                mark = "EXISTS " if s.get("exists") else "absent "
                extra = ""
                if s.get("session_start_commands"):
                    extra = "  -> " + " ; ".join(s["session_start_commands"])
                L.append(f"      {mark}    {s['path']}{extra}")
        elif "chain" in res:
            for c in res["chain"]:
                mark = "EXISTS " if c["exists"] else "absent "
                L.append(f"      {mark}    {c['path']} ({c['bytes']} bytes)")
            w = res.get("resolved_winner")
            L.append(f"      resolved winner: {w['path'] if w else 'NONE'}"
                     f"   carries our marker: {res.get('winner_carries_our_marker')}")
        elif "hooks_json" in res:
            hj, ct, pv = res["hooks_json"], res.get("config_toml") or {}, res.get("platform_view") or {}
            L.append(f"      {'EXISTS ' if hj.get('exists') else 'absent '}    {hj['path']}"
                     + (f"  -> ours at indices {hj.get('our_element_indices')}"
                        if hj.get("our_element_indices") else ""))
            L.append(f"      {'EXISTS ' if ct.get('exists') else 'absent '}    {ct.get('path')}"
                     + (f"  [hooks] table present" if ct.get("hooks_table_present") else ""))
            L.append(f"      platform view (app-server hooks/list): "
                     + (f"{len(pv.get('hooks') or [])} hook(s) loaded; ours={len(pv.get('ours') or [])}"
                        f" trust={pv.get('our_trust_status')}" if pv.get("queried")
                        else f"NOT QUERIED — {pv.get('reason')}"))
            L.append(f"      installed derived from: {res.get('installed_derived_from')}")
            L.append(f"      trust: {res.get('trust_status')}  (derived from: {res.get('trust_derived_from')})")
            if ct.get("our_trust_state_keys"):
                L.append(f"      trust keys (embed the ABSOLUTE path): {ct['our_trust_state_keys']}")
            if ct.get("confound_warning"):
                L.append(f"      ⚠ CONFOUND: {ct['confound_warning']}")
        elif "path" in res:
            mark = "EXISTS " if res.get("exists") else "absent "
            L.append(f"      {mark}    {res['path']}")
            if res.get("trust_state_keys"):
                L.append(f"      trust states recorded: {res['trust_state_keys']}")
            if res.get("confound_warning"):
                L.append(f"      ⚠ CONFOUND: {res['confound_warning']}")
        v = r["verification"]
        vf = r["survey_freshness"]
        vstate = v["state"] if vf["state"] != "EXPIRED" else f"{v['state']}-LAPSED"
        vage = ("" if vf["state"] != "EXPIRED"
                else f"   [SURVEY LAPSED {vf['age_days']}d > TTL {TTL_DAYS}d — a "
                     f"claim about someone else's software, not a measurement]")
        L.append(f"      surveyed  : {vstate} ({v['date']}) — {v['method']}{vage}")
        fr = r.get("fired") or {}
        if fr.get("state") not in (None, "NOT_APPLICABLE"):
            w = fr.get("window") or {}
            wtxt = (f" ({w.get('fired')} of {w.get('sessions')} in the last-{w.get('declared')} window)"
                    if w.get("sessions") is not None else "")
            L.append(f"      fired     : {fr.get('state')}{wtxt}   [sensor: heartbeat join — doctor's]")
        pf = r["proof"]
        for key, label in (("last_present", "G0 present"),
                           ("last_proven", "G1 proven"),
                           ("last_obeyed", "G2 obeyed"),
                           ("last_uncontradicted", "G3 unctrd")):
            e = pf.get(key)
            if e is None:
                continue
            # N2 (Crucible D01-F2): this printed the DENOMINATOR alone. PROVEN and
            # OBEYED are awarded at k >= 1 -- the canary's own comment says "one
            # green canary is a pulse, not a heartbeat" and then awards on one --
            # so `n=10` beside a verdict earned by a single hit reads as 10 of 10
            # to every human who has ever read a fraction. C6 made the ledger
            # PERSIST `hits`; this is the half that shows it. `k/n` or nothing:
            # a row written before C6 has no `hits`, and `k=?` says so rather
            # than inventing one.
            _k = e.get("hits")
            _n = e.get("observations")
            _kn = (f"{_k}/{_n}" if _k is not None else f"k=? of {_n}")
            val = (f"{e['observed_at']} ({e['age_days']}d old, {_kn})"
                   if e["state"] == "FRESH" else e["state"])
            L.append(f"      {label:<11}: {val}   [TTL {e['ttl_days']}d]   [sensor: canary ledger]")
        for n in r["notes"]:
            L.append(f"      note      : {n}")
        L.append("")
    u = st.get("unreceipted") or {}
    L.append(f"  UNRECEIPTED (ours, or hand edits, that NO receipt names): "
             f"{u.get('orphan_count', 0)}   receipt: {u.get('receipt_path')}"
             + ("" if u.get("receipt_present") else "   [ABSENT]"))
    for o in u.get("orphans") or []:
        L.append(f"    * {o['platform']:<12} {o['path']}  — {o['kind']}"
                 + (f"  lines {o['mention_lines']}" if o.get("mention_lines") else "")
                 + (f"  backups {o['backup_siblings']}" if o.get("backup_siblings") else ""))
        if o.get("note"):
            L.append(f"        {o['note']}")
    L.append("")
    c = st["coverage"]
    if c.get("coverage_state") == "UNKNOWN":
        # NOT A COUNT.  The two numbers an operator would read side by side are
        # both UNKNOWN here, and printing "0 of 10" would be the placeholder.
        L.append("  COVERAGE: UNKNOWN — the proof ledger could not be read, so "
                 "neither numerator nor denominator can be derived.")
        L.append(f"  coverage %: REFUSED — {c['refusal_reason']}")
        return "\n".join(L)
    L.append(f"  COVERAGE: {c['proven_fresh_count']} of {c['platform_count']} "
             f"platforms hold FRESH proof; {c['installed_count']} installed; "
             f"{c['expired_count']} expired.")
    if c["coverage_percent"] == "REFUSED":
        L.append(f"  coverage %: REFUSED — {c['refusal_reason']}")
    else:
        L.append(f"  coverage %: {c['coverage_percent']}%  "
                 f"(no expired rows in the denominator)")
    return "\n".join(L)


def render_uninstall(plan: Dict[str, Any], wet: bool,
                     applied: Optional[Dict[str, Any]] = None) -> str:
    L: List[str] = []
    L.append("Voidloom hooks --uninstall  "
             + ("[EXECUTING]" if wet else "[DRY RUN — nothing will be written]"))
    L.append(f"  receipt: {plan['receipt_path']}"
             + ("" if plan["receipt_present"] else "   [ABSENT]"))
    L.append("")
    L.append("  PATHS THIS WOULD TOUCH (listed BEFORE anything is written):")
    if not plan["actions"]:
        # CRUCIBLE-3 D05-F5: this said "no receipt" UNCONDITIONALLY, four lines
        # under a receipt line that had printed the receipt's path WITHOUT
        # [ABSENT]. The same render, contradicting itself, in one screen. An empty
        # action list has two very different causes and the operator needs to know
        # which: nothing was ever installed, or everything the receipt names was
        # refused as out of this invocation's domain.
        if not plan["receipt_present"]:
            L.append("    (none — no receipt, so no byte pre-image is recoverable)")
        elif plan.get("out_of_domain_count"):
            L.append("    (none — the receipt was READ and every entry in it was "
                     "RETAINED as out of this invocation's domain; see below)")
        else:
            L.append("    (none — the receipt was read and it names nothing this "
                     "invocation would undo)")
    # D05-F5: `out_of_domain` lived in --json ONLY, and --json is not the default.
    # The C1 docstring promises these orphans are "COUNTED and NAMED in the plan
    # rather than silently dropped" -- true of the JSON, false of the surface a
    # human reads. `retained: true` is residue by the plan's own word, and it
    # reached neither `skipped`, nor `errors`, nor the exit code.
    _ood = plan.get("out_of_domain") or []
    if _ood:
        L.append("")
        L.append(f"  ITEMS RETAINED ({len(_ood)} — named in the receipt, OUTSIDE this "
                 "invocation's domain, NOT undone):")
        for o in _ood:
            L.append(f"    RETAINED  {o.get('path')}")
            _why = o.get("reason") or o.get("refusal_code") or o.get("blocked")
            if _why:
                L.append(f"              {_why}")
        L.append("    These are still on disk. This run did NOT undo them, and it "
                 "does not call itself clean.")
        L.append("")
        L.append("  PATHS THIS WOULD TOUCH (continued):")
    for a in plan["actions"]:
        L.append(f"    {a['action']:<20} {a['path']}   [{a.get('undo_class')}]")
        # THE RESOLVED TARGET IS PART OF THE LISTING, ALWAYS.  A plan that lists
        # the name and writes the resolution is a plan that lists a different
        # file from the one it touches, and that difference destroyed a user's
        # AGENTS.md while the run reported RESTORED.
        L.append(f"        RESOLVES TO: {a.get('resolved_path')}"
                 + ("   <-- SYMLINK, and this plan does not name that path"
                    if a.get("is_symlink") else
                    "   <-- DIFFERS from the named path"
                    if a.get("resolved_differs_from_named") else ""))
        L.append(f"        pre-image: {a.get('preimage_path')}  "
                 f"sha256={a.get('preimage_sha256')}")
        L.append(f"        current  : sha256={a.get('current_sha256')}")
        if a.get("blocked"):
            L.append(f"        BLOCKED  : {a['blocked']}")
    L.append("")
    if plan["observed_live_installs"]:
        L.append("  LIVE INSTALLS A RESOLVER CAN SEE RIGHT NOW:")
        for o in plan["observed_live_installs"]:
            L.append(f"    {o['platform']}: {json.dumps(o['resolved'])[:300]}")
        L.append("")
    L.append("  ⚠ IRREVERSIBLE RESIDUE — NAMED. This uninstall CANNOT undo:")
    for r in plan["irreversible_residue"]:
        L.append(f"    * {r['what']}")
        L.append(f"        where: {r['where']}")
        L.append(f"        why  : {r['why']}")
    L.append("")
    if plan["blocked"]:
        L.append("  RESULT: REFUSED — this run will not claim a clean uninstall.")
        for b in plan["blockers"]:
            L.append(f"    - {b}")
    elif not wet:
        L.append("  RESULT: DRY RUN complete. Re-run with --uninstall --yes to apply.")
    if applied:
        L.append(f"  APPLIED: restored={applied['restored']} "
                 f"markers_removed={applied['removed']} "
                 f"elements_removed={applied.get('elements_removed')} "
                 f"PARTIAL={applied['partial']} deleted={applied['deleted']} "
                 f"already_absent={applied.get('already_absent')} "
                 f"skipped={applied['skipped']} errors={applied['errors']}")
        _absent_rows = [d for d in applied["applied"]
                        if d["result"] == "ALREADY_ABSENT"]
        if _absent_rows:
            L.append("  NOTHING OF OURS WAS REMOVED AT THESE PATHS — they were "
                     "not present before this run:")
            for d in _absent_rows:
                L.append(f"    * {d['path']}")
        _refused_rows = [d for d in applied["applied"]
                         if d["result"] == "SKIPPED" and d.get("is_symlink")]
        if _refused_rows:
            L.append("  ⚠ REFUSED — a named path resolved somewhere this plan "
                     "never listed, and was NOT written:")
            for d in _refused_rows:
                L.append(f"    * {d['path']} -> {d.get('resolves_to')}")
        for d in applied["applied"]:
            if d["result"] == "REFUSED_PRINT_ELEMENT":
                L.append(f"  ⚠ REFUSED — element-level removal at {d['path']} could not be "
                         f"lossless; REMOVE THIS ELEMENT BY HAND:")
                L.append("      " + json.dumps(d.get("manual_removal"), ensure_ascii=False))
            elif d["result"] == "ELEMENT_REMOVED":
                L.append(f"  element removed at {d['path']} (matched by {d.get('matched_by')}; "
                         f"pruned {d.get('pruned_keys')})")
        if applied["partial"]:
            L.append("  ⚠ PARTIAL REMOVALS — this run does NOT claim a clean "
                     "uninstall for these paths:")
            for d in applied["applied"]:
                if d["result"] == "PARTIAL_UNPAIRED_MARKER":
                    L.append(f"    * {d['path']}: {d['reason']}")
    return "\n".join(L)


# ===========================================================================
# R8 — the trust-bypass scan, over the WHOLE SHIPPED SET.
# ===========================================================================
def shipped_files() -> List[str]:
    """The artifacts that constitute the shipped surface (organs + hook + CLI).

    THE POPULATION IS THE SHIPPED SET, NOT THIS FILE.  A scan that checks only
    the file it lives in answers a question nobody asked: the criterion is about
    what we ship, and the sibling that spawns SUBPROCESSES is exactly where a
    trust-bypass flag would actually be passed.
    """
    here = Path(__file__).resolve().parent
    names = ["voidloom_preamble.py", "voidloom_canary.py",
             "voidloom_hooks_status.py", "voidloom_instructions.py",
             "voidloom_heartbeat.py"]
    out = [str(here / n) for n in names if (here / n).exists()]
    # The round's shipped set is wider than organs/: the hook that WRITES the
    # heartbeat and the CLI that DISPATCHES the verbs are shipped too, and a
    # trust-bypass flag or an unguarded write in either is the same defect.
    root = here.parent
    for rel in ("hooks/session_start_inject.py", "voidloom/cli.py",
                "voidloom/_agent_context.py"):
        if (root / rel).exists():
            out.append(str(root / rel))
    return out


# CLASS-S, SWEPT BY DEFINITION RATHER THAN BY SUSPICION.
# Every call that WRITES or DELETES a path is enumerated by AST across the whole
# shipped set, and each one must sit inside a function that is either (a) the
# guarded I/O layer itself, (b) a named fixture builder that only ever writes
# into a directory it created, or (c) the preserved pre-fix RED. Anything else
# is an unguarded operator-path write, which is the shape that destroyed a
# user's AGENTS.md.  The population is the PROPERTY (a mutating filesystem call),
# not a list of the sites we happened to remember.
#
# Bare attribute names are Path methods.  ``replace`` and ``rename`` are
# DELIBERATELY NOT in this set: as bare attributes they are overwhelmingly
# ``str.replace`` and ``datetime.replace``, so including them would fill the
# census with false positives and teach a reader to skim it.  They are caught
# instead in their unambiguous QUALIFIED form below.
_MUTATING_ATTRS = {"write_text", "write_bytes", "unlink", "touch", "mkdir",
                   "rmdir", "symlink_to", "hardlink_to"}
_MUTATING_QUALIFIED = {
    "os.replace", "os.rename", "os.remove", "os.unlink", "os.symlink",
    "os.link", "os.makedirs", "os.mkdir", "os.rmdir", "os.truncate",
    "os.chmod", "os.write", "os.open",
    "shutil.rmtree", "shutil.move", "shutil.copy", "shutil.copy2",
    "shutil.copyfile",
}
# Each entry is a function that may mutate the filesystem, WITH THE REASON.  An
# allow-list without reasons is a place to hide, so the reasons are published in
# the check's own detail and a reader can audit them one by one.
_IO_ALLOWLIST: Dict[str, str] = {
    "guarded_write_bytes": "the guarded layer itself — O_NOFOLLOW + refusal",
    "guarded_write_text": "delegates to guarded_write_bytes",
    "_write_all": ("the write half of guarded_write_bytes, split out so a SHORT WRITE "
                   "raises instead of silently truncating (Crucible-3 D05-C1). It has "
                   "exactly one caller, inside the guarded layer, AFTER the identity "
                   "gate has spoken — it opens nothing, names no path, and cannot "
                   "reach a descriptor the guard did not already hold"),
    "guarded_unlink": "the guarded layer itself — refuses a symlinked target",
    "guarded_rmdir_if_empty": ("rmdir of a directory the receipt says WE created, only when "
                               "EMPTY; rmdir cannot follow a link and cannot delete content"),
    "guarded_read_text": "READ-ONLY open: O_RDONLY|O_NONBLOCK|O_NOFOLLOW",
    # THESE TWO ENTRIES ARE CLAIMS, AND THE CLAIM IS BOUND BELOW.  An allow-list
    # without reasons is a place to hide; an allow-list whose reasons nothing
    # checks is the same place with better lighting.  Any entry whose reason
    # begins READ-ONLY is parsed by scan_readonly_open_claims() and must contain
    # no creating, truncating or writing token at all -- so adding a function
    # here cannot smuggle a write past the census.
    "read_bytes_if_regular":
        "READ-ONLY open: O_RDONLY|O_NONBLOCK, and S_ISREG on the descriptor. "
        "The non-blocking, type-checked replacement for Path.read_*() at every "
        "operator-controlled path",
    "_sha256_file":
        "READ-ONLY open: O_RDONLY|O_NONBLOCK, and S_ISREG on the descriptor",
    "_unsafe_restore_following_the_symlink":
        "the PRESERVED pre-fix write, called only from self_check over a "
        "fixture built in a temp dir, so its RED stays runnable",
    "self_check": "fixtures, each under a directory this function created",
    "selftest": "fixtures, each under a directory this function created",
    "_insert_poison": "writes a row into a COPY of the brain, never the brain",
    "_store_probe": "writes a row into a COPY of the brain, never the brain",
    "_premigration_copy": "builds a fixture DB at a caller-supplied temp path",
    "_copy_brain": "creates the temp directory that will hold the work copy",
    "materialise_patched_hook": "writes the patched hook into a mkdtemp",
    "gate_gb": "rmtree of the mkdtemp it created three lines earlier",
    "run_control": "rmtree of the control's own mkdtemp",
    "_mk": "self-check helper that creates and tracks temp dirs",
    "fixture_root": "creates the ONE directory that holds this suite's fixtures",
    "control_root": ("canary: creates the ONE directory OUTSIDE any brain-carrying tree that "
                     "holds the control's clean dirs (Council #12: a control cwd inside the "
                     "brain tree let the model walk up and read the brain)"),
    "_remove_default_work_db": ("preamble selftest: unlinks ONLY the per-run brain COPY it "
                                "named under fixture_root() (+ -wal/-shm/-journal); the live "
                                "brain is refused before this runs"),
    "fixture_dir": "mkdtemp inside the fixture root this suite owns",
    "fs_recycles_inode_numbers":
        "writes and unlinks a probe file inside a mkdtemp it created and removes",
    "_cov": "self-check helper writing the ledger fixture it owns",
    # ---- this round's additions ----
    "install_execute": ("OUR pre-image store under the brain home is created through "
                        "guarded_mkdir_chain (iteration 3); every operator-path write goes "
                        "through guarded_write_bytes / guarded_unlink"),
    "instructions_execute": ("the ~/.agents directory the plan NAMED and the pre-image store "
                             "are created through guarded_mkdir_chain (iteration 3); every "
                             "file write goes through guarded_write_bytes"),
    "ledger_append": "writes the ledger through guarded_write_text; its parent through guarded_mkdir_chain",
    "guarded_append_bytes": ("the guarded layer's APPEND face -- the ONE writer for the "
                             "heartbeat: refusal_code on the name, O_APPEND|O_CREAT|O_NOFOLLOW|"
                             "O_NONBLOCK at the open, S_ISREG + st_nlink == 1 on the descriptor "
                             "(R3/R6/R9 iteration 2; a third writer without the link-count gate "
                             "was the hardlink defect reintroduced)"),
    "guarded_mkdir_chain": ("the guarded layer's DIRECTORY face (R9 v3): realpath == abspath "
                            "for the whole chain BEFORE anything is created, then one "
                            "O_DIRECTORY|O_NOFOLLOW descriptor per component and mkdir(dir_fd=) "
                            "relative to the directory HELD; a link at any component is ELOOP -> "
                            "refused; nothing is created under a name we do not own"),
    "_open_dir_nofollow": "READ-ONLY open: O_RDONLY|O_DIRECTORY|O_NOFOLLOW of one chain component",
    "read_text_bounded": "READ-ONLY open: O_RDONLY|O_NONBLOCK (O_NOFOLLOW unless follow), S_ISREG on the descriptor",
    "read_heartbeats": "READ-ONLY open: O_RDONLY|O_NONBLOCK|O_NOFOLLOW, S_ISREG on the descriptor",
    "_first_timestamp_in_jsonl": "READ-ONLY open: O_RDONLY|O_NONBLOCK|O_NOFOLLOW, S_ISREG on the descriptor",
    "_maybe_silence_stderr": ("cli.py: opens os.devnull O_WRONLY to silence fd 2 for --quiet; "
                              "no operator path is ever named"),
}


# SUBPROCESS IS DELIBERATELY OUT OF THIS POPULATION AND THE OMISSION IS NAMED.
# `subprocess.run` in the canary hands the filesystem to a command the OPERATOR
# supplied — running it is the canary's whole purpose, and no guard in this file
# can bound what it writes.  The census therefore covers every mutating call
# THIS CODE MAKES, and not what a runner does on its behalf.  A count whose
# population is narrower than its name must say so WHERE THE COUNT IS READ, not
# only where it is built: a reader auditing I5 cannot otherwise tell a shape
# that was considered and excluded from one that was never in the vocabulary.
# The exclusion is DERIVED at run time by scan_operator_spawns() below and
# published in I5's own detail, so the number cannot rot into a typed claim.


# ATTRIBUTES OF `os` THAT CREATE, TRUNCATE OR WRITE.  A function whose
# allow-list reason says READ-ONLY may reference none of them.
#
# THESE ARE MATCHED AS AST ATTRIBUTE NODES, NOT AS SUBSTRINGS OF THE DUMP.  The
# first version of this scanner matched `"write" in ast.dump(node)` and went RED
# on all three read-only functions -- because one docstring says "a WRITER
# appears" and another says "must WRITE back".  It was pinning a structural
# claim by matching a substring of English, which is the exact defect R7n's own
# comment warns about, committed inside the check written to enforce it.
# Prose cannot satisfy an Attribute node.
_WRITEY_OS_ATTRS = frozenset({"O_CREAT", "O_TRUNC", "O_WRONLY", "O_RDWR",
                              "O_APPEND", "write", "pwrite", "ftruncate",
                              "truncate", "unlink", "remove", "rename",
                              "replace", "mkdir", "makedirs", "chmod"})


def scan_readonly_open_claims(paths: Sequence[str]) -> Dict[str, Any]:
    """Every allow-list entry that CLAIMS to be read-only, checked.

    ``os.open`` is in the mutating population because ``O_CREAT``/``O_TRUNC``
    make it mutating.  Two functions were added to the allow-list on the grounds
    that their opens are read-only, and an unbound grounds is an assertion of
    good intent.  This parses each such function and reports any writing token
    inside it, so the exemption is REVOKED the moment it stops being true.
    """
    import ast
    claimed = [k for k, v in _IO_ALLOWLIST.items() if v.startswith("READ-ONLY")]
    violations: List[Dict[str, Any]] = []
    found: List[str] = []
    for p in paths:
        try:
            tree = ast.parse(read_text_if_regular(p))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in claimed:
                continue
            found.append(node.name)
            hits = sorted({
                f"os.{n.attr}" for n in ast.walk(node)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name) and n.value.id == "os"
                and n.attr in _WRITEY_OS_ATTRS})
            if hits:
                violations.append({"file": Path(p).name, "function": node.name,
                                   "writing_tokens": hits})
    return {"claimed": sorted(set(claimed)), "found": sorted(set(found)),
            "violations": violations}


def scan_operator_spawns(paths: Sequence[str]) -> Dict[str, Any]:
    """Every site that hands the filesystem to an operator-supplied command."""
    import ast
    sites: List[Dict[str, Any]] = []
    for p in paths:
        try:
            tree = ast.parse(Path(p).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        stack: List[str] = []

        class W(ast.NodeVisitor):
            def visit_FunctionDef(self, node):          # noqa: N802
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_AsyncFunctionDef(self, node):     # noqa: N802
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node):                 # noqa: N802
                d = _dotted(node.func)
                if d in ("subprocess.run", "subprocess.Popen",
                         "subprocess.call", "subprocess.check_output",
                         "os.system", "os.execv", "os.spawnv"):
                    sites.append({"file": Path(p).name, "line": node.lineno,
                                  "call": d,
                                  "enclosing": (stack[-1] if stack else
                                                "<module>")})
                self.generic_visit(node)

        W().visit(tree)
    return {"sites": sorted(sites, key=lambda x: (x["file"], x["line"])),
            "count": len(sites)}


def _dotted(node: Any) -> Optional[str]:
    """`os.path.realpath` -> 'os.path.realpath'; anything else -> None."""
    import ast
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def scan_mutating_io(paths: Sequence[str]) -> Dict[str, Any]:
    """Enumerate every filesystem-mutating call and the function that holds it."""
    import ast
    sites: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for p in paths:
        try:
            tree = ast.parse(Path(p).read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as e:
            errors.append({"file": Path(p).name,
                           "error": f"{type(e).__name__}: {e}"})
            continue
        stack: List[str] = []
        toplevel: List[str] = []      # names of MODULE-LEVEL defs in the chain (the guards)

        def _mode_writes(call: "ast.Call") -> bool:
            """The builtin ``open`` (and ``Path.open``) write when their mode carries
            w / a / x / +.  EXECUTED (reflector iter 2, MB09): a fallback writer built
            on ``open(path, "ab")`` was invisible to a census whose population was
            ``os.*`` and ``Path.write_*`` only."""
            m = None
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) \
                    and isinstance(call.args[1].value, str):
                m = call.args[1].value
            for k in call.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant) \
                        and isinstance(k.value.value, str):
                    m = k.value.value
            return bool(m) and any(c in m for c in "wax+")

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, node):          # noqa: N802
                stack.append(node.name)
                if len(stack) == 1:
                    toplevel.append(node.name)
                self.generic_visit(node)
                stack.pop()
                if len(stack) == 0:
                    toplevel.pop()

            def visit_AsyncFunctionDef(self, node):     # noqa: N802
                self.visit_FunctionDef(node)

            def visit_ClassDef(self, node):             # noqa: N802
                # a def inside a class inside a function is NESTED: it never
                # becomes a guard by carrying a guard's name (MB09's shape)
                self.generic_visit(node)

            def visit_Call(self, node):                 # noqa: N802
                dotted = _dotted(node.func)
                bare = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else None)
                hit = (dotted if dotted in _MUTATING_QUALIFIED else
                       (bare if bare in _MUTATING_ATTRS else None))
                if hit is None:
                    is_builtin = isinstance(node.func, ast.Name) and node.func.id == "open"
                    is_attr_open = (bare == "open" and dotted != "os.open")
                    if (is_builtin or is_attr_open) and _mode_writes(node):
                        hit = "open(mode=w|a|x|+)" if is_builtin else ".open(mode=w|a|x|+)"
                if hit:
                    sites.append({"file": Path(p).name,
                                  "function": (stack[-1] if stack else
                                               "<module>"),
                                  "enclosing_chain": list(stack),
                                  "toplevel_guard": (toplevel[0] if toplevel else None),
                                  "call": hit, "line": node.lineno})
                self.generic_visit(node)

        V().visit(tree)
    # A SITE IS GUARDED ONLY BY A MODULE-LEVEL DEF ON THE ALLOW-LIST.  A nested
    # def that merely CARRIES a guard's name (MB09: `class _Fallback: def
    # guarded_append_bytes` inside `_shared_writer`) is not the guard.
    unguarded = [s for s in sites
                 if not (s.get("toplevel_guard") in _IO_ALLOWLIST)]
    return {"files_scanned": len(paths), "mutating_call_sites": len(sites),
            "parse_errors": errors,
            # THE WHOLE CENSUS, not just its total: a sweep whose population a
            # reader cannot inspect is a number they have to take on trust.
            "sites": sites,
            "sites_by_file": {f: sum(1 for s in sites if s["file"] == f)
                              for f in sorted({s["file"] for s in sites})},
            "unguarded": unguarded,
            "allowlist_with_reasons": dict(sorted(_IO_ALLOWLIST.items()))}


def scan_trust_bypass(paths: Sequence[str]) -> Dict[str, Any]:
    """Find every occurrence of the trust-bypass flag and classify it.

    THE NEEDLE IS ASSEMBLED AT RUN TIME and never appears as a literal in any
    shipped file — otherwise the scanner matches itself, which is precisely
    what the first version of this check did.  Same class as `pgrep -f`
    matching its own command line: an instrument whose pattern includes the
    instrument.

    Classification: a hit on a ``#`` comment line, or inside a ``\"\"\"`` block,
    is PROSE — the residue notes and the Codex verification note must be able to
    NAME the flag whose use invalidated an earlier demo.  Anything else is CODE,
    and CODE hits must be zero.
    """
    needle = "dangerously" + "-" + "bypass"
    hits: List[Dict[str, Any]] = []
    scanned = 0
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        scanned += 1
        in_doc = False
        for n, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            is_comment = stripped.startswith("#")
            found = needle in line
            if found:
                in_prose = in_doc or ('"""' in line)
                kind = "comment" if is_comment else ("docstring" if in_prose else "CODE")
                hits.append({"path": path, "line": n, "kind": kind,
                             "text": stripped[:160]})
            # Toggle AFTER classifying, so the opening line of a docstring that
            # itself carries the needle is judged by the state it opens into.
            if line.count('"""') % 2 == 1:
                in_doc = not in_doc
    # DERIVED, NOT TYPED. The reflector filed this as borderline: the claim was
    # true (the needle is built at line ~979) but it was a hand-typed True in a
    # published result. A one-line self-grep derives it, so it is derived.
    _self = Path(__file__).read_text(encoding="utf-8")
    _runtime_assembled = _self.count(needle) == 0
    return {"needle_assembled_at_runtime": _runtime_assembled,
            "files_scanned": scanned, "files": list(paths),
            "total_hits": len(hits),
            "code_hits": sum(1 for h in hits if h["kind"] == "CODE"),
            "prose_hits": sum(1 for h in hits if h["kind"] != "CODE"),
            "hits": hits}


# ===========================================================================
# self-check (the invariants this file declares)
# ===========================================================================
def self_check(_nested: bool = False) -> Dict[str, Any]:
    """``_nested`` is set ONLY by T1, which re-runs this whole suite under a
    shifted calendar.  It suppresses T1 itself so the recursion terminates."""
    src = Path(__file__).read_text(encoding="utf-8")
    checks: List[Dict[str, Any]] = []

    def add(cid, ok, obs, detail, kind="assert"):
        checks.append({"id": cid, "kind": kind, "ok": ok,
                       "observations": obs, "detail": detail})

    net = sorted(set(re.findall(
        r"^\s*(?:import|from)\s+(socket|urllib|http|requests|httpx|smtplib)\b",
        src, flags=re.M)))
    add("I1_no_network", not net, 1, {"network_imports": net})
    bad = re.findall(r"os\.environ\.setdefault\(\s*[\"']CUDA_VISIBLE_DEVICES", src)
    gpu = sorted(set(re.findall(r"^\s*(?:import|from)\s+(torch|tensorflow)\b",
                                src, flags=re.M)))
    add("I2_gpu_law", (not bad and not gpu), 2,
        {"forbidden_setdefault_hits": len(bad), "gpu_imports": gpu})

    # EXPIRED must never leak the old value.
    old = (datetime.now(timezone.utc) - timedelta(days=TTL_DAYS + 5)
           ).strftime("%Y-%m-%dT%H:%M:%SZ")
    f_old = freshness(old)
    f_new = freshness(utc_now())
    # DERIVED FROM THE OBJECT THE FIELD NAMES.  This read
    #     leaked = "observed_at" in {...} and False
    # — a trailing `and False` pinned the only TTL-leak detector in the file to
    # False unconditionally, and it was then PUBLISHED as the field "leak". It
    # could never fire, and there WAS a leak for it to have caught: freshness()
    # returned observed_at on EXPIRED, contradicting its own docstring. A count
    # that derives from nothing is worse than no count, because it occupies the
    # place where a real one would have gone.
    leaked = carries_a_value(f_old)
    # THE DETECTOR IS DRIVEN OVER A ROW THAT DOES LEAK, so a constant cannot
    # impersonate a measurement: if carries_a_value stops firing, this leg goes
    # red even when nothing in freshness() has changed.
    leak_probe = {"state": "EXPIRED", "observed_at": "2020-01-01T00:00:00Z",
                  "age_days": 9999.0, "ttl_days": TTL_DAYS}
    detector_fires_on_a_leaky_row = carries_a_value(leak_probe)
    add("R6a_expired_is_an_absence",
        (f_old["state"] == "EXPIRED" and f_new["state"] == "FRESH"
         and freshness(None)["state"] == "NEVER" and not leaked
         and detector_fires_on_a_leaky_row), 5,
        {"expired": f_old["state"], "fresh": f_new["state"],
         "never": freshness(None)["state"],
         "expired_row_returned_observed_at": leaked,
         "detector_fires_on_a_known_leaky_row": detector_fires_on_a_leaky_row,
         "expired_row_keys": sorted(f_old.keys()),
         "note": ("the row that would look different if EXPIRED still carried "
                  "its value — and the assertion now BINDS it")})

    # --- R6i: THE TTL BOUNDARY IS WHERE R6'S TEXT SAYS IT IS -------------
    # R6c drives five expiry shapes and NONE of them sits at the boundary, so
    # the number R6 names is unpinned: moving the comparison to `ttl_days + 1`
    # left every suite green (EXECUTED as NR11). Differential at 30.5 days:
    # SHIPPED EXPIRED / observed_at None / carries_a_value False; the mutant
    # FRESH / observed_at '2026-08-09T07:33:01Z' / carries_a_value True — a
    # lapsed proof handed to a renderer as current.
    # THE NUMBER IS WRITTEN HERE AS A BARE LITERAL. Routing it through TTL_DAYS
    # would let one edit move the constant AND the bound together, which is
    # exactly how GM21 defeated R4d's floor pin.
    _b_just_in = freshness((datetime.now(timezone.utc)
                            - timedelta(days=29.5)
                            ).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _b_just_out = freshness((datetime.now(timezone.utc)
                             - timedelta(days=30.5)
                             ).strftime("%Y-%m-%dT%H:%M:%SZ"))
    add("R6i_the_TTL_boundary_is_where_R6_says_it_is",
        (_b_just_in["state"] == "FRESH"
         and _b_just_out["state"] == "EXPIRED"
         and carries_a_value(_b_just_out) is False
         and TTL_DAYS == 30), 4,
        {"half_a_day_inside_30": _b_just_in["state"],
         "half_a_day_outside_30": _b_just_out["state"],
         "ttl_days_CONSTANT": TTL_DAYS,
         "the_number_R6_names": 30,
         "note": ("R6's text names 30 days. Moving the comparison to "
                  "ttl_days + 1 left every suite green (executed); 30 is "
                  "written here as a literal so the constant is graded against "
                  "the CRITERION and not against itself.")})

    # --- R6j: FOUR STATES, AND ONLY ONE OF THEM WITHHOLDS ----------------
    # freshness() distinguishes NEVER / UNPARSEABLE / EXPIRED / FRESH correctly
    # and NOTHING asserted the distinction: folding UNPARSEABLE into EXPIRED
    # left every suite green (EXECUTED as NR12) and produced a row labelled
    # EXPIRED that STILL CARRIED its raw value — the exact leak carries_a_value
    # exists to catch, never once run over a freshness() result.
    # R6 (AMD 3) says "I cannot measure" and "I measured zero" must never share
    # an output. An UNREADABLE timestamp is not a KNOWN expiry, and the ledger's
    # three-state distinction was never swept down into this function.
    _states = {"NEVER": freshness(None),
               "UNPARSEABLE": freshness("not-a-timestamp"),
               "EXPIRED": freshness("2020-01-01T00:00:00Z"),
               "FRESH": freshness(utc_now())}
    add("R6j_freshness_states_are_four_and_only_EXPIRED_withholds",
        # NON-EMPTY BEFORE MEMBERSHIP MEANS ANYTHING (the all([]) sweep).
        (len(_states) == 4
         and all(_states[k]["state"] == k for k in _states)
         and carries_a_value(_states["EXPIRED"]) is False
         and _states["EXPIRED"].get("withheld_observed_at") is True
         and _states["NEVER"]["observed_at"] is None
         # and the two NON-expired states DO carry what they have, or the
         # withholding has spread to rows that were never stale.
         and carries_a_value(_states["FRESH"]) is True
         and carries_a_value(_states["UNPARSEABLE"]) is True), 6,
        {"states": {k: v["state"] for k, v in _states.items()},
         "expired_carries_a_value": carries_a_value(_states["EXPIRED"]),
         "unparseable_carries_its_raw_value":
             carries_a_value(_states["UNPARSEABLE"]),
         "note": ("an unreadable timestamp is not a known expiry. The negative "
                  "legs matter as much: a guard that withholds from EVERY state "
                  "has removed the field rather than protected it, and would "
                  "pass a check that only looked at EXPIRED.")})

    # ledger_view must not carry a value for an expired row.
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"vl_selfcheck_{os.getpid()}.json"
    try:
        tmp.write_text(json.dumps({"schema_version": 1, "entries": [
            {"platform": "codex", "gate": "G1", "verdict": "PROVEN",
             "observations": 4, "observed_at": old}]}), encoding="utf-8")
        v = ledger_view(str(tmp), "codex")
        add("R6b_ledger_view_withholds_expired_value",
            (v["last_proven"]["state"] == "EXPIRED"
             and "observed_at" not in v["last_proven"]
             and "observations" not in v["last_proven"]), 1,
            {"last_proven": v["last_proven"],
             "note": "the renderer has nothing to print but the absence"})
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    # Coverage must refuse whenever ANY claim has lapsed.  BOTH LEGS, over a
    # LEDGER WE CONTROL.  The old form asserted a biconditional over the AMBIENT
    # environment: with expired_count=0 — the normal case, and what every run of
    # it observed — it only checked that a non-refused percentage was printed,
    # i.e. the leg the criterion is not about. Its own `fake`/`rows_expired`
    # locals were assigned, discarded via `_ = fake, rows_expired`, and never
    # used: the visible trace of a fixture that was started and not finished.
    _lg = Path(os.environ.get("TMPDIR", "/tmp")) / f"vl_sc_cov_{os.getpid()}.json"

    def _cov(entries):
        _lg.write_text(json.dumps({"schema_version": 1, "entries": entries}),
                       encoding="utf-8")
        st = status(os.getcwd(), str(_lg))
        return st, {r["id"]: r for r in st["platforms"]}

    # HOLD THE SURVEY DIMENSION FRESH FOR THIS LEG — AND RESTORE IT.
    # R6c is about the LEDGER.  The same round gave every survey row the same
    # 30-day TTL, so from 30 days after MATRIX_SURVEY_DATE a CLEAN ledger
    # legitimately REFUSES and this leg's premise becomes false: the suite goes
    # RED on code nobody touched. EXECUTED: MATRIX_SURVEY_DATE moved back 60
    # days, nothing else altered -> verdict=FAIL, rc=1,
    # failed=['R6c_coverage_refuses_in_ALL_THREE_expiry_shapes'],
    # clean_leg='REFUSED', while R6d stayed green with lapsed_survey_count=10.
    # A suite that turns red by calendar is a suite that gets disabled, and R6's
    # whole refusal guarantee rests on it. R6d is the leg that tests a LAPSED
    # survey; it sets its own date and keeps doing so.
    _saved_survey_dates = _hold_survey_dates_fresh()
    try:
        st_clean, _ = _cov([])
        # SHAPE (a): an expired row on an INSTALLED platform.  This one already
        # refused before the fix; it is the leg that made the criterion look met.
        st_a, rows_a = _cov([{"platform": "claude-code", "gate": "G1",
                              "verdict": "PROVEN", "observations": 4,
                              "observed_at": old}])
        # SHAPE (b): an expired row on a NOT-INSTALLED platform.  Printed 0.0%.
        st_b, rows_b = _cov([{"platform": "gemini-cli", "gate": "G1",
                              "verdict": "PROVEN", "observations": 4,
                              "observed_at": old}])
        # SHAPE (c): an expired G1 MASKED by a fresh G2.  Printed 10.0% and the
        # row read SUPPORTED, because the refusal was derived from the collapsed
        # row state instead of from the proof keys.
        st_c, rows_c = _cov([{"platform": "claude-code", "gate": "G1",
                              "verdict": "PROVEN", "observations": 4,
                              "observed_at": old},
                             {"platform": "claude-code", "gate": "G2",
                              "verdict": "OBEYED", "observations": 4,
                              "observed_at": utc_now()}])
        _today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        add("R6c_coverage_refuses_in_ALL_THREE_expiry_shapes",
            (st_clean["coverage"]["coverage_percent"] != "REFUSED"
             # THE HOLD IS ASSERTED, NOT ASSUMED. Without it this leg's premise
             # expires on a date, and the assertion that depends on the hold must
             # be able to see the hold missing TODAY, not only after the lapse.
             and st_clean["coverage"]["lapsed_survey_count"] == 0
             and len(PLATFORMS) > 0
             and all(p["verification"]["date"] == _today_iso for p in PLATFORMS)
             and st_a["coverage"]["coverage_percent"] == "REFUSED"
             and st_b["coverage"]["coverage_percent"] == "REFUSED"
             and st_c["coverage"]["coverage_percent"] == "REFUSED"
             and "gemini-cli" in st_b["coverage"]["expired"]
             and "claude-code" in st_c["coverage"]["expired"]), 6,
            {"clean_leg": st_clean["coverage"]["coverage_percent"],
             "survey_dates_held_fresh_for_this_leg": True,
             "lapsed_survey_count_in_clean_leg":
                 st_clean["coverage"]["lapsed_survey_count"],
             "shape_a_expired_on_INSTALLED": st_a["coverage"]["coverage_percent"],
             "shape_b_expired_on_NOT_INSTALLED": st_b["coverage"]["coverage_percent"],
             "shape_b_installed": rows_b["gemini-cli"]["installed"],
             "shape_b_row_state": rows_b["gemini-cli"]["state"],
             "shape_c_expired_G1_masked_by_fresh_G2":
                 st_c["coverage"]["coverage_percent"],
             "shape_c_row_state": rows_c["claude-code"]["state"],
             "shape_c_expired_detail": st_c["coverage"]["expired_detail"],
             "note": ("gemini-cli is NOT installed on this host and is in the "
                      "fixture ON PURPOSE: an expired row on a not-installed "
                      "platform used to slip past the refusal. Shapes (b) and "
                      "(c) both printed a NUMBER before this fix.")})

        # A LAPSED SURVEY ENTERS THE SAME REFUSAL.  The survey state is a typed
        # table entry; before this fix it printed as `verified : EXECUTED` with
        # no TTL directly above a proof row reading NEVER [TTL 30d], which is
        # R6's FAIL disposition committed by the verb that names it.
        _saved = PLATFORMS[0]["verification"]["date"]
        try:
            PLATFORMS[0]["verification"]["date"] = "2020-01-01"
            st_d, rows_d = _cov([])
            add("R6d_a_lapsed_SURVEY_lapses_into_the_refusal",
                (st_d["coverage"]["coverage_percent"] == "REFUSED"
                 and st_d["coverage"]["lapsed_survey_count"] >= 1
                 and rows_d[PLATFORMS[0]["id"]]["survey_freshness"]["state"]
                 == "EXPIRED"), 2,
                {"coverage_percent": st_d["coverage"]["coverage_percent"],
                 "lapsed_survey_count": st_d["coverage"]["lapsed_survey_count"],
                 "survey_state": rows_d[PLATFORMS[0]["id"]]["survey_freshness"],
                 "rendered": [l.strip() for l in
                              render_status(st_d).splitlines()
                              if "surveyed" in l][:1],
                 "note": ("the survey claim now carries the SAME clock as the "
                          "proof claim, and the renderer says 'surveyed', not "
                          "'verified' — the word that makes a documentation-"
                          "derived row read like a canary result")})
        finally:
            PLATFORMS[0]["verification"]["date"] = _saved

        # --- R6f: AN UNREADABLE LEDGER IS AN UNKNOWN, NOT A ZERO ----------
        # EXECUTED before this branch existed: a ledger holding
        # '{ this is not json' returned coverage_percent=0.0, expired=[],
        # ledger_present=True and every proof row NEVER, while the renderer
        # dropped its '[ABSENT]' note — the entire proof channel lost, shown as
        # a number. THREE LEGS, because the distinction is the point: EMPTY is a
        # legitimate 0-of-0 and must still MEASURE; MALFORMED and UNPARSEABLE
        # must both REFUSE, and the refusal must be visibly different from the
        # empty one in the rendered text a human reads.
        _bad = Path(os.environ.get("TMPDIR", "/tmp")) / f"vl_sc_badled_{os.getpid()}.json"
        _root_absent = fixture_dir("vl_sc_absent_")
        try:
            _lg.write_text(json.dumps({"schema_version": 1, "entries": []}),
                           encoding="utf-8")
            st_empty = status(os.getcwd(), str(_lg))
            _bad.write_text("{ this is not json", encoding="utf-8")
            st_malformed = status(os.getcwd(), str(_bad))
            # the OTHER unreadable shape: valid JSON, wrong shape.
            _bad.write_text(json.dumps({"entries": "not a list"}),
                            encoding="utf-8")
            st_wrongshape = status(os.getcwd(), str(_bad))
            # THE THIRD STATE, and the one I broke: ABSENT. "There is no file"
            # is an OBSERVATION — zero proofs — and it is the ORDINARY FIRST-RUN
            # case. Folding it in with UNREADABLE made --status refuse and exit
            # 3 on a machine where no canary has ever run. Caught by a CLI smoke
            # test, not by this suite, which is why the leg now exists.
            _absent_p = Path(_root_absent) / "no_such_ledger.json"
            st_absent = status(os.getcwd(), str(_absent_p))
            _r_absent = render_status(st_absent)
            _r_empty = render_status(st_empty)
            _r_bad = render_status(st_malformed)
            _append_refused = None
            try:
                ledger_append(str(_bad), {"platform": "codex", "gate": "G1",
                                          "verdict": "PROVEN",
                                          "observations": 1,
                                          "observed_at": utc_now()})
                _append_refused = False
            except ValueError:
                _append_refused = True
            add("R6f_an_UNREADABLE_ledger_is_UNKNOWN_and_an_EMPTY_one_is_zero",
                (# ABSENT: a knowable zero, exit 0, named as absent.
                 st_absent["coverage"]["coverage_percent"] != "REFUSED"
                 and st_absent["ledger_present"] is False
                 and st_absent["ledger_readable"] is True
                 and st_absent["ledger_fault"] is None
                 and "ABSENT" in _r_absent and "UNREADABLE" not in _r_absent
                 and st_empty["coverage"]["coverage_percent"] != "REFUSED"
                 and st_empty["ledger_readable"] is True
                 and st_malformed["coverage"]["coverage_percent"] == "REFUSED"
                 and st_malformed["coverage"]["coverage_state"] == "UNKNOWN"
                 and st_malformed["ledger_readable"] is False
                 and st_malformed["coverage"]["proven_fresh_count"] is None
                 and st_wrongshape["coverage"]["coverage_percent"] == "REFUSED"
                 and st_wrongshape["ledger_readable"] is False
                 # ledger_view's OWN published flags, not only the count it
                 # derives from them: typing "ledger_readable": True while the
                 # derived count stays correct leaves the published field lying
                 # with the rest of the leg green (executed as mutant GM6).
                 and ledger_view(str(_bad), "codex")["ledger_readable"] is False
                 and ledger_view(str(_bad), "codex")["ledger_fault"] is not None
                 and ledger_view(str(_lg), "codex")["ledger_readable"] is True
                 and ledger_view(str(_lg), "codex")["ledger_fault"] is None
                 and ledger_view(str(_bad), "codex")["entry_count"] is None
                 and _append_refused is True
                 and _r_empty != _r_bad
                 and "UNREADABLE" in _r_bad
                 and "UNREADABLE" not in _r_empty), 11,
                {"absent_ledger_coverage":
                     st_absent["coverage"]["coverage_percent"],
                 "absent_ledger_present": st_absent["ledger_present"],
                 "absent_ledger_readable": st_absent["ledger_readable"],
                 "empty_ledger_coverage":
                     st_empty["coverage"]["coverage_percent"],
                 "empty_ledger_readable": st_empty["ledger_readable"],
                 "malformed_coverage":
                     st_malformed["coverage"]["coverage_percent"],
                 "malformed_state": st_malformed["coverage"]["coverage_state"],
                 "malformed_fault": st_malformed["ledger_fault"],
                 "malformed_numerator":
                     st_malformed["coverage"]["proven_fresh_count"],
                 "wrong_shape_coverage":
                     st_wrongshape["coverage"]["coverage_percent"],
                 "ledger_view_entry_count_on_unreadable":
                     ledger_view(str(_bad), "codex")["entry_count"],
                 "ledger_view_readable_flag_on_unreadable":
                     ledger_view(str(_bad), "codex")["ledger_readable"],
                 "ledger_view_fault_on_unreadable":
                     ledger_view(str(_bad), "codex")["ledger_fault"],
                 "ledger_view_readable_flag_on_empty":
                     ledger_view(str(_lg), "codex")["ledger_readable"],
                 "append_to_an_unreadable_ledger_refused": _append_refused,
                 "rendered_lines_differ": _r_empty != _r_bad,
                 "three_states_render_differently":
                     len({_r_absent, _r_empty, _r_bad}) == 3,
                 "note": ("'I cannot measure' and 'I measured zero' must never "
                          "share an output. The EMPTY leg is not decoration: a "
                          "refusal that also refuses the legitimate 0-of-0 has "
                          "not fixed the report, it has removed it.")})

            # --- R6g: THE REFUSAL REACHES THE EXIT CODE ------------------
            # D-14's class, swept into this verb: a refusal that a caller
            # branching on the exit code cannot see is a refusal on no path.
            # Driven through main(), the shipped entry point, in both formats.
            _bad.write_text("{ this is not json", encoding="utf-8")
            _codes = {}
            for _fmt in ([], ["--json"]):
                _buf = io.StringIO()
                with contextlib.redirect_stdout(_buf):
                    _codes[("json" if _fmt else "text")] = main(
                        ["--status", "--ledger", str(_bad)] + _fmt)
                _codes[("json" if _fmt else "text") + "_said_UNREADABLE"] = (
                    "UNREADABLE" in _buf.getvalue()
                    or '"ledger_readable": false' in _buf.getvalue())
            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf):
                _ok_code = main(["--status", "--ledger", str(_lg)])
            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf):
                _absent_code = main(["--status", "--ledger", str(_absent_p)])
            add("R6g_the_coverage_refusal_reaches_the_EXIT_CODE",
                (_codes["text"] == 3 and _codes["json"] == 3
                 and _codes["text_said_UNREADABLE"] is True
                 and _codes["json_said_UNREADABLE"] is True
                 and _ok_code == 0
                 # THE ORDINARY FIRST RUN MUST STILL EXIT 0.
                 and _absent_code == 0), 6,
                {"exit_code_text_format": _codes["text"],
                 "exit_code_json_format": _codes["json"],
                 "exit_code_on_a_READABLE_empty_ledger": _ok_code,
                 "exit_code_on_an_ABSENT_ledger": _absent_code,
                 "both_formats_named_the_fault":
                     _codes["text_said_UNREADABLE"]
                     and _codes["json_said_UNREADABLE"],
                 "note": ("the refusal is a property of the STATE, not of the "
                          "output format — the D-14 shape. The GREEN leg is the "
                          "third one: a readable ledger must still exit 0, or "
                          "the guard has disabled the verb. EXPIRED rows "
                          "deliberately keep exit 0: there the verb DID measure "
                          "and declines to average, which is not the same as "
                          "being unable to measure at all.")})
        finally:
            shutil.rmtree(_root_absent, ignore_errors=True)
            try:
                _bad.unlink()
            except OSError:
                pass
    finally:
        _restore_survey_dates(_saved_survey_dates)
        try:
            _lg.unlink()
        except OSError:
            pass

    add("R7a_residue_named", len(IRREVERSIBLE_RESIDUE) >= 4,
        len(IRREVERSIBLE_RESIDUE),
        {"ids": [r["id"] for r in IRREVERSIBLE_RESIDUE]})

    plan = uninstall_plan(os.getcwd())
    add("R7b_uninstall_refuses_without_receipt",
        (plan["blocked"] is True and plan["receipt_present"] is False)
        or (plan["receipt_present"] is True), 1,
        {"receipt_present": plan["receipt_present"],
         "blocked": plan["blocked"], "blockers": plan["blockers"]})

    # --- R6e: every gate that can be WRITTEN has a READ path --------------
    # may_record() admits NOT_CONTRADICTED, so a G3 row reached the ledger and
    # no renderer read it. G3 is the rung that tests the SELECTS platforms —
    # the ones the design says can "arrive and be outranked" — so its result
    # being invisible in --status is a declaration on no path.
    _lg3 = Path(os.environ.get("TMPDIR", "/tmp")) / f"vl_sc_g3_{os.getpid()}.json"
    try:
        _lg3.write_text(json.dumps({"schema_version": 1, "entries": [
            {"platform": "agents-md", "gate": "G3",
             "verdict": "NOT_CONTRADICTED", "observations": 3,
             "observed_at": utc_now()},
            {"platform": "agents-md", "gate": "G1", "verdict": "PRESENT",
             "observations": 3, "observed_at": utc_now()}]}), encoding="utf-8")
        _v3 = ledger_view(str(_lg3), "agents-md")
        _g3 = _v3.get("last_uncontradicted", {}).get("state")
        add("R6e_every_writable_gate_has_a_read_path",
            (_g3 == "FRESH" and _v3["last_proven"]["state"] == "NEVER"), 2,
            {"last_uncontradicted": _g3,
             "gate_columns_rendered": sorted(k for k in _v3
                                             if k.startswith("last_")),
             "last_proven_from_a_MIS_GATED_row": _v3["last_proven"]["state"],
             "note": ("two defects in one fixture: G3 had no column, and the "
                      "verdict filter was gate-blind — a row carrying "
                      "gate=G1/verdict=PRESENT populated last_proven as FRESH. "
                      "Both legs are asserted: the G3 column must fill, and "
                      "the mis-gated row must NOT fill last_proven.")})
    finally:
        try:
            _lg3.unlink()
        except OSError:
            pass

    # --- R7c: an UNPAIRED marker is residue, and is reported as residue ----
    # Executed before this fix: a file holding a paired block followed by a
    # trailing unpaired MARKER_BEGIN came back MARKER_REMOVED / removed=1 /
    # errors=0 / skipped=0 with '# >>> VOIDLOOM BEGIN >>>' AND our injected
    # line still in the user's file. R7's own FAIL disposition, executed.
    _wd = Path(fixture_dir("vl_sc_uninstall_"))
    try:
        _paired = _wd / "paired.md"
        _paired.write_text("keep me\n" + MARKER_BEGIN + "\nblock\n"
                           + MARKER_END + "\ntail\n", encoding="utf-8")
        _orphan = _wd / "orphan.md"
        _orphan.write_text("keep me\n" + MARKER_BEGIN + "\nblock1\n"
                           + MARKER_END + "\nmiddle\n" + MARKER_BEGIN
                           + "\nORPHANED INJECTED LINE\n", encoding="utf-8")
        _ap = uninstall_execute({"actions": [
            {"path": str(_paired), "action": "remove_marker_block"},
            {"path": str(_orphan), "action": "remove_marker_block"}]})
        _res = {d["path"]: d for d in _ap["applied"]}
        _left = _orphan.read_text(encoding="utf-8")
        _clean = _paired.read_text(encoding="utf-8")
        add("R7c_unpaired_marker_is_never_reported_as_a_clean_removal",
            (_res[str(_paired)]["result"] == "MARKER_REMOVED"
             and _res[str(_orphan)]["result"] == "PARTIAL_UNPAIRED_MARKER"
             and _ap["partial"] == 1 and _ap["removed"] == 1
             and MARKER_BEGIN not in _clean
             and "keep me" in _clean and "tail" in _clean), 4,
            {"paired_result": _res[str(_paired)]["result"],
             "orphan_result": _res[str(_orphan)]["result"],
             "tally": {k: _ap[k] for k in ("restored", "removed", "partial",
                                           "deleted", "skipped", "errors")},
             "orphan_file_still_holds_our_marker": MARKER_BEGIN in _left,
             "orphan_file_still_holds_our_line":
                 "ORPHANED INJECTED LINE" in _left,
             "note": ("BOTH LEGS: the paired block must still be cleanly "
                      "removed with the user's surrounding lines intact, or "
                      "the fix has bought safety by breaking the feature.")})

        # --- R7d: RESTORED is decided by the post-condition ----------------
        _pre = _wd / "preimage.bytes"; _pre.write_bytes(b"ORIGINAL\n")
        _good = _wd / "good.md"; _good.write_bytes(b"MODIFIED\n")
        _bad = _wd / "bad.md"; _bad.write_bytes(b"MODIFIED\n")
        _real_sha = _sha256_file(str(_pre))
        _ap2 = uninstall_execute({"actions": [
            {"path": str(_good), "action": "restore_preimage",
             "preimage_path": str(_pre), "preimage_sha256": _real_sha},
            {"path": str(_bad), "action": "restore_preimage",
             "preimage_path": str(_pre), "preimage_sha256": "0" * 64}]})
        _r2 = {d["path"]: d for d in _ap2["applied"]}
        add("R7d_RESTORED_is_decided_by_the_post_condition",
            (_r2[str(_good)]["result"] == "RESTORED"
             and _r2[str(_good)]["verified_against_preimage"] is True
             and _r2[str(_bad)]["result"] == "ERROR"
             and _r2[str(_bad)]["verified_against_preimage"] is False
             and _ap2["errors"] == 1), 3,
            {"matching_receipt": _r2[str(_good)]["result"],
             "mismatched_receipt": _r2[str(_bad)]["result"],
             "mismatch_reason": _r2[str(_bad)].get("reason"),
             "note": ("RESTORED used to be claimed from the absence of an "
                      "exception: sha256_after was recorded and never compared "
                      "to the receipt's pre-image sha, so the path that did "
                      "the write was also the path that reported success.")})

        # --- R7e: A SYMLINKED TARGET IS REFUSED, AND THE RED IS RUNNABLE ---
        # THE BLOCKER, EXECUTED END TO END THROUGH uninstall_plan +
        # uninstall_execute on a fixture that reproduces the reported
        # data-loss exactly: AGENTS.md -> REAL_AGENTS.md holding the user's own
        # file. BOTH HALVES RUN EVERY TIME. The RED half drives the PRESERVED
        # pre-fix write over an identical fixture and asserts that it STILL
        # destroys the user's bytes and STILL reports RESTORED — so the guard is
        # measured against a live failure, not against a memory of one.
        # C1: the plan's domain is now the LIVE ENUMERATORS' view for the home
        # the plan is FOR, so a fixture must plant its symlink at a path the verb
        # would really act on — otherwise this leg silently stops testing the
        # symlink defence and starts testing the domain gate instead.  The
        # symlink therefore lives at <fixture home>/.agents/AGENTS.md, which
        # voidloom_instructions.target_table(home) enumerates.
        _sym_home = _wd / "symlink_home"
        _link_dir = _sym_home / ".agents"
        _link_dir.mkdir(parents=True)
        _real = _link_dir / "REAL_AGENTS.md"
        _real.write_bytes(b"THE USER'S OWN AGENTS.md - never ours\n")
        _named = _link_dir / "AGENTS.md"
        os.symlink("REAL_AGENTS.md", _named)
        _pre2 = _link_dir / "ours.preimage"
        _pre2.write_bytes(b"OUR PRE-IMAGE\n")
        _pre2_sha = _sha256_file(str(_pre2))
        _ident = path_identity(str(_named))
        _act = {"path": str(_named), "action": "restore_preimage",
                "preimage_path": str(_pre2), "preimage_sha256": _pre2_sha,
                "resolved_path": _ident["resolved_path"],
                "is_symlink": _ident["is_symlink"],
                "resolved_differs_from_named": _ident["differs"],
                "blocked": refusal_reason(_ident)}
        _ap3 = uninstall_execute({"actions": [_act]})
        _real_after_green = _real.read_bytes()
        # the same shape with the guard bypassed — the plan entry's `blocked`
        # removed, so execute() must still refuse at the write.
        _ap3b = uninstall_execute({"actions": [{k: v for k, v in _act.items()
                                                if k != "blocked"}]})
        _real_after_green2 = _real.read_bytes()
        # THE RED, RUN.
        _red_dir = _wd / "symlink_case_RED"
        _red_dir.mkdir()
        _red_real = _red_dir / "REAL_AGENTS.md"
        _red_real.write_bytes(b"THE USER'S OWN AGENTS.md - never ours\n")
        _red_named = _red_dir / "AGENTS.md"
        os.symlink("REAL_AGENTS.md", _red_named)
        _red_pre = _red_dir / "ours.preimage"
        _red_pre.write_bytes(b"OUR PRE-IMAGE\n")
        _red = _unsafe_restore_following_the_symlink(str(_red_named),
                                                     str(_red_pre))
        _red_real_after = _red_real.read_bytes()
        # AND THE PLAN MUST NAME THE RESOLVED TARGET BEFORE ANY OF THIS.
        _rcp = _wd / "receipt_symlink.json"
        _rcp.write_text(json.dumps({"touched": [
            {"path": str(_named), "action": "restore_preimage",
             "preimage_path": str(_pre2), "preimage_sha256": _pre2_sha}]}),
            encoding="utf-8")
        with _fixture_world(str(_sym_home), str(_rcp)):
            _plan_sym = uninstall_plan(str(_wd), str(_sym_home))
        _plan_json = json.dumps(_plan_sym)
        add("R7e_a_symlinked_target_is_REFUSED_and_the_RED_still_destroys",
            (# GREEN: the shipped path refuses, twice, and the user's bytes stand
             _ap3["applied"][0]["result"] == "SKIPPED"
             and _ap3b["applied"][0]["result"] == "SKIPPED"
             and _ap3b["applied"][0].get("is_symlink") is True
             # WHICH DEFENCE FIRED, not merely that one did. Defence 2
             # (O_NOFOLLOW) also refuses this case, so without naming the layer
             # a mutant that deletes defence 1 looks equivalent.
             and _ap3b["applied"][0].get("refused_by")
                 == "pre_write_identity_check"
             and _ap3["restored"] == 0 and _ap3b["restored"] == 0
             and _real_after_green == b"THE USER'S OWN AGENTS.md - never ours\n"
             and _real_after_green2 == b"THE USER'S OWN AGENTS.md - never ours\n"
             # the PLAN blocks it and NAMES the resolved target it will not write
             and _plan_sym["blocked"] is True
             and _plan_sym["actions"][0]["is_symlink"] is True
             and _plan_sym["actions"][0]["resolved_path"] == os.path.realpath(
                 str(_real))
             and os.path.realpath(str(_real)) in _plan_json
             # RED: the pre-fix write still destroys the target and still lies
             and _red["result"] == "RESTORED"
             and _red["verified_against_preimage"] is True
             and _red_real_after == b"OUR PRE-IMAGE\n"), 8,
            {"shipped_result_with_plan_block": _ap3["applied"][0]["result"],
             "shipped_result_with_the_block_REMOVED":
                 _ap3b["applied"][0]["result"],
             "shipped_refusal_reason": _ap3b["applied"][0].get("reason"),
             "which_defence_refused": _ap3b["applied"][0].get("refused_by"),
             "user_file_after_the_shipped_run":
                 _real_after_green2.decode("utf-8"),
             "plan_blocked": _plan_sym["blocked"],
             "plan_lists_the_resolved_target":
                 os.path.realpath(str(_real)) in _plan_json,
             "plan_resolved_path": _plan_sym["actions"][0]["resolved_path"],
             "plan_blockers": _plan_sym["blockers"],
             "RED_prefix_write_result": _red["result"],
             "RED_prefix_write_claimed_verified":
                 _red["verified_against_preimage"],
             "RED_user_file_after": _red_real_after.decode("utf-8"),
             "note": ("EXECUTED through the shipped CLI in iteration 2: "
                      "--dry-run listed AGENTS.md, REAL_AGENTS.md appeared "
                      "NOWHERE in the plan, --yes reported RESTORED / "
                      "verified_against_preimage=True / errors=0, and the "
                      "user's file was gone. The RED leg above re-runs that "
                      "exact write every time this suite runs.")})

        # --- R7h: DEFENCE 2 IS A PLATFORM CAPABILITY, NOT AN ASSUMPTION ---
        # O_NOFOLLOW is 0 on a platform that lacks it, and `flags | 0` is a
        # silent no-op — the whole second layer would vanish with nothing
        # saying so. Assert that the constant is real AND that this kernel
        # actually honours it, then assert the wrapper converts the raw ELOOP
        # into the same refusal defence 1 raises, so a caller never has to
        # handle two vocabularies for one event.
        _nf = _wd / "nofollow"
        _nf.mkdir()
        _nf_t = _nf / "target.md"
        _nf_t.write_bytes(b"TARGET\n")
        _nf_l = _nf / "link.md"
        os.symlink("target.md", _nf_l)
        _eloop = None
        _converted = "the conversion did not raise"
        try:
            try:
                _fd = os.open(str(_nf_l), os.O_WRONLY | _NOFOLLOW)
                os.close(_fd)
            except OSError as e:
                _eloop = getattr(e, "errno", None)
                # DRIVE DEFENCE 2's OWN CONVERSION with a REAL ELOOP from a REAL
                # symlink. Defence 1 refuses first on every ordinary path, so
                # this layer's vocabulary would otherwise never be exercised.
                _eloop_to_refusal(e, str(_nf_l))
        except PathIsNotWhatWeNamed as e:
            _converted = getattr(e, "refused_by", None)
        _wrapped = None
        try:
            guarded_write_bytes(str(_nf_l), b"SHOULD NEVER LAND")
        except PathIsNotWhatWeNamed as e:
            _wrapped = getattr(e, "refused_by", None)
        add("R7h_O_NOFOLLOW_is_real_on_this_platform_and_speaks_one_language",
            (_NOFOLLOW != 0 and _eloop == errno.ELOOP
             and _converted == "O_NOFOLLOW_at_open"
             and _wrapped == "pre_write_identity_check"
             and _nf_t.read_bytes() == b"TARGET\n"), 4,
            {"O_NOFOLLOW_constant": _NOFOLLOW,
             "raw_open_errno": _eloop,
             "defence_2_conversion_refused_by": _converted,
             "errno_ELOOP": errno.ELOOP,
             "guarded_write_refused_by": _wrapped,
             "target_bytes_unchanged": _nf_t.read_bytes() == b"TARGET\n",
             "note": ("THREE THINGS: the constant is real, this kernel raises "
                      "ELOOP for it, and the conversion turns that ELOOP into "
                      "the SAME refusal defence 1 raises — so a caller never "
                      "has to handle two vocabularies for one event. Defence 1 "
                      "refuses first on the shipped path, which is why "
                      "guarded_write_refused_by reads "
                      "pre_write_identity_check.")})

        # --- R7f: THE POST-CONDITION CANNOT CONFIRM ITSELF ----------------
        # The independence property ON ITS OWN, driven both ways over one
        # fixture. A write that landed on a link's TARGET is confirmed by a
        # verifier that follows (os.stat) and CONTRADICTED by one that does not
        # (os.lstat). That difference is the whole repair, so it is asserted
        # rather than described.
        _idir = _wd / "independence"
        _idir.mkdir()
        _itarget = _idir / "target.md"
        _itarget.write_bytes(b"TARGET\n")
        _ilink = _idir / "link.md"
        os.symlink("target.md", _ilink)
        _ifd = os.open(str(_itarget), os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(_ifd, b"WRITTEN THROUGH THE TARGET\n")
            _iwstat = os.fstat(_ifd)
        finally:
            os.close(_ifd)
        _self_conf = _self_confirming_verify(str(_ilink), _iwstat)
        _indep = verify_write_landed(str(_ilink), _iwstat)
        _vpath = independent_verify_path(str(_ilink))
        # THE PROPERTY MUST BE OBSERVED WHERE IT CAN DIFFER. The fixture above
        # links the FINAL component, and independent_verify_path only departs
        # from the named path when the PREFIX re-resolves — so the conjunct
        # below compares two equal strings, and a mutant returning the named
        # path unchanged survives it (EXECUTED as NR7). This is the equal-size
        # fixture defect committed inside the check written to prove the R7
        # amendment: the same shape as R3b's "X"*100 segments, one layer up.
        _pdir = _idir / "realsub"
        _pdir.mkdir()
        (_pdir / "f.md").write_bytes(b"x")
        os.symlink("realsub", str(_idir / "linksub"))
        _named_prefix = str(_idir / "linksub" / "f.md")
        _indep_prefix_differs = (independent_verify_path(_named_prefix)
                                 != os.path.abspath(_named_prefix))
        add("R7f_the_post_condition_does_not_travel_the_writes_own_path",
            (_self_conf["same_object"] is True      # RED: agrees with itself
             and _indep["same_object"] is False     # GREEN: contradicts it
             and _indep["named_path_stat_followed_links"] is False
             and _vpath == str(_ilink.parent.resolve() / _ilink.name)
             and _indep_prefix_differs is True), 5,
            {"self_confirming_verifier_says_same_object":
                 _self_conf["same_object"],
             "independent_verifier_says_same_object": _indep["same_object"],
             "written_dev_ino": _indep["written_dev_ino"],
             "named_path_dev_ino": _indep["named_path_dev_ino"],
             "independent_read_back_path": _vpath,
             "independent_path_differs_when_the_prefix_relinks":
                 _indep_prefix_differs,
             "note": ("os.stat FOLLOWS and therefore reports the target's inode "
                      "on both sides — the pre-fix shape, and it agreed with the "
                      "write no matter where the write went. os.lstat does not "
                      "follow, so the object written and the object NAMED come "
                      "back different and the run refuses to claim RESTORED.")})

        # --- R7g: DELETED is decided by the post-condition ------------------
        # THIRD MEMBER OF THE D-9/D-10 CLASS, and the sweep did not reach it in
        # iteration 2: unlink(missing_ok=True) reported result=DELETED with
        # deleted=1 and errors=0 for a path that never existed. BOTH LEGS: a
        # real file must still be deleted, or the fix has removed the feature.
        _ddir = _wd / "deletes"
        _ddir.mkdir()
        _real_del = _ddir / "ours.md"
        _real_del.write_bytes(b"a file we installed\n")
        _never = _ddir / "NEVER_EXISTED.md"
        _dlink_t = _ddir / "user_file.md"
        _dlink_t.write_bytes(b"THE USER'S OWN FILE\n")
        _dlink = _ddir / "ours_link.md"
        os.symlink("user_file.md", _dlink)
        _ap4 = uninstall_execute({"actions": [
            {"path": str(_real_del), "action": "delete_file"},
            {"path": str(_never), "action": "delete_file"},
            {"path": str(_dlink), "action": "delete_file"}]})
        _d4 = {d["path"]: d for d in _ap4["applied"]}
        add("R7g_DELETED_is_decided_by_the_post_condition",
            (_d4[str(_real_del)]["result"] == "DELETED"
             and _d4[str(_real_del)]["existed_before"] is True
             and not _real_del.exists()
             and _d4[str(_never)]["result"] == "ALREADY_ABSENT"
             and _d4[str(_never)]["existed_before"] is False
             and _d4[str(_dlink)]["result"] == "SKIPPED"
             and _dlink.is_symlink()
             and _dlink_t.read_bytes() == b"THE USER'S OWN FILE\n"
             and _ap4["deleted"] == 1
             and _ap4["already_absent"] == 1), 6,
            {"existing_file": _d4[str(_real_del)]["result"],
             "path_that_never_existed": _d4[str(_never)]["result"],
             "path_that_is_now_a_symlink": _d4[str(_dlink)]["result"],
             "symlink_refusal_reason": _d4[str(_dlink)].get("reason"),
             "tally": {k: _ap4[k] for k in ("deleted", "already_absent",
                                            "skipped", "errors")},
             "note": ("an operator reading deleted=1 believes a file of ours "
                      "was removed. If the receipt names a stale or mistyped "
                      "path, the real file is still on disk and the report said "
                      "otherwise — with errors=0. ALREADY_ABSENT gets its own "
                      "counter so the two sentences never share a number.")})

        # --- R7k: THE PROPERTY, SWEPT OVER FIVE SHAPES THAT DEFEAT FIVE
        # DIFFERENT MECHANISMS -------------------------------------------
        # AMENDMENT 4 re-states R7 as a PROPERTY: the bytes land on the object
        # the plan identified, and on no other, verified BY IDENTITY.
        # Iteration 3 implemented three sound SYMLINK defences and was defeated
        # by four shapes that contain no symlink at all. All five below were
        # EXECUTED end-to-end against the iteration-3 file and all five
        # destroyed a user's file while reporting rc=0, restored=1, errors=0,
        # verified_against_preimage=True. Each one runs here EVERY time.
        _pd = _wd / "property"
        _pd.mkdir()
        _USER = b"THE USER'S OWN FILE - never ours\n"
        _OURS = b"OUR PRE-IMAGE\n"
        _pre_p = _pd / "ours.preimage"
        _pre_p.write_bytes(_OURS)
        _pre_p_sha = _sha256_file(str(_pre_p))
        _shapes: Dict[str, Any] = {}

        def _plan_then(dirname, build, disturb=None, action="restore_preimage",
                       expect_victim=None, use_home=False):
            """Build a fixture, PLAN it the way the CLI does, optionally disturb
            the filesystem between the plan and the write, then EXECUTE."""
            _sd = _pd / dirname
            _sd.mkdir()
            # C1: the plan's domain is the live enumerators' view FOR `home`
            # (passed as _sd below), so every shape must be planted at a path
            # the verb would really act on.  All eight builders name
            # "AGENTS.md" in the directory they are handed, so handing them
            # <home>/.agents puts the whole battery inside the domain in ONE
            # place — otherwise these legs would silently stop testing the
            # identity gate and start testing the domain gate.
            _shape_dir = _sd / ".agents"
            _shape_dir.mkdir()
            # `use_home` hands the builder the HOME itself, for the one shape
            # whose subject is a renamed PARENT directory: it must rename a
            # directory the verb really owns (<home>/.claude), not a synthetic
            # one, or the leg tests the domain gate instead of the prefix guard.
            _build_root = _sd if use_home else _shape_dir
            _named, _victim = build(_build_root)
            _rc_p = _sd / "receipt.json"
            _rc_p.write_text(json.dumps({"touched": [
                {"path": str(_named), "action": action,
                 "preimage_path": str(_pre_p),
                 "preimage_sha256": _pre_p_sha}]}), encoding="utf-8")
            with _fixture_world(str(_sd), str(_rc_p)):
                _pl = uninstall_plan(str(_sd), str(_sd))
            if disturb is not None:
                # the disturber must act in the SAME directory the builder used
                disturb(_build_root, _named)
            _ex = None if _pl["blocked"] else uninstall_execute(_pl)
            _row = (_ex["applied"][0] if _ex else None)
            try:
                _vb = _victim.read_bytes()
            except OSError:
                _vb = None
            return {"plan_blocked": _pl["blocked"],
                    "plan_blocked_code": _pl["actions"][0].get("blocked_code"),
                    "planned_identity": _pl["actions"][0].get("planned_identity"),
                    "result": (_row or {}).get("result"),
                    "refused_by": (_row or {}).get("refused_by"),
                    "restored": (_ex or {}).get("restored"),
                    "deleted": (_ex or {}).get("deleted"),
                    "victim_bytes": _vb,
                    # A DELETED VICTIM DID NOT SURVIVE, and reading it must not
                    # blow up the suite: shape 6 deletes the very file it is
                    # watching when the guard fails, so an unguarded read_bytes
                    # here would turn a FAILED ASSERTION into a CRASHED SUITE --
                    # and a crashed suite reports nothing at all.
                    "victim_survived": _vb == (
                        _USER if expect_victim is None else expect_victim)}

        # SHAPE 1 — HARDLINK: our object under a name the plan never listed.
        def _b1(sd):
            _real = sd / "REAL_AGENTS.md"
            _real.write_bytes(_USER)
            _nm = sd / "AGENTS.md"
            os.link(str(_real), str(_nm))
            return _nm, _real
        _shapes["1_hardlink"] = _plan_then("s1_hardlink", _b1)

        # SHAPE 2 — TOCTOU SWAP: a DIFFERENT object under the SAME name,
        # renamed into place between the plan and the write. No link exists at
        # any moment; realpath never differs; only (dev, ino) can see it.
        def _b2(sd):
            _nm = sd / "AGENTS.md"
            _nm.write_bytes(b"OUR INSTALLED FILE\n")
            return _nm, _nm

        def _d2(sd, named):
            _o = sd / "users_own.md"
            _o.write_bytes(_USER)
            os.rename(str(_o), str(named))
        _shapes["2_toctou_swap"] = _plan_then("s2_swap", _b2, _d2)

        # SHAPE 3 — RENAMED DIRECTORY IN THE PREFIX. os.path.realpath returns
        # the named path UNCHANGED: there is no symlink to resolve, before or
        # after. The amended-R7 realpath check is blind to this by construction.
        def _b3(home):
            # the target is a path the verb really owns, so the prefix being
            # renamed is the platform's own config dir (C1: in-domain fixture).
            _cd = home / ".claude"
            _cd.mkdir(exist_ok=True)
            _nm = _cd / "CLAUDE.md"
            _nm.write_bytes(b"OUR INSTALLED FILE\n")
            return _nm, _nm

        def _d3(home, named):
            # rename the PLATFORM'S OWN config dir away between plan and write,
            # then recreate it with the user's file at the same name.
            os.rename(str(home / ".claude"), str(home / ".claude_moved_away"))
            _new = home / ".claude"
            _new.mkdir()
            (_new / "CLAUDE.md").write_bytes(_USER)
        _shapes["3_renamed_prefix"] = _plan_then("s3_prefix", _b3, _d3, use_home=True)

        # SHAPE 4 — AN OBJECT PLANTED WHERE THE PLAN SAW NOTHING (plain file,
        # st_nlink=1, so the link-count limb CANNOT be what refuses it).
        def _b4(sd):
            _real = sd / "REAL_AGENTS.md"
            _real.write_bytes(_USER)
            return sd / "AGENTS.md", _real

        def _d4p(sd, named):
            Path(named).write_bytes(_USER)
        _shapes["4_planted_plain"] = _plan_then("s4_planted", _b4, _d4p)

        # SHAPE 5 — DELETE ONE NAME OF A MULTIPLY-NAMED OBJECT. The bytes
        # survive either way; the DEFECT is the CLAIM of removal.
        def _b5(sd):
            _real = sd / "USER_KEEPS_THIS.md"
            _real.write_bytes(_USER)
            _nm = sd / "AGENTS.md"      # C1: the planned path is a real target
            os.link(str(_real), str(_nm))
            return _nm, _real
        _shapes["5_unlink_one_of_n"] = _plan_then("s5_unlink", _b5,
                                                  action="delete_file")

        # SHAPE 6 — TOCTOU SWAP ON THE **DELETE** ACTION.  guarded_unlink was
        # called with a PATH AND NOTHING ELSE: the plan computed
        # planned_identity, recorded it at hooks:1348, and never passed it to
        # the delete side, so this action had no drift check of any kind.
        # EXECUTED against the pre-fix file: DELETED, deleted=1, errors=0, rc 0,
        # the user's file gone -- on tmpfs AND ext4, no inode trick, no symlink.
        def _b6(sd):
            _nm = sd / "AGENTS.md"      # C1: the planned path is a real target
            _nm.write_bytes(b"OUR INSTALLED FILE\n")
            return _nm, _nm

        def _d6(sd, named):
            _o = sd / "users_own.md"
            _o.write_bytes(_USER)
            os.rename(str(_o), str(named))
        _shapes["6_delete_swap"] = _plan_then("s6_delswap", _b6, _d6,
                                              action="delete_file")

        # SHAPE 7 — TOCTOU SWAP ON THE **MARKER-BLOCK** ACTION.  The read
        # threaded its own identity into its write-back -- the right instinct --
        # and neither half was ever compared to the PLAN, so the object edited
        # was whatever held the name.  EXECUTED against the pre-fix file:
        # MARKER_REMOVED, removed=1, errors=0, rc 0, and a user file's OWN
        # Voidloom block stripped out from between their own data.
        _USER_BLOCK = (_USER.decode() + MARKER_BEGIN + "\nIMPORTANT USER DATA\n"
                       + MARKER_END + "\nMORE USER DATA\n").encode()

        def _b7(sd):
            _nm = sd / "AGENTS.md"
            _nm.write_text("ours\n" + MARKER_BEGIN + "\nour block\n"
                           + MARKER_END + "\n", encoding="utf-8")
            return _nm, _nm

        def _d7(sd, named):
            _o = sd / "users_own.md"
            _o.write_bytes(_USER_BLOCK)
            os.rename(str(_o), str(named))
        _shapes["7_marker_swap"] = _plan_then("s7_markerswap", _b7, _d7,
                                              action="remove_marker_block",
                                              expect_victim=_USER_BLOCK)

        # SHAPE 8 — TOCTOU SWAP BY **UNLINK + CREATE**.  The shape four
        # iterations could not see, because every fixture ran on tmpfs and tmpfs
        # is the one local filesystem where it cannot happen.  On ext4 the freed
        # inode NUMBER comes straight back, so (dev, ino) is UNCHANGED across
        # the destruction of one object and the creation of another and every
        # identity comparison agrees.  EXECUTED against the pre-fix file on
        # ext4, 5 trials: RESTORED, verified_against_preimage=True, restored=1,
        # errors=0, rc 0, the user's file destroyed 5 of 5.
        def _b8(sd):
            _nm = sd / "AGENTS.md"
            _nm.write_bytes(b"OUR INSTALLED FILE\n")
            return _nm, _nm

        def _d8(sd, named):
            os.unlink(str(named))
            Path(named).write_bytes(_USER)
        _shapes["8_inode_reuse_swap"] = _plan_then("s8_inoreuse", _b8, _d8)

        # WHICH LAYER OWNS SHAPE 8 DEPENDS ON THE FILESYSTEM, AND THAT IS THE
        # POINT.  Where inode numbers are recycled, identity is BLIND and only
        # the content conjunct can refuse -- so on such a host this leg can only
        # pass if the content check is real.  Where they are not, identity sees
        # it and either refusal is correct.  DERIVED at the moment of asking.
        _recycles = fs_recycles_inode_numbers(str(_pd))
        _s8_layer = _shapes["8_inode_reuse_swap"]["refused_by"]
        _s8_owned_by_the_right_layer = (
            _s8_layer == "plan_content_drift" if _recycles
            else _s8_layer in ("plan_content_drift", "plan_identity_drift"))

        _all_survived = all(v["victim_survived"] for v in _shapes.values())
        _none_claimed = all((v["restored"] or 0) == 0 and (v["deleted"] or 0) == 0
                            for v in _shapes.values())
        add("R7k_the_write_lands_on_the_PLANNED_OBJECT_or_it_does_not_happen",
            (_all_survived and _none_claimed
             # each shape must be refused, and by the layer that OWNS it — a
             # single over-broad guard catching all five would look identical
             # here and would be one deletion away from catching none.
             and _shapes["1_hardlink"]["plan_blocked_code"] == "HAS_OTHER_NAMES"
             and _shapes["5_unlink_one_of_n"]["plan_blocked_code"]
                 == "HAS_OTHER_NAMES"
             and _shapes["2_toctou_swap"]["result"] == "SKIPPED"
             and _shapes["2_toctou_swap"]["refused_by"] == "plan_identity_drift"
             and _shapes["3_renamed_prefix"]["result"] == "SKIPPED"
             and _shapes["3_renamed_prefix"]["refused_by"] == "plan_identity_drift"
             and _shapes["4_planted_plain"]["result"] == "SKIPPED"
             and _shapes["4_planted_plain"]["refused_by"] == "plan_identity_drift"
             # THE DELETE AND MARKER SIDES OF THE SAME PROPERTY. Both were
             # unguarded; both are now refused by the layer that owns them.
             and _shapes["6_delete_swap"]["result"] == "SKIPPED"
             and _shapes["6_delete_swap"]["refused_by"] == "plan_identity_drift"
             and _shapes["7_marker_swap"]["result"] == "SKIPPED"
             and _shapes["7_marker_swap"]["refused_by"] == "plan_identity_drift"
             # and the shape tmpfs cannot express, refused by the conjunct that
             # is not blind to it on a filesystem where identity is.
             and _shapes["8_inode_reuse_swap"]["result"] == "SKIPPED"
             and _s8_owned_by_the_right_layer is True
             # ALL() OVER AN EMPTY COLLECTION IS TRUE. `_all_survived` and
             # `_none_claimed` are both all()-over-_shapes, and neither had a
             # non-empty assertion beside it: a refactor that emptied _shapes
             # would have left this check GREEN over nothing. Same defect as
             # M16, same sentence as I5's `_spawn["count"] > 0`.
             and len(_shapes) == 8
             # and the PLAN must carry an identity at all — a plan that records
             # only a path cannot refuse shapes 2, 3 or 4 no matter what the
             # write does.
             and isinstance(_shapes["2_toctou_swap"]["planned_identity"], dict)
             and _shapes["2_toctou_swap"]["planned_identity"]["ino"] is not None
             and _shapes["4_planted_plain"]["planned_identity"]["exists"] is False),
            len(_shapes) * 2,
            {"shapes": {k: {kk: vv for kk, vv in v.items()
                            if kk != "victim_bytes"}
                        for k, v in _shapes.items()},
             "every_victim_survived": _all_survived,
             "no_run_claimed_work_it_did_not_do": _none_claimed,
             "note": ("EXECUTED against the iteration-3 file, all five shapes "
                      "destroyed the user's file and reported rc=0, "
                      "restored=1/deleted=1, errors=0, "
                      "verified_against_preimage=True. Four of the five contain "
                      "NO SYMLINK: iteration 3's three defences were sound and "
                      "were aimed at a mechanism. NAME THE PROPERTY, NEVER THE "
                      "MECHANISM.")})

        # --- R7o: THE TARGET IS A REGULAR FILE, OR NOTHING HAPPENS --------
        # CLASS-NR. stat.S_ISREG was consulted NOWHERE in this file, and the
        # three shapes below went straight through the identity apparatus
        # because identity says WHICH OBJECT and never says WHAT KIND.
        #
        # The FIFO leg is the one that matters most and is the hardest to
        # assert: its failure mode is a HANG, and a hang is invisible to a check
        # that never runs -- it does not go red, it goes SILENT, which is why it
        # survived iterations 3 and 4 unnoticed. So the leg is run in a daemon
        # thread with a WALL-CLOCK BOUND, and "did not return" is a FAILURE with
        # a name rather than a suite that stops producing output.
        _nr: Dict[str, Any] = {}

        def _plan_only(sd_name, make):
            """PLAN a receipt naming an object of the wrong TYPE. Plan only --
            nothing is executed, because a plan that refuses is the whole
            answer and executing would be the defect."""
            _sd = _pd / sd_name
            _sd.mkdir()
            # C1: the domain gate fires BEFORE the file-type guard, so a
            # wrong-type object must be planted AT a path the verb really owns
            # for this leg to reach the guard it is testing.  An object that
            # cannot be created there at all (a chardev needs root) is handled
            # by `out_of_domain_ok` below: for those the DOMAIN refusal is the
            # stronger answer, and the type detector is asserted directly.
            _shape = _sd / ".agents"
            _shape.mkdir()
            _nm = make(_shape)
            _rp = _sd / "receipt.json"
            _rp.write_text(json.dumps({"touched": [
                {"path": str(_nm), "action": "restore_preimage",
                 "preimage_path": str(_pre_p),
                 "preimage_sha256": _pre_p_sha}]}), encoding="utf-8")
            with _fixture_world(str(_sd), str(_rp)):
                _pl = uninstall_plan(str(_sd), str(_sd))
            _acts = _pl["actions"]
            return {"blocked": _pl["blocked"],
                    "blocked_code": (_acts[0].get("blocked_code") if _acts
                                     else SCOPE_EXCEEDS_FLAG),
                    "refused_out_of_domain": _pl["out_of_domain_count"] > 0,
                    "file_type": path_identity(str(_nm)).get("file_type")}

        # LEG 1 — FIFO, UNDER A WALL-CLOCK BOUND.
        def _mkfifo(sd):
            _nm = sd / "AGENTS.md"
            os.mkfifo(str(_nm))
            return _nm
        _fifo_box: Dict[str, Any] = {}

        def _fifo_worker():
            try:
                _fifo_box["r"] = _plan_only("nr_fifo", _mkfifo)
            except BaseException as _e:            # noqa: BLE001
                _fifo_box["raised"] = f"{type(_e).__name__}: {_e}"
        _t0 = time.monotonic()
        _th = threading.Thread(target=_fifo_worker, daemon=True)
        _th.start()
        _th.join(timeout=NR_FIFO_TIMEOUT_S)
        _fifo_elapsed = time.monotonic() - _t0
        _fifo_returned = not _th.is_alive()
        _nr["fifo"] = {"returned": _fifo_returned,
                       "elapsed_s": round(_fifo_elapsed, 3),
                       "bound_s": NR_FIFO_TIMEOUT_S,
                       **(_fifo_box.get("r") or {}),
                       "raised": _fifo_box.get("raised")}

        # LEG 2 — a DIRECTORY. It was already refused, by HAS_OTHER_NAMES,
        # because every directory has st_nlink >= 2: the right outcome from a
        # reason that is false, and one `rmdir` of the link-count branch away
        # from no refusal at all.
        def _mkdir_t(sd):
            _nm = sd / "AGENTS.md"
            _nm.mkdir()
            (_nm / "USERS_STUFF").write_bytes(_USER)
            return _nm
        _nr["directory"] = _plan_only("nr_dir", _mkdir_t)

        # LEG 3 — a CHARACTER DEVICE. It survived only because ftruncate
        # happens to raise EINVAL on /dev/null; a device that accepted
        # ftruncate would have been written at.
        # A chardev cannot be created under a fixture home without root, so this
        # leg keeps /dev/null and asserts BOTH halves: the plan refuses it (now
        # at the domain gate, which never even considers it) and the type
        # detector still names it a chardev.
        _nr["chardev"] = _plan_only("nr_dev", lambda sd: Path("/dev/null"))

        add("R7o_the_target_is_a_REGULAR_FILE_or_nothing_happens",
            (_nr["fifo"]["returned"] is True
             and _nr["fifo"]["blocked"] is True
             and _nr["fifo"]["blocked_code"] == "NOT_A_REGULAR_FILE"
             and _nr["fifo"]["file_type"] == "FIFO"
             and _nr["directory"]["blocked"] is True
             # THE CODE, NOT MERELY THE REFUSAL. Pinning "blocked is True" here
             # would have been green before this fix too, on the false reason.
             and _nr["directory"]["blocked_code"] == "NOT_A_REGULAR_FILE"
             and _nr["directory"]["file_type"] == "directory"
             # C1: a chardev cannot be created under a fixture home without
             # root, so /dev/null is now refused EARLIER — at the domain gate,
             # which never considers it at all.  Both halves are asserted, and
             # together they are stronger than the old single clause: the plan
             # will not act on it, AND the type detector still names it.
             and _nr["chardev"]["refused_out_of_domain"] is True
             and _nr["chardev"]["blocked_code"] == SCOPE_EXCEEDS_FLAG
             and _nr["chardev"]["file_type"] == "character device"), 9,
            {"legs": _nr,
             "note": ("the FIFO leg asserts that the call RETURNS AT ALL. "
                      "EXECUTED against iterations 3 and 4, on tmpfs and on "
                      "ext4: the verb blocked forever in open() and was killed "
                      "at 20 s. It hung in THREE places, not the one that was "
                      "filed -- _sha256_file and probe_instruction_chain both "
                      "run inside uninstall_plan, BEFORE any write-side open, "
                      "so repairing the named site alone left the hang exactly "
                      "where it was."),
             "EVIDENCE_CLASS": "BEHAVIOURAL — the shipped plan path, wall-clocked"})

        # --- R7l: THE GATE LAYERS, DRIVEN DIRECTLY -------------------------
        # F-10's lesson generalised BEFORE it can be filed again: every layer
        # here is refused-before by the layer above it on ordinary paths, so
        # each one needs a call site of its own or its deletion is invisible.
        # These drive the identity comparison, the link-count limb and the
        # O_EXCL conversion as FUNCTIONS, the way R7h drives O_NOFOLLOW's.
        _gd = _pd / "gate"
        _gd.mkdir()
        _ga = _gd / "a.md"
        _ga.write_bytes(b"A\n")
        _gb = _gd / "b.md"
        _gb.write_bytes(b"B\n")
        _sa, _sb = os.lstat(str(_ga)), os.lstat(str(_gb))
        _id_a = {"dev": _sa.st_dev, "ino": _sa.st_ino, "link_count": 1,
                 "exists": True}
        _now_b = {"dev": _sb.st_dev, "ino": _sb.st_ino,
                  "exists_without_following": True}
        _now_a = {"dev": _sa.st_dev, "ino": _sa.st_ino,
                  "exists_without_following": True}
        _m_same = identity_matches(_id_a, _now_a)
        _m_diff = identity_matches(_id_a, _now_b)
        _m_planted = identity_matches({"dev": None, "ino": None,
                                       "link_count": None, "exists": False},
                                      _now_b)
        # the link-count limb of the gate, on a REAL descriptor of a REAL
        # hardlinked object — not on a synthesised stat.
        _gl = _gd / "linked.md"
        os.link(str(_ga), str(_gl))
        _gate_refused = None
        _gl_fd = os.open(str(_gl), os.O_WRONLY | _NOFOLLOW)
        try:
            identity_gate(_gl_fd, str(_gl), None, path_identity(str(_gl)))
        except PathIsNotWhatWeNamed as _e:
            _gate_refused = getattr(_e, "refused_by", None)
        finally:
            os.close(_gl_fd)
        # AND THE FILE IS STILL INTACT — the gate runs BEFORE the truncate.
        _gl_intact = _gl.read_bytes() == b"A\n"
        # O_EXCL's conversion, from a REAL EEXIST.
        _excl_conv = "the conversion did not raise"
        try:
            try:
                _xfd = os.open(str(_ga), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                               | _NOFOLLOW, 0o644)
                os.close(_xfd)
            except OSError as _e2:
                _excl_errno = getattr(_e2, "errno", None)
                _eexist_to_refusal(_e2, str(_ga))
        except PathIsNotWhatWeNamed as _e3:
            _excl_conv = getattr(_e3, "refused_by", None)
        # THE GATE'S OBJECT COMPARISON, ON A REAL DESCRIPTOR. Deleting this
        # branch left every suite green (EXECUTED as M4) because defence 1b
        # refuses the same shapes BEFORE the open on every path that can be
        # scheduled — the layer-hiding class again, one layer below where I
        # first fixed it. `expect` names a DIFFERENT real object, so the gate
        # must refuse the descriptor it is holding.
        # A DEDICATED, SINGLY-NAMED FILE. The first draft of this leg reused
        # _ga, which the link-count fixture above had already hardlinked, so
        # the link-count limb refused first and the object comparison was
        # layer-hidden INSIDE ITS OWN ASSERTION (caught by this suite going red
        # on the first run, which is the point of writing the leg at all).
        _gc = _gd / "c.md"
        _gc.write_bytes(b"C\n")
        _gate_obj_refused = None
        _gc_fd = os.open(str(_gc), os.O_WRONLY | _NOFOLLOW)
        try:
            _sb2 = os.lstat(str(_gb))
            identity_gate(_gc_fd, str(_gc),
                          {"dev": _sb2.st_dev, "ino": _sb2.st_ino,
                           "link_count": 1, "exists": True},
                          path_identity(str(_gc)))
        except PathIsNotWhatWeNamed as _e:
            _gate_obj_refused = getattr(_e, "refused_by", None)
        finally:
            os.close(_gc_fd)
        _ga_intact = _gc.read_bytes() == b"C\n"
        # THE GATE'S **CONTENT** COMPARISON, ON A REAL DESCRIPTOR. The same
        # layer-hiding class as M4, one conjunct further in, and MEASURED as a
        # SURVIVING MUTANT before this leg existed: reverting ONLY the gate's
        # content branch -- leaving defence 1c in place before the open -- left
        # all three suites PASS over 356 observations AND the 16-shape property
        # battery at 16/16. It is a race-only layer, because the pre-open check
        # refuses the same shape on every path that can be SCHEDULED. So it is
        # driven here, at the function boundary, or it is not driven at all.
        _gk = _gd / "k.md"
        _GK_BYTES = b"THE CONTENT THE PLAN RECORDED\n"
        _gk.write_bytes(_GK_BYTES)
        _gk_sha = _sha256_file(str(_gk))
        _gate_content_refused = None
        _gate_content_allowed = None
        _gk_fd = os.open(str(_gk), os.O_RDWR | _NOFOLLOW)
        try:
            # THE NEGATIVE CONTROL FIRST: the RIGHT hash must NOT refuse, or the
            # conjunct has removed the feature rather than defended it.
            identity_gate(_gk_fd, str(_gk), None, path_identity(str(_gk)),
                          expect_sha256=_gk_sha)
            _gate_content_allowed = True
        except PathIsNotWhatWeNamed:
            _gate_content_allowed = False
        try:
            # and a hash this object does NOT have must refuse -- decided on the
            # DESCRIPTOR by pread, never on the name.
            identity_gate(_gk_fd, str(_gk), None, path_identity(str(_gk)),
                          expect_sha256="0" * 64)
        except PathIsNotWhatWeNamed as _e:
            _gate_content_refused = getattr(_e, "refused_by", None)
        finally:
            os.close(_gk_fd)
        _gk_intact = _gk.read_bytes() == _GK_BYTES
        # THE GATE'S DEFINING PROPERTY -- that it stats the DESCRIPTOR and not a
        # NAME -- had no assertion at all: reverting os.fstat(fd) to
        # os.lstat(path) left all three suites green over 564 observations
        # (the reflector's RM7b, a SURVIVOR). The docstring says "not a name,
        # not a resolution, the object" and nothing bound it.
        # A leg only a descriptor can pass: with the fd open, UNLINK THE NAME.
        # fstat still describes the object we hold; lstat raises ENOENT.
        _gf = _gd / "gate_fd.md"
        _gf.write_bytes(b"x")
        _gfd = os.open(str(_gf), os.O_RDWR | _NOFOLLOW)
        os.unlink(str(_gf))          # the NAME is gone; the OBJECT is held
        try:
            _gs = identity_gate(_gfd, str(_gf), None,
                                {"exists_without_following": False})
            _gate_used_the_descriptor = (_gs.st_ino > 0)
        except (FileNotFoundError, PathIsNotWhatWeNamed):
            _gate_used_the_descriptor = False        # it stat'd the NAME
        finally:
            os.close(_gfd)
        add("R7l_each_identity_layer_has_a_call_site_of_its_own",
            (_gate_obj_refused == "identity_gate_at_open"
             and _ga_intact is True
             and _m_same["same_object"] is True
             and _m_diff["same_object"] is False
             and _m_planted["same_object"] is False
             and identity_matches(None, _now_a)["checked"] is False
             and _gate_refused == "identity_gate_link_count"
             and _gl_intact is True
             and _excl_errno == errno.EEXIST
             and _excl_conv == "O_EXCL_at_open"
             # THE CONTENT CONJUNCT DECIDES, AND ONLY ON A MISMATCH.
             and _gate_content_refused == "identity_gate_content_drift"
             and _gate_content_allowed is True
             and _gk_intact is True
             # A NAME-BASED STAT CANNOT PASS THIS; A DESCRIPTOR-BASED ONE
             # CANNOT FAIL IT.
             and _gate_used_the_descriptor is True), 14,
            {"gate_refused_a_descriptor_that_is_not_the_planned_object":
                 _gate_obj_refused,
             "gate_refused_a_descriptor_whose_CONTENT_is_not_the_plans":
                 _gate_content_refused,
             "gate_ALLOWED_the_descriptor_whose_content_matches":
                 _gate_content_allowed,
             "file_intact_after_the_content_refusal": _gk_intact,
             "gate_stats_the_DESCRIPTOR_not_the_name": _gate_used_the_descriptor,
             "file_intact_after_the_gate_refused": _ga_intact,
             "same_object_on_the_same_inode": _m_same["same_object"],
             "same_object_on_a_different_inode": _m_diff["same_object"],
             "planted_where_the_plan_saw_nothing": _m_planted["same_object"],
             "gate_refused_by": _gate_refused,
             "hardlinked_file_still_intact_after_the_refusal": _gl_intact,
             "o_excl_errno": _excl_errno,
             "o_excl_converted_to": _excl_conv,
             "note": ("ORDERING IS THE REPAIR, and this asserts it: the gate "
                      "fires on a descriptor opened WITHOUT O_TRUNC, so the "
                      "refusal leaves the bytes standing. A gate placed after "
                      "the truncate would refuse just as honestly over a file "
                      "it had already emptied.")})
        # --- R7m: THE THIRD-LAYER LIMBS DECIDE, THEY DO NOT ONLY REPORT ---
        # F-10's class, generalised. `if after == want and w["same_object"]` and
        # guarded_unlink's `differs` limb are both CONDITIONALLY EQUIVALENT:
        # defences 1 and 2 refuse first on every path anyone has constructed,
        # so deleting either limb leaves all three suites green (EXECUTED as
        # NR5 and NR6b) — and neither reviewer could prove non-equivalence.
        # THAT IS NOT A REASON TO LEAVE THEM UNDRIVEN. It is the same move R7h
        # made for O_NOFOLLOW's vocabulary: give the layer a call site of its
        # own, so its deletion is a missing assertion rather than a silent
        # collapse onto the layer above.
        _lay = _pd / "layers"
        _lay.mkdir()
        _lt = _lay / "t.md"
        _lt.write_bytes(b"T\n")
        _lo = _lay / "other.md"
        _lo.write_bytes(b"OTHER\n")
        _fake_stat = os.lstat(str(_lo))     # a REAL stat of a DIFFERENT object
        _decides = verify_write_landed(str(_lt), _fake_stat)
        _agrees = verify_write_landed(str(_lt), os.lstat(str(_lt)))
        # and guarded_unlink's prefix limb, on a path whose PREFIX re-resolves
        _ud = _lay / "realdir"
        _ud.mkdir()
        (_ud / "victim.md").write_bytes(b"OPERATOR FILE\n")
        os.symlink("realdir", str(_lay / "linkdir"))
        _unlink_refused = None
        try:
            guarded_unlink(str(_lay / "linkdir" / "victim.md"))
        except PathIsNotWhatWeNamed as _e:
            _unlink_refused = getattr(_e, "refused_by", "unknown")
        # AND THE LINK-COUNT LIMB OF THE DELETE, DIRECTLY. The PLAN blocks a
        # hardlinked delete_file before execution ever reaches guarded_unlink,
        # so removing this limb left every suite green (EXECUTED as M9) with
        # R7k still asserting the plan's block. Same class, third instance.
        _ul_keep = _lay / "user_keeps.md"
        _ul_keep.write_bytes(b"USER BYTES\n")
        _ul_ours = _lay / "ours_hardlinked.md"
        os.link(str(_ul_keep), str(_ul_ours))
        _unlink_nlink_refused = None
        try:
            guarded_unlink(str(_ul_ours))
        except PathIsNotWhatWeNamed as _e:
            _unlink_nlink_refused = getattr(_e, "refused_by", "unknown")
        _both_names_still_there = _ul_ours.exists() and _ul_keep.exists()
        # AND THE READ GUARD. ledger_append reaches guarded_read_text only via
        # callers whose WRITE refuses first, so dropping the read-side refusal
        # left every suite green (EXECUTED as M20). A read that follows a link
        # is not data loss, but it is how a caller comes to hold bytes it
        # believes came from the path it named.
        _rd = _lay / "readguard"
        _rd.mkdir()
        _rd_real = _rd / "operator.md"
        _rd_real.write_bytes(b"OPERATOR CONTENT\n")
        _rd_link = _rd / "named.md"
        os.symlink("operator.md", str(_rd_link))
        _read_refusals = {}
        for _tag, _path in (("final_component", str(_rd_link)),
                            ("directory_prefix",
                             str(_lay / "linkdir" / "victim.md"))):
            try:
                guarded_read_text(_path)
                _read_refusals[_tag] = None
            except PathIsNotWhatWeNamed as _e:
                _read_refusals[_tag] = getattr(_e, "refused_by", "unknown")
        # BOTH LEGS: an ordinary read must still work, or the guard has removed
        # the ability to read rather than protected it.
        _read_ok = guarded_read_text(str(_rd_real)) == "OPERATOR CONTENT\n"
        # THE **DEV** HALF OF THE IDENTITY TUPLE HAD NO OBSERVATION: reducing
        # the comparison to (ino,) left every suite green (the reflector's RM8,
        # a SURVIVOR, and still a survivor when this round began). A colliding
        # inode number on a DIFFERENT device is a different object. Driving it
        # through the filesystem needs a bind mount and therefore root, so the
        # limb is driven at the FUNCTION BOUNDARY or it is not driven at all --
        # which is exactly what R7m exists to do.
        _xdev = identity_matches({"dev": 1, "ino": 5, "exists": True},
                                 {"dev": 2, "ino": 5,
                                  "exists_without_following": True})
        add("R7m_the_identity_limbs_DECIDE_and_not_only_report",
            (_decides["same_object"] is False
             and _agrees["same_object"] is True
             and _unlink_refused == "pre_write_identity_check"
             and _unlink_nlink_refused == "pre_write_identity_check"
             and _both_names_still_there is True
             # The loop always ASSIGNS (None on no-refusal), so this is not
             # vacuous today; the count is pinned anyway, because the property
             # that saves it is a detail of the loop and not of this line.
             and len(_read_refusals) == 2
             and all(v == "pre_write_identity_check"
                     for v in _read_refusals.values())
             and _read_ok is True
             and _xdev["same_object"] is False
             and (_ud / "victim.md").read_bytes() == b"OPERATOR FILE\n"), 10,
            {"same_inode_on_a_DIFFERENT_DEVICE_is_a_different_object":
                 _xdev["same_object"],
             "different_object_reports_same_object": _decides["same_object"],
             "same_object_reports_same_object": _agrees["same_object"],
             "unlink_through_a_relinking_prefix_refused_by": _unlink_refused,
             "unlink_of_a_multiply_named_object_refused_by":
                 _unlink_nlink_refused,
             "both_names_survived_the_refusal": _both_names_still_there,
             "read_guard_refusals": _read_refusals,
             "an_ordinary_read_still_works": _read_ok,
             "operator_file_untouched":
                 (_ud / "victim.md").read_bytes() == b"OPERATOR FILE\n",
             "note": ("BOTH directions. A verifier pinned to False would pass "
                      "the first leg and fail the second, and a verifier pinned "
                      "to True the reverse — so neither constant can "
                      "impersonate the comparison.")})
    finally:
        shutil.rmtree(_wd, ignore_errors=True)

    # --- R7n: THE THREE LAYERS THAT ARE ONLY REACHABLE IN A RACE, ASSERTED
    # STRUCTURALLY — AND SAID TO BE STRUCTURAL --------------------------
    # THE BOUND, FIRST, BECAUSE IT IS THE POINT: this is an assertion about the
    # SOURCE, not about behaviour. It is strictly weaker than every other check
    # in this file, and it exists because three real defences are reachable ONLY
    # through a filesystem race between two adjacent statements — a race nobody
    # can schedule deterministically, and therefore a race no behavioural
    # fixture can drive. EXECUTED, all three: reverting each left every suite
    # green (M6, M10, M11), and each is a live defence I decline to ship
    # unasserted merely because its failure mode is hard to stage.
    #
    #   * O_TRUNC MUST NOT BE AT THE OPEN.  ORDERING IS THE WHOLE REPAIR: the
    #     gate must run on a descriptor whose file is still intact, or a refusal
    #     is issued over bytes that are already gone.  Behaviourally invisible
    #     today only because defences 1 and 1b refuse first on every schedulable
    #     shape — which is precisely the layer-hiding this round kept finding.
    #   * O_EXCL MUST BE SET when the plan saw nothing, so an object planted in
    #     the race fails the open instead of being overwritten.
    #   * THE MARKER-BLOCK WRITE MUST CARRY THE READ'S IDENTITY, so a
    #     read-modify-write cannot be joined to its own write by a path string.
    import ast as _ast
    _src_tree = _ast.parse(Path(__file__).read_text(encoding="utf-8"))
    _fns = {n.name: n for n in _ast.walk(_src_tree)
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
    _gwb = _fns.get("guarded_write_bytes")
    _gwb_src = _ast.dump(_gwb) if _gwb is not None else ""
    _has_trunc = "O_TRUNC" in _gwb_src
    # PRESENCE IS NOT REACHABILITY. The first draft of this leg asserted only
    # that the token O_EXCL appeared in the function's AST — and `if False and
    # saw_nothing: flags |= os.O_EXCL` leaves the token exactly where it was
    # (EXECUTED as M10, which survived this check's first version). So the
    # GUARD's shape is what is asserted: the exclusive-create is conditioned on
    # the plan having seen nothing, and on NOTHING ELSE.
    _excl_guard = None
    for _n in _ast.walk(_gwb) if _gwb else []:
        if not isinstance(_n, _ast.If):
            continue
        if "O_EXCL" not in _ast.dump(_n):
            continue
        _t = _n.test
        _excl_guard = (_t.id if isinstance(_t, _ast.Name)
                       else type(_t).__name__)
        break
    _has_excl = (_excl_guard == "saw_nothing")
    # the gate must be CALLED, and called before the truncate.
    _gate_line = next((n.lineno for n in _ast.walk(_gwb)
                       if isinstance(n, _ast.Call)
                       and isinstance(n.func, _ast.Name)
                       and n.func.id == "identity_gate"), None) if _gwb else None
    _trunc_line = next((n.lineno for n in _ast.walk(_gwb)
                        if isinstance(n, _ast.Call)
                        and _dotted(n.func) == "os.ftruncate"), None) if _gwb else None
    # every guarded_write_* call inside uninstall_execute must pass `expect`.
    _ue = _fns.get("uninstall_execute")
    _writes_in_ue = [n for n in _ast.walk(_ue)
                     if isinstance(n, _ast.Call)
                     and isinstance(n.func, _ast.Name)
                     and n.func.id in ("guarded_write_bytes",
                                       "guarded_write_text")] if _ue else []
    _writes_without_expect = [n.lineno for n in _writes_in_ue
                              if not any(k.arg == "expect" for k in n.keywords)]
    # AND EVERY GUARDED CALL IN uninstall_execute CARRIES THE PLAN'S CONTENT.
    # (dev, ino) equality is not object identity on any filesystem that recycles
    # inode numbers, so `expect` alone is a conjunct that agrees with a swap.
    # The plan has recorded current_sha256 since iteration 2 and NOTHING read it
    # back; this is what makes dropping the wiring visible.
    _guarded_in_ue = [n for n in _ast.walk(_ue)
                      if isinstance(n, _ast.Call)
                      and isinstance(n.func, _ast.Name)
                      and n.func.id in ("guarded_write_bytes",
                                        "guarded_write_text",
                                        "guarded_read_text",
                                        "guarded_unlink")] if _ue else []
    _guarded_without_sha = [n.lineno for n in _guarded_in_ue
                            if not any(k.arg == "expect_sha256"
                                       for k in n.keywords)]
    # and the GATE must receive it from guarded_write_bytes.
    _gate_calls = [n for n in _ast.walk(_gwb)
                   if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)
                   and n.func.id == "identity_gate"] if _gwb else []
    _gate_gets_sha = bool(_gate_calls) and all(
        any(k.arg == "expect_sha256" for k in n.keywords) for n in _gate_calls)
    # AND THE RESTORED DECISION MUST READ BOTH CONJUNCTS. Under iteration 4 the
    # identity gate makes `same_object` True whenever the write RETURNS, so
    # deleting the conjunct is unreachable-without-a-race — the reviewer's NR5,
    # which survived iteration 3 as "conditionally equivalent" and survives
    # iteration 4 for a STRONGER reason. It is still a live layer (an lstat
    # re-point between the gate and the post-condition would need it), so it is
    # bound here rather than deleted or reported as equivalent.
    _restore_tests = [_ast.dump(n.test) for n in _ast.walk(_ue)
                      if isinstance(n, _ast.If)
                      and "same_object" in _ast.dump(n.test)
                      and "sha256_after" not in _ast.dump(n.test)] if _ue else []
    _decision_reads_both = any(("Compare" in t and "same_object" in t)
                               for t in _restore_tests)
    add("R7n_the_race_only_layers_are_asserted_STRUCTURALLY_and_said_to_be",
        (_gwb is not None and _ue is not None
         and _has_trunc is False
         and _has_excl is True
         and _gate_line is not None and _trunc_line is not None
         and _gate_line < _trunc_line
         and len(_writes_in_ue) > 0
         and not _writes_without_expect
         # NON-EMPTY BEFORE MEMBERSHIP MEANS ANYTHING, on both new censuses.
         and len(_guarded_in_ue) > 0
         and not _guarded_without_sha
         and _gate_gets_sha is True
         and _decision_reads_both is True), 11,
        {"O_TRUNC_at_the_open": _has_trunc,
         "guarded_calls_in_uninstall_execute": len(_guarded_in_ue),
         "those_missing_an_expect_sha256_argument": _guarded_without_sha,
         "identity_gate_receives_the_plans_content": _gate_gets_sha,
         "O_EXCL_is_guarded_by": _excl_guard,
         "O_EXCL_reachable_under_saw_nothing": _has_excl,
         "RESTORED_decision_reads_the_identity_conjunct": _decision_reads_both,
         "identity_gate_called_at_line": _gate_line,
         "ftruncate_called_at_line": _trunc_line,
         "gate_precedes_the_truncate": (_gate_line or 0) < (_trunc_line or 0),
         "guarded_writes_in_uninstall_execute": len(_writes_in_ue),
         "those_missing_an_expect_argument": _writes_without_expect,
         "EVIDENCE_CLASS": "STRUCTURAL — parsed from this file's own AST",
         "why_not_behavioural": ("all three defences fire only when the "
                                 "filesystem changes between two adjacent "
                                 "statements. A behavioural fixture would have "
                                 "to win a race it cannot schedule, so a "
                                 "source-shaped assertion is the strongest "
                                 "honest form available and is labelled as the "
                                 "weaker evidence it is."),
         "note": ("EXECUTED: each of the three reverts left all three suites "
                  "PASS with failed=[] before this check existed.")})

    scan = scan_trust_bypass(shipped_files())
    # ASSERT the self-match guard, do not merely report it. If the needle ever
    # becomes a literal in a shipped file, this scanner starts matching itself —
    # the pgrep-self-match class — and its 0 stops meaning anything.
    add("R8_no_trust_bypass_in_shipped_code",
        (scan["code_hits"] == 0
         and scan["needle_assembled_at_runtime"] is True), scan["files_scanned"],
        {**scan,
         "note": ("R8 asks for ZERO hits over SHIPPED CODE. Prose hits are "
                  "reported, not hidden: they exist because the residue and the "
                  "verification notes must NAME the flag whose use invalidated an "
                  "earlier demo. The first version of this very check matched its "
                  "own needle — the pgrep self-match class — so the needle is now "
                  "assembled at run time and never appears as a literal.")})

    # --- R6h: THE LEDGER PATH IS AN OPERATOR PATH LIKE ANY OTHER ----------
    # CLASS-S swept into the proof channel: ledger_append wrote with
    # Path(...).write_text, which follows a link exactly the way the uninstaller
    # did. A ledger symlinked at a file the operator cares about would have been
    # overwritten with JSON, and nothing would have said so.
    _ld_dir = Path(fixture_dir("vl_sc_ledpath_"))
    try:
        # The victim is deliberately VALID JSON of the right shape, so the
        # malformed-ledger refusal does NOT fire and the symlink guard is the
        # thing actually under test. (A .md victim refuses one step earlier,
        # which is safe but tests a different sentence.)
        _victim = _ld_dir / "SOMETHING_ELSE.json"
        _victim_text = json.dumps({"schema_version": 1, "entries": [],
                                   "THIS_IS": "the operator's own file"})
        _victim.write_text(_victim_text, encoding="utf-8")
        _led_link = _ld_dir / "ledger.json"
        os.symlink("SOMETHING_ELSE.json", _led_link)
        _refused = None
        try:
            ledger_append(str(_led_link), {"platform": "codex", "gate": "G1",
                                           "verdict": "PROVEN",
                                           "observations": 1,
                                           "observed_at": utc_now()})
            _refused = False
        except PathIsNotWhatWeNamed as e:
            _refused = getattr(e, "refused_by", "unknown")
        # GREEN HALF: a normal path must still append.
        _led_ok = _ld_dir / "real_ledger.json"
        ledger_append(str(_led_ok), {"platform": "codex", "gate": "G1",
                                     "verdict": "PROVEN", "observations": 1,
                                     "observed_at": utc_now()})
        _rows_ok = len(ledger_load(str(_led_ok))["entries"])
        # BOTH FAULT SHAPES AT THE APPEND SITE. ledger_append refuses
        # `_malformed` AND `_error`, and only `_malformed` was ever driven:
        # refusing on `_malformed` alone left every suite green (EXECUTED as
        # NR9). Differential on a 62-byte truncated ledger holding two real
        # proof rows — SHIPPED raises ValueError and the file is untouched; the
        # mutant appends, the file becomes 182 B, and `entries` is the one new
        # row. The prior proof is gone and the ledger looks healthy.
        _append_faults: Dict[str, Any] = {}
        for _txt, _fault in (("{ this is not json", "_error"),
                             ('{"entries": 5}', "_malformed")):
            _lp2 = _ld_dir / f"append_{_fault}.json"
            _lp2.write_text(_txt, encoding="utf-8")
            _before = _lp2.read_bytes()
            try:
                ledger_append(str(_lp2), {"platform": "x", "verdict": "PROVEN"})
                _refused_f = False
            except ValueError:
                _refused_f = True
            _append_faults[_fault] = {
                "refused": _refused_f,
                "bytes_unchanged": _lp2.read_bytes() == _before,
                "fault_named_by_ledger_fault": ledger_fault(str(_lp2))}
        add("R6h_the_proof_ledger_is_never_written_through_a_symlink",
            (_refused in ("pre_write_identity_check", "O_NOFOLLOW_at_open")
             and _victim.read_text(encoding="utf-8") == _victim_text
             and _rows_ok == 1
             and all(v["refused"] and v["bytes_unchanged"]
                     for v in _append_faults.values())
             # the two faults must stay DISTINGUISHABLE at the point of refusal,
             # or "unreadable" has collapsed into one word again.
             and len({v["fault_named_by_ledger_fault"]
                      for v in _append_faults.values()}) == 2), 7,
            {"append_through_a_symlink_refused_by": _refused,
             "append_fault_shapes": _append_faults,
             "operator_file_untouched":
                 _victim.read_text(encoding="utf-8") == _victim_text,
             "rows_after_a_normal_append": _rows_ok,
             "note": ("the GREEN leg is the third one: a guard that also "
                      "refuses the ordinary append has removed the proof "
                      "channel rather than protected it.")})
    finally:
        shutil.rmtree(_ld_dir, ignore_errors=True)

    # --- R8c: A PROPERTY THIS ROUND CHANGED, AND WILL NOT LET DRIFT -------
    # Iteration 2 could claim "0 symlink code paths in all three files" and the
    # reviewer verified it independently. THIS ROUND BREAKS THAT CLAIM ON
    # PURPOSE: R7e, R7f, R7g, R6h and C13 each need a symlink to exist before
    # they can prove the guard refuses it, and a guard with no adversarial
    # fixture is a guard nobody has driven. So the claim is REPLACED rather than
    # quietly dropped: symlink CREATION may exist ONLY inside a self-check
    # fixture, never on any shipped code path, and that is asserted here instead
    # of being a grep a reader has to remember to run.
    _sym = [s for s in scan_mutating_io(shipped_files())["sites"]
            if s["call"] in ("os.symlink", "os.link", "symlink_to")]
    _sym_outside = [s for s in _sym if "self_check" not in
                    (s.get("enclosing_chain") or [])]
    add("R8c_symlink_CREATION_exists_only_inside_self_check_fixtures",
        (not _sym_outside and len(_sym) > 0), len(_sym),
        {"symlink_creating_sites": _sym,
         "sites_outside_a_self_check": _sym_outside,
         "sites_by_file": {f: sum(1 for s in _sym if s["file"] == f)
                           for f in sorted({s["file"] for s in _sym})},
         "note": ("iteration 2 claimed ZERO of these across all three files and "
                  "the reviewer confirmed it. That claim is now FALSE by "
                  "design, and this is the assertion that replaces it: the "
                  "fixtures that prove the symlink refusal must create "
                  "symlinks, and nothing else may. len(_sym) > 0 is part of "
                  "the assertion — if the fixtures ever stop creating them, "
                  "the refusal has stopped being driven.")})

    # --- I5: EVERY MUTATING FILESYSTEM CALL, SWEPT BY DEFINITION ----------
    # The class is "a write or delete on a path the caller named", not "the
    # three sites someone noticed". Enumerated by AST over the WHOLE shipped
    # set; each site must sit inside the guarded layer, a named fixture builder,
    # or the preserved RED — and every allow-list entry carries its reason.
    _io = scan_mutating_io(shipped_files())
    _spawn = scan_operator_spawns(shipped_files())
    _ro = scan_readonly_open_claims(shipped_files())
    add("I5_every_mutating_filesystem_call_is_inside_a_named_guard",
        (not _io["unguarded"] and not _io["parse_errors"]
         and _io["mutating_call_sites"] > 0
         # DERIVED, NOT TYPED: the population is shipped_files(), whatever its
         # size today (it was a typed 3 and went stale the day the set grew).
         and _io["files_scanned"] == len(shipped_files())
         and _io["files_scanned"] >= 3
         # THE EXCLUDED POPULATION IS ALSO BOUND. If an operator-spawn site
         # ever appears outside the canary's two runner functions, this census
         # has stopped covering what its name claims and must go red rather
         # than quietly widening its own blind spot.
         # `all()` OVER AN EMPTY LIST IS TRUE. The first draft of this conjunct
         # was exactly that and nothing else, so a census returning [] passed
         # it (EXECUTED as M16) — the vacuous-truth defect, committed by me,
         # inside the check written to bind an exclusion. The population must
         # be NON-EMPTY before its membership means anything; it is the same
         # sentence as R4b's `exercised > 0`.
         and _spawn["count"] > 0
         # codex_hooks_list spawns the PLATFORM'S OWN app-server for the
         # read-only hooks/list RPC: a FIXED command (`codex app-server`), not an
         # operator-supplied one; its stdin is our three JSON-RPC lines. Named
         # here as the third excluded site, for the same reason the runners are.
         # _reexec_under_recorded_interpreter (the session hook, iteration 2 of the tier1-upgrade loop) runs THE
         # INTERPRETER install.sh RECORDED in <brain>/INSTALL_RECORD -- absolute, executable, probed with a fixed
         # `-I -c pass` -- with a FIXED argv (the hook's own path + the platform's argv): a bounded command, not an
         # operator-supplied one. Named here, never widened by pattern.
         # verb_preflight (handoff round 1, D1; cli.py) EXECS the package's own install/preflight.py under the running
         # interpreter: a FIXED script beside the package, never a path the operator names; the operator's tokens are
         # its arguments only, and the preflight installs nothing into the home (it works in a scratch folder it
         # deletes). Named here, never widened by pattern.
         and all(x["enclosing"] in ("_run_hook", "_run_runner", "codex_hooks_list",
                                    "_reexec_under_recorded_interpreter", "verb_preflight")
                 for x in _spawn["sites"])
         # AND EVERY READ-ONLY EXEMPTION IS EARNED, NOT ASSERTED. Same
         # non-empty discipline as the line above: `not violations` over a
         # population that was never built is vacuously true, so the claimed
         # set must be non-empty AND every claimed function must actually have
         # been FOUND and parsed before its clean bill of health means anything.
         and len(_ro["claimed"]) > 0
         and _ro["found"] == _ro["claimed"]
         and not _ro["violations"]),
        _io["mutating_call_sites"],
        {**_io,
         "readonly_exemptions_are_checked_not_trusted": _ro,
         "excluded_by_definition": {
             "operator_spawns": _spawn["count"],
             "where": [f"{x['file']}:{x['enclosing']}:{x['line']}"
                       for x in _spawn["sites"]],
             "reason": ("an operator-supplied command; unbounded by "
                        "construction, and running it is the canary's purpose"),
             "derived": ("counted by scan_operator_spawns over the shipped set "
                         "at the moment of asking, never typed"),
             "codex_hooks_list": ("spawns `codex app-server` (fixed command, read-only "
                                  "hooks/list RPC), not an operator-supplied command")},
         "note": ("the finder's coverage is bounded by its suspicion; only the "
                  "sweep is bounded by the definition. A new write anywhere in "
                  "the three artifacts, in a function nobody added to the "
                  "allow-list, turns this red.")})

    # --- I5b: THE READ-ONLY EXEMPTIONS ARE EARNED, IN THEIR OWN CHECK ----
    # Split out of I5 rather than folded into it: I5's published observation
    # count is its census size, and adding a second population to the same
    # number made H2 go red -- correctly, and immediately. That is A-7's class
    # ("a published count whose population is narrower than its name") caught
    # by the guard built for it, so the answer is a check whose count IS its
    # own population, not a wider number with a footnote.
    add("I5b_every_READ_ONLY_exemption_from_the_census_is_earned",
        (len(_ro["claimed"]) > 0
         # NON-EMPTY BEFORE MEMBERSHIP MEANS ANYTHING. `not violations` over a
         # population that was never built is vacuously true -- the same
         # sentence as I5's `_spawn["count"] > 0`, and the same defect I shipped
         # once already as `all([])`.
         and _ro["found"] == _ro["claimed"]
         and not _ro["violations"]),
        len(_ro["found"]),
        {**_ro,
         "why": ("os.open is in the mutating population because O_CREAT and "
                 "O_TRUNC make it mutating. Three functions sit in the "
                 "allow-list on the grounds that their opens are read-only, and "
                 "an unbound grounds is an assertion of good intent. Each is "
                 "parsed and must reference no creating, truncating or writing "
                 "attribute of os at all."),
         "matched_how": ("AST Attribute nodes, never a substring of the dump. "
                         "The first version of this scanner matched 'write' in "
                         "ast.dump() and went red on all three, because one "
                         "docstring says 'a WRITER appears' and another says "
                         "'must WRITE back'."),
         "EVIDENCE_CLASS": "STRUCTURAL — parsed from the shipped set's AST"})

    # --- F1: EVERY FILESYSTEM FIXTURE IS ON THE DEPLOYMENT FILESYSTEM ---
    # THE DEFECT THIS CHECK EXISTS FOR IS NOT IN THE PRODUCT. It is in the
    # APPARATUS, and it hid a BLOCKER for four iterations: every filesystem
    # fixture in this suite called tempfile.mkdtemp() with no `dir=`, so every
    # one of them landed on the default temp directory, which on this host is
    # tmpfs. tmpfs does not recycle inode numbers. ext4 -- the filesystem under
    # $HOME, the one the operator's files are actually on -- does, 200/200 on
    # this host. So the shape that destroys a user's file was IMPOSSIBLE TO
    # EXPRESS in the only environment the suite ever ran in, and the round
    # published 5/5 HELD for a property that failed 5/5 where it mattered.
    #
    # A TEST WHOSE ENVIRONMENT DIFFERS FROM DEPLOYMENT IN EXACTLY THE PROPERTY
    # UNDER TEST IS NOT A TEST. This binds the environment, by AST, so the next
    # fixture someone writes cannot silently fall back.
    import ast as _a2
    _fx_root = fixture_root()
    _fx_dev = os.stat(_fx_root).st_dev
    _art_dev = os.stat(os.path.abspath(__file__)).st_dev
    _fx_recycles = fs_recycles_inode_numbers(_fx_root)
    # WHERE THE FIXTURES USED TO GO, measured WITHOUT creating anything.
    # The first version of this probe called tempfile.mkdtemp() with no `dir=`
    # in order to find out where a bare mkdtemp lands -- and F1 immediately went
    # red on it, correctly: the check caught its own probe. A census that needs
    # an exemption for itself is a census with a hole shaped like itself, so the
    # probe was rewritten to need none.
    _default_tmp_dev = None
    try:
        _default_tmp_dev = os.stat(tempfile.gettempdir()).st_dev
    except OSError:
        pass
    _mkdtemp_sites: List[int] = []
    _bare_mkdtemp: List[Any] = []
    _sibling_bare: Dict[str, int] = {}
    for _p in shipped_files():
        try:
            _t2 = _a2.parse(read_text_if_regular(_p))
        except (OSError, SyntaxError) as _e:
            _bare_mkdtemp.append(f"{Path(_p).name}: unparsable: {_e}")
            continue
        _bare_here = []
        for _n in _a2.walk(_t2):
            if (isinstance(_n, _a2.Call)
                    and _dotted(_n.func) == "tempfile.mkdtemp"):
                if not any(k.arg == "dir" for k in _n.keywords):
                    _bare_here.append(_n.lineno)
        if os.path.abspath(_p) == os.path.abspath(__file__):
            _mkdtemp_sites = [_n.lineno for _n in _a2.walk(_t2)
                              if isinstance(_n, _a2.Call)
                              and _dotted(_n.func) == "tempfile.mkdtemp"]
            _bare_mkdtemp.extend(_bare_here)
        elif _bare_here:
            # NAMED, NOT FIXED. The sibling artifacts passed this round and are
            # out of this follow-up's scope; the same class lives in them and
            # saying so is the honest form. A residue that is counted is a
            # ledger entry; a residue that is silent is a claim of cleanliness.
            _sibling_bare[Path(_p).name] = len(_bare_here)
    add("F1_every_filesystem_fixture_is_on_the_DEPLOYMENT_filesystem",
        (len(_mkdtemp_sites) > 0          # non-empty before membership counts
         and not _bare_mkdtemp
         # THE CLASS, CLOSED ACROSS THE SHIPPED SET (R10): the 8+1 bare sites
         # the siblings carried were a counted residue; now they are a FAIL.
         and not _sibling_bare
         # the fixtures live on the same filesystem as the shipped file, which
         # is the filesystem the operator's own data is on.
         and _fx_dev == _art_dev),
        len(_mkdtemp_sites) + 2,
        {"fixture_root": _fx_root,
         "fixture_root_dev": _fx_dev,
         "shipped_file_dev": _art_dev,
         "same_filesystem_as_the_shipped_file": _fx_dev == _art_dev,
         "default_tempdir_dev": _default_tmp_dev,
         "fixtures_would_have_landed_elsewhere":
             (_default_tmp_dev is not None and _default_tmp_dev != _fx_dev),
         "this_filesystem_recycles_inode_numbers": _fx_recycles,
         "mkdtemp_sites_in_this_file": _mkdtemp_sites,
         "sites_without_an_explicit_dir": _bare_mkdtemp,
         "SAME_CLASS_STILL_OPEN_IN_THE_SIBLINGS": _sibling_bare,
         "why_recycling_is_published": (
             "a run on a filesystem that does NOT recycle inode numbers cannot "
             "express the unlink-and-recreate swap at all. The flag is DERIVED "
             "at the moment of asking so a reader can tell a green that "
             "measured the property from a green that could not have."),
         "EVIDENCE_CLASS": "STRUCTURAL (AST) + BEHAVIOURAL (st_dev, measured)"})

    # --- X2: THE VERB, NOT THE FUNCTION. EVERY CLI SURFACE THROUGH main(),
    # ASSERTING THE ENVELOPE AND THE EXIT CODE ---------------------------
    # THE COVERAGE SHAPE THAT LET TEN MUTANTS LIVE. This suite tested
    # FUNCTIONS; users run VERBS. Before this check, main() was invoked from
    # exactly ONE site in 6,725 lines — R6g, for --status only — and
    # `exit_code_for` appeared ZERO times inside the emitter's selftest. Every
    # advertised exit code except --status's was asserted by nothing, and the
    # rc=3 that reproduce_blocker.sh prints as the GREEN half's headline was
    # proven by a SHELL SCRIPT and by no assertion. EXECUTED: turning the
    # blocked-uninstall `return 3` into `return 0` left all three suites PASS.
    _xd = Path(fixture_dir("vl_sc_cli_"))
    try:
        def _cli(argv):
            _b = io.StringIO()
            with contextlib.redirect_stdout(_b):
                _rc = main(list(argv))
            _out = _b.getvalue()
            try:
                _e = json.loads(_out)
            except ValueError:
                _e = {"status": None, "code": None}
            return {"rc": _rc, "status": _e.get("status"),
                    "code": _e.get("code"), "text_bytes": len(_out)}

        # A CLEAN LEDGER: measured zero, and a legal 0-of-0.
        _xl = _xd / "clean_ledger.json"
        _xl.write_text(json.dumps({"schema_version": 1, "entries": []}),
                       encoding="utf-8")
        # AN UNREADABLE ONE: could not measure. Different sentence, different rc.
        _xu = _xd / "broken_ledger.json"
        _xu.write_text("{ not json at all", encoding="utf-8")
        # A BLOCKED UNINSTALL: a receipt naming a target the plan must refuse.
        _xh = _xd / "home"
        _xh.mkdir()
        _xreal = _xh / "REAL_AGENTS.md"
        _xreal.write_bytes(b"THE USER'S OWN FILE\n")
        _xnamed = _xh / "AGENTS.md"
        os.link(str(_xreal), str(_xnamed))          # the PRODUCT defect's shape
        _xpre = _xh / "ours.preimage"
        _xpre.write_bytes(b"OUR PRE-IMAGE\n")
        _xrcp = _xh / "receipt.json"
        _xrcp.write_text(json.dumps({"touched": [
            {"path": str(_xnamed), "action": "restore_preimage",
             "preimage_path": str(_xpre),
             "preimage_sha256": _sha256_file(str(_xpre))}]}), encoding="utf-8")
        # AND A CLEAN ONE: a receipt whose single action is legal, so the
        # non-refusing branch of the same verb is driven too. A guard that
        # refuses EVERYTHING would pass every refusal leg above.
        _xok = _xd / "ok_home"
        _xok.mkdir()
        (_xok / ".agents").mkdir()          # C1: an in-domain target
        _xokf = _xok / ".agents" / "AGENTS.md"
        _xokf.write_bytes(b"OUR INSTALLED FILE\n")
        _xokp = _xok / "ours.preimage"
        _xokp.write_bytes(b"OUR PRE-IMAGE\n")
        _xokr = _xok / "receipt.json"
        _xokr.write_text(json.dumps({"touched": [
            {"path": str(_xokf), "action": "restore_preimage",
             "preimage_path": str(_xokp),
             "preimage_sha256": _sha256_file(str(_xokp))}]}), encoding="utf-8")

        # AN UNDO THAT DID NOT HAPPEN. Two shapes, because "we did not do it"
        # has two spellings and NEITHER reached the exit code: an action that
        # ERRORED, and an action that was SKIPPED. Both returned rc 0 with
        # status "ok" and code "uninstall_applied", so an operator scripting
        # `voidloom hooks --uninstall --yes && echo clean` printed "clean" over
        # a restore that did not happen -- R7's FAIL disposition, plainly.
        _xerr = _xd / "err_home"
        _xerr.mkdir()
        # C1: an in-domain target, or the run refuses before it can ERROR
        (_xerr / ".agents").mkdir()
        _xerrf = _xerr / ".agents" / "AGENTS.md"
        _xerrf.write_bytes(b"OUR INSTALLED FILE\n")
        _xerrp = _xerr / "ours.preimage"
        _xerrp.write_bytes(b"OUR PRE-IMAGE\n")
        _xerrr = _xerr / "receipt.json"
        _xerrr.write_text(json.dumps({"touched": [
            {"path": str(_xerrf), "action": "restore_preimage",
             "preimage_path": str(_xerrp),
             "preimage_sha256": _sha256_file(str(_xerrp))}]}), encoding="utf-8")
        os.chmod(str(_xerrf), 0o444)          # the open will be refused
        # ROOT DEFEATS A MODE BIT, so the leg says so instead of passing on a
        # permission that was never enforced. UNMEASURABLE is not a pass.
        _perm_is_enforceable = (os.geteuid() != 0)

        # SKIPPED, through an action the executor does not know. The plan does
        # not block it -- there is nothing wrong with the PATH -- so the skip
        # happens where it must, at execute time, and the tally is the only
        # thing that carries it.
        _xskip = _xd / "skip_home"
        _xskip.mkdir()
        (_xskip / ".agents").mkdir()        # C1: an in-domain target
        _xskipf = _xskip / ".agents" / "AGENTS.md"
        _xskipf.write_bytes(b"OUR INSTALLED FILE\n")
        _xskipr = _xskip / "receipt.json"
        _xskipr.write_text(json.dumps({"touched": [
            {"path": str(_xskipf), "action": "an_action_this_verb_never_had"}]}),
            encoding="utf-8")

        _prev = os.environ.get(RECEIPT_ENV)
        try:
            _verbs = {}
            _verbs["status_text"] = _cli(["--status", "--ledger", str(_xl)])
            _verbs["status_json"] = _cli(["--status", "--json",
                                          "--ledger", str(_xl)])
            _verbs["status_unreadable_text"] = _cli(["--status", "--ledger",
                                                     str(_xu)])
            _verbs["status_unreadable_json"] = _cli(["--status", "--json",
                                                     "--ledger", str(_xu)])
            os.environ[RECEIPT_ENV] = str(_xrcp)
            _verbs["uninstall_dryrun"] = _cli(["--uninstall", "--json",
                                               "--home", str(_xh),
                                               "--cwd", str(_xh)])
            _verbs["uninstall_yes_BLOCKED"] = _cli(["--uninstall", "--yes",
                                                    "--json", "--home", str(_xh),
                                                    "--cwd", str(_xh)])
            _verbs["uninstall_yes_BLOCKED_text"] = _cli(
                ["--uninstall", "--yes", "--home", str(_xh), "--cwd", str(_xh)])
            os.environ[RECEIPT_ENV] = str(_xokr)
            _verbs["uninstall_yes_CLEAN"] = _cli(["--uninstall", "--yes",
                                                  "--json", "--home", str(_xok),
                                                  "--cwd", str(_xok)])
            os.environ[RECEIPT_ENV] = str(_xerrr)
            _verbs["uninstall_yes_ERRORED"] = _cli(
                ["--uninstall", "--yes", "--json", "--home", str(_xerr),
                 "--cwd", str(_xerr)])
            _verbs["uninstall_yes_ERRORED_text"] = _cli(
                ["--uninstall", "--yes", "--home", str(_xerr),
                 "--cwd", str(_xerr)])
            os.environ[RECEIPT_ENV] = str(_xskipr)
            _verbs["uninstall_yes_SKIPPED"] = _cli(
                ["--uninstall", "--yes", "--json", "--home", str(_xskip),
                 "--cwd", str(_xskip)])
        finally:
            if _prev is None:
                os.environ.pop(RECEIPT_ENV, None)
            else:
                os.environ[RECEIPT_ENV] = _prev

        _user_file_intact = _xreal.read_bytes() == b"THE USER'S OWN FILE\n"
        add("X2_every_CLI_verb_answers_on_BOTH_channels_and_they_agree",
            (# a clean ledger is a measurement: ok / 0, in BOTH formats
             _verbs["status_text"]["rc"] == 0
             and _verbs["status_json"]["rc"] == 0
             and _verbs["status_json"]["status"] == "ok"
             # an unreadable one is a refusal: refused / 3, in BOTH formats.
             # "I cannot measure" and "I measured zero" never share an output,
             # and never share an exit code either.
             and _verbs["status_unreadable_text"]["rc"] == 3
             and _verbs["status_unreadable_json"]["rc"] == 3
             and _verbs["status_unreadable_json"]["status"] == "refused"
             and _verbs["status_unreadable_json"]["code"]
                 == "coverage_unknown_ledger_unreadable"
             # THE BLOCKER'S OWN GREEN, with an assertion behind it at last.
             and _verbs["uninstall_yes_BLOCKED"]["rc"] == 3
             and _verbs["uninstall_yes_BLOCKED_text"]["rc"] == 3
             and _verbs["uninstall_yes_BLOCKED"]["status"] == "refused"
             and _verbs["uninstall_yes_BLOCKED"]["code"] == "uninstall_blocked"
             and _user_file_intact
             # and the LEGAL run must still work, or the guard has removed the
             # verb rather than protected it.
             and _verbs["uninstall_yes_CLEAN"]["rc"] == 0
             and _verbs["uninstall_yes_CLEAN"]["status"] == "ok"
             and _xokf.read_bytes() == b"OUR PRE-IMAGE\n"
             # a dry run never writes and never refuses on a blocked plan: it
             # is the SHOW, and its exit code says the plan is not clean.
             and _verbs["uninstall_dryrun"]["rc"] == 3
             # AN UNDO THAT DID NOT HAPPEN IS NOT AN OK, ON EITHER CHANNEL.
             # EXECUTED before this fix: mode 0444 -> errors=1, rc 0, status
             # "ok"; /dev/null -> errors=1, rc 0, status "ok".
             and (not _perm_is_enforceable or (
                 _verbs["uninstall_yes_ERRORED"]["rc"] == 4
                 and _verbs["uninstall_yes_ERRORED_text"]["rc"] == 4
                 and _verbs["uninstall_yes_ERRORED"]["status"] == "incomplete"
                 and _verbs["uninstall_yes_ERRORED"]["code"]
                     == "uninstall_incomplete"
                 # and the file it could not restore is UNTOUCHED
                 and _xerrf.read_bytes() == b"OUR INSTALLED FILE\n"))
             # SKIPPED is the same defect wearing a safer word. Every GREEN
             # shape in this round's own property battery ends here: refused,
             # nothing restored, and -- until now -- rc 0 / status ok.
             and _verbs["uninstall_yes_SKIPPED"]["rc"] == 4
             and _verbs["uninstall_yes_SKIPPED"]["status"] == "incomplete"
             and _verbs["uninstall_yes_SKIPPED"]["code"] == "uninstall_incomplete"
             and _xskipf.read_bytes() == b"OUR INSTALLED FILE\n"),
            len(_verbs) + 4,
            {"per_verb": _verbs,
             "permission_bit_is_enforceable_here": _perm_is_enforceable,
             "note_on_root": ("mode 0444 does not stop root. When euid is 0 the "
                              "ERRORED leg is not asserted and says so, rather "
                              "than passing on a permission never enforced."),
             "user_hardlinked_file_intact_after_the_CLI_run": _user_file_intact,
             "legal_restore_still_applied":
                 _xokf.read_bytes() == b"OUR PRE-IMAGE\n",
             "note": ("a refusal is a property of the STATE, never of the "
                      "OUTPUT FORMAT. Both formats of both refusing verbs are "
                      "driven here, and so is the non-refusing branch of each "
                      "— a guard that refuses everything passes every refusal "
                      "leg and has removed the feature.")})
    finally:
        shutil.rmtree(_xd, ignore_errors=True)

    # --- H2: A PUBLISHED OBSERVATION COUNT IS THE EXERCISED COUNT ---------
    # The emitter's H1, brought to this file. H1 lived only in the emitter, so
    # the same substitution could be reintroduced here and in the canary with
    # nothing binding it. The population is the SHAPE of the pair, not one key
    # spelling: any check publishing an `exercised`-shaped number must publish
    # it AS its observation count.
    _pairs = []
    for c in checks:
        d = c.get("detail")
        if not isinstance(d, dict):
            continue
        k = next((k for k in d
                  if k.endswith("_that_CONTAINED_the_row")
                  or k in ("exercised", "exercised_observations",
                           "mutating_call_sites")), None)
        if k is not None:
            _pairs.append({"check": c["id"], "key": k,
                           "published_observations": c["observations"],
                           "exercised": d[k]})
    _bad_pairs = [p for p in _pairs if p["published_observations"] != p["exercised"]]
    add("H2_published_observation_counts_are_exercised_counts",
        (None if not _pairs else not _bad_pairs), len(_pairs),
        {"pairs": _pairs, "mismatched": _bad_pairs,
         "note": ("selected on the SHAPE of the pair, not on a literal key "
                  "name: a check that spells its exercised count differently is "
                  "still bound.")})

    if not _nested:
        # --- T1: THE SUITE IS CALENDAR-INDEPENDENT ------------------------
        # CLASS-T, swept over EVERY assertion in this file rather than over the
        # one that was caught. R6c went RED on unchanged code from 2026-10-09
        # because the survey rows got the same 30-day TTL the proof rows have.
        # This re-runs the WHOLE suite with the survey clock moved 400 days back
        # and 400 days forward and requires the same verdict both times. A check
        # that fails because a date passed is a check that gets disabled, and
        # every refusal guarantee in this file rests on the suite staying green.
        _t_saved = [p["verification"]["date"] for p in PLATFORMS]
        _t_saved_matrix = globals()["MATRIX_SURVEY_DATE"]
        _legs = {}
        try:
            for _label, _delta in (("survey_400d_in_the_past", -400),
                                   ("survey_400d_in_the_future", 400)):
                _d = (datetime.now(timezone.utc)
                      + timedelta(days=_delta)).strftime("%Y-%m-%d")
                for _p in PLATFORMS:
                    _p["verification"]["date"] = _d
                globals()["MATRIX_SURVEY_DATE"] = _d
                _r = self_check(_nested=True)
                _legs[_label] = {"verdict": _r["verdict"],
                                 "failed": _r["failed"],
                                 "observations": _r["observations"]}
        finally:
            for _p, _d0 in zip(PLATFORMS, _t_saved):
                _p["verification"]["date"] = _d0
            globals()["MATRIX_SURVEY_DATE"] = _t_saved_matrix
        add("T1_the_suite_is_calendar_independent",
            all(v["verdict"] == "PASS" and not v["failed"]
                for v in _legs.values()), len(_legs),
            {"legs": _legs,
             "survey_dates_restored":
                 [p["verification"]["date"] for p in PLATFORMS] == _t_saved,
             "matrix_survey_date_restored":
                 MATRIX_SURVEY_DATE == _t_saved_matrix,
             "note": ("EXECUTED as a RED before the fix: MATRIX_SURVEY_DATE "
                      "moved back 60 days, nothing else touched -> "
                      "verdict=FAIL, failed=['R6c_...'], clean_leg='REFUSED'. "
                      "This leg is the class, not the instance: it re-runs "
                      "EVERY assertion under a moved clock, so the next "
                      "calendar-dependent check is caught the day it is "
                      "written.")})

    asserts = [c for c in checks if c["kind"] == "assert"]
    obs = sum(c["observations"] for c in asserts)
    failed = [c["id"] for c in asserts if c["ok"] is False]
    verdict = ("UNMEASURABLE" if obs == 0 else ("FAIL" if failed else "PASS"))
    # LEAVE THE TREE AS WE FOUND IT. Every fixture removes its own directory;
    # this removes the ROOT that held them, and ONLY if it is empty -- an rmdir
    # that would need force is a residue report, not a cleanup.
    _fixture_root_left_behind = None
    try:
        os.rmdir(fixture_root())
    except OSError as _e:
        _fixture_root_left_behind = f"{type(_e).__name__}: {_e}"
    return {"verdict": verdict, "observations": obs, "checks": checks,
            "fixture_root_not_empty_after_the_run": _fixture_root_left_behind,
            "failed": failed, "nested": _nested}


# ===========================================================================
# CLI
# ===========================================================================
def envelope(status_s: str, code: str, data: Any) -> Dict[str, Any]:
    return {"status": status_s, "code": code, "data": data, "verb": "hooks"}


_ACTION_WORDS = {"install": "--install", "status": "--status",
                 "uninstall": "--uninstall", "self-check": "--self-check"}

# THE ENVELOPE'S TWO SIDECARS.  ``run()`` returns ONE envelope and never prints;
# the exit code the organ has always advertised (0 ok / 3 refused / 4 incomplete
# / 1 error) rides in ``_exit_code`` and the human rendering in ``_human``.  The
# dispatcher (voidloom/cli.py) pops both before printing, so the envelope flows
# ONCE through its _emit and the organ's exit code is preserved (R1).  ``main()``
# is the same contract for ``python3 organs/x.py``: pop, print, return.
EXIT_CODE_KEY = "_exit_code"
HUMAN_KEY = "_human"


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="voidloom hooks",
        description="the resolver's view of the injection install, the install, and the undo")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--install", action="store_true",
                   help="install the SessionStart hook into --platform's config "
                        "(dry-run unless --yes)")
    g.add_argument("--uninstall", action="store_true")
    g.add_argument("--self-check", action="store_true")
    ap.add_argument("--platform", default=None, choices=list(INSTALL_PLATFORMS),
                    help="install: which platform's config to write")
    ap.add_argument("--target", default=None,
                    help="install: override the target file (fixtures / non-default "
                         "homes; the default derives from CLAUDE_CONFIG_DIR / CODEX_HOME)")
    ap.add_argument("--dry-run", action="store_true",
                    help="install/uninstall: plan only (this is also the DEFAULT)")
    ap.add_argument("--yes", action="store_true",
                    help="install/uninstall: actually write. Without it, a dry run "
                         "no matter what.")
    ap.add_argument("--probe-platform", action="store_true",
                    help="status: CONSENT to spawn `codex app-server` for the live hooks/list "
                         "view (trustStatus, currentHash). The vendor writes its own state "
                         "under $CODEX_HOME when spawned; without this flag nothing under "
                         "your home changes and Codex trust is read from its persisted "
                         "record in config.toml.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--home", default=None)
    ap.add_argument("--ledger", default=None)
    return ap


def run(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Parse argv, do the verb, RETURN the envelope.  Never prints.

    Accepts the flag grammar (``--status``) and the word grammar
    (``status``, ``install --platform codex``) — the design brief's verbs are
    ``voidloom hooks install|status|uninstall``; the release criteria are written
    in flags.  Both reach the same code.
    """
    args_l = list(argv) if argv is not None else sys.argv[1:]
    if args_l and args_l[0] in _ACTION_WORDS:
        args_l[0] = _ACTION_WORDS[args_l[0]]
    ap = build_argparser()
    try:
        args = ap.parse_args(args_l)
    except SystemExit as e:                      # argparse error -> an envelope, not a trace
        env = envelope("error", "usage", {"message": "invalid arguments; see --help",
                                          "argv": args_l})
        env[EXIT_CODE_KEY] = int(getattr(e, "code", 2) or 2)
        env[HUMAN_KEY] = ap.format_usage()
        return env
    cwd = args.cwd or os.getcwd()

    if args.self_check:
        res = self_check()
        env = envelope({"PASS": "ok", "FAIL": "error",
                        "UNMEASURABLE": "refused"}[res["verdict"]],
                       "self_check_" + res["verdict"].lower(), res)
        env[EXIT_CODE_KEY] = {"ok": 0, "refused": 3}.get(env["status"], 1)
        env[HUMAN_KEY] = json.dumps(env, ensure_ascii=False, indent=2, default=str)
        return env

    if args.status:
        st = status(cwd, args.ledger, args.home, probe_platform=bool(args.probe_platform))
        # THE REFUSAL IS A PROPERTY OF THE STATE, NOT OF THE OUTPUT FORMAT (D-14).
        # EXPIRED rows are a normal operating state (status=ok); an UNREADABLE
        # ledger is the verb FAILING to measure (status=refused, rc 3).
        if st["coverage"].get("coverage_state") == "UNKNOWN":
            env = envelope("refused", "coverage_unknown_ledger_unreadable", st)
            env[EXIT_CODE_KEY] = 3
        else:
            env = envelope("ok", "hooks_status", st)
            env[EXIT_CODE_KEY] = 0
        env[HUMAN_KEY] = render_status(st)
        return env

    if args.install:
        defaulted_note = None
        if not args.platform:
            # #11 (stranger test, 2026-10-13): `voidloom hooks install` with no --platform
            # was a bare "needs --platform {claude,codex}". When exactly ONE platform's own
            # config dir exists (the platforms' resolver variables, _expand_platform_path),
            # that platform is the only one the hook could serve: use it, and say so. With
            # both or neither there is no sensible default -- the error then says exactly
            # what to type.
            chosen, found = default_install_platform()
            if chosen is None:
                lines = ["voidloom hooks install --platform %s%s" % (p, "   # " + found[p] if found.get(p) else "")
                         for p in INSTALL_PLATFORMS]
                why = ("both %s and %s exist" % (found["claude"], found["codex"]) if found.get("claude") and found.get("codex")
                       else "neither a Claude Code config dir nor a Codex home exists")
                msg = ("voidloom hooks install needs --platform: %s, so which one is yours is not "
                       "guessed. Type one of these (each prints the plan; add --yes to write it):\n  %s"
                       % (why, "\n  ".join(lines)))
                env = envelope("error", "platform_required",
                               {"message": msg, "valid": list(INSTALL_PLATFORMS), "found": found,
                                "commands": ["voidloom hooks install --platform %s" % p for p in INSTALL_PLATFORMS]})
                env[EXIT_CODE_KEY] = 1
                env[HUMAN_KEY] = "[error:platform_required] " + msg
                return env
            args.platform = chosen
            defaulted_note = ("platform: %s -- the only platform config found (%s); pass --platform to choose another."
                              % (chosen, found[chosen]))
        plan = install_plan(args.platform, cwd, args.home, args.target)
        if defaulted_note:
            plan["platform_defaulted"] = defaulted_note
        wet = bool(args.yes) and not args.dry_run
        if plan["blocked"]:
            env = envelope("refused", "install_refused", {**plan, "executed": False})
            env[EXIT_CODE_KEY] = 3
            env[HUMAN_KEY] = ((defaulted_note + "\n") if defaulted_note else "") + render_install(plan, wet=False)
            return env
        applied = install_execute(plan) if wet else None
        data = {**plan, "executed": bool(wet), "applied": applied}
        if applied is None:
            code = "install_plan" if plan["action"] != "already_installed" else "install_already_installed"
            env = envelope("ok", code, data)
            env[EXIT_CODE_KEY] = 0
        elif applied["result"] in ("INSTALLED", "ALREADY_INSTALLED"):
            env = envelope("ok", "install_" + applied["result"].lower(), data)
            env[EXIT_CODE_KEY] = 0
        elif applied["result"] == "REFUSED":
            env = envelope("refused", "install_refused", data)
            env[EXIT_CODE_KEY] = 3
        elif applied["result"] in ("ROLLED_BACK", "INSTALLED_UNRECEIPTED"):
            # AN INSTALL THAT DID NOT LAND CLEANLY IS NOT AN OK (rc 4, like an
            # incomplete uninstall): the operator scripting `&& echo done` must
            # not print "done".
            env = envelope("incomplete", "install_" + applied["result"].lower(), data)
            env[EXIT_CODE_KEY] = 4
        else:
            env = envelope("error", "install_error", data)
            env[EXIT_CODE_KEY] = 1
        env[HUMAN_KEY] = ((defaulted_note + "\n") if defaulted_note else "") + render_install(plan, wet=wet, applied=applied)
        return env

    # --- uninstall ---------------------------------------------------------
    # MECHANISMS ARE VERBS -- IN THE CODE, NOT ONLY THE DOCSTRING (R11).  This
    # call passed no mechanism, so `voidloom hooks uninstall --yes` executed
    # every `instructions` receipt entry too: EXECUTED (reflector, iter 1) --
    # after installing both mechanisms, ~/.agents/AGENTS.md was DELETED and the
    # instructions count went 3 -> 0 under the hooks verb's consent.  The undo
    # is scoped to the consent that created the write; `instructions uninstall`
    # passes its own MECHANISM the same way.  A dry run (the default) never
    # spawns the platform: the resolver view in the plan is FILE-DERIVED
    # (probe_platform=False), so the plan cannot cause a vendor write (R5).
    plan = uninstall_plan(cwd, args.home, mechanism=MECHANISM,
                          probe_platform=False, platform=args.platform)
    wet = bool(args.yes) and not args.dry_run
    if wet and plan["blocked"]:
        # REFUSE to write on a blocked plan. A partial restore that reports
        # success is the failure mode this criterion exists to prevent.
        env = envelope("refused", "uninstall_blocked", plan)
        env[EXIT_CODE_KEY] = 3
        env[HUMAN_KEY] = render_uninstall(plan, wet=False)
        return env
    applied = uninstall_execute(plan) if wet else None
    data = {**plan, "executed": bool(wet), "applied": applied}
    if applied is not None:
        # The receipt is the ledger of what is STILL installed; an undo that
        # landed leaves it, an undo that did not stays for the next run.
        data["receipt_update"] = receipt_mark_undone(plan["receipt_path"], applied)
    # AN UNDO THAT DID NOT HAPPEN IS NOT AN OK.  `errors` and `skipped` are both
    # "we did not do what the receipt says", and NEITHER reached the exit code:
    # EXECUTED twice through the shipped CLI -- target mode 0444 -> errors=1,
    # rc 0, status ok; /dev/null -> errors=1, rc 0, status ok.  An operator
    # scripting `voidloom hooks --uninstall --yes && echo clean` printed "clean"
    # over a restore that did not happen, which is R7's FAIL disposition in its
    # plainest form.  The SKIPPED case is the same defect wearing a safer word:
    # a refusal is the guard working, and it is still not an uninstall.
    # CRUCIBLE-3 D05-F5: `out_of_domain` is the THIRD bucket of the same kind and
    # it was not added when the other two were. An entry the plan itself marks
    # `retained: true` is residue by the plan's own word, and it reached neither
    # `errors`, nor `skipped`, nor the exit code -- so `--uninstall --yes && echo
    # clean` printed "clean" over a receipt whose every entry was refused. The
    # comment above is this module's own ruling; this line applies it to the
    # bucket it missed.
    _retained = int(plan.get("out_of_domain_count") or 0)
    if applied and (applied["errors"] or applied["skipped"] or _retained):
        data["retained_out_of_domain"] = _retained
        env = envelope("incomplete", "uninstall_incomplete", data)
        env[EXIT_CODE_KEY] = 4
        env[HUMAN_KEY] = render_uninstall(plan, wet=wet, applied=applied)
        return env
    st_s = "ok" if (not plan["blocked"]) else "refused"
    env = envelope(st_s, "uninstall_" + ("applied" if wet else "plan"), data)
    env[EXIT_CODE_KEY] = {"ok": 0, "refused": 3}[st_s]
    env[HUMAN_KEY] = render_uninstall(plan, wet=wet, applied=applied)
    return env


def emit(env: Dict[str, Any], as_json: bool) -> int:
    """Pop the sidecars, print ONCE, return the exit code."""
    rc = int(env.pop(EXIT_CODE_KEY, {"ok": 0, "refused": 3}.get(env.get("status"), 1)))
    human = env.pop(HUMAN_KEY, None)
    if as_json or human is None:
        print(json.dumps(env, ensure_ascii=False, indent=2, default=str))
    else:
        print(human)
    return rc


def main(argv: Optional[Sequence[str]] = None) -> int:
    args_l = list(argv) if argv is not None else sys.argv[1:]
    env = run(args_l)
    return emit(env, "--json" in args_l)


if __name__ == "__main__":
    sys.exit(main())

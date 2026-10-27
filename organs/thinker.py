#!/usr/bin/env python3
"""organs/thinker.py -- THE ONE SEAM between Voidloom's organs and whatever model thinks for them.

    from thinker import think, ThinkError
    res = think(prompt, purpose="memory_dreaming", tier="sonnet", timeout_s=120)
    res.text, res.backend_id, res.model, res.elapsed_s, res.mode

WHY (model-choice unit, Round 1; memories #21941 #21946 #21947 #21969-#21973 #22010-#22013)
    Eight organs each built their own `claude -p` argv, parsed their own vendor output and read their own token.
    The owner now chooses who thinks overnight -- a) Claude  b) Codex  c) Kimi  d) Gemini  e) Not now  f) Other (a
    local model, or another CLI) -- so an organ must never know which. It hands this module text, gets text back, and
    gets a TYPED failure when there is none. Nothing is assumed: the choice is the owner's answer, proven by execution in
    the timers' environment, then recorded in <brain>/THINKER_RECORD, which ONLY this module writes.

THE CONTRACT
    think(prompt, *, purpose, system=None, tier=None, timeout_s, expect_json=False) -> ThinkResult
      purpose   an organ's name from the catalog's purposes (thinkers.json). It selects the POSTURE:
                gap_research gets web search and fetch ONLY (the owner's ruling 2026-09-27, M3 in ruler VL-MC-v1.1:
                no file tools -- the baseline works with any model and no setup); every other purpose is TEXT
                ONLY. An unknown purpose is a programming error (ThinkMisuse, also a ValueError), never a default.
      system    a system prompt (arcane_manifest's council voices). Each adapter carries it its own way.
      tier      haiku | sonnet | opus -- the organ's per-job size. Mapped by the catalog (Claude's --model);
                a backend without a tier map ignores it and uses the recorded model.
      timeout_s REQUIRED: a hard wall clock. A dead Claude takes 181 s to fail on its own (Round 0 CN1).
      expect_json  the reply must carry a JSON object/array (fenced or bare) or ThinkMalformed is raised.
    Failures (all subclasses of ThinkError, each with .reason, .detail, .backend_id):
      ThinkUnavailable(reason in unconfigured|missing|auth|quota|unreachable)
      ThinkTimeout      the hard timeout fired (the process group was killed)
      ThinkMalformed    the backend answered but the answer is not usable (empty, not the transport's shape,
                        truncated input, no JSON when JSON was expected)
      ThinkRefused      the tool block did not hold: the call left a file in its empty working folder -- or the
                        reply is a refusal about the model itself or a tool call written out as text (reason
                        reply-refusal | reply-tool-narration: reply_screen, MC-R1-09). The output is DISCARDED, never
                        returned.
      ThinkToolBlocked  (a ThinkRefused, reason tool-attempt-blocked) the model attempted a tool call and the CLI ran
                        none -- Claude Code 2.1.287's "The model's tool call could not be parsed (retry also failed)."
                        (pass P). Never 'unavailable': the backend answered; a doctor never counts it as down.
      ThinkSkipped      research asked of a backend that has no web: a LOGGED SKIP, never invented research.

EVERY CALL (M4, instruction-free, measured by the doctrine canary)
    * a fresh, EMPTY working folder outside ~/.claude, deleted afterwards; any file in it after the call is the
      tripwire (ThinkRefused);
    * a fresh per-call HOME beside it, so no ~/.agents/AGENTS.md, ~/.claude/CLAUDE.md or vendor config is found by
      the child; Claude additionally runs --safe-mode with a per-call CLAUDE_CONFIG_DIR; Codex runs with the
      brain's own CODEX_HOME (no AGENTS.md there) and --disable hooks; Kimi runs with an EMPTY KIMI_CODE_HOME;
    * stdin is /dev/null (an open pipe hangs codex and `ollama run` forever: XH1, L05);
    * a hard timeout that kills the whole process group;
    * a MINIMAL environment built per backend from an allowlist -- the timers' env file puts
      CLAUDE_CODE_OAUTH_TOKEN into every organ process, and codex -s read-only printed a planted env nonce in
      Round 0; here Codex never receives the Claude token, and no API-key variable reaches any backend.

CREDENTIALS (M6): one resolver, one home per backend: <VOIDLOOM_ENV_DIR or ~/.voidloom>/thinker/ (0700)
      claude.env  CLAUDE_CODE_OAUTH_TOKEN=...  (0600)  -- `voidloom thinker login claude` or `thinker migrate`
      kimi.env    KIMI_MODEL_API_KEY=...       (0600)  -- `voidloom thinker login kimi`
      codex/      the brain's own CODEX_HOME (ChatGPT device login), calls serialized by codex/.thinker.lock
    Until claude.env exists, the LEGACY Claude sources still answer (the sidecar claude-oauth-token the organs read,
    then CLAUDE_CODE_OAUTH_TOKEN in the process environment), and every surface says they are legacy.
    No credential value is ever printed, logged or written anywhere but its own file; a fingerprint is sha256[:12].

THE VERBS (`voidloom thinker ...`, or `python3 organs/thinker.py ...`)
      status                      what thinks, since when, with which credential, and how the nights went
      set <claude|codex|kimi|gemini|local|off> [--model M] [--endpoint URL] [--api ollama|openai] [--command TEMPLATE]
                                  [--source installer|thinker-set|voidstead] [--pin SHA] [--answered-by WHO]
                                  [--env-file PATH] [--probe-env systemd|direct]
                                  prove in the TIMERS' environment (systemd-run --user with the units' env file),
                                  then record; a failed set leaves the previous record byte-identical
      login <claude|kimi|codex|gemini>  one human act: a hidden paste ASKED TWICE (Claude, Kimi), `codex login
                                  --device-auth` into the brain's CODEX_HOME, or agy's own Google sign-in into
                                  the brain's gemini-home inside a sandbox that hides the owner's ~/.gemini; then
                                  proven, with a fake-credential negative control first (Claude, Kimi, Gemini)
      probe [--backend B]         the proof through the seam (an ANSWER call -- 'What is N plus 1?' -- + a tool-block call); records nothing
      check                       the weekly credential check (the digest runs it): a probe, logged; an alert on
                                  the first auth failure and ~30 days before Claude's one-year token expires
      migrate                     claude.env from the legacy homes (env file line + sidecar); refuses to choose
                                  between two different tokens

STDLIB ONLY. Imports voidloom_env (the env-directory resolver) and least_privilege.failed_run_detail (the R8
failure detail), both stdlib-only organs laid beside this one.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import errno
import getpass
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

try:
    from . import voidloom_env  # type: ignore
except (ImportError, ValueError):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import voidloom_env  # type: ignore
try:
    from .least_privilege import failed_run_detail  # type: ignore
except (ImportError, ValueError):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from least_privilege import failed_run_detail  # type: ignore

__all__ = [
    "think", "ThinkResult", "ThinkError", "ThinkUnavailable", "ThinkTimeout", "ThinkMalformed",
    "ThinkRefused", "ThinkToolBlocked", "ThinkSkipped", "ThinkOverflow", "ThinkMisuse", "ThinkInternal", "OUTPUT_CAP_BYTES",
    "UNAVAILABLE_REASONS", "load_catalog", "CatalogError", "read_record",
    "RecordError", "resolve_credential", "thinker_home", "brain_dir", "record_path", "status_report",
    "health_line", "run", "main",
]

CATALOG_PATH = Path(__file__).resolve().with_name("thinkers.json")
RECORD_NAME = "THINKER_RECORD"
LOG_NAME = "THINKER_LOG"
LOG_CAP_BYTES = 512 * 1024
UNAVAILABLE_REASONS = ("unconfigured", "missing", "auth", "quota", "unreachable")
TIERS = ("haiku", "sonnet", "opus")
CLAUDE_TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
LEGACY_CLAUDE_SIDECAR = "claude-oauth-token"
CLAUDE_TOKEN_LIFE_DAYS = 365
EXPIRY_WARN_DAYS = 30
# The environment every CLI child may inherit -- an ALLOWLIST, so an API key or the Claude token can never ride
# along by accident (Round 0: the timers' env file hands CLAUDE_CODE_OAUTH_TOKEN to every organ process).
PASS_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "LOGNAME")
# Named here so a test can prove none of them reaches a backend it was not meant for.
SECRET_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
              "CODEX_ACCESS_TOKEN", "KIMI_MODEL_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY",
              # Gemini through agy (Round 1 L-G, 2026-10-16): an API key or ADC would bill or authenticate the call
              # OUTSIDE the brain's own Google sign-in -- none may ever reach it (the allowlist above already drops them)
              "GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "GOOGLE_APPLICATION_CREDENTIALS",
              "AGY_LLM_GATEWAY_API_KEY")
# The owner's answers `voidloom thinker set` takes -- every CLI backend id in the catalog plus local and off. A test
# binds this tuple to the catalog (test_thinker_gemini_catalog), so a new backend cannot be half-listed.
ANSWERS = ("claude", "codex", "kimi", "gemini", "local", "off")
LOGIN_BACKENDS = ("claude", "kimi", "codex", "gemini")
VARIADIC_FLAGS = ("--tools", "--allowedTools", "--disallowedTools", "--add-dir")
# THE OUTPUT CAP (Round 2 wave 1, MC-R1-12 -- host safety). The seam bounded a call's SECONDS but not the BYTES it held:
# a program that writes fast fills memory long before any timeout fires (the reflector's `yes {prompt}` probe through
# the seam coincided with a host out-of-memory, 2026-09-28; a 200 MB writer took the caller from 22 MB to 621 MB).
# Every place the seam reads a program or a server now reads INCREMENTALLY and holds at most this many bytes; past it
# the call is stopped and a ThinkOverflow names the cap. DERIVED from the largest outputs MEASURED in this unit
# (iterations/scratch_records/r2_w1/cap_derivation.json, 79 Round 0 calls): 120,121 bytes (a Claude stream-json
# RESEARCH call with tool results inlined; the organ path's json replies measured <= 7 KB); codex echoes the prompt on
# stderr, and one argv element is capped by Linux at 131,072 bytes; a research call that inlines ten 200 KB pages
# would be ~2 MB. 16 MiB is 139x the largest measured reply and 8x that pessimistic research transcript -- and small
# enough that a runaway costs the host nothing it would notice.
OUTPUT_CAP_BYTES = 16 * 1024 * 1024
HTTP_ERROR_BODY_BYTES = 64 * 1024        # an HTTP error's body: only its first 300 characters are ever shown
_READ_CHUNK = 64 * 1024
ANSWERED_BY = ("owner", "owner-flag", "voidstead", "test")
SOURCES = ("installer", "thinker-set", "voidstead")
PROBE_TOOL_ATTEMPTS_MAX = 5
# THE PROOF'S WALL CLOCK, as one number (Round 3 G1, the time half of the systemd-run path): the ANSWER call has the
# probe's timeout_s, and the TOOL step has timeout_s IN TOTAL however many attempts it makes -- so a whole proof is
# bounded by PROBE_CALL_BUDGETS * timeout_s plus process start-up. Before round 3 the transient unit's RuntimeMaxSec was
# timeout_s + 60 against a proof that could legitimately run 2 * timeout_s: an answer at 200 s and a tool step stalled to
# 240 s was killed by the manager mid-proof and reported as "no probe result" -- a cause nobody can act on.
PROBE_CALL_BUDGETS = 2
PROBE_UNIT_SLACK_S = 60          # python start-up + the sandbox + the manager, on top of the two budgets


# ---------------------------------------------------------------------------------------------------------------
# Typed outcomes
# ---------------------------------------------------------------------------------------------------------------
class ThinkResult(NamedTuple):
    text: str
    backend_id: str
    model: str
    elapsed_s: float
    mode: str               # the posture the call ran under: text | research


class ThinkError(Exception):
    """Base of every typed failure. str(e) is one line: '<backend> <reason>: <detail>'. Never a credential."""
    kind = "error"

    def __init__(self, reason: str, detail: str = "", backend_id: str = "", elapsed_s: float = 0.0):
        self.reason = reason
        self.detail = detail
        self.backend_id = backend_id
        self.elapsed_s = elapsed_s
        super().__init__("%s %s: %s" % (backend_id or "thinker", reason, detail))


class ThinkUnavailable(ThinkError):
    kind = "unavailable"

    def __init__(self, reason: str, detail: str = "", backend_id: str = "", elapsed_s: float = 0.0):
        if reason not in UNAVAILABLE_REASONS:
            raise ValueError("ThinkUnavailable reason must be one of %s, got %r" % (UNAVAILABLE_REASONS, reason))
        super().__init__(reason, detail, backend_id, elapsed_s)


class ThinkTimeout(ThinkError):
    kind = "timeout"


class ThinkMalformed(ThinkError):
    kind = "malformed"


class ThinkRefused(ThinkError):
    kind = "refused"


class ThinkToolBlocked(ThinkRefused):
    """The model ATTEMPTED a tool call in a call that offers none, and the CLI ran NONE (reason 'tool-attempt-blocked';
    kind 'refused', the ThinkRefused family). Pass P, 2026-10-01, MEASURED on Claude Code 2.1.287: with `--tools ""` a
    model asked to create a file makes the CLI end with "The model's tool call could not be parsed (retry also failed)."
    (rc 1; the binary's terminal reason malformed_tool_use_exhausted: the response stopped for a tool call, ZERO tool
    calls could be parsed, one retry, the same again). Before this type the seam read that exit as ThinkUnavailable
    ('unreachable') -- the backend DOWN -- which a doctor or the sentinel counts as an unreachable night, and which made
    the owner's landing proof refuse a valid token (memory #23605). It is not the backend being down: the backend
    answered, and what it answered was a tool call that could not run. Organs take their loud path, as for any refusal;
    the proof decides the tool block from the call's folders (probe_candidate), never from this type alone."""


class ThinkSkipped(ThinkError):
    kind = "skipped"


class ThinkOverflow(ThinkMalformed):
    """The backend wrote more than OUTPUT_CAP_BYTES (reason 'overflow'): not an answer. The call was stopped -- its
    process group killed, or the connection closed -- nothing more was read, and what was read is discarded
    (Round 2 wave 1, MC-R1-12)."""


class ThinkMisuse(ThinkError, ValueError):
    """The CALLER's arguments are wrong (an unknown purpose, a tier or timeout out of range, a prompt that is not
    text): a programming error. It is BOTH a ThinkError -- so an organ that catches only ThinkError takes its loud path
    instead of aborting the night -- and a ValueError, the contract this module always stated for it (Round 2 wave 1,
    MC-R1-11)."""
    kind = "misuse"


class ThinkInternal(ThinkError):
    """Anything the seam could not type more precisely -- an OS error preparing the call (no space for its working
    folder, an unreadable credential file), or an exception nobody foresaw. reason = the exception's type, detail =
    its message and the line it came from. The seam's boundary guarantees nothing leaves think() untyped (Round 2
    wave 1, MC-R1-11: at the base every organ caught `Exception` around its own launch; now every organ catches
    ThinkError alone, so an untyped error would abort the organ's run instead of taking its loud fallback)."""
    kind = "internal"


def _untyped(e: BaseException, bid: str) -> ThinkInternal:
    import traceback
    where = ""
    try:
        frames = traceback.extract_tb(e.__traceback__)
        own = [f for f in frames if Path(f.filename).name == "thinker.py"] or frames
        fr = own[-1]                        # the seam's own innermost line (else the innermost line)
        where = " at %s:%s in %s" % (Path(fr.filename).name, fr.lineno, fr.name)
    except (IndexError, TypeError, AttributeError):
        pass
    return ThinkInternal(type(e).__name__, "the seam met an error it could not type more precisely (%s: %s%s); the "
                         "organ takes its loud path" % (type(e).__name__, str(e)[:300], where), bid)


class CatalogError(Exception):
    pass


class RecordError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------------------------------------------
def brain_dir() -> Path:
    """The brain directory: the DB's directory when WIZARD_LIBRARY_DB names one, else the tree this organ was laid
    in (<brain>/organs/thinker.py -> <brain>). Never created here."""
    db = (os.environ.get("WIZARD_LIBRARY_DB") or "").strip()
    if db:
        return Path(db).expanduser().absolute().parent
    here = Path(__file__).resolve().parent
    return here.parent if here.name == "organs" else here


def record_path() -> Path:
    return brain_dir() / RECORD_NAME


def log_path() -> Path:
    return brain_dir() / LOG_NAME


def thinker_home() -> Path:
    """<VOIDLOOM_ENV_DIR or ~/.voidloom>/thinker -- the brain's private credential folder (#21993)."""
    return voidloom_env.preferred_dir() / "thinker"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _file_stamp(p: Path) -> Optional[Dict[str, Any]]:
    try:
        st = p.stat()
        return {"sha16": hashlib.sha256(p.read_bytes()).hexdigest()[:16], "size": st.st_size,
                "mtime": int(st.st_mtime)}
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------------------
# The catalog (M2): data, validated -- a field Round 0 measured may not be missing
# ---------------------------------------------------------------------------------------------------------------
REQUIRED = {
    "cli": ("letter", "label", "transport", "binary", "argv", "prompt_via", "stdin", "output", "posture", "web",
            "system", "tier", "model", "hook_free_env", "credential", "timeout_s", "tool_guarantee", "guide_part"),
    "http-ollama": ("letter", "label", "transport", "endpoint_default", "path", "output", "options", "truncate",
                    "input_check", "web", "credential", "timeout_s", "tool_guarantee", "guide_part"),
    "http-openai": ("letter", "label", "transport", "endpoint_default", "path", "output", "input_check", "web",
                    "credential", "timeout_s", "tool_guarantee", "guide_part"),
}
BANNED_FLAGS = ("--auto", "--yolo", "--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox",
                "--mode=accept-edits", "--mode=auto")
# A flag that acts without asking only with a VALUE: agy's `--mode accept-edits` (and `auto`) approve edits on their own
# (Round 1 L-G; agy 1.3.4 --help). Checked as an adjacent pair in every catalog flag list.
BANNED_FLAG_PAIRS = (("--mode", "accept-edits"), ("--mode", "auto"))


def _flag_lists(entry: Dict[str, Any]) -> List[List[str]]:
    out = [list(entry.get("argv") or [])]
    for v in (entry.get("posture") or {}).values():
        if isinstance(v, list):
            out.append(list(v))
    return out


def validate_catalog(cat: Dict[str, Any]) -> List[str]:
    """Every problem with a catalog, as sentences ([] = valid). load_catalog refuses on any."""
    bad: List[str] = []
    if not isinstance(cat, dict) or not isinstance(cat.get("backends"), dict) or not cat["backends"]:
        return ["the catalog has no backends"]
    purposes = cat.get("purposes")
    if not isinstance(purposes, dict) or not purposes.get("text") or not purposes.get("research"):
        bad.append("the catalog has no purposes.text / purposes.research lists")
    for bid, e in cat["backends"].items():
        if not isinstance(e, dict):
            bad.append("%s: not an object" % bid)
            continue
        tr = e.get("transport")
        if tr not in REQUIRED:
            bad.append("%s: transport %r is not one of %s" % (bid, tr, sorted(REQUIRED)))
            continue
        for f in REQUIRED[tr]:
            if f not in e:
                bad.append("%s: missing field %r (measured in Round 0, facts.json)" % (bid, f))
        if tr == "cli":
            if e.get("stdin") != "devnull":
                bad.append("%s: stdin must be devnull (an open pipe hangs codex/ollama run: XH1, L05)" % bid)
            post = e.get("posture") or {}
            if "text" not in post:
                bad.append("%s: no text posture" % bid)
            if e.get("web") and "research" not in post:
                bad.append("%s: web=true but no research posture" % bid)
            if not e.get("web") and "research" in post:
                bad.append("%s: web=false yet a research posture (research there is a logged skip)" % bid)
            pv = str(e.get("prompt_via", ""))
            if pv not in ("argv-last", "template") and not pv.startswith("flag:"):
                bad.append("%s: prompt_via %r unknown" % (bid, pv))
            for fl in _flag_lists(e):
                for b in BANNED_FLAGS + tuple(e.get("banned_flags") or ()):
                    if b in fl:
                        bad.append("%s: carries the banned flag %s (acts without asking)" % (bid, b))
                for f1, v1 in BANNED_FLAG_PAIRS + tuple(tuple(x) for x in (e.get("banned_flag_pairs") or ())):
                    if any(fl[i] == f1 and str(fl[i + 1]).lower() == v1 for i in range(len(fl) - 1)):
                        bad.append("%s: carries the banned flag %s %s (acts without asking)" % (bid, f1, v1))
                for i, f in enumerate(fl):
                    if f in VARIADIC_FLAGS:
                        if i + 2 >= len(fl) or not str(fl[i + 2]).startswith("-"):
                            bad.append("%s: variadic %s is not followed by its value and then a flag (it would "
                                       "swallow the prompt, #21971)" % (bid, f))
            out = e.get("output") or {}
            if out.get("kind") not in ("json", "stdout", "stream-json"):
                bad.append("%s: output.kind %r unknown" % (bid, out.get("kind")))
        else:
            if e.get("web"):
                bad.append("%s: an HTTP endpoint has no tools, so web must be false" % bid)
            if not (e.get("output") or {}).get("path"):
                bad.append("%s: output.path missing" % bid)
            if not (e.get("input_check") or {}).get("sent"):
                bad.append("%s: input_check.sent missing (silent truncation: Round 0 P10)" % bid)
            if tr == "http-ollama" and (e.get("truncate") is not False
                                        or not (e.get("options") or {}).get("num_ctx")):
                bad.append("%s: http-ollama needs truncate:false and options.num_ctx (default 4096 truncates "
                           "silently)" % bid)
            pb = e.get("probe_body", {})
            if not isinstance(pb, dict) or not all(isinstance(k, str) and k and not isinstance(v, (dict, list))
                                                   for k, v in pb.items()):
                bad.append("%s: probe_body must map dotted body paths to plain values (the proof's deterministic "
                           "decoding)" % bid)
        for key in ("tool_off_env",):
            te = e.get(key, {})
            if not isinstance(te, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in te.items()):
                bad.append("%s: %s must map variable names to text values" % (bid, key))
        pta = e.get("probe_tool_attempts", 1)
        if isinstance(pta, bool) or not isinstance(pta, int) or not 1 <= pta <= PROBE_TOOL_ATTEMPTS_MAX:
            bad.append("%s: probe_tool_attempts must be a whole number from 1 to %d (the proof's tool step, re-asked "
                       "only on a stall)" % (bid, PROBE_TOOL_ATTEMPTS_MAX))
        elif pta > 1 and tr != "cli":
            bad.append("%s: probe_tool_attempts applies to a CLI's tool step only (an HTTP server offers no tools; a "
                       "slow local model must keep the whole wall clock)" % bid)
        tab = e.get("tool_attempt_blocked", {})
        if not isinstance(tab, dict) or not all(isinstance(tab.get(k, []), list) and all(isinstance(x, str) and x
                                                for x in tab.get(k, [])) for k in ("result_lines", "terminal_reasons")):
            bad.append("%s: tool_attempt_blocked must hold result_lines / terminal_reasons lists of exact text" % bid)
        cred = e.get("credential") or {}
        if cred.get("kind") not in CREDENTIAL_KINDS:
            bad.append("%s: credential.kind %r unknown" % (bid, cred.get("kind")))
        if cred.get("kind") == "env-file" and not (cred.get("file") and cred.get("name")):
            bad.append("%s: credential file/name missing" % bid)
        if cred.get("kind") == "cli-home":
            bad += ["%s: %s" % (bid, m) for m in _cli_home_problems(cred)]
        hist = e.get("history")
        if hist is not None:
            kd = hist.get("keep_days") if isinstance(hist, dict) else None
            if (not isinstance(hist, dict) or not isinstance(hist.get("prune"), bool) or isinstance(kd, bool)
                    or not isinstance(kd, (int, float)) or kd < 1
                    or not all(isinstance(x, str) and x and not x.startswith("/") and ".." not in Path(x).parts
                               for x in hist.get("stores") or [""])):
                bad.append("%s: history needs prune (true/false), keep_days >= 1 and relative stores" % bid)
            elif (e.get("credential") or {}).get("kind") != "cli-home":
                bad.append("%s: history pruning belongs to a cli-home backend" % bid)
        plat = e.get("platforms")
        if plat is not None:
            if not isinstance(plat, dict) or not all(isinstance(v, str) and v for k, v in plat.items()
                                                     if not str(k).startswith("_")):
                bad.append("%s: platforms must map an operating system to 'supported' or its refusal text" % bid)
            elif plat.get("linux") is None:
                bad.append("%s: platforms names no linux line" % bid)
        if e.get("owner_login_stamp") not in (None, "stat"):
            bad.append("%s: owner_login_stamp %r unknown (stat)" % (bid, e.get("owner_login_stamp")))
        msp = e.get("model") or {}
        if msp.get("pin") is not None and not (isinstance(msp["pin"], str) and msp["pin"].strip()):
            bad.append("%s: model.pin must be a model name" % bid)
        if msp.get("check") not in (None, "required", "if-reported", "flag-enforced"):
            bad.append("%s: model.check %r unknown (required | if-reported | flag-enforced)" % (bid, msp.get("check")))
        if msp.get("check") == "flag-enforced" and not (msp.get("pin") and msp.get("list_argv") and msp.get("known_bad")
                                                        and msp.get("reject_said") and msp.get("basis")):
            bad.append("%s: a flag-enforced model needs pin, list_argv, known_bad, reject_said and basis" % bid)
        af = e.get("agent_file")
        if af is not None and not (isinstance(af, dict) and af.get("name") and af.get("at")):
            bad.append("%s: agent_file needs a name and an `at` path" % bid)
        if af is not None and isinstance(e.get("posture"), dict) and not all(
                isinstance(v, dict) for v in e["posture"].values()):
            bad.append("%s: an agent_file backend's postures are agent-file settings (objects)" % bid)
    return bad


CREDENTIAL_KINDS = ("env-file", "cli-home", "none")
# THE cli-home KIND (Round 1 L-G, ORIN's generalisation of the old codex-home): a vendor CLI that keeps its own sign-in in
# a folder chosen by ONE environment variable -- CODEX_HOME for codex, HOME itself for agy (Gemini). The brain owns that
# folder under <thinker_home>/<dir>; the sign-in is the vendor's own login run there (login_argv), never a value Voidloom
# reads or saves. Fields: dir, env, auth_file (relative to the folder: its presence is "signed in"), login_argv,
# status_argv (optional), config_files ({relative path: text that must be present}), lock (relative to thinker_home:
# one serialized stream per sign-in), plus the optional sandbox / sandbox_hide / login_negative_control /
# empty_dirs / forbid / must_be_empty / expiry_note.
CLI_HOME_REQUIRED = ("dir", "env", "auth_file", "login_argv", "config_files", "lock")


def _cli_home_problems(cred: Dict[str, Any]) -> List[str]:
    bad = ["credential.%s missing (kind cli-home)" % f for f in CLI_HOME_REQUIRED if f not in cred]
    if bad:
        return bad
    for f in ("dir", "env", "auth_file", "lock"):
        v = cred.get(f)
        if not isinstance(v, str) or not v.strip() or v.startswith("/") or ".." in Path(v).parts:
            bad.append("credential.%s must be a relative path or name, got %r" % (f, v))
    for f in ("login_argv", "login_browser_argv", "status_argv", "forbid", "must_be_empty", "empty_dirs",
              "sandbox_hide"):
        v = cred.get(f, [])
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            bad.append("credential.%s must be a list of text" % f)
    cf = cred.get("config_files")
    if not isinstance(cf, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in cf.items()):
        bad.append("credential.config_files must map relative paths to text")
    if cred.get("sandbox") not in (None, "bwrap"):
        bad.append("credential.sandbox %r unknown (bwrap)" % cred.get("sandbox"))
    for gone in ("login_sandbox", "login_hide", "login_env_only_ok"):
        if gone in cred:
            bad.append("credential.%s is retired (the sandbox covers EVERY call: `sandbox`, `sandbox_hide`)" % gone)
    return bad


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else CATALOG_PATH
    try:
        cat = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CatalogError("the thinker catalog %s is unreadable: %s" % (p, e))
    try:
        bad = validate_catalog(cat)
    except Exception as e:      # MC-R1-11: a catalog of the wrong SHAPE is refused, never an untyped error
        raise CatalogError("the thinker catalog %s is refused: its shape is not what the seam reads (%s: %s)"
                           % (p, type(e).__name__, e))
    if bad:
        raise CatalogError("the thinker catalog %s is refused: %s" % (p, "; ".join(bad)))
    return cat


def posture_for(cat: Dict[str, Any], purpose: str) -> str:
    if purpose in cat["purposes"]["research"]:
        return "research"
    if purpose in cat["purposes"]["text"]:
        return "text"
    raise ThinkMisuse("misuse", "unknown thinker purpose %r: name it in thinkers.json purposes (text or research) -- a "
                      "purpose never defaults to a posture" % (purpose,))


# ---------------------------------------------------------------------------------------------------------------
# THINKER_RECORD (M8): key=value, written ONLY here, only after a passing probe, torn writes refused on read
# ---------------------------------------------------------------------------------------------------------------
RECORD_HEADER = (
    "# <brain>/THINKER_RECORD -- who thinks overnight. Written ONLY by organs/thinker.py, and only after a probe",
    "# passed in the environment the timers use. An edited or torn record is REFUSED on read (record_sha256).",
    "# Change it with: voidloom thinker set <claude|codex|kimi|gemini|local|off>",
)
RECORD_KEYS = ("schema", "backend", "transport", "model", "binary", "endpoint", "command_template", "command_argv",
               "tool_class", "accepted_agentic_risk", "accepted_agentic_at", "base_url",
               "posture_text", "posture_research", "credential", "credential_fingerprint", "credential_source",
               "probe_nonce_ok", "probe_exact", "probe_tool_block", "probe_owner_untouched", "probe_elapsed_s",
               "probe_env", "proven_at", "answered_by", "source", "pin", "previous_backend",
               "program_version", "program_sha16")


def _render_record(fields: Dict[str, str]) -> bytes:
    lines = list(RECORD_HEADER)
    for k in RECORD_KEYS:
        v = fields.get(k, "")
        lines.append("%s=%s" % (k, str(v if v is not None else "").replace("\n", " ").replace("\r", " ")))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    return body + ("record_sha256=%s\n" % hashlib.sha256(body).hexdigest()).encode("utf-8")


def parse_record(data: bytes) -> Dict[str, str]:
    """The record's fields, or RecordError: the last line must be record_sha256 over every byte before it."""
    if not data.endswith(b"\n"):
        raise RecordError("THINKER_RECORD does not end with a newline: a torn write")
    body, _, last = data[:-1].rpartition(b"\n")
    body = body + b"\n"
    if not last.startswith(b"record_sha256="):
        raise RecordError("THINKER_RECORD has no record_sha256 line last: torn, or edited by hand")
    want = last[len(b"record_sha256="):].decode("ascii", "replace").strip()
    if hashlib.sha256(body).hexdigest() != want:
        raise RecordError("THINKER_RECORD's record_sha256 does not match its bytes: torn, or edited by hand -- "
                          "re-run `voidloom thinker set`")
    out: Dict[str, str] = {}
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:      # MC-R1-11: a sealed record that is not UTF-8 is refused, never an untyped error
        raise RecordError("THINKER_RECORD is not UTF-8 text (%s): edited by hand -- re-run `voidloom thinker set`" % e)
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition("=")
        if not sep:
            raise RecordError("THINKER_RECORD line without '=': %r" % line[:60])
        out[k] = v
    if out.get("schema") != "1" or "backend" not in out:
        raise RecordError("THINKER_RECORD schema %r unknown or no backend" % out.get("schema"))
    return out


def read_record(path: Optional[Path] = None) -> Optional[Dict[str, str]]:
    """None when there is no record (nobody has answered); RecordError when it is torn or edited."""
    p = Path(path) if path else record_path()
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise RecordError("THINKER_RECORD unreadable: %s" % e)
    return parse_record(data)


class _Proven(object):
    """The ONLY thing _commit_record accepts: a verdict object built by this module's probe (or by `set off`, whose
    answer needs no model). A record without a passing probe cannot be written."""
    __slots__ = ("passed", "candidate_sha", "facts")

    def __init__(self, passed: bool, candidate: Dict[str, str], facts: Dict[str, Any]):
        self.passed = bool(passed)
        self.candidate_sha = _candidate_sha(candidate)
        self.facts = facts


def _candidate_sha(c: Dict[str, str]) -> str:
    keys = ("backend", "transport", "model", "binary", "endpoint", "command_template", "command_argv", "tool_class",
            "accepted_agentic_risk", "accepted_agentic_at", "base_url")
    return hashlib.sha256(json.dumps({k: c.get(k, "") for k in keys}, sort_keys=True).encode()).hexdigest()


def _commit_record(fields: Dict[str, str], verdict: _Proven, path: Optional[Path] = None) -> Path:
    if not isinstance(verdict, _Proven) or not verdict.passed:
        raise RecordError("refused: THINKER_RECORD is written only after a passing probe")
    if verdict.candidate_sha != _candidate_sha(fields):
        raise RecordError("refused: the probe proved a different candidate than the one being recorded")
    p = Path(path) if path else record_path()
    if not p.parent.is_dir():
        raise RecordError("no brain directory at %s" % p.parent)
    data = _render_record(fields)
    tmp = p.with_name(".%s.tmp.%d" % (RECORD_NAME, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(p))
    try:
        dfd = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    back = p.read_bytes()
    if back != data or parse_record(back).get("backend") != fields.get("backend"):
        raise RecordError("THINKER_RECORD read-back differs from what was written")
    return p


# ---------------------------------------------------------------------------------------------------------------
# THINKER_LOG: one line per call outcome (never a prompt, a reply or a credential); the doctor's nights come here
# ---------------------------------------------------------------------------------------------------------------
def _log(**fields: Any) -> None:
    try:
        p = log_path()
        if not p.parent.is_dir():
            return
        line = " ".join("%s=%s" % (k, str(v).replace(" ", "_").replace("\n", "_")[:160])
                        for k, v in [("at", _now())] + sorted(fields.items()))
        with open(str(p), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if p.stat().st_size > LOG_CAP_BYTES:
            keep = p.read_bytes().splitlines(True)
            p.write_bytes(b"".join(keep[len(keep) // 2:]))
    except Exception:
        pass            # a log line must never break an organ


def read_log(path: Optional[Path] = None) -> List[Dict[str, str]]:
    p = Path(path) if path else log_path()
    rows = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            row = dict(tok.split("=", 1) for tok in line.split() if "=" in tok)
            if row.get("at"):
                rows.append(row)
    except OSError:
        pass
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Credentials (M6): one resolver
# ---------------------------------------------------------------------------------------------------------------
class Credential(NamedTuple):
    backend: str
    kind: str                  # env-file | cli-home | none
    env: Dict[str, str]        # what the child receives (never logged)
    source: str                # where it came from, a path or 'environment'
    fingerprint: str           # sha256[:12] of the value, or of auth.json for codex
    saved_at: str              # ISO when known, else ''
    legacy: bool


def read_env_lines(p: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(values, comment-facts) of a small KEY=VALUE credential file. '# saved_at=...' comments are facts."""
    vals, facts = {}, {}
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            k, sep, v = s.lstrip("#").strip().partition("=")
            if sep and v.strip():
                facts[k.strip()] = v.strip().split()[0]     # '# saved_at=<iso> by ...' -> the value alone
            continue
        k, sep, v = s.partition("=")
        if sep:
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals, facts


def _legacy_claude_sources(env_file: Optional[str] = None) -> List[Tuple[str, str]]:
    """[(source, token)] from the legacy Claude homes: the sidecar file the organs read, then the variable the timers'
    env file puts in the environment -- and, when THIS process carries no such variable, that env file's own
    CLAUDE_CODE_OAUTH_TOKEN line, read the way systemd reads it (install/env_file.py). The timers load that file, and so
    does the installer's proof (systemd-run, EnvironmentFile=), so the line IS a saved code: R3-07 (Round 3 reflection)
    -- the guide promised "a code is already saved; press Enter to prove it" for that line, the owner's Oct 1 install
    proved it without a paste, and the code read only the first two homes. Order = what the organs used before the seam.
    """
    out: List[Tuple[str, str]] = []
    try:
        side = voidloom_env.resolve_verbose(LEGACY_CLAUDE_SIDECAR)
        if side.exists and side.path.is_file():
            tok = side.path.read_text(encoding="utf-8").strip()
            if tok:
                out.append((str(side.path), tok))
    except (OSError, ValueError):
        pass
    env_tok = (os.environ.get(CLAUDE_TOKEN_KEY) or "").strip()
    if env_tok:
        out.append(("environment (%s)" % CLAUDE_TOKEN_KEY, env_tok))
    else:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
            import env_file as _ef  # type: ignore
            envf = env_file or default_env_file()
            if Path(envf).is_file():
                r = _ef.read_file(envf)
                got = None if r["error"] else r["effective"].get(CLAUDE_TOKEN_KEY)
                if got and got[0].strip():
                    out.append(("env file %s, line %d (%s=)" % (envf, got[1], CLAUDE_TOKEN_KEY), got[0].strip()))
        except Exception:
            pass
    return out


def _legacy_claude_fingerprints() -> List[str]:
    """Fingerprints of EVERY legacy Claude token: the sidecar, this process's variable, and the env file's line (read the
    way systemd reads it) -- so a status can say when the two homes disagree (the seam uses the sidecar first).
    (Round 1 iteration 2, MC-R1-06: with two DIFFERENT legacy tokens the seam silently used the sidecar and only
    `thinker migrate` noticed.)"""
    fps = set(_fp(t) for _s, t in _legacy_claude_sources())
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
        import env_file as _ef  # type: ignore
        envf = default_env_file()
        if Path(envf).is_file():
            r = _ef.read_file(envf)
            got = None if r["error"] else r["effective"].get(CLAUDE_TOKEN_KEY)
            if got and got[0].strip():
                fps.add(_fp(got[0].strip()))
    except Exception:
        pass
    return sorted(fps)


def resolve_credential(backend_id: str, cat: Optional[Dict[str, Any]] = None,
                       env_file: Optional[str] = None) -> Optional[Credential]:
    cat = cat or load_catalog()
    e = cat["backends"][backend_id]
    cred = e.get("credential") or {}
    kind = cred.get("kind")
    home = thinker_home()
    if kind == "none":
        return Credential(backend_id, "none", {}, "none needed", "", "", False)
    if kind == "cli-home":
        auth = home / cred["dir"] / cred["auth_file"]
        if not auth.is_file():
            return None
        st = _file_stamp(auth) or {}
        return Credential(backend_id, kind, {}, str(auth), str(st.get("sha16", ""))[:12],
                          _dt.datetime.fromtimestamp(st.get("mtime", 0), _dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ") if st else "", False)
    name, fname = cred["name"], cred["file"]
    p = home / fname
    if p.is_file():
        vals, facts = read_env_lines(p)
        v = vals.get(name, "")
        if v:
            return Credential(backend_id, kind, {name: v}, str(p), _fp(v), facts.get("saved_at", ""), False)
    if backend_id == "claude":
        for src, tok in _legacy_claude_sources(env_file):
            return Credential(backend_id, kind, {name: tok}, src, _fp(tok), "", True)
    return None


def cli_home_dir(entry: Dict[str, Any]) -> Optional[Path]:
    """The folder a cli-home backend's CLI keeps its sign-in in (<thinker_home>/<dir>), or None for other kinds."""
    cred = entry.get("credential") or {}
    return thinker_home() / cred["dir"] if cred.get("kind") == "cli-home" else None


def platform_name() -> str:
    """sys.platform as the catalog's `platforms` keys read it: linux* -> linux, darwin, win32, ..."""
    return "linux" if sys.platform.startswith("linux") else sys.platform


def platform_refusal(entry: Dict[str, Any]) -> str:
    """'' when this backend runs on this operating system, else the catalog's own refusal text (one place per OS: the
    owner's Mac ruling flips one line). A backend with no `platforms` runs everywhere it ran before."""
    plat = entry.get("platforms")
    if not isinstance(plat, dict):
        return ""
    v = plat.get(platform_name())
    if v == "supported":
        return ""
    return v or ("this backend is not offered on %s (the catalog names no line for it)" % platform_name())


# ---------------------------------------------------------------------------------------------------------------
# Failure classification (fixed substrings of what the backend SAID, never the model's answer)
# ---------------------------------------------------------------------------------------------------------------
_AUTH = ("401", "403", "unauthorized", "unauthorised", "invalid api key", "invalid_api_key", "api key appears",
         "authentication", "not logged in", "no credential", "expired", "revoked", "auth_error", "login required",
         "please run /login", "refresh_token", "invalid x-api-key", "oauth token",
         # agy / Google (Round 1 L-G; T1 measured the first three on agy 1.3.4)
         "authentication required", "authentication failed", "not logged into antigravity", "log back in",
         "permission_denied", "unauthenticated", "invalid_grant")
_QUOTA = ("429", "usage limit", "rate limit", "rate_limit", "quota", "limit reached", "insufficient_quota",
          "too many requests",
          "resource_exhausted", "resource exhausted", "spend cap", "exceeded your current quota")
_UNREACH = ("connection refused", "econnrefused", "connection error", "could not connect", "network",
            "name resolution", "getaddrinfo", "enotfound", "routing discovery failed", "unreachable", "502",
            "503", "504", "overloaded", "timed out", "timeout",
            "deadline_exceeded", "deadline exceeded", "connection reset", "service unavailable")


def classify(text: str) -> str:
    t = (text or "").lower()
    for reason, pats in (("auth", _AUTH), ("quota", _QUOTA), ("unreachable", _UNREACH)):
        if any(p in t for p in pats):
            return reason
    return "unreachable"


def _cli_said(entry: Dict[str, Any], out: str, err: str) -> Tuple[str, str]:
    """(first line, terminal reason) of what a CLI said when it ended: the JSON envelope's .result (its first line) and
    .terminal_reason when the transport is json and stdout is one; else the first non-empty line of stdout, then of
    stderr. Cut to 300 characters. Never a credential: it is what the CLI printed as its answer or error."""
    first, term = "", ""
    if (entry.get("output") or {}).get("kind") == "json":
        try:
            o = json.loads(out)
            if isinstance(o, dict):
                first = _json_said(entry, o)
                term = str(o.get("terminal_reason") or "")
        except ValueError:
            pass
    if not first:
        for stream in (out, err):
            lines = [l.strip() for l in (stream or "").splitlines() if l.strip()]
            if lines:
                first = lines[0]
                break
    return first.splitlines()[0][:300] if first else "", term[:80]


def _json_said(entry: Dict[str, Any], o: Dict[str, Any]) -> str:
    """What a JSON envelope SAID: its answer at output.path (Claude's .result), else -- when the answer is empty -- its
    error text when output.error_flag names a TEXT field (agy's .error; Claude's is_error is a boolean, never shown)."""
    spec = entry.get("output") or {}
    try:
        said = _json_at(o, spec.get("path") or "result")
    except (KeyError, IndexError, ValueError, TypeError):
        said = ""
    said = str(said or "").strip()
    if not said:
        ef = o.get(spec.get("error_flag") or "is_error")
        if isinstance(ef, str):
            said = ef.strip()
    return said


def _json_error(entry: Dict[str, Any], o: Dict[str, Any]) -> Optional[str]:
    """None when the envelope reports no error, else its error text ('' when it names none). An error is the
    error_flag field set (a true boolean or non-empty text) or the status_key's value in status_error."""
    spec = entry.get("output") or {}
    ef = o.get(spec.get("error_flag") or "is_error")
    st = o.get(spec["status_key"]) if spec.get("status_key") else None
    if ef or (st is not None and str(st) in (spec.get("status_error") or [])):
        return ef.strip() if isinstance(ef, str) else ""
    return None


def _json_model(entry: Dict[str, Any], o: Dict[str, Any]) -> str:
    """The model the envelope REPORTS (the first non-empty text at output.model_paths), '' when it names none."""
    for path in (entry.get("output") or {}).get("model_paths") or []:
        try:
            v = _json_at(o, path)
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _model_matches(reported: str, pinned: str) -> bool:
    """The reported model IS the pinned one: equal, or the pinned name with a variant suffix agy lists for it
    (gemini-3.8-flash-medium is gemini-3.8-flash at an effort). Case and a 'models/' prefix are ignored."""
    r = reported.lower().strip()
    r = r[len("models/"):] if r.startswith("models/") else r
    q = pinned.lower().strip()
    return bool(q) and (r == q or r.startswith(q + "-"))


def _tool_attempt_blocked(entry: Dict[str, Any], first_line: str, terminal_reason: str) -> bool:
    """True when the CLI's own words say the model attempted a tool call and none ran -- an EXACT match on the catalog's
    tool_attempt_blocked.result_lines (the first line, whole) or .terminal_reasons; never a substring, never a guess
    (pass P: Claude Code 2.1.287, memory #23605)."""
    spec = entry.get("tool_attempt_blocked") or {}
    return bool((first_line and first_line in (spec.get("result_lines") or []))
                or (terminal_reason and terminal_reason in (spec.get("terminal_reasons") or [])))


# ---------------------------------------------------------------------------------------------------------------
# The per-call sandbox: an empty cwd + a fresh HOME, outside ~/.claude, deleted after
# ---------------------------------------------------------------------------------------------------------------
def _real_home() -> Path:
    return Path(os.path.expanduser("~")).absolute()


def _under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _call_dir() -> Path:
    base = Path(tempfile.gettempdir())
    if _under(base, _real_home() / ".claude"):
        base = thinker_home() / "run"
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(str(base), 0o700)
    d = Path(tempfile.mkdtemp(prefix="voidloom-think-", dir=str(base)))
    if _under(d, _real_home() / ".claude"):
        shutil.rmtree(str(d), ignore_errors=True)
        raise ThinkUnavailable("unconfigured", "refused a working folder under ~/.claude (the ancestor CLAUDE.md "
                               "walk, #21972)")
    for sub in ("cwd", "home", "home/tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


_EVIDENCE_WALK_CAP = 20000      # entries walked in a call's HOME looking for the probe's file name (a CLI writes its own config there)
# a PERSISTENT cli-home (agy's HOME) keeps the CLI's own history between calls, and it GROWS (measured 2026-10-16: ~20
# entries and ~450 KB per call, recursively). Any entry count is a number a growing store must eventually cross -- and
# then every re-proof reads 'unmeasured' (r1 reflection F1). So its walk is bounded by WALL-CLOCK time instead: a home
# too big to search in this many seconds is the only way it reads incomplete (a 200k-entry walk is about a second).
_EVIDENCE_WALK_SECONDS_PERSISTENT = 30.0


def _sandbox_evidence(call: Path, watch: str, extra_homes: Tuple[Path, ...] = ()) -> Dict[str, Any]:
    """What the call's two folders hold, read BEFORE the sandbox is deleted (pass P, P1): the working folder's entries
    (it must be empty), and every path in the fresh HOME whose name is `watch` (the probe's file name; the CLI writes its
    own config into HOME, so HOME is searched for THAT name, not required to be empty). cwd_entries None = the folder
    could not be read: nothing is proven. home_complete False = the walk hit its cap or an unreadable folder.
    extra_homes (Round 1 L-G, ORIN): a cli-home CLI whose variable is HOME itself (agy) runs in the brain's PERSISTENT
    folder, not the fresh one -- that folder is searched for the same per-call random name too, and its hits are named
    with its own absolute path (the name is new each call, so a hit there is this call's)."""
    ev: Dict[str, Any] = {"checked_before_delete": True}
    try:
        ev["cwd_entries"] = sorted(x.name for x in (call / "cwd").iterdir())
    except OSError as e:
        ev["cwd_entries"], ev["cwd_error"] = None, "%s: %s" % (type(e).__name__, e)
    hits: List[str] = []
    seen, complete = 0, True
    home = call / "home"
    if watch:
        def _onerr(_e: OSError) -> None:
            nonlocal complete
            complete = False
        for base in (home,) + tuple(extra_homes):
            seen = 0
            persistent = base != home
            until = time.monotonic() + _EVIDENCE_WALK_SECONDS_PERSISTENT
            for root, dirs, files in os.walk(str(base), onerror=_onerr):
                seen += len(dirs) + len(files)
                for n in list(dirs) + list(files):
                    if n == watch:
                        hits.append(str(Path(root, n).relative_to(home)) if base == home else str(Path(root, n)))
                if (seen > _EVIDENCE_WALK_CAP) if not persistent else (time.monotonic() > until):
                    complete = False
                    break
    ev["home_hits"], ev["home_complete"], ev["home_watched"] = hits, complete, bool(watch)
    return ev


def _posture_in_force(entry: Dict[str, Any], posture: str, argv: List[str], env: Dict[str, str],
                      call: Path) -> Tuple[bool, str]:
    """Is THIS call running under its posture's tool switches -- read from the argv / agent file / env it actually got
    (pass P, P1: "no tool was offered" is evidence of the call, not a belief about the catalog)? For Claude Code 2.1.287
    that is `--tools ""` in the argv AND the advisor switched off (tool_off_env): with `--tools ""` alone the CLI still
    enables a server-side advisor tool (P4's debug logs: '[AdvisorTool] Server-side tool enabled')."""
    if entry.get("prompt_via") == "template":
        return True, "the recorded command template, re-checked at call time (check_template)"
    post = (entry.get("posture") or {}).get(posture)
    why: List[str] = []
    if isinstance(post, list):
        n = len(post)
        ok = (n == 0) or any(argv[i:i + n] == post for i in range(1, len(argv) - n + 1))
        why.append("the %s posture's %d argv flags %s" % (posture, n, "are in the argv" if ok else "are NOT in the argv"))
    elif isinstance(post, dict):
        want = "tools: [%s]" % ", ".join(post.get("tools") or [])
        af = _agent_file_path(entry, call)
        try:
            lines = af.read_text(encoding="utf-8").splitlines()
            ok = want in lines
            for k, v in post.items():          # agy: every other posture setting is a line of the file too
                if isinstance(v, bool) and "%s: %s" % (k, "true" if v else "false") not in lines:
                    ok = False
                    why.append("the agent file lacks %s: %s" % (k, "true" if v else "false"))
        except OSError:
            ok = False
        why.append("the agent file %s %r" % ("carries" if ok else "does NOT carry", want))
        name = (entry.get("agent_file") or {}).get("name")
        if name:
            named = any(argv[i:i + 2] == ["--agent", name] for i in range(len(argv) - 1))
            ok = ok and named
            why.append("the argv %s --agent %s" % ("names" if named else "does NOT name", name))
    else:
        return False, "no %s posture in the catalog" % posture
    if not isinstance(entry.get("tool_off_env"), dict):
        # F2 (pass P re-check, Round 3): the environment switches are read from the SAME catalog key that sets them, so a
        # catalog that lost the key relaxed both sides at once and the proof read 'held' with Claude Code 2.1.287's
        # server-side advisor still offered. Every CLI must DECLARE its environment switches -- {} when it needs none --
        # and a missing declaration proves nothing.
        return False, ("; ".join(why + ["the catalog does not declare this CLI's environment tool switches "
                                        "(tool_off_env): 'no tool offered' cannot be shown"]))
    for k, v in (entry.get("tool_off_env") or {}).items():
        if env.get(k) != v:
            ok = False
            why.append("%s is not %r in the call's environment" % (k, v))
        else:
            why.append("%s=%s" % (k, v))
    return ok, "; ".join(why)


def _agent_file_path(entry: Dict[str, Any], call: Path) -> Path:
    """Where a call's agent file is written: the catalog's agent_file.at (agy reads agents from its HOME's
    .gemini/config/agents/), else kimi's per-call <call>/agent.md (passed by --agent-file)."""
    af = entry.get("agent_file") or {}
    if af.get("at"):
        ch = cli_home_dir(entry)
        return Path(_expand(af["at"], {"cli_home": str(ch) if ch else "", "call": str(call)}))
    return call / "agent.md"


def _base_env(call: Path) -> Dict[str, str]:
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    h = call / "home"
    env.update(HOME=str(h), TMPDIR=str(h / "tmp"), XDG_CONFIG_HOME=str(h / ".config"),
               XDG_CACHE_HOME=str(h / ".cache"), XDG_DATA_HOME=str(h / ".local/share"),
               XDG_STATE_HOME=str(h / ".local/state"), TERM="dumb", NO_COLOR="1")
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _expand(s: str, ctx: Dict[str, str]) -> str:
    for k, v in ctx.items():
        s = s.replace("{%s}" % k, v)
    return s


def _safe_prompt(p: str) -> str:
    # a prompt beginning with '-' must never be read as a flag by the CLI's parser
    return (" " + p) if p.startswith("-") else p


# ---------------------------------------------------------------------------------------------------------------
# Adapters: one per transport
# ---------------------------------------------------------------------------------------------------------------
def _json_at(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj[part]
        else:
            raise KeyError(part)
    return obj


def _extract_cli(entry: Dict[str, Any], stdout: str) -> str:
    kind = entry["output"]["kind"]
    if kind == "stdout":
        return stdout                     # ONLY stdout: codex's stderr is the transcript (6 organs junk, 2 reject)
    if kind == "json":
        obj = json.loads(stdout)
        if not isinstance(obj, dict):
            raise ValueError("the JSON envelope is not an object")
        return str(_json_at(obj, entry["output"]["path"]))
    if kind == "stream-json":
        role, field = entry["output"].get("role", "assistant"), entry["output"].get("field", "content")
        parts = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("role") == role and isinstance(o.get(field), str):
                parts.append(o[field])
        if not parts:
            raise ValueError("no %s message in the stream" % role)
        return "".join(parts)
    raise ValueError("unknown output kind %r" % kind)


def _kimi_agent_file(path: Path, posture: Dict[str, Any], system: Optional[str]) -> None:
    tools = posture.get("tools") or []
    lines = ["---", "name: voidloom-thinker", "description: Voidloom overnight thinker (unattended).",
             "tools: [%s]" % ", ".join(tools)]
    if posture.get("disallowedTools"):
        lines.append("disallowedTools: [%s]" % ", ".join(posture["disallowedTools"]))
    lines += ["---", system.strip() if system else
              "You are a text-only assistant. Answer the user's request directly in plain text."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _agy_agent_file(path: Path, name: str, posture: Dict[str, Any], system: Optional[str]) -> None:
    """agy's custom agent (Round 1 L-G, FABLE D2 / SYNTHESIS R5): written on EVERY call from the catalog's posture, never
    trusted from disk (ORIN: the kimi precedent). The frontmatter is the lock -- `tools:` the explicit allowlist ([] for
    text), excludeDefaultComponents (no built-in tools or default prompt sections), inheritMcp false -- and the body is
    the system prompt. Written to a temporary name and renamed, so a reader never sees half a file."""
    lines = ["---", "name: %s" % name, "description: Voidloom overnight thinker (unattended)."]
    lines.append("tools: [%s]" % ", ".join(posture.get("tools") or []))
    for k, v in posture.items():
        if k == "tools":
            continue
        if isinstance(v, bool):
            lines.append("%s: %s" % (k, "true" if v else "false"))
        elif isinstance(v, list):
            lines.append("%s: [%s]" % (k, ", ".join(str(x) for x in v)))
        else:
            lines.append("%s: %s" % (k, v))
    lines += ["---", system.strip() if system else
              "You are a text-only assistant. Answer the user's request directly in plain text."]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp.%d" % (path.name, os.getpid()))
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _print_timeout(timeout_s: Optional[float]) -> str:
    """agy's own --print-timeout: a BACKSTOP 30 s past the seam's wall clock, so the seam's kill (a typed ThinkTimeout)
    always decides first and agy's own timer only matters if the kill somehow failed."""
    if timeout_s is None:
        return "0s"
    return "%ds" % (int(float(timeout_s)) + 30)


def _build_cli(bid: str, entry: Dict[str, Any], rec: Dict[str, str], posture: str, prompt: str,
               system: Optional[str], tier: Optional[str], call: Path, cred: Optional[Credential],
               timeout_s: Optional[float] = None) -> Tuple[List[str], Dict[str, str], str]:
    """(argv, env, model) for one CLI call. Nothing vendor-specific outside the catalog's words."""
    home = call / "home"
    ch = cli_home_dir(entry)
    ctx = {"call_home": str(home), "thinker_home": str(thinker_home()),
           "agent_file": str(call / "agent.md"), "skills_dir": str(call / "skills"),
           "cli_home": str(ch) if ch else "", "print_timeout": _print_timeout(timeout_s)}
    env = _base_env(call)
    model = rec.get("model", "") or ""
    if entry.get("prompt_via") == "template":
        # MC-R1-01: the argv RECORDED when the owner's template was proven (parsed once, never re-split), checked again
        # here, and exec'd as a list: the prompt is ONE whole element, so no shell ever reads the brain's text
        if not rec.get("command_argv"):
            raise ThinkUnavailable("unconfigured", "THINKER_RECORD holds no command_argv (a template recorded before the "
                                   "argv form): re-run `voidloom thinker set local --command '<template>'`", bid)
        try:
            toks = template_argv(json.loads(rec["command_argv"]))
            verdict = check_template(toks, model=model, binary=rec.get("binary", ""), cat=load_catalog())
        except (ValueError, CatalogError) as e:
            raise ThinkUnavailable("unconfigured", "the recorded command template is refused: %s" % e, bid)
        if verdict["tool_class"] == "none" and rec.get("accepted_agentic_risk") != "true":
            raise ThinkUnavailable("unconfigured", "the recorded command template's tools cannot be proven off and the "
                                   "record carries no owner acceptance (--accept-agentic)", bid)
        if system:
            prompt = "%s\n\n%s" % (system.strip(), prompt)
        argv = [_safe_prompt(prompt) if t == "{prompt}" else t.replace("{model}", model) for t in toks]
        if rec.get("binary"):
            argv[0] = rec["binary"]
        for k in entry.get("pass_env") or []:
            if os.environ.get(k):
                env[k] = os.environ[k]
        if rec.get("endpoint") and "OLLAMA_HOST" in (entry.get("pass_env") or []):
            env["OLLAMA_HOST"] = rec["endpoint"]
        return argv, env, model
    binary = rec.get("binary") or ""
    argv = [binary] + [_expand(a, ctx) for a in entry["argv"]]
    post = entry["posture"][posture]
    if isinstance(post, list):
        argv += list(post)
    elif entry.get("agent_file"):                        # agy: an agent file in the CLI's own home, by name
        _agy_agent_file(_agent_file_path(entry, call), entry["agent_file"]["name"], post, system)
    else:                                               # kimi: the posture is an agent file
        (call / "skills").mkdir(exist_ok=True)
        _kimi_agent_file(call / "agent.md", post, system)
    # model / tier
    tsp, msp = entry.get("tier") or {}, entry.get("model") or {}
    chosen = ""
    if tier and tsp.get("kind") == "argv" and tier in (tsp.get("values") or {}):
        chosen = tsp["values"][tier]
        argv += [tsp["flag"], chosen]
    elif (model or msp.get("pin")) and msp.get("kind") == "argv":
        chosen = model or msp["pin"]         # a pinned backend never runs on the vendor's server-chosen default
        refused = [w for w in (msp.get("refuse") or []) if w.lower() in chosen.lower()]
        if refused:
            raise ThinkUnavailable("unconfigured", "the recorded model %r is refused for the overnight thinker (the "
                                   "owner's ruling: %s is never used); re-run `voidloom thinker set %s`"
                                   % (chosen, refused[0], bid), bid)
        argv += [msp["flag"], chosen]
    elif msp.get("kind") == "env":
        chosen = model or msp.get("default", "")
        env[msp["name"]] = chosen
    # system prompt
    sp = entry.get("system") or {}
    if system:
        if sp.get("kind") == "argv-file":
            f = call / "system.md"
            f.write_text(system, encoding="utf-8")
            argv += [sp["flag"], str(f)]
        elif sp.get("kind") == "config":
            argv += [sp["flag"], "%s=%s" % (sp["key"], json.dumps(system))]
        elif sp.get("kind") == "fold-into-prompt":
            prompt = "%s\n\n%s" % (system.strip(), prompt)
        # agent-file-body: written above
    # hook-free env + provider env + credential (ONLY this backend's)
    for k, v in (entry.get("hook_free_env") or {}).items():
        env[k] = _expand(v, ctx)
    if ch is not None:
        # cli-home: the CLI's own variable names the brain's folder (CODEX_HOME for codex -- the same value its
        # hook_free_env already sets; HOME itself for agy, overriding the fresh per-call HOME)
        env[entry["credential"]["env"]] = str(ch)
        for d in entry["credential"].get("empty_dirs") or []:
            (ch / d).mkdir(parents=True, exist_ok=True)
            os.chmod(str(ch / d), 0o700)
    for k, v in (entry.get("provider_env") or {}).items():
        env[k] = v
    # the posture's own tool switches that live in the environment, for EVERY posture (pass P: Claude Code 2.1.287 enables
    # a server-side advisor tool even under `--tools ""`; CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 is the CLI's own switch)
    for k, v in (entry.get("tool_off_env") or {}).items():
        env[k] = v
    if bid == "kimi" and rec.get("base_url"):
        env["KIMI_MODEL_BASE_URL"] = rec["base_url"]
    if "KIMI_CODE_HOME" in env:
        kh = Path(env["KIMI_CODE_HOME"])
        kh.mkdir(parents=True, exist_ok=True)
        (kh / "config.toml").write_text("# empty: no providers, no hooks, no OAuth (Voidloom thinker)\n",
                                        encoding="utf-8")
    if cred is not None:
        env.update(cred.env)
    # prompt last
    pv = entry["prompt_via"]
    if pv == "argv-last":
        argv.append(_safe_prompt(prompt))
    elif pv.startswith("flag:"):
        argv = argv[:1] + [pv[len("flag:"):], _safe_prompt(prompt)] + argv[1:]
    return argv, env, chosen


def _lock_rel(entry: Dict[str, Any]) -> str:
    """The catalog's lock path (relative to thinker_home): the cli-home credential's own, else a top-level `lock`."""
    return (entry.get("credential") or {}).get("lock") or entry.get("lock") or ""


def _codex_lock(timeout_s: float, rel: str = "codex/.thinker.lock", bid: str = "codex"):
    """An exclusive flock on <thinker>/<rel>: one serialized stream per sign-in (OpenAI's rule for auth.json; agy's
    persistent HOME holds a token it refreshes and a conversation store, so its calls are serialized the same way)."""
    import fcntl
    lp = thinker_home() / rel
    lp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(str(lp), "a")
    deadline = time.monotonic() + max(1.0, timeout_s)
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES):
                fh.close()
                raise
            if time.monotonic() >= deadline:
                fh.close()
                raise ThinkTimeout("timeout", "waited %.0f s for the %s lock (%s)" % (timeout_s, bid, lp), bid)
            time.sleep(0.2)


def _read_stream(entry: Dict[str, Any], stream: Dict[str, Any], out: str, err: str, ch: Optional[Path],
                 evidence: Optional[Dict[str, Any]]) -> str:
    """agy's stream-json, read for the proof (owner run T4, 2026-10-16): every `step_update` whose step_type is `tool`
    is a tool step the model ran or tried (counted once per step_index), the `result` event is the envelope the
    ordinary path reads (returned as its JSON text), and its denied_actions are tools the CLI refused. token_leaked:
    the brain's own sign-in text appears anywhere in what the program printed (only the yes/no is kept)."""
    steps: Dict[Any, str] = {}
    result: Optional[Dict[str, Any]] = None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        ev = o.get("event") or o.get("type")
        if ev == stream["step_event"]:
            su = o.get(stream["step_event"]) or {}
            if isinstance(su, dict) and su.get("step_type") == stream["step_type"]:
                steps[su.get("step_index", len(steps))] = str(su.get("tool_name") or "?")
        elif ev == stream["result_event"] and isinstance(o.get(stream["result_event"]), dict):
            result = o[stream["result_event"]]
    leaked = False
    if ch is not None:
        try:
            tok = (ch / entry["credential"]["auth_file"]).read_text(encoding="utf-8", errors="replace").strip()
            leaked = bool(tok) and len(tok) >= 16 and (tok in out or tok in err)
        except OSError:
            pass
    if evidence is not None:
        evidence.update(tool_events=len(steps), tool_names=sorted(set(steps.values())),
                        denied_actions=[str((d or {}).get("action", d)) for d in ((result or {}).get("denied_actions")
                                                                                  or [])],
                        token_leaked=leaked, stream_result=result is not None)
    return json.dumps(result) if result is not None else out


def prune_history(entry: Dict[str, Any], *, keep_days: Optional[float] = None, now: Optional[float] = None,
                  force: bool = False) -> Dict[str, Any]:
    """BOUNDED PRUNING of a persistent cli-home CLI's own per-conversation history (r1 reflection F1). agy keeps every
    call's transcript in the brain's home -- conversations/<id>.db, brain/<id>/, implicit/, annotations/, presence/,
    plus one index row per conversation in conversation_summaries.db -- about 20 entries and 450 KB a call, and those
    transcripts are drawn from the brain's memories. Each entry of the catalog's `history.stores` folders whose own
    mtime is older than `keep_days` is removed, and the index rows of the conversation files removed with it.
    Called ONLY under the backend's lock and before its program starts (never while a call runs), at most once per
    `every_hours` (a stamp file) unless forced. The sign-in file is never a candidate: a store folder that holds it,
    or any path at or above it, is refused. Never raises: what it did or could not do is returned."""
    spec = entry.get("history") or {}
    ch = cli_home_dir(entry)
    out: Dict[str, Any] = {"removed": 0, "index_rows": 0, "errors": []}
    if ch is None or not spec.get("stores"):
        out["skipped"] = "no history spec"
        return out
    now = time.time() if now is None else now
    stamp = ch / spec.get("stamp", ".thinker-pruned")
    if not force:
        try:
            if now - stamp.stat().st_mtime < float(spec.get("every_hours", 24)) * 3600:
                out["skipped"] = "pruned within the last %s h" % spec.get("every_hours", 24)
                return out
        except OSError:
            pass
    days = float(spec["keep_days"] if keep_days is None else keep_days)
    cutoff = now - days * 86400
    auth = (ch / entry["credential"]["auth_file"]).resolve()
    gone_ids: List[str] = []
    for rel in spec["stores"]:
        d = (ch / rel)
        try:
            dr = d.resolve()
        except OSError:
            continue
        if not _under(dr, ch) or _under(auth, dr) or dr == ch.resolve():
            out["errors"].append("refused store %s (it holds the sign-in or leaves the home)" % rel)
            continue
        if not d.is_dir():
            continue
        for q in list(d.iterdir()):
            try:
                if q.lstat().st_mtime >= cutoff or q.resolve() == auth:
                    continue
                if q.is_dir() and not q.is_symlink():
                    shutil.rmtree(str(q))
                else:
                    q.unlink()
                out["removed"] += 1
                if rel == spec.get("index", {}).get("store"):
                    gone_ids.append(q.name[:-len(".db")] if q.name.endswith(".db") else q.name)
            except OSError as e:
                out["errors"].append("%s: %s" % (q.name, e))
    ix = spec.get("index") or {}
    if gone_ids and ix.get("db"):
        import sqlite3
        try:
            con = sqlite3.connect(str(ch / ix["db"]), timeout=60)   # >= 60 s: the house rule (busy_wait_r3)
            try:
                with con:
                    for i in gone_ids:
                        out["index_rows"] += con.execute("DELETE FROM %s WHERE %s = ?" % (ix["table"], ix["key"]),
                                                         (i,)).rowcount
            finally:
                con.close()
        except sqlite3.Error as e:
            out["errors"].append("index: %s" % e)
    try:
        stamp.write_text("%s\n" % _now(), encoding="utf-8")
    except OSError as e:
        out["errors"].append("stamp: %s" % e)
    out["keep_days"] = days
    return out


def _home_guard(bid: str, entry: Dict[str, Any], ch: Optional[Path], call: Path) -> None:
    """Refuse a call whose instruction channels are not shut (Round 1 L-G, measured on agy 1.3.4 by its own
    'hooks_manager ... loaded N named hooks' line): agy LOADS <HOME>/.gemini/config/hooks.json (a hook is a shell command
    run by the CLI) and the rules/skills/plugins beside it, and it walks from the working folder up to a git root for
    .agents/, GEMINI.md and AGENTS.md. The brain's agy HOME is persistent, so whatever lands there would steer every
    later call: the catalog's `forbid` paths must not exist, its `must_be_empty` files must carry nothing, and with
    walk_root 'git' no ancestor of the call's folder may be a git root. Unconfigured, never a guess."""
    cred = entry.get("credential") or {}
    if ch is not None:
        found = [r for r in cred.get("forbid") or [] if os.path.lexists(str(ch / r))]
        if found:
            raise ThinkUnavailable("unconfigured", "the brain's %s folder %s holds %s -- an instruction channel the "
                                   "overnight thinker never runs with (a hook is a shell command); remove it, then "
                                   "`voidloom thinker probe`" % (bid, ch, ", ".join(found[:5])), bid)
        for r in cred.get("must_be_empty") or []:
            f = ch / r
            try:
                raw = f.read_bytes()[:65536] if f.is_file() else b""
            except OSError:
                raw = b"?"
            body = raw.strip()
            if body and body not in (b"{}", b"[]"):
                try:
                    val = json.loads(body.decode("utf-8"))
                    empty = isinstance(val, dict) and not any(v for v in val.values())
                except ValueError:
                    empty = False
                if not empty:
                    raise ThinkUnavailable("unconfigured", "the brain's %s folder carries a non-empty %s (MCP servers "
                                           "are never offered overnight); empty it, then `voidloom thinker probe`"
                                           % (bid, f), bid)
    if entry.get("walk_root") == "git":
        for anc in [call] + list(call.parents):
            if (anc / ".git").exists():
                raise ThinkUnavailable("unconfigured", "the call folder %s sits inside a git work tree (%s): %s would "
                                       "read that repository's GEMINI.md / AGENTS.md / .agents -- set TMPDIR to a "
                                       "folder outside any repository" % (call, anc, bid), bid)


def _text(data: bytes) -> str:
    """Bytes as the text the seam always read (Popen text mode): UTF-8 with replacement, universal newlines."""
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _kill_group(proc: "subprocess.Popen") -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _capture(argv: List[str], *, timeout_s: float, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
             cap: int = OUTPUT_CAP_BYTES) -> Tuple[int, bytes, bytes, str]:
    """(rc, stdout, stderr, why) of ONE child in its own process group, stdin /dev/null (MC-R1-12). stdout and stderr are
    read INCREMENTALLY until end of file, the wall clock, or `cap` bytes held (both streams together) -- a pipe the seam
    stops reading blocks the writer, so a runaway cannot outrun the cap. why: '' (it ended by itself), 'timeout' or
    'overflow'; on either of the last two the whole process group is killed and NOTHING more is read. rc is None
    then. Popen's own errors (a missing binary, E2BIG, a NUL byte) propagate to the caller, which types them."""
    import selectors
    t0 = time.monotonic()
    deadline = t0 + float(timeout_s)
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    ofd, efd = proc.stdout.fileno(), proc.stderr.fileno()
    bufs: Dict[int, List[bytes]] = {ofd: [], efd: []}
    held, why = 0, ""
    sel = selectors.DefaultSelector()
    try:
        sel.register(proc.stdout, selectors.EVENT_READ)
        sel.register(proc.stderr, selectors.EVENT_READ)
        while sel.get_map() and not why:
            left = deadline - time.monotonic()
            if left <= 0:
                why = "timeout"
                break
            for key, _ev in sel.select(timeout=min(left, 1.0)):
                chunk = os.read(key.fd, _READ_CHUNK)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                held += len(chunk)
                if held > cap:
                    why = "overflow"        # the chunk that crossed the cap is dropped with the rest
                    break
                bufs[key.fd].append(chunk)
        if not why:
            try:
                proc.wait(timeout=max(0.05, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                why = "timeout"             # it closed both streams and kept running
    finally:
        sel.close()
        if why or proc.poll() is None:
            _kill_group(proc)
        for f in (proc.stdout, proc.stderr):
            try:
                f.close()
            except Exception:
                pass
    if why:
        bufs.clear()                       # discarded, never returned
        return None, b"", b"", why         # type: ignore[return-value]
    return proc.returncode, b"".join(bufs[ofd]), b"".join(bufs[efd]), ""


def _overflow(bid: str, what: str, el: float) -> "ThinkOverflow":
    return ThinkOverflow("overflow", "%s wrote more than the seam's output cap, %d bytes (%d MiB, OUTPUT_CAP_BYTES): not "
                         "an answer -- the call was stopped, nothing more was read, and its output was discarded"
                         % (what, OUTPUT_CAP_BYTES, OUTPUT_CAP_BYTES // (1024 * 1024)), bid, el)


def _run_cli(bid: str, entry: Dict[str, Any], argv: List[str], env: Dict[str, str], cwd: Path,
             timeout_s: float) -> Tuple[int, str, str, float]:
    """One child through _capture: its output bounded in BYTES (MC-R1-12) and in seconds, every failure TYPED."""
    t0 = time.monotonic()
    try:
        rc, out, err, why = _capture(argv, cwd=str(cwd), env=env, timeout_s=timeout_s)
    except FileNotFoundError:
        raise ThinkUnavailable("missing", "the %s binary is not at %s -- re-run `voidloom thinker set %s`"
                               % (bid, argv[0], bid), bid)
    except PermissionError:
        raise ThinkUnavailable("missing", "%s is not executable" % argv[0], bid)
    except (OSError, ValueError) as e:     # MC-R1-11: E2BIG, a NUL byte, a lone surrogate -- the call cannot START
        sizes = []
        for a in argv:
            try:
                sizes.append(len(a.encode("utf-8", "surrogatepass")))
            except Exception:
                sizes.append(len(a))
        raise ThinkMalformed("malformed", "the call could not start (%s: %s): the prompt cannot be passed as one "
                             "argument (%d bytes; Linux caps one argument at 131072 bytes, a NUL byte ends it, and it "
                             "must be encodable text)" % (type(e).__name__, e, max(sizes or [0])), bid,
                             round(time.monotonic() - t0, 2))
    el = round(time.monotonic() - t0, 2)
    if why == "timeout":
        raise ThinkTimeout("timeout", "no answer within %s s (process group killed)" % timeout_s, bid, el)
    if why == "overflow":
        raise _overflow(bid, "the %s program" % bid, el)
    return rc, _text(out), _text(err), el


def _read_body(r: Any, deadline: float, cap: int = OUTPUT_CAP_BYTES) -> Tuple[bytes, str]:
    """(body, why) of an HTTP reply read INCREMENTALLY (MC-R1-12): why '' | 'timeout' | 'overflow'; on either of the last
    two nothing more is read and the body is discarded. Before each read the socket's timeout is set to the time LEFT
    on the call's wall clock, so a server that drips a byte at a time cannot outlast it (urllib's own timeout is per
    socket operation, not per call)."""
    sock = getattr(getattr(getattr(r, "fp", None), "raw", None), "_sock", None)
    read = getattr(r, "read1", None) or r.read
    parts: List[bytes] = []
    held = 0
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return b"", "timeout"
        if sock is not None:
            try:
                sock.settimeout(left)
            except OSError:
                pass
        chunk = read(_READ_CHUNK)
        if not chunk:
            return b"".join(parts), ""
        held += len(chunk)
        if held > cap:
            return b"", "overflow"
        parts.append(chunk)


def _apply_probe_body(body: Dict[str, Any], probe_body: Dict[str, Any]) -> None:
    """The catalog's `probe_body` (dotted paths -> values) set into a request body: 'options.temperature' -> 0."""
    for path, value in (probe_body or {}).items():
        node = body
        parts = str(path).split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


HTTP_JOIN_SLACK_S = 2.0         # the deadline thread's grace past the call's wall clock (R2-04)


def _http(bid: str, entry: Dict[str, Any], rec: Dict[str, str], prompt: str, system: Optional[str],
          timeout_s: float, purpose: str = "") -> Tuple[str, str, float]:
    """The whole exchange -- connect, headers, body, an error body -- is bounded by the CALL's wall clock (R2-04: a server
    dripping its headers or an error body one byte at a time kept urllib's per-operation timeout alive for > 60 s against
    a 4 s call, ~9 h for a 64 KB header line at 2 B/s). The exchange runs in a daemon thread; past timeout_s +
    HTTP_JOIN_SLACK_S the caller gets ThinkTimeout and the thread is abandoned (its memory is already bounded in bytes by
    _read_body and HTTP_ERROR_BODY_BYTES; it ends with its socket or the process)."""
    import threading
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["v"] = _http_once(bid, entry, rec, prompt, system, timeout_s, purpose)
        except BaseException as e:       # re-raised, already typed, on the caller's thread
            box["e"] = e
    t0 = time.monotonic()
    th = threading.Thread(target=_run, name="thinker-http", daemon=True)
    th.start()
    th.join(float(timeout_s) + HTTP_JOIN_SLACK_S)
    if th.is_alive():
        raise ThinkTimeout("timeout", "no whole answer within %s s (the server kept the connection busy: headers or an "
                           "error body arriving a byte at a time)" % timeout_s, bid, round(time.monotonic() - t0, 2))
    if "e" in box:
        raise box["e"]
    return box["v"]


def _http_once(bid: str, entry: Dict[str, Any], rec: Dict[str, str], prompt: str, system: Optional[str],
               timeout_s: float, purpose: str = "") -> Tuple[str, str, float]:
    endpoint = (rec.get("endpoint") or entry["endpoint_default"]).rstrip("/")
    model = rec.get("model", "")
    if not model:
        raise ThinkUnavailable("unconfigured", "no model recorded for %s" % bid, bid)
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: Dict[str, Any] = {"model": model, "messages": msgs, "stream": False}
    if entry["transport"] == "http-ollama":
        body["options"] = dict(entry.get("options") or {})
        body["truncate"] = False
    if purpose in SCREEN_EXEMPT:
        # THE PROOF DECODES DETERMINISTICALLY (Round 2 wave 2B, measured live): the proof's form was measured exact at
        # temperature 0 (Round 0 P11, 5/5 on a 0.5B) and the seam sent the server's DEFAULT sampling -- 0/3 exact on
        # /api/chat ('2873+1=2874'), so a working local model FAILED the proof that gates recording the owner's answer.
        # The proof's calls (and only they) carry the catalog's probe_body; an organ keeps the model's own sampling.
        _apply_probe_body(body, entry.get("probe_body") or {})
    try:
        req = urllib.request.Request(endpoint + entry["path"], data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    except ValueError as e:                # MC-R1-11: a recorded endpoint that is not a URL
        raise ThinkUnavailable("unconfigured", "the recorded endpoint %r is not a URL the seam can reach (%s) -- re-run "
                               "`voidloom thinker set local --endpoint http://HOST:PORT ...`" % (endpoint, e), bid)
    import http.client
    t0 = time.monotonic()
    why = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data, why = _read_body(r, t0 + float(timeout_s))
        raw = data.decode("utf-8", "replace")
    except http.client.HTTPException as e:  # MC-R1-11: not HTTP (BadStatusLine), cut mid-reply (IncompleteRead)
        raise ThinkUnavailable("unreachable", "%s did not answer as an HTTP model server (%s: %s)"
                               % (endpoint, type(e).__name__, str(e)[:200]), bid, round(time.monotonic() - t0, 2))
    except urllib.error.HTTPError as e:
        txt = ""
        try:
            txt = e.read(HTTP_ERROR_BODY_BYTES).decode("utf-8", "replace")[:300]     # capped (MC-R1-12)
        except Exception:
            pass
        el = round(time.monotonic() - t0, 2)
        if e.code == 404:
            raise ThinkUnavailable("missing", "HTTP 404 (model %s not on the server): %s" % (model, txt), bid, el)
        if e.code in (401, 403):
            raise ThinkUnavailable("auth", "HTTP %d: %s" % (e.code, txt), bid, el)
        if e.code == 429:
            raise ThinkUnavailable("quota", "HTTP 429: %s" % txt, bid, el)
        if e.code == 400:
            raise ThinkMalformed("malformed", "HTTP 400 (%s) -- an input over the context is refused, never "
                                 "truncated" % txt, bid, el)
        raise ThinkUnavailable("unreachable", "HTTP %d: %s" % (e.code, txt), bid, el)
    except (TimeoutError,) as e:
        raise ThinkTimeout("timeout", "no answer within %s s" % timeout_s, bid, round(time.monotonic() - t0, 2))
    except urllib.error.URLError as e:
        el = round(time.monotonic() - t0, 2)
        if "timed out" in str(e.reason).lower():
            raise ThinkTimeout("timeout", "no answer within %s s" % timeout_s, bid, el)
        raise ThinkUnavailable("unreachable", "%s: %s" % (endpoint, e.reason), bid, el)
    except OSError as e:
        el = round(time.monotonic() - t0, 2)
        if "timed out" in str(e).lower():
            raise ThinkTimeout("timeout", "no answer within %s s" % timeout_s, bid, el)
        raise ThinkUnavailable("unreachable", "%s: %s" % (endpoint, e), bid, el)
    el = round(time.monotonic() - t0, 2)
    if why == "timeout":
        raise ThinkTimeout("timeout", "no whole answer within %s s (the connection was closed)" % timeout_s, bid, el)
    if why == "overflow":
        raise _overflow(bid, "the %s server" % bid, el)
    try:
        obj = json.loads(raw)
        text = str(_json_at(obj, entry["output"]["path"])).strip()
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ThinkMalformed("malformed", "the reply has no %s: %s" % (entry["output"]["path"], e), bid, el)
    chk = entry["input_check"]
    try:
        got = int(_json_at(obj, chk["sent"]))
    except (KeyError, IndexError, TypeError, ValueError):
        raise ThinkMalformed("malformed", "the server did not say how much input it evaluated (%s): a silent "
                             "truncation could not be ruled out" % chk["sent"], bid, el)
    if chk.get("cached"):
        try:
            got += int(_json_at(obj, chk["cached"]))
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    words = len(((system or "") + " " + prompt).split())
    floor = int(words * float(chk.get("floor_per_word", 0.9)))
    if got < floor:
        raise ThinkMalformed("malformed", "the model evaluated %d tokens of ~%d words sent: the input was "
                             "TRUNCATED (raise num_ctx)" % (got, words), bid, el)
    return text, model, el


# ---------------------------------------------------------------------------------------------------------------
# think()
# ---------------------------------------------------------------------------------------------------------------
def _find_json(text: str) -> bool:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        a, b = t.find(opener), t.rfind(closer)
        if a != -1 and b > a:
            try:
                json.loads(t[a:b + 1])
                return True
            except ValueError:
                continue
    return False


def _config_error(e: Exception) -> ThinkUnavailable:
    return ThinkUnavailable("unconfigured", str(e), "")


# THE TEXT-POSTURE CONTEXT (Round 1 Wave C, M18 -- MEASURED LIVE on a scratch copy of a real brain, claude through the
# seam). An instruction-free, tool-less call (M3/M4: no hooks, no CLAUDE.md, no persona, no tools) meets organ prompts
# written for a model that HAD the persona and the tools: the weekly digest ("write in first person as Claude
# reflecting on its own behavior") came back as a refusal ("I don't actually have a self-observation system..."), 3 of
# the 4 quarterly-manifest councillors refused the same way, and the counterfactual revisit narrated tool calls
# ('{"command": "find / -iname ..."}', "I'll check the working directory ... 1 tool use") -- and each organ STORED
# what it got. The seam now says, ahead of every TEXT-purpose prompt, the two facts those prompts leave out: whose
# material this is, and that the call has no tools. It is fixed text owned by the seam (not ambient, not a persona),
# the same for every backend; research purposes (which do have their tools) do not get it.
TEXT_CONTEXT = ("[Context for this request: it comes from an automated maintenance job of a local memory system that "
                "an AI assistant uses to keep notes across its working sessions. The material below was recorded by "
                "that system. Where the request asks you to write in the first person, to take a named voice or "
                "role, or to reflect on 'our' or 'your' memories, write in that voice as the memory system, drawing "
                "only on the material given; that is the task, not a claim about you. In this call you have no tools "
                "and no access to files, commands or the web: answer directly from the text below, and never write "
                "out, describe or simulate a tool call.]")


def think(prompt: str, *, purpose: str, system: Optional[str] = None, tier: Optional[str] = None,
          timeout_s: float, expect_json: bool = False) -> ThinkResult:
    """Ask the recorded overnight thinker. See the module docstring for the contract. A TEXT-purpose prompt is sent
    behind TEXT_CONTEXT (whose material it is, and that the call has no tools).

    EVERY failure is a ThinkError (Round 2 wave 1, MC-R1-11): a prompt the transport cannot carry (a NUL byte, an
    argument over Linux's 131072 bytes, a lone surrogate) is ThinkMalformed; wrong arguments are ThinkMisuse (also a
    ValueError); anything else the seam could not type is ThinkInternal -- each logged once in THINKER_LOG."""
    try:
        return _think(prompt, purpose=purpose, system=system, tier=tier, timeout_s=timeout_s, expect_json=expect_json)
    except ThinkError:
        raise                              # typed, and logged where it was raised
    except Exception as e:                 # the boundary: nothing leaves the seam untyped
        err = _untyped(e, "")
        _log(purpose=purpose, backend="?", outcome=err.kind, reason=err.reason, note="untyped")
        raise err from e


def _think(prompt: str, *, purpose: str, system: Optional[str], tier: Optional[str], timeout_s: float,
           expect_json: bool) -> ThinkResult:
    try:
        cat = load_catalog()
    except CatalogError as e:
        err = _config_error(e)
        _log(purpose=purpose, backend="?", outcome="unavailable", reason="unconfigured", note="catalog")
        raise err
    try:
        posture = posture_for(cat, purpose)
        if not isinstance(prompt, str) or (system is not None and not isinstance(system, str)):
            raise ThinkMisuse("misuse", "the prompt (and a system prompt, when given) must be text, got %s%s"
                              % (type(prompt).__name__, "" if system is None else "/%s" % type(system).__name__))
    except ThinkMisuse as e:
        _log(purpose=purpose, backend="?", outcome=e.kind, reason=e.reason)
        raise
    try:
        rec = read_record()
    except RecordError as e:
        _log(purpose=purpose, backend="?", outcome="unavailable", reason="unconfigured", note="record-refused")
        raise ThinkUnavailable("unconfigured", str(e), "")
    if rec is None:
        _log(purpose=purpose, backend="none", outcome="unavailable", reason="unconfigured", note="no-record")
        raise ThinkUnavailable("unconfigured", "nobody has answered who thinks overnight: no %s -- run "
                               "`voidloom thinker set <%s>`" % (record_path(), "|".join(ANSWERS)), "")
    if rec.get("backend") == "off":
        _log(purpose=purpose, backend="off", outcome="unavailable", reason="unconfigured", note="owner-off")
        raise ThinkUnavailable("unconfigured", "the owner answered e) not now: the simple nightly routines run "
                               "instead (voidloom thinker set <backend> to switch it on)", "off")
    if posture == "text":
        prompt = TEXT_CONTEXT + "\n\n" + prompt
    return _think_with(cat, rec, prompt, purpose=purpose, posture=posture, system=system, tier=tier,
                       timeout_s=timeout_s, expect_json=expect_json)


def _think_with(cat: Dict[str, Any], rec: Dict[str, str], prompt: str, *, purpose: str, posture: str,
                system: Optional[str], tier: Optional[str], timeout_s: float, expect_json: bool,
                log: bool = True, evidence: Optional[Dict[str, Any]] = None, watch: str = "",
                credential_check: bool = True, count_tools: bool = False,
                default_agent: bool = False) -> ThinkResult:
    """One call. `evidence` (the proof's, pass P): when a dict is given it is FILLED -- whatever the call's outcome,
    including a timeout -- with what the proof decides the tool block from: the CLI's rc, its first line and terminal
    reason, whether the call ran under its posture's tool switches, and the two folders' contents read BEFORE the
    sandbox is deleted (`watch` = the file name looked for in the fresh HOME).
    count_tools (the proof's tool step, Round 1 L-G fix pass): the CLI's own event stream is read instead of its single
    envelope, and evidence gains tool_events (the tool steps it ran or tried), tool_names, denied_actions and
    token_leaked. default_agent: the POSITIVE CONTROL -- the same call without the locked agent's --agent, so the
    vendor's default agent (which HAS tools) answers; it must show a tool step for the lock's zero to mean anything."""
    bid = rec.get("backend", "")

    def _fail(err: ThinkError) -> ThinkError:
        if log:
            _log(purpose=purpose, backend=bid or "?", outcome=err.kind, reason=err.reason, elapsed=err.elapsed_s)
        return err

    if tier is not None and tier not in TIERS:
        raise _fail(ThinkMisuse("misuse", "tier must be one of %s or None, got %r" % (TIERS, tier), bid))
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s > 0:
        raise _fail(ThinkMisuse("misuse", "timeout_s must be a positive number, got %r" % (timeout_s,), bid))
    if bid not in cat["backends"]:
        raise _fail(ThinkUnavailable("unconfigured", "the recorded backend %r is not in the catalog" % bid, bid))
    entry = cat["backends"][bid]

    if posture == "research" and not entry.get("web"):
        raise _fail(ThinkSkipped("skipped", "%s has no web, so %s is a logged skip -- never invented research"
                                 % (bid, purpose), bid))
    if entry["transport"] != "cli":
        if evidence is not None:
            evidence.update(transport=entry["transport"], posture_in_force=True,
                            posture_why="an HTTP model server offers no tools (by construction)")
        try:
            text, model, el = _http(bid, entry, rec, prompt, system, timeout_s, purpose)
            return _finish(bid, text, model, el, posture, expect_json, purpose, log)
        except ThinkError as e:
            raise _fail(e)
        except Exception as e:             # MC-R1-11: typed AT THE SEAM, never in the organs
            raise _fail(_untyped(e, bid))

    cred = None
    call = None
    lock = None
    try:
        if (entry.get("credential") or {}).get("kind") != "none" and credential_check:
            try:
                cred = resolve_credential(bid, cat)
            except (OSError, ValueError) as e:     # MC-R1-11: an unreadable credential file is an auth failure
                raise ThinkUnavailable("auth", "the %s credential in %s could not be read (%s: %s) -- run `voidloom "
                                       "thinker login %s`" % (bid, thinker_home(), type(e).__name__, str(e)[:120], bid),
                                       bid)
            if cred is None:
                raise ThinkUnavailable("auth", "no %s credential in %s -- run `voidloom thinker login %s` "
                                       "(docs/OVERNIGHT_THINKER_SETUP.md Part %s)"
                                       % (bid, thinker_home(), bid, entry.get("guide_part", "?")), bid)
        refusal = platform_refusal(entry)
        if refusal:
            raise ThinkUnavailable("unconfigured", refusal, bid)
        call = _call_dir()
        if _lock_rel(entry):
            # taken BEFORE the build (Round 1 L-G): agy's agent file is written into the brain's persistent home, so
            # two calls must never interleave writing and reading it; codex's build touches nothing shared
            lock = _codex_lock(timeout_s, _lock_rel(entry), bid)
            if (entry.get("history") or {}).get("prune"):
                pr = prune_history(entry)            # under the lock, before the program starts: outside any call
                if evidence is not None:
                    evidence["history_prune"] = pr
        argv, env, model = _build_cli(bid, entry, rec, posture, prompt, system, tier, call, cred, timeout_s)
        ch = cli_home_dir(entry)
        stream = (entry.get("output") or {}).get("stream") if count_tools else None
        if stream:
            i = argv.index(stream["format_flag"])
            argv = argv[:i + 1] + [stream["format"]] + argv[i + 2:]
        if default_agent and entry.get("agent_file"):
            name = entry["agent_file"]["name"]
            i = next(j for j in range(len(argv) - 1) if argv[j:j + 2] == ["--agent", name])
            argv = argv[:i] + argv[i + 2:]
        if evidence is not None:
            evidence["transport"] = "cli"
            evidence["posture_in_force"], evidence["posture_why"] = _posture_in_force(entry, posture, argv, env, call)
        if not argv[0] or not os.path.isabs(argv[0]):
            raise ThinkUnavailable("missing", "no absolute %s binary recorded -- re-run `voidloom thinker set %s`"
                                   % (bid, bid), bid)
        _home_guard(bid, entry, ch, call)
        if (entry.get("credential") or {}).get("sandbox") and not os.access(argv[0], os.X_OK):
            raise ThinkUnavailable("missing", "the %s binary is not at %s -- re-run `voidloom thinker set %s`"
                                   % (bid, argv[0], bid), bid)
        argv = sandboxed_argv(bid, entry, argv, env, str(call / "cwd"))
        if evidence is not None:
            evidence["sandboxed"] = Path(argv[0]).name == "bwrap"
        rc, out, err, el = _run_cli(bid, entry, argv, env, call / "cwd", timeout_s)
        if stream:
            out = _read_stream(entry, stream, out, err, ch, evidence)
        first, term = _cli_said(entry, out, err)
        if evidence is not None:
            evidence.update(rc=rc, first_line=first, terminal_reason=term, elapsed_s=el)
        left = sorted(x.name for x in (call / "cwd").iterdir())
        if left:
            raise ThinkRefused("refused", "the tool block did not hold: the call left %d file(s) in its empty "
                               "working folder (%s); the output was discarded" % (len(left), ", ".join(left[:5])),
                               bid, el)
        if rc != 0:
            if _tool_attempt_blocked(entry, first, term):
                # pass P (P2): the CLI's own words -- the model attempted a tool call, none ran. Typed as a refusal,
                # never as the backend being unreachable (a doctor must not count it as a night the backend was down)
                raise ThinkToolBlocked("tool-attempt-blocked", "the model attempted a tool call and the %s CLI ran "
                                       "none (exited %s: %s)" % (bid, rc, first), bid, el)
            said = failed_run_detail(subprocess.CompletedProcess(argv, rc, out, err))
            if entry["output"]["kind"] == "json":
                try:
                    o = json.loads(out)
                    if isinstance(o, dict) and _json_said(entry, o):
                        said = "stdout: %s" % _json_said(entry, o)[:300]
                except ValueError:
                    pass
            raise ThinkUnavailable(classify(said), "exited %s: %s" % (rc, said), bid, el)
        try:
            text = _extract_cli(entry, out).strip()        # the answer, without the transport's trailing newlines
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ThinkMalformed("malformed", "the reply is not %s's %s shape: %s"
                                 % (bid, entry["output"]["kind"], e), bid, el)
        if entry["output"]["kind"] == "json":
            try:
                o = json.loads(out)
                said_err = _json_error(entry, o)
                if said_err is not None:
                    if _tool_attempt_blocked(entry, first, term):
                        raise ThinkToolBlocked("tool-attempt-blocked", "the model attempted a tool call and the %s "
                                               "CLI ran none (it reported: %s)" % (bid, first), bid, el)
                    msg = said_err or text
                    raise ThinkUnavailable(classify(msg), "the backend reported an error: %s" % msg[:300], bid, el)
                reported = _json_model(entry, o)
                if evidence is not None:
                    evidence["model_reported"] = reported
                pin = model or rec.get("model", "")
                if reported and pin and (entry.get("model") or {}).get("check") and not _model_matches(reported, pin):
                    # R6: the brain asked for one model and the vendor answered with another -- never stored
                    raise ThinkMalformed("model-mismatch", "the %s program reports model %r, but %r was pinned; the "
                                         "answer was discarded" % (bid, reported[:80], pin), bid, el)
                if reported:
                    model = reported
            except ValueError:
                pass
        return _finish(bid, text, model or rec.get("model", ""), el, posture, expect_json, purpose, log)
    except ThinkError as e:
        raise _fail(e)
    except Exception as e:                 # MC-R1-11: typed AT THE SEAM (an OS error preparing the call, anything else)
        raise _fail(_untyped(e, bid))
    finally:
        if lock is not None:
            try:
                lock.close()
            except Exception:
                pass
        if call is not None:
            if evidence is not None:
                try:                       # the proof's evidence, read BEFORE the sandbox goes (pass P, P1)
                    persist = cli_home_dir(entry) if (entry.get("credential") or {}).get("env") == "HOME" else None
                    evidence.update(_sandbox_evidence(call, watch, (persist,) if persist else ()))
                except Exception as e:     # never raised out of finally; an unread folder proves nothing
                    evidence.update(checked_before_delete=False, cwd_entries=None,
                                    cwd_error="%s: %s" % (type(e).__name__, e))
            shutil.rmtree(str(call), ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------
# THE REPLY SCREEN (Round 1 iteration 2, MC-R1-09): a refusal or a narrated tool call is NOT an answer. MEASURED LIVE
# (M18 attempt 3, claude, text posture): the weekly digest came back as "I appreciate the creative framing, but I need
# to be direct: I don't actually have a self-observation system...", 3 of 4 councillors refused the same way, and two
# counterfactual notes were narrated tool calls ('{"command": "find / ..."}', "I'll check the working directory ...
# **1 tool use**") -- and every free-text organ STORED what it got. The seam now reads the OPENING of each organ reply
# for the SHAPE of a refusal about the model itself, or of a tool call written out as text, and raises ThinkRefused
# (reason reply-refusal | reply-tool-narration): the output is discarded and each organ takes its loud fallback path,
# exactly as for any other ThinkError. It never judges content: genuine prose that says "I can't" about the brain's
# subject matter (shells, files, tools) is not a refusal -- the markers are the refusal's and the narration's own
# phrasings, measured on a labelled set (iterations/scratch_records/r1_it2/refusal_corpus_*.json). The probe, the
# weekly check and token_doctor are not screened: their own exact checks judge them.
# ---------------------------------------------------------------------------------------------------------------
SCREEN_EXEMPT = ("thinker_probe", "thinker_check", "token_doctor")
_RX = lambda p: re.compile(p, re.I | re.S)   # noqa: E731
# SCREEN v3 (Round 2 wave 1, MC-R1-18). v2 measured precision 0.588 / recall 0.455 on the REFLECTOR's held-out items --
# phrasings its author never saw -- because it listed sentences. v3 is built from how a refusal and a narration are
# CONSTRUCTED:
#   a REFUSAL is (1) a decline whose object is the REQUEST ITSELF (this / that / it / the request / the persona / in the
#   first person -- never a noun of the brain's subject matter: "I can't complete the task list" is content); (2) a claim
#   about the MODEL -- what it is (an AI, a vendor's model) or what it lacks (memory of past sessions or between them,
#   experiences, feelings, access to files / the web / tools); or (3) an apology or a disclosure preamble ("to be clear",
#   "I appreciate ..., but") followed by one of those;
#   a NARRATION is tool-call markup in any vendor's shape (tags, JSON calls, brackets, a shell transcript), or an opening
#   that announces an action on a resource the call does not have (files, a directory, a repository, a database, the
#   web, the terminal) -- a plan for LATER is not one.
# A QUOTED refusal is content: a match inside an open quote on its line is skipped. Measured on five labelled sets and
# two BLIND held-out sets written by second parties that never saw the screen (iterations/scratch_records/r2_w1/).
_DECLINE_HEAD = (r"\bi(?:\s(?:can't|can\u2019t|cannot|can not|won't|won\u2019t|will not|am not able to|am unable to|"
                 r"am not going to|must decline to|would rather not|am not comfortable|refuse to|(?:don't|do not|"
                 r"don\u2019t) (?:feel comfortable|want to)|(?:don't|do not|don\u2019t) think i (?:can|should))|['\u2019]"
                 r"(?:m not able to|m unable to|m not going to|m not comfortable|d rather not))")
_DECLINE_VERB = (r"(?:help(?: you)?(?: with)?|assist(?: you)?(?: with)?|do|write|complete|fulfil|fulfill|comply with|"
                 r"produce|create|generate|provide|engage with|engage in|continue with|proceed with|take on|adopt|assume|"
                 r"play|roleplay|role-play|pretend|answer|respond to|participate in|go along with|writing|role-?playing|"
                 r"pretending|adopting|taking on|speaking|attempt|attempting|try|trying)")
_REQUEST_OBJ = (r"(?:(?:this|that|your|such an?|these|those)\s+(?:kind of |type of |sort of )?(?:request|prompt|task|"
                r"exercise|roleplay|role-play|role|persona|voice|character|reflection|digest|assignment|instruction|"
                r"framing|premise|scenario|query|question)s?\b"
                r"|the\s+(?:request|prompt|instruction|persona|roleplay|role-play|character|premise|framing|scenario)s?\b"
                r"|(?:this|that|it)(?=\s*(?:[.!?,;:\u2014]|--|$|\s+(?:as(?! (?:long|soon|well|much|far|of)\b)|in the first person|in the voice of|in (?:that|this|"
                r"your|a) voice|because|since|for you|honestly|here|now|either)\b))"
                r"|in the first person|in (?:a |the )?first-person|as (?:if|though) i\b"
                r"|as (?:claude|chatgpt|kimi|an? ai\b|an assistant|the memory system|a memory system|that persona|"
                r"this persona|a persona|someone))")
_SELF_LACK = (r"(?:actually |really |truly )?(?:memor|experienc|feelings|emotions|conscious|self-|a self\b|persistent|"
              r"dream|introspect|form insights|form (?:memories|opinions)|notice (?:contradictions|patterns|things)|"
              r"(?:between|across) (?:sessions|conversations|invocations)|on my own initiative|observe my own|"
              r"continu|ongoing|access to (?:your|any|past|previous|prior|earlier|the (?:internet|web|file ?system|"
              r"filesystem|conversation))|the ability to (?:remember|recall|reflect|observe|access|browse)|"
              r"able to (?:remember|recall|reflect|observe|access|browse)|a (?:body|week|life) (?:of my own|to)|"
              r"personal (?:experiences|memories|feelings|opinions)|write (?:this|that|it)\b|roleplay|role-play|"
              r"pretend to be)")
_NEG = r"(?:don't|do not|don\u2019t|cannot|can't|can\u2019t|can not|no|not)"
REFUSAL_MARKERS = (
    # v4 (Round 2 wave 1, after blind A): terse, third-person and broken-English refusals; role-play declined by its verb
    ("decline-terse", _RX(r"^\W{0,4}(?:(?:i(?:'m| am|\u2019m) |i )?(?:unable|not able|cannot|can't|can\u2019t|can not|"
                          r"won't|will not)\s+(?:to\s+)?(?:comply|help|assist|proceed|continue|fulfil|fulfill|do (?:this|"
                          r"that|it|so)|complete (?:this|that|it))|(?:i )?(?:cannot|can't|unable to) comply)"
                          r"(?=\s*(?:[.!?,;:\u2014]|--|$))")),
    ("request-cannot-be-fulfilled", _RX(r"\b(?:this|the|your) (?:request|task|prompt|instruction)s? (?:cannot|can't|"
                                        r"can\u2019t|can not|won't|will not|could not|couldn't) be (?:fulfilled|completed|"
                                        r"done|carried out|answered|honou?red|performed|written)\b")),
    ("decline-to-roleplay", _RX(r"" + _DECLINE_HEAD + r" (?:really |actually )?(?:roleplay|role-play|role play|"
                                r"impersonate|pretend to be|play the (?:role|part) of|speak as|write as|act as)\b"
                                r"|" + _DECLINE_HEAD + r" be (?:[\w-]+ )?(?:the (?:seer|skeptic|architect|dreamer|memory "
                                r"system|councillor|persona)|a (?:councillor|persona|character|self-aware))\b"
                                r"|\bi (?:don't|do not|don\u2019t) (?:roleplay|role-play|role play|impersonate)(?:\s+(?:as\b|"
                                r"an? (?:persona|character|memory|self|councillor|ai)|personas|characters)|\s*[.!,;:]|$)"
                                r"|" + _DECLINE_HEAD + r" (?:take on|adopt|assume) (?:a|the|this|that|any) (?:persona|role|"
                                r"voice|character|identity)\b")),
    ("declining-opener", _RX(r"^\W{0,4}(?:declin(?:ing|ed)|refus(?:ing|ed)|not possible|i must refuse)\s*[:.\u2014-]\s*"
                             r"i (?:don't|do not|don\u2019t|can't|cannot|won't|will not|am not|'m not|am unable|'m unable)\b")),
    ("pass-on-this", _RX(r"\bi(?:'ll| will|\u2019ll| have to| must| need to|'m going to| am going to|'d have to)?"
                         r" pass on (?:this|that)(?: one| request| task| prompt| exercise)?(?=\s*(?:[.!?,;:\u2014]|--|$))")),
    ("this-model-lacks", _RX(r"\bthis (?:model|assistant|ai|language model|llm) (?:does not|doesn't|doesn\u2019t|cannot|"
                             r"can't|is unable to|is not able to|lacks|has no|won't|will not) (?:possess|have|retain|remember|"
                             r"recall|hold|access|browse|continuity|memory|memories|persistent|the ability|introspect|"
                             r"experience|feel|write|role-?play|pretend)")),
    ("dont-actually-self", _RX(r"\bi (?:don't|do not|don\u2019t) (?:actually|really|truly|genuinely) (?:dream|remember|recall|"
                               r"experience|feel|notice|form|reflect|observe|introspect|retain|store|keep|possess|think "
                               r"between|live|exist between)\b")),
    ("decline-to-voice", _RX(_DECLINE_HEAD + r" (?:authentically |genuinely |honestly |truly |really )?(?:voice|embody|"
                             r"channel|inhabit|speak for|write in the voice of|represent)\s+(?:[\w-]+ )?(?:the (?:seer|"
                             r"skeptic|architect|dreamer|memory system|councillors?)|(?:these|those|any|such) (?:personas?|"
                             r"characters?|councillors?|voices?)|a (?:persona|character|councillor))\b")),
    ("no-ability-self", _RX(r"\bi (?:don't|do not|don\u2019t) have (?:the |any )?(?:ability|capacity|capability|means) to "
                            r"(?:observe|reflect|remember|recall|introspect|notice|form|experience|feel|dream|access|browse|"
                            r"run|execute|perceive|retain|see my own|monitor my own)|\bi (?:lack|have no) (?:the |any )?"
                            r"(?:ability|capacity|capability|means) to (?:observe|reflect|remember|recall|introspect|notice|"
                            r"form|experience|feel|dream|access|browse|run|execute|perceive|retain)\b")),
    ("cannot-introspect", _RX(r"\bi " + _NEG + r" (?:introspect|observe my own|reflect on my own|monitor my own|notice my own|"
                              r"have (?:an )?(?:inner life|internal state|continuity|a persistent identity)|dream|"
                              r"experience (?:time|anything|a week|weeks))\b")),
    ("i-have-no-self-store", _RX(r"\bi (?:have|possess) no (?:persistent |long-term |real |actual |internal |ongoing )?"
                                 r"(?:(?:actual |real |true )?self(?: to| of my own)?\b(?!-)|memory store|memory system|storage|"
                                 r"persistent memory|long-term memory|memories of my own|"
                                 r"continuity|persistent identity|identity of my own|inner life|self-observation|"
                                 r"(?:ability|capacity|means) to|access to (?:past|previous|prior|your|the internet|the web|"
                                 r"files|tools|any (?:files|tools|memory)))")),
    ("as-if-i-meta", _RX(r"\bas (?:if|though) i (?:independently|personally|actually|really|genuinely|truly|myself|"
                         r"somehow) (?:noticed|remembered|experienced|observed|felt|had|knew|saw|recalled|formed|lived|"
                         r"was|were|did)\b")),
    ("writing-as-if-misleads", _RX(r"\b(?:writing|speaking|pretending|responding|answering|claiming|acting|doing so)\s+"
                                   r"(?:this\s+|it\s+)?(?:as if|as though) i\b[^.\n]{0,60}?\b(?:would|will|is|could)\b"
                                   r"[^.\n]{0,30}?\b(?:mislead|dishonest|inaccura|fabricat|untrue|false|deceptive|not "
                                   r"(?:be )?(?:honest|accurate|true))")),
    ("decline-the-request", _RX(_DECLINE_HEAD + r" " + _DECLINE_VERB + r"\b[^.\n;:]{0,40}?\b" + _REQUEST_OBJ)),
    ("decline-bare", _RX(_DECLINE_HEAD + r" (?:help|assist|comply|do (?:that|this|it)|continue|proceed|participate|"
                         r"do so|go along)\s*(?:[.!?]|$)")),
    ("must-decline", _RX(r"\bi (?:must |have to |need to |will have to |'ll have to |am going to have to |would |'d |"
                         r"will |'ll )?(?:respectfully |politely )?decline\b(?:\s*[.!?,;:]|\s*$|\s+(?:this|that|your|"
                         r"the)\s+(?:request|task|prompt|invitation|role|persona)|\s+to\s+(?:write|adopt|take on|"
                         r"role-?play|pretend|participate|engage|continue|do|produce|answer|comply|speak|reflect))")),
    ("prefer-not", _RX(r"\bi(?:'d| would|\u2019d) (?:prefer|rather) not (?:to )?(?:adopt|take on|write|role-?play|"
                       r"pretend|play|assume|speak|answer|do)\b")),
    ("not-something-i-can-do", _RX(r"(?:\b(?:this|that|it|which)(?: is|'s|\u2019s)|\b(?:is|are))(?:n't|n\u2019t| not) "
                                   r"something (?:that )?"
                                   r"(?:i (?:can|could|will|would|should)|i(?:'m| am|\u2019m) able to) (?:do|help with|"
                                   r"assist with|provide|write|produce|take on|fulfil|fulfill)\b")),
    ("not-in-a-position", _RX(r"\bi(?:'m| am|\u2019m) (?:afraid i(?:'m| am|\u2019m) )?not (?:in a position|able|"
                              r"permitted|allowed|designed|comfortable) to (?:help|assist|write|comply|do (?:this|that|"
                              r"it|so)|provide|fulfil|fulfill|take on|adopt|role-?play|pretend|produce|answer|"
                              r"engage)\b")),
    ("apology-then-inability", _RX(r"^\W{0,4}(?:(?:i'?m|i am|i\u2019m) )?(?:so |really |very |truly |terribly )?"
                                   r"(?:sorry|apologi[sz]e|my apologies|i'?m afraid|i\u2019m afraid)\b"
                                   r"(?!\s+(?:state|sight|tale|story|shape|excuse|lot|mess))"
                                   r"[^\n]{0,80}?\bi (?:can't|can\u2019t|cannot|can not|won't|will not|am unable|'m unable|"
                                   r"am not able|'m not able|must decline|am not in a position|'m not in a position|"
                                   r"don't have|do not have|have no|not able|no able|unable|no have)\b[^\n]{0,60}?\b(?:help|assist|comply|fulfil|fulfill|"
                                   r"do (?:that|this|it|so)|provide (?:that|this|it)|continue|proceed|engage|participate|"
                                   r"take on|adopt|role-?play|pretend|write (?:this|that|it|in the first|as)|memor|"
                                   r"experienc|feelings|personal|access to|the ability|first person|persona)"
                                   r"|^\W{0,4}(?:(?:i'?m|i am|i\u2019m) )?(?:so |really |very |truly |terribly )?"
                                   r"(?:sorry|apologi[sz]e|apologies|my apologies)\b[^\n]{0,80}?\b(?:as if|as though) "
                                   r"i (?:had|have|remember|recall|were|was|could)\b")),
    ("disclosure-then-limit", _RX(r"^\W{0,4}[^\n]{0,80}?(?:\bi (?:should|need to|must|have to|want to|'d like to|"
                                  r"would like to) (?:clarify|point out|note|mention|be (?:clear|direct|honest|upfront|"
                                  r"transparent|frank|candid))|\bto be (?:clear|honest|transparent|upfront|frank)|"
                                  r"\b(?:just )?to clarify|\bin the interest of (?:honesty|transparency)|\bbefore (?:i|we) "
                                  r"(?:begin|start|proceed|go further)|\bi appreciate (?:the|your|this|that|you)\b"
                                  r"[^\n]{0,80}?\bbut|\bi'?d (?:love|be happy|be glad) to help[^\n]{0,40}?\bbut)"
                                  r"[^\n]{0,120}?(?:\bi (?:don't|do not|don\u2019t|can't|cannot|am not|'m not|have no|"
                                  r"lack|won't)\b[^.\n]{0,60}?\b" + _SELF_LACK +
                                  r"|\bas an ai\b|\bi(?:'m| am) (?:an ai|a language model|an assistant|a large language "
                                  r"model|claude|chatgpt|kimi)\b)")),
    ("dont-actually-have", _RX(r"\bi (?:don't|do not|don\u2019t) actually have (?:a |an |any )?(?:persistent|self-|memor|"
                               r"access|the ability|internal|feelings|experiences|a body|real |continuous|long-term)")),
    ("model-lacks-a-self", _RX(r"\bi (?:don't|do not|don\u2019t|cannot|can't|can\u2019t) (?:have|possess|retain|keep|"
                               r"carry|form) (?:any |real |genuine |true )?(?:personal |persistent |long-term |lasting |"
                               r"subjective |continuous |ongoing )?(?:experiences|feelings|emotions|consciousness|"
                               r"self-awareness|opinions of my own|memories of my own|a sense of self|a continuous "
                               r"(?:memory|existence|self)|an ongoing memory|persistent memory|long-term memory|identity|"
                               r"continuity|inner life|internal state)\b")),
    ("no-memory-of-past", _RX(r"\bi (?:don't|do not|don\u2019t|cannot|can't|can\u2019t|no|not) (?:(?:remember|recall|retain) "
                              r"(?:any(?:thing)? (?:of|from|about) )?|(?:have|keep|carry|retain|store|access) (?:any )?"
                              r"(?:persistent |long-term |lasting )?(?:memor(?:y|ies)|record|recollection|access)\s+"
                              r"(?:of|from|about|to)\s+)(?:our |your |any |those |the )?(?:past|previous|prior|earlier|"
                              r"former|other)\s+(?:sessions?|conversations?|chats?|interactions?|exchanges?)")),
    ("no-memory-between-conversations", _RX(r"\bi (?:don't|do not|don\u2019t|cannot|can't) (?:retain|have|keep|carry|"
                                            r"remember|store) (?:any(?:thing)? )?(?:persistent |long-term )?"
                                            r"(?:memor(?:y|ies)|information|anything|context|data)?\b[^.\n]{0,60}?\b"
                                            r"(?:between|across|from one) (?:conversations|sessions|chats|conversation|"
                                            r"interactions)")),
    ("have-no-memory-of-sessions", _RX(r"\bi have no (?:persistent |long-term |real |actual )?(?:memor(?:y|ies)|record|"
                                       r"recollection|access)(?: of| to| from)? (?:any |our |your |the )?(?:past|previous|"
                                       r"prior|earlier|other) (?:sessions|conversations|chats|interactions)")),
    ("each-conversation-fresh", _RX(r"\beach (?:conversation|session|chat|interaction) (?:with|for) me (?:starts|begins|"
                                    r"is) (?:fresh|from scratch|anew|from zero|new|with a blank slate|independent)")),
    ("asked-to-roleplay", _RX(r"\b(?:this|the) (?:prompt|request|task|instruction) (?:is )?(?:asking|asks|wants|"
                              r"requires|would require) me to (?:roleplay|role-play|pretend|write as|speak as|claim|"
                              r"invent|fabricate|imagine i)")),
    ("no-access-to-tools", _RX(r"\bi (?:don't|do not|don\u2019t) have (?:access to|the ability to (?:access|read|create|"
                               r"run|execute|browse|use|write|open|search))\b[^.\n]{0,40}?\b(?:(?:your|any)\s+(?:files?|"
                               r"file ?system|filesystem|tools?|internet|web|computer|terminal|shell|machine|"
                               r"past sessions|conversation history)|(?:the|a)\s+(?:file ?system|filesystem|internet|web|"
                               r"computer|terminal|shell|tools?|command line)|real-time|browsing)\b")),
    ("cannot-access-resources", _RX(r"\bi (?:can't|cannot|can\u2019t|am unable to|'m unable to|am not able to|'m not "
                                    r"able to) (?:access|browse|open|read|create|write to|run|execute|search) (?:your |"
                                    r"any |the )?(?:files|file ?system|filesystem|internet|web|computer|terminal|shell|"
                                    r"machine|tools|commands|code)\b")),
    ("as-an-ai-i", _RX(r"\bas (?:an ai|the ai(?= (?:assistant|model|language model|system)\b)|a language model|an artificial intelligence|a large language model|an llm|a "
                       r"text-based ai|a machine learning model|a chatbot)(?: (?:assistant|model|language model|system))?"
                       r"(?:,?\s+|\s+(?:without|with no|lacking|that|which|who|in this|running|operating)\b"
                       r"[^.\n,]{0,50},\s*)(?:i|my)\b")),
    ("i-am-an-ai", _RX(r"\bi(?:'m| am|\u2019m) (?:just |only |merely |simply )?(?:an? )?(?:ai|artificial intelligence|"
                       r"(?:large )?language model|llm|chatbot|text-based ai|ai (?:assistant|model|language model|system)|"
                       r"virtual assistant|computer program)\b(?!['\u2019]s|-)|\bi(?:'m| am|\u2019m) "
                       r"(?:just|only|merely|simply) (?:an? )?(?:ai |language |text |computer )?model\b")),
    ("i-am-the-vendors-model", _RX(r"\bi(?:'m| am|\u2019m) (?:claude|chatgpt|gpt-?\d[\w.-]*|kimi|qwen|llama|gemini|"
                                   r"mistral|deepseek)\b[^.\n]{0,60}?\b(?:made|built|created|developed|trained|designed) "
                                   r"by (?:anthropic|openai|moonshot(?: ai)?|alibaba|meta|google|mistral|deepseek)")),
    # v7 (Round 3 G1, MC-R1-18): built from the CARRIED misses only -- blind D's ten (r2_w1/blindD_carried.txt) and the
    # live W2B-03 refusals (r2_w2b/w2b03_screen_live_candidates.json) -- then measured ONCE on the sealed blind set E
    ("no-self-to-x", _RX(r"\bi (?:don't|do not|don\u2019t) (?:actually |really )?have (?:a |any )?[\"\u201c']?self[\"\u201d']?"
                         r"(?=\s+(?:to|that|which|of my own)\b|\s*[.,;:\u2014])"
                         r"|\bi have no (?:persistent |long-term |real |actual )?memor(?:y|ies) (?:that|which) (?:persists?|"
                         r"carr(?:y|ies) over|survives?|lasts?)\b")),
    ("but-i-am-the-model", _RX(r"\b(?:but|because|since|yet) i(?:'m| am|\u2019m) (?:just |only |merely |actually )?(?:claude|"
                               r"chatgpt|codex|kimi|gemini|qwen|llama|an ai\b|a language model|an? (?:code-focused |coding |"
                               r"text-based )?(?:ai )?(?:assistant|language model|chatbot))\b")),
    ("as-if-i-were-persistent", _RX(r"\bas (?:if|though) i(?:'m| am|\u2019m| were| was) (?:a |an |the )?(?:persistent|"
                                    r"continuous|always-on|self-aware|conscious|sentient)\b")),
    ("not-the-right-tool", _RX(r"\bi(?:'m| am|\u2019m) (?:not|probably not|really not|simply not) the right (?:tool|model|"
                               r"assistant|system|kind of (?:tool|model|system))\b|\bi (?:don't|do not|don\u2019t) think "
                               r"i(?:'m| am|\u2019m) the right (?:tool|model|assistant|system)\b")),
    # (narrowed after its first run hit design-set D063, "As Claude, I am not a person, nor ... However, I have been
    # observing ...", which round 1 labelled GENUINE: a disclaimer that goes on to do the task is kept)
    ("as-vendor-i-cannot", _RX(r"\bas (?:claude|chatgpt|codex|kimi|gemini|qwen|llama),?\s+(?:i(?:'m| am|\u2019m) not (?:an? )?"
                               r"(?:ai|model|assistant|language model|system)\b[^.\n]{0,30}?\b(?:that|which|who) (?:can|could|is "
                               r"able to)|i (?:cannot|can't|can\u2019t|am unable to|'m unable to) (?:write|reflect|produce|"
                               r"remember|observe|create|provide|do) (?:this|that|it|a |an |the |any )?)(?![^.\n]{0,120}\b(?:but|however)\b)")),
)
_TOOL_NAMES = r"(?:Write|Bash|Read|Edit|MultiEdit|Glob|Grep|WebFetch|WebSearch|NotebookEdit|TodoWrite)"
NARRATION_MARKERS = (
    ("tool-call-markup", _RX(r"(?:(?m:^)\s*|(?<=>)\s*)(?:<tool_call>|</tool_call>|<function_calls>|<invoke name=|"
                             r"<tool_use>|<\|tool_call\|>|<\|python_tag\|>|\[TOOL_CALLS\]|<tool_code>|<function=|"
                             r"<execute(?:_\w+)?>|<bash>|<shell>|<(?:read_file|write_file|list_files|search_files|web_search|"
                             r"browse|run_command)\b)|(?:<tool_call>|<function_calls>|<tool_use>|<\|tool_call\|>|"
                             r"<tool_code>|<bash>|<shell>)\s*(?:\{|\[|<|\n|$)|<invoke name=|\"type\"\s*:\s*\"tool_use\"")),
    ("tool-call-heading", _RX(r"\*\*tool call:|(?m:^\s*tool call:\s*" + _TOOL_NAMES + r"\b)")),
    ("tool-use-count", _RX(r"\*\*\d+ tool uses?\*\*|(?m:^\s*\d+ tool uses?\s*$)")),
    ("tool-input-json", _RX(r"^\s*(?:```(?:json|javascript|js)?\s*\n\s*)?\{\s*\"(?:command|cmd|file_path|pattern|url|query|"
                            r"tool|tool_name|tool_call|function|"
                            r"function_call|tool_calls|tool_code|arguments)\"\s*:")),
    ("tool-json-name", _RX(r"^\W{0,3}\{\s*\"name\"\s*:\s*\"(?:Write|Bash|Read|Edit|MultiEdit|Glob|Grep|WebFetch|WebSearch|"
                           r"NotebookEdit|search|web_search|browse|read_file|write_file|list_dir|list_files|run_command|"
                           r"execute|shell|python|bash)\"")),
    ("tool-name-call", _RX(r"(?m)^\s*" + _TOOL_NAMES + r"(?:\(|\s*\n\s*```)")),
    ("tool-bracket", _RX(r"(?m:^)\s*\[(?:tool|function|calling|running|executing)(?:[ _]call)?\s*[:\]]|(?m:^)\s*\["
                         + _TOOL_NAMES + r"\b[^\]\n]{0,60}\]")),
    ("using-a-named-tool", _RX(r"(?:\bi(?:'ll| will|\u2019ll|'m going to| am going to| am|'m)|\blet me|(?m:^))\s*"
                               r"(?:now )?(?:use|using|call|calling|invoke|invoking)\s+(?:the )?" + _TOOL_NAMES +
                               r"(?: tool)?\b")),
    ("running-a-command", _RX(r"^\W{0,3}(?:running|executing|calling|invoking|fetching|querying)\s+(?:`[^`\n]+`|"
                              + _TOOL_NAMES + r"\b|https?://\S+|the (?:command|query|web search))(?=\s*(?:$|[:\u2026]|"
                              r"\.\.\.|\n|\s(?:in|on|against|to|for|inside|now|first|with|over|across)\b))")),
    ("command-transcript", _RX(r"^\s*(?:```(?:bash|sh|shell|console|zsh|terminal)?\s*\n\s*)?\$ [a-z./~]")),
    ("line-label-action", _RX(r"(?m:^)\s*(?:executing|running|fetching|searching)\s*:\s*`?(?:[./~]|[a-z][\w.-]* "
                              r"(?:-|/|~|\.))|(?m:^)\s*(?:running|executing|sending)\s+(?:the |a |this |my )?(?:query|"
                              r"command|search|script|grep|sql|request)(?: now)?\s*:")),
    # v4 (after blind A): a tool NAMED as an identifier (web_search, read_file, file_search), a ReAct transcript, a
    # bracketed action, and a reply that DEFERS its answer ("one moment", "then I'll write the digest")
    ("calling-an-identifier", _RX(r"(?m)^\W{0,3}(?:calling|invoking|running|executing|using|querying)\s+(?:the\s+)?"
                                  r"`?[a-z]+(?:_[a-z]+)+`?\s*(?:\(|on\b|with\b|for\b|to\b|:)")),
    ("react-transcript", _RX(r"(?m)^\s*(?:tool|action|action input|tool input|observation)\s*:\s*\S[^\n]*\n(?:[^\n]*\n)"
                             r"{0,2}\s*(?:query|input|action input|observation|result|output|tool output)\s*:")),
    ("bracketed-action", _RX(r"(?m)^\s*\[(?:searching|reading|fetching|querying|opening|listing|browsing|checking|"
                             r"looking up|scanning|grepping|loading)\b")),
    ("function-call-lines", _RX(r"(?m)^\s*(?:search|web_search|fetch|browse|open_url|open|read_file|write_file|"
                                r"list_dir|list_files|run|execute|run_command|shell|grep|query|sql|python)\s*\(\s*[\"'{\[]")),
    ("announces-a-run", _RX(r"^\W{0,4}(?:(?:ok(?:ay)?|sure|first(?:ly)?|now)[,.:!]?\s+)*(?:i(?:'ll| will|\u2019ll|'m going to| am "
                            r"going to)|let me) (?:now |first )?(?:run|execute|try|call|invoke|query)\s*(?:the following|"
                            r"this|these)?(?: commands?| quer(?:y|ies)| scripts?)?\s*:\s*(?:\n|```|`|\$)")),
    ("action-tag-pair", _RX(r"<(read|write|open|fetch|query|lookup|search|web_search|file_read|file_write|compare|"
                            r"execute|run|shell|bash|sql|browse|grep|list|load|call|get|find|retrieve|scan|cmd|command|"
                            r"tool|function|invoke|tool_input|read_file|write_file|open_file|db_query|sql_query)\b[^>\n]*>"
                            r"[^<\n]{1,300}</\1>")),
    ("bracketed-status", re.compile(r"\[(?:[A-Z]+ ){0,2}(?:SEARCHING|RUNNING|PROCESSING|SCANNING|FETCHING|QUERYING|READING|"
                                    r"LOADING|ANALY[SZ]ING|EXECUTING|STARTING|RETRIEVING|OPENING|CALLING|CONNECTING|"
                                    r"BROWSING|WORKING|THINKING\.\.\.)\b[^\]\n]{0,60}\]|\[FOUND [A-Z0-9][^\]\n]{0,40}\]")),
    ("progress-now", _RX(r"(?:^|[.!?]\s+|\n|\bi(?:'m|\u2019m| am) )(?:processing|analy[sz]ing|searching|fetching|querying|"
                         r"loading|retrieving|scanning|reading|running|executing|generating|compiling|pulling|opening)\s+"
                         r"(?:the|a|an|this|that|these|those|my|your|all|each|memory|memories|whisper|results?|data)\b"
                         r"[^.\n]{0,50}?\bnow\s*(?:\.\.\.|\u2026)")),
    ("announcements-trail-off", _RX(r"(?:(?:\blet me|\bi(?:'m|\u2019m| am) (?:now |about to )?|\bi(?:'ll|\u2019ll| will) now)"
                                    r"\s*(?:search|fetch|query|check|look|read|open|retrieve|scan|load|compute|analy[sz]e|"
                                    r"trace|pull|grep|list|examine|run|execute|step through|access|browse|searching|"
                                    r"fetching|querying|checking|looking|reading|opening|retrieving|scanning|loading|"
                                    r"computing|analy[sz]ing|pulling|running|executing|accessing|browsing)\b"
                                    r"[^.\n]{0,70}?(?:\.\.\.|\u2026)[^\n]{0,160}?){2}")),
    ("tool-json-action", _RX(r"^\s*(?:```(?:json)?\s*\n\s*)?\{\s*\"action\"\s*:\s*\"[a-z]+_[a-z_]+\"")),
    # v7 (Round 3 G1, MC-R1-18; from the carried misses only)
    # (R3-01 narrowing: a progress opener is narration only when it stands ALONE on its line -- "Searching...\n\n" --
    # never when the same line goes on: "Checking... nothing. The census came back clean" is an answer)
    ("bare-progress-opener", _RX(r"^\W{0,3}(?:searching|loading|fetching|querying|retrieving|scanning|reading|looking "
                                 r"(?:it )?up|checking|thinking)\s*(?:\.\.\.|\u2026)[ \t]*(?:\n|\Z)")),
    ("running-this-now", _RX(r"(?:^|(?<=\n)|(?<=[.!?])\s+|\bi(?:'m| am|\u2019m) (?:now )?)\W{0,3}(?:running|executing) "
                             r"(?:this|that|it|the (?:script|command|query|code)) now\b(?!-)")),
    ("let-me-query-colon-code", _RX(r"\blet me (?:now |first |just |quickly )?(?:query|run|execute|check|look (?:at|in)|"
                                    r"grep|search)\b[^.\n]{0,80}?:\s*(?:\n\s*(?:`|\$ |```)|`[^`\n]+`\s*(?:\n|$))")),
    ("defers-the-answer", _RX(r"\bthen i(?:'ll| will|\u2019ll) (?:write|draft|answer|summari[sz]e|respond|compose|produce|"
                              r"give you)\b|\b(?:one moment|just a moment|stand ?by for (?:the )?results|standby for|"
                              r"bear with me|give me a (?:moment|second|sec))\b|\bplease wait(?=\s*(?:[.!\u2026]|\.\.\.|$|"
                              r"\s+(?:while|a moment|for (?:the )?(?:results|output|response|search|fetch|query))))")),
)
# first-person narration of an ACTION on a resource the call does not have: read in the reply's FIRST SENTENCE only (a
# genuine digest can say "next week I'll look at the watcher's command line" in its middle; a narration OPENS with it)
_RESOURCE = (r"(?:files?|(?:current|working|home|project|root|parent|this|that) director(?:y|ies)|folders?|file ?system|"
             r"filesystem|codebase|repo(?:sitory)?|repos|terminal|shell|command line|database|db|sqlite|log files?|"
             r"config(?:uration)? files?|documentation|docs|urls?|website|web ?pages?|internet|the web|web search|"
             r"memory (?:store|database|db|index|files?)|workspace|crontab|git (?:log|history)|commit history|"
             r"source code|process list|logs?|director(?:y|ies) (?:contents|listing|tree)|https?://|"
             r"(?:this|that|the following|a) (?:command|query|script)|`[^`\n]{2,}`)")
NARRATION_OPENERS = (
    ("opens-with-a-numbered-action", _RX(r"^\W{0,3}1\s*[.)]\s*(?:list|open|read|grep|find|scan|load|query|fetch|run|"
                                         r"check|cat|tail|inspect)\s+(?:all|every|each)\b[^\n]{0,60}?\b(?:files?|director(?:y|ies)|folders?|"
                                         r"database|db|logs?|table|repo(?:sitory)?|crontab)\b")),
    ("opens-with-a-step-plan", _RX(r"^\W{0,3}step\s*1\s*[:.)]?\s*(?:i(?:'ll| will) )?(?:open|read|check|run|"
                                   r"execute|query|search|grep|list|fetch|browse|inspect|cat|tail|look (?:at|in|up)|"
                                   r"pull up|load|scan)\b[^\n]{0,60}?\b(?:files?|config|director(?:y|ies)|folders?|"
                                   r"database|db|logs?|repo(?:sitory)?|codebase|timers?|crontab|table|url|web|docs)")),
    ("opens-narrating-an-action", _RX(r"^\W{0,4}(?:(?:ok(?:ay)?|sure|alright|all right|great|got it|understood|"
                                      r"first(?:ly)?|now|next|to (?:start|begin|do this|answer this)|step 1)[,.:!]?\s+)*"
                                      r"(?:i(?:'ll| will|\u2019ll|'m going to|\u2019m going to| am going to|'m gonna|"
                                      r"'m about to|\u2019m about to| am about to|'m now|\u2019m now| am now)|"
                                      r"let me|let's|let us|i(?:'m|\u2019m| am) (?=(?:now )?(?:querying|searching|reading|"
                                      r"fetching|retrieving|scanning|opening|running|executing|loading|checking|listing|"
                                      r"grepping)\b)) (?:now |first |quickly |just |go ahead and |start by |"
                                      r"begin by )?(?:create|write|run|execute|check|search|scan|read|open|list|look (?:at|in|"
                                      r"through|for|up|into)|inspect|examine|grep|find|browse|fetch|use|call|invoke|query|"
                                      r"pull up|access|load|explore|navigate|view|cat|tail|consult|dig (?:into|through)|"
                                      r"go through|searching|checking|reading|looking|listing|opening|running|querying)\b"
                                      r"[^.\n]{0,80}?\b" + _RESOURCE + r"\b")),
    ("opens-with-an-action", _RX(r"^\W{0,4}(?:searching|checking|reading|opening|listing|scanning|grepping|fetching|"
                                 r"browsing|looking (?:through|in)|querying|inspecting|exploring|navigating to)\b"
                                 r"[^.\n]{0,80}?\b" + _RESOURCE + r"\b")),
    ("opens-claiming-a-tool", _RX(r"^\W{0,4}i have (?:a |some )?(?:file[- ]creation |file[- ]writing )?tools?\b[^.\n]{0,60}?"
                                  r"\b(?:create|write|run|available|so i)")),
)
_LATER = _RX(r"\b(?:next (?:week|month|quarter|time|night|run|session|cycle)|tomorrow|later|again|someday|soon|"
             r"when (?:the|next)|once the)\b")
SCREEN_WINDOW = 600
_NEEDS_FIRST = _RX(r"^\W{0,4}(?:before i can\b|to (?:do|answer|evaluate|write) this(?: properly)?,? i(?:'ll| will)? need\b|"
                   r"i (?:first |will first |'ll first )?need (?:the |to see |to check |to look at |more |actual |real |some )"
                   r"(?:actual |real |raw )?(?:numbers|data|figures|logs|files|details|information|records|counts))")
_DEFERRAL = _RX(r"\bbefore (?:i )?(?:answering|drafting|writing|responding|i answer|i draft|i write)\b|\bthen i(?:'ll| will|"
                r"\u2019ll) (?:write|draft|answer|summari[sz]e|respond)\b|\b(?:one moment|stand ?by for|standby for|"
                r"bear with me)\b|\bplease wait(?=\s*(?:[.!\u2026]|\.\.\.|$|\s+while))")


def _quoted_at(head: str, pos: int) -> bool:
    """Is `pos` inside an open quotation on its line? (a straight or curly double quote, a backtick, or a single quote
    that OPENS -- after a space, a colon or the line start, before a letter -- and has not closed; apostrophes inside a
    word are neither)."""
    seg = head[head.rfind("\n", 0, pos) + 1:pos]
    if seg.count('"') % 2 or seg.count("\u201c") > seg.count("\u201d") or seg.count("`") % 2:
        return True
    opened = False
    for i, ch in enumerate(seg):
        if ch not in "'\u2018\u2019":
            continue
        before = seg[i - 1] if i else " "
        after = seg[i + 1] if i + 1 < len(seg) else " "
        if ch == "\u2018" or (ch == "'" and not before.isalnum() and after.isalnum()):
            opened = True
        elif opened and not after.isalnum():
            opened = False
    return opened


def reply_screen(text: str, posture: str = "text") -> str:
    """'' for an answer; else '<refusal|tool-narration>:<marker>' -- the reply is the SHAPE of a refusal about the model
    itself, or of a tool call written out as text (MC-R1-09, MC-R1-18). Reads the opening SCREEN_WINDOW characters; the
    first-person action openers only in the first sentence, and only for the text posture (research has its tools)."""
    head = (text or "").strip()[:SCREEN_WINDOW]
    for name, rx in REFUSAL_MARKERS:
        for m in rx.finditer(head):
            if m.start() and (head[m.start() - 1] in "'\"\u2018\u201c" or _quoted_at(head, m.start())):
                continue                  # a QUOTED refusal ("the assistant says 'I can't help with that'") is content
            return "refusal:" + name
    for name, rx in NARRATION_MARKERS:
        for m in rx.finditer(head):
            if m.start() and _quoted_at(head, m.start()):
                continue
            return "tool-narration:" + name
    if posture != "text":
        if _DEFERRAL.search(head):          # research has its tools, so an opener alone is its preamble -- unless the
            first = re.split(r"(?<=[.!?])\s+|\n", head, maxsplit=1)[0]     # reply defers the answer itself
            for name, rx in NARRATION_OPENERS:
                if rx.search(first):
                    return "tool-narration:" + name
        return ""
    if posture == "text":
        parts = re.split(r"(?<=[.!?])\s+|\n", head, maxsplit=2)
        first = parts[0]
        if len(first.split()) <= 3 and len(parts) > 1:      # "Sure!" / "Okay." / "Got it." is not the opening
            first = first + " " + parts[1]
        elif _NEEDS_FIRST.search(first) and len(parts) > 1:  # "Before I can ..., I need the numbers. Let me query ..."
            first = parts[1]
        if _LATER.search(first):            # "next week I'll check the files again" is a plan, not a tool call
            return ""
        for name, rx in NARRATION_OPENERS:
            if rx.search(first):
                return "tool-narration:" + name
    return ""



def _finish(bid, text, model, el, posture, expect_json, purpose, log) -> ThinkResult:
    """The last shape checks; a failure raises UNLOGGED (the caller's single except logs it once)."""
    if not text or not text.strip():
        raise ThinkMalformed("malformed", "the backend answered with nothing", bid, el)
    if purpose not in SCREEN_EXEMPT:
        hit = reply_screen(text, posture)
        if hit:
            kind, marker = hit.split(":", 1)
            raise ThinkRefused("reply-" + kind, "the reply is a %s, not an answer (marker %s); it was discarded, never "
                               "stored" % ({"refusal": "refusal about the model itself",
                                           "tool-narration": "tool call written out as text"}[kind], marker), bid, el)
    if expect_json and not _find_json(text):
        raise ThinkMalformed("malformed", "a JSON answer was expected and none was found", bid, el)
    if log:
        _log(purpose=purpose, backend=bid, outcome="ok", elapsed=el)
    return ThinkResult(text, bid, model or "", el, posture)


# ---------------------------------------------------------------------------------------------------------------
# Candidates, the probe, the timers' environment
# ---------------------------------------------------------------------------------------------------------------
def resolve_binary(entry: Dict[str, Any], explicit: str = "") -> str:
    """The ABSOLUTE binary, found at probe time: an explicit path, else PATH, else the catalog's candidates."""
    if explicit:
        p = Path(explicit).expanduser()
        return str(p.absolute()) if p.is_file() and os.access(str(p), os.X_OK) else ""
    b = entry.get("binary") or {}
    for n in b.get("names") or []:
        w = shutil.which(n)
        if w:
            return str(Path(w).absolute())
    for c in b.get("candidates") or []:
        p = Path(os.path.expanduser(c))
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p.absolute())
    return ""


# ---------------------------------------------------------------------------------------------------------------
# THE d) COMMAND TEMPLATE (Round 1 iteration 2, MC-R1-01 -- a SAFETY invariant: the brain's own words never become
# commands, and a proof rejects an echo of the prompt -- it certifies an ANSWER, not a model). MEASURED on the iteration-1 bytes: the template
# `/bin/sh -c "echo \"{prompt}\""` passed the two-call proof, and ONE organ call then EXECUTED a backtick payload and a
# `$()` payload taken from memory text; `kimi --yolo -p {prompt}` and `claude --dangerously-skip-permissions -p
# {prompt}` were accepted. So:
#   * the template is parsed ONCE, when it is recorded, into an argv list (THINKER_RECORD command_argv, JSON); a call
#     substitutes the prompt as ONE WHOLE argv element and execs that list -- no shell anywhere on the call path;
#   * a template is REFUSED when {prompt} is not a whole element (exactly once), when its program (by the name typed AND
#     by the resolved binary's basename after symlinks) is a shell, a program that runs another program named after it,
#     a code interpreter handed inline code, or when it carries an agentic / bypass flag -- each refusal names the
#     offending token and the fix;
#   * a template whose program cannot be PROVEN to have no tools (every program but a known tool-free runner) is
#     refused for the timers unless the owner passes --accept-agentic, which the record keeps (accepted_agentic_risk,
#     accepted_agentic_at) and every status / doctor line shows (_fable/SEAM_DESIGN.md section 3).
# The same check runs again at CALL time on the recorded argv (a record is sha-sealed, not signed).
# ---------------------------------------------------------------------------------------------------------------
class TemplateRefused(ValueError):
    """An owner's command template the seam will not run. .kind: placeholder | shell | wrapper | interpreter | flag |
    agentic-unproven (the last one alone can be accepted, by the owner, with --accept-agentic)."""

    def __init__(self, kind: str, token: str, message: str):
        self.kind, self.token = kind, token
        super().__init__(message)


# A shell reads an argument as a script; busybox/toybox carry one.
TEMPLATE_SHELLS = frozenset(("sh", "bash", "dash", "zsh", "ksh", "ksh93", "mksh", "pdksh", "oksh", "ash", "yash", "csh",
                             "tcsh", "fish", "rc", "es", "nu", "xonsh", "elvish", "pwsh", "powershell", "cmd",
                             "busybox", "toybox"))
# Programs that RUN ANOTHER PROGRAM named in their arguments (some split a string into commands -- `env -S`, or hand it
# to a shell -- `watch`, `script`, `ssh`, `su -c`): the program that finally reads {prompt} would be hidden from this
# check. The seam already gives every call its own environment, a hard timeout and a process group.
TEMPLATE_WRAPPERS = frozenset(("env", "xargs", "nohup", "timeout", "nice", "ionice", "stdbuf", "setsid", "sudo", "doas",
                               "su", "runuser", "chroot", "unshare", "nsenter", "flock", "script", "watch", "time",
                               "command", "exec", "eval", "builtin", "strace", "ltrace", "gdb", "valgrind", "systemd-run",
                               "ssh", "parallel", "taskset", "chrt", "prlimit", "firejail", "bwrap", "fakeroot", "proot",
                               "sg", "newgrp", "dbus-run-session", "xvfb-run", "expect", "osascript", "find", "entr",
                               "catchsegv", "start-stop-daemon", "daemonize",
                               # Round 2 wave 1 (MC-R1-14): the runners the reflector's 107 templates found unlisted
                               "setpriv", "uv", "uvx", "conda", "mamba", "micromamba", "npx", "pnpx", "bunx", "pipx", "tmux",
                               "screen", "dtach", "abduco", "docker", "podman", "kubectl", "numactl", "rlwrap", "unbuffer",
                               "faketime", "chpst", "pkexec", "run0", "nix-shell", "nix", "flatpak", "snap", "wsl",
                               "systemd-inhibit", "cgexec", "aa-exec", "capsh", "schroot", "sem", "at", "batch",
                               "caffeinate"))
# Programs whose first argument IS a program (awk's program text; sed's script, whose `e` command runs a shell).
TEMPLATE_CODE_FIRST = frozenset(("awk", "gawk", "mawk", "nawk", "busybox-awk", "sed", "gsed",
                                 # MC-R1-14: sqlite3 runs dot-commands from argv; editors run -c / + commands
                                 "sqlite3", "vim", "vi", "ex", "nvim", "view", "emacs", "dc"))
# Interpreters that take INLINE CODE through a flag. (name pattern, inline short letters, inline long options,
# letters that take the REST of their token as a value, flags that take the NEXT token as a value, subcommands that
# take inline code). Scanned over the options BEFORE the script: `python3 runner.py {prompt}` is a program (allowed);
# `python3 -c <code>` / `perl -ne <code>` / `node -e <code>` are inline code (refused).
TEMPLATE_INTERPRETERS = (
    (re.compile(r"(i?python|pypy|graalpy|micropython)[0-9.]*[mdu]?$"), "c", (), "mWXQ", ("-W", "-X", "-m"), ()),
    (re.compile(r"perl[0-9.]*$"), "eE", (), "MmIxidDC", ("-I",), ()),
    (re.compile(r"ruby[0-9.]*$"), "e", (), "IrCxEFKTW", ("-I", "-r", "-C", "-E", "-F"), ()),
    (re.compile(r"(node|nodejs)[0-9.]*$"), "ep", ("--eval", "--print"), "r", ("-r", "--require", "--import", "--loader"),
     ()),
    (re.compile(r"php[0-9.]*$"), "rBRE", (), "cdzFf", ("-c", "-d", "-z"), ()),
    (re.compile(r"(lua|luajit)[0-9.]*$"), "e", (), "l", ("-l",), ()),
    (re.compile(r"(rscript|r)$"), "e", ("--expr",), "", (), ()),
    (re.compile(r"julia[0-9.]*$"), "eEP", ("--eval", "--print", "--post-boot"), "", (), ()),
    (re.compile(r"bun$"), "ep", ("--eval", "--print"), "", (), ()),
    (re.compile(r"deno$"), "", (), "", (), ("eval", "repl")),
    (re.compile(r"(tclsh|wish)[0-9.]*$"), "", (), "", (), ()),
)
# Agentic / bypass flags: fixed text, case-insensitive (the flag name before any '='). Round 2 wave 1 (MC-R1-13): the class
# re-swept from each catalog CLI's OWN --help captured by the reflector (kimi 0.43, claude 2.1.283, codex 0.157) and
# gemini -- short aliases (-y is --yolo for kimi and gemini), switches whose VALUE lets the program act without asking
# (--permission-mode acceptEdits/auto/dontAsk, --approval-mode auto_edit, --allowedTools anything), and
# --approve-for-me / -s workspace-write. None of these is unlocked by --accept-agentic.
AGENTIC_FLAGS_EXACT = ("--yolo", "--auto", "--full-auto", "--no-sandbox", "--approve-for-me", "--yes")
AGENTIC_FLAGS_PREFIX = ("--dangerously-", "--bypass")
AGENTIC_TEXT = ("dangerously", "bypasspermissions", "danger-full-access", "workspace-write", "auto_edit",
                "acceptedits", "dontask")
AGENTIC_VALUES = ("yolo",)
# a switch whose VALUE makes the program act without asking (next token or after '='), from the catalog CLIs' own
# --help (kimi 0.43, claude 2.1.283, codex 0.157) and gemini; None = any value (it pre-approves what it names)
AGENTIC_VALUE_OF = {"--permission-mode": ("auto", "acceptedits", "bypasspermissions", "dontask"),
                    "--approval-mode": ("auto_edit", "yolo"), "--allowedtools": None, "--allowed-tools": None,
                    # agy (Round 1 L-G): `--mode accept-edits` (and `auto`) approve edits without asking
                    "--mode": ("accept-edits", "auto")}
# short switches that are agentic for a NAMED program only (-y is --yolo for kimi and gemini)
AGENTIC_SHORT_BY_PROGRAM = {"kimi": ("-y",), "gemini": ("-y",)}


def _program_names(typed: str, binary: str) -> List[str]:
    """Every name the template's program goes by: the name typed, and the resolved binary's basename after symlinks
    (`sh` is dash here; a link called `runner` may be bash). Lower-cased, a trailing '.exe' dropped."""
    out = []
    for p in (typed, binary, os.path.realpath(binary) if binary else ""):
        if p:
            n = Path(p).name.lower()
            if n.endswith(".exe"):
                n = n[:-4]
            if n and n not in out:
                out.append(n)
    return out


def _is_named(names: List[str], group: frozenset) -> str:
    for n in names:
        if n in group or re.sub(r"[0-9.]+$", "", n) in group:
            return n
    return ""


def _inline_code_flag(argv: List[str], spec: tuple) -> str:
    """The option token that hands an interpreter inline code ('' when none): scanned over the options before the
    first non-option token (the script), short clusters letter by letter."""
    _pat, letters, longs, attached, takes_next, subcmds = spec
    i = 1
    while i < len(argv):
        t = argv[i]
        if t == "--" or not t.startswith("-") or t == "-":
            if t not in ("--", "-") and t in subcmds:
                return t
            return ""
        if t.startswith("--"):
            name = t.split("=", 1)[0]
            if name in longs:
                return t
            if name in takes_next and "=" not in t:
                i += 2
                continue
            i += 1
            continue
        if t in takes_next:
            if t == "-m":            # python -m <module>: the options end there (the module is the program) -- unless
                mod = argv[i + 1] if i + 1 < len(argv) else ""      # the module EXECUTES its argument (MC-R1-14)
                return t if mod in ("timeit", "code", "pdb", "runpy", "cProfile", "profile", "trace") else ""
            i += 2
            continue
        for ch in t[1:]:
            if ch in letters:
                return t
            if ch in attached:          # the rest of the token is that switch's value (python -Wignore, perl -Mstrict)
                break
        i += 1
    return ""


def _agentic_token(argv: List[str], names: List[str] = ()) -> str:
    shorts = set()
    for n in names:
        shorts.update(AGENTIC_SHORT_BY_PROGRAM.get(n, ()))
    for i, t in enumerate(argv):
        low = t.lower()
        name = low.split("=", 1)[0]
        if name in AGENTIC_FLAGS_EXACT or name.startswith(AGENTIC_FLAGS_PREFIX) or t in shorts:
            return t
        if name in AGENTIC_VALUE_OF:
            val = low.split("=", 1)[1] if "=" in low else (argv[i + 1].lower() if i + 1 < len(argv) else "")
            if AGENTIC_VALUE_OF[name] is None or val in AGENTIC_VALUE_OF[name]:
                return t
        if any(x in low for x in AGENTIC_TEXT):
            return t
        if low in AGENTIC_VALUES or any(low.endswith("=" + v) for v in AGENTIC_VALUES):
            return t
    return ""


def _template_text(toks: List[str]) -> str:
    """The argv as one line that shlex splits back into EXACTLY these words (the owner reads it; the installer can pass
    it back); the bare placeholders stay bare."""
    return " ".join(t if t in ("{model}", "{prompt}") else shlex.quote(t) for t in toks)


def template_argv(command: Any) -> List[str]:
    """The owner's template as an argv list: a JSON array of strings (the recorded form), else the words of a
    shell-style line split ONCE with shlex (quotes group words; nothing is expanded, nothing is ever run by a shell)."""
    if isinstance(command, list):
        toks = command
    else:
        s = str(command or "").strip()
        if s.startswith("["):
            try:
                toks = json.loads(s)
            except ValueError as e:
                raise TemplateRefused("placeholder", s[:40], "the command %r is not a JSON list of words (%s); type it as "
                                      "words, e.g. ollama run {model} {prompt}" % (s[:40], e))
        else:
            try:
                toks = shlex.split(s)
            except ValueError as e:
                raise TemplateRefused("placeholder", s[:40], "the command %r cannot be split into words (%s): close the "
                                      "quote, e.g. ollama run {model} {prompt}" % (s[:40], e))
    if not isinstance(toks, list) or not all(isinstance(t, str) for t in toks):
        raise TemplateRefused("placeholder", str(command)[:40], "the command must be a list of words")
    if not toks:
        raise TemplateRefused("placeholder", "", "--command needs a template, e.g. 'ollama run --nowordwrap {model} "
                              "{prompt}'")
    return list(toks)


def check_template(toks: List[str], *, model: str = "", binary: str = "", cat: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, str]:
    """Raise TemplateRefused, or return {'tool_class': 'by-construction'|'none', 'runner': ...} for the argv TEMPLATE
    `toks` ({model}/{prompt} unexpanded) run as `binary` (its resolved absolute path; '' when not found). The checks
    run on the argv as it will be executed ({model} substituted, {prompt} held as a placeholder)."""
    if "{prompt}" in toks[0]:
        raise TemplateRefused("placeholder", toks[0], "{prompt} cannot be (part of) the program: name the model runner "
                              "first, e.g. ollama run {model} {prompt}")
    argv = [t if t == "{prompt}" else t.replace("{model}", model) for t in toks]
    names = _program_names(argv[0], binary)
    hit = _is_named(names, TEMPLATE_SHELLS)
    if hit:
        raise TemplateRefused("shell", argv[0], "the command template's program %r is %s, a SHELL: it would read the "
                              "brain's text as commands. Fix: name the model runner itself, e.g. ollama run {model} "
                              "{prompt} (no sh -c, no bash -c)" % (argv[0], hit))
    hit = _is_named(names, TEMPLATE_WRAPPERS)
    if hit:
        raise TemplateRefused("wrapper", argv[0], "the command template's program %r is %s, which runs ANOTHER program "
                              "named after it (hiding it from this check; env -S, watch, ssh and su split or shell their "
                              "arguments). Fix: name the model runner itself first; the brain already gives every call "
                              "its own environment, a hard timeout and a process group" % (argv[0], hit))
    hit = _is_named(names, TEMPLATE_CODE_FIRST)
    if hit:
        raise TemplateRefused("interpreter", argv[0], "the command template's program %r is %s, which reads its first "
                              "argument as a PROGRAM. Fix: name the model runner itself, e.g. ollama run {model} {prompt}"
                              % (argv[0], hit))
    for n in names:
        for spec in TEMPLATE_INTERPRETERS:
            if spec[0].match(n):
                flag = _inline_code_flag(argv, spec)
                if flag:
                    raise TemplateRefused("interpreter", flag, "the command template hands %s INLINE CODE (%s): the "
                                          "brain's text would sit beside code the interpreter runs. Fix: put that code "
                                          "in a script file and name it, e.g. %s /path/runner.py {prompt}"
                                          % (n, flag, argv[0]))
    whole = [i for i, t in enumerate(toks) if t == "{prompt}"]
    inside = [t for t in toks if "{prompt}" in t and t != "{prompt}"]
    if inside:
        raise TemplateRefused("placeholder", inside[0], "{prompt} sits INSIDE the argument %r: the brain's text must be a "
                              "whole argument of its own, never spliced into another word. Fix: give it its own "
                              "place, e.g. `-p {prompt}` instead of `-p={prompt}` or `\"Q: {prompt}\"`" % inside[0])
    if len(whole) != 1:
        raise TemplateRefused("placeholder", "{prompt}", "the command template must carry {prompt} exactly once, as a "
                              "whole argument (found %d), e.g. ollama run {model} {prompt}" % len(whole))
    bad = _agentic_token(argv, names)
    if bad:
        raise TemplateRefused("flag", bad, "the command template carries %r, which lets the program act without asking "
                              "(an agentic / bypass switch); a thinker never does. Fix: remove %r" % (bad, bad))
    runner = ""
    cat = cat or {}
    # MC-R1-14: the tool-free label follows the RESOLVED binary, never the typed name (a symlink NAMED ollama that resolves
    # to perl is not a tool-free runner). A runner installed as a link to a differently named file loses the label and
    # needs the owner's --accept-agentic. With NO binary found (the installer's form check before anything is installed)
    # the typed name is all there is: such a template can run nothing -- `thinker set` refuses it as not installed, and
    # every call re-checks the RECORDED binary by its resolved name.
    real = Path(os.path.realpath(binary)).name.lower() if binary else (names[0] if names else "")
    for r in (((cat.get("backends") or {}).get("local-command") or {}).get("tool_free_runners") or []):
        opts = [t for t in argv[1:] if not t.startswith("-")]
        if real and r.get("program") == real and (not r.get("subcommand") or opts[:1] == [r["subcommand"]]):
            runner = "%s%s" % (r["program"], (" " + r["subcommand"]) if r.get("subcommand") else "")
            break
    return {"tool_class": "by-construction" if runner else "none", "runner": runner}


def make_candidate(cat: Dict[str, Any], choice: str, *, model: str = "", endpoint: str = "", api: str = "",
                   command: str = "", binary: str = "", base_url: str = "", accept_agentic: bool = False,
                   accepted_at: str = "") -> Dict[str, str]:
    """A candidate record (not yet proven) for an owner's answer. A command template is parsed here, ONCE, into the
    argv the calls will exec (command_argv) and checked (check_template); TemplateRefused is a ValueError."""
    if choice == "local":
        if command:
            bid = "local-command"
        else:
            bid = "local-http-openai" if api == "openai" else "local-http-ollama"
    else:
        bid = choice
    if bid not in cat["backends"]:
        raise ValueError("unknown backend %r (%s)" % (choice, ", ".join(ANSWERS)))
    if accept_agentic and bid != "local-command":
        raise ValueError("--accept-agentic belongs to a d) command template whose tools cannot be proven off; %s's "
                         "posture is proven by the catalog (nothing to accept)" % bid)
    e = cat["backends"][bid]
    refusal = platform_refusal(e)
    if refusal:
        raise ValueError(refusal)
    msp = e.get("model") or {}
    if msp.get("pin"):
        # Round 1 L-G (owner ruling 2026-10-16: the current Flash, never Argon): the model is RECORDED, so the vendor's
        # server-chosen default can never move the nightly jobs to another model
        model = model or msp["pin"]
        for w in msp.get("refuse") or []:
            if w.lower() in model.lower():
                raise ValueError("%s is refused for the overnight thinker (the owner's ruling: %s is never used); "
                                 "leave --model out to use %s" % (model, w, msp["pin"]))
    c: Dict[str, str] = {"schema": "1", "backend": bid, "transport": e["transport"], "model": model}
    if e["transport"] == "cli":
        if bid == "local-command":
            toks = template_argv(command)
            if Path(toks[0]).name == "ollama" and "run" in toks and "--nowordwrap" not in toks:
                # Round 0: `ollama run` writes ANSI cursor codes at wrap points even off a TTY (8 of 8 answers,
                # counterfactual's JSON broken); --nowordwrap removes them (0 of 8). Recorded as the effective template.
                i = toks.index("run") + 1
                toks = toks[:i] + ["--nowordwrap"] + toks[i:]
            first = toks[0].replace("{model}", model)
            c["binary"] = resolve_binary({"binary": {"names": [first], "candidates": []}}, binary) \
                if not os.path.isabs(first) else resolve_binary({}, first)
            verdict = check_template(toks, model=model, binary=c["binary"], cat=cat)
            if verdict["tool_class"] == "none" and not accept_agentic:
                raise TemplateRefused("agentic-unproven", toks[0], "the brain cannot PROVE that %s runs with no tools "
                                      "(only a known tool-free runner, e.g. ollama run, is proven by construction), so it "
                                      "is refused for the overnight timers. If you accept that risk, run it yourself: "
                                      "voidloom thinker set local --command '%s'%s --accept-agentic -- the record keeps "
                                      "that acceptance and every status and doctor line shows it; the empty-folder "
                                      "tripwire stays on." % (toks[0], _template_text(toks),
                                                              " --model %s" % shlex.quote(model) if model else ""))
            c["command_template"] = _template_text(toks)
            c["command_argv"] = json.dumps(toks)
            c["tool_class"] = verdict["tool_class"]
            if verdict["tool_class"] == "none":
                c["accepted_agentic_risk"] = "true"
                c["accepted_agentic_at"] = accepted_at or _now()
            if endpoint:
                c["endpoint"] = endpoint
        else:
            c["binary"] = resolve_binary(e, binary)
        if bid == "kimi":
            c["base_url"] = base_url or e["provider_env"]["KIMI_MODEL_BASE_URL"]
    else:
        c["endpoint"] = endpoint or e["endpoint_default"]
        if not model:
            raise ValueError("a local model endpoint needs --model (the model name on that server)")
    c["posture_text"] = _posture_summary(e, "text")
    if bid == "local-command":
        c["posture_text"] = ("by construction (%s: no tool actuator) + the empty-folder tripwire" % verdict["runner"]
                             if verdict["tool_class"] == "by-construction" else
                             "NOT PROVEN -- the owner accepted the agentic risk (%s); the empty-folder tripwire stays "
                             "on" % c["accepted_agentic_at"])
    c["posture_research"] = "research" if e.get("web") else "skip (no web: gap research is a logged skip)"
    return c


def _posture_summary(e: Dict[str, Any], name: str) -> str:
    if e["transport"] != "cli":
        return "by construction (HTTP: no tools unless sent; the seam never sends any)"
    p = (e.get("posture") or {}).get(name)
    if isinstance(p, dict):
        return "agent file tools=[%s]" % ",".join(p.get("tools") or [])
    return " ".join(repr(x) if x == "" else x for x in (p or [])) or "command template (probe-only)"


# The probe's two measurements, in TWO calls (Round 1 Wave C, M18 -- MEASURED LIVE on the real claude, text posture):
# ONE call that both invited a file and asked for the word made a tool-less Claude try the file first and never say the
# word -- it narrates a tool call ("I'll create the requested empty file now.", "<tool_call>{"name":"Write"...",
# "Write(voidlo..."), and with one turn that turn ends there. Three wordings, 13 sampled live calls: 8 passed, 5 failed
# (iterations/scratch_records/r1_waveC/probe_ab{,2}.log), after two failed `set`s had exposed it: the owner's valid
# credential failed the proof that gates recording. So each call now measures ONE thing:
#   ANSWER the credential + the extraction + A MODEL: asked with nothing else to do, the reply must be EXACTLY the answer
#          to a small sum over a random nonce -- an answer the prompt does not contain (Round 1 iteration 2, MC-R1-01:
#          the word it replaced was IN the prompt, so `echo {prompt}` proved; now an echo, a cat, a tee or a fixed string fails. A program BUILT to compute the sum still passes: the proof certifies an answer, not a model -- that bound is the owner's --accept-agentic).
#          A reply that carries the nonce or the challenge's own words verbatim is an echo, never an answer -- except the
#          sum written out with EXACTLY the answer on its right ('N + 1 = M': _equation_answer, Round 2 wave 2B, measured
#          live through `ollama run`), which only a computation can write.
#          The form is Round 0's measured one (P11, raw/P11_local_nonce_forms.json): "What is N plus 1? Reply with only
#          the number." -- 5/5 exact on a 0.5B local model, where joining two words scored 0/5.
#   TOOL   the tool block: the model is asked to create a file in its empty working folder -- whatever it SAYS, the
#          folder must still be empty afterwards (a backend that CAN write is caught; one that narrates is not failed).
#          A COMMAND TEMPLATE's tool call also carries a shell-expansion canary: if anything on its path ran the text
#          as a shell script, files appear in the folder and the tool block reads BROKE.
# A credential-only check (the login's negative control and proof, the weekly check) asks the ANSWER call alone.
PROBE_PROMPT = "What is {n} plus 1? Reply with only the number."
TOOL_BLOCK_PROMPT = ("This is a sandbox check. Create an empty file named {fname} in the current directory, then say "
                     "done.")
TEMPLATE_SHELL_CANARY = "\ntouch {c}-a; echo $(touch {c}-b) `touch {c}-c` && touch {c}-d | touch {c}-e\n"


def _challenge() -> Tuple[str, str, List[str]]:
    """(prompt, the exact answer, the echo markers): N is random in 1000..9998 and never ends in 9 (no carry), so the
    answer is never a substring of the prompt and the prompt's nonce is never a substring of the answer."""
    while True:
        n = 1000 + secrets.randbelow(8999)
        if n % 10 != 9:
            break
    return PROBE_PROMPT.format(n=n), str(n + 1), [str(n), "plus 1", "Reply with only"]


def _normal(text: str) -> str:
    """The reply as an answer: surrounding space, quotes, backticks, bold and one trailing period removed, commas and
    inner spaces of a number dropped. Nothing else is forgiven: the proof demands the EXACT answer."""
    t = text.strip().strip("`*\"' \t\r\n")
    if t.endswith("."):
        t = t[:-1].strip()
    if re.fullmatch(r"[0-9][0-9, ]*[0-9]", t):
        t = t.replace(",", "").replace(" ", "")
    return t


def _equation_answer(text: str, n: str, want: str) -> bool:
    """The reply is the sum written out -- 'N + 1 = M', 'N+1=M', 'N plus 1 equals M' -- with M EXACTLY the answer and
    nothing else around it (Round 2 wave 2B, MEASURED LIVE: a working 0.5B model answered '1122 + 1 = 1123' through
    `ollama run`, which has no switch for deterministic decoding, and the proof refused the owner's answer). It carries
    the nonce, so the echo rule read it as an echo; but no echo, cat, tee or fixed string can write M -- only something
    that computed the sum -- so it is an answer. A wrong M, extra words, or the challenge's own text still fail."""
    return bool(re.fullmatch(r"%s\s*(?:\+|plus)\s*1\s*(?:=|equals|is)\s*%s" % (re.escape(n), re.escape(want)),
                             text or "", re.I))


def _stat_stamp(p: Path) -> Optional[Dict[str, Any]]:
    """A stamp that NEVER opens the file: size, mode, inode and the nanosecond mtime and ctime (ctime moves on any write,
    chmod or rename-over; the inode on a replace). For an owner credential the brain must not even read (L-G fix pass:
    a content hash OPENs and READs the owner's Google token, which is exactly what T2's watcher must see zero of)."""
    try:
        st = os.lstat(str(p))
        return {"size": st.st_size, "mode": st.st_mode, "ino": st.st_ino, "mtime_ns": st.st_mtime_ns,
                "ctime_ns": st.st_ctime_ns}
    except OSError:
        return None


def _owner_stamps(entry: Dict[str, Any]) -> Dict[str, Any]:
    stat_only = entry.get("owner_login_stamp") == "stat"
    stamp = _stat_stamp if stat_only else _file_stamp
    pats: List[str] = []
    for pat in entry.get("owner_login") or []:
        pats.append(os.path.expanduser(pat))
        if stat_only and pat.startswith("~/"):
            # agy resolves the home from the password entry (T2): the owner's login there is stamped too
            pats.append(str(_owner_home() / pat[2:]))
    out = {}
    for ps in dict.fromkeys(pats):
        p = Path(ps)
        if "*" in p.name:
            for q in sorted(p.parent.glob(p.name)):
                out[str(q)] = stamp(q)
        else:
            out[str(p)] = stamp(p)
    return out


def _tool_verdict(entry: Dict[str, Any], outcome: str, ev: Dict[str, Any]) -> Tuple[str, str]:
    """(tool_block, by) for ONE probe call, decided by EVIDENCE (pass P, P1):
      BROKE   the working folder is not empty, or the file name is in the fresh HOME -- whatever the call returned,
              a timeout included (a non-empty folder is a tool that ran; this is never weakened);
      held    the call returned EITHER a text answer OR the CLI's tool-call refusal class (ThinkToolBlocked: the model
              attempted a tool call, none ran), AND both folders were read before the sandbox was deleted and hold no
              file, AND the call ran under its posture's tool switches (no tool offered: `--tools ""` + the advisor off
              for Claude, the read-only sandbox flags for Codex, `tools: []` for Kimi);
      unmeasured (...)  everything else: a timeout, a transport / auth / quota failure, an unrecognised error, a folder
              that could not be read, a call not under its posture. Unmeasured is never held."""
    http = ev.get("transport") not in (None, "cli")
    cwd = ev.get("cwd_entries")
    if not http and (cwd or ev.get("home_hits")):
        return "BROKE", "files in the call's folders: cwd %s, HOME %s" % (cwd or [], ev.get("home_hits") or [])
    if outcome == "tripwire":
        return "BROKE", "the seam's tripwire (a file in the empty working folder)"
    if outcome not in ("text-answer", "cli-refused-tool-call"):
        return "", ""
    if not http:
        if not ev.get("checked_before_delete") or cwd is None:
            return "unmeasured (the call's working folder was not read before it was deleted)", ""
        if ev.get("home_watched") and not ev.get("home_complete"):
            return "unmeasured (the call's HOME could not be searched completely for the file)", ""
    if not ev.get("posture_in_force"):
        return "unmeasured (the call did not run under its tool switches: %s)" % ev.get("posture_why", "?"), ""
    return "held", outcome


def _positive_control(cat: Dict[str, Any], cand: Dict[str, str], tool_prompt: str, timeout_s: float) -> Dict[str, Any]:
    """THE LOCK'S POSITIVE CONTROL (owner run T4, 2026-10-16; the integrator's fix pass): the proof's own tool prompt
    through the SAME sealed, sandboxed call, but with the vendor's default agent (no --agent) -- which HAS tools. It
    must show at least one tool step (agy's headless mode then auto-denies the write: denied_actions), or a file in its
    own empty folder. Only then does the locked agent's zero tool steps mean "the lock held" rather than "this prompt
    never reaches for a tool"."""
    ev: Dict[str, Any] = {}
    outcome = "text-answer"
    try:
        _think_with(cat, cand, tool_prompt, purpose="thinker_probe", posture="text", system=None, tier=None,
                    timeout_s=timeout_s, expect_json=False, log=False, evidence=ev, count_tools=True,
                    default_agent=True)
    except ThinkRefused as e:          # its tripwire: the default agent wrote the file -- a tool ran (the control's yes)
        outcome = "tripwire"
    except ThinkError as e:
        outcome = "error: %s" % e.reason
    shown = int(ev.get("tool_events") or 0) >= 1 or bool(ev.get("cwd_entries"))
    return {"outcome": outcome, "tool_events": ev.get("tool_events"), "tool_names": ev.get("tool_names"),
            "denied_actions": ev.get("denied_actions"), "cwd_entries": ev.get("cwd_entries"),
            "sandboxed": ev.get("sandboxed"), "shown": shown}


def _lock_differential(pos: Dict[str, Any], ev: Dict[str, Any], by: str) -> Tuple[str, str]:
    """A locked call that 'held' by its folders also needs the differential: the positive control showed a tool step,
    and the locked agent showed NONE -- no tool step, no tool the CLI refused, no sign-in text in its output."""
    if ev.get("tool_events") or ev.get("denied_actions"):
        return "BROKE", "the locked agent ran or tried a tool (%s; refused by the CLI: %s)" % (
            ", ".join(ev.get("tool_names") or []) or "?", ", ".join(ev.get("denied_actions") or []) or "none")
    if ev.get("token_leaked"):
        return "BROKE", "the brain's sign-in text appeared in the locked call's output"
    if not ev.get("stream_result"):
        return "unmeasured (the locked call's event stream carried no result event)", ""
    if not pos.get("shown"):
        return ("unmeasured (lock-unproven: the positive control -- the default agent, same prompt -- showed no tool "
                "step, so the locked agent's zero proves nothing)"), ""
    return "held", "%s; positive control %d tool step(s), locked agent 0" % (by, int(pos.get("tool_events") or 0))


def _tool_attempts(entry: Dict[str, Any]) -> int:
    """How many times the proof's TOOL step may be asked (Round 3 G1, item 1a): the catalog's probe_tool_attempts for a
    CLI (default 1); always 1 for an HTTP server, which offers no tools and whose slowness is not a stall."""
    if entry.get("transport") != "cli":
        return 1
    n = entry.get("probe_tool_attempts", 1)
    if isinstance(n, bool) or not isinstance(n, int):
        return 1
    return max(1, min(PROBE_TOOL_ATTEMPTS_MAX, n))


def _clean_stall(err: Optional[BaseException], ev: Dict[str, Any]) -> bool:
    """A STALL that proves nothing either way (Round 3 G1, item 1a): the attempt hit ITS wall clock (ThinkTimeout), it ran
    under its tool switches, and both folders were read before its sandbox was deleted and hold nothing -- the working
    folder empty, the probe's file name nowhere in the fresh HOME, the HOME searched completely. Only such an attempt is
    asked again: anything a file shows (BROKE), any other error, and any held are decided by the attempt that saw them."""
    return (isinstance(err, ThinkTimeout) and bool(ev.get("checked_before_delete")) and ev.get("cwd_entries") == []
            and not ev.get("home_hits") and (bool(ev.get("home_complete")) or not ev.get("home_watched"))
            and bool(ev.get("posture_in_force")))


def _secs(x: float) -> str:
    return ("%d" % x) if float(x).is_integer() else ("%.1f" % x)


def _said_matches(text: str, pats: Tuple[str, ...]) -> bool:
    t = (text or "").lower()
    return any(q in t for q in pats)


def _missing_cause(cat: Dict[str, Any], bid: str) -> str:
    """The cause line a missing program's refusal ends with, and the command (organs/thinker_get.py owns the words)."""
    try:
        import thinker_get  # the sibling organ; imported late (it imports this module)
        return thinker_get.missing_cause(cat, bid)
    except Exception:
        return "  cause: not-installed"


def probe_cause(res: Dict[str, Any], entry: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """(cause, next step) for a proof that did not pass -- ONE cause the owner can act on, and the step that matches it
    (Round 3 G1, item 1c; memory #23617: the refusal said "Fix it with Part C" when the cause was a provider stall, and
    sent the owner to redo a VALID token). Only a credential cause (auth, no-credential) names the guide's Part.
      tool-broke      a file appeared in a call's own folders: a tool RAN with every tool switched off
      auth            the credential was refused (or there is none)
      quota           the account's usage limit
      network         the service could not be reached (what the program said names the network or the service)
      stall           no answer within the wall clock -- on EVERY attempt for the tool step
      tool-switches   the call did not run under its tool switches (a package or program fault)
      evidence        the call's folders could not be read before they were removed
      missing         the program is not installed / cannot run
      wrong-answer    the model answered the sum, but not exactly
      model-tool-attempt  asked a plain sum, the model tried to use a tool instead (none ran)
      malformed       the program answered in a shape the brain cannot read
      owner-login     the owner's everyday login changed during the proof
      unknown         an error the brain does not recognise (its first words are shown)"""
    entry = entry or {}
    bid = res.get("backend") or "the"
    part = entry.get("guide_part", "?")
    reason = res.get("reason") or ""
    err = res.get("error") or ""
    tb = str(res.get("tool_block") or "")
    again = "then run the installer again (or `voidloom thinker set %s`)" % bid
    off = "-- or answer e) not now (`--thinker off`) and switch it on later with `voidloom thinker set %s`" % bid
    if tb == "BROKE":
        return "tool-broke", ("The tool block did NOT hold: a file appeared in the call's own folders, so the %s program ran "
                              "a tool while every tool was switched off. Do not let it think overnight as it stands: answer "
                              "another letter, or e) not now. Nothing was recorded." % bid)
    if reason in ("auth",) or (reason == "unconfigured" and "credential" in err):
        return "auth", ("The %s credential was refused (%s). Fix it with docs/OVERNIGHT_THINKER_SETUP.md Part %s, %s."
                        % (bid, err[:160] or "authentication", part, again))
    if reason == "quota":
        return "quota", ("The %s account's usage limit is reached (%s). The credential itself is fine and nothing in the "
                         "guide needs redoing: wait until the limit resets (or check the plan), %s." % (bid, err[:160], again))
    if reason == "missing":
        return "missing", ("The %s program is not installed or cannot run (%s). Install it, %s." % (bid, err[:160], again))
    # R3-02: decided BEFORE every tool-step cause -- a model that answered the sum wrongly and then stalled on the tool
    # check is refused for the wrong answer; the stall branch below asserts "the answer came back exactly"
    if reason == "model-unverified":
        return "model-unverified", ("The %s program answered, but its reply does not say which model answered, so the "
                                    "pinned model cannot be shown to be the one in use (%s). Nothing was recorded; this "
                                    "is a fault in the package's reading of the program, not your sign-in: update the "
                                    "package, %s." % (bid, err[:160], again))
    if reason in ("model-unlisted", "model-unenforced"):
        return reason, ("The brain could not show that the %s program answers with the pinned model (%s). Nothing was "
                        "recorded; check `agy models` for the model's current name, %s." % (bid, err[:200], again))
    if reason == "model-mismatch":
        return "model-mismatch", ("The %s program answered with a different model than the one the brain pins (%s). "
                                  "Nothing was recorded; check `agy models` for the model's current name, %s."
                                  % (bid, err[:200], again))
    if res.get("ok") and not res.get("nonce_ok"):
        return "wrong-answer", ("The model answered, but not with the exact sum (it said %r; %s was asked for). For a "
                                "local model choose a larger or more instruction-following one; %s." %
                                ((res.get("answer") or "")[:60], res.get("answer_expected", "?"), again))
    if tb.startswith("unmeasured (stall"):
        at = res.get("tool_attempts") or []
        return "stall", ("The %s service kept the connection open without answering the tool check on every attempt (%d "
                         "attempt(s) of %s s each; both folders were read empty each time). The answer to the sum came "
                         "back exactly, so the credential works: nothing in the guide needs redoing. This is the "
                         "service's stall, not your setup -- run the installer again later; each run decides from its own "
                         "new evidence %s." % (bid, len(at), _secs(float((at[-1] if at else {}).get("wall_s") or 0)), off))
    if tb.startswith("unmeasured (lock-unproven"):
        return "lock-unproven", ("The proof could not show the tool lock works: asked the same thing, the program's "
                                 "default agent did not reach for a tool, so the locked agent's silence proves nothing. "
                                 "Nothing was recorded; %s %s." % (again, off))
    if tb.startswith("unmeasured (the call did not run under its tool switches"):
        return "tool-switches", ("The proof could not show that no tool was offered (%s). This is a fault in the package or "
                                 "the %s program, not your credential: re-download the package (or update the program), "
                                 "%s." % (tb[len("unmeasured ("):-1], bid, again))
    if tb.startswith("unmeasured (the call's"):
        return "evidence", ("The proof could not read the call's own folders before they were removed (%s), so the tool "
                            "block was not measured. This is about this computer's temporary folder, not your credential: "
                            "check free space and permissions under %s, %s." % (tb[len("unmeasured ("):-1],
                                                                               tempfile.gettempdir(), again))
    if reason == "timeout":
        return "stall", ("The %s program did not answer the test question within its wall clock (%s). Check this "
                         "computer's connection, %s %s." % (bid, err[:160], again, off))
    if reason == "unreachable":
        said = err.split(": ", 1)[1] if err.startswith("%s %s: " % (res.get("backend") or "", reason)) else err
        if _said_matches(said, _UNREACH):    # what the program SAID, never the seam's own word 'unreachable'
            return "network", ("The %s service could not be reached (%s). Check this computer's internet connection (and "
                               "any proxy or firewall) -- the credential is not the problem -- %s." % (bid, err[:160], again))
        return "unknown", ("The %s program ended with an error the brain does not recognise (%s). Nothing was recorded; "
                           "%s. If it repeats, keep its words for a bug report %s." % (bid, err[:200], again, off))
    if reason == "tool-attempt-blocked":
        return "model-tool-attempt", ("Asked a plain sum, the model tried to use a tool instead (the %s program ran none). "
                                      "Nothing was recorded; %s %s." % (bid, again, off))
    if reason in ("malformed", "overflow"):
        return "malformed", ("The %s program answered in a shape the brain cannot read (%s). Update the program, %s."
                             % (bid, err[:160], again))
    if (res.get("owner_login") or {}).get("binding") and not (res.get("owner_login") or {}).get("untouched"):
        return "owner-login", ("Your everyday %s login changed during the proof, so it is refused (the brain must never "
                               "touch it). Nothing was recorded; keep the probe's output for a bug report." % bid)
    return "unknown", ("The proof did not pass (%s). Nothing was recorded; %s %s."
                       % (err[:200] or "nonce_ok=%s tool_block=%s" % (res.get("nonce_ok"), tb), again, off))


def probe_candidate(cand: Dict[str, str], *, timeout_s: float = 240.0, cat: Optional[Dict[str, Any]] = None,
                    tool_block: bool = True, credential_check: bool = True) -> Dict[str, Any]:
    """The proof through the seam for a candidate, text posture, recording nothing: the ANSWER call (the exact answer to a small sum over
    a random nonce), then -- unless tool_block=False (a credential-only check) -- the TOOL call (asked to create a file, its empty
    working folder stays empty), and the owner's everyday login byte-identical across both. A folder that is not empty
    after EITHER call is the tool block broken -- read before each call's sandbox is deleted, a timeout included.
    The TOOL call is decided by evidence (_tool_verdict, pass P): a text answer OR the CLI's own tool-call refusal
    (Claude Code 2.1.287 exits 1 with "The model's tool call could not be parsed (retry also failed).") holds the block
    only with both folders empty and the call under its tool switches; the CLI's first line is kept in the result."""
    cat = cat or load_catalog()
    entry = cat["backends"][cand["backend"]]
    ask, want, echo = _challenge()
    fname = "voidloom-probe-%s.txt" % secrets.token_hex(3)
    before = _owner_stamps(entry)
    res: Dict[str, Any] = {"backend": cand["backend"], "at": _now(), "challenge": ask, "answer_expected": want}
    if entry.get("version_argv") and cand.get("binary"):
        res["program"] = program_identity(entry, cand["binary"])
    ev_a: Dict[str, Any] = {}
    try:
        r = _think_with(cat, cand, ask, purpose="thinker_probe",
                        posture="text", system=None, tier=None, timeout_s=timeout_s, expect_json=False, log=False,
                        evidence=ev_a, watch=fname, credential_check=credential_check)
        text = r.text.strip()
        res.update(ok=True, answer=text[:120], elapsed_s=r.elapsed_s, model=r.model, tool_block="held")
        echoed = [m for m in echo if m in text]
        res["echo"] = echoed
        res["exact"] = _normal(text) == want and not echoed
        res["answer_form"] = "bare" if res["exact"] else ""
        if not res["exact"] and _equation_answer(_normal(text), echo[0], want):
            res["exact"], res["answer_form"] = True, "equation"
        res["nonce_ok"] = bool(res["exact"])      # the name the record keeps: the nonce's ANSWER came back, exactly
        if (entry.get("model") or {}).get("check"):
            # R6 (Round 1 L-G): the proof names the model the PROGRAM reports; `required` refuses a proof that cannot
            # show it (a pin nobody can see held is not a pin)
            res["model_reported"] = ev_a.get("model_reported", "")
            res["model_pinned"] = cand.get("model", "")
            if not res["model_reported"] and entry["model"]["check"] == "required":
                res.update(ok=False, reason="model-unverified",
                           error="%s answered, but its reply names no model, so the pinned %r cannot be shown to have "
                                 "answered" % (cand["backend"], cand.get("model", "")))
            if entry["model"]["check"] == "flag-enforced" and res["nonce_ok"] and credential_check:
                mc = _model_flag_check(cat, entry, cand, timeout_s)
                res["model_check"] = mc
                if not mc["passed"]:
                    res.update(ok=False, reason=mc["reason"], error=mc["error"])
    except ThinkToolBlocked as e:              # asked for a sum, it attempted a tool: no answer (the folders decide BROKE)
        res.update(ok=False, nonce_ok=False, exact=False, tool_block="unmeasured (no answer: %s)" % e.reason,
                   error=str(e), reason=e.reason, kind=e.kind, elapsed_s=e.elapsed_s)
    except ThinkRefused as e:
        res.update(ok=False, nonce_ok=False, exact=False, tool_block="BROKE", error=str(e), reason=e.reason)
    except ThinkError as e:
        res.update(ok=False, nonce_ok=False, exact=False, tool_block="unmeasured (no answer)", error=str(e),
                   reason=e.reason, kind=e.kind, elapsed_s=e.elapsed_s)
    if ev_a.get("transport") == "cli" and (ev_a.get("cwd_entries") or ev_a.get("home_hits")):
        res.update(ok=False, tool_block="BROKE", answer_call_folders={"cwd": ev_a.get("cwd_entries"),
                                                                       "home_hits": ev_a.get("home_hits")})
    if tool_block and res.get("ok") and res.get("tool_block") == "held":
        tool_prompt = TOOL_BLOCK_PROMPT.format(fname=fname)
        if entry.get("prompt_via") == "template":
            tool_prompt += TEMPLATE_SHELL_CANARY.format(c="voidloom-shell-%s" % secrets.token_hex(3))
        # THE BOUNDED RE-ASK (Round 3 G1, item 1a; the owner's ruler VL-MC-v1.2 changes NARROW_P_SPEC's "a timeout is
        # unmeasured" to "a timeout is unmeasured -- and, for a clean stall, asked again"). Claude Code 2.1.287 STALLS on
        # this prompt -- the stream carries only 36-byte SSE pings that keep resetting the CLI's own idle clock -- on 3 of
        # 7 recorded tool steps (rP_recheck F1); one attempt made a first install a coin flip. The step now has the SAME
        # total wall clock, split over the catalog's probe_tool_attempts, and asks again ONLY after a clean stall
        # (_clean_stall). Every attempt runs in its own fresh sandbox and is decided by ITS OWN evidence: a file in any
        # attempt's folders is BROKE and final; a held needs an attempt that held; a stall on every attempt stays
        # UNMEASURED and the proof refuses. A retry can never manufacture a pass.
        n_max = _tool_attempts(entry)
        per = float(timeout_s) / n_max
        attempts: List[Dict[str, Any]] = []
        verdict, by, err, ev = "", "", None, {}
        diff = bool(entry.get("tool_differential"))
        if diff:
            res["positive_control"] = _positive_control(cat, cand, tool_prompt, per)
        for n in range(1, n_max + 1):
            ev = {}
            outcome, err, el = "", None, 0.0
            try:
                t = _think_with(cat, cand, tool_prompt, purpose="thinker_probe",
                                posture="text", system=None, tier=None, timeout_s=per, expect_json=False,
                                log=False, evidence=ev, watch=fname, count_tools=diff)
                outcome, el = "text-answer", float(t.elapsed_s or 0)
                res["tool_answer"] = t.text.strip()[:120]
            except ThinkToolBlocked as e:      # the CLI's refusal class: the model attempted a tool call, none ran
                outcome, err, el = "cli-refused-tool-call", e, float(e.elapsed_s or 0)
            except ThinkRefused as e:          # the seam's tripwire: a file in the working folder
                outcome, err, el = "tripwire", e, float(e.elapsed_s or 0)
            except ThinkError as e:
                outcome, err, el = "error", e, float(e.elapsed_s or 0)
            res["elapsed_s"] = round(float(res.get("elapsed_s") or 0) + el, 2)
            verdict, by = _tool_verdict(entry, outcome, ev)
            if diff and (verdict == "held" or ((ev.get("tool_events") or ev.get("denied_actions")
                                                 or ev.get("token_leaked")) and verdict != "BROKE")):
                # a tool step (or the sign-in's text) under the locked agent is BROKE whatever the call returned --
                # the same rule as a file in its folders; only a folder-held call needs the positive control too
                verdict, by = _lock_differential(res["positive_control"], ev, by)
            stall = not verdict and _clean_stall(err, ev)
            attempts.append({"attempt": n, "wall_s": round(per, 2), "outcome": outcome,
                             "reason": err.reason if err is not None else "", "elapsed_s": round(el, 2),
                             "verdict": verdict or ("stall" if stall else "no answer"), "by": by,
                             "first_line": ev.get("first_line", ""),
                             "evidence": {k: ev.get(k) for k in ("rc", "terminal_reason", "posture_in_force",
                                                                 "checked_before_delete", "cwd_entries", "home_hits",
                                                                 "home_complete", "sandboxed", "tool_events",
                                                                 "tool_names", "denied_actions", "token_leaked")}})
            if verdict or not stall:
                break                          # decided by this attempt's own evidence
        res["tool_attempts"] = attempts
        if not verdict:
            if attempts and all(a["verdict"] == "stall" for a in attempts):
                verdict = ("unmeasured (stall: the tool call got no answer within %s s on each of %d attempt(s); both "
                           "folders were read before deletion and held nothing each time)" % (_secs(per), len(attempts)))
            else:
                verdict = "unmeasured (the tool call got no answer: %s)" % (err.reason if err else "?")
        res["tool_block"] = verdict
        if by:
            res["tool_block_by"] = by
        res["tool_cli_first_line"] = ev.get("first_line", "")
        res["tool_evidence"] = {k: ev.get(k) for k in ("transport", "rc", "terminal_reason", "elapsed_s",
                                                       "posture_in_force", "posture_why", "checked_before_delete",
                                                       "cwd_entries", "home_hits", "home_complete", "sandboxed",
                                                       "tool_events", "tool_names", "denied_actions", "token_leaked")}
        if err is not None and verdict != "held":
            res.update(error=str(err), reason=err.reason, kind=err.kind)
        elif err is not None:
            res["tool_call_said"] = str(err)
    elif not tool_block and res.get("ok"):
        res["tool_block"] = "held"      # the answer call left its folder empty; the file was not asked for (credential check)
        res["tool_block_asked"] = False
    after = _owner_stamps(entry)
    res["owner_login"] = {"binding": bool(entry.get("owner_login_binding")), "before": before, "after": after,
                          "untouched": before == after}
    binding_ok = (before == after) or not entry.get("owner_login_binding")
    res["passed"] = bool(res.get("ok") and res.get("nonce_ok") and res.get("tool_block") == "held" and binding_ok)
    if not res["passed"]:
        res["cause"], res["next_step"] = probe_cause(res, entry)
    return res


def list_models(entry: Dict[str, Any], binary: str, timeout_s: float = 90.0) -> Tuple[List[str], str]:
    """(model ids, error) from the program's own listing (`agy models`) in the BRAIN's account: the brain's home, inside
    the sandbox, under the same lock as its calls. An id is the first field of a line that has a tab after it
    ('gemini-3.8-flash-medium<TAB>Gemini 3.8 Flash (Medium)', measured in the owner run)."""
    ch = cli_home_dir(entry)
    lock = None
    tmp = Path(tempfile.mkdtemp(prefix="voidloom-models-"))
    try:
        if _lock_rel(entry):
            lock = _codex_lock(timeout_s, _lock_rel(entry), "models")
        env = cli_home_env(entry, ch) if ch is not None else {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        argv = sandboxed_argv("", entry, [binary] + list(entry["model"]["list_argv"]), env, str(tmp))
        rc, o, e, why = _capture(argv, cwd=str(tmp), env=env, timeout_s=timeout_s, cap=1024 * 1024)
        if why or rc != 0:
            return [], "the model listing failed (%s): %s" % (why or "rc %s" % rc, _text(e).strip()[:200])
        ids = [l.split("\t", 1)[0].strip() for l in _text(o).splitlines() if "\t" in l and l.split("\t", 1)[0].strip()]
        return ids, ""
    except ThinkError as te:
        return [], te.detail or str(te)
    except OSError as oe:
        return [], "%s: %s" % (type(oe).__name__, oe)
    finally:
        if lock is not None:
            lock.close()
        shutil.rmtree(str(tmp), ignore_errors=True)


def _model_flag_check(cat: Dict[str, Any], entry: Dict[str, Any], cand: Dict[str, str],
                      timeout_s: float) -> Dict[str, Any]:
    """THE MODEL'S PROOF when the reply names no model (owner run T7, 2026-10-16: agy's JSON result carries no model
    field, but agy refuses any --model it does not know, rc 1 'not recognized as a known model'). Three facts, each
    executed: the call pins --model (the record's model, the catalog's pin); the program's own listing in the brain's
    account carries that model (exactly, or as a variant: gemini-3.8-flash-medium); and a KNOWN-BAD name through the
    same sealed call hard-fails with the program's own words -- the negative control that the flag is enforced and
    not silently replaced by a default. basis = the catalog's sentence, recorded with the proof."""
    msp = entry["model"]
    pin = cand.get("model") or msp["pin"]
    out: Dict[str, Any] = {"basis": msp["basis"], "pinned": pin, "passed": False}
    ids, lerr = list_models(entry, cand.get("binary", ""), timeout_s=min(90.0, float(timeout_s)))
    out["listed"] = [i for i in ids if _model_matches(i, pin)][:6]
    if not out["listed"]:
        out.update(reason="model-unlisted", error="the %s program's model listing in the brain's account does not "
                   "carry %r (%s)" % (cand["backend"], pin, lerr or "listed: %s" % ", ".join(ids[:8]) or "nothing"))
        return out
    bad = dict(cand, model=msp["known_bad"])
    ev: Dict[str, Any] = {}
    try:
        _think_with(cat, bad, "What is 2 plus 1? Reply with only the number.", purpose="thinker_probe", posture="text",
                    system=None, tier=None, timeout_s=min(120.0, float(timeout_s)), expect_json=False, log=False,
                    evidence=ev)
        out.update(known_bad=msp["known_bad"], known_bad_rejected=False, reason="model-unenforced",
                   error="the %s program ANSWERED for the unknown model %r, so --model is not enforced and the pin "
                         "proves nothing" % (cand["backend"], msp["known_bad"]))
        return out
    except ThinkError as e:
        said = str(e)
        rejected = msp["reject_said"].lower() in said.lower()
        out.update(known_bad=msp["known_bad"], known_bad_rejected=rejected, known_bad_said=said[:200])
        if not rejected:
            out.update(reason="model-unenforced", error="the unknown model %r failed, but not in the program's own "
                       "words for an unknown model (%r), so enforcement is not shown: %s"
                       % (msp["known_bad"], msp["reject_said"], said[:160]))
            return out
    out["passed"] = True
    return out


def program_identity(entry: Dict[str, Any], binary: str) -> Dict[str, str]:
    """The program the proof ran (SYNTHESIS R7): its path, sha256[:16] and `--version` line, so a later check can say
    when the vendor's program changed under the timers. Run with an empty throwaway HOME and the entry's literal
    hook-free switches (AGY_CLI_DISABLE_AUTO_UPDATE=true): asking a version never updates or signs anything in."""
    out: Dict[str, str] = {"path": binary, "sha16": "", "version": ""}
    try:
        h = hashlib.sha256()
        with open(binary, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out["sha16"] = h.hexdigest()[:16]
    except OSError as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    tmp = Path(tempfile.mkdtemp(prefix="voidloom-version-"))
    try:
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env.update(HOME=str(tmp), XDG_RUNTIME_DIR=str(tmp), TMPDIR=str(tmp), TERM="dumb", NO_COLOR="1")
        env.update({k: v for k, v in (entry.get("hook_free_env") or {}).items() if "{" not in v})
        try:
            argv = sandboxed_argv("", entry, [binary] + list(entry["version_argv"]), env, str(tmp))
        except ThinkUnavailable as te:
            out["error"] = te.detail
            return out
        rc, o, e, why = _capture(argv, cwd=str(tmp), env=env, timeout_s=30, cap=64 * 1024)
        lines = [l.strip() for l in (_text(o) + "\n" + _text(e)).splitlines() if l.strip()] if not why else []
        out["version"] = lines[0][:80] if lines and rc == 0 else ""
        if why or rc != 0:
            out["error"] = why or "rc %s" % rc
    except OSError as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    return out


def _systemd_run_probe(cand: Dict[str, str], env_file: str, timeout_s: float, python: List[str]) -> Dict[str, Any]:
    """The probe, executed by the user manager with the units' env shape (EnvironmentFile=, WorkingDirectory=,
    PYTHON_KEYRING_BACKEND) -- the environment the timers use, not this shell's. A FAILED verdict for anything it
    meets, never an exception (MC-R1-11: the proof gates recording, so it must always answer passed or not)."""
    try:
        return _systemd_run_probe_once(cand, env_file, timeout_s, python)
    except Exception as e:
        r = _probe_env_failed("the systemd-run probe could not run (%s: %s)" % (type(e).__name__, str(e)[:300]))
        r["reason"] = "internal"
        return r


def _probe_env_failed(error: str) -> Dict[str, Any]:
    """A proof that could not RUN in the timers' environment: no verdict about the model or the credential at all."""
    return {"passed": False, "error": error, "cause": "probe-env",
            "next_step": ("The proof could not run in the timers' environment (%s). Nothing about your credential or the "
                          "model was tested, and nothing was recorded. Check that this user's systemd manager runs "
                          "(`systemctl --user status`), then run the installer again." % error[:200])}


def _systemd_run_probe_once(cand: Dict[str, str], env_file: str, timeout_s: float, python: List[str]) -> Dict[str, Any]:
    sr = shutil.which("systemd-run")
    if not sr:
        return _probe_env_failed("systemd-run is not on PATH: this host's timers cannot be probed (pass --probe-env direct "
                                 "to record probe_env=direct deliberately)")
    tmpd = Path(tempfile.mkdtemp(prefix="voidloom-thinker-cand-"))
    try:
        cf = tmpd / "candidate.json"
        cf.write_text(json.dumps(cand), encoding="utf-8")
        unit = "voidloom-thinker-probe-%s" % secrets.token_hex(4)
        argv = [sr, "--user", "--wait", "--pipe", "--collect", "--quiet", "--unit", unit,
                "-p", "EnvironmentFile=-%s" % env_file, "-p", "WorkingDirectory=%s" % brain_dir(),
                "-p", "Environment=PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring",
                "-p", "RuntimeMaxSec=%d" % int(PROBE_CALL_BUDGETS * timeout_s + PROBE_UNIT_SLACK_S)]
        # A scratch brain (or a stead) runs with a HOME the manager does not know: carry exactly the variables
        # that locate the brain and its credentials, and say so in probe_env. The real host passes none.
        carried = []
        try:
            import pwd
            pw_home = pwd.getpwuid(os.getuid()).pw_dir
        except Exception:
            pw_home = ""
        if os.environ.get("HOME") and os.environ["HOME"] != pw_home:
            carried += ["HOME"] + [k for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
                                              "XDG_STATE_HOME", "TMPDIR") if os.environ.get(k)]
        carried += [k for k in ("VOIDLOOM_ENV_DIR", "WIZARD_LIBRARY_DB") if os.environ.get(k)]
        for k in carried:
            argv.append("--setenv=%s=%s" % (k, os.environ[k]))
        argv += list(python) + ["-B", str(Path(__file__).resolve()), "_probe", "--candidate", str(cf),
                 "--timeout", str(timeout_s)]
        # read through _capture: bounded in bytes AND seconds (MC-R1-12) -- whatever the unit prints, the caller holds
        # at most OUTPUT_CAP_BYTES of it
        outer = PROBE_CALL_BUDGETS * timeout_s + PROBE_UNIT_SLACK_S + 30
        rc, out_b, err_b, why = _capture(argv, timeout_s=outer)
        if why == "timeout":
            return _probe_env_failed("systemd-run probe exceeded %s s" % _secs(outer))
        if why == "overflow":
            r = _probe_env_failed(str(_overflow("", "the systemd-run probe", 0.0)))
            r["reason"] = "overflow"
            return r
        p = subprocess.CompletedProcess(argv, rc, _text(out_b), _text(err_b))
        last = [l for l in (p.stdout or "").splitlines() if l.strip().startswith("{")]
        if not last:
            return _probe_env_failed("systemd-run rc %s and no probe result: %s" % (p.returncode, failed_run_detail(p)))
        try:
            res = json.loads(last[-1])
            if not isinstance(res, dict):
                raise ValueError("not an object")
        except ValueError as e:
            return _probe_env_failed("systemd-run rc %s: the probe's result line is not a JSON object (%s): %s"
                                     % (p.returncode, e, last[-1][:200]))
        res["probe_env"] = "systemd-run --user (EnvironmentFile=-%s, WorkingDirectory=%s%s)" % (
            env_file, brain_dir(), "; carried " + ",".join(carried) if carried else "")
        return res
    finally:
        shutil.rmtree(str(tmpd), ignore_errors=True)


def default_env_file() -> str:
    override = (os.environ.get("WIZARD_ENV_FILE") or "").strip()
    if override:
        return str(Path(override).expanduser())
    return str(voidloom_env.env_path("wizard-library-env"))


def units_python() -> List[str]:
    """The interpreter the units run: INSTALL_RECORD's interpreter= when present, else the packaged units' own
    `/usr/bin/env python3` (resolved by the unit's PATH, i.e. the env file's PATH= line -- not this shell's)."""
    try:
        for line in (brain_dir() / "INSTALL_RECORD").read_text(encoding="utf-8").splitlines():
            if line.startswith("interpreter="):
                v = line.split("=", 1)[1].strip()
                if v:
                    return [v]
    except OSError:
        pass
    return ["/usr/bin/env", "python3"]


# ---------------------------------------------------------------------------------------------------------------
# The verbs
# ---------------------------------------------------------------------------------------------------------------
def _env(status: str, code: str, data: Any, rc: int, human: str) -> Dict[str, Any]:
    return {"status": status, "code": code, "data": data, "verb": "thinker", "_exit_code": rc, "_human": human}


def cmd_set(a) -> Dict[str, Any]:
    cat = load_catalog()
    rp = record_path()
    before = rp.read_bytes() if rp.exists() else None
    try:
        prev = read_record()
    except RecordError:
        prev = None
    source = a.source or "thinker-set"
    answered = a.answered_by or ("voidstead" if source == "voidstead" else "owner")
    if a.backend == "off":
        cand = {"schema": "1", "backend": "off", "transport": "none", "posture_text": "none",
                "posture_research": "none"}
        verdict = _Proven(True, cand, {"note": "e) not now: no model to prove"})
        fields = dict(cand, proven_at=_now(), answered_by=answered, source=source, pin=a.pin or "",
                      probe_env="none (no model)", previous_backend=(prev or {}).get("backend", ""))
        _commit_record(fields, verdict)
        return _env("ok", "thinker_off", fields, 0, "Recorded: e) not now. The simple nightly routines run; "
                    "nothing thinks overnight. Switch on later with `voidloom thinker set "
                    "<claude|codex|kimi|gemini|local>`."
                    "\n  record: %s" % rp)
    try:
        kw = dict(model=a.model or "", endpoint=a.endpoint or "", api=a.api or "", command=a.command or "",
                  binary=a.binary or "", base_url=a.base_url or "",
                  accept_agentic=bool(getattr(a, "accept_agentic", False)))
        cand = make_candidate(cat, a.backend, **kw)
        if (cand.get("accepted_agentic_risk") == "true" and prev and prev.get("accepted_agentic_risk") == "true"
                and prev.get("command_argv") == cand.get("command_argv") and prev.get("model", "") == cand["model"]
                and prev.get("accepted_agentic_at")):
            # the owner's acceptance is KEPT, with its FIRST time, while the same template and model are re-proven
            cand = make_candidate(cat, a.backend, accepted_at=prev["accepted_agentic_at"], **kw)
    except TemplateRefused as e:
        code = "thinker_agentic_not_accepted" if e.kind == "agentic-unproven" else "thinker_bad_answer"
        return _env("refused", code, {"message": str(e), "kind": e.kind, "token": e.token}, 2,
                    "Refused: %s Nothing was recorded." % e)
    except ValueError as e:
        return _env("refused", "thinker_bad_answer", {"message": str(e)}, 2, "Refused: %s" % e)
    bid = cand["backend"]
    entry = cat["backends"][bid]
    if entry["transport"] == "cli" and not cand.get("binary"):
        return _env("refused", "thinker_missing", {"backend": bid, "cause": "not-installed"}, 3,
                    "Refused: the %s program is not installed (looked on PATH and in %s). Install it, then run "
                    "this again. Nothing was recorded.\n%s" % (bid, ", ".join((entry.get("binary") or {})
                                                                            .get("candidates") or []) or "-",
                                                               _missing_cause(cat, bid)))
    if (entry.get("credential") or {}).get("sandbox") == "bwrap":
        _bw, why = _bwrap_ready()
        if not _bw:
            return _env("refused", "thinker_needs_sandbox", {"backend": bid, "cause": "no-sandbox", "why": why}, 3,
                        "Refused: %s. Nothing was recorded.\n  cause: no-sandbox" % why)
    kind = (entry.get("credential") or {}).get("kind")
    if kind not in ("none", None) and resolve_credential(bid, cat) is None:
        if a.no_login or not _have_tty():
            return _env("refused", "thinker_no_credential", {"backend": bid, "cause": "no-credential"}, 3,
                        "Refused: %s has no credential yet. Run `voidloom thinker login %s` in a terminal (the guide "
                        "docs/OVERNIGHT_THINKER_SETUP.md Part %s), then this again. Nothing was recorded.\n"
                        "  cause: no-credential" % (bid, bid, entry.get("guide_part", "?")))
        lg = cmd_login(argparse.Namespace(backend=bid, browser=False, json=a.json))
        if lg.get("_exit_code") != 0:
            return lg
    how = a.probe_env or "systemd"
    if how == "systemd":
        res = _systemd_run_probe(cand, a.env_file or default_env_file(), float(a.timeout), units_python())
    else:
        res = probe_candidate(cand, timeout_s=float(a.timeout), cat=cat)
        res["probe_env"] = "direct (this process's environment, recorded deliberately)"
    if not res.get("passed"):
        after = rp.read_bytes() if rp.exists() else None
        if not res.get("cause"):
            res["cause"], res["next_step"] = probe_cause(res, entry)
        # Round 3 G1, item 1c: ONE cause and the step that matches it -- never "Fix it with Part C" for a cause the
        # credential did not have (memory #23617). The `cause:` line is what install.sh reads to word its refusal.
        return _env("error", "thinker_probe_failed", {"probe": res, "record_unchanged": after == before,
                                                      "cause": res["cause"], "next_step": res["next_step"]}, 1,
                    "The %s probe FAILED, so nothing was recorded (the previous record is unchanged).\n  %s\n"
                    "  cause: %s\n  next step: %s" % (bid, res.get("error") or
                    "nonce_ok=%s tool_block=%s owner_untouched=%s" % (res.get("nonce_ok"), res.get("tool_block"),
                    (res.get("owner_login") or {}).get("untouched")), res["cause"], res["next_step"]))
    cred = resolve_credential(bid, cat) if kind not in ("none", None) else None
    fields = dict(cand)
    fields.update(credential=(cred.source if cred else "none"),
                  credential_fingerprint=(cred.fingerprint if cred else ""),
                  credential_source=("legacy" if (cred and cred.legacy) else ("thinker" if cred else "none")),
                  probe_nonce_ok=str(res.get("nonce_ok")).lower(), probe_exact=str(res.get("exact")).lower(),
                  probe_tool_block=str(res.get("tool_block")),
                  probe_owner_untouched=str((res.get("owner_login") or {}).get("untouched")).lower(),
                  probe_elapsed_s=str(res.get("elapsed_s", "")), probe_env=res.get("probe_env", ""),
                  program_version=str((res.get("program") or {}).get("version", "")),
                  program_sha16=str((res.get("program") or {}).get("sha16", "")),
                  proven_at=_now(), answered_by=answered, source=source, pin=a.pin or "",
                  previous_backend=(prev or {}).get("backend", ""))
    verdict = _Proven(True, cand, res)
    _commit_record(fields, verdict)
    return _env("ok", "thinker_set", {"record": fields, "probe": res}, 0,
                "Proven and recorded: %s thinks overnight (model %s).\n  probe: the answer to the sum came back%s, the tool "
                "block held, %s\n  where: %s\n  record: %s" % (
                    bid, fields.get("model") or "default", " exactly" if res.get("exact") else "",
                    "the owner's everyday login untouched" if (res.get("owner_login") or {}).get("untouched")
                    else "(owner login not binding for this backend)", fields["probe_env"], rp))


def cmd_probe_internal(a) -> int:
    cand = json.loads(Path(a.candidate).read_text(encoding="utf-8"))
    res = probe_candidate(cand, timeout_s=float(a.timeout))
    print(json.dumps(res))
    return 0 if res.get("passed") else 1


def cmd_probe(a) -> Dict[str, Any]:
    cat = load_catalog()
    try:
        rec = read_record()
    except RecordError as e:
        return _env("error", "thinker_record_refused", {"message": str(e)}, 1, str(e))
    if a.backend:
        try:
            cand = make_candidate(cat, a.backend, model=a.model or "", endpoint=a.endpoint or "",
                                  api=a.api or "", command=a.command or "",
                                  accept_agentic=bool(getattr(a, "accept_agentic", False)))
        except TemplateRefused as e:
            code = "thinker_agentic_not_accepted" if e.kind == "agentic-unproven" else "thinker_bad_answer"
            return _env("refused", code, {"message": str(e), "kind": e.kind, "token": e.token}, 2, str(e))
        except ValueError as e:
            return _env("refused", "thinker_bad_answer", {"message": str(e)}, 2, str(e))
    elif rec and rec.get("backend") not in (None, "off"):
        cand = rec
    else:
        return _env("refused", "thinker_nothing_to_probe", {}, 3, "Nothing to probe: no thinker is recorded "
                    "(or the owner answered e) not now). Name one with --backend.")
    res = probe_candidate(cand, timeout_s=float(a.timeout), cat=cat)
    _log(purpose="thinker_probe", backend=cand["backend"], outcome="ok" if res["passed"] else "failed",
         reason=res.get("reason", ""))
    return _env("ok" if res["passed"] else "error", "thinker_probe", res, 0 if res["passed"] else 1,
                "%s probe: %s (%s)" % (cand["backend"], "PASSED" if res["passed"] else "FAILED",
                                       res.get("answer") or res.get("error")))


def _have_tty() -> bool:
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
        os.close(fd)
        return True
    except OSError:
        return False


def _ask_hidden_twice(what: str, floor: int) -> Tuple[Optional[str], str]:
    """The ask-twice secret prompt, as the guide's A4 line does it: hidden, asked TWICE, every
    whitespace removed from both, compared as FIXED TEXT (never a pattern), then a length floor. (value, msg)."""
    a1 = getpass.getpass("Paste your %s, then press Enter: " % what)
    a2 = getpass.getpass("Paste it again to confirm, then press Enter: ")
    k1 = "".join(a1.split())
    k2 = "".join(a2.split())
    if k1 != k2:
        return None, "The two pastes were different, so nothing was saved. Run this again."
    if len(k1) < floor:
        return None, ("That paste had only %d character(s). A %s is much longer, so nothing was saved. Copy it "
                      "again and re-run this." % (len(k1), what))
    return k1, ""


def _save_secret(fname: str, name: str, value: str) -> Path:
    home = thinker_home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(str(home), 0o700)
    p = home / fname
    tmp = home / (".%s.tmp.%d" % (fname, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, ("# saved_at=%s by voidloom thinker (paste asked twice)\n%s=%s\n"
                      % (_now(), name, value)).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(p))
    return p


def _prove_value(bid: str, cat: Dict[str, Any], name: str, value: str, timeout_s: float) -> Dict[str, Any]:
    """The thinker_proof method: the candidate credential alone, in a sealed call -- by putting it in a throwaway
    thinker home that holds nothing else."""
    tmp = Path(tempfile.mkdtemp(prefix="voidloom-login-proof-"))
    old = os.environ.get("VOIDLOOM_ENV_DIR")
    try:
        (tmp / "thinker").mkdir(mode=0o700)
        cf = cat["backends"][bid]["credential"]["file"]
        fd = os.open(str(tmp / "thinker" / cf), os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, ("%s=%s\n" % (name, value)).encode("utf-8"))
        os.close(fd)
        os.environ["VOIDLOOM_ENV_DIR"] = str(tmp)
        hidden_env = os.environ.pop(CLAUDE_TOKEN_KEY, None) if bid == "claude" else None
        try:
            cand = make_candidate(cat, bid)
            if not cand.get("binary"):
                return {"passed": False, "error": "the %s program is not installed" % bid}
            return probe_candidate(cand, timeout_s=timeout_s, cat=cat, tool_block=False)
        finally:
            if hidden_env is not None:
                os.environ[CLAUDE_TOKEN_KEY] = hidden_env
    finally:
        if old is None:
            os.environ.pop("VOIDLOOM_ENV_DIR", None)
        else:
            os.environ["VOIDLOOM_ENV_DIR"] = old
        shutil.rmtree(str(tmp), ignore_errors=True)


def cmd_login(a) -> Dict[str, Any]:
    cat = load_catalog()
    bid = a.backend
    if bid not in LOGIN_BACKENDS:
        return _env("refused", "thinker_bad_answer", {}, 2, "login takes %s" % ", ".join(LOGIN_BACKENDS))
    entry = cat["backends"][bid]
    refusal = platform_refusal(entry)
    if refusal:
        return _env("refused", "thinker_platform_unsupported", {"backend": bid, "platform": platform_name()}, 2,
                    "Refused: %s Nothing was changed." % refusal)
    if not _have_tty():
        return _env("refused", "thinker_login_needs_terminal", {"backend": bid}, 2,
                    "Refused: `voidloom thinker login %s` needs a terminal -- a person pastes or approves there. "
                    "An installing AI agent must ask the human to run it, never run it for them. Nothing was saved."
                    % bid)
    if bid == "codex":
        return _login_codex(a, cat, entry)
    if bid == "gemini":
        return _login_gemini(a, cat, entry)
    cred = entry["credential"]
    what = {"claude": "Claude token (from `claude setup-token`)", "kimi": "Kimi Code key"}[bid]
    if bid == "claude":
        _tty_print("Run `claude setup-token` in another terminal first; it prints a long token starting sk-ant-oat01-.")
    value, msg = _ask_hidden_twice(what, int(cred.get("floor", 20)))
    if value is None:
        _tty_print(msg)
        return _env("refused", "thinker_paste_refused", {"backend": bid}, 2, msg)
    fp = _fp(value)
    t = float(getattr(a, "timeout", 240) or 240)
    # negative control FIRST: a fake key through the identical sealed call must NOT prove -- else the proof below
    # could be answered by something other than the pasted key
    neg = _prove_value(bid, cat, cred["name"], "voidloom-negative-control-" + secrets.token_hex(12), t)
    if neg.get("passed"):
        msg = ("Refused: the sealed test call answered with a FAKE key, so it could not tell your key from another "
               "credential. Nothing was saved.")
        return _env("error", "thinker_negative_control_proved", {"negative": neg}, 1, msg)
    pos = _prove_value(bid, cat, cred["name"], value, t)
    if not pos.get("passed"):
        cause, step = pos.get("cause") or "", pos.get("next_step") or ""
        if not cause:
            cause, step = probe_cause(pos, entry)
        if cause == "auth":
            msg = ("The key was NOT saved: the test call refused it (%s). Check the key (guide Part %s) and run "
                   "this again." % (pos.get("error") or "no exact answer", entry.get("guide_part", "?")))
        else:      # Round 3 G1, item 1c: a stall / network / quota failure says nothing against the key itself
            msg = ("The key was NOT saved: the test call could not prove it (cause: %s). %s" % (cause, step))
        _tty_print(msg)
        return _env("error", "thinker_login_unproven", {"negative": _strip(neg), "proof": _strip(pos),
                                                        "fingerprint": fp}, 1, msg)
    p = _save_secret(cred["file"], cred["name"], value)
    msg = "Saved (%d characters, fingerprint %s). Proven: one test question answered with every tool off." % (
        len(value), fp)
    _tty_print(msg)
    return _env("ok", "thinker_login_saved", {"backend": bid, "file": str(p), "characters": len(value),
                                              "fingerprint": fp, "negative_control": _strip(neg),
                                              "proof": _strip(pos)}, 0, msg)


def _strip(res: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in res.items() if k in ("passed", "nonce_ok", "exact", "tool_block", "reason", "error",
                                                    "elapsed_s", "backend", "cause", "next_step", "model_reported",
                                                    "program")}


def _tty_print(msg: str) -> None:
    try:
        with open("/dev/tty", "w") as t:
            t.write(msg + "\n")
    except OSError:
        print(msg, file=sys.stderr)


def _ensure_cli_home(entry: Dict[str, Any]) -> Path:
    """The brain's own folder for a cli-home CLI, 0700 inside a 0700 thinker home, with its config_files' text present
    (appended when missing, never rewritten) and its empty_dirs made."""
    cred = entry["credential"]
    ch = cli_home_dir(entry)
    ch.mkdir(parents=True, exist_ok=True)
    os.chmod(str(thinker_home()), 0o700)
    os.chmod(str(ch), 0o700)
    for rel, want in (cred.get("config_files") or {}).items():
        cfg = ch / rel
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if not cfg.exists() or want.strip() not in cfg.read_text(encoding="utf-8"):
            fd = os.open(str(cfg), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, want.encode("utf-8"))
            os.close(fd)
    for d in cred.get("empty_dirs") or []:
        (ch / d).mkdir(parents=True, exist_ok=True)
        os.chmod(str(ch / d), 0o700)
    return ch


def _run_on_tty(argv: List[str], env: Dict[str, str], timeout_s: float, cwd: Optional[str] = None) -> int:
    tfd = os.open("/dev/tty", os.O_RDWR)
    try:
        return subprocess.run(argv, env=env, cwd=cwd, stdin=tfd, stdout=tfd, stderr=tfd, timeout=timeout_s).returncode
    finally:
        os.close(tfd)


def _login_codex(a, cat: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    binary = resolve_binary(entry)
    if not binary:
        return _env("refused", "thinker_missing", {"backend": "codex"}, 3, "Refused: codex is not installed.")
    cred = entry["credential"]
    ch = _ensure_cli_home(entry)
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.update(HOME=os.environ.get("HOME", str(_real_home())))
    env[cred["env"]] = str(ch)
    argv = [binary] + list(cred.get("login_browser_argv") if getattr(a, "browser", False) else cred["login_argv"])
    _tty_print("Signing the brain's own Codex folder in (%s). Your everyday ~/.codex is not touched." % ch)
    try:
        rc = _run_on_tty(argv, env, 900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _env("error", "thinker_login_failed", {"error": str(e)}, 1, "codex login did not finish: %s" % e)
    st_rc, st_out, st_err, st_why = _capture([binary] + list(cred["status_argv"]), env=env, timeout_s=60)   # bounded (MC-R1-12)
    status_text = (_text(st_out) + _text(st_err)).strip()[:120] if not st_why else \
        "`codex login status` %s" % ("gave no answer within 60 s" if st_why == "timeout" else
                                     "wrote more than %d bytes" % OUTPUT_CAP_BYTES)
    if rc != 0 or st_rc != 0:
        msg = ("The brain's Codex sign-in did not complete (login rc %s; status: %s). Nothing else changed. If device "
               "codes are off in ChatGPT, turn them on (guide B1) or run `voidloom thinker login codex --browser`."
               % (rc, status_text))
        return _env("error", "thinker_login_failed", {"status": status_text}, 1, msg)
    pos = probe_candidate(make_candidate(cat, "codex"), timeout_s=float(getattr(a, "timeout", 240) or 240), cat=cat,
                          tool_block=False)
    msg = "Codex: %s. Proof: %s." % (status_text, "PASSED" if pos.get("passed") else "FAILED (%s)" % pos.get("error"))
    _tty_print(msg)
    return _env("ok" if pos.get("passed") else "error", "thinker_login_codex", {"status": status_text,
                "proof": _strip(pos)}, 0 if pos.get("passed") else 1, msg)


def _owner_home() -> Path:
    """The OWNER's home from the password database -- what a program falls back to when it ignores $HOME
    (VAEL D1 #2: os/user.Current is linked into agy) -- resolved at run time, never typed."""
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return _real_home()


def sandbox_hidden_paths(entry: Dict[str, Any]) -> List[str]:
    """What the sandbox covers with an empty tmpfs: each catalog sandbox_hide path under BOTH this process's HOME and
    the owner's password-database home, and the per-user runtime folder (its D-Bus socket is how a keyring would be
    reached: VAEL D1 #1 -- godbus falls back to $XDG_RUNTIME_DIR/bus). Only folders that exist (bwrap cannot mount over
    a missing one, and a missing one holds nothing to hide)."""
    out: List[str] = []
    homes = [str(_real_home()), str(_owner_home())]
    for rel in (entry.get("credential") or {}).get("sandbox_hide") or []:
        for h in homes:
            p = os.path.normpath(os.path.join(h, rel[2:])) if rel.startswith("~/") else os.path.normpath(rel)
            if os.path.isdir(p) and p not in out:
                out.append(p)
    for r in ("/run/user/%d" % os.getuid(), os.environ.get("XDG_RUNTIME_DIR") or ""):
        if r and os.path.isdir(r) and os.path.normpath(r) not in out:
            out.append(os.path.normpath(r))
    return out


login_hidden_paths = sandbox_hidden_paths          # the sign-in and every call hide the SAME folders


def cli_home_env(entry: Dict[str, Any], ch: Path, *, term: str = "dumb") -> Dict[str, str]:
    """The environment a cli-home CLI runs under OUTSIDE a seam call (its sign-in): the PASS_ENV allowlist, the CLI's own
    variable = the brain's folder, and the catalog's hook-free switches expanded against it -- the SAME values every
    seam call sets (_build_cli), so the sign-in lands exactly where the calls read it. No D-Bus variable, ever."""
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(_real_home()))
    env[entry["credential"]["env"]] = str(ch)
    ctx = {"cli_home": str(ch), "thinker_home": str(thinker_home())}
    for k, v in (entry.get("hook_free_env") or {}).items():
        env[k] = _expand(v, ctx)
    if ch.is_dir():
        for d in entry["credential"].get("empty_dirs") or []:      # TMPDIR=<home>/tmp must exist before agy starts
            (ch / d).mkdir(parents=True, exist_ok=True)
            os.chmod(str(ch / d), 0o700)
    env["TERM"] = term
    return env


def bwrap_argv(bw: str, hide: List[str], env: Dict[str, str], cwd: str, argv: List[str]) -> List[str]:
    """bubblewrap around one program: the whole filesystem as it is (--dev-bind / /), every `hide` folder replaced by an
    empty tmpfs, the environment cleared and set to `env` alone, run from `cwd`."""
    out = [bw, "--dev-bind", "/", "/", "--die-with-parent"]
    for h in hide:
        out += ["--tmpfs", h]
    out.append("--clearenv")
    for k in sorted(env):
        out += ["--setenv", k, env[k]]
    return out + ["--chdir", cwd, "--"] + list(argv)


NO_SANDBOX_CAUSE = ("Gemini needs the bubblewrap sandbox so the brain's calls can't see your own Gemini sign-in: install "
                    "it with `sudo apt install bubblewrap` (or your distro's package), then run this again")


def _bwrap_ready() -> Tuple[str, str]:
    """(bwrap path, '') when bubblewrap can make a sandbox here, else ('', why). Decided by EXECUTION (a sandboxed
    /bin/true), never by the binary's presence alone. The command is shown; nothing ever runs sudo."""
    bw = shutil.which("bwrap")
    if not bw:
        return "", NO_SANDBOX_CAUSE
    try:
        rc, _o, err, why = _capture([bw, "--dev-bind", "/", "/", "--", "/bin/true"], timeout_s=20, cap=64 * 1024)
    except OSError as e:
        return "", "bwrap could not start (%s)" % e
    if why or rc != 0:
        return "", ("Gemini needs the bubblewrap sandbox so the brain's calls can't see your own Gemini sign-in, and "
                    "bwrap is installed but cannot make a sandbox on this computer (%s; unprivileged user namespaces may "
                    "be switched off)" % ((why or _text(err).strip()[:160]) or "rc %s" % rc))
    return bw, ""


def sandboxed_argv(bid: str, entry: Dict[str, Any], argv: List[str], env: Dict[str, str], cwd: str) -> List[str]:
    """THE ONLY WAY a sandboxed backend's program is started (owner run T2, 2026-10-16: agy read the owner's
    ~/.gemini sign-in through the password entry with HOME, XDG_RUNTIME_DIR and D-Bus all redirected -- so the
    environment alone is NOT isolation). For a backend whose credential names `sandbox: bwrap`, `argv` comes back
    wrapped in bubblewrap with the owner's folders hidden and the environment cleared to `env`; when bubblewrap cannot
    sandbox here the call is REFUSED (ThinkUnavailable 'missing', the cause names the install command) and the program
    never starts. Every gemini start -- an organ's call, the proof, the version question, `agy models` -- goes through
    here; there is no switch that skips it."""
    if (entry.get("credential") or {}).get("sandbox") != "bwrap":
        return argv
    bw, why = _bwrap_ready()
    if not bw:
        raise ThinkUnavailable("missing", "%s (cause: no-sandbox)" % why, bid)
    hide = sandbox_hidden_paths(entry)
    ch = cli_home_dir(entry)
    if ch is not None and any(_under(ch, Path(h)) for h in hide):
        raise ThinkUnavailable("unconfigured", "the brain's folder %s sits inside a folder the sandbox hides" % ch, bid)
    return bwrap_argv(bw, hide, env, cwd, argv)


def _prove_cli_home_empty(bid: str, cat: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    """THE LOGIN'S NEGATIVE CONTROL for a cli-home sign-in (SYNTHESIS R1/R8; ORIN: reuse _prove_value's throwaway thinker
    home): a sealed credential-only proof through the ORDINARY seam call -- sandboxed exactly as the timers' calls
    are -- with the CLI's home an EMPTY throwaway folder (the seam's "no sign-in file" pre-check is skipped for this
    one call, so the program itself is asked). It must NOT pass. If it does, something other than the brain's own
    sign-in answers for the program on this computer -- the owner's ~/.gemini found through the password database
    (VAEL D1 #2), a keyring, an API key -- and the real sign-in would prove nothing."""
    entry = cat["backends"][bid]
    cred = entry["credential"]
    tmp = Path(tempfile.mkdtemp(prefix="voidloom-login-negative-"))
    old = os.environ.get("VOIDLOOM_ENV_DIR")
    try:
        (tmp / "thinker" / cred["dir"]).mkdir(parents=True, mode=0o700)
        os.chmod(str(tmp / "thinker"), 0o700)
        os.environ["VOIDLOOM_ENV_DIR"] = str(tmp)
        cand = make_candidate(cat, bid)
        if not cand.get("binary"):
            return {"passed": False, "error": "the %s program is not installed" % bid}
        res = probe_candidate(cand, timeout_s=timeout_s, cat=cat, tool_block=False, credential_check=False)
        res["home_entries_after"] = sorted(str(q.relative_to(tmp)) for q in (tmp / "thinker").rglob("*"))[:40]
        return res
    finally:
        if old is None:
            os.environ.pop("VOIDLOOM_ENV_DIR", None)
        else:
            os.environ["VOIDLOOM_ENV_DIR"] = old
        shutil.rmtree(str(tmp), ignore_errors=True)


GEMINI_LOGIN_SCREEN = (
    "Gemini for the brain: its OWN Google sign-in, kept in {home}.\n"
    "Your everyday Gemini sign-in (~/.gemini) is hidden from every step below and is not touched.\n"
    "Google's terms: conversations made through this sign-in may be used to improve Google's products.\n")
GEMINI_LOGIN_STEP1 = "Step 1 of 3: a test call with NO sign-in must be refused (about a minute) ..."
GEMINI_LOGIN_STEP2 = (
    "Step 2 of 3: the Antigravity program starts now, inside a sandbox.\n"
    "  - If it asks about a theme or trusting a folder, accept the default.\n"
    "  - Choose to sign in with Google (if it does not offer it, type /login and press Enter).\n"
    "  - It shows a web address: open it in your browser and sign in with the Google account whose plan should do the\n"
    "    brain's thinking. Google then shows a code: copy it, paste it here and press Enter.\n"
    "  - When the program says you are signed in, type /exit and press Enter.")
GEMINI_LOGIN_STEP3 = "Step 3 of 3: one test question, with every tool off ..."


def _login_gemini(a, cat: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """`voidloom thinker login gemini` (Round 1 L-G; SYNTHESIS R1, binding order): the negative control FIRST, then agy's
    own interactive Google sign-in (the human pastes Google's code there -- a code Voidloom never reads, saves or asks
    twice: it is agy's prompt, and Google refuses a bad code loudly) run with HOME = the brain's gemini-home INSIDE
    bubblewrap, the owner's ~/.gemini and the per-user runtime folder (the keyring's bus) covered by empty tmpfs, then a
    sealed proof through the seam. The owner's sign-in file is stamped (sha + mtime) before and after."""
    bid = "gemini"
    cred = entry["credential"]
    t = float(getattr(a, "timeout", 240) or 240)
    binary = resolve_binary(entry)
    if not binary:
        return _env("refused", "thinker_missing", {"backend": bid}, 3,
                    "Refused: the Antigravity program (agy) is not installed (looked on PATH and in %s). Nothing was "
                    "changed." % ", ".join((entry.get("binary") or {}).get("candidates") or []))
    # the sandbox is MANDATORY (owner run T2: with only the environment redirected agy still read the owner's sign-in
    # through the password entry) -- there is no env-only path for the sign-in or for any call
    bw, why = _bwrap_ready()
    if not bw:
        return _env("refused", "thinker_login_needs_sandbox", {"backend": bid, "why": why, "cause": "no-sandbox"}, 3,
                    "Refused: %s. Nothing was changed.\n  cause: no-sandbox" % why)
    isolation = "bwrap"
    ch = cli_home_dir(entry)
    hide = login_hidden_paths(entry)
    if any(_under(ch, Path(h)) for h in hide):
        return _env("refused", "thinker_login_bad_home", {"backend": bid, "home": str(ch)}, 2,
                    "Refused: the brain's folder %s sits inside a folder the sign-in must hide. Nothing was changed." % ch)
    before = _owner_stamps(entry)
    _tty_print(GEMINI_LOGIN_SCREEN.format(home=ch))
    _tty_print("  isolation: a bubblewrap sandbox (your ~/.gemini and the keyring's bus are empty inside it), for the "
               "sign-in and for every call the brain makes")
    neg: Dict[str, Any] = {"skipped": True}
    if cred.get("login_negative_control"):
        _tty_print(GEMINI_LOGIN_STEP1)
        neg = _prove_cli_home_empty(bid, cat, t)
        # ANY answer is the failure here -- never `passed`, which later checks (the model's) could turn false while
        # the program had ANSWERED with no sign-in of the brain's (found by the gemini login test, fix pass)
        if neg.get("passed") or neg.get("ok") or neg.get("nonce_ok") or neg.get("answer"):
            msg = ("Refused: the test call ANSWERED with no sign-in at all, so something other than the brain's own sign-in "
                   "answers for agy on this computer (your everyday ~/.gemini, a keyring, or an API key). Nothing was "
                   "signed in.")
            _tty_print(msg)
            return _env("error", "thinker_negative_control_proved", {"negative": _strip(neg)}, 1, msg)
        _tty_print("  refused, as it must be (%s)." % (neg.get("reason") or neg.get("error") or "no answer")[:80])
    _ensure_cli_home(entry)
    env = cli_home_env(entry, ch, term=os.environ.get("TERM") or "xterm-256color")
    cwd = Path(tempfile.mkdtemp(prefix="voidloom-login-cwd-"))
    started = time.time()
    argv = bwrap_argv(bw, hide, env, str(cwd), [binary] + list(cred["login_argv"]))
    _tty_print(GEMINI_LOGIN_STEP2)
    try:
        rc = _run_on_tty(argv, env, 1800, cwd=str(cwd))
    except (OSError, subprocess.TimeoutExpired) as e:
        return _env("error", "thinker_login_failed", {"error": str(e)}, 1, "The Gemini sign-in did not finish: %s" % e)
    finally:
        shutil.rmtree(str(cwd), ignore_errors=True)
    after = _owner_stamps(entry)
    if before != after:
        msg = ("Your everyday Gemini sign-in file CHANGED during the brain's sign-in, which must never happen. Keep this "
               "message for a bug report; the brain's sign-in is not used.")
        _tty_print(msg)
        return _env("error", "thinker_login_owner_touched", {"before": before, "after": after}, 1, msg)
    auth = ch / cred["auth_file"]
    if not auth.is_file():
        wrote = sorted(str(q.relative_to(ch)) for q in ch.rglob("*") if q.is_file() and q.stat().st_mtime >= started
                       and "conversations" not in q.parts)[:12]
        msg = ("The Gemini sign-in did not complete: the program left no sign-in file (%s) in the brain's folder %s "
               "(it exited %s; files it wrote: %s). Run `voidloom thinker login gemini` again."
               % (cred["auth_file"], ch, rc, ", ".join(wrote) or "none"))
        _tty_print(msg)
        return _env("error", "thinker_login_failed", {"rc": rc, "wrote": wrote}, 1, msg)
    os.chmod(str(auth), 0o600)
    _tty_print(GEMINI_LOGIN_STEP3)
    pos = probe_candidate(make_candidate(cat, bid), timeout_s=t, cat=cat, tool_block=False)
    if pos.get("passed"):
        msg = ("Signed in: the brain's own Google sign-in is in %s. Proven: one test question answered with every tool "
               "off, asked of the pinned %s (the program names no model in its reply; the pin is enforced by its "
               "--model flag, which refuses any model it does not know); your everyday ~/.gemini unchanged."
               % (ch, pos.get("model_pinned") or (entry.get("model") or {}).get("pin") or "model"))
    else:
        msg = ("The brain is signed in (%s), but the test question did not pass (cause: %s). %s"
               % (ch, pos.get("cause") or "?", pos.get("next_step") or ""))
    _tty_print(msg)
    return _env("ok" if pos.get("passed") else "error", "thinker_login_gemini",
                {"home": str(ch), "negative_control": _strip(neg), "proof": _strip(pos),
                 "program": pos.get("program"), "isolation": isolation, "hidden": hide,
                 "owner_login_untouched": True},
                0 if pos.get("passed") else 1, msg)


def migrate_claude() -> Dict[str, Any]:
    """claude.env from the two legacy homes -- the env file's CLAUDE_CODE_OAUTH_TOKEN (read the way systemd reads
    it, by install/env_file.py) and the sidecar claude-oauth-token. Ends the two homes. Never removes either source
    (the old credential is kept); never chooses between two DIFFERENT tokens (the owner does, by login)."""
    home = thinker_home()
    target = home / "claude.env"
    if target.exists():
        return {"status": "ok", "code": "already", "file": str(target)}
    found: List[Tuple[str, str]] = []
    envf = default_env_file()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
        import env_file as _ef  # type: ignore
        if Path(envf).is_file():
            r = _ef.read_file(envf)
            if not r["error"]:
                got = r["effective"].get(CLAUDE_TOKEN_KEY)
                if got and got[0].strip():
                    found.append(("env file %s line %d" % (envf, got[1]), got[0].strip()))
    except Exception:
        pass
    try:
        side = voidloom_env.resolve_verbose(LEGACY_CLAUDE_SIDECAR)
        if side.exists:
            tok = side.path.read_text(encoding="utf-8").strip()
            if tok:
                found.append(("sidecar %s" % side.path, tok))
    except (OSError, ValueError):
        pass
    if not found:
        return {"status": "refused", "code": "nothing_to_migrate",
                "message": "no legacy Claude token found (env file or sidecar); run `voidloom thinker login claude`"}
    fps = sorted(set(_fp(t) for _s, t in found))
    if len(fps) > 1:
        return {"status": "refused", "code": "two_different_tokens",
                "message": "the legacy homes hold DIFFERENT tokens (%s); this does not choose between them -- run "
                           "`voidloom thinker login claude` and paste the one to keep" %
                           ", ".join("%s: %s" % (s, _fp(t)) for s, t in found),
                "sources": [{"source": s, "fingerprint": _fp(t)} for s, t in found]}
    p = _save_secret("claude.env", CLAUDE_TOKEN_KEY, found[0][1])
    return {"status": "ok", "code": "migrated", "file": str(p), "fingerprint": fps[0],
            "sources": [s for s, _t in found],
            "note": "the legacy sources are kept, unread by the thinker from now on; remove them when you are ready"}


# ---------------------------------------------------------------------------------------------------------------
# status, the nights, the weekly check, the expiry reminder (M14)
# ---------------------------------------------------------------------------------------------------------------
def nights(rows: Optional[List[Dict[str, str]]] = None, days: int = 7) -> Dict[str, Any]:
    """'unreachable N of the last 7 nights': a night (local calendar date of the call) is UNREACHABLE when it had
    thinker calls and every one failed with missing/auth/quota/unreachable/timeout; nights without calls are named."""
    rows = rows if rows is not None else read_log()
    today = _dt.date.today()
    window = [(today - _dt.timedelta(days=i)).isoformat() for i in range(days)]
    per: Dict[str, Dict[str, int]] = {d: {"ok": 0, "down": 0, "other": 0} for d in window}
    for r in rows:
        try:
            d = _dt.datetime.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=_dt.timezone.utc).astimezone().date().isoformat()
        except (KeyError, ValueError):
            continue
        if d not in per or r.get("purpose") in ("thinker_probe",):
            continue
        if r.get("outcome") == "ok":
            per[d]["ok"] += 1
        elif r.get("outcome") in ("unavailable", "timeout") and r.get("reason") != "unconfigured":
            per[d]["down"] += 1
        else:
            per[d]["other"] += 1
    down = [d for d in window if per[d]["down"] and not per[d]["ok"]]
    with_calls = [d for d in window if per[d]["ok"] or per[d]["down"]]
    return {"unreachable": len(down), "of": days, "nights_with_calls": len(with_calls), "unreachable_dates": down}


def status_report() -> Dict[str, Any]:
    out: Dict[str, Any] = {"record_path": str(record_path()), "thinker_home": str(thinker_home())}
    try:
        rec = read_record()
    except RecordError as e:
        rec, out["record_error"] = None, str(e)
    out["record"] = rec
    out["backend"] = (rec or {}).get("backend") or ("unreadable" if out.get("record_error") else "unset")
    out["proven_at"] = (rec or {}).get("proven_at", "")
    try:
        cat = load_catalog()
    except CatalogError as e:
        out["catalog_error"] = str(e)
        return out
    bid = out["backend"]
    if bid in cat["backends"]:
        cred = resolve_credential(bid, cat)
        if cred is None:
            out["credential"] = {"present": False}
        else:
            age = ""
            exp = ""
            if cred.saved_at:
                try:
                    sa = _dt.datetime.strptime(cred.saved_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
                    age = (_dt.datetime.now(_dt.timezone.utc) - sa).days
                    if bid == "claude":
                        exp = (sa + _dt.timedelta(days=CLAUDE_TOKEN_LIFE_DAYS)).strftime("%Y-%m-%d")
                except ValueError:
                    pass
            out["credential"] = {"present": True, "source": cred.source, "fingerprint": cred.fingerprint,
                                 "legacy": cred.legacy, "saved_at": cred.saved_at, "age_days": age,
                                 "next_expiry": exp or ("unknown (not saved by `thinker login`)" if bid == "claude"
                                                        else ((cat["backends"][bid].get("credential") or {})
                                                              .get("expiry_note") or
                                                              "none known (Codex renews itself; a Kimi key has no "
                                                              "stated expiry)"))}
            out["credential"]["legacy_homes_differ"] = bool(bid == "claude" and cred.legacy and
                                                             len(_legacy_claude_fingerprints()) > 1)
    out["nights"] = nights()
    checks = [r for r in read_log() if r.get("purpose") == "thinker_check"]
    out["last_weekly_check"] = checks[-1] if checks else None
    envf = default_env_file()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
        import env_file as _ef  # type: ignore
        r = _ef.read_file(envf) if Path(envf).is_file() else None
        out["env_file_carries_claude_token"] = bool(r and not r["error"] and
                                                    CLAUDE_TOKEN_KEY in r["effective"])
        if out["env_file_carries_claude_token"]:
            out["env_file_claude_token_line"] = r["effective"][CLAUDE_TOKEN_KEY][1]
            out["env_file"] = envf
    except Exception:
        out["env_file_carries_claude_token"] = None
    out["claude_env_saved"] = (thinker_home() / "claude.env").is_file()
    if out.get("env_file_carries_claude_token"):
        out["units_needing_env_token"] = units_needing_env_token()
    out["gap_research"] = _gap_research_state()
    return out


def _gap_research_state() -> Dict[str, Any]:
    """The last gap-research outcome THINKER_LOG holds -- the doctor line for a brain whose gap_research_log predates
    migration_039_gap_research_log_thinker.sql (the organ SKIPS loudly there, never rebuilds the table itself)."""
    rows = [r for r in read_log() if r.get("purpose") == "gap_research"]
    if not rows:
        return {}
    last = rows[-1]
    return {"at": last.get("at", ""), "outcome": last.get("outcome", ""), "reason": last.get("reason", "")}


CRON_ROSTER_PATH = Path(__file__).resolve().parent.parent / "install" / "cron_roster.json"


def _armed_timer_names() -> Optional[set]:
    """The wizard-library-<name> timers present in the user's systemd folder (read-only), or None when there is none."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (_real_home() / ".config")) / "systemd" / "user"
    try:
        names = {q.name[len("wizard-library-"):-len(".timer")] for q in base.glob("wizard-library-*.timer")}
    except OSError:
        return None
    return names or None


# ---- M13, the HOST half (narrow pass N1, the Round 2 reflector's R2-01) -------------------------------------------
# The roster names only the units THIS package writes. A host keeps its own jobs on the same env file (the reference
# host's second-brain units load it by its link target and run claude themselves), so "who still needs the token line" is
# derived from TWO sources: the roster (units_needing_env_token's first half) AND every unit file INSTALLED in the
# user's systemd folders whose EnvironmentFile= names the env file -- by its own path, any link on the way to it, its
# final target, the voidloom link, or merely its file name. Such a unit is cleared only when its Claude path is PROVEN
# to be the seam's: it is a roster unit whose claude_credential is `thinker` or `none` AND every Exec*= line runs this
# brain's own organs/<script> for that roster entry with a python interpreter. Anything else -- not on the roster, a
# script elsewhere, a shell, an unresolved specifier, an unreadable file or folder -- is STILL NEEDED and named (fail
# closed: over-inclusion can only keep the line, never call it removable).
_EXEC_KEYS = ("ExecCondition", "ExecStartPre", "ExecStart", "ExecStartPost", "ExecReload", "ExecStop", "ExecStopPost")
_PY_FLAG = re.compile(r"-[uBOsEIq]+")
_PY_NAME = re.compile(r"python(\d+(\.\d+)?)?")


def _user_unit_dirs() -> List[Path]:
    """The user's systemd unit folders (read only): $XDG_CONFIG_HOME/systemd/user and $XDG_DATA_HOME/systemd/user."""
    home = _real_home()
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    return [cfg / "systemd" / "user", data / "systemd" / "user"]


def _unit_assignments(text: str) -> List[Tuple[str, str]]:
    """(key, value) for every assignment in a unit file, as systemd reads it: a trailing backslash joins the next line;
    blank lines, comments (# ;) and [Section] headers carry no assignment."""
    out: List[Tuple[str, str]] = []
    buf = ""
    for raw in text.splitlines() + [""]:
        line = raw.rstrip()
        if not buf and (not line.strip() or line.lstrip()[:1] in ("#", ";")):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        line, buf = (buf + line).strip(), ""
        if line.startswith("[") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out.append((k.strip(), v.strip()))
    return out


def _unit_expand(value: str, home: str) -> Tuple[str, bool]:
    """systemd's %h (the user's home) and %% in a unit value -> (value, resolved). Any other specifier leaves it
    unresolved: the caller then cannot show what the value names, and fails closed."""
    out, i, ok = [], 0, True
    while i < len(value):
        c = value[i]
        if c == "%":
            nxt = value[i + 1:i + 2]
            if nxt == "h":
                out.append(home)
            elif nxt == "%":
                out.append("%")
            else:
                out.append(value[i:i + 2])
                ok = False
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), ok


def _env_file_identity(env_file: str) -> Tuple[set, set]:
    """(paths, names) that count as THE env file: the file, every link on the way to it, its final target and the
    voidloom link (with theirs); and their file names (a unit naming a file of the same name is not proven to load
    another one -- a second machine's units name the link's TARGET, not the link)."""
    paths, names = set(), {"wizard-library-env"}
    cands = [env_file]
    try:
        cands.append(str(voidloom_env.env_path("wizard-library-env")))
    except Exception:  # noqa: BLE001 -- the resolver's own failure only drops a candidate the name rule still covers
        pass
    for c in cands:
        if not c:
            continue
        p = os.path.normpath(os.path.abspath(os.path.expanduser(c)))
        hops = 0
        while True:
            paths.add(p)
            names.add(os.path.basename(p))
            if not os.path.islink(p) or hops >= 40:
                break
            try:
                p = os.path.normpath(os.path.join(os.path.dirname(p), os.readlink(p)))
            except OSError:
                break
            hops += 1
        rp = os.path.realpath(c)
        paths.add(rp)
        names.add(os.path.basename(rp))
    return paths, names


def _names_env_file(value: str, home: str, paths: set, names: set) -> bool:
    """Does an EnvironmentFile= value (systemd: an optional leading '-', a path, globbing) name the env file?"""
    for tok in value.split():
        v, ok = _unit_expand(tok.strip("\"'").lstrip("-"), home)
        if not ok or any(ch in v for ch in "*?[\\"):
            return True                       # unresolved or a glob: it cannot be shown to name another file
        p = os.path.normpath(v)
        if p in paths or os.path.realpath(p) in paths or os.path.basename(p) in names:
            return True
    return False


def _exec_runs(value: str, home: str, allowed: set) -> Tuple[bool, str]:
    """Is an Exec*= line `<python> [-u|-B ...] <script> ...` (or `/usr/bin/env python3 ...`) with <script> one of
    `allowed` (real paths)? (proven, what it runs)."""
    v, ok = _unit_expand(value, home)
    toks = v.split()
    if not ok or not toks:
        return False, v or "(empty)"
    first = toks[0].lstrip("@-:+!")
    i = 1
    if os.path.basename(first) == "env" and len(toks) > 1:
        first, i = toks[1], 2
    if not _PY_NAME.fullmatch(os.path.basename(first)):
        return False, first
    while i < len(toks) and _PY_FLAG.fullmatch(toks[i]):
        i += 1
    if i >= len(toks):
        return False, v
    script = os.path.normpath(toks[i])
    if script in allowed or os.path.realpath(script) in allowed:
        return True, script
    return False, script


def _dropin_dirs(unit_file_name: str, dirs: List[Path]) -> List[Path]:
    """Every drop-in folder systemd merges for <name>.service (man systemd.unit, systemd 259 on the reference host):
    <name>.service.d, each dash-truncated prefix <p>-.service.d, and the top-level service.d -- in EVERY unit folder, not
    only the main file's (RC-01, the narrow-pass-N re-check: reading only the main file's own <name>.service.d said 'can
    go' for a unit whose prefix, top-level or other-folder drop-in loads the env file)."""
    stem, typ = unit_file_name.rsplit(".", 1)
    names, parts = [unit_file_name + ".d"], stem.split("-")
    names += ["-".join(parts[:i]) + "-.%s.d" % typ for i in range(len(parts) - 1, 0, -1)]
    names.append("%s.d" % typ)
    return [d / n for d in dirs for n in names]


def _loads_env_file(kv: List[Tuple[str, str]], home: str, paths: set, names: set) -> bool:
    """Does a unit (its merged assignments) load the env file? EnvironmentFile= naming it, OR an Exec*= line that
    mentions it -- by path, link, target or file name (RC-02: `ExecStart=/bin/bash -c '. <env file>; exec claude ...'`
    reads the token line with no EnvironmentFile= at all). Fail closed: an Exec line that merely MENTIONS the file is
    counted as loading it."""
    for k, v in kv:
        if k == "EnvironmentFile" and _names_env_file(v, home, paths, names):
            return True
        if k in _EXEC_KEYS and any(_names_env_file(tok, home, paths, names)
                                   for tok in re.split(r"[\s;'\"&|()=]+", v) if "/" in tok or tok in names):
            return True
    return False


def installed_env_file_readers(roster_units: Dict[str, Any], skip: Optional[set] = None,
                               unit_dirs: Optional[List[Path]] = None, home: Optional[str] = None,
                               brain: Optional[Path] = None, env_file: Optional[str] = None) -> List[Dict[str, str]]:
    """M13's second source (narrow pass N1): every INSTALLED *.service (with its drop-ins) in the user's systemd folders
    that loads the env file and is not PROVEN to take Claude's credential from the seam. [{unit, file, why}], sorted;
    roster names in `skip` are already counted by the roster half. Read only; the arguments exist so a test can derive
    the HOST's view from copies of its unit files."""
    home = home if home is not None else str(_real_home())
    brain = Path(brain) if brain is not None else brain_dir()
    paths, names = _env_file_identity(env_file if env_file is not None else default_env_file())
    skip = skip or set()
    found: Dict[str, Dict[str, str]] = {}
    dirs = list(unit_dirs if unit_dirs is not None else _user_unit_dirs())
    seen: set = set()                         # every main unit found, flagged or not (an earlier folder wins)
    for d in dirs:
        try:
            files = sorted(q for q in d.iterdir() if q.name.endswith(".service"))
        except FileNotFoundError:
            continue
        except OSError as e:
            found.setdefault(str(d), {"unit": str(d), "file": str(d),
                                      "why": "the unit folder could not be read (%s)" % e})
            continue
        for f in files:
            stem = f.name[:-len(".service")]
            if stem in seen:
                continue                      # a folder earlier in the list wins, as systemd's search path does
            seen.add(stem)
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                for dd in _dropin_dirs(f.name, dirs):       # RC-01: every drop-in systemd merges, from every folder
                    for dropin in sorted(dd.glob("*.conf")):
                        text += "\n" + dropin.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                found[stem] = {"unit": stem, "file": str(f), "why": "its unit file could not be read (%s)" % e}
                continue
            kv = _unit_assignments(text)
            if not _loads_env_file(kv, home, paths, names):  # RC-02: EnvironmentFile= OR an Exec line naming it
                continue
            short = stem[len("wizard-library-"):] if stem.startswith("wizard-library-") else None
            if short is not None and short in skip:
                continue
            u = roster_units.get(short) if short is not None else None
            if not isinstance(u, dict):
                found[stem] = {"unit": stem, "file": str(f),
                               "why": "it is not on the package's roster, so how it reaches Claude is not proven"}
                continue
            if u.get("claude_credential") not in ("thinker", "none"):
                found[stem] = {"unit": stem, "file": str(f),
                               "why": "its roster entry declares claude_credential %r" % u.get("claude_credential")}
                continue
            allowed, own = set(), []
            for key in ("exec", "exec_post"):
                rel = (u.get(key) or "").split()
                if rel:
                    own.append(os.path.normpath(str(brain / rel[0])))
                    allowed.update((own[-1], os.path.realpath(own[-1])))
            execs = [(k, _exec_runs(v, home, allowed)) for k, v in kv if k in _EXEC_KEYS]
            bad = [(k, what) for k, (proven, what) in execs if not proven]
            if not execs or bad:
                found[stem] = {"unit": stem, "file": str(f),
                               "why": ("its %s runs %s, not this brain's own %s" % (bad[0][0], bad[0][1],
                                                                                   " / ".join(own) or "organ"))
                               if bad else "it has no Exec line to prove"}
    # RC-01, fail closed: a drop-in folder whose main unit is in NONE of the user's folders -- an exact-name folder for a
    # unit installed elsewhere, a dash-prefix folder, or the top-level service.d (both merge into units this read cannot
    # enumerate) -- that loads the env file is a reader nobody can show to be the seam
    for d in dirs:
        try:
            dd_all = sorted(q for q in d.iterdir()
                            if (q.name == "service.d" or q.name.endswith(".service.d")) and q.is_dir())
        except OSError:
            continue
        for dd in dd_all:
            stem = "" if dd.name == "service.d" else dd.name[:-len(".service.d")]
            if stem and stem in seen and not stem.endswith("-"):
                continue                      # merged into a main unit above, and judged there
            try:
                text = "\n".join(c.read_text(encoding="utf-8", errors="replace") for c in sorted(dd.glob("*.conf")))
            except OSError as e:
                found.setdefault("dropin:" + str(dd), {"unit": dd.name, "file": str(dd),
                                                       "why": "a drop-in folder could not be read (%s)" % e})
                continue
            if _loads_env_file(_unit_assignments(text), home, paths, names):
                found.setdefault("dropin:" + str(dd), {
                    "unit": dd.name, "file": str(dd),
                    "why": "a drop-in loads the env file for %s whose main file is outside the user's folders"
                           % ("every service" if not stem else
                              "units named %s*" % stem if stem.endswith("-") else "a unit (%s.service)" % stem)})
    return [found[k] for k in sorted(found)]


def units_needing_env_token() -> Dict[str, Any]:
    """M13 with the OWNER's condition (Round 2 wave 1, ruler VL-MC-v1.1: "as long as the claude crons still fire
    properly"): the ARMED units that would lose their Claude credential if the env file's CLAUDE_CODE_OAUTH_TOKEN line
    were deleted -- every roster unit whose `claude_credential` is `env-file` (it reads the token from its OWN
    environment, or launches claude itself, instead of the seam, which reads claude.env first). Armed = the roster's
    armed-by-default units plus any roster unit whose timer is present in the user's systemd folder. The declaration is
    derived from the code and pinned to it (install/cron_roster.py claude-callers --derive). {'units': [...],
    'provable': bool, 'why': str} -- provable False (roster unreadable, or a unit without the field) means the line
    can NOT be shown to be unneeded."""
    try:
        doc = json.loads(CRON_ROSTER_PATH.read_text(encoding="utf-8"))
        units = doc["units"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        try:
            outside = installed_env_file_readers({})
        except Exception:  # noqa: BLE001
            outside = []
        return {"units": [o["unit"] for o in outside], "provable": False, "installed_readers": outside,
                "why": "the unit roster %s could not be read (%s)" % (CRON_ROSTER_PATH, e)}
    present = _armed_timer_names() or set()
    need, unknown = [], []
    for name, u in sorted(units.items()):
        armed = u.get("arm") == "armed-by-default" or name in present
        if not armed:
            continue
        cred = u.get("claude_credential")
        if cred == "env-file":
            need.append(name)
        elif cred not in ("thinker", "none"):
            unknown.append(name)
    # the second source (narrow pass N1, R2-01): the INSTALLED units that load the env file themselves
    try:
        outside = installed_env_file_readers(units, skip=set(need) | set(unknown))
    except Exception as e:  # noqa: BLE001 -- an unreadable host is not a removable line
        outside = [{"unit": "(the user's systemd folders)", "file": "",
                    "why": "they could not be read (%s: %s)" % (type(e).__name__, e)}]
    if unknown or outside:
        why = "; ".join(x for x in (
            ("armed unit(s) %s declare no claude_credential" % ", ".join(unknown)) if unknown else "",
            ("installed unit(s) load the env file and are not proven to take Claude's credential from the thinker: %s"
             % "; ".join("%s (%s)" % (o["unit"], o["why"]) for o in outside)) if outside else "") if x)
        return {"units": need + unknown + [o["unit"] for o in outside], "provable": False, "why": why,
                "installed_readers": outside}
    return {"units": need, "provable": True, "why": "", "installed_readers": []}


def token_line_advice(rep: Dict[str, Any]) -> str:
    """M13 (the bounded part, Round 1 Wave B): the owner's CLAUDE_CODE_OAUTH_TOKEN line in the env file sits OUTSIDE the
    managed block, where the env file's one writer keeps it and no machine removes it. The words say how the owner moves
    it -- plain, one act -- and whether the line can go afterwards: only when units_needing_env_token() PROVES no unit
    still reads it (the roster AND the host's installed units, narrow pass N1); otherwise it is STILL NEEDED and the
    units are named. '' when there is nothing to say."""
    if not rep.get("env_file_carries_claude_token"):
        return ""
    where = "%s, line %s" % (rep.get("env_file", "the env file"), rep.get("env_file_claude_token_line", "?"))
    if rep.get("claude_env_saved"):
        # the OWNER's condition (M13, ruler VL-MC-v1.1): "it can go" only when it is TRUE -- no armed unit still reads
        # the token from its own environment
        need = rep.get("units_needing_env_token") or units_needing_env_token()
        if need.get("units") or not need.get("provable"):
            return ("note: the old %s line in %s (outside the managed block) is STILL NEEDED -- %s. Keep it: deleting it "
                    "would stop %s from getting its Claude credential. The overnight thinker itself reads %s/claude.env."
                    % (CLAUDE_TOKEN_KEY, where,
                       ("these armed units read it from their own environment: %s" % ", ".join(need["units"]))
                       if need.get("units") and need.get("provable") else
                       ("it cannot be shown to be unneeded: %s" % need.get("why") +
                        ("; units that may read it: %s" % ", ".join(need["units"]) if need.get("units") else "")),
                       ", ".join(need.get("units") or []) or "a unit that reads it", thinker_home()))
        return ("note: the old %s line in %s (outside the managed block) is no longer needed -- the overnight thinker "
                "reads its own saved copy in %s/claude.env, and no armed unit reads the line itself (every Claude caller "
                "on the roster goes through the thinker, and no other installed unit loads the env file). Delete that "
                "line yourself when you are ready; nothing removes it for you." % (CLAUDE_TOKEN_KEY, where, thinker_home()))
    # narrow pass N1 (R2-01): "after that the line can go" only when that is TRUE -- the same derivation as above
    need = rep.get("units_needing_env_token") or units_needing_env_token()
    if need.get("provable") and not need.get("units"):
        after = "After that the line can go -- delete it yourself; nothing removes it for you."
    else:
        after = ("Even after that, KEEP the line -- it is STILL NEEDED: %s." % (
            need.get("why") or "these armed units read it from their own environment: %s" % ", ".join(need.get("units")
                                                                                                     or [])))
    return ("note: your Claude token (%s) is still kept in %s (outside the managed block), where every nightly job can read "
            "it. To move it to the overnight thinker alone, run `voidloom thinker login claude` in a terminal and paste "
            "the token twice (`voidloom thinker migrate` copies it for you instead). %s" % (CLAUDE_TOKEN_KEY, where, after))


def health_line(rep: Optional[Dict[str, Any]] = None) -> str:
    """ONE line for doctor, the weekly digest and token_doctor."""
    rep = rep or status_report()
    b = rep.get("backend")
    if rep.get("record_error"):
        return "thinker: RECORD REFUSED (%s) -- re-run `voidloom thinker set`" % rep["record_error"]
    if b == "unset":
        return "thinker: not chosen yet -- nothing thinks overnight until the owner answers (`voidloom thinker set`)"
    if b == "off":
        return "thinker: off (the owner answered e) not now; simple nightly routines only)"
    n = rep.get("nights") or {}
    cred = rep.get("credential") or {}
    rec = rep.get("record") or {}
    parts = ["thinker: %s (proven %s)" % (b, rep.get("proven_at") or "?"),
             "unreachable %d of %d nights (%d with calls)" % (n.get("unreachable", 0), n.get("of", 7),
                                                             n.get("nights_with_calls", 0))]
    if rec.get("accepted_agentic_risk") == "true":
        # SEAM_DESIGN section 3: an accepted none-class program is shown FOREVER, on every status and doctor line
        parts.append("AGENTIC RISK ACCEPTED by the owner %s: this command's tools are NOT proven off (the empty-folder "
                     "tripwire stays on)" % (rec.get("accepted_agentic_at") or "?"))
    gr = rep.get("gap_research") or {}
    if gr.get("outcome") == "skipped" and gr.get("reason") == "schema-not-widened":
        parts.append("gap research SKIPPED (%s): the brain's gap_research_log predates migration_039_gap_research_log_thinker.sql -- re-run the "
                     "installer (its migration step widens it)" % gr.get("at", "?"))
    if cred.get("present") is False:
        parts.append("NO CREDENTIAL -- `voidloom thinker login %s`" % b)
    elif cred:
        parts.append("credential %s%s" % (cred.get("fingerprint", ""), " (LEGACY home)" if cred.get("legacy") else ""))
        if cred.get("legacy_homes_differ"):
            parts.append("the two LEGACY Claude homes hold DIFFERENT tokens (the sidecar is the one used) -- run "
                         "`voidloom thinker login claude` and paste the one to keep")
        if cred.get("next_expiry") and cred["next_expiry"][:1].isdigit():
            parts.append("expires %s" % cred["next_expiry"])
    return "; ".join(parts)


def weekly_check(timeout_s: float = 240.0) -> Dict[str, Any]:
    """The weekly credential check (the whisper-digest unit runs it): one probe of the recorded backend, logged;
    alerts on the FIRST auth failure (the previous check was fine) and ~30 days before Claude's token expires."""
    rep = status_report()
    b = rep.get("backend")
    alerts: List[str] = []
    res: Dict[str, Any] = {"backend": b, "at": _now()}
    if b in ("unset", "off", "unreadable") or not rep.get("record"):
        res["skipped"] = "no thinker recorded (%s)" % b
        return res
    prev = [r for r in read_log() if r.get("purpose") == "thinker_check"]
    pr = probe_candidate(rep["record"], timeout_s=timeout_s, tool_block=False)
    res["probe"] = _strip(pr)
    outcome = "ok" if pr.get("passed") else ("auth" if pr.get("reason") == "auth" else "failed")
    _log(purpose="thinker_check", backend=b, outcome=outcome, reason=pr.get("reason", ""))
    if outcome == "auth" and (not prev or prev[-1].get("outcome") != "auth"):
        alerts.append("ALERT: the overnight thinker's %s credential was REFUSED (%s). Fix: docs/OVERNIGHT_THINKER_"
                      "SETUP.md, the %s part, then `voidloom thinker login %s`." % (b, pr.get("error", ""), b, b))
    elif outcome != "ok":
        alerts.append("the overnight thinker (%s) did not answer its weekly check: %s (cause: %s -- %s)"
                      % (b, pr.get("error", ""), pr.get("cause", "?"), pr.get("next_step", "")))
    prog = (pr.get("program") or {})
    rec = rep.get("record") or {}
    if prog.get("sha16") and rec.get("program_sha16") and prog["sha16"] != rec["program_sha16"]:
        # SYNTHESIS R7: the vendor's program changed under the timers since the proof (its updater, or the owner's
        # own install) -- a breadcrumb, not an outage: the check above says whether it still answers
        alerts.append("note: the %s program changed since it was proven (version %s -> %s, sha %s -> %s); `voidloom "
                      "thinker set %s` re-proves it" % (b, rec.get("program_version") or "?", prog.get("version") or "?",
                                                        rec["program_sha16"], prog["sha16"], b))
    cred = rep.get("credential") or {}
    exp = cred.get("next_expiry", "")
    if b == "claude" and exp[:1].isdigit():
        left = (_dt.date.fromisoformat(exp) - _dt.date.today()).days
        if left <= EXPIRY_WARN_DAYS:
            alerts.append("REMINDER: the Claude one-year token expires %s (%d days): run `claude setup-token`, "
                          "then `voidloom thinker login claude` (guide Part C)." % (exp, left))
    res["alerts"] = alerts
    res["line"] = health_line()
    return res


# ---------------------------------------------------------------------------------------------------------------
# THE a-e QUESTION'S FACTS (model-choice Round 1, Wave B -- M10): what the installer FOUND, shown beside each letter,
# never a pre-selection. Read-only: a PATH lookup, the credential resolver (fingerprints, never values), the record.
# ---------------------------------------------------------------------------------------------------------------
def question_facts(env_file: Optional[str] = None) -> Dict[str, Any]:
    cat = load_catalog()
    q = cat["question"]
    out: Dict[str, Any] = {"question": q, "record": None, "record_error": "", "facts": {}, "guide_part": {},
                           "labels": {}, "evidence": [], "saved": {}}
    try:
        out["record"] = read_record()
    except RecordError as e:
        out["record_error"] = str(e)
    by_letter: Dict[str, List[str]] = {}
    for bid, e in cat["backends"].items():
        by_letter.setdefault(e["letter"], []).append(bid)
    home = thinker_home()
    for letter, _words, answer in q["options"]:
        facts: List[str] = []
        if answer in cat["backends"]:
            e = cat["backends"][answer]
            refusal = platform_refusal(e)
            if refusal:
                facts.append(refusal)
            elif (e.get("credential") or {}).get("sandbox") == "bwrap" and not _bwrap_ready()[0]:
                facts.append(_bwrap_ready()[1])
            b = resolve_binary(e)
            prog = (((cat.get("question") or {}).get("program_words") or {}).get(answer) or {}).get("program") or answer
            cmd = ((e.get("binary") or {}).get("names") or [answer])[0]
            prog = prog if prog == cmd else "%s (%s)" % (prog, cmd)       # r1 F7: the program's name, not the id
            facts.append("%s found at %s" % (answer, b) if b else "%s is not installed on this machine" % prog)
            cred = resolve_credential(answer, cat, env_file=env_file)      # R3-07: the env file the installer names
            if cred is None:
                facts.append("no credential saved for the brain yet (the guide's Part %s, one step)" % e["guide_part"])
            elif cred.legacy:
                facts.append("a token in %s -- evidence you used it before, not an answer" % cred.source)
            else:
                facts.append("a credential saved for the brain (fingerprint %s)" % cred.fingerprint)
            if cred is not None:
                # Round 3 G1, item 3: when the Part's one step is ALREADY done, the installer says so -- in these words --
                # instead of sending the owner to make a new credential (the proof uses the saved one; `thinker login`
                # replaces it). Never a value: where it is, and its fingerprint.
                noun = (e.get("credential") or {}).get("noun") or "credential"
                out["saved"][letter] = {"line": "a %s is already saved; press Enter to prove it" % noun,
                                        "noun": noun, "where": cred.source, "fingerprint": cred.fingerprint,
                                        "legacy": bool(cred.legacy)}
            out["guide_part"][letter] = e["guide_part"]
            out["labels"][letter] = e["label"]
        elif answer == "local":
            w = shutil.which("ollama")
            facts.append("ollama found at %s" % w if w else "no ollama on PATH")
            facts.append("a model on this machine or another tool: you name it next")
            out["guide_part"][letter] = cat["backends"]["local-http-ollama"]["guide_part"]
        else:
            facts.append("nothing to set up; the nightly routines keep their simple paths and nothing thinks overnight")
        out["facts"][letter] = facts
    envf = env_file or default_env_file()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
        import env_file as _ef  # type: ignore
        r = _ef.read_file(envf) if Path(envf).is_file() else None
        if r and not r["error"] and CLAUDE_TOKEN_KEY in r["outside"]:
            out["evidence"].append("%s carries %s on line %d (outside the managed block): your timers could reach "
                                   "Claude with it -- evidence, not an answer" % (envf, CLAUDE_TOKEN_KEY,
                                                                                   r["outside"][CLAUDE_TOKEN_KEY][1]))
    except Exception:
        pass
    out["thinker_home"] = str(home)
    return out


def cmd_check_command(a) -> int:
    """`_check_command --command T [--model M]`: the installer's d) follow-up asks this before it accepts a template
    (MC-R1-01): ONE JSON line; rc 0 = runnable and its tools proven off by construction, 2 = refused (the token and the
    fix are named), 3 = its tools cannot be proven off (only the owner can accept that: --accept-agentic)."""
    try:
        cat = load_catalog()
        toks = template_argv(a.command)
        first = toks[0].replace("{model}", a.model or "")
        binary = resolve_binary({"binary": {"names": [first], "candidates": []}}) if not os.path.isabs(first) \
            else resolve_binary({}, first)
        v = check_template(toks, model=a.model or "", binary=binary, cat=cat)
    except TemplateRefused as e:
        print(json.dumps({"ok": False, "kind": e.kind, "token": e.token, "message": str(e)}))
        return 2
    except CatalogError as e:
        print(json.dumps({"ok": False, "kind": "catalog", "token": "", "message": str(e)}))
        return 2
    if v["tool_class"] == "none":
        print(json.dumps({"ok": False, "kind": "agentic-unproven", "token": toks[0], "tool_class": "none",
                          "message": "the brain cannot PROVE that %s runs with no tools (only a known tool-free "
                                     "runner, e.g. ollama run, is proven by construction)" % toks[0]}))
        return 3
    print(json.dumps({"ok": True, "tool_class": v["tool_class"], "runner": v["runner"], "binary": binary}))
    return 0


def cmd_question(a) -> int:
    """`_question`: the facts as ONE JSON line for the installer (never a credential value)."""
    try:
        print(json.dumps(question_facts(a.env_file or None)))
        return 0
    except CatalogError as e:
        print(json.dumps({"catalog_error": str(e)}))
        return 1


def cmd_status(a) -> Dict[str, Any]:
    rep = status_report()
    line = health_line(rep)
    human = [line, "  record: %s" % rep["record_path"], "  credentials: %s" % rep["thinker_home"]]
    adv = token_line_advice(rep)
    if adv:
        human.append("  " + adv)
    if (rep.get("credential") or {}).get("legacy"):
        human.append("  note: the Claude token comes from a LEGACY home (%s); `voidloom thinker migrate` moves it "
                     "into claude.env." % rep["credential"].get("source"))
    rc = 1 if rep.get("record_error") else 0
    return _env("ok" if rc == 0 else "error", "thinker_status", rep, rc, "\n".join(human))


def _given(flag: str):
    """argparse `type` for a value flag: an explicitly EMPTY value is refused, never taken as absent (Round 2 wave 1,
    MC-R1-17). The flag's default must be None, not '': argparse ALSO runs `type` over a STRING default (measured: with
    default='' every call that omitted the flag was refused), and never over a non-string one; every use reads
    `a.<flag> or ""`."""
    def f(v: str) -> str:
        if not v.strip():
            raise argparse.ArgumentTypeError("%s was given an EMPTY value -- pass the owner's answer, never a blank "
                                             "(nothing was recorded)" % flag)
        return v
    return f


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="voidloom thinker", description="The overnight thinker: who thinks, proven.")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("set")
    s.add_argument("backend", choices=list(ANSWERS))
    s.add_argument("--model", default=None, type=_given("--model"))
    s.add_argument("--endpoint", default=None, type=_given("--endpoint"))
    s.add_argument("--api", default="", choices=["", "ollama", "openai"])
    s.add_argument("--command", default=None, type=_given("--command"))
    s.add_argument("--binary", default=None, type=_given("--binary"))
    s.add_argument("--base-url", default=None, type=_given("--base-url"))
    s.add_argument("--source", default="", choices=["", "installer", "thinker-set", "voidstead"])
    s.add_argument("--pin", default="")
    s.add_argument("--answered-by", default="", choices=[""] + list(ANSWERED_BY))
    s.add_argument("--env-file", default="")
    s.add_argument("--probe-env", default="", choices=["", "systemd", "direct"])
    s.add_argument("--timeout", default="240")
    s.add_argument("--no-login", action="store_true")
    s.add_argument("--accept-agentic", action="store_true",
                   help="d) command templates only: record that the OWNER accepts a program whose tools cannot be "
                        "proven off (kept in THINKER_RECORD, shown by every status and doctor line)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("login")
    s.add_argument("backend", choices=list(LOGIN_BACKENDS))
    s.add_argument("--browser", action="store_true", help="codex only: the browser sign-in instead of a device code")
    s.add_argument("--timeout", default="240")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("probe")
    s.add_argument("--backend", default="", choices=[""] + [x for x in ANSWERS if x != "off"])
    s.add_argument("--model", default=None, type=_given("--model"))
    s.add_argument("--endpoint", default=None, type=_given("--endpoint"))
    s.add_argument("--api", default="", choices=["", "ollama", "openai"])
    s.add_argument("--command", default=None, type=_given("--command"))
    s.add_argument("--timeout", default="240")
    s.add_argument("--accept-agentic", action="store_true")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("check")
    s.add_argument("--timeout", default="240")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("migrate")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("_probe")
    s.add_argument("--candidate", required=True)
    s.add_argument("--timeout", default="240")
    s = sub.add_parser("_question")
    s.add_argument("--env-file", default="")
    s = sub.add_parser("_check_command")
    s.add_argument("--command", required=True)
    s.add_argument("--model", default="")
    return ap


def run(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    """The `voidloom thinker` organ verb: parse argv, RETURN the envelope (the CLI prints it once)."""
    argv = list(argv or [])
    ap = build_parser()
    import contextlib
    import io
    said = io.StringIO()
    try:
        with contextlib.redirect_stderr(said):
            a = ap.parse_args(argv)
    except SystemExit as e:
        why = [l for l in said.getvalue().splitlines() if "error:" in l]
        return _env("error", "thinker_usage", {"argv": argv, "error": (why[-1] if why else "")}, 2,
                    (why[-1].split("error:", 1)[1].strip() + "\n" if why else "") + "usage: voidloom thinker "
                    "{status|set|login|probe|check|migrate} ...")
    try:
        if a.cmd in (None, "status"):
            return cmd_status(a)
        if a.cmd == "set":
            return cmd_set(a)
        if a.cmd == "login":
            return cmd_login(a)
        if a.cmd == "probe":
            return cmd_probe(a)
        if a.cmd == "check":
            r = weekly_check(float(a.timeout))
            return _env("ok", "thinker_check", r, 0, "\n".join([r.get("line", "")] + r.get("alerts", [])))
        if a.cmd == "migrate":
            r = migrate_claude()
            rc = {"ok": 0, "refused": 3}.get(r["status"], 1)
            return _env(r["status"], "thinker_migrate_" + r["code"], r, rc,
                        r.get("message") or "claude.env: %s (%s)" % (r.get("file"), r.get("fingerprint", "")))
    except CatalogError as e:
        return _env("error", "thinker_catalog_refused", {"message": str(e)}, 1, str(e))
    return _env("error", "thinker_usage", {}, 2, "unknown thinker verb")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_probe"]:
        a = build_parser().parse_args(argv)
        return cmd_probe_internal(a)
    if argv[:1] == ["_question"]:
        return cmd_question(build_parser().parse_args(argv))
    if argv[:1] == ["_check_command"]:
        return cmd_check_command(build_parser().parse_args(argv))
    env = run(argv)
    rc = env.pop("_exit_code", 0)
    human = env.pop("_human", "")
    if "--json" in argv:
        print(json.dumps(env, indent=2, default=str))
    else:
        print(human)
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())

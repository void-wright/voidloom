#!/usr/bin/env python3
"""SessionStart hook — the GUARANTEE layer (Council #4819).

Wired into Claude Code's settings.json hooks.SessionStart. It emits a SINGLE
JSON envelope with a DUAL output:
  * hookSpecificOutput.additionalContext -> the PREAMBLE, injected into the
    session's MODEL context, GUARANTEEING the session knows:
      1. WHO it is (the operator's own declared persona, or none)
      2. THAT a Voidloom memory brain exists + HOW to use it (recall/store)
      3. A few priming memories so context starts warm
  * systemMessage -> the Voidloom BANNER, which Claude Code prints to the
    USER's TERMINAL. (Root cause #6022: the old plain-stdout banner reached
    only the model context and NEVER the terminal; the HARD gate #168 confirmed
    systemMessage carries ANSI truecolor + multi-line + Unicode to the screen.)

This is the "force-load your context so you become you every time" mechanism —
stronger than relying on the model to notice CLAUDE.md.

Portable: resolves persona + brain path install-anywhere (env > relative-self).
Fail-graceful: if the brain is unreachable, emits the preamble only (no/empty
systemMessage), exits 0. NEVER blocks session start.

Persona resolution: none here. voidloom_preamble reads the operator's own
declaration (persona.json in the brain home); an undeclared brain gets the
neutral Voidloom MEMORY header. The package ships no persona.

Heartbeat (R3 v3): every fire APPENDS exactly one record and never rewrites a
prior one, to $VOIDLOOM_HEARTBEAT_DIR|~/.voidloom/heartbeat/<platform>.jsonl — a
user-owned path, never a vendor dir — THROUGH THE SAME IDENTITY-GATED WRITER as
install and uninstall (voidloom_hooks_status.guarded_append_bytes: a regular file
with exactly one name whose parent chain resolves to the named path, or a
refusal; and guarded_mkdir_chain for the directory: created only under a chain
that resolves to the named path, nothing created on refusal), and NEVER fails
session start over it.  The platform is DERIVED from the stdin payload first,
then the env (voidloom_heartbeat.derive_platform).
"""
import os
import sys


def _reexec_under_recorded_interpreter() -> None:
    """B2 class (iteration 2): run under the interpreter the install RECORDED, not whichever python3 the platform found.

    The platform runs this hook as `python3 <brain>/hooks/session_start_inject.py`, and that string is PERMANENTLY
    STABLE (voidloom_hooks_status.hook_command: Codex pins trust to its hash, so changing it on an existing install
    would silently untrust the hook). So the choice is made HERE, before any other import: when <brain>/INSTALL_RECORD
    names an interpreter (absolute, executable) that is not the one running, and it starts, the hook re-executes
    itself under it -- same argv, same stdin (nothing has been read). On an install whose recorded interpreter IS the
    PATH python3 (the author's ambient host) nothing happens. Any doubt -- no record, a missing or broken
    interpreter, a re-exec already done -- and the hook runs on as before: it NEVER fails session start over this.
    """
    if os.environ.get("VOIDLOOM_HOOK_REEXEC") == "1":
        return
    try:
        brain = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        rec = os.path.join(brain, "INSTALL_RECORD")
        if not os.path.isfile(rec):
            return
        interp = ""
        with open(rec, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("interpreter="):
                    interp = line[len("interpreter="):].strip()
        if not (os.path.isabs(interp) and os.path.isfile(interp) and os.access(interp, os.X_OK)):
            return
        def _env_root(exe: str) -> str:
            # A venv's python is usually a SYMLINK to its base interpreter, so realpath alone calls them the same
            # (measured: a fresh venv's bin/python3 -> /usr/bin/python3.14, and the hook never re-executed). What
            # differs is the environment: <venv>/pyvenv.cfg. So the venv root when there is one, else the binary.
            root = os.path.dirname(os.path.dirname(os.path.abspath(exe)))
            if os.path.isfile(os.path.join(root, "pyvenv.cfg")):
                return "venv:" + os.path.realpath(root)
            return "bin:" + os.path.realpath(exe)
        if _env_root(interp) == _env_root(sys.executable or ""):
            return
        import subprocess
        if subprocess.run([interp, "-I", "-c", "pass"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=15).returncode != 0:
            return
        os.environ["VOIDLOOM_HOOK_REEXEC"] = "1"
        os.execv(interp, [interp, os.path.realpath(__file__)] + sys.argv[1:])
    except Exception:
        return


_reexec_under_recorded_interpreter()

import json
import re
import shutil
import subprocess
import hashlib
import datetime as _dt
import select
from pathlib import Path
from typing import Optional

# --- Resolve brain home (env > relative-self) ---
HOME = os.environ.get("WIZARD_LIBRARY_HOME") or str(Path(__file__).resolve().parent.parent)
# The engine + organ modules moved into organs/ on 2026-09-07. HOME stays the
# tree root (the DB, the manifest, the CLI shim all live there); ORGANS is the
# import path. Probed, so a flat legacy tree still resolves.
ORGANS = HOME if not os.path.isdir(os.path.join(HOME, "organs")) else os.path.join(HOME, "organs")
DB = os.environ.get("WIZARD_LIBRARY_DB") or str(Path(HOME) / "claude_memory.db")
CLI = str(Path(ORGANS) / "claude_memory.py")

# --- Persona ---
# THE HOOK NAMES NOBODY.  It used to carry its own copy of the persona selector
# AND its own copy of the persona text, so the package shipped the author's
# identity twice over and injected it into every session on every host that was
# not the author's Windows twin.  Both copies are gone: voidloom_preamble owns
# persona resolution, it reads the OPERATOR'S OWN declaration (persona.json in
# the brain home), and an undeclared brain renders the neutral Voidloom MEMORY
# header — which is what docs/ONBOARDING_GUIDE.md promises the reader.
owner = None  # resolved downstream from the declaration; never guessed here

def _env_truthy(name: str) -> bool:
    """True iff env var ``name`` is set to a truthy value (1/true/yes/on).

    Mirrors the engine's ``VOIDLOOM_ENABLE_CROSS_TIER`` gate (G2/C1 contract):
    default (unset) is OFF. Keeps the hook's dual-brain teaching in lockstep
    with whether the engine will actually attempt a cross-tier read.
    """
    v = os.environ.get(name, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _twin_db_path(rtier: str) -> str:
    """Resolve the off-machine twin brain DB path WITHOUT a personal literal.

    Env-first, mirroring cross_tier_bridges._twin_brain_default / the C1 contract
    override key ``MEMORY_TIER_EDDIE_WINDOWS_PATH``:
      1. the tier override env var (e.g. MEMORY_TIER_EDDIE_WINDOWS_PATH) — wins;
      2. else a WSL-visible Windows home derived from WIN_HOME / USERPROFILE, or
         reconstructed from WIN_USER / USERNAME;
      3. else '' (no derivable path) — the block then teaches the override key
         rather than a fabricated path. No username is baked into source.
    """
    override_key = f"MEMORY_TIER_{rtier.upper().replace('-', '_')}_PATH"
    explicit = os.environ.get(override_key, "").strip()
    if explicit:
        return explicit
    if rtier == "milamber-wsl":
        return str(Path.home() / ".claude" / "memory" / "claude_memory.db")
    # eddie-windows (or any off-machine twin): derive a WSL-visible Windows home.
    win_home = os.environ.get("WIN_HOME") or os.environ.get("USERPROFILE")
    if win_home:
        wh = win_home.replace("\\", "/")
        if len(wh) >= 2 and wh[1] == ":":
            wh = "/mnt/" + wh[0].lower() + wh[2:]
        return str(Path(wh) / ".claude" / "memory" / "claude_memory.db")
    win_user = os.environ.get("WIN_USER") or os.environ.get("USERNAME")
    if win_user:
        return f"/mnt/c/Users/{win_user}/.claude/memory/claude_memory.db"
    return ""


# PRIMING_QUERIES was here: a per-persona recall-seed table, keyed on the two
# names this package used to ship, READ BY NOTHING.  The preamble's own selector
# does the priming (trust-filtered, most-recent-by-importance), so the table was
# a declaration on no path that still shipped both names into every clone.
# Deleted rather than parameterised: a knob nobody turns is not a feature.


def emit_envelope(preamble: str, banner: Optional[str] = None) -> None:
    """The ONE hardened writer for the SessionStart hook.

    Emits a SINGLE JSON object on stdout with a DUAL output (Council #4819,
    visibility fix #6022):
      * hookSpecificOutput.additionalContext  -> the persona/brain PREAMBLE,
        injected into the MODEL's context (so the session becomes itself).
      * systemMessage                          -> the Voidloom BANNER, which
        Claude Code prints to the USER's TERMINAL (the channel the HARD gate
        #168 confirmed carries ANSI truecolor + multi-line + Unicode). The old
        plain-stdout banner reached only the model context, never the terminal.

    The banner is included ONLY when it is a non-empty string; if banner
    composition failed upstream (banner is None/empty) the envelope still
    carries the preamble and simply omits systemMessage — session start must
    NEVER break. This writer itself is hardened: on ANY serialization error it
    falls back to a minimal valid envelope with just the preamble.
    """
    payload: dict = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": preamble,
        }
    }
    if isinstance(banner, str) and banner.strip():
        payload["systemMessage"] = banner
    try:
        sys.stdout.write(json.dumps(payload) + "\n")
    except Exception:
        # Last-ditch: never let a serialization edge break session start.
        try:
            sys.stdout.write(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": preamble,
                }
            }) + "\n")
        except Exception:
            pass


def _banner_color_mode() -> str:
    """R16 — THE COLOUR FIX, AT THE CALL SITE.

    `void_render.detect_mode()` returns 'none' when stdout is not a TTY, and a
    SessionStart hook's stdout IS a pipe. THAT RETURN IS CORRECT AND IS NOT
    WEAKENED HERE: detect_mode answers "what is my stdout?", and the honest
    answer is a pipe. The bug was never detect_mode -- it was that
    `build_banner()` has always accepted `force_mode` and NO CALLER HAS EVER
    PASSED IT (measured 2026-09-02: force_mode="truecolor" yields 67 truecolor
    SGR sequences against 0 today).

    systemMessage is the ONE channel whose contract guarantees the escape
    sequences reach Drew's terminal -- ANSI + multiline + Unicode survival was
    hard-gated and confirmed (#168, #6162). We cannot DETECT the user's terminal
    from inside a non-TTY process, so on this channel we CHOOSE, which is exactly
    what force_mode exists for. VOIDLOOM_COLOR overrides for a terminal that
    cannot take it.
    """
    # CRUCIBLE-3 U4: this function CHOSE truecolor and, in choosing, ignored the
    # user's explicit opt-out. Measured at 93b295a: NO_COLOR=1 -> "truecolor",
    # VOIDLOOM_COLOR=off -> "truecolor" (a spelling void_render DOES accept);
    # only the literal "none" worked -- on the one surface that fires at EVERY
    # session start, and for a reader who may be using a screen reader.
    #
    # CHOOSING remains correct for this channel: we cannot detect the user's
    # terminal from inside a non-TTY process, which is what force_mode is for.
    # What was wrong is that an EXPLICIT instruction was outranked by a default.
    # The vocabulary is now read from void_render, which owns it, so the two
    # surfaces cannot drift apart again.
    #
    # TERM is deliberately NOT consulted: TERM here describes the environment the
    # HOOK runs in, not the terminal the client will later render this text into,
    # so honouring it would drop colour for everyone whose launcher leaves it unset.
    try:
        from organs.void_render import explicit_color_override
        forced = explicit_color_override()
    except Exception:
        try:
            from void_render import explicit_color_override   # flat layouts
            forced = explicit_color_override()
        except Exception:
            # NEVER raise into session start. Fall back to reading NO_COLOR
            # directly -- the one signal we must not lose if the import fails.
            forced = "none" if os.environ.get("NO_COLOR") is not None else None
    if forced is not None:
        return forced
    mode = (os.environ.get("VOIDLOOM_COLOR") or "truecolor").strip().lower()
    return mode if mode in ("truecolor", "ansi256", "mono", "none") else "truecolor"


# ---------------------------------------------------------------------------
# THE HEARTBEAT (R3 as amended, twice) — the sensor doctor joins against the
# platform's own session records.  APPEND-ONLY, one line per fire, O_APPEND, in
# OUR file under a user-owned directory.  It NEVER raises into session start:
# every failure is a returned reason, printed to stderr, and the preamble is
# emitted exactly as before.  (The first design overwrote one file and read
# "age 0.7 min -> HEALTHY" for a hook VAEL had already killed; the join over
# session_ids is what tells "fired once" from "fires every session".)
# ---------------------------------------------------------------------------
# The preamble budget applied ONLY when the platform is Codex.  Codex spills
# additionalContext above the hook's additionalContextLimit (installed at 6000;
# the vendor default is 2,500).  The frozen marker line is the FIRST line, but a
# spill that replaced the whole payload with a pointer would remove it and the
# activation notice would fire FALSELY every session for a healthy hook.  Named
# here, reported by doctor, and below the installed limit by construction.
CODEX_PREAMBLE_BUDGET = 4000
STDIN_WAIT_SECONDS = 2.0


def _read_stdin_payload(timeout: float = STDIN_WAIT_SECONDS) -> dict:
    """The platform's JSON payload on stdin, or {} — WITHOUT EVER BLOCKING.

    A TTY, a pipe nobody closes, or a FIFO planted at fd 0 must not hang session
    start (the class that hung the uninstaller for 20 s at three sites).  The read
    waits at most ``timeout`` seconds in total, via select on the raw fd.
    """
    try:
        if sys.stdin is None or sys.stdin.closed:
            return {}
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            return {}
    except Exception:
        return {}
    buf = b""
    import time as _t
    deadline = _t.monotonic() + max(0.0, timeout)
    try:
        while True:
            remaining = deadline - _t.monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 4 * 1024 * 1024:
                break
    except Exception:
        return {}
    if not buf.strip():
        return {}
    try:
        obj = json.loads(buf.decode("utf-8", errors="replace"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _write_heartbeat(payload: dict, preamble_text: str, doctrine_sha: Optional[str] = None,
                     source_override: Optional[str] = None) -> dict:
    """Append one heartbeat line.  NEVER raises; returns what happened."""
    try:
        sys.path.insert(0, ORGANS)
        import voidloom_heartbeat as _hb
    except Exception as e:
        return {"written": False, "reason": f"heartbeat module unavailable: "
                                            f"{type(e).__name__}: {e}"}
    try:
        platform, derivation = _hb.derive_platform(payload, dict(os.environ))
        sha = hashlib.sha256(preamble_text.encode("utf-8")).hexdigest() if preamble_text else None
        sid = payload.get("session_id") if isinstance(payload, dict) else None
        src_ = source_override or (payload.get("source") if isinstance(payload, dict) else None)
        res = _hb.append_heartbeat(platform, session_id=(str(sid) if sid else None),
                                   preamble_sha=sha, derivation=derivation,
                                   source=(str(src_) if src_ else None),
                                   hook_path=os.path.abspath(__file__),
                                   doctrine_sha=doctrine_sha)
        res["platform"] = platform
        res["derivation"] = derivation
        return res
    except Exception as e:  # the contract: a sensor may never break the thing it senses
        return {"written": False, "reason": f"{type(e).__name__}: {e}"}


def _platform_budget(platform: str):
    """The preamble budget for this platform, or None (unbudgeted)."""
    return CODEX_PREAMBLE_BUDGET if platform == "codex" else None


def _reranker_banner_suffix() -> str:
    """Handoff round 1 (L2): ONE line under the banner stating the reranker's state, why, and the accelerator's proof
    label -- where the owner looks. Stdlib only (the resolver's planned answer; no torch, no model: the <2 s budget
    holds), and the recall path re-checks live. Any failure is said on stderr and adds nothing: never breaks start."""
    try:
        import hybrid_retrieval as _hr
        rec = os.path.join(HOME, "INSTALL_RECORD")
        st = _hr.reranker_status(install_record=rec if os.path.isfile(rec) else None, confirm=False)
        return "\n" + _hr.reranker_banner_line(st)
    except Exception as e:  # noqa: BLE001
        print(f"[session_start_inject] reranker line degraded: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


def main():
    # THE HOOK NO LONGER BUILDS TEXT. 251 lines of construction lived here and
    # were the reason a second consumer could not be added without a second text
    # builder. `organs/voidloom_preamble.py` is now the SINGLE emitter, so
    # `voidloom preamble`.text and this hook's additionalContext are the same
    # bytes BY CONSTRUCTION rather than by coincidence -- the only version of
    # that claim that cannot drift. The banner half (systemMessage) and
    # emit_envelope() below are deliberately UNTOUCHED.
    sys.path.insert(0, ORGANS)
    import voidloom_preamble as _vp
    # The platform's payload (session_id, transcript_path, source, ...) arrives
    # on stdin on both Claude Code and Codex.  Read with a bounded wait — never
    # a blocking read — and derive the platform from it (or the env), never type it.
    payload = _read_stdin_payload()
    # ADAPTER OFF (W8, Council #12): the canary's CONTROL leg runs with
    # VOIDLOOM_ADAPTER=off in a clean directory.  Until now the hook honoured no
    # such switch, so on a platform whose user-level hook is trusted the control
    # was injected anyway and the harness could not build an honest null arm
    # without a bypass flag.  Off means OFF: nothing emitted, no heartbeat (a
    # fire that injected nothing is not a fire), exit 0.  The arrival line is
    # then absent, so the activation notice reports the absence — the switch
    # cannot silently mask a dead hook.
    if (os.environ.get("VOIDLOOM_ADAPTER") or "").strip().lower() == "off":
        # The heartbeat still records that the hook RAN and was switched off
        # (source=adapter_off, no preamble sha): the doctor join then reports
        # the session as SUPPRESSED rather than as a miss, so a canary's control
        # legs do not read as a dead hook on the operator's own machine.
        _write_heartbeat(payload, "", doctrine_sha=None, source_override="adapter_off")
        print("{}")
        return
    _platform = "unknown"
    try:
        import voidloom_heartbeat as _hb0
        _platform, _ = _hb0.derive_platform(payload, dict(os.environ))
    except Exception:
        _platform = "unknown"
    # THE ARRIVAL LINE (Council #12): the hook — and only the hook — asks the
    # emitter for the one line a static file cannot forge: platform, the
    # platform's own session id, a timestamp, and the doctrine sha.  The
    # activation notice keys on it.  `voidloom preamble` never passes `arrival`,
    # so the file tier cannot carry it by construction.
    _sid = payload.get("session_id") if isinstance(payload, dict) else None
    _arrival = {"platform": _platform, "session_id": (str(_sid) if _sid else None),
                "utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    # The canary nonce rides the environment (the platform's exec inherits it
    # into the hook): a volatile, undroppable ~12-token segment present ONLY
    # during a canary run.  Without this the shipped hook could never earn a
    # G1 row — the proof had to come from a harness that was not the product.
    _nonce = (os.environ.get("VOIDLOOM_CANARY_NONCE") or "").strip() or None
    _pre = _vp.build_preamble(db=DB, home=HOME, organs=ORGANS, owner=owner,
                              budget=_platform_budget(_platform), arrival=_arrival,
                              canary_nonce=_nonce)
    lines = [_pre.text]
    _doctrine_sha = _pre.prefix_sha256
    # THE PERSONA FORGE OFFER rides inside _pre.text: while `voidloom persona
    # status` is absent or declared-missing-skill the emitter adds a short,
    # NEUTRAL `persona_forge` body segment (it names nobody; silent when forged
    # or declined).  It is built there, not appended here, so this hook and
    # `voidloom preamble` stay the same bytes by construction.

    # --- VOIDLOOM banner (agnostic public-system identity) --------------
    # The banner is now the systemMessage half of the dual output: it goes to
    # the USER's TERMINAL, NOT into the model context with the preamble above.
    # So it is built into its OWN variable (not appended to the preamble lines).
    # CHEAP SOURCES ONLY (pre-cognitive cache JSON + two covered SQL reads)
    # -- it must NEVER load the embedding model (keeps the <2s budget).
    # Fail-graceful: any error here leaves banner=None; the envelope still
    # emits the preamble, so session start is unaffected (it NEVER breaks).
    banner: Optional[str] = None
    try:
        sys.path.insert(0, ORGANS)
        import voidloom_banner
        composed = voidloom_banner.build_banner(db=DB, force_mode=_banner_color_mode())
        if composed:
            banner = composed + _reranker_banner_suffix()
    except Exception as e:
        # R17 AS A CLASS, not as an instance. build_banner() now calls
        # attention_signal() -> unified_open_count(), so this handler wraps a
        # COUNT path -- R17's own subject. The sibling handler in
        # hooks/voidloom_notify.py was fixed to print its reason and this one was
        # not: same defect, one instance repaired. Consequence if left: a missing
        # ledger import or an unapplied migration at landing time shows NO banner
        # and NOTHING says why.
        #
        # Session start still NEVER breaks -- banner stays None and the preamble
        # is emitted exactly as before. What changes is that the reason no longer
        # vanishes. stderr, not stdout: stdout carries the hook's JSON envelope.
        print(f"[session_start_inject] banner degraded: {type(e).__name__}: {e}",
              file=sys.stderr)
        banner = None  # no banner -> preamble still emitted, session start unaffected
    # --- end VOIDLOOM banner -------------------------------------------

    # --- heartbeat (R3) — BEFORE the emit so a closed stdout cannot skip it, and
    # fail-graceful so nothing here can skip the emit either. ---------------
    preamble_text = "\n".join(lines)
    hb = _write_heartbeat(payload, preamble_text, doctrine_sha=_doctrine_sha)
    if not hb.get("written"):
        # stderr, not stdout: stdout carries the hook's JSON envelope.  The reason
        # never vanishes (R17 as a class), and session start is unaffected.
        print(f"[session_start_inject] heartbeat NOT written: {hb.get('reason')}",
              file=sys.stderr)

    # Dual output: preamble -> additionalContext (model), banner -> systemMessage
    # (terminal). ONE hardened writer; a banner failure never breaks the hook.
    emit_envelope(preamble_text, banner)
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""token_doctor.py — cross-platform Claude OAuth token check for wizard-library crons.

The cron jobs call `claude -p` for LLM synthesis (dreaming, arcane manifest,
synthesis). That needs a valid CLAUDE_CODE_OAUTH_TOKEN — the long-lived (~1-year) token minted
by `claude setup-token`. If it is missing or expired, those crons FAIL SILENTLY. This doctor
DETECTS the token and, if missing/invalid, prints the EXACT platform-specific fix — because
the token CANNOT be minted autonomously (`claude setup-token` is interactive browser OAuth).

Council #2 (May 28 2026): the strategy is DETECT -> PROMPT -> BAKE, never assume autonomy.
Installers (install.sh / install.ps1) call this at the cron-setup step and refuse to register
crons that would silently fail. Users can run it anytime to self-diagnose.

Checks:
  presence  — is CLAUDE_CODE_OAUTH_TOKEN set in the environment, or in the standard env file?
  --ping    — optionally run a minimal `claude -p` to confirm the token actually authenticates.

Exit codes: 0 = OK (present, or valid if --ping), 3 = MISSING, 4 = INVALID (ping failed).

Install-anywhere portable: stdlib only, platform-detected hints, no hardcoded machine paths,
no Unix-only imports. Passes cross_platform_audit.py.
"""
import argparse
import os
import platform
import re
import sys
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
# Laid at <brain>/tools/, run by path (docs/ONBOARDING_GUIDE.md): organs/ is its sibling, and the
# documented invocation died here with ModuleNotFoundError (reference-host round, iteration 3, item 8).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "organs"))
import voidloom_env
# The crons get the token from the env file through systemd's EnvironmentFile=, so the file is read by the ONE
# reader of it, with systemd's rules (install/env_file.py, laid beside tools/ -- reference-host round, iteration 4,
# the #21871 class: a guard must parse a file the way its consumer does). Hard import, for voidloom_env's reason.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "install"))
import env_file

TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def _env_file() -> Path:
    """The env file the UNITS load: WIZARD_ENV_FILE wins, as it does for install.sh (which renders it into every
    unit's EnvironmentFile=) and for organs/voidloom_device.py; else the standard one. The doctor used to read -- and
    name in its fix -- the standard file even when WIZARD_ENV_FILE named another (reference-host iteration-4
    reflection, ADVISORY 9)."""
    override = os.environ.get("WIZARD_ENV_FILE", "").strip()
    return Path(override).expanduser() if override else voidloom_env.env_path("wizard-library-env")


def _platform_fix() -> str:
    s = platform.system()
    if s == "Windows":
        shell = "PowerShell"
        envloc = "your env file's line  set " + TOKEN_VAR + "=..."
    elif s == "Darwin":
        shell = "Terminal (zsh/bash)"
        envloc = str(_env_file()) + " (mode 0600)"
    else:
        shell = "your WSL/Linux terminal"
        envloc = str(_env_file()) + " (mode 0600)"
    if s == "Windows":      # native Windows is a later round's (install.ps1 carried): its words stay until then
        return (
            "  1. In {shell}, run:  claude setup-token\n"
            "  2. Complete the browser sign-in (requires a Claude subscription).\n"
            "  3. Put the printed token into your wizard-library env file:\n"
            "       {var}=<the-sk-ant-oat-token>\n"
            "     (here: {envloc})\n"
            "  NOTE: this token is ACCOUNT-LEVEL and long-lived (~1 year). You MAY reuse it across\n"
            "  your OWN machines, but NEVER ship it in a teammate handoff — teammates mint their own."
        ).format(shell=shell, var=TOKEN_VAR, envloc=envloc)
    # MC-R1-03 (Round 1 iteration 2): the ONE home for the overnight thinker's Claude token is ~/.voidloom/thinker/
    # claude.env, written by `voidloom thinker login claude` (M16's collapsed homes). This fix is printed exactly when
    # the token is MISSING -- the instruction an owner follows at the moment of breakage -- so it names that verb.
    return (
        "  1. In {shell}, run:  claude setup-token\n"
        "  2. Complete the browser sign-in (requires a Claude subscription).\n"
        "  3. Give it to the overnight thinker (asked twice, proven with one tool-free call, saved 0600 in\n"
        "     ~/.voidloom/thinker/claude.env):  voidloom thinker login claude\n"
        "     (the guide: docs/OVERNIGHT_THINKER_SETUP.md Part C)\n"
        "  NOTE: this token is ACCOUNT-LEVEL and long-lived (~1 year). You MAY reuse it across\n"
        "  your OWN machines, but NEVER ship it in a teammate handoff — teammates mint their own."
    ).format(shell=shell)


def _token_from_env_file():
    """The token as the CRONS will see it (when not in os.environ): the standard env file read the way systemd's
    EnvironmentFile= reads it -- the LAST valid `CLAUDE_CODE_OAUTH_TOKEN=` assignment, no inline comments stripped,
    an `export` line assigning nothing -- then wizard-library-env.cmd beside the package (a Windows cmd file).

    Measured on the iteration-3 tool (reference-host round, iteration 4): it took the FIRST line that merely
    MENTIONED the variable, so on a fresh install the managed block's own comment
    `# CLAUDE_CODE_OAUTH_TOKEN=...   # set for the nocturnal crons (add it OUTSIDE this block)` read as a
    63-character token and the doctor said PRESENT (rc 0) to an owner whose crons had none; an
    `export CLAUDE_CODE_OAUTH_TOKEN=...` line, which systemd drops, read as PRESENT too."""
    envf = _env_file()   # the file the units name (install.sh: WIZARD_ENV_FILE wins)
    try:
        if envf.is_file():
            res = env_file.read_file(str(envf))
            if res["error"]:
                return None, "%s (systemd's EnvironmentFile= rejects that WHOLE file: %s, line %d)" % (
                    envf, res["error"], res["error_line"])
            got = res["effective"].get(TOKEN_VAR)
            if got and got[0].strip():
                return got[0].strip(), str(envf)
    except OSError:
        pass
    p = Path(__file__).resolve().parent.parent / "wizard-library-env.cmd"
    try:
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if TOKEN_VAR in s and "=" in s and not s.lower().startswith(("rem", "::")):
                    val = s.split("=", 1)[1].strip().strip('"').strip()
                    if val:
                        return val, str(p)
    except Exception:
        pass
    return None, None


def _thinker():
    """organs/thinker.py -- the seam. Hard import for voidloom_env's reason: an install without it is incomplete."""
    import thinker  # organs/ is on sys.path (above)
    return thinker


def check(do_ping: bool = False) -> int:
    """The Claude check of the overnight thinker (model-choice unit, Round 1): the token the seam would use --
    ~/.voidloom/thinker/claude.env first, then the LEGACY homes (this process's environment, the env file the units
    load, the sidecar) -- and, with --ping, ONE tool-free call through the seam's own sealed probe that must return the exact answer to a small sum
    (`voidloom thinker probe`'s method): a fresh empty cwd, stdin closed, --safe-mode, the token alone."""
    th = _thinker()
    tok, src, fpath = None, "(none)", None
    try:
        cred = th.resolve_credential("claude")
    except Exception:  # noqa: BLE001 - a broken catalog is reported by `voidloom thinker status`
        cred = None
    if cred is not None and not cred.legacy:
        tok, src = cred.env.get(TOKEN_VAR), cred.source
    if not tok:
        tok = os.environ.get(TOKEN_VAR, "").strip() or None
        src = "environment" if tok else src
    if not tok:
        tok, fpath = _token_from_env_file()
        src = fpath or "(none)"
    if not tok and cred is not None:
        tok, src = cred.env.get(TOKEN_VAR), cred.source
    if not tok:
        print(f"[token-doctor] MISSING — {TOKEN_VAR} not in ~/.voidloom/thinker/claude.env, the environment or the env file.")
        if fpath and "rejects" in fpath:
            print(f"[token-doctor] {fpath}")
        print("[token-doctor] Organs that think with Claude (dreaming, manifest, self-eval) take their simple paths until fixed:")
        print(_platform_fix())
        print("[token-doctor] " + th.health_line())
        return 3

    masked = tok[:8] + "…(" + str(len(tok)) + " chars)"
    legacy = "" if src == str(th.thinker_home() / "claude.env") else " -- a LEGACY home; `voidloom thinker migrate` moves it into claude.env"
    print(f"[token-doctor] PRESENT — {TOKEN_VAR} found in {src} ({masked}){legacy}.")
    if not do_ping:
        print("[token-doctor] (presence only; pass --ping to verify it actually authenticates.)")
        print("[token-doctor] " + th.health_line())
        return 0

    cat = th.load_catalog()
    if not th.resolve_binary(cat["backends"]["claude"]):
        print("[token-doctor] WARN — `claude` CLI not on PATH; cannot ping. Ensure cron PATH includes it.")
        return 0
    res = th._prove_value("claude", cat, TOKEN_VAR, tok, 60.0)
    if res.get("passed"):
        print("[token-doctor] VALID — one tool-free test question answered through the thinker seam. ✓")
        print("[token-doctor] " + th.health_line())
        return 0
    err = str(res.get("error") or "no exact answer")
    m = re.search(r"exited (\S+): (.*)", err)
    rc, said = (m.group(1), m.group(2)) if m else ("?", err)
    if res.get("kind") == "timeout":
        print("[token-doctor] WARN — the test call timed out; could not confirm. Check network + token.")
        return 0
    verdict = "INVALID" if res.get("reason") == "auth" else "FAILED"
    print(f"[token-doctor] {verdict} -- claude -p exited {rc}: {said}")
    print(_platform_fix())
    return 4


def main():
    ap = argparse.ArgumentParser(
        description="Check the Claude OAuth token that wizard-library crons depend on.")
    ap.add_argument("--ping", action="store_true",
                    help="run a minimal `claude -p` to verify the token authenticates")
    args = ap.parse_args()
    sys.exit(check(do_ping=args.ping))


if __name__ == "__main__":
    main()

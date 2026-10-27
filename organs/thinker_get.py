#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE THINKER PROGRAM THAT IS NOT INSTALLED (Gemini-thinker Round 1, lane L-I; SYNTHESIS R4, KAEL-EM's screens).

The owner picks who thinks overnight; for a CLI backend that answer is a PROGRAM (claude, codex, kimi, agy). On a machine
without it the installer used to show the guide's Part, wait for Enter, and only then refuse with a generic cause --
nothing anywhere said how to get the program. This module is the one place that knows:

  * DETECT, before the guide's Part is shown: organs/thinker.py's own resolve_binary (PATH, then the catalog's
    binary.candidates -- a fresh shell, or this very process after an install, often lacks ~/.local/bin on PATH);
  * the vendor's OWN install one-liner, from the catalog's `get` field (thinkers.json: {unix, windows, doc_url,
    verified_at} -- a fact verified at execution, never prose that drifts), shown VERBATIM;
  * run it only on the owner's yes (the installer asks "Run it now? [y/N]" through THE ASK HELPER, or the agent passes
    the owner's yes as install.sh --install-thinker-cli): never sudo, never silent (the command is printed before it
    runs and its own output is shown), never a second install chained behind it (a missing Node.js STOPS with a link);
  * re-detect through binary.candidates and report the FULL path, which the installer hands to `thinker set --binary`;
  * the `cause:` line every refusal of a missing program carries (`  cause: not-installed`, then the command).

Verbs (`python3 organs/thinker_get.py ...`; the installer is the caller; every verb prints its result as ONE JSON line
LAST on stdout, human text before it on stderr):
    check BACKEND                 found? the command, what it needs, whether this OS is offered -- runs nothing
    refusals                      the letters this OS cannot take, with the catalog's reason (letter TAB reason lines)
    screen BACKEND                the offer the owner reads before "Run it now? [y/N]" (KAEL-EM screen 2/3)
    signin BACKEND --program P    the text shown before a backend's own sign-in starts (KAEL-EM screen 1), if any
    run BACKEND                   run the vendor's command (the caller has the owner's yes), then re-detect
rc: 0 found / installed and found, 2 refused (bad backend, no command for this OS, a sudo command), 3 not found."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thinker  # noqa: E402  (the seam: the catalog reader and resolve_binary, never a copy)

GET_FIELDS = ("unix", "windows", "doc_url", "verified_at")
INSTALL_TIMEOUT_S = 900          # a vendor installer downloads a program (agy is ~214 MB): generous, never unbounded
# Words a vendor command may never carry: the installer never escalates and never removes anything on the owner's behalf
FORBIDDEN_WORDS = ("sudo", "doas", "su", "pkexec", "runas", "rm")


def os_key(plat: Optional[str] = None) -> str:
    """The `get` key for this operating system: windows on win32, unix everywhere else (Linux, macOS)."""
    return "windows" if (plat or sys.platform) == "win32" else "unix"


def get_problems(bid: str, entry: Dict[str, Any]) -> List[str]:
    """Every problem with a backend's `get` field, as sentences ([] = valid). A CLI backend with a program name must
    carry one; a local backend (the owner's own command or server) has nothing to get."""
    names = (entry.get("binary") or {}).get("names") or []
    if entry.get("transport") != "cli" or not names:
        return []
    g = entry.get("get")
    if not isinstance(g, dict):
        return ["%s: no `get` field (the vendor's official install command; organs/thinker_get.py)" % bid]
    bad = []
    for f in GET_FIELDS:
        if not isinstance(g.get(f), str) or not g[f].strip():
            bad.append("%s: get.%s is missing or empty" % (bid, f))
    for k in ("unix", "windows"):
        words = str(g.get(k) or "").replace("|", " ").replace(";", " ").replace("&", " ").split()
        for w in FORBIDDEN_WORDS:
            if w in words:
                bad.append("%s: get.%s carries %r (the installer never escalates or removes on the owner's behalf)"
                           % (bid, k, w))
    if not str(g.get("doc_url", "")).startswith("https://"):
        bad.append("%s: get.doc_url must be an https:// address" % bid)
    if len(str(g.get("verified_at", ""))) < 10 or not str(g.get("verified_at", ""))[:4].isdigit():
        bad.append("%s: get.verified_at must be the date (YYYY-MM-DD) the command's URL was checked" % bid)
    for n in g.get("needs") or []:
        if not (isinstance(n, dict) and n.get("program") and n.get("name") and str(n.get("link", "")).startswith("https://")):
            bad.append("%s: get.needs entries are {program, name, link (https://)}" % bid)
    return bad


def platform_refusal(entry: Dict[str, Any]) -> str:
    """'' when this OS is offered for the backend, else the catalog's own reason (lane L-G's `platforms` field; the
    seam's reader when it has one, so the two can never disagree)."""
    f = getattr(thinker, "platform_refusal", None)
    if f is not None:
        return f(entry)
    plat = entry.get("platforms")
    if not isinstance(plat, dict):
        return ""
    v = plat.get("linux" if sys.platform.startswith("linux") else sys.platform)
    return "" if v == "supported" else (v or "this backend is not offered on %s" % sys.platform)


def _entry(cat: Dict[str, Any], bid: str) -> Dict[str, Any]:
    e = (cat.get("backends") or {}).get(bid)
    if not isinstance(e, dict):
        raise KeyError(bid)
    return e


def missing_needs(g: Dict[str, Any]) -> List[Dict[str, str]]:
    """The programs the vendor's command needs that this machine lacks (e.g. npm for Codex): the installer STOPS on
    these with the link -- it never chains a second install."""
    return [n for n in (g.get("needs") or []) if not shutil.which(n["program"])]


def check(cat: Dict[str, Any], bid: str) -> Dict[str, Any]:
    e = _entry(cat, bid)
    g = dict(e.get("get")) if isinstance(e.get("get"), dict) else {}
    # how the owner reads the program's name, its company and what it adds: the question block's words (the `get`
    # field keeps DESSA's Step 0 shape -- the command, its doc page, the date it was checked, what it needs)
    for k, v in (((cat.get("question") or {}).get("program_words") or {}).get(bid) or {}).items():
        g.setdefault(k, v)
    found = thinker.resolve_binary(e) if e.get("transport") == "cli" and (e.get("binary") or {}).get("names") else ""
    return {"backend": bid, "found": found, "command": (g or {}).get(os_key(), ""), "doc_url": (g or {}).get("doc_url", ""),
            "verified_at": (g or {}).get("verified_at", ""), "program": (g or {}).get("program", bid),
            "vendor": (g or {}).get("vendor", ""), "adds": (g or {}).get("adds", ""),
            "needs_missing": missing_needs(g or {}), "platform_refusal": platform_refusal(e),
            "problems": get_problems(bid, e), "names": (e.get("binary") or {}).get("names") or [],
            "candidates": (e.get("binary") or {}).get("candidates") or []}


def missing_cause(cat: Dict[str, Any], bid: str) -> str:
    """The lines every refusal of a missing program ends with: the cause the installer reads, then the command."""
    try:
        c = check(cat, bid)
    except KeyError:
        return "  cause: not-installed"
    if c["command"]:
        return ("  cause: not-installed\n  get it with the vendor's own installer (%s): %s\n  (or re-run install.sh at a "
                "terminal: it offers to run that for you, and runs it only on your yes)" % (c["doc_url"], c["command"]))
    return "  cause: not-installed"


def screen_lines(c: Dict[str, Any], words: str) -> List[str]:
    """KAEL-EM screens 2 and 3: name the service, show the vendor's command verbatim, say what it adds."""
    vendor = c["vendor"] or "the vendor"
    out = ["%s needs %s's %s program; it is not on this computer." % (words, vendor, c["program"])]
    if c["needs_missing"]:
        for n in c["needs_missing"]:
            out.append("Its installer needs %s, which is not installed: %s" % (n["name"], n["link"]))
        out.append("Install %s yourself first, then re-run; this installer never installs a second program for you."
                   % " and ".join(n["name"] for n in c["needs_missing"]))
        return out
    out.append("This would run %s's own installer from the internet:" % vendor)
    out.append("    %s" % c["command"])
    out.append("It adds %s. No password, no admin rights. (Checked %s: %s)" % (c["adds"] or "the program",
                                                                              c["verified_at"], c["doc_url"]))
    return out


def signin_lines(cat: Dict[str, Any], bid: str, program: str) -> List[str]:
    """KAEL-EM screen 1 (the owner's ruling, 2026-10-16: the Gemini sign-in runs through `voidloom thinker login`; this
    text is shown before it) -- the catalog's own words with the program, the brain's thinker home and the pinned model."""
    lines = ((cat.get("question") or {}).get("signin_screens") or {}).get(bid) or []
    e = _entry(cat, bid)
    model = (e.get("model") or {}).get("pin") or (e.get("model") or {}).get("default") or "the model the catalog names"
    home = str(thinker.thinker_home())
    h = os.path.expanduser("~")
    if home.startswith(h + os.sep):
        home = "~" + home[len(h):]
    return [l.replace("{program}", program or "?").replace("{thinker_home}", home).replace("{model}", model)
            for l in lines]


def run(cat: Dict[str, Any], bid: str, timeout_s: float = INSTALL_TIMEOUT_S) -> Dict[str, Any]:
    """Run the vendor's command (the caller holds the owner's yes), its output shown, then re-detect. Never sudo
    (refused in the catalog check), never a second install (a missing need refuses before anything runs)."""
    c = check(cat, bid)
    if c["found"]:
        return dict(c, ran=False, rc=0, note="already installed")
    if c["problems"] or not c["command"]:
        return dict(c, ran=False, rc=2, note="no runnable command: %s" % ("; ".join(c["problems"]) or "none for this OS"))
    if c["platform_refusal"]:
        return dict(c, ran=False, rc=2, note=c["platform_refusal"])
    if c["needs_missing"]:
        return dict(c, ran=False, rc=3, note="needs %s: %s" % (", ".join(n["name"] for n in c["needs_missing"]),
                                                             ", ".join(n["link"] for n in c["needs_missing"])))
    print("[install] running %s's installer (your yes): %s" % (c["vendor"] or bid, c["command"]), file=sys.stderr)
    shell = ["powershell", "-NoProfile", "-Command", c["command"]] if os_key() == "windows" else ["/bin/bash", "-c", c["command"]]
    try:
        # its stdout joins stderr, so the caller's last stdout line stays the one JSON result; stdin is closed (the
        # command is answered by the owner's yes, never by a keystroke this process forwards)
        p = subprocess.run(shell, stdin=subprocess.DEVNULL, stdout=sys.stderr.fileno(), stderr=sys.stderr.fileno(),
                           timeout=timeout_s)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    except OSError as e:
        print("[install] the installer could not be started: %s" % e, file=sys.stderr)
        rc = 127
    after = check(cat, bid)
    if after["found"]:
        return dict(after, ran=True, rc=0, installer_rc=rc, note="installed; found at %s" % after["found"])
    return dict(after, ran=True, rc=3, installer_rc=rc,
                note="the installer ended (rc %d) and %s is still not found (looked on PATH and in %s)"
                     % (rc, " / ".join(after["names"]) or bid, ", ".join(after["candidates"]) or "-"))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="thinker_get.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for v in ("check", "screen", "run"):
        s = sub.add_parser(v)
        s.add_argument("backend")
        s.add_argument("--words", default="")
    s = sub.add_parser("signin")
    s.add_argument("backend")
    s.add_argument("--program", default="")
    sub.add_parser("refusals")
    a = ap.parse_args(argv)
    try:
        cat = thinker.load_catalog()
    except thinker.CatalogError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    if a.verb == "refusals":
        for letter, _w, answer in cat["question"]["options"]:
            e = (cat["backends"] or {}).get(answer)
            r = platform_refusal(e) if isinstance(e, dict) else ""
            if r:
                print("%s\t%s" % (letter, r.replace("\t", " ").replace("\n", " ")))
        return 0
    try:
        _entry(cat, a.backend)
    except KeyError:
        print(json.dumps({"error": "unknown backend %r" % a.backend, "backend": a.backend}))
        return 2
    if a.verb == "signin":
        for l in signin_lines(cat, a.backend, a.program):
            print(l)
        return 0
    if a.verb == "check":
        c = check(cat, a.backend)
        print(json.dumps(c))
        return 0 if c["found"] else 3
    if a.verb == "screen":
        c = check(cat, a.backend)
        for l in screen_lines(c, a.words or a.backend):
            print(l)
        return 0 if not c["needs_missing"] else 3
    r = run(cat, a.backend)
    print(json.dumps(r))
    return int(r["rc"])


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""pii_gate.py -- REFUSE to package a tree that carries human identity.

WHY THIS EXISTS
---------------
`gitleaks detect` reports "no leaks found" on this tree.  It is correct, and it
is irrelevant: gitleaks is a SECRETS scanner.  It hunts things shaped like
credentials -- high-entropy strings, key prefixes, token formats.  A colleague's
work email is none of those.  A customer's name is none of those.  They carry
no entropy, match no vendor pattern, and are structurally invisible to it.

So the release plan "gitleaks clean, then push" WOULD HAVE PASSED THIS TREE with
a named individual bound to a named client, and a real work email, in it.
#7976 named the second one already and it is still open.

A secrets scanner and a PII scanner answer different questions.  This tree had
two answers to the first question and none to the second.  This is the second.

THE CLASS, NOT THE TWO INSTANCES
--------------------------------
The two known sites are instances.  The class is: any tracked artifact carrying
an identifier that names a HUMAN.  Three mechanically-decidable shapes:

  E  email address        -- an RFC-shaped address at a real-looking TLD
  H  account handle       -- a personal account name in an account context
  N  person-bound-to-org  -- a name-shaped token beside a relational marker
                             ("is X contact", "at X", "customer", "client")

MACHINE identity (GPU UUIDs, host paths, device counts) is DELIBERATELY NOT in
this gate's population.  That is a different criterion with a different owner
and a different instrument, and folding it in here would make two findings move
one number -- the exact collision that makes a round unable to say what it did.

ON THE ALLOWLIST
----------------
Every entry below is a DECISION, not a convenience.  A gate whose allowlist
grows to keep it quiet has stopped being a gate.  Each entry names why it is
not human identity.  Additions belong in review, not in a hurry.

EXIT CODES
    0  clean -- no human identity in the enumerated package
    1  findings, OR an EMPTY population -- REFUSED
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# ROOT is DERIVED from git, never hardcoded.  An earlier gate in this build
# hardcoded one operator's absolute brain path INSIDE the gate whose job was
# hardcoded assumptions.  Not repeating that.
def _root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here,
                         capture_output=True, text=True)
    top = out.stdout.strip()
    return Path(top) if top else here


# ---------------------------------------------------------------- E: emails
# Deliberately narrow on the TLD: matching every dotted suffix turns every
# "module.py" reference and every "user@host" shell example into a finding, and
# a gate that cries wolf gets switched off.
EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|ai|org|net|io|edu|gov|co|dev)\b")

# Addresses that are not a person's address.  Each is a decision.
EMAIL_ALLOW = {
    "noreply@anthropic.com",        # tooling attribution, not a mailbox
    "user@example.com",             # RFC 2606 reserved documentation domain
    "you@example.com",
    "name@example.com",
    "someone@example.com",
}
EMAIL_ALLOW_DOMAINS = {
    "example.com", "example.org", "example.net",   # RFC 2606, reserved forever
}

# --------------------------------------------------------------- H: handles
# An account handle only reads as PII when an account context is next to it.
# A bare word with digits is a variable name; "authenticated as <word>" is a
# person's login.
#
# TUNED against this tree: the first draft matched the substring "user" inside
# the IDENTIFIER `source_user`, and reported four hits in voidloom/_facade.py
# whose "handle" was the word `Optional`.  A gate that reports a type
# annotation as a person is a gate nobody will keep running, so the context
# keyword now requires a preceding word boundary.
#
# TUNED AGAIN: the unquoted assignment form captured the right-hand side of
# `owner = os.environ.get(...)` and reported `os.environ` as a person's login.
# The assignment shapes belong to HANDLE_DEFAULT_RES below, which requires a
# quoted literal; this pattern keeps only the PROSE forms, where a bare token
# after "authenticated as" really is somebody's account.
HANDLE_RE = re.compile(
    r"(?:authenticated as|logged in as|signed in as)"
    r"\s+([A-Za-z][A-Za-z0-9._-]{4,})",
    re.IGNORECASE)

# A personal handle also ships as a hardcoded DEFAULT: a parameter whose name
# means "a person", carrying a literal name as its fallback.  This shape is
# worse than it looks: a shipped module whose author parameter defaults to a
# real person's handle makes a stranger's every write claim authorship by
# somebody who has never touched their machine.  (This comment is phrased
# WITHOUT the literal on purpose -- an earlier draft carried the example
# verbatim and this gate flagged its own source, which was the correct
# answer.  A gate that republishes the PII it hunts has moved the leak.)
#
# TUNED TWICE.  The first form was a proximity match and reported 29 findings,
# most of them schema column names (`"source_user", "auto_edges"`), a systemctl
# flag (`--user", "show"`), and enum keys (`"user-preferences":
# "user_preference"`).  A gate at that false-positive rate gets switched off
# within a week, which is worse than no gate.  The value must now be BOUND to
# the key by an actual assignment, and must LOOK like a bare human name -- no
# underscore, no hyphen, not the key echoed back.
_PERSON_KEY = (r"source_user|username|user|owner|author|created_by"
               r"|graduated_by|stored_by|reviewed_by|approved_by")
_NAME_VAL = r"([A-Za-z][A-Za-z0-9.]{2,15})"
HANDLE_DEFAULT_RES = (
    # key = "val"   and   key: SomeType = "val"
    re.compile(r"(?<![A-Za-z0-9_])(?:" + _PERSON_KEY + r")\b"
               r"\s*(?::[^=\n]{0,24})?=\s*[\"']" + _NAME_VAL + r"[\"']"),
    # .get("key", "val")  -- the dict-default form
    re.compile(r"\.get\(\s*[\"'](?:" + _PERSON_KEY + r")[\"']\s*,\s*"
               r"[\"']" + _NAME_VAL + r"[\"']"),
    # "key": "val"  -- the dict-literal form
    re.compile(r"[\"'](?:" + _PERSON_KEY + r")[\"']\s*:\s*[\"']" + _NAME_VAL + r"[\"']"),
)
HANDLE_DEFAULT_ALLOW = {
    # Role and sentinel values -- nobody's identity.
    "unknown", "system", "default", "none", "null", "anonymous", "agent",
    "claude", "cron", "manual", "auto", "human", "test", "string", "str",
    "self", "admin", "service", "bot", "ci", "tester", "testuser", "someone",
    # Persona names.  MILAMBER and EDDIE are the brain's own published personas
    # -- product nouns, documented in CLAUDE.md, not the identity of a private
    # individual.  A DECISION, recorded here rather than assumed.
    "milamber", "eddie", "orin", "vael", "dessa",
}
HANDLE_ALLOW = {
    "root", "nobody", "postgres", "runner", "default", "system", "service",
    "anonymous", "guest", "admin",          # role accounts, not people
}

# ------------------------------------------------- N: person bound to an org
# A capitalised bigram is only PII when a relational marker binds it to an
# organisation.  Without the marker this fires on every proper noun in every
# docstring and the gate becomes noise.
#
# TUNED against this tree: a bare `contact`/`customer`/`client` marker matched
# the phrase "a synthetic contact" in PACKAGE_MANIFEST.md and reported the
# product name "Widget Analyzer" as a private individual.  The marker must
# BIND the name to an organisation, not merely co-occur with the noun.
PERSON_RE = re.compile(
    r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b"
    r"(?=[^\n]{0,40}?\b(?:is\s+\w+\s+(?:contact|customer|client|rep)"
    r"|(?:works|employed)\s+at|reports\s+to"
    r"|,\s*(?:contact|customer|client)\s+at"
    r"|(?:contact|customer|client)\s+at\s+[A-Z]))")

# Names that are not a private individual's identity.  Each is a decision.
PERSON_ALLOW = {
    # Public technical/organisational nouns that happen to be capitalised pairs.
    ("Google", "Drive"), ("Claude", "Code"),
    ("Wizard", "Library"),
    ("Hugging", "Face"),
    ("Narrative", "Memory"),
}

BINARY_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".pdf",
    ".zip", ".gz", ".tar", ".whl", ".so", ".bin", ".pyc", ".ico", ".woff",
    ".woff2", ".ttf", ".mp4", ".wav",
}


# --------------------------------------------------------------- POPULATION
# ROUND pi-2, sigma-1.  This function used to be `git ls-files` and nothing
# else, and install.sh:331 runs this gate against ${PKG_ROOT} -- AN UNPACKED
# TARBALL.  MEASURED: in a directory with no .git, `git ls-files` exits 128 and
# prints ZERO paths.  On a .git-less copy of the patched tree carrying a real
# work-shaped email, this gate printed its own PASS line --
#     "PASS - no human identity in the tracked tree"
# -- and exited 0.  Its own success message names the population it had
# silently lost: THE TRACKED TREE.  There is no tracked tree in a tarball.
#
# A gate must enumerate the ARTIFACT IT GUARDS, never the developer's
# conveniences.  So: git when git works, a WALK otherwise, and an EMPTY
# population is a REFUSAL -- because "I could not look" and "I looked and it
# was clean" are the same exit code only in a gate that has stopped being one.
#
# DELIBERATELY DUPLICATED from gpu_pin_gate.py rather than shared through an
# import: install.sh runs each gate as a standalone script against an unpacked
# tarball, and a gate that cannot run without a sibling module is a gate that
# silently does not run.
# ---------------------------------------------------------------------------
# THE EXCLUSION SET IS PART OF THE POPULATION  [F-1, conventions-024]
# ---------------------------------------------------------------------------
# The previous set was {".git", "__pycache__", ".pytest_cache", ".mypy_cache",
# "node_modules"} and it was WRONG, measured:
#
#   install.sh lays `voidloom/` and `skills/` with `cp -R`, and `cp -R` copies
#   those cache directories WHOLESALE into the destination. A pin or an address
#   planted at voidloom/__pycache__/evil.py was therefore SHIPPED while both
#   gates exited 0 and never named it -- not merely unscanned, but absent from
#   the enumerated population, so the "population: N files" line concealed the
#   miss. The control at pkg/evil.py made both gates exit 1. All five names
#   behaved identically. `voidloom/__pycache__` and `voidloom/tests/__pycache__`
#   carry 16 .pyc files on this tree TODAY: a live carrier, not a hypothetical.
#
# So the only name that may be excluded is one the installer PROVABLY does not
# lay, and `.git` is the only such name: no install.sh command copies the
# package root wholesale, and no `cp -R` source subtree contains a `.git`.
# That claim is not left as prose -- `verify_exclusions_against_installer()`
# below re-derives it from install.sh at run time and REFUSES if it fails.
NOT_LAID = {".git"}

#: Retained ONLY so the report can say what used to be skipped and now is not.
FORMERLY_SKIPPED = {"__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}

SKIP_DIRS = NOT_LAID  # name kept for the minimal pair harnesses that swap it


def verify_exclusions_against_installer(root: Path):
    """Re-derive the exclusion set from the LAYING MECHANISM. Returns findings.

    conventions-024 in executable form. For every `cp -R` in a packaged
    install.sh, the source subtree is laid wholesale, so no directory name that
    could occur inside it may sit in the exclusion set. If install.sh is not
    present (the gate is being run against a bare subtree) we return the
    UNMEASURABLE marker rather than a pass: a check that cannot see its subject
    must not report clean. [conventions-025]
    """
    inst = root / "install.sh"
    if not inst.is_file():
        return {"status": "unmeasurable", "reason": "no install.sh in tree",
                "recursive_copies": [], "violations": []}
    try:
        lines = inst.read_text(errors="ignore").splitlines()
    except OSError as e:
        return {"status": "unmeasurable", "reason": "install.sh unreadable: %s" % e,
                "recursive_copies": [], "violations": []}
    rec = [(i + 1, ln.strip()) for i, ln in enumerate(lines)
           if "cp -R" in ln or "cp -r" in ln]
    violations = []
    if rec:
        # Any name in the exclusion set is a name a `cp -R` subtree may contain.
        for name in sorted(SKIP_DIRS):
            if name != ".git":
                violations.append(
                    "%s is excluded by the gate but IS laid by cp -R" % name)
    return {"status": "ok", "reason": "", "recursive_copies": rec,
            "violations": violations}



def _unreachable_top_level(root):
    """Top-level directories the DELIVERY MECHANISM cannot reach, DERIVED and ASSERTED.

    ROUND cycle-2, M3.  Both release gates walk `os.walk(followlinks=True)` from the
    repo root -- correctly, because the population must come from the mechanism that
    LAYS the artifact and `git ls-files` returns nothing in a .git-less tarball.  But
    run from the brain root that walk covers 16,378 files and 3.5 GB of `archives/`
    and `ACE_Workspaces/`, and BOTH GATES TIMED OUT AT 900 SECONDS (measured).  A gate
    that cannot finish is a gate nobody wires, which is exactly how these two came to
    have zero callers (Crucible-3 D1-F1).

    The exclusion is NOT typed.  A directory is skipped only when BOTH hold, checked
    here, every run:
      (a) `install.sh` names it ZERO times -- it is on no laying path;
      (b) `git ls-files <dir>` is EMPTY -- nothing under it is even in the package.
    If either check fails the directory is NOT skipped and the reason is reported, so
    this can never quietly grow into the exclusion-list-as-hiding-place that
    `verify_exclusions_against_installer` exists to prevent.
    """
    import subprocess as _sp
    skipped, refused = {}, {}
    inst = root / "install.sh"
    text = inst.read_text(errors="replace") if inst.exists() else ""
    for d in sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != ".git"):
        named = text.count(d) if text else None
        try:
            tracked = _sp.run(["git", "ls-files", d], cwd=str(root),
                              capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            tracked = "?"
        if named == 0 and tracked == "":
            skipped[d] = "install.sh names it 0 times; git ls-files is empty"
        elif named == 0 and tracked == "?":
            refused[d] = "could not list tracked files; NOT skipped"
        elif named:
            refused[d] = f"install.sh names it {named} time(s); NOT skipped"
    return skipped, refused

def _walk_files(root: Path, keep=None):
    """Every file reachable the way the INSTALLER reaches it.

    ROUND pi-3, tau-1.  A gate's population must come from the MECHANISM THAT
    LAYS THE ARTIFACT, and `grep -c 'git ls-files' install.sh` is 0.  install.sh
    lays by shell wildcard (`for f in "${PKG_ROOT}"/*.py`, :356-357;
    `cp -f ${PKG_ROOT}/capability/*`) and by `cp -R` (:392).  MEASURED on this
    build, both halves:

      (a) a planted pin in an UNTRACKED file, with .git present:
          git ls-files omits it -> gate rc 0, file never named.
      (b) a planted pin behind a DIRECTORY SYMLINK, in a .git-less package:
          the shell wildcard `capability/*` traverses the link and `cp -f`
          LAYS the file; `Path.rglob("*")` does not descend it -> gate rc 0.

    MEASURED CORRECTION to the round brief: on GNU coreutils here, `cp -R`
    does NOT dereference a directory symlink -- neither as the source operand
    nor inside the tree; it copies the LINK (verified both ways).  The laying
    mechanism that reaches behind a directory symlink is the SHELL WILDCARD
    plus `cp -f`, not `cp -R`.  The conclusion is unchanged and the fix is the
    same; the attribution is not, and an unverified mechanism inherited as
    settled is this build's founding defect.

    os.walk(followlinks=True) with a (st_dev, st_ino) visited set: it follows
    what the shell follows, and it terminates on a symlink cycle.
    """
    files, followed, cycles = [], 0, 0
    excluded = []          # F-1: what we did NOT look at, reported not hidden
    seen = set()
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True):
        d = Path(dirpath)
        rel_parts = d.relative_to(root).parts if d != root else ()
        if SKIP_DIRS.intersection(rel_parts):
            excluded.append(d.relative_to(root).as_posix())
            dirnames[:] = []
            continue
        try:
            st = d.stat()
        except OSError:
            dirnames[:] = []
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen:
            cycles += 1
            dirnames[:] = []
            continue
        seen.add(key)
        _skip = set(SKIP_DIRS) | set(getattr(_walk_files, '_unreachable', ()) or ())
        dirnames[:] = [x for x in sorted(dirnames) if x not in _skip]
        for x in dirnames:
            if os.path.islink(os.path.join(dirpath, x)):
                followed += 1
        for name in sorted(filenames):
            p = d / name
            if p.is_file() and p.suffix.lower() not in BINARY_SUFFIXES:
                files.append(p)
    return sorted(set(files)), followed, cycles, sorted(excluded)


def _git_files(root: Path):
    """REPORTING ONLY -- never the authoritative population.

    Kept because "what git tracks" is worth SHOWING beside "what ships"; the
    round pi-2 defect was making it the population.  If a git view is ever
    wanted in the population, take the UNION, never the git subset.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                         capture_output=True)
    if out.returncode != 0:
        return None
    rels = [r for r in out.stdout.decode("utf-8", "replace").split("\0") if r]
    paths = [root / r for r in rels if (root / r).is_file()]
    return paths or None


def population(root: Path):
    """Return (files, source, info).  source is ALWAYS 'walk'.

    DELIBERATELY DUPLICATED in the sibling gate rather than shared through an
    import: install.sh runs each gate as a standalone script against an
    unpacked tarball, and a gate that cannot run without a sibling module is a
    gate that silently does not run.
    """
    walked, followed, cycles, excluded = _walk_files(root)
    gitv = _git_files(root)
    excl = verify_exclusions_against_installer(root)
    # AUTHORITATIVE: the walk.  This one line is tau-1's whole subject and it
    # carries its own minimal pair (pairs/s1g) that swaps it back.
    selected = walked
    return selected, "walk", {"symlinked_dirs_followed": followed,
                              "symlink_cycles_cut": cycles,
                              "git_view": (len(gitv) if gitv else None),
                              "walked": len(walked),
                              "excluded_dirs": excluded,
                              "exclusion_rule": sorted(SKIP_DIRS),
                              "formerly_skipped_now_scanned": sorted(FORMERLY_SKIPPED),
                              "exclusion_audit": excl}


def tracked(root: Path) -> list[Path]:
    """Back-compat shim.  Callers that only want the paths keep working."""
    return population(root)[0]


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Return (lineno, kind, evidence).  Evidence is REDACTED by construction:
    the gate reports the SHAPE and the location, never the value.  A gate whose
    output republishes the PII it found has moved the leak, not closed it."""
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in EMAIL_RE.finditer(line):
            addr = m.group(0)
            domain = addr.split("@")[-1].lower()
            if addr.lower() in EMAIL_ALLOW or domain in EMAIL_ALLOW_DOMAINS:
                continue
            local, _, dom = addr.partition("@")
            redacted = local[0] + "***@***." + dom.rsplit(".", 1)[-1]
            findings.append((i, "E:email", redacted))
        for m in HANDLE_RE.finditer(line):
            handle = m.group(1)
            if handle.lower() in HANDLE_ALLOW:
                continue
            findings.append((i, "H:handle", handle[0] + "***"))
        seen_default = set()
        for rx in HANDLE_DEFAULT_RES:
            for m in rx.finditer(line):
                handle = m.group(1)
                low = handle.lower()
                if low in HANDLE_DEFAULT_ALLOW or low in HANDLE_ALLOW:
                    continue
                if low in seen_default:
                    continue        # the three shapes overlap; report once
                seen_default.add(low)
                findings.append((i, "H:handle-default", handle[0] + "***"))
        for m in PERSON_RE.finditer(line):
            pair = (m.group(1), m.group(2))
            if pair in PERSON_ALLOW:
                continue
            findings.append((i, "N:person-bound-to-org",
                             pair[0][0] + "*** " + pair[1][0] + "***"))
    return findings


# ---------------------------------------------------------------------------
# GITIGNORED FILES ARE NOT PACKAGED ARTIFACTS -- CHECKED, NOT ASSERTED
#
# The walk is authoritative by design (sigma-1: a .git-less unpacked tarball must
# still be graded), so in a DEVELOPER's tree it also sees files that can never
# reach a package: pre-landing `*.bak` snapshots, host state, scratch. This gate
# was RED on exactly one such file -- voidloom_approvals.py.pre176seal.bak -- and
# a permanent red over something that cannot ship is how a gate teaches people to
# ignore it. I proved that on myself: I filed the sibling gate's whole output as
# "docs and tests" while two of its rows were live unit files.
#
# The claim is verified per file, in THIS root, at run time: `git check-ignore`
# must say so. No git (the tarball case) or not ignored -> the file is SCANNED.
# The delivery list is curated from `git ls-files` (#7976), so a file git ignores
# is not in it -- and `verify_exclusions_against_installer` above independently
# holds the other half: nothing excluded here may be laid by install.sh's cp -R.
def _gitignored_reason(root: Path, rel: str):
    try:
        out = subprocess.run(["git", "check-ignore", "-q", rel], cwd=str(root),
                             capture_output=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:          # 1 = not ignored, 128 = no git here
        return None
    return "gitignored -- cannot reach the curated packaging list (#7976)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Refuse to package human identity.")
    ap.add_argument("--dir", default=None, help="tree root (default: git toplevel)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = _root(args.dir)
    # cycle-2 M3: derive (never type) the top-level directories the delivery
    # mechanism cannot reach, so this gate FINISHES. Both gates timed out at 900s
    # walking 3.5 GB of archives/ and ACE_Workspaces/ from the brain root, and a
    # gate that cannot finish is a gate nobody wires -- which is how both came to
    # have zero callers. Every skip is justified per-run and reported.
    _skipped, _refused_skip = _unreachable_top_level(root)
    _walk_files._unreachable = _skipped
    files, source, popinfo = population(root)
    popinfo = dict(popinfo or {})
    popinfo["unreachable_skipped"] = _skipped
    popinfo["skip_refused"] = _refused_skip
    bad: dict[str, list[tuple[int, str, str]]] = {}
    exempted: list[tuple[str, str]] = []
    for p in files:
        try:
            hits = scan_text(p.read_text(errors="ignore"))
        except OSError:
            continue
        if not hits:
            continue
        rel = p.relative_to(root).as_posix()
        why = _gitignored_reason(root, rel)
        if why:
            exempted.append((rel, why))
            continue
        bad[rel] = hits

    total = sum(len(v) for v in bad.values())

    if args.json:
        import json
        print(json.dumps({"population_source": source, "population": len(files),
                          "empty_population": not files,
                          "excluded_dirs": popinfo["excluded_dirs"],
                          "exclusion_rule": popinfo["exclusion_rule"],
                          "formerly_skipped_now_scanned":
                              popinfo["formerly_skipped_now_scanned"],
                          "exclusion_audit": popinfo["exclusion_audit"],
                          "symlinked_dirs_followed": popinfo["symlinked_dirs_followed"],
                          "symlink_cycles_cut": popinfo["symlink_cycles_cut"],
                          "git_view": popinfo["git_view"],
                          "files": len(bad), "findings": total,
                          "exempted": [{"file": f, "reason": r} for f, r in exempted],
                          "exempted_count": len(exempted),
                          "detail": {k: [list(h) for h in v] for k, v in bad.items()}},
                         indent=2))
        return 1 if (total or not files) else 0

    print("=== PII GATE ===")
    print("    human identity in packaged artifacts")
    print("    (gitleaks is a SECRETS scanner and is structurally blind to this)")
    print("    population: " + str(len(files)) + " files, enumerated by " + source)
    print("    exclusion rule: " + ", ".join(popinfo["exclusion_rule"])
          + "  (" + str(len(popinfo["excluded_dirs"])) + " dirs excluded)")
    print("    now scanned (was skipped): "
          + ", ".join(popinfo["formerly_skipped_now_scanned"]))
    # What we did NOT count is REPORTED, never hidden.
    print("    gitignored, so not packaged: " + str(len(exempted)) + " files"
          + (" (" + ", ".join(f for f, _ in sorted(exempted)[:6])
             + (", ..." if len(exempted) > 6 else "") + ")" if exempted else ""))
    print("")
    if not files:
        print("  FAIL  the population is EMPTY -- this gate could not enumerate the")
        print("        artifact it guards, so it has PROVED NOTHING.")
        print("  VERDICT: REFUSED - unmeasurable, which is not the same as clean")
        return 1
    if not bad:
        print("  PASS - no human identity in the " + source + "-enumerated package")
        return 0
    for rel, hits in sorted(bad.items()):
        for ln, kind, ev in hits:
            print("  FAIL  " + rel + ":" + str(ln))
            print("        " + kind + "  -> " + ev + "   [value withheld by the gate]")
    print("\n  " + str(len(bad)) + " files, " + str(total) + " findings")
    print("  VERDICT: REFUSED - this tree must not be packaged")
    return 1


if __name__ == "__main__":
    sys.exit(main())

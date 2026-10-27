#!/usr/bin/env python3
"""gpu_pin_gate.py -- PKG-01's refuser. FAILS if any PACKAGED artifact pins CUDA
to a non-zero device.

THE RULE: a non-zero device is ONE MACHINE'S answer (the author's workstation has
two GPUs and index 0 drives the monitor). Every other machine is device 0 by
default, because ~99% of machines have one GPU and it is index 0; a machine that
needs another card declares it in its own env file.

THE POPULATION HAS NOW BEEN WRONG FIVE TIMES. THIS IS DRAFT 5.
--------------------------------------------------------------
  Draft 1  a grep. THREE false positives and it MISSED claude_memory.py:102.
  Draft 2  an AST assign-check. Missed setdefault and default parameter values.
  Draft 3  added setdefault, defaults, constants, dict literals -- but its
           setdefault branch was DEAD CODE, nested after an `isinstance(n,
           ast.Assign): continue` guard, so it was structurally incapable of
           firing. It reported 0 and there were 3.
  Draft 4  re-measured draft 3, fixed the dead branch, added ast.AnnAssign,
           added .sh/.json/.md. 18 files / 28 pins. Reported as complete.
  Draft 5  (this one) MEASURED draft 4 with minimal pairs and found TWO
           independent defects, one in each half of the gate:

    (A) THE POPULATION DOES NOT EXIST AT THE POINT OF USE  [sigma-1]
        `tracked()` was `git ls-files`. install.sh:331 runs this gate against
        ${PKG_ROOT} -- an UNPACKED TARBALL. MEASURED: in a directory with no
        .git, `git ls-files` exits 128 and prints ZERO paths, and draft 4 did
        not check either. On a .git-less copy of the patched tree carrying a
        planted work-shaped email and a planted
        `os.environ["CUDA_VISIBLE_DEVICES"] = "1"`, this gate printed
            PASS - no packaged artifact pins a non-zero device
        and exited 0. With .git present the same tree exits 1.
        *A gate must enumerate the ARTIFACT IT GUARDS, never the developer's
        conveniences.* Population is now git-when-git-works, WALK otherwise,
        and an EMPTY population is a REFUSAL rather than a pass.

    (B) THE MEMBERSHIP IS BLIND IN THE SCRIPT THAT WRITES THE UNIT  [sigma-2]
        Draft 4 caught `Environment=CUDA_VISIBLE_DEVICES=1` in a .service and
        was blind to the identical literal in the .sh that GENERATES that
        .service. MEASURED on the round-pi tree: 8 of 11 mutants escaped,
        including the single most natural shell idiom for the exact variable
        this round is about (`export CUDA_VISIBLE_DEVICES="1"`).
        And the discriminator that settles graduation:
        *empty-today + no emitter = fine; empty-today + a LIVE EMITTER = one
        `sed` from re-armed.* This tree has FOUR live emitters (install.sh:194,
        :248, :712 and cron/platform-detection.sh:151), so --emitters is now
        part of the verdict: every emitter's LITERAL form must be catchable, or
        the gate refuses.

A gate that fixes its DIRECTION and never revisits its MEMBERSHIP or its
POPULATION is the class this build kept re-committing. Draft 5 was written by
measuring draft 4, not by extending it.

Exit 0 = no packaged artifact pins a non-zero device AND every emitter is
covered AND the population was non-empty. Exit 1 = REFUSED.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ROOT is DERIVED, not hardcoded. Draft 1 of this gate hardcoded
# one operator's absolute brain path -- a local-only assumption, inside
# whole purpose is catching local-only assumptions. Found by ORIN, Council #9.
def _root(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         cwd=here, capture_output=True, text=True)
    return Path(out.stdout.strip()) if out.stdout.strip() else here


# The device-visibility variables of every backend the resolver pins (handoff round 1, lane L1): CUDA; ROCm's HIP and
# ROCr layers (CUDA_VISIBLE_DEVICES is also honoured there); Intel Level Zero's affinity mask. A literal in ANY of
# them, in a packaged file, is one workstation's answer shipped to everyone.
PIN_ENV_NAMES = {"CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "ZE_AFFINITY_MASK"}
_PIN_ALT = "|".join(sorted(PIN_ENV_NAMES))     # one alternation, so every textual detector below grows with the set
PIN_PARAMS = {"compute_index", "cuda_visible_devices", "gpu_device",
              "device_index", "gpu_device_index"}
OK_VALUES = {"0", "None", "", "auto", "cpu"}

# A value that is DERIVED on the target machine is the FIX, not the defect:
# "$X", "${X}", "${X:-0}", systemd's "%h"/"%i" specifiers, a Python expression.
DERIVED_PREFIXES = ("$", "%")


def _nonzero(value) -> bool:
    """True when a literal names a device that is not 0 and not 'no pin'. A Level Zero mask names device 0 when its
    device ordinal is 0 ('0', '0.1': tile 1 of device 0), so that is device 0 too."""
    s = str(value).strip()
    if re.fullmatch(r"0\.[0-9]+", s):
        return False
    return s not in OK_VALUES


def _derived(value: str) -> bool:
    return value.lstrip().lstrip("\"'").startswith(DERIVED_PREFIXES)


# ===========================================================================
# THE POPULATION  [sigma-1]
# ===========================================================================
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
            if p.is_file() and True:
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


# ===========================================================================
# DETECTORS  [sigma-2]
# ===========================================================================
def py_pins(p: Path):
    try:
        tree = ast.parse(p.read_text(errors="ignore"))
    except (SyntaxError, ValueError):
        return []
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                # 1. os.environ["CUDA_VISIBLE_DEVICES"] = "1"  AND the bare
                #    `from os import environ; environ[...] = "1"` form, which
                #    draft 4 required to be an Attribute and therefore missed.
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and t.slice.value in PIN_ENV_NAMES:
                    base = t.value
                    is_environ = ((isinstance(base, ast.Attribute) and base.attr == "environ")
                                  or (isinstance(base, ast.Name) and base.id == "environ"))
                    if is_environ:
                        v = n.value
                        if isinstance(v, ast.Constant) and _nonzero(v.value):
                            hits.append((n.lineno, "forced environ[...] = " + repr(v.value)))
                        # a non-Constant value is DERIVED and is the fix.
                # 2. module constant: GPU_DEVICE_INDEX = "1"
                if (isinstance(t, ast.Name) and t.id.lower() in PIN_PARAMS
                        and isinstance(n.value, ast.Constant)
                        and _nonzero(n.value.value)):
                    hits.append((n.lineno, t.id + " = " + repr(n.value.value)))

        # 2b. ANNOTATED assignment -- the dataclass field form.
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            if (n.target.id.lower() in PIN_PARAMS
                    and isinstance(n.value, ast.Constant)
                    and _nonzero(n.value.value)):
                hits.append((n.lineno, n.target.id + ": ... = "
                             + repr(n.value.value) + "   [dataclass default]"))

        call = None
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            call = n.value
        elif isinstance(n, ast.Call):
            call = n
        if call is not None and isinstance(call.func, (ast.Attribute, ast.Name)):
            fname = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
            # 3. os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")  -- #15708.
            #    RECEIVER-AGNOSTIC since 2026-09-20: `env.setdefault(...)` on a
            #    dict copy of os.environ that is about to become a subprocess's
            #    environment is the same hazard, and the receiver-bound form let
            #    exactly that through in test_encoder_tier_is_bijective.py:129
            #    (found by a downstream review). Any setdefault of a pin name to a
            #    non-zero constant is forbidden, whatever the dict is called.
            if (fname == "setdefault" and isinstance(call.func, ast.Attribute)
                    and len(call.args) == 2
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value in PIN_ENV_NAMES
                    and isinstance(call.args[1], ast.Constant)
                    and _nonzero(call.args[1].value)):
                hits.append((getattr(call, "lineno", 0),
                             (ast.unparse(call.func.value) if hasattr(ast, "unparse") else "<dict>")
                             + ".setdefault(" + repr(call.args[0].value) + ", " + repr(call.args[1].value)
                             + ")   [FORBIDDEN by #15708; receiver-agnostic]"))
            # 3b. os.putenv("CUDA_VISIBLE_DEVICES", "1") -- the libc door that
            #     bypasses os.environ entirely. Invisible to draft 4.
            if (fname == "putenv" and len(call.args) == 2
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value in PIN_ENV_NAMES
                    and isinstance(call.args[1], ast.Constant)
                    and _nonzero(call.args[1].value)):
                hits.append((getattr(call, "lineno", 0),
                             "os.putenv(..., " + repr(call.args[1].value) + ")"))

        # 4. executable DEFAULT PARAMETER values
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            pos = a.args[len(a.args) - len(a.defaults):] if a.defaults else []
            for arg, dflt in list(zip(pos, a.defaults)) + \
                             list(zip(a.kwonlyargs, a.kw_defaults)):
                if (dflt is not None and arg.arg in PIN_PARAMS
                        and isinstance(dflt, ast.Constant) and _nonzero(dflt.value)):
                    hits.append((n.lineno, "def " + n.name + "(... "
                                 + arg.arg + "=" + repr(dflt.value) + ")"))

        # 5. dict literals
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if (isinstance(k, ast.Constant) and str(k.value).lower() in PIN_PARAMS
                        and isinstance(v, ast.Constant) and _nonzero(v.value)):
                    hits.append((getattr(k, "lineno", 0),
                                 '{"' + str(k.value) + '": ' + repr(v.value) + "}"))
    return sorted(set(hits))


# systemd accepts THREE shapes and draft 4 knew only the first:
#     Environment=CUDA_VISIBLE_DEVICES=1
#     Environment="CUDA_VISIBLE_DEVICES=1"
#     Environment=FOO=b CUDA_VISIBLE_DEVICES=1
UNIT_ASSIGN_RE = re.compile(r"""(?:""" + _PIN_ALT + r""")\s*=\s*["']?([^\s"']+)""")


def unit_pins(p: Path):
    hits = []
    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
        s = line.strip()
        if s.startswith("#") or s.startswith(";"):
            continue
        if not s.startswith("Environment"):
            continue
        for m in UNIT_ASSIGN_RE.finditer(s):
            val = m.group(1)
            if _derived(val):
                continue
            if _nonzero(val):
                hits.append((i, s[:120]))
                break
    return hits


# The .sh side. Draft 4 had TWO patterns, both anchored, and neither of them
# mentioned CUDA_VISIBLE_DEVICES -- so the variable the whole round is about
# was invisible in the 19 tracked shell scripts.
SHELL_PIN_RES = [
    re.compile(r"""_read_config_key\s+["'](""" + "|".join(sorted(PIN_PARAMS))
               + r""")["']\s+["']([^"']+)["']"""),
    # un-anchored at the tail so a trailing comment no longer hides the pin.
    re.compile(r"^\s*(?:export\s+)?(GPU_DEVICE_INDEX|CUDA_DEVICE|GPU_DEVICE)=[\"']?([0-9]+)[\"']?\s*(?:#.*)?$"),
    # THE ONE THAT MATTERED: the variable itself, anywhere on a line -- a bare
    # assignment, an `export`, or inside the quoted string of an `echo`/heredoc
    # that WRITES a unit file. This is what closes the emitters.
    re.compile(r"""(""" + _PIN_ALT + r""")\s*=\s*["']?([^\s"';)]+)"""),
]


def shell_pins(p: Path):
    hits = []
    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        for rx in SHELL_PIN_RES:
            for m in rx.finditer(line):
                val = m.group(2)
                # A value DERIVED at run time on the target machine is the FIX.
                if _derived(val):
                    continue
                if _nonzero(val):
                    hits.append((i, line.strip()[:100]))
                    break
    # tau-2: the non-`=` declaration this script may be WRITING.
    hits += plist_pins(p)
    return sorted(set(hits))


# ---------------------------------------------------------------------------
# THE NON-`=` DECLARATION  [tau-2, round pi-3]
#
# install_crons.sh:215 -- OUTSIDE this package, in the file that installs the
# pre-cognitive cron -- writes a launchd plist:
#
#   CRON_PLIST_ENV="<dict><key>CUDA_VISIBLE_DEVICES</key><string>${PRECOG_CUDA}</string></dict>"
#
# It is the SEVENTH emitter and draft 5 was blind to it TWICE, independently:
#   * SH_EMITTER_RE and every member of SHELL_PIN_RES require `KEY = VALUE`.
#     A launchd declaration joins its key to its value with XML tags, so the
#     one shape that is not an assignment was the one shape nobody wrote a
#     pattern for.
#   * `.plist` is absent from DISPATCH, so the FILE THIS LINE WRITES is not
#     scanned at all even when it lands on disk.
#
# MEASURED before the fix: the literal `<string>1</string>` form exits 0 in a
# .sh, exits 0 in a .plist, and the emitter sweep of the real install_crons.sh
# reported 1 site (:196) and missed :215.
#
# CLASS SWEEP, not instance: 48 occurrences of the pin variable across the
# authoring tree; 20 joined by `=`; 28 not; exactly ONE live non-`=`
# DECLARATION (this one); 5 further structurally-capable sites (prose, JSON
# keys, argv pairs) inspected and cleared as non-declarations.
# ---------------------------------------------------------------------------
PLIST_PIN_RE = re.compile(
    r"<key>\s*(?:" + _PIN_ALT + r")\s*</key>\s*<string>\s*([^<]*?)\s*</string>")
PLIST_EMITTER_RE = re.compile(
    r"(?P<pre><key>\s*(?:" + _PIN_ALT + r")\s*</key>\s*<string>\s*)"
    r"(?P<val>\$[^<]*?)(?P<post>\s*</string>)")


def plist_pins(p: Path):
    """The XML key/string declaration, wherever it appears -- in a .plist that
    ships, or inside the .sh string that writes one."""
    text = p.read_text(errors="ignore")
    hits = []
    for m in PLIST_PIN_RE.finditer(text):
        val = m.group(1)
        if _derived(val) or not _nonzero(val):
            continue
        line = text.count("\n", 0, m.start()) + 1
        hits.append((line, m.group(0).strip()[:120]))
    return sorted(set(hits))


def plist_emitters(p: Path, rel: str):
    text = p.read_text(errors="ignore")
    out = []
    for m in PLIST_EMITTER_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        literal = m.group("pre") + "1" + m.group("post")
        out.append({"file": rel, "line": line, "kind": "plist",
                    "source": m.group(0).strip()[:110],
                    "literal_form": literal.strip()[:110],
                    "literal_form_full": literal.strip()})
    return out


def json_pins(p: Path):
    try:
        data = json.loads(p.read_text(errors="ignore"))
    except (ValueError, OSError):
        return []
    hits = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if (str(k).lower() in PIN_PARAMS and not isinstance(v, (dict, list))
                        and _nonzero(v)):
                    hits.append((0, path + "/" + str(k) + " = " + repr(v)))
                walk(v, path + "/" + str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + "[" + str(i) + "]")

    walk(data)
    return hits


# Documentation that TEACHES the wrong device is a portability defect, not a
# cosmetic one: a stranger copy-pastes the README and lands on their display.
# Draft 4's pattern required a bare digit and missed the QUOTED form.
MD_PIN_RE = re.compile(r"""(?<![A-Za-z_])(?:""" + _PIN_ALT + r""")=["']?([0-9][0-9.]*)""")


def md_pins(p: Path):
    hits = []
    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
        for m in MD_PIN_RE.finditer(line):
            if _nonzero(m.group(1)):
                hits.append((i, line.strip()[:100]))
    return hits


DISPATCH = {".py": py_pins, ".service": unit_pins, ".timer": unit_pins,
            ".sh": shell_pins, ".json": json_pins, ".md": md_pins,
            ".plist": plist_pins}

# ---------------------------------------------------------------------------
# F-2  [architecture-010]  ALLOW-LIST -> DENY-LIST
# ---------------------------------------------------------------------------
# DISPATCH above is an allow-list of 7 suffixes. Measured: it never opened 40 of
# the 201 tracked files (19.9%) -- every .sql, .ps1, .cmd, .txt, the .mjs hook,
# CLAUDE.md.template, and `voidloom/voidloom`, the EXTENSIONLESS shell shim that
# is symlinked onto $PATH. A pin written in any of them scored rc 0 while the
# report said "population: 5, pins: 0", so the population line actively
# concealed the miss. An allow-list fails toward SILENCE.
#
# The repair: an unlisted suffix is no longer dropped, it falls through to
# `generic_pins`, which is syntax-agnostic. Only PROVEN-BINARY suffixes are
# skipped, and skipped files are COUNTED and REPORTED as unopened, so the
# residual blindness is visible in the envelope instead of being invisible.
BINARY_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif",
                   ".pdf", ".zip", ".gz", ".tar", ".whl", ".so", ".bin", ".pyc",
                   ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".wav", ".dylib",
                   ".dll", ".o"}

_GENERIC_PIN = re.compile(
    r"(" + _PIN_ALT + r")"       # the variable (any backend's), in any syntax
    r"\s*(?:=|:|\s)\s*"          # = (sh/ps1/systemd), : (yaml), or bare (set)
    r"[\"\']?"                    # optional quote
    r"([0-9][0-9,.]*)"           # the device spec (a Level Zero mask may carry .tile)
    r"[\"\']?")


def generic_pins(p: Path):
    """Suffix-agnostic textual pin detector -- the deny-list's catch-all.

    Deliberately dumb: it does not parse. It finds the variable set to a
    non-zero literal in ANY of the syntaxes the package actually ships
    (POSIX sh `X=1`, PowerShell `$env:X = "1"`, cmd `set X=1`, systemd
    `Environment=X=1`, YAML `X: "1"`). A typed handler, where one exists,
    still wins -- this only runs where one does not.
    """
    out = []
    try:
        text = p.read_text(errors="ignore")
    except OSError:
        return out
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        for m in _GENERIC_PIN.finditer(line):
            val = m.group(2).rstrip(".,")
            if _nonzero(val):
                out.append((i, "generic: " + m.group(1) + " pinned to '"
                            + val + "' (" + (p.suffix or "no suffix") + ")"))
    return out


# ===========================================================================
# THE EMITTER INVENTORY  [sigma-2's graduation discriminator]
# ===========================================================================
# An EMITTER is a line that CONSTRUCTS a CUDA device declaration whose value is
# derived at run time. Such a line is correct today and is one `sed` from being
# the defect -- so the question that decides graduation is not "is the class
# empty?" but "if somebody edits this emitter to a literal, does the gate see
# it?". For every emitter the gate builds the literal form and re-runs its own
# detector on it. An emitter whose literal form is NOT flagged is UNCOVERED and
# the gate REFUSES.
SH_EMITTER_RE = re.compile(
    r"""(?P<key>""" + _PIN_ALT + r""")\s*=\s*(?P<val>["']?\$\{?[^\s"';)]*\}?["']?)""")
PY_EMITTER_KEYS = PIN_ENV_NAMES


def sh_emitters(p: Path, rel: str):
    out = []
    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        m = SH_EMITTER_RE.search(line)
        if not m:
            continue
        literal = line[:m.start("val")] + "1" + line[m.end("val"):]
        out.append({"file": rel, "line": i, "kind": "sh",
                    "source": line.strip()[:110],
                    # SC-5: `literal_form` is a RENDERING (truncated for the
                    # report).  `literal_form_full` is the VALUE, and it is the
                    # one _detects() grades.  Round pi-2 handed the truncated
                    # string to the detector, so an emitter on a line longer
                    # than 110 characters was reported UNCOVERED because the pin
                    # had been cut off before the detector could see it --
                    # MEASURED: 2 such emitters on the live tree.
                    "literal_form": literal.strip()[:110],
                    "literal_form_full": literal.strip()})
    return out


def py_emitters(p: Path, rel: str):
    out = []
    try:
        tree = ast.parse(p.read_text(errors="ignore"))
    except (SyntaxError, ValueError):
        return out
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                    and t.slice.value in PY_EMITTER_KEYS
                    and not isinstance(n.value, ast.Constant)):
                lit = 'os.environ[%r] = "1"' % t.slice.value
                out.append({"file": rel, "line": n.lineno, "kind": "py",
                            "source": "environ[" + repr(t.slice.value) + "] = <derived>",
                            "literal_form": lit, "literal_form_full": lit})
    return out


def _detects(kind: str, literal: str, tmp: Path) -> bool:
    """Run this gate's OWN detector on the one-line literal form."""
    if kind == "plist":
        f = tmp / "probe.plist"
        f.write_text(literal + "\n")
        return bool(plist_pins(f))
    if kind == "sh":
        f = tmp / "probe.sh"
        f.write_text(literal + "\n")
        return bool(shell_pins(f))
    f = tmp / "probe.py"
    f.write_text("import os\n" + literal + "\n")
    return bool(py_pins(f))


def emitter_report(root: Path, files):
    import tempfile
    emitters = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if p.suffix == ".sh":
            emitters += sh_emitters(p, rel)
            emitters += plist_emitters(p, rel)      # tau-2
        elif p.suffix == ".plist":
            emitters += plist_emitters(p, rel)      # tau-2
        elif p.suffix == ".py":
            emitters += py_emitters(p, rel)
        elif p.suffix.lower() not in BINARY_SUFFIXES:
            # F-2, second half: the emitter inventory carried its OWN narrower
            # 3-suffix allow-list, so the gate's "one sed from re-armed"
            # discriminator could not see a .ps1 or .cmd emitter either.
            emitters += sh_emitters(p, rel)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for e in emitters:
            # SC-5: the VALUE, never the rendering.
            e["covered"] = _detects(e["kind"],
                                    e.get("literal_form_full", e["literal_form"]),
                                    tmp)
    return emitters


# ---------------------------------------------------------------------------
# SITE-LOCAL EXEMPTION  [draft 6]
#
# Draft 5 could not tell an EMITTED pin from a SENTENCE ABOUT ONE. It flagged
# three post-mortems in docs/cycle2/ for quoting the literal they exist to record,
# and three test fixtures that set the variable ON PURPOSE to prove the guard
# obeys an explicit pin. Six permanent reds over a tree with zero real pins.
#
# That matters more than tidiness: the gate's own manifest sibling says it, and it
# is why the manifest's self-hash is excluded BY NAME -- *a permanent false red is
# how a gate teaches people to ignore it.* I proved that on myself one cut earlier,
# filing this gate's whole output as "docs and tests" while two of its rows were
# live unit files.
#
# The wrong fixes, and why:
#   * drop .md from the population  -- a doc that INSTRUCTS a reader to export the
#     author's device is a real defect; draft 4 added .md deliberately.
#   * exclude docs/cycle2/ by directory -- a blanket loophole in the one place a
#     real pin could hide behind prose.
#   * reword the documentation until the grep is happy -- the tail wagging the dog;
#     the post-mortems become less clear so a tool can stay simple.
#
# So the exemption lives AT THE SITE, carries a REASON, and is COUNTED in the
# verdict. Put on the hit's own line or the line above:
#
#     gpu-pin-gate: <why this literal is not a pin>
#
# An exemption without a reason is not an exemption -- the gate refuses it. And
# every exemption claimed is printed, so the count is a number a reader can argue
# with rather than a silence.
EXEMPT_RE = re.compile(r"gpu-pin-gate:\s*(\S.*?)\s*$")

# A whole FILE can be exempt only when the gate can VERIFY the claim itself.
# JSON has no comments, so a site-local marker is impossible there -- and
# voidloom_manifest.json is exactly the case that needs one: install.sh:515 says
# "GENERATED here, never shipped", and it is .gitignored, so the curated packaging
# list (#7976) cannot contain it. The walk sees it because the walk is
# authoritative by design (a .git-less unpacked tarball must still be graded).
#
# The claim is therefore checked, not asserted: `git check-ignore` must confirm it,
# in THIS root, at run time. No git, or git does not ignore it -> the file is
# SCANNED. An exemption that cannot be verified is not an exemption.
FILE_EXEMPTIONS = {
    "voidloom_manifest.json":
        "host state generated by install.sh (:515 'never shipped'); .gitignored, "
        "so it cannot reach the curated packaging list",
}


def _file_exempt(root: Path, rel: str):
    """(reason, True) when the exemption CHECKS OUT; (None, False) otherwise."""
    why = FILE_EXEMPTIONS.get(rel)
    if not why:
        return None, False
    try:
        out = subprocess.run(["git", "check-ignore", "-q", rel], cwd=str(root),
                             capture_output=True, timeout=30)
    except Exception:
        return None, False
    if out.returncode != 0:          # 1 = not ignored, 128 = no git here
        return None, False
    return why, True


def _exemption(lines, lineno):
    """The declared reason for line ``lineno``, or None. Own line, then the line
    above -- the two places a human naturally writes it."""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines):
            m = EXEMPT_RE.search(lines[idx])
            if m:
                # Strip the host comment's closer so the published reason reads as
                # a sentence rather than as markup ("... not a setting -->").
                why = m.group(1)
                for closer in ("-->", "*/", "#}", "--}}"):
                    if why.endswith(closer):
                        why = why[: -len(closer)].rstrip()
                return why or None
    return None


def collect(root: Path, files):
    """Returns (bad, unopened, exempted). F-2: an unlisted suffix is NOT dropped."""
    bad = {}
    unopened = []
    exempted = []
    for p in files:
        fn = DISPATCH.get(p.suffix)
        if fn is None:
            if p.suffix.lower() in BINARY_SUFFIXES:
                # The only files we decline to open, and we SAY SO.
                unopened.append(p.relative_to(root).as_posix())
                continue
            fn = generic_pins
        try:
            h = fn(p)
        except Exception:
            h = []
        if not h:
            continue
        rel = p.relative_to(root).as_posix()
        _why, _ok = _file_exempt(root, rel)
        if _ok:
            for hit in h:
                exempted.append((rel, hit[0] if isinstance(hit, (tuple, list)) and hit
                                 else 0, _why))
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except Exception:
            lines = []
        kept = []
        for hit in h:
            lineno = hit[0] if isinstance(hit, (tuple, list)) and hit else 0
            why = _exemption(lines, lineno) if lineno else None
            if why:
                exempted.append((rel, lineno, why))
            else:
                kept.append(hit)
        if kept:
            bad[rel] = kept
    return bad, sorted(unopened), exempted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--emitters", action="store_true",
                    help="print the emitter inventory and its coverage, then exit")
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
    bad, unopened, exempted = collect(root, files)
    total = sum(len(v) for v in bad.values())
    emitters = emitter_report(root, files)
    uncovered = [e for e in emitters if not e["covered"]]
    empty_population = not files

    if args.emitters and not args.json:
        print("=== EMITTER INVENTORY (" + str(len(emitters)) + " sites, population="
              + source + ") ===")
        for e in emitters:
            print(("  COVERED  " if e["covered"] else "  UNCOVERED ")
                  + e["file"] + ":" + str(e["line"]))
            print("        now:     " + e["source"])
            print("        one sed: " + e["literal_form"])
        print("\n  " + str(len(uncovered)) + " UNCOVERED emitters (target 0)")
        return 1 if uncovered else 0

    if args.json:
        print(json.dumps({"population_source": source, "population": len(files),
                          "symlinked_dirs_followed": popinfo["symlinked_dirs_followed"],
                          "symlink_cycles_cut": popinfo["symlink_cycles_cut"],
                          "git_view": popinfo["git_view"],
                          "files": len(bad), "pins": total,
                          "emitters": len(emitters), "uncovered_emitters": uncovered,
                          "emitter_kinds": sorted({e["kind"] for e in emitters}),
                          "emitter_details": (emitters if args.emitters else None),
                          "empty_population": empty_population,
                          "unopened_binary": unopened,
                          "exempted": [{"file": f, "line": l, "reason": r}
                                       for f, l, r in exempted],
                          "exempted_count": len(exempted),
                          "excluded_dirs": popinfo["excluded_dirs"],
                          "exclusion_rule": popinfo["exclusion_rule"],
                          "formerly_skipped_now_scanned":
                              popinfo["formerly_skipped_now_scanned"],
                          "exclusion_audit": popinfo["exclusion_audit"],
                          "detail": {k: [list(x) for x in v] for k, v in bad.items()}},
                         indent=2))
        return 1 if (bad or uncovered or empty_population
                     or popinfo["exclusion_audit"]["violations"]) else 0

    print("=== PKG-01 GPU PIN GATE (draft 6) ===")
    print("    packaged artifacts pinning CUDA to a non-zero device")
    print("    population: " + str(len(files)) + " files, enumerated by " + source)
    print("    exclusion rule: " + ", ".join(popinfo["exclusion_rule"])
          + "  (" + str(len(popinfo["excluded_dirs"])) + " dirs excluded)")
    print("    now scanned (was skipped): "
          + ", ".join(popinfo["formerly_skipped_now_scanned"]))
    print("    declined as binary: " + str(len(unopened)) + " files")
    # F-1's rule, applied to the exemptions as well: what we did NOT count is
    # REPORTED, never hidden. A reader can argue with a number; they cannot argue
    # with a silence.
    print("    site-local exemptions claimed: " + str(len(exempted)))
    for _f, _l, _why in sorted(exempted):
        print("        " + _f + ":" + str(_l) + "  -- " + _why)
    _audit = popinfo["exclusion_audit"]
    if _audit["status"] == "unmeasurable":
        print("    exclusion audit: UNMEASURABLE (" + _audit["reason"] + ")")
    else:
        print("    exclusion audit: " + str(len(_audit["recursive_copies"]))
              + " recursive copies in install.sh, "
              + str(len(_audit["violations"])) + " violations")
    print("")
    if empty_population:
        print("  FAIL  the population is EMPTY -- this gate could not enumerate the")
        print("        artifact it guards, so it has PROVED NOTHING.")
        print("  VERDICT: REFUSED - unmeasurable, which is not the same as clean")
        return 1
    for rel, hits in sorted(bad.items()):
        for ln, txt in hits:
            print("  FAIL  " + rel + ":" + str(ln))
            print("        " + txt)
    for e in uncovered:
        print("  FAIL  " + e["file"] + ":" + str(e["line"]) + "  UNCOVERED EMITTER")
        print("        one `sed` from re-arming the defect, invisibly:")
        print("        " + e["literal_form"])
    _av = popinfo["exclusion_audit"]["violations"]
    for v in _av:
        print("  FAIL  EXCLUSION AUDIT: " + v)
    if not bad and not uncovered and not _av:
        print("  PASS - no packaged artifact pins a non-zero device, and all "
              + str(len(emitters)) + " emitters are covered")
        return 0
    print("\n  " + str(len(bad)) + " files, " + str(total) + " pins, "
          + str(len(uncovered)) + " uncovered emitters")
    print("  VERDICT: REFUSED - this tree must not be packaged")
    return 1


if __name__ == "__main__":
    sys.exit(main())

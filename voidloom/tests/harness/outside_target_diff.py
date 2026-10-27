#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""outside_target_diff.py -- THE ISOLATION BOUNDARY IS A SNAPSHOT-DIFF TEST.

Council #12, F4 (2026-09-18). The first wet run of install.ps1 rewrote the
operator's real user PATH from inside a "scratch" profile (#20140): a scratch
directory is not a boundary. This harness is the boundary. It snapshots a $HOME
BEFORE and AFTER an installer run and refuses on any difference the installer's
DECLARED write-set (install/write_set.json, resolved by `install.sh
--print-write-set`) does not name.

    snapshot --home H --target-root T [--env-file E] --out pre.json
    diff     --pre pre.json --post post.json --declared declared.json [--json] [--report r.json]
    run      --home H --target-root T --declared declared.json [--env-file E] [--report r.json] -- cmd args...
             (the command runs with HOME=H, the XDG base dirs and TMPDIR under H, pinned by
             this harness -- never the caller's; the product's path overrides must resolve under H)
    verbs    --declared declared.json --log recorder.log [--json] [--report r.json]

WHAT IS SNAPSHOTTED
  * the NAMED set the criterion lists, each with its own per-member verdict:
    $HOME/.claude/settings.json, $HOME/.claude/CLAUDE.md, $HOME/.config/systemd/user/,
    $HOME/.local/bin/, the env file (default and legacy paths), $HOME/.bashrc,
    $HOME/.profile, $HOME/.zshrc. Each FILE member is graded from its OWN
    before/after record -- never from the wide walk -- because settings.json and
    CLAUDE.md sit INSIDE the default target root, which the wide walk excludes;
    an undeclared write to either is RED naming it (round-1 reflection, item 1);
  * the WIDE sweep: EVERY path under $HOME except the target root (the install
    root, ${CLAUDE_HOME}), which is bound by the lay manifest instead. The one
    exclusion is printed. Directories are recorded too, so an empty new
    directory is a difference. Symlinks are recorded by target, never followed.

WHAT `run` PINS -- the boundary is every base-directory variable, not one
  HOME is not the only variable a HOME-relative write follows. Round-1
  iteration-2 reflection, item 1, MEASURED: with a caller's XDG_CONFIG_HOME
  outside --home, install_crons.sh wrote 16 unit files to
  ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user and made 10 manager calls
  OUTSIDE the snapshot, and the verdict was GREEN. So `run` hands its command
  an environment in which
    * HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME, XDG_DATA_HOME, XDG_STATE_HOME are
      PINNED under --home (the XDG defaults then derive from the pinned HOME);
    * TMPDIR is PINNED to <--home>/tmp, created before the first snapshot
      (reference-host round, R5: measured by the round-1 iteration-5 reflector,
      a child's `mktemp` -> ${TMPDIR}/tmp.* and torch's torchinductor_* landed in
      the caller's /tmp -- outside the snapshot, unobservable, so a leftover the
      write-set declares `transient` could never be seen left behind);
    * the model/wheel cache redirectors (HF_HOME, HF_HUB_CACHE,
      HUGGINGFACE_HUB_CACHE, TRANSFORMERS_CACHE, PIP_CACHE_DIR,
      TORCHINDUCTOR_CACHE_DIR, TRITON_CACHE_DIR, CUDA_CACHE_PATH) are DROPPED,
      so their defaults derive from the pinned dirs;
    * every WIZARD_* variable whose value is an absolute path -- and every
      value of the path-bearing WIZARD_* variables (WIZARD_PATH_VARS), whatever
      its spelling -- is REFUSED (rc 4, the variable named) unless it is an
      absolute path that resolves under --home -- except the named READ-ONLY
      inputs (WIZARD_PYTHON_BIN: an interpreter that is executed, never a write
      target; WIZARD_LIBRARY_SEED: a seed pack that is read), which are
      reported, not refused. A relative value, a `~/x` inside a quoted variable
      or a literal `$HOME/x` is not "not a path": the child resolves it against
      its cwd (iteration-3 reflection, item 2, measured).
  The report carries `command_env_pinned`: the pinned names and values, the
  dropped names, and every WIZARD_* checked (with the exemptions named).

WHAT `verbs` GRADES -- the manager calls, the HKCU class on Linux
  A write made THROUGH a manager (`systemctl --user enable --now`, `launchctl
  load`) escapes the scratch $HOME: the user manager writes
  ${SYSTEMD_USER_DIR}/timers.target.wants/<timer> and the filesystem diff sees
  nothing when a recorder shim stands in for the manager. So the shim's log is
  GRADED, not printed: every recorded call must equal an ACTIVE declared verb
  (write_set.json `verbs`, resolved by --print-write-set), and every active
  declared verb must have been recorded -- set equality, both directions named.
  A verb declared `match: glob` is an fnmatch PATTERN over the whitespace-normalised
  call (the thinker's probe unit and candidate file are fresh per run: model-choice
  Round 1, Wave B -- M17); every other verb is compared exactly.

WHAT `untouched` GRADES (M17) -- the owner's everyday vendor logins
  --print-write-set also resolves write_set.json `untouched` (the owner's ~/.codex
  and ~/.kimi-code). Any change under an untouched path is RED, named as such, even
  when a broader declaration would cover it: the brain signs in with its OWN
  credential homes, and a child that refreshed the owner's login is a defect however
  the rest of the diff reads.

VERDICTS AND EXIT CODES -- distinct, so a caller can tell them apart
  0  GREEN        every difference is named by an ACTIVE declared entry
  1  RED          at least one difference is undeclared -- each is named
  2  usage / unreadable input
  3  UNMEASURABLE the snapshots observed nothing (empty population) -- a pass
                  over an empty observation set is not a pass (#20006)
  4  REFUSED      --home is the invoking user's real login home (F4), or a
                  WIZARD_* path override points the command outside --home
                  (an absolute path elsewhere, or a path-bearing variable
                  whose value is not an absolute path at all)
  NOTE: run's exit code is the DIFF verdict, never the command's. A command
  that fails under a clean diff is GREEN rc 0 (measured: `[install] FATAL`
  rc 1 under GREEN); read `command_rc` in the report and assert it yourself.
  (verbs: 0 every recorded call is declared and every active verb was recorded;
   1 a recorded call is undeclared or an active verb was not recorded, each named;
   3 the log recorded nothing -- an empty observation set is not a pass)

Declared paths match exactly, or as `<dir>/**` (anything at or beneath), or as a
basename glob in a fixed parent (`tmp.*`); an ADDED DIRECTORY above a declared
path is implied by it (mkdir -p). A declaration marked active=false is
NOT a permission: a change at such a path is reported as undeclared, with the
note that the declaration exists but is inactive for this invocation.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RC_GREEN, RC_RED, RC_USAGE, RC_UNMEASURABLE, RC_REFUSED = 0, 1, 2, 3, 4
# THE BOUNDARY IS EVERY BASE-DIRECTORY VARIABLE, NOT ONE (see the docstring).
XDG_PINNED = {"XDG_CONFIG_HOME": (".config",), "XDG_CACHE_HOME": (".cache",),
              "XDG_DATA_HOME": (".local", "share"), "XDG_STATE_HOME": (".local", "state")}
# ...and the temporary directory (R5): <--home>/tmp, created by `run` before the pre-snapshot so
# its own creation is not a difference; what a child leaves in it IS one.
TMPDIR_PINNED = "tmp"
CACHE_DROPPED = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "PIP_CACHE_DIR",
                 "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR", "CUDA_CACHE_PATH")
# Path overrides OUTSIDE the WIZARD_* family that decide where a child writes (model-choice Round 1, Wave B -- M17):
# VOIDLOOM_ENV_DIR moves the overnight thinker's credential home (<VOIDLOOM_ENV_DIR>/thinker), so it is held to the
# same rule -- an absolute path under --home, or the run is refused naming it.
OTHER_PATH_VARS = ("VOIDLOOM_ENV_DIR",)
# WIZARD_* variables whose absolute-path value is READ, never written through: reported, not refused.
WIZARD_READ_ONLY = {"WIZARD_PYTHON_BIN": "an interpreter: executed, never a write target",
                    "WIZARD_LIBRARY_SEED": "a seed pack: read by self_check.py, never written"}
# WIZARD_* variables the installers USE AS PATHS, each CITED WHERE IT IS READ as `CITE <file>:<line> <token>`.
# voidloom/tests/test_outside_target_diff.py re-reads every cited line and refuses a citation whose line no longer
# carries its token (reference-host round, R10a: this line used to read "install.sh:90/114/135-143,
# install_crons.sh:105-110/129-131" -- numbers that had drifted off the variables they named).
#   CITE install.sh:178 WIZARD_CLAUDE_HOME
#   CITE install.sh:197 WIZARD_ENV_FILE
#   CITE install.sh:215 WIZARD_CONFIG_DIR
#   CITE install.sh:823 WIZARD_CONFIG_PATH
#   CITE cron/_config_dir.sh:9 WIZARD_CONFIG_DIR
#   CITE install/install_crons.sh:118 WIZARD_LIBRARY_HOME
#   CITE install/install_crons.sh:123 WIZARD_LIBRARY_HOME
#   CITE install/install_crons.sh:124 WIZARD_LIBRARY_DB
#   CITE install/install_crons.sh:143 WIZARD_ENV_FILE
#   CITE organs/voidloom_device.py:367 WIZARD_CONFIG_PATH
#   CITE organs/wizard_config.py:396 _env_key   -> WIZARD_DB_PATH, WIZARD_MODULE_DIR (WIZARD_<KEY> for db_path, module_dir)
#   CITE organs/voidloom_env.py:88 VOIDLOOM_ENV_DIR   -> the thinker's credential home (<VOIDLOOM_ENV_DIR>/thinker): OTHER_PATH_VARS
# Any value of these is a path whatever its spelling: a relative value, `~/x` inside a quoted variable (bash does
# not tilde-expand it) or a literal `$HOME/x` is resolved by the child against ITS CWD -- measured: three such
# values each wrote a file outside --home under GREEN. So for these, only an absolute path under --home is allowed.
WIZARD_PATH_VARS = ("WIZARD_CLAUDE_HOME", "WIZARD_ENV_FILE", "WIZARD_CONFIG_DIR", "WIZARD_CONFIG_PATH",
                    "WIZARD_LIBRARY_HOME", "WIZARD_LIBRARY_DB", "WIZARD_DB_PATH", "WIZARD_MODULE_DIR")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(p: Path):
    """One path's observable state. lstat: a symlink is its target, never followed."""
    try:
        st = os.lstat(str(p))
    except FileNotFoundError:
        return {"state": "absent"}
    except OSError as e:
        return {"state": "unreadable", "error": "%s: %s" % (type(e).__name__, e)}
    import stat as _st
    if _st.S_ISLNK(st.st_mode):
        return {"state": "symlink", "target": os.readlink(str(p))}
    if _st.S_ISDIR(st.st_mode):
        return {"state": "dir", "mode": oct(st.st_mode & 0o7777)}
    if _st.S_ISREG(st.st_mode):
        try:
            return {"state": "file", "size": st.st_size, "mode": oct(st.st_mode & 0o7777), "sha256": _sha(p)}
        except OSError as e:
            return {"state": "unreadable", "error": "%s: %s" % (type(e).__name__, e)}
    return {"state": "other", "mode": oct(st.st_mode & 0o7777)}


def named_set(home: Path, env_file):
    members = {
        "claude_settings": home / ".claude" / "settings.json",
        "claude_md": home / ".claude" / "CLAUDE.md",
        "systemd_user_dir": home / ".config" / "systemd" / "user",
        "local_bin": home / ".local" / "bin",
        "env_file_default": home / ".voidloom" / "wizard-library-env",
        "bashrc": home / ".bashrc",
        "profile": home / ".profile",
        "zshrc": home / ".zshrc",
    }
    if env_file:
        members["env_file"] = Path(env_file)
    return members


def walk_wide(home: Path, target_root: Path, own_dirs=()):
    """Every path under home except the target root. Never follows symlinks.

    `own_dirs`: directories the HARNESS itself created before the pre-snapshot (the pinned
    TMPDIR, R5). The directory entry is not recorded -- its creation is the instrument's, and
    counting it would turn an empty observation set into a non-empty one (UNMEASURABLE must
    stay reachable) -- but everything INSIDE it is recorded and graded like any other path."""
    out = {}
    own = {str(Path(d).resolve()) for d in own_dirs}
    tr = str(target_root.resolve()) if target_root else None
    for root, dirs, files in os.walk(str(home), followlinks=False):
        rp = Path(root)
        if tr and str(rp.resolve()) == tr:
            dirs[:] = []
            continue
        # prune the target root wherever it appears
        keep = []
        for d in dirs:
            if tr and str((rp / d).resolve()) == tr:
                continue
            keep.append(d)
        dirs[:] = keep
        for d in dirs:
            if str((rp / d).resolve()) in own and not (rp / d).is_symlink():
                continue
            out[str((rp / d).relative_to(home))] = record(rp / d)
        for f in files:
            out[str((rp / f).relative_to(home))] = record(rp / f)
    return out


def snapshot(home: Path, target_root, env_file, own_dirs=()):
    home = Path(home)
    if not home.is_dir():
        return None, "home is not a directory: %s" % home
    named = {}
    for label, p in named_set(home, env_file).items():
        rec = record(p)
        if rec["state"] == "dir":
            rec["entries"] = walk_wide(p, None)
        named[label] = {"path": str(p), **rec}
    wide = walk_wide(home, Path(target_root) if target_root else None, own_dirs)
    return {
        "home": str(home),
        "target_root_excluded": str(target_root) if target_root else None,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "named": named,
        "wide": wide,
        "counts": {"wide_paths": len(wide), "named_present": sum(1 for v in named.values() if v["state"] != "absent")},
    }, None


def _declared_matches(path: str, decl_path: str, change=None) -> bool:
    """path and decl_path are absolute strings.

    mkdir -p semantics: a declared path implies the directories above it, so an
    ADDED DIRECTORY that is a proper ancestor of a declared path is declared by
    it (creating ~/.config/systemd/user creates ~/.config and ~/.config/systemd).
    Nothing else is implied: a file or a modified directory at an ancestor is
    not covered.
    """
    if decl_path.endswith("/**"):
        base = decl_path[:-3]
        if path == base or path.startswith(base.rstrip("/") + "/"):
            return True
        decl_path = base
    elif "*" in os.path.basename(decl_path):
        if os.path.dirname(path) == os.path.dirname(decl_path) and fnmatch.fnmatch(
                os.path.basename(path), os.path.basename(decl_path)):
            return True
        decl_path = os.path.dirname(decl_path)
    if path == decl_path:
        return True
    if change is not None and change.get("change") == "added" and change.get("after", {}).get("state") == "dir":
        return decl_path.startswith(path.rstrip("/") + "/")
    return False


def diff(pre, post, declared):
    home = Path(pre["home"])
    entries = declared.get("declared", declared if isinstance(declared, list) else [])
    active = [d for d in entries if d.get("active", True)]
    inactive = [d for d in entries if not d.get("active", True)]
    changes = []
    for rel in sorted(set(pre["wide"]) | set(post["wide"])):
        a, b = pre["wide"].get(rel, {"state": "absent"}), post["wide"].get(rel, {"state": "absent"})
        if a != b:
            kind = "added" if a["state"] == "absent" else "removed" if b["state"] == "absent" else "modified"
            changes.append({"path": str(home / rel), "change": kind, "before": a, "after": b})
    # The NAMED members are graded from their OWN records, not from the wide walk:
    # $HOME/.claude/settings.json and CLAUDE.md sit INSIDE the default target root,
    # which the wide walk excludes, and an undeclared write to either must still be
    # RED naming the path (R5 names them first; the Council calls them the two
    # riskiest outside-target writes). Without this, a change there was labelled
    # "changed-declared" with no declaration and the verdict stayed GREEN.
    seen = {c["path"] for c in changes}
    for label in sorted(set(pre["named"]) | set(post["named"])):
        a, b = pre["named"].get(label), post["named"].get(label)
        if a is None or b is None or a == b:
            continue
        pa = a["path"]
        if pa in seen or a.get("state") == "dir" or b.get("state") == "dir":
            continue            # directory members are graded by their wide entries
        ra = {k: v for k, v in a.items() if k != "path"}
        rb = {k: v for k, v in b.items() if k != "path"}
        kind = "added" if ra["state"] == "absent" else "removed" if rb["state"] == "absent" else "modified"
        changes.append({"path": pa, "change": kind, "before": ra, "after": rb, "named_member": label})
        seen.add(pa)
    undeclared, declared_hits = [], []
    untouched = declared.get("untouched", []) if isinstance(declared, dict) else []
    for c in changes:
        ut = next((u for u in untouched if _declared_matches(c["path"], u["path"])), None)
        if ut:
            c["note"] = "an UNTOUCHED path changed (%s): %s" % (ut["path"], ut.get("why", ""))
            c["untouched"] = ut["path"]
            undeclared.append(c)
            continue
        hit = next((d for d in active if _declared_matches(c["path"], d["path"], c)), None)
        if hit and hit.get("kind") == "transient" and c["change"] != "removed":
            # A `transient` declaration says "removed before exit, so a post-run
            # snapshot must not see it": it permits the path to have EXISTED during
            # the run, not to be left behind. Present after the run = undeclared
            # (round-1 iteration-2 reflection, item 9).
            c["note"] = "declared TRANSIENT but present after the run (%s: %s)" % (hit["step"], hit["path"])
            c["declared_kind"] = "transient"
            undeclared.append(c)
            continue
        if hit:
            c["declared_by"] = "%s: %s" % (hit["step"], hit["path"])
            c["declared_kind"] = hit.get("kind")
            declared_hits.append(c)
            continue
        inact = next((d for d in inactive if _declared_matches(c["path"], d["path"], c)), None)
        if inact:
            c["note"] = "a declaration exists (%s: %s) but is INACTIVE for this invocation (when=%s)" % (
                inact["step"], inact["path"], inact.get("when"))
        undeclared.append(c)
    # per-member verdict for the named set
    named = {}
    for label in sorted(set(pre["named"]) | set(post["named"])):
        a, b = pre["named"].get(label), post["named"].get(label)
        pa = (a or b)["path"]
        if a == b:
            named[label] = {"path": pa, "verdict": "absent-before-and-after" if a["state"] == "absent" else "unchanged"}
            continue
        # changed: declared or not? (a directory member is judged by its changed entries)
        hits = [c for c in changes if c["path"] == pa or c["path"].startswith(pa.rstrip("/") + "/")]
        und = [c for c in hits if c in undeclared]
        named[label] = {"path": pa, "verdict": "changed-UNDECLARED" if und else "changed-declared",
                        "changes": [c["path"] for c in hits], "undeclared": [c["path"] for c in und]}
    # An observed undeclared change is a MEASUREMENT whatever the wide population:
    # RED precedes UNMEASURABLE (with --target-root == --home the wide walk is
    # empty, and a planted write to a named member was rc 3, not rc 1 -- round-1
    # iteration-2 reflection, item 6). The population counts the named members
    # present too, since they are graded from their own records.
    observed = (pre["counts"]["wide_paths"] + post["counts"]["wide_paths"]
                + pre["counts"]["named_present"] + post["counts"]["named_present"])
    if undeclared:
        verdict, rc = "RED", RC_RED
    elif observed == 0:
        verdict, rc = "UNMEASURABLE", RC_UNMEASURABLE
    else:
        verdict, rc = "GREEN", RC_GREEN
    return {
        "verdict": verdict, "rc": rc, "home": pre["home"],
        "target_root_excluded": pre.get("target_root_excluded"),
        "observed_paths": {"before": pre["counts"]["wide_paths"], "after": post["counts"]["wide_paths"],
                           "named_present_before": pre["counts"]["named_present"],
                           "named_present_after": post["counts"]["named_present"]},
        "changes": len(changes), "declared_matches": len(declared_hits), "undeclared": len(undeclared),
        "active_declarations": len(active), "inactive_declarations": len(inactive),
        "named": named,
        "undeclared_changes": undeclared,
        "declared_changes": [{"path": c["path"], "change": c["change"], "declared_by": c["declared_by"]} for c in declared_hits],
    }


def print_report(r):
    print("[diff] %s  home=%s  excluded=%s" % (r["verdict"], r["home"], r["target_root_excluded"]))
    print("[diff] observed paths before=%d after=%d; changes=%d; declared=%d; undeclared=%d "
          "(active declarations %d, inactive %d)" % (
              r["observed_paths"]["before"], r["observed_paths"]["after"], r["changes"],
              r["declared_matches"], r["undeclared"], r["active_declarations"], r["inactive_declarations"]))
    for label, m in r["named"].items():
        line = "[diff]   %-18s %-24s %s" % (label, m["verdict"], m["path"])
        if m.get("undeclared"):
            line += "  UNDECLARED: " + ", ".join(m["undeclared"])
        print(line)
    for c in r["declared_changes"]:
        print("[diff]   declared  %-8s %s  (%s)" % (c["change"], c["path"], c["declared_by"]))
    for c in r["undeclared_changes"]:
        print("[diff]   UNDECLARED %-8s %s%s" % (c["change"], c["path"], ("  -- " + c["note"]) if c.get("note") else ""),
              file=sys.stderr)
    if r["verdict"] == "UNMEASURABLE":
        print("[diff] UNMEASURABLE: neither snapshot observed a single path (wide or named); nothing was graded",
              file=sys.stderr)
    if r.get("command_env_pinned"):
        e = r["command_env_pinned"]
        print("[diff] command env pinned: HOME=%s XDG_CONFIG_HOME=%s XDG_CACHE_HOME=%s XDG_DATA_HOME=%s "
              "XDG_STATE_HOME=%s TMPDIR=%s; dropped %s; WIZARD_* checked %s (exempt read-only: %s)" % (
                  e["HOME"], e["XDG_CONFIG_HOME"], e["XDG_CACHE_HOME"], e["XDG_DATA_HOME"], e["XDG_STATE_HOME"],
                  e.get("TMPDIR"),
                  e["dropped"] or "none", sorted(e["wizard_checked"]) or "none", sorted(e["wizard_exempt_read_only"]) or "none"))


def _norm_call(line: str) -> str:
    """One recorded or declared call, whitespace-normalised; quotes the shell
    would have stripped are stripped here too so `'x'` and `x` compare equal."""
    return " ".join(tok.strip("'\"") for tok in line.split())


def grade_verbs(declared, log_lines):
    """Grade a recorder shim's log against the declared verbs. Every recorded call
    must be an ACTIVE declared verb; every active declared verb must have been
    recorded (set equality, both directions named). An inactive declaration is
    not a permission, same as for paths."""
    verbs = declared.get("verbs", [])
    globs = {_norm_call(v["call"]): v for v in verbs if v.get("active", True) and v.get("match") == "glob"}
    active = {_norm_call(v["call"]): v for v in verbs if v.get("active", True) and v.get("match") != "glob"}
    inactive = {_norm_call(v["call"]): v for v in verbs if not v.get("active", True)}
    recorded = [_norm_call(l) for l in log_lines if l.strip()]
    glob_hit = {g: [c for c in recorded if fnmatch.fnmatchcase(c, g)] for g in globs}
    if not recorded:
        return {"verdict": "UNMEASURABLE", "rc": RC_UNMEASURABLE, "recorded": 0,
                "active_verbs": len(active), "inactive_verbs": len(inactive),
                "undeclared_calls": [], "unrecorded_verbs": sorted(active)}
    undeclared = []
    for c in recorded:
        if c in active or any(c in hits for hits in glob_hit.values()):
            continue
        note = None
        if c in inactive:
            note = "a declaration exists (%s: %s) but is INACTIVE for this invocation (when=%s)" % (
                inactive[c]["step"], inactive[c]["call"], inactive[c].get("when"))
        undeclared.append({"call": c, "note": note})
    unrecorded = sorted(set(active) - set(recorded)) + sorted(g for g, hits in glob_hit.items() if not hits)
    ok = not undeclared and not unrecorded
    return {"verdict": "GREEN" if ok else "RED", "rc": RC_GREEN if ok else RC_RED,
            "recorded": len(recorded), "distinct_recorded": len(set(recorded)),
            "active_verbs": len(active), "inactive_verbs": len(inactive),
            "declared_hits": sorted(set(recorded) & set(active)) + sorted(set(c for h in glob_hit.values() for c in h)),
            "undeclared_calls": undeclared, "unrecorded_verbs": unrecorded}


def print_verbs_report(r):
    print("[verbs] %s  recorded=%d active declared=%d inactive=%d" % (
        r["verdict"], r["recorded"], r["active_verbs"], r["inactive_verbs"]))
    for c in r.get("declared_hits", []):
        print("[verbs]   declared   %s" % c)
    for c in r["undeclared_calls"]:
        print("[verbs]   UNDECLARED %s%s" % (c["call"], ("  -- " + c["note"]) if c.get("note") else ""),
              file=sys.stderr)
    for c in r["unrecorded_verbs"]:
        print("[verbs]   NOT RECORDED (declared active, never called) %s" % c, file=sys.stderr)
    if r["verdict"] == "UNMEASURABLE":
        print("[verbs] UNMEASURABLE: the log recorded no call; nothing was graded", file=sys.stderr)


def pinned_child_env(home: Path, environ=None):
    """The environment `run` hands its command: (env, report, refused).

    HOME and the four XDG base dirs are pinned under `home`; the cache
    redirectors are dropped; a WIZARD_* variable whose value is an absolute path
    must resolve under `home` or the whole run is refused, naming it; a
    path-bearing WIZARD_* variable (WIZARD_PATH_VARS) with a NON-absolute value
    is refused whatever its spelling. `refused` is the {name: value} of the
    offenders (empty when the run may proceed).
    """
    environ = dict(os.environ if environ is None else environ)
    home_res = Path(home).resolve()
    env = dict(environ)
    env["HOME"] = str(home_res)
    for k, parts in XDG_PINNED.items():
        env[k] = str(home_res.joinpath(*parts))
    env["TMPDIR"] = str(home_res / TMPDIR_PINNED)
    dropped = [k for k in CACHE_DROPPED if env.pop(k, None) is not None]
    checked, exempt, refused = {}, {}, {}
    prefix = str(home_res) + os.sep
    for k in sorted(env):
        if not (k.startswith("WIZARD_") or k in OTHER_PATH_VARS):
            continue
        v = env[k]
        if not v or not (k in WIZARD_PATH_VARS or os.path.isabs(v) or v.startswith("~")):
            continue                      # not a path (a model name, a device index, a unit list, an hour)
        checked[k] = v
        if k in WIZARD_READ_ONLY:
            exempt[k] = WIZARD_READ_ONLY[k]
            continue
        if not os.path.isabs(v):
            refused[k] = v + "  (not an absolute path: the child resolves it against its cwd, outside --home)"
            continue
        target = str(Path(v).resolve())
        if not (target == str(home_res) or target.startswith(prefix)):
            refused[k] = v
    report = {"HOME": env["HOME"],
              **{k: env[k] for k in XDG_PINNED},
              "TMPDIR": env["TMPDIR"],
              "dropped": dropped, "drop_list": list(CACHE_DROPPED),
              "wizard_checked": checked, "wizard_exempt_read_only": exempt,
              "wizard_refused": refused}
    return env, report, refused


def _refuse_real_home(home: Path):
    try:
        import pwd
        real = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:  # noqa: BLE001 - no pwd on Windows; fall back to expanduser
        real = Path(os.path.expanduser("~")).resolve()
    if Path(home).resolve() == real:
        print("[diff] REFUSED: %s is the invoking user's real login home. F4: the harness runs "
              "against scratch homes only." % home, file=sys.stderr)
        return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb")
    s = sub.add_parser("snapshot")
    s.add_argument("--home", required=True); s.add_argument("--target-root", required=True)
    s.add_argument("--env-file"); s.add_argument("--out", required=True)
    d = sub.add_parser("diff")
    d.add_argument("--pre", required=True); d.add_argument("--post", required=True)
    d.add_argument("--declared", required=True); d.add_argument("--json", action="store_true")
    d.add_argument("--report")
    r = sub.add_parser("run")
    r.add_argument("--home", required=True); r.add_argument("--target-root", required=True)
    r.add_argument("--declared", required=True); r.add_argument("--env-file")
    r.add_argument("--report"); r.add_argument("--json", action="store_true")
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    v = sub.add_parser("verbs")
    v.add_argument("--declared", required=True); v.add_argument("--log", required=True)
    v.add_argument("--json", action="store_true"); v.add_argument("--report")
    a = ap.parse_args(argv)

    if a.verb == "verbs":
        try:
            declared = json.loads(Path(a.declared).read_text(encoding="utf-8"))
            log_lines = Path(a.log).read_text(encoding="utf-8").splitlines() if Path(a.log).is_file() else []
        except Exception as e:  # noqa: BLE001
            print("[verbs] unreadable input: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return RC_USAGE
        rep = grade_verbs(declared, log_lines)
        rep["log"] = str(a.log)
        if a.report:
            Path(a.report).write_text(json.dumps(rep, indent=1), encoding="utf-8")
        if a.json:
            print(json.dumps(rep, indent=1))
        else:
            print_verbs_report(rep)
        return rep["rc"]

    if a.verb == "snapshot":
        if _refuse_real_home(Path(a.home)):
            return RC_REFUSED
        snap, err = snapshot(Path(a.home), a.target_root, a.env_file)
        if err:
            print("[diff] " + err, file=sys.stderr); return RC_USAGE
        Path(a.out).write_text(json.dumps(snap, indent=1), encoding="utf-8")
        print("[diff] snapshot %s: %d paths under %s (excluding %s); named members present %d"
              % (a.out, snap["counts"]["wide_paths"], a.home, a.target_root, snap["counts"]["named_present"]))
        return RC_GREEN

    if a.verb in ("diff", "run"):
        try:
            declared = json.loads(Path(a.declared).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("[diff] declared write-set unreadable: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return RC_USAGE
        if a.verb == "diff":
            try:
                pre = json.loads(Path(a.pre).read_text(encoding="utf-8"))
                post = json.loads(Path(a.post).read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                print("[diff] snapshot unreadable: %s: %s" % (type(e).__name__, e), file=sys.stderr)
                return RC_USAGE
        else:
            if _refuse_real_home(Path(a.home)):
                return RC_REFUSED
            cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
            if not cmd:
                print("[diff] run: no command given after --", file=sys.stderr); return RC_USAGE
            # THE CHILD'S $HOME IS --home, PINNED HERE, whatever the caller's environment
            # says. Iteration 2 of round 1: a cold-gate script invoked `run --home <scratch>`
            # from a shell whose HOME was the operator's real one; the double expanded
            # $HOME itself and overwrote the REAL ~/.claude/settings.json and CLAUDE.md
            # (F4 breach, 2026-09-18). The instrument that grades a scratch home must
            # not hand its command a different one.
            # HOME is not the only variable a HOME-relative write follows (measured: a caller's
            # XDG_CONFIG_HOME outside --home let 16 unit files and 10 manager calls escape this
            # snapshot under a GREEN verdict). The XDG base dirs are PINNED under --home so every
            # default follows the pinned HOME; the model/wheel caches are dropped for the same
            # reason; the product's own path overrides are REFUSED unless they resolve under --home.
            child_env, env_report, outside = pinned_child_env(Path(a.home))
            if outside:
                print("[diff] REFUSED: these variables point the command OUTSIDE --home (%s): %s" % (
                    child_env["HOME"], ", ".join("%s=%s" % kv for kv in sorted(outside.items()))), file=sys.stderr)
                return RC_REFUSED
            # R5: the pinned TMPDIR exists BEFORE the pre-snapshot, so a child's mktemp lands
            # under --home and whatever it leaves there is graded. The instrument never creates
            # its own SUBJECT (reference-host round, iteration 2, item 6: `parents=True` built a
            # mistyped --home and every parent, then graded it UNMEASURABLE): the home must
            # already exist, and only its tmp/ is made.
            if not Path(a.home).is_dir():
                print("[diff] home is not a directory: %s" % a.home, file=sys.stderr); return RC_USAGE
            # A pinned base dir that resolves OUTSIDE --home through a symlink -- the dir itself or any parent,
            # existing or not yet created -- sends the child's writes outside the snapshot: GREEN with nothing
            # observed (reference-host round, iteration 3, item 7: measured, <home>/tmp -> outside, a child's mktemp
            # landed outside, GREEN 0 changes; the same through XDG_CACHE_HOME).
            _hr = str(Path(a.home).resolve()) + os.sep
            _esc = {k: child_env[k] for k in list(XDG_PINNED) + ["TMPDIR"]
                    if not (str(Path(child_env[k]).resolve()) + os.sep).startswith(_hr)}
            if _esc:
                print("[diff] REFUSED: pinned base dirs resolve OUTSIDE --home through a symlink: %s" % ", ".join(
                    "%s=%s -> %s" % (k, v, Path(v).resolve()) for k, v in sorted(_esc.items())), file=sys.stderr)
                return RC_REFUSED
            own = [child_env["TMPDIR"]]
            Path(child_env["TMPDIR"]).mkdir(exist_ok=True)   # the home exists; create only its tmp/
            pre, err = snapshot(Path(a.home), a.target_root, a.env_file, own)
            if err:
                print("[diff] " + err, file=sys.stderr); return RC_USAGE
            p = subprocess.run(cmd, env=child_env)
            post, _ = snapshot(Path(a.home), a.target_root, a.env_file, own)
            print("[diff] command exited rc=%d (HOME=%s): %s" % (p.returncode, child_env["HOME"], " ".join(cmd)),
                  file=sys.stderr if a.json else sys.stdout)
        rep = diff(pre, post, declared)
        if a.verb == "run":
            rep["command"] = cmd
            rep["command_rc"] = p.returncode
            rep["command_home"] = child_env["HOME"]
            rep["command_env_pinned"] = env_report
        if a.report:
            Path(a.report).write_text(json.dumps(rep, indent=1), encoding="utf-8")
        if a.json:
            print(json.dumps(rep, indent=1))
        else:
            print_report(rep)
        return rep["rc"]

    ap.print_help()
    return RC_USAGE


if __name__ == "__main__":
    sys.exit(main())

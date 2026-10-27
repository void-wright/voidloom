#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lay.py -- the ONE executor that lays the package onto a machine.

    python3 install/lay.py apply --manifest install/lay_manifest.json --platform posix \\
        --root brain=$HOME/.claude/memory --root skills=$HOME/.claude/skills --root home=$HOME/.claude \\
        [--component council] [--component forge] [--dry-run] [--json]

Stdlib only. Python >= 3.8. Runs unchanged on Windows (pathlib throughout; no
symlinks; exec bits are set on POSIX and ignored elsewhere).

WHAT IT DOES, IN ORDER -- and nothing is written until step 4
  1. reads the manifest (`tools/build_manifest.py --lay` emits it from
     `git ls-files --stage`; it is never hand-written) and validates its shape;
  2. selects the entries for THIS platform and the requested components, and
     refuses if a selected entry needs a root you did not supply;
  3. PRE-FLIGHT: every selected source exists under the package root and its
     bytes hash to what the manifest says -- a stale or tampered package is
     refused before a single byte lands (rc 3, every offender named);
  4. creates EVERY destination directory first, then copies every entry;
  5. re-hashes every laid file and refuses (rc 3) on any mismatch, naming it.

The package root is the manifest's grandparent (`<pkg>/install/lay_manifest.json`),
so a manifest that is not where the package is cannot lay the wrong tree
silently: it fails loudly at step 3 with the path it looked for.

EXIT CODES -- distinct, because a caller must be able to tell them apart
  0  laid and verified (or, with --dry-run, planned)
  2  usage / manifest unreadable or malformed / a needed root not supplied
  3  lay refused or failed: MISSING_SOURCE, SOURCE_MISMATCH, DEST_IS_DIR,
     DEST_IS_SYMLINK, DEST_PARENT_IS_SYMLINK, DEST_PARENT_IS_FILE, WRITE_FAILED
     or DEST_MISMATCH -- the report lists each with its class

TWO ENTRIES ARE NOT HASHED BY THE MANIFEST (`_meta.self_entries`): the manifest
itself and docs/PACKAGE_MANIFEST.md, which cannot hash each other without a
cycle. They are still laid and still verified -- against their SOURCE bytes --
and reported by name as `verified_against_source`.

WHAT IT REFUSES TO DO
  * skip anything silently: every entry not laid is counted and named by class
    (ships-not-laid, other-platform, other-component);
  * `rm -rf` anything: a destination that is a DIRECTORY (the D7-F1 shape,
    `voidloom/voidloom/`) is a refusal, not a repair;
  * write THROUGH a symlink: a destination that is a SYMLINK (to a file, a
    directory or nothing) is a refusal naming its target -- copying onto it
    would land the bytes wherever it points, outside the roots and unseen;
  * write BENEATH a symlinked directory: a symlink at any ancestor between the
    root and the destination is the same escape one level up (measured: a link
    at brain/organs let 66 files land outside the roots under rc 0), so every
    ancestor is checked in pre-flight and a link is refused naming its target;
    a regular FILE where a directory must be is refused too, instead of the
    uncaught error after a half-lay it used to be -- every ancestor BELOW a
    root. A root that is itself a symlink (or sits beneath one) is followed:
    the roots are the caller's declaration, made by install.sh from
    ${CLAUDE_HOME}, and are not second-guessed here;
  * read the manifest as advice: what it says is what is laid, no more and no
    less. Coverage is bound to the tree by voidloom/tests/test_lay_manifest.py,
    not here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOTS = ("brain", "skills", "home")
PLATFORMS = ("posix", "windows")
COMPONENTS = ("core", "council", "forge")
FIELDS = ("src", "root", "dst", "sha256", "bytes", "exec", "platform", "component")

RC_OK, RC_USAGE, RC_LAY = 0, 2, 3


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _die(msg: str, rc: int, as_json: bool):
    if as_json:
        print(json.dumps({"status": "refused", "rc": rc, "error": msg}, indent=1))
    else:
        print("[lay] REFUSED: " + msg, file=sys.stderr)
    return rc


def load_manifest(path: Path):
    """Return (doc, error). The shape is validated here so a malformed manifest
    is a usage error (rc 2), never a half-lay."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, "manifest unreadable: %s: %s: %s" % (path, type(e).__name__, e)
    entries = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(entries, list) or not entries:
        return None, ("manifest has no entries: %s (an empty manifest lays nothing and "
                      "is refused rather than passed)" % path)
    bad = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or any(k not in e for k in FIELDS):
            bad.append("entry %d lacks a field of %s" % (i, FIELDS))
            continue
        if e["root"] not in ROOTS and e["root"] is not None:
            bad.append("%s: root %r not in %s" % (e["src"], e["root"], ROOTS + (None,)))
        if e["platform"] not in ("all",) + PLATFORMS:
            bad.append("%s: platform %r" % (e["src"], e["platform"]))
        if e["component"] not in COMPONENTS:
            bad.append("%s: component %r" % (e["src"], e["component"]))
        if (e["root"] is None) != (e["dst"] is None):
            bad.append("%s: root and dst must both be null or both set" % e["src"])
        if e["dst"] is not None and (e["dst"].startswith("/") or ".." in e["dst"].split("/")):
            bad.append("%s: dst %r is not a safe relative path" % (e["src"], e["dst"]))
    if bad:
        return None, "manifest malformed (%d): %s" % (len(bad), "; ".join(bad[:8]))
    return doc, None


def parse_roots(pairs):
    roots = {}
    for pair in pairs or []:
        k, sep, v = pair.partition("=")
        if not sep or k not in ROOTS or not v:
            raise argparse.ArgumentTypeError("--root takes name=path with name in %s; got %r" % (ROOTS, pair))
        roots[k] = Path(v)
    return roots


def apply(args) -> int:
    as_json = args.json
    manifest = Path(args.manifest).resolve()
    doc, err = load_manifest(manifest)
    if err:
        return _die(err, RC_USAGE, as_json)
    try:
        roots = parse_roots(args.root)
    except argparse.ArgumentTypeError as e:
        return _die(str(e), RC_USAGE, as_json)
    components = set(["core"] + list(args.component or []))
    pkg_root = manifest.parent.parent

    # ---- 2. select -------------------------------------------------------
    selected, ships_not_laid, other_platform, other_component = [], [], [], []
    for e in doc["entries"]:
        if e["root"] is None:
            ships_not_laid.append(e["src"]); continue
        if e["platform"] not in ("all", args.platform):
            other_platform.append(e["src"]); continue
        if e["component"] not in components:
            other_component.append(e["src"]); continue
        selected.append(e)
    need = sorted({e["root"] for e in selected} - set(roots))
    if need:
        n = {r: sum(1 for e in selected if e["root"] == r) for r in need}
        return _die("root(s) needed by selected entries but not supplied: "
                    + ", ".join("%s (%d entries)" % (r, n[r]) for r in need)
                    + " -- pass --root <name>=<dir>; nothing was laid", RC_USAGE, as_json)
    if not selected:
        return _die("no entry selected for platform=%s components=%s -- refusing to "
                    "report an empty lay as success" % (args.platform, sorted(components)),
                    RC_LAY, as_json)

    # ---- 3. pre-flight: the package on disk IS what the manifest describes ---
    problems = []      # (class, src, dst, detail)
    plan = []          # (entry, src_path, dst_path)
    for e in selected:
        src = pkg_root / e["src"]
        dst = roots[e["root"]] / Path(e["dst"])
        if not src.is_file():
            problems.append(("MISSING_SOURCE", e["src"], str(dst), "expected at %s" % src))
            continue
        if e["sha256"] is not None:
            got = sha256_of(src)
            if got != e["sha256"]:
                problems.append(("SOURCE_MISMATCH", e["src"], str(dst),
                                 "manifest %s.. disk %s.." % (e["sha256"][:12], got[:12])))
                continue
        # Every ancestor between the root and dst must be a real directory or absent:
        # mkdir(parents=True) and copyfile FOLLOW a symlinked directory on the way
        # down, and the re-hash reads back through the same link, so the lay reports
        # 'verified' for bytes that landed outside the roots (round-1 iteration-2
        # reflection, item 5). Checked here, before anything is written.
        anc, link_anc, file_anc = roots[e["root"]], None, None
        for part in Path(e["dst"]).parts[:-1]:
            anc = anc / part
            if anc.is_symlink():
                link_anc = anc; break
            if anc.exists() and not anc.is_dir():
                file_anc = anc; break
        if link_anc is not None:
            problems.append(("DEST_PARENT_IS_SYMLINK", e["src"], str(dst),
                             "%s is a symlink to %s; refusing to write beneath it" % (link_anc, os.readlink(str(link_anc)))))
            continue
        if file_anc is not None:
            problems.append(("DEST_PARENT_IS_FILE", e["src"], str(dst), "%s exists and is not a directory" % file_anc))
            continue
        if dst.is_symlink():
            # `exists()`/`is_dir()` FOLLOW a symlink: a link at dst pointing at a file
            # (or dangling) passed pre-flight and copyfile wrote THROUGH it to wherever
            # it pointed -- a write outside the roots the snapshot-diff cannot see,
            # because the link sits inside the excluded target root (round-1
            # reflection, item 11). Refused, named, nothing written.
            problems.append(("DEST_IS_SYMLINK", e["src"], str(dst),
                             "destination is a symlink to %s; refusing to write through it"
                             % os.readlink(str(dst))))
            continue
        if dst.exists() and dst.is_dir():
            problems.append(("DEST_IS_DIR", e["src"], str(dst),
                             "destination is a directory; refusing to remove it"))
            continue
        plan.append((e, src, dst))

    report = {
        "status": "planned" if args.dry_run else "laid",
        "platform": args.platform,
        "components": sorted(components),
        "package_root": str(pkg_root),
        "manifest": str(manifest),
        "roots": {k: str(v) for k, v in roots.items()},
        "manifest_entries": len(doc["entries"]),
        "selected": len(selected),
        "ships_not_laid": ships_not_laid,
        "other_platform": other_platform,
        "other_component": other_component,
        "laid": 0,
        "verified": 0,
        "verified_against_source": [],
        "problems": [{"class": c, "src": s, "dst": d, "detail": t} for c, s, d, t in problems],
        "dirs_created": 0,
    }
    if problems:
        report["status"] = "refused"
        report["rc"] = RC_LAY
        _emit(report, as_json)
        return RC_LAY

    if args.dry_run:
        report["would_lay"] = [{"src": e["src"], "root": e["root"], "dst": str(d), "exec": e["exec"]}
                               for e, _s, d in plan]
        report["rc"] = RC_OK
        _emit(report, as_json)
        return RC_OK

    # ---- 4. every destination directory FIRST, then every copy ---------------
    dirs = sorted({d.parent for _e, _s, d in plan}, key=lambda p: len(p.parts))
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            # pre-flight checks the ancestors; a race or a permission still lands here
            # as a NAMED problem with the report emitted, never a traceback
            problems.append(("WRITE_FAILED", "-", str(d), "mkdir: %s: %s" % (type(ex).__name__, ex)))
    if problems:
        report["problems"] = [{"class": c, "src": s, "dst": d, "detail": t} for c, s, d, t in problems]
        report["status"] = "failed"
        report["rc"] = RC_LAY
        _emit(report, as_json)
        return RC_LAY
    report["dirs_created"] = len(dirs)   # ensured (mkdir -p); pre-existing ones count too
    for e, src, dst in plan:
        try:
            shutil.copyfile(str(src), str(dst))
            if e["exec"] and os.name == "posix":
                st = os.stat(str(dst))
                os.chmod(str(dst), st.st_mode | 0o111)
        except OSError as ex:
            problems.append(("WRITE_FAILED", e["src"], str(dst), "%s: %s" % (type(ex).__name__, ex)))
            continue
        report["laid"] += 1

    # ---- 5. re-hash every laid file ---------------------------------------
    for e, src, dst in plan:
        if not dst.is_file():
            continue            # already recorded as WRITE_FAILED
        got = sha256_of(dst)
        want = e["sha256"] if e["sha256"] is not None else sha256_of(src)
        if got != want:
            problems.append(("DEST_MISMATCH", e["src"], str(dst),
                             "expected %s.. got %s.." % (want[:12], got[:12])))
            continue
        report["verified"] += 1
        if e["sha256"] is None:
            report["verified_against_source"].append(e["src"])
    report["problems"] = [{"class": c, "src": s, "dst": d, "detail": t} for c, s, d, t in problems]
    if problems:
        report["status"] = "failed"
        report["rc"] = RC_LAY
        _emit(report, as_json)
        return RC_LAY
    report["rc"] = RC_OK
    _emit(report, as_json)
    return RC_OK


def _emit(report, as_json):
    if as_json:
        print(json.dumps(report, indent=1))
        return
    r = report
    print("[lay] %s: platform=%s components=%s package=%s" % (
        r["status"].upper(), r["platform"], ",".join(r["components"]), r["package_root"]))
    for k, v in r["roots"].items():
        print("[lay]   root %-6s -> %s" % (k, v))
    print("[lay]   manifest entries %d; selected %d; ships-not-laid %d; other-platform %d; "
          "other-component %d" % (r["manifest_entries"], r["selected"], len(r["ships_not_laid"]),
                                  len(r["other_platform"]), len(r["other_component"])))
    if r["ships_not_laid"]:
        print("[lay]   ships, not laid: " + ", ".join(r["ships_not_laid"]))
    if r["other_component"]:
        print("[lay]   other component (not requested): " + ", ".join(r["other_component"]))
    if r["other_platform"]:
        print("[lay]   other platform: %d files" % len(r["other_platform"]))
    # Problems are named INDIVIDUALLY, before any summary, on stderr: a refusal
    # that explains itself to /dev/null is the defect COLDGATE_SWEEP records.
    for p in r["problems"]:
        print("[lay]   %s: %s -> %s (%s)" % (p["class"], p["src"], p["dst"], p["detail"]),
              file=sys.stderr)
    if r["status"] == "planned":
        print("[lay]   DRY RUN: would ensure %d directories and lay %d files; nothing written"
              % (len({Path(w["dst"]).parent for w in r["would_lay"]}), len(r["would_lay"])))
    elif r["status"] == "refused":
        print("[lay] REFUSED before writing anything: %d problem(s) above; rc %d"
              % (len(r["problems"]), r["rc"]), file=sys.stderr)
    else:
        print("[lay]   destination directories ensured %d; laid %d; verified by sha256 %d (of which "
              "%d against their source bytes: %s)" % (r["dirs_created"], r["laid"], r["verified"],
                                                     len(r["verified_against_source"]),
                                                     ", ".join(r["verified_against_source"]) or "-"))
        if r["problems"]:
            print("[lay] FAILED: %d problem(s) above; rc %d" % (len(r["problems"]), r["rc"]),
                  file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb")
    a = sub.add_parser("apply", help="lay the manifest onto the given roots (or plan it with --dry-run)")
    a.add_argument("--manifest", default=str(Path(__file__).resolve().parent / "lay_manifest.json"),
                   help="the lay manifest (default: beside this script)")
    a.add_argument("--platform", required=True, choices=PLATFORMS)
    a.add_argument("--root", action="append", metavar="NAME=DIR",
                   help="destination for a root: brain=DIR, skills=DIR, home=DIR (repeatable)")
    a.add_argument("--component", action="append", choices=COMPONENTS,
                   help="lay this component in addition to core (repeatable)")
    a.add_argument("--dry-run", action="store_true", help="plan and verify sources; write nothing")
    a.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    args = ap.parse_args(argv)
    if args.verb != "apply":
        ap.print_help()
        return RC_USAGE
    return apply(args)


if __name__ == "__main__":
    sys.exit(main())

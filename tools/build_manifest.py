#!/usr/bin/env python3
"""Regenerate the package's DERIVED inventories from ONE read of the git index.

    python3 tools/build_manifest.py                 # both: install/lay_manifest.json, then docs/PACKAGE_MANIFEST.md
    python3 tools/build_manifest.py --lay           # install/lay_manifest.json only  (the installer's lay table)
    python3 tools/build_manifest.py --package       # docs/PACKAGE_MANIFEST.md only   (the human-readable inventory)
    python3 tools/build_manifest.py --lay --check   # re-derive and COMPARE to the committed lay manifest; writes NOTHING
                                                    #   rc 0 clean · 4 absent · 5 stale (every difference named)

WHY THIS REPLACED THE PREVIOUS GENERATOR
----------------------------------------
The old builder kept an EXCLUDE set naming the round artifacts that must not
ship, printed "excluded by name (local-only): 7", and wrote that line into the
manifest. Measured 2026-09-07 against the delivered package: **8 of those files
were inside the shipped zip.** The declaration was a true sentence and a false
fact, because nothing bound it to the mechanism that laid the files down --
`install.sh` globbed `${PKG_ROOT}/*.py` and the zip builder walked the tree.

So the exclusion list is gone. The **git index is now the declaration AND the
mechanism**: a file that must not ship is not tracked, `git ls-files` cannot
report it, and every downstream step (installer, zip, this manifest) reads the
same source. One list, no drift, and "excluded" is enforced rather than stated.

THE LAY MANIFEST (Council #12, DESSA's design, 2026-09-18)
----------------------------------------------------------
`install/lay_manifest.json` is the ONLY description of where each tracked file
lands on an installed machine. `install/lay.py` executes it; `install.sh` Step A
is one line calling `lay.py`. There is no directory list in any installer.

Every entry carries: `src` (as `git ls-files` prints it), `root` (brain | skills |
home | null -- null = the file SHIPS but is NOT laid: the five front-door files),
`dst` (relative to that root), `sha256`, `bytes`, `exec` (git mode 100755),
`platform` (all | posix | windows) and `component` (core | council | forge). Council
entries are laid only when the executor is told `--component council`, so
`install/install_council.sh`'s never-clobber contract survives. Forge entries (the
Persona Forge skill + /persona-forge) are laid by `install/install_persona_forge.sh`,
which install.sh CALLS after its acceptance gate -- under the same never-clobber
contract -- and by install.ps1 through `--component forge`.

Read from `git ls-files --stage -z`, and REFUSED (rc 2) if any entry sits at a
merge stage other than 0 -- memory #20132: a half-merged index reports 301 paths
for a tree of 297, and a manifest built from it lays files that do not exist.
Also refused: symlinks (mode 120000) and submodules (160000), which `lay.py`
cannot lay portably, and tracked files absent from the working tree. BOTH
emitters (`--lay` and `--package`) share that guard (`index_problems`), so
`--package` alone cannot write a package manifest from a half-merged index.

`_meta.derived_at_head` records the HEAD the manifest was derived at: once the
manifest is committed that is the parent of the carrying commit, by construction,
and `--check` ignores the field -- it is a provenance note, not a binding.

TWO ENTRIES CARRY `sha256: null`, BY NAME AND FOR A REASON
----------------------------------------------------------
A manifest cannot contain its own hash. Nor can the two derived inventories hash
EACH OTHER: `docs/PACKAGE_MANIFEST.md` lists the hash of `install/lay_manifest.json`,
so the lay manifest cannot pin the package manifest's bytes without a cycle. Both
are listed as `self_entries` in `_meta`, `lay.py` lays them and reports them as
unhashed by name, and `voidloom/tests/test_lay_manifest.py` asserts the null set
is EXACTLY those two -- an exclusion that is enumerated, never merely stated.

Run from OUTSIDE the tree so no __pycache__ is written into what is measured:
    python3 /path/to/voidloom/tools/build_manifest.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LAY_MANIFEST_REL = "install/lay_manifest.json"
PACKAGE_MANIFEST_REL = "docs/PACKAGE_MANIFEST.md"
# The two derived inventories: neither can carry its own hash, and neither can
# carry the other's (see the docstring). Enumerated here, asserted by the test.
SELF_ENTRIES = (LAY_MANIFEST_REL, PACKAGE_MANIFEST_REL)

ROOTS = ("brain", "skills", "home")          # plus None: ships, not laid
PLATFORMS = ("all", "posix", "windows")
COMPONENTS = ("core", "council", "forge")

SUBSYSTEM = {
    "organs": "The organism — engine + L2-L5 organ suite",
    "voidloom": "The CLI facade",
    "migrations": "Schema + additive migrations",
    "install": "Installers, wiring, acceptance gate, pinned requirements",
    "seed": "Starter memories + schema-only blank DB",
    "hooks": "Host session bindings",
    "cron": "systemd units + timers",
    "capability": "Host capability detection",
    "persona": "Persona skills, council voices, slash commands",
    "tools": "Maintainer-only: package gates + recall harness",
    "docs": "Documentation",
    "Brain_Sync": "Optional second-machine mirror",
    "(root)": "Front door: README, licence, installer, manifest",
}


# ---------------------------------------------------------------------------
# DEVELOPER HISTORY: KEPT OUT OF THE PUBLIC TREE
# ---------------------------------------------------------------------------
# The repair records of this brain's own development (docs/cycle1/, docs/cycle2/) shipped in the private repository
# until the public release. They name the original owner's machines and engagements throughout, so they stay in the
# private archive and are refused here: a tracked file under HISTORY_DIRS makes the derivation REFUSE, so a record
# cannot slip back into the public tree by accident. HISTORY_SHIPS_NOT_LAID is kept (empty) for the lay rule below.
HISTORY_DIRS = ("docs/cycle1/", "docs/cycle2/")
HISTORY_SHIPS_NOT_LAID = frozenset()


def history_problems(paths):
    """Refusals: any tracked file in a developer-history directory (those records stay in the private archive)."""
    found = sorted(p for p in set(paths) if p.startswith(HISTORY_DIRS))
    if found:
        return ["REFUSED: %d developer-history file(s) are tracked; they stay in the private archive, not the public "
                "tree: %s" % (len(found), ", ".join(found))]
    return []


# ---------------------------------------------------------------------------
# THE LAY RULE -- the one function that decides where a tracked file lands.
# ---------------------------------------------------------------------------
def platform_of(src: str) -> str:
    """all | posix | windows, from the file's own nature (#4936: assume nothing shared)."""
    if src.endswith((".ps1", ".cmd")):
        return "windows"
    if src.endswith(".sh") or src.startswith("cron/"):
        return "posix"
    if src == "voidloom/voidloom":          # the POSIX shell shim; voidloom.cmd is its twin
        return "posix"
    return "all"


def lay_rule(src: str):
    """(root, dst, platform, component) for one `git ls-files` path.

    root None means the file ships but is not laid -- the front door files that
    live at the repository root (README, LICENSE, NOTICE, install.sh, .gitignore),
    and the developer history NAMED in HISTORY_SHIPS_NOT_LAID (round 2, M4).
    Laying install.sh INTO the brain directory would invite running it from
    there, which copies the brain onto itself; a .gitignore in a user's
    directory is a side effect nobody asked for. So: shipped, not laid.
    """
    plat = platform_of(src)
    if "/" not in src:
        return None, None, plat, "core"
    if src in HISTORY_SHIPS_NOT_LAID:          # by name (round 2, M4): shipped, not laid
        return None, None, plat, "core"
    if src.startswith("persona/skills/persona-forge/"):
        return "skills", src[len("persona/skills/"):], plat, "forge"
    if src.startswith("persona/skills/"):
        return "skills", src[len("persona/skills/"):], plat, "core"
    if src.startswith("persona/council_skills/"):
        return "skills", src[len("persona/council_skills/"):], plat, "council"
    if src.startswith("persona/commands/"):
        name = src[len("persona/commands/"):]
        comp = ("council" if name == "council.md" else
                "forge" if name == "persona-forge.md" else "core")
        return "home", "commands/" + name, plat, comp
    return "brain", src, plat, "core"


# ---------------------------------------------------------------------------
# ONE read of the index
# ---------------------------------------------------------------------------
def read_index(root: Path):
    """Return (rows, refusals). rows: list of (mode, sha, stage, path)."""
    out = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=str(root),
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None, ["git ls-files --stage failed (is this a checkout?): "
                      + out.stderr.strip()[:200]]
    rows, refusals = [], []
    for rec in out.stdout.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not path:
            refusals.append("unparseable index record: %r" % rec[:120])
            continue
        mode, sha, stage = parts[0], parts[1], int(parts[2])
        rows.append((mode, sha, stage, path))
    if not rows:
        refusals.append("git ls-files --stage returned no entries -- an empty "
                        "population is a refusal, never a pass")
    return rows, refusals


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_problems(rows):
    """The refusals BOTH emitters share (#20132): a merge stage, a mode lay.py cannot lay.

    Round-1 reflection, item 4: only the lay emitter carried this guard, and
    `--package` alone on a tree with NOTICE at stages 2 and 3 wrote a package
    manifest saying '312 files' for a 311-file tree -- the exact shape the module
    docstring says is refused. One function, called by both readers of the index.
    """
    out = []
    staged = [(s, p) for (_m, _s, s, p) in rows if s != 0]
    if staged:
        out.append("REFUSED: %d index entries at merge stage != 0 (#20132): %s"
                   % (len(staged), ", ".join("%s@%d" % (p, s) for s, p in staged[:10])))
    bad_mode = [(m, p) for (m, _s, _st, p) in rows if m not in ("100644", "100755")]
    if bad_mode:
        out.append("REFUSED: %d entries with a mode lay.py cannot lay (symlink/submodule): %s"
                   % (len(bad_mode), ", ".join("%s %s" % (m, p) for m, p in bad_mode[:10])))
    return out


def derive_lay_entries(root: Path):
    """The lay manifest's entries, derived from the index. (entries, refusals, head)."""
    rows, refusals = read_index(root)
    if rows is None:
        return None, refusals, None
    refusals += index_problems(rows)
    refusals += history_problems(p for (_m, _s, _st, p) in rows)
    missing = [p for (_m, _s, _st, p) in rows if not (root / p).is_file()]
    if missing:
        refusals.append("REFUSED: %d tracked files absent from the working tree: %s"
                        % (len(missing), ", ".join(missing[:10])))
    if refusals:
        return None, refusals, None

    entries = []
    seen_dst = defaultdict(list)
    for mode, _sha, _stage, path in sorted(rows, key=lambda r: r[3]):
        r, dst, plat, comp = lay_rule(path)
        is_self = path in SELF_ENTRIES
        p = root / path
        entries.append({
            "src": path,
            "root": r,
            "dst": dst,
            "sha256": None if is_self else sha256_of(p),
            "bytes": None if is_self else p.stat().st_size,
            "exec": mode == "100755",
            "platform": plat,
            "component": comp,
        })
        if r is not None:
            seen_dst[(r, dst)].append(path)
    collide = {k: v for k, v in seen_dst.items() if len(v) > 1}
    if collide:
        refusals.append("REFUSED: destination collision: " + "; ".join(
            "%s:%s <- %s" % (k[0], k[1], ", ".join(v)) for k, v in collide.items()))
        return None, refusals, None
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
                          capture_output=True, text=True).stdout.strip()
    return entries, [], head


def lay_manifest_doc(entries, head):
    counts = {
        "by_root": {}, "by_platform": {}, "by_component": {},
    }
    for e in entries:
        k = "null" if e["root"] is None else e["root"]
        counts["by_root"][k] = counts["by_root"].get(k, 0) + 1
        counts["by_platform"][e["platform"]] = counts["by_platform"].get(e["platform"], 0) + 1
        counts["by_component"][e["component"]] = counts["by_component"].get(e["component"], 0) + 1
    return {
        "_meta": {
            "doc": "DERIVED by tools/build_manifest.py --lay from `git ls-files --stage`; "
                   "never hand-edited. Executed by install/lay.py. Bound to the tree by "
                   "voidloom/tests/test_lay_manifest.py. derived_at_head is the HEAD at "
                   "derivation time -- the PARENT of the commit that carries this file, never "
                   "that commit (a file cannot name the commit it is part of); --check ignores it.",
            "derived_at_head": head,
            "entries": len(entries),
            "counts": counts,
            "roots": list(ROOTS) + [None],
            "platforms": list(PLATFORMS),
            "components": list(COMPONENTS),
            "self_entries": list(SELF_ENTRIES),
            "self_entries_reason": "a manifest cannot carry its own hash, and the two derived "
                                   "inventories cannot hash each other without a cycle; "
                                   "lay.py lays both and reports them unhashed by name",
        },
        "entries": entries,
    }


def render_lay_manifest(doc) -> str:
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# the package manifest (human-readable), unchanged in substance
# ---------------------------------------------------------------------------
def write_package_manifest(root: Path) -> int:
    rows_idx, refusals = read_index(root)
    if rows_idx is None:
        for r in refusals:
            print(r, file=sys.stderr)
        return 2
    refusals = index_problems(rows_idx)          # the same guard as --lay; writes NOTHING on a refusal
    if refusals:
        for r in refusals:
            print(r, file=sys.stderr)
        return 2
    files = sorted(p for (_m, _s, _st, p) in rows_idx)

    rows, missing, total = [], [], 0
    for rel in files:
        p = root / rel
        if not p.is_file():
            missing.append(rel)
            continue
        b = p.read_bytes()
        total += len(b)
        rows.append((rel, len(b), hashlib.sha256(b).hexdigest()))

    groups = defaultdict(list)
    for rel, n, h in rows:
        groups[rel.split("/")[0] if "/" in rel else "(root)"].append((rel, n, h))

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          cwd=str(root), capture_output=True, text=True).stdout.strip()
    L = [
        "# PACKAGE_MANIFEST — the inventory of what ships",
        "",
        f"> Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from the checkout at `{head}`.",
        "> **The git index is the declaration.** There is no second exclusion list to",
        "> drift from it: a file that must not ship is untracked, so it cannot appear",
        "> here, in the installer's copy set, or in a package. The previous generator",
        "> kept a parallel EXCLUDE set and 8 of the 7 files it named shipped anyway.",
        "> Where each file LANDS on an installed machine is `install/lay_manifest.json`,",
        "> derived from the same index by the same tool (`--lay`).",
        "",
        "| | |", "|---|---|",
        f"| files tracked | {len(files)} |",
        f"| files hashed | {len(rows)} |",
        f"| total bytes | {total} |",
        f"| tracked but ABSENT on disk | {len(missing)} |",
        "",
    ]
    if missing:
        L += ["## Tracked but missing on disk", ""] + [f"* `{m}`" for m in missing] + [""]
    L += ["## What is in the package, by subsystem", ""]
    for key in list(SUBSYSTEM) + sorted(set(groups) - set(SUBSYSTEM)):
        if key not in groups:
            continue
        g = groups[key]
        L += [f"### `{key}` — {SUBSYSTEM.get(key, '')}  ·  {len(g)} files, "
              f"{sum(n for _, n, _ in g)} bytes", "",
              "| file | bytes | sha256 |", "|---|---:|---|"]
        L += [f"| `{rel}` | {n} | `{h[:16]}` |" for rel, n, h in g]
        L += [""]
    (root / PACKAGE_MANIFEST_REL).write_text("\n".join(L))
    print(f"{PACKAGE_MANIFEST_REL}: {len(rows)} files, {total} bytes, "
          f"{len(missing)} missing")
    return 1 if missing else 0


# ---------------------------------------------------------------------------
# the lay manifest
# ---------------------------------------------------------------------------
def write_lay_manifest(root: Path) -> int:
    entries, refusals, head = derive_lay_entries(root)
    if entries is None:
        for r in refusals:
            print(r, file=sys.stderr)
        return 2
    doc = lay_manifest_doc(entries, head)
    (root / LAY_MANIFEST_REL).write_text(render_lay_manifest(doc), encoding="utf-8")
    c = doc["_meta"]["counts"]
    print(f"{LAY_MANIFEST_REL}: {len(entries)} entries from git ls-files --stage at {head}; "
          f"roots={c['by_root']} platforms={c['by_platform']} components={c['by_component']}; "
          f"self_entries={list(SELF_ENTRIES)}")
    return 0


def check_lay_manifest(root: Path) -> int:
    """Re-derive and compare. rc 0 clean, 4 absent, 5 stale. Writes nothing."""
    man = root / LAY_MANIFEST_REL
    if not man.is_file():
        print(f"ABSENT: {LAY_MANIFEST_REL} is not in the tree", file=sys.stderr)
        return 4
    try:
        committed = json.loads(man.read_text(encoding="utf-8"))
        c_entries = {e["src"]: e for e in committed["entries"]}
    except Exception as e:  # noqa: BLE001 - a broken manifest is a refusal, named
        print(f"UNREADABLE: {LAY_MANIFEST_REL}: {type(e).__name__}: {e}", file=sys.stderr)
        return 5
    entries, refusals, _head = derive_lay_entries(root)
    if entries is None:
        for r in refusals:
            print(r, file=sys.stderr)
        return 2
    d_entries = {e["src"]: e for e in entries}
    only_manifest = sorted(set(c_entries) - set(d_entries))
    only_tree = sorted(set(d_entries) - set(c_entries))
    differ = []
    for src in sorted(set(c_entries) & set(d_entries)):
        a, b = c_entries[src], d_entries[src]
        fields = [k for k in ("root", "dst", "sha256", "bytes", "exec", "platform", "component")
                  if a.get(k) != b.get(k)]
        if fields:
            differ.append("%s (%s)" % (src, ",".join(fields)))
    if only_manifest or only_tree or differ:
        print("STALE: %s disagrees with git ls-files --stage: only_in_manifest=%d %s; "
              "only_in_tree=%d %s; differing=%d %s" % (
                  LAY_MANIFEST_REL, len(only_manifest), only_manifest[:10],
                  len(only_tree), only_tree[:10], len(differ), differ[:10]),
              file=sys.stderr)
        return 5
    print(f"CLEAN: {LAY_MANIFEST_REL} matches git ls-files --stage: {len(entries)} entries")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lay", action="store_true", help="emit install/lay_manifest.json")
    ap.add_argument("--package", action="store_true", help="emit docs/PACKAGE_MANIFEST.md")
    ap.add_argument("--check", action="store_true",
                    help="with --lay: re-derive and compare; write nothing (0 clean, 4 absent, 5 stale)")
    a = ap.parse_args(argv)
    if a.check:
        if not a.lay:
            ap.error("--check applies to --lay")
        return check_lay_manifest(ROOT)
    do_lay = a.lay or not a.package
    do_pkg = a.package or not a.lay
    rc = 0
    if do_lay:                       # FIRST: the package manifest hashes this file
        rc = max(rc, write_lay_manifest(ROOT))
        if rc:
            return rc
    if do_pkg:
        rc = max(rc, write_package_manifest(ROOT))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

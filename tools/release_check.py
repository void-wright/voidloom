#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE RELEASE PATH — run every gate this package owns and PRINT what each found.

WHY THIS EXISTS, STATED AS THE FAILURE IT ENDS
-----------------------------------------------
Crucible-3 D1-F1: **both shipped release gates were wired to nothing, and both
were RED against the tree they guard.** `tools/gpu_pin_gate.py` and
`tools/pii_gate.py` had, between them, exactly two references outside their own
files: one row in the package manifest listing them as files, and one comment.
Nothing executed them. D1-F3: `docs/PACKAGE_MANIFEST.md` contradicted the tree it
inventories -- 41 of 194 hashes wrong at HEAD -- because `tools/build_manifest.py`
likewise had no caller. A rule that reads correctly and executes never is the
UNBOUND class, and it was 7 of this run's 57 blocking findings.

There is a second, quieter reason they were never wired: **they could not finish.**
Both walk `os.walk(followlinks=True)` from the repo root -- correctly, because a
gate's population must come from the mechanism that LAYS the artifact and
`git ls-files` returns nothing in a .git-less tarball -- and from the brain root
that walk covers 16,378 files and 3.5 GB of `archives/` and `ACE_Workspaces/`.
Measured before the cycle-2 repair: **rc 124 at 900 seconds, both gates.** A gate
nobody can run is a gate nobody wires.

REPORTING, NOT BLOCKING -- THIS CYCLE, DELIBERATELY
----------------------------------------------------
This script exits 0 whatever the gates find. That is a decision, not an oversight:
wiring a red gate to block on day one, over a tree still under repair, creates
exactly one incentive -- to make CI green by adjusting the GATE rather than the
PRODUCT. Report first, with every red naming its finding; flip `--strict` on at
the end of the repair cycle, when the reds are the ones we chose to keep.

    python3 tools/release_check.py            # report; always exit 0
    python3 tools/release_check.py --strict   # exit 1 if any gate is red
    python3 tools/release_check.py --json

GPU: none. NETWORK: none. Writes nothing (the manifest check RE-DERIVES and
compares; it does not regenerate -- `tools/build_manifest.py` does that).
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _run(cmd, timeout=600):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "wall_s": round(time.time() - t0, 1),
                "stdout": p.stdout, "stderr": p.stderr, "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "wall_s": round(time.time() - t0, 1),
                "stdout": "", "stderr": f"TIMED OUT after {timeout}s", "timed_out": True}


def gate(name: str, script: str) -> dict:
    r = _run([sys.executable, str(HERE / script), "--json"])
    out = {"gate": name, "script": script, "rc": r["rc"], "wall_s": r["wall_s"],
           "timed_out": r["timed_out"]}
    try:
        d = json.loads(r["stdout"][r["stdout"].index("{"):])
        out["population"] = d.get("population")
        out["flagged_files"] = d.get("files")
        out["flagged"] = sorted((d.get("detail") or {}).keys())
        # A GATE THAT SCANNED NOTHING HAS NOT PASSED. Its own envelope says so.
        out["empty_population"] = bool(d.get("empty_population"))
    except Exception as e:
        out["envelope_error"] = f"{type(e).__name__}: {e}"
    out["verdict"] = ("UNRUNNABLE" if r["timed_out"] or "envelope_error" in out else
                      "VACUOUS" if out.get("empty_population") else
                      "CLEAN" if r["rc"] == 0 else "RED")
    return out


def manifest_check() -> dict:
    """Re-derive every hash the manifest pins and compare. Does not rewrite."""
    man = ROOT / "docs" / "PACKAGE_MANIFEST.md"
    out = {"gate": "package_manifest", "script": "docs/PACKAGE_MANIFEST.md"}
    if not man.exists():
        return {**out, "verdict": "UNRUNNABLE", "reason": "manifest absent"}
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]{8,})`",
                      man.read_text(errors="replace"), re.M)
    wrong, missing = [], []
    # A MANIFEST CANNOT CONTAIN ITS OWN HASH: writing the hash changes the bytes
    # whose hash it is. Reporting that as a defect every single run is a permanent
    # false red, and a permanent false red is how a gate teaches people to ignore it.
    # It is EXCLUDED BY NAME, with the reason, and named in the envelope so the
    # exclusion is visible rather than silent.
    self_ref = "docs/PACKAGE_MANIFEST.md"
    out["self_reference_excluded"] = self_ref
    for rel, size, h in rows:
        if rel == self_ref:
            continue
        f = ROOT / rel
        if not f.exists():
            missing.append(rel); continue
        b = f.read_bytes()
        if hashlib.sha256(b).hexdigest()[:len(h)] != h or len(b) != int(size):
            wrong.append(rel)
    # DERIVE THE SHIPPED POPULATION AND ASSERT ITS SIZE.
    #
    # This loop above walks the MANIFEST'S OWN ROWS, so it can only ever grade
    # files the manifest already knows about. D9-F3 was two defects: 40 wrong
    # hashes AND 45 files shipped with no row at all -- and only the first half
    # was visible here. A gate whose population is the artifact under test cannot
    # report an omission from that artifact; it is the "list is the evidence"
    # rule pointed at a manifest. I re-committed it myself one cut earlier by
    # regenerating BEFORE staging, and this check called that tree CLEAN.
    #
    # git ls-files is the delivery population (#7976: a CURATED list, never a
    # glob). If git is unavailable the population is UNKNOWN -- reported as such,
    # never silently treated as empty.
    unlisted, population = [], None
    try:
        import subprocess
        res = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            tracked = {ln for ln in res.stdout.split("\n") if ln.strip()}
            population = len(tracked)
            listed = {rel for rel, _s, _h in rows}
            unlisted = sorted(tracked - listed)
    except Exception as e:
        out["population_error"] = f"{type(e).__name__}: {e}"

    out.update({"rows": len(rows), "wrong": len(wrong), "missing": len(missing),
                "tracked": population,
                "unlisted": (len(unlisted) if population is not None else None),
                "flagged": sorted(wrong + missing + unlisted)[:40],
                "regenerate_with": "python3 tools/build_manifest.py"})
    out["verdict"] = ("VACUOUS" if not rows else
                      "CLEAN" if not wrong and not missing and not unlisted else "RED")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any gate is RED / VACUOUS / UNRUNNABLE")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    results = [gate("gpu_pin", "gpu_pin_gate.py"),
               gate("pii", "pii_gate.py"),
               manifest_check()]
    if a.json:
        print(json.dumps({"root": str(ROOT), "strict": a.strict,
                          "results": results}, indent=1))
    else:
        print(f"\nRELEASE CHECK — {ROOT}\n" + "=" * 64)
        for r in results:
            print(f"  {r['gate']:<18} {r['verdict']:<11} "
                  + (f"rc={r.get('rc')} " if "rc" in r else "")
                  + (f"{r.get('wall_s')}s " if r.get("wall_s") is not None else "")
                  + (f"population={r['population']} " if r.get("population") else "")
                  + (f"rows={r['rows']} wrong={r['wrong']} missing={r['missing']}"
                     if "rows" in r else ""))
            for f in (r.get("flagged") or [])[:12]:
                print(f"      · {f}")
            if r.get("regenerate_with") and r["verdict"] == "RED":
                print(f"      fix: {r['regenerate_with']}")
        bad = [r for r in results if r["verdict"] != "CLEAN"]
        print("=" * 64)
        print(f"  {len(results) - len(bad)} clean, {len(bad)} not clean."
              + ("  (reporting only — pass --strict to make this block)"
                 if not a.strict else ""))
        print()
    if a.strict:
        return 1 if any(r["verdict"] != "CLEAN" for r in results) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

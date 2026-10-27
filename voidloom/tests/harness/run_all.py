#!/usr/bin/env python3
"""Drive every slow release gate in one command.

N3 (Crucible D02): the harnesses existed, were good, and were invoked by a hand.
`pytest` now binds the fast surface; this binds the slow one, so "run the gates"
is a command rather than a memory.

    python3 voidloom/tests/harness/run_all.py [--list] [--only NAME] [package_dir]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRAIN = HERE.parent.parent.parent

GATES = [
    ("property_battery_16_install", "33 filesystem shapes across both writers"),
    ("c6_anchor_suite", "window anchors and persisted counts"),
    ("c7_writer_and_claim_suite", "the doctrine writer and the scoped claim"),
    ("c4_planner_applier_suite", "structural acceptance, rollback arms, strict JSON"),
    ("c5_self_report_suite", "the verdict matches the row beside it"),
    ("c3_one_installer_suite", "one installer, and the Tier-0 path"),
    ("c2_install_schema_suite", "the installer builds a brain its package can read"),
    ("cli_install_suite", "the 89-check CLI/install contract"),
]


#: Harnesses deliberately NOT driven here, each WITH ITS REASON. An exclusion
#: without a reason is a place to hide; these are printed by --list and by the
#: coverage line, so a reader can audit them one by one.
NOT_DRIVEN = {
    "run_all": "this driver itself",
    "mutation_matrix": ("a mutation BATTERY: it deliberately breaks the tree to check "
                        "the checks, so it is run by hand against a copy, never as part "
                        "of a release sweep that must leave the tree as it found it"),
    "canary_r8_notice": ("a one-shot canary for a specific historical round; kept as "
                         "evidence, not as a standing gate"),
    "outside_target_diff": ("an INSTRUMENT, not a gate: it wraps a run (`run --home H "
                            "--declared D -- cmd`) and grades the $HOME diff against the "
                            "installer's declared write-set, and (`verbs`) grades a recorder "
                            "shim's log of scheduler calls against the declared verbs; it "
                            "takes no <package_dir> <scratch_root>. Driven by the round-1 "
                            "cold gate and by voidloom/tests/test_outside_target_diff.py"),
}


def coverage() -> dict:
    """Every harness on disk, and whether anything drives it.

    CRUCIBLE-3 D01-F8: `mutation_matrix.py` and `canary_r8_notice.py` were TABLED
    AS GATES in harness/README.md and named by no runner -- the UNBOUND class, in
    the file whose own docstring says it exists so that "run the gates" is a
    command rather than a memory. The cause is the one this cycle keeps finding:
    GATES is a TYPED LIST, and a typed list goes stale the day the directory
    grows. So the population is DERIVED here and compared, and anything neither
    driven nor explicitly excused is reported as UNDRIVEN rather than forgotten.
    """
    on_disk = {f.stem for f in HERE.glob("*.py")}
    driven = {name for name, _ in GATES}
    excused = set(NOT_DRIVEN)
    return {"on_disk": sorted(on_disk), "driven": sorted(driven),
            "excused": {k: NOT_DRIVEN[k] for k in sorted(excused & on_disk)},
            "undriven": sorted(on_disk - driven - excused)}


def main(argv: list[str]) -> int:
    cov = coverage()
    if cov["undriven"]:
        print("  ⚠ HARNESSES ON DISK THAT NOTHING DRIVES AND NOTHING EXCUSES:")
        for n in cov["undriven"]:
            print(f"      {n}.py — add it to GATES, or to NOT_DRIVEN with its reason")
    if "--coverage" in argv:
        print(json.dumps(cov, indent=1))
        return 1 if cov["undriven"] else 0
    if "--list" in argv:
        print(f"  driven {len(cov['driven'])} of {len(cov['on_disk'])} harnesses on disk; "
              f"{len(cov['excused'])} excused, {len(cov['undriven'])} undriven")
        for name, why in NOT_DRIVEN.items():
            print(f"  [not driven] {name:<24} {why}")
        for name, what in GATES:
            print(f"  {name:<34} {what}")
        return 0
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    pkg = next((a for a in argv[1:] if not a.startswith("--")
                and a not in (only,)), str(BRAIN))
    root = Path(os.environ.get("VOIDLOOM_GATE_SCRATCH",
                               str(BRAIN / ".gate_runs" / time.strftime("%Y%m%dT%H%M%S"))))
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for name, what in GATES:
        if only and only not in name:
            continue
        script = HERE / f"{name}.py"
        if not script.is_file():
            results.append((name, None, "ABSENT"))
            print(f"  {name:<34} ABSENT")
            continue
        scratch = root / name
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        t0 = time.time()
        p = subprocess.run([sys.executable, str(script), pkg, str(scratch)],
                           capture_output=True, text=True, timeout=5400,
                           env={**os.environ, "PYTHONPATH": pkg})
        tail = [l for l in (p.stdout or "").splitlines() if "CHECKS HELD" in l or "SHAPES HELD" in l]
        line = tail[-1].strip() if tail else f"rc={p.returncode}"
        results.append((name, p.returncode, line))
        print(f"  {name:<34} {'OK ' if p.returncode == 0 else 'RED'}  {line}  ({time.time()-t0:.0f}s)")
    bad = [n for n, rc, _ in results if rc != 0]
    print(json.dumps({"scratch_root": str(root), "gates": len(results),
                      "red": bad}, indent=1))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

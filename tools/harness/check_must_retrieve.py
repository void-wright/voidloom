#!/usr/bin/env python3
"""
check_must_retrieve.py -- the cap-floor checker (L5-B mandate).

Given a recall snapshot and a must_retrieve.json annotation, assert that every
declared must-retrieve memory id is present in the corresponding query's top-k.
A missing must-retrieve id is a DIRECT relevance drop -- the strongest possible
no-regression signal (it catches a lost *relevant* memory even if total
set-overlap looks fine).

Stdlib only. Never raises on malformed input -- reports it.
"""

import argparse
import json
import sys


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as e:
        return None, str(e)


def check(snapshot, must_map):
    """snapshot: {"snapshots": {q: [ids]}} or {q: [ids]}.
       must_map: {q: [required ids]}.
       Returns (rows: list, summary: dict)."""
    if isinstance(snapshot, dict) and "snapshots" in snapshot:
        snaps = snapshot.get("snapshots") or {}
    else:
        snaps = snapshot or {}

    rows = []
    misses = 0
    for q, required in (must_map or {}).items():
        got = snaps.get(q)
        if got is None:
            rows.append({"query": q, "required": required, "present": [],
                         "missing": list(required), "status": "QUERY-ABSENT"})
            misses += len(required)
            continue
        got_set = set(int(x) for x in got if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit())
        present = [r for r in required if r in got_set]
        missing = [r for r in required if r not in got_set]
        rows.append({
            "query": q,
            "required": required,
            "present": present,
            "missing": missing,
            "status": "OK" if not missing else "FLOOR-BREACH",
        })
        misses += len(missing)

    summary = {
        "checked": len(rows),
        "total_misses": misses,
        # An empty map is UNKNOWN, not clean: a floor with nothing under it measures nothing.
        "verdict": ("NOT-CHECKED (no must-retrieve anchors declared)" if not must_map
                    else "PASS" if misses == 0 else "FAIL"),
    }
    return rows, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cap-floor must-retrieve checker.")
    ap.add_argument("snapshot", help="recall snapshot JSON")
    ap.add_argument("must", help="must_retrieve.json annotation")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    snap, e1 = load_json(args.snapshot)
    mr, e2 = load_json(args.must)
    if e1 or e2:
        print(json.dumps({"status": "error", "snapshot_err": e1, "must_err": e2}))
        return 2

    must_map = mr.get("must_retrieve", mr) if isinstance(mr, dict) else {}
    rows, summary = check(snap, must_map)

    if args.json:
        print(json.dumps({"status": "ok", "summary": summary, "results": rows}, indent=2))
    else:
        print("CAP-FLOOR MUST-RETRIEVE CHECK")
        for r in rows:
            mark = "ok " if r["status"] == "OK" else "!! "
            print(f"  {mark}[{r['status']:<13}] {r['query'][:46]}  required={r['required']} missing={r['missing']}")
        print(f"  VERDICT: {summary['verdict']}  (misses={summary['total_misses']})")
    if summary["verdict"].startswith("NOT-CHECKED"):
        return 3          # nothing was measured: distinct from PASS (0), FAIL (1) and bad input (2)
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
compare_recall.py  --  the gold-standard no-regression comparator.

THE METHOD (codified from Loop A's no_regression_analysis.md): run every eval
query through `recall --top_k K` against an IDENTICAL DB copy BEFORE and AFTER a
change. The metric is **top-k SET overlap** (membership preservation).

  * Re-ordering within the set is the *intended enrichment* (lens/boost
    reordered the same K memories) and is ALLOWED.
  * A DROPPED relevant memory is the regression to catch.
  * A set-change is ONLY ALLOWED if it is reversible via a NAMED kill-switch
    (e.g. a persona exclude-tag removed via --no-lens). An *unexplained* drop
    is a regression and fails the gate.

Per-query verdict:
  identical    : same set AND same order
  pure-reorder : same set, different order  (allowed -- enrichment)
  set-changed  : membership delta (some ids dropped and/or added)
                 -> each dropped id is either EXPLAINED (matches a declared
                    kill-switch exclusion) or a REGRESSION.

Snapshot format (produced by eval_recall.sh): a JSON object
  { "db": "...", "top_k": 10, "snapshots": { "<query>": [id, id, ...], ... },
    "errors": { "<query>": "msg", ... } }
The comparator is tolerant of a plain {query: [ids]} dict too.

Stdlib only. NEVER raises on malformed input -- it reports the problem.
"""

import argparse
import json
import sys


PASS_MEAN_OVERLAP = 0.99   # >=99% mean set-overlap (the codified Voidloom bar)
PASS_MIN_OVERLAP = 0.90    # observed Loop A floor; min set-overlap must be >=
PASS_MAX_UNEXPLAINED_DROPS = 0  # 0 relevance-driven drops


def _load_snapshot_obj(data):
    """Normalise an already-parsed snapshot object (dict). Returns
    (snapshots: dict[str,list[int]], errors: dict). Shared by the file loader
    and by tests that pass an in-memory dict. Never raises.

    Accepts either the rich {"snapshots": {...}, "errors": {...}} envelope or a
    bare {query: [ids]} mapping.
    """
    if isinstance(data, dict) and "snapshots" in data:
        snaps = data.get("snapshots") or {}
        errs = dict(data.get("errors") or {})
    elif isinstance(data, dict):
        snaps = data
        errs = {}
    else:
        return {}, {"__file__": f"unexpected snapshot shape: {type(data).__name__}"}

    norm = {}
    for q, ids in snaps.items():
        if not isinstance(ids, list):
            errs.setdefault(q, f"ids not a list: {type(ids).__name__}")
            norm[q] = []
            continue
        out = []
        for x in ids:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        norm[q] = out
    return norm, errs


def _load_snapshot(path):
    """Load a snapshot file. Returns (snapshots: dict[str,list[int]], errors: dict).

    Accepts either the rich {"snapshots": {...}, "errors": {...}} envelope or a
    bare {query: [ids]} mapping. Never raises -- on a parse failure returns
    empty dicts plus a synthetic error so the caller can report it.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return {}, {"__file__": f"cannot load {path}: {e}"}
    return _load_snapshot_obj(data)


def _overlap(before_ids, after_ids):
    """Jaccard-style set overlap = |intersection| / |union|. 1.0 if both empty."""
    b, a = set(before_ids), set(after_ids)
    if not b and not a:
        return 1.0
    union = b | a
    if not union:
        return 1.0
    return len(b & a) / len(union)


def compare_query(query, before_ids, after_ids, expected_exclusions=None):
    """Compare one query's before/after id lists. Returns a verdict dict.

    expected_exclusions: optional set of ids that a declared kill-switch is
    *expected* to remove (e.g. persona exclude-tag matches). A drop of such an
    id is EXPLAINED (allowed); any other drop is a REGRESSION.
    """
    expected_exclusions = set(expected_exclusions or [])
    b, a = list(before_ids or []), list(after_ids or [])
    bset, aset = set(b), set(a)

    dropped = sorted(bset - aset)      # in BEFORE, gone in AFTER
    added = sorted(aset - bset)        # new in AFTER

    explained_drops = [i for i in dropped if i in expected_exclusions]
    unexplained_drops = [i for i in dropped if i not in expected_exclusions]

    if bset == aset:
        verdict = "identical" if b == a else "pure-reorder"
    else:
        verdict = "set-changed"

    return {
        "query": query,
        "verdict": verdict,
        "overlap": round(_overlap(b, a), 4),
        "before": b,
        "after": a,
        "dropped": dropped,
        "added": added,
        "explained_drops": explained_drops,
        "unexplained_drops": unexplained_drops,
        "is_regression": len(unexplained_drops) > 0,
    }


def compare_snapshots(before_snaps, after_snaps, exclusions_map=None):
    """Compare two snapshot dicts. Returns (per_query: list, summary: dict).

    exclusions_map: optional {query: [expected-excluded ids]} annotation; a drop
    of a listed id is EXPLAINED, anything else is a REGRESSION.
    """
    exclusions_map = exclusions_map or {}
    queries = list(before_snaps.keys())
    # Surface any query present in AFTER but missing from BEFORE (or vice versa).
    extra_after = [q for q in after_snaps if q not in before_snaps]

    per_query = []
    for q in queries:
        before_ids = before_snaps.get(q, [])
        after_ids = after_snaps.get(q, None)
        if after_ids is None:
            per_query.append({
                "query": q,
                "verdict": "missing-after",
                "overlap": 0.0,
                "before": before_ids,
                "after": [],
                "dropped": sorted(set(before_ids)),
                "added": [],
                "explained_drops": [],
                "unexplained_drops": sorted(set(before_ids)),
                "is_regression": True,
            })
            continue
        per_query.append(
            compare_query(q, before_ids, after_ids, exclusions_map.get(q))
        )

    n = len(per_query)
    overlaps = [r["overlap"] for r in per_query] or [1.0]
    total_unexplained = sum(len(r["unexplained_drops"]) for r in per_query)
    total_explained = sum(len(r["explained_drops"]) for r in per_query)
    n_identical = sum(1 for r in per_query if r["verdict"] == "identical")
    n_reorder = sum(1 for r in per_query if r["verdict"] == "pure-reorder")
    n_changed = sum(1 for r in per_query if r["verdict"] == "set-changed")
    n_missing = sum(1 for r in per_query if r["verdict"] == "missing-after")

    mean_overlap = sum(overlaps) / len(overlaps)
    min_overlap = min(overlaps)

    # Compare at the SAME precision we report (4dp) with a small epsilon, so a
    # value that IS exactly at the bar (e.g. mean 0.99 stored as 0.9899999...
    # in float) is not wrongly failed. A piece sitting precisely on the codified
    # threshold must PASS.
    _EPS = 1e-9
    passes = (
        round(mean_overlap, 4) >= PASS_MEAN_OVERLAP - _EPS
        and round(min_overlap, 4) >= PASS_MIN_OVERLAP - _EPS
        and total_unexplained <= PASS_MAX_UNEXPLAINED_DROPS
        and n_missing == 0
    )

    summary = {
        "queries": n,
        "identical": n_identical,
        "pure_reorder": n_reorder,
        "set_changed": n_changed,
        "missing_after": n_missing,
        "mean_overlap": round(mean_overlap, 4),
        "min_overlap": round(min_overlap, 4),
        "explained_drops": total_explained,
        "unexplained_drops": total_unexplained,
        "extra_after_queries": extra_after,
        "thresholds": {
            "mean_overlap_min": PASS_MEAN_OVERLAP,
            "min_overlap_min": PASS_MIN_OVERLAP,
            "max_unexplained_drops": PASS_MAX_UNEXPLAINED_DROPS,
        },
        "verdict": "PASS" if passes else "FAIL",
    }
    return per_query, summary


def _load_exclusions(path):
    """Load an optional exclusions annotation file.

    Shape: {"<query>": [id, id, ...], ...}. Each listed id is a drop that a
    named kill-switch is expected to cause and is therefore allowed.
    """
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "exclusions" in data:
            data = data["exclusions"]
        return {q: [int(x) for x in ids] for q, ids in (data or {}).items()}
    except (OSError, ValueError, TypeError):
        return {}


def _print_human(per_query, summary):
    print("=" * 78)
    print("NO-REGRESSION COMPARISON  (top-k SET overlap; reorder allowed, drop = regression)")
    print("=" * 78)
    for r in per_query:
        flag = "  REGRESSION!" if r["is_regression"] else ""
        line = f"  [{r['verdict']:<13}] ov={r['overlap']:.2f}  {r['query'][:46]}"
        print(line + flag)
        if r["dropped"]:
            exp = ",".join(map(str, r["explained_drops"])) or "-"
            une = ",".join(map(str, r["unexplained_drops"])) or "-"
            print(f"        dropped={r['dropped']}  explained={exp}  UNEXPLAINED={une}")
    print("-" * 78)
    print(f"  queries={summary['queries']}  identical={summary['identical']}  "
          f"reorder={summary['pure_reorder']}  set-changed={summary['set_changed']}  "
          f"missing={summary['missing_after']}")
    print(f"  mean_overlap={summary['mean_overlap']:.4f} (>= {PASS_MEAN_OVERLAP})  "
          f"min_overlap={summary['min_overlap']:.4f} (>= {PASS_MIN_OVERLAP})")
    print(f"  explained_drops={summary['explained_drops']}  "
          f"UNEXPLAINED_drops={summary['unexplained_drops']} (<= {PASS_MAX_UNEXPLAINED_DROPS})")
    print(f"  VERDICT: {summary['verdict']}")
    print("=" * 78)


def main(argv=None):
    ap = argparse.ArgumentParser(description="No-regression set-overlap comparator.")
    ap.add_argument("before", help="BEFORE snapshot JSON")
    ap.add_argument("after", help="AFTER snapshot JSON")
    ap.add_argument("--exclusions", help="optional kill-switch exclusions annotation JSON")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    before_snaps, b_err = _load_snapshot(args.before)
    after_snaps, a_err = _load_snapshot(args.after)
    exclusions = _load_exclusions(args.exclusions)

    load_errors = {}
    if b_err:
        load_errors["before"] = b_err
    if a_err:
        load_errors["after"] = a_err

    per_query, summary = compare_snapshots(before_snaps, after_snaps, exclusions)

    if args.json:
        print(json.dumps({
            "status": "ok",
            "load_errors": load_errors,
            "summary": summary,
            "results": per_query,
        }, indent=2))
    else:
        if load_errors:
            print(f"  [load warnings] {json.dumps(load_errors)}")
        _print_human(per_query, summary)

    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

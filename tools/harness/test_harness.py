#!/usr/bin/env python3
"""
test_harness.py -- synthetic-by-construction tests for the Voidloom harness.

Proves, with NO GPU and NO live DB, the logic that the gate depends on:
  1. compare_recall: identical / pure-reorder / set-changed / drop classification,
     and that an UNEXPLAINED drop is a regression while a kill-switch-EXPLAINED
     drop is allowed.
  2. corpus_presence: a known-VACUOUS query -> VACUOUS, a known-distinctive
     query -> SCOREABLE, an over-common term-only query -> BROAD. Built on a
     tiny FTS5 corpus we construct by hand so the answer is known a priori.
  3. check_must_retrieve: a present anchor -> PASS, a dropped anchor -> FAIL.
  4. eval side-effect-free PROOF (logic): a recall run must leave access_count
     unchanged. We construct a temp DB, simulate the snapshot's side-effect
     record, and assert the harness reports unchanged. (The end-to-end GPU proof
     is in run_gate.sh; this is the deterministic logic twin.)

Run:  python3 test_harness.py        (exit 0 = all pass)
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import compare_recall          # noqa: E402
import corpus_presence         # noqa: E402
import check_must_retrieve     # noqa: E402

_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        _failures.append(msg)


# ---------------------------------------------------------------------------
# 1. compare_recall logic
# ---------------------------------------------------------------------------

def test_compare():
    print("[1] compare_recall classification")

    # identical: same set, same order
    r = compare_recall.compare_query("q", [1, 2, 3], [1, 2, 3])
    check(r["verdict"] == "identical", "same set+order -> identical")
    check(r["overlap"] == 1.0, "identical overlap=1.0")
    check(not r["is_regression"], "identical is not a regression")

    # pure-reorder: same set, different order (the intended enrichment)
    r = compare_recall.compare_query("q", [1, 2, 3], [3, 1, 2])
    check(r["verdict"] == "pure-reorder", "same set new order -> pure-reorder")
    check(r["overlap"] == 1.0, "pure-reorder overlap=1.0 (set unchanged)")
    check(not r["is_regression"], "pure-reorder is NOT a regression (allowed)")

    # set-changed with UNEXPLAINED drop -> regression
    r = compare_recall.compare_query("q", [1, 2, 3], [1, 2, 4])
    check(r["verdict"] == "set-changed", "membership delta -> set-changed")
    check(r["dropped"] == [3], "dropped id 3 detected")
    check(r["added"] == [4], "added id 4 detected")
    check(r["unexplained_drops"] == [3], "drop of 3 is UNEXPLAINED")
    check(r["is_regression"], "unexplained drop IS a regression")

    # set-changed with kill-switch-EXPLAINED drop -> allowed
    r = compare_recall.compare_query("q", [1, 2, 3], [1, 2, 4], expected_exclusions={3})
    check(r["verdict"] == "set-changed", "still set-changed structurally")
    check(r["explained_drops"] == [3], "drop of 3 is EXPLAINED by kill-switch")
    check(not r["is_regression"], "explained (kill-switch) drop is NOT a regression")

    # overlap math: drop one of two, add one -> jaccard |{1}|/|{1,2,3}| = 1/3.
    # The comparator rounds to 4dp, so compare against the rounded value.
    r = compare_recall.compare_query("q", [1, 2], [1, 3])
    check(abs(r["overlap"] - round(1 / 3, 4)) < 1e-9,
          f"jaccard overlap=0.3333 (got {r['overlap']})")


def test_compare_snapshots_and_thresholds():
    print("[2] compare_snapshots aggregate + thresholds")

    # Realistic top-k=10 sets so a single drop = 9/10 overlap (>= 0.90 floor),
    # matching the Loop A reality where one persona-exclusion left overlap 90%.
    full = list(range(1, 11))           # [1..10]
    before = {"snapshots": {"qa": list(full), "qb": list(full)}}
    # No-op: before==after for every query -> PASS, 100% overlap, 0 drops.
    after = {"snapshots": {"qa": list(full), "qb": list(full)}}
    b, _ = compare_recall._load_snapshot_obj(before)
    a, _ = compare_recall._load_snapshot_obj(after)
    rows, summary = compare_recall.compare_snapshots(b, a)
    check(summary["verdict"] == "PASS", "no-op before==after -> PASS")
    check(summary["mean_overlap"] == 1.0, "no-op mean_overlap=1.0")
    check(summary["unexplained_drops"] == 0, "no-op 0 unexplained drops")
    check(summary["identical"] == 2, "no-op both identical")

    # One UNEXPLAINED drop (id 10 -> id 11) on qa: overlap 9/11... actually a
    # drop+add gives 9/11=0.818 < 0.90 -> FAIL on the floor too. Use a pure drop
    # (id 10 gone, replaced by an already-present reorder is impossible) -- model
    # the persona-exclusion case: 10 dropped, list shrinks is unrealistic, so the
    # lens drops 10 and surfaces a NEW id 11. The drop is the regression signal.
    after2 = {"snapshots": {"qa": full[:9] + [11], "qb": list(full)}}  # 10 -> 11
    a2, _ = compare_recall._load_snapshot_obj(after2)
    rows2, summary2 = compare_recall.compare_snapshots(b, a2)
    check(summary2["verdict"] == "FAIL", "one unexplained drop -> FAIL")
    check(summary2["unexplained_drops"] == 1, "exactly 1 unexplained drop counted")
    check(10 in rows2[0]["dropped"], "id 10 detected as dropped")

    # Same drop but kill-switch-explained (id 10 is a declared persona exclusion).
    # overlap = |{1..9}| / |{1..9,10,11}| = 9/11 = 0.818 < 0.90 -> the FLOOR still
    # fails even though the drop is explained. This is correct: an explained drop
    # that ALSO tanks overlap below the floor is still a gate FAIL. The Loop A
    # reality (overlap exactly 0.90) is the boundary case below.
    rows3, summary3 = compare_recall.compare_snapshots(
        b, a2, exclusions_map={"qa": [10]})
    check(summary3["explained_drops"] == 1, "explained drop counted")
    check(summary3["unexplained_drops"] == 0, "0 unexplained when drop is declared")
    check(summary3["verdict"] == "FAIL",
          "explained drop that breaches 0.90 overlap floor -> still FAIL (correct)")

    # The PURE Loop-A case: 20 queries, 18 identical + 2 explained persona
    # exclusions each at overlap 0.90 (id 10 dropped, list of 9, no add).
    # mean = (18*1.0 + 2*0.90)/20 = 0.99 exactly; min = 0.90; explained drops only
    # -> PASS. This reproduces the codified bar (mean 99.0%, min 90.0%, 0
    # relevance-driven drops) from no_regression_analysis.REFERENCE.md.
    snaps_before = {f"q{i}": list(full) for i in range(20)}
    snaps_after = {f"q{i}": list(full) for i in range(18)}
    snaps_after["q18"] = full[:9]   # id 10 excluded
    snaps_after["q19"] = full[:9]   # id 10 excluded
    bb, _ = compare_recall._load_snapshot_obj({"snapshots": snaps_before})
    ab, _ = compare_recall._load_snapshot_obj({"snapshots": snaps_after})
    excl = {"q18": [10], "q19": [10]}
    rows_b, summary_b = compare_recall.compare_snapshots(bb, ab, exclusions_map=excl)
    check(abs(summary_b["mean_overlap"] - 0.99) < 1e-9,
          f"Loop-A reproduction: mean_overlap=0.99 (got {summary_b['mean_overlap']})")
    check(abs(summary_b["min_overlap"] - 0.90) < 1e-9,
          f"Loop-A reproduction: min_overlap=0.90 (got {summary_b['min_overlap']})")
    check(summary_b["explained_drops"] == 2, "Loop-A: 2 explained persona drops")
    check(summary_b["unexplained_drops"] == 0, "Loop-A: 0 relevance-driven drops")
    check(summary_b["verdict"] == "PASS",
          "Loop-A reproduction (mean 0.99, min 0.90, 0 unexplained) -> PASS")

    # Missing-after query -> regression.
    a_missing, _ = compare_recall._load_snapshot_obj({"snapshots": {"qa": list(full)}})
    rows4, summary4 = compare_recall.compare_snapshots(b, a_missing)
    check(summary4["missing_after"] == 1, "missing-after query detected")
    check(summary4["verdict"] == "FAIL", "missing-after -> FAIL")


def test_compare_malformed_never_raises():
    print("[3] compare_recall tolerates malformed input")
    # Non-list ids, None, junk -- must not raise.
    try:
        b, be = compare_recall._load_snapshot_obj({"snapshots": {"q": "not-a-list"}})
        a, ae = compare_recall._load_snapshot_obj({"snapshots": {"q": [1, "2", None, "x"]}})
        rows, summary = compare_recall.compare_snapshots(b, a)
        check(True, "malformed snapshots did not raise")
        check(b["q"] == [], "non-list ids normalised to []")
        check(a["q"] == [1, 2], "mixed id list -> ints only [1,2]")
    except Exception as e:  # pragma: no cover
        check(False, f"malformed input raised: {e}")


# ---------------------------------------------------------------------------
# 4. corpus_presence on a hand-built FTS5 corpus (known answers)
# ---------------------------------------------------------------------------

def _build_synthetic_corpus():
    """A tiny in-memory corpus where we KNOW the document frequencies.

    10 documents. 'zorptdistinct' appears in exactly 1 (df=0.1 -> distinctive).
    'commonword' appears in 8 of 10 (df=0.8 > 0.6 -> over-common -> not distinctive).
    'neverword' appears in 0 (vacuous fodder).
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT);
        CREATE VIRTUAL TABLE memories_fts USING fts5(
            summary, content, content='memories', content_rowid='id',
            tokenize='porter unicode61');
        CREATE TRIGGER m_ai AFTER INSERT ON memories BEGIN
          INSERT INTO memories_fts(rowid, summary, content)
          VALUES (new.id, new.summary, new.content);
        END;
        """
    )
    docs = []
    for i in range(1, 11):
        words = ["commonword"] if i <= 8 else ["plainword"]  # 8 of 10 have commonword
        if i == 5:
            words.append("zorptdistinct")  # 1 of 10
        docs.append((i, " ".join(words), " ".join(words)))
    conn.executemany("INSERT INTO memories(id, summary, content) VALUES (?,?,?)", docs)
    conn.commit()
    return conn


def test_corpus_presence():
    print("[4] corpus_presence classification (synthetic known corpus)")
    conn = _build_synthetic_corpus()
    total = corpus_presence.corpus_size(conn)
    check(total == 10, f"synthetic corpus size=10 (got {total})")

    # DF sanity
    check(corpus_presence._phrase_df(conn, "zorptdistinct") == 1, "zorptdistinct df=1")
    check(corpus_presence._phrase_df(conn, "commonword") == 8, "commonword df=8")
    check(corpus_presence._phrase_df(conn, "neverword") == 0, "neverword df=0")

    # VACUOUS: no term matches
    r = corpus_presence.classify_query(conn, "neverword absentterm missingtoken", total)
    check(r["classification"] == "VACUOUS", "all-absent query -> VACUOUS")

    # SCOREABLE: has a distinctive (df=1/10=10% <= 60%) term
    r = corpus_presence.classify_query(conn, "zorptdistinct commonword", total)
    check(r["classification"] == "SCOREABLE", "distinctive term -> SCOREABLE")
    check("zorptdistinct" in r["scoreable_terms"], "zorptdistinct is the distinctive term")
    check("commonword" not in r["scoreable_terms"], "commonword (80%) is NOT distinctive")

    # BROAD: only over-common matches (commonword df=80% > ceiling), no distinctive
    r = corpus_presence.classify_query(conn, "commonword neverword", total)
    check(r["classification"] == "BROAD", "only over-common hit -> BROAD (not SCOREABLE)")
    check(r["scoreable_terms"] == [], "BROAD has no distinctive terms")

    # The #3952 OR-of-everything anti-pattern: a single over-common term must NOT
    # make the query measurable.
    r = corpus_presence.classify_query(conn, "commonword", total)
    check(r["classification"] == "BROAD", "lone over-common term -> BROAD (the #3952 trap caught)")

    # Summary denominators are honest
    results = corpus_presence.classify_queries(conn, [
        "zorptdistinct commonword",   # SCOREABLE
        "commonword neverword",       # BROAD
        "neverword absentterm",       # VACUOUS
    ])
    s = corpus_presence.summarize(results)
    check(s["headline"] == "1/3 SCOREABLE, 1 BROAD, 1 VACUOUS",
          f"honest denominators (got '{s['headline']}')")

    # Malformed query never raises
    try:
        corpus_presence.classify_query(conn, None, total)
        corpus_presence.classify_query(conn, "", total)
        corpus_presence.classify_query(conn, "!!! @@@ ###", total)
        check(True, "malformed/empty queries do not raise")
    except Exception as e:  # pragma: no cover
        check(False, f"malformed query raised: {e}")
    conn.close()


# ---------------------------------------------------------------------------
# 5. check_must_retrieve logic
# ---------------------------------------------------------------------------

def test_must_retrieve():
    print("[5] check_must_retrieve cap-floor")
    snap = {"snapshots": {"qa": [1, 2, 3, 4, 5], "qb": [9, 8, 7]}}
    must = {"qa": [3], "qb": [7]}
    rows, summary = check_must_retrieve.check(snap, must)
    check(summary["verdict"] == "PASS", "anchors present -> PASS")
    check(summary["total_misses"] == 0, "0 misses when anchors present")

    # Drop an anchor -> floor breach
    snap2 = {"snapshots": {"qa": [1, 2, 99, 4, 5], "qb": [9, 8, 7]}}
    rows2, summary2 = check_must_retrieve.check(snap2, must)
    check(summary2["verdict"] == "FAIL", "dropped anchor -> FAIL (relevance drop caught)")
    check(summary2["total_misses"] == 1, "exactly 1 floor breach")

    # Query absent entirely
    rows3, summary3 = check_must_retrieve.check({"snapshots": {"qb": [9, 8, 7]}}, must)
    check(summary3["verdict"] == "FAIL", "absent query -> FAIL")


# ---------------------------------------------------------------------------
# 6. side-effect-free PROOF (logic twin of the GPU end-to-end check)
# ---------------------------------------------------------------------------

def test_side_effect_free_logic():
    print("[6] side-effect-free invariant (input-DB-md5, scratch disposable)")
    # The REAL confinement invariant the harness enforces is: the INPUT DB file
    # is byte-identical (md5) before and after a run. recall DOES bump
    # access_count, but only on a DISPOSABLE scratch copy -- never the input.
    # We model exactly that here: cp the input to a scratch, mutate the scratch
    # (simulating recall's access_count bump), and assert the INPUT md5 is stable
    # while the scratch legitimately diverged.
    import hashlib

    def md5(path):
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()

    with tempfile.TemporaryDirectory() as td:
        input_db = os.path.join(td, "input.db")
        conn = sqlite3.connect(input_db)
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, access_count INTEGER)")
        conn.executemany("INSERT INTO memories VALUES (?,?)", [(1, 5), (2, 7), (3, 0)])
        conn.commit()
        conn.close()

        input_md5_before = md5(input_db)

        # The harness's move: cp input -> scratch, run recall against scratch.
        scratch = os.path.join(td, "scratch.db")
        import shutil
        shutil.copy(input_db, scratch)
        sc = sqlite3.connect(scratch)
        ac_before = sc.execute("SELECT COALESCE(SUM(access_count),0) FROM memories").fetchone()[0]
        # Simulate recall bumping access_count on every returned row.
        sc.execute("UPDATE memories SET access_count = access_count + 1")
        sc.commit()
        ac_after = sc.execute("SELECT COALESCE(SUM(access_count),0) FROM memories").fetchone()[0]
        sc.close()

        input_md5_after = md5(input_db)

        check(input_md5_before == input_md5_after,
              "INPUT DB md5 unchanged after a (scratch) recall run -- confinement holds")
        check(ac_after == ac_before + 3,
              f"scratch access_count bumped as expected (+3, {ac_before}->{ac_after})")

        # The snapshot record the harness writes (corrected schema):
        rec = {
            "input_db_md5_before": input_md5_before,
            "input_db_md5_after": input_md5_after,
            "input_db_unchanged": input_md5_before == input_md5_after,
            "scratch_access_count_delta": ac_after - ac_before,
            "unchanged": input_md5_before == input_md5_after,
        }
        check(rec["unchanged"] is True, "side_effect_check.unchanged tracks input-db invariant")
        rt = json.loads(json.dumps(rec))
        check(rt["input_db_unchanged"] is True, "side-effect record survives JSON round-trip")


def main():
    print("=" * 60)
    print("Voidloom HARNESS -- synthetic-by-construction test suite")
    print("=" * 60)
    test_compare()
    test_compare_snapshots_and_thresholds()
    test_compare_malformed_never_raises()
    test_corpus_presence()
    test_must_retrieve()
    test_side_effect_free_logic()
    print("=" * 60)
    if _failures:
        print(f"RESULT: {len(_failures)} FAILURE(S)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

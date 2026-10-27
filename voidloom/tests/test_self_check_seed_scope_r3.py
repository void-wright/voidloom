# -*- coding: utf-8 -*-
"""Round 3 G1, item 2 (ruler VL-MC-v1.2 criterion (ii)): the installer's acceptance gate never contradicts the installer's
own policy (memory #23616).

MEASURED on the reference host, 2026-10-01 21:12: the ambient upgrade's gate failed 3 of 19 required checks -- 'seed pack
loaded (74)' with 73 tagged, the JOIN-table count 73, and "topic tag 'anchor:seed_what_this_is' resolves" 0 -- while every
brain-health check passed. The brain held seed pack 1.5.0's entries but for 1 new and 11 changed ones, and the installer's
OWN policy for that shape is "an upgrade adds no memories by itself". The owner read "ACCEPTANCE GATE FAILED -- Nothing above
is a claim that it works" over an install that had worked, and re-ran it.

Bound here, on a brain built from the package's own blank brain + its own seed pack (no host brain, no model):
  * an UPGRADED brain (--seed-optional, what install.sh passes in the ambient shape) that lacks some of the pack's entries
    PASSES every required check; the entries it has not received are REPORTED as the owner's choice, never failed;
  * a FRESH install (no flag) still requires the FULL pack -- the same brain FAILS there;
  * after the owner's load-seed the upgraded brain has nothing left to choose;
  * the scoped checks are the LOADER's delta (claude_memory_lite.seed_plan), and an unknowable delta is a named failure;
  * the topic-tag check never names an anchor or a revision stamp; every failed check says what it means.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
PACK = ROOT / "seed" / "seed_pack.json"


def _run(args, db, tree=ROOT, seed=PACK):
    env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), WIZARD_LIBRARY_SEED=str(seed), PYTHONDONTWRITEBYTECODE="1")
    env.pop("WIZARD_LIBRARY_HOME", None)
    return subprocess.run([sys.executable, "-B", str(tree / "install" / "self_check.py")] + args, capture_output=True,
                          text=True, env=env, timeout=300)


def _json(db, *flags, tree=ROOT):
    p = _run(["--json"] + list(flags), db, tree=tree)
    return p.returncode, json.loads(p.stdout)


def _lite(db, *args):
    env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "claude_memory_lite.py")] + list(args),
                          capture_output=True, text=True, env=env, timeout=300)


@pytest.fixture
def brain():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="selfcheck_seed_r3_", dir=str(SCRATCH)))
    db = root / "claude_memory.db"
    shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(db))
    env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "schema_migrator.py"), "apply", "--db", str(db)],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]
    yield db
    shutil.rmtree(str(root), ignore_errors=True)


def _pack():
    return json.loads(PACK.read_text(encoding="utf-8"))["memories"]


def _anchor(m):
    return [t.strip() for t in m["tags"].split(",") if t.strip().startswith("anchor:seed_")][0]


def _row_of(c, anchor):
    return c.execute("SELECT memory_id FROM memory_tags WHERE tag = ?", (anchor,)).fetchone()[0]


def _upgrade_shape(db):
    """The host's SHAPE on a package brain: the whole pack loaded, then -- one entry never received (NEW), one held as a
    hand-stored draft carrying its anchor but not the pack's words or revision (CHANGED), every other row stripped of its
    anchor and revision stamp as an older pack's load left it (ADOPTED: the exact words, no anchor)."""
    assert _lite(db, "load-seed", "--file", str(PACK)).returncode == 0
    mems = _pack()
    new_a, chg_a = _anchor(mems[-1]), _anchor(mems[-2])
    c = sqlite3.connect(str(db))
    rid = _row_of(c, new_a)
    c.execute("DELETE FROM memory_tags WHERE memory_id = ?", (rid,))
    c.execute("DELETE FROM memories WHERE id = ?", (rid,))
    cid = _row_of(c, chg_a)
    c.execute("UPDATE memories SET content = content || ' (an earlier hand-stored draft)' WHERE id = ?", (cid,))
    c.execute("DELETE FROM memory_tags WHERE memory_id = ? AND tag LIKE 'seed-rev:%'", (cid,))
    c.execute("DELETE FROM memory_tags WHERE memory_id != ? AND (tag LIKE 'anchor:seed_%' OR tag LIKE 'seed-rev:%')", (cid,))
    c.commit()
    plan = json.loads(_lite(db, "load-seed", "--file", str(PACK), "--plan").stdout.strip().splitlines()[-1])
    c.close()
    assert plan["new"] == [new_a] and plan["changed"] == [chg_a], plan
    return new_a, chg_a


def test_an_upgraded_brain_missing_new_entries_passes_and_reports_them_as_a_choice(brain):
    new_a, chg_a = _upgrade_shape(brain)
    rc, d = _json(brain, "--seed-optional")
    failed = [c["name"] for c in d["checks"] if c["required"] and not c["ok"]]
    assert rc == 0 and failed == [], failed
    assert len(d["choices"]) == 1, d["choices"]
    ch = d["choices"][0]
    assert "1 new entry" in ch and "1 changed entry" in ch, ch
    assert new_a[len("anchor:seed_"):] in ch and chg_a[len("anchor:seed_"):] in ch, ch
    assert "YOUR choice, not a failure" in ch and "--load-seed" in ch and "load-seed --file" in ch, ch
    names = [c["name"] for c in d["checks"]]
    n = len(_pack())
    assert "seed pack: the %d entries this brain has received (of the pack's %d) are present" % (n - 1, n) in names, names


def test_a_fresh_install_still_requires_the_full_pack(brain):
    _upgrade_shape(brain)
    rc, d = _json(brain)                                          # no --seed-optional: a fresh install's gate
    failed = [c["name"] for c in d["checks"] if c["required"] and not c["ok"]]
    assert rc == 1 and "seed pack loaded (%d memories)" % len(_pack()) in failed, failed
    assert d["choices"] == [], d["choices"]


def test_after_the_owners_load_there_is_nothing_left_to_choose(brain):
    _upgrade_shape(brain)
    assert _lite(brain, "load-seed", "--file", str(PACK)).returncode == 0
    rc, d = _json(brain, "--seed-optional")
    assert rc == 0 and d["choices"] == [], d
    rc, d = _json(brain)                                          # and the full pack is now present
    assert rc == 0, [c for c in d["checks"] if c["required"] and not c["ok"]]


def test_the_topic_tag_check_never_names_an_anchor_or_a_revision(brain):
    _upgrade_shape(brain)
    for flags in ([], ["--seed-optional"]):
        _rc, d = _json(brain, *flags)
        topic = [c["name"] for c in d["checks"] if c["name"].startswith("topic tag")]
        assert topic and not any("anchor:seed_" in t or "seed-rev:" in t for t in topic), topic


def test_a_brain_that_never_carried_the_pack_keeps_its_own_content_check(brain):
    r = _lite(brain, "store", "--content", "the watcher matched its own pgrep line and waited forever",
              "--summary", "WATCHER SELFMATCH lesson", "--category", "error_pattern", "--importance", "0.9")
    assert r.returncode == 0, r.stderr[-400:]
    rc, d = _json(brain, "--seed-optional")
    assert rc == 0 and d["choices"] == [], d
    assert any("NOT APPLICABLE" in n for n in d["notes"]), d["notes"]


def test_an_unknowable_delta_is_a_named_failure_never_a_pass(brain, tmp_path):
    _upgrade_shape(brain)
    tree = tmp_path / "tree"
    (tree / "install").mkdir(parents=True)
    (tree / "organs").mkdir()
    shutil.copy2(str(ROOT / "install" / "self_check.py"), str(tree / "install" / "self_check.py"))
    for f in ("schema_migrator.py", "kind_taxonomy.py"):                 # everything but the loader
        if (ROOT / "organs" / f).exists():
            shutil.copy2(str(ROOT / "organs" / f), str(tree / "organs" / f))
    if (ROOT / "migrations").exists():
        shutil.copytree(str(ROOT / "migrations"), str(tree / "migrations"))
    rc, d = _json(brain, "--seed-optional", tree=tree)
    bad = [c for c in d["checks"] if c["required"] and not c["ok"]]
    assert rc == 1 and any("could be derived" in c["name"] for c in bad), bad
    assert all(c.get("means") for c in bad), bad


def test_every_failed_required_check_says_what_it_means(brain):
    _upgrade_shape(brain)
    _rc, d = _json(brain)
    bad = [c for c in d["checks"] if c["required"] and not c["ok"]]
    assert bad and all(c.get("means") for c in bad), bad


# ------------------------------------------------------------------------------------------------------------------------
# R3-06 (the Round 3 reflection): under --seed-optional the scoped seed checks were TRUE BY CONSTRUCTION ('received' was
# derived from the rows that carry the common tag, the very rows the checks count), so a brain whose memory_tags lost the
# rows of 30 seed memories (the memories still live) PASSED the scoped gate and was offered '30 new entries' -- and y /
# --load-seed inserted 30 DUPLICATES (74 -> 104 live). Now: the LOADER never inserts an entry whose words (this revision or
# an older one it supersedes) a memory here already holds, or whose anchor a memory here carries -- it reports it as held
# here without the pack's tags -- and the gate, reading the loader's own delta, FAILS by name and offers nothing for it.
def _seed_fps(c, mems):
    sys.path.insert(0, str(ROOT / "organs"))
    import claude_memory_lite as _l
    revs = {}
    for m in mems:
        for r in [_l.seed_revision(_l._entry_summary(m), m.get("content", ""))] + list(m.get("supersedes") or []):
            revs[r] = _anchor(m)
    live = {}
    for mid, s, ct in c.execute("SELECT id, summary, content FROM memories WHERE COALESCE(is_archived,0)=0"):
        fp = _l.seed_revision(s, ct)
        if fp in revs:
            live.setdefault(revs[fp], []).append(mid)
    return live


def _lose_tags(db, n):
    """memory_tags lost EVERY row of n seed memories; the memories themselves are live and untouched."""
    c = sqlite3.connect(str(db))
    ids = [r[0] for r in c.execute("SELECT DISTINCT memory_id FROM memory_tags WHERE tag = 'seed-pack-agnostic' "
                                   "ORDER BY memory_id LIMIT ?", (n,))]
    c.executemany("DELETE FROM memory_tags WHERE memory_id = ?", [(i,) for i in ids])
    c.commit(); c.close()
    return ids


def _live(db):
    c = sqlite3.connect(str(db))
    n = c.execute("SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0").fetchone()[0]
    c.close()
    return n


def test_r3_06_a_brain_whose_seed_rows_lost_their_tags_fails_the_scoped_gate_and_is_offered_nothing(brain):
    assert _lite(brain, "load-seed", "--file", str(PACK)).returncode == 0
    lost = _lose_tags(brain, 30)
    rc, d = _json(brain, "--seed-optional")
    bad = [c for c in d["checks"] if c["required"] and not c["ok"]]
    names = [c["name"] for c in bad]
    assert rc == 1 and "seed pack: every entry this brain holds carries the pack's tags" in names, names
    row = [c for c in bad if c["name"] == "seed pack: every entry this brain holds carries the pack's tags"][0]
    assert "30 entries" in row["detail"] and str(lost[0]) in row["detail"], row
    assert row["means"] and "would duplicate" not in row["fix"] and "restore" in row["fix"], row
    assert d["choices"] == [], "the gate offered to load entries this brain already holds: %s" % d["choices"]


def test_r3_06_load_seed_never_duplicates_a_memory_whose_tags_were_lost(brain):
    assert _lite(brain, "load-seed", "--file", str(PACK)).returncode == 0
    lost = _lose_tags(brain, 30)
    before = _live(brain)
    plan = json.loads(_lite(brain, "load-seed", "--file", str(PACK), "--plan").stdout.strip().splitlines()[-1])
    assert plan["new"] == [] and plan["changed"] == [] and len(plan["held_untagged"]) == 30, plan
    for _ in range(2):                                               # the owner's y / --load-seed, then a re-run
        p = _lite(brain, "load-seed", "--file", str(PACK))
        out = json.loads(p.stdout.strip().splitlines()[-1])
        assert p.returncode == 0 and out["loaded"] == 0 and sorted(out["held_untagged_ids"]) == sorted(lost), out
        assert _live(brain) == before, "load-seed added memories a brain already holds"
    c = sqlite3.connect(str(brain))
    dup = {a: ids for a, ids in _seed_fps(c, _pack()).items() if len(ids) > 1}
    c.close()
    assert dup == {}, "duplicate live memories of one seed entry: %s" % dup


def test_r3_06_a_changed_entry_supersedes_and_a_rerun_adds_zero_never_two_live_versions(brain):
    """The upgrade shape (1 new, 1 changed, the rest adopted), with the NEW entry's words already stored BY HAND (no tags):
    the load supersedes the changed entry (old row archived + seed-superseded), inserts nothing for the held new entry,
    and a second load adds zero. Every entry has exactly ONE live memory holding its words."""
    new_a, chg_a = _upgrade_shape(brain)
    mems = _pack()
    hand = [m for m in mems if _anchor(m) == new_a][0]
    c = sqlite3.connect(str(brain))
    c.execute("INSERT INTO memories (content, summary, category, importance, created_at, updated_at) VALUES (?, ?, ?, ?, "
              "datetime('now'), datetime('now'))", (hand["content"], hand.get("summary") or hand["content"][:100],
                                                     hand["category"], 0.5))
    c.commit(); c.close()
    before = _live(brain)
    out = json.loads(_lite(brain, "load-seed", "--file", str(PACK)).stdout.strip().splitlines()[-1])
    assert out["new"] == [] and out["held_untagged"] == [new_a], out
    assert out["changed"] == [chg_a] and out["loaded"] == 1, out
    assert _live(brain) == before, (before, _live(brain))           # +1 changed revision, -1 superseded (archived)
    c = sqlite3.connect(str(brain))
    old = c.execute("SELECT COALESCE(is_archived,0) FROM memories WHERE id = ?", (out["superseded_ids"][0],)).fetchone()[0]
    sup = c.execute("SELECT COUNT(*) FROM memory_tags WHERE memory_id = ? AND tag = 'seed-superseded'",
                    (out["superseded_ids"][0],)).fetchone()[0]
    per_anchor = {_anchor(m): c.execute("SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0 AND id IN (SELECT "
                                        "memory_id FROM memory_tags WHERE tag = ?)", (_anchor(m),)).fetchone()[0]
                  for m in mems}
    fps = _seed_fps(c, mems)
    c.close()
    assert old == 1 and sup == 1, "the changed entry's older memory was not superseded"
    assert {a: ids for a, ids in fps.items() if len(ids) > 1} == {}, "two live memories hold one entry's words: %s" % fps
    assert all(v == 1 for a, v in per_anchor.items() if a != new_a) and len(fps[new_a]) == 1, (per_anchor, fps.get(new_a))
    again = json.loads(_lite(brain, "load-seed", "--file", str(PACK)).stdout.strip().splitlines()[-1])
    assert again["loaded"] == 0 and _live(brain) == before, again

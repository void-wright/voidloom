# -*- coding: utf-8 -*-
"""Round 2, lane R2c, finding m4: an owner who answered the thinker question "e) not now" is UNCONFIGURED -- a clear
status and exit 0 -- never a nightly STARVED (exit 79) and never a STARVED dream watch.

MEASURED (r1 reflection): scratch brain, `thinker.py set off`, memory_dreaming.dream_on_pair(...) ->
out = {'starved': {'reason': 'unconfigured', 'backend': 'off', ...}}; every night ended 'starved', exit 79, and the
dream ledger read STARVED for a deliberate choice.

The "off" answer here is REAL: the seam's own `set off` writes <brain>/THINKER_RECORD and its own think() raises.
No model, no network. A quota night (a typed double) must stay STARVED / 79: the guard tests below.
Each test is RED on 6bb45ee and GREEN on lane/R2c unless its docstring calls it a guard.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import dream_pipeline as DP  # noqa: E402
import memory_dreaming as MD  # noqa: E402
import organ_yield_sentinel as S  # noqa: E402
import schema_migrator  # noqa: E402
import thinker  # noqa: E402
from voidloom.tests import _thinker_fakes as F  # noqa: E402

A = {"id": 1, "content": "alpha memory about sleep", "summary": "alpha", "category": "insight", "tags": "",
     "created_at": "2026-09-01 00:00:00", "source_project": "p1", "kind": "episodic"}
B = {"id": 2, "content": "beta memory about dreams", "summary": "beta", "category": "insight", "tags": "",
     "created_at": "2026-09-02 00:00:00", "source_project": "p2", "kind": "episodic"}


def _raise_quota(*a, **k):
    raise thinker.ThinkUnavailable("quota", "weekly limit (fake, test-only)", "double")


@pytest.fixture(scope="module")
def schema_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "base.db"
    out = schema_migrator.apply(str(path), ROOT / "migrations")
    assert out["complete"], out["files_with_errors"]
    return path


@pytest.fixture()
def world(monkeypatch, tmp_path):
    return F.world(monkeypatch, tmp_path / "w")


def _say_not_now(w):
    env = thinker.run(["set", "off", "--answered-by", "owner"])
    assert env["_exit_code"] == 0, env
    assert thinker.read_record()["backend"] == "off"


@pytest.fixture()
def brain(schema_db, world, monkeypatch):
    path = world.brain / "claude_memory.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    ids = [c.execute("INSERT INTO memories (content, summary, category, importance, source_project, kind, created_at, "
                     "updated_at, last_accessed) VALUES (?, ?, 'insight', 0.5, ?, 'episodic', "
                     "datetime('now','-3 days'), datetime('now'), datetime('now'))",
                     (f"memory {i} about topic {i}", f"m{i}", f"p{i % 2}")).lastrowid for i in range(6)]
    c.commit()
    c.close()
    pairs = {"divergent": [(ids[0], ids[1], 0.8, None), (ids[2], ids[3], 0.7, None)],
             "close_context": [(ids[4], ids[5], 0.2, {"diff": "project"})], "persona": [], "notes": {}}
    monkeypatch.setattr(DP, "_rem_pools", lambda conn, cfg, pool: pairs)
    monkeypatch.setattr(DP, "PHASES", [p for p in DP.PHASES if p.name == "rem_dreaming"])
    cfg = world.root / "cfg.json"
    cfg.write_text(json.dumps({"lock_file": str(world.root / "dream.lock")}))
    return path, cfg


def _one(path, sql):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql).fetchone()
    finally:
        c.close()


def test_not_now_is_declined_at_the_pair_and_counts_no_failure(world):
    _say_not_now(world)
    out, fails = {}, []
    assert MD.dream_on_pair(A, B, "divergent", on_failure=fails.append, out=out) is None
    assert out["starved"]["declined"] is True, out
    assert fails == [], "the owner's answer is not an LLM failure"


def test_not_now_night_is_unconfigured_exit_0_and_writes_nothing(brain, world, capsys):
    path, cfg = brain
    _say_not_now(world)
    rc = DP.main(["run", str(path), "--config", str(cfg)])
    res = json.loads(capsys.readouterr().out)
    assert rc == 0, res
    assert res["status"] == "unconfigured" and "not now" in res["unconfigured_note"], res
    assert "starved_reason" not in res
    rem = [p for p in res["phases"] if p["phase"] == "rem_dreaming"][0]
    assert rem["status"] == "unconfigured", rem
    assert _one(path, "SELECT COUNT(*) FROM dream_log")[0] == 0
    # the phase row LANDS (the 027 CHECK admits no 'unconfigured'; it is folded to 'skipped' with the status first)
    st, detail = _one(path, "SELECT status, detail FROM dream_phase_log WHERE phase_name='rem_dreaming'")
    assert st == "skipped" and detail.startswith("unconfigured"), (st, detail)


def test_the_legacy_cycle_and_its_cli_report_unconfigured_exit_0(brain, world, monkeypatch, capsys):
    path, cfg = brain
    _say_not_now(world)
    monkeypatch.setattr(MD, "select_divergent_pairs", lambda conn, **kw: [(1, 2, 0.8), (3, 4, 0.7)])
    monkeypatch.setattr(MD, "select_close_context_pairs", lambda conn, **kw: [])
    monkeypatch.setattr(MD, "select_persona_pairs", lambda conn, **kw: [])
    res = MD.run_dream_cycle_legacy(str(path), config=json.loads(Path(cfg).read_text()))
    assert res["status"] == "unconfigured" and res["total_pairs"] == 0, res
    monkeypatch.setattr(MD, "run_dream_cycle", lambda *a, **k: res)
    monkeypatch.setattr(sys, "argv", ["memory_dreaming.py", "run", str(path)])
    with pytest.raises(SystemExit) as ei:
        MD.main()
    assert ei.value.code == 0


def test_the_sentinel_reads_unconfigured_not_starved(brain, world):
    path, _cfg = brain
    _say_not_now(world)
    rs, _meta = S.scan(str(path))
    dream = [r for r in rs if r.organ == "dream_pipeline.phase_rem_dreaming"][0]
    assert dream.verdict == "UNCONFIGURED" and "not now" in dream.detail, dream


def test_guard_without_the_answer_the_dream_watch_still_reads_starved(brain):
    """Guard (GREEN on both): no record -> a 0-attempt window stays STARVED (a missing answer is still loud)."""
    path, _cfg = brain
    rs, _meta = S.scan(str(path))
    assert [r for r in rs if r.organ == "dream_pipeline.phase_rem_dreaming"][0].verdict == "STARVED"


def test_guard_a_quota_night_stays_starved_79(brain, monkeypatch, capsys):
    """Guard: a real quota night keeps STARVED / exit 79 (the round-1 behaviour this round must not lose)."""
    path, cfg = brain
    monkeypatch.setattr(MD._thinker, "think", _raise_quota)
    rc = DP.main(["run", str(path), "--config", str(cfg)])
    res = json.loads(capsys.readouterr().out)
    assert rc == DP.EXIT_STARVED == 79 and res["status"] == "starved" and res["starved_reason"] == "quota", res


def test_a_quota_night_leaves_its_phase_row(brain, monkeypatch, capsys):
    """Round 1's 'starved' phase status was refused by dream_phase_log's CHECK and the row was dropped silently."""
    path, cfg = brain
    monkeypatch.setattr(MD._thinker, "think", _raise_quota)
    DP.main(["run", str(path), "--config", str(cfg)])
    capsys.readouterr()
    row = _one(path, "SELECT status, detail FROM dream_phase_log WHERE phase_name='rem_dreaming'")
    assert row is not None and row[0] == "skipped" and row[1].startswith("starved: quota"), row

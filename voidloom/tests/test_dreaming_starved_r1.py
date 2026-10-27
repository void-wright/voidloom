# -*- coding: utf-8 -*-
"""Round 1, lane L5: when the thinker is UNAVAILABLE (quota, auth, unreachable,
...), dreaming writes no empty dream and exits STARVED, which its units admit.

Measured (health check, 2026-10-14): 45 empty dreams across five cycles
(2026-10-06 18:00 to 10-08 02:00 UTC) while the journal said "thinker
unavailable ... You've hit your weekly limit"; every cycle exited 0.

The thinker is a double raising the seam's own typed error; no model, no
network. Pair selection is a double (the selectors need embeddings).
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

import dream_ledger  # noqa: E402
import dream_pipeline as DP  # noqa: E402
import memory_dreaming as MD  # noqa: E402
import schema_migrator  # noqa: E402
import thinker  # noqa: E402

QUOTA = "You've hit your weekly limit - resets Oct 8, 1pm (fake, test-only)"


def _raise_quota(*a, **k):
    raise thinker.ThinkUnavailable("quota", QUOTA, "double")


def _raise_timeout(*a, **k):
    raise thinker.ThinkTimeout("timeout", "60 s", "double")


def _dream(*a, **k):
    return thinker.ThinkResult(text="RELATION: connection\nThe assay drift and the reagent lot share one cause: "
                                    "temperature excursions during shipping.", backend_id="double",
                               elapsed_s=0.1, mode="text")


A = {"id": 1, "content": "alpha memory about sleep", "summary": "alpha", "category": "insight", "tags": "",
     "created_at": "2026-09-01 00:00:00", "source_project": "p1", "kind": "episodic"}
B = {"id": 2, "content": "beta memory about dreams", "summary": "beta", "category": "insight", "tags": "",
     "created_at": "2026-09-02 00:00:00", "source_project": "p2", "kind": "episodic"}


def test_the_typed_exit_is_one_definition_and_distinct():
    assert MD.EXIT_STARVED == DP.EXIT_STARVED == 79
    assert MD.EXIT_STARVED not in (0, 1, 2, 3, MD.EXIT_SKIPPED_LOCK)


def test_an_unavailable_thinker_is_reported_as_starved_by_the_pair(monkeypatch):
    monkeypatch.setattr(MD._thinker, "think", _raise_quota)
    out, fails = {}, []
    assert MD.dream_on_pair(A, B, "divergent", on_failure=fails.append, out=out) is None
    assert out["starved"]["reason"] == "quota" and fails, out      # the old contract: None + on_failure


def test_a_single_failed_call_is_not_starvation(monkeypatch):
    monkeypatch.setattr(MD._thinker, "think", _raise_timeout)
    out = {}
    assert MD.dream_on_pair(A, B, "divergent", out=out) is None
    assert "starved" not in out


# --------------------------------------------------------------------------
# The cycle: no row for an undreamt pair, status 'starved', exit 79.
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def schema_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "base.db"
    out = schema_migrator.apply(str(path), ROOT / "migrations")
    assert out["complete"], out["files_with_errors"]
    return path


@pytest.fixture()
def brain(schema_db, tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    ids = []
    for i in range(6):
        ids.append(c.execute(
            "INSERT INTO memories (content, summary, category, importance, source_project, kind, created_at, "
            "updated_at, last_accessed) VALUES (?, ?, 'insight', 0.5, ?, 'episodic', datetime('now','-3 days'), "
            "datetime('now'), datetime('now'))", (f"memory {i} about topic {i}", f"m{i}", f"p{i % 2}")).lastrowid)
    c.commit()
    c.close()
    pairs = {"divergent": [(ids[0], ids[1], 0.8, None), (ids[2], ids[3], 0.7, None)],
             "close_context": [(ids[4], ids[5], 0.2, {"diff": "project"})], "persona": [], "notes": {}}
    monkeypatch.setattr(DP, "_rem_pools", lambda conn, cfg, pool: pairs)
    rem = [p for p in DP.PHASES if p.name == "rem_dreaming"]
    monkeypatch.setattr(DP, "PHASES", rem)          # only REM: the phase the 45 empty rows came from
    monkeypatch.setattr(MD, "_store_dream_as_reflection",
                        lambda conn, text, a, b, kind, **kw: conn.execute(
                            "INSERT INTO memories (content, summary, category, importance, kind, created_at, "
                            "updated_at, last_accessed) VALUES (?, 's', 'reflection', 0.4, 'reflection', "
                            "datetime('now'), datetime('now'), datetime('now'))", (text,)).lastrowid)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"lock_file": str(tmp_path / "dream.lock")}))
    return path, cfg


def _rows(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM dream_log").fetchone()[0]
    finally:
        c.close()


def test_a_quota_night_writes_no_dream_and_exits_starved(brain, monkeypatch, capsys):
    path, cfg = brain
    monkeypatch.setattr(MD._thinker, "think", _raise_quota)
    rc = DP.main(["run", str(path), "--config", str(cfg)])
    res = json.loads(capsys.readouterr().out)
    assert rc == DP.EXIT_STARVED, res
    assert res["status"] == "starved" and res["starved_reason"] == "quota"
    rem = [p for p in res["phases"] if p["phase"] == "rem_dreaming"][0]
    assert rem["status"] == "starved" and rem["result"]["attempts"] == 0
    assert _rows(path) == 0                       # was: one empty row per pair
    c = sqlite3.connect(path)
    w = dream_ledger.dream_watch(c, 2.0)
    c.close()
    assert w["verdict"] == "STARVED", w           # the sentinel's dream watch agrees


def test_a_working_thinker_still_dreams(brain, monkeypatch, capsys):
    path, cfg = brain
    monkeypatch.setattr(MD._thinker, "think", _dream)
    rc = DP.main(["run", str(path), "--config", str(cfg)])
    res = json.loads(capsys.readouterr().out)
    assert rc == 0 and res["status"] == "ok", res
    assert _rows(path) == 3


def test_the_legacy_cycle_stops_too(brain, monkeypatch):
    path, cfg = brain
    monkeypatch.setattr(MD._thinker, "think", _raise_quota)
    monkeypatch.setattr(MD, "select_divergent_pairs", lambda conn, **kw: [(1, 2, 0.8), (3, 4, 0.7)])
    monkeypatch.setattr(MD, "select_close_context_pairs", lambda conn, **kw: [])
    monkeypatch.setattr(MD, "select_persona_pairs", lambda conn, **kw: [])
    res = MD.run_dream_cycle_legacy(str(path), config=json.loads(Path(cfg).read_text()))
    assert res["status"] == "starved" and res["total_pairs"] == 0, res
    assert _rows(path) == 0


@pytest.mark.parametrize("unit", ["wizard-library-dreaming.service", "wizard-library-dream-daytime.service"])
def test_both_dream_units_admit_the_starved_exit(unit):
    lines = [l.strip() for l in (ROOT / "cron" / unit).read_text().splitlines()]
    vals = [l.split("=", 1)[1].split() for l in lines if l.startswith("SuccessExitStatus=")]
    assert vals and str(MD.EXIT_STARVED) in vals[0], vals

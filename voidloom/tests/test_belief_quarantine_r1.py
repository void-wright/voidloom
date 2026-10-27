# -*- coding: utf-8 -*-
"""Round 1, lane L5: the belief engine stops authoring same-organ-template
contradictions, and the ones it already authored are QUARANTINED (reversible,
never deleted).

Measured on the live brain (read-only, 2026-10-15): of 3,789 enacted contradicts
pairs, 2,412 joined two 'Distilled from' summaries, 648 two dreams and 467 two
gap-research answers; 0 joined a template to free text; all 174 pending
proposals were same-template pairs. These fed 458 unresolved_contradiction gaps.

No model is loaded: the NLI is a double, the neighbour search is a double, and
torch is never imported (on this host torch 2.6.0+cu124 segfaults on
cuda.is_available() when every card is hidden).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
MIGRATIONS = ROOT / "migrations"
for _p in (str(ORGANS),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import belief_engine as BE  # noqa: E402
import belief_cli  # noqa: E402
import gap_mapping  # noqa: E402
import schema_migrator  # noqa: E402

DISTILLED = "Distilled from {n} episodic memories (2026-09-0{n} to 2026-09-0{n}). Common themes: t{n}. Core insight: x{n}"
DREAM = "[Dream synthesis — divergent]\nSource memories: #{n} and #{m}\n\nBoth memories discuss topic {n}."
GAP = "Auto-researched gap (thin_entity): ENT{n}\n\nENT{n} is a company that does thing {n}."


@pytest.fixture(scope="module")
def schema_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "base.db"
    out = schema_migrator.apply(str(path), MIGRATIONS)
    assert out["complete"], out["files_with_errors"]
    return path


def _mem(conn, content, summary):
    cur = conn.execute(
        "INSERT INTO memories (content, summary, category, importance, source_project, kind, "
        "created_at, updated_at, last_accessed) VALUES (?, ?, 'insight', 0.5, 'p', 'semantic', "
        "datetime('now'), datetime('now'), datetime('now'))", (content, summary))
    return cur.lastrowid


def _authored(conn, mid):
    """Stamp a memory as the store path does: a person wrote it (2026-10-26: the
    belief engine judges only pairs whose both ends a person authored)."""
    conn.execute(
        "INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, dim, content_hash, "
        "embedded_at, organ) VALUES (?, 'memory_embeddings_v2', 'test/model', 'unresolved', 1024, ?, "
        "datetime('now'), 'claude_memory.store')", (mid, "0" * 64))


def _edge_pair(conn, a, b, conf=0.95):
    for s, t in ((a, b), (b, a)):
        conn.execute(
            "INSERT INTO memory_edges (source_id, target_id, predicate, confidence, metadata, extraction_method) "
            "VALUES (?, ?, 'contradicts', ?, '{\"authored_by\":\"belief_engine\"}', 'manual')", (s, t, conf))
    conn.execute(
        "INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, nli_label, nli_score, "
        "cosine, status, report_batch) VALUES ('add_contradicts', ?, ?, 'contradicts', 'contradiction', "
        "0.99, 0.55, 'enacted', 'b0')", (a, b))


def _pending(conn, a, b):
    return conn.execute(
        "INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, nli_label, nli_score, "
        "cosine, status, report_batch) VALUES ('add_contradicts', ?, ?, 'contradicts', 'contradiction', "
        "0.99, 0.55, 'pending', 'b1')", (a, b)).lastrowid


@pytest.fixture()
def brain(schema_db, tmp_path):
    """A brain with one pair per family, one free-text pair, a pending proposal
    per family, and the unresolved_contradiction gaps gap_mapping derives."""
    path = tmp_path / "brain.db"
    shutil.copy(schema_db, path)
    conn = sqlite3.connect(path)
    ids = {}
    ids["d1"] = _mem(conn, DISTILLED.format(n=1), "Consolidated 3 episodic memories: alpha")
    ids["d2"] = _mem(conn, DISTILLED.format(n=2), "Consolidated 3 episodic memories: beta")
    ids["r1"] = _mem(conn, DREAM.format(n=1, m=2), "dream one")
    ids["r2"] = _mem(conn, DREAM.format(n=3, m=4), "dream two")
    ids["g1"] = _mem(conn, GAP.format(n=1), "[gap-research] ENT1")
    ids["g2"] = _mem(conn, GAP.format(n=2), "[gap-research] ENT2")
    ids["f1"] = _mem(conn, "The assay runs at 37 C.", "assay at 37")
    ids["f2"] = _mem(conn, "The assay runs at 4 C.", "assay at 4")
    _authored(conn, ids["f1"])
    _authored(conn, ids["f2"])
    ids["d3"] = _mem(conn, DISTILLED.format(n=3), "Consolidated 3 episodic memories: gamma")
    ids["d4"] = _mem(conn, DISTILLED.format(n=4), "Consolidated 3 episodic memories: delta")
    ids["r3"] = _mem(conn, DREAM.format(n=5, m=6), "dream three")
    ids["r4"] = _mem(conn, DREAM.format(n=7, m=8), "dream four")
    ids["g3"] = _mem(conn, GAP.format(n=3), "[gap-research] ENT3")
    ids["g4"] = _mem(conn, GAP.format(n=4), "[gap-research] ENT4")
    for a, b in (("d1", "d2"), ("r1", "r2"), ("g1", "g2"), ("f1", "f2")):
        _edge_pair(conn, ids[a], ids[b])
    pend = {fam: _pending(conn, ids[a], ids[b])
            for fam, (a, b) in {"sleep_consolidation": ("d3", "d4"), "dream_synthesis": ("r3", "r4"),
                                "gap_research": ("g3", "g4")}.items()}
    conn.commit()
    conn.close()
    gap_mapping.run_gap_inventory(str(path), config={})
    return path, ids, pend


def _q(path, sql, *a):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


# --------------------------------------------------------------------------
# 1. The class is defined by the WRITERS' formats, not by a guess about data.
# --------------------------------------------------------------------------
WRITERS = {
    "sleep_consolidation": ("sleep_loop.py", r'f"(Distilled from \{[^"]*)"'),
    "dream_synthesis": ("memory_dreaming.py", r'f"(\[Dream synthesis[^"]*)"'),
    "gap_research": ("gap_mapping.py", r'f"(Auto-researched gap[^"]*)"'),
}


@pytest.mark.parametrize("family", sorted(WRITERS))
def test_each_family_matches_its_writers_own_format(family):
    fname, rx = WRITERS[family]
    src = (ORGANS / fname).read_text(encoding="utf-8")
    literals = re.findall(rx, src)
    assert literals, f"{fname} no longer writes the {family} template this regex keys on"
    for lit in literals:
        rendered = re.sub(r"\{[^}]*\}", "3", lit).replace("\\n", "\n")
        assert BE.template_family(rendered) == family, (fname, lit)


def test_free_text_and_mixed_pairs_are_not_the_class():
    assert BE.template_family("The assay runs at 37 C.") is None
    assert not BE.same_template("sleep_consolidation", None)
    assert not BE.same_template("sleep_consolidation", "dream_synthesis")
    assert BE.same_template("gap_research", "gap_research")


# --------------------------------------------------------------------------
# 2. The precondition: a same-template pair is never NLI'd, never proposed.
# --------------------------------------------------------------------------
class _NLIScreams:
    """An NLI double that calls EVERYTHING a contradiction (the measured failure)."""
    calls = 0

    def predict(self, pairs, apply_softmax=True):
        type(self).calls += len(pairs)
        return [[0.99, 0.005, 0.005] for _ in pairs]


def test_scan_pair_returns_template_pair_without_nli(brain, monkeypatch):
    path, ids, _ = brain
    conn = sqlite3.connect(path)
    _NLIScreams.calls = 0
    out = BE.scan_pair(conn, ids["d1"], ids["d2"], nli_model=_NLIScreams(), cosine_gate=0.5,
                       precomputed_cosine=0.6)
    assert out["decision"] == BE.TEMPLATE_PAIR_DECISION
    assert _NLIScreams.calls == 0
    free = BE.scan_pair(conn, ids["f1"], ids["f2"], nli_model=_NLIScreams(), cosine_gate=0.5,
                        precomputed_cosine=0.6)
    assert free["decision"] == "contradicts"  # free text is still judged
    conn.close()


def test_nightly_scan_authors_zero_template_pair_proposals(brain, monkeypatch):
    path, ids, _ = brain
    conn = sqlite3.connect(path)
    neighbours = {ids["d1"]: [(ids["d2"], 0.6)], ids["r1"]: [(ids["r2"], 0.6)],
                  ids["g1"]: [(ids["g2"], 0.6)], ids["f1"]: [(ids["f2"], 0.6)]}
    # Drop the existing edges' suppression so the scan has to DECIDE each pair.
    conn.execute("DELETE FROM memory_edges")
    conn.execute("DELETE FROM edge_proposals")
    for m in neighbours:
        conn.execute("INSERT INTO belief_scan_queue (memory_id) VALUES (?)", (m,))
    conn.commit()
    monkeypatch.setattr(BE, "_top_k_neighbors", lambda c, mid, k, gate: neighbours.get(mid, []))
    _NLIScreams.calls = 0
    out = BE.nightly_scan(conn, nli_model=_NLIScreams(), report_batch="t")
    rows = conn.execute("SELECT source_id, target_id FROM edge_proposals").fetchall()
    conn.close()
    assert out["template_pairs_skipped"] == 3
    assert rows == [(ids["f1"], ids["f2"])], rows
    assert _NLIScreams.calls == 2  # the free pair, both directions; no template pair reached NLI


# --------------------------------------------------------------------------
# 3. The quarantine: derived counts, reversible, idempotent, never a DELETE.
# --------------------------------------------------------------------------
FAMS = {"sleep_consolidation": 1, "dream_synthesis": 1, "gap_research": 1}


def test_dry_run_counts_the_class_and_writes_nothing(brain):
    path, ids, _ = brain
    before = _q(path, "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL")[0][0]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = BE.quarantine_template_pairs(conn, apply=False)
    conn.close()
    assert out["pairs"] == FAMS
    assert out["edges"] == {k: 2 for k in FAMS}
    assert out["proposals"] == FAMS
    assert out["gaps"] == {k: 2 for k in FAMS}, out  # one gap per directed edge
    assert out["totals"] == {"pairs": 3, "edges": 6, "proposals": 3, "gaps": 6}
    assert _q(path, "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL")[0][0] == before == 8
    assert _q(path, "SELECT COUNT(*) FROM belief_quarantine")[0][0] == 0


def test_apply_retires_the_class_and_only_the_class(brain):
    path, ids, pend = brain
    n_edges = _q(path, "SELECT COUNT(*) FROM memory_edges")[0][0]
    conn = sqlite3.connect(path)
    out = BE.quarantine_template_pairs(conn, apply=True)
    assert out["written"] == {"edges": 6, "proposals": 3, "gaps": 6}
    again = BE.quarantine_template_pairs(conn, apply=True)
    conn.close()
    assert again["totals"] == {"pairs": 0, "edges": 0, "proposals": 0, "gaps": 0}
    # Nothing deleted, anywhere.
    assert _q(path, "SELECT COUNT(*) FROM memory_edges")[0][0] == n_edges
    # The free pair is still believed; every template edge is retired.
    live = _q(path, "SELECT source_id FROM memory_edges WHERE predicate='contradicts' AND valid_until IS NULL")
    assert sorted(r[0] for r in live) == sorted([ids["f1"], ids["f2"]])
    assert {r[0] for r in _q(path, "SELECT status FROM edge_proposals WHERE id IN (?,?,?)", *pend.values())} \
        == {"rejected"}
    # The old reader of contradictions (gap_mapping, unchanged semantics) sees only the free pair.
    c = sqlite3.connect(path)
    gaps = gap_mapping.detect_unresolved_contradictions(c)
    c.close()
    assert {(g["source_id"], g["target_id"]) for g in gaps} == {(ids["f1"], ids["f2"]), (ids["f2"], ids["f1"])}
    open_gaps = _q(path, "SELECT COUNT(*) FROM gap_inventory WHERE gap_type='unresolved_contradiction' "
                         "AND acknowledged=0")[0][0]
    assert open_gaps == 2


def test_report_is_derived_and_lists_each_pair_once(brain):
    path, ids, _ = brain
    conn = sqlite3.connect(path)
    BE.quarantine_template_pairs(conn, apply=True)
    rep = BE.quarantine_report(conn)
    conn.close()
    assert rep["open_pairs_all_kinds"] == 6  # 3 edge pairs + 3 pending-proposal pairs (round 2, l2: labelled)
    assert rep["open_edge_pairs"] == 3 and rep["open_pairs_without_an_edge"] == 3
    assert rep["counts"]["open"]["edge"] == {k: 2 for k in FAMS}
    assert len({tuple(p["memory_ids"]) for p in rep["pairs"]}) == len(rep["pairs"])


def test_release_pair_restores_and_is_never_requarantined(brain):
    path, ids, _ = brain
    conn = sqlite3.connect(path)
    BE.quarantine_template_pairs(conn, apply=True)
    out = BE.release_quarantine(conn, pair=(ids["d2"], ids["d1"]), note="reviewed: keep")
    assert out["released"] == 4 and out["conflicts"] == []  # 2 edges + their 2 gaps
    again = BE.quarantine_template_pairs(conn, apply=True)
    conn.close()
    assert again["totals"]["edges"] == 0 and again["totals"]["gaps"] == 0
    assert _q(path, "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL AND source_id IN (?,?)",
              ids["d1"], ids["d2"])[0][0] == 2
    assert _q(path, "SELECT COUNT(*) FROM gap_inventory WHERE gap_type='unresolved_contradiction' "
                    "AND acknowledged=0")[0][0] == 4


def test_release_family_restores_pending_proposals_exactly(brain):
    path, ids, pend = brain
    before = _q(path, "SELECT status, decided_at, error_log FROM edge_proposals WHERE id=?",
                pend["dream_synthesis"])
    conn = sqlite3.connect(path)
    BE.quarantine_template_pairs(conn, apply=True)
    out = BE.release_quarantine(conn, family="dream_synthesis")
    conn.close()
    assert out["conflicts"] == []
    assert _q(path, "SELECT status, decided_at, error_log FROM edge_proposals WHERE id=?",
              pend["dream_synthesis"]) == before


def test_release_never_overwrites_a_later_human_change(brain):
    path, ids, _ = brain
    conn = sqlite3.connect(path)
    BE.quarantine_template_pairs(conn, apply=True)
    conn.execute("UPDATE memory_edges SET valid_until = '2030-01-01 00:00:00' WHERE source_id = ? "
                 "AND predicate='contradicts'", (ids["g1"],))
    conn.commit()
    out = BE.release_quarantine(conn, family="gap_research")
    conn.close()
    assert [c["object_kind"] for c in out["conflicts"]] == ["edge"]
    assert _q(path, "SELECT valid_until FROM memory_edges WHERE source_id=? AND predicate='contradicts'",
              ids["g1"]) == [("2030-01-01 00:00:00",)]


def test_release_refuses_without_a_selector(brain):
    path, _, _ = brain
    conn = sqlite3.connect(path)
    assert BE.release_quarantine(conn)["status"] == "error"
    conn.close()


# --------------------------------------------------------------------------
# 4. The CLI: scan applies it nightly; `quarantine` is read-only by default.
# --------------------------------------------------------------------------
def _args(db, action, **kw):
    ns = argparse.Namespace(belief_action=action, db=str(db), json=True, limit=None, neighbor_k=10,
                            apply=False, release_pair=None, release_family=None, release_ids=None,
                            reason=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_cli_scan_applies_the_quarantine(brain, monkeypatch, capsys):
    path, _, _ = brain
    monkeypatch.setattr(BE, "_get_nli_model", lambda model=None: None)  # never load a model
    assert belief_cli.cmd_belief(_args(path, "scan")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["quarantine"]["written"] == {"edges": 6, "proposals": 3, "gaps": 6}
    assert _q(path, "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL")[0][0] == 2


def test_cli_quarantine_default_is_a_dry_run(brain, capsys):
    path, _, _ = brain
    assert belief_cli.cmd_belief(_args(path, "quarantine")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "dry-run" and out["would_quarantine"]["totals"]["pairs"] == 3
    assert _q(path, "SELECT COUNT(*) FROM belief_quarantine")[0][0] == 0
    assert belief_cli.cmd_belief(_args(path, "quarantine", apply=True)) == 0
    capsys.readouterr()
    assert belief_cli.cmd_belief(_args(path, "quarantine", release_family="sleep_consolidation")) == 0
    rel = json.loads(capsys.readouterr().out)
    assert rel["action"] == "release" and rel["released"] == 5  # 2 edges + 1 proposal + 2 gaps


def test_cli_names_the_missing_migration(schema_db, tmp_path, capsys):
    path = tmp_path / "old.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    c.execute("DROP TABLE belief_quarantine")
    c.commit()
    c.close()
    assert belief_cli.cmd_belief(_args(path, "quarantine")) == 1
    assert "migration_041" in json.loads(capsys.readouterr().out)["message"]


# --------------------------------------------------------------------------
# 5. Migration 041 is additive and applied once.
# --------------------------------------------------------------------------
def test_migration_041_is_additive_and_idempotent(schema_db, tmp_path):
    path = tmp_path / "again.db"
    shutil.copy(schema_db, path)
    out = schema_migrator.apply(str(path), MIGRATIONS)
    assert out["complete"]
    assert "migration_041_belief_quarantine.sql" in out["skipped_already_applied"], out["skipped_already_applied"]
    sql = (MIGRATIONS / "migration_041_belief_quarantine.sql").read_text()
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert not re.search(r"\b(DROP|DELETE|UPDATE|ALTER)\b", body, re.I)

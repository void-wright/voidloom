# -*- coding: utf-8 -*-
"""Round 1, lane L5: a research reply that is a NON-ANSWER is never stored as
knowledge.

The health check found replies such as "I can't tell which entity you mean" in
the brain as `domain_knowledge`. Re-derived on the live brain (read-only,
2026-10-15) with the gate's rules: 55 of 270 stored replies. The thinker is a
double, the Warden is off and the embedding attach is a no-op: no model, no
network.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import gap_mapping as GM  # noqa: E402
import schema_migrator  # noqa: E402

# Shapes taken from replies stored in the live brain (entities replaced).
NON_ANSWERS = {
    "protocol": "NO_ANSWER: no company, product or term called ZQX exists in any source I searched.",
    "asks-which": ('"Zqx" is ambiguous to me as stated - could you clarify what you mean?\n\n'
                   '- A company literally named "Zqx"\n- Zqx as a lab workflow'),
    "no-facts": ("I couldn't identify what \"ZQX\" refers to, so I have nothing entity-specific to report. "
                 "Two searches returned only general industry coverage.\n\n" + "## Possible meanings\n- x\n" * 60),
    "short": "I couldn't find any real company, product or term called \"zqx-lane-drive\". It looks internal.",
    "no-substitute": ("I ran several targeted searches and couldn't find any verifiable company, product, drug, "
                      "gene, ticker, or defined acronym called \"ZQX\" in any field I could search. The only "
                      "results were unrelated.\n\n" + "- An unrelated hit about something else entirely.\n" * 30),
}
ANSWERS = {
    "a plain brief": ("## ZQX Therapeutics - research brief\n\n**Key facts**\n- Clinical-stage biotech founded "
                      "2019, Boston.\n- Lead asset ZQX-01 in Phase 2 for AML.\n" * 10),
    "a substitute it covers": ("# AD1 research brief\n\n**Which \"AD1\" this covers.** I couldn't find a biotech "
                               "company, drug or product called \"AD1.\" The main medical meaning is Familial "
                               "Alzheimer Disease type 1, so this brief covers that.\n\n" + "- a fact\n" * 400),
}


@pytest.mark.parametrize("rule", sorted(NON_ANSWERS))
def test_each_non_answer_shape_is_refused(rule):
    v = GM.classify_research_output(NON_ANSWERS[rule])
    assert v["answer"] is False and v["rule"] == rule, v


@pytest.mark.parametrize("name", sorted(ANSWERS))
def test_answers_are_kept(name):
    assert GM.classify_research_output(ANSWERS[name])["answer"] is True


def test_empty_reply_is_not_an_answer():
    assert GM.classify_research_output("   ")["answer"] is False


def test_the_prompt_asks_for_the_protocol_line():
    p = GM._build_research_prompt("ZQX", "thin_entity")
    assert GM.NO_ANSWER_SENTINEL in p


# --------------------------------------------------------------------------
# The store path.
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
    gid = c.execute("INSERT INTO gap_inventory (entity, gap_type, score, auto_research_eligible) "
                    "VALUES ('ZQX', 'thin_entity', 1.0, 1)").lastrowid
    c.commit()
    c.close()
    monkeypatch.setattr(GM._thinker, "read_record", lambda *a, **k: {"backend": "double"})
    monkeypatch.setattr(GM._thinker, "load_catalog", lambda *a, **k: {"backends": {"double": {"web": True}}})
    monkeypatch.setattr(GM, "_warden", None)
    monkeypatch.setattr(GM, "_vl_attach", lambda *a, **k: {"embedded": False})
    return path, gid


def _reply(monkeypatch, text):
    monkeypatch.setattr(GM, "_dispatch_research", lambda prompt, timeout_seconds=300: {
        "status": "success", "output": text, "wall_clock_seconds": 1.0, "backend": "double"})


def _one(path, sql, *a):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql, a).fetchone()
    finally:
        c.close()


@pytest.mark.parametrize("rule", sorted(NON_ANSWERS))
def test_a_non_answer_is_never_stored(brain, monkeypatch, rule):
    path, gid = brain
    _reply(monkeypatch, NON_ANSWERS[rule])
    out = GM.dispatch_auto_research(str(path), gap_ids=[gid])
    assert out["results"][0]["status"] == "no_answer", out
    assert _one(path, "SELECT COUNT(*) FROM memories WHERE content LIKE 'Auto-researched gap%'")[0] == 0
    researched, ack, reason = _one(path, "SELECT researched, acknowledged, acknowledged_reason "
                                         "FROM gap_inventory WHERE id=?", gid)
    assert (researched, ack) == (0, 1) and reason.startswith("no-answer: ")
    status, msg = _one(path, "SELECT status, error_message FROM gap_research_log WHERE gap_id=?", gid)
    assert status == "skipped" and msg.startswith("NO ANSWER [%s]" % rule)
    # Acknowledged: the next run does not ask about the same non-entity again.
    again = GM.dispatch_auto_research(str(path))
    assert again.get("dispatched", 0) == 0


@pytest.mark.parametrize("name", sorted(ANSWERS))
def test_an_answer_is_still_stored_and_retires_its_gap(brain, monkeypatch, name):
    path, gid = brain
    _reply(monkeypatch, ANSWERS[name])
    out = GM.dispatch_auto_research(str(path), gap_ids=[gid])
    assert out["results"][0]["status"] == "success"
    mid = _one(path, "SELECT research_memory_id FROM gap_inventory WHERE id=? AND researched=1", gid)[0]
    assert _one(path, "SELECT content FROM memories WHERE id=?", mid)[0].startswith("Auto-researched gap (")


# --------------------------------------------------------------------------
# The class at rest: the sweep archives (never deletes) and is a read by default.
# --------------------------------------------------------------------------
def test_sweep_lists_by_default_and_archives_on_apply(schema_db, tmp_path):
    path = tmp_path / "sweep.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    for body in list(NON_ANSWERS.values()) + list(ANSWERS.values()):
        c.execute("INSERT INTO memories (content, summary, category, importance, source_project, kind, "
                  "created_at, updated_at, last_accessed) VALUES (?, 's', 'domain_knowledge', 0.5, "
                  "'gap-research', 'semantic', datetime('now'), datetime('now'), datetime('now'))",
                  ("Auto-researched gap (thin_entity): ZQX\n\n" + body,))
    c.commit()
    c.close()
    dry = GM.sweep_stored_non_answers(str(path))
    assert (dry["stored_replies"], dry["non_answers"], dry["applied"]) == (7, 5, False)
    assert _one(path, "SELECT COUNT(*) FROM memories WHERE is_archived=1")[0] == 0
    wet = GM.sweep_stored_non_answers(str(path), apply=True)
    assert wet["applied"] and wet["non_answers"] == 5
    assert _one(path, "SELECT COUNT(*) FROM memories")[0] == 7  # nothing deleted
    assert _one(path, "SELECT COUNT(*) FROM memories m JOIN memory_tags t ON t.memory_id=m.id "
                      "WHERE m.is_archived=1 AND t.tag=?", GM.NON_ANSWER_TAG)[0] == 5
    assert GM.sweep_stored_non_answers(str(path))["non_answers"] == 0

# -*- coding: utf-8 -*-
"""Round 3 G2 item 4 (model-choice; R2-05's sibling): the counterfactual revisit's answer -- the dream parser's
classes, applied to this organ's JSON.

The prompt shows its answer shape with two placeholders: {"outcome": "<one of: validated, still_active, reversed,
partially_wrong, context_changed>", "notes": "<2-3 sentence assessment>"}. On the N bytes (voidloom f8f0c30 =
artifacts_r2) a reply that echoed the NOTES placeholder beside a valid outcome was recorded as the assessment -- and
'validated' / 'reversed' are TERMINAL states: a decision could be closed for good on template text. A genuine answer
decorated the way models decorate (Validated, `reversed`, "partially wrong", the JSON inside prose) was lost to
'invalid outcome' / 'not parseable'.

  REJECTED (still_active + a note that says what came back): a placeholder echoed in either field, the shape's own
  words, notes missing / empty / not text.
  READ: the outcome in another case, quoted, in backticks, with a space or a hyphen for the underscore; the JSON
  inside prose or a fence.
No model: the seam is stubbed. RED rows are RED on the N bytes; the controls are GREEN on both.
"""
import json
import sqlite3

import pytest

import counterfactual_memory as cm

CF = {"id": 1, "topic": "A or B", "chosen_path": "A", "alternative_paths": [{"path": "B"}], "revisit_notes": []}
GOOD = "We chose A, and the six weeks since show the cost we feared from B never arrived; A holds."


def _analysis(monkeypatch, reply):
    th = cm._thinker
    monkeypatch.setattr(th, "think", lambda prompt, **kw: th.ThinkResult(reply, "stub", "m", 0.1, "text"))
    return cm._llm_revisit_analysis(dict(CF))


@pytest.mark.parametrize("reply", [
    json.dumps({"outcome": "validated", "notes": "<2-3 sentence assessment>"}),
    json.dumps({"outcome": "reversed", "notes": "  <a 2-3 sentence assessment of the decision>  "}),
    json.dumps({"outcome": "validated", "notes": "2-3 sentence assessment"}),
    json.dumps({"outcome": "<one of: validated, still_active, reversed, partially_wrong, context_changed>",
                "notes": GOOD}),
    json.dumps({"outcome": "validated"}),
    json.dumps({"outcome": "validated", "notes": ""}),
    json.dumps({"outcome": "reversed", "notes": ["a", "list"]}),
], ids=["notes-placeholder-verbatim", "notes-placeholder-paraphrased", "notes-shape-words", "outcome-placeholder",
        "notes-missing", "notes-empty", "notes-not-text"])
def test_a_reply_that_is_the_shape_not_an_assessment_never_closes_a_decision(monkeypatch, reply):
    r = _analysis(monkeypatch, reply)
    assert r["outcome"] == "still_active", r
    assert "2-3 sentence assessment" != r["notes"].strip("<> ") and r["notes"].startswith(("LLM analysis rejected",
                                                                                           "LLM returned invalid")), r


@pytest.mark.parametrize("reply,outcome", [
    (json.dumps({"outcome": "Validated", "notes": GOOD}), "validated"),
    (json.dumps({"outcome": "`reversed`", "notes": GOOD}), "reversed"),
    (json.dumps({"outcome": "partially wrong", "notes": GOOD}), "partially_wrong"),
    (json.dumps({"outcome": "context-changed", "notes": GOOD}), "context_changed"),
    ("Here is my assessment: " + json.dumps({"outcome": "still_active", "notes": GOOD}) + " Thanks.", "still_active"),
    ("I weighed {the two paths} first.\n" + json.dumps({"outcome": "validated", "notes": GOOD}), "validated"),
], ids=["capitalised", "backticks", "space-for-underscore", "hyphen-for-underscore", "json-inside-prose",
        "a-brace-in-prose-before-the-json"])
def test_a_decorated_genuine_answer_is_read(monkeypatch, reply, outcome):
    assert _analysis(monkeypatch, reply) == {"outcome": outcome, "notes": GOOD}


@pytest.mark.parametrize("reply,outcome", [
    (json.dumps({"outcome": "validated", "notes": GOOD}), "validated"),
    ("```json\n" + json.dumps({"outcome": "reversed", "notes": GOOD}) + "\n```", "reversed"),
    (json.dumps({"outcome": "still_active", "notes": "The note mentions <angle text> inline, which is fine here."}),
     "still_active"),
], ids=["plain", "fenced", "inline-angle-text-is-not-a-placeholder"])
def test_genuine_answers_are_kept(monkeypatch, reply, outcome):
    r = _analysis(monkeypatch, reply)
    assert r["outcome"] == outcome and "rejected" not in r and not r["notes"].startswith("LLM"), r


@pytest.mark.parametrize("reply", ["not json at all", json.dumps({"verdict": "validated", "notes": GOOD}),
                                   json.dumps({"outcome": "maybe", "notes": GOOD})],
                         ids=["no-json", "no-outcome-key", "unknown-outcome"])
def test_an_unreadable_answer_degrades_to_still_active(monkeypatch, reply):
    assert _analysis(monkeypatch, reply)["outcome"] == "still_active"


def test_end_to_end_a_placeholder_validated_leaves_the_decision_in_rotation(tmp_path, monkeypatch):
    """The unit's own entry point on a scratch brain: the echoed 'validated' must not reach the TERMINAL state."""
    db = tmp_path / "brain.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT, is_archived INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    c.execute("INSERT INTO memories (id, summary, content) VALUES (1, 'a decision', 'we chose A over B')")
    c.commit()
    c.close()
    assert cm.apply_counterfactual_migration(str(db))["status"] == "ok"
    conn = cm._get_conn(str(db))
    assert cm.capture_counterfactual(conn, 1, "A or B", "A", [{"path": "B", "predicted_outcome": "x",
                                                                "rationale": "y"}], council_weight_score=0.9)["status"] == "ok"
    conn.execute("UPDATE counterfactual_decisions SET captured_at = '2025-01-01 00:00:00'")
    conn.commit()
    conn.close()
    th = cm._thinker
    reply = json.dumps({"outcome": "validated", "notes": "<2-3 sentence assessment>"})
    monkeypatch.setattr(th, "think", lambda prompt, **kw: th.ThinkResult(reply, "stub", "m", 0.1, "text"))
    res = cm.run_quarterly_revisit(str(db), use_llm=True)
    row = sqlite3.connect(str(db)).execute("SELECT status, revisit_notes FROM counterfactual_decisions").fetchone()
    note = json.loads(row[1])[-1]
    assert res["status"] == "ok" and row[0] == "revisited" and note["outcome"] == "still_active", (res, row)
    assert "placeholder" in note["notes"], note

# -*- coding: utf-8 -*-
"""Narrow pass N4 (model-choice; the Round 2 reflector's R2-05, the LOW that bears on the proof nights): a dream reply
that echoes the prompt's answer SHAPE is not a dream, and a RELATION word written in the prompt's own brackets is read.

The prompt ends "Otherwise answer in exactly this shape:" and two template lines -- `RELATION: <one word -- ...>` and
`<a single concise paragraph (2-4 sentences) stating the insight>`. On the Round 2 bytes (executed offline by the
reflector, `dream_on_pair` with the seam stubbed):
  * the two template lines echoed verbatim were KEPT as a dream (the body was the template);
  * `RELATION: <contradiction>` / `RELATION: [tension]` + a genuine body were read as `connection` (the tension edge was
    lost) and the header line stayed in the stored body.
Each row is RED on those bytes and GREEN after; the placeholder row and the genuine control are unchanged.
"""
import pytest

import memory_dreaming as md

A = {"id": 1, "summary": "the watcher fix for the approvals queue", "content": "x", "category": "insight", "tags": [],
     "source_project": "p", "created_at": "2026-09-01 00:00:00", "importance": 0.8, "kind": "semantic"}
B = {"id": 2, "summary": "a stale pointer outlived its context", "content": "y", "category": "insight", "tags": [],
     "source_project": "q", "created_at": "2026-09-02 00:00:00", "importance": 0.8, "kind": "semantic"}
GENUINE = ("The watcher fix assumes the queue is drained nightly, while the stale-pointer note shows a captured value "
           "outliving the drain.")


def _template_lines():
    t = md._build_dream_prompt("divergent", A, B, None, 300)
    lines = [l for l in t.splitlines() if l.startswith("RELATION: <") or l.startswith("<a single")]
    assert len(lines) == 2, lines          # the prompt still carries the shape this test echoes
    return lines


def _dream(monkeypatch, reply):
    th = md._thinker
    monkeypatch.setattr(th, "think", lambda prompt, **kw: th.ThinkResult(reply, "stub", "m", 0.1, "text"))
    fails, out = [], {}
    body = md.dream_on_pair(A, B, "divergent", on_failure=fails.append, out=out)
    return body, out.get("relation"), fails


def test_the_two_template_lines_echoed_verbatim_take_the_loud_path(monkeypatch):
    body, _rel, fails = _dream(monkeypatch, "\n".join(_template_lines()))
    assert body is None, body
    assert fails and "placeholder" in fails[0], fails


@pytest.mark.parametrize("header,relation", [("RELATION: <contradiction>", "contradiction"),
                                             ("RELATION: [tension]", "tension"),
                                             ("RELATION: (question)", "question"),
                                             ("**RELATION:** <precedent>.", "precedent")],
                         ids=["angle", "square", "round", "bold-angle"])
def test_a_bracketed_relation_word_is_read_and_the_header_leaves_the_body(monkeypatch, header, relation):
    body, rel, fails = _dream(monkeypatch, "%s\n%s" % (header, GENUINE))
    assert (rel, body, fails) == (relation, GENUINE, []), (rel, body, fails)


def test_the_relation_word_then_the_placeholder_still_takes_the_loud_path(monkeypatch):
    body, rel, fails = _dream(monkeypatch, "RELATION: tension\n" + _template_lines()[1])
    assert body is None and rel == "tension" and fails and "placeholder" in fails[0], (body, rel, fails)


def test_a_placeholder_line_below_a_genuine_sentence_takes_the_loud_path(monkeypatch):
    body, _rel, fails = _dream(monkeypatch, "RELATION: tension\n%s\n%s" % (GENUINE, _template_lines()[1]))
    assert body is None and fails, (body, fails)


def test_a_body_that_is_a_second_relation_header_takes_the_loud_path(monkeypatch):
    body, _rel, fails = _dream(monkeypatch, "RELATION: tension\nRELATION: connection")
    assert body is None and fails, (body, fails)


@pytest.mark.parametrize("reply,relation", [
    ("RELATION: tension\n" + GENUINE, "tension"),
    ("RELATION: tension. " + GENUINE, "tension"),
    (GENUINE, "connection"),
    ("RELATION: contradiction\nThe note on <b> tags says one thing; the renderer note says another, about sanitising.",
     "contradiction"),
], ids=["header-line", "one-line", "no-header", "inline-angle-text-is-not-a-placeholder"])
def test_genuine_replies_are_kept_unchanged(monkeypatch, reply, relation):
    body, rel, fails = _dream(monkeypatch, reply)
    assert body and rel == relation and fails == [] and not body.upper().startswith("RELATION"), (body, rel, fails)


def test_null_is_still_null_with_a_bracketed_word(monkeypatch):
    body, rel, fails = _dream(monkeypatch, "RELATION: <connection>\nNULL")
    assert body is None and fails == [], (body, rel, fails)

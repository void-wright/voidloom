# -*- coding: utf-8 -*-
"""Round 3 G2 item 1 (model-choice; ruler VL-MC-v1.2 + VL-DF-v1.1): the dream reply's SHAPE, fixed as CLASSES.

The re-check of narrow pass N (rN_recheck RC-03, RC-04) found new members of R2-05's class on the N bytes: three reply
shapes still STORED template junk as a dream, and two genuine shapes (the relation word in `backticks` or <<double
brackets>>) took the loud path, so the dream was lost. The finder's coverage is bounded by its suspicion; these rows
are the CLASS definitions, swept:

  CLASS 1 -- DECORATION around the header or its word. A model writes the relation word the way it writes any
            emphasised word: the prompt's own brackets, markdown (`code`, **bold**, _italic_), quotes, doubled or
            unusual brackets, the whole header as a quote / bullet / heading / code span, an em-dash separator.
            Every such shape is READ (the right relation, the header OUT of the body) when the decoration is
            BALANCED. An opener never closed is the template's placeholder, not a word: the loud path.
  CLASS 2 -- TEMPLATE JUNK: any reply that reproduces the prompt's fixed answer-shape text (its two placeholders or
            its instruction lines), verbatim or decorated, at any position, or that carries a second RELATION
            header (a preface before it, or a header mid-body) takes the LOUD path. A header echoed at the TAIL is
            stripped and the dream kept. A reply fenced in ``` is unwrapped.
  CLASS 3 -- NULL said with an explanation is a NULL, not a 60-character dream.

Every RED row was RED on the N bytes (artifacts_r2 = voidloom f8f0c30); every row is GREEN on Round 3's. The
controls (genuine replies) are GREEN on both.
"""
import pytest

import memory_dreaming as md

A = {"id": 1, "summary": "the watcher fix for the approvals queue", "content": "x", "category": "insight", "tags": [],
     "source_project": "p", "created_at": "2026-09-01 00:00:00", "importance": 0.8, "kind": "semantic"}
B = {"id": 2, "summary": "a stale pointer outlived its context", "content": "y", "category": "insight", "tags": [],
     "source_project": "q", "created_at": "2026-09-02 00:00:00", "importance": 0.8, "kind": "semantic"}
GENUINE = ("The watcher fix assumes the queue is drained nightly, while the stale-pointer note shows a captured value "
           "outliving the drain.")


def _shape_lines():
    """The prompt's own answer-shape lines, read from the prompt the organ builds (never typed here)."""
    t = md._build_dream_prompt("divergent", A, B, None, 300)
    tail = t[t.index("Otherwise answer in exactly this shape:"):].splitlines()
    assert tail[1].startswith("RELATION: <") and tail[2].startswith("<a single"), tail
    return tail      # [Otherwise..., RELATION: <one word ...>, <a single ...>, Name each memory ...]


def _dream(monkeypatch, reply):
    th = md._thinker
    monkeypatch.setattr(th, "think", lambda prompt, **kw: th.ThinkResult(reply, "stub", "m", 0.1, "text"))
    fails, out = [], {}
    body = md.dream_on_pair(A, B, "divergent", on_failure=fails.append, out=out)
    return body, out.get("relation"), fails


# ---------------------------------------------------------------- CLASS 1: decoration is read when balanced
DECORATED = [
    ("RELATION: `contradiction`", "contradiction"),       # RC-04 (lost to the loud path on the N bytes)
    ("RELATION: <<tension>>", "tension"),                 # RC-04
    ('RELATION: "question"', "question"),
    ("RELATION: 'precedent'", "precedent"),
    ("RELATION: _tension_", "tension"),
    ("RELATION: «tension»", "tension"),
    ("RELATION: “tension”", "tension"),
    ("RELATION: [[question]]", "question"),
    ("RELATION: {tension}", "tension"),
    ("RELATION: <`tension`>", "tension"),
    ("RELATION: <**contradiction**>", "contradiction"),
    ("`RELATION: tension`", "tension"),
    ("`RELATION`: tension", "tension"),
    ("> RELATION: tension", "tension"),
    ("- RELATION: tension", "tension"),
    ("## RELATION: tension", "tension"),
    ("RELATION — tension", "tension"),
    ("RELATION – precedent", "precedent"),
    # already read on the N bytes: kept as controls so the widening cannot lose them
    ("RELATION: **tension**", "tension"),
    ("**RELATION: tension**", "tension"),
    ("**RELATION:** <precedent>.", "precedent"),
    ("Relation: Tension", "tension"),
    ("RELATION:tension", "tension"),
    ("RELATION: <contradiction>", "contradiction"),
    ("RELATION: [tension]", "tension"),
]


@pytest.mark.parametrize("header,relation", DECORATED, ids=[h for h, _ in DECORATED])
def test_a_decorated_relation_word_is_read_and_the_header_leaves_the_body(monkeypatch, header, relation):
    body, rel, fails = _dream(monkeypatch, "%s\n%s" % (header, GENUINE))
    assert (rel, body, fails) == (relation, GENUINE, []), (rel, body, fails)


@pytest.mark.parametrize("header,relation", [("RELATION: `tension`.", "tension"), ("RELATION: <<question>> --", "question"),
                                             ("**RELATION:** _contradiction_:", "contradiction")],
                         ids=["backtick-dot", "double-angle-dash", "bold-key-italic-word"])
def test_a_decorated_word_on_one_line_with_the_body(monkeypatch, header, relation):
    body, rel, fails = _dream(monkeypatch, "%s %s" % (header, GENUINE))
    assert (rel, body, fails) == (relation, GENUINE, []), (rel, body, fails)


# ---------------------------------------------------------------- CLASS 2: template junk is never stored
def _junk_rows():
    ot, hdr, ph, name = _shape_lines()
    return [
        ("template-header-verbatim+genuine", "%s\n%s" % (hdr, GENUINE)),                 # RC-03 row 1
        ("template-body-quoted", "> " + ph),                                              # RC-03 row 2
        ("template-body-bulleted", "- " + ph),
        ("template-body-in-code", "`%s`" % ph),
        ("template-body-bold-under-header", "RELATION: tension\n**%s**" % ph),
        ("template-body-quoted-under-header", "RELATION: tension\n> %s\n%s" % (ph, GENUINE)),
        ("instruction-line-echoed", "RELATION: tension\n%s\n%s" % (GENUINE, name)),
        ("shape-intro-echoed", "RELATION: tension\n%s\n%s" % (ot, GENUINE)),
        ("placeholder-paraphrased-in-prose", "RELATION: tension\nHere is a single concise paragraph (2-4 sentences) "
                                             "on the two notes: " + GENUINE),
        ("header-word-unclosed", "RELATION: <one word\n" + GENUINE),
        ("second-header-mid-body", "RELATION: tension\n%s\nRELATION: contradiction\n%s" % (GENUINE, GENUINE)),
        ("preface-before-header", "Here is the insight:\nRELATION: tension\n" + GENUINE),
    ]


JUNK_IDS = ["template-header-verbatim+genuine", "template-body-quoted", "template-body-bulleted",
            "template-body-in-code", "template-body-bold-under-header", "template-body-quoted-under-header",
            "instruction-line-echoed", "shape-intro-echoed", "placeholder-paraphrased-in-prose",
            "header-word-unclosed", "second-header-mid-body", "preface-before-header"]


@pytest.mark.parametrize("row", JUNK_IDS)
def test_template_junk_takes_the_loud_path(monkeypatch, row):
    reply = dict(_junk_rows())[row]
    body, _rel, fails = _dream(monkeypatch, reply)
    assert body is None and len(fails) == 1, (row, body, fails)


@pytest.mark.parametrize("reply,relation", [
    ("RELATION: tension\n%s\n\nRELATION: tension" % GENUINE, "tension"),                  # RC-03 row 3
    ("RELATION: tension\n%s\n**RELATION:** `tension`" % GENUINE, "tension"),
    ("%s\n\nRELATION: contradiction" % GENUINE, "contradiction"),                          # the header put LAST
], ids=["trailing-echo", "trailing-decorated-echo", "header-at-the-tail-only"])
def test_a_header_echoed_at_the_tail_is_stripped_and_the_dream_kept(monkeypatch, reply, relation):
    body, rel, fails = _dream(monkeypatch, reply)
    assert (rel, body, fails) == (relation, GENUINE, []), (rel, body, fails)


@pytest.mark.parametrize("reply", ["```\nRELATION: tension\n%s\n```" % GENUINE,
                                   "```text\nRELATION: tension\n%s\n```" % GENUINE],
                         ids=["fence", "fence-with-language"])
def test_a_fenced_reply_is_unwrapped(monkeypatch, reply):
    body, rel, fails = _dream(monkeypatch, reply)
    assert (rel, body, fails) == ("tension", GENUINE, []), (rel, body, fails)


# ---------------------------------------------------------------- CLASS 3: NULL with an explanation is NULL
@pytest.mark.parametrize("reply", [
    "NULL\n\nThese two memories genuinely have nothing interesting to say to each other.",
    "RELATION: connection\nNULL -- they are only about the same topic, nothing deeper.",
    "RELATION: connection\nNULL: the two notes share a word and nothing else at all.",
    "**NULL**", "`NULL`", "NULL.",
], ids=["null-then-why", "header-null-dash-why", "header-null-colon-why", "bold", "code", "dot"])
def test_null_said_with_an_explanation_is_null(monkeypatch, reply):
    body, _rel, fails = _dream(monkeypatch, reply)
    assert body is None and fails == [], (body, fails)


# ---------------------------------------------------------------- controls: genuine replies are kept unchanged
@pytest.mark.parametrize("reply,relation,want", [
    ("RELATION: tension\n" + GENUINE, "tension", GENUINE),
    ("RELATION: tension. " + GENUINE, "tension", GENUINE),
    (GENUINE, "connection", GENUINE),
    ("RELATION: tension `setdefault` yields to an existing empty value, just as the drain yields to a stale pointer.",
     "tension", "`setdefault` yields to an existing empty value, just as the drain yields to a stale pointer."),
    ('RELATION: tension "Drained" means two things here: the queue note says nightly, the pointer note says never.',
     "tension", '"Drained" means two things here: the queue note says nightly, the pointer note says never.'),
    ("RELATION: contradiction\nThe note on <b> tags says one thing; the renderer note says another, about sanitising.",
     "contradiction", "The note on <b> tags says one thing; the renderer note says another, about sanitising."),
    ("RELATION: tension\nNULL values in the dream log mirror the stale pointer: both record an absence as data.",
     "tension", "NULL values in the dream log mirror the stale pointer: both record an absence as data."),
    ("RELATION: synergy\n" + GENUINE, "connection", GENUINE),
    ("RELATION: counter-example\n" + GENUINE, "connection", GENUINE),
    ("RELATION: tension\n%s\n\n%s" % (GENUINE, GENUINE), "tension", "%s\n\n%s" % (GENUINE, GENUINE)),
    ("RELATION: question\nWhat relation holds between the drain and the pointer? " + GENUINE, "question",
     "What relation holds between the drain and the pointer? " + GENUINE),
], ids=["header-line", "one-line", "no-header", "body-opens-with-backtick", "body-opens-with-quote",
        "inline-angle-text", "body-opens-with-the-word-NULL", "unknown-word", "hyphenated-unknown",
        "two-paragraphs", "prose-says-relation"])
def test_genuine_replies_are_kept_unchanged(monkeypatch, reply, relation, want):
    body, rel, fails = _dream(monkeypatch, reply)
    assert (rel, body, fails) == (relation, want, []), (rel, body, fails)


def test_the_store_path_reads_the_same_body_the_dream_path_returned():
    """_store_dream_as_reflection re-parses what dream_on_pair returned: the parse is idempotent on a body."""
    for reply in ("RELATION: `contradiction`\n" + GENUINE, "RELATION: tension\n%s\n\nRELATION: tension" % GENUINE):
        rel, body = md.parse_relation(reply)
        assert md.parse_relation(body) == ("connection", body), (reply, body)


def test_the_pipeline_mouth_path_shares_the_parse():
    """dream_pipeline's mockable mouth goes through the same parse + NULL rule (its docstring's promise)."""
    import dream_pipeline as dp
    for raw, want in (("RELATION: `contradiction`\n" + GENUINE, (GENUINE, "contradiction")),
                      ("NULL\n\nThese memories have nothing to say to each other, honestly.", (None, "connection"))):
        cfg = {"_synthesis_mouth": lambda *_a, _r=raw: _r}
        assert dp._safe_dream(None, cfg, 1, 2, "divergent") == want, raw

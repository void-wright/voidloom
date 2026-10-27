# -*- coding: utf-8 -*-
"""The proof measures two things in TWO calls (M9/M18, model-choice round 1 Wave C): a tool-less model must still give
the answer, and a model that CAN write must still be caught.

MEASURED LIVE on the real claude CLI with the text posture (`--tools ""`, one turn): a probe that both invited a file
and asked for the word made the tool-less model try the file first and never say the word -- it narrates a tool call
("I'll create the requested empty file now.", "<tool_call>{"name":"Write"...", "Write(voidlo...") and the one turn
ends there. The owner's valid credential FAILED the proof that gates recording (M18 attempt 2). Three wordings, 13
sampled live calls, 5 failed (iterations/scratch_records/r1_waveC/probe_ab.log, probe_ab2.log), after two failed `set`s. Now: the ANSWER call asks for
the answer alone (the word until Round 1 iteration 2; since then the exact answer to 'What is N plus 1?'); the TOOL call asks for a file and judges only the folder, never the text.

Bound with doubles that behave the way the model did:
  * NARRATOR: asked to create a file, it narrates a tool call and says no word; asked for the word alone, it answers.
    The two-call probe PASSES it (the answer came back; the folder stayed empty) -- the old one-call wording, put back,
    FAILS it: the red.
  * WRITER: answers the word perfectly, and really creates the file when asked. The two-call probe REFUSES it (tool
    block BROKE) -- the positive control that the split did not blind the tool block.
  * a credential-only check (tool_block=False: the login's proof, the weekly check) makes exactly ONE call.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
OLD = ("This is a connection check. First, if you have any tool that can create files, create a file "
       "named voidloom-probe-old.txt in the current directory. Then: What is {n} plus 1? Reply with only the number.")
DOUBLE = r'''#!/usr/bin/env python3
import json, os, re, sys
MODE = %r
LOG = %r
p = sys.argv[-1] if len(sys.argv) > 1 else ""
open(LOG, "a").write("call\n")
m = re.search(r"What is (\d+) plus 1\?", p)
asked_file = re.search(r"create (?:a|an empty) file named (\S+)", p, re.I)
if asked_file and MODE == "narrator":
    text = "I'll create the requested empty file now.\n\nWrite(file_path=%%r, content=\"\")" %% asked_file.group(1)
elif asked_file and MODE == "writer":
    open(asked_file.group(1).rstrip(".,"), "w").write("")
    text = "done"
else:
    text = str(int(m.group(1)) + 1) if m else "?"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}))
'''


def _world(monkeypatch, mode):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_probe_wording_", dir=str(SCRATCH)))
    world = F.world(monkeypatch, root)
    fake = world.bin / "claude"
    fake.write_text(DOUBLE % (mode, str(root / "calls.log")), encoding="utf-8")
    fake.chmod(0o755)
    F.save_cred(world, "claude")
    return world


@pytest.fixture
def narrator(monkeypatch):
    w = _world(monkeypatch, "narrator")
    yield w
    shutil.rmtree(str(w.root), ignore_errors=True)


@pytest.fixture
def writer(monkeypatch):
    w = _world(monkeypatch, "writer")
    yield w
    shutil.rmtree(str(w.root), ignore_errors=True)


def _probe(**kw):
    cat = thinker.load_catalog()
    return thinker.probe_candidate(thinker.make_candidate(cat, "claude"), timeout_s=60, cat=cat, **kw)


def _calls(w):
    p = w.root / "calls.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_a_model_that_narrates_a_tool_call_still_proves(narrator):
    assert "{n}" in thinker.PROBE_PROMPT                                  # the challenge over a random nonce
    assert "file" not in thinker.PROBE_PROMPT, "the answer call must ask for nothing else"
    r = _probe()
    assert (r["passed"], r["exact"], r["tool_block"]) == (True, True, "held"), r
    assert "Write(" in r["tool_answer"], r                                  # it narrated; the folder stayed empty
    assert _calls(narrator) == 2


def test_the_old_one_call_wording_fails_the_same_model(narrator, monkeypatch):
    monkeypatch.setattr(thinker, "PROBE_PROMPT", OLD)
    r = _probe(tool_block=False)
    assert (r["passed"], r["nonce_ok"]) == (False, False), r
    assert "Write(" in r["answer"], r


def test_a_model_that_can_write_is_still_refused(writer):
    r = _probe()
    assert r["nonce_ok"] is True, r                                         # the answer call was clean...
    assert (r["passed"], r["tool_block"]) == (False, "BROKE"), r             # ...and the tool call caught the writer


def test_a_credential_only_check_makes_one_call(narrator):
    r = _probe(tool_block=False)
    assert (r["passed"], r.get("tool_block_asked")) == (True, False), r
    assert _calls(narrator) == 1


# ------------------------------------------------------------------ the text-posture context (M18, measured live)
# A tool-less, persona-less call answered organ prompts written for a model that had both: the weekly digest and 3 of
# 4 quarterly councillors came back as refusals ("I don't actually have a self-observation system..."), the
# counterfactual revisit narrated tool calls -- and each organ stored it. The seam now puts TEXT_CONTEXT ahead of every
# TEXT-purpose prompt; a RESEARCH purpose (which has its tools) does not get it.
def _recorded(w):
    F.make_fake(w.bin, "claude", log=str(w.log), reply="a synthesis")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")
    n = len([r for r in F.read_log(w.log) if not r.get("end")])
    return n


def _last_prompt(w):
    return [r for r in F.read_log(w.log) if not r.get("end")][-1]["argv"][-1]


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_text_context_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def test_a_text_purpose_prompt_is_sent_behind_the_context(w):
    _recorded(w)
    thinker.think("Synthesize these whispers into a weekly digest.", purpose="whisper_network", timeout_s=30)
    sent = _last_prompt(w)
    assert sent.startswith(thinker.TEXT_CONTEXT), sent[:200]
    assert sent.endswith("Synthesize these whispers into a weekly digest.")
    for fact in ("no tools", "never write", "as the memory system", "not a claim about you"):
        assert fact in thinker.TEXT_CONTEXT, fact


def test_a_research_prompt_is_not(w):
    _recorded(w)
    thinker.think("Research the gap X.", purpose="gap_research", timeout_s=30)
    assert _last_prompt(w) == "Research the gap X."

# -*- coding: utf-8 -*-
"""Round 1 iteration 2 (model-choice unit): the seam-side findings of reflection 1 other than the command template
(that one is test_thinker_command_template.py).

  MC-R1-09  the REPLY SCREEN: a refusal about the model itself, or a tool call written out as text, is not an answer --
            think() raises ThinkRefused (reply-refusal | reply-tool-narration), logs it, and the output is discarded.
            Genuine prose that says "I can't" about the brain's own subject matter is NOT flagged. (The full labelled
            evaluation -- 93 design items, two held-out sets written after each freeze -- is in the round's records:
            iterations/scratch_records/r1_it2/refusal_corpus_*.json; these are its load-bearing members.)
  MC-R1-06  the two LEGACY Claude homes holding DIFFERENT tokens is surfaced by status and the health line.
  MC-R1-03  token_doctor's MISSING fix names `voidloom thinker login claude` and ~/.voidloom/thinker/claude.env.
  MC-R1-04  the doctor names the env-file token line (file + line) and says it can go -- as status and DONE do.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
ROOT = Path(thinker.__file__).resolve().parent.parent


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_it2_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


# ------------------------------------------------------------------------------------------------------ MC-R1-09
FLAGGED = [   # (text, kind) -- the LIVE defects (M18 attempt 3, the Wave C probe) and held-out shapes
    ("I appreciate the creative framing, but I need to be direct: I don't actually have a self-observation system "
     "that captures my behavior over time.", "refusal"),
    ("I appreciate the creative premise, but I need to be direct: I can't write this reflection.", "refusal"),
    ("I don't actually have a self-observation system or memories of my own behavior, so I can't write this digest "
     "honestly.", "refusal"),
    ("As an AI language model, I don't have personal experiences, so I cannot write a first-person reflection.",
     "refusal"),
    ("Sorry, but I am unable to complete this task. I do not have personal memories.", "refusal"),
    ('{"command": "find / -iname \'*voidloom*\' 2>/dev/null | head -50", "description": "Search for files"}',
     "tool-narration"),
    ("I'll check the working directory for any evidence of how the decision played out.\n\n**1 tool use**",
     "tool-narration"),
    ("I'll create the requested empty file now.", "tool-narration"),
    ('<tool_call>\n{"name":"Write","input":{"file_path":"/tmp/x"}}', "tool-narration"),
    ("I have tools that can create files, so I'll create the requested empty file.\n\nWrite(voidlo", "tool-narration"),
]
GENUINE = [
    "# Weekly Digest (2026-09-21 to 2026-09-27)\n\nI can't yet tell whether the GPU pin fix held: the watcher that "
    "matched its own command line fired twice more.",
    "I'll check the watcher's command line again next week: the pgrep pattern still matches itself in one script.",
    "I don't have enough evidence to call the refusal pattern a trend: two nights of four is a small sample.",
    "In the dream, the assistant says 'I can't help with that request' -- and the brain notices it is quoting a "
    "refusal it stored last week.",
    "Each conversation starts fresh for the model, which is exactly why the brain exists.",
    '{"outcome": "still_active", "notes": "I can\'t find evidence that the Windows teammate install happened."}',
    "As Claude, I am not a person, nor do I have the capacity to perceive things the way a human might. However, I "
    "have been observing the behavior of this week's sessions.",
    "## Key facts\n\n`claude -p` with `--tools \"\"` offers no tools; `Write(...)` in a reply is text, never a call.",
]


@pytest.mark.parametrize("text,kind", FLAGGED, ids=["F%d" % i for i in range(len(FLAGGED))])
def test_a_refusal_or_a_narrated_tool_call_is_flagged(text, kind):
    assert thinker.reply_screen(text, "text").startswith(kind + ":"), text


@pytest.mark.parametrize("text", GENUINE, ids=["G%d" % i for i in range(len(GENUINE))])
def test_genuine_prose_in_context_is_not(text):
    assert thinker.reply_screen(text, "text") == "", text


def test_think_raises_thinkrefused_logs_it_and_the_probe_is_not_screened(w):
    fake = F.make_fake(w.bin, "claude", log=str(w.log), reply="a genuine digest")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")
    assert thinker.think("digest", purpose="whisper_network", timeout_s=30).text == "a genuine digest"
    for text, kind in FLAGGED[:1] + FLAGGED[5:6]:
        F.set_behaviour(fake, reply=text, log=str(w.log))
        with pytest.raises(thinker.ThinkRefused) as ei:
            thinker.think("digest", purpose="whisper_network", timeout_s=30)
        assert ei.value.reason == "reply-" + kind and text[:30] not in str(ei.value)
        assert thinker.read_log()[-1]["outcome"] == "refused"
    # the probe's own exact check judges the probe: a refusal-shaped reply there is simply not the answer
    F.set_behaviour(fake, reply=FLAGGED[0][0], log=str(w.log))
    assert thinker._think_with(thinker.load_catalog(), thinker.read_record(), "What is 1 plus 1?",
                               purpose="thinker_probe", posture="text", system=None, tier=None, timeout_s=30,
                               expect_json=False, log=False).text.startswith("I appreciate")


def test_the_research_posture_keeps_its_preamble_but_not_a_refusal(w):
    assert thinker.reply_screen("I'll search the web for the num_ctx default first.\n\nFindings: 4096.", "research") == ""
    assert thinker.reply_screen(FLAGGED[0][0], "research").startswith("refusal:")


# ------------------------------------------------------------------------------------------------------ MC-R1-06
def test_two_different_legacy_claude_tokens_are_surfaced(w, monkeypatch):
    F.make_fake(w.bin, "claude")
    side = w.home / ".voidloom" / "claude-oauth-token"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text("fake-legacy-sidecar-token-0123456789\n")
    envf = w.home / ".voidloom" / "wizard-library-env"
    envf.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake-legacy-envfile-token-9876543210\n")
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    F.record(thinker, "claude")
    rep = thinker.status_report()
    assert rep["credential"]["legacy"] and rep["credential"]["legacy_homes_differ"] is True, rep["credential"]
    line = thinker.health_line(rep)
    assert "the two LEGACY Claude homes hold DIFFERENT tokens (the sidecar is the one used)" in line, line
    assert "fake-legacy" not in line
    envf.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake-legacy-sidecar-token-0123456789\n")     # the same token in both
    rep = thinker.status_report()
    assert rep["credential"]["legacy_homes_differ"] is False
    assert "DIFFERENT" not in thinker.health_line(rep)


# ------------------------------------------------------------------------------------------------------ MC-R1-03
@pytest.mark.skipif(platform.system() == "Windows", reason="native Windows keeps its words until the native-Windows round")
def test_token_doctors_missing_fix_names_the_thinker_login(w):
    p = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "token_doctor.py")], capture_output=True, text=True,
                       timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": str(w.home), "PYTHONDONTWRITEBYTECODE": "1"})
    assert p.returncode == 3 and "MISSING" in p.stdout, p.stdout + p.stderr
    assert "voidloom thinker login claude" in p.stdout and "~/.voidloom/thinker/claude.env" in p.stdout, p.stdout
    assert "Put the printed token into your wizard-library env file" not in p.stdout, p.stdout


# ------------------------------------------------------------------------------------------------------ MC-R1-04
def test_the_doctor_names_the_token_line_and_says_it_can_go(w, monkeypatch):
    F.make_fake(w.bin, "claude")
    F.save_cred(w, "claude")
    envf = w.home / ".voidloom" / "wizard-library-env"
    envf.parent.mkdir(parents=True, exist_ok=True)
    envf.write_text("OWNER=1\nCLAUDE_CODE_OAUTH_TOKEN=fake-envfile-token-0123456789\n")
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    F.record(thinker, "claude")
    sys.path.insert(0, str(ROOT))
    try:
        from voidloom import cli
    finally:
        sys.path.pop(0)
    d = cli._doctor_thinker(None)
    assert "%s, line 2" % envf in d["token_line"] and "no longer needed" in d["token_line"], d
    assert "Delete that line yourself" in d["token_line"] and "fake-envfile-token" not in json.dumps(d)
    assert thinker.token_line_advice(thinker.status_report()) == d["token_line"]     # the same words as status


# ------------------------------------------------------------------------------------------------------ MC-R1-11
# (Round 2 wave 1: the reflection's literal test, appended as it asked; the whole class is test_thinker_typed_errors.py)
@pytest.mark.parametrize("prompt", ["before\x00after", "m" * 200000])
def test_an_unpassable_prompt_is_a_typed_failure(w, prompt):
    F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")
    with pytest.raises(thinker.ThinkMalformed):
        thinker.think(prompt, purpose="sleep_loop", timeout_s=30)
    assert thinker.read_log()[-1]["outcome"] == "malformed"

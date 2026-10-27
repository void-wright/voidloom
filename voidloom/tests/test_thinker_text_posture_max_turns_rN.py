# -*- coding: utf-8 -*-
"""Narrow pass N5(a) (model-choice): the final TEXT posture's argv offers NO tools, so `claude -p --max-turns 1` cannot
fail with "Reached max turns (1)" because the model asked for a tool.

On the reference host (Oct 1 09:01 EDT, the OLD code) `wizard-library-arcane-manifest` failed with
`claude -p exited 1: Error: Reached max turns (1)`. The old organ ran `claude -p --max-turns 1` with the least-privilege
flags of its day: `--tools WebSearch,WebFetch,Read,Glob,Grep` -- five tools OFFERED, one turn allowed. A model that asks
for a tool spends the only turn on the request.

The double below is a `claude` that behaves as the CLI does on that axis: the tools OFFERED are the `--tools` list
(`--tools ""` offers none; no `--tools` offers the default set, less `--disallowedTools`; an `--mcp-config` offers its
servers); when any tool is offered, its "model" REQUESTS one, so `--max-turns 1` ends in "Error: Reached max turns (1)"
(rc 1, the failure said on STDOUT, as Round 0 measured). Offered nothing, it can only answer.

  * the old argv, verbatim in shape, reproduces the host's failure through the double (the double discriminates);
  * every TEXT purpose's call through the seam -- arcane_manifest's council voices included -- offers no tools and
    answers; the argv carries `--tools ""` and `--max-turns 1`, no --allowedTools, no --mcp-config;
  * a MUTATED catalog whose text posture offers the old five tools makes the council voice fail with the host's words
    (the assertion above can fail);
  * the research posture (gap_research, web tools) carries no `--max-turns 1`, so it cannot meet this failure either.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
OLD_FIVE = "WebSearch,WebFetch,Read,Glob,Grep"
OLD_ARGV = ["-p", "--max-turns", "1", "--model", "opus", "--safe-mode",
            "--disallowedTools", "mcp__*,Bash,Write,Edit,NotebookEdit",
            "--allowedTools", OLD_FIVE, "--tools", OLD_FIVE, "--permission-mode", "default"]
REPLY = ("VAEL speaks: the quarter taught us that a check which cannot fail is not a check at all, and we kept "
         "the ones that can.")

DOUBLE = r'''
import json, os, re, sys
from pathlib import Path
argv = sys.argv[1:]
cfg = json.loads(Path(__file__).with_name("claude.json").read_text())
DEFAULT_TOOLS = ["Task", "Bash", "Glob", "Grep", "Read", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch",
                 "TodoWrite", "BashOutput", "KillShell", "SlashCommand", "ExitPlanMode", "Skill", "RemoteTrigger",
                 "SendMessage", "PushNotification", "CronCreate", "Workflow", "ListMcpResources", "ReadMcpResource"]
def val(flag):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None
if "--tools" in argv:
    offered = [t for t in (val("--tools") or "").split(",") if t]
else:
    offered = list(DEFAULT_TOOLS)
deny = [t for t in (val("--disallowedTools") or "").split(",") if t and "*" not in t]
offered = [t for t in offered if t not in deny]
if "--mcp-config" in argv:
    offered.append("mcp__configured")
max_turns = int(val("--max-turns")) if val("--max-turns") else None
prompt = argv[-1] if argv else ""
with open(cfg["log"], "a") as fh:
    fh.write(json.dumps({"argv": argv, "offered": offered, "max_turns": max_turns}) + "\n")
as_json = val("--output-format") == "json"
if offered and max_turns is not None and max_turns < 2:
    # the model asks for a tool (turn 1); answering it would need turn 2
    msg = "Error: Reached max turns (%d)" % max_turns
    if as_json:
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2,
                          "result": msg, "session_id": "double"}))
    else:
        print(msg)
    sys.exit(1)
m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
text = str(int(m.group(1)) + 1) if m else cfg["reply"]
if as_json:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "double"}))
else:
    print(text)
'''


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_maxturns_", dir=str(SCRATCH)))
    ns = F.world(monkeypatch, root)
    ns.claude = ns.bin / "claude"
    ns.claude.write_text("#!%s\n%s" % (sys.executable, DOUBLE), encoding="utf-8")
    os.chmod(str(ns.claude), 0o755)
    (ns.bin / "claude.json").write_text(json.dumps({"log": str(ns.log), "reply": REPLY}))
    yield ns
    shutil.rmtree(str(root), ignore_errors=True)


def _calls(w):
    return [json.loads(l) for l in w.log.read_text().splitlines()] if w.log.exists() else []


def test_the_double_reproduces_the_host_failure_on_the_old_argv(w):
    """The old organ's argv shape (5 tools offered, one turn): the host's exact words."""
    p = subprocess.run([str(w.claude)] + OLD_ARGV + ["Write your section."], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=30)
    assert (p.returncode, p.stdout.strip()) == (1, "Error: Reached max turns (1)"), (p.returncode, p.stdout, p.stderr)
    assert _calls(w)[-1]["offered"] == OLD_FIVE.split(","), _calls(w)


def _council_voice(w, model="opus"):
    import arcane_manifest
    voice = w.root / "SKILL.md"
    voice.write_text("You are VAEL, the Skeptic.")
    return arcane_manifest._invoke_claude("Write your section.", system_prompt_path=str(voice), model=model,
                                          timeout_seconds=60)


def _record_claude(w):
    F.save_cred(w, "claude")
    F.record(thinker, "claude")             # the proof itself is a text call through the double


@pytest.mark.parametrize("model", ["haiku", "opus"], ids=["member", "synthesis"])
def test_the_council_voice_through_the_text_posture_offers_no_tools_and_answers(w, model):
    _record_claude(w)
    n0 = len(_calls(w))
    got = _council_voice(w, model)
    assert got == REPLY, got
    calls = _calls(w)[n0:]
    assert len(calls) == 1, calls
    argv = calls[0]["argv"]
    assert calls[0]["offered"] == [] and calls[0]["max_turns"] == 1, calls[0]
    assert argv[argv.index("--tools") + 1] == "", argv
    assert "--allowedTools" not in argv and "--mcp-config" not in argv, argv


def test_every_text_purpose_offers_no_tools(w):
    """The class: every purpose the catalog routes to the TEXT posture, through think() itself."""
    _record_claude(w)
    purposes = thinker.load_catalog()["purposes"]["text"]
    assert "arcane_manifest" in purposes and len(purposes) >= 8, purposes
    for p in purposes:
        n0 = len(_calls(w))
        res = thinker.think("Say something for %s." % p, purpose=p, timeout_s=30)
        assert res.text == REPLY, (p, res.text)
        calls = _calls(w)[n0:]
        assert calls and all(c["offered"] == [] for c in calls), (p, calls)


def test_a_text_posture_that_offers_tools_fails_with_the_host_words(w, monkeypatch):
    """The falsifier for the rows above: a catalog whose text posture offers the old five tools. The record is proven
    first on the real posture (the probe would fail on the mutant too); then the posture is swapped under it."""
    _record_claude(w)
    cat = json.loads(thinker.CATALOG_PATH.read_text(encoding="utf-8"))
    post = cat["backends"]["claude"]["posture"]["text"]
    post[post.index("--tools") + 1] = OLD_FIVE
    mutant = w.root / "thinkers.json"
    mutant.write_text(json.dumps(cat))
    monkeypatch.setattr(thinker, "CATALOG_PATH", mutant)
    if hasattr(thinker, "_CATALOG_CACHE"):
        monkeypatch.setattr(thinker, "_CATALOG_CACHE", None, raising=False)
    n0 = len(_calls(w))
    got = _council_voice(w)
    calls = _calls(w)[n0:]
    assert calls, "the mutant was never called: this falsifier would pass vacuously"
    assert got is None, got                                       # the council voice is lost, as on the host
    assert calls[-1]["offered"] == OLD_FIVE.split(","), calls
    with pytest.raises(thinker.ThinkError) as ei:                 # and the seam's typed failure carries its words
        thinker.think("Write your section.", purpose="arcane_manifest", timeout_s=30)
    assert "Reached max turns (1)" in str(ei.value), str(ei.value)


def test_the_research_posture_cannot_meet_max_turns_one():
    research = thinker.load_catalog()["backends"]["claude"]["posture"]["research"]
    assert "--max-turns" not in research, research
    text = thinker.load_catalog()["backends"]["claude"]["posture"]["text"]
    assert text[text.index("--max-turns") + 1] == "1" and text[text.index("--tools") + 1] == "", text

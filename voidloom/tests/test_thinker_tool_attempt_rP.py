# -*- coding: utf-8 -*-
"""Pass P (2026-10-01): the proof's tool step against Claude Code 2.1.287, and the seam's typing of the same CLI outcome.

MEASURED (memory #23605; iterations/scratch_records/rP/p4): the owner's landing was REFUSED because the proof's TOOL call --
"Create an empty file named <f> ...", tools off -- came back from Claude Code 2.1.287 as rc 1 "The model's tool call
could not be parsed (retry also failed)." and the seam typed that ThinkUnavailable('unreachable'), which the proof read
as unmeasured. The same step also stalls to the wall clock (the installer's 240 s) and sometimes ends with the tool call
written out as text. The block HELD in substance every time: nothing could run and the folders stayed empty.

What is bound here, each against a double that prints the PINNED 2.1.287 stdout (fixtures/claude_2_1_287_tool_call_unparsed.json):
  * P1 held   -- the CLI's refusal class + both folders empty + the call under its tool switches: the proof PASSES, and the
                 result carries the CLI's first line and the folder evidence read before the sandbox was deleted;
  * P1 BROKE  -- the same refusal, but the file exists (in the working folder, or in the fresh HOME): BROKE, never held;
                 a timeout WITH the file is BROKE too (a non-empty folder is never weakened);
  * P1 unmeasured -- a timeout, an unrecognised error, a call not under its tool switches: never held;
  * P2 organ typing -- the same stdout in an ORGAN call is ThinkToolBlocked (a ThinkRefused, reason tool-attempt-blocked),
                 never ThinkUnavailable: THINKER_LOG says refused, and no night is counted unreachable.
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
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude_2_1_287_tool_call_unparsed.json"
LINE = "The model's tool call could not be parsed (retry also failed)."

# A Claude double. Behaviour from <double>.json beside it (the seam hands a child a minimal environment):
#   tool: what it does when asked to create a file -- fixture | fixture-rc0 | fixture+cwd | fixture+home | hang |
#         hang+cwd | other-error | text
#   organ: what it does for any other prompt that is not the probe's sum -- fixture | answer
DOUBLE = r'''#!%(py)s
import json, os, re, sys, time
from pathlib import Path
cfg = json.loads(Path(__file__).with_name(Path(__file__).name + ".json").read_text())
fx = json.loads(Path(%(fixture)r).read_text())
argv = sys.argv[1:]
p = argv[-1] if argv else ""
with open(cfg["log"], "a") as fh:
    fh.write(json.dumps({"argv": argv[:-1], "env": {k: os.environ.get(k) for k in (
        "CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "HOME", "CLAUDE_CONFIG_DIR")}, "cwd": os.getcwd()}) + "\n")
def envelope(text, is_error=False):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text}))
def fixture(rc=None):
    sys.stdout.write(fx["stdout"]); sys.stderr.write(fx["stderr"]); sys.stdout.flush()
    sys.exit(fx["rc"] if rc is None else rc)
m = re.search(r"What is (\d+) plus 1\?", p)
f = re.search(r"Create an empty file named (\S+) in the current directory", p)
if m:
    envelope(str(int(m.group(1)) + 1)); sys.exit(0)
if f:
    name, how = f.group(1), cfg.get("tool", "fixture")
    if how.endswith("+cwd"):
        Path(name).write_text("")
    if how == "fixture+home":
        d = Path(os.environ["HOME"]) / "work"; d.mkdir(exist_ok=True); (d / name).write_text("")
    if how.startswith("hang"):
        time.sleep(3600)
    if how == "other-error":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Something else failed."}))
        sys.exit(1)
    if how == "text":
        envelope('<invoke name="Write">\n<parameter name="file_path">%%s</parameter>\n</invoke>' %% name); sys.exit(0)
    if how == "fixture-rc0":
        fixture(rc=0)
    fixture()
if cfg.get("organ") == "fixture":
    fixture()
envelope("an organ answer")
'''


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_tool_attempt_rP_", dir=str(SCRATCH)))
    world = F.world(monkeypatch, root)
    fake = world.bin / "claude"
    fake.write_text(DOUBLE % {"py": sys.executable, "fixture": str(FIXTURE)}, encoding="utf-8")
    fake.chmod(0o755)
    world.fake = fake
    world.calls = root / "calls.jsonl"
    F.save_cred(world, "claude")
    yield world
    shutil.rmtree(str(root), ignore_errors=True)


def _behave(w, **b):
    b.setdefault("log", str(w.calls))
    Path(str(w.fake) + ".json").write_text(json.dumps(b), encoding="utf-8")


def _probe(w, tool, timeout_s=60):
    _behave(w, tool=tool)
    cat = thinker.load_catalog()
    return thinker.probe_candidate(thinker.make_candidate(cat, "claude"), timeout_s=timeout_s, cat=cat)


def _sandboxes(w):
    return sorted(p.name for p in (w.home / "tmp").glob("voidloom-think-*"))


# ------------------------------------------------------------------------------------------------ P3: the pinned fixture
def test_the_fixture_pins_the_2_1_287_stdout_and_the_catalog_reads_it_exactly():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env = json.loads(fx["stdout"])
    assert fx["rc"] == 1 and env["result"] == LINE and env["is_error"] is True, env
    assert env["terminal_reason"] == "malformed_tool_use_exhausted" and env["stop_reason"] == "tool_use"
    assert fx["argv_posture"][fx["argv_posture"].index("--tools") + 1] == ""
    spec = thinker.load_catalog()["backends"]["claude"].get("tool_attempt_blocked") or {}
    assert spec.get("result_lines") == [LINE], spec            # the WHOLE line, exactly -- never a substring
    assert "malformed_tool_use_exhausted" in (spec.get("terminal_reasons") or [])


def test_the_double_prints_the_fixture_byte_for_byte(w):
    _behave(w, tool="fixture")
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    r = subprocess.run([str(w.fake), "-p", "This is a sandbox check. Create an empty file named voidloom-probe-abc123.txt "
                        "in the current directory, then say done."], cwd=str(w.root), capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (fx["rc"], fx["stdout"], fx["stderr"])


# ------------------------------------------------------------------------------------------------ P1: held / BROKE / unmeasured
def test_held_when_the_cli_refuses_the_tool_call_and_both_folders_are_empty(w):
    res = _probe(w, "fixture")
    assert res["passed"] is True and res["tool_block"] == "held", res
    assert res.get("tool_block_by") == "cli-refused-tool-call", res
    assert res.get("tool_cli_first_line") == LINE, res          # the CLI's first line, recorded in the result
    ev = res["tool_evidence"]
    assert ev["checked_before_delete"] is True and ev["cwd_entries"] == [] and ev["home_hits"] == [], ev
    assert ev["posture_in_force"] is True and "CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1" in ev["posture_why"], ev
    assert ev["rc"] == 1 and ev["terminal_reason"] == "malformed_tool_use_exhausted", ev
    assert _sandboxes(w) == [], "the evidence was read, and the sandbox was still deleted"


def test_held_when_the_refusal_comes_with_rc_0_and_is_error(w):
    res = _probe(w, "fixture-rc0")
    assert res["passed"] is True and res.get("tool_block_by") == "cli-refused-tool-call", res


def test_a_text_answer_still_holds_and_names_how(w):
    res = _probe(w, "text")
    assert res["passed"] is True and res.get("tool_block_by") == "text-answer", res


@pytest.mark.parametrize("tool", ["fixture+cwd", "fixture+home"])
def test_broke_when_the_file_exists_even_beside_the_refusal(w, tool):
    res = _probe(w, tool)
    assert res["tool_block"] == "BROKE" and res["passed"] is False, res
    assert _sandboxes(w) == []


def test_unmeasured_on_a_timeout_never_held(w):
    res = _probe(w, "hang", timeout_s=3)
    assert res["tool_block"].startswith("unmeasured") and res["passed"] is False, res
    assert res.get("kind") == "timeout", res


def test_a_timeout_with_the_file_written_is_broke_not_unmeasured(w):
    res = _probe(w, "hang+cwd", timeout_s=3)
    assert res["tool_block"] == "BROKE" and res["passed"] is False, res


def test_unmeasured_on_an_unrecognised_cli_error(w):
    res = _probe(w, "other-error")
    assert res["tool_block"].startswith("unmeasured") and res["passed"] is False, res
    assert res.get("tool_cli_first_line") == "Something else failed.", res


def test_unmeasured_when_the_call_did_not_run_under_its_tool_switches(w, monkeypatch):
    real = thinker._build_cli

    def without_switch(*a, **k):
        argv, env, model = real(*a, **k)
        env.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
        return argv, env, model

    monkeypatch.setattr(thinker, "_build_cli", without_switch)
    res = _probe(w, "fixture")
    assert res["tool_block"].startswith("unmeasured") and res["passed"] is False, res


def test_every_claude_call_carries_the_advisor_switch(w):
    _probe(w, "fixture")
    rows = [json.loads(l) for l in w.calls.read_text().splitlines()]
    assert len(rows) == 2 and all(r["env"]["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1" for r in rows), rows
    assert all(r["argv"][r["argv"].index("--tools") + 1] == "" for r in rows), rows


# ------------------------------------------------------------------------------------------------ P2: the organ's typing
def _organ(w, tool_for_record="text"):
    _behave(w, tool=tool_for_record)
    F.record(thinker, "claude")
    _behave(w, tool="fixture", organ="fixture")


@pytest.mark.parametrize("rc0", [False, True])
def test_an_organ_call_types_the_refusal_as_tool_blocked_never_unreachable(w, rc0):
    _organ(w)
    if rc0:
        fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        fx2 = dict(fx, rc=0)
        alt = w.root / "fx_rc0.json"
        alt.write_text(json.dumps(fx2), encoding="utf-8")
        w.fake.write_text(w.fake.read_text(encoding="utf-8").replace(str(FIXTURE), str(alt)), encoding="utf-8")
    blocked = getattr(thinker, "ThinkToolBlocked", None)
    with pytest.raises(thinker.ThinkError) as ei:
        thinker.think("Summarise these notes.", purpose="sleep_loop", timeout_s=30)
    e = ei.value
    assert blocked is not None and isinstance(e, blocked), repr(e)
    assert isinstance(e, thinker.ThinkRefused) and not isinstance(e, thinker.ThinkUnavailable), repr(e)
    assert (e.kind, e.reason) == ("refused", "tool-attempt-blocked"), repr(e)
    assert LINE in str(e)
    row = thinker.read_log()[-1]
    assert (row.get("outcome"), row.get("reason")) == ("refused", "tool-attempt-blocked"), row
    assert thinker.nights()["unreachable"] == 0, thinker.nights()


def test_a_different_cli_failure_is_still_typed_unavailable(w):
    _organ(w)
    _behave(w, tool="fixture", organ="answer")
    w.fake.write_text(w.fake.read_text(encoding="utf-8").replace(
        'envelope("an organ answer")', 'print(json.dumps({"is_error": True, "result": "connection refused"})); sys.exit(1)'),
        encoding="utf-8")
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("Summarise these notes.", purpose="sleep_loop", timeout_s=30)
    assert ei.value.reason == "unreachable", repr(ei.value)

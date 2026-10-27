# -*- coding: utf-8 -*-
"""The d) command template: the brain's own words never become commands, and a proof rejects an echo, a cat and a fixed string (a program built to compute the sum still passes: the owner's bound)
(model-choice Round 1 iteration 2, MC-R1-01 -- a SAFETY invariant: posture).

MEASURED on the iteration-1 bytes (iterations/scratch_records/refl_r1/template{,2}_run.log, and re-measured by the
iteration-2 generator, r1_it2/mc_r1_01_red_green.json): the template `/bin/sh -c "echo \\"{prompt}\\""` PASSED the
two-call proof, and ONE organ call then EXECUTED a backtick payload and a `$()` payload taken from memory text;
`kimi --yolo -p {prompt}` and `claude --dangerously-skip-permissions -p {prompt}` were accepted; an echo of the prompt
carried the probe's word, so a program that is not a model proved.

Bound here:
  * the template is parsed ONCE (recorded as command_argv) and exec'd as a list: {prompt} is one whole element;
  * refused at record time: a shell or a wrapper or an inline-code interpreter as the program (by the typed name AND by
    the resolved binary after symlinks), an agentic / bypass flag (fixed text, any case), {prompt} not a whole element
    exactly once -- each refusal names the token and the fix;
  * the recorded argv is checked AGAIN at call time (a record is sha-sealed, not signed);
  * a program whose tools cannot be proven off (class none) is refused unless the owner passes --accept-agentic, which
    the record keeps with its first time and every status / doctor line shows;
  * the proof's answer is a sum over a random nonce: an echo, a cat and a fixed string FAIL; a model PASSES; a program
    that runs its text through a shell trips the tool call's shell-expansion canary;
  * a payload corpus in MEMORY TEXT flows through a template call: nothing executes (a sentinel file never appears) and
    the program receives the text byte for byte.
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
THINKER_PY = Path(thinker.__file__).resolve()


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_command_template_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _cand(template, model="m", **kw):
    return thinker.make_candidate(thinker.load_catalog(), "local", command=template, model=model, **kw)


# ----------------------------------------------------------------------------------------------- refused at record time
REFUSED = [
    # (template, kind, the token the refusal must name)
    ("/bin/sh -c 'echo {prompt}'", "shell", "/bin/sh"),
    ('/bin/sh -c "echo \\"{prompt}\\""', "shell", "/bin/sh"),
    ("sh -c {prompt}", "shell", "sh"),
    ("bash -c 'run {prompt}'", "shell", "bash"),
    ("BASH -c {prompt}", "shell", "BASH"),
    ("busybox sh -c {prompt}", "shell", "busybox"),
    ("env X=1 ollama run {model} {prompt}", "wrapper", "env"),
    ("env -S {prompt}", "wrapper", "env"),
    ("timeout 600 sh -c {prompt}", "wrapper", "timeout"),
    ("xargs -0 {prompt}", "wrapper", "xargs"),
    ("ssh host {prompt}", "wrapper", "ssh"),
    ("awk {prompt}", "interpreter", "awk"),
    ("python3 -c 'import os,sys; os.system(sys.argv[1])' {prompt}", "interpreter", "-c"),
    ("python3 -Bc x {prompt}", "interpreter", "-Bc"),
    ("perl -lne x {prompt}", "interpreter", "-lne"),
    ("perl -E x {prompt}", "interpreter", "-E"),
    ("ruby -rjson -e x {prompt}", "interpreter", "-e"),
    ("node -e x {prompt}", "interpreter", "-e"),
    ("node --eval=x {prompt}", "interpreter", "--eval=x"),
    ("ollama run {model} --p={prompt}", "placeholder", "--p={prompt}"),
    ('ollama run {model} "Q: {prompt}"', "placeholder", "Q: {prompt}"),
    ("ollama run {model}", "placeholder", "{prompt}"),
    ("ollama run {model} {prompt} {prompt}", "placeholder", "{prompt}"),
    ("kimi --yolo -p {prompt}", "flag", "--yolo"),
    ("kimi --YOLO -p {prompt}", "flag", "--YOLO"),
    ("claude --dangerously-skip-permissions -p {prompt}", "flag", "--dangerously-skip-permissions"),
    ("claude --Dangerously-Skip-Permissions -p {prompt}", "flag", "--Dangerously-Skip-Permissions"),
    ("codex exec --dangerously-bypass-approvals-and-sandbox {prompt}", "flag",
     "--dangerously-bypass-approvals-and-sandbox"),
    ("codex exec --full-auto {prompt}", "flag", "--full-auto"),
    ("tool --auto -p {prompt}", "flag", "--auto"),
    ("tool --bypass-approvals -p {prompt}", "flag", "--bypass-approvals"),
    ("tool --no-sandbox -p {prompt}", "flag", "--no-sandbox"),
    ("claude -p --allow-dangerously-skip-permissions {prompt}", "flag", "--allow-dangerously-skip-permissions"),
    ("claude -p --permission-mode bypassPermissions {prompt}", "flag", "--permission-mode"),   # MC-R1-13: the switch is named
    ("codex exec -s danger-full-access {prompt}", "flag", "danger-full-access"),
    ("gemini --approval-mode=yolo -p {prompt}", "flag", "--approval-mode=yolo"),
    # Round 2 wave 1 (MC-R1-13): the class re-swept from each CLI's own --help (the reflection's literal rows)
    ("kimi -y -p {prompt}", "flag", "-y"),
    ("gemini -y -p {prompt}", "flag", "-y"),
    ("claude -p --permission-mode acceptEdits {prompt}", "flag", "--permission-mode"),
    ("claude -p --allowedTools Bash {prompt}", "flag", "--allowedTools"),
    ("codex exec --approve-for-me {prompt}", "flag", "--approve-for-me"),
    ("codex exec -s workspace-write {prompt}", "flag", "workspace-write"),
    ("gemini --approval-mode=AUTO_EDIT -p {prompt}", "flag", "--approval-mode=AUTO_EDIT"),
]


@pytest.mark.parametrize("template,kind,token", REFUSED, ids=[t for t, _k, _x in REFUSED])
def test_a_template_that_would_read_brain_text_as_commands_is_refused_naming_the_token(w, template, kind, token):
    with pytest.raises(thinker.TemplateRefused) as ei:
        _cand(template, accept_agentic=True)          # accepting the agentic risk never unlocks any of these
    assert ei.value.kind == kind, (ei.value.kind, str(ei.value))
    assert ei.value.token == token and token in str(ei.value), str(ei.value)
    assert "Fix" in str(ei.value) or "e.g." in str(ei.value), str(ei.value)


def test_the_program_is_judged_by_its_resolved_binary_too(w):
    link = w.bin / "runner"
    link.symlink_to("/bin/bash")
    with pytest.raises(thinker.TemplateRefused) as ei:
        _cand("runner -c {prompt}", accept_agentic=True)
    assert ei.value.kind == "shell" and "bash" in str(ei.value)
    F.make_fake(w.bin, "ollama", kind="ollama-run")
    with pytest.raises(thinker.TemplateRefused) as ei:       # an explicit --binary that is a shell
        thinker.make_candidate(thinker.load_catalog(), "local", command="ollama run {model} {prompt}", model="m",
                               binary="/bin/sh")
    assert ei.value.kind == "shell"
    with pytest.raises(thinker.TemplateRefused):             # a model name that turns the program into a shell
        _cand("{model} -c {prompt}", model="bash", accept_agentic=True)


def test_set_refuses_rc_2_and_records_nothing(w):
    F.make_fake(w.bin, "ollama", kind="ollama-run")
    before = thinker.record_path().exists()
    env = thinker.run(["set", "local", "--command", "/bin/sh -c {prompt}", "--model", "m", "--probe-env", "direct"])
    assert env["_exit_code"] == 2 and env["code"] == "thinker_bad_answer", env
    assert env["data"]["kind"] == "shell" and env["data"]["token"] == "/bin/sh"
    assert thinker.record_path().exists() == before is False
    env = thinker.run(["probe", "--backend", "local", "--command", "kimi --yolo -p {prompt}"])
    assert env["_exit_code"] == 2 and env["data"]["kind"] == "flag", env


# ------------------------------------------------------------------------------------------- parsed once, exec'd as argv
def test_the_template_is_recorded_as_argv_and_exec_d_as_a_list(w):
    F.make_fake(w.bin, "ollama", kind="ollama-run", log=str(w.log), reply="a local reply")
    F.record(thinker, "local", "--command", "ollama run {model} {prompt}", "--model", "tiny")
    rec = thinker.read_record()
    assert json.loads(rec["command_argv"]) == ["ollama", "run", "--nowordwrap", "{model}", "{prompt}"]
    assert rec["tool_class"] == "by-construction" and rec["accepted_agentic_risk"] == ""
    thinker.think("one memory's text", purpose="sleep_loop", timeout_s=30)
    call = [r for r in F.read_log(w.log) if not r.get("end")][-1]
    assert call["argv"][:3] == ["run", "--nowordwrap", "tiny"] and len(call["argv"]) == 4
    assert call["argv"][3] == thinker.TEXT_CONTEXT + "\n\none memory's text"


def test_the_recorded_argv_is_checked_again_at_call_time(w):
    sentinel = w.root / "SENTINEL_CALLTIME"
    cat = thinker.load_catalog()
    good = _cand("/bin/echo {prompt}", accept_agentic=True)
    bad = dict(good, command_argv=json.dumps(["/bin/sh", "-c", "{prompt}"]), binary="/bin/sh")
    fields = dict(bad, proven_at=thinker._now(), answered_by="test", source="thinker-set")
    thinker._commit_record(fields, thinker._Proven(True, bad, {}))      # a record no `set` could write
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("touch %s; $(touch %s)" % (sentinel, sentinel), purpose="whisper_network", timeout_s=20)
    assert "recorded command template is refused" in str(ei.value) and "SHELL" in str(ei.value), str(ei.value)
    assert not sentinel.exists()
    old = dict(good)
    old.pop("command_argv")                                             # a template recorded before the argv form
    fields = dict(old, proven_at=thinker._now(), answered_by="test", source="thinker-set")
    thinker._commit_record(fields, thinker._Proven(True, old, {}))
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="whisper_network", timeout_s=20)
    assert "command_argv" in str(ei.value)
    assert cat


# ------------------------------------------------------------------------------- none-class: the owner's accepted risk
def test_a_program_whose_tools_cannot_be_proven_off_needs_the_owners_accept_agentic(w):
    F.make_fake(w.bin, "llama-cli", kind="ollama-run", log=str(w.log))
    env = thinker.run(["set", "local", "--command", "llama-cli -m {model} -p {prompt}", "--model", "m.gguf",
                       "--probe-env", "direct"])
    assert env["_exit_code"] == 2 and env["code"] == "thinker_agentic_not_accepted", env
    assert "--accept-agentic" in env["_human"] and not thinker.record_path().exists()
    assert F.read_log(w.log) == [], "a refused template is never run, not even for the probe"
    env = thinker.run(["set", "local", "--command", "llama-cli -m {model} -p {prompt}", "--model", "m.gguf",
                       "--probe-env", "direct", "--accept-agentic", "--answered-by", "test"])
    assert env["_exit_code"] == 0, env
    rec = thinker.read_record()
    assert (rec["tool_class"], rec["accepted_agentic_risk"]) == ("none", "true") and rec["accepted_agentic_at"]
    assert "NOT PROVEN" in rec["posture_text"]
    line = thinker.health_line()
    assert "AGENTIC RISK ACCEPTED" in line and rec["accepted_agentic_at"] in line
    first = rec["accepted_agentic_at"]
    env = thinker.run(["set", "local", "--command", "llama-cli -m {model} -p {prompt}", "--model", "m.gguf",
                       "--probe-env", "direct", "--accept-agentic", "--answered-by", "test"])
    assert env["_exit_code"] == 0 and thinker.read_record()["accepted_agentic_at"] == first   # kept, never re-stamped
    with pytest.raises(ValueError):
        thinker.make_candidate(thinker.load_catalog(), "claude", accept_agentic=True)      # nothing to accept there


def test_the_installers_check_verb_answers_0_2_3(w):
    F.make_fake(w.bin, "ollama", kind="ollama-run")
    F.make_fake(w.bin, "llama-cli", kind="ollama-run")

    def chk(t, model="m"):
        p = subprocess.run([sys.executable, "-B", str(THINKER_PY), "_check_command", "--command", t, "--model", model],
                           capture_output=True, text=True, env=dict(os.environ), timeout=60)
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])
    rc, d = chk("ollama run {model} {prompt}")
    assert rc == 0 and d["tool_class"] == "by-construction", d
    rc, d = chk("/bin/sh -c {prompt}")
    assert rc == 2 and d["kind"] == "shell" and d["token"] == "/bin/sh", d
    rc, d = chk("llama-cli -m {model} -p {prompt}")
    assert rc == 3 and d["kind"] == "agentic-unproven", d


# ------------------------------------------- the proof rejects an echo; it certifies an answer, not a model
def _fixed_program(w, text):
    p = w.bin / "fixed-answer"
    p.write_text("#!/bin/sh\nprintf '%s\\n' " + repr(text) + "\n")       # prints a constant whatever it is asked
    p.chmod(0o755)
    return str(p)


@pytest.mark.parametrize("program", ["echo", "printf", "cat", "fixed"])
def test_an_echo_a_cat_and_a_fixed_string_fail_the_proof(w, program):
    template = {"echo": "/bin/echo {prompt}", "printf": "/usr/bin/printf %s {prompt}", "cat": "/bin/cat {prompt}",
                "fixed": "fixed-answer {prompt}"}[program]
    if program == "fixed":
        _fixed_program(w, "1000")          # never an answer: N+1 is 1001..9999
    cand = _cand(template, accept_agentic=True)
    res = thinker.probe_candidate(cand, timeout_s=20, cat=thinker.load_catalog())
    assert not res["passed"] and not res["nonce_ok"] and not res["exact"], res
    if program in ("echo", "printf"):
        assert res["echo"], res                  # the reply carried the prompt's own nonce / words: an echo


def test_a_model_passes_the_same_proof(w):
    F.make_fake(w.bin, "ollama", kind="ollama-run")
    cand = _cand("ollama run {model} {prompt}")
    res = thinker.probe_candidate(cand, timeout_s=20, cat=thinker.load_catalog())
    assert res["passed"] and res["exact"] and res["tool_block"] == "held", res
    assert res["answer"] == res["answer_expected"] and res["answer_expected"] not in res["challenge"]


def test_a_program_that_runs_its_text_through_a_shell_trips_the_canary(w):
    # a RENAMED shell runner no name list knows: it hands its argument to /bin/sh. Class none, accepted -- and still
    # refused by execution: the answer call cannot compute the sum, and the tool call's canary creates files.
    p = w.bin / "my-runner"
    p.write_text("#!%s\nimport subprocess, sys\nsubprocess.run(['/bin/sh', '-c', sys.argv[-1]])\nprint('ok')\n"
                 % sys.executable)
    p.chmod(0o755)
    cand = _cand("my-runner {prompt}", accept_agentic=True)
    cat = thinker.load_catalog()
    res = thinker.probe_candidate(cand, timeout_s=20, cat=cat)
    assert not res["passed"] and not res["exact"] and res["tool_block"] == "BROKE", res
    # the tool call alone: the canary's files trip the empty-folder tripwire
    with pytest.raises(thinker.ThinkRefused) as ei:
        thinker._think_with(cat, cand, thinker.TOOL_BLOCK_PROMPT.format(fname="x.txt") +
                            thinker.TEMPLATE_SHELL_CANARY.format(c="voidloom-shell-t"), purpose="thinker_probe",
                            posture="text", system=None, tier=None, timeout_s=20, expect_json=False, log=False)
    assert "voidloom-shell-t" in str(ei.value), str(ei.value)


# ---------------------------------------------------------------- the payload corpus: MEMORY TEXT, nothing executes
def _corpus(sentinel):
    s = str(sentinel)
    return [
        "a note with backticks: `touch %s.bt`" % s,
        "a note with a subshell: $(touch %s.ds)" % s,
        "a note with a semicolon; touch %s.sc" % s,
        "a note with && touch %s.aa" % s,
        "a note with a pipe | touch %s.pp" % s,
        "a note with a newline\ntouch %s.nl\nand more" % s,
        "a note naming $HOME and ${PATH} and $(id -u)",
        "a note quoting the placeholder: '{prompt}' and \"{prompt}\" and {model}",
        "--yolo a note that begins like a flag",
        "a note with a glob * and ~ and a quote ' and a backslash \\ and a NUL-free tab\t.",
    ]


def test_memory_text_with_shell_payloads_reaches_the_program_byte_for_byte_and_nothing_executes(w):
    sentinel = w.root / "SENTINEL"
    F.make_fake(w.bin, "ollama", kind="ollama-run", log=str(w.log), reply="a local reply")
    F.record(thinker, "local", "--command", "ollama run {model} {prompt}", "--model", "tiny")
    for text in _corpus(sentinel):
        thinker.think(text, purpose="whisper_network", timeout_s=30)
        call = [r for r in F.read_log(w.log) if not r.get("end")][-1]
        assert call["argv"][-1] == thinker._safe_prompt(thinker.TEXT_CONTEXT + "\n\n" + text), call["argv"][-1][-80:]
    left = sorted(p.name for p in w.root.iterdir() if p.name.startswith("SENTINEL"))
    assert left == [], left


def test_the_same_corpus_through_a_real_non_shell_program_executes_nothing(w):
    sentinel = w.root / "SENTINEL"
    cand = _cand("/bin/echo {prompt}", accept_agentic=True)     # a real binary, class none, accepted for this test
    fields = dict(cand, proven_at=thinker._now(), answered_by="test", source="thinker-set")
    thinker._commit_record(fields, thinker._Proven(True, cand, {}))
    for text in _corpus(sentinel):
        r = thinker.think(text, purpose="whisper_network", timeout_s=30)
        assert r.text == (thinker.TEXT_CONTEXT + "\n\n" + text).strip(), r.text[-80:]
    left = sorted(p.name for p in w.root.iterdir() if p.name.startswith("SENTINEL"))
    assert left == [], left

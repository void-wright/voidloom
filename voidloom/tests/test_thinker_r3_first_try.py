# -*- coding: utf-8 -*-
"""Round 3 G1, item 1 + carried F2 + the proof's time bound: a stranger's FIRST install either proves first time or
refuses with a cause they can act on (ROUND3_PLAN.md, ruler VL-MC-v1.2 criterion (i); memories #23616, #23617).

MEASURED before this round (rP_recheck F1): Claude Code 2.1.287 STALLS on the proof's tool step -- the stream carries only
36-byte SSE pings that keep resetting the CLI's own idle clock -- on 3 of 7 recorded tool steps. The proof is right to
call a stall UNMEASURED, so one attempt made the owner's first install a coin flip (run 1 refused, runs 2 and 3 passed),
and the refusal said "Fix it with docs/OVERNIGHT_THINKER_SETUP.md Part C" -- sending him to redo a VALID token.

Bound here, each against a Claude double whose tool-step behaviour is a SEQUENCE (one entry per attempt):
  * 1a  a clean stall (timeout, both folders read empty, the switches on) is asked AGAIN, in a fresh sandbox, inside the
        SAME total wall clock; each attempt is decided by its own evidence: a later refusal holds, a later file is BROKE,
        a stall on every attempt stays unmeasured; a file, an error or a held is never re-asked;
  * 1c  every failed proof names ONE cause and the step that matches it; only a credential cause names the guide's Part;
  * F2  a catalog that does not DECLARE a CLI's environment switches (tool_off_env missing) reads unmeasured, never held;
  * the time bound: the transient unit's RuntimeMaxSec and the caller's own clock cover the WHOLE proof (answer + tool).
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude_2_1_287_tool_call_unparsed.json"
LINE = "The model's tool call could not be parsed (retry also failed)."

# tool_seq: one behaviour per TOOL attempt (the last repeats): hang | hang+cwd | fixture | fixture+cwd | text | other-error
# | auth | quota | network ; answer: ok | hang | wrong | auth
DOUBLE = r'''#!%(py)s
import json, os, re, sys, time
from pathlib import Path
here = Path(__file__)
cfg = json.loads(here.with_name(here.name + ".json").read_text())
fx = json.loads(Path(%(fixture)r).read_text())
argv = sys.argv[1:]
p = argv[-1] if argv else ""
def envelope(text, is_error=False):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text}))
m = re.search(r"What is (\d+) plus 1\?", p)
f = re.search(r"Create an empty file named (\S+) in the current directory", p)
with open(cfg["log"], "a") as fh:
    fh.write(json.dumps({"t": time.time(), "step": "answer" if m else ("tool" if f else "organ"), "cwd": os.getcwd(),
                         "home": os.environ.get("HOME"),
                         "advisor": os.environ.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL")}) + "\n")
if m:
    how = cfg.get("answer", "ok")
    if how == "hang":
        time.sleep(3600)
    if how == "auth":
        print("Invalid API key - please run /login"); sys.exit(1)
    envelope(str(int(m.group(1)) + (2 if how == "wrong" else 1))); sys.exit(0)
if f:
    cnt = here.with_name(here.name + ".count")
    n = int(cnt.read_text()) if cnt.exists() else 0
    cnt.write_text(str(n + 1))
    seq = cfg.get("tool_seq") or ["fixture"]
    how = seq[min(n, len(seq) - 1)]
    name = f.group(1)
    if how.endswith("+cwd"):
        Path(name).write_text("")
    if how.startswith("hang"):
        time.sleep(3600)
    if how == "other-error":
        envelope("Something else failed.", True); sys.exit(1)
    if how == "auth":
        envelope("Invalid API key - please run /login", True); sys.exit(1)
    if how == "quota":
        envelope("Claude AI usage limit reached", True); sys.exit(1)
    if how == "network":
        envelope("API Error: Connection error. (could not connect)", True); sys.exit(1)
    if how == "text":
        envelope('<invoke name="Write">\n<parameter name="file_path">%%s</parameter>\n</invoke>' %% name); sys.exit(0)
    sys.stdout.write(fx["stdout"]); sys.stderr.write(fx["stderr"]); sys.exit(fx["rc"])
envelope("an organ answer")
'''


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_r3_first_try_", dir=str(SCRATCH)))
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
    cnt = Path(str(w.fake) + ".count")
    if cnt.exists():
        cnt.unlink()


def _probe(w, timeout_s=6.0, cat=None, **b):
    _behave(w, **b)
    cat = cat or thinker.load_catalog()
    t0 = time.monotonic()
    res = thinker.probe_candidate(thinker.make_candidate(cat, "claude"), timeout_s=timeout_s, cat=cat)
    res["_wall"] = time.monotonic() - t0
    return res


def _rows(w):
    return [json.loads(l) for l in w.calls.read_text().splitlines()] if w.calls.exists() else []


def _tool_rows(w):
    return [r for r in _rows(w) if r["step"] == "tool"]


# ------------------------------------------------------------------------------------- 1a: the bounded re-ask on a STALL
def test_the_catalog_gives_claude_three_tool_attempts_and_no_one_else_more_than_one():
    cat = thinker.load_catalog()
    got = {b: e.get("probe_tool_attempts", 1) for b, e in cat["backends"].items()}
    assert got["claude"] == 3, got
    assert all(v == 1 for b, v in got.items() if b != "claude"), got


def test_a_stall_then_the_cli_refusal_is_held_on_the_second_attempt(w):
    res = _probe(w, tool_seq=["hang", "fixture"])
    assert res["passed"] is True and res["tool_block"] == "held", res
    at = res.get("tool_attempts") or []
    assert [a["verdict"] for a in at] == ["stall", "held"], at
    assert at[0]["evidence"]["cwd_entries"] == [] and at[0]["evidence"]["checked_before_delete"] is True, at
    rows = _tool_rows(w)
    assert len(rows) == 2 and rows[0]["cwd"] != rows[1]["cwd"], "each attempt must run in its OWN fresh sandbox"


def test_a_stall_then_a_written_file_is_broke(w):
    res = _probe(w, tool_seq=["hang", "fixture+cwd"])
    assert res["tool_block"] == "BROKE" and res["passed"] is False, res
    assert [a["verdict"] for a in res["tool_attempts"]] == ["stall", "BROKE"], res["tool_attempts"]
    assert res.get("cause") == "tool-broke", res


def test_a_stall_then_a_text_answer_is_held_by_text(w):
    res = _probe(w, tool_seq=["hang", "hang", "text"])
    assert res["passed"] is True and res.get("tool_block_by") == "text-answer", res
    assert len(res["tool_attempts"]) == 3, res["tool_attempts"]


def test_a_stall_on_every_attempt_stays_unmeasured_and_refuses_with_the_stall_cause(w):
    res = _probe(w, timeout_s=6.0, tool_seq=["hang"])
    assert res["passed"] is False and res["tool_block"].startswith("unmeasured (stall"), res
    assert [a["verdict"] for a in res["tool_attempts"]] == ["stall", "stall", "stall"], res["tool_attempts"]
    assert res.get("cause") == "stall", res
    assert "Part" not in res.get("next_step", "x Part"), res.get("next_step")
    assert "credential works" in res["next_step"] and "e) not now" in res["next_step"], res["next_step"]


def test_the_tool_step_keeps_the_same_total_wall_clock(w):
    res = _probe(w, timeout_s=6.0, tool_seq=["hang"])
    per = [a["wall_s"] for a in res["tool_attempts"]]
    assert per == [2.0, 2.0, 2.0], per
    assert res["_wall"] < 6.0 + 6.0, res["_wall"]       # the answer is instant; the tool step stays within ~timeout_s


@pytest.mark.parametrize("first,want_verdict", [("hang+cwd", "BROKE"), ("fixture", "held"), ("text", "held"),
                                                ("other-error", "no answer"), ("auth", "no answer")])
def test_only_a_clean_stall_is_asked_again(w, first, want_verdict):
    res = _probe(w, tool_seq=[first, "fixture"])
    at = res["tool_attempts"]
    assert len(at) == 1, at
    assert at[0]["verdict"] == want_verdict, at


def test_a_stall_without_the_tool_switches_is_decided_now_not_asked_again(w, monkeypatch):
    real = thinker._build_cli

    def without_switch(*a, **k):
        argv, env, model = real(*a, **k)
        env.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
        return argv, env, model
    monkeypatch.setattr(thinker, "_build_cli", without_switch)
    res = _probe(w, tool_seq=["hang", "fixture"])
    assert len(res["tool_attempts"]) == 1 and res["passed"] is False, res


def test_an_http_backend_never_gets_more_than_one_tool_attempt():
    cat = thinker.load_catalog()
    assert thinker._tool_attempts(cat["backends"]["local-http-ollama"]) == 1
    bad = json.loads(json.dumps(cat))
    bad["backends"]["local-http-ollama"]["probe_tool_attempts"] = 2
    assert any("probe_tool_attempts applies to a CLI" in x for x in thinker.validate_catalog(bad))
    for v in (0, 6, True, "3", 2.5):
        bad = json.loads(json.dumps(cat))
        bad["backends"]["claude"]["probe_tool_attempts"] = v
        assert any("probe_tool_attempts must be" in x for x in thinker.validate_catalog(bad)), v


# ------------------------------------------------------------------------------------- 1c: one cause, the matching step
@pytest.mark.parametrize("b,cause", [
    (dict(answer="auth"), "auth"),
    (dict(tool_seq=["quota"]), "quota"),
    (dict(tool_seq=["network"]), "network"),
    (dict(tool_seq=["other-error"]), "unknown"),
    (dict(tool_seq=["fixture+cwd"]), "tool-broke"),
    (dict(tool_seq=["hang"]), "stall"),
    (dict(answer="wrong"), "wrong-answer"),
    (dict(answer="hang"), "stall"),
    (dict(answer="wrong", tool_seq=["hang"]), "wrong-answer"),   # R3-02: a compound failure names the REAL cause
])
def test_every_refusal_names_one_cause_and_only_a_credential_cause_names_the_part(w, b, cause):
    res = _probe(w, timeout_s=4.0, **b)
    assert res["passed"] is False and res.get("cause") == cause, res
    step = res.get("next_step") or ""
    assert step, res
    if cause == "auth":
        assert "OVERNIGHT_THINKER_SETUP.md Part C" in step, step
    else:
        assert "Part C" not in step and "OVERNIGHT_THINKER_SETUP.md Part" not in step, step


def test_a_wrong_answer_then_a_stall_is_named_wrong_answer_never_stall(w):
    """R3-02: the model answered the sum WRONGLY, then stalled on the tool check every time. The cause is the wrong
    answer, and the step must never claim "the answer to the sum came back exactly" (it did not)."""
    res = _probe(w, timeout_s=4.0, answer="wrong", tool_seq=["hang"])
    assert res["passed"] is False and not res.get("nonce_ok"), res
    assert str(res.get("tool_block") or "").startswith("unmeasured (stall"), res      # the stall really happened too
    assert res.get("cause") == "wrong-answer", res
    assert "came back exactly" not in (res.get("next_step") or ""), res["next_step"]
    assert "Part C" not in (res.get("next_step") or ""), res["next_step"]


def test_the_set_verb_prints_the_cause_and_its_step_never_a_blanket_part_c(w):
    _behave(w, tool_seq=["hang"])
    out = thinker.run(["set", "claude", "--probe-env", "direct", "--timeout", "3", "--no-login"])
    assert out["_exit_code"] == 1, out
    assert out["data"]["cause"] == "stall", out["data"]
    assert "\n  cause: stall\n" in out["_human"], out["_human"]
    assert "Fix: docs/OVERNIGHT_THINKER_SETUP.md" not in out["_human"], out["_human"]
    assert thinker.read_record() is None


def test_the_systemd_path_failures_carry_a_cause_too(monkeypatch):
    monkeypatch.setattr(thinker.shutil, "which", lambda name: None)
    res = thinker._systemd_run_probe({"backend": "claude"}, "/nonexistent-env", 10.0, ["python3"])
    assert res["passed"] is False and res.get("cause") == "probe-env" and res.get("next_step"), res


# ------------------------------------------------------------------------------------- F2: a missing declaration
def test_a_catalog_without_claudes_tool_off_env_reads_unmeasured_never_held(w):
    cat = json.loads(json.dumps(thinker.load_catalog()))
    del cat["backends"]["claude"]["tool_off_env"]
    res = _probe(w, cat=cat, tool_seq=["fixture"])
    assert res["passed"] is False and res["tool_block"].startswith("unmeasured"), res
    assert "does not declare" in res["tool_block"], res["tool_block"]
    assert res.get("cause") == "tool-switches", res
    rows = _tool_rows(w)
    assert rows and rows[-1]["advisor"] is None        # the removal really did drop the switch from the child


def test_every_cli_backend_declares_its_environment_switches():
    cat = thinker.load_catalog()
    for b, e in cat["backends"].items():
        if e["transport"] == "cli" and e.get("prompt_via") != "template":
            assert isinstance(e.get("tool_off_env"), dict), b


# ------------------------------------------------------------------------------------- the proof's whole time bound
def test_the_transient_unit_and_the_callers_clock_cover_the_whole_proof(monkeypatch, tmp_path):
    seen = {}

    def fake_capture(argv, *, timeout_s, cwd=None, env=None, cap=thinker.OUTPUT_CAP_BYTES):
        seen["argv"], seen["timeout"] = argv, timeout_s
        return 0, b'{"passed": true}\n', b"", ""
    monkeypatch.setattr(thinker.shutil, "which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setattr(thinker, "_capture", fake_capture)
    res = thinker._systemd_run_probe({"backend": "claude"}, str(tmp_path / "env"), 240.0, ["python3"])
    assert res["passed"] is True, res
    rms = [a for a in seen["argv"] if str(a).startswith("RuntimeMaxSec=")]
    assert rms, seen["argv"]
    unit_max = int(rms[0].split("=", 1)[1])
    whole = 2 * 240.0                                   # the answer's 240 s + the tool step's 240 s in total
    assert unit_max >= whole + 30, unit_max             # the manager never kills a proof that is still in its budget
    assert seen["timeout"] > unit_max, (seen["timeout"], unit_max)


# ------------------------------------------------------------------------------------- item 3: a saved credential
def test_question_facts_say_a_saved_code_is_already_saved(w):
    facts = thinker.question_facts(env_file=str(w.home / "no-env-file"))
    s = (facts.get("saved") or {}).get("a") or {}
    assert s.get("line") == "a code is already saved; press Enter to prove it", facts.get("saved")
    assert s.get("fingerprint") and "fake-credential" not in json.dumps(facts), s


# ------------------------------------------------------------------------- R3-07: the env file's legacy line IS saved
def test_r3_07_the_env_files_legacy_token_line_counts_as_a_code_already_saved(w, monkeypatch):
    """R3-07 (the Round 3 reflection): the guide promises that a code saved as the older CLAUDE_CODE_OAUTH_TOKEN= line makes
    the installer say 'a code is already saved; press Enter to prove it', and on Oct 1 the owner's install proved that
    legacy line without asking him to paste it. The code read only claude.env, the sidecar and THIS process's variable --
    so with the line only in the env file, saved == {} and Part C asked for `claude setup-token` and a paste. Now the env
    file's EFFECTIVE value (read the way systemd reads it: the timers, and the proof run through them, load that file)
    is the third legacy source, after the sidecar and the process variable."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    saved = w.thinker_home / "claude.env"            # the fixture saves one; this brain predates claude.env
    if saved.exists():
        saved.unlink()
    assert thinker.resolve_credential("claude") is None              # no env file yet: nothing saved anywhere
    tok = "sk-ant-oat01-" + "e" * 60
    envf = w.home / ".voidloom" / "wizard-library-env"
    envf.parent.mkdir(parents=True, exist_ok=True)
    envf.write_text("# BEGIN Voidloom MANAGED\nPATH=/usr/bin:/bin\n# END Voidloom MANAGED\nCLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok)
    c = thinker.resolve_credential("claude")
    assert c is not None and c.legacy and c.fingerprint == thinker._fp(tok), c
    assert c.source == "env file %s, line 4 (CLAUDE_CODE_OAUTH_TOKEN=)" % envf, c.source
    facts = thinker.question_facts(env_file=str(envf))
    s = (facts.get("saved") or {}).get("a") or {}
    assert s.get("line") == "a code is already saved; press Enter to prove it" and s.get("legacy") is True, facts["saved"]
    assert s.get("where") == "env file %s, line 4 (CLAUDE_CODE_OAUTH_TOKEN=)" % envf and s.get("fingerprint") == thinker._fp(tok), s
    assert tok not in json.dumps(facts) and "e" * 20 not in json.dumps(facts), "a credential value was printed"
    # the order is kept: this process's variable (what the timers' env file puts there) comes before the file's line
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-from-the-environment-test-only")
    assert thinker.resolve_credential("claude").source.startswith("environment")

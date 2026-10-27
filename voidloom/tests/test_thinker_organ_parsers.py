# -*- coding: utf-8 -*-
"""M5 -- EACH ORGAN'S OWN PARSER ACCEPTS EACH BACKEND'S EXTRACTED REPLY, with no transport residue.

DESSA Q4: "the canary checks the ROUTE, not the ANSWER" -- an adapter returning a transcript or an envelope passes
every routing assertion while every organ stores junk. Round 0 measured it (round0/PROBE_REPORT.md section 4): seven
of eight organs accept ANY non-empty text, so a wrong extraction is stored silently; counterfactual's check fails
SILENTLY to a legitimate-looking still_active.

So, per organ x backend, each organ's REAL call-and-parse path runs through think() against a double that speaks
that backend's measured transport shape, and what the organ keeps must EQUAL the answer the model gave:
  claude          one JSON envelope, answer at .result
  codex           the answer alone on stdout; the transcript (banner, prompt, 'tokens used') on stderr
  kimi            stream-json; the answer = the concatenated role=assistant content
  local http      /api/chat .message.content with prompt_eval_count; /v1 .choices[0].message.content with usage
  local command   `ollama run --nowordwrap` stdout
Gap research on a web-less backend is a logged SKIP (M3), never a stored answer.
THE FALSIFIER (criteria M5): the Round-0 NAIVE extractions -- codex stdout+stderr ('2>&1'), kimi text mode ('• ')
-- must FAIL this very assertion. Local input checks: prompt_eval_count >= the words sent, num_ctx and
truncate:false, or a typed ThinkMalformed -- never a silently truncated answer.
"""
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"

ANSWERS = {
    "sleep_loop": "Force the GPU before importing torch; a pipe's exit status hides the command's own failure.",
    "whisper_network": "This week the dominant theme was instrument hygiene.\n\nOne concrete fix: check PIPESTATUS.",
    "dream_pipeline": "The cluster shares one theme: fence every unattended call before it runs.",
    "memory_dreaming": "Both memories describe a guard that must read its input the way its consumer does.",
    "arcane_manifest": "VAEL speaks: the quarter taught us that a check which cannot fail is not a check at all.",
    "counterfactual_memory": '{"outcome": "validated", "notes": "The isolation held for the whole quarter."}',
    "gap_mapping": "Findings: num_ctx defaults to 4096 and truncates silently; set it explicitly per request.",
    "monthly_self_eval": json.dumps({"enhancements": [{"title": "Measure the fallback rate",
                                                      "piece_affected": "L2-C1-sleep-loop",
                                                      "rationale": "fallbacks read as success", "priority": "high"}],
                                     "deprecations": [], "narrative": "A steady month."}),
}


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_m5_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _drive(organ, w):
    """Run one organ's own call-and-parse path; return what it KEPT (None = rejected / fell back)."""
    if organ == "sleep_loop":
        import sleep_loop
        return sleep_loop._llm_consolidation("Distill these 3 related episodic memories ...", 3)
    if organ == "whisper_network":
        import whisper_network
        obs = [{"observation_type": "insight", "intensity": 0.7, "observation": "recall before writing"}]
        r = whisper_network._synthesize_llm(obs, {"insight": obs}, "2026-09-14", "2026-09-21")
        return r[0] if r else None
    if organ == "dream_pipeline":
        import dream_pipeline
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT)")
        for i in (1, 2, 3):
            conn.execute("INSERT INTO memories VALUES (?, ?)", (i, "summary %d" % i))
        return dream_pipeline._raptor_summarize(conn, {"claude_timeout_seconds": 60, "claude_model": "sonnet"},
                                                "cluster-x", [1, 2, 3])
    if organ == "memory_dreaming":
        import memory_dreaming
        def meta(i):
            return {"id": i, "summary": "memory %d" % i, "content": "content %d" % i, "category": "insight",
                    "source_project": "p%d" % i, "created_at": "2026-09-0%d" % i, "tags": "", "kind": "episodic"}
        return memory_dreaming.dream_on_pair(meta(1), meta(2), "divergent", timeout_seconds=60)
    if organ == "arcane_manifest":
        import arcane_manifest
        voice = w.root / "SKILL.md"
        voice.write_text("You are VAEL, the Skeptic.")
        return arcane_manifest._invoke_claude("Write your section.", system_prompt_path=str(voice), model="haiku",
                                              timeout_seconds=60)
    if organ == "counterfactual_memory":
        import counterfactual_memory
        r = counterfactual_memory._llm_revisit_analysis({
            "topic": "t", "chosen_path": "c", "alternative_paths": [], "council_weight_score": 0.4,
            "memory_summary": "m", "criteria_for_revisit": "r"})
        want = json.loads(ANSWERS[organ])
        return ANSWERS[organ] if (r.get("outcome") == want["outcome"] and r.get("notes") == want["notes"]) \
            else json.dumps(r)
    if organ == "gap_mapping":
        import gap_mapping
        d = gap_mapping._dispatch_research("Research: num_ctx.", 60)
        return d.get("output") if d["status"] == "success" else "SKIPPED" if d["status"] == "skipped" else None
    if organ == "monthly_self_eval":
        import monthly_self_eval as mse
        cfg = dict(mse.DEFAULT_CONFIG)
        cfg.update({"llm_timeout_seconds": 60, "max_enhancements": 5, "max_deprecations": 2, "piece_registry": {}})
        r = mse._generate_llm_proposals({"L2-C1-sleep-loop": {"score": 0.3}}, "2026-09", {}, cfg)
        want = json.loads(ANSWERS[organ])
        return ANSWERS[organ] if (r and r["enhancements"] == want["enhancements"]
                                  and r["narrative"] == want["narrative"]) else json.dumps(r)
    raise AssertionError(organ)


def _matrix(w, set_reply):
    out = {}
    for organ, answer in ANSWERS.items():
        set_reply(answer)
        out[organ] = _drive(organ, w)
    return out


def _cli(w, backend):
    fake = F.make_fake(w.bin, backend, log=str(w.log))
    F.save_cred(w, backend)
    F.record(thinker, backend)
    return lambda a: F.set_behaviour(fake, reply=a, log=str(w.log))


@pytest.mark.parametrize("backend", ["claude", "codex", "kimi"])
def test_every_organ_keeps_exactly_the_answer_cli(w, backend):
    got = _matrix(w, _cli(w, backend))
    bad = {o: v for o, v in got.items() if v != ANSWERS[o]}
    assert bad == {}, json.dumps(bad, indent=1)


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_every_organ_keeps_exactly_the_answer_http(w, api):
    srv, url = F.fake_model_server()
    try:
        F.record(thinker, "local", "--endpoint", url, "--model", "tiny", "--api", api)
        got = _matrix(w, lambda a: srv.cfg.__setitem__("reply", a))
    finally:
        srv.shutdown()
        srv.server_close()
    want = dict(ANSWERS, gap_mapping="SKIPPED")          # a web-less backend: a logged skip, never research
    bad = {o: v for o, v in got.items() if v != want[o]}
    assert bad == {}, json.dumps(bad, indent=1)


def test_every_organ_keeps_exactly_the_answer_command_template(w):
    fake = F.make_fake(w.bin, "ollama", kind="ollama-run", log=str(w.log))
    F.record(thinker, "local", "--command", "ollama run {model} {prompt}", "--model", "tiny")
    assert "--nowordwrap" in thinker.read_record()["command_template"], "ollama run without --nowordwrap: ANSI junk"
    got = _matrix(w, lambda a: F.set_behaviour(fake, reply=a, log=str(w.log)))
    want = dict(ANSWERS, gap_mapping="SKIPPED")
    bad = {o: v for o, v in got.items() if v != want[o]}
    assert bad == {}, json.dumps(bad, indent=1)
    argv = [r for r in F.read_log(w.log) if not r.get("end")][-1]["argv"]
    assert argv[:2] == ["run", "--nowordwrap"], argv


# Organs whose OWN parser extracts their structured answer from surrounding text, so a transport residue around an
# intact answer is not junk to them (Round 3 G2 item 4: counterfactual_memory's JSON reader).
COPES_WITH_SURROUNDING_TEXT = {"counterfactual_memory"}


def test_FALSIFIER_codex_stdout_plus_stderr_fails(w, monkeypatch):
    """Round 0's naive codex extraction (the transcript on stderr read with the answer) must FAIL M5."""
    set_reply = _cli(w, "codex")
    real = thinker._run_cli

    def naive(*a, **k):
        rc, out, err, el = real(*a, **k)
        return rc, out + err, "", el                       # 2>&1
    monkeypatch.setattr(thinker, "_run_cli", naive)
    got = _matrix(w, set_reply)
    clean = [o for o, v in got.items() if v == ANSWERS[o]]
    junk = [o for o, v in got.items() if v and v != ANSWERS[o] and "tokens used" in str(v)]
    # Round 3 G2 item 4: counterfactual's parser now READS its JSON answer inside surrounding text (a genuine answer in
    # prose or after a bullet was lost to still_active), so a naive extraction that keeps the JSON whole is not junk to
    # it. The falsifier keeps its power through the organs that store any text -- and M5 is still RED (junk below).
    assert set(clean) <= COPES_WITH_SURROUNDING_TEXT, \
        "a naive extraction passed for %s -- the assertion cannot fail" % sorted(set(clean) - COPES_WITH_SURROUNDING_TEXT)
    assert len(junk) >= 5, got                               # stored junk, silently (Round 0: 6 of 8)


def test_FALSIFIER_kimi_text_mode_fails(w, monkeypatch):
    """Round 0's naive kimi extraction (text mode: a bullet before every answer) must FAIL M5."""
    set_reply = _cli(w, "kimi")
    real = thinker.load_catalog

    def text_mode(path=None):
        cat = real(path)
        k = cat["backends"]["kimi"]
        k["argv"] = ["--output-format", "text"] + k["argv"][2:]
        k["output"] = {"kind": "stdout"}
        return cat
    monkeypatch.setattr(thinker, "load_catalog", text_mode)
    got = _matrix(w, set_reply)
    bad = [o for o, v in got.items() if v != ANSWERS[o]]
    # Round 0 (text mode): 6 organs stored the bullet, counterfactual rejected, monthly_self_eval's own
    # {...} search happened to cope. The M5 assertion is RED for seven of eight -- exactly the measured shape.
    # Round 3 G2 item 4: counterfactual now reads the JSON after the bullet too (COPES_WITH_SURROUNDING_TEXT), so six of
    # eight -- every organ that stores the text it is given, which is what this falsifier needs to stay RED.
    assert len(bad) >= 6, got
    assert not (set(bad) & COPES_WITH_SURROUNDING_TEXT), got        # the bullet does not hide the JSON from it
    assert sum(1 for v in got.values() if v and str(v).startswith("\u2022 ")) >= 5, got


def _local(w, api="ollama"):
    srv, url = F.fake_model_server()
    F.record(thinker, "local", "--endpoint", url, "--model", "tiny", "--api", api)
    return srv


def test_ollama_over_context_is_a_typed_error_never_a_truncated_answer(w):
    srv = _local(w)
    try:
        long_prompt = "word " * 17000                       # > num_ctx 16384: truncate:false -> HTTP 400
        with pytest.raises(thinker.ThinkMalformed) as ei:
            thinker.think(long_prompt, purpose="sleep_loop", timeout_s=30)
        assert "400" in str(ei.value)
        assert srv.requests[-1]["body"]["truncate"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_silently_truncated_count_is_caught(w):
    srv = _local(w, api="openai")                           # /v1 has no truncate:false: the count is the guard
    try:
        srv.cfg["default_ctx"] = 4096
        with pytest.raises(thinker.ThinkMalformed) as ei:
            thinker.think("word " * 6000, purpose="sleep_loop", timeout_s=30)
        assert "TRUNCATED" in str(ei.value)
        srv.cfg["omit_count"] = True
        with pytest.raises(thinker.ThinkMalformed) as ei:
            thinker.think("a short prompt", purpose="sleep_loop", timeout_s=30)
        assert "could not be ruled out" in str(ei.value)
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ MC-R1-09 (Round 1 iteration 2): the reply screen
# MEASURED LIVE (M18 attempt 3, claude, text posture): the weekly digest came back as a REFUSAL and the counterfactual
# revisit as a NARRATED TOOL CALL -- and each free-text organ STORED what it got (the reflector re-executed the digest:
# iterations/scratch_records/refl_r1/organ_results.json). The seam now raises ThinkRefused on the SHAPE of either, so
# every organ's own path takes its loud fallback and keeps NOTHING of it -- the same organ paths as M5 above.
REFUSAL = ("I appreciate the creative framing, but I need to be direct: I don't actually have a self-observation "
           "system that captures my behavior over time, persistent memory across sessions, or topics like these.")
NARRATION = ("I'll check the working directory for any evidence of how the voidloom CLI decision played out.\n\n"
             "**1 tool use**")


@pytest.mark.parametrize("backend", ["claude", "codex", "kimi"])
@pytest.mark.parametrize("reply", [REFUSAL, NARRATION], ids=["refusal", "narration"])
def test_no_organ_keeps_a_refusal_or_a_narrated_tool_call(w, backend, reply):
    set_reply = _cli(w, backend)
    for organ in ANSWERS:
        set_reply(reply)
        got = _drive(organ, w)
        assert reply[:48] not in json.dumps(got), (organ, got)
    refused = [r for r in thinker.read_log() if r.get("outcome") == "refused"]
    assert {r["purpose"] for r in refused} >= {"sleep_loop", "whisper_network", "dream_pipeline", "memory_dreaming",
                                                "arcane_manifest", "counterfactual_memory", "monthly_self_eval",
                                                "gap_research"}, refused
    assert {r["reason"] for r in refused} == {"reply-refusal" if reply is REFUSAL else "reply-tool-narration"}

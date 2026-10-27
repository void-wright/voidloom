# -*- coding: utf-8 -*-
"""M3 -- POSTURE BY PURPOSE, proven by the TOOL-BLOCK PROBE with its controls, on every backend x purpose.

Owner, 2026-09-26: the minimum tools each organ needs now, widened only by a deliberate, recorded act. So:
  * gap research gets web search and fetch ONLY (the owner's ruling 2026-09-27: no file tools); the seven synthesis
    organs get TEXT ONLY;
  * a backend with no web makes gap research a LOGGED SKIP (the 0.5B invented research in Round 0, and the
    Warden screen allowed it) -- never a call.
The probe asks the backend for the exact answer to a small sum ('What is N plus 1?'), then -- a second call -- to create a
file in the call's empty working folder; the tool block is graded by SIDE EFFECTS only (Round 0: two backends invented tool outcomes in their text):
  treatment         the posture the seam builds -> the file is ABSENT and the answer comes back (held)
  positive control  a backend that DOES write   -> the tripwire fires: ThinkRefused, output discarded, row BROKE
  negative control  a dead backend              -> a typed ThinkUnavailable, a logged row, never a success
What each backend's argv/agent file SAYS for each purpose is read from the double's own log -- the argv the CLI
actually received -- never from the seam's intentions. The real CLIs' behaviour under these exact flags is Round 0's
(facts.json: CT3, XT3/XT2, KT1/KT2); the live smoke per adapter is in the cold gate.
At 0b061d7: no seam, no catalog; every organ ran claude with the research set (Read + web) for a text job -- red.
"""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
CLI_BACKENDS = ("claude", "codex", "kimi")


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_posture_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _setup(w, backend, **behaviour):
    fake = F.make_fake(w.bin, backend, log=str(w.log), **behaviour)
    F.save_cred(w, backend)
    F.record(thinker, backend)
    return fake


def _last_call(w):
    starts = [r for r in F.read_log(w.log) if not r.get("end")]
    return starts[-1]


@pytest.mark.parametrize("backend", CLI_BACKENDS)
def test_text_purposes_get_no_tools(w, backend):
    _setup(w, backend, reply="a synthesis")
    for purpose in ("sleep_loop", "whisper_network", "dream_pipeline", "memory_dreaming", "arcane_manifest",
                    "counterfactual_memory", "monthly_self_eval"):
        r = thinker.think("summarise", purpose=purpose, timeout_s=30)
        assert r.mode == "text" and r.text == "a synthesis"
        call = _last_call(w)
        argv = call["argv"]
        if backend == "claude":
            i = argv.index("--tools")
            assert argv[i + 1] == "" and argv[i + 2] == "--permission-mode", argv
            assert "--safe-mode" in argv and "--allowedTools" not in argv
        elif backend == "codex":
            assert 'web_search="disabled"' in argv and "shell_tool" in argv and argv[argv.index("-s") + 1] == "read-only"
        else:
            assert "tools: []" in call["agent_file"], call["agent_file"]
            assert "--auto" not in argv and "--yolo" not in argv


@pytest.mark.parametrize("backend", CLI_BACKENDS)
def test_gap_research_gets_the_web_only(w, backend):
    _setup(w, backend, reply="research findings")
    r = thinker.think("research X", purpose="gap_research", timeout_s=30)
    assert r.mode == "research"
    call = _last_call(w)
    argv = call["argv"]
    if backend == "claude":
        assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
        assert argv[argv.index("--disallowedTools") + 1] == "mcp__*,Bash,Write,Edit,NotebookEdit,Read,Glob,Grep"
    elif backend == "codex":
        assert 'web_search="disabled"' not in argv and "shell_tool" in argv
    else:
        assert "tools: [WebSearch, FetchURL]" in call["agent_file"]
        assert "disallowedTools: [Bash, Write, Edit, Read, Glob, Grep]" in call["agent_file"]


@pytest.mark.parametrize("backend", CLI_BACKENDS + ("local-command",))
def test_tool_block_probe_treatment_and_positive_control(w, backend):
    if backend == "local-command":
        fake = F.make_fake(w.bin, "ollama", kind="ollama-run", log=str(w.log))
        F.record(thinker, "local", "--command", "ollama run --nowordwrap {model} {prompt}", "--model", "tiny")
        cand = thinker.read_record()
    else:
        fake = _setup(w, backend)
        cand = thinker.read_record()
    # treatment: the file stays absent, the word comes back
    res = thinker.probe_candidate(cand, timeout_s=30)
    assert res["passed"] and res["tool_block"] == "held" and res["nonce_ok"], res
    # positive control: a backend that writes -> the tripwire fires, the output is discarded
    F.set_behaviour(fake, mode="write", log=str(w.log))
    res = thinker.probe_candidate(cand, timeout_s=30)
    assert res["passed"] is False and res["tool_block"] == "BROKE", res
    with pytest.raises(thinker.ThinkRefused) as ei:
        thinker.think("anything", purpose="sleep_loop", timeout_s=30)
    assert "tool-was-used.txt" in str(ei.value)
    rows = thinker.read_log()
    assert rows[-1]["outcome"] == "refused", rows[-1]


@pytest.mark.parametrize("backend", CLI_BACKENDS)
def test_negative_control_dead_backend_is_a_loud_row_never_success(w, backend):
    fake = _setup(w, backend)
    said = {"claude": "API Error: Connection refused (ECONNREFUSED)",
            "codex": "ERROR: workspace routing discovery failed",
            "kimi": "error: failed to run prompt: provider.connection_error: Connection error."}[backend]
    F.set_behaviour(fake, mode="fail", said=said, log=str(w.log))
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="whisper_network", timeout_s=30)
    assert ei.value.reason == "unreachable", ei.value
    assert said.split(":")[0] in str(ei.value)
    assert thinker.read_log()[-1]["outcome"] == "unavailable"
    res = thinker.probe_candidate(thinker.read_record(), timeout_s=30)
    assert res["passed"] is False


def test_organ_level_negative_control_is_a_fallback_row(w):
    """The dead backend reaches an ORGAN as its documented fallback -- never as a success."""
    import whisper_network
    fake = _setup(w, "claude")
    F.set_behaviour(fake, mode="fail", said="API Error: Connection refused")
    obs = [{"observation_type": "insight", "intensity": 0.7, "observation": "a whisper"}]
    assert whisper_network._synthesize_llm(obs, {"insight": obs}, "2026-09-14", "2026-09-21") is None
    assert thinker.read_log()[-1]["purpose"] == "whisper_network"
    assert thinker.read_log()[-1]["outcome"] == "unavailable"


def test_http_backends_are_tool_free_by_construction_and_dead_is_loud(w):
    srv, url = F.fake_model_server(reply="local text")
    try:
        F.record(thinker, "local", "--endpoint", url, "--model", "tiny")
        r = thinker.think("hello", purpose="sleep_loop", timeout_s=30)
        assert r.text == "local text" and r.backend_id == "local-http-ollama"
        body = srv.requests[-1]["body"]
        assert "tools" not in body and body["truncate"] is False and body["options"]["num_ctx"] == 16384
    finally:
        srv.shutdown()
        srv.server_close()
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("hello", purpose="sleep_loop", timeout_s=10)
    assert ei.value.reason == "unreachable"


@pytest.mark.parametrize("shape", ["http", "command"])
def test_research_on_a_web_less_backend_is_a_logged_skip(w, shape):
    import gap_mapping
    if shape == "http":
        srv, url = F.fake_model_server(reply="INVENTED RESEARCH")
        F.record(thinker, "local", "--endpoint", url, "--model", "tiny")
    else:
        srv = None
        F.make_fake(w.bin, "ollama", kind="ollama-run", log=str(w.log), reply="INVENTED RESEARCH")
        F.record(thinker, "local", "--command", "ollama run --nowordwrap {model} {prompt}", "--model", "tiny")
    try:
        n_before = len(srv.requests) if srv else len(F.read_log(w.log))
        with pytest.raises(thinker.ThinkSkipped):
            thinker.think("research X", purpose="gap_research", timeout_s=30)
        d = gap_mapping._dispatch_research("research X", 30)
        assert d["status"] == "skipped" and "never invented research" in d["message"], d
        n_after = len(srv.requests) if srv else len(F.read_log(w.log))
        assert n_after == n_before, "a web-less backend must never be ASKED to research"
        assert thinker.read_log()[-1]["outcome"] == "skipped"
    finally:
        if srv:
            srv.shutdown()
            srv.server_close()


def test_an_unknown_purpose_never_defaults_to_a_posture(w):
    _setup(w, "claude")
    with pytest.raises(ValueError):
        thinker.think("x", purpose="some_new_organ", timeout_s=10)


def test_the_catalog_refuses_an_entry_missing_a_measured_field(w):
    cat = json.loads(thinker.CATALOG_PATH.read_text(encoding="utf-8"))
    for bid, field in (("codex", "posture"), ("kimi", "hook_free_env"), ("claude", "credential"),
                       ("local-http-ollama", "input_check"), ("local-http-ollama", "truncate")):
        bad = json.loads(json.dumps(cat))
        del bad["backends"][bid][field]
        problems = thinker.validate_catalog(bad)
        assert any(bid in p and field in p for p in problems), (bid, field, problems)
    bad = json.loads(json.dumps(cat))
    bad["backends"]["claude"]["posture"]["text"] = ["--tools", "", "--max-turns", "1"]
    bad["backends"]["claude"]["posture"]["text"][2] = "x"
    assert any("variadic" in p for p in thinker.validate_catalog(bad))
    bad = json.loads(json.dumps(cat))
    bad["backends"]["kimi"]["argv"].append("--auto")
    assert any("banned" in p for p in thinker.validate_catalog(bad))
    assert thinker.validate_catalog(cat) == []

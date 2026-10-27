# -*- coding: utf-8 -*-
"""Round 2 wave 1 (model-choice unit): the LOW findings of Round 1's closing reflection, each by its literal replacement
text, plus the class members the sweep found beyond it.

  MC-R1-13  agentic switches behind --accept-agentic: short aliases (-y), switches whose VALUE pre-approves
            (--permission-mode acceptEdits, --allowedTools ...), --approve-for-me, -s workspace-write -- rows in
            test_thinker_command_template.py REFUSED (none unlocked by the owner's acceptance).
  MC-R1-14  runners that execute their argument or run another program, and the tool-free label read from the
            RESOLVED binary (a link NAMED ollama that resolves to perl is not a tool-free runner).
  MC-R1-15  the proof certifies an ANSWER, not a model: no site claims more.
  MC-R1-16  every description of the proof says what it now does (a sum's exact answer), everywhere; the three
            changed seed entries SUPERSEDE their old revisions so a seeded brain receives the new words.
  MC-R1-17  an explicitly EMPTY value in the seam's own CLI (`thinker set` / `probe`) is refused, never taken as absent.
  MC-R1-19  the thinker migration's number re-derived across main and every origin branch (039); no number shared.
  MC-R1-20  a template refusal's message carries the token it reports.
"""
import json
import os
import re
import shutil
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
    root = Path(tempfile.mkdtemp(prefix="thinker_low_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _cand(template, model="m", **kw):
    return thinker.make_candidate(thinker.load_catalog(), "local", command=template, model=model, **kw)


# ------------------------------------------------------------------------------------------------------ MC-R1-14
RUNNERS = [   # (template, kind, token) -- the reflector's T12-T68 members, accepted by the frozen seam WITH acceptance
    ("setpriv --reuid=1000 ollama run {model} {prompt}", "wrapper", "setpriv"),
    ("uv run runner.py {prompt}", "wrapper", "uv"),
    ("conda run -n base python runner.py {prompt}", "wrapper", "conda"),
    ("npx -c {prompt}", "wrapper", "npx"),
    ("tmux new-session -d {prompt}", "wrapper", "tmux"),
    ("screen -dm {prompt}", "wrapper", "screen"),
    ("docker exec box {prompt}", "wrapper", "docker"),
    ("pipx run tool {prompt}", "wrapper", "pipx"),
    ("numactl --cpunodebind=0 ollama run {model} {prompt}", "wrapper", "numactl"),
    ("rlwrap ollama run {model} {prompt}", "wrapper", "rlwrap"),
    ("unbuffer ollama run {model} {prompt}", "wrapper", "unbuffer"),
    ("faketime now ollama run {model} {prompt}", "wrapper", "faketime"),
    ("sqlite3 x.db {prompt}", "interpreter", "sqlite3"),
    ("vim -c {prompt}", "interpreter", "vim"),
    ("ex -c {prompt}", "interpreter", "ex"),
    ("ipython -c x {prompt}", "interpreter", "-c"),
    ("python3 -m timeit {prompt}", "interpreter", "-m"),
    ("python3 -m code {prompt}", "interpreter", "-m"),
]


@pytest.mark.parametrize("template,kind,token", RUNNERS, ids=[r[0] for r in RUNNERS])
def test_a_runner_that_executes_its_argument_is_refused_even_with_acceptance(w, template, kind, token):
    with pytest.raises(thinker.TemplateRefused) as ei:
        _cand(template, accept_agentic=True)
    assert ei.value.kind == kind and ei.value.token == token, (ei.value.kind, ei.value.token, str(ei.value))


def test_python_m_a_module_that_is_a_program_is_still_allowed(w):
    c = _cand("python3 -m my_runner {prompt}", accept_agentic=True)
    assert c["tool_class"] == "none" and c["accepted_agentic_risk"] == "true", c


def test_the_tool_free_label_follows_the_resolved_binary_not_the_typed_name(w):
    perl = shutil.which("perl")
    if not perl:
        pytest.skip("no perl on this host: the resolved-binary label is not measured here")
    (w.bin / "ollama").symlink_to(perl)             # a link NAMED ollama that resolves to perl
    with pytest.raises(thinker.TemplateRefused) as ei:
        _cand("ollama run {model} {prompt}")        # no acceptance: it must NOT be classed tool-free by construction
    assert ei.value.kind == "agentic-unproven", (ei.value.kind, str(ei.value))
    F.make_fake(w.bin.parent / "real", "ollama", kind="ollama-run")      # a real runner file named ollama keeps it
    (w.bin / "ollama").unlink()
    (w.bin / "ollama").symlink_to(w.bin.parent / "real" / "ollama")
    assert _cand("ollama run {model} {prompt}")["tool_class"] == "by-construction"


# ------------------------------------------------------------------------------------------------------ MC-R1-15/16
PROOF_FILES = ["README.md", "docs/ONBOARDING_GUIDE.md", "docs/OVERNIGHT_THINKER_SETUP.md", "install/write_set.json",
               "install.sh", "tools/token_doctor.py", "organs/thinker.py", "voidloom/tests/test_thinker_posture.py",
               "voidloom/tests/test_thinker_probe_wording.py", "voidloom/tests/test_thinker_command_template.py"]
STALE = re.compile(r"exact[- ]word\b|\bword call\b|nonce (?:came|comes) back|proof proves a model|is not a model now fails",
                   re.I)
PROOF_SEEDS = ("anchor:seed_thinker_asked_never_assumed", "anchor:seed_kimi_console_key",
               "anchor:seed_claude_setup_token_posture")


def test_every_description_of_the_proof_says_what_it_now_does():
    """The proof asks 'What is N plus 1?' and demands the exact answer (since Round 1 iteration 2); no shipped description
    still says 'an exact word', 'the word call' or 'the nonce came back', and none claims it proves a model."""
    bad = []
    for rel in PROOF_FILES:
        for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if STALE.search(line):
                bad.append("%s:%d" % (rel, i))
    pack = json.loads((ROOT / "seed" / "seed_pack.json").read_text(encoding="utf-8"))
    for m in pack["memories"]:
        words = re.sub(r"PROVEN \([^)]*\):[^.]*\.", "", m.get("summary", "") + " " + m.get("content", ""))  # dated history
        if any(a in m.get("tags", "") for a in PROOF_SEEDS) and STALE.search(words):
            bad.append("seed %s" % [a for a in PROOF_SEEDS if a in m["tags"]][0])
    assert not bad, bad


def test_the_changed_proof_seeds_supersede_their_old_revisions():
    """A brain seeded with a revision of these entries that said 'exact word' must RECEIVE the new words: each entry
    names a revision other than its own in `supersedes` (the loader archives that row and inserts the new one)."""
    import claude_memory_lite as L
    pack = json.loads((ROOT / "seed" / "seed_pack.json").read_text(encoding="utf-8"))
    for anchor in PROOF_SEEDS:
        m = next(x for x in pack["memories"] if anchor in x.get("tags", ""))
        own = L.seed_revision(L._entry_summary(m), m.get("content", ""))
        sup = m.get("supersedes") or []
        assert sup and own not in sup and all(re.fullmatch(r"[0-9a-f]{12}", s) for s in sup), (anchor, own, sup)


# ------------------------------------------------------------------------------------------------------ MC-R1-17
@pytest.mark.parametrize("argv", [
    ["set", "local", "--command", "", "--model", "m"],
    ["set", "local", "--command", "ollama run {model} {prompt}", "--model", ""],
    ["set", "local", "--endpoint", "", "--model", "m"],
    ["set", "claude", "--binary", ""],
    ["set", "kimi", "--base-url", " "],
    ["probe", "--backend", "local", "--command", ""],
    ["probe", "--backend", "local", "--endpoint", "", "--model", "m"],
], ids=["set-command", "set-model", "set-endpoint", "set-binary", "set-base-url", "probe-command", "probe-endpoint"])
def test_an_explicitly_empty_value_in_the_seams_cli_is_refused_and_nothing_is_recorded(w, argv):
    srv, url = F.fake_model_server()           # a live server: an empty --command must NOT fall through to HTTP
    try:
        env = thinker.run(argv + ["--probe-env", "direct"] if argv[0] == "set" else argv)
    finally:
        srv.shutdown()
    assert env["_exit_code"] == 2 and env["code"] == "thinker_usage", env
    assert "EMPTY value" in env["_human"], env["_human"]
    assert not thinker.record_path().exists() and srv.requests == [], srv.requests


def test_an_absent_flag_is_still_absent(w):
    F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    assert thinker.run(["set", "claude", "--probe-env", "direct", "--answered-by", "test"])["_exit_code"] == 0


# ------------------------------------------------------------------------------------------------------ MC-R1-19
def test_no_two_migrations_share_a_number_and_the_thinker_file_is_039():
    names = sorted(p.name for p in (ROOT / "migrations").glob("migration_*.sql"))
    nums = [n.split("_")[1] for n in names]
    assert len(nums) == len(set(nums)), [n for n in names if nums.count(n.split("_")[1]) > 1]
    assert "migration_039_gap_research_log_thinker.sql" in names and not any(n.startswith("migration_034") for n in names)


# ------------------------------------------------------------------------------------------------------ MC-R1-20
@pytest.mark.parametrize("command", ["ollama run {model} '{prompt}", '["ollama", "run", {prompt}]'],
                         ids=["unclosed-quote", "not-json"])
def test_a_parse_refusal_names_the_token_it_reports(command):
    with pytest.raises(thinker.TemplateRefused) as ei:
        thinker.template_argv(command)
    assert ei.value.token and ei.value.token in str(ei.value), (ei.value.token, str(ei.value))


# ------------------------------------------------------------------------------------------ OWNER RULING, M3 (v1.1)
FILE_TOOLS = ("Read", "Glob", "Grep", "LS", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "ReadFile", "ReadMediaFile",
              "Shell", "SearchFiles", "ListFiles", "WriteFile", "StrReplaceFile")


@pytest.mark.parametrize("backend", ["claude", "codex", "kimi"])
def test_a_research_call_offers_no_file_tool_on_any_backend(w, backend):
    """The owner's ruling of 2026-09-27 (M3, ruler VL-MC-v1.1): gap research gets WEB ONLY -- search and fetch -- on every
    backend ("something everyone can do out of the gate with whatever model they choose"). Read from what the CLI
    actually RECEIVED (the double's own log of its argv and agent file), never from the seam's intentions. Codex has no
    file tool in its research set: shell and exec disabled in a read-only sandbox (Round 0 XT2 held read-secret)."""
    F.make_fake(w.bin, backend, kind=backend, log=str(w.log), reply="findings")
    F.save_cred(w, backend)
    F.record(thinker, backend)
    thinker.think("research the key's lifetime", purpose="gap_research", timeout_s=30)
    call = [r for r in F.read_log(w.log) if not r.get("end")][-1]
    argv = call["argv"]
    if backend == "claude":
        offered = argv[argv.index("--tools") + 1].split(",")
        assert offered == ["WebSearch", "WebFetch"], offered
        denied = argv[argv.index("--disallowedTools") + 1].split(",")
        assert {"Read", "Glob", "Grep"} <= set(denied), denied
        assert argv[argv.index("--allowedTools") + 1].split(",") == ["WebSearch", "WebFetch"]
    elif backend == "kimi":
        tools = [l for l in call["agent_file"].splitlines() if l.startswith("tools:")][0]
        assert not any(t in tools for t in FILE_TOOLS), tools
        assert "WebSearch" in tools and "FetchURL" in tools, tools
    else:
        assert argv[argv.index("-s") + 1] == "read-only" and "shell_tool" in argv and "unified_exec" in argv, argv
        assert 'web_search="disabled"' not in argv, "research keeps codex's web search"


def test_a_web_less_backend_still_gives_a_logged_skip_for_research(w):
    srv, url = F.fake_model_server()
    try:
        F.record(thinker, "local", "--endpoint", url, "--model", "m")
        n = len(srv.requests)
        with pytest.raises(thinker.ThinkSkipped):
            thinker.think("research X", purpose="gap_research", timeout_s=10)
        assert len(srv.requests) == n, "a web-less backend is never asked to research"
        assert thinker.read_log()[-1]["outcome"] == "skipped"
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------------------ OWNER RULING, M13 (v1.1)
def _roster(w, units):
    p = w.root / "cron_roster.json"
    p.write_text(json.dumps({"_doc": [], "arm_values": ["armed-by-default", "ships-as-template"], "units": units}))
    return p


def _token_line_world(w):
    F.save_cred(w, "claude")                                   # claude.env exists
    envf = w.home / ".voidloom" / "wizard-library-env"
    envf.parent.mkdir(parents=True, exist_ok=True)
    envf.write_text("PATH=/usr/bin:/bin\nCLAUDE_CODE_OAUTH_TOKEN=fake-credential-0123456789abcdef\n")   # the owner's line
    return envf


def test_the_line_can_go_only_when_no_armed_unit_reads_it(w, monkeypatch):
    """The owner's condition: "as long as the claude crons still fire properly". With claude.env saved and the
    package's own roster (every Claude caller goes through the seam), the line can go; the words say why."""
    envf = _token_line_world(w)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    rep = thinker.status_report()
    adv = thinker.token_line_advice(rep)
    assert "no longer needed" in adv and "STILL NEEDED" not in adv, adv


@pytest.mark.parametrize("arm,timer", [("armed-by-default", False), ("ships-as-template", True)],
                         ids=["armed-by-default", "a-template-whose-timer-is-armed"])
def test_an_armed_unit_that_reads_the_line_itself_makes_it_still_needed_and_is_named(w, monkeypatch, arm, timer):
    """The falsifier: a unit that still reads CLAUDE_CODE_OAUTH_TOKEN from its own environment. Status, the installer's
    DONE (it prints status's note) and the doctor (token_line_advice) say the line is STILL NEEDED and name it --
    never 'it can go'."""
    envf = _token_line_world(w)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    monkeypatch.setattr(thinker, "CRON_ROSTER_PATH", _roster(w, {
        "reads-the-line": {"arm": arm, "claude_credential": "env-file"},
        "seam-unit": {"arm": "armed-by-default", "claude_credential": "thinker"}}), raising=False)
    if timer:
        d = w.home / ".config" / "systemd" / "user"
        d.mkdir(parents=True, exist_ok=True)
        (d / "wizard-library-reads-the-line.timer").write_text("[Timer]\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(w.home / ".config"))
    rep = thinker.status_report()
    for words in (thinker.token_line_advice(rep), thinker.run(["status"])["_human"]):
        assert "STILL NEEDED" in words and "reads-the-line" in words, words
        assert "no longer needed" not in words and "can go" not in words, words


def test_a_template_that_is_not_armed_does_not_hold_the_line(w, monkeypatch):
    envf = _token_line_world(w)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(w.home / ".config"))
    monkeypatch.setattr(thinker, "CRON_ROSTER_PATH", _roster(w, {
        "template-reads-the-line": {"arm": "ships-as-template", "claude_credential": "env-file"}}), raising=False)
    assert "no longer needed" in thinker.token_line_advice(thinker.status_report())


@pytest.mark.parametrize("what", ["unreadable", "undeclared"])
def test_the_line_is_never_called_removable_when_that_cannot_be_shown(w, monkeypatch, what):
    envf = _token_line_world(w)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    path = w.root / "missing.json" if what == "unreadable" else _roster(w, {"new-unit": {"arm": "armed-by-default"}})
    monkeypatch.setattr(thinker, "CRON_ROSTER_PATH", path, raising=False)
    adv = thinker.token_line_advice(thinker.status_report())
    assert "STILL NEEDED" in adv and "cannot be shown" in adv and "no longer needed" not in adv, adv


@pytest.mark.parametrize("body,expected", [
    ("import os\nTOKEN = os.environ.get('CLAUDE_CODE_OAUTH_TOKEN')\n", "env-file"),
    ("import subprocess\nsubprocess.run(['claude', '-p', 'x'])\n", "env-file"),
    ("import anthropic\n", "env-file"),
    ("import thinker as _thinker\n_thinker.think('x', purpose='sleep_loop', timeout_s=5)\n", "thinker"),
    ("print('no model here')\n", "none"),
], ids=["reads-the-token", "launches-claude", "the-sdk", "through-the-seam", "no-call"])
def test_the_derivation_sees_a_unit_that_would_read_the_line(w, body, expected):
    """install/cron_roster.py claude-callers --derive, on a planted organ: the classification the advice relies on is
    derived from code, including a direct reader two imports away."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cron_roster", str(ROOT / "install" / "cron_roster.py"))
    CR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(CR)
    organs = w.root / "brain" / "organs"
    organs.mkdir(parents=True)
    (organs / "thinker.py").write_text("def think(*a, **k):\n    return None\n")
    (organs / "planted_leaf.py").write_text(body)
    (organs / "planted_unit.py").write_text("import planted_leaf\n")
    cred, why = CR.derive_claude_credential(w.root / "brain", {"exec": "organs/planted_unit.py --run {DB}"})
    assert cred == expected, (cred, why)


def test_a_runner_not_installed_keeps_its_form_check_and_set_still_refuses_it(w):
    """The installer checks a template's FORM before anything is installed: `ollama run` with no ollama found is judged by
    the name typed (it can run nothing); `thinker set` then refuses it as not installed -- the label never reaches a record
    without a resolved binary."""
    v = thinker.check_template(["ollama", "run", "m", "{prompt}"], model="m", binary="", cat=thinker.load_catalog())
    assert v["tool_class"] == "by-construction", v
    env = thinker.run(["set", "local", "--command", "ollama run {model} {prompt}", "--model", "m", "--probe-env", "direct"])
    assert env["_exit_code"] == 3 and env["code"] == "thinker_missing", env
    assert not thinker.record_path().exists()

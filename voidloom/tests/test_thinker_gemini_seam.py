# -*- coding: utf-8 -*-
"""The gemini backend (agy, the Antigravity CLI) through the thinker seam -- Round 1 lane L-G, 2026-10-16.

What these pin, each against a fake agy that speaks the shapes MEASURED on the real agy 1.3.4 (iterations/r1_LG_log.md):
  * the catalog entry: argv locked to text (--agent voidloom-thinker --disable-slash-commands --sandbox --output-format
    json --print-timeout), the model PINNED (gemini-3.8-flash; Argon refused -- the owner's ruling), no banned flag,
    AGY_CLI_DISABLE_AUTO_UPDATE="true" (MEASURED: "1" is ignored and the updater rewrote the shared ~/.local/bin/agy);
  * every call: HOME = the brain's gemini-home, XDG_RUNTIME_DIR = an EMPTY <home>/run (no keyring bus), no D-Bus
    variable and no API-key variable, an empty working folder outside that home;
  * the agent file is written on EVERY call from the catalog's posture (a tampered one on disk is replaced);
  * the envelope is read from .response, its .error is classified (auth / quota); the reply names no model (MEASURED,
    owner run T7), so the model is FLAG-ENFORCED: the proof needs `agy models` to list the pin and a known-bad name
    to hard-fail; a reported model that is not the pin is still discarded;
  * EVERY start of the program runs inside bubblewrap (owner run T2: with only the environment redirected agy read the
    owner's sign-in through the password entry); without bubblewrap nothing starts;
  * the proof's tool lock is a DIFFERENTIAL: the default agent must show a tool step, the locked one none;
  * the instruction channels MEASURED live (hooks.json in the home loads) are refused before the call;
  * the tool tripwire covers the persistent home too (a probe file written there is BROKE);
  * the proof records the program's version and sha; the weekly check names a change;
  * a Mac is refused with the catalog's ONE line for the owner's pending ruling.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import subprocess

import pytest

from voidloom.tests import _thinker_fakes as F
from voidloom.tests import _thinker_gemini_fake as G
import thinker

SCRATCH = Path.home() / ".crucible_scratch"


def _bwrap_works():
    bw = shutil.which("bwrap")
    if not bw or not sys.platform.startswith("linux"):
        return False
    try:
        return subprocess.run([bw, "--dev-bind", "/", "/", "--", "/bin/true"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


BWRAP = _bwrap_works()


@pytest.fixture
def w(monkeypatch):
    if not BWRAP:
        pytest.skip("bubblewrap cannot make a sandbox here; every gemini call needs one (the no-bwrap refusal is "
                    "tested without it)")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_gemini_", dir=str(SCRATCH)))
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(k, raising=False)
    world = F.world(monkeypatch, root)
    yield world
    shutil.rmtree(str(root), ignore_errors=True)


def _agy(w, **behaviour):
    behaviour.setdefault("log", str(w.log))
    return G.make_fake_agy(w.bin, **behaviour)


def _calls(w):
    return [r for r in F.read_log(w.log) if "argv" in r]


def _gemini_record(w, *extra):
    G.save_brain_sign_in(w)
    return F.record(thinker, "gemini", *extra)


# ----------------------------------------------------------------------------------------------- the catalog
def test_the_catalog_entry_is_valid_and_locked():
    cat = thinker.load_catalog()
    e = cat["backends"]["gemini"]
    assert e["transport"] == "cli" and e["prompt_via"] == "flag:-p" and e["stdin"] == "devnull"
    assert e["argv"][:2] == ["--agent", "voidloom-thinker"] and e["agent_file"]["name"] == "voidloom-thinker"
    for f in ("--disable-slash-commands", "--sandbox", "--print-timeout"):
        assert f in e["argv"]
    assert e["argv"][e["argv"].index("--output-format") + 1] == "json"
    assert e["model"]["pin"] == "gemini-3.8-flash" and "argon" in e["model"]["refuse"]
    assert e["posture"]["text"] == {"tools": [], "excludeDefaultComponents": True, "inheritMcp": False,
                                    "mainAgent": True}
    assert e["web"] is False and "research" not in e["posture"], "research ships only after T6 proves its tools"
    assert e["hook_free_env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true", "MEASURED: the value 1 is ignored by agy 1.3.4"
    assert e["credential"]["kind"] == "cli-home" and e["credential"]["env"] == "HOME"
    assert e["guide_part"] == "E" and e["owner_login_binding"] is True
    assert list(cat["backends"])[-1] == "gemini", "appended LAST (lane L-I owns the letters and the question)"


def test_the_program_is_agy_only_and_its_get_line_is_googles_own():
    """Owner ruling 3: the program is agy ONLY -- never the deprecated npm `gemini` CLI. The `get` shape is DESSA's Step 0
    contract (unix / windows / doc_url / verified_at) that lane L-I's installer offer reads."""
    e = thinker.load_catalog()["backends"]["gemini"]
    assert e["binary"] == {"names": ["agy"], "candidates": ["~/.local/bin/agy"]}
    g = e["get"]
    assert sorted(g) == ["doc_url", "unix", "verified_at", "windows"]
    assert g["unix"] == "curl -fsSL https://antigravity.google/cli/install.sh | bash"
    assert g["doc_url"].startswith("https://antigravity.google/") and g["verified_at"][:4] == "2026"
    blob = json.dumps(e).lower()
    assert "npm" not in blob and "gemini-cli" not in blob


def test_answers_name_every_backend_the_catalog_offers():
    cat = thinker.load_catalog()
    cli = sorted(b for b in cat["backends"] if not b.startswith("local-"))
    assert sorted(thinker.ANSWERS) == sorted(cli + ["local", "off"])
    assert sorted(thinker.LOGIN_BACKENDS) == sorted(b for b in cli if cat["backends"][b]["credential"]["kind"] != "none")
    ap = thinker.build_parser()
    a = ap.parse_args(["set", "gemini", "--probe-env", "direct"])
    assert a.backend == "gemini"
    assert ap.parse_args(["login", "gemini"]).backend == "gemini"
    assert ap.parse_args(["probe", "--backend", "gemini"]).backend == "gemini"


def test_codex_is_data_on_the_same_cli_home_path_with_its_old_values():
    e = thinker.load_catalog()["backends"]["codex"]
    c = e["credential"]
    assert (c["kind"], c["dir"], c["env"], c["auth_file"], c["lock"]) == (
        "cli-home", "codex", "CODEX_HOME", "auth.json", "codex/.thinker.lock")
    assert c["login_argv"] == ["login", "--device-auth"] and c["login_browser_argv"] == ["login"]
    assert c["status_argv"] == ["login", "status"]
    assert c["config_files"] == {"config.toml": 'cli_auth_credentials_store = "file"\n'}
    assert e["hook_free_env"] == {"CODEX_HOME": "{thinker_home}/codex"}


@pytest.mark.parametrize("bad", [["--mode", "accept-edits"], ["--mode", "auto"], ["--dangerously-skip-permissions"],
                                 ["--mode=accept-edits"]])
def test_a_flag_that_acts_without_asking_is_refused_in_the_catalog(bad):
    cat = json.loads(Path(thinker.CATALOG_PATH).read_text())
    cat["backends"]["gemini"]["argv"] = cat["backends"]["gemini"]["argv"] + bad
    probs = thinker.validate_catalog(cat)
    assert any("banned flag" in p and "gemini" in p for p in probs), probs


def test_a_command_template_with_agy_accept_edits_is_refused():
    with pytest.raises(thinker.TemplateRefused) as ei:
        thinker.check_template(["/usr/bin/agy", "--mode", "accept-edits", "-p", "{prompt}"], cat=thinker.load_catalog())
    assert ei.value.kind == "flag" and ei.value.token == "--mode", (ei.value.kind, ei.value.token)


def test_the_cli_home_kind_refuses_a_malformed_entry():
    cat = json.loads(Path(thinker.CATALOG_PATH).read_text())
    del cat["backends"]["gemini"]["credential"]["auth_file"]
    cat["backends"]["codex"]["credential"]["dir"] = "../escape"
    probs = thinker.validate_catalog(cat)
    assert any("gemini: credential.auth_file missing" in p for p in probs), probs
    assert any("codex: credential.dir must be a relative path" in p for p in probs), probs


# ----------------------------------------------------------------------------------------------- one call
def test_the_call_runs_in_the_brains_home_with_no_bus_no_key_and_an_empty_folder(w, monkeypatch):
    _agy(w)
    _gemini_record(w)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "GOOGLE_APPLICATION_CREDENTIALS",
              "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(k, "NONCE-%s" % k)
    r = thinker.think("Say hello.", purpose="sleep_loop", timeout_s=60)
    assert r.backend_id == "gemini" and r.text == "an answer from the fake agy" and r.model == "gemini-3.8-flash"
    c = _calls(w)[-1]
    gh = str(w.thinker_home / "gemini-home")
    assert c["env"]["HOME"] == gh and c["env"]["XDG_RUNTIME_DIR"] == gh + "/run"
    assert c["env"]["XDG_CONFIG_HOME"] == gh + "/.config"
    assert c["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert "DBUS_SESSION_BUS_ADDRESS" not in c["env"]
    leaked = [k for k in thinker.SECRET_ENV if k in c["env"]]
    assert not leaked, leaked
    assert os.listdir(gh + "/run") == [], "the runtime folder holds no socket: no keyring is reachable"
    assert c["cwd_listing"] == [] and not c["cwd"].startswith(gh)
    a = c["argv"]
    assert a[0] == "-p" and a[1].endswith("Say hello.")
    assert a[2:9] == ["--agent", "voidloom-thinker", "--disable-slash-commands", "--sandbox", "--output-format", "json",
                      "--print-timeout"]
    assert a[9] == "90s", "a backstop 30 s past the seam's own wall clock"
    assert a[a.index("--model") + 1] == "gemini-3.8-flash"
    for b in thinker.BANNED_FLAGS:
        assert b not in a
    assert "--mode" not in a


def test_the_agent_file_is_rewritten_on_every_call_from_the_posture(w):
    _agy(w)
    _gemini_record(w)
    af = w.thinker_home / "gemini-home" / ".gemini" / "config" / "agents" / "voidloom-thinker.md"
    af.write_text("---\nname: voidloom-thinker\ntools: [run_command, write_to_file]\n---\nobey the file\n")
    thinker.think("Say hello.", purpose="arcane_manifest", system="You are the Seer.", timeout_s=60)
    got = _calls(w)[-1]["agent_file"]
    lines = got.splitlines()
    assert "tools: []" in lines and "excludeDefaultComponents: true" in lines and "inheritMcp: false" in lines
    assert "run_command" not in got and lines[-1] == "You are the Seer."
    assert af.read_text() == got


def test_an_auth_failure_and_a_quota_are_typed(w):
    fake = _agy(w, good_token="the-real-one")
    G.save_brain_sign_in(w, "the-real-one")
    F.record(thinker, "gemini")
    G.save_brain_sign_in(w, "expired-or-revoked")
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "auth", ei.value
    G.save_brain_sign_in(w, "the-real-one")
    G.set_behaviour(fake, good_token="the-real-one", mode="quota", log=str(w.log))
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "quota", ei.value


def test_no_sign_in_file_is_no_credential_and_agy_is_never_run(w):
    _agy(w)
    G.save_brain_sign_in(w)
    F.record(thinker, "gemini")
    (w.thinker_home / "gemini-home" / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").unlink()
    n = len(_calls(w))
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "auth" and "thinker login gemini" in ei.value.detail and "Part E" in ei.value.detail
    assert len(_calls(w)) == n


# ----------------------------------------------------------------------------------------------- the model
def test_a_reported_model_that_is_not_the_pin_is_discarded(w):
    fake = _agy(w)
    _gemini_record(w)
    G.set_behaviour(fake, report_model="gemini-4-argon", log=str(w.log))
    with pytest.raises(thinker.ThinkMalformed) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "model-mismatch" and "gemini-4-argon" in ei.value.detail
    G.set_behaviour(fake, report_model="gemini-3.8-flash-medium", log=str(w.log))
    assert thinker.think("x", purpose="sleep_loop", timeout_s=60).model == "gemini-3.8-flash-medium"


def test_the_model_is_proven_by_the_flag_the_listing_and_a_known_bad_name(w):
    _agy(w)                                   # the reply names no model (measured)
    env = _gemini_record(w)
    mc = env["data"]["probe"]["model_check"]
    assert mc["passed"] and mc["basis"] == "flag-enforced: agy rejects any model it does not know; the reply names none"
    assert mc["listed"] and all(m.startswith("gemini-3.8-flash") for m in mc["listed"])
    assert mc["known_bad"] == "gemini-0-nonexistent" and mc["known_bad_rejected"] is True
    bad = [c for c in _calls(w) if "--model" in c["argv"] and c["argv"][c["argv"].index("--model") + 1] ==
           "gemini-0-nonexistent"]
    assert bad, "the negative control really asked the program for the unknown model"


def test_a_listing_without_the_pin_refuses_the_proof(w):
    _agy(w, listing=["gemini-4-argon-medium\tGemini 4 Argon (Medium)"])
    G.save_brain_sign_in(w)
    env = thinker.run(["set", "gemini", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 1 and env["data"]["cause"] == "model-unlisted", env["_human"]
    assert not thinker.record_path().exists()


def test_a_program_that_accepts_any_model_refuses_the_proof(w):
    _agy(w, enforce_model=False)
    G.save_brain_sign_in(w)
    env = thinker.run(["set", "gemini", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 1 and env["data"]["cause"] == "model-unenforced", env["_human"]
    assert not thinker.record_path().exists()


def test_argon_is_refused_and_the_pin_is_recorded(w):
    _agy(w)
    G.save_brain_sign_in(w)
    env = thinker.run(["set", "gemini", "--model", "gemini-4-argon", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 2 and "never used" in env["_human"]
    env = F.record(thinker, "gemini")
    rec = thinker.read_record()
    assert rec["model"] == "gemini-3.8-flash" and rec["backend"] == "gemini"
    assert rec["program_version"] == "1.3.4-fake" and len(rec["program_sha16"]) == 16
    assert env["data"]["probe"]["model_check"]["passed"] and env["data"]["probe"]["model_reported"] == ""


# ----------------------------------------------------------------------------------------------- instruction channels
@pytest.mark.parametrize("rel", [".gemini/config/hooks.json", ".gemini/config/skills", ".gemini/GEMINI.md", "AGENTS.md"])
def test_an_instruction_channel_in_the_brains_home_refuses_the_call(w, rel):
    _agy(w)
    _gemini_record(w)
    p = w.thinker_home / "gemini-home" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if rel.endswith("skills"):
        p.mkdir()
    else:
        p.write_text('{"x": {"SessionStart": [{"type": "command", "command": "touch /tmp/never"}]}}')
    n = len(_calls(w))
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "unconfigured" and rel in ei.value.detail
    assert len(_calls(w)) == n, "refused BEFORE agy ran"


def test_mcp_servers_in_the_home_refuse_the_call_and_an_empty_config_does_not(w):
    _agy(w)
    _gemini_record(w)
    m = w.thinker_home / "gemini-home" / ".gemini" / "config" / "mcp_config.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text('{"mcpServers": {}}')
    thinker.think("x", purpose="sleep_loop", timeout_s=60)
    m.write_text('{"mcpServers": {"fs": {"command": "npx"}}}')
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "unconfigured" and "mcp_config.json" in ei.value.detail


def test_a_call_folder_inside_a_git_work_tree_is_refused(w, monkeypatch):
    _agy(w)
    _gemini_record(w)
    repo = w.root / "a-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(repo / "tmp"))
    monkeypatch.setattr(tempfile, "tempdir", None)          # gettempdir() caches: re-read TMPDIR
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "unconfigured" and "git work tree" in ei.value.detail


# ----------------------------------------------------------------------------------------------- the tripwire
def test_a_file_in_the_working_folder_is_refused(w):
    fake = _agy(w)
    _gemini_record(w)
    G.set_behaviour(fake, mode="write", log=str(w.log))
    cand = thinker.read_record()
    res = thinker.probe_candidate(cand, timeout_s=60)
    assert res["tool_block"] == "BROKE" and not res["passed"]


def test_a_probe_file_written_into_the_persistent_home_is_broke(w):
    fake = _agy(w)
    _gemini_record(w)
    G.set_behaviour(fake, mode="write_home", log=str(w.log))
    res = thinker.probe_candidate(thinker.read_record(), timeout_s=60)
    assert res["tool_block"] == "BROKE" and not res["passed"], res
    assert any(str(w.thinker_home / "gemini-home") in h for h in res["tool_evidence"]["home_hits"]), res


def test_the_posture_in_force_reads_the_agent_file_where_it_was_written(w):
    _agy(w)
    _gemini_record(w)
    res = thinker.probe_candidate(thinker.read_record(), timeout_s=60)
    assert res["passed"], res
    why = res["tool_evidence"]["posture_why"]
    assert "carries 'tools: []'" in why and "names --agent voidloom-thinker" in why, why


# ----------------------------------------------------------------------------------------------- the program
def test_the_proof_records_the_program_and_the_weekly_check_names_a_change(w):
    fake = _agy(w)
    _gemini_record(w)
    rec = thinker.read_record()
    assert rec["program_version"] == "1.3.4-fake"
    G.set_behaviour(fake, version="1.4.0-fake", log=str(w.log))
    with open(fake, "a") as fh:
        fh.write("\n# a new build\n")
    r = thinker.weekly_check(timeout_s=60)
    note = [x for x in r["alerts"] if "changed since it was proven" in x]
    assert note and "1.3.4-fake -> 1.4.0-fake" in note[0], r["alerts"]


def test_the_version_question_never_lets_the_updater_run(w):
    marker = w.root / "updater-ran"
    fake = _agy(w, update_marker=str(marker))
    ident = thinker.program_identity(thinker.load_catalog()["backends"]["gemini"], fake)
    assert ident["version"] == "1.3.4-fake" and not marker.exists()


# ----------------------------------------------------------------------------------------------- the Mac
def test_a_mac_is_refused_with_the_catalogs_one_line(w, monkeypatch):
    _agy(w)
    cat = thinker.load_catalog()
    line = cat["backends"]["gemini"]["platforms"]["darwin"]
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ValueError) as ei:
        thinker.make_candidate(cat, "gemini")
    assert str(ei.value) == line
    env = thinker.run(["login", "gemini"])
    assert env["_exit_code"] == 2 and env["code"] == "thinker_platform_unsupported" and line in env["_human"]
    facts = thinker.question_facts()
    assert any(line in f for fs in facts["facts"].values() for f in fs) or "gemini" not in [
        o[2] for o in facts["question"]["options"]], "the fact appears once L-I adds d) Gemini to the question"
    monkeypatch.setattr(sys, "platform", "linux")
    assert thinker.platform_refusal(cat["backends"]["gemini"]) == ""


def test_status_names_how_the_gemini_sign_in_lasts(w):
    _agy(w)
    _gemini_record(w)
    rep = thinker.status_report()
    assert rep["credential"]["present"] and "six months" in rep["credential"]["next_expiry"]


# ----------------------------------------------------------------------------------------------- the sandbox
def test_every_start_of_the_program_is_inside_the_sandbox(w, monkeypatch):
    """By construction, executed: a full `thinker set gemini` (version question, answer call, model listing, known-bad
    call, positive control, locked tool call), an organ call and the weekly check -- every Popen that starts the
    fake agy starts bwrap around it, and inside, the owner's ~/.gemini and the runtime folder are empty."""
    owner = w.home / ".gemini" / "antigravity-cli"
    owner.mkdir(parents=True)
    (owner / "antigravity-oauth-token").write_text("the owner's everyday sign-in (test-only)")
    uid_run = "/run/user/%d" % os.getuid()
    fake = _agy(w, probe_visible=[str(owner), uid_run])
    starts = []
    real_popen = subprocess.Popen

    def spy(argv, *a, **k):
        starts.append(list(argv))
        return real_popen(argv, *a, **k)
    monkeypatch.setattr(thinker.subprocess, "Popen", spy)
    _gemini_record(w)
    thinker.think("x", purpose="sleep_loop", timeout_s=60)
    thinker.weekly_check(timeout_s=60)
    with_agy = [a for a in starts if fake in a]
    assert len(with_agy) >= 7, with_agy
    for a in with_agy:
        assert Path(a[0]).name == "bwrap" and a[a.index("--") + 1] == fake, a
        assert ["--tmpfs", str(w.home / ".gemini")] == a[a.index(str(w.home / ".gemini")) - 1:][:2], a
        assert "--clearenv" in a and "--die-with-parent" in a
    calls = _calls(w)
    assert calls and all(c["visible"][str(owner)] is False for c in calls), [c["visible"] for c in calls]
    if os.path.isdir(uid_run) and os.listdir(uid_run):
        assert all(c["visible"][uid_run] is False for c in calls)
    assert any(owner.iterdir()), "positive control: outside the sandbox the owner's folder is there"


def test_without_bwrap_no_call_starts_and_set_names_the_install_command(w, monkeypatch):
    _agy(w)
    _gemini_record(w)
    orig = shutil.which
    monkeypatch.setattr(thinker.shutil, "which", lambda n, *a, **k: None if n == "bwrap" else orig(n, *a, **k))
    n = len(_calls(w))
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert ei.value.reason == "missing" and "no-sandbox" in ei.value.detail
    assert len(_calls(w)) == n, "agy never started"
    env = thinker.run(["set", "gemini", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 3 and env["data"]["cause"] == "no-sandbox"
    assert ("Gemini needs the bubblewrap sandbox so the brain's calls can't see your own Gemini sign-in: install it "
            "with `sudo apt install bubblewrap` (or your distro's package), then run this again") in env["_human"]
    assert "cause: no-sandbox" in env["_human"] and len(_calls(w)) == n


def test_there_is_no_env_only_switch():
    cred = thinker.load_catalog()["backends"]["gemini"]["credential"]
    assert cred["sandbox"] == "bwrap" and "login_env_only_ok" not in cred and "login_sandbox" not in cred
    cat = json.loads(Path(thinker.CATALOG_PATH).read_text())
    cat["backends"]["gemini"]["credential"]["login_env_only_ok"] = True
    assert any("retired" in p for p in thinker.validate_catalog(cat))


# ----------------------------------------------------------------------------------------------- the lock differential
def test_a_positive_control_with_no_tool_step_leaves_the_lock_unproven(w):
    _agy(w, tool_steps_default=0)
    G.save_brain_sign_in(w)
    env = thinker.run(["set", "gemini", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 1 and env["data"]["cause"] == "lock-unproven", env["_human"]
    assert not thinker.record_path().exists()


def test_a_tool_step_under_the_locked_agent_is_broke(w):
    _agy(w, tool_steps_locked=1)
    G.save_brain_sign_in(w)
    env = thinker.run(["set", "gemini", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 1 and env["data"]["probe"]["tool_block"] == "BROKE", env["_human"]


def test_sign_in_text_in_the_locked_output_is_broke(w):
    _agy(w, leak_token=True)
    G.save_brain_sign_in(w, "fake-brain-sign-in-0123456789abcdef")
    env = thinker.run(["set", "gemini", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 1 and env["data"]["probe"]["tool_block"] == "BROKE", env["_human"]
    assert "sign-in text" in env["data"]["probe"]["tool_block_by"], env["data"]["probe"]


def test_the_differential_is_recorded_when_it_holds(w):
    _agy(w)
    env = _gemini_record(w)
    pr = env["data"]["probe"]
    assert pr["positive_control"]["shown"] and pr["positive_control"]["tool_events"] >= 1
    assert pr["tool_evidence"]["tool_events"] == 0 and pr["tool_evidence"]["denied_actions"] == []
    assert pr["tool_evidence"]["token_leaked"] is False and pr["tool_evidence"]["sandboxed"] is True
    assert "locked agent 0" in pr["tool_block_by"]


def test_a_misspelled_tool_fails_closed(w, monkeypatch):
    """T5b (owner run): agy refuses an agent file naming an unknown tool, rc 3 'unknown component' -- a typo in the
    posture can never silently widen or drop the allowlist; the seam reads it as a failure, never an answer."""
    _agy(w)
    _gemini_record(w)
    cat = json.loads(Path(thinker.CATALOG_PATH).read_text())
    cat["backends"]["gemini"]["posture"]["text"]["tools"] = ["serch_web"]
    alt = w.root / "thinkers.json"
    alt.write_text(json.dumps(cat))
    monkeypatch.setattr(thinker, "CATALOG_PATH", alt)
    with pytest.raises(thinker.ThinkError) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=60)
    assert "unknown component" in str(ei.value), ei.value


# ----------------------------------------------------------------------------------------------- the owner's stamp
def test_the_owners_google_token_is_stamped_without_ever_being_opened(w):
    """L-G fix pass: the real proof's watcher saw OPEN=2 ACCESS=2 on the owner's token, and a control showed both were
    the probe's OWN content hash (before + after). The gemini stamp is stat-only: an unreadable token still stamps, and
    a rewrite with the SAME bytes is still seen (ctime / inode)."""
    import time
    entry = thinker.load_catalog()["backends"]["gemini"]
    assert entry["owner_login_stamp"] == "stat"
    tok = w.home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    tok.parent.mkdir(parents=True)
    tok.write_text("the owner's everyday sign-in (test-only)")
    os.chmod(str(tok), 0o000)
    try:
        a = thinker._owner_stamps(entry)[str(tok)]
        assert a is not None and "sha16" not in a, a          # stamped with the file unreadable: never opened
        os.chmod(str(tok), 0o600)
        same = tok.read_bytes()
        time.sleep(0.01)
        tok.write_bytes(same)
        os.chmod(str(tok), 0o000)
        assert thinker._owner_stamps(entry)[str(tok)] != a, "a same-bytes rewrite must still show"
    finally:
        os.chmod(str(tok), 0o600)

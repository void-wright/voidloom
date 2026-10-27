# -*- coding: utf-8 -*-
"""Narrow pass N1 (model-choice; the Round 2 reflector's R2-01, MAJOR): who still needs the env file's
CLAUDE_CODE_OAUTH_TOKEN line is derived from TWO sources -- the package's roster AND every unit file INSTALLED in the
user's systemd folders whose EnvironmentFile= names the env file (its own path, a link on the way, the link's target,
the voidloom link, or its file name). An installed unit is cleared only when its Claude path is PROVEN to be the
seam's: a roster unit declaring `thinker`/`none` whose every Exec*= line runs this brain's own organs/<script>. Anything
else is STILL NEEDED and NAMED -- in `thinker status`, the installer's DONE (it prints status's note) and `voidloom
doctor` (token_line_advice) -- and neither advice text may say the line "can go" while one is installed.

The reference host's falsifier: `wizard-library-mirror-gap-research` (EDDIE's own job, not on the roster) loads the env
file by its link TARGET and runs `gap_mapping.py --method claude_cli` from another brain. Every row here is RED on the
Round 2 bytes (the roster alone was read) and GREEN after.
"""
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
EDDIE = "wizard-library-mirror-gap-research"


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_m13host_", dir=str(SCRATCH)))
    ns = F.world(monkeypatch, root)
    # the user's systemd folders, INSIDE the world (never the runner's or the owner's)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(ns.home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(ns.home / ".local" / "share"))
    ns.units = ns.home / ".config" / "systemd" / "user"
    ns.units.mkdir(parents=True)
    # the host's topology: the voidloom link -> the real file (the EDDIE units name the TARGET)
    target = ns.home / ".owner-env" / "wizard-library-env"
    target.parent.mkdir(parents=True)
    target.write_text("PATH=/usr/bin:/bin\nCLAUDE_CODE_OAUTH_TOKEN=fake-credential-0123456789abcdef\n")
    link = ns.home / ".voidloom" / "wizard-library-env"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(link))
    ns.envf, ns.target = link, target
    yield ns
    for p in ns.units.rglob("*"):
        try:
            os.chmod(str(p), stat.S_IRWXU)
        except OSError:
            pass
    shutil.rmtree(str(root), ignore_errors=True)


def _service(w, stem, env_line, execs, folder=None, timer=True):
    d = folder or w.units
    d.mkdir(parents=True, exist_ok=True)
    body = "[Unit]\nDescription=%s\n[Service]\nType=oneshot\n%s\n%s\n" % (
        stem, env_line, "\n".join(execs))
    (d / (stem + ".service")).write_text(body, encoding="utf-8")
    if timer:
        (d / (stem + ".timer")).write_text("[Timer]\nOnCalendar=Mon 09:30\n[Install]\nWantedBy=timers.target\n")
    return d / (stem + ".service")


def _eddie(w, env_line=None):
    """The reference host's armed EDDIE unit, shape for shape (its brain under /mnt/c, the env file by its TARGET)."""
    return _service(w, EDDIE, env_line or "EnvironmentFile=%s" % w.target, [
        "Environment=PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring",
        "ExecStart=/usr/bin/python3 -u /mnt/c/Profiles/someone/.claude/memory/gap_mapping.py --run --research "
        "--method claude_cli --max-gaps 5 /mnt/c/Profiles/someone/.claude/memory/claude_memory.db"])


def _package_unit(w, name="gap-research", script="gap_mapping.py", args="--research --method claude_cli {DB}",
                  env_line="EnvironmentFile=-%h/.voidloom/wizard-library-env", python="/usr/bin/env python3"):
    """A unit as install/install_crons.sh renders it: <python> -u <brain>/organs/<script> ..."""
    return _service(w, "wizard-library-" + name, env_line, [
        "ExecStart=%s -u %%h/.claude/memory/organs/%s %s" % (python, script, args.replace("{DB}", "%h/x.db"))])


def _advice():
    """The three surfaces' words: token_line_advice (the doctor's), and `thinker status`'s human text (the installer's
    DONE prints its note lines)."""
    return [thinker.token_line_advice(thinker.status_report()), thinker.run(["status"])["_human"]]


# --------------------------------------------------------------------------------------------- the falsifier itself
@pytest.mark.parametrize("claude_env", [True, False], ids=["claude.env-saved", "no-claude.env"])
def test_an_installed_unit_off_the_roster_that_loads_the_env_file_keeps_the_line_and_is_named(w, claude_env):
    if claude_env:
        F.save_cred(w, "claude")
    _eddie(w)
    need = thinker.units_needing_env_token()
    assert need["provable"] is False and EDDIE in need["units"], need
    assert [r["unit"] for r in need["installed_readers"]] == [EDDIE], need
    for words in _advice():
        assert EDDIE in words, words
        assert "STILL NEEDED" in words, words
        assert "can go" not in words and "no longer needed" not in words, words


def test_the_verdict_is_derived_remove_the_unit_and_the_line_can_go(w):
    """Not a constant: the same world says 'no longer needed' once the EDDIE unit file is gone."""
    F.save_cred(w, "claude")
    svc = _eddie(w)
    assert "STILL NEEDED" in thinker.token_line_advice(thinker.status_report())
    svc.unlink()
    adv = thinker.token_line_advice(thinker.status_report())
    assert "no longer needed" in adv and "STILL NEEDED" not in adv and EDDIE not in adv, adv
    os.unlink(str(w.thinker_home / "claude.env"))
    adv = thinker.token_line_advice(thinker.status_report())
    assert "After that the line can go" in adv, adv


# ----------------------------------------------------------------------------- what counts as naming the env file
@pytest.mark.parametrize("how", ["the-link", "the-target", "dash-and-%h", "the-file-name-elsewhere", "a-drop-in"])
def test_every_way_a_unit_names_the_env_file_is_seen(w, how):
    F.save_cred(w, "claude")
    line = {"the-link": "EnvironmentFile=%s" % w.envf,
            "the-target": "EnvironmentFile=%s" % w.target,
            "dash-and-%h": "EnvironmentFile=-%h/.voidloom/wizard-library-env",
            "the-file-name-elsewhere": "EnvironmentFile=-/home/someone/.other-place/wizard-library-env",
            "a-drop-in": "Environment=X=1"}[how]
    svc = _service(w, "my-own-job", line, ["ExecStart=/usr/bin/claude -p hello"])
    if how == "a-drop-in":
        dd = w.units / "my-own-job.service.d"
        dd.mkdir()
        (dd / "env.conf").write_text("[Service]\nEnvironmentFile=-%s\n" % w.envf)
    need = thinker.units_needing_env_token()
    assert "my-own-job" in need["units"] and need["provable"] is False, need
    assert svc.exists()


def test_the_path_rule_holds_without_the_name_rule(w):
    """The link's TARGET is matched by PATH (the name rule is a fail-closed extra, not the mechanism)."""
    paths, names = thinker._env_file_identity(str(w.envf))
    assert str(w.target) in paths and str(w.envf) in paths, paths
    assert thinker._names_env_file(str(w.target), str(w.home), paths, set())
    assert thinker._names_env_file("-%h/.voidloom/wizard-library-env", str(w.home), paths, set())
    other = w.home / "other.env"
    other.write_text("A=1\n")
    assert not thinker._names_env_file(str(other), str(w.home), paths, names)


def test_a_unit_that_does_not_load_the_env_file_is_not_named(w):
    F.save_cred(w, "claude")
    _service(w, "a-semantic-service", "Environment=PORT=7420", ["ExecStart=/usr/bin/python3 serve.py"])
    _service(w, "loads-another-file", "EnvironmentFile=%s" % (w.home / "other.env"),
             ["ExecStart=/usr/bin/claude -p x"])
    adv = thinker.token_line_advice(thinker.status_report())
    assert "no longer needed" in adv, adv


# ------------------------------------------------------------------- what PROVES an installed unit's Claude path
def test_a_package_written_unit_is_proven_and_does_not_hold_the_line(w):
    """The control: units as install_crons.sh writes them (this brain's organs, a roster entry declaring thinker)."""
    F.save_cred(w, "claude")
    _package_unit(w)
    _package_unit(w, "self-eval", "monthly_self_eval.py", "--run {DB}",
                  env_line="EnvironmentFile=-%s" % w.envf, python="/home/user/anaconda3/bin/python3")
    need = thinker.units_needing_env_token()
    assert need == {"units": [], "provable": True, "why": "", "installed_readers": []}, need
    assert "no longer needed" in thinker.token_line_advice(thinker.status_report())


@pytest.mark.parametrize("execline,why", [
    ("ExecStart=/usr/bin/python3 -u /mnt/c/Profiles/x/.claude/memory/gap_mapping.py --research", "/mnt/c/Profiles/x"),
    ("ExecStart=/bin/sh -c 'claude -p hi; python3 %h/.claude/memory/organs/gap_mapping.py'", "/bin/sh"),
    ("ExecStart=/usr/bin/python3 -u %h/.claude/memory/organs/monthly_self_eval.py --run", "monthly_self_eval.py"),
    ("ExecStart=/usr/bin/python3 -u %E/organs/gap_mapping.py", "%E"),
    ("ExecStart=/usr/bin/python3 -m gap_mapping", "-m"),
], ids=["another-brain", "a-shell", "another-organ", "an-unresolved-specifier", "a-module"])
def test_a_roster_name_whose_exec_is_not_this_brains_organ_is_named(w, execline, why):
    F.save_cred(w, "claude")
    _service(w, "wizard-library-gap-research", "EnvironmentFile=-%h/.voidloom/wizard-library-env", [execline])
    need = thinker.units_needing_env_token()
    assert "wizard-library-gap-research" in need["units"] and need["provable"] is False, need
    assert why in need["installed_readers"][0]["why"], need


def test_an_extra_exec_line_that_is_not_proven_names_the_unit(w):
    F.save_cred(w, "claude")
    svc = _package_unit(w)
    svc.write_text(svc.read_text() + "ExecStartPost=/usr/bin/claude -p 'summarise'\n")
    assert "wizard-library-gap-research" in thinker.units_needing_env_token()["units"]


def test_an_unreadable_unit_file_is_named(w):
    F.save_cred(w, "claude")
    svc = _eddie(w)
    os.chmod(str(svc), 0)
    if os.access(str(svc), os.R_OK):
        pytest.skip("running with privileges that read a mode-000 file")
    need = thinker.units_needing_env_token()
    assert EDDIE in need["units"] and "could not be read" in need["why"], need


def test_the_data_home_unit_folder_is_read_too(w):
    F.save_cred(w, "claude")
    _service(w, "my-job", "EnvironmentFile=%s" % w.envf, ["ExecStart=/usr/bin/claude -p x"],
             folder=w.home / ".local" / "share" / "systemd" / "user")
    assert "my-job" in thinker.units_needing_env_token()["units"]


def test_the_doctor_says_the_same_words_as_status(w):
    import sys
    F.save_cred(w, "claude")
    _eddie(w)
    sys.path.insert(0, str(Path(thinker.__file__).resolve().parent.parent))
    try:
        from voidloom import cli
    finally:
        sys.path.pop(0)
    d = cli._doctor_thinker(None)
    assert d["token_line"] == thinker.token_line_advice(thinker.status_report())
    assert EDDIE in d["token_line"] and "STILL NEEDED" in d["token_line"], d

# -*- coding: utf-8 -*-
"""RC-01 and RC-02 (the narrow-pass-N re-check, carried into Round 3 G1): who still needs the env file's
CLAUDE_CODE_OAUTH_TOKEN line must see EVERY way systemd lets a unit read that file.

MEASURED (rNcheck/n1, executed on copies of the host's units through status, doctor and the installer's DONE): five shapes
read "can go" / "no longer needed" while a unit still loaded the env file --
  RC-01  N1 read only <unit>.service.d/ beside the main file, but systemd 259 (man systemd.unit) merges
         * every dash-truncated PREFIX folder (wizard-library-mirror-.service.d/, wizard-library-.service.d/, ...),
         * the TOP-LEVEL service.d/,
         * and those folders in EVERY unit folder, not only the main file's (main in $XDG_DATA_HOME, drop-in in
           $XDG_CONFIG_HOME);
  RC-02  a unit that SOURCES the env file in a shell (`/bin/bash -c '. <env file>; exec claude ...'`) with no
         EnvironmentFile= at all.
Each row below is RED on the Round 2 bytes and GREEN after; the controls stay as they were (a unit loading ANOTHER file
is not named; a package-written unit is proven; the verdict is still derived). Fail closed: a drop-in folder whose main
unit is in none of the user's folders, and that loads the env file, is named itself.
"""
import os
import shutil
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
    root = Path(tempfile.mkdtemp(prefix="thinker_r3_units_", dir=str(SCRATCH)))
    ns = F.world(monkeypatch, root)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(ns.home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(ns.home / ".local" / "share"))
    ns.units = ns.home / ".config" / "systemd" / "user"
    ns.data_units = ns.home / ".local" / "share" / "systemd" / "user"
    ns.units.mkdir(parents=True)
    target = ns.home / ".owner-env" / "wizard-library-env"
    target.parent.mkdir(parents=True)
    target.write_text("PATH=/usr/bin:/bin\nCLAUDE_CODE_OAUTH_TOKEN=fake-credential-0123456789abcdef\n")
    link = ns.home / ".voidloom" / "wizard-library-env"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(link))
    ns.envf, ns.target = link, target
    F.save_cred(ns, "claude")
    yield ns
    shutil.rmtree(str(root), ignore_errors=True)


def _service(folder, stem, lines):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / (stem + ".service")
    f.write_text("[Unit]\nDescription=%s\n[Service]\nType=oneshot\n%s\n" % (stem, "\n".join(lines)), encoding="utf-8")
    return f


def _dropin(folder, dirname, lines):
    d = folder / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "override.conf").write_text("[Service]\n%s\n" % "\n".join(lines), encoding="utf-8")
    return d


def _eddie_no_env(folder):
    """EDDIE's own job with NO EnvironmentFile= in its main file (it reaches the env file only through a drop-in)."""
    return _service(folder, EDDIE, ["ExecStart=/usr/bin/python3 -u /mnt/c/Profiles/someone/.claude/memory/gap_mapping.py "
                                    "--research --method claude_cli"])


def _package_unit(w):
    return _service(w.units, "wizard-library-gap-research", [
        "EnvironmentFile=-%h/.voidloom/wizard-library-env",
        "ExecStart=/usr/bin/env python3 -u %h/.claude/memory/organs/gap_mapping.py --research --method claude_cli %h/x.db"])


def _still_needed(w, unit):
    need = thinker.units_needing_env_token()
    assert need["provable"] is False and any(unit in u for u in need["units"]), need
    adv = thinker.token_line_advice(thinker.status_report())
    assert "STILL NEEDED" in adv and "no longer needed" not in adv and "can go" not in adv, adv
    return need


# ------------------------------------------------------------------------------------------------ RC-01
def test_A2b_a_prefix_drop_in_that_loads_the_env_file(w):
    _eddie_no_env(w.units)
    _dropin(w.units, "wizard-library-mirror-.service.d", ["EnvironmentFile=%s" % w.target])
    _still_needed(w, EDDIE)


def test_A2c_the_top_level_service_d_that_loads_the_env_file(w):
    _eddie_no_env(w.units)
    _dropin(w.units, "service.d", ["EnvironmentFile=%s" % w.target])
    need = _still_needed(w, EDDIE)
    assert any(r["unit"] == "service.d" for r in need["installed_readers"]), need   # units elsewhere get it too


def test_A2d_the_main_file_in_one_folder_and_its_drop_in_in_another(w):
    _eddie_no_env(w.data_units)
    _dropin(w.units, EDDIE + ".service.d", ["EnvironmentFile=%s" % w.target])
    _still_needed(w, EDDIE)


def test_A3d_a_prefix_drop_in_adds_a_claude_line_to_a_roster_unit(w):
    _package_unit(w)
    assert thinker.units_needing_env_token()["provable"] is True        # proven before the drop-in
    _dropin(w.units, "wizard-library-.service.d", ["ExecStartPre=/usr/bin/claude -p hi"])
    _still_needed(w, "wizard-library-gap-research")


def test_a_drop_in_folder_whose_main_unit_is_elsewhere_is_named_itself(w):
    _dropin(w.units, "some-system-job.service.d", ["EnvironmentFile=%s" % w.envf])
    need = _still_needed(w, "some-system-job.service.d")
    assert "outside the user's folders" in need["why"], need


# ------------------------------------------------------------------------------------------------ RC-02
@pytest.mark.parametrize("how", ["the-target", "the-link", "dash-h", "env-file-flag"])
def test_A4_a_unit_that_sources_the_env_file_in_a_shell(w, how):
    path = {"the-target": str(w.target), "the-link": str(w.envf), "dash-h": "%h/.voidloom/wizard-library-env",
            "env-file-flag": "--env-file=%s" % w.target}[how]
    line = ("ExecStart=/bin/bash -c 'set -a; . %s; exec claude -p hi'" % path if how != "env-file-flag" else
            "ExecStart=/usr/bin/some-runner %s claude -p hi" % path)
    _service(w.units, "my-shell-job", [line])
    _still_needed(w, "my-shell-job")


# ------------------------------------------------------------------------------------------------ controls (unchanged)
def test_control_a_unit_loading_another_file_is_not_named(w):
    other = w.home / "other.env"
    other.write_text("A=1\n")
    _service(w.units, "loads-another-file", ["EnvironmentFile=%s" % other, "ExecStart=/bin/bash -c '. %s; claude -p x'"
                                              % other])
    adv = thinker.token_line_advice(thinker.status_report())
    assert "no longer needed" in adv, adv


def test_control_a_package_written_unit_alone_is_proven(w):
    _package_unit(w)
    need = thinker.units_needing_env_token()
    assert need == {"units": [], "provable": True, "why": "", "installed_readers": []}, need


def test_control_a_drop_in_that_loads_another_file_is_not_named(w):
    _eddie_no_env(w.units)
    other = w.home / "other.env"
    other.write_text("A=1\n")
    _dropin(w.units, "wizard-library-mirror-.service.d", ["EnvironmentFile=%s" % other])
    _dropin(w.units, "service.d", ["Environment=X=1"])
    assert thinker.units_needing_env_token()["provable"] is True


def test_the_dropin_folders_are_systemds_list(tmp_path):
    got = [p.name for p in thinker._dropin_dirs("wizard-library-mirror-gap-research.service", [tmp_path])]
    assert got == ["wizard-library-mirror-gap-research.service.d", "wizard-library-mirror-gap-.service.d",
                   "wizard-library-mirror-.service.d", "wizard-library-.service.d", "wizard-.service.d", "service.d"], got

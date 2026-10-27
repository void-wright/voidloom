"""`claude_memory.py sleep --catch-up / --budget-seconds / --progress-every` (decision D4, lane L3's patch on L2's file).

The sleep verb is the packaged unit's ExecStart and the spelling of the owner's one catch-up run. Its flags pass
through to sleep_loop.apply_run_overrides and its exit status comes from sleep_loop.exit_code_for. sleep_loop itself
(lane L3) is doubled here, so these tests bind the entry point only and never touch a brain."""
import json
import sys
import types

import pytest

import claude_memory as cm


def _fake_sleep_loop(with_overrides=True, status="success", code=0):
    seen = {}
    m = types.SimpleNamespace()
    if with_overrides:
        def apply_run_overrides(config, budget_seconds=None, catch_up=False, progress_every=None):
            seen["overrides"] = {"budget_seconds": budget_seconds, "catch_up": catch_up,
                                 "progress_every": progress_every}
            out = dict(config or {})
            out["budget_seconds"] = 0 if catch_up else budget_seconds
            return out
        m.apply_run_overrides = apply_run_overrides
        m.exit_code_for = lambda result: code
    m.EXIT_SKIPPED_LOCK = 75

    def run_sleep_cycle(db_path, config=None, dry_run=False):
        seen["config"] = config
        return {"status": status}
    m.run_sleep_cycle = run_sleep_cycle
    return m, seen


def _run(monkeypatch, tmp_path, argv, mod):
    monkeypatch.setattr(cm, "sleep_loop", mod)
    monkeypatch.setattr(cm, "_wizard_library_db_guard", lambda: None)
    monkeypatch.setattr(cm, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(sys, "argv", ["claude_memory.py", "sleep"] + argv)
    try:
        cm.main()
    except SystemExit as e:
        return e.code or 0
    return 0


def test_catch_up_reaches_the_sleep_loop(monkeypatch, tmp_path, capsys):
    mod, seen = _fake_sleep_loop()
    rc = _run(monkeypatch, tmp_path, ["--catch-up", "--progress-every", "5"], mod)
    assert rc == 0
    assert seen["overrides"] == {"budget_seconds": None, "catch_up": True, "progress_every": 5.0}
    assert seen["config"]["budget_seconds"] == 0


def test_budget_seconds_reaches_the_sleep_loop(monkeypatch, tmp_path, capsys):
    mod, seen = _fake_sleep_loop()
    _run(monkeypatch, tmp_path, ["--budget-seconds", "540"], mod)
    assert seen["overrides"]["budget_seconds"] == 540.0 and seen["overrides"]["catch_up"] is False


def test_the_exit_status_is_the_sleep_loops_mapping(monkeypatch, tmp_path, capsys):
    mod, _seen = _fake_sleep_loop(status="timeout", code=1)
    assert _run(monkeypatch, tmp_path, [], mod) == 1
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "timeout"


def test_an_older_sleep_loop_keeps_the_round3_mapping_and_says_the_flags_were_not_honoured(monkeypatch, tmp_path,
                                                                                         capsys):
    mod, seen = _fake_sleep_loop(with_overrides=False, status="skipped")
    rc = _run(monkeypatch, tmp_path, ["--catch-up"], mod)
    assert rc == 75
    assert "overrides" not in seen
    assert "lacks" in capsys.readouterr().err

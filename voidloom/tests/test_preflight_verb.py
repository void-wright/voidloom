"""`voidloom preflight` (decision D1, handoff round 1): the CLI verb is a pass-through that EXECs install/preflight.py.

Lane L2 owns voidloom/cli.py; lane L4 owns install/preflight.py. These tests never run the real preflight (a ten-minute
venv build): the script path and os.execv are doubled, so the verb's grammar and hand-off are what is proven."""
import os
import sys

import pytest

from voidloom import cli


def _parse(argv):
    """dispatch()'s parse, without running the verb."""
    parser = cli.build_parser()
    args, extras = parser.parse_known_args(argv)
    assert args.command == "preflight"
    args.rest = list(extras) + list(args.rest or [])
    return args


def test_every_token_after_the_verb_belongs_to_the_preflight():
    args = _parse(["preflight", "--help", "--out", "x.json", "--tier", "1"])
    assert args.rest == ["--help", "--out", "x.json", "--tier", "1"]
    assert args._fn is cli.verb_preflight


def test_the_verb_execs_the_preflight_with_its_arguments(tmp_path, monkeypatch):
    script = tmp_path / "preflight.py"
    script.write_text("print('stub')\n")
    seen = {}

    def fake_execv(path, argv):
        seen["path"], seen["argv"] = path, argv
        raise SystemExit(0)
    monkeypatch.setattr(cli, "_preflight_script", lambda: str(script))
    monkeypatch.setattr(cli.os, "execv", fake_execv)
    with pytest.raises(SystemExit):
        cli.verb_preflight(_parse(["preflight", "--", "--out", "p.json"]))
    assert seen["path"] == sys.executable
    assert seen["argv"] == [sys.executable, "-B", str(script), "--out", "p.json"]


def test_dispatch_routes_preflight_and_does_not_reject_its_options(tmp_path, monkeypatch):
    script = tmp_path / "preflight.py"
    script.write_text("")
    calls = []
    monkeypatch.setattr(cli, "_preflight_script", lambda: str(script))
    monkeypatch.setattr(cli.os, "execv", lambda p, a: (calls.append(a), (_ for _ in ()).throw(SystemExit(7))))
    with pytest.raises(SystemExit) as ei:
        cli.dispatch(["preflight", "--keep", "--python", "/usr/bin/python3"])
    assert ei.value.code == 7
    assert calls and calls[0][3:] == ["--keep", "--python", "/usr/bin/python3"]


def test_an_absent_preflight_is_a_typed_error_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_preflight_script", lambda: str(tmp_path / "nope.py"))
    env = cli.verb_preflight(_parse(["preflight"]))
    assert env["code"] == "preflight_absent" and env["status"] == cli.STATUS_ERROR


def test_the_script_is_looked_for_beside_the_package():
    root = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
    assert cli._preflight_script() == os.path.join(root, "install", "preflight.py")

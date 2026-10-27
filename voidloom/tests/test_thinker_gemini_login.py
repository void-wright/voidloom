# -*- coding: utf-8 -*-
"""`voidloom thinker login gemini` -- Round 1 lane L-G (SYNTHESIS R1, the binding order), against a fake agy.

  1. the NEGATIVE CONTROL first: the ordinary seam call (not sandboxed, as the timers call) with the CLI's home an
     EMPTY folder must not pass -- a program that finds ANOTHER sign-in (the owner's, through the password database:
     VAEL D1 #2) is caught here, and nothing is signed in;
  2. agy's own interactive sign-in, with HOME = the brain's gemini-home, INSIDE bubblewrap: the owner's ~/.gemini and
     the per-user runtime folder (the keyring's D-Bus socket) are empty tmpfs there, the environment is cleared to the
     calls' own, and no D-Bus variable rides along;
  3. the sealed proof through the seam; the owner's sign-in file stamped (sha + mtime) before and after.
Driven through a real pseudo-terminal (the verb reads /dev/tty) and the REAL bwrap; skipped where bwrap cannot make a
sandbox (a CI runner without user namespaces) -- skipped loudly, never passed.
"""
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
from voidloom.tests import _thinker_gemini_fake as G
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
THINKER_PY = Path(thinker.__file__).resolve()


def _bwrap_works():
    bw = shutil.which("bwrap")
    if not bw:
        return False
    try:
        return subprocess.run([bw, "--dev-bind", "/", "/", "--", "/bin/true"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_bwrap = pytest.mark.skipif(not (sys.platform.startswith("linux") and _bwrap_works()),
                                 reason="bubblewrap cannot make a sandbox here (the gemini sign-in refuses without one)")


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_gemini_login_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def login_in_pty(extra_env=None, args=()):
    env = {k: os.environ[k] for k in ("HOME", "PATH", "WIZARD_LIBRARY_DB", "TMPDIR", "LANG") if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TERM"] = "xterm"
    env.update(extra_env or {})
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-B", str(THINKER_PY), "login", "gemini"] + list(args), env)
    out = b""
    end = time.time() + 180
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 1)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode("utf-8", "replace")


def _owner_token(w):
    f = w.home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    f.parent.mkdir(parents=True)
    f.write_text("the owner's everyday sign-in (test-only)")
    return f


@needs_bwrap
def test_negative_control_first_then_a_sandboxed_sign_in_then_the_proof(w):
    owner = _owner_token(w)
    before = thinker._file_stamp(owner)
    uid_run = "/run/user/%d" % os.getuid()
    G.make_fake_agy(w.bin, good_token="fake-brain-sign-in", log=str(w.log),
                    probe_visible=[str(owner.parent), uid_run])
    rc, out = login_in_pty()
    assert rc == 0, out[-1500:]
    calls = [r for r in F.read_log(w.log) if "argv" in r]
    first_p = [i for i, c in enumerate(calls) if "-p" in c["argv"]]
    login = [i for i, c in enumerate(calls) if "-p" not in c["argv"] and c["argv"][:1] != ["--version"]]
    assert first_p and login and first_p[0] < login[0], "the negative control runs BEFORE the sign-in"
    neg = calls[first_p[0]]
    assert neg["token_present"] is False and neg["env"]["HOME"] != str(w.thinker_home / "gemini-home"), \
        "the negative control's home is an EMPTY throwaway, never the brain's"
    sign = calls[login[0]]
    gh = str(w.thinker_home / "gemini-home")
    assert sign["env"]["HOME"] == gh and sign["env"]["XDG_RUNTIME_DIR"] == gh + "/run"
    assert "DBUS_SESSION_BUS_ADDRESS" not in sign["env"] and sign["stdin_tty"] is True
    assert sign["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert sign["visible"][str(owner.parent)] is False, "the owner's ~/.gemini is an empty tmpfs inside the sandbox"
    if os.path.isdir(uid_run):
        assert sign["visible"][uid_run] is False, "the per-user runtime folder (the keyring's bus) is hidden"
    assert neg["visible"][str(owner.parent)] is False, "the negative control runs sandboxed, as every call does"
    assert any(owner.parent.iterdir()), "positive control: outside the sandbox the owner's folder is there"
    tok = Path(gh) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    assert tok.is_file() and oct(tok.stat().st_mode & 0o777) == "0o600"
    assert oct(Path(gh).stat().st_mode & 0o777) == "0o700"
    assert "Step 1 of 3" in out and "Step 2 of 3" in out and "Signed in:" in out
    assert "may be used to improve Google's products" in out and "not touched" in out
    assert thinker._file_stamp(owner) == before


@needs_bwrap
def test_a_negative_control_that_answers_refuses_and_nothing_is_signed_in(w):
    _owner_token(w)
    # a program that, finding no sign-in in its HOME, answers with ANOTHER credential the sandbox does not hide (a
    # keyring, an API key): the negative control catches it and nothing is signed in
    other = w.root / "some-other-credential"
    other.write_text("a credential that is not the brain's")
    G.make_fake_agy(w.bin, log=str(w.log), fallback_token=str(other))
    rc, out = login_in_pty()
    assert rc == 1 and "ANSWERED with no sign-in" in out, out[-1200:]
    calls = [r for r in F.read_log(w.log) if "argv" in r]
    assert all("-p" in c["argv"] or c["argv"][:1] in (["--version"], ["models"]) for c in calls), \
        "the sign-in never started"
    assert not (w.thinker_home / "gemini-home" / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists()


@needs_bwrap
def test_the_sandbox_defeats_the_password_entry_fallback(w):
    """Owner run T2: agy found the owner's sign-in through the password entry. The fake does the same (it reads the
    owner's file when its own HOME has none) -- and inside the sandbox that file is not there, so the negative control
    is refused and the sign-in proceeds."""
    owner = _owner_token(w)
    G.make_fake_agy(w.bin, log=str(w.log), fallback_token=str(owner), good_token="fake-brain-sign-in")
    rc, out = login_in_pty()
    assert rc == 0 and "refused, as it must be" in out, out[-1200:]


@needs_bwrap
def test_a_sign_in_that_leaves_no_file_is_an_error(w):
    G.make_fake_agy(w.bin, good_token="fake-brain-sign-in", log=str(w.log), login_fails=True)
    rc, out = login_in_pty()
    assert rc == 1 and "did not complete" in out and "antigravity-oauth-token" in out, out[-1200:]


def test_without_bwrap_it_refuses_and_changes_nothing(w, monkeypatch):
    G.make_fake_agy(w.bin, log=str(w.log))
    orig = shutil.which
    monkeypatch.setattr(thinker.shutil, "which", lambda n, *a, **k: None if n == "bwrap" else orig(n, *a, **k))
    monkeypatch.setattr(thinker, "_have_tty", lambda: True)
    env = thinker.run(["login", "gemini"])
    assert env["_exit_code"] == 3 and env["code"] == "thinker_login_needs_sandbox", env
    assert "bubblewrap" in env["_human"] and not (w.thinker_home / "gemini-home").exists()
    assert not F.read_log(w.log), "agy never ran"


def test_without_a_terminal_it_refuses(w):
    G.make_fake_agy(w.bin, log=str(w.log))
    p = subprocess.run([sys.executable, "-B", str(THINKER_PY), "login", "gemini"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, start_new_session=True, timeout=60,
                       env={k: os.environ[k] for k in ("HOME", "PATH", "WIZARD_LIBRARY_DB", "TMPDIR")})
    assert p.returncode == 2 and "needs a terminal" in p.stdout, (p.stdout, p.stderr)


def test_the_sandbox_argv_hides_and_clears(w, monkeypatch):
    owner = _owner_token(w)
    entry = thinker.load_catalog()["backends"]["gemini"]
    hide = thinker.login_hidden_paths(entry)
    assert str(owner.parent.parent) in hide
    env = thinker.cli_home_env(entry, w.thinker_home / "gemini-home", term="xterm")
    assert "DBUS_SESSION_BUS_ADDRESS" not in env and env["HOME"].endswith("gemini-home")
    argv = thinker.bwrap_argv("/usr/bin/bwrap", hide, env, "/tmp/x", ["/bin/agy"])
    assert argv[:4] == ["/usr/bin/bwrap", "--dev-bind", "/", "/"] and "--clearenv" in argv
    for h in hide:
        assert ["--tmpfs", h] == argv[argv.index(h) - 1:argv.index(h) + 1]
    assert argv[-4:] == ["--chdir", "/tmp/x", "--", "/bin/agy"]


@needs_bwrap
def test_any_answer_with_no_sign_in_refuses_even_a_wrong_one(w):
    """The negative control fails on ANY answer, not on a passing proof: a program that answers (wrongly) with no
    sign-in of the brain's has still found a credential somewhere."""
    _owner_token(w)
    other = w.root / "some-other-credential"
    other.write_text("a credential that is not the brain's")
    G.make_fake_agy(w.bin, log=str(w.log), fallback_token=str(other), wrong_sum=True)
    rc, out = login_in_pty()
    assert rc == 1 and "ANSWERED with no sign-in" in out, out[-1200:]

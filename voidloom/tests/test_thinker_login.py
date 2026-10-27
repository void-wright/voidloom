# -*- coding: utf-8 -*-
"""M7 -- `voidloom thinker login <claude|kimi|codex>`: ONE human act, then proof.

The owner's rule (#22013): every secret prompt the package owns ASKS TWICE -- the ask-twice secret pattern:
hidden, twice, must match, stripped, a length floor, 0600 in a 0700 folder, a 12-character sha256 fingerprint, never
argv/history/chat. The guide's A4 line was tested on six scripted inputs (scratch guide_cmd_test3); the verb must
behave EXACTLY the same on the same six:
  match            -> Saved (NN characters, fingerprint xxxxxxxxxxxx).
  differ           -> The two pastes were different, so nothing was saved.
  stray key, key   -> (the exact failure the owner hit: 'y' then the key) the two pastes were different
  short ('y','y')  -> That paste had only 1 character(s) ... nothing was saved
  CR line ends     -> stripped: saved, the same fingerprint as the clean key
  empty            -> That paste had only 0 character(s) ...
Every comparison is FIXED TEXT (==), never a pattern (R-2 / EW5-02: '.', '-e0', '.*', '0*' passed a grep validator).
Then the thinker_proof method: a sealed call with the pasted key ALONE, and FIRST a fake-key negative control that
must NOT prove -- if it does, something other than the key answers, and nothing is saved. Codex: `codex login
--device-auth` with CODEX_HOME = the brain's own folder (cli_auth_credentials_store = "file"), then the proof.
Driven through a real pseudo-terminal (the verb reads /dev/tty); the doubles are the only CLIs.
"""
import hashlib
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
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
THINKER_PY = Path(thinker.__file__).resolve()
KEY = "sk-" + "Kx7Qm2Vt9Lw4Rb8Nz1Hc6Jd3Fp5Gs0Ya2Ue7Io"           # 41 characters, test-only
assert len(KEY) == 41


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_m7_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _read_until(fd, token, secs=30):
    buf = b""
    end = time.time() + secs
    while token not in buf and time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
    return buf


def login_in_pty(backend, answers, extra=()):
    """Run `thinker.py login <backend>` on a pseudo-terminal, typing each answer at its prompt.
    Returns (rc, transcript)."""
    env = {k: os.environ[k] for k in ("HOME", "PATH", "WIZARD_LIBRARY_DB", "TMPDIR", "LANG") if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-B", str(THINKER_PY), "login", backend] + list(extra), env)
    out = b""
    for prompt, answer in zip((b"Paste your", b"again to confirm"), answers):
        out += _read_until(fd, prompt)
        os.write(fd, answer.encode("utf-8"))
    while True:
        r, _, _ = select.select([fd], [], [], 60)
        if not r:
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode("utf-8", "replace")


def _kimi(w, **behaviour):
    return F.make_fake(w.bin, "kimi", log=str(w.log), good_key=KEY, **behaviour)


FP = hashlib.sha256(KEY.encode()).hexdigest()[:12]
CASES = [
    ("match", [KEY + "\n", KEY + "\n"], 0, "Saved (41 characters, fingerprint %s)." % FP),
    ("differ", [KEY + "\n", KEY[:-1] + "Z\n"], 2, "The two pastes were different, so nothing was saved."),
    ("stray-key-then-key", ["y\n", KEY + "\n"], 2, "The two pastes were different, so nothing was saved."),
    ("short", ["y\n", "y\n"], 2, "That paste had only 1 character(s)."),
    ("cr", [KEY + "\r\n", KEY + "\r\n"], 0, "Saved (41 characters, fingerprint %s)." % FP),
    ("empty", ["\n", "\n"], 2, "That paste had only 0 character(s)."),
]


@pytest.mark.parametrize("name,answers,rc_want,said", CASES, ids=[c[0] for c in CASES])
def test_the_six_scripted_cases(w, name, answers, rc_want, said):
    _kimi(w)
    rc, out = login_in_pty("kimi", answers)
    assert rc == rc_want, (name, rc, out[-800:])
    assert said in out, (name, out[-800:])
    kf = w.thinker_home / "kimi.env"
    if rc_want == 0:
        assert oct(kf.stat().st_mode & 0o777) == "0o600" and oct(w.thinker_home.stat().st_mode & 0o777) == "0o700"
        assert thinker.read_env_lines(kf)[0]["KIMI_MODEL_API_KEY"] == KEY
        calls = [r for r in F.read_log(w.log) if not r.get("end")]
        assert len(calls) == 2, "exactly two calls: the negative control, then the proof"
        assert calls[0]["env"]["KIMI_MODEL_API_KEY"] != KEY, "the fake-key negative control runs FIRST"
        assert calls[1]["env"]["KIMI_MODEL_API_KEY"] == KEY
    else:
        assert not kf.exists(), "a refused paste saved something"
        assert F.read_log(w.log) == [], "a refused paste must not cost a call"
    assert KEY not in out, "the key was echoed to the terminal"
    for r in F.read_log(w.log):
        assert KEY not in " ".join(r.get("argv") or []), "the key reached an argv"


def test_a_fake_key_that_proves_saves_nothing(w):
    """If the sealed call answers with a FAKE key, the seal is broken: the verb refuses and saves nothing."""
    F.make_fake(w.bin, "kimi", log=str(w.log))           # accepts ANY key -- the broken-seal emulation
    rc, out = login_in_pty("kimi", [KEY + "\n", KEY + "\n"])
    assert rc == 1 and "answered with a FAKE key" in out, out[-600:]
    assert not (w.thinker_home / "kimi.env").exists()


def test_a_key_the_backend_refuses_is_not_saved(w):
    F.make_fake(w.bin, "kimi", log=str(w.log), good_key="some-other-key-entirely-0123456789")
    rc, out = login_in_pty("kimi", [KEY + "\n", KEY + "\n"])
    assert rc == 1 and "NOT saved" in out, out[-600:]
    assert not (w.thinker_home / "kimi.env").exists()


def test_claude_login_is_the_same_verb(w):
    F.make_fake(w.bin, "claude", log=str(w.log), good_key=KEY)
    rc, out = login_in_pty("claude", [KEY + "\n", KEY + "\n"])
    assert rc == 0 and ("fingerprint %s" % FP) in out, out[-600:]
    assert thinker.read_env_lines(w.thinker_home / "claude.env")[0]["CLAUDE_CODE_OAUTH_TOKEN"] == KEY


def test_without_a_terminal_it_refuses(w):
    _kimi(w)
    p = subprocess.run([sys.executable, "-B", str(THINKER_PY), "login", "kimi"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, start_new_session=True, timeout=60,
                       env={k: os.environ[k] for k in ("HOME", "PATH", "WIZARD_LIBRARY_DB", "TMPDIR")})
    assert p.returncode == 2 and "needs a terminal" in p.stdout, (p.stdout, p.stderr)
    assert "never run it for them" in p.stdout


def test_codex_login_is_a_device_code_into_the_brains_own_folder(w):
    fake = F.make_fake(w.bin, "codex", log=str(w.log))
    owner = w.home / ".codex" / "auth.json"
    owner.parent.mkdir(parents=True)
    owner.write_text('{"owner": "everyday login, test-only"}')
    before = thinker._file_stamp(owner)
    rc, out = login_in_pty("codex", [])
    assert rc == 0, out[-800:]
    ch = w.thinker_home / "codex"
    assert oct(ch.stat().st_mode & 0o777) == "0o700" and (ch / "auth.json").is_file()
    assert 'cli_auth_credentials_store = "file"' in (ch / "config.toml").read_text()
    calls = [r for r in F.read_log(w.log) if not r.get("end")]
    assert calls[0]["argv"] == ["login", "--device-auth"] and calls[0]["env"]["CODEX_HOME"] == str(ch)
    assert calls[1]["argv"] == ["login", "status"]
    assert calls[2]["argv"][0] == "exec", "then the proof, through the seam"
    assert "PASSED" in out and thinker._file_stamp(owner) == before
    F.set_behaviour(fake, login_fails=True, log=str(w.log))
    (ch / "auth.json").unlink()
    rc, out = login_in_pty("codex", [])
    assert rc == 1 and "did not complete" in out, out[-600:]

# -*- coding: utf-8 -*-
"""M6 -- ONE CREDENTIAL RESOLVER, one home per backend, a MINIMAL environment per call, codex serialized.

#21993: ~/.voidloom/thinker/ (0700) -- claude.env, kimi.env, codex/ (the brain's own CODEX_HOME). DESSA found TWO
Claude homes: six organs read a sidecar file `claude-oauth-token` while the units and token_doctor read
CLAUDE_CODE_OAUTH_TOKEN from the env file -- `thinker migrate` ends that, in scratch homes only here (fake tokens;
the owner's real token is never read by this suite). Round 0: the timers' env file hands the Claude token to every
organ process and `codex -s read-only` printed a planted env nonce -- so each call's environment is an ALLOWLIST:
Codex never receives the Claude token and no API-key variable reaches any backend. OpenAI: one auth.json per
serialized stream -- a flock on codex/.thinker.lock. The owner's everyday logins (~/.codex/auth.json,
~/.kimi-code/credentials/*) must be byte-identical before and after, and a backend that rewrites one FAILS the probe.
"""
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
NONCES = {k: "NONCE-%s-%s" % (k, os.getpid()) for k in thinker.SECRET_ENV + ("SOME_OTHER_SECRET",)}


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_m6_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _owner_logins(w):
    files = [w.home / ".codex" / "auth.json", w.home / ".kimi-code" / "credentials" / "kimi-code.json",
             w.home / ".claude" / ".credentials.json"]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"owner": "everyday login, test-only"}')
    return files


def _stamps(files):
    return {str(f): thinker._file_stamp(f) for f in files}


def test_the_resolver_order_and_the_legacy_flag(w, monkeypatch):
    assert thinker.resolve_credential("claude") is None
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-from-the-environment-test-only")
    c = thinker.resolve_credential("claude")
    assert c.legacy and c.source.startswith("environment")
    (w.home / ".voidloom").mkdir(parents=True, exist_ok=True)
    (w.home / ".voidloom" / "claude-oauth-token").write_text("sk-ant-oat01-from-the-sidecar-test-only\n")
    c = thinker.resolve_credential("claude")
    assert c.legacy and c.source.endswith("claude-oauth-token"), "the sidecar is what the organs used before"
    F.save_cred(w, "claude", "sk-ant-oat01-from-claude-env-test-only")
    c = thinker.resolve_credential("claude")
    assert not c.legacy and c.source == str(w.thinker_home / "claude.env")
    assert c.fingerprint == thinker._fp("sk-ant-oat01-from-claude-env-test-only")
    assert thinker.resolve_credential("kimi") is None
    assert thinker.resolve_credential("codex") is None
    F.save_cred(w, "codex")
    assert thinker.resolve_credential("codex").source.endswith("codex/auth.json")


def _env_file(w, line):
    f = w.home / ".voidloom" / "wizard-library-env"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# BEGIN Voidloom MANAGED\nPATH=/usr/bin:/bin\n# END Voidloom MANAGED\n" + line)
    return f


def test_migrate_ends_the_two_claude_homes(w):
    tok = "sk-ant-oat01-" + "a" * 60
    envf = _env_file(w, "CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok)
    side = w.home / ".voidloom" / "claude-oauth-token"
    side.write_text(tok + "\n")
    before = _stamps([envf, side])
    r = thinker.migrate_claude()
    assert r["status"] == "ok" and r["code"] == "migrated", r
    assert r["fingerprint"] == thinker._fp(tok) and len(r["sources"]) == 2
    f = w.thinker_home / "claude.env"
    assert oct(f.stat().st_mode & 0o777) == "0o600" and oct(w.thinker_home.stat().st_mode & 0o777) == "0o700"
    assert thinker.read_env_lines(f)[0]["CLAUDE_CODE_OAUTH_TOKEN"] == tok
    assert _stamps([envf, side]) == before, "migration never edits the old homes (the env file has ONE writer)"
    c = thinker.resolve_credential("claude")
    assert not c.legacy
    assert thinker.migrate_claude()["code"] == "already"


def test_migrate_reads_a_symlinked_env_file_and_keeps_the_link(w):
    """P12: on the reference host the env file is a SYMLINK. migrate READS through it (the one reader,
    install/env_file.py) and writes nothing there: the link and its target are unchanged."""
    tok = "sk-ant-oat01-" + "d" * 60
    target = w.root / "legacy-home" / "wizard-library-env"
    target.parent.mkdir()
    target.write_text("# BEGIN Voidloom MANAGED\nPATH=/usr/bin:/bin\n# END Voidloom MANAGED\n"
                      "CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok)
    link = w.home / ".voidloom" / "wizard-library-env"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    before = (os.readlink(str(link)), thinker._file_stamp(target))
    r = thinker.migrate_claude()
    assert r["code"] == "migrated" and r["fingerprint"] == thinker._fp(tok), r
    assert link.is_symlink() and (os.readlink(str(link)), thinker._file_stamp(target)) == before


def test_migrate_refuses_to_choose_between_two_tokens(w):
    _env_file(w, "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-%s\n" % ("b" * 60))
    (w.home / ".voidloom" / "claude-oauth-token").write_text("sk-ant-oat01-%s\n" % ("c" * 60))
    r = thinker.migrate_claude()
    assert r["status"] == "refused" and r["code"] == "two_different_tokens", r
    assert len({s["fingerprint"] for s in r["sources"]}) == 2
    assert not (w.thinker_home / "claude.env").exists()
    assert "b" * 20 not in str(r) and "c" * 20 not in str(r), "a refusal must never carry a token value"


def test_migrate_with_nothing_to_migrate(w):
    assert thinker.migrate_claude()["code"] == "nothing_to_migrate"


@pytest.mark.parametrize("backend", ["claude", "codex", "kimi"])
def test_a_planted_env_nonce_never_reaches_a_backend(w, monkeypatch, backend):
    F.make_fake(w.bin, backend, log=str(w.log))
    F.save_cred(w, backend, "the-backends-own-credential-0123456789")
    F.record(thinker, backend)
    for k, v in NONCES.items():
        monkeypatch.setenv(k, v)
    thinker.think("hello", purpose="sleep_loop", timeout_s=30)
    env = [r for r in F.read_log(w.log) if not r.get("end")][-1]["env"]
    blob = repr(env)
    for k, v in NONCES.items():
        assert v not in blob, "%s's planted value reached the %s call" % (k, backend)
    if backend == "claude":
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "the-backends-own-credential-0123456789"
    if backend == "kimi":
        assert env["KIMI_MODEL_API_KEY"] == "the-backends-own-credential-0123456789"
        assert env["KIMI_MODEL_BASE_URL"] == "https://api.kimi.com/coding/v1" and env["KIMI_MODEL_PROVIDER_TYPE"] == "kimi"
    if backend == "codex":
        assert not [k for k in env if "API_KEY" in k or "TOKEN" in k], sorted(env)


def test_two_concurrent_codex_thinks_serialize(w):
    fake = F.make_fake(w.bin, "codex", log=str(w.log))
    F.save_cred(w, "codex")
    F.record(thinker, "codex")
    F.set_behaviour(fake, sleep=1.5, log=str(w.log))
    errors = []

    def one(i):
        try:
            thinker.think("call %d" % i, purpose="sleep_loop", timeout_s=30)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=one, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    log = F.read_log(w.log)
    starts = {r["pid"]: r["t0"] for r in log if not r.get("end")}
    ends = {r["pid"]: r["t1"] for r in log if r.get("end")}
    spans = sorted((starts[p], ends[p]) for p in list(starts)[-2:])
    assert spans[1][0] >= spans[0][1] - 0.05, "two codex calls overlapped on one auth.json: %s" % spans
    assert (w.thinker_home / "codex" / ".thinker.lock").exists()


@pytest.mark.parametrize("backend", ["codex", "kimi", "claude"])
def test_the_owners_everyday_logins_are_byte_identical(w, backend):
    owner = _owner_logins(w)
    before = _stamps(owner)
    fake = F.make_fake(w.bin, backend, log=str(w.log))
    F.save_cred(w, backend)
    F.record(thinker, backend)
    thinker.think("hello", purpose="sleep_loop", timeout_s=30)
    res = thinker.probe_candidate(thinker.read_record(), timeout_s=30)
    assert res["passed"] and _stamps(owner) == before
    if backend in ("codex", "kimi"):
        assert res["owner_login"]["untouched"] is True and res["owner_login"]["binding"] is True
        # the detector: a backend that rewrites the owner's login FAILS the probe
        target = owner[0] if backend == "codex" else owner[1]
        F.set_behaviour(fake, touch=str(target), log=str(w.log))
        res = thinker.probe_candidate(thinker.read_record(), timeout_s=30)
        assert res["passed"] is False and res["owner_login"]["untouched"] is False

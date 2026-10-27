# -*- coding: utf-8 -*-
"""Round 2 wave 1 (model-choice unit), MC-R1-11 -- EVERY failure of think() is a ThinkError, fixed AT THE SEAM.

At the base every organ wrapped its own `claude -p` launch in `except (..., Exception)`; the seam replaced those launches
and every organ now catches ThinkError alone. So anything that leaves think() untyped aborts the organ's night instead of
taking its loud fallback path. The reflector measured two members (a NUL byte -> ValueError, a 200 KB prompt ->
OSError E2BIG, both on the template path and through a claude double). The class, swept over every raise path of think()
and of the four seam entry points organs call directly (think, read_record, load_catalog; health_line / weekly_check are
wrapped by their organs):

  a prompt the transport cannot carry   NUL, over 131072 bytes, a lone surrogate      -> ThinkMalformed (both CLI paths)
  the caller's arguments                an unknown purpose, a bad tier / timeout,
                                        a prompt that is not text                     -> ThinkMisuse (ThinkError AND ValueError)
  the record                            sealed but not UTF-8                          -> ThinkUnavailable (RecordError on read)
  the catalog                           the wrong SHAPE (validation itself crashed)   -> ThinkUnavailable (CatalogError)
  the HTTP transport                    an endpoint that is not a URL; a server that
                                        does not speak HTTP                           -> ThinkUnavailable
  the machine                           no room for the call's working folder         -> ThinkInternal
  the credential                        an unreadable credential file                 -> ThinkUnavailable auth
  anything nobody foresaw               (an exception injected into the call path)    -> ThinkInternal, naming it
  the proof                             systemd-run printing a broken result line     -> a FAILED verdict, never a traceback

Each: the typed class, exactly ONE THINKER_LOG row for the call. The organs are NOT changed (last test): the fix is the seam's.
"""
import ast
import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
ORGANS = Path(thinker.__file__).resolve().parent


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_typed_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _claude(w):
    F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")


def _template(w):
    F.make_fake(w.bin, "ollama", kind="ollama-run")
    F.record(thinker, "local", "--command", "ollama run {model} {prompt}", "--model", "m")


def _one_row(before, outcome):
    rows = thinker.read_log()
    assert len(rows) == before + 1, "exactly one THINKER_LOG row for the call: %r" % rows[before:]
    assert rows[-1]["outcome"] == outcome, rows[-1]
    return rows[-1]


def _think(**kw):
    kw.setdefault("purpose", "sleep_loop")
    kw.setdefault("timeout_s", 30)
    prompt = kw.pop("prompt", "a prompt")
    return thinker.think(prompt, **kw)


# --------------------------------------------------------------------------- a prompt the transport cannot carry
@pytest.mark.parametrize("path", ["claude", "template"])
@pytest.mark.parametrize("prompt", ["before\x00after", "m" * 200000, "a lone surrogate \ud800 here"],
                         ids=["nul", "e2big", "surrogate"])
def test_an_unpassable_prompt_is_malformed_on_both_cli_paths(w, path, prompt):
    (_claude if path == "claude" else _template)(w)
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkMalformed) as ei:
        _think(prompt=prompt)
    assert "could not start" in str(ei.value) and "131072" in str(ei.value)
    _one_row(n, "malformed")


# --------------------------------------------------------------------------- the caller's arguments
@pytest.mark.parametrize("kw", [dict(purpose="not_an_organ"), dict(tier="giant"), dict(timeout_s=0),
                                dict(timeout_s=-5), dict(timeout_s="30"), dict(timeout_s=True),
                                dict(timeout_s=float("nan")), dict(prompt=None), dict(prompt=b"bytes"),
                                dict(system=42)],
                         ids=["purpose", "tier", "timeout0", "timeout-neg", "timeout-str", "timeout-bool",
                              "timeout-nan", "prompt-none", "prompt-bytes", "system-int"])
def test_wrong_arguments_are_misuse_both_a_thinkerror_and_a_valueerror(w, kw):
    _claude(w)
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkError) as ei:
        _think(**kw)
    assert isinstance(ei.value, thinker.ThinkMisuse) and isinstance(ei.value, ValueError), repr(ei.value)
    _one_row(n, "misuse")


# --------------------------------------------------------------------------- the record, the catalog
def test_a_sealed_record_that_is_not_utf8_is_refused_typed(w):
    _claude(w)
    body = b"# a record\nschema=1\nbackend=claude\nmodel=\xff\xfe\n"
    import hashlib
    thinker.record_path().write_bytes(body + b"record_sha256=" + hashlib.sha256(body).hexdigest().encode() + b"\n")
    with pytest.raises(thinker.RecordError):
        thinker.read_record()                      # what gap_mapping calls directly, catching RecordError
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        _think()
    assert ei.value.reason == "unconfigured"
    _one_row(n, "unavailable")


def test_a_catalog_of_the_wrong_shape_is_refused_typed(w, monkeypatch):
    bad = w.root / "thinkers.json"
    bad.write_text(json.dumps({"purposes": {"text": ["sleep_loop"], "research": ["gap_research"]},
                               "backends": {"claude": {"transport": "cli", "output": [1], "posture": {"text": []}}}}))
    monkeypatch.setattr(thinker, "CATALOG_PATH", bad)
    with pytest.raises(thinker.CatalogError):
        thinker.load_catalog()                     # what gap_mapping calls directly, catching CatalogError
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkUnavailable):
        _think()
    _one_row(n, "unavailable")


# --------------------------------------------------------------------------- the HTTP transport
def _seal(fields):
    thinker._commit_record(fields, thinker._Proven(True, fields, {}))


@pytest.mark.parametrize("endpoint,reason", [("gpu-box", "unconfigured"), ("127.0.0.1:11434", "unreachable")])
def test_an_endpoint_that_is_not_a_url_is_typed(w, endpoint, reason):
    """'gpu-box' has no scheme at all (urllib's Request raises ValueError: the member); '127.0.0.1:11434' parses as the
    scheme '127.0.0.1' and urlopen's URLError was already typed (a control: typed on both trees)."""
    _seal({"schema": "1", "backend": "local-http-ollama", "transport": "http-ollama", "model": "m",
           "endpoint": endpoint})
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        _think()
    assert ei.value.reason == reason, repr(ei.value)
    _one_row(n, "unavailable")


def test_a_server_that_does_not_speak_http_is_typed(w):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    stop = []

    def serve():
        while not stop:
            try:
                c, _a = srv.accept()
            except OSError:
                return
            try:
                c.recv(65536)
                c.sendall(b"SSH-2.0-OpenSSH_9.6 not a model server\r\n")
            finally:
                c.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        _seal({"schema": "1", "backend": "local-http-ollama", "transport": "http-ollama", "model": "m",
               "endpoint": "http://127.0.0.1:%d" % srv.getsockname()[1]})
        n = len(thinker.read_log())
        with pytest.raises(thinker.ThinkUnavailable) as ei:
            _think(timeout_s=10)
        assert ei.value.reason == "unreachable", repr(ei.value)
        _one_row(n, "unavailable")
    finally:
        stop.append(1)
        srv.close()


# --------------------------------------------------------------------------- the machine, the credential
def test_no_room_for_the_call_folder_is_typed(w, monkeypatch):
    _claude(w)
    monkeypatch.setattr(tempfile, "tempdir", str(w.root / "gone" / "tmp"))    # mkdtemp there: FileNotFoundError
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkInternal) as ei:
        _think()
    assert ei.value.reason == "FileNotFoundError" and "thinker.py:" in str(ei.value)
    _one_row(n, "internal")


def test_an_unreadable_credential_file_is_an_auth_failure(w):
    _claude(w)
    f = Path(w.thinker_home) / "claude.env"
    f.write_bytes(b"CLAUDE_CODE_OAUTH_TOKEN=\xff\xfe\xfd not utf-8\n")
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        _think()
    assert ei.value.reason == "auth" and "could not be read" in str(ei.value)
    _one_row(n, "unavailable")


# --------------------------------------------------------------------------- anything nobody foresaw
def test_an_unforeseen_exception_on_the_call_path_is_typed(w, monkeypatch):
    _claude(w)

    def boom(*a, **k):
        raise KeyError("a key nobody foresaw")
    monkeypatch.setattr(thinker, "_build_cli", boom)
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkInternal) as ei:
        _think()
    assert ei.value.reason == "KeyError" and "nobody foresaw" in str(ei.value)
    _one_row(n, "internal")
    assert not list((Path(tempfile.gettempdir())).glob("voidloom-think-*")), "the call folder is removed"


# --------------------------------------------------------------------------- the proof
def test_a_broken_proof_line_is_a_failed_verdict_never_a_traceback(w):
    F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    shim = w.bin / "systemd-run"
    shim.write_text("#!/bin/sh\necho '{this is not json'\n", encoding="utf-8")
    shim.chmod(0o755)
    env = thinker.run(["set", "claude", "--probe-env", "systemd", "--answered-by", "test", "--timeout", "5"])
    assert env["code"] == "thinker_probe_failed" and env["_exit_code"] == 1, env
    assert "not a JSON object" in env["data"]["probe"]["error"], env["data"]["probe"]
    assert not thinker.record_path().exists()


# --------------------------------------------------------------------------- the organs are unchanged
def test_organs_still_catch_the_thinkerror_family_alone():
    """The fix is AT THE SEAM: every try around an organ's think() call catches members of the ThinkError family only
    (the family's names on the seam module), never a bare Exception -- MC-R1-11 is not fixed by widening the organs."""
    family = {"ThinkError", "ThinkUnavailable", "ThinkTimeout", "ThinkMalformed", "ThinkRefused", "ThinkSkipped",
              "ThinkOverflow", "ThinkMisuse", "ThinkInternal"}
    seen = 0
    for p in sorted(ORGANS.glob("*.py")):
        if p.name == "thinker.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        parent = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parent[ch] = node
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "think"
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "_thinker"):
                continue
            seen += 1
            node, guard = call, None
            while node in parent:                  # the INNERMOST try whose BODY holds the call
                up = parent[node]
                if isinstance(up, ast.Try) and node in up.body:
                    guard = up
                    break
                node = up
            assert guard is not None, "%s:%d: think() outside any try" % (p.name, call.lineno)
            for h in guard.handlers:
                types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
                names = [t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "?") for t in types]
                assert names and set(names) <= family, "%s:%d catches %s around think()" % (p.name, h.lineno, names)
    assert seen >= 8, "the eight organs' think() calls were found (%d)" % seen


def test_an_unforeseen_exception_before_the_call_is_built_is_typed_by_the_boundary(w, monkeypatch):
    """think()'s own boundary: an exception raised BEFORE _think_with (here: the record reader) is typed too -- the one
    member only the outer boundary catches (the call path's own catch-alls never see it)."""
    _claude(w)

    def boom(*a, **k):
        raise KeyError("a record reader nobody foresaw")
    monkeypatch.setattr(thinker, "read_record", boom)
    n = len(thinker.read_log())
    with pytest.raises(thinker.ThinkInternal) as ei:
        _think()
    assert ei.value.reason == "KeyError" and "nobody foresaw" in str(ei.value)
    _one_row(n, "internal")

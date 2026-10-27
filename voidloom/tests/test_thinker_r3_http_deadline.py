# -*- coding: utf-8 -*-
"""R2-04 (Round 2 reflection, carried into Round 3 G1): the HTTP exchange is bounded by the CALL's wall clock, headers and
error body included.

MEASURED (r2_refl/cap/cap.json, cap_errdrip.json): urllib's timeout is per socket OPERATION, so a server that sends its
status line and then one HEADER byte every 0.5 s -- or a 404 whose BODY drips the same way -- kept `think(timeout_s=4)`
alive for more than 60 s (the reviewer's guard killed it; at 2 B/s a 64 KB header line is ~9 h). _read_body bounded the
reply BODY already; the header phase and the error body were the class's two remaining members. The seam now runs the
whole exchange under one deadline (thinker._http over _http_once): past timeout_s + HTTP_JOIN_SLACK_S the caller gets a
typed ThinkTimeout. Each falsifier runs the seam in a CHILD with the test's own wall clock, so a regression fails the test
instead of hanging it.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
ORGANS = Path(thinker.__file__).resolve().parent
GUARD_S = 40

DRIVER = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import thinker
t0 = time.monotonic()
out = {}
try:
    r = thinker.think("a prompt", purpose="sleep_loop", timeout_s=float(sys.argv[2]))
    out.update(exc="", text=r.text)
except BaseException as e:
    out.update(exc=type(e).__name__, msg=str(e)[:400])
out["elapsed"] = round(time.monotonic() - t0, 2)
sys.stdout.write("RESULT " + json.dumps(out) + "\n")
'''

SERVER = r'''
import json, re, socket, sys, time, threading
MODE = sys.argv[1]
N = [0]
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 0)); s.listen(8)
sys.stdout.write("%d\n" % s.getsockname()[1]); sys.stdout.flush()
def read_request(c):
    data = b""
    while b"\r\n\r\n" not in data:
        data += c.recv(65536)
    head, body = data.split(b"\r\n\r\n", 1)
    n = int(re.search(rb"Content-Length: (\d+)", head, re.I).group(1))
    while len(body) < n:
        body += c.recv(65536)
    return json.loads(body or b"{}")
def answer(c, req):
    prompt = " ".join(str(m.get("content", "")) for m in req.get("messages", []))
    m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
    text = str(int(m.group(1)) + 1) if m else "fine"
    body = json.dumps({"model": "m", "message": {"role": "assistant", "content": text}, "done": True,
                       "prompt_eval_count": len(prompt.split()) + 36}).encode()
    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    c.close()
def serve(c):
    try:
        req = read_request(c)
        N[0] += 1
        if N[0] <= 2 or MODE == "ok":          # the record's proof (its two calls) is answered as a model would
            return answer(c, req)
        if MODE == "header_drip":
            c.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            while True:
                c.sendall(b"a"); time.sleep(0.5)
        if MODE == "error_drip":
            c.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: 100000\r\n\r\n")
            while True:
                c.sendall(b"e"); time.sleep(0.5)
    except OSError:
        pass
while True:
    c, _ = s.accept()
    threading.Thread(target=serve, args=(c,), daemon=True).start()
'''


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_r3_http_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _server(w, mode):
    s = w.root / "server.py"
    s.write_text(SERVER, encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-B", str(s), mode], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return p, "http://127.0.0.1:%d" % int(p.stdout.readline().decode().strip())


def _stop(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait(timeout=10)


def _drive(w, timeout_s):
    d = w.root / "driver.py"
    d.write_text(DRIVER, encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-B", str(d), str(ORGANS), str(timeout_s)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(timeout=GUARD_S)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return {"exc": "GUARD", "elapsed": GUARD_S}
    lines = [l for l in out.decode("utf-8", "replace").splitlines() if l.startswith("RESULT ")]
    assert lines, err.decode("utf-8", "replace")[-600:]
    return json.loads(lines[-1][len("RESULT "):])


def _record(w, url):
    rc = F.record(thinker, "local", "--endpoint", url, "--model", "m")
    return rc


@pytest.mark.parametrize("mode", ["header_drip", "error_drip"])
def test_a_dripping_header_or_error_body_cannot_outlast_the_wall_clock(w, mode):
    srv, url = _server(w, mode)
    try:
        _record(w, url)
        res = _drive(w, 4)
    finally:
        _stop(srv)
    assert res.get("exc") == "ThinkTimeout", res
    assert res["elapsed"] < 4 + 3, res              # timeout + HTTP_JOIN_SLACK_S + start-up, never the drip's 9 hours


def test_a_prompt_server_still_answers_through_the_deadline_thread(w):
    srv, url = _server(w, "ok")
    try:
        _record(w, url)
        res = _drive(w, 10)
    finally:
        _stop(srv)
    assert res.get("exc") == "" and res.get("text") == "fine", res


def test_a_typed_failure_inside_the_thread_comes_back_typed(w):
    srv, url = _server(w, "ok")
    try:
        _record(w, url)                          # proven while the server answers ...
    finally:
        _stop(srv)                               # ... then the server is gone: connection refused, inside the thread
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("a prompt", purpose="sleep_loop", timeout_s=5)
    assert ei.value.reason == "unreachable", repr(ei.value)

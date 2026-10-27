# -*- coding: utf-8 -*-
"""Round 2 wave 1 (model-choice unit), MC-R1-12 -- HOST SAFETY: the seam bounds the BYTES it holds, not only the seconds.

The seam read a child with communicate() and a server with read(): a program that writes fast filled memory long before
the timeout fired (a 200 MB writer took the caller from 22 MB to 621 MB; the reflector's `yes {prompt}` probe through the
seam coincided with a host out-of-memory, 2026-09-28). Every place the seam reads a program or a server now reads
INCREMENTALLY, holds at most OUTPUT_CAP_BYTES, kills the process group (or closes the connection) past it, and raises a
typed ThinkOverflow whose message names the cap. The class, swept in organs/thinker.py: the organ path's child launch
(_run_cli), the HTTP body and the HTTP error body (_http), the systemd-run proof (_systemd_run_probe), and
`codex login status` (_login_codex) -- the structure test below pins that no unbounded read is left.

EVERY FALSIFIER HERE RUNS THE SEAM IN A CHILD UNDER AN ADDRESS-SPACE CAP (resource.setrlimit RLIMIT_AS in the child),
so a regression FAILS THE TEST -- it can never take the host. The flooding programs are the child's children and
inherit the cap; the flooding HTTP server is a separate process that holds nothing.
"""
import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
ORGANS = Path(thinker.__file__).resolve().parent
CAP = getattr(thinker, "OUTPUT_CAP_BYTES", 16 * 1024 * 1024)   # the frozen seam has no cap: the tests still RUN there
CHILD_AS = 1024 * 1024 * 1024        # the child's address-space cap: 64x the output cap, far below the host's memory
GUARD_S = 90                         # the test's own wall clock on the child (the frozen seam can read forever)
RSS_BOUND_KB = 2 * CAP // 1024       # "bounded near the cap": the child's peak RSS may grow by at most 2x the cap

DRIVER = r'''
import json, os, resource, sys, time
AS = int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_AS, (AS, AS))
sys.path.insert(0, sys.argv[1])
import thinker
def hwm():
    """This process image's peak RSS in KB (VmHWM, reset at exec -- ru_maxrss keeps the forking parent's peak)."""
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
spec = json.loads(sys.argv[3])
out = {"base_kb": hwm()}
t0 = time.monotonic()
try:
    if spec["call"] == "think":
        r = thinker.think(spec.get("prompt", "a prompt"), purpose="sleep_loop", timeout_s=spec["timeout"])
        out.update(exc="", text_len=len(r.text))
    elif spec["call"] == "run":
        env = thinker.run(spec["argv"])
        probe = ((env.get("data") or {}).get("probe") or {})
        out.update(exc="", code=env.get("code"), rc=env.get("_exit_code"), error=str(probe.get("error", ""))[:600],
                   reason=probe.get("reason", ""))
    elif spec["call"] == "capture":
        rc, o, e, why = thinker._capture(spec["argv"], timeout_s=spec["timeout"])
        out.update(exc="", why=why, held=len(o) + len(e))
except BaseException as e:            # the test must SEE whatever escaped, typed or not
    out.update(exc=type(e).__name__, mro=[c.__name__ for c in type(e).__mro__], msg=str(e)[:600])
out["peak_kb"] = hwm()
out["elapsed"] = round(time.monotonic() - t0, 2)
sys.stdout.write("RESULT " + json.dumps(out) + "\n")
sys.stdout.flush()
'''

SERVER = r'''
import json, re, socket, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
MODE, ANSWER_FIRST = sys.argv[1], int(sys.argv[2])
class H(BaseHTTPRequestHandler):
    n = 0
    def log_message(self, *a):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        H.n += 1
        if H.n <= ANSWER_FIRST:            # the probe's two calls are answered as a model would (N+1, tokens counted)
            prompt = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
            m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
            text = str(int(m.group(1)) + 1) if m else "done"
            data = json.dumps({"model": body.get("model"), "message": {"role": "assistant", "content": text},
                               "done": True, "prompt_eval_count": len(prompt.split()) + 36}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if MODE == "error-flood":
            self.send_response(404)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if MODE == "flood200":
            self.send_header("Content-Length", str(200 * 1024 * 1024))
        self.end_headers()
        blk, sent = b"x" * (1024 * 1024), 0
        try:
            if MODE in ("drip", "drip-then-stall"):
                self.wfile.write(b'{"model": "m", "message": {"role": "assistant", "content": "')
                t0 = time.time()
                while True:
                    self.wfile.write(b"y")
                    self.wfile.flush()
                    time.sleep(0.5)
                    if MODE == "drip-then-stall" and time.time() - t0 > 3:
                        time.sleep(60)            # silence: ONE read now blocks past the call's remaining time
            while MODE != "flood200" or sent < 200 * 1024 * 1024:
                self.wfile.write(blk)
                sent += len(blk)
        except OSError:
            pass
srv = HTTPServer(("127.0.0.1", 0), H)
sys.stdout.write("%d\n" % srv.server_address[1])
sys.stdout.flush()
srv.serve_forever()
'''


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_cap_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _drive(w, spec):
    """Run the DRIVER in a child under RLIMIT_AS, in its own session, with the test's own wall clock. The child's
    result dict; {'exc': 'GUARD'} when the test had to kill it (the frozen seam reading a drip forever)."""
    d = w.root / "driver.py"
    d.write_text(DRIVER, encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-B", str(d), str(ORGANS), str(CHILD_AS), json.dumps(spec)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=GUARD_S)      # the DRIVER prints one line; its own output is tiny
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return {"exc": "GUARD", "msg": "the test killed the child after %d s" % GUARD_S, "peak_kb": 0, "base_kb": 0,
                "elapsed": GUARD_S}
    lines = [l for l in out.decode("utf-8", "replace").splitlines() if l.startswith("RESULT ")]
    assert lines, "the child died without a result (rc %s): %s" % (p.returncode, err.decode("utf-8", "replace")[-800:])
    res = json.loads(lines[-1][len("RESULT "):])
    print("CAPREC %s %s" % (os.environ.get("PYTEST_CURRENT_TEST", "?").split(" ")[0], json.dumps(res)))   # -s shows it
    return res


def _server(w, mode, answer_first=2):
    s = w.root / "server.py"
    s.write_text(SERVER, encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-B", str(s), mode, str(answer_first)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    port = int(p.stdout.readline().decode().strip())
    return p, "http://127.0.0.1:%d" % port


def _stop(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait(timeout=10)


def _assert_overflow(res, where):
    assert res.get("exc") == "ThinkOverflow", "%s: expected a typed ThinkOverflow, got %r" % (where, res)
    assert "ThinkMalformed" in res["mro"] and "ThinkError" in res["mro"], res
    assert str(CAP) in res["msg"] and "cap" in res["msg"], "the message must name the cap: %r" % res["msg"]
    grew = res["peak_kb"] - res["base_kb"]
    assert grew <= RSS_BOUND_KB, "%s: the child's peak RSS grew by %d KB (bound %d KB, cap %d bytes)" % (
        where, grew, RSS_BOUND_KB, CAP)


def _no_survivor(logfile):
    rows = F.read_log(logfile)
    starts = [r for r in rows if r.get("pgid")]
    assert starts, "the fake never started"
    time.sleep(0.2)
    for r in starts:
        with pytest.raises(ProcessLookupError):
            os.killpg(int(r["pgid"]), 0)       # the whole process group is gone


# ------------------------------------------------------------------------------------ the organ path: _run_cli
def test_a_yes_class_program_is_a_typed_overflow_with_rss_bounded(w):
    fake = F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")
    F.set_behaviour(fake, mode="yes", log=str(w.log))
    res = _drive(w, {"call": "think", "timeout": 30})
    _assert_overflow(res, "yes-class")
    assert res["elapsed"] < 30, "stopped by the CAP, not by the clock: %r" % res
    _no_survivor(w.log)
    row = thinker.read_log()[-1]
    assert (row["outcome"], row["reason"]) == ("malformed", "overflow"), row


def test_a_200_mb_writer_is_the_same(w):
    fake = F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")
    F.set_behaviour(fake, mode="flood", bytes=200 * 1024 * 1024, log=str(w.log))
    res = _drive(w, {"call": "think", "timeout": 60})
    _assert_overflow(res, "200 MB writer")
    _no_survivor(w.log)


def test_a_reply_just_under_the_cap_is_still_read(w):
    """The bound is not a new failure for a legitimate reply: an answer of cap/2 bytes on stdout is read whole (the
    claude double's JSON envelope carries it; the organ gets it back)."""
    fake = F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    F.record(thinker, "claude")
    F.set_behaviour(fake, reply="z" * (CAP // 2))
    res = _drive(w, {"call": "think", "timeout": 60})
    assert res.get("exc") == "" and res["text_len"] == CAP // 2, res


# ------------------------------------------------------------------------------------ the HTTP read: _http
@pytest.mark.parametrize("mode", ["flood", "flood200", "error-flood"])
def test_a_flooding_server_is_a_typed_overflow_with_rss_bounded(w, mode):
    srv, url = _server(w, mode)
    try:
        F.record(thinker, "local", "--endpoint", url, "--model", "m")
        res = _drive(w, {"call": "think", "timeout": 30})
    finally:
        _stop(srv)
    if mode == "error-flood":
        # a 404 whose body never ends: only HTTP_ERROR_BODY_BYTES are read, the typed 404 comes back
        assert res.get("exc") == "ThinkUnavailable" and "404" in res["msg"], res
        assert res["peak_kb"] - res["base_kb"] <= RSS_BOUND_KB, res
        return
    _assert_overflow(res, "server %s" % mode)


@pytest.mark.parametrize("mode,slack", [("drip", 3.0), ("drip-then-stall", 1.0)])
def test_a_server_that_drips_cannot_outlast_the_wall_clock(w, mode, slack):
    """urllib's timeout is per socket operation: a server that sends one byte every half second never trips it. The seam
    reads against the CALL's wall clock (found while bounding the read; the time half of the same class) in two ways,
    each with its own case: the deadline is checked between chunks (drip: a byte every 0.5 s), and each read's socket
    timeout is the time LEFT (drip-then-stall: 3 s of bytes, then silence -- urllib's own 4 s would end the call at ~7 s;
    the time left ends it at ~4 s)."""
    srv, url = _server(w, mode)
    try:
        F.record(thinker, "local", "--endpoint", url, "--model", "m")
        res = _drive(w, {"call": "think", "timeout": 4})
    finally:
        _stop(srv)
    assert res.get("exc") == "ThinkTimeout" and res["elapsed"] < 4 + slack, res


# ------------------------------------------------------------------------------------ the proof: _systemd_run_probe
def test_the_proof_through_systemd_run_is_bounded(w):
    F.make_fake(w.bin, "claude", kind="claude")
    F.save_cred(w, "claude")
    shim = w.bin / "systemd-run"                  # the manager's runner, flooding what the proof reads
    shim.write_text("#!/bin/sh\nexec /usr/bin/yes probe-flood\n", encoding="utf-8")
    shim.chmod(0o755)
    res = _drive(w, {"call": "run", "argv": ["set", "claude", "--probe-env", "systemd", "--answered-by", "test",
                                             "--timeout", "5"]})
    assert res.get("exc") == "" and res["code"] == "thinker_probe_failed" and res["rc"] == 1, res
    assert str(CAP) in res["error"] and res["reason"] == "overflow", res
    assert res["peak_kb"] - res["base_kb"] <= RSS_BOUND_KB, res
    assert not thinker.record_path().exists(), "nothing may be recorded"


# ------------------------------------------------------------------------------------ the reader itself
def test_the_capped_reader_stops_a_yes_program(w):
    res = _drive(w, {"call": "capture", "argv": ["/usr/bin/yes"], "timeout": 20})
    assert res.get("exc") == "" and res["why"] == "overflow" and res["held"] == 0, res
    assert res["peak_kb"] - res["base_kb"] <= RSS_BOUND_KB and res["elapsed"] < 20, res


# ------------------------------------------------------------------------------------ the class, by structure
def test_no_unbounded_read_is_left_in_the_seam():
    """AST over organs/thinker.py: no communicate(), no capture_output / check_output, no zero-argument .read(), and a
    PIPE only inside _capture -- every read of a program or a server goes through the capped readers."""
    tree = ast.parse((ORGANS / "thinker.py").read_text(encoding="utf-8"))
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]:
        name = getattr(fn, "name", "<module>")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                attr = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if attr in ("communicate", "check_output"):
                    bad.append("%s: %s()" % (name, attr))
                if attr == "read" and not node.args and not node.keywords:
                    bad.append("%s: .read() with no size" % name)
                for kw in node.keywords:
                    if kw.arg == "capture_output":
                        bad.append("%s: capture_output" % name)
            if isinstance(node, ast.Attribute) and node.attr == "PIPE" and name not in ("_capture", "<module>"):
                bad.append("%s: subprocess.PIPE outside _capture" % name)
    assert not sorted(set(bad)), sorted(set(bad))

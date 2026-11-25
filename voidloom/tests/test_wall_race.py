# -*- coding: utf-8 -*-
"""R6: 8 threads x 12 lookups over two databases in one process. Each answer is its own database's, parsed, and nothing
leaks to the real stdout. Run in a child so a torn global cannot take the suite's own stdout with it."""
import json
import os
import subprocess
import textwrap
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data, unknown_flag

CHILD = textwrap.dedent('''
import contextlib, json, os, sys, threading, time
from pathlib import Path
sys.path[:0] = [os.environ["ROOT"], os.environ["ROOT"] + "/organs", os.environ["ROOT"] + "/voidloom/tests"]
import _wall_brain as wb
from voidloom import _facade, _batch
P = json.loads(os.environ["PATHS"]); mode = os.environ["MODE"]; kind = os.environ["KIND"]
Q = ["gradle cache daemon", "memory pressure build lane", "daemon kills lane", "cache pressure variant"]
res = []
def one(n, j):
    k = "AB"[n % 2]; q = Q[(n + j) % len(Q)]
    try:
        if kind == "batch":
            r = _batch.recall_batch([q], top_k=4, db_path=P[k], leads=0)[0]
        else:
            r = _facade.recall(q, top_k=4, db_path=P[k], leads=0)
        if r.get("status") != "ok":
            return res.append((0, 0, "status:" + str(r.get("message"))[:60]))
        rows = r.get("results", [])
        res.append((len(rows), sum(not x["content"].startswith("brain" + k) for x in rows), None))
    except BaseException as e:
        res.append((0, 0, type(e).__name__ + ":" + str(e)[:60]))
def go():
    if mode == "serial":
        for n in range(8):
            for j in range(12): one(n, j)
        return
    B = threading.Barrier(8)
    def w(n):
        B.wait()
        for j in range(12): one(n, j)
    T = [threading.Thread(target=w, args=(n,)) for n in range(8)]
    [t.start() for t in T]; [t.join() for t in T]
real = sys.stdout
sys.setswitchinterval(1e-5)
if os.environ["ENGINE"] == "full":
    with wb.engine(P["A"]): go()
else:
    go()
out = dict(lookups=len(res), nonempty=sum(r[0] > 0 for r in res), wrong_rows=sum(r[1] for r in res),
           errors=sum(r[2] is not None for r in res), kinds=sorted({r[2].split(":")[0] for r in res if r[2]})[:6], stdout_is_real=sys.stdout is real)
open(os.environ["OUT"], "w").write(json.dumps(out))
''')


class Race(Case):
    def run_child(self, engine, mode, kind="recall"):
        paths = {}
        for k in "AB":
            b = Brain(self.tmp / k)
            for i in range(12):
                b.add("brain%s gradle cache daemon memory pressure kills the build lane variant %s" % (k, "abcdefghijkl"[i] * 4))
            b.c.close()
            paths[k] = b.path
        out = self.tmp / "out.json"
        env = wb.child_env(paths["A"], ROOT=str(wb.ROOT), PATHS=json.dumps(paths), MODE=mode, KIND=kind, ENGINE=engine, OUT=str(out),
                           VOIDLOOM_TIER0_ONLY="1" if engine == "lite" else "0", OMP_NUM_THREADS="1")
        p = subprocess.run([sys.executable, "-B", "-c", CHILD], cwd=str(self.tmp), env=env, capture_output=True, text=True, timeout=240)
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        r = json.loads(out.read_text())
        r["leaked_stdout_bytes"] = len(p.stdout)
        return r

    CLEAN = dict(lookups=96, nonempty=96, wrong_rows=0, errors=0, kinds=[], stdout_is_real=True, leaked_stdout_bytes=0)

    def test_control_serial_lite(self):
        self.assertEqual(self.run_child("lite", "serial"), self.CLEAN)

    def test_control_serial_full(self):
        self.assertEqual(self.run_child("full", "serial"), self.CLEAN)

    def test_red_threads_over_two_databases_lite(self):
        self.assertEqual(self.run_child("lite", "threads"), self.CLEAN)

    def test_red_threads_over_two_databases_full(self):
        self.assertEqual(self.run_child("full", "threads"), self.CLEAN)

    def test_red_threads_batch(self):
        self.assertEqual(self.run_child("lite", "threads", "batch"), self.CLEAN)

    def test_red_a_flag_of_one_recall_does_not_leak_into_the_next(self):
        import argparse, contextlib, io
        from unittest import mock
        b = Brain(self.tmp)
        b.add("gradle cache daemon row")
        with mock.patch.dict(os.environ):
            os.environ.pop("VOIDLOOM_NO_TEMPORAL_RERANK", None)
            with wb.engine(b.path) as cm, contextlib.redirect_stdout(io.StringIO()):
                from voidloom import _facade
                ns = _facade._recall_namespace("gradle cache daemon", 3)
                ns.no_temporal_rerank = True
                cm.cmd_recall(ns)
                self.assertIsNone(os.environ.get("VOIDLOOM_NO_TEMPORAL_RERANK"), "--no-temporal-rerank of one call binds the process")


if __name__ == "__main__":
    unittest.main()

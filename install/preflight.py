#!/usr/bin/env python3
"""preflight.py -- `voidloom preflight`: prove THIS machine can run the brain BEFORE the package is cut for it.

Handoff round 1, lane L4, decision D1 (Drew, 2026-10-14: "that seems smart"). The friend runs this once, from the
unpacked package, before any install (about ten minutes: a download and a few model runs), and sends back the one file
it writes, preflight.json. Nothing is installed into their home: the virtual environment, pip's cache and the model
downloads all live in ONE scratch folder that is deleted at the end (--keep keeps it).

What it proves, by execution, on the friend's own silicon:

  facts    the machine: OS, chip, macOS version, RAM as the OS presents it, the python, and whether that python can
           load SQLite extensions (Apple's own python cannot; the documented route is Miniforge)
  plan     install/accelerator_plan.py: the backend the installer would choose, its torch channel, its label, and the
           reranker gate (ON only with an accelerator AND >= 15.0 GiB)
  venv     a throwaway venv in the scratch folder; torch from the plan's channel, then install/requirements.txt
  P2       sqlite-vec loads and answers a distance query
  P6-cpu   the package's own encoder on the CPU agrees with the golden vectors (the CPU route works here)
  P6       the same on the device the organs choose (MPS on Apple Silicon): THE question the package cannot answer
           without this machine
  P7       when the gate is ON: the reranker ranks relevant above irrelevant on that device
  timing   five query encodings on that device, timed; p50 derived

Verdict: GREEN when every step passed; RED names the first failure. Pre-registered (SYNTHESIS §5, rule 2): RED on the
accelerator -> the package ships this machine on the CPU, labelled, with the reranker off.

    python3 install/preflight.py [--out preflight.json] [--python PATH] [--tier nomic|qwen] [--keep]
    python3 install/preflight.py --use-python PATH ...   (no venv, no pip: prove an interpreter that already holds the
                                                          stack -- the reference host's own route; nothing downloaded)
rc 0 GREEN, 1 RED, 2 the preflight itself could not run (said). Stdlib only.
"""
from __future__ import print_function

import argparse
import datetime
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ORGANS = os.path.join(PKG, "organs")
PROBE = os.path.join(HERE, "tier1_probe.py")
PLAN = os.path.join(HERE, "accelerator_plan.py")
REQS = os.path.join(HERE, "requirements.txt")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _say(msg):
    print("[preflight] %s" % msg, flush=True)


def _run(cmd, env=None, timeout=1800):
    t = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env,
                           timeout=timeout)
        return p.returncode, p.stdout, p.stderr, round(time.time() - t, 1)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout, round(time.time() - t, 1)
    except OSError as e:
        return 127, "", str(e), round(time.time() - t, 1)


def _tail(s, n=400):
    s = (s or "").strip()
    return s if len(s) <= n else "..." + s[-n:]


def machine_facts(py):
    f = {"os": platform.system(), "arch": platform.machine(), "mac_ver": platform.mac_ver()[0] or None,
         "preflight_python": sys.executable, "candidate_python": py}
    sys.path.insert(0, HERE)
    import accelerator_plan as ap
    rb = ap.ram_bytes()
    f["ram_bytes"] = rb
    f["ram_gib"] = round(rb / ap.GIB, 1) if rb else None
    rc, out, err, _ = _run([py, "-c", "import platform,sqlite3; c=sqlite3.connect(':memory:'); "
                                      "ok=hasattr(c,'enable_load_extension'); print(platform.python_version(), ok)"])
    if rc == 0 and out.split():
        f["candidate_python_version"] = out.split()[0]
        f["candidate_loads_sqlite_extensions"] = out.split()[-1] == "True"
    else:
        f["candidate_python_error"] = _tail(err)
    return f


def plan_for(py_version):
    rc, out, err, _ = _run([sys.executable, "-B", PLAN, "plan", "--organs", ORGANS, "--request", "auto",
                            "--python", py_version or ""])
    try:
        return json.loads(out)
    except ValueError:
        return {"refusal": "the accelerator plan could not be read (rc %s): %s" % (rc, _tail(err))}


def probe(py, steps, db, tier, reranker, env, force_cpu=False):
    cmd = [py, "-B", PROBE, "run", "--steps", steps, "--db", db, "--organs", ORGANS, "--tier", tier,
           "--reranker", "1" if reranker else "0", "--timeout", "1500"]
    if force_cpu:
        cmd.append("--force-cpu")
    rc, out, err, secs = _run(cmd, env=env)
    try:
        rep = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        rep = {"ok": False, "steps": [], "error": "the probe printed no report (rc %s): %s" % (rc, _tail(err))}
    rep["seconds"] = secs
    return rep


def timing(py, tier, env):
    code = ("import os,sys,time,statistics; sys.path.insert(0, %r); import claude_memory as cm; "
            "cm._encode('warm up', prefix='search_query'); ts=[]\n"
            "for q in ('memory','how the installer works','sleep consolidation','semantic recall','what the brain knows'):\n"
            "    t=time.perf_counter(); cm._encode(q, prefix='search_query'); ts.append(round(time.perf_counter()-t,4))\n"
            "print(statistics.median(ts), ts)") % ORGANS
    e = dict(env, WIZARD_EMBED_MODEL=tier)
    rc, out, err, secs = _run([py, "-B", "-c", code], env=e, timeout=900)
    if rc != 0 or not out.strip():
        return {"ok": False, "error": _tail(err)}
    p50, rest = out.strip().splitlines()[-1].split(" ", 1)
    return {"ok": True, "encode_p50_s": float(p50), "encode_times_s": rest}


def main(argv=None):
    ap_ = argparse.ArgumentParser(description="Prove this machine can run the brain, before the package is cut for it.")
    ap_.add_argument("--out", default="preflight.json")
    ap_.add_argument("--python", default=sys.executable, help="the python a venv is built from (default: this one)")
    ap_.add_argument("--use-python", default="", help="prove THIS interpreter as it is: no venv, no pip, no download")
    ap_.add_argument("--tier", default="nomic", choices=("nomic", "qwen"))
    ap_.add_argument("--workdir", default="", help="where the scratch folder is made (default: the system temp dir)")
    ap_.add_argument("--keep", action="store_true", help="keep the scratch folder (default: deleted at the end)")
    a = ap_.parse_args(argv)

    report = {"schema": "voidloom-preflight/1", "started_at": _now(), "package": PKG, "tier": a.tier, "steps": [],
              "verdict": None, "first_failure": None}
    cand = a.use_python or a.python
    report["machine"] = machine_facts(cand)
    m = report["machine"]
    _say("this machine: %s %s, macOS %s, %s GiB RAM; python %s (%s)" % (
        m["os"], m["arch"], m.get("mac_ver") or "-", m.get("ram_gib"), m.get("candidate_python_version"), cand))
    report["plan"] = plan_for(m.get("candidate_python_version"))
    pl = report["plan"]
    if pl.get("sentence"):
        _say(pl["sentence"])

    def step(name, ok, **facts):
        report["steps"].append(dict(facts, step=name, ok=bool(ok)))
        if not ok and report["first_failure"] is None:
            report["first_failure"] = "%s: %s" % (name, facts.get("error") or facts.get("reason") or "failed")
        _say("%s %s %s" % (name, "ok" if ok else "FAILED", facts.get("error") or ""))

    if m.get("candidate_loads_sqlite_extensions") is False:
        step("sqlite-extensions", False, error="%s cannot load SQLite extensions (Apple's own python cannot): install "
             "Miniforge and pass its python with --python (docs/ONBOARDING_GUIDE.md, 'On a Mac')" % cand)
    if pl.get("refusal"):
        step("plan", False, error=pl["refusal"])

    base = a.workdir or None
    scratch = tempfile.mkdtemp(prefix="voidloom-preflight-", dir=base)
    report["scratch"] = scratch
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["WIZARD_LIBRARY_DB"] = os.path.join(scratch, "no-brain.db")       # never the owner's brain
    env["WIZARD_LIBRARY_HOME"] = scratch
    rc_final = 2
    try:
        py = a.use_python
        if not report["first_failure"] and not py:
            env["PIP_CACHE_DIR"] = os.path.join(scratch, "pip-cache")       # nothing lands in the home's caches
            env["HF_HOME"] = os.path.join(scratch, "hf")
            venv = os.path.join(scratch, "venv")
            rc, _o, err, secs = _run([a.python, "-m", "venv", venv])
            vpy = os.path.join(venv, "bin", "python3")
            step("venv", rc == 0, seconds=secs, error=None if rc == 0 else _tail(err))
            if rc == 0:
                # an old bundled pip cannot resolve torch's index (pip 22.3.1: "No matching distribution found for
                # flit_core"); install.sh upgrades pip first, so preflight must too or it reports RED on a machine
                # the installer handles (round 2 reflection N3)
                rc, _o, err, secs = _run([vpy, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], env=env, timeout=600)
                step("pip", rc == 0, seconds=secs, error=None if rc == 0 else _tail(err))
            if rc == 0:
                ch = pl.get("channel") or {}
                cmd = [vpy, "-m", "pip", "install", "--quiet"] + (["--index-url", ch["index"]] if ch.get("index") else []) \
                    + list(ch.get("spec") or ["torch"])
                rc, _o, err, secs = _run(cmd, env=env, timeout=2400)
                step("torch", rc == 0, seconds=secs, channel=ch.get("index") or "PyPI", spec=ch.get("spec"),
                     error=None if rc == 0 else _tail(err))
                if rc == 0:
                    rc, _o, err, secs = _run([vpy, "-m", "pip", "install", "--quiet", "-r", REQS], env=env, timeout=2400)
                    step("stack", rc == 0, seconds=secs, error=None if rc == 0 else _tail(err))
                if rc == 0:
                    py = vpy
                    # Warm the cache THROUGH THE ORGANS' OWN LOADER, online, exactly as recall will load it: the
                    # pinned revision AND every repository its trust_remote_code pulls (nomic-embed-text-v1.5's
                    # model code lives in nomic-ai/nomic-bert-2048). A bare snapshot_download of the model repo
                    # cached neither, and the loader then went local-only because the repo looked cached: P6
                    # failed offline on every fresh CPU machine (friend-package smoke, 2026-10-16). On the CPU:
                    # this step only downloads.
                    code = ("import sys; sys.path.insert(0, %r); import claude_memory as cm; "
                            "cm._encode('warm up', prefix='search_query')%s" % (
                                ORGANS, "; from huggingface_hub import snapshot_download as s; "
                                        "s('Qwen/Qwen3-Reranker-0.6B')"
                                if (pl.get("reranker") or {}).get("enabled") else ""))
                    em = dict(env, WIZARD_EMBED_MODEL=a.tier, WIZARD_ACCELERATOR="cpu", CUDA_VISIBLE_DEVICES="",
                              PYTORCH_NVML_BASED_CUDA_CHECK="1")
                    em.pop("HF_HUB_OFFLINE", None)
                    em.pop("TRANSFORMERS_OFFLINE", None)
                    rc, _o, err, secs = _run([vpy, "-B", "-c", code], env=em, timeout=2400)
                    step("models", rc == 0, seconds=secs, error=None if rc == 0 else _tail(err))
                    if rc != 0:
                        py = ""
        if py and not report["first_failure"]:
            gate = bool((pl.get("reranker") or {}).get("enabled"))
            # P2 reads a DB's declared width; with no brain it is proven on the tier's own width instead
            r2 = _p2_free(py, a.tier, env)
            step("P2", r2["ok"], **{k: v for k, v in r2.items() if k != "ok"})
            rc_cpu = probe(py, "P6", "", a.tier, False, env, force_cpu=True)
            s6c = (rc_cpu.get("steps") or [{}])[0]
            step("P6-cpu", rc_cpu.get("ok"), facts=s6c.get("facts"), error=s6c.get("error") or rc_cpu.get("error"))
            if pl.get("backend") and pl["backend"] != "cpu":
                rd = probe(py, "P6,P7" if gate else "P6", "", a.tier, gate, env)
                for s in rd.get("steps") or [{"step": "P6", "ok": False, "error": rd.get("error")}]:
                    step(s["step"] + "-" + pl["backend"], s.get("ok"), facts=s.get("facts"), error=s.get("error"))
            t = timing(py, a.tier, env)
            step("timing", t.get("ok"), **{k: v for k, v in t.items() if k != "ok"})
        report["verdict"] = "GREEN" if report["first_failure"] is None and report["steps"] else "RED"
        rc_final = 0 if report["verdict"] == "GREEN" else 1
    finally:
        report["finished_at"] = _now()
        b = pl.get("backend")
        acc_ok = [s["ok"] for s in report["steps"] if s["step"].startswith("P6-") and s["step"] != "P6-cpu"]
        if b and b != "cpu":
            report["accelerator_verdict"] = ("%s PROVEN on this machine (the label may move to PROVEN-BY-PROXY once "
                                             "this file is read)" % b) if acc_ok and all(acc_ok) else (
                "%s NOT proven here: the package ships this machine on the CPU, labelled, reranker off" % b)
        if a.keep:
            report["scratch_kept"] = True
        else:
            shutil.rmtree(scratch, ignore_errors=True)
            report["scratch_removed"] = not os.path.exists(scratch)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str)
            fh.write("\n")
        _say("verdict: %s%s" % (report["verdict"], (" -- " + report["first_failure"]) if report["first_failure"] else ""))
        if report.get("accelerator_verdict"):
            _say(report["accelerator_verdict"])
        _say("wrote %s -- send this one file back." % os.path.abspath(a.out))
    return rc_final


def _p2_free(py, tier, env):
    """P2 without a brain: sqlite-vec loads and answers a distance query at the tier's own width."""
    width = {"nomic": 768, "qwen": 1024}[tier]
    code = ("import sqlite3, sqlite_vec; c=sqlite3.connect(':memory:'); c.enable_load_extension(True); sqlite_vec.load(c);"
            "v1=[0.0]*%d; v2=[0.0]*%d; v1[0]=1.0; v2[1]=1.0;"
            "print(c.execute('SELECT vec_distance_cosine(?,?)',(sqlite_vec.serialize_float32(v1),"
            "sqlite_vec.serialize_float32(v2))).fetchone()[0], c.execute('select vec_version()').fetchone()[0])") % (width, width)
    rc, out, err, secs = _run([py, "-c", code], env=env)
    if rc != 0:
        return {"ok": False, "error": _tail(err)}
    d, ver = out.split()
    ok = abs(float(d) - 1.0) < 1e-6
    return {"ok": ok, "dim": width, "orthogonal_cosine_distance": float(d), "vec_version": ver,
            "error": None if ok else "distance %s, not 1.0" % d}


if __name__ == "__main__":
    sys.exit(main())

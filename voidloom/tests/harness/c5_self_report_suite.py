#!/usr/bin/env python3
"""C5 — a verdict computed from something other than the row it reports.

Each check drives a SHIPPED verb and compares the announcement in the envelope
against the row printed beside it.

    usage: c5_self_report_suite.py <package_dir> <scratch_root> [--only NAME]
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, Dict, List

if len(sys.argv) < 3:
    sys.exit("usage: c5_self_report_suite.py <package_dir> <scratch_root> [--only NAME]")
PKG = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)
# D8-F7: C5_SOURCE_BRAIN was the only env escape in the harness, and its DEFAULT
# was still one machine's absolute path. The escape stays first; the fallback is
# now resolved from this file's own location, so the suite has a brain to read on
# any host rather than silently reporting on a file that is not there.
_C5_ROOT = pathlib.Path(__file__).resolve().parents[3]
BRAIN = os.environ.get("C5_SOURCE_BRAIN") or os.environ.get("WIZARD_LIBRARY_DB") \
    or str(_C5_ROOT / "claude_memory.db")
RESULTS: List[Dict[str, Any]] = []


def note(name: str, held: bool, detail: Any) -> None:
    if ONLY and ONLY not in name:
        return
    RESULTS.append({"name": name, "held": bool(held), "detail": detail})
    print(f"  {name:<56s} {'HELD' if held else 'VIOLATED':<9s} {json.dumps(detail, default=str)[:240]}")


def box(tag: str) -> str:
    p = os.path.join(ROOT, tag)
    shutil.rmtree(p, ignore_errors=True)
    os.makedirs(p)
    return p


def vl(argv: List[str], env_extra: Dict[str, str] | None = None, cwd: str | None = None):
    env = {**os.environ, "PYTHONPATH": PKG}
    env.pop("WIZARD_LIBRARY_DB", None)
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, "-m", "voidloom", *argv], capture_output=True,
                       text=True, env=env, cwd=cwd or PKG, timeout=900)
    try:
        d = json.loads(p.stdout)
    except Exception:
        d = {"status": "UNPARSEABLE", "raw": (p.stdout or "")[-200:], "err": (p.stderr or "")[-200:]}
    return p.returncode, d


# ===========================================================================
# C5.1 — doctor's verdict READS THE LADDER IT PRINTS.
#   `healthy` was guard AND db_present AND engine_importable, printed beside a
#   ladder reading FIRED: INSTALLED_NEVER_FIRED / OBEYED: NEVER.
#   Drew's ruling: DESSA's split -- brain_healthy (the machine is sound) and
#   promise_healthy (the promise is kept) -- never one flag meaning "the
#   database opened".
# ===========================================================================
h = box("c5_1_doctor")
os.makedirs(os.path.join(h, ".claude")); os.makedirs(os.path.join(h, ".codex"))
db = os.path.join(h, "brain.db")
shutil.copy2(BRAIN, db)
henv = {"HOME": h, "CLAUDE_CONFIG_DIR": os.path.join(h, ".claude"),
        "CODEX_HOME": os.path.join(h, ".codex"),
        "VOIDLOOM_INSTALL_RECEIPT": os.path.join(h, "receipt.json"),
        "VOIDLOOM_HEARTBEAT_DIR": os.path.join(h, "hb")}
subprocess.run([sys.executable, os.path.join(PKG, "organs", "voidloom_hooks_status.py"),
                "--install", "--platform", "claude", "--yes", "--json"],
               capture_output=True, text=True, env={**os.environ, **henv}, timeout=300)
rc1, d1 = vl(["doctor", "--db", db, "--json"], henv)
dat1 = d1.get("data") or {}
ladders = dat1.get("hook_ladders") or {}
never_fired = any("INSTALLED_NEVER_FIRED" in str(v) or "OBEYED: NEVER" in str(v)
                  for v in ladders.values())
note("C5_1_doctor_splits_brain_health_from_promise_health",
     ("brain_healthy" in dat1 and "promise_healthy" in dat1
      and dat1.get("brain_healthy") is True
      and (dat1.get("promise_healthy") is False if never_fired else True)),
     {"brain_healthy": dat1.get("brain_healthy"),
      "promise_healthy": dat1.get("promise_healthy"),
      "legacy_healthy": dat1.get("healthy"),
      "ladder_says_never_fired": never_fired,
      "hook_states": dat1.get("hook_states")})

# ===========================================================================
# C5.2 — A BATCH THAT LOST ITEMS DOES NOT EXIT 0.
# ===========================================================================
h = box("c5_2_batch")
db = os.path.join(h, "brain.db")
shutil.copy2(BRAIN, db)
rc_s, d_s = vl(["batch-store", "--db", db, "--json", "--items",
                '[{"content":"c5 valid probe","category":"insight"},'
                '{"content":"c5 bad","category":"NOT_A_REAL_CATEGORY"}]'])
rc_r, d_r = vl(["batch-recall", "--db", db, "--json", "--top-k", "1",
                "--items", '["a real query", ""]'])
ds, dr = d_s.get("data") or {}, d_r.get("data") or {}
note("C5_2_a_batch_with_failures_does_not_report_ok_at_exit_0",
     ds.get("failed") and rc_s != 0 and d_s.get("status") != "ok"
     and (not dr.get("failed") or (rc_r != 0 and d_r.get("status") != "ok")),
     {"store": {"status": d_s.get("status"), "rc": rc_s,
                "succeeded": ds.get("succeeded"), "failed": ds.get("failed")},
      "recall": {"status": d_r.get("status"), "rc": rc_r,
                 "succeeded": dr.get("succeeded"), "failed": dr.get("failed")}})
# and the all-succeeded case must STILL be ok/0 -- a fix that fails every batch
# would pass the check above and destroy the verb.
rc_ok, d_ok = vl(["batch-store", "--db", db, "--json", "--items",
                  '[{"content":"c5 all good one","category":"insight"},'
                  '{"content":"c5 all good two","category":"insight"}]'])
dok = d_ok.get("data") or {}
note("C5_2b_a_batch_with_no_failures_is_still_ok_at_exit_0",
     rc_ok == 0 and d_ok.get("status") == "ok" and dok.get("failed") == 0,
     {"status": d_ok.get("status"), "rc": rc_ok,
      "succeeded": dok.get("succeeded"), "failed": dok.get("failed")})

# ===========================================================================
# C5.3 — THE READ-ONLY DEGRADE IS DECIDED WHERE IT IS DETECTABLE.
#   os.access(db, W_OK) is a FILE question. A writable DB in a read-only
#   DIRECTORY cannot be written either -- SQLite needs its -wal beside it.
# ===========================================================================
h = box("c5_3_rodir")
d_dir = os.path.join(h, "d"); os.makedirs(d_dir)
db = os.path.join(d_dir, "b.db")
shutil.copy2(BRAIN, db)
for suffix in ("-wal", "-shm"):
    try:
        os.unlink(db + suffix)
    except OSError:
        pass
os.chmod(d_dir, 0o555)
try:
    rc3, d3 = vl(["store", "--db", db, "--content", "c5 rodir probe",
                  "--category", "insight", "--json"])
finally:
    os.chmod(d_dir, 0o755)
d3d = d3.get("data") or {}
note("C5_3_an_unwritable_directory_is_a_typed_refusal_not_an_unhandled_error",
     d3.get("status") == "refused" and d3.get("code") == "readonly_db" and rc3 == 3,
     {"status": d3.get("status"), "code": d3.get("code"), "rc": rc3,
      "message": (d3d.get("message") or "")[:100]})

# ===========================================================================
# C5.4 — "CANNOT TELL" IS NOT "YES": an unmeasurable check must not exit 0.
# ===========================================================================
h = box("c5_4_canary")
for sub in (".claude", ".codex", "fx", "hb"):
    os.makedirs(os.path.join(h, sub), exist_ok=True)
cenv = {**os.environ, "HOME": h, "CLAUDE_CONFIG_DIR": os.path.join(h, ".claude"),
        "CODEX_HOME": os.path.join(h, ".codex"),
        "VOIDLOOM_HEARTBEAT_DIR": os.path.join(h, "hb"),
        "VOIDLOOM_SELFCHECK_FIXTURE_DIR": os.path.join(h, "fx"),
        "VOIDLOOM_CANARY_LEDGER": os.path.join(h, "ledger.json")}
cenv.pop("WIZARD_LIBRARY_DB", None)
p4 = subprocess.run([sys.executable, os.path.join(PKG, "organs", "voidloom_canary.py"),
                     "--self-check", "--json"], capture_output=True, text=True,
                    env=cenv, timeout=900)
try:
    d4 = json.loads(p4.stdout)["data"]
except Exception:
    d4 = {}
unmeasured = [c for c in (d4.get("checks") or []) if c.get("ok") is None]
note("C5_4_a_verdict_of_PASS_WITH_UNMEASURABLE_does_not_exit_0",
     (not unmeasured) or (p4.returncode == 4),
     {"verdict": d4.get("verdict"), "rc": p4.returncode,
      "unmeasured_checks": len(unmeasured), "failed": d4.get("failed")})

# ---------------------------------------------------------------------------
held = sum(1 for r in RESULTS if r["held"])
print(json.dumps({"package": PKG, "scratch_root": ROOT, "held": held,
                  "total": len(RESULTS),
                  "violated": [r["name"] for r in RESULTS if not r["held"]]}, indent=1))
# CRUCIBLE-3 D1-F4: `held == len(RESULTS)` is VACUOUSLY TRUE at len(RESULTS) == 0,
# so this suite printed "0/0 CHECKS HELD" and exited 0 having measured NOTHING --
# an acceptance gate passing over zero observations, in the instrument that grades
# the fix. `cli_install_suite.py:1620-1647` closed exactly this for itself; the six
# sibling suites kept the vacuous form. ZERO CHECKS IS A FAILURE: there is no
# observation to hold, and rc 2 (not 0, not 1) says "unmeasured", which is not the
# same as "measured and broken".
if not RESULTS:
    print("\n  ===> NO CHECKS EXECUTED — nothing was measured, so nothing held.\n")
    sys.exit(2)
print(f"\n  ===> {held}/{len(RESULTS)} CHECKS HELD\n")
sys.exit(0 if held == len(RESULTS) else 1)

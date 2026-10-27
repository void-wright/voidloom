#!/usr/bin/env python3
"""C3 — two installers, one product.

`voidloom hooks install` writes the Python hook that emits the arrival line and
that HOOK_MARKER_RE recognises. `install.sh` lays down `hooks/session-start.mjs`
and the three `wire_*` scripts WIRE THAT ONE -- a hook the product cannot see,
cannot uninstall, and which never announces itself. Retiring it deletes the only
code-resident consumer of the Tier-0 engine, so the Tier-0 Python path lands in
the same commit or the retirement makes things worse.

    usage: c3_one_installer_suite.py <package_dir> <scratch_root> [--only NAME]

Every check drives a SHIPPED artifact -- the real `.mjs` under node, the real
`voidloom` CLI under a stdlib-only interpreter, the real regex from the organ.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List

if len(sys.argv) < 3:
    sys.exit("usage: c3_one_installer_suite.py <package_dir> <scratch_root> [--only NAME]")
PKG = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)

# The interpreter the Tier-0 promise names: a bare system python3 with no venv,
# no sqlite_vec, no torch. Chosen explicitly, because choosing the interpreter
# chooses WHICH defect you are looking at.
STDLIB_PY = "/usr/bin/python3"
RESULTS: List[Dict[str, Any]] = []


def note(name: str, held: bool, detail: Any) -> None:
    if ONLY and ONLY not in name:
        return
    RESULTS.append({"name": name, "held": bool(held), "detail": detail})
    print(f"  {name:<60s} {'HELD' if held else 'VIOLATED':<9s} {json.dumps(detail, default=str)[:250]}")


def box(tag: str) -> str:
    p = os.path.join(ROOT, tag)
    shutil.rmtree(p, ignore_errors=True)
    os.makedirs(p)
    return p


# ===========================================================================
# C3.1 — THE TIER-0 PROMISE: `voidloom recall` works on a stdlib-only python3
#   install.sh:26-27 says "Tier 0 (ALWAYS succeeds, stdlib-only) ... Keyword
#   recall works on ANY python3". Measured at the parent: it dies with
#   "No module named 'sqlite_vec'" while `doctor` on the SAME interpreter
#   returns healthy: true.
# ===========================================================================
h1 = box("c3_1_tier0")
brain = os.path.join(h1, ".claude", "memory")
os.makedirs(brain, exist_ok=True)
for d in ("organs", "voidloom", "migrations", "hooks", "install", "seed"):
    if os.path.isdir(os.path.join(PKG, d)):
        shutil.copytree(os.path.join(PKG, d), os.path.join(brain, d), dirs_exist_ok=True)
DB = os.path.join(brain, "claude_memory.db")
subprocess.run([STDLIB_PY, os.path.join(brain, "organs", "schema_migrator.py"), "apply", "--db", DB],
               capture_output=True, text=True, env={**os.environ, "WIZARD_LIBRARY_DB": DB}, timeout=600)
if not os.path.exists(DB):                       # migrator needs a file to open
    import sqlite3 as _s
    _c = _s.connect(DB); _c.close()
    subprocess.run([STDLIB_PY, os.path.join(brain, "organs", "schema_migrator.py"), "apply", "--db", DB],
                   capture_output=True, text=True, env={**os.environ, "WIZARD_LIBRARY_DB": DB}, timeout=600)
env0 = {"HOME": h1, "PATH": "/usr/bin:/bin", "USER": os.environ.get("USER", "user"),
        "TERM": "dumb", "WIZARD_LIBRARY_DB": DB, "PYTHONPATH": brain}
p_store = subprocess.run([STDLIB_PY, "-m", "voidloom", "store", "--content",
                          "c3 tier zero probe memory", "--category", "insight", "--json"],
                         capture_output=True, text=True, env=env0, timeout=600)
p_rec = subprocess.run([STDLIB_PY, "-m", "voidloom", "recall", "--query", "tier zero probe",
                        "--top-k", "1", "--json"], capture_output=True, text=True, env=env0, timeout=600)
p_doc = subprocess.run([STDLIB_PY, "-m", "voidloom", "doctor", "--json"],
                       capture_output=True, text=True, env=env0, timeout=600)


def envelope(p):
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


e_rec, e_doc = envelope(p_rec), envelope(p_doc)
note("C3_1_recall_works_on_a_stdlib_only_interpreter",
     p_rec.returncode == 0 and e_rec.get("status") == "ok",
     {"recall_rc": p_rec.returncode, "recall_status": e_rec.get("status"),
      "recall_code": e_rec.get("code"),
      "message": ((e_rec.get("data") or {}).get("message") or "")[:90],
      "doctor_brain_healthy": (e_doc.get("data") or {}).get("brain_healthy")})
note("C3_1b_store_works_on_a_stdlib_only_interpreter",
     p_store.returncode == 0 and envelope(p_store).get("status") == "ok",
     {"store_rc": p_store.returncode, "store_status": envelope(p_store).get("status")})
# A doctor that says healthy while recall is dead is the pairing that hid this.
note("C3_1c_doctor_does_not_claim_healthy_while_recall_is_dead",
     not ((e_doc.get("data") or {}).get("brain_healthy") is True and p_rec.returncode != 0),
     {"doctor_brain_healthy": (e_doc.get("data") or {}).get("brain_healthy"),
      "recall_rc": p_rec.returncode})

# ===========================================================================
# C3.2 — HOOK_MARKER_RE must SEE a legacy Node hook, or status/uninstall
#        cannot report on an install the product itself produced.
# ===========================================================================
sys.path.insert(0, os.path.join(PKG, "organs"))
try:
    import voidloom_hooks_status as _hs  # type: ignore
    seen = {c: bool(_hs.HOOK_MARKER_RE.search(c)) for c in (
        "node /home/x/.claude/hooks/session-start.mjs",
        'node "D:\\Profiles\\x\\.claude\\hooks\\session-start.mjs"',
        "python3 /home/x/.claude/memory/hooks/session_start_inject.py",
    )}
    foreign = _hs.HOOK_MARKER_RE.search("node /home/x/.claude/hooks/someone-elses.mjs")
    note("C3_2_HOOK_MARKER_RE_sees_the_legacy_node_hook_and_not_a_foreign_one",
         all(seen.values()) and not foreign, {"seen": seen, "matches_foreign_hook": bool(foreign)})
except Exception as e:
    note("C3_2_HOOK_MARKER_RE_sees_the_legacy_node_hook_and_not_a_foreign_one", False,
         {"error": f"{type(e).__name__}: {e}"})

# ===========================================================================
# C3.3 — NOTHING WIRES THE NODE HOOK ANY MORE; the installers agree on one hook
# ===========================================================================
wire_sites: Dict[str, List[str]] = {}
for rel in ("install.sh", "install/wire_persona.sh", "install/wire_persona.ps1",
            "install/install.ps1"):
    f = pathlib.Path(PKG, rel)
    if not f.exists():
        continue
    hits = [l.strip()[:100] for l in f.read_text(encoding="utf-8").splitlines()
            if "session-start.mjs" in l and not l.strip().startswith(("#", "//"))
            and not l.strip().startswith("Say ")]
    if hits:
        wire_sites[rel] = hits
note("C3_3_no_installer_lays_down_or_wires_the_node_hook", not wire_sites,
     {"sites": {k: len(v) for k, v in wire_sites.items()},
      "sample": next(iter(wire_sites.values()), [])[:2]})

# ===========================================================================
# C3.4 — the ONE hook every installer wires emits EXACTLY ONE arrival line
# ===========================================================================
hook_py = os.path.join(brain, "hooks", "session_start_inject.py")
payload = json.dumps({"session_id": "c3-arrival", "hook_event_name": "SessionStart"})
p_h = subprocess.run([STDLIB_PY, hook_py], input=payload, capture_output=True, text=True,
                     env={"HOME": h1, "PATH": "/usr/bin:/bin",
                          "VOIDLOOM_HEARTBEAT_DIR": os.path.join(h1, "hb")}, timeout=300)
try:
    ctx = json.loads(p_h.stdout)["hookSpecificOutput"]["additionalContext"]
except Exception:
    ctx = p_h.stdout
arrivals = [l for l in ctx.splitlines() if l.startswith("=== VOIDLOOM HOOK FIRED")]
note("C3_4_the_wired_hook_emits_exactly_one_arrival_line",
     p_h.returncode == 0 and len(arrivals) == 1,
     {"rc": p_h.returncode, "arrival_lines": len(arrivals), "bytes": len(ctx)})

# ===========================================================================
# C3.5 — if the Node hook still ships at all, it must not lie:
#        no stale flat-layout paths, and no "brain not reachable" over a
#        brain that is reachable.
# ===========================================================================
mjs = pathlib.Path(PKG, "hooks", "session-start.mjs")
if not mjs.exists():
    note("C3_5_no_node_hook_that_misreports_a_reachable_brain", True,
         {"ships": False, "note": "the Node hook is no longer in the package"})
else:
    hm = box("c3_5_mjs")
    shutil.copytree(os.path.join(h1, ".claude"), os.path.join(hm, ".claude"), dirs_exist_ok=True)
    node = shutil.which("node")
    if not node:
        note("C3_5_no_node_hook_that_misreports_a_reachable_brain", False,
             {"ships": True, "note": "node not on PATH; cannot measure -- UNMEASURED counts as not held"})
    else:
        p_m = subprocess.run([node, str(mjs)], input=payload, capture_output=True, text=True,
                             env={"HOME": hm, "PATH": os.environ.get("PATH", "")}, timeout=300)
        out = p_m.stdout
        db_here = os.path.join(hm, ".claude", "memory", "claude_memory.db")
        note("C3_5_no_node_hook_that_misreports_a_reachable_brain",
             ("not reachable" not in out) and ("=== VOIDLOOM HOOK FIRED" in out),
             {"ships": True, "brain_exists": os.path.exists(db_here),
              "says_not_reachable": "not reachable" in out,
              "arrival_line": "=== VOIDLOOM HOOK FIRED" in out, "first_line": out.splitlines()[:1]})

# ===========================================================================
# C3.6 — the docs name the verb that actually installs the hook
# ===========================================================================
docs: Dict[str, int] = {}
for rel in ("docs/ONBOARDING_GUIDE.md", "docs/PACKAGE_MANIFEST.md"):
    f = pathlib.Path(PKG, rel)
    if f.exists():
        n = f.read_text(encoding="utf-8").count("session-start.mjs")
        if n:
            docs[rel] = n
guide = pathlib.Path(PKG, "docs/ONBOARDING_GUIDE.md")
names_verb = guide.exists() and "voidloom hooks install" in guide.read_text(encoding="utf-8")
note("C3_6_docs_name_the_real_install_verb_and_not_the_retired_hook",
     not docs and names_verb, {"mjs_mentions": docs, "names_voidloom_hooks_install": names_verb})

# ---------------------------------------------------------------------------
held = sum(1 for r in RESULTS if r["held"])
print(json.dumps({"package": PKG, "scratch_root": ROOT, "stdlib_python": STDLIB_PY,
                  "held": held, "total": len(RESULTS),
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

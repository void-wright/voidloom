#!/usr/bin/env python3
"""C6 — a count or window anchored on the wrong event.

    usage: c6_anchor_suite.py <package_dir> <scratch_root> [--only NAME]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, Dict, List

if len(sys.argv) < 3:
    sys.exit("usage: c6_anchor_suite.py <package_dir> <scratch_root> [--only NAME]")
PKG = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)
sys.path.insert(0, os.path.join(PKG, "organs"))
RESULTS: List[Dict[str, Any]] = []


def note(name: str, held: bool, detail: Any) -> None:
    if ONLY and ONLY not in name:
        return
    RESULTS.append({"name": name, "held": bool(held), "detail": detail})
    print(f"  {name:<56s} {'HELD' if held else 'VIOLATED':<9s} {json.dumps(detail, default=str)[:240]}")


NOW = dt.datetime.now(dt.timezone.utc)
def ago(minutes: int) -> str:
    return (NOW - dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# C6.1 — THE FIRED WINDOW STARTS AT INSTALL, NOT AT THE FIRST FIRE.
#   A hash-pinned Codex hook is SKIPPED SILENTLY until trusted. Sessions run,
#   nothing fires. Anchoring the domain at the first fire erases every one of
#   them the moment a later session finally fires.
# ===========================================================================
import voidloom_heartbeat as hb  # noqa: E402

INSTALLED_AT = ago(100)
sessions = {"root": "/x", "root_present": True, "count": 4, "sessions": [
    {"id": "before-install",         "started_at": ago(200)},   # not judgeable
    {"id": "after-install-nofire-1", "started_at": ago(90)},    # A MISS
    {"id": "after-install-nofire-2", "started_at": ago(60)},    # A MISS
    {"id": "fired-one",              "started_at": ago(5)},
]}
beats = {"path": "/hb", "present": True, "readable": True, "fire_count": 1,
         "first_ts": ago(5), "last_ts": ago(5), "unparseable_lines": 0,
         "fires": [{"session_id": "fired-one", "ts": ago(5), "source": "startup"}]}
try:
    r1 = hb.join_state(registered=True, heartbeats=beats, sessions=sessions,
                       platform="codex", installed_at=INSTALLED_AT)
    sig = True
except TypeError as e:
    r1 = hb.join_state(registered=True, heartbeats=beats, sessions=sessions, platform="codex")
    sig = False
note("C6_1_the_fired_window_is_anchored_at_install_not_at_the_first_fire",
     sig and r1.get("missed_count") == 2 and r1.get("state") == "DEAD"
     and sorted(r1.get("missed_sessions") or []) == ["after-install-nofire-1",
                                                     "after-install-nofire-2"],
     {"accepts_installed_at": sig, "state": r1.get("state"),
      "missed_count": r1.get("missed_count"), "missed": r1.get("missed_sessions"),
      "not_judgeable_before_install": r1.get("sessions_before_install",
                                             r1.get("sessions_before_first_fire")),
      "domain": (r1.get("window") or {}).get("domain", "")[:110]})

# and a genuinely clean install must still read LIVE -- a fix that called
# everything DEAD would satisfy the check above and destroy the verb.
sessions_ok = {"root": "/x", "root_present": True, "count": 2, "sessions": [
    {"id": "before-install", "started_at": ago(200)},
    {"id": "fired-one",      "started_at": ago(5)},
]}
try:
    r1b = hb.join_state(registered=True, heartbeats=beats, sessions=sessions_ok,
                        platform="codex", installed_at=INSTALLED_AT)
except TypeError:
    r1b = hb.join_state(registered=True, heartbeats=beats, sessions=sessions_ok, platform="codex")
note("C6_1b_a_clean_install_still_reads_LIVE_with_zero_misses",
     r1b.get("state") == "LIVE" and r1b.get("missed_count") == 0,
     {"state": r1b.get("state"), "missed_count": r1b.get("missed_count")})

# ===========================================================================
# C6.2 — THE LEDGER PERSISTS THE NUMBER THAT DISTINGUISHES THE VERDICTS.
#   `observations: 10` with 10 hits and with 0 hits must not read the same.
# ===========================================================================
import voidloom_canary as vc  # noqa: E402

led = pathlib.Path(ROOT, "c6_ledger.json")
shutil.rmtree(str(led), ignore_errors=True)
if led.exists():
    led.unlink()
work = pathlib.Path(ROOT, "c6_g0"); shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
runner = work / "ok_runner.sh"
runner.write_text('#!/bin/sh\nif [ -n "$VOIDLOOM_CANARY_NONCE" ]; then\n'
                  '  printf "%s\\n" "$VOIDLOOM_CANARY_NONCE"\nelse\n'
                  "  printf \"I don't know\\n\"\nfi\n", encoding="utf-8")
os.chmod(runner, 0o755)
rows: List[Dict[str, Any]] = []
try:
    vc.live_run("codex", "G1", str(runner), str(work), "", trials=2,
                ledger=str(led), record=True)
    rows = json.loads(led.read_text(encoding="utf-8")).get("entries", []) if led.exists() else []
except Exception as e:
    rows = [{"_error": f"{type(e).__name__}: {e}"}]
have_hits = bool(rows) and all("hits" in r for r in rows if "_error" not in r)
note("C6_2_a_ledger_row_records_hits_not_only_observations",
     have_hits and bool(rows),
     {"rows": len(rows), "keys": sorted(rows[0].keys()) if rows else [],
      "every_row_has_hits": have_hits})

# ===========================================================================
# C6.3 — THE SELF-CHECK MEASURES WHAT IT CAN REACH.
#   C11 / C11b reported UNMEASURABLE on every default run for want of a hook
#   path the organ can derive from its own location.
# ===========================================================================
h = pathlib.Path(ROOT, "c6_selfcheck"); shutil.rmtree(h, ignore_errors=True)
for sub in (".claude", ".codex", "fx", "hb"):
    (h / sub).mkdir(parents=True, exist_ok=True)
env = {**os.environ, "HOME": str(h), "CLAUDE_CONFIG_DIR": str(h / ".claude"),
       "CODEX_HOME": str(h / ".codex"), "VOIDLOOM_HEARTBEAT_DIR": str(h / "hb"),
       "VOIDLOOM_SELFCHECK_FIXTURE_DIR": str(h / "fx"),
       "VOIDLOOM_CANARY_LEDGER": str(h / "ledger.json")}
env.pop("WIZARD_LIBRARY_DB", None)
p3 = subprocess.run([sys.executable, os.path.join(PKG, "organs", "voidloom_canary.py"),
                     "--self-check", "--json"], capture_output=True, text=True,
                    env=env, timeout=900)
try:
    d3 = json.loads(p3.stdout)["data"]
except Exception:
    d3 = {}
by_id = {c.get("id"): c for c in (d3.get("checks") or [])}
unmeasured = {i: c for i, c in by_id.items() if c.get("ok") is None}
# The first version demanded that C11 be MEASURED. It cannot be on this host --
# the `systemMessage` half is absent from BOTH legs, so the "banner untouched"
# claim has zero observations no matter what we pass. Demanding it would be the
# C4 mistake in a new place: requiring the code to deliver what the environment
# cannot. The honest property is narrower and is the actual defect: NO CHECK IS
# UNMEASURED FOR WANT OF AN ARGUMENT THE ORGAN COULD HAVE SUPPLIED ITSELF. What
# remains unmeasured must name a HOST limitation, not a missing flag.
_for_want_of_an_argument = {
    i: (c.get("detail") or {}).get("reason", "")[:80]
    for i, c in unmeasured.items()
    if "no --hook" in str((c.get("detail") or {}).get("reason", ""))
    or "needs a hook on disk" in str((c.get("detail") or {}).get("reason", ""))}
note("C6_3_no_check_is_unmeasured_for_want_of_an_argument_the_organ_knows",
     not _for_want_of_an_argument and bool(by_id),
     {"checks": len(by_id), "unmeasured": sorted(unmeasured),
      "unmeasured_for_want_of_an_argument": _for_want_of_an_argument,
      "verdict": d3.get("verdict"), "rc": p3.returncode})

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

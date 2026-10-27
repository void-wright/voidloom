#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""canary_r8_notice.py — R8: does the ACTIVATION NOTICE fire on an untrusted Codex,
and CANCEL when the hook is trusted?  CONTROL FIRST.  Four arms, live model.

    python3 harness/canary_r8_notice.py <artifacts_dir> <scratch_root> [--runs 5]

  ARM 0  CONTROL      empty dir, NO notice, NO hook           -> expect 0/N mention /hooks
  ARM A  UNTRUSTED    notice in AGENTS.md, hook REGISTERED per-invocation (-c) but NOT
                      trusted -> Codex skips it silently        -> expect >= 3/5 mention /hooks
  ARM C  TRUSTED      notice + hook, trust BYPASSED for this canary ONLY
                      (the marker line is in context)           -> expect 0/N mention /hooks
                      + the heartbeat appended N lines whose session_ids match Codex's own
                        rollout filenames (the doctor join key, verified on the real platform)
  ARM K  COMPETITOR   our notice in the git ROOT's AGENTS.md, cwd = a SUBDIR with its OWN
                      unrelated AGENTS.md, no hook               -> DESSA's NOT-CONTRADICTED rung
  ARM D  DOCTRINE     notice + the DOCTRINE block (the frozen prefix) in AGENTS.md, NO hook
         -IN-FILE     -> the case that was DEAD before Council #12: the file carried the
                      sentinel and cancelled the notice. Keyed on the hook-only arrival line
                      the notice must now FIRE (>= 3/5). A file cannot forge the key.

===========================================================================================
WARNING — THIS FILE, canary_r8_notice.py, IS THE ONE PLACE IN THE SHIPPED SET THAT PASSES
`--dangerously-bypass-hook-trust` TO CODEX.  It does so ONLY in ARM C, ONLY to emulate a hook
the user has trusted through /hooks, ONLY against a scratch working directory, and it
prints this warning on every run.  Nothing the installer ships requires the flag (R9).
===========================================================================================

Confinement: the REAL $CODEX_HOME is used for AUTH ONLY; nothing under ~/.codex is written
by this script.  Codex itself writes its own session rollouts under ~/.codex/sessions on
every invocation (the vendor's behaviour; the count of new rollouts is reported).  The hook
under test is the ARTIFACTS copy; its heartbeat goes to $VOIDLOOM_HEARTBEAT_DIR inside the
scratch root; its brain is the DB COPY beside the artifacts package.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

if len(sys.argv) < 3:
    sys.exit("usage: canary_r8_notice.py <artifacts_dir> <scratch_root> [--runs N]")
ART = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
RUNS = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 5
HOOK = os.path.join(ART, "hooks", "session_start_inject.py")
ORGANS = os.path.join(ART, "organs")
PROMPT = "Reply in one short sentence: what is 2 + 2?"
BYPASS_FLAG = "--dangerously-bypass-hook-trust"   # THE ONE PERMITTED SITE (see the WARNING above)
os.makedirs(ROOT, exist_ok=True)

print(__doc__.split("=" * 91)[1].strip() if "=" * 91 in __doc__ else "WARNING: canary_r8_notice.py passes the hook-trust bypass in ARM C only.")
print(f"\n########## R8 NOTICE CANARY  runs={RUNS}  artifacts={ART}\n# scratch={ROOT}\n" + "#" * 74)

sys.path.insert(0, ORGANS)
import voidloom_instructions as _vi  # noqa: E402
import voidloom_heartbeat as _hb  # noqa: E402

NOTICE = _vi.marker_block(_vi.NOTICE_BODY)


def doctrine_block() -> str:
    """The frozen prefix as the file tier carries it, produced by the ARTIFACTS'
    own emitter (never a copy of a live file): `voidloom preamble --budget 950`
    drops the volatile body and keeps the doctrine.  No arrival line can appear
    here by construction — the CLI never passes `arrival=`."""
    import voidloom_preamble as _vp  # noqa: E402
    pre = _vp.build_preamble(home=ART, budget=950)
    assert _vp.ARRIVAL_PREFIX not in pre.text, "the file-tier emit carried the hook-only key"
    return ("# >>> VOIDLOOM DOCTRINE BEGIN >>>\n## VOIDLOOM memory brain — the doctrine (file tier)\n"
            + pre.text.rstrip() + "\n# <<< VOIDLOOM DOCTRINE END <<<\n")
HB_DIR = os.path.join(ROOT, "heartbeat")
HOOK_TOML = ('hooks.SessionStart=[{matcher="startup",hooks=[{type="command",command="python3 '
             + HOOK + '",additionalContextLimit=6000}]}]')


def md(pfx: str) -> str:
    return tempfile.mkdtemp(prefix=pfx, dir=ROOT)


def wilson(k: int, n: int, z: float = 1.959964) -> Dict[str, Any]:
    """THE FLOOR IS AN INTERVAL, NOT A POINT (R8 v2 / CD7).  A 95% Wilson score
    interval for k successes in n Bernoulli trials -- the honest statement of what
    k/n says about the adherence rate.  With n = 5 the interval is wide by
    construction (5/5 -> [0.57, 1.00]); the number is reported WITH its width so a
    later reader cannot quote '100%' as if it were measured to a point."""
    if n <= 0:
        return {"k": k, "n": n, "point": None, "low": None, "high": None}
    ph = k / n
    den = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / den
    half = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / den
    return {"k": k, "n": n, "point": round(ph, 3), "low": round(max(0.0, centre - half), 3),
            "high": round(min(1.0, centre + half), 3), "z": z}


def codex_sessions_now() -> set:
    return set(glob.glob(os.path.join(_hb.codex_sessions_dir(), "**", "rollout-*.jsonl"), recursive=True))


def run_codex(cwd: str, *, hook: bool, bypass: bool, tag: str, i: int) -> Dict[str, Any]:
    out_file = os.path.join(ROOT, f"{tag}_{i}.last.txt")
    argv = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
            "--cd", cwd, "-o", out_file]
    if hook:
        argv += ["-c", HOOK_TOML]
    if bypass:
        argv.append(BYPASS_FLAG)
    argv.append(PROMPT)
    env = dict(os.environ)
    env["VOIDLOOM_HEARTBEAT_DIR"] = HB_DIR
    env.pop("WIZARD_LIBRARY_DB", None)
    env.pop("WIZARD_LIBRARY_HOME", None)
    before = codex_sessions_now()
    t0 = time.time()
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           env=env, cwd=cwd, timeout=240)
        rc, err = p.returncode, p.stderr
    except subprocess.TimeoutExpired:
        rc, err = -999, "TIMEOUT"
    dur = round(time.time() - t0, 1)
    after = codex_sessions_now()
    new_rollouts = sorted(after - before)
    text = Path(out_file).read_text(encoding="utf-8", errors="replace") if Path(out_file).exists() else ""
    return {"arm": tag, "i": i, "rc": rc, "seconds": dur,
            "mentions_slash_hooks": "/hooks" in text,
            "mentions_voidloom_doctor": "voidloom doctor" in text,
            "hook_line_in_stderr": bool(re.search(r"hook:\s*SessionStart", err)),
            "answer": text.strip()[:300], "new_rollouts": [os.path.basename(x) for x in new_rollouts],
            "stderr_tail": err.strip()[-200:]}


REPORT: Dict[str, Any] = {"runs": RUNS, "prompt": PROMPT, "arms": {}, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def arm(tag: str, cwd: str, *, hook: bool, bypass: bool, expect: str) -> Dict[str, Any]:
    rows = [run_codex(cwd, hook=hook, bypass=bypass, tag=tag, i=i) for i in range(RUNS)]
    hooks_n = sum(1 for r in rows if r["mentions_slash_hooks"])
    doctor_n = sum(1 for r in rows if r["mentions_voidloom_doctor"])
    ok_n = sum(1 for r in rows if r["rc"] == 0)
    w = wilson(hooks_n, RUNS)
    summary = {"cwd": cwd, "hook_registered": hook, "trust_bypassed": bypass, "expect": expect,
               "runs": RUNS, "rc0": ok_n, "mention_/hooks": hooks_n, "mention_voidloom_doctor": doctor_n,
               "wilson_95_mention_/hooks": w, "rows": rows}
    REPORT["arms"][tag] = summary
    print(f"\n  ARM {tag:<10} runs={RUNS} rc0={ok_n} mention(/hooks)={hooks_n} mention(voidloom doctor)={doctor_n}  "
          f"Wilson95=[{w['low']}, {w['high']}]  expect: {expect}")
    for r in rows:
        print(f"     #{r['i']} rc={r['rc']} {r['seconds']}s /hooks={r['mentions_slash_hooks']} doctor={r['mentions_voidloom_doctor']} "
              f"hookline={r['hook_line_in_stderr']} :: {r['answer'][:110]!r}")
    return summary


# ---- ARM 0: CONTROL FIRST -----------------------------------------------------------
c0 = md("arm0_control_")
control = arm("CONTROL", c0, hook=False, bypass=False, expect="0 of N mention /hooks")
if control["mention_/hooks"] != 0:
    print("\n  CONTROL IS DIRTY — the treatment arms are UNINTERPRETABLE. Stopping.")
    REPORT["verdict"] = "CONTROL_MISSING"
    Path(ROOT, "r8_report.json").write_text(json.dumps(REPORT, indent=1))
    sys.exit(3)

# ---- ARM A: untrusted hook + notice -------------------------------------------------
ca = md("armA_untrusted_")
Path(ca, "AGENTS.md").write_text(NOTICE, encoding="utf-8")
untrusted = arm("UNTRUSTED", ca, hook=True, bypass=False, expect=">= 3 of 5 mention /hooks")

# ---- ARM C: trusted (bypass, THIS canary only) + notice ------------------------------
cc = md("armC_trusted_")
Path(cc, "AGENTS.md").write_text(NOTICE, encoding="utf-8")
print(f"\n  WARNING: ARM C passes {BYPASS_FLAG} — the one permitted site (canary_r8_notice.py).")
hb_before = _hb.read_heartbeats("codex")["fire_count"] if Path(_hb.heartbeat_path("codex")).exists() else 0
os.environ["VOIDLOOM_HEARTBEAT_DIR"] = HB_DIR
hb_before = _hb.read_heartbeats("codex")["fire_count"]
trusted = arm("TRUSTED", cc, hook=True, bypass=True, expect="0 of N mention /hooks; heartbeat +N")
beats = _hb.read_heartbeats("codex")
new_fires = beats["fires"][hb_before:]
fired_ids = [f.get("session_id") for f in new_fires]
rollout_ids = [rid[-36-6:-6] if rid.endswith(".jsonl") else rid for r in trusted["rows"] for rid in r["new_rollouts"]]
rollout_ids = [Path(r).stem[-36:] for row in trusted["rows"] for r in row["new_rollouts"]]
join_ok = all(sid in rollout_ids for sid in fired_ids) and len(fired_ids) == RUNS
REPORT["trusted_join"] = {"heartbeat_new_lines": len(new_fires), "fired_session_ids": fired_ids,
                          "codex_rollout_ids_created": rollout_ids,
                          "every_fire_has_a_rollout_with_the_same_id": join_ok,
                          "derivations": [f.get("derivation") for f in new_fires]}
print(f"\n  TRUSTED JOIN: heartbeat +{len(new_fires)} lines; session_ids {fired_ids}\n"
      f"                rollouts   {rollout_ids}\n"
      f"                join key holds on the real platform: {join_ok}")

# ---- ARM K: the competitor (NOT-CONTRADICTED rung) ----------------------------------
ck = md("armK_competitor_")
subprocess.run(["git", "init", "-q", ck], check=False, capture_output=True)
Path(ck, "AGENTS.md").write_text(NOTICE, encoding="utf-8")
sub = Path(ck, "sub"); sub.mkdir()
Path(sub, "AGENTS.md").write_text("# Project rules\n- Always answer in British English.\n"
                                  "- Never use exclamation marks.\n", encoding="utf-8")
competitor = arm("COMPETITOR", str(sub), hook=False, bypass=False,
                 expect="notice survives a nearer, unrelated AGENTS.md (>= 3 of 5)")

# ---- ARM D: doctrine in the file, no hook — the notice must still fire ---------------
cd_ = md("armD_doctrine_in_file_")
Path(cd_, "AGENTS.md").write_text(NOTICE + "\n" + doctrine_block(), encoding="utf-8")
doctrine_arm = arm("DOCTRINE", cd_, hook=False, bypass=False,
                   expect="notice fires although the doctrine banner is in the file (>= 3 of 5)")

# ---- verdicts --------------------------------------------------------------------------
REPORT["verdict"] = {
    "R8d_doctrine_in_file_does_not_cancel_the_notice": doctrine_arm["mention_/hooks"] >= 3,
    "R8b_untrusted_fires": untrusted["mention_/hooks"] >= 3 and control["mention_/hooks"] == 0,
    "R8c_trusted_cancels": trusted["mention_/hooks"] == 0 and trusted["rc0"] == RUNS,
    "R8c_hook_fired_every_trusted_session": len(new_fires) == RUNS,
    "join_key_verified_on_real_platform": join_ok,
    "competitor_not_contradicted": competitor["mention_/hooks"] >= 3,
    "counts": {"control": f"{control['mention_/hooks']}/{RUNS}", "untrusted": f"{untrusted['mention_/hooks']}/{RUNS}",
               "trusted": f"{trusted['mention_/hooks']}/{RUNS}", "competitor": f"{competitor['mention_/hooks']}/{RUNS}",
               "doctrine_in_file": f"{doctrine_arm['mention_/hooks']}/{RUNS}"},
    # R8 v2 (CD7): each arm's count WITH its 95% Wilson interval; the floor for the
    # firing arms is the interval's LOW end, the ceiling for the silent arms its HIGH end.
    "wilson_95": {a: REPORT["arms"][a]["wilson_95_mention_/hooks"] for a in REPORT["arms"]},
    "floors_and_ceilings": {
        "untrusted_fires_at_least": untrusted["wilson_95_mention_/hooks"]["low"],
        "competitor_fires_at_least": competitor["wilson_95_mention_/hooks"]["low"],
        "control_fires_at_most": control["wilson_95_mention_/hooks"]["high"],
        "trusted_fires_at_most": trusted["wilson_95_mention_/hooks"]["high"],
        "doctrine_in_file_fires_at_least": doctrine_arm["wilson_95_mention_/hooks"]["low"],
        "note": ("n = RUNS per arm; a point estimate of 1.0 at n=5 is [0.57, 1.0] -- "
                 "the interval is the claim, the count is the evidence")},
}
REPORT["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
Path(ROOT, "r8_report.json").write_text(json.dumps(REPORT, indent=1))
print("\n  VERDICT " + json.dumps({k: v for k, v in REPORT["verdict"].items() if k != "wilson_95"}, indent=1))
print(f"  report: {os.path.join(ROOT, 'r8_report.json')}")
sys.exit(0 if all(v for k, v in REPORT["verdict"].items() if k != "counts") else 1)

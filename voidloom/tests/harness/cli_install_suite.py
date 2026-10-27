#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cli_install.py — CLI-LEVEL COVERAGE (R10), every verb through `voidloom`.

    python3 harness/test_cli_install.py <artifacts_dir> <scratch_root> [--only NAME]

The scratch root is REQUIRED and must be on the deployment filesystem (ext4
under $HOME); the harness asserts it recycles inode numbers and refuses tmpfs.
Every check below drives `voidloom <verb>` (the shim, a subprocess) and asserts
the ENVELOPE and the EXIT CODE.  Each verb has a negative control.  The four
doctor states are REACHED BY FIXTURE, including the fixture that defeated the
first heartbeat design (fired once, then stopped, then two sessions ran).

Environment isolation per check: HOME / CODEX_HOME / CLAUDE_CONFIG_DIR /
VOIDLOOM_HEARTBEAT_DIR / VOIDLOOM_INSTALL_RECEIPT / VOIDLOOM_PREIMAGE_STORE all
point INTO the scratch root, so nothing here can touch the live tree, the live
settings.json, ~/.codex or ~/.agents.  The one live referent is a COPY of the
real settings.json placed in the artifacts by the generator.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
try:
    from zoneinfo import ZoneInfo
except ImportError:                 # pragma: no cover
    ZoneInfo = None

if len(sys.argv) < 3:
    sys.exit("usage: test_cli_install.py <artifacts_dir> <scratch_root> [--only NAME]")
ART = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
SHIM = os.path.join(ART, "voidloom", "voidloom")
HOOK = os.path.join(ART, "hooks", "session_start_inject.py")
ORGANS = os.path.join(ART, "organs")
REAL_SETTINGS_COPY = os.path.join(ART, "preimages_ref", "settings.json.REAL_COPY")
R: Dict[str, Dict[str, Any]] = {}

os.makedirs(ROOT, exist_ok=True)
_probe = tempfile.mkdtemp(prefix="fsprobe_", dir=ROOT)
_p = Path(_probe) / "x"
_p.write_bytes(b"a"); _i1 = os.lstat(_p).st_ino
os.unlink(_p)
_p.write_bytes(b"b"); _i2 = os.lstat(_p).st_ino
INODE_REUSE = (_i1 == _i2)
FS_DEV = os.stat(_probe).st_dev
shutil.rmtree(_probe, ignore_errors=True)
if not INODE_REUSE:
    sys.exit(f"REFUSED: {ROOT} does not recycle inode numbers (tmpfs-class); "
             f"the shapes under test cannot occur here. Use ext4 under $HOME.")


def md(pfx: str) -> str:
    return tempfile.mkdtemp(prefix=pfx, dir=ROOT)


# A FIFO LEFT ON DISK IS A HANG PLANTED FOR THE NEXT TREE WALKER (A9, iteration 2):
# the preamble census rglob'd `*.json` over a tree in which an earlier battery run
# had left a FIFO named hooks.json and blocked >15 min in the kernel's
# wait_for_partner.  Every FIFO this suite creates is registered and unlinked at
# exit, and a final sweep asserts none remain under the scratch root.
_FIFOS: List[str] = []


def mkfifo(path: str) -> None:
    os.mkfifo(path)
    _FIFOS.append(path)


def _sweep_fifos() -> None:
    for f in _FIFOS:
        try:
            os.unlink(f)
        except OSError:
            pass


atexit.register(_sweep_fifos)


def sha(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def note(name: str, held: Any, detail: Dict[str, Any]) -> None:
    if ONLY and name != ONLY:
        return
    # THE VERDICT KEY IS NOT OVERRIDABLE BY THE DETAIL (iteration 3, found by the
    # reflector's own instrument): the R6 battery row's detail carries its own
    # `"held": <shapes held>`, and `{"held": bool(held), **detail}` let 32 overwrite
    # False -- a RED battery row printed VIOLATED and was COUNTED as held in the
    # summary (RM19x read 88/88 while H9 was red).  A count from an object a row
    # can rewrite is not a count.
    if "held" in detail:
        detail = {**detail, "detail_held": detail["held"]}
        del detail["held"]
    R[name] = {**detail, "held": bool(held)}
    print(f"  {name:58s} {'HELD' if held else 'VIOLATED':9s} "
          f"{json.dumps(detail, default=str)[:300]}")


def env_for(box: str, **extra: str) -> Dict[str, str]:
    """A fully isolated environment rooted at ``box``."""
    home = os.path.join(box, "home")
    for d in (home, os.path.join(home, ".codex"), os.path.join(home, ".claude")):
        os.makedirs(d, exist_ok=True)
    env = dict(os.environ)
    for k in ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "PYTHONPATH", "VOIDLOOM_PLATFORM",
              "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update({
        "HOME": home,
        "CODEX_HOME": os.path.join(home, ".codex"),
        "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude"),
        "VOIDLOOM_HEARTBEAT_DIR": os.path.join(box, "heartbeat"),
        "VOIDLOOM_INSTALL_RECEIPT": os.path.join(box, "receipt.json"),
        "VOIDLOOM_PREIMAGE_STORE": os.path.join(box, "preimages"),
        "VOIDLOOM_SELFCHECK_FIXTURE_DIR": os.path.join(box, "fx"),
        "VOIDLOOM_CANARY_LEDGER": os.path.join(box, "ledger.json"),
    })
    env.update(extra)
    return env


def vl(argv: List[str], env: Dict[str, str], cwd: Optional[str] = None,
       timeout: int = 180) -> Tuple[int, str, str, Optional[Dict[str, Any]]]:
    """Run `voidloom <argv>` through the shim in a subprocess."""
    try:
        p = subprocess.run([SHIM] + argv, capture_output=True, text=True, env=env,
                           cwd=cwd or env["HOME"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return -999, "", f"TIMEOUT-{timeout}s", None
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        doc = None
    return p.returncode, p.stdout, p.stderr, doc


def organ(script: str, argv: List[str], env: Dict[str, str], cwd: Optional[str] = None,
          timeout: int = 180) -> Tuple[int, str, str, Optional[Dict[str, Any]]]:
    """Run the organ DIRECTLY (python3 organs/x.py ...) for the exit-code twin."""
    try:
        p = subprocess.run([sys.executable, os.path.join(ORGANS, script)] + argv,
                           capture_output=True, text=True, env=env, cwd=cwd or env["HOME"],
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return -999, "", f"TIMEOUT-{timeout}s", None
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        doc = None
    return p.returncode, p.stdout, p.stderr, doc


def depth0_status_count(text: str) -> int:
    """How many top-level JSON documents carrying a `status` key sit on stdout."""
    n = 0
    dec = json.JSONDecoder()
    i = 0
    s = text.strip()
    while i < len(s):
        try:
            obj, j = dec.raw_decode(s, i)
        except ValueError:
            break
        if isinstance(obj, dict) and "status" in obj:
            n += 1
        i = j
        while i < len(s) and s[i].isspace():
            i += 1
    return n


def run_hook(payload: Any, env: Dict[str, str], timeout: int = 60) -> Tuple[int, str, str]:
    inp = payload if isinstance(payload, str) else json.dumps(payload)
    try:
        p = subprocess.run([sys.executable, HOOK], input=inp, capture_output=True, text=True,
                           env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -999, "", f"TIMEOUT-{timeout}s"
    return p.returncode, p.stdout, p.stderr


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def fake_codex_session(env: Dict[str, str], sid: str, started: datetime) -> str:
    d = Path(env["CODEX_HOME"]) / "sessions" / started.strftime("%Y/%m/%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-{started.strftime('%Y-%m-%dT%H-%M-%S')}-{sid}.jsonl"
    p.write_text(json.dumps({"timestamp": ts(started), "type": "session_meta",
                             "payload": {"session_id": sid, "id": sid,
                                         "timestamp": ts(started), "cwd": "/tmp"}}) + "\n",
                 encoding="utf-8")
    return str(p)


def fake_claude_session(env: Dict[str, str], sid: str, started: datetime) -> str:
    d = Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / "-tmp-proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(json.dumps({"type": "last-prompt", "sessionId": sid}) + "\n"
                 + json.dumps({"type": "user", "sessionId": sid, "timestamp": ts(started),
                               "cwd": "/tmp"}) + "\n", encoding="utf-8")
    return str(p)


def snapshot(root: str) -> Dict[str, Any]:
    """mtime_ns + size + sha of every file, and every directory, under root."""
    out: Dict[str, Any] = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p_ = os.path.join(dp, f)
            st_ = os.lstat(p_)
            out[p_] = (st_.st_mtime_ns, st_.st_size, sha(p_) if stat.S_ISREG(st_.st_mode) else None)
        for dd in dn:
            out[os.path.join(dp, dd) + "/"] = "dir"
    return out


def heartbeat_line(env: Dict[str, str], platform: str, sid: str, when: datetime) -> None:
    d = Path(env["VOIDLOOM_HEARTBEAT_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{platform}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"v": 2, "ts": ts(when), "platform": platform, "session_id": sid,
                             "preamble_sha": "x", "derivation": "fixture"}) + "\n")


print(f"\n########## CLI-LEVEL SUITE  artifacts={ART}\n# scratch={ROOT} dev={FS_DEV} "
      f"inode_numbers_recycled={INODE_REUSE}\n" + "#" * 74)

# =====================================================================
# R1 — ONE envelope per verb through dispatch; exit code preserved
# =====================================================================
box = md("r1_")
E = env_for(box)
for name, argv in (("preamble", ["preamble", "--json"]),
                   ("hooks", ["hooks", "--status", "--json"]),
                   ("canary", ["canary", "--self-check", "--json"]),
                   ("instructions", ["instructions", "status", "--json"])):
    rc, out, err, doc = vl(argv, E, cwd=box)
    n = depth0_status_count(out)
    # C5: this asserted `rc == 0`, which was incidental to the stated subject --
    # "ONE envelope per verb through dispatch; EXIT CODE PRESERVED". Assert the
    # preservation itself: the process code must be the one the envelope's own
    # status maps to. That is strictly stronger than `rc == 0`, and it is what
    # lets `canary --self-check` return 4 (PASS_WITH_UNMEASURABLE) without the
    # suite having to encode "unmeasurable is fine" -- the same taxonomy the
    # exit-code twins below already exercise for refusal (3) and incomplete (4).
    _expect = {"ok": 0, "refused": 3, "incomplete": 4}.get((doc or {}).get("status"), 1)
    note(f"R1_single_envelope_{name}", n == 1 and doc is not None and doc.get("verb") == name
         and rc == _expect,
         {"rc": rc, "expected_rc_for_status": _expect, "depth0_status_docs": n,
          "status": (doc or {}).get("status"), "code": (doc or {}).get("code")})

# exit-code twins: organ direct vs through the shim, on refusal (3) and incomplete (4)
tw = md("r1_twin_")
ET = env_for(tw)
# a receipt naming a hardlinked target -> blocked plan -> rc 3 on --yes
real = Path(tw) / "REAL.md"; real.write_bytes(b"USER\n")
named = Path(tw) / "AGENTS.md"; os.link(str(real), str(named))
pre = Path(tw) / "pre.bytes"; pre.write_bytes(b"OURS\n")
Path(ET["VOIDLOOM_INSTALL_RECEIPT"]).write_text(json.dumps({"touched": [
    {"path": str(named), "action": "restore_preimage", "preimage_path": str(pre),
     "preimage_sha256": sha(str(pre))}]}))
rc_o, _, _, d_o = organ("voidloom_hooks_status.py", ["--uninstall", "--yes", "--json", "--home", tw], ET, cwd=tw)
rc_v, out_v, _, d_v = vl(["hooks", "--uninstall", "--yes", "--json", "--home", tw], ET, cwd=tw)
note("R1_exit_code_preserved_refused_3",
     rc_o == 3 and rc_v == 3 and (d_o or {}).get("code") == (d_v or {}).get("code")
     and depth0_status_count(out_v) == 1 and real.read_bytes() == b"USER\n",
     {"organ_rc": rc_o, "voidloom_rc": rc_v, "code": (d_v or {}).get("code")})
# THE SAME REFUSAL WITHOUT --json (R1 v2: both output modes).  RM22 (rc override
# honoured only under --json) survived 57/57 because every exit-code assertion
# passed --json; `voidloom hooks uninstall --yes && echo done` printed done.
rc_oh, out_oh, _, _ = organ("voidloom_hooks_status.py", ["--uninstall", "--yes", "--home", tw], ET, cwd=tw)
rc_vh, out_vh, _, d_vh = vl(["hooks", "--uninstall", "--yes", "--home", tw], ET, cwd=tw)
note("R1_exit_code_preserved_human_mode_refused_3",
     rc_oh == 3 and rc_vh == 3 and d_vh is None and out_vh.count("Voidloom hooks --uninstall") == 1
     and out_vh.strip() != "" and real.read_bytes() == b"USER\n",
     {"organ_rc": rc_oh, "voidloom_rc": rc_vh, "headers": out_vh.count("Voidloom hooks --uninstall"),
      "is_json": d_vh is not None})
# an incomplete undo -> rc 4 both ways: a receipt whose target is a directory
tw2 = md("r1_twin4_")
ET2 = env_for(tw2)
# C1: the plan's domain is the live enumerators' view for this HOME, so a
# fixture target must be a path the verb really owns — otherwise the run is
# refused at the domain gate and this leg stops testing the exit-code contract.
(Path(tw2) / ".agents").mkdir(parents=True, exist_ok=True)
dtarget = Path(tw2) / ".agents" / "AGENTS.md"; dtarget.mkdir()
Path(ET2["VOIDLOOM_INSTALL_RECEIPT"]).write_text(json.dumps({"touched": [
    {"path": str(dtarget), "action": "delete_file"}]}))
rc_o4, _, _, d_o4 = organ("voidloom_hooks_status.py", ["--uninstall", "--yes", "--json", "--home", tw2], ET2, cwd=tw2)
rc_v4, out_v4, _, d_v4 = vl(["hooks", "uninstall", "--yes", "--json", "--home", tw2], ET2, cwd=tw2)
note("R1_exit_code_preserved_incomplete_or_refused",
     rc_o4 == rc_v4 and rc_o4 in (3, 4) and depth0_status_count(out_v4) == 1
     and dtarget.is_dir(),
     {"organ_rc": rc_o4, "voidloom_rc": rc_v4, "code": (d_v4 or {}).get("code")})
# and rc 4 (INCOMPLETE) in HUMAN mode -- the one status whose process exit code
# depends on the override (refused maps to 3 either way; incomplete would map to 1).
tw4h = md("r1_twin4h_")
ET4h = env_for(tw4h)
# C1: the undo domain is what the live enumerators see for this --home, so an
# INCOMPLETE undo has to be attempted on a path the verb really owns.  At
# <tw4h>/ours.json the run is refused at the domain gate (0 actions, status ok,
# rc 0) and this leg would silently stop testing the exit-code contract it was
# written for -- RM22's exact shape, one gate further in.
(Path(tw4h) / ".agents").mkdir(parents=True, exist_ok=True)
t4 = Path(tw4h) / ".agents" / "AGENTS.md"; t4.write_bytes(b'{"a": 1}\n')
p4 = Path(tw4h) / "pre.bytes"; p4.write_bytes(b'{"a": 0}\n')
Path(ET4h["VOIDLOOM_INSTALL_RECEIPT"]).write_text(json.dumps({"touched": [
    {"path": str(t4), "action": "restore_preimage", "preimage_path": str(p4), "preimage_sha256": sha(str(p4))}]}))
os.chmod(t4, 0o444)
try:
    rc_o4h, out_o4h, _, _ = organ("voidloom_hooks_status.py", ["--uninstall", "--yes", "--home", tw4h], ET4h, cwd=tw4h)
    rc_v4h, out_v4h, _, d_v4h = vl(["hooks", "uninstall", "--yes", "--home", tw4h], ET4h, cwd=tw4h)
    rc_v4j, _, _, d_v4j = vl(["hooks", "uninstall", "--yes", "--home", tw4h, "--json"], ET4h, cwd=tw4h)
finally:
    os.chmod(t4, 0o644)
note("R1_exit_code_preserved_human_mode_incomplete_4_equals_json_mode",
     rc_o4h == 4 and rc_v4h == 4 and rc_v4j == 4 and d_v4h is None and (d_v4j or {}).get("status") == "incomplete"
     and out_v4h.count("Voidloom hooks --uninstall") == 1,
     {"organ_rc": rc_o4h, "voidloom_rc_human": rc_v4h, "voidloom_rc_json": rc_v4j, "status_json": (d_v4j or {}).get("status")})
# the drift test
p = subprocess.run([sys.executable, "-m", "pytest", "-q", "voidloom/tests/test_voidloom.py",
                    "-k", "manifest"], capture_output=True, text=True, cwd=ART,
                   env={**os.environ, "PYTHONPATH": ART})
note("R1_drift_test_manifest_matches_dispatchable_verbs",
     p.returncode == 0 and "passed" in p.stdout and "failed" not in p.stdout,
     {"rc": p.returncode, "tail": p.stdout.strip().splitlines()[-1:] })
# negative control: an unknown verb is a usage error, not an envelope
rc, out, err, doc = vl(["frobnicate", "--json"], E, cwd=box)
note("R1_negative_unknown_verb_is_usage_error", rc == 2 and doc is None, {"rc": rc})

# =====================================================================
# R2 — the install-not-onboard guarantee: no heavy module, by census
# =====================================================================
HEAVY = ("torch", "sentence_transformers", "sqlite_vec", "transformers", "numpy")
census = f'''
import sys, json
from voidloom.cli import main
import io, contextlib
for argv in (["bootstrap", "--json"], ["agent-context", "--json"], ["hooks", "--status", "--json"],
             ["instructions", "status", "--json"], ["doctor", "--json"]):
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            main(argv)
        except SystemExit:
            pass
print("CENSUS" + json.dumps(sorted(m for m in sys.modules if m.split(".")[0] in {HEAVY!r})))
'''
p = subprocess.run([sys.executable, "-c", census], capture_output=True, text=True,
                   env={**env_for(box), "PYTHONPATH": ART}, cwd=box, timeout=300)
loaded = json.loads(p.stdout.split("CENSUS")[-1]) if "CENSUS" in p.stdout else None
note("R2_no_heavy_import_across_bootstrap_agent_context_hooks_instructions_doctor",
     loaded == [], {"loaded": loaded, "rc": p.returncode, "stderr_tail": p.stderr[-200:]})
# cwd=box, not the invoker's: `python -c` puts the CURRENT DIRECTORY first on
# sys.path, so a suite launched from inside the live tree imported the LIVE
# package here and measured the wrong tree (found 2026-09-15).
p = subprocess.run([sys.executable, "-X", "importtime", "-c", "import voidloom.cli"],
                   capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ART}, cwd=box)
heavy_hits = [ln for ln in p.stderr.splitlines() if any(h in ln for h in HEAVY)]
note("R2_importtime_import_voidloom_cli_no_heavy", p.returncode == 0 and not heavy_hits,
     {"rc": p.returncode, "heavy_lines": heavy_hits[:3]})

# =====================================================================
# R3 — the heartbeat: append-only, derived platform, never breaks session start
# =====================================================================
hb = md("r3_")
EH = env_for(hb)
codex_payload = {"session_id": "01a0-r3-codex-1", "transcript_path":
                 os.path.join(EH["CODEX_HOME"], "sessions/2026/09/14/rollout-x-01a0-r3-codex-1.jsonl"),
                 "cwd": hb, "hook_event_name": "SessionStart", "model": "gpt", "permission_mode": "default",
                 "source": "startup"}
rc1, out1, err1 = run_hook(codex_payload, EH)
rc2, out2, err2 = run_hook({**codex_payload, "session_id": "01a0-r3-codex-2"}, EH)
hbf = Path(EH["VOIDLOOM_HEARTBEAT_DIR"]) / "codex.jsonl"
lines = hbf.read_text().splitlines() if hbf.exists() else []
recs = [json.loads(x) for x in lines]
note("R3_heartbeat_is_append_only_one_line_per_fire",
     rc1 == 0 and rc2 == 0 and len(recs) == 2
     and [r["session_id"] for r in recs] == ["01a0-r3-codex-1", "01a0-r3-codex-2"]
     and all(r.get("platform") == "codex" and r.get("preamble_sha") for r in recs),
     {"rc": [rc1, rc2], "lines": len(recs), "derivation": [r.get("derivation") for r in recs]})
claude_payload = {"session_id": "abc-r3-claude", "transcript_path":
                  os.path.join(EH["CLAUDE_CONFIG_DIR"], "projects/-x/abc-r3-claude.jsonl"),
                  "cwd": hb, "hook_event_name": "SessionStart", "source": "startup"}
rc3, out3, err3 = run_hook(claude_payload, EH)
rc4, out4, err4 = run_hook({}, EH)
rc5, out5, err5 = run_hook({"session_id": "s"}, {**EH, "VOIDLOOM_PLATFORM": "gemini-cli"})
cl = json.loads((Path(EH["VOIDLOOM_HEARTBEAT_DIR"]) / "claude-code.jsonl").read_text().splitlines()[0])
un = json.loads((Path(EH["VOIDLOOM_HEARTBEAT_DIR"]) / "unknown.jsonl").read_text().splitlines()[0])
ge = json.loads((Path(EH["VOIDLOOM_HEARTBEAT_DIR"]) / "gemini-cli.jsonl").read_text().splitlines()[0])
note("R3_platform_is_DERIVED_from_payload_or_env_never_typed",
     rc3 == 0 and rc4 == 0 and rc5 == 0 and cl["platform"] == "claude-code"
     and "transcript_path" in cl["derivation"] and un["platform"] == "unknown"
     and un["derivation"].startswith("none") and ge["derivation"] == "env:VOIDLOOM_PLATFORM",
     {"claude": cl["derivation"], "unknown": un["derivation"], "env": ge["derivation"]})
# the payload for the full preamble under every case
_ARRIVAL_PREFIX = "=== VOIDLOOM HOOK FIRED"


def preamble_of(out: str) -> str:
    """The hook's additionalContext MINUS the one line only the hook emits.

    Council #12 (2026-09-15): the hook appends a session-bound ARRIVAL line the
    file-tier emitter never carries.  Byte-identity with `voidloom preamble` is
    therefore asserted MODULO that line, and the line's presence is asserted on
    its own (below, and R8a) so neither half can hide the other."""
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    # the arrival SEGMENT is "\n" + line (a blank separator, then the line):
    # remove both, or the stripped text carries an extra blank the CLI never emits
    # the hook body is "\n" + ARRIVAL_LINE + "\n" + <the CLI body>: strip exactly that
    return re.sub(r"\n" + re.escape(_ARRIVAL_PREFIX) + r"[^\n]*\n", "", ctx, count=1)


def arrival_lines_of(out: str) -> int:
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    return sum(1 for l in ctx.split("\n") if l.startswith(_ARRIVAL_PREFIX))
full = preamble_of(out4)
note("R8a2_every_hook_emit_carries_exactly_one_arrival_line_and_the_cli_emit_none",
     all(arrival_lines_of(o) == 1 for o in (out1, out3, out4, out5)),
     {"counts": [arrival_lines_of(o) for o in (out1, out3, out4, out5)]})
# UNWRITABLE heartbeat dir: exit 0 + full preamble + the reason on stderr
ro = md("r3_ro_")
ERO = env_for(ro)
Path(ERO["VOIDLOOM_HEARTBEAT_DIR"]).mkdir(parents=True)
os.chmod(ERO["VOIDLOOM_HEARTBEAT_DIR"], 0o555)
try:
    rc6, out6, err6 = run_hook(codex_payload, ERO)
finally:
    os.chmod(ERO["VOIDLOOM_HEARTBEAT_DIR"], 0o755)
note("R3_unwritable_heartbeat_dir_still_exits_0_with_full_preamble",
     rc6 == 0 and preamble_of(out6) == full and "heartbeat NOT written" in err6
     and not (Path(ERO["VOIDLOOM_HEARTBEAT_DIR"]) / "codex.jsonl").exists(),
     {"rc": rc6, "preamble_equal": preamble_of(out6) == full, "stderr": err6.strip()[-120:]})
# the heartbeat DIR cannot be CREATED (parent unwritable): the mkdir branch, exit 0
rop = md("r3_roparent_")
ERP = env_for(rop)
_par = Path(rop, "ro_parent"); _par.mkdir()
ERP["VOIDLOOM_HEARTBEAT_DIR"] = str(_par / "heartbeat")
os.chmod(_par, 0o555)
try:
    rc6b, out6b, err6b = run_hook(codex_payload, ERP)
finally:
    os.chmod(_par, 0o755)
note("R3_uncreatable_heartbeat_dir_parent_unwritable_still_exits_0",
     rc6b == 0 and preamble_of(out6b) == full and "heartbeat NOT written" in err6b
     and "mkdir" in err6b, {"rc": rc6b, "stderr": err6b.strip()[-100:]})
# a FIFO planted at the heartbeat path: no hang, exit 0
ff = md("r3_fifo_")
EF = env_for(ff)
Path(EF["VOIDLOOM_HEARTBEAT_DIR"]).mkdir(parents=True)
mkfifo(str(Path(EF["VOIDLOOM_HEARTBEAT_DIR"]) / "codex.jsonl"))
t0 = time.time()
rc7, out7, err7 = run_hook(codex_payload, EF, timeout=30)
note("R3_fifo_at_heartbeat_path_does_not_hang_session_start",
     rc7 == 0 and preamble_of(out7) == full and (time.time() - t0) < 25
     and "heartbeat NOT written" in err7,
     {"rc": rc7, "elapsed_s": round(time.time() - t0, 1), "stderr": err7.strip()[-100:]})
# stdin that never closes (a pipe held open): the hook must not block on it
holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.PIPE)
t0 = time.time()
try:
    p = subprocess.run([sys.executable, HOOK], stdin=holder.stdout, capture_output=True, text=True,
                       env=EH, timeout=40)
    rc8, out8 = p.returncode, p.stdout
except subprocess.TimeoutExpired:
    rc8, out8 = -999, ""
finally:
    holder.kill()
note("R3_stdin_that_never_closes_does_not_block_the_hook",
     rc8 == 0 and preamble_of(out8) == full and (time.time() - t0) < 20,
     {"rc": rc8, "elapsed_s": round(time.time() - t0, 1)})
# PAYLOAD BEFORE ENV (RM05): a Codex session launched from a Claude Code shell
# inherits CLAUDE_PROJECT_DIR; the payload's transcript_path under CODEX_HOME must
# win, or the codex row reads NEVER_FIRED forever while claude gets phantom fires.
rm05 = md("r3_rm05_")
ER5 = env_for(rm05)
ER5["CLAUDE_PROJECT_DIR"] = rm05
rc_m5, out_m5, err_m5 = run_hook({**codex_payload, "session_id": "01a0-rm05"}, ER5)
_m5f = Path(ER5["VOIDLOOM_HEARTBEAT_DIR"]) / "codex.jsonl"
_m5 = json.loads(_m5f.read_text().splitlines()[0]) if _m5f.exists() else {}
note("R3_payload_transcript_path_outranks_CLAUDE_PROJECT_DIR_in_the_env",
     rc_m5 == 0 and _m5.get("platform") == "codex" and "transcript_path" in _m5.get("derivation", "")
     and not (Path(ER5["VOIDLOOM_HEARTBEAT_DIR"]) / "claude-code.jsonl").exists(),
     {"rc": rc_m5, "platform": _m5.get("platform"), "derivation": _m5.get("derivation")})
# THE HEARTBEAT WRITES ONLY TO A REGULAR FILE WITH EXACTLY ONE NAME WHOSE PARENT
# CHAIN RESOLVES TO THE NAMED PATH (R3 v2 / R9 v2 -- the property, four shapes).
# P5: a hardlink of a user's file at the heartbeat path RECEIVED our line (rc 0,
# no reason) because the writer had no link-count gate; the writer is now the
# shared identity-gated one.  Each shape: rc 0, full preamble, reason on stderr,
# victim byte-identical, nothing written.
_shapes: Dict[str, Dict[str, Any]] = {}
for shape in ("hardlink", "symlink", "directory", "parent_chain_symlink", "relinked_ancestor_dir_absent"):
    bx = md(f"r3_{shape}_")
    EX = env_for(bx)
    victim = Path(bx) / "USER_KEEPS_THIS.txt"; victim.write_bytes(b"THE USER'S FILE\n")
    hbd = Path(EX["VOIDLOOM_HEARTBEAT_DIR"])
    if shape == "parent_chain_symlink":
        elsewhere = Path(bx) / "elsewhere"; elsewhere.mkdir()
        os.symlink(str(elsewhere), str(hbd))          # the DIR is a link: parent chain re-resolves
    elif shape == "relinked_ancestor_dir_absent":
        # P2 (R3/R9 v3): ~/.voidloom -> elsewhere and NO heartbeat directory yet.  The
        # iteration-2 hook's mkdir(parents=True) CREATED elsewhere/heartbeat/ BEFORE the
        # file gate spoke.  The shipped four-shape check linked the heartbeat DIR itself,
        # so mkdir found it existing and this branch was never reached (reflector, iter 2).
        elsewhere = Path(bx) / "elsewhere"; elsewhere.mkdir()
        hbd = Path(EX["HOME"]) / ".voidloom" / "heartbeat"
        EX["VOIDLOOM_HEARTBEAT_DIR"] = str(hbd)
        os.symlink(str(elsewhere), str(Path(EX["HOME"]) / ".voidloom"))
    else:
        hbd.mkdir(parents=True)
        tgt_ = hbd / "codex.jsonl"
        if shape == "hardlink":
            os.link(str(victim), str(tgt_))
        elif shape == "symlink":
            os.symlink(str(victim), str(tgt_))
        else:
            tgt_.mkdir()
    rcx, outx, errx = run_hook(codex_payload, EX, timeout=30)
    try:
        pre_eq = preamble_of(outx) == full
    except Exception:
        pre_eq = False
    _shapes[shape] = {"rc": rcx, "preamble_equal": pre_eq,
                      "reason_on_stderr": "heartbeat NOT written" in errx,
                      "refused": "REFUSED" in errx,
                      "victim_intact": victim.read_bytes() == b"THE USER'S FILE\n",
                      "nothing_written_elsewhere": not (Path(bx) / "elsewhere" / "codex.jsonl").exists()
                                                    if shape == "parent_chain_symlink" else True,
                      # the far side must hold NOTHING -- no file AND no directory (P2)
                      "nothing_created_far_side": (not any((Path(bx) / "elsewhere").iterdir())
                                                   if shape == "relinked_ancestor_dir_absent" else True),
                      "stderr": errx.strip()[-100:]}
note("R3_heartbeat_refuses_hardlink_symlink_directory_relinked_parent_and_relinked_ancestor_nothing_created",
     len(_shapes) == 5 and all(v["rc"] == 0 and v["preamble_equal"] and v["reason_on_stderr"]
                               and v["refused"] and v["victim_intact"] and v["nothing_written_elsewhere"]
                               and v["nothing_created_far_side"]
                               for v in _shapes.values()),
     {k: {kk: vv for kk, vv in v.items() if kk != "stderr"} for k, v in _shapes.items()})
# THE "ONE WRITER" PROPERTY, EXECUTED (MB09, reflector iter 2): a copy of the artifacts
# with organs/voidloom_hooks_status.py renamed AWAY -- the hook must exit 0 with the
# full preamble, say on stderr that it refuses a second, ungated writer, and create NO
# file under the heartbeat dir.  A fallback built on the builtin open(path, "ab") passed
# the previous TEXT check ("os.open(" absent) and the census (builtin open was not in its
# population); it fails this one because the file appears.
_art9 = md("r3_noorgan_art_")
for _sub in ("voidloom", "organs", "hooks", "preimages_ref"):
    shutil.copytree(os.path.join(ART, _sub), os.path.join(_art9, _sub), symlinks=False,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".vl_selfcheck_fixtures"))
shutil.copy2(os.path.join(ART, "voidloom_manifest.json"), _art9)
os.link(os.path.join(ART, "claude_memory.db"), os.path.join(_art9, "claude_memory.db"))
os.rename(os.path.join(_art9, "organs", "voidloom_hooks_status.py"), os.path.join(_art9, "organs", "voidloom_hooks_status.py.away"))
_b9 = md("r3_noorgan_"); _E9 = env_for(_b9)
_p9 = subprocess.run([sys.executable, os.path.join(_art9, "hooks", "session_start_inject.py")],
                     input=json.dumps(codex_payload), capture_output=True, text=True, env=_E9, timeout=60)
_hb9 = sorted(p.name for p in Path(_E9["VOIDLOOM_HEARTBEAT_DIR"]).glob("*")) if Path(_E9["VOIDLOOM_HEARTBEAT_DIR"]).exists() else []
# the FULL preamble is the COPY's own `voidloom preamble` (the emitter embeds the tree it
# runs from, so equality is asserted within the copy, as CANARY3 asserts it within ART)
_pp9 = subprocess.run([os.path.join(_art9, "voidloom", "voidloom"), "preamble"], capture_output=True, text=True,
                      env=_E9, cwd=_b9, timeout=120)
try:
    _pre9 = _pp9.returncode == 0 and preamble_of(_p9.stdout) + "\n" == _pp9.stdout and len(_pp9.stdout) > 1000
except Exception:
    _pre9 = False
import ast as _ast
_hb_src = Path(ORGANS, "voidloom_heartbeat.py").read_text(encoding="utf-8")
_hb_tree = _ast.parse(_hb_src)
_writing_os_open = [n.lineno for n in _ast.walk(_hb_tree) if isinstance(n, _ast.Call)
                    and getattr(getattr(n.func, "value", None), "id", None) == "os" and getattr(n.func, "attr", None) == "open"
                    and any(getattr(a, "attr", "") in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC") for a in _ast.walk(n))]
_builtin_open = [n.lineno for n in _ast.walk(_hb_tree) if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) and n.func.id == "open"]
note("R3_heartbeat_goes_through_the_shared_identity_gated_writer_EXECUTED_no_fallback_when_organ_absent",
     _p9.returncode == 0 and _pre9 and _hb9 == [] and "refusing to write with a second, ungated writer" in _p9.stderr
     and _writing_os_open == [] and _builtin_open == [],
     {"rc": _p9.returncode, "preamble_equal": _pre9, "heartbeat_files": _hb9, "stderr": _p9.stderr.strip()[-120:],
      "writing_os_open_sites": _writing_os_open, "builtin_open_sites": _builtin_open})
shutil.rmtree(_art9, ignore_errors=True)
# the heartbeat is in a USER-OWNED path by default (never a vendor dir)
p = subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, %r); import voidloom_heartbeat as h; print(h.heartbeat_dir())" % ORGANS],
                   capture_output=True, text=True, env={k: v for k, v in env_for(box).items()
                                                       if k != "VOIDLOOM_HEARTBEAT_DIR"})
default_dir = p.stdout.strip()
_eh = env_for(box)["HOME"]
note("R3_default_heartbeat_dir_is_user_owned_not_vendor",
     default_dir == os.path.join(_eh, ".voidloom", "heartbeat")
     and not default_dir.startswith(os.path.join(_eh, ".codex"))
     and not default_dir.startswith(os.path.join(_eh, ".claude")),
     {"default": default_dir, "home": _eh})
# negative control: the hook byte-identity with the emitter (CANARY 3)
rc9, out9, _, _ = vl(["preamble"], EH, cwd=hb)
note("CANARY3_hook_additionalContext_byte_identical_to_voidloom_preamble",
     rc9 == 0 and out9 == full + "\n", {"rc": rc9, "bytes": len(full)})
# and on codex the hook budgets below the installed spill limit.  BOTH numbers are
# DERIVED at the moment of asking (the hook's own constant; the installer's own
# constant) -- a fixture literal equal to the constant under test is the P10 class.
_hook_src = Path(HOOK).read_text(encoding="utf-8")
_m = re.search(r"^CODEX_PREAMBLE_BUDGET\s*=\s*(\d+)", _hook_src, flags=re.M)
_budget = int(_m.group(1)) if _m else None
_hs_src = Path(ORGANS, "voidloom_hooks_status.py").read_text(encoding="utf-8")
_m2 = re.search(r"^CODEX_ADDITIONAL_CONTEXT_LIMIT\s*=\s*(\d+)", _hs_src, flags=re.M)
_limit = int(_m2.group(1)) if _m2 else None
rc10, out10, _, d10 = vl(["preamble", "--json", "--budget", str(_budget)], EH, cwd=hb)
_tok = ((d10 or {}).get("data", {}).get("tokens") or {}).get("total")
note("CANARY3b_codex_payload_hook_equals_preamble_at_the_hooks_own_budget_below_the_install_limit",
     _budget is not None and _limit is not None and _budget < _limit and rc10 == 0
     and (d10 or {}).get("data", {}).get("preamble") == preamble_of(out1)
     and ((d10 or {}).get("data", {}).get("budget") or {}).get("requested") == _budget
     and isinstance(_tok, int) and _tok < _budget,
     {"rc": rc10, "hook_budget": _budget, "install_limit": _limit, "preamble_tokens": _tok,
      "headroom": ((d10 or {}).get("data", {}).get("budget") or {}).get("headroom_unspent")})

# =====================================================================
# R4 — doctor's four states, EACH reached by fixture
# =====================================================================
def doctor_state(env: Dict[str, str], platform: str, cwd: str) -> Tuple[Optional[str], Dict[str, Any]]:
    rc, out, err, doc = vl(["doctor", "--json", "--platform", platform, "--cwd", cwd], env, cwd=cwd)
    row = ((doc or {}).get("data") or {}).get("hooks", {}).get("platforms", {}).get(platform) or {}
    return row.get("state"), {"rc": rc, "row": {k: row.get(k) for k in
                              ("state", "fire_count", "missed_count", "sessions_since_last_fire_count",
                               "approval_step", "trust_status", "session_count_total")}}

def sid() -> str:
    """Real-shaped ids: Codex rollout stems end in a 36-char uuid; Claude Code
    transcript stems ARE the uuid.  A short id ('s-0') can never equal
    stem[-36:], which is how the iteration-1 LIVE fixtures were vacuous (A1)."""
    return str(uuid.uuid4())


for platform in ("codex", "claude-code"):
    now = datetime.now(timezone.utc)
    mk_sess = fake_codex_session if platform == "codex" else fake_claude_session
    install_argv = ["hooks", "install", "--platform", "codex" if platform == "codex" else "claude",
                    "--yes", "--json"]
    def seed_claude(En: Dict[str, str]) -> None:
        if platform == "claude-code":
            Path(En["CLAUDE_CONFIG_DIR"], "settings.json").write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
    # NOT_INSTALLED (a stale heartbeat file from an earlier install does not make it installed)
    b = md(f"r4_{platform}_ni_")
    En = env_for(b)
    mk_sess(En, sid(), now - timedelta(days=3))
    heartbeat_line(En, platform, sid(), now - timedelta(days=30))
    st, det = doctor_state(En, platform, b)
    note(f"R4_{platform}_NOT_INSTALLED_by_fixture", st == "NOT_INSTALLED", det)
    # INSTALLED_NEVER_FIRED: install, two sessions ran, no heartbeat
    b = md(f"r4_{platform}_inf_")
    En = env_for(b); seed_claude(En)
    rc_i, _, _, d_i = vl(install_argv, En, cwd=b)
    mk_sess(En, sid(), now - timedelta(hours=2)); mk_sess(En, sid(), now - timedelta(hours=1))
    st, det = doctor_state(En, platform, b)
    note(f"R4_{platform}_INSTALLED_NEVER_FIRED_by_fixture",
         rc_i == 0 and st == "INSTALLED_NEVER_FIRED" and det["row"].get("approval_step")
         and det["row"].get("missed_count") == 2,
         {**det, "install_rc": rc_i, "install_code": (d_i or {}).get("code")})
    # DEAD: fired once, then stopped, then two sessions ran (VAEL's fixture)
    b = md(f"r4_{platform}_dead_")
    En = env_for(b); seed_claude(En)
    vl(install_argv, En, cwd=b)
    t1 = now - timedelta(minutes=30)
    s1 = sid()
    mk_sess(En, s1, t1); heartbeat_line(En, platform, s1, t1 + timedelta(seconds=1))
    mk_sess(En, sid(), now - timedelta(minutes=20)); mk_sess(En, sid(), now - timedelta(minutes=10))
    st, det = doctor_state(En, platform, b)
    note(f"R4_{platform}_DEAD_fired_once_then_two_sessions_ran",
         st == "DEAD" and det["row"].get("missed_count") == 2 and det["row"].get("fire_count") == 1,
         det)
    # the SAME fixture graded by the clock would say healthy: the last fire is 30 min old
    note(f"R4_{platform}_DEAD_is_decided_by_the_session_join_not_the_clock",
         st == "DEAD" and (now - (t1 + timedelta(seconds=1))) < timedelta(days=1), det)
    # DEAD(1): a hook dead for EXACTLY ONE session is DEAD (RM02: `>= 2` survived)
    b = md(f"r4_{platform}_dead1_")
    En = env_for(b); seed_claude(En)
    vl(install_argv, En, cwd=b)
    s1 = sid()
    mk_sess(En, s1, now - timedelta(minutes=30)); heartbeat_line(En, platform, s1, now - timedelta(minutes=30, seconds=-1))
    mk_sess(En, sid(), now - timedelta(minutes=10))
    st, det = doctor_state(En, platform, b)
    note(f"R4_{platform}_DEAD_after_exactly_one_missed_session", st == "DEAD" and det["row"].get("missed_count") == 1, det)
    # LIVE, NON-VACUOUS (R4 v2 / A1): every session fired, AND at least one platform
    # session is stamped AFTER its own heartbeat with an id EQUAL to the fire's id,
    # so the id join is CONSULTED (sessions_since_last_fire_count == 1, missed 0).
    # Real-shaped: on Claude Code the transcript's first timestamp is the first
    # message, which lands after the hook fired; a mis-extracted id (RM01) reads
    # this fixture DEAD.
    b = md(f"r4_{platform}_live_")
    En = env_for(b); seed_claude(En)
    vl(install_argv, En, cwd=b)
    live_ids = [sid(), sid(), sid()]
    for i, mins in enumerate((40, 25, 5)):
        t_ = now - timedelta(minutes=mins)
        if i < 2:
            mk_sess(En, live_ids[i], t_); heartbeat_line(En, platform, live_ids[i], t_ + timedelta(seconds=1))
        else:
            heartbeat_line(En, platform, live_ids[i], t_ - timedelta(seconds=2)); mk_sess(En, live_ids[i], t_)
    if platform == "claude-code":
        # a nested subagent transcript is NOT a session (RM03: `**` counted it)
        nd = Path(En["CLAUDE_CONFIG_DIR"]) / "projects" / "-tmp-proj" / "subagents"; nd.mkdir(parents=True)
        (nd / f"agent-{sid()}.jsonl").write_text(json.dumps({"type": "user", "timestamp": ts(now)}) + "\n")
    st, det = doctor_state(En, platform, b)
    note(f"R4_{platform}_LIVE_by_fixture_with_the_id_join_consulted",
         st == "LIVE" and det["row"].get("missed_count") == 0 and det["row"].get("fire_count") == 3
         and det["row"].get("sessions_since_last_fire_count") == 1
         and det["row"].get("session_count_total") == 3, det)
    # a user away three weeks with a healthy hook is LIVE, not stale (join consulted here too)
    b = md(f"r4_{platform}_away_")
    En = env_for(b); seed_claude(En)
    vl(install_argv, En, cwd=b)
    t_ = now - timedelta(days=21); s_away = sid()
    heartbeat_line(En, platform, s_away, t_ - timedelta(seconds=2)); mk_sess(En, s_away, t_)
    st, det = doctor_state(En, platform, b)
    note(f"R4_{platform}_three_weeks_away_healthy_hook_is_LIVE_not_stale",
         st == "LIVE" and det["row"].get("sessions_since_last_fire_count") == 1
         and det["row"].get("missed_count") == 0, det)

# THE CODEX FILENAME STAMP IS LOCAL TIME (P6): a rollout whose first line carries
# no timestamp falls back to the filename; the old code suffixed 'Z' to local
# digits.  Two zones, two directions: under UTC-7 a real miss hid (LIVE); under
# UTC+9 a fired session read newer than its own fire (false DEAD).
if ZoneInfo is not None:
    _tz_res: Dict[str, Any] = {}
    for tzname, expect_state, expect_missed in (("America/Los_Angeles", "DEAD", 1), ("Asia/Tokyo", "LIVE", 0)):
        b = md("r4_tz_")
        En = env_for(b); En["TZ"] = tzname
        vl(["hooks", "install", "--platform", "codex", "--yes", "--json"], En, cwd=b)
        now_utc = datetime.now(timezone.utc)
        fired = sid(); other = sid()
        # Council #12: a fire must JOIN a platform session to count, so the fired
        # session gets a rollout too (stamped in LOCAL time like the other).
        if expect_state == "DEAD":
            # fired 15 min ago; an UNFIRED session started 10 min ago (a real miss)
            fired_at = now_utc - timedelta(minutes=15)
            heartbeat_line(En, "codex", fired, fired_at)
            when = now_utc - timedelta(minutes=10); which = other
        else:
            # a fired session 10 min ago; an older session 20 min ago predates the
            # first fire and is not judged: no miss
            fired_at = now_utc - timedelta(minutes=10)
            heartbeat_line(En, "codex", fired, fired_at)
            when = now_utc - timedelta(minutes=20); which = other
        for _when, _which in ((fired_at, fired), (when, which)):
            local = _when.astimezone(ZoneInfo(tzname))
            d_ = Path(En["CODEX_HOME"]) / "sessions" / local.strftime("%Y/%m/%d"); d_.mkdir(parents=True, exist_ok=True)
            (d_ / f"rollout-{local.strftime('%Y-%m-%dT%H-%M-%S')}-{_which}.jsonl").write_text(
                json.dumps({"type": "session_meta", "payload": {"id": _which}}) + "\n")   # NO timestamp anywhere
        st, det = doctor_state(En, "codex", b)
        _tz_res[tzname] = {"state": st, "missed": det["row"].get("missed_count"),
                           "expected": (expect_state, expect_missed)}
    note("R4_codex_filename_stamp_is_LOCAL_time_converted_to_UTC_in_two_zones",
         len(_tz_res) == 2 and all(v["state"] == v["expected"][0] and v["missed"] == v["expected"][1]
                                   for v in _tz_res.values()), _tz_res)

# Codex trust: DEFAULT (no vendor spawn) reads the platform's persisted record ->
# untrusted after a fresh install; --probe-platform (consent) asks hooks/list.
# The install limit is DERIVED from the installer's source (A5), never typed.
b = md("r4_codex_trust_")
En = env_for(b)
vl(["hooks", "install", "--platform", "codex", "--yes", "--json"], En, cwd=b)
st, det = doctor_state(En, "codex", b)
rc, out, err, doc = vl(["hooks", "status", "--json"], En, cwd=b)
crow = [r for r in ((doc or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]
note("R4_codex_trust_untrusted_after_install_from_the_persisted_record_no_vendor_spawn",
     det["row"].get("trust_status") == "untrusted" and st == "INSTALLED_NEVER_FIRED"
     and crow["installed"] is True and crow["resolved"]["platform_view"]["queried"] is False
     and crow["resolved"]["trust_status"] == "untrusted"
     and "hooks.json:session_start:0:0" in (crow["resolved"]["hooks_json"].get("our_keys_by_schema") or [""])[0],
     {**det, "derived_from": crow["resolved"]["installed_derived_from"][:60],
      "keys_by_schema": crow["resolved"]["hooks_json"].get("our_keys_by_schema")})
# a "Trust all" record written the way Codex writes it (Amendment 2, executed) -> trusted_by_record
_key = crow["resolved"]["hooks_json"]["our_keys_by_schema"][0]
with open(Path(En["CODEX_HOME"], "config.toml"), "a", encoding="utf-8") as fh:
    fh.write(f'\n[hooks.state."{_key}"]\ntrusted_hash = "sha256:0000"\n')
rc, out, err, doc = vl(["hooks", "status", "--json"], En, cwd=b)
crow2 = [r for r in ((doc or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]
note("R4_codex_trust_record_in_config_toml_reads_trusted_by_record_without_spawning",
     crow2["resolved"]["trust_status"] == "trusted_by_record"
     and crow2["resolved"]["platform_view"]["queried"] is False
     and crow2["resolved"]["config_toml"]["our_trust_state_keys"] == [_key],
     {"trust": crow2["resolved"]["trust_status"], "keys": crow2["resolved"]["config_toml"]["our_trust_state_keys"]})
Path(En["CODEX_HOME"], "config.toml").unlink()
# R15 / MB07: a record for a DIFFERENT key (a relocated CODEX_HOME -- Amendment 2's own scenario --
# or a project-level hook) and NO record for ours: trust stays untrusted, the record is
# reported FOREIGN (stale_trust_state_keys), never under our_trust_state_keys (P6).
_stale_key = "/elsewhere/.codex/hooks.json:session_start:0:0"
Path(En["CODEX_HOME"], "config.toml").write_text(f'[hooks.state."{_stale_key}"]\ntrusted_hash = "sha256:1111"\n')
rc, out, err, doc = vl(["hooks", "status", "--json"], En, cwd=b)
crow2b = [r for r in ((doc or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]
_ct2b = crow2b["resolved"]["config_toml"]
note("R4_codex_stale_trust_record_for_another_path_does_not_trust_our_hook_and_is_reported_FOREIGN",
     crow2b["resolved"]["trust_status"] == "untrusted" and crow2b["resolved"]["platform_view"]["queried"] is False
     and _ct2b.get("our_trust_state_keys") == [] and _ct2b.get("stale_trust_state_keys") == [_stale_key]
     and _ct2b.get("trust_key_embeds_absolute_path") is False and _stale_key != _key,
     {"trust": crow2b["resolved"]["trust_status"], "ours": _ct2b.get("our_trust_state_keys"),
      "stale": _ct2b.get("stale_trust_state_keys"), "our_key": _key[-40:]})
Path(En["CODEX_HOME"], "config.toml").unlink()
# CONSENTED: the live view; installed derived from the app-server alone; the limit == the installer's constant
rc, out, err, doc = vl(["hooks", "status", "--json", "--probe-platform"], En, cwd=b)
crow3 = [r for r in ((doc or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]
_pv3 = crow3["resolved"].get("platform_view") or {}    # tolerant: a timed-out probe (RM15) must grade RED, not crash the suite
note("R5_status_under_probe_platform_reports_the_platforms_own_resolver_view_codex",
     crow3["installed"] is True and str(crow3["resolved"].get("installed_derived_from")).startswith("app-server")
     and _pv3.get("queried") is True
     and _pv3.get("our_additional_context_limit") == [_limit]
     and (_pv3.get("our_keys") or [""])[0].endswith("hooks.json:session_start:0:0")
     and crow3["resolved"].get("trust_status") == "untrusted"
     and "side_effect" in _pv3
     and ((doc or {}).get("data") or {}).get("platform_probe", {}).get("consented") is True
     and "--probe-platform" in err and En["CODEX_HOME"] in err,
     {"derived_from": str(crow3["resolved"].get("installed_derived_from"))[:60],
      "keys": _pv3.get("our_keys"), "trust": crow3["resolved"].get("trust_status"),
      "limit": _pv3.get("our_additional_context_limit"), "installer_limit": _limit,
      "probe_reason": _pv3.get("reason"), "stderr_names_prefix": En["CODEX_HOME"] in err})
# R4/R5 v3 (P4): the statement precedes the SPAWN -- asserted in-process by intercepting
# Popen and reading what stderr already held at that instant (never by wall-clock order).
_p4code = f'''
import io, json, subprocess, sys
sys.path.insert(0, {ORGANS!r})
import voidloom_hooks_status as hs
buf = io.StringIO(); sys.stderr = buf
seen = {{}}
def fake(*a, **k):
    seen["stderr_at_spawn"] = buf.getvalue(); seen["argv"] = list(a[0])
    raise OSError("spawn intercepted by the suite")
subprocess.Popen = fake
hs.probe_codex_hooks(["{{codex_home}}/hooks.json", "{{codex_home}}/config.toml"], {b!r}, probe_platform=True)
sys.__stderr__.write("P4" + json.dumps(seen))
'''
_p4 = subprocess.run([sys.executable, "-c", _p4code], capture_output=True, text=True, env=En, cwd=b, timeout=60)
try:
    _seen = json.loads(_p4.stderr.split("P4")[-1])
except Exception:
    _seen = {"raw": _p4.stderr[-200:]}
_at = _seen.get("stderr_at_spawn") or ""
note("R5_probe_platform_side_effect_is_stated_on_stderr_BEFORE_the_vendor_spawn_naming_CODEX_HOME",
     "--probe-platform" in _at and "app-server" in _at and En["CODEX_HOME"] in _at
     and (_seen.get("argv") or [""])[1:2] == ["app-server"],
     {"stderr_at_spawn": _at.strip()[-140:], "spawn_argv_tail": (_seen.get("argv") or [])[1:]})
# RM06: a hooks.json Codex does NOT load (our command under type=mcpTool) is NOT
# installed -- under BOTH derivations; the basename in the bytes is not the answer.
b = md("r4_rm06_")
En = env_for(b)
_cmd = f"python3 {HOOK}"
Path(En["CODEX_HOME"], "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
    {"matcher": "startup", "hooks": [{"type": "mcpTool", "command": _cmd, "additionalContextLimit": 6000}]}]}}, indent=2) + "\n")
rc_a, _, _, d_a = vl(["hooks", "status", "--json"], En, cwd=b)
rc_b, _, _, d_b = vl(["hooks", "status", "--json", "--probe-platform"], En, cwd=b)
ra = [r for r in ((d_a or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]["resolved"]
rb = [r for r in ((d_b or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]["resolved"]
_pvb = rb.get("platform_view") or {}
note("R4_hooks_json_that_codex_does_not_load_is_NOT_installed_under_either_derivation",
     ra["installed"] is False and rb["installed"] is False and _pvb.get("queried") is True
     and _pvb.get("ours") == [] and "session_start_inject" in Path(En["CODEX_HOME"], "hooks.json").read_text(),
     {"file_derived": ra["installed"], "platform_derived": rb["installed"],
      "hooks_loaded_by_codex": len(_pvb.get("hooks") or []), "probe_reason": _pvb.get("reason")})
# negative control for doctor: a bogus platform is a usage error
rc, out, err, doc = vl(["doctor", "--json", "--platform", "gemini"], En, cwd=b)
note("R4_negative_doctor_bogus_platform_is_usage_error", rc == 2 and doc is None, {"rc": rc})

# =====================================================================
# R5 — enumerable BEFORE (dry-run writes NOTHING), AFTER, and IN REVERSE
# =====================================================================
b = md("r5_dry_")
E5 = env_for(b)
tgt = Path(E5["CLAUDE_CONFIG_DIR"]) / "settings.json"
shutil.copyfile(REAL_SETTINGS_COPY, tgt)
# strip our element so the install has work to do
d = json.loads(tgt.read_bytes())
d["hooks"]["SessionStart"] = [g for g in d["hooks"]["SessionStart"]
                              if not any("session_start_inject" in h.get("command", "") for h in g["hooks"])]
tgt.write_bytes((json.dumps(d, indent=2) + "\n").encode())
os.utime(tgt, (1_700_000_000, 1_700_000_000))
before = snapshot(b)
rc, out, err, doc = vl(["hooks", "install", "--platform", "claude", "--dry-run", "--json"], E5, cwd=b)
rc2, out2, err2, doc2 = vl(["instructions", "install", "--dry-run", "--json"], E5, cwd=b)
rc3, out3, err3, doc3 = vl(["hooks", "install", "--platform", "codex", "--json"], E5, cwd=b)  # default = dry
after = snapshot(b)
ww = ((doc or {}).get("data") or {}).get("would_write") or []
wi = ((doc2 or {}).get("data") or {}).get("would_write") or []
note("R5_dry_run_writes_NOTHING_mtimes_hashes_dirs_unchanged",
     before == after and rc == 0 and rc2 == 0 and rc3 == 0
     and (doc or {}).get("code") == "install_plan" and (doc3 or {}).get("code") == "install_plan",
     {"rc": [rc, rc2, rc3], "entries_before": len(before), "entries_after": len(after)})
# DRY IN ALL THREE DIRECTIONS (R5 v2 / P4): after a REAL install of both mechanisms,
# the UNDO dry-runs, `hooks status`, `instructions status` and `doctor` leave every
# path under the home (~/.codex, ~/.claude, ~/.agents, ~/.voidloom) unchanged by
# mtime AND hash.  The iteration-1 undo dry-run spawned `codex app-server` and
# changed 119 paths under ~/.codex.
b5 = md("r5_dry_undo_")
E5u = env_for(b5)
Path(E5u["CLAUDE_CONFIG_DIR"], "settings.json").write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
_ins = [vl(["hooks", "install", "--platform", "codex", "--yes", "--json"], E5u, cwd=b5)[0],
        vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E5u, cwd=b5)[0],
        vl(["instructions", "install", "--yes", "--json"], E5u, cwd=b5)[0]]
E5u["VOIDLOOM_HEARTBEAT_DIR"] = os.path.join(E5u["HOME"], ".voidloom", "heartbeat")   # the default, in the home
run_hook({"session_id": sid(), "transcript_path": os.path.join(E5u["CODEX_HOME"], "sessions/x/rollout-x.jsonl")}, E5u)
_snap0 = snapshot(E5u["HOME"])
_dry = {"hooks_uninstall_dry": vl(["hooks", "uninstall", "--json"], E5u, cwd=b5),
        "hooks_uninstall_dry_flag": vl(["hooks", "uninstall", "--dry-run", "--json"], E5u, cwd=b5),
        "instructions_uninstall_dry": vl(["instructions", "uninstall", "--json"], E5u, cwd=b5),
        "hooks_status": vl(["hooks", "status", "--json"], E5u, cwd=b5),
        "instructions_status": vl(["instructions", "status", "--json"], E5u, cwd=b5),
        "doctor": vl(["doctor", "--json", "--cwd", b5], E5u, cwd=b5),
        "hooks_install_claude_dry_again": vl(["hooks", "install", "--platform", "claude", "--json"], E5u, cwd=b5),
        "instructions_install_dry_again": vl(["instructions", "install", "--json"], E5u, cwd=b5)}
_snap1 = snapshot(E5u["HOME"])
_changed = sorted(k for k in set(_snap0) | set(_snap1) if _snap0.get(k) != _snap1.get(k))
_undo_rows = ((_dry["hooks_uninstall_dry"][3] or {}).get("data") or {}).get("actions") or []
note("R5_dry_run_undo_status_and_doctor_write_NOTHING_under_the_home_including_vendor_state",
     _ins == [0, 0, 0] and _changed == [] and all(v[0] == 0 for v in _dry.values())
     and (_dry["hooks_uninstall_dry"][3] or {}).get("code") == "uninstall_plan"
     and len(_undo_rows) >= 2 and all("resolved_path" in a and "planned_identity" in a for a in _undo_rows)
     and ((_dry["doctor"][3] or {}).get("data") or {}).get("hooks", {}).get("platform_probe", {}).get("consented") is False,
     {"install_rc": _ins, "changed_paths": _changed[:8], "changed_count": len(_changed),
      "rc": {k: v[0] for k, v in _dry.items()}, "undo_rows": len(_undo_rows), "snapshot_entries": len(_snap0)})
# R15 (P5): EVERY row of EVERY plan carries identity -- no kind filter, both verbs.
_ident_keys = ("realpath", "st_dev", "st_ino", "st_nlink")
_lacking = [(w.get("kind"), w.get("path", "")[-24:]) for w in ww + wi if not all(k in w for k in _ident_keys)]
note("R5_dry_run_lists_every_path_with_realpath_and_identity",
     len(ww) >= 3 and len(wi) >= 4 and _lacking == []
     and any(w["kind"] == "merge_element" for w in ww) and any(w["kind"] == "receipt_append" for w in ww)
     and any(w["kind"] == "mkdir" for w in wi) and any(w["kind"] == "receipt_append" for w in wi),
     {"hooks_would_write": [(w["kind"], w["path"][-30:]) for w in ww],
      "instructions_would_write": [(w["kind"], w["path"][-30:]) for w in wi], "rows_lacking_identity": _lacking})
rd = ((doc2 or {}).get("data") or {}).get("readers_of_agents_md") or {}
targets = ((doc2 or {}).get("data") or {}).get("targets") or []
note("R5_instructions_dry_run_names_readers_in_three_bands_and_create_vs_append",
     set(rd.keys()) == {"present_on_this_machine_probed", "known_but_absent", "open_ended"}
     and any(r["name"].startswith("OpenAI Codex") for r in rd["present_on_this_machine_probed"])
     and {t["action"] for t in targets} <= {"create", "append", "already_installed"}
     and any(t["action"] == "create" and t["id"] == "agents-md" for t in targets),
     {"present": [r["name"] for r in rd.get("present_on_this_machine_probed", [])],
      "actions": {t["id"]: t["action"] for t in targets}})
# negative controls: bad platform / missing platform / unknown flag
rc, out, err, doc = vl(["hooks", "install", "--platform", "gemini", "--json"], E5, cwd=b)
rc2, out2, err2, doc2 = vl(["hooks", "--install", "--json"], E5, cwd=b)
rc3, out3, err3, doc3 = vl(["instructions", "--bogus", "--json"], E5, cwd=b)
note("R5_negative_controls_bad_platform_missing_platform_bogus_flag",
     rc == 2 and (doc is not None and doc.get("code") == "usage")
     and rc2 == 1 and (doc2 or {}).get("code") == "platform_required"
     and rc3 == 2 and (doc3 or {}).get("code") == "usage",
     {"rc": [rc, rc2, rc3], "codes": [(doc or {}).get("code"), (doc2 or {}).get("code"), (doc3 or {}).get("code")]})

# =====================================================================
# R7 — the REAL settings.json copy: round trip byte-identical; JSONC refused
# =====================================================================
b = md("r7_")
E7 = env_for(b)
tgt = Path(E7["CLAUDE_CONFIG_DIR"]) / "settings.json"
shutil.copyfile(REAL_SETTINGS_COPY, tgt)
orig = tgt.read_bytes()
real_line195 = orig.decode().split("\n")[194]
note("R7_real_file_has_a_block_comment_opener_INSIDE_a_string_on_line_195",
     "/*" in real_line195 and json.loads(orig) is not None, {"line195": real_line195[:80]})
rc, out, err, doc = vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E7, cwd=b)
note("R7_real_copy_with_the_live_element_is_ALREADY_INSTALLED_and_unwritten",
     rc == 0 and (doc or {}).get("code") == "install_already_installed" and tgt.read_bytes() == orig
     and not Path(E7["VOIDLOOM_INSTALL_RECEIPT"]).exists(),
     {"rc": rc, "code": (doc or {}).get("code")})
rc, out, err, doc = vl(["hooks", "status", "--json"], E7, cwd=b)
u = ((doc or {}).get("data") or {}).get("unreceipted") or {}
note("R7_status_SEES_the_unreceipted_live_element_as_an_orphan",
     u.get("orphan_count", 0) >= 1 and any(o["platform"] == "claude-code" and o["path"] == str(tgt)
                                           for o in u.get("orphans", [])),
     {"orphans": [(o["platform"], o["kind"]) for o in u.get("orphans", [])]})
# stripped: the fresh-machine shape with the user's 15 other hooks
d = json.loads(orig)
d["hooks"]["SessionStart"] = [g for g in d["hooks"]["SessionStart"]
                              if not any("session_start_inject" in h.get("command", "") for h in g["hooks"])]
stripped = (json.dumps(d, indent=2) + "\n").encode()
tgt.write_bytes(stripped)
rc1, _, _, d1 = vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E7, cwd=b)
post = tgt.read_bytes()
pd = json.loads(post)
pd_minus = json.loads(post); pd_minus["hooks"]["SessionStart"] = [
    g for g in pd_minus["hooks"]["SessionStart"] if not any("session_start_inject" in h.get("command", "") for h in g["hooks"])]
rc2, _, _, d2 = vl(["hooks", "uninstall", "--yes", "--json"], E7, cwd=b)
back = tgt.read_bytes()
note("R7_real_copy_install_then_uninstall_is_BYTE_IDENTICAL",
     rc1 == 0 and (d1 or {}).get("code") == "install_installed" and rc2 == 0
     and (d2 or {}).get("code") == "uninstall_applied" and back == stripped,
     {"install": (d1 or {}).get("code"), "uninstall": (d2 or {}).get("code"),
      "sha_before": sha(str(tgt))[:12], "byte_identical": back == stripped})
note("R7_existing_hooks_for_other_tools_untouched_asserted_by_deep_equal_diff",
     pd_minus == json.loads(stripped)
     and sum(len(g["hooks"]) for ev in pd["hooks"].values() for g in ev) == 16
     and ((d1 or {}).get("data") or {}).get("diff", {}).get("pure") is True,
     {"hooks_after_install": sum(len(g["hooks"]) for ev in pd["hooks"].values() for g in ev),
      "diff_pure": ((d1 or {}).get("data") or {}).get("diff", {}).get("pure")})
# element-level: the platform rewrote the file after install
rc1, _, _, d1 = vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E7, cwd=b)
d = json.loads(tgt.read_bytes()); d["permissions"]["allow"].append("Bash(git status:*)"); d["effortLevel"] = "high"
tgt.write_bytes((json.dumps(d, indent=2) + "\n").encode())
changed = tgt.read_bytes()
rc2, _, _, d2 = vl(["hooks", "uninstall", "--yes", "--json"], E7, cwd=b)
now_ = json.loads(tgt.read_bytes())
exp = json.loads(changed); exp["hooks"]["SessionStart"] = [
    g for g in exp["hooks"]["SessionStart"] if not any("session_start_inject" in h.get("command", "") for h in g["hooks"])]
applied_rows = (((d2 or {}).get("data") or {}).get("applied") or {}).get("applied") or []
note("R5_R7_element_level_undo_keeps_the_users_grants_when_the_file_changed",
     rc2 == 0 and applied_rows and applied_rows[0].get("result") == "ELEMENT_REMOVED"
     and "Bash(git status:*)" in now_["permissions"]["allow"] and now_["effortLevel"] == "high"
     and tgt.read_bytes() == (json.dumps(exp, indent=2) + "\n").encode(),
     {"result": applied_rows[0].get("result") if applied_rows else None,
      "undo_class": applied_rows[0].get("undo_class") if applied_rows else None})
# JSONC / lossy fixtures -> REFUSED with a typed code, file untouched, element printed
fx = {"jsonc_line_comment": b"// top comment\n" + stripped,
      "jsonc_block_comment": stripped.replace(b'"cleanupPeriodDays": 365,', b'"cleanupPeriodDays": 365, /* keep */'),
      "crlf": stripped.replace(b"\n", b"\r\n"),
      "four_space": (json.dumps(json.loads(stripped), indent=4) + "\n").encode(),
      "tabs": stripped.replace(b"  ", b"\t"),
      "no_trailing_newline": stripped.rstrip(b"\n"),
      "not_an_object": b"[1, 2]\n"}
got = {}
for name, blob in fx.items():
    f = Path(b) / f"fx_{name}.json"; f.write_bytes(blob)
    rc, out, err, doc = vl(["hooks", "install", "--platform", "claude", "--target", str(f), "--yes", "--json"], E7, cwd=b)
    dd = (doc or {}).get("data") or {}
    got[name] = (rc, dd.get("refusal_code"), f.read_bytes() == blob, bool(dd.get("manual_paste")),
                 ("SKIPPING this file" in (dd.get("blockers") or [""])[0]) if name.startswith("jsonc") else None)
note("R7_JSONC_and_lossy_fixtures_REFUSED_typed_code_untouched_element_printed",
     all(v[0] == 3 and v[2] and v[3] for v in got.values())
     and got["jsonc_line_comment"][1] == "NOT_JSON" and got["jsonc_block_comment"][1] == "NOT_JSON"
     and got["jsonc_block_comment"][4] is True
     and all(got[k][1] == "LOSSY_SERIALIZATION" for k in ("crlf", "four_space", "tabs", "no_trailing_newline"))
     and got["not_an_object"][1] == "NOT_AN_OBJECT",
     {k: v[:2] for k, v in got.items()})
# the JSONC detector is THE PARSER: a raw-unicode file (ensure_ascii=False contract) is accepted
f = Path(b) / "fx_raw_unicode.json"
f.write_bytes((json.dumps(json.loads(stripped), indent=2, ensure_ascii=False) + "\n").encode())
rc, out, err, doc = vl(["hooks", "install", "--platform", "claude", "--target", str(f), "--yes", "--json"], E7, cwd=b)
note("R7_raw_unicode_file_matches_the_ensure_ascii_False_contract_and_installs",
     rc == 0 and (doc or {}).get("code") == "install_installed"
     and (((doc or {}).get("data") or {}).get("contract") or {}).get("ensure_ascii") is False,
     {"rc": rc, "contract": ((doc or {}).get("data") or {}).get("contract")})
# clause (e): the read-back must json.loads, or the pre-image is restored IMMEDIATELY
rb = f'''
import sys, json, os
sys.path.insert(0, {ORGANS!r})
import voidloom_hooks_status as hs
tgt = {str(Path(b) / "fx_readback.json")!r}
open(tgt, "wb").write({stripped!r})
plan = hs.install_plan("claude", {b!r}, {b!r}, tgt)
hs.serialize_under_contract = lambda doc, contract: b"{{ not json at all"
res = hs.install_execute(plan)
print("RB" + json.dumps({{"result": res["result"], "restored": open(tgt, "rb").read() == {stripped!r},
                         "rows": [(a.get("kind"), a.get("result")) for a in res["applied"]]}}))
'''
p = subprocess.run([sys.executable, "-c", rb], capture_output=True, text=True, env=E7, cwd=b)
rbj = json.loads(p.stdout.split("RB")[-1]) if "RB" in p.stdout else {}
note("R7e_readback_that_does_not_parse_restores_the_preimage_in_the_same_run",
     rbj.get("result") == "ROLLED_BACK" and rbj.get("restored") is True,
     {**rbj, "stderr": p.stderr[-150:]})
# R7(e) v2 (CD6): the read-back must json.loads AND EQUAL THE PLANNED POST-IMAGE.
# RM11 (parse-only) survived because the iteration-1 simulation fed garbage; this
# one feeds a DIFFERENT, PARSEABLE document on the post-write read-back only (the
# second read of the target; the first is the pre-image re-read).
rb2 = f'''
import sys, json, os
sys.path.insert(0, {ORGANS!r})
import voidloom_hooks_status as hs
tgt = {str(Path(b) / "fx_readback2.json")!r}
open(tgt, "wb").write({stripped!r})
plan = hs.install_plan("claude", {b!r}, {b!r}, tgt)
_orig = hs.read_bytes_if_regular
_n = [0]
def _fake(pth):
    if os.path.basename(pth) == "fx_readback2.json":
        _n[0] += 1
        if _n[0] >= 2:
            return b'{{"a_concurrent_writer": true}}\\n'
    return _orig(pth)
hs.read_bytes_if_regular = _fake
res = hs.install_execute(plan)
print("RB" + json.dumps({{"result": res["result"], "restored": open(tgt, "rb").read() == {stripped!r},
                         "reads_of_target": _n[0]}}))
'''
p2 = subprocess.run([sys.executable, "-c", rb2], capture_output=True, text=True, env=E7, cwd=b)
rbj2 = json.loads(p2.stdout.split("RB")[-1]) if "RB" in p2.stdout else {}
note("R7e_readback_that_parses_but_differs_from_the_planned_postimage_restores_the_preimage",
     rbj2.get("result") == "ROLLED_BACK" and rbj2.get("restored") is True and rbj2.get("reads_of_target", 0) >= 2,
     {**rbj2, "stderr": p2.stderr[-150:]})
# RM16: a user's PRE-EXISTING empty SessionStart array survives install + element-level undo
b16 = md("r7_rm16_")
E16 = env_for(b16)
t16 = Path(E16["CLAUDE_CONFIG_DIR"]) / "settings.json"
t16.write_bytes(b'{\n  "hooks": {\n    "SessionStart": []\n  },\n  "permissions": {\n    "allow": []\n  }\n}\n')
rc1, _, _, d1 = vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E16, cwd=b16)
d16 = json.loads(t16.read_bytes()); d16["permissions"]["allow"].append("Bash(ls:*)")
t16.write_bytes((json.dumps(d16, indent=2) + "\n").encode())
exp16 = json.loads(t16.read_bytes()); exp16["hooks"]["SessionStart"] = []
rc2, _, _, d2 = vl(["hooks", "uninstall", "--yes", "--json"], E16, cwd=b16)
note("R7_users_preexisting_empty_SessionStart_array_survives_element_level_undo",
     rc1 == 0 and rc2 == 0 and t16.read_bytes() == (json.dumps(exp16, indent=2) + "\n").encode()
     and json.loads(t16.read_bytes())["hooks"]["SessionStart"] == [],
     {"rc": [rc1, rc2], "after": t16.read_text()[:80]})
# R14: THE EMPTY CONTAINER, BOTH SHAPES.  The class is "a container Claude Code writes
# when the user removes its last member" and it has exactly two members: `"SessionStart": []`
# (RM16, above) and `"hooks": {}` (P1: the sibling the iteration-2 sweep missed -- refused
# rc 3 as a `replace`).  Each shape with AND without a trailing comma (MB12: `comma = False`
# survived because the RM16 fixture had SessionStart as the only key); the pre-image
# literals are the criterion's own, install rc 0, undo byte-identical.
_r14: Dict[str, Dict[str, Any]] = {}
for _nm, _pre in (
        ("hooks_object_with_comma", b'{\n  "hooks": {},\n  "permissions": {\n    "allow": []\n  }\n}\n'),
        ("hooks_object_last_key", b'{\n  "permissions": {\n    "allow": []\n  },\n  "hooks": {}\n}\n'),
        ("sessionstart_array_with_comma_before_another_key",
         b'{\n  "hooks": {\n    "SessionStart": [],\n    "Stop": [\n      {\n        "hooks": [\n          {\n            "type": "command",\n            "command": "echo bye"\n          }\n        ]\n      }\n    ]\n  }\n}\n'),
        ("sessionstart_array_last_key", b'{\n  "hooks": {\n    "Stop": [],\n    "SessionStart": []\n  }\n}\n')):
    _bx = md(f"r14_{_nm}_"); _Ex = env_for(_bx)
    _tx = Path(_Ex["CLAUDE_CONFIG_DIR"]) / "settings.json"; _tx.write_bytes(_pre)
    _rc1, _, _, _d1 = vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], _Ex, cwd=_bx)
    _mid = _tx.read_bytes()
    _rc2, _, _, _d2 = vl(["hooks", "uninstall", "--yes", "--json"], _Ex, cwd=_bx)
    _r14[_nm] = {"install_rc": _rc1, "install_code": (_d1 or {}).get("code"), "undo_rc": _rc2,
                 "element_present_between": b"session_start_inject" in _mid,
                 "round_trip_identical": _tx.read_bytes() == _pre,
                 "containers_opened": (((_d1 or {}).get("data") or {}).get("diff") or {}).get("empty_containers_opened")}
note("R14_empty_container_BOTH_shapes_with_and_without_trailing_comma_install_and_round_trip",
     len(_r14) == 4 and all(v["install_rc"] == 0 and v["undo_rc"] == 0 and v["element_present_between"]
                            and v["round_trip_identical"] for v in _r14.values()), _r14)
# post-image sha in the receipt
rc1, _, _, d1 = vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E7, cwd=b)
rcpt = json.loads(Path(E7["VOIDLOOM_INSTALL_RECEIPT"]).read_text())
ent = [t for t in rcpt["touched"] if t["path"] == str(tgt)]
note("R7d_receipt_records_postimage_sha_element_contract_and_identity",
     len(ent) == 1 and ent[0].get("postimage_sha256") == sha(str(tgt)) and ent[0].get("element")
     and ent[0].get("contract") and ent[0].get("planned_identity_at_install", {}).get("ino"),
     {"keys": sorted(ent[0].keys()) if ent else None})

# =====================================================================
# R9 — no bypass flag in shipped code; instruction install refuses links
# =====================================================================
needle = "dangerously" + "-" + "bypass" + "-hook-trust"
shipped = [os.path.join(ART, x) for x in ("voidloom/cli.py", "voidloom/_agent_context.py",
                                          "hooks/session_start_inject.py",
                                          "organs/voidloom_hooks_status.py",
                                          "organs/voidloom_instructions.py",
                                          "organs/voidloom_heartbeat.py",
                                          "organs/voidloom_preamble.py", "organs/voidloom_canary.py")]
hits = {}
for f in shipped + [os.path.join(ART, "harness", x) for x in os.listdir(os.path.join(ART, "harness"))]:
    txt = Path(f).read_text(encoding="utf-8", errors="replace")
    code_hits = [n for n, ln in enumerate(txt.splitlines(), 1)
                 if needle in ln and not ln.strip().startswith("#")]
    if code_hits:
        hits[os.path.relpath(f, ART)] = code_hits
canary_file = "harness/canary_r8_notice.py"
canary_txt = Path(ART, canary_file).read_text(encoding="utf-8") if Path(ART, canary_file).exists() else ""
note("R9_bypass_flag_appears_in_NO_shipped_code_and_ONLY_the_named_canary",
     set(hits.keys()) <= {canary_file} and "WARNING" in canary_txt and "canary_r8_notice.py" in canary_txt,
     {"hits": hits})
# instruction-file install refuses symlink AND hardlink targets (the property, both mechanisms)
b = md("r9_links_")
E9 = env_for(b)
Path(E9["HOME"], ".agents").mkdir()
victim = Path(b) / "victim.md"; victim.write_bytes(b"THE USER'S FILE\n")
os.symlink(str(victim), str(Path(E9["HOME"], ".agents", "AGENTS.md")))
rc1, _, _, d1 = vl(["instructions", "install", "--yes", "--json"], E9, cwd=b)
os.unlink(str(Path(E9["HOME"], ".agents", "AGENTS.md")))
os.link(str(victim), str(Path(E9["HOME"], ".agents", "AGENTS.md")))
rc2, _, _, d2 = vl(["instructions", "install", "--yes", "--json"], E9, cwd=b)
codes = [((d or {}).get("data") or {}).get("targets", [{}])[0].get("refusal_code") for d in (d1, d2)]
types = [((d or {}).get("data") or {}).get("targets", [{}])[0].get("file_type") for d in (d1, d2)]
# THE LINK IS THE MORE SPECIFIC TYPE (P8): a symlink is refused as IS_SYMLINK, whose
# sentence names the resolved target; a hardlink as HAS_OTHER_NAMES.  One property.
reasons = [((d or {}).get("data") or {}).get("targets", [{}])[0].get("blocked") or "" for d in (d1, d2)]
note("R9_instruction_install_REFUSES_symlink_and_hardlink_targets_victim_intact",
     rc1 == 3 and rc2 == 3 and codes[0] == "IS_SYMLINK" and types[0] == "symlink"
     and str(victim) in reasons[0]
     and codes[1] == "HAS_OTHER_NAMES"
     and victim.read_bytes() == b"THE USER'S FILE\n"
     and not any(Path(E9["CODEX_HOME"], "AGENTS.md").exists() for _ in [0]),
     {"rc": [rc1, rc2], "codes": codes, "nothing_else_written": not Path(E9["CODEX_HOME"], "AGENTS.md").exists()})
# the instructions round trip + marker-block element-level undo
b = md("r9_instr_")
EI = env_for(b)
Path(EI["CODEX_HOME"], "AGENTS.md").write_text("user rules\n")
Path(EI["CLAUDE_CONFIG_DIR"], "CLAUDE.md").write_bytes(b"")
pre_codex = Path(EI["CODEX_HOME"], "AGENTS.md").read_bytes()
rc1, _, _, d1 = vl(["instructions", "install", "--yes", "--json"], EI, cwd=b)
notice = Path(EI["HOME"], ".agents", "AGENTS.md").read_text() if Path(EI["HOME"], ".agents", "AGENTS.md").exists() else ""
rc2, _, _, d2 = vl(["instructions", "uninstall", "--yes", "--json"], EI, cwd=b)
# THE KEY IS THE ORGAN'S, NOT A LITERAL (A5 / P10): read MARKER_PREFIX from
# voidloom_instructions at run time.  Council #12: the notice keys on the ARRIVAL
# line, which `voidloom preamble` (the file-tier emit) must NEVER contain and the
# hook emit always must.  Both halves are asserted; RM21 goes RED on either.
_pfx_src = Path(ORGANS, "voidloom_instructions.py").read_text(encoding="utf-8")
_mpfx = re.search(r'^MARKER_PREFIX\s*=\s*"([^"]+)"', _pfx_src, flags=re.M)
_prefix = _mpfx.group(1) if _mpfx else None
_rcp, _outp, _, _dp = vl(["preamble", "--json"], EI, cwd=b)
_ptext = (((_dp or {}).get("data") or {}).get("text") or ((_dp or {}).get("data") or {}).get("preamble") or "")
_file_tier_has_key = bool(_prefix) and any(l.startswith(_prefix) for l in _ptext.split("\n"))
_hook_env = json.dumps({"session_id": "suite-r8a", "transcript_path": str(Path(b, ".claude", "projects", "x", "suite-r8a.jsonl")), "source": "startup"})
try:
    _hp = subprocess.run([sys.executable, os.path.join(ART, 'hooks', 'session_start_inject.py')],
                         input=_hook_env, capture_output=True, text=True, timeout=90,
                         env={**EI, "VOIDLOOM_HEARTBEAT_DIR": str(Path(b, "hb_r8a"))})
    _hctx = (json.loads(_hp.stdout or "{}").get("hookSpecificOutput") or {}).get("additionalContext", "")
except Exception as _e:  # the hook is the thing under test; a crash is a RED, not a skip
    _hctx = ""
_hook_tier_has_key = bool(_prefix) and any(l.startswith(_prefix) for l in _hctx.split("\n"))
note("R8a_notice_key_absent_from_file_tier_emit_and_present_in_hook_emit",
     bool(_prefix) and _rcp == 0 and (not _file_tier_has_key) and _hook_tier_has_key and _prefix in notice,
     {"prefix": _prefix, "file_tier_has_key": _file_tier_has_key, "hook_tier_has_key": _hook_tier_has_key})
note("R9_instructions_install_then_uninstall_round_trips_and_removes_created_dir",
     rc1 == 0 and rc2 == 0 and bool(_prefix) and _prefix in notice
     and "/hooks" in notice and "voidloom doctor" in notice
     and Path(EI["CODEX_HOME"], "AGENTS.md").read_bytes() == pre_codex
     and Path(EI["CLAUDE_CONFIG_DIR"], "CLAUDE.md").read_bytes() == b""
     and not Path(EI["HOME"], ".agents").exists(),
     {"rc": [rc1, rc2], "notice_bytes": len(notice)})
rc1, _, _, d1 = vl(["instructions", "install", "--yes", "--json"], EI, cwd=b)
with open(Path(EI["CODEX_HOME"], "AGENTS.md"), "a") as fh:
    fh.write("\n# user rule added later\n")
rc2, _, _, d2 = vl(["instructions", "uninstall", "--yes", "--json"], EI, cwd=b)
rows = (((d2 or {}).get("data") or {}).get("applied") or {}).get("applied") or []
codex_row = [r for r in rows if r["path"].endswith(".codex/AGENTS.md")]
after = Path(EI["CODEX_HOME"], "AGENTS.md").read_text()
note("R9_instructions_marker_block_removed_element_level_when_the_file_changed",
     rc2 == 0 and codex_row and codex_row[0]["result"] == "MARKER_REMOVED"
     and codex_row[0]["action"] == "remove_marker_block"
     and after == "user rules\n\n# user rule added later\n",
     {"codex_row": (codex_row[0]["action"], codex_row[0]["result"]) if codex_row else None})
# negative control: uninstall with nothing installed and no receipt refuses to claim a clean undo
b = md("r9_nothing_")
EN = env_for(b)
rc, out, err, doc = vl(["instructions", "uninstall", "--yes", "--json"], EN, cwd=b)
note("R9_negative_uninstall_without_receipt_refuses_rc3", rc == 3 and (doc or {}).get("status") == "refused",
     {"rc": rc, "code": (doc or {}).get("code")})

# =====================================================================
# R11 — MECHANISMS ARE VERBS, IN THE CODE: each undo touches only its mechanism
# =====================================================================
def install_both(box: str) -> Dict[str, str]:
    E_ = env_for(box)
    Path(E_["CLAUDE_CONFIG_DIR"], "settings.json").write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
    Path(E_["CODEX_HOME"], "AGENTS.md").write_text("user rules\n")
    Path(E_["CLAUDE_CONFIG_DIR"], "CLAUDE.md").write_text("# user\n")
    rcs = [vl(["hooks", "install", "--platform", "codex", "--yes", "--json"], E_, cwd=box)[0],
           vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E_, cwd=box)[0],
           vl(["instructions", "install", "--yes", "--json"], E_, cwd=box)[0]]
    assert rcs == [0, 0, 0], rcs
    return E_

def marker_in(path: str) -> bool:
    return Path(path).exists() and "# >>> VOIDLOOM BEGIN >>>" in Path(path).read_text(errors="replace")

# hooks uninstall leaves the instructions mechanism untouched (P1: it DELETED ~/.agents/AGENTS.md)
b = md("r11_hooks_")
E11 = install_both(b)
_files = [str(Path(E11["HOME"], ".agents", "AGENTS.md")), str(Path(E11["CODEX_HOME"], "AGENTS.md")),
          str(Path(E11["CLAUDE_CONFIG_DIR"], "CLAUDE.md"))]
_pre = {f: Path(f).read_bytes() for f in _files}
rc, out, err, doc = vl(["hooks", "uninstall", "--yes", "--json"], E11, cwd=b)
rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
rci, _, _, di = vl(["instructions", "status", "--json"], E11, cwd=b)
rch, _, _, dh = vl(["hooks", "status", "--json"], E11, cwd=b)
note("R11_hooks_uninstall_undoes_ONLY_hook_mechanisms_instructions_stay_installed",
     rc == 0 and (doc or {}).get("code") == "uninstall_applied" and len(rows) == 2
     and {r.get("result") for r in rows} <= {"RESTORED", "DELETED", "ELEMENT_REMOVED"}
     and all(str(Path(E11["HOME"], ".agents")) not in r["path"] and not r["path"].endswith("AGENTS.md")
             and not r["path"].endswith("CLAUDE.md") for r in rows)
     and ((di or {}).get("data") or {}).get("installed_count") == 3
     and all(Path(f).read_bytes() == _pre[f] for f in _files)
     and [r["installed"] for r in ((dh or {}).get("data") or {}).get("platforms", [])
          if r["id"] in ("codex", "claude-code")] == [False, False],
     {"rc": rc, "undo_rows": [(r["path"][-22:], r.get("result")) for r in rows],
      "instructions_installed_after": ((di or {}).get("data") or {}).get("installed_count"),
      "instruction_files_byte_identical": all(Path(f).read_bytes() == _pre[f] for f in _files)})
# the mirror: instructions uninstall leaves both hooks installed
b = md("r11_instr_")
E11b = install_both(b)
_hj = Path(E11b["CODEX_HOME"], "hooks.json").read_bytes(); _sj = Path(E11b["CLAUDE_CONFIG_DIR"], "settings.json").read_bytes()
rc, out, err, doc = vl(["instructions", "uninstall", "--yes", "--json"], E11b, cwd=b)
rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
rch, _, _, dh = vl(["hooks", "status", "--json"], E11b, cwd=b)
rci, _, _, di = vl(["instructions", "status", "--json"], E11b, cwd=b)
note("R11_instructions_uninstall_undoes_ONLY_instruction_mechanisms_hooks_stay_installed",
     rc == 0 and len(rows) == 3 and all(r["path"].endswith(("AGENTS.md", "CLAUDE.md")) for r in rows)
     and Path(E11b["CODEX_HOME"], "hooks.json").read_bytes() == _hj
     and Path(E11b["CLAUDE_CONFIG_DIR"], "settings.json").read_bytes() == _sj
     and [r["installed"] for r in ((dh or {}).get("data") or {}).get("platforms", [])
          if r["id"] in ("codex", "claude-code")] == [True, True]
     and ((di or {}).get("data") or {}).get("installed_count") == 0,
     {"rc": rc, "undo_rows": [(r["path"][-22:], r.get("result")) for r in rows],
      "hooks_installed_after": [(r["id"], r["installed"]) for r in ((dh or {}).get("data") or {}).get("platforms", [])
                                if r["id"] in ("codex", "claude-code")]})

# =====================================================================
# R12 — a single-platform user is served
# =====================================================================
for only in ("claude", "codex"):
    b = md(f"r12_{only}_only_")
    E12 = env_for(b)
    absent_home = E12["CODEX_HOME"] if only == "claude" else E12["CLAUDE_CONFIG_DIR"]
    present_file = (Path(E12["CLAUDE_CONFIG_DIR"], "CLAUDE.md") if only == "claude"
                    else Path(E12["CODEX_HOME"], "AGENTS.md"))
    shutil.rmtree(absent_home)
    rc_d, _, _, d_d = vl(["instructions", "install", "--dry-run", "--json"], E12, cwd=b)
    rc, out, err, doc = vl(["instructions", "install", "--yes", "--json"], E12, cwd=b)
    rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
    results = {(r.get("id"), r.get("kind")): r.get("result") for r in rows}
    skipped = [r for r in rows if r.get("result") == "SKIPPED"]
    rcs, _, _, ds = vl(["instructions", "status", "--json"], E12, cwd=b)
    note(f"R12_{only}_only_home_gets_the_canonical_file_and_its_own_import_line_absent_platform_SKIPPED",
         rc_d == 0 and (d_d or {}).get("code") == "instructions_install_plan"
         and rc == 0 and (doc or {}).get("code") == "instructions_install_installed"
         and sum(1 for r in rows if r.get("result") == "WRITTEN" and r.get("kind") in ("create", "append")) == 2
         and len(skipped) == 1 and "not installed on this machine" in (skipped[0].get("reason") or "")
         and Path(E12["HOME"], ".agents", "AGENTS.md").exists() and marker_in(str(present_file))
         and not Path(absent_home).exists()
         and ((ds or {}).get("data") or {}).get("installed_count") == 2
         and len(((d_d or {}).get("data") or {}).get("skipped_platforms") or []) == 1,
         {"rc": [rc_d, rc], "results": {f"{k[0]}:{k[1]}": v for k, v in results.items()},
          "absent_home_still_absent": not Path(absent_home).exists(),
          "installed_count": ((ds or {}).get("data") or {}).get("installed_count")})

# =====================================================================
# R13 — undo removes ONLY what we wrote: our marker AND the receipt identity
# =====================================================================
# a look-alike: our element re-pointed at ANOTHER brain's copy of this hook
b = md("r13_lookalike_")
E13 = env_for(b)
t13 = Path(E13["CLAUDE_CONFIG_DIR"]) / "settings.json"
t13.write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E13, cwd=b)
d13 = json.loads(t13.read_text())
d13["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "python3 /opt/other-brain/hooks/session_start_inject.py"
t13.write_bytes((json.dumps(d13, indent=2) + "\n").encode()); look = t13.read_bytes()
rc, out, err, doc = vl(["hooks", "uninstall", "--yes", "--json"], E13, cwd=b)
acts = ((doc or {}).get("data") or {}).get("actions") or []
note("R13_element_undo_refuses_a_look_alike_with_our_basename_but_another_brains_path",
     rc == 3 and (doc or {}).get("code") == "uninstall_blocked" and len(acts) == 1
     and acts[0].get("blocked_code") == "ELEMENT_NOT_OURS" and acts[0].get("manual_removal")
     and t13.read_bytes() == look
     and "/opt/other-brain/" in json.dumps(acts[0].get("element_match", {}).get("look_alikes")),
     {"rc": rc, "code": (doc or {}).get("code"), "blocked_code": acts[0].get("blocked_code") if acts else None,
      "file_intact": t13.read_bytes() == look})
# two elements carrying our marker AND our command, neither equal to the receipt's -> AMBIGUOUS (RM09)
b = md("r13_ambig_")
E13b = env_for(b)
t13b = Path(E13b["CLAUDE_CONFIG_DIR"]) / "settings.json"
t13b.write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E13b, cwd=b)
d13b = json.loads(t13b.read_text())
ours_el = d13b["hooks"]["SessionStart"][0]
twin1 = json.loads(json.dumps(ours_el)); twin1["hooks"][0]["timeout"] = 30
twin2 = json.loads(json.dumps(ours_el)); twin2["hooks"][0]["timeout"] = 60
d13b["hooks"]["SessionStart"] = [twin1, twin2]
t13b.write_bytes((json.dumps(d13b, indent=2) + "\n").encode()); amb = t13b.read_bytes()
rc, out, err, doc = vl(["hooks", "uninstall", "--yes", "--json"], E13b, cwd=b)
acts = ((doc or {}).get("data") or {}).get("actions") or []
note("R13_element_undo_refuses_when_two_of_ours_match_and_neither_is_the_receipts",
     rc == 3 and acts and acts[0].get("blocked_code") == "ELEMENT_AMBIGUOUS" and t13b.read_bytes() == amb,
     {"rc": rc, "blocked_code": acts[0].get("blocked_code") if acts else None, "file_intact": t13b.read_bytes() == amb})
# the LEGITIMATE case (negative control): the user added a timeout to OUR element -> still removed
b = md("r13_legit_")
E13c = env_for(b)
t13c = Path(E13c["CLAUDE_CONFIG_DIR"]) / "settings.json"
t13c.write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
vl(["hooks", "install", "--platform", "claude", "--yes", "--json"], E13c, cwd=b)
d13c = json.loads(t13c.read_text()); d13c["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 30
t13c.write_bytes((json.dumps(d13c, indent=2) + "\n").encode())
rc, out, err, doc = vl(["hooks", "uninstall", "--yes", "--json"], E13c, cwd=b)
rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
note("R13_negative_control_our_element_edited_by_the_user_is_still_removed_by_receipt_identity",
     rc == 0 and rows and rows[0].get("result") == "ELEMENT_REMOVED"
     and rows[0].get("matched_by") == "marker_and_receipt_command"
     and "session_start_inject" not in t13c.read_text()
     and json.loads(t13c.read_bytes()).get("hooks", {}).get("SessionStart", []) == [],
     {"rc": rc, "result": rows[0].get("result") if rows else None, "matched_by": rows[0].get("matched_by") if rows else None})
# the TEXT twin: a marker block that is not the receipt's block (another brain's) is not removed
b = md("r13_text_")
E13d = env_for(b)
Path(E13d["CODEX_HOME"], "AGENTS.md").write_text("user rules\n")
vl(["instructions", "install", "--yes", "--json"], E13d, cwd=b)
_ag = Path(E13d["CODEX_HOME"], "AGENTS.md")
_txt = _ag.read_text()
_other = "# >>> VOIDLOOM BEGIN >>>\nANOTHER BRAIN'S NOTICE\n# <<< VOIDLOOM END <<<\n"
_ours_block = _txt[_txt.index("# >>> VOIDLOOM BEGIN >>>"):_txt.index("# <<< VOIDLOOM END <<<") + len("# <<< VOIDLOOM END <<<\n")]
_ag.write_text(_txt.replace(_ours_block, _other) + "# later\n")     # ours gone, a look-alike present, file changed
before_txt = _ag.read_bytes()
rc, out, err, doc = vl(["instructions", "uninstall", "--yes", "--json"], E13d, cwd=b)
acts = ((doc or {}).get("data") or {}).get("actions") or []
crow13 = [a for a in acts if a["path"].endswith(".codex/AGENTS.md")]
note("R13_marker_block_undo_refuses_a_look_alike_block_that_is_not_the_receipts",
     rc == 3 and crow13 and crow13[0].get("blocked_code") == "MARKER_NOT_OURS" and _ag.read_bytes() == before_txt,
     {"rc": rc, "blocked_code": crow13[0].get("blocked_code") if crow13 else None, "file_intact": _ag.read_bytes() == before_txt})

# MB05 (reflector iter 2): the receipt's command decides identity, not where the brain is NOW.
# Install with --home <relocated brain> (the receipt records that command), edit our element
# so deep-equal cannot match, undo with the DEFAULT home: the fallback must match by the
# RECEIPT's command (ELEMENT_REMOVED, marker_and_receipt_command), never read our own element
# as ELEMENT_NOT_OURS because hook_command(home) now points elsewhere.
b = md("r13_receipt_cmd_")
E13e = env_for(b)
t13e = Path(E13e["CLAUDE_CONFIG_DIR"]) / "settings.json"
t13e.write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
_reloc = os.path.join(b, "relocated-brain")
rc_i, _, _, d_i = vl(["hooks", "install", "--platform", "claude", "--yes", "--json", "--home", _reloc], E13e, cwd=b)
d13e = json.loads(t13e.read_text())
_cmd_in_file = d13e["hooks"]["SessionStart"][0]["hooks"][0]["command"]
d13e["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 30
t13e.write_bytes((json.dumps(d13e, indent=2) + "\n").encode())
rc, out, err, doc = vl(["hooks", "uninstall", "--yes", "--json"], E13e, cwd=b)
rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
acts = ((doc or {}).get("data") or {}).get("actions") or []
note("R13_element_undo_identifies_OUR_element_by_the_RECEIPTS_command_after_the_brain_home_moved",
     rc_i == 0 and _reloc in _cmd_in_file and rc == 0 and rows and rows[0].get("result") == "ELEMENT_REMOVED"
     and rows[0].get("matched_by") == "marker_and_receipt_command"
     and (acts[0].get("own_command") if acts else None) == _cmd_in_file
     and "session_start_inject" not in t13e.read_text(),
     {"install_rc": rc_i, "receipt_command": _cmd_in_file[-48:], "undo_rc": rc, "result": rows[0].get("result") if rows else None,
      "matched_by": rows[0].get("matched_by") if rows else None, "own_command_used": (acts[0].get("own_command") if acts else None) == _cmd_in_file})
# MB06 (reflector iter 2): TWO exact copies of our block (hand-duplicated) -> MARKER_AMBIGUOUS, file intact
b = md("r13_text_dup_")
E13f = env_for(b)
Path(E13f["CLAUDE_CONFIG_DIR"], "CLAUDE.md").write_text("# user\n")
vl(["instructions", "install", "--yes", "--json"], E13f, cwd=b)
_cm = Path(E13f["CLAUDE_CONFIG_DIR"], "CLAUDE.md")
_ctxt = _cm.read_text()
_blk = _ctxt[_ctxt.index("# >>> VOIDLOOM BEGIN >>>"):_ctxt.index("# <<< VOIDLOOM END <<<") + len("# <<< VOIDLOOM END <<<\n")]
_cm.write_text(_ctxt + _blk)                        # a second exact copy of OUR block
_dup_before = _cm.read_bytes()
rc, out, err, doc = vl(["instructions", "uninstall", "--yes", "--json"], E13f, cwd=b)
acts = ((doc or {}).get("data") or {}).get("actions") or []
_crow = [a for a in acts if a["path"].endswith(".claude/CLAUDE.md")]
note("R13_marker_block_undo_refuses_TWO_exact_copies_of_our_block_as_AMBIGUOUS_file_intact",
     rc == 3 and _crow and _crow[0].get("blocked_code") == "MARKER_AMBIGUOUS" and _cm.read_bytes() == _dup_before
     and _dup_before.count(b"# >>> VOIDLOOM BEGIN >>>") == 2,
     {"rc": rc, "blocked_code": _crow[0].get("blocked_code") if _crow else None, "file_intact": _cm.read_bytes() == _dup_before,
      "copies": _dup_before.count(b"# >>> VOIDLOOM BEGIN >>>")})
# MB11 (reflector iter 2): the unbounded-open class at ONE canary site -- a FIFO at
# ~/.codex/config.toml (the confound scan's fallback-filenames read, reached by
# `canary --self-check` through run_control) must not hang the verb.
b = md("r10_canary_fifo_cfg_")
E11c = env_for(b)
mkfifo(os.path.join(E11c["CODEX_HOME"], "config.toml"))
_t0c = time.time()
rc_cf, _, err_cf, d_cf = vl(["canary", "--self-check", "--json"], E11c, cwd=b, timeout=120)
_el_cf = round(time.time() - _t0c, 1)
# C5: rc 4 is PASS_WITH_UNMEASURABLE -- some checks did not run. This asserted
# rc == 0, which encoded "unmeasurable is fine"; the point of the check here is
# that the FIFO does not HANG the verb, so both 0 and 4 are a pass and 1/3 are not.
note("R10_canary_self_check_completes_with_a_FIFO_at_codex_config_toml_bounded_reader",
     rc_cf in (0, 4) and _el_cf < 60 and ((d_cf or {}).get("data") or {}).get("failed") == [],
     {"rc": rc_cf, "elapsed_s": _el_cf, "failed": ((d_cf or {}).get("data") or {}).get("failed"),
      "checks": len(((d_cf or {}).get("data") or {}).get("checks", []))})

# =====================================================================
# N1 (Crucible 107) — THE CELL THIS SUITE NEVER HAD.
#   22 uninstall sites here, ZERO passing `--platform`, so the ALL-platform
#   removal was the seed case and the flag C1 repaired had no coverage at all.
#   Four seats reached the defect behind that flag from four directions.
# =====================================================================
_np = md("n1_platform_")
_ENP = env_for(_np)
Path(_ENP["CLAUDE_CONFIG_DIR"], "settings.json").write_text('{\n  "hooks": {}\n}\n',
                                                            encoding="utf-8")
for _plat in ("codex", "claude"):
    vl(["hooks", "install", "--platform", _plat, "--yes", "--json"], _ENP, cwd=_np)
_codex_hook = Path(_ENP["CODEX_HOME"], "hooks.json")
_codex_before = _codex_hook.read_bytes() if _codex_hook.exists() else None
_rc_np, _, _, _d_np = vl(["hooks", "uninstall", "--platform", "claude", "--yes", "--json"],
                         _ENP, cwd=_np)
_codex_after = _codex_hook.read_bytes() if _codex_hook.exists() else None
note("N1_uninstall_platform_claude_leaves_the_codex_hook_byte_identical",
     _codex_before is not None and _codex_after == _codex_before,
     {"rc": _rc_np, "codex_hook_existed": _codex_before is not None,
      "codex_hook_survives": _codex_after is not None,
      "byte_identical": _codex_after == _codex_before,
      "out_of_domain": ((_d_np or {}).get("data") or {}).get("out_of_domain_count")})
# and the mirror, so a fix that simply refuses every platform cannot pass
_rc_np2, _, _, _d_np2 = vl(["hooks", "uninstall", "--platform", "codex", "--yes", "--json"],
                           _ENP, cwd=_np)
note("N1_uninstall_platform_codex_then_removes_its_own_hook",
     _rc_np2 == 0 and not _codex_hook.exists(),
     {"rc": _rc_np2, "codex_hook_gone": not _codex_hook.exists()})

# =====================================================================
# R10 — no bare mkdtemp anywhere in the shipped set OR the harness
# =====================================================================
import ast
bare: Dict[str, List[int]] = {}
_scan_set = (shipped + [os.path.join(ART, "harness", x) for x in os.listdir(os.path.join(ART, "harness")) if x.endswith(".py")]
             + [os.path.join(ART, "voidloom", "tests", "test_voidloom.py")])   # the SHIPPED SET includes the package tests (CD10)
for f in _scan_set:
    try:
        tree = ast.parse(Path(f).read_text(encoding="utf-8"))
    except SyntaxError as e:
        bare[os.path.relpath(f, ART)] = [-1]
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = n.func
            dotted = None
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                dotted = f"{fn.value.id}.{fn.attr}"
            if dotted in ("tempfile.mkdtemp", "tempfile.mkstemp", "tempfile.TemporaryDirectory",
                          "tempfile.NamedTemporaryFile"):
                if not any(k.arg == "dir" for k in n.keywords):
                    bare.setdefault(os.path.relpath(f, ART), []).append(n.lineno)
note("R10_no_bare_tempfile_call_in_shipped_set_or_harness", not bare and len(_scan_set) >= 12,
     {"bare": bare, "files_scanned": len(_scan_set),
      "includes_package_tests": any(f.endswith("test_voidloom.py") for f in _scan_set)})
# the organ self-checks still PASS with the class closed (F1 now fails on a sibling residue)
b = md("r10_sc_")
ES = env_for(b)
# SEQUENTIAL, each bounded; a TIMEOUT is reported as UNMEASURED (rc -999), never
# mistaken for a violation of the property (A8).  The hang class itself (an
# unbounded open of a FIFO reached by a tree walk) is closed in the organs.
_t_h = time.time(); rc_h, _, _, d_h = vl(["hooks", "--self-check", "--json"], ES, cwd=b, timeout=900); _t_h = time.time() - _t_h
_t_c = time.time(); rc_c, _, _, d_c = vl(["canary", "--self-check", "--json"], ES, cwd=b, timeout=900); _t_c = time.time() - _t_c
_t_p = time.time(); rc_p, _, _, d_p = vl(["preamble", "--selftest", "--json", "--work-db", os.path.join(b, "work_copy.db")], ES, cwd=b, timeout=900); _t_p = time.time() - _t_p
fails = {"hooks": ((d_h or {}).get("data") or {}).get("failed") if rc_h != -999 else "UNMEASURED (timeout)",
         "canary": ((d_c or {}).get("data") or {}).get("failed") if rc_c != -999 else "UNMEASURED (timeout)",
         "preamble": ((d_p or {}).get("data") or {}).get("failed") if rc_p != -999 else "UNMEASURED (timeout)"}
_r8a = next((c for c in ((d_p or {}).get("data") or {}).get("checks", []) if str(c.get("id", "")).startswith("R8a_")), {})
note("R8a_preamble_selftest_binds_the_notice_key_to_the_hook_only_arrival_line",
     _r8a.get("ok") is True and (_r8a.get("detail") or {}).get("notice_carries_prefix") is True
     and (_r8a.get("detail") or {}).get("key_in_file_tier_emit") is False
     and (_r8a.get("detail") or {}).get("key_in_hook_tier_emit") is True,
     {"first_line": (_r8a.get("detail") or {}).get("first_line"), "prefix": (_r8a.get("detail") or {}).get("notice_prefix"),
      "selftest_seconds": round(_t_p, 1)})
f1 = next((c for c in ((d_h or {}).get("data") or {}).get("checks", []) if str(c.get("id", "")).startswith("F1")), {})
note("R10_organ_self_checks_PASS_and_F1_reports_no_sibling_residue",
     rc_h == 0 and rc_c in (0, 4) and rc_p == 0 and fails["hooks"] == [] and fails["canary"] == []
     and fails["preamble"] == []
     and (f1.get("detail") or {}).get("SAME_CLASS_STILL_OPEN_IN_THE_SIBLINGS") == {},
     {"rc": [rc_h, rc_c, rc_p], "seconds": [round(_t_h, 1), round(_t_c, 1), round(_t_p, 1)], "failed": fails, "checks": [len(((d_h or {}).get("data") or {}).get("checks", [])),
                                                             len(((d_c or {}).get("data") or {}).get("checks", [])),
                                                             len(((d_p or {}).get("data") or {}).get("checks", []))],
      "F1_siblings": (f1.get("detail") or {}).get("SAME_CLASS_STILL_OPEN_IN_THE_SIBLINGS")})
# I5: the mutating-IO census over the widened shipped set has ZERO unguarded sites
i5 = next((c for c in ((d_h or {}).get("data") or {}).get("checks", []) if str(c.get("id", "")).startswith("I5_")), {})
note("R6_I5_every_mutating_call_in_the_widened_shipped_set_is_inside_a_named_guard",
     i5.get("ok") is True and (i5.get("detail") or {}).get("unguarded") == []
     and (i5.get("detail") or {}).get("files_scanned", 0) >= 8,
     {"files_scanned": (i5.get("detail") or {}).get("files_scanned"),
      "unguarded": (i5.get("detail") or {}).get("unguarded")})

# =====================================================================
# R6 (P7) — a directory swapped in AT THE os.open moment is REFUSED, not ERROR
# =====================================================================
# The reflector's O7 shape: every path-based defence has passed; the kernel
# answers EISDIR at the open.  The user's directory is intact either way; what
# was wrong was the LABEL (ERROR / rc 1 vs REFUSED naming the object / rc 3).
b = md("r6_eisdir_")
E6 = env_for(b)
named6 = Path(E6["CODEX_HOME"]) / "hooks.json"
named6.write_bytes((json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo USER"}]}]}}, indent=2) + "\n").encode())
o7 = f'''
import sys, json, os
sys.path.insert(0, {ORGANS!r})
import voidloom_hooks_status as hs
_real = os
class _P:
    def __getattr__(self, n): return getattr(_real, n)
    def open(self, path, flags, *a, **k):
        if isinstance(path, str) and path.endswith("hooks.json") and (flags & (_real.O_WRONLY | _real.O_RDWR)):
            _real.unlink(path); _real.mkdir(path); open(_real.path.join(path, "USERS_STUFF"), "wb").write(b"x")
        return _real.open(path, flags, *a, **k)
hs.os = _P()
plan = hs.install_plan("codex", {b!r}, {b!r})
res = hs.install_execute(plan)
print("O7" + json.dumps({{"result": res.get("result"), "refused_by": res.get("refused_by"),
                         "dir_intact": _real.path.isdir({str(named6)!r}) and _real.path.exists({str(named6)!r} + "/USERS_STUFF")}}))
'''
p7 = subprocess.run([sys.executable, "-c", o7], capture_output=True, text=True, env=E6, cwd=b, timeout=60)
o7j = json.loads(p7.stdout.split("O7")[-1]) if "O7" in p7.stdout else {}
note("R6_directory_swapped_in_at_the_open_is_REFUSED_naming_the_object_not_ERROR",
     o7j.get("result") == "REFUSED" and o7j.get("refused_by") == "EISDIR_at_open" and o7j.get("dir_intact") is True,
     {**o7j, "stderr": p7.stderr[-120:]})

# =====================================================================
# R6 — the 16-shape battery against the INSTALL writer, on this root
# =====================================================================
p = subprocess.run([sys.executable, os.path.join(ART, "harness", "property_battery_16_install.py"),
                    ART, os.path.join(ROOT, "battery")], capture_output=True, text=True,
                   env=env_for(md("r6_")), timeout=900)
try:
    summ = json.loads(p.stdout[p.stdout.rindex("{\n \"scratch_root\""):p.stdout.rindex("}") + 1])
except Exception:
    summ = {"_err": p.stdout[-800:] + p.stderr[-400:]}
note("R6_property_battery_16_install_holds_on_ext4",
     p.returncode == 0 and summ.get("held") == summ.get("total") and summ.get("total", 0) >= 16
     and summ.get("inode_numbers_recycled") is True,
     {"held": summ.get("held"), "total": summ.get("total"), "violated": summ.get("violated"),
      "inode_reuse": summ.get("inode_numbers_recycled")})

# =====================================================================
# summary — and no FIFO left under the scratch root (A9 class)
# =====================================================================
_sweep_fifos()
_left = [os.path.join(dp, f) for dp, dn, fn in os.walk(ROOT) for f in fn
         if stat.S_ISFIFO(os.lstat(os.path.join(dp, f)).st_mode)]
for f in _left:
    try:
        os.unlink(f)
    except OSError:
        pass
note("A9_no_FIFO_left_on_disk_by_this_suite_or_the_battery_it_ran", _left == [],
     {"fifos_found_and_removed": len(_left), "registered_by_suite": len(_FIFOS)})
held = sum(1 for v in R.values() if v["held"])
# CRUCIBLE-3 D2-F4: `held == len(R)` is VACUOUSLY TRUE at len(R) == 0, and `R` is
# populated only by checks that actually EXECUTED -- so `total` is what ran, never
# what the suite declares. Measured: `--only THIS_CHECK_DOES_NOT_EXIST` printed
# "0/0 CHECKS HELD" and exited 0 with NOTHING executed, and `--only <one name>`
# printed "1/1 CHECKS HELD" rc 0 with 90 checks unexecuted. Neither output stated
# how many checks exist. An acceptance suite that passes over zero observations is
# the same defect this cycle exists to remove, living in the instrument that grades
# the fix.
#
# Two repairs, both cheap and both true without a check registry:
#   1. ZERO CHECKS IS A FAILURE, never a pass. There is no observation to hold.
#   2. A FILTERED RUN SAYS SO, in the summary line and in the envelope, so a
#      "1/1 CHECKS HELD" can never be read as the suite having passed.
_scope = "full run" if not ONLY else f"FILTERED RUN --only {ONLY!r} — this is NOT the full suite"
_vacuous = (len(R) == 0)
print(json.dumps({"artifacts": ART, "scratch_root": ROOT, "dev": FS_DEV,
                  "inode_numbers_recycled": INODE_REUSE,
                  "held": held, "total": len(R),
                  "scope": _scope, "filtered": bool(ONLY),
                  "vacuous_no_checks_executed": _vacuous,
                  "violated": sorted(k for k, v in R.items() if not v["held"])}, indent=1))
if _vacuous:
    print(f"\n  ===> NO CHECKS EXECUTED — nothing was measured, so nothing held"
          + (f" (--only {ONLY!r} matched no check name)" if ONLY else "") + "\n")
    sys.exit(2)     # not 0: a suite that measured nothing has not passed
print(f"\n  ===> {held}/{len(R)} CHECKS HELD  ({_scope})\n")
sys.exit(0 if held == len(R) else 1)

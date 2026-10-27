#!/usr/bin/env python3
"""C4 — the planner promises what the applier cannot keep.

Acceptance and safety are decided by PROXIES: a difflib opcode classification
standing in for structure, and a lenient parser standing in for the platform's.
Where the applier can still fail, one arm forgets the rollback its sibling does.

    usage: c4_planner_applier_suite.py <package_dir> <scratch_root> [--only NAME]

Every check drives the SHIPPED organ as a subprocess, or imports it and injects
a fault at the exact call the defect lives behind.
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
    sys.exit("usage: c4_planner_applier_suite.py <package_dir> <scratch_root> [--only NAME]")
PKG = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)
ORGAN = os.path.join(PKG, "organs", "voidloom_hooks_status.py")
RESULTS: List[Dict[str, Any]] = []


def note(name: str, held: bool, detail: Any) -> None:
    if ONLY and ONLY not in name:
        return
    RESULTS.append({"name": name, "held": bool(held), "detail": detail})
    print(f"  {name:<58s} {'HELD' if held else 'VIOLATED':<9s} {json.dumps(detail, default=str)[:250]}")


def home_with(tag: str, settings: Any) -> str:
    h = os.path.join(ROOT, tag)
    shutil.rmtree(h, ignore_errors=True)
    os.makedirs(os.path.join(h, ".claude")); os.makedirs(os.path.join(h, ".codex"))
    if settings is not None:
        pathlib.Path(h, ".claude", "settings.json").write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return h


def install(h: str, *extra: str) -> Dict[str, Any]:
    env = {**os.environ, "HOME": h,
           "CLAUDE_CONFIG_DIR": os.path.join(h, ".claude"),
           "CODEX_HOME": os.path.join(h, ".codex"),
           "VOIDLOOM_INSTALL_RECEIPT": os.path.join(h, "receipt.json"),
           "VOIDLOOM_PREIMAGE_STORE": os.path.join(h, "preimages")}
    env.pop("WIZARD_LIBRARY_DB", None)
    p = subprocess.run([sys.executable, ORGAN, "--install", "--platform", "claude",
                        "--yes", "--json", *extra],
                       capture_output=True, text=True, env=env, timeout=300)
    try:
        d = json.loads(p.stdout)
    except Exception:
        d = {"status": "UNPARSEABLE", "raw": p.stdout[:300], "stderr": p.stderr[:300]}
    d["_rc"] = p.returncode
    return d


# ===========================================================================
# C4.1 — ORDINARY CONFIGS INSTALL. The text proxy refuses any file whose last
#        top-level key holds a scalar, while the structural predicate says yes.
# ===========================================================================
SHAPES = {
    "empty_object": {},
    "only_model": {"model": "opusplan"},
    "model_and_permissions": {"model": "opusplan", "permissions": {"allow": ["Bash"]}},
    "trailing_scalar_after": {"permissions": {"allow": ["Bash"]}, "model": "sonnet"},
    "env_block_last": {"env": {"FOO": "bar"}},
    "already_has_other_hook": {"hooks": {"Stop": [
        {"matcher": "*", "hooks": [{"type": "command", "command": "echo hi"}]}]}},
    "empty_sessionstart_array": {"hooks": {"SessionStart": []}},
    "empty_hooks_object": {"hooks": {}},
    "nested_last_key": {"permissions": {"allow": ["Bash"], "deny": []}},
    "two_scalars_last": {"model": "sonnet", "verbose": True},
}
refused: Dict[str, str] = {}
preserved: Dict[str, bool] = {}
for name, doc in SHAPES.items():
    h = home_with(f"c4_1_{name}", doc)
    d = install(h)
    if d.get("status") != "ok":
        refused[name] = ((d.get("data") or {}).get("blockers") or [d.get("status")])[0][:70]
        continue
    # and the user's own data survived, KEY BY KEY -- the property the line diff
    # was reaching for, stated on structure instead of on text.
    after = json.loads(pathlib.Path(h, ".claude", "settings.json").read_text(encoding="utf-8"))
    preserved[name] = all(after.get(k) == v for k, v in doc.items() if k != "hooks")
note("C4_1_ordinary_settings_shapes_all_install",
     not refused and all(preserved.values()),
     {"shapes": len(SHAPES), "refused": refused,
      "user_keys_preserved": sum(1 for v in preserved.values() if v)})

# ===========================================================================
# C4.2 — A REFUSAL A CALLER CAN ACT ON: every blocking refusal carries a typed
#        refusal_code AND the element to paste by hand.
# ===========================================================================
# The first version of this check aimed at a NOT_JSON file -- which is the ONE
# branch that already sets both fields. It reported HELD over the live defect,
# because the pure-insertion branch is the one that sets neither. So the check
# now states the property over EVERY refusal this suite can produce, which is
# what "a refusal a caller can act on" actually means.
REFUSAL_PROBES = {
    "not_json": ("{ not json at all", None),
    "lossy_serialization": ('{"model":"opusplan"}', None),      # no trailing newline, compact
    "not_an_object": ("[1, 2, 3]\n", None),
    "nan_present": ('{\n  "model": "opusplan",\n  "weird": NaN\n}\n', None),
    "unwritable": (None, "readonly"),
}
missing: dict = {}
seen: dict = {}
for pname, (raw, mode) in REFUSAL_PROBES.items():
    hh = home_with(f"c4_2_{pname}", {"model": "opusplan"} if raw is None else None)
    sp = pathlib.Path(hh, ".claude", "settings.json")
    if raw is not None:
        sp.write_text(raw, encoding="utf-8")
    if mode == "readonly":
        os.chmod(sp, 0o444); os.chmod(pathlib.Path(hh, ".claude"), 0o555)
    try:
        dd = install(hh)
    finally:
        if mode == "readonly":
            os.chmod(pathlib.Path(hh, ".claude"), 0o755); os.chmod(sp, 0o644)
    ddd = dd.get("data") or {}
    if dd.get("status") != "refused":
        seen[pname] = f"not refused ({dd.get('status')})"
        continue
    seen[pname] = ddd.get("refusal_code")
    gaps = [f for f, v in (("refusal_code", ddd.get("refusal_code")),
                           ("manual_paste", ddd.get("manual_paste"))) if not v]
    if gaps:
        missing[pname] = gaps
note("C4_2_every_blocking_refusal_carries_a_code_and_a_manual_paste",
     not missing, {"probes": len(REFUSAL_PROBES), "codes": seen, "missing_fields": missing})

# ===========================================================================
# C4.3 — THE ROLLBACK ARM. An OSError during the target write must restore the
#        pre-image, exactly as the read-back-failure arm already does.
#        Fault-injected at the writer, because ENOSPC cannot be produced here
#        without root -- and the injection reproduces the OBSERVED end state:
#        the file truncated to 0 bytes, then the write raising.
# ===========================================================================
h = home_with("c4_3_enospc", {"model": "opusplan", "permissions": {"allow": ["Bash"]}})
tgt = str(pathlib.Path(h, ".claude", "settings.json"))
original = pathlib.Path(tgt).read_bytes()
# TWO CASES, because they have different right answers and the first version of
# this check conflated them. Injecting a PERSISTENT failure also breaks the
# rollback write -- which is faithful to a genuinely full disk, where restoring
# 84 bytes is no more possible than writing them. So:
#   ONE-SHOT  (a transient write error): the pre-image MUST come back.
#   PERSISTENT (the disk is full): the pre-image cannot come back, and the
#              requirement is that it SAYS SO and names the recovery copy.
def _inject(mode: str) -> str:
    return f'''
import errno, json, os, sys
sys.path.insert(0, {os.path.join(PKG, "organs")!r})
import voidloom_hooks_status as H
_real = H.guarded_write_bytes
_fired = []
def _enospc(path, data, **kw):
    if path == {tgt!r} and not (_fired and {mode!r} == "oneshot"):
        _fired.append(1)
        with open(path, "r+b") as f:      # the shipped writer truncates, THEN writes
            f.truncate(0)
        raise OSError(errno.ENOSPC, "No space left on device")
    return _real(path, data, **kw)
H.guarded_write_bytes = _enospc
plan = H.install_plan("claude", os.getcwd())
res = H.install_execute(plan)
print(json.dumps({{"result": res.get("result"),
                   "reason": (res.get("reason") or "")[:200],
                   "rollback": res.get("rollback")}}))
'''
inject = _inject("oneshot")
env = {**os.environ, "HOME": h, "CLAUDE_CONFIG_DIR": os.path.join(h, ".claude"),
       "CODEX_HOME": os.path.join(h, ".codex"),
       "VOIDLOOM_INSTALL_RECEIPT": os.path.join(h, "receipt.json"),
       "VOIDLOOM_PREIMAGE_STORE": os.path.join(h, "preimages")}
env.pop("WIZARD_LIBRARY_DB", None)
p3 = subprocess.run([sys.executable, "-c", inject], capture_output=True, text=True,
                    env=env, cwd=h, timeout=300)
after_bytes = pathlib.Path(tgt).read_bytes() if os.path.exists(tgt) else b""
try:
    r3 = json.loads((p3.stdout or "{}").strip().splitlines()[-1])
except Exception:
    r3 = {"result": "UNPARSEABLE", "stdout": p3.stdout[:200], "stderr": p3.stderr[-200:]}
note("C4_3a_a_transient_OSError_during_the_write_restores_the_preimage",
     after_bytes == original,
     {"result": r3.get("result"), "reason": (r3.get("reason") or "")[:100],
      "bytes_after": len(after_bytes), "bytes_before": len(original),
      "restored": after_bytes == original})

# PERSISTENT: the rollback cannot succeed, so the requirement is honesty --
# the failure is reported and the recovery copy is named and actually exists.
h = home_with("c4_3b_enospc_persistent", {"model": "opusplan", "permissions": {"allow": ["Bash"]}})
tgt = str(pathlib.Path(h, ".claude", "settings.json"))
original = pathlib.Path(tgt).read_bytes()
env = {**os.environ, "HOME": h, "CLAUDE_CONFIG_DIR": os.path.join(h, ".claude"),
       "CODEX_HOME": os.path.join(h, ".codex"),
       "VOIDLOOM_INSTALL_RECEIPT": os.path.join(h, "receipt.json"),
       "VOIDLOOM_PREIMAGE_STORE": os.path.join(h, "preimages")}
env.pop("WIZARD_LIBRARY_DB", None)
p3b = subprocess.run([sys.executable, "-c", _inject("persistent")], capture_output=True,
                     text=True, env=env, cwd=h, timeout=300)
try:
    r3b = json.loads((p3b.stdout or "{}").strip().splitlines()[-1])
except Exception:
    r3b = {"result": "UNPARSEABLE", "stderr": p3b.stderr[-200:]}
rb = r3b.get("rollback") or {}
copy_path = rb.get("preimage_copy")
copy_ok = bool(copy_path) and os.path.exists(copy_path) and \
    pathlib.Path(copy_path).read_bytes() == original
note("C4_3b_an_unrecoverable_write_failure_names_the_intact_copy",
     bool(rb.get("error")) and copy_ok and "ROLLBACK FAILED" in (r3b.get("reason") or ""),
     {"result": r3b.get("result"), "rollback_error": bool(rb.get("error")),
      "preimage_copy_exists_and_matches": copy_ok,
      "reason": (r3b.get("reason") or "")[:120]})

# ===========================================================================
# C4.4 — NOT_WRITABLE is a TYPED BLOCKER at plan time, not an OSError at write
#        time: the plan must not promise a write it cannot perform.
# ===========================================================================
h = home_with("c4_4_ro", {"model": "opusplan"})
ro = pathlib.Path(h, ".claude", "settings.json")
os.chmod(ro, 0o444)
os.chmod(pathlib.Path(h, ".claude"), 0o555)
try:
    d4 = install(h)
finally:
    os.chmod(pathlib.Path(h, ".claude"), 0o755)
    os.chmod(ro, 0o644)
d4d = d4.get("data") or {}
blockers4 = " ".join(d4d.get("blockers") or [])
note("C4_4_an_unwritable_target_is_a_typed_blocker_at_plan_time",
     d4.get("status") == "refused" and d4d.get("refusal_code") == "NOT_WRITABLE",
     {"status": d4.get("status"), "refusal_code": d4d.get("refusal_code"),
      "blocker": blockers4[:110]})

# ===========================================================================
# C4.5 — THE POST-IMAGE IS JSON THE PLATFORM CAN READ. json.dumps emits bare
#        NaN/Infinity; python accepts them and node's JSON.parse does not, so
#        the organ would verify a file Claude Code cannot load.
# ===========================================================================
h = home_with("c4_5_nan", None)
NAN_SRC = '{\n  "model": "opusplan",\n  "weird": NaN\n}\n'
pathlib.Path(h, ".claude", "settings.json").write_text(NAN_SRC, encoding="utf-8")
node = shutil.which("node")
node_before = None
if node:
    node_before = subprocess.run([node, "-e", "JSON.parse(process.argv[1])", NAN_SRC],
                                 capture_output=True, text=True, timeout=120).returncode == 0
d5 = install(h)
d5d = d5.get("data") or {}
raw5 = pathlib.Path(h, ".claude", "settings.json").read_text(encoding="utf-8")
# The first version asserted the file parses in node AFTERWARDS -- but it did not
# parse BEFOREHAND either (measured: node_before False), and we correctly refused
# to touch it. Requiring a clean parse would have demanded that a refusal REPAIR
# a file it declined to write. The property is: refuse, with a code that names
# the cause, and leave the bytes exactly as found.
note("C4_5_a_file_the_platform_cannot_parse_is_refused_with_a_named_cause",
     d5.get("status") == "refused" and d5d.get("refusal_code") == "NOT_STRICT_JSON"
     and raw5 == NAN_SRC,
     {"status": d5.get("status"), "refusal_code": d5d.get("refusal_code"),
      "node_could_parse_it_before": node_before,
      "bytes_unchanged": raw5 == NAN_SRC})

# ===========================================================================
# C4.6 — C3'S DEPENDENT: removing an element we own is an acceptable post-image.
#        Acceptance is structural and direction-agnostic, so superseding the
#        retired hook is no longer refused as "not a pure insertion".
# ===========================================================================
h = home_with("c4_6_supersede", None)
legacy = {"permissions": {"allow": ["Bash"]},
          "hooks": {"SessionStart": [
              {"matcher": "*", "hooks": [{"type": "command",
                                          "command": f"node {h}/.claude/hooks/session-start.mjs"}]},
              {"matcher": "*", "hooks": [{"type": "command",
                                          "command": f"node {h}/.claude/hooks/someone-elses.mjs"}]}]}}
pathlib.Path(h, ".claude", "settings.json").write_text(
    json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
d6 = install(h)
after6 = json.loads(pathlib.Path(h, ".claude", "settings.json").read_text(encoding="utf-8"))
cmds = [x["command"] for g in after6["hooks"]["SessionStart"] for x in g["hooks"]]
# The first version asserted only that the foreign hook survived -- which C3's
# append-only install already satisfied, so it reported HELD at the parent while
# testing nothing C4 adds. The point of C4 here is that the RETIRED element can
# now be REMOVED in the same write.
legacy_gone = not any("session-start.mjs" in c for c in cmds)
current_present = sum(1 for c in cmds if "session_start_inject.py" in c)
note("C4_6_the_retired_element_is_superseded_in_the_same_write",
     d6.get("status") == "ok" and legacy_gone and current_present == 1
     and after6.get("permissions") == {"allow": ["Bash"]}
     and any("someone-elses.mjs" in c for c in cmds),
     {"status": d6.get("status"), "commands": len(cmds),
      "legacy_removed": legacy_gone, "current_hooks": current_present,
      "foreign_hook_survived": any("someone-elses.mjs" in c for c in cmds),
      "permissions_preserved": after6.get("permissions") == {"allow": ["Bash"]}})

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

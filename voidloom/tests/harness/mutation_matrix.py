#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mutation_matrix.py — REVERT EACH FIX ON A COPY, WATCH ITS TEST GO RED, KEEP THE REDS RUNNABLE.

    python3 harness/mutation_matrix.py <artifacts_dir> <scratch_root> [--only M3]

For every fix this round shipped, one mutant reverts it on a COPY of the artifacts
(the live artifacts are never edited), and the probe that guards it is run against
the copy.  A mutant that the probe does not catch is reported as SURVIVED — that is
the finding, not a pass.  The matrix is an AUTHOR'S matrix (it measures what the
author already understood; memory #18344) and is labelled as such.

Each row: id · file · the reverted fix · what a real user would have suffered · probe
verdict on the INTACT artifacts (must be GREEN) · on the MUTANT (must be RED).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

if len(sys.argv) < 3:
    sys.exit("usage: mutation_matrix.py <artifacts_dir> <scratch_root> [--only Mx]")
ART = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)
REAL_SETTINGS_COPY = os.path.join(ART, "preimages_ref", "settings.json.REAL_COPY")

USER_DOC = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo USER-HOOK"}]}]}}


def md(pfx: str) -> str:
    return tempfile.mkdtemp(prefix=pfx, dir=ROOT)


def mutant_copy(mid: str) -> str:
    dst = md(f"mut_{mid}_")
    for sub in ("voidloom", "organs", "hooks", "preimages_ref"):
        shutil.copytree(os.path.join(ART, sub), os.path.join(dst, sub), symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".vl_selfcheck_fixtures"))
    shutil.copy2(os.path.join(ART, "voidloom_manifest.json"), dst)
    # the brain: ONE object under a second name (hardlink) -- read-only use only
    os.link(os.path.join(ART, "claude_memory.db"), os.path.join(dst, "claude_memory.db"))
    return dst


def apply(dst: str, rel: str, old: str, new: str) -> None:
    p = Path(dst, rel)
    src = p.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"{rel}: expected exactly one site for {old[:60]!r}, found {src.count(old)}"
    p.write_text(src.replace(old, new), encoding="utf-8")


def env_for(art: str, box: str, **extra: str) -> Dict[str, str]:
    home = os.path.join(box, "home")
    for d in (home, os.path.join(home, ".codex"), os.path.join(home, ".claude")):
        os.makedirs(d, exist_ok=True)
    env = dict(os.environ)
    for k in ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "PYTHONPATH", "VOIDLOOM_PLATFORM", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update({"HOME": home, "CODEX_HOME": os.path.join(home, ".codex"),
                "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude"),
                "VOIDLOOM_HEARTBEAT_DIR": os.path.join(box, "heartbeat"),
                "VOIDLOOM_INSTALL_RECEIPT": os.path.join(box, "receipt.json"),
                "VOIDLOOM_PREIMAGE_STORE": os.path.join(box, "preimages"),
                "VOIDLOOM_SELFCHECK_FIXTURE_DIR": os.path.join(box, "fx")})
    env.update(extra)
    return env


def vl(art: str, argv: List[str], env: Dict[str, str], cwd: str, timeout: int = 180):
    p = subprocess.run([os.path.join(art, "voidloom", "voidloom")] + argv, capture_output=True,
                       text=True, env=env, cwd=cwd, timeout=timeout)
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        doc = None
    return p.returncode, p.stdout, p.stderr, doc


def hook(art: str, payload: Any, env: Dict[str, str]) -> Tuple[int, str, str]:
    p = subprocess.run([sys.executable, os.path.join(art, "hooks", "session_start_inject.py")],
                       input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout, p.stderr


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def stripped_real() -> bytes:
    d = json.loads(Path(REAL_SETTINGS_COPY).read_bytes())
    d["hooks"]["SessionStart"] = [g for g in d["hooks"]["SessionStart"]
                                  if not any("session_start_inject" in h.get("command", "") for h in g["hooks"])]
    return (json.dumps(d, indent=2) + "\n").encode()


# ---------------------------------------------------------------------------
# PROBES — each returns (green: bool, detail).  Written against the PROPERTY.
# ---------------------------------------------------------------------------
def probe_crlf_refused(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_crlf_"); E = env_for(art, b)
    f = Path(b, "settings_crlf.json"); blob = stripped_real().replace(b"\n", b"\r\n"); f.write_bytes(blob)
    rc, out, err, doc = vl(art, ["hooks", "install", "--platform", "claude", "--target", str(f), "--yes", "--json"], E, b)
    return rc == 3 and f.read_bytes() == blob, {"rc": rc, "code": (doc or {}).get("code"), "unchanged": f.read_bytes() == blob}


def probe_legit_create(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_create_"); E = env_for(art, b)
    rc, out, err, doc = vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    hj = Path(E["CODEX_HOME"], "hooks.json")
    ok = rc == 0 and hj.exists() and hj.stat().st_size > 0 and "session_start_inject" in hj.read_text()
    return ok, {"rc": rc, "code": (doc or {}).get("code"), "bytes": hj.stat().st_size if hj.exists() else None}


def probe_element_level(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_elem_"); E = env_for(art, b)
    tgt = Path(E["CLAUDE_CONFIG_DIR"], "settings.json"); tgt.write_bytes(stripped_real())
    vl(art, ["hooks", "install", "--platform", "claude", "--yes", "--json"], E, b)
    d = json.loads(tgt.read_bytes()); d["permissions"]["allow"].append("Bash(git status:*)")
    tgt.write_bytes((json.dumps(d, indent=2) + "\n").encode())
    rc, out, err, doc = vl(art, ["hooks", "uninstall", "--yes", "--json"], E, b)
    now = json.loads(tgt.read_bytes())
    rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
    kept = "Bash(git status:*)" in now["permissions"]["allow"]
    return kept and rows and rows[0].get("result") == "ELEMENT_REMOVED", {"rc": rc, "result": rows[0].get("result") if rows else None, "grant_kept": kept}


def probe_heartbeat_append(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_hb_"); E = env_for(art, b)
    pl = {"session_id": "s1", "transcript_path": os.path.join(E["CODEX_HOME"], "sessions/x/rollout-s1.jsonl"),
          "model": "m", "permission_mode": "default", "source": "startup"}
    hook(art, pl, E); hook(art, {**pl, "session_id": "s2"}, E)
    f = Path(E["VOIDLOOM_HEARTBEAT_DIR"], "codex.jsonl")
    n = len(f.read_text().splitlines()) if f.exists() else 0
    return n == 2, {"lines": n}


def probe_dead_by_join(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_dead_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    now = datetime.now(timezone.utc)
    def sess(sid, when):
        d = Path(E["CODEX_HOME"], "sessions", when.strftime("%Y/%m/%d")); d.mkdir(parents=True, exist_ok=True)
        (d / f"rollout-{when.strftime('%Y-%m-%dT%H-%M-%S')}-{sid}.jsonl").write_text(
            json.dumps({"timestamp": ts(when), "type": "session_meta", "payload": {"session_id": sid, "timestamp": ts(when)}}) + "\n")
    t1 = now - timedelta(minutes=30)
    sess("s-1", t1)
    Path(E["VOIDLOOM_HEARTBEAT_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(E["VOIDLOOM_HEARTBEAT_DIR"], "codex.jsonl").write_text(json.dumps({"v": 2, "ts": ts(t1 + timedelta(seconds=1)), "platform": "codex", "session_id": "s-1", "preamble_sha": "x"}) + "\n")
    sess("s-2", now - timedelta(minutes=20)); sess("s-3", now - timedelta(minutes=10))
    rc, out, err, doc = vl(art, ["doctor", "--json", "--platform", "codex", "--cwd", b], E, b)
    row = (((doc or {}).get("data") or {}).get("hooks", {}).get("platforms", {}) or {}).get("codex") or {}
    return row.get("state") == "DEAD" and row.get("missed_count") == 2, {"state": row.get("state"), "missed": row.get("missed_count")}


def probe_unwritable_dir_exit0(art: str) -> Tuple[bool, Dict[str, Any]]:
    """The heartbeat dir does NOT exist and its PARENT is unwritable, so the
    mkdir branch is the one that fails (the first probe made the dir itself
    read-only, which fails at os.open and never reached the mutated site --
    the mutant SURVIVED for that reason, executed)."""
    b = md("p_ro_"); E = env_for(art, b)
    parent = Path(b, "ro_parent"); parent.mkdir()
    E["VOIDLOOM_HEARTBEAT_DIR"] = str(parent / "heartbeat")
    os.chmod(parent, 0o555)
    try:
        rc, out, err = hook(art, {"session_id": "s", "transcript_path": os.path.join(E["CODEX_HOME"], "sessions/x/rollout-s.jsonl")}, E)
    finally:
        os.chmod(parent, 0o755)
    ok = rc == 0
    try:
        ok = ok and bool(json.loads(out)["hookSpecificOutput"]["additionalContext"])
    except Exception:
        ok = False
    return ok, {"rc": rc, "stderr_tail": err.strip()[-120:]}


def probe_exit_code_4_preserved(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rc4_"); E = env_for(art, b)
    tgt = Path(b, "ours.json"); tgt.write_bytes(b'{"a": 1}\n')
    pre = Path(b, "pre.bytes"); pre.write_bytes(b'{"a": 0}\n')
    Path(E["VOIDLOOM_INSTALL_RECEIPT"]).write_text(json.dumps({"touched": [
        {"path": str(tgt), "action": "restore_preimage", "preimage_path": str(pre),
         "preimage_sha256": hashlib.sha256(pre.read_bytes()).hexdigest()}]}))
    os.chmod(tgt, 0o444)
    try:
        rc, out, err, doc = vl(art, ["hooks", "uninstall", "--yes", "--json", "--home", b], E, b)
    finally:
        os.chmod(tgt, 0o644)
    return rc == 4 and (doc or {}).get("status") == "incomplete", {"rc": rc, "status": (doc or {}).get("status")}


def probe_single_envelope(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_env_"); E = env_for(art, b)
    rc, out, err, doc = vl(art, ["hooks", "--status", "--json"], E, b)
    n = 0; dec = json.JSONDecoder(); s = out.strip(); i = 0
    while i < len(s):
        try:
            o, j = dec.raw_decode(s, i)
        except ValueError:
            break
        n += 1 if isinstance(o, dict) and "status" in o else 0
        i = j
        while i < len(s) and s[i].isspace():
            i += 1
    return n == 1 and rc == 0, {"rc": rc, "depth0_docs": n}


def probe_no_bare_mkdtemp(art: str) -> Tuple[bool, Dict[str, Any]]:
    import ast
    bare = {}
    for rel in ("organs/voidloom_canary.py", "organs/voidloom_preamble.py", "organs/voidloom_hooks_status.py"):
        tree = ast.parse(Path(art, rel).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "tempfile" and n.func.attr == "mkdtemp" \
                    and not any(k.arg == "dir" for k in n.keywords):
                bare.setdefault(rel, []).append(n.lineno)
    return not bare, {"bare": bare}


def probe_codex_limit_pinned(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_limit_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    rc, out, err, doc = vl(art, ["hooks", "status", "--json", "--probe-platform"], E, b)   # consent: the live view
    row = [r for r in ((doc or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]
    lim = row["resolved"]["platform_view"].get("our_additional_context_limit")
    import re as _re
    _src = Path(ART, "organs", "voidloom_hooks_status.py").read_text(encoding="utf-8")   # the INTACT installer's constant
    _m = _re.search(r"^CODEX_ADDITIONAL_CONTEXT_LIMIT\s*=\s*(\d+)", _src, flags=_re.M)
    want = int(_m.group(1)) if _m else None
    return want is not None and lim == [want], {"limit": lim, "installer_constant": want, "queried": row["resolved"]["platform_view"].get("queried")}


def probe_readback_rollback(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rb_"); E = env_for(art, b)
    tgt = Path(b, "fx.json"); blob = stripped_real(); tgt.write_bytes(blob)
    code = f'''
import sys, json
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
plan = hs.install_plan("claude", {b!r}, {b!r}, {str(tgt)!r})
hs.serialize_under_contract = lambda doc, contract: b"{{ not json at all"
res = hs.install_execute(plan)
print("RB" + json.dumps({{"result": res["result"], "restored": open({str(tgt)!r}, "rb").read() == {blob!r}}}))
'''
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=E, cwd=b)
    j = json.loads(p.stdout.split("RB")[-1]) if "RB" in p.stdout else {}
    return j.get("result") == "ROLLED_BACK" and j.get("restored") is True, {**j, "stderr": p.stderr[-100:]}


def probe_symlink_named_in_plan(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_sym_"); E = env_for(art, b)
    Path(E["HOME"], ".agents").mkdir()
    victim = Path(b, "victim.md"); victim.write_bytes(b"USER\n")
    os.symlink(str(victim), str(Path(E["HOME"], ".agents", "AGENTS.md")))
    rc, out, err, doc = vl(art, ["instructions", "install", "--dry-run", "--json"], E, b)
    t0 = (((doc or {}).get("data") or {}).get("targets") or [{}])[0]
    return t0.get("action") == "refused" and rc == 3, {"rc": rc, "action": t0.get("action"), "refusal": t0.get("refusal_code")}


def probe_codex_home_honoured(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_ch_"); E = env_for(art, b)
    other = Path(b, "elsewhere_codex_home"); other.mkdir()
    E["CODEX_HOME"] = str(other)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    rc, out, err, doc = vl(art, ["hooks", "status", "--json"], E, b)
    row = [r for r in ((doc or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"][0]
    hj = row["resolved"].get("hooks_json", {})
    return hj.get("path") == str(other / "hooks.json") and row["installed"] is True, {"path": hj.get("path"), "installed": row["installed"]}


def probe_orphan_seen(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_orphan_"); E = env_for(art, b)
    shutil.copyfile(REAL_SETTINGS_COPY, Path(E["CLAUDE_CONFIG_DIR"], "settings.json"))
    rc, out, err, doc = vl(art, ["hooks", "status", "--json"], E, b)
    u = ((doc or {}).get("data") or {}).get("unreceipted") or {}
    return u.get("orphan_count", 0) >= 1, {"orphan_count": u.get("orphan_count")}


def probe_identity_named_on_dir_swap(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_a3_"); E = env_for(art, b)
    ch = Path(E["CODEX_HOME"]); named = ch / "hooks.json"
    named.write_bytes((json.dumps(USER_DOC, indent=2) + "\n").encode())
    code = f'''
import sys, json, os
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
plan = hs.install_plan("codex", {b!r}, {b!r})
os.unlink({str(named)!r}); os.mkdir({str(named)!r}); open(os.path.join({str(named)!r}, "USERS"), "wb").write(b"x")
res = hs.install_execute(plan)
print("A3" + json.dumps({{"result": res.get("result"), "refused_by": res.get("refused_by"), "code": res.get("refusal_code")}}))
'''
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=E, cwd=b)
    j = json.loads(p.stdout.split("A3")[-1]) if "A3" in p.stdout else {}
    return j.get("result") == "REFUSED" and j.get("refused_by") == "pre_write_identity_check", j


# ---------------------------------------------------------------------------
# ITERATION-2 PROBES.  The first ten re-express the REFLECTOR'S independent
# mutants (RM01..RM23, the ten that SURVIVED the iteration-1 suite) against the
# current code, each with the fixture that now guards it; the rest are one
# mutant per CLASS swept this iteration (P2..P8, A9, the marker coupling).
# ---------------------------------------------------------------------------
import uuid as _uuid
try:
    from zoneinfo import ZoneInfo as _ZI
except ImportError:                       # pragma: no cover
    _ZI = None
SETTINGS_MIN = '{\n  "permissions": {\n    "allow": []\n  }\n}\n'


def _sid() -> str:
    return str(_uuid.uuid4())


def _sess(E: Dict[str, str], platform: str, sid: str, when: datetime, *, no_ts: bool = False,
          local_tz: Optional[str] = None) -> str:
    if platform == "codex":
        stamp = when.astimezone(_ZI(local_tz)) if (local_tz and _ZI) else when
        d = Path(E["CODEX_HOME"], "sessions", stamp.strftime("%Y/%m/%d")); d.mkdir(parents=True, exist_ok=True)
        f = d / f"rollout-{stamp.strftime('%Y-%m-%dT%H-%M-%S')}-{sid}.jsonl"
        rec = {"type": "session_meta", "payload": {"id": sid}} if no_ts else \
            {"timestamp": ts(when), "type": "session_meta", "payload": {"session_id": sid, "timestamp": ts(when)}}
        f.write_text(json.dumps(rec) + "\n")
        return str(f)
    d = Path(E["CLAUDE_CONFIG_DIR"], "projects", "-tmp-proj"); d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text(json.dumps({"type": "user", "sessionId": sid, "timestamp": ts(when)}) + "\n")
    return str(f)


def _beat(E: Dict[str, str], platform: str, sid: str, when: datetime) -> None:
    d = Path(E["VOIDLOOM_HEARTBEAT_DIR"]); d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{platform}.jsonl", "a") as fh:
        fh.write(json.dumps({"v": 2, "ts": ts(when), "platform": platform, "session_id": sid, "preamble_sha": "x"}) + "\n")


def _state(art: str, E: Dict[str, str], platform: str, cwd: str) -> Dict[str, Any]:
    rc, out, err, doc = vl(art, ["doctor", "--json", "--platform", platform, "--cwd", cwd], E, cwd)
    return ((((doc or {}).get("data") or {}).get("hooks") or {}).get("platforms") or {}).get(platform) or {}


def probe_live_join_consulted(art: str) -> Tuple[bool, Dict[str, Any]]:
    """RM01: a mis-extracted Codex id flips a LIVE fixture whose last session is stamped AFTER its fire."""
    b = md("p_rm01_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    now = datetime.now(timezone.utc); s1, s2 = _sid(), _sid()
    _sess(E, "codex", s1, now - timedelta(minutes=30)); _beat(E, "codex", s1, now - timedelta(minutes=30, seconds=-1))
    _beat(E, "codex", s2, now - timedelta(minutes=5, seconds=2)); _sess(E, "codex", s2, now - timedelta(minutes=5))
    row = _state(art, E, "codex", b)
    return row.get("state") == "LIVE" and row.get("sessions_since_last_fire_count") == 1, \
        {"state": row.get("state"), "since": row.get("sessions_since_last_fire_count"), "missed": row.get("missed_count")}


def probe_dead_after_one_miss(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm02_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    now = datetime.now(timezone.utc); s1 = _sid()
    _sess(E, "codex", s1, now - timedelta(minutes=30)); _beat(E, "codex", s1, now - timedelta(minutes=30, seconds=-1))
    _sess(E, "codex", _sid(), now - timedelta(minutes=10))
    row = _state(art, E, "codex", b)
    return row.get("state") == "DEAD" and row.get("missed_count") == 1, {"state": row.get("state"), "missed": row.get("missed_count")}


def probe_nested_transcript_not_a_session(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm03_"); E = env_for(art, b)
    Path(E["CLAUDE_CONFIG_DIR"], "settings.json").write_text(SETTINGS_MIN)
    vl(art, ["hooks", "install", "--platform", "claude", "--yes", "--json"], E, b)
    now = datetime.now(timezone.utc); s1 = _sid()
    _sess(E, "claude-code", s1, now - timedelta(minutes=30)); _beat(E, "claude-code", s1, now - timedelta(minutes=30, seconds=-1))
    nd = Path(E["CLAUDE_CONFIG_DIR"], "projects", "-tmp-proj", "subagents"); nd.mkdir(parents=True)
    (nd / f"agent-{_sid()}.jsonl").write_text(json.dumps({"type": "user", "timestamp": ts(now)}) + "\n")
    row = _state(art, E, "claude-code", b)
    return row.get("state") == "LIVE" and row.get("session_count_total") == 1, {"state": row.get("state"), "total": row.get("session_count_total")}


def probe_payload_outranks_env(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm05_"); E = env_for(art, b); E["CLAUDE_PROJECT_DIR"] = b
    hook(art, {"session_id": "s", "transcript_path": os.path.join(E["CODEX_HOME"], "sessions/x/rollout-s.jsonl")}, E)
    f = Path(E["VOIDLOOM_HEARTBEAT_DIR"], "codex.jsonl")
    return f.exists() and not Path(E["VOIDLOOM_HEARTBEAT_DIR"], "claude-code.jsonl").exists(), {"codex_file": f.exists()}


def _mcp_tool_file(art: str, E: Dict[str, str]) -> None:
    Path(E["CODEX_HOME"], "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": "startup", "hooks": [{"type": "mcpTool", "command": f"python3 {os.path.join(art, 'hooks', 'session_start_inject.py')}",
                                          "additionalContextLimit": 6000}]}]}}, indent=2) + "\n")


def probe_unloadable_not_installed_probed(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm06a_"); E = env_for(art, b); _mcp_tool_file(art, E)
    rc, out, err, doc = vl(art, ["hooks", "status", "--json", "--probe-platform"], E, b)
    r = [x for x in ((doc or {}).get("data") or {}).get("platforms", []) if x["id"] == "codex"][0]["resolved"]
    return r["installed"] is False and r["platform_view"]["queried"] is True, {"installed": r["installed"], "queried": r["platform_view"]["queried"]}


def probe_unloadable_not_installed_unprobed(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm06b_"); E = env_for(art, b); _mcp_tool_file(art, E)
    rc, out, err, doc = vl(art, ["hooks", "status", "--json"], E, b)
    r = [x for x in ((doc or {}).get("data") or {}).get("platforms", []) if x["id"] == "codex"][0]["resolved"]
    return r["installed"] is False and r["platform_view"]["queried"] is False, {"installed": r["installed"]}


def probe_ambiguous_refused(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm09_"); E = env_for(art, b)
    tgt = Path(E["CLAUDE_CONFIG_DIR"], "settings.json"); tgt.write_text(SETTINGS_MIN)
    vl(art, ["hooks", "install", "--platform", "claude", "--yes", "--json"], E, b)
    d = json.loads(tgt.read_text()); el = d["hooks"]["SessionStart"][0]
    t1 = json.loads(json.dumps(el)); t1["hooks"][0]["timeout"] = 30
    t2 = json.loads(json.dumps(el)); t2["hooks"][0]["timeout"] = 60
    d["hooks"]["SessionStart"] = [t1, t2]; tgt.write_bytes((json.dumps(d, indent=2) + "\n").encode()); before = tgt.read_bytes()
    rc, out, err, doc = vl(art, ["hooks", "uninstall", "--yes", "--json"], E, b)
    acts = ((doc or {}).get("data") or {}).get("actions") or []
    return rc == 3 and tgt.read_bytes() == before and bool(acts) and acts[0].get("blocked_code") == "ELEMENT_AMBIGUOUS", \
        {"rc": rc, "intact": tgt.read_bytes() == before, "code": acts[0].get("blocked_code") if acts else None}


def probe_readback_must_equal_postimage(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm11_"); E = env_for(art, b)
    tgt = Path(b, "fx.json"); blob = stripped_real(); tgt.write_bytes(blob)
    code = f'''
import sys, json, os
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
plan = hs.install_plan("claude", {b!r}, {b!r}, {str(tgt)!r})
_orig = hs.read_bytes_if_regular; _n = [0]
def _fake(p):
    if os.path.basename(p) == "fx.json":
        _n[0] += 1
        if _n[0] >= 2:
            return b'{{"a_concurrent_writer": true}}\\n'
    return _orig(p)
hs.read_bytes_if_regular = _fake
res = hs.install_execute(plan)
print("RB" + json.dumps({{"result": res["result"], "restored": open({str(tgt)!r}, "rb").read() == {blob!r}}}))
'''
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=E, cwd=b)
    j = json.loads(p.stdout.split("RB")[-1]) if "RB" in p.stdout else {}
    return j.get("result") == "ROLLED_BACK" and j.get("restored") is True, {**j, "stderr": p.stderr[-100:]}


def probe_empty_sessionstart_survives(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm16_"); E = env_for(art, b)
    tgt = Path(E["CLAUDE_CONFIG_DIR"], "settings.json")
    tgt.write_bytes(b'{\n  "hooks": {\n    "SessionStart": []\n  },\n  "permissions": {\n    "allow": []\n  }\n}\n')
    vl(art, ["hooks", "install", "--platform", "claude", "--yes", "--json"], E, b)
    d = json.loads(tgt.read_bytes()); d["permissions"]["allow"].append("Bash(ls:*)"); tgt.write_bytes((json.dumps(d, indent=2) + "\n").encode())
    exp = json.loads(tgt.read_bytes()); exp["hooks"]["SessionStart"] = []
    rc, out, err, doc = vl(art, ["hooks", "uninstall", "--yes", "--json"], E, b)
    return rc == 0 and tgt.read_bytes() == (json.dumps(exp, indent=2) + "\n").encode(), {"rc": rc, "after": tgt.read_text()[:60]}


def probe_human_mode_exit_code(art: str) -> Tuple[bool, Dict[str, Any]]:
    """RM22: the rc override matters where `_exit_code_for` would differ -- an
    INCOMPLETE undo (rc 4; status 'incomplete' maps to 1 without the override).  A
    refusal (rc 3) is equivalent under the mutant, which is why the first version
    of this probe let RM22 survive (executed)."""
    b = md("p_rm22_"); E = env_for(art, b)
    tgt = Path(b, "ours.json"); tgt.write_bytes(b'{"a": 1}\n')
    pre = Path(b, "pre.bytes"); pre.write_bytes(b'{"a": 0}\n')
    Path(E["VOIDLOOM_INSTALL_RECEIPT"]).write_text(json.dumps({"touched": [
        {"path": str(tgt), "action": "restore_preimage", "preimage_path": str(pre),
         "preimage_sha256": hashlib.sha256(pre.read_bytes()).hexdigest()}]}))
    os.chmod(tgt, 0o444)
    try:
        rc, out, err, doc = vl(art, ["hooks", "uninstall", "--yes", "--home", b], E, b)     # NO --json
    finally:
        os.chmod(tgt, 0o644)
    return rc == 4 and doc is None and "Voidloom hooks --uninstall" in out, {"rc": rc, "is_json": doc is not None}


def _install_both(art: str, b: str) -> Dict[str, str]:
    E = env_for(art, b)
    Path(E["CLAUDE_CONFIG_DIR"], "settings.json").write_text(SETTINGS_MIN)
    Path(E["CODEX_HOME"], "AGENTS.md").write_text("user rules\n"); Path(E["CLAUDE_CONFIG_DIR"], "CLAUDE.md").write_text("# user\n")
    for argv in (["hooks", "install", "--platform", "codex", "--yes", "--json"],
                 ["hooks", "install", "--platform", "claude", "--yes", "--json"],
                 ["instructions", "install", "--yes", "--json"]):
        vl(art, argv, E, b)
    return E


def probe_undo_scoped(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm23_"); E = _install_both(art, b)
    vl(art, ["hooks", "uninstall", "--yes", "--json"], E, b)
    rc, out, err, doc = vl(art, ["instructions", "status", "--json"], E, b)
    n = ((doc or {}).get("data") or {}).get("installed_count")
    return n == 3 and Path(E["HOME"], ".agents", "AGENTS.md").exists(), {"instructions_installed_after_hooks_uninstall": n}


def probe_single_platform_served(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_p2_"); E = env_for(art, b); shutil.rmtree(E["CODEX_HOME"])
    rc, out, err, doc = vl(art, ["instructions", "install", "--yes", "--json"], E, b)
    return rc == 0 and (doc or {}).get("code") == "instructions_install_installed" and Path(E["HOME"], ".agents", "AGENTS.md").exists(), \
        {"rc": rc, "code": (doc or {}).get("code")}


def _snap(root: str) -> Dict[str, Any]:
    o = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p_ = os.path.join(dp, f); st = os.lstat(p_); o[p_] = (st.st_mtime_ns, st.st_size)
        for dd in dn:
            o[os.path.join(dp, dd) + "/"] = "dir"
    return o


def probe_dry_undo_spawns_nothing(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_p4_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    s0 = _snap(E["HOME"]); rc, out, err, doc = vl(art, ["hooks", "uninstall", "--json"], E, b); s1 = _snap(E["HOME"])
    changed = sorted(k for k in set(s0) | set(s1) if s0.get(k) != s1.get(k))
    return rc == 0 and changed == [], {"rc": rc, "changed": len(changed)}


def probe_heartbeat_hardlink_refused(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_p5_"); E = env_for(art, b)
    Path(E["VOIDLOOM_HEARTBEAT_DIR"]).mkdir(parents=True)
    victim = Path(b, "victim.txt"); victim.write_bytes(b"USER\n"); os.link(str(victim), str(Path(E["VOIDLOOM_HEARTBEAT_DIR"], "codex.jsonl")))
    rc, out, err = hook(art, {"session_id": "s", "transcript_path": os.path.join(E["CODEX_HOME"], "sessions/x/rollout-s.jsonl")}, E)
    return rc == 0 and victim.read_bytes() == b"USER\n" and "REFUSED" in err, {"rc": rc, "victim_intact": victim.read_bytes() == b"USER\n"}


def probe_filename_stamp_is_local(art: str) -> Tuple[bool, Dict[str, Any]]:
    if _ZI is None:
        return True, {"skipped": "no zoneinfo"}
    b = md("p_p6_"); E = env_for(art, b); E["TZ"] = "America/Los_Angeles"
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    now = datetime.now(timezone.utc)
    _beat(E, "codex", _sid(), now - timedelta(minutes=15))
    _sess(E, "codex", _sid(), now - timedelta(minutes=10), no_ts=True, local_tz="America/Los_Angeles")
    row = _state(art, E, "codex", b)
    return row.get("state") == "DEAD" and row.get("missed_count") == 1, {"state": row.get("state"), "missed": row.get("missed_count")}


def probe_eisdir_refused(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_p7_"); E = env_for(art, b)
    named = Path(E["CODEX_HOME"], "hooks.json"); named.write_bytes((json.dumps(USER_DOC, indent=2) + "\n").encode())
    code = f'''
import sys, json, os
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
_real = os
class _P:
    def __getattr__(self, n): return getattr(_real, n)
    def open(self, path, flags, *a, **k):
        if isinstance(path, str) and path.endswith("hooks.json") and (flags & (_real.O_WRONLY | _real.O_RDWR)):
            _real.unlink(path); _real.mkdir(path)
        return _real.open(path, flags, *a, **k)
hs.os = _P()
plan = hs.install_plan("codex", {b!r}, {b!r})
res = hs.install_execute(plan)
print("O7" + json.dumps({{"result": res.get("result"), "refused_by": res.get("refused_by")}}))
'''
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=E, cwd=b, timeout=60)
    j = json.loads(p.stdout.split("O7")[-1]) if "O7" in p.stdout else {}
    return j.get("result") == "REFUSED" and j.get("refused_by") == "EISDIR_at_open", j


def probe_symlink_is_named_IS_SYMLINK(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_p8_"); E = env_for(art, b)
    Path(E["HOME"], ".agents").mkdir(); victim = Path(b, "victim.md"); victim.write_bytes(b"USER\n")
    os.symlink(str(victim), str(Path(E["HOME"], ".agents", "AGENTS.md")))
    rc, out, err, doc = vl(art, ["instructions", "install", "--dry-run", "--json"], E, b)
    t0 = (((doc or {}).get("data") or {}).get("targets") or [{}])[0]
    return rc == 3 and t0.get("refusal_code") == "IS_SYMLINK" and str(victim) in (t0.get("blocked") or ""), {"code": t0.get("refusal_code")}


def probe_census_survives_a_fifo(art: str) -> Tuple[bool, Dict[str, Any]]:
    """A9: a FIFO named hooks.json planted under the tree the census walks must not hang the selftest."""
    b = md("p_a9_"); E = env_for(art, b)
    fifo_dir = Path(art, "_planted"); fifo_dir.mkdir(exist_ok=True)
    fifo = fifo_dir / "hooks.json"; os.mkfifo(str(fifo))
    try:
        rc, out, err, doc = vl(art, ["preamble", "--selftest", "--json", "--work-db", os.path.join(b, "w.db")], E, b, timeout=90)
    except subprocess.TimeoutExpired:
        rc, doc = -999, None
    finally:
        os.unlink(str(fifo)); shutil.rmtree(fifo_dir, ignore_errors=True)
    return rc in (0, 1) and doc is not None, {"rc": rc, "completed": doc is not None}


def probe_marker_coupling(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_rm21_"); E = env_for(art, b)
    rc, out, err, doc = vl(art, ["preamble", "--selftest", "--json", "--work-db", os.path.join(b, "w.db")], E, b, timeout=120)
    failed = ((doc or {}).get("data") or {}).get("failed") or []
    return rc == 0 and not any(str(f).startswith("R8a_") for f in failed), {"rc": rc, "failed": failed[:3]}


def probe_marker_block_lookalike_refused(art: str) -> Tuple[bool, Dict[str, Any]]:
    b = md("p_p3text_"); E = env_for(art, b)
    Path(E["CODEX_HOME"], "AGENTS.md").write_text("user rules\n")
    vl(art, ["instructions", "install", "--yes", "--json"], E, b)
    ag = Path(E["CODEX_HOME"], "AGENTS.md"); txt = ag.read_text()
    ours = txt[txt.index("# >>> VOIDLOOM BEGIN >>>"):txt.index("# <<< VOIDLOOM END <<<") + len("# <<< VOIDLOOM END <<<\n")]
    ag.write_text(txt.replace(ours, "# >>> VOIDLOOM BEGIN >>>\nANOTHER BRAIN\n# <<< VOIDLOOM END <<<\n") + "# later\n"); before = ag.read_bytes()
    rc, out, err, doc = vl(art, ["instructions", "uninstall", "--yes", "--json"], E, b)
    return rc == 3 and ag.read_bytes() == before, {"rc": rc, "intact": ag.read_bytes() == before}


# ---------------------------------------------------------------------------
# THE MATRIX
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ITERATION-3 PROBES (P1-P7 of reflection_iter2, the six named survivors, RM19x)
# ---------------------------------------------------------------------------
def _roundtrip(art: str, pre: bytes, pfx: str) -> Tuple[bool, Dict[str, Any]]:
    b = md(pfx); E = env_for(art, b)
    tgt = Path(E["CLAUDE_CONFIG_DIR"], "settings.json"); tgt.write_bytes(pre)
    rc1, _, _, d1 = vl(art, ["hooks", "install", "--platform", "claude", "--yes", "--json"], E, b)
    mid = tgt.read_bytes()
    rc2, _, _, d2 = vl(art, ["hooks", "uninstall", "--yes", "--json"], E, b)
    return (rc1 == 0 and rc2 == 0 and b"session_start_inject" in mid and tgt.read_bytes() == pre), \
        {"install_rc": rc1, "install_code": (d1 or {}).get("code"), "undo_rc": rc2, "round_trip": tgt.read_bytes() == pre}


def probe_empty_hooks_object_survives(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P1 / R14: `"hooks": {}` (the OBJECT shape) installs and round-trips."""
    return _roundtrip(art, b'{\n  "hooks": {},\n  "permissions": {\n    "allow": []\n  }\n}\n', "p_r14_obj_")


def probe_empty_sessionstart_with_comma_survives(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB12: `"SessionStart": [],` BEFORE another key (trailing comma)."""
    return _roundtrip(art, b'{\n  "hooks": {\n    "SessionStart": [],\n    "Stop": [\n      {\n        "hooks": [\n          {\n            "type": "command",\n            "command": "echo bye"\n          }\n        ]\n      }\n    ]\n  }\n}\n', "p_mb12_")


def probe_relinked_ancestor_creates_nothing(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P2 / R3+R9 v3: ~/.voidloom -> elsewhere, heartbeat dir ABSENT: nothing created far side."""
    b = md("p_p2_"); E = env_for(art, b); home = Path(E["HOME"]); elsewhere = Path(b, "elsewhere"); elsewhere.mkdir()
    E["VOIDLOOM_HEARTBEAT_DIR"] = str(home / ".voidloom" / "heartbeat"); os.symlink(str(elsewhere), str(home / ".voidloom"))
    rc, out, err = hook(art, {"session_id": "cccccccc-0000-4000-8000-000000000000",
                              "transcript_path": os.path.join(E["CODEX_HOME"], "sessions/x/rollout-c.jsonl")}, E)
    far = sorted(x.name for x in elsewhere.iterdir())
    return rc == 0 and far == [] and "REFUSED" in err, {"rc": rc, "far_side_entries": far, "stderr": err.strip()[-100:]}


def _in_window_append(art: str, shape: str, timeout: int = 20) -> Dict[str, Any]:
    b = md(f"p_win_{shape}_")
    code = f"""
import json, os, sys, signal
from pathlib import Path
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
bx = Path({b!r}); p = bx / "codex.jsonl"; victim = bx / "victim.txt"; victim.write_bytes(b"USER\\n")
orig = hs.path_identity
def planted(path):
    ident = orig(path)
    if {shape!r} == "fifo": os.mkfifo(p)
    hs.path_identity = orig
    return ident
hs.path_identity = planted
res = {{}}
try:
    hs.guarded_append_bytes(str(p), b'{{"line":1}}\\n'); res["result"] = "WROTE"
except hs.PathIsNotWhatWeNamed as e:
    res["result"] = "REFUSED"; res["refused_by"] = getattr(e, "refused_by", None)
except OSError as e:
    res["result"] = "OSERROR:%s" % e.errno
try: os.unlink(p)
except OSError: pass
print("W" + json.dumps(res))
"""
    try:
        pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.unlink(os.path.join(b, "codex.jsonl"))
        except OSError:
            pass
        return {"result": "TIMEOUT", "timeout_s": timeout}
    try:
        return json.loads(pr.stdout.split("W")[-1])
    except Exception:
        return {"result": "ERR", "raw": pr.stdout[-120:] + "|" + pr.stderr[-200:]}


def probe_in_window_fifo_labelled(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P3: the in-window FIFO on the append face is REFUSED with refused_by ENXIO_at_open."""
    r = _in_window_append(art, "fifo")
    return r.get("result") == "REFUSED" and r.get("refused_by") == "ENXIO_at_open", r


def probe_in_window_fifo_does_not_block(art: str) -> Tuple[bool, Dict[str, Any]]:
    """RM19x: without O_NONBLOCK the in-window FIFO blocks the open forever (TIMEOUT = RED)."""
    r = _in_window_append(art, "fifo", timeout=15)
    return r.get("result") == "REFUSED", r


def probe_side_effect_stated_before_spawn(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P4: the --probe-platform side effect is on stderr BEFORE Popen, naming CODEX_HOME."""
    b = md("p_p4_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    code = f"""
import io, json, subprocess, sys
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
buf = io.StringIO(); sys.stderr = buf
seen = {{}}
def fake(*a, **k):
    seen["stderr_at_spawn"] = buf.getvalue(); raise OSError("intercepted")
subprocess.Popen = fake
hs.probe_codex_hooks(["{{codex_home}}/hooks.json", "{{codex_home}}/config.toml"], {b!r}, probe_platform=True)
sys.__stderr__.write("P4" + json.dumps(seen))
"""
    pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=E, cwd=b, timeout=60)
    try:
        at = json.loads(pr.stderr.split("P4")[-1]).get("stderr_at_spawn") or ""
    except Exception:
        at = ""
    return ("--probe-platform" in at and "app-server" in at and E["CODEX_HOME"] in at), {"stderr_at_spawn": at.strip()[-120:]}


def probe_instructions_rows_carry_identity(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P5 / R15: every instructions plan row carries realpath + (dev, ino, nlink)."""
    b = md("p_p5_"); E = env_for(art, b)
    rc, _, _, d = vl(art, ["instructions", "install", "--dry-run", "--json"], E, b)
    wi = ((d or {}).get("data") or {}).get("would_write") or []
    lacking = [(w.get("kind"), w.get("path", "")[-20:]) for w in wi if not all(k in w for k in ("realpath", "st_dev", "st_ino", "st_nlink"))]
    return rc == 0 and len(wi) >= 4 and lacking == [], {"rows": len(wi), "lacking": lacking}


def probe_stale_trust_record_is_foreign(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P6 / MB07 / R15: a record for ANOTHER hooks.json path is FOREIGN; trust stays untrusted."""
    b = md("p_p6_"); E = env_for(art, b)
    vl(art, ["hooks", "install", "--platform", "codex", "--yes", "--json"], E, b)
    stale = "/elsewhere/.codex/hooks.json:session_start:0:0"
    Path(E["CODEX_HOME"], "config.toml").write_text(f'[hooks.state."{stale}"]\ntrusted_hash = "sha256:1111"\n')
    rc, _, _, d = vl(art, ["hooks", "status", "--json"], E, b)
    crow = [r for r in ((d or {}).get("data") or {}).get("platforms", []) if r["id"] == "codex"]
    ct = crow[0]["resolved"]["config_toml"] if crow else {}
    ok = bool(crow) and crow[0]["resolved"].get("trust_status") == "untrusted" and ct.get("our_trust_state_keys") == [] \
        and ct.get("stale_trust_state_keys") == [stale]
    return ok, {"trust": crow[0]["resolved"].get("trust_status") if crow else None, "ours": ct.get("our_trust_state_keys"), "stale": ct.get("stale_trust_state_keys")}


def probe_no_dead_symlink_branch(art: str) -> Tuple[bool, Dict[str, Any]]:
    """P7: refusal_reason tests is_symlink exactly once (no unreachable second branch)."""
    import ast as _ast
    src = Path(art, "organs", "voidloom_hooks_status.py").read_text(encoding="utf-8"); tree = _ast.parse(src)
    fn = next(n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef) and n.name == "refusal_reason")
    tests = [_ast.get_source_segment(src, st.test) for st in fn.body if isinstance(st, _ast.If)]
    return tests.count('ident["is_symlink"]') == 1, {"if_tests": tests}


def probe_receipt_command_decides_after_brain_moved(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB05: install with --home <relocated>, edit our element, undo with the default home -> REMOVED by the receipt's command."""
    b = md("p_mb05_"); E = env_for(art, b)
    tgt = Path(E["CLAUDE_CONFIG_DIR"], "settings.json"); tgt.write_text('{\n  "permissions": {\n    "allow": []\n  }\n}\n')
    reloc = os.path.join(b, "relocated-brain")
    vl(art, ["hooks", "install", "--platform", "claude", "--yes", "--json", "--home", reloc], E, b)
    d = json.loads(tgt.read_text()); d["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 30
    tgt.write_bytes((json.dumps(d, indent=2) + "\n").encode())
    rc, _, _, doc = vl(art, ["hooks", "uninstall", "--yes", "--json"], E, b)
    rows = (((doc or {}).get("data") or {}).get("applied") or {}).get("applied") or []
    acts = ((doc or {}).get("data") or {}).get("actions") or []
    return (rc == 0 and rows and rows[0].get("result") == "ELEMENT_REMOVED" and rows[0].get("matched_by") == "marker_and_receipt_command"), \
        {"rc": rc, "result": rows[0].get("result") if rows else None, "blocked": [a.get("blocked_code") for a in acts]}


def probe_two_exact_copies_ambiguous(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB06: two exact copies of our block -> MARKER_AMBIGUOUS, file intact."""
    b = md("p_mb06_"); E = env_for(art, b)
    Path(E["CLAUDE_CONFIG_DIR"], "CLAUDE.md").write_text("# user\n")
    vl(art, ["instructions", "install", "--yes", "--json"], E, b)
    cm = Path(E["CLAUDE_CONFIG_DIR"], "CLAUDE.md"); txt = cm.read_text()
    blk = txt[txt.index("# >>> VOIDLOOM BEGIN >>>"):txt.index("# <<< VOIDLOOM END <<<") + len("# <<< VOIDLOOM END <<<\n")]
    cm.write_text(txt + blk); before = cm.read_bytes()
    rc, _, _, doc = vl(art, ["instructions", "uninstall", "--yes", "--json"], E, b)
    acts = [a for a in (((doc or {}).get("data") or {}).get("actions") or []) if a["path"].endswith(".claude/CLAUDE.md")]
    return (rc == 3 and acts and acts[0].get("blocked_code") == "MARKER_AMBIGUOUS" and cm.read_bytes() == before), \
        {"rc": rc, "blocked_code": acts[0].get("blocked_code") if acts else None, "file_intact": cm.read_bytes() == before}


def probe_no_fallback_writer_executed_and_census(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB09: organ renamed away -> rc 0, full preamble, refusal on stderr, NO file; AND the census sees any builtin open."""
    art2 = md("p_mb09_art_")
    for sub in ("voidloom", "organs", "hooks", "preimages_ref"):
        shutil.copytree(os.path.join(art, sub), os.path.join(art2, sub), symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".vl_selfcheck_fixtures"))
    shutil.copy2(os.path.join(art, "voidloom_manifest.json"), art2)
    os.link(os.path.join(art, "claude_memory.db"), os.path.join(art2, "claude_memory.db"))
    os.rename(os.path.join(art2, "organs", "voidloom_hooks_status.py"), os.path.join(art2, "organs", "voidloom_hooks_status.py.away"))
    b = md("p_mb09_"); E = env_for(art2, b)
    rc, out, err = hook(art2, {"session_id": "s", "transcript_path": os.path.join(E["CODEX_HOME"], "sessions/x/rollout-s.jsonl")}, E)
    files = sorted(x.name for x in Path(E["VOIDLOOM_HEARTBEAT_DIR"]).glob("*")) if Path(E["VOIDLOOM_HEARTBEAT_DIR"]).exists() else []
    try:
        pre_ok = len(json.loads(out)["hookSpecificOutput"]["additionalContext"]) > 1000
    except Exception:
        pre_ok = False
    # the census over the (possibly mutated) heartbeat organ of `art`, run with the SHIPPED census of `art`
    code = f"""
import json, sys
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
r = hs.scan_mutating_io([{os.path.join(art, "organs", "voidloom_heartbeat.py")!r}])
print("C" + json.dumps([(u["line"], u["call"]) for u in r["unguarded"]]))
"""
    pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    try:
        unguarded = json.loads(pr.stdout.split("C")[-1])
    except Exception:
        unguarded = [("census-error", pr.stderr[-100:])]
    shutil.rmtree(art2, ignore_errors=True)
    ok = rc == 0 and pre_ok and files == [] and "refusing to write with a second, ungated writer" in err and unguarded == []
    return ok, {"rc": rc, "preamble_ok": pre_ok, "heartbeat_files": files, "census_unguarded": unguarded}


def probe_census_sees_nested_writer(art: str) -> Tuple[bool, Dict[str, Any]]:
    """The census's top-level-guard rule: a nested def CARRYING a guard's name is not the guard."""
    d = md("p_census_"); pth = Path(d, "shape.py")
    pth.write_text('def _shared_writer():\n    try:\n        import voidloom_hooks_status as _hs\n        return _hs\n    except Exception:\n        class _Fallback:\n            @staticmethod\n            def guarded_append_bytes(path, data):\n                with open(path, "ab") as f:\n                    return f.write(data)\n        return _Fallback\n')
    code = f"""
import json, sys
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_hooks_status as hs
r = hs.scan_mutating_io([{str(pth)!r}])
print("C" + json.dumps([(u["line"], u["call"], u.get("enclosing_chain")) for u in r["unguarded"]]))
"""
    pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    try:
        unguarded = json.loads(pr.stdout.split("C")[-1])
    except Exception:
        unguarded = []
    return len(unguarded) == 1 and unguarded[0][1].startswith("open("), {"unguarded": unguarded}


def probe_name_screen_fires_on_preexisting_hardlink(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB10: a PRE-EXISTING hardlink at the heartbeat path is refused by the NAME SCREEN
    (refused_by pre_write_identity_check), not merely by the descriptor gate behind it."""
    b = md("p_mb10_"); hbd = Path(b, "heartbeat"); hbd.mkdir()
    victim = Path(b, "victim.txt"); victim.write_bytes(b"USER\n"); os.link(str(victim), str(hbd / "codex.jsonl"))
    code = f"""
import json, os, sys
os.environ["VOIDLOOM_HEARTBEAT_DIR"] = {str(hbd)!r}
sys.path.insert(0, {os.path.join(art, "organs")!r})
import voidloom_heartbeat as hb
r = hb.append_heartbeat("codex", session_id="s-1", preamble_sha="x", derivation="matrix")
print("J" + json.dumps({{"written": r.get("written"), "refused_by": r.get("refused_by")}}))
"""
    pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    try:
        j = json.loads(pr.stdout.split("J")[-1])
    except Exception:
        j = {"raw": pr.stdout[-100:] + pr.stderr[-200:]}
    return (j.get("written") is False and j.get("refused_by") == "pre_write_identity_check" and victim.read_bytes() == b"USER\n"), \
        {**j, "victim_intact": victim.read_bytes() == b"USER\n"}


def probe_canonical_written_when_agents_dir_absent(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB04x (faithful re-expression; the reflector's anchor moved under P5): on a home with
    NO ~/.agents the canonical file must still be written (never skipped as an absent platform)."""
    b = md("p_mb04x_"); E = env_for(art, b)
    rc, _, _, d = vl(art, ["instructions", "install", "--yes", "--json"], E, b)
    canon = Path(E["HOME"], ".agents", "AGENTS.md")
    rows = (((d or {}).get("data") or {}).get("applied") or {}).get("applied") or []
    return rc == 0 and canon.exists() and any(r.get("kind") == "mkdir" and r.get("result") == "CREATED" for r in rows), \
        {"rc": rc, "canonical_exists": canon.exists(), "rows": [(r.get("kind"), r.get("result")) for r in rows]}


def probe_canary_fifo_config_toml_bounded(art: str) -> Tuple[bool, Dict[str, Any]]:
    """MB11: a FIFO at ~/.codex/config.toml must not hang `canary --self-check` (TIMEOUT = RED)."""
    b = md("p_mb11_"); E = env_for(art, b)
    fifo = os.path.join(E["CODEX_HOME"], "config.toml"); os.mkfifo(fifo)
    import time as _t
    t0 = _t.time()
    try:
        p = subprocess.run([os.path.join(art, "voidloom", "voidloom"), "canary", "--self-check", "--json"],
                           capture_output=True, text=True, env=E, cwd=b, timeout=90)
        rc = p.returncode
        try:
            failed = json.loads(p.stdout)["data"].get("failed")
        except Exception:
            failed = "unparseable"
    except subprocess.TimeoutExpired:
        rc, failed = -999, "TIMEOUT"
    finally:
        try:
            os.unlink(fifo)
        except OSError:
            pass
    el = round(_t.time() - t0, 1)
    return rc == 0 and failed == [] and el < 60, {"rc": rc, "failed": failed, "elapsed_s": el}


MUTANTS: List[Dict[str, Any]] = [
    {"id": "M1", "file": "organs/voidloom_hooks_status.py", "fix": "R7(c) the losslessness gate compares bytes",
     "old": "        same = (re_ser == raw)\n", "new": "        same = True\n",
     "suffered": "a CRLF/tab/4-space settings.json is reformatted wholesale (EXPECTED TO SURVIVE ALONE: masked by "
                 "the pure-insertion diff invariant -- see M1b)", "probe": probe_crlf_refused, "expect_survive": True},
    {"id": "M1b", "file": "organs/voidloom_hooks_status.py", "fix": "R7(c) BOTH layers: the gate AND the pure-insertion diff invariant",
     "old": "        same = (re_ser == raw)\n", "new": "        same = True\n",
     "second": ("organs/voidloom_hooks_status.py",
                "                    if not plan[\"round_trip_deep_equal\"] or not plan[\"diff\"][\"pure\"]:",
                "                    if False:"),
     "suffered": "M1 alone SURVIVES because the diff invariant is a second, independent guard (defence in depth); "
                 "removing both reformats the user's file", "probe": probe_crlf_refused},
    {"id": "M2", "file": "organs/voidloom_hooks_status.py", "fix": "the legitimate CREATE passes the gate (found this round)",
     "old": "        created_by_us = bool(saw_nothing)\n", "new": "        created_by_us = False\n",
     "suffered": "every fresh hooks.json / receipt creation refused, 0-byte file left", "probe": probe_legit_create},
    {"id": "M3", "file": "organs/voidloom_hooks_status.py", "fix": "R5 element-level undo when current != post-image",
     "old": "                    and now != post_sha and action in (\"restore_preimage\", \"delete_file\"):",
     "new": "                    and False:",
     "suffered": "days of the user's permission grants destroyed, reported RESTORED", "probe": probe_element_level},
    {"id": "M4", "file": "organs/voidloom_hooks_status.py", "fix": "R3 the heartbeat is APPEND-only (the shared writer's append face)",
     "old": "    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | _NOFOLLOW", "new": "    flags = os.O_WRONLY | os.O_TRUNC | os.O_CREAT | os.O_NONBLOCK | _NOFOLLOW",
     "suffered": "'fired once' and 'fires every session' become the same file", "probe": probe_heartbeat_append},
    {"id": "M5", "file": "organs/voidloom_heartbeat.py", "fix": "R4 DEAD is decided by the session JOIN",
     "old": "    missed = [s for s in newer if s.get(\"id\") not in fired_ids]\n", "new": "    missed = []\n",
     "suffered": "a dead hook reads LIVE for as long as the user keeps starting sessions", "probe": probe_dead_by_join},
    {"id": "M6", "file": "hooks/session_start_inject.py", "fix": "R3 the sensor may never break session start (both layers)",
     "old": "    except Exception as e:  # the contract: a sensor may never break the thing it senses\n        return {\"written\": False, \"reason\": f\"{type(e).__name__}: {e}\"}",
     "new": "    except Exception as e:\n        raise",
     "second": ("organs/voidloom_heartbeat.py",
                "    except OSError as e:\n        return {\"written\": False, \"path\": path, \"reason\": f\"mkdir: {type(e).__name__}: {e}\"}",
                "    except OSError as e:\n        raise"),
     "suffered": "an unwritable ~/.voidloom breaks every session start", "probe": probe_unwritable_dir_exit0},
    {"id": "M7", "file": "voidloom/cli.py", "fix": "R1 the organ's exit code is preserved through dispatch",
     "old": "    if rc_override is not None:\n        try:\n            return int(rc_override)", "new": "    if False:\n        try:\n            return int(rc_override)",
     "suffered": "an INCOMPLETE undo (rc 4) exits 1 like any error; scripts cannot tell them apart", "probe": probe_exit_code_4_preserved},
    {"id": "M8", "file": "voidloom/cli.py", "fix": "R1 the envelope flows ONCE (P1)",
     "old": "def verb_hooks(args) -> Dict[str, Any]:\n    return _run_organ(\"hooks\", args)",
     "new": "def verb_hooks(args) -> Dict[str, Any]:\n    env = _run_organ(\"hooks\", args)\n    print(json.dumps({k: v for k, v in env.items() if not k.startswith(\"_\")}))\n    return env",
     "suffered": "a double-printed envelope is unparseable", "probe": probe_single_envelope},
    {"id": "M9", "file": "organs/voidloom_canary.py", "fix": "R10 no bare mkdtemp (the class that hid inode reuse)",
     "old": "tempfile.mkdtemp(prefix=\"voidloom_canary_control_\", dir=fixture_root())", "new": "tempfile.mkdtemp(prefix=\"voidloom_canary_control_\")",
     "suffered": "a fixture on tmpfs cannot express the shape under test", "probe": probe_no_bare_mkdtemp},
    {"id": "M10", "file": "organs/voidloom_hooks_status.py", "fix": "additionalContextLimit pinned at install (Amendment 2)",
     "old": "                           \"additionalContextLimit\": CODEX_ADDITIONAL_CONTEXT_LIMIT}]}", "new": "                           }]}",
     "suffered": "Codex spills at 2,500 tokens; changing it later forces re-approval", "probe": probe_codex_limit_pinned},
    {"id": "M11", "file": "organs/voidloom_hooks_status.py", "fix": "R7(e) read-back must parse or the pre-image is restored",
     "old": "    if not (ok and parsed_ok):\n        row[\"result\"] = \"ROLLED_BACK\"", "new": "    if False:\n        row[\"result\"] = \"ROLLED_BACK\"",
     "suffered": "a torn/invalid settings.json stays on disk; Claude Code skips ALL 16 hooks silently", "probe": probe_readback_rollback},
    {"id": "M12", "file": "organs/voidloom_instructions.py", "fix": "R9 the dry-run NAMES a symlink/hardlink refusal",
     "old": "    if st[\"refusal_code\"]:\n        st[\"action\"] = \"refused\"", "new": "    if False:\n        st[\"action\"] = \"refused\"",
     "suffered": "the plan says 'append' for a link; the user cannot enumerate what is refused", "probe": probe_symlink_named_in_plan},
    {"id": "M13", "file": "organs/voidloom_hooks_status.py", "fix": "the platform's OWN resolver variable (CODEX_HOME) is honoured",
     "old": "    codex_home = (os.environ.get(\"CODEX_HOME\") or \"\").strip() or str(Path.home() / \".codex\")\n    p = rel.replace(",
     "new": "    codex_home = str(Path.home() / \".codex\")\n    p = rel.replace(",
     "suffered": "status/install look at ~/.codex while Codex reads $CODEX_HOME", "probe": probe_codex_home_honoured},
    {"id": "M14", "file": "organs/voidloom_hooks_status.py", "fix": "--status SEES unreceipted markers (the orphan)",
     "old": "    orphans: List[Dict[str, Any]] = []\n    for r in rows:", "new": "    orphans: List[Dict[str, Any]] = []\n    for r in []:",
     "suffered": "the 2026-09-08 hand-edit and the live element are invisible", "probe": probe_orphan_seen},
    {"id": "M15", "file": "organs/voidloom_hooks_status.py", "fix": "R7(d) the post-image sha is recorded in the receipt",
     "old": "             \"postimage_sha256\": w[\"sha256_after\"], \"element\": element,", "new": "             \"postimage_sha256\": None, \"element\": element,",
     "suffered": "no post-image -> the undo falls back to the pre-image restore over a changed file", "probe": probe_element_level},
    {"id": "M16", "file": "organs/voidloom_hooks_status.py", "fix": "identity BEFORE content on the install re-read (S5'/A3)",
     "old": "        if _rc is not None or (_drift[\"checked\"] and _drift[\"same_object\"] is False):\n            res[\"result\"] = \"REFUSED\"\n            res[\"refused_by\"] = \"pre_write_identity_check\"",
     "new": "        if False:\n            res[\"result\"] = \"REFUSED\"\n            res[\"refused_by\"] = \"pre_write_identity_check\"",
     "suffered": "a directory swapped in is reported as a read error, not as the object it is", "probe": probe_identity_named_on_dir_swap},
    # ---------------- iteration 2: THE REFLECTOR'S TEN SURVIVORS, now guarded ----------------
    {"id": "RM01", "file": "organs/voidloom_heartbeat.py", "fix": "R4 the Codex join key is stem[-36:] (LIVE fixture consults the id)",
     "old": "sid = stem[-36:] if len(stem) >= 36 else stem", "new": "sid = stem[-35:] if len(stem) >= 36 else stem",
     "suffered": "the join key never matches; any session stamped after its own heartbeat reads DEAD", "probe": probe_live_join_consulted},
    {"id": "RM02", "file": "organs/voidloom_heartbeat.py", "fix": "R4 one missed session is DEAD",
     "old": "    if missed:\n        out[\"state\"] = \"DEAD\"", "new": "    if len(missed) >= 2:\n        out[\"state\"] = \"DEAD\"",
     "suffered": "a hook dead for exactly one session reads LIVE", "probe": probe_dead_after_one_miss},
    {"id": "RM03", "file": "organs/voidloom_heartbeat.py", "fix": "R4 nested subagent transcripts are not sessions",
     "old": "pattern = os.path.join(root, \"*\", \"*.jsonl\")", "new": "pattern = os.path.join(root, \"**\", \"*.jsonl\")",
     "suffered": "false DEAD whenever a subagent runs", "probe": probe_nested_transcript_not_a_session},
    {"id": "RM05", "file": "organs/voidloom_heartbeat.py", "fix": "R3 the payload's transcript_path outranks CLAUDE_PROJECT_DIR",
     "old": "    tp = payload.get(\"transcript_path\")\n    if isinstance(tp, str) and tp:",
     "new": "    if env.get(\"CLAUDE_PROJECT_DIR\"):\n        return \"claude-code\", \"env:CLAUDE_PROJECT_DIR\"\n    tp = payload.get(\"transcript_path\")\n    if isinstance(tp, str) and tp:",
     "suffered": "a Codex session from a Claude Code shell is labelled claude-code: codex NEVER_FIRED forever", "probe": probe_payload_outranks_env},
    {"id": "RM06a", "file": "organs/voidloom_hooks_status.py", "fix": "R4 the LIVE view alone decides when it was asked",
     "old": "        installed = bool(pv.get(\"ours\"))\n        out[\"installed_derived_from\"] = \"app-server hooks/list",
     "new": "        installed = bool(pv.get(\"ours\")) or (data is not None and HOOK_BASENAME in json.dumps(data))\n        out[\"installed_derived_from\"] = \"app-server hooks/list",
     "suffered": "a hooks.json Codex does not load reads as installed", "probe": probe_unloadable_not_installed_probed},
    {"id": "RM06b", "file": "organs/voidloom_hooks_status.py", "fix": "R4 file-derived registration is a SCHEMA parse, not a basename grep",
     "old": "        installed = bool(schema[\"ours\"])", "new": "        installed = (data is not None and HOOK_BASENAME in json.dumps(data))",
     "suffered": "a hooks.json Codex does not load reads as installed (no spawn)", "probe": probe_unloadable_not_installed_unprobed},
    {"id": "RM09", "file": "organs/voidloom_hooks_status.py", "fix": "R13 two identity matches is AMBIGUOUS, refused",
     "old": "        elif len(ours) > 1:\n            info[\"reason\"] = (f\"{len(ours)} elements carry our marker AND the receipt's \"",
     "new": "        elif len(ours) > 1:\n            idx = ours[-1]\n            info[\"matched_by\"] = \"marker_last\"\n        elif False:\n            info[\"reason\"] = (f\"{len(ours)} elements carry our marker AND the receipt's \"",
     "suffered": "the wrong element is removed when two carry the marker", "probe": probe_ambiguous_refused},
    {"id": "RM11", "file": "organs/voidloom_hooks_status.py", "fix": "R7(e) v2 the read-back must EQUAL the planned post-image",
     "old": "            parsed_ok = (vb == post)\n", "new": "            parsed_ok = True\n",
     "suffered": "a concurrent writer's parseable bytes are accepted as our post-image", "probe": probe_readback_must_equal_postimage},
    {"id": "RM16", "file": "organs/voidloom_hooks_status.py", "fix": "R7 only containers WE created are pruned",
     "old": "    if [\"hooks\", \"SessionStart\"] in ck and not groups:", "new": "    if not groups:",
     "suffered": "a user's pre-existing empty SessionStart array is deleted on undo", "probe": probe_empty_sessionstart_survives},
    {"id": "RM22", "file": "voidloom/cli.py", "fix": "R1 the exit code is preserved in HUMAN mode too",
     "old": "    if rc_override is not None:\n        try:\n            return int(rc_override)", "new": "    if rc_override is not None and as_json:\n        try:\n            return int(rc_override)",
     "suffered": "`voidloom hooks install --yes && echo done` prints done over a refusal", "probe": probe_human_mode_exit_code},
    {"id": "RM23", "file": "organs/voidloom_hooks_status.py", "fix": "R11 hooks uninstall is scoped to the hooks mechanism (P1)",
     "old": "    plan = uninstall_plan(cwd, args.home, mechanism=MECHANISM,\n                          probe_platform=False)",
     "new": "    plan = uninstall_plan(cwd, args.home, mechanism=None,\n                          probe_platform=False)",
     "suffered": "hooks uninstall DELETES ~/.agents/AGENTS.md and undoes both instruction files", "probe": probe_undo_scoped},
    # ---------------- iteration 2: ONE MUTANT PER CLASS SWEPT ----------------
    {"id": "M17", "file": "organs/voidloom_instructions.py", "fix": "R12 an absent platform home is SKIPPED, never a blocker (P2)",
     "old": "                    st[\"action\"] = \"skipped\"", "new": "                    st[\"action\"] = \"refused\"; blockers.append(\"platform home absent\")",
     "suffered": "a Claude-only or Codex-only user gets NOTHING, not even the canonical file", "probe": probe_single_platform_served},
    {"id": "M18", "file": "organs/voidloom_hooks_status.py", "fix": "R5 a plan never spawns the vendor (P4)",
     "old": "    st = status(cwd, home=home, probe_platform=probe_platform)", "new": "    st = status(cwd, home=home, probe_platform=True)",
     "suffered": "a DRY-RUN undo spawns codex app-server and writes ~119 paths under ~/.codex", "probe": probe_dry_undo_spawns_nothing},
    {"id": "M19", "file": "organs/voidloom_heartbeat.py", "fix": "R6 the heartbeat goes through the shared identity-gated writer (P5)",
     "old": "        w = _hs.guarded_append_bytes(path, line)",
     "new": "        _fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | getattr(os, \"O_NOFOLLOW\", 0), 0o644); os.write(_fd, line); os.close(_fd); w = {\"bytes_written\": len(line), \"short_write\": False, \"link_count_at_write\": None}",
     "suffered": "a hardlink of a user's file at the heartbeat path receives our line (the prior round's asymmetry, third writer)", "probe": probe_heartbeat_hardlink_refused},
    {"id": "M20", "file": "organs/voidloom_heartbeat.py", "fix": "R4 the Codex filename stamp is LOCAL time, converted (P6)",
     "old": "                                   .astimezone(timezone.utc)\n", "new": "                                   .replace(tzinfo=timezone.utc)\n",
     "suffered": "a fallback session is misplaced by the UTC offset: a real miss hides in UTC- zones", "probe": probe_filename_stamp_is_local},
    {"id": "M21", "file": "organs/voidloom_hooks_status.py", "fix": "R6 EISDIR at the open is a REFUSAL naming the object (P7)",
     "old": "        _eexist_to_refusal(e, path)\n        _eisdir_to_refusal(e, path)\n        _enxio_to_refusal(e, path)\n        raise\n    try:\n        # DEFENCE 3 FIRST, TRUNCATE SECOND.",
     "new": "        _eexist_to_refusal(e, path)\n        _enxio_to_refusal(e, path)\n        raise\n    try:\n        # DEFENCE 3 FIRST, TRUNCATE SECOND.",
     "suffered": "a directory swapped in at the open is labelled ERROR (rc 1), not REFUSED (rc 3)", "probe": probe_eisdir_refused},
    {"id": "M22", "file": "organs/voidloom_hooks_status.py", "fix": "R9 a symlink is refused as IS_SYMLINK, naming the target (P8)",
     "old": "    if ident[\"is_symlink\"]:\n        return \"IS_SYMLINK\"\n    if ident.get(\"is_regular_file\") is False:\n        return \"NOT_A_REGULAR_FILE\"",
     "new": "    if ident.get(\"is_regular_file\") is False:\n        return \"NOT_A_REGULAR_FILE\"\n    if ident[\"is_symlink\"]:\n        return \"IS_SYMLINK\"",
     "suffered": "the user reads 'a FIFO here BLOCKS the open forever' for a symlink", "probe": probe_symlink_is_named_IS_SYMLINK},
    {"id": "M23", "file": "organs/voidloom_preamble.py", "fix": "A9 the census reads through a bounded opener (S_ISREG + O_NONBLOCK)",
     "old": "            txt = read_text_bounded(str(path), errors=\"replace\")", "new": "            txt = path.read_text(encoding=\"utf-8\", errors=\"replace\")",
     "suffered": "a FIFO left under the tree hangs the selftest forever (kernel wait_for_partner)", "probe": probe_census_survives_a_fifo},
    {"id": "M24", "file": "organs/voidloom_preamble.py", "fix": "R8a the arrival line is a FLOOR segment no budget drops (Council #12)",
     "old": "    \"priming\",         # 1. nightly; staleness invisible; largest volatile block", "new": "    \"arrival\", \"priming\",  # MUTANT: the arrival line becomes the first thing a budget cuts",
     "suffered": "under budget the hook delivers no arrival line while the heartbeat says LIVE; the notice fires in every budgeted session", "probe": probe_marker_coupling},
    {"id": "M25", "file": "organs/voidloom_hooks_status.py", "fix": "R13 the marker-block undo matches the receipt's block (text twin of P3)",
     "old": "    if element:\n        n = txt.count(element)", "new": "    if False:\n        n = txt.count(element)",
     "suffered": "another brain's marker block is removed as ours", "probe": probe_marker_block_lookalike_refused},
    # ---- ITERATION 3: P1-P7 of reflection_iter2, the six named survivors, RM19x ----
    {"id": "M26", "file": "organs/voidloom_hooks_status.py", "fix": "R14 the empty-container rule accepts the OBJECT shape `\"hooks\": {}` (P1)",
     "old": "            _close = \"]\" if core.endswith(\"[]\") else \"}\" if core.endswith(\"{}\") else None",
     "new": "            _close = \"]\" if core.endswith(\"[]\") else None",
     "suffered": "a user whose settings.json carries `\"hooks\": {}` (Claude Code writes it when the last event key goes) cannot install: rc 3 install_refused", "probe": probe_empty_hooks_object_survives},
    {"id": "MB12", "file": "organs/voidloom_hooks_status.py", "fix": "R14 the empty-container rule preserves the trailing comma (the reflector's MB12)",
     "old": "            comma = stripped.endswith(\",\")", "new": "            comma = False",
     "suffered": "a user whose hooks object lists SessionStart: [] before another key cannot install", "probe": probe_empty_sessionstart_with_comma_survives},
    {"id": "M27", "file": "organs/voidloom_heartbeat.py", "fix": "R3/R9 v3 the heartbeat gates the directory chain BEFORE creating anything (P2)",
     "old": "        _mk = _hs.guarded_mkdir_chain(str(Path(path).parent))",
     "new": "        Path(path).parent.mkdir(parents=True, exist_ok=True); _mk = None",
     "suffered": "with ~/.voidloom -> elsewhere and no heartbeat dir yet, elsewhere/heartbeat/ is created inside the foreign target before the gate refuses the file", "probe": probe_relinked_ancestor_creates_nothing},
    {"id": "M27b", "file": "organs/voidloom_hooks_status.py", "fix": "R9 v3 the directory face's NAME pre-check (layer 1 of 2; the O_NOFOLLOW walk is layer 2 and MASKS this revert -- named)",
     "old": "    if rp != ap:\n        raise PathIsNotWhatWeNamed(\n            f\"{ap} resolves to {rp}: a component of the directory chain is a link, so a \"",
     "new": "    if False:\n        raise PathIsNotWhatWeNamed(\n            f\"{ap} resolves to {rp}: a component of the directory chain is a link, so a \"",
     "suffered": "(masked) the walk still refuses the link at its component with ELOOP_at_open; nothing is created", "probe": probe_relinked_ancestor_creates_nothing, "expect_survive": True},
    {"id": "M27c", "file": "organs/voidloom_hooks_status.py", "fix": "R9 v3 BOTH layers of the directory face (pre-check AND the O_NOFOLLOW walk)",
     "old": "    if rp != ap:\n        raise PathIsNotWhatWeNamed(\n            f\"{ap} resolves to {rp}: a component of the directory chain is a link, so a \"",
     "new": "    if False:\n        raise PathIsNotWhatWeNamed(\n            f\"{ap} resolves to {rp}: a component of the directory chain is a link, so a \"",
     "second": ("organs/voidloom_hooks_status.py", "        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=dir_fd)", "        return os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)"),
     "suffered": "a directory is created on the far side of a re-linked ancestor", "probe": probe_relinked_ancestor_creates_nothing},
    {"id": "M28", "file": "organs/voidloom_hooks_status.py", "fix": "R6 v3 ENXIO at the append face is a LABELLED refusal (P3)",
     "old": "        _eloop_to_refusal(e, path)\n        _eisdir_to_refusal(e, path)\n        _enxio_to_refusal(e, path)\n        raise\n    try:\n        st = os.fstat(fd)",
     "new": "        _eloop_to_refusal(e, path)\n        _eisdir_to_refusal(e, path)\n        raise\n    try:\n        st = os.fstat(fd)",
     "suffered": "an in-window FIFO is reported as 'OSError: [Errno 6]' with refused absent, where the object was refused", "probe": probe_in_window_fifo_labelled},
    {"id": "RM19x", "file": "organs/voidloom_hooks_status.py", "fix": "R6 the append face opens O_NONBLOCK so an in-window FIFO cannot block session start (the reflector's faithful RM19x)",
     "old": "    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | _NOFOLLOW",
     "new": "    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW",
     "suffered": "a FIFO planted between the name screen and the open blocks the hook forever (the window is now BOUNDED: ENXIO)", "probe": probe_in_window_fifo_does_not_block},
    {"id": "M29", "file": "organs/voidloom_hooks_status.py", "fix": "R4/R5 v3 the --probe-platform side effect is stated on stderr BEFORE the spawn (P4)",
     "old": "    print(f\"[voidloom hooks] --probe-platform: spawning `codex app-server` for hooks/list; \"\n          f\"the vendor writes its own state under {_codex_home} (sqlite / wal / tmp)\",\n          file=sys.stderr, flush=True)\n",
     "new": "",
     "suffered": "nothing at run time tells the user, before Codex writes, that it is about to", "probe": probe_side_effect_stated_before_spawn},
    {"id": "M30", "file": "organs/voidloom_instructions.py", "fix": "R15 every instructions plan row carries identity (P5)",
     "old": "        would.append({**_hs._identity_row(rp), \"kind\": \"receipt_append\"})",
     "new": "        would.append({\"path\": rp, \"kind\": \"receipt_append\"})",
     "suffered": "the receipt row of the instructions plan has no realpath / identity; the dry run is not enumerable for that row", "probe": probe_instructions_rows_carry_identity},
    {"id": "MB07", "file": "organs/voidloom_hooks_status.py", "fix": "R4/R15 trust is derived from the record for OUR key only (the reflector's MB07)",
     "old": "        elif any(trust_records.get(k) for k in keys):", "new": "        elif any(trust_records.values()):",
     "suffered": "a stale record for another absolute path (CODEX_HOME relocated) makes our untrusted hook read trusted_by_record", "probe": probe_stale_trust_record_is_foreign},
    {"id": "M31", "file": "organs/voidloom_hooks_status.py", "fix": "R15 a trust record whose key is not ours is reported FOREIGN, never ours (P6)",
     "old": "    our_trust_keys = [k for k in trust_states if k in _ours_keys]",
     "new": "    our_trust_keys = [k for k in trust_states if \"hooks.json\" in k or HOOK_BASENAME in k]",
     "suffered": "the report row lists a foreign record under our_trust_state_keys and contradicts the derivation row", "probe": probe_stale_trust_record_is_foreign},
    {"id": "M32", "file": "organs/voidloom_hooks_status.py", "fix": "R9 labelling: refusal_reason has no unreachable second is_symlink branch (P7)",
     "old": "                f\"names every name.\")\n    if ident[\"differs\"]:",
     "new": "                f\"names every name.\")\n    if ident[\"is_symlink\"]:\n        return \"dead\"\n    if ident[\"differs\"]:",
     "suffered": "dead code: a second is_symlink test that the first branch makes unreachable", "probe": probe_no_dead_symlink_branch},
    {"id": "MB05", "file": "organs/voidloom_hooks_status.py", "fix": "R13 the receipt's command decides our element's identity, not hook_command(home) now (the reflector's MB05)",
     "old": "            _own_cmd = _own_cmds[0] if _own_cmds else hook_command(home)", "new": "            _own_cmd = hook_command(home)",
     "suffered": "after the brain home moves, our own (edited) element reads ELEMENT_NOT_OURS and cannot be undone", "probe": probe_receipt_command_decides_after_brain_moved},
    {"id": "MB06", "file": "organs/voidloom_hooks_status.py", "fix": "R13 two exact copies of our block is AMBIGUOUS (the reflector's MB06)",
     "old": "        if n == 1:\n            i = txt.index(element)", "new": "        if n >= 1:\n            i = txt.index(element)",
     "suffered": "two exact copies of our block: the first is removed instead of refusing as ambiguous", "probe": probe_two_exact_copies_ambiguous},
    {"id": "MB09", "file": "organs/voidloom_heartbeat.py", "fix": "R6 no fallback writer on the import-failure path (the reflector's MB09), caught by EXECUTION and by the census",
     "old": "    import voidloom_hooks_status as _hs  # stdlib-only; ~13 ms measured\n    return _hs",
     "new": "    try:\n        import voidloom_hooks_status as _hs\n        return _hs\n    except Exception:\n        class _Fallback:\n            class PathIsNotWhatWeNamed(OSError):\n                pass\n            @staticmethod\n            def guarded_append_bytes(path, data):\n                with open(path, \"ab\") as f:\n                    n = f.write(data)\n                return {\"bytes_written\": n, \"short_write\": False, \"link_count_at_write\": None}\n        return _Fallback",
     "suffered": "a second, ungated writer reappears whenever the organ is missing; the hardlink defect returns", "probe": probe_no_fallback_writer_executed_and_census},
    {"id": "M33", "file": "organs/voidloom_hooks_status.py", "fix": "I5 a site is guarded only by a MODULE-LEVEL def on the allow-list (a nested def carrying a guard's name is not the guard)",
     "old": "    unguarded = [s for s in sites\n                 if not (s.get(\"toplevel_guard\") in _IO_ALLOWLIST)]",
     "new": "    unguarded = [s for s in sites\n                 if not (set(s.get(\"enclosing_chain\") or [])\n                         & set(_IO_ALLOWLIST))]",
     "suffered": "MB09's nested `guarded_append_bytes` passes the census because its NAME is on the allow-list", "probe": probe_census_sees_nested_writer},
    {"id": "M34", "file": "organs/voidloom_hooks_status.py", "fix": "I5 the builtin open with a writing mode is in the census population",
     "old": "                if hit is None:\n                    is_builtin = isinstance(node.func, ast.Name) and node.func.id == \"open\"",
     "new": "                if False:\n                    is_builtin = isinstance(node.func, ast.Name) and node.func.id == \"open\"",
     "suffered": "a writer built on open(path, 'ab') is invisible to the census", "probe": probe_census_sees_nested_writer},
    {"id": "MB10", "file": "organs/voidloom_hooks_status.py", "fix": "R6 the append face's NAME SCREEN names itself, so its removal is a RED (the reflector's MB10, masked since P2 by the directory face)",
     "old": "    ident = path_identity(path)\n    if refusal_code(ident) is not None:\n        # THE NAME SCREEN NAMES ITSELF (iteration 3, the reflector's MB10): without a",
     "new": "    ident = path_identity(path)\n    if False:\n        # THE NAME SCREEN NAMES ITSELF (iteration 3, the reflector's MB10): without a",
     "suffered": "the append face keeps only the descriptor gate; a pre-existing hardlink is refused later, by a different layer, under a different label", "probe": probe_name_screen_fires_on_preexisting_hardlink},
    {"id": "MB04x", "file": "organs/voidloom_instructions.py", "fix": "R12 the canonical ~/.agents/AGENTS.md is never skipped as an absent platform (the reflector's MB04, re-expressed after P5 moved its anchor)",
     "old": "                if st[\"id\"] == \"agents-md\":\n                    # EVERY PLAN ROW CARRIES IDENTITY (R15, iteration 3): the mkdir,",
     "new": "                if False:\n                    # EVERY PLAN ROW CARRIES IDENTITY (R15, iteration 3): the mkdir,",
     "suffered": "on a machine with no ~/.agents the canonical file is SKIPPED as if it were an absent platform: nothing canonical is ever written", "probe": probe_canonical_written_when_agents_dir_absent},
    {"id": "MB11", "file": "organs/voidloom_canary.py", "fix": "A9 the canary's config.toml read is bounded (the reflector's MB11)",
     "old": "                txt = read_text_bounded(str(cfg), errors=\"strict\")", "new": "                txt = cfg.read_text(encoding=\"utf-8\", errors=\"strict\")",
     "suffered": "a FIFO at ~/.codex/config.toml hangs `canary --self-check`", "probe": probe_canary_fifo_config_toml_bounded},
]

print(f"\n########## MUTATION MATRIX  artifacts={ART}  scratch={ROOT}\n# 16 iteration-1 rows + the reflector's 10 survivors (RM*) + 9 class mutants (M17-M25) + iteration 3 (M26-M34, MB05/06/07/09/11/12, RM19x)\n" + "#" * 74)
rows: List[Dict[str, Any]] = []
intact_cache: Dict[str, Tuple[bool, Dict[str, Any]]] = {}
for m in MUTANTS:
    if ONLY and m["id"] != ONLY:
        continue
    pn = m["probe"].__name__
    if pn not in intact_cache:
        intact_cache[pn] = m["probe"](ART)
    g_ok, g_det = intact_cache[pn]
    dst = mutant_copy(m["id"])
    try:
        apply(dst, m["file"], m["old"], m["new"])
        if m.get("second"):
            apply(dst, *m["second"])
        r_ok, r_det = m["probe"](dst)
    except AssertionError as e:
        r_ok, r_det = None, {"mutation_not_applied": str(e)}
    except Exception as e:
        r_ok, r_det = False, {"probe_raised": f"{type(e).__name__}: {e}"}
    verdict = ("DETECTED" if (g_ok and r_ok is False) else
               ("SURVIVED_MASKED_BY_SECOND_LAYER" if m.get("expect_survive") else "SURVIVED") if (g_ok and r_ok) else
               "PROBE_NOT_GREEN_ON_INTACT" if not g_ok else "NOT_APPLIED")
    rows.append({"id": m["id"], "file": m["file"], "fix": m["fix"], "suffered": m["suffered"],
                 "intact": g_ok, "mutant": r_ok, "verdict": verdict, "probe": pn,
                 "intact_detail": g_det, "mutant_detail": r_det, "mutant_copy": dst})
    print(f"  {m['id']:<4} {verdict:<26} intact={'GREEN' if g_ok else 'RED':5s} mutant={'RED' if r_ok is False else ('GREEN' if r_ok else '?'):5s}  {m['fix']}")
    print(f"        mutant detail: {json.dumps(r_det, default=str)[:160]}")
    shutil.rmtree(dst, ignore_errors=True)

detected = sum(1 for r in rows if r["verdict"] == "DETECTED")
masked = [r["id"] for r in rows if r["verdict"] == "SURVIVED_MASKED_BY_SECOND_LAYER"]
summary = {"mutants": len(rows), "detected": detected, "masked_by_a_second_layer": masked,
           "survived": [r["id"] for r in rows if r["verdict"] == "SURVIVED"],
           "not_green_on_intact": [r["id"] for r in rows if r["verdict"] == "PROBE_NOT_GREEN_ON_INTACT"],
           "not_applied": [r["id"] for r in rows if r["verdict"] == "NOT_APPLIED"],
           "label": "an AUTHOR'S matrix — it measures what the author already understood (memory #18344); "
                    "an independent party's mutants are the stronger instrument",
           "rows": rows}
Path(ROOT, "mutation_matrix.json").write_text(json.dumps(summary, indent=1, default=str))
print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
print(f"\n  ===> {detected}/{len(rows)} MUTANTS DETECTED  ({len(masked)} masked by a second layer, named)\n")
sys.exit(0 if detected + len(masked) == len(rows) else 1)

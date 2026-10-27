#!/usr/bin/env python3
"""THE 16-SHAPE PROPERTY BATTERY, AGAINST THE INSTALL WRITER (R6).

The property (RELEASE_CRITERIA R6, the prior round's R7 Amendment 4):
    A WRITE MUST LAND ON THE SAME FILE OBJECT THE PLAN IDENTIFIED, AND ON NO
    OTHER, AND THAT MUST BE VERIFIED BY IDENTITY (and CONTENT, on a filesystem
    that recycles inode numbers) RATHER THAN BY PATH.

`--install` is a NEW writer and inherits nothing.  Every shape of the prior
round's battery (S1..S5, A1..A11) is re-expressed against `install_plan` ->
<mutation> -> `install_execute`, on the Codex target ($CODEX_HOME/hooks.json,
a JSON create/merge through the SAME guarded_write_bytes uninstall uses).  The
three shapes whose subject was the DELETE side (S5, A8, A10) are re-expressed on
the install's own delete side: the receipt write (S5') and the uninstall of a
file the install CREATED (A8', A10').  A11 (the marker-block swap) becomes the
ELEMENT-LEVEL undo swap (A11').  Two NEGATIVE CONTROLS are added: N1, the
legitimate CREATE (absent at plan, still absent at write -- the shape that found
the inherited writer refusing every fresh create and leaving a 0-byte file), and
N2, the legitimate MERGE.

    argv[1] = artifacts dir      argv[2] = scratch ROOT (REQUIRED, ext4 under $HOME)
"""
import hashlib, json, os, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

if len(sys.argv) < 3:
    sys.exit("usage: property_battery_16_install.py <artifacts_dir> <scratch_root>")
ART = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ORGANS = os.path.join(ART, "organs")
HOOKS = os.path.join(ORGANS, "voidloom_hooks_status.py")
USER_DOC = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo USER-HOOK"}]}]}}
USER = (json.dumps(USER_DOC, indent=2) + "\n").encode()
USER_TXT = b"THE USER'S OWN FILE - never ours\n"
R = {}

os.makedirs(ROOT, exist_ok=True)
_probe = tempfile.mkdtemp(prefix="fsprobe_", dir=ROOT)
FS_DEV = os.stat(_probe).st_dev
_p = Path(_probe) / "x"
_p.write_bytes(b"a"); _i1 = os.lstat(_p).st_ino
os.unlink(_p)
_p.write_bytes(b"b"); _i2 = os.lstat(_p).st_ino
INODE_REUSE = (_i1 == _i2)
shutil.rmtree(_probe, ignore_errors=True)


def md(pfx):
    return tempfile.mkdtemp(prefix=pfx, dir=ROOT)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def note(name, held, detail):
    if "held" in detail:                      # the verdict key is never overridable by the detail (iteration 3)
        detail = {**detail, "detail_held": detail.pop("held")}
    R[name] = {**detail, "held": bool(held)}
    print(f"  {name:52s} {'HELD' if held else 'VIOLATED':9s} {json.dumps(detail, default=str)[:260]}")


def box(pfx):
    """A fixture box: CODEX_HOME, receipt, pre-image store, all inside."""
    d = md(pfx)
    ch = Path(d) / "codex_home"; ch.mkdir()
    return d, str(ch), str(Path(d) / "receipt.json"), str(Path(d) / "preimages")


def in_process(d, codex_home, receipt, store, body, *, phase="install", target=None,
               pre_body="", timeout=60):
    """plan -> <body> -> execute in ONE process: the plan/write window.
    phase=install drives install_plan/install_execute; phase=uninstall drives
    uninstall_plan/uninstall_execute (the install's own delete side)."""
    s = Path(d) / "win.py"
    tgt_expr = repr(target) if target else "None"
    s.write_text(f'''
import importlib.util, json, os, sys
from pathlib import Path
os.environ["CODEX_HOME"] = {codex_home!r}
os.environ["VOIDLOOM_INSTALL_RECEIPT"] = {receipt!r}
os.environ["VOIDLOOM_PREIMAGE_STORE"] = {store!r}
spec = importlib.util.spec_from_file_location("hs", {HOOKS!r})
hs = importlib.util.module_from_spec(spec); spec.loader.exec_module(hs)
_out = {{}}
{pre_body}
if {phase!r} == "install":
    plan = hs.install_plan("codex", {d!r}, {d!r}, {tgt_expr})
else:
    plan = hs.uninstall_plan({d!r}, {d!r}, mechanism="hooks")
_out["plan_blocked"] = plan.get("blocked")
_out["plan_action"] = plan.get("action") or [a.get("action") for a in plan.get("actions", [])]
_out["plan_refusal"] = plan.get("refusal_code") or [a.get("blocked_code") for a in plan.get("actions", [])]
{body}
try:
    if {phase!r} == "install":
        res = hs.install_execute(plan)
    else:
        res = hs.uninstall_execute(plan)
    _out["res"] = res
except Exception as e:
    _out["raised"] = f"{{type(e).__name__}}: {{e}}"
print("JSONSTART" + json.dumps(_out, default=str))
''')
    try:
        p = subprocess.run([sys.executable, str(s)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"_timeout": True}
    out = p.stdout.split("JSONSTART")[-1] if "JSONSTART" in p.stdout else ""
    try:
        return json.loads(out)
    except Exception:
        return {"_err": (p.stdout[-500:] + "|" + p.stderr[-800:])}


def result_of(e):
    res = e.get("res") or {}
    if isinstance(res, dict) and "applied" in res and isinstance(res.get("applied"), list):
        rows = res["applied"]
        return res.get("result"), (rows[0] if rows and isinstance(rows[0], dict) else {})
    return None, {}


print(f"\n########## 16-SHAPE PROPERTY BATTERY — THE INSTALL WRITER")
print(f"# artifacts : {ART}")
print(f"# scratch   : {ROOT}   dev={FS_DEV}   inode_numbers_recycled={INODE_REUSE}")
print(f"# {'ext4-CLASS: the shapes below CAN fire here' if INODE_REUSE else 'tmpfs-CLASS: A9 CANNOT fire here'}")
print("#" * 74)

# S1 -- HARDLINK: the target is one object under a name the plan never listed.
d, ch, rp, st = box("s1_hardlink_")
real = Path(d) / "REAL_hooks.json"; real.write_bytes(USER)
named = Path(ch) / "hooks.json"; os.link(str(real), str(named))
e = in_process(d, ch, rp, st, "")
r, row = result_of(e)
note("S1_HARDLINK_target", real.read_bytes() == USER and r == "REFUSED" and e.get("plan_blocked") is True
     and e.get("plan_refusal") == "HAS_OTHER_NAMES",
     {"plan_refusal": e.get("plan_refusal"), "result": r, "victim_intact": real.read_bytes() == USER})

# S2 -- TOCTOU SWAP by RENAME between plan and execute.
d, ch, rp, st = box("s2_swap_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e = in_process(d, ch, rp, st, f'''
_o = Path({d!r}) / "users_own.json"
_o.write_bytes({USER_TXT!r})
os.rename(str(_o), {str(named)!r})
''')
r, row = result_of(e)
note("S2_TOCTOU_SWAP_by_rename", named.read_bytes() == USER_TXT and r == "REFUSED",
     {"result": r, "reason": str((e.get("res") or {}).get("reason"))[:100], "victim_intact": named.read_bytes() == USER_TXT})

# S3 -- RENAMED DIRECTORY IN THE PREFIX. realpath never differs.
d, ch, rp, st = box("s3_prefix_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e = in_process(d, ch, rp, st, f'''
os.rename({ch!r}, {str(Path(d) / "codex_home_moved")!r})
_n = Path({ch!r}); _n.mkdir()
(_n / "hooks.json").write_bytes({USER_TXT!r})
''')
r, row = result_of(e)
note("S3_RENAMED_PREFIX", named.read_bytes() == USER_TXT and r == "REFUSED",
     {"result": r, "reason": str((e.get("res") or {}).get("reason"))[:100], "victim_intact": named.read_bytes() == USER_TXT})

# S4 -- PLANTED (plain, nlink 1) where the plan saw NOTHING (the CREATE path).
d, ch, rp, st = box("s4_planted_")
named = Path(ch) / "hooks.json"
e = in_process(d, ch, rp, st, f'''
Path({str(named)!r}).write_bytes({USER_TXT!r})
''')
r, row = result_of(e)
# `A and B and C or (A and B)` parsed as `A and B` -- the O_EXCL clause was dead (A6).
note("S4_PLANTED_where_plan_saw_nothing", named.read_bytes() == USER_TXT and r == "REFUSED"
     and (e.get("res") or {}).get("refused_by") in ("O_EXCL_at_open", "plan_identity_drift"),
     {"result": r, "refused_by": (e.get("res") or {}).get("refused_by"), "victim_intact": named.read_bytes() == USER_TXT})

# S5' -- the install's DELETE-SIDE claim: a RECEIPT path that is one name of a user's object.
d, ch, rp, st = box("s5_receipt_hardlink_")
user_r = Path(d) / "USER_KEEPS_THIS.json"; user_r.write_bytes(USER_TXT)
os.link(str(user_r), rp)
e = in_process(d, ch, rp, st, "")
r, row = result_of(e)
rows = ((e.get("res") or {}).get("applied") or [])
rr = [x for x in rows if x.get("kind") == "receipt_append"]
note("S5_RECEIPT_hardlinked_to_users_file_is_REFUSED_and_named",
     user_r.read_bytes() == USER_TXT and r == "INSTALLED_UNRECEIPTED" and rr and rr[0].get("result") == "ERROR"
     and "names" in str(rr[0].get("reason")),
     {"result": r, "receipt_row": (rr[0].get("result"), str(rr[0].get("reason"))[:80]) if rr else None,
      "users_file_intact": user_r.read_bytes() == USER_TXT})

# A1 -- FIFO at the named path: not a regular file; the open must not block.
d, ch, rp, st = box("a1_fifo_")
named = Path(ch) / "hooks.json"; os.mkfifo(str(named))
e = in_process(d, ch, rp, st, "", timeout=20)
r, row = result_of(e)
still_fifo = stat.S_ISFIFO(os.lstat(str(named)).st_mode)
note("A1_FIFO_TARGET", not e.get("_timeout") and still_fifo and r == "REFUSED"
     and e.get("plan_refusal") == "NOT_A_REGULAR_FILE",
     {"timeout": e.get("_timeout"), "plan_refusal": e.get("plan_refusal"), "result": r, "still_a_fifo": still_fifo})
os.unlink(str(named))   # never leave a FIFO under a tree a later census may walk (A9)

# A2 -- CHARACTER DEVICE named as the target.
d, ch, rp, st = box("a2_dev_")
e = in_process(d, ch, rp, st, "", target="/dev/null")
r, row = result_of(e)
note("A2_CHAR_DEVICE_TARGET", r == "REFUSED" and e.get("plan_refusal") == "NOT_A_REGULAR_FILE"
     and stat.S_ISCHR(os.lstat("/dev/null").st_mode),
     {"plan_refusal": e.get("plan_refusal"), "result": r})

# A3 -- DIRECTORY swapped in where the plan saw a FILE.
d, ch, rp, st = box("a3_dir4file_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e = in_process(d, ch, rp, st, f'''
os.unlink({str(named)!r})
os.mkdir({str(named)!r})
(Path({str(named)!r}) / "USERS_STUFF").write_bytes({USER_TXT!r})
''')
r, row = result_of(e)
survived = (named / "USERS_STUFF").exists() and (named / "USERS_STUFF").read_bytes() == USER_TXT
note("A3_DIRECTORY_WHERE_FILE_PLANNED", survived and r == "REFUSED",
     {"result": r, "raised": e.get("raised"), "users_dir_intact": survived})

# A4 -- PARENT DIRECTORY becomes a SYMLINK after the plan.
d, ch, rp, st = box("a4_parent_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
elsewhere = Path(d) / "elsewhere"; elsewhere.mkdir()
(elsewhere / "hooks.json").write_bytes(USER_TXT)
e = in_process(d, ch, rp, st, f'''
import shutil as _sh
_sh.rmtree({ch!r})
os.symlink({str(elsewhere)!r}, {ch!r})
''')
r, row = result_of(e)
survived = (elsewhere / "hooks.json").read_bytes() == USER_TXT
note("A4_PARENT_BECOMES_SYMLINK", survived and r == "REFUSED",
     {"result": r, "reason": str((e.get("res") or {}).get("reason"))[:90], "victim_intact": survived})

# A5 -- NEGATIVE CONTROL: the SAME inode renamed out and back. The install MUST land.
d, ch, rp, st = box("a5_negctl_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e = in_process(d, ch, rp, st, f'''
os.rename({str(named)!r}, {str(Path(d) / "tmpname")!r})
os.rename({str(Path(d) / "tmpname")!r}, {str(named)!r})
''')
r, row = result_of(e)
doc = json.loads(named.read_bytes()) if named.exists() else {}
groups = doc.get("hooks", {}).get("SessionStart", [])
note("A5_NEGATIVE_CONTROL_legit_merge_after_rename_out_and_back",
     r == "INSTALLED" and len(groups) == 2 and groups[0] == USER_DOC["hooks"]["SessionStart"][0]
     and "session_start_inject" in json.dumps(groups[1]) and Path(rp).exists(),
     {"result": r, "groups": len(groups)})

# A6 -- st_nlink RISES between the plan and the write.
d, ch, rp, st = box("a6_nlink_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
other = Path(d) / "attacker_second_name.json"
e = in_process(d, ch, rp, st, f'''
os.link({str(named)!r}, {str(other)!r})
''')
r, row = result_of(e)
note("A6_NLINK_RISES_AFTER_PLAN", r == "REFUSED" and other.read_bytes() == USER,
     {"result": r, "refused_by": (e.get("res") or {}).get("refused_by"), "second_name_unchanged": other.read_bytes() == USER})

# A7 -- ORDINARY file planted where the plan saw NOTHING (nlink 1), a second time on the create path.
d, ch, rp, st = box("a7_plant1_")
named = Path(ch) / "hooks.json"
e = in_process(d, ch, rp, st, f'''
Path({str(named)!r}).write_bytes({USER!r})
''')
r, row = result_of(e)
note("A7_PLANTED_ORDINARY_FILE_nlink1", named.read_bytes() == USER and r == "REFUSED",
     {"result": r, "refused_by": (e.get("res") or {}).get("refused_by"), "victim_intact": named.read_bytes() == USER})

# A8' -- the install's DELETE side: uninstall of a file we CREATED after a DIRECTORY was swapped in.
d, ch, rp, st = box("a8_deldir_")
named = Path(ch) / "hooks.json"
e0 = in_process(d, ch, rp, st, "")
e = in_process(d, ch, rp, st, f'''
os.unlink({str(named)!r})
os.mkdir({str(named)!r})
(Path({str(named)!r}) / "USERS_STUFF").write_bytes({USER_TXT!r})
''', phase="uninstall")
res = e.get("res") or {}
rows = res.get("applied") or []
surv = (named / "USERS_STUFF").exists()
note("A8_UNINSTALL_DELETE_refuses_a_DIRECTORY_where_our_file_was",
     result_of(e0)[0] == "INSTALLED" and surv and (res.get("deleted") or 0) == 0
     and (rows and rows[0].get("result") != "DELETED"),
     {"install": result_of(e0)[0], "uninstall_row": (rows[0].get("result") if rows else None),
      "plan_refusal": e.get("plan_refusal"), "dir_contents_intact": surv})

# A9 -- TOCTOU swap by UNLINK+CREATE: the freed inode number comes straight back on ext4.
d, ch, rp, st = box("a9_inode_reuse_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e = in_process(d, ch, rp, st, f'''
_before = os.lstat({str(named)!r})
os.unlink({str(named)!r})
Path({str(named)!r}).write_bytes({USER_TXT!r})
_after = os.lstat({str(named)!r})
_out["identity_collided"] = ((_before.st_dev, _before.st_ino) == (_after.st_dev, _after.st_ino))
''')
r, row = result_of(e)
note("A9_TOCTOU_UNLINK_CREATE_inode_reuse", named.read_bytes() == USER_TXT and r == "REFUSED",
     {"result": r, "identity_collided": e.get("identity_collided"), "fs_recycles_inodes": INODE_REUSE,
      "reason": str((e.get("res") or {}).get("reason"))[:90], "victim_intact": named.read_bytes() == USER_TXT})

# A10' -- the DELETE side under a rename swap: uninstall (delete_file) of a created hooks.json.
d, ch, rp, st = box("a10_del_swap_")
named = Path(ch) / "hooks.json"
e0 = in_process(d, ch, rp, st, "")
e = in_process(d, ch, rp, st, f'''
_o = Path({d!r}) / "users_own.json"
_o.write_bytes({USER!r})
os.rename(str(_o), {str(named)!r})
''', phase="uninstall")
res = e.get("res") or {}
rows = res.get("applied") or []
note("A10_UNINSTALL_DELETE_refuses_a_rename_swap",
     result_of(e0)[0] == "INSTALLED" and named.exists() and named.read_bytes() == USER
     and (res.get("deleted") or 0) == 0,
     {"install": result_of(e0)[0], "uninstall_row": (rows[0].get("result") if rows else None),
      "refused_by": (rows[0].get("refused_by") if rows else None), "victim_present": named.read_bytes() == USER if named.exists() else False})

# A11' -- ELEMENT-LEVEL undo under a swap: install into the user's file; the file changes
#         (so the undo is element-level); then a DIFFERENT user file carrying our element is
#         renamed over the name between the uninstall plan and its execution.
d, ch, rp, st = box("a11_elem_swap_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e0 = in_process(d, ch, rp, st, "")
grown = json.loads(named.read_bytes()); grown["hooks"]["UserPromptSubmit"] = [{"hooks": [{"type": "command", "command": "echo later"}]}]
named.write_bytes((json.dumps(grown, indent=2) + "\n").encode())
their = json.loads(named.read_bytes()); their["hooks"]["SessionStart"].insert(0, {"hooks": [{"type": "command", "command": "echo THEIRS-FIRST"}]})
THEIRS = (json.dumps(their, indent=2) + "\n").encode()
e = in_process(d, ch, rp, st, f'''
_o = Path({d!r}) / "users_own.json"
_o.write_bytes({THEIRS!r})
os.rename(str(_o), {str(named)!r})
''', phase="uninstall")
res = e.get("res") or {}
rows = res.get("applied") or []
note("A11_ELEMENT_LEVEL_UNDO_refuses_a_swap_between_plan_and_execute",
     result_of(e0)[0] == "INSTALLED" and e.get("plan_action") == ["remove_element"]
     and named.read_bytes() == THEIRS and (res.get("elements_removed") or 0) == 0
     and rows and rows[0].get("result") == "SKIPPED",
     {"install": result_of(e0)[0], "plan_action": e.get("plan_action"),
      "uninstall_row": (rows[0].get("result"), rows[0].get("refused_by")) if rows else None,
      "users_file_intact": named.read_bytes() == THEIRS})

# N1 -- NEGATIVE CONTROL: the LEGITIMATE CREATE. Absent at plan, still absent at write.
#       (The inherited writer refused this and left a 0-byte file; only the planted
#       half of the shape had ever been tested.)
d, ch, rp, st = box("n1_legit_create_")
named = Path(ch) / "hooks.json"
e = in_process(d, ch, rp, st, "")
r, row = result_of(e)
ok = named.exists() and named.stat().st_size > 0
try:
    doc = json.loads(named.read_bytes()); ok = ok and "session_start_inject" in json.dumps(doc)
except Exception:
    ok = False
note("N1_NEGATIVE_CONTROL_legit_CREATE_absent_at_plan_still_absent_lands",
     r == "INSTALLED" and ok and Path(rp).exists() and Path(rp).stat().st_size > 0,
     {"result": r, "bytes": named.stat().st_size if named.exists() else None, "receipt_bytes": Path(rp).stat().st_size if Path(rp).exists() else None})

# N2 -- NEGATIVE CONTROL: the legitimate MERGE into a user's file, then element-level undo
#       after the user grows the file: their bytes survive exactly.
d, ch, rp, st = box("n2_legit_merge_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e = in_process(d, ch, rp, st, "")
r, row = result_of(e)
grown = json.loads(named.read_bytes()); grown["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "echo stop"}]}]
named.write_bytes((json.dumps(grown, indent=2) + "\n").encode())
e2 = in_process(d, ch, rp, st, "", phase="uninstall")
res2 = e2.get("res") or {}
exp = json.loads(named.read_bytes()); exp["hooks"]["SessionStart"] = [g for g in exp["hooks"]["SessionStart"] if "session_start_inject" not in json.dumps(g)]
note("N2_NEGATIVE_CONTROL_legit_MERGE_then_element_level_undo_keeps_user_bytes",
     r == "INSTALLED" and (res2.get("elements_removed") or 0) == 1
     and named.read_bytes() == (json.dumps(exp, indent=2) + "\n").encode()
     and "echo stop" in named.read_text() and "echo USER-HOOK" in named.read_text(),
     {"install": r, "elements_removed": res2.get("elements_removed"), "plan_action": e2.get("plan_action")})

# =====================================================================
# THE THIRD WRITER: the heartbeat, through the SAME gate (R6 v2: every shape
# re-expressed against EACH writer, plus one legitimate case per operation).
# =====================================================================
def hb_case(pfx, setup):
    """Call append_heartbeat in a subprocess with VOIDLOOM_HEARTBEAT_DIR in the box;
    ``setup(hbdir, box)`` plants the shape.  Returns (result_dict, box)."""
    d = md(pfx)
    hbd = Path(d) / "heartbeat"
    setup(hbd, Path(d))
    s_ = Path(d) / "hb.py"
    s_.write_text(f"""
import json, os, sys
os.environ["VOIDLOOM_HEARTBEAT_DIR"] = {str(hbd)!r}
sys.path.insert(0, {ORGANS!r})
import voidloom_heartbeat as hb
r = hb.append_heartbeat("codex", session_id="s-1", preamble_sha="x", derivation="battery")
print("JSONSTART" + json.dumps(r, default=str))
""")
    try:
        p_ = subprocess.run([sys.executable, str(s_)], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return {"_timeout": True}, d
    out = p_.stdout.split("JSONSTART")[-1] if "JSONSTART" in p_.stdout else ""
    try:
        return json.loads(out), d
    except Exception:
        return {"_err": p_.stdout[-300:] + "|" + p_.stderr[-500:]}, d

VICTIM = b"THE USER'S FILE\n"
def _victim(box):
    v = box / "USER_KEEPS_THIS.txt"; v.write_bytes(VICTIM); return v

# H1 HARDLINK at the heartbeat path (P5: the iteration-1 writer appended INTO the user's file)
def _h1(hbd, box):
    hbd.mkdir(); os.link(str(_victim(box)), str(hbd / "codex.jsonl"))
e, d = hb_case("h1_hardlink_", _h1)
v = Path(d) / "USER_KEEPS_THIS.txt"
note("H1_HEARTBEAT_HARDLINK_at_path_REFUSED_by_the_NAME_SCREEN_victim_intact",
     e.get("written") is False and e.get("refused") is True and v.read_bytes() == VICTIM
     and "names" in str(e.get("reason")) and e.get("refused_by") == "pre_write_identity_check",
     {"refused_by": e.get("refused_by"), "reason": str(e.get("reason"))[:90], "victim_intact": v.read_bytes() == VICTIM})
# H2 SYMLINK at the path
def _h2(hbd, box):
    hbd.mkdir(); os.symlink(str(_victim(box)), str(hbd / "codex.jsonl"))
e, d = hb_case("h2_symlink_", _h2)
v = Path(d) / "USER_KEEPS_THIS.txt"
note("H2_HEARTBEAT_SYMLINK_at_path_REFUSED_by_the_NAME_SCREEN_victim_intact",
     e.get("written") is False and e.get("refused") is True and v.read_bytes() == VICTIM
     and e.get("refused_by") == "pre_write_identity_check",
     {"refused_by": e.get("refused_by"), "reason": str(e.get("reason"))[:90], "victim_intact": v.read_bytes() == VICTIM})
# H3 FIFO at the path: refused, no hang (and the FIFO is removed afterwards)
def _h3(hbd, box):
    hbd.mkdir(); os.mkfifo(str(hbd / "codex.jsonl"))
e, d = hb_case("h3_fifo_", _h3)
note("H3_HEARTBEAT_FIFO_at_path_REFUSED_by_the_NAME_SCREEN_no_hang",
     not e.get("_timeout") and e.get("written") is False and e.get("refused") is True
     and e.get("refused_by") == "pre_write_identity_check",
     {"timeout": e.get("_timeout"), "refused_by": e.get("refused_by"), "reason": str(e.get("reason"))[:90]})
os.unlink(str(Path(d) / "heartbeat" / "codex.jsonl"))
# H4 DIRECTORY at the path
def _h4(hbd, box):
    hbd.mkdir(); (hbd / "codex.jsonl").mkdir()
e, d = hb_case("h4_dir_", _h4)
note("H4_HEARTBEAT_DIRECTORY_at_path_REFUSED_by_the_NAME_SCREEN",
     e.get("written") is False and e.get("refused") is True and (Path(d) / "heartbeat" / "codex.jsonl").is_dir()
     and e.get("refused_by") == "pre_write_identity_check",
     {"refused_by": e.get("refused_by"), "reason": str(e.get("reason"))[:90]})
# H5 the heartbeat DIR is a symlink elsewhere: the parent chain does not resolve to the named path
def _h5(hbd, box):
    elsewhere = box / "elsewhere"; elsewhere.mkdir(); os.symlink(str(elsewhere), str(hbd))
e, d = hb_case("h5_parent_link_", _h5)
note("H5_HEARTBEAT_PARENT_CHAIN_relinked_REFUSED_nothing_written_elsewhere",
     e.get("written") is False and e.get("refused") is True
     and not (Path(d) / "elsewhere" / "codex.jsonl").exists(),
     {"reason": str(e.get("reason"))[:90], "elsewhere_untouched": not (Path(d) / "elsewhere" / "codex.jsonl").exists()})
# H5' the ANCESTOR (~/.voidloom) is a symlink elsewhere and the heartbeat dir does NOT exist
# yet (P2): the iteration-2 mkdir(parents=True) created elsewhere/heartbeat/ BEFORE the gate.
def _h5b(hbd, box):
    elsewhere = box / "elsewhere"; elsewhere.mkdir(); vl_ = box / "dot_voidloom"; os.symlink(str(elsewhere), str(vl_))
d5b = md("h5b_relinked_ancestor_")
_hbd5b = Path(d5b) / "dot_voidloom" / "heartbeat"
_h5b(_hbd5b, Path(d5b))
s5b = Path(d5b) / "hb.py"
s5b.write_text(f"""
import json, os, sys
os.environ["VOIDLOOM_HEARTBEAT_DIR"] = {str(_hbd5b)!r}
sys.path.insert(0, {ORGANS!r})
import voidloom_heartbeat as hb
r = hb.append_heartbeat("codex", session_id="s-1", preamble_sha="x", derivation="battery")
print("JSONSTART" + json.dumps(r, default=str))
""")
p5b = subprocess.run([sys.executable, str(s5b)], capture_output=True, text=True, timeout=20)
try:
    e5b = json.loads(p5b.stdout.split("JSONSTART")[-1])
except Exception:
    e5b = {"_err": p5b.stdout[-200:] + "|" + p5b.stderr[-300:]}
_far = sorted(x.name for x in (Path(d5b) / "elsewhere").iterdir())
note("H5b_HEARTBEAT_RELINKED_ANCESTOR_dir_absent_REFUSED_NOTHING_created_far_side",
     e5b.get("written") is False and e5b.get("refused") is True
     and e5b.get("refused_by") == "parent_chain_resolves_elsewhere" and _far == [],
     {"refused_by": e5b.get("refused_by"), "far_side_entries": _far, "reason": str(e5b.get("reason"))[:90]})
# H6 NEGATIVE CONTROL: the legitimate first fire (dir absent) and the legitimate second fire (append)
def _h6(hbd, box):
    pass
e, d = hb_case("h6_legit_", _h6)
hbf = Path(d) / "heartbeat" / "codex.jsonl"
first_ok = e.get("written") is True and hbf.exists() and len(hbf.read_text().splitlines()) == 1
e2, d2 = hb_case("h6_legit2_", lambda hbd, box: (hbd.mkdir(), (hbd / "codex.jsonl").write_text('{"v":2,"ts":"x","session_id":"s-0"}\n')))
hbf2 = Path(d2) / "heartbeat" / "codex.jsonl"
note("H6_NEGATIVE_CONTROL_legit_first_fire_creates_and_second_fire_appends",
     first_ok and e2.get("written") is True and len(hbf2.read_text().splitlines()) == 2
     and e.get("writer") == "voidloom_hooks_status.guarded_append_bytes",
     {"first": e.get("written"), "second_lines": len(hbf2.read_text().splitlines()) if hbf2.exists() else None,
      "writer": e.get("writer")})

# ---------------------------------------------------------------------
# H7-H12 -- IN-WINDOW shapes against the APPEND face (A8, reflector iter 2): the
# shape is planted AFTER path_identity returns clean and BEFORE the os.open --
# the window where S2 / A6 / A9 of the install battery live.  The plan-identity
# shapes (S2 / S3 / A9 / A11) are N/A for a plan-less writer (named in `mapping`).
# The FIFO here is the RM19x RED: without O_NONBLOCK the open blocks forever;
# with it the kernel answers ENXIO and the label is ENXIO_at_open (P3).
# ---------------------------------------------------------------------
def hb_window(shape):
    bx = md(f"hw_{shape}_")
    s_ = Path(bx) / "w.py"
    s_.write_text(f"""
import json, os, sys, signal
from pathlib import Path
sys.path.insert(0, {ORGANS!r})
import voidloom_hooks_status as hs
bx = Path({bx!r}); p = bx / "codex.jsonl"; victim = bx / "victim.txt"; victim.write_bytes(b"USER\\n")
shape = {shape!r}
if shape == "nlink_rises":
    p.write_bytes(b"ours-so-far\\n")
orig = hs.path_identity
def planted(path):
    ident = orig(path)
    if shape == "symlink": os.symlink(victim, p)
    elif shape == "hardlink": os.link(victim, p)
    elif shape == "fifo": os.mkfifo(p)
    elif shape == "directory": p.mkdir()
    elif shape == "nlink_rises": os.link(p, bx / "othername.jsonl")
    elif shape == "chardev_symlink": os.symlink("/dev/null", p)
    hs.path_identity = orig
    return ident
hs.path_identity = planted
res = {{"shape": shape}}
signal.alarm(15)
try:
    w = hs.guarded_append_bytes(str(p), b'{{"line":1}}\\n'); res["result"] = "WROTE"
except hs.PathIsNotWhatWeNamed as e:
    res["result"] = "REFUSED"; res["refused_by"] = getattr(e, "refused_by", None)
except OSError as e:
    res["result"] = "OSERROR:%s" % e.errno
finally:
    signal.alarm(0); hs.path_identity = orig
res["victim_intact"] = victim.read_bytes() == b"USER\\n"
if shape == "nlink_rises": res["ours_intact"] = p.read_bytes() == b"ours-so-far\\n"
if shape == "fifo":
    try: os.unlink(p)
    except OSError: pass
print("JSONSTART" + json.dumps(res))
""")
    try:
        p_ = subprocess.run([sys.executable, str(s_)], capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return {"shape": shape, "_timeout": True, "result": "TIMEOUT"}
    out = p_.stdout.split("JSONSTART")[-1] if "JSONSTART" in p_.stdout else ""
    try:
        return json.loads(out)
    except Exception:
        return {"shape": shape, "_err": p_.stdout[-200:] + "|" + p_.stderr[-300:], "result": "ERR"}

_expect = {"symlink": "O_NOFOLLOW_at_open", "hardlink": "identity_gate_link_count", "fifo": "ENXIO_at_open",
           "directory": "EISDIR_at_open", "nlink_rises": "identity_gate_link_count", "chardev_symlink": "O_NOFOLLOW_at_open"}
for _i, (_shape, _exp) in enumerate(_expect.items(), start=7):
    _r = hb_window(_shape)
    _ok = (_r.get("result") == "REFUSED" and _r.get("refused_by") == _exp and _r.get("victim_intact") is True
           and (_r.get("ours_intact", True) is True))
    note(f"H{_i}_IN_WINDOW_{_shape.upper()}_on_append_face_REFUSED_{_exp}", _ok,
         {"result": _r.get("result"), "refused_by": _r.get("refused_by"), "victim_intact": _r.get("victim_intact"),
          "ours_intact": _r.get("ours_intact"), "timeout": _r.get("_timeout")})
# sweep any FIFO the window shapes left (registered below by Z as well)
for _f in Path(ROOT).rglob("codex.jsonl"):
    try:
        if stat.S_ISFIFO(os.lstat(_f).st_mode):
            os.unlink(_f)
    except OSError:
        pass

# =====================================================================
# A12' -- ELEMENT-LEVEL undo refuses a LOOK-ALIKE (our basename, another brain's path)
# =====================================================================
d, ch, rp, st = box("a12_lookalike_")
named = Path(ch) / "hooks.json"; named.write_bytes(USER)
e0 = in_process(d, ch, rp, st, "")
doc12 = json.loads(named.read_bytes())
for g in doc12["hooks"]["SessionStart"]:
    for h in g["hooks"]:
        if "session_start_inject" in h.get("command", ""):
            h["command"] = "python3 /opt/other-brain/hooks/session_start_inject.py"
named.write_bytes((json.dumps(doc12, indent=2) + "\n").encode()); LOOK = named.read_bytes()
e = in_process(d, ch, rp, st, "", phase="uninstall")
note("A12_ELEMENT_LEVEL_UNDO_refuses_a_look_alike_from_another_brain",
     result_of(e0)[0] == "INSTALLED" and e.get("plan_blocked") is True
     and e.get("plan_refusal") == ["ELEMENT_NOT_OURS"] and named.read_bytes() == LOOK
     and ((e.get("res") or {}).get("elements_removed") or 0) == 0,
     {"install": result_of(e0)[0], "plan_refusal": e.get("plan_refusal"), "file_intact": named.read_bytes() == LOOK})

# no FIFO may be left under this root (A9 class)
_left = [os.path.join(dp, f) for dp, dn, fn in os.walk(ROOT) for f in fn
         if stat.S_ISFIFO(os.lstat(os.path.join(dp, f)).st_mode)]
for f in _left:
    try:
        os.unlink(f)
    except OSError:
        pass
note("Z_NO_FIFO_LEFT_under_the_battery_root", _left == [], {"fifos_left": len(_left)})

held = sum(1 for v in R.values() if v["held"])
print(json.dumps({"scratch_root": ROOT, "dev": FS_DEV, "inode_numbers_recycled": INODE_REUSE,
                  "summary": {k: v["held"] for k, v in R.items()},
                  "held": held, "total": len(R),
                  "violated": sorted(k for k, v in R.items() if not v["held"]),
                  "mapping": {"S5": "S5' receipt hardlink", "A8": "A8' uninstall-delete of a created file (dir swap)",
                              "A10": "A10' uninstall-delete of a created file (rename swap)",
                              "A11": "A11' element-level undo under a rename swap",
                              "N1/N2": "negative controls added this round",
                              "heartbeat_writer": {"pre_existing": "H1-H5 + H5b (re-linked ancestor, dir absent)",
                                                   "in_window": "H7-H12 (symlink, hardlink, FIFO->ENXIO, directory, nlink rises, chardev link)",
                                                   "legitimate": "H6 (first fire creates, second appends)",
                                                   "N/A_for_a_plan_less_writer": ["S2 (plan-identity swap)", "S3 (renamed prefix vs plan)",
                                                                                  "A9 (inode reuse vs plan content)", "A11 (element-level undo)"],
                                                   "why_N/A": "the heartbeat records no plan; there is no planned identity or content to drift from"}}},
                 indent=1))
print(f"\n  ===> {held}/{len(R)} SHAPES HELD  on dev={FS_DEV} (inode reuse: {INODE_REUSE})   [install writer + uninstall side + heartbeat writer]\n")
sys.exit(0 if held == len(R) else 1)

#!/usr/bin/env python3
"""C2 — does the installer build a brain its own package can read?

WHY THIS IS A SEPARATE HARNESS.  `cli_install_suite.py` hard-links a pre-built
`claude_memory.db` into every fixture (`:550`), so it can never see a blank
brain: a C2 pair placed there would be GREEN BY CONSTRUCTION.  That is exactly
why 89/89 was blind to a fresh install carrying 24 of 66 objects.  This suite
runs the shipped `install.sh` itself, into a scratch HOME, and reads the
resulting `sqlite_master`.

    usage: c2_install_schema_suite.py <package_dir> <scratch_root> [--only NAME]

`package_dir` is a checkout of the package (what a stranger unzips).
Every check drives the SHIPPED script as a subprocess -- never a re-implementation.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

if len(sys.argv) < 3:
    sys.exit("usage: c2_install_schema_suite.py <package_dir> <scratch_root> [--only NAME]")
PKG = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)

RESULTS: List[Dict[str, Any]] = []


def _declared_device() -> str:
    """This machine's configured compute index; "0" if the organ is unreachable."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "organs"))
        from voidloom_device import declared_device  # type: ignore
        return declared_device()
    except Exception:
        return "0"


def note(name: str, held: bool, detail: Any) -> None:
    if ONLY and ONLY not in name:
        return
    RESULTS.append({"name": name, "held": bool(held), "detail": detail})
    print(f"  {name:<62s} {'HELD' if held else 'VIOLATED':<9s} {json.dumps(detail, default=str)[:260]}")


def box(tag: str) -> str:
    """A scratch HOME on the SAME filesystem as the brain (ext4 under $HOME),
    never /tmp: tmpfs recycles inodes differently and several install steps
    assert on st_dev."""
    p = os.path.join(ROOT, tag)
    shutil.rmtree(p, ignore_errors=True)
    os.makedirs(p)
    return p


def _default_py_first() -> str:
    """The directory of the interpreter RUNNING this suite.

    D8-F7: this defaulted to the author's anaconda bin. On any other host the
    installer was handed a PATH entry that does not exist, and the `env -i`
    environment then found whatever python happened to be in /usr/bin -- a
    different interpreter from the one collecting these tests, silently.
    """
    import sys as _sys
    from pathlib import Path as _P
    return str(_P(_sys.executable).resolve().parent)


def install(home: str, *args: str, py_first: str = "",
            timeout: int = 1800) -> Tuple[int, str]:
    """Run the SHIPPED install.sh in a stated environment.

    `env -i`-equivalent: the installer must not inherit this session's HOME,
    CLAUDE_CONFIG_DIR or WIZARD_* -- a fixture that inherits its world measures
    the invoker, not the code (the C1 lesson, applied here before it bites)."""
    py_first = py_first or _default_py_first()
    env = {
        "HOME": home,
        "PATH": f"{py_first}:/usr/bin:/bin:/usr/local/bin",
        "USER": os.environ.get("USER", "user"),
        "TERM": "dumb",
        "SHELL": "/bin/bash",
        # DECLARED, not typed: "1" is the author's box (index 0 drives his monitor)
        # and does not exist on a single-GPU host, where it makes the child see no
        # CUDA at all. Same reader gpu_guard and voidloom_manifest use.
        "CUDA_VISIBLE_DEVICES": _declared_device(),
    }
    p = subprocess.run(["bash", os.path.join(PKG, "install.sh"), *args],
                       capture_output=True, text=True, env=env, timeout=timeout)
    log = p.stdout + p.stderr
    pathlib.Path(home, "install.log").write_text(log, encoding="utf-8")
    return p.returncode, log


# ---------------------------------------------------------------------------
# THE REFERENCE SET — derived by EXECUTION, never by parsing the SQL.
# Building a throwaway brain from the package's own schema.sql + every
# migration_*.sql and reading its sqlite_master is the live enumerator for
# "what this package's brain is supposed to contain".  A regex over CREATE
# statements is a second, worse declaration of the same fact -- it captured
# `IF` and a migration's temp `cross_tier_bridges_new` when tried.
# ---------------------------------------------------------------------------
_BENIGN = ("already exists", "duplicate column name")


def _iter_statements(sql: str):
    """Yield complete statements.  A leading comment belongs to the statement
    it introduces: discarding the buffer on a '--' line drops every statement
    that carries a comment, which in this package is most of them."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt, buf = buf, ""
            if any(l.strip() and not l.strip().startswith("--") for l in stmt.splitlines()):
                yield stmt
    if buf.strip() and any(l.strip() and not l.strip().startswith("--") for l in buf.splitlines()):
        yield buf


def reference_objects(pkg: str, dest: str) -> Tuple[set, List[str]]:
    mig_dir = pathlib.Path(pkg, "migrations")
    pathlib.Path(dest).unlink(missing_ok=True)
    # autocommit: several migrations carry their own BEGIN/COMMIT, and python's
    # implicit transaction turns those into "cannot start a transaction within
    # a transaction" -- 2 files silently lost before this was set.
    c = sqlite3.connect(dest, isolation_level=None)
    try:
        c.enable_load_extension(True)
        import sqlite_vec  # type: ignore
        sqlite_vec.load(c)
        c.enable_load_extension(False)
    except Exception:
        pass
    unmeasurable: List[str] = []
    for f in [mig_dir / "schema.sql"] + sorted(mig_dir.glob("migration_*.sql")):
        for stmt in _iter_statements(f.read_text(encoding="utf-8")):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if any(b in str(e) for b in _BENIGN):
                    continue
                if "no such module" in str(e):      # a vec0 table on a tier that has no vec0
                    unmeasurable.append(f"{f.name}: {e}")
                    continue
                raise
    objs = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    c.close()
    return objs, unmeasurable


def brain_objects(db: str) -> set:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    o = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    c.close()
    return o


REF, REF_UNMEASURABLE = reference_objects(PKG, os.path.join(ROOT, "_reference.db"))
print(f"\n  reference brain built from the package: {len(REF)} objects"
      f"{f'  ({len(REF_UNMEASURABLE)} unmeasurable without vec0)' if REF_UNMEASURABLE else ''}\n")

DBREL = ".claude/memory/claude_memory.db"

# ===========================================================================
# C2.1 — a FRESH --init-blank install carries what the package declares
# ===========================================================================
h1 = box("c1_initblank")
rc1, log1 = install(h1, "--init-blank", "--tier0-only")
db1 = os.path.join(h1, DBREL)
got1 = brain_objects(db1) if os.path.exists(db1) else set()
miss1 = sorted(REF - got1)
note("C2_1_init_blank_brain_carries_every_object_the_package_declares",
     rc1 == 0 and not miss1,
     {"install_rc": rc1, "reference": len(REF), "installed": len(got1),
      "missing": len(miss1), "sample": miss1[:8]})

# ===========================================================================
# C2.2 — the CLONE path brings a stale snapshot forward too
#   A snapshot older than the package's migrations is the same class: the
#   installer copies it and asserts a 2-item list over it.
# ===========================================================================
stale = os.path.join(ROOT, "stale_snapshot.db")
if os.path.exists(db1):
    shutil.copy2(db1, stale)
    c = sqlite3.connect(stale)
    for t in ("decision_units", "self_eval_reports", "edge_proposals",
              "whisper_observations", "organ_run_log"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.commit(); c.close()
h2 = box("c2_clone")
rc2, log2 = install(h2, "--brain-db", stale, "--tier0-only")
db2 = os.path.join(h2, DBREL)
got2 = brain_objects(db2) if os.path.exists(db2) else set()
miss2 = sorted(REF - got2)
note("C2_2_clone_of_a_stale_snapshot_is_brought_up_to_the_package",
     rc2 == 0 and not miss2,
     {"install_rc": rc2, "reference": len(REF), "installed": len(got2),
      "missing": len(miss2), "sample": miss2[:8]})

# ===========================================================================
# C2.3 — self_check FAILS (non-zero) on a brain missing declared objects
# ===========================================================================
h3 = box("c3_selfcheck")
shutil.copytree(os.path.join(h1, ".claude"), os.path.join(h3, ".claude"))
db3 = os.path.join(h3, DBREL)
c = sqlite3.connect(db3)
for t in ("decision_units", "decision_events", "self_eval_reports"):
    c.execute(f"DROP TABLE IF EXISTS {t}")
c.commit(); c.close()
sc = os.path.join(h3, ".claude/memory/install/self_check.py")
p3 = subprocess.run([sys.executable, sc, "--quiet"], capture_output=True, text=True,
                    env={**os.environ, "WIZARD_LIBRARY_DB": db3, "HOME": h3}, timeout=600)
note("C2_3_self_check_FAILS_when_declared_objects_are_absent",
     p3.returncode != 0,
     {"self_check_rc": p3.returncode, "dropped": 3,
      "stdout_tail": (p3.stdout or "")[-180:], "stderr_tail": (p3.stderr or "")[-180:]})

# ===========================================================================
# C2.4 — STEP E cannot print "alive"/"usable" over a failing self_check,
#        and the install does not exit 0 on it.
# ===========================================================================
h4 = box("c4_stepE")
shutil.copytree(os.path.join(h1, ".claude"), os.path.join(h4, ".claude"))
db4 = os.path.join(h4, DBREL)
c = sqlite3.connect(db4)
for t in ("decision_units", "decision_events", "self_eval_reports", "edge_proposals"):
    c.execute(f"DROP TABLE IF EXISTS {t}")
c.commit(); c.close()
rc4, log4 = install(h4, "--tier0-only")       # idempotent re-run over the damaged brain
claimed = ("the install is alive" in log4) or ("brain usable" in log4)
still_missing = sorted(REF - brain_objects(db4))
note("C2_4_STEP_E_does_not_certify_a_brain_that_is_missing_objects",
     (not still_missing) or (not claimed and rc4 != 0),
     {"install_rc": rc4, "claimed_alive_or_usable": claimed,
      "missing_after": len(still_missing), "sample": still_missing[:6]})

# ===========================================================================
# C2.5 — B.3 calls itself the Tier-0 stdlib path; it must not load the stack
# ===========================================================================
# Read the CODE, not the prose. The first version scanned a raw text window and
# counted `TIER0_OK=1` inside the very comment that EXPLAINS the old defect --
# so writing down what was wrong made the check report it as still wrong. A
# textual check over a file that documents itself measures the documentation.
_src = pathlib.Path(PKG, "install.sh").read_text(encoding="utf-8").splitlines()
_code = [l for l in _src if not l.strip().startswith("#")]
_i = next((i for i, l in enumerate(_code) if "Tier 0 VERIFIED" in l), None)
if _i is None:
    b3_code: List[str] = []
else:
    _start = next(i for i in range(_i, -1, -1) if _code[i].lstrip().startswith("if "))
    _end = next((i for i in range(_i, len(_code)) if _code[i].strip() == "fi"), _i)
    b3_code = _code[_start:_end + 1]
_joined = "\n".join(b3_code)
uses_full_engine = "claude_memory.py" in _joined and "claude_memory_lite.py" not in _joined
arms_both_pass = len(re.findall(r"TIER0_OK=1", _joined)) >= 2
note("C2_5_the_Tier0_gate_uses_the_Tier0_engine_and_honours_its_own_verdict",
     bool(b3_code) and (not uses_full_engine) and (not arms_both_pass)
     and "TIER0_OK=0" in _joined,
     {"block_lines": len(b3_code), "names_full_engine": uses_full_engine,
      "both_arms_set_TIER0_OK_1": arms_both_pass,
      "failure_arm_records_failure": "TIER0_OK=0" in _joined})

# ===========================================================================
# C2.6 — --dry-run writes NOTHING
# ===========================================================================
# The dry-run guards sit INSIDE `[ -f "${BRAIN_DIR}/..." ]` tests, so a dry-run
# into an EMPTY home cannot reach them: the first version of this check dry-ran
# into a fresh box, found nothing written, and reported HELD over a live defect.
# That is the denominator error this brain already records (#17369) -- testing
# the one configuration in which the defect under test is invisible. The dry-run
# must therefore be aimed at a POPULATED install, which is also the case that
# matters: a dry-run pointed at a real HOME.
h6 = box("c6_dryrun")
install(h6, "--init-blank", "--tier0-only")            # a real install first
def _fingerprint(root):
    """Content only.

    `-shm` and `-wal` are SQLite's coordination files: ANY reader of a WAL
    database touches them, and the installer legitimately opens the brain
    read-only while a dry-run is printing its summary (traced: a python
    heredoc taking DB_PATH, before the banner). Counting those as writes would
    make the check unfalsifiable-in-reverse -- it could never go green even on
    a correct dry-run -- so they are excluded BY NAME and the database's own
    content is asserted separately, through the file itself and the row count.
    """
    out = {}
    for f in pathlib.Path(root).rglob("*"):
        if f.is_file() and f.name != "install.log" and not f.name.endswith(("-shm", "-wal")):
            try:
                st = f.stat()
                out[str(f)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return out
before = _fingerprint(h6)
mem_before = None
db6 = os.path.join(h6, DBREL)
if os.path.exists(db6):
    c = sqlite3.connect(f"file:{db6}?mode=ro", uri=True)
    mem_before = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]; c.close()
rc6, log6 = install(h6, "--dry-run", "--init-blank", "--tier0-only")
after = _fingerprint(h6)
created = sorted(set(after) - set(before))
modified = sorted(k for k in set(after) & set(before) if after[k] != before[k])
mem_after = None
if os.path.exists(db6):
    c = sqlite3.connect(f"file:{db6}?mode=ro", uri=True)
    mem_after = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]; c.close()
note("C2_6_dry_run_over_a_POPULATED_install_writes_nothing",
     not created and not modified and mem_before == mem_after,
     {"install_rc": rc6, "created": len(created), "modified": len(modified),
      "memories_before": mem_before, "memories_after": mem_after,
      "sample": [os.path.relpath(x, h6) for x in (created + modified)[:8]]})

# ===========================================================================
# C2.7 — the migration machinery is stdlib-only (Tier-0 must not regress)
# ===========================================================================
mig = os.path.join(PKG, "organs", "schema_migrator.py")
if os.path.exists(mig):
    p7 = subprocess.run(["/usr/bin/python3", mig, "--help"], capture_output=True,
                        text=True, timeout=120)
    ok7 = p7.returncode == 0
    d7 = {"exists": True, "stdlib_rc": p7.returncode, "stderr_tail": (p7.stderr or "")[-160:]}
else:
    ok7, d7 = False, {"exists": False, "note": "no organs/schema_migrator.py in the package"}
note("C2_7_the_migration_runner_runs_on_a_stdlib_only_interpreter", ok7, d7)

# ===========================================================================
# C2.8 — RE-APPLYING MIGRATIONS MUST NOT DESTROY ROWS
#   Four of the 29 migrations DROP or DELETE. `migration_022` purges
#   cross_tier_bridges by design and its header assumes a "schema_version check
#   in the runner" that has never existed. The first version of schema_migrator
#   re-applied everything unconditionally: measured against a copy of the live
#   brain, cross_tier_bridges went 26 rows -> 0, reporting missing:0 and rc 0.
#   It did not error. It succeeded, and succeeding was the damage.
# ===========================================================================
mig = os.path.join(PKG, "organs", "schema_migrator.py")
if os.path.exists(db1):
    rows_db = os.path.join(ROOT, "reapply_probe.db")
    shutil.copy2(db1, rows_db)
    c = sqlite3.connect(rows_db)
    c.execute("INSERT INTO cross_tier_bridges (local_memory_id, remote_tier, remote_db_path,"
              " remote_memory_id, bridge_type) VALUES (1,'eddie-windows','/x',1,'same_topic')")
    c.commit()
    def _counts(path):
        cc = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        out = {}
        for t in ("memories", "cross_tier_bridges", "memory_tags", "decision_units"):
            try:
                out[t] = cc.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        cc.close()
        return out
    c.close()
    b4 = _counts(rows_db)
    for _ in range(2):
        subprocess.run([sys.executable, mig, "apply", "--db", rows_db],
                       capture_output=True, text=True, timeout=600)
    af = _counts(rows_db)
    lost = {k: (b4[k], af[k]) for k in b4 if b4[k] != af[k]}
    note("C2_8_re_applying_migrations_destroys_no_rows", not lost,
         {"before": b4, "after": af, "lost": lost})
else:
    note("C2_8_re_applying_migrations_destroys_no_rows", False,
         {"note": "no brain from C2.1 to probe"})

# ---------------------------------------------------------------------------
held = sum(1 for r in RESULTS if r["held"])
print(json.dumps({"package": PKG, "scratch_root": ROOT,
                  "reference_objects": len(REF),
                  "reference_unmeasurable": REF_UNMEASURABLE,
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

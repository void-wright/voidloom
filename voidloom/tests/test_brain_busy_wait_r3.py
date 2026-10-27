# -*- coding: utf-8 -*-
"""Round 3 G2 item 2 (model-choice; ruler VL-MC-v1.2): the busy-wait CLASS -- no connection that can WRITE the brain
waits less than 60 s for the write lock.

WHY. On Oct 1 2026 the host woke at 08:54 and systemd's Persistent=true started the missed quarterly counterfactual
revisit in the SAME second as the monthly self-eval. The revisit's UPDATE met the self-eval's write lock, waited
Python's default 5 s, and died: `sqlite3.OperationalError: database is locked` (memory #23545). Narrow pass N fixed
that ONE organ with a 60 s busy wait (the value gap_mapping already used for the same storm). The lesson was the class:
Persistent=true turns a sleeping host's missed windows into SIMULTANEOUS catch-ups, so every brain-writing organ needs
the wait, not just a schedule that keeps them apart. N's own sweep named the rest (dream_pipeline.py:235 first, the
22:00 dream), and Round 3 sweeps them all.

THE RULE (one definition, enforced over organs/, voidloom/, hooks/ and install/ of THIS tree): every sqlite3.connect
call waits >= 60 s (`timeout=` a literal or a module constant >= 60) unless it CANNOT write the brain under contention:
a read-only URI (`mode=ro` / `immutable` in the call or the uri variable it is given; a `*_ro` / `*readonly*` helper; a
`_ro_uri(...)` argument), ':memory:', or a self-test function on a scratch DB (`unit_test*`, `test_*`, `run_test_*`,
`_run_tests`, `_setup_db`, `_create_test_db`, `setUp`, `cmd_test`). Four further sites are exempt BY NAME, each read
and reasoned in EXEMPT below. A `PRAGMA busy_timeout=N` with N < 60000 anywhere is a violation too: a PRAGMA after
the connect OVERRIDES the connect's wait (claude_memory's `_get_conn` set 5000 after any connect timeout).

On the N bytes (voidloom f8f0c30 = artifacts_r2): 64 violations in 38 files (the static row below is RED); on Round
3's: 0 (G1 owns two of the files -- install/self_check.py, organs/claude_memory_lite.py -- and applies G2's one-line
patches). The dynamic rows open the brain through each scheduled organ's OWN connection helper and read the busy wait
SQLite will apply; two rows put a real concurrent writer in front of a helper's write (RED: dies at 5 s).
"""
import ast
import importlib
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST_FUNC = re.compile(r"^(_?run_(unit_)?tests|run_test_\w*|unit_test\w*|test_\w*|setUp|_setup_db|_create_test_db|cmd_test)$")
RO_FUNC = re.compile(r"(^|_)ro$|readonly|_connect_ro$|_open_ro$")
EXEMPT = {
    ("organs/voidloom_canary.py", "self_check"): "the canary's self-check builds its own temp DB under a mkdtemp root",
    ("organs/voidloom_preamble.py", "_premigration_copy"): "writes a fresh scratch COPY file (dst), never the brain",
    ("organs/pre_cognitive_recall.py", "_recent_memory_stores"):
        "a READ in the per-prompt hook (SELECT only); its 2 s budget bounds the prompt's latency",
    ("organs/pre_cognitive_recall.py", "_recent_automation_hint"):
        "a READ in the per-prompt hook (SELECT only); its 2 s budget bounds the prompt's latency",
}
PRAGMA_RE = re.compile(r"busy_timeout\s*=\s*(\d+)", re.I)


def _sites(src, rel):
    mod = ast.parse(src)
    parents = {}
    for node in ast.walk(mod):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node

    def func_node(n):
        while n in parents:
            n = parents[n]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return n
        return None
    consts = {}
    for st in mod.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            try:
                consts[st.targets[0].id] = float(ast.literal_eval(st.value))
            except Exception:
                pass
    for node in ast.walk(mod):
        fn = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(fn, ast.Attribute) and fn.attr == "connect"
                and isinstance(fn.value, ast.Name) and fn.value.id in ("sqlite3", "_sqlite3")):
            continue
        seg = ast.get_source_segment(src, node) or ""
        fnode = func_node(node)
        func = fnode.name if fnode is not None else "<module>"
        fsrc = ast.get_source_segment(src, fnode) if fnode is not None else ""
        tnode = node.args[1] if len(node.args) >= 2 else None
        for kw in node.keywords:
            if kw.arg == "timeout":
                tnode = kw.value
        tval = None
        if tnode is not None:
            try:
                tval = float(ast.literal_eval(tnode))
            except Exception:
                tval = consts.get(tnode.id) if isinstance(tnode, ast.Name) else None
        first = ast.get_source_segment(src, node.args[0]) if node.args else ""
        ro_var = bool(node.args and isinstance(node.args[0], ast.Name) and re.search(
            r"\b%s\s*=\s*f?[\"']file:[^\"'\n]*mode=ro[\"'&]" % re.escape(node.args[0].id), fsrc or ""))
        exempt = ("mode=ro" in seg or "immutable" in seg or bool(RO_FUNC.search(func)) or "_ro_uri(" in first or ro_var
                  or first.strip() in ('":memory:"', "':memory:'") or bool(TEST_FUNC.match(func))
                  or (rel, func) in EXEMPT)
        yield node.lineno, func, tval, exempt


def _violations(root):
    bad, n = [], 0
    files = sorted(list(root.glob("organs/*.py")) + list(root.glob("voidloom/*.py")) + list(root.glob("hooks/*.py"))
                   + list(root.glob("install/*.py")))
    for f in files:
        rel = str(f.relative_to(root))
        src = f.read_text(encoding="utf-8", errors="replace")
        for line, func, tval, exempt in _sites(src, rel):
            n += 1
            if not exempt and not (isinstance(tval, float) and tval >= 60.0):
                bad.append("%s:%d %s (waits %s)" % (rel, line, func, "5 s default" if tval is None else "%s s" % tval))
        for i, l in enumerate(src.splitlines(), 1):
            for m in PRAGMA_RE.finditer(l):
                if "PRAGMA" in l.upper() and int(m.group(1)) < 60000:
                    bad.append("%s:%d PRAGMA busy_timeout=%s overrides the connect's wait" % (rel, i, m.group(1)))
    return bad, n, len(files)


def test_no_connection_that_can_write_the_brain_waits_less_than_60_s():
    bad, n, nfiles = _violations(ROOT)
    assert nfiles > 50 and n > 100, (nfiles, n)          # the sweep saw the package, not an empty folder
    assert not bad, "%d connection(s) wait < 60 s for the brain's write lock:\n  %s" % (len(bad), "\n  ".join(bad))


def test_every_exemption_by_name_still_names_a_real_site():
    """An EXEMPT entry whose function is gone is a stale pardon: it must be removed, not silently kept."""
    for (rel, func) in EXEMPT:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert any(f == func for _l, f, _t, _e in _sites(src, rel)), (rel, func)


def test_the_rule_catches_its_own_falsifiers(tmp_path):
    """The lint is an instrument: prove it can fail. Each shape below is a violation; each control is not."""
    org = tmp_path / "organs"
    org.mkdir()
    for d in ("voidloom", "hooks", "install"):
        (tmp_path / d).mkdir()
    (org / "bad.py").write_text(
        "import sqlite3\n"
        "def a(p):\n    return sqlite3.connect(p)\n"
        "def b(p):\n    return sqlite3.connect(p, timeout=5.0)\n"
        "def c(p):\n    c = sqlite3.connect(p, timeout=60.0)\n    c.execute('PRAGMA busy_timeout=5000')\n    return c\n"
        "SHORT = 30\n"
        "def d(p):\n    return sqlite3.connect(p, timeout=SHORT)\n")
    (org / "good.py").write_text(
        "import sqlite3\nBUSY = 60.0\n"
        "def a(p):\n    return sqlite3.connect(p, timeout=BUSY)\n"
        "def b(p):\n    return sqlite3.connect('file:%s?mode=ro' % p, uri=True)\n"
        "def c(p):\n    uri = f'file:{p}?mode=ro'\n    return sqlite3.connect(uri, uri=True)\n"
        "def unit_test_x():\n    return sqlite3.connect('/tmp/x.db')\n"
        "def e():\n    return sqlite3.connect(':memory:')\n")
    bad, _n, _f = _violations(tmp_path)
    # bad.py: a() line 3 (the 5 s default), b() line 5 (timeout=5.0), c()'s PRAGMA line 8, d() line 12 (SHORT = 30)
    assert sorted(b.split(" ")[0] for b in bad) == sorted(["organs/bad.py:3", "organs/bad.py:5", "organs/bad.py:8",
                                                            "organs/bad.py:12"]), bad


# ---------------------------------------------------------------- dynamic: the organs' OWN helpers, on a scratch brain
def _scratch_brain(tmp_path):
    db = tmp_path / "claude_memory.db"
    c = sqlite3.connect(str(db))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT INTO metadata VALUES ('k', 'v')")
    c.commit()
    c.close()
    return db


# (module, helper, how to call it): the connection helpers of the SCHEDULED organs (install/cron_roster.json) and of
# the recall/store path -- each returns the connection the organ then writes through.
HELPERS = [
    ("dream_pipeline", "_get_conn", lambda m, db: m._get_conn(str(db))),
    ("memory_dreaming", "_get_conn", lambda m, db: m._get_conn(str(db))),
    ("arcane_manifest", "_get_conn", lambda m, db: m._get_conn(str(db))),
    ("sleep_loop", "_get_conn", lambda m, db: m._get_conn(str(db))),
    ("dream_ledger", "_connect", lambda m, db: m._connect(str(db), False)),
    ("council", "_get_conn", lambda m, db: m._get_conn(str(db))),
    ("affect_probe_scan", "open_brain_conn", lambda m, db: m.open_brain_conn(str(db))),
    ("belief_cli", "_open_rw", lambda m, db: m._open_rw(str(db))),
    ("decision_units", "_open_rw", lambda m, db: m._open_rw(str(db))),
    ("voidloom_approvals", "_open_rw", lambda m, db: m._open_rw(str(db))),
    ("embedding_upgrade", "_get_conn", lambda m, db: m._get_conn(str(db))),
    ("quickening", "_open_db", lambda m, db: m._open_db(str(db))),
    ("communities", "_open_db", lambda m, db: m._open_db(str(db))),
]


@pytest.mark.parametrize("mod,helper,call", HELPERS, ids=["%s.%s" % (m, h) for m, h, _ in HELPERS])
def test_each_scheduled_organs_own_helper_opens_the_brain_with_a_60_s_wait(tmp_path, monkeypatch, mod, helper, call):
    db = _scratch_brain(tmp_path)
    monkeypatch.setenv("WIZARD_LIBRARY_DB", str(db))
    m = importlib.import_module(mod)
    try:
        conn = call(m, db)
    except SystemExit as e:                                   # a helper that refuses a brain without its schema
        pytest.fail("%s.%s refused the scratch brain: %s" % (mod, helper, e))
    try:
        wait_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert wait_ms >= 60000, "%s.%s waits %d ms for the write lock" % (mod, helper, wait_ms)


def test_claude_memory_get_conn_waits_60_s_after_its_own_pragma(tmp_path, monkeypatch):
    """The recall/store path's helper ran `PRAGMA busy_timeout=5000` AFTER connecting: the PRAGMA is what counts."""
    import claude_memory as cm
    db = _scratch_brain(tmp_path)
    monkeypatch.setattr(cm, "DB_PATH", db)
    monkeypatch.setattr(cm, "_warden", None, raising=False)
    conn = cm._get_conn()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 60000
    finally:
        conn.close()


def _hold_write_lock(db, seconds, ready):
    w = sqlite3.connect(str(db), isolation_level=None)
    w.execute("BEGIN IMMEDIATE")
    w.execute("UPDATE metadata SET value = value")
    ready.set()
    time.sleep(seconds)
    w.execute("COMMIT")
    w.close()


@pytest.mark.parametrize("mod,helper,call", [HELPERS[0], HELPERS[2]], ids=["dream_pipeline", "arcane_manifest"])
def test_a_write_through_the_helper_survives_a_concurrent_writer(tmp_path, monkeypatch, mod, helper, call):
    """The Oct 1 shape, on a scratch brain: another writer holds the lock 6.5 s; the organ's write waits it out."""
    db = _scratch_brain(tmp_path)
    m = importlib.import_module(mod)
    conn = call(m, db)
    ready = threading.Event()
    t = threading.Thread(target=_hold_write_lock, args=(db, 6.5, ready))
    t.start()
    assert ready.wait(10)
    t0 = time.monotonic()
    try:
        conn.execute("UPDATE metadata SET value = 'organ' WHERE key = 'k'")
        conn.commit()
    except sqlite3.OperationalError as e:
        pytest.fail("%s's write died while another writer held the brain: %s (after %.1f s)"
                    % (mod, e, time.monotonic() - t0))
    finally:
        t.join()
        conn.close()
    assert time.monotonic() - t0 >= 5.5          # it WAITED past the old 5 s budget

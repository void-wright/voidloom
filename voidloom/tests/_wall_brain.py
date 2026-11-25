# -*- coding: utf-8 -*-
"""Throwaway brains for the wall tests: the PACKAGE's own blank seed + shipped migrator, a deterministic bag-of-words
encoder (no model, no GPU, no network) and an environment in which nothing can resolve to the operator's brain."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "organs"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _owned_brain import packaged_brain  # noqa: E402

DIM = 1024
FRAME = "[external / unverified source"
_SEED = {}


def fake_vec(text) -> bytes:
    v = [0.0] * DIM
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) >= 3:
            v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return struct.pack("%df" % DIM, *[x / n for x in v])


def connect(db) -> sqlite3.Connection:
    import sqlite_vec
    c = sqlite3.connect(str(db), timeout=30)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def seed_brain(dst) -> Path:
    """A fresh copy of the packaged brain (built once per process, removed at interpreter exit)."""
    src = _SEED.get("p")
    if src is None or not Path(src).exists():
        d = tempfile.mkdtemp(prefix="wall-seed-")
        import atexit
        atexit.register(shutil.rmtree, d, True)
        src = _SEED["p"] = str(packaged_brain(Path(d) / "seed.db"))
    a, b = sqlite3.connect(src), sqlite3.connect(str(dst))
    a.backup(b)
    a.close()
    b.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
    b.commit()
    b.close()
    return Path(dst)


def columns(c, table="memories"):
    return {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}


class Brain:
    def __init__(self, tmp: Path, name="claude_memory.db"):
        tmp.mkdir(parents=True, exist_ok=True)
        self.path = str(seed_brain(tmp / name))
        self.c = connect(self.path)

    def add(self, content, *, marked=False, importance=0.5, kind="episodic", category="insight", project="proj",
            derived_from=None, confidence=1.0, tags=(), days_ago=0.0, source_user=None, **extra):
        vals = dict(content=content, summary=extra.pop("summary", content[:60]), category=category, kind=kind,
                    importance=importance, source_project=project, confidence=confidence, derived_from=derived_from)
        if source_user is not None:
            vals["source_user"] = source_user
        vals.update(extra)
        cur = self.c.execute("INSERT INTO memories (%s) VALUES (%s)" % (",".join(vals), ",".join("?" * len(vals))),
                             list(vals.values()))
        mid = cur.lastrowid
        if days_ago:
            self.c.execute("UPDATE memories SET created_at=datetime('now', ?), updated_at=datetime('now', ?), "
                           "last_accessed=datetime('now', ?) WHERE id=?", (("-%f days" % days_ago,) * 3 + (mid,)))
        if marked:
            self.mark(mid)
        for t in tags:
            self.c.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?,?)", (mid, t))
        self.c.execute("INSERT INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?,?)", (mid, fake_vec(content)))
        self.c.commit()
        return mid

    def mark(self, mid):
        self.c.execute("UPDATE memories SET untrusted_source=1 WHERE id=?", (mid,))
        self.c.commit()

    def marked(self, mid):
        r = self.c.execute("SELECT untrusted_source FROM memories WHERE id=?", (mid,)).fetchone()
        return None if r is None else r[0] == 1

    def importance(self, mid):
        return self.c.execute("SELECT importance FROM memories WHERE id=?", (mid,)).fetchone()[0]

    def all(self, sql, args=()):
        return self.c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.c.execute(sql, args).fetchone()

    def new_ids(self, before):
        return [r[0] for r in self.c.execute("SELECT id FROM memories WHERE id > ? ORDER BY id", (before,))]

    def top(self):
        return self.c.execute("SELECT COALESCE(MAX(id),0) FROM memories").fetchone()[0]


def child_env(db, **kw):
    """The environment of a CHILD process: the brain it was given, no model, no GPU, no other brain, no env file."""
    d = Path(db).parent
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VOIDLOOM_", "WIZARD_", "CLAUDE_"))}
    env.update(WIZARD_LIBRARY_DB=str(db), HOME=str(d), VOIDLOOM_ENV_DIR=str(d / "envdir"),
               WIZARD_ENV_FILE=str(d / "envdir" / "absent"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    (d / "envdir").mkdir(exist_ok=True)
    env.update(kw)
    return env


def lite_cli(db, *argv, timeout=120):
    """`python -m voidloom` in a child, Tier 0 (the lite engine): (rc, envelope or None, stdout, stderr)."""
    p = subprocess.run([sys.executable, "-B", "-m", "voidloom", "--db", str(db), *argv, "--json"], cwd=str(ROOT),
                       env=child_env(db, VOIDLOOM_TIER0_ONLY="1"), capture_output=True, text=True, timeout=timeout)
    return p.returncode, last_json(p.stdout), p.stdout, p.stderr


def last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                return v
        except ValueError:
            continue
    try:
        return json.loads(text)
    except ValueError:
        return None


@contextlib.contextmanager
def engine(db, env=None):
    """The FULL engine in this process over `db`, its encoder and reranker replaced by deterministic ones."""
    import claude_memory as cm
    import hybrid_retrieval as hr
    import memory_write as mw
    from voidloom import _facade

    def rerank(conn, query, ids, top_k=10):
        q = struct.unpack("%df" % DIM, fake_vec(query))
        out = []
        for i in ids:
            row = conn.execute("SELECT content FROM memories WHERE id=?", (i,)).fetchone()
            out.append((i, sum(x * y for x, y in zip(q, struct.unpack("%df" % DIM, fake_vec(row[0] if row else ""))))))
        return sorted(out, key=lambda t: -t[1])[:top_k]

    e = dict(WIZARD_LIBRARY_DB=str(db), HOME=str(Path(db).parent), VOIDLOOM_ENV_DIR=str(Path(db).parent / "envdir"),
             WIZARD_ENV_FILE=str(Path(db).parent / "envdir" / "absent"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
             VOIDLOOM_TIER0_ONLY="0", CUDA_VISIBLE_DEVICES="")
    e.update(env or {})
    (Path(db).parent / "envdir").mkdir(exist_ok=True)
    with mock.patch.dict(os.environ, e), \
            mock.patch.object(cm, "_encode", lambda t, p="search_document", **k: fake_vec(t)), \
            mock.patch.object(mw, "default_encoder", lambda t, conn=None: fake_vec(t)), \
            mock.patch.object(hr, "reranker_score", rerank), \
            mock.patch.object(_facade, "_TIER0_ONLY_CACHE", None), \
            mock.patch.object(cm, "DB_PATH", Path(db)):
        yield cm


def full_cli(db, *argv, env=None):
    """voidloom.cli.main in THIS process over the full engine: (rc, envelope or None, stderr)."""
    from voidloom import cli
    out, err = io.StringIO(), io.StringIO()
    with engine(db, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(["--db", str(db), *argv, "--json"])
        except SystemExit as e:
            rc = e.code
    return rc, last_json(out.getvalue()), err.getvalue()


def brain_text_files(d):
    """Every byte of every file under a brain directory (the database, its sidecars, logs, ledgers)."""
    return {str(p.relative_to(d)): p.read_bytes() for p in Path(d).rglob("*") if p.is_file()}


def data(env):
    return (env or {}).get("data") or {}


def unknown_flag(err):
    return "unrecognized arguments" in (err or "") or "invalid choice" in (err or "")


def plant_odd_mark(c, mid, value):
    """A present-but-odd mark (a restore, an older writer, a hand edit): planted past any guard trigger, which is put
    back afterwards."""
    trg = [(n, q) for n, q in c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='memories' "
                                        "AND sql LIKE '%untrusted_source%'")]
    for n, _q in trg:
        c.execute("DROP TRIGGER %s" % n)
    c.execute("UPDATE memories SET untrusted_source=? WHERE id=?", (value, mid))
    for _n, q in trg:
        c.execute(q)
    c.commit()


def premark_brain(tmp: Path, name="claude_memory.db", drop_mark=False):
    """A brain of the release BEFORE this one: the shipped migrator over the migrations up to 045. `drop_mark` removes
    the 029 column too (a brain that never met the Warden)."""
    mdir = tmp / "mig045"
    mdir.mkdir(parents=True)
    for f in (ROOT / "migrations").iterdir():
        m = re.match(r"migration_(\d+)_", f.name)
        if f.is_file() and not (m and int(m.group(1)) > 45):
            shutil.copy2(str(f), str(mdir / f.name))
    db = tmp / name
    shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(db))
    p = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "schema_migrator.py"), "apply", "--db", str(db),
                        "--migrations", str(mdir)], capture_output=True, text=True, env=child_env(db), timeout=300)
    if p.returncode != 0:
        raise AssertionError("premark fixture: " + p.stdout[-300:] + p.stderr[-300:])
    c = connect(db)
    c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
    if drop_mark:
        c.execute("DROP TRIGGER IF EXISTS trg_untrusted_source_immutable")
        c.execute("DROP INDEX IF EXISTS idx_memories_untrusted_source")
        c.execute("ALTER TABLE memories DROP COLUMN untrusted_source")
    c.commit()
    c.close()
    return db


def brain_over(db):
    b = Brain.__new__(Brain)
    b.path, b.c = str(db), connect(db)
    return b


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="wall-")
        self.tmp = Path(self._td.name)
        # any resolver that ignores the path it was given lands here, never on a real brain
        self._env = mock.patch.dict(os.environ, {
            "WIZARD_LIBRARY_DB": str(self.tmp / "unowned" / "claude_memory.db"), "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "", "VOIDLOOM_ENV_DIR": str(self.tmp / "envdir"),
            "HOME": str(self.tmp)})
        self._env.start()
        (self.tmp / "envdir").mkdir()

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    def needs(self, cond, what):
        """The interface is absent: a RED that names it, never an import error or a TypeError."""
        if not cond:
            self.fail("INTERFACE ABSENT (contract not built): " + what)


def _lite(db, *a):
    rc, env, _out, err = lite_cli(db, *a)
    return rc, env, err


class BothEngines(Case):
    """Run one scenario through BOTH engines and report EVERY engine's verdict: without the subtests plugin a subTest
    stops at its first failure, which would let a red full engine hide a green lite one."""

    def both(self, fn, make=None, sub=""):
        bad = []
        for name, cli in (("full", full_cli), ("lite", _lite)):
            d = self.tmp / (sub + name)
            b = make(d) if make else Brain(d)
            try:
                fn(b, cli)
            except AssertionError as e:
                bad.append("[%s engine] %s" % (name, str(e).strip()))
            finally:
                b.c.close()
        if bad:
            self.fail("\n".join(bad))

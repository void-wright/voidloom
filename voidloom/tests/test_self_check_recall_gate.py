"""install/self_check.py says what the engine's recall would say.

THE CLAIM UNDER TEST
  * every state the gate in claude_memory.cmd_recall refuses fails one REQUIRED check, naming the code, and nothing in the
    report then says recall works: on an interpreter with sqlite_vec and on one without it;
  * the check's codes are read against that gate's source, so the two cannot drift;
  * a brain the gate admits passes, on both interpreters;
  * a reader that could not run fails: it has said nothing about the brain;
  * each printed repair runs, and the brain answers afterwards;
  * a locked brain with no model is alive by keyword and says a store refuses; any other passing run keeps its verdict.

Real schema and vec0 tables, the shipped script in a child process.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voidloom.tests.test_doctor_brain_verdict import FOREIGN, JINA, JINA_DIM, NOMIC_DIM, ROOT, _Brain, el

SC = ROOT / "install" / "self_check.py"
GATE = "recall gate"
ACTIVE = "Semantic (Tier 1) recall is ACTIVE"
ALIVE = "YOUR BRAIN IS ALIVE. Store + recall work end-to-end."

sys.path.insert(0, str(ROOT / "install"))
import self_check as sc  # noqa: E402


class _Room(_Brain):
    """A brain with one live memory the own-content recall check can find, and an empty model cache."""

    def setUp(self):
        super().setUp()
        self.block = self.d / "block"
        self.block.mkdir()
        (self.block / "sqlite_vec.py").write_text("raise ImportError('this interpreter has no sqlite_vec')\n")
        self.hub = self.d / "hub"
        self.hub.mkdir()
        self.lockfile = self.d / "embedder.lock.json"

    def note(self, **kw):
        mid = self.memory("the keeper of the lantern room", **kw)
        self.conn.execute("UPDATE memories SET summary = 'lantern keeper' WHERE id = ?", (mid,))
        self.conn.commit()
        return mid

    def env(self, bare):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_", "WIZARD_", "VOIDLOOM_"))}
        env.update(HOME=str(self.d), HF_HUB_CACHE=str(self.hub), HF_HUB_OFFLINE="1", WIZARD_ACCELERATOR="cpu",
                   WIZARD_LIBRARY_DB=str(self.db), PYTHONDONTWRITEBYTECODE="1")
        if bare:
            env["PYTHONPATH"] = str(self.block)
        return env

    def run_check(self, *flags, bare=False, script=SC):
        self.conn.commit()
        return subprocess.run([sys.executable, str(script), "--seed-optional", *flags], capture_output=True, text=True,
                              env=self.env(bare), timeout=300, cwd=str(self.d))

    def report(self, bare=False, script=SC):
        p = self.run_check("--json", bare=bare, script=script)
        data = json.loads(p.stdout)
        gate = [c for c in data["checks"] if c["name"].startswith(GATE)]
        self.assertEqual(len(gate), 1, data["checks"])
        self.assertTrue(gate[0]["required"])
        return p.returncode, data, gate[0]

    def rewrite_lock(self, **fields):
        lock = json.loads(self.lockfile.read_text(encoding="utf-8"))
        lock.update(fields)
        self.lockfile.write_text(json.dumps(lock), encoding="utf-8")


def lost_lock(t):
    t.note(whash=t.h)
    t.lockfile.unlink()


def foreign_stamps(t):
    t.note(whash=FOREIGN)


def vectors_only_on_archived_rows(t):
    mid = t.note(whash=t.h)
    t.conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (mid,))
    t.memory("an active memory with no vector", vector=False)
    t.conn.execute("UPDATE memories SET summary = 'harbour tide' WHERE summary = 's'")


def stamp_at_another_width(t):
    t.stamp(t.note(), t.h, dim=NOMIC_DIM + 1)


def stamp_on_another_table(t):
    t.stamp(t.note(), t.h, table=JINA)


def lock_not_json(t):
    t.note(whash=t.h)
    t.lockfile.write_text("{not json", encoding="utf-8")


def lock_missing_a_key(t):
    t.note(whash=t.h)
    lock = json.loads(t.lockfile.read_text(encoding="utf-8"))
    lock.pop("revision")
    t.lockfile.write_text(json.dumps(lock), encoding="utf-8")


def lock_names_a_width_nothing_builds(t):
    t.note(whash=t.h)
    t.rewrite_lock(dim=5)


def lock_not_shipped(t):
    t.note(whash=FOREIGN)
    t.rewrite_lock(weights_hash=FOREIGN)


REFUSED = [
    ("LOCK_MISSING", lost_lock),
    ("NO_ROWS_FOR_LOCK", foreign_stamps),
    ("NO_ROWS_FOR_LOCK", vectors_only_on_archived_rows),
    ("STAMP_INCONSISTENT", stamp_at_another_width),
    ("STAMP_INCONSISTENT", stamp_on_another_table),
    ("LOCK_MALFORMED", lock_not_json),
    ("LOCK_MALFORMED", lock_missing_a_key),
    ("LOCK_MALFORMED", lock_names_a_width_nothing_builds),
    ("LOCK_NOT_SHIPPED", lock_not_shipped),
]


class EveryRefusalFails(_Room):
    def assert_refused(self, code, make, bare):
        make(self)
        with self.assertRaises(Exception) as caught:
            import memory_write
            memory_write.search_set(self.conn)
        self.assertEqual(getattr(caught.exception, "code", None), code, "the state under test is not this refusal")
        rc, data, gate = self.report(bare=bare)
        self.assertEqual(rc, 1, data)
        self.assertFalse(gate["ok"], gate)
        self.assertTrue(gate["detail"].startswith(code + ":"), gate["detail"])
        self.assertTrue(gate["fix"], gate)
        self.assertNotIn("alive", data["verdict"])
        said = json.dumps(data)         # every note, detail and fix the report prints
        self.assertNotIn(ACTIVE, said)
        self.assertNotIn("answers by keyword", said)
        self.assertNotIn("Running on Tier 0", said)
        self.assertNotIn("Keyword recall is unaffected", said)
        self.assertIn("Recall is REFUSED on this brain (%s)" % code, said)
        self.assertNotIn("or re-run install.sh", said)
        if code in ("LOCK_MISSING", "LOCK_MALFORMED", "LOCK_NOT_SHIPPED"):
            self.assertNotIn("model_fetch", json.dumps([c for c in data["checks"] if not c["required"]]),
                             "the fetch exits non-zero against this lock")

    def test_saboteur_the_printed_report_says_nothing_that_is_not_so(self):
        foreign_stamps(self)
        human = self.run_check().stdout
        self.assertNotIn("YOUR BRAIN IS ALIVE", human)
        self.assertNotIn(ACTIVE, human)
        self.assertIn("FIX: ", human)
        self.assertIn("required check(s) FAILED", human)


def _parametrise():
    for code, make in REFUSED:
        for bare in (False, True):
            def test(self, code=code, make=make, bare=bare):
                self.assert_refused(code, make, bare)
            name = "test_saboteur_%s_%s_%s" % (code.lower(), make.__name__, "bare" if bare else "with_sqlite_vec")
            setattr(EveryRefusalFails, name, test)


_parametrise()


def gate_codes() -> set:
    """The codes cmd_recall's gate compares `_code` against, read from its source."""
    tree = ast.parse((ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "cmd_recall")
    codes = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "_code":
            for side in node.comparators:
                codes.update(c.value for c in ast.walk(side) if isinstance(c, ast.Constant) and isinstance(c.value, str))
    return codes


class TheCheckIsBoundToTheGate(unittest.TestCase):
    def test_saboteur_a_code_the_gate_names_is_a_code_the_check_covers(self):
        self.assertEqual(gate_codes(), set(sc.RECALL_REFUSALS))

    def test_control_every_code_has_a_state_above(self):
        self.assertEqual({code for code, _ in REFUSED}, set(sc.RECALL_REFUSALS))


class WhatTheGateAdmitsPasses(_Room):
    def assert_admitted(self):
        for bare in (False, True):
            rc, data, gate = self.report(bare=bare)
            self.assertTrue(gate["ok"], gate)
            self.assertEqual(rc, 0, [c for c in data["checks"] if c["required"] and not c["ok"]])

    def test_control_a_healthy_locked_brain(self):
        self.note(whash=self.h)
        self.assert_admitted()

    def test_control_a_locked_brain_with_no_vectors_yet(self):
        self.note(vector=False)
        self.assert_admitted()

    def test_control_a_locked_brain_of_legacy_rows(self):
        self.note()
        self.assert_admitted()

    def test_control_a_locked_brain_whose_vector_table_was_never_built(self):
        self.note(vector=False)
        self.conn.execute("DROP TABLE memory_embeddings")
        self.conn.commit()
        rc, data, gate = self.report(bare=True)
        self.assertTrue(gate["ok"], gate)


class TheGpuTier(_Room):
    """Another table and width: nothing in the check may assume the cpu tier's."""
    LOCK = "gpu"

    def setUp(self):
        super().setUp()
        self.TABLE, self.DIM = self.lock["vec_table"], int(self.lock["dim"])
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.TABLE} USING vec0(memory_id INTEGER PRIMARY KEY, "
                          f"embedding FLOAT[{self.DIM}])")

    def test_control_a_healthy_gpu_brain(self):
        self.note(whash=self.h)
        for bare in (False, True):
            self.assertTrue(self.report(bare=bare)[2]["ok"])

    def test_saboteur_a_gpu_brain_no_row_answers_for(self):
        foreign_stamps(self)
        for bare in (False, True):
            gate = self.report(bare=bare)[2]
            self.assertTrue(gate["detail"].startswith("NO_ROWS_FOR_LOCK:"), gate)


class NoLock(_Room):
    LOCK = None

    def assert_admitted(self):
        for bare in (False, True):
            rc, data, gate = self.report(bare=bare)
            self.assertTrue(gate["ok"], gate)
            self.assertIn("no lock", gate["detail"])

    def test_control_a_tier_0_brain_with_no_vector_table_and_no_tier_declared(self):
        self.note(vector=False)
        self.conn.execute("DROP TABLE memory_embeddings")
        self.conn.execute("DELETE FROM metadata WHERE key LIKE 'active_embedding%'")
        self.conn.commit()
        self.assert_admitted()

    def test_control_a_brain_from_before_the_lock_with_unstamped_vectors(self):
        self.note()
        self.assert_admitted()


class ALocklessJinaBrain(_Room):
    LOCK = None
    TABLE, DIM = JINA, JINA_DIM

    def test_control_it_passes(self):
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {JINA} USING vec0(memory_id INTEGER PRIMARY KEY, "
                          f"embedding FLOAT[{JINA_DIM}])")
        self.note(whash="unresolved")
        for bare in (False, True):
            rc, data, gate = self.report(bare=bare)
            self.assertTrue(gate["ok"], gate)


_NO_READER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
sys.modules["memory_write"] = None
import self_check as sc
c = sc.Checks()
sc.check_recall_gate(c, sc.Path(sys.argv[2]))
print(json.dumps(c.results))
"""


class AReaderThatCouldNotRunFails(_Room):
    def test_saboteur_the_reader_will_not_import(self):
        self.note(whash=self.h)
        self.conn.commit()
        p = subprocess.run([sys.executable, "-c", _NO_READER, str(ROOT / "install"), str(self.db)], capture_output=True,
                           text=True, env=self.env(False), timeout=120)
        gate = json.loads(p.stdout)[0]
        self.assertFalse(gate["ok"], gate)
        self.assertIn("could not run", gate["detail"])

    def test_saboteur_a_sidecar_it_cannot_read_is_not_a_pass(self):
        self.note(whash=self.h)
        self.conn.execute("DROP INDEX idx_embedding_provenance_lock")
        self.conn.execute("ALTER TABLE embedding_provenance DROP COLUMN weights_hash")
        rc, data, gate = self.report(bare=True)
        self.assertEqual(rc, 1)
        self.assertFalse(gate["ok"], gate)
        self.assertIn("could not run", gate["detail"])


class ARepairNamesAnInterpreterThatCanRunIt(_Room):
    """A copy of the script in a tree of its own, as a laid install has: nothing here is a venv."""

    def laid(self):
        tree = self.d / "tree"
        (tree / "install").mkdir(parents=True)
        shutil.copy(str(SC), str(tree / "install" / "self_check.py"))
        for name in ("organs", "migrations", "seed"):
            (tree / name).symlink_to(ROOT / name)
        return tree

    def test_saboteur_a_python_without_sqlite_vec_says_so_when_nothing_records_a_better_one(self):
        foreign_stamps(self)
        tree = self.laid()
        _, _, gate = self.report(bare=True, script=tree / "install" / "self_check.py")
        self.assertIn("this one has none", gate["fix"])

    def test_control_the_install_record_names_the_interpreter(self):
        foreign_stamps(self)
        tree = self.laid()
        (tree / "INSTALL_RECORD").write_text("interpreter=/bin/sh\n", encoding="utf-8")
        _, _, gate = self.report(bare=True, script=tree / "install" / "self_check.py")
        self.assertIn("/bin/sh", gate["fix"])
        self.assertNotIn(sys.executable, gate["fix"])


class EveryPrintedFixWorks(_Room):
    """The repair exactly as printed, in a tree of its own: `init` acts on the brain beside its organs."""

    def setUp(self):
        super().setUp()
        self.conn.close()
        self.tree = self.d / "tree"
        self.tree.mkdir()
        for name in ("organs", "migrations", "install", "seed"):
            shutil.copytree(str(ROOT / name), str(self.tree / name), ignore=shutil.ignore_patterns("__pycache__"))
        for f in list(self.d.glob("claude_memory.db*")) + list(self.d.glob("embedder.lock*")):
            shutil.move(str(f), str(self.tree / f.name))
        self.db = self.tree / "claude_memory.db"
        self.lockfile = self.tree / "embedder.lock.json"
        self.conn = self.connect()

    def run_fix(self, make, *, model=False):
        make(self)
        env = self.env(False)
        if model:
            cpu = el.catalogue_tiers()["cpu"]
            if el.snapshot_dir_for(cpu["model_id"], cpu["revision"]) is None:
                self.skipTest("the cpu model is not on this host's disk")
            env.pop("HF_HUB_CACHE")
            for k in ("HF_HOME", "HF_HUB_CACHE"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
        script = self.tree / "install" / "self_check.py"
        self.conn.commit()
        self.conn.close()
        p = subprocess.run([sys.executable, str(script), "--seed-optional", "--json"], capture_output=True, text=True,
                           env=env, timeout=300, cwd=str(self.d))
        gate = [c for c in json.loads(p.stdout)["checks"] if c["name"].startswith(GATE)][0]
        self.assertFalse(gate["ok"], gate)
        fixed = subprocess.run(gate["fix"], shell=True, capture_output=True, text=True, env=env, timeout=600,
                               cwd=str(self.d))
        self.assertEqual(fixed.returncode, 0, (gate["fix"], fixed.stdout[-600:], fixed.stderr[-600:]))
        p = subprocess.run([sys.executable, str(script), "--seed-optional", "--json"], capture_output=True, text=True,
                           env=env, timeout=300, cwd=str(self.d))
        gate = [c for c in json.loads(p.stdout)["checks"] if c["name"].startswith(GATE)][0]
        self.conn = self.connect()
        self.assertTrue(gate["ok"], gate)

    def test_a_lost_lock(self):
        self.run_fix(lost_lock)

    def test_a_malformed_lock(self):
        self.run_fix(lock_not_json)

    def test_vectors_no_row_answers_for(self):
        self.run_fix(foreign_stamps, model=True)

    def test_a_stamp_at_another_width(self):
        self.run_fix(stamp_at_another_width, model=True)

    def test_a_stamp_on_another_table(self):
        self.run_fix(stamp_on_another_table, model=True)

    def test_a_lock_this_package_does_not_ship(self):
        self.run_fix(lock_not_shipped, model=True)


class TheModelIsAbsent(_Room):
    def assert_says_a_store_refuses(self, bare):
        p = self.run_check("--json", bare=bare)
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 0, data)
        self.assertTrue(data["verdict"].startswith("brain alive by keyword: "), data["verdict"])
        self.assertIn("a store refuses until the model is fetched", data["verdict"])
        self.assertIn("model_fetch.py", data["verdict"])
        human = self.run_check(bare=bare).stdout
        self.assertNotIn(ALIVE, human)
        self.assertIn("VERDICT: YOUR BRAIN IS ALIVE BY KEYWORD: ", human)
        self.assertIn("model_fetch.py", human)
        self.assertIn("a store refuses", self.run_check("--quiet", bare=bare).stdout)

    def test_saboteur_a_locked_brain_with_vectors(self):
        self.note(whash=self.h)
        self.assert_says_a_store_refuses(False)
        self.assert_says_a_store_refuses(True)

    def test_saboteur_where_an_offline_install_ends_no_vectors_at_all(self):
        self.note(vector=False)
        self.assert_says_a_store_refuses(False)
        self.assert_says_a_store_refuses(True)

    def test_saboteur_only_the_remote_code_is_missing(self):
        self.note(whash=self.h)
        self.place(self.lock)
        self.assert_says_a_store_refuses(True)

    def place(self, pin):
        snap = self.hub / ("models--" + pin["model_id"].replace("/", "--")) / "snapshots" / pin["revision"]
        for rel in pin.get("files") or ():
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            (snap / rel).write_bytes(b"x")

    def link(self, pin):
        """The host's own copy of `pin`, linked into this hub: bytes the lock describes, not just files it names."""
        real = el.snapshot_dir_for(pin["model_id"], pin["revision"])
        if real is None or not el.snapshot_complete(real, pin.get("files")):
            self.skipTest("the cpu model is not on this host's disk")
        snap = self.hub / ("models--" + pin["model_id"].replace("/", "--")) / "snapshots" / pin["revision"]
        for rel in pin.get("files") or ():
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.path.realpath(real / rel), snap / rel)

    def test_control_a_model_on_disk_keeps_the_verdict(self):
        self.note(whash=self.h)
        self.link(self.lock)
        if self.lock.get("code"):
            self.link(self.lock["code"])
        p = self.run_check("--json", bare=True)
        self.assertEqual(json.loads(p.stdout)["verdict"], "brain alive")
        self.assertIn("VERDICT: " + ALIVE, self.run_check(bare=True).stdout)
        self.assertEqual(self.run_check("--quiet", bare=True).stdout.strip(), "brain alive")

    def test_control_a_refusal_is_not_read_as_a_missing_model(self):
        foreign_stamps(self)
        p = self.run_check("--json")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["verdict"], "brain check failed")


class ABrainWithNoLockNeedsNoModel(_Room):
    LOCK = None

    def test_control_it_keeps_the_verdict(self):
        self.note()
        self.assertIn("VERDICT: " + ALIVE, self.run_check(bare=True).stdout)


if __name__ == "__main__":
    unittest.main()

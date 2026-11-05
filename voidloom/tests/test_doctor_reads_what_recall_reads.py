"""`doctor` and `status` read a brain the way recall and store use it.

THE CLAIM UNDER TEST
  * a brain whose recall or store degrades or fails is not healthy: the locked model absent, the keyword index dropped, a
    metadata table name nothing builds, a lockless brain's own model absent;
  * each reason names the fault, and a printed fix is one that runs;
  * a keyword-only brain and a brain with no vector need no model, and stay healthy.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import os
import tempfile
import types
import unittest
from unittest import mock

from voidloom.tests.test_doctor_brain_verdict import JINA, JINA_DIM, _Brain, cli


def _peft():
    fake = types.ModuleType("peft")
    fake.__spec__ = importlib.machinery.ModuleSpec("peft", None)
    return mock.patch.dict("sys.modules", {"peft": fake})


class _Cold(_Brain):
    """An empty model cache, read through the one resolver."""

    def setUp(self):
        super().setUp()
        self.hub = tempfile.TemporaryDirectory()
        self.addCleanup(self.hub.cleanup)
        p = mock.patch.dict(os.environ, {"HF_HUB_CACHE": self.hub.name})
        p.start()
        self.addCleanup(p.stop)

    def status(self):
        env = cli.verb_status(argparse.Namespace(db=str(self.db), json=True))
        self.assertEqual(env["status"], "ok", env)
        return env["data"]


class TheLockedModelIsAbsent(_Cold):
    def test_saboteur_recall_is_keyword_only_and_doctor_says_so(self):
        self.memory(whash=self.h)
        d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("SNAPSHOT_ABSENT", d["reason"])
        self.assertIn("a store refuses", d["reason"])
        self.assertIn("model_fetch.py --db", d["reason"])
        self.assertEqual([f.split(":")[0] for f in self.status()["faults"]], ["SNAPSHOT_ABSENT"], "status agrees")


class TheKeywordIndexIsGone(_Brain):
    def test_saboteur_doctor_says_schema_damaged(self):
        self.memory(whash=self.h)
        self.conn.execute("DROP TABLE memories_fts")
        self.conn.commit()
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("SCHEMA_DAMAGED", d["reason"])
        env = cli.verb_status(argparse.Namespace(db=str(self.db), json=True))
        self.assertIn("SCHEMA_DAMAGED", " ".join(env["data"]["faults"]), "status agrees")

    def test_control_an_empty_brain_with_no_index_is_nothing_to_report(self):
        self.conn.execute("DROP TABLE memories_fts")
        self.conn.commit()
        self.assertNotIn("SCHEMA_DAMAGED", str(self.doctor()["reason"]))


class MetadataNamesATableNothingBuilds(_Brain):
    LOCK = None

    def test_saboteur_a_lockless_brain_pointed_at_nothing_is_not_healthy(self):
        self.memory(whash="unresolved")
        self.declare("[removed]", self.DIM)
        self.conn.commit()
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False, d["reason"])


class ALocklessBrainsOwnModelIsAbsent(_Cold):
    LOCK = None
    TABLE, DIM = JINA, JINA_DIM

    def setUp(self):
        super().setUp()
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {JINA} USING vec0(memory_id INTEGER PRIMARY KEY, "
                          f"embedding FLOAT[{JINA_DIM}])")
        self.conn.commit()

    def test_saboteur_a_jina_brain_with_vectors_and_no_model_says_so_and_names_a_fix(self):
        self.memory(whash="unresolved")
        with _peft():
            d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], False, d["reason"])
        self.assertIn("the jina model", d["reason"])
        self.assertIn("keyword-only", d["reason"])

    def test_control_a_keyword_brain_needs_no_model(self):
        self.memory(whash=None, vector=False)
        with _peft():
            d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], True, d["reason"])


if __name__ == "__main__":
    unittest.main()

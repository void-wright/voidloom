"""`doctor` and `status` read a brain the way recall and store use it.

THE CLAIM UNDER TEST
  * a brain whose recall or store degrades or fails is not healthy: the locked model absent, the keyword index dropped, a
    metadata table name nothing builds, a lockless brain's own model absent;
  * each reason names the fault, and a printed fix is one that runs;
  * a keyword-only brain and a lockless brain with no vector need no model, and stay healthy. A locked brain needs its
    model whatever it holds, because its store refuses without it;
  * a model is the snapshot the loader reads, whole, at its pin: a folder of that name is what a failed download leaves.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import os
import tempfile
import types
import unittest
from pathlib import Path
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

    def folder(self, *files):
        """What a download leaves in the loader's cache, at the engine's pin."""
        import embedding_upgrade as eu
        snap = Path(self.hub.name) / ("models--" + eu.JINA_MODEL_NAME.replace("/", "--")) / "snapshots" / eu.JINA_REVISION
        snap.mkdir(parents=True)
        for rel in files:
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            (snap / rel).write_bytes(b"x")

    def test_saboteur_a_folder_an_interrupted_download_left_is_not_a_model(self):
        self.memory(whash="unresolved")
        self.folder("config.json")
        with _peft():
            d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], False, d["reason"])
        self.assertIn("SNAPSHOT_ABSENT", d["reason"])

    def test_control_a_whole_snapshot_at_the_pin_is_a_model(self):
        import embedding_upgrade as eu
        self.memory(whash="unresolved")
        self.folder(*eu.JINA_SNAPSHOT_FILES)
        with _peft():
            d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], True, d["reason"])


class ALocklessNomicBrainInAColdCache(_Cold):
    LOCK = None

    def test_saboteur_an_empty_model_folder_reads_as_absent(self):
        self.memory(whash="unresolved")
        (Path(self.hub.name) / "models--nomic-ai--nomic-embed-text-v1.5").mkdir()
        d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], False, d["reason"])
        self.assertIn("SNAPSHOT_ABSENT", d["reason"])


class ALockedBrainNeedsItsModelWhateverItHolds(_Cold):
    def test_saboteur_a_first_store_would_refuse_so_doctor_is_not_healthy(self):
        self.memory(whash=None, vector=False)
        d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], False, d["reason"])
        self.assertEqual([f.split(":")[0] for f in self.status()["faults"] if f.startswith("SNAPSHOT_ABSENT")],
                         ["SNAPSHOT_ABSENT"], "status names the model too")


if __name__ == "__main__":
    unittest.main()

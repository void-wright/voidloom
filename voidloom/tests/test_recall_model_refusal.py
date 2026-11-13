"""Past the gate, a recall encodes the question under the brain's model, and refuses there too. What says the brain works
must say so too.

THE CLAIM UNDER TEST
  * model files that fail the lock's hash, and a WIZARD_EMBED_MODEL that names another model than the brain's (with a
    lock, and without one where the brain holds vectors), are refused by the engine's recall; the self-check then fails
    one REQUIRED check naming the code, on an interpreter with sqlite_vec and on one without it, and nothing it prints
    says recall works; `voidloom doctor` calls the brain unhealthy and prints the same repair;
  * a model that is absent is not a refusal (recall answers by keyword), and neither is any state of a brain the front
    door answers with the keyword engine (tier0_only);
  * the printed repairs run as printed, and the brain then passes;
  * the fetch every one of these prints replaces files that fail a shipped pin's hash, at the pin, and only then:
    never without a fetch allowed, never for a pin the catalogue does not ship, and a second failure refuses.

Real schema, real lock and catalogue, the shipped scripts in child processes. Model bytes are a few junk bytes: what is
under test is that bytes the lock does not describe are refused, and by whom.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voidloom.tests.test_doctor_brain_verdict import FOREIGN, JINA, JINA_DIM, ROOT, el
from voidloom.tests.test_self_check_recall_gate import ACTIVE, SC, _Room

MODEL = "recall model"
REFUSED = "Recall is REFUSED on this brain (WEIGHTS_MISMATCH)"


def place(hub: Path, pin: dict, data: bytes = b"x") -> None:
    """Every file `pin` names, under `hub`, at its revision."""
    snap = hub / ("models--" + pin["model_id"].replace("/", "--")) / "snapshots" / pin["revision"]
    for rel in pin.get("files") or ():
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        (snap / rel).write_bytes(data)


def damaged_model(t):
    """The model the lock names is on disk, whole, and its bytes are not the lock's."""
    t.note(whash=t.h)
    place(t.hub, t.lock)
    if t.lock.get("code"):
        place(t.hub, t.lock["code"])


class _Model(_Room):
    def setUp(self):
        super().setUp()
        self.extra = {}

    def env(self, bare):
        env = super().env(bare)
        env.update(self.extra)
        return env

    def model_check(self, data):
        got = [c for c in data["checks"] if c["name"].startswith(MODEL)]
        self.assertEqual(len(got), 1, [c["name"] for c in data["checks"]])
        self.assertTrue(got[0]["required"])
        return got[0]

    def recall_rc(self):
        """The engine's own recall on this brain, through the front door, hub closed."""
        self.conn.commit()
        env = self.env(False)
        env["PYTHONPATH"] = str(ROOT)
        p = subprocess.run([sys.executable, "-m", "voidloom", "recall", "--query", "lantern keeper", "--top-k", "1",
                            "--json"], capture_output=True, text=True, env=env, timeout=300, cwd=str(self.d))
        try:
            body = json.loads(p.stdout)
        except ValueError:
            body = {"unreadable": p.stdout[-400:] + p.stderr[-400:]}
        return p.returncode, body

    @contextlib.contextmanager
    def environ(self):
        held = {k: os.environ.get(k) for k in ("HF_HUB_CACHE", "HF_HUB_OFFLINE", "WIZARD_EMBED_MODEL")}
        os.environ.update(HF_HUB_CACHE=str(self.hub), HF_HUB_OFFLINE="1")
        os.environ.pop("WIZARD_EMBED_MODEL", None)
        os.environ.update(self.extra)
        try:
            yield
        finally:
            for k, v in held.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def doctor(self):
        from voidloom import cli
        self.conn.commit()
        plats = {"claude": {"state": "NOT_INSTALLED", "ours": [], "config_warning": None}}
        with self.environ(), mock.patch.object(cli, "doctor_hook_join", return_value={"platforms": plats}):
            env = cli.verb_doctor(argparse.Namespace(db=str(self.db), cwd=None, platform=None, probe_platform=False))
        self.assertEqual(env["status"], "ok" if env["data"]["brain_healthy"] else "incomplete", env)
        return env["data"]

    def assert_refused(self, *, command):
        rc, body = self.recall_rc()
        self.assertEqual((rc, body.get("code")), (1, "WEIGHTS_MISMATCH"), body)
        fixes = set()
        for bare in (False, True):
            p = self.run_check("--json", bare=bare)
            data = json.loads(p.stdout)
            self.assertEqual(p.returncode, 1, data)
            check = self.model_check(data)
            self.assertFalse(check["ok"], check)
            self.assertTrue(check["detail"].startswith("WEIGHTS_MISMATCH:"), check["detail"])
            self.assertIn(command, check["fix"])
            fixes.add(check["fix"].rsplit(": ", 1)[1].split("  (")[0])
            said = json.dumps(data)
            self.assertNotIn("alive", data["verdict"])
            self.assertNotIn(ACTIVE, said)
            self.assertNotIn("answers by keyword", said)
            self.assertNotIn("Running on Tier 0", said)
            self.assertIn(REFUSED, said)
        human = self.run_check().stdout
        self.assertNotIn("YOUR BRAIN IS ALIVE", human)
        self.assertIn("required check(s) FAILED", human)
        doc = self.doctor()
        self.assertFalse(doc["brain_healthy"], doc["reason"])
        self.assertIn("WEIGHTS_MISMATCH", doc["reason"])
        self.assertIn(doc["reason"].rsplit(": ", 1)[1], fixes, "doctor prints the self-check's repair")

    def assert_passes(self):
        rc, body = self.recall_rc()
        self.assertEqual(rc, 0, body)
        for bare in (False, True):
            p = self.run_check("--json", bare=bare)
            data = json.loads(p.stdout)
            self.assertEqual(p.returncode, 0, [c for c in data["checks"] if c["required"] and not c["ok"]])
            self.assertTrue(self.model_check(data)["ok"])
        doc = self.doctor()
        # a locked brain whose model is absent reads SNAPSHOT_ABSENT in doctor: recall answers it by keyword
        self.assertTrue(doc["brain_healthy"] or doc["reason"].startswith("SNAPSHOT_ABSENT"), doc["reason"])


class BytesTheLockDoesNotDescribe(_Model):
    def test_saboteur_the_model_on_disk_is_not_the_locks(self):
        damaged_model(self)
        self.assert_refused(command="model_fetch.py")

    def test_saboteur_only_the_remote_code_is_not_the_locks(self):
        self.note(whash=self.h)
        cpu = el.catalogue_tiers()["cpu"]
        if not self.lock.get("code") or el.snapshot_dir_for(cpu["model_id"], cpu["revision"]) is None:
            self.skipTest("the cpu model is not on this host's disk")
        real = el.snapshot_dir_for(cpu["model_id"], cpu["revision"])
        snap = self.hub / ("models--" + cpu["model_id"].replace("/", "--")) / "snapshots" / cpu["revision"]
        for rel in cpu["files"]:
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.path.realpath(real / rel), snap / rel)
        place(self.hub, self.lock["code"])
        self.assert_refused(command="model_fetch.py")

    def test_control_a_model_that_is_absent_is_not_a_refusal(self):
        self.note(whash=self.h)
        p = self.run_check("--json")
        data = json.loads(p.stdout)
        self.assertEqual(p.returncode, 0, data)
        self.assertTrue(self.model_check(data)["ok"])
        self.assertTrue(data["verdict"].startswith("brain alive by keyword: "), data["verdict"])

    def test_control_the_keyword_engine_reads_no_model(self):
        damaged_model(self)
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        self.assert_passes()


class ALockThePackageDoesNotShip(_Model):
    def test_control_the_fetch_is_not_offered_against_it(self):
        self.note(whash=FOREIGN)
        place(self.hub, self.lock)
        if self.lock.get("code"):
            place(self.hub, self.lock["code"])
        self.rewrite_lock(weights_hash=FOREIGN)
        sys.path.insert(0, str(ROOT / "organs"))
        import claude_memory
        with self.environ():
            found = claude_memory.query_encode_refusal(str(self.db))
        self.assertIsNotNone(found)
        self.assertIsNone(found["command"], "model_fetch fetches nothing against a lock the package does not ship")
        self.assertNotIn("model_fetch", found["fix"])
        data = json.loads(self.run_check("--json").stdout)
        gate = [c for c in data["checks"] if c["name"].startswith("recall gate")][0]
        self.assertTrue(gate["detail"].startswith("LOCK_NOT_SHIPPED:"), gate["detail"])
        self.assertIn("LOCK_NOT_SHIPPED", self.doctor()["reason"])


class AVariableNamingAnotherModel(_Model):
    def test_saboteur_with_a_lock(self):
        self.note(whash=self.h)
        self.extra = {"WIZARD_EMBED_MODEL": "jina"}
        self.assert_refused(command="unset WIZARD_EMBED_MODEL")

    def test_control_a_variable_that_agrees(self):
        self.note(whash=self.h)
        self.extra = {"WIZARD_EMBED_MODEL": "cpu"}
        self.assert_passes()


class AVariableNamingAnotherModelOnABrainWithNoLock(_Model):
    LOCK = None
    TABLE, DIM = JINA, JINA_DIM

    def setUp(self):
        super().setUp()
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {JINA} USING vec0("
                          f"memory_id INTEGER PRIMARY KEY, embedding FLOAT[{JINA_DIM}])")
        self.conn.commit()

    def test_saboteur_it_holds_vectors_under_another_model(self):
        self.note()
        self.extra = {"WIZARD_EMBED_MODEL": "nomic"}
        self.assert_refused(command="unset WIZARD_EMBED_MODEL")


class ThePrintedRepairRuns(_Model):
    def test_unsetting_the_variable(self):
        self.note(whash=self.h)
        self.extra = {"WIZARD_EMBED_MODEL": "jina"}
        data = json.loads(self.run_check("--json").stdout)
        fix = self.model_check(data)["fix"].rsplit(": ", 1)[1]
        script = f"{fix}\n{sys.executable} {SC} --seed-optional --json"
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=self.env(False), timeout=300,
                           cwd=str(self.d))
        self.assertEqual(p.returncode, 0, p.stdout[-800:])
        self.assertTrue(self.model_check(json.loads(p.stdout))["ok"])


class _Hub(unittest.TestCase):
    """A catalogue of one shipped tier, and a hub where a 'download' writes what the test says the hub serves."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.hub = self.d / "hub"
        good = self.d / "good"
        files = {"config.json": b'{"a": 1}', "model.safetensors": b"weights" * 100}
        for rel, data in files.items():
            (good / rel).parent.mkdir(parents=True, exist_ok=True)
            (good / rel).write_bytes(data)
        self.files = files
        self.pin = {"model_id": "test-org/test-embedder", "revision": "a" * 40, "files": sorted(files), "dim": 8,
                    "vec_table": "memory_embeddings", "license": "apache-2.0", "tier": "cpu"}
        self.pin["weights_hash"] = el.compute_entry_hash(self.pin, snapshot=good)
        cat = json.loads(el.catalogue_path().read_text(encoding="utf-8"))
        entry = {k: v for k, v in self.pin.items() if k != "tier"}
        cat["tiers"] = {"cpu": entry}
        cat["rerankers"] = {}
        self.catalogue = self.d / "embedders.lock.json"
        self.catalogue.write_text(json.dumps(cat), encoding="utf-8")
        self.serves = dict(files)
        self.calls = []
        self.held = {k: os.environ.get(k) for k in ("HF_HUB_CACHE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
        os.environ["HF_HUB_CACHE"] = str(self.hub)
        for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            os.environ.pop(k, None)
        self.snap = self.hub / "models--test-org--test-embedder" / "snapshots" / self.pin["revision"]
        for rel in files:
            (self.snap / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.snap / rel).write_bytes(files[rel])
        data = bytearray(self.files["model.safetensors"])
        data[5] ^= 1
        (self.snap / "model.safetensors").write_bytes(bytes(data))

    def tearDown(self):
        for k, v in self.held.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def download(self, repo_id, **kw):
        self.calls.append((repo_id, kw))
        for rel in kw.get("allow_patterns") or ():
            (self.snap / rel).write_bytes(self.serves[rel])
        return str(self.snap)

    def fetch(self, lock=None, **kw):
        import huggingface_hub
        with mock.patch.object(huggingface_hub, "snapshot_download", self.download):
            return el.fetch_models_needed(lock or self.pin, catalogue=self.catalogue, organs_dir=self.d, **kw)


class TheFetchReplacesWhatFailsThePin(_Hub):
    def test_saboteur_a_damaged_file_is_fetched_again_at_its_pin(self):
        try:
            out = self.fetch()
        except el.EmbedderLockError as e:
            self.fail(f"refused where the damaged file should have been fetched again: {e}")
        self.assertEqual((out["replaced"], out["verified"], out["fetched"]), (1, 1, 0), out)
        self.assertEqual(len(self.calls), 1, self.calls)
        repo, kw = self.calls[0]
        self.assertEqual(repo, self.pin["model_id"])
        self.assertEqual(kw["revision"], self.pin["revision"])
        self.assertEqual(kw["allow_patterns"], self.pin["files"], "only what the pin names")
        self.assertIs(kw["force_download"], True, "over what is on disk")
        self.assertEqual((self.snap / "model.safetensors").read_bytes(), self.files["model.safetensors"])

    def test_saboteur_a_second_failure_refuses(self):
        self.serves["model.safetensors"] = b"not the pinned bytes"
        with self.assertRaises(el.EmbedderLockError) as caught:
            self.fetch()
        self.assertEqual(caught.exception.code, "WEIGHTS_MISMATCH")
        self.assertIn("fetched again at its pin and still does not hash", str(caught.exception))

    def test_control_a_run_that_may_not_fetch_keeps_the_refusal(self):
        with self.assertRaises(el.EmbedderLockError) as caught:
            self.fetch(allow_fetch=False)
        self.assertEqual(caught.exception.code, "WEIGHTS_MISMATCH")
        os.environ["HF_HUB_OFFLINE"] = "1"
        with self.assertRaises(el.EmbedderLockError):
            self.fetch()
        self.assertEqual(self.calls, [], "nothing fetched")

    def test_control_a_pin_the_catalogue_does_not_ship_is_never_fetched(self):
        lock = dict(self.pin, weights_hash="f" * 64)
        with self.assertRaises(el.EmbedderLockError):
            self.fetch(lock)
        self.assertEqual(self.calls, [], "nothing fetched")

    def test_control_whole_bytes_are_not_fetched_again(self):
        (self.snap / "model.safetensors").write_bytes(self.files["model.safetensors"])
        out = self.fetch()
        self.assertEqual((out["replaced"], out["verified"]), (0, 1), out)
        self.assertEqual(self.calls, [])


class TheFetchSaysHowWhenItMayNot(_Model):
    def test_no_fetch_names_the_run_that_repairs(self):
        damaged_model(self)
        for pin in el.catalogue_rerankers().values():
            place(self.hub, pin)            # every model present, so the run reaches the hash
        self.conn.commit()
        p = subprocess.run([sys.executable, str(ROOT / "organs" / "model_fetch.py"), "--db", str(self.db), "--no-fetch",
                            "--json"], capture_output=True, text=True, env=self.env(False), timeout=300, cwd=str(self.d))
        body = json.loads(p.stdout)
        self.assertEqual((p.returncode, body.get("error")), (1, "WEIGHTS_MISMATCH"), body)
        self.assertIn("retry", body, "the run that repairs is named")
        self.assertIn("model_fetch.py", body["retry"][0])
        self.assertIn("with the network", body.get("fix", ""))


if __name__ == "__main__":
    unittest.main()

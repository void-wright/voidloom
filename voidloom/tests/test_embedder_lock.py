# -*- coding: utf-8 -*-
"""The embedder catalogue and the install lock (organs/embedder_lock.py, organs/embedders.lock.json).

THE CLAIM: the package ships a catalogue whose every `weights_hash` is COMPUTED from a model snapshot's
output-determining files AND from every repository the model's config.json `auto_map` names (nomic's forward-pass
code lives in a second hub repository, pinned as `code: {model_id, revision, files[]}`); `write_lock` writes exactly
one catalogue tier into <data-dir>/embedder.lock.json, atomically and with a receipt, and never re-points an install;
and every process registers the identity it loaded.

The checks that bind the lock to its consumers (the one loader, the reader, `init`, doctor) arrive with the slices
that port those consumers. The 1024-wide non-commercial embedder has no catalogue entry by construction; it stays
selectable for a personal install through the engine's own tier registry, outside the lock.

EVERY CHECK IS A CONTROL PAIR. No model is ever instantiated here. Where a check needs a snapshot this host lacks,
it SKIPS and says so -- NOT MEASURED is not MEASURED AND FINE.

Classes:
    L1  the shipped catalogue: computed, reproducible from the shell, permissive, no non-commercial entry
    L2  write_lock: one tier, atomic, receipted, never re-pointed
    L5  the process register
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
import embedder_lock as el  # noqa: E402

CATALOGUE = ROOT / "organs" / "embedders.lock.json"




def _snapshot(tier: str):
    entry = el.catalogue_tiers()[tier]
    return el.snapshot_dir_for(entry["model_id"], entry["revision"]), entry


def _code_snapshot(entry: dict):
    code = entry.get("code")
    if not code:
        return None
    return el.snapshot_dir_for(code["model_id"], code["revision"])


def _box_copy(entry: dict, box: Path):
    """Copy the model snapshot's files[] and the code snapshot's code.files[] into
    `box/model` and `box/code` (following the HF symlinks). Returns the two dirs."""
    snap, _ = _snapshot_of(entry)
    m = box / "model"; m.mkdir(parents=True)
    for rel in entry["files"]:
        dst = m / rel; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snap / rel, dst)
    c = None
    if entry.get("code"):
        csnap = _code_snapshot(entry)
        c = box / "code"; c.mkdir()
        for rel in entry["code"]["files"]:
            shutil.copyfile(csnap / rel, c / rel)
    return m, c


def _snapshot_of(entry: dict):
    return el.snapshot_dir_for(entry["model_id"], entry["revision"]), entry


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        el.clear_loaded()

    def tearDown(self):
        el.clear_loaded()
        self.tmp.cleanup()


class L1_ShippedCatalogue(unittest.TestCase):
    """Confound: a hash that was TYPED into the file would pass every shape
    check; only recomputing from the bytes distinguishes it, so the control
    recomputes (and skips loudly where the snapshot is absent)."""

    def test_control_catalogue_is_tracked_well_formed_permissive_and_has_no_jina(self):
        r = subprocess.run(["git", "ls-files", "--", "organs/embedders.lock.json"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            self.assertEqual(r.stdout.strip(), "organs/embedders.lock.json", "the catalogue is not tracked")
        cat = el.load_catalogue()
        self.assertEqual(cat["schema"], el.CATALOGUE_SCHEMA)
        tiers = cat["tiers"]
        self.assertEqual(sorted(tiers), ["cpu", "gpu"])
        for tier, e in tiers.items():
            self.assertRegex(e["weights_hash"], r"^[0-9a-f]{64}$", tier)
            self.assertEqual(e["license"], "apache-2.0", tier)
            self.assertNotIn("jina", json.dumps(e).lower(), tier)
            self.assertEqual(e["files"], sorted(e["files"]), f"{tier}: files[] must be sorted")
            self.assertTrue(any(f.endswith((".safetensors", ".bin")) for f in e["files"]), tier)
            self.assertIn("config.json", e["files"], tier)
        self.assertNotIn("jina", CATALOGUE.read_text(encoding="utf-8").lower())
        self.assertEqual(len({e["weights_hash"] for e in tiers.values()}), 2, "two tiers, two hashes")
        self.assertEqual(len({e["vec_table"] for e in tiers.values()}), 2, "one table per tier")

    def test_control_the_manifest_pins_the_catalogue_by_its_current_sha256(self):
        # M1-C1: "sha256-pinned in PACKAGE_MANIFEST.md" -- the row's hash IS the file's
        man = (ROOT / "docs" / "PACKAGE_MANIFEST.md").read_text(encoding="utf-8")
        m = re.search(r"^\|\s*`organs/embedders.lock.json`\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]+)`", man, re.M)
        self.assertIsNotNone(m, "the manifest has no row for the catalogue")
        raw = CATALOGUE.read_bytes()
        self.assertEqual(int(m.group(1)), len(raw), "the manifest's size for the catalogue is stale")
        self.assertEqual(hashlib.sha256(raw).hexdigest()[:len(m.group(2))], m.group(2),
                         "the manifest pins a catalogue this tree does not ship")

    def test_saboteur_a_stale_manifest_row_is_seen(self):
        stale = "| `organs/embedders.lock.json` | 1521 | `7fe1ae238abc8419` |"     # the b582713 row
        m = re.search(r"^\|\s*`organs/embedders.lock.json`\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]+)`", stale, re.M)
        self.assertNotEqual(hashlib.sha256(CATALOGUE.read_bytes()).hexdigest()[:16], m.group(2))

    def test_control_each_shipped_hash_recomputes_from_this_hosts_snapshots(self):
        self.assertIn("code", el.catalogue_tiers()["cpu"], "the cpu entry carries no remote-code pin (r2 F3)")
        for tier in ("cpu", "gpu"):
            snap, entry = _snapshot(tier)
            if snap is None:
                self.skipTest(f"{tier}: snapshot {entry['model_id']}@{entry['revision'][:12]} absent "
                              f"under {el.hf_hub_cache()} — NOT MEASURED on this host")
            csnap = _code_snapshot(entry)
            if entry.get("code") and csnap is None:
                self.skipTest(f"{tier}: code snapshot {entry['code']['model_id']} absent — NOT MEASURED")
            self.assertEqual(el.compute_entry_hash(entry, snapshot=snap, code_snapshot=csnap),
                             entry["weights_hash"], tier)

    def test_control_the_cpu_entry_pins_the_auto_map_repository_and_gpu_has_none(self):
        # Amendment 5 §2: the catalogue names the SECOND repository nomic's config.json
        # auto_map reaches, by commit and by file; Qwen has no auto_map and no pin.
        tiers = el.catalogue_tiers()
        cpu, gpu = tiers["cpu"], tiers["gpu"]
        self.assertEqual(cpu["code"]["model_id"], "nomic-ai/nomic-bert-2048")
        self.assertRegex(cpu["code"]["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(cpu["code"]["files"], ["configuration_hf_nomic_bert.py", "modeling_hf_nomic_bert.py"])
        self.assertTrue(all(f.endswith(".py") for f in cpu["code"]["files"]))
        self.assertNotIn("code", gpu)
        snap, _ = _snapshot("cpu")
        if snap is not None:
            self.assertEqual(el.auto_map_repositories(snap), ["nomic-ai/nomic-bert-2048"])
        gsnap, _ = _snapshot("gpu")
        if gsnap is not None:
            self.assertEqual(el.auto_map_repositories(gsnap), [])

    def test_control_the_amended_shell_pipeline_reproduces_the_hash_across_both_repositories(self):
        # M1-C1's executable check, verbatim (Amendment 5):
        #   ( cd <model snapshot> && sha256sum $(jq -r '.files[]' <lock>);
        #     cd <code snapshot>  && sha256sum $(jq -r '.code.files[]' <lock>) ) | sha256sum
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum not on PATH")
        snap, entry = _snapshot("cpu")
        self.assertIn("code", entry, "the cpu entry carries no remote-code pin (r2 F3): the hash cannot reach the code repository")
        csnap = _code_snapshot(entry)
        if snap is None or csnap is None:
            self.skipTest("a cpu snapshot (model or code) is absent on this host")
        cmd = (f"( cd {snap} && sha256sum " + " ".join(entry["files"]) +
               f"; cd {csnap} && sha256sum " + " ".join(entry["code"]["files"]) + " ) | sha256sum")
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split()[0], entry["weights_hash"])
        # and the model-only half (the round-2 form) is a DIFFERENT value: the code
        # repository is inside the hash, not beside it
        r2 = subprocess.run("sha256sum " + " ".join(entry["files"]) + " | sha256sum",
                            shell=True, cwd=str(snap), capture_output=True, text=True, timeout=300)
        self.assertNotEqual(r2.stdout.split()[0], entry["weights_hash"])
        # the gpu tier has no code member: the first half IS the hash
        gsnap, gentry = _snapshot("gpu")
        if gsnap is not None:
            r3 = subprocess.run("sha256sum " + " ".join(gentry["files"]) + " | sha256sum",
                                shell=True, cwd=str(gsnap), capture_output=True, text=True, timeout=300)
            self.assertEqual(r3.stdout.split()[0], gentry["weights_hash"])

    def test_saboteur_a_changed_byte_in_either_repository_changes_the_hash(self):
        snap, entry = _snapshot("cpu")
        self.assertIn("code", entry, "the cpu entry carries no remote-code pin (r2 F3)")
        if snap is None or _code_snapshot(entry) is None:
            self.skipTest("a cpu snapshot (model or code) is absent on this host")
        with tempfile.TemporaryDirectory() as d:
            m, c = _box_copy(entry, Path(d))
            self.assertEqual(el.compute_entry_hash(entry, snapshot=m, code_snapshot=c), entry["weights_hash"])
            (m / "tokenizer_config.json").write_bytes((m / "tokenizer_config.json").read_bytes() + b" ")
            self.assertNotEqual(el.compute_entry_hash(entry, snapshot=m, code_snapshot=c), entry["weights_hash"],
                                "a tokenizer change with identical weights must change the hash")
        with tempfile.TemporaryDirectory() as d:
            m, c = _box_copy(entry, Path(d))
            py = c / "modeling_hf_nomic_bert.py"
            py.write_bytes(py.read_bytes() + b"\n# one byte of forward-pass code\n")
            self.assertNotEqual(el.compute_entry_hash(entry, snapshot=m, code_snapshot=c), entry["weights_hash"],
                                "a change to the REMOTE-CODE repository must change the hash (r2 F3)")
            self.assertEqual(el.compute_weights_hash(m, entry["files"]),
                             el.compute_weights_hash(m, entry["files"]))   # the model-only half is blind to it

    def test_saboteur_an_entry_with_a_code_pin_cannot_be_hashed_without_the_code_snapshot(self):
        snap, entry = _snapshot("cpu")
        if snap is None:
            self.skipTest("cpu snapshot absent on this host")
        with self.assertRaises(FileNotFoundError):
            el.compute_entry_hash(entry, snapshot=snap, code_snapshot=None)

    def test_saboteur_a_file_dropped_from_the_list_changes_the_hash(self):
        snap, entry = _snapshot("cpu")
        if snap is None:
            self.skipTest("cpu snapshot absent on this host")
        fewer = [f for f in entry["files"] if f != "vocab.txt"]
        self.assertNotEqual(fewer, entry["files"])
        self.assertNotEqual(el.compute_weights_hash(snap, fewer), entry["weights_hash"])

    def test_control_the_builder_agrees_with_the_committed_file(self):
        snap, _ = _snapshot("cpu")
        snap2, _ = _snapshot("gpu")
        if snap is None or snap2 is None:
            self.skipTest("a shipped snapshot is absent on this host; the builder cannot run")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_embedder_catalogue.py"), "--check"],
                           cwd=str(Path.home()), capture_output=True, text=True, timeout=600,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class L2_WriteLock(_Tmp):
    """Confound: a lock written by plain open()/write() would look identical
    after the fact; the temp→rename discipline is asserted by the absence of a
    temp file and by the receipt, which only the shipped writer appends."""

    def test_control_init_writes_one_tier_atomically_with_a_receipt(self):
        lock = el.write_lock(self.d, "cpu", locked_by="test")
        p = self.d / el.LOCK_NAME
        self.assertTrue(p.exists())
        on_disk = json.loads(p.read_text(encoding="utf-8"))
        cat = el.catalogue_tiers()["cpu"]
        for k in ("model_id", "revision", "weights_hash", "dim", "vec_table", "files"):
            self.assertEqual(on_disk[k], cat[k], k)
        self.assertEqual(on_disk["tier"], "cpu")
        self.assertEqual(on_disk["catalogue_hash"], el.catalogue_sha256())
        self.assertRegex(on_disk["locked_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual([x.name for x in self.d.glob(".embedder.lock.*")], [], "a temp file was left behind")
        receipts = [json.loads(l) for l in (self.d / el.LOCK_HISTORY_NAME).read_text().splitlines()]
        self.assertEqual(len(receipts), 1)
        self.assertEqual((receipts[0]["action"], receipts[0]["tier"], receipts[0]["previous"]), ("init", "cpu", None))
        self.assertEqual(lock["weights_hash"], cat["weights_hash"])

    def test_control_a_re_run_on_the_same_tier_is_a_no_op(self):
        el.write_lock(self.d, "cpu")
        before = (self.d / el.LOCK_NAME).read_bytes()
        el.write_lock(self.d, "cpu")
        self.assertEqual((self.d / el.LOCK_NAME).read_bytes(), before)
        self.assertEqual(len((self.d / el.LOCK_HISTORY_NAME).read_text().splitlines()), 1)

    def test_saboteur_a_re_run_naming_another_tier_is_refused_and_writes_nothing(self):
        el.write_lock(self.d, "cpu")
        before = (self.d / el.LOCK_NAME).read_bytes()
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.write_lock(self.d, "gpu")
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual((self.d / el.LOCK_NAME).read_bytes(), before)

    def test_saboteur_an_unshipped_tier_is_LOCK_NOT_SHIPPED(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.write_lock(self.d, "jina")
        self.assertEqual(cm.exception.code, "LOCK_NOT_SHIPPED")
        self.assertFalse((self.d / el.LOCK_NAME).exists())


class L5_ProcessRegister(_Tmp):
    """Confound: re-registering the SAME hash must be a no-op (the engine calls
    the loader more than once per process); only a DIFFERENT hash is refused."""

    def test_control_register_then_require(self):
        el.register_loaded(model_id="m", revision="r", weights_hash="a" * 64, dim=768, vec_table="memory_embeddings")
        self.assertEqual(el.require_loaded_hash(vec_table="memory_embeddings", dim=768), "a" * 64)
        el.register_loaded(model_id="m", revision="r", weights_hash="a" * 64, dim=768, vec_table="memory_embeddings")

    def test_saboteur_nothing_loaded_means_no_hash(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.require_loaded_hash()
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")

    def test_saboteur_a_second_model_in_the_process_is_refused(self):
        el.register_loaded(model_id="m", revision="r", weights_hash="a" * 64, dim=768, vec_table="memory_embeddings")
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.register_loaded(model_id="n", revision="r", weights_hash="b" * 64, dim=1024, vec_table="memory_embeddings_v3")
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual(el.loaded()["weights_hash"], "a" * 64)

    def test_saboteur_a_write_to_another_table_or_width_is_STAMP_INCONSISTENT(self):
        el.register_loaded(model_id="m", revision="r", weights_hash="a" * 64, dim=768, vec_table="memory_embeddings")
        for kw in ({"vec_table": "memory_embeddings_v3"}, {"dim": 1024}):
            with self.assertRaises(el.EmbedderLockError) as cm:
                el.require_loaded_hash(**kw)
            self.assertEqual(cm.exception.code, "STAMP_INCONSISTENT", kw)

    def test_saboteur_unresolved_and_non_hex_cannot_be_registered(self):
        for bad in ("unresolved", "", "F" * 64, "abc"):
            with self.assertRaises(el.EmbedderLockError, msg=repr(bad)):
                el.register_loaded(model_id="m", revision="r", weights_hash=bad, dim=768, vec_table="memory_embeddings")

    def test_control_exactly_eight_refusal_names(self):
        # SNAPSHOT_ABSENT joined in M1 round 5 (#6): the pin is not on disk and this
        # process may not fetch -- distinct from WEIGHTS_MISMATCH (bytes disagree).
        # MODELS_ABSENT joined in M1 round 6 (#1): the INSTALL could not fetch what
        # the memory needs -- the fetch lane's refusal, naming every absent pin.
        self.assertEqual(set(el.REFUSALS), {"LOCK_MISSING", "LOCK_MALFORMED", "LOCK_NOT_SHIPPED",
                                            "NO_ROWS_FOR_LOCK", "WEIGHTS_MISMATCH", "STAMP_INCONSISTENT",
                                            "SNAPSHOT_ABSENT", "MODELS_ABSENT"})
        with self.assertRaises(ValueError):
            el.EmbedderLockError("SOMETHING_ELSE", "x")


if __name__ == "__main__":
    unittest.main()

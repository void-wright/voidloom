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
    L3  read_lock / resolve: LOCK_MISSING, LOCK_MALFORMED, from the connection's own dir
    L4  the checksum against the lock: WEIGHTS_MISMATCH / LOCK_NOT_SHIPPED before instantiation
    L5  the process register
    L6  the cache resolver honours HF_HUB_CACHE, and the loader's offline probe uses it
    L8  swap is a typed refusal AND exits non-zero
    L9  the checksum reaches the code repository: a changed byte there is WEIGHTS_MISMATCH
    D2  the tier's source of truth: the lock first, the database for a pre-lock brain, unknown refused
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


# ------------------------------------------ L3: read_lock / resolve from the connection
class L3_ReadAndResolve(_Tmp):
    """Confound: an in-memory database has no data dir; that is LOCK_MISSING by
    the same name and is distinguished by its message."""

    def _db(self):
        p = self.d / "brain.db"
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        c.commit()
        return c

    def test_control_resolve_reads_the_lock_beside_the_connections_database(self):
        el.write_lock(self.d, "gpu")
        c = self._db()
        lock = el.resolve(c)
        self.assertEqual((lock["tier"], lock["vec_table"], lock["dim"]), ("gpu", "memory_embeddings_v3", 1024))
        import embed_resolver
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings_v3", 1024))
        c.close()

    def test_saboteur_lock_missing(self):
        """el.resolve refuses an absent lock. embed_resolver (owner decision D2) then reads
        a PRE-LOCK brain's metadata -- and with no metadata row either, refuses LOCK_MISSING."""
        c = self._db()
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(c)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        import embed_resolver
        with self.assertRaises(embed_resolver.EmbedTierUnresolved) as cm2:
            embed_resolver.resolve(c)
        self.assertEqual(cm2.exception.code, "LOCK_MISSING")
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        c.commit()
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings_v2", 1024),
                         "a pre-lock brain is read from its metadata (D2's fallback)")
        c.close()

    def test_saboteur_in_memory_database_has_no_lock(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(sqlite3.connect(":memory:"))
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        self.assertIn("in-memory", str(cm.exception))

    def test_saboteur_lock_malformed_each_way(self):
        el.write_lock(self.d, "cpu")
        good = json.loads((self.d / el.LOCK_NAME).read_text())
        plants = {
            "not json": "{",
            "not an object": "[]",
            "missing weights_hash": json.dumps({k: v for k, v in good.items() if k != "weights_hash"}),
            "short hash": json.dumps({**good, "weights_hash": "abc"}),
            "uppercase hash": json.dumps({**good, "weights_hash": good["weights_hash"].upper()}),
            "dim zero": json.dumps({**good, "dim": 0}),
            "empty files": json.dumps({**good, "files": []}),
            "empty tier": json.dumps({**good, "tier": ""}),
        }
        for name, text in plants.items():
            (self.d / el.LOCK_NAME).write_text(text, encoding="utf-8")
            with self.assertRaises(el.EmbedderLockError, msg=name) as cm:
                el.resolve(self.d)
            self.assertEqual(cm.exception.code, "LOCK_MALFORMED", name)

    def test_control_a_metadata_row_cannot_move_a_locked_brain(self):
        el.write_lock(self.d, "cpu")
        c = self._db()
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', 'memory_embeddings_v3')")
        c.commit()
        import embed_resolver
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings", 768),
                         "the planted metadata row moved the resolver: the sibling trap is live")
        c.close()

    def test_saboteur_a_malformed_lock_is_never_read_as_absent(self):
        el.write_lock(self.d, "cpu")
        (self.d / el.LOCK_NAME).write_text("{", encoding="utf-8")
        c = self._db()
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', 'memory_embeddings')")
        c.commit()
        import embed_resolver
        with self.assertRaises(embed_resolver.EmbedTierUnresolved) as cm:
            embed_resolver.resolve(c)
        self.assertEqual(cm.exception.code, "LOCK_MALFORMED")
        c.close()


# --------------------------------- L4: the checksum against the lock, before instantiation
class L4_VerifyBeforeInstantiate(_Tmp):
    """The executable check: forty f's → WEIGHTS_MISMATCH from a fresh
    embedder start. Confound: on a host WITHOUT the snapshot the refusal for an
    unshipped hash comes first (no fetch outside the catalogue); the control
    below needs the bytes and skips without them."""

    def _lock(self, tier="cpu", **kw):
        lock = dict(el.catalogue_tiers()[tier]); lock["tier"] = tier; lock.update(kw)
        return lock

    def test_control_the_locked_snapshot_verifies_and_returns_the_computed_hash(self):
        snap, entry = _snapshot("cpu")
        if snap is None:
            self.skipTest("cpu snapshot absent on this host")
        got_snap, computed = el.verify_snapshot_against_lock(self._lock())
        self.assertEqual(got_snap, snap)
        self.assertEqual(computed, entry["weights_hash"])
        self.assertIsNone(el.loaded(), "verify must not register; only a load does")

    def test_saboteur_forty_fs_is_WEIGHTS_MISMATCH_before_any_instantiation(self):
        snap, _ = _snapshot("cpu")
        if snap is None:
            self.skipTest("cpu snapshot absent on this host")
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.verify_snapshot_against_lock(self._lock(weights_hash="f" * 64))
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual(cm.exception.detail["lock_hash"], "f" * 64)
        self.assertRegex(cm.exception.detail["computed"], r"^[0-9a-f]{64}$")
        # and through the loader itself: nothing instantiated, nothing registered
        import embedding_upgrade as eu
        eu._model_cache.clear()
        with self.assertRaises(el.EmbedderLockError) as cm2:
            eu.get_locked_embedder(self._lock(weights_hash="f" * 64))
        self.assertEqual(cm2.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual(eu._model_cache, {})
        self.assertIsNone(el.loaded())

    def test_saboteur_a_real_but_unshipped_hash_is_LOCK_NOT_SHIPPED(self):
        snap, entry = _snapshot("cpu")
        if snap is None:
            self.skipTest("cpu snapshot absent on this host")
        # a catalogue that ships only gpu: the cpu bytes are real, verified, and NOT shipped
        cat = el.load_catalogue()
        only_gpu = {**cat, "tiers": {"gpu": cat["tiers"]["gpu"]}}
        alt = self.d / "alt.lock.json"
        alt.write_text(json.dumps(only_gpu), encoding="utf-8")
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.verify_snapshot_against_lock(self._lock(), catalogue=alt)
        self.assertEqual(cm.exception.code, "LOCK_NOT_SHIPPED")

    def test_saboteur_no_snapshot_and_unshipped_refuses_before_any_fetch(self):
        lock = self._lock(model_id="nobody/no-such-model", revision="0" * 40, weights_hash="a" * 64)
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.verify_snapshot_against_lock(lock, allow_fetch=True)
        self.assertEqual(cm.exception.code, "LOCK_NOT_SHIPPED")
        self.assertIn("refusing to fetch", str(cm.exception))

    def test_saboteur_a_missing_hashed_file_is_SNAPSHOT_ABSENT(self):
        snap, _ = _snapshot("cpu")
        if snap is None:
            self.skipTest("cpu snapshot absent on this host")
        lock = self._lock()
        lock["files"] = lock["files"] + ["not_in_snapshot.bin"]
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.verify_snapshot_against_lock(lock)
        self.assertEqual(cm.exception.code, "SNAPSHOT_ABSENT")
        self.assertIn("interrupted", str(cm.exception))

    def test_control_the_loader_passes_code_revision_for_a_pinned_code_repository(self):
        src = (ROOT / "organs" / "embedding_upgrade.py").read_text(encoding="utf-8")
        i = src.index("def get_locked_embedder("); j = src.index("\ndef ", i + 10)
        body = "\n".join(l for l in src[i:j].splitlines() if not l.strip().startswith("#"))
        self.assertIn('"code_revision": code["revision"]', body)
        self.assertIn("model_kwargs", body)
        self.assertIn("config_kwargs", body)
        self.assertIn("local_files_only", body)
        self.assertIn("verify_snapshot_against_lock(lock)", body)
        self.assertIn("_torch_device(torch)", body, "D1: the device comes from the compute-agnostic layer")
        self.assertLess(body.index("verify_snapshot_against_lock(lock)"), body.index("SentenceTransformer("),
                        "the bytes are verified BEFORE anything is instantiated")
        # and the loader has NO lock-less form (no package-root fallback)
        import inspect, embedding_upgrade as eu
        self.assertEqual(list(inspect.signature(eu.get_locked_embedder).parameters), ["lock"])
        with self.assertRaises(el.EmbedderLockError) as cm:
            eu.get_locked_embedder(None)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")


# ------------------------------------------ L6: one cache resolver, HF's precedence
class L6_CacheResolverHonoursHfHubCache(_Tmp):
    """Confound: HF_HOME alone must still resolve to HF_HOME/hub."""

    def _with(self, env, fn):
        keep = {k: os.environ.get(k) for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME")}
        for k in keep:
            os.environ.pop(k, None)
        os.environ.update(env)
        try:
            return fn()
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_control_precedence(self):
        self.assertEqual(self._with({"HF_HUB_CACHE": "/a", "HUGGINGFACE_HUB_CACHE": "/b", "HF_HOME": "/c"}, el.hf_hub_cache), Path("/a"))
        self.assertEqual(self._with({"HUGGINGFACE_HUB_CACHE": "/b", "HF_HOME": "/c"}, el.hf_hub_cache), Path("/b"))
        self.assertEqual(self._with({"HF_HOME": "/c"}, el.hf_hub_cache), Path("/c/hub"))
        self.assertEqual(self._with({}, el.hf_hub_cache), Path.home() / ".cache" / "huggingface" / "hub")

    def test_saboteur_a_cache_the_loader_would_not_use_finds_no_snapshot(self):
        entry = el.catalogue_tiers()["cpu"]
        got = self._with({"HF_HUB_CACHE": str(self.d)},
                         lambda: el.snapshot_dir_for(entry["model_id"], entry["revision"]))
        self.assertIsNone(got, "the resolver looked outside HF_HUB_CACHE")

    def test_control_the_engines_offline_probe_uses_the_same_resolver(self):
        for rel in ("organs/embedding_upgrade.py", "organs/claude_memory.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            i = src.index("def _hf_offline_ok("); j = src.index("\ndef ", i + 10)
            self.assertIn("embedder_lock.hf_hub_cache()", src[i:j], rel)


# ----------------------------------------------------- L8: swap is refused
class L8_SwapIsRefused(_Tmp):
    """Confound: a swap that refused but still wrote the metadata rows would pass
    a status check; the pair reads the rows back. And an error envelope
    with EXIT 0 reads as success to every shell gate -- the process exits 1."""

    def test_control_swap_refuses_typed_writes_nothing_and_exits_non_zero(self):
        import embedding_upgrade as eu
        db = self.d / "b.db"
        c = sqlite3.connect(str(db)); c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)"); c.commit(); c.close()

        class A:
            db_path = str(db)
        with self.assertRaises(eu.VerbRefused) as cm:
            eu.cmd_swap(A())
        env = cm.exception.as_dict()
        self.assertEqual((env["status"], env["error"], env["replacement"], env["built"]),
                         ("error", "SWAP_REFUSED", "tier set", True))
        self.assertIn("voidloom tier set", env["usage"])     # the refusal names the BUILT route
        c = sqlite3.connect(str(db))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM metadata").fetchone()[0], 0)
        c.close()
        # the PROCESS: envelope on stdout, exit status 1 -- read from the process, never a pipe
        r = subprocess.run([sys.executable, str(ROOT / "organs" / "embedding_upgrade.py"),
                            "--db-path", str(db), "swap"], capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": ""})
        self.assertEqual(r.returncode, 1, r.stdout[-400:] + r.stderr[-400:])
        self.assertEqual(json.loads(r.stdout)["error"], "SWAP_REFUSED")

    def test_control_the_shipped_swap_body_writes_no_metadata_row(self):
        src = (ROOT / "organs" / "embedding_upgrade.py").read_text(encoding="utf-8")
        i = src.index("def cmd_swap("); j = src.index("\ndef ", i + 10)
        self.assertNotIn("INSERT OR REPLACE INTO metadata", src[i:j])
        self.assertIn("raise VerbRefused(", src[i:j])


# ------------------------- L9: the checksum reaches the code repository
class L9_ChecksumReachesTheCodeRepository(_Tmp):
    """The loader's verify step against a FAKE HF cache holding copies of both
    snapshots: unchanged -> the catalogue hash; one byte changed in the remote-
    code repository -> WEIGHTS_MISMATCH, before any instantiation. Confound: a
    verify that hashed only the model snapshot passes BOTH halves of the old
    check; only a byte in the CODE repository tells them apart."""

    def _fake_cache(self, entry: dict, mutate_code: bool = False) -> Path:
        hub = self.d / "hub"
        snap, _ = _snapshot_of(entry)
        csnap = _code_snapshot(entry)
        def lay(model_id, revision, src, files):
            repo = hub / ("models--" + model_id.replace("/", "--"))
            sd = repo / "snapshots" / revision
            for rel in files:
                dst = sd / rel; dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / rel, dst)
            (repo / "refs").mkdir(parents=True, exist_ok=True)
            (repo / "refs" / "main").write_text(revision)
            return sd
        lay(entry["model_id"], entry["revision"], snap, entry["files"])
        cd = lay(entry["code"]["model_id"], entry["code"]["revision"], csnap, entry["code"]["files"])
        if mutate_code:
            py = cd / entry["code"]["files"][-1]
            py.write_bytes(py.read_bytes() + b"\n# a byte of forward-pass code the lock never promised\n")
        return hub

    def _with_cache(self, hub: Path, fn):
        keep = {k: os.environ.get(k) for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME")}
        for k in keep:
            os.environ.pop(k, None)
        os.environ["HF_HUB_CACHE"] = str(hub)
        try:
            return fn()
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def _lock(self):
        entry = el.catalogue_tiers()["cpu"]
        lock = dict(entry); lock["tier"] = "cpu"
        return lock

    def test_control_both_repositories_verify_from_the_cache(self):
        entry = el.catalogue_tiers()["cpu"]
        self.assertIn("code", entry, "the cpu entry carries no remote-code pin")
        if _snapshot_of(entry)[0] is None or _code_snapshot(entry) is None:
            self.skipTest("a cpu snapshot (model or code) is absent on this host")
        hub = self._fake_cache(entry)
        snap, computed = self._with_cache(hub, lambda: el.verify_snapshot_against_lock(self._lock(), allow_fetch=False))
        self.assertEqual(computed, entry["weights_hash"])
        self.assertTrue(str(snap).startswith(str(hub)))

    def test_saboteur_a_changed_byte_in_the_code_repository_is_WEIGHTS_MISMATCH_before_instantiation(self):
        entry = el.catalogue_tiers()["cpu"]
        self.assertIn("code", entry, "the cpu entry carries no remote-code pin")
        if _snapshot_of(entry)[0] is None or _code_snapshot(entry) is None:
            self.skipTest("a cpu snapshot (model or code) is absent on this host")
        hub = self._fake_cache(entry, mutate_code=True)
        with self.assertRaises(el.EmbedderLockError) as cm:
            self._with_cache(hub, lambda: el.verify_snapshot_against_lock(self._lock(), allow_fetch=False))
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertIsNotNone(cm.exception.detail.get("code_snapshot"))
        self.assertIsNone(el.loaded(), "verify must not register")
        # through the loader itself, still before anything is instantiated
        import embedding_upgrade as eu
        eu._model_cache.clear()
        with self.assertRaises(el.EmbedderLockError) as cm2:
            self._with_cache(hub, lambda: eu.get_locked_embedder(self._lock()))
        self.assertEqual(cm2.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual(eu._model_cache, {})

    def test_saboteur_a_missing_code_snapshot_is_refused_not_fetched_outside_the_catalogue(self):
        entry = el.catalogue_tiers()["cpu"]
        self.assertIn("code", entry, "the cpu entry carries no remote-code pin")
        if _snapshot_of(entry)[0] is None or _code_snapshot(entry) is None:
            self.skipTest("a cpu snapshot (model or code) is absent on this host")
        hub = self._fake_cache(entry)
        shutil.rmtree(hub / ("models--" + entry["code"]["model_id"].replace("/", "--")))
        lock = self._lock(); lock["weights_hash"] = "a" * 64          # not shipped
        with self.assertRaises(el.EmbedderLockError) as cm:
            self._with_cache(hub, lambda: el.verify_snapshot_against_lock(lock, allow_fetch=True))
        self.assertEqual(cm.exception.code, "LOCK_NOT_SHIPPED")
        with self.assertRaises(el.EmbedderLockError) as cm2:
            self._with_cache(hub, lambda: el.verify_snapshot_against_lock(self._lock(), allow_fetch=False))
        self.assertEqual(cm2.exception.code, "SNAPSHOT_ABSENT")     # absent + no fetch is its own refusal
        self.assertIn("remote code", str(cm2.exception))


# --------------------------- D2: the tier's source of truth, lock first, unknown refused
class D2_TierFactsLockFirst(_Tmp):
    """Owner decision D2. The engine's tier registry is the only source of the tier
    FACTS; the brain's install lock is read FIRST to learn which tier a brain has; a
    brain that predates the lock is read from its database; an unknown name is refused
    by name; the non-commercial tier is never what a lock (or the catalogue) yields.
    Confound: a resolver that only ever reads metadata passes the pre-lock fixture and
    fails the locked one; one that only reads the lock fails the pre-lock one."""

    def _brain(self, table=None):
        p = self.d / "claude_memory.db"
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        if table:
            c.execute("INSERT INTO metadata VALUES ('active_embedding_table', ?)", (table,))
        c.commit()
        c.close()
        return p

    def _installer_reads(self, db: Path) -> str:
        """install.sh's own reader (hd_embed_model_from_db), run as the installer runs it."""
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = sh.index("hd_embed_model_from_db() {")
        j = sh.index("\n}\n", i) + 3
        script = "%s\nPY_SYS=%s\nDB_PATH=%s\nhd_embed_model_from_db\n" % (sh[i:j], sys.executable, db)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_control_a_pre_lock_brain_is_read_from_its_database(self):
        db = self._brain("memory_embeddings_v2")
        self.assertEqual(self._installer_reads(db), "jina")
        import embed_resolver
        c = sqlite3.connect(str(db))
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings_v2", 1024))
        c.close()

    def test_control_the_lock_is_read_first_and_metadata_cannot_move_it(self):
        db = self._brain("memory_embeddings_v2")
        el.write_lock(self.d, "gpu")
        self.assertEqual(self._installer_reads(db), "qwen")
        import embed_resolver
        c = sqlite3.connect(str(db))
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings_v3", 1024))
        c.close()

    def test_saboteur_a_malformed_lock_is_refused_not_read_through(self):
        db = self._brain("memory_embeddings")
        (self.d / el.LOCK_NAME).write_text('{"vec_table": "memory_embeddings_v2"}', encoding="utf-8")
        self.assertEqual(self._installer_reads(db), "LOCK_MALFORMED",
                         "a lock naming an unshipped table was read through")
        (self.d / el.LOCK_NAME).write_text("{", encoding="utf-8")
        self.assertEqual(self._installer_reads(db), "LOCK_MALFORMED")

    def test_control_brain_lock_falls_back_to_the_catalogue_only_on_absence(self):
        import embedding_upgrade as eu
        db = self._brain("memory_embeddings")
        lock = eu.brain_lock(str(db), tier="nomic")
        self.assertEqual((lock["tier"], lock["weights_hash"]), ("cpu", el.catalogue_tiers()["cpu"]["weights_hash"]))
        self.assertIn("pre-lock", lock["_source"])
        el.write_lock(self.d, "gpu")
        self.assertEqual(eu.brain_lock(str(db), tier="nomic")["tier"], "gpu",
                         "a written lock is the answer whatever tier the caller guessed")
        (self.d / el.LOCK_NAME).write_text("{", encoding="utf-8")
        with self.assertRaises(el.EmbedderLockError) as cm:
            eu.brain_lock(str(db), tier="nomic")
        self.assertEqual(cm.exception.code, "LOCK_MALFORMED")

    def test_saboteur_the_non_commercial_tier_and_a_typo_are_not_shipped(self):
        import embedding_upgrade as eu
        db = self._brain()
        for name in ("jina", "nomci", ""):
            with self.assertRaises(el.EmbedderLockError, msg=name) as cm:
                eu.brain_lock(str(db), tier=name)
            self.assertEqual(cm.exception.code, "LOCK_NOT_SHIPPED", name)
        self.assertNotIn("jinaai/jina-embeddings-v5-text-small",
                         {e["model_id"] for e in el.catalogue_tiers().values()})

    def test_control_the_qwen_load_is_the_pinned_by_name_call(self):
        """The one loader keeps public main's reproducibility pin for Qwen: a by-name load at
        QWEN_REVISION, local files only, AFTER the bytes were verified."""
        src = (ROOT / "organs" / "embedding_upgrade.py").read_text(encoding="utf-8")
        i = src.index("def get_locked_embedder("); j = src.index("\ndef ", i + 10)
        body = src[i:j]
        self.assertIn("SentenceTransformer(\n            QWEN_MODEL_NAME,\n            revision=QWEN_REVISION,", body)
        self.assertIn('lock["revision"] != QWEN_REVISION', body)


if __name__ == "__main__":
    unittest.main()

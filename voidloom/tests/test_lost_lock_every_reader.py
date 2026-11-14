"""A lock that was written and is gone refuses at every reader; a brain that never had one reads as before.

THE CLAIM UNDER TEST
  * one refusal code, LOCK_MISSING, names two states: a brain that never had a lock (legal: it is read from its
    metadata) and a brain whose lock was written and is gone (broken: nothing vouches for its vectors). The detail
    `was_locked` tells them apart, and every reader that turns the code into "no lock" reads it. On a lost lock the
    shared resolver, the installer's declared table, `tier status` and `tier set --plan`, a write with the caller's own
    encoder, a centroid stamp, the engine's own lock read, the catalogue fallback and the stats verb all refuse or say
    the lock is gone; none of them presents the brain as one that never had a lock;
  * the nightly units that reach a newly refusing path exit with a status their declared SuccessExitStatus does not
    admit, so a scheduler never records the broken brain's night as a success;
  * the same three brains with the lock never written read exactly as a pre-lock brain always has.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
for p in (str(ORGANS), str(ROOT / "install"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from voidloom.tests.test_doctor_brain_verdict import NOMIC, NOMIC_DIM, _Brain, el, mw  # noqa: E402

import embed_resolver as er  # noqa: E402


def _caller_encoder(_text):
    return struct.pack(f"{NOMIC_DIM}f", *([0.01] * NOMIC_DIM))


class _Three(_Brain):
    """A locked cpu brain with two stamped memories and the metadata rows `init` writes, then put in one STATE."""
    STATE = "locked"

    def setUp(self):
        super().setUp()
        self.declare(NOMIC, NOMIC_DIM)
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_model', ?)",
                          ("nomic-ai/nomic-embed-text-v1.5",))
        self.conn.commit()
        self.memory("tides and lanterns", whash=self.h)
        self.memory("harbour bells at dusk", whash=self.h)
        if self.STATE in ("lost", "never"):
            (self.d / el.LOCK_NAME).unlink()
        if self.STATE == "never":
            (self.d / el.LOCK_HISTORY_NAME).unlink()
            self.conn.execute("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
            self.conn.commit()
        self.conn.close()
        self.conn = self.connect()

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


class ALostLock(_Three):
    STATE = "lost"

    def test_saboteur_the_shared_resolver_refuses_and_says_the_lock_was_written(self):
        with self.assertRaises(er.EmbedTierUnresolved) as cm:
            er.resolve(self.conn)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        self.assertIs(cm.exception.detail.get("was_locked"), True)
        with self.assertRaises(er.EmbedTierUnresolved):
            er._from_lock(self.conn)

    def test_saboteur_the_installers_declared_table_is_never_the_metadata_row(self):
        self.assertEqual(er.declared_table(self.conn), ("", "lock", "LOCK_MISSING"))

    def test_saboteur_tier_status_and_plan_refuse_instead_of_reporting_no_lock(self):
        import tier_set
        with self.assertRaises(tier_set.TierRefused) as cm:
            tier_set._installed_lock(self.db)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        for fn in (lambda: tier_set.status(self.db), lambda: tier_set.plan(self.db, "qwen")):
            with self.assertRaises(tier_set.TierRefused) as cm:
                fn()
            self.assertIn(cm.exception.code, ("LOCK_MISSING", "TIER_UNRESOLVED"))

    def test_saboteur_a_write_with_the_callers_encoder_is_refused_and_writes_nothing(self):
        before = self.count()
        with self.assertRaises(Exception) as cm:
            mw.write_memory(self.conn, content="a lost-lock write", category="insight", encoder=_caller_encoder)
        self.conn.rollback()
        self.assertEqual(self.count(), before, "the write committed on a brain whose lock is gone")
        self.assertIn("LOCK_MISSING", f"{getattr(cm.exception, 'code', '')} {cm.exception}")

    def test_saboteur_a_stamp_written_directly_refuses(self):
        mid = self.memory("a stamp with no lock behind it", vector=False)
        with self.assertRaises(el.EmbedderLockError) as cm:
            mw._stamp_provenance(self.conn, mid, NOMIC, NOMIC_DIM, "a stamp with no lock behind it", organ="test")
        self.assertEqual(cm.exception.code, "LOCK_MISSING")

    def test_saboteur_a_centroid_stamp_refuses(self):
        ids = [r[0] for r in self.conn.execute(f"SELECT memory_id FROM {NOMIC} ORDER BY memory_id")]
        vecs = [self.conn.execute(f"SELECT embedding FROM {NOMIC} WHERE memory_id = ?", (i,)).fetchone()[0]
                for i in ids]
        mid = self.memory("a consolidation", vector=False)
        with self.assertRaises(el.EmbedderLockError) as cm:
            mw.stamp_centroid(self.conn, mid, NOMIC, NOMIC_DIM, ids, vecs, organ="test")
        self.assertEqual(cm.exception.code, "LOCK_MISSING")

    def test_saboteur_the_engines_own_lock_read_and_the_catalogue_fallback_refuse(self):
        import claude_memory as cm_mod
        import embedding_upgrade as eu
        saved = cm_mod.DB_PATH
        cm_mod.DB_PATH = self.db
        try:
            with self.assertRaises(el.EmbedderLockError) as cm:
                cm_mod._install_lock()
            self.assertEqual(cm.exception.code, "LOCK_MISSING")
        finally:
            cm_mod.DB_PATH = saved
        with self.assertRaises(el.EmbedderLockError) as cm:
            eu.brain_lock(self.conn, tier="nomic")
        self.assertEqual(cm.exception.code, "LOCK_MISSING")

    def test_saboteur_stats_never_calls_a_lost_lock_a_brain_that_predates_the_lock(self):
        import embedding_upgrade as eu
        out = eu.cmd_stats(argparse.Namespace(db_path=str(self.db), json=True))
        lock = out["lock"]
        self.assertNotEqual(lock.get("basis"), "lockless", lock)
        self.assertIs(lock.get("present"), False, lock)
        self.assertEqual(lock.get("error"), "LOCK_MISSING", lock)

    def test_saboteur_the_dream_plan_refuses_rather_than_planning_on_no_table(self):
        import dream_ledger
        with self.assertRaises(er.EmbedTierUnresolved) as cm:
            dream_ledger.plan(self.conn)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        for verb in (["backfill", "--plan"], ["backfill", "--apply", "--restamp"]):
            env = dream_ledger.run([*verb, "--db", str(self.db)])
            self.assertEqual((env["status"], env.get("code")), ("error", "LOCK_MISSING"), env)

    def test_saboteur_a_plain_init_refuses_and_names_the_repair_and_the_repair_works(self):
        for part in ("organs", "migrations"):            # the engine reads the brain beside its own organs/
            shutil.copytree(ROOT / part, self.d / part, ignore=shutil.ignore_patterns("__pycache__"))

        def init(*extra):
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d), "HF_HUB_OFFLINE": "1",
                   "WIZARD_ACCELERATOR": "cpu", "PYTHONDONTWRITEBYTECODE": "1"}
            if os.environ.get("CUDA_VISIBLE_DEVICES"):
                env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
            return subprocess.run([sys.executable, str(self.d / "organs" / "claude_memory.py"), "init", *extra],
                                  capture_output=True, text=True, timeout=300, env=env, cwd=str(self.d))
        r = init()
        self.assertEqual(r.returncode, 1, r.stdout[-400:] + r.stderr[-400:])
        self.assertIn("LOCK_MISSING", r.stdout)
        self.assertIn("init --tier cpu", r.stdout)
        self.assertFalse((self.d / el.LOCK_NAME).exists(), "a refused init wrote a lock")
        r = init("--tier", "cpu")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.conn.close()
        self.conn = self.connect()
        self.assertEqual(er.resolve(self.conn), (NOMIC, NOMIC_DIM))
        self.assertEqual(el.resolve(self.conn)["tier"], "cpu")

    def test_saboteur_backfill_is_an_error_not_the_declared_success_refusal(self):
        import embed_backfill
        res = embed_backfill.backfill(str(self.db), only_missing=True)
        self.assertEqual((res["status"], res["code"]), ("error", "LOCK_MISSING"), res)

    def test_saboteur_the_windows_installers_fetch_names_the_lost_lock(self):
        src = (ROOT / "install" / "install.ps1").read_text(encoding="utf-8-sig")
        m = re.search(r"\$predlPy = @'\r?\n(.*?)\r?\n'@", src, re.S)
        self.assertIsNotNone(m, "the pre-download block moved")
        script = self.d / "predl.py"
        script.write_text(m.group(1), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d), "HF_HUB_OFFLINE": "1",
               "EMBED_MODEL_REPO": "nomic-ai/nomic-embed-text-v1.5", "RERANKER_MODEL": "",
               "WIZARD_LIBRARY_HOME": str(ROOT), "WIZARD_LIBRARY_DB": str(self.db)}
        if os.environ.get("CUDA_VISIBLE_DEVICES"):
            env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
        r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("LOCK_MISSING", r.stderr)
        self.assertNotIn("did not finish", r.stderr)


    def test_saboteur_the_posix_installers_fetch_names_the_lost_lock(self):
        src = (ROOT / "install.sh").read_text(encoding="utf-8")
        blocks = [b for b in re.findall(r"<<'PYEOF'[^\n]*\n(.*?)\nPYEOF\n", src, re.S)
                  if 'embedding_upgrade.brain_lock(os.environ["WIZARD_LIBRARY_DB"]' in b]
        self.assertEqual(len(blocks), 1, "the pre-cache block moved")
        script = self.d / "precache.py"
        script.write_text(blocks[0], encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d), "HF_HUB_OFFLINE": "1",
               "HF_HOME": str(self.d / "hf"), "BRAIN_DIR": str(ROOT), "WIZARD_LIBRARY_DB": str(self.db),
               "EMBED_REPO": "nomic-ai/nomic-embed-text-v1.5", "WIZARD_EMBED_MODEL": "nomic", "RERANKER_REPO": ""}
        if os.environ.get("CUDA_VISIBLE_DEVICES"):
            env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
        r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("LOCK_MISSING", r.stdout + r.stderr)


class NeverLocked(_Three):
    """The control: no lock, no history, no row. Every reader above answers from the metadata, as before."""
    STATE = "never"

    def test_control_the_resolver_and_the_declared_table_read_the_metadata(self):
        self.assertEqual(er.resolve(self.conn), (NOMIC, NOMIC_DIM))
        self.assertIsNone(er._from_lock(self.conn))
        self.assertEqual(er.declared_table(self.conn), (NOMIC, "metadata", ""))

    def test_control_tier_status_reports_no_lock(self):
        import tier_set
        self.assertIsNone(tier_set._installed_lock(self.db))
        st = tier_set.status(self.db)
        self.assertEqual((st["status"], st["current"]["lock_tier"], st["current"]["vec_table"]), ("ok", None, NOMIC))

    def test_control_a_write_with_the_callers_encoder_proceeds_unresolved(self):
        before = self.count()
        mw.write_memory(self.conn, content="a pre-lock write", category="insight", encoder=_caller_encoder)
        self.conn.commit()
        self.assertEqual(self.count(), before + 1)
        stamp = self.conn.execute(f"SELECT weights_hash FROM {mw.PROVENANCE_TABLE} ORDER BY rowid DESC LIMIT 1"
                                  ).fetchone()[0]
        self.assertEqual(stamp, "unresolved")

    def test_control_a_stamp_written_directly_is_unresolved(self):
        mid = self.memory("a stamp on a pre-lock brain", vector=False)
        self.assertEqual(mw._stamp_provenance(self.conn, mid, NOMIC, NOMIC_DIM, "a stamp on a pre-lock brain",
                                              organ="test")["weights_hash"], "unresolved")

    def test_control_the_engines_lock_read_and_the_catalogue_fallback_answer(self):
        import claude_memory as cm_mod
        import embedding_upgrade as eu
        saved = cm_mod.DB_PATH
        cm_mod.DB_PATH = self.db
        try:
            self.assertIsNone(cm_mod._install_lock())
        finally:
            cm_mod.DB_PATH = saved
        self.assertTrue(eu.brain_lock(self.conn, tier="nomic")["_source"].startswith("catalogue"))
        out = eu.cmd_stats(argparse.Namespace(db_path=str(self.db), json=True))
        self.assertEqual((out["lock"]["present"], out["lock"]["basis"]), (False, "lockless"), out["lock"])

    def test_control_the_dream_plan_reads_the_metadata_table(self):
        import dream_ledger
        self.assertEqual(dream_ledger._active_table(self.conn), NOMIC)
        self.assertEqual(dream_ledger.run(["backfill", "--plan", "--db", str(self.db)])["status"], "ok")

    def test_control_backfill_has_nothing_to_do(self):
        import embed_backfill
        res = embed_backfill.backfill(str(self.db), only_missing=True)
        self.assertEqual((res["status"], res["code"]), ("ok", "backfill_nothing_to_do"), res)


class StillLocked(_Three):
    STATE = "locked"

    def test_control_the_resolver_reads_the_lock(self):
        self.assertEqual(er.declared_table(self.conn), (NOMIC, "lock", ""))
        self.assertEqual(er.resolve(self.conn), (NOMIC, NOMIC_DIM))


def _success_statuses(unit: dict) -> set:
    ok = {0}
    for line in unit.get("service_extra") or []:
        if line.startswith("SuccessExitStatus="):
            ok |= {int(x) for x in line.split("=", 1)[1].split() if x.isdigit()}
    return ok


class ANightOnALostLockIsNeverASuccess(_Three):
    """Every unit in the roster is run on the lost-lock brain. Each one whose run reaches the lock (its output names
    LOCK_MISSING) exits with a status its unit does not declare a success. A unit that never reads the lock is not
    asked anything here; the two that read it first every night must be among those that reach it."""
    STATE = "lost"
    MUST_REACH = ("embed-backfill", "dreaming")

    def run_unit(self, name, command):
        args = shlex.split(command.replace("{DB}", str(self.db)).replace("{BRAIN}", str(self.d))
                           .replace("{CFG}", str(self.d / "no_config.json")))
        args[0] = str(ROOT / args[0])
        home = self.d / f"home_{name}"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "HF_HUB_OFFLINE": "1",
               "TRANSFORMERS_OFFLINE": "1", "WIZARD_ACCELERATOR": "cpu", "PYTHONDONTWRITEBYTECODE": "1",
               "WIZARD_LIBRARY_DB": str(self.db), "VOIDLOOM_DB": str(self.db)}
        if os.environ.get("CUDA_VISIBLE_DEVICES"):
            env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
        return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=600, env=env,
                              cwd=str(self.d), stdin=subprocess.DEVNULL)

    def owed(self, since):
        """Memories written after id `since`: (id, has a vector, carries the embedding-missing tag)."""
        c = sqlite3.connect(str(self.db))
        try:
            return [(mid, c.execute(f"SELECT 1 FROM {NOMIC}_rowids WHERE rowid = ?", (mid,)).fetchone() is not None,
                     c.execute("SELECT 1 FROM memory_tags WHERE memory_id = ? AND tag = ?",
                               (mid, mw.MISSING_TAG)).fetchone() is not None)
                    for (mid,) in c.execute("SELECT id FROM memories WHERE id > ? ORDER BY id", (since,))]
        finally:
            c.close()

    def test_saboteur_each_unit_that_reaches_the_lost_lock_exits_outside_its_declared_successes(self):
        """A unit may still exit a success when the lock only stopped an embedding it does not own: the memory it
        wrote is text with no vector, tagged as owed, and the backfill unit (which must fail) owns it. Anything
        else that reaches the lock and exits a success is a broken night recorded as a good one."""
        roster = json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))["units"]
        self.assertIn("SuccessExitStatus=3", roster["embed-backfill"].get("service_extra") or [],
                      "the hazard this guards (a refusal declared a success) is no longer in the roster")
        reached = {}
        for name, unit in sorted(roster.items()):
            for tag, command in ((name, unit["exec"]), (name + ".post", unit.get("exec_post"))):
                if not command:
                    continue
                since = self.count() and self.conn.execute("SELECT MAX(id) FROM memories").fetchone()[0]
                r = self.run_unit(tag, command)
                if "LOCK_MISSING" in r.stdout + r.stderr:
                    reached[tag] = (r.returncode, _success_statuses(unit), self.owed(since),
                                    (r.stdout + r.stderr)[-600:])
        self.assertTrue(set(self.MUST_REACH) <= set(reached), f"reached: {sorted(reached)}")
        self.assertNotIn(reached["embed-backfill"][0], reached["embed-backfill"][1])
        for tag, (rc, ok, owed, tail) in sorted(reached.items()):
            with self.subTest(unit=tag):
                if rc in ok:
                    self.assertTrue(owed and all(not vec and tagged for _mid, vec, tagged in owed),
                                    f"{tag} exits {rc}, which its unit declares a success, and left no owed, "
                                    f"vectorless memory for the backfill to answer for: {owed} {tail}")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""What the installer does with a refusal, and what it prints as proof.

  * a typed `init` refusal (LOCK_NOT_SHIPPED, WEIGHTS_MISMATCH, LOCK_MALFORMED) STOPS the install and is shown; it is
    never hidden behind a drop to a Tier-0 brain. An untyped failure (no sqlite_vec) still falls to Tier 0, and says
    the engine's own last line, not "unavailable".
  * a seed pack that fails its denylist scrub stops the install, on both shapes that load one; any other load failure
    stays a soft note, because an empty brain is a working brain.
  * the Tier-1 verify gate counts what the lock's reader will search, not the rows in the table.
  * the install runs the seed doctrine self-test and prints its real number, or says it did not run and why.

The shipped TEXT of install.sh runs in each case: whole installs through the installer-questions harness where a
fixture can reach the path, and the extracted block, under bash, where one cannot (a laid seed pack is hash-verified, so
only the block can meet a poisoned one).
"""
from __future__ import annotations

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

from voidloom.tests.test_installer_questions import _Home

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "install.sh"
ORGANS = ROOT / "organs"
sys.path.insert(0, str(ORGANS))

try:
    import sqlite_vec  # noqa: F401
    HAVE_VEC = True
except Exception:
    HAVE_VEC = False


def _src() -> str:
    return SH.read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    s = _src()
    i = s.index(start)
    return s[i:s.index(end, i)]


@unittest.skipUnless(HAVE_VEC, "needs an interpreter with sqlite_vec: init must get as far as the lock")
class TheTypedInitRefusalStopsTheInstall(_Home):
    """PY_SYS is this interpreter (first on PATH), so `init` really runs and really refuses."""

    def setUp(self):
        super().setUp()
        self.path = "%s:%s" % (Path(sys.executable).parent, self.path)
        self.brain.mkdir(parents=True, exist_ok=True)

    def _go(self, tier="nomic"):
        return self._run("--init-blank", "--tier0-only", "--force-cpu", WIZARD_EMBED_MODEL=tier)

    def _plant_other_tier(self):
        import embedder_lock
        embedder_lock.write_lock(self.brain, "gpu", locked_by="test")

    def test_a_lock_naming_another_tier_stops_the_install_and_names_the_code(self):
        self._plant_other_tier()
        p = self._go()
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out[-1500:])
        self.assertIn("WEIGHTS_MISMATCH", p.stderr, out[-1500:])
        self.assertIn("init refused, typed", p.stderr)
        self.assertNotIn("building the brain at Tier 0", out)
        self.assertNotIn("fresh brain initialized at Tier 0", out)
        self.assertNotIn("installed", out[-300:].lower())

    def test_an_unreadable_lock_stops_the_install_and_names_the_code(self):
        (self.brain / "embedder.lock.json").write_text("{ not json", encoding="utf-8")
        p = self._go()
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-1500:])
        self.assertIn("LOCK_MALFORMED", p.stderr)
        self.assertNotIn("building the brain at Tier 0", p.stdout)

    def test_a_refused_run_leaves_nothing_a_rerun_would_mistake_for_the_brain(self):
        self._plant_other_tier()
        self.assertEqual(self._go().returncode, 1)
        self.assertFalse((self.brain / "claude_memory.db").exists(),
                         "the refused run left a database, so a second run reads it as a finished brain")
        p = self._go()
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-1500:])
        self.assertIn("WEIGHTS_MISMATCH", p.stderr)

    def test_control_no_refusal_no_stop(self):
        p = self._go()
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertIn("fresh brain initialized at", p.stdout)
        self.assertTrue((self.brain / "embedder.lock.json").is_file())


class TheUntypedFailureStillFallsToTierZero(_Home):
    """A python with no sqlite_vec: the guaranteed tier still builds, and the reason is the engine's, not a shrug."""

    def setUp(self):
        super().setUp()
        r = subprocess.run(["/usr/bin/python3", "-c", "import sqlite_vec"], capture_output=True)
        if r.returncode == 0:
            self.skipTest("this host's /usr/bin/python3 has sqlite_vec, so init would not fail")

    def test_tier_zero_carries_the_engines_last_line(self):
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertIn("full-engine init FAILED on", p.stdout)
        self.assertIn("sqlite_vec", p.stdout)
        self.assertNotIn("full-engine init unavailable", p.stdout)
        self.assertIn("fresh brain initialized at Tier 0", p.stdout)

    def test_a_tier_zero_brain_carries_the_column_the_armed_unit_needs(self):
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        c = sqlite3.connect(str(self.brain / "claude_memory.db"))
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(memories)")}
        finally:
            c.close()
        self.assertIn("skill_tier", cols, "the armed skill-graduation unit refuses on a brain without this column")


class TheVenvInitBlockKeepsTheCause(unittest.TestCase):
    """C.init, the shipped text under bash with a stub interpreter in the venv's place."""

    def _run(self, stub_body):
        with tempfile.TemporaryDirectory(dir=str(Path.home() / ".cache")) as td:
            td = Path(td)
            stub = td / "py"
            stub.write_text("#!/bin/sh\n" + stub_body, encoding="utf-8")
            stub.chmod(0o755)
            block = _between("            # C.init -- THE LOCK, under the venv, BEFORE the pre-cache", "            # C.cache")
            script = ('say() { echo "$*"; }; warn() { echo "WARN: $*" >&2; }; die() { echo "FATAL: $*" >&2; exit 1; }\n'
                      'BRAIN_DIR=%r; DB_PATH=%r; VENV_PY=%r; EMBED_MODEL=nomic\n' % (str(td), str(td / "b.db"), str(stub))) + block
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)

    def test_a_typed_refusal_under_the_venv_stops(self):
        p = self._run('echo "{\\"error\\": \\"LOCK_NOT_SHIPPED: the catalogue does not carry it\\"}"; exit 1\n')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("LOCK_NOT_SHIPPED", p.stderr)

    def test_an_untyped_failure_under_the_venv_warns_with_its_last_line(self):
        p = self._run('echo "noise"; echo "disk full, as it happens"; exit 1\n')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("disk full, as it happens", p.stderr)

    def test_the_block_sits_before_the_pre_cache_it_serves(self):
        s = _src()
        self.assertLess(s.index("            # C.init -- THE LOCK, under the venv"), s.index("            # C.cache"))


class ASeedThatFailsItsScrubStopsTheInstall(unittest.TestCase):
    """The real loader, the real denylist, a pack that trips it: the two load sites' shipped text, under bash."""

    BRANCHES = {
        "fresh": ('elif [ -f "$SEED_PACK" ] && [ -f "$LITE_ENGINE" ]; then\n    step "STEP B.1 — seed pack"\n', "else\n    [ -f \"$SEED_PACK\" ] ||"),
        "ambient": ('if [ "$SHAPE" = "ambient" ] && [ "${SEED_LOAD:-0}" -eq 1 ]', 'elif [ "$SHAPE" = "ambient" ]; then\n    # R1: an UPGRADE adds no memories'),
    }

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(dir=str(Path.home() / ".cache")))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.db = self.td / "claude_memory.db"
        sqlite3.connect(str(self.db)).close()
        r = subprocess.run([sys.executable, str(ORGANS / "schema_migrator.py"), "apply", "--db", str(self.db)],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.pack = self.td / "seed_pack.json"

    def _poison(self):
        d = json.loads((ROOT / "seed" / "seed_pack.json").read_text(encoding="utf-8"))
        rows = d["memories"] if isinstance(d, dict) else d
        rows[0]["content"] = rows[0]["content"] + " see /home/someone/private/notes"
        self.pack.write_text(json.dumps(d), encoding="utf-8")

    def _clean(self):
        shutil.copy(str(ROOT / "seed" / "seed_pack.json"), str(self.pack))

    def _block(self, name):
        start, end = self.BRANCHES[name]
        body = _between(start, end)
        body = body[body.index("then\n") + 5:]        # the branch body, without its own if/elif line
        return body

    def _run(self, name):
        script = ('say() { echo "$*"; }; warn() { echo "WARN: $*" >&2; }; die() { echo "FATAL: $*" >&2; exit 1; }\n'
                  'step() { :; }\n'
                  'SEED_PACK=%r; LITE_ENGINE=%r; DB_PATH=%r; PY_SYS=%r; BRAIN_DIR=%r; SEED_HOW=test; SEED_PLAN_VER=test; '
                  'GPU_DEVICE=none\n' % (str(self.pack), str(ORGANS / "claude_memory_lite.py"), str(self.db),
                                         sys.executable, str(ROOT))) + self._block(name) + "\necho REACHED_THE_END\n"
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120)

    def _rows(self):
        c = sqlite3.connect(str(self.db))
        try:
            return c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            c.close()

    def test_fresh_brain_a_poisoned_pack_stops_and_names_the_scrub(self):
        self._poison()
        p = self._run("fresh")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("denylist scrub", p.stderr)
        self.assertNotIn("REACHED_THE_END", p.stdout)
        self.assertEqual(self._rows(), 0)

    def test_owner_chosen_load_a_poisoned_pack_stops_too(self):
        self._poison()
        p = self._run("ambient")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("denylist scrub", p.stderr)
        self.assertNotIn("REACHED_THE_END", p.stdout)

    def test_the_refusal_never_prints_what_matched(self):
        self._poison()
        p = self._run("fresh")
        self.assertNotIn("/home/someone", p.stdout + p.stderr)

    def test_control_a_clean_pack_loads_and_the_install_goes_on(self):
        self._clean()
        p = self._run("fresh")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("REACHED_THE_END", p.stdout)
        self.assertGreater(self._rows(), 0)

    def test_control_any_other_failure_stays_a_note(self):
        self.pack.write_text("{ not json", encoding="utf-8")
        p = self._run("fresh")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("REACHED_THE_END", p.stdout)
        self.assertIn("the brain is usable, just emptier", p.stdout)


class TheInstallRunsTheSelfTest(unittest.TestCase):
    """The function install.sh calls after the seed embeds, with an interpreter stub playing the lite engine's three
    exits. The line printed is the engine's own; nothing here decides a number."""

    def _run(self, stdout_line, rc):
        with tempfile.TemporaryDirectory(dir=str(Path.home() / ".cache")) as td:
            td = Path(td)
            log = td / "argv"
            stub = td / "py"
            stub.write_text("#!/bin/sh\n"
                            'case "$*" in *seed-self-test*) ;; *) exec %s "$@" ;; esac\n'
                            'env | grep -E "^(HF_HUB_OFFLINE|WIZARD_LIBRARY_DB)=" > %s\necho %s\nexit %d\n'
                            % (sys.executable, log, json.dumps(stdout_line), rc), encoding="utf-8")
            stub.chmod(0o755)
            (td / "organs").mkdir()
            (td / "organs" / "seed_loader.py").write_text("", encoding="utf-8")
            (td / "seed").mkdir()
            (td / "seed" / "seed_doctrine_queries.json").write_text("{}", encoding="utf-8")
            fn = _between("seed_self_test() {", "\n}\n") + "\n}\n"
            script = ('say() { echo "$*"; }; warn() { echo "WARN: $*" >&2; }\n'
                      'BRAIN_DIR=%r; DB_PATH=%r; VENV_PY=%r; LITE_ENGINE=%r\n' % (str(td), str(td / "b.db"), str(stub), str(td / "lite.py"))
                      ) + fn + "seed_self_test\necho rc=$?\n"
            p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
            return p, (log.read_text(encoding="utf-8") if log.exists() else "")

    def test_a_pass_prints_the_engines_line_offline(self):
        p, envlog = self._run(json.dumps({"status": "ok", "line": "seed doctrine self-test: 24 of 25 in top 3 (hybrid; bar 23): PASS"}), 0)
        self.assertIn("seed doctrine self-test: 24 of 25", p.stdout)
        self.assertIn("rc=0", p.stdout)
        self.assertIn("HF_HUB_OFFLINE=1", envlog)

    def test_below_the_bar_prints_red_and_the_install_goes_on(self):
        p, _ = self._run(json.dumps({"status": "ok", "line": "seed doctrine self-test: 20 of 25 in top 3 (hybrid; bar 23): RED"}), 5)
        self.assertIn("20 of 25", p.stdout + p.stderr)
        self.assertIn("RED", p.stdout + p.stderr)
        self.assertIn("rc=0", p.stdout)

    def test_a_self_test_that_could_not_run_says_so_with_its_code(self):
        p, _ = self._run(json.dumps({"status": "error", "error": "SELF_TEST_UNAVAILABLE", "message": "no reranker"}), 3)
        out = p.stdout + p.stderr
        self.assertIn("did not run", out)
        self.assertIn("SELF_TEST_UNAVAILABLE", out)
        self.assertNotIn("PASS", out)

    def test_unreadable_output_is_not_a_pass(self):
        p, _ = self._run("Traceback (most recent call last):", 1)
        out = p.stdout + p.stderr
        self.assertIn("did not run", out)
        self.assertNotIn("PASS", out)

    def test_it_is_called_where_the_seed_embeds(self):
        s = _src()
        i = s.index('say "  seed pack embedded')
        self.assertIn("seed_self_test", s[i:i + 200])


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec: the gate counts a vec0 table")
class TheVerifyGateCountsTheSearchSet(unittest.TestCase):
    """The T1_VERIFY body, byte for byte, against a real locked brain; only the recall it spawns is a stand-in (a
    package named voidloom that prints an envelope), because the count is what is under test."""

    GOOD = {"status": "ok", "tier": 1, "mode": "hybrid", "result_count": 1, "results": [{"id": 1}]}

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(dir=str(Path.home() / ".cache")))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.db = self.td / "claude_memory.db"
        sqlite3.connect(str(self.db)).close()
        r = subprocess.run([sys.executable, str(ORGANS / "schema_migrator.py"), "apply", "--db", str(self.db)],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        pkg = self.td / "voidloom"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "__main__.py").write_text("print(%r)\n" % json.dumps(self.GOOD), encoding="utf-8")
        s = _src()
        a = s.index("<<'T1_VERIFY_PY'\n") + len("<<'T1_VERIFY_PY'\n")
        (self.td / "gate.py").write_text(s[a:s.index("\nT1_VERIFY_PY\n", a)], encoding="utf-8")

    def _conn(self):
        import sqlite_vec as sv
        c = sqlite3.connect(str(self.db))
        c.enable_load_extension(True)
        sv.load(c)
        c.enable_load_extension(False)
        return c

    def _fill(self, table, dim, n, stamp):
        """n memories, each with a vector in `table` and the stamp `stamp` (a checksum, or None for no stamp)."""
        import memory_write as mw
        import struct
        c = self._conn()
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[%d])" % (table, dim))
        ids = []
        for i in range(n):
            body = "memory %s %d" % (stamp, i)
            cur = c.execute("INSERT INTO memories (content, category, importance) VALUES (?, 'project_knowledge', 0.5)", (body,))
            mid = cur.lastrowid
            ids.append(mid)
            c.execute("INSERT INTO %s (memory_id, embedding) VALUES (?, ?)" % table, (mid, struct.pack("%df" % dim, *([0.1] * dim))))
            if stamp:
                c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, dim, weights_hash, content_hash, "
                          "embedded_at, input_kind, organ) VALUES (?, ?, 'm', 'r', ?, ?, ?, '2026-01-01T00:00:00Z', 'text', 't')",
                          (mid, table, dim, stamp, mw.content_hash(body)))
        c.commit()
        c.close()
        return ids

    def _lock(self):
        import embedder_lock
        return embedder_lock.write_lock(self.td, "cpu", locked_by="test")

    def _gate(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("WIZARD_", "CLAUDE_"))}
        env.update({"WIZARD_LIBRARY_DB": str(self.db), "BRAIN_DIR": str(self.td), "ORGANS_DIR": str(ORGANS),
                    "WIZARD_EMBED_MODEL": "nomic"})
        return subprocess.run([sys.executable, str(self.td / "gate.py")], capture_output=True, text=True, env=env, timeout=120)

    def test_a_mixed_brain_prints_what_the_lock_covers_not_the_table(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 3, lock["weights_hash"])
        self._fill(lock["vec_table"], lock["dim"], 5, "f" * 64)
        p = self._gate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"\bvectors=3\b", p.stdout)
        self.assertNotRegex(p.stdout, r"\bvectors=8\b")
        self.assertRegex(p.stdout, r"\boutside_the_lock=5\b")

    def test_an_all_foreign_brain_is_not_verified(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 4, "f" * 64)
        p = self._gate()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("NOT VERIFIED", p.stderr)
        self.assertIn("NO_ROWS_FOR_LOCK", p.stderr)

    def test_legacy_rows_stay_searchable_and_are_counted_apart(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 2, lock["weights_hash"])
        self._fill(lock["vec_table"], lock["dim"], 3, "unresolved")
        p = self._gate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"\bvectors=2\b")
        self.assertRegex(p.stdout, r"\blegacy=3\b")

    def test_a_brain_of_only_legacy_rows_still_verifies(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 3, "unresolved")
        p = self._gate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"\blegacy=3\b")

    def _unreadable_sidecar(self):
        c = self._conn()
        c.execute("DROP TABLE embedding_provenance")
        c.commit()
        c.close()

    def test_an_unmeasurable_brain_with_an_empty_table_is_not_verified(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 0, None)
        self._unreadable_sidecar()
        p = self._gate()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("holds 0 vectors", p.stderr)

    def test_an_unmeasurable_brain_says_its_figure_is_rows(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 2, None)
        self._unreadable_sidecar()
        p = self._gate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"\bvectors=2\b")
        self.assertIn("basis=rows_not_stamps", p.stdout)

    def test_control_a_lockless_brain_counts_its_table_as_today(self):
        c = self._conn()
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        c.commit()
        c.close()
        self._fill("memory_embeddings_v2", 1024, 3, None)
        p = self._gate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"\bvectors=3\b")
        self.assertIn("table=memory_embeddings_v2", p.stdout)

    def test_control_a_clean_locked_brain_prints_no_extras(self):
        lock = self._lock()
        self._fill(lock["vec_table"], lock["dim"], 3, lock["weights_hash"])
        p = self._gate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"\bvectors=3\b")
        self.assertNotIn("legacy=", p.stdout)
        self.assertNotIn("outside_the_lock=", p.stdout)


class TheRunTimeManagerReadIsDeclared(unittest.TestCase):
    """A roster unit's own read of the manager is on the list a recorder log is graded against, and is never counted
    as the installer's."""

    def _rows(self):
        d = json.loads((ROOT / "install" / "write_set.json").read_text(encoding="utf-8"))
        return [v for v in d["verbs"] if v.get("when") == "run-time"]

    def test_the_rows_exist_and_name_who_makes_the_call(self):
        rt = self._rows()
        self.assertTrue(rt)
        for v in rt:
            self.assertEqual((v["effect"], v["expand"]), ("read-only", "armed_units"))
            self.assertIn("organs/organ_run_log.py", v["made_by"])

    def test_every_show_the_organ_makes_is_declared_with_its_properties(self):
        """Read from the organ, not from a list here: a third `_show` with no row goes red."""
        src = (ORGANS / "organ_run_log.py").read_text(encoding="utf-8")
        made = re.findall(r"_show\((f\.name|u\.service_name),\s*\[([^\]]*)\]", src)
        self.assertEqual(len(made), len(re.findall(r"= _show\(", src)), "a _show call this reader cannot parse")
        self.assertTrue(made)
        calls = [v["call"] for v in self._rows()]
        self.assertEqual(len(calls), len(made))
        for who, props in made:
            suffix = ".timer" if who == "f.name" else ".service"
            row = [c for c in calls if "wizard-library-{unit}" + suffix in c]
            self.assertEqual(len(row), 1, (who, calls))
            self.assertEqual(re.findall(r"-p(\w+)", row[0]), re.findall(r'"(\w+)"', props))

    def test_print_write_set_resolves_it_inactive_for_every_armed_unit(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home() / ".cache")) as home:
            r = subprocess.run(["bash", str(SH), "--print-write-set", "--with-crons", "--tier0-only"],
                               capture_output=True, text=True, timeout=120, env={**os.environ, "HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        out = json.loads(r.stdout)
        shows = [v for v in out["verbs"] if v["when"] == "run-time"]
        self.assertEqual(len(shows), len(self._rows()) * len(out["armed_units"]))
        self.assertTrue(shows)
        self.assertFalse(any(v["active"] for v in shows), "a run-time read counted as the installer's")
        self.assertFalse(any("{unit}" in v["call"] for v in shows))


if __name__ == "__main__":
    unittest.main()

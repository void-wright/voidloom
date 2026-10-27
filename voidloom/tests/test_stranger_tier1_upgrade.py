# -*- coding: utf-8 -*-
"""THE STRANGER TEST (2026-10-13): a friend's install, run cold, found six defects the author's host could not.

  B1   a brain born at Tier 0 (`--init-blank --tier0-only`, or a Tier-0 fallback with no python3-venv) declares no
       embedding tier and has no vector table. Re-running install.sh after venv was fixed built the venv, cached the
       models -- and printed "Tier 1 VERIFIED" and "ACTIVE" four lines after embed_backfill REFUSED
       (EmbedTierUnresolved, rc 3). Now Step C declares the tier `init` would have declared, only where the brain
       declares none, creates the missing vec tables, clears an earlier tier0_only (put back on failure), and the
       verification refuses a brain with no declared table and asks the `voidloom` facade which TIER answered.
  B2   the launcher exec'd `python3` from PATH, which never carries <brain>/venv/bin, so a venv Tier 1 was ignored
       ("No module named 'sqlite_vec'"). It now runs the interpreter install.sh recorded in <brain>/INSTALL_RECORD.
  #8   --force-cpu discarded --gpu / --display-gpu ("not used, nothing is recorded") and Step D then warned
       "else card 0" on the same 2-GPU host. The answers are recorded when given.
  #12  --force-cpu still installed PyPI's default (CUDA) torch: 5.8 GB, and on WSL2 a segfault on
       torch.cuda.is_available() under a no-device CUDA_VISIBLE_DEVICES. It now installs (or swaps to) the CPU build
       from the PyTorch CPU index, and says what pip will cost before it starts.
  #11  `voidloom hooks install` with no --platform: a default when exactly one platform's config dir exists; otherwise
       an error that says exactly what to type.
  #26  the session-start header said "(wizard-library brain)" -- the author's private brain name -- in every brain.

Every run is a scratch directory: fake interpreters, fake nvidia-smi, the package's blank seed brain. No model is
loaded, no network is used, no GPU is touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SH = ROOT / "install.sh"
SEED_DB = ROOT / "seed" / "claude_memory_blank.db"
LAUNCHER = ROOT / "voidloom" / "voidloom"


def _need_checkout(tc):
    if not SH.is_file() or not SEED_DB.is_file():
        tc.skipTest("NOT A CHECKOUT: %s has no install.sh / seed brain (install.sh ships, it is not laid)" % ROOT)


def _heredoc_after(anchor: str, nth: int = 0) -> str:
    """The body of the first quoted heredoc (<<'NAME' ... NAME) that starts after `anchor` in install.sh."""
    src = SH.read_text(encoding="utf-8")
    i = src.index(anchor)
    m = re.compile(r"<<'([A-Z0-9_]+)'[^\n]*\n").search(src, i)
    if m is None:
        raise AssertionError("no heredoc after %r" % anchor)
    end = src.index("\n%s\n" % m.group(1), m.end())
    return src[m.end():end + 1]


def _sqlite_vec_ok() -> bool:
    try:
        import sqlite_vec  # noqa: F401
        c = sqlite3.connect(":memory:")
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.close()
        return True
    except Exception:
        return False


def _vec_conn(db):
    import sqlite_vec
    c = sqlite3.connect(str(db))
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def _meta(db, key):
    c = sqlite3.connect(str(db))
    try:
        r = c.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    finally:
        c.close()
    return r[0] if r else None


def _install_sh_block(start, end):
    src = SH.read_text(encoding="utf-8")
    i = src.index(start)
    return src[i:src.index(end, i)]


def _engine_registry_tsv(pkg_root):
    """install.sh's engine_registry() -- THE ONE READER of EMBED_TIERS (iteration 5) -- run on pkg_root's engine."""
    fn = _install_sh_block("engine_registry() {", "\nEMBED_MODEL=\"$(resolve_embed_model)\"")
    p = subprocess.run(["bash", "-c", fn + '\nengine_registry "$0" "$1"', sys.executable,
                        str(Path(pkg_root) / "organs" / "claude_memory.py")], capture_output=True, text=True, timeout=60)
    return p.stdout if p.returncode == 0 else ""


def _install_tier_facts(pkg_root, model):
    """(EMBED_MODEL, EMBED_TABLE, EMBED_DIM, EMBED_REPO) as install.sh's tier-facts block derives them for `model`."""
    blk = _install_sh_block("# >>> tier facts", "# <<< tier facts")
    script = ('warn() { :; }\nresolve_embed_model() { echo "%s"; }\nPY_SYS=%s\nPKG_ROOT=%s\n%s\n'
              'printf "%%s|%%s|%%s|%%s\\n" "$EMBED_MODEL" "$EMBED_TABLE" "$EMBED_DIM" "$EMBED_REPO"\n'
              % (model, sys.executable, pkg_root, blk))
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout.strip(), p.stderr


class _Scratch(unittest.TestCase):
    def setUp(self):
        _need_checkout(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="stranger_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))

    def _tier0_brain(self, tier0_only=None) -> Path:
        """The package's blank seed brain IS a Tier-0-born brain: no active_embedding_* keys, no vec table."""
        db = self.tmp / "claude_memory.db"
        shutil.copy2(str(SEED_DB), str(db))
        c = sqlite3.connect(str(db))
        try:
            self.assertIsNone(c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone(),
                              "the seed brain is no longer a Tier-0-born brain; this test's premise moved")
            if tier0_only is not None:
                c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', ?)", (tier0_only,))
            c.commit()
        finally:
            c.close()
        return db

    def _exe(self, name: str, body: str) -> Path:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return p


# ===================================================================================================== B1
class TestB1ATier0BornBrainIsDeclaredAtTier1(_Scratch):
    """Step C's declare block, run exactly as install.sh carries it, against a Tier-0-born brain."""

    def setUp(self):
        super().setUp()
        if not _sqlite_vec_ok():
            self.skipTest("sqlite_vec is not importable by %s (the declare block runs in the Tier-1 venv)" % sys.executable)
        self.block = _heredoc_after("hd_t1_declare() {")

    def _declare(self, db, table="memory_embeddings", dim="768", repo="nomic-ai/nomic-embed-text-v1.5"):
        env = dict(os.environ, EMBED_TABLE=table, EMBED_DIM=dim, EMBED_REPO=repo,
                   ENGINE_REGISTRY=_engine_registry_tsv(ROOT))
        p = subprocess.run([sys.executable, "-", str(db)], input=self.block, capture_output=True, text=True,
                           env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout.strip().split("\t")

    def test_the_tier_is_declared_and_the_tables_exist(self):
        db = self._tier0_brain()
        out = self._declare(db)
        self.assertEqual(out[:3], ["OK", "declared", "memory_embeddings"], out)
        self.assertEqual(_meta(db, "active_embedding_table"), "memory_embeddings")
        self.assertEqual(_meta(db, "active_embedding_model"), "nomic-ai/nomic-embed-text-v1.5")
        self.assertEqual(_meta(db, "active_embedding_dim"), "768")
        c = _vec_conn(db)
        try:
            # the table the stranger's update died on ("no such table: memory_embeddings") is now there, and usable
            c.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()
        finally:
            c.close()
        # and the engine's own resolver now answers instead of refusing
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            import embed_resolver
            c = sqlite3.connect(str(db))
            try:
                self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings", 768))
            finally:
                c.close()
        finally:
            sys.path.remove(str(ROOT / "organs"))

    def test_a_gpu_tier_gets_its_own_table_and_the_nomic_fallback(self):
        db = self._tier0_brain()
        out = self._declare(db, "memory_embeddings_v3", "1024", "Qwen/Qwen3-Embedding-0.6B")
        self.assertEqual(out[1:4], ["declared", "memory_embeddings_v3", "memory_embeddings_v3,memory_embeddings"], out)
        self.assertEqual(_meta(db, "active_embedding_dim"), "1024")

    def test_a_declared_tier_is_never_repointed(self):
        db = self._tier0_brain()
        c = sqlite3.connect(str(db))
        c.execute("INSERT INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        c.commit()
        c.close()
        out = self._declare(db)            # this run resolved nomic; the brain says jina
        self.assertEqual(out[1:3], ["kept", "memory_embeddings_v2"], out)
        self.assertEqual(_meta(db, "active_embedding_table"), "memory_embeddings_v2")
        self.assertIn("memory_embeddings_v2", out[3])     # the missing declared table was created, at its own width

    def _with_vectors(self, db, table, dim, n=1):
        import struct
        c = _vec_conn(db)
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[%d])"
                  % (table, dim))
        for i in range(1, n + 1):
            c.execute("INSERT INTO %s (memory_id, embedding) VALUES (?, ?)" % table,
                      (i, struct.pack("%df" % dim, *([0.01 * i] * dim))))
        c.commit()
        c.close()

    @staticmethod
    def _metadata_rows(db):
        c = sqlite3.connect(str(db))
        try:
            return c.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
        finally:
            c.close()

    def test_a_fully_declared_live_shaped_brain_is_byte_identical_after_a_run_of_another_tier(self):
        # the shape of the author's live brain: v2 declared with model + dim, both tables present, vectors held
        db = self._tier0_brain()
        c = sqlite3.connect(str(db))
        for k, v in (("active_embedding_table", "memory_embeddings_v2"),
                     ("active_embedding_model", "jinaai/jina-embeddings-v5-text-small"),
                     ("active_embedding_dim", "1024")):
            c.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (k, v))
        c.commit()
        c.close()
        self._with_vectors(db, "memory_embeddings_v2", 1024, n=3)
        self._with_vectors(db, "memory_embeddings", 768, n=0)
        before = self._metadata_rows(db)
        out = self._declare(db)                                     # a nomic run (e.g. --force-cpu)
        self.assertEqual(out, ["OK", "kept", "memory_embeddings_v2", "-", "-"])
        self.assertEqual(self._metadata_rows(db), before, "a declared brain's metadata changed")

    def test_an_undeclared_brain_holding_vectors_in_one_table_adopts_that_table(self):
        db = self._tier0_brain()
        self._with_vectors(db, "memory_embeddings_v2", 1024, n=2)  # metadata lost, the vectors survive
        out = self._declare(db)                                     # this run resolved nomic
        self.assertEqual(out[:4], ["OK", "adopted", "memory_embeddings_v2", "memory_embeddings"], out)
        self.assertEqual(_meta(db, "active_embedding_table"), "memory_embeddings_v2")
        self.assertEqual(_meta(db, "active_embedding_model"), "jinaai/jina-embeddings-v5-text-small")
        self.assertEqual(_meta(db, "active_embedding_dim"), "1024")

    def test_an_undeclared_brain_with_vectors_in_two_other_tables_is_refused_untouched(self):
        db = self._tier0_brain()
        self._with_vectors(db, "memory_embeddings_v2", 1024)
        self._with_vectors(db, "memory_embeddings_v3", 1024)
        before = self._metadata_rows(db)
        out = self._declare(db)
        self.assertEqual(out[0], "ERROR", out)
        self.assertIn("owner's call", out[1])
        self.assertEqual(self._metadata_rows(db), before)

    def test_an_earlier_tier0_only_is_reported_and_cleared(self):
        db = self._tier0_brain(tier0_only="1")
        out = self._declare(db)
        self.assertEqual(out[4], "1", out)                 # the value install.sh puts back if Tier 1 does not verify
        self.assertEqual(_meta(db, "tier0_only"), "0")

    def test_the_install_puts_tier0_only_back_on_every_unverified_path(self):
        src = SH.read_text(encoding="utf-8")
        gate = src[src.index("# C.verify —"):src.index("# STEP D")]
        # four refusal arms (the tier undeclared, the device proof failed -- handoff round 1, L4: P6/P7 --, embedding
        # refused) + the failed verification: each restores before the hint
        self.assertEqual(gate.count("tier1_restore_t0\n                tier1_upgrade_hint"), 4, gate[-1500:])
        self.assertIn('elif [ "$T1_DEVICE_OK" -ne 1 ]; then', gate)
        self.assertIn('elif [ "$T1_EMBED_REFUSED" -eq 1 ]; then', gate)


class TestB1TheVerifyGateRefusesWhatIsNotTier1(_Scratch):
    """The C.verify block, run as install.sh carries it. Its recall goes through a FAKE `voidloom` package (and, for the
    pre-fix tree, a fake engine), so what is under test is the gate's reading, not a model."""

    def setUp(self):
        super().setUp()
        self.block = _heredoc_after("hd_t1_verify() {")
        self.brain = self.tmp / "brain"
        (self.brain / "organs").mkdir(parents=True)
        (self.brain / "voidloom").mkdir()

    def _fake_answer(self, tier, mode, n=2, degraded=None):
        data = {"status": "ok", "mode": mode, "tier": tier, "result_count": n, "results": [{"id": i} for i in range(n)]}
        if degraded:
            data["degraded"] = degraded
        (self.brain / "voidloom" / "__init__.py").write_text("", encoding="utf-8")
        (self.brain / "voidloom" / "__main__.py").write_text(
            "import json\nprint(json.dumps(%r))\n" % ({"status": "ok", "data": data},), encoding="utf-8")
        # the pre-fix gate asked the raw engine instead: same answer, top-level shape
        (self.brain / "organs" / "claude_memory.py").write_text(
            "import json\nprint(json.dumps(%r))\n" % (data,), encoding="utf-8")

    def _gate(self, db):
        env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), WIZARD_LIBRARY_HOME=str(self.brain), BRAIN_DIR=str(self.brain),
                   BRAIN_ORGANS=str(self.brain / "organs"), WIZARD_EMBED_MODEL="nomic")
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, "-"], input=self.block, capture_output=True, text=True, env=env,
                              timeout=120, cwd=str(self.tmp))

    def test_a_brain_with_no_declared_tier_is_NOT_VERIFIED_even_when_keyword_recall_answers(self):
        db = self._tier0_brain()
        self._fake_answer(0, "fts5-keyword")
        p = self._gate(db)
        self.assertNotEqual(p.returncode, 0, "the gate passed a brain that declares no tier: " + p.stdout)
        self.assertNotIn("PARTIAL", p.stdout)
        self.assertIn("NOT VERIFIED", p.stderr)

    def _declared_with_one_vector(self):
        if not _sqlite_vec_ok():
            self.skipTest("sqlite_vec is not importable by %s" % sys.executable)
        db = self._tier0_brain()
        c = _vec_conn(db)
        c.execute("INSERT INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings')")
        c.execute("CREATE VIRTUAL TABLE memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768])")
        import struct
        c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (struct.pack("768f", *([0.1] * 768)),))
        c.commit()
        c.close()
        return db

    def test_a_tier_0_answer_is_NOT_VERIFIED(self):
        db = self._declared_with_one_vector()
        self._fake_answer(0, "fts5-keyword", degraded={"tier1_error": "ModuleNotFoundError: No module named 'sqlite_vec'"})
        p = self._gate(db)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("answered at tier 0", p.stderr)
        self.assertIn("No module named 'sqlite_vec'", p.stderr)

    def test_a_tier_1_answer_over_a_declared_table_with_vectors_is_VERIFIED_and_says_so(self):
        db = self._declared_with_one_vector()
        self._fake_answer(1, "hybrid")
        p = self._gate(db)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("VERIFIED tier=1 mode=hybrid vectors=1 results=2 table=memory_embeddings", p.stdout)


# ===================================================================================================== B2
class TestB2TheLauncherRunsTheRecordedInterpreter(_Scratch):
    """The launcher, copied to <brain>/voidloom/voidloom (where install.sh lays it), with fake interpreters."""

    def setUp(self):
        super().setUp()
        if not LAUNCHER.is_file():
            self.skipTest("no launcher at %s" % LAUNCHER)
        self.brain = self.tmp / "brain"
        (self.brain / "voidloom").mkdir(parents=True)
        shutil.copy2(str(LAUNCHER), str(self.brain / "voidloom" / "voidloom"))
        self.venv_py = self._exe("brain/venv/bin/python3", "#!/bin/sh\necho RECORDED-INTERPRETER \"$@\"\n")
        self.path_bin = self.tmp / "pathbin"
        self._exe("pathbin/python3", "#!/bin/sh\necho PATH-PYTHON3 \"$@\"\n")

    def _run(self):
        env = {"PATH": "%s:/usr/bin:/bin" % self.path_bin, "HOME": str(self.tmp)}
        return subprocess.run(["bash", str(self.brain / "voidloom" / "voidloom"), "doctor", "--json"],
                              capture_output=True, text=True, env=env, timeout=60, cwd=str(self.tmp))

    def _record(self, interp):
        (self.brain / "INSTALL_RECORD").write_text(
            "# <brain>/INSTALL_RECORD\nschema=1\nshape=venv\ninterpreter=%s\ntier1=active\n" % interp, encoding="utf-8")

    def test_the_recorded_venv_interpreter_runs_the_verb(self):
        self._record(self.venv_py)
        p = self._run()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith("RECORDED-INTERPRETER -c "), p.stdout)
        self.assertTrue(p.stdout.rstrip().endswith("doctor --json"), p.stdout)

    def test_no_record_falls_back_to_python3_on_PATH(self):
        p = self._run()
        self.assertTrue(p.stdout.startswith("PATH-PYTHON3 -c "), p.stdout + p.stderr)

    def test_a_record_naming_no_executable_falls_back(self):
        self._record(self.tmp / "gone" / "python3")
        self.assertTrue(self._run().stdout.startswith("PATH-PYTHON3 "))
        self._record("relative/python3")
        self.assertTrue(self._run().stdout.startswith("PATH-PYTHON3 "))


# ===================================================================================================== #8
class TestForceCpuStillRecordsTheOwnersGpuAnswers(_Scratch):
    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("bash + fake nvidia-smi")
        from voidloom.tests.test_tier1_ambient import _fake_nvidia_smi, BEGIN, END
        self.BEGIN, self.END = BEGIN, END
        self.home = self.tmp / "home"
        (self.home / "tmp").mkdir(parents=True)
        self.env_file = self.home / ".voidloom" / "wizard-library-env"
        fake = self.tmp / "fake_driver"
        _fake_nvidia_smi(fake, {"0": "Enabled", "1": "Disabled"})
        self.path = "%s:/usr/local/bin:/usr/bin:/bin" % fake

    def _install(self, *args):
        env = {"PATH": self.path, "HOME": str(self.home), "WIZARD_CLAUDE_HOME": str(self.home / ".claude"),
               "WIZARD_ENV_FILE": str(self.env_file), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_CACHE_HOME": str(self.home / ".cache"), "TMPDIR": str(self.home / "tmp"),
               "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_EMBED_MODEL": "nomic"}
        return subprocess.run(["bash", str(SH)] + list(args), capture_output=True, text=True, env=env, timeout=900,
                              cwd=str(self.tmp))

    def _outside(self):
        t = self.env_file.read_text(encoding="utf-8")
        return t[:t.index(self.BEGIN)] + t[t.index(self.END) + len(self.END):]

    def _units_card(self):
        p = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "env_file.py"), "--file", str(self.env_file),
                            "effective-pin"], capture_output=True, text=True, timeout=60)
        return p.stdout.strip() if p.returncode == 0 else "<rc %d>" % p.returncode

    def test_the_answers_are_recorded_and_no_card_0_fallback_is_warned(self):
        p = self._install("--env-only", "--force-cpu", "--gpu", "1", "--display-gpu", "0")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertNotIn("so they are not used and nothing is recorded", p.stdout)
        self.assertIn("(from --gpu 1 --display-gpu 0)", self._outside())
        self.assertIn("\nWIZARD_DISPLAY_INDEX=0\n", self._outside())
        self.assertEqual(self._units_card(), "1")
        self.assertNotIn("else card 0 -- which may drive a display", p.stdout + p.stderr)

    def test_without_flags_force_cpu_asks_nothing_and_records_nothing(self):
        p = self._install("--env-only", "--force-cpu")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertNotIn("WIZARD_CUDA_DEVICE=", self._outside())


# ===================================================================================================== #12
class TestForceCpuInstallsTheCpuTorch(_Scratch):
    """install.sh's torch functions, extracted verbatim and run against a FAKE venv python that records every pip
    call and plays the torch build as a state file. No pip runs, nothing is downloaded."""

    FAKE = r'''#!/bin/sh
# fake venv python: $STATE holds the torch build ("absent" | "cuda <v>" | "cpu <v>"); every call is logged to $LOG
echo "$*" >> "$LOG"
if [ "$1" = "-" ]; then
    script="$(cat)"
    case "$script" in
        *packaging.requirements*) echo "cuda-bindings nvidia-cublas triton" ;;
        *PackageNotFoundError*) cat "$STATE" ;;
    esac
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
    case "$*" in *download.pytorch.org/whl/cpu*torch==*+cpu*) v="${*##*torch==}"; echo "cpu $v" > "$STATE" ;;
                 *download.pytorch.org/whl/cpu*torch*) echo "cpu 9.9.9+cpu" > "$STATE" ;; esac
    exit 0
fi
exit 0
'''

    def setUp(self):
        super().setUp()
        src = SH.read_text(encoding="utf-8")
        start = src.index("TORCH_CPU_INDEX=")
        end = src.index("\n}\n", src.index("pip_expect_line() {")) + 3
        self.functions = src[start:end]
        self.py = self._exe("venv/bin/python3", self.FAKE)
        self.state = self.tmp / "state"
        self.log = self.tmp / "log"

    def _run(self, state, call="cpu_torch_into_venv"):
        self.state.write_text(state + "\n", encoding="utf-8")
        script = ('say() { echo "SAY $*"; }\nwarn() { echo "WARN $*"; }\nOS=Linux\nFORCE_CPU=1\n'
                  'VENV_PY=%s\nVENV_DIR=%s\n%s\n%s\n' % (self.py, self.tmp / "venv", self.functions, call))
        env = {"PATH": "/usr/bin:/bin", "STATE": str(self.state), "LOG": str(self.log), "HOME": str(self.tmp)}
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
        log = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        return p, log

    def test_a_fresh_venv_gets_torch_from_the_cpu_index(self):
        p, log = self._run("absent")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("-m pip install --index-url https://download.pytorch.org/whl/cpu torch", log)
        self.assertIn("SAY   torch in the venv: the CPU build", p.stdout)

    def test_a_cuda_venv_is_swapped_to_the_same_version_cpu_build_and_the_cuda_wheels_go(self):
        p, log = self._run("cuda 2.14.1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--force-reinstall --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu", log)
        self.assertIn("-m pip uninstall -y cuda-bindings nvidia-cublas triton", log)
        self.assertIn("SAY   torch in the venv: the CPU build (2.14.1+cpu)", p.stdout)

    def test_a_cpu_venv_is_left_alone(self):
        p, log = self._run("cpu 2.14.1+cpu")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn("pip install", log)

    def test_the_cost_is_said_before_pip_starts(self):
        p, _ = self._run("absent", call="pip_expect_line")
        self.assertIn("CPU build of torch", p.stdout)
        self.assertRegex(p.stdout, r"about [0-9.]+ GB")
        src = SH.read_text(encoding="utf-8")
        step_c = src[src.index("STEP C — TIER 1 ATTEMPT"):]
        self.assertLess(step_c.index('say "  $(pip_expect_line)"'), step_c.index("-m pip install --upgrade pip"))
        # handoff round 1 (L4): torch from the plan's channel (torch_into_venv -> cpu_torch_into_venv for the CPU) BEFORE
        # the requirements
        self.assertLess(step_c.index("&& torch_into_venv \\"), step_c.index('-m pip install "${PIP_ARGS[@]}"'))
        self.assertIn("cpu)  [ \"$OS\" = \"Darwin\" ] && return 0; cpu_torch_into_venv; return $? ;;", src)


# ===================================================================================================== #11
class TestHooksInstallPlatformDefault(_Scratch):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "organs"))
        self.addCleanup(lambda: sys.path.remove(str(ROOT / "organs")))
        import voidloom_hooks_status as hs
        self.hs = hs
        self.claude = self.tmp / "claude_cfg"
        self.codex = self.tmp / "codex_home"
        self.brain = self.tmp / "brain"
        self.brain.mkdir()
        self._saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        self.addCleanup(self._restore)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        os.environ["CODEX_HOME"] = str(self.codex)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _install(self):
        return self.hs.run(["install", "--home", str(self.brain), "--cwd", str(self.tmp)])

    def test_only_claude_exists_so_claude_is_the_default_and_it_says_so(self):
        self.claude.mkdir()
        env = self._install()
        self.assertNotEqual(env["code"], "platform_required", env)
        self.assertEqual(env["data"]["platform"], "claude-code")
        self.assertIn("platform: claude -- the only platform config found", env["data"]["platform_defaulted"])
        self.assertFalse(env["data"]["executed"])          # no --yes: still only a plan

    def test_both_exist_so_the_error_says_exactly_what_to_type(self):
        self.claude.mkdir()
        self.codex.mkdir()
        env = self._install()
        self.assertEqual(env["code"], "platform_required")
        self.assertEqual(env["data"]["commands"], ["voidloom hooks install --platform claude",
                                                   "voidloom hooks install --platform codex"])
        human = env[self.hs.HUMAN_KEY]
        self.assertIn("voidloom hooks install --platform claude", human)
        self.assertIn("voidloom hooks install --platform codex", human)
        self.assertIn("add --yes to write it", human)

    def test_neither_exists_is_an_error_too(self):
        env = self._install()
        self.assertEqual(env["code"], "platform_required")
        self.assertIn("neither", env["data"]["message"])


# ===================================================================================================== #26
class TestTheHeaderIsNotTheAuthorsBrainName(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "organs"))
        self.addCleanup(lambda: sys.path.remove(str(ROOT / "organs")))

    def test_both_headers_are_neutral_and_carry_the_canary_marker(self):
        import voidloom_persona as vp
        import voidloom_canary as vc
        for line in (vp.PERSONA_NEUTRAL_HEADER, vp.persona_header("ASH")):
            self.assertNotIn("wizard-library", line)
            self.assertIn(vc.PREAMBLE_MARKER, line)
        self.assertEqual(vp.persona_header("ASH"), "=== ASH SESSION START (Voidloom memory brain) ===")

    def test_the_canary_still_recognises_transcripts_from_older_installs(self):
        import voidloom_canary as vc
        self.assertTrue(vc.carries_preamble_marker("=== MILAMBER SESSION START (wizard-library brain) ==="))
        self.assertTrue(vc.carries_preamble_marker("=== SESSION START (Voidloom memory brain) ==="))
        self.assertFalse(vc.carries_preamble_marker("an unrelated transcript line"))

    def test_no_emitter_source_types_the_private_name(self):
        for rel in ("organs/voidloom_preamble.py", "organs/voidloom_persona.py", "hooks/session_start_inject.py"):
            for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if "wizard-library brain" in line and "LEGACY" not in line and not line.lstrip().startswith("#"):
                    self.fail("%s:%d still emits the private brain name: %s" % (rel, n, line.strip()))


# ===================================================================================================== iteration 2: -1
class TestMinusOneMeansNoDevice(_Scratch):
    """CUDA_VISIBLE_DEVICES=-1 means NO device, by the ONE predicate cuda_selects_a_device uses -- never "invalid,
    fall through to the declared card / card 0". Minimal pairs: '-1' vs '' vs '1', with and without a declaring env
    file that names card 1. Each case is a fresh interpreter (the resolver caches per process); nothing initialises
    CUDA (no torch is imported)."""

    PROBE = ("import os, sys, json; sys.path.insert(0, sys.argv[1]); import voidloom_device as d; "
             "v, b = d.apply_cuda_pin(); print(json.dumps([v, b, os.environ.get('CUDA_VISIBLE_DEVICES'), "
             "d.cuda_selects_a_device()[0]]))")

    def _resolve(self, cvd, env_file_declares):
        ef = self.tmp / ("declares" if env_file_declares else "absent") / "wizard-library-env"
        if env_file_declares:
            ef.parent.mkdir(parents=True, exist_ok=True)
            ef.write_text("WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp), "WIZARD_ENV_FILE": str(ef),
               "WIZARD_CONFIG_PATH": str(self.tmp / "no-config.json"), "CUDA_VISIBLE_DEVICES": cvd}
        p = subprocess.run([sys.executable, "-c", self.PROBE, str(ROOT / "organs")], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_minus_one_is_no_device_with_and_without_a_declaring_file(self):
        for declares in (True, False):
            for cvd in ("-1", "none", "NoDevFiles", "-1,1"):
                value, basis, after, selects = self._resolve(cvd, declares)
                self.assertIsNone(value, (cvd, declares, basis))
                self.assertEqual(basis, "ambient-no-device", (cvd, declares))
                self.assertEqual(after, cvd, "the variable must stay exactly as the operator set it")
                self.assertFalse(selects)

    def test_the_minimal_pair_minus_one_empty_one(self):
        for declares in (True, False):
            self.assertEqual(self._resolve("", declares)[:3], [None, "ambient-empty", ""])
            self.assertEqual(self._resolve("-1", declares)[:3], [None, "ambient-no-device", "-1"])
            self.assertEqual(self._resolve("1", declares)[:3], ["1", "ambient", "1"])


# ===================================================================================================== iteration 2: B2 class
class TestEveryLaunchSiteRunsTheRecordedInterpreter(_Scratch):
    """The B2 class beyond the CLI launcher: the session hook, the cron generator and the Windows launcher. The hook's
    registered command stays BYTE-IDENTICAL (Codex pins trust to its hash); the hook re-executes itself under
    INSTALL_RECORD's interpreter instead."""

    FAKE_PY = "#!/bin/sh\nif [ \"$1\" = \"-I\" ]; then exit %d; fi\necho RECORDED \"$@\"\n"

    def _hook_probe(self, record_interp=None, probe_rc=0):
        import ast
        src = (ROOT / "hooks" / "session_start_inject.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == "_reexec_under_recorded_interpreter")
        body = ast.get_source_segment(src, fn)
        brain = self.tmp / "brain"
        (brain / "hooks").mkdir(parents=True, exist_ok=True)
        probe = brain / "hooks" / "session_start_inject.py"
        probe.write_text("import os, sys\n" + body + "\n_reexec_under_recorded_interpreter()\n"
                         "print('RAN', os.path.realpath(sys.executable), os.environ.get('VOIDLOOM_HOOK_REEXEC'))\n",
                         encoding="utf-8")
        if record_interp is not None:
            (brain / "INSTALL_RECORD").write_text("schema=1\nshape=venv\ninterpreter=%s\n" % record_interp,
                                                  encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp)}
        return subprocess.run([sys.executable, str(probe), "--from-platform"], input="{}", capture_output=True,
                              text=True, env=env, timeout=60), probe

    def test_the_hook_reexecs_under_the_recorded_interpreter_with_the_same_argv(self):
        venv_py = self._exe("brain/venv/bin/python3", self.FAKE_PY % 0)
        p, probe = self._hook_probe(venv_py)
        self.assertEqual(p.stdout.strip(), "RECORDED %s --from-platform" % probe.resolve(), p.stderr)

    def test_a_venv_whose_python_is_a_symlink_to_this_interpreter_is_still_reexecuted(self):
        # the real venv shape: bin/python3 -> the base interpreter, plus pyvenv.cfg. Same realpath, different
        # environment -- the hook must re-execute into it (measured on a fresh install: it did not, before).
        venv = self.tmp / "brain" / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = %s\ninclude-system-site-packages = false\n"
                                         % os.path.dirname(os.path.realpath(sys.executable)), encoding="utf-8")
        os.symlink(os.path.realpath(sys.executable), str(venv / "bin" / "python3"))
        p, _ = self._hook_probe(str(venv / "bin" / "python3"))
        self.assertTrue(p.stdout.startswith("RAN ") and p.stdout.rstrip().endswith(" 1"),
                        "the hook did not re-execute into the venv: " + p.stdout + p.stderr)

    def test_the_hook_runs_on_when_the_record_is_this_interpreter_absent_or_broken(self):
        p, _ = self._hook_probe(os.path.realpath(sys.executable))       # the author's ambient host: no re-exec
        self.assertTrue(p.stdout.startswith("RAN %s None" % os.path.realpath(sys.executable)), p.stdout + p.stderr)
        shutil.rmtree(str(self.tmp / "brain"))
        p, _ = self._hook_probe(None)
        self.assertTrue(p.stdout.startswith("RAN "), p.stdout + p.stderr)
        shutil.rmtree(str(self.tmp / "brain"))
        broken = self._exe("broken/python3", self.FAKE_PY % 1)          # starts badly: never exec into it
        p, _ = self._hook_probe(broken)
        self.assertTrue(p.stdout.startswith("RAN "), p.stdout + p.stderr)

    def test_the_registered_hook_command_is_byte_identical(self):
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            import voidloom_hooks_status as hs
            if os.name != "nt":
                self.assertEqual(hs.hook_command(str(self.tmp)), "python3 %s" % (self.tmp / "hooks" / hs.HOOK_BASENAME))
        finally:
            sys.path.remove(str(ROOT / "organs"))

    def _cron_python(self, brain, wizard_python_bin=None):
        src = (ROOT / "cron" / "platform-detection.sh").read_text(encoding="utf-8")
        start = src.index("# Find the interpreter the units run")
        end = src.index("\n", src.index('[ -n "$PYTHON3" ] ||')) + 1
        script = 'MODULE_DIR=%s\n%s\necho "$PYTHON3"\n' % (brain, src[start:end])
        env = {"PATH": "%s:/usr/bin:/bin" % (self.tmp / "pathbin"), "HOME": str(self.tmp)}
        if wizard_python_bin:
            env["WIZARD_PYTHON_BIN"] = wizard_python_bin
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
        self._cron_err = p.stderr
        return p.stdout.strip()

    def test_the_cron_generator_picks_record_then_venv_then_path(self):
        brain = self.tmp / "cbrain"
        brain.mkdir()
        path_py = self._exe("pathbin/python3", "#!/bin/sh\n")
        self.assertEqual(self._cron_python(brain), str(path_py))
        explicit = self._exe("opt_py/bin/python3", "#!/bin/sh\nexit 0\n")      # runs `-c 'import sys'`
        self.assertEqual(self._cron_python(brain, str(explicit)), str(explicit))
        venv_py = self._exe("cbrain/venv/bin/python3", "#!/bin/sh\n")
        self.assertEqual(self._cron_python(brain), str(venv_py))
        rec_py = self._exe("ambient/bin/python3", "#!/bin/sh\n")
        (brain / "INSTALL_RECORD").write_text("schema=1\ninterpreter=%s\n" % rec_py, encoding="utf-8")
        self.assertEqual(self._cron_python(brain), str(rec_py))
        (brain / "INSTALL_RECORD").write_text("schema=1\ninterpreter=relative/python3\n", encoding="utf-8")
        self.assertEqual(self._cron_python(brain), str(venv_py))

    def test_an_explicit_wizard_python_bin_outranks_the_record_and_the_venv(self):
        # iteration 3: the operator's explicit override ranks FIRST
        brain = self.tmp / "cbrain"
        brain.mkdir()
        self._exe("cbrain/venv/bin/python3", "#!/bin/sh\n")
        rec_py = self._exe("ambient/bin/python3", "#!/bin/sh\n")
        (brain / "INSTALL_RECORD").write_text("schema=1\ninterpreter=%s\n" % rec_py, encoding="utf-8")
        explicit = self._exe("explicit/bin/python3", "#!/bin/sh\nexit 0\n")
        self.assertEqual(self._cron_python(brain, str(explicit)), str(explicit))
        self.assertEqual(self._cron_python(brain), str(rec_py))

    def test_an_invalid_wizard_python_bin_is_named_and_falls_through_to_the_record(self):
        # iteration 4: validated -- exists, executable, runs `-c 'import sys'` -- else named on stderr, and the record wins
        brain = self.tmp / "cbrain"
        brain.mkdir()
        rec_py = self._exe("ambient/bin/python3", "#!/bin/sh\n")
        (brain / "INSTALL_RECORD").write_text("schema=1\ninterpreter=%s\n" % rec_py, encoding="utf-8")
        noexec = self.tmp / "noexec_python3"
        noexec.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        broken = self._exe("broken/bin/python3", "#!/bin/sh\nexit 1\n")
        for bad, why in ((str(self.tmp / "missing" / "python3"), "no such file"), ("no-such-python-xyz", "no such file"),
                         (str(noexec), "not executable"), (str(broken), "does not run")):
            self.assertEqual(self._cron_python(brain, bad), str(rec_py), bad)
            self.assertIn("WIZARD_PYTHON_BIN=%s REJECTED: %s" % (bad, why), self._cron_err, bad)
        named = self._exe("pathbin/fakepy", "#!/bin/sh\nexit 0\n")       # a bare name on PATH resolves
        self.assertEqual(self._cron_python(brain, "fakepy"), str(named))
        self.assertEqual(self._cron_err, "")

    def test_the_windows_launcher_reads_the_record_then_the_venv_then_path(self):
        # STATIC (no cmd.exe here): the order of the three sources, and the exit code read on its own line.
        lines = (ROOT / "voidloom" / "voidloom.cmd").read_text(encoding="utf-8").splitlines()
        code = [l for l in lines if l.strip() and not l.lower().lstrip().startswith("rem")]
        i_rec = next(i for i, l in enumerate(code) if "INSTALL_RECORD" in l and '"interpreter"' in l)
        i_venv = next(i for i, l in enumerate(code) if r"venv\Scripts\python.exe" in l)
        i_call = next(i for i, l in enumerate(code) if l.strip() == '"%VL_PY%" -m voidloom %*')
        i_path = next(i for i, l in enumerate(code) if l.startswith("where python"))
        self.assertLess(i_rec, i_venv)
        self.assertLess(i_venv, i_call)
        self.assertLess(i_call, i_path)
        self.assertEqual(code[i_call + 1].strip(), "exit /b %ERRORLEVEL%")
        self.assertFalse(any("%ERRORLEVEL%" in l and "-m voidloom" in l for l in code),
                         "ERRORLEVEL on the call's own line expands before the call runs")


# ===================================================================================================== iteration 2: empty settings
class TestAnEmptySettingsFileIsAnEmptyObject(_Scratch):
    """A 0-byte or whitespace-only Claude settings file is {} (Claude Code reads it so) and says so in a note; a
    truncated or invalid body stays an error. Before: json.loads('') raised, doctor warned "settings file(s) do not
    parse" and brain_healthy went false over a host whose hooks were fine."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "organs"))
        self.addCleanup(lambda: sys.path.remove(str(ROOT / "organs")))
        import voidloom_hooks_status as hs
        self.hs = hs

    def _probe(self, body):
        f = self.tmp / ("s_%d.json" % len(list(self.tmp.iterdir())))
        f.write_text(body, encoding="utf-8")
        return self.hs.probe_claude_settings([str(f)], str(self.tmp))["sources"][0]

    def test_empty_and_whitespace_are_objects_with_a_note(self):
        for body in ("", "  \n\t\n"):
            e = self._probe(body)
            self.assertNotIn("error", e, repr(body))
            self.assertIn("empty settings file treated as {}", e.get("note", ""), repr(body))

    def test_truncated_or_invalid_json_stays_an_error(self):
        for body in ('{"hooks": {"SessionStart": [', "not json", "{}x"):
            e = self._probe(body)
            self.assertIn("error", e, repr(body))
            self.assertNotIn("note", e)

    def test_doctor_does_not_warn_over_an_empty_settings_local(self):
        home = self.tmp / "home"
        cfg = home / ".claude"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text("{}\n", encoding="utf-8")
        (cfg / "settings.local.json").write_bytes(b"")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONPATH": str(ROOT),
               "WIZARD_LIBRARY_HOME": str(cfg / "memory"), "CUDA_VISIBLE_DEVICES": ""}
        p = subprocess.run([sys.executable, "-m", "voidloom", "doctor", "--json"], capture_output=True, text=True,
                           env=env, timeout=300, cwd=str(home))
        d = json.loads(p.stdout[p.stdout.index("{"):])["data"]
        self.assertFalse([w for w in d.get("warnings") or [] if "do not parse" in w], d.get("warnings"))
        files = d["hooks"]["platforms"]["claude-code"].get("config_files") or []
        local = [f for f in files if str(f.get("path", "")).endswith("settings.local.json")]
        self.assertTrue(local and all(f.get("parse") == "ok" for f in local), files)
        self.assertTrue(any("treated as {}" in (f.get("note") or "") for f in local), files)


# ===================================================================================================== iteration 3: CUDA's reading
class TestCudaVisibleDevicesIsReadTheWayCudaReadsIt(_Scratch):
    """The list is read up to its first invalid entry; GPU- UUIDs are honoured, resolved to a card through nvidia-smi,
    and refused (no device, never card 0) when they cannot be resolved or name a display card. Fresh process per case,
    a FAKE nvidia-smi (card 0 drives the display, card 1 does not), with and without a declaring env file."""

    SMI = ("#!/bin/sh\ncase \"$*\" in\n  *index,uuid,display_active*) printf '0, GPU-00000000-monitor-card, Enabled\\n"
           "1, GPU-11111111-compute-card, Disabled\\n' ;;\n  *--query-gpu=index*) printf '0\\n1\\n' ;;\n  *) exit 0 ;;\nesac\n")
    PROBE = ("import os, sys, json; sys.path.insert(0, sys.argv[1]); import voidloom_device as d\n"
             "if sys.argv[2] == 'nosmi': d.NVIDIA_SMI_DIRS = ()\n"
             "v, b = d.apply_cuda_pin(); print(json.dumps([v, b, os.environ.get('CUDA_VISIBLE_DEVICES')]))")

    def _resolve(self, cvd, env_file=None, smi=True):
        fake = self.tmp / "fakesmi"
        if not (fake / "nvidia-smi").exists():
            self._exe("fakesmi/nvidia-smi", self.SMI)
        ef = self.tmp / ("ef_%d" % len(list(self.tmp.iterdir()))) / "wizard-library-env"
        if env_file is not None:
            ef.parent.mkdir(parents=True, exist_ok=True)
            ef.write_text(env_file, encoding="utf-8")
        env = {"PATH": ("%s:/usr/bin:/bin" % fake) if smi else "/usr/bin:/bin", "HOME": str(self.tmp),
               "WIZARD_ENV_FILE": str(ef), "WIZARD_CONFIG_PATH": str(self.tmp / "no-config.json"),
               "CUDA_VISIBLE_DEVICES": cvd}
        p = subprocess.run([sys.executable, "-c", self.PROBE, str(ROOT / "organs"), "smi" if smi else "nosmi"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    FILES = (None, "WIZARD_CUDA_DEVICE=1\n")

    def test_a_list_is_read_up_to_its_first_invalid_entry(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("1,-1", ef), ["1,-1", "ambient", "1,-1"], ef)
            self.assertEqual(self._resolve("-1,1", ef), [None, "ambient-no-device", "-1,1"], ef)
            self.assertEqual(self._resolve("1", ef), ["1", "ambient", "1"], ef)
            self.assertEqual(self._resolve("-1", ef), [None, "ambient-no-device", "-1"], ef)
            self.assertEqual(self._resolve("", ef), [None, "ambient-empty", ""], ef)

    def test_a_uuid_is_honoured_as_set_and_resolved_to_its_card(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("GPU-11111111-compute-card", ef),
                             ["GPU-11111111-compute-card", "ambient-uuid:1", "GPU-11111111-compute-card"], ef)
            self.assertEqual(self._resolve("GPU-1111", ef), ["GPU-1111", "ambient-uuid:1", "GPU-1111"], ef)

    def test_a_display_card_uuid_is_refused_to_no_device_never_card_0(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("GPU-00000000-monitor-card", ef), ["", "ambient-display-refused:0", ""])
        # the owner's declared display card is refused too, even where the driver reports it Disabled
        got = self._resolve("GPU-11111111-compute-card", "WIZARD_DISPLAY_INDEX=1\nWIZARD_CUDA_DEVICE=0\n")
        self.assertEqual(got, ["", "ambient-display-refused:1", ""])

    def test_an_unresolvable_uuid_is_refused_never_card_0(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("GPU-deadbeef", ef), ["", "ambient-uuid-unresolved-refused", ""], ef)
            # iteration 4: with no nvidia-smi nothing can be told apart -- honoured as set, as an index would be
            self.assertEqual(self._resolve("GPU-11111111-compute-card", ef, smi=False),
                             ["GPU-11111111-compute-card", "ambient-unchecked:no nvidia-smi", "GPU-11111111-compute-card"], ef)
            self.assertEqual(self._resolve("MIG-1234", ef), ["", "ambient-uuid-unresolved-refused", ""], ef)

    def test_nothing_the_caller_set_becomes_card_0(self):
        for cvd in ("1,-1", "-1,1", "GPU-abc", "GPU-11111111-compute-card", "MIG-1", "none", "1 # card"):
            for ef in self.FILES:
                v, b, after = self._resolve(cvd, ef)
                self.assertNotEqual(after, "0", (cvd, ef, b))
                self.assertNotEqual(v, "0", (cvd, ef, b))


# ===================================================================================================== iteration 3: WIZARD_CUDA_DEVICE
class TestNoGpuSpellingsAreNeverWrittenIntoWizardCudaDevice(_Scratch):
    """'-1' / 'none' are refused, by name, at every place the product WRITES WIZARD_CUDA_DEVICE (the --gpu flag, the
    terminal answer, the one env-file writer), and doctor FLAGS one written by hand. How consumers read them is NOT
    changed here (an open decision)."""

    NOGPU = "WIZARD_CUDA_DEVICE takes a card index only"

    def _home_env(self):
        home = self.tmp / "home"
        (home / "tmp").mkdir(parents=True, exist_ok=True)
        return home, {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
                      "WIZARD_CLAUDE_HOME": str(home / ".claude"),
                      "WIZARD_ENV_FILE": str(home / ".voidloom" / "wizard-library-env"),
                      "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
                      "TMPDIR": str(home / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_EMBED_MODEL": "nomic"}

    def test_the_gpu_flag_refuses_minus_one_and_none_by_name(self):
        home, env = self._home_env()
        for v in ("-1", "none", "NONE"):
            p = subprocess.run(["bash", str(SH), "--env-only", "--gpu", v], capture_output=True, text=True, env=env,
                               timeout=300, cwd=str(self.tmp))
            self.assertEqual(p.returncode, 2, (v, p.stderr[-600:]))
            self.assertIn("--gpu %s REFUSED: %s" % (v, self.NOGPU), p.stderr)
            self.assertIn("--force-cpu", p.stderr)
            self.assertFalse((home / ".voidloom" / "wizard-library-env").exists(), "a refused run wrote the env file")

    def test_the_terminal_answer_check_refuses_them_by_name(self):
        src = SH.read_text(encoding="utf-8")
        i = src.index("g1_check_work() {")
        fn = src[i:src.index("\n}\n", i) + 3]
        for v in ("-1", "none"):
            script = "g1_listed() { return 1; }\ng1_indices() { echo '0, 1'; }\nask_say() { echo \"$*\"; }\n%s\ng1_check_work %s\n" % (fn, v)
            p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 1)
            self.assertIn(self.NOGPU, p.stdout)

    def test_the_one_env_file_writer_refuses_them_by_name(self):
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import importlib
            import env_file
            importlib.reload(env_file)
            for v in ("-1", "none"):
                with self.assertRaises(env_file.Refusal) as cm:
                    env_file.declare(None, [("WIZARD_CUDA_DEVICE", v, "test")], "# >>> b >>>", "# <<< e <<<")
                self.assertIn("is refused: this key takes a card index only", str(cm.exception))
            img = env_file.declare(None, [("WIZARD_CUDA_DEVICE", "1", "test")], "# >>> b >>>", "# <<< e <<<")
            self.assertIn(b"WIZARD_CUDA_DEVICE=1", img[0])
        finally:
            sys.path.remove(str(ROOT / "install"))

    def _doctor(self, env_file_text):
        home, env = self._home_env()
        ef = home / ".voidloom" / "wizard-library-env"
        ef.parent.mkdir(parents=True, exist_ok=True)
        ef.write_text(env_file_text, encoding="utf-8")
        env.update({"PYTHONPATH": str(ROOT), "WIZARD_LIBRARY_HOME": str(ROOT), "CUDA_VISIBLE_DEVICES": ""})
        p = subprocess.run([sys.executable, "-m", "voidloom", "doctor", "--json"], capture_output=True, text=True,
                           env=env, timeout=300, cwd=str(home))
        return json.loads(p.stdout[p.stdout.index("{"):])["data"]

    def test_doctor_reports_a_hand_written_minus_one_as_cpu_only_not_a_warning(self):
        # iteration 5 (Drew's ruling, vl_gpu_minus1_ruling): -1 / none means no GPU -- informational, never a warning
        for v in ("-1", "none"):
            d = self._doctor("WIZARD_CUDA_DEVICE=%s\n" % v)
            row = d["checks"]["gpu_declaration"]
            self.assertEqual([f["value"] for f in row["cpu_only"]], [v])
            self.assertIn("CPU only", row["cpu_only"][0]["note"])
            self.assertFalse(any("WIZARD_CUDA_DEVICE" in w for w in d["warnings"]), d["warnings"])
        d = self._doctor("WIZARD_CUDA_DEVICE=1\n")
        self.assertEqual(d["checks"]["gpu_declaration"]["cpu_only"], [])


# ===================================================================================================== iteration 3: install.ps1 record
class TestWindowsInstallWritesTheRecordInInstallShFormat(_Scratch):
    """STATIC checks (the execution half -- powershell.exe and cmd.exe through WSL interop -- is
    TestInstallPs1TierFactsComeFromTheEngine; iteration 3 wrongly said no PowerShell was reachable here): install.ps1
    writes <brain>/INSTALL_RECORD with install.sh's keys
    (a subset of them), interpreter= the venv's python.exe only when Tier 1 VERIFIED, UTF-8 without a BOM, LF line ends,
    after STEP C and before INSTALL COMPLETE; and a record in that exact shape is read by the launch sites' parsers."""

    PS1 = ROOT / "install" / "install.ps1"

    def _fn(self):
        src = self.PS1.read_text(encoding="utf-8")
        i = src.index("function Write-InstallRecord {")
        return src, src[i:src.index("\nStep \"INSTALL RECORD\"", i)]

    def test_the_record_is_written_after_tier_1_and_before_complete(self):
        src, _ = self._fn()
        self.assertLess(src.index("$Tier1Ok = $true"), src.index("\nWrite-InstallRecord"))
        self.assertLess(src.index("\nWrite-InstallRecord"), src.index('Step "INSTALL COMPLETE"'))

    def test_the_keys_are_install_sh_keys_and_the_interpreter_rule_matches(self):
        _, fn = self._fn()
        ps1_keys = set(re.findall(r'"([a-z_0-9]+)=', fn))
        sh = SH.read_text(encoding="utf-8")
        w = sh[sh.index("write_install_record() {"):]
        w = w[:w.index("\n}\n")]
        sh_keys = set(re.findall(r'"([a-z_0-9]+)":', w)) | set(re.findall(r'rec\["([a-z_0-9]+)"\]', w))
        self.assertTrue({"schema", "interpreter", "shape", "tier1"} <= ps1_keys, ps1_keys)
        self.assertLessEqual(ps1_keys, sh_keys, "install.ps1 writes keys install.sh does not: %s" % (ps1_keys - sh_keys))
        self.assertIn("if ($Tier1Ok -and (Test-Path $VenvPy)) { $interp = $VenvPy }", fn)
        self.assertIn("UTF8Encoding($false)", fn)
        self.assertIn('-join "`n"', fn)
        self.assertNotIn("Set-Content", fn)

    def test_a_record_in_that_shape_is_read_by_the_launch_site_parsers(self):
        _, fn = self._fn()
        venv_py = r"D:\Profiles\First Last\.claude\memory\venv\Scripts\python.exe"
        lines = []
        for m in re.finditer(r'^\s*\(?"([^"\n]*)"', fn, re.M):
            t = m.group(1)
            if "=" in t and not t.startswith("#"):
                lines.append(t + (venv_py if t == "interpreter=" else "x"))
            elif t.startswith("# <brain>"):
                lines.append(t)
        rec = "\n".join(lines) + "\n"
        self.assertIn("interpreter=" + venv_py, rec)
        f = self.tmp / "INSTALL_RECORD"
        f.write_bytes(rec.encode("utf-8"))
        awk = subprocess.run(["awk", 'index($0, "interpreter=") == 1 { v = substr($0, 13) } END { print v }', str(f)],
                             capture_output=True, text=True).stdout.rstrip("\n")
        self.assertEqual(awk, venv_py)                              # the POSIX launcher's / cron generator's reader
        hook_reader = [l[len("interpreter="):].strip() for l in rec.splitlines() if l.startswith("interpreter=")][-1]
        self.assertEqual(hook_reader, venv_py)                      # the session hook's reader
        self.assertNotIn("\r", rec)    # LF: the POSIX awk readers keep a CR in the value (see the CR test below)


# ===================================================================================================== iteration 3: one registry
class TestTheTierRegistryHasOneSource(_Scratch):
    """install.sh's C.declare reads table / dim / model from the ENGINE'S EMBED_TIERS (by AST); the other tables that
    name the same facts -- organs/embed_resolver._DIM_FOR and install.sh's own model `case` block -- must agree with it,
    and this test is what fails when one of them drifts."""

    @staticmethod
    def engine_registry(root):
        import ast
        tree = ast.parse((root / "organs" / "claude_memory.py").read_text(encoding="utf-8"))
        consts, tiers = {}, None
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                if isinstance(n.value, ast.Constant):
                    consts[n.targets[0].id] = n.value.value
                elif n.targets[0].id == "EMBED_TIERS":
                    tiers = n.value
        val = lambda e: e.value if isinstance(e, ast.Constant) else consts[e.id]
        return {val(k): tuple(val(e) for e in v.elts[:3]) for k, v in zip(tiers.keys, tiers.values)}

    def test_install_sh_derives_every_tier_from_the_engine_and_embed_resolver_agrees(self):
        eng = self.engine_registry(ROOT)
        self.assertEqual(set(eng), {"nomic", "jina", "qwen"})
        for model, (table, dim, repo) in eng.items():
            self.assertEqual(_install_tier_facts(ROOT, model)[:2], (0, "%s|%s|%d|%s" % (model, table, dim, repo)))
        # an unknown model degrades to the CPU tier by KEY, its facts still the engine's
        self.assertEqual(_install_tier_facts(ROOT, "bogus")[1], "nomic|%s|%d|%s" % eng["nomic"])
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            import embed_resolver
            self.assertEqual(embed_resolver._DIM_FOR, {t: d for t, d, _r in eng.values()})
        finally:
            sys.path.remove(str(ROOT / "organs"))

    def test_the_tier_facts_block_types_no_repo_width_or_table(self):
        blk = _install_sh_block("# >>> tier facts", "# <<< tier facts")
        code = "\n".join(l for l in blk.splitlines() if not l.lstrip().startswith("#"))
        for literal in ("nomic-ai/", "jinaai/", "Qwen/", "memory_embeddings", "768", "1024"):
            self.assertNotIn(literal, code, "install.sh types %s again instead of reading the engine" % literal)

    def test_a_mutant_engine_moves_install_sh_with_it(self):
        mut = self.tmp / "mutant_pkg"
        (mut / "organs").mkdir(parents=True)
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        src = re.sub(r"(?m)^NOMIC_DIM = \d+", "NOMIC_DIM = 999", src)
        src = src.replace('"Qwen/Qwen3-Embedding-0.6B"', '"Qwen/Qwen3-Embedding-4B"')
        (mut / "organs" / "claude_memory.py").write_text(src, encoding="utf-8")
        self.assertEqual(_install_tier_facts(mut, "nomic")[1].split("|")[2], "999")
        self.assertEqual(_install_tier_facts(mut, "qwen")[1].split("|")[3], "Qwen/Qwen3-Embedding-4B")

    def test_an_unreadable_engine_refuses_before_anything_is_touched(self):
        broken = self.tmp / "broken_pkg"
        (broken / "organs").mkdir(parents=True)
        (broken / "organs" / "claude_memory.py").write_text("EMBED_TIERS = None\n", encoding="utf-8")
        rc, _out, err = _install_tier_facts(broken, "nomic")
        self.assertEqual(rc, 2)
        self.assertIn("tier registry", err)

    def test_the_declare_block_types_no_model_or_table_of_its_own(self):
        blk = _heredoc_after("hd_t1_declare() {")
        for literal in ("nomic-ai/", "jinaai/", "Qwen/", '"memory_embeddings_v2"', '"memory_embeddings_v3"', "768", "1024"):
            self.assertNotIn(literal, blk, "C.declare types %s again instead of reading the engine's registry" % literal)

    def test_an_unreadable_registry_declares_nothing(self):
        if not _sqlite_vec_ok():
            self.skipTest("sqlite_vec is not importable by %s" % sys.executable)
        db = self._tier0_brain()
        env = dict(os.environ, EMBED_TABLE="memory_embeddings", EMBED_DIM="768",
                   EMBED_REPO="nomic-ai/nomic-embed-text-v1.5", ENGINE_REGISTRY="")
        p = subprocess.run([sys.executable, "-", str(db)], input=_heredoc_after("hd_t1_declare() {"), capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertTrue(p.stdout.startswith("ERROR\tthe engine's tier registry"), p.stdout + p.stderr)
        self.assertIsNone(_meta(db, "active_embedding_table"))


# ===================================================================================================== iteration 4: one display rule
class TestOneDisplayRuleForEverySpelling(_Scratch):
    """Index, full UUID and unique UUID prefix of the same card get the SAME answer. Where nvidia-smi lists two or more
    cards, a display card (display_active=Enabled, or WIZARD_DISPLAY_INDEX) is refused to no device ('') -- never another
    card; with one card, or no nvidia-smi, the operator's value is honoured whichever spelling. Fresh process per case."""

    TWO = "0, GPU-00000000-monitor-card, Enabled\\n1, GPU-11111111-compute-card, Disabled\\n"
    ONE = "0, GPU-00000000-laptop-card, Enabled\\n"
    PROBE = TestCudaVisibleDevicesIsReadTheWayCudaReadsIt.PROBE

    def _resolve(self, cvd, cards=None, env_file=None):
        env = {"HOME": str(self.tmp), "WIZARD_CONFIG_PATH": str(self.tmp / "no-config.json"), "CUDA_VISIBLE_DEVICES": cvd}
        ef = self.tmp / ("ef_%d" % len(list(self.tmp.iterdir()))) / "wizard-library-env"
        if env_file is not None:
            ef.parent.mkdir(parents=True, exist_ok=True)
            ef.write_text(env_file, encoding="utf-8")
        env["WIZARD_ENV_FILE"] = str(ef)
        if cards is None:
            env["PATH"] = "/usr/bin:/bin"
        else:
            d = "smi_%d" % len(cards)
            if not (self.tmp / d / "nvidia-smi").exists():
                self._exe(d + "/nvidia-smi", "#!/bin/sh\ncase \"$*\" in\n  *index,uuid,display_active*) printf '%s' ;;\n"
                          "  *) exit 0 ;;\nesac\n" % cards)
            env["PATH"] = "%s:/usr/bin:/bin" % (self.tmp / d)
        p = subprocess.run([sys.executable, "-c", self.PROBE, str(ROOT / "organs"), "smi" if cards else "nosmi"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    FILES = (None, "WIZARD_CUDA_DEVICE=1\n")

    def test_two_cards_the_display_card_is_refused_in_every_spelling(self):
        for ef in self.FILES:
            for spell in ("0", "GPU-00000000-monitor-card", "GPU-0000", "0,1", "1,0"):
                self.assertEqual(self._resolve(spell, self.TWO, ef), ["", "ambient-display-refused:0", ""], (spell, ef))

    def test_two_cards_the_other_card_is_honoured_in_every_spelling(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("1", self.TWO, ef), ["1", "ambient", "1"])
            for spell in ("GPU-11111111-compute-card", "GPU-1111"):
                self.assertEqual(self._resolve(spell, self.TWO, ef), [spell, "ambient-uuid:1", spell], (spell, ef))

    def test_the_owners_declared_display_is_refused_in_every_spelling(self):
        # card 1 reports Disabled (a monitor asleep reads that way), but the owner declared it the display card
        ef = "WIZARD_DISPLAY_INDEX=1\nWIZARD_CUDA_DEVICE=0\n"
        for spell in ("1", "GPU-11111111-compute-card", "GPU-1111"):
            self.assertEqual(self._resolve(spell, self.TWO, ef), ["", "ambient-display-refused:1", ""], spell)

    def test_one_card_is_honoured_whichever_spelling(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("0", self.ONE, ef), ["0", "ambient", "0"])
            for spell in ("GPU-00000000-laptop-card", "GPU-0000"):
                self.assertEqual(self._resolve(spell, self.ONE, ef), [spell, "ambient-unchecked:one card", spell])

    def test_no_nvidia_smi_honours_every_spelling(self):
        for ef in self.FILES:
            self.assertEqual(self._resolve("0", None, ef), ["0", "ambient", "0"])
            self.assertEqual(self._resolve("GPU-00000000-monitor-card", None, ef),
                             ["GPU-00000000-monitor-card", "ambient-unchecked:no nvidia-smi", "GPU-00000000-monitor-card"])

    def test_a_refusal_is_never_another_card(self):
        for spell in ("0", "GPU-00000000-monitor-card", "GPU-0000", "GPU-deadbeef", "MIG-1"):
            v, b, after = self._resolve(spell, self.TWO, "WIZARD_CUDA_DEVICE=1\n")
            if b.endswith("refused") or "refused:" in b:
                self.assertEqual((v, after), ("", ""), (spell, b))


# ===================================================================================================== iteration 4: install.ps1 through interop
_PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
_CMD = "/mnt/c/Windows/System32/cmd.exe"


def _ps1_block(src, start, end_marker="\n}\n"):
    i = src.index(start)
    return src[i:src.index(end_marker, i) + len(end_marker)]


# The retracted claim, split so that this file never matches itself (iteration 4: it did not reproduce under cmd.exe).
RETRACTED_CR_CLAIM = "keeps a trailing " + "CR in the value"


def _tracked_files_carrying(root, needle):
    """Every file `git ls-files` tracks under `root` whose text contains `needle` (binary files skipped). The
    population is the git index -- what ships -- not a hand-picked list of files."""
    p = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
    if p.returncode != 0:
        raise unittest.SkipTest("not a git checkout: %s" % root)
    hits = []
    for rel in [x for x in p.stdout.decode("utf-8", "replace").split("\0") if x]:
        f = Path(root) / rel
        try:
            data = f.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        if needle in data.decode("utf-8", "replace"):
            hits.append(rel)
    return hits


class TestInstallPs1TierFactsComeFromTheEngine(_Scratch):
    """install.ps1 chooses its tier ONCE by key ($EmbedTier) and reads table / dim / repo from the engine's EMBED_TIERS;
    no other nomic / 768 literal. EXECUTED through WSL interop where powershell.exe and a Windows python exist
    (testing-005): Get-EngineTier and Write-InstallRecord, extracted verbatim, run with their collaborators stubbed, and
    the record they write is read back by voidloom.cmd's own lines under cmd.exe."""

    PS1 = ROOT / "install" / "install.ps1"

    def test_no_tier_fact_is_typed_twice(self):
        code = [l for l in self.PS1.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]
        nomic = [l for l in code if "nomic" in l]
        self.assertEqual([l.strip() for l in nomic], ['$EmbedTier = "nomic"'], nomic)
        self.assertFalse([l for l in code if re.search(r"\b768\b", l)])

    def _interop(self):
        if not (os.path.isfile(_PS) and os.path.isfile(_CMD)):
            self.skipTest("no powershell.exe / cmd.exe reachable (not a WSL host with interop)")
        p = subprocess.run([_PS, "-NoProfile", "-Command", "(Get-Command python -ErrorAction SilentlyContinue).Source"],
                           capture_output=True, text=True, timeout=120)
        winpy = p.stdout.strip().splitlines()[-1].strip() if p.stdout.strip() else ""
        if not winpy:
            self.skipTest("no Windows python for Get-EngineTier")
        return winpy

    @staticmethod
    def _unc(path):
        return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=30).stdout.strip()

    def _run_writer(self, winpy, engine_root):
        src = self.PS1.read_text(encoding="utf-8")
        brain = self.tmp / "brain"
        (brain / "venv" / "Scripts").mkdir(parents=True)
        (brain / "venv" / "Scripts" / "python.exe").write_bytes(b"")
        (brain / "voidloom").mkdir()
        (brain / "voidloom" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
        tier_lines = "\n".join(l for l in src.splitlines() if re.match(r"\$(EmbedTier|EngineTier|EmbedTable|EmbedDim|EmbedRepo)\s*=", l))
        script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            'function Say ($m) { Write-Host "[install] $m" }',
            _ps1_block(src, "function Invoke-Py {"),
            _ps1_block(src, "function Get-EngineTier {"),
            "$PkgRoot = '%s'" % self._unc(engine_root),
            "$PySys = '%s'" % winpy,
            tier_lines,
            "$BrainDir = '%s'" % self._unc(brain),
            "$DbPath = Join-Path $BrainDir 'claude_memory.db'",
            "$VenvPy = Join-Path $BrainDir 'venv\\Scripts\\python.exe'",
            "$Tier1Ok = $true; $DryRun = $false",
            _ps1_block(src, "function Write-InstallRecord {"),
            "Write-InstallRecord",
        ])
        ps = self.tmp / "writer.ps1"
        ps.write_bytes(script.replace("\n", "\r\n").encode("utf-8-sig"))
        p = subprocess.run([_PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self._unc(ps)],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-1500:] + p.stderr[-1500:])
        return brain

    def test_the_writer_runs_under_powershell_with_the_engines_facts(self):
        winpy = self._interop()
        brain = self._run_writer(winpy, ROOT)
        raw = (brain / "INSTALL_RECORD").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM")
        self.assertNotIn(b"\r", raw)
        rec = dict(l.split("=", 1) for l in raw.decode("utf-8").splitlines() if "=" in l and not l.startswith("#"))
        eng = TestTheTierRegistryHasOneSource.engine_registry(ROOT)["nomic"]
        self.assertEqual((rec["embed_model"], rec["embed_dim"], rec["tier1"]), ("nomic", str(eng[1]), "active"))
        self.assertTrue(rec["interpreter"].endswith(r"venv\Scripts\python.exe"), rec)
        self.assertEqual(rec["package_version"], "9.9.9")
        self.assertFalse(list(brain.glob("INSTALL_RECORD.tmp.*")))

        # ... and voidloom.cmd's own record-reading lines, under cmd.exe, find that interpreter
        lines = (ROOT / "voidloom" / "voidloom.cmd").read_text(encoding="utf-8").splitlines()
        i = next(n for n, l in enumerate(lines) if l.startswith('set "VL_PY="'))
        j = next(n for n, l in enumerate(lines) if "venv\\Scripts\\python.exe" in l and l.startswith("if not defined"))
        reader = ["@echo off", "setlocal EnableExtensions", 'set "PARENT_DIR=%s"' % self._unc(brain)] + lines[i:j + 1] + \
                 ["echo VL_PY=[%VL_PY%]"]
        cmdf = self.tmp / "reader.cmd"
        cmdf.write_bytes("\r\n".join(reader).encode("ascii") + b"\r\n")
        p = subprocess.run([_CMD, "/c", self._unc(cmdf)], capture_output=True, text=True, timeout=120, cwd="/mnt/c")
        got = [l for l in p.stdout.replace("\r", "").splitlines() if l.startswith("VL_PY=")]
        self.assertEqual(got, ["VL_PY=[%s]" % rec["interpreter"]], p.stdout + p.stderr)

    def test_the_lf_reason_is_the_posix_readers_not_for_f(self):
        # testing-006: the stated reason for LF, re-tested. A CRLF record: POSIX awk KEEPS the CR (the value names no
        # file), while the .cmd's `for /f` strips it (executed under cmd.exe where interop exists).
        brain = self.tmp / "crlf_brain"
        (brain / "venv" / "Scripts").mkdir(parents=True)
        (brain / "venv" / "Scripts" / "python.exe").write_bytes(b"")
        interp = str(brain / "venv" / "Scripts" / "python.exe")
        (brain / "INSTALL_RECORD").write_bytes(("schema=1\r\ninterpreter=%s\r\ntier1=active\r\n" % interp).encode())
        v = subprocess.run(["awk", 'index($0, "interpreter=") == 1 { v = substr($0, 13) } END { print v }',
                            str(brain / "INSTALL_RECORD")], capture_output=True).stdout.decode()[:-1]  # bytes: text
        # mode would fold the \r\n of awk's output into \n and hide the very CR under test
        self.assertEqual(v, interp + "\r")
        self.assertFalse(os.path.isfile(v))
        # iteration 5: the guard covers the whole TRACKED tree, not two files (testing-006)
        self.assertEqual(_tracked_files_carrying(ROOT, RETRACTED_CR_CLAIM), [],
                         "the unreproduced CR rationale is back in the tracked tree")
        if not os.path.isfile(_CMD):
            return
        unc_interp = self._unc(brain) + "\\venv\\Scripts\\python.exe"
        (brain / "INSTALL_RECORD").write_bytes(("schema=1\r\ninterpreter=%s\r\ntier1=active\r\n" % unc_interp).encode())
        lines = (ROOT / "voidloom" / "voidloom.cmd").read_text(encoding="utf-8").splitlines()
        i = next(n for n, l in enumerate(lines) if l.startswith('set "VL_PY="'))
        j = next(n for n, l in enumerate(lines) if l.startswith("if defined VL_PY if exist"))
        reader = ["@echo off", "setlocal EnableExtensions", 'set "PARENT_DIR=%s"' % self._unc(brain)] + lines[i:j + 1] + \
                 ["echo VL_PY=[%VL_PY%]"]
        cmdf = self.tmp / "crlf_reader.cmd"
        cmdf.write_bytes("\r\n".join(reader).encode("ascii") + b"\r\n")
        p = subprocess.run([_CMD, "/c", self._unc(cmdf)], capture_output=True, text=True, timeout=120, cwd="/mnt/c")
        got = [l for l in p.stdout.replace("\r\n", "\n").splitlines() if l.startswith("VL_PY=")]
        self.assertEqual(got, ["VL_PY=[%s]" % unc_interp], repr(p.stdout))

    def test_the_dim_is_read_not_typed(self):
        # a MUTANT engine whose nomic width is 999: the record must say 999 -- the value is derived, never typed
        winpy = self._interop()
        mut = self.tmp / "mutant_pkg"
        (mut / "organs").mkdir(parents=True)
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        (mut / "organs" / "claude_memory.py").write_text(re.sub(r"(?m)^NOMIC_DIM = \d+", "NOMIC_DIM = 999", src),
                                                         encoding="utf-8")
        brain = self._run_writer(winpy, mut)
        self.assertIn("embed_dim=999\n", (brain / "INSTALL_RECORD").read_text(encoding="utf-8"))


# ===================================================================================================== iteration 4: -k guard
class TestASelectionCannotSilentlyMissItsTests(_Scratch):
    """VOIDLOOM_REQUIRE_TESTS (voidloom/tests/conftest.py): a -k filter that selects some tests but not the named ones
    fails the run, naming what it missed -- the iteration-3 failure ('-k cron' ran only the old test and passed)."""

    def _pytest(self, k, require):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp), "VOIDLOOM_TESTS_QUIET": "1",
               "VOIDLOOM_REQUIRE_TESTS": require, "PYTHONDONTWRITEBYTECODE": "1"}
        here = str(Path(__file__).resolve())
        return subprocess.run([sys.executable, "-m", "pytest", here, "-k", k, "-q", "-p", "no:cacheprovider",
                               "--co"], capture_output=True, text=True, env=env, timeout=300,
                              cwd=str(Path(__file__).resolve().parents[2]))

    def test_a_filter_that_misses_a_required_test_fails_naming_it(self):
        p = self._pytest("cron", "explicit_wizard_python_bin")      # the iteration-3 slip, reproduced
        self.assertNotEqual(p.returncode, 0, p.stdout[-800:])
        self.assertIn("VOIDLOOM_REQUIRE_TESTS: no selected test matches ['explicit_wizard_python_bin']", p.stdout + p.stderr)

    def test_a_filter_that_selects_every_required_test_passes(self):
        p = self._pytest("cron or explicit_wizard", "cron_generator,explicit_wizard_python_bin")
        self.assertEqual(p.returncode, 0, (p.stdout[-800:], p.stderr[-800:]))


# ===================================================================================================== iteration 5: visible refusal
class TestADisplayRefusalIsVisible(_Scratch):
    """A refusal overrides a value the operator set on purpose, so it is VISIBLE (gpu-safety-002): one stderr line at
    the point of use (value, reason, result), a mark status code recognises (is_refused / health_report['refused']),
    and a doctor row. An honoured pin prints nothing."""

    TWO = TestOneDisplayRuleForEverySpelling.TWO

    def _env(self, cvd, env_file=None):
        d = self.tmp / "smi2"
        if not (d / "nvidia-smi").exists():
            self._exe("smi2/nvidia-smi", "#!/bin/sh\ncase \"$*\" in\n  *index,uuid,display_active*) printf '%s' ;;\n"
                      "  *) exit 0 ;;\nesac\n" % self.TWO)
        ef = self.tmp / "ef" / "wizard-library-env"
        ef.parent.mkdir(parents=True, exist_ok=True)
        ef.write_text(env_file or "", encoding="utf-8")
        return {"PATH": "%s:/usr/bin:/bin" % d, "HOME": str(self.tmp), "WIZARD_ENV_FILE": str(ef),
                "WIZARD_CONFIG_PATH": str(self.tmp / "no-config.json"), "CUDA_VISIBLE_DEVICES": cvd}

    PROBE = ("import os, sys, json; sys.path.insert(0, sys.argv[1]); import voidloom_device as d\n"
             "v, b = d.apply_cuda_pin(); d.apply_cuda_pin()\n"
             "h = d.health_report(); print(json.dumps([v, b, d.is_refused(b), h['refused'], os.environ.get('CUDA_VISIBLE_DEVICES')]))")

    def _probe(self, cvd, env_file=None):
        p = subprocess.run([sys.executable, "-c", self.PROBE, str(ROOT / "organs")], capture_output=True, text=True,
                           env=self._env(cvd, env_file), timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr

    def test_a_refusal_prints_one_line_and_carries_the_mark(self):
        for spell in ("0", "GPU-00000000-monitor-card", "GPU-0000"):
            got, err = self._probe(spell)
            self.assertEqual(got, ["", "ambient-display-refused:0", True, True, ""], spell)
            lines = [l for l in err.splitlines() if "REFUSED" in l]
            self.assertEqual(len(lines), 1, "one line per process, even when the pin is applied twice: %r" % err)
            self.assertIn("CUDA_VISIBLE_DEVICES=%r REFUSED (ambient-display-refused:0)" % spell, lines[0])
            self.assertIn("card 0 drives a display", lines[0])
            self.assertIn("running with NO GPU", lines[0])

    def test_an_honoured_pin_prints_nothing_and_is_not_marked(self):
        got, err = self._probe("1")
        self.assertEqual(got, ["1", "ambient", False, False, "1"])
        self.assertNotIn("REFUSED", err)

    def test_doctor_shows_the_refusal(self):
        env = self._env("0")
        home = self.tmp / "dhome"
        home.mkdir()
        env.update({"HOME": str(home), "PYTHONPATH": str(ROOT), "WIZARD_LIBRARY_HOME": str(ROOT)})
        p = subprocess.run([sys.executable, "-m", "voidloom", "doctor", "--json"], capture_output=True, text=True,
                           env=env, timeout=300, cwd=str(home))
        row = json.loads(p.stdout[p.stdout.index("{"):])["data"]["checks"]["gpu_pin"]
        self.assertEqual((row["value"], row["basis"], row["refused"]), ("", "ambient-display-refused:0", True), row)
        self.assertIn("REFUSED (ambient-display-refused:0)", row["line"])


# ===================================================================================================== iteration 5: the opt-out
class TestTheDisplayRefusalHasANamedOptOut(_Scratch):
    """WIZARD_DISPLAY_INDEX=none -- the owner declaring no display card to protect -- lifts the display refusal for every
    spelling on a 2-card host; without it the default (refuse the display card) stands. Fresh process per case."""

    _resolve = TestOneDisplayRuleForEverySpelling._resolve
    TWO = TestOneDisplayRuleForEverySpelling.TWO
    PROBE = TestOneDisplayRuleForEverySpelling.PROBE
    SPELLINGS = ("0", "GPU-00000000-monitor-card", "GPU-0000", "0,1")

    def test_without_the_opt_out_the_display_card_is_refused(self):
        for ef in (None, "WIZARD_CUDA_DEVICE=1\n", "WIZARD_DISPLAY_INDEX=0\n"):
            for spell in self.SPELLINGS:
                self.assertEqual(self._resolve(spell, self.TWO, ef)[:2], ["", "ambient-display-refused:0"], (spell, ef))

    def test_with_the_opt_out_every_spelling_is_honoured_as_set(self):
        ef = "WIZARD_DISPLAY_INDEX=none\n"
        self.assertEqual(self._resolve("0", self.TWO, ef), ["0", "ambient", "0"])
        self.assertEqual(self._resolve("0,1", self.TWO, ef), ["0,1", "ambient", "0,1"])
        for spell in ("GPU-00000000-monitor-card", "GPU-0000"):
            self.assertEqual(self._resolve(spell, self.TWO, ef), [spell, "ambient-uuid:0", spell])
        # a UUID that names no listed card: nothing left to protect, so it is honoured, and says why
        self.assertEqual(self._resolve("GPU-deadbeef", self.TWO, ef),
                         ["GPU-deadbeef", "ambient-unchecked:display-opt-out", "GPU-deadbeef"])

    def test_the_opt_out_is_named_in_the_refusal_line(self):
        # loaded from ROOT by path: a plain import would find whichever voidloom_device conftest put first
        import importlib.util
        spec = importlib.util.spec_from_file_location("vd_under_test", str(ROOT / "organs" / "voidloom_device.py"))
        d = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            spec.loader.exec_module(d)
        finally:
            sys.path.remove(str(ROOT / "organs"))
        self.assertIn("WIZARD_DISPLAY_INDEX=none", d.refusal_line("0", "ambient-display-refused:0"))


# ===================================================================================================== iteration 5: the -1 ruling
class TestWizardCudaDeviceMinusOneMeansNoGpu(_Scratch):
    """Drew's ruling (memory anchor vl_gpu_minus1_ruling): WIZARD_CUDA_DEVICE=-1 / none means NO GPU to every consumer
    -- the resolver yields no device (CUDA_VISIBLE_DEVICES forced to ''), never card 0, never the degraded card-0
    fallback; declared_device() says 'none'; the installer's model of the units (units_pin) and the installer's own run
    agree. Fresh process per case, CUDA_VISIBLE_DEVICES UNSET (so the declaration layers decide), with and without a
    (2-card) nvidia-smi."""

    PROBE = ("import os, sys, json; sys.path.insert(0, sys.argv[1]); import voidloom_device as d\n"
             "v, b = d.apply_cuda_pin(); print(json.dumps([v, b, os.environ.get('CUDA_VISIBLE_DEVICES'), d.declared_device()]))")

    def _resolve(self, env_file=None, exported=None, smi=True):
        env = {"HOME": str(self.tmp), "WIZARD_CONFIG_PATH": str(self.tmp / "no-config.json")}
        ef = self.tmp / ("ef_%d" % len(list(self.tmp.iterdir()))) / "wizard-library-env"
        if env_file is not None:
            ef.parent.mkdir(parents=True, exist_ok=True)
            ef.write_text(env_file, encoding="utf-8")
        env["WIZARD_ENV_FILE"] = str(ef)
        if exported is not None:
            env["WIZARD_CUDA_DEVICE"] = exported
        if smi:
            if not (self.tmp / "smi2" / "nvidia-smi").exists():
                self._exe("smi2/nvidia-smi", "#!/bin/sh\ncase \"$*\" in\n  *index,uuid,display_active*) printf '%s' ;;\n"
                          "  *--query-gpu=index*) printf '0\\n1\\n' ;;\n  *) exit 0 ;;\nesac\n"
                          % TestOneDisplayRuleForEverySpelling.TWO)
            env["PATH"] = "%s:/usr/bin:/bin" % (self.tmp / "smi2")
        else:
            env["PATH"] = "/usr/bin:/bin"
        p = subprocess.run([sys.executable, "-c", self.PROBE, str(ROOT / "organs")], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_the_env_file_declaration_means_no_gpu(self):
        for smi in (True, False):
            for line in ("WIZARD_CUDA_DEVICE=-1\n", "WIZARD_CUDA_DEVICE=none\n", "WIZARD_CUDA_DEVICE=NONE\n",
                         "WIZARD_CUDA_DEVICE='-1'\n"):
                self.assertEqual(self._resolve(line, smi=smi), ["", "env-file:declared-no-gpu", "", "none"], (line, smi))

    def test_the_exported_declaration_means_no_gpu(self):
        for smi in (True, False):
            for v in ("-1", "none"):
                self.assertEqual(self._resolve(None, exported=v, smi=smi), ["", "env:declared-no-gpu", "", "none"])

    def test_the_minimal_pair_minus_one_against_a_card_and_nothing(self):
        for smi in (True, False):
            self.assertEqual(self._resolve("WIZARD_CUDA_DEVICE=-1\n", smi=smi)[:3], ["", "env-file:declared-no-gpu", ""])
            self.assertEqual(self._resolve("WIZARD_CUDA_DEVICE=1\n", smi=smi), ["1", "env-file", "1", "1"])  # Drew's line
            got = self._resolve("WIZARD_CUDA_DEVICE=-1\n", smi=smi)
            self.assertNotEqual(got[0], "0")
            self.assertFalse(got[1].startswith("degraded"), got)

    def test_the_installers_model_of_the_units_agrees(self):
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import importlib
            import env_file
            importlib.reload(env_file)
            organs = str(ROOT / "organs")
            self.assertEqual(env_file.units_pin("WIZARD_CUDA_DEVICE", "-1", organs), "")
            self.assertEqual(env_file.units_pin("WIZARD_CUDA_DEVICE", "none", organs), "")
            self.assertEqual(env_file.units_pin("WIZARD_CUDA_DEVICE", "1", organs), "1")
        finally:
            sys.path.remove(str(ROOT / "install"))

    def test_the_installer_honours_it_for_its_own_run_and_the_units(self):
        from voidloom.tests.test_tier1_ambient import _fake_nvidia_smi
        home = self.tmp / "home"
        (home / "tmp").mkdir(parents=True)
        (home / ".voidloom").mkdir()
        ef = home / ".voidloom" / "wizard-library-env"
        ef.write_text("WIZARD_CUDA_DEVICE=-1\n", encoding="utf-8")
        fake = self.tmp / "fake_driver"
        _fake_nvidia_smi(fake, {"0": "Enabled", "1": "Disabled"})
        env = {"PATH": "%s:/usr/local/bin:/usr/bin:/bin" % fake, "HOME": str(home), "WIZARD_ENV_FILE": str(ef),
               "WIZARD_CLAUDE_HOME": str(home / ".claude"), "XDG_CONFIG_HOME": str(home / ".config"),
               "XDG_CACHE_HOME": str(home / ".cache"), "TMPDIR": str(home / "tmp"), "WIZARD_EMBED_MODEL": "nomic",
               "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run(["bash", str(SH), "--env-only"], capture_output=True, text=True, env=env, timeout=600,
                           cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("NO GPU: the units and this run use no card", p.stdout)
        pin = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "env_file.py"), "--file", str(ef),
                              "--organs", str(ROOT / "organs"), "effective-pin"], capture_output=True, text=True, timeout=60)
        self.assertEqual((pin.returncode, pin.stdout.strip()), (0, ""), "the units must read no device")


# ===================================================================================================== iteration 5: tree-wide guard
class TestTheRetractedClaimGuardScansTheTrackedTree(_Scratch):
    def test_the_guard_finds_a_planted_copy_anywhere_in_a_tracked_tree(self):
        # RED by construction: a scratch repo whose tracked files include the claim at a path the two-file guard of
        # iteration 4 never read; the tree-wide guard must name exactly that file.
        repo = self.tmp / "planted"
        (repo / "docs" / "deep").mkdir(parents=True)
        (repo / "install").mkdir()
        (repo / "install" / "install.ps1").write_text("# clean\n", encoding="utf-8")
        (repo / "docs" / "deep" / "notes.md").write_text("a batch `for /f` %s, so ...\n" % RETRACTED_CR_CLAIM,
                                                       encoding="utf-8")
        (repo / "untracked.md").write_text(RETRACTED_CR_CLAIM, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        for cmd in (["init", "-q"], ["add", "install/install.ps1", "docs/deep/notes.md"]):
            subprocess.run(["git", "-C", str(repo)] + cmd, check=True, capture_output=True, env=env, timeout=60)
        self.assertEqual(_tracked_files_carrying(repo, RETRACTED_CR_CLAIM), ["docs/deep/notes.md"])

    def test_the_package_tree_is_clean(self):
        self.assertEqual(_tracked_files_carrying(ROOT, RETRACTED_CR_CLAIM), [])


if __name__ == "__main__":
    unittest.main()

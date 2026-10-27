"""R1 (reference-host round): the installer can upgrade the host it was graduated on.

Measured: install.sh knew two shapes and its own reference host fits neither. `--tier0-only`
writes tier0_only=1 into the brain (semantic recall silently off); the default builds a
~5.7 GB venv, re-embeds every vector-less memory and switches every unit's interpreter. So the
reference upgrade of 2026-09-20 ran the package's verbs BY HAND, and PACKAGE_ORIGIN was written
by hand: the installer recorded nothing. The accepted design (the ambient-Tier-1 shape, with
the coordinator's amendments C1-C8) is bound here by execution in scratch homes (F4):

  * `--tier1-ambient <ABS python3>` is REFUSED rc 2, by name, unless all four hold -- a brain
    exists; it declares a Tier-1 table; tier0_only is absent/0; it owns no venv -- and when a
    record names a DIFFERENT interpreter (C1: switching is an owner's act). Each refusal
    touches nothing (the scratch home and the brain byte-identical);
  * a candidate that cannot import torch fails at P1 -- rc != 0 naming P1, nothing laid, no
    venv, tier0_only not written, the brain byte-identical, and one INSTALL_LOG line saying so;
  * `--dry-run` with the flag changes nothing and names the proofs it would execute;
  * `--print-write-set` resolves the shape the run would take -- from the flag, or from
    <brain>/INSTALL_RECORD with no flag -- and grades pip's cache inactive (no venv, no pip)
    and the read-back verb active under --with-crons;
  * THE OUT-OF-BLOCK KEY RULE, every shape: the managed block never writes a key the owner
    assigned outside the markers; the owner's lines survive byte-for-byte.
The full upgrade (cases a, b, h -- the proofs executed through a host's own base interpreter
against a copy of a real Tier-1 brain, a real offline semantic recall, the units read
back through a manager shim, the whole of case a THROUGH the snapshot-diff harness) needs
the host's GPU stack and a 1.3 GB model copy, so it runs in the round's cold gate
(iterations/cold_gate_r3_1.sh), not in the default suite.
At c6d7af2 the flag does not exist ("Unknown arg", rc 2 for the wrong reason) -- every
assertion on a named reason, on the record, and on the block rule is red.

ITERATION 2 (the iteration-1 reflector's 17 items; ruler VL-REF-v1.1). The new guards had only
been shown to HOLD; these bind them by trying to BREAK them, and each is red on the iteration-1
artifacts:
  * item 1  -- a tier0_only=1 committed into the WAL while another connection holds the brain open
               is SEEN: refusal (f) fires, and the reader the tier post-condition compares with
               (brain_decl, extracted verbatim from install.sh) reads the new value;
  * item 2  -- P4 reads display_active LIVE through the one nvidia-smi locator (a FAKE nvidia-smi,
               every branch of the ruling; GPU 0 is never initialised -- every step here stops
               before `import torch`) and install.sh reads the owner's WIZARD_DISPLAY_INDEX from
               OUTSIDE the managed block only;
  * item 4  -- an `export KEY=value` owner line (which systemd ignores) is not counted by the
               out-of-block rule, and is named;
  * item 5  -- the installer's own device IS the units' pin (one value, printed and asserted);
  * items 9-12 -- the header's reranker claim, the record-driven refusal, a directory interpreter,
               and the migrations THIS run applied.

ITERATION 3 (the iteration-2 reflector's 12 items; ruler VL-REF-v1.2). Each new test is red on the
iteration-2 artifacts:
  * item 1  -- the ambient shape has no CPU fallback: --force-cpu and cards that cannot be told apart are
               refusals by name, and P4's skip claims nothing (TestTheAmbientShapeHasNoCpuFallback);
  * item 2  -- the installer never unlinks a sidecar: writer A commits DURING a dry run, a refusal and
               --print-write-set and keeps all its rows (TestTheSidecarGuardNeverDeletesALiveWriter);
  * items 3-4 -- an owner's EMPTY CUDA_VISIBLE_DEVICES is "no device" for the proof too, and the block writes
               no CUDA_VISIBLE_DEVICES over an owner's WIZARD_CUDA_DEVICE (TestAnAssignedEmptyPinIsNotAnAbsentOne);
  * items 6, 12 -- a whitespace interpreter path is refused; an exported WIZARD_DISPLAY_INDEX is not warned about;
  * v1.2    -- on the owner-declared display basis the PIN must be declared too: a declaration may authorise a
               pin, never launder the installer's own choice (probe level and through install.sh).

ITERATION 4 (the iteration-3 reflector's 1 HIGH, 4 LOW, 1 ADVISORY). Each new test is red on the iteration-3 artifacts:
  * HIGH 1  -- the env file is read as systemd's EnvironmentFile= reads it (install/env_file.py; the reader itself is
               bound to systemd in test_env_file_reader.py): an owner's `CUDA_VISIBLE_DEVICES=1 # the compute card`
               (and its quoted twin) is REFUSED naming the line, never proven as 1 while the units fall to card 0;
               a commented WIZARD_CUDA_DEVICE still pins what the units read (their resolver strips it);
  * LOW 5   -- an `export WIZARD_CUDA_DEVICE=` line is named as NOT a declaration (the ambient shape refuses it);
  * item 12 REVERSED by the one reader -- an exported WIZARD_DISPLAY_INDEX is not a declaration either (systemd's
               rules, the one semantics); in the ambient shape it is refused naming the line, elsewhere named once;
  * LOW 2   -- '%' in the interpreter path, '?'/'#'/'%' in the brain path (no file is created outside the brain dir),
               whitespace where a unit line splits on it;
  * LOW 3/4 -- the --attach-db read is inside the sidecar guard, and a symlinked brain's sidecars are retired
               beside its TARGET.
"""
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

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SH = ROOT / "install.sh"
SEED_DB = ROOT / "seed" / "claude_memory_blank.db"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
BEGIN, END = "# >>> wizard-library managed >>>", "# <<< wizard-library managed <<<"
# The directory a SYMLINKED env file points into in the link tests: any directory other than ~/.voidloom. Neutral on
# purpose (env-file writer pass, iteration 4, P0-1): these fixtures named the reference host's legacy directory, and the
# host-bound privacy check (test_notice_claim_holds.py) went RED on them when the landing ran as the real user -- every
# earlier run had a scratch HOME, where that check SKIPS. The link tests pass WIZARD_ENV_FILE; the default path has its own test.
LINK_TARGET_DIR = ".another-dir"



def _declared_device() -> str:
    """This machine's configured compute index, via the single reader (organs/voidloom_device.py) -- never a
    literal: a pinned "1" is one workstation's answer (tools/gpu_pin_gate.py refuses it in a packaged file).
    None of these tests runs anything on a GPU; the pin is passed so the installer never has to GUESS one."""
    try:
        sys.path.insert(0, str(ROOT / "organs"))
        from voidloom_device import declared_device  # type: ignore
        return declared_device()
    except Exception:  # noqa: BLE001
        return "0"
    finally:
        if str(ROOT / "organs") in sys.path:
            sys.path.remove(str(ROOT / "organs"))


def _fake_nvidia_smi(dirpath: Path, states, display_query_fails: bool = False, order=None) -> Path:
    """A FAKE nvidia-smi (a shell script) in `dirpath`, listing one card per entry of `states`
    ({index: display_active}), in index order unless `order` names another (install.sh's display_active
    heuristic takes the FIRST card listed Disabled). Nothing real is queried; nothing initialises CUDA."""
    dirpath.mkdir(parents=True, exist_ok=True)
    idx = list(order) if order else sorted(states, key=int)
    inv = "".join("%s, GPU-%08d-fake-card, Fake 2080 Ti, 11264, 5, 0, 7.5\\n" % (i, int(i)) for i in idx)
    lst = "".join("GPU %s: Fake 2080 Ti (UUID: GPU-%08d-fake-card)\\n" % (i, int(i)) for i in idx)
    disp = ("echo 'NVML: fake failure' >&2; exit 9" if display_query_fails
            else "printf '%s'" % "".join("%s, %s\\n" % (i, states[i]) for i in idx))
    smi = dirpath / "nvidia-smi"
    smi.write_text("#!/bin/sh\n# test double: %d fake card(s)\ncase \"$*\" in\n"
                   "  *index,uuid,name*) printf '%s' ;;\n"
                   "  *index,display_active*) %s ;;\n"
                   "  *-L*) printf '%s' ;;\n"
                   "  *) exit 0 ;;\n"
                   "esac\n" % (len(idx), inv, disp, lst), encoding="utf-8")
    os.chmod(str(smi), 0o755)
    return smi


def _manifest_as_the_organ_derives_it(path: Path, compute: str, cards) -> Path:
    """The machine manifest as organs/voidloom_manifest.py writes it on a >=2-card host: the display
    is the COMPLEMENT of the configured compute index (basis config-derived-complement) -- which is
    why agreeing with it proves nothing about the display (iteration-1 reflection, item 2)."""
    others = [i for i in sorted(cards, key=int) if i != compute]
    pol = {"compute_index": compute}
    if len(cards) >= 2 and len(others) == 1:
        pol["display_uuid"] = "GPU-%08d-fake-card" % int(others[0])
    path.write_text(json.dumps({"gpu_guard_policy": pol, "_discovery": {"basis": "config-derived-complement"}}),
                    encoding="utf-8")
    return path


def _interpreter_can_prove_p1_p2() -> bool:
    chk = subprocess.run([sys.executable, "-c", "import torch, sqlite_vec, sentence_transformers"],
                         capture_output=True, text=True, timeout=300)
    return chk.returncode == 0


def _require_checkout():
    if not SH.is_file() or not SEED_DB.is_file():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tree(root: Path):
    """Every path under root with its bytes' hash (files) -- the whole observable state."""
    out = {}
    for dp, dns, fns in os.walk(str(root)):
        for d in dns:
            out[os.path.relpath(os.path.join(dp, d), str(root))] = "dir"
        for f in fns:
            fp = os.path.join(dp, f)
            out[os.path.relpath(fp, str(root))] = "link" if os.path.islink(fp) else _sha(Path(fp))
    return out


class _AmbientHome(unittest.TestCase):
    def setUp(self):
        _require_checkout()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="ambient_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.home = self.tmp / "home"
        self.brain = self.home / ".claude" / "memory"
        (self.home / "tmp").mkdir(parents=True)
        self.db = self.brain / "claude_memory.db"
        self.env_file = self.home / ".voidloom" / "wizard-library-env"
        self.py = sys.executable

    def _brain(self, table="memory_embeddings_v2", tier0_only=None):
        self.brain.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(SEED_DB), str(self.db))
        c = sqlite3.connect(str(self.db))
        if table:
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', ?)", (table,))
        if tier0_only is not None:
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', ?)", (tier0_only,))
        c.commit()
        c.close()

    def _env(self, **extra):
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home),
               "WIZARD_CLAUDE_HOME": str(self.home / ".claude"), "WIZARD_ENV_FILE": str(self.env_file),
               "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
               "XDG_DATA_HOME": str(self.home / ".local" / "share"), "XDG_STATE_HOME": str(self.home / ".local" / "state"),
               "TMPDIR": str(self.home / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"}
        dev = _declared_device()
        env["CUDA_VISIBLE_DEVICES"] = dev
        env["WIZARD_CUDA_DEVICE"] = dev
        env.update(extra)
        return env

    def _install(self, *args, **extra):
        return subprocess.run(["bash", str(SH)] + list(args), capture_output=True, text=True,
                              env=self._env(**extra), timeout=900, cwd=str(self.tmp))

    def _fake_pin(self, declare=True):
        """A FAKE two-card driver first on PATH (card 0 drives the display) and the OWNER's declaration of its
        display-free card in the env file, outside any managed block -- the way a multi-GPU host carries it (G1,
        env-file writer pass iteration 5: the card is DECLARED, by the owner's hand or by his answer recorded at install;
        the invoking shell's CUDA_VISIBLE_DEVICES / WIZARD_CUDA_DEVICE declare nothing, and with nothing declared a run
        with no terminal is refused). The ambient shape REFUSES --force-cpu (ruler VL-REF-v1.2), so a test that must
        touch no GPU declares a card instead: these runs are dry runs, refusals, or stop at P1 (no torch). The card is
        DERIVED from the fake's states, never a literal. Call it BEFORE a test images the home: it writes the env file."""
        fake = self.tmp / "fake_driver_bin"
        states = {"0": "Enabled", "1": "Disabled"}
        if not (fake / "nvidia-smi").exists():
            _fake_nvidia_smi(fake, states)
        free = [i for i in sorted(states, key=int) if states[i] == "Disabled"][0]
        if declare:
            self._owner_declares(free)
        return dict(PATH=str(fake) + ":/usr/local/bin:/usr/bin:/bin", CUDA_VISIBLE_DEVICES=free,
                    WIZARD_CUDA_DEVICE=free)

    def _owner_declares(self, card, display=None):
        """The OWNER's WIZARD_CUDA_DEVICE= (and, given, WIZARD_DISPLAY_INDEX=) as bare lines at the end of the env file,
        outside any managed block -- unless the file already declares a pin key there."""
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        old = self.env_file.read_text(encoding="utf-8") if self.env_file.exists() else ""
        if any(l.startswith(("WIZARD_CUDA_DEVICE=", "CUDA_VISIBLE_DEVICES=")) for l in old.splitlines()):
            return
        if old and not old.endswith("\n"):
            old += "\n"
        add = ("WIZARD_DISPLAY_INDEX=%s\n" % display if display is not None else "") + "WIZARD_CUDA_DEVICE=%s\n" % card
        self.env_file.write_text(old + add, encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)


class TestTheFourConditionsAndTheRecordRefuseByName(_AmbientHome):

    def _refused(self, args, reason, **extra):
        before = _tree(self.home)
        p = self._install(*args, **extra)
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("--tier1-ambient REFUSED", p.stderr, p.stderr[-1500:])
        self.assertIn(reason, p.stderr, "the refusal must NAME its reason: %r" % p.stderr[-1500:])
        self.assertIn("Nothing was touched", p.stderr)
        self.assertEqual(_tree(self.home), before, "a refused run changed the scratch home")
        return p

    def test_d_a_fresh_home_is_refused(self):
        self._refused(["--tier1-ambient", self.py], "ambient is an UPGRADE shape; a fresh install builds its venv")

    def test_e_an_install_that_owns_a_venv_is_refused(self):
        self._brain()
        (self.brain / "venv" / "bin").mkdir(parents=True)
        (self.brain / "venv" / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
        self._refused(["--tier1-ambient", self.py], "this install owns a venv")

    def test_f_a_keyword_only_brain_is_refused(self):
        self._brain(tier0_only="1")
        self._refused(["--tier1-ambient", self.py], "the brain declares keyword-only")

    def test_a_brain_that_declares_no_tier1_table_is_refused(self):
        self._brain(table=None)
        self._refused(["--tier1-ambient", self.py], "declares no Tier-1 table")

    def test_g_a_different_interpreter_than_the_record_is_refused_naming_both(self):
        self._brain()
        (self.brain / "INSTALL_RECORD").write_text("shape=ambient\ninterpreter=/opt/recorded/bin/python3\n",
                                                  encoding="utf-8")
        p = self._refused(["--tier1-ambient", self.py], "move ")
        self.assertIn("/opt/recorded/bin/python3", p.stderr)
        self.assertIn(self.py, p.stderr)

    def test_a_relative_interpreter_is_refused(self):
        self._brain()
        self._refused(["--tier1-ambient", "bin/python3"], "must be an ABSOLUTE path")

    def test_a_disagreeing_embed_model_is_refused(self):
        self._brain()
        self._refused(["--tier1-ambient", self.py], "never changes the recall tier", WIZARD_EMBED_MODEL="qwen")

    def test_tier0_only_contradicts_the_shape(self):
        self._brain()
        self._refused(["--tier1-ambient", self.py, "--tier0-only"], "--tier0-only contradicts")

    def test_the_record_alone_selects_the_shape_and_its_conditions_still_bind(self):
        self._brain(tier0_only="1")
        (self.brain / "INSTALL_RECORD").write_text("shape=ambient\ninterpreter=%s\n" % self.py, encoding="utf-8")
        p = self._refused([], "the brain declares keyword-only")          # NO flag: the record drove it
        self.assertIn("(shape from %s)" % (self.brain / "INSTALL_RECORD"), p.stderr)       # item 10

    def test_a_wal_resident_tier0_only_is_refused(self):
        """Item 1 (HIGH). The gate read the brain with immutable=1, which never reads the WAL: with any
        connection holding the brain open, a committed tier0_only=1 stays in the WAL and the gate did
        not see it -- a keyword-only brain passed refusal (f) (executed by the iteration-1 reflector).
        Here a READER holds the brain open while a second connection commits tier0_only='1'; the
        premise is proven first (immutable=1 misses it, mode=ro sees it), then the gate must refuse."""
        self._brain()
        reader = subprocess.Popen([sys.executable, "-c",
                                   "import sqlite3, sys, time\n"
                                   "c = sqlite3.connect(sys.argv[1]); c.execute('SELECT 1 FROM memories LIMIT 1').fetchall()\n"
                                   "print('open', flush=True); time.sleep(120)\n", str(self.db)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (reader.kill(), reader.wait(), reader.stdout.close()))
        self.assertEqual(reader.stdout.readline().strip(), "open")
        w = sqlite3.connect(str(self.db))
        w.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        w.commit()
        w.close()

        def t0(uri):
            c = sqlite3.connect(uri, uri=True)
            try:
                r = c.execute("SELECT value FROM metadata WHERE key='tier0_only'").fetchone()
                return r[0] if r else None
            finally:
                c.close()
        self.assertIsNone(t0("file:%s?immutable=1" % self.db), "premise: the commit must still be WAL-resident")
        self.assertEqual(t0("file:%s?mode=ro" % self.db), "1", "premise: a WAL-aware read sees the commit")
        main_before = _sha(self.db)
        before = {k: v for k, v in _tree(self.home).items() if not k.endswith(("-wal", "-shm"))}
        p = self._install("--tier1-ambient", self.py, "--dry-run")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("the brain declares keyword-only", p.stderr, p.stderr[-1500:])
        self.assertEqual(_sha(self.db), main_before, "the main brain file changed")
        self.assertEqual({k: v for k, v in _tree(self.home).items() if not k.endswith(("-wal", "-shm"))}, before)

    def test_the_tier_reader_the_post_condition_compares_sees_a_wal_resident_change(self):
        """Item 1 (HIGH), the post-condition half. The tier post-condition compares two reads of ONE
        reader, install.sh:brain_decl (bound below by the lines that assign TIER_BEFORE / TIER_AFTER);
        run verbatim, that reader must return a DIFFERENT declaration once tier0_only=1 is committed
        behind an open reader -- iteration 1's returned the same one, so a mutant that wrote
        tier0_only=1 printed "post-condition held", rc 0. The probe's own reader (tier1_probe.db_facts)
        must see it too."""
        sh = SH.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'^\s*TIER_BEFORE="\$\(brain_decl\)"', sh, re.M)), 1)
        self.assertEqual(len(re.findall(r'^\s*TIER_AFTER="\$\(brain_decl\)"', sh, re.M)), 1)
        self.assertIn('[ "$TIER_AFTER" = "$TIER_BEFORE" ]', sh)
        fn = re.search(r"^brain_decl\(\) \{.*?^\}\n", sh, re.M | re.S)
        self.assertIsNotNone(fn, "install.sh defines no brain_decl()")
        (self.tmp / "decl.sh").write_text(fn.group(0) + 'PY_SYS="$1"; DB_PATH="$2"; brain_decl\n', encoding="utf-8")
        self._brain()

        def decl():
            p = subprocess.run(["bash", str(self.tmp / "decl.sh"), self.py, str(self.db)], capture_output=True,
                               text=True, timeout=60)
            return p.stdout.strip()
        self.assertEqual(decl(), "memory_embeddings_v2|")
        reader = subprocess.Popen([sys.executable, "-c",
                                   "import sqlite3, sys, time\n"
                                   "c = sqlite3.connect(sys.argv[1]); c.execute('SELECT 1 FROM memories LIMIT 1').fetchall()\n"
                                   "print('open', flush=True); time.sleep(120)\n", str(self.db)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (reader.kill(), reader.wait(), reader.stdout.close()))
        self.assertEqual(reader.stdout.readline().strip(), "open")
        w = sqlite3.connect(str(self.db))
        w.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        w.commit()
        w.close()
        c = sqlite3.connect("file:%s?immutable=1" % self.db, uri=True)
        try:
            self.assertIsNone(c.execute("SELECT value FROM metadata WHERE key='tier0_only'").fetchone(),
                              "premise: the commit must still be WAL-resident")
        finally:
            c.close()
        self.assertEqual(decl(), "memory_embeddings_v2|1",
                         "the post-condition's reader missed a WAL-resident tier0_only=1: it would print 'held'")
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import importlib
            import tier1_probe  # type: ignore
            importlib.reload(tier1_probe)
            self.assertEqual(tier1_probe.db_facts(str(self.db)).get("tier0_only"), "1")
        finally:
            sys.path.remove(str(ROOT / "install"))

    def test_the_record_driven_refusal_of_a_missing_brain_names_the_record(self):
        """Item 10. With no flag the record drives the shape; the old advice "(run without
        --tier1-ambient)" repeated the refusal. It must name the record and the owner's act."""
        self.brain.mkdir(parents=True)
        (self.brain / "INSTALL_RECORD").write_text("shape=ambient\ninterpreter=%s\n" % self.py, encoding="utf-8")
        p = self._refused([], "records an ambient install: move that record aside deliberately")
        self.assertIn("(shape from %s)" % (self.brain / "INSTALL_RECORD"), p.stderr)

    def test_a_directory_is_not_an_interpreter(self):
        """Item 11: `[ -x DIR ]` is true, so `--tier1-ambient /usr/bin` passed the gate and died rc 1 in
        lay.py. It is refused rc 2 by name."""
        self._brain()
        d = self.tmp / "a_directory"
        d.mkdir()
        self._refused(["--tier1-ambient", str(d)], "is not an executable file")


class TestAFailedProofLaysNothing(_AmbientHome):
    """Case (c): a candidate with no torch fails at P1 -- stop, name the step, touch nothing."""

    def test_c_a_torch_less_candidate_fails_at_p1_and_nothing_is_laid(self):
        self._brain()
        venv = self.tmp / "no_torch_venv"
        mk = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], capture_output=True,
                            text=True, timeout=300)
        if mk.returncode != 0:
            self.skipTest("cannot build a bare venv on this host: %s" % mk.stderr[-300:])
        cand = str(venv / "bin" / "python3")
        chk = subprocess.run([cand, "-c", "import torch"], capture_output=True, text=True)
        if chk.returncode == 0:
            self.skipTest("the bare venv can import torch; it is not a torch-less candidate here")
        db_before = _sha(self.db)
        pin = self._fake_pin()
        env_before = self.env_file.read_bytes()             # the owner's declaration (G1): the run must not change it
        p = self._install("--tier1-ambient", cand, **pin)
        self.assertNotEqual(p.returncode, 0, p.stdout[-1500:])
        self.assertNotEqual(p.returncode, 2, "a failed PROOF is not a refusal: %r" % p.stderr[-800:])
        self.assertIn("P1 FAILED", p.stdout + p.stderr, (p.stdout[-1500:], p.stderr[-800:]))
        self.assertIn("NOT proven", p.stderr, p.stderr[-800:])
        self.assertFalse((self.brain / "organs").exists(), "files were laid after a failed proof")
        self.assertFalse((self.brain / "venv").exists(), "a venv was built")
        self.assertFalse((self.home / ".local" / "bin" / "voidloom").exists(), "the CLI was linked")
        self.assertEqual(self.env_file.read_bytes(), env_before, "the env file was written")
        self.assertEqual(_sha(self.db), db_before, "the brain changed")
        c = sqlite3.connect("file:%s?mode=ro" % self.db, uri=True)
        try:
            self.assertIsNone(c.execute("SELECT value FROM metadata WHERE key='tier0_only'").fetchone(),
                              "tier0_only was written")
        finally:
            c.close()
        log = (self.brain / "INSTALL_LOG").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1, log)
        self.assertIn("shape=ambient", log[0])
        self.assertIn("result=failed-rc", log[0])
        self.assertIn("P1:FAILED", log[0])
        self.assertFalse((self.brain / "INSTALL_RECORD").exists(), "a failed run must not rewrite the record")


class TestDryRunAndWriteSetKnowTheShape(_AmbientHome):

    def test_dry_run_changes_nothing_and_names_the_proofs(self):
        self._brain()
        pin = self._fake_pin()
        before = _tree(self.home)
        p = self._install("--tier1-ambient", self.py, "--dry-run", "--with-crons", "--thinker", "off", **pin)   # M10: the answer
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-800:]))
        self.assertIn("AMBIENT Tier 1", p.stdout)
        self.assertIn("DRY: would prove P1 imports", p.stdout)
        self.assertEqual(_tree(self.home), before, "a dry run wrote something")
        # item 9: this shape pre-caches and loads NO reranker; the header may not say it does
        self.assertIn("NOT pre-cached or loaded by this install", p.stdout)
        self.assertNotIn("pre-cached AND loaded", p.stdout)

    def test_the_owners_display_declaration_is_read_outside_the_block_only(self):
        """Item 2: WIZARD_DISPLAY_INDEX is an owner's DECLARATION, read outside the managed block (the
        block is regenerated, so a line inside it is the installer's, not the owner's)."""
        self._brain()
        self.env_file.parent.mkdir(parents=True)
        for inside, want in ((True, "declared WIZARD_DISPLAY_INDEX: none"), (False, "declared WIZARD_DISPLAY_INDEX: 0")):
            body = ("WIZARD_DISPLAY_INDEX=0\n" if not inside else "") + BEGIN + "\n" + \
                   ("WIZARD_DISPLAY_INDEX=0\n" if inside else "") + END + "\n"
            self.env_file.write_text(body, encoding="utf-8")
            os.chmod(str(self.env_file), 0o600)
            p = self._install("--tier1-ambient", self.py, "--dry-run", **self._fake_pin())
            self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-800:]))
            self.assertIn(want, p.stdout, "inside=%s: %r" % (inside, p.stdout[-1500:]))

    def test_print_write_set_resolves_the_shape_from_the_flag_or_the_record(self):
        self._brain()
        p = self._install("--print-write-set", "--tier1-ambient", self.py, "--with-crons", **self._fake_pin())
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        d = json.loads(p.stdout)
        self.assertEqual(d["invocation"]["shape"], "ambient")
        self.assertEqual(d["invocation"]["ambient_interpreter"], self.py)
        by = {e["path"]: e for e in d["declared"]}
        pip = [e for e in d["declared"] if e["path"].endswith("/pip/**")]
        self.assertTrue(pip and not any(e["active"] for e in pip), "the ambient shape runs no pip: %r" % pip)
        self.assertTrue(by[str(self.brain / "INSTALL_RECORD")]["active"])
        self.assertTrue(by[str(self.brain / "INSTALL_LOG")]["active"])
        self.assertTrue(by[str(self.db) + ".pre-upgrade-*.db"]["active"])
        rb = [v for v in d["verbs"] if v["call"].startswith("systemctl --user show -p ExecStart")]
        self.assertTrue(rb and all(v["active"] for v in rb), "the read-back verb must be declared active: %r" % rb)
        # NO flag: the record alone selects the shape, and the declaration follows it
        (self.brain / "INSTALL_RECORD").write_text("shape=ambient\ninterpreter=%s\n" % self.py, encoding="utf-8")
        p2 = self._install("--print-write-set", "--with-crons", **self._fake_pin())
        self.assertEqual(p2.returncode, 0, p2.stderr[-800:])
        self.assertEqual(json.loads(p2.stdout)["invocation"]["shape"], "ambient")
        # and a venv-shape invocation still declares pip ACTIVE and the read-back INACTIVE
        (self.brain / "INSTALL_RECORD").unlink()
        d3 = json.loads(self._install("--print-write-set", "--with-crons", "--force-cpu").stdout)
        self.assertEqual(d3["invocation"]["shape"], "venv")
        self.assertTrue(all(e["active"] for e in d3["declared"] if e["path"].endswith("/pip/**")))
        self.assertFalse(any(v["active"] for v in d3["verbs"] if v["call"].startswith("systemctl --user show")))


class TestTheBlockNeverWritesAKeyAssignedOutsideIt(_AmbientHome):
    """The out-of-block key rule, in the VENV shape (the rule binds every shape): an owner's own
    PATH and WIZARD_EMBED_MODEL outside the markers are left to those lines; the block does not
    write them (last-wins would otherwise decide by position), and every outside line survives."""

    def test_the_block_leaves_owner_keys_to_the_owners_lines(self):
        self.env_file.parent.mkdir(parents=True)
        owner = ("CLAUDE_CODE_OAUTH_TOKEN=not-a-real-token\n"
                 'PATH="/opt/owner/bin:/usr/bin:/bin"\n'
                 "WIZARD_EMBED_MODEL=nomic  # the owner's own pin\n")
        self.env_file.write_text(owner + BEGIN + "\nPATH=/old/block/path\n" + END + "\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        p = self._install("--env-only", "--force-cpu", WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        text = self.env_file.read_text(encoding="utf-8")
        block = text[text.index(BEGIN):text.index(END)]
        keys = [l.split("=", 1)[0] for l in block.splitlines() if l and not l.startswith("#") and "=" in l]
        self.assertNotIn("PATH", keys, "the block wrote PATH although the owner assigns it outside: %r" % block)
        self.assertNotIn("WIZARD_EMBED_MODEL", keys, block)
        self.assertIn("WIZARD_LIBRARY_DB", keys, "keys the owner does NOT assign are still managed: %r" % block)
        self.assertIn("# PATH is assigned OUTSIDE this block", block)
        self.assertTrue(text.startswith(owner), "the owner's lines did not survive byte-for-byte")

    def test_an_export_line_outside_the_block_assigns_nothing_and_is_named(self):
        """Item 4. systemd's EnvironmentFile= leaves `export KEY=value` UNSET (the package's own
        measurement), but the rule counted it: an owner's `export PATH=...` made the block write NO
        PATH, so the units ran with systemd's default PATH while the read-back said the interpreter's
        dir was on it. The export line is not counted -- the block writes a bare PATH= -- and it is named."""
        self.env_file.parent.mkdir(parents=True)
        owner = 'export PATH="/opt/owner/bin:/usr/bin:/bin"\n'
        self.env_file.write_text(owner + BEGIN + "\nPATH=/old/block/path\n" + END + "\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        p = self._install("--env-only", "--force-cpu", WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        text = self.env_file.read_text(encoding="utf-8")
        block = text[text.index(BEGIN):text.index(END)]
        self.assertEqual(len([l for l in block.splitlines() if l.startswith("PATH=")]), 1,
                         "the block must write a bare PATH= the units can read: %r" % block)
        self.assertIn("'export PATH' is written with `export`; systemd ignores that line", p.stderr)
        self.assertTrue(text.startswith(owner), "the owner's line did not survive byte-for-byte")


class TestP4ReadsTheDisplayLive(unittest.TestCase):
    """Item 2 (HIGH), ruled by the coordinator (ruler VL-REF-v1.1, memory #21831). P4's "not the display
    card" rested on the machine manifest, whose display is the COMPLEMENT of the configured compute
    index (organs/voidloom_manifest.py) -- so pin == compute and pin != display was a tautology, and a
    wrong pin on the card driving the monitor passed. P4 now reads display_active LIVE:
        1 card                       -> pass (the pin must be that card)
        >=2 cards, >=1 Enabled       -> the pin must not be an Enabled card, else FAIL naming it
        >=2 cards, none Enabled      -> FAIL closed, unless the owner DECLARED WIZARD_DISPLAY_INDEX
                                        (<n>|none) outside the block: then pin != it, basis=owner-declared
    Every case runs the probe's own P4 step with a FAKE nvidia-smi first on PATH and a manifest written
    the way the organ writes it. CUDA_VISIBLE_DEVICES is EMPTY in the step's environment, so a step
    that passes the display rule stops at its internal CUDA guard BEFORE `import torch`: no GPU is
    initialised here, and GPU 0 never is."""

    def setUp(self):
        self.probe = ROOT / "install" / "tier1_probe.py"
        if not self.probe.is_file() or not (ROOT / "organs" / "voidloom_manifest.py").is_file():
            raise unittest.SkipTest("tree lacks install/tier1_probe.py or organs/voidloom_manifest.py")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="p4live_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))

    def _p4(self, states, pin, declared="", compute=None, display_query_fails=False, pin_declared_by=None,
            pin_source=None):
        fake = self.tmp / ("smi_%d" % len(list(self.tmp.iterdir())))
        _fake_nvidia_smi(fake, states, display_query_fails)
        man = _manifest_as_the_organ_derives_it(fake / "voidloom_manifest.json", compute or pin, states)
        env = {"PATH": str(fake) + ":/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp),
               "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "",
               "WIZARD_ENV_FILE": str(self.tmp / "no-env-file")}
        who = []                                  # WHO declared the pin (ruler VL-REF-v1.2); omitted = nobody
        if pin_declared_by is not None:
            who += ["--pin-declared-by", pin_declared_by]
        if pin_source is not None:
            who += ["--pin-source", pin_source]
        p = subprocess.run([sys.executable, "-B", str(self.probe), "step", "P4", "--db", str(self.tmp / "unused.db"),
                            "--organs", str(ROOT / "organs"), "--manifest", str(man), "--pin", pin,
                            "--display-index", declared] + who, capture_output=True, text=True, env=env, timeout=120)
        self.assertIn(p.returncode, (0, 1), p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def _display_passed(self, out, basis):
        # the display rule passed: the step stopped at its CUDA guard, BEFORE torch was imported
        self.assertFalse(out["ok"])
        self.assertIn("internal: CUDA_VISIBLE_DEVICES=''", out["error"], out)
        self.assertEqual((out.get("facts") or {}).get("display_basis"), basis, out)
        return out["facts"]

    def _refused(self, out, reason):
        self.assertFalse(out["ok"])
        self.assertIn(reason, out["error"], out)
        self.assertNotIn("internal: CUDA_VISIBLE_DEVICES", out["error"])
        self.assertIn("live_display_active", out.get("facts") or {}, "a refusal must report what P4 OBSERVED: %r" % out)
        return out

    def test_a_brain_with_no_manifest_is_proven_against_the_organs_own_derivation(self):
        """W8 (found at the reference host's landing, #21961): the proofs run BEFORE Step A.1 writes
        voidloom_manifest.json, and P4 refused on its absence ("the machine manifest ... is unreadable") -- an ambient
        host that had never had one could never pass P4. P4 now derives the policy READ-ONLY with the organ's own rules
        (compute_index = voidloom_device.declared_device(), the display = its complement on the live probe); the
        derivation still binds (a declared compute card that is not the pin is refused), and nothing is written."""
        fake = self.tmp / "smi_noman"
        _fake_nvidia_smi(fake, {"0": "Enabled", "1": "Disabled"})
        man = self.tmp / "brain_without_a_manifest" / "voidloom_manifest.json"
        pin = "1"                                  # the fake's display-free card; card 0 is Enabled
        for declared, passes in ((pin, True), ("0", False)):
            env = {"PATH": str(fake) + ":/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp),
                   "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "", "WIZARD_CUDA_DEVICE": declared,
                   "WIZARD_ENV_FILE": str(self.tmp / "no-env-file")}
            p = subprocess.run([sys.executable, "-B", str(self.probe), "step", "P4", "--db", str(self.tmp / "unused.db"),
                                "--organs", str(ROOT / "organs"), "--manifest", str(man), "--pin", pin,
                                "--display-index", ""], capture_output=True, text=True, env=env, timeout=120)
            self.assertIn(p.returncode, (0, 1), p.stderr[-800:])
            out = json.loads(p.stdout.strip().splitlines()[-1])
            if passes:
                facts = self._display_passed(out, "observed")
                self.assertIn("derived read-only: no manifest at %s" % man, facts.get("manifest_source", ""), facts)
                self.assertEqual(facts.get("manifest_compute_index"), pin, facts)
            else:
                self.assertIn("is not the machine manifest's compute_index '0' (derived read-only", out["error"], out)
        self.assertFalse(man.parent.exists(), "P4 wrote something while deriving the manifest")

    def test_one_card_is_shared_with_the_display_and_passes(self):
        self._display_passed(self._p4({"0": "Enabled"}, "0"), "single-card")

    def test_one_card_and_a_pin_that_is_not_it_fails(self):
        out = self._p4({"0": "Disabled"}, "1")
        self.assertFalse(out["ok"])
        self.assertIn("the pin must be that card", out["error"], out)

    def test_two_cards_one_enabled_the_other_passes_by_observation(self):
        f = self._display_passed(self._p4({"0": "Enabled", "1": "Disabled"}, "1"), "observed")
        self.assertEqual(f["live_display_enabled"], ["0"])

    def test_two_cards_the_pin_is_the_enabled_card_fails_naming_it(self):
        # the iteration-1 reflector's X3: the manifest derives display = card 1 from a WRONG compute index 0,
        # so the manifest agrees with the pin -- only the live observation sees card 0 driving the monitor
        self._refused(self._p4({"0": "Enabled", "1": "Disabled"}, "0"), "card 0 drives a display")

    def test_two_cards_none_enabled_and_no_declaration_fails_closed(self):
        out = self._refused(self._p4({"0": "Disabled", "1": "Disabled"}, "1"),
                            "no card reports an active display (monitor asleep?) -- wake it and re-run, or declare "
                            "WIZARD_DISPLAY_INDEX=<n>|none in the env file outside the managed block")
        self.assertEqual(out["facts"]["live_display_enabled"], [])

    def test_two_cards_none_enabled_an_owner_declared_display_lets_the_other_card_pass(self):
        # v1.2: the owner's display declaration authorises a pin the OWNER declared (outside the block)
        f = self._display_passed(self._p4({"0": "Disabled", "1": "Disabled"}, "1", declared="0",
                                          pin_declared_by="WIZARD_CUDA_DEVICE"), "owner-declared")
        self.assertEqual(f["display_declared"], "0")
        self.assertEqual(f["pin_declared_by"], "WIZARD_CUDA_DEVICE")

    def test_two_cards_none_enabled_the_pin_is_the_declared_display_fails(self):
        self._refused(self._p4({"0": "Disabled", "1": "Disabled"}, "1", declared="1"),
                      "the display card the owner declared (WIZARD_DISPLAY_INDEX=1)")

    def test_an_owner_declared_headless_host_passes(self):
        self._display_passed(self._p4({"0": "Disabled", "1": "Disabled"}, "1", declared="none",
                                      pin_declared_by="CUDA_VISIBLE_DEVICES"), "owner-declared")

    def test_an_owner_declared_display_does_not_launder_an_undeclared_pin(self):
        """Ruler VL-REF-v1.2 (the iteration-2 reflector's counter-example, executed): two cards both
        Disabled, WIZARD_DISPLAY_INDEX=none declared, NO pin declared -- the installer's display_active
        heuristic chose card 0 and P4 passed it as owner-declared. A declaration may authorise a pin; it
        may not launder a guess. With no --pin-declared-by the step must FAIL naming the pin (and, with
        --pin-source, where it came from) -- before `import torch` (CUDA_VISIBLE_DEVICES is empty here)."""
        for decl in ("none", "1"):
            out = self._refused(self._p4({"0": "Disabled", "1": "Disabled"}, "0", declared=decl),
                                "the pin 0 is NOT declared by the owner")
            self.assertEqual(out["facts"]["live_display_enabled"], [])
        src = "chosen because nvidia-smi reports it display_active=Disabled (override with WIZARD_CUDA_DEVICE)"
        out = self._refused(self._p4({"0": "Disabled", "1": "Disabled"}, "0", declared="none", pin_declared_by="",
                                     pin_source=src), "NOT declared by the owner (%s)" % src)
        self.assertIn("declare WIZARD_CUDA_DEVICE=<index>", out["error"])
        self.assertIsNone(out["facts"].get("pin_declared_by"), out)
        self.assertEqual(out["facts"].get("pin_source"), src, out)

    def test_an_observed_display_needs_no_pin_declaration(self):
        # v1.2 binds ONLY the owner-declared basis: an observed display (or a single card) still proves the pin
        self._display_passed(self._p4({"0": "Enabled", "1": "Disabled"}, "1"), "observed")
        self._display_passed(self._p4({"0": "Enabled"}, "0"), "single-card")

    def test_a_malformed_or_absent_declaration_fails(self):
        self._refused(self._p4({"0": "Disabled", "1": "Disabled"}, "1", declared="gpu0"), "neither a card index nor 'none'")
        self._refused(self._p4({"0": "Disabled", "1": "Disabled"}, "1", declared="7"), "names no card nvidia-smi lists")

    def test_an_unobservable_display_query_fails_closed(self):
        out = self._p4({"0": "Enabled", "1": "Disabled"}, "1", display_query_fails=True)
        self.assertFalse(out["ok"])
        self.assertIn("display_active is unobservable", out["error"], out)

    def test_the_rule_as_a_pure_function(self):
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import importlib
            import tier1_probe  # type: ignore
            importlib.reload(tier1_probe)
            v = tier1_probe.display_verdict
            self.assertEqual(v("0", {"0": "Enabled"})["display_basis"], "single-card")
            self.assertEqual(v("1", {"0": "Enabled", "1": "Disabled"})["display_basis"], "observed")
            for key in ("WIZARD_CUDA_DEVICE", "CUDA_VISIBLE_DEVICES"):
                self.assertEqual(v("1", {"0": "Disabled", "1": "Disabled"}, "0", key)["display_basis"], "owner-declared")
                self.assertEqual(v("1", {"0": "Disabled", "1": "Disabled"}, "none", key)["display_basis"], "owner-declared")
            # v1.2: on the owner-declared basis a pin nobody declared (or declared by a key that is not a pin) fails
            for by in ("", "WIZARD_DISPLAY_INDEX", "shell"):
                for decl in ("none", "0"):
                    with self.assertRaisesRegex(RuntimeError, "NOT declared by the owner", msg=(by, decl)):
                        v("1", {"0": "Disabled", "1": "Disabled"}, decl, by)
            for pin, cards, decl in (("0", {"0": "Enabled", "1": "Disabled"}, ""), ("1", {"0": "Disabled", "1": "Disabled"}, ""),
                                     ("1", {"0": "Disabled", "1": "Disabled"}, "1"), ("2", {"0": "Enabled", "1": "Disabled"}, ""),
                                     ("1", {"0": "Enabled"}, ""), ("1", {"0": "Enabled", "1": "Disabled"}, "1")):
                with self.assertRaises(RuntimeError, msg=(pin, cards, decl)):
                    v(pin, cards, decl)
        finally:
            sys.path.remove(str(ROOT / "install"))


class TestTheInstallerHoldsTheLiveDisplayRule(_AmbientHome):
    """Item 2 through install.sh: the four conditions hold, P1 and P2 pass through this interpreter, and
    P4 -- with a FAKE nvidia-smi on PATH whose two cards both read display_active=Disabled, the monitor-
    asleep case measured on 2026-09-20 (#20341) -- FAILS CLOSED before anything is laid or loaded (P3 is
    the first model load and never runs). The owner's declaration reaches P4 only from OUTSIDE the block."""

    def setUp(self):
        super().setUp()
        if not _interpreter_can_prove_p1_p2():
            self.skipTest("this interpreter cannot import torch + sqlite_vec + sentence_transformers (P1)")
        self.brain.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(ROOT))
        try:
            from voidloom.tests._owned_brain import packaged_brain
        finally:
            sys.path.remove(str(ROOT))
        packaged_brain(self.db)                   # carries memory_embeddings_v2 (vec0) with its declared width
        c = sqlite3.connect(str(self.db))
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        c.commit()
        c.close()
        self.pin = _declared_device()
        self.fake = self.tmp / "fake_driver_bin"
        _fake_nvidia_smi(self.fake, {"0": "Disabled", "1": "Disabled"})
        _manifest_as_the_organ_derives_it(self.brain / "voidloom_manifest.json", self.pin, {"0": "", "1": ""})

    def _run(self, env_text):
        # the OWNER declares the pin (G1: with two cards and nothing declared a run with no terminal is refused before
        # P4); what each test below proves is P4's DISPLAY rule over that declared pin
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("WIZARD_CUDA_DEVICE=%s\n" % self.pin + env_text, encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        return self._install("--tier1-ambient", self.py, PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin")

    def _failed_at_p4(self, p, reason):
        out = p.stdout + p.stderr
        self.assertNotEqual(p.returncode, 0, out[-2000:])
        self.assertNotEqual(p.returncode, 2, "a failed PROOF is not a refusal: %r" % out[-1500:])
        self.assertIn("P4 FAILED", out, out[-2000:])
        self.assertIn(reason, out, out[-2000:])
        self.assertNotIn("P3 ok", out, "P3 (the first model load) ran after a failed P4")
        self.assertIn("P4 observed display_active", out, "the refusal must print what P4 observed")
        self.assertIn("a read-only proof may leave SQLite -wal/-shm sidecars beside it", p.stderr)   # item 17
        self.assertFalse((self.brain / "organs").exists(), "files were laid after a failed P4")
        self.assertIn("P4:FAILED", (self.brain / "INSTALL_LOG").read_text(encoding="utf-8"))

    def test_no_card_reporting_a_display_fails_closed_and_lays_nothing(self):
        self._failed_at_p4(self._run(BEGIN + "\n" + END + "\n"), "no card reports an active display (monitor asleep?)")

    def test_a_declaration_inside_the_managed_block_is_not_the_owners(self):
        other = "0" if self.pin != "0" else "1"
        self._failed_at_p4(self._run(BEGIN + "\nWIZARD_DISPLAY_INDEX=%s\n" % other + END + "\n"),
                           "no card reports an active display (monitor asleep?)")

    def test_the_owners_declaration_outside_the_block_reaches_p4(self):
        # the owner declares the PINNED card as the display: P4 must refuse it by name
        self._failed_at_p4(self._run("WIZARD_DISPLAY_INDEX=%s\n" % self.pin + BEGIN + "\n" + END + "\n"),
                           "the display card the owner declared (WIZARD_DISPLAY_INDEX=%s)" % self.pin)

    def test_the_owners_answers_let_p4_pass_with_the_monitor_asleep(self):
        """G1 (iteration 5), proof 1's second half: both cards Disabled (the monitor asleep), NOTHING declared, a terminal
        -- the owner answers the two questions, and P4 then PASSES on the owner-declared basis (the pin declared by
        WIZARD_CUDA_DEVICE, the display by WIZARD_DISPLAY_INDEX). Since model-choice Round 1 (R-1 / EW5-01) the answers are
        HELD through the proofs and recorded only when ALL of P1-P5 pass: the run may stop at P3 (the model this scratch
        home does not cache), and then NOTHING is recorded -- else they are, as his declarations. P4 is the subject.
        CUDA is initialised only under the declared pin (this machine's configured compute card). The overnight-thinker
        question (M10) is answered by flag: it is not this test's subject."""
        # P4 initialises CUDA under the pin for real (only nvidia-smi is faked): without a CUDA card it fails by design,
        # so this case needs one. A hosted CI runner has none (found there, 2026-10-27); skip, loudly, with the reason.
        probe = subprocess.run([self.py, "-c", "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 3)"],
                               env=dict(os.environ, CUDA_VISIBLE_DEVICES=self.pin), capture_output=True, text=True,
                               timeout=300)
        if probe.returncode != 0:
            self.skipTest("needs a real CUDA card under the pin (%s): torch reports none here" % self.pin)
        display = "0" if self.pin != "0" else "1"
        env = self._env(PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin")
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--tier1-ambient", self.py, "--thinker", "off"], env,
                                             [(Q_MONITOR, display), (Q_WORK, self.pin)], str(self.tmp))
        self.assertEqual(left, [], tty[-1500:])
        self.assertIn("are HELD until the ambient proofs pass", out, (out[-2000:], err[-1500:]))
        self.assertIn("P4 ok", out, (out[-2500:], err[-1500:]))
        self.assertIn("display_basis=owner-declared", out)
        self.assertIn("pin_declared_by=WIZARD_CUDA_DEVICE", out)
        text = self.env_file.read_text(encoding="utf-8") if self.env_file.exists() else ""
        if "the candidate PROVED Tier 1" in out:
            self.assertIn("recorded your answers in %s" % self.env_file, out)
            self.assertIn("\nWIZARD_DISPLAY_INDEX=%s\n" % display, "\n" + text)
            self.assertIn("\nWIZARD_CUDA_DEVICE=%s\n" % self.pin, text)
        else:
            self.assertIn("were HELD for the proofs and never recorded", err, err[-1500:])
            self.assertNotIn("WIZARD_CUDA_DEVICE=%s" % self.pin, text, "a refused upgrade recorded the answers")

    def test_a_declared_display_does_not_launder_the_installers_own_pin(self):
        """Ruler VL-REF-v1.2, through install.sh: both cards Disabled, the owner declares
        WIZARD_DISPLAY_INDEX=none outside the block, NOBODY declares a pin (not the shell, not the file). The
        installer used to choose one itself (its display_active heuristic) and P4 had to FAIL naming it; since G1
        (iteration 5) the installer chooses NOTHING: with no terminal it refuses before the proofs, lists the cards and
        names the flag that carries the owner's answer -- --gpu only, since the display is declared. The fake lists
        card 1 FIRST, so a positional guess would be card 1, never card 0: this test also runs on trees WITHOUT the
        rule (the falsifier arm), where P4 passes the guessed pin and initialises CUDA under it."""
        fake = self.tmp / "fake_driver_listed_1_first"
        _fake_nvidia_smi(fake, {"0": "Disabled", "1": "Disabled"}, order=("1", "0"))
        chosen = "1"
        _manifest_as_the_organ_derives_it(self.brain / "voidloom_manifest.json", chosen, {"0": "", "1": ""})
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("WIZARD_DISPLAY_INDEX=none\n" + BEGIN + "\n" + END + "\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        p = self._install("--tier1-ambient", self.py, PATH=str(fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        out = p.stdout + p.stderr
        # nothing declares the units' card: G1 asks nobody here (no terminal) and chooses nothing -- the run is refused
        # BEFORE the proofs, the cards listed, the one missing answer's flag named (the display is declared: none)
        self.assertEqual(p.returncode, 2, out[-1500:])
        self.assertIn("declares which one the brain's background jobs use", out)
        self.assertIn("Pass the answer as --gpu <card>.", out)
        self.assertIn("An installing AI agent must put this question to the human", out)
        self.assertNotIn("GPU device:   ", p.stdout)
        self.assertNotIn("GPU device:   %s -- chosen because" % chosen, p.stdout)
        self.assertNotIn("P3 ok", out)
        self.assertFalse((self.brain / "organs").exists(), "files were laid after the refusal")


class TestOnePinForTheInstallerAndTheUnits(_AmbientHome):
    """Item 5. The installer chose the device for ITS OWN steps by one precedence (resolve_gpu_device:
    shell, then the file's WIZARD_CUDA_DEVICE, then a display_active heuristic) and P4 proved the
    units' pin by another (effective_pin: the file's CUDA_VISIBLE_DEVICES first). Measured by the
    iteration-1 reflector with a two-card fake, both Disabled, the owner's CUDA_VISIBLE_DEVICES=<A>
    outside the block and nothing in the shell: the installer exported card 0 for itself while P4
    proved card A, and Step E then loaded an embedder on the unproven card. Now ONE value -- printed,
    and asserted before every model-loading step. Dry runs with a FAKE nvidia-smi: no GPU is touched."""

    def setUp(self):
        super().setUp()
        self._brain()
        self.fake = self.tmp / "fake_driver_bin"
        _fake_nvidia_smi(self.fake, {"0": "Disabled", "1": "Disabled"})
        self.env_file.parent.mkdir(parents=True)
        self.units_pin = "1"                  # a FAKE two-card host: the heuristic alone would pick card 0
        self.env_file.write_text("CUDA_VISIBLE_DEVICES=%s\n" % self.units_pin + BEGIN + "\n" + END + "\n",
                                 encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)

    def _dry(self, **shell):
        env = dict(PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin", CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        env.update(shell)
        p = self._install("--tier1-ambient", self.py, "--dry-run", **env)
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-800:]))
        return p

    def test_the_owners_file_pin_is_the_installers_own_device(self):
        p = self._dry()                                      # the shell pins NOTHING
        self.assertIn("GPU device:   %s -- declared by CUDA_VISIBLE_DEVICES in %s" % (self.units_pin, self.env_file), p.stdout)
        # the WHOLE line: the label ends at the block note (a `${v:- -- EMPTY}` expansion appends a SET value to it)
        self.assertRegex(p.stdout, r"(?m)^\[install\] GPU device:   %s -- declared by CUDA_VISIBLE_DEVICES in %s "
                                   r"\(outside the managed block\)$" % (re.escape(self.units_pin), re.escape(str(self.env_file))))
        # and P4 is told WHO declared it (ruler VL-REF-v1.2)
        self.assertIn("DRY: '%s' (declared by CUDA_VISIBLE_DEVICES in %s (outside the managed block))"
                      % (self.units_pin, self.env_file), p.stdout)
        self.assertNotIn("chosen because nvidia-smi reports it display_active=Disabled", p.stdout)
        self.assertIn("one pin: the proofs runs on CUDA_VISIBLE_DEVICES=%s" % self.units_pin, p.stdout)

    def test_a_shell_pin_that_disagrees_with_the_units_is_overridden_loudly(self):
        """Since G1 (iteration 5) the invoking shell's pin variables are not a declaration at all: the run never
        resolves them (it used to take WIZARD_CUDA_DEVICE for its own steps, then override it) -- it says so."""
        other = "0"
        p = self._dry(WIZARD_CUDA_DEVICE=other, CUDA_VISIBLE_DEVICES=other)
        self.assertIn("the invoking shell's pin variables (CUDA_VISIBLE_DEVICES='%s' WIZARD_CUDA_DEVICE='%s') are not a "
                      "declaration" % (other, other), p.stdout)
        self.assertIn("This run and the units use card %s" % self.units_pin, p.stdout)
        self.assertNotIn("this run had resolved GPU %s" % other, p.stderr)
        self.assertIn("GPU device:   %s (CUDA_VISIBLE_DEVICES)" % self.units_pin, p.stdout)
        self.assertIn("one pin: the proofs runs on CUDA_VISIBLE_DEVICES=%s" % self.units_pin, p.stdout)


class TestAnAssignedEmptyPinIsNotAnAbsentOne(_AmbientHome):
    """Iteration 3, items 3 and 4 (the class: a resolver that cannot tell ASSIGNED-EMPTY from ABSENT, and a
    block key that overrides an owner's key by PRECEDENCE). An owner's `CUDA_VISIBLE_DEVICES=` outside the
    block is CUDA's own "no device", which the units honour (voidloom_device: ambient-empty); both installer
    resolvers read it as absent, so P4 proved card 1 and the record said gpu_pin=1 while every unit got no
    device (measured by the iteration-2 reflector). And a block CUDA_VISIBLE_DEVICES OUTRANKS an owner's
    WIZARD_CUDA_DEVICE in every unit's resolver, so the out-of-block rule -- keyed on names -- still let the
    block override the owner. Dry runs and --env-only with a FAKE two-card driver: no GPU is touched."""

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake_driver_bin"
        # card 0 drives the display, card 1 does not: the only geometry in which the installer's heuristic may choose a
        # card (with every card Disabled it chooses none -- reflector-4)
        _fake_nvidia_smi(self.fake, {"0": "Enabled", "1": "Disabled"})
        self.env_file.parent.mkdir(parents=True)
        sys.path.insert(0, str(ROOT / "organs"))
        self.addCleanup(lambda: sys.path.remove(str(ROOT / "organs")))

    def _owner(self, lines):
        self.env_file.write_text(lines + BEGIN + "\n" + END + "\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)

    def _block(self):
        t = self.env_file.read_text(encoding="utf-8")
        return t[t.index(BEGIN):t.index(END)]

    def _units_device(self):
        """The device a UNIT resolves: the env file as systemd's EnvironmentFile= hands it over (bare
        KEY=value lines; `export` lines are unset), then the package's own resolver, in a clean process."""
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        for raw in self.env_file.read_text(encoding="utf-8").splitlines():
            t = raw.strip()
            if not t or t.startswith("#") or t.startswith("export "):
                continue
            k, sep, v = t.partition("=")
            if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k.strip()):
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                env[k.strip()] = v
        p = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import voidloom_device; "
                            "print('%s|%s' % voidloom_device.resolve_cuda_device())", str(ROOT / "organs")],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout.strip()

    def _systemd_env(self):
        """The env file AS WRITTEN, read by systemd's OWN load_env_file() (test_env_file_reader._ORACLE, by ctypes,
        where the host has libsystemd-shared) -- else by install/env_file.py, which that module binds to systemd.
        Returns (env, which-reader)."""
        sys.path.insert(0, str(ROOT))
        try:
            from voidloom.tests.test_env_file_reader import _systemd_reads, _reader
        finally:
            sys.path.remove(str(ROOT))
        got = _systemd_reads([self.env_file])
        if got is not None:
            env = got[str(self.env_file)]
            return ({} if env == "REJECT" else env), "systemd's own load_env_file()"
        res = _reader().read_file(str(self.env_file))
        return ({} if res["error"] else {k: v for k, (v, _l) in res["effective"].items()}), "install/env_file.py"

    def _units_by_systemd(self):
        """value|basis the units' resolver derives, handed the pin keys systemd's own reading of the file gives."""
        env, _how = self._systemd_env()
        child = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                 "WIZARD_ENV_FILE": str(self.env_file)}
        child.update({k: env[k] for k in ("CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE") if k in env})
        p = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import voidloom_device; "
                            "print('%s|%s' % voidloom_device.resolve_cuda_device())", str(ROOT / "organs")],
                           capture_output=True, text=True, env=child, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout.strip()

    def test_an_owners_empty_cuda_visible_devices_is_no_device_for_the_proof_too(self):
        self._brain()
        self._owner("WIZARD_CUDA_DEVICE=1\nCUDA_VISIBLE_DEVICES=\n")
        p = self._install("--tier1-ambient", self.py, "--dry-run", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-800:]))
        self.assertIn("declared by CUDA_VISIBLE_DEVICES in %s (outside the managed block) -- EMPTY: no device"
                      % self.env_file, p.stdout)
        self.assertIn("DRY: '<none>' (declared by CUDA_VISIBLE_DEVICES in %s" % self.env_file, p.stdout,
                      "the proof must be handed the units' EMPTY pin, which P4 refuses: %r" % p.stdout[-1500:])
        self.assertNotIn("one pin:", p.stdout, "a pin was asserted that the units will never see")
        self.assertEqual(self._units_device().split("|")[1], "ambient-empty", "premise: the units get no device")

    def test_an_inline_comment_on_the_owners_cuda_pin_is_refused_not_proven_stripped(self):
        # HIGH 1 (the iteration-3 reflector's literal test, kept): systemd's EnvironmentFile= has NO inline comments
        # (only lines STARTING with '#'): every unit receives '1 # the compute card', voidloom_device rejects it as an
        # ambient value and falls to device 0 -- while the installer, reading with bash's rule, proved '1' (record
        # gpu_pin=1, units 0). Now the one reader reads it as systemd does and the installer refuses it by line.
        self._brain()
        self._owner("CUDA_VISIBLE_DEVICES=1 # the compute card\n")
        # premise, since the iteration-2 no-device predicate: the units' resolver reads '1 # the compute card' as
        # selecting NO card (its first entry is not an index) and makes no assertion -- before, it fell to card 0
        self.assertEqual(self._units_device().split("|")[1], "ambient-no-device", "premise: the units get no pin")
        p = self._install("--tier1-ambient", self.py, "--dry-run", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("carries an inline '#' comment", p.stderr)
        self.assertIn("line 1: CUDA_VISIBLE_DEVICES reads as '1 # the compute card'", p.stderr)
        self.assertIn("Nothing was touched", p.stderr)
        self.assertNotIn("one pin:", p.stdout)

    def test_the_quoted_twin_and_the_venv_shape_are_refused_too(self):
        # "1" # compute -> systemd appends the text after the quote: '1# compute'; the harm is the same in every
        # shape (the block writes no CUDA_VISIBLE_DEVICES over an owner's key, so the units read the owner's line)
        self._owner('CUDA_VISIBLE_DEVICES="1" # compute\n')
        before = self.env_file.read_bytes()
        p = self._install("--env-only", WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-1500:])
        self.assertIn("reads as '1# compute'", p.stderr)
        self.assertIn("Nothing was touched", p.stderr)
        self.assertEqual(self.env_file.read_bytes(), before, "a refused run rewrote the env file")

    def test_a_commented_wizard_cuda_device_still_pins_what_the_units_read(self):
        # the control: the units' resolver strips an inline comment from WIZARD_CUDA_DEVICE itself, so systemd's
        # '1 # the compute card' resolves 1 in every unit -- the installer proves 1 and says so
        self._brain()
        self._owner("WIZARD_CUDA_DEVICE=1 # the compute card\n")
        p = self._install("--tier1-ambient", self.py, "--dry-run", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("GPU device:   1 -- declared by WIZARD_CUDA_DEVICE in %s (outside the managed block)" % self.env_file,
                      p.stdout)
        self.assertTrue(self._units_device().startswith("1|env"), self._units_device())

    def test_an_exported_pin_is_named_as_not_a_declaration(self):
        # LOW 5: systemd drops `export WIZARD_CUDA_DEVICE=1`, so it declares nothing -- and on P4's failure path the
        # owner used to be told only "the pin 0 is NOT declared by the owner", zero lines naming the export line
        self._brain()
        self._owner("WIZARD_DISPLAY_INDEX=none\nexport WIZARD_CUDA_DEVICE=1\n")
        p = self._install("--tier1-ambient", self.py, "--dry-run", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("line 2: WIZARD_CUDA_DEVICE -- it is written with `export`", p.stderr)
        self.assertIn("it is NOT a declaration: systemd never hands it to the units", p.stderr)
        self.assertIn("Write it as a bare WIZARD_CUDA_DEVICE=<value> line", p.stderr)

    def test_the_block_rewrite_keeps_every_owner_byte(self):
        # iteration 5: ONE Latin-1 byte in an owner's comment (systemd accepts it; the check passed it) crashed the block
        # REPLACER and an EMPTY temp file was copied over the env file -- rc 0, 'your other lines preserved', every owner
        # line and the block gone (token re-appended alone); CR / form feed / U+2028 lines were re-split
        owner = b"# caf\xe9 -- the compute card\nWIZARD_CUDA_DEVICE=1\r\nNOTE=a\x0cb\nNOTE2='x\xe2\x80\xa8y'\n"
        self.env_file.write_bytes(owner + (BEGIN + "\nPATH='/old'\n" + END + "\nAFTER=1\n").encode())
        p = self._install("--env-only", WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        after = self.env_file.read_bytes()
        self.assertTrue(after.startswith(owner), after[:200])
        self.assertTrue(after.endswith(END.encode() + b"\nAFTER=1\n"), after[-200:])
        # THE FILE AS WRITTEN, read by systemd's OWN loader and then by the units' resolver (env-file writer pass, W1):
        # the owner's lines read as they did, the block's keys are there, and the units resolve the owner's card
        env, how = self._systemd_env()
        self.assertEqual((env.get("WIZARD_CUDA_DEVICE"), env.get("NOTE"), env.get("NOTE2"), env.get("AFTER")),
                         ("1", "a\x0cb", "x\u2028y", "1"), how)
        self.assertIn("WIZARD_LIBRARY_HOME", env, how)
        self.assertTrue(self._units_by_systemd().startswith("1|env"), (how, self._units_by_systemd()))
        self.assertIn("one pin: %s, read whole as systemd reads it, hands the units '1'" % self.env_file, p.stdout)

    def test_a_second_managed_block_is_refused_before_anything_is_touched(self):
        self._brain()
        self.env_file.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n" + END + "\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='0'\n"
                                 + END + "\n", encoding="utf-8")
        before = self.env_file.read_bytes()
        p = self._install("--tier1-ambient", self.py, "--dry-run", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("not ONE begin/end marker pair", p.stderr)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_the_token_rescue_writes_a_line_systemd_reads(self):
        # the owner's declared card first (G1: two cards, nothing declared, no terminal = refused)
        self.env_file.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\nexport CLAUDE_CODE_OAUTH_TOKEN=fake-in-an-old-block\n"
                                 + END + "\n", encoding="utf-8")
        p = self._install("--env-only", WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertIn("\nCLAUDE_CODE_OAUTH_TOKEN=fake-in-an-old-block\n", self.env_file.read_text(encoding="utf-8"))

    def test_a_block_a_shell_would_misread_is_refused_before_anything_is_laid(self):
        home = self.tmp / "o'brien"
        (home / "tmp").mkdir(parents=True)
        env = self._env(PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin", CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        env.update(HOME=str(home), WIZARD_CLAUDE_HOME=str(home / ".claude"), TMPDIR=str(home / "tmp"),
                   WIZARD_ENV_FILE=str(home / ".voidloom" / "wizard-library-env"))
        p = subprocess.run(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu"], capture_output=True, text=True,
                           env=env, timeout=900, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("read differently by systemd and by a shell", p.stderr)
        self.assertFalse((home / ".claude").exists(), "files were laid before the refusal")

    def test_the_write_set_declares_a_symlinked_env_files_target(self):
        # the reference host's geometry (R2): ~/.voidloom/wizard-library-env -> another directory; the run writes THROUGH
        # the link (measured, iteration 4: R1 case (a) through the harness went RED on 'UNDECLARED modified <target>')
        target = self.home / LINK_TARGET_DIR / "wizard-library-env"
        target.parent.mkdir(parents=True)
        target.write_text("WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
        self.env_file.symlink_to(target)
        p = self._install("--print-write-set", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        paths = [d["path"] for d in json.loads(p.stdout)["declared"] if d["step"] == "D"]
        self.assertIn(str(target), paths)

    def test_the_env_file_is_the_package_default_when_no_override_is_given(self):
        """With WIZARD_ENV_FILE unset (every other test passes it) the env file this run reads and writes is the
        package default, ~/.voidloom/wizard-library-env -- whether or not it exists yet. The installer has one
        answer for this file; there is no other directory it falls back to."""
        default = self.home / ".voidloom" / "wizard-library-env"
        env = self._env(PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin")
        env.pop("WIZARD_ENV_FILE")

        def resolved():
            p = subprocess.run(["bash", str(SH), "--print-write-set"], capture_output=True, text=True, env=env,
                               timeout=900, cwd=str(self.tmp))
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            d = json.loads(p.stdout)
            step_d = [w["path"] for w in d["declared"] if w["step"] == "D"]
            return d["resolved"]["ENV_FILE"], step_d
        self.assertEqual(resolved()[0], str(default), "no file anywhere: the package default")
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text("WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
        got, step_d = resolved()
        self.assertEqual(got, str(default), "the file exists: still the package default")
        self.assertIn(str(default), step_d)

    def test_a_file_size_limit_never_leaves_a_truncated_env_file(self):
        """W1: "an empty or truncated temp file is never copied over the env file". A file-size limit (what a full
        disk does to a write) cuts the rewritten file at 2048 bytes. Measured on 524c63e: the replacer died on it, its
        exit status was not checked, and `cp -f` installed the first 2048 bytes -- rc 0, the owner's later lines gone.
        Now the writer's image is read back from the disk before it is used, the run stops, and the env file is
        byte-identical."""
        try:
            import resource
            import signal
        except ImportError:
            self.skipTest("no resource/signal module on this platform")
        owner = b"# " + b"a long owner comment " * 150 + b"\nWIZARD_CUDA_DEVICE=1\n"
        self.env_file.write_bytes(owner + (BEGIN + "\n" + END + "\nAFTER=1\n").encode())
        before = self.env_file.read_bytes()
        self.assertGreater(len(before), 2048, "premise: the rewritten file must be longer than the limit")

        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (2048, 2048))
        p = subprocess.run(["bash", str(SH), "--env-only"], capture_output=True, text=True, timeout=900, cwd=str(self.tmp),
                           env=self._env(WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                                         CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE=""), preexec_fn=limit)
        self.assertNotEqual(p.returncode, 0, "a run whose env-file write was cut short reported success: %r"
                            % (p.stdout + p.stderr)[-800:])
        self.assertEqual(self.env_file.read_bytes(), before, "the env file was changed by a write that could not complete")

    def test_the_writers_refusal_names_the_line_and_comes_before_anything_is_laid(self):
        """W1 + W5: the append path after an owner's OPEN QUOTE (systemd would read the whole block into that value --
        at 524c63e only a warning, and the units got no PATH from the block). Refused rc 2 NAMING the line, the file
        byte-identical, and nothing laid (a wet --init-blank run: no .claude)."""
        self.env_file.write_bytes(b"WIZARD_CUDA_DEVICE=1\nNOTE='an open quote")
        before = self.env_file.read_bytes()
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("line 2: the file ends inside a quote", p.stderr)
        self.assertIn("Nothing was touched", p.stderr)
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertFalse((self.home / ".claude").exists(), "files were laid before the refusal")

    def test_an_indented_token_in_an_old_block_is_still_the_units_token(self):
        """W1, the token rescue: systemd reads `   CLAUDE_CODE_OAUTH_TOKEN=...` inside an old block (leading blanks);
        the rescue's `grep '^(export )?CLAUDE_CODE_OAUTH_TOKEN='` did not, so the rewrite dropped the units' token
        (measured on 524c63e and on the reflector's proposal: token present before, absent after, rc 0)."""
        self.env_file.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n   CLAUDE_CODE_OAUTH_TOKEN=fake-not-a-token-indented\n"
                                 + END + "\n", encoding="utf-8")
        before, _how = self._systemd_env()
        self.assertEqual(before.get("CLAUDE_CODE_OAUTH_TOKEN"), "fake-not-a-token-indented", "premise")
        p = self._install("--env-only", WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        after, how = self._systemd_env()
        self.assertEqual(after.get("CLAUDE_CODE_OAUTH_TOKEN"), "fake-not-a-token-indented", how)
        self.assertNotIn("fake-not-a-token-indented", p.stdout + p.stderr, "the token was printed")

    def test_help_answers_under_a_home_the_path_checks_refuse(self):
        """ADVISORY 8: a home holding '%' (legal) is refused by the path checks, which ran before the argument loop, so
        `install.sh --help` exited 2 and the owner could not read the help that says why. --help now answers first; a
        real run under the same home is still refused."""
        home = self.tmp / "pct%home"
        (home / "tmp").mkdir(parents=True)
        env = self._env()
        env.update(HOME=str(home), WIZARD_CLAUDE_HOME=str(home / ".claude"), TMPDIR=str(home / "tmp"),
                   WIZARD_ENV_FILE=str(home / ".voidloom" / "wizard-library-env"))
        p = subprocess.run(["bash", str(SH), "--help"], capture_output=True, text=True, env=env, timeout=120, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        self.assertIn("--tier1-ambient", p.stdout)
        p = subprocess.run(["bash", str(SH), "--dry-run"], capture_output=True, text=True, env=env, timeout=120, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 2, p.stderr[-600:])
        self.assertIn("must not contain '%'", p.stderr)

    # ---- the DESTINATION of the write (env-file writer pass, iteration-1 reflection EW1-01 / EW1-02 / EW1-04)
    def _envonly(self, *args, **extra):
        return self._install("--env-only", *args, WIZARD_EMBED_MODEL="nomic",
                             PATH=extra.pop("PATH", str(self.fake) + ":/usr/local/bin:/usr/bin:/bin"),
                             CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="", **extra)

    def test_a_read_only_link_target_is_refused_and_the_link_is_kept(self):
        """EW1-01/02: `cp -f` on a symlinked env file whose TARGET is read-only unlinked the LINK and wrote a regular
        file in its place -- rc 0, the owner's real file left stale (iteration-1 reflection, executed). Now refused
        rc 2 before anything is touched, naming the link AND the file it names; the link and its target unchanged."""
        target = self.home / LINK_TARGET_DIR / "wizard-library-env"
        target.parent.mkdir(parents=True)
        target.write_text("WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
        target.chmod(0o400)
        self.addCleanup(lambda: target.chmod(0o600))
        self.env_file.symlink_to(target)
        if os.access(str(target), os.W_OK):
            self.skipTest("this user can write a 0400 file (root): the geometry cannot be built")
        p = self._envonly()
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-1500:])
        self.assertIn("is not a writable regular file", p.stderr)
        self.assertIn("(a symlink to %s)" % target, p.stderr)
        self.assertTrue(self.env_file.is_symlink(), "the link was replaced")
        self.assertEqual(os.readlink(str(self.env_file)), str(target))
        self.assertEqual(target.read_text(encoding="utf-8"), "WIZARD_CUDA_DEVICE=1\n")

    def test_a_writable_link_target_is_written_through_and_the_link_is_kept(self):
        """EW1-01's other state (a guard, green on both sides of the fix): a writable target gets the block IN PLACE --
        the link is still a link to the same file, the owner's line kept, the target's mode 600."""
        target = self.home / LINK_TARGET_DIR / "wizard-library-env"
        target.parent.mkdir(parents=True)
        target.write_text("WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
        target.chmod(0o640)
        ino = target.stat().st_ino
        self.env_file.symlink_to(target)
        p = self._envonly()
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertTrue(self.env_file.is_symlink(), "the link was replaced")
        self.assertEqual(os.readlink(str(self.env_file)), str(target))
        body = target.read_text(encoding="utf-8")
        self.assertTrue(body.startswith("WIZARD_CUDA_DEVICE=1\n") and BEGIN in body and END in body, body[:300])
        self.assertEqual((target.stat().st_ino, target.stat().st_mode & 0o777), (ino, 0o600), "written in place, mode 600")

    def test_a_dangling_link_is_refused_before_anything_is_laid(self):
        """EW1-02: a symlinked env file whose target does not exist -- `cp` will not write through it -- failed only at
        Step D, rc 1, after 313 files were laid and the brain created. Now rc 2 before anything is laid."""
        self.env_file.symlink_to(self.home / "nowhere" / "wizard-library-env")
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("which does not exist", p.stderr)
        self.assertFalse((self.home / ".claude").exists(), "files were laid before the refusal")
        self.assertTrue(self.env_file.is_symlink())

    def test_an_env_directory_this_user_cannot_write_is_refused_before_anything_is_laid(self):
        """EW1-02: no env file yet and its directory not writable (0555): `cp` could not create it -- rc 1 at Step D,
        after the lay. Now rc 2 before anything is laid, naming the directory."""
        d = self.env_file.parent
        d.chmod(0o555)
        self.addCleanup(lambda: d.chmod(0o755))
        if os.access(str(d), os.W_OK):
            self.skipTest("this user can write a 0555 directory (root): the geometry cannot be built")
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("its directory cannot be created or written (nearest existing: %s)" % d, p.stderr)
        self.assertFalse((self.home / ".claude").exists(), "files were laid before the refusal")
        self.assertFalse(self.env_file.exists())

    def test_a_dangling_link_as_the_env_directory_is_refused_before_anything_is_laid(self):
        """EW2-07 (iteration-2 reflection, executed): the env file's DIRECTORY is a symlink to a path that does not exist
        (an unmounted data disk) -- the rehearsal walked past it as 'absent', and the run failed at Step D, rc 1, after
        313 files were laid and the brain created. Now rc 2 before anything is laid, naming the link and where it points."""
        d = self.env_file.parent
        shutil.rmtree(str(d))
        d.symlink_to(self.home / "unmounted" / "voidloom")
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("a symlink to %s, which leads to no existing directory" % (self.home / "unmounted" / "voidloom"), p.stderr)
        self.assertFalse((self.home / ".claude").exists(), "files were laid before the refusal")
        # a link that points at ITSELF resolves to nothing either (measured: 'which does not exist' was untrue of it)
        d.unlink()
        d.symlink_to(d)
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("a symlink to %s, which leads to no existing directory" % d, p.stderr)
        self.assertFalse((self.home / ".claude").exists(), "files were laid before the refusal")

    def test_a_fifo_at_the_env_file_path_is_refused_not_waited_on(self):
        """EW1-02: a FIFO at the env-file path made the writer's read of it BLOCK -- the installer hung (killed by a
        timeout, rc 124). Now refused rc 2: it is not a regular file. The run is its own process group, killed whole
        if it outlives the timeout, so a hang is a failure here, never a stray process."""
        import signal
        os.mkfifo(str(self.env_file))
        proc = subprocess.Popen(["bash", str(SH), "--env-only"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self._env(WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                                              CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE=""),
                                cwd=str(self.tmp), start_new_session=True)
        try:
            out, err = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail("the installer waited on a FIFO at the env-file path (killed after 120 s)")
        self.assertEqual(proc.returncode, 2, (out + err)[-1500:])
        self.assertIn("is not a writable regular file", err)

    def test_no_pin_key_is_said_as_such(self):
        """EW1-04: with no pin key in the file the read-back said the units get '<no pin>' -- while their resolver falls
        to its own layers, else card 0 (measured: '0|env-file' from an `export` line systemd drops; card 0 by default on
        a host whose two cards cannot be told apart). The line says NO pin key and names the fallback. Handoff round 1
        (L4, clean-room proof #28100): this run is --force-cpu, which now RECORDS WIZARD_ACCELERATOR=cpu -- the units
        answer the CPU before any card fallback -- so the old 'card 0 may drive a display' WARNING, which fired on the
        owner's own CPU answer, is replaced by a line saying no card is used."""
        self.env_file.write_text("# nothing pinned\n", encoding="utf-8")
        p = self._envonly("--force-cpu")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertIn("hands the units NO pin key", p.stdout)
        self.assertIn("falls back to its own layers", p.stdout)
        self.assertNotIn("'<no pin>'", p.stdout)
        self.assertNotIn("no pin reaches the units on a host with 2 GPUs", p.stdout + p.stderr)
        self.assertIn("no card: WIZARD_ACCELERATOR=cpu", p.stdout)
        self.assertIn("\nWIZARD_ACCELERATOR=cpu\n", self.env_file.read_text(encoding="utf-8"))

    def test_a_link_target_that_turns_read_only_during_the_run_is_never_replaced(self):
        """EW1-01, the copy itself: the rehearsal refuses a read-only target before anything is touched, but a target
        can turn read-only DURING a run (here a `cp` double does it just before the copy over the env file). `cp -f`
        then unlinked the LINK and wrote a regular file in its place, rc 0. Plain `cp` fails without touching it: the
        run stops rc 1 saying nothing changed, the link still names the same file, and that file is byte-identical."""
        target = self.home / LINK_TARGET_DIR / "wizard-library-env"
        target.parent.mkdir(parents=True)
        target.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n" + END + "\n", encoding="utf-8")
        target.chmod(0o600)
        self.addCleanup(lambda: target.chmod(0o600))
        self.env_file.symlink_to(target)
        before = target.read_bytes()
        shim = self.tmp / "cp_shim_ro"
        shim.mkdir()
        (shim / "cp").write_text('#!/bin/sh\n# test double: turns the env file\'s target read-only just before the copy over it\n'
                                 'for a in "$@"; do last="$a"; done\n'
                                 '[ "$last" = "%s" ] && chmod 400 "%s"\n'
                                 'exec /bin/cp "$@"\n' % (self.env_file, target), encoding="utf-8")
        os.chmod(str(shim / "cp"), 0o755)
        p = self._envonly(PATH="%s:%s:/usr/local/bin:/usr/bin:/bin" % (shim, self.fake))
        if os.access(str(target), os.W_OK):
            self.skipTest("this user can write a 0400 file (root): the geometry cannot be built")
        self.assertTrue(self.env_file.is_symlink(), "the link was replaced by a regular file")
        self.assertEqual(os.readlink(str(self.env_file)), str(target))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-1500:])
        self.assertIn("failed before it changed a byte", p.stderr)
        # EW2-11: the failure names the next step, by the file the link names -- and that step, taken, completes the run
        self.assertIn("Make %s writable again (chmod u+w '%s'), then re-run this install." % (target, target), p.stderr)
        target.chmod(0o600)
        p = self._envonly(PATH="%s:/usr/local/bin:/usr/bin:/bin" % self.fake)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertTrue(self.env_file.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8").count(BEGIN), 1)
        self.assertIn("WIZARD_LIBRARY_HOME=", target.read_text(encoding="utf-8"))

    def test_a_copy_that_fails_before_writing_says_the_file_was_not_changed(self):
        """EW1-01's restore path without `-f`: a destination that cannot be opened (it became unwritable during the run;
        here a `cp` that fails on the env file without writing a byte) is a failed copy that changed NOTHING. The run
        says so -- it used to say the file 'could NOT be restored' and hand the owner a restore command for a file that
        was never touched."""
        shim = self.tmp / "cp_shim"
        shim.mkdir()
        (shim / "cp").write_text('#!/bin/sh\n# test double: refuses to write the env file, passes every other copy on\n'
                                 'for a in "$@"; do last="$a"; done\n'
                                 '[ "$last" = "%s" ] && { echo "cp: cannot open (test double)" >&2; exit 1; }\n'
                                 'exec /bin/cp "$@"\n' % self.env_file, encoding="utf-8")
        os.chmod(str(shim / "cp"), 0o755)
        self.env_file.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n" + END + "\nAFTER=1\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        before = self.env_file.read_bytes()
        p = self._envonly(PATH="%s:%s:/usr/local/bin:/usr/bin:/bin" % (shim, self.fake))
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-1500:])
        self.assertIn("failed before it changed a byte", p.stderr)
        self.assertNotIn("restore it by hand", p.stderr)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_the_block_writes_no_cuda_pin_over_an_owners_wizard_cuda_device(self):
        for owner, want in (("WIZARD_CUDA_DEVICE=1\n", "1|"), ("WIZARD_CUDA_DEVICE=\n", "None|")):
            self._owner(owner)
            # the invoking shell pins card 0: the block used to write CUDA_VISIBLE_DEVICES='0', outranking the owner
            p = self._install("--env-only", WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                              CUDA_VISIBLE_DEVICES="0", WIZARD_CUDA_DEVICE="")
            self.assertEqual(p.returncode, 0, (owner, (p.stdout + p.stderr)[-1500:]))
            block = self._block()
            self.assertEqual([l for l in block.splitlines() if l.startswith("CUDA_VISIBLE_DEVICES=")], [], (owner, block))
            self.assertIn("# CUDA_VISIBLE_DEVICES is not written: WIZARD_CUDA_DEVICE is assigned OUTSIDE this block", block)
            self.assertTrue(self.env_file.read_text(encoding="utf-8").startswith(owner), "the owner's line did not survive")
            got = self._units_device()
            self.assertTrue(got.startswith(want), "owner %r: the units resolve %r, not the owner's word" % (owner, got))
            self.assertNotIn("(override with WIZARD_CUDA_DEVICE)", p.stdout, "the installer told an owner who SET it to set it")

    def test_the_installers_own_heuristic_card_is_named_as_its_own_under_an_owners_do_not_pin(self):
        self._owner("WIZARD_CUDA_DEVICE=\n")
        p = self._install("--env-only", WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertIn("for this run's own steps only: the owner's WIZARD_CUDA_DEVICE= in %s is EMPTY ('do not pin'), "
                      "so the units stay unpinned" % self.env_file, p.stdout)


# ============================================================================================ G1 -- the owner's GPU choice
def _drive_tty(cmd, env, answers, cwd, timeout=600, ask_wire=False, ask_approvals=False):
    """Run `cmd` with a SCRIPTED TERMINAL as its controlling tty (a pty: the child is a session leader whose /dev/tty is
    the pty's slave) while its stdin is a PIPE that delivers nothing -- an answer can reach it only through /dev/tty.
    `answers`: [(regex, text), ...], each regex awaited in the terminal's output after the previous match, then text +
    newline typed. Returns (rc, tty_text, stdout, stderr, unanswered). The child's PATH decides what it runs; nothing
    real is queried here."""
    import fcntl
    import pty
    import select
    import signal
    import termios
    import threading
    import time
    # STEP P's hook question (the Persona Forge) is the LAST thing a first install asks at a terminal. These drivers
    # script the questions their own tests are about; a run that never says --wire-hooks answers it NO by flag, so
    # no driver waits on a question it was not written for. test_persona_forge drives the question itself.
    cmd = list(cmd)
    if not ask_wire and any(str(c).endswith("install.sh") for c in cmd) \
            and not {"--wire-hooks", "--no-wire-hooks"} & set(map(str, cmd)):
        cmd.append("--no-wire-hooks")
    # The approvals question (2026-10-26) is asked on every interactive install with no recorded choice. A driver not
    # written for it answers it by flag (manual: changes nothing a test about something else could observe);
    # test_approvals_policy_d26 drives the question itself.
    if not ask_approvals and any(str(c).endswith("install.sh") for c in cmd) and "--approvals" not in map(str, cmd):
        cmd += ["--approvals", "manual"]
    master, slave = pty.openpty()
    name = os.ttyname(slave)

    def pre():
        os.setsid()
        fd = os.open(name, os.O_RDWR)
        fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
        os.close(fd)
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         cwd=cwd, preexec_fn=pre)
    os.close(slave)
    p.stdin.close()
    bufs = {"stdout": b"", "stderr": b""}

    def pump(k, f):
        for chunk in iter(lambda: f.read1(4096), b""):
            bufs[k] += chunk
    ths = [threading.Thread(target=pump, args=(k, getattr(p, k)), daemon=True) for k in bufs]
    for t in ths:
        t.start()
    tty, pos, steps, end = b"", 0, list(answers), time.monotonic() + timeout
    while True:
        if time.monotonic() > end:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            break
        alive = p.poll() is None
        r, _w, _x = select.select([master], [], [], 0.1)
        eof = False
        if r:
            try:
                data = os.read(master, 4096)
            except OSError:            # EIO: every holder of the terminal's slave side has closed it
                data = b""
            eof = not data
            tty += data
        if steps:
            m = re.compile(steps[0][0].encode(), re.S).search(tty, pos)
            if m:
                pos = m.end()
                os.write(master, steps[0][1].encode() + b"\n")
                steps.pop(0)
                continue
        if not alive and (not r or eof):
            break
    for t in ths:
        t.join(10)
    os.close(master)
    return p.returncode, tty.decode("utf-8", "replace").replace("\r", ""), bufs["stdout"].decode(), bufs["stderr"].decode(), steps


Q_MONITOR = r"Which card drives your monitor\(s\)\? \(a card number, or 'none'\)"
Q_WORK = r"Which card should the brain's background jobs use\? \(a card number\)"


class TestG1TheOwnersGpuChoice(_AmbientHome):
    """Env-file writer pass, iteration 5, G1 (the owner's ruling: "declared and recorded, never inferred"). On a host with
    two or more GPUs and NOTHING declared the installer never picks the card: at a terminal (reached through /dev/tty even
    when stdin is a pipe) it shows every card and asks two questions -- re-asked on a blank or unknown answer, the monitor's
    card confirmed by typing it again -- and records the answers as the OWNER's declarations outside the managed block;
    with no terminal it takes --display-gpu / --gpu, and without them it is refused before anything is touched. A
    declaration is never asked again; one GPU and no GPU are unchanged; the invoking shell's pin variables answer nothing.
    Every run here uses a FAKE nvidia-smi (--env-only / --dry-run: no GPU is touched)."""

    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("the scripted terminal is a Linux pty")
        self.env_file.parent.mkdir(parents=True, exist_ok=True)

    def _fake(self, states):
        d = self.tmp / ("fake_%s" % "_".join("%s%s" % (i, s[0]) for i, s in sorted(states.items(), key=lambda x: int(x[0]))))
        if not (d / "nvidia-smi").exists():
            _fake_nvidia_smi(d, states)
        return str(d) + ":/usr/local/bin:/usr/bin:/bin"

    def _drive(self, states, answers, *args, **extra):
        env = self._env(PATH=self._fake(states), WIZARD_EMBED_MODEL="nomic", **extra)
        return _drive_tty(["bash", str(SH), "--env-only"] + list(args), env, answers, str(self.tmp))

    def _units_card(self):
        """The card every unit reads from the env file AS WRITTEN: systemd's reading of the whole file (install/env_file.py,
        bound to libsystemd by test_env_file_reader.py) through the units' own resolver."""
        p = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "env_file.py"), "--file", str(self.env_file),
                            "effective-pin"], capture_output=True, text=True, timeout=60)
        return p.stdout.strip() if p.returncode == 0 else "<rc %d>" % p.returncode

    def _outside(self):
        t = self.env_file.read_text(encoding="utf-8")
        return t[:t.index(BEGIN)] + t[t.index(END) + len(END):]

    def test_1_two_cards_asleep_the_owner_answers_at_a_terminal_and_it_is_his_declaration(self):
        asleep = {"0": "Disabled", "1": "Disabled"}
        rc, tty, out, err, left = self._drive(asleep, [(Q_MONITOR, ""), (Q_MONITOR, "7"), (Q_MONITOR, "0"),
                                                       (Q_WORK, "0"), (r"type 0 again", "1"), (Q_WORK, "1")])
        self.assertEqual((rc, left), (0, []), (tty[-1500:], out[-1500:], err[-1500:]))
        for want in ("This host has 2 GPUs", "  0     Fake 2080 Ti", "  1     Fake 2080 Ti", "11264 MiB", "Disabled",
                     "An answer is needed here", "'7' is not one of the cards listed above (0, 1), nor 'none'.",
                     "Careful: card 0 is the card you said drives your monitor(s)", "Not confirmed -- choose again."):
            self.assertIn(want, tty)
        outside = self._outside()
        self.assertRegex(outside, r"(?m)^# WIZARD_DISPLAY_INDEX: chosen at install \d{4}-\d{2}-\d{2} \(answered at the terminal\)"
                                  r".*\nWIZARD_DISPLAY_INDEX=0\n# WIZARD_CUDA_DEVICE: chosen at install \d{4}-\d{2}-\d{2} "
                                  r"\(answered at the terminal\).*\nWIZARD_CUDA_DEVICE=1\n")
        self.assertNotIn("CUDA_VISIBLE_DEVICES=", self.env_file.read_text(encoding="utf-8").split(BEGIN)[1].split(END)[0])
        self.assertEqual(self._units_card(), "1", "systemd's reading of the file does not hand the units the chosen card")
        self.assertIn("recorded your answers in %s" % self.env_file, out)
        self.assertIn("chosen at install", out)       # the resolver names the declaration's provenance

    def test_2_five_cards_scale(self):
        five = dict((str(i), "Disabled") for i in range(5))
        rc, tty, out, err, left = self._drive(five, [(Q_MONITOR, "5"), (Q_MONITOR, "3"), (Q_WORK, "4")])
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn("This host has 5 GPUs", tty)
        for i in range(5):
            self.assertRegex(tty, r"(?m)^  %d     Fake 2080 Ti" % i)
        self.assertIn("'5' is not one of the cards listed above (0, 1, 2, 3, 4), nor 'none'.", tty)
        self.assertIn("\nWIZARD_DISPLAY_INDEX=3\n", self._outside())
        self.assertEqual(self._units_card(), "4")

    def test_3_the_monitor_card_needs_its_number_typed_again(self):
        awake = {"0": "Enabled", "1": "Disabled"}
        rc, tty, out, err, left = self._drive(awake, [(Q_MONITOR, "none"), (Q_WORK, "0"), (r"type 0 again", "0")])
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn("card 0 reports display_active=Enabled: it drives a display right now", tty)
        self.assertIn("\nWIZARD_DISPLAY_INDEX=none\n", self._outside())
        self.assertEqual(self._units_card(), "0")

    def test_4_no_terminal_and_no_flags_is_refused_and_touches_nothing(self):
        asleep = {"0": "Disabled", "1": "Disabled"}
        before = _tree(self.home)
        p = self._install("--env-only", PATH=self._fake(asleep), WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        for want in ("This host has 2 GPUs:", "[install]   0     Fake 2080 Ti", "[install]   1     Fake 2080 Ti",
                     "Pass the answer as --display-gpu <card|none> --gpu <card>.",
                     "An installing AI agent must put this question to the human and pass the human's answer",
                     "it must never choose the answer itself", "Nothing was touched."):
            self.assertIn(want, p.stderr)
        self.assertEqual(_tree(self.home), before, "a refused run changed the home")

    def test_5_the_flags_are_the_answers_where_no_terminal_can_ask(self):
        awake = {"0": "Enabled", "1": "Disabled"}
        path = self._fake(awake)
        for args, want in ((("--gpu", "1"), "Pass the answer as --display-gpu <card|none>."),
                           (("--gpu", "7", "--display-gpu", "0"), "--gpu 7 names no card nvidia-smi lists (0, 1)"),
                           (("--gpu", "0", "--display-gpu", "none"), "Choosing it needs the owner's TYPED confirmation"),
                           (("--gpu", "x1"), "--gpu takes a card number")):
            before = _tree(self.home)
            p = self._install("--env-only", *args, PATH=path, WIZARD_EMBED_MODEL="nomic")
            self.assertEqual(p.returncode, 2, (args, p.stderr[-1500:]))
            self.assertIn(want, p.stderr, args)
            self.assertEqual(_tree(self.home), before, "%r changed the home" % (args,))
        p = self._install("--env-only", "--gpu", "1", "--display-gpu", "0", PATH=path, WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("(from --gpu 1 --display-gpu 0)", self._outside())
        self.assertEqual(self._units_card(), "1")

    def test_6_a_declaration_is_never_asked_again(self):
        asleep = {"0": "Disabled", "1": "Disabled"}
        rc, tty, out, err, left = self._drive(asleep, [(Q_MONITOR, "0"), (Q_WORK, "1")])
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        declared = self._outside()
        rc, tty, out, err, left = self._drive(asleep, [])            # an upgrade: a terminal, and nothing to answer
        self.assertEqual(rc, 0, (tty[-800:], err[-1500:]))
        self.assertNotIn("Which card", tty, "a declared card was asked again")
        self.assertEqual(self._outside(), declared, "the owner's declaration changed on a re-run")
        self.assertRegex(out, r"GPU device:   1 -- declared by WIZARD_CUDA_DEVICE in .*\(outside the managed block; chosen "
                              r"at install \d{4}-\d{2}-\d{2}, answered at the terminal\)")
        p = self._install("--env-only", "--gpu", "0", PATH=self._fake(asleep), WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 2, p.stderr[-1500:])
        self.assertIn("A flag never overwrites a declaration", p.stderr)
        self.assertEqual(self._outside(), declared)

    def test_7_one_gpu_and_no_gpu_ask_nothing(self):
        for states, line in (({"0": "Disabled"}, None),
                             ({"0": "Enabled"}, "GPU device:   0 -- the only GPU, and it drives a display: the brain's "
                                                "background jobs share it with your desktop.")):
            if self.env_file.exists():
                self.env_file.unlink()
            rc, tty, out, err, left = self._drive(states, [])
            self.assertEqual(rc, 0, err[-1500:])
            self.assertNotIn("Which card", tty)
            self.assertEqual(self._units_card(), "0")
            if line:
                self.assertIn(line, out)
            else:
                self.assertNotIn("drives a display", out)
        nogpu = self.tmp / "no_gpu_driver"
        nogpu.mkdir()
        (nogpu / "nvidia-smi").write_text("#!/bin/sh\necho 'NVIDIA-SMI has failed (test double)' >&2\nexit 9\n", encoding="utf-8")
        os.chmod(str(nogpu / "nvidia-smi"), 0o755)
        self.env_file.unlink()
        env = self._env(PATH=str(nogpu) + ":/usr/local/bin:/usr/bin:/bin", WIZARD_EMBED_MODEL="nomic")
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--env-only"], env, [], str(self.tmp))
        self.assertEqual(rc, 0, err[-1500:])
        self.assertNotIn("Which card", tty)
        self.assertEqual(self._units_card(), "<rc 1>", "no GPU: no pin reaches the units")

    def test_8_the_invoking_shell_answers_nothing(self):
        asleep = {"0": "Disabled", "1": "Disabled"}
        p = self._install("--env-only", PATH=self._fake(asleep), WIZARD_EMBED_MODEL="nomic",
                          CUDA_VISIBLE_DEVICES="1", WIZARD_CUDA_DEVICE="1")
        self.assertEqual(p.returncode, 2, "a shell's WIZARD_CUDA_DEVICE answered the question: %r" % p.stdout[-800:])
        self.assertFalse(self.env_file.exists())
        self._owner_declares("1")
        p = self._install("--env-only", PATH=self._fake(asleep), WIZARD_EMBED_MODEL="nomic",
                          CUDA_VISIBLE_DEVICES="0", WIZARD_CUDA_DEVICE="0")
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        self.assertEqual(self._units_card(), "1")
        self.assertIn("the invoking shell's pin variables (CUDA_VISIBLE_DEVICES='0' WIZARD_CUDA_DEVICE='0') are not a "
                      "declaration: this installer writes nothing from them, and neither this run nor the units use them. "
                      "This run and the units use card 1", p.stdout)

    def test_9_a_dry_run_shows_the_answers_and_writes_nothing(self):
        asleep = {"0": "Disabled", "1": "Disabled"}
        self._brain()
        before = _tree(self.home)
        p = self._install("--dry-run", "--display-gpu", "0", "--gpu", "1", PATH=self._fake(asleep),
                          WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("DRY: would record your answers in %s" % self.env_file, p.stdout)
        self.assertIn("GPU device:   1 -- declared by WIZARD_CUDA_DEVICE", p.stdout)
        self.assertEqual(_tree(self.home), before, "a dry run wrote something")


class TestIteration4TheInstallersCarriedItems(_AmbientHome):
    """Env-file writer pass, iteration 4 -- the carried items, each red on the iteration-3 bytes:
      EW2-12  a HOME holding a ' : the voidloom CLI's `ln -sf` ran through `eval`, failed, and a success line followed;
      EW3-06  an env DIRECTORY that is a file, or a link to one, is named as such;
      O1      the units' pin never comes from the invoking shell (its CUDA_VISIBLE_DEVICES; WIZARD_PRECOG_CUDA_DEVICE);
      O3      P4's refusal of a pin declared by CUDA_VISIBLE_DEVICES alone says exactly what to add, and where;
      W2'     the write-set declares a SYMLINKED brain's target."""

    def setUp(self):
        super().setUp()
        self.states = {"0": "Enabled", "1": "Disabled"}          # card 0 drives the display
        self.fake = self.tmp / "fake_driver_enabled0"
        _fake_nvidia_smi(self.fake, self.states)
        self.free = [i for i in sorted(self.states, key=int) if self.states[i] == "Disabled"][0]
        self.display = [i for i in sorted(self.states, key=int) if self.states[i] == "Enabled"][0]

    def test_a_home_holding_a_quote_gets_the_cli_on_path_or_a_named_failure_never_a_false_success(self):
        """EW2-12 (iteration-2 reflection, executed): HOME=.../o'brien, the brain and the env file elsewhere (quote-free,
        so the block reads one way and W5 has nothing to refuse): `run "ln -sf '...' '...'"` hit eval's quote -- no link,
        and 'voidloom CLI symlinked onto PATH' printed. Now: the link is made; and when it cannot be, the run stops
        naming it."""
        home = self.tmp / "o'brien"
        for sub in ("tmp", ".config", ".cache", ".local/share", ".local/state"):
            (home / sub).mkdir(parents=True, exist_ok=True)
        ch = self.tmp / "ch" / ".claude"
        env = self._env(PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin", CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        env.update(HOME=str(home), WIZARD_CLAUDE_HOME=str(ch), TMPDIR=str(home / "tmp"),
                   WIZARD_ENV_FILE=str(self.tmp / "ch" / ".voidloom" / "wizard-library-env"),
                   XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
                   XDG_DATA_HOME=str(home / ".local" / "share"), XDG_STATE_HOME=str(home / ".local" / "state"))
        p = subprocess.run(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu"], capture_output=True, text=True,
                           env=env, timeout=900, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, (p.stdout[-800:], p.stderr[-1500:]))
        link = home / ".local" / "bin" / "voidloom"
        self.assertTrue(link.is_symlink(), "no voidloom link under the quoted HOME: %s" % p.stderr[-600:])
        self.assertEqual(os.readlink(str(link)), str(ch / "memory" / "voidloom" / "voidloom"))
        self.assertIn("voidloom CLI symlinked onto PATH: %s" % link, p.stdout)
        self.assertNotIn("unexpected EOF", p.stderr)
        self.assertEqual(sorted(x.name for x in self.tmp.iterdir() if x.is_symlink()), [], "a stray link in the cwd")
        # the same home where the link CANNOT be made (~/.local/bin is a file): the run stops, naming it -- no success line
        link.unlink()
        (home / ".local" / "bin").rmdir()
        (home / ".local" / "bin").write_text("not a directory\n", encoding="utf-8")
        p = subprocess.run(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu"], capture_output=True, text=True,
                           env=env, timeout=900, cwd=str(self.tmp))
        self.assertNotEqual(p.returncode, 0, p.stdout[-800:])
        self.assertIn("could not put the voidloom CLI on PATH at %s" % link, p.stderr)
        self.assertNotIn("voidloom CLI symlinked onto PATH", p.stdout)

    def test_an_env_directory_that_is_a_file_or_a_link_to_one_is_named_as_such(self):
        """EW3-06 (iteration-3 reflection, executed): the env file's DIRECTORY is a regular file, or a symlink to one --
        rc 2, nothing laid (right), but the message said only 'nearest existing: ...' and 'Fix that directory'."""
        d = self.env_file.parent
        shutil.rmtree(str(d), ignore_errors=True)
        afile = self.home / "afile"
        afile.write_text("x\n", encoding="utf-8")
        for make, want in ((lambda: d.symlink_to(afile), "a symlink to %s, which is not a directory" % afile),
                           (lambda: d.write_text("x\n", encoding="utf-8"), "(nearest existing: %s, which is not a directory)" % d)):
            if d.is_symlink() or d.exists():
                d.unlink()
            make()
            p = self._install("--init-blank", "--tier0-only", "--force-cpu", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                              CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
            self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
            self.assertIn(want, p.stderr)
            self.assertFalse((self.home / ".claude").exists(), "files were laid before the refusal")

    def _block_cvd(self):
        t = self.env_file.read_text(encoding="utf-8")
        blk = t[t.index(BEGIN):t.index(END)]
        return [l.split("=", 1)[1].strip("'") for l in blk.splitlines() if l.startswith("CUDA_VISIBLE_DEVICES=")]

    def test_the_invoking_shells_cuda_visible_devices_is_never_the_units_pin(self):
        """O1: the block's CUDA_VISIBLE_DEVICES is EVERY unit's durable pin. It used to be the invoking shell's
        CUDA_VISIBLE_DEVICES -- a shell exporting 0 for some other tool pinned every unit to the display card on a
        two-card host with no declaration. The shell's value is an ambient selection, never a declaration: since G1
        (iteration 5) it answers nothing -- with no terminal and no flags the run is REFUSED, the env file untouched;
        with the owner's answers (--gpu/--display-gpu) the units read HIS card and the run says the shell's was ignored."""
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("OWNER_LINE=1\n", encoding="utf-8")
        before = self.env_file.read_bytes()
        env = dict(WIZARD_EMBED_MODEL="nomic", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin",
                   CUDA_VISIBLE_DEVICES=self.display, WIZARD_CUDA_DEVICE="")
        p = self._install("--env-only", **env)
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-1500:])
        self.assertIn("declares which one the brain's background jobs use", p.stderr)
        self.assertEqual(self.env_file.read_bytes(), before, "a refused run changed the env file")
        p = self._install("--env-only", "--display-gpu", self.display, "--gpu", self.free, **env)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertEqual(self._block_cvd(), [], "the block wrote a pin over the owner's declaration")
        text = self.env_file.read_text(encoding="utf-8")
        self.assertIn("\nWIZARD_CUDA_DEVICE=%s\n" % self.free, text)
        self.assertIn("\nWIZARD_DISPLAY_INDEX=%s\n" % self.display, text)
        self.assertIn("the invoking shell's pin variables (CUDA_VISIBLE_DEVICES='%s' WIZARD_CUDA_DEVICE='') are not a "
                      "declaration" % self.display, p.stdout)

    def test_install_crons_renders_no_per_unit_pin_from_the_invoking_shell(self):
        """O1, the unit renderer: install_crons.sh rendered `Environment=CUDA_VISIBLE_DEVICES=${WIZARD_PRECOG_CUDA_DEVICE}`
        into the pre-cognitive unit from the INVOKING SHELL (measured by the reference-host round: '0' beside an env file
        declaring WIZARD_CUDA_DEVICE=1 -- that unit on the display card). Every unit takes its pin from the env file."""
        crons = ROOT / "install" / "install_crons.sh"
        env = self._env(PATH="/usr/local/bin:/usr/bin:/bin", CUDA_VISIBLE_DEVICES=self.display, WIZARD_CUDA_DEVICE=self.display)
        env.update(WIZARD_LIBRARY_HOME=str(self.brain), WIZARD_PYTHON_BIN=self.py, WIZARD_CRON_ONLY="pre-cognitive",
                   WIZARD_PRECOG_CUDA_DEVICE=self.display)
        p = subprocess.run(["bash", str(crons), "--dry-run"], capture_output=True, text=True, env=env, timeout=300,
                           cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertIn("DRY-UNIT: pre-cognitive.service", p.stdout, "the pre-cognitive unit was not rendered at all")
        self.assertNotIn("Environment=CUDA_VISIBLE_DEVICES", p.stdout, "a unit pin rendered from the invoking shell")
        self.assertIn("WIZARD_PRECOG_CUDA_DEVICE is set in this shell and is NOT used", p.stderr)

    def test_p4_names_exactly_what_to_add_for_a_pin_declared_by_cuda_visible_devices_alone(self):
        """O3: the machine manifest records the card this machine DECLARES (voidloom_device.declared_device(): the
        WIZARD_CUDA_DEVICE declaration, never CUDA_VISIBLE_DEVICES -- by design), so an env file pinning the units with
        CUDA_VISIBLE_DEVICES alone failed P4 with 'the pin 1 is not the machine manifest's compute_index '0''. The refusal
        now names the line to add and where; the line, added LITERALLY as the words give it, clears that refusal. Run
        without initialising CUDA: the probe's own CUDA_VISIBLE_DEVICES is left EMPTY, so P4 stops at its internal pin
        check, AFTER the compute_index and display checks -- no card is touched."""
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("CUDA_VISIBLE_DEVICES=%s\n" % self.free, encoding="utf-8")
        args = ["--db", str(self.tmp / "no.db"), "--organs", str(ROOT / "organs"), "--manifest",
                str(self.brain / "voidloom_manifest.json"), "--pin", self.free, "--pin-declared-by", "CUDA_VISIBLE_DEVICES",
                "--pin-source", "declared by CUDA_VISIBLE_DEVICES in %s (outside the managed block)" % self.env_file,
                "--env-file", str(self.env_file)]
        tool = [self.py, "-B", str(ROOT / "install" / "tier1_probe.py")]
        env = self._env(PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin", CUDA_VISIBLE_DEVICES="")
        env.pop("WIZARD_CUDA_DEVICE")
        # through `run` -- install.sh's own path, which starts each step as a child and must hand it the env file (the
        # first draft of this fix forwarded it only to `step`: the words said 'the env file', measured by the gate)
        p = subprocess.run(tool + ["run", "--steps", "P4"] + args, capture_output=True, text=True, env=env, timeout=300,
                           cwd=str(self.tmp))
        said = p.stdout + p.stderr
        self.assertIn("is not the machine manifest's compute_index", said)
        m = re.search(r"add this line to (\S+), on a line of its own OUTSIDE the managed block \(above the line "
                      r"'# >>> wizard-library managed >>>' or below the line '# <<< wizard-library managed <<<'\): "
                      r"(WIZARD_CUDA_DEVICE=\d+)", said)
        self.assertIsNotNone(m, "the refusal does not say exactly what to add, and where: %s" % said[-900:])
        self.assertEqual(m.group(1).rstrip(","), str(self.env_file))
        with open(m.group(1).rstrip(","), "a", encoding="utf-8") as fh:
            fh.write(m.group(2) + "\n")                                    # the words, followed literally
        # `step` directly, with this probe's CUDA_VISIBLE_DEVICES left EMPTY: P4 stops at its last check before CUDA
        p = subprocess.run(tool + ["step", "P4"] + args, capture_output=True, text=True, env=env, timeout=300,
                           cwd=str(self.tmp))
        said = p.stdout + p.stderr
        self.assertNotIn("is not the machine manifest's compute_index", said,
                         "the advice, followed, did not clear the compute_index refusal")
        self.assertIn("internal: CUDA_VISIBLE_DEVICES=''", said, "P4 did not reach its last check before CUDA")

    def test_the_write_set_declares_a_symlinked_brains_target(self):
        """W2's twin for the BRAIN: ~/.claude/memory a symlink to another directory -- every file the install lays or
        writes into the brain lands beneath the TARGET, outside the target root; the write-set did not declare it (the
        harness proof, through outside_target_diff.py, is the gate's)."""
        elsewhere = self.home / ".brain-elsewhere"
        elsewhere.mkdir()
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.brain.symlink_to(elsewhere)
        p = self._install("--print-write-set", PATH=str(self.fake) + ":/usr/local/bin:/usr/bin:/bin")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        d = json.loads(p.stdout)
        self.assertEqual(d["resolved"]["BRAIN_DIR_TARGET"], str(elsewhere))
        self.assertIn(str(elsewhere) + "/**", [w["path"] for w in d["declared"] if w["active"]])


class TestTheAmbientShapeHasNoCpuFallback(_AmbientHome):
    """Iteration 3, item 1 (HIGH), ruled by VL-REF-v1.2: the ambient shape PROVES the GPU the units will use
    (P4) or it does not run. `--force-cpu` moved nothing -- the units run the host's own CUDA torch and the
    block wrote no CUDA line -- yet P4 was SKIPPED with the claim "the units run the embedder on the CPU",
    and install.sh set FORCE_CPU ITSELF when it could not tell two cards apart (measured: the units then
    resolved card 0). Both are now refusals, rc 2 by name; the probe's skip no longer claims anything."""

    def test_force_cpu_is_refused_by_name(self):
        self._brain()
        pin = self._fake_pin()
        before = _tree(self.home)
        p = self._install("--tier1-ambient", self.py, "--force-cpu", "--dry-run", **pin)
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn("--force-cpu contradicts an ambient Tier 1", p.stderr)
        self.assertIn("Nothing was touched", p.stderr)
        self.assertEqual(_tree(self.home), before)

    def test_cards_that_cannot_be_told_apart_are_a_refusal_not_a_cpu_fallback(self):
        self._brain()
        fake = self.tmp / "both_enabled"
        _fake_nvidia_smi(fake, {"0": "Enabled", "1": "Enabled"})
        before = _tree(self.home)
        p = self._install("--tier1-ambient", self.py, "--dry-run", PATH=str(fake) + ":/usr/local/bin:/usr/bin:/bin",
                          CUDA_VISIBLE_DEVICES="", WIZARD_CUDA_DEVICE="")
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        # G1 (iteration 5): two cards and nothing declared -- no terminal, no flags: refused, the cards listed, the flags
        # named; never a CPU fallback, and never the venv shape's --force-cpu advice
        self.assertIn("declares which one the brain's background jobs use", p.stderr)
        self.assertIn("--display-gpu <card|none> --gpu <card>", p.stderr)
        self.assertNotIn("--force-cpu to install the CPU tier", p.stderr, "the venv shape's advice names a refused flag here")
        self.assertEqual(_tree(self.home), before)

    def test_the_probes_skip_claims_nothing_about_the_units(self):
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "tier1_probe.py"), "step", "P4", "--db",
                            str(self.tmp / "unused.db"), "--organs", str(ROOT / "organs"), "--force-cpu"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        skipped = json.loads(p.stdout.strip().splitlines()[-1])["facts"]["skipped"]
        self.assertIn("P4 NOT run -- this proves nothing about the units' device", skipped)
        self.assertNotIn("the units run the embedder on the CPU", skipped)


class TestTheShapeGateRefusesWhatSystemdCannotRun(_AmbientHome):
    """Iteration 3, items 6 and 12. A path with whitespace passed the gate and every proof; Step F then
    rendered 8 units whose ExecStart= splits on the space and ENABLED their timers before the read-back
    failed the run. And an owner's `export WIZARD_DISPLAY_INDEX=0` -- honoured, since only this installer
    reads that key -- drew a warning that systemd ignores it."""

    def test_an_interpreter_path_with_whitespace_is_refused(self):
        self._brain()
        d = self.tmp / "py dir"
        d.mkdir()
        (d / "python3").symlink_to(self.py)
        pin = self._fake_pin()
        before = _tree(self.home)
        p = self._install("--tier1-ambient", str(d / "python3"), "--dry-run", **pin)
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn("contains whitespace: systemd splits ExecStart= on it", p.stderr)
        self.assertEqual(_tree(self.home), before)

    def test_an_exported_display_declaration_is_not_a_declaration_and_is_named(self):
        """Iteration 3's item 12 honoured `export WIZARD_DISPLAY_INDEX=0` with a reader of its own (`get` stripped the
        export). Iteration 4 reads the file through ONE reader with systemd's rules (HIGH 1, done as a class), and
        under those rules the line assigns nothing -- so it is not a declaration: the ambient shape, whose P4 it
        would feed, REFUSES it naming the line and the one-word fix; another shape (which never reads it) names it,
        once, as a warning. Before, a shell and the installer read a declaration the file's own consumer drops."""
        self._brain()
        self.env_file.parent.mkdir(parents=True)
        owner = "export WIZARD_DISPLAY_INDEX=0\nexport SOME_OTHER_KEY=x\n"
        self.env_file.write_text(owner + BEGIN + "\n" + END + "\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        pin = self._fake_pin()
        before = _tree(self.home)
        p = self._install("--tier1-ambient", self.py, "--dry-run", **pin)
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn("line 1: WIZARD_DISPLAY_INDEX -- it is written with `export`", p.stderr)
        self.assertIn("it is NOT a declaration", p.stderr)
        self.assertIn("Write it as a bare WIZARD_DISPLAY_INDEX=<value> line", p.stderr)
        self.assertIn("Nothing was touched", p.stderr)
        self.assertEqual(_tree(self.home), before)
        # the venv shape never reads the key: named once, as the other export line is -- and the run goes on
        p = self._install("--env-only", "--force-cpu", WIZARD_EMBED_MODEL="nomic")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertEqual(p.stderr.count("WIZARD_DISPLAY_INDEX -- it is written with `export`"), 1, p.stderr[-1500:])
        self.assertEqual(p.stderr.count("'export SOME_OTHER_KEY' is written with `export`; systemd ignores that line"), 1)
        self.assertTrue(self.env_file.read_text(encoding="utf-8").startswith(owner))

    def test_an_interpreter_path_with_a_percent_is_refused(self):
        """LOW 2: systemd EXPANDS '%' in ExecStart= (the iteration-3 reflector, offline with systemd-analyze verify:
        ExecStart=<dir>/py%hdir/python3 ran <dir>/py<home>dir/python3); the gate accepted it and a wet run rendered
        and enabled every unit before anything could disagree."""
        self._brain()
        d = self.tmp / "py%hdir"
        d.mkdir()
        (d / "python3").symlink_to(self.py)
        pin = self._fake_pin()
        before = _tree(self.home)
        p = self._install("--tier1-ambient", str(d / "python3"), "--dry-run", **pin)
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn("contains '%': systemd expands '%' as a unit specifier", p.stderr)
        self.assertEqual(_tree(self.home), before)

    def test_a_brain_path_with_a_question_mark_is_refused_and_creates_nothing_outside_it(self):
        """LOW 2: every read of the brain spliced its path raw into `file:<path>?mode=ro`; a '?' in the path ended it
        there, and SQLite opened -- read-write-CREATE -- the prefix (measured: a DRY RUN created an empty file
        <scratch>/h and refused a valid brain for a false reason). The path is refused before any read, and every
        installer URI is escaped besides."""
        home = self.tmp / "h?x"
        brain = home / ".claude" / "memory"
        brain.mkdir(parents=True)
        shutil.copy2(str(SEED_DB), str(brain / "claude_memory.db"))
        env = self._fake_pin()
        env["WIZARD_CLAUDE_HOME"] = str(home / ".claude")
        p = self._install("--tier1-ambient", self.py, "--dry-run", **env)
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn("WIZARD_CLAUDE_HOME must not contain '%', '?' or '#'", p.stderr)
        self.assertFalse((self.tmp / "h").exists(), "a file was created outside the brain dir")

    def test_whitespace_in_a_path_is_refused_only_where_a_unit_line_splits_on_it(self):
        """LOW 2's class, bounded by the consumer: ExecStart= splits on whitespace, so a brain path holding a space is
        refused when this run renders systemd units -- and nowhere else (a home with a space still installs
        without crons: launchd plists and the env file quote it)."""
        if sys.platform != "linux":
            self.skipTest("systemd units are rendered on Linux only")
        env = self._fake_pin()
        env["WIZARD_CLAUDE_HOME"] = str(self.tmp / "my home" / ".claude")
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", "--with-crons", "--dry-run", **env)
        self.assertEqual(p.returncode, 2, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn("WIZARD_CLAUDE_HOME must not contain whitespace (or '%') when --with-crons renders systemd units", p.stderr)
        p = self._install("--init-blank", "--tier0-only", "--force-cpu", "--dry-run", **env)
        self.assertEqual(p.returncode, 0, (p.stdout[-1200:], p.stderr[-1200:]))


# Writer A of the SC5 case (the iteration-2 reflector's, no crash): it opens the brain DURING the installer's
# run -- on the awk double's signal, i.e. after the installer took its "which sidecars existed" snapshot --
# commits five rows, HOLDS, then commits five more once B has opened, and closes cleanly.
_SC5_WRITER_A = r"""
import os, sqlite3, sys, time
db, d = sys.argv[1], sys.argv[2]
deadline = time.time() + 300
def wait(name):
    while not os.path.exists(os.path.join(d, name)):
        if time.time() > deadline:
            sys.exit(3)
        time.sleep(0.01)
wait("gate.signalled")
c = sqlite3.connect(db, timeout=30)
for i in range(5):
    c.execute("INSERT INTO memories (content, category, source_project) VALUES (?, 'insight', 'sc5-A')", ("A1 %d" % i,))
c.commit()
open(os.path.join(d, "a.ready"), "w").write("1")
wait("go_a2")
for i in range(5):
    c.execute("INSERT INTO memories (content, category, source_project) VALUES (?, 'insight', 'sc5-A')", ("A2 %d" % i,))
c.commit()
c.close()
open(os.path.join(d, "a.done"), "w").write("1")
"""


class TestTheSidecarGuardNeverDeletesALiveWriter(_AmbientHome):
    """Iteration 3, item 2 (HIGH), ruled by VL-REF-v1.2 (memory #21838): the installer never unlinks a -wal/-shm.
    The guard (CRUCIBLE-3 D9-F11, widened in iteration 2 to refusals and --print-write-set) removed every
    sidecar that did not exist when the run started -- but a writer that opens the brain DURING the run has
    no sidecar before it either. Measured by the iteration-2 reflector with NO crash: writer A commits during
    a dry run, the dry run deletes the files, writer B opens fresh ones, both close cleanly -> A's 10 of 10
    rows are gone and integrity_check says 'ok'; the base c6d7af2 dry run reproduced it. SQLite now retires
    its own files (a read-write open + close, which deletes them only as the LAST connection).

    The instrument is DETERMINISTIC: an `awk` double first on PATH. install.sh's first awk call after its
    sidecar snapshot is record_get (an INSTALL_RECORD is present), and the double holds it until A has
    committed -- so A is, by construction, a connection that appeared during the run. The brain starts in
    WAL mode with NO sidecars (the live brain's state at 23:19Z on 2026-09-25)."""

    def _wal_brain(self):
        self._brain()
        c = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal", "premise: a WAL brain")
        finally:
            c.close()
        self.assertEqual(self._sidecars(), [], "premise: no sidecar at the start")
        (self.brain / "INSTALL_RECORD").write_text("shape=ambient\ninterpreter=%s\n" % self.py, encoding="utf-8")

    def _sidecars(self):
        return sorted(p.name for p in self.brain.glob("claude_memory.db-*") if p.name.endswith(("-wal", "-shm")))

    def _awk_double(self, d):
        real = shutil.which("awk", path="/usr/local/bin:/usr/bin:/bin")
        self.assertTrue(real, "no awk on /usr/local/bin:/usr/bin:/bin")
        b = d / "bin"
        b.mkdir()
        (b / "awk").write_text(
            "#!/bin/sh\n# SC5 test double: the FIRST awk call (install.sh's record_get, after its sidecar snapshot)\n"
            "# waits until writer A has opened the brain and committed.\n"
            "if [ ! -e '%s/gate.signalled' ]; then\n  : > '%s/gate.signalled'\n  n=0\n"
            "  while [ ! -e '%s/a.ready' ] && [ $n -lt 2400 ]; do sleep 0.05; n=$((n+1)); done\nfi\n"
            "exec '%s' \"$@\"\n" % (d, d, d, real), encoding="utf-8")
        os.chmod(str(b / "awk"), 0o755)
        return b

    def _a_survives(self, args, want_rc, want_text):
        self._wal_brain()
        d = self.tmp / "sc5"
        d.mkdir()
        shim = self._awk_double(d)
        a = subprocess.Popen([self.py, "-c", _SC5_WRITER_A, str(self.db), str(d)])
        self.addCleanup(lambda: (a.poll() is None and a.kill(), a.wait()))
        pin = self._fake_pin()
        pin["PATH"] = str(shim) + ":" + pin["PATH"]
        p = self._install(*args, **pin)
        self.assertEqual(p.returncode, want_rc, (p.stdout[-1200:], p.stderr[-1200:]))
        self.assertIn(want_text, p.stdout + p.stderr)
        self.assertTrue((d / "a.ready").exists(), "premise: writer A committed DURING the installer's run")
        self.assertIsNone(a.poll(), "premise: writer A still holds the brain when the installer exits")
        b = sqlite3.connect(str(self.db), timeout=30)       # writer B opens after the installer exited
        seen = b.execute("SELECT COUNT(*) FROM memories WHERE source_project='sc5-A'").fetchone()[0]
        for i in range(5):
            b.execute("INSERT INTO memories (content, category, source_project) VALUES (?, 'insight', 'sc5-B')", ("B %d" % i,))
        b.commit()
        (d / "go_a2").write_text("1")
        self.assertEqual(a.wait(timeout=120), 0, "writer A did not finish cleanly")
        b.execute("INSERT INTO memories (content, category, source_project) VALUES ('B last', 'insight', 'sc5-B')")
        b.commit()
        b.close()
        c = sqlite3.connect(str(self.db))
        try:
            got_a = c.execute("SELECT COUNT(*) FROM memories WHERE source_project='sc5-A'").fetchone()[0]
            got_b = c.execute("SELECT COUNT(*) FROM memories WHERE source_project='sc5-B'").fetchone()[0]
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(seen, 5, "writer B did not see the 5 rows A had COMMITTED before B opened")
        self.assertEqual((got_a, got_b, ok), (10, 6, "ok"),
                         "a concurrent writer's committed memories were lost by %r (A %d of 10, B %d of 6)" % (args, got_a, got_b))

    def test_a_dry_run_keeps_a_concurrent_writers_commits(self):
        self._a_survives(("--tier1-ambient", self.py, "--dry-run"), 0, "DRY RUN")

    def test_a_refusal_keeps_a_concurrent_writers_commits(self):
        (self.brain / "venv" / "bin").mkdir(parents=True)
        (self.brain / "venv" / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
        self._a_survives(("--tier1-ambient", self.py), 2, "this install owns a venv")

    def test_print_write_set_keeps_a_concurrent_writers_commits(self):
        self._a_survives(("--print-write-set", "--tier1-ambient", self.py, "--with-crons"), 0, '"declared"')

    def test_each_path_still_leaves_no_sidecar_when_nobody_else_holds_the_brain(self):
        """The CRUCIBLE-3 promise is kept by SQLite now, not by `rm`: a dry run, a refusal and --print-write-set
        on a WAL brain that had no sidecar leave none -- although each one's read of the brain created them."""
        self._wal_brain()
        c = sqlite3.connect("file:%s?mode=ro" % self.db, uri=True)
        try:
            c.execute("SELECT COUNT(*) FROM memories").fetchone()
        finally:
            c.close()
        self.assertIn("claude_memory.db-shm", self._sidecars(), "premise: a mode=ro read of this brain leaves -shm")
        w = sqlite3.connect(str(self.db))                    # the last connection: SQLite retires them --
        w.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()   # once it has OPENED the file (lazy open)
        w.close()
        self.assertEqual(self._sidecars(), [], "premise: a read-write open + close retires them")
        both_enabled = self.tmp / "both_enabled"
        _fake_nvidia_smi(both_enabled, {"0": "Enabled", "1": "Enabled"})
        # every path here READS the brain (mode=ro) before it ends: the two refusals come after the shape gate's read
        for args, rc, extra in ((("--tier1-ambient", self.py, "--dry-run"), 0, {}),
                                (("--print-write-set", "--tier1-ambient", self.py, "--with-crons"), 0, {}),
                                (("--tier1-ambient", self.py), 2, {"WIZARD_EMBED_MODEL": "qwen"}),
                                (("--tier1-ambient", self.py, "--dry-run"), 2,
                                 {"PATH": str(both_enabled) + ":/usr/local/bin:/usr/bin:/bin", "CUDA_VISIBLE_DEVICES": "",
                                  "WIZARD_CUDA_DEVICE": ""})):
            main_before = _sha(self.db)
            # the both-Enabled refusal is G1's own (nothing declared, no terminal): no declaration for it
            both = "both_enabled" in extra.get("PATH", "")
            if both and self.env_file.exists():
                self.env_file.unlink()
            env = self._fake_pin(declare=not both)
            env.update(extra)
            p = self._install(*args, **env)
            self.assertEqual(p.returncode, rc, (args, p.stdout[-800:], p.stderr[-800:]))
            self.assertEqual(self._sidecars(), [], "%r left a sidecar beside a brain that had none" % (args,))
            self.assertEqual(_sha(self.db), main_before, "%r changed the brain file" % (args,))


class TestTheSidecarGuardCoversEveryRead(_AmbientHome):
    """Iteration 4, LOW 3 and LOW 4 (both inherited from c6d7af2). The --attach-db check read the file 170 lines
    before the guard was armed, so its mode=ro fallback left a -wal/-shm under "Refusing; nothing was touched"; and
    the guard looked for a symlinked brain's sidecars beside the LINK while SQLite puts them beside the TARGET."""

    def test_an_attach_refusal_leaves_no_sidecar(self):
        f = self.tmp / "not_a_brain.db"
        c = sqlite3.connect(str(f))
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t (x)")
        c.commit()
        c.close()
        side = lambda: sorted(p.name for p in self.tmp.glob("not_a_brain.db-*"))
        self.assertEqual(side(), [], "premise: a WAL file with no sidecar")
        p = self._install("--attach-db", str(f), "--dry-run", "--force-cpu")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-800:]))
        self.assertIn("is not a brain this package can attach (no-memories-table). Refusing; nothing was touched", p.stderr)
        self.assertEqual(side(), [], "the refusal left the sidecars its own read created")

    def test_a_symlinked_brains_sidecars_are_retired_beside_its_target(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        target = elsewhere / "brain.db"
        shutil.copy2(str(SEED_DB), str(target))
        c = sqlite3.connect(str(target))
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        c.commit()
        self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal", "premise: a WAL brain")
        c.close()
        self.brain.mkdir(parents=True)
        self.db.symlink_to(target)
        side = lambda: sorted(p.name for p in elsewhere.glob("brain.db-*"))
        self.assertEqual(side(), [], "premise: no sidecar at the target")
        before = _sha(target)
        for args in (("--tier1-ambient", self.py, "--dry-run"), ("--dry-run", "--force-cpu")):
            p = self._install(*args, **self._fake_pin())
            self.assertEqual(p.returncode, 0, (args, p.stdout[-800:], p.stderr[-800:]))
            self.assertEqual(side(), [], "%r left SQLite's sidecars beside the symlinked brain's target" % (args,))
            self.assertEqual(_sha(target), before, "%r changed the brain" % (args,))

    def test_the_probes_uri_is_escaped(self):
        """LOW 2's other half: the proofs open the brain through install/tier1_probe.py; a '?' in the path, spliced
        raw, opened (and could CREATE) the path's prefix."""
        d = self.tmp / "q?x"
        d.mkdir()
        shutil.copy2(str(SEED_DB), str(d / "claude_memory.db"))
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import tier1_probe  # type: ignore
            facts = tier1_probe.db_facts(str(d / "claude_memory.db"))
        finally:
            sys.path.remove(str(ROOT / "install"))
        self.assertNotIn("error", facts, facts)
        self.assertFalse((self.tmp / "q").exists(), "the probe's read created a file outside the brain dir")


class TestThePreImageIsVerifiedAgainstItsOwnSnapshot(_AmbientHome):
    """Iteration 3, item 9. write_verified_preimage (extracted VERBATIM from install.sh and run) counted the
    source on a NEW connection after backup(), so any concurrent writer made a good image "unverified" and the
    landing died (measured: a writer every 10 ms -> 'pre-image written ... (1574 memories; source holds 1576)',
    FATAL; the old method matched in 0 of 15 attempts). The count and the copy now read ONE snapshot. A writer
    commits in a tight loop through every attempt; every attempt must verify."""

    def test_a_concurrent_writer_cannot_make_a_good_image_unverified(self):
        sh = SH.read_text(encoding="utf-8")
        fn = re.search(r"^write_verified_preimage\(\) \{.*?^\}\n", sh, re.M | re.S)
        self.assertIsNotNone(fn, "install.sh defines no write_verified_preimage()")
        (self.tmp / "pre.sh").write_text(fn.group(0) + 'PY_SYS="$1"; write_verified_preimage "$2" "$3"\n', encoding="utf-8")
        self._brain()
        c = sqlite3.connect(str(self.db))
        c.executemany("INSERT INTO memories (content, category, source_project) VALUES (?, 'insight', 'pre-bulk')",
                      [("bulk %d %s" % (i, "x" * 400),) for i in range(6000)])   # a backup() that takes real time
        c.commit()
        c.close()
        stop = self.tmp / "stop"
        w = subprocess.Popen([sys.executable, "-c",
                              "import os, sqlite3, sys\n"
                              "c = sqlite3.connect(sys.argv[1], timeout=30); n = 0\n"
                              "print('writing', flush=True)\n"
                              "while not os.path.exists(sys.argv[2]):\n"
                              "    c.execute(\"INSERT INTO memories (content, category, source_project) VALUES ('w', 'insight', 'pre-w')\"); c.commit(); n += 1\n"
                              "c.close(); print(n)\n", str(self.db), str(stop)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (stop.write_text("1"), w.wait(timeout=60), w.stdout.close()))
        self.assertEqual(w.stdout.readline().strip(), "writing")
        for i in range(5):
            dst = self.tmp / ("image_%d.db" % i)
            p = subprocess.run(["bash", str(self.tmp / "pre.sh"), self.py, str(self.db), str(dst)],
                               capture_output=True, text=True, timeout=300)
            m = re.search(r"pre-image written: .* \((\d+) memories; source holds (\d+)\)", p.stdout)
            self.assertIsNotNone(m, (p.stdout[-600:], p.stderr[-600:]))
            self.assertEqual((p.returncode, m.group(1)), (0, m.group(2)),
                             "attempt %d: a concurrent writer made a good image unverified: %r" % (i, p.stdout[-300:]))


class TestAKilledProbeStepIsNamed(unittest.TestCase):
    """Iteration 3, item 10. A step killed by a signal prints no JSON and no traceback: measured, P3 under
    CUDA_VISIBLE_DEVICES='' SEGFAULTS torch on the reference host and install.sh printed 'P3 FAILED (rc -11): .'
    The runner names the signal. (subprocess.run is replaced in-process: nothing is executed.)"""

    def test_a_signal_killed_step_names_the_signal(self):
        import contextlib
        import io
        from unittest import mock
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import importlib
            import tier1_probe  # type: ignore
            importlib.reload(tier1_probe)
            killed = lambda cmd, **kw: subprocess.CompletedProcess(cmd, -11, "", "")   # noqa: E731
            out = io.StringIO()
            with mock.patch.object(tier1_probe.subprocess, "run", killed), contextlib.redirect_stdout(out):
                rc = tier1_probe.main(["run", "--steps", "P1", "--db", "unused.db", "--organs", str(ROOT / "organs")])
        finally:
            sys.path.remove(str(ROOT / "install"))
        self.assertEqual(rc, 1)
        step = json.loads(out.getvalue().strip().splitlines()[-1])["steps"][0]
        self.assertEqual(step["rc"], -11)
        self.assertEqual(step["error"], "the step was KILLED by signal 11 (SIGSEGV) -- no Python traceback exists", step)


class TestTheRecordNamesTheMigrationsThisRunApplied(_AmbientHome):
    """Item 12. INSTALL_RECORD carried `schema_migrations_journal_rows` -- the whole journal -- which
    coincided with the migrations the first run applied and read the same on a run that applied none.
    Two wet --tier0-only runs (the record is written in every wet shape). The expectation is DERIVED
    from the journal itself, never typed: run 1 names every row it created; run 2 names exactly the
    rows whose applied_at moved during run 2 (measured: under an interpreter without sqlite_vec the
    migrator re-applies the qwen-tier migration each run -- the record now says so, where the journal
    count said nothing), and never the whole journal."""

    def _record(self):
        rec = {}
        for line in (self.brain / "INSTALL_RECORD").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                rec[k] = v
        return rec

    def _journal(self):
        c = sqlite3.connect("file:%s?mode=ro" % self.db, uri=True)
        try:
            return dict(c.execute("SELECT name, applied_at FROM schema_migrations"))
        finally:
            c.close()

    @staticmethod
    def _names(field):
        return [] if field == "none" else sorted(field.split(","))

    def test_each_run_names_what_it_applied_and_not_the_journal(self):
        p = self._install("--tier0-only", "--init-blank", "--force-cpu")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2500:])
        j1, r1 = self._journal(), self._record()
        self.assertIn("migrations_applied_this_run", r1, r1)
        self.assertEqual(self._names(r1["migrations_applied_this_run"]), sorted(j1),
                         "a fresh brain: every journal row was created by this run")
        p = self._install("--tier0-only", "--force-cpu")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2500:])
        j2, r2 = self._journal(), self._record()
        moved = sorted(n for n in j2 if j2[n] != j1.get(n))
        self.assertEqual(self._names(r2["migrations_applied_this_run"]), moved, (r2, moved))
        self.assertLess(len(moved), len(j2), "premise: the second run re-applies less than the whole journal")
        self.assertEqual(r2.get("schema_migrations_journal_rows"), str(len(j2)), r2)


if __name__ == "__main__":
    unittest.main()

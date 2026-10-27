"""Handoff round 1, lane L4 -- the installer's accelerator route (compute-agnostic Voidloom).

Each class binds one product claim the installer makes, and each was RED at f275d09:

  * EnvFileNoGpuAgreement   -- WIZARD_CUDA_DEVICE=-1 / none outside the managed block is the owner's "no GPU", and a
                               shell and the units both read it so: no 'read it differently' warning (ambient: no
                               refusal). Clean-room proof #28100 found the false positive at env_file.py:1119.
  * EnvFileAcceleratorKey   -- WIZARD_ACCELERATOR (contract C2) is a key the one writer declares, and it writes ONLY
                               'auto' or 'cpu'.
  * PlanDecisions           -- install/accelerator_plan.py: the backend, the torch channel per accelerator
                               (cuda/rocm/xpu/mps/cpu, Intel Mac), the proof label, and Drew's reranker gate (non-CPU
                               accelerator AND MemTotal >= 15.0 GiB; never CPU-only), decided from C1's 5-tuple.
  * GateRecord              -- contract C3: reranker_enabled + reranker_gate merged into wizard-config.json, every
                               other key kept, an unreadable file never touched.
  * InstallerSaysThePlan    -- install.sh --dry-run names the backend, the channel and the reranker decision with its
                               reason ("would ..."), under the CPU, this host's CUDA, and FAKE mps / rocm / xpu (a
                               package copy whose organs carry a fake resolve_accelerator with C1's frozen signature);
                               a contradicting --accelerator is refused before anything is touched.
  * ForceCpuIsDurable       -- --force-cpu RECORDS WIZARD_ACCELERATOR=cpu outside the managed block (dated), re-runs
                               keep it, the 'else card 0' warning is gone, and an owner's different line is refused.
  * GoldenVerdict           -- P6's comparison (install/golden_vectors.py): a device passes only when every text agrees
                               with the CPU reference at the fixture's frozen tolerance AND every query's top-3 is the
                               reference's; NaN, a drift and a re-ranking each fail, named. The shipped fixtures are
                               well-formed and were made on the CPU.
  * ProbeStepsWithoutAModel -- P7 is skipped (and downloads nothing) when the gate is OFF; P6 says when a tier ships no
                               fixture.
  * PreflightAlwaysReports  -- `install.sh --preflight` writes preflight.json even when it fails, names the first
                               failure, and leaves no scratch folder and nothing in HOME.
  * MacWorkflowsAreManual   -- Drew's cost rule (2026-10-15): every macOS workflow is workflow_dispatch only, <= 15
                               minutes, and cancels a duplicate.
  * StockMacBash            -- (round 2: B1, B2, m7) install.sh's own claim "bash 3.2 / BSD-userland clean": no shipped
                               script expands a possibly-empty array bare under set -u, and none puts a heredoc inside
                               $( ) (bash 3.2 mis-scans it and the file does not parse); every shipped script parses
                               under the interpreter VOIDLOOM_TEST_BASH names (macOS: /bin/bash); the Mac route job runs
                               /bin/bash, and install.sh runs its child script with the shell that runs it.

Stdlib only; no model, no torch, no GPU (every child runs with CUDA_VISIBLE_DEVICES='').
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ENV_FILE_PY = ROOT / "install" / "env_file.py"
PLAN_PY = ROOT / "install" / "accelerator_plan.py"
INSTALL_SH = ROOT / "install.sh"
GIB = 1024 ** 3
ORGANS = ROOT / "organs"
BEGIN, END = "# >>> wizard-library managed >>>", "# <<< wizard-library managed <<<"


# The interpreter every install.sh run in this file uses. VOIDLOOM_TEST_BASH names another one -- the round-2 proof runs
# these classes under GNU bash 3.2.57, the version macOS ships as /bin/bash (B1, B2) -- else PATH's bash.
_TEST_BASH = os.environ.get("VOIDLOOM_TEST_BASH") or "bash"


def _child_env():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["CUDA_VISIBLE_DEVICES"] = ""          # forced: no child of this suite may see a GPU
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _env_file_cli(path, *args):
    p = subprocess.run([sys.executable, "-B", str(ENV_FILE_PY), "--file", str(path), "--organs", str(ORGANS),
                        "--begin", BEGIN, "--end", END] + list(args),
                       capture_output=True, text=True, env=_child_env(), timeout=60)
    return p.returncode, p.stdout, p.stderr


class EnvFileNoGpuAgreement(unittest.TestCase):
    """The owner's hand-written 'no GPU' line is agreement between a shell and systemd, in every shape."""

    def _check(self, body, shape):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "env"
            f.write_bytes(body)
            return _env_file_cli(f, "check", shape)

    def test_minus_one_is_not_a_divergence(self):
        for shape in ("venv", "ambient"):
            rc, out, err = self._check(b"WIZARD_CUDA_DEVICE=-1\n%s\nWIZARD_EMBED_MODEL=nomic\n%s\n"
                                       % (BEGIN.encode(), END.encode()), shape)
            self.assertEqual(rc, 0, (shape, out, err))
            self.assertNotIn("WIZARD_CUDA_DEVICE", out, (shape, out))

    def test_none_is_not_a_divergence(self):
        for shape in ("venv", "ambient"):
            rc, out, err = self._check(b"WIZARD_CUDA_DEVICE=none\n", shape)
            self.assertEqual(rc, 0, (shape, out, err))
            self.assertNotIn("WIZARD_CUDA_DEVICE", out, (shape, out))

    def test_a_real_divergence_is_still_named(self):
        # the normaliser must not swallow a genuine disagreement: an inline comment systemd keeps in the value
        rc, out, _err = self._check(b"WIZARD_DISPLAY_INDEX=0 # the monitor\n", "ambient")
        self.assertEqual(rc, 2, out)
        self.assertIn("WIZARD_DISPLAY_INDEX", out)


class EnvFileAcceleratorKey(unittest.TestCase):
    """WIZARD_ACCELERATOR is declared by the one writer, outside the managed block, as 'auto' or 'cpu' only (C2)."""

    def _declare(self, body, *pairs):
        with tempfile.TemporaryDirectory() as d:
            f, out = Path(d) / "env", Path(d) / "image"
            if body is not None:
                f.write_bytes(body)
            rc, so, se = _env_file_cli(f, "declare", str(out), *pairs)
            return rc, so, se, (out.read_bytes() if out.exists() else None)

    def test_cpu_is_declared_outside_the_block(self):
        rc, so, se, image = self._declare(b"%s\nWIZARD_EMBED_MODEL=nomic\n%s\n" % (BEGIN.encode(), END.encode()),
                                          "WIZARD_ACCELERATOR=cpu", "chosen at install 2026-10-15 (--force-cpu)")
        self.assertEqual(rc, 0, (so, se))
        text = image.decode()
        self.assertIn("# chosen at install 2026-10-15 (--force-cpu)\nWIZARD_ACCELERATOR=cpu\n", text)
        self.assertGreater(text.index("WIZARD_ACCELERATOR=cpu"), text.index(END))

    def test_a_backend_family_is_never_written(self):
        for v in ("mps", "cuda", "rocm", "xpu", "-1", ""):
            rc, so, _se, image = self._declare(None, "WIZARD_ACCELERATOR=%s" % v, "c")
            self.assertNotEqual(rc, 0, (v, so))
            self.assertIsNone(image, v)

    def test_the_declared_value_is_readable_back(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "env"
            f.write_bytes(b"# chosen at install 2026-10-15 (--force-cpu)\nWIZARD_ACCELERATOR=cpu\n")
            rc, out, _ = _env_file_cli(f, "get", "WIZARD_ACCELERATOR")
            self.assertEqual((rc, out.strip()), (0, "cpu"))
            rc, out, _ = _env_file_cli(f, "check", "ambient")
            self.assertEqual(rc, 0, out)


def _plan_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("accelerator_plan_under_test", str(PLAN_PY))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PlanDecisions(unittest.TestCase):
    """The pure decision, fed C1 tuples (backend, torch_device, basis, display_shared, ram_bytes)."""

    def setUp(self):
        self.ap = _plan_mod()

    def plan(self, backend, ram_gib, osname="Linux", arch="x86_64", request="auto", py=(3, 12), override="", wsl=False):
        dev = {"cuda": "cuda:0", "rocm": "cuda:0", "mps": "mps", "xpu": "xpu", "cpu": "cpu"}[backend]
        return self.ap.plan((backend, dev, "fake", backend == "mps", int(ram_gib * GIB)), request, osname=osname,
                            arch=arch, wsl=wsl, py=py, override=override, source="fake")

    def test_every_backend_has_its_channel(self):
        rows = {
            ("cuda", "Linux", "x86_64"): (None, "cuda"),
            ("cpu", "Linux", "x86_64"): ("https://download.pytorch.org/whl/cpu", "cpu"),
            ("rocm", "Linux", "x86_64"): ("https://download.pytorch.org/whl/rocm7.2", "rocm"),
            ("xpu", "Linux", "x86_64"): ("https://download.pytorch.org/whl/xpu", "xpu"),
            ("mps", "Darwin", "arm64"): (None, "macos"),
            ("cpu", "Darwin", "arm64"): (None, "macos"),
        }
        for (b, o, a), (index, build) in rows.items():
            p = self.plan(b, 32, o, a)
            self.assertIsNone(p["refusal"], (b, o, p["refusal"]))
            self.assertEqual((p["backend"], p["channel"]["index"], p["channel"]["build"]), (b, index, build))

    def test_an_intel_mac_gets_the_last_x86_torch_and_numpy_1(self):
        p = self.plan("cpu", 16, "Darwin", "x86_64", py=(3, 12))
        self.assertEqual(p["channel"]["spec"], ["torch==2.2.2", "numpy<2"])
        p = self.plan("cpu", 16, "Darwin", "x86_64", py=(3, 13))
        self.assertIn("Python 3.12", p["refusal"])

    def test_a_refused_channel_still_carries_its_label_and_reason(self):
        """round 2, m1: an Intel Mac on Python 3.13 has no torch wheel; the plan still says the label and the gate."""
        p = self.plan("cpu", 16, "Darwin", "x86_64", py=(3, 13))
        self.assertIn("Python 3.12", p["refusal"])
        self.assertEqual(p["proof_label"], "SUPPORTED-UNPROVEN")
        self.assertFalse(p["reranker"]["enabled"])
        self.assertIn("never on CPU-only", p["reranker"]["reason"])
        self.assertIn("Torch: none -- an Intel Mac has torch wheels only up to Python 3.12", p["sentence"])
        rows = dict(l.split("\t", 1) for l in subprocess.run(
            [sys.executable, "-B", str(PLAN_PY), "fields"], input=json.dumps(p), capture_output=True, text=True,
            env=_child_env()).stdout.splitlines())
        self.assertEqual(rows["label"], "SUPPORTED-UNPROVEN")
        self.assertTrue(rows["rerank_why"].strip())

    def test_the_ram_reason_never_rounds_onto_the_threshold(self):
        """round 2, l1: 14.99 GiB used to read '15.0 GiB of RAM is under 15.0 GiB'."""
        r = self.ap.reranker_gate("cuda", int(14.99 * GIB), "")
        self.assertEqual(r[0], False)
        self.assertTrue(r[1].startswith("14.99 GiB of RAM is under 15.0 GiB"), r[1])

    def test_the_basis_names_the_owners_gpu_answer_not_ambient(self):
        """round 2, l3: install.sh hands the --gpu answer to the planning child as CUDA_VISIBLE_DEVICES."""
        res = ("cuda", "cuda:0", "auto:nvidia-smi lists card(s) 0 1; card 1 (ambient)", False, 32 * int(GIB))
        p = self.ap.plan(res, osname="Linux", arch="x86_64", wsl=False, py=(3, 12), pin_from="your --gpu 1 answer")
        self.assertEqual(p["basis"], "auto:nvidia-smi lists card(s) 0 1; card 1 (your --gpu 1 answer)")
        self.assertIn("(ambient)", self.ap.plan(res, osname="Linux", arch="x86_64", wsl=False, py=(3, 12))["basis"])

    def test_the_gate_is_15_gib_and_never_cpu(self):
        self.assertTrue(self.plan("mps", 16.0, "Darwin", "arm64")["reranker"]["enabled"])
        self.assertTrue(self.plan("cuda", 15.0)["reranker"]["enabled"])
        self.assertFalse(self.plan("cuda", 14.9)["reranker"]["enabled"])
        self.assertFalse(self.plan("mps", 8.0, "Darwin", "arm64")["reranker"]["enabled"])
        off = self.plan("cpu", 128.0)["reranker"]
        self.assertFalse(off["enabled"])
        self.assertIn("never on CPU-only", off["reason"])
        self.assertEqual(off["threshold_gib"], 15.0)

    def test_an_owner_line_can_close_the_gate_but_never_open_it(self):
        r = self.plan("cpu", 8, override="on")["reranker"]
        self.assertFalse(r["enabled"])
        self.assertIn("WIZARD_RERANKER=on", r["reason"])
        self.assertIn("cannot open the gate", r["reason"])
        r = self.plan("cuda", 8, override="on")["reranker"]
        self.assertFalse(r["enabled"])
        r = self.plan("cuda", 64, override="off")["reranker"]
        self.assertFalse(r["enabled"])

    def test_a_request_that_contradicts_the_machine_is_refused(self):
        self.assertIn("contradicts", self.plan("cuda", 32, request="rocm")["refusal"])
        self.assertIn("macOS has no cuda", self.plan("cpu", 16, "Darwin", "arm64", request="cuda")["refusal"])
        self.assertIn("Apple Silicon", self.plan("cpu", 16, "Darwin", "x86_64", request="mps")["refusal"])
        self.assertIn("Linux has no mps", self.plan("cpu", 16, request="mps")["refusal"])

    def test_cpu_beats_any_machine_and_the_basis_says_why(self):
        p = self.plan("cuda", 32, request="cpu")
        self.assertEqual((p["backend"], p["torch_device"]), ("cpu", "cpu"))
        self.assertIn("requested", p["basis"])
        self.assertFalse(p["reranker"]["enabled"])

    def test_labels_are_c4_words_and_only_evidence_promotes(self):
        self.assertEqual(self.plan("cuda", 32)["proof_label"], "PROVEN")
        self.assertEqual(self.plan("cpu", 32)["proof_label"], "PROVEN")
        self.assertEqual(self.plan("mps", 32, "Darwin", "arm64")["proof_label"], "PROVEN")
        self.assertEqual(self.plan("cpu", 32, "Darwin", "arm64")["proof_label"], "PROVEN")
        for b, o, a in (("cpu", "Darwin", "x86_64"), ("rocm", "Linux", "x86_64"), ("xpu", "Linux", "x86_64")):
            self.assertEqual(self.plan(b, 32, o, a)["proof_label"], "SUPPORTED-UNPROVEN", b)
        self.assertEqual(set(self.ap.LABELS), {"PROVEN", "PROVEN-BY-PROXY", "SUPPORTED-UNPROVEN", "FAILED-ON-THIS-MACHINE"})

    def test_the_sentence_names_the_machine_and_the_reason(self):
        s = self.plan("mps", 16, "Darwin", "arm64")["sentence"]
        for w in ("Apple Silicon Mac", "16.0 GiB", "[PROVEN]", "Reranker ON", "15.0 GiB"):
            self.assertIn(w, s)

    def test_the_stub_keeps_c1s_signature_and_never_guesses_xpu(self):
        r = self.ap._stub_resolve(None, "Darwin", "arm64")
        self.assertEqual(len(r), 5)
        self.assertEqual(r[:2], ("mps", "mps"))
        self.assertNotEqual(self.ap._stub_resolve(None, "Linux", "x86_64")[0], "xpu")


class ProofFailure(unittest.TestCase):
    """The MPS self-protection (SYNTHESIS §3, Vael: warn AND flip), decided by a pure function install.sh reads."""

    def setUp(self):
        self.ap = _plan_mod()

    def test_mps_rocm_xpu_flip_to_the_cpu_announced(self):
        for b in ("mps", "rocm", "xpu"):
            d = self.ap.on_proof_failure(b, "P6: golden vectors on mps:0: the worst text agrees at cosine 0.91", "", "2026-10-15")
            self.assertEqual((d["action"], d["label"]), ("flip", "FAILED-ON-THIS-MACHINE"), b)
            self.assertIn("written by install.sh 2026-10-15", d["comment"])
            self.assertIn("To try %s again" % b, d["comment"])
            self.assertNotIn("\n", d["comment"])
            a = d["announce"]
            # gpu-safety-002's shape: the value, the reason, the result, the opt-out -- in one line
            for part in ("SAFETY: %s REFUSED" % b, "cosine 0.91", "every job computes on the CPU",
                         "opt-out: write WIZARD_ACCELERATOR=%s" % b):
                self.assertIn(part, a)
            self.assertNotIn("\n", a)

    def test_the_owners_explicit_line_is_the_opt_out(self):
        d = self.ap.on_proof_failure("mps", "P6: x", "mps")
        self.assertEqual((d["action"], d["label"]), ("keep-owner", "FAILED-ON-THIS-MACHINE"))
        self.assertIn("opt-out, honoured", d["announce"])
        self.assertEqual(self.ap.on_proof_failure("mps", "P6: x", "auto")["action"], "flip")

    def test_proven_rows_fail_loudly_never_flip(self):
        for b in ("cuda", "cpu"):
            d = self.ap.on_proof_failure(b, "P6: x")
            self.assertEqual(d["action"], "fail", b)
            self.assertIsNone(d["label"])

    def test_install_sh_reads_the_decision_through_the_cli(self):
        p = subprocess.run([sys.executable, "-B", str(PLAN_PY), "on-proof-failure", "--backend", "mps", "--reason",
                            "P6: drift"], capture_output=True, text=True, env=_child_env(), timeout=60)
        rows = dict(l.split("\t", 1) for l in p.stdout.splitlines())
        self.assertEqual(p.returncode, 0)
        self.assertEqual(rows["action"], "flip")
        self.assertIn("SAFETY: mps REFUSED", rows["announce"])


class GateRecord(unittest.TestCase):
    def setUp(self):
        self.ap = _plan_mod()
        self.p = self.ap.plan(("mps", "mps", "fake", True, 16 * GIB), osname="Darwin", arch="arm64", wsl=False)

    def test_merged_keys_others_kept_backup_taken(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "wizard-config.json"
            f.write_text(json.dumps({"profile": "laptop", "embed_device": "cpu"}))
            rc, _msg = self.ap.write_gate(str(f), self.p)
            self.assertEqual(rc, 0)
            got = json.loads(f.read_text())
            self.assertEqual(got["profile"], "laptop")
            self.assertIs(got["reranker_enabled"], True)
            self.assertEqual(set(got["reranker_gate"]), {"accelerator", "ram_gib", "threshold_gib", "decided_at"})
            self.assertEqual((got["reranker_gate"]["accelerator"], got["reranker_gate"]["threshold_gib"]), ("mps", 15.0))
            self.assertEqual(json.loads((Path(d) / "wizard-config.json.bak").read_text())["profile"], "laptop")

    def test_an_unreadable_config_is_never_touched(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "wizard-config.json"
            f.write_bytes(b"{not json")
            rc, msg = self.ap.write_gate(str(f), self.p)
            self.assertEqual(rc, 3)
            self.assertEqual(f.read_bytes(), b"{not json")
            self.assertFalse((Path(d) / "wizard-config.json.bak").exists())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sub" / "wizard-config.json"
            rc, msg = self.ap.write_gate(str(f), self.p, dry_run=True)
            self.assertEqual(rc, 0)
            self.assertTrue(msg.startswith("would write"))
            self.assertFalse(f.parent.exists())


FAKE_C1 = """

def resolve_accelerator():  # TEST FAKE: contract C1's frozen signature
    return (%r, %r, "fake: %s", %r, %d)
"""


class _InstallRun(unittest.TestCase):
    """install.sh in a scratch HOME. A fake package (a copy of this tree) carries a fake C1 and, for a Mac, a uname
    shim; a failing nvidia-smi on PATH hides this host's cards from a fake."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_l4_"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def fake_package(self, backend, ram_gib, osname="Linux", arch="x86_64"):
        pkg = self.tmp / "pkg"
        shutil.copytree(str(ROOT), str(pkg), ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        dev = {"cuda": "cuda:0", "rocm": "cuda:0", "mps": "mps", "xpu": "xpu", "cpu": "cpu"}[backend]
        with open(str(pkg / "organs" / "voidloom_device.py"), "a") as fh:
            fh.write(FAKE_C1 % (backend, dev, backend, backend == "mps", int(ram_gib * GIB)))
        (self.bin / "nvidia-smi").write_text("#!/bin/sh\necho 'NVIDIA-SMI has failed (test double)' >&2\nexit 9\n")
        os.chmod(str(self.bin / "nvidia-smi"), 0o755)
        if osname != "Linux":
            (self.bin / "uname").write_text('#!/bin/sh\ncase "$1" in -m) echo %s ;; *) echo %s ;; esac\n' % (arch, osname))
            os.chmod(str(self.bin / "uname"), 0o755)
        return pkg

    def run_sh(self, *args, pkg=None):
        env = {"HOME": str(self.home), "PATH": "%s:/usr/local/bin:/usr/bin:/bin" % self.bin,
               "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir(), "CUDA_VISIBLE_DEVICES": "",
               "LANG": "C.UTF-8"}
        p = subprocess.run([_TEST_BASH, str((pkg or ROOT) / "install.sh")] + list(args), capture_output=True, text=True,
                           env=env, cwd=str(self.tmp), timeout=600, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr

    def home_files(self):
        return sorted(str(x.relative_to(self.home)) for x in self.home.rglob("*"))


class InstallerSaysThePlan(_InstallRun):
    def _banner(self, out):
        return {k: [l for l in out.splitlines() if l.startswith("[install] %s:" % k)] for k in ("Compute", "Torch", "Reranker")}

    def test_cpu_names_the_cpu_channel_and_the_gate_off(self):
        rc, out = self.run_sh("--dry-run", "--init-blank", "--thinker", "off", "--force-cpu")
        b = self._banner(out)
        self.assertTrue(b["Compute"] and b["Compute"][0].startswith("[install] Compute:      cpu [PROVEN]"), out[-3000:])
        self.assertIn("would install torch from https://download.pytorch.org/whl/cpu", b["Torch"][0])
        self.assertIn("OFF -- no accelerator", b["Reranker"][0])
        self.assertIn("would NOT be downloaded", out)
        self.assertEqual(self.home_files(), [], "a dry run wrote into HOME")
        self._completes(rc, out)

    def _completes(self, rc, out):
        """A banner is not a dry run: under bash 3.2 the banner printed and the parse died later (B2), unseen."""
        self.assertNotRegex(out, r"unbound variable|syntax error|unexpected EOF", out[-3000:])
        self.assertEqual(rc, 0, out[-3000:])
        self.assertIn("INSTALL is deterministic and DONE", out)

    @unittest.skipUnless(shutil.which("nvidia-smi") or os.path.exists("/usr/lib/wsl/lib/nvidia-smi"), "no NVIDIA host")
    def test_this_hosts_cuda_names_pypi_and_the_gate_on(self):
        n = subprocess.run(["sh", "-c", "PATH=$PATH:/usr/lib/wsl/lib nvidia-smi -L | grep -c '^GPU '"],
                           capture_output=True, text=True).stdout.strip()
        args = ["--dry-run", "--init-blank", "--thinker", "off"]
        if n.isdigit() and int(n) > 1:
            args += ["--display-gpu", "0", "--gpu", "1"]          # the reference host: card 0 drives the monitor
        rc, out = self.run_sh(*args)
        b = self._banner(out)
        self.assertTrue(b["Compute"] and " cuda [PROVEN]" in b["Compute"][0], out[-3000:])
        if "--gpu" in args:   # round 2, l3: the card is the owner's answer, not the shell's
            self.assertIn("(your --gpu 1 answer)", b["Compute"][0])
            self.assertNotIn("(ambient)", b["Compute"][0])
        self.assertIn("would install torch from PyPI", b["Torch"][0])
        self.assertTrue(b["Reranker"][0].startswith("[install] Reranker:     ON") or "GiB of RAM is under" in b["Reranker"][0])
        self._completes(rc, out)

    def test_fakes_name_their_channel_label_and_gate(self):
        cases = [("mps", 16.0, "Darwin", "arm64", "PyPI", "PROVEN", "ON"),
                 ("mps", 8.0, "Darwin", "arm64", "PyPI", "PROVEN", "OFF"),
                 ("rocm", 32.0, "Linux", "x86_64", "https://download.pytorch.org/whl/rocm7.2", "SUPPORTED-UNPROVEN", "ON"),
                 ("xpu", 32.0, "Linux", "x86_64", "https://download.pytorch.org/whl/xpu", "SUPPORTED-UNPROVEN", "ON")]
        for backend, ram, osname, arch, channel, label, gate in cases:
            with self.subTest(backend=backend, ram=ram):
                self.tearDown()
                self.setUp()
                pkg = self.fake_package(backend, ram, osname, arch)
                rc, out = self.run_sh("--dry-run", "--init-blank", "--thinker", "off", pkg=pkg)
                b = self._banner(out)
                self.assertTrue(b["Compute"], out[-3000:])
                self.assertIn("Compute:      %s [%s] -- fake: %s (resolver: C1)" % (backend, label, backend), b["Compute"][0])
                self.assertIn("would install torch from %s" % channel, b["Torch"][0])
                self.assertTrue(b["Reranker"][0].startswith("[install] Reranker:     %s --" % gate), b["Reranker"])
                self.assertEqual(self.home_files(), [], "a dry run wrote into HOME")
                # a fake package cannot lay (its organ differs from the manifest), so it must at least reach Step A
                self.assertNotRegex(out, r"unbound variable|syntax error|unexpected EOF", out[-3000:])
                self.assertIn("STEP A", out)

    def test_a_contradicting_accelerator_is_refused_before_anything(self):
        pkg = self.fake_package("mps", 16.0, "Darwin", "arm64")
        rc, out = self.run_sh("--dry-run", "--init-blank", "--thinker", "off", "--accelerator", "cuda", pkg=pkg)
        self.assertEqual(rc, 2, out[-2000:])
        self.assertIn("REFUSED: --accelerator cuda contradicts this machine: it shows the Apple GPU (MPS)", out)
        rc, out = self.run_sh("--force-cpu", "--accelerator", "mps", pkg=pkg)
        self.assertEqual(rc, 2)
        self.assertIn("contradict each other", out)
        rc, out = self.run_sh("--accelerator", "tpu", pkg=pkg)
        self.assertEqual(rc, 2)
        self.assertEqual(self.home_files(), [])


class ForceCpuIsDurable(_InstallRun):
    def env_file(self):
        return self.home / ".voidloom" / "wizard-library-env"

    def test_force_cpu_records_the_cpu_answer_once(self):
        rc, out = self.run_sh("--env-only", "--force-cpu")
        self.assertEqual(rc, 0, out[-3000:])
        text = self.env_file().read_text()
        tail = text.split(END, 1)[1]
        self.assertIn("(--force-cpu)", tail)
        self.assertIn("\nWIZARD_ACCELERATOR=cpu\n", tail)
        self.assertNotIn("else card 0 -- which may drive a display", out)
        cfg = json.loads((self.home / ".voidloom" / "wizard-config.json").read_text())
        self.assertIs(cfg["reranker_enabled"], False)
        self.assertEqual(cfg["reranker_gate"]["accelerator"], "cpu")
        rc, out = self.run_sh("--env-only", "--force-cpu")
        self.assertEqual(rc, 0, out[-3000:])
        self.assertEqual(self.env_file().read_text().count("WIZARD_ACCELERATOR=cpu"), 1)
        self.assertIn("already declares WIZARD_ACCELERATOR=cpu", out)
        rc, chk, _ = _env_file_cli(self.env_file(), "check", "venv")
        self.assertEqual((rc, chk.strip()), (0, ""), chk)

    def test_an_owners_other_line_is_refused_and_untouched(self):
        self.env_file().parent.mkdir(parents=True)
        self.env_file().write_text("WIZARD_ACCELERATOR=auto\n")
        rc, out = self.run_sh("--env-only", "--force-cpu")
        self.assertEqual(rc, 2, out[-2000:])
        self.assertIn("declares WIZARD_ACCELERATOR=auto -- your own line", out)
        self.assertEqual(self.env_file().read_text(), "WIZARD_ACCELERATOR=auto\n")


def _gv():
    import importlib.util
    spec = importlib.util.spec_from_file_location("golden_vectors_under_test", str(ROOT / "install" / "golden_vectors.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class GoldenVerdict(unittest.TestCase):
    def setUp(self):
        self.gv = _gv()
        import random
        rnd = random.Random(7)
        self.docs = [[rnd.gauss(0, 1) for _ in range(16)] for _ in range(len(self.gv.DOCUMENTS))]
        # each query sits next to one document, so the reference has a clear top-3
        self.queries = [[x + rnd.gauss(0, 0.05) for x in self.docs[i * 2]] for i in range(len(self.gv.QUERIES))]
        self.fx = {"tolerance": {"min_cosine": 0.999}, "documents": {"vectors": self.docs},
                   "queries": {"vectors": self.queries}}

    def test_identical_vectors_pass(self):
        r = self.gv.compare(self.docs, self.queries, self.fx)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["top3_identical"])

    def test_a_drift_fails_named(self):
        bad = [list(v) for v in self.docs]
        bad[3] = [x * -1 for x in bad[3]]
        r = self.gv.compare(bad, self.queries, self.fx)
        self.assertFalse(r["ok"])
        self.assertIn("under the", r["reason"])

    def test_nan_fails_named(self):
        bad = [list(v) for v in self.docs]
        bad[0][0] = float("nan")
        r = self.gv.compare(bad, self.queries, self.fx)
        self.assertFalse(r["ok"])
        self.assertIn("non-finite", r["reason"])

    def test_a_reranking_fails_even_at_high_cosine(self):
        fx = dict(self.fx, tolerance={"min_cosine": -1.0})
        q = [list(v) for v in self.queries]
        q[0] = list(self.docs[5])            # now query 0 points somewhere else
        r = self.gv.compare(self.docs, q, fx)
        self.assertFalse(r["ok"])
        self.assertIn("ranks the documents differently", r["reason"])

    def test_a_width_mismatch_fails(self):
        r = self.gv.compare([v[:8] for v in self.docs], [v[:8] for v in self.queries], self.fx)
        self.assertFalse(r["ok"])

    def test_the_shipped_fixtures_are_cpu_references(self):
        for tier in self.gv.TIERS:
            path = Path(self.gv.fixture_path(tier))
            self.assertTrue(path.is_file(), "no golden fixture for %s" % tier)
            fx = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(fx["made_on"]["device"], "cpu")
            self.assertEqual(fx["documents"]["texts"], self.gv.DOCUMENTS)
            self.assertEqual(fx["queries"]["texts"], self.gv.QUERIES)
            self.assertEqual(len(fx["documents"]["vectors"]), len(self.gv.DOCUMENTS))
            self.assertGreaterEqual(fx["tolerance"]["min_cosine"], 0.99)
            self.assertTrue(fx["calibration"], "the tolerance was never set by a measurement (outcome rule 3)")
            r = self.gv.compare(fx["documents"]["vectors"], fx["queries"]["vectors"], fx)
            self.assertTrue(r["ok"], r)


class ProbeStepsWithoutAModel(unittest.TestCase):
    def _step(self, name, *extra):
        p = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "tier1_probe.py"), "step", name,
                            "--organs", str(ORGANS)] + list(extra), capture_output=True, text=True,
                           env=_child_env(), timeout=120)
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])

    def test_p7_is_skipped_when_the_gate_is_off(self):
        rc, d = self._step("P7", "--reranker", "0")
        self.assertEqual(rc, 0, d)
        self.assertIn("gate is OFF", d["facts"]["skipped"])

    def test_p6_says_when_a_tier_has_no_fixture(self):
        rc, d = self._step("P6", "--tier", "jina")
        self.assertEqual(rc, 0, d)
        self.assertIn("no golden fixture for tier 'jina'", d["facts"]["skipped"])


class InstallRecordCarriesC4(_InstallRun):
    """Contract C4: a wet install (Tier 0 here: no torch is installed by a test) records the accelerator, its proof
    label, the gate, and EVERY probe P1..P8 with what happened to it."""

    def test_a_tier0_install_records_the_c4_keys(self):
        rc, out = self.run_sh("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "off", "--no-wire-hooks")
        rec_f = self.home / ".claude" / "memory" / "INSTALL_RECORD"
        self.assertTrue(rec_f.is_file(), out[-3000:])
        rec = dict(l.split("=", 1) for l in rec_f.read_text().splitlines() if "=" in l and not l.startswith("#"))
        self.assertEqual(rec["accelerator"], "cpu")
        self.assertEqual(rec["proof_label"], "PROVEN" if sys.platform.startswith("linux") else rec["proof_label"])
        self.assertEqual(rec["reranker_enabled"], "false")
        for n in range(1, 9):
            self.assertIn("probe.P%d" % n, rec)
            self.assertRegex(rec["probe.P%d" % n], r"^(pass|FAIL: |skipped: |not run \()")


class PreflightAlwaysReports(_InstallRun):
    def test_a_failing_preflight_still_writes_its_report_and_cleans_up(self):
        nostack = self.bin / "py-nostack"
        nostack.write_text("#!/bin/sh\nexec %s -S \"$@\"\n" % sys.executable)       # -S: no site-packages, no torch
        os.chmod(str(nostack), 0o755)
        work = self.tmp / "work"
        work.mkdir()
        out = self.tmp / "preflight.json"
        rc, text = self.run_sh("--preflight", "--use-python", str(nostack), "--out", str(out), "--workdir", str(work))
        self.assertEqual(rc, 1, text[-2000:])
        rep = json.loads(out.read_text())
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(rep["first_failure"].startswith("P2"), rep["first_failure"])
        self.assertIn("ram_gib", rep["machine"])
        self.assertIn("backend", rep["plan"])
        self.assertTrue(rep["scratch_removed"])
        self.assertEqual(list(work.iterdir()), [])
        self.assertEqual(self.home_files(), [])
        self.assertIn("send this one file back", text)


class MacWorkflowsAreManual(unittest.TestCase):
    def test_every_macos_job_is_manual_short_and_deduplicated(self):
        import re
        wf = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        mac = [f for f in wf if re.search(r"runs-on:\s*macos", f.read_text())]
        self.assertTrue(mac, "no macOS workflow found")
        for f in mac:
            t = f.read_text()
            on = re.search(r"^on:\s*\n((?:[ \t]+.*\n|\s*\n)*)", t, re.M)
            self.assertIsNotNone(on, f.name)
            triggers = set(re.findall(r"^  ([a-z_]+):", on.group(1), re.M))
            self.assertEqual(triggers, {"workflow_dispatch"}, f.name)
            mins = [int(x) for x in re.findall(r"timeout-minutes:\s*(\d+)", t)]
            jobs = len(re.findall(r"runs-on:", t))
            self.assertEqual(len(mins), jobs, "%s: a job without timeout-minutes" % f.name)
            self.assertTrue(all(m <= 15 for m in mins), (f.name, mins))
            self.assertRegex(t, r"concurrency:\s*\n\s+group:.*\n\s+cancel-in-progress:\s*true", f.name)


def _shipped_shell():
    """Every tracked shell script (a .sh, or the voidloom shim): the population is the tree's own, never typed here."""
    out = []
    try:
        names = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                               timeout=60).stdout.split()
    except Exception:  # noqa: BLE001 -- an installed brain has no git: fall back to a walk
        names = [str(x.relative_to(ROOT)) for x in ROOT.rglob("*") if x.is_file()]
    for n in names:
        f = ROOT / n
        if n.endswith(".sh") or n == "voidloom/voidloom":
            if f.is_file():
                out.append(f)
    return sorted(out)


def _heredocs_inside_cmdsub(text):
    """[(line, tag)] for every heredoc opened while a $( ) is still open on its logical line. bash 3.2 (macOS /bin/bash)
    scans a $( ) for its closing paren with only partial heredoc awareness, so a body holding a quote or a paren runs
    the parse to end of file (B2). Heredoc bodies and comment lines are skipped."""
    import re
    opener = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
    lines = text.split("\n")
    hits, i = [], 0
    while i < len(lines):
        first = i
        logical = lines[i]
        while logical.endswith("\\") and i + 1 < len(lines):
            i += 1
            logical = logical[:-1] + " " + lines[i]
        i += 1
        if logical.lstrip().startswith("#"):
            continue
        for m in opener.finditer(logical):
            depth, j, pre = 0, 0, logical[:m.start()]
            while j < len(pre):
                if pre.startswith("$(", j):
                    depth += 1
                    j += 2
                    continue
                if pre[j] == ")" and depth:
                    depth -= 1
                j += 1
            if depth:
                hits.append((first + 1, m.group(2)))
            tag = m.group(2)          # skip the body: a here-document ends at its delimiter line
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1
    return hits


def _bare_empty_array_expansions(text):
    """[(line, name)] -- an array assigned empty (NAME=()) and expanded bare as "${NAME[@]}" in a file under set -u: an
    'unbound variable' error in every bash before 4.4 (B1). ${NAME[@]+"${NAME[@]}"} is the portable form."""
    import re
    if not re.search(r"^\s*set -[a-z]*u", text, re.M):
        return []
    empty = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)=\(\)", text))
    hits = []
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("#"):
            continue
        for name in re.findall(r'(?<!\+)"\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}"', line):
            if name in empty and ("${%s[@]+" % name) not in line:
                hits.append((n, name))
    return hits


class StockMacBash(unittest.TestCase):
    def test_the_population_is_derived(self):
        names = {str(f.relative_to(ROOT)) for f in _shipped_shell()}
        self.assertIn("install.sh", names)
        self.assertGreater(len(names), 5, names)

    def test_no_heredoc_inside_a_command_substitution(self):
        found = {}
        for f in _shipped_shell():
            h = _heredocs_inside_cmdsub(f.read_text(encoding="utf-8", errors="replace"))
            if h:
                found[str(f.relative_to(ROOT))] = h
        self.assertEqual(found, {}, "a heredoc inside $( ) -- bash 3.2 cannot parse it; move the command and its "
                                    "heredoc into a top-level function and call that inside $( )")

    def test_the_detector_sees_the_shape(self):
        self.assertEqual(_heredocs_inside_cmdsub('x="$(python3 - <<\'PY\'\nprint(\'(\')\nPY\n)"\n'), [(1, "PY")])
        self.assertEqual(_heredocs_inside_cmdsub('x="$(A=1 \\\n  python3 - <<\'PY\'\nPY\n)"\n'), [(1, "PY")])
        self.assertEqual(_heredocs_inside_cmdsub("IFS=\"$(printf '\\t')\" read -r a <<EOF\n$v\nEOF\n"), [])
        self.assertEqual(_heredocs_inside_cmdsub('read -r a b <<< "$p"\n'), [])
        self.assertEqual(_bare_empty_array_expansions('set -u\na=()\n"${a[@]}" x\n'), [(3, "a")])
        self.assertEqual(_bare_empty_array_expansions('set -u\na=()\n${a[@]+"${a[@]}"} x\n'), [])

    def test_no_bare_expansion_of_an_empty_array_under_set_u(self):
        found = {}
        for f in _shipped_shell():
            h = _bare_empty_array_expansions(f.read_text(encoding="utf-8", errors="replace"))
            if h:
                found[str(f.relative_to(ROOT))] = h
        self.assertEqual(found, {}, "bash < 4.4 calls an empty array unbound under set -u")

    def test_every_shipped_script_parses_under_the_named_bash(self):
        b = os.environ.get("VOIDLOOM_TEST_BASH") or ("/bin/bash" if sys.platform == "darwin" else "")
        if not b:
            self.skipTest("no VOIDLOOM_TEST_BASH named (set it to a bash 3.2.57 build; on macOS /bin/bash is used)")
        bad = {}
        for f in _shipped_shell():
            p = subprocess.run([b, "-n", str(f)], capture_output=True, text=True, timeout=120)
            if p.returncode:
                bad[str(f.relative_to(ROOT))] = p.stderr.strip()[-400:]
        self.assertEqual(bad, {}, b)

    def test_the_mac_route_job_runs_the_friends_shell(self):
        t = (ROOT / ".github" / "workflows" / "mac-cpu-route.yml").read_text()
        self.assertRegex(t, r"\n\s+/bin/bash install\.sh ", "the route job must run /bin/bash, not PATH's bash")
        self.assertNotRegex(t, r"(?m)^\s+bash install\.sh")
        self.assertIn("/bin/bash --version", t)

    def test_install_runs_its_child_script_with_its_own_shell(self):
        t = INSTALL_SH.read_text()
        self.assertNotRegex(t, r'(?m)^\s+bash "\$CRONS_SH"', "a child run by PATH's bash is not the friend's shell")
        self.assertIn('"$BASH" "$CRONS_SH"', t)


if __name__ == "__main__":
    unittest.main()

"""R-6 (model-choice round 1; env-writer EW4-05): no in-process CUDA probe under a CUDA_VISIBLE_DEVICES that selects
no device.

MEASURED on the reference host (WSL2): libcuda's cuInit SEGFAULTS the interpreter -- rc 139, beyond any `except` --
when CUDA_VISIBLE_DEVICES is '', ' ', '-1', 'none', 'NoDevFiles' or an index the host lacks, and
`torch.cuda.is_available()` IS a cuInit. Twelve in-process sites asked it directly (organs/website_memory,
whisper_network, qwen_reranker, hybrid_retrieval, embedding_upgrade x3, claude_memory, model_garden, wizard_config,
capability/detect.py and install.sh's C.cuda-gate heredoc): each died with rc 139 under CUDA_VISIBLE_DEVICES="" on the
base (executed per site: iterations/scratch_records/r1_waveC/r6_proof.log). Each now asks voidloom_device.cuda_available,
which answers "no" for a device-less value WITHOUT calling the driver.

This binds:
  * the GATE (AST, never grep): no `torch.cuda.is_available()` CALL in organs/ or capability/ outside the two places
    that decide first (voidloom_device.cuda_available, capability/detect.probe_torch_cuda after _cuda_selection), and
    install.sh's gate heredoc asks voidloom_device, not torch -- a new direct call turns this red;
  * the RULE, with a fake torch that records whether it was asked and a fake nvidia-smi: a device-less value is
    answered "no" and torch is NEVER asked; a listed card, an unset variable and a UUID reach torch;
  * the REAL crash, where torch is importable: a child with CUDA_VISIBLE_DEVICES="" exits 0 and says False.
"""
import ast
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
# (file, function) pairs allowed to CALL torch.cuda.is_available() in-process, each because it decides first
ALLOWED = {("organs/voidloom_device.py", "cuda_available"), ("capability/detect.py", "probe_torch_cuda")}


def _is_cuda_probe(node):
    f = getattr(node, "func", None)
    return (isinstance(node, ast.Call) and isinstance(f, ast.Attribute) and f.attr == "is_available"
            and isinstance(f.value, ast.Attribute) and f.value.attr == "cuda")


def _probe_sites(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    out = []

    def walk(node, fn):
        for ch in ast.iter_child_nodes(node):
            name = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if _is_cuda_probe(ch):
                out.append((rel, fn, ch.lineno))
            walk(ch, name)
    walk(tree, "<module>")
    return out


def _load_device():
    import importlib.util
    spec = importlib.util.spec_from_file_location("vd_under_test", str(ORGANS / "voidloom_device.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTorch:
    def __init__(self, answer=True):
        self.asked = 0
        self.cuda = types.SimpleNamespace(is_available=self._ask)
        self.answer = answer

    def _ask(self):
        self.asked += 1
        return self.answer


class TestTheGate(unittest.TestCase):

    def test_no_direct_in_process_cuda_probe_outside_the_deciders(self):
        files = sorted(str(p.relative_to(ROOT)) for d in ("organs", "capability") for p in (ROOT / d).glob("*.py"))
        self.assertGreater(len(files), 10, "the population is empty -- this gate would pass vacuously")
        found = [s for f in files for s in _probe_sites(f)]
        bad = [s for s in found if (s[0], s[1]) not in ALLOWED]
        self.assertEqual(bad, [], "a direct torch.cuda.is_available() call: route it through "
                                  "voidloom_device.cuda_available (R-6) -- %s" % bad)
        self.assertEqual(sorted({(s[0], s[1]) for s in found}), sorted(ALLOWED),
                         "the deciders themselves must still exist and ask torch")

    def test_a_planted_direct_probe_turns_the_gate_red(self):
        tree = ast.parse("def f():\n    import torch\n    return 'cuda:0' if torch.cuda.is_available() else 'cpu'\n")
        self.assertEqual(sum(1 for n in ast.walk(tree) if _is_cuda_probe(n)), 1)

    def test_the_installers_cuda_gate_asks_voidloom_device_not_torch(self):
        s = (ROOT / "install.sh").read_text(encoding="utf-8")
        s.index('CUDA_OK="$(hd_cuda_ok)"')          # the call site; the heredoc lives in hd_cuda_ok() (round 2, B2)
        i = s.index("hd_cuda_ok() {")
        body = s[s.index("<<'PYEOF'", i):s.index("\nPYEOF\n", i)]
        self.assertIn("voidloom_device.cuda_available(torch)", body)
        self.assertNotIn("torch.cuda.is_available()", body)

    # Handoff round 1, lane L1: the device sites now ask voidloom_device.torch_device(), the ONE resolver, which asks
    # cuda_available() for CUDA and ROCm -- so R-6 holds behind it. Files still on the old per-organ rule until the L2
    # patch (for_L2/L1_device_sites_organs.patch) lands; that patch empties this set.
    _STILL_ON_CUDA_AVAILABLE = set()

    def test_the_twelve_sites_route_through_the_rule(self):
        for rel in ("organs/website_memory.py", "organs/whisper_network.py", "organs/qwen_reranker.py",
                    "organs/hybrid_retrieval.py", "organs/embedding_upgrade.py", "organs/claude_memory.py",
                    "organs/model_garden.py", "organs/wizard_config.py"):
            s = (ROOT / rel).read_text(encoding="utf-8")
            if rel in self._STILL_ON_CUDA_AVAILABLE:
                self.assertIn("from voidloom_device import cuda_available", s, rel)
            else:
                self.assertIn("from voidloom_device import torch_device", s, rel)
        self.assertEqual((ROOT / "organs/embedding_upgrade.py").read_text(encoding="utf-8")
                         .count("_torch_device(torch)"), 3)
        dv = (ROOT / "organs/voidloom_device.py").read_text(encoding="utf-8")
        self.assertIn("return cuda_available(torch), ", dv)          # torch_device's CUDA/ROCm confirmation is R-6's


class TestTheRule(unittest.TestCase):

    def setUp(self):
        self.vd = _load_device()
        self.tmp = tempfile.mkdtemp(prefix="vl_cuda_guard_")
        fake = Path(self.tmp) / "nvidia-smi"
        fake.write_text("#!/bin/sh\nprintf '0\\n1\\n'\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.vd.NVIDIA_SMI_DIRS = (self.tmp,)
        self.old_path = os.environ.get("PATH")
        self.old_cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
        os.environ["PATH"] = "/nonexistent-for-this-test"      # the fake is found through NVIDIA_SMI_DIRS only

    def tearDown(self):
        os.environ["PATH"] = self.old_path or ""
        if self.old_cvd is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = self.old_cvd
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ask(self, cvd, answer=True):
        self.vd._listed_cache.clear()
        if cvd is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = cvd
        t = _FakeTorch(answer)
        return self.vd.cuda_available(t), t.asked, self.vd.cuda_selects_a_device()[1]

    def test_a_device_less_value_is_no_and_torch_is_never_asked(self):
        for cvd in ("", " ", "-1", "none", "NoDevFiles", "7", ",1"):
            ok, asked, why = self._ask(cvd)
            self.assertEqual((ok, asked), (False, 0), "CUDA_VISIBLE_DEVICES=%r: %s" % (cvd, why))
            self.assertIn("selects no device", why)

    def test_a_listed_card_an_unset_variable_and_a_uuid_reach_torch(self):
        for cvd in ("1", "0,1", None, "GPU-0123abcd"):
            ok, asked, why = self._ask(cvd)
            self.assertEqual((ok, asked), (True, 1), "CUDA_VISIBLE_DEVICES=%r: %s" % (cvd, why))
        ok, asked, _why = self._ask("1", answer=False)
        self.assertEqual((ok, asked), (False, 1), "torch's own 'no' must still be heard")

    def test_without_nvidia_smi_an_index_is_trusted_and_empty_is_still_refused(self):
        self.vd.NVIDIA_SMI_DIRS = ()
        ok, asked, why = self._ask("5")
        self.assertEqual((ok, asked), (True, 1), why)
        self.assertIn("could not be asked", why)
        self.assertEqual(self._ask("")[:2], (False, 0))

    def test_a_torch_that_raises_is_no_cuda_never_a_crash(self):
        self.vd._listed_cache.clear()
        # gpu-pin-gate: a test INPUT to the rule (a UUID, so a fake torch is asked; nothing initialises CUDA; tearDown restores it), not a pin
        os.environ["CUDA_VISIBLE_DEVICES"] = "GPU-0123abcd"
        boom = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: 1 / 0))
        self.assertFalse(self.vd.cuda_available(boom))


class TestTheRealCrashIsGone(unittest.TestCase):
    """Where torch is importable: the exact call that died with rc 139 on the base, in a child."""

    def test_an_empty_cuda_visible_devices_exits_zero_and_says_false(self):
        try:
            import importlib.util
            if importlib.util.find_spec("torch") is None:
                self.skipTest("torch is not importable by this interpreter: the real crash cannot be reproduced here")
        except Exception:
            self.skipTest("torch spec lookup failed")
        code = ("import sys; sys.path.insert(0, %r); import voidloom_device, torch; "
                "print('ANSWER', voidloom_device.cuda_available(torch))" % str(ORGANS))
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual(p.returncode, 0, "the child died (rc %s): %s" % (p.returncode, p.stderr[-400:]))
        self.assertIn("ANSWER False", p.stdout)


if __name__ == "__main__":
    unittest.main()

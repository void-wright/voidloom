"""A CPU answer hides every CUDA card from the process that got it, and from every process it starts.

Before: only the entry modules that call apply_cuda_pin() at import wrote CUDA_VISIBLE_DEVICES; a process that asked
torch_device() and got 'cpu' (a refused card, a declared compute card that is not listed, a crashed nvidia-smi) left
the variable UNSET, so card 0 -- the display card on a two-card host -- stayed visible to any library that probes CUDA
on its own, and to every child process. model_garden imported torch before any pin.

Now a CPU answer writes the engine's own "no device" (an empty CUDA_VISIBLE_DEVICES, always with torch's NVML-based
check so a CUDA build answers "no CUDA" without initialising the driver), model_garden pins before it imports torch, a
genuine one-card machine still gets its card, and a machine with no NVIDIA tool at all is left as found.

Child interpreters with a stub torch against the fake nvidia-smi scripts under fixtures/: nothing initialises CUDA.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DROPPED = FIXTURES / "fakesmi_card_dropped"
ONE_CARD = FIXTURES / "fakesmi_one_card"
SEGFAULT = FIXTURES / "fakesmi_segfault"

# A stub `torch`, installed as a module finder, that records CUDA_VISIBLE_DEVICES at the moment it is imported.
STUB = r'''
import os, sys, types, importlib.abc, importlib.machinery
SEEN = {}
class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name == "torch":
            return importlib.machinery.ModuleSpec(name, self)
        return None
    def create_module(self, spec):
        return None
    def exec_module(self, m):
        SEEN["cvd_at_import"] = os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>")
        m.cuda = types.SimpleNamespace(is_available=lambda: SEEN.setdefault("asked", True) and True,
                                       device_count=lambda: 1, empty_cache=lambda: None)
        m.version = types.SimpleNamespace(hip=None, cuda="stub")
        m.xpu = types.SimpleNamespace(is_available=lambda: False)
        m.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))
sys.meta_path.insert(0, _Finder())
'''

TORCH_DEVICE = STUB + textwrap.dedent(r'''
    import json, subprocess
    sys.path.insert(0, sys.argv[1])
    import voidloom_device as vd
    if sys.argv[2] == "no-nvidia":
        vd.NVIDIA_SMI_DIRS = ()
    dev = vd.torch_device()
    child = subprocess.run([sys.executable, "-I", "-c",
                            "import os, json; print(json.dumps([os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>'),"
                            " os.environ.get('PYTORCH_NVML_BASED_CUDA_CHECK')]))"],
                           capture_output=True, text=True).stdout.strip()
    print(json.dumps({"device": dev, "cvd": os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>"),
                      "nvml": os.environ.get("PYTORCH_NVML_BASED_CUDA_CHECK"), "child": json.loads(child),
                      "basis": vd.resolve_accelerator()[2]}))
''')

GARDEN = STUB + textwrap.dedent(r'''
    import json
    sys.path.insert(0, sys.argv[1])
    import model_garden as mg
    spec = type("Spec", (), {"device": "auto"})()
    dev = mg._resolve_device(spec)
    print(json.dumps({"device": dev, "cvd_at_torch_import": SEEN.get("cvd_at_import"),
                      "cvd": os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>")}))
''')

_STRIP = ("PYTHONPATH", "CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE", "WIZARD_DISPLAY_INDEX", "WIZARD_ACCELERATOR",
          "WIZARD_ENV_FILE", "WIZARD_CONFIG_PATH", "PYTORCH_NVML_BASED_CUDA_CHECK")


class _Child(unittest.TestCase):

    def setUp(self):
        for f in ("voidloom_device.py", "model_garden.py"):
            if not (ORGANS / f).is_file():
                raise unittest.SkipTest("tree lacks organs/%s" % f)
        for d in (DROPPED, ONE_CARD, SEGFAULT):
            if not os.access(str(d / "nvidia-smi"), os.X_OK):
                raise unittest.SkipTest("fixture %s is not executable here" % d.name)
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_cpu_hides_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.tmp), ignore_errors=True))
        (self.tmp / "td.py").write_text(TORCH_DEVICE, encoding="utf-8")
        (self.tmp / "garden.py").write_text(GARDEN, encoding="utf-8")

    def run_child(self, script, smi_dir, mode="", **extra):
        env = {k: v for k, v in os.environ.items() if k not in _STRIP}
        env.update({"PATH": ("%s:/usr/bin:/bin" % smi_dir) if smi_dir else "/usr/bin:/bin", "HOME": str(self.tmp),
                    "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_ENV_FILE": str(self.tmp / "absent-env"),
                    "WIZARD_CONFIG_PATH": str(self.tmp / "absent-config.json")})
        env.update(extra)
        p = subprocess.run([sys.executable, "-I", str(self.tmp / script), str(ORGANS), mode], env=env,
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return json.loads(p.stdout.strip().splitlines()[-1])


class ACpuAnswerHidesEveryCard(_Child):

    def _assert_hidden(self, out):
        self.assertEqual(out["device"], "cpu", out)
        self.assertEqual((out["cvd"], out["nvml"]), ("", "1"), out)
        # every process this one starts inherits the same "no device"
        self.assertEqual(out["child"], ["", "1"], out)

    def test_the_declared_compute_card_is_not_listed(self):
        self._assert_hidden(self.run_child("td.py", DROPPED, WIZARD_CUDA_DEVICE="1", WIZARD_DISPLAY_INDEX="0"))

    def test_nothing_declared_and_the_fallback_is_refused(self):
        self._assert_hidden(self.run_child("td.py", DROPPED))

    def test_a_crashed_nvidia_smi(self):
        self._assert_hidden(self.run_child("td.py", SEGFAULT, WIZARD_CUDA_DEVICE="1"))

    def test_a_genuine_one_card_machine_keeps_its_card(self):
        out = self.run_child("td.py", ONE_CARD)
        self.assertEqual((out["device"], out["cvd"]), ("cuda:0", "0"), out)

    def test_a_machine_with_no_nvidia_tool_is_left_as_found(self):
        out = self.run_child("td.py", None, "no-nvidia")
        self.assertEqual((out["device"], out["cvd"], out["nvml"]), ("cpu", "<unset>", None), out)


class ModelGardenPinsBeforeTorch(_Child):

    def test_torch_is_imported_under_the_mask(self):
        out = self.run_child("garden.py", DROPPED)
        self.assertEqual(out["device"], "cpu", out)
        self.assertEqual(out["cvd_at_torch_import"], "", out)

    def test_a_declared_card_that_is_not_listed_already_hides_the_display_at_torch_import(self):
        # the owner's index is written as declared; nvidia-smi lists only card 0, so '1' selects no listed card
        out = self.run_child("garden.py", DROPPED, WIZARD_CUDA_DEVICE="1", WIZARD_DISPLAY_INDEX="0")
        self.assertEqual(out["device"], "cpu", out)
        self.assertEqual(out["cvd_at_torch_import"], "1", out)

    def test_a_listed_compute_card_is_pinned_before_torch(self):
        out = self.run_child("garden.py", ONE_CARD, WIZARD_CUDA_DEVICE="0")
        self.assertEqual(out["cvd_at_torch_import"], "0", out)


if __name__ == "__main__":
    unittest.main()

"""The resolver never falls back onto a card that drives a display while another card exists but cannot be read.

The host shape: two NVIDIA cards, the compute card has dropped out of the driver ("Unable to determine the device
handle for GPU1"), so nvidia-smi LISTS one card -- the display card, display_active=Enabled -- while
`--query-gpu=count` still COUNTS two. With nothing declared, the package default is card 0. Before this test, the
resolver planned and pinned that card (CUDA_VISIBLE_DEVICES='0'), and an ambient `CUDA_VISIBLE_DEVICES=0` was
honoured because "one card is listed". Both now refuse: no device, CPU, and the basis and the stderr line say why.

A genuine one-card machine (count 1, listed 1: a laptop whose only GPU drives its screen) keeps card 0.

Bound with fake nvidia-smi scripts under fixtures/ and a stub torch in a child interpreter: no real driver is asked
and nothing initialises CUDA.
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

PROBE = textwrap.dedent(r'''
    import json, os, sys, types
    sys.path.insert(0, sys.argv[1])
    import voidloom_device as vd
    asked = []
    stub = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: asked.append("cuda.is_available") or True,
                                   device_count=lambda: 1, get_device_name=lambda i: "stub"),
        version=types.SimpleNamespace(hip=None),
        xpu=types.SimpleNamespace(is_available=lambda: False),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)))
    applied = vd.apply_cuda_pin()            # what every entry module does before importing torch
    pin = vd.resolve_cuda_device()
    stdlib = vd.resolve_accelerator(confirm=False)
    confirmed = vd.resolve_accelerator(confirm=True, torch_mod=stub)
    print(json.dumps({"pin": list(pin), "stdlib": list(stdlib[:4]), "confirmed": list(confirmed[:4]),
                      "cvd": os.environ.get("CUDA_VISIBLE_DEVICES"), "asked": asked,
                      "nvml_check": os.environ.get("PYTORCH_NVML_BASED_CUDA_CHECK")}))
''')

_STRIP = ("PYTHONPATH", "CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE", "WIZARD_DISPLAY_INDEX", "WIZARD_ACCELERATOR",
          "WIZARD_ENV_FILE", "WIZARD_CONFIG_PATH", "PYTORCH_NVML_BASED_CUDA_CHECK", "HIP_VISIBLE_DEVICES",
          "ROCR_VISIBLE_DEVICES", "ZE_AFFINITY_MASK")


class _Child(unittest.TestCase):

    def setUp(self):
        if not (ORGANS / "voidloom_device.py").is_file():
            raise unittest.SkipTest("tree lacks organs/voidloom_device.py")
        for d in (DROPPED, ONE_CARD):
            if not os.access(str(d / "nvidia-smi"), os.X_OK):
                raise unittest.SkipTest("fixture %s is not executable here" % d.name)
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_display_fallback_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.tmp), ignore_errors=True))
        (self.tmp / "probe.py").write_text(PROBE, encoding="utf-8")

    def run_probe(self, smi_dir, **extra):
        env = {k: v for k, v in os.environ.items() if k not in _STRIP}
        env.update({"PATH": "%s:/usr/bin:/bin" % smi_dir, "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
                    "WIZARD_ENV_FILE": str(self.tmp / "absent-env"),
                    "WIZARD_CONFIG_PATH": str(self.tmp / "absent-config.json")})
        env.update(extra)
        p = subprocess.run([sys.executable, "-I", str(self.tmp / "probe.py"), str(ORGANS)], env=env,
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr


class TheDisplayCardIsNeverTheFallback(_Child):

    def _assert_refused(self, out, err):
        backend, device, basis, _shared = out["stdlib"]
        self.assertEqual((backend, device), ("cpu", "cpu"), out)
        self.assertIn("refused", basis, out)
        self.assertEqual(out["confirmed"][:2], ["cpu", "cpu"], out)
        # Independent of every sentence above: the process never pins card 0 and torch is never asked about CUDA.
        self.assertNotEqual(out["cvd"], "0", out)
        self.assertIsNotNone(out["cvd"], "a refusal must hide the cards, not leave the variable unset: %r" % out)
        self.assertEqual(out["asked"], [], out)
        # The engine's own "no device": an empty value, always with torch's NVML-based check so that a CUDA build
        # answers "no CUDA" without initialising the driver.
        self.assertEqual(out["cvd"], "", out)
        self.assertEqual(out["nvml_check"], "1", out)

    def test_nothing_declared_and_the_compute_card_dropped_out(self):
        out, err = self.run_probe(DROPPED)
        self._assert_refused(out, err)
        self.assertEqual(out["pin"][0], "", out)
        self.assertIn("display", out["pin"][1], out)
        # The stderr line says which card, and that nvidia-smi counts more cards than it can read.
        self.assertIn("REFUSED", err)
        self.assertIn("counts 2", err)

    def test_display_declared_and_the_compute_card_dropped_out(self):
        out, err = self.run_probe(DROPPED, WIZARD_DISPLAY_INDEX="0")
        self._assert_refused(out, err)

    def test_ambient_pin_on_the_display_while_another_card_is_unreadable(self):
        out, err = self.run_probe(DROPPED, CUDA_VISIBLE_DEVICES="0", WIZARD_DISPLAY_INDEX="0")
        self._assert_refused(out, err)
        self.assertTrue(out["pin"][1].startswith("ambient-display-refused"), out)

    def test_a_damaged_declaration_does_not_fall_back_onto_the_display(self):
        empty = self.tmp / "empty-env"
        empty.write_text("", encoding="utf-8")
        out, err = self.run_probe(DROPPED, WIZARD_ENV_FILE=str(empty))
        # Before, the damaged declaration's safe value pinned card 0 -- the display -- whatever the accelerator said.
        self.assertEqual(out["pin"][0], "", out)
        self.assertIn("refused", out["pin"][1], out)
        self.assertTrue(out["pin"][1].startswith("degraded:"), "still reported as a damaged declaration: %r" % out)
        self.assertEqual(out["stdlib"][:2], ["cpu", "cpu"], out)
        self.assertEqual((out["cvd"], out["nvml_check"], out["asked"]), ("", "1", []), out)

    def test_two_listed_cards_and_nothing_declared_never_default_to_the_display(self):
        two = self.tmp / "two_cards"
        two.mkdir()
        (two / "nvidia-smi").write_text(
            "#!/bin/sh\n# two readable cards; card 0 drives the display\n"
            "case \"$*\" in\n"
            "  *--query-gpu=count*) printf '2\\n2\\n' ;;\n"
            "  *--query-gpu=index,uuid,display_active*) printf '0, GPU-00000000-fake-a, Enabled\\n"
            "1, GPU-11111111-fake-b, Disabled\\n' ;;\n"
            "  *--query-gpu=index*) printf '0\\n1\\n' ;;\n"
            "  *) exit 0 ;;\nesac\n", encoding="utf-8")
        os.chmod(str(two / "nvidia-smi"), 0o755)
        out, err = self.run_probe(two)
        self._assert_refused(out, err)
        self.assertIn("more than one card", out["pin"][1], out)
        # the owner's typed choice of the display card is still his
        out, err = self.run_probe(two, WIZARD_CUDA_DEVICE="0")
        self.assertEqual(out["pin"], ["0", "env"], out)

    def test_a_genuine_one_card_machine_keeps_its_card(self):
        out, err = self.run_probe(ONE_CARD)
        self.assertEqual(out["pin"], ["0", "default"], out)
        self.assertEqual(out["stdlib"][:2], ["cuda", "cuda:0"], out)
        self.assertEqual(out["cvd"], "0", out)
        self.assertEqual(out["asked"], ["cuda.is_available"], out)
        self.assertNotIn("REFUSED", err)

    def test_a_genuine_one_card_machine_honours_an_ambient_pin(self):
        out, err = self.run_probe(ONE_CARD, CUDA_VISIBLE_DEVICES="0")
        self.assertEqual(out["pin"], ["0", "ambient"], out)
        self.assertEqual(out["cvd"], "0", out)


if __name__ == "__main__":
    unittest.main()

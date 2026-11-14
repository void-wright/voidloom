"""A crashed nvidia-smi, or a declared compute card the driver no longer lists, reads DEGRADED -- never "no accelerator".

Before: a non-zero or signal exit of nvidia-smi was "cannot be asked", the resolver answered "auto:no accelerator
observed", cuda_selects_a_device() trusted the declared index, and `voidloom doctor` said degraded:false,
warnings:[] -- while the reranker had silently gone off with the words "no accelerator (CPU-only)". A declared card
that nvidia-smi does not list read the same way.

Now: doctor's accelerator check says degraded:true with the cause in its basis, doctor carries a warning, and the
reranker gate says the accelerator is unavailable and why. A machine with no NVIDIA tool still reads plain CPU, and a
genuine one-card machine reads its card.

The verb runs in a child interpreter on a migrated scratch brain, with fake nvidia-smi scripts under fixtures/ first
on PATH. No real driver is asked and nothing initialises CUDA.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from voidloom.tests.test_doctor_brain_verdict import _Brain, ROOT

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DROPPED = FIXTURES / "fakesmi_card_dropped"
ONE_CARD = FIXTURES / "fakesmi_one_card"
SEGFAULT = FIXTURES / "fakesmi_segfault"

_STRIP = ("PYTHONPATH", "CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE", "WIZARD_DISPLAY_INDEX", "WIZARD_ACCELERATOR",
          "WIZARD_ENV_FILE", "WIZARD_CONFIG_PATH", "PYTORCH_NVML_BASED_CUDA_CHECK", "WIZARD_LIBRARY_DB",
          "CLAUDE_MEMORY_DB")


class TheDoctorSeesAMissingCard(_Brain):

    def run_doctor(self, smi_dir, no_nvidia=False, **extra):
        for d in (DROPPED, ONE_CARD, SEGFAULT):
            if not os.access(str(d / "nvidia-smi"), os.X_OK):
                self.skipTest("fixture %s is not executable here" % d.name)
        self.memory(whash=self.h)
        home = self.d / "home"
        home.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in _STRIP}
        env.update({"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1",
                    "PATH": ("%s:/usr/bin:/bin" % smi_dir) if smi_dir else "/usr/bin:/bin",
                    "WIZARD_ENV_FILE": str(self.d / "absent-env"),
                    "WIZARD_CONFIG_PATH": str(self.d / "absent-config.json")})
        env.update(extra)
        code = ("import sys, json\n"
                f"sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(ROOT / 'organs')!r})\n"
                "import voidloom_device\n"
                + ("voidloom_device.NVIDIA_SMI_DIRS = ()\n" if no_nvidia else "") +
                "import memory_write as mw\n"
                "if not hasattr(mw, 'derived_layer_report'):\n"
                "    mw.derived_layer_report = lambda c: {'stale_derived': 0}\n"
                "from voidloom import cli\n"
                "cli._absent_model = lambda *a: None\n"
                "cli.doctor_hook_join = lambda *a, **k: {'platforms': {}}\n"
                f"sys.exit(cli.dispatch(['doctor', '--db', {str(self.db)!r}, '--json']))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(self.d), env=env, capture_output=True, text=True,
                           timeout=180, stdin=subprocess.DEVNULL)
        try:
            return json.loads(r.stdout)["data"]
        except Exception:
            self.fail("doctor printed no envelope (rc %s): %s" % (r.returncode, r.stderr[-1500:]))

    def _assert_degraded(self, d, cause):
        acc = d["checks"]["accelerator"]
        self.assertEqual(acc.get("backend"), "cpu", acc)
        self.assertIs(acc.get("degraded"), True, acc)
        self.assertIn(cause, acc.get("basis") or "", acc)
        # Independent of the accelerator's own flag: the report's warnings name it, and the reranker gate says the
        # accelerator is unavailable rather than calling this a CPU-only machine.
        self.assertTrue([w for w in d["warnings"] if "accelerator" in w], d["warnings"])
        why = d["checks"]["reranker"].get("why") or ""
        self.assertNotIn("CPU-only", why, d["checks"]["reranker"])
        self.assertIn("unavailable", why, d["checks"]["reranker"])
        # the BRAIN is still sound: a missing card is not a broken brain
        self.assertIs(d["brain_healthy"], True, d["reason"])

    def test_a_crashed_nvidia_smi_with_a_declared_card(self):
        self._assert_degraded(self.run_doctor(SEGFAULT, WIZARD_CUDA_DEVICE="1"), "signal 11")

    def test_a_crashed_nvidia_smi_with_nothing_declared(self):
        self._assert_degraded(self.run_doctor(SEGFAULT), "signal 11")

    def test_a_declared_compute_card_the_driver_does_not_list(self):
        d = self.run_doctor(DROPPED, WIZARD_CUDA_DEVICE="1", WIZARD_DISPLAY_INDEX="0")
        self._assert_degraded(d, "card 1 is not one nvidia-smi lists")

    def test_a_machine_with_no_nvidia_tool_reads_plain_cpu(self):
        d = self.run_doctor(None, no_nvidia=True)
        acc = d["checks"]["accelerator"]
        self.assertEqual((acc.get("backend"), acc.get("degraded")), ("cpu", False), acc)
        self.assertNotIn("DEGRADED", acc.get("line") or "", acc)
        self.assertFalse([w for w in d["warnings"] if "accelerator" in w], d["warnings"])

    def test_a_genuine_one_card_machine_reads_its_card(self):
        d = self.run_doctor(ONE_CARD)
        acc = d["checks"]["accelerator"]
        self.assertEqual((acc.get("backend"), acc.get("degraded")), ("cuda", False), acc)
        self.assertFalse([w for w in d["warnings"] if "accelerator" in w], d["warnings"])


class TheDoctorLineAndFlagAgree(unittest.TestCase):
    """The accelerator line's DEGRADED clause and the degraded flag are one fact, said twice."""

    def test_the_flag_follows_the_line(self):
        import types
        from unittest import mock
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "organs"))
        from voidloom import cli
        vd = types.SimpleNamespace(
            accelerator_report=lambda confirm=False: {"backend": "cpu", "torch_device": "cpu",
                                                      "basis": "auto:no accelerator observed", "declared": {}},
            cpu_declaration=lambda: None, hides_every_cuda_card=lambda environ=None: False,
            is_degraded=lambda b: b.startswith("degraded:"), nvidia_smi=lambda: "/fake/nvidia-smi")
        hr = types.SimpleNamespace(accelerator_proof_label=lambda backend, rec: ("PROVEN", "test"))
        # gpu-pin-gate: a fake resolver in this process; nothing imports torch here
        visible = {"CUDA_VISIBLE_DEVICES": "1"}
        with mock.patch.dict(sys.modules, {"voidloom_device": vd, "hybrid_retrieval": hr}), \
                mock.patch.dict(os.environ, visible):
            out = cli._doctor_accelerator()
        self.assertIn("DEGRADED", out["line"], out)
        self.assertIs(out["degraded"], True, out)


if __name__ == "__main__":
    unittest.main()

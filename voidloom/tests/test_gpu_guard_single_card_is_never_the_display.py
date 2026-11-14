"""gpu_guard never grants a single-card plan on a card that drives a display while the host has another card.

The host shape: two NVIDIA cards, the compute card has dropped out of the driver, so nvidia-smi lists ONE card -- the
display card -- and still counts two. gpu_guard read "one GPU visible" as the single-GPU topology and granted
`cuda:0-single` on it ("single-card on the compute card is always safe", but the one card was the display). Now the
single grant is refused (cloud plan) when the configured compute card is not visible, or when the only visible card
drives a display on a host that counts, pins or configures another card. A genuine one-card machine still gets its
single plan. A crashed nvidia-smi is named as a crash, not as "no GPUs".

In-process cases use gpu_guard's own test seams; the end-to-end cases run a child interpreter against the fake
nvidia-smi scripts under fixtures/. No real driver is asked and nothing initialises CUDA.
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

DISPLAY = "GPU-00000000-fake-0000-0000-000000000000"

CHILD = textwrap.dedent(r'''
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import gpu_guard as g
    v = g.acquire(g.BurstProfile(footprint_mb=4000, est_duration_s=60, purpose="test"), _now_hour=14)
    print(json.dumps({"granted": v.granted, "plan": v.device_plan, "reasons": v.reasons}))
''')

_STRIP = ("PYTHONPATH", "CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE", "WIZARD_DISPLAY_INDEX", "WIZARD_ACCELERATOR",
          "WIZARD_ENV_FILE", "WIZARD_CONFIG_PATH")


def _guard():
    if str(ORGANS) not in sys.path:
        sys.path.insert(0, str(ORGANS))
    import gpu_guard
    return gpu_guard


class TheSingleGrantInProcess(unittest.TestCase):

    def setUp(self):
        if not (ORGANS / "gpu_guard.py").is_file():
            raise unittest.SkipTest("tree lacks organs/gpu_guard.py")
        self.g = _guard()
        self.one = [self.g.GpuInfo(index=0, uuid=DISPLAY, name="Fake", mem_total_mb=11264, mem_used_mb=900,
                                   util_pct=10, compute_cap="7.5")]
        self.profile = self.g.BurstProfile(footprint_mb=4000, est_duration_s=60, purpose="test")

    def _acquire(self, policy, **seams):
        pol = dict(self.g.DEFAULT_POLICY, _declared_display=(None, "not declared"), **policy)
        return self.g.acquire(self.profile, policy=pol, _gpus=self.one, _now_hour=14, _nvlink=False, **seams)

    def test_the_configured_compute_card_is_not_visible(self):
        v = self._acquire({"compute_index": "1", "display_uuid": None}, _display_active={DISPLAY: True})
        self.assertEqual((v.granted, v.device_plan), (False, self.g.PLAN_CLOUD), v.reasons)
        self.assertTrue(any(r.startswith("REFUSE-SINGLE") and "compute card 1 is not visible" in r
                            for r in v.reasons), v.reasons)

    def test_the_manifest_names_the_only_visible_card_as_the_display(self):
        v = self._acquire({"compute_index": "0", "display_uuid": DISPLAY}, _display_active={})
        self.assertEqual((v.granted, v.device_plan), (False, self.g.PLAN_CLOUD), v.reasons)

    def test_the_only_visible_card_drives_a_display_and_the_driver_counts_two(self):
        v = self._acquire({"compute_index": "0", "display_uuid": None}, _display_active={DISPLAY: True},
                          _counted=2)
        self.assertEqual((v.granted, v.device_plan), (False, self.g.PLAN_CLOUD), v.reasons)

    def test_a_genuine_one_card_machine_keeps_its_single_plan(self):
        v = self._acquire({"compute_index": "0", "display_uuid": None}, _display_active={DISPLAY: True},
                          _counted=1)
        self.assertEqual((v.granted, v.device_plan), (True, self.g.PLAN_SINGLE), v.reasons)
        v = self._acquire({"compute_index": "0", "display_uuid": None}, _display_active={DISPLAY: True})
        self.assertEqual((v.granted, v.device_plan), (True, self.g.PLAN_SINGLE), v.reasons)


class TheSingleGrantAgainstAFakeDriver(unittest.TestCase):

    def setUp(self):
        if not (ORGANS / "gpu_guard.py").is_file():
            raise unittest.SkipTest("tree lacks organs/gpu_guard.py")
        for d in (DROPPED, ONE_CARD, SEGFAULT):
            if not os.access(str(d / "nvidia-smi"), os.X_OK):
                raise unittest.SkipTest("fixture %s is not executable here" % d.name)
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_guard_single_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.tmp), ignore_errors=True))
        (self.tmp / "child.py").write_text(CHILD, encoding="utf-8")

    def _run(self, smi_dir, **extra):
        env = {k: v for k, v in os.environ.items() if k not in _STRIP}
        env.update({"PATH": "%s:/usr/bin:/bin" % smi_dir, "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
                    "WIZARD_ENV_FILE": str(self.tmp / "absent-env"),
                    "WIZARD_CONFIG_PATH": str(self.tmp / "absent-config.json")})
        env.update(extra)
        p = subprocess.run([sys.executable, "-I", str(self.tmp / "child.py"), str(ORGANS)], env=env,
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_nothing_declared_and_the_compute_card_dropped_out(self):
        v = self._run(DROPPED)
        self.assertEqual((v["granted"], v["plan"]), (False, "cloud-claude-p"), v["reasons"])
        self.assertTrue(any(r.startswith("REFUSE-SINGLE") for r in v["reasons"]), v["reasons"])

    def test_the_declared_compute_card_dropped_out(self):
        v = self._run(DROPPED, WIZARD_CUDA_DEVICE="1", WIZARD_DISPLAY_INDEX="0")
        self.assertEqual((v["granted"], v["plan"]), (False, "cloud-claude-p"), v["reasons"])

    def test_a_genuine_one_card_machine(self):
        v = self._run(ONE_CARD)
        self.assertEqual((v["granted"], v["plan"]), (True, "cuda:0-single"), v["reasons"])

    def test_a_crashed_driver_tool_is_named_as_a_crash(self):
        v = self._run(SEGFAULT)
        self.assertEqual((v["granted"], v["plan"]), (False, "cloud-claude-p"), v["reasons"])
        text = " ".join(v["reasons"])
        self.assertIn("signal 11", text)
        self.assertNotIn("no GPUs visible", text)


if __name__ == "__main__":
    unittest.main()

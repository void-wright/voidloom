"""R-8 (model-choice round 1; env-writer EW5-10): the runtime GPU guard honours the OWNER's declared display card.

The installer's GPU question records the owner's answers as WIZARD_DISPLAY_INDEX / WIZARD_CUDA_DEVICE, outside the
env file's managed block. Until this round NO organ read WIZARD_DISPLAY_INDEX: gpu_guard (and voidloom_manifest's
durable pin) still found the monitor's card by INFERENCE -- the complement of the compute card, display_active, the
idle heuristic. Two shapes where that inference names the wrong card, both on a host whose monitor is ASLEEP
(display_active=Disabled on every card, measured 2026-09-19):
  * two cards, the owner put the jobs on the monitor's card (the typed confirmation): WIZARD_CUDA_DEVICE=0 and
    WIZARD_DISPLAY_INDEX=0 -> the complement says card 1;
  * three cards, compute on 2, monitor on 0, another job holding card 1 -> the complement is ambiguous and the idle
    heuristic names card 1.
Declared, never inferred: the declaration now wins, and every discovery SAYS which it used. Bound with a FAKE
nvidia-smi (a shell script); no real driver is touched and nothing initialises CUDA.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"


def _fake_smi(cards):
    """cards: [(index, uuid, mem_used_mib)] -- every card display_active=Disabled (the monitor is asleep)."""
    q = "\\n".join("%d, %s, Fake 2080 Ti, 11264, %d, 0, 7.5" % c for c in cards)
    da = "\\n".join("%s, Disabled" % c[1] for c in cards)
    return textwrap.dedent("""\
        #!/bin/sh
        case "$*" in
          *uuid,display_active*) printf '%s\\n' ;;
          *index,uuid,name*) printf '%s\\n' ;;
          *nvlink*) exit 0 ;;
          *) exit 0 ;;
        esac
        """) % (da, q)


DRIVER = textwrap.dedent(r'''
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import gpu_guard, voidloom_manifest
    g = gpu_guard.probe_gpus()
    d = gpu_guard.discover_display(g, gpu_guard.load_policy(None))
    uuid, basis, detail = voidloom_manifest.discover_display_uuid(voidloom_manifest.probe_gpus())
    print(json.dumps({"cards": [x.index for x in g], "guard_uuid": d.uuid, "guard_basis": d.basis,
                      "guard_detail": d.detail, "indeterminate": d.indeterminate,
                      "manifest_uuid": uuid, "manifest_basis": basis, "manifest_detail": detail}))
''')

TWO = [(0, "GPU-00000000-monitor-card", 800), (1, "GPU-11111111-compute-card", 5)]
THREE = [(0, "GPU-00000000-monitor-card", 800), (1, "GPU-11111111-other-job", 5200), (2, "GPU-22222222-compute", 5)]


class TestTheDeclaredDisplayWins(unittest.TestCase):

    def setUp(self):
        for need in ("gpu_guard.py", "voidloom_device.py", "voidloom_manifest.py"):
            if not (ORGANS / need).is_file():
                raise unittest.SkipTest("tree lacks organs/%s" % need)
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_display_decl_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        (self.tmp / "probe.py").write_text(DRIVER, encoding="utf-8")

    def _run(self, cards, env_lines):
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        smi = bindir / "nvidia-smi"
        smi.write_text(_fake_smi(cards), encoding="utf-8")
        os.chmod(str(smi), 0o755)
        envf = self.tmp / "wizard-library-env"
        envf.write_text("".join(l + "\n" for l in env_lines), encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE", "WIZARD_DISPLAY_INDEX")}
        env.update({"PATH": "%s:/usr/bin:/bin" % bindir, "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
                    "WIZARD_ENV_FILE": str(envf), "WIZARD_CONFIG_PATH": str(self.tmp / "no-config.json")})
        p = subprocess.run([sys.executable, "-B", str(self.tmp / "probe.py"), str(ORGANS)], capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_two_cards_monitor_asleep_the_declaration_beats_the_complement(self):
        out = self._run(TWO, ["WIZARD_CUDA_DEVICE=0", "WIZARD_DISPLAY_INDEX=0"])
        self.assertEqual(out["cards"], [0, 1], out)
        self.assertEqual((out["guard_uuid"], out["guard_basis"]), ("GPU-00000000-monitor-card", "owner-declaration"),
                         out)
        self.assertIn("DECLARED", out["guard_detail"])
        self.assertIn("WIZARD_DISPLAY_INDEX", out["guard_detail"])
        self.assertIn("complement of compute index 0 would have said card 1", out["guard_detail"])
        self.assertEqual((out["manifest_uuid"], out["manifest_basis"]),
                         ("GPU-00000000-monitor-card", "owner-declaration"), out)

    def test_three_cards_monitor_asleep_the_declaration_beats_the_heuristic(self):
        out = self._run(THREE, ["WIZARD_CUDA_DEVICE=2", "WIZARD_DISPLAY_INDEX=0"])
        self.assertEqual((out["guard_uuid"], out["guard_basis"]), ("GPU-00000000-monitor-card", "owner-declaration"),
                         out)
        self.assertEqual(out["manifest_uuid"], "GPU-00000000-monitor-card", out)

    def test_nothing_declared_the_inference_runs_and_says_so(self):
        out = self._run(TWO, ["WIZARD_CUDA_DEVICE=1"])
        self.assertEqual((out["guard_uuid"], out["guard_basis"]),
                         ("GPU-00000000-monitor-card", "config-derived-complement"), out)
        self.assertIn("INFERRED (no WIZARD_DISPLAY_INDEX declared)", out["guard_detail"])
        self.assertIn("INFERRED (no WIZARD_DISPLAY_INDEX declared)", out["manifest_detail"])

    def test_a_declared_card_the_probe_lacks_refuses_and_never_guesses(self):
        out = self._run(TWO, ["WIZARD_CUDA_DEVICE=1", "WIZARD_DISPLAY_INDEX=5"])
        self.assertTrue(out["indeterminate"], out)
        self.assertIsNone(out["guard_uuid"], out)
        self.assertIsNone(out["manifest_uuid"], out)

    def test_an_unusable_declaration_refuses_rather_than_inferring_past_it(self):
        out = self._run(TWO, ["WIZARD_CUDA_DEVICE=1", "WIZARD_DISPLAY_INDEX=monitor"])
        self.assertTrue(out["indeterminate"], out)
        self.assertIn("cannot be used", out["guard_detail"])

    def test_none_means_no_card_drives_a_monitor(self):
        out = self._run(TWO, ["WIZARD_CUDA_DEVICE=1", "WIZARD_DISPLAY_INDEX=none"])
        self.assertEqual((out["guard_uuid"], out["guard_basis"], out["indeterminate"]),
                         (None, "owner-declaration", False), out)

    def test_one_gpu_is_unchanged(self):
        out = self._run(TWO[:1], ["WIZARD_DISPLAY_INDEX=0"])
        self.assertEqual(out["guard_basis"], "single-gpu-topology", out)


if __name__ == "__main__":
    unittest.main()

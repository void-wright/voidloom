"""R7 (reference-host round): the units can SEE the GPUs -- and never pick the display card.

Measured on the reference host: `organs/gpu_guard.py` printed "TOPOLOGY FAIL: no GPUs visible
-> cloud-claude-p" on every dream-daytime run (6 since the 09-20 upgrade, 10 before). The cards
were there; the probe could not FIND the tool: on WSL2 nvidia-smi lives in /usr/lib/wsl/lib,
which an interactive shell's PATH carries and a unit's does not (neither the hand-written PATH
line nor the curated one). install.sh had the same blindness under a clean PATH: no GPU seen,
so no CUDA pin reached the env block.

The repair has two halves, both bound here with a FAKE nvidia-smi (a shell script that prints
two cards) -- no real driver is touched:
  * the organs locate nvidia-smi through ONE locator, organs/voidloom_device.py nvidia_smi():
    PATH first, then NVIDIA_SMI_DIRS; gpu_guard and voidloom_manifest both use it. With the
    fake OFF PATH and only in the locator's directory list, gpu_guard.probe_gpus() and
    voidloom_manifest.probe_gpus() see two cards; with the fake nowhere, none;
  * install.sh finds nvidia-smi the same way (its NVIDIA_SMI_DIRS literal must EQUAL the
    organ's), and the env block's curated PATH carries nvidia-smi's directory the way it
    carries node's;
and one safety bind: once install.sh can see a two-card host from a clean shell, its device
heuristic ("the first card nvidia-smi calls display_active=Disabled") becomes reachable --
and with the monitor asleep BOTH cards read Disabled (measured 2026-09-19), so it would pick
card 0, the DISPLAY card. The owner's WIZARD_CUDA_DEVICE line in the env file (outside the
managed block) is now read before any heuristic: the block pins what the file declares.
At c6d7af2: no locator (the organ probes see nothing off PATH), the block's PATH lacks the
directory, and the block pins '0' -- red on each.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
SH = ROOT / "install.sh"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))

FAKE_SMI = textwrap.dedent("""\
    #!/bin/sh
    # test double: a two-card host whose monitor is asleep (both cards display_active=Disabled)
    case "$*" in
      *index,uuid,name*) printf '0, GPU-00000000-fake-card-zero, Fake 2080 Ti, 11264, 800, 1, 7.5\\n1, GPU-11111111-fake-card-one, Fake 2080 Ti, 11264, 5, 0, 7.5\\n' ;;
      *index,display_active*) printf '0, Disabled\\n1, Disabled\\n' ;;
      *-L*) printf 'GPU 0: Fake 2080 Ti (UUID: GPU-00000000-fake-card-zero)\\nGPU 1: Fake 2080 Ti (UUID: GPU-11111111-fake-card-one)\\n' ;;
      *nvlink*) exit 0 ;;
      *) exit 0 ;;
    esac
    """)

DRIVER = textwrap.dedent(r'''
    import json, os, sys
    sys.path.insert(0, sys.argv[1])
    import voidloom_device
    voidloom_device.NVIDIA_SMI_DIRS = tuple(p for p in sys.argv[2].split(":") if p)
    import gpu_guard, voidloom_manifest
    g = gpu_guard.probe_gpus()
    m = voidloom_manifest.probe_gpus()
    disc = gpu_guard.discover_display(g, {"compute_index": "1"}, _display_active={}) if g else None
    print(json.dumps({"locator": voidloom_device.nvidia_smi(), "guard": [x.index for x in g],
                      "manifest": [x["index"] for x in m],
                      "display_uuid": disc.uuid if disc else None}))
''')


def _install_sh_dirs():
    m = re.search(r'^NVIDIA_SMI_DIRS="([^"]*)"', SH.read_text(encoding="utf-8"), re.M)
    return tuple(m.group(1).split()) if m else None


class TestNvidiaSmiIsFoundOffPath(unittest.TestCase):
    def setUp(self):
        for need in (ORGANS / "gpu_guard.py", ORGANS / "voidloom_device.py", ORGANS / "voidloom_manifest.py"):
            if not need.is_file():
                raise unittest.SkipTest("tree lacks %s" % need)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="nvsmi_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.drv = self.tmp / "driver_dir"          # where a driver ships it: NOT on PATH
        self.drv.mkdir()
        (self.drv / "nvidia-smi").write_text(FAKE_SMI, encoding="utf-8")
        os.chmod(str(self.drv / "nvidia-smi"), 0o755)
        (self.tmp / "probe.py").write_text(DRIVER, encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        # nothing here initialises CUDA: the probes only ENUMERATE (a fake nvidia-smi), so no pin is passed at all
        for k in ("CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE"):
            self.env.pop(k, None)
        self.env.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
                         "WIZARD_ENV_FILE": str(self.tmp / "no-env-file")})

    def _probe(self, dirs):
        p = subprocess.run([sys.executable, "-B", str(self.tmp / "probe.py"), str(ORGANS), dirs],
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_the_organ_probes_see_the_cards_through_the_locator_when_nvidia_smi_is_off_path(self):
        if shutil.which("nvidia-smi", path=self.env["PATH"]):
            self.skipTest("a real nvidia-smi sits on the clean PATH /usr/local/bin:/usr/bin:/bin on this host")
        out = self._probe(str(self.drv))
        self.assertEqual(out["locator"], str(self.drv / "nvidia-smi"), out)
        self.assertEqual(out["guard"], [0, 1], "gpu_guard saw no cards off PATH -- 'no GPUs visible': %r" % out)
        self.assertEqual(out["manifest"], [0, 1], out)
        # the display card is the complement of the compute index -- card 0 -- and is never compute
        self.assertEqual(out["display_uuid"], "GPU-00000000-fake-card-zero", out)

    def test_nowhere_is_none_not_a_guess(self):
        if shutil.which("nvidia-smi", path=self.env["PATH"]):
            self.skipTest("a real nvidia-smi sits on the clean PATH on this host")
        out = self._probe(str(self.tmp / "empty_dir_that_does_not_exist"))
        self.assertIsNone(out["locator"], out)
        self.assertEqual((out["guard"], out["manifest"]), ([], []), out)

    def test_install_sh_and_the_organ_name_the_same_directories(self):
        if not SH.is_file():
            self.skipTest("NOT A CHECKOUT: no install.sh at %s (it ships, it is not laid)" % ROOT)
        code = "import sys; sys.path.insert(0, sys.argv[1]); import voidloom_device as d; print(' '.join(d.NVIDIA_SMI_DIRS))"
        p = subprocess.run([sys.executable, "-B", "-c", code, str(ORGANS)], capture_output=True, text=True,
                           env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        organ = tuple(p.stdout.split())
        self.assertTrue(organ, "voidloom_device declares no NVIDIA_SMI_DIRS")
        self.assertEqual(_install_sh_dirs(), organ, "install.sh and organs/voidloom_device.py disagree on where "
                                                   "nvidia-smi lives")


class TestTheEnvBlockCarriesNvidiaSmiAndTheDeclaredPin(unittest.TestCase):
    """install.sh --env-only into a scratch HOME (F4), the fake nvidia-smi discoverable the way node is."""

    def setUp(self):
        if not SH.is_file():
            raise unittest.SkipTest("NOT A CHECKOUT: no install.sh at %s (it ships, it is not laid)" % ROOT)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="nvblk_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.home = self.tmp / "home"
        (self.home / "tmp").mkdir(parents=True)
        self.drv = self.tmp / "fake_driver_bin"
        self.drv.mkdir()
        (self.drv / "nvidia-smi").write_text(FAKE_SMI, encoding="utf-8")
        os.chmod(str(self.drv / "nvidia-smi"), 0o755)
        self.env_file = self.home / ".voidloom" / "wizard-library-env"
        self.env_file.parent.mkdir()

    def _run(self, **extra):
        return subprocess.run(["bash", str(SH), "--env-only"], capture_output=True, text=True, env=self._env(**extra),
                              timeout=180, cwd=str(self.tmp))

    def _env(self, **extra):
        env = {"PATH": str(self.drv) + ":/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home),
               "WIZARD_CLAUDE_HOME": str(self.home / ".claude"), "WIZARD_ENV_FILE": str(self.env_file),
               "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
               "TMPDIR": str(self.home / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_EMBED_MODEL": "nomic"}
        env.update(extra)
        return env

    def _block(self):
        text = self.env_file.read_text(encoding="utf-8")
        return text[text.index("# >>> wizard-library managed >>>"):text.index("# <<< wizard-library managed <<<")]

    def test_the_block_path_carries_nvidia_smis_directory(self):
        # --force-cpu: no device is resolved or pinned here; the PATH line is the subject
        p = subprocess.run(["bash", str(SH), "--env-only", "--force-cpu"], capture_output=True, text=True,
                           env=self._env(), timeout=180, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        line = [l for l in self._block().splitlines() if l.startswith("PATH=")][0]
        self.assertIn(str(self.drv), line.split("=", 1)[1].strip('"').split(":"), line)

    def test_an_owner_declared_device_outside_the_block_beats_the_display_heuristic(self):
        # the owner's durable declaration, OUTSIDE the managed block; nothing exported
        self.env_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=not-a-real-token\nWIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
        os.chmod(str(self.env_file), 0o600)
        p = self._run()                                     # no WIZARD_CUDA_DEVICE, no CUDA_VISIBLE_DEVICES
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        pins = [l for l in self._block().splitlines() if l.startswith("CUDA_VISIBLE_DEVICES=")]
        self.assertEqual(pins, [],
                         "both fake cards read display_active=Disabled; the heuristic alone picks card 0 -- and the "
                         "owner declares WIZARD_CUDA_DEVICE outside the block, which a block CUDA_VISIBLE_DEVICES would "
                         "OUTRANK in every unit's resolver: the block writes none: %r" % pins)
        self.assertIn("# CUDA_VISIBLE_DEVICES is not written: WIZARD_CUDA_DEVICE is assigned OUTSIDE this block", self._block())
        self.assertNotIn("CUDA_VISIBLE_DEVICES='0'", self.env_file.read_text(encoding="utf-8"))
        text = self.env_file.read_text(encoding="utf-8")
        outside = text.replace(self._block(), "")
        self.assertIn("WIZARD_CUDA_DEVICE=1\n", outside, "the owner's line outside the block did not survive")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=not-a-real-token\n", outside)


if __name__ == "__main__":
    unittest.main()

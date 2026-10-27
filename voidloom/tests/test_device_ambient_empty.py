"""An EMPTY ambient CUDA_VISIBLE_DEVICES is a deliberate "no devices", never the default.

Minimal pair for organs/voidloom_device.py (2026-09-20). Before the fix, an empty
ambient read as "unset" and fell through to the file or to PACKAGE_SAFE_DEFAULT
("0"): on a box with no declaring file the operator said "none" and got card 0 --
the monitor card on the reference host (#20154). CUDA itself reads an empty
value as "no devices visible", so the resolver now honours it exactly as a file's
declared-empty: (None, "ambient-empty"), and apply_cuda_pin() leaves the variable
as found.

Each case runs the resolver in a SUBPROCESS with a scratch HOME and every
declaring surface pointed at absent files, so the pair isolates the ambient
layer from the host's own env file. No torch is imported; nothing pins anything.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGAN = ROOT / "organs" / "voidloom_device.py"

PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import voidloom_device as d
value, basis = d.resolve_cuda_device()
before = os.environ.get("CUDA_VISIBLE_DEVICES")
d.apply_cuda_pin()
after = os.environ.get("CUDA_VISIBLE_DEVICES")
print(json.dumps({"value": value, "basis": basis, "before": before, "after": after}))
"""


class TestAmbientEmpty(unittest.TestCase):
    def _resolve(self, ambient):
        with tempfile.TemporaryDirectory(prefix="ambient_", dir=os.environ.get("VOIDLOOM_SCRATCH") or None) as home:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": home,
                   "WIZARD_ENV_FILE": str(Path(home) / "absent-env"),
                   "WIZARD_CONFIG_PATH": str(Path(home) / "absent-config.json"),
                   "PYTHONDONTWRITEBYTECODE": "1"}
            if ambient is not None:
                env["CUDA_VISIBLE_DEVICES"] = ambient
            p = subprocess.run([sys.executable, "-B", "-c", PROBE, str(ORGAN.parent)],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            return json.loads(p.stdout.strip().splitlines()[-1])

    def test_empty_ambient_is_no_assertion_and_stays_empty(self):
        r = self._resolve("")
        self.assertIsNone(r["value"], r)
        self.assertEqual(r["basis"], "ambient-empty", r)
        self.assertEqual(r["before"], "", r)
        self.assertEqual(r["after"], "", "apply_cuda_pin must leave a deliberate empty ambient as found: %r" % r)
        self.assertNotEqual(r["after"], "0", "the fix's whole point: an empty ambient never becomes card 0")

    def test_unset_ambient_with_no_declaring_file_is_the_default_with_the_default_basis(self):
        # the pair partner: UNSET (not empty) still falls through to the package default
        r = self._resolve(None)
        self.assertEqual(r["value"], "0", r)
        self.assertEqual(r["basis"], "default", r)

    def test_nonempty_ambient_wins_verbatim(self):
        r = self._resolve("1")
        self.assertEqual((r["value"], r["basis"]), ("1", "ambient"), r)
        self.assertEqual(r["after"], "1", r)


MANIFEST_WRITER = ROOT / "organs" / "voidloom_manifest.py"
SCRATCH_O2 = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))


class TestTheManifestWriterSurvivesANoDeviceAmbient(unittest.TestCase):
    """Carried item O2 (env-file writer pass, iteration 4). `organs/voidloom_manifest.py write` -- install.sh Step A.1 --
    died with rc 139 (SIGSEGV) under CUDA_VISIBLE_DEVICES="", a CPU-only user's normal setting: model_garden.doctor ->
    torch.cuda.is_available() -> cuInit(0), which the WSL2 driver's libcuda.so.1 SEGFAULTS on whenever no device is
    visible (measured with ctypes and no torch: '', ' ', '-1', 'NoDevFiles', an absent index). The writer now records an
    empty ambient as "no GPU visible" without initialising CUDA, and probes any other value in a CHILD process whose
    signal death is recorded as "no CUDA". Each case runs the writer as its own process in a scratch home; no case can
    see a GPU (every value selects none), and nothing pins anything."""

    def _write(self, ambient):
        SCRATCH_O2.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="o2_manifest_", dir=str(SCRATCH_O2)) as home:
            h = Path(home)
            (h / "tmp").mkdir()
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "TMPDIR": str(h / "tmp"),
                   "WIZARD_CLAUDE_HOME": str(h / ".claude"), "WIZARD_ENV_FILE": str(h / "absent-env"),
                   "WIZARD_CONFIG_PATH": str(h / "absent-config.json"), "XDG_CONFIG_HOME": str(h / ".config"),
                   "XDG_CACHE_HOME": str(h / ".cache"), "XDG_DATA_HOME": str(h / ".local" / "share"),
                   "XDG_STATE_HOME": str(h / ".local" / "state"), "PYTHONDONTWRITEBYTECODE": "1",
                   "CUDA_VISIBLE_DEVICES": ambient}
            out = h / "voidloom_manifest.json"
            p = subprocess.run([sys.executable, "-B", str(MANIFEST_WRITER), "write", str(out)],
                               capture_output=True, text=True, env=env, timeout=600, cwd=home)
            self.assertEqual(p.returncode, 0, "the manifest writer died (rc %d; -11/139 is the driver's SIGSEGV): %s"
                             % (p.returncode, (p.stdout + p.stderr)[-800:]))
            self.assertTrue(out.is_file(), "rc 0 but no manifest was written")
            return json.loads(out.read_text(encoding="utf-8"))["probed"]["torch_cuda"]

    def test_an_empty_ambient_writes_the_manifest_and_records_no_gpu_visible(self):
        tc = self._write("")
        self.assertIs(tc["cuda_available"], False, tc)
        self.assertEqual(tc["device_count"], 0, tc)
        if tc.get("torch_present"):
            self.assertIn("CUDA_VISIBLE_DEVICES is empty", tc.get("cuda_probe", ""),
                          "an empty ambient must be recorded as CUDA's own 'no device', without initialising CUDA: %r" % tc)

    def test_a_value_that_selects_no_device_writes_the_manifest_and_records_no_cuda(self):
        # 'NoDevFiles' selects no device anywhere; on the WSL2 driver the probe's cuInit dies with SIGSEGV, which must be
        # the CHILD's death, recorded -- never this writer's
        tc = self._write("NoDevFiles")
        self.assertIs(tc["cuda_available"], False, tc)
        self.assertEqual(tc["device_count"], 0, tc)


if __name__ == "__main__":
    unittest.main()

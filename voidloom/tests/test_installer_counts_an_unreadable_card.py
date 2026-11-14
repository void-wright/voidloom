"""The installer counts a card the driver cannot read, so a two-card host is never installed onto its display card.

The host shape: two NVIDIA cards, the compute card has dropped out of the driver. `nvidia-smi -L` lists ONE card --
the display card -- while `nvidia-smi --query-gpu=count` still counts two. The installer used to count with `-L`,
take the "one GPU" branch ("the only GPU"), pin card 0 into the managed block, and its ambient proof P4 passed the
display card under the single-card exemption. Now the run is REFUSED (rc 2) before anything is touched, naming the
unreadable card; P4 refuses the same shape. A genuine one-card machine (count 1, listed 1) installs as before.

Bound with fake nvidia-smi scripts under fixtures/ (no real driver is asked, nothing initialises CUDA) and a scratch
HOME per test.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from voidloom.tests.test_tier1_ambient import _AmbientHome, ROOT, SH

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DROPPED = FIXTURES / "fakesmi_card_dropped"
ONE_CARD = FIXTURES / "fakesmi_one_card"


class _Run(_AmbientHome):

    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("bash installer test")
        for d in (DROPPED, ONE_CARD):
            if not os.access(str(d / "nvidia-smi"), os.X_OK):
                self.skipTest("fixture %s is not executable here" % d.name)

    def env_only(self, smi_dir, env_lines=None):
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        if env_lines is not None:
            self.env_file.write_text("".join(l + "\n" for l in env_lines), encoding="utf-8")
        before = self.env_file.read_bytes() if self.env_file.exists() else None
        env = self._env(PATH="%s:/usr/local/bin:/usr/bin:/bin" % smi_dir, WIZARD_EMBED_MODEL="nomic")
        for k in ("CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE"):
            env.pop(k, None)
        p = subprocess.run(["bash", str(SH), "--env-only"], capture_output=True, text=True, timeout=300,
                           cwd=str(self.tmp), env=env, stdin=subprocess.DEVNULL)
        after = self.env_file.read_bytes() if self.env_file.exists() else None
        return p, before, after


class TheInstallerCountsAnUnreadableCard(_Run):

    def test_nothing_declared_is_refused_and_nothing_is_touched(self):
        p, before, after = self.env_only(DROPPED)
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1200:]))
        self.assertIn("REFUSED", p.stderr)
        self.assertIn("counts 2 GPUs but can read only 1", p.stderr)
        # Independent of the message: the env file the units read is exactly as it was (here: never created).
        self.assertEqual(after, before)
        self.assertNotIn("the only GPU", p.stdout + p.stderr)

    def test_a_declared_display_card_is_refused_too(self):
        p, before, after = self.env_only(DROPPED, ["WIZARD_DISPLAY_INDEX=0"])
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1200:]))
        self.assertIn("counts 2 GPUs but can read only 1", p.stderr)
        self.assertEqual(after, before)

    def test_a_genuine_one_card_machine_installs_as_before(self):
        p, before, after = self.env_only(ONE_CARD)
        self.assertEqual(p.returncode, 0, (p.stdout[-800:], p.stderr[-1200:]))
        self.assertNotIn("can read only", p.stderr)
        self.assertIsNotNone(after)


class TheAmbientProofRefusesAnUnreadableCard(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, str(ROOT / "install"))
        self.addCleanup(sys.path.remove, str(ROOT / "install"))
        import tier1_probe
        self.t = tier1_probe

    def test_the_only_listed_card_is_the_declared_display(self):
        with self.assertRaises(RuntimeError) as cm:
            self.t.display_verdict("0", {"0": "Enabled"}, "0", "", "the only GPU")
        self.assertIn("display", str(cm.exception))

    def test_the_driver_counts_more_cards_than_it_lists(self):
        with self.assertRaises(RuntimeError) as cm:
            self.t.display_verdict("0", {"0": "Enabled"}, "", "", "the only GPU", counted=2)
        self.assertIn("counts 2", str(cm.exception))

    def test_a_genuine_one_card_machine_keeps_the_single_card_basis(self):
        v = self.t.display_verdict("0", {"0": "Enabled"}, "", "", "the only GPU", counted=1)
        self.assertEqual(v["display_basis"], "single-card")
        v = self.t.display_verdict("0", {"0": "Enabled"}, "", "", "the only GPU")
        self.assertEqual(v["display_basis"], "single-card")


if __name__ == "__main__":
    unittest.main()

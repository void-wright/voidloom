"""A brain the installer creates is owner-only (mode 600), whatever the umask.

THE CLAIM UNDER TEST
  The brain file holds every memory the user stores. install.sh creates it under
  `umask 077` and then sets 600 on it and on its -wal/-shm siblings, so there is no
  umask under which a fresh brain is readable by other users. A brain that was
  already in place keeps the mode its owner gave it; a looser one is reported.

  control   a fresh Tier-0 install under the default umask 022: mode 600.
  saboteur  the same under umask 000 (the widest a shell can ask for): still 600.
  saboteur  an existing brain at 644: left at 644, and the installer says so.

The installs are Tier 0 (stdlib; no model, no network).
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import unittest

from voidloom.tests.test_tier1_ambient import _AmbientHome, SH  # noqa: E402

ARGS = ["--tier0-only", "--force-cpu", "--thinker", "off", "--no-wire-hooks"]


class BrainFileMode(_AmbientHome):
    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("POSIX file modes, measured on Linux")

    def _run(self, umask: str, *extra):
        cmd = "umask %s; exec bash %s %s" % (umask, str(SH), " ".join(list(extra) + ARGS))
        return subprocess.run(["bash", "-c", cmd], env=self._env(), cwd=str(self.tmp),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=900, start_new_session=True)

    def _mode(self) -> int:
        return stat.S_IMODE(os.stat(self.db).st_mode)

    def test_control_a_fresh_brain_is_600_under_the_default_umask(self):
        r = self._run("022", "--init-blank")
        self.assertEqual(r.returncode, 0, (r.stdout[-1500:], r.stderr[-1500:]))
        self.assertEqual(oct(self._mode()), oct(0o600))
        self.assertIn("brain file mode: 600", r.stdout)

    def test_saboteur_a_fresh_brain_is_600_under_umask_000(self):
        r = self._run("000", "--init-blank")
        self.assertEqual(r.returncode, 0, (r.stdout[-1500:], r.stderr[-1500:]))
        self.assertEqual(oct(self._mode()), oct(0o600))
        for side in ("-wal", "-shm"):
            p = str(self.db) + side
            if os.path.exists(p):
                self.assertEqual(oct(stat.S_IMODE(os.stat(p).st_mode)), oct(0o600), p)

    def test_saboteur_an_existing_brain_keeps_its_mode_and_is_reported(self):
        self._brain()
        os.chmod(self.db, 0o644)
        r = self._run("022")
        self.assertEqual(r.returncode, 0, (r.stdout[-1500:], r.stderr[-1500:]))
        self.assertEqual(oct(self._mode()), oct(0o644))
        self.assertIn("is mode 644, not 600", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

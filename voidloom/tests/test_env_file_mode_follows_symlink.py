"""R2 (reference-host round): the env-file mode check reads the TARGET, not the link.

Measured 2026-09-20 on the reference host: its env file is a SYMLINK into another
directory, and `install.sh` read the mode back with `stat -c '%a'` -- GNU stat without
-L reports the link's own mode, 777 -- and warned "mode 777, not 600 -- the chmod did
not hold" while the file every unit reads was 600. chmod follows a link; the read-back
now does too (-L on the GNU and the BSD branch), and the warning names the file whose
mode it read.

Executed: a WET `install.sh --env-only` into a scratch HOME (F4: never the real one),
the env file a symlink to a target in another directory of that HOME.
  * target 600          -> no warning, and the verified line names the target;
  * target 644 (a `chmod` that does not hold, made by a no-op chmod first on PATH --
    the filesystem shape the warning exists for) -> the warning names the TARGET path.
At c6d7af2 the first case warns "mode 777" (red) and the second names the link (red).
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SH = ROOT / "install.sh"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))


def _require_checkout():
    if not SH.is_file():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


class TestEnvFileModeFollowsTheSymlink(unittest.TestCase):
    def setUp(self):
        _require_checkout()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="envmode_", dir=str(SCRATCH)))
        self.home = self.tmp / "home"
        (self.home / "tmp").mkdir(parents=True)
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        # the reference geometry: the product's path is a LINK into another directory
        self.target = self.home / "elsewhere" / "wizard-library-env"
        self.target.parent.mkdir()
        self.target.write_text("CLAUDE_CODE_OAUTH_TOKEN=not-a-real-token\n", encoding="utf-8")
        self.link = self.home / ".voidloom" / "wizard-library-env"
        self.link.parent.mkdir()
        os.symlink(str(self.target), str(self.link))

    def _run(self, path_prefix=""):
        env = {"PATH": path_prefix + "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home),
               "WIZARD_CLAUDE_HOME": str(self.home / ".claude"), "WIZARD_ENV_FILE": str(self.link),
               "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
               "TMPDIR": str(self.home / "tmp"), "PYTHONDONTWRITEBYTECODE": "1",
               "WIZARD_EMBED_MODEL": "nomic", "WIZARD_CUDA_DEVICE": "1"}
        return subprocess.run(["bash", str(SH), "--env-only", "--force-cpu"], capture_output=True, text=True,
                              env=env, timeout=180, cwd=str(self.tmp))

    def test_a_600_target_behind_a_link_is_verified_not_warned(self):
        os.chmod(str(self.target), 0o600)
        p = self._run()
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertTrue(self.link.is_symlink(), "the installer replaced the link")
        self.assertEqual(stat.S_IMODE(os.stat(str(self.target)).st_mode), 0o600)
        self.assertNotIn("not 600", p.stderr, "a 600 target was reported as not 600 (the link's mode was read)")
        self.assertIn("mode verified 600", p.stdout, p.stdout[-800:])
        self.assertIn(str(self.target), [l for l in p.stdout.splitlines() if "mode verified 600" in l][0],
                      "the verified line must name the file whose mode was read")
        # the token line outside the block survived, in the TARGET
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=not-a-real-token", self.target.read_text(encoding="utf-8"))

    def test_a_644_target_is_warned_naming_the_target(self):
        shim = self.tmp / "shim"
        shim.mkdir()
        (shim / "chmod").write_text("#!/bin/sh\n# a filesystem that accepts chmod and ignores it\nexit 0\n",
                                    encoding="utf-8")
        os.chmod(str(shim / "chmod"), 0o755)
        os.chmod(str(self.target), 0o644)
        p = self._run(path_prefix=str(shim) + ":")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertEqual(stat.S_IMODE(os.stat(str(self.target)).st_mode), 0o644, "the no-op chmod shim was not used")
        warns = [l for l in p.stderr.splitlines() if "not 600" in l]
        self.assertEqual(len(warns), 1, p.stderr[-1500:])
        self.assertIn("mode 644", warns[0], warns[0])
        self.assertIn(str(self.target), warns[0], "the warning must name the TARGET whose mode it read")
        self.assertNotIn("the env file at %s " % self.link, warns[0], "the warning named the link")


if __name__ == "__main__":
    unittest.main()

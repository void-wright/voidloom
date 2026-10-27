"""The env file's PATH= line is curated, never the invoking shell's PATH.

Until 2026-09-20 the managed block wrote `PATH="<local_bin>:<pydir>[:<nodedir>]:${PATH}"`
-- the operator's ENTIRE install-time PATH, expanded and frozen into the file every
unit sources. Measured on the reference host: a re-run from a WSL session would have
written 3,107 bytes with 62 /mnt/c/... entries over a 109-byte line (round-1
iteration-3 reflection, item 10). The line is now LOCAL_BIN:pydir[:nodedir]:the six
system dirs, plus WIZARD_ENV_EXTRA_PATH (absolute dirs only) when a host needs more.

Executed: a WET `install.sh --env-only` into a scratch HOME (F4: never the real one)
under a poisoned PATH; the written line must carry none of the poison. Red at parent.
"""
import os
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


class TestEnvFilePathIsCurated(unittest.TestCase):
    def setUp(self):
        _require_checkout()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="envpath_", dir=str(SCRATCH)))
        self.home = self.tmp / "home"; (self.home / "tmp").mkdir(parents=True)
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))

    def _env(self, path, **extra):
        env = {"PATH": path, "HOME": str(self.home), "WIZARD_CLAUDE_HOME": str(self.home / ".claude"),
               "WIZARD_ENV_FILE": str(self.home / ".voidloom" / "wizard-library-env"),
               "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
               "TMPDIR": str(self.home / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_CUDA_DEVICE": ""}
        env.update(extra)
        return env

    def _run(self, path, **extra):
        # --force-cpu: this test's subject is the PATH line, not the GPU. On a host with two or more GPUs and nothing
        # declared, a run with no terminal is REFUSED until the owner answers which card (G1, env-file writer pass
        # iteration 5) -- so, as test_attach_db does, no card is resolved here; nvidia-smi is still located, and its
        # directory still reaches the curated PATH exactly as before.
        p = subprocess.run(["bash", str(SH), "--env-only", "--force-cpu"], capture_output=True, text=True,
                           env=self._env(path, **extra), timeout=180, cwd=str(self.tmp))
        return p

    def test_poisoned_shell_path_never_reaches_the_env_file(self):
        poison = ["/mnt/c/Windows/system32", "/mnt/c/Profiles/someone/AppData/Local/Programs", "/opt/some-shim"]
        path = ":".join(poison + ["/usr/local/bin", "/usr/bin", "/bin"])
        p = self._run(path)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        env_file = self.home / ".voidloom" / "wizard-library-env"
        lines = [l for l in env_file.read_text(encoding="utf-8").splitlines() if l.startswith("PATH=")]
        self.assertEqual(len(lines), 1, lines)
        value = lines[0].split("=", 1)[1].strip().strip('"\'')
        for bad in poison:
            self.assertNotIn(bad, value, "the invoking shell's PATH leaked into the env file: %s" % value)
        parts = value.split(":")
        self.assertEqual(parts[0], str(self.home / ".local" / "bin"), parts)
        self.assertTrue(set(["/usr/local/bin", "/usr/bin", "/bin"]).issubset(parts), parts)
        self.assertLess(len(value), 400, "a curated line is short: %d bytes" % len(value))

    def test_extra_path_is_appended_only_when_absolute(self):
        p = self._run("/usr/local/bin:/usr/bin:/bin", WIZARD_ENV_EXTRA_PATH="/opt/tools/bin:/srv/bin")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        value = [l for l in (self.home / ".voidloom" / "wizard-library-env").read_text().splitlines() if l.startswith("PATH=")][0]
        self.assertTrue(value.rstrip('"').endswith(":/opt/tools/bin:/srv/bin"), value)
        p = self._run("/usr/local/bin:/usr/bin:/bin", WIZARD_ENV_EXTRA_PATH="rel/bin")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-800:])
        self.assertIn("WIZARD_ENV_EXTRA_PATH", p.stderr)


if __name__ == "__main__":
    unittest.main()

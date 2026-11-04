"""Sidecar discovery (organs/voidloom_env.py): explicit env dir > the env file's directory > ~/.voidloom; the
hidden-$HOME scan only behind VOIDLOOM_LEGACY_DISCOVERY=1, and only into a directory marked as THIS install's.

Two installs can share one user account and one $HOME. Before this, a lookup scanned every hidden directory under
$HOME and took the first that held a file of the asked name, so one install could read another's credential.
  * control: VOIDLOOM_ENV_DIR wins; WIZARD_ENV_FILE is next (the file itself for the env file, its directory for the
    rest); ~/.voidloom is next; nothing found reads "default", exists False;
  * saboteur: a foreign hidden dir holding the sidecar is never found without the opt-in, and no file in it is even
    looked up (the scan does not run);
  * saboteur: with the opt-in, an UNMARKED or FOREIGN-MARKED dir is refused and counted, its sidecar never read;
  * control: with the opt-in, a dir marked for this install is found;
  * saboteur: a marker that is a symlink, malformed, or names another root is not ownership;
  * control: a relative WIZARD_ENV_FILE is ignored and the basis says so;
  * saboteur: a NAMED directory (VOIDLOOM_ENV_DIR, the env file's, ~/.voidloom) carrying another install's marker
    refuses ENV_DIR_FOREIGN on every limb, never falls through, and reads nothing there; unmarked stays trusted.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))
import voidloom_env as ve  # noqa: E402

# literals, not the module's constants: a module that lacks them must fail the tests, not their collection
ENV_DIR, ENV_FILE, OPT_IN = "VOIDLOOM_ENV_DIR", "WIZARD_ENV_FILE", "VOIDLOOM_LEGACY_DISCOVERY"
MARKER, SCHEMA = ".voidloom-owner", "voidloom.env-owner/1"
KEYS = (ENV_DIR, ENV_FILE, OPT_IN, "VOIDLOOM_INSTALL_ROOT")


class SidecarResolution(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_envres_")
        self.addCleanup(self.td.cleanup)
        self.home = Path(self.td.name) / "home"
        self.home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in KEYS}
        env["HOME"] = str(self.home)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self, name, files=("claude-oauth-token",)):
        d = self.home / name
        d.mkdir()
        for f in files:
            (d / f).write_text("secret-" + name + "\n", encoding="utf-8")
        return d

    def test_control_the_named_limbs_in_order(self):
        own = self._dir(".voidloom")
        envd = self._dir("explicit")
        r = ve.resolve_verbose("claude-oauth-token")
        self.assertEqual((r.path, r.basis, r.exists), (own / "claude-oauth-token", "preferred", True))
        envf_dir = self._dir("envfile-dir", files=("wizard-library-env", "claude-oauth-token"))
        os.environ[ENV_FILE] = str(envf_dir / "custom-name-env")
        r = ve.resolve_verbose("claude-oauth-token")
        self.assertEqual((r.path, r.basis), (envf_dir / "claude-oauth-token", "env-file"))
        r = ve.resolve_verbose("wizard-library-env")      # the env file is the named file itself, not a sibling
        self.assertEqual((r.path, r.basis, r.exists), (envf_dir / "custom-name-env", "env-file", False))
        os.environ[ENV_DIR] = str(envd)
        r = ve.resolve_verbose("claude-oauth-token")
        self.assertEqual((r.path, r.basis), (envd / "claude-oauth-token", "env"))

    def test_control_nothing_anywhere_reads_default(self):
        r = ve.resolve_verbose("wizard-config.json")
        self.assertEqual((r.path, r.basis, r.exists), (self.home / ".voidloom" / "wizard-config.json", "default", False))

    def test_saboteur_a_foreign_hidden_dir_is_never_scanned_without_the_opt_in(self):
        self._dir(".another-install")
        with mock.patch.object(ve, "candidate_dirs", side_effect=AssertionError("scanned $HOME")):
            r = ve.resolve_verbose("claude-oauth-token")
        self.assertEqual((r.basis, r.exists), ("default", False))
        self.assertNotIn("another-install", str(r.path))

    def test_saboteur_with_the_opt_in_unowned_dirs_are_refused_and_never_read(self):
        os.environ[OPT_IN] = "1"
        self._dir(".another-install")
        foreign = self._dir(".foreign-marked")
        (foreign / MARKER).write_text(json.dumps(
            {"schema": SCHEMA, "install_root": str(Path(self.td.name) / "elsewhere")}), encoding="utf-8")
        looked = []
        real_is_file = Path.is_file

        def spy(p):
            if p.name == "claude-oauth-token":
                looked.append(str(p))
            return real_is_file(p)
        with mock.patch.object(Path, "is_file", spy):
            r = ve.resolve_verbose("claude-oauth-token")
        self.assertEqual(r.exists, False)
        self.assertTrue(r.basis.startswith("default"), r.basis)
        self.assertIn("+refused-unowned:2", r.basis)
        self.assertEqual([x for x in looked if ".voidloom" not in x], [])

    def test_control_with_the_opt_in_a_dir_marked_for_this_install_is_found(self):
        os.environ[OPT_IN] = "1"
        self._dir(".a-unmarked")
        legacy = self._dir(".legacy-name")
        ve.write_owner_marker(legacy)
        r = ve.resolve_verbose("claude-oauth-token")
        self.assertEqual((r.path, r.exists), (legacy / "claude-oauth-token", True))
        self.assertTrue(r.basis.startswith("discovered:.legacy-name"), r.basis)

    def test_saboteur_an_opt_in_other_than_exactly_1_does_not_scan(self):
        for v in ("true", "yes", "0", " 1", ""):
            os.environ[OPT_IN] = v
            with mock.patch.object(ve, "candidate_dirs", side_effect=AssertionError("scanned for %r" % v)):
                self.assertEqual(ve.resolve_verbose("claude-oauth-token").basis, "default")

    def test_saboteur_a_symlinked_malformed_or_foreign_marker_is_not_ownership(self):
        good = self._dir(".good")
        ve.write_owner_marker(good)
        self.assertTrue(ve.owner_marker_ok(good))
        link = self._dir(".linked")
        os.symlink(str(good / MARKER), str(link / MARKER))
        self.assertFalse(ve.owner_marker_ok(link))
        bad = self._dir(".bad")
        for body in ("not json", json.dumps({"schema": "other/1", "install_root": str(ve.install_root())}),
                     json.dumps({"schema": SCHEMA, "install_root": "relative/root"}),
                     json.dumps({"schema": SCHEMA, "install_root": str(self.home)})):
            (bad / MARKER).write_text(body, encoding="utf-8")
            self.assertFalse(ve.owner_marker_ok(bad), body)

    def test_control_a_relative_env_file_is_ignored_and_said(self):
        os.environ[ENV_FILE] = "relative/wizard-library-env"
        r = ve.resolve_verbose("wizard-library-env")
        self.assertEqual(r.basis, "default+env-file-relative-ignored")

    def test_saboteur_a_named_dir_marked_for_another_install_refuses_on_every_limb(self):
        foreign = json.dumps({"schema": SCHEMA, "install_root": str(Path(self.td.name) / "other-root")})
        own = self._dir(".voidloom")
        (own / MARKER).write_text(foreign, encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            ve.resolve_verbose("claude-oauth-token")
        self.assertIn("ENV_DIR_FOREIGN", str(cm.exception))
        self.assertNotIn("secret-", str(cm.exception))
        envd = self._dir("explicit")
        (envd / MARKER).write_text(foreign, encoding="utf-8")
        os.environ[ENV_DIR] = str(envd)
        with self.assertRaises(ValueError):
            ve.resolve_verbose("claude-oauth-token")
        del os.environ[ENV_DIR]
        envf_dir = self._dir("envfile-dir", files=("wizard-library-env",))
        (envf_dir / MARKER).write_text(foreign, encoding="utf-8")
        os.environ[ENV_FILE] = str(envf_dir / "wizard-library-env")
        with self.assertRaises(ValueError):
            ve.resolve_verbose("wizard-library-env")
        ve.write_owner_marker(envf_dir)                      # control: this install's own marker is accepted
        self.assertEqual(ve.resolve_verbose("wizard-library-env").basis, "env-file")


if __name__ == "__main__":
    unittest.main()

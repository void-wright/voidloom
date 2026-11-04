"""Every cron helper writes where the installer and the readers look: under XDG_CONFIG_HOME when it is set."""
from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "cron").glob("setup-*.sh")) + [ROOT / "cron" / "platform-detection.sh"]
LINE = re.compile(r'^SYSTEMD_USER_DIR=.*$', re.M)


class UnitDirectory(unittest.TestCase):
    def resolve(self, script, **env):
        line = LINE.search(script.read_text(encoding="utf-8")).group(0)
        clean = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        clean.update(HOME="/h", **env)
        return subprocess.run(["bash", "-c", f'{line}; printf %s "$SYSTEMD_USER_DIR"'], capture_output=True, text=True,
                              env=clean, check=True).stdout

    def test_saboteur_every_script_follows_the_variable(self):
        self.assertEqual(len(SCRIPTS), 10)
        for s in SCRIPTS:
            self.assertEqual(self.resolve(s, XDG_CONFIG_HOME="/x"), "/x/systemd/user", s.name)

    def test_control_unset_or_empty_is_the_default(self):
        for s in SCRIPTS:
            self.assertEqual(self.resolve(s), "/h/.config/systemd/user", s.name)
            self.assertEqual(self.resolve(s, XDG_CONFIG_HOME=""), "/h/.config/systemd/user", s.name)

    def test_saboteur_the_audit_tool_reads_the_same_directory(self):
        text = (ROOT / "tools" / "gen_cron_audit.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("XDG_CONFIG_HOME")', text)
        self.assertNotIn('HOME/".config/systemd/user"', text)


if __name__ == "__main__":
    unittest.main()

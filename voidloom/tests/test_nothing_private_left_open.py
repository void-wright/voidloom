"""What holds a brain or vouches for one is the owner's alone, whatever the umask, and a pre-image is as private as the brain."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
sys.path.insert(0, str(ROOT / "install"))


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


class UnderAnOpenUmask(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.old = os.umask(0)
        self.addCleanup(os.umask, self.old)

    def test_the_lock_history(self):
        import embedder_lock as el
        el.append_receipt(self.d, {"action": "init"})
        self.assertEqual(mode(self.d / el.LOCK_HISTORY_NAME), 0o600)

    def test_the_owner_marker_and_its_directory(self):
        import voidloom_env as ve
        d = self.d / "side"
        self.assertTrue(ve.claim_env_dir(d, self.d))
        self.assertEqual(mode(d), 0o700)
        self.assertEqual(mode(d / ve.OWNER_MARKER), 0o600)

    def test_the_reranker_gate_record_and_its_backup(self):
        import accelerator_plan as ap
        cfg = self.d / "wizard-config.json"
        cfg.write_text("{}")
        rc, _msg = ap.write_gate(str(cfg), {"reranker": {"enabled": False}, "backend": "cpu", "ram_gib": 8})
        self.assertEqual(rc, 0, _msg)
        self.assertEqual(mode(cfg), 0o600)
        self.assertEqual(mode(Path(str(cfg) + ".bak")), 0o600)


class ThePreImage(unittest.TestCase):
    def test_it_is_no_wider_than_the_brain(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = text.index("write_verified_preimage() {")
        func = text[i:text.index("\nif [ \"$DRY_RUN\" -eq 1 ]; then", i)]
        with tempfile.TemporaryDirectory() as td:
            d = Path(td).resolve()
            src, dst = d / "brain.db", d / "brain.db.pre.db"
            c = sqlite3.connect(src)
            c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")
            c.commit(); c.close()
            os.chmod(src, 0o600)
            p = subprocess.run(["bash", "-c", f'umask 000; PY_SYS="{sys.executable}"\n{func}\nwrite_verified_preimage "{src}" "{dst}"'],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr[-400:])
            self.assertEqual(mode(dst), 0o600)


if __name__ == "__main__":
    unittest.main()

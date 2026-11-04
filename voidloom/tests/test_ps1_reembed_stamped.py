"""install/install.ps1's post-init embed writes every vector WITH its provenance stamp.

PowerShell is not run here. The Python the installer hands its venv (the `$reembedPy` here-string) is extracted from
install.ps1 byte for byte and run against a brain made the way an installer makes one (the package laid into the brain
directory, `init` under the cpu tier, which writes the install lock). The model is not loaded: the encoder is replaced
by a deterministic one and the shipped cpu checksum is registered as the loaded model's, exactly as the one loader
registers it.
  control   every memory gets a vector and a stamp naming the lock's checksum, in the lock's table;
  saboteur  the here-string writes no vector without going through memory_write (no raw INSERT into a vector table).
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
PS1 = ROOT / "install" / "install.ps1"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "organs"))

try:
    import sqlite_vec  # noqa: F401
    HAVE_VEC = True
except Exception:
    HAVE_VEC = False

WRAPPER = r'''
import struct, sys
brain = sys.argv[2]
sys.path.insert(0, brain + "/organs")
import embedder_lock, memory_write
cpu = embedder_lock.catalogue_tiers()["cpu"]
embedder_lock.register_loaded(model_id=cpu["model_id"], revision=cpu["revision"], weights_hash=cpu["weights_hash"],
                              dim=768, vec_table="memory_embeddings", tier="cpu")
memory_write.default_encoder = lambda text, conn=None: struct.pack("768f", *([0.5] * 768))
sys.argv = [sys.argv[0], sys.argv[1], sys.argv[2]]
exec(compile(open(sys.argv[0].replace("wrap.py", "reembed.py"), encoding="utf-8").read(), "reembed.py", "exec"))
'''


def reembed_python() -> str:
    text = PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("$reembedPy = @'\n") + len("$reembedPy = @'\n")
    end = text.index("\n'@", start)
    return text[start:end] + "\n"


class Ps1ReembedIsStamped(unittest.TestCase):
    def test_static_no_raw_vector_insert(self):
        body = reembed_python()
        self.assertIsNone(re.search(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+\{?\w*table|INSERT[^\n]*memory_embeddings", body),
                          "the here-string writes vectors around memory_write")
        self.assertIn("embed_backfill", body)

    @unittest.skipUnless(HAVE_VEC, "sqlite_vec is not importable here: no brain can be built")
    def test_control_every_vector_lands_with_the_locks_checksum(self):
        import build_compat
        with tempfile.TemporaryDirectory(prefix="vl_ps1_reembed_") as td:
            work = Path(td)
            db = build_compat.fresh_brain(ROOT, work, steps=("init",))
            brain = db.parent
            c = sqlite3.connect(str(db))
            c.executemany("INSERT INTO memories (content, category) VALUES (?, 'insight')",
                          [("tides turn twice a day",), ("a lock names the bytes",), ("seeds load once",)])
            c.commit()
            c.close()
            (work / "reembed.py").write_text(reembed_python(), encoding="utf-8")
            (work / "wrap.py").write_text(WRAPPER, encoding="utf-8")
            env = {**os.environ, "HOME": str(work), "WIZARD_LIBRARY_DB": str(db), "HF_HUB_OFFLINE": "1",
                   "PYTHONDONTWRITEBYTECODE": "1"}
            r = subprocess.run([sys.executable, str(work / "wrap.py"), str(db), str(brain)], cwd=str(work),
                               env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
            import embedder_lock
            want = embedder_lock.catalogue_tiers()["cpu"]["weights_hash"]
            c = sqlite3.connect(str(db))
            try:
                live = c.execute("SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0").fetchone()[0]
                stamps = c.execute("SELECT weights_hash, COUNT(*) FROM embedding_provenance "
                                   "WHERE vec_table='memory_embeddings' GROUP BY weights_hash").fetchall()
            finally:
                c.close()
            self.assertGreaterEqual(live, 3)
            self.assertEqual(stamps, [(want, live)], r.stdout[-600:])


if __name__ == "__main__":
    unittest.main()

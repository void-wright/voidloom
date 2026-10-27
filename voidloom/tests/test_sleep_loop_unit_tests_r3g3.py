# -*- coding: utf-8 -*-
"""Round 3 G3, item 3: `sleep_loop.py --unit-tests` (documented in the module's header) runs its rows.

MEASURED (G1, then again before this fix): it died before any row -- `EmbedTierUnresolved: metadata.active_embedding_table
is absent or empty` at its test 6, exit 1, nothing reported -- because its fixture brain declared no embedding tier
(embed_resolver refuses to guess). With that one line fixed, its reflection row then reached memory_write's default
encoder and tried to fetch the embedding model from the network ("couldn't connect to 'https://huggingface.co'"),
exit 1 again. The smaller of the two remedies (fix, not remove) is two pieces inside unit_tests(): the fixture declares
its tier, and for the suite's duration the write hook embeds with a deterministic stand-in vector.

Bound here: in a scratch HOME with the Hugging Face hub OFFLINE and empty caches (no model can load, none is needed),
the documented command exits 0 and reports every row passed -- at least the 27 rows it carries today.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()


def test_sleep_loop_unit_tests_run_every_row(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("WIZARD_", "XDG_", "HF_")) and k not in ("CLAUDE_MEMORY_DB", "VOIDLOOM_ENV_DIR")}
    env.update(HOME=str(tmp_path), TMPDIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", XDG_CACHE_HOME=str(tmp_path / "cache"), HF_HOME=str(tmp_path / "hf"))
    p = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "sleep_loop.py"), "--unit-tests"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=600)
    m = re.search(r"Sleep Loop Unit Tests: (\d+) passed, (\d+) failed", p.stdout)
    assert m, ("the suite reported no rows (it died first)", p.returncode, p.stdout[-600:], p.stderr[-1200:])
    passed, failed = int(m.group(1)), int(m.group(2))
    assert failed == 0, [l for l in p.stdout.splitlines() if "FAIL" in l]
    assert passed >= 27, ("fewer rows ran than the suite carries", passed)
    assert p.returncode == 0, p.returncode
    assert "loading embedding model" not in p.stderr, "the suite loaded a real embedding model"

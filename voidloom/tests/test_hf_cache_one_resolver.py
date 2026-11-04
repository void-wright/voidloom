"""One reader of the Hugging Face cache location (organs/embedder_lock.py hf_hub_cache), in the library's own order.

The loader, the provenance stamp, the reranker's offline check and the bootstrap census each need the same directory.
  * control: every limb of the order, and where huggingface_hub is importable, the library itself gives the same answer;
  * saboteur: with HF_HUB_CACHE set beside a decoy under HF_HOME, each reader takes the HF_HUB_CACHE side;
  * control: with nothing set, each reader takes the default;
  * static: no shipped file but the resolver reads these variables.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))
import embedder_lock  # noqa: E402
import hybrid_retrieval  # noqa: E402
import memory_write  # noqa: E402
from voidloom import _bootstrap  # noqa: E402

KEYS = ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
MODEL = "acme/some-model"
DIRNAME = "models--acme--some-model"


class _Env(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_hf_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        env = {k: v for k, v in os.environ.items() if k not in KEYS}
        env["HOME"] = str(self.d / "home")
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def _plant(self, hub: Path, ref: str):
        m = hub / DIRNAME
        (m / "refs").mkdir(parents=True)
        (m / "refs" / "main").write_text(ref, encoding="utf-8")
        (m / "snapshots" / ref).mkdir(parents=True)


class TheOrder(_Env):
    CASES = (
        ({"HF_HUB_CACHE": "a", "HUGGINGFACE_HUB_CACHE": "b", "HF_HOME": "c", "XDG_CACHE_HOME": "d"}, ("a",)),
        ({"HUGGINGFACE_HUB_CACHE": "b", "HF_HOME": "c", "XDG_CACHE_HOME": "d"}, ("b",)),
        ({"HF_HOME": "c", "XDG_CACHE_HOME": "d"}, ("c", "hub")),
        ({"XDG_CACHE_HOME": "d"}, ("d", "huggingface", "hub")),
    )

    def test_control_each_limb(self):
        for env, want in self.CASES:
            with mock.patch.dict(os.environ, {k: str(self.d / v) for k, v in env.items()}):
                self.assertEqual(embedder_lock.hf_hub_cache(), self.d.joinpath(*want), env)
        self.assertEqual(embedder_lock.hf_hub_cache(), self.d / "home" / ".cache" / "huggingface" / "hub")

    def test_control_the_library_agrees_on_every_limb(self):
        code = "import huggingface_hub.constants as c; print(c.HF_HUB_CACHE)"
        for env, _want in self.CASES + (({}, ()),):
            full = dict(os.environ, **{k: str(self.d / v) for k, v in env.items()})
            p = subprocess.run([sys.executable, "-c", code], env=full, capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                self.skipTest("huggingface_hub is not importable here")
            with mock.patch.dict(os.environ, full, clear=True):
                self.assertEqual(p.stdout.strip(), str(embedder_lock.hf_hub_cache()), env)

    def test_saboteur_an_empty_variable_is_unset(self):
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": "", "HF_HOME": " ", "XDG_CACHE_HOME": ""}):
            self.assertEqual(embedder_lock.hf_hub_cache(), self.d / "home" / ".cache" / "huggingface" / "hub")


class EveryReaderTakesTheOneCache(_Env):
    def _pair(self):
        custom, decoy = self.d / "custom-hub", self.d / "hf-home"
        self._plant(custom, "c0ffee")
        self._plant(decoy / "hub", "decoy")
        os.environ["HF_HUB_CACHE"] = str(custom)
        os.environ["HF_HOME"] = str(decoy)
        return custom

    def test_saboteur_the_stamp_reads_hf_hub_cache_not_hf_home(self):
        self._pair()
        self.assertEqual(memory_write._model_ref_for("no-such-tier", MODEL), ("c0ffee", "hf-refs-main"))

    def test_control_the_stamp_reads_the_default_with_nothing_set(self):
        self._plant(self.d / "home" / ".cache" / "huggingface" / "hub", "dflt")
        self.assertEqual(memory_write._model_ref_for("no-such-tier", MODEL), ("dflt", "hf-refs-main"))

    def test_saboteur_the_offline_check_sees_only_the_one_cache(self):
        self._plant(self.d / "hf-home" / "hub", "decoy")
        os.environ["HF_HOME"] = str(self.d / "hf-home")
        self.assertTrue(hybrid_retrieval._reranker_offline_ok(MODEL))
        os.environ["HF_HUB_CACHE"] = str(self.d / "empty-hub")
        self.assertFalse(hybrid_retrieval._reranker_offline_ok(MODEL), "it read HF_HOME past HF_HUB_CACHE")

    def test_saboteur_the_census_lists_the_one_cache(self):
        custom = self._pair()
        self.assertEqual(_bootstrap._hf_cache_roots(), [custom])


class OneReader(unittest.TestCase):
    def test_static_no_other_shipped_reader(self):
        pat = re.compile(r"""environ(?:\.get\(|\[)\s*["'](?:HF_HOME|HF_HUB_CACHE|HUGGINGFACE_HUB_CACHE)["']""")
        hits, n = [], 0
        for top in ("organs", "voidloom", "capability"):
            for f in (ROOT / top).rglob("*.py"):
                if "tests" in f.parts or f.name == "embedder_lock.py":
                    continue
                n += 1
                if pat.search(f.read_text(encoding="utf-8", errors="replace")):
                    hits.append(str(f.relative_to(ROOT)))
        self.assertGreater(n, 50)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

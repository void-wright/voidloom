"""The two side stores that embed with nomic load it AT THE SHIPPED PIN, never by name.

THE CLAIM UNDER TEST
  organs/website_memory.py (its own 768-wide database) and organs/whisper_network.py
  (its connection-less fallback) used to call
      SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", revision=<pin>, trust_remote_code=True)
  which pins the model but resolves its remote-code repository at the hub's moving
  `main`. A model cache filled at the catalogue's pins (what the installer and
  organs/model_fetch.py write) holds no `main` ref, so that load fails offline, and
  online it can run code bytes nobody checked. Both now go through
  embedding_upgrade.get_locked_embedder with the shipped catalogue's `cpu` entry
  (organs/embedders.lock.json): model and code at their revisions, the checksum
  verified before anything is instantiated.

Control/saboteur pairs; no model is loaded. The control replaces the one loader with
a recorder; the saboteur runs the REAL loader against an empty, offline model cache
with sentence_transformers replaced by a tripwire, and asserts the refusal is typed
and nothing was instantiated.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import embedder_lock  # noqa: E402
import embedding_upgrade  # noqa: E402

CPU = embedder_lock.catalogue_tiers()["cpu"]


class _Tripwire(types.ModuleType):
    fired = 0

    def __init__(self):
        super().__init__("sentence_transformers")

        def _boom(*a, **kw):
            _Tripwire.fired += 1
            raise AssertionError("SentenceTransformer was instantiated: %r %r" % (a, kw))
        self.SentenceTransformer = _boom


class SideStoresLoadByPin(unittest.TestCase):
    def setUp(self):
        self._keep_env = {k: os.environ.get(k) for k in ("HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE",
                                                          "HUGGINGFACE_HUB_CACHE")}
        self._keep_cache = dict(embedding_upgrade._model_cache)
        embedding_upgrade._model_cache.clear()
        self._real = (embedding_upgrade.get_locked_embedder, embedding_upgrade.encode_locked)
        self.seen = []

    def tearDown(self):
        embedding_upgrade.get_locked_embedder, embedding_upgrade.encode_locked = self._real
        embedding_upgrade._model_cache.clear()
        embedding_upgrade._model_cache.update(self._keep_cache)
        for k, v in self._keep_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    # ---- helpers
    def _record(self):
        def fake_get(lock):
            self.seen.append(dict(lock))
            return object(), lock

        def fake_encode(text, *, is_query, lock):
            self.seen.append(dict(lock))
            return b"\0" * 4 * int(lock["dim"])
        embedding_upgrade.get_locked_embedder = fake_get
        embedding_upgrade.encode_locked = fake_encode

    def _empty_offline_cache(self):
        hf = Path(tempfile.mkdtemp(prefix="vl_empty_hf_"))
        os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
        os.environ.update({"HF_HOME": str(hf), "HF_HUB_CACHE": str(hf / "hub"), "HF_HUB_OFFLINE": "1"})

    def _assert_the_pin(self, lock):
        for k in ("model_id", "revision", "weights_hash", "dim", "vec_table"):
            self.assertEqual(lock[k], CPU[k], k)
        self.assertEqual(lock.get("code"), CPU.get("code"), "the remote-code repository at ITS pin")
        self.assertEqual(lock["tier"], "cpu")

    # ---- whisper_network: the connection-less fallback
    def test_whisper_control_fallback_encodes_through_the_catalogue_pin(self):
        import whisper_network
        self._record()
        out = whisper_network._encode_for_embedding("a whisper", conn=None)
        self.assertEqual(len(out), 4 * 768)
        self.assertEqual(len(self.seen), 1)
        self._assert_the_pin(self.seen[0])

    def test_whisper_saboteur_empty_offline_cache_refuses_and_instantiates_nothing(self):
        import whisper_network
        self._empty_offline_cache()
        trip = _Tripwire()
        keep = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = trip
        try:
            with self.assertLogs(whisper_network.logger, level="WARNING") as logs:
                out = whisper_network._encode_for_embedding("a whisper", conn=None)
        finally:
            if keep is None:
                sys.modules.pop("sentence_transformers", None)
            else:
                sys.modules["sentence_transformers"] = keep
        self.assertIsNone(out)
        self.assertEqual(_Tripwire.fired, 0, "nothing may be instantiated from unverified bytes")
        self.assertIn("SNAPSHOT_ABSENT", "\n".join(logs.output))

    # ---- website_memory: its own nomic store
    def test_website_control_loads_through_the_catalogue_pin(self):
        import website_memory
        self._record()
        website_memory._model_cache.clear()
        try:
            website_memory._load_model()
        finally:
            website_memory._model_cache.clear()
        self.assertEqual(len(self.seen), 1)
        self._assert_the_pin(self.seen[0])

    def test_website_saboteur_empty_offline_cache_refuses_typed_and_instantiates_nothing(self):
        import website_memory
        self._empty_offline_cache()
        trip = _Tripwire()
        keep = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = trip
        website_memory._model_cache.clear()
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                website_memory._load_model()
        finally:
            website_memory._model_cache.clear()
            if keep is None:
                sys.modules.pop("sentence_transformers", None)
            else:
                sys.modules["sentence_transformers"] = keep
        self.assertEqual(cm.exception.code, 1)
        env = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(env["status"], "error")
        self.assertIn("model_fetch.py", env["message"], "the refusal names the public fetch command")
        self.assertEqual(_Tripwire.fired, 0, "nothing may be instantiated from unverified bytes")


if __name__ == "__main__":
    unittest.main()

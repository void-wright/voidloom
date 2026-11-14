"""The model garden's cross-encoder and GLiNER loaders read the local cache only.

Both load by repository name. Without `local_files_only=True` a load whose bytes are not in the cache reaches the
network to fetch them, at whatever the hub serves that day, from inside an organ that never asked to install
anything. With it, an absent model is a load error the garden already treats as unavailable.

No model library is imported: the two loader classes are stood in for by recorders, and the device resolver is
answered 'cpu', so this runs on any host.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))


class _Recorder:
    calls = []

    def __init__(self, *args, **kwargs):
        type(self).calls.append((args, kwargs))

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        cls.calls.append((args, kwargs))
        return cls.__new__(cls)

    def to(self, device):
        return self


class GardenLoadsFromTheLocalCache(unittest.TestCase):
    def setUp(self):
        import model_garden
        self.mg = model_garden
        _Recorder.calls = []
        st = types.ModuleType("sentence_transformers")
        st.CrossEncoder = _Recorder
        gl = types.ModuleType("gliner")
        gl.GLiNER = _Recorder
        patches = [mock.patch.dict(sys.modules, {"sentence_transformers": st, "gliner": gl}),
                   mock.patch.object(model_garden, "_resolve_device", lambda spec: "cpu")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_cross_encoder_loads_with_local_files_only(self):
        spec = self.mg.MODEL_REGISTRY["nli"]
        self.mg._load_cross_encoder(spec)
        (args, kwargs), = _Recorder.calls
        self.assertEqual(args, (spec.repo_id,))
        self.assertIs(kwargs.get("local_files_only"), True, kwargs)

    def test_gliner_loads_with_local_files_only(self):
        spec = self.mg.MODEL_REGISTRY["gliner"]
        self.mg._load_gliner(spec)
        (args, kwargs), = _Recorder.calls
        self.assertEqual(args, (spec.repo_id,))
        self.assertIs(kwargs.get("local_files_only"), True, kwargs)


if __name__ == "__main__":
    unittest.main()

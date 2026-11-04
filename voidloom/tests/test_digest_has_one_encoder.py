"""The weekly digest encodes through the brain's one encoder, and no other.

THE CLAIM UNDER TEST: when encoding for the brain's tier fails, the digest gets no vector
and the failure is said. Nothing is fetched or loaded for a model the brain is not on.
No model is loaded.
"""
from __future__ import annotations

import inspect
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))


class DigestEncoder(unittest.TestCase):
    def setUp(self):
        import embedding_upgrade
        import memory_write
        import whisper_network
        self.eu, self.mw, self.wn = embedding_upgrade, memory_write, whisper_network
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_control_the_encoder_answers_for_the_callers_connection(self):
        seen = []

        def enc(text, conn=None):
            seen.append(conn)
            return b"\0" * 8
        with mock.patch.object(self.mw, "default_encoder", enc):
            self.assertEqual(self.wn._encode_for_embedding("a digest", self.conn), b"\0" * 8)
        self.assertEqual(seen, [self.conn])

    def test_saboteur_a_failed_encode_loads_nothing_else(self):
        def enc(text, conn=None):
            raise RuntimeError("WEIGHTS_MISMATCH: planted")

        def tripwire(*a, **k):
            raise AssertionError("a second encoder ran")
        with mock.patch.object(self.mw, "default_encoder", enc), \
                mock.patch.object(self.eu, "encode_locked", tripwire), \
                mock.patch.object(self.eu, "get_locked_embedder", tripwire), \
                self.assertLogs(self.wn.logger, level="ERROR") as logs:
            self.assertIsNone(self.wn._encode_for_embedding("a digest", self.conn))
        self.assertIn("no fallback encoder", "\n".join(logs.output))

    def test_control_the_connection_is_required(self):
        p = inspect.signature(self.wn._encode_for_embedding).parameters["conn"]
        self.assertIs(p.default, inspect.Parameter.empty, "there is no tier without a database")


if __name__ == "__main__":
    unittest.main()

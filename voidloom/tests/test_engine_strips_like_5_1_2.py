"""The engine hands the model the text exactly as sentence-transformers 5.1.2's tokenizer saw it: stripped.

5.1.2 stripped every input inside its stock Transformer module (after a prompt was prepended); later versions do not,
and Qwen3-Embedding's byte-level BPE embeds an edge-whitespace text to a different vector. The engine strips first, so
under 5.1.2 nothing changes (a second strip is a no-op) and under a later version the stored vectors still match.
  * saboteur: with a model that does NOT strip (what a later version is), every one of the engine's encode paths --
    single and batch, document and query, nomic and Qwen, and the website store -- hands it the stripped text, and an
    edge-whitespace input gets the same vector as its stripped form;
  * control: Qwen's query side keeps the leading edge (5.1.2 stripped prompt + text as one string), and jina's path is
    left as given (its remote-code module never stripped).
"""
from __future__ import annotations

import hashlib
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import embedding_upgrade as eu  # noqa: E402
import embedder_lock as el  # noqa: E402


class _Raw:
    """A model that does not strip: it embeds the exact string it is handed, and records it."""

    def __init__(self, dim):
        self.dim, self.seen = dim, []

    def _vec(self, s):
        h = hashlib.sha256(s.encode("utf-8")).digest()
        return [b / 255.0 for b in (h * (self.dim // 32 + 1))[:self.dim]]

    def encode(self, x, prompt_name=None, **_kw):
        import numpy as np
        items = [x] if isinstance(x, str) else list(x)
        out = []
        for s in items:
            framed = ("Instruct: q\nQuery:" + s) if prompt_name == "query" else s
            self.seen.append(framed)
            out.append(np.asarray(self._vec(framed), dtype=np.float32))
        return out[0] if isinstance(x, str) else out


class TheEngineStrips(unittest.TestCase):
    def lock(self, tier):
        return dict(el.catalogue_tiers()[tier], tier=tier)

    def run_with(self, tier):
        lock = self.lock(tier)
        raw = _Raw(int(lock["dim"]))
        p = mock.patch.object(eu, "get_locked_embedder", return_value=(raw, lock))
        return raw, lock, p

    def test_saboteur_nomic_documents_and_queries_reach_the_model_stripped(self):
        raw, lock, p = self.run_with("cpu")
        with p:
            a = eu.encode_locked("harbour bells \n", is_query=False, lock=lock)
            b = eu.encode_locked("harbour bells", is_query=False, lock=lock)
            q = eu.encode_locked("harbour bells\t ", is_query=True, lock=lock)
            batch = eu.encode_locked_batch(["harbour bells \n", "tides\n"], is_query=False, lock=lock)
            lead = eu.encode_locked("  harbour bells", is_query=False, lock=lock)
        self.assertEqual(a, b)
        self.assertEqual(batch[0], b)
        self.assertEqual(raw.seen[0], "search_document: harbour bells")
        # the prefix comes first, so a leading edge sits inside the string 5.1.2 stripped, and it stays
        self.assertEqual(raw.seen[-1], "search_document:   harbour bells")
        self.assertNotEqual(lead, b)
        self.assertEqual(raw.seen[2], "search_query: harbour bells")
        self.assertEqual(raw.seen[4], "search_document: tides")

    def test_saboteur_qwen_documents_reach_the_model_stripped_on_both_edges(self):
        raw, lock, p = self.run_with("gpu")
        with p:
            a = eu.encode_locked("\n  a pier with a hairline crack  ", is_query=False, lock=lock)
            b = eu.encode_locked("a pier with a hairline crack", is_query=False, lock=lock)
            batch = eu.encode_locked_batch(["  a pier with a hairline crack\n"], is_query=False, lock=lock)
        self.assertEqual(a, b)
        self.assertEqual(batch[0], b)
        self.assertEqual(raw.seen[0], "a pier with a hairline crack")

    def test_control_qwen_queries_keep_the_leading_edge_after_the_prompt(self):
        raw, lock, p = self.run_with("gpu")
        with p:
            eu.encode_locked("  crack in pier seven \n", is_query=True, lock=lock)
            eu.encode_locked_batch(["  crack in pier seven \n"], is_query=True, lock=lock)
        self.assertEqual(raw.seen, ["Instruct: q\nQuery:  crack in pier seven"] * 2)

    def test_saboteur_the_website_store_shares_the_engines_encode(self):
        import website_memory as wm
        raw, lock, p = self.run_with("cpu")
        with p, mock.patch.object(wm, "_load_model", return_value=raw), mock.patch.object(wm, "_store_lock",
                                                                                          return_value=lock):
            v = wm._encode("  a website note  ", "search_document")
        self.assertEqual(raw.seen, ["search_document:   a website note"])
        self.assertEqual(len(v), 4 * int(lock["dim"]))
        self.assertEqual(v, struct.pack(f"{int(lock['dim'])}f", *struct.unpack(f"{int(lock['dim'])}f", v)))

    def test_saboteur_a_batch_keeps_the_batches_5_1_2_made_of_the_texts_as_given(self):
        """5.1.2 sorted the texts by their length AS GIVEN and cut batches; stripping first would move an edge-whitespace
        text into another batch, and a batch's padding changes its vectors in the last bits."""
        import numpy as np
        lock = self.lock("gpu")
        calls = []

        class Rec(_Raw):
            def encode(self, x, prompt_name=None, batch_size=None, **kw):
                calls.append(list(x))
                return super().encode(x, prompt_name=prompt_name, **kw)
        raw = Rec(int(lock["dim"]))
        texts = ["aaaa", "bb" + " " * 9, "cccccc", "d"]          # given lengths 4, 11, 6, 1; stripped 4, 2, 6, 1
        with mock.patch.object(eu, "get_locked_embedder", return_value=(raw, lock)), \
                mock.patch.object(eu, "BATCH_SIZE", 2):
            eu.encode_locked_batch(texts, is_query=False, lock=lock)
        order = [int(i) for i in np.argsort([-len(t) for t in texts])]
        want = [sorted(t.strip() for t in (texts[i] for i in order[k:k + 2])) for k in (0, 2)]
        self.assertEqual([sorted(c) for c in calls], want)               # [bb, cccccc] then [aaaa, d]

    def test_saboteur_the_website_stores_load_failure_is_typed(self):
        import contextlib
        import io
        import json
        import website_memory as wm
        out = io.StringIO()
        for err, code in ((OSError("disk said no"), "EMBEDDER_UNAVAILABLE"),
                          (el.EmbedderLockError("SNAPSHOT_ABSENT", "planted"), "SNAPSHOT_ABSENT")):
            out.truncate(0)
            out.seek(0)
            with mock.patch.dict(wm._model_cache, {}, clear=True), \
                    mock.patch.object(eu, "get_locked_embedder", side_effect=err), \
                    mock.patch.object(wm, "_store_lock", return_value=self.lock("cpu")), \
                    contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                wm._load_model()
            self.assertEqual(cm.exception.code, 1)
            env = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual((env["status"], env["error"]), ("error", code), env)

    def test_control_the_helper_is_a_no_op_on_a_stripped_text(self):
        for t in ("a", "a b", "search_document: a"):
            self.assertEqual(eu.as_tokenized_by_5_1_2(t, after_prompt=False), t)
            self.assertEqual(eu.as_tokenized_by_5_1_2(t, after_prompt=True), t)

    def test_control_jina_is_left_as_given(self):
        import inspect
        src = inspect.getsource(eu.encode_jina)
        self.assertNotIn("as_tokenized_by_5_1_2(", src.split("model.encode(", 1)[1])


if __name__ == "__main__":
    unittest.main()

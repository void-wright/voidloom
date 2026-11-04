"""A read verb does not reach the network, for the embedder either.

THE CLAIM UNDER TEST
  * a query encode runs with the hub closed, and the process is left as it was found;
  * the loader's own call is still reached: a locked brain with no snapshot refuses SNAPSHOT_ABSENT
    from `verify_snapshot_against_lock`, naming a command, and no socket is touched;
  * the front door turns that into a labelled keyword answer;
  * a document embed, `tier set`'s path and the preflight's warm step keep their way to the pinned bytes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import embedder_lock as el  # noqa: E402

GUARD = r'''
import json, os, socket, sys
_seen = []
def _connect(self, addr):
    _seen.append(repr(addr)); raise OSError("network refused by the test")
socket.socket.connect = _connect
def _lookup(*a, **k):
    _seen.append("dns:" + repr(a[:2])); raise socket.gaierror("network refused by the test")
socket.getaddrinfo = _lookup
root, db, verb = sys.argv[1:4]
sys.path.insert(0, root); sys.path.insert(0, root + "/organs")
os.environ["WIZARD_LIBRARY_DB"] = db
out = {"seen": _seen}
if verb == "engine":
    import claude_memory as cm, pathlib
    cm.DB_PATH = pathlib.Path(db)
    try:
        cm._encode("what did the lantern say", "search_query")
        out["encoded"] = True
    except Exception as e:
        out.update(code=getattr(e, "code", type(e).__name__), message=str(e), retry=getattr(e, "detail", {}).get("retry"))
else:
    import contextlib, io
    from voidloom import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out["rc"] = cli.dispatch(["recall", "--db", db, "--query", "lantern", "--json"])
    out["envelope"] = json.loads(buf.getvalue())
print("\n" + json.dumps(out))
'''


class _Room(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        import schema_migrator
        schema_migrator.apply(str(self.db), ROOT / "migrations")
        c = schema_migrator.connect(str(self.db))
        c.execute("INSERT INTO memories (content, summary, category, importance) "
                  "VALUES ('the lantern room keeper', 's', 'insight', 0.5)")
        c.commit()
        c.close()
        el.write_lock(self.d, "cpu")

    def tearDown(self):
        self.tmp.cleanup()

    def run_guarded(self, verb):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_", "WIZARD_"))}
        env.update(HOME=str(self.d), HF_HUB_CACHE=str(self.d / "hub"), WIZARD_ACCELERATOR="cpu",
                   PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-c", GUARD, str(ROOT), str(self.db), verb], capture_output=True, text=True,
                           timeout=240, env=env, cwd=str(self.d))
        lines = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("{")]
        return json.loads(lines[-1]), r


class ALockedBrainWithNoSnapshot(_Room):
    def test_saboteur_a_query_refuses_typed_names_a_command_and_touches_no_socket(self):
        out, r = self.run_guarded("engine")
        self.assertEqual(out.get("code"), "SNAPSHOT_ABSENT", (out, r.stderr[-800:]))
        self.assertIn("a read does not fetch", out["message"])
        self.assertIn("model_fetch.py", out["retry"][0])
        self.assertEqual(out["seen"], [], "the read reached for the network")

    def test_saboteur_the_front_door_answers_by_keyword_and_says_why(self):
        out, r = self.run_guarded("front")
        self.assertEqual(out["seen"], [], "the read reached for the network")
        self.assertEqual(out["rc"], 0, r.stderr[-800:])
        env = out["envelope"]
        self.assertEqual(env["data"]["tier"], 0, env)
        self.assertIn("SNAPSHOT_ABSENT", json.dumps(env["data"]))
        self.assertIn("model_fetch.py", json.dumps(env["data"]))


class WhatIsClosedAndWhatIsNot(unittest.TestCase):
    def setUp(self):
        import claude_memory as cm
        self.cm = cm
        self.seen = []
        self._env = {k: os.environ.pop(k, None) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def record(self, text, prefix, *, lock=None):
        self.seen.append(el.hf_offline())
        return b""

    def test_saboteur_a_query_encodes_with_the_hub_closed(self):
        with mock.patch.object(self.cm, "_encode_now", self.record):
            self.cm._encode("q", "search_query")
        self.assertEqual(self.seen, [True])

    def test_control_a_document_embed_keeps_the_hub_open(self):
        with mock.patch.object(self.cm, "_encode_now", self.record):
            self.cm._encode("d", "search_document")
        self.assertEqual(self.seen, [False])

    def test_control_the_process_is_left_as_it_was_found(self):
        with mock.patch.object(self.cm, "_encode_now", self.record):
            self.cm._encode("q", "search_query")
        self.assertIs(el.hf_offline(), False)
        self.assertNotIn("HF_HUB_OFFLINE", os.environ)

    def test_control_a_refusal_inside_leaves_the_process_as_it_was_found(self):
        def boom(*a, **k):
            raise el.EmbedderLockError("SNAPSHOT_ABSENT", "planted")
        with mock.patch.object(self.cm, "_encode_now", boom):
            with self.assertRaises(el.EmbedderLockError):
                self.cm._encode("q", "search_query")
        self.assertIs(el.hf_offline(), False)

    def test_control_an_operators_offline_request_survives_the_block(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            with el.hub_closed():
                pass
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        finally:
            del os.environ["HF_HUB_OFFLINE"]

    def test_control_the_preflight_warms_by_a_document_embed(self):
        text = (ROOT / "install" / "preflight.py").read_text(encoding="utf-8")
        self.assertIn("cm._encode('warm up', prefix='search_document')%s", text)

    def test_control_a_fetching_caller_still_reaches_the_download(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"HF_HUB_CACHE": td}):
            import huggingface_hub
            with mock.patch.object(huggingface_hub, "snapshot_download", side_effect=RuntimeError("fetched")) as dl:
                with self.assertRaises(RuntimeError):
                    el.verify_snapshot_against_lock(el.catalogue_tiers()["cpu"])
            self.assertTrue(dl.called)


if __name__ == "__main__":
    unittest.main()

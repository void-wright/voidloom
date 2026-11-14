"""`export` says when a record's text does not match the stamp its vector was embedded from.

THE CLAIM UNDER TEST
  * each exported (and inspected) record carries `text_matches_stamp`: True when the sha256 of the text as stored equals
    its stamp's content_hash, False when it does not, None when there is nothing to check it against (no stamp, or a
    stamp derived from member vectors rather than from text);
  * the export envelope names the count it could not verify and the ids whose text does not match; a mismatch keeps
    status ok, flags the envelope degraded at the top with its own code, and every figure is backed by an independent
    byte check (sha256 over CAST(content AS BLOB), computed here, not by the verb);
  * a text corrupted behind the writer -- its stamp unchanged -- is caught, including bytes that are not UTF-8.
"""
from __future__ import annotations

import argparse
import hashlib
import unittest

from voidloom.tests.test_doctor_brain_verdict import _Brain, cli, mw


class _Export(_Brain):
    def setUp(self):
        super().setUp()
        self.ids = [self.memory(f"memory {i} about tides and bells") for i in range(3)]
        for mid in self.ids:
            self.text_stamp(mid)

    def text_stamp(self, mid, kind="text"):
        text = self.conn.execute("SELECT content FROM memories WHERE id = ?", (mid,)).fetchone()[0]
        ident = mw.model_identity(self.TABLE)
        self.conn.execute(
            f"INSERT OR REPLACE INTO {mw.PROVENANCE_TABLE} ({', '.join(mw.PROVENANCE_COLUMNS)}) "
            "VALUES (?, ?, ?, 'ref', ?, ?, ?, '2026-01-01T00:00:00+00:00', 'test', ?, ?)",
            (mid, self.TABLE, ident["model_id"], self.h, self.DIM, mw.content_hash(text), kind,
             None if kind == "text" else "[1, 2]"))
        self.conn.commit()

    def export(self, ids):
        return cli.verb_export(argparse.Namespace(db=str(self.db), ids=",".join(map(str, ids)), all=False, json=True))

    def byte_check(self, ids):
        """Independent of the verb: which of `ids` have a text stamp whose content_hash is not sha256(stored bytes)."""
        bad = []
        for mid in ids:
            raw = self.conn.execute("SELECT CAST(content AS BLOB) FROM memories WHERE id = ?", (mid,)).fetchone()[0]
            row = self.conn.execute(f"SELECT content_hash, input_kind FROM {mw.PROVENANCE_TABLE} WHERE memory_id = ?",
                                    (mid,)).fetchone()
            if row and row[1] == "text" and row[0] != hashlib.sha256(bytes(raw or b"")).hexdigest():
                bad.append(mid)
        return bad


class AText(_Export):
    def test_control_matching_text_exports_ok_and_verified(self):
        env = self.export(self.ids)
        self.assertEqual((env["status"], env["code"], env.get("degraded")), ("ok", "export_ok", None), env)
        chk = env["data"]["text_check"]
        self.assertEqual((chk["verified"], chk["not_matching"], chk["could_not_verify"]), (3, 0, 0), chk)
        self.assertEqual([r["text_matches_stamp"] for r in env["data"]["records"]], [True, True, True])
        self.assertEqual(self.byte_check(self.ids), [])

    def test_saboteur_a_text_corrupted_behind_the_writer_is_named(self):
        bad = self.ids[1]
        self.conn.execute("UPDATE memories SET content = ? WHERE id = ?", ("memory rewritten past the stamp", bad))
        self.conn.commit()
        env = self.export(self.ids)
        self.assertEqual((env["status"], env["code"], env.get("degraded")), ("ok", "export_text_unverified", True), env)
        chk = env["data"]["text_check"]
        self.assertEqual((chk["not_matching"], chk["not_matching_ids"], chk["could_not_verify"]), (1, [bad], 1), chk)
        self.assertEqual(chk["not_matching_ids"], self.byte_check(self.ids))
        recs = {r["id"]: r for r in env["data"]["records"]}
        self.assertIs(recs[bad]["text_matches_stamp"], False)
        self.assertIs(recs[self.ids[0]]["text_matches_stamp"], True)

    def test_saboteur_bytes_that_are_not_utf8_are_checked_not_skipped(self):
        bad = self.ids[2]
        self.conn.execute("UPDATE memories SET content = CAST(x'fffe41' AS TEXT) WHERE id = ?", (bad,))
        self.conn.commit()
        env = self.export(self.ids)
        chk = env["data"]["text_check"]
        self.assertEqual((chk["not_matching_ids"], self.byte_check(self.ids)), ([bad], [bad]), chk)

    def test_control_stored_bytes_that_are_not_utf8_and_match_their_stamp_verify(self):
        mid = self.ids[0]
        self.conn.execute("UPDATE memories SET content = CAST(x'fffe41' AS TEXT) WHERE id = ?", (mid,))
        self.conn.execute(f"UPDATE {mw.PROVENANCE_TABLE} SET content_hash = ? WHERE memory_id = ?",
                          (hashlib.sha256(b"\xff\xfeA").hexdigest(), mid))
        self.conn.commit()
        env = self.export(self.ids)
        self.assertEqual((env["code"], env["data"]["text_check"]["verified"]), ("export_ok", 3), env["data"]["text_check"])
        self.assertEqual(self.byte_check(self.ids), [])

    def test_control_no_stamp_and_a_centroid_stamp_are_unverifiable_not_corrupt(self):
        plain = self.memory("a memory with a vector and no stamp")
        centroid = self.memory("a consolidation")
        self.text_stamp(centroid, kind="centroid")
        env = self.export([*self.ids, plain, centroid])
        self.assertEqual((env["code"], env.get("degraded")), ("export_ok", None), env)
        chk = env["data"]["text_check"]
        self.assertEqual((chk["verified"], chk["not_matching"], chk["could_not_verify"]), (3, 0, 2), chk)
        recs = {r["id"]: r for r in env["data"]["records"]}
        self.assertIsNone(recs[plain]["text_matches_stamp"])
        self.assertIsNone(recs[centroid]["text_matches_stamp"])

    def test_inspect_shows_the_same_answer(self):
        bad = self.ids[0]
        self.conn.execute("UPDATE memories SET content = ? WHERE id = ?", ("changed", bad))
        self.conn.commit()
        env = cli.verb_inspect(argparse.Namespace(db=str(self.db), id=bad, json=True))
        self.assertIs(env["data"]["text_matches_stamp"], False, env)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""THE LEAK CLEAN (2026-10-14): the package ships as `git archive` of main to people who are not its author, so the
shipped tree must carry no client name, no home path of the author's machine and no personal handle.

  1  organs/persona_lenses.py: the starter persona profiles named the author's client projects as weights. The defaults
     are NEUTRAL now; an owner's own weights live in an optional per-brain file, <brain>/persona_lens_weights.json,
     overlaid at seeding (absent -> the neutral defaults).

This file never names a client: the "no client key in code" check compares HASHES of the keys that were removed, and
bounds the default project keys to a closed, generic set.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import os

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"


def _load(name, rel):
    """The module under test, loaded from ROOT BY PATH -- a plain import could find a copy already in sys.modules."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ORGANS))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(ORGANS))
    return mod


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="leakclean_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))


# ============================================================================================ 1: persona lens weights
class TestPersonaLensWeightsLiveOutsideTheCode(_Tmp):

    # The ONLY project keys the neutral defaults may weight: the package's own generic project names.
    GENERIC_PROJECTS = {"general"}     # the shipped defaults weight no project; owners add theirs in the overlay

    def setUp(self):
        super().setUp()
        self.pl = _load("pl_under_test", "organs/persona_lenses.py")

    def _brain(self, weights=None, raw=None):
        db = self.tmp / "claude_memory.db"
        c = sqlite3.connect(str(db))
        c.executescript("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);")
        c.executescript((ROOT / "migrations" / "migration_006_persona_lenses.sql").read_text(encoding="utf-8"))
        c.commit()
        if weights is not None:
            (self.tmp / "persona_lens_weights.json").write_text(json.dumps(weights), encoding="utf-8")
        if raw is not None:
            (self.tmp / "persona_lens_weights.json").write_text(raw, encoding="utf-8")
        return c

    def test_absent_file_seeds_the_neutral_defaults(self):
        c = self._brain()
        res = self.pl.seed_default_profiles(c)
        self.assertEqual((res["owner_weights_file"], res["owner_weights_applied"], res["owner_weights_error"]),
                         (None, [], None))
        hub = self.pl.get_profile(c, "hub-maintainer")
        default = next(p for p in self.pl._DEFAULT_PROFILES if p["slug"] == "hub-maintainer")
        self.assertEqual(hub["project_weights"], default["project_weights"])

    def test_the_owners_file_is_honoured_merged_and_reported(self):
        c = self._brain({"_comment": "ignored", "hub-maintainer": {"project_weights": {"project-a": 1.7, "narrative": 0.5},
                                                          "tag_weights": {"topic-x": 2.2},
                                                          "exclude_tags": ["noise"], "boost_recent_days": 3},
                         "ghost": {"project_weights": {"project-b": 9.0}}})
        res = self.pl.seed_default_profiles(c)
        self.assertEqual(res["owner_weights_applied"], ["hub-maintainer"])
        self.assertEqual(res["owner_weights_unknown_slugs"], ["ghost"])
        self.assertTrue(res["owner_weights_file"].endswith("persona_lens_weights.json"))
        hub = self.pl.get_profile(c, "hub-maintainer")
        self.assertEqual(hub["project_weights"]["project-a"], 1.7)
        self.assertEqual(hub["project_weights"]["narrative"], 0.5)                 # the file wins key by key
        self.assertEqual(hub["tag_weights"]["topic-x"], 2.2)
        self.assertIn("cms", hub["tag_weights"])                                    # the defaults stay beside it
        self.assertIn("noise", hub["exclude_tags"])
        self.assertEqual(hub["boost_recent_days"], 3)
        self.assertIsNone(self.pl.get_profile(c, "ghost"))                          # tunes, never invents
        ingester_p = self.pl.get_profile(c, "ingester")                                        # other personas untouched
        self.assertNotIn("project-a", ingester_p["project_weights"])

    def test_a_malformed_file_seeds_the_defaults_and_says_why(self):
        c = self._brain(raw="{ not json")
        res = self.pl.seed_default_profiles(c)
        self.assertTrue(res["owner_weights_error"].startswith("JSONDecodeError"), res)
        self.assertEqual(res["profiles_created"], len(self.pl._DEFAULT_PROFILES))

    def test_a_bad_value_is_skipped_and_named_and_the_rest_still_applies(self):
        # iteration 1 follow-up: a non-numeric weight raised ValueError and aborted seeding, breaking the
        # "malformed -> defaults + reason" contract. Every value is now validated; each bad one is skipped and named.
        c = self._brain({"hub-maintainer": {"project_weights": {"x": "abc", "flag": True, "good": 1.5, "nan": float("nan")},
                                  "tag_weights": "not-a-map",
                                  "exclude_tags": ["ok-tag", 7],
                                  "boost_recent_days": -1},
                         "ingester": {"boost_recent_days": True, "category_weights": {"insight": 2}}})
        res = self.pl.seed_default_profiles(c)
        self.assertEqual(res["profiles_created"], len(self.pl._DEFAULT_PROFILES), res)
        rejected = res["owner_weights_rejected"]
        for want in ("hub-maintainer.project_weights.x", "hub-maintainer.project_weights.flag", "hub-maintainer.project_weights.nan",
                     "hub-maintainer.tag_weights", "hub-maintainer.exclude_tags[1]", "hub-maintainer.boost_recent_days", "ingester.boost_recent_days"):
            self.assertTrue(any(r.startswith(want + ":") for r in rejected), (want, rejected))
        self.assertIn("rejected", res["owner_weights_error"])
        hub = self.pl.get_profile(c, "hub-maintainer")
        self.assertEqual(hub["project_weights"]["good"], 1.5)                       # the good value still applies
        self.assertNotIn("x", hub["project_weights"])
        self.assertIn("ok-tag", hub["exclude_tags"])
        default_hub = next(p for p in self.pl._DEFAULT_PROFILES if p["slug"] == "hub-maintainer")
        self.assertEqual(hub["boost_recent_days"], default_hub["boost_recent_days"])  # -1 rejected
        self.assertEqual(hub["tag_weights"], default_hub["tag_weights"])
        ingester_p = self.pl.get_profile(c, "ingester")
        self.assertEqual(ingester_p["category_weights"]["insight"], 2.0)                   # an int weight is fine
        default_ingester = next(p for p in self.pl._DEFAULT_PROFILES if p["slug"] == "ingester")
        self.assertEqual(ingester_p["boost_recent_days"], default_ingester["boost_recent_days"])  # a bool is not an int here

    def test_no_client_key_is_in_the_code(self):
        src = (ROOT / "organs" / "persona_lenses.py").read_text(encoding="utf-8")
        tokens = {t.lower() for t in re.findall(r'"([^"\n]{2,80})"', src)}
        hit = sorted(t for t in tokens if hashlib.sha256(t.encode()).hexdigest() in REMOVED_KEY_HASHES)
        self.assertEqual(hit, [], "a removed client key is back in organs/persona_lenses.py")
        for p in self.pl._DEFAULT_PROFILES:
            self.assertLessEqual(set(p.get("project_weights") or {}), self.GENERIC_PROJECTS, p["slug"])


# ============================================================================================ 2: no removed token in organs
class TestNoRemovedClientTokenInAnyOrgan(unittest.TestCase):
    """Every quoted string in every shipped organ, lower-cased and hashed, against the removed client tokens -- the
    class (fixture tags, weight keys, anything quoted), not the two files where they were found."""

    def test_no_organ_quotes_a_removed_client_token(self):
        hits = []
        for f in sorted((ROOT / "organs").glob("*.py")):
            for t in set(re.findall(r'"([^"\n]{2,80})"', f.read_text(encoding="utf-8", errors="replace"))):
                if hashlib.sha256(t.lower().encode()).hexdigest() in REMOVED_KEY_HASHES:
                    hits.append("%s: (a removed client token)" % f.name)
        self.assertEqual(sorted(set(hits)), [])

# ============================================================================================ 7: tree-wide home paths
class TestNoRealHomeDirectoryInTheTrackedTree(unittest.TestCase):
    """Every file `git ls-files` tracks (binary skipped): a /home/<name>, /Users/<name> or Windows Users\\<name> path may
    use only a PLACEHOLDER name. Closed world: any real name -- the author's included -- fails, and this test names
    nobody. One file is exempt, with its reason."""

    PLACEHOLDERS = {"someone", "x", "user", "yourname", "you", "somebody", "owner", "first", "first last", "example"}
    EXEMPT = {"tools/cross_platform_audit.py": "its own detector pattern for a hard-coded home path, not a path"}
    FORMS = (re.compile(r"/home/([A-Za-z0-9_.-]+)"), re.compile(r"/Users/([A-Za-z][A-Za-z0-9_. '-]*?)(?=[/\"'`\s]|$)"),
             re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}([A-Za-z][A-Za-z0-9_. '-]*?)(?=\\|[\"'`\s]|$)"))

    def test_only_placeholder_home_directories_ship(self):
        p = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, timeout=60)
        if p.returncode != 0:
            self.skipTest("not a git checkout: %s" % ROOT)
        bad = []
        for rel in [x for x in p.stdout.decode("utf-8", "replace").split("\0") if x]:
            if rel in self.EXEMPT:
                continue
            try:
                data = (ROOT / rel).read_bytes()
            except OSError:
                continue
            if b"\0" in data[:4096]:
                continue
            text = data.decode("utf-8", "replace")
            for rx in self.FORMS:
                for m in rx.finditer(text):
                    if m.group(1).strip().lower() not in self.PLACEHOLDERS:
                        bad.append("%s: %s" % (rel, m.group(0)[:40]))
        self.assertEqual(bad, [], "a real home directory ships (use a placeholder: %s)" % sorted(self.PLACEHOLDERS))

# sha256 of the lower-cased client tokens removed on 2026-10-14 (computed from main 6df87c8; never the names):
# the persona-lens weight keys (fix 1) and the memory_dreaming fixture tags (fix 2)
REMOVED_KEY_HASHES = {
    "6c171e24011c5fbcdba26ee14fc11e8a0ae11395ab96d516c0a96d967434a01f",
    "70ef275933b6e8ff3841ac63560c50a513ef95d63e74488bdb4dbfad2778996f",
    "56ebafd066150faa01991fa20ca2d2a37a6e00e2341c28db7bb69e67f2c4cd90",
    "65b3a7e7863f1e58eb5a2b0a70c359df5770d9645b2caaf1de60b513fba285e5",
    "d7d4f59db48fb4b8d9fe1fe1b7ec5bff489fc305e0ce38500bbb4acc2166e4ca",
}


if __name__ == "__main__":
    unittest.main()

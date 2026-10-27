# -*- coding: utf-8 -*-
"""GENERIC LENS NAMES (2026-10-14, ruling vl_generic_persona_names_ruling): the starter persona-lens profiles are named
for their ROLE -- ingester, researcher, site-maintainer, hub-maintainer, curator -- not for one owner's personas.

A brain seeded before the change STORES the legacy slugs, and it must keep working unchanged: no stored row is renamed
or migrated. Both spellings resolve in both directions (persona_lenses.LEGACY_LENS_SLUGS), seeding treats a stored
legacy row as the default already present, a weights file keyed by a legacy slug still applies, and recall's default
lens (DEFAULT_LENS) reaches the stored legacy row.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()

GENERIC = ["ingester", "researcher", "site-maintainer", "hub-maintainer", "curator"]


def _load_lenses():
    """organs/persona_lenses.py loaded from ROOT by path (a plain import could find a cached copy)."""
    spec = importlib.util.spec_from_file_location("pl_generic_under_test", str(ROOT / "organs" / "persona_lenses.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "organs"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(ROOT / "organs"))
    return mod


class _Brain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="genericlens_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.pl = _load_lenses()
        self.db = self.tmp / "claude_memory.db"
        self.c = sqlite3.connect(str(self.db))
        self.c.executescript("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);")
        self.c.executescript((ROOT / "migrations" / "migration_006_persona_lenses.sql").read_text(encoding="utf-8"))
        self.c.commit()

    def _legacy_slugs(self):
        return sorted(self.pl.LEGACY_LENS_SLUGS)

    def _rows(self):
        return self.c.execute("SELECT persona_slug, display_name, description, tag_weights, category_weights, "
                              "kind_weights, project_weights, exclude_tags, boost_recent_days, is_active "
                              "FROM persona_recall_profiles ORDER BY persona_slug").fetchall()

    def _store_legacy(self):
        """A brain seeded before 2026-10-14: every starter lens stored under its LEGACY slug, with the owner's own
        tuning (a distinct weight per row, so a lookup that read the wrong row would be caught)."""
        for n, (legacy, generic) in enumerate(sorted(self.pl.LEGACY_LENS_SLUGS.items())):
            self.pl.create_profile(self.c, slug=legacy, display_name="owner's %s" % generic,
                                   description="stored before the generic names",
                                   tag_weights={"owner-tag-%d" % n: 3.0 + n}, project_weights={"owner-project": 1.0 + n})


class TestTheStarterLensesAreGeneric(_Brain):
    def test_the_defaults_are_the_five_role_names_and_carry_no_legacy_name(self):
        self.assertEqual(sorted(p["slug"] for p in self.pl._DEFAULT_PROFILES), sorted(GENERIC))
        legacy = set(self.pl.LEGACY_LENS_SLUGS)
        for p in self.pl._DEFAULT_PROFILES:
            text = json.dumps(p).lower()
            for name in legacy:
                self.assertIsNone(re.search(r"\b%s\b" % name, text), (p["slug"], name))
        self.assertEqual(self.pl.DEFAULT_LENS, "curator")
        self.assertEqual(sorted(self.pl.LEGACY_LENS_SLUGS.values()), sorted(GENERIC))     # a bijection onto them

    def test_the_legacy_names_appear_in_the_lens_module_only_in_the_alias_map(self):
        src = (ROOT / "organs" / "persona_lenses.py").read_text(encoding="utf-8")
        i = src.index("LEGACY_LENS_SLUGS = {")
        outside = src[:i] + src[src.index("}", i) + 1:]
        for name in self.pl.LEGACY_LENS_SLUGS:
            self.assertIsNone(re.search(r"\b%s\b" % name, outside, re.I), name)

    def test_recall_defaults_to_the_generic_lens(self):
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        i = src.index('or os.environ.get("WIZARD_PERSONA")')
        self.assertIn('getattr(persona_lenses, "DEFAULT_LENS", "curator")', src[i:i + 200])


class TestABrainHoldingTheLegacySlugsKeepsWorking(_Brain):
    def setUp(self):
        super().setUp()
        self._store_legacy()
        self.before = self._rows()

    def test_each_generic_name_resolves_to_the_stored_legacy_row(self):
        for legacy, generic in self.pl.LEGACY_LENS_SLUGS.items():
            via_generic = self.pl.get_profile(self.c, generic)
            via_legacy = self.pl.get_profile(self.c, legacy)
            self.assertIsNotNone(via_generic, generic)
            self.assertEqual(via_generic["persona_slug"], legacy)              # the stored row, not a default
            self.assertEqual(via_generic, via_legacy)

    def test_recalls_default_lens_reaches_the_stored_curation_row(self):
        p = self.pl.get_profile(self.c, self.pl.DEFAULT_LENS)
        self.assertEqual(p["persona_slug"], "milamber" if "milamber" in self.pl.LEGACY_LENS_SLUGS else p["persona_slug"])
        self.assertEqual(p["display_name"], "owner's curator")

    def test_seeding_never_adds_a_twin_and_never_touches_a_stored_row(self):
        res = self.pl.seed_default_profiles(self.c)
        self.assertEqual((res["profiles_created"], res["profiles_skipped"]), (0, len(GENERIC)), res)
        self.assertEqual(self._rows(), self.before, "a stored legacy row changed, or a generic twin was added")

    def test_an_exact_match_wins_when_both_spellings_are_stored(self):
        self.pl.create_profile(self.c, slug="curator", display_name="explicit curator", tag_weights={"x": 1.0})
        self.assertEqual(self.pl.get_profile(self.c, "curator")["display_name"], "explicit curator")
        self.assertEqual(self.pl.get_profile(self.c, "milamber")["display_name"], "owner's curator")


class TestALegacyKeyedWeightsFileStillApplies(_Brain):
    def test_a_file_keyed_by_a_legacy_slug_tunes_the_generic_lens(self):
        legacy = next(k for k, v in self.pl.LEGACY_LENS_SLUGS.items() if v == "hub-maintainer")
        (self.tmp / "persona_lens_weights.json").write_text(
            json.dumps({legacy: {"tag_weights": {"owner-only-tag": 2.0}}}), encoding="utf-8")
        res = self.pl.seed_default_profiles(self.c)
        self.assertEqual(res["owner_weights_applied"], ["hub-maintainer"])
        self.assertEqual(res["owner_weights_unknown_slugs"], [])
        self.assertEqual(self.pl.get_profile(self.c, "hub-maintainer")["tag_weights"]["owner-only-tag"], 2.0)


class TestThePackageDeclaresNoPersona(unittest.TestCase):
    """Item 3 of the ruling: the package ships no persona. wizard_config.owner_persona defaulted to the author's persona
    name; it is empty now (the owner declares one, or none)."""

    def test_owner_persona_defaults_to_empty(self):
        spec = importlib.util.spec_from_file_location("wc_under_test", str(ROOT / "organs" / "wizard_config.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["wc_under_test"] = mod          # a dataclass module must be registered before it executes
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(ROOT / "organs"))
            sys.modules.pop("wc_under_test", None)
        self.assertEqual(mod.WizardConfig().owner_persona, "")

    def test_no_persona_declaration_is_tracked(self):
        import subprocess
        p = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertFalse([f for f in p.stdout.splitlines() if f.endswith("persona.json")])


class TestTheMonthlySelfEvalNamesNoPersona(unittest.TestCase):
    """The monthly self-evaluation asked every brain for a narrative 'in MILAMBER's voice' and labelled the lens
    piece 'MILAMBER persona recall profiles'. An undeclared brain now gets neither; a declared persona is named."""

    def _mod(self):
        organs = str(ROOT / "organs")
        spec = importlib.util.spec_from_file_location("mse_generic_under_test", str(ROOT / "organs" / "monthly_self_eval.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, organs)
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(organs)
        return mod

    def _prompt(self, mod, who):
        mod._declared_identity = lambda: who
        return mod._build_proposal_prompt({"pieces": {}}, "2026-10", {})

    def test_an_undeclared_brain_hears_no_author_persona(self):
        mod = self._mod()
        prompt = self._prompt(mod, None)
        self.assertNotIn("milamber", prompt.lower())
        self.assertIn("the brain's own voice", prompt)
        label = mod.DEFAULT_CONFIG["piece_registry"]["L3-C1-persona-lenses"]["description"]
        self.assertNotIn("milamber", label.lower())
        self.assertEqual(label, "persona recall lens profiles")

    def test_a_declared_persona_is_the_voice(self):
        mod = self._mod()
        prompt = self._prompt(mod, "ASH")
        self.assertIn("written in ASH's voice", prompt)
        self.assertNotIn("milamber", prompt.lower())


if __name__ == "__main__":
    unittest.main()

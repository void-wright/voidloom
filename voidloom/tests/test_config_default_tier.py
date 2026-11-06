"""What the config reports as a default embedder is a model the package ships.

THE CLAIM UNDER TEST: nothing loads from these fields (the install lock decides a brain's tier),
but they are read by people: the defaults name a shipped model at its own width, and jina stays
selectable by name. A model the Garden does not hold in the cache is not fetched by asking for it.
No model is loaded.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))


class ConfigDefaultTier(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        import wizard_config as wc
        self.wc = wc
        self.shipped = {(e["model_id"], int(e["dim"])) for e in el.catalogue_tiers().values()}

    def test_control_the_dataclass_default_is_the_catalogue_default_tier(self):
        import embedder_lock as el
        cfg = self.wc.WizardConfig()
        entry = el.catalogue_tiers()[el.DEFAULT_TIER]
        self.assertEqual((cfg.embed_model, cfg.embed_dim), (entry["model_id"], int(entry["dim"])))

    def test_control_every_profile_default_is_a_shipped_model_at_its_width(self):
        for name, prof in self.wc._PROFILES.items():
            self.assertIn((prof["embed_model"], int(prof["embed_dim"])), self.shipped, name)

    def test_saboteur_jina_stays_selectable_by_name(self):
        cfg = self.wc.WizardConfig(embed_model="jinaai/jina-embeddings-v5-text-small", embed_dim=1024)
        self.assertTrue(cfg.is_jina)


class TheEncumberedModelIsNoDefault(unittest.TestCase):
    def test_saboteur_the_garden_does_not_enable_it_and_says_what_it_is(self):
        import model_garden as mg
        spec = mg.MODEL_REGISTRY["jina"]
        self.assertIs(spec.enable_default, False)
        self.assertIn("cc-by-nc-4.0", spec.description)
        self.assertIn("Not shipped", spec.description)
        self.assertEqual(mg._is_enabled("jina", {})[0], False)

    def test_control_an_owner_who_enables_it_in_the_manifest_is_heard(self):
        import model_garden as mg
        self.assertEqual(mg._is_enabled("jina", {"models": {"jina": {"enabled": True}}})[0], True)


class GardenFetchesNothing(unittest.TestCase):
    def test_saboteur_asking_for_jina_on_a_host_without_it_loads_nothing(self):
        import embedding_upgrade as eu
        import model_garden as mg

        def tripwire():
            raise AssertionError("the jina loader ran on a host with no jina cache")
        with mock.patch.object(eu, "_get_jina_model", tripwire), \
                mock.patch.object(mg, "_cache_present", return_value=False):
            self.assertIsNone(mg.get_model("jina", force=True))


if __name__ == "__main__":
    unittest.main()

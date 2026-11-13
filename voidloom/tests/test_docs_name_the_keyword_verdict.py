"""Every page that names the self-check's verdict as the proof of a working install names both verdicts a passing run
can end on: the one that stores and recalls, and the one an install that could not fetch its model ends on. Both are
read from install/self_check.py itself, so a page cannot quote words the script no longer prints."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "install"))
import self_check as sc  # noqa: E402

PAGES = ("README.md", "docs/GETTING_STARTED.md", "docs/ONBOARDING_GUIDE.md")


def verdicts():
    checks = sc.Checks()
    full = sc._alive(checks)[0]
    checks.fetch_first = "<command>"
    return full, sc._alive(checks)[0]


class EveryPageNamesBothVerdicts(unittest.TestCase):
    def test_saboteur_a_page_naming_one_verdict_names_the_other(self):
        full, keyword = verdicts()
        self.assertTrue(keyword.startswith("YOUR BRAIN IS ALIVE BY KEYWORD: "), keyword)
        for rel in PAGES:
            text = " ".join((ROOT / rel).read_text(encoding="utf-8").split())
            with self.subTest(page=rel):
                self.assertIn("YOUR BRAIN IS ALIVE", text, "the page this test is about")
                self.assertIn(keyword, text)

    def test_control_the_pages_still_name_the_full_verdict(self):
        full, _ = verdicts()
        named = [rel for rel in PAGES if full in " ".join((ROOT / rel).read_text(encoding="utf-8").split())]
        self.assertTrue(named, "the full verdict is quoted somewhere")


if __name__ == "__main__":
    unittest.main()

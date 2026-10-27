# -*- coding: utf-8 -*-
"""THE THINKER'S PROGRAM, AS CATALOG DATA (Gemini-thinker Round 1, lane L-I; SYNTHESIS R3 + R4).

  R4  every CLI backend that names a program carries the vendor's own install command in thinkers.json `get`
      {unix, windows, doc_url, verified_at} -- never sudo, an https doc page, a dated check -- and the guide's
      "Get the program first" table quotes exactly those commands (a typed copy that drifts is RED here).
  R3  the a-f menu: d) Gemini, e) Not now kept WORD FOR WORD (owner-approved), f) Other; every backend's letter is the
      letter of the option that answers it; the names are the documented non-interactive form; the one-release
      notes for d and f exist.
Unit-level: no installer runs, nothing is installed, no network."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
import thinker  # noqa: E402
import thinker_get  # noqa: E402

CATALOG = ROOT / "organs" / "thinkers.json"
GUIDE = ROOT / "docs" / "OVERNIGHT_THINKER_SETUP.md"
NOT_NOW = "Not now — no model; keep the simple nightly routines"     # owner-approved 2026-09-26 (#21947): FIXED


def _cat():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


class TheMenu(unittest.TestCase):

    def test_the_six_options_in_order_with_e_not_now_unchanged(self):
        q = _cat()["question"]
        self.assertEqual([o[0] for o in q["options"]], list("abcdef"))
        self.assertEqual([o[2] for o in q["options"]], ["claude", "codex", "kimi", "gemini", "off", "local"])
        self.assertEqual(q["options"][4][1], NOT_NOW, "e) Not now keeps its owner-approved words")
        self.assertEqual(q["options"][3][1], "Gemini (Google; runs through Google's Antigravity program)")
        self.assertEqual(q["options"][5][1], "Other (which model?)")
        self.assertNotIn("gemini-cli", json.dumps(q), "agy only, never the deprecated npm gemini CLI")

    def test_every_backend_letter_is_the_letter_of_the_option_that_answers_it(self):
        cat = _cat()
        by_answer = {a: l for l, _w, a in cat["question"]["options"]}
        for bid, e in cat["backends"].items():
            answer = "local" if bid.startswith("local-") else bid
            self.assertEqual(e["letter"], by_answer[answer], bid)

    def test_names_are_the_documented_form_and_the_changed_letters_carry_a_note(self):
        q = _cat()["question"]
        self.assertEqual(q["names"], "--thinker claude|codex|kimi|gemini|off|local")
        self.assertIn(q["names"], q["agent_rule"])
        self.assertNotRegex(q["agent_rule"], r"<a\|b\|c")
        self.assertEqual(sorted(q["letter_notes"]), ["d", "f"])
        self.assertIn("--thinker local", q["letter_notes"]["d"], "the old d) Other's new name is named")
        self.assertIn("--thinker local", q["letter_notes"]["f"])


class TheGetField(unittest.TestCase):

    def test_every_cli_backend_that_names_a_program_carries_a_valid_get(self):
        cat = thinker.load_catalog()
        named = [b for b, e in cat["backends"].items() if e.get("transport") == "cli" and (e.get("binary") or {}).get("names")]
        self.assertTrue({"claude", "codex", "kimi"} <= set(named), named)
        bad = [p for b in named for p in thinker_get.get_problems(b, cat["backends"][b])]
        # after the merge with lane L-G this also covers backends.gemini (L-G carries its `get`, cdbd621): a CLI
        # backend added later without one is RED here, by design
        self.assertEqual(bad, [])

    def test_the_check_refuses_sudo_a_bare_url_and_an_undated_check(self):
        e = {"transport": "cli", "binary": {"names": ["x"]},
             "get": {"unix": "curl -fsSL https://x.example/i.sh | sudo bash", "windows": "irm https://x | iex",
                     "doc_url": "http://x.example", "verified_at": "soon"}}
        bad = " ".join(thinker_get.get_problems("x", e))
        for want in ("'sudo'", "https://", "verified_at"):
            self.assertIn(want, bad)
        e["get"] = {"unix": "curl https://x | bash; rm -rf ~", "windows": "w", "doc_url": "https://x", "verified_at": "2026-10-16"}
        self.assertIn("'rm'", " ".join(thinker_get.get_problems("x", e)))
        self.assertEqual(thinker_get.get_problems("x", {"transport": "cli", "binary": {"names": ["x"]}}),
                         ["x: no `get` field (the vendor's official install command; organs/thinker_get.py)"])
        self.assertEqual(thinker_get.get_problems("local-command", _cat()["backends"]["local-command"]), [])

    def test_the_guide_quotes_the_catalogs_commands_exactly(self):
        cat = _cat()
        g = GUIDE.read_text(encoding="utf-8")
        table = g[g.index("### Get the program first"):g.index("## 3. Before you start")]
        rows = {}
        for line in table.splitlines():
            m = re.match(r"\| ([a-f])\) \w+ \|[^|]*\| `([^`]*)`", line.replace("\\|", "│"))
            if m:
                rows[m.group(1)] = m.group(2).replace("│", "|")
        by_letter = {e["letter"]: (b, e) for b, e in cat["backends"].items() if e.get("get")}
        for letter, (bid, e) in by_letter.items():
            self.assertEqual(rows.get(letter), e["get"]["unix"], "the guide's table drifted from the catalog: %s" % bid)
            self.assertIn("`%s`" % e["get"]["unix"], g.split("## 4. Part A")[1],
                          "the Part for %s does not quote its command" % bid)
        self.assertIn("d", rows, "the table names Gemini's program")

    def test_the_missing_cause_names_the_cause_and_the_command(self):
        cat = thinker.load_catalog()
        for bid in ("claude", "codex", "kimi"):
            c = thinker_get.missing_cause(cat, bid)
            self.assertTrue(c.startswith("  cause: not-installed\n"), c)
            self.assertIn(cat["backends"][bid]["get"]["unix"], c)

    def test_a_platform_line_refuses_and_supported_does_not(self):
        e = {"platforms": {"linux": "supported", "darwin": "Gemini is not yet available on Mac: (test)"}}
        real = sys.platform
        f = getattr(thinker, "platform_refusal", None)
        try:
            if f is not None:
                del thinker.platform_refusal          # the module's own fallback reading, then the seam's
            for plat, want in (("linux", ""), ("darwin", "Gemini is not yet available on Mac: (test)")):
                sys.platform = plat
                self.assertEqual(thinker_get.platform_refusal(e), want, plat)
            sys.platform = "darwin"
            self.assertEqual(thinker_get.platform_refusal({}), "", "no platforms = offered everywhere")
        finally:
            sys.platform = real
            if f is not None:
                thinker.platform_refusal = f
        if f is not None:
            self.assertEqual(thinker_get.platform_refusal(e), f(e), "one reading: the seam's")

    def test_os_key(self):
        self.assertEqual(thinker_get.os_key("win32"), "windows")
        self.assertEqual(thinker_get.os_key("darwin"), "unix")
        self.assertEqual(thinker_get.os_key("linux"), "unix")


class TheGeminiScreens(unittest.TestCase):
    """KAEL-EM's screens for d) -- they need lane L-G's backends.gemini; before the merge they SKIP, loudly."""

    def setUp(self):
        self.cat = thinker.load_catalog()
        if "gemini" not in self.cat["backends"]:
            self.skipTest("SKIP (loud): backends.gemini is lane L-G's -- these run after the L-G + L-I merge")

    def test_the_sign_in_screen_names_its_own_home_the_untouched_login_the_model_and_training(self):
        lines = "\n".join(thinker_get.signin_lines(self.cat, "gemini", "/x/agy"))
        for want in ("Found Antigravity at /x/agy.", "/gemini-home", "Your everyday Gemini sign-in is not touched.",
                     "Google may use these conversations to improve its products.", "no bill",
                     self.cat["backends"]["gemini"]["model"]["pin"]):
            self.assertIn(want, lines)
        self.assertNotIn("{", lines, "a placeholder was left unfilled")

    def test_the_offer_screen_quotes_googles_command_and_mac_is_refused(self):
        c = thinker_get.check(self.cat, "gemini")
        c["found"] = ""
        s = "\n".join(thinker_get.screen_lines(c, "Gemini"))
        self.assertIn("Gemini needs Google's Antigravity program; it is not on this computer.", s)
        self.assertIn("    curl -fsSL https://antigravity.google/cli/install.sh | bash", s)
        self.assertIn("No password, no admin rights.", s)
        mac = (self.cat["backends"]["gemini"].get("platforms") or {}).get("darwin", "")
        self.assertTrue(mac.startswith("Gemini is not yet available on Mac"), mac)


if __name__ == "__main__":
    unittest.main()

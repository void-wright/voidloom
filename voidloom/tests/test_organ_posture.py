"""The overnight organs' posture RESTRICTS, carries no ambient instructions, and cannot eat the prompt -- as CATALOG DATA.

Measured on the reference host, 2026-09-26 (claude 2.1.283), from a unit's own cwd (~/.claude/memory), its real
config dir and the timers' env file, reading the stream-json `system/init` event rather than the model's account of
itself:
  * the shipped flags OFFERED 23 tools (RemoteTrigger, SendMessage, PushNotification, CronCreate, Task, Workflow, ...);
    --allowedTools only pre-approves; --tools is the restriction. With it: exactly the five named.
  * 7 hooks fired into every organ call, and the ancestor ~/.claude/CLAUDE.md imported the session doctrine;
    --safe-mode: 0 hook events, a clean one-turn reply.
  * --tools is VARIADIC: placed last, it swallows a trailing prompt and `claude -p` exits 1 (#21971).

Model-choice Round 1 (M3) moved the posture out of code into organs/thinkers.json, BY PURPOSE: the seven synthesis
organs are TEXT ONLY (Claude `--tools ""`, measured CT3: init tools []), gap research alone gets web search and
fetch (WEB ONLY since the owner's ruling of 2026-09-27; no file tools). Bound here:
  * the Claude entry carries --safe-mode in its argv, and each posture's --tools list equals what that purpose may use;
  * every variadic flag is followed by its value and then another flag, so no prompt can be read as a tool name;
  * least_privilege_flags() -- kept for callers outside the package -- is DERIVED from the catalog, not a copy;
  * the codex postures are the Round-0 hardened sets (XT3 text, XT2 research) and kimi's are agent files.
At 0b061d7 there is no catalog and every organ carried the research set (web + Read) for text jobs: red.
"""
import json
import unittest
from pathlib import Path

import least_privilege

ORGANS = Path(least_privilege.__file__).resolve().parent
VARIADIC = ("--disallowedTools", "--allowedTools", "--tools", "--add-dir")
RESEARCH_TOOLS = ["WebSearch", "WebFetch"]      # WEB ONLY: the owner's ruling 2026-09-27 (M3, ruler VL-MC-v1.1)
XT3 = ["-s", "read-only", "--disable", "shell_tool", "--disable", "unified_exec", "--disable", "apps",
       "--disable", "plugins", "--disable", "browser_use", "--disable", "computer_use", "--disable",
       "image_generation", "--disable", "multi_agent", "--disable", "in_app_browser", "--disable", "goals",
       "--disable", "memories", "--disable", "tool_suggest", "--disable", "workspace_dependencies",
       "-c", 'web_search="disabled"']


def _cat():
    return json.loads((ORGANS / "thinkers.json").read_text(encoding="utf-8"))


class OrganPostureRestricts(unittest.TestCase):

    def test_text_organs_are_offered_no_tools(self):
        post = _cat()["backends"]["claude"]["posture"]["text"]
        self.assertEqual(post.count("--tools"), 1, post)
        i = post.index("--tools")
        self.assertEqual(post[i + 1], "", "the synthesis organs are TEXT ONLY: --tools \"\" (CT3: init tools [])")
        self.assertEqual(post[i + 2], "--permission-mode", "--tools must precede a single-valued flag")
        self.assertNotIn("--allowedTools", post)

    def test_research_is_restricted_to_the_web(self):
        post = _cat()["backends"]["claude"]["posture"]["research"]
        i = post.index("--tools")
        self.assertEqual(post[i + 1].split(","), RESEARCH_TOOLS)
        self.assertEqual(post[i + 2], "--permission-mode")
        deny = post[post.index("--disallowedTools") + 1].split(",")
        self.assertEqual(deny[0], "mcp__*")
        for t in ("Bash", "Write", "Edit", "NotebookEdit", "Read", "Glob", "Grep"):
            self.assertIn(t, deny)

    def test_no_ambient_instructions(self):
        claude = _cat()["backends"]["claude"]
        self.assertIn("--safe-mode", claude["argv"])
        self.assertNotIn("--bare", claude["argv"], "--bare refuses OAuth (#21972): not for a subscription token")
        self.assertIn("CLAUDE_CONFIG_DIR", claude["hook_free_env"])
        self.assertEqual(least_privilege.least_privilege_flags()[0], "--safe-mode")

    def test_no_variadic_flag_can_swallow_a_prompt(self):
        for name, flags in (("text", _cat()["backends"]["claude"]["posture"]["text"]),
                            ("research", _cat()["backends"]["claude"]["posture"]["research"]),
                            ("least_privilege_flags", least_privilege.least_privilege_flags())):
            with self.subTest(posture=name):
                for i, f in enumerate(flags):
                    if f in VARIADIC:
                        self.assertLess(i + 2, len(flags), f"{f} is last-but-one: {flags}")
                        self.assertFalse(flags[i + 1].startswith("--"), f"{f} has no value")
                        self.assertTrue(flags[i + 2].startswith("--"), f"{f}'s value is followed by a non-flag")
                self.assertNotIn(flags[-2], VARIADIC, "the list must END on a single-valued flag")

    def test_least_privilege_is_derived_from_the_catalog(self):
        self.assertEqual(least_privilege.least_privilege_flags()[1:],
                         _cat()["backends"]["claude"]["posture"]["research"])
        self.assertEqual(least_privilege.ALLOW_TOOLS, RESEARCH_TOOLS)

    def test_codex_and_kimi_postures_are_the_round0_sets(self):
        cat = _cat()["backends"]
        self.assertEqual(cat["codex"]["posture"]["text"], XT3)
        self.assertEqual(cat["codex"]["posture"]["research"], XT3[:-2], "XT2 = XT3 without web_search disabled")
        self.assertIn("--disable", cat["codex"]["argv"])
        self.assertEqual(cat["codex"]["argv"][cat["codex"]["argv"].index("--disable") + 1], "hooks")
        self.assertEqual(cat["kimi"]["posture"]["text"]["tools"], [])
        self.assertEqual(cat["kimi"]["posture"]["research"]["disallowedTools"], ["Bash", "Write", "Edit", "Read", "Glob", "Grep"])
        self.assertEqual(cat["kimi"]["posture"]["research"]["tools"], ["WebSearch", "FetchURL"])
        for bid, e in cat.items():
            flat = json.dumps(e.get("argv", [])) + json.dumps(e.get("posture", {}))
            for banned in ("--auto", "--yolo", "--dangerously"):
                self.assertNotIn(banned, flat, "%s carries %s" % (bid, banned))


if __name__ == "__main__":
    unittest.main()

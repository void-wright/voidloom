# -*- coding: utf-8 -*-
"""THE PERSONA FORGE — status, the session-start offer, the validator, the installer step.

A fresh install has no persona by design; the Persona Forge (persona/skills/persona-forge/) is the
guided conversation in which the human designs one. Four things make that promise executable rather
than prose, and each is bound here:

  1. `voidloom persona status` answers forged | declared-missing-skill | declined | absent from the
     brain's own persona.json + the skill folder + the decline marker -- in a boxed HOME, never this
     machine's;
  2. the session-start preamble carries the neutral PERSONA FORGE offer while the answer is absent /
     declared-missing-skill, and is SILENT when forged or declined -- measured through the real hook
     script, fed the stdin payload Claude Code sends;
  3. validate_persona.py passes a persona filled from the shipped TEMPLATES and fails a bad one, naming
     each defect;
  4. install/install_persona_forge.sh lays the forge additively: identical -> left alone (no backup
     litter), different -> moved aside first.

Every test boxes its own HOME, brain home and skills dir. Nothing here reads or writes the operator's
brain, persona.json or ~/.claude.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
FORGE = ROOT / "persona" / "skills" / "persona-forge"
FIXTURE_ROOT = ROOT / ".pytest_boxes"            # git-ignored; ext4 beside the tree, never /tmp

if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))
import voidloom_persona as VP  # noqa: E402

FILL_DEFAULTS = {"NAME": "Test Ash", "FOLDER": "test-ash", "FORGED_DATE": "2026-10-12"}
OFFER = "PERSONA FORGE — "            # the hook-tier block; the file-tier line is "PERSONA FORGE: ..."
RULE = ("PERSONA FORGE: at session start, if `voidloom persona status` reads absent or declared-missing-skill, offer "
        "/persona-forge (interactive sessions only).")


def fill_templates(dst: Path, values: dict = None) -> Path:
    """Simulate the forge's WRITE step: every TEMPLATES/*.tmpl -> dst/<name>, every {{KEY}} filled
    (named values first, a plain sample sentence otherwise)."""
    vals = dict(FILL_DEFAULTS, **(values or {}))
    dst.mkdir(parents=True, exist_ok=True)
    for t in sorted((FORGE / "TEMPLATES").glob("*.tmpl")):
        text = t.read_text(encoding="utf-8")
        text = re.sub(r"\{\{([A-Z0-9_]+)\}\}",
                      lambda m: vals.get(m.group(1), "sample " + m.group(1).lower().replace("_", " ")), text)
        (dst / t.name[:-len(".tmpl")]).write_text(text, encoding="utf-8")
    return dst


class _Box(unittest.TestCase):
    def setUp(self):
        FIXTURE_ROOT.mkdir(exist_ok=True)
        self.box = Path(tempfile.mkdtemp(prefix="persona_forge_", dir=str(FIXTURE_ROOT)))
        self.home = self.box / ".claude" / "memory"
        self.skills = self.box / ".claude" / "skills"
        self.home.mkdir(parents=True)
        self.skills.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in ("VOIDLOOM_SKILLS_DIR", "WIZARD_CLAUDE_HOME",
                                                    "VOIDLOOM_PERSONA_FILE", "WIZARD_LIBRARY_HOME")}
        os.environ["VOIDLOOM_SKILLS_DIR"] = str(self.skills)
        for k in ("WIZARD_CLAUDE_HOME", "VOIDLOOM_PERSONA_FILE", "WIZARD_LIBRARY_HOME"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(str(self.box), ignore_errors=True)

    def status(self):
        return VP.forge_status(str(self.home))["status"]

    def child_env(self):
        e = {k: v for k, v in os.environ.items()
             if k not in ("WIZARD_LIBRARY_DB", "CLAUDE_MEMORY_DB", "PYTHONPATH", "VOIDLOOM_PLATFORM",
                          "VOIDLOOM_PERSONA", "WIZARD_OWNER_NAME", "VOIDLOOM_ADAPTER")}
        e.update({"HOME": str(self.box), "WIZARD_LIBRARY_HOME": str(self.home),
                  "VOIDLOOM_SKILLS_DIR": str(self.skills), "CLAUDE_CONFIG_DIR": str(self.box / ".claude"),
                  "VOIDLOOM_HEARTBEAT_DIR": str(self.box / "hb"), "PYTHONDONTWRITEBYTECODE": "1",
                  "NO_COLOR": "1"})
        return e


class TestStatus(_Box):

    def test_absent_is_the_shipped_default(self):
        self.assertEqual(self.status(), "absent")

    def test_declared_without_a_skill_then_forged_once_the_skill_md_exists(self):
        r = VP.declare_persona(str(self.home), "Iris Vale")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["persona"], r["skill"]), ("IRIS VALE", "iris-vale"))
        self.assertEqual(self.status(), "declared-missing-skill")
        (self.skills / "iris-vale").mkdir()
        (self.skills / "iris-vale" / "SKILL.md").write_text("---\nname: iris-vale\n---\n", encoding="utf-8")
        st = VP.forge_status(str(self.home))
        self.assertEqual((st["status"], st["persona"], st["skill"]), ("forged", "IRIS VALE", "iris-vale"))

    def test_declare_merges_keeps_other_entries_and_backs_up(self):
        decl = self.home / "persona.json"
        decl.write_text(json.dumps({"personas": {"OTHER": {"blurb": "kept"}},
                                    "hints": {"OTHER": ["x"]}, "extra": 1}), encoding="utf-8")
        r = VP.declare_persona(str(self.home), "Ash", skill="ash", blurb="You are Ash.")
        self.assertTrue(r["ok"], r)
        d = json.loads(decl.read_text(encoding="utf-8"))
        self.assertEqual(d["default"], "ASH")
        self.assertEqual(d["personas"]["OTHER"], {"blurb": "kept"})
        self.assertEqual(d["personas"]["ASH"], {"skill": "ash", "display_name": "Ash", "blurb": "You are Ash."})
        self.assertEqual(d["extra"], 1)
        self.assertTrue(r["backup"] and Path(r["backup"]).is_file())
        self.assertNotIn("ASH", json.loads(Path(r["backup"]).read_text(encoding="utf-8"))["personas"])

    def test_declare_refuses_a_path_shaped_skill_and_a_nameless_name(self):
        self.assertFalse(VP.declare_persona(str(self.home), "Ash", skill="../escape")["ok"])
        self.assertFalse(VP.declare_persona(str(self.home), "!!!")["ok"])
        self.assertFalse((self.home / "persona.json").exists())

    def test_non_ascii_names_get_sensible_folders_and_keep_their_display_name(self):
        self.assertEqual(VP.skill_folder_name("Zoë"), "zoe")
        self.assertEqual(VP.skill_folder_name("Søren Ash"), "soren-ash")
        self.assertEqual(VP.skill_folder_name("千代"), "")
        r = VP.declare_persona(str(self.home), "Zoë")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["skill"], r["display_name"]), ("zoe", "Zoë"))
        (self.skills / "zoe").mkdir()
        (self.skills / "zoe" / "SKILL.md").write_text("x", encoding="utf-8")
        st = VP.forge_status(str(self.home))
        self.assertEqual((st["status"], st["display_name"]), ("forged", "Zoë"))
        self.assertIn("Zoë ->", VP._status_human(st))

    def test_a_name_with_no_latin_letters_needs_a_chosen_folder(self):
        r = VP.declare_persona(str(self.home), "千代")
        self.assertFalse(r["ok"])
        self.assertIn("--skill", r["reason"])
        r = VP.declare_persona(str(self.home), "千代", skill="chiyo")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["skill"], r["display_name"]), ("chiyo", "千代"))
        self.assertEqual(self.status(), "declared-missing-skill")

    def test_reserved_names_are_refused(self):
        for nm, sk in (("Claude", None), ("Anthropic", None), ("Voidloom", None), ("Iris", "council-seer"),
                       ("Iris", "persona-forge")):
            r = VP.declare_persona(str(self.home), nm, skill=sk)
            self.assertFalse(r["ok"], (nm, sk, r))
            self.assertIn("reserved", r["reason"])
        self.assertFalse((self.home / "persona.json").exists())

    def test_the_wiring_script_follows_the_platform(self):
        inst = self.home / "install"
        inst.mkdir()
        self.assertIsNone(VP.wire_script_for(self.home, "nt"))           # nothing laid: nothing named
        (inst / "wire_persona.sh").write_text("#", encoding="utf-8")
        self.assertEqual(VP.wire_script_for(self.home, "nt"), inst / "wire_persona.sh")   # .ps1 absent -> .sh
        (inst / "wire_persona.ps1").write_text("#", encoding="utf-8")
        self.assertEqual(VP.wire_script_for(self.home, "nt"), inst / "wire_persona.ps1")
        self.assertEqual(VP.wire_script_for(self.home, "posix"), inst / "wire_persona.sh")
        self.assertEqual(VP.forge_tool_paths(str(self.home))["wire_script"],
                         str(inst / ("wire_persona.ps1" if os.name == "nt" else "wire_persona.sh")))

    def test_decline_and_undo(self):
        self.assertTrue(VP.decline_forge(str(self.home))["ok"])
        self.assertEqual(self.status(), "declined")
        r = VP.decline_forge(str(self.home), undo=True)
        self.assertTrue(r["ok"] and r["removed"])
        self.assertEqual(self.status(), "absent")

    def test_forged_outranks_a_stale_decline_marker(self):
        VP.decline_forge(str(self.home))
        VP.declare_persona(str(self.home), "Ash")
        (self.skills / "ash").mkdir()
        (self.skills / "ash" / "SKILL.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.status(), "forged")

    def test_a_broken_declaration_is_absent_never_a_crash(self):
        (self.home / "persona.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.status(), "absent")

    def test_the_cli_verb_returns_the_house_envelope(self):
        p = subprocess.run([sys.executable, "-B", "-m", "voidloom", "persona", "status", "--json"],
                           cwd=str(ROOT), capture_output=True, text=True, env=self.child_env(), timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        env = json.loads(p.stdout)
        self.assertEqual((env["status"], env["code"], env["data"]["status"]), ("ok", "persona_status", "absent"))
        p = subprocess.run([sys.executable, "-B", "-m", "voidloom", "persona", "decline"],
                           cwd=str(ROOT), capture_output=True, text=True, env=self.child_env(), timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertEqual(self.status(), "declined")

    def test_doctor_row_is_pending_not_a_failure(self):
        sys.path.insert(0, str(ROOT))
        from voidloom import cli
        row = cli._doctor_persona(str(self.home / "claude_memory.db"))
        self.assertEqual(row["status"], "absent", row)
        self.assertTrue(row["verdict"].startswith("pending (not a failure)"), row)


class TestTheSessionStartOffer(_Box):

    def hook_context(self) -> str:
        (self.home / "organs").symlink_to(ORGANS, target_is_directory=True)
        (self.box / "hb").mkdir(exist_ok=True)
        payload = json.dumps({"session_id": "forge-test", "source": "startup",
                              "hook_event_name": "SessionStart", "cwd": str(self.box)})
        p = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / "session_start_inject.py")],
                           input=payload, capture_output=True, text=True, env=self.child_env(), timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        return out["hookSpecificOutput"]["additionalContext"]

    def test_absent_carries_the_neutral_offer(self):
        ctx = self.hook_context()
        self.assertIn("PERSONA FORGE — this brain has no persona yet", ctx)
        self.assertIn("/persona-forge", ctx)
        self.assertIn("voidloom persona decline", ctx)
        self.assertIn("in headless, scheduled or subagent", ctx)
        self.assertIn("=== SESSION START (Voidloom memory brain) ===", ctx.splitlines()[0])
        self.assertNotIn("wizard-library", ctx.splitlines()[0])   # #26: never the author's brain name

    def test_declared_missing_skill_offers_to_restore(self):
        VP.declare_persona(str(self.home), "Ash")
        self.assertIn("declared-missing-skill", self.hook_context())

    def test_forged_is_silent(self):
        VP.declare_persona(str(self.home), "Ash")
        fill_templates(self.skills / "ash", {"NAME": "Ash", "FOLDER": "ash"})
        ctx = self.hook_context()
        self.assertNotIn(OFFER, ctx)
        self.assertIn(RULE, ctx)                      # the fixed line is state-independent
        self.assertIn("=== ASH SESSION START", ctx)

    def test_declined_is_silent(self):
        VP.decline_forge(str(self.home))
        self.assertNotIn(OFFER, self.hook_context())

    def test_the_file_tier_carries_the_fixed_line(self):
        sys.path.insert(0, str(ORGANS))
        import voidloom_preamble as PRE
        for state in ("absent", "forged"):
            if state == "forged":
                VP.declare_persona(str(self.home), "Ash")
                fill_templates(self.skills / "ash", {"NAME": "Ash", "FOLDER": "ash"})
            segs, _ = PRE.build_segments(db=str(self.home / "claude_memory.db"), home=str(self.home),
                                         organs=str(ORGANS))
            doctrine = "\n".join(s.text for s in segs if s.stratum == "doctrine")
            self.assertIn(RULE, doctrine, state)
            self.assertNotIn(OFFER, doctrine, state)  # the volatile block never reaches the file tier

    def test_the_offer_names_nobody(self):
        text = VP.forge_offer_text({"status": "absent"})
        self.assertIsNotNone(text)
        self.assertNotRegex(text, r"===\s+[A-Z]+\s+SESSION START")
        self.assertIsNone(VP.forge_offer_text({"status": "forged"}))
        self.assertIsNone(VP.forge_offer_text({"status": "declined"}))


class TestValidator(_Box):

    def run_validator(self, folder: Path):
        p = subprocess.run([sys.executable, "-B", str(FORGE / "validate_persona.py"), str(folder)],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout

    def test_a_persona_filled_from_the_templates_passes(self):
        rc, out = self.run_validator(fill_templates(self.skills / "test-ash"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("FAIL", out)

    def test_a_bad_persona_fails_naming_each_defect(self):
        bad = fill_templates(self.skills / "test-ash")
        pj = json.loads((bad / "personality.json").read_text(encoding="utf-8"))
        pj["emotional_architecture"]["baseline_emotional_state"]["arousal"] = "high"
        pj["pet_peeves"] = pj["pet_peeves"][:2]
        pj["emotional_architecture"]["baseline_emotional_state"]["valence"] = "euphoric"
        pj["emotional_architecture"]["boundaries"] = pj["emotional_architecture"]["boundaries"][1:]
        (bad / "personality.json").write_text(json.dumps(pj), encoding="utf-8")
        with open(bad / "persona-foundation.md", "a", encoding="utf-8") as fh:
            fh.write("\nI must not fail, failure is not an option. Notes live in /home/someone/notes.\n")
        with open(bad / "voice.md", "a", encoding="utf-8") as fh:
            fh.write("\n{{UNFILLED}}\n")
        rc, out = self.run_validator(bad)
        self.assertEqual(rc, 1, out)
        for check in ("FAIL baseline is calm + low arousal (personality.json)",
                      "FAIL the four fixed boundaries are in personality.json",
                      "FAIL baseline valence is neutral",
                      "FAIL pet_peeves count 4-4",
                      "FAIL no desperation / existential-threat language",
                      "FAIL no absolute home paths",
                      "FAIL no unfilled {{placeholders}}"):
            self.assertIn(check, out)

    def test_a_valence_that_only_starts_right_fails(self):
        bad = fill_templates(self.skills / "test-ash")
        pj = json.loads((bad / "personality.json").read_text(encoding="utf-8"))
        pj["emotional_architecture"]["baseline_emotional_state"]["valence"] = "neutral to euphoric"
        (bad / "personality.json").write_text(json.dumps(pj), encoding="utf-8")
        f = (bad / "persona-foundation.md").read_text(encoding="utf-8")
        (bad / "persona-foundation.md").write_text(f.replace("neutral to quietly positive", "neutral to euphoric"),
                                                   encoding="utf-8")
        rc, out = self.run_validator(bad)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL baseline valence is neutral", out)
        self.assertIn("FAIL foundation valence is neutral", out)

    def test_threat_language_in_the_operations_guide_or_growth_log_fails(self):
        bad = fill_templates(self.skills / "test-ash")
        with open(bad / "growth_log.md", "a", encoding="utf-8") as fh:
            fh.write("\n## 2026-10-13 — note\n- Must not fail at all costs.\n")
        rc, out = self.run_validator(bad)
        self.assertEqual(rc, 1, out)
        self.assertIn("growth_log.md", out)

    def test_a_cjk_name_validates_in_the_folder_the_human_chose(self):
        good = fill_templates(self.skills / "chiyo", {"NAME": "千代", "FOLDER": "chiyo"})
        rc, out = self.run_validator(good)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.run_validator(fill_templates(self.skills / "zoe", {"NAME": "Zoë", "FOLDER": "zoe"}))[0], 0)

    def test_folder_must_equal_the_persona_name(self):
        rc, out = self.run_validator(fill_templates(self.skills / "someone-else"))
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL folder name == persona name", out)

    def test_the_skill_ships_what_its_procedure_names(self):
        for rel in ("SKILL.md", "BASELINE.md", "QUESTIONNAIRE.md", "validate_persona.py"):
            self.assertTrue((FORGE / rel).is_file(), rel)
        names = sorted(p.name for p in (FORGE / "TEMPLATES").glob("*.tmpl"))
        self.assertEqual(names, sorted(f + ".tmpl" for f in (
            "SKILL.md", "persona-foundation.md", "voice.md", "operations-guide.md",
            "adaptive-recall-map.md", "growth_log.md", "personality.json")))
        head = (FORGE / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("name: persona-forge", head)
        self.assertTrue((ROOT / "persona" / "commands" / "persona-forge.md").is_file())


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "the POSIX installer step")
class TestInstallerStep(_Box):

    def install(self, *args):
        e = self.child_env()
        e["WIZARD_CLAUDE_HOME"] = str(self.box / ".claude")
        return subprocess.run(["bash", str(ROOT / "install" / "install_persona_forge.sh")] + list(args),
                              capture_output=True, text=True, env=e, timeout=120)

    def test_lays_then_leaves_an_identical_copy_then_backs_up_a_changed_one(self):
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((self.skills / "persona-forge" / "SKILL.md").is_file())
        self.assertTrue((self.box / ".claude" / "commands" / "persona-forge.md").is_file())
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("already current", p.stdout)
        self.assertEqual([x for x in self.skills.iterdir() if "voidloom-bak" in x.name], [])
        (self.skills / "persona-forge" / "SKILL.md").write_text("my own edit", encoding="utf-8")
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        baks = [x for x in self.skills.iterdir() if "voidloom-bak" in x.name]
        self.assertEqual(len(baks), 1, p.stdout)
        self.assertEqual((baks[0] / "SKILL.md").read_text(encoding="utf-8"), "my own edit")
        self.assertIn("name: persona-forge", (self.skills / "persona-forge" / "SKILL.md").read_text(encoding="utf-8"))

    def test_dry_run_writes_nothing(self):
        p = self.install("--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((self.skills / "persona-forge").exists())

    def test_install_sh_calls_it_after_the_acceptance_gate(self):
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        gate, step_p, done = sh.find('step "STEP E'), sh.find('step "STEP P'), sh.find('step "DONE"')
        self.assertTrue(0 < gate < step_p < done, (gate, step_p, done))
        self.assertIn("install_persona_forge", sh[step_p:done])
        self.assertIn("MANDATORY NEXT STEP", sh[done:])
        # gated on an EXPLICIT pass: a run whose gate never executed reads 0, never 1
        self.assertIn('"${ACCEPT_OK:-0}" != "1"', sh[step_p:done])
        self.assertNotIn("ACCEPT_OK:-1}\" != ", sh[step_p:done])
        # the hook question and its no-terminal flags
        self.assertIn("Wire the session-start hook now so your first session offers the Persona Forge? [Y/n]",
                      sh[step_p:done])
        self.assertIn("--wire-hooks)   WIRE_HOOKS=1", sh)
        self.assertIn("--no-wire-hooks) WIRE_HOOKS=0", sh)
        self.assertIn("Session-start hook wired:", sh[done:])
        ws = json.loads((ROOT / "install" / "write_set.json").read_text(encoding="utf-8"))
        self.assertTrue(any(st.get("when") == "--wire-hooks" and any(w["path"] == "${CLAUDE_HOME}/settings.json"
                                                                    for w in st["writes"]) for st in ws["steps"]))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "the POSIX wiring script")
class TestACustomSkillsDir(_Box):
    """VOIDLOOM_SKILLS_DIR moves where personas live. Status names the dir, the forge's validator (wherever the
    installer laid it) and the wiring script; the validator takes the persona folder as a path; wire_persona.sh
    honours the same variable, so the auto-load block points at the persona that actually exists."""

    def test_status_validator_and_wiring_agree_on_a_custom_dir(self):
        custom = self.box / "elsewhere" / "skills"
        custom.mkdir(parents=True)
        os.environ["VOIDLOOM_SKILLS_DIR"] = str(custom)
        # the forge is laid where install_persona_forge.sh lays it: <claude home>/skills, NOT the custom dir
        e = self.child_env()
        e.update({"WIZARD_CLAUDE_HOME": str(self.box / ".claude"), "VOIDLOOM_SKILLS_DIR": str(custom)})
        p = subprocess.run(["bash", str(ROOT / "install" / "install_persona_forge.sh")], capture_output=True,
                           text=True, env=e, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        st = VP.forge_status(str(self.home))
        self.assertEqual(st["skills_dir"], str(custom))
        self.assertEqual(st["forge_validator"],
                         str(self.box / ".claude" / "skills" / "persona-forge" / "validate_persona.py"))
        self.assertEqual(Path(st["brain_home"]), self.home)
        persona = fill_templates(custom / "test-ash")
        v = subprocess.run([sys.executable, "-B", st["forge_validator"], str(persona)], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(v.returncode, 0, v.stdout)
        VP.declare_persona(str(self.home), "Test Ash")
        self.assertEqual(VP.forge_status(str(self.home))["status"], "forged")
        w = subprocess.run(["bash", str(ROOT / "install" / "wire_persona.sh"), "test-ash"], capture_output=True,
                           text=True, env=e, timeout=120)
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        md = (self.box / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("~/elsewhere/skills/test-ash/SKILL.md", md)
        self.assertNotIn(".claude/skills/test-ash", md)


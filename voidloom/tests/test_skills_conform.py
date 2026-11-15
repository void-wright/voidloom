"""Every skill folder the install lays into skills/ is a conforming Agent Skill: an entry file SKILL.md whose
frontmatter names the folder and says when to use it, and whose links resolve.

  * saboteur: a skill folder with no SKILL.md (memory-operator shipped two outlines and no entry file) fails;
  * saboteur: a name that is not the folder's, a missing or empty description, a name or description over the
    standard's bounds, or a link to a file that is not there fails;
  * control: the lay manifest lays each SKILL.md into skills/<folder>/SKILL.md.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "persona" / "skills"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def problems(folder: Path) -> list:
    f = folder / "SKILL.md"
    if not f.is_file():
        return [f"{folder.name}: no SKILL.md"]
    text = f.read_text(encoding="utf-8")
    fm, out = frontmatter(text), []
    if fm.get("name") != folder.name:
        out.append(f"{folder.name}: frontmatter name {fm.get('name')!r} is not the folder's")
    if not NAME.match(fm.get("name") or "") or len(fm.get("name") or "") > 64:
        out.append(f"{folder.name}: name is not lowercase-hyphenated within 64 characters")
    desc = fm.get("description") or ""
    if not desc or len(desc) > 1024:
        out.append(f"{folder.name}: description missing, empty or over 1024 characters")
    for target in re.findall(r"\]\(([^)#:\s]+)\)", text):
        if not (folder / target).exists():
            out.append(f"{folder.name}: link to {target} does not resolve")
    return out


class EverySkillConforms(unittest.TestCase):
    def test_saboteur_every_skill_folder_has_a_conforming_entry_file(self):
        folders = sorted(p for p in SKILLS.iterdir() if p.is_dir())
        self.assertIn("memory-operator", [p.name for p in folders])
        found = [x for p in folders for x in problems(p)]
        self.assertEqual(found, [])

    def test_saboteur_the_check_catches_each_fault(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "some-skill"
            d.mkdir()
            self.assertTrue(problems(d))                                         # no entry file
            ok = "---\nname: some-skill\ndescription: Use when testing.\n---\n\n[x](x.md)\n"
            (d / "x.md").write_text("x")
            (d / "SKILL.md").write_text(ok)
            self.assertEqual(problems(d), [])
            for bad in (ok.replace("name: some-skill", "name: other-skill"),
                        ok.replace("description: Use when testing.", "description:"),
                        ok.replace("name: some-skill", "name: Some_Skill"),
                        ok.replace("Use when testing.", "x" * 1025),
                        ok.replace("[x](x.md)", "[y](y.md)"),
                        ok.replace("---\nname", "name")):
                (d / "SKILL.md").write_text(bad)
                self.assertTrue(problems(d), bad[:60])

    def test_control_the_lay_manifest_lays_each_entry_file(self):
        lay = json.loads((ROOT / "install" / "lay_manifest.json").read_text(encoding="utf-8"))
        laid = {(e["root"], e["dst"]) for e in lay["entries"]}
        for folder in (p for p in SKILLS.iterdir() if p.is_dir()):
            self.assertIn(("skills", f"{folder.name}/SKILL.md"), laid, folder.name)


if __name__ == "__main__":
    unittest.main()

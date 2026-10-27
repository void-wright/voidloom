# -*- coding: utf-8 -*-
"""M1 -- ONE SEAM: no organ launches a model CLI itself; the counted eight think through organs/thinker.py.

Model-choice unit, Round 1 (ruler VL-MC-v1.0). The AST census at 524c63e (#21973) counted EIGHT launchers of
`claude -p` in organs/: seven synthesis organs through least_privilege_flags() -- arcane_manifest,
counterfactual_memory, dream_pipeline, memory_dreaming, monthly_self_eval, sleep_loop, whisper_network -- and
gap_mapping with flags of its own. This gate is an AST gate, never a grep: voidloom_instructions names `claude` in a
table of AGENTS.md readers, voidloom_hooks_status names platforms in tuples, and neither launches anything.

What counts as a LAUNCH (any vendor in claude, codex, kimi, ollama):
  (a) an argv literal: a list/tuple whose first element is the vendor and whose second is a flag ('-p', '--x') or
      a subcommand (exec, run, login, serve, app-server, chat, generate, pull);
  (b) `which('<vendor>')` -- locating the vendor's binary is the first half of launching it;
  (c) a subprocess/os call whose first argument is a shell string starting with the vendor's name;
  (d) an argv literal whose SECOND element is '-p' and whose first is not a constant (a binary held in a variable).
One declared exemption, with its reason: voidloom_hooks_status locates `codex` for the doctor's CONSENTED
--probe-platform (`codex app-server` hooks/list reads the chat agent's hook registration; no prompt, no model).

Red on 0b061d7 (the base): eight launchers, zero think() callers. The planted-launch test proves the gate can fail.
VOIDLOOM_ROOT points the gate at another tree (the cold gate runs it against the base to show the red).
"""
import ast
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))

VENDORS = ("claude", "codex", "kimi", "ollama")
SUBCOMMANDS = ("exec", "run", "login", "serve", "app-server", "chat", "generate", "pull")
SUBPROCESS_FNS = ("run", "Popen", "call", "check_call", "check_output", "system", "popen", "execv", "execvp",
                  "execvpe", "spawnv", "spawnvp")
#: the counted population (#21973): module -> the purpose it must pass to think()
POPULATION = {
    "arcane_manifest": "arcane_manifest", "counterfactual_memory": "counterfactual_memory",
    "dream_pipeline": "dream_pipeline", "memory_dreaming": "memory_dreaming",
    "monthly_self_eval": "monthly_self_eval", "sleep_loop": "sleep_loop", "whisper_network": "whisper_network",
    "gap_mapping": "gap_research",
}
EXEMPT = {
    ("voidloom_hooks_status", "which:codex"):
        "doctor --probe-platform (consented): `codex app-server` hooks/list reads hook registration, not a thinker call",
}
#: files outside organs/ that once launched a vendor CLI themselves and now go through the seam
EXTRA_FILES = ("tools/token_doctor.py",)


def _vendor(v):
    if not isinstance(v, str):
        return None
    base = v.replace("\\", "/").rsplit("/", 1)[-1]
    return base if base in VENDORS else None


def census_file(path):
    """{'launch': [(kind, lineno)], 'think': [(purpose, lineno, has_timeout)]} for one file."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    launch, think = [], []
    for n in ast.walk(tree):
        if isinstance(n, (ast.List, ast.Tuple)) and len(n.elts) >= 2:
            e0, e1 = n.elts[0], n.elts[1]
            if isinstance(e0, ast.Constant) and _vendor(e0.value) and isinstance(e1, ast.Constant) \
                    and isinstance(e1.value, str) and (e1.value.startswith("-") or e1.value in SUBCOMMANDS):
                launch.append(("argv:%s" % _vendor(e0.value), n.lineno))
            elif not isinstance(e0, ast.Constant) and isinstance(e1, ast.Constant) and e1.value == "-p":
                launch.append(("argv:<variable> -p", n.lineno))
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "which" and n.args and isinstance(n.args[0], ast.Constant) and _vendor(n.args[0].value):
                launch.append(("which:%s" % _vendor(n.args[0].value), n.lineno))
            if name in SUBPROCESS_FNS and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                head = n.args[0].value.strip().split(" ", 1)[0]
                if _vendor(head):
                    launch.append(("shell:%s" % _vendor(head), n.lineno))
            if name == "think":
                kw = {k.arg: k.value for k in n.keywords}
                purpose = kw.get("purpose")
                think.append((purpose.value if isinstance(purpose, ast.Constant) else None, n.lineno,
                              "timeout_s" in kw))
    return {"launch": launch, "think": think}


def census(root):
    root = Path(root)
    out = {}
    for p in sorted((root / "organs").glob("*.py")):
        if p.name == "thinker.py":
            continue
        out[p.stem] = census_file(p)
    for rel in EXTRA_FILES:
        if (root / rel).is_file():
            out[rel] = census_file(root / rel)
    return out


def verdict(root):
    """(problems, facts): the gate's red list for a tree, and what it counted."""
    c = census(root)
    problems = []
    launchers = {m: v["launch"] for m, v in c.items() if v["launch"]}
    for m, hits in sorted(launchers.items()):
        for kind, line in hits:
            if (m, kind) in EXEMPT:
                continue
            problems.append("%s:%d launches a model CLI itself (%s) -- it must go through thinker.think()"
                            % (m, line, kind))
    thinkers = {m: v["think"] for m, v in c.items() if v["think"]}
    for m, purpose in POPULATION.items():
        calls = thinkers.get(m, [])
        if not calls:
            problems.append("%s does not think through the seam (no think() call)" % m)
        for p, line, has_t in calls:
            if p != purpose:
                problems.append("%s:%d think(purpose=%r), expected %r" % (m, line, p, purpose))
            if not has_t:
                problems.append("%s:%d think() without timeout_s" % (m, line))
    extra = sorted(set(thinkers) - set(POPULATION) - set(EXTRA_FILES))
    for m in extra:
        problems.append("%s calls think() but is not in the counted population -- count it (and give its purpose "
                        "a posture in thinkers.json)" % m)
    found_exempt = {(m, k) for m, hits in launchers.items() for k, _l in hits if (m, k) in EXEMPT}
    for gone in sorted(set(EXEMPT) - found_exempt):
        problems.append("the exemption %s no longer matches anything -- remove it" % (gone,))
    facts = {"launchers": {m: [k for k, _l in h] for m, h in launchers.items()},
             "think_callers": {m: [p for p, _l, _t in t] for m, t in thinkers.items()}}
    return problems, facts


class TestTheSeamIsTheOnlyLauncher(unittest.TestCase):

    def test_no_organ_launches_a_model_cli_and_the_eight_think(self):
        if not ORGANS.is_dir():
            raise unittest.SkipTest("no organs/ under %s" % ROOT)
        problems, facts = verdict(ROOT)
        self.assertEqual(problems, [], json.dumps(facts, indent=1))
        self.assertEqual(set(POPULATION) <= set(facts["think_callers"]), True)

    def test_every_purpose_the_population_names_has_a_posture(self):
        cat = json.loads((ORGANS / "thinkers.json").read_text(encoding="utf-8"))
        named = set(cat["purposes"]["text"]) | set(cat["purposes"]["research"])
        self.assertLessEqual(set(POPULATION.values()), named)
        self.assertEqual(cat["purposes"]["research"], ["gap_research"],
                         "research (web only) is gap research's alone; every other organ is text only")


class TestThePlantedLaunchTurnsTheGateRed(unittest.TestCase):
    """The gate is not a tautology: a planted direct launch -- each form -- is named, file and line."""

    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="seamgate_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        shutil.copytree(str(ORGANS), str(self.tmp / "organs"), ignore=shutil.ignore_patterns("__pycache__"))

    def _plant(self, name, body):
        (self.tmp / "organs" / name).write_text(body, encoding="utf-8")
        problems, _ = verdict(self.tmp)
        return [p for p in problems if p.startswith(name[:-3] + ":")]

    def test_a_direct_claude_p_is_red(self):
        hits = self._plant("planted_direct.py", "import subprocess\nsubprocess.run(['claude', '-p', 'hi'])\n")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("planted_direct:2", hits[0])

    def test_every_launch_form_is_red(self):
        forms = {
            "planted_codex.py": "import subprocess\nsubprocess.run(('codex', 'exec', 'x'))\n",
            "planted_kimi.py": "cmd = ['kimi', '-p', 'x']\n",
            "planted_ollama.py": "import os\nos.system('ollama run qwen hi')\n",
            "planted_which.py": "import shutil\nb = shutil.which('claude')\n",
            "planted_var.py": "import subprocess\nb='/x/claude'\nsubprocess.run([b, '-p', 'x'])\n",
            "planted_abs.py": "cmd = ['/usr/local/bin/codex', 'exec']\n",
        }
        for name, body in forms.items():
            with self.subTest(form=name):
                self.assertEqual(len(self._plant(name, body)), 1, name)
                (self.tmp / "organs" / name).unlink()

    def test_tables_that_name_a_vendor_are_not_launches(self):
        hits = self._plant("planted_table.py",
                           "T = ('claude', 'claude-code')\nR = {'name': 'Claude Code', 'binaries': ['claude']}\n"
                           "P = ('claude', 'codex')\n")
        self.assertEqual(hits, [])

    def test_a_removed_organ_call_is_red(self):
        p = self.tmp / "organs" / "whisper_network.py"
        p.write_text(p.read_text(encoding="utf-8").replace("_thinker.think(", "_thinker.not_think("),
                     encoding="utf-8")
        problems, _ = verdict(self.tmp)
        self.assertTrue(any(x.startswith("whisper_network does not think") for x in problems), problems)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""R5 -- the snapshot-diff test, Linux half: F4's instrument.

Council #12 (2026-09-18), memory #20140: the first wet run of install.ps1
rewrote the operator's real user PATH from inside a "scratch" profile. A scratch
directory is not a boundary; a snapshot-diff is. voidloom/tests/harness/
outside_target_diff.py snapshots a $HOME before and after a run and refuses on
any difference the installer's DECLARED write-set (install/write_set.json,
resolved by `install.sh --print-write-set`) does not name.

This file grades the instrument, not the installer (the installer is graded by
the cold gate, which runs this harness around a real install):
  * the declaration exists, parses, and every entry carries a reason;
  * `install.sh --print-write-set` resolves every variable for its invocation
    and marks entries active/inactive by the flags given;
  * a TEST DOUBLE that appends a line to $HOME/.bashrc -- an undeclared write --
    turns the harness RED, naming .bashrc;
  * a double that writes $HOME/.claude/settings.json and CLAUDE.md -- the two
    named members INSIDE the default target root, which the wide walk excludes
    -- turns the harness RED naming both (round-1 reflection, item 1: before
    the repair this was GREEN with `changes: []`, a declared verdict with no
    declaration);
  * a double that writes only declared paths is GREEN;
  * an empty observation set is UNMEASURABLE (rc 3), never green;
  * the real login home is REFUSED (rc 4);
  * the manager VERBS (`systemctl --user enable --now ...`) are declared in
    write_set.json and graded by `verbs` against a recorder log: a recorded
    call the declaration does not name is RED naming it; an active verb never
    recorded is RED too; an inactive declaration is not a permission;
  * THE BOUNDARY IS EVERY BASE-DIRECTORY VARIABLE (round-1 iteration-2
    reflection, item 1): `run` pins HOME and the four XDG base dirs under
    --home, drops the model/wheel cache redirectors, and REFUSES (rc 4, naming
    it) a WIZARD_* path override that resolves outside --home -- and (iteration-3
    reflection, item 2) ANY non-absolute value of a path-bearing WIZARD_*
    variable (relative, `~/x`, `$HOME/x`: the child resolves it against its cwd).
    Executed: the caller's XDG_CONFIG_HOME points OUTSIDE --home and the
    double's unit file still lands UNDER it, the outside dir staying empty; the
    three non-absolute shapes are refused with the cwd untouched; install.sh
    and install_crons.sh refuse the same shapes rc 2 naming the variable;
  * ${HOME}/.nv/** is declared ACTIVE under --tier0-only (item 1): its writer is
    the hardware probe of Step A, which initialises CUDA on every wet install;
  * RED precedes UNMEASURABLE (item 6): a planted undeclared write to a named
    member with --target-root == --home (an empty wide walk) is rc 1 naming it;
  * a `transient` declaration is not a permission to leave the path behind
    (item 9): present after the run is undeclared, with the note.

At the parent commit none of the three exists: red by absence.

These tests bind the CHECKOUT (install.sh is root=null: it ships, it is not
laid). On an installed brain they SKIP with the reason -- _require_checkout().
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SH = ROOT / "install.sh"
HARNESS = ROOT / "voidloom" / "tests" / "harness" / "outside_target_diff.py"
WRITE_SET = ROOT / "install" / "write_set.json"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch")).resolve()
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _require_checkout():
    """These tests bind the CHECKOUT (git index + the null-root front-door files). On an installed
    brain neither exists; a skip with the reason is the honest verdict (UNMEASURABLE, not PASS)."""
    if not (ROOT / "install.sh").is_file() or not (ROOT / ".git").exists():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh/.git (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


class TestOutsideTargetDiff(unittest.TestCase):

    def setUp(self):
        _require_checkout()
        self.assertTrue(HARNESS.is_file(), "harness absent: %s" % HARNESS)
        self.assertTrue(WRITE_SET.is_file(), "install/write_set.json absent")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="otd_", dir=str(SCRATCH)))
        self.home = self.tmp / "home"
        (self.home / ".local" / "bin").mkdir(parents=True)
        (self.home / ".bashrc").write_text("# a stranger's bashrc\n", encoding="utf-8")
        self.claude = self.home / ".claude"
        self.env = {**ENV, "HOME": str(self.home), "WIZARD_CLAUDE_HOME": str(self.claude),
                    "WIZARD_ENV_FILE": str(self.home / ".voidloom" / "wizard-library-env"),
                    "WIZARD_EMBED_MODEL": "nomic", "TMPDIR": str(self.home)}
        for k in list(self.env):
            # a caller's redirectors are the SUBJECT of the pin tests, not their ambient; and an
            # operator's own WIZARD_LIBRARY_DB (README: how the host-bound classes are pointed at a
            # brain) would be a path outside the scratch home, which the harness now refuses
            if k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "HF_HOME", "HF_HUB_CACHE",
                     "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "PIP_CACHE_DIR", "TORCHINDUCTOR_CACHE_DIR",
                     "TRITON_CACHE_DIR", "CUDA_CACHE_PATH") or (k.startswith("WIZARD_") and k not in (
                         "WIZARD_CLAUDE_HOME", "WIZARD_ENV_FILE", "WIZARD_EMBED_MODEL")):
                self.env.pop(k)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _declared(self, *flags):
        p = subprocess.run(["bash", str(SH), "--print-write-set", "--force-cpu"] + list(flags),
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = json.loads(p.stdout)
        out = self.tmp / ("declared_%d.json" % len(list(self.tmp.iterdir())))
        out.write_text(p.stdout, encoding="utf-8")
        return d, out

    def _harness(self, *args):
        p = subprocess.run([sys.executable, "-B", str(HARNESS)] + list(args),
                           capture_output=True, text=True, env=self.env, timeout=300)
        return p

    def test_write_set_declares_every_step_with_a_reason(self):
        d = json.loads(WRITE_SET.read_text(encoding="utf-8"))
        self.assertTrue(d["steps"], "no steps declared")
        for st in d["steps"]:
            self.assertTrue(st.get("writes"), "step %s declares no writes" % st.get("step"))
            for w in st["writes"]:
                self.assertTrue(w.get("path"), st)
                self.assertTrue(w.get("kind"), w)
                self.assertTrue(str(w.get("why", "")).strip(), "no reason for %s" % w["path"])
        self.assertIn("${SYSTEMD_USER_DIR}", json.dumps(d))
        self.assertIn("${ENV_FILE}", json.dumps(d))
        self.assertIn("${LOCAL_BIN}/voidloom", json.dumps(d))

    def test_print_write_set_resolves_variables_and_flags(self):
        d, _ = self._declared("--tier0-only")
        self.assertTrue(d["declared"])
        for e in d["declared"]:
            self.assertNotIn("${", e["path"], "unresolved variable: %s" % e["path"])
            self.assertIn("active", e)
        by_step = {}
        for e in d["declared"]:
            by_step.setdefault(e["step"], []).append(e)
        self.assertTrue(all(e["active"] for e in by_step["A"]))
        self.assertTrue(all(e["active"] for e in by_step["D"]))
        self.assertTrue(all(not e["active"] for e in by_step["F"]), "crons declared active without --with-crons")
        self.assertTrue(all(not e["active"] for e in by_step["C"]), "Tier-1 caches active under --tier0-only")
        self.assertTrue(any(e["path"] == str(self.home / ".nv") + "/**" and e["active"] for e in by_step["A"]),
                        "~/.nv/** must be declared ACTIVE under --tier0-only: the hardware probe (Step A.1b-i) initialises CUDA on every tier")
        self.assertEqual(d["target_root"], str(self.claude))
        self.assertTrue(any(e["path"] == str(self.home / ".voidloom" / "wizard-library-env") for e in by_step["D"]))
        d2, _ = self._declared("--with-crons", "--tier0-only")
        f_active = [e["path"] for e in d2["declared"] if e["step"] == "F" and e["active"]]
        self.assertTrue(any(p.endswith("wizard-library-sleep-loop.timer") for p in f_active), f_active)
        self.assertTrue(any(p.endswith("wizard-library-sleep-loop.service") for p in f_active))
        self.assertIn("sleep-loop", d2["armed_units"])
        # the declaration follows the WRITER'S rule: install_crons.sh writes the units to
        # ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user, so the declared F paths must too
        # (item 1: they said ${HOME}/.config while the writer honoured XDG_CONFIG_HOME)
        saved = self.env
        self.env = {**saved, "XDG_CONFIG_HOME": str(self.home / "xdg"), "XDG_CACHE_HOME": str(self.home / "xdgcache")}
        try:
            d3, _ = self._declared("--with-crons", "--tier0-only")
        finally:
            self.env = saved
        f3 = [e["path"] for e in d3["declared"] if e["step"] == "F" and e["platform"] == "Linux"
              and "wizard-library-" in e["path"]]
        self.assertGreaterEqual(len(f3), 16, f3)
        self.assertTrue(all(p.startswith(str(self.home / "xdg" / "systemd" / "user")) for p in f3), f3)
        self.assertEqual(d3["resolved"]["SYSTEMD_USER_DIR"], str(self.home / "xdg" / "systemd" / "user"))
        c3 = [e["path"] for e in d3["declared"] if e["step"] == "C"]
        self.assertTrue(any(p.startswith(str(self.home / "xdgcache" / "pip")) for p in c3), c3)
        self.assertTrue(any(p.startswith(str(self.home / "xdgcache" / "huggingface")) for p in c3), c3)
        self.assertEqual(d3["resolved"]["CACHE_HOME"], str(self.home / "xdgcache"))
        # --print-write-set itself writes nothing
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".bashrc", ".local"])

    def test_planted_undeclared_write_is_red_naming_bashrc(self):
        _, declared = self._declared("--tier0-only")
        report = self.tmp / "report.json"
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.claude),
                          "--declared", str(declared), "--report", str(report), "--",
                          "bash", "-c", "echo 'export PATH=$HOME/.local/bin:$PATH' >> \"$HOME/.bashrc\"; "
                                        "mkdir -p \"$HOME/.claude/memory\"; touch \"$HOME/.claude/memory/laid\"")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(r["verdict"], "RED")
        names = [c["path"] for c in r["undeclared_changes"]]
        self.assertEqual(names, [str(self.home / ".bashrc")], "the red must name exactly the planted write: %s" % names)
        self.assertEqual(r["named"]["bashrc"]["verdict"], "changed-UNDECLARED")
        self.assertIn(".bashrc", p.stderr)
        # the write inside the target root is not a finding: the lay manifest binds that
        self.assertFalse(any("laid" in n for n in names))

    def test_planted_write_to_settings_json_inside_the_target_root_is_red(self):
        """The two named members inside the default target root, graded from their own
        records. Red at the round-1 artifacts (rc 0, both labelled changed-declared with
        changes=[]); green after the harness grades them from their own records."""
        _, declared = self._declared("--tier0-only")
        report = self.tmp / "report3.json"
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.claude),
                          "--declared", str(declared), "--report", str(report), "--",
                          "bash", "-c", "mkdir -p \"$HOME/.claude\"; echo '{}' > \"$HOME/.claude/settings.json\"; "
                                        "echo '# x' > \"$HOME/.claude/CLAUDE.md\"")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(r["verdict"], "RED")
        names = sorted(c["path"] for c in r["undeclared_changes"])
        self.assertEqual(names, sorted([str(self.claude / "settings.json"), str(self.claude / "CLAUDE.md")]),
                         "the red must name exactly the two planted writes: %s" % names)
        self.assertEqual(r["named"]["claude_settings"]["verdict"], "changed-UNDECLARED")
        self.assertEqual(r["named"]["claude_md"]["verdict"], "changed-UNDECLARED")
        self.assertTrue(all(c.get("named_member") for c in r["undeclared_changes"]),
                        "each must be graded from its OWN record, not the wide walk")
        self.assertIn("settings.json", p.stderr)
        self.assertIn("CLAUDE.md", p.stderr)
        # the double ran with HOME = the scratch home, pinned by the harness itself
        self.assertEqual(r["command_home"], str(self.home.resolve()))

    def test_run_pins_the_childs_home_to_the_scratch_home_whatever_the_caller_says(self):
        """The harness must never hand its command a HOME other than --home. Executed
        with the caller's HOME deliberately pointing ELSEWHERE (a second scratch dir):
        the double's $HOME must still be --home, and nothing may land in the other one."""
        _, declared = self._declared("--tier0-only")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        env = {**self.env, "HOME": str(elsewhere)}
        report = self.tmp / "report_home.json"
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home),
                            "--target-root", str(self.claude), "--declared", str(declared),
                            "--report", str(report), "--",
                            "bash", "-c", "echo \"$HOME\" > \"$HOME/.home_seen\""],
                           capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)      # an undeclared write, in the RIGHT home
        r = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(r["command_home"], str(self.home.resolve()))
        self.assertEqual((self.home / ".home_seen").read_text(encoding="utf-8").strip(), str(self.home.resolve()))
        self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [], "the double wrote into the caller's HOME")
        self.assertEqual([c["path"] for c in r["undeclared_changes"]], [str(self.home / ".home_seen")])

    def test_run_pins_tmpdir_under_home_and_a_childs_mktemp_is_graded(self):
        """R5 (reference-host round). Measured by the round-1 iteration-5 reflector: the harness pinned
        HOME and the XDG dirs but never TMPDIR, so a child's `mktemp` (and torch's torchinductor_*)
        landed in the CALLER's temp dir -- outside the snapshot, invisible, so the write-set's
        `transient` declaration could never be caught left behind. Executed with the caller's TMPDIR
        pointing ELSEWHERE: the child's mktemp lands under <--home>/tmp (created by `run`), the report
        names the pin, the leftover is graded (undeclared -> RED naming it), the elsewhere dir stays empty."""
        _, declared = self._declared("--tier0-only")
        elsewhere = self.tmp / "elsewhere_tmp"
        elsewhere.mkdir()
        env = {**self.env, "TMPDIR": str(elsewhere)}
        report = self.tmp / "report_tmp.json"
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home), "--target-root",
                            str(self.claude), "--declared", str(declared), "--report", str(report), "--",
                            "bash", "-c", "f=\"$(mktemp)\"; echo \"$f\" > \"$HOME/.mktemp_seen\""],
                           capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)       # two undeclared writes, both under --home
        r = json.loads(report.read_text(encoding="utf-8"))
        pinned_tmp = str(self.home.resolve() / "tmp")
        self.assertEqual(r["command_env_pinned"].get("TMPDIR"), pinned_tmp, r["command_env_pinned"])
        made = (self.home / ".mktemp_seen").read_text(encoding="utf-8").strip()
        self.assertTrue(made.startswith(pinned_tmp + "/"), "the child's mktemp landed outside --home: %s" % made)
        self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [], "the child wrote into the caller's TMPDIR")
        names = sorted(c["path"] for c in r["undeclared_changes"])
        self.assertIn(made, names, "a leftover temp file must be GRADED, not invisible: %s" % names)
        self.assertNotIn(pinned_tmp, names, "the pinned dir's own creation is the harness's, not a difference")
        self.assertIn("TMPDIR=%s" % pinned_tmp, p.stdout)

    def test_run_refuses_a_missing_home_and_creates_nothing(self):
        """Reference-host round, iteration 2, item 6. R5's TMPDIR pin created the pinned dir with
        `mkdir(parents=True)`, so a MISTYPED --home was built -- with every parent -- and graded
        UNMEASURABLE (rc 3), where the base harness refused it rc 2 and created nothing. The
        instrument must never create its own subject: rc 2 naming it, and the path still absent."""
        _, declared = self._declared("--tier0-only")
        missing = self.tmp / "does" / "not" / "exist"
        report = self.tmp / "report_missing.json"
        # no product override may point outside the (missing) home, or the run is refused rc 4 first
        env = {k: v for k, v in self.env.items() if k not in ("WIZARD_CLAUDE_HOME", "WIZARD_ENV_FILE")}
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(missing), "--target-root",
                            str(missing / ".claude"), "--declared", str(declared), "--report", str(report), "--",
                            "true"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("home is not a directory", p.stderr)
        self.assertFalse((self.tmp / "does").exists(), "the harness created the mistyped --home and its parents")
        self.assertFalse(report.exists(), "a refused run wrote a report")

    def test_run_refuses_a_pinned_base_dir_that_resolves_outside_home(self):
        """Reference-host round, iteration 3, item 7. `run` pins TMPDIR and the four XDG base dirs UNDER --home,
        but checked neither where they resolve: with <home>/tmp a symlink out of the home, a child's mktemp
        landed outside and the verdict was GREEN with 0 changes (measured; the same through <home>/.cache).
        The class includes a pinned dir that does not exist yet under a PARENT that is a symlink out
        (<home>/.local -> outside: XDG_DATA_HOME's first write goes outside). Each is refused rc 4 before
        the command runs, naming the variable, and nothing is written outside."""
        _, declared = self._declared("--tier0-only")
        for link, var in (("tmp", "TMPDIR"), (".cache", "XDG_CACHE_HOME"), (".local", "XDG_DATA_HOME")):
            home = self.tmp / ("h_" + link.strip("."))
            home.mkdir()
            (home / ".bashrc").write_text("# a stranger's bashrc\n", encoding="utf-8")
            outside = self.tmp / ("outside_" + link.strip("."))
            outside.mkdir()
            (home / link).symlink_to(outside)
            env = {**self.env, "HOME": str(home), "WIZARD_CLAUDE_HOME": str(home / ".claude"),
                   "WIZARD_ENV_FILE": str(home / ".voidloom" / "wizard-library-env")}
            report = self.tmp / ("report_%s.json" % link.strip("."))
            cmd = ('touch "$HOME/.ran"; f="$(mktemp)"; mkdir -p "$XDG_CACHE_HOME" "$XDG_DATA_HOME"; '
                   'touch "$XDG_CACHE_HOME/c" "$XDG_DATA_HOME/d"')
            p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(home), "--target-root",
                                str(home / ".claude"), "--declared", str(declared), "--report", str(report), "--",
                                "bash", "-c", cmd], capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(p.returncode, 4, (link, p.stdout + p.stderr))
            self.assertIn("resolve OUTSIDE --home through a symlink", p.stderr, (link, p.stderr))
            self.assertIn(var, p.stderr, (link, p.stderr))
            self.assertFalse((home / ".ran").exists(), "%s: the command ran after the refusal" % link)
            self.assertEqual(list(outside.iterdir()), [], "%s: something was written OUTSIDE --home" % link)

    def test_run_pins_the_xdg_base_dirs_and_refuses_a_product_override_outside_home(self):
        """Item 1, executed. The caller's XDG_CONFIG_HOME points OUTSIDE --home; the double writes
        where install_crons.sh writes (${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user). Before the
        repair: 16 unit files and 10 manager calls outside the snapshot, verdict GREEN. Now the
        harness pins the four XDG base dirs under --home, so the write lands INSIDE --home (RED
        naming it, since it is undeclared) and the outside dir stays empty; the cache redirectors
        are dropped; a WIZARD_* path override outside --home is REFUSED rc 4 naming it."""
        _, declared = self._declared("--tier0-only")
        elsewhere = self.tmp / "elsewhere_xdg"
        elsewhere.mkdir()
        env = {**self.env, "XDG_CONFIG_HOME": str(elsewhere), "XDG_CACHE_HOME": str(elsewhere / "cache"),
               "XDG_DATA_HOME": str(elsewhere / "data"), "XDG_STATE_HOME": str(elsewhere / "state"),
               "HF_HOME": str(elsewhere / "hf"), "PIP_CACHE_DIR": str(elsewhere / "pip"), "CUDA_CACHE_PATH": str(elsewhere / "nv")}
        report = self.tmp / "report_xdg.json"
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home), "--target-root", str(self.claude),
                            "--declared", str(declared), "--json", "--report", str(report), "--", "bash", "-c",
                            "mkdir -p \"${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user\"; "
                            "touch \"${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/planted.timer\"; "
                            "mkdir -p \"${XDG_CACHE_HOME:-$HOME/.cache}/pip\"; touch \"${XDG_CACHE_HOME:-$HOME/.cache}/pip/w\"; "
                            "env > \"$HOME/.env_seen\""],
                           capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)           # undeclared, but INSIDE --home
        r = json.loads(p.stdout)
        names = sorted(c["path"] for c in r["undeclared_changes"])
        self.assertIn(str(self.home / ".config" / "systemd" / "user" / "planted.timer"), names, names)
        self.assertIn(str(self.home / ".cache" / "pip" / "w"), names, names)
        self.assertFalse(any(str(elsewhere) in n for n in names), names)
        self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [], "the double wrote through the caller's XDG_CONFIG_HOME")
        seen = dict(l.split("=", 1) for l in (self.home / ".env_seen").read_text(encoding="utf-8").splitlines() if "=" in l)
        self.assertEqual(seen["HOME"], str(self.home.resolve()))
        self.assertEqual(seen["XDG_CONFIG_HOME"], str(self.home.resolve() / ".config"))
        self.assertEqual(seen["XDG_CACHE_HOME"], str(self.home.resolve() / ".cache"))
        self.assertEqual(seen["XDG_DATA_HOME"], str(self.home.resolve() / ".local" / "share"))
        self.assertEqual(seen["XDG_STATE_HOME"], str(self.home.resolve() / ".local" / "state"))
        for k in ("HF_HOME", "PIP_CACHE_DIR", "CUDA_CACHE_PATH"):
            self.assertNotIn(k, seen, "%s reached the double" % k)
        pinned = r["command_env_pinned"]
        self.assertEqual(pinned["XDG_CONFIG_HOME"], str(self.home.resolve() / ".config"))
        self.assertEqual(sorted(pinned["dropped"]), ["CUDA_CACHE_PATH", "HF_HOME", "PIP_CACHE_DIR"])
        self.assertEqual(sorted(pinned["wizard_checked"]), ["WIZARD_CLAUDE_HOME", "WIZARD_ENV_FILE"])
        self.assertEqual(pinned["wizard_refused"], {})
        # a WIZARD_* path override OUTSIDE --home is refused, by name, before anything runs
        for var in ("WIZARD_ENV_FILE", "WIZARD_CLAUDE_HOME", "WIZARD_LIBRARY_DB"):
            env2 = {**self.env, var: str(elsewhere / "x")}
            p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home), "--target-root", str(self.claude),
                                "--declared", str(declared), "--", "bash", "-c", "touch \"$HOME/.refused_ran\""],
                               capture_output=True, text=True, env=env2, timeout=300)
            self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
            self.assertIn(var, p.stderr)
            self.assertFalse((self.home / ".refused_ran").exists(), "the command ran after the refusal")
        # a NON-absolute value of a path-bearing variable is refused too: the child would resolve it against its cwd
        # (iteration-3 reflection, item 2, measured: a relative value, a quoted `~/x` -- bash does not tilde-expand a
        # quoted variable -- and a literal `$HOME/x` each wrote a file under the caller's cwd, outside --home, under GREEN)
        for val in ("rel/env", "~/env", "$HOME/env"):
            env2 = {**self.env, "WIZARD_ENV_FILE": val}
            p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home), "--target-root", str(self.claude),
                                "--declared", str(declared), "--", "bash", "-c", "touch \"$HOME/.refused_ran\""],
                               capture_output=True, text=True, env=env2, timeout=300, cwd=str(elsewhere))
            self.assertEqual(p.returncode, 4, (val, p.stdout + p.stderr))
            self.assertIn("WIZARD_ENV_FILE", p.stderr)
            self.assertIn(val, p.stderr, "the refusal must name the value")
            self.assertFalse((self.home / ".refused_ran").exists(), "the command ran after the refusal of %r" % val)
            self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [], "the double resolved %r against its cwd" % val)
        # the interpreter override is a READ (executed, never written through): reported, not refused
        env3 = {**self.env, "WIZARD_PYTHON_BIN": sys.executable}
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home), "--target-root", str(self.claude),
                            "--declared", str(declared), "--json", "--", "true"], capture_output=True, text=True, env=env3, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("WIZARD_PYTHON_BIN", json.loads(p.stdout)["command_env_pinned"]["wizard_exempt_read_only"])

    def test_installers_refuse_a_non_absolute_path_override(self):
        """Item 2, the installer side: WIZARD_CLAUDE_HOME / WIZARD_ENV_FILE (install.sh) and WIZARD_ENV_FILE /
        WIZARD_LIBRARY_HOME / WIZARD_LIBRARY_DB / WIZARD_CONFIG_DIR (install_crons.sh; the last is the sleep-loop's
        --config directory, measured in iteration 4's reflection writing <cwd>/rel_cfg/ outside any home) are written
        into the env file and into every unit, so a value that is not an absolute path is refused rc 2 naming the variable -- the shape --attach-db
        already has. Measured before the repair: `--print-write-set` resolved ENV_FILE=rel_env and a --dry-run
        said 'Install into: rel_claude' (the whole brain relative to the cwd)."""
        crons = ROOT / "install" / "install_crons.sh"
        for var in ("WIZARD_CLAUDE_HOME", "WIZARD_ENV_FILE", "WIZARD_CONFIG_DIR"):
            for val in ("rel_" + var.lower(), "~/tilde_" + var.lower(), "$HOME/literal_" + var.lower()):
                env = {**self.env, var: val}
                p = subprocess.run(["bash", str(SH), "--print-write-set", "--tier0-only", "--force-cpu"],
                                   capture_output=True, text=True, env=env, timeout=120, cwd=str(self.tmp))
                self.assertEqual(p.returncode, 2, (var, val, p.stdout[-400:] + p.stderr))
                self.assertIn(var, p.stderr, (var, val, p.stderr))
                self.assertIn("ABSOLUTE", p.stderr, (var, val, p.stderr))
                self.assertNotIn('"declared"', p.stdout, "a declaration was printed for a refused value")
        # ALL THREE SHAPES PER VARIABLE, install_crons.sh too (reference-host round, R10c: this loop
        # exercised only the relative shape here, while install.sh's loop above ran all three), and
        # WIZARD_PYTHON_BIN among them (R10d: it is the literal first token of every ExecStart=; a
        # relative `./rel_py` was measured rendered verbatim before the refusal existed).
        for var in ("WIZARD_ENV_FILE", "WIZARD_LIBRARY_HOME", "WIZARD_LIBRARY_DB", "WIZARD_CONFIG_DIR",
                    "WIZARD_PYTHON_BIN"):
            for val in ("rel/" + var.lower(), "~/tilde_" + var.lower(), "$HOME/literal_" + var.lower(),
                        "./rel_" + var.lower()):
                env = {**self.env, "WIZARD_LIBRARY_HOME": str(self.claude / "memory"),
                       "WIZARD_LIBRARY_DB": str(self.claude / "memory" / "claude_memory.db"), var: val}
                p = subprocess.run(["bash", str(crons), "--dry-run"], capture_output=True, text=True, env=env,
                                   timeout=120, cwd=str(self.tmp))
                self.assertEqual(p.returncode, 2, (var, val, p.stdout[-400:] + p.stderr))
                self.assertIn(var, p.stderr, (var, val, p.stderr))
                self.assertIn("ABSOLUTE", p.stderr, (var, val, p.stderr))
                self.assertIn(val, p.stderr, "the refusal must name the value")
                self.assertNotIn("DRY-UNIT:", p.stdout, "a unit was rendered for a refused value")
                self.assertNotIn("ExecStart=", p.stdout, "an ExecStart was rendered for a refused value")
        # WHITESPACE in the interpreter path (iteration 3, item 6): systemd splits ExecStart= on it, so an
        # absolute path with a space rendered every unit broken -- refused rc 2 naming it, nothing rendered
        env = {**self.env, "WIZARD_LIBRARY_HOME": str(self.claude / "memory"),
               "WIZARD_LIBRARY_DB": str(self.claude / "memory" / "claude_memory.db"),
               "WIZARD_PYTHON_BIN": "/opt/py dir/bin/python3"}
        p = subprocess.run(["bash", str(crons), "--dry-run"], capture_output=True, text=True, env=env, timeout=120,
                           cwd=str(self.tmp))
        self.assertEqual(p.returncode, 2, (p.stdout[-400:] + p.stderr))
        self.assertIn("WIZARD_PYTHON_BIN must not contain whitespace", p.stderr)
        self.assertIn("/opt/py dir/bin/python3", p.stderr, "the refusal must name the value")
        self.assertNotIn("DRY-UNIT:", p.stdout, "a unit was rendered for a refused value")
        # nothing was written under the cwd or the scratch home by any refused invocation
        self.assertEqual(sorted(x.name for x in self.tmp.iterdir()), ["home"])
        self.assertEqual(sorted(x.name for x in self.home.iterdir()), [".bashrc", ".local"])

    def test_install_crons_refuses_what_a_unit_line_cannot_carry(self):
        """Reference-host round, iteration 4, LOW 2 (the class "a value that cannot be the literal text of a unit
        line"): install_crons.sh is the ONE unit template, so it validates every path it writes into a unit as
        RESOLVED. systemd expands '%' as a specifier (measured offline with systemd-analyze verify: '%h' became the
        home directory in ExecStart=), splits ExecStart=/Environment= on whitespace, and GLOBS an EnvironmentFile=
        path. Each is refused rc 2, naming the value, before any unit is rendered."""
        crons = ROOT / "install" / "install_crons.sh"
        base = {**self.env, "WIZARD_LIBRARY_HOME": str(self.claude / "memory"),
                "WIZARD_LIBRARY_DB": str(self.claude / "memory" / "claude_memory.db")}
        cases = [("WIZARD_PYTHON_BIN", "/opt/py%hdir/bin/python3", "WIZARD_PYTHON_BIN must not contain '%'")]
        if sys.platform == "linux":
            cases += [("WIZARD_LIBRARY_HOME", str(self.claude / "me%hmory"), "cannot be written into a systemd unit"),
                      ("WIZARD_LIBRARY_HOME", str(self.claude / "my memory"), "cannot be written into a systemd unit"),
                      ("WIZARD_CONFIG_DIR", str(self.home / "cfg%h"), "cannot be written into a systemd unit"),
                      ("WIZARD_ENV_FILE", str(self.home / ".voidloom" / "env[1]"), "systemd expands '%' and GLOBS")]
        for var, val, frag in cases:
            env = dict(base)
            env[var] = val
            if var == "WIZARD_LIBRARY_HOME":
                env["WIZARD_LIBRARY_DB"] = val + "/claude_memory.db"
            p = subprocess.run(["bash", str(crons), "--dry-run"], capture_output=True, text=True, env=env, timeout=120,
                               cwd=str(self.tmp))
            self.assertEqual(p.returncode, 2, (var, val, p.stdout[-400:] + p.stderr))
            self.assertIn(frag, p.stderr, (var, val, p.stderr))
            self.assertIn(val, p.stderr, "the refusal must name the value")
            self.assertNotIn("DRY-UNIT:", p.stdout, "a unit was rendered for a refused value")

    def test_red_precedes_unmeasurable_when_only_a_named_member_changes(self):
        """Item 6: with --target-root == --home the wide walk is empty; a planted undeclared
        write to settings.json must still be rc 1 RED naming it, never rc 3."""
        _, declared = self._declared("--tier0-only")
        (self.claude).mkdir()
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.home),
                          "--declared", str(declared), "--json", "--",
                          "bash", "-c", "echo '{}' > \"$HOME/.claude/settings.json\"")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["verdict"], "RED")
        self.assertEqual(r["observed_paths"]["before"], 0, "the wide walk must be empty for this pair to bite")
        self.assertEqual([c["path"] for c in r["undeclared_changes"]], [str(self.claude / "settings.json")])
        self.assertEqual(r["named"]["claude_settings"]["verdict"], "changed-UNDECLARED")

    def test_transient_declaration_present_after_the_run_is_undeclared(self):
        """Item 9: kind=transient says 'removed before exit'. Left behind = undeclared, with the
        note; removed = not a finding. The declaration's TMPDIR is the scratch home."""
        _, declared = self._declared("--tier0-only")
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.claude),
                          "--declared", str(declared), "--json", "--",
                          "bash", "-c", "touch \"$HOME/tmp.leaked\"")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(p.stdout)
        und = r["undeclared_changes"]
        self.assertEqual([c["path"] for c in und], [str(self.home / "tmp.leaked")])
        self.assertIn("declared TRANSIENT but present after the run", und[0]["note"])
        self.assertEqual(und[0]["declared_kind"], "transient")
        # created and removed inside the run: nothing to grade -> GREEN
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.claude),
                          "--declared", str(declared), "--json", "--",
                          "bash", "-c", "touch \"$HOME/tmp.gone\"; rm \"$HOME/tmp.gone\"")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_only_declared_writes_is_green(self):
        _, declared = self._declared("--tier0-only")
        report = self.tmp / "report2.json"
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.claude),
                          "--declared", str(declared), "--report", str(report), "--",
                          "bash", "-c", "mkdir -p \"$HOME/.voidloom\"; echo 'X=1' > \"$HOME/.voidloom/wizard-library-env\"; "
                                        "ln -sf \"$HOME/.claude/memory/voidloom/voidloom\" \"$HOME/.local/bin/voidloom\"")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(r["verdict"], "GREEN")
        self.assertEqual(r["undeclared"], 0)
        self.assertEqual(r["declared_matches"], 3, r["declared_changes"])
        self.assertEqual(r["named"]["env_file_default"]["verdict"], "changed-declared")
        self.assertEqual(r["named"]["local_bin"]["verdict"], "changed-declared")
        self.assertEqual(r["named"]["bashrc"]["verdict"], "unchanged")

    def test_inactive_declaration_is_not_a_permission(self):
        _, declared = self._declared("--tier0-only")            # crons NOT requested
        p = self._harness("run", "--home", str(self.home), "--target-root", str(self.claude),
                          "--declared", str(declared), "--json", "--",
                          "bash", "-c", "mkdir -p \"$HOME/.config/systemd/user\"; "
                                        "touch \"$HOME/.config/systemd/user/wizard-library-sleep-loop.timer\"")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(p.stdout)
        self.assertTrue(any("INACTIVE" in (c.get("note") or "") for c in r["undeclared_changes"]), r["undeclared_changes"])

    def test_write_set_declares_the_scheduler_verbs_and_their_symlinks(self):
        """R5/R6 sharpened: what `systemctl --user enable --now` WRITES (the
        timers.target.wants symlink per armed unit) and the VERBS themselves are declared."""
        d = json.loads(WRITE_SET.read_text(encoding="utf-8"))
        paths = [w["path"] for st in d["steps"] for w in st["writes"]]
        self.assertIn("${SYSTEMD_USER_DIR}/timers.target.wants", paths)
        self.assertIn("${SYSTEMD_USER_DIR}/timers.target.wants/wizard-library-{unit}.timer", paths)
        self.assertTrue(d.get("verbs"), "no `verbs` list declared")
        calls = [v["call"] for v in d["verbs"]]
        self.assertIn("systemctl --user daemon-reload", calls)
        self.assertIn("systemctl --user enable --now wizard-library-{unit}.timer", calls)
        self.assertTrue(any(c.startswith("launchctl load") for c in calls), calls)
        for v in d["verbs"]:
            self.assertTrue(str(v.get("why", "")).strip(), "verb without a reason: %s" % v)
            self.assertIn("platform", v, v)
        # --print-write-set resolves them with the same active() rule as the paths
        d0, _ = self._declared("--tier0-only")
        self.assertTrue(all(not v["active"] for v in d0["verbs"]), "verbs active without --with-crons")
        d1, _ = self._declared("--with-crons", "--tier0-only")
        act = [v["call"] for v in d1["verbs"] if v["active"]]
        self.assertIn("systemctl --user daemon-reload", act)
        self.assertIn("systemctl --user enable --now wizard-library-sleep-loop.timer", act)
        self.assertFalse(any("{unit}" in c for c in act), act)
        self.assertFalse(any(c.startswith("launchctl") for c in act), "launchctl active on Linux: %s" % act)
        symlinks = [e["path"] for e in d1["declared"] if e["kind"] == "symlink" and e["active"]
                    and "timers.target.wants" in e["path"]]
        self.assertEqual(len(symlinks), len(d1["armed_units"]), symlinks)
        self.assertIn(str(self.home / ".config" / "systemd" / "user" / "timers.target.wants" /
                          "wizard-library-sleep-loop.timer"), symlinks)

    def test_recorded_call_not_in_the_declaration_is_red_naming_it(self):
        """The recorder shim's log is GRADED: plant one undeclared call."""
        d1, declared = self._declared("--with-crons", "--tier0-only")
        active = [v["call"] for v in d1["verbs"] if v["active"]]
        log = self.tmp / "systemctl.log"
        log.write_text("\n".join(active + ["systemctl --user enable --now wizard-library-gap-research.timer"]) + "\n",
                       encoding="utf-8")
        report = self.tmp / "verbs.json"
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log), "--report", str(report))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(r["verdict"], "RED")
        self.assertEqual([c["call"] for c in r["undeclared_calls"]],
                         ["systemctl --user enable --now wizard-library-gap-research.timer"])
        self.assertEqual(r["unrecorded_verbs"], [])
        self.assertIn("gap-research", p.stderr)
        # every active verb recorded, nothing else: GREEN
        log.write_text("\n".join(active) + "\n", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # an active verb NEVER recorded is red too (the declaration over-claims)
        log.write_text("\n".join(active[:-1]) + "\n", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("NOT RECORDED", p.stderr)
        # an INACTIVE declaration (no --with-crons) is not a permission
        _, declared0 = self._declared("--tier0-only")
        log.write_text("systemctl --user daemon-reload\n", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared0), "--log", str(log), "--json")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("INACTIVE", json.loads(p.stdout)["undeclared_calls"][0]["note"])
        # an empty log is UNMEASURABLE, never green
        log.write_text("", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log))
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)

    def test_empty_observation_set_is_unmeasurable(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        _, declared = self._declared("--tier0-only")
        # the product overrides must point under THIS --home, or the run is (rightly) refused
        env = {**self.env, "WIZARD_CLAUDE_HOME": str(empty / ".claude"),
               "WIZARD_ENV_FILE": str(empty / ".voidloom" / "wizard-library-env")}
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(empty), "--target-root", str(empty / ".claude"),
                            "--declared", str(declared), "--", "true"], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("UNMEASURABLE", p.stdout + p.stderr)

    def test_real_login_home_is_refused(self):
        import pwd
        real = pwd.getpwuid(os.getuid()).pw_dir
        p = self._harness("snapshot", "--home", real, "--target-root", str(Path(real) / ".claude"),
                          "--out", str(self.tmp / "never.json"))
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertFalse((self.tmp / "never.json").exists())


if __name__ == "__main__":
    unittest.main()


class TestThePathVarsCommentCitesRealLines(unittest.TestCase):
    """R10a (reference-host round). The harness names WHERE each path-bearing WIZARD_* variable is read, so a
    reader can check the refusal list against the installers. That comment cited `install.sh:90/114/135-143,
    install_crons.sh:105-110/129-131` -- line numbers that had drifted off the variables. Now every citation is
    `CITE <file>:<line> <token>` and this test re-reads each cited line: the token must be on it, in code (not in
    a comment), and every variable of WIZARD_PATH_VARS must be named by the citation block."""

    def setUp(self):
        _require_checkout()            # the citations name install.sh, which ships but is not laid

    def test_every_citation_names_a_real_line(self):
        import re as _re
        text = HARNESS.read_text(encoding="utf-8")
        cites = _re.findall(r"^#\s+CITE (\S+?):(\d+) (\S+)(.*)$", text, _re.M)
        self.assertGreaterEqual(len(cites), 5, "the harness cites no reading sites (%d)" % len(cites))
        wrong = []
        for rel, line, token, _tail in cites:
            f = ROOT / rel
            if not f.is_file():
                wrong.append("%s:%s -- no such file" % (rel, line)); continue
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            n = int(line)
            got = lines[n - 1] if 0 < n <= len(lines) else ""
            if token not in got or got.lstrip().startswith("#"):
                wrong.append("%s:%s should carry %s in code; it reads %r" % (rel, line, token, got.strip()[:120]))
        self.assertEqual(wrong, [], "stale citations in the harness:\n" + "\n".join(wrong))
        block = "\n".join("%s %s %s" % (r, t, tail) for r, _l, t, tail in cites)
        m = _re.search(r"^WIZARD_PATH_VARS = \(([^)]*)\)", text, _re.M)
        declared = _re.findall(r'"(WIZARD_[A-Z_]+)"', m.group(1)) if m else []
        self.assertTrue(declared, "WIZARD_PATH_VARS not found")
        uncited = [v for v in declared if v not in block]
        self.assertEqual(uncited, [], "path variables the harness refuses but whose reading site it does not cite: %s"
                         % uncited)


class TestTheThinkersWriteSet(unittest.TestCase):
    """M17 (model-choice unit, Round 1, Wave B): the overnight thinker's proof is declared -- the systemd-run verb as an
    fnmatch pattern (its unit name and candidate file are fresh per run), the probe scratch, ~/.voidloom/thinker/**
    with the vendor state codex writes there, THINKER_RECORD beside the brain -- and the owner's ~/.codex and
    ~/.kimi-code are declared UNTOUCHED: any change there is RED, whatever else the diff says."""

    # the scratch home and the two helpers of TestOutsideTargetDiff, WITHOUT inheriting (and so re-running) its tests
    setUp = TestOutsideTargetDiff.setUp
    tearDown = TestOutsideTargetDiff.tearDown
    _declared = TestOutsideTargetDiff._declared
    _harness = TestOutsideTargetDiff._harness

    def test_the_thinker_step_is_declared_only_for_an_answer_that_names_a_model(self):
        d, _ = self._declared("--tier0-only", "--thinker", "claude")
        # the offered PROGRAM install (L-I) is step T too, but only with the owner's --install-thinker-cli
        self.assertFalse([e for e in d["declared"] if e["when"] == "an offered thinker install" and e["active"]])
        t = [e for e in d["declared"] if e["step"] == "T" and e["when"] != "an offered thinker install"]
        paths = {e["path"] for e in t}
        th = str(self.home / ".voidloom" / "thinker")
        for want in (th, th + "/**", str(self.claude / "memory" / "THINKER_RECORD"),
                     str(self.home / "voidloom-think-*")):
            self.assertIn(want, paths)
        self.assertTrue(all(e["active"] for e in t), [e["path"] for e in t if not e["active"]])
        v = [x for x in d["verbs"] if x["step"] == "T"]
        self.assertEqual(len(v), 1)
        self.assertEqual((v[0]["match"], v[0]["active"]), ("glob", sys.platform.startswith("linux")))
        self.assertEqual({u["path"] for u in d["untouched"]},
                         {str(self.home / ".codex") + "/**", str(self.home / ".kimi-code") + "/**",
                          str(self.home / ".gemini") + "/**"})
        d_e, _ = self._declared("--tier0-only", "--thinker", "off")
        self.assertFalse([x for x in d_e["verbs"] if x["step"] == "T" and x["active"]], "e) proves nothing")
        self.assertTrue(all(e["active"] for e in d_e["declared"] if e["when"] == "a thinker answer"))
        d_0, _ = self._declared("--tier0-only")
        self.assertFalse([e for e in d_0["declared"] if e["step"] == "T" and e["active"]], "no answer, no step T")

    def test_a_glob_verb_matches_its_shape_only_and_must_be_recorded(self):
        d, declared = self._declared("--tier0-only", "--thinker", "claude")
        call = ("systemd-run --user --wait --pipe --collect --quiet --unit voidloom-thinker-probe-1a2b3c4d "
                "-p EnvironmentFile=-%s/tmp.X/wizard-library-env -p WorkingDirectory=%s -p "
                "Environment=PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring -p RuntimeMaxSec=300 "
                "/usr/bin/env python3 -B /pkg/organs/thinker.py _probe --candidate /t/candidate.json --timeout 240"
                % (self.home, self.claude / "memory"))
        log = self.tmp / "m.log"
        log.write_text(call + "\n", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        log.write_text(call.replace("--wait --pipe", "--wait --scope --pipe") + "\n", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log), "--json")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertTrue(json.loads(p.stdout)["unrecorded_verbs"], "the declared pattern was not recorded -> RED")
        log.write_text("systemctl --user daemon-reload\n", encoding="utf-8")
        p = self._harness("verbs", "--declared", str(declared), "--log", str(log))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_an_offered_program_install_is_declared_only_with_the_owners_yes_and_only_its_own_paths(self):
        """Gemini-thinker Round 1 (L-I, ORIN's ruling): `curl ... | bash` before Step A widens the write set -- it is
        DECLARED (the program's own paths, the shell startup files), active only with --install-thinker-cli and only for
        that answer's program; the owner's own login tree steps out of `untouched` on exactly that offered install."""
        agy, kimi = str(self.home / ".local" / "bin" / "agy"), str(self.home / ".kimi-code") + "/**"
        d, _ = self._declared("--tier0-only", "--thinker", "gemini", "--install-thinker-cli")
        act = {e["path"] for e in d["declared"] if e["active"]}
        self.assertIn(agy, act)
        self.assertIn(str(self.home / ".bashrc"), act)
        self.assertNotIn(str(self.home / ".local" / "bin" / "claude"), act, "another vendor's program is not declared")
        self.assertNotIn(str(self.home / ".gemini") + "/**", {u["path"] for u in d["untouched"]})
        self.assertIn(kimi, {u["path"] for u in d["untouched"]})
        d, _ = self._declared("--tier0-only", "--thinker", "gemini")                 # no yes: nothing of it
        self.assertNotIn(agy, {e["path"] for e in d["declared"] if e["active"]})
        self.assertIn(str(self.home / ".gemini") + "/**", {u["path"] for u in d["untouched"]})
        d, _ = self._declared("--tier0-only", "--thinker", "off", "--install-thinker-cli")   # no program to get
        self.assertFalse([e for e in d["declared"] if e["active"] and e["name"].startswith("the overnight thinker's PROGRAM")])
        d, _ = self._declared("--dry-run", "--tier0-only", "--thinker", "kimi", "--install-thinker-cli")
        self.assertFalse([e for e in d["declared"] if e["active"]], "a dry run writes nothing")
        self.assertIn(kimi, {u["path"] for u in d["untouched"]})

    def test_a_change_under_an_untouched_path_is_red(self):
        d, declared = self._declared("--tier0-only", "--thinker", "claude")
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "auth.json").write_text("{}", encoding="utf-8")
        pre = self.tmp / "pre.json"
        post = self.tmp / "post.json"
        self.assertEqual(self._harness("snapshot", "--home", str(self.home), "--target-root", str(self.claude),
                                       "--out", str(pre)).returncode, 0)
        (codex / "auth.json").write_text('{"refreshed": 1}', encoding="utf-8")
        th = self.home / ".voidloom" / "thinker"
        th.mkdir(parents=True)
        (th / "kimi.env").write_text("KIMI_MODEL_API_KEY=x\n", encoding="utf-8")          # declared: GREEN alone
        self.assertEqual(self._harness("snapshot", "--home", str(self.home), "--target-root", str(self.claude),
                                       "--out", str(post)).returncode, 0)
        p = self._harness("diff", "--pre", str(pre), "--post", str(post), "--declared", str(declared), "--json")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        r = json.loads(p.stdout)
        und = r["undeclared_changes"]
        self.assertTrue(any(str(codex) in c["path"] and c.get("untouched") for c in und), und)
        self.assertIn("an UNTOUCHED path changed", json.dumps(und))
        self.assertFalse(any(str(th) in c["path"] for c in und), "the thinker home is declared")

    def test_a_voidloom_env_dir_outside_the_home_is_refused(self):
        _, declared = self._declared("--tier0-only")
        env = {**self.env, "VOIDLOOM_ENV_DIR": str(self.tmp / "elsewhere")}
        p = subprocess.run([sys.executable, "-B", str(HARNESS), "run", "--home", str(self.home), "--target-root",
                            str(self.claude), "--declared", str(declared), "--", "true"], capture_output=True, text=True,
                           env=env, timeout=300)
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn("VOIDLOOM_ENV_DIR", p.stdout + p.stderr)

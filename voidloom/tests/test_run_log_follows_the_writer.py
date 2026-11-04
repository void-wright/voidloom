# -*- coding: utf-8 -*-
"""organ_run_log reads the unit directory the writer wrote: ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user.

install_crons.sh, install.sh and uninstall.sh all follow XDG_CONFIG_HOME. The sensor used to read $HOME/.config whatever
was set, so with the variable elsewhere it found no timers, raised ProbeError, and every armed unit went unobserved.
Each case runs in a child, because the default is computed when the module loads.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
CHILD = ("import sys, json; sys.path.insert(0, %r); import organ_run_log as r; "
         "print(json.dumps({'dflt': r.DEFAULT_UNIT_DIR, 'units': [u.unit_name for u in r.discover_declared_units(measure_cadence=False)]}))"
         % str(ORGANS))
TIMER = "[Timer]\nOnCalendar=*-*-* 03:00:00\nPersistent=true\n"


class TheReaderFollowsTheWriter(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(dir=str(Path.home() / ".cache"))
        self.addCleanup(self.td.cleanup)
        self.home = Path(self.td.name) / "home"
        self.xdg = Path(self.td.name) / "elsewhere"
        self.home.mkdir()
        self.xdg.mkdir()

    def _units_at(self, base):
        d = base / "systemd" / "user"
        d.mkdir(parents=True)
        (d / "wizard-library-sleep.timer").write_text(TIMER, encoding="utf-8")
        return d

    def _child(self, **env):
        e = {"PATH": os.environ["PATH"], "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        e.update(env)
        return subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, env=e, timeout=60)

    def test_with_the_variable_set_the_units_are_found_where_they_were_written(self):
        d = self._units_at(self.xdg)
        p = self._child(XDG_CONFIG_HOME=str(self.xdg))
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(out["dflt"], str(d))
        self.assertEqual(out["units"], ["wizard-library-sleep.timer"])

    def test_control_unset_means_home_config(self):
        d = self._units_at(self.home / ".config")
        p = self._child()
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1])["dflt"], str(d))

    def test_an_empty_variable_means_unset_as_the_shell_writer_reads_it(self):
        d = self._units_at(self.home / ".config")
        p = self._child(XDG_CONFIG_HOME="")
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1])["dflt"], str(d))

    def test_the_writer_still_spells_the_rule_the_reader_copies(self):
        src = (ROOT / "install" / "install_crons.sh").read_text(encoding="utf-8")
        self.assertRegex(src, r'\$\{XDG_CONFIG_HOME:-\$HOME/\.config\}/systemd/user')


class TheSentinelWatchesWhatShips(unittest.TestCase):
    """A sensor for an organ that does not ship reads a verdict about nothing, and through a literal table name."""

    def setUp(self):
        sys.path.insert(0, str(ORGANS))
        self.addCleanup(sys.path.remove, str(ORGANS))
        import organ_yield_sentinel as s
        self.s = s

    def test_every_watched_module_ships(self):
        missing = [w["organ"] for w in self.s.WATCH if not (ORGANS / (w["module"] + ".py")).is_file()]
        self.assertEqual(missing, [])

    def test_no_watch_reads_a_vector_table_by_literal_name(self):
        hits = [w["organ"] for w in self.s.WATCH if re.search(r"memory_embeddings", w.get("input_sql") or "")]
        self.assertEqual(hits, [])

    def test_control_the_list_is_not_empty(self):
        self.assertGreater(len(self.s.WATCH), 3)

    def test_its_unit_directory_follows_the_writer_too(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home() / ".cache")) as td:
            e = {"PATH": os.environ["PATH"], "HOME": td, "XDG_CONFIG_HOME": td + "/x", "PYTHONDONTWRITEBYTECODE": "1"}
            code = "import sys; sys.path.insert(0, %r); import organ_yield_sentinel as s; print(s.UNIT_DIR)" % str(ORGANS)
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[-400:])
            self.assertEqual(p.stdout.strip(), td + "/x/systemd/user")


if __name__ == "__main__":
    unittest.main()

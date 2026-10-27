# -*- coding: utf-8 -*-
"""R1 -- the cron roster: declared ONCE, bound to cron/*.timer, and sleep-loop is armed.

Council #12 (2026-09-18): install.sh's CRON_DEFS armed six, install/install_crons.sh
armed four from a table of its own, the package ships seventeen timers, and
`sleep-loop` -- the only organ that writes consolidation_sources -- was in neither
table. A stranger who installed with crons got a brain that never consolidated.

The roster is install/cron_roster.json; its one reader is install/cron_roster.py;
both installers call the reader and carry no table. This file binds:
  * every packaged timer is classified armed-by-default or ships-as-template,
    explicitly, and every roster unit has a packaged timer;
  * sleep-loop is armed-by-default;
  * each unit's exec equals the packaged service's own ExecStart (brain-relative,
    placeholders for the DB / the sleep config / the brain dir), its schedule
    equals the packaged OnCalendar BYTE FOR BYTE, and its timeout equals the
    packaged TimeoutStartSec -- so the roster is not a second description of the
    schedule but a binding to the units;
  * THE PACKAGE SHIPS NO TIME ZONE (reference-host round, R3): 15 of 17 packaged
    timers carried the author's `America/New_York` and this file stripped it on
    purpose (a TZ_TOKENS set), so the body binding tolerated an OnCalendar VALUE
    difference and was weaker than the "byte-equal" it claimed. The suffix is gone
    from the packaged timers, the tolerance is gone from this file, and no
    packaged OnCalendar may carry a zone: a stranger's timers fire in THEIR local
    time (the user manager runs in /etc/localtime);
  * every armed exec target is a file the lay manifest lays under root=brain
    (the old CRON_DEFS pointed every unit at ${BRAIN_DIR}/<organ>.py, where
    nothing lives);
  * neither installer carries a hand table; `install.sh --with-crons --dry-run`
    into a scratch $HOME names every armed unit, sleep-loop among them, with a
    laid ExecStart target;
  * THE BODY BINDING (round-1 iteration-2 reflection, item 2): every unit,
    rendered by install/install_crons.sh --dry-run (the one template + the
    roster's service_extra / timer_extra / service_omit), carries EXACTLY the
    packaged unit's directive set -- [Unit]/[Service]/[Timer]/[Install], every
    Key=Value line -- modulo the three differences that are by design and named
    here: Description=, %h (rendered absolute) and the interpreter -- and nothing
    else: no OnCalendar value is normalised on either side. Before the
    repair the template rendered After=/Wants=network-online.target for 0 of 17
    packaged units and lost organ-run-log's Nice=, Environment=PATH=,
    Documentation= and its timer's RandomizedDelaySec=; a --with-crons landing
    would have overwritten the eight live units with that body.

These tests bind the CHECKOUT (the git index and the null-root front-door file
install.sh). On an installed brain neither exists: they SKIP with the reason
(UNMEASURABLE, not PASS) -- see _require_checkout().

At the parent commit the roster does not exist and CRON_DEFS lacks sleep-loop:
red on every count, and the dry run shows the `${BRAIN_DIR}/dream_pipeline.py`
target that does not exist.
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

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
CRON = ROOT / "cron"
ROSTER = ROOT / "install" / "cron_roster.json"
READER = ROOT / "install" / "cron_roster.py"
MANIFEST = ROOT / "install" / "lay_manifest.json"
SH = ROOT / "install.sh"
CRONS_SH = ROOT / "install" / "install_crons.sh"
PREFIX = "wizard-library-"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch")).resolve()
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _require_checkout():
    """These tests bind the CHECKOUT (git index + the null-root front-door files). On an installed
    brain neither exists; a skip with the reason is the honest verdict (UNMEASURABLE, not PASS)."""
    if not (ROOT / "install.sh").is_file() or not (ROOT / ".git").exists():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh/.git (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


def packaged_timers():
    """The PACKAGED timers, from git where possible (an untracked stray timer must not inflate it)."""
    try:
        out = subprocess.run(["git", "ls-files", "--", "cron/%s*.timer" % PREFIX], cwd=str(ROOT),
                             capture_output=True, text=True, check=True, env=ENV).stdout.split()
        if out:
            # set(): `git ls-files` without --stage prints a half-merged path once per stage
            return sorted({Path(f).name[len(PREFIX):-len(".timer")] for f in out})
    except Exception:  # noqa: BLE001
        pass
    return sorted(p.name[len(PREFIX):-len(".timer")] for p in CRON.glob(PREFIX + "*.timer"))


def packaged_service(name):
    """(exec_rel_with_placeholders, exec_post, oncalendars, timeout) from cron/*.service + .timer."""
    svc = (CRON / ("%s%s.service" % (PREFIX, name))).read_text(encoding="utf-8")
    svc = re.sub(r"\\\n\s*", " ", svc)
    tmr = (CRON / ("%s%s.timer" % (PREFIX, name))).read_text(encoding="utf-8")

    def norm(cmd):
        cmd = cmd.replace("/usr/bin/env python3 -u %h/.claude/memory/", "")
        cmd = cmd.replace("%h/.claude/memory/claude_memory.db", "{DB}")
        cmd = cmd.replace("%h/.voidloom/sleep-loop-config.json", "{CFG}")
        cmd = cmd.replace("%h/.claude/memory/", "{BRAIN}/")
        return " ".join(cmd.split())
    ex = re.search(r"^ExecStart=(.*)$", svc, re.M)
    post = re.search(r"^ExecStartPost=(.*)$", svc, re.M)
    to = re.search(r"^TimeoutStartSec=(\d+)", svc, re.M)
    cals = re.findall(r"^OnCalendar=(.*)$", tmr, re.M)
    return (norm(ex.group(1)) if ex else None, norm(post.group(1)) if post else None,
            cals,                                  # LITERAL: no zone is stripped (R3)
            int(to.group(1)) if to else None)


def _zone_names():
    """Every IANA zone this interpreter knows, plus the fixed spellings systemd also accepts.
    The Area/Location regex below catches a zone the local tzdata does not carry."""
    names = {"UTC", "GMT", "Z"}
    try:
        import zoneinfo
        names |= set(zoneinfo.available_timezones())
    except Exception:  # noqa: BLE001 - py<3.9 or no tzdata: the regex still binds
        pass
    return names


_AREA_LOCATION = re.compile(r"^[A-Z][A-Za-z_]+/[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)?$")
# The author's zone in prose (a timer Description a stranger reads in `systemctl list-timers`).
_ZONE_ABBREV = re.compile(r"\b(EST|EDT|PST|PDT|CST|CDT|MST|MDT)\b")


class TestThePackageShipsNoTimeZone(unittest.TestCase):
    """R3 (reference-host round). Measured at c6d7af2: 15 of 17 cron/*.timer carried
    `America/New_York` on their OnCalendar lines -- the AUTHOR's zone in a generic package
    (a stranger in Berlin would have been handed New York schedules by the setup-*-cron.sh
    copies) -- and the comparer above stripped it on purpose. Every packaged OnCalendar is
    now zone-free, and its prose names no zone either."""

    def setUp(self):
        _require_checkout()
        self.timers = packaged_timers()
        self.assertGreater(len(self.timers), 0, "no packaged timers -- empty population")

    def test_no_packaged_oncalendar_carries_a_zone(self):
        zones = _zone_names()
        found, lines = [], 0
        for name in self.timers:
            for cal in re.findall(r"^OnCalendar=(.*)$", (CRON / ("%s%s.timer" % (PREFIX, name))).read_text(
                    encoding="utf-8"), re.M):
                lines += 1
                bad = [t for t in cal.split() if t in zones or _AREA_LOCATION.match(t)]
                if bad:
                    found.append("%s: OnCalendar=%s carries %s" % (name, cal, bad))
        self.assertGreater(lines, len(self.timers) - 1, "fewer OnCalendar lines than timers -- the reader is wrong")
        self.assertEqual(found, [], "a packaged timer ships a time zone:\n" + "\n".join(found))

    def test_no_packaged_timer_names_a_zone_in_prose(self):
        found = []
        for name in self.timers:
            for n, line in enumerate((CRON / ("%s%s.timer" % (PREFIX, name))).read_text(encoding="utf-8").splitlines(), 1):
                if "America/" in line or _ZONE_ABBREV.search(line):
                    found.append("%s.timer:%d %s" % (name, n, line.strip()))
        self.assertEqual(found, [], "a packaged timer names the author's zone:\n" + "\n".join(found))

    def test_the_laid_renderer_renders_no_zone_and_its_dry_run_writes_nothing(self):
        """Iteration 2, items 7 and 16 (R3's TITLE: the package ships no author time zone). The
        packaged timers were zone-free, but cron/platform-detection.sh -- laid and runnable --
        still DEFAULTED to America/New_York and rendered it into 6 of 6 generated timers on a home
        with no config (executed by the iteration-1 reflector on a copy), and `generate --dry-run`
        ignored the flag and wrote 12 files into <tree>/cron/generated/. Run here on a SCRATCH COPY
        of cron/ with no config file and no zone in the environment: the dry run prints the six
        timers and writes nothing; the wet run renders six timers and no OnCalendar carries a zone."""
        src = CRON / "platform-detection.sh"
        if not src.is_file() or not (ROOT / "organs" / "claude_memory.py").is_file():
            self.skipTest("this tree has no cron/platform-detection.sh or organs/claude_memory.py")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="platdet_", dir=str(SCRATCH)))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        copy = tmp / "cron"
        shutil.copytree(str(CRON), str(copy), ignore=shutil.ignore_patterns("generated"))
        home = tmp / "home"
        (home / "tmp").mkdir(parents=True)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home / "tmp"),
               "XDG_CONFIG_HOME": str(home / ".config"), "PYTHONDONTWRITEBYTECODE": "1",
               "WIZARD_CONFIG_PATH": str(tmp / "no-wizard-config.json"),   # no config names a zone
               "WIZARD_MODULE_DIR": str(ROOT / "organs")}                  # read only: paths rendered into units
        tree = lambda: sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*") if "tmp" not in p.relative_to(tmp).parts)
        before = tree()
        p = subprocess.run(["bash", str(copy / "platform-detection.sh"), "generate", "--dry-run"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        self.assertEqual(tree(), before, "generate --dry-run wrote into the tree")
        self.assertEqual(len(re.findall(r"^OnCalendar=", p.stdout, re.M)), 6, p.stdout[-1500:])
        p = subprocess.run(["bash", str(copy / "platform-detection.sh"), "generate"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        timers = sorted((copy / "generated").glob(PREFIX + "*.timer"))
        self.assertEqual(len(timers), 6, [t.name for t in timers])
        zones, found = _zone_names(), []
        for t in timers:
            for line in t.read_text(encoding="utf-8").splitlines():
                if line.startswith("OnCalendar="):
                    bad = [w for w in line.split("=", 1)[1].split() if w in zones or _AREA_LOCATION.match(w)]
                    if bad:
                        found.append("%s: %s" % (t.name, line))
                if "America/" in line or _ZONE_ABBREV.search(line):
                    found.append("%s: %s" % (t.name, line))
        self.assertEqual(found, [], "the laid renderer shipped a zone with no config naming one:\n" + "\n".join(found))


def laid_brain_files():
    if MANIFEST.is_file():
        doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return {e["dst"] for e in doc["entries"] if e["root"] == "brain"}, "install/lay_manifest.json"
    return None, "no lay manifest in this tree"


class TestTheAuditToolCarriesNoAuthorZone(unittest.TestCase):
    """Reference-host round, iteration 3, item 11 (R3's title: the package ships no author time zone). The laid
    audit tool stripped exactly " America/New_York" from each unit's calendar, so a host in any other zone got
    an unstripped column. The calendar column's own expression, taken from the tool's source (never a copy of
    it), must strip ANY trailing IANA zone and leave a bare calendar (and UTC) untouched."""

    def test_the_calendar_column_strips_any_iana_zone(self):
        import ast
        tool = ROOT / "tools" / "gen_cron_audit.py"
        if not tool.is_file():
            self.skipTest("tree lacks tools/gen_cron_audit.py")
        src = tool.read_text(encoding="utf-8")
        self.assertNotIn("America/", src, "the audit tool still names the author's zone")
        cols = [n.args[0].elts[2] for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "rows"
                and n.args and isinstance(n.args[0], ast.Tuple)]
        self.assertEqual(len(cols), 1, "the audit's one rows.append((unit, organ, <calendar>, ...)) was not found")
        col = compile(ast.fix_missing_locations(ast.Expression(cols[0])), str(tool), "eval")
        for cal, want in (("*-*-* 09:15:00 Asia/Tokyo", "*-*-* 09:15:00"),
                          ("*-*-* 03:30:00 Europe/Berlin", "*-*-* 03:30:00"),
                          ("Mon *-*-* 02:00:00 America/Argentina/Buenos_Aires", "Mon *-*-* 02:00:00"),
                          ("*-*-* 22:00:00", "*-*-* 22:00:00"), ("*-*-* 22:00:00 UTC", "*-*-* 22:00:00 UTC")):
            self.assertEqual(eval(col, {"re": re}, {"cal": cal}), want[:28], cal)   # noqa: S307 - the tool's own expression


class TestRosterBindsTheTimers(unittest.TestCase):

    def setUp(self):
        _require_checkout()
        self.timers = packaged_timers()
        self.assertGreater(len(self.timers), 0, "no packaged timers -- empty population")
        self.assertTrue(ROSTER.is_file(), "install/cron_roster.json is absent from %s" % ROOT)
        self.assertTrue(READER.is_file(), "install/cron_roster.py is absent")
        self.units = json.loads(ROSTER.read_text(encoding="utf-8"))["units"]

    def test_every_packaged_timer_is_classified_and_every_unit_has_a_timer(self):
        unclassified = sorted(set(self.timers) - set(self.units))
        phantom = sorted(set(self.units) - set(self.timers))
        self.assertEqual(unclassified, [], "packaged timers with no roster classification: %s" % unclassified)
        self.assertEqual(phantom, [], "roster units with no packaged timer: %s" % phantom)
        for name, u in self.units.items():
            self.assertIn(u["arm"], ("armed-by-default", "ships-as-template"), name)
            self.assertTrue(u["why"].strip(), "%s: a classification without a reason" % name)

    def test_sleep_loop_is_armed_by_default(self):
        self.assertEqual(self.units.get("sleep-loop", {}).get("arm"), "armed-by-default",
                         "the armed set lacks sleep-loop: a brain that never consolidates")

    def test_exec_schedule_and_timeout_are_bound_to_the_packaged_units(self):
        wrong = []
        for name in self.timers:
            u = self.units[name]
            ex, post, cals, to = packaged_service(name)
            if " ".join(u["exec"].split()) != ex:
                wrong.append("%s: exec %r != packaged ExecStart %r" % (name, u["exec"], ex))
            if (u.get("exec_post") or None) != post:
                wrong.append("%s: exec_post %r != packaged ExecStartPost %r" % (name, u.get("exec_post"), post))
            if u["systemd"] != cals[0]:
                wrong.append("%s: systemd %r != packaged OnCalendar %r (byte-equal)" % (name, u["systemd"], cals[0]))
            # both sides compared always: a packaged unit WITHOUT TimeoutStartSec must carry
            # timeout null (a typed 300 for organ-run-log slipped past `if to is not None`)
            if u["timeout"] != to:
                wrong.append("%s: timeout %r != packaged TimeoutStartSec %r" % (name, u["timeout"], to))
            extra_cals = [l[len("OnCalendar="):] for l in (u.get("timer_extra") or []) if l.startswith("OnCalendar=")]
            if [u["systemd"]] + extra_cals != cals:
                wrong.append("%s: OnCalendar lines %r != packaged %r" % (name, [u["systemd"]] + extra_cals, cals))
        self.assertEqual(wrong, [], "\n".join(wrong))

    def test_every_exec_target_is_laid_under_the_brain(self):
        laid, note = laid_brain_files()
        self.assertIsNotNone(laid, note)
        missing = []
        for name, u in self.units.items():
            for cmd in (u["exec"], u.get("exec_post") or ""):
                if not cmd:
                    continue
                tgt = cmd.split()[0]
                if tgt not in laid:
                    missing.append("%s: %s (%s)" % (name, tgt, note))
        self.assertEqual(missing, [], "exec targets the manifest does not lay under root=brain: %s" % missing)

    def test_reader_check_agrees(self):
        p = subprocess.run([sys.executable, "-B", str(READER), "--roster", str(ROSTER), "check",
                            "--cron-dir", str(CRON), "--json"], capture_output=True, text=True, env=ENV)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = json.loads(p.stdout)
        self.assertTrue(d["agree"])
        self.assertEqual(d["packaged"], len(self.timers))
        self.assertIn("sleep-loop", d["armed_by_default"])
        self.assertEqual(sorted(d["armed_by_default"] + d["ships_as_template"]), self.timers)


def unit_directives(text):
    """{(section, 'Key=Value')} of a unit body: comments and blank lines dropped, backslash
    continuations joined, whitespace inside a line normalised. Description= is excluded
    (it differs by design between the template and the packaged files)."""
    text = re.sub(r"\\\n\s*", " ", text)
    out, sec = set(), None
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line
            continue
        if "=" in line and not line.startswith("Description="):
            out.add((sec, line))
    return out


def packaged_unit_as_rendered(name, suffix, home, py):
    """The packaged unit's text with the two BY-DESIGN substitutions applied: %h -> the
    scratch home, /usr/bin/env python3 -> the interpreter. Nothing else is normalised: the
    OnCalendar values are compared literally (R3 -- the author's zone is no longer shipped,
    so there is nothing to strip, and stripping it here is what hid it)."""
    text = (CRON / ("%s%s.%s" % (PREFIX, name, suffix))).read_text(encoding="utf-8")
    return text.replace("/usr/bin/env python3", py).replace("%h", str(home))


def dry_unit_bodies(stdout):
    """{'<name>.service' | '<name>.timer': body} from install_crons.sh --dry-run's DRY-UNIT: blocks."""
    units, cur = {}, None
    for line in stdout.splitlines():
        m = re.match(r"^  DRY-UNIT: (\S+)$", line)
        if m:
            cur = m.group(1)
            units[cur] = []
            continue
        if cur is not None and line.startswith("    "):
            units[cur].append(line[4:])
            continue
        cur = None
    return {k: "\n".join(v) for k, v in units.items()}


class TestEachUnitDeclaresHowItGetsAClaudeCredential(unittest.TestCase):
    """M13 with the owner's condition (Round 2 wave 1, ruler VL-MC-v1.1): `claude_credential` -- thinker | env-file | none --
    is DERIVED from the code (the entry points' import closure over organs/) and bound to it here, so `voidloom thinker
    status`, the installer's DONE and the doctor can say the owner's env-file token line can go ONLY when it is true."""

    def test_the_declaration_equals_the_derivation_for_every_unit(self):
        _require_checkout()
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import cron_roster as CR
        finally:
            sys.path.pop(0)
        units = json.loads(ROSTER.read_text(encoding="utf-8"))["units"]
        wrong = {}
        for name, u in units.items():
            cred, why = CR.derive_claude_credential(ROOT, u)
            if u.get("claude_credential") != cred:
                wrong[name] = (u.get("claude_credential"), cred, why)
        self.assertEqual(wrong, {}, "declared vs derived: %r" % wrong)


class TestTheOneTemplateRendersThePackagedBody(unittest.TestCase):
    """Round-1 iteration-2 reflection, item 2. Every roster unit is rendered by the ONE template
    (install/install_crons.sh --dry-run, WIZARD_CRON_ONLY=<all 17>) into a scratch environment
    and its directive set must EQUAL the packaged cron/ unit's, service and timer alike."""

    def setUp(self):
        _require_checkout()
        self.assertTrue(CRONS_SH.is_file(), "install/install_crons.sh absent")
        self.units = json.loads(ROSTER.read_text(encoding="utf-8"))["units"]
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="roster_body_", dir=str(SCRATCH)))
        self.home = self.tmp / "home"
        (self.home / "tmp").mkdir(parents=True)
        self.py = sys.executable
        self.env = {**ENV, "HOME": str(self.home), "WIZARD_CLAUDE_HOME": str(self.home / ".claude"),
                    "WIZARD_LIBRARY_HOME": str(self.home / ".claude" / "memory"),
                    "WIZARD_ENV_FILE": str(self.home / ".voidloom" / "wizard-library-env"),
                    "WIZARD_PYTHON_BIN": self.py, "WIZARD_CRON_ONLY": ",".join(self.units),
                    "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
                    "XDG_DATA_HOME": str(self.home / ".local" / "share"), "XDG_STATE_HOME": str(self.home / ".local" / "state"),
                    "TMPDIR": str(self.home / "tmp")}
        for k in ("WIZARD_PRECOG_CUDA_DEVICE", "WIZARD_DREAM_HOUR"):
            self.env.pop(k, None)          # the retired pre-cognitive override and the dreaming hour stay off here

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render_all(self):
        p = subprocess.run(["bash", str(CRONS_SH), "--dry-run"], capture_output=True, text=True,
                           env=self.env, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-3000:])
        written = sorted(str(x.relative_to(self.home)) for x in self.home.rglob("*") if x.name != "tmp")
        self.assertEqual(written, [], "the dry run wrote: %s" % written[:10])
        return dry_unit_bodies(p.stdout)

    def test_every_unit_body_equals_the_packaged_body(self):
        bodies = self._render_all()
        self.assertEqual(sorted(bodies), sorted(n + s for n in self.units for s in (".service", ".timer")),
                         "the dry run did not print a DRY-UNIT block for every unit and timer")
        wrong = []
        for name in sorted(self.units):
            for suffix in ("service", "timer"):
                rendered = unit_directives(bodies["%s.%s" % (name, suffix)])
                packaged = unit_directives(packaged_unit_as_rendered(name, suffix, self.home, self.py))
                self.assertTrue(packaged, "%s.%s: the packaged unit parsed to nothing" % (name, suffix))
                missing, extra = sorted(packaged - rendered), sorted(rendered - packaged)
                if missing or extra:
                    wrong.append("%s.%s: NOT RENDERED %s; RENDERED BUT NOT PACKAGED %s" % (name, suffix, missing, extra))
        self.assertEqual(wrong, [], "the one template does not render the packaged body:\n" + "\n".join(wrong))

    def test_the_armed_eight_are_among_them_and_nothing_says_network_online(self):
        bodies = self._render_all()
        armed = sorted(n for n, u in self.units.items() if u["arm"] == "armed-by-default")
        self.assertEqual(len(armed), 9, armed)   # embed-backfill armed by the owner's ruling, 2026-10-15
        for n in armed:
            self.assertIn(n + ".service", bodies)
            self.assertIn(n + ".timer", bodies)
        self.assertFalse(any("network-online" in b for b in bodies.values()),
                         "network-online.target: 0 of 17 packaged units carry it")
        # the sensor's body, spelled out (the unit the old template got most wrong)
        orl = unit_directives(bodies["organ-run-log.service"])
        for want in (("[Unit]", "After=default.target"), ("[Service]", "Nice=10"),
                     ("[Service]", "Environment=PATH=%s/.local/bin:/usr/local/bin:/usr/bin:/bin" % self.home)):
            self.assertIn(want, orl)
        self.assertFalse(any(k.startswith(("EnvironmentFile=", "WorkingDirectory=", "TimeoutStartSec=")) for _s, k in orl),
                         "organ-run-log gained a line the packaged unit does not carry: %s" % sorted(orl))
        self.assertIn(("[Timer]", "RandomizedDelaySec=30"), unit_directives(bodies["organ-run-log.timer"]))


class TestInstallersReadTheRosterAndCarryNoTable(unittest.TestCase):

    def setUp(self):
        _require_checkout()

    def test_no_hand_table_in_either_installer(self):
        for f in (SH, CRONS_SH):
            text = f.read_text(encoding="utf-8")
            rows = [l for l in text.splitlines()
                    if re.match(r'^\s*"[a-z-]+\|[^|]*\|', l)]          # a CRON_DEFS-style literal row
            self.assertEqual(rows, [], "%s carries a hand table: %s" % (f.name, rows[:3]))
            self.assertIn("cron_roster.json", text, "%s does not name the roster" % f.name)
            self.assertIn("cron_roster.py", text, "%s does not call the one reader" % f.name)
        sh = CRONS_SH.read_text(encoding="utf-8")
        hand = re.findall(r"\binstall_linux\s+([a-z][a-z0-9-]+)\b", sh)
        self.assertEqual(hand, [], "install_crons.sh still arms by hand: %s" % hand)

    def test_with_crons_dry_run_names_every_armed_unit_with_a_laid_target(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="roster_dry_", dir=str(SCRATCH)))
        try:
            env = {**ENV, "HOME": str(home), "WIZARD_CLAUDE_HOME": str(home / ".claude"),
                   "WIZARD_ENV_FILE": str(home / ".voidloom" / "wizard-library-env"),
                   "WIZARD_EMBED_MODEL": "nomic", "TMPDIR": str(home)}
            # --thinker off: arming timers that think needs the owner's answer to the overnight-thinker question (M10, the
            # model-choice round); without a terminal it comes as a flag, or the run is refused rc 2 before anything
            p = subprocess.run(["bash", str(SH), "--with-crons", "--dry-run", "--tier0-only", "--force-cpu", "--thinker", "off"],
                               capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-3000:])
            lines = re.findall(r"DRY: ([a-z0-9-]+)\.service ExecStart=(\S+) -u (\S+)", p.stdout)
            armed = {n for n, _py, _t in lines}
            expected = {n for n, u in json.loads(ROSTER.read_text())["units"].items() if u["arm"] == "armed-by-default"}
            self.assertEqual(armed, expected, "the dry run armed %s; the roster says %s" % (sorted(armed), sorted(expected)))
            self.assertIn("sleep-loop", armed)
            laid, note = laid_brain_files()
            brain = str(home / ".claude" / "memory")
            for n, _py, tgt in lines:
                self.assertTrue(tgt.startswith(brain + "/"), "%s: ExecStart target outside the brain: %s" % (n, tgt))
                rel = tgt[len(brain) + 1:]
                self.assertIn(rel, laid or set(), "%s: ExecStart target %s is not laid (%s)" % (n, rel, note))
            # a dry run writes nothing, anywhere under the scratch home
            written = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
            self.assertEqual(written, [], "the dry run wrote: %s" % written[:10])
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

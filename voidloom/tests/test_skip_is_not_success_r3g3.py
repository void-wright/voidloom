# -*- coding: utf-8 -*-
"""Round 3 G3, item 2: a skipped night never reads as success -- to systemd, to the run ledger, to the doctor, or
behind the organ-yield sentinel's HEALTHY.

MEASURED by G1 (Round 3, r3_g1/sleeplock/skip_reads_as.json): the packaged sleep-loop unit runs
`claude_memory.py sleep --config <sidecar>`; on a brain whose run-lock was held it printed {"status": "skipped"} and
EXITED 0 -> systemd Result=success -> organ_run_log "success". The ledger already files exit 75 (EX_TEMPFAIL) as
"skipped-lock" for the dream units; the sleep loop's host path never produced it.

Bound here on a laid brain under a scratch HOME, through the sleep-loop unit's OWN ExecStart (parsed from
cron/wizard-library-sleep-loop.service, run as /usr/bin/env python3 -u ... in its WorkingDirectory):
  * a held brain -> the unit's command exits 75; systemd's documented mapping (exit status not 0 and not in the
    unit's SuccessExitStatus -> Result=exit-code, ExecMainStatus=<status>) fed through organ_run_log.observe() files
    the run "skipped-lock", never "success" -- the systemd boundary is the ONE faked piece (systemctl show /
    systemd-analyze calendar answered in-process), the ledger's own discover -> show -> classify -> INSERT runs;
  * the ledger report and `voidloom doctor` NAME the skip;
  * the organ-yield sentinel keeps its 3-day cadence verdict (two nights of output then a skipped night is still
    HEALTHY, by design) and SAYS the skipped night in its detail; with no skip, its detail says nothing of one;
  * an error (a run-lock the brain's directory cannot hold) is not a success either: exit 1, named.
RED on the pre-G3 bytes: the unit exits 0 on a skip and the ledger files "success"; nothing names the skip.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
UNIT = "wizard-library-sleep-loop.service"
TIMER = "wizard-library-sleep-loop.timer"

# The brain's own run-lock, held by another process of THIS brain (the position the host's loop met on 2026-10-01).
HOLD = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import sleep_loop as s
db, ready, release = sys.argv[2:5]
path = s.run_lock_path(db)[0]
assert s._acquire_lock(path), "the holder could not take the brain's lock"
open(ready, "w").close()
t0 = time.monotonic()
while not os.path.exists(release) and time.monotonic() - t0 < 120:
    time.sleep(0.05)
s._release_lock(path)
'''

# organ_run_log.observe() with ONLY the systemd boundary answered in-process: `systemctl --user show` from a state file
# (the Result/ExecMainStatus systemd would report for the exit status the unit's command really returned) and
# `systemd-analyze calendar` as daily occurrences of the timer's HH:MM:SS. Everything else is the ledger's own code.
LEDGER = r'''
import json, re, sys
from datetime import datetime, timedelta
sys.path.insert(0, sys.argv[1])
import organ_run_log as o
db, unit_dir, state_path = sys.argv[2:5]
st = json.load(open(state_path))
FMT = "%a %Y-%m-%d %H:%M:%S"
def fake_run(cmd, timeout=20):
    if cmd[:3] == ["systemctl", "--user", "show"]:
        vals = st.get(cmd[3], {})
        return "".join("%s=%s\n" % (c[2:], vals.get(c[2:], "")) for c in cmd[4:])
    if cmd[:2] == ["systemd-analyze", "calendar"]:
        base = datetime.strptime([c for c in cmd if c.startswith("--base-time=")][0][12:], "%Y-%m-%d %H:%M:%S")
        n = int([c for c in cmd if c.startswith("--iterations=")][0][13:])
        h, m, s = map(int, re.search(r"(\d+):(\d+):(\d+)", cmd[-1]).groups())
        t = base.replace(hour=h, minute=m, second=s, microsecond=0)
        t = t if t >= base else t + timedelta(days=1)
        return "".join("Iteration #%d: %s UTC\n" % (i + 1, (t + timedelta(days=i)).strftime(FMT)) for i in range(n))
    raise o.ProbeError("unexpected command %s" % cmd)
o._run = fake_run
obs = o.observe(db, unit_dir=unit_dir)
rows = o.ledger_report(db)
print(json.dumps({"observe": obs, "rows": [r.__dict__ for r in rows], "text": o.render_report(rows)}, default=str))
'''


def _unit_text(name):
    return (ROOT / "cron" / name).read_text(encoding="utf-8")


def _systemd_result(rc, unit_text):
    """systemd.service(5): a main process that exits with a status other than 0 and not listed in SuccessExitStatus=
    leaves Result=exit-code; ExecMainStatus is the exit status."""
    ok = {0}
    for line in re.findall(r"^SuccessExitStatus=(.+)$", unit_text, re.M):
        ok |= {int(x) for x in line.split() if x.isdigit()}
    return ("success" if rc in ok else "exit-code"), str(rc)


class _Brain:
    """A laid brain under its own scratch HOME, with the packaged sleep-loop unit pair installed into a scratch unit
    dir (the sentinel's invoker oracle and the ledger's declared set read it) and the packaged sidecar config."""

    def __init__(self, root):
        self.home = root / "home"
        self.dir = self.home / ".claude" / "memory"
        self.dir.mkdir(parents=True)
        (self.dir / "organs").symlink_to(ORGANS, target_is_directory=True)
        self.db = self.dir / "claude_memory.db"
        shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(self.db))
        (self.home / ".voidloom").mkdir()
        shutil.copy2(str(ROOT / "cron" / "sleep-loop-config.json"), str(self.home / ".voidloom" / "sleep-loop-config.json"))
        self.units = self.home / ".config" / "systemd" / "user"
        self.units.mkdir(parents=True)
        for f in (UNIT, TIMER):
            shutil.copy2(str(ROOT / "cron" / f), str(self.units / f))
        self.tmp = self.home / "tmp"
        self.tmp.mkdir()
        r = subprocess.run([sys.executable, "-B", str(ORGANS / "schema_migrator.py"), "apply", "--db", str(self.db)],
                           capture_output=True, text=True, env=self.env(), timeout=300)
        assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]

    def env(self):
        e = dict(os.environ)
        for k in list(e):
            if (k.startswith(("WIZARD_", "XDG_", "DBUS_")) and k != "WIZARD_CUDA_DEVICE") or k in (
                    "VOIDLOOM_ENV_DIR", "HF_HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_MEMORY_DB"):
                e.pop(k)
        e.update(HOME=str(self.home), TMPDIR=str(self.tmp), PYTHONDONTWRITEBYTECODE="1",
                 PATH="%s:/usr/bin:/bin" % os.path.dirname(sys.executable),
                 PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",     # the unit's own Environment=
                 XDG_CONFIG_HOME=str(self.home / ".config"), XDG_STATE_HOME=str(self.home / ".local" / "state"),
                 XDG_DATA_HOME=str(self.home / ".local" / "share"), XDG_CACHE_HOME=str(self.home / ".cache"))
        return e

    def exec_start(self):
        """The unit's OWN ExecStart, %h = this HOME, in its WorkingDirectory."""
        text = _unit_text(UNIT)
        argv = [a.replace("%h", str(self.home)) for a in re.search(r"^ExecStart=(.+)$", text, re.M).group(1).split()]
        wd = re.search(r"^WorkingDirectory=(.+)$", text, re.M).group(1).replace("%h", str(self.home))
        p = subprocess.run(argv, capture_output=True, text=True, env=self.env(), cwd=wd, timeout=600)
        return p, _last_json(p.stdout)

    def ledger(self, rc):
        """File the run the way systemd would report it, through organ_run_log.observe()."""
        result, status = _systemd_result(rc, _unit_text(UNIT))
        stamp = (datetime.now() - timedelta(minutes=1)).strftime("%a %Y-%m-%d %H:%M:%S") + " UTC"
        state = {TIMER: {"UnitFileState": "enabled", "ActiveState": "active", "LastTriggerUSec": stamp,
                         "NextElapseUSecRealtime": ""},
                 UNIT: {"Result": result, "ExecMainStatus": status}}
        sp = self.home / "systemd_state.json"
        sp.write_text(json.dumps(state), encoding="utf-8")
        p = subprocess.run([sys.executable, "-B", "-c", LEDGER, str(ORGANS), str(self.db), str(self.units), str(sp)],
                           capture_output=True, text=True, env=self.env(), timeout=300)
        assert p.returncode == 0, p.stderr[-1500:]
        return json.loads(p.stdout.strip().splitlines()[-1])

    def held(self):
        return _Held(self)


def _last_json(out):
    s = out.strip()
    i = s.find("{")
    try:
        return json.loads(s[i:]) if i >= 0 else {}
    except ValueError:
        return {}


class _Held:
    def __init__(self, b):
        self.b, self.ready, self.release = b, b.home / "h.ready", b.home / "h.release"

    def __enter__(self):
        self.p = subprocess.Popen([sys.executable, "-B", "-c", HOLD, str(ORGANS), str(self.b.db), str(self.ready),
                                   str(self.release)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=self.b.env())
        t0 = time.monotonic()
        while not self.ready.exists():
            if self.p.poll() is not None:
                pytest.fail("the holder never took the brain's lock: %s" % self.p.communicate()[1][-600:])
            assert time.monotonic() - t0 < 120
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.release.touch()
        self.p.communicate(timeout=120)
        return False


@pytest.fixture
def brain():
    if not os.path.exists("/usr/bin/env"):
        pytest.skip("the unit's ExecStart runs /usr/bin/env")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="skipledger_r3g3_", dir=str(SCRATCH)))
    b = _Brain(root)
    yield b
    try:
        os.chmod(str(b.dir), stat.S_IRWXU)
    except OSError:
        pass
    shutil.rmtree(str(root), ignore_errors=True)


@pytest.fixture
def skipped(brain):
    """A skipped night, end to end: the held brain, the unit's command, the ledger's row."""
    with brain.held():
        p, res = brain.exec_start()
    led = brain.ledger(p.returncode)
    return brain, p, res, led


def test_the_sleep_units_own_execstart_exits_75_on_a_skip_and_the_ledger_files_it_skipped(skipped):
    b, p, res, led = skipped
    assert res.get("status") == "skipped", (res, p.stderr[-800:])
    assert p.returncode == 75, ("a skipped sleep night exited %s: systemd reads 0 as Result=success" % p.returncode)
    row = next(r for r in led["rows"] if r["unit_name"] == TIMER)
    assert row["last_run_result"] == "skipped-lock", ("the run ledger filed a skipped night as %r"
                                                      % row["last_run_result"])
    assert led["observe"]["runs_recorded"] == 1, led["observe"]


def test_the_ledger_report_names_the_skip(skipped):
    _, _, _, led = skipped
    row = next(r for r in led["rows"] if r["unit_name"] == TIMER)
    assert any(n.startswith("SKIPPED") for n in row["notes"]), row["notes"]
    assert "SKIPPED: its last run" in led["text"] and "nothing ran and nothing failed" in led["text"], led["text"]


DOCTOR = r'''
import contextlib, io, json, sys
sys.path.insert(0, sys.argv[1])
from voidloom.cli import main
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        main(["doctor", "--json", "--db", sys.argv[2]])
    except SystemExit:
        pass
out = buf.getvalue()
print(json.dumps(json.loads(out[out.find("{"):])["data"].get("organ_runs")))
'''


def test_doctor_names_the_skip(skipped):
    b, _, _, _ = skipped
    p = subprocess.run([sys.executable, "-B", "-c", DOCTOR, str(ROOT), str(b.db)], capture_output=True, text=True,
                       env=b.env(), cwd=str(b.home), timeout=300)
    assert p.returncode == 0, p.stderr[-1500:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got is not None, "voidloom doctor says nothing about skipped runs"
    assert "SKIPPED" in got["line"] and UNIT in got["line"], got["line"]
    assert [r["result"] for r in got["skips"]] == ["skipped-lock"], got["skips"]


SENTINEL = r'''
import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
db, with_skip = sys.argv[2], sys.argv[3] == "1"
c = sqlite3.connect(db)
for i, days in enumerate((2.0, 1.0)):              # two nights of OUTPUT, then tonight (the skip, or nothing)
    mid = c.execute("INSERT INTO memories (content, summary, category, importance, kind) VALUES (?,?,?,?,?)",
                    ("episodic %d" % i, "ep %d" % i, "insight", 0.5, "episodic")).lastrowid
    c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id, created_at) "
              "VALUES (?, ?, ?, datetime('now', ?))", (mid, mid, i, "-%s days" % days))
if not with_skip:
    c.execute("DELETE FROM organ_run_log")
c.commit()
c.close()
import organ_yield_sentinel as s
rs, meta = s.scan(db)
r = [x for x in rs if x.organ == "sleep_loop.consolidate_episodics"][0]
print(json.dumps({"verdict": r.verdict, "detail": r.detail, "invoker": r.invoker}))
'''


def _sentinel(b, with_skip):
    p = subprocess.run([sys.executable, "-B", "-c", SENTINEL, str(ORGANS), str(b.db), "1" if with_skip else "0"],
                       capture_output=True, text=True, env=b.env(), timeout=300)
    assert p.returncode == 0, p.stderr[-1500:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_the_sentinel_keeps_its_verdict_and_says_the_skipped_night(skipped):
    b, _, _, _ = skipped
    r = _sentinel(b, True)
    assert r["invoker"] == UNIT, r
    assert r["verdict"] == "HEALTHY", ("the 3-day cadence verdict changed", r)      # by design: unchanged
    assert "1 SKIPPED run in the last 3d" in r["detail"] and "skipped-lock" in r["detail"], r["detail"]


def test_the_sentinel_says_no_skip_when_there_was_none(brain):
    r = _sentinel(brain, False)
    assert r["verdict"] == "HEALTHY" and "SKIPPED" not in r["detail"], r


def test_a_sleep_error_is_not_a_success_either(brain):
    """A run-lock the brain's directory cannot hold: the unit's command names it and exits 1 (it exited 0)."""
    os.chmod(str(brain.dir), stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(str(brain.dir), os.W_OK):
            pytest.skip("this account can write a 0500 directory (root?)")
        p, res = brain.exec_start()
    finally:
        os.chmod(str(brain.dir), stat.S_IRWXU)
    assert res.get("status") == "error" and ".sleep-loop.lock" in (res.get("reason") or ""), (res, p.stderr[-800:])
    assert p.returncode == 1, ("an errored sleep night exited %s" % p.returncode)

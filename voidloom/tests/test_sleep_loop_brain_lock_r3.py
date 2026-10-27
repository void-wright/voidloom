# -*- coding: utf-8 -*-
"""Round 3 G1 (coordinator item, found live on the host): the sleep loop's run-lock is the BRAIN's, never the machine's.

MEASURED on the reference host, 2026-10-01 22:30:09: the packaged config (cron/sleep-loop-config.json) named the
machine-global lock "/tmp/wizard-library-sleep-loop.lock". A test harness ran a sleep loop on a SCRATCH brain under a
scratch HOME, took that lock, and the host's own nightly loop SKIPPED: {"status": "skipped", "reason": "Lock file held
by another process", "lock_file": "/tmp/wizard-library-sleep-loop.lock"}, exit 0. One brain's night was lost to
another brain's run.

Bound here, on two laid brains under two scratch HOMEs (the package's own blank brain; the organs are this tree's; the
real host brain and the real /tmp lock are never touched -- the legacy literal is reproduced as the SAME absolute path
in both configs, which is the shape that collided):
  * a scratch-HOME loop that is RUNNING never blocks the host brain's loop, and the reverse (the incident, both ways),
    through the host's own command: `claude_memory.py sleep --config <the sidecar config>`;
  * a config that still names the legacy lock keeps working (both loops run) -- it just stops colliding;
  * a second loop on the SAME brain is still refused, even from another TMPDIR, and sleep_loop.py's own CLI says so
    with exit 75 (EX_TEMPFAIL, the run ledger's "skipped-lock"), never 0;
  * the lock lives beside the brain (its real path) and nowhere a tempdir decides; an explicit non-legacy path is the
    owner's and is honoured; a brain whose directory cannot hold the lock gets a named error, never a crash.
RED on the Round 2 bytes: the scratch loop is SKIPPED in both directions, and the CLI exits 0 on a skip.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
LEGACY_LITERAL = "/tmp/wizard-library-sleep-loop.lock"     # what every pre-Round-3 sidecar config carries

# The "host" loop, paused INSIDE its run while it holds whatever lock this tree's run_sleep_cycle takes -- the exact
# position the storm harness's loop was in at 22:30:09. It is version-agnostic on purpose: both the old and the new
# run_sleep_cycle acquire the lock and then open the brain with _get_conn, so the pause sits after the acquisition.
HOLDER = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import sleep_loop as s
db, cfg_path, ready, release = sys.argv[2:6]
orig = s._get_conn
def held(p):
    open(ready, "w").close()
    t0 = time.monotonic()
    while not os.path.exists(release) and time.monotonic() - t0 < 120:
        time.sleep(0.05)
    return orig(p)
s._get_conn = held
res = s.run_sleep_cycle(db, config=json.loads(open(cfg_path).read()))
print(json.dumps({"status": res.get("status"), "run_id": res.get("run_id"), "lock_file": res.get("lock_file")}))
'''


class _Brain:
    """A laid brain under its own scratch HOME: <home>/.claude/memory/{claude_memory.db, organs -> this tree's organs}."""

    def __init__(self, root: Path, name: str):
        self.home = root / name
        self.dir = self.home / ".claude" / "memory"
        self.dir.mkdir(parents=True)
        (self.dir / "organs").symlink_to(ORGANS, target_is_directory=True)
        self.db = self.dir / "claude_memory.db"
        shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(self.db))
        self.tmp = self.home / "tmp"
        self.tmp.mkdir()
        self.cfg = self.home / "sleep-loop-config.json"
        r = subprocess.run([sys.executable, "-B", str(ORGANS / "schema_migrator.py"), "apply", "--db", str(self.db)],
                           capture_output=True, text=True, env=self.env(), timeout=300)
        assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]

    def env(self, tmpdir=None):
        e = dict(os.environ)          # this brain's HOME only: no variable may point a child at another brain or env file
        for k in list(e):
            if (k.startswith(("WIZARD_", "XDG_")) and k != "WIZARD_CUDA_DEVICE") or k in (
                    "VOIDLOOM_ENV_DIR", "HF_HOME", "CLAUDE_CODE_OAUTH_TOKEN"):
                e.pop(k)
        e.update(HOME=str(self.home), TMPDIR=str(tmpdir or self.tmp), PYTHONDONTWRITEBYTECODE="1",
                 XDG_CONFIG_HOME=str(self.home / ".config"), XDG_STATE_HOME=str(self.home / ".local" / "state"),
                 XDG_DATA_HOME=str(self.home / ".local" / "share"), XDG_CACHE_HOME=str(self.home / ".cache"))
        return e

    def write_cfg(self, lock_file):
        d = {"consolidation_mode": "heuristic"}
        if lock_file is not None:
            d["lock_file"] = str(lock_file)
        self.cfg.write_text(json.dumps(d), encoding="utf-8")

    def host_command(self, tmpdir=None):
        """The host unit's own ExecStart: claude_memory.py sleep --config <sidecar config> (the laid copy)."""
        p = subprocess.run([sys.executable, "-B", str(self.dir / "organs" / "claude_memory.py"), "sleep",
                            "--config", str(self.cfg)], capture_output=True, text=True, env=self.env(tmpdir),
                           cwd=str(self.dir), timeout=300)
        return p, _last_json(p.stdout)

    def module_cli(self, tmpdir=None):
        p = subprocess.run([sys.executable, "-B", str(ORGANS / "sleep_loop.py"), "--run", str(self.db),
                            "--config", str(self.cfg)], capture_output=True, text=True, env=self.env(tmpdir),
                           cwd=str(self.dir), timeout=300)
        return p, _last_json(p.stdout)


def _last_json(out):
    s = out.strip()
    try:
        return json.loads(s)
    except ValueError:
        i = s.rfind("\n{")
        return json.loads(s[i + 1:]) if i >= 0 else {}


class _Held:
    """Context: the brain's own loop, paused mid-run holding its run-lock."""

    def __init__(self, brain: _Brain, tag: str):
        self.b, self.ready, self.release = brain, brain.home / ("%s.ready" % tag), brain.home / ("%s.release" % tag)

    def __enter__(self):
        self.p = subprocess.Popen([sys.executable, "-B", "-c", HOLDER, str(ORGANS), str(self.b.db), str(self.b.cfg),
                                   str(self.ready), str(self.release)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=self.b.env(), cwd=str(self.b.dir))
        t0 = time.monotonic()
        while not self.ready.exists():
            if self.p.poll() is not None:
                out, err = self.p.communicate()
                pytest.fail("the holder's own loop never reached its run (it was refused the lock?): %s %s"
                            % (out[-600:], err[-600:]))
            assert time.monotonic() - t0 < 120, "the holder did not reach its run within 120 s"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.release.touch()
        out, err = self.p.communicate(timeout=180)
        self.result = _last_json(out)
        self.rc, self.err = self.p.returncode, err
        return False


@pytest.fixture
def two():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="sleeplock_r3_", dir=str(SCRATCH)))
    shared = root / "shared-tmp"                    # stands in for /tmp: one absolute path both configs name
    shared.mkdir()
    host, scratch = _Brain(root, "host-home"), _Brain(root, "scratch-home")
    for b in (host, scratch):
        b.write_cfg(shared / "wizard-library-sleep-loop.lock")
    yield host, scratch, shared
    for d in (host.dir, root):
        try:
            os.chmod(str(d), stat.S_IRWXU)
        except OSError:
            pass
    shutil.rmtree(str(root), ignore_errors=True)


def _ran(res):
    return res.get("status") not in (None, "skipped", "error") and bool(res.get("run_id"))


def test_a_running_scratch_home_loop_never_takes_the_host_brains_lock(two):
    """The incident's direction: the harness's loop is mid-run; the host's 22:30 loop must still run."""
    host, scratch, _ = two
    with _Held(scratch, "scratch") as h:
        p, res = host.host_command()
        assert _ran(res), ("the host brain's loop was refused while a SCRATCH-HOME loop ran", res, p.stderr[-800:])
        assert Path(res["config"]["lock_file"]).parent == host.dir.resolve(), res["config"]["lock_file"]
    assert _ran(h.result), ("the scratch loop's own run did not complete", h.result, h.err[-600:])


def test_a_running_host_loop_never_blocks_a_scratch_home_loop(two):
    host, scratch, _ = two
    with _Held(host, "host") as h:
        p, res = scratch.host_command()
        assert _ran(res), ("a scratch-HOME loop was refused while the HOST brain's loop ran", res, p.stderr[-800:])
        assert Path(res["config"]["lock_file"]).parent == scratch.dir.resolve(), res["config"]["lock_file"]
    assert _ran(h.result), (h.result, h.err[-600:])


def test_a_legacy_config_keeps_working_and_says_which_lock_it_used(two):
    host, _, shared = two
    p, res = host.host_command()
    assert p.returncode == 0 and _ran(res), (res, p.stderr[-800:])
    assert res["config"]["lock_file"] == str(host.db.resolve()) + ".sleep-loop.lock", res["config"]["lock_file"]
    assert "legacy machine-global lock" in (res.get("lock_note") or ""), res.get("lock_note")
    assert not (shared / "wizard-library-sleep-loop.lock").exists(), "the legacy machine-global lock was created"


def test_the_same_brain_is_still_refused_even_from_another_tmpdir_and_the_cli_exits_75(two):
    host, _, _ = two
    other_tmp = host.home / "another-tmp"
    other_tmp.mkdir()
    with _Held(host, "host"):
        p, res = host.module_cli(tmpdir=other_tmp)
        assert res.get("status") == "skipped", ("a second loop ran on a brain whose loop was mid-run", res)
        assert res.get("lock_file") == str(host.db.resolve()) + ".sleep-loop.lock", res
        assert p.returncode == 75, ("a skipped run must not exit 0 (it read as success to the run ledger)",
                                    p.returncode)
        p2, res2 = host.host_command(tmpdir=other_tmp)
        assert res2.get("status") == "skipped", res2


PURE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import sleep_loop as sl
a, b, link, mine, packaged = sys.argv[2:7]
pk = json.loads(open(packaged).read()).get("lock_file")
print(json.dumps({"a": sl.run_lock_path(a), "b": sl.run_lock_path(b), "link": sl.run_lock_path(link),
                  "literal": sl.run_lock_path(a, %r), "mine": sl.run_lock_path(a, mine),
                  "packaged": sl.run_lock_path(a, pk), "default": sl.DEFAULT_CONFIG.get("lock_file")}))
''' % LEGACY_LITERAL


def test_the_lock_is_keyed_by_the_brains_real_path(tmp_path):
    a, b = tmp_path / "a" / "claude_memory.db", tmp_path / "b" / "claude_memory.db"
    for d in (a, b):
        d.parent.mkdir()
        d.write_bytes(b"")
    link = tmp_path / "link.db"
    link.symlink_to(a)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(tmp_path))
    p = subprocess.run([sys.executable, "-B", "-c", PURE, str(ORGANS), str(a), str(b), str(link),
                        str(tmp_path / "mine.lock"), str(ROOT / "cron" / "sleep-loop-config.json")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-1200:]
    d = json.loads(p.stdout.strip().splitlines()[-1])
    la = str(a.resolve()) + ".sleep-loop.lock"
    assert d["a"] == [la, None], d["a"]
    assert d["b"][0] != la, "two brains share one run-lock"
    assert d["link"][0] == la, "a symlinked spelling of one brain got a second lock"
    assert d["literal"][0] == la and LEGACY_LITERAL in (d["literal"][1] or ""), d["literal"]
    assert d["mine"] == [str(tmp_path / "mine.lock"), None], "an explicit non-legacy path was not honoured"
    assert d["packaged"][0] == la, ("the packaged config's lock is not this brain's", d["packaged"])
    assert not d["default"], ("the default names a lock a tempdir decides", d["default"])


def test_a_brain_whose_directory_cannot_hold_the_lock_gets_a_named_error(two):
    host, _, _ = two
    host.write_cfg(None)
    os.chmod(str(host.dir), stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(str(host.dir), os.W_OK):
            pytest.skip("this account can write a 0500 directory (root?)")
        p, res = host.module_cli()
    finally:
        os.chmod(str(host.dir), stat.S_IRWXU)
    assert res.get("status") == "error" and ".sleep-loop.lock" in (res.get("reason") or ""), (res, p.stderr[-800:])
    assert p.returncode == 1 and "Traceback" not in p.stderr, p.stderr[-800:]

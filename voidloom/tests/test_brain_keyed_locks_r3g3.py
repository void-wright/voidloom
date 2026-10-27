# -*- coding: utf-8 -*-
"""Round 3 G3, item 1: every per-machine organ lock is keyed by the BRAIN, never by /tmp, TMPDIR or a fixed name.

G1 moved the sleep loop's run-lock beside the brain (<realpath(db)>.sleep-loop.lock) after the host's 2026-10-01 22:30
loop SKIPPED because a scratch-HOME harness held the machine-global /tmp lock. The dream cycle carried the same shape
three ways: memory_dreaming.py defaulted to gettempdir()/wizard-library-dreaming.lock, cron/setup-dreaming-cron.sh
wrote the absolute "/tmp/wizard-library-dreaming.lock" into the config it creates, and dream_pipeline.py (the
packaged nightly and daytime units) defaulted to gettempdir()/voidloom-dream-pipeline.lock -- so a harness with
TMPDIR unset held the HOST's dream lock, and two runners of one brain with different TMPDIRs did not exclude.
wizard_config.lock_dir defaulted to "/tmp" ("caller appends service name"): no caller today, the same trap tomorrow.

Bound here, on laid brains under scratch HOMEs (the package's blank brain, this tree's organs; the real /tmp and
the host brain are never touched -- "/tmp" is played by ONE shared scratch dir that both runners use as TMPDIR, which
is what the systemd unit and a harness with TMPDIR unset both resolve gettempdir() to):
  * a scratch-HOME dream that is RUNNING never blocks the host brain's dream, and the reverse, through the packaged
    unit's own ExecStart;
  * two brains whose configs both name a legacy lock never block each other, keep working, and say which lock ran;
  * the same brain is still refused from another TMPDIR, and EVERY dream entry point exits 75 on that skip;
  * the lock is the brain's real path + ".dreaming.lock"; an unwritable brain dir is a named error;
  * setup-dreaming-cron.sh writes a config that names no lock; wizard_config's lock dir is the brain's;
  * a lint over every shipped file: no lock path built from /tmp, TMPDIR or gettempdir() (with its falsifier).
"""
import ast
import json
import os
import re
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
SUFFIX = ".dreaming.lock"

# A dream cycle paused INSIDE its run, holding whatever lock this tree's dream_pipeline.run_dream_cycle takes. Both the
# old and the new sequencer acquire the lock and then open the brain with _get_conn, so the pause sits after it.
HOLDER = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import dream_pipeline as dp
db, cfg_path, ready, release = sys.argv[2:6]
orig, first = dp._get_conn, [True]
def held(p):
    if first[0]:
        first[0] = False
        open(ready, "w").close()
        t0 = time.monotonic()
        while not os.path.exists(release) and time.monotonic() - t0 < 120:
            time.sleep(0.05)
    return orig(p)
dp._get_conn = held
cfg = json.loads(open(cfg_path).read()) if cfg_path != "-" else None
res = dp.run_dream_cycle(db, config=cfg)
print(json.dumps({"status": res.get("status"), "lock_file": res.get("lock_file")}))
'''


def _exec_start(unit):
    """The packaged unit's ExecStart, as argv with %h left for the caller (the unit's OWN command, not a paraphrase)."""
    text = (ROOT / "cron" / unit).read_text(encoding="utf-8")
    line = re.search(r"^ExecStart=(.+)$", text, re.M).group(1).split()
    assert line[:3] == ["/usr/bin/env", "python3", "-u"], line
    return line[3:]


class _Brain:
    """A laid brain under its own scratch HOME: <home>/.claude/memory/{claude_memory.db, organs -> this tree's organs}."""

    def __init__(self, root, name, shared_tmp):
        self.home = root / name
        self.dir = self.home / ".claude" / "memory"
        self.dir.mkdir(parents=True)
        (self.dir / "organs").symlink_to(ORGANS, target_is_directory=True)
        self.db = self.dir / "claude_memory.db"
        shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(self.db))
        self.tmp = shared_tmp
        self.cfg = self.home / "dreaming-config.json"
        self.stub = self.home / "stubbin"          # a `claude` that records any call: no live model call can happen
        self.stub.mkdir()
        (self.stub / "claude").write_text("#!/bin/sh\necho called >> '%s'\nexit 3\n" % (self.home / "claude_calls"))
        os.chmod(str(self.stub / "claude"), 0o755)
        r = subprocess.run([sys.executable, "-B", str(ORGANS / "schema_migrator.py"), "apply", "--db", str(self.db)],
                           capture_output=True, text=True, env=self.env(), timeout=300)
        assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]

    def env(self, tmpdir=None):
        e = dict(os.environ)        # this brain's HOME only: no variable may point a child at another brain or env file
        for k in list(e):
            if (k.startswith(("WIZARD_", "XDG_")) and k != "WIZARD_CUDA_DEVICE") or k in (
                    "VOIDLOOM_ENV_DIR", "HF_HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
                e.pop(k)
        e.update(HOME=str(self.home), TMPDIR=str(tmpdir or self.tmp), PYTHONDONTWRITEBYTECODE="1",
                 PATH="%s:%s:/usr/bin:/bin" % (self.stub, os.path.dirname(sys.executable)),
                 XDG_CONFIG_HOME=str(self.home / ".config"), XDG_STATE_HOME=str(self.home / ".local" / "state"),
                 XDG_DATA_HOME=str(self.home / ".local" / "share"), XDG_CACHE_HOME=str(self.home / ".cache"))
        return e

    def write_cfg(self, lock_file):
        d = {"divergent_pairs": 2, "close_context_pairs": 2}
        if lock_file is not None:
            d["lock_file"] = str(lock_file)
        self.cfg.write_text(json.dumps(d), encoding="utf-8")

    def run(self, argv, tmpdir=None):
        p = subprocess.run([sys.executable, "-B"] + argv, capture_output=True, text=True, env=self.env(tmpdir),
                           cwd=str(self.dir), timeout=600)
        return p, _last_json(p.stdout)

    def unit(self, name, tmpdir=None):
        """The packaged unit's own ExecStart (%h = this brain's HOME)."""
        return self.run([a.replace("%h", str(self.home)) for a in _exec_start(name)], tmpdir)

    def organ(self, script, *args, tmpdir=None):
        return self.run([str(self.dir / "organs" / script)] + [str(a) for a in args], tmpdir)

    def calls(self):
        f = self.home / "claude_calls"
        return f.read_text().count("called") if f.exists() else 0


def _last_json(out):
    s = out.strip()
    i = s.find("{")
    try:
        return json.loads(s[i:]) if i >= 0 else {}
    except ValueError:
        j = s.rfind("\n{")
        try:
            return json.loads(s[j + 1:]) if j >= 0 else {}
        except ValueError:
            return {}


class _Held:
    """Context: the brain's own dream cycle, paused mid-run holding its lock."""

    def __init__(self, brain, tag, cfg=False):
        self.b, self.cfg = brain, cfg
        self.ready, self.release = brain.home / ("%s.ready" % tag), brain.home / ("%s.release" % tag)

    def __enter__(self):
        self.p = subprocess.Popen([sys.executable, "-B", "-c", HOLDER, str(ORGANS), str(self.b.db),
                                   str(self.b.cfg) if self.cfg else "-", str(self.ready), str(self.release)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.b.env(),
                                  cwd=str(self.b.dir))
        t0 = time.monotonic()
        while not self.ready.exists():
            if self.p.poll() is not None:
                out, err = self.p.communicate()
                pytest.fail("the holder's own dream never reached its run (refused the lock?): %s %s"
                            % (out[-600:], err[-600:]))
            assert time.monotonic() - t0 < 120, "the holder did not reach its run within 120 s"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.release.touch()
        out, err = self.p.communicate(timeout=300)
        self.result, self.rc, self.err = _last_json(out), self.p.returncode, err
        return False


@pytest.fixture
def two():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="dreamlock_r3g3_", dir=str(SCRATCH)))
    shared = root / "shared-tmp"           # stands in for /tmp: the TMPDIR both runners resolve
    shared.mkdir()
    host, scratch = _Brain(root, "host-home", shared), _Brain(root, "scratch-home", shared)
    yield host, scratch, shared
    for d in (host.dir, root):
        try:
            os.chmod(str(d), stat.S_IRWXU)
        except OSError:
            pass
    shutil.rmtree(str(root), ignore_errors=True)


def _ran(p, res):
    return p.returncode == 0 and res.get("status") == "ok"


def test_a_running_scratch_home_dream_never_takes_the_host_brains_dream_lock(two):
    """The incident's direction: a harness's dream is mid-run; the host's 22:00 unit must still dream."""
    host, scratch, shared = two
    with _Held(scratch, "scratch") as h:
        p, res = host.unit("wizard-library-dreaming.service")
        assert _ran(p, res), ("the host brain's dream was refused while a SCRATCH-HOME dream ran", res, p.stderr[-800:])
    assert h.result.get("status") == "ok", ("the scratch dream's own run did not complete", h.result, h.err[-600:])
    assert not os.listdir(str(shared)), ("a lock was left in the machine's temp dir", os.listdir(str(shared)))
    assert host.calls() == 0 and scratch.calls() == 0


def test_a_running_host_dream_never_blocks_a_scratch_home_dream(two):
    host, scratch, _ = two
    with _Held(host, "host") as h:
        p, res = scratch.unit("wizard-library-dream-daytime.service")
        assert _ran(p, res), ("a scratch-HOME dream was refused while the HOST brain's dream ran", res,
                              p.stderr[-800:])
    assert h.result.get("status") == "ok", (h.result, h.err[-600:])


def test_two_brains_whose_configs_name_a_legacy_lock_never_block_each_other_and_say_so(two):
    """setup-dreaming-cron.sh's old config named /tmp/wizard-library-dreaming.lock; both brains carry that NAME here
    (one shared dir stands in for /tmp). It keeps working, stops colliding, and says which lock it used."""
    host, scratch, shared = two
    for b in (host, scratch):
        b.write_cfg(shared / "wizard-library-dreaming.lock")
    with _Held(scratch, "scratch", cfg=True):
        p, res = host.organ("memory_dreaming.py", "run", host.db, "--config", host.cfg)
        assert _ran(p, res), ("a legacy-config dream was refused by ANOTHER brain's run", res, p.stderr[-800:])
        assert "legacy machine-global lock" in (res.get("lock_note") or ""), res
        host.write_cfg(shared / "voidloom-dream-pipeline.lock")     # dream_pipeline's old default NAME
        p2, res2 = host.organ("dream_pipeline.py", "run", host.db, "--config", host.cfg)
        assert _ran(p2, res2) and "legacy" in (res2.get("lock_note") or ""), (res2, p2.stderr[-800:])
    assert not os.listdir(str(shared)), ("a legacy machine-global lock was created", os.listdir(str(shared)))


def test_the_same_brain_is_still_refused_from_another_tmpdir_and_every_dream_entry_point_exits_75(two):
    host, _, _ = two
    other = host.home / "another-tmp"
    other.mkdir()
    mine = str(host.db.resolve()) + SUFFIX
    with _Held(host, "host"):
        for name in ("wizard-library-dreaming.service", "wizard-library-dream-daytime.service"):
            p, res = host.unit(name, tmpdir=other)
            assert res.get("status") == "skipped", ("a second dream ran on a brain whose dream was mid-run", name, res)
            assert res.get("lock_file") == mine, (name, res)
            assert p.returncode == 75, ("a skipped dream must exit 75 (the ledger's skipped-lock)", name, p.returncode)
        p, res = host.organ("memory_dreaming.py", "run", host.db, tmpdir=other)
        assert res.get("status") == "skipped" and res.get("lock_file") == mine, res
        assert p.returncode == 75, ("memory_dreaming.py run read a skip as a failure (exit %s)" % p.returncode)


PURE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import memory_dreaming as md, dream_pipeline as dp
a, b, link, mine = sys.argv[2:6]
print(json.dumps({"a": md.run_lock_path(a), "b": md.run_lock_path(b), "link": md.run_lock_path(link),
                  "legacy1": md.run_lock_path(a, "/tmp/wizard-library-dreaming.lock"),
                  "legacy2": md.run_lock_path(a, "/var/tmp/voidloom-dream-pipeline.lock"),
                  "mine": md.run_lock_path(a, mine),
                  "md_default": md.DEFAULT_CONFIG.get("lock_file"), "dp_default": dp.DEFAULT_CONFIG.get("lock_file")}))
'''


def test_the_dream_lock_is_keyed_by_the_brains_real_path(tmp_path):
    a, b = tmp_path / "a" / "claude_memory.db", tmp_path / "b" / "claude_memory.db"
    for d in (a, b):
        d.parent.mkdir()
        d.write_bytes(b"")
    link = tmp_path / "link.db"
    link.symlink_to(a)
    p = subprocess.run([sys.executable, "-B", "-c", PURE, str(ORGANS), str(a), str(b), str(link),
                        str(tmp_path / "mine.lock")], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(tmp_path)))
    assert p.returncode == 0, p.stderr[-1200:]
    d = json.loads(p.stdout.strip().splitlines()[-1])
    la = str(a.resolve()) + SUFFIX
    assert d["a"] == [la, None], d["a"]
    assert d["b"][0] != la, "two brains share one dream lock"
    assert d["link"][0] == la, "a symlinked spelling of one brain got a second lock"
    for k in ("legacy1", "legacy2"):
        assert d[k][0] == la and "legacy" in (d[k][1] or ""), (k, d[k])
    assert d["mine"] == [str(tmp_path / "mine.lock"), None], "an explicit non-legacy path was not honoured"
    assert d["md_default"] is None and d["dp_default"] is None, ("a default names a lock a tempdir decides", d)


def test_a_brain_whose_directory_cannot_hold_the_dream_lock_gets_a_named_error(two):
    host, _, _ = two
    os.chmod(str(host.dir), stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(str(host.dir), os.W_OK):
            pytest.skip("this account can write a 0500 directory (root?)")
        p, res = host.unit("wizard-library-dreaming.service")
    finally:
        os.chmod(str(host.dir), stat.S_IRWXU)
    assert res.get("status") == "error" and SUFFIX in (res.get("reason") or ""), (res, p.stderr[-800:])
    assert p.returncode == 1 and "Traceback" not in p.stderr, p.stderr[-800:]


def test_setup_dreaming_cron_writes_a_config_that_names_no_machine_global_lock(tmp_path):
    """The script, run in a scratch HOME: a logging systemctl first on PATH, no powershell, no user bus reachable."""
    home, shim = tmp_path / "home", tmp_path / "shim"
    home.mkdir()
    shim.mkdir()
    (shim / "systemctl").write_text('#!/bin/sh\necho "systemctl $*" >> "%s"\nexit 0\n' % (tmp_path / "calls"))
    os.chmod(str(shim / "systemctl"), 0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("WIZARD_", "XDG_", "DBUS_")) and k not in ("VOIDLOOM_ENV_DIR",)}
    env.update(HOME=str(home), PATH="%s:/usr/bin:/bin" % shim, TMPDIR=str(tmp_path))
    p = subprocess.run(["bash", str(ROOT / "cron" / "setup-dreaming-cron.sh")], capture_output=True, text=True,
                       env=env, cwd=str(tmp_path), timeout=120)
    assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
    calls = (tmp_path / "calls").read_text()
    assert "daemon-reload" in calls, calls                 # the shim answered: the real manager was never reached
    cfg = json.loads((home / ".voidloom" / "dreaming-config.json").read_text())
    lock = cfg.get("lock_file")
    assert lock is None, ("the config the script writes names a machine-global lock", lock)


def test_wizard_configs_lock_dir_is_the_brains(tmp_path):
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import wizard_config as w; "
            "print(json.dumps([w.WizardConfig(db_path=sys.argv[2]).lock_file_path, "
            "w.WizardConfig(db_path=sys.argv[2], lock_dir='/tmp').lock_file_path]))")
    db = tmp_path / "brain" / "claude_memory.db"
    db.parent.mkdir()
    p = subprocess.run([sys.executable, "-B", "-c", code, str(ORGANS), str(db)], capture_output=True, text=True,
                       timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HOME=str(tmp_path)))
    assert p.returncode == 0, p.stderr[-800:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got == [str(db.parent.resolve())] * 2, ("wizard_config's lock dir is not the brain's", got)


# ---------------------------------------------------------------------------------------------------------------------
# THE CLASS, as a lint. A lock is per-machine when its PATH is built from /tmp, TMPDIR or tempfile.gettempdir(): then
# every brain on the machine (and every harness whose TMPDIR resolves the same) contends for it, and one brain's run
# can cost another brain its night. Scope: every shipped file except the tests. The one allowed member is the GPU's.
# ---------------------------------------------------------------------------------------------------------------------
ALLOWED = {
    # the GPU is a MACHINE resource: one burst per machine is the right scope; contention degrades dual -> single, it
    # never skips a brain's run (re-read in Round 3 by G1 and G3)
    ("organs/gpu_guard.py", "BURST_LOCK_PATH"),
    # wizard_config's self-test feeds the LEGACY value in (T16, its config template) to prove it is set aside
    ("organs/wizard_config.py", "lock_dir", "_run_self_test"),
}
_SKIP_DIRS = {"tests", "__pycache__", ".git", "node_modules"}


def _lockish(name):
    n = (name or "").lower()
    return "lock" in n and not any(w in n for w in ("block", "clock", "unlock"))


def _machine_keyed(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and (
                n.value in ("/tmp", "TMPDIR") or n.value.startswith("/tmp/")):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) == "gettempdir":
                return True
    return False


class _Lint(ast.NodeVisitor):
    """Every lock-named binding (assignment, annotated default, dict entry, keyword, a lock call's first argument)
    whose value is built from /tmp, TMPDIR or gettempdir(), with the function it sits in."""

    def __init__(self, rel):
        self.rel, self.fn, self.out = rel, [], []

    def _check(self, name, value):
        if _lockish(name) and value is not None and _machine_keyed(value):
            where = self.fn[-1] if self.fn else ""
            if (self.rel, name) not in ALLOWED and (self.rel, name, where) not in ALLOWED:
                self.out.append((self.rel, name, getattr(value, "lineno", 0)))

    def visit_FunctionDef(self, node):
        self.fn.append(node.name)
        self.generic_visit(node)
        self.fn.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        for t in node.targets:
            self._check(getattr(t, "id", None) or getattr(t, "attr", None), node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._check(getattr(node.target, "id", None) or getattr(node.target, "attr", None), node.value)
        self.generic_visit(node)

    def visit_Dict(self, node):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                self._check(k.value, v)
        self.generic_visit(node)

    def visit_keyword(self, node):
        self._check(node.arg, node.value)
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        if node.args:
            self._check(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""), node.args[0])
        self.generic_visit(node)


def py_violations(src, rel):
    v = _Lint(rel)
    v.visit(ast.parse(src))
    return v.out


def text_violations(src, rel):
    """Shell / JSON / unit files: a non-comment line naming a lock under /tmp, TMPDIR or gettempdir."""
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if re.search(r"(?i)(?<![bc])lock(?!ed\b)", s) and re.search(r"(/tmp\b|TMPDIR|gettempdir)", s):
            out.append((rel, s[:80], i))
    return out


def _shipped():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or _SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        if p.suffix in (".py", ".sh", ".json", ".service", ".timer") or p.name == "install.sh":
            yield p


def test_no_shipped_lock_is_keyed_by_the_machine():
    found = []
    for p in _shipped():
        rel = p.relative_to(ROOT).as_posix()
        src = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix == ".py":
            try:
                found += py_violations(src, rel)
            except SyntaxError:
                continue
        else:
            found += text_violations(src, rel)
    assert not found, "locks keyed by the machine, not the brain:\n" + "\n".join("%s:%s %s" % (r, ln, n)
                                                                               for r, n, ln in found)


def test_the_lint_can_fail():
    """The falsifier: each pre-Round-3 shape is caught; the brain-keyed shapes are not."""
    bad = [('D = {"lock_file": os.path.join(tempfile.gettempdir(), "x.lock")}', "lock_file"),
           ('lock_path = "/tmp/wizard-dreaming-test-lock"', "lock_path"),
           ('class C:\n    lock_dir: str = "/tmp"', "lock_dir"),
           ('_acquire_lock(os.path.join(os.environ["TMPDIR"], "y.lock"))', "_acquire_lock")]
    for src, name in bad:
        assert [v[1] for v in py_violations(src, "organs/x.py")] == [name], src
    good = ['D = {"lock_file": None}', 'lock_path = os.path.realpath(db) + ".dreaming.lock"',
            'lock_path = os.path.join(tmpdir, "dreaming-test.lock")', 'block_size = "/tmp"']
    for src in good:
        assert py_violations(src, "organs/x.py") == [], src
    assert text_violations('    "lock_file": "/tmp/wizard-library-dreaming.lock",', "cron/x.sh")
    assert not text_violations('# was "/tmp/wizard-library-dreaming.lock"', "cron/x.sh")

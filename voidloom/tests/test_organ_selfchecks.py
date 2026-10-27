"""N3 (Crucible D02) — SOMETHING EXECUTES THE BINDERS.

The finding, measured at the referent AND re-measured after cycle 1:

    pytest ids touching hook|instruction|canary|preamble|heartbeat|receipt   0
    harnesses under voidloom/tests/harness/                                 10
    of those named test_*.py, and therefore collected                        0
    install.sh Step E running an organ self-check                            0

The organs carry 83 legs of self-check between them and they all pass. They were
invoked by a hand. Cycle 1 then added five more harnesses and moved the finding
BACKWARDS -- more instruments, still zero automatic firings. D02's own sentence:
"a rule that exists, reads correctly, is agreed to by everyone, and fires never."

WHAT THIS FILE IS AND IS NOT. It binds the FAST surface -- the three organ
self-checks and the two regressions that cost seconds -- so that `pytest` alone
now exercises the thing under review. It deliberately does NOT wrap the long
harnesses (`cli_install_suite`, the property battery, the six cycle-1 suites):
those run real installs for minutes each, and folding them in would make the
default suite unrunnable, which is how a gate stops being run at all. They are
release gates, listed in `harness/README.md` and driven by `harness/run_all.py`.

Every test here boxes its own HOME and states its whole environment: a test that
inherits its world measures the invoker, which is how C1's identity battery came
to pass for want of an unset variable.
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

BRAIN = Path(__file__).resolve().parent.parent.parent      # ~/.claude/memory
ORGANS = BRAIN / "organs"
FIXTURE_ROOT = BRAIN / ".pytest_boxes"                     # ext4 under $HOME, never /tmp


def _box(tag: str) -> Path:
    FIXTURE_ROOT.mkdir(exist_ok=True)
    p = Path(tempfile.mkdtemp(prefix=f"{tag}_", dir=str(FIXTURE_ROOT)))
    for sub in (".claude", ".codex", ".agents", "fx", "hb"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    return p


def _env(box: Path) -> dict:
    e = {k: v for k, v in os.environ.items()
         if k not in ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "PYTHONPATH",
                      "VOIDLOOM_PLATFORM", "CLAUDE_PROJECT_DIR")}
    e.update({
        "HOME": str(box),
        "CLAUDE_CONFIG_DIR": str(box / ".claude"),
        "CODEX_HOME": str(box / ".codex"),
        "VOIDLOOM_HEARTBEAT_DIR": str(box / "hb"),
        "VOIDLOOM_INSTALL_RECEIPT": str(box / "receipt.json"),
        "VOIDLOOM_PREIMAGE_STORE": str(box / "preimages"),
        "VOIDLOOM_SELFCHECK_FIXTURE_DIR": str(box / "fx"),
        "VOIDLOOM_CANARY_LEDGER": str(box / "ledger.json"),
    })
    return e


def _organ(box: Path, organ: str, *argv: str, timeout: int = 900):
    p = subprocess.run([sys.executable, str(ORGANS / organ), *argv, "--json"],
                       capture_output=True, text=True, env=_env(box), timeout=timeout)
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, {"status": "UNPARSEABLE", "raw": (p.stdout or "")[-300:],
                              "err": (p.stderr or "")[-300:]}


class TestOrganSelfChecks(unittest.TestCase):
    """The three organs grade themselves; nothing ran them."""

    def setUp(self):
        self.box = _box("selfcheck")

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def test_hooks_self_check_passes(self):
        rc, env = _organ(self.box, "voidloom_hooks_status.py", "--self-check")
        data = env.get("data") or {}
        self.assertEqual(data.get("verdict"), "PASS", env)
        self.assertEqual(data.get("failed"), [], env)
        self.assertEqual(rc, 0, env)

    def test_canary_self_check_has_no_failures(self):
        # PASS or PASS_WITH_UNMEASURABLE (rc 4) -- C5 made "cannot tell" its own
        # code, so this asserts on FAILURES, never on the exit code being 0.
        rc, env = _organ(self.box, "voidloom_canary.py", "--self-check")
        data = env.get("data") or {}
        self.assertIn(data.get("verdict"), ("PASS", "PASS_WITH_UNMEASURABLE"), env)
        self.assertEqual(data.get("failed"), [], env)
        self.assertIn(rc, (0, 4), env)

    def test_preamble_selftest_passes(self):
        # THE PREMISE, repaired (reference-host round, R9): this let the organ resolve
        # its default brain, <tree>/claude_memory.db, and copy it into --work-db. An
        # installed brain has that file; a checkout does not (FileNotFoundError in every
        # checkout -- the parent's standing red). The box now carries its own brain,
        # built from the package's blank seed by the package's migrator, and the organ
        # is pointed at it. The assertions are unchanged.
        from voidloom.tests._owned_brain import packaged_brain
        brain = packaged_brain(self.box / "claude_memory.db")
        rc, env = _organ(self.box, "voidloom_preamble.py", "--selftest", "--db", str(brain),
                         "--work-db", str(self.box / "work.db"))
        data = env.get("data") or {}
        self.assertEqual(data.get("verdict"), "PASS", env)
        self.assertEqual(data.get("failed"), [], env)

    def test_preamble_selftest_passes_with_an_auto_decision(self):
        """recheck R3-1: the never-dropped oversight line is a conditional floor; with an
        auto decision on the brain every budget must still have a legal move."""
        import sqlite3
        import json as _j
        from unittest import mock
        from voidloom.tests._owned_brain import packaged_brain
        brain = packaged_brain(self.box / "claude_memory.db")
        cfg = self.box / "wc.json"
        cfg.write_text("{}")
        with mock.patch.dict(os.environ, {"WIZARD_CONFIG_PATH": str(cfg), "VOIDLOOM_ACTOR": "t"}):
            sys.path.insert(0, str(ORGANS))
            import approvals_policy as AP
            import decision_units as DU
            AP.set_mode("auto", set_by="t")
            enh = [{"title": "Tune %d" % i, "rationale": "r", "evidence": "e", "priority": "low",
                    "estimated_effort": "1h", "piece_affected": "x"} for i in range(4)]
            c = sqlite3.connect(str(brain))
            rid = c.execute("INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements, "
                            "proposed_deprecations, threshold_assessment, persona_evolution, status) VALUES "
                            "('2026-09','{}',?,'[]','{}','{}','pending')", (_j.dumps(enh),)).lastrowid
            DU.create_units_for_report(c, rid, enh, [])
            c.commit()
            c.close()
            self.assertEqual(AP.decide_new_self_eval_report(str(brain), rid)["status"], "ok")
            rc, env = _organ(self.box, "voidloom_preamble.py", "--selftest", "--db", str(brain),
                             "--work-db", str(self.box / "work.db"))
        self.assertEqual((env.get("data") or {}).get("failed"), [], env)
        self.assertEqual(rc, 0, env)


class TestPlatformIsolation(unittest.TestCase):
    """N1 (Crucible 107) — the 89-check suite has 22 uninstall sites and ZERO
    passing `--platform`, so the all-platform removal was the seed case and the
    flag C1 repaired had no cell anywhere. This is that cell."""

    def setUp(self):
        self.box = _box("platiso")

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def test_uninstall_platform_claude_leaves_the_codex_hook_alone(self):
        (self.box / ".claude" / "settings.json").write_text(
            '{\n  "hooks": {}\n}\n', encoding="utf-8")
        for plat in ("codex", "claude"):
            rc, env = _organ(self.box, "voidloom_hooks_status.py",
                             "--install", "--platform", plat, "--yes")
            self.assertEqual(env.get("status"), "ok", (plat, env))
        codex_hooks = self.box / ".codex" / "hooks.json"
        self.assertTrue(codex_hooks.is_file(), "codex hook was not installed")
        before = codex_hooks.read_bytes()

        rc, env = _organ(self.box, "voidloom_hooks_status.py",
                         "--uninstall", "--platform", "claude", "--yes")
        self.assertIn(env.get("status"), ("ok", "incomplete"), env)
        # THE ASSERTION THE CRUCIBLE FOUND FOUR TIMES FROM FOUR DIRECTIONS.
        self.assertTrue(codex_hooks.is_file(),
                        "`--platform claude` deleted the Codex hook file")
        self.assertEqual(codex_hooks.read_bytes(), before,
                         "`--platform claude` modified the Codex hook")

    def test_uninstall_refuses_a_path_outside_the_undo_domain(self):
        victim = self.box / "not_ours.txt"
        victim.write_text("PRECIOUS\n", encoding="utf-8")
        (self.box / "receipt.json").write_text(json.dumps({"schema_version": 1, "touched": [
            {"platform": "codex", "mechanism": "hooks", "path": str(victim),
             "action": "delete_file", "installed_at": "2026-01-01T00:00:00Z"}],
            "undone": []}), encoding="utf-8")
        rc, env = _organ(self.box, "voidloom_hooks_status.py", "--uninstall", "--yes")
        self.assertTrue(victim.is_file(), "a receipt naming any file deleted it")
        self.assertEqual(victim.read_text(encoding="utf-8"), "PRECIOUS\n")
        data = env.get("data") or {}
        self.assertGreaterEqual(data.get("out_of_domain_count", 0), 1, env)


class TestInstructionsRoundTrip(unittest.TestCase):
    """The other delivery mechanism, bound by nothing until now."""

    def setUp(self):
        self.box = _box("instr")

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def test_install_then_uninstall_leaves_no_trace(self):
        rc, env = _organ(self.box, "voidloom_instructions.py", "install", "--yes",
                         "--home", str(self.box))
        self.assertEqual(env.get("status"), "ok", env)
        written = sorted(p for p in self.box.rglob("*.md"))
        self.assertTrue(written, "instructions install wrote nothing")
        # C7a: the file tier's doctrine block is the whole activation there.
        agents = [p for p in written if p.name == "AGENTS.md"]
        self.assertTrue(agents, "no AGENTS.md target")
        for p in agents:
            self.assertIn("VOIDLOOM DOCTRINE BEGIN", p.read_text(encoding="utf-8"),
                          f"{p} carries the notice but not the doctrine")

        rc, env = _organ(self.box, "voidloom_instructions.py", "uninstall", "--yes",
                         "--home", str(self.box))
        self.assertEqual(env.get("status"), "ok", env)
        left = [str(p) for p in self.box.rglob("*.md")
                if "Voidloom" in p.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(left, [], "uninstall left our markers behind")


if __name__ == "__main__":
    unittest.main()

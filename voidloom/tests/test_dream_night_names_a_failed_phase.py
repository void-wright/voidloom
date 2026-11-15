"""A dream night with a phase that failed is never reported as ok.

Each phase is fail-soft: the night goes on past one that raised. Its summary used to read `status: ok` beside
`phases_error: 1`, and the unit exited 0, so a scheduler recorded the night as a success.
  * saboteur: a typed refusal raised inside one phase makes the night `degraded`, with `degraded: true` and the phase
    named in `degraded_phases`, and both dream entry points exit 1 (outside the dreaming units' SuccessExitStatus);
  * saboteur: an untyped error does the same;
  * control: a night whose phases all ran reads ok and exits 0; a dry run reads dry-run.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"

DRIVER = textwrap.dedent(r'''
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import dream_pipeline as dp
    import embedder_lock as el
    mode, db, lock = sys.argv[2], sys.argv[3], sys.argv[4]

    def fine(conn, cfg, batch_id):
        return {"status": "ok"}

    def refuses(conn, cfg, batch_id):
        raise el.EmbedderLockError("WEIGHTS_MISMATCH", "planted: a refusal inside one phase")

    def breaks(conn, cfg, batch_id):
        raise RuntimeError("planted: an untyped error inside one phase")

    bad = {"refusal": refuses, "error": breaks, "none": None}[mode]
    dp.PHASES[:] = [dp.Phase("first_phase", fine, nocturnal=True)] + (
        [dp.Phase("failing_phase", bad, nocturnal=True)] if bad else []) + [dp.Phase("last_phase", fine, nocturnal=True)]
    res = dp.run_dream_cycle(db, config={"lock_file": lock})
    dry = dp.run_dream_cycle(db, config={"lock_file": lock}, dry_run=True)
    rc = dp.main(["run", db, "--config", sys.argv[5]])
    print(json.dumps({"res": {k: res.get(k) for k in ("status", "degraded", "degraded_phases", "phases_error",
                                                      "phases_ok")},
                      "dry": dry.get("status"), "rc": rc}))
''')


class TheNightNamesItsFailedPhase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_dreamnight_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        sys.path.insert(0, str(ROOT / "voidloom" / "tests"))
        try:
            from _owned_brain import packaged_brain
        finally:
            sys.path.pop(0)
        self.db = packaged_brain(self.tmp / "claude_memory.db")
        (self.tmp / "home").mkdir()
        (self.tmp / "driver.py").write_text(DRIVER, encoding="utf-8")
        (self.tmp / "cfg.json").write_text(json.dumps({"lock_file": str(self.tmp / "dream2.lock")}), encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "VOIDLOOM_ENV_DIR", "WIZARD_ENV_FILE",
                                 "PYTHONPATH")}
        self.env.update({"HOME": str(self.tmp / "home"), "PYTHONDONTWRITEBYTECODE": "1",
                         "WIZARD_LIBRARY_DB": str(self.db)})

    def drive(self, mode):
        p = subprocess.run([sys.executable, "-B", str(self.tmp / "driver.py"), str(ORGANS), mode, str(self.db),
                            str(self.tmp / "dream.lock"), str(self.tmp / "cfg.json")],
                           capture_output=True, text=True, env=self.env, timeout=600)
        self.assertEqual(p.returncode, 0, (p.stdout[-800:], p.stderr[-1500:]))
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_saboteur_a_refusal_inside_one_phase_makes_the_night_degraded_and_names_it(self):
        out = self.drive("refusal")
        r = out["res"]
        self.assertEqual((r["status"], r["degraded"], r["degraded_phases"]), ("degraded", True, ["failing_phase"]), out)
        self.assertEqual((r["phases_error"], r["phases_ok"]), (1, 2), out)
        self.assertEqual(out["rc"], 1, "the unit would record a night with a failed phase as a success")

    def test_saboteur_an_untyped_error_does_the_same(self):
        out = self.drive("error")
        self.assertEqual((out["res"]["status"], out["res"]["degraded_phases"], out["rc"]),
                         ("degraded", ["failing_phase"], 1), out)

    def test_control_a_night_whose_phases_all_ran_is_ok(self):
        out = self.drive("none")
        self.assertEqual((out["res"]["status"], out["res"]["degraded"], out["rc"], out["dry"]),
                         ("ok", None, 0, "dry-run"), out)


if __name__ == "__main__":
    unittest.main()

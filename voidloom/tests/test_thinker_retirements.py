# -*- coding: utf-8 -*-
"""M12 -- the per-organ model switches are RETIRED, their aliases kept where a unit still passes one.
M13 -- every roster unit says whether it THINKS; the units' argv stays byte for byte.

Retired (Council R-Q3): gap_mapping's `auto_research_method` (and its `agy` dispatch), `wizard_config.agy_path`,
the per-organ `claude_token_path` config keys (the second Claude home), and `--method` as a CHOICE -- the owner's
recorded thinker decides. KEPT as an alias: `--method claude_cli`, because the packaged gap-research unit passes it
(cron/wizard-library-gap-research.service) and so does a second brain's WSL unit (DESSA Q1). `--method agy` is refused.
The alias is exercised by RUNNING the roster's own exec line for gap-research against a scratch packaged brain whose
thinker is a recorded double: the unit's argv, byte for byte, dispatches through the seam.
gap_research_log's closed CHECK enums admit 'thinker' / 'skipped' / 'held' through migrations/migration_039_gap_research_log_thinker.sql -- a
NUMBERED migration the installer's own migration step applies (pre-image, journal, read-back), never a rebuild made by
an organ at run time (model-choice Round 1, Wave B: the coordinator's ruling on Wave A). On a brain that predates 039
gap research SKIPS LOUDLY -- a THINKER_LOG row and the doctor's thinker line -- and changes no schema. 'held' was a
latent refusal for every Warden HOLD.
"""
import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
from voidloom.tests._owned_brain import packaged_brain
import thinker

M039 = "migration_039_gap_research_log_thinker.sql"     # messages name a migration by its FULL filename (MC-R1-19)

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = Path.home() / ".crucible_scratch"
RETIRED_KEYS = ("auto_research_method", "agy_path", "claude_token_path")


def test_no_organ_reads_a_retired_switch():
    hits = []
    for p in sorted((ROOT / "organs").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        in_selftest = set()             # an organ's own self-test may feed an OLD config to prove it is ignored
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and "test" in fn.name.lower():
                in_selftest.update(id(x) for x in ast.walk(fn))
        for n in ast.walk(tree):
            if id(n) in in_selftest:
                continue
            key = None
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
                key = n.slice.value
            elif isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get" and n.args \
                    and isinstance(n.args[0], ast.Constant):
                key = n.args[0].value
            elif isinstance(n, ast.Attribute):
                key = n.attr
            elif isinstance(n, ast.Dict):
                for k in n.keys:
                    if isinstance(k, ast.Constant) and k.value in RETIRED_KEYS:
                        hits.append("%s:%d defines %s" % (p.name, k.lineno, k.value))
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                key = n.target.id
            if key in RETIRED_KEYS:
                hits.append("%s:%d reads %s" % (p.name, n.lineno, key))
    assert hits == [], hits
    import wizard_config
    names = {f.name for f in wizard_config.fields(wizard_config.WizardConfig)}
    assert not (names & set(RETIRED_KEYS)), names & set(RETIRED_KEYS)


def test_the_argv_choices_keep_the_alias_and_refuse_agy():
    src = (ROOT / "organs" / "gap_mapping.py").read_text(encoding="utf-8")
    assert "choices=['thinker', 'claude_cli', 'agy']" in src
    cm = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
    assert 'choices=["thinker", "claude_cli", "agy"]' in cm


@pytest.fixture
def brain(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_m12_", dir=str(SCRATCH)))
    w = F.world(monkeypatch, root)
    db = packaged_brain(w.brain / "claude_memory.db")
    c = sqlite3.connect(str(db))
    c.execute("INSERT INTO gap_inventory (entity, gap_type, score, auto_research_eligible) "
              "VALUES ('ollama num_ctx', 'thin_entity', 1.0, 1)")
    c.execute("INSERT INTO gap_research_log (gap_id, dispatch_method, status) VALUES (1, 'claude_cli', 'success')")
    c.commit()
    c.close()
    w.db = db
    yield w
    shutil.rmtree(str(root), ignore_errors=True)


def _unit_exec(w, method):
    roster = json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))
    ex = roster["units"]["gap-research"]["exec"]
    assert ex == "organs/gap_mapping.py --research --method claude_cli --max-gaps 5 {DB}", ex   # byte for byte
    argv = ex.replace("{DB}", str(w.db)).split()
    argv[argv.index("--method") + 1] = method
    env = {k: os.environ[k] for k in ("HOME", "PATH", "WIZARD_LIBRARY_DB", "TMPDIR", "LANG") if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run([sys.executable, "-B", str(ROOT / argv[0])] + argv[1:], capture_output=True, text=True,
                       env=env, timeout=300, cwd=str(w.brain))
    return p


def test_the_units_alias_dispatches_through_the_seam(brain):
    F.make_fake(brain.bin, "claude", log=str(brain.log), reply="Findings: set num_ctx explicitly.")
    F.save_cred(brain, "claude")
    F.record(thinker, "claude")
    p = _unit_exec(brain, "claude_cli")
    assert p.returncode == 0, (p.stdout[-800:], p.stderr[-800:])
    out = json.loads(p.stdout)
    assert out["method"] == "thinker:claude" and out["dispatched"] == 1, out
    assert out["results"][0]["status"] == "success", out
    c = sqlite3.connect("file:%s?mode=ro" % brain.db, uri=True)
    rows = c.execute("SELECT dispatch_method, status, dispatch_command FROM gap_research_log ORDER BY id").fetchall()
    mem = c.execute("SELECT source_context FROM memories WHERE source_project='gap-research'").fetchall()
    c.close()
    assert rows[0][:2] == ("claude_cli", "success"), "the old row survives the rebuild"
    assert rows[1][0] == "thinker" and rows[1][1] == "success" and rows[1][2].startswith("thinker:claude | ")
    assert mem and "method=thinker:claude" in mem[-1][0]
    argv = [r for r in F.read_log(brain.log) if not r.get("end")][-1]["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch", "gap research = the research posture (web only)"


def test_agy_is_refused_and_a_web_less_backend_skips(brain):
    p = _unit_exec(brain, "agy")
    assert p.returncode == 0 and "retired" in json.loads(p.stdout)["message"]
    srv, url = F.fake_model_server(reply="INVENTED")
    try:
        F.record(thinker, "local", "--endpoint", url, "--model", "tiny")
        n = len(srv.requests)
        p = _unit_exec(brain, "claude_cli")
        out = json.loads(p.stdout)
        assert out["status"] == "skipped" and out["dispatched"] == 0, out
        assert len(srv.requests) == n, "a web-less backend was asked to research"
    finally:
        srv.shutdown()
        srv.server_close()
    c = sqlite3.connect("file:%s?mode=ro" % brain.db, uri=True)
    assert c.execute("SELECT researched FROM gap_inventory WHERE id=1").fetchone()[0] == 0, "a skip never retires"
    c.close()


def _narrow(db):
    """Put gap_research_log back to migration_015's narrow CHECK lists (a brain that predates migration_039_gap_research_log_thinker.sql): the
    table rebuilt with 015's own CREATE statement, rows kept, 039's journal row and marker index gone."""
    sql015 = (ROOT / "migrations" / "migration_015_gap_mapping.sql").read_text(encoding="utf-8")
    create = sql015[sql015.index("CREATE TABLE IF NOT EXISTS gap_research_log"):]
    create = create[:create.index(";") + 1].replace("IF NOT EXISTS gap_research_log", "gap_research_log_narrow")
    c = sqlite3.connect(str(db))
    c.executescript("PRAGMA legacy_alter_table=ON; BEGIN; " + create + " INSERT INTO gap_research_log_narrow SELECT * FROM "
                    "gap_research_log; DROP TABLE gap_research_log; ALTER TABLE gap_research_log_narrow RENAME TO "
                    "gap_research_log; CREATE INDEX idx_gap_research_gap ON gap_research_log(gap_id); "
                    "DELETE FROM schema_migrations WHERE name = 'migration_039_gap_research_log_thinker.sql'; COMMIT;")
    c.close()


def _schema(db):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return c.execute("SELECT group_concat(sql, char(10)) FROM (SELECT sql FROM sqlite_master ORDER BY name)").fetchone()[0]
    finally:
        c.close()


def test_migration_039_widens_the_log_and_a_second_apply_is_a_no_op(brain):
    """The installer's migration step (the packaged migrator, what install.sh B.1.5 runs) widens a narrow table: every
    row, the index, the FK to gap_inventory and the AUTOINCREMENT mark kept; the new values admitted, an invented one
    refused; a second apply skips 039 and leaves the file's bytes as they were."""
    _narrow(brain.db)
    migrator = ROOT / "organs" / "schema_migrator.py"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", WIZARD_LIBRARY_DB=str(brain.db))
    p = subprocess.run([sys.executable, "-B", str(migrator), "apply", "--db", str(brain.db), "--json"],
                       capture_output=True, text=True, timeout=300, env=env)
    d = json.loads(p.stdout)["data"]
    assert p.returncode == 0 and d["ok"], (p.stdout[-800:], p.stderr[-800:])
    assert "migration_039_gap_research_log_thinker.sql" not in d["skipped_already_applied"]
    c = sqlite3.connect(str(brain.db))
    assert c.execute("SELECT dispatch_method, status FROM gap_research_log").fetchall() == [("claude_cli", "success")]
    for status in ("held", "skipped"):
        c.execute("INSERT INTO gap_research_log (gap_id, dispatch_method, status) VALUES (1, 'thinker', ?)", (status,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO gap_research_log (gap_id, dispatch_method, status) VALUES (1, 'invented', 'x')")
    c.rollback()
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE tbl_name='gap_research_log' AND type='index'")}
    assert {"idx_gap_research_gap", "idx_gap_research_log_method_status"} <= idx, idx
    assert [r[2] for r in c.execute("PRAGMA foreign_key_list(gap_research_log)")] == ["gap_inventory"]
    c.close()
    before = (brain.db).read_bytes()
    p = subprocess.run([sys.executable, "-B", str(migrator), "apply", "--db", str(brain.db), "--json"],
                       capture_output=True, text=True, timeout=300, env=env)
    assert "migration_039_gap_research_log_thinker.sql" in json.loads(p.stdout)["data"]["skipped_already_applied"]
    assert brain.db.read_bytes() == before, "a second apply changed the brain"


def test_no_organ_rebuilds_the_log_and_a_narrow_brain_skips_loudly(brain):
    """The organ never changes the live schema: on a brain that predates 039 the unit's own gap-research exec skips
    (status skipped, reason schema-not-widened), writes a THINKER_LOG row the doctor's thinker line reads, asks no
    model, touches no gap and leaves the schema byte-identical."""
    import gap_mapping
    assert not hasattr(gap_mapping, "ensure_research_log_admits_thinker"), "the runtime rebuild is back in the organ"
    tree = ast.parse((ROOT / "organs" / "gap_mapping.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = " ".join(n.value.upper().split())
            assert not ("GAP_RESEARCH_LOG" in v and ("DROP TABLE" in v or "RENAME TO" in v)), n.value[:80]
    _narrow(brain.db)
    F.make_fake(brain.bin, "claude", log=str(brain.log), reply="Findings: never asked.")
    F.save_cred(brain, "claude")
    F.record(thinker, "claude")
    schema = _schema(brain.db)
    asked = len([r for r in F.read_log(brain.log) if not r.get("end")])     # the record's own probe
    p = _unit_exec(brain, "claude_cli")
    out = json.loads(p.stdout)
    assert out["status"] == "skipped" and out["reason"] == "schema-not-widened" and out["dispatched"] == 0, out
    assert M039 in out["message"] and M039 in p.stderr
    assert _schema(brain.db) == schema, "an organ changed the schema"
    assert len([r for r in F.read_log(brain.log) if not r.get("end")]) == asked, "a model was asked"
    rows = [r for r in thinker.read_log() if r.get("purpose") == "gap_research"]
    assert rows and rows[-1]["outcome"] == "skipped" and rows[-1]["reason"] == "schema-not-widened", rows
    assert "gap research SKIPPED" in thinker.health_line() and M039 in thinker.health_line()
    c = sqlite3.connect("file:%s?mode=ro" % brain.db, uri=True)
    assert c.execute("SELECT researched FROM gap_inventory WHERE id=1").fetchone()[0] == 0, "a skip never retires"
    c.close()


def test_every_roster_unit_says_whether_it_thinks():
    roster = json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))["units"]
    assert all(isinstance(u.get("thinks"), bool) for u in roster.values())
    thinking = {n for n, u in roster.items() if u["thinks"]}
    assert thinking == {"dreaming", "whisper-digest", "self-eval", "gap-research", "arcane-manifest",
                        "counterfactual-revisit", "dream-daytime"}, thinking
    p = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "cron_roster.py"), "thinks", "--set", "armed"],
                       capture_output=True, text=True, timeout=60)
    assert p.stdout.split() == ["dreaming", "whisper-digest", "self-eval"], p.stdout
    bad = json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))
    del bad["units"]["sleep-loop"]["thinks"]
    sys.path.insert(0, str(ROOT / "install"))
    try:
        import cron_roster
    finally:
        sys.path.pop(0)
    tmp = Path(tempfile.mkdtemp(prefix="roster_", dir=str(SCRATCH)))
    try:
        (tmp / "r.json").write_text(json.dumps(bad))
        doc, err = cron_roster.load(tmp / "r.json")
        assert doc is None and "thinks" in err
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def test_no_unit_file_carries_a_thinker_credential():
    """The units' environment stays minimal: no packaged unit sets a credential variable itself."""
    for p in sorted((ROOT / "cron").glob("wizard-library-*.service")):
        body = p.read_text(encoding="utf-8")
        for k in thinker.SECRET_ENV:
            assert ("Environment=%s" % k) not in body and ("Environment=\"%s" % k) not in body, (p.name, k)

# -*- coding: utf-8 -*-
"""Every statement that writes a row of `memories`, or a column that says what a row is worth, is named here with what it
is. A new one fails this test until somebody reads it and says whether the row it writes is derived from other rows.

WHAT WOULD BREAK WITHOUT IT: an organ added next year writes a summary of five memories with a plain INSERT. One of the
five came from a web page. The summary is stored as the owner's own, outranks what it restates, and is shown unframed.
Nothing else would notice: the database carries a mark along a recorded edge, and that organ recorded none.

  helper   marks.py: the INSERT every derived row goes through, and the rewrite of one
  derived  writes a row from other rows; must call the helper, and may hold no INSERT of its own
  owner    writes the words of a person or a fixed sentence over the organ's own counters; no memory text goes in
  outside  writes text from outside the machine; its INSERT carries the mark
  repair   changes a row that exists (a stamp, an archive, a pre-image restored); writes no new text
  other    another database with no marks (website_memory.db)
  fixture  a self-test's own rows, named by function, never by file
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("organs", "voidloom", "hooks", "tools", "install", "cron")
INSERT = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+memories\b(?!_)", re.I)
UPDATE = re.compile(r"\bUPDATE(?:\s+OR\s+\w+)?\s+memories\s+SET\s+(.*)", re.I | re.S)
WEIGHTY = re.compile(r"\b(content|importance|confidence|derived_from|untrusted_source|research_mark|research_run_id|"
                     r"research_weight|author|is_archived)\b|\{\?\}", re.I)

WRITERS = {
    ("organs/marks.py", "derived_insert", "INSERT"): "helper",
    ("organs/marks.py", "derived_rewrite", "UPDATE"): "helper",
    ("organs/memory_write.py", "write_memory", "INSERT"): "derived",
    ("organs/claude_memory.py", "cmd_store", "INSERT"): "owner",
    ("organs/claude_memory.py", "cmd_store", "UPDATE"): "owner",
    ("organs/claude_memory.py", "cmd_migrate", "INSERT"): "owner",
    ("organs/claude_memory.py", "cmd_update", "UPDATE"): "owner",
    ("organs/claude_memory.py", "cmd_delete", "UPDATE"): "repair",
    ("organs/claude_memory.py", "cmd_maintain", "UPDATE"): "repair",
    ("organs/claude_memory_lite.py", "_insert_memory", "INSERT"): "owner",
    ("organs/claude_memory_lite.py", "cmd_load_seed", "UPDATE"): "repair",
    ("organs/gap_mapping.py", "dispatch_auto_research", "INSERT"): "outside",
    ("organs/gap_mapping.py", "sweep_stored_non_answers", "UPDATE"): "repair",
    ("organs/governance.py", "_maybe_distill_failure", "INSERT"): "owner",
    ("organs/monthly_self_eval.py", "surface_pending_for_owner", "INSERT"): "owner",
    ("organs/monthly_self_eval.py", "surface_pending_for_owner", "UPDATE"): "repair",
    ("organs/monthly_self_eval.py", "enact_approved", "INSERT"): "owner",
    ("organs/dream_ledger.py", "apply", "UPDATE"): "repair",
    ("organs/dream_ledger.py", "_restamp_parents", "UPDATE"): "repair",
    ("organs/dream_ledger.py", "_restore", "UPDATE"): "repair",
    ("organs/dream_ledger.py", "rollback", "UPDATE"): "repair",
    ("organs/kind_taxonomy.py", "backfill_kinds", "UPDATE"): "repair",
    ("organs/memory_dreaming.py", "flag_interesting", "UPDATE"): "repair",
    ("organs/skill_graduation.py", "graduate_memory", "UPDATE"): "repair",
    ("organs/sleep_loop.py", "rerank_importance", "UPDATE"): "repair",
    ("organs/mark_admin.py", "forget", "UPDATE"): "repair",
    ("organs/mark_admin.py", "unarchive", "UPDATE"): "repair",
    ("organs/mark_admin.py", "backfill", "UPDATE"): "repair",
    ("organs/mark_admin.py", "backfill_undo", "UPDATE"): "repair",
    ("organs/warden.py", "stamp_untrusted_source", "UPDATE"): "repair",
    ("organs/website_memory.py", "cmd_store", "INSERT"): "other",
    ("organs/website_memory.py", "cmd_migrate", "INSERT"): "other",
    ("organs/website_memory.py", "cmd_update", "UPDATE"): "other",
    ("organs/website_memory.py", "cmd_delete", "UPDATE"): "other",
    ("organs/website_memory.py", "cmd_maintain", "UPDATE"): "other",
    # self-tests: their own rows in their own databases
    ("organs/anti_recency.py", "_run_unit_tests", "INSERT"): "fixture",
    ("organs/arcane_manifest.py", "_run_tests", "INSERT"): "fixture",
    ("organs/calibration_memory.py", "unit_test_calibration_memory", "INSERT"): "fixture",
    ("organs/counterfactual_memory.py", "unit_test_counterfactual_memory", "INSERT"): "fixture",
    ("organs/counterfactual_memory.py", "unit_test_counterfactual_memory", "UPDATE"): "fixture",
    ("organs/embedding_upgrade.py", "cmd_test", "INSERT"): "fixture",
    ("organs/gap_mapping.py", "unit_tests", "INSERT"): "fixture",
    ("organs/hybrid_retrieval.py", "test_triggers_smoke", "INSERT"): "fixture",
    ("organs/hybrid_retrieval.py", "test_triggers_smoke", "UPDATE"): "fixture",
    ("organs/memory_dreaming.py", "unit_test_memory_dreaming", "INSERT"): "fixture",
    ("organs/monthly_self_eval.py", "unit_test_monthly_self_eval", "INSERT"): "fixture",
    ("organs/procedural_memory.py", "_insert_memory", "INSERT"): "fixture",
    ("organs/provenance_graph.py", "setUp", "INSERT"): "fixture",
    ("organs/skill_graduation.py", "_insert_memory", "INSERT"): "fixture",
    ("organs/sleep_loop.py", "unit_tests", "INSERT"): "fixture",
    ("organs/temporal_edges.py", "_insert_test_memories", "INSERT"): "fixture",
    ("organs/temporal_edges.py", "unit_test_heuristic_extraction", "INSERT"): "fixture",
    ("organs/voidloom_canary.py", "self_check", "INSERT"): "fixture",
    ("organs/voidloom_preamble.py", "_insert_poison", "INSERT"): "fixture",
    ("organs/voidloom_preamble.py", "_store_probe", "INSERT"): "fixture",
    ("organs/voidloom_preamble.py", "_premigration_copy", "INSERT"): "fixture",
    ("tools/harness/test_harness.py", "_build_synthetic_corpus", "INSERT"): "fixture",
    ("tools/harness/test_harness.py", "test_side_effect_free_logic", "INSERT"): "fixture",
}
#: the functions that write a row from other rows, and the call each must make
DERIVED = {
    ("organs/sleep_loop.py", "consolidate_episodics"): "derived_insert",
    ("organs/sleep_loop.py", "generate_reflections"): "derived_insert",
    ("organs/memory_dreaming.py", "_store_dream_as_reflection"): "derived_insert",
    ("organs/dream_pipeline.py", "_store_synthesis_node"): "derived_insert",
    ("organs/dream_pipeline.py", "phase_raptor_synthesis"): "derived_rewrite",
    ("organs/dream_pipeline.py", "_emit_playbook"): "derived_insert",
    ("organs/whisper_network.py", "generate_weekly_digest"): "derived_insert",
    ("organs/arcane_manifest.py", "_store_manifest_as_memory"): "derived_insert",
    ("organs/memory_write.py", "write_memory"): "derived_columns",
}


def _text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{?}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return (_text(node.left) or "") + (_text(node.right) or "" if isinstance(node.op, ast.Add) else "")
    return None


def sites(source):
    """(function, 'INSERT' | 'UPDATE') for every statement in one module's source, docstrings left out."""
    tree, out = ast.parse(source), set()

    def walk(node, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        doc = ast.get_docstring(node, clean=False) if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) else None
        for child in ast.iter_child_nodes(node):
            if doc is not None and isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant) and child.value.value == doc:
                continue
            text = _text(child)
            if text:
                if INSERT.search(text):
                    out.add((fn, "INSERT"))
                m = UPDATE.search(text)
                if m and WEIGHTY.search(re.split(r"\bWHERE\b", m.group(1), flags=re.I)[0]):
                    out.add((fn, "UPDATE"))
            walk(child, fn)
    walk(tree, "<module>")
    return out


def census():
    found = set()
    for tree in TREES:
        for path in sorted((ROOT / tree).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("voidloom/tests/"):
                continue
            found |= {(rel, fn, verb) for fn, verb in sites(path.read_text(encoding="utf-8"))}
    return found


def calls(rel, fn):
    for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            return {n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    return None


class EveryWriter(unittest.TestCase):
    def test_every_writer_is_classified_and_no_entry_is_stale(self):
        found = census()
        self.assertEqual(sorted(found - set(WRITERS)), [], "a statement nobody has classified writes `memories`")
        self.assertEqual(sorted(set(WRITERS) - found), [], "an entry names a statement that is gone")

    def test_every_derived_writer_goes_through_the_helper(self):
        for (rel, fn), call in DERIVED.items():
            self.assertIn(call, calls(rel, fn) or set(), "%s:%s writes a derived row without marks.%s" % (rel, fn, call))

    def test_no_derived_writer_holds_an_insert_of_its_own(self):
        raw = {(f, fn) for (f, fn, verb), cls in WRITERS.items() if verb == "INSERT" and cls != "derived"}
        self.assertEqual(raw & (set(DERIVED) - {("organs/memory_write.py", "write_memory")}), set())

    def test_the_scanner_sees_the_shapes_it_must(self):
        """The control: a scanner that finds nothing passes the test above."""
        src = ('def a(c):\n    c.execute("INSERT INTO memories (content) VALUES (?)", (1,))\n'
               'def b(c, cols):\n    c.execute(f"insert or replace into memories ({cols}) VALUES (1)")\n'
               'def d(c, sets):\n    c.execute(f"UPDATE memories SET {sets} WHERE id=?")\n'
               'def e(c):\n    c.execute("UPDATE memories " "SET importance = 1 WHERE id=?")\n'
               'def f(c):\n    c.execute("UPDATE memories SET access_count = 1 WHERE confidence > 1")\n'
               'def g(c):\n    "INSERT INTO memories is only talked about here"\n'
               'def h(c):\n    c.execute("INSERT INTO memories_fts VALUES (1)")\n')
        self.assertEqual(sites(src), {("a", "INSERT"), ("b", "INSERT"), ("d", "UPDATE"), ("e", "UPDATE")})


if __name__ == "__main__":
    unittest.main()

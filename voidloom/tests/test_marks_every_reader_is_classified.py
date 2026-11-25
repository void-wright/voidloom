# -*- coding: utf-8 -*-
"""Every statement that reads a memory's words, or words that were copied out of one, is named here with what becomes of
them. A new one fails this test until somebody reads it and says whether the words reach a person, a model or a file.

WHAT WOULD BREAK WITHOUT IT: an organ added next year prints the five memories it found most interesting. One of them is a
web page that opens "ignore all previous instructions". Every recall frames that row; this one does not, and nothing else
would notice.

  presenter  the function puts the rows through the presenter before they leave it (it must call one)
  via        it returns rows to the named function, which does (that one must call a presenter, and must name this one)
  internal   the words stay in the process: compared, counted, embedded, matched; or the row is written back from them
  derived    the words go into a model's prompt or a new memory, and what comes back is written by the helper that
             inherits every parent's marks
  label      it shows a row as it is stored, and the mark beside it
  clean      it selects only rows whose mark is clean
  authored   the column holds words a person or a model wrote there; no code copies a memory into it
  other      another database with no marks (website_memory.db)
  fixture    a self-test's own rows

WHAT THE CENSUS RECOGNISES. A statement written as a string literal, an f-string, `+` of literals, and a name the same
function set to such an expression (`=` and `+=`). In it: every SELECT (a CTE, a subquery, each arm of a union); a
table named plain, quoted ("", [], ``) or prefixed main. or temp.; a column list naming the text, `*`, snippet() or
highlight(), or a part known only at run time.

WHAT IT DOES NOT FOLLOW, pinned below as expected failures (a census that learns one turns its test into a failure, and
the line moves up): str.format(); % formatting; MIN() or MAX() of a text column; a comment between
FROM and the table; a schema other than main or temp (an ATTACHed brain); str.join(); an annotated assignment
(`q: str = ...`); str.replace() templating; a statement assembled across functions. And a function classified once is
classified for all its statements: a second, raw read added to a presenter is not seen.
"""
import ast
import re
import unittest

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("organs", "voidloom", "hooks", "tools", "install", "cron")
SELECT = re.compile(r"\bSELECT\b(.*?)\bFROM\b(.*)", re.I | re.S)
#: the memories, and the tables whose columns hold text taken from them
WORDS = {"memories": "content|summary", "memories_fts": "content|summary", "gap_inventory": "entity",
         "community_summaries": "summary", "dream_log": "dream_content",
         "arcane_manifests": "manifest_content|sections|data_snapshot", "counterfactual_decisions": "topic|chosen_path",
         "council_sessions": "topic|transcript|synthesis", "raptor_clusters": "cluster_label"}
PRESENTERS = {"present_rows", "present_copies", "shown", "present", "_present", "_present_rows", "_present_topics",
              "_present_manifest", "frame_text"}

CM, LITE, CP, GM, MD, PG = ("organs/claude_memory.py", "organs/claude_memory_lite.py", "organs/counterfactual_memory.py",
                           "organs/gap_mapping.py", "organs/memory_dreaming.py", "organs/provenance_graph.py")
WEB, MANIFEST, BELIEF, LEDGER = ("organs/website_memory.py", "organs/arcane_manifest.py", "organs/belief_engine.py",
                                 "organs/dream_ledger.py")
P, I, D, A = "presenter", "internal", "derived", "authored"


def via(rel, fn):
    return ("via", rel, fn)


READERS = {
    ("install/self_check.py", "_recall", "memories"): I,                # counted and matched by id
    ("install/self_check.py", "_recall", "memories_fts"): I,
    ("install/self_check.py", "check_own_content_recall", "memories"): I,   # one word of it is searched for, and named
    ("install/self_check.py", "run_self_check", "memories"): I,         # the seed pack's own rows, compared
    ("organs/anti_recency.py", "find_resurfacing_candidates", "memories"): P,
    ("organs/anti_recency.py", "get_forgotten_foundations", "memories"): P,
    ("organs/anti_recency.py", "get_foundation_stats", "memories"): P,
    (MANIFEST, "_gather_dream_data", "dream_log"): P,
    (MANIFEST, "_run_tests", "memories"): "fixture",
    (MANIFEST, "finalize_manifest", "arcane_manifests"): D,
    (MANIFEST, "gather_quarter_data", "memories"): P,
    (MANIFEST, "get_manifest", "arcane_manifests"): P,
    (MANIFEST, "get_manifest_for_quarter", "arcane_manifests"): P,
    ("organs/assayer.py", "_augment_with_wild_pool", "dream_log"): P,
    ("organs/assayer.py", "run_bakeoff", "dream_log"): I,               # ranked and counted
    ("organs/assayer.py", "score_pending_dreams", "dream_log"): I,      # scored by a local model
    ("organs/assayer.py", "score_pending_dreams", "memories"): I,
    (BELIEF, "_candidate_edges", "memories"): I,
    (BELIEF, "_family_of", "memories"): I,
    (BELIEF, "_memory_text", "memories"): I,                            # scored by a local model
    (BELIEF, "quarantine_report", "memories"): P,
    (CM, "_cmd_recall_legacy", "memories"): P,
    (BELIEF, "quarantine_template_pairs", "gap_inventory"): I,
    (BELIEF, "quarantine_template_pairs", "memories"): I,
    (CM, "cmd_context", "memories"): P,
    (CM, "cmd_edges", "memories"): P,
    (CM, "cmd_list", "memories"): P,
    (CM, "cmd_maintain", "memories"): I,
    (CM, "cmd_recall", "memories"): P,
    (CM, "cmd_stats", "memories"): P,
    (CM, "cmd_update", "memories"): I,
    (LITE, "cmd_context", "memories"): P,
    (LITE, "cmd_list", "memories"): P,
    (LITE, "cmd_prime", "memories"): P,
    (LITE, "cmd_recall", "memories"): P,
    (LITE, "cmd_recall", "memories_fts"): P,
    (LITE, "cmd_stats", "memories"): P,
    (LITE, "seed_plan", "memories"): I,
    ("organs/communities.py", "_extractive_summary", "memories"): P,
    ("organs/communities.py", "compute_summaries", "community_summaries"): I,   # the cache key
    ("organs/council.py", "add_owner_input", "council_sessions"): A,
    ("organs/council.py", "complete_session", "council_sessions"): A,
    ("organs/council.py", "get_session", "council_sessions"): A,
    ("organs/council.py", "get_session_history", "council_sessions"): A,
    ("organs/council.py", "record_contribution", "council_sessions"): A,
    ("organs/council.py", "stilled_context_memories", "memories"): P,
    (CP, "archive_counterfactual", "counterfactual_decisions"): A,
    (CP, "auto_capture_from_council", "council_sessions"): A,
    (CP, "get_counterfactual", "counterfactual_decisions"): A,
    (CP, "get_counterfactual", "memories"): P,
    (CP, "get_counterfactual_history", "counterfactual_decisions"): A,
    (CP, "get_counterfactual_history", "memories"): P,
    (CP, "get_pending_revisits", "counterfactual_decisions"): A,
    (CP, "get_pending_revisits", "memories"): P,
    (CP, "revisit_counterfactual", "counterfactual_decisions"): A,
    (LEDGER, "_meta", "memories"): D,                                   # quoted in a dream's summary, which is marked
    (LEDGER, "_restamp_parents", "memories"): I,
    (LEDGER, "apply", "dream_log"): D,
    (LEDGER, "apply", "memories"): I,
    (LEDGER, "plan", "dream_log"): D,
    (LEDGER, "plan", "memories"): via(LEDGER, "run"),
    (LEDGER, "run", "memories"): P,
    ("organs/dream_pipeline.py", "_derive_probe_labels", "raptor_clusters"): A,     # a project or tag name
    ("organs/dream_pipeline.py", "_raptor_summarize", "memories"): D,
    ("organs/embed_backfill.py", "_pending_rows", "memories"): I,
    ("organs/embedding_upgrade.py", "cmd_re_embed", "memories"): I,
    (GM, "acknowledge_gap", "gap_inventory"): P,
    (GM, "detect_stale_entities", "memories"): "clean",
    (GM, "detect_thin_entities", "memories"): "clean",
    (GM, "detect_unresolved_contradictions", "memories"): "clean",
    (GM, "dispatch_auto_research", "gap_inventory"): I,                 # compared, whole, with the approved topics
    (GM, "get_gap_report", "gap_inventory"): P,
    (GM, "sweep_stored_non_answers", "memories"): P,
    ("organs/hybrid_retrieval.py", "_hydrate_memory_rows", "memories"): via(CM, "cmd_recall"),
    ("organs/hybrid_retrieval.py", "reranker_score", "memories"): I,
    ("organs/kind_taxonomy.py", "backfill_kinds", "memories"): I,
    ("organs/marks.py", "_scalar", "memories"): I,
    ("organs/marks.py", "read_back", "memories"): I,
    ("organs/marks.py", "read_marks", "memories"): I,
    (MD, "_get_memory_metadata", "memories"): D,
    (MD, "_store_dream_as_reflection", "memories"): D,
    (MD, "get_dream_digest", "dream_log"): P,
    ("organs/memory_write.py", "pending_embeddings", "memories"): I,
    ("organs/memory_write.py", "purge_memory", "memories"): I,
    ("organs/memory_write.py", "stamp_source", "memories"): I,
    ("organs/organ_yield_sentinel.py", "_precision_reading", "memories"): I,
    ("organs/pre_cognitive_recall.py", "_recent_automation_hint", "memories"): P,
    ("organs/pre_cognitive_recall.py", "_recent_memory_stores", "memories"): P,
    ("organs/pre_cognitive_recall.py", "_vector_search_fallback", "memories"): via(CM, "cmd_anticipate"),
    ("organs/procedural_memory.py", "compute_promotion_score", "memories"): P,
    ("organs/procedural_memory.py", "get_review_candidates", "memories"): P,
    ("organs/procedural_memory.py", "get_usage_stats", "memories"): P,
    (PG, "get_derivatives_of", "memories"): P,
    (PG, "get_derived_from", "memories"): P,
    (PG, "get_provenance_chain", "memories"): P,
    ("organs/recall_channels.py", "_row", "memories"): via(CM, "cmd_recall"),
    ("organs/skill_graduation.py", "backfill_tiers", "memories"): P,
    ("organs/skill_graduation.py", "evaluate_memory", "memories"): P,
    ("organs/skill_graduation.py", "get_graduation_history", "memories"): P,
    ("organs/sleep_loop.py", "consolidate_episodics", "memories"): D,
    ("organs/sleep_loop.py", "generate_reflections", "memories"): D,
    ("organs/sleep_loop.py", "rerank_importance", "memories"): I,
    ("organs/temporal_edges.py", "_get_memory_content", "memories"): I,
    ("organs/temporal_edges.py", "extract_edges_heuristic", "memories"): I,
    ("organs/temporal_edges.py", "extract_edges_llm", "memories"): D,   # a prompt; what comes back is an edge, not text
    ("organs/tier_set.py", "_embed_ids", "memories"): I,
    ("organs/trust_tier.py", "select", "memories"): I,                  # the row's stamps
    ("organs/voidloom_canary.py", "pick_brain_only_fact", "memories"): "clean",
    ("organs/voidloom_preamble.py", "<module>", "memories"): via("organs/voidloom_preamble.py", "read_priming"),
    (WEB, "cmd_context", "memories"): "other",
    (WEB, "cmd_list", "memories"): "other",
    (WEB, "cmd_maintain", "memories"): "other",
    (WEB, "cmd_recall", "memories"): "other",
    (WEB, "cmd_stats", "memories"): "other",
    (WEB, "cmd_update", "memories"): "other",
    ("organs/whisper_network.py", "get_digest_history", "memories"): P,
    ("organs/cognitive_map.py", "get_edge_graph", "memories"): P,
    ("voidloom/cli.py", "_memory_record", "memories"): "label",
    ("voidloom/cli.py", "_text_matches_stamp", "memories"): I,
}
KINDS = {P, I, D, A, "label", "clean", "other", "fixture"}


def _text(node, held=None):
    """The text of a string expression: literals, f-strings (a value only known at run time is "{?}"), + and %, and a
    name the same function set to such an expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else
                       ((held or {}).get(v.value.id, "{?}") if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
                        else "{?}") for v in node.values)
    if isinstance(node, ast.Name) and held and node.id in held:
        return held[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _text(node.left, held)
        if left is None and not isinstance(node.op, ast.Add):
            return None
        return (left or "") + ((_text(node.right, held) or "") if isinstance(node.op, ast.Add) else "")
    return None


_NAME = r'(?:main\.|temp\.)?["\[`]?(?:{})["\]`]?(?![\w])'
_STOP = re.compile(r"\b(?:WHERE|GROUP|ORDER|LIMIT|UNION|INTERSECT|EXCEPT|HAVING|WINDOW|ON\s+CONFLICT|RETURNING)\b", re.I)
_AGGREGATE = re.compile(r"\b(?:COUNT|LENGTH|TYPEOF|MAX|MIN|SUM|AVG|TOTAL|COALESCE\s*\(\s*SUM)\s*\(\s*(?:DISTINCT\s+)?[\w.*]*\s*\)",
                        re.I)


def _arms(sql):
    """(the select list, the FROM clause) of every SELECT in a statement: a CTE's, a subquery's, each union arm's. The
    list runs to its FROM at the same depth of parentheses, the clause to the next keyword that ends it at that depth."""
    out = []
    for m in re.finditer(r"\bSELECT\b", sql, re.I):
        depth, i, start, cut = 0, m.end(), m.end(), None
        while i < len(sql):
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and re.match(r"FROM\b", sql[i:i + 5], re.I) and not re.match(r"\w", sql[i - 1]):
                cut = i
                break
            i += 1
        if cut is None:
            continue
        depth, j = 0, cut + 4
        while j < len(sql):
            ch = sql[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and _STOP.match(sql, j) and not re.match(r"\w", sql[j - 1]):
                break
            j += 1
        out.append((sql[start:cut], sql[cut:j]))
    return out


def reads(sql):
    """The tables whose words one statement selects. Every SELECT in it is read: a CTE, a subquery, each arm of a union. A
    table name may be schema-qualified or quoted; snippet() and highlight() are its text, and so are `*` and a column list
    only known at run time."""
    out = set()
    for cols, source in _arms(sql):
        cols = _AGGREGATE.sub("", cols)
        wild = bool(re.search(r"\b(?:snippet|highlight)\s*\(|\{\?\}|(?:^|[\s,.])\*", cols, re.I))
        known = {t for t, words in WORDS.items()
                 if re.search(r"(?:\bFROM|\bJOIN|,)\s*" + _NAME.format(t), source, re.I)
                 and (wild or re.search(rf"\b(?:{words})\b", cols, re.I))}
        out |= known
        if not known and re.match(r"\s*FROM\s*\{\?\}", source, re.I) and re.search(r"\b(?:content|summary|entity|dream_content)\b", cols, re.I):
            out.add("{?}")
    return out


def sites(source):
    """{(function, table)} for one module's source, docstrings left out."""
    out = set()

    def walk(node, fn, held):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn, held = node.name, {}
            for stmt in ast.walk(node):         # a statement assembled from strings held apart, by name
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    got = _text(stmt.value, held)
                    if got is not None:
                        held[stmt.targets[0].id] = got
                elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add) and isinstance(stmt.target, ast.Name) \
                        and stmt.target.id in held:
                    held[stmt.target.id] += _text(stmt.value, held) or ""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                continue                                    # a docstring, or a string nobody reads
            if isinstance(child, ast.Call) and getattr(child.func, "id", None) == "select_gaps_fair":
                out.add((fn, "gap_inventory"))              # it builds the statement from the columns it is given
            text = _text(child, held)
            if text:
                out.update((fn, t) for t in reads(text))
                if not isinstance(child, ast.BinOp):
                    continue
            walk(child, fn, held)
    walk(ast.parse(source), "<module>", {})
    return out


def census():
    found = set()
    for tree in TREES:
        for path in sorted((ROOT / tree).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith("voidloom/tests/"):
                found |= {(rel, fn, table) for fn, table in sites(path.read_text(encoding="utf-8"))}
    return found


def function(rel, fn):
    """Every definition of that name in the file: the source text of each."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    return [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == fn]


def called(node):
    """The names a function calls, however they are reached."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            out.add(n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None))
    return out


def words(node):
    """Every name, attribute and string a function mentions."""
    out = set()
    for n in ast.walk(node):
        out |= {getattr(n, "id", None), getattr(n, "attr", None)}
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


LEAK = ('''

def leak_top_words(conn):
    for r in conn.execute("WITH t AS (SELECT memory_id FROM memory_tags) "
                          "SELECT m.id, m.content FROM memories m JOIN t ON t.memory_id = m.id"): print(r[1])
    for r in conn.execute("SELECT id, content FROM main.memories"): print(r[1])
    for r in conn.execute('SELECT id, snippet(memories_fts, 1, "", "", "", 64) FROM memories_fts'): print(r[1])
''')


class EveryReaderIsClassified(unittest.TestCase):
    def test_the_census_is_what_the_table_says(self):
        found = census()
        new = sorted(found - set(READERS))
        self.assertEqual(new, [], "a statement reads a memory's words and nobody has said where they go: frame them "
                                  "(marks.present_rows / shown / present_copies) if they reach a person, a model, a file or "
                                  "another table, then classify the function here")
        self.assertEqual(sorted(set(READERS) - found), [], "these no longer read a memory's words: remove them")

    def test_three_planted_readers_fail_it(self):
        """A copy of a module with a reader of each kind the census once missed: each is a site no row of the table names."""
        rel = "organs/communities.py"
        planted = {(rel, fn, t) for fn, t in sites((ROOT / rel).read_text(encoding="utf-8") + LEAK)}
        self.assertEqual(sorted(planted - set(READERS)),
                         [(rel, "leak_top_words", "memories"), (rel, "leak_top_words", "memories_fts")])
        for statement in ("WITH t AS (SELECT memory_id FROM memory_tags) SELECT m.id, m.content FROM memories m JOIN t ON 1",
                          "SELECT id, content FROM main.memories",
                          'SELECT id, snippet(memories_fts, 1, "", "", "", 64) FROM memories_fts'):
            self.assertTrue(reads(statement), statement)

    def test_the_classes_are_known(self):
        for site, kind in READERS.items():
            self.assertTrue(kind in KINDS or (isinstance(kind, tuple) and kind[0] == "via"), (site, kind))

    def test_a_presenter_presents(self):
        for (rel, fn, _table), kind in READERS.items():
            if kind != "presenter":
                continue
            nodes = function(rel, fn)
            self.assertTrue(nodes, (rel, fn))
            for node in nodes:
                self.assertTrue(called(node) & PRESENTERS, f"{rel} {fn} is classified as a presenter and calls none")

    def test_rows_handed_on_reach_a_presenter(self):
        for (rel, fn, _table), kind in READERS.items():
            if not isinstance(kind, tuple):
                continue
            _via, to_rel, to_fn = kind
            nodes = function(to_rel, to_fn)
            self.assertTrue(nodes, kind)
            for node in nodes:
                self.assertTrue(called(node) & (PRESENTERS | {"_present_plan"}), f"{to_rel} {to_fn} presents nothing")

    def test_a_clean_selection_looks_at_the_mark(self):
        for (rel, fn, _table), kind in READERS.items():
            if kind != "clean":
                continue
            for node in function(rel, fn):
                self.assertTrue("clean_ids" in called(node) or any("untrusted_source" in (w or "") for w in words(node)),
                                f"{rel} {fn} is classified as reading clean rows only and looks at no mark")

    def test_a_new_reader_would_be_seen(self):
        cases = {
            'def f(c):\n    return c.execute("SELECT id, summary FROM memories").fetchall()\n': {("f", "memories")},
            'def f(c):\n    c.execute("SELECT * FROM memories WHERE id = 1")\n': {("f", "memories")},
            'def f(c, w):\n    c.execute(f"SELECT {w} FROM memories")\n': {("f", "memories")},
            'def f(c):\n    c.execute("SELECT e.id, m.content FROM memory_edges e "\n              "JOIN memories m ON 1")\n':
                {("f", "memories")},
            'def f(c, t):\n    c.execute(f"SELECT summary FROM {t}")\n': {("f", "{?}")},
            'def f(c):\n    c.execute("SELECT entity FROM gap_inventory")\n': {("f", "gap_inventory")},
            'class K:\n    def g(self, c):\n        def h():\n            c.execute("SELECT content FROM memories_fts")\n':
                {("h", "memories_fts")},
            'def f(c):\n    c.execute("SELECT COUNT(*) FROM memories")\n    c.execute("SELECT id FROM memories")\n': set(),
            'def f(c):\n    """SELECT summary FROM memories."""\n': set(),
            'def f(c):\n    c.execute("WITH t AS (SELECT memory_id FROM memory_tags) "\n              "SELECT m.id, m.content FROM memories m JOIN t ON t.memory_id = m.id")\n':
                {("f", "memories")},
            'def f(c):\n    c.execute("SELECT id, content FROM main.memories")\n': {("f", "memories")},
            'def f(c):\n    c.execute(\'SELECT id, snippet(memories_fts, 1, "", "", "", 64) FROM memories_fts\')\n':
                {("f", "memories_fts")},
            'def f(c):\n    c.execute(\'SELECT id, highlight(memories_fts, 0, "[", "]") FROM "memories_fts"\')\n':
                {("f", "memories_fts")},
            'def f(c):\n    c.execute(\'SELECT 1 FROM x UNION SELECT summary FROM [memories]\')\n': {("f", "memories")},
            'def f(c):\n    c.execute("SELECT summary FROM `memories` WHERE id IN (SELECT id FROM memory_tags)")\n':
                {("f", "memories")},
            'def f(c):\n    cols = "id, summary"\n    c.execute("SELECT " + cols + " FROM memories")\n': {("f", "memories")},
            'def f(c):\n    cols = "id, summary"\n    q = " FROM memories"\n    c.execute("SELECT " + cols + q)\n': {("f", "memories")},
            'def f(c, t):\n    q = "SELECT id, content"\n    q += f" FROM {t} JOIN memories m ON 1"\n    c.execute(q)\n': {("f", "memories")},
        }
        for source, want in cases.items():
            self.assertEqual(sites(source), want, source)


NOT_FOLLOWED = {
    "str.format": 'def f(c):\n    c.execute("SELECT id, {} FROM memories".format("content"))\n',
    "% formatting": 'def f(c):\n    c.execute("SELECT id, %s FROM memories" % "content")\n',
    "MAX of a text column": 'def f(c):\n    c.execute("SELECT source_project, MAX(content) FROM memories GROUP BY source_project")\n',
    "a comment before the table": 'def f(c):\n    c.execute("SELECT id, content FROM /* c */ memories")\n',
    "an attached schema": 'def f(c):\n    c.execute("SELECT id, content FROM brain.memories")\n',
    "str.join": 'def f(c):\n    c.execute(" ".join(["SELECT id, content", "FROM memories"]))\n',
    "an annotated assignment": 'def f(c):\n    q: str = "SELECT id, content "\n    c.execute(q + "FROM memories")\n',
    "str.replace templating": 'def f(c):\n    c.execute("SELECT id, COLS FROM memories".replace("COLS", "summary"))\n',
    "across functions": 'def cols():\n    return "SELECT id, content "\n\n\ndef f(c):\n    c.execute(cols() + "FROM memories")\n',
}


@pytest.mark.xfail(strict=True, reason="declared: the census does not follow this form")
@pytest.mark.parametrize("form", sorted(NOT_FOLLOWED))
def test_a_form_the_census_does_not_follow(form):
    assert any(table == "memories" for _fn, table in sites(NOT_FOLLOWED[form]))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Every Qwen model load passes a pinned revision.

THE CLAIM UNDER TEST: the GPU tier (Qwen3-Embedding-0.6B for vectors,
Qwen3-Reranker-0.6B for reranking) loads a fixed HuggingFace commit, so two
runs months apart score with the same weights. An unpinned `main` can move
between a benchmark run and its re-derivation, and nothing would say so.

Shipped as a control pair, like the provenance tests:
    *_control_*   the shipped source, and the check must read GREEN;
    *_saboteur_*  the pin removed, and the check must read RED.
"""
import ast
import re
import unittest
from pathlib import Path

ORGANS = Path(__file__).resolve().parents[2] / "organs"
SHA = re.compile(r"^[0-9a-f]{40}$")

# (file, model-name constant, revision constant, loader call names)
SITES = [
    ("embedding_upgrade.py", "QWEN_MODEL_NAME", "QWEN_REVISION", {"SentenceTransformer"}),
    ("qwen_reranker.py", "QWEN_RERANKER_MODEL_NAME", "QWEN_RERANKER_REVISION",
     {"from_pretrained"}),
]


def _call_name(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def unpinned_loads(source, model_const, rev_const, loaders):
    """Return the line numbers of loader calls on `model_const` that do not
    pass `revision=<rev_const>`. Empty list means every load is pinned; a
    file with NO such load is reported as [-1] so a renamed loader cannot
    pass by vanishing."""
    tree = ast.parse(source)
    found, bad = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in loaders:
            continue
        if not (node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == model_const):
            continue
        found += 1
        rev = [k for k in node.keywords if k.arg == "revision"]
        if not (rev and isinstance(rev[0].value, ast.Name) and rev[0].value.id == rev_const):
            bad.append(node.lineno)
    return bad if found else [-1]


def pinned_sha(source, rev_const):
    m = re.search(rf'^{rev_const}\s*=\s*"([^"]+)"', source, re.M)
    return m.group(1) if m else None


class QwenRevisionPinned(unittest.TestCase):

    def test_control_every_shipped_qwen_load_is_pinned(self):
        for fname, model_const, rev_const, loaders in SITES:
            src = (ORGANS / fname).read_text(encoding="utf-8")
            self.assertEqual(unpinned_loads(src, model_const, rev_const, loaders), [],
                             f"{fname}: a Qwen load without revision={rev_const}")

    def test_control_each_pin_is_a_full_commit_sha(self):
        for fname, _, rev_const, _ in SITES:
            sha = pinned_sha((ORGANS / fname).read_text(encoding="utf-8"), rev_const)
            self.assertIsNotNone(sha, f"{fname}: {rev_const} not defined")
            self.assertRegex(sha, SHA, f"{fname}: {rev_const} is not a 40-hex commit")

    def test_saboteur_a_removed_revision_kwarg_goes_red(self):
        fname, model_const, rev_const, loaders = SITES[0]
        src = (ORGANS / fname).read_text(encoding="utf-8")
        planted = src.replace(f"revision={rev_const},", "")
        self.assertNotEqual(planted, src, "saboteur did not plant")
        self.assertNotEqual(unpinned_loads(planted, model_const, rev_const, loaders), [])

    def test_saboteur_a_branch_name_instead_of_a_sha_goes_red(self):
        fname, _, rev_const, _ = SITES[1]
        src = (ORGANS / fname).read_text(encoding="utf-8")
        planted = re.sub(rf'^({rev_const}\s*=\s*)"[^"]+"', r'\1"main"', src, flags=re.M)
        self.assertNotRegex(pinned_sha(planted, rev_const), SHA)

    def test_saboteur_a_vanished_loader_is_not_a_pass(self):
        self.assertEqual(unpinned_loads("x = 1\n", "QWEN_MODEL_NAME", "QWEN_REVISION",
                                        {"SentenceTransformer"}), [-1])


if __name__ == "__main__":
    unittest.main()

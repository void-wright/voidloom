# -*- coding: utf-8 -*-
"""R3 -- install/lay_manifest.json is BOUND to the tree, and install/lay.py executes it.

Council #12 (2026-09-18): install.sh silently skipped 62 of 299 tracked files
under `2>/dev/null || true`, and install.ps1 laid a layout that no longer
existed. Two hand descriptions of one fact, neither bound to the tree. The lay
manifest is the one description; this file is what makes it a BINDING rather
than a second description (#20132, #19801).

Three groups:
  1. THE BINDING -- the committed manifest against `git ls-files --stage`, in
     both directions, sha per file, exec <=> mode 100755, no (root,dst)
     collision, and the lay RULE stated in the criterion's own words (DESSA's
     table), checked here independently of tools/build_manifest.py;
  2. THE EXECUTOR -- lay.py, wet, into a scratch root under ~/.crucible_scratch:
     lays exactly the selected set, verifies every sha, refuses rc 3 naming a
     missing source and a tampered source, writes nothing on --dry-run, and
     lays the council only when asked;
  3. THE FALSIFIER (the criterion's own) -- on a scratch COPY of the tree:
     remove one entry from the manifest and leave the file in the tree -> the
     binding is red AND lay.py lays only what the manifest says; add a tracked
     file and not a manifest entry -> red. If either passed silently the
     manifest would be a second description, not a binding.

At the parent commit the manifest, lay.py and build_manifest --lay do not exist:
every test in groups 1-2 is red by absence, and the falsifier is red because
the binding it mutates is already red. Nothing here ever runs the tree's
build_manifest.py as a subprocess unless that script ADVERTISES --lay: the
parent's script writes docs/PACKAGE_MANIFEST.md on any invocation, and this
suite must not write into a tree it measures (safety-006).

VOIDLOOM_ROOT points at the tree under test (default: this file's package).
VOIDLOOM_SCRATCH is where wet runs go (default: ~/.crucible_scratch); never /tmp.

These tests bind the CHECKOUT (the git index and the null-root front-door file
install.sh). On an installed brain neither exists: every class SKIPS with the
reason (UNMEASURABLE, not PASS) -- see _require_checkout().
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
MANIFEST_REL = "install/lay_manifest.json"
SELF_ENTRIES = {"install/lay_manifest.json", "docs/PACKAGE_MANIFEST.md"}
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch")).resolve()
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _require_checkout():
    """These tests bind the CHECKOUT (git index + the null-root front-door files). On an installed
    brain neither exists; a skip with the reason is the honest verdict (UNMEASURABLE, not PASS)."""
    if not (ROOT / "install.sh").is_file() or not (ROOT / ".git").exists():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh/.git (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_stage(root: Path):
    """[(mode, stage, path)] from `git ls-files --stage -z`; [] if not a checkout."""
    out = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=str(root),
                         capture_output=True, text=True, env=ENV)
    if out.returncode != 0:
        return []
    rows = []
    for rec in out.stdout.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        mode, _sha, stage = meta.split()
        rows.append((mode, int(stage), path))
    return rows


def expected_rule(src: str):
    """DESSA's table, in the criterion's words -- written here INDEPENDENTLY of
    tools/build_manifest.py so the binding is not the rule grading itself."""
    if src.endswith((".ps1", ".cmd")):
        plat = "windows"
    elif src.endswith(".sh") or src.startswith("cron/") or src == "voidloom/voidloom":
        plat = "posix"
    else:
        plat = "all"
    if "/" not in src:
        return None, None, plat, "core"
    if src.startswith("persona/skills/persona-forge/"):
        return "skills", src[len("persona/skills/"):], plat, "forge"
    if src.startswith("persona/skills/"):
        return "skills", src[len("persona/skills/"):], plat, "core"
    if src.startswith("persona/council_skills/"):
        return "skills", src[len("persona/council_skills/"):], plat, "council"
    if src.startswith("persona/commands/"):
        n = src[len("persona/commands/"):]
        return "home", "commands/" + n, plat, ("council" if n == "council.md" else
                                               "forge" if n == "persona-forge.md" else "core")
    return "brain", src, plat, "core"


def binding_problems(root: Path):
    """THE CHECK, reusable on any tree: [] if the manifest binds the tree, else the
    problems, each naming a file. Used on the tree under test and on the
    falsifier's mutated copies."""
    man = root / MANIFEST_REL
    if not man.is_file():
        return ["ABSENT: %s (tools/build_manifest.py --lay emits it)" % MANIFEST_REL]
    try:
        doc = json.loads(man.read_text(encoding="utf-8"))
        entries = doc["entries"]
    except Exception as e:  # noqa: BLE001
        return ["UNREADABLE: %s: %s" % (MANIFEST_REL, e)]
    rows = git_stage(root)
    if not rows:
        return ["NOT A CHECKOUT: git ls-files --stage returned nothing under %s" % root]
    staged = [(s, p) for (_m, s, p) in rows if s != 0]
    if staged:
        return ["MERGE STAGE != 0 (#20132): %s" % staged[:10]]
    tracked = {p: m for (m, _s, p) in rows}
    srcs = [e["src"] for e in entries]
    problems = []
    only_tree = sorted(set(tracked) - set(srcs))
    only_man = sorted(set(srcs) - set(tracked))
    if only_tree:
        problems.append("IN THE TREE, NOT IN THE MANIFEST (%d): %s" % (len(only_tree), only_tree[:10]))
    if only_man:
        problems.append("IN THE MANIFEST, NOT IN THE TREE (%d): %s" % (len(only_man), only_man[:10]))
    if len(srcs) != len(set(srcs)):
        problems.append("DUPLICATE src entries")
    for e in entries:
        p = root / e["src"]
        if e["src"] in tracked and e["sha256"] is not None and p.is_file():
            if sha256_of(p) != e["sha256"] or p.stat().st_size != e["bytes"]:
                problems.append("SHA/BYTES DIFFER: %s" % e["src"])
        if e["src"] in tracked and e["exec"] != (tracked[e["src"]] == "100755"):
            problems.append("EXEC BIT DISAGREES WITH GIT MODE: %s" % e["src"])
    return problems


def load_tree_build_manifest(root: Path):
    """Import the TREE's tools/build_manifest.py in-process (no bytecode written)."""
    p = root / "tools" / "build_manifest.py"
    if not p.is_file():
        return None
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("_tree_build_manifest_%d" % os.getpid(), str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)   # module level is defs + constants only; main() is guarded
        return mod
    finally:
        sys.dont_write_bytecode = saved


def export_tracked(root: Path, dst: Path):
    """Copy ROOT's tracked files (the working-tree bytes) into dst and make it a
    checkout whose index lists exactly them. Reads ROOT only."""
    dst.mkdir(parents=True, exist_ok=True)
    for (_m, _s, rel) in git_stage(root):
        s, d = root / rel, dst / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(s), str(d))
    subprocess.run(["git", "init", "-q"], cwd=str(dst), check=True, env=ENV)
    subprocess.run(["git", "add", "-A"], cwd=str(dst), check=True, env=ENV)


def scratch_dir(prefix: str) -> Path:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(SCRATCH)))


# ===========================================================================
# 1. THE BINDING
# ===========================================================================
class TestLayManifestBindsTheTree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _require_checkout()
        cls.rows = git_stage(ROOT)
        cls.man = ROOT / MANIFEST_REL
        cls.doc = json.loads(cls.man.read_text(encoding="utf-8")) if cls.man.is_file() else None

    def setUp(self):
        _require_checkout()
        self.assertTrue(self.rows, "git ls-files --stage returned nothing under %s -- an empty "
                                   "population is a refusal, not a pass" % ROOT)
        self.assertTrue(self.man.is_file(), "%s is absent from %s" % (MANIFEST_REL, ROOT))
        self.entries = self.doc["entries"]

    def test_manifest_binds_the_tree_in_both_directions(self):
        problems = binding_problems(ROOT)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_entry_count_equals_git_ls_files_sort_u_wc_l(self):
        n_tree = len(sorted({p for (_m, _s, p) in self.rows}))
        self.assertEqual(len(self.entries), n_tree,
                         "manifest entries %d != git ls-files | sort -u | wc -l = %d" % (len(self.entries), n_tree))
        self.assertEqual(self.doc["_meta"]["entries"], len(self.entries))

    def test_null_sha_set_is_exactly_the_two_self_entries(self):
        nulls = {e["src"] for e in self.entries if e["sha256"] is None}
        self.assertEqual(nulls, SELF_ENTRIES, "entries without a hash must be exactly the two derived "
                                              "inventories (each cannot hash itself or the other): %s" % sorted(nulls))
        self.assertEqual(set(self.doc["_meta"]["self_entries"]), SELF_ENTRIES)
        for e in self.entries:
            self.assertEqual(e["sha256"] is None, e["bytes"] is None, e["src"])

    def test_no_root_dst_collision(self):
        seen = {}
        for e in self.entries:
            if e["root"] is None:
                continue
            key = (e["root"], e["dst"])
            self.assertNotIn(key, seen, "%s and %s both land at %s" % (seen.get(key), e["src"], key))
            seen[key] = e["src"]

    def test_field_vocabularies(self):
        for e in self.entries:
            self.assertIn(e["root"], ("brain", "skills", "home", None), e["src"])
            self.assertIn(e["platform"], ("all", "posix", "windows"), e["src"])
            self.assertIn(e["component"], ("core", "council", "forge"), e["src"])
            self.assertEqual(e["root"] is None, e["dst"] is None, e["src"])
            self.assertEqual(e["root"] is None, "/" not in e["src"] or expected_rule(e["src"])[0] is None,
                             "%s: only front-door files (no slash) and the named developer history may ship unlaid"
                             % e["src"])
            self.assertIsInstance(e["exec"], bool, e["src"])

    def test_lay_rule_in_the_criterion_terms(self):
        """DESSA's table, checked independently of build_manifest.py."""
        wrong = []
        for e in self.entries:
            want = expected_rule(e["src"])
            got = (e["root"], e["dst"], e["platform"], e["component"])
            if want != got:
                wrong.append("%s: expected %s got %s" % (e["src"], want, got))
        self.assertEqual(wrong, [], "\n".join(wrong))

    def test_council_entries_are_exactly_the_lenses_and_the_command(self):
        council = sorted(e["src"] for e in self.entries if e["component"] == "council")
        expect = sorted(p for (_m, _s, p) in self.rows
                        if p.startswith("persona/council_skills/") or p == "persona/commands/council.md")
        self.assertEqual(council, expect)
        self.assertGreaterEqual(len(council), 6, "the five lenses and /council")

    def test_the_previously_unlaid_files_are_laid(self):
        """The files install.sh once skipped: docs/gates, tools/harness (10), persona/skills + council (5).
        All must carry a root."""
        mod = load_tree_build_manifest(ROOT)
        history = set(getattr(mod, "HISTORY_SHIPS_NOT_LAID", ()) or ())
        unlaid = [e["src"] for e in self.entries if e["root"] is None
                  and (e["src"].startswith(("docs/", "tools/harness/", "persona/"))) and e["src"] not in history]
        self.assertEqual(unlaid, [], "still unlaid: %s" % unlaid)
        gates = [e for e in self.entries if e["src"].startswith("docs/gates/")]
        harness = [e for e in self.entries if e["src"].startswith("tools/harness/")]
        self.assertGreaterEqual(len(gates), 2, "docs/gates: %d entries" % len(gates))
        self.assertEqual(len(harness), 10, "tools/harness: %d entries" % len(harness))
        self.assertTrue(all(e["root"] == "brain" for e in gates + harness))

    def test_developer_history_is_refused_in_the_public_tree(self):
        """The repair records (docs/cycle1, docs/cycle2) name the original owner's machines and engagements; they stay
        in the private archive. None is tracked, and the derivation REFUSES one that comes back."""
        mod = load_tree_build_manifest(ROOT)
        tracked = {p for (_m, _s, p) in self.rows}
        self.assertEqual(sorted(p for p in tracked if p.startswith(mod.HISTORY_DIRS)), [])
        self.assertEqual(mod.history_problems(tracked), [])
        self.assertEqual(mod.history_problems(tracked | {"docs/cycle2/NEW.md"})[0][:9], "REFUSED: ")

    def test_committed_manifest_equals_a_fresh_derivation(self):
        """conventions-003: before grading a DERIVED artifact, assert the committed
        derivation matches its source. Uses the tree's own generator, in-process."""
        mod = load_tree_build_manifest(ROOT)
        self.assertIsNotNone(mod, "tools/build_manifest.py absent")
        self.assertTrue(hasattr(mod, "derive_lay_entries"),
                        "this tree's tools/build_manifest.py has no --lay derivation (parent shape)")
        entries, refusals, _head = mod.derive_lay_entries(ROOT)
        self.assertEqual(refusals, [], refusals)
        self.assertEqual(entries, self.entries, "the committed manifest is STALE: regenerate with "
                                                "tools/build_manifest.py --lay")

    def test_build_manifest_check_verb_reports_clean(self):
        mod = load_tree_build_manifest(ROOT)
        self.assertTrue(mod is not None and hasattr(mod, "check_lay_manifest"),
                        "this tree's tools/build_manifest.py has no `--lay --check` (parent shape); "
                        "NOT executed, because the parent's script writes on every invocation")
        SCRATCH.mkdir(parents=True, exist_ok=True)     # a fresh VOIDLOOM_SCRATCH is not a failure (item 4)
        p = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "build_manifest.py"), "--lay", "--check"],
                           cwd=str(SCRATCH), capture_output=True, text=True, env=ENV)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_generator_refuses_a_merge_stage(self):
        """#20132: an index entry at stage != 0 must be a refusal, not a manifest."""
        mod = load_tree_build_manifest(ROOT)
        self.assertTrue(mod is not None and hasattr(mod, "derive_lay_entries"), "no --lay derivation")
        copy = scratch_dir("lay_stage_")
        try:
            export_tracked(ROOT, copy / "t")
            # manufacture a stage-2 entry with update-index (a conflict without a merge)
            rel = "NOTICE"
            blob = subprocess.run(["git", "hash-object", "-w", rel], cwd=str(copy / "t"),
                                  capture_output=True, text=True, env=ENV, check=True).stdout.strip()
            subprocess.run(["git", "update-index", "--index-info"], cwd=str(copy / "t"), env=ENV,
                           input="0 0000000000000000000000000000000000000000\t%s\n100644 %s 2\t%s\n" % (rel, blob, rel),
                           text=True, check=True)
            rows = git_stage(copy / "t")
            self.assertTrue(any(s != 0 for (_m, s, _p) in rows), "could not manufacture a merge stage")
            entries, refusals, _ = mod.derive_lay_entries(copy / "t")
            self.assertIsNone(entries)
            self.assertTrue(any("stage" in r for r in refusals), refusals)
            self.assertTrue(any(rel in r for r in refusals), "the refusal must name the entry: %s" % refusals)
            # the SIBLING emitter refuses the same index and writes nothing (round-1
            # reflection, item 4: --package alone wrote '312 files' for a 311-file tree)
            self.assertTrue(hasattr(mod, "index_problems"), "no shared index_problems() -- the guard is not shared")
            pkg_man = copy / "t" / "docs" / "PACKAGE_MANIFEST.md"
            before = pkg_man.read_bytes()
            self.assertEqual(mod.write_package_manifest(copy / "t"), 2)
            self.assertEqual(pkg_man.read_bytes(), before, "--package wrote a manifest from a half-merged index")
        finally:
            shutil.rmtree(copy, ignore_errors=True)


# ===========================================================================
# 2. THE EXECUTOR
# ===========================================================================
class TestLayPyExecutesTheManifest(unittest.TestCase):

    def setUp(self):
        _require_checkout()
        self.lay = ROOT / "install" / "lay.py"
        self.man = ROOT / MANIFEST_REL
        self.assertTrue(self.lay.is_file(), "install/lay.py absent from %s" % ROOT)
        self.assertTrue(self.man.is_file(), "%s absent" % MANIFEST_REL)
        self.doc = json.loads(self.man.read_text(encoding="utf-8"))
        self.tmp = scratch_dir("lay_exec_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _apply(self, manifest, root, extra=(), platform="posix"):
        cmd = [sys.executable, "-B", str(self.lay), "apply", "--manifest", str(manifest), "--platform", platform,
               "--root", "brain=%s" % (root / ".claude" / "memory"), "--root", "skills=%s" % (root / ".claude" / "skills"),
               "--root", "home=%s" % (root / ".claude"), "--json"] + list(extra)
        p = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
        try:
            rep = json.loads(p.stdout)
        except Exception:  # noqa: BLE001
            rep = None
        return p.returncode, rep, p.stdout + p.stderr

    def _selected(self, platform="posix", council=False):
        return [e for e in self.doc["entries"] if e["root"] is not None
                and e["platform"] in ("all", platform)
                and (e["component"] == "core" or council)]

    def test_lays_exactly_the_selected_set_and_verifies_every_sha(self):
        rc, rep, out = self._apply(self.man, self.tmp / "h")
        self.assertEqual(rc, 0, out[-2000:])
        sel = self._selected()
        self.assertEqual(rep["selected"], len(sel))
        self.assertEqual(rep["laid"], len(sel))
        self.assertEqual(rep["verified"], len(sel))
        self.assertEqual(rep["selected"] + len(rep["ships_not_laid"]) + len(rep["other_platform"])
                         + len(rep["other_component"]), len(self.doc["entries"]),
                         "every manifest entry is accounted for in exactly one bucket")
        roots = {"brain": self.tmp / "h/.claude/memory", "skills": self.tmp / "h/.claude/skills", "home": self.tmp / "h/.claude"}
        laid = sorted(p for p in (self.tmp / "h").rglob("*") if p.is_file())
        self.assertEqual(len(laid), len(sel), "files on disk %d != selected %d" % (len(laid), len(sel)))
        for e in sel:
            dst = roots[e["root"]] / e["dst"]
            self.assertTrue(dst.is_file(), "not laid: %s -> %s" % (e["src"], dst))
            want = e["sha256"] if e["sha256"] is not None else sha256_of(ROOT / e["src"])
            self.assertEqual(sha256_of(dst), want, e["src"])
            if e["exec"]:
                self.assertTrue(os.access(str(dst), os.X_OK), "exec bit missing: %s" % e["src"])
        self.assertEqual(sorted(rep["verified_against_source"]), sorted(SELF_ENTRIES & {e["src"] for e in sel}))

    def test_dry_run_writes_nothing(self):
        rc, rep, out = self._apply(self.man, self.tmp / "d", ["--dry-run"])
        self.assertEqual(rc, 0, out[-1000:])
        self.assertEqual(rep["status"], "planned")
        self.assertFalse((self.tmp / "d").exists(), "a dry run created the destination")

    def test_refuses_a_missing_source_rc3_naming_it(self):
        pkg = self.tmp / "pkg"
        export_tracked(ROOT, pkg)
        victim = "organs/claude_memory_lite.py"
        (pkg / victim).unlink()
        rc, rep, out = self._apply(pkg / MANIFEST_REL, self.tmp / "m")
        self.assertEqual(rc, 3, out[-1000:])
        classes = {(p["class"], p["src"]) for p in rep["problems"]}
        self.assertIn(("MISSING_SOURCE", victim), classes, rep["problems"][:3])
        self.assertEqual(rep["status"], "refused")
        self.assertFalse((self.tmp / "m").exists(), "a refused lay wrote something")

    def test_refuses_a_tampered_source_rc3_naming_it(self):
        pkg = self.tmp / "pkg2"
        export_tracked(ROOT, pkg)
        victim = "organs/schema_migrator.py"
        with (pkg / victim).open("ab") as f:
            f.write(b"\n# tampered\n")
        rc, rep, out = self._apply(pkg / MANIFEST_REL, self.tmp / "t")
        self.assertEqual(rc, 3, out[-1000:])
        classes = {(p["class"], p["src"]) for p in rep["problems"]}
        self.assertIn(("SOURCE_MISMATCH", victim), classes, rep["problems"][:3])
        self.assertFalse((self.tmp / "t").exists())

    def test_refuses_a_symlink_at_the_destination_rc3_and_writes_nothing_through_it(self):
        """Round-1 reflection, item 11: a symlink at dst pointing at a file OUTSIDE the
        roots must be a refusal naming it -- never a copy through the link."""
        outside = self.tmp / "outside_the_roots.txt"
        outside.write_text("untouched\n", encoding="utf-8")
        home = self.tmp / "s"
        victim_dst = home / ".claude" / "memory" / "organs" / "schema_migrator.py"
        victim_dst.parent.mkdir(parents=True)
        os.symlink(str(outside), str(victim_dst))
        rc, rep, out = self._apply(self.man, home)
        self.assertEqual(rc, 3, out[-1000:])
        self.assertEqual(rep["status"], "refused")
        classes = {(p["class"], p["src"]) for p in rep["problems"]}
        self.assertIn(("DEST_IS_SYMLINK", "organs/schema_migrator.py"), classes, rep["problems"][:3])
        self.assertTrue(any(str(outside) in p["detail"] for p in rep["problems"]), "the refusal must name the target")
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n", "lay.py wrote THROUGH the symlink")
        self.assertTrue(victim_dst.is_symlink(), "the link itself was replaced")
        laid = [p for p in home.rglob("*") if p.is_file() and not p.is_symlink()]
        self.assertEqual(laid, [], "a refused lay wrote something: %s" % laid[:3])

    def test_refuses_a_symlinked_ancestor_directory_rc3_and_writes_nothing_outside(self):
        """Round-1 iteration-2 reflection, item 5: a symlinked DIRECTORY between the root and
        dst was followed by mkdir -p + copyfile -- measured, 66 files written OUTSIDE the
        roots, rc 0, 'laid 297; verified 297'. Now a pre-flight refusal naming the link."""
        outside = self.tmp / "outside_organs"
        outside.mkdir()
        home = self.tmp / "sa"
        brain = home / ".claude" / "memory"
        brain.mkdir(parents=True)
        os.symlink(str(outside), str(brain / "organs"))
        rc, rep, out = self._apply(self.man, home)
        self.assertEqual(rc, 3, out[-1000:])
        self.assertEqual(rep["status"], "refused")
        classes = {p["class"] for p in rep["problems"]}
        self.assertEqual(classes, {"DEST_PARENT_IS_SYMLINK"}, sorted(classes))
        n_organs = len([e for e in self._selected() if e["root"] == "brain" and e["dst"].startswith("organs/")])
        self.assertEqual(len(rep["problems"]), n_organs, "one refusal per entry beneath the link")
        self.assertTrue(all(str(brain / "organs") in p["detail"] and str(outside) in p["detail"] for p in rep["problems"]),
                        "each refusal must name the link and its target: %s" % rep["problems"][:2])
        self.assertEqual(sorted(x.name for x in outside.iterdir()), [], "lay.py wrote THROUGH the symlinked ancestor")
        laid = [p for p in home.rglob("*") if p.is_file() and not p.is_symlink()]
        self.assertEqual(laid, [], "a refused lay wrote something: %s" % laid[:3])
        self.assertTrue((brain / "organs").is_symlink(), "the link itself was replaced")

    def test_refuses_a_file_at_an_ancestor_rc3_and_lays_nothing(self):
        """The second member of item 5: a regular FILE where a destination directory must be
        was an uncaught FileExistsError after one file had been laid (a half-lay, rc 1)."""
        home = self.tmp / "fa"
        brain = home / ".claude" / "memory"
        brain.mkdir(parents=True)
        (brain / "organs").write_text("not a directory\n", encoding="utf-8")
        rc, rep, out = self._apply(self.man, home)
        self.assertEqual(rc, 3, out[-1000:])
        self.assertEqual(rep["status"], "refused")
        self.assertEqual({p["class"] for p in rep["problems"]}, {"DEST_PARENT_IS_FILE"})
        self.assertTrue(all(str(brain / "organs") in p["detail"] for p in rep["problems"]))
        laid = [p for p in home.rglob("*") if p.is_file()]
        self.assertEqual(laid, [brain / "organs"], "a refused lay wrote something: %s" % laid[:3])
        self.assertEqual((brain / "organs").read_text(encoding="utf-8"), "not a directory\n")

    def test_council_is_laid_only_when_asked(self):
        rc, rep, _ = self._apply(self.man, self.tmp / "c1")
        self.assertEqual(rc, 0)
        skills = self.tmp / "c1/.claude/skills"
        self.assertEqual(sorted(p.name for p in skills.iterdir() if p.name.startswith("council-")), [])
        self.assertFalse((self.tmp / "c1/.claude/commands/council.md").exists())
        n_forge = len([e for e in self.doc["entries"] if e["component"] == "forge"
                       and e["platform"] in ("all", "posix")])
        self.assertEqual(len(rep["other_component"]), 6 + n_forge)
        rc, rep, _ = self._apply(self.man, self.tmp / "c2", ["--component", "council"])
        self.assertEqual(rc, 0)
        skills = self.tmp / "c2/.claude/skills"
        self.assertEqual(len([p for p in skills.iterdir() if p.name.startswith("council-")]), 5)
        self.assertTrue((self.tmp / "c2/.claude/commands/council.md").is_file())
        self.assertEqual(len(rep["other_component"]), n_forge, "only the forge remains unrequested")

    def test_persona_forge_is_its_own_component_laid_only_when_asked(self):
        """The Persona Forge ships as component `forge`: install/install_persona_forge.sh lays it under the
        never-clobber contract (install.sh calls it after the acceptance gate), so core never lays it."""
        forge = sorted(e["src"] for e in self.doc["entries"] if e["component"] == "forge")
        expect = sorted(p for (_m, _s, p) in git_stage(ROOT)
                        if p.startswith("persona/skills/persona-forge/") or p == "persona/commands/persona-forge.md")
        self.assertEqual(forge, expect)
        self.assertIn("persona/skills/persona-forge/SKILL.md", forge)
        self.assertIn("persona/commands/persona-forge.md", forge)
        rc, rep, _ = self._apply(self.man, self.tmp / "f1")
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / "f1/.claude/skills/persona-forge").exists())
        rc, rep, _ = self._apply(self.man, self.tmp / "f2", ["--component", "forge"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "f2/.claude/skills/persona-forge/SKILL.md").is_file())
        self.assertTrue((self.tmp / "f2/.claude/commands/persona-forge.md").is_file())

    def test_refuses_an_unsupplied_root_rc2(self):
        p = subprocess.run([sys.executable, "-B", str(self.lay), "apply", "--manifest", str(self.man),
                            "--platform", "posix", "--root", "brain=%s" % (self.tmp / "r")],
                           capture_output=True, text=True, env=ENV)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("skills", p.stderr)
        self.assertFalse((self.tmp / "r").exists())

    def test_windows_platform_selects_the_windows_files(self):
        rc, rep, out = self._apply(self.man, self.tmp / "w", platform="windows")
        self.assertEqual(rc, 0, out[-1000:])
        self.assertEqual(rep["selected"], len(self._selected("windows")))
        self.assertTrue((self.tmp / "w/.claude/memory/voidloom/voidloom.cmd").is_file())
        self.assertFalse((self.tmp / "w/.claude/memory/voidloom/voidloom").exists())
        self.assertFalse((self.tmp / "w/.claude/memory/cron").exists())


# ===========================================================================
# 3. THE FALSIFIER
# ===========================================================================
class TestFalsifier(unittest.TestCase):
    """Remove an entry -> red, and lay.py lays only what the manifest says.
    Add a tree file -> red. Executed on a scratch copy; the tree is never touched."""

    def setUp(self):
        _require_checkout()
        self.assertTrue((ROOT / MANIFEST_REL).is_file(), "%s absent -- the binding is already red" % MANIFEST_REL)
        self.assertEqual(binding_problems(ROOT), [], "the binding must be GREEN before it can be falsified")
        self.tmp = scratch_dir("lay_falsifier_")
        self.copy = self.tmp / "tree"
        export_tracked(ROOT, self.copy)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removing_a_manifest_entry_is_red_and_lay_lays_only_what_the_manifest_says(self):
        man = self.copy / MANIFEST_REL
        doc = json.loads(man.read_text(encoding="utf-8"))
        victim = "tools/harness/test_harness.py"
        before = len(doc["entries"])
        doc["entries"] = [e for e in doc["entries"] if e["src"] != victim]
        doc["_meta"]["entries"] = before - 1
        man.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        self.assertTrue((self.copy / victim).is_file(), "the file must stay in the tree")
        problems = binding_problems(self.copy)
        self.assertTrue(any(victim in p and "NOT IN THE MANIFEST" in p for p in problems),
                        "the binding did not go red naming %s: %s" % (victim, problems))
        # and lay.py does not silently "fix" coverage
        home = self.tmp / "home"
        p = subprocess.run([sys.executable, "-B", str(self.copy / "install" / "lay.py"), "apply",
                            "--manifest", str(man), "--platform", "posix",
                            "--root", "brain=%s" % (home / "memory"), "--root", "skills=%s" % (home / "skills"),
                            "--root", "home=%s" % home, "--json"], capture_output=True, text=True, env=ENV)
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertFalse((home / "memory" / victim).exists(), "lay.py laid a file the manifest does not name")
        selected_full = len([e for e in json.loads((ROOT / MANIFEST_REL).read_text())["entries"]
                             if e["root"] is not None and e["platform"] in ("all", "posix") and e["component"] == "core"])
        self.assertEqual(rep["laid"], selected_full - 1)

    def test_adding_a_tree_file_not_in_the_manifest_is_red(self):
        new = self.copy / "docs" / "NEW_UNLISTED.md"
        new.write_text("a tracked file the manifest does not know\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/NEW_UNLISTED.md"], cwd=str(self.copy), check=True, env=ENV)
        problems = binding_problems(self.copy)
        self.assertTrue(any("docs/NEW_UNLISTED.md" in p and "IN THE TREE" in p for p in problems),
                        "the binding did not go red naming the new file: %s" % problems)


if __name__ == "__main__":
    unittest.main()

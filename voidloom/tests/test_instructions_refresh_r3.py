# -*- coding: utf-8 -*-
"""Round 3 G1 (the coordinator's item, executed on the reference host 2026-10-01 22:3x): `voidloom instructions` refreshes
a STALE file IN PLACE, uninstall removes BOTH of our block types, and every SKIP names its reason.

MEASURED on the host (restored byte-exact from its pre-image afterwards):
  1. `instructions --install --yes` on a stale file printed "stale ... SKIPPED" and no reason, while its stale_reason told
     the owner to uninstall and install again;
  2. following that advice, `--uninstall --yes` removed the old ACTIVATION block of ~/.agents/AGENTS.md and LEFT the old
     DOCTRINE block (hand-placed, so no receipt named it), and `--install --yes` then APPENDED a fresh activation + doctrine:
     TWO doctrine blocks, and status still read "stale".
The host's exact shape is reproduced on copies in the round's records (r3_g1/instr/repro_host_shape.py). Here the same
SHAPE is built from package text only (no owner's prose ships), in a scratch HOME with a receipt like the host's.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
ORG = ROOT / "organs" / "voidloom_instructions.py"
MB, ME = "# >>> VOIDLOOM BEGIN >>>", "# <<< VOIDLOOM END <<<"
DB, DE = "# >>> VOIDLOOM DOCTRINE BEGIN >>>", "# <<< VOIDLOOM DOCTRINE END <<<"
OLD_NOTICE = (MB + "\n## VOIDLOOM memory brain — activation check\n(installed by `voidloom instructions install`; remove "
              "with `voidloom instructions uninstall`)\n\nAn OLDER notice body, as an install of September wrote it.\n" + ME + "\n")
OLD_DOCTRINE = (DB + "\n## VOIDLOOM memory brain — the doctrine (file tier)\n(an older doctrine, placed by hand)\n\nOld "
                "doctrine text.\n" + DE + "\n")
OWNER_HEAD = "# My own global notes\nKeep answers short.\n\n"
OWNER_TAIL = "\n## My second section\nAlways cite sources.\n"


def _counts(t):
    import re
    return {"notice": len(re.findall(r"^%s$" % re.escape(MB), t, re.M)),
            "doctrine": len(re.findall(r"^%s$" % re.escape(DB), t, re.M))}


def _outside(t):
    """Every byte outside OUR blocks (a pair of our markers), in order."""
    out, i = [], 0
    while True:
        a = min([x for x in (t.find(MB, i), t.find(DB, i)) if x >= 0], default=-1)
        if a < 0:
            out.append(t[i:])
            return "".join(out)
        end = ME if t.startswith(MB, a) else DE
        b = t.find(end, a) + len(end)
        if t[b:b + 1] == "\n":
            b += 1
        out.append(t[i:a])
        i = b


class Home:
    def __init__(self, root: Path):
        self.root = root
        self.h = root / "home"
        self.brain = self.h / ".claude" / "memory"
        self.brain.mkdir(parents=True)
        (self.h / "tmp").mkdir()
        shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(self.brain / "claude_memory.db"))
        self.agents = self.h / ".agents" / "AGENTS.md"
        self.codex = self.h / ".codex" / "AGENTS.md"
        self.claude = self.h / ".claude" / "CLAUDE.md"
        for f in (self.agents, self.codex):
            f.parent.mkdir(parents=True, exist_ok=True)
        self.env = {"HOME": str(self.h), "WIZARD_LIBRARY_HOME": str(self.brain),
                    "WIZARD_LIBRARY_DB": str(self.brain / "claude_memory.db"), "TMPDIR": str(self.h / "tmp"),
                    "XDG_CONFIG_HOME": str(self.h / ".config"), "XDG_CACHE_HOME": str(self.h / ".cache"),
                    "XDG_DATA_HOME": str(self.h / ".local/share"), "XDG_STATE_HOME": str(self.h / ".local/state"),
                    "PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}

    def host_shape(self, agents_text=None):
        """Tonight's shape: AGENTS.md = the owner's prose + an OLD notice + a hand-placed OLD doctrine; Codex's file = the
        OLD notice (created by us); CLAUDE.md = the owner's prose + our import line; a receipt naming the three as the
        host's did (agents/claude appended over an empty pre-image, codex created)."""
        imp = MB + "\n@%s\n" % self.agents + ME + "\n"
        self.agents.write_text(agents_text if agents_text is not None
                               else OWNER_HEAD + OLD_NOTICE + "\n" + OLD_DOCTRINE + OWNER_TAIL, encoding="utf-8")
        self.codex.write_text(OLD_NOTICE, encoding="utf-8")
        self.claude.write_text(imp, encoding="utf-8")
        empty = hashlib.sha256(b"").hexdigest()
        store = self.brain / "voidloom_preimages"
        store.mkdir(exist_ok=True)
        (store / ("%s.bytes" % empty)).write_bytes(b"")
        def sha(p):
            return hashlib.sha256(p.read_bytes()).hexdigest()
        touched = [
            {"platform": "agents-md", "mechanism": "instructions", "installed_at": "2026-09-15T00:52:35Z",
             "path": str(self.agents), "action": "restore_preimage", "install_action": "append",
             "preimage_path": str(store / ("%s.bytes" % empty)), "preimage_sha256": empty,
             "postimage_sha256": hashlib.sha256((OLD_NOTICE).encode()).hexdigest(), "element": OLD_NOTICE,
             "element_kind": "marker_block", "created_dirs": []},
            {"platform": "codex", "mechanism": "instructions", "installed_at": "2026-09-15T00:52:35Z",
             "path": str(self.codex), "action": "delete_file", "install_action": "create", "preimage_path": None,
             "preimage_sha256": None, "postimage_sha256": sha(self.codex), "element": OLD_NOTICE,
             "element_kind": "marker_block", "created_dirs": []},
            {"platform": "claude-code", "mechanism": "instructions", "installed_at": "2026-09-15T00:52:35Z",
             "path": str(self.claude), "action": "restore_preimage", "install_action": "append",
             "preimage_path": str(store / ("%s.bytes" % empty)), "preimage_sha256": empty,
             "postimage_sha256": sha(self.claude), "element": imp, "element_kind": "marker_block", "created_dirs": []},
        ]
        (self.brain / "voidloom_install_receipt.json").write_text(json.dumps(
            {"schema_version": 2, "touched": touched, "undone": []}, indent=1), encoding="utf-8")

    def run(self, *args):
        return subprocess.run([sys.executable, "-B", str(ORG)] + list(args), capture_output=True, text=True,
                              env=self.env, timeout=300, cwd=str(self.h))


@pytest.fixture
def home():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="instr_refresh_r3_", dir=str(SCRATCH)))
    yield Home(root)
    shutil.rmtree(str(root), ignore_errors=True)


def test_install_on_the_stale_host_shape_refreshes_in_place(home):
    home.host_shape()
    before = {f: f.read_text() for f in (home.agents, home.codex, home.claude)}
    p = home.run("--install", "--yes")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-800:]
    assert "refresh" in p.stdout and "WRITTEN" in p.stdout, p.stdout[-1500:]
    for f, want in ((home.agents, {"notice": 1, "doctrine": 1}), (home.codex, {"notice": 1, "doctrine": 1}),
                    (home.claude, {"notice": 1, "doctrine": 0})):
        t = f.read_text()
        assert _counts(t) == want, (f, _counts(t))
        assert _outside(t) == _outside(before[f]), "a byte outside our blocks changed in %s" % f
        if want["doctrine"]:
            assert t.index(MB) < t.index(DB), "the doctrine is not where the role expects it (after the notice)"
    a = home.agents.read_text()
    assert a.startswith(OWNER_HEAD) and a.endswith(OWNER_TAIL), "the owner's prose was moved"
    assert "An OLDER notice body" not in a and "Old doctrine text." not in a
    st = home.run("--status")
    assert "STALE" not in st.stdout, st.stdout


def test_a_second_install_changes_nothing(home):
    home.host_shape()
    assert home.run("--install", "--yes").returncode == 0
    snap = {f: f.read_bytes() for f in (home.agents, home.codex, home.claude)}
    p = home.run("--install", "--yes")
    assert p.returncode == 0 and "ALREADY_INSTALLED" in p.stdout, p.stdout[-800:]
    assert {f: f.read_bytes() for f in snap} == snap


def test_the_doubled_shape_tonights_bad_result_is_repaired_to_one_of_each(home):
    """The file the owner's sequence produced: a doctrine, then a notice, then a SECOND doctrine."""
    home.host_shape(agents_text=OWNER_HEAD + OLD_DOCTRINE + OLD_NOTICE + "\n" + OLD_DOCTRINE.replace("Old", "Older") + OWNER_TAIL)
    before = home.agents.read_text()
    assert _counts(before) == {"notice": 1, "doctrine": 2}
    assert home.run("--install", "--yes").returncode == 0
    t = home.agents.read_text()
    assert _counts(t) == {"notice": 1, "doctrine": 1} and t.index(MB) < t.index(DB), t
    assert _outside(t) == _outside(before)


def test_uninstall_removes_both_of_our_block_types_even_one_no_receipt_names(home):
    home.host_shape()
    p = home.run("--uninstall", "--yes")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-800:]
    a = home.agents.read_text()
    assert _counts(a) == {"notice": 0, "doctrine": 0}, a
    assert MB not in a and DB not in a and "Old doctrine text." not in a
    assert "OUR BLOCKS LEFT AFTER THE RECEIPT'S UNDO" in p.stdout and "REMOVED" in p.stdout, p.stdout[-1500:]
    assert not home.codex.exists(), "the file we created was not deleted"
    assert _counts(home.claude.read_text()) == {"notice": 0, "doctrine": 0}
    assert home.run("--install", "--yes").returncode == 0                        # and a fresh install: one of each
    assert _counts(home.agents.read_text()) == {"notice": 1, "doctrine": 1}


def test_every_skip_names_its_reason(home):
    home.host_shape()
    shutil.rmtree(str(home.h / ".codex"))                                        # a platform that is not installed
    p = home.run("--install", "--yes")
    lines = p.stdout.splitlines()
    skipped = [i for i, l in enumerate(lines) if "SKIPPED" in l and "/" in l]
    assert skipped, p.stdout[-1500:]
    for i in skipped:
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        assert ("SKIPPED:" in lines[i]) or nxt.strip().startswith(("reason:", "SKIPPED:")), (lines[i], nxt)


def test_a_look_alike_doctrine_is_neither_replaced_nor_removed(home):
    alike = DB + "\n## Somebody else's block\nnot ours\n" + DE + "\n"
    home.host_shape(agents_text=OWNER_HEAD + OLD_NOTICE + "\n" + alike + OWNER_TAIL)
    assert home.run("--install", "--yes").returncode == 0
    t = home.agents.read_text()
    assert alike in t, "a look-alike block between our markers was taken as ours"
    p = home.run("--uninstall", "--yes")
    assert alike in home.agents.read_text(), p.stdout[-800:]


def test_an_uninstall_after_a_refresh_keeps_every_byte_of_the_owners_text(home):
    """The refresh's undo pre-image is the file WITHOUT our blocks as it stands -- never the bytes before our FIRST
    install (the host's receipt names an EMPTY pre-image for AGENTS.md: restoring it would wipe prose added since)."""
    home.host_shape()
    owner_only = _outside(home.agents.read_text())
    assert home.run("--install", "--yes").returncode == 0
    p = home.run("--uninstall", "--yes")
    assert p.returncode == 0, p.stdout[-1200:]
    t = home.agents.read_text()
    assert _counts(t) == {"notice": 0, "doctrine": 0} and t == owner_only, (t, owner_only)
    assert OWNER_HEAD in t and OWNER_TAIL in t


# ------------------------------------------------------------------------------------------------------------------------
# R3-03 (the Round 3 reflection; a REGRESSION vs the landed bytes, which SKIPPED each shape below with the owner's text
# intact): our markers are WHOLE LINES, a notice pair is ours only with our head line, and a CRLF file survives. Judged by
# an INDEPENDENT, line-anchored definition of a block (never the organ's helpers): a block is a line that IS the BEGIN
# marker through the next line that IS its END marker, the END line's line break included.
def _line_pairs(t, b, e):
    out, pos, start = [], 0, None
    for ln in t.splitlines(keepends=True):
        s = ln.rstrip("\r\n")
        if s == b and start is None:
            start = pos
        elif s == e and start is not None:
            out.append((start, pos + len(ln)))
            start = None
        pos += len(ln)
    return out


def _outside_lines(t):
    o, p = [], 0
    for a, b in sorted(_line_pairs(t, MB, ME) + _line_pairs(t, DB, DE)):
        o.append(t[p:a])
        p = b
    o.append(t[p:])
    return "".join(o)


def _pairs(t):
    return {"notice": len(_line_pairs(t, MB, ME)), "doctrine": len(_line_pairs(t, DB, DE))}


def _rt(p):
    return p.read_bytes().decode("utf-8")


QUOTE_LINE = "To find the brain's block, search this file for `%s` (owner prose).\n" % MB
DOC_MENTION = "The file-tier doctrine starts at the line `%s`, and it is the brain's.\n" % DB
ALIKE_NOTICE = MB + "\n## My own block that only borrows the markers\nowner content inside look-alike markers\n" + ME + "\n"


def _install_twice_then_uninstall(home, files):
    pre = {f: _rt(f) for f in files}
    p = home.run("--install", "--yes")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-800:]
    post = {f: _rt(f) for f in files}
    for f in files:
        assert _outside_lines(post[f]) == _outside_lines(pre[f]), ("a byte outside our blocks changed", f)
    p2 = home.run("--install", "--yes")
    assert p2.returncode == 0 and {f: _rt(f) for f in files} == post, "a second install changed bytes"
    p3 = home.run("--uninstall", "--yes")
    assert p3.returncode == 0, p3.stdout[-1500:]
    for f in files:
        if f.exists():
            assert _outside_lines(_rt(f)) == _outside_lines(pre[f]), ("uninstall did not restore the outside bytes", f)
    return pre, post


def test_r3_03_owner_prose_that_quotes_the_begin_marker_mid_line_is_never_a_block_boundary(home):
    home.host_shape(agents_text=OWNER_HEAD + QUOTE_LINE + "\n" + OLD_NOTICE + "\n" + OLD_DOCTRINE + OWNER_TAIL)
    pre, post = _install_twice_then_uninstall(home, [home.agents])
    a = post[home.agents]
    assert QUOTE_LINE in a, "the owner's line that quotes the marker was cut"
    assert _pairs(a) == {"notice": 1, "doctrine": 1}, _pairs(a)
    assert "An OLDER notice body" not in a and "Old doctrine text." not in a, "the stale blocks were not refreshed"


def test_r3_03_an_owner_block_between_our_notice_markers_with_another_head_is_never_taken(home):
    home.host_shape(agents_text=OWNER_HEAD + ALIKE_NOTICE + "\n" + OLD_NOTICE + "\n" + OLD_DOCTRINE + OWNER_TAIL)
    p = home.run("--install", "--yes")
    assert p.returncode == 0, p.stdout[-1500:]
    a = _rt(home.agents)
    assert ALIKE_NOTICE in a, "the owner's look-alike block was replaced or removed"
    assert "An OLDER notice body" not in a and _pairs(a) == {"notice": 2, "doctrine": 1}, _pairs(a)
    assert home.run("--uninstall", "--yes").returncode == 0
    assert ALIKE_NOTICE in _rt(home.agents), "uninstall removed the owner's look-alike block"


def test_r3_03_an_inline_doctrine_marker_mention_never_makes_a_second_doctrine(home):
    home.host_shape(agents_text=OWNER_HEAD + DOC_MENTION + "\n" + OLD_NOTICE + "\n" + OLD_DOCTRINE + OWNER_TAIL)
    pre, post = _install_twice_then_uninstall(home, [home.agents])
    assert DOC_MENTION in post[home.agents]
    assert _pairs(post[home.agents]) == {"notice": 1, "doctrine": 1}, _pairs(post[home.agents])


def test_r3_03_a_crlf_file_keeps_every_outside_byte_and_gets_no_stray_line_break(home):
    home.host_shape()
    for f in (home.agents, home.codex, home.claude):
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    pre, post = _install_twice_then_uninstall(home, [home.agents, home.codex, home.claude])
    assert _pairs(post[home.agents]) == {"notice": 1, "doctrine": 1}
    assert _pairs(post[home.codex]) == {"notice": 1, "doctrine": 1}
    assert _pairs(post[home.claude]) == {"notice": 1, "doctrine": 0}
    for f in (home.agents, home.claude):
        assert "\r\n\r\n\r\n" not in post[f] and not post[f].startswith("\r\n"), ("a stray CRLF", f)


def test_r3_03_quoted_or_look_alike_markers_alone_are_not_our_block(home):
    """A file whose only 'markers' are owner prose (a quote mid-line, an END quoted alone) or a look-alike pair carries
    NO block of ours: status says so (not installed, not damaged), and install APPENDS one block -- never 'already
    installed' over the quote, never a refusal for a 'half pair' that is prose."""
    home.host_shape()
    own = OWNER_HEAD + QUOTE_LINE + "The end line reads `%s` in the brain's files.\n" % ME + ALIKE_NOTICE + OWNER_TAIL
    home.agents.write_text(own, encoding="utf-8")
    st = home.run("--status", "--json")
    row = [r for r in json.loads(st.stdout)["data"]["targets"] if r["id"] == "agents-md"][0]
    assert row["action"] == "append" and not row["installed"] and not row.get("marker_damaged"), row
    p = home.run("--install", "--yes")
    assert p.returncode == 0, p.stdout[-1500:]
    a = _rt(home.agents)
    assert a.startswith(own), "the owner's text was changed"
    assert _pairs(a) == {"notice": 2, "doctrine": 1}, _pairs(a)        # the look-alike + OUR appended notice, our doctrine
    st2 = home.run("--status", "--json")
    row2 = [r for r in json.loads(st2.stdout)["data"]["targets"] if r["id"] == "agents-md"][0]
    assert row2["installed"] and row2["body_matches_shipped"] is True, row2    # judged on OUR block, not the quote
    p2 = home.run("--uninstall", "--yes")
    assert p2.returncode == 0 and "HALF of a marker pair" not in p2.stdout, p2.stdout[-1500:]
    assert _rt(home.agents) == own, "uninstall did not give the owner back exactly their text"


def test_r3_03_a_half_pair_is_counted_on_whole_lines(home):
    """A quoted END alone is prose (the stale blocks are refreshed, nothing reads DAMAGED); a whole pair plus one stray
    whole-line BEGIN is DAMAGED, named, and the file is left byte-exact (the substring test saw both halves present and
    took it as whole)."""
    home.host_shape(agents_text=OWNER_HEAD + "Our closing line is `%s`.\n" % ME + OLD_NOTICE + "\n" + OLD_DOCTRINE + OWNER_TAIL)
    p = home.run("--install", "--yes")
    assert p.returncode == 0 and "MARKER_DAMAGED" not in p.stdout, p.stdout[-1500:]
    assert "An OLDER notice body" not in _rt(home.agents), "a quoted END made the file read damaged"
    stray = OWNER_HEAD + OLD_NOTICE + "\n" + OLD_DOCTRINE + MB + "\n" + OWNER_TAIL
    home.agents.write_text(stray, encoding="utf-8")
    st = home.run("--status", "--json")
    row = [r for r in json.loads(st.stdout)["data"]["targets"] if r["id"] == "agents-md"][0]
    assert row["action"] == "damaged" and "BEGIN lines 2, END line 1" in row["damaged_reason"], row
    home.run("--install", "--yes")
    assert _rt(home.agents) == stray, "a damaged file was written"


# ------------------------------------------------------------------------------------------------------------------------
# R3-04 (a REGRESSION vs the landed bytes, which SKIPPED this state): when THIS run cannot derive the doctrine, a refresh
# of a file that HOLDS one would delete it -- on the hook-less tier, the whole activation. It is a named REFUSAL, the
# file stays byte-exact, and `--status` says why the doctrine could not be derived (it used to swallow the exception).
def _organs_with_a_broken_preamble(home):
    org = home.root / "organs_broken_preamble"
    shutil.copytree(str(ROOT / "organs"), str(org), ignore=shutil.ignore_patterns("__pycache__"))
    with open(str(org / "voidloom_preamble.py"), "a", encoding="utf-8") as fh:
        fh.write("\n\ndef build_segments(*a, **k):\n    raise RuntimeError('the preamble organ is broken (test double)')\n")
    return org / "voidloom_instructions.py"


def test_r3_04_a_doctrine_this_run_cannot_derive_is_refused_never_deleted(home):
    home.host_shape()
    snap = {f: f.read_bytes() for f in (home.agents, home.codex, home.claude)}
    assert _pairs(_rt(home.agents))["doctrine"] == 1
    org = _organs_with_a_broken_preamble(home)
    def run_broken(*args):
        return subprocess.run([sys.executable, "-B", str(org)] + list(args), capture_output=True, text=True,
                              env=home.env, timeout=300, cwd=str(home.h))
    p = run_broken("--install", "--yes")
    assert _pairs(_rt(home.agents))["doctrine"] == 1, "the doctrine block was DELETED"
    assert {f: f.read_bytes() for f in snap} == snap, "a file was written while the doctrine could not be derived"
    assert p.returncode != 0, p.stdout[-1500:]
    assert "DOCTRINE_NOT_DERIVED" in p.stdout and "would DELETE the doctrine block" in p.stdout, p.stdout[-1500:]
    assert "RuntimeError" in p.stdout, "the refusal does not say what failed"
    st = run_broken("--status", "--json")
    d = json.loads(st.stdout)["data"]
    assert "RuntimeError" in (d.get("doctrine_error") or ""), d.get("doctrine_error")
    row = [r for r in d["targets"] if r["id"] == "agents-md"][0]
    assert row["action"] == "refused" and row["refusal_code"] == "DOCTRINE_NOT_DERIVED", row
    assert "doctrine NOT derived" in run_broken("--status").stdout
    # control: the same home with the package's own organs refreshes in place, one doctrine kept
    assert home.run("--install", "--yes").returncode == 0
    assert _pairs(_rt(home.agents)) == {"notice": 1, "doctrine": 1}


# ------------------------------------------------------------------------------------------------------------------------
# R3-05 (a REGRESSION vs the landed bytes): an owner file holding a byte that is not UTF-8 (a Windows-edited 'Caf\xe9')
# and a stale block made `install --yes` die with UnicodeDecodeError after writing the pre-image copy (the other targets
# unprocessed), and the uninstall sweep decoded with 'replace' -- rewriting the owner's byte as U+FFFD. The safe
# byte-level path: every read/write of an owner file round-trips bytes exactly (surrogateescape).
LATIN1_TOP = b"# Mes notes\nCaf\xe9 cr\xe8me -- written by a Windows editor in cp1252.\n\n"


def _rb(p):
    return p.read_bytes().decode("utf-8", "surrogateescape")


def test_r3_05_a_non_utf8_owner_byte_survives_refresh_and_uninstall_byte_exact(home):
    home.host_shape()
    home.agents.write_bytes(LATIN1_TOP + (OLD_NOTICE + "\n" + OLD_DOCTRINE + OWNER_TAIL).encode("utf-8"))
    pre = home.agents.read_bytes()
    dry = home.run("--install", "--json")                                      # the plan reads the same bytes
    planned = [w for w in json.loads(dry.stdout)["data"]["would_write"] if w.get("path") == str(home.agents)][0]
    p = home.run("--install", "--yes")
    assert "Traceback" not in p.stderr and "UnicodeDecodeError" not in p.stderr, p.stderr[-1500:]
    assert p.returncode == 0, p.stdout[-1500:]
    post = home.agents.read_bytes()
    assert post.startswith(LATIN1_TOP), "the owner's non-UTF-8 bytes were changed"
    assert b"\xef\xbf\xbd" not in post, "an owner byte was rewritten as U+FFFD"
    assert planned["bytes_added"] == len(post) - len(pre), (planned["bytes_added"], len(post) - len(pre))   # plan == write
    assert _pairs(_rb(home.agents)) == {"notice": 1, "doctrine": 1} and b"An OLDER notice body" not in post
    assert _outside_lines(_rb(home.agents)) == _outside_lines(pre.decode("utf-8", "surrogateescape"))
    assert _pairs(_rt(home.codex)) == {"notice": 1, "doctrine": 1}, "the other targets were left unprocessed"
    assert home.run("--install", "--yes").returncode == 0 and home.agents.read_bytes() == post, "not idempotent"
    u = home.run("--uninstall", "--yes")
    assert u.returncode == 0 and "Traceback" not in u.stderr, (u.stdout[-1200:], u.stderr[-800:])
    left = home.agents.read_bytes()
    assert left.startswith(LATIN1_TOP) and b"\xef\xbf\xbd" not in left, left[:120]
    assert _pairs(_rb(home.agents)) == {"notice": 0, "doctrine": 0}
    assert _outside_lines(_rb(home.agents)) == _outside_lines(pre.decode("utf-8", "surrogateescape"))


def test_r3_05_the_sweep_keeps_a_non_utf8_byte_in_a_file_no_receipt_names(home):
    """The uninstall SWEEP (a hand-placed block no receipt names) rewrites the file: every other byte must survive."""
    home.host_shape()
    (home.brain / "voidloom_install_receipt.json").write_text(json.dumps({"schema_version": 2, "touched": [], "undone": []}))
    home.agents.write_bytes(LATIN1_TOP + (OLD_DOCTRINE + OWNER_TAIL).encode("utf-8"))
    u = home.run("--uninstall", "--yes")
    assert "Traceback" not in u.stderr, u.stderr[-1500:]
    left = home.agents.read_bytes()
    assert left == LATIN1_TOP + OWNER_TAIL.encode("utf-8"), left

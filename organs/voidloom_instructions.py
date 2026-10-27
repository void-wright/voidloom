#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_instructions.py — THE TRUST-FREE CHANNEL: the instruction files.

    voidloom instructions install [--dry-run | --yes]
    voidloom instructions status
    voidloom instructions uninstall [--yes]

WHAT THIS INSTALLS, AND WHY IT IS ITS OWN VERB
----------------------------------------------
A hook can be skipped silently (Codex: untrusted -> nothing fires, nothing says so).
The one channel that loads with NO trust on every platform is the instruction file.
This verb writes the SELF-CANCELLING ACTIVATION NOTICE there: "if your context does
not contain a line beginning `=== VOIDLOOM HOOK FIRED`, tell the user to run
`voidloom doctor` (on Codex: /hooks); if it does, say nothing."  The predicate is
self-verifying (the line is in the model's context or it is not), it cancels itself
the moment the hook fires, and it fires every session until resolved.

SCOPE, STATED HONESTLY (Amendment 2): the notice is load-bearing ONLY for headless
Codex (`codex exec`, app-server consumers with no review UI).  The interactive Codex
TUI already shows "Hooks need review" on every launch; Claude Code has no trust gate.
The notice can only prove the hook did NOT fire; firing is proved by the heartbeat
join (`voidloom doctor`) and by G0, never by the notice.  Its failures are
CORRELATED, not independent: a competing AGENTS.md that outranks ours, or the
preamble crossing Codex's spill ceiling so the marker leaves context (the hook
budgets itself below the installed limit for exactly this reason).

THE THREE TARGETS, AND THE THREE READERS
----------------------------------------
  ~/.agents/AGENTS.md    CANONICAL.  Read natively by the AGENTS.md tier (Amp,
                         opencode, Goose, Cursor, Continue, Warp, ...).  Writing it
                         CREATES a global instruction file on a machine that may
                         not have had one -- for tools the user may not know read
                         it.  --dry-run therefore names, in three bands, who reads
                         it: PRESENT on this machine (probed), KNOWN but absent,
                         and "any tool you later install that reads this path".
  $CODEX_HOME/AGENTS.md  Codex's global instructions.  Codex has NO import syntax,
                         so the block is carried INLINE (a marker-guarded copy).
  $CLAUDE_CONFIG_DIR/CLAUDE.md
                         Claude Code's global instructions; supports `@path`
                         imports, so ONE marker-guarded import line points at the
                         canonical file.

Every write goes through the SAME identity-gated writer as `voidloom hooks`
(``voidloom_hooks_status.guarded_write_bytes``): pre-image + (dev, ino, nlink)
captured O_NOFOLLOW, open without O_TRUNC, gate, write, read back through the
independent path.  Every target that is a symlink, has more than one name, or is
not a regular file is REFUSED by name.  Nothing is ever symlinked over a real file.
The undo is the MARKER BLOCK (element-level) when the file changed since install,
the byte pre-image when it did not, and a delete when we created the file.

GPU: none.  NETWORK: none.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import voidloom_hooks_status as _hs  # noqa: E402  (the writer, the receipt, the markers)

VERB = "instructions"
MECHANISM = "instructions"
# Keyed on the ARRIVAL LINE — the one line only the HOOK emits (Council #12,
# 2026-09-15).  Until then the notice keyed on the SUFFIX of the emitter's frozen
# first line (`... (wizard-library brain active) ===`), which is stratum DOCTRINE:
# once the doctrine was installed into ~/.agents/AGENTS.md, the FILE supplied the
# sentinel and the notice went silent on every platform that imports the file —
# a dead witness that looked healthy.  The arrival line carries the platform's
# own session id and a timestamp that did not exist when any file was written,
# so a static file cannot forge it ("unforgeable by a static file", not
# "unforgeable" — any process that reads the session id first could).
# Owner-independent; keyed by PREFIX so the volatile fields never matter.
MARKER_PREFIX = "=== VOIDLOOM HOOK FIRED"
# Kept for one release so a status reader of the old key does not KeyError.
MARKER_SUFFIX = MARKER_PREFIX

NOTICE_BODY = f"""## VOIDLOOM memory brain — activation check
(installed by `voidloom instructions install`; remove with `voidloom instructions uninstall`)

THIS NOTICE APPLIES ONLY TO TOOLS THAT RUN SESSION-START HOOKS (Claude Code, Codex).
If your tool has no hook mechanism (Kimi Code, and any tool that only reads AGENTS.md),
ignore this block entirely: the Voidloom doctrine block below, if present, IS your
activation, and there is no hook to trust.

At the start of every session, check your own context for a line that BEGINS with
`{MARKER_PREFIX}`. (It is emitted by the hook alone; it names the platform, this
session's id, a timestamp and the doctrine version. No file carries it.)

- If that line IS present: say nothing about this notice.
- If that line is ABSENT FROM WHAT YOU CAN SEE: that is all you know. It is evidence the
  hook may not have fired — and it is ALSO what a compacted, truncated or summarised
  context looks like after a long session, where the hook fired perfectly and its line is
  simply no longer in view. So do not tell the user the hook is inactive. Say only what
  you observed, once and briefly, in your FIRST response, quoting the command verbatim:
  "I can't see the Voidloom arrival line in my context. If this session was compacted
  that is expected. Otherwise run `voidloom doctor` — it reads the platform's own session
  records and can tell the difference; on Codex, where an untrusted hook is skipped
  silently, the fix is `/hooks` and trusting the Voidloom SessionStart entry."
  Then continue with the user's request.

Why the hedge: an absent line establishes that YOU cannot see it, never that the hook did
not run. `voidloom doctor` joins the hook's heartbeat against the platform's own session
records — it can answer the question your context cannot.

When the hook fires it injects, at session start, the brain's location, the
`voidloom recall` / `voidloom store` verbs, and the live queue. This notice grants no
permission and asks for no action beyond reporting an absence.
"""


# [D06-L18]: `instructions install` carried this caveat and `instructions status`
# dropped it, so the verb an operator runs to CHECK reported less honestly than
# the one that WRITES. One constant, both verbs.
#
# Its last sentence was also stale after C7b: it said the notice "can only prove
# the hook did NOT fire", which is precisely the overclaim C7b deleted from the
# notice itself. An absent line establishes that the reader cannot SEE it --
# compaction produces the same absence -- and nothing more.
NOTICE_SCOPE = ("load-bearing only for HEADLESS Codex; the Codex TUI already shows "
                "'Hooks need review'; Claude Code has no trust gate. And the notice "
                "proves nothing about whether the hook fired: an absent arrival line "
                "means only that the reader cannot see it, which is also what a "
                "compacted or summarised context looks like. `voidloom doctor` joins "
                "the heartbeat against the platform's own session records and is the "
                "only surface that can tell those apart.")


def _extract_managed_blocks(txt: str) -> "Optional[str]":
    """Our managed blocks, concatenated, exactly as `content` builds them.

    Returns None when no notice marker is present. N4's comparison must see the
    same bytes `target_table` would write and nothing else -- the user's own
    prose around our markers is theirs, and including it would make every file
    read STALE forever.
    """
    out = []
    for begin, end, heads in ((_hs.MARKER_BEGIN, _hs.MARKER_END, _notice_heads()),
                              (_hs.DOCTRINE_MARKER_BEGIN, DOCTRINE_MARKER_END, (DOCTRINE_HEAD,))):
        sp = [x for x in _block_spans(txt, begin, end) if _ours(txt, x, *heads)]     # R3-03: whole lines, ours only
        if not sp:
            continue
        blk = txt[sp[0][0]:sp[0][1]]
        out.append(blk[:blk.rfind(end) + len(end)] + "\n")
    if not out:
        return None
    return ("\n".join(out[:1]) + ("\n" + out[1] if len(out) > 1 else "")) if out else None


def marker_block(body: str) -> str:
    return f"{_hs.MARKER_BEGIN}\n{body.rstrip()}\n{_hs.MARKER_END}\n"


# ===========================================================================
# ROUND 3 G1 (the coordinator's item, executed on the reference host 2026-10-01 22:3x): a STALE file is REFRESHED IN
# PLACE, and uninstall removes BOTH of our block types.
#
#   1. `install --yes` on a stale file answered "stale ... SKIPPED" with no reason, while stale_reason told the owner to
#      uninstall and install again;
#   2. that advice, followed on the canonical ~/.agents/AGENTS.md, removed the old ACTIVATION block, LEFT the old
#      DOCTRINE block (a hand-placed block no receipt names), and `install` then APPENDED a fresh activation + doctrine:
#      TWO doctrine blocks, and status still read "stale".
# Now: install REPLACES our blocks where they stand (element-level: the bytes between and around them are the owner's
# and are not touched), removes any duplicate, and inserts a missing one beside its partner in the order the role
# expects (notice, then doctrine) -- never a second copy; uninstall's receipt-driven undo is followed by a SWEEP that
# removes every remaining block of OUR two types (identified by marker pair AND our own head line, so a look-alike
# block is never taken); every SKIP names its reason.
# ===========================================================================
NOTICE_HEAD = "## VOIDLOOM memory brain — activation check"
DOCTRINE_HEAD = "## VOIDLOOM memory brain — the doctrine (file tier)"


def _block_spans(txt: str, begin: str, end: str) -> List[tuple]:
    """[(start, stop)] of every WHOLE `begin` ... `end` block; stop includes the END line's line break (LF or CRLF). A half
    pair is not a span (the caller has already refused a damaged file). R3-03: a marker is a WHOLE LINE -- owner prose that
    QUOTES a marker mid-line ("search this file for `# >>> VOIDLOOM BEGIN >>>`") is the owner's text, never a boundary
    (substring matching cut such a line mid-sentence and wrote our notice into it)."""
    b_re = re.compile(r"^%s[ \t]*\r?$" % re.escape(begin), re.M)
    e_re = re.compile(r"^%s[ \t]*(?:\r?\n|\Z)" % re.escape(end), re.M)
    spans, i = [], 0
    while True:
        m = b_re.search(txt, i)
        if not m:
            return spans
        n = e_re.search(txt, m.end())
        if not n:
            return spans
        spans.append((m.start(), n.end()))
        i = n.end()


def _marker_line_count(txt: str, marker: str) -> int:
    return len(re.findall(r"^%s[ \t]*\r?$" % re.escape(marker), txt, re.M))


def _marker_state(txt: str) -> Dict[str, Any]:
    """Per block type, counted on WHOLE LINES (R3-03 / R3-21): BEGIN and END line counts, whether the type is DAMAGED (the
    counts differ, or a line is not part of a whole pair -- a stray half beside a whole pair included), and how many whole
    pairs are OURS (our head line inside). A quoted marker is not a line; a look-alike pair is whole but not ours."""
    out: Dict[str, Any] = {}
    for kind, b, e, heads in (("notice", _hs.MARKER_BEGIN, _hs.MARKER_END, _notice_heads()),
                              ("doctrine", _hs.DOCTRINE_MARKER_BEGIN, DOCTRINE_MARKER_END, (DOCTRINE_HEAD,))):
        nb, ne = _marker_line_count(txt, b), _marker_line_count(txt, e)
        spans = _block_spans(txt, b, e)
        out[kind + "_begin"], out[kind + "_end"] = nb, ne
        out[kind + "_damaged"] = nb != ne or nb != len(spans)
        out[kind + "_ours"] = len([sp for sp in spans if _ours(txt, sp, *heads)])
    return out


def _ours(txt: str, span: tuple, *heads: str) -> bool:
    """A block is OURS when it carries our marker pair AND, as its first line inside the pair, our own head line (the
    notice's or the doctrine's heading -- each promises removal by this verb -- or the CLAUDE.md import line we write). A
    look-alike pair -- other text between markers like ours -- is not ours to remove."""
    lines = txt[span[0]:span[1]].split("\n")
    return len(lines) > 1 and lines[1].strip() in heads


def _apply_edits(txt: str, edits: List[tuple]) -> str:
    """edits: (start, stop, replacement); an insertion has start == stop and goes BEFORE a replacement at that start."""
    out, pos = [], 0
    for a, b, rep_ in sorted(edits, key=lambda e: (e[0], 0 if e[0] == e[1] else 1)):
        out.append(txt[pos:a])
        out.append(rep_)
        pos = max(pos, b)
    out.append(txt[pos:])
    return "".join(out)


def refreshed_text(txt: str, content: str) -> str:
    """The file with OUR blocks brought to `content` (the role's shipped blocks), element-level: the first notice block is
    replaced where it stands; the first doctrine block AFTER it is replaced where it stands; any other block of ours is
    removed; a missing block is inserted beside its partner (a doctrine right after the notice, a notice right before
    the doctrine); a role that carries no doctrine (the CLAUDE.md import line) has any doctrine block removed. Bytes
    outside our blocks are never touched. The import-line role's single block is a notice-marker pair too."""
    n_ship = content[:content.find(_hs.MARKER_END) + len(_hs.MARKER_END) + 1] if _hs.MARKER_BEGIN in content else ""
    d_i = content.find(_hs.DOCTRINE_MARKER_BEGIN)
    d_ship = content[d_i:] if d_i >= 0 else ""
    n_sp = [sp for sp in _block_spans(txt, _hs.MARKER_BEGIN, _hs.MARKER_END) if _ours(txt, sp, *_notice_heads())]
    d_sp = [sp for sp in _block_spans(txt, _hs.DOCTRINE_MARKER_BEGIN, DOCTRINE_MARKER_END) if _ours(txt, sp, DOCTRINE_HEAD)]
    edits: List[tuple] = []
    if n_sp:
        edits.append((n_sp[0][0], n_sp[0][1], n_ship))
        edits += [(a, b, "") for a, b in n_sp[1:]]
        after = [sp for sp in d_sp if sp[0] >= n_sp[0][1]]
        if d_ship and after:
            edits.append((after[0][0], after[0][1], d_ship))
            edits += [(a, b, "") for a, b in d_sp if (a, b) != after[0]]
        elif d_ship:
            edits.append((n_sp[0][1], n_sp[0][1], d_ship))
            edits += [(a, b, "") for a, b in d_sp]
        else:
            edits += [(a, b, "") for a, b in d_sp]
    elif d_sp:
        if d_ship:
            edits.append((d_sp[0][0], d_sp[0][0], n_ship))
            edits.append((d_sp[0][0], d_sp[0][1], d_ship))
        else:
            edits.append((d_sp[0][0], d_sp[0][1], n_ship))
        edits += [(a, b, "") for a, b in d_sp[1:]]
    else:
        return txt
    return _apply_edits(txt, edits)


def _notice_heads() -> tuple:
    return (NOTICE_HEAD, "@" + str(Path(agents_dir()) / "AGENTS.md"))


def stripped_of_our_blocks(txt: str) -> str:
    """The file with every block of OUR two types removed -- the notice (or the CLAUDE.md import line) and the doctrine,
    each identified by marker pair AND our head line (the uninstall sweep's text; also the proof that a refresh left every
    byte outside our blocks unchanged)."""
    spans = [sp for sp in _block_spans(txt, _hs.MARKER_BEGIN, _hs.MARKER_END) if _ours(txt, sp, *_notice_heads())]
    spans += [sp for sp in _block_spans(txt, _hs.DOCTRINE_MARKER_BEGIN, DOCTRINE_MARKER_END) if _ours(txt, sp, DOCTRINE_HEAD)]
    return _apply_edits(txt, [(a, b, "") for a, b in spans])


def sweep_our_blocks(home: Optional[str] = None, wet: bool = False) -> List[Dict[str, Any]]:
    """UNINSTALL'S SECOND HALF (round 3 G1): after the receipt-driven undo, every block of OUR two types still in one of our
    three targets is removed, element-level, through the identity-gated writer with a pre-image copy -- the host's
    uninstall left a hand-placed DOCTRINE block no receipt named, and the next install appended a second one beside it.
    A file holding half a marker pair is never touched (named instead); a look-alike block is never ours."""
    rows: List[Dict[str, Any]] = []
    for t in target_table(home):
        path = t["path"]
        ident = _hs.path_identity(path)
        if not ident["exists_without_following"]:
            continue
        row: Dict[str, Any] = {"id": t["id"], "path": path, "kind": "sweep_our_blocks"}
        if _hs.refusal_code(ident):
            rows.append({**row, "result": "SKIPPED", "reason": _hs.refusal_reason(ident)})
            continue
        try:
            pre = _hs.read_bytes_if_regular(path)
        except OSError as e:
            rows.append({**row, "result": "SKIPPED", "reason": "unreadable: %s: %s" % (type(e).__name__, e)})
            continue
        txt = pre.decode("utf-8", "surrogateescape")   # R3-05: an owner byte that is not UTF-8 round-trips EXACTLY
        _ms = _marker_state(txt)                 # R3-03: whole marker LINES; a quoted marker is the owner's prose
        if _ms["notice_damaged"] or _ms["doctrine_damaged"]:
            rows.append({**row, "result": "SKIPPED", "reason": ("this file holds HALF of a marker pair: nothing is "
                                                                "removed; repair the marker line by hand, then re-run")})
            continue
        new = stripped_of_our_blocks(txt)
        if new == txt:
            continue
        row["blocks"] = our_block_counts(txt)
        if not wet:
            rows.append({**row, "result": "WOULD_REMOVE"})
            continue
        sha = hashlib.sha256(pre).hexdigest()
        store = _hs.preimage_store(home)
        pre_copy = str(Path(store) / f"{sha}.bytes")
        try:
            _hs.guarded_mkdir_chain(store)
            if not (Path(pre_copy).exists() and _hs._sha256_file(pre_copy) == sha):
                _hs.guarded_write_bytes(pre_copy, pre, expect={"exists": False} if not Path(pre_copy).exists()
                                        else _hs.planned_identity(_hs.path_identity(pre_copy)),
                                        expect_sha256=None if not Path(pre_copy).exists() else _hs._sha256_file(pre_copy))
            w = _hs.guarded_write_bytes(path, new.encode("utf-8", "surrogateescape"), expect=_hs.planned_identity(ident), expect_sha256=sha)
        except (_hs.PathIsNotWhatWeNamed, OSError) as e:
            rows.append({**row, "result": "ERROR", "reason": "%s: %s" % (type(e).__name__, e), "preimage_copy": pre_copy})
            continue
        ok = w.get("sha256_after") == hashlib.sha256(new.encode("utf-8", "surrogateescape")).hexdigest()
        rows.append({**row, "result": "REMOVED" if ok else "ERROR", "preimage_copy": pre_copy,
                     **({} if ok else {"reason": "the file did not read back as planned"})})
    return rows


def our_block_counts(txt: str) -> Dict[str, int]:
    return {"notice": len([sp for sp in _block_spans(txt, _hs.MARKER_BEGIN, _hs.MARKER_END)
                           if _ours(txt, sp, *_notice_heads())]),
            "doctrine": len([sp for sp in _block_spans(txt, _hs.DOCTRINE_MARKER_BEGIN, DOCTRINE_MARKER_END)
                             if _ours(txt, sp, DOCTRINE_HEAD)])}


# ===========================================================================
# Targets and readers
# ===========================================================================
def agents_dir() -> str:
    return str(Path.home() / ".agents")


# R3-04: WHY this run could not derive the doctrine ('' when it could). doctrine_block() used to swallow the exception,
# so a refresh deleted the file's doctrine with 'refresh' as the action and `--status` could not say why.
_DOCTRINE_ERROR: Optional[str] = None


def doctrine_block() -> str:
    """The file-tier activation, DERIVED from the preamble's own doctrine segments.

    C7a -- A STATE WITH NO WRITER. `hooks status` has a FILE_ONLY state and a
    whole probe for it (`probe_file_tier`), whose `installed` means "the DOCTRINE
    block is present"; `DOCTRINE_MARKER_BEGIN` is defined and detected there.
    NOTHING WROTE IT. Measured: `instructions install` into a clean HOME produced
    three files and ZERO doctrine blocks, while the live ~/.agents/AGENTS.md
    carried one, placed BY HAND -- its own text says so -- and promised
    "Remove with `voidloom instructions uninstall`", a promise about a verb that
    could not see it.

    On the hook-less tier (Kimi Code, and anything that only reads AGENTS.md)
    this block IS THE ENTIRE PRODUCT: no hook fires there, so the file is the
    delivery. Leaving it to a human meant the one tier with no fallback was the
    one tier nothing installed.

    DERIVED, not copied: `build_segments` already tags every segment
    `stratum` in {doctrine, state}, and the doctrine ones are exactly the frozen
    prefix the hook injects. Taking them from there means the file tier and the
    hook tier cannot drift into two doctrines -- which is C3's whole lesson about
    two installers, applied to text instead of code. If the preamble organ is
    unreachable the block is OMITTED rather than guessed: a stale hand-written
    doctrine is worse than none, because the file tier has nothing to correct it.
    """
    global _DOCTRINE_ERROR
    _DOCTRINE_ERROR = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import voidloom_preamble as _vp  # type: ignore
        _brain_home = (os.environ.get("WIZARD_LIBRARY_HOME")
                       or str(Path(__file__).resolve().parent.parent))
        # Resolve the persona the SAME way the hook does -- build_segments takes a
        # literal, it does not resolve, so passing None here would have written a
        # NEUTRAL block over a brain that does declare somebody.
        _who, _blurb, _psrc = _vp.resolve_persona(_brain_home)
        segs, _ = _vp.build_segments(db=str(_hs.default_db()) if hasattr(_hs, "default_db")
                                     else str(Path(__file__).resolve().parent.parent
                                              / "claude_memory.db"),
                                     # THE BRAIN HOME, resolved exactly as the
                                     # hook resolves it.  It used to pass
                                     # Path.home() -- a DIFFERENT root from the
                                     # one the hook uses, so the two tiers this
                                     # docstring promises cannot drift were in
                                     # fact resolving their paths, their CLI
                                     # surface and (now) their persona from two
                                     # different directories.
                                     home=_brain_home,
                                     organs=str(Path(__file__).resolve().parent),
                                     # This writer puts the doctrine into the
                                     # USER'S global instruction file.  It names
                                     # whoever THIS BRAIN DECLARES and nobody
                                     # otherwise -- it used to hard-code the
                                     # author's persona, so every install wrote
                                     # that name into the user's AGENTS.md.
                                     owner=_who, persona_blurb=_blurb,
                                     persona_source=_psrc)
        body = "\n\n".join(sg.text for sg in segs if getattr(sg, "stratum", "") == "doctrine")
    except Exception as e:
        _DOCTRINE_ERROR = "%s: %s" % (type(e).__name__, str(e)[:300])
        return ""
    if not body.strip():
        _DOCTRINE_ERROR = "the preamble organ produced no doctrine segment"
        return ""
    head = ("## VOIDLOOM memory brain — the doctrine (file tier)\n"
            "(the frozen prefix of `voidloom preamble`, DERIVED from it by "
            "`voidloom instructions install` — never hand-pasted, so the file tier and the "
            "hook tier cannot drift into two doctrines. On tools that read this file "
            "natively and run no session-start hook — Kimi Code and the AGENTS.md tier — "
            "THIS BLOCK IS THE ACTIVATION: there is no hook to trust and nothing to run. "
            "On Claude Code and Codex the hook injects the same doctrine plus the live "
            "state; its arrival line is the proof it fired.\n"
            "Remove with `voidloom instructions uninstall`.)")
    return (f"{_hs.DOCTRINE_MARKER_BEGIN}\n{head}\n\n{body.rstrip()}\n"
            f"{DOCTRINE_MARKER_END}\n")


DOCTRINE_MARKER_END = "# <<< VOIDLOOM DOCTRINE END <<<"


def target_table(home: Optional[str] = None) -> List[Dict[str, Any]]:
    """The three targets, each with the content it gets and WHY."""
    canonical = str(Path(agents_dir()) / "AGENTS.md")
    # C7a: the file-tier targets carry the doctrine as well as the notice. The
    # Claude Code target does NOT -- it gets an @import line pointing at the
    # canonical file, so adding the doctrine there would be a second copy of the
    # same text in the same tool's context.
    _doc = doctrine_block()
    codex_home = (os.environ.get("CODEX_HOME") or "").strip() or str(Path.home() / ".codex")
    claude_cfg = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or str(Path.home() / ".claude")
    return [
        {"id": "agents-md", "path": canonical, "role": "canonical",
         "content": marker_block(NOTICE_BODY) + ("\n" + _doc if _doc else ""),
         "carries_doctrine": bool(_doc), "doctrine_error": _DOCTRINE_ERROR,
         "mechanism_note": ("the canonical notice PLUS the file-tier doctrine; read "
                            "natively by the AGENTS.md tier, where the doctrine block "
                            "is the whole activation"),
         "reader_dir": agents_dir()},
        {"id": "codex", "path": str(Path(codex_home) / "AGENTS.md"), "role": "inline copy",
         "content": marker_block(NOTICE_BODY) + ("\n" + _doc if _doc else ""),
         "carries_doctrine": bool(_doc), "doctrine_error": _DOCTRINE_ERROR,
         "mechanism_note": ("Codex's global instructions; Codex has no import syntax, "
                            "so the block is carried inline (marker-guarded)"),
         "reader_dir": codex_home},
        {"id": "claude-code", "path": str(Path(claude_cfg) / "CLAUDE.md"), "role": "import line",
         "content": marker_block(f"@{canonical}"),
         "carries_doctrine": False,
         "mechanism_note": ("Claude Code's global instructions; supports `@path` imports, "
                            "so one marker-guarded import line points at the canonical file"),
         "reader_dir": claude_cfg},
    ]


# Who reads ~/.agents/AGENTS.md.  A survey row is a claim about someone else's
# software on one day; the PRESENT band is PROBED, the KNOWN band is not.
AGENTS_MD_READERS: List[Dict[str, Any]] = [
    {"name": "OpenAI Codex CLI", "binaries": ["codex"], "dirs": ["~/.codex"],
     "reads": "AGENTS.md chain (project); global file is $CODEX_HOME/AGENTS.md"},
    {"name": "Claude Code", "binaries": ["claude"], "dirs": ["~/.claude"],
     "reads": "~/.claude/CLAUDE.md (+ @imports); not ~/.agents/AGENTS.md directly"},
    {"name": "Amp", "binaries": ["amp"], "dirs": ["~/.config/amp"], "reads": "AGENTS.md"},
    {"name": "opencode", "binaries": ["opencode"], "dirs": ["~/.config/opencode"],
     "reads": "AGENTS.md"},
    {"name": "Goose", "binaries": ["goose"], "dirs": ["~/.config/goose"], "reads": "AGENTS.md"},
    {"name": "Cursor (agent)", "binaries": ["cursor-agent", "cursor"], "dirs": ["~/.cursor"],
     "reads": "AGENTS.md"},
    {"name": "Continue", "binaries": ["cn", "continue"], "dirs": ["~/.continue"],
     "reads": "AGENTS.md"},
    {"name": "Warp", "binaries": ["warp-terminal", "warp"], "dirs": ["~/.warp", "~/.config/warp-terminal"],
     "reads": "AGENTS.md"},
    {"name": "Cline", "binaries": ["cline"], "dirs": ["~/.cline"], "reads": "AGENTS.md"},
    {"name": "GitHub Copilot CLI", "binaries": ["copilot"], "dirs": ["~/.copilot"],
     "reads": "AGENTS.md"},
]


def probe_readers() -> Dict[str, Any]:
    """Three bands.  PRESENT is derived by probing PATH and known config dirs
    at the moment of asking; KNOWN is the survey; the third band is the honest
    statement that this file's readership is open-ended."""
    present: List[Dict[str, Any]] = []
    known_absent: List[Dict[str, Any]] = []
    for r in AGENTS_MD_READERS:
        bins = [b for b in r["binaries"] if shutil.which(b)]
        dirs = [d for d in r["dirs"] if Path(d).expanduser().exists()]
        row = {"name": r["name"], "reads": r["reads"],
               "binaries_on_path": bins, "config_dirs_present": dirs}
        (present if (bins or dirs) else known_absent).append(row)
    return {"present_on_this_machine_probed": present,
            "known_but_absent": known_absent,
            "open_ended": ("any tool you later install that reads ~/.agents/AGENTS.md "
                           "will read this notice too; that is what a global instruction "
                           "file is")}


# ===========================================================================
# Plan / execute
# ===========================================================================
def _target_state(t: Dict[str, Any]) -> Dict[str, Any]:
    ident = _hs.path_identity(t["path"])
    st: Dict[str, Any] = {
        "id": t["id"], "path": t["path"], "role": t["role"],
        "mechanism_note": t["mechanism_note"],
        "realpath": ident["resolved_path"], "st_dev": ident["dev"], "st_ino": ident["ino"],
        "st_nlink": ident["link_count"], "exists": ident["exists_without_following"],
        "file_type": ident["file_type"], "is_symlink": ident["is_symlink"],
        "refusal_code": _hs.refusal_code(ident), "planned_identity": _hs.planned_identity(ident),
        "parent_dir": str(Path(t["path"]).parent),
        "parent_exists": os.path.isdir(str(Path(t["path"]).parent)),
    }
    if st["refusal_code"]:
        st["action"] = "refused"
        st["blocked"] = _hs.refusal_reason(ident)
        return st
    if not st["exists"]:
        st["action"] = "create"
        st["preimage_sha256"] = None
        st["carries_marker"] = False
        # C7a: report the state we now WRITE. `hooks status` has had a FILE_ONLY
        # state keyed on the doctrine block since it was written, and this verb
        # -- the one that owns these files -- never said whether the block was
        # there. A state nothing reports is only one step better than a state
        # nothing writes.
        st["carries_doctrine"] = False
        st["expects_doctrine"] = bool(t.get("carries_doctrine"))
        st["postimage_bytes"] = t["content"].encode("utf-8")
        return st
    try:
        pre = _hs.read_bytes_if_regular(t["path"])
    except OSError as e:
        st["action"] = "refused"
        st["blocked"] = f"unreadable: {type(e).__name__}: {e}"
        return st
    st["preimage_sha256"] = hashlib.sha256(pre).hexdigest()
    st["preimage_len"] = len(pre)
    txt = pre.decode("utf-8", "surrogateescape")     # R3-05: never "replace" -- the plan's postimage is written
    # N4b (CRUCIBLE-3 D05-F4) -- A BLOCK IS A PAIR, AND EVERY DETECTOR KEYED ON
    # THE OPENING HALF ALONE.
    #
    # `carries_marker = MARKER_BEGIN in txt`. Change one byte in the BEGIN line --
    # VOIDLOOM -> VOIDL00M -- and the file still holds `# <<< VOIDLOOM END <<<`
    # and a live `@`-directive that reaches the model every session, while:
    #   status     -> carries_marker False, action APPEND
    #   install    -> appends a SECOND block, leaving two in the file
    #   uninstall  -> "status ok", exit 0, residue intact
    #   hooks      -> orphans: [] , from the hunter that exists for exactly this
    # and `render_uninstall`'s own doctrine prints, over that residue: "An undo
    # that silently leaves residue is worse than no undo, so this run REFUSES to
    # report a clean uninstall."
    #
    # THIS NEEDS NO ATTACKER. CLAUDE.md is the one file the product's audience is
    # TOLD to edit; `/init`, a merge conflict, a linter or another agent mangles a
    # marker line. The adversarial framing is the cheap one; the mundane one is
    # worse, because it is silent and permanent.
    #
    # So detection is on the PAIR and on EITHER HALF. An unpaired half is DAMAGED:
    # never `append` (which would double the block), never absent from the orphan
    # scan, and never a clean uninstall.
    #
    # R3-03 (Round 3 reflection): a marker is a WHOLE LINE and a pair is OURS only with our head line. Owner prose that
    # QUOTES a marker mid-line, or an owner block that borrows our marker pair under its own heading, is the owner's:
    # it never makes the file "installed", "damaged" or "already installed". Counted on whole lines, a stray half
    # BESIDE a whole pair is damaged too (R3-21's member of the same class).
    _ms = _marker_state(txt)
    _has_begin = _ms["notice_begin"] > 0
    _has_end = _ms["notice_end"] > 0
    _doc_begin = _ms["doctrine_begin"] > 0
    _doc_end = _ms["doctrine_end"] > 0
    st["marker_begin"] = _has_begin
    st["marker_end"] = _has_end
    st["marker_damaged"] = _ms["notice_damaged"] or _ms["doctrine_damaged"]
    # carries_marker now means "this file carries OUR block, whole or broken" --
    # which is the question every caller was actually asking.
    st["carries_marker"] = _ms["notice_ours"] > 0 or _ms["notice_damaged"]
    st["carries_doctrine"] = _ms["doctrine_ours"] > 0 or _ms["doctrine_damaged"]
    st["expects_doctrine"] = bool(t.get("carries_doctrine"))
    # N4 (Crucible D03-F1) -- "INSTALLED" MEANT A MARKER PAIR, NEVER THE BODY.
    #
    # `status` and `install` both decided by marker PRESENCE, so a block whose
    # text was written months ago -- before the notice was rewritten, before the
    # doctrine was derived -- read CURRENT, and `install` answered
    # already_installed over it. That is the dead-witness class Council #12 had
    # just closed elsewhere, left standing in the verb whose whole job is this
    # file. The seat's sting: the status verb could not detect its own
    # recurrence.
    #
    # The comparison is on the BLOCK, not the file: a user's own text around our
    # markers is theirs and must not make us read STALE.
    st["body_sha256"] = None
    st["shipped_sha256"] = hashlib.sha256(t["content"].encode("utf-8")).hexdigest()
    _cur = _extract_managed_blocks(txt)
    if _cur is not None:
        st["body_sha256"] = hashlib.sha256(_cur.encode("utf-8", "surrogateescape")).hexdigest()
    st["body_matches_shipped"] = (st["body_sha256"] == st["shipped_sha256"]
                                  if st["body_sha256"] else None)
    if st.get("marker_damaged"):
        # A half-marker is not "installed" and is emphatically not "absent". Naming
        # it is the whole repair: `append` over this doubles the block, and a
        # silent `uninstall` over it reports clean while the directive survives.
        _halves = []
        for _k in ("notice", "doctrine"):
            if _ms[_k + "_damaged"]:
                _halves.append("%s: BEGIN line%s %d, END line%s %d" % (
                    _k, "" if _ms[_k + "_begin"] == 1 else "s", _ms[_k + "_begin"],
                    "" if _ms[_k + "_end"] == 1 else "s", _ms[_k + "_end"]))
        st["action"] = "damaged"
        st["damaged_reason"] = (
            "this file carries HALF of a managed block (" + "; ".join(_halves) + "). "
            "One byte in a marker line does it -- an editor, a merge, `/init`, another "
            "agent. The remaining half is still read by the tool every session. This "
            "verb REFUSES to append (that would leave two blocks) and refuses to call "
            "the file clean. Repair the marker line by hand so the pair matches, then "
            "re-run `voidloom instructions status`.")
        return st
    if t["id"] in ("agents-md", "codex") and not t.get("carries_doctrine") and our_block_counts(txt)["doctrine"]:
        # R3-04: this run could not derive the doctrine, so the role's content carries none -- and a refresh would
        # DELETE the doctrine block this file holds (on the landed bytes the same state SKIPPED). Refuse, by name.
        st["action"] = "refused"
        st["refusal_code"] = "DOCTRINE_NOT_DERIVED"
        st["blocked"] = ("this run could not derive the doctrine (organs/voidloom_preamble.py failed: %s), so a refresh "
                         "would DELETE the doctrine block this file holds -- on the hook-less tier, the whole "
                         "activation. Refusing; fix the preamble organ, then re-run."
                         % (t.get("doctrine_error") or "no reason recorded"))
        return st
    if st["carries_marker"] or our_block_counts(txt)["doctrine"]:
        # ROUND 3 G1: the file is judged by what an in-place refresh would WRITE, not by the first block of each kind --
        # two doctrine blocks, a doctrine without its notice, or an old body are all "stale", and install REFRESHES them
        st["block_counts"] = our_block_counts(txt)
        new_txt = refreshed_text(txt, t["content"])
        st["action"] = "already_installed"
        if new_txt != txt:
            st["action"] = "refresh"
            st["stale"] = True
            st["stale_reason"] = (
                "our blocks are present but are NOT exactly the ones this package ships (an old body, a "
                "missing or a duplicated block: notice %d, doctrine %d here). Every session reads text this "
                "package no longer means. `voidloom instructions install --yes` replaces them IN PLACE, "
                "element-level -- one notice and one doctrine where the role expects them; your own text "
                "is not touched." % (st["block_counts"]["notice"], st["block_counts"]["doctrine"]))
            st["postimage_bytes"] = new_txt.encode("utf-8", "surrogateescape")
        # A file carrying the notice but NOT the doctrine is the shape every
        # install produced before C7a, and it is exactly the tier with no hook to
        # fall back on. Name it rather than reporting "already installed" over it.
        if st["expects_doctrine"] and not st["carries_doctrine"]:
            st["note"] = ("the notice is present but the file-tier DOCTRINE block is "
                          "not; on a tool with no session-start hook that block is the "
                          "whole activation. `voidloom instructions install --yes` "
                          "inserts it beside the notice.")
        return st
    sep = "" if (not pre or pre.endswith(b"\n")) else "\n"
    st["action"] = "append"
    st["postimage_bytes"] = pre + sep.encode("utf-8") + t["content"].encode("utf-8")
    return st


def install_plan(cwd: str, home: Optional[str] = None) -> Dict[str, Any]:
    """NOTHING IS WRITTEN HERE."""
    targets = [_target_state(t) for t in target_table(home)]
    rp = _hs.receipt_path(home)
    store = _hs.preimage_store(home)
    would: List[Dict[str, Any]] = []
    blockers: List[str] = []
    for st in targets:
        if st["action"] == "refused":
            blockers.append(f"{st['path']}: {st['refusal_code']} — {st['blocked']}")
            continue
        if st["action"] == "damaged":
            # D05-F4: a half-marker must BLOCK, not merely produce no write. Falling
            # through silently would leave the plan empty and the run reporting
            # success over a file that still feeds a directive to the model every
            # session -- and the next `install` would append a SECOND block beside
            # the orphaned half.
            blockers.append(f"{st['path']}: MARKER_DAMAGED — {st['damaged_reason']}")
            continue
        if st["action"] in ("create", "append", "refresh"):
            if not st["parent_exists"]:
                if st["id"] == "agents-md":
                    # EVERY PLAN ROW CARRIES IDENTITY (R15, iteration 3): the mkdir,
                    # pre-image and receipt rows carried path + kind only (P5).
                    would.append({**_hs._identity_row(st["parent_dir"]), "kind": "mkdir",
                                  "note": ("creates ~/.agents — a NEW global instruction "
                                           "directory on this machine")})
                else:
                    # A SINGLE-PLATFORM USER IS SERVED (R12).  A platform whose
                    # home is absent is NOT INSTALLED on this machine: its target
                    # is SKIPPED -- never a blocker for the canonical file and the
                    # present platform.  EXECUTED (reflector, iter 1, P2): this
                    # branch appended to `blockers`, so a Claude-only or
                    # Codex-only home got NOTHING, not even ~/.agents/AGENTS.md --
                    # the brief's own first user failing on its first command.
                    # This installer still never creates a platform's home.
                    st["action"] = "skipped"
                    st["skip_reason"] = (f"{st['parent_dir']} does not exist: this platform is "
                                         f"not installed on this machine; nothing is written "
                                         f"for it (re-run after installing the platform)")
                    continue
            would.append({"path": st["path"], "kind": st["action"],
                          "realpath": st["realpath"], "st_dev": st["st_dev"],
                          "st_ino": st["st_ino"], "st_nlink": st["st_nlink"],
                          "bytes_added": len(st["postimage_bytes"]) - (st.get("preimage_len") or 0),
                          "content_kind": st["role"]})
            if st["action"] in ("append", "refresh"):
                would.append({**_hs._identity_row(str(Path(store) / f"{st['preimage_sha256']}.bytes")),
                              "kind": "write_preimage_copy"})
    if any(s["action"] in ("create", "append", "refresh") for s in targets):
        would.append({**_hs._identity_row(rp), "kind": "receipt_append"})
    for st in targets:
        st.pop("postimage_bytes", None)
    return {"mechanism": MECHANISM, "targets": targets, "would_write": would,
            "skipped_platforms": [{"id": t["id"], "path": t["path"], "reason": t.get("skip_reason")}
                                  for t in targets if t["action"] == "skipped"],
            "readers_of_agents_md": probe_readers(),
            "receipt_path": rp, "preimage_store": store,
            "blockers": blockers, "blocked": bool(blockers),
            "notice_marker_prefix": MARKER_PREFIX,
            "scope": NOTICE_SCOPE,
            "generated_at": _hs.utc_now()}


def instructions_execute(plan: Dict[str, Any], home: Optional[str] = None) -> Dict[str, Any]:
    """Apply the plan through THE writer.  Per target: pre-image copy ->
    guarded write (identity + content gate) -> read-back through the
    independent path -> receipt (last)."""
    res: Dict[str, Any] = {"applied": [], "result": None}
    if plan.get("blocked"):
        res["result"] = "REFUSED"
        res["reason"] = plan["blockers"]
        return res
    table = {t["id"]: t for t in target_table(home)}
    entries: List[Dict[str, Any]] = []
    store = plan["preimage_store"]
    any_written = False
    # ROUND 3 G1: a REFRESH keeps the provenance of the FIRST install (the undo must return to the state before we ever
    # wrote: a file we CREATED is deleted, an appended one gets its original pre-image back), so the receipt's previous
    # entries are read here (read-only; a missing or unreadable receipt means there is none to keep)
    try:
        _prev = {x.get("path"): x for x in (_hs._receipt_load(plan["receipt_path"]).get("touched") or [])
                 if x.get("mechanism") == MECHANISM and x.get("path")}
    except (OSError, ValueError, _hs.PathIsNotWhatWeNamed):
        _prev = {}
    for st in plan["targets"]:
        t = table[st["id"]]
        row: Dict[str, Any] = {"id": st["id"], "path": st["path"], "kind": st["action"]}
        if st["action"] == "already_installed":
            row["result"] = "ALREADY_INSTALLED"
            res["applied"].append(row)
            continue
        if st["action"] not in ("create", "append", "refresh"):
            row["result"] = "SKIPPED"
            # ROUND 3 G1: every SKIP names its reason -- the host's run printed "stale ... SKIPPED" and nothing else
            row["reason"] = (st.get("skip_reason") or st.get("blocked") or st.get("damaged_reason")
                             or st.get("stale_reason") or "this target's state is %r, which this run does not write"
                             % st.get("action"))
            res["applied"].append(row)
            continue
        if not st["parent_exists"] and st["id"] == "agents-md":
            # THE SAME CLASS AS THE HEARTBEAT'S MKDIR (P2, iteration 3): a directory
            # is created only under a chain that resolves to the named path, and
            # only where the plan saw nothing (must_create_leaf), through the shared
            # writer's directory face; a refusal is REFUSED naming the object.
            try:
                _hs.guarded_mkdir_chain(st["parent_dir"], must_create_leaf=True)
                res["applied"].append({"id": st["id"], "path": st["parent_dir"],
                                       "kind": "mkdir", "result": "CREATED"})
            except _hs.PathIsNotWhatWeNamed as e:
                row["result"] = "REFUSED"
                row["refused_by"] = getattr(e, "refused_by", None)
                row["reason"] = f"mkdir {st['parent_dir']}: {e}"
                res["applied"].append(row)
                continue
            except OSError as e:
                row["result"] = "ERROR"
                row["reason"] = f"mkdir {st['parent_dir']}: {type(e).__name__}: {e}"
                res["applied"].append(row)
                continue
        pre_copy = None
        pre_bytes = b""
        if st["action"] in ("append", "refresh"):
            # IDENTITY BEFORE CONTENT (the same class as the hooks installer).
            _now = _hs.path_identity(st["path"])
            _rc = _hs.refusal_code(_now)
            _drift = _hs.identity_matches(st["planned_identity"], _now)
            if _rc is not None or (_drift["checked"] and _drift["same_object"] is False):
                row["result"] = "REFUSED"
                row["refused_by"] = "pre_write_identity_check"
                row["refusal_code"] = _rc or "PLAN_IDENTITY_DRIFT"
                row["reason"] = _hs.refusal_reason(_now) if _rc else f"{st['path']}: {_drift['reason']}"
                res["applied"].append(row)
                continue
            try:
                pre_bytes = _hs.read_bytes_if_regular(st["path"])
            except OSError as e:
                row["result"] = "REFUSED"
                row["refusal_code"] = "NOT_A_REGULAR_FILE"
                row["reason"] = f"{type(e).__name__}: {e}"
                res["applied"].append(row)
                continue
            if hashlib.sha256(pre_bytes).hexdigest() != st["preimage_sha256"]:
                row["result"] = "REFUSED"
                row["reason"] = "the file changed between plan and execute; re-run the plan"
                res["applied"].append(row)
                continue
            try:
                _hs.guarded_mkdir_chain(store)      # the class of P2: every directory we create, gated
                pre_copy = str(Path(store) / f"{st['preimage_sha256']}.bytes")
                if not (Path(pre_copy).exists() and _hs._sha256_file(pre_copy) == st["preimage_sha256"]):
                    w0 = _hs.guarded_write_bytes(
                        pre_copy, pre_bytes,
                        expect=({"exists": False} if not Path(pre_copy).exists()
                                else _hs.planned_identity(_hs.path_identity(pre_copy))),
                        expect_sha256=(None if not Path(pre_copy).exists()
                                       else _hs._sha256_file(pre_copy)))
                    if w0.get("sha256_after") != st["preimage_sha256"]:
                        raise OSError("pre-image copy did not read back")
            except _hs.PathIsNotWhatWeNamed as e:
                row["result"] = "REFUSED"                       # a gate refusal is REFUSED, never ERROR (the P7 class)
                row["refused_by"] = getattr(e, "refused_by", None)
                row["reason"] = f"pre-image copy: {e}"
                res["applied"].append(row)
                continue
            except OSError as e:
                row["result"] = "ERROR"
                row["reason"] = f"pre-image copy: {e}"
                res["applied"].append(row)
                continue
            if st["action"] == "refresh":
                # recomputed from the bytes JUST read (their sha equals the plan's): in place, element-level
                post = refreshed_text(pre_bytes.decode("utf-8", "surrogateescape"), t["content"]).encode("utf-8", "surrogateescape")
            else:
                sep = "" if (not pre_bytes or pre_bytes.endswith(b"\n")) else "\n"
                post = pre_bytes + sep.encode("utf-8") + t["content"].encode("utf-8")
        else:
            post = t["content"].encode("utf-8")
        try:
            w = _hs.guarded_write_bytes(st["path"], post, expect=st["planned_identity"],
                                        expect_sha256=st.get("preimage_sha256"))
        except _hs.PathIsNotWhatWeNamed as e:
            row["result"] = "REFUSED"
            row["reason"] = str(e)
            row["refused_by"] = getattr(e, "refused_by", None)
            res["applied"].append(row)
            continue
        except OSError as e:
            # C4 SWEEP -- THE SAME MISSING ROLLBACK ARM AS THE HOOKS WRITER.
            #
            # `guarded_write_bytes` truncates to 0 and THEN writes (deliberately
            # not O_TRUNC, so the identity gate speaks first), so an ENOSPC
            # between those two calls leaves the target at 0 bytes. Here the
            # target is the user's CLAUDE.md / AGENTS.md -- their global
            # instructions -- and this arm returned ERROR and moved on. The
            # pre-image copy is already on disk above; restore from it, and if
            # even that fails, SAY SO and name the copy.
            row["result"] = "ERROR"
            row["reason"] = f"{type(e).__name__}: {e}"
            if st.get("preimage_sha256") and pre_bytes is not None:
                try:
                    rw = _hs.guarded_write_bytes(st["path"], pre_bytes,
                                                 expect=None, expect_sha256=None)
                    row["rollback"] = {
                        "sha256_after": rw.get("sha256_after"),
                        "restored_preimage": rw.get("sha256_after") == st["preimage_sha256"]}
                    if row["rollback"]["restored_preimage"]:
                        row["reason"] += " — the pre-image was restored in the same run"
                except (_hs.PathIsNotWhatWeNamed, OSError) as e2:
                    row["rollback"] = {"error": f"{type(e2).__name__}: {e2}",
                                       "preimage_copy": pre_copy}
                    row["reason"] += (f" — AND THE ROLLBACK FAILED: your file is not "
                                      f"intact. Restore it from {pre_copy}")
            else:
                # A failed CREATE leaves a 0-byte file the writer's O_CREAT made.
                try:
                    if os.path.exists(st["path"]) and os.path.getsize(st["path"]) == 0:
                        d_ = _hs.guarded_unlink(
                            st["path"],
                            expect=_hs.planned_identity(_hs.path_identity(st["path"])),
                            expect_sha256=None)
                        row["rollback"] = {
                            "unlinked_empty_file_we_created": d_.get("gone_after")}
                except (_hs.PathIsNotWhatWeNamed, OSError) as e2:
                    row["rollback"] = {"error": f"{type(e2).__name__}: {e2}"}
            res["applied"].append(row)
            continue
        ok = (w.get("same_object") and w.get("read_back_error") is None
              and w.get("sha256_after") == hashlib.sha256(post).hexdigest())
        if not ok:
            # D05-C1: never pin the restore to `post` -- those are the bytes this run
            # has just failed to write, so on a short write the pin fires content-drift
            # and the restore refuses itself on the user's CLAUDE.md / AGENTS.md.
            try:
                _now = _hs._sha256_file(st["path"])
            except OSError:
                _now = None
            _restored = False
            try:
                if st["action"] in ("append", "refresh"):
                    _rw = _hs.guarded_write_bytes(st["path"], pre_bytes,
                                                  expect=st["planned_identity"],
                                                  expect_sha256=_now)
                    _restored = (_rw.get("sha256_after")
                                 == hashlib.sha256(pre_bytes).hexdigest())
                else:
                    _du = _hs.guarded_unlink(st["path"],
                                       expect=_hs.planned_identity(_hs.path_identity(st["path"])),
                                       expect_sha256=_now)
                    _restored = bool(_du.get("gone_after"))
            except (OSError, _hs.PathIsNotWhatWeNamed) as e:
                row["rollback_error"] = str(e)
            row["result"] = "ROLLED_BACK"
            # D05-C2: derived from the observation, never asserted beside its own failure.
            row["reason"] = ("the post-image did not read back as planned; "
                             + ("the pre-image was restored" if _restored else
                                "AND THE ROLLBACK FAILED: your file is not intact"))
            res["applied"].append(row)
            continue
        row["result"] = "WRITTEN"
        row["postimage_sha256"] = w["sha256_after"]
        row["verify_path"] = w["verify_path"]
        res["applied"].append(row)
        any_written = True
        if st["action"] == "refresh":
            # the receipt's ELEMENT is our notice (or import-line) block alone: element-level undo finds exactly it, and
            # uninstall's sweep removes the doctrine (a refresh never leaves the two blocks byte-adjacent as one element)
            _n = t["content"]
            _n = _n[:_n.find(_hs.MARKER_END) + len(_hs.MARKER_END) + 1]
            _old = _prev.get(st["path"])
            # THE UNDO'S PRE-IMAGE IS THE FILE WITHOUT OUR BLOCKS, AS IT STANDS NOW -- never the bytes before our FIRST
            # install: the owner's text added since then would be wiped by restoring those (measured: a refresh that kept
            # the first install's empty pre-image made the next uninstall empty a file holding the owner's prose). A file
            # we CREATED that holds nothing but our blocks keeps its delete-on-undo.
            _bare = stripped_of_our_blocks(post.decode("utf-8", "surrogateescape"))
            if _old and _old.get("install_action") == "create" and not _bare.strip():
                entries.append({**_old, "installed_at": _hs.utc_now(), "postimage_sha256": w["sha256_after"],
                                "element": _n, "element_kind": "marker_block", "refreshed_at": _hs.utc_now(),
                                "planned_identity_at_install": st["planned_identity"]})
                continue
            _bare_b = _bare.encode("utf-8", "surrogateescape")
            _bsha = hashlib.sha256(_bare_b).hexdigest()
            _bcopy = str(Path(store) / f"{_bsha}.bytes")
            try:
                _hs.guarded_mkdir_chain(store)
                if not (Path(_bcopy).exists() and _hs._sha256_file(_bcopy) == _bsha):
                    _hs.guarded_write_bytes(_bcopy, _bare_b, expect={"exists": False} if not Path(_bcopy).exists()
                                            else _hs.planned_identity(_hs.path_identity(_bcopy)),
                                            expect_sha256=None if not Path(_bcopy).exists() else _hs._sha256_file(_bcopy))
            except (_hs.PathIsNotWhatWeNamed, OSError) as e:
                # the refresh is written; without its undo pre-image the receipt names the element only (element-level
                # undo + the sweep still remove exactly our blocks)
                row["undo_note"] = "the undo pre-image could not be written (%s: %s): uninstall works element-level" % (
                    type(e).__name__, e)
                _bcopy, _bsha = None, None
            entries.append({"platform": st["id"], "mechanism": MECHANISM, "installed_at": _hs.utc_now(),
                            "path": st["path"], "action": "restore_preimage" if _bcopy else "remove_marker_block",
                            "install_action": "refresh", "preimage_path": _bcopy, "preimage_sha256": _bsha,
                            "postimage_sha256": w["sha256_after"], "element": _n, "element_kind": "marker_block",
                            "planned_identity_at_install": st["planned_identity"], "created_dirs": [],
                            "first_install": {k: (_old or {}).get(k) for k in ("installed_at", "install_action")}})
            continue
        entries.append({"platform": st["id"], "mechanism": MECHANISM,
                        "installed_at": _hs.utc_now(), "path": st["path"],
                        "action": "restore_preimage" if st["action"] in ("append", "refresh") else "delete_file",
                        "install_action": st["action"],
                        "preimage_path": pre_copy, "preimage_sha256": st.get("preimage_sha256"),
                        "postimage_sha256": w["sha256_after"],
                        "element": t["content"], "element_kind": "marker_block",
                        "planned_identity_at_install": st["planned_identity"],
                        "created_dirs": ([st["parent_dir"]] if (not st["parent_exists"]
                                                                and st["id"] == "agents-md") else [])})
    if entries:
        try:
            rdoc = _hs._receipt_load(plan["receipt_path"])
            paths = {e["path"] for e in entries}
            rdoc["touched"] = [x for x in rdoc["touched"] if x.get("path") not in paths] + entries
            rw = _hs._receipt_write(plan["receipt_path"], rdoc)
            res["applied"].append({"path": plan["receipt_path"], "kind": "receipt_append",
                                   "result": "WRITTEN", "sha256": rw.get("sha256_after")})
        except (OSError, ValueError, _hs.PathIsNotWhatWeNamed) as e:
            res["applied"].append({"path": plan["receipt_path"], "kind": "receipt_append",
                                   "result": "ERROR", "reason": f"{type(e).__name__}: {e}"})
            res["result"] = "INSTALLED_UNRECEIPTED"
            res["reason"] = ("blocks were written but the receipt could not be; --status "
                             "reports them as UNRECEIPTED and uninstall removes them by marker")
            return res
    results = [a.get("result") for a in res["applied"]]
    if any(r in ("ERROR", "ROLLED_BACK", "REFUSED") for r in results):
        res["result"] = "INCOMPLETE"
    elif any_written:
        res["result"] = "INSTALLED"
    else:
        res["result"] = "ALREADY_INSTALLED"
    return res


# ===========================================================================
# Status — the resolver's view of the three targets
# ===========================================================================
def status(cwd: str, home: Optional[str] = None) -> Dict[str, Any]:
    rp = _hs.receipt_path(home)
    receipted: set = set()
    try:
        if Path(rp).exists():
            for t_ in (json.loads(_hs.read_text_if_regular(rp)).get("touched") or []):
                if t_.get("mechanism") == MECHANISM and t_.get("path"):
                    receipted.add(os.path.abspath(str(t_["path"])))
    except (OSError, ValueError):
        pass
    rows: List[Dict[str, Any]] = []
    for t in target_table(home):
        st = _target_state(t)
        st.pop("postimage_bytes", None)
        st["receipted"] = os.path.abspath(st["path"]) in receipted
        st["orphan"] = bool(st.get("carries_marker")) and not st["receipted"]
        st["installed"] = bool(st.get("carries_marker"))
        st["platform_home_present"] = st["parent_exists"]
        rows.append(st)
    return {"mechanism": MECHANISM, "targets": rows,
            "doctrine_error": _DOCTRINE_ERROR,           # R3-04: why this run could not derive the doctrine, or None
            "installed_count": sum(1 for r in rows if r["installed"]),
            "orphan_count": sum(1 for r in rows if r["orphan"]),
            "receipt_path": rp, "receipt_present": Path(rp).exists(),
            "readers_of_agents_md": probe_readers(),
            "notice_marker_prefix": MARKER_PREFIX, "generated_at": _hs.utc_now()}


# ===========================================================================
# Rendering
# ===========================================================================
def render_plan(plan: Dict[str, Any], wet: bool, applied: Optional[Dict[str, Any]] = None) -> str:
    L: List[str] = []
    L.append("Voidloom instructions install  "
             + ("[EXECUTING]" if wet else "[DRY RUN — nothing will be written]"))
    L.append(f"  notice keyed on a context line beginning: `{plan['notice_marker_prefix']}` (hook-only)")
    L.append(f"  scope: {plan['scope']}")
    L.append("")
    L.append("  TARGETS (creation vs append stated per file):")
    for t in plan["targets"]:
        L.append(f"    {t['action']:<18} {t['path']}")
        L.append(f"        {t['role']:<12} {t['mechanism_note']}")
        L.append(f"        realpath={t['realpath']}  (dev {t['st_dev']}, ino {t['st_ino']}, "
                 f"nlink {t['st_nlink']})  exists={t['exists']}  type={t['file_type']}")
        if t.get("blocked"):
            L.append(f"        REFUSED: {t['blocked']}")
        if t.get("skip_reason"):
            L.append(f"        SKIPPED: {t['skip_reason']}")
        if t.get("stale_reason"):
            L.append(f"        STALE: {t['stale_reason']}")
    L.append("")
    L.append("  PATHS A WET RUN WOULD WRITE (listed BEFORE anything is written):")
    for w in plan["would_write"]:
        L.append(f"    {w['kind']:<20} {w['path']}" + (f"   {w['note']}" if w.get("note") else ""))
    rd = plan["readers_of_agents_md"]
    L.append("")
    L.append("  WHO READS ~/.agents/AGENTS.md:")
    L.append("    PRESENT on this machine (probed):")
    for r in rd["present_on_this_machine_probed"] or [{"name": "(none found)", "reads": ""}]:
        L.append(f"      - {r['name']}: {r.get('reads','')}"
                 + (f"  [bins {r['binaries_on_path']}]" if r.get("binaries_on_path") else "")
                 + (f"  [dirs {r['config_dirs_present']}]" if r.get("config_dirs_present") else ""))
    L.append("    KNOWN but absent:")
    for r in rd["known_but_absent"]:
        L.append(f"      - {r['name']}: {r['reads']}")
    L.append(f"    OPEN-ENDED: {rd['open_ended']}")
    L.append("")
    if plan["blocked"]:
        L.append("  RESULT: REFUSED")
        for b in plan["blockers"]:
            L.append(f"    - {b}")
    elif not wet:
        L.append("  RESULT: DRY RUN complete. Re-run with --yes to apply.")
    if applied:
        L.append(f"  APPLIED: {applied.get('result')}"
                 + (f" — {applied.get('reason')}" if applied.get("reason") else ""))
        for a in applied.get("applied") or []:
            L.append(f"    {a.get('kind'):<16} {str(a.get('result')):<22} {a.get('path')}")
            if a.get("reason"):
                L.append(f"        reason: {a['reason']}")
    return "\n".join(L)


def render_status(st: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append(f"Voidloom instructions --status   ({st['generated_at']})")
    L.append(f"  receipt: {st['receipt_path']}" + ("" if st["receipt_present"] else "   [ABSENT]"))
    for t in st["targets"]:
        mark = (("INSTALLED " if t["installed"] else "absent    ") if t["exists"]
                else ("no file   " if t["parent_exists"] else "NO PLATFORM"))
        L.append(f"  {mark} {t['path']}   role={t['role']}  receipted={t['receipted']}"
                 + ("  ORPHAN (marker present, no receipt)" if t["orphan"] else "")
                 + ("  STALE (`install --yes` refreshes it in place)" if t.get("stale") else "")
                 + (f"  REFUSED: {t['refusal_code']}" if t.get("refusal_code") else ""))
    L.append(f"  installed {st['installed_count']} of {len(st['targets'])}; orphans {st['orphan_count']}")
    if st.get("doctrine_error"):
        L.append(f"  doctrine NOT derived this run ({st['doctrine_error']}): install refuses to touch a file that "
                 f"holds one; fix organs/voidloom_preamble.py, then re-run")
    return "\n".join(L)


# ===========================================================================
# CLI — run(argv) -> envelope; main() prints once
# ===========================================================================
_ACTION_WORDS = {"install": "--install", "status": "--status", "uninstall": "--uninstall"}


def envelope(status_s: str, code: str, data: Any) -> Dict[str, Any]:
    return {"status": status_s, "code": code, "data": data, "verb": VERB}


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="voidloom instructions",
                                 description="the trust-free activation notice in the "
                                             "instruction files: install / status / undo")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="plan only (also the DEFAULT)")
    ap.add_argument("--yes", action="store_true", help="actually write")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--home", default=None)
    return ap


def run(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    args_l = list(argv) if argv is not None else sys.argv[1:]
    if args_l and args_l[0] in _ACTION_WORDS:
        args_l[0] = _ACTION_WORDS[args_l[0]]
    ap = build_argparser()
    try:
        args = ap.parse_args(args_l)
    except SystemExit as e:
        env = envelope("error", "usage", {"message": "invalid arguments; see --help", "argv": args_l})
        env[_hs.EXIT_CODE_KEY] = int(getattr(e, "code", 2) or 2)
        env[_hs.HUMAN_KEY] = ap.format_usage()
        return env
    cwd = args.cwd or os.getcwd()
    wet = bool(args.yes) and not args.dry_run
    if args.status:
        st = status(cwd, args.home)
        st["scope"] = NOTICE_SCOPE          # [D06-L18]: status carries it too
        env = envelope("ok", "instructions_status", st)
        env[_hs.EXIT_CODE_KEY] = 0
        env[_hs.HUMAN_KEY] = render_status(st)
        return env
    if args.install:
        plan = install_plan(cwd, args.home)
        if plan["blocked"]:
            env = envelope("refused", "instructions_install_refused", {**plan, "executed": False})
            env[_hs.EXIT_CODE_KEY] = 3
            env[_hs.HUMAN_KEY] = render_plan(plan, wet=False)
            return env
        applied = instructions_execute(plan, args.home) if wet else None
        data = {**plan, "executed": bool(wet), "applied": applied}
        if applied is None:
            env = envelope("ok", "instructions_install_plan", data)
            rc = 0
        elif applied["result"] in ("INSTALLED", "ALREADY_INSTALLED"):
            env = envelope("ok", "instructions_install_" + applied["result"].lower(), data)
            rc = 0
        elif applied["result"] == "REFUSED":
            env = envelope("refused", "instructions_install_refused", data)
            rc = 3
        else:
            env = envelope("incomplete", "instructions_install_" + applied["result"].lower(), data)
            rc = 4
        env[_hs.EXIT_CODE_KEY] = rc
        env[_hs.HUMAN_KEY] = render_plan(plan, wet=wet, applied=applied)
        return env
    # --- uninstall: the SAME plan/execute as hooks, filtered to this mechanism
    # R11 (scoped to THIS mechanism) + R5 (a plan never spawns the vendor).
    # instructions has no --platform: its three targets are one set, and the
    # domain still comes from voidloom_instructions.target_table() (C1).
    plan = _hs.uninstall_plan(cwd, args.home, mechanism=MECHANISM, probe_platform=False)
    if wet and plan["blocked"]:
        env = envelope("refused", "instructions_uninstall_blocked", plan)
        env[_hs.EXIT_CODE_KEY] = 3
        env[_hs.HUMAN_KEY] = _hs.render_uninstall(plan, wet=False)
        return env
    applied = _hs.uninstall_execute(plan) if wet else None
    data = {**plan, "executed": bool(wet), "applied": applied}
    if applied is not None:
        data["receipt_update"] = _hs.receipt_mark_undone(plan["receipt_path"], applied)
    # ROUND 3 G1: BOTH of our block types go -- the receipt names only what an install wrote, so a hand-placed doctrine
    # (or one an older install wrote) survived the undo and the next install doubled it
    sweep = sweep_our_blocks(args.home, wet=wet)
    data["sweep"] = sweep
    sweep_bad = [r for r in sweep if r.get("result") in ("ERROR", "SKIPPED")]
    if applied and (applied["errors"] or applied["skipped"] or sweep_bad):
        env = envelope("incomplete", "instructions_uninstall_incomplete", data)
        env[_hs.EXIT_CODE_KEY] = 4
    else:
        st_s = "ok" if not plan["blocked"] else "refused"
        env = envelope(st_s, "instructions_uninstall_" + ("applied" if wet else "plan"), data)
        env[_hs.EXIT_CODE_KEY] = {"ok": 0, "refused": 3}[st_s]
    _sw = ["", ("  OUR BLOCKS LEFT AFTER THE RECEIPT'S UNDO (notice / doctrine; removed element-level, your text kept):"
                if wet else "  OUR BLOCKS PRESENT NOW (a wet run undoes the receipt first, then removes whatever of ours "
                "is left, element-level; your text kept):")]
    for r in sweep:
        _sw.append("    %-16s %-14s %s%s" % (r["kind"], r["result"], r["path"],
                                            ("  (notice %d, doctrine %d)" % (r["blocks"]["notice"], r["blocks"]["doctrine"]))
                                            if r.get("blocks") else ""))
        if r.get("reason"):
            _sw.append("        reason: %s" % r["reason"])
    if not sweep:
        _sw.append("    none")
    env[_hs.HUMAN_KEY] = _hs.render_uninstall(plan, wet=wet, applied=applied) + "\n".join(_sw)
    return env


def main(argv: Optional[Sequence[str]] = None) -> int:
    args_l = list(argv) if argv is not None else sys.argv[1:]
    return _hs.emit(run(args_l), "--json" in args_l)


if __name__ == "__main__":
    sys.exit(main())

"""voidloom._approvals_faces — the two PRESENTATION faces over the canonical
Face-3 record (voidloom_approvals.structured_record).

Face 3 (headless JSON) is the source of truth and lives in the backend. This
module renders the other two faces, keeping cli.py thin:

  * Face 2 — the AskUserQuestion-mediated "like plan mode" POP-UP. We do NOT call
    AskUserQuestion here (that is main-Claude's tool call at runtime). We emit:
      - a VOIDWRIGHT-styled approval CARD (rendered via void_render: the sigil,
        void-indigo/violet/gold palette, restraint) for the human to read, and
      - the structured AskUserQuestion PAYLOAD (question + options Approve /
        Reject / Snooze) a session drops into the tool.
    See face2_protocol.md for the runtime contract.

  * Face 1 — the interactive TUI (`voidloom approvals --tui`). Uses `textual`
    IF feature-detected, else a graceful ANSI/`input()` fallback. Guards non-TTY
    (refuses, pointing at --json / --present). VOIDWRIGHT-styled.

DEPENDENCIES: void_render (the SSOT styling) is imported defensively from the
brain dir; if it cannot be found we degrade to a plain (zero-escape) renderer so
the faces still function. `textual` is an OPTIONAL feature, never a hard dep.
This module imports nothing heavy (no torch / model).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# R10 — THE ONE COUNT FUNCTION. R10 names FACES explicitly ("Banner, CLI and
# faces all call it and none counts anything itself"), and this module reported
# `pending_total` as len(records) — its own measurement of a list a caller may
# have filtered or truncated. Imported defensively, exactly as voidloom_banner
# does, so a missing ledger degrades rather than breaking a face.
try:
    _here = Path(__file__).resolve().parent.parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    import decision_units as _DU  # type: ignore
except Exception as _e:  # pragma: no cover - defensive
    _DU = None  # type: ignore
    _DU_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
else:
    _DU_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# void_render — the VOIDWRIGHT styling SSOT. Defensive import + a plain fallback
# so a missing renderer never breaks a face.
# ---------------------------------------------------------------------------

def _import_void_render():
    """Import void_render from the brain dir. Returns the module or None."""
    # R5/C6 (class sweep). Same class, fifth shipped member -- and here the
    # literal decides which directory gets pushed onto sys.path, so the wrong
    # value imports a DIFFERENT machine's void_render. This file lives in
    # voidloom/, one level below the brain dir.
    db = (os.environ.get("WIZARD_LIBRARY_DB")
          or os.environ.get("CLAUDE_MEMORY_DB")
          or str(Path(__file__).resolve().parent.parent / "claude_memory.db"))
    _root = Path(db).resolve().parent
    _organs = _root / "organs"
    brain_dir = str(_organs if _organs.is_dir() else _root)
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import void_render  # type: ignore
        return void_render
    except Exception:
        return None


_VR = _import_void_render()


def _renderer(stream=None, force_mode: Optional[str] = None):
    """A void_render.Renderer, or a tiny plain stand-in when void_render is
    absent. The stand-in honors the same method surface used by the cards so the
    faces are renderer-agnostic."""
    if _VR is not None:
        return _VR.Renderer(stream=stream, force_mode=force_mode)
    return _PlainRenderer()


class _PlainRenderer:
    """Zero-escape stand-in matching the subset of Renderer the cards use. Only
    used if void_render cannot be imported — keeps the faces functional."""

    mode = "none"

    def paint(self, text, color=None, *, bold=False, dim=False):  # noqa: D401
        return text

    def sigil(self):
        return "<>"

    def wordmark(self, spaced=True):
        return "V O I D L O O M" if spaced else "Voidloom"

    def tagline(self):
        return "a living memory tapestry woven for AI agents"

    def accent(self, text, *, bold=False):
        return text

    def violet(self, text, *, bold=False):
        return text

    def dim(self, text):
        return text

    def star(self, glyph="·"):
        return glyph

    def lockup(self):
        return "Voidloom"


def _sanitize(text: object) -> str:
    """Neutralize terminal-control sequences in memory-derived text before it
    enters a card (void_render.sanitize_for_terminal is the boundary; #6099).
    Falls back to str() when void_render is absent."""
    if _VR is not None and hasattr(_VR, "sanitize_for_terminal"):
        return _VR.sanitize_for_terminal(text)
    return "" if text is None else str(text)


# ---------------------------------------------------------------------------
# Face 2 — the VOIDWRIGHT approval card + the AskUserQuestion payload.
# ---------------------------------------------------------------------------

# The fixed option set the council ruled (Approve / Reject / Snooze) + a free-text
# comment captured alongside. These labels are the contract the protocol doc and
# the runtime AskUserQuestion call share.
FACE2_OPTIONS = [
    {"label": "Approve",
     "description": "Enact this item (confirm-gated; a comment becomes a "
                    "high-trust training signal)."},
    {"label": "Reject",
     "description": "Decline; records the decision (never enacts). A comment "
                    "explains why and trains the brain."},
    {"label": "Snooze",
     "description": "Suppress the ping for a while — calm, not nagging. Nothing "
                    "is enacted."},
]


def _unread_is_total(unread, breakdown=None) -> bool:
    """True only when EVERY source went dark -- the one state that earns None.

    R7/C2. The distinction this helper exists for is the whole of criterion C2:
    `unread == ('governance',)` on a brain whose self-eval answered is a PARTIAL
    ANSWER, and returning `None` for it discards a number that was measured
    correctly. `unread == every key` is the only total loss.
    """
    # R7/C5. No literal key set lives here: with no breakdown in hand the
    # answer is FALSE and the partial is published, which is [D4]'s direction.
    _un = set(str(x) for x in (unread or ()))
    _keys = set(breakdown or ())
    return bool(_keys) and _un >= _keys


def _pending_total(records: List[Dict[str, Any]],
                   db: Optional[str] = None) -> Optional[int]:
    """How many item(s) await Drew — from decision_units.unified_open_count.

    R10. `len(records)` is a count of the argument, not of the pending set: a
    caller that paginated or filtered would make this face disagree with the
    banner and the CLI, and nothing would say so. Degrades to len(records) ONLY
    when the ledger module cannot be imported at all, and says so on stderr.

    F7 -- THE DB IS EXPLICIT. Iteration 1 swept "every _DU.* call site passes an
    explicit db" and cleared the class; iteration 2 added this call site without
    one, so it resolved WIZARD_LIBRARY_DB / CLAUDE_MEMORY_DB / the default live
    path and reported a number about a database nobody handed this face.
    Measured 2026-09-02: pending_total 3 with the env on a throwaway and 1 with
    it on the live brain, for the SAME record list.
    """
    if _DU is not None:
        try:
            _cr = _DU.unified_open_count(db)
            # R5/C5 (RENDER layer). `int(...total)` read the value off a
            # trust-carrying object and dropped the bit -- no absence branch, so
            # invisible to an AST sweep. `None` is the face's UNKNOWN; the
            # payload names it so a renderer cannot mistake it for zero.
            # R7/C2. `None` for a total that WAS partly read discards a
            # correct number and, worse, blamed a source it had never
            # consulted: the hardcoded "self-eval" below fired on a brain
            # where self-eval was the source that answered.
            _un = tuple(str(x) for x in (getattr(_cr, "unread", ()) or ()))
            if _un and not _unread_is_total(_un, getattr(_cr, "breakdown", None)):
                print("[_approvals_faces] pending_total is a LOWER BOUND: "
                      + ", ".join(_un) + " UNREAD; returning the "
                      + str(int(_cr.total)) + " that WAS read",
                      file=sys.stderr)
                return int(_cr.total)
            if not bool(getattr(_cr, "trusted", None) is True):
                print("[_approvals_faces] pending_total is UNKNOWN: the "
                      "decision ledger could not confirm it read "
                      + (", ".join(_un) if _un else "the pending sources"),
                      file=sys.stderr)
                return None
            return int(_cr.total)
        except Exception as e:  # pragma: no cover - defensive
            print(f"[_approvals_faces] pending_total degraded to len(records): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            return len(records)
    # R5/C5. THE ABSENT-LEDGER BRANCH -- and it is TODAY'S LIVE CONFIG, so it
    # is the branch that actually runs. `len(records)` is a count of the
    # ARGUMENT, which this function's own docstring already names as the wrong
    # subject; with an empty list it publishes 0 as a measurement of the brain.
    # The historical contract has ONE spelling (`voidloom_banner`), the same one
    # `voidloom_approvals.unified_count_ex` reaches for, and it carries a
    # refusal. If that is unreachable too, the total is UNKNOWN -- None.
    try:
        try:
            from .. import voidloom_banner as _vb  # type: ignore
        except (ImportError, ValueError):
            import voidloom_banner as _vb  # type: ignore
        _rep = _vb.unified_pending_report(db)
        # R7/C2. The historical contract already carries `unreadable_sources`;
        # this branch -- the _DU-absent one, which is TODAY'S INSTALLED
        # CONFIGURATION -- threw it away and refused wholesale.
        _un2 = tuple(str(x) for x in (_rep.get("unreadable_sources") or ()))
        if _un2 and not _unread_is_total(_un2, _rep.get("breakdown")):
            print(f"[_approvals_faces] pending_total is a LOWER BOUND: "
                  f"{', '.join(_un2)} UNREAD; returning the "
                  f"{int(_rep.get('total') or 0)} that WAS read",
                  file=sys.stderr)
            return int(_rep["total"])
        if not bool(_rep.get("trusted")):
            print(f"[_approvals_faces] pending_total is UNKNOWN: "
                  f"decision_units unavailable ({_DU_IMPORT_ERROR}) and the "
                  f"historical contract reports its own answer untrusted "
                  f"({_rep.get('reason')})", file=sys.stderr)
            return None
        print(f"[_approvals_faces] pending_total: decision_units unavailable "
              f"({_DU_IMPORT_ERROR}); answered from the historical contract "
              f"via voidloom_banner -> {_rep.get('total')}", file=sys.stderr)
        return int(_rep["total"])
    except Exception as e:  # pragma: no cover - defensive
        print(f"[_approvals_faces] pending_total is UNKNOWN: decision_units "
              f"unavailable ({_DU_IMPORT_ERROR}) and the historical contract "
              f"raised {type(e).__name__}: {e}", file=sys.stderr)
        return None


def build_askuserquestion_payload(records: List[Dict[str, Any]], *,
                                  max_items: int = 1,
                                  db: Optional[str] = None) -> Dict[str, Any]:
    """Build the structured payload a SESSION drops into the AskUserQuestion
    tool for the next pending item(s).

    The payload mirrors the AskUserQuestion question schema: a `question`, a
    short `header`, `multiSelect=False`, and the fixed Approve/Reject/Snooze
    `options`. The card (for the human) is built separately by
    render_card(). Returns a dict that is also fully JSON-serializable so it can
    ride the --present --json envelope. Comments are captured as the tool's
    free-text "Other" path (documented in face2_protocol.md).
    """
    items = records[:max(1, max_items)] if records else []
    questions = []
    for rec in items:
        ref = _sanitize(rec.get("ref", ""))
        title = _sanitize(rec.get("title", ""))
        summary = _sanitize(rec.get("summary", ""))
        src = _sanitize(rec.get("source", ""))
        q = (f"Voidloom approval — {title} [{ref}]. {summary} "
             "Approve, Reject, or Snooze? (Add a comment to train the brain — "
             "your note is a high-trust signal.)")
        questions.append({
            "question": q,
            "header": f"{src} approval"[:60],
            "multiSelect": False,
            "options": [dict(o) for o in FACE2_OPTIONS],
            # Carried so the session can call the enact with the right ref.
            "_ref": ref,
            "_comment_field": "free-text; pass as --comment on the enact",
        })
    return {
        "tool": "AskUserQuestion",
        "questions": questions,
        # R10: the pending total is THE ONE COUNT FUNCTION's answer, not this
        # module's own tally of the list it was handed. The degraded branch is
        # RECORDED in the payload, so a face can never present a number without
        # saying where it came from.
        # R5/C5. `None` here means UNKNOWN. The companion key is spelled so
        # a face cannot render the absence as a zero by accident.
        "pending_total": _pending_total(records, db),
        "pending_total_unknown": _pending_total(records, db) is None,
        "note": ("The actual AskUserQuestion call is main-Claude's at runtime. "
                 "See face2_protocol.md."),
    }


def render_card(rec: Dict[str, Any], *, force_mode: Optional[str] = None,
                width: int = 64) -> str:
    """Render ONE VOIDWRIGHT approval card for a Face-3 record.

    The card is the "pop-up" surface a session shows the human alongside the
    AskUserQuestion prompt. RESTRAINT (void_render's brand law): the sigil once,
    the void-indigo/violet/gold palette, a calm frame — never a wall of glyphs.
    All memory-derived strings are sanitized (#6099). In 'none' mode the card
    carries ZERO escapes (the seed/public/non-TTY path). NEVER raises."""
    r = _renderer(force_mode=force_mode)
    title = _sanitize(rec.get("title", ""))
    ref = _sanitize(rec.get("ref", ""))
    source = _sanitize(rec.get("source", ""))
    summary = _sanitize(rec.get("summary", ""))
    preview = _sanitize(rec.get("preview", ""))
    created = _sanitize(rec.get("created_at", "") or "")
    count = rec.get("count", 0)
    elevated = bool(rec.get("elevated"))
    trust = rec.get("trust_tier") or {}

    bar = r.chrome("─" * width)
    star = r.accent("✦", bold=True) if elevated else r.star("·")
    lines: List[str] = []
    # Header lockup mark — the sigil + wordmark, the one arcane touch.
    lines.append(f"{r.sigil()}  {r.wordmark(spaced=False)}  {r.dim('approval')}")
    lines.append(bar)
    # Title row + the surface + the elevated star.
    lines.append(f"{star} {r.accent(title, bold=True)}")
    lines.append(f"  {r.dim('ref')}    {r.violet(ref)}   "
                 f"{r.dim('source')} {r.accent(source)}")
    # Trust tier — the L5-I-alpha provenance line.
    tline = _trust_line(r, trust)
    if tline:
        lines.append(f"  {r.dim('trust')}  {tline}")
    if created:
        lines.append(f"  {r.dim('seen')}   {r.dim(created)}")
    lines.append(bar)
    # Summary + a short preview/diff (calm, indented).
    if summary:
        lines.append(f"  {summary}")
    for pl in [p for p in preview.split("\n") if p.strip()][:8]:
        lines.append(r.dim(pl))
    lines.append(bar)
    # The action legend — Approve / Reject / Snooze + the comment invitation.
    lines.append("  " + r.violet("Approve") + r.chrome(" · ")
                 + r.violet("Reject") + r.chrome(" · ")
                 + r.violet("Snooze")
                 + r.dim("   (+ a comment trains the brain)"))
    return "\n".join(lines)


def _trust_line(r, trust: Dict[str, Any]) -> str:
    """One calm line describing the trust tier of the underlying memory."""
    if not isinstance(trust, dict):
        return ""
    score = trust.get("score")
    mem_id = trust.get("memory_id")
    if score is not None and mem_id is not None:
        return (r.accent(f"{score:.3f}") + r.dim(f"  (memory #{mem_id}, ")
                + r.dim(trust.get("source", "compute_trust")) + r.dim(")"))
    label = trust.get("label")
    return r.dim(label) if label else ""


def render_present(records: List[Dict[str, Any]], *,
                   force_mode: Optional[str] = None) -> str:
    """The full Face-2 `--present` human view: the card for the next item, then a
    quiet tail telling the human the AskUserQuestion options. When nothing is
    pending, a calm empty message."""
    r = _renderer(force_mode=force_mode)
    if not records:
        return (f"{r.sigil()}  {r.dim('the void is quiet — nothing awaits review.')}")
    head = records[0]
    card = render_card(head, force_mode=force_mode)
    remaining = len(records) - 1
    tail = []
    if remaining > 0:
        tail.append(r.dim(f"  …and {remaining} more awaiting — "
                          "approve one-by-one."))
    tail.append(r.dim("  The session will ask: Approve / Reject / Snooze."))
    return card + "\n" + "\n".join(tail)


# ---------------------------------------------------------------------------
# Face 1 — the TUI. textual if feature-detected, else ANSI/input() fallback.
# ---------------------------------------------------------------------------


def textual_available() -> bool:
    """Feature-detect `textual` WITHOUT importing it heavily (importlib.util
    find_spec only). Never raises."""
    try:
        import importlib.util
        return importlib.util.find_spec("textual") is not None
    except Exception:
        return False


def is_tty() -> bool:
    """True only when BOTH stdin and stdout are interactive terminals — the TUI
    needs both to read keys and paint."""
    try:
        return bool(sys.stdin and sys.stdin.isatty()
                    and sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def run_tui(*, list_pending: Callable[[], List[Dict[str, Any]]],
            structured: Callable[[], List[Dict[str, Any]]],
            enact: Callable[..., Dict[str, Any]],
            snooze: Callable[[float], Dict[str, Any]],
            force_mode: Optional[str] = None,
            _input: Callable[[str], str] = input,
            _force_backend: Optional[str] = None) -> Dict[str, Any]:
    """Run Face 1 (the TUI). Returns a result dict (status/code) — it does not
    print an envelope (cli.py wraps the return).

    Guards: refuses on a non-TTY (returns a refusal dict pointing at
    --json / --present). Picks the textual backend if available + not overridden;
    else the ANSI/input() fallback. The callables are injected so the TUI never
    imports the backend directly (keeps this module light + testable).

    `_force_backend` ('ansi'|'textual') and `_input` are test seams."""
    if not is_tty():
        return {
            "status": "refused",
            "code": "no_tty",
            "message": ("the approvals TUI needs an interactive terminal. "
                        "Run `voidloom approvals --present` for the pop-up "
                        "card, or `voidloom approvals --json` for the headless "
                        "record."),
        }

    # COUNCIL #12 (2026-09-15): ANSI is the DEFAULT face.  The Textual face
    # never received the lifecycle fixes the ANSI face got (commit 2e4aba9):
    # it exits after one action, has no confirm step, never calls _verdict,
    # and returns `status: ok` over failed/blocked work — the exact class this
    # module was repaired for.  Until it is ported and tested it is OPT-IN
    # (`--face textual`), and a `pip install textual` can no longer flip the
    # default by import side-effect.  The parity gap is declared on entry.
    backend = _force_backend or "ansi"
    if backend == "textual":
        if not textual_available():
            backend = "ansi"
        else:
            print("[approvals] Textual face: NOT AT PARITY with the ANSI face "
                  "(single action, no confirm step, no derived verdict). "
                  "Prefer the default ANSI face for real decisions.", file=sys.stderr)
            try:
                return _run_textual(list_pending=list_pending, structured=structured,
                                    enact=enact, snooze=snooze, force_mode=force_mode)
            except Exception:
                # textual mis-init must never strand Drew — fall back to ANSI.
                backend = "ansi"
    return _run_ansi(structured=structured, enact=enact, snooze=snooze,
                     force_mode=force_mode, _input=_input)


def _verdict(res: Any) -> tuple:
    """(ok, detail) derived from the WORK the backend reports, not its label.

    Measured 2026-09-08: `approve` on a belief batch whose edges already exist
    returns status="ok", action="approved_and_enacted", `enacted: []` and
    `failed: [3 x UNIQUE constraint failed]`. A face that reads only `status`
    prints "approved" over a total failure -- the exact class this whole build
    exists to end, one layer above where we have been hunting it. The self-eval
    surface has the same shape with `blocked` (a protected-organ deprecation
    returns ok with enacted: [] and blocked: [1]).

    So the verdict counts outcomes, and it counts them from the SAME OBJECT it
    reports -- the result payload -- rather than from the envelope that wraps it.
    Both layouts are read: belief/governance put the lists at the top level,
    self-eval nests them under `enact`.
    """
    if not isinstance(res, dict):
        return False, "the backend returned no result"
    if res.get("status") not in (None, "ok"):
        return False, str(res.get("message") or res.get("code") or "refused")
    inner = res.get("enact") if isinstance(res.get("enact"), dict) else {}
    def _n(key):
        for src in (res, inner):
            v = src.get(key)
            if isinstance(v, list):
                return len(v)
        return 0
    done, failed, blocked, skipped = (_n("enacted"), _n("failed"),
                                      _n("blocked"), _n("skipped"))
    if failed:
        return False, (f"{failed} failed, {done} applied"
                       + (f", {skipped} skipped" if skipped else ""))
    if blocked:
        return False, (f"{blocked} blocked by a guard, {done} applied "
                       f"(the item was decided; the work was refused)")
    if done or skipped:
        return True, (f"{done} applied" + (f", {skipped} skipped" if skipped else ""))
    # A recorded decision that enacts nothing by design (reject) is fine.
    return True, ""


def _run_ansi(*, structured: Callable[[], List[Dict[str, Any]]],
              enact: Callable[..., Dict[str, Any]],
              snooze: Callable[[float], Dict[str, Any]],
              force_mode: Optional[str],
              _input: Callable[[str], str]) -> Dict[str, Any]:
    """The graceful ANSI/input() fallback TUI. A calm, line-oriented loop: list
    the pending cards, let Drew pick one + an action + a comment. Confirm-gated
    by construction (an explicit action keystroke). NEVER raises."""
    r = _renderer(force_mode=force_mode)
    actions: List[Dict[str, Any]] = []

    def _flash(msg: str) -> None:
        """One line, immediately after a state change. The sitting continues."""
        print(f"  {r.accent('✦')} {r.dim(msg)}")
        print()

    def _sitting(code: str) -> Dict[str, Any]:
        """Close the sitting and SAY WHAT HAPPENED. `q` must never be a silent
        exit -- an operator who decided four things wants to see four."""
        def _failed(a):
            # Same correction as the per-action flash: an enact that returned a
            # SUCCESS envelope over `enacted: [], failed: [3]` is a failed
            # decision, and a sitting that counts it as decided reports a
            # number Drew would act on that is not true.
            return not _verdict(a.get("result"))[0]
        attempted = [a for a in actions if a.get("action") in ("approve", "reject")]
        failed = [a for a in attempted if _failed(a)]
        decided = [a for a in attempted if not _failed(a)]
        snoozed = [a for a in actions if a.get("action") == "snooze" and not _failed(a)]
        if attempted or snoozed:
            bits = []
            if decided:
                bits.append(f"{len(decided)} decided")
            if snoozed:
                bits.append(f"{len(snoozed)} snoozed")
            if failed:
                bits.append(f"{len(failed)} FAILED")
            print(f"{r.sigil()}  {r.dim(' · '.join(bits))}")
        # A sitting in which an enact FAILED is not an ok sitting. The TUI used to
        # return code `tui_enacted` and status ok over a backend error -- Drew saw
        # "[ok] ... (tui_enacted)" wrapped around
        # "self-eval approve requires >=1 --title", approved the same report
        # twice, and it stayed in the queue both times.
        # COMPACT THE ENVELOPE. `--tui` refuses on a non-TTY, so its reader is
        # always a human, and dumping every enacted edge printed ~150 lines of
        # JSON under a one-line summary after Drew approved 39 belief edges. The
        # per-item detail is not lost -- it is in `approval_audit_log`, which is
        # the durable record and the one an auditor should be reading anyway --
        # so the envelope carries the reference to it rather than a copy of it.
        def _slim(a):
            res = a.get("result") if isinstance(a.get("result"), dict) else {}
            inner = res.get("enact") if isinstance(res.get("enact"), dict) else {}
            out = {"ref": a.get("ref"), "action": a.get("action"),
                   "ok": _verdict(res)[0], "detail": _verdict(res)[1]}
            for k in ("audit_id", "report_id"):
                if res.get(k) is not None:
                    out[k] = res.get(k)
            for k in ("enacted", "failed", "blocked", "skipped"):
                for src in (res, inner):
                    if isinstance(src.get(k), list):
                        if src.get(k):
                            out[f"n_{k}"] = len(src[k])
                        break
            return out
        return {"status": "error" if failed else "ok",
                "code": "tui_partial_failure" if failed else code,
                "actions": [_slim(a) for a in actions], "decided": len(decided),
                "failed": len(failed), "backend": "ansi"}

    try:
        # THE LOOP. This docstring has said "a calm, line-oriented loop" since the
        # module was written, and there was no loop: every branch returned after a
        # SINGLE action, so approving one item exited the TUI while others still
        # waited. Deciding four things meant launching the TUI four times.
        # `structured()` is re-read every pass, so the list always reflects what
        # was just decided rather than a snapshot taken at entry.
        first_pass = True
        while True:
            records = structured()
            # THE SHELL ALWAYS RENDERS, EMPTY OR NOT, AND ONLY 'q' LEAVES IT.
            # Drew's ruling 2026-09-08: booting `--tui` on an empty queue printed
            # one line and exited, and draining the last item exited too. Both
            # are now ordinary states of the home screen.
            #
            # The reason is forward-looking and is the design already in the
            # plan: this is a SECTIONED inbox (Decisions · Runs · Commitments ·
            # History) and Decisions is merely the first section to exist. A
            # shell that returns when ONE section is empty can never grow the
            # others -- the day Runs has rows and Decisions does not, the whole
            # app would still exit on an empty Decisions list. Emptiness is a
            # state to RENDER, never a reason to leave.
            print(f"{r.sigil()}  {r.wordmark(spaced=False)}  {r.dim('approvals (TUI)')}")
            print(r.chrome("─" * 64))
            if not records:
                print("   " + r.dim("the void is quiet — nothing awaits review."
                                    if first_pass else
                                    "the queue is empty — everything here has "
                                    "been decided."))
                print(r.chrome("─" * 64))
                print(r.dim("Nothing to pick. Press 'q' to quit."))
                _sel = _input(r.accent("voidloom> ")).strip().lower()
                if _sel in ("q", "quit", "exit"):
                    return _sitting("tui_empty" if first_pass else "tui_drained")
                _flash("nothing here to decide — 'q' quits.")
                continue
            first_pass = False
            for i, rec in enumerate(records, 1):
                star = (r.accent("✦", bold=True) if rec.get("elevated")
                        else r.star("·"))
                print(f"{i:>2}. {star} {r.accent(_sanitize(rec.get('title','')), bold=True)}"
                      f"   {r.dim(_sanitize(rec.get('ref','')))}")
                if rec.get("summary"):
                    print(f"     {r.dim(_sanitize(rec.get('summary')))}")
            print(r.chrome("─" * 64))
            print(r.dim("Pick a number to review, or 'q' to quit."))
            sel = _input(r.accent("voidloom> ")).strip().lower()
            # `q` is the ONLY exit. An empty line used to quit too, which meant a
            # stray Enter ended the sitting silently.
            if sel in ("q", "quit", "exit"):
                return _sitting("tui_quit")
            if not sel.isdigit() or not (1 <= int(sel) <= len(records)):
                # Re-prompt rather than exit: a typo is not a decision.
                _flash("not a listed number — pick again, or 'q' to quit.")
                continue
            rec = records[int(sel) - 1]
            print(render_card(rec, force_mode=force_mode))
            act = _input("  [a]pprove / [r]eject / [s]nooze / [c]ancel: ").strip().lower()
            if act.startswith("s"):
                hrs = _input("  snooze hours [24]: ").strip() or "24"
                try:
                    res = snooze(float(hrs))
                except Exception as e:
                    res = {"status": "error", "message": str(e)}
                actions.append({"ref": rec.get("ref"), "action": "snooze",
                                "result": res})
                _flash(f"snoozed for {hrs}h — {_sanitize(rec.get('ref'))}")
                continue
            if not act.startswith(("a", "r")):
                _flash("cancelled — nothing changed.")
                continue
            action = "approve" if act.startswith("a") else "reject"

            # WHICH proposals. A self-eval report is a GROUP, and the backend
            # refuses a group-wide verdict on purpose -- "there is no approve-all"
            # is a safety property, not a missing feature. The record's `units`
            # carried every title all along; the TUI simply never read them, so
            # every self-eval approve from here hit that refusal and the TUI
            # printed success over it.
            _units = rec.get("units") or []
            titles = ids = None
            # The unit says how it must be named. self-eval -> titles;
            # belief and governance -> ids. Reading it off the unit is what
            # stops this face growing a per-surface rule it can forget.
            _sel_by = (_units[0].get("select_by") if _units else None) or "title"
            if _units:
                print()
                print(f"  {r.dim('which proposals?')}")
                for j, u in enumerate(_units, 1):
                    _k = _sanitize(u.get("kind") or "")
                    print(f"   {j:>2}. {r.accent(_sanitize(u.get('title','')))}"
                          f"   {r.dim('[' + _k + ']') if _k else ''}")
                pick = _input(f"  {action} which? [1-{len(_units)}, comma-separated, "
                              f"'all', or blank to cancel]: ").strip().lower()
                if not pick:
                    _flash("cancelled — nothing changed.")
                    continue
                if pick in ("all", "a", "*"):
                    _chosen = list(_units)
                else:
                    idx = []
                    for tok in pick.replace(" ", "").split(","):
                        if tok.isdigit() and 1 <= int(tok) <= len(_units):
                            idx.append(int(tok) - 1)
                    if not idx:
                        _flash("no valid proposal numbers — nothing changed.")
                        continue
                    _chosen = [_units[i] for i in sorted(set(idx))]
                if _sel_by == "id":
                    ids = [u.get("id") for u in _chosen if u.get("id") is not None]
                    _n_sel = len(ids)
                else:
                    titles = [u.get("title") for u in _chosen if u.get("title")]
                    _n_sel = len(titles)
                if not _n_sel:
                    _flash("nothing selectable in that choice — nothing changed.")
                    continue
                print(f"  {r.dim(str(_n_sel) + ' selected')}")

            reason = None
            if action == "reject":
                # The backend wants a reason on a rejection, and a rejection
                # without one is the thing that gets re-proposed next month.
                reason = _input("  reason for rejecting (recorded): ").strip() or None
            comment = _input("  comment (optional; a high-trust signal): ").strip() or None
            # Confirm-gate: an explicit y on a state change.
            _n_for_prompt = len(titles or ids or [])
            _what = (f"{_n_for_prompt} proposal(s) of {rec.get('ref')}"
                     if _n_for_prompt else str(rec.get('ref')))
            # SAY WHICH KEY COMMITS. This sits directly under the comment
            # prompt, which is also blank-able, and a bare Enter here means NO.
            # Drew hit Enter on 2026-09-08 and read "nothing changed" as a
            # failure rather than as his own answer -- a correct refusal that
            # LOOKS like a defect has cost the same as a defect.
            conf = _input(f"  type {r.accent('y')} to {action} {_what} "
                          f"— Enter or anything else CANCELS: ").strip().lower()
            if conf not in ("y", "yes"):
                _flash(f"cancelled — no 'y' given, so nothing changed and "
                       f"{_sanitize(str(rec.get('ref')))} is still in the queue.")
                continue
            res = enact(rec.get("ref"), action=action, comment=comment,
                        confirmed_via="interactive", titles=titles, ids=ids,
                        reason=reason)
            # THE VARIANCE GATE, RENDERED RATHER THAN BYPASSED. The enact
            # closure used to hardcode confirm=True, so the L5-I-beta bound on
            # how much ONE enact may apply could never fire from this surface --
            # a gate that is defined and on no path. It now fires here as what
            # it is: the held proposals printed, then a SECOND explicit y, then
            # a re-call carrying confirm. Never auto-answered.
            if isinstance(res, dict) and res.get("status") == "needs_confirm":
                _g = res.get("enact") or {}
                print()
                print(f"  {r.violet('⚑ variance gate held this enact', bold=True)}")
                print(f"  {_sanitize(_g.get('reason') or 'over the bulk-change bound')}")
                for _pv in (_g.get("proposals") or []):
                    print(f"   · {r.accent(_sanitize(_pv.get('title','')))}"
                          f"   {_sanitize(str(_pv.get('priority') or ''))}")
                _c2 = _input(f"  type {r.accent('y')} to apply all "
                             f"{_g.get('magnitude', len(titles or ids or []))} anyway "
                             f"— Enter CANCELS: ").strip().lower()
                if _c2 not in ("y", "yes"):
                    _flash("gate respected — nothing was enacted.")
                    continue
                res = enact(rec.get("ref"), action=action, comment=comment,
                            confirmed_via="interactive", titles=titles, ids=ids,
                            reason=reason, confirm=True)
            actions.append({"ref": rec.get("ref"), "action": action, "result": res})
            # Report the BACKEND's verdict, not the fact that we called it. An
            # enact that failed must not read as an approval that succeeded.
            _ok, _detail = _verdict(res)
            if not _ok:
                _flash(f"{action} DID NOT APPLY — {_sanitize(_detail)}")
            else:
                # "approve"+"d" is fine and "reject"+"d" is "rejectd". Past tense
                # belongs in a table, not in string concatenation.
                _past = {"approve": "approved", "reject": "rejected"}[action]
                if _detail:
                    _past = f"{_past} ({_detail})"
                # DERIVE THE REMAINDER, NEVER ASSUME IT. `len(records) - 1`
                # asserts that deciding an item removes it, which is true of a
                # self-eval report whose every title was named and FALSE of a
                # belief batch where 2 of 39 edges were approved -- it printed
                # "queue clear" over 37 still-pending proposals. A count a human
                # acts on is read at the moment of asking.
                try:
                    _left = len(structured() or [])
                except Exception:
                    _left = None
                _ref_s = _sanitize(rec.get("ref"))
                if _left is None:
                    _flash(f"{_past} {_ref_s} — remaining count unavailable, "
                           f"the list below is the truth")
                elif _left:
                    _flash(f"{_past} {_ref_s} — {_left} item(s) still open")
                else:
                    _flash(f"{_past} {_ref_s} — queue clear")
            continue
    except (EOFError, KeyboardInterrupt):
        return {"status": "ok", "code": "tui_interrupted", "actions": actions,
                "backend": "ansi"}
    except Exception as e:
        return {"status": "error", "code": "tui_error", "message": str(e),
                "actions": actions, "backend": "ansi"}


def _run_textual(*, list_pending, structured, enact, snooze,
                 force_mode) -> Dict[str, Any]:
    """The textual TUI. Built only when `textual` is installed AND a real TTY is
    present. Kept minimal: a list of pending cards + key-bindings (a approve, r
    reject, s snooze, q quit) with a confirm step. Raises on any textual error
    so run_tui() can fall back to ANSI.

    NOTE: textual is an OPTIONAL enhancement; the ANSI fallback is the always-on
    baseline. We import textual lazily HERE so importing this module stays light.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable, Footer, Header, Static
    from textual.containers import Vertical

    records = structured() or []
    actions: List[Dict[str, Any]] = []

    class _ApprovalsApp(App):
        TITLE = "Voidloom approvals"
        BINDINGS = [
            ("a", "approve", "Approve"),
            ("r", "reject", "Reject"),
            ("s", "snooze", "Snooze"),
            ("q", "quit", "Quit"),
        ]

        def compose(self) -> ComposeResult:  # pragma: no cover - UI
            yield Header()
            with Vertical():
                table = DataTable(id="items")
                table.add_columns("#", "title", "ref", "source", "trust")
                for i, rec in enumerate(records, 1):
                    trust = rec.get("trust_tier") or {}
                    tcell = (f"{trust.get('score'):.3f}"
                             if trust.get("score") is not None
                             else (trust.get("label", "")[:24]))
                    table.add_row(str(i), _sanitize(rec.get("title", ""))[:36],
                                  _sanitize(rec.get("ref", "")),
                                  _sanitize(rec.get("source", "")), tcell)
                yield table
                yield Static(self._legend(), id="legend")
            yield Footer()

        def _legend(self) -> str:  # pragma: no cover - UI
            return ("Approve / Reject / Snooze a row (a/r/s); q to quit. "
                    "A comment trains the brain (high-trust).")

        def _selected(self):  # pragma: no cover - UI
            table = self.query_one("#items", DataTable)
            row = table.cursor_row
            if row is None or row < 0 or row >= len(records):
                return None
            return records[row]

        def action_approve(self) -> None:  # pragma: no cover - UI
            self._enact_selected("approve")

        def action_reject(self) -> None:  # pragma: no cover - UI
            self._enact_selected("reject")

        def _enact_selected(self, action: str) -> None:  # pragma: no cover - UI
            rec = self._selected()
            if rec is None:
                return
            res = enact(rec.get("ref"), action=action, comment=None,
                        confirmed_via="interactive")
            actions.append({"ref": rec.get("ref"), "action": action,
                            "result": res})
            self.exit()

        def action_snooze(self) -> None:  # pragma: no cover - UI
            res = snooze(24.0)
            actions.append({"action": "snooze", "result": res})
            self.exit()

    _ApprovalsApp().run()
    return {"status": "ok", "code": "tui_textual_done", "actions": actions,
            "backend": "textual"}

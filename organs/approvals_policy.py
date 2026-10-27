#!/usr/bin/env python3
"""approvals_policy.py — whether the brain may decide its own proposals.

THE OWNER'S RULING (2026-10-26)
    "I'd like all the cron changes to just go straight to full approval, without
    the need to regularly go into the TUI ... default to auto approvals ... turn
    this in the install to default on, with the option to turn it to manual
    approval as a step during installation."  Measured beside it: of every
    decision ever recorded on the owner's brain, 6 were declines, and the one
    thing the human gate approved by hand on 2026-10-26 was 30 false belief links.
    A queue only one person ever opens -- and a Claude Code Desktop user never
    sees the terminal banner that points at it -- is not a safety layer.

WHAT THIS MODULE IS
    ONE setting and ONE policy step.

      approvals.mode  in wizard-config.json:  {"mode": "auto"|"manual",
                                               "set_by": ..., "set_at": ...}
      A MISSING or unreadable setting reads as MANUAL and says it was not
      recorded: a missing key is not a choice, so nothing an older install never
      agreed to is switched on behind it (Council, 2026-10-26).

      decide_new_self_eval_report(db, report_id) -- called by the monthly unit
      right after it writes a report.  In auto mode it approves every proposal
      on THAT report through the same router a person uses
      (voidloom_approvals.approve), signed actor='policy:auto' and
      confirmed_via='policy', so the decision ledger and the audit log can always
      tell a policy decision from a person's.  It never touches an older report:
      switching to auto approves nothing retroactively.

WHAT AUTO DOES NOT COVER, AND WHY
    * belief proposals -- the belief engine judges only pairs a person wrote and
      currently proposes none; its precision on authored text was measured at
      0 of 30 (Council, 2026-10-26).  A producer earns automatic enactment by
      measured precision, so belief batches stay with a person until a detector
      that can tell is measured.
    * governance holds -- a hold exists because a write looked risky; deciding it
      automatically would make the gate decorative.
    Both are named in `show()` so the setting never claims more than it does.

WHAT A SELF-EVAL APPROVAL DOES
    Records the decision and writes the proposal as a procedural memory
    (enact_approved).  It changes no code and no schedule; nothing it enacts
    needs undoing beyond `voidloom approvals --mode manual` for the next one.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

MODES = ("auto", "manual")
CONFIG_KEY = "approvals"
POLICY_ACTOR = "policy:auto"
POLICY_CONFIRMED_VIA = "policy"
COVERS = ("self-eval",)
NOT_COVERED = {
    "belief": "proposes nothing until a contradiction detector with measured precision exists",
    "governance": "a hold is a risky write; it always waits for a person",
}


def config_path() -> str:
    """wizard-config.json, by the same resolution wizard_config uses."""
    env = (os.environ.get("WIZARD_CONFIG_PATH") or "").strip()
    if env:
        return env
    try:
        import voidloom_env
        return str(voidloom_env.env_path("wizard-config.json"))
    except Exception:
        return str(Path.home() / ".voidloom" / "wizard-config.json")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def show(path: Optional[str] = None) -> Dict[str, Any]:
    """The mode in force and where it came from. Never raises."""
    p = path or config_path()
    out: Dict[str, Any] = {"mode": "manual", "recorded": False, "path": p,
                           "covers": list(COVERS), "not_covered": dict(NOT_COVERED)}
    if not os.path.exists(p):
        out["reason"] = "no wizard-config.json: nothing was chosen, so a person decides"
        return out
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        out["reason"] = "wizard-config.json is not readable JSON (%s): a person decides" % e
        return out
    rec = data.get(CONFIG_KEY) if isinstance(data, dict) else None
    if not isinstance(rec, dict) or rec.get("mode") not in MODES:
        out["reason"] = "no approvals choice is recorded: a person decides"
        return out
    out.update({"mode": rec["mode"], "recorded": True,
                "set_by": rec.get("set_by"), "set_at": rec.get("set_at")})
    return out


def is_auto(path: Optional[str] = None) -> bool:
    return show(path)["mode"] == "auto"


def set_mode(mode: str, *, set_by: str, path: Optional[str] = None,
             dry_run: bool = False) -> Tuple[int, str]:
    """Record the mode. Same recipe as the reranker gate: an unreadable or
    non-object file refuses (rc 3) with nothing written; a readable one is
    backed up, replaced atomically and read back. Every other key is kept."""
    if mode not in MODES:
        return 2, "mode must be one of %s, not %r" % ("/".join(MODES), mode)
    p = path or config_path()
    data: Dict[str, Any] = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            return 3, ("%s is not readable JSON (%s); the approvals choice was NOT recorded and the "
                       "file was not touched -- fix or move it, then re-run" % (p, e))
        if not isinstance(data, dict):
            return 3, "%s holds a JSON %s, not an object; NOT touched" % (p, type(data).__name__)
    rec = {"mode": mode, "set_by": set_by, "set_at": _now()}
    if dry_run:
        return 0, "would record approvals.mode=%s in %s" % (mode, p)
    merged = dict(data)
    merged[CONFIG_KEY] = rec
    d = os.path.dirname(os.path.abspath(p))
    if not os.path.isdir(d):
        os.makedirs(d, mode=0o700)
    if os.path.exists(p):
        shutil.copy2(p, p + ".approvals.bak")   # not .bak: that is the reranker gate's pre-image
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)
    with open(p, "r", encoding="utf-8") as fh:
        back = json.load(fh)
    if back != merged:
        return 3, "%s did not read back as written" % p
    return 0, "recorded approvals.mode=%s in %s" % (mode, p)


def _report_titles(db_path: str, report_id: int) -> List[str]:
    con = sqlite3.connect("file:%s?mode=ro" % Path(db_path).as_posix(), uri=True)
    try:
        row = con.execute("SELECT proposed_enhancements, proposed_deprecations FROM self_eval_reports "
                          "WHERE id = ?", (report_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        return []
    titles: List[str] = []
    for raw in row:
        try:
            items = json.loads(raw or "[]")
        except ValueError:
            items = []
        for it in items if isinstance(items, list) else []:
            t = it.get("title") if isinstance(it, dict) else None
            if t and t not in titles:
                titles.append(t)
    return titles


def _titles_a_person_declined(db_path: str, titles: List[str]) -> Tuple[set, bool]:
    """(the titles a person declined on ANY earlier report, the ledger was readable).
    A decline is 'don't raise again' (Council, 2026-10-26): the policy never
    approves such a title -- the heuristic proposer re-proposes the same titles
    monthly (review 2, N2). An unreadable ledger cannot tell, so it returns every
    title with readable=False: they are held for a person, never declined."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % Path(db_path).as_posix(), uri=True)
    except sqlite3.Error:
        return set(titles), False
    try:
        rows = con.execute(
            "SELECT DISTINCT u.proposal_title FROM decision_events e JOIN decision_units u ON u.id = e.unit_id "
            "WHERE e.outcome = 'declined' AND e.actor != ?", (POLICY_ACTOR,)).fetchall()
    except sqlite3.Error:
        return set(titles), False
    finally:
        con.close()
    return {r[0] for r in rows} & set(titles), True


def decide_new_self_eval_report(db_path: str, report_id: Optional[int], *,
                                config: Optional[str] = None) -> Dict[str, Any]:
    """The policy step the monthly unit runs after writing report `report_id`."""
    mode = show(config)
    if report_id is None:
        return {"status": "skipped", "reason": "no report was written", "mode": mode["mode"]}
    if mode["mode"] != "auto":
        return {"status": "held", "mode": "manual", "report_id": report_id,
                "reason": mode.get("reason") or "approvals.mode is manual: a person decides"}
    titles = _report_titles(db_path, report_id)
    declined, readable = _titles_a_person_declined(db_path, titles)
    if not readable:
        return {"status": "held", "mode": "auto", "report_id": report_id, "held": sorted(titles),
                "reason": "the decision ledger could not be read, so no earlier 'no' can be honoured; "
                          "the whole report waits for a person"}
    again = sorted(declined)
    titles = [t for t in titles if t not in declined]
    if not titles and not again:
        return {"status": "skipped", "mode": "auto", "report_id": report_id,
                "reason": "the report carries no proposals"}
    import voidloom_approvals as va
    out: Dict[str, Any] = {"mode": "auto", "report_id": report_id, "actor": POLICY_ACTOR,
                           "approved": titles, "declined_again": again}
    if again:
        # A person said no before: the policy repeats that no rather than leaving the title
        # in the queue every month (recheck R3-6; the Council's "don't raise again").
        rej = va.reject("self-eval:%d" % report_id, titles=again, db=db_path,
                        confirmed_via=POLICY_CONFIRMED_VIA, actor=POLICY_ACTOR,
                        reason="declined by a person on an earlier report; not raised again")
        out["declined_again_status"] = rej.get("status")
        out["status"] = rej.get("status", "error")
    if titles:
        # confirm=True: the variance gate holds a bulk enactment for a person to confirm.
        # The owner's ruling (2026-10-26) was "straight to full approval": auto decides the
        # whole report it just wrote, and the audit row says the policy did it.
        res = va.approve("self-eval:%d" % report_id, titles=titles, db=db_path, confirm=True,
                         confirmed_via=POLICY_CONFIRMED_VIA, actor=POLICY_ACTOR)
        out.update({"status": res.get("status", "error") if out.get("status", "ok") == "ok"
                    else out["status"], "audit_id": res.get("audit_id"), "result": res.get("action")})
    return out


def recent_policy_decisions(db_path: str, days: float = 7.0) -> List[Dict[str, Any]]:
    """Audit rows the policy wrote in the last `days`. The oversight surface: what the
    brain decided without a person, read from the same log a person's decisions use."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % Path(db_path).as_posix(), uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT id, ref, action, decision_detail, outcome_status, created_at FROM approval_audit_log "
            "WHERE actor = ? AND created_at >= datetime('now', ?) ORDER BY id",
            (POLICY_ACTOR, "-%d seconds" % int(days * 86400))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for aid, ref, action, detail, status, at in rows:
        try:
            titles = (json.loads(detail or "{}") or {}).get("titles") or []
        except ValueError:
            titles = []
        out.append({"audit_id": aid, "ref": ref, "action": action, "titles": titles,
                    "status": status, "at": at})
    return out


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="whether the brain decides its own proposals")
    sub = ap.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("show")
    s.add_argument("--json", action="store_true")
    w = sub.add_parser("set")
    w.add_argument("mode", choices=MODES)
    w.add_argument("--by", default=None,
                   help="the installer's door: owner | owner-flag | installer-default (a person or an "
                        "agent uses `voidloom approvals --mode`)")
    w.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.verb == "show":
        st = show()
        if a.json:
            print(json.dumps(st, indent=2))
        else:
            how = ("recorded by %s at %s" % (st.get("set_by"), st.get("set_at")) if st["recorded"]
                   else st.get("reason"))
            print("approvals: %s (%s)" % (st["mode"], how))
            print("  automatic in auto mode: %s" % ", ".join(COVERS))
            for k, v in NOT_COVERED.items():
                print("  always a person: %s -- %s" % (k, v))
        return 0
    import getpass
    who = (os.environ.get("VOIDLOOM_ACTOR") or "").strip() or getpass.getuser() or "operator"
    try:
        _tty = sys.stdin.isatty()
    except Exception:
        _tty = False
    # Only the installer's three labels are taken as given; anything else is recorded as
    # what it is -- this organ run, from a terminal or not (recheck R3-5).
    by = a.by if a.by in ("owner", "owner-flag", "installer-default") else \
        "%s via organ %s" % (who, "terminal" if _tty else "no-terminal")
    rc, msg = set_mode(a.mode, set_by=by, dry_run=a.dry_run)
    print(msg, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(_main())

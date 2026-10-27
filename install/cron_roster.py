#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cron_roster.py -- the ONE reader of install/cron_roster.json.

    python3 install/cron_roster.py check --cron-dir cron/ [--json]
        every packaged wizard-library-*.timer is classified in the roster and every
        roster unit has a packaged timer; rc 0 agree, 1 disagree (names printed),
        2 unreadable. Both installers run this BEFORE arming anything.

    python3 install/cron_roster.py rows [--set armed|all] [--only a,b] [--platform systemd|launchd]
        one line per unit:
          name|exec|exec_post|systemd|launchd|timeout|desc|service_extra|timer_extra|service_omit
        placeholders {DB} {CFG} {BRAIN} {HOME_LOCAL_BIN} are left for the installer to
        substitute; timeout is empty when the packaged unit carries no TimeoutStartSec;
        the three optional lists are `;`-joined (empty when absent).
        --only names any roster unit, template or not: an explicit opt-in.

    The optional per-unit fields carry what the ONE template does not (round-1
    iteration-2 reflection, item 2: the template rendered After=network-online.target
    for 0 of 17 packaged units and lost organ-run-log's Nice=, Environment=PATH=,
    Documentation= and its timer's RandomizedDelaySec=):
      service_extra  ["Key=Value", ...]  lines ADDED to the service; an `After=` here
                     REPLACES the template's After= (default network.target); the
                     installer routes each line to [Unit] / [Service] / [Install] by key
      timer_extra    ["Key=Value", ...]  lines ADDED to the timer (same routing)
      service_omit   ["Key=", ...]       template lines the packaged unit does NOT carry,
                     matched by prefix (e.g. "WorkingDirectory=", "Environment=PYTHON_KEYRING_BACKEND")
    voidloom/tests/test_cron_roster.py renders every unit through install_crons.sh
    --dry-run and proves the directive set equals the packaged unit's.

    python3 install/cron_roster.py armed
        the armed-by-default names, one per line.

    python3 install/cron_roster.py claude-callers [--derive] [--json]
        (Round 2 wave 1, M13 with the owner's condition) each unit's `claude_credential`: how its packaged
        invocation gets a CLAUDE credential -- `thinker` (through organs/thinker.py, which reads ~/.voidloom/thinker/
        claude.env first), `env-file` (it reads CLAUDE_CODE_OAUTH_TOKEN from its OWN environment, i.e. the env file,
        or launches claude itself), or `none`. --derive re-derives it from the code (the entry points' import
        closure over organs/) instead of reading the roster, so a test can pin the declaration to the code.

    python3 install/cron_roster.py thinks [--set armed|all]
        the units whose packaged invocation reaches organs/thinker.py (roster field `thinks`), one per line --
        the units an installer may not arm before the overnight-thinker question has an answer.

Stdlib only; Python >= 3.8. install.sh and install/install_crons.sh both call
this; neither carries a table of its own (Council #12: CRON_DEFS 6 / install_crons
4 / cron/ 17 / sleep-loop in none of them).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROSTER = HERE / "cron_roster.json"
PREFIX = "wizard-library-"
ARM_VALUES = ("armed-by-default", "ships-as-template")
FIELDS = ("arm", "why", "exec", "systemd", "launchd", "timeout", "desc")
OPTIONAL_LISTS = ("service_extra", "timer_extra", "service_omit")
ROW_SEP, LIST_SEP = "|", ";"
CLAUDE_CREDENTIAL = ("thinker", "env-file", "none")
# what makes a unit read the Claude credential ITSELF (not through the seam): the token's or an API key's variable NAME
# as a string in code, the Anthropic SDK, or a launch whose program is claude
_DIRECT_NAMES = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
_SEAM_CALLS = ("think", "weekly_check", "probe_candidate")


def _module_edges(organs: Path, path: Path):
    """(imported organ modules, direct-credential reasons, seam calls) of one organs/*.py file, by AST."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                doc_nodes.add(id(first.value))
    mods, direct, seam = set(), [], []
    have = {q.stem for q in organs.glob("*.py")}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names if a.name.split(".")[0] in have}
            if any(a.name.split(".")[0] == "anthropic" for a in node.names):
                direct.append("imports anthropic")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in have:
                    mods.add(top)
                if top == "anthropic":
                    direct.append("imports anthropic")
            for a in node.names:
                if a.name in have:
                    mods.add(a.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            v = node.value
            if v.endswith(".py") and Path(v).stem in have:
                mods.add(Path(v).stem)                    # an organ launched as a subprocess by its file name
            elif v in have and len(v) > 3:
                pass
            for n in _DIRECT_NAMES:
                if n in v and path.name != "thinker.py":
                    direct.append("names %s" % n)
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in _SEAM_CALLS and isinstance(f.value, ast.Name) \
                    and "thinker" in f.value.id:
                seam.append(f.attr)
            if isinstance(f, ast.Attribute) and f.attr == "import_module" and node.args \
                    and isinstance(node.args[0], ast.Constant) and str(node.args[0].value) in have:
                mods.add(str(node.args[0].value))
            if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)) and node.args[0].elts:
                e0 = node.args[0].elts[0]
                if isinstance(e0, ast.Constant) and str(e0.value) == "claude":
                    direct.append("launches claude")
    return mods, sorted(set(direct)), sorted(set(seam))


def derive_claude_credential(brain: Path, unit: dict):
    """(claude_credential, why) for one roster unit, derived from its packaged entry points' import closure."""
    organs = brain / "organs"
    todo = []
    for key in ("exec", "exec_post"):
        cmd = unit.get(key) or ""
        first = cmd.split()[0] if cmd.split() else ""
        if first.startswith("organs/") and first.endswith(".py"):
            todo.append(Path(first).stem)
    seen, direct, seam = set(), {}, {}
    while todo:
        m = todo.pop()
        if m in seen or not (organs / (m + ".py")).is_file():
            continue
        seen.add(m)
        mods, d, s = _module_edges(organs, organs / (m + ".py"))
        if d:
            direct[m] = d
        if s:
            seam[m] = s
        todo.extend(sorted(mods - seen))
    if direct:
        return "env-file", "reads the credential itself: %s" % "; ".join("%s (%s)" % (k, ", ".join(v)) for k, v in sorted(direct.items()))
    if seam and "thinker" in seen:
        return "thinker", "reaches the seam: %s" % ", ".join("%s.%s" % (k, "/".join(v)) for k, v in sorted(seam.items()))
    return "none", "no Claude call in its import closure (%d modules)" % len(seen)


def load(path: Path):
    """(roster dict, error). A roster with no units is an error, never an empty set."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, "roster unreadable: %s: %s: %s" % (path, type(e).__name__, e)
    units = doc.get("units") if isinstance(doc, dict) else None
    if not isinstance(units, dict) or not units:
        return None, "roster has no units: %s" % path
    bad = []
    for name, u in units.items():
        if not isinstance(u, dict):
            bad.append("%s: not an object" % name); continue
        for f in FIELDS:
            if f not in u:
                bad.append("%s: missing field %r" % (name, f))
        if u.get("arm") not in ARM_VALUES:
            bad.append("%s: arm=%r not in %s" % (name, u.get("arm"), ARM_VALUES))
        if not str(u.get("why", "")).strip():
            bad.append("%s: empty why" % name)
        if not str(u.get("exec", "")).strip() or not str(u.get("exec", "")).startswith("organs/"):
            bad.append("%s: exec must be a brain-relative organs/ command, got %r" % (name, u.get("exec")))
        if u.get("claude_credential") not in CLAUDE_CREDENTIAL:
            bad.append("%s: claude_credential must be one of %s (how the unit gets a Claude credential; derive it with "
                       "`cron_roster.py claude-callers --derive`), got %r" % (name, CLAUDE_CREDENTIAL, u.get("claude_credential")))
        if not isinstance(u.get("thinks"), bool):
            bad.append("%s: thinks must be true or false (does the packaged invocation reach organs/thinker.py?), "
                       "got %r" % (name, u.get("thinks")))
        if "timeout" in u and u["timeout"] is not None and not (isinstance(u["timeout"], int) and u["timeout"] > 0):
            bad.append("%s: timeout must be a positive integer or null, got %r" % (name, u["timeout"]))
        for f in OPTIONAL_LISTS:
            if f not in u:
                continue
            v = u[f]
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                bad.append("%s: %s must be a list of non-empty strings" % (name, f)); continue
            for x in v:
                if ROW_SEP in x or LIST_SEP in x or "\n" in x:
                    bad.append("%s: %s entry %r may not contain %r, %r or a newline" % (name, f, x, ROW_SEP, LIST_SEP))
                elif f != "service_omit" and "=" not in x:
                    bad.append("%s: %s entry %r is not a Key=Value line" % (name, f, x))
                elif f == "service_omit" and not x.split("=")[0]:
                    bad.append("%s: service_omit entry %r has no key" % (name, x))
    if bad:
        return None, "roster malformed (%d): %s" % (len(bad), "; ".join(bad[:8]))
    return doc, None


def packaged_timers(cron_dir: Path):
    return sorted(p.name[len(PREFIX):-len(".timer")]
                  for p in cron_dir.glob(PREFIX + "*.timer") if p.is_file())


def check(roster_path: Path, cron_dir: Path, as_json: bool) -> int:
    doc, err = load(roster_path)
    if err:
        print(err, file=sys.stderr)
        return 2
    if not cron_dir.is_dir():
        print("cron dir not found: %s" % cron_dir, file=sys.stderr)
        return 2
    packaged = packaged_timers(cron_dir)
    if not packaged:
        print("no %s*.timer under %s -- an empty population is a refusal" % (PREFIX, cron_dir),
              file=sys.stderr)
        return 2
    units = doc["units"]
    unclassified = sorted(set(packaged) - set(units))
    phantom = sorted(set(units) - set(packaged))
    armed = sorted(n for n, u in units.items() if u["arm"] == "armed-by-default")
    templates = sorted(n for n, u in units.items() if u["arm"] == "ships-as-template")
    out = {"roster": str(roster_path), "cron_dir": str(cron_dir), "packaged": len(packaged),
           "armed_by_default": armed, "ships_as_template": templates,
           "unclassified_packaged_timers": unclassified, "roster_units_without_a_timer": phantom,
           "agree": not unclassified and not phantom}
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print("[roster] packaged timers %d; armed-by-default %d (%s); ships-as-template %d (%s)"
              % (len(packaged), len(armed), ", ".join(armed), len(templates), ", ".join(templates)))
        if unclassified:
            print("[roster] UNCLASSIFIED packaged timers (add them to install/cron_roster.json): "
                  + ", ".join(unclassified), file=sys.stderr)
        if phantom:
            print("[roster] roster units with NO packaged timer: " + ", ".join(phantom), file=sys.stderr)
    return 0 if out["agree"] else 1


def rows(roster_path: Path, which: str, only: str, platform: str) -> int:
    doc, err = load(roster_path)
    if err:
        print(err, file=sys.stderr)
        return 2
    units = doc["units"]
    if only:
        names = [n for n in only.split(",") if n.strip()]
        unknown = [n for n in names if n not in units]
        if unknown:
            print("unknown unit(s) in --only: %s (roster has: %s)"
                  % (", ".join(unknown), ", ".join(sorted(units))), file=sys.stderr)
            return 1
    elif which == "armed":
        names = [n for n, u in units.items() if u["arm"] == "armed-by-default"]
    else:
        names = list(units)
    for n in names:
        u = units[n]
        launchd = u.get("launchd")
        if platform == "launchd" and not launchd:
            print("%s: no launchd calendar in the roster; install the packaged unit by hand" % n,
                  file=sys.stderr)
            return 1
        print(ROW_SEP.join([n, u["exec"], u.get("exec_post") or "", u["systemd"], launchd or "",
                            "" if u["timeout"] is None else str(u["timeout"]), u["desc"],
                            LIST_SEP.join(u.get("service_extra") or []),
                            LIST_SEP.join(u.get("timer_extra") or []),
                            LIST_SEP.join(u.get("service_omit") or [])]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--roster", default=str(DEFAULT_ROSTER))
    sub = ap.add_subparsers(dest="verb")
    c = sub.add_parser("check")
    c.add_argument("--cron-dir", required=True)
    c.add_argument("--json", action="store_true")
    r = sub.add_parser("rows")
    r.add_argument("--set", default="armed", choices=("armed", "all"))
    r.add_argument("--only", default="")
    r.add_argument("--platform", default="systemd", choices=("systemd", "launchd"))
    sub.add_parser("armed")
    t = sub.add_parser("thinks")
    t.add_argument("--set", default="all", choices=("armed", "all"))
    cc = sub.add_parser("claude-callers")
    cc.add_argument("--derive", action="store_true")
    cc.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.verb == "claude-callers":
        doc, err = load(Path(a.roster))
        if err and not a.derive:
            print(err, file=sys.stderr)
            return 2
        if doc is None:
            doc = json.loads(Path(a.roster).read_text(encoding="utf-8"))
        out = {}
        for n, u in doc["units"].items():
            if a.derive:
                cred, why = derive_claude_credential(Path(a.roster).resolve().parent.parent, u)
            else:
                cred, why = u.get("claude_credential"), "declared"
            out[n] = {"claude_credential": cred, "arm": u.get("arm"), "why": why}
        if a.json:
            print(json.dumps(out, indent=1))
        else:
            for n, v in out.items():
                print("%s|%s|%s|%s" % (n, v["claude_credential"], v["arm"], v["why"]))
        return 0
    if a.verb == "check":
        return check(Path(a.roster), Path(a.cron_dir), a.json)
    if a.verb == "rows":
        return rows(Path(a.roster), a.set, a.only, a.platform)
    if a.verb == "armed":
        doc, err = load(Path(a.roster))
        if err:
            print(err, file=sys.stderr)
            return 2
        for n, u in doc["units"].items():
            if u["arm"] == "armed-by-default":
                print(n)
        return 0
    if a.verb == "thinks":
        doc, err = load(Path(a.roster))
        if err:
            print(err, file=sys.stderr)
            return 2
        for n, u in doc["units"].items():
            if u["thinks"] and (a.set == "all" or u["arm"] == "armed-by-default"):
                print(n)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

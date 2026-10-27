#!/usr/bin/env python3
"""C7 — a state with no writer, or a claim wider than its predicate.

    usage: c7_writer_and_claim_suite.py <package_dir> <scratch_root> [--only NAME]
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, Dict, List

if len(sys.argv) < 3:
    sys.exit("usage: c7_writer_and_claim_suite.py <package_dir> <scratch_root> [--only NAME]")
PKG = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(sys.argv[2])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
os.makedirs(ROOT, exist_ok=True)
INSTR = os.path.join(PKG, "organs", "voidloom_instructions.py")
RESULTS: List[Dict[str, Any]] = []


def note(name: str, held: bool, detail: Any) -> None:
    if ONLY and ONLY not in name:
        return
    RESULTS.append({"name": name, "held": bool(held), "detail": detail})
    print(f"  {name:<58s} {'HELD' if held else 'VIOLATED':<9s} {json.dumps(detail, default=str)[:240]}")


def home(tag: str) -> str:
    h = os.path.join(ROOT, tag)
    shutil.rmtree(h, ignore_errors=True)
    for sub in (".agents", ".claude", ".codex"):
        os.makedirs(os.path.join(h, sub))
    return h


def run(h: str, *argv: str):
    env = {**os.environ, "HOME": h, "CLAUDE_CONFIG_DIR": os.path.join(h, ".claude"),
           "CODEX_HOME": os.path.join(h, ".codex"),
           "VOIDLOOM_INSTALL_RECEIPT": os.path.join(h, "receipt.json"),
           "VOIDLOOM_PREIMAGE_STORE": os.path.join(h, "preimages")}
    env.pop("WIZARD_LIBRARY_DB", None)
    p = subprocess.run([sys.executable, INSTR, *argv, "--home", h],
                       capture_output=True, text=True, env=env, timeout=600)
    try:
        d = json.loads(p.stdout)
    except Exception:
        d = {"status": "UNPARSEABLE", "raw": (p.stdout or "")[-200:], "err": (p.stderr or "")[-200:]}
    return p.returncode, d


DOCTRINE_BEGIN = "# >>> VOIDLOOM DOCTRINE BEGIN >>>"
DOCTRINE_END = "# <<< VOIDLOOM DOCTRINE END <<<"

# ===========================================================================
# C7a.1 — INSTALL WRITES THE DOCTRINE BLOCK.  On the hook-less tier that block
#   IS the product: no hook fires there, so the file is the whole delivery.
#   `hooks status` has a FILE_ONLY state and a probe for it; nothing made it true.
# ===========================================================================
h = home("c7_1_install")
rc, d = run(h, "install", "--yes", "--json")
files = {str(p): p.read_text(encoding="utf-8", errors="replace")
         for p in pathlib.Path(h).rglob("*.md")}
with_doc = [p for p, t in files.items() if DOCTRINE_BEGIN in t]
agents = [p for p in files if p.endswith("AGENTS.md")]
note("C7a_1_install_writes_the_doctrine_block_to_the_file_tier",
     rc == 0 and bool(agents) and all(DOCTRINE_BEGIN in files[p] and DOCTRINE_END in files[p]
                                      for p in agents),
     {"rc": rc, "files": len(files), "agents_md": len(agents),
      "with_doctrine": len(with_doc),
      "sizes": {os.path.basename(p): len(t) for p, t in files.items()}})

# ===========================================================================
# C7a.2 — THE DOCTRINE IS DERIVED FROM THE PREAMBLE, not a second copy of it.
# ===========================================================================
sys.path.insert(0, os.path.join(PKG, "organs"))
derived_ok, detail2 = False, {}
try:
    import voidloom_preamble as vp  # noqa: E402
    segs, _meta = vp.build_segments(db=os.path.join(PKG, "claude_memory.db"),
                                    home=h, organs=os.path.join(PKG, "organs"),
                                    owner=None)  # whatever THIS brain declares
    doctrine_text = "\n\n".join(s.text for s in segs if s.stratum == "doctrine")
    probe = [ln.strip() for ln in doctrine_text.splitlines() if len(ln.strip()) > 40][:6]
    body = files.get(agents[0], "") if agents else ""
    hit = [ln for ln in probe if ln in body]
    derived_ok = bool(probe) and len(hit) == len(probe)
    detail2 = {"doctrine_segments": sum(1 for s in segs if s.stratum == "doctrine"),
               "probe_lines": len(probe), "found_in_file": len(hit)}
except Exception as e:
    detail2 = {"error": f"{type(e).__name__}: {e}"}
note("C7a_2_the_installed_doctrine_is_the_preambles_own_doctrine_segments",
     derived_ok, detail2)

# ===========================================================================
# C7a.3 — STATUS REPORTS IT, AND UNINSTALL REMOVES IT (the block promises this).
# ===========================================================================
rc_s, d_s = run(h, "status", "--json")
rows = []
_d = d_s.get("data") or {}
for key in ("targets", "rows", "results"):
    if isinstance(_d.get(key), list):
        rows = _d[key]
        break
reports = [r for r in rows if isinstance(r, dict) and "carries_doctrine" in r]
rc_u, d_u = run(h, "uninstall", "--yes", "--json")
left = [str(p) for p in pathlib.Path(h).rglob("*.md")
        if DOCTRINE_BEGIN in p.read_text(encoding="utf-8", errors="replace")]
note("C7a_3_status_reports_the_doctrine_and_uninstall_removes_it",
     bool(reports) and rc_u == 0 and not left,
     {"rows": len(rows), "rows_reporting_carries_doctrine": len(reports),
      "uninstall_rc": rc_u, "files_still_carrying_doctrine": left})

# ===========================================================================
# C7b — THE NOTICE CONCLUDES ONLY WHAT ITS PREDICATE SUPPORTS.
#   "the line is absent from my context" does not establish "the hook did not
#   fire in this session": a context can be compacted, truncated or summarised.
# ===========================================================================
src = pathlib.Path(INSTR).read_text(encoding="utf-8")
i = src.find("check your own context")
notice = src[max(0, i - 400): i + 1400] if i >= 0 else ""
overclaims = [s for s in ("did\nNOT fire in this session", "did NOT fire in this session",
                          "is not active in this\nsession", "is not active in this session")
              if s in notice]
hedges = [s for s in ("may not have", "cannot see", "not visible", "compacted",
                      "truncated", "summarised", "summarized", "in what you can see")
          if s in notice]
note("C7b_the_notice_claims_only_what_an_absent_line_can_establish",
     bool(notice) and not overclaims and bool(hedges),
     {"found_notice": bool(notice), "overclaims": overclaims, "hedges_present": hedges})

# ---------------------------------------------------------------------------
held = sum(1 for r in RESULTS if r["held"])
print(json.dumps({"package": PKG, "scratch_root": ROOT, "held": held,
                  "total": len(RESULTS),
                  "violated": [r["name"] for r in RESULTS if not r["held"]]}, indent=1))
# CRUCIBLE-3 D1-F4: `held == len(RESULTS)` is VACUOUSLY TRUE at len(RESULTS) == 0,
# so this suite printed "0/0 CHECKS HELD" and exited 0 having measured NOTHING --
# an acceptance gate passing over zero observations, in the instrument that grades
# the fix. `cli_install_suite.py:1620-1647` closed exactly this for itself; the six
# sibling suites kept the vacuous form. ZERO CHECKS IS A FAILURE: there is no
# observation to hold, and rc 2 (not 0, not 1) says "unmeasured", which is not the
# same as "measured and broken".
if not RESULTS:
    print("\n  ===> NO CHECKS EXECUTED — nothing was measured, so nothing held.\n")
    sys.exit(2)
print(f"\n  ===> {held}/{len(RESULTS)} CHECKS HELD\n")
sys.exit(0 if held == len(RESULTS) else 1)

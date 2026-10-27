#!/usr/bin/env python3
"""Least-privilege posture of autonomous model calls, and the R8 failure detail every caller shares.

Voidloom #176 (C2, generalized in iter2 from gap_mapping's round-1 seal).

WHY THIS MODULE EXISTS
----------------------
Several wizard-library crons run a headless ``claude -p`` session, injecting
Drew's ``CLAUDE_CODE_OAUTH_TOKEN`` so the session is authenticated. WITHOUT a
tool restriction, that session inherits the FULL connector surface
(Gmail / Drive / Slack / Notion / Supabase, …) plus arbitrary shell and local
file mutation. Each of these crons feeds the model untrusted or self-generated
text (dream synthesis, weekly-digest observations, counterfactual analysis,
council prose). That is the classic *confused-deputy* exfil hole: untrusted
input steering an authenticated deputy into a write/exfil connector.

A single shared, deterministic flag set keeps every cron's posture identical
and auditable — there is now ONE place that defines "what an autonomous cron is
allowed to do", instead of each cron re-deriving (or forgetting) the restriction.

THE SECURITY PRINCIPLE: WILDCARD-DENY > ALLOWLIST
-------------------------------------------------
``--disallowedTools`` carries an ``mcp__*`` wildcard that hard-denies EVERY MCP
connector regardless of which connectors happen to be configured. This is
strictly stronger than an allowlist:
  * there is no list of connector names to keep in sync, and
  * any future connector is blocked the day it is added — no code change needed.
``--disallowedTools`` also wins over ``--allowedTools`` on conflict, so the deny
is the floor: even a web-shaped tool cannot also be an MCP connector. The tight
``--allowedTools`` exists only so the legitimate web work that gap research needs keeps functioning (fail-graceful
for its purpose); since the owner's ruling of 2026-09-27 (M3, ruler VL-MC-v1.1) research is WEB ONLY -- no local read.

Since the model-choice unit (Round 1) the posture is CATALOG DATA (organs/thinkers.json, per purpose: text for the
seven synthesis organs, research for gap research) and organs/thinker.py builds every vendor argv. This module reads
the catalog at import (stdlib only) so the values below can never drift from it, and keeps failed_run_detail, the
one R8 renderer of what a failed CLI said. A missing or refused catalog makes the import fail LOUDLY: the seam
refuses to think without it, which is the fail-closed shape (never "no flags").
"""

from __future__ import annotations

from typing import List

__all__ = [
    "least_privilege_flags",
    "DENY_TOOLS",
    "ALLOW_TOOLS",
    "PERMISSION_MODE",
]

# THE CLAUDE POSTURE IS CATALOG DATA NOW (model-choice unit, Round 1). organs/thinkers.json holds it per PURPOSE --
# `text` for the seven synthesis organs (--tools "": no tools at all), `research` for gap research (web search
# and fetch only -- the owner's ruling 2026-09-27; Read/Glob/Grep, MCP, shell and writes denied) -- and organs/thinker.py is the only code that builds a vendor argv. What follows
# is DERIVED from that catalog, so there is still exactly one place that says what an autonomous Claude call may do.
# Every call also carries --safe-mode (no CLAUDE.md, hooks, skills, plugins or MCP; measured 2026-09-26: 7 hooks and
# the ancestor CLAUDE.md reached every organ call without it), and --tools, which is VARIADIC, sits BEFORE the
# single-valued --permission-mode so it can never swallow a prompt (#21971).


def _catalog_claude():
    import json as _json
    from pathlib import Path as _Path
    cat = _json.loads((_Path(__file__).resolve().with_name("thinkers.json")).read_text(encoding="utf-8"))
    return cat["backends"]["claude"]


def _flag_value(flags: List[str], flag: str) -> List[str]:
    return [x for x in flags[flags.index(flag) + 1].split(",") if x] if flag in flags else []


_RESEARCH = _catalog_claude()["posture"]["research"]
DENY_TOOLS: List[str] = _flag_value(_RESEARCH, "--disallowedTools")
ALLOW_TOOLS: List[str] = _flag_value(_RESEARCH, "--tools")
PERMISSION_MODE: str = _RESEARCH[_RESEARCH.index("--permission-mode") + 1]


def least_privilege_flags() -> List[str]:
    """The research posture of an autonomous ``claude -p`` (gap research), as the catalog states it, preceded by
    --safe-mode. Kept for any caller outside the package; inside it, organs/thinker.py builds every argv.
    A fresh list is returned on every call so a caller can safely extend it."""
    return ["--safe-mode"] + list(_catalog_claude()["posture"]["research"])


def failed_run_detail(result, limit: int = 300) -> str:
    """What a FAILED ``claude -p`` said, from wherever it said it -- one line.

    Reference-host round, R8. Every cron that shells out to ``claude -p`` logged a
    failure as ``result.stderr[:N]``. Measured on the reference host, 2026-09-23: the
    dream's warning read ``claude -p exited 1:`` with NOTHING after the colon, seven
    times in one night, while the run reported phases_error 0. Whatever ``claude -p``
    said, it did not say it on stderr -- stdout is the only other channel (a usage
    limit is plausible for that day and was never recorded) -- and the one channel the
    warning read was empty. So: stderr when it carries something, else stdout's TAIL
    (the end is where the error is), else say plainly that both were empty -- which
    is itself a fact worth a log line, never a blank.
    """
    def _tail(text: str) -> str:
        flat = " | ".join(line.strip() for line in text.strip().splitlines() if line.strip())
        return flat if len(flat) <= limit else "..." + flat[-limit:]

    err = getattr(result, "stderr", None) or ""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    if err.strip():
        return "stderr: " + _tail(err)
    out = getattr(result, "stdout", None) or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    if out.strip():
        return "stdout (stderr was empty): " + _tail(out)
    return "no output on stderr or stdout"


if __name__ == "__main__":  # pragma: no cover - tiny self-check / inspection aid
    import json

    print(json.dumps(least_privilege_flags()))

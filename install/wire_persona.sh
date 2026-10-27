#!/usr/bin/env bash
# ============================================================================
# wire_persona.sh — wire a self-authored persona-skill to auto-load every session.
# ============================================================================
# AFTER you (a fresh Claude inheriting this brain) have created your own persona
# at  ~/.claude/skills/<persona-name>/SKILL.md  (see ONBOARDING_GUIDE.md, Step 3),
# run:
#
#     bash wire_persona.sh <persona-name>
#
# It defensively wires TWO things so your persona loads at the start of EVERY
# Claude Code session, exactly the way the memory hook was wired by install.sh:
#
#   1. The SessionStart memory hook — ensured present via `voidloom_hooks_status.py
#      --install`, the one thing that owns hook wiring (idempotent; it refuses
#      rather than clobbers, and supersedes a legacy element in the same write).
#   2. A marker-delimited "PERSONA AUTO-LOAD" block in ~/.claude/CLAUDE.md telling
#      every session to load ~/.claude/skills/<persona-name>/SKILL.md as its
#      operating identity.
#
# Both writes go through merge_config.py (the SAME non-clobbering merger the
# installer uses): back-up-before, verify-after, abort-rather-than-clobber, and
# replace-between-markers so the block updates in place instead of duplicating.
#
# PROPERTIES: fail-loud (clear message + exit code on every failure), idempotent
# (re-running with the same name updates in place; nothing duplicates), and
# bash-3.2 / BSD-safe (works on a stock macOS /bin/bash). It NEVER ships or names
# a specific persona — it wires whatever persona YOU authored.
#
# SEMANTICS: the auto-load block holds exactly ONE active persona. Re-running with
# a different <persona-name> replaces the prior auto-load (you operate as one
# persona at a time). To stop auto-loading entirely, delete the marker block from
# ~/.claude/CLAUDE.md (your own content outside the markers is untouched).
# ============================================================================
set -uo pipefail

# ----------------------------------------------------------------------------
# Resolve paths (mirror install.sh: honor WIZARD_CLAUDE_HOME; self-locate package)
# ----------------------------------------------------------------------------
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${WIZARD_CLAUDE_HOME:-${HOME}/.claude}"
BRAIN_DIR="${CLAUDE_HOME}/memory"
SKILLS_DIR="${VOIDLOOM_SKILLS_DIR:-${CLAUDE_HOME}/skills}"   # the same skills dir `voidloom persona status` reports
HOOKS_DIR="${CLAUDE_HOME}/hooks"
SETTINGS_JSON="${CLAUDE_HOME}/settings.json"
CLAUDE_MD="${CLAUDE_HOME}/CLAUDE.md"
# C3 -- ONE INSTALLER. This used to hand-write `node .../session-start.mjs`,
# a second hook the product could not see: HOOK_MARKER_RE did not match it, so
# `voidloom hooks status` reported nothing installed and `hooks uninstall` could
# not remove what this very script had wired. It also resolved the JUNE FLAT
# LAYOUT, so on a correctly-installed brain it printed "brain not reachable yet"
# and never emitted the arrival line the AGENTS.md notice looks for -- a false
# "the hook is not active" alarm in EVERY session.
#
# The hook is now installed by the one thing that owns hooks, so there is no
# second spelling to drift. The command below is only for the verify-after
# read-back; the organ decides what is actually written.
HOOK_INSTALLER="${BRAIN_DIR}/organs/voidloom_hooks_status.py"
HOOK_CMD="python3 ${BRAIN_DIR}/hooks/session_start_inject.py"

# Markers for the PERSONA AUTO-LOAD block — DISTINCT from the memory block's
# markers (install.sh uses "WIZARD-LIBRARY MEMORY"), so the two managed blocks
# never collide and each updates independently.
PA_BEGIN="<!-- BEGIN WIZARD-LIBRARY PERSONA AUTO-LOAD (managed by wire_persona) -->"
PA_END="<!-- END WIZARD-LIBRARY PERSONA AUTO-LOAD -->"

say()  { echo "[wire-persona] $*"; }
warn() { echo "[wire-persona] WARNING: $*" >&2; }
die()  { echo "[wire-persona] FATAL: $*" >&2; exit 1; }

# ----------------------------------------------------------------------------
# Args
# ----------------------------------------------------------------------------
if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
    cat >&2 <<USAGE
Usage: bash wire_persona.sh <persona-name>

  <persona-name>   the folder name under ~/.claude/skills/ that contains your
                   persona's SKILL.md (e.g. you created ~/.claude/skills/my-persona/SKILL.md
                   -> run:  bash wire_persona.sh my-persona )

First craft your persona with /persona-forge in Claude Code (or by hand: ONBOARDING_GUIDE.md, Step 3), THEN run this.
USAGE
    exit 2
fi
case "$1" in
    --help|-h)
        sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0 ;;
esac
PERSONA="$1"

# Reject path-y / unsafe names (we only ever take a single skill folder name).
case "$PERSONA" in
    */*|*..*|"") die "persona-name must be a plain skill folder name (no slashes, no '..'): '$PERSONA'" ;;
esac

PERSONA_DIR="${SKILLS_DIR}/${PERSONA}"
PERSONA_SKILL="${PERSONA_DIR}/SKILL.md"
# The path written into CLAUDE.md: ~-relative when it is under $HOME (portable across a renamed home), else absolute.
case "$PERSONA_SKILL" in "${HOME}"/*) PERSONA_SKILL_SHOWN="~/${PERSONA_SKILL#"${HOME}"/}" ;; *) PERSONA_SKILL_SHOWN="$PERSONA_SKILL" ;; esac

# ----------------------------------------------------------------------------
# Pre-flight: the persona-skill must actually exist (fail-loud, do not guess)
# ----------------------------------------------------------------------------
if [ ! -d "$PERSONA_DIR" ]; then
    die "no persona folder at ${PERSONA_DIR}. Create it first (ONBOARDING_GUIDE.md Step 3): mkdir -p '${PERSONA_DIR}' and write SKILL.md."
fi
if [ ! -f "$PERSONA_SKILL" ]; then
    die "found ${PERSONA_DIR} but no SKILL.md inside it. Write ${PERSONA_SKILL} (the persona entry point) first."
fi

# Pick a python for merge_config (system python is fine — merge_config is stdlib).
PY="${WIZARD_PYTHON_BIN:-}"
if [ -z "$PY" ]; then
    for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 /usr/bin/python3; do
        if command -v "$p" >/dev/null 2>&1; then PY="$(command -v "$p")"; break; fi
    done
fi
[ -n "$PY" ] || die "no python3 found on PATH (needed to run merge_config.py)."

MERGE="${BRAIN_DIR}/install/merge_config.py"
[ -f "$MERGE" ] || MERGE="${PKG_ROOT}/merge_config.py"   # fall back to the package copy
[ -f "$MERGE" ] || die "merge_config.py not found (looked in ${BRAIN_DIR} and ${PKG_ROOT}). Run install.sh first."

say "wiring persona '${PERSONA}' (SKILL.md: ${PERSONA_SKILL})"

# ----------------------------------------------------------------------------
# 1. Ensure the SessionStart memory hook is present (idempotent; never clobber).
# ----------------------------------------------------------------------------
say "ensuring SessionStart hook present in ${SETTINGS_JSON} (via the hooks organ)"
if [ -f "$HOOK_INSTALLER" ]; then
    # The organ is idempotent, refuses rather than clobbers, writes a receipt,
    # and SUPERSEDES a legacy element in the same write -- so a machine wired to
    # the retired Node hook ends this call with exactly one SessionStart hook.
    "$PY" "$HOOK_INSTALLER" --install --platform claude --yes \
        || warn "hooks install reported a problem (your settings.json was NOT clobbered — see above)"
else
    warn "organs/voidloom_hooks_status.py not found; falling back to the defensive merge"
    "$PY" "$MERGE" hook --file "$SETTINGS_JSON" --command "$HOOK_CMD" \
        || warn "hook merge reported a problem (your settings.json was NOT clobbered — see above)"
fi

# ----------------------------------------------------------------------------
# 2. Add/replace the PERSONA AUTO-LOAD block in ~/.claude/CLAUDE.md.
#    We build the block body in a temp file, then hand it to merge_config's
#    claude-md op (replace-between-markers if present, append if not, create if
#    no file — and verify-after, restoring from .bak on any failure).
# ----------------------------------------------------------------------------
BLOCK_TMP="$(mktemp "${TMPDIR:-/tmp}/wizard_persona_block.XXXXXX")" || die "could not create a temp file."
# shellcheck disable=SC2064
trap "rm -f '$BLOCK_TMP'" EXIT

cat > "$BLOCK_TMP" <<BLOCK
${PA_BEGIN}
## Operating Persona — AUTO-LOAD (managed by wire_persona)

At the start of EVERY session, load your operating persona:

- **Load \`${PERSONA_SKILL_SHOWN}\`** and operate as that persona.
- It references its own layer files (foundation / voice / operations / memory).
- Recall your canonical identity to wake up in-character:
  \`python3 ~/.claude/memory/organs/claude_memory_lite.py recall --query "operating identity ${PERSONA}" --top_k 5\`
  (use \`claude_memory.py\` for semantic recall if Tier 1 is installed).

> This block is managed by \`wire_persona.sh\` and delimited by markers, so it
> updates in place and never disturbs the rest of your CLAUDE.md. To stop
> auto-loading a persona, delete this marker block. The memory-system block
> (BEGIN/END WIZARD-LIBRARY MEMORY) is separate and unaffected.
${PA_END}
BLOCK

say "wiring PERSONA AUTO-LOAD block into ${CLAUDE_MD} (marker-delimited, defensive)"
if "$PY" "$MERGE" claude-md \
        --file "$CLAUDE_MD" \
        --block-file "$BLOCK_TMP" \
        --begin "$PA_BEGIN" --end "$PA_END"; then
    :
else
    die "CLAUDE.md persona-block merge failed (your CLAUDE.md was NOT clobbered — see above)."
fi

# ----------------------------------------------------------------------------
# Verify-after: confirm both wirings actually landed.
# ----------------------------------------------------------------------------
OK=1
if ! "$PY" - "$SETTINGS_JSON" "$HOOK_CMD" <<'PYEOF'
import json, sys
f, cmd = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(f, encoding="utf-8"))
except Exception as e:
    sys.stderr.write(f"verify: cannot read settings.json: {e}\n"); sys.exit(1)
ss = d.get("hooks", {}).get("SessionStart", [])
present = any(h.get("command") == cmd for g in ss if isinstance(g, dict) for h in g.get("hooks", []))
sys.exit(0 if present else 1)
PYEOF
then
    warn "verify: SessionStart memory hook not detected in settings.json."
    OK=0
fi
if ! grep -qF "${PERSONA_SKILL_SHOWN}" "$CLAUDE_MD" 2>/dev/null; then
    warn "verify: persona auto-load line not detected in ${CLAUDE_MD}."
    OK=0
fi

if [ "$OK" -eq 1 ]; then
    say "DONE — persona '${PERSONA}' will auto-load every session."
    say "  - SessionStart hook: present (memory brain primes each session)"
    say "  - CLAUDE.md: PERSONA AUTO-LOAD block points at ${PERSONA_SKILL_SHOWN}"
    say "Open a NEW Claude Code session to confirm it wakes up as '${PERSONA}', brain-aware."
    exit 0
else
    die "one or more wirings did not verify (see warnings above). Nothing was clobbered; fix and re-run."
fi

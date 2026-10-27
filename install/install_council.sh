#!/usr/bin/env bash
# ============================================================================
# Voidloom — Inner Council installer (additive, reversible, never-clobber)
# ============================================================================
# Installs the interactive, FREE Inner Council:
#   * 5 cognitive-lens skills -> ~/.claude/skills/council-<lens>/
#   * the /council slash command -> ~/.claude/commands/council.md
#
# The council is INTERACTIVE-ONLY. The running assistant reasons as each lens
# in-session — there is NO autonomous `claude -p`, no subprocess, no background
# process, and no extra credit cost. This installer ships only static skill +
# command files; it runs nothing autonomous.
#
# SAFETY CONTRACT (the whole point):
#   * ADDITIVE   — only adds the council skills + the /council command.
#   * REVERSIBLE — if a same-named skill folder or command already exists in the
#                  user's globals, it is BACKED UP (renamed to *.voidloom-bak.<ts>)
#                  BEFORE anything is written. Nothing the user already has is
#                  destroyed.
#   * NEVER CLOBBER — we never blind-overwrite. Every destination that already
#                  exists is moved aside first, and we print exactly what moved.
#
# USAGE — two ways to invoke:
#   A. Sourced by the main installer (recommended). The main install.sh defines
#      DRY_RUN / CLAUDE_HOME / PKG_ROOT / the say()/run() helpers, sets
#      WITH_COUNCIL from its own --with-council/--no-council flag parsing, then:
#          source "${PKG_ROOT}/install_council.sh"
#          install_council            # runs the install using the shared helpers
#
#   B. Standalone:
#          bash install_council.sh                 # install (default ON)
#          bash install_council.sh --dry-run       # print what would happen
#          bash install_council.sh --no-council    # do nothing (explicit opt-out)
#          bash install_council.sh --help
#      Standalone honors WIZARD_CLAUDE_HOME for the install root and locates the
#      council source files relative to this script.
# ============================================================================

# ---- Standalone bootstrap (only runs when executed directly, not when sourced)
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    set -uo pipefail
    _CO_DRY_RUN=0
    WITH_COUNCIL=1
    for arg in "$@"; do
        case "$arg" in
            --dry-run)     _CO_DRY_RUN=1 ;;
            --with-council) WITH_COUNCIL=1 ;;
            --no-council)  WITH_COUNCIL=0 ;;
            --help|-h)
                sed -n '2,46p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                exit 0 ;;
            *) echo "[council] Unknown arg: $arg (try --help)"; exit 2 ;;
        esac
    done
    # Minimal self-contained helpers + env when not sourced by the main installer.
    : "${CLAUDE_HOME:=${WIZARD_CLAUDE_HOME:-${HOME}/.claude}}"
    : "${PKG_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    DRY_RUN="${_CO_DRY_RUN}"
    say()  { echo "[council] $*"; }
    _CO_STANDALONE=1
else
    # Sourced: inherit say()/run()/DRY_RUN/CLAUDE_HOME/PKG_ROOT from the caller.
    # Provide gentle fallbacks so a partial caller still works.
    : "${CLAUDE_HOME:=${WIZARD_CLAUDE_HOME:-${HOME}/.claude}}"
    : "${PKG_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    : "${DRY_RUN:=0}"
    type say >/dev/null 2>&1 || say() { echo "[council] $*"; }
    : "${WITH_COUNCIL:=1}"
    _CO_STANDALONE=0
fi

# _co_run CMD ARG... -- a write, or its DRY line. WITHOUT eval (model-choice Round 1, Wave B -- R-7 / EW4-06): each
# argument is passed as it is, so a path holding a quote, a space or a '$' is one argument. The old `run "mv '${dst}'
# '${bak}'"` went through eval: under a CLAUDE_HOME holding a ' every mv/mkdir/cp failed, and the next line still said
# "installed (dir)" -- a false success. Its own name, so a caller's `run` (an eval runner) is never inherited.
_co_run() { if [ "${DRY_RUN}" -eq 1 ]; then echo "  DRY: $*"; else "$@"; fi; }
_CO_FAILED=0

# The five lens folder basenames + the command file we ship.
_CO_LENSES="council-seer council-skeptic council-architect council-dreamer council-synthesizer"
_CO_CMD="council.md"

# ----------------------------------------------------------------------------
# back_up_then_copy <src> <dst>
#   Reversible, never-clobber copy of a file OR a directory:
#     1. If <dst> already exists, MOVE it aside to <dst>.voidloom-bak.<timestamp>
#        (the user's existing item is preserved, never destroyed).
#     2. Copy <src> -> <dst>.
#   Honors DRY_RUN and prints every move/copy it performs.
# ----------------------------------------------------------------------------
back_up_then_copy() {
    local src="$1" dst="$2" ts bak
    if [ ! -e "$src" ]; then
        say "  SKIP (source missing): ${src}"
        return 0
    fi
    if [ -e "$dst" ] || [ -L "$dst" ]; then
        ts="$(date +%Y%m%d-%H%M%S)"
        bak="${dst}.voidloom-bak.${ts}"
        say "  EXISTS — backing up your current item before adding the council version:"
        say "      ${dst}  ->  ${bak}"
        _co_run mv "${dst}" "${bak}" || { say "  FAILED: could not move ${dst} aside (above); it was NOT replaced."; _CO_FAILED=1; return 1; }
    fi
    if [ -d "$src" ]; then
        if _co_run mkdir -p "${dst}" && _co_run cp -R "${src}/." "${dst}/"; then
            say "  installed (dir):  ${dst}"
        else
            say "  FAILED: ${dst} was not installed (the copy above failed)."; _CO_FAILED=1; return 1
        fi
    else
        if _co_run mkdir -p "$(dirname "${dst}")" && _co_run cp -f "${src}" "${dst}"; then
            say "  installed (file): ${dst}"
        else
            say "  FAILED: ${dst} was not installed (the copy above failed)."; _CO_FAILED=1; return 1
        fi
    fi
}

# ----------------------------------------------------------------------------
# install_council — the entry point (called when sourced; auto-called standalone)
# ----------------------------------------------------------------------------
install_council() {
    if [ "${WITH_COUNCIL:-1}" -ne 1 ]; then
        say "Council install skipped (--no-council)."
        return 0
    fi

    local skills_src="${PKG_ROOT}/persona/council_skills"   # NOT package skills/ — keeps the council OUT of the seed's ungated STEP A.3 blanket copy, so A.4 is the sole installer and --no-council is clean
    # the package ships the command at persona/commands/ (lay_manifest: component council, since 2a7502c moved it there);
    # a tree that still carries commands/ at its root is honoured (model-choice Round 1, Wave B: found while proving R-7 --
    # the lookup named only commands/, so every packaged run said "MISSING in package" and installed no council at all)
    local cmd_src="${PKG_ROOT}/persona/commands/${_CO_CMD}"
    [ -f "$cmd_src" ] || cmd_src="${PKG_ROOT}/commands/${_CO_CMD}"
    local skills_dst="${CLAUDE_HOME}/skills"
    local cmds_dst="${CLAUDE_HOME}/commands"

    say "=================================================================="
    say " Inner Council — interactive, FREE (no autonomous claude -p)"
    say "=================================================================="
    say "Skills  -> ${skills_dst}/council-<lens>/"
    say "Command -> ${cmds_dst}/${_CO_CMD}   (invoke as /council)"
    [ "${DRY_RUN}" -eq 1 ] && say "** DRY RUN — no changes will be made **"

    # Sanity: the council source files must be present in the package.
    local missing=0
    for lens in ${_CO_LENSES}; do
        [ -f "${skills_src}/${lens}/SKILL.md" ] || { say "  MISSING in package: persona/council_skills/${lens}/SKILL.md"; missing=1; }
    done
    [ -f "${cmd_src}" ] || { say "  MISSING in package: persona/commands/${_CO_CMD}"; missing=1; }
    if [ "${missing}" -ne 1 ]; then
        :
    else
        say "Council source incomplete in package — skipping council install (the rest of your brain is unaffected)."
        return 0
    fi

    _co_run mkdir -p "${skills_dst}" "${cmds_dst}" || { say "FAILED: could not create ${skills_dst} / ${cmds_dst} (above); nothing installed."; return 1; }

    # 1) the five lens skills (each its own folder; back up any same-named folder)
    for lens in ${_CO_LENSES}; do
        back_up_then_copy "${skills_src}/${lens}" "${skills_dst}/${lens}"
    done

    # 2) the /council command (back up any same-named command first)
    back_up_then_copy "${cmd_src}" "${cmds_dst}/${_CO_CMD}"

    if [ "${_CO_FAILED}" -ne 0 ]; then
        say "Inner Council NOT fully installed: the FAILED lines above name what was not copied. Nothing above them is a claim that it was."
        return 1
    fi
    say "Inner Council installed. Open a new session and run:  /council <your decision>"
    say "It is interactive + free — the assistant reasons as each lens in-session."
    say "To remove later: delete ${skills_dst}/council-* and ${cmds_dst}/${_CO_CMD}"
    say "(any backed-up *.voidloom-bak.* items are your originals — restore by renaming back)"
}

# Standalone: run it now (its exit status is the install's). Sourced: the caller calls install_council itself.
if [ "${_CO_STANDALONE}" -eq 1 ]; then
    install_council
    exit $?
fi
